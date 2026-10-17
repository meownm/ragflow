"""Browser acceptance for the durable SQL comment and revision loop."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_document_constructor_ui import DocumentConstructorStub, _envelope, _fulfill_json, _install_session


COMPLEX_SQL = (
    "SELECT o.order_id AS order_id FROM dwh.order_fact AS o "
    "JOIN dwh.customer_dim AS c ON c.customer_id = o.customer_id "
    "WHERE EXISTS (SELECT i.order_id AS order_id FROM dwh.order_fact AS i "
    "WHERE i.customer_id = o.customer_id AND i.status_id = :completed_status_id) "
    "LIMIT :row_limit"
)


class RevisionBrowserStub(DocumentConstructorStub):
    def __init__(self):
        super().__init__()
        self.sql = COMPLEX_SQL
        self.threads = []
        self.proposals = []
        self.query_number = 1

    def _compile_sql_project(self, payload):
        response = super()._compile_sql_project(payload)
        response["compilation"]["sql"] = self.sql
        response["project"]["compilation"]["result"]["sql"] = self.sql
        return response

    def _complete_sql_project(self, payload):
        result = super()._complete_sql_project(payload)
        project = self.sql_agent_project
        documents = project.setdefault("documents", [])
        revision = len(documents) + 1
        document = {
            "id": f"document-{revision}",
            "revision": revision,
            "payload": {"run_id": project["latest_run"]["id"], "confirmed_conclusion": {"text": "Получено 10 заказов"}, "row_count": 1},
        }
        documents.insert(0, document)
        project["document"] = document
        result.update(document_id=document["id"], revision=revision, document=document["payload"])
        return result

    def _workspace(self):
        project = self.sql_agent_project
        query_id = project["artifact_ids"]["query"] if project else None
        document = project.get("document") if project else None
        blocks = (
            [
                {"id": "join:1:stable", "kind": "JOIN", "title": "Соединение с клиентами", "sql": "JOIN dwh.customer_dim AS c ON c.customer_id = o.customer_id", "fingerprint": "stable"},
                {
                    "id": "subquery:2:stable",
                    "kind": "SUBQUERY",
                    "title": "Вложенная выборка",
                    "sql": "SELECT i.order_id AS order_id FROM dwh.order_fact AS i WHERE i.customer_id = o.customer_id",
                    "fingerprint": "stable",
                },
            ]
            if query_id
            else []
        )
        return {
            "project_id": project["id"] if project else "",
            "state_version": project["state_version"] if project else 0,
            "query_artifact_id": query_id,
            "document_artifact_id": document["id"] if document else None,
            "sql": self.sql if query_id else "",
            "blocks": blocks,
            "query_revisions": [{"id": query_id, "revision": self.query_number, "sql": self.sql, "blocks": blocks}] if query_id else [],
            "draft": None,
            "threads": deepcopy(self.threads),
            "history": [],
        }

    def _request_revision(self, thread_id):
        project = self.sql_agent_project
        thread = next(item for item in self.threads if item["id"] == thread_id)
        thread["version"] += 1
        project["state_version"] += 1
        project["operation_state"] = "REVIEW"
        target = thread["target"]
        if target == "QUERY":
            subquery_edit = thread["anchor"].get("block_id", "").startswith("subquery:")
            candidate = (
                self.sql.replace("i.status_id = :completed_status_id", "i.status_id <> :completed_status_id")
                if subquery_edit
                else self.sql.replace(" AS o JOIN dwh.customer_dim", " AS o LEFT JOIN dwh.customer_dim")
            )
            proposal = {
                "sql": candidate,
                "parameters": [{"name": "completed_status_id", "type": "integer", "value": 9}, {"name": "row_limit", "type": "integer", "value": 1000}],
                "summary": "Изменить проверку статуса" if subquery_edit else "Сохранить заказы без клиента",
                "checks": {"status": "PASS"},
                "affected_blocks": [
                    {"change": "ADDED", "kind": "SUBQUERY" if subquery_edit else "JOIN", "sql": "i.status_id <> :completed_status_id" if subquery_edit else "LEFT JOIN dwh.customer_dim"}
                ],
                "sql_diff": "-i.status_id = :completed_status_id\n+i.status_id <> :completed_status_id" if subquery_edit else "-JOIN dwh.customer_dim\n+LEFT JOIN dwh.customer_dim",
                "parameter_diff": '- "value": 100\n+ "value": 1000',
            }
        else:
            proposal = {"document_text": "Всего получено 10 заказов", "summary": "Уточнить формулировку", "checks": {"status": "PASS"}}
        number = len(self.proposals) + 1
        item = {
            "id": f"revision-proposal-{number}",
            "kind": "REVISION",
            "status": "PENDING",
            "payload": {
                "agent_result": {"proposal": proposal, "source": {"target": target, "thread_id": thread_id, "thread_version": thread["version"], "base_artifact_id": thread["base_artifact_id"]}}
            },
        }
        self.proposals.append(item)
        project["pending_proposal"] = item
        thread["proposals"].append({"id": item["id"], "status": "PENDING", "proposal": proposal, "decided_by": None, "decided_at": None})
        return project

    def _decide_revision(self, payload):
        project = self.sql_agent_project
        item = project["pending_proposal"]
        target = item["payload"]["agent_result"]["source"]["target"]
        decision = payload["decision"]
        item["status"] = "ACCEPTED" if decision == "ACCEPT" else "REJECTED"
        for thread in self.threads:
            for history in thread["proposals"]:
                if history["id"] == item["id"]:
                    history["status"] = item["status"]
        if decision == "ACCEPT" and target == "QUERY":
            self.sql = item["payload"]["agent_result"]["proposal"]["sql"]
            self.query_number += 1
            project["artifact_ids"]["query"] = f"artifact-query-{self.query_number}"
            project["artifacts"]["query"] = {"mode": "manual", "sql": self.sql}
            project["compilation"]["id"] = f"compilation-{self.query_number}"
            project["compilation"]["result"]["sql"] = self.sql
        if decision == "ACCEPT" and target == "DOCUMENT":
            base_id = item["payload"]["agent_result"]["source"]["base_artifact_id"]
            document = deepcopy(next(doc for doc in project["documents"] if doc["id"] == base_id))
            document["revision"] = len(project["documents"]) + 1
            document["id"] = f"document-{document['revision']}"
            document["payload"]["confirmed_conclusion"]["text"] = item["payload"]["agent_result"]["proposal"]["document_text"]
            project["documents"].insert(0, document)
            project["document"] = document
        project["pending_proposal"] = None
        project["operation_state"] = "IDLE"
        project["state_version"] += 1
        return project

    def __call__(self, route):
        request = route.request
        path = urlparse(request.url).path.rstrip("/")
        if re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+/workspace", path) and request.method == "GET":
            _fulfill_json(route, _envelope(self._workspace()))
            return
        if re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+/threads", path) and request.method == "POST":
            payload = json.loads(request.post_data or "{}")
            thread = {
                "id": f"thread-{len(self.threads) + 1}",
                "target": payload["target"],
                "base_artifact_id": payload.get("base_artifact_id", self._workspace()["query_artifact_id"] if payload["target"] == "QUERY" else self._workspace()["document_artifact_id"]),
                "anchor": payload["anchor"],
                "anchor_status": "CURRENT",
                "status": "OPEN",
                "version": 1,
                "messages": [{"id": f"message-{len(self.threads) + 1}", "author_id": "test", "text": payload["text"], "created_at": 1}],
                "proposals": [],
            }
            self.threads.append(thread)
            _fulfill_json(route, _envelope(thread))
            return
        match = re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+/threads/([^/]+)/proposal", path)
        if match and request.method == "POST":
            _fulfill_json(route, _envelope(self._request_revision(match.group(1))))
            return
        if re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+/revision-proposals/[^/]+/decision", path) and request.method == "POST":
            _fulfill_json(route, _envelope(self._decide_revision(json.loads(request.post_data or "{}"))))
            return
        return super().__call__(route)


@pytest.mark.p1
@pytest.mark.auth
def test_sql_revision_comments_survive_saved_document_and_reload(page, base_url):
    stub = RevisionBrowserStub()
    _install_session(page)
    page.route("**/api/v1/**", stub)
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.goto(f"{base_url.rstrip('/')}/sql-projects", wait_until="commit")
    workbench = page.get_by_test_id("sql-agent-workbench")
    workbench.get_by_role("button", name="Новый запрос").first.click()
    workbench.get_by_label("Название (необязательно)").fill("Заказы с клиентами")
    workbench.get_by_label("Какой вопрос нужно решить?").fill("Покажи заказы и клиентов, включая вложенную проверку статуса")
    workbench.get_by_role("button", name="Создать и продолжить").click()
    requirements = workbench.get_by_test_id("sql-agent-requirements-review")
    requirements.get_by_role("button", name="Календарный месяц").click()
    requirements.get_by_role("button", name="Подтвердить требования").click()
    workbench.get_by_label("Что искать в данных?").fill("Заказ")
    workbench.get_by_test_id("sql-agent-run-schema").click()
    schema = workbench.get_by_test_id("sql-agent-schema-review")
    schema.get_by_role("button", name=re.compile("order_fact")).first.click()
    for column in ("order_id", "status_id", "paid_amount_rub"):
        schema.get_by_role("checkbox", name=column).check()
    schema.get_by_role("button", name="Подтвердить схему").click()
    query = workbench.get_by_test_id("sql-agent-query-review")
    query.get_by_role("checkbox", name="Подтверждаю исключение строк этим условием.").check()
    query.get_by_role("button", name="Подтвердить и собрать SQL").click()

    revision = workbench.get_by_test_id("sql-revision-workspace")
    expect(revision.get_by_role("button", name="Соединение с клиентами")).to_be_visible()
    expect(revision.get_by_role("button", name="Вложенная выборка")).to_be_visible()
    revision.get_by_role("button", name="Соединение с клиентами").click()
    revision.get_by_label("Что изменить?").fill("Оставь заказы без клиента")
    revision.get_by_role("button", name="Добавить комментарий").click()
    revision.get_by_role("button", name="Вложенная выборка").click()
    revision.get_by_label("Что изменить?").fill("Уточни проверку статуса внутри выборки")
    revision.get_by_role("button", name="Добавить комментарий").click()
    expect(_comment_thread(revision, "Уточни проверку статуса внутри выборки")).to_be_visible()
    _comment_thread(revision, "Оставь заказы без клиента").get_by_role("button", name="Предложить изменение через ИИ").click()
    proposal = revision.get_by_test_id("sql-revision-proposal")
    expect(proposal).to_contain_text("Сохранить заказы без клиента")
    expect(proposal).to_contain_text("Различия в SQL")
    expect(proposal).to_contain_text("Изменения параметров")
    proposal.get_by_role("button", name="Принять версию").click()
    expect(revision.get_by_label("Проверенный SQL")).to_have_value(re.compile("LEFT JOIN"))

    completed = workbench.get_by_test_id("sql-agent-complete")
    completed.get_by_role("button", name="Выполнить запрос").click()
    expect(completed.get_by_test_id("sql-result-table")).to_contain_text("1250.00")
    completed.get_by_role("button", name="Завершить", exact=True).click()
    expect(completed.get_by_test_id("sql-completed-document")).to_contain_text("Документ · ревизия 1")
    completed.get_by_test_id("sql-completed-document").get_by_role("button", name="Доработать").click()
    expect(page.get_by_test_id("sql-revision-workspace")).to_be_visible()
    page.reload()
    revision = page.get_by_test_id("sql-revision-workspace")
    revision.get_by_role("button", name="Вложенная выборка").click()
    revision.get_by_label("Что изменить?").fill("После сохранения измени проверку внутри подзапроса")
    revision.get_by_role("button", name="Добавить комментарий").click()
    _comment_thread(revision, "После сохранения измени проверку внутри подзапроса").get_by_role("button", name="Предложить изменение через ИИ").click()
    expect(revision.get_by_test_id("sql-revision-proposal")).to_contain_text("Изменить проверку статуса")
    revision.get_by_test_id("sql-revision-proposal").get_by_role("button", name="Принять версию").click()
    completed = page.get_by_test_id("sql-agent-complete")
    completed.get_by_role("button", name="Повторить запрос").click()
    completed.get_by_role("button", name="Выполнить запрос").click()
    expect(completed.get_by_test_id("sql-result-table")).to_contain_text("1250.00")
    completed.get_by_role("button", name="Завершить", exact=True).click()
    expect(completed.get_by_test_id("sql-completed-document")).to_contain_text("Документ · ревизия 2")
    page.reload()
    revision = page.get_by_test_id("sql-revision-workspace")
    revision.get_by_role("button", name="Итоговый документ").click()
    expect(revision).to_contain_text("Получено 10 заказов")
    revision.get_by_label("Что изменить?").fill("Сделай вывод понятнее")
    revision.get_by_role("button", name="Добавить комментарий").click()
    _comment_thread(revision, "Сделай вывод понятнее").get_by_role("button", name="Предложить изменение через ИИ").click()
    expect(revision.get_by_test_id("sql-revision-proposal")).to_contain_text("Уточнить формулировку")
    revision.get_by_role("checkbox", name=re.compile("Я сравнил текст")).check()
    revision.get_by_test_id("sql-revision-proposal").get_by_role("button", name="Принять версию").click()
    expect(revision).to_contain_text("Всего получено 10 заказов")
    revision.get_by_role("button", name="Итоговый документ").click()
    revision.get_by_role("button", name="Документ · версия 1").click()
    expect(revision).to_contain_text("Выбрана прежняя версия")
    revision.get_by_label("Что изменить?").fill("Вернись к первой редакции и уточни формулировку")
    revision.get_by_role("button", name="Добавить комментарий").click()
    expect(_comment_thread(revision, "Вернись к первой редакции и уточни формулировку")).to_be_visible()
    assert stub.threads[-1]["base_artifact_id"] == "document-1"
    assert page._diag["page_errors"] == []
    assert page._diag["console_errors"] == []


def _comment_thread(workspace, text):
    # A textarea may already contain the draft while its POST is still pending.
    message = workspace.locator("p").filter(has_text=re.compile(r"^" + re.escape(text) + r"$"))
    expect(message).to_be_visible()
    return message.locator("..")
