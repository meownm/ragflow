"""New research surface on the built SPA; controlled transport, no live model."""

import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse, parse_qs

import pytest
from playwright.sync_api import expect

from business_documents.sql_assistant import default_view, output_columns, present_rows, result_metrics, metric_aggregation_allowed
from business_documents.sql_assistant_query import compile_assistant_query
from business_documents.sql_dialogue import context_identity, metric_matches
from test.playwright.e2e.test_document_constructor_ui import _envelope, _fulfill_json, _install_session
from test.playwright.e2e.test_sql_workspace_ui import SqlWorkspaceBrowserStub


class AssistantBrowserStub(SqlWorkspaceBrowserStub):
    def __init__(self, *, hidden=False, revoked=False, structured=False):
        super().__init__()
        self.hidden, self.revoked = hidden, revoked
        self.commands = []
        self.created = []
        self.columns = output_columns(self.sql, ["order_id", "amount"])
        self.presentation = default_view(self.columns)
        self.view_revision = 0
        project = self.sql_agent_project
        project.update(title="Поисковые запросы bot", runs=[{"id": "run-1", "status": "READY", "row_count": 250, "compilation_id": "compilation-1"}])
        project["capabilities"]["edit"] = True
        self.session = {
            "id": project["id"],
            "revision": 1,
            "mode": "EXPLORATORY",
            "project": project,
            "messages": [{"id": "m1", "text": "Покажи данные bot", "created_at": int(time.time() * 1000), "parent_result_id": None}],
            "selected_result_id": "run-1",
            "draft": None,
            "structured_query": None,
            "definition_status": "NOT_VALIDATED",
            "source_completeness": "UNKNOWN",
        }
        self.definitions = []
        self.query_change = None
        self.command_failure = None
        self.deadline = int((time.time() + 3600) * 1000)
        if structured:
            prefix = "warehouse.analytics.public.orders."
            self.session["structured_query"] = {
                "base_entity_id": "orders",
                "aliases": {"orders": "o"},
                "joins": [],
                "select": [{"id": "total", "kind": "count", "column_id": prefix + "id", "alias": "total", "grain": None}],
                "filters": [
                    {
                        "id": "minimum",
                        "column_id": prefix + "id",
                        "operator": "gt",
                        "parameter_name": "minimum",
                        "parameter_type": "integer",
                        "parameter_value": "0",
                        "description": "Минимальный id",
                        "confirmed": True,
                        "decision": "user",
                    }
                ],
                "order_by": [{"select_item_id": "total", "direction": "DESC"}],
                "row_limit": 100,
            }
            project["capabilities"]["execute"] = False
            self.definitions = [
                {
                    "definition_id": "definition-1",
                    "version": 1,
                    "version_id": "version-1",
                    "content_hash": "sha256:fixture",
                    "title": "Количество заказов",
                    "description": "COUNT непустых id",
                    "approved_by": "qa-admin",
                    "approved_at": int(time.time() * 1000),
                    "freshness": "CURRENT",
                    "checks": ["formula", "grain"],
                    "contract": {
                        "grain": [],
                        "metrics": [{"select_item_id": "total", "unit": "заказы", "null_policy": "EXCLUDE_NULLS_ZERO_IF_EMPTY", "reaggregation": "FORBIDDEN"}],
                        "time_column_id": None,
                        "timezone": "UTC",
                        "mutable_parameters": ["minimum"],
                    },
                }
            ]

    def dialogue_context(self):
        return context_identity("run-1", "query-1", "compilation-1", self.sql, {}, "UTC", self.view_revision)

    def refresh_cards(self):
        self.session["cards"] = []
        if self.session["selected_result_id"]:
            self.session["cards"] = [
                {
                    "kind": "RESULT",
                    "run_id": "run-1",
                    "query_artifact_id": "query-1",
                    "created_at": int(time.time() * 1000),
                    "expires_at": self.deadline,
                    "context": self.dialogue_context(),
                    "parameters": {},
                },
                {"kind": "SOURCE", "entity_id": "orders", "title": "warehouse.analytics.public.orders"},
            ]
        if self.query_change:
            self.session["cards"].append(self.query_change)

    def __call__(self, route):
        request = route.request
        path = urlparse(request.url).path.rstrip("/")
        if path == "/api/v1/system/config":
            _fulfill_json(route, _envelope({"registerEnabled": 0, "visibleSections": ["home"] if self.hidden else ["home", "sql_assistant"], "accessibleSections": ["home", "sql_assistant"]}))
            return
        if path == "/api/v1/sql-assistant/sessions":
            if request.method == "POST":
                self.created.append(request.post_data_json)
                _fulfill_json(route, _envelope(self.session))
            else:
                _fulfill_json(
                    route, _envelope({"sessions": [{"id": self.session["id"], "title": self.session["project"]["title"], "revision": self.session["revision"], "updated_at": int(time.time() * 1000)}]})
                )
            return
        if path == "/api/v1/sql-assistant/catalog":
            _fulfill_json(route, _envelope({"entities": self.tables, "warnings": [], "freshness": {"stale": False, "snapshot_at": "2026-10-28T12:00:00Z"}}))
            return
        if path.startswith("/api/v1/sql-assistant/sessions/"):
            self.refresh_cards()
            if path.endswith("/dialogue-options"):
                question = parse_qs(urlparse(request.url).query).get("q", [""])[0]
                metrics = metric_matches(question, self.definitions)
                refs = [{"kind": "TABLE", "id": entity["id"], "title": entity["fqn"], "fqn": entity["fqn"]} for entity in self.tables]
                definitions = [
                    {
                        "kind": "DEFINITION",
                        "id": item["definition_id"],
                        "title": item["title"],
                        "version": item["version"],
                        "content_hash": item["content_hash"],
                        "freshness": item["freshness"],
                        "description": item["description"],
                    }
                    for item in self.definitions
                ]
                _fulfill_json(route, _envelope({"references": refs + definitions, "metrics": [item for item in definitions if any(match["definition_id"] == item["id"] for match in metrics)]}))
                return
            if path.endswith("/definitions"):
                _fulfill_json(route, _envelope({"definitions": self.definitions, "can_publish": False}))
                return
            if path.endswith("/commands"):
                data = request.post_data_json
                self.commands.append(data)
                if self.command_failure:
                    route.fulfill(status=self.command_failure["status"], content_type="application/json", body=json.dumps(self.command_failure["body"]))
                    return
                assert data["expected_revision"] == self.session["revision"]
                kind, payload = data["type"], data["payload"]
                if kind == "select_result":
                    self.session["selected_result_id"] = payload["run_id"]
                elif kind == "message":
                    assert payload["run_id"] == self.session["selected_result_id"]
                    assert payload["context"] == self.dialogue_context()
                    if payload["text"] == "Теперь по месяцам":
                        self.session["messages"].append({"id": "change-request", "role": "user", "text": payload["text"], "created_at": int(time.time() * 1000), "parent_result_id": payload["run_id"]})
                        self.query_change = {
                            "kind": "QUERY_CHANGE",
                            "proposal_id": "proposal-1",
                            "before_sql": self.sql,
                            "after_sql": "SELECT month, COUNT(*) FROM orders GROUP BY month",
                            "summary": "Группировка по месяцам",
                        }
                        self.session["conversation"] = {
                            "phase": "WAITING",
                            "pending": {"kind": "REVISION_REVIEW"},
                            "question": {"text": "Применить расчёт?", "options": ["Применить правку", "Оставить прежний расчёт"]},
                        }
                    elif payload["text"] == "Количество заказов по месяцам":
                        definition = self.definitions[0]
                        self.session["conversation"] = {
                            "phase": "WAITING",
                            "pending": {"kind": "METRIC", "candidates": [{"id": definition["definition_id"], "version": definition["version"], "content_hash": definition["content_hash"]}]},
                            "question": {"text": "Какое определение использовать?", "options": [{"id": definition["definition_id"], "label": definition["title"]}]},
                        }
                    elif payload["text"] == "Количество заказов":
                        assert payload["references"] == [{"kind": "DEFINITION", "id": "definition-1", "version": 1, "content_hash": "sha256:fixture"}]
                        self.session["conversation"] = {"phase": "READY"}
                    else:
                        self.session["messages"].append(
                            {
                                "id": f"answer-{len(self.commands)}",
                                "role": "assistant",
                                "text": payload["text"],
                                "created_at": int(time.time() * 1000),
                                "parent_result_id": payload["run_id"],
                                "answer": {
                                    "status": "READY",
                                    "kind": "ANSWER",
                                    "text": "В первой строке order_id: 1, amount: 499.",
                                    "scope": "LIMITED",
                                    "expires_at": int((time.time() + 3600) * 1000),
                                    "citations": [{"row_index": 0, "column_id": self.columns[0]["id"], "column": "order_id", "value": "1"}],
                                },
                            }
                        )
                elif kind == "update_view":
                    assert payload["expected_view_revision"] == self.view_revision
                    self.presentation = payload["view"]
                    self.view_revision += 1
                elif kind == "save_draft":
                    query = payload["query"]
                    if query.get("mode") == "manual":
                        compiled = {"sql": query["sql"], "parameters": {}, "blocking_issues": [], "status": "READY"}
                    else:
                        _, compiled = compile_assistant_query(self.sql_agent_project["artifacts"]["requirements"], self.sql_agent_project["artifacts"]["schema"], query)
                    self.session["draft"] = {
                        "base_query_artifact_id": payload["base_query_artifact_id"],
                        "query": payload["query"],
                        "compiled": compiled,
                        "revision": (self.session.get("draft") or {}).get("revision", 0) + 1,
                    }
                elif kind == "use_definition":
                    query = {**self.session["structured_query"], "timezone": "UTC"}
                    _, compiled = compile_assistant_query(self.sql_agent_project["artifacts"]["requirements"], self.sql_agent_project["artifacts"]["schema"], query)
                    definition = self.definitions[0]
                    self.session["draft"] = {
                        "base_query_artifact_id": self.sql_agent_project["artifact_ids"]["query"],
                        "query": query,
                        "compiled": compiled,
                        "revision": 1,
                        "mode": "AGREED",
                        "definition": definition,
                        "definition_ref": {key: definition[key] for key in ("definition_id", "version", "version_id", "content_hash")},
                    }
                elif kind == "apply_draft":
                    assert payload["execute"] is False
                    draft = self.session["draft"]
                    self.session.update(
                        mode=draft.get("mode", "EXPLORATORY"),
                        definition_status="DEFINITION_VALIDATED" if draft.get("mode") == "AGREED" else "NOT_VALIDATED",
                        definition_freshness="CURRENT",
                        definition=draft.get("definition"),
                        structured_query=draft["query"],
                        draft=None,
                    )
                    self.sql_agent_project["artifact_ids"]["query"] = "query-2"
                    self.sql_agent_project["compilation"]["result"] = draft["compiled"]
                elif kind == "start_exploration":
                    query = self.session["structured_query"]
                    _, compiled = compile_assistant_query(self.sql_agent_project["artifacts"]["requirements"], self.sql_agent_project["artifacts"]["schema"], query)
                    self.session["draft"] = {"base_query_artifact_id": self.sql_agent_project["artifact_ids"]["query"], "query": query, "compiled": compiled, "revision": 1, "mode": "EXPLORATORY"}
                else:
                    raise AssertionError("Unexpected research mutation: " + kind)
                self.session["revision"] += 1
                self.refresh_cards()
                _fulfill_json(route, _envelope({"session": self.session, "effect": {"kind": kind}}))
            elif "/results/" in path:
                if self.revoked:
                    route.fulfill(status=403, content_type="application/json", body=json.dumps({"code": 403, "message": "Доступ отозван"}))
                    return
                raw_rows = [[i, 500 - i] for i in range(1, 251)]
                values = present_rows(raw_rows, self.columns, self.presentation)
                _fulfill_json(
                    route,
                    _envelope(
                        {
                            "run_id": "run-1",
                            "compilation_id": "compilation-1",
                            "query_artifact_id": "query-1",
                            "sql": self.sql,
                            "sql_hash": "sha256:frozen",
                            "parameters": {},
                            "execution_timezone": "UTC",
                            "dialogue_context": self.dialogue_context(),
                            "columns": self.columns,
                            "rows": [{"index": i, "values": row} for i, row in values[:100]],
                            "offset": 0,
                            "filtered_count": len(values),
                            "metrics": result_metrics(raw_rows, self.columns, self.presentation, self.sql),
                            "metric_aggregation_allowed": metric_aggregation_allowed(self.sql),
                            "row_count": 250,
                            "duration_ms": 10,
                            "created_at": int(time.time() * 1000),
                            "expires_at": int((time.time() + 3600) * 1000),
                            "checks": {"status": "PASS", "completeness": "LIMITED", "truncated": True},
                            "history": {"requirements": {"text": "Исходные условия bot"}, "schema": {"snapshot": {"tables": []}}},
                            "view": self.presentation,
                            "view_revision": self.view_revision,
                            "execution_status": "READY",
                            "output_completeness": "LIMITED",
                            "source_completeness": "UNKNOWN",
                            "definition_status": "NOT_VALIDATED",
                            "catalog_changed": False,
                        }
                    ),
                )
            elif path.endswith("/preflight"):
                _fulfill_json(
                    route,
                    _envelope(
                        {
                            "compilation_id": "compilation-1",
                            "state_version": 1,
                            "binding": {"status": "UNAVAILABLE"},
                            "blocker": {"code": "QA_NO_EXECUTION", "message": "Контролируемая browser-проверка не исполняет SQL"},
                        }
                    ),
                )
            else:
                _fulfill_json(route, _envelope(self.session))
            return
        return super().__call__(route)


def open_assistant(page, base_url, stub, *, session=True):
    _install_session(page)
    page.add_init_script("localStorage.setItem('ragflow-ui-theme','light')")
    page.route("**/api/v1/**", stub)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(base_url.rstrip("/") + "/sql-assistant" + ("?session=" + stub.session["id"] if session else ""), wait_until="domcontentloaded")
    expect(page.get_by_test_id("sql-assistant")).to_be_visible(timeout=120000)


def screenshot(page, name):
    root = Path(os.environ["PW_ARTIFACTS_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(root / (name + ".png")), full_page=True)


@pytest.mark.p0
def test_assistant_is_separate_hidden_by_default_and_can_start_question(page, base_url):
    stub = AssistantBrowserStub(hidden=True)
    open_assistant(page, base_url, stub, session=False)
    expect(page.get_by_test_id("nav-sql-assistant")).to_have_count(0)
    expect(page.get_by_role("heading", name="Что хотите узнать о данных?")).to_be_visible()
    screenshot(page, "welcome-light-1440")
    page.get_by_label("Вопрос к данным").fill("Сколько поисковых запросов в bot за август 2026?")
    page.get_by_role("button", name="Отправить", exact=True).click()
    expect(page.get_by_role("heading", name="Поисковые запросы bot", exact=True)).to_be_visible()
    assert len(stub.created) == 1
    assert stub.created[0]["question"] == "Сколько поисковых запросов в bot за август 2026?"


@pytest.mark.p0
def test_result_answer_restores_and_query_change_is_an_explicit_path(page, base_url):
    stub = AssistantBrowserStub()
    open_assistant(page, base_url, stub)
    page.get_by_label("Сообщение SQL-помощнику", exact=True).fill("Кто первый и какое значение?")
    page.get_by_role("button", name="Отправить сообщение", exact=True).click()
    expect(page.get_by_text("В первой строке order_id: 1, amount: 499.", exact=True)).to_be_visible()
    page.get_by_text("Основания ответа", exact=True).click()
    expect(page.get_by_text("order_id, строка 1: 1", exact=True)).to_be_visible()
    assert [item["type"] for item in stub.commands] == ["message"]
    assert stub.sql_agent_project["compilation"] == stub.query_before["compilation"]
    screenshot(page, "result-answer-light-1440")
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_text("В первой строке order_id: 1, amount: 499.", exact=True)).to_be_visible()
    page.get_by_label("Сообщение SQL-помощнику", exact=True).fill("Теперь по месяцам")
    assert len(stub.commands) == 1
    page.get_by_role("button", name="Отправить сообщение", exact=True).click()
    expect(page.get_by_role("log").get_by_text("Теперь по месяцам", exact=True)).to_be_visible()
    page.get_by_text("Изменения SQL и параметров", exact=True).click()
    expect(page.get_by_role("log")).to_contain_text("GROUP BY month")
    expect(page.get_by_role("button", name="Применить правку", exact=True)).to_be_enabled()
    assert stub.sql_agent_project["compilation"] == stub.query_before["compilation"]
    assert [item["type"] for item in stub.commands] == ["message", "message"]
    page.evaluate("document.documentElement.classList.add('dark')")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Отправить сообщение", exact=True).scroll_into_view_if_needed()
    screenshot(page, "result-answer-dark-390")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.p0
def test_assistant_preserves_draft_views_and_immutable_result_across_surfaces(page, base_url):
    stub = AssistantBrowserStub()
    open_assistant(page, base_url, stub)
    result = page.get_by_test_id("sql-assistant-result")
    expect(result.get_by_role("table")).to_be_visible()
    result.get_by_role("button", name="Настроить результат", exact=True).click()
    result.get_by_label("Показывать amount", exact=True).click()
    expect(result.get_by_label("Показывать amount", exact=True)).not_to_be_checked()
    result.get_by_role("button", name="Сохранить представление", exact=True).click()
    expect(result.get_by_role("table").get_by_role("columnheader", name="amount", exact=True)).to_have_count(0)
    result.get_by_role("button", name="order_id", exact=True).click()
    result.get_by_role("button", name="order_id ↑", exact=True).click()
    expect(result.get_by_role("table").locator("tbody tr").first.locator("td").first).to_have_text("250")
    page.get_by_text("Изменить расчёт", exact=True).click()
    page.get_by_label("Исследовательский SQL", exact=True).fill("SELECT o.id AS order_id FROM public.orders o LIMIT :row_limit")
    page.get_by_text("Изменить расчёт", exact=True).click()
    page.get_by_text("Изменить расчёт", exact=True).click()
    expect(page.get_by_label("Исследовательский SQL", exact=True)).to_have_value("SELECT o.id AS order_id FROM public.orders o LIMIT :row_limit")
    page.get_by_role("button", name="Проверить и сохранить черновик", exact=True).click()
    expect(page.get_by_text("Черновик сохранён на сервере", exact=True)).to_be_visible()
    page.reload(wait_until="domcontentloaded")
    page.get_by_text("Изменить расчёт", exact=True).click()
    expect(page.get_by_label("Исследовательский SQL", exact=True)).to_have_value("SELECT o.id AS order_id FROM public.orders o LIMIT :row_limit")
    assert all(item["type"] in {"update_view", "save_draft"} for item in stub.commands)
    assert stub.sql_agent_project["compilation"] == stub.query_before["compilation"]
    screenshot(page, "workspace-sql-light-1440")
    page.evaluate("document.documentElement.classList.add('dark')")
    screenshot(page, "workspace-sql-dark-1440")
    page.set_viewport_size({"width": 390, "height": 844})
    screenshot(page, "workspace-sql-dark-390")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.p0
def test_revoked_result_is_not_displayed_as_empty_data_or_retried(page, base_url):
    stub = AssistantBrowserStub(revoked=True)
    open_assistant(page, base_url, stub)
    expect(page.get_by_text("Результат недоступен:", exact=False)).to_be_visible()
    expect(page.get_by_role("table")).to_have_count(0)
    assert stub.commands == []


@pytest.mark.p0
def test_sql_grouping_and_having_survive_draft_save_without_execution(page, base_url):
    stub = AssistantBrowserStub(structured=True)
    open_assistant(page, base_url, stub)
    page.get_by_text("Изменить расчёт", exact=True).click()
    sql = "SELECT o.id, COUNT(*) FROM public.orders o WHERE o.id > 5 GROUP BY o.id HAVING COUNT(*) > 10 LIMIT :row_limit"
    page.get_by_label("Исследовательский SQL", exact=True).fill(sql)
    page.get_by_role("button", name="Проверить и сохранить черновик", exact=True).click()
    expect(page.get_by_text("Черновик сохранён на сервере", exact=True)).to_be_visible()
    assert stub.session["draft"]["compiled"]["sql"] == sql
    page.reload(wait_until="domcontentloaded")
    page.get_by_text("Изменить расчёт", exact=True).click()
    expect(page.get_by_label("Исследовательский SQL", exact=True)).to_have_value(sql)
    assert [item["type"] for item in stub.commands] == ["save_draft"]
    assert stub.sql_agent_project["compilation"] == stub.query_before["compilation"]


@pytest.mark.p0
def test_definition_mode_locks_manual_sql_until_explicit_exploration(page, base_url):
    stub = AssistantBrowserStub(structured=True)
    open_assistant(page, base_url, stub)
    page.get_by_text("Изменить расчёт", exact=True).click()
    page.get_by_label("Согласованное определение", exact=True).select_option("definition-1")
    page.get_by_role("button", name="Создать черновик по определению", exact=True).click()
    editor = page.get_by_label("Исследовательский SQL", exact=True)
    expect(editor).to_be_disabled()
    page.get_by_role("button", name="Применить проверенный запрос", exact=True).click()
    expect(page.get_by_text("По согласованному определению", exact=True)).to_be_visible()
    expect(editor).to_be_disabled()
    page.get_by_role("button", name="Перейти к исследовательской правке", exact=True).click()
    expect(editor).to_be_enabled()
    assert [item["type"] for item in stub.commands] == ["use_definition", "apply_draft", "start_exploration"]


@pytest.mark.p0
def test_dialogue_context_mentions_and_saved_sql_evidence(page, base_url):
    stub = AssistantBrowserStub(structured=True)
    open_assistant(page, base_url, stub)
    question = page.get_by_label("Сообщение SQL-помощнику", exact=True)
    question.fill("Покажи @orders")
    page.get_by_role("listbox", name="Доступные объекты").get_by_role("button", name="Таблица", exact=False).first.click()
    expect(page.get_by_label("Выбранные упоминания")).to_be_visible()
    page.get_by_role("button", name="Отправить сообщение", exact=True).click()
    expect(page.get_by_text("В первой строке order_id: 1, amount: 499.", exact=True)).to_be_visible()
    answer = stub.commands[0]["payload"]
    assert answer["context"] == stub.dialogue_context()
    assert answer["references"][0]["kind"] == "TABLE"
    assert "title" not in answer["references"][0]
    page.get_by_text("SQL и основания", exact=True).click()
    expect(page.get_by_label("SQL выбранного результата", exact=True)).to_contain_text(stub.sql)
    page.get_by_text("Изменить расчёт", exact=True).click()
    definition = page.get_by_label("Согласованное определение", exact=True)
    expect(definition).to_contain_text("Количество заказов")
    page.set_viewport_size({"width": 390, "height": 844})
    question.fill("Количество заказов по месяцам")
    page.get_by_role("button", name="Отправить сообщение", exact=True).click()
    page.get_by_role("button", name="Количество заказов", exact=True).click()
    expect(page.get_by_role("button", name="Количество заказов", exact=True)).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert [item["type"] for item in stub.commands] == ["message", "message", "message"]


@pytest.mark.p0
@pytest.mark.parametrize("status", [403, 409, 503])
def test_dialogue_error_preserves_input_and_never_automatically_retries(page, base_url, status):
    stub = AssistantBrowserStub()
    open_assistant(page, base_url, stub)
    stub.command_failure = {"status": status, "body": {"code": status, "message": "private diagnostic", "data": {"error_code": "SQL_RESULT_ANSWER_UNAVAILABLE"}}}
    question = page.get_by_label("Сообщение SQL-помощнику", exact=True)
    question.fill("Кто первый?")
    page.get_by_role("button", name="Отправить сообщение", exact=True).click()
    expect(page.locator(".sa-chat-error")).to_be_visible()
    expect(page.locator(".sa-chat-error")).not_to_contain_text("private")
    expect(question).to_have_value("Кто первый?")
    assert [item["type"] for item in stub.commands] == ["message"]


@pytest.mark.p0
def test_initial_question_uses_catalog_mention_ids(page, base_url):
    stub = AssistantBrowserStub()
    open_assistant(page, base_url, stub, session=False)
    page.get_by_label("Вопрос к данным", exact=True).fill("Исследуй @orders")
    page.get_by_role("listbox", name="Доступные объекты").get_by_role("button", name="Таблица", exact=False).first.click()
    page.get_by_role("button", name="Отправить", exact=True).click()
    expect(page.get_by_role("heading", name="Поисковые запросы bot", exact=True)).to_be_visible()
    assert stub.created[0]["references"][0]["kind"] == "TABLE"
    assert "title" not in stub.created[0]["references"][0]


@pytest.mark.p0
def test_result_builder_persists_fields_filters_metrics_and_appearance(page, base_url):
    stub = AssistantBrowserStub()
    open_assistant(page, base_url, stub)
    builder = page.get_by_label("Конструктор результата", exact=True)
    builder.get_by_role("button", name="Настроить результат", exact=True).click()
    builder.get_by_label("Название amount", exact=True).fill("Сумма")
    builder.get_by_role("button", name="Раньше Сумма", exact=True).click()
    builder.get_by_role("button", name="Фильтры и сортировка", exact=True).click()
    builder.get_by_role("button", name="Добавить фильтр", exact=True).click()
    builder.get_by_label("Условие фильтра 1", exact=True).select_option("gte")
    builder.get_by_label("Значение фильтра 1", exact=True).fill("201")
    builder.get_by_role("button", name="Внешний вид", exact=True).click()
    builder.get_by_label("Плотность таблицы", exact=True).select_option("compact")
    builder.get_by_label("Чередование строк", exact=True).check()
    builder.get_by_label("Перенос текста в ячейках", exact=True).check()
    builder.get_by_role("button", name="Метрики", exact=True).click()
    builder.get_by_role("button", name="Добавить метрику", exact=True).click()
    builder.get_by_label("Название метрики 1", exact=True).fill("Строки после фильтра")
    builder.get_by_role("button", name="Добавить метрику", exact=True).click()
    builder.get_by_label("Название метрики 2", exact=True).fill("Сумма сохранённого набора")
    builder.get_by_label("Тип метрики 2", exact=True).select_option("sum")
    builder.get_by_label("Поле метрики 2", exact=True).select_option(stub.columns[1]["id"])
    assert stub.commands == []
    builder.get_by_role("button", name="Сохранить представление", exact=True).click()
    result = page.get_by_test_id("sql-assistant-result")
    expect(result.get_by_role("table", name="Данные выбранного результата").locator("thead th").first).to_have_text("Сумма")
    assert stub.view_revision == 1
    metrics = page.get_by_label("Показатели сохранённого набора", exact=True)
    expect(metrics).to_contain_text("50 строк")
    expect(metrics.locator("article").first).to_contain_text("50")
    expect(metrics.locator("article").nth(1)).to_contain_text("13725")
    screenshot(page, "result-builder-metrics-light-1440")
    page.reload(wait_until="domcontentloaded")
    builder = page.get_by_label("Конструктор результата", exact=True)
    builder.get_by_role("button", name="Настроить результат", exact=True).click()
    expect(builder.get_by_label("Название amount", exact=True)).to_have_value("Сумма")
    builder.get_by_role("button", name="Внешний вид", exact=True).click()
    expect(builder.get_by_label("Плотность таблицы", exact=True)).to_have_value("compact")
    builder.get_by_label("Отображение результата", exact=True).select_option("both")
    builder.get_by_label("Тип графика", exact=True).select_option("line")
    builder.get_by_label("Ось X", exact=True).select_option(stub.columns[0]["id"])
    builder.get_by_label("Ось Y", exact=True).select_option(stub.columns[1]["id"])
    builder.get_by_role("button", name="Сохранить представление", exact=True).click()
    expect(page.get_by_text("График страницы · без новой агрегации", exact=True)).to_be_visible()
    page.evaluate("document.documentElement.classList.add('dark')")
    page.set_viewport_size({"width": 390, "height": 844})
    builder.scroll_into_view_if_needed()
    screenshot(page, "result-builder-appearance-dark-390")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert all(item["type"] in {"update_view"} for item in stub.commands)
    assert stub.sql_agent_project["compilation"] == stub.query_before["compilation"]


@pytest.mark.p0
def test_result_builder_long_names_stay_inside_controls(page, base_url):
    stub = AssistantBrowserStub()
    stub.columns[0]["name"] = "column_" * 9
    stub.presentation["metrics"] = [{"id": "rows", "title": "M" * 120, "kind": "rows", "column_id": None, "row_index": None}]
    open_assistant(page, base_url, stub)
    builder = page.get_by_label("Конструктор результата", exact=True)
    builder.get_by_role("button", name="Настроить результат", exact=True).click()
    for width, height in [(1440, 1000), (390, 844)]:
        page.set_viewport_size({"width": width, "height": height})
        checkbox = builder.get_by_label("Показывать " + stub.columns[0]["name"], exact=True)
        checkbox.scroll_into_view_if_needed()
        assert checkbox.evaluate("input => input.closest('label').scrollWidth <= input.closest('label').clientWidth")
        card = page.get_by_label("Показатели сохранённого набора", exact=True).locator("article").first
        assert card.evaluate("el => el.scrollWidth <= el.clientWidth")
