"""Real browser registry interactions with deterministic API responses."""

from copy import deepcopy
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from business_documents.sql_query.revisions import query_blocks
from test.playwright.e2e.test_document_constructor_ui import _envelope, _fulfill_json, _install_session
from test.playwright.e2e.test_sql_connection_ui import ConnectionBrowserStub
from test.playwright.e2e.test_sql_workspace_ui import SqlWorkspaceBrowserStub, _open


class RegistryBrowserStub(ConnectionBrowserStub):
    def __init__(self):
        super().__init__()
        self.commands = []
        for project in self.projects.values():
            project.update(archived_at=None, copy_origin=None)
            project["capabilities"].update(manage=True, copy=True)

    def __call__(self, route):
        request = route.request
        target = urlparse(request.url)
        prefix = "/api/v1/business-documents/sql-query/projects"
        path = target.path
        if path == prefix and request.method == "GET":
            archived = parse_qs(target.query).get("archived", ["false"])[0] == "true"
            _fulfill_json(route, _envelope([item for item in self.projects.values() if bool(item.get("archived_at")) == archived]))
            return
        match = re.fullmatch(prefix + r"/([^/]+)(/copy)?", path)
        if match:
            project_id, copy = match.groups()
            if project_id not in self.projects:
                route.fulfill(status=404, content_type="application/json", body=json.dumps({"code": 404, "message": "Проект недоступен", "data": {"error_code": "SQL_AGENT_PROJECT_NOT_FOUND"}}))
                return
            if request.method in {"PATCH", "POST"}:
                body = request.post_data_json
                project = self.projects[project_id]
                assert body["expected_state_version"] == project["state_version"]
                self.commands.append((project_id, deepcopy(body), bool(copy)))
                if copy:
                    assert set(body) == {"schema_version", "expected_state_version", "idempotency_key"}
                    copied = deepcopy(project)
                    copied.update(id="copied-project", title="Копия запроса", state_version=1, execution_profile_id=None, archived_at=None, latest_run=None, runs=[], compilation=None, next_agent="REQUIREMENTS", stage="REQUIREMENTS", artifact_ids={"requirements": None, "schema": None, "query": None}, artifacts={"requirements": None, "schema": None, "query": None})
                    self.projects[copied["id"]] = copied
                    self.views[copied["id"]] = deepcopy(self.views[project_id])
                    _fulfill_json(route, _envelope(copied))
                else:
                    assert set(body) <= {"schema_version", "expected_state_version", "idempotency_key", "title", "archived"}
                    if "title" in body:
                        project["title"] = body["title"]
                    if "archived" in body:
                        project["archived_at"] = 1792526400000 if body["archived"] else None
                        project["capabilities"].update(edit=not body["archived"], execute=not body["archived"])
                    project["state_version"] += 1
                    _fulfill_json(route, _envelope(project))
                return
        return super().__call__(route)


def _no_runs(stub):
    assert not any(method == "POST" and path.endswith("/runs") for method, path in stub.requests)


def test_registry_search_rename_reload_preserves_sql(page, base_url):
    stub = RegistryBrowserStub()
    project_id = stub.sql_agent_project["id"]
    original_artifacts = deepcopy(stub.sql_agent_project["artifacts"])
    _open(page, base_url, stub=stub)
    search = page.get_by_role("textbox", name="Поиск проектов", exact=True)
    search.fill("no-matches")
    expect(page.get_by_role("heading", name=stub.sql_agent_project["title"], exact=True)).to_be_visible()
    expect(page.get_by_text("0 в списке", exact=True)).to_be_visible()
    search.fill("")
    page.get_by_role("button", name="Переименовать", exact=True).click()
    page.get_by_role("textbox", name="Название проекта", exact=True).fill("Продажи 50%_дня")
    page.get_by_role("button", name="Сохранить название", exact=True).click()
    expect(page.get_by_role("heading", name="Продажи 50%_дня", exact=True)).to_be_visible()
    expect(page).to_have_url(re.compile(r"[?&]project=" + re.escape(project_id)))
    page.reload()
    expect(page.get_by_role("heading", name="Продажи 50%_дня", exact=True)).to_be_visible()
    assert stub.projects[project_id]["artifacts"] == original_artifacts
    assert len(stub.commands) == 1 and not stub.connection_commands
    _no_runs(stub)


def test_registry_archive_read_and_restore(page, base_url):
    stub = RegistryBrowserStub()
    _open(page, base_url, stub=stub)
    page.get_by_role("button", name="В архив", exact=True).click()
    expect(page.get_by_text("Проект в архиве. Для изменения и выполнения восстановите его.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Переименовать", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Проверить подключение", exact=True)).to_have_count(0)
    page.get_by_role("checkbox", name="Показать архив", exact=True).check()
    expect(page.get_by_text("1 в архиве", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_text("Проект в архиве. Для изменения и выполнения восстановите его.", exact=True)).to_be_visible()
    page.get_by_role("button", name="Восстановить из архива", exact=True).click()
    expect(page.get_by_role("button", name="Переименовать", exact=True)).to_be_visible()
    assert [item[1]["archived"] for item in stub.commands] == [True, False]
    _no_runs(stub)


def test_registry_copy_navigates_without_running_or_changing_source(page, base_url):
    stub = RegistryBrowserStub()
    original = deepcopy(stub.sql_agent_project)
    _open(page, base_url, stub=stub)
    page.get_by_role("button", name="Создать копию", exact=True).click()
    expect(page.get_by_role("heading", name="Копия запроса", exact=True)).to_be_visible()
    expect(page).to_have_url(re.compile(r"[?&]project=copied-project"))
    assert stub.projects[original["id"]] == original
    assert len(stub.commands) == 1 and stub.commands[0][2]
    assert not stub.connection_commands
    _no_runs(stub)


def test_registry_denied_direct_link_does_not_fall_back_to_another_project(page, base_url):
    stub = RegistryBrowserStub()
    _open(page, base_url, stub=stub)
    page.goto(base_url.rstrip("/") + "/sql-projects?project=foreign-project")
    expect(page.get_by_role("alert")).to_contain_text("Проект недоступен")
    expect(page.get_by_role("heading", name=stub.sql_agent_project["title"], exact=True)).to_have_count(0)
    assert not stub.commands
    _no_runs(stub)


@pytest.mark.parametrize("ready", [False, True], ids=["without-result", "ready-result"])
def test_sql_reader_can_view_query_history_er_and_result_without_mutation(page, base_url, ready):
    stub = ConnectionBrowserStub(reader=True, ready=ready)
    current = deepcopy(stub.sql_agent_project)
    historical_sql = "SELECT o.id AS order_id FROM public.orders AS o LIMIT :row_limit"
    stub.history = [{"id": "old-query", "revision": 1, "sql": historical_sql, "blocks": query_blocks(historical_sql)}]
    stub.sql_agent_project["capabilities"].update(python_agent=True, result_agent=ready)
    _, graph = _open(page, base_url, stub=stub)
    workspace = page.get_by_test_id("sql-revision-workspace")
    expect(workspace.get_by_label("Проверенный SQL")).to_have_value(stub.sql)
    workspace.get_by_role("button", name="Запрос · версия 1", exact=True).click()
    expect(workspace.get_by_label("Проверенный SQL")).to_have_value(historical_sql)
    expect(graph).to_contain_text("Версия запроса 1")
    workspace.get_by_role("button", name="Запрос · версия 2", exact=True).click()
    expect(workspace.get_by_label("Проверенный SQL")).to_have_value(stub.sql)
    expect(graph.locator('[data-entity-id="orders"]')).to_have_attribute("data-membership", "QUERY")
    expect(graph.locator('[data-entity-id="customers"]')).to_have_attribute("data-membership", "RELATED")
    for name in ("Изменить задачу", "Переименовать", "В архив", "Создать копию", "Сохранить источник",
                 "Проверить подключение", "Редактировать финальный SQL", "Завершить", "Удалить данные этого запуска",
                 "Сформировать вывод", "Преобразовать в Python", "Дополнить данные", "Сохранить документ без выполнения"):
        expect(page.get_by_role("button", name=name, exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Открыть сохранённый проверенный SQL", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_have_count(0)
    if ready:
        expect(page.get_by_test_id("sql-result-table")).to_contain_text("1250.00")
        expect(page.get_by_test_id("sql-agent-complete")).to_contain_text("Original warehouse · версия 3")
    page.reload()
    expect(page.get_by_label("Проверенный SQL")).to_have_value(stub.sql)
    if ready:
        expect(page.get_by_test_id("sql-result-table")).to_contain_text("1250.00")
    assert stub.sql_agent_project["artifact_ids"] == current["artifact_ids"]
    assert stub.sql_agent_project.get("latest_run") == current.get("latest_run")
    assert stub.connection_commands == [] and stub.health_checks == []
    stub.assert_sql_preserved()
    assert page._diag["page_errors"] == []
    assert page._diag["console_errors"] == []


def test_sql_reader_cannot_decide_a_pending_requirements_proposal(page, base_url):
    stub = ConnectionBrowserStub(reader=True)
    project = stub.sql_agent_project
    project.update(stage="REQUIREMENTS", operation_state="REVIEW", compilation=None,
        artifact_ids={"requirements": None, "schema": None, "query": None}, artifacts={"requirements": None, "schema": None, "query": None},
        pending_proposal={"id": "pending-requirements", "kind": "REQUIREMENTS", "status": "PENDING", "source_state_version": 1,
            "payload": {"agent_result": {"proposal": {"requirements": [{"id": "output", "kind": "output", "statement": "Вывести сумму заказов."}], "questions": []}}}})
    before = deepcopy(project)
    _install_session(page)
    page.route("**/api/v1/**", stub)
    page.goto(base_url.rstrip("/") + "/sql-projects", wait_until="commit")
    review = page.get_by_test_id("sql-agent-requirements-review")
    expect(review).to_be_visible(timeout=30000)
    expect(review).to_contain_text("Вывести сумму заказов.")
    expect(review.get_by_role("button", name="Подтвердить требования", exact=True)).to_be_disabled()
    expect(review.get_by_role("button", name="Отклонить и повторить", exact=True)).to_be_disabled()
    assert project == before
    assert stub.connection_commands == [] and stub.health_checks == []
    _no_runs(stub)
    assert not any(method in {"POST", "PUT", "PATCH", "DELETE"} and not path.endswith("/view") for method, path in stub.requests)
    assert page._diag["page_errors"] == []


class LateProjectBrowserStub(ConnectionBrowserStub):
    def __init__(self):
        super().__init__()
        different = SqlWorkspaceBrowserStub(query="SELECT o.id AS order_id, c.region AS region FROM public.orders o LEFT JOIN public.customers c ON c.id=o.customer_id ORDER BY o.id LIMIT :row_limit")
        other = different.sql_agent_project
        other.update(id="other-project", title="Второй проект", execution_profile_id="profile-2")
        self.projects["other-project"] = other
        self.pending_detail = []

    def __call__(self, route):
        path = urlparse(route.request.url).path.rstrip("/")
        if path.endswith("/projects/other-project") and route.request.method == "GET":
            self.detail_reads.append("other-project")
            self.pending_detail.append(route)
            return
        return super().__call__(route)


def test_registry_late_project_response_cannot_replace_the_current_workspace(page, base_url):
    stub = LateProjectBrowserStub()
    _, graph = _open(page, base_url, stub=stub)
    primary = stub.sql_agent_project
    expect(page.get_by_label("Проверенный SQL")).to_have_value(stub.sql)
    page.get_by_role("button", name=re.compile("Второй проект")).click()
    for _ in range(100):
        if stub.pending_detail:
            break
        page.wait_for_timeout(20)
    assert len(stub.pending_detail) == 1
    page.get_by_role("button", name=re.compile(re.escape(primary["title"]))).click()
    expect(page.get_by_role("button", name="Обновить", exact=True)).to_be_enabled()
    expect(page.get_by_role("heading", name=primary["title"], exact=True)).to_be_visible()
    pending = stub.pending_detail.pop()
    with page.expect_response(lambda response: response.url.endswith("/projects/other-project")) as received:
        _fulfill_json(pending, _envelope(stub.projects["other-project"]))
    received.value.finished()
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.get_by_role("heading", name=primary["title"], exact=True)).to_be_visible()
    expect(page.get_by_label("Проверенный SQL")).to_have_value(stub.sql)
    expect(page.get_by_role("combobox", name="Источник данных", exact=True)).to_have_value("profile-1")
    expect(graph.locator('[data-entity-id="customers"]')).to_have_attribute("data-membership", "RELATED")
    expect(page).to_have_url(re.compile(r"[?&]project=" + re.escape(primary["id"])))
    assert stub.detail_reads.count(stub.primary) >= 2
    assert stub.connection_commands == [] and stub.health_checks == []
    stub.assert_sql_preserved()
    assert page._diag["page_errors"] == []
    assert page._diag["console_errors"] == []
