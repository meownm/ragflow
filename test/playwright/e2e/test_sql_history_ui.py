"""Native history/restore UI with immutable fixture records and real SQL guard."""

from copy import deepcopy
from dataclasses import asdict
import os
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from business_documents.sql_query.project_compilation import build_project_compile_command
from business_documents.sql_query.query_specification import compile_query_payload, parse_schema_snapshot
from business_documents.sql_query.revisions import query_blocks
from test.playwright.e2e.test_document_constructor_ui import _envelope, _fulfill_json
from test.playwright.e2e.test_sql_connection_ui import ConnectionBrowserStub, _fail_connection
from test.playwright.e2e.test_sql_workspace_ui import _open, _open_sql_panels


class HistoryBrowserStub(ConnectionBrowserStub):
    def __init__(self, *, reader=False, without_current_schema=False):
        super().__init__(ready=True, reader=reader)
        project = self.sql_agent_project
        old_schema = deepcopy(project["artifacts"]["schema"])
        old_schema["schema_snapshot"]["requirements"][0]["selected_table"]["display_name"] = "Заказы исходные"
        current_schema = deepcopy(old_schema)
        table = current_schema["schema_snapshot"]["requirements"][0]["selected_table"]
        table.update(version=2, schema_fingerprint="sha256:orders-current", display_name="Заказы актуальные")
        current_schema["accepted_schema"][0].update(version=2, schema_fingerprint=table["schema_fingerprint"])
        next(item for item in self.tables if item["id"] == "orders").update(version=2, schema_fingerprint=table["schema_fingerprint"], display_name="Заказы актуальные")
        old_parameters = deepcopy(project["artifacts"]["query"]["parameters"])
        current_parameters = deepcopy(old_parameters)
        next(item for item in current_parameters if item["name"] == "row_limit")["value"] = 25
        self.records = [
            self.record("query-current", "compilation-current", 2, current_parameters, "qa-current-author", "schema-current"),
            self.record("query-original", "compilation-original", 1, old_parameters, "qa-original-author", "schema-original"),
        ]
        self.records[1]["matches_current_context"] = False
        self.original_records = deepcopy(self.records)
        self.history = [self.records[1]]
        self.source_snapshots.update(
            {
                "compilation-original": {"sql": self.sql, "schema": old_schema, "query_id": "query-original"},
                "compilation-current": {"sql": self.sql, "schema": current_schema, "query_id": "query-current"},
            }
        )
        query = {"mode": "manual", "sql": self.sql, "parameters": current_parameters, "confirmed_alignment": True}
        compiled = compile_query_payload(build_project_compile_command(project["artifacts"]["requirements"], current_schema, query))
        assert compiled["status"] == "READY"
        project.update(
            state_version=7,
            artifact_ids={"requirements": "requirements-1", "schema": "schema-current", "query": "query-current"},
            artifacts={**project["artifacts"], "schema": current_schema, "query": query},
            compilation={"id": "compilation-current", "result": compiled},
        )
        project["latest_run"].update(compilation_id="compilation-original", profile_version=7)
        self.original_run = deepcopy(project["latest_run"])
        if without_current_schema:
            project.update(stage="SCHEMA", compilation=None)
            project["artifact_ids"].update(schema=None, query=None)
            project["artifacts"].update(schema=None, query=None)
        self.restore_requests = []
        self.context_requests = []
        self.context_error = None
        self.hold_context = False
        self.pending_context = []
        for snapshot in self.source_snapshots.values():
            snapshot["requirements"] = deepcopy(project["artifacts"]["requirements"])
            snapshot["requirements"]["questions"] = [
                {"text": "Сохранённое уточнение " + str(i), "answer": "я" * 1800 + (" Последний согласованный ответ" if i == 14 else ""), "status": "ANSWERED"} for i in range(15)
            ]
        self.context_artifacts = {}
        self.restored = {}
        self.restore_failure = None
        self.lose_response_once = False
        self.hold_restore = False
        self.pending_restore = []

    def record(self, query_id, compilation_id, revision, parameters, author, schema_id, origin=None):
        return {
            "id": query_id,
            "compilation_id": compilation_id,
            "revision": revision,
            "sql": self.sql,
            "parameters": deepcopy(parameters),
            "blocks": query_blocks(self.sql),
            "accepted_by": author,
            "created_at": 1792440000000 + revision * 1000,
            "source_artifact_ids": ["requirements-1", schema_id],
            "source_proposal_id": f"proposal-{revision}",
            "matches_current_context": True,
            "restored_from": origin,
        }

    def __call__(self, route):
        path = urlparse(route.request.url).path.rstrip("/")
        project = self.projects[self.primary]
        prefix = f"/api/v1/business-documents/sql-query/projects/{self.primary}"
        if path == prefix + "/query-history/context":
            command = deepcopy(route.request.post_data_json)
            assert set(command) in ({"schema_version", "compilation_id", "query_artifact_id"}, {"schema_version", "artifact_id"})
            self.context_requests.append(command)
            if self.context_error:
                _fail_connection(route, *self.context_error)
                return
            if "artifact_id" in command:
                response = deepcopy(self.context_artifacts[command["artifact_id"]])
                _fulfill_json(route, _envelope(response))
                return
            record = next(item for item in self.records if item["compilation_id"] == command["compilation_id"])
            assert record["id"] == command["query_artifact_id"]
            snapshot = self.source_snapshots[command["compilation_id"]]
            metadata = {"revision": 1, "accepted_by": "qa-context-author", "created_at": record["created_at"], "content_hash": "sha256:context", "source_proposal_id": "context-proposal"}
            response = {
                "schema_version": "1",
                "project_id": self.primary,
                "source": {
                    **{key: command[key] for key in ("compilation_id", "query_artifact_id")},
                    "requirements_artifact_id": record["source_artifact_ids"][0],
                    "schema_artifact_id": record["source_artifact_ids"][1],
                },
                "catalog_changed": record["matches_current_context"] is False,
                "requirements": {
                    **metadata,
                    "id": record["source_artifact_ids"][0],
                    "text": "\n".join("- " + item["statement"] for item in snapshot["requirements"]["requirements"]),
                    **snapshot["requirements"],
                },
                "schema": {
                    **metadata,
                    "id": record["source_artifact_ids"][1],
                    "accepted_schema": snapshot["schema"]["accepted_schema"],
                    "snapshot": asdict(parse_schema_snapshot(snapshot["schema"]["schema_snapshot"])),
                },
            }
            if self.hold_context:
                self.pending_context.append((route, response))
                return
            _fulfill_json(route, _envelope(response))
            return
        if path == prefix + "/workspace":
            current = next((item for item in self.records if item["id"] == project["artifact_ids"].get("query")), None)
            history = [
                {
                    "id": item["id"],
                    "kind": "QUERY",
                    "revision": item["revision"],
                    "content_hash": "sha256:fixture",
                    "preview": item["sql"],
                    **{key: item[key] for key in ("accepted_by", "created_at", "source_artifact_ids", "source_proposal_id", "restored_from")},
                }
                for item in self.records
            ]
            for identity, packet in self.context_artifacts.items():
                kind = "REQUIREMENTS" if packet["requirements"] is not None else "SCHEMA"
                artifact = packet["requirements"] or packet["schema"]
                history.append(
                    {
                        "id": identity,
                        "kind": kind,
                        "revision": artifact["revision"],
                        "content_hash": artifact["content_hash"],
                        "preview": "Сохранённая версия без SQL",
                        "accepted_by": artifact["accepted_by"],
                        "created_at": artifact["created_at"],
                    }
                )
            _fulfill_json(
                route,
                _envelope(
                    {
                        "project_id": self.primary,
                        "state_version": project["state_version"],
                        "query_artifact_id": project["artifact_ids"].get("query"),
                        "document_artifact_id": None,
                        "sql": current["sql"] if current else "",
                        "blocks": current["blocks"] if current else [],
                        "query_revisions": deepcopy(self.records),
                        "history": history,
                        "draft": None,
                        "threads": [],
                    }
                ),
            )
            return
        if path == prefix + "/preflight":
            profile = self.profiles[0]
            allowed = project["capabilities"]["execute"]
            _fulfill_json(
                route,
                _envelope(
                    {
                        "compilation_id": (project.get("compilation") or {}).get("id"),
                        "state_version": project["state_version"],
                        "binding": {"status": "BOUND" if allowed else "UNAVAILABLE", "selection": {"profile": profile} if allowed else None, "candidates": self.profiles if allowed else []},
                        **({"blocker": {"code": "SQL_EXECUTION_FORBIDDEN", "message": "Выполнение недоступно"}} if not allowed else {}),
                    }
                ),
            )
            return
        if path == prefix + "/query-history/restore":
            command = route.request.post_data_json
            self.restore_requests.append(deepcopy(command))
            assert set(command) == {"schema_version", "compilation_id", "expected_state_version", "idempotency_key"}
            if self.restore_failure:
                _fail_connection(route, *self.restore_failure)
                return
            assert project["capabilities"]["edit"]
            key = command["idempotency_key"]
            if key not in self.restored:
                assert command["expected_state_version"] == project["state_version"]
                selected = next(item for item in self.records if item["compilation_id"] == command["compilation_id"])
                number = max(item["revision"] for item in self.records) + 1
                origin = {
                    "query_artifact_id": selected["id"],
                    "compilation_id": selected["compilation_id"],
                    "requirements_artifact_id": selected["source_artifact_ids"][0],
                    "schema_artifact_id": selected["source_artifact_ids"][1],
                }
                restored = self.record(f"restored-query-{number}", f"restored-compilation-{number}", number, selected["parameters"], "qa-restoring-author", project["artifact_ids"]["schema"], origin)
                self.records.insert(0, restored)
                query = {"mode": "manual", "sql": restored["sql"], "parameters": restored["parameters"], "confirmed_alignment": True, "restored_from": origin}
                compiled = compile_query_payload(build_project_compile_command(project["artifacts"]["requirements"], project["artifacts"]["schema"], query))
                assert compiled["status"] == "READY"
                project["artifact_ids"]["query"] = restored["id"]
                project["artifacts"]["query"] = query
                project.update(compilation={"id": restored["compilation_id"], "result": compiled}, state_version=project["state_version"] + 1)
                self.source_snapshots[restored["compilation_id"]] = {"sql": restored["sql"], "query_id": restored["id"], "schema": deepcopy(project["artifacts"]["schema"])}
                self.restored[key] = (deepcopy(command), {"project": deepcopy(project), "compilation_id": restored["compilation_id"], "compilation": compiled})
            saved, response = self.restored[key]
            assert saved == command
            if self.lose_response_once:
                self.lose_response_once = False
                _fail_connection(route, 503, "SQL_RESTORE_RESPONSE_LOST", "Ответ восстановления потерян")
                return
            if self.hold_restore:
                self.pending_restore.append((route, response))
                return
            _fulfill_json(route, _envelope(response))
            return
        return super().__call__(route)

    def assert_no_run_or_history_overwrite(self):
        assert self.records[-2:] == self.original_records
        assert self.projects[self.primary]["latest_run"] == self.original_run
        assert not [path for method, path in self.requests if method == "POST" and path.endswith("/runs")]


def select_original(page):
    expect(page.locator("summary").filter(has_text=re.compile("^Доработка и обсуждения$"))).to_be_visible()
    _open_sql_panels(page)
    page.get_by_role("button", name="Запрос · версия 1", exact=True).click()


@pytest.mark.parametrize("compact", [False, True], ids=["desktop", "compact"])
def test_sql_history_context_reads_full_saved_answers_and_schema_as_reader(page, base_url, compact):
    stub, _ = _open(page, base_url, stub=HistoryBrowserStub(reader=True), compact=compact)
    select_original(page)
    assert stub.context_requests == []
    page.get_by_role("button", name="Требования и схема запроса", exact=True).click()
    context = page.get_by_role("region", name="Контекст выбранного запроса")
    expect(context.get_by_test_id("sql-history-requirements")).to_be_visible()
    for summary in context.locator("summary").filter(has_text="Технические сведения об источнике").all():
        summary.click()
    expect(context.get_by_text("Версия: schema-original", exact=True)).to_be_visible()
    expect(context.get_by_text("Автор: qa-context-author", exact=True).first).to_be_visible()
    context.get_by_text("Уточнения, ответы и принятые требования", exact=True).click()
    expect(context.get_by_text(re.compile("Последний согласованный ответ"))).to_be_visible()
    schema = context.get_by_test_id("sql-history-schema")
    schema.locator("summary").first.click()
    expect(schema.get_by_role("cell", name="amount", exact=True)).to_be_visible()
    assert context.evaluate("element => element.getBoundingClientRect().width <= window.innerWidth + 1")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    evidence = Path(os.environ["PW_ARTIFACTS_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / ("history-context-compact.png" if compact else "history-context-desktop.png")), full_page=True, animations="disabled")
    assert stub.context_requests == [{"schema_version": "1", "compilation_id": "compilation-original", "query_artifact_id": "query-original"}]
    assert page.get_by_role("button", name="Восстановить как новую версию", exact=True).count() == 0
    stub.assert_no_run_or_history_overwrite()


def test_sql_history_context_revocation_clears_data_before_explicit_retry(page, base_url):
    stub, _ = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    page.get_by_role("button", name="Требования и схема запроса", exact=True).click()
    expect(page.get_by_test_id("sql-history-schema")).to_be_visible()
    stub.context_error = (403, "PERMISSION_DENIED", "Доступ к полной схеме отозван")
    page.get_by_role("button", name="Обновить контекст запроса", exact=True).click()
    expect(page.get_by_test_id("sql-history-schema")).to_have_count(0)
    expect(page.get_by_role("alert").filter(has_text="Доступ к полной схеме отозван")).to_be_visible()
    assert len(stub.context_requests) == 2
    stub.context_error = None
    page.get_by_role("button", name="Обновить контекст запроса", exact=True).click()
    expect(page.get_by_test_id("sql-history-schema")).to_be_visible()
    assert len(stub.context_requests) == 3
    stub.assert_no_run_or_history_overwrite()


def test_sql_history_context_late_response_cannot_show_previous_project(page, base_url):
    stub, _ = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    stub.hold_context = True
    page.get_by_role("button", name="Требования и схема запроса", exact=True).click()
    expect(page.get_by_role("status").filter(has_text="Загружаем требования и схему")).to_be_visible()
    for _ in range(100):
        if stub.pending_context:
            break
        page.wait_for_timeout(20)
    assert len(stub.pending_context) == 1
    page.get_by_role("button", name=re.compile("Второй проект")).click()
    expect(page.get_by_role("heading", name="Второй проект", exact=True)).to_be_visible()
    route, response = stub.pending_context.pop()
    _fulfill_json(route, _envelope(response))
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.get_by_test_id("sql-history-schema")).to_have_count(0)
    expect(page.get_by_role("heading", name="Второй проект", exact=True)).to_be_visible()
    stub.assert_no_run_or_history_overwrite()


def test_sql_history_context_pagehide_discards_data_until_new_read(page, base_url):
    stub, _ = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    page.get_by_role("button", name="Требования и схема запроса", exact=True).click()
    expect(page.get_by_test_id("sql-history-schema")).to_be_visible()
    page.evaluate("() => { window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true})); window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true})); }")
    expect(page.get_by_test_id("sql-history-schema")).to_have_count(0)
    select_original(page)
    page.get_by_role("button", name="Требования и схема запроса", exact=True).click()
    expect(page.get_by_test_id("sql-history-schema")).to_be_visible()
    assert len(stub.context_requests) == 2
    stub.assert_no_run_or_history_overwrite()


def test_sql_session_pagehide_removes_all_private_dom_before_bfcache(page, base_url):
    stub, _ = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    page.get_by_role("button", name="Требования и схема запроса", exact=True).click()
    expect(page.get_by_test_id("sql-history-schema")).to_be_visible()
    assert (
        page.evaluate("""() => {
        window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}));
        return document.querySelectorAll('[data-testid="sql-agent-workbench"], [data-testid="sql-schema-graph"], [data-testid="sql-history-schema"]').length;
    }""")
        == 0
    )
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    expect(page.locator("summary").filter(has_text=re.compile("^Сущности и связи$"))).to_be_visible()
    _open_sql_panels(page)
    expect(page.get_by_test_id("sql-schema-graph")).to_be_visible()
    expect(page.get_by_test_id("sql-history-schema")).to_have_count(0)
    page.route("**/__sql-qa-away", lambda route: route.fulfill(content_type="text/html", body="<!doctype html><title>SQL QA navigation</title>"))
    page.goto(f"{base_url.rstrip('/')}/__sql-qa-away", wait_until="commit")
    page.go_back(wait_until="commit")
    expect(page.get_by_test_id("sql-agent-workbench")).to_be_visible(timeout=30000)
    expect(page.locator("summary").filter(has_text=re.compile("^Сущности и связи$"))).to_be_visible()
    _open_sql_panels(page)
    expect(page.get_by_test_id("sql-schema-graph")).to_be_visible()
    expect(page.get_by_test_id("sql-history-schema")).to_have_count(0)
    stub.assert_no_run_or_history_overwrite()


@pytest.mark.parametrize("change", ["logout", "account-switch"])
def test_sql_session_cross_tab_change_drops_private_state(page, base_url, change):
    stub, _ = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    page.get_by_role("button", name="Требования и схема запроса", exact=True).click()
    expect(page.get_by_test_id("sql-history-schema")).to_be_visible()
    if change == "account-switch":
        stub.hold_context = True
        with page.expect_request("**/query-history/context"):
            page.get_by_role("button", name="Обновить контекст запроса", exact=True).click()
        for _ in range(100):
            if stub.pending_context:
                break
            page.wait_for_timeout(20)
        assert len(stub.pending_context) == 1
    denied = []

    def reject_previous_account(route):
        denied.append(route.request.url)
        _fail_connection(route, 403, "SQL_PROJECT_NOT_FOUND", "Проект прежнего пользователя недоступен")

    page.route("**/sql-query/projects**", reject_previous_account)
    other = page.context.new_page()
    other.route("**/api/v1/**", stub)
    try:
        other.goto(f"{base_url.rstrip('/')}/login", wait_until="commit")
        other.evaluate(
            """change => {
            if (change === 'logout') {
                ['Authorization', 'token', 'userInfo'].forEach(key => localStorage.removeItem(key));
            } else {
                localStorage.setItem('Authorization', 'Bearer another-synthetic-account');
                localStorage.setItem('token', 'another-synthetic-account');
                localStorage.setItem('userInfo', JSON.stringify({id:'another-synthetic-owner'}));
            }
        }""",
            change,
        )
        expect(page.get_by_test_id("sql-history-schema")).to_have_count(0)
        expect(page.get_by_test_id("sql-schema-graph")).to_have_count(0)
        expect(page.get_by_test_id("sql-revision-workspace")).to_have_count(0)
        if stub.pending_context:
            route, response = stub.pending_context.pop()
            _fulfill_json(route, _envelope(response))
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect(page.get_by_test_id("sql-history-schema")).to_have_count(0)
        # A restored page must not recover any object from the old SQL subtree.
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
        expect(page.get_by_test_id("sql-history-schema")).to_have_count(0)
        expect(page.get_by_test_id("sql-schema-graph")).to_have_count(0)
        if change == "account-switch":
            expect(page.get_by_role("alert").filter(has_text="Проект прежнего пользователя недоступен")).to_be_visible()
            assert denied
    finally:
        other.close()
    stub.assert_no_run_or_history_overwrite()


@pytest.mark.parametrize("kind", ["REQUIREMENTS", "SCHEMA"], ids=["requirements", "schema"])
def test_sql_history_accepted_context_without_any_query_is_fully_readable(page, base_url, kind):
    stub = HistoryBrowserStub(reader=True)
    project = stub.projects[stub.primary]
    project.update(stage="QUERY", compilation=None)
    project["artifact_ids"]["query"] = None
    project["artifacts"]["query"] = None
    stub.records = []
    stub.original_records = []
    identity = "uncompiled-context"
    metadata = {"id": identity, "revision": 3, "content_hash": "sha256:uncompiled", "accepted_by": "qa-uncompiled-author", "created_at": 1792440000000}
    schema = stub.source_snapshots["compilation-original"]["schema"]
    packet = {
        "schema_version": "1",
        "project_id": stub.primary,
        "source": {"artifact_id": identity},
        "catalog_changed": False,
        "requirements": {**metadata, "text": "Принятое требование без запроса", "requirements": [{"statement": "Принятое требование без запроса"}], "questions": []}
        if kind == "REQUIREMENTS"
        else None,
        "schema": {**metadata, "accepted_schema": schema["accepted_schema"], "snapshot": asdict(parse_schema_snapshot(schema["schema_snapshot"]))} if kind == "SCHEMA" else None,
    }
    stub.context_artifacts[identity] = packet
    _open(page, base_url, stub=stub)
    title = "Требования" if kind == "REQUIREMENTS" else "Данные"
    page.get_by_text(f"{title} · версия 3", exact=True).click()
    label = "Полные требования" if kind == "REQUIREMENTS" else "Полная схема"
    page.get_by_role("button", name=label, exact=True).click()
    expect(page.get_by_test_id("sql-history-requirements" if kind == "REQUIREMENTS" else "sql-history-schema")).to_be_visible()
    summary = page.get_by_text("Технические сведения об источнике", exact=True)
    summary.click()
    metadata = summary.locator("..")
    expect(metadata.get_by_text("Версия: uncompiled-context", exact=True)).to_be_visible()
    expect(metadata.get_by_text("Автор: qa-uncompiled-author", exact=True)).to_be_visible()
    assert stub.context_requests == [{"schema_version": "1", "artifact_id": identity}]
    stub.assert_no_run_or_history_overwrite()


def test_sql_history_compares_exact_params_author_and_saved_graph(page, base_url):
    stub, graph = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    page.get_by_text("Технические сведения о версии", exact=True).first.click()
    expect(page.get_by_text(re.compile("Автор: qa-original-author")).first).to_be_visible()
    expect(page.locator('time[datetime="2026-10-19T20:00:01.000Z"]').first).to_be_visible()
    expect(page.get_by_text("Источник: requirements-1 · schema-original", exact=True).first).to_be_visible()
    comparison = page.get_by_role("table", name="Различия параметров", exact=True)
    expect(comparison.locator('tr[data-change="CHANGED"]')).to_contain_text("row_limit")
    expect(comparison).to_contain_text("integer · 25")
    expect(comparison).to_contain_text("integer · 100")
    expect(graph.get_by_text("Заказы исходные", exact=True)).to_be_visible()
    assert stub.graph_requests[-1]["source_compilation_id"] == "compilation-original"
    assert stub.restore_requests == []
    stub.assert_no_run_or_history_overwrite()


def test_sql_history_restore_is_new_revision_preserves_results_and_reload(page, base_url):
    stub, _ = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    page.get_by_role("button", name="Восстановить как новую версию", exact=True).click()
    expect(page.get_by_role("button", name="Запрос · версия 3", exact=True)).to_be_visible()
    expect(page.get_by_test_id("sql-historical-result")).to_be_visible()
    assert stub.projects[stub.primary]["artifact_ids"]["query"] == "restored-query-3"
    assert len(stub.restore_requests) == 1
    assert stub.projects[stub.primary]["compilation"]["result"]["parameters"]["row_limit"] == 100
    page.reload(wait_until="commit")
    expect(page.get_by_test_id("sql-agent-workbench")).to_be_visible(timeout=30000)
    expect(page.locator("summary").filter(has_text=re.compile("^Доработка и обсуждения$"))).to_be_visible()
    _open_sql_panels(page)
    expect(page.get_by_role("button", name="Запрос · версия 3", exact=True)).to_be_visible()
    page.get_by_text("Технические сведения о версии", exact=True).first.click()
    expect(page.get_by_text("Восстановлено из запроса query-original · проверка compilation-original", exact=True).first).to_be_visible()
    stub.assert_no_run_or_history_overwrite()


@pytest.mark.parametrize(
    "failure",
    [(403, "CATALOG_ACCESS_DENIED", "Доступ к таблице отозван"), (409, "SQL_SCHEMA_STALE", "Подтвердите обновлённую схему"), (422, "SQL_QUERY_BLOCKED", "Поле прежней версии недоступно")],
    ids=["revoked", "schema-stale", "guard-blocked"],
)
def test_sql_history_restore_failure_keeps_selected_version(page, base_url, failure):
    stub, graph = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    stub.restore_failure = failure
    page.get_by_role("button", name="Восстановить как новую версию", exact=True).click()
    expect(page.get_by_role("alert").filter(has_text=failure[2])).to_be_visible()
    expect(graph.get_by_text("Заказы исходные", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Восстановить как новую версию", exact=True)).to_be_enabled()
    assert len(stub.records) == 2 and stub.projects[stub.primary]["state_version"] == 7
    stub.assert_no_run_or_history_overwrite()


def test_sql_history_ambiguous_restore_retry_replays_original_command(page, base_url):
    stub, _ = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    stub.lose_response_once = True
    page.get_by_role("button", name="Восстановить как новую версию", exact=True).click()
    expect(page.get_by_role("alert").filter(has_text="Ответ восстановления потерян")).to_be_visible()
    page.get_by_role("button", name="Восстановить как новую версию", exact=True).click()
    expect(page.get_by_role("button", name="Запрос · версия 3", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Восстановить как новую версию", exact=True)).to_have_count(0)
    assert len(stub.restore_requests) == 2 and stub.restore_requests[0] == stub.restore_requests[1]
    assert len(stub.records) == 3
    stub.assert_no_run_or_history_overwrite()


def test_sql_history_refresh_recovers_committed_restore_without_repeating_command(page, base_url):
    stub, graph = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    stub.lose_response_once = True
    page.get_by_role("button", name="Восстановить как новую версию", exact=True).click()
    expect(page.get_by_role("alert").filter(has_text="Ответ восстановления потерян")).to_be_visible()
    page.get_by_role("button", name="Обновить", exact=True).click()
    expect(page.get_by_role("button", name="Запрос · версия 3", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Восстановить как новую версию", exact=True)).to_have_count(0)
    expect(graph.get_by_text("Заказы актуальные", exact=True)).to_be_visible()
    assert len(stub.restore_requests) == 1 and len(stub.records) == 3
    stub.assert_no_run_or_history_overwrite()


def test_sql_history_late_restore_does_not_replace_another_project(page, base_url):
    stub, _ = _open(page, base_url, stub=HistoryBrowserStub())
    select_original(page)
    stub.hold_restore = True
    page.get_by_role("button", name="Восстановить как новую версию", exact=True).click()
    expect(page.get_by_role("button", name="Восстановить как новую версию", exact=True)).to_be_disabled()
    for _ in range(100):
        if stub.pending_restore:
            break
        page.wait_for_timeout(20)
    assert len(stub.pending_restore) == 1
    page.get_by_role("button", name=re.compile("Второй проект")).click()
    expect(page.get_by_role("heading", name="Второй проект", exact=True)).to_be_visible()
    route, response = stub.pending_restore.pop()
    _fulfill_json(route, _envelope(response))
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.get_by_role("heading", name="Второй проект", exact=True)).to_be_visible()
    stub.assert_no_run_or_history_overwrite()


def test_sql_history_reader_can_inspect_but_cannot_restore(page, base_url):
    stub, graph = _open(page, base_url, stub=HistoryBrowserStub(reader=True))
    select_original(page)
    expect(graph.get_by_text("Заказы исходные", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Восстановить как новую версию", exact=True)).to_have_count(0)
    assert stub.restore_requests == []


def test_sql_history_old_query_is_visible_without_current_schema(page, base_url):
    stub, graph = _open(page, base_url, stub=HistoryBrowserStub(without_current_schema=True))
    select_original(page)
    expect(page.get_by_label("Проверенный SQL")).to_have_value(stub.sql)
    expect(graph.get_by_text("Заказы исходные", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Восстановить как новую версию", exact=True)).to_be_disabled()
    assert stub.restore_requests == []
