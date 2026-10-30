"""C14: stop an actual in-flight Qwen turn, then accept a different change."""

from datetime import datetime, timezone
import json
import subprocess
import time

from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

from test.playwright.e2e import sql_chat_relation_fixture as catalog_fixture
from test.playwright.e2e import test_sql_chat_calendar_live as calendar

live = native.live
real_sql_catalog = catalog_fixture.real_sql_catalog

chat_probe = chat.chat_probe


def _audit(marker, since):
    # Same actual proxy audit source as native.Live.inference, but include the
    # active request so stop is exercised during genuine upstream work.
    script = """
import asyncio,json,sys
from datetime import datetime
from sqlalchemy import select
from app.database import get_engine
from app.models import HttpTransaction as T
async def main():
 engine=get_engine();output=[]
 async with engine.connect() as connection:
  result=await connection.execute(select(T.request_id,T.status,T.source_type,T.dedup_hit,T.upstream_id,T.model_name,T.upstream_started_at,T.completed_at,T.canceled_at,T.response_status_code,T.request_body_raw).where(T.received_at>=datetime.fromisoformat(sys.argv[2]),T.model_name.like('qwen3.8%')).order_by(T.received_at.desc()).limit(1000))
  for record in result:
   value=dict(record._mapping);body=value.pop('request_body_raw') or b''
   if isinstance(body,bytes):body=body.decode('utf-8')
   if sys.argv[1] in body:output.append(value)
 await engine.dispose();print(json.dumps(output,default=str))
asyncio.run(main())
"""
    response = subprocess.run(["docker", "exec", "-w", "/app", "ollama-proxy", "python", "-c", script, marker, since], capture_output=True, encoding="utf-8", timeout=60)
    assert response.returncode == 0, "Own real upstream audit unavailable"
    return json.loads(response.stdout)


def test_chat_stop_inflight_turn_then_accept_new_change_without_late_overwrite(page, live, chat_probe, real_sql_catalog):
    chat._start(page, live, chat_probe, calendar._question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    original = chat._rows(page, live, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
    since = datetime.now(timezone.utc).isoformat()
    chat._send(page, chat_probe, "Оставь только amount не меньше 50. Остальные условия сохрани.")
    observed = {}

    def actual_inflight(value):
        assert chat._identity(value) == chat._identity(original)
        job = value["session"]["project"].get("current_job")
        if not job or job["status"] != "RUNNING":
            return False
        for request in _audit(chat_probe["fqn"], since):
            if request["status"] not in {"done", "failed", "cancelled", "canceled", "rejected"} and request["upstream_started_at"] and not request["completed_at"] and not request["canceled_at"]:
                current = live.api("GET", chat.SESSIONS + "/" + chat_probe["id"])["project"].get("current_job")
                if not current or current["id"] != job["id"] or current["status"] != "RUNNING":
                    return False
                observed.update(job_id=job["id"], request_id=request["request_id"])
                return True
        return False

    chat._wait(page, live, chat_probe, actual_inflight)
    stop_started = datetime.now(timezone.utc)
    chat._root(page).get_by_role("button", name="Остановить запрос", exact=True).click()
    cancelled = chat._wait(page, live, chat_probe, lambda value: value["session"]["conversation"]["phase"] == "CANCELLED")
    assert chat._identity(cancelled) == chat._identity(original)
    assert cancelled["result"]["rows"] == original["result"]["rows"]
    # The cancelled >=50 request must not become the basis of this new turn.
    newer_since = datetime.now(timezone.utc).isoformat()
    chat._send(page, chat_probe, "Теперь оставь только amount меньше 50. Статус paid, исходный источник и сентябрь 2026 UTC сохрани.")
    newer = chat._rows(page, live, chat_probe, [[105, 40], [107, 0]], ["id", "amount"])
    assert chat._identity(newer) != chat._identity(original)
    deadline = time.monotonic() + 180
    old_request = None
    while time.monotonic() < deadline:
        old_request = next((item for item in _audit(chat_probe["fqn"], since) if item["request_id"] == observed["request_id"]), None)
        if old_request and (old_request["completed_at"] or old_request["canceled_at"]):
            break
        page.wait_for_timeout(500)
    assert old_request and (old_request["completed_at"] or old_request["canceled_at"]), "Old actual upstream work did not terminate"
    assert old_request["source_type"] == "upstream" and old_request["dedup_hit"] is False and old_request["upstream_id"]
    assert old_request["model_name"].startswith("qwen3.8")
    if old_request["completed_at"] and not old_request["canceled_at"]:
        assert old_request["response_status_code"] == 200
        assert datetime.fromisoformat(old_request["completed_at"]) > stop_started, "Stop must precede the old upstream completion"
    job_id = observed["job_id"]
    assert len(job_id) == 32 and all(character in "0123456789abcdef" for character in job_id)
    old_job = live.sql(
        f"SELECT json_build_object('status',status,'published_proposals',(SELECT count(*) FROM business_document_sql_agent_proposal WHERE job_id='{job_id}')) FROM business_document_job WHERE id='{job_id}';"
    )
    assert old_job == {"status": "CANCELLED", "published_proposals": 0}
    # A fresh browser load must still show the later accepted result after the
    # old real request is terminal; no fabricated completion/result is posted.
    page.reload(wait_until="domcontentloaded")
    restored = chat._rows(page, live, chat_probe, [[105, 40], [107, 0]], ["id", "amount"])
    assert chat._identity(restored) == chat._identity(newer)
    live.proof(
        chat_probe["proof_id"] + "-cancel-late",
        {
            "cancelled_job": observed["job_id"],
            "old_upstream": old_request,
            "late_upstream_after_stop": bool(old_request["completed_at"] and not old_request["canceled_at"]),
            "old_job": old_job,
            "previous_good": chat._identity(original),
            "newer_accepted": chat._identity(newer),
            "restored_unchanged": True,
        },
    )
    # Native all-success auditing excludes genuinely cancelled transports only
    # in this cancellation scenario; the two completed results retain actual
    # rows, worker claims and a source-matched screenshot as separate evidence.
    live.worker_claims(sorted(chat_probe["jobs"]))
    upstream = _audit(chat_probe["fqn"], live.started_at)
    assert all(
        row["status"] == "done" and row["source_type"] == "upstream" and row["dedup_hit"] is False and row["response_status_code"] == 200
        for row in upstream
        if row["request_id"] != observed["request_id"]
    )
    newer_inference = _audit(chat_probe["fqn"], newer_since)
    assert newer_inference, "The newer accepted turn must have its own actual model request"
    live.proof(
        chat_probe["proof_id"],
        {
            "scope": "Actualbrowser/nativecancel/reload with realupstream audit",
            "source_fqn": chat_probe["fqn"],
            "identity": chat._identity(restored),
            "rows": restored["result"]["rows"],
            "upstream": upstream,
            "newer_inference": newer_inference,
        },
    )
    page.screenshot(path=str(live.out / (chat_probe["proof_id"] + ".png")))
