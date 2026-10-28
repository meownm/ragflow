"""Real four-section journeys; only the disposable live runner may execute them.

No HTTP interception, model substitutes, patched services or fabricated catalog
responses. All persisted records belong to this runner's private database.
"""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import ip_address, ip_interface
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from uuid import uuid4

import pytest
import requests
import mistune
from playwright.sync_api import expect

from api.utils.crypt import crypt

ROOT = Path(__file__).resolve().parents[3]
PREFIX = "/api/v1/business-documents/sql-query"


class Live:
    def __init__(self, base):
        port = os.environ.get("QA_LIVE_PORT", "19382")
        assert port in {"19382", "19482"} and base == "http://127.0.0.1:" + port
        assert os.environ.get("QA_DISPOSABLE_PROJECT", "").startswith("ragflow-t1-live-")
        self.base = base
        self.started_at = datetime.now(UTC).isoformat()
        self.http = requests.Session()
        self.http.headers["Authorization"] = os.environ["QA_LIVE_TOKEN"]
        self.user = self.api("GET", "/api/v1/users/me")
        assert self.user["is_superuser"]
        assert self.user["email"].startswith("regression-probe-")
        self.out = Path(os.environ["QA_LIVE_EVIDENCE"])
        self.claim_table = "qa_claims_" + uuid4().hex[:16]
        self.sql(
            f"CREATE TABLE {self.claim_table} (job_id text, worker_id text, client_ip text);\n"
            f"CREATE FUNCTION {self.claim_table}_observe() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO {self.claim_table} VALUES (NEW.id,NEW.lease_owner,host(inet_client_addr())); RETURN NEW; END $$;\n"
            f"CREATE TRIGGER {self.claim_table}_trigger AFTER UPDATE ON business_document_job FOR EACH ROW WHEN (NEW.lease_owner IS NOT NULL AND NEW.lease_token IS DISTINCT FROM OLD.lease_token) EXECUTE FUNCTION {self.claim_table}_observe();\n"
            "SELECT json_build_object('observer_installed',true);"
        )

    def response(self, method, path, data=None, *, token=None):
        headers = {"Authorization": token} if token is not None else {}
        response = self.http.request(method, self.base + path, json=data, headers=headers, timeout=(10, 360))
        # Each API persona is its explicit bearer token, without a previous
        # admin login cookie accidentally authenticating a negative request.
        self.http.cookies.clear()
        return response

    def api(self, method, path, data=None, *, token=None):
        r = self.response(method, path, data, token=token)
        assert r.status_code in (200, 201, 202), f"{method} {urlsplit(path).path}: HTTP {r.status_code}"
        body = r.json()
        assert body.get("code") == 0, f"{method} {urlsplit(path).path}: API code {body.get('code')}"
        return body["data"]

    def browser(self, page, path, *, user=None, token=None):
        state = {"Authorization": token or self.http.headers["Authorization"], "userInfo": json.dumps(user or self.user), "lng": "ru", "ragflow-ui-theme": "light"}
        page.add_init_script("const qaSession=" + json.dumps(state) + ";for(const [k,v] of Object.entries(qaSession))localStorage.setItem(k,v)")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(self.base + path, wait_until="domcontentloaded")

    def proof(self, name, data):
        (self.out / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def sql(self, statement):
        project = os.environ["QA_DISPOSABLE_PROJECT"]
        assert project.startswith("ragflow-t1-live-")
        container = subprocess.check_output(
            ["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=" + project, "--filter", "label=com.docker.compose.service=postgres"], text=True
        ).strip()
        assert container and "\n" not in container
        result = subprocess.run(
            ["docker", "exec", "-i", container, "psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-U", "rag_flow", "-d", "rag_flow"],
            input=statement,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, "Owned QA PostgreSQL probe failed"
        return json.loads(result.stdout.strip())

    def application(self, code):
        container = os.environ["QA_LIVE_APP_CONTAINER"]
        script = "import os,json;assert os.environ['POSTGRES_HOST']=='postgres'\n" + code
        r = subprocess.run(["docker", "exec", "-i", container, "python3", "-B", "-"], input=script, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        if r.returncode:
            raise AssertionError(f"Owned QA database probe failed: exit {r.returncode}")
        lines = [line[len("QA_DB_RESULT=") :] for line in r.stdout.splitlines() if line.startswith("QA_DB_RESULT=")]
        assert len(lines) == 1
        return json.loads(lines[0])

    def create_user(self, role="user"):
        email = "regression-probe-" + uuid4().hex + "@example.org"
        password = secrets.token_urlsafe(18)
        user = self.api("POST", "/api/v1/admin/users", {"username": email, "password": crypt(password), "role": role})
        assert "password" not in user
        return user, password

    def reader(self):
        if not hasattr(self, "reader_token"):
            user, password = self.create_user()
            self.api("PATCH", "/api/v1/business-documents/access/users/" + user["id"], {"role": "READER"})
            login = self.response("POST", "/api/v1/auth/login", {"email": user["email"], "password": crypt(password)})
            assert login.status_code == 200 and login.json()["code"] == 0
            self.reader_token = login.headers["Authorization"]
        return self.reader_token

    def worker_claims(self, job_ids):
        assert all(len(value) == 32 and all(c in "0123456789abcdef" for c in value) for value in job_ids)
        values = ",".join("'" + value + "'" for value in job_ids)
        claims = self.sql(f"SELECT coalesce(json_agg(row_to_json(r)), '[]'::json) FROM (SELECT job_id,worker_id,client_ip FROM {self.claim_table} WHERE job_id IN ({values})) r;")
        inspected = json.loads(subprocess.check_output(["docker", "inspect", os.environ["QA_LIVE_WORKER_CONTAINER"]]))[0]
        addresses = {ip_address(value[key]) for value in inspected["NetworkSettings"]["Networks"].values() for key in ("IPAddress", "GlobalIPv6Address") if value.get(key)}
        assert set(job_ids) <= {row["job_id"] for row in claims}
        assert all(ip_interface(row["client_ip"]).ip in addresses for row in claims), "Job was not claimed by the distinct worker container"
        return claims

    def inference(self, markers, minimum):
        # Use the existing real proxy audit DB, restricted to this run's unique
        # inputs. Request bodies remain inside the proxy and are not exported.
        script = """
import asyncio,json,sys
from datetime import datetime
from sqlalchemy import select
from app.database import get_engine
from app.models import HttpTransaction as T
async def main():
 engine=get_engine();markers=json.loads(sys.argv[1])
 fields=[T.request_id,T.status,T.source_type,T.dedup_hit,T.upstream_id,T.model_name,T.response_status_code,T.request_body_raw]
 async with engine.connect() as connection:
  result=await connection.execute(select(*fields).where(T.received_at >= datetime.fromisoformat(sys.argv[2]),T.model_name.like('qwen3.8%')).order_by(T.received_at.desc()).limit(1000))
  rows=[]
  for source in result:
   row=dict(source._mapping);body=row.pop('request_body_raw') or b''
   if isinstance(body,bytes):body=body.decode('utf-8')
   if any(m in body for m in markers):rows.append(row)
 await engine.dispose();print(json.dumps(rows))
asyncio.run(main())
"""
        r = subprocess.run(["docker", "exec", "-w", "/app", "ollama-proxy", "python", "-c", script, json.dumps(markers), self.started_at], capture_output=True, encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError("Real proxy inference audit unavailable")
        rows = json.loads(r.stdout)
        assert len(rows) >= minimum, "Actual upstream call inventory is incomplete"
        assert all(
            row["status"] == "done"
            and row["source_type"] == "upstream"
            and row["dedup_hit"] is False
            and row["upstream_id"]
            and row["response_status_code"] == 200
            and row["model_name"].startswith("qwen3.8")
            for row in rows
        ), "Fresh successful qwen3.8 inference is not proven"
        return rows


@pytest.fixture(scope="module")
def live(base_url):
    value = Live(base_url.rstrip("/"))
    yield value
    value.http.close()


def test_admin_real_users_auth_validation_and_cleanup(page, live):
    browser_user, browser_password = live.create_user("admin")
    reader, reader_password = live.create_user()
    target, old_password = live.create_user()
    login = live.response("POST", "/api/v1/admin/login", {"email": browser_user["email"], "password": crypt(browser_password)})
    assert login.status_code == 200 and login.json()["code"] == 0
    browser_token = login.headers["Authorization"]
    reader_login = live.response("POST", "/api/v1/auth/login", {"email": reader["email"], "password": crypt(reader_password)})
    assert reader_login.status_code == 200 and reader_login.json()["code"] == 0
    reader_token = reader_login.headers["Authorization"]
    assert live.response("GET", "/api/v1/admin/users", token="invalid-synthetic-token").status_code == 401
    assert live.response("GET", "/api/v1/admin/users", token=reader_token).status_code == 403
    live.api("PATCH", "/api/v1/business-documents/access/users/" + reader["id"], {"role": "READER"})
    live.reader_token = reader_token
    anonymous = requests.get(live.base + "/api/v1/admin/users", timeout=15)
    assert anonymous.status_code == 401
    for payload in ({}, [], {"username": 1, "password": "bad"}):
        assert live.response("POST", "/api/v1/admin/users", payload).status_code == 400
    target_path = "/api/v1/admin/users/" + quote(target["email"], safe="")
    assert live.response("PUT", target_path + "/activate", {"activate_status": True}).status_code == 400
    active_delete = live.response("DELETE", target_path)
    assert active_delete.status_code == 400 and active_delete.json()["code"] != 0
    new_password = secrets.token_urlsafe(18)
    old_login = live.response("POST", "/api/v1/auth/login", {"email": target["email"], "password": crypt(old_password)})
    assert old_login.json()["code"] == 0
    old_token = old_login.headers["Authorization"]
    assert live.response("GET", "/api/v1/users/me", token=old_token).status_code == 200
    live.api("PUT", target_path + "/password", {"new_password": crypt(new_password)})
    assert live.response("GET", "/api/v1/users/me", token=old_token).status_code == 401
    rejected = live.response("POST", "/api/v1/auth/login", {"email": target["email"], "password": crypt(old_password)})
    assert rejected.json()["code"] != 0
    accepted = live.response("POST", "/api/v1/auth/login", {"email": target["email"], "password": crypt(new_password)})
    assert accepted.json()["code"] == 0
    target_token = accepted.headers["Authorization"]
    assert live.response("GET", "/api/v1/admin/users", token=target_token).status_code == 403
    live.api("PUT", target_path + "/admin")
    assert live.response("GET", "/api/v1/admin/users", token=target_token).status_code == 200
    live.api("DELETE", target_path + "/admin")
    assert live.response("GET", "/api/v1/admin/users", token=target_token).status_code == 403
    writes = []
    page.on("request", lambda r: writes.append((r.method, urlsplit(r.url).path)) if r.method not in {"GET", "OPTIONS"} else None)
    live.browser(page, "/admin/users", user=browser_user, token=browser_token)
    expect(page.get_by_text(target["email"], exact=True)).to_be_visible(timeout=30000)
    page.get_by_role("button", name="Новый пользователь", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Электронная почта", exact=True).fill("not-an-email")
    dialog.get_by_label("Пароль", exact=True).fill("valid-example123")
    dialog.get_by_label("Подтвердите пароль", exact=True).fill("different123")
    dialog.get_by_role("button", name="Подтвердить", exact=True).click()
    expect(dialog.get_by_text("Пожалуйста, введите действительный адрес электронной почты!", exact=True)).to_be_visible()
    assert not any(method == "POST" and path.endswith("/admin/users") for method, path in writes)
    ui_email = "regression-probe-ui-" + uuid4().hex + "@example.org"
    dialog.get_by_label("Электронная почта", exact=True).fill(ui_email)
    dialog.get_by_label("Подтвердите пароль", exact=True).fill("valid-example123")
    with page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path.endswith("/admin/users")) as created:
        dialog.get_by_role("button", name="Подтвердить", exact=True).click()
    assert created.value.status == 200 and created.value.json()["code"] == 0
    ui_user = created.value.json()["data"]
    expect(page.get_by_text(ui_email, exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_text(ui_email, exact=True)).to_be_visible()
    ui_path = "/api/v1/admin/users/" + quote(ui_email, safe="")
    live.api("PUT", ui_path + "/activate", {"activate_status": "off"})
    live.api("DELETE", ui_path)
    trigger = page.get_by_role("button", name=f"Удалить пользователя {target['email']}", exact=True)
    trigger.click()
    page.get_by_role("dialog").get_by_role("button", name="Отмена", exact=True).click()
    assert not any(method == "DELETE" for method, _ in writes)
    live.api("PUT", target_path + "/activate", {"activate_status": "off"})
    page.reload()
    trigger.click()
    with page.expect_response(lambda r: r.request.method == "DELETE" and unquote(urlsplit(r.url).path) == unquote(target_path), timeout=60000) as deleted:
        page.get_by_role("dialog").get_by_role("button", name="Удалить", exact=True).click()
    assert deleted.value.status == 200
    expect(page.get_by_text(target["email"], exact=True)).to_have_count(0)
    page.reload()
    expect(page.get_by_text(browser_user["email"], exact=True)).to_be_visible()
    expect(page.get_by_text(target["email"], exact=True)).to_have_count(0)
    page.screenshot(path=str(live.out / "admin-real.png"))
    counts = live.sql(
        f"SELECT json_build_object('user',(SELECT count(*) FROM \"user\" WHERE id='{target['id']}'),'tenant',(SELECT count(*) FROM tenant WHERE id='{target['id']}'),'membership',(SELECT count(*) FROM user_tenant WHERE user_id='{target['id']}'),'files',(SELECT count(*) FROM file WHERE tenant_id='{target['id']}'));"
    )
    live.proof("admin-real-proof", {"real_http": True, "browser": True, "auth_negative": True, "invalid_inputs": True, "password_rotation": True, "cleanup_counts": counts})
    assert all(value == 0 for value in counts.values()), "Deleting a new user left owned database records"
    ui_counts = live.sql(f"SELECT json_build_object('user',(SELECT count(*) FROM \"user\" WHERE id='{ui_user['id']}'),'files',(SELECT count(*) FROM file WHERE tenant_id='{ui_user['id']}'));")
    assert all(value == 0 for value in ui_counts.values())


def test_bt_real_model_separate_worker_ui_and_idempotency(page, live):
    assert os.environ.get("QA_LIVE_WORKER_CONTAINER") and os.environ["QA_LIVE_WORKER_CONTAINER"] != os.environ["QA_LIVE_APP_CONTAINER"]
    root = "/api/v1/business-documents"
    marker = "LIVE_BT_" + uuid4().hex
    document = live.api(
        "POST",
        root,
        {
            "schema_version": "1",
            "document_type": "business_requirements",
            "title": "regression-probe-bt-" + marker,
            "idea": "Нужна система бронирования синтетических телескопов " + marker + ". Пользователи, ограничения и критерии успеха пока не определены.",
        },
    )
    path = root + "/" + document["document_id"]
    try:
        assert live.response("GET", path, token="invalid-synthetic-token").status_code == 401
        assert live.response("POST", path + "/commands", {}).status_code == 422
        key = uuid4().hex
        payload = {"schema_version": "1", "command_id": key, "idempotency_key": key, "expected_state_version": document["state_version"], "type": "REQUEST_INTAKE_ASSESSMENT", "payload": {}}
        assert live.response("POST", path + "/commands", payload, token=live.reader()).status_code == 403
        command = live.api("POST", path + "/commands", payload)
        owners = set()
        deadline = time.monotonic() + 420
        while True:
            fresh = live.api("GET", path)
            job = fresh.get("latest_job") or {}
            row = live.sql(f"SELECT json_build_object('status',status,'attempt',attempt,'owner',lease_owner) FROM business_document_job WHERE id='{command['job_id']}';")
            if row["owner"]:
                owners.add(row["owner"])
            if job.get("status") == "COMPLETED":
                break
            assert job.get("status") not in {"FAILED", "DEAD", "CANCELLED"}, f"BT job failed: {job.get('status')}"
            assert time.monotonic() < deadline, "BT worker timeout"
            time.sleep(2)
        assert job["job_id"] == command["job_id"] and row["attempt"] >= 1
        questions = fresh["protocol"]["questions"]
        assert questions and all(2 <= len(q["options"]) <= 4 for q in questions)
        assert live.api("POST", path + "/commands", payload)["job_id"] == command["job_id"]
        stale_key = uuid4().hex
        stale_payload = {**payload, "command_id": stale_key, "idempotency_key": stale_key, "expected_state_version": fresh["state_version"] - 1}
        assert live.response("POST", path + "/commands", stale_payload).status_code == 409
        audits = live.sql(
            f"SELECT coalesce(json_agg(json_build_object('actor',actor_id,'ai',payload::jsonb->'execution'->'ai')), '[]'::json) FROM business_document_event WHERE document_id='{document['document_id']}' AND payload::jsonb->'execution'->'ai' IS NOT NULL;"
        )
        assert audits and all(a["ai"]["model"].split("@")[0].startswith("qwen3.8") for a in audits)
        assert all(a["ai"]["token_usage"]["total_tokens"] > 0 for a in audits)
        claims = live.worker_claims([command["job_id"]])
        upstream = live.inference([marker], 1)
        live.browser(page, "/business-documents/" + document["document_id"])
        expect(page.get_by_test_id("business-document-workbench")).to_be_visible(timeout=30000)
        expect(page.get_by_text(questions[0]["text"], exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_text(questions[0]["text"], exact=True)).to_be_visible()
        page.screenshot(path=str(live.out / "bt-real.png"))
        live.proof(
            "bt-real-proof",
            {
                "job_id": command["job_id"],
                "status": "COMPLETED",
                "attempt": row["attempt"],
                "observed_worker_owners": sorted(owners),
                "worker_container": os.environ["QA_LIVE_WORKER_CONTAINER"],
                "question_count": len(questions),
                "audits": audits,
                "worker_claims": claims,
                "upstream_transactions": upstream,
                "browser_reload": True,
            },
        )
    finally:
        live.api("DELETE", path)
        assert live.response("GET", path).status_code == 404


def test_tz_real_chat_verification_apply_and_conflicts(page, live):
    corpus = json.loads((ROOT / "test/evals/technical_specifications/golden.v2.json").read_bytes())
    case = next(c for c in corpus["cases"] if c["id"] == "C02")
    original = deepcopy(corpus["documents"][case["document"]])
    marker = "LIVE_TZ_" + uuid4().hex
    marker_line = "\nВнутренний идентификатор спецификации: " + marker + "."
    original["5"] += marker_line
    expected = {**original, **case["expected_changes"]}
    expected["5"] += marker_line
    workspace = live.api("POST", "/api/v1/source-workspaces", {"title": "regression-probe-tz-" + uuid4().hex, "dataset_ids": []})
    collection = f"/api/v1/source-workspaces/{workspace['id']}/technical-specifications"
    created = live.api("POST", collection, {"title": "QA ТЗ " + uuid4().hex})
    path = collection + "/" + created["id"]
    saved = live.api("PUT", path, {"version": created["version"], "title": created["title"], "sections": original})
    assert live.response("GET", path, token="invalid-synthetic-token").status_code == 401
    assert live.response("PUT", path, {"version": saved["version"], "title": saved["title"], "sections": original}, token=live.reader()).status_code in (403, 404)
    live.browser(page, f"/technical-specifications?workspace={workspace['id']}&document={created['id']}")
    expect(page.get_by_role("textbox", name="Раздел 5", exact=True)).to_be_visible(timeout=30000)
    page.get_by_role("button", name="Открыть помощника", exact=True).click()
    assistant = page.get_by_role("complementary", name="Помощник по ТЗ", exact=True)
    page.get_by_label("Контекст чата").select_option("5")
    assistant.get_by_role("textbox", name="Сообщение помощнику").fill(case["text"])
    with page.expect_response(lambda r: r.request.method == "POST" and r.url == live.base + path + "/chat", timeout=360000) as response:
        assistant.get_by_role("button", name="Отправить", exact=True).click()
    assert response.value.status == 200
    submitted = response.value.request.post_data_json
    chat = response.value.json()["data"]
    assert chat["sections"] == original and chat["revision"] == saved["revision"]
    proposal = next(p for p in chat["proposals"] if p.get("message_id") == submitted["draft"]["message_id"])
    assert proposal["status"] == "ready" and proposal["sections"] == expected
    assistant.get_by_role("button", name="Посмотреть предложение 1", exact=True).click()
    comparison = page.get_by_role("region", name="Сравнение предложенных изменений", exact=True)
    with page.expect_response(lambda r: r.request.method == "POST" and r.url == live.base + path + "/apply", timeout=360000) as response:
        comparison.get_by_role("button", name="Применить изменения", exact=True).click()
    assert response.value.status == 200
    applied = response.value.json()["data"]
    assert next(p for p in applied["proposals"] if p["id"] == proposal["id"])["status"] == "applied"
    assert applied["sections"] == expected and applied["revision"] == saved["revision"] + 1
    replay = live.api("POST", path + "/chat", submitted)
    assert replay["version"] == applied["version"] and replay["revision"] == applied["revision"]
    assert len([m for m in replay["messages"] if m["id"] == submitted["draft"]["message_id"]]) == 1
    for method, suffix, bad, status in [
        ("PUT", "", {"version": saved["version"], "title": saved["title"], "sections": original}, 409),
        ("PUT", "", {"version": applied["version"], "title": applied["title"], "sections": {k: v for k, v in expected.items() if k != "9"}}, 422),
        ("POST", "/chat", {**submitted, "draft": {**submitted["draft"], "text": submitted["draft"]["text"] + " Другое содержимое."}}, 409),
    ]:
        assert live.response(method, path + suffix, bad).status_code == status
        assert live.api("GET", path) == applied
    page.reload()
    expect(page.get_by_role("textbox", name="Раздел 5", exact=True)).to_contain_text("Отправить")
    page.screenshot(path=str(live.out / "tz-real.png"))
    assert live.api("GET", path + "/revisions/2")["sections"] == original
    assert live.api("GET", path + "/revisions/3")["sections"] == expected
    live.proof(
        "tz-real-proof",
        {
            "workspace_id": workspace["id"],
            "document_id": created["id"],
            "message_id": submitted["draft"]["message_id"],
            "upstream_transactions": live.inference([marker], 2),
            "revision": applied["revision"],
            "exact_nine_sections": True,
            "idempotency": True,
            "negative_unchanged": True,
            "browser_reload": True,
        },
    )


@pytest.fixture
def real_sql_catalog(live):
    """Register one actual quarantined table in the actual metadata service."""
    marker = "regression_probe_" + uuid4().hex[:18]
    schema = "qa_" + uuid4().hex[:16]
    role = "qa_ro_" + uuid4().hex[:16]
    password = secrets.token_hex(24)
    live.sql(
        f"CREATE SCHEMA {schema};\n"
        f"CREATE TABLE {schema}.regression_orders (id integer PRIMARY KEY,status varchar(16) NOT NULL,amount numeric(12,2) NOT NULL);\n"
        f"INSERT INTO {schema}.regression_orders VALUES (1,'paid',12.50),(2,'cancelled',99.00),(3,'paid',7.25);\n"
        f"CREATE ROLE {role} LOGIN PASSWORD '{password}';\n"
        f"GRANT USAGE ON SCHEMA {schema} TO {role};\n"
        f"GRANT SELECT ON {schema}.regression_orders TO {role};\n"
        f"ALTER ROLE {role} SET default_transaction_read_only=on;\n"
        "SELECT json_build_object('created',true,'rows',3,'readonly_role',true);"
    )
    metadata = requests.Session()
    base = os.environ.get("QA_OPENMETADATA_HOST_URL", "http://127.0.0.1:8585")
    token = os.environ.get("QA_OPENMETADATA_JWT_TOKEN", "")
    if not token:
        r = metadata.post(
            base + "/api/v1/users/login",
            json={"email": os.environ["QA_OPENMETADATA_USERNAME"], "password": base64.b64encode(os.environ["QA_OPENMETADATA_PASSWORD"].encode()).decode()},
            timeout=(10, 90),
        )
        assert r.status_code == 200, f"Real OpenMetadata login: HTTP {r.status_code}"
        token = r.json()["accessToken"]
    metadata.headers["Authorization"] = "Bearer " + token
    timings = []
    application_probes = []

    def om(method, path, value=None, *, content_type=None):
        started = time.monotonic()
        r = metadata.request(method, base + "/api/v1/" + path, json=value, headers={"Content-Type": content_type} if content_type else {}, timeout=(10, 120))
        timings.append({"method": method, "path": path.split("?")[0], "http": r.status_code, "seconds": round(time.monotonic() - started, 3)})
        assert r.status_code in (200, 201), f"Real OpenMetadata {method} {path.split('?')[0]}: HTTP {r.status_code}"
        return r.json()

    service = None
    try:
        service = om(
            "POST", "services/databaseServices", {"name": marker, "serviceType": "Postgres", "description": "Disposable live QA: an actual read-only PostgreSQL table, removed after this test."}
        )
        database = om("POST", "databases", {"name": "rag_flow", "service": marker})
        scope = om("POST", "databaseSchemas", {"name": schema, "database": database["fullyQualifiedName"]})
        table = om(
            "POST",
            "tables",
            {
                "name": "regression_orders",
                "databaseSchema": scope["fullyQualifiedName"],
                "description": "Synthetic paid and cancelled orders. id identifies an order, status is paid or cancelled, amount is its exact decimal amount.",
                "columns": [
                    {"name": "id", "dataType": "INT", "constraint": "PRIMARY_KEY", "description": "Order identifier"},
                    {"name": "status", "dataType": "VARCHAR", "dataLength": 16, "description": "paid or cancelled"},
                    {"name": "amount", "dataType": "DECIMAL", "precision": 12, "scale": 2, "description": "Exact order amount"},
                ],
            },
        )
        changed = om(
            "PATCH",
            "tables/" + table["id"],
            [{"op": "replace", "path": "/description", "value": "Updated live QA description: paid and cancelled orders, exact decimal amounts."}],
            content_type="application/json-patch+json",
        )
        assert changed["description"].startswith("Updated live QA description")
        assert om("GET", "tables/" + table["id"])["description"] == changed["description"]
        # This uses the real application's own client from the separate container.
        application_probe = live.application(
            # Load the actual client file without bootstrapping a second API
            # process. The normal SQL HTTP and worker paths below exercise its
            # production package wiring separately.
            "import importlib.util,sys,time\n"
            "spec=importlib.util.spec_from_file_location('qa_metadata_client','/ragflow/api/apps/services/openmetadata_copilot_service.py')\n"
            "module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)\n"
            "client=module.OpenMetadataClient(module.OpenMetadataConfig.from_env());started=time.monotonic()\n"
            f"changed=client.patch('/api/v1/tables/{table['id']}',[{{'op':'replace','path':'/description','value':'Live application client confirmed the private container metadata route.'}}])\n"
            f"read=client.get_table({table['id']!r})\n"
            "print('QA_DB_RESULT='+json.dumps({'write_read':changed['description']==read['description'],'seconds':round(time.monotonic()-started,3),'base_url':client.config.base_url}))"
        )
        assert application_probe["write_read"]
        application_probes.append(application_probe)
        # Search is eventually indexed by the real service; wait for this exact ID.
        deadline = time.monotonic() + 120
        while True:
            result = om("GET", "search/query?q=" + quote(table["fullyQualifiedName"], safe="") + "&index=table_search_index&size=10")
            if any(h.get("_source", {}).get("id") == table["id"] for h in result.get("hits", {}).get("hits", [])):
                break
            assert time.monotonic() < deadline, "Actual OpenMetadata search did not index the QA table"
            time.sleep(2)
        connector = live.api(
            "POST",
            "/api/v1/connectors",
            {"name": marker, "source": "postgresql", "config": {"host": "postgres", "port": 5432, "database": "rag_flow", "credentials": {"username": role, "password": password}}},
        )
        profile = live.api(
            "POST",
            PREFIX + "/execution-profiles",
            {
                "schema_version": "1",
                "name": marker,
                "connector_id": connector["id"],
                "dialect": "postgres",
                "allowed_schemas": [schema],
                "statement_timeout_ms": 5000,
                "max_rows": 100,
                "max_result_bytes": 1048576,
                "enabled": True,
            },
        )
        binding = live.api(
            "POST",
            PREFIX + "/catalog-bindings",
            {"schema_version": "1", "catalog_service": marker, "catalog_database": "rag_flow", "catalog_schema": schema, "execution_profile_id": profile["id"], "enabled": True},
        )
        yield {"table": table, "profile": profile, "binding": binding, "schema": schema, "application_probe": application_probe}
    finally:
        if service is None:
            lookup = metadata.get(base + "/api/v1/services/databaseServices/name/" + marker, timeout=30)
            if lookup.status_code == 200:
                service = lookup.json()
            else:
                assert lookup.status_code == 404, "Cannot resolve outcome of own metadata service creation"
        if service:
            assert service["name"] == marker and marker.startswith("regression_probe_")
            r = metadata.delete(base + "/api/v1/services/databaseServices/" + service["id"], params={"recursive": "true", "hardDelete": "true"}, timeout=(10, 120))
            assert r.status_code in (200, 204), f"Own metadata service cleanup: HTTP {r.status_code}; id={service['id']}"
            r = metadata.get(base + "/api/v1/services/databaseServices/" + service["id"], timeout=30)
            assert r.status_code == 404, f"Own metadata service remains: {service['id']}"
        live.proof(
            "openmetadata-real-proof",
            {"requests": timings, "application_clients": application_probes, "own_service_id": service["id"] if service else None, "cleanup_verified": service is not None, "no_fault_injection": True},
        )
        metadata.close()


def test_sql_real_catalog_llm_worker_execution_and_reload(page, live, real_sql_catalog):
    fixture = real_sql_catalog
    table = fixture["table"]
    title = "regression-probe-sql-" + uuid4().hex
    request = f"Используй только таблицу {table['fullyQualifiedName']}. Покажи отдельные заказы со status = 'paid': поля id и amount, с именами выходных полей id и amount. Сортируй по id по возрастанию. Без агрегации, без JOIN, лимит 10 строк, без изменения данных."
    project = live.api("POST", PREFIX + "/projects", {"schema_version": "1", "title": title, "source_request": request, "locale": "ru"})
    path = PREFIX + "/projects/" + project["id"]
    jobs = []

    def fresh():
        return live.api("GET", path)

    def job(kind):
        p = fresh()
        payload = {
            "schema_version": "1",
            "expected_state_version": p["state_version"],
            "idempotency_key": uuid4().hex,
            "kind": kind,
            "payload": {"locale": "ru", **({"terms": []} if kind == "SCHEMA" else {})},
        }
        queued = live.api("POST", path + "/agent-jobs", payload)
        job_id = queued["current_job"]["id"]
        assert live.api("POST", path + "/agent-jobs", payload)["current_job"]["id"] == job_id
        jobs.append({"id": job_id, "kind": kind})
        deadline = time.monotonic() + 480
        while True:
            p = fresh()
            assert not p.get("last_error"), f"Real SQL {kind} failed: {(p.get('last_error') or {}).get('code')}"
            if p["operation_state"] == "REVIEW" and p.get("pending_proposal"):
                result = p["pending_proposal"]["payload"]["agent_result"]
                assert result.get("status") not in {"FALLBACK", "SKIPPED", "FAILED"}
                return p, result
            assert time.monotonic() < deadline, f"Real SQL {kind} worker timeout"
            time.sleep(2)

    def accept(p, artifact):
        return live.api(
            "POST",
            path + "/proposals/" + p["pending_proposal"]["id"] + "/decision",
            {"schema_version": "1", "expected_state_version": p["state_version"], "idempotency_key": uuid4().hex, "decision": "ACCEPT", "artifact_payload": artifact},
        )

    p, result = job("REQUIREMENTS")
    assert result["status"] == "PROPOSED", "Complete QA question unexpectedly needs clarification"
    p = accept(p, {"answers": {}})
    p, result = job("SCHEMA")
    live.proof("sql-schema-stage", {"project_id": project["id"], "requirements": p["artifacts"]["requirements"], "agent_result": result})
    assert result["llm"]["status"] == "APPLIED", "Schema search bypassed the actual LLM"
    resolutions = result["resolutions"]
    resolution = next(r for r in resolutions if any(e["id"] == table["id"] for e in r["catalog_answer"].get("entities", [])))
    detail = live.api("POST", PREFIX + "/schema/entities", {"entity_ids": [table["id"]], "locale": "ru"})["entities"][0]
    entity = detail["entity"]
    assert detail["lookup"]["status"] == "OK" and entity["schema_loaded"] and not detail["freshness"]["stale"]
    allowed_table = {
        "id",
        "fqn",
        "name",
        "display_name",
        "technical_name",
        "description",
        "version",
        "updated_at",
        "service",
        "database",
        "schema",
        "owners",
        "domains",
        "tags",
        "glossary_terms",
        "url",
        "matched_by",
        "column_count",
        "schema_loaded",
        "schema_fingerprint",
        "table_constraints",
    }
    allowed_column = {"id", "name", "fqn", "data_type", "description", "constraint", "glossary_terms"}
    selected = {k: v for k, v in entity.items() if k in allowed_table}
    selected.update(
        columns=[{**{k: v for k, v in column.items() if k in allowed_column}, "selected": True} for column in entity["column_details"]],
        loaded_column_count=len(entity["column_details"]),
        columns_truncated=False,
    )
    canonical = "\n".join("- " + r["statement"].strip() for r in p["artifacts"]["requirements"]["requirements"] if r["statement"].strip())
    snapshot = {
        "format": "ragflow-sql-schema-snapshot",
        "schema_version": "1",
        "status": "READY",
        "original_requirements": canonical,
        "source": {
            "type": "openmetadata",
            "catalog_snapshot_at": detail["freshness"]["snapshot_at"],
            "checked_at": detail["freshness"]["checked_at"],
            "stale": False,
            "retrieval": [detail["retrieval"]],
            "references": detail["sources"],
        },
        "requirements": [{"term": resolution["term"], "state": "CONFIRMED", "decision": "USER", "interpretation": resolution["interpretation"], "selected_table": selected}],
        "warnings": detail["warnings"],
    }
    p = accept(p, {"schema_snapshot": snapshot, "accepted_schema": [{"entity_id": entity["id"], "version": entity["version"], "schema_fingerprint": entity["schema_fingerprint"]}]})
    p, result = job("QUERY")
    assert result["status"] == "PROPOSED"
    missing_confirmation = live.response(
        "POST",
        path + "/proposals/" + p["pending_proposal"]["id"] + "/decision",
        {"schema_version": "1", "expected_state_version": p["state_version"], "idempotency_key": uuid4().hex, "decision": "ACCEPT", "artifact_payload": {}},
    )
    assert missing_confirmation.status_code == 422
    assert fresh()["state_version"] == p["state_version"]
    p = accept(p, {"confirmed_join_ids": [j["id"] for j in result["proposal"]["joins"]], "confirmed_filter_ids": [f["id"] for f in result["proposal"]["filters"]]})
    compiled = live.api("POST", path + "/compilations", {"schema_version": "1", "expected_state_version": p["state_version"], "idempotency_key": uuid4().hex})
    assert compiled["compilation"]["status"] == "READY" and compiled["compilation"]["guard"]["status"] == "PASS"
    p = compiled["project"]
    profile_id = fixture["profile"]["id"]
    checked = live.api("POST", path + "/connection/check", {"schema_version": "1", "selected_profile_id": profile_id})
    assert checked["status"] == "AVAILABLE" and checked["query_checked"] is False
    preflight = live.api("GET", path + "/preflight?selected_profile_id=" + profile_id)
    assert preflight["binding"]["status"] == "BOUND"
    assert not preflight.get("blocker")
    stale = live.response("POST", path + "/runs", {"schema_version": "1", "expected_state_version": p["state_version"] - 1, "idempotency_key": uuid4().hex, "selected_profile_id": profile_id})
    assert stale.status_code == 409
    p = fresh()
    payload = {"schema_version": "1", "expected_state_version": p["state_version"], "idempotency_key": uuid4().hex, "selected_profile_id": profile_id}
    run = live.api("POST", path + "/runs", payload)
    assert live.api("POST", path + "/runs", payload)["run_id"] == run["run_id"]
    deadline = time.monotonic() + 180
    while True:
        p = fresh()
        stored = next(r for r in p["runs"] if r["id"] == run["run_id"])
        assert stored["status"] not in {"FAILED", "CANCELLED", "PURGED"}, "Real SQL execution failed"
        if stored["status"] == "READY":
            break
        assert time.monotonic() < deadline, "SQL run worker timeout"
        time.sleep(1)
    preview = live.api("GET", path + "/runs/" + run["run_id"] + "/preview?offset=0")
    assert preview["columns"] == ["id", "amount"]
    assert [(int(r[0]), Decimal(str(r[1]))) for r in preview["rows"]] == [(1, Decimal("12.50")), (3, Decimal("7.25"))]
    assert preview["row_count"] == 2
    live.browser(page, "/sql-projects?project=" + project["id"])
    expect(page.get_by_test_id("sql-result-table")).to_be_visible(timeout=30000)
    expect(page.get_by_test_id("sql-result-table")).to_contain_text("12.50")
    page.reload()
    expect(page.get_by_test_id("sql-result-table")).to_contain_text("7.25")
    page.screenshot(path=str(live.out / "sql-real.png"))
    assert live.api("GET", path + "/runs/" + run["run_id"] + "/preview?offset=0")["rows"] == preview["rows"]
    live.proof("sql-execution-proof", {"project_id": project["id"], "jobs": jobs, "run_id": run["run_id"], "rows": preview["rows"], "columns": preview["columns"], "browser_reload": True})
    mixed = live.api(
        "POST",
        PREFIX + "/schema/resolve",
        {"requirements": f"Нужны заказы из {table['fullyQualifiedName']} и ФИО клиента из справочника клиентов. Найди оба источника.", "terms": [], "locale": "ru"},
    )
    assert len(mixed["resolutions"]) >= 2, "Explicit reference hid a required business entity"
    assert any(e["id"] == table["id"] for r in mixed["resolutions"] for e in r["catalog_answer"].get("entities", []))
    negative = live.api(
        "POST",
        PREFIX + "/schema/resolve",
        {"requirements": f"Не используй {table['fullyQualifiedName']}; найди актуальные заказы из других таблиц каталога.", "terms": [], "locale": "ru"},
    )
    assert all(r["catalog_answer"]["question"] != table["fullyQualifiedName"] for r in negative["resolutions"]), "Excluded source became an exact query"
    assert all(r["interpretation"].get("recommended_entity_id") != table["id"] for r in negative["resolutions"]), "Excluded source was recommended"
    live.proof("sql-source-semantics-proof", {"mixed": mixed, "excluded": negative})
    live.proof(
        "sql-real-proof",
        {
            "project_id": project["id"],
            "jobs": jobs,
            "run_id": run["run_id"],
            "catalog_entity": table["id"],
            "real_metadata": True,
            "upstream_transactions": live.inference([table["service"]["name"], fixture["schema"], table["id"]], 4),
            "worker_claims": live.worker_claims(
                [j["id"] for j in jobs] + [live.sql(f"SELECT json_build_object('job_id',job_id) FROM business_document_sql_query_run WHERE id='{run['run_id']}';")["job_id"]]
            ),
            "rows": preview["rows"],
            "columns": preview["columns"],
            "connection_check": checked["status"],
            "preflight_binding": preflight["binding"]["status"],
            "browser_reload": True,
            "idempotency": True,
        },
    )


@pytest.fixture
def real_eva_page(live, request):
    config = json.loads(os.environ["QA_EVA_CONNECTOR_CONFIG"])
    token = config["credentials"]["eva_api_token"]
    original = urlsplit(config["api_base_url"])
    assert not original.username and not original.password
    # The host fixture reaches the same published service through loopback;
    # the real application retains its configured container route.
    target = (
        urlunsplit((original.scheme, "127.0.0.1" + (":" + str(original.port) if original.port else ""), original.path, "", ""))
        if original.hostname == "host.docker.internal"
        else config["api_base_url"]
    )
    session = requests.Session()
    session.headers["X-Eva-Token"] = token
    marker = "regression-probe-eva-" + uuid4().hex
    code = "QA-" + uuid4().hex[:16]
    page_id = None

    def rpc(method, kwargs=None, args=None):
        body = {"method": method, "kwargs": kwargs or {}, "callid": uuid4().hex, "jsonrpc": "2.2"}
        if args is not None:
            body["args"] = args
        response = session.post(urljoin(target.rstrip("/") + "/", "api/?m=" + quote(method, safe="")), json=body, verify=config.get("verify_ssl", True), timeout=(10, 90), allow_redirects=False)
        assert response.status_code == 200, "Real EVA fixture HTTP failure"
        result = response.json()
        if result.get("error") or result.get("abort") or "result" not in result:
            raise AssertionError("Real EVA fixture RPC failure: " + method)
        return result["result"]

    def own():
        identity_filter = ["id", "==", page_id] if page_id else ["name", "==", marker]
        return rpc(
            "CmfDocument.list",
            {
                "filter": json.dumps([["project_id", "==", config["project_id"]], identity_filter, ["cmf_deleted", "==", False]]),
                "fields": ["id", "code", "name", "text", "text_render", "text_draft"],
                "order_by": ["id"],
                "slice": [0, 2],
            },
        )

    try:
        page_id = rpc("CmfDocument.create", {"project": config["project_id"], "name": marker, "code": code, "text_draft": getattr(request, "param", "<p>Own disposable regression page.</p>")})
        assert isinstance(page_id, str) and page_id.startswith("CmfDocument:")
        live.proof("eva-page-fixture", {"page_id": page_id, "code": code, "cleanup_verified": False})
        rpc("CmfDocument.do_publish", {}, [page_id])
        created_page = own()
        assert len(created_page) == 1
        connection = {key: config[key] for key in ("api_base_url", "project_id")}
        connection.update(web_base_url=config.get("web_base_url") or config["api_base_url"], verify_ssl=config.get("verify_ssl", True), include_archived=config.get("include_archived", False))
        live.api("PUT", "/api/v1/admin/business-documents", {"eva_connection": connection})
        access = live.api("GET", "/api/v1/business-documents/eva/access")
        connector_id = access["connector_id"]
        assert access["state"] != "AVAILABLE"
        live.api("PUT", "/api/v1/users/me/eva-credentials/" + connector_id, {"eva_api_token": token})
        assert live.api("GET", "/api/v1/business-documents/eva/access")["state"] == "AVAILABLE"
        yield {
            "id": page_id,
            "url": connection["web_base_url"].rstrip("/") + "/project/Document/" + quote(created_page[0]["code"], safe=""),
            "web_base": connection["web_base_url"],
            "read": own,
            "connection_id": connector_id,
        }
    finally:
        if page_id is None:
            found = own()
            assert len(found) <= 1, "Own EVA creation outcome is ambiguous"
            page_id = found[0]["id"] if found else None
        if page_id:
            rpc("CmfDocument.delete", {}, [page_id])
            assert own() == [], "Own EVA page remains"
        live.proof("eva-page-fixture", {"page_id": page_id, "code": code, "cleanup_verified": page_id is not None, "no_fault_injection": True})
        session.close()


def test_bt_real_draft_persistence_and_eva_publication(page, live, real_eva_page):
    corpus = json.loads((ROOT / "agent/business_requirements/golden_model_quality/v2.json").read_bytes())
    case = next(c for c in corpus["cases"] if c["id"] == "M02_complete_appointment_draft")
    marker = "LIVE_BT_DRAFT_" + uuid4().hex
    facts = "\n".join(chunk["content"] for chunk in case["evidence_chunks"])
    idea = case["idea"] + "\n\nПодтверждённые исходные факты:\n" + facts + "\n\nАвторские уточнения:\n" + "\n".join(case["answers_by_section"].values()) + "\nИдентификатор запроса: " + marker
    root = "/api/v1/business-documents"
    document = live.api("POST", root, {"schema_version": "1", "document_type": "business_requirements", "title": "regression-probe-bt-full-" + marker, "idea": idea, "dataset_ids": []})
    path = root + "/" + document["document_id"]
    jobs = []

    def fresh():
        return live.api("GET", path)

    def command(kind, payload, *, wait=False):
        current = fresh()
        key = uuid4().hex
        body = {"schema_version": "1", "command_id": key, "idempotency_key": key, "expected_state_version": current["state_version"], "type": kind, "payload": payload}
        queued = live.api("POST", path + "/commands", body)
        if not wait:
            return fresh()
        job_id = queued["job_id"]
        jobs.append({"kind": kind, "id": job_id})
        assert live.api("POST", path + "/commands", body)["job_id"] == job_id
        deadline = time.monotonic() + 720
        while True:
            current = fresh()
            job = current.get("latest_job") or {}
            if job.get("job_id") == job_id:
                assert job.get("status") not in {"FAILED", "DEAD", "CANCELLED"}, "Real BT generation worker failed: " + kind
                if job.get("status") == "COMPLETED":
                    return current
            assert time.monotonic() < deadline, "Real BT generation timeout: " + kind
            time.sleep(2)

    try:
        for _ in range(5):
            current = command("REQUEST_INTAKE_ASSESSMENT", {}, wait=True)
            if "REQUEST_DRAFT" in current["allowed_commands"]:
                break
            questions = [q for q in current["protocol"]["questions"] if q["status"] == "OPEN"]
            assert questions, "Intake did not expose a resolvable boundary"
            for question in questions:
                section = question.get("target_section_id") or ""
                answer = case["answers_by_section"].get(section) or case["answers_by_section"].get(section.split(".")[0])
                assert answer, "No authored answer for the model's intake question"
                command("ANSWER_QUESTION", {"question_id": question["question_id"], "selected_option_id": None, "custom_answer": answer})
        else:
            raise AssertionError("Actual intake did not close within five authored rounds")
        drafted = command("REQUEST_DRAFT", {}, wait=True)
        assert drafted["lifecycle_state"] == "REVIEW" and drafted["current_revision"]
        revision_id = drafted["current_revision"]["revision_id"]
        revision = live.api("GET", path + "/revisions/" + revision_id)
        assert revision["body_markdown"] and "@startuml" in revision["body_markdown"]
        assert live.api("GET", path + "/revisions/" + revision_id)["body_markdown"] == revision["body_markdown"]
        live.proof(
            "bt-draft-proof",
            {
                "document_id": document["document_id"],
                "revision_id": revision_id,
                "lifecycle": drafted["lifecycle_state"],
                "jobs": jobs,
                "input_variant": "M02 authored facts in idea; no controlled evidence adapter",
                "markdown_characters": len(revision["body_markdown"]),
                "plantuml_count": revision["body_markdown"].count("@startuml"),
                "worker_claims": live.worker_claims([j["id"] for j in jobs]),
                "upstream_transactions": live.inference([marker], 2),
            },
        )
        denied_key = uuid4().hex
        denied = live.response(
            "POST",
            path + "/commands",
            {
                "schema_version": "1",
                "command_id": denied_key,
                "idempotency_key": denied_key,
                "expected_state_version": drafted["state_version"],
                "type": "REQUEST_EXPORT",
                "payload": {"revision_id": revision_id, "format": "MARKDOWN"},
            },
        )
        assert denied.status_code == 409, "Unagreed draft was exported"
        assert fresh()["state_version"] == drafted["state_version"]
        live.browser(page, "/business-documents/" + document["document_id"])
        expect(page.get_by_test_id("business-document-workbench")).to_be_visible(timeout=30000)
        page.reload()
        expect(page.get_by_test_id("business-document-workbench")).to_be_visible(timeout=30000)
        page.screenshot(path=str(live.out / "bt-draft-real.png"))
        live.api("POST", path + "/eva/rebind", {"expected_state_version": fresh()["state_version"], "eva_page_url": real_eva_page["url"]})
        change = live.api("POST", path + "/eva/changes", {"expected_state_version": fresh()["state_version"]})
        change_path = root + "/eva/changes/" + change["change_id"]
        for action, expected in [("approve", "APPROVED"), ("prepare", "EVA_DRAFT_READY"), ("publish", "PUBLISHED")]:
            change = live.api("POST", change_path + "/" + action, {"expected_state_version": change["state_version"]})
            assert change["workflow_state"] == expected
        remote = real_eva_page["read"]()
        assert len(remote) == 1 and remote[0]["id"] == real_eva_page["id"]
        published_html = remote[0].get("text") or remote[0].get("text_render") or ""
        assert remote[0]["name"] == document["title"] and "<svg" in published_html
        assert fresh()["current_revision"]["revision_id"] == revision_id and fresh()["lifecycle_state"] == "REVIEW"
        live.api("DELETE", "/api/v1/users/me/eva-credentials/" + real_eva_page["connection_id"])
        assert live.api("GET", "/api/v1/business-documents/eva/access")["state"] != "AVAILABLE"
        live.proof(
            "bt-publication-proof",
            {
                "document_id": document["document_id"],
                "revision_id": revision_id,
                "eva_page_id": real_eva_page["id"],
                "change_id": change["change_id"],
                "state": change["workflow_state"],
                "published_svg_count": published_html.count("<svg"),
                "revision_unchanged": True,
                "personal_token_removed": True,
                "unagreed_export_denied": True,
            },
        )
    finally:
        live.api("DELETE", path)
        assert live.response("GET", path).status_code == 404


@pytest.mark.parametrize("real_eva_page", ["<p></p>"], indirect=True)
def test_tz_real_eva_publish_pull_copy_import_and_markdown(page, live, real_eva_page):
    corpus = json.loads((ROOT / "test/evals/technical_specifications/golden.v2.json").read_bytes())
    case = next(c for c in corpus["cases"] if c["id"] == "C02")
    sections = deepcopy(corpus["documents"][case["document"]])
    marker = "LIVE_TZ_EVA_" + uuid4().hex
    sections["1"] += "\nИдентификатор тестового обмена: " + marker + "."
    workspace = live.api("POST", "/api/v1/source-workspaces", {"title": "regression-probe-tz-eva-" + uuid4().hex, "dataset_ids": []})
    collection = f"/api/v1/source-workspaces/{workspace['id']}/technical-specifications"
    document = live.api("POST", collection, {"title": "regression-probe-TZ-EVA-" + uuid4().hex})
    path = collection + "/" + document["id"]
    saved = live.api("PUT", path, {"version": document["version"], "title": document["title"], "sections": sections})
    linked = live.api("POST", path + "/connect-eva", {"version": saved["version"], "url": real_eva_page["url"]})
    assert linked["sections"] == sections and linked["revision"] == saved["revision"] and linked["eva"]
    stale = live.response("POST", path + "/publish-eva", {"version": saved["version"]})
    assert stale.status_code == 409
    published = live.api("POST", path + "/publish-eva", {"version": linked["version"]})
    assert published["eva_export"]["status"] == "published" and published["eva_export"]["revision"] == saved["revision"]
    remote = real_eva_page["read"]()
    assert len(remote) == 1 and remote[0]["name"] == document["title"]
    assert marker in (remote[0].get("text") or remote[0].get("text_render") or "")
    current = live.api("GET", path)
    pulled = live.api("POST", path + "/pull", {"version": current["version"]})
    assert pulled["sections"] == sections and pulled["revision"] == saved["revision"] and not pulled["proposals"]
    exported = live.api("POST", path + "/export", {"revision": pulled["revision"]})
    assert exported["filename"].endswith(".md") and "markdown" in exported["mime_type"]
    assert exported["revision"] == saved["revision"] and all(content in exported["content"] for content in sections.values())
    url = real_eva_page["web_base"].rstrip("/") + "/project/Document/" + quote(remote[0]["code"], safe="")
    original = live.api("POST", collection + "/import-eva", {"url": url, "copy": False, "operation_id": uuid4().hex})
    assert original["id"] == document["id"]
    operation = uuid4().hex
    copied = live.api("POST", collection + "/import-eva", {"url": url, "copy": True, "operation_id": operation})
    assert copied["id"] != document["id"] and copied["revision"] == 1 and copied["eva"] is None
    assert copied["origin"]["document_id"] == real_eva_page["id"]
    assert set(copied["sections"]) == set(sections)
    # External HTML is decoded to Markdown. Escaping and hard-break spelling
    # may normalize; the complete rendered content and structure must survive.
    render = mistune.create_markdown(hard_wrap=True, plugins=["table", "task_lists", "strikethrough", "url"])
    assert {key: render(value) for key, value in copied["sections"].items()} == {key: render(value) for key, value in sections.items()}
    assert live.api("GET", path)["sections"] == sections
    status = live.api("GET", collection + "/import-eva/" + operation)
    assert status["state"] == "completed" and status["document"]["id"] == copied["id"]
    assert live.api("GET", collection + "/" + copied["id"])["sections"] == copied["sections"]
    live.browser(page, f"/technical-specifications?workspace={workspace['id']}&document={copied['id']}")
    expect(page.get_by_role("textbox", name="Раздел 1", exact=True)).to_contain_text(marker, timeout=30000)
    page.reload()
    expect(page.get_by_role("textbox", name="Раздел 1", exact=True)).to_contain_text(marker, timeout=30000)
    page.screenshot(path=str(live.out / "tz-eva-real.png"))
    live.api("DELETE", "/api/v1/users/me/eva-credentials/" + real_eva_page["connection_id"])
    assert live.api("GET", "/api/v1/business-documents/eva/access")["state"] != "AVAILABLE"
    live.proof(
        "tz-eva-proof",
        {
            "workspace_id": workspace["id"],
            "document_id": document["id"],
            "copy_id": copied["id"],
            "eva_page_id": real_eva_page["id"],
            "published_revision": published["eva_export"]["revision"],
            "exact_nine_sections": True,
            "copy_presentation_preserved": True,
            "original_source_unchanged": True,
            "copy_markdown_comparison": "Complete rendered HTML; external HTML import normalizes Markdown escapes and line breaks",
            "unchanged_pull": True,
            "same_origin_reimport": True,
            "actual_copy_import": True,
            "markdown_export": True,
            "browser_reload": True,
            "personal_token_removed": True,
            "ai_required_for_exchange": False,
        },
    )
