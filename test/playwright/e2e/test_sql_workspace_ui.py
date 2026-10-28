"""Browser evidence for actual graph rendering; API/model execution is stubbed."""

import json
from copy import deepcopy
import os
import hashlib
import platform
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from business_documents.sql_query.query_graph import project_query_graph, related_catalog, relationship_paths
from business_documents.sql_query.revisions import query_blocks
from business_documents.sql_query.view_state import validate_view
from business_documents.sql_query.project_compilation import build_project_compile_command
from business_documents.sql_query.query_specification import compile_query_payload
from test.playwright.e2e.test_document_constructor_ui import DocumentConstructorStub, _envelope, _fulfill_json, _install_session


SQL = "SELECT o.id AS order_id, o.amount AS amount FROM public.orders o WHERE o.amount >= :minimum ORDER BY o.id LIMIT :row_limit"


def _tables():
    def table(name, columns, links=()):
        fqn = f"warehouse.analytics.public.{name}"
        return {
            "id": name,
            "type": "table",
            "name": name,
            "technical_name": name,
            "display_name": {"orders": "Заказы", "customers": "Клиенты", "order_items": "Позиции заказа", "products": "Товары", "categories": "Категории"}[name],
            "fqn": fqn,
            "description": name,
            "version": 1,
            "schema": "public",
            "database": "analytics",
            "service": "warehouse",
            "owners": [],
            "domains": ["Sales"],
            "tags": [],
            "column_count": len(columns),
            "described_column_count": 0,
            "url": "",
            "schema_loaded": True,
            "schema_fingerprint": f"sha256:{name}",
            "column_details": [
                {
                    "id": f"{fqn}.{column}",
                    "fqn": f"{fqn}.{column}",
                    "name": column,
                    "data_type": "TEXT" if column in {"name", "region"} else "NUMERIC" if column == "amount" else "BIGINT",
                    "constraint": "PRIMARY_KEY" if column == "id" else "",
                    "description": "",
                    "glossary_terms": [],
                }
                for column in columns
            ],
            "table_constraints": [{"constraint_type": "FOREIGN_KEY", "columns": [source], "referred_columns": [f"warehouse.analytics.public.{target}.id"]} for source, target in links],
        }

    return [
        table("orders", ["id", "customer_id", "payer_id", "amount"], [("customer_id", "customers"), ("payer_id", "customers")]),
        table("customers", ["id", "region"]),
        table("order_items", ["id", "order_id", "product_id"], [("order_id", "orders"), ("product_id", "products")]),
        table("products", ["id", "category_id"], [("category_id", "categories")]),
        table("categories", ["id", "name"]),
    ]


class SqlWorkspaceBrowserStub(DocumentConstructorStub):
    def __init__(self, query=SQL, *, accepted_ids=None, tables=None):
        super().__init__()
        self.tables = _tables() if tables is None else tables
        self.graph_requests = []
        self.requests = []
        self.graph_offline = False
        self.graph_metadata = {}
        self.view_failure = None
        self.history = []
        self.view = {"version": 0, "view": None}
        project = self._create_sql_agent_project({"title": "ER: заказы", "source_request": "Показать выбранные поля заказов", "locale": "ru"})
        self.sql = query
        accepted_ids = accepted_ids or (["orders", "customers"] if "JOIN public.customers" in query else ["orders"])
        snapshot_tables = [
            {
                **{key: value for key, value in table.items() if key not in {"type", "described_column_count", "column_details"}},
                "columns_truncated": False,
                "loaded_column_count": table["column_count"],
                "columns": [{**column, "selected": True} for column in table["column_details"]],
            }
            for table in self.tables
            if table["id"] in accepted_ids
        ]
        requirements = {"requirements": [{"statement": "Показать выбранные поля заказов"}]}
        schema = {
            "accepted_schema": [{"entity_id": table["id"], "version": table["version"], "schema_fingerprint": table["schema_fingerprint"]} for table in snapshot_tables],
            "schema_snapshot": {
                "format": "ragflow-sql-schema-snapshot",
                "schema_version": "1",
                "status": "READY",
                "original_requirements": "- Показать выбранные поля заказов",
                "source": {"type": "openmetadata", "stale": False},
                "requirements": [{"selected_table": table} for table in snapshot_tables],
            },
        }
        query_artifact = {
            "mode": "manual",
            "sql": query,
            "confirmed_alignment": True,
            "parameters": [{"name": name, "type": "integer", "value": value} for name, value in (("minimum", 0), ("row_limit", 100)) if f":{name}" in query],
        }
        compilation = compile_query_payload(build_project_compile_command(requirements, schema, query_artifact))
        assert compilation["status"] == "READY", compilation
        project.update(
            stage="COMPLETE",
            next_agent=None,
            next_action="EXECUTE",
            artifact_ids={"requirements": "requirements-1", "schema": "schema-1", "query": "query-1"},
            artifacts={"requirements": requirements, "schema": schema, "query": query_artifact},
            compilation={"id": "compilation-1", "result": compilation},
        )
        self.query_before = {key: deepcopy(project.get(key)) for key in ("state_version", "artifact_ids", "artifacts", "compilation", "latest_run")}

    def __call__(self, route):
        request = route.request
        path = urlparse(request.url).path.rstrip("/")
        self.requests.append((request.method, path))
        if path.endswith("/sql-query/schema/graph"):
            payload = json.loads(request.post_data or "{}")
            self.graph_requests.append(payload)
            if self.graph_offline:
                route.fulfill(
                    status=503, content_type="application/json", body=json.dumps({"code": 503, "message": "Каталог временно недоступен", "data": {"error_code": "SQL_SCHEMA_CATALOG_UNAVAILABLE"}})
                )
                return
            neighborhood = related_catalog(self.tables, payload["entity_ids"], depth=payload["depth"])
            graph = project_query_graph(payload["sql"], neighborhood["tables"])
            roots = list({node["entity_id"] for node in graph["nodes"] if node["membership"] == "QUERY" and node["entity_id"]})
            _fulfill_json(
                route,
                _envelope(
                    {
                        **neighborhood,
                        **graph,
                        **relationship_paths(neighborhood["relationships"], roots, max_hops=payload["depth"]),
                        "freshness": {"stale": False, "threshold_hours": 24},
                        **self.graph_metadata,
                    }
                ),
            )
            return
        if re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+/view", path):
            if self.view_failure and request.method == self.view_failure[0]:
                status = self.view_failure[1]
                route.fulfill(status=status, content_type="application/json", body=json.dumps({"code": status, "message": "Конфликт вида" if status == 409 else "Вид недоступен", "data": {}}))
                return
            if request.method == "PUT":
                payload = json.loads(request.post_data or "{}")
                assert payload["expected_version"] == self.view["version"]
                self.view = {"version": self.view["version"] + 1, "view": validate_view(payload["view"])}
            _fulfill_json(route, _envelope(self.view))
            return
        if path.endswith("/workspace"):
            _fulfill_json(
                route,
                _envelope(
                    {
                        "project_id": self.sql_agent_project["id"],
                        "state_version": 1,
                        "query_artifact_id": "query-1",
                        "document_artifact_id": None,
                        "sql": self.sql,
                        "blocks": query_blocks(self.sql),
                        "query_revisions": [{"id": "query-1", "revision": 2 if self.history else 1, "sql": self.sql, "blocks": query_blocks(self.sql)}, *self.history],
                        "draft": None,
                        "threads": [],
                        "history": [],
                    }
                ),
            )
            return
        return super().__call__(route)


def _open_sql_panels(page):
    for label in ("Сущности и связи", "Доработка и обсуждения", "Источник для следующего запуска"):
        summary = page.locator("summary").filter(has_text=re.compile(f"^{label}$"))
        if summary.count() and summary.locator("..").get_attribute("open") is None:
            summary.click()


def _open(page, base_url, *, stub=None, compact=False):
    stub = stub or SqlWorkspaceBrowserStub()
    _install_session(page)
    page.route("**/api/v1/**", stub)
    page.set_viewport_size({"width": 390 if compact else 1440, "height": 844 if compact else 1000})
    page.goto(f"{base_url.rstrip('/')}/sql-projects", wait_until="commit")
    expect(page.get_by_test_id("sql-agent-workbench")).to_be_visible(timeout=120000)
    expect(page.locator("summary").filter(has_text=re.compile("^Сущности и связи$"))).to_be_visible(timeout=120000)
    _open_sql_panels(page)
    graph = page.get_by_test_id("sql-schema-graph")
    expect(graph).to_be_visible(timeout=120000)
    expect(graph.locator('[data-entity-id="orders"]')).to_have_count(1, timeout=30000)
    return stub, graph


def _no_query_mutations(stub):
    unexpected = [
        (method, path)
        for method, path in stub.requests
        if method in {"POST", "PUT", "PATCH", "DELETE"}
        and not (method == "POST" and path.endswith("/sql-query/schema/graph") or method == "PUT" and re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+/view", path))
    ]
    assert unexpected == [], f"Presentation issued an unexpected write: {unexpected}"
    assert {key: stub.sql_agent_project.get(key) for key in stub.query_before} == stub.query_before


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_sql_er_active_and_unused_tables_have_distinct_readable_styles(page, base_url, theme):
    stub, graph = _open(page, base_url)
    page.evaluate("theme => { document.documentElement.classList.toggle('dark', theme === 'dark'); document.documentElement.dataset.theme = theme; }", theme)
    active = graph.locator('[data-entity-id="orders"]')
    unused = graph.locator('[data-entity-id="customers"]')
    expect(active).to_have_attribute("data-membership", "QUERY")
    expect(unused).to_have_attribute("data-membership", "RELATED")

    def palette(locator):
        return locator.evaluate("node => { const style=getComputedStyle(node); return {border:style.borderColor,color:style.color,opacity:style.opacity}; }")

    assert palette(active)["border"] != palette(unused)["border"]
    assert palette(active)["color"] != palette(unused)["color"]
    assert palette(unused)["opacity"] == "1"
    expect(graph.get_by_label("Легенда диаграммы")).to_contain_text("В запросе")
    artifact_root = Path(os.environ["PW_ARTIFACTS_DIR"])
    artifact_root.mkdir(parents=True, exist_ok=True)
    expect(graph.get_by_role("status").filter(has_text="Вид сохранён")).to_be_visible(timeout=15000)
    page.evaluate("document.fonts.ready")
    graph.screenshot(path=str(artifact_root / f"sql-er-membership-{theme}.png"), animations="disabled")
    build_root = os.getenv("SQL_BROWSER_BUILD_ROOT")
    build_index = Path(build_root, "index.html") if build_root else None
    (artifact_root / f"sql-er-membership-{theme}.json").write_text(
        json.dumps(
            {
                "browser": page.context.browser.browser_type.name,
                "version": page.context.browser.version,
                "os": platform.system(),
                "viewport": page.viewport_size,
                "theme": theme,
                "query_sha256": hashlib.sha256(SQL.encode()).hexdigest(),
                "build_index_sha256": hashlib.sha256(build_index.read_bytes()).hexdigest() if build_index and build_index.is_file() else None,
                "evidence": "captured-screenshot-and-style-assertions; no pixel-baseline comparison",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _no_query_mutations(stub)


def test_sql_er_hover_and_selection_keep_unused_table_outside_query(page, base_url, snap):
    stub, graph = _open(page, base_url)
    unused = graph.locator('[data-entity-id="customers"]')
    unused.hover()
    unused.click()
    expect(unused).to_have_attribute("data-membership", "RELATED")
    expect(graph.get_by_label("Свойства выбранной сущности")).to_contain_text("Пока не входит в запрос")
    expect(graph.get_by_label("Свойства выбранной сущности").locator("p.font-mono").filter(has_text=re.compile(r"^id\s+BIGINT$"))).to_be_visible()
    expect(graph.get_by_label("Свойства выбранной сущности")).to_contain_text("BIGINT")
    disclosure = page.locator("summary").filter(has_text=re.compile("^Сущности и связи$"))
    disclosure.click()
    expect(graph).not_to_be_visible()
    disclosure.click()
    unused.hover()
    unused.click()
    expect(graph.get_by_label("Свойства выбранной сущности")).to_contain_text("Пока не входит в запрос")
    snap("er-after-reopen")
    _no_query_mutations(stub)


def test_sql_related_table_requires_choice_between_buyer_and_payer_keys(page, base_url):
    stub, graph = _open(page, base_url)
    graph.locator('[data-entity-id="customers"]').click()
    add = graph.get_by_role("button", name="Добавить в запрос", exact=True)
    expect(add).to_be_disabled()
    route = graph.get_by_label("Путь соединения связанной таблицы")
    expect(route).to_contain_text("customer_id=id")
    expect(route).to_contain_text("payer_id=id")
    route.select_option("1")
    add.click()
    expect(page.get_by_label("Что изменить?", exact=True)).to_have_value(re.compile("payer_id=id"))
    _no_query_mutations(stub)


def test_sql_related_tables_expand_through_intermediate_entities_without_changing_sql(page, base_url):
    stub, graph = _open(page, base_url)
    expect(graph.locator('[data-entity-id="products"]')).to_have_count(0)
    graph.get_by_label("Глубина связанных таблиц").select_option("2")
    expect(graph.locator('[data-entity-id="products"]')).to_have_count(1)
    graph.get_by_label("Глубина связанных таблиц").select_option("3")
    expect(graph.locator('[data-entity-id="categories"]')).to_have_count(1)
    graph.locator(".react-flow__controls-fitview").click()
    graph.locator('[data-entity-id="categories"]').click()
    expect(graph.get_by_label("Путь соединения связанной таблицы")).to_contain_text("orders → order_items → products → categories")
    assert stub.graph_requests[-1]["sql"] == SQL
    _no_query_mutations(stub)


def test_sql_er_query_only_and_search_are_presentation_filters(page, base_url):
    stub, graph = _open(page, base_url)
    graph.get_by_label("Только запрос", exact=True).check()
    expect(graph.get_by_test_id("sql-er-table")).to_have_count(1)
    graph.get_by_label("Только запрос", exact=True).uncheck()
    graph.get_by_label("Поиск таблицы на диаграмме").fill("customers")
    expect(graph.get_by_test_id("sql-er-table")).to_have_count(1)
    expect(graph.get_by_test_id("sql-er-table")).to_have_attribute("data-membership", "RELATED")
    _no_query_mutations(stub)


def test_sql_er_personal_view_survives_reload_without_creating_query_revision(page, base_url):
    stub, graph = _open(page, base_url)
    with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/view") and response.request.post_data_json["view"]["mode"] == "query"):
        graph.get_by_label("Только запрос", exact=True).check()
    expect(graph.get_by_role("status").filter(has_text="Вид сохранён")).to_be_visible(timeout=15000)
    assert stub.view["view"]["mode"] == "query"
    page.reload(wait_until="commit")
    expect(page.locator("summary").filter(has_text=re.compile("^Сущности и связи$"))).to_be_visible(timeout=30000)
    _open_sql_panels(page)
    expect(page.get_by_test_id("sql-schema-graph").get_by_label("Только запрос", exact=True)).to_be_checked(timeout=30000)
    _no_query_mutations(stub)


def test_sql_er_catalog_failure_and_retry_preserve_current_query(page, base_url):
    stub = SqlWorkspaceBrowserStub()
    stub.graph_offline = True
    _install_session(page)
    page.route("**/api/v1/**", stub)
    page.goto(f"{base_url.rstrip('/')}/sql-projects", wait_until="commit")
    graph = page.get_by_test_id("sql-schema-graph")
    expect(page.locator("summary").filter(has_text=re.compile("^Сущности и связи$"))).to_be_visible(timeout=30000)
    _open_sql_panels(page)
    expect(graph.get_by_role("alert")).to_contain_text("Каталог временно недоступен", timeout=30000)
    stub.graph_offline = False
    graph.get_by_role("button", name="Обновить схему").click()
    expect(graph.locator('[data-entity-id="orders"]')).to_have_count(1)
    _no_query_mutations(stub)


@pytest.mark.parametrize("compact", [False, True], ids=["desktop", "compact"])
@pytest.mark.parametrize("magnification", [1, 2], ids=["100percent", "200percent-css"])
def test_sql_er_list_and_fullscreen_remain_operable_on_compact_screens(page, base_url, compact, magnification):
    stub, graph = _open(page, base_url, compact=compact)
    page.evaluate("value => document.documentElement.style.zoom = String(value)", magnification)
    assert graph.evaluate("node => Number(getComputedStyle(document.documentElement).zoom)") == magnification
    graph.get_by_role("button", name="Список", exact=True).click()
    expect(graph.get_by_role("table")).to_contain_text("Не входит в запрос")
    graph.get_by_role("button", name="Посмотреть поля").nth(1).click()
    expect(graph.get_by_label("Свойства выбранной сущности")).to_contain_text("Клиенты")
    graph.get_by_role("button", name="Развернуть", exact=True).click()
    for name in ("Диаграмма", "Свернуть", "Обновить схему"):
        button = graph.get_by_role("button", name=name, exact=True)
        button.scroll_into_view_if_needed()
        expect(button).to_be_in_viewport(ratio=1)
    page.keyboard.press("Escape")
    expect(graph.get_by_role("button", name="Развернуть", exact=True)).to_be_visible()
    _no_query_mutations(stub)


@pytest.mark.parametrize("roles", ["customer-roles", "employee-self-join"])
def test_sql_er_keeps_two_aliases_of_one_table_distinct(page, base_url, roles):
    if roles == "customer-roles":
        query = "SELECT buyer.region AS buyer_region, payer.region AS payer_region FROM public.orders o LEFT JOIN public.customers buyer ON o.customer_id=buyer.id LEFT JOIN public.customers payer ON o.payer_id=payer.id LIMIT :row_limit"
        entity, aliases = "customers", ("buyer", "payer")
        stub = SqlWorkspaceBrowserStub(query)
    else:
        from test.evals.sql_projects.schema import accepted_schema

        tables = [
            {
                **item["selected_table"],
                "type": "table",
                "name": item["selected_table"]["technical_name"],
                "display_name": item["selected_table"]["technical_name"],
                "column_details": item["selected_table"]["columns"],
            }
            for item in accepted_schema()["schema_snapshot"]["requirements"]
        ]
        query = "SELECT employee.name AS employee_name, manager.name AS manager_name FROM public.orders o LEFT JOIN public.customers c ON o.customer_id=c.id LEFT JOIN public.employees employee ON c.account_manager_id=employee.id LEFT JOIN public.employees manager ON employee.manager_id=manager.id LIMIT :row_limit"
        entity, aliases = "employees", ("employee", "manager")
        stub = SqlWorkspaceBrowserStub(query, accepted_ids=["orders", "customers", "employees"], tables=tables)
    stub, graph = _open(page, base_url, stub=stub)
    expect(graph.locator(f'[data-entity-id="{entity}"][data-membership="QUERY"]')).to_have_count(2)
    for alias in aliases:
        node = graph.locator(f'[data-entity-id="{entity}"][data-alias="{alias}"]')
        expect(node).to_have_count(1)
        node.click()
        inspector = graph.get_by_label("Свойства выбранной сущности")
        expect(inspector).to_contain_text(f"public.{entity} {alias}")
        inspector.get_by_role("button", name="Обсудить удаление", exact=True).click()
        expect(page.get_by_label("Что изменить?", exact=True)).to_have_value(re.compile(f"с псевдонимом {alias}\\b"))
    assert stub.graph_requests[-1]["sql"] == query
    _no_query_mutations(stub)


def test_sql_er_parallel_keys_have_distinct_paths_and_accessible_labels(page, base_url):
    stub, graph = _open(page, base_url)
    buyer = graph.get_by_role("button", name="Связь FK · customer_id · ?", exact=True)
    payer = graph.get_by_role("button", name="Связь FK · payer_id · ?", exact=True)
    expect(buyer).to_be_visible()
    expect(payer).to_be_visible()
    paths = graph.locator(".react-flow__edge-path").evaluate_all("nodes => nodes.map(node => node.getAttribute('d'))")
    assert len(paths) == len(set(paths)), "Alternative foreign keys overlap exactly"
    payer.click()
    expect(graph.get_by_label("Свойства выбранной сущности")).to_contain_text("payer_id → id")
    _no_query_mutations(stub)


def test_sql_er_restores_exact_viewport_and_node_positions(page, base_url):
    stub = SqlWorkspaceBrowserStub()
    stub.view = {"version": 9, "view": {"positions": {"query:root:o": {"x": 80, "y": 100}}, "viewport": {"x": 70, "y": 90, "zoom": 0.7}, "mode": "all", "depth": 1, "list_view": False}}
    stub, graph = _open(page, base_url, stub=stub)
    viewport = graph.locator(".react-flow__viewport")
    expect(viewport).to_have_css("transform", re.compile(r"matrix\(0\.7, 0, 0, 0\.7, 70, 90\)"))
    expect(graph.locator('.react-flow__node[data-id="query:root:o"]')).to_have_css("transform", "matrix(1, 0, 0, 1, 80, 100)")
    expect(graph.get_by_role("status").filter(has_text="Вид сохранён")).to_be_visible(timeout=15000)
    assert stub.view["view"]["viewport"] == {"x": 70, "y": 90, "zoom": 0.7}
    assert stub.view["view"]["positions"]["query:root:o"] == {"x": 80, "y": 100}
    _no_query_mutations(stub)


def test_sql_er_view_load_failure_keeps_graph_readable_and_does_not_overwrite_saved_view(page, base_url):
    stub = SqlWorkspaceBrowserStub()
    stub.view_failure = ("GET", 503)
    stub, graph = _open(page, base_url, stub=stub)
    expect(graph.get_by_role("status").filter(has_text="Не удалось загрузить расположение")).to_be_visible()
    graph.get_by_label("Только запрос", exact=True).check()
    graph.get_by_role("button", name="Список", exact=True).click()
    expect(graph.get_by_role("table")).to_contain_text("В запросе")
    assert not any(method == "PUT" and path.endswith("/view") for method, path in stub.requests)
    _no_query_mutations(stub)


def test_sql_er_view_conflict_stops_autosave_without_mutating_query(page, base_url):
    stub = SqlWorkspaceBrowserStub()
    stub.view_failure = ("PUT", 409)
    stub, graph = _open(page, base_url, stub=stub)
    expect(graph.get_by_role("status").filter(has_text="Конфликт вида")).to_be_visible(timeout=15000)
    attempts = sum(method == "PUT" and path.endswith("/view") for method, path in stub.requests)
    graph.get_by_label("Только запрос", exact=True).check()
    graph.get_by_role("button", name="Список", exact=True).click()
    expect(graph.get_by_role("table")).to_contain_text("В запросе")
    page.wait_for_timeout(1800)  # Observe longer than two 800ms save-debounce windows.
    assert sum(method == "PUT" and path.endswith("/view") for method, path in stub.requests) == attempts
    assert stub.view["version"] == 0
    _no_query_mutations(stub)


@pytest.mark.parametrize("metadata,message", [({"freshness": {"stale": True}}, "Метаданные устарели"), ({"truncated": True}, "Показана ограниченная часть графа")], ids=["stale", "truncated"])
def test_sql_er_metadata_limits_are_exposed_without_fabricating_completeness(page, base_url, metadata, message):
    stub = SqlWorkspaceBrowserStub()
    stub.graph_metadata = metadata
    stub, graph = _open(page, base_url, stub=stub)
    expect(graph.get_by_role("status").filter(has_text=message)).to_be_visible()
    _no_query_mutations(stub)


def test_sql_er_reader_can_inspect_but_cannot_propose_entity_or_field_changes(page, base_url):
    stub = SqlWorkspaceBrowserStub()
    stub.sql_agent_project["capabilities"].update(edit=False, execute=False)
    stub, graph = _open(page, base_url, stub=stub)
    graph.locator('[data-entity-id="customers"]').click()
    graph.get_by_label("Путь соединения связанной таблицы").select_option("0")
    expect(graph.get_by_role("button", name="Добавить в запрос", exact=True)).to_be_disabled()
    graph.locator('[data-entity-id="orders"]').click()
    expect(graph.get_by_role("button", name="Обсудить удаление", exact=True)).to_be_disabled()
    field_buttons = graph.get_by_role("button", name="В фильтр", exact=True)
    assert field_buttons.count() > 0
    for button in field_buttons.all():
        expect(button).to_be_disabled()
    _no_query_mutations(stub)


def test_sql_er_list_supports_keyboard_selection_without_changing_sql(page, base_url):
    stub, graph = _open(page, base_url)
    graph.get_by_role("button", name="Список", exact=True).click()
    fields = graph.get_by_role("button", name="Посмотреть поля").nth(1)
    graph.get_by_role("button", name="Диаграмма", exact=True).focus()
    for _ in range(30):
        if fields.evaluate("node => node===document.activeElement"):
            break
        page.keyboard.press("Tab")
    expect(fields).to_be_focused()
    page.keyboard.press("Enter")
    expect(graph.get_by_label("Свойства выбранной сущности")).to_contain_text("Клиенты")
    expect(graph.get_by_label("Свойства выбранной сущности")).to_contain_text("Пока не входит в запрос")
    inspector = graph.get_by_label("Свойства выбранной сущности")
    route = inspector.get_by_label("Путь соединения связанной таблицы")
    add = inspector.get_by_role("button", name="Добавить в запрос", exact=True)
    expect(add).to_be_disabled()
    for _ in range(15):
        if route.evaluate("node => node === document.activeElement"):
            break
        page.keyboard.press("Tab")
    expect(route).to_be_focused()
    page.keyboard.press("Home")
    page.keyboard.press("ArrowDown")
    expect(route).to_have_value("0")
    page.keyboard.press("Escape")
    expect(add).to_be_enabled()
    page.keyboard.press("Tab")
    expect(add).to_be_focused()
    page.keyboard.press("Tab")
    all_fields = inspector.get_by_label("Все поля", exact=True)
    expect(all_fields).to_be_focused()
    page.keyboard.press("Space")
    expect(all_fields).to_be_checked()
    expect(inspector).to_contain_text("region")
    _no_query_mutations(stub)


def test_sql_er_cte_and_physical_source_have_distinct_kinds(page, base_url):
    query = "WITH eligible AS (SELECT o.id AS order_id FROM public.orders o WHERE o.amount>=:minimum) SELECT e.order_id AS order_id FROM eligible e ORDER BY e.order_id LIMIT :row_limit"
    stub, graph = _open(page, base_url, stub=SqlWorkspaceBrowserStub(query))
    cte = graph.locator('[data-alias="e"]')
    expect(cte).to_contain_text("CTE")
    expect(cte).to_have_attribute("data-membership", "QUERY")
    expect(graph.locator('[data-entity-id="orders"]')).to_have_count(1)
    _no_query_mutations(stub)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_sql_er_muted_entity_text_keeps_readable_contrast(page, base_url, theme):
    stub, graph = _open(page, base_url)
    page.evaluate("theme => { document.documentElement.classList.toggle('dark',theme==='dark'); document.documentElement.dataset.theme=theme; }", theme)
    ratio = graph.locator('[data-entity-id="customers"]').evaluate("""node => {
      const context=document.createElement('canvas').getContext('2d');
      const luminance=color => {context.fillStyle=color; context.fillRect(0,0,1,1); const rgb=[...context.getImageData(0,0,1,1).data].slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4); return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;};
      const style=getComputedStyle(node), foreground=luminance(style.color),background=luminance(style.backgroundColor);
      return (Math.max(foreground,background)+.05)/(Math.min(foreground,background)+.05);
    }""")
    assert ratio >= 4.5, f"Muted text contrast is {ratio:.2f}, below 4.5"
    _no_query_mutations(stub)


def test_sql_er_historical_selection_matches_viewed_sql_and_keeps_current_artifact(page, base_url):
    stub = SqlWorkspaceBrowserStub(accepted_ids=["orders", "customers"])
    historical = SQL.replace(" WHERE", " LEFT JOIN public.customers c ON c.id=o.customer_id WHERE")
    stub.history = [{"id": "query-old", "revision": 1, "sql": historical, "blocks": query_blocks(historical)}]
    stub, graph = _open(page, base_url, stub=stub)
    expect(graph.locator('[data-entity-id="customers"]')).to_have_attribute("data-membership", "RELATED")
    revision = page.get_by_test_id("sql-revision-workspace")
    revision.get_by_role("button", name="Запрос · версия 1", exact=True).click()
    expect(revision.get_by_label("Проверенный SQL", exact=True)).to_have_value(historical)
    expect(graph.get_by_role("status").filter(has_text="Версия запроса 1")).to_be_visible()
    expect(graph.locator('[data-entity-id="customers"]')).to_have_attribute("data-membership", "QUERY")
    assert stub.graph_requests[-1]["sql"] == historical
    revision.get_by_role("button", name="Запрос · версия 2", exact=True).click()
    expect(graph.locator('[data-entity-id="customers"]')).to_have_attribute("data-membership", "RELATED")
    expect(graph.get_by_role("status").filter(has_text="Версия запроса 1")).to_have_count(0)
    _no_query_mutations(stub)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_sql_er_zoom_controls_have_contrasting_background_and_foreground(page, base_url, theme):
    stub, graph = _open(page, base_url)
    page.evaluate("theme => document.documentElement.classList.toggle('dark',theme==='dark')", theme)
    controls = graph.locator(".react-flow__controls-button")
    assert controls.count() >= 3
    for control in controls.all():
        ratio = control.evaluate("""node => {
          const context=document.createElement('canvas').getContext('2d');
          const luminance=color => {context.clearRect(0,0,1,1); context.fillStyle=color; context.fillRect(0,0,1,1); const rgb=[...context.getImageData(0,0,1,1).data].slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4); return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;};
          const style=getComputedStyle(node),a=luminance(style.color),b=luminance(style.backgroundColor);
          return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
        }""")
        assert ratio >= 4.5, f"{theme} control contrast is {ratio:.2f}"
    _no_query_mutations(stub)


def test_sql_er_large_catalog_keeps_all_hundred_nodes_accessible_through_list(page, base_url):
    stub = SqlWorkspaceBrowserStub()
    template = deepcopy(stub.tables[2])
    for index in range(97):
        table = deepcopy(template)
        name = f"related_{index:02}"
        table.update(id=name, name=name, technical_name=name, display_name=f"Связанная {index:02}", fqn=f"warehouse.analytics.public.{name}")
        for column in table["column_details"]:
            column["id"] = column["fqn"] = f"{table['fqn']}.{column['name']}"
        stub.tables.append(table)
    stub, graph = _open(page, base_url, stub=stub)
    expect(graph.get_by_test_id("sql-er-table")).to_have_count(100, timeout=30000)
    graph.get_by_role("button", name="Список", exact=True).click()
    expect(graph.get_by_role("table").locator("tbody tr")).to_have_count(100)
    graph.get_by_label("Поиск таблицы на диаграмме").fill("related_96")
    expect(graph.get_by_role("table").locator("tbody tr")).to_have_count(1)
    expect(graph.get_by_role("table")).to_contain_text("Не входит в запрос")
    _no_query_mutations(stub)


def test_sql_er_search_matches_schema_and_table_instance_alias(page, base_url):
    query = "SELECT billing_party.region AS region FROM public.orders o LEFT JOIN public.customers billing_party ON o.payer_id=billing_party.id LIMIT :row_limit"
    stub, graph = _open(page, base_url, stub=SqlWorkspaceBrowserStub(query))
    search = graph.get_by_label("Поиск таблицы на диаграмме")
    search.fill("PUBLIC")
    expect(graph.get_by_test_id("sql-er-table")).to_have_count(3)
    search.fill("billing_party")
    expect(graph.get_by_test_id("sql-er-table")).to_have_count(1)
    expect(graph.get_by_test_id("sql-er-table")).to_have_attribute("data-alias", "billing_party")
    _no_query_mutations(stub)
