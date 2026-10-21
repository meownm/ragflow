"""Explanation and clarification rendering with controlled server responses."""

import re
from copy import deepcopy
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from business_documents.sql_query.revisions import query_blocks
from test.playwright.e2e.test_document_constructor_ui import _envelope, _fulfill_json
from test.playwright.e2e.test_sql_workspace_ui import SqlWorkspaceBrowserStub, _open


class DialogueBrowserStub(SqlWorkspaceBrowserStub):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.threads = []
        self.pending_answer = None
        self.completion_polls = 0

    def __call__(self, route):
        request = route.request
        path = urlparse(request.url).path.rstrip("/")
        if re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+", path) and request.method == "GET":
            self.requests.append((request.method, path))
            if self.pending_answer:
                self.completion_polls += 1
                if self.completion_polls >= 3:
                    self.threads[0]["messages"].append(self.pending_answer)
                    self.threads[0]["version"] += 1
                    self.pending_answer = None
                    self.sql_agent_project.update(operation_state="IDLE", current_job=None)
            _fulfill_json(route, _envelope(self.sql_agent_project))
            return
        if path.endswith("/workspace"):
            self.requests.append((request.method, path))
            _fulfill_json(route, _envelope({"project_id": self.sql_agent_project["id"], "query_artifact_id": "query-1", "document_artifact_id": None,
                "sql": self.sql, "blocks": query_blocks(self.sql), "query_revisions": [{"id": "query-1", "revision": 1, "sql": self.sql, "blocks": query_blocks(self.sql)}],
                "draft": None, "threads": deepcopy(self.threads), "history": []}))
            return
        if re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+/threads", path) and request.method == "POST":
            self.requests.append((request.method, path))
            payload = request.post_data_json
            thread = {"id": "thread-1", "target": payload["target"], "base_artifact_id": "query-1", "anchor": payload["anchor"], "anchor_status": "CURRENT", "status": "OPEN", "version": 1,
                "messages": [{"id": "user-1", "role": "user", "text": payload["text"], "created_at": 1}], "proposals": []}
            self.threads.append(thread)
            _fulfill_json(route, _envelope(thread))
            return
        if path.endswith("/threads/thread-1/proposal") and request.method == "POST":
            self.requests.append((request.method, path))
            message = {"id": "assistant-1", "role": "assistant", "response_kind": self.kind, "created_at": 2,
                "text": "Условие суммы сохраняет заказы не меньше заданного порога." if self.kind == "EXPLANATION" else "Укажите календарный год для периода."}
            if self.kind == "CLARIFICATION":
                message["questions"] = [{"id": "year", "text": "Какой год?", "options": ["2026", "2027"]}]
            self.pending_answer = message
            self.sql_agent_project.update(operation_state="RUNNING", current_job={"id": "dialogue-job-1", "kind": "REVISION", "status": "RUNNING"})
            _fulfill_json(route, _envelope(self.sql_agent_project))
            return
        if path.endswith("/threads/thread-1/messages") and request.method == "POST":
            self.requests.append((request.method, path))
            payload = request.post_data_json
            assert payload["expected_thread_version"] == self.threads[0]["version"]
            self.threads[0]["messages"].append({"id": "user-2", "role": "user", "text": payload["text"], "created_at": 3})
            self.threads[0]["version"] += 1
            _fulfill_json(route, _envelope(self.threads[0]))
            return
        return super().__call__(route)


@pytest.mark.parametrize("kind", ["EXPLANATION", "CLARIFICATION"])
def test_sql_dialogue_answer_survives_reload_without_proposal_or_query_mutation(page, base_url, kind):
    stub, _ = _open(page, base_url, stub=DialogueBrowserStub(kind))
    workspace = page.get_by_test_id("sql-revision-workspace")
    workspace.get_by_label("Что изменить?").fill("Объясни условие суммы" if kind == "EXPLANATION" else "Оставь сентябрь, год пока не выбран")
    workspace.get_by_role("button", name="Добавить комментарий", exact=True).click()
    workspace.get_by_role("button", name="Предложить изменение через ИИ", exact=True).click()
    expect(workspace).to_contain_text("ИИ готовит предложение по комментарию")
    answer = workspace.get_by_test_id("sql-dialogue-message").filter(has_text="ИИ ·")
    expect(answer).to_contain_text("Объяснение" if kind == "EXPLANATION" else "Нужно уточнение")
    expect(workspace.get_by_test_id("sql-revision-proposal")).to_have_count(0)
    if kind == "CLARIFICATION":
        writes = [(method, path) for method, path in stub.requests if method == "POST"]
        answer.get_by_role("button", name="2026", exact=True).click()
        reply = workspace.get_by_label("Ответ в обсуждении")
        expect(reply).to_have_value("Какой год?: 2026")
        assert [(method, path) for method, path in stub.requests if method == "POST"] == writes
        reply.fill("2026, границы в UTC")
        workspace.get_by_role("button", name="Ответить", exact=True).click()
        expect(workspace.get_by_test_id("sql-dialogue-message").filter(has_text="2026, границы в UTC")).to_be_visible()
    page.reload(wait_until="commit")
    workspace = page.get_by_test_id("sql-revision-workspace")
    expect(workspace.get_by_test_id("sql-dialogue-message").filter(has_text="ИИ ·")).to_have_count(1, timeout=30000)
    expect(workspace.get_by_test_id("sql-revision-proposal")).to_have_count(0)
    for name, original in stub.query_before.items():
        if name != "state_version":
            assert stub.sql_agent_project.get(name) == original
    assert stub.sql_agent_project["pending_proposal"] is None
    assert stub.sql_agent_project["state_version"] == stub.query_before["state_version"]
    assert stub.completion_polls == 3
    forbidden = [(method, path) for method, path in stub.requests if method in {"POST", "PUT", "PATCH", "DELETE"} and not (
        path.endswith(("/threads", "/threads/thread-1/proposal", "/threads/thread-1/messages", "/sql-query/schema/graph")) or method == "PUT" and path.endswith("/view"))]
    assert forbidden == []
