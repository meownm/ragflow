"""Real browser registry interactions with deterministic API responses."""

from copy import deepcopy
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from business_documents.sql_query.revisions import query_blocks
from test.playwright.e2e.test_document_constructor_ui import DocumentConstructorStub, _envelope, _fulfill_json, _install_session
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
                    copied.update(
                        id="copied-project",
                        title="Копия запроса",
                        state_version=1,
                        execution_profile_id=None,
                        archived_at=None,
                        latest_run=None,
                        runs=[],
                        compilation=None,
                        next_agent="REQUIREMENTS",
                        stage="REQUIREMENTS",
                        artifact_ids={"requirements": None, "schema": None, "query": None},
                        artifacts={"requirements": None, "schema": None, "query": None},
                    )
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
    for name in (
        "Изменить задачу",
        "Переименовать",
        "В архив",
        "Создать копию",
        "Сохранить источник",
        "Проверить подключение",
        "Редактировать финальный SQL",
        "Завершить",
        "Удалить данные этого запуска",
        "Сформировать вывод",
        "Преобразовать в Python",
        "Дополнить данные",
        "Сохранить документ без выполнения",
    ):
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
    project.update(
        stage="REQUIREMENTS",
        operation_state="REVIEW",
        compilation=None,
        artifact_ids={"requirements": None, "schema": None, "query": None},
        artifacts={"requirements": None, "schema": None, "query": None},
        pending_proposal={
            "id": "pending-requirements",
            "kind": "REQUIREMENTS",
            "status": "PENDING",
            "source_state_version": 1,
            "payload": {"agent_result": {"proposal": {"requirements": [{"id": "output", "kind": "output", "statement": "Вывести сумму заказов."}], "questions": []}}},
        },
    )
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


class PreparationBrowserStub(DocumentConstructorStub):
    def __init__(self, *, reader=False, stale=False):
        super().__init__()
        project = self._create_sql_agent_project(
            {
                "title": "QA · проверка поиска без имён таблиц",
                "source_request": "По поставщикам: число уникальных источников и снимков. За весь период, до 10 строк, только источники с лицензионной проверкой.",
            }
        )
        project.update(stage="SCHEMA", next_agent="SCHEMA")
        project["artifacts"]["requirements"] = {"requirements": [{"statement": project["source_request"], "status": "ACCEPTED"}]}
        project["artifact_ids"]["requirements"] = "requirements-v1"
        self.stale = stale
        self._run_sql_agent({"kind": "SCHEMA"})
        if reader:
            project["capabilities"].update(edit=False, execute=False, manage=False, copy=False)

    def _schema_resolution_answer(self):
        answer = super()._schema_resolution_answer()
        first = answer["resolutions"][0]
        answer["resolutions"] = []
        for term in ("Поставщики и источники", "Полученные снимки", "Лицензионные проверки"):
            item = deepcopy(first)
            item.update(term=term)
            item["interpretation"].update(recommended_entity_id=None, reason="Каталог предложил таблицы по описанию. Проверьте, содержат ли они нужные данные.")
            answer["resolutions"].append(item)
        return answer

    def _schema_entity_details_answer(self):
        answer = super()._schema_entity_details_answer()
        if self.stale:
            answer["entities"][0]["freshness"]["stale"] = True
        return answer


def _open_preparation(page, base_url, stub, width):
    _install_session(page)
    page.route("**/api/v1/**", stub)
    page.set_viewport_size({"width": width, "height": 1080})
    page.goto(base_url.rstrip("/") + "/sql-projects", wait_until="commit")
    review = page.get_by_test_id("sql-agent-schema-review")
    expect(review).to_be_visible(timeout=120000)
    return review


@pytest.mark.parametrize("width", [1440, 949])
@pytest.mark.parametrize("reader", [False, True], ids=["editor", "reader"])
def test_sql_preparation_responsive_layout_and_schema_decision(page, base_url, snap, width, reader):
    stub = PreparationBrowserStub(reader=reader)
    review = _open_preparation(page, base_url, stub, width)
    expect(review.get_by_text("Подтверждено 0 из 3", exact=True)).to_be_visible()
    accept = review.get_by_role("button", name="Подтвердить схему", exact=True)
    expect(accept).to_be_disabled()
    expect(page.get_by_test_id("sql-revision-workspace")).not_to_be_visible()
    needs = review.get_by_role("group", name="Нужные данные").get_by_role("button")
    expect(needs).to_have_count(3)
    boxes = [needs.nth(index).bounding_box() for index in range(3)]
    if width == 1440:
        assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"]
        assert abs(boxes[0]["x"] - boxes[2]["x"]) < 1
        expect(page.get_by_role("textbox", name="Поиск проектов", exact=True)).to_be_visible()
    else:
        assert abs(boxes[0]["y"] - boxes[2]["y"]) < 1
        assert boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"]
        switch = page.get_by_role("button", name="Запросы · выбрать проект", exact=True)
        switch.click()
        expect(page.get_by_role("textbox", name="Поиск проектов", exact=True)).to_be_visible()
        page.get_by_role("button", name="Закрыть список запросов", exact=True).click()
    assert accept.bounding_box()["y"] + accept.bounding_box()["height"] <= 1080
    candidate_box = review.locator(".sql-schema-candidate").bounding_box()
    footer_box = review.locator(".sql-schema-footer").bounding_box()
    assert candidate_box["y"] + candidate_box["height"] <= footer_box["y"]
    assert footer_box["y"] + footer_box["height"] <= 1080
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    snap(f"sql-preparation-{width}-{'reader' if reader else 'editor'}")
    page.get_by_text("Вопрос целиком", exact=True).click()
    expect(page.locator(".sql-question-summary[open]")).to_contain_text(stub.sql_agent_project["source_request"])
    page.get_by_text("Вопрос целиком", exact=True).click()

    for index in range(3):
        needs.nth(index).click()
        expect(needs.nth(index)).to_have_attribute("aria-pressed", "true")
        review.get_by_role("button", name="Просмотреть таблицу", exact=True).click()
        expect(review.get_by_role("checkbox", name=re.compile(r"^order_id "))).to_be_visible()
        if reader:
            expect(review.get_by_role("checkbox").first).to_be_disabled()
        else:
            for column in ("order_id", "status_id", "paid_amount_rub"):
                review.get_by_role("checkbox", name=re.compile(r"^" + column + r" ")).check()
    if reader:
        expect(accept).to_be_disabled()
        expect(review.get_by_role("button", name="Повторить поиск", exact=True)).to_be_disabled()
        assert stub.sql_agent_project["pending_proposal"] is not None
        assert stub.sql_agent_project["artifact_ids"]["schema"] is None
    else:
        expect(review.get_by_text("Подтверждено 3 из 3", exact=True)).to_be_visible()
        expect(accept).to_be_enabled()
        snap(f"sql-preparation-{width}-fields")
        accept.click()
        expect(page.get_by_test_id("sql-agent-query-review")).to_be_visible()
        assert stub.sql_agent_project["artifacts"]["schema"]["schema_snapshot"]["status"] == "READY"
    assert stub.sql_project_commands == []  # No compilation or execution during preparation.
    assert page._diag["page_errors"] == []
    assert page._diag["console_errors"] == []


@pytest.mark.parametrize("width", [1440, 949])
def test_sql_preparation_stale_catalog_blocks_confirmation_and_retry_restarts_search(page, base_url, width):
    stub = PreparationBrowserStub(stale=True)
    review = _open_preparation(page, base_url, stub, width)
    for index in range(3):
        review.get_by_role("group", name="Нужные данные").get_by_role("button").nth(index).click()
        review.get_by_role("button", name="Просмотреть таблицу", exact=True).click()
        review.get_by_role("checkbox").first.check()
    expect(review).to_contain_text("Для подтверждения обновите устаревшие данные каталога.")
    expect(review.get_by_role("button", name="Подтвердить схему", exact=True)).to_be_disabled()
    version = stub.sql_agent_project["state_version"]
    review.get_by_role("button", name="Повторить поиск", exact=True).click()
    expect(review.get_by_text("Подтверждено 0 из 3", exact=True)).to_be_visible()
    expect(review.get_by_role("checkbox")).to_have_count(0)
    expect(review.get_by_role("button", name="Повторить поиск", exact=True)).to_be_enabled()
    assert stub.sql_agent_project["state_version"] == version + 2  # Rejection then a new schema job.
    assert stub.sql_agent_project["operation_state"] == "REVIEW"
    assert stub.sql_agent_project["artifacts"]["schema"] is None
    assert stub.sql_project_commands == []


@pytest.mark.parametrize("width", [1440, 949])
def test_sql_preparation_table_lookup_failure_keeps_confirmation_blocked(page, base_url, width):
    stub = PreparationBrowserStub()
    review = _open_preparation(page, base_url, stub, width)
    review.get_by_role("button", name="Другие варианты (1)", exact=True).click()
    review.get_by_role("button", name=re.compile(r"^order_event_fact ")).click()
    expect(review.get_by_role("alert")).to_contain_text("Схема выбранной таблицы недоступна.")
    expect(review.get_by_role("button", name="Подтвердить схему", exact=True)).to_be_disabled()
    review.get_by_role("button", name="Другие варианты (1)", exact=True).click()
    review.get_by_role("button", name=re.compile(r"^order_fact ")).click()
    expect(review.get_by_role("checkbox").first).to_be_visible()
    expect(review.get_by_role("alert")).to_have_count(0)
    assert stub.sql_agent_project["artifacts"]["schema"] is None
    assert stub.sql_project_commands == []


@pytest.mark.parametrize("width", [1440, 949])
def test_sql_preparation_long_catalog_text_and_fields_remain_readable(page, base_url, snap, width):
    class LongCatalogStub(PreparationBrowserStub):
        def _schema_entity_details_answer(self):
            answer = super()._schema_entity_details_answer()
            entity = answer["entities"][0]["entity"]
            entity["display_name"] = "Подробная таблица поставщиков, источников и результатов лицензионных проверок"
            entity["description"] = "Описание источника с дополнительными условиями и пояснением связей между наборами данных. " * 5
            entity["column_details"][0]["display_name"] = "ИдентификаторИсточникаБезПробелов" * 6
            entity["column_details"][0]["description"] = "Полный идентификатор поставщика для группировки и связи со снимками данных. " * 3
            return answer

    stub = LongCatalogStub()
    review = _open_preparation(page, base_url, stub, width)
    review.get_by_role("button", name="Просмотреть таблицу", exact=True).click()
    field = review.get_by_role("checkbox").first
    field.check()
    expect(field).to_be_checked()
    assert review.evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(review.get_by_role("button", name="Подтвердить схему", exact=True)).to_be_disabled()
    snap(f"sql-preparation-{width}-long-fields")
    assert stub.sql_agent_project["artifacts"]["schema"] is None
    assert page._diag["page_errors"] == []


@pytest.mark.parametrize("width", [1440, 949])
def test_sql_preparation_keyboard_order_follows_primary_step(page, base_url, width):
    review = _open_preparation(page, base_url, PreparationBrowserStub(), width)
    page.locator(".sql-question-summary > summary").focus()
    page.keyboard.press("Tab")
    expect(review.locator(".sql-schema-need").first).to_be_focused()
    context = page.locator(".sql-project-context")
    assert review.evaluate("(el, context) => !!(el.compareDocumentPosition(context) & Node.DOCUMENT_POSITION_FOLLOWING)", context.element_handle())
    requirements = context.get_by_role("heading", name="Подтверждённые требования", exact=True)
    context.locator("summary").click()
    expect(requirements).to_be_visible()
    context.locator("summary").click()
    expect(requirements).not_to_be_visible()


@pytest.mark.parametrize("width", [1440, 949])
def test_sql_preparation_expanded_question_has_one_full_copy(page, base_url, snap, width):
    stub = PreparationBrowserStub()
    stub.sql_agent_project["source_request"] = ("Поставщики и результаты проверок за весь период. " * 8) + "\n" + "ДлинноеУточнениеБезПробелов" * 8
    _open_preparation(page, base_url, stub, width)
    question = page.locator(".sql-question-summary")
    question.locator("summary").click()
    copies = question.locator("span, p").evaluate_all(
        "(nodes, value) => nodes.filter(n => n.textContent.trim() === value && n.getClientRects().length).length",
        stub.sql_agent_project["source_request"],
    )
    assert copies == 1
    assert question.evaluate("el => el.scrollWidth <= el.clientWidth")
    snap(f"sql-preparation-{width}-expanded-question")
    question.locator("summary").click()
    expect(question).not_to_have_attribute("open", "")


class AlternativePreparationStub(PreparationBrowserStub):
    reason = "Таблица order_fact содержит оплаченную сумму заказов."

    def _schema_resolution_answer(self):
        answer = super()._schema_resolution_answer()
        for item in answer["resolutions"]:
            item["interpretation"].update(recommended_entity_id="orders", reason=self.reason)
        return answer

    def __call__(self, route):
        if urlparse(route.request.url).path.endswith("/sql-query/schema/entities"):
            entity_id = route.request.post_data_json["entity_ids"][0]
            entity = deepcopy(next(item for item in self._catalog_answer()["entities"] if item["id"] == entity_id))
            entity.update(schema_loaded=True, schema_fingerprint="sha256:" + "a" * 64)
            answer = self._schema_entity_details_answer()
            answer["entities"][0].update(entity_id=entity_id, entity=entity)
            _fulfill_json(route, _envelope(answer))
            return
        return super().__call__(route)


@pytest.mark.parametrize("width", [1440, 949])
def test_sql_preparation_alternative_table_uses_its_own_explanation(page, base_url, snap, width):
    stub = AlternativePreparationStub()
    review = _open_preparation(page, base_url, stub, width)
    inspector = review.get_by_role("region", name=re.compile("Таблица для:"))
    expect(inspector).to_contain_text(stub.reason)
    review.get_by_role("button", name="Другие варианты (1)", exact=True).click()
    review.get_by_role("button", name=re.compile(r"^order_event_fact ")).click()
    expect(inspector.get_by_role("heading", name="order_event_fact", exact=True)).to_be_visible()
    expect(inspector.get_by_role("checkbox").first).to_be_visible()
    expect(inspector).not_to_contain_text(stub.reason)
    alternative = next(item for item in stub._catalog_answer()["entities"] if item["id"] != "orders")
    expect(inspector).to_contain_text(alternative["description"])
    snap(f"sql-preparation-{width}-alternative")
    review.get_by_role("button", name="Другие варианты (1)", exact=True).click()
    review.get_by_role("button", name=re.compile(r"^order_fact ")).click()
    expect(inspector).to_contain_text(stub.reason)
    assert page._diag["page_errors"] == []


class TwoPreparationProjectsStub(PreparationBrowserStub):
    def __init__(self):
        super().__init__()
        second = self.sql_agent_project
        second["title"] = "Проект B"
        first = deepcopy(second)
        first.update(id="project-a", title="Проект A", operation_state="IDLE", next_agent="SCHEMA", pending_proposal=None)
        self.projects = {first["id"]: first, second["id"]: second}
        self.sql_agent_project = first

    def __call__(self, route):
        path = urlparse(route.request.url).path
        prefix = "/api/v1/business-documents/sql-query/projects"
        if path == prefix and route.request.method == "GET":
            _fulfill_json(route, _envelope(list(self.projects.values())))
            return
        if path.startswith(prefix + "/"):
            project_id = path[len(prefix) + 1 :].split("/")[0]
            if project_id in self.projects:
                self.sql_agent_project = self.projects[project_id]
        return super().__call__(route)


@pytest.mark.parametrize("width", [1440, 949])
@pytest.mark.parametrize("navigation", ["registry", "back", "forward"])
def test_sql_preparation_search_terms_belong_to_current_project(page, base_url, width, navigation):
    stub = TwoPreparationProjectsStub()
    _install_session(page)
    page.route("**/api/v1/**", stub)
    page.set_viewport_size({"width": width, "height": 1080})
    page.goto(base_url.rstrip("/") + "/sql-projects", wait_until="commit")
    expect(page.get_by_test_id("sql-agent-run-schema")).to_be_visible(timeout=120000)

    def select(title):
        if width == 949:
            page.get_by_role("button", name="Запросы · выбрать проект", exact=True).click()
        page.get_by_role("button", name=re.compile("^" + title)).click()

    if navigation != "registry":
        select("Проект B")
        expect(page.get_by_test_id("sql-agent-schema-review")).to_be_visible()
        select("Проект A")
        expect(page.get_by_test_id("sql-agent-run-schema")).to_be_visible()
    page.get_by_text("Уточнить поиск (необязательно)", exact=True).click()
    page.get_by_role("textbox", name=re.compile("Темы для уточнения поиска")).fill("Тема только проекта A")
    if navigation == "registry":
        select("Проект B")
    else:
        page.go_back(wait_until="commit")
    review = page.get_by_test_id("sql-agent-schema-review")
    expect(review).to_be_visible()
    expect(page).to_have_url(re.compile("project=sql-agent-project-1"))
    if navigation == "forward":
        page.go_forward(wait_until="commit")
        expect(page.get_by_test_id("sql-agent-run-schema")).to_be_visible()
        page.get_by_text("Уточнить поиск (необязательно)", exact=True).click()
        expect(page.get_by_role("textbox", name=re.compile("Темы для уточнения поиска"))).to_have_value("")
        target = "project-a"
        action = page.get_by_test_id("sql-agent-run-schema")
    else:
        target = "sql-agent-project-1"
        action = review.get_by_role("button", name="Повторить поиск", exact=True)
    with page.expect_request(f"**/projects/{target}/agent-jobs") as request:
        action.click()
    assert request.value.post_data_json["payload"]["terms"] == []
    assert page._diag["page_errors"] == []


def test_sql_preparation_compact_registry_focus_stays_visible(page, base_url, snap):
    review = _open_preparation(page, base_url, PreparationBrowserStub(), 949)
    switch = page.locator(".sql-project-switcher")
    registry = page.locator("#sql-project-registry")
    switch.click()
    expect(registry.get_by_role("textbox", name="Поиск проектов")).to_be_focused()
    snap("sql-preparation-949-registry")
    page.keyboard.press("Escape")
    expect(registry).not_to_be_visible()
    expect(switch).to_be_focused()
    switch.click()
    registry.get_by_role("button").last.focus()
    for _ in range(8):
        page.keyboard.press("Tab")
        if review.locator(".sql-schema-need").first.evaluate("el => el === document.activeElement"):
            break
    expect(review.locator(".sql-schema-need").first).to_be_focused()
    expect(registry).not_to_be_visible()
    assert review.locator(".sql-schema-need").first.evaluate("el => {const r=el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}")
    switch.click()
    registry.get_by_role("button").last.click()
    expect(registry).not_to_be_visible()
    expect(switch).to_be_focused()
