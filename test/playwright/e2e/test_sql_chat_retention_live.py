"""C20: the real retention guard removes access to rows and derived answers.

The fixture ages only its own actual Run by its observed retention interval.
It never manufactures a terminal status, result, model response or API response.
"""

import re
from decimal import Decimal
from uuid import uuid4

from playwright.sync_api import expect

from test.playwright.e2e import test_sql_chat_calendar_live as calendar
from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
real_sql_catalog = calendar.real_sql_catalog
chat_probe = chat.chat_probe


def _job_count(live, session_id):
    assert re.fullmatch(r"[0-9a-f]{32}", session_id)
    return live.sql(f"SELECT json_build_object('count',count(*)) FROM business_document_job WHERE document_id='{session_id}';")["count"]


def test_chat_retention_blocks_rows_answers_and_export_without_new_sql(page, live, chat_probe, real_sql_catalog):
    chat._start(page, live, chat_probe, calendar._question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    before = chat._rows(page, live, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
    known = {message["id"] for message in before["session"]["messages"]}
    chat._send(page, chat_probe, "Какова сумма amount только уже показанных строк? Новый SQL не запускай.")
    answered = chat._wait(page, live, chat_probe, lambda value: any(message["id"] not in known and message.get("answer", {}).get("status") == "READY" for message in value["session"]["messages"]))
    answer = next(message for message in answered["session"]["messages"] if message["id"] not in known and message.get("answer", {}).get("status") == "READY")
    total = 490 if real_sql_catalog["calendar_discriminator"] else 240
    numbers = re.findall(r"[+-]?\d+(?:[,.]\d+)?", answer["answer"]["text"])
    assert numbers and Decimal(numbers[-1].replace(",", ".")) == Decimal(total)
    assert answer["answer"]["calculation"]["operation"] == "sum"
    run = answered["result"]
    assert chat._identity(answered) == chat._identity(before)
    assert re.fullmatch(r"[0-9a-f]{32}", run["run_id"])
    interval = run["expires_at"] - run["created_at"]
    assert type(interval) is int and interval > 0
    count_before = _job_count(live, chat_probe["id"])
    # Move this fixture's birth timestamp beyond its real server policy. The
    # application performs its ordinary expiry checks/purge on subsequent reads.
    age = live.sql(
        f"UPDATE business_document_sql_query_run SET create_time=(extract(epoch FROM clock_timestamp())*1000)::bigint-{interval}-2000 "
        f"WHERE id='{run['run_id']}' AND project_id='{chat_probe['id']}'; "
        f"SELECT json_build_object('aged_rows',count(*)) FROM business_document_sql_query_run WHERE id='{run['run_id']}';"
    )
    assert age["aged_rows"] == 1
    result_path = chat.SESSIONS + "/" + chat_probe["id"] + "/results/" + run["run_id"]
    denied_rows = live.response("GET", result_path)
    assert denied_rows.status_code == 410 and "SQL_RESULT_EXPIRED" in str(denied_rows.json())
    expired = live.api("GET", chat.SESSIONS + "/" + chat_probe["id"])
    assert expired["project"]["artifact_ids"]["query"] == before["session"]["project"]["artifact_ids"]["query"]
    scrubbed = next(message for message in expired["messages"] if message["id"] == answer["id"])
    assert scrubbed["answer"]["status"] == "UNAVAILABLE"
    assert not re.search(r"(?<!\d)" + str(total) + r"(?!\d)", scrubbed["answer"]["text"])
    denied_export = live.response("POST", native.PREFIX + "/projects/" + chat_probe["id"] + "/export", {"schema_version": "1", "kind": "RESULT", "format": "json", "run_id": run["run_id"]})
    assert denied_export.status_code == 410 and "SQL_RESULT_EXPIRED" in str(denied_export.json())
    rejected_answer = live.response(
        "POST",
        chat.SESSIONS + "/" + chat_probe["id"] + "/commands",
        {
            "client_request_id": "expiry-probe-" + uuid4().hex,
            "expected_revision": expired["revision"],
            "expected_project_version": expired["project"]["state_version"],
            "type": "message",
            "payload": {"text": "Повтори сумму истёкших строк", "run_id": run["run_id"], "context": run["dialogue_context"]},
        },
    )
    assert rejected_answer.status_code in (409, 410), "Server must reject expired context before starting another model/SQL job"
    assert _job_count(live, chat_probe["id"]) == count_before
    page.reload(wait_until="domcontentloaded")
    root = chat._root(page)
    expect(root.get_by_role("alert").filter(has_text="Результат недоступен")).to_be_visible(timeout=30000)
    expect(root.get_by_role("table", name="Данные выбранного результата", exact=True)).to_have_count(0)
    expect(root.get_by_role("button", name="Скачать результат", exact=True)).to_have_count(0)
    assert _job_count(live, chat_probe["id"]) == count_before
    page.screenshot(path=str(live.out / (chat_probe["proof_id"] + "-expired.png")), full_page=True)
    live.proof(
        chat_probe["proof_id"] + "-retention",
        {
            "scope": "real expiry guard on an owned actual Run; no manufactured result/status",
            "session_id": chat_probe["id"],
            "run_id": run["run_id"],
            "retention_ms": interval,
            "rows_http": denied_rows.status_code,
            "export_http": denied_export.status_code,
            "message_http": rejected_answer.status_code,
            "answer_status": scrubbed["answer"]["status"],
            "job_count_unchanged": count_before,
            "query_artifact_id_preserved": expired["project"]["artifact_ids"]["query"],
            "upstream_transactions": live.inference([chat_probe["fqn"]], 1),
            "worker_claims": live.worker_claims(sorted(chat_probe["jobs"])),
        },
    )
