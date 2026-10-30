"""C19: an actual catalog permission revocation hides retained answers and rows.

The disposable runner has no catalog Dataset: its real catalog gate is admin
access. An additional QA admin owns the session, while the original QA admin
revokes/restores that principal through the real admin API. Its document-author
role stays enabled, so catalog denial is distinguished from a reader-only role.
"""

from copy import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from decimal import Decimal
from urllib.parse import quote
from uuid import uuid4

from playwright.sync_api import expect
import requests

from api.utils.crypt import crypt
from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
from test.playwright.e2e import sql_chat_relation_fixture as catalog_fixture

real_sql_catalog = catalog_fixture.real_sql_catalog
from test.playwright.e2e import test_sql_chat_calendar_live as calendar

chat_probe = chat.chat_probe


def _qa_actor(live):
    user, password = live.create_user(role="admin")
    assert user["email"].startswith("regression-probe-")
    live.api("PATCH", "/api/v1/business-documents/access/users/" + user["id"], {"role": "AUTHOR_CREATOR"})
    response = live.response("POST", "/api/v1/auth/login", {"email": user["email"], "password": crypt(password)})
    assert response.status_code == 200 and response.json()["code"] == 0
    actor = copy(live)
    actor.http = requests.Session()
    actor.http.headers["Authorization"] = response.headers["Authorization"]
    actor.user = actor.api("GET", "/api/v1/users/me")
    assert actor.user["is_superuser"] and actor.user["id"] != live.user["id"]
    # Configure the real tenant default, with the same native model window as
    # the runner. No model, response, authorization or API stub is installed.
    identity = json.loads((Path(live.out) / "identity.json").read_text(encoding="utf-8"))
    window = identity["ollama_models"]["qwen3.8:latest"]["context_length"]
    instance = "QA-revocation-" + uuid4().hex[:8]
    actor.api("PUT", "/api/v1/providers", {"provider_name": "Ollama"})
    actor.api(
        "POST",
        "/api/v1/providers/Ollama/instances",
        {
            "instance_name": instance,
            "api_key": "",
            "base_url": "http://host.docker.internal:11435",
            "region": "default",
            "model_info": [{"model_name": "qwen3.8:latest", "model_type": ["chat"], "max_tokens": window}],
        },
    )
    actor.api("PATCH", "/api/v1/models/default", {"model_provider": "Ollama", "model_instance": instance, "model_name": "qwen3.8:latest", "model_type": "chat"})
    defaults = actor.api("GET", "/api/v1/users/me/models")
    actor.api(
        "PATCH",
        "/api/v1/users/me/models",
        {"tenant_id": defaults["tenant_id"], "llm_id": "qwen3.8:latest@" + instance + "@Ollama", "embd_id": "", "img2txt_id": "", "asr_id": "", "rerank_id": "", "tts_id": ""},
    )
    actor.started_at = datetime.now(timezone.utc).isoformat()
    return actor


def test_chat_real_catalog_revocation_hides_a_cached_answer_and_blocks_export(browser, live, chat_probe, real_sql_catalog):
    actor = _qa_actor(live)
    context = browser.new_context(locale="ru-RU", viewport={"width": 1280, "height": 900})
    page = context.new_page()
    revoked = False
    admin_path = "/api/v1/admin/users/" + quote(actor.user["email"], safe="") + "/admin"
    try:
        chat._start(page, actor, chat_probe, calendar._question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
        original = chat._rows(page, actor, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
        known = {message["id"] for message in original["session"]["messages"]}
        chat._send(page, chat_probe, "Сложи amount только по сохранённым строкам результата; новый SQL не нужен.")
        before = chat._wait(page, actor, chat_probe, lambda value: any(message["id"] not in known and message.get("answer", {}).get("status") == "READY" for message in value["session"]["messages"]))
        answer = next(message["answer"] for message in before["session"]["messages"] if message["id"] not in known and message.get("answer", {}).get("status") == "READY")
        assert answer["calculation"]["operation"] == "sum"
        rendered = re.search(r":\s*(-?\d+(?:[.,]\d+)?)\.", answer["text"])
        assert rendered and Decimal(rendered.group(1).replace(",", ".")) == Decimal(490 if real_sql_catalog["calendar_discriminator"] else 240)
        assert {run["id"] for run in before["session"]["project"]["runs"]} == {run["id"] for run in original["session"]["project"]["runs"]}
        expect(chat._root(page)).to_contain_text(answer["text"])
        assert chat._identity(before) == chat._identity(original)
        live.api("DELETE", admin_path)
        revoked = True
        current_user = actor.api("GET", "/api/v1/users/me")
        assert current_user["is_superuser"] is False
        capabilities = actor.api("GET", "/api/v1/business-documents/capabilities")
        assert capabilities["capabilities"]["create"] is True
        denied = {}
        session_path = chat.SESSIONS + "/" + chat_probe["id"]
        for name, path in {"session": session_path, "rows": session_path + "/results/" + before["result"]["run_id"]}.items():
            response = actor.response("GET", path)
            assert response.status_code == 403 and answer["text"] not in response.text
            denied[name] = response.status_code
        response = actor.response(
            "POST", native.PREFIX + "/projects/" + chat_probe["id"] + "/export", {"schema_version": "1", "kind": "RESULT", "format": "json", "run_id": before["result"]["run_id"]}
        )
        assert response.status_code == 403 and before["result"]["sql"] not in response.text
        denied["export"] = response.status_code
        response = actor.response(
            "POST",
            session_path + "/commands",
            {
                "client_request_id": uuid4().hex,
                "expected_revision": before["session"]["revision"],
                "expected_project_version": before["session"]["project"]["state_version"],
                "type": "message",
                "payload": {"text": "Объясни выбранный результат"},
            },
        )
        assert response.status_code == 403
        denied["message"] = response.status_code
        chat._root(page).get_by_role("button", name="Обновить исследование", exact=True).click()
        expect(chat._root(page)).to_contain_text("Не удалось открыть исследование")
        expect(chat._root(page).get_by_role("table", name="Данные выбранного результата", exact=True)).to_have_count(0)
        expect(chat._root(page)).not_to_contain_text(answer["text"])
        page.screenshot(path=str(live.out / (chat_probe["proof_id"] + "-revoked.png")))
        live.api("PUT", admin_path)
        revoked = False
        page.reload(wait_until="domcontentloaded")
        restored = chat._rows(page, actor, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
        assert chat._identity(restored) == chat._identity(before)
        assert [message["id"] for message in restored["session"]["messages"]] == [message["id"] for message in before["session"]["messages"]]
        assert {run["id"] for run in restored["session"]["project"]["runs"]} == {run["id"] for run in before["session"]["project"]["runs"]}
        live.proof(
            chat_probe["proof_id"] + "-revocation",
            {
                "actual_distinct_qa_principal": True,
                "author_create_capability_retained": True,
                "catalog_admin_grant_revoked_and_restored": True,
                "denied": denied,
                "answer_hidden_on_refresh": True,
                "original_result_immutable": True,
            },
        )
        chat._complete(page, actor, chat_probe, restored)
    finally:
        if revoked:
            live.api("PUT", admin_path)
        context.close()
        actor.http.close()
