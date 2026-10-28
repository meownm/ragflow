"""Persisted source selection in the real workbench, with controlled transport."""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from business_documents.sql_query.revisions import query_blocks
from business_documents.sql_query.exports import graph_svg, json_chunks, public_graph, result_json_chunks
from business_documents.sql_query.query_graph import project_query_graph, related_catalog, relationship_paths, snapshot_tables
from test.playwright.e2e.test_document_constructor_ui import _envelope, _fulfill_json, _install_session
from test.playwright.e2e.test_sql_workspace_ui import SqlWorkspaceBrowserStub, _open, _open_sql_panels


class ConnectionBrowserStub(SqlWorkspaceBrowserStub):
    def __init__(self, *, initial="profile-1", ready=False, reader=False, compile_failure=False, before_query=False):
        super().__init__()
        self.profiles = [
            {"id": "profile-1", "name": "QA Warehouse A", "version": 1, "max_rows": 1000, "statement_timeout_ms": 5000},
            {"id": "profile-2", "name": "QA Warehouse B", "version": 2, "max_rows": 1000, "statement_timeout_ms": 5000},
        ]
        self.sql_agent_project["execution_profile_id"] = initial
        for profile in self.profiles:
            profile.update(target_database="qa_warehouse", dialect="postgres")
        self.sql_agent_project["capabilities"].update(edit=not reader, execute=not reader)
        if before_query:
            self.sql_agent_project.update(stage="QUERY", next_agent="QUERY", next_action="CONTINUE", compilation=None)
            self.sql_agent_project["artifact_ids"]["query"] = None
            self.sql_agent_project["artifacts"]["query"] = None
        if compile_failure:
            self.sql_agent_project["compilation"] = None
        if ready:
            run = {
                "id": "old-run",
                "status": "READY",
                "row_count": 1,
                "columns": ["order_id", "amount"],
                "duration_ms": 2,
                "compilation_id": "compilation-1",
                "profile_id": "profile-1",
                "profile_version": 3,
                "profile_name": "Original warehouse",
            }
            self.sql_agent_project.update(latest_run=run, runs=[run], next_action="VIEW_RESULT")
        self.primary = self.sql_agent_project["id"]
        other = deepcopy(self.sql_agent_project)
        other.update(id="other-project", title="Второй проект", execution_profile_id="profile-1", latest_run=None, runs=[])
        self.projects = {self.primary: self.sql_agent_project, "other-project": other}
        self.immutable = {key: deepcopy(self.sql_agent_project.get(key)) for key in ("artifact_ids", "artifacts", "compilation", "latest_run")}
        self.source_snapshots = {}
        if self.sql_agent_project.get("compilation"):
            self.source_snapshots[self.sql_agent_project["compilation"]["id"]] = {
                "sql": self.sql,
                "schema": deepcopy(self.sql_agent_project["artifacts"]["schema"]),
                "query_id": self.sql_agent_project["artifact_ids"]["query"],
            }
        self.connection_commands = []
        self.saved_commands = {}
        self.fail_save = None
        self.hold_save = False
        self.hold_preflight = False
        self.pending_save = []
        self.pending_preflight = []
        self.fail_preflight = None
        self.compile_failure = compile_failure
        self.health_failure = None
        self.hold_health = False
        self.health_checks = []
        self.pending_health = []
        self.detail_reads = []
        self.views = {key: {"version": 0, "view": None} for key in self.projects}

    def __call__(self, route):
        request = route.request
        target = urlparse(request.url)
        path = target.path.rstrip("/")
        prefix = "/api/v1/business-documents/sql-query/projects"
        if path == prefix and request.method == "GET":
            self.requests.append((request.method, path))
            _fulfill_json(route, _envelope(list(self.projects.values())))
            return
        match = re.fullmatch(prefix + r"/([^/]+)(?:/(.*))?", path)
        if not match:
            return super().__call__(route)
        project_id, action = match.groups()
        project = self.projects[project_id]
        if action is None and request.method == "GET":
            self.detail_reads.append(project_id)
            self.requests.append((request.method, path))
            _fulfill_json(route, _envelope(project))
            return
        if action == "preflight":
            self.requests.append((request.method, path))
            if self.fail_preflight:
                _fail_connection(route, *self.fail_preflight)
                return
            if self.hold_preflight:
                self.pending_preflight.append(route)
                return
            selected = parse_qs(target.query).get("selected_profile_id", [project.get("execution_profile_id")])[0]
            profile = next((item for item in self.profiles if item["id"] == selected), None)
            blocker = None
            if not project["capabilities"]["execute"]:
                binding = {"status": "UNAVAILABLE", "candidates": [], "selection": None}
                blocker = {"code": "SQL_EXECUTION_FORBIDDEN", "message": "Выполнение недоступно"}
            elif selected is not None and profile is None:
                binding = {"status": "UNAVAILABLE", "candidates": self.profiles, "selection": None}
                blocker = {"code": "SQL_PROFILE_UNAVAILABLE", "message": "Сохранённый источник недоступен. Выберите другой профиль."}
            elif selected is None and len(self.profiles) > 1:
                binding = {"status": "NEEDS_SELECTION", "candidates": self.profiles, "selection": None}
            else:
                binding = {
                    "status": "BOUND",
                    "candidates": self.profiles,
                    "selection": {"profile": profile or self.profiles[0]},
                    "catalog_scopes": [{"service": "qa", "database": "qa_warehouse", "schema": "public", "table_ids": ["orders"]}],
                }
            _fulfill_json(route, _envelope({"compilation_id": "compilation-1", "state_version": project["state_version"], "binding": binding, **({"blocker": blocker} if blocker else {})}))
            return
        if action == "connection/check" and request.method == "POST":
            self.requests.append((request.method, path))
            body = request.post_data_json
            assert set(body) == {"schema_version", "selected_profile_id"} and body["schema_version"] == "1"
            self.health_checks.append((project_id, deepcopy(body)))
            if self.hold_health:
                self.pending_health.append(route)
                return
            if self.health_failure:
                _fail_connection(route, *self.health_failure)
                return
            selected = body["selected_profile_id"]
            profile = next((item for item in self.profiles if item["id"] == selected), None)
            if selected is None and len(self.profiles) == 1:
                profile = self.profiles[0]
            status = "AVAILABLE" if profile else "NEEDS_SELECTION" if selected is None and self.profiles else "UNAVAILABLE"
            _fulfill_json(
                route,
                _envelope(
                    {
                        "schema_version": "1",
                        "project_id": project_id,
                        "state_version": project["state_version"],
                        "status": status,
                        "binding": {"status": "BOUND" if profile else status, "candidates": self.profiles, "selection": {"profile": profile} if profile else None},
                        "query_checked": False,
                        "checked_at": 1792500000000 if profile else None,
                        "duration_ms": 2 if profile else None,
                    }
                ),
            )
            return
        if action == "compilations" and self.compile_failure:
            self.requests.append((request.method, path))
            _fail_connection(route, 422, "SQL_COMPILATION_FAILED", "SQL требует исправления")
            return
        if action == "connection" and request.method == "PUT":
            self.requests.append((request.method, path))
            body = request.post_data_json
            assert set(body) == {"schema_version", "expected_state_version", "idempotency_key", "selected_profile_id"}
            assert body["schema_version"] == "1" and body["idempotency_key"]
            self.connection_commands.append(deepcopy(body))
            if self.fail_save:
                route.fulfill(
                    status=self.fail_save,
                    content_type="application/json",
                    body=json.dumps({"code": self.fail_save, "message": "Источник изменён в другой вкладке", "data": {"error_code": "SQL_PROJECT_VERSION_CONFLICT"}}),
                )
                return
            key = (project_id, body["idempotency_key"])
            if key not in self.saved_commands:
                assert body["expected_state_version"] == project["state_version"]
                assert body["selected_profile_id"] is None or any(item["id"] == body["selected_profile_id"] for item in self.profiles)
                project.update(execution_profile_id=body["selected_profile_id"], state_version=project["state_version"] + 1)
                self.saved_commands[key] = deepcopy(project)
            result = self.saved_commands[key]
            if self.hold_save:
                self.pending_save.append((route, result))
                return
            _fulfill_json(route, _envelope(result))
            return
        if action == "graph":
            self.requests.append((request.method, path))
            payload = request.post_data_json
            saved = self.source_snapshots.get(payload["compilation_id"])
            if saved is None:
                historical = next((item for item in self.history if item.get("compilation_id") == payload["compilation_id"]), None)
                if historical:
                    saved = {"sql": historical["sql"], "query_id": historical["id"], "schema": self.immutable["artifacts"]["schema"]}
            if saved is None or saved["query_id"] != payload["query_artifact_id"]:
                _fail_connection(route, 409, "SQL_GRAPH_SOURCE_MISMATCH", "Сохранённый источник графа недоступен")
                return
            frozen = {table["id"]: table for table in snapshot_tables(saved["schema"])}
            visible = [frozen.get(table["id"], table) for table in self.tables]
            initial = project_query_graph(saved["sql"], visible)
            roots = list({node["entity_id"] for node in initial["nodes"] if node["membership"] == "QUERY" and node["entity_id"]})
            neighborhood = related_catalog(visible, roots, depth=payload["depth"])
            self.graph_requests.append({"schema_version": "1", "entity_ids": roots, "sql": saved["sql"], "depth": payload["depth"], "source_compilation_id": payload["compilation_id"]})
            _fulfill_json(
                route,
                _envelope(
                    {
                        **project_query_graph(saved["sql"], neighborhood["tables"]),
                        **neighborhood,
                        **relationship_paths(neighborhood["relationships"], roots, max_hops=payload["depth"]),
                        "freshness": {"stale": False},
                        "warnings": [],
                    }
                ),
            )
            return
        if action == "workspace":
            self.requests.append((request.method, path))
            query_id = project["artifact_ids"].get("query")
            sql = self.sql if query_id else ""
            _fulfill_json(
                route,
                _envelope(
                    {
                        "project_id": project_id,
                        "state_version": project["state_version"],
                        "query_artifact_id": query_id,
                        "document_artifact_id": None,
                        "sql": sql,
                        "blocks": query_blocks(sql) if sql else [],
                        "query_revisions": (
                            [
                                {
                                    "id": query_id,
                                    "compilation_id": project["compilation"]["id"] if project.get("compilation") else None,
                                    "revision": 2 if self.history else 1,
                                    "sql": sql,
                                    "blocks": query_blocks(sql),
                                    "parameters": project.get("artifacts", {}).get("query", {}).get("parameters"),
                                    "accepted_by": "qa-owner",
                                    "created_at": 1792530000000,
                                    "source_artifact_ids": [project["artifact_ids"].get("requirements"), project["artifact_ids"].get("schema")],
                                    "matches_current_context": True,
                                }
                            ]
                            if query_id
                            else []
                        )
                        + self.history,
                        "draft": None,
                        "threads": [],
                        "history": [],
                    }
                ),
            )
            return
        if action == "runs/old-run/preview":
            self.requests.append((request.method, path))
            _fulfill_json(
                route,
                _envelope(
                    {
                        "run_id": "old-run",
                        "columns": ["order_id", "amount"],
                        "rows": [[42, "1250.00"]],
                        "row_count": 1,
                        "offset": 0,
                        "duration_ms": 2,
                        "result_bytes": 20,
                        "checks": {"status": "PASS", "schema": "PASS", "bounds": "PASS", "truncated": False, "null_cells": 0, "completeness": "FULL"},
                    }
                ),
            )
            return
        previous_project, previous_view = self.sql_agent_project, self.view
        self.sql_agent_project, self.view = project, self.views[project_id]
        try:
            return super().__call__(route)
        finally:
            self.views[project_id] = self.view
            self.sql_agent_project, self.view = previous_project, previous_view

    def assert_sql_preserved(self):
        assert {key: self.projects[self.primary].get(key) for key in self.immutable} == self.immutable
        unexpected = [
            (method, path)
            for method, path in self.requests
            if method in {"POST", "PUT", "PATCH", "DELETE"}
            and not (
                method == "POST"
                and path.endswith(("/graph", "/connection/check"))
                or method == "PUT"
                and path.endswith(("/view", "/connection"))
                or self.compile_failure
                and method == "POST"
                and path.endswith("/compilations")
            )
        ]
        assert unexpected == []


class ExportBrowserStub(ConnectionBrowserStub):
    """Controlled transport; browser downloads and pure renderers are real."""

    def __init__(self):
        super().__init__(ready=True, reader=True)
        self.exports = []
        self.export_error = None
        self.hold_export = False
        self.pending_export = []

    def __call__(self, route):
        if urlparse(route.request.url).path.endswith("/export"):
            command = route.request.post_data_json
            self.exports.append(deepcopy(command))
            if self.hold_export:
                self.pending_export.append(route)
                return
            if self.export_error:
                _fail_connection(route, 403, "CATALOG_ACCESS_DENIED", self.export_error)
                return
            self.fulfill_export(route, command)
            return
        return super().__call__(route)

    def fulfill_export(self, route, command):
        source = {
            "project_id": self.primary,
            "query": {"id": command.get("query_artifact_id", "query-1"), "revision": 1},
            "compilation": {"id": command.get("compilation_id", "compilation-1")},
            "schema": {"id": "schema-1"},
        }
        content_type = "application/json"
        if command["kind"] == "RESULT":
            body = b"".join(
                result_json_chunks(
                    {"source": source, "run": {"id": command["run_id"], "profile_id": "profile-1", "profile_version": 3}},
                    ["id", "amount", "absent", "empty", "present"],
                    [[9223372036854775807, "0.00100", None, "", False]],
                    [{"postgres_oid": oid} for oid in [20, 1700, 25, 25, 16]],
                )
            )
        elif command["kind"] == "GRAPH":
            neighborhood = related_catalog(self.tables, ["orders"], depth=command["view"]["depth"])
            graph = {
                **project_query_graph(self.sql, neighborhood["tables"]),
                **neighborhood,
                **relationship_paths(neighborhood["relationships"], ["orders"], max_hops=command["view"]["depth"]),
                "freshness": {},
                "warnings": [],
            }
            packet = {
                "source": source,
                "view": command["view"],
                "exported_at": "2026-10-21T00:00:00Z",
                "graphs": [{"label": "Выбранная проверенная версия", "graph": public_graph(graph, only_query=command["view"]["mode"] == "query")}],
            }
            if command["format"] == "svg":
                body, content_type = graph_svg(packet), "image/svg+xml"
            else:
                body = b"".join(json_chunks(packet))
        else:
            sql = next((item["sql"] for item in self.history if item["id"] == command["query_artifact_id"]), self.sql)
            body = b"".join(json_chunks({"source": source, "sql": sql, "parameters": [{"name": "row_limit", "type": "integer", "value": 100}]}))
        route.fulfill(status=200, content_type=content_type, body=body, headers={"Cache-Control": "no-store"})


@pytest.mark.parametrize(
    "kind,label",
    [("QUERY", "Скачать SQL и параметры"), ("RESULT", "Скачать результат JSON"), ("GRAPH-svg", "Скачать ER SVG"), ("GRAPH-json", "Скачать ER JSON")],
    ids=["query", "result", "er-svg", "er-json"],
)
def test_sql_reader_downloads_selected_query_result_and_er_without_mutations(page, base_url, kind, label):
    stub, graph = _open(page, base_url, stub=ExportBrowserStub())
    if kind == "QUERY":
        page.locator("summary").filter(has_text=re.compile("^SQL и параметры$")).click()
    if kind == "GRAPH-json":
        graph.get_by_label("Только запрос", exact=True).check()
    with page.expect_download() as captured:
        page.get_by_role("button", name=label, exact=True).click()
    download = captured.value
    raw = Path(download.path()).read_bytes()
    assert download.failure() is None
    command = stub.exports[-1]
    assert command["kind"] == kind.split("-")[0]
    if kind == "RESULT":
        data = json.loads(raw)
        assert data["rows"][0] == [
            {"type": "integer", "value": "9223372036854775807"},
            {"type": "decimal", "value": "0.00100"},
            {"type": "null", "value": None},
            {"type": "string", "value": ""},
            {"type": "boolean", "value": False},
        ]
        assert data["run"]["profile_version"] == 3
    elif kind == "QUERY":
        data = json.loads(raw)
        assert data["sql"] == stub.sql and data["source"]["compilation"]["id"] == "compilation-1"
    elif kind == "GRAPH-json":
        data = json.loads(raw)
        assert command["view"]["mode"] == "query"
        assert all(node["membership"] == "QUERY" for node in data["graphs"][0]["graph"]["nodes"])
        assert data["source"]["query"]["id"] == "query-1"
    else:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(raw)
        assert {node.attrib["data-membership"] for node in root.iter() if "data-membership" in node.attrib} == {"QUERY", "RELATED"}
        assert "Связи каталога" in " ".join(root.itertext())
        preview = page.context.new_page()
        try:
            preview.set_content(raw.decode("utf-8"))
            expect(preview.locator("svg")).to_be_visible()
            assert preview.locator('g[data-membership="RELATED"]').count() > 0
            assert preview.evaluate(
                "() => {const svg=document.querySelector('svg');const box=svg.viewBox.baseVal;return [...svg.querySelectorAll('g[data-membership]')].every(node=>{const b=node.getBBox();return b.x>=0&&b.y>=0&&b.x+b.width<=box.width&&b.y+b.height<=box.height});}"
            )
            root_path = Path(os.environ["PW_ARTIFACTS_DIR"])
            root_path.mkdir(parents=True, exist_ok=True)
            preview.screenshot(path=str(root_path / "sql-er-export.svg.png"), full_page=True, animations="disabled")
        finally:
            preview.close()
    assert len(stub.exports) == 1
    stub.assert_sql_preserved()


@pytest.mark.parametrize("copy", [False, True])
def test_sql_open_page_rechecks_revoked_access_for_download_and_copy(page, base_url, copy):
    stub, _ = _open(page, base_url, stub=ExportBrowserStub())
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    page.evaluate("window.__sqlCopied=[]; Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:async text=>window.__sqlCopied.push(text)}})")
    stub.export_error = "Доступ к таблице отозван"
    if copy:
        page.locator("summary").filter(has_text=re.compile("^SQL и параметры$")).click()
    page.get_by_role("button", name="Копировать" if copy else "Скачать результат JSON", exact=True).click()
    expect(page.get_by_role("alert").filter(has_text=stub.export_error)).to_be_visible()
    assert page.evaluate("window.__sqlCopied") == []
    assert downloads == [] and len(stub.exports) == 1


def test_sql_late_export_from_previous_project_cannot_trigger_download(page, base_url):
    stub, _ = _open(page, base_url, stub=ExportBrowserStub())
    stub.hold_export = True
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    page.get_by_role("button", name="Скачать результат JSON", exact=True).click()
    expect(page.get_by_role("button", name="Подготовка…", exact=True)).to_be_disabled()
    assert len(stub.pending_export) == 1
    page.get_by_role("button", name=re.compile("Второй проект")).click()
    expect(page.get_by_role("heading", name="Второй проект", exact=True)).to_be_visible()
    pending = stub.pending_export.pop()
    try:
        stub.fulfill_export(pending, pending.request.post_data_json)
    except Exception as error:
        # A navigation may have already canceled the browser transport.
        assert "disposed" in str(error) or "closed" in str(error) or "canceled" in str(error)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert downloads == []
    expect(page.get_by_role("button", name="Скачать результат JSON", exact=True)).to_have_count(0)


def test_sql_historical_download_uses_selected_compilation_instead_of_current_query(page, base_url):
    stub = ExportBrowserStub()
    old_sql = stub.sql.replace("ORDER BY o.id", "ORDER BY o.id DESC")
    stub.history = [{"id": "old-query", "compilation_id": "old-compilation", "revision": 1, "sql": old_sql, "blocks": query_blocks(old_sql)}]
    _open(page, base_url, stub=stub)
    page.get_by_role("button", name="Запрос · версия 1", exact=True).click()
    with page.expect_download() as captured:
        page.get_by_role("button", name="Скачать запрос · версия 1", exact=True).click()
    packet = json.loads(Path(captured.value.path()).read_bytes())
    assert packet["sql"] == old_sql
    assert packet["source"]["query"]["id"] == "old-query"
    assert stub.exports[-1]["compilation_id"] == "old-compilation"
    assert stub.exports[-1]["query_artifact_id"] == "old-query"


def _fail_connection(route, status, code, message):
    route.fulfill(status=status, content_type="application/json", body=json.dumps({"code": status, "message": message, "data": {"error_code": code}}, ensure_ascii=False))


@pytest.mark.parametrize("initial", [None, "profile-1"], ids=["ambiguous-auto", "already-bound"])
def test_sql_connection_choice_survives_reload_without_revising_sql(page, base_url, initial):
    stub, _ = _open(page, base_url, stub=ConnectionBrowserStub(initial=initial))
    source = page.get_by_role("combobox", name="Источник данных", exact=True)
    expect(source.locator("option[value='profile-2']")).to_have_count(1)
    source.select_option("profile-2")
    expect(page.get_by_text("Выбран профиль: QA Warehouse B", exact=True)).to_be_visible()
    connection = page.get_by_role("region", name="Подключение проекта", exact=True)
    expect(connection).to_contain_text("PostgreSQL · база qa_warehouse · схемы public")
    expect(connection).to_contain_text("Только чтение")
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Сохранить источник", exact=True).click()
    expect(page.get_by_role("button", name="Сохранить источник", exact=True)).to_be_disabled()
    assert stub.projects[stub.primary]["execution_profile_id"] == "profile-2"
    page.reload(wait_until="commit")
    expect(page.get_by_test_id("sql-agent-complete")).to_be_visible(timeout=30000)
    _open_sql_panels(page)
    expect(page.get_by_role("combobox", name="Источник данных", exact=True)).to_have_value("profile-2", timeout=30000)
    expect(page.get_by_text("Выбран профиль: QA Warehouse B", exact=True)).to_be_visible()
    assert len(stub.connection_commands) == 1
    stub.assert_sql_preserved()


def test_sql_revoked_saved_connection_requires_explicit_replacement(page, base_url):
    stub = ConnectionBrowserStub()
    stub.profiles = stub.profiles[1:]
    _open(page, base_url, stub=stub)
    expect(page.get_by_text("Сохранённый источник недоступен. Выберите другой профиль.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_be_disabled()
    assert stub.projects[stub.primary]["execution_profile_id"] == "profile-1"
    assert stub.connection_commands == []
    page.get_by_role("combobox", name="Источник данных", exact=True).select_option("profile-2")
    expect(page.get_by_text("Выбран профиль: QA Warehouse B", exact=True)).to_be_visible()
    page.get_by_role("button", name="Сохранить источник", exact=True).click()
    expect(page.get_by_role("button", name="Сохранить источник", exact=True)).to_be_disabled()
    assert stub.projects[stub.primary]["execution_profile_id"] == "profile-2"
    stub.assert_sql_preserved()


def test_sql_connection_conflict_keeps_the_choice_without_automatic_retry(page, base_url):
    stub, _ = _open(page, base_url, stub=ConnectionBrowserStub())
    stub.fail_save = 409
    page.get_by_role("combobox", name="Источник данных", exact=True).select_option("profile-2")
    expect(page.get_by_text("Выбран профиль: QA Warehouse B", exact=True)).to_be_visible()
    page.get_by_role("button", name="Сохранить источник", exact=True).click()
    expect(page.get_by_text("Источник изменён в другой вкладке", exact=True)).to_be_visible()
    expect(page.get_by_role("combobox", name="Источник данных", exact=True)).to_have_value("profile-2")
    expect(page.get_by_role("button", name="Сохранить источник", exact=True)).to_be_enabled()
    assert len(stub.connection_commands) == 1 and stub.projects[stub.primary]["execution_profile_id"] == "profile-1"
    stub.assert_sql_preserved()


def test_sql_ready_result_keeps_original_connection_after_project_source_change(page, base_url):
    stub, _ = _open(page, base_url, stub=ConnectionBrowserStub(ready=True))
    expect(page.get_by_test_id("sql-result-table")).to_contain_text("1250.00")
    source_label = page.get_by_text("Источник выбранного запуска: Original warehouse · версия профиля 3", exact=True)
    expect(source_label).to_be_visible()
    page.get_by_role("combobox", name="Источник данных", exact=True).select_option("profile-2")
    expect(page.get_by_text("Выбран профиль: QA Warehouse B", exact=True)).to_be_visible()
    with page.expect_response(lambda response: response.request.method == "GET" and response.url.endswith(f"/projects/{stub.primary}")) as refreshed:
        page.get_by_role("button", name="Сохранить источник", exact=True).click()
    refreshed.value.finished()
    _open_sql_panels(page)
    expect(page.get_by_role("button", name="Сохранить источник", exact=True)).to_be_disabled()
    expect(source_label).to_be_visible()
    expect(page.get_by_test_id("sql-result-table")).to_contain_text("1250.00")
    stub.assert_sql_preserved()


def test_sql_late_connection_save_does_not_reopen_previous_project(page, base_url):
    stub, _ = _open(page, base_url, stub=ConnectionBrowserStub())
    stub.hold_save = True
    page.get_by_role("combobox", name="Источник данных", exact=True).select_option("profile-2")
    expect(page.get_by_text("Выбран профиль: QA Warehouse B", exact=True)).to_be_visible()
    page.get_by_role("button", name="Сохранить источник", exact=True).click()
    for _ in range(100):
        if stub.pending_save:
            break
        page.wait_for_timeout(20)
    assert len(stub.pending_save) == 1
    page.get_by_role("button", name=re.compile("Второй проект")).click()
    expect(page.get_by_role("heading", name="Второй проект", exact=True)).to_be_visible()
    route, saved = stub.pending_save.pop()
    with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/connection")) as received:
        _fulfill_json(route, _envelope(saved))
    received.value.finished()
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.get_by_role("heading", name="Второй проект", exact=True)).to_be_visible()
    expect(page.get_by_role("combobox", name="Источник данных", exact=True)).to_have_value("profile-1")
    assert stub.detail_reads.count(stub.primary) == 1
    stub.assert_sql_preserved()


def test_sql_reader_cannot_change_project_connection(page, base_url):
    stub, _ = _open(page, base_url, stub=ConnectionBrowserStub(reader=True))
    expect(page.get_by_role("combobox", name="Источник данных", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Сохранить источник", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Проверить подключение", exact=True)).to_have_count(0)
    expect(page.get_by_text("Выбор подключения доступен пользователям с правом выполнения запросов.", exact=True)).to_be_visible()
    assert stub.connection_commands == []
    stub.assert_sql_preserved()


def test_sql_preflight_is_cancelled_before_navigation_on_reload(page, base_url):
    stub = ConnectionBrowserStub()
    stub.hold_preflight = True
    _open(page, base_url, stub=stub)
    for _ in range(100):
        if stub.pending_preflight:
            break
        page.wait_for_timeout(20)
    assert len(stub.pending_preflight) == 1
    request = stub.pending_preflight[0].request
    with page.expect_event("requestfailed", predicate=lambda current: current == request):
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    stub.hold_preflight = False
    page.reload(wait_until="commit")
    expect(page.get_by_test_id("sql-agent-complete")).to_be_visible(timeout=30000)
    _open_sql_panels(page)
    expect(page.get_by_text("Выбран профиль: QA Warehouse A", exact=True)).to_be_visible(timeout=30000)
    stub.assert_sql_preserved()
    assert page._diag["page_errors"] == []


def test_sql_preflight_retry_preserves_unsaved_profile_and_never_runs_or_saves(page, base_url):
    stub, _ = _open(page, base_url, stub=ConnectionBrowserStub())
    source = page.get_by_role("combobox", name="Источник данных", exact=True)
    expect(page.get_by_text("Выбран профиль: QA Warehouse A", exact=True)).to_be_visible()
    stub.fail_preflight = (503, "SQL_CONNECTION_NETWORK_FAILED", "Источник недоступен по сети")
    source.select_option("profile-2")
    expect(page.get_by_role("alert")).to_have_text("Источник недоступен по сети")
    expect(source).to_have_value("profile-2")
    expect(page.get_by_text("Проверка запроса не завершена.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_be_disabled()
    requests = len([path for method, path in stub.requests if path.endswith("/preflight")])
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert len([path for method, path in stub.requests if path.endswith("/preflight")]) == requests
    stub.fail_preflight = None
    page.get_by_role("button", name="Повторить проверку запроса", exact=True).click()
    expect(page.get_by_text("Выбран профиль: QA Warehouse B", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Повторить проверку запроса", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Сохранить источник", exact=True)).to_be_enabled()
    assert stub.connection_commands == [] and stub.health_checks == []
    stub.assert_sql_preserved()


@pytest.mark.parametrize("compile_failure", [False, True], ids=["query-preflight-failed", "compilation-failed"])
def test_sql_connection_check_is_independent_of_failed_query_validation(page, base_url, compile_failure):
    stub = ConnectionBrowserStub(compile_failure=compile_failure)
    if not compile_failure:
        stub.fail_preflight = (422, "SQL_PHYSICAL_SCHEMA_INVALID", "SQL требует исправления")
    _open(page, base_url, stub=stub)
    expect(page.get_by_text("SQL требует исправления", exact=True)).to_be_visible()
    query_checks = len([path for method, path in stub.requests if path.endswith(("/preflight", "/compilations"))])
    page.get_by_role("button", name="Проверить подключение", exact=True).click()
    expect(page.get_by_text(re.compile("Подключение доступно: QA Warehouse A"))).to_be_visible()
    assert stub.health_checks == [(stub.primary, {"schema_version": "1", "selected_profile_id": "profile-1"})]
    assert len([path for method, path in stub.requests if path.endswith(("/preflight", "/compilations"))]) == query_checks
    run_button = page.get_by_role("button", name="Выполнить запрос", exact=True)
    if compile_failure:
        expect(run_button).to_have_count(0)
        page.get_by_role("combobox", name="Источник данных", exact=True).select_option("profile-2")
        page.get_by_role("button", name="Проверить подключение", exact=True).click()
        expect(page.get_by_text(re.compile("Подключение доступно: QA Warehouse B"))).to_be_visible()
        with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/connection")):
            page.get_by_role("button", name="Сохранить источник", exact=True).click()
        assert stub.projects[stub.primary]["execution_profile_id"] == "profile-2"
        assert len(stub.connection_commands) == 1 and len(stub.health_checks) == 2
    else:
        expect(run_button).to_be_disabled()
        assert stub.connection_commands == []
    stub.assert_sql_preserved()


@pytest.mark.parametrize(
    "status,code,message",
    [
        (503, "SQL_CONNECTION_AUTH_FAILED", "Источник не принял учётные данные"),
        (503, "SQL_CONNECTION_TLS_FAILED", "Не удалось установить защищённое соединение"),
        (503, "SQL_CONNECTION_NETWORK_FAILED", "Источник недоступен по сети"),
        (504, "SQL_CONNECTION_TIMEOUT", "Источник не ответил за время подключения"),
    ],
    ids=["auth", "tls", "network", "timeout"],
)
def test_sql_connection_failure_requires_explicit_retry_and_keeps_project(page, base_url, status, code, message):
    stub, _ = _open(page, base_url, stub=ConnectionBrowserStub())
    expect(page.get_by_text("Выбран профиль: QA Warehouse A", exact=True)).to_be_visible()
    stub.health_failure = (status, code, message)
    page.get_by_role("button", name="Проверить подключение", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text(message)
    expect(page.get_by_role("combobox", name="Источник данных", exact=True)).to_have_value("profile-1")
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_be_disabled()
    assert len(stub.health_checks) == 1
    stub.health_failure = None
    page.get_by_role("button", name="Проверить подключение", exact=True).click()
    expect(page.get_by_text(re.compile("Подключение доступно: QA Warehouse A"))).to_be_visible()
    assert len(stub.health_checks) == 2 and stub.connection_commands == []
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_be_enabled()
    stub.assert_sql_preserved()


def test_sql_connection_probe_is_aborted_when_another_project_is_selected(page, base_url):
    assert_probe_aborted_when_switching(page, base_url, ConnectionBrowserStub())


def assert_probe_aborted_when_switching(page, base_url, stub):
    _open(page, base_url, stub=stub)
    stub.hold_health = True
    with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/connection/check")) as pending:
        page.get_by_role("button", name="Проверить подключение", exact=True).click()
    assert len(stub.pending_health) == 1
    request = pending.value
    with page.expect_event("requestfailed", predicate=lambda current: current == request):
        page.get_by_role("button", name=re.compile("Второй проект")).click()
    expect(page.get_by_role("heading", name="Второй проект", exact=True)).to_be_visible()
    expect(page.get_by_text(re.compile("Подключение доступно:"))).to_have_count(0)
    assert len(stub.health_checks) == 1
    stub.assert_sql_preserved()


class RetentionBrowserStub(ExportBrowserStub):
    """Controlled clock/transport; real result lifetime and download UI."""

    def __init__(self, *, reader=False, already_expired=False):
        import time

        super().__init__()
        self.now = int(time.time() * 1000)
        self.deadline = self.now - 1 if already_expired else self.now + 60_000
        self.expired = False
        self.hold_preview = False
        self.pending_preview = []
        self.preview_reads = []
        self.fail_next_page = False
        self.derived_deadline = None
        project = self.projects[self.primary]
        project["capabilities"].update(edit=not reader, execute=not reader)
        project["latest_run"].update(expires_at=self.deadline, row_count=2)
        if already_expired:
            self.expire()

    def expire(self):
        self.expired = True
        self.projects[self.primary]["latest_run"].update(status="EXPIRED", columns=[], checks={}, result_bytes=0)

    def __call__(self, route):
        target = urlparse(route.request.url)
        if target.path.endswith("/runs/derived-run/preview"):
            self.preview_reads.append(route.request)
            _fulfill_json(
                route,
                _envelope(
                    {
                        "run_id": "derived-run",
                        "expires_at": self.derived_deadline,
                        "columns": ["doubled"],
                        "rows": [[84]],
                        "row_count": 1,
                        "offset": 0,
                        "duration_ms": 2,
                        "result_bytes": 4,
                        "checks": {"status": "PASS"},
                    }
                ),
            )
            return
        if target.path.endswith("/runs/old-run/preview"):
            self.requests.append((route.request.method, target.path))
            self.preview_reads.append(route.request)
            offset = int(parse_qs(target.query).get("offset", ["0"])[0])
            if self.expired or offset and self.fail_next_page:
                self.expire()
                _fail_connection(route, 410, "SQL_RESULT_EXPIRED", "Срок хранения результата истёк")
                return
            payload = {
                "run_id": "old-run",
                "expires_at": self.deadline,
                "columns": ["order_id", "amount"],
                "rows": [[42, "1250.00"]],
                "row_count": 2,
                "offset": offset,
                "duration_ms": 2,
                "result_bytes": 20,
                "checks": {"status": "PASS", "completeness": "FULL"},
            }
            if self.hold_preview:
                self.pending_preview.append((route, payload))
            else:
                _fulfill_json(route, _envelope(payload))
            return
        return super().__call__(route)

    def assert_accepted_query_unchanged(self):
        project = self.projects[self.primary]
        for field in ("artifact_ids", "artifacts", "compilation"):
            assert project[field] == self.immutable[field]
        assert project["state_version"] == 1
        assert self.connection_commands == []
        assert not [(method, path) for method, path in self.requests if method == "POST" and path.endswith(("/runs", "/complete", "/agent-jobs"))]


def _open_retained_result(page, base_url, stub, *, compact=False):
    page.add_init_script(f"window.__sqlRetentionClock = {stub.now}; Date.now = () => window.__sqlRetentionClock;")
    return _open(page, base_url, stub=stub, compact=compact)


def _expire_retained_result(page, stub):
    stub.expire()
    return page.evaluate(
        """deadline => {
        window.__sqlRetentionClock = deadline;
        window.dispatchEvent(new Event('focus'));
        return document.querySelectorAll('[data-testid="sql-result-table"]').length;
    }""",
        stub.deadline,
    )


@pytest.mark.parametrize("compact", [False, True], ids=["desktop", "compact"])
def test_sql_result_expiry_clears_rows_and_preserves_query(page, base_url, compact):
    stub = RetentionBrowserStub()
    _, graph = _open_retained_result(page, base_url, stub, compact=compact)
    expect(page.get_by_test_id("sql-result-table")).to_be_visible()
    assert _expire_retained_result(page, stub) == 0
    expect(page.get_by_role("heading", name="Срок хранения результата истёк", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Скачать результат JSON", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_be_enabled()
    expect(graph.locator('[data-entity-id="orders"][data-membership="QUERY"]')).to_have_count(1)
    root = Path(os.environ.get("PW_ARTIFACTS_DIR", "test/playwright/artifacts"))
    root.mkdir(parents=True, exist_ok=True)
    page.get_by_role("heading", name="Срок хранения результата истёк", exact=True).scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(root / f"result-expired-{'compact' if compact else 'desktop'}.png"), full_page=True, animations="disabled")
    stub.assert_accepted_query_unchanged()


def test_sql_expiry_cancels_pending_preview_and_ignores_late_rows(page, base_url):
    stub = RetentionBrowserStub()
    stub.hold_preview = True
    _open_retained_result(page, base_url, stub)
    for _ in range(100):
        if stub.pending_preview:
            break
        page.wait_for_timeout(20)
    assert len(stub.pending_preview) == 1
    route, payload = stub.pending_preview.pop()
    with page.expect_event("requestfailed", predicate=lambda request: request == route.request):
        assert _expire_retained_result(page, stub) == 0
    _fulfill_json(route, _envelope(payload))
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.get_by_test_id("sql-result-table")).to_have_count(0)
    expect(page.get_by_role("heading", name="Срок хранения результата истёк", exact=True)).to_be_visible()
    stub.assert_accepted_query_unchanged()


def test_sql_expiry_cancels_pending_download_before_a_late_success(page, base_url):
    stub = RetentionBrowserStub()
    _open_retained_result(page, base_url, stub)
    expect(page.get_by_test_id("sql-result-table")).to_be_visible()
    stub.hold_export = True
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    page.get_by_role("button", name="Скачать результат JSON", exact=True).click()
    for _ in range(100):
        if stub.pending_export:
            break
        page.wait_for_timeout(20)
    assert len(stub.pending_export) == 1
    pending = stub.pending_export.pop()
    with page.expect_event("requestfailed", predicate=lambda request: request == pending.request):
        assert _expire_retained_result(page, stub) == 0
    stub.fulfill_export(pending, pending.request.post_data_json)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert downloads == []
    expect(page.get_by_test_id("sql-result-table")).to_have_count(0)
    stub.assert_accepted_query_unchanged()


def test_sql_server_expiry_during_paging_discards_already_visible_rows(page, base_url):
    stub = RetentionBrowserStub()
    _open_retained_result(page, base_url, stub)
    expect(page.get_by_test_id("sql-result-table")).to_be_visible()
    stub.fail_next_page = True
    page.get_by_role("button", name="Показать ещё", exact=True).click()
    expect(page.get_by_role("heading", name="Срок хранения результата истёк", exact=True)).to_be_visible()
    expect(page.get_by_test_id("sql-result-table")).to_have_count(0)
    assert page.evaluate("Date.now()") < stub.deadline
    assert len(stub.preview_reads) == 2
    stub.assert_accepted_query_unchanged()


def test_sql_reader_opening_an_expired_result_does_not_request_or_execute_rows(page, base_url):
    stub = RetentionBrowserStub(reader=True, already_expired=True)
    _open_retained_result(page, base_url, stub)
    expect(page.get_by_role("heading", name="Срок хранения результата истёк", exact=True)).to_be_visible()
    expect(page.get_by_test_id("sql-result-table")).to_have_count(0)
    expect(page.get_by_role("button", name="Выполнить запрос", exact=True)).to_have_count(0)
    expect(page.locator("summary").filter(has_text=re.compile("^SQL и параметры$"))).to_be_visible()
    assert stub.preview_reads == []
    stub.assert_accepted_query_unchanged()


def test_sql_derived_expiry_keeps_the_unexpired_parent_rows(page, base_url):
    stub = RetentionBrowserStub()
    stub.derived_deadline = stub.now + 30_000
    derived = {"id": "derived-run", "kind": "PYTHON", "source_run_id": "old-run", "status": "READY", "expires_at": stub.derived_deadline, "row_count": 1, "checks": {"status": "PASS"}}
    stub.projects[stub.primary]["derived_runs"] = [derived]
    _open_retained_result(page, base_url, stub)
    expect(page.get_by_test_id("sql-result-table")).to_be_visible()
    expect(page.get_by_test_id("sql-python-result-table")).to_be_visible()
    derived.update(status="EXPIRED", checks={})
    visible = page.evaluate(
        """deadline => {
        window.__sqlRetentionClock = deadline;
        window.dispatchEvent(new Event('focus'));
        return [document.querySelectorAll('[data-testid="sql-result-table"]').length, document.querySelectorAll('[data-testid="sql-python-result-table"]').length];
    }""",
        stub.derived_deadline,
    )
    assert visible == [1, 0]
    expect(page.get_by_test_id("sql-result-table")).to_be_visible()
    expect(page.get_by_role("heading", name="Срок хранения результата истёк", exact=True)).to_have_count(0)
    stub.assert_accepted_query_unchanged()


@pytest.mark.parametrize("width", [1440, 1280, 1024])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_sql_result_first_disclosures_and_completion_cancel(page, base_url, snap, width, theme):
    stub = ConnectionBrowserStub(ready=True)
    _install_session(page)
    page.add_init_script(f"localStorage.setItem('ragflow-ui-theme', '{theme}');")
    page.route("**/api/v1/**", stub)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"{base_url.rstrip('/')}/sql-projects", wait_until="commit")
    result = page.get_by_test_id("sql-result-table")
    expect(result).to_be_visible(timeout=120000)
    expect(page.locator("html")).to_have_class(re.compile(rf"(^|\s){theme}(\s|$)"))
    expect(result).to_contain_text("1250.00")
    expect(page.get_by_test_id("sql-run-identity")).to_contain_text("Источник выбранного запуска: Original warehouse · версия профиля 3")
    for label in ("SQL и параметры", "Сущности и связи", "Доработка и обсуждения", "Источник для следующего запуска"):
        details = page.locator("summary").filter(has_text=re.compile(f"^{label}$")).locator("..")
        expect(details).not_to_have_attribute("open", "")
    assert result.bounding_box()["y"] < 650
    assert result.evaluate("el => !!(el.compareDocumentPosition(document.querySelector('.sql-project-context')) & Node.DOCUMENT_POSITION_FOLLOWING)")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    snap(f"sql-result-first-{width}-{theme}")
    page.get_by_role("button", name="Сохранить документ и удалить строки", exact=True).click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_contain_text("Запусков SQL: 1; производных результатов: 0")
    expect(dialog).to_contain_text("Это затронет и другие запуски")
    dialog.get_by_role("button", name="Отмена", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(result).to_contain_text("1250.00")
    assert not any(path.endswith("/complete") for _, path in stub.requests)
    page.locator("summary").filter(has_text=re.compile("^SQL и параметры$")).click()
    expect(page.get_by_role("button", name="Скачать SQL и параметры", exact=True)).to_be_visible()
    page.get_by_role("button", name="Редактировать финальный SQL", exact=True).click()
    expect(page.get_by_test_id("guided-sql-form")).to_be_visible()
    assert stub.connection_commands == []
    stub.assert_sql_preserved()
    assert page._diag["page_errors"] == []


class ExistingOwnerBrowserStub(ConnectionBrowserStub):
    def __init__(self, role):
        super().__init__(ready=True, reader=role == "READER")
        self.role = role
        self.sql_agent_project["capabilities"].update(execute=False, copy=False)

    def __call__(self, route):
        if urlparse(route.request.url).path == "/api/v1/business-documents/capabilities":
            _fulfill_json(route, _envelope({"access_role": self.role, "capabilities": {"read": True, "create": False, "edit_own": self.role != "READER", "edit_all": False}}))
            return
        return super().__call__(route)


@pytest.mark.parametrize("role", ["AUTHOR_EDITOR", "READER"])
def test_sql_existing_owner_without_create_opens_saved_result_but_cannot_create_or_execute(page, base_url, role):
    stub = ExistingOwnerBrowserStub(role)
    _open(page, base_url, stub=stub)
    expect(page.get_by_test_id("sql-result-table")).to_contain_text("1250.00")
    expect(page.get_by_role("button", name="Новый запрос", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Создать копию", exact=True)).to_have_count(0)
    run = page.get_by_role("button", name="Выполнить запрос", exact=True)
    if run.count():
        expect(run).to_be_disabled()
    else:
        expect(page.get_by_role("button", name="Открыть сохранённый проверенный SQL", exact=True)).to_be_visible()
    if role == "READER":
        expect(page.get_by_role("button", name="Сохранить документ и удалить строки", exact=True)).to_have_count(0)
    else:
        expect(page.get_by_role("button", name="Сохранить документ и удалить строки", exact=True)).to_be_enabled()
    assert stub.connection_commands == []
    stub.assert_sql_preserved()
