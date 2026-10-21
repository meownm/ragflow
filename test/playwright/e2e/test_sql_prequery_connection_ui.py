"""Connection management after accepting the schema and before generating SQL."""

import re

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_sql_connection_ui import ConnectionBrowserStub, assert_probe_aborted_when_switching
from test.playwright.e2e.test_sql_workspace_ui import _open


@pytest.mark.parametrize("compact", [False, True], ids=["desktop", "mobile"])
def test_sql_source_can_be_checked_and_saved_before_query_exists(page, base_url, compact):
    stub, _ = _open(page, base_url, stub=ConnectionBrowserStub(initial=None, before_query=True), compact=compact)
    connection = page.get_by_role("region", name="Подключение проекта", exact=True)
    expect(connection).to_be_visible()
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Проверить подключение", exact=True).click()
    expect(connection).to_contain_text("Для проверки подключения выберите один источник.")
    source = page.get_by_role("combobox", name="Источник данных", exact=True)
    source.select_option("profile-2")
    page.get_by_role("button", name="Проверить подключение", exact=True).click()
    expect(connection).to_contain_text("Подключение доступно: QA Warehouse B")
    page.get_by_role("button", name="Сохранить источник", exact=True).click()
    expect(page.get_by_role("button", name="Сохранить источник", exact=True)).to_be_disabled()
    assert stub.projects[stub.primary]["execution_profile_id"] == "profile-2"
    page.reload(wait_until="commit")
    expect(page.get_by_role("combobox", name="Источник данных", exact=True)).to_have_value("profile-2", timeout=30000)
    page.get_by_role("button", name="Проверить подключение", exact=True).click()
    expect(connection).to_contain_text("Подключение доступно: QA Warehouse B")
    assert len(stub.health_checks) == 3 and len(stub.connection_commands) == 1
    assert not [path for _, path in stub.requests if path.endswith(("/preflight", "/compilations", "/runs"))]
    assert stub.projects[stub.primary]["artifact_ids"]["query"] is None
    stub.assert_sql_preserved()
    assert page._diag["page_errors"] == []


def test_sql_reader_cannot_probe_or_change_source_before_query(page, base_url):
    stub, _ = _open(page, base_url, stub=ConnectionBrowserStub(before_query=True, reader=True))
    expect(page.get_by_role("region", name="Подключение проекта", exact=True)).to_be_visible()
    for name in ("Проверить подключение", "Сохранить источник", "Выполнить запрос"):
        expect(page.get_by_role("button", name=name, exact=True)).to_have_count(0)
    expect(page.get_by_role("combobox", name="Источник данных", exact=True)).to_have_count(0)
    assert stub.health_checks == [] and stub.connection_commands == []
    stub.assert_sql_preserved()


def test_sql_probe_before_query_is_aborted_on_project_switch(page, base_url):
    stub = ConnectionBrowserStub(before_query=True)
    assert_probe_aborted_when_switching(page, base_url, stub)
    expect(page.get_by_text(re.compile("Подключение доступно:"))).to_have_count(0)
