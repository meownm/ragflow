"""C18: actual reader capabilities and foreign-tenant read/export boundaries."""

from uuid import uuid4

from playwright.sync_api import expect

from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
from test.playwright.e2e import sql_chat_relation_fixture as catalog_fixture

real_sql_catalog = catalog_fixture.real_sql_catalog
from test.playwright.e2e import test_sql_chat_calendar_live as calendar

chat_probe = chat.chat_probe


def test_chat_reader_controls_and_foreign_tenant_cannot_read_or_export(page, browser, live, chat_probe, real_sql_catalog):
    chat._start(page, live, chat_probe, calendar._question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    original = chat._rows(page, live, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
    reader_token = live.reader()
    reader = live.api("GET", "/api/v1/users/me", token=reader_token)
    assert not reader["is_superuser"] and reader["id"] != live.user["id"]
    reader_sessions = live.api("GET", chat.SESSIONS, token=reader_token)
    assert chat_probe["id"] not in {session["id"] for session in reader_sessions["sessions"]}
    denied = {}
    paths = {
        "session": chat.SESSIONS + "/" + chat_probe["id"],
        "rows": chat.SESSIONS + "/" + chat_probe["id"] + "/results/" + original["result"]["run_id"],
        "project": native.PREFIX + "/projects/" + chat_probe["id"],
    }
    for name, path in paths.items():
        response = live.response("GET", path, token=reader_token)
        assert response.status_code == 404, "A foreign tenant must not discover the owner research"
        assert chat_probe["fqn"] not in response.text and original["result"]["sql"] not in response.text
        denied[name] = response.status_code
    response = live.response(
        "POST",
        native.PREFIX + "/projects/" + chat_probe["id"] + "/export",
        {"schema_version": "1", "kind": "RESULT", "format": "json", "run_id": original["result"]["run_id"]},
        token=reader_token,
    )
    assert response.status_code == 404 and chat_probe["fqn"] not in response.text
    denied["export"] = response.status_code
    response = live.response("POST", chat.SESSIONS, {"client_request_id": uuid4().hex, "question": "Покажи оплаченные заказы"}, token=reader_token)
    assert response.status_code == 403, "Reader creation must be rejected by the server before planning"
    denied["reader_create"] = response.status_code
    context = browser.new_context(locale="ru-RU", viewport={"width": 1280, "height": 900})
    try:
        reader_page = context.new_page()
        with reader_page.expect_response(lambda response: response.url.endswith("/api/v1/business-documents/capabilities") and response.request.method == "GET") as capabilities:
            live.browser(reader_page, "/sql-assistant", user=reader, token=reader_token)
        capability_response = capabilities.value
        assert capability_response.status == 200
        assert capability_response.json()["data"]["capabilities"]["create"] is False
        root = chat._root(reader_page)
        expect(root.get_by_role("button", name="Новое исследование", exact=True)).to_be_disabled()
        expect(root.get_by_role("button", name="Отправить", exact=True)).to_be_disabled()
        expect(root.locator("textarea:visible")).to_be_disabled()
        live.browser(reader_page, "/sql-assistant?session=" + chat_probe["id"], user=reader, token=reader_token)
        expect(chat._root(reader_page)).to_contain_text("Не удалось открыть исследование")
        expect(root.get_by_role("table", name="Данные выбранного результата", exact=True)).to_have_count(0)
        expect(root.get_by_role("button", name="Скачать результат", exact=True)).to_have_count(0)
        assert chat_probe["fqn"] not in root.inner_text()
        reader_page.screenshot(path=str(live.out / (chat_probe["proof_id"] + "-reader-denied.png")))
    finally:
        context.close()
    restored = chat._rows(page, live, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
    assert chat._identity(restored) == chat._identity(original)
    live.proof(chat_probe["proof_id"] + "-reader-tenant", {"reader_is_superuser": False, "denied_statuses": denied, "owner_result_unchanged": True})
    chat._complete(page, live, chat_probe, restored)
