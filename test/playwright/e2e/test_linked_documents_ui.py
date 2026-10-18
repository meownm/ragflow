"""Source Workbench journeys against synthetic APIs; no live backend or model."""

from copy import deepcopy
import json
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_business_documents_ui import _install_common_routes, _install_session


NOW = "2026-10-18T10:00:00Z"
BASE = "/api/v1/source-workspaces"
DETAIL = f"{BASE}/a/documents/doc"


def _workspace(identifier, role):
    return {
        "id": identifier,
        "title": f"Пространство {identifier.upper()}",
        "role": role,
        "tenant_id": None,
        "dataset_ids": ["dataset"],
        "selected_documents": [],
        "search_queries": [],
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _document(role):
    content = "# Хранение\nСтарый текст: 30 дней.\n"
    return {
        "id": "doc",
        "title": "Контрольный документ",
        "document_type": "technical_specification",
        "change_policy": "working",
        "source_kind": "local",
        "current_revision_id": "rev1",
        "approved_revision_id": None,
        "responsible_id": "user",
        "approver_id": "user",
        "version": 1,
        "provenance_state": "resolved",
        "content": content,
        "sections": [{"id": "section", "text": content}],
        "source_ref": None,
        "dependencies": [],
        "impacts": [],
        "waiting": [],
        "changes": [],
        "jobs": [],
        "revisions": [{"id": "rev1", "number": 1, "parent_id": None, "content_hash": "old", "author_id": "user", "created_at": NOW}],
        "capabilities": {"edit": role in {"owner", "editor"}, "manage": role == "owner", "approve": role in {"owner", "reviewer"}},
    }


def _change(status="ready"):
    dependency = {
        "id": "dep",
        "source_id": "source",
        "target_id": "doc",
        "relation": "requires",
        "required": True,
        "source_scope": [],
        "target_scope": [],
        "refresh_on": "saved",
        "maintenance": "manual",
        "blocks_approval": True,
        "reviewed_revision_id": None,
        "version": 1,
    }
    return {
        "id": "change",
        "base_revision_id": "rev1",
        "document_version": 1,
        "status": status,
        "inputs": [{"dependency_id": "dep", "source_id": "source", "source_revision_id": "source-rev", "title": "Основание", "policy": dependency}],
        "plan": {
            "outcome": "changes_required",
            "summary": "Увеличить срок",
            "operations": [{"op": "replace", "section_id": "section", "text": "# Хранение\nПодтверждённый срок: 90 дней.\n", "source_ids": ["dep"]}],
            "source_results": [{"dependency_id": "dep", "disposition": "changed", "explanation": "Новый срок"}],
        },
        "verification": {"verdict": "PASS", "issues": []},
    }


class LinkedDocumentApi:
    def __init__(self):
        self.workspaces = [_workspace("a", "owner"), _workspace("b", "owner")]
        self.document = _document("owner")
        self.requests = []
        self.held_organization = None
        self.hold_organization = False
        self.held_document = None
        self.hold_next_document = False

    @staticmethod
    def respond(route, value):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"code": 0, "data": value}, ensure_ascii=False))

    def update_content(self, content):
        self.document["version"] += 1
        self.document.update(content=content, sections=[{"id": "section", "text": content}], current_revision_id=f"rev{self.document['version']}")

    def draft(self):
        return {
            "id": "doc",
            "workspace_id": "a",
            "content": self.document["content"],
            "prompt": "Составить ТЗ",
            "mode": "all",
            "source_version": 1,
            "sources": [],
            "version": self.document["version"],
            "created_at": NOW,
            "updated_at": NOW,
        }

    def matching(self, method, path):
        return [request for request in self.requests if request["method"] == method and request["path"] == path]

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
        payload = request.post_data_json if request.post_data else None
        self.requests.append({"method": request.method, "path": path, "body": payload})
        if path == BASE:
            return self.respond(route, self.workspaces)
        if path == f"{BASE}/datasets":
            return self.respond(route, [{"id": "dataset", "name": "База", "tenant_id": "tenant", "embd_id": "model"}])
        parts = path.removeprefix(BASE).strip("/").split("/")
        workspace = next(item for item in self.workspaces if item["id"] == parts[0])
        if len(parts) == 1:
            return self.respond(route, workspace)
        if parts[1] == "organization":
            if request.method == "PUT":
                assert payload == {"tenant_id": "tenant", "expected_version": workspace["version"]}
                if self.hold_organization:
                    self.held_organization = route
                    return
                workspace.update(tenant_id="tenant", version=workspace["version"] + 1)
            return self.respond(route, self.organization(workspace))
        if parts[1] == "members":
            return self.respond(route, [{"user_id": "user", "role": "owner", "name": "QA"}])
        if parts[1] == "drafts":
            if len(parts) == 2:
                return self.respond(route, [self.draft()])
            if request.method == "PUT":
                assert payload["expected_version"] == self.document["version"]
                self.update_content(payload["content"])
            return self.respond(route, self.draft())
        if parts[1] == "documents":
            if len(parts) == 2:
                return self.respond(route, [self.document])
            if len(parts) == 3:
                if request.method == "GET" and self.hold_next_document:
                    self.hold_next_document = False
                    self.held_document = (route, deepcopy(self.document))
                    return
                if request.method == "PUT":
                    assert payload["expected_version"] == self.document["version"]
                    self.update_content(payload["content"])
                return self.respond(route, self.document)
            if parts[-1] == "refresh":
                return self.respond(route, self.document)
            if parts[-1] == "decision":
                assert payload["action"] == "accept"
                self.update_content(self.document["changes"][0]["plan"]["operations"][0]["text"])
                self.document["changes"][0]["status"] = "applied"
                return self.respond(route, self.document)
        raise AssertionError(f"Unhandled synthetic API request: {request.method} {path}")

    @staticmethod
    def organization(workspace):
        return {"tenant_id": workspace["tenant_id"], "version": workspace["version"], "items": [{"id": "tenant", "name": "Организация QA"}], "workspace": workspace}


@pytest.fixture
def linked_api(page):
    _install_session(page)
    _install_common_routes(page)
    api = LinkedDocumentApi()
    page.route("**/api/v1/users/me/models", lambda route: api.respond(route, {"tenant_id": "tenant", "llm_id": "", "asr_id": "", "embd_id": ""}))
    page.route(
        "**/api/v1/business-documents/capabilities",
        lambda route: api.respond(
            route,
            {
                "access_role": "AUTHOR_CREATOR",
                "capabilities": {
                    "read": True,
                    "create": True,
                    "edit_own": True,
                    "edit_all": False,
                    "delete": False,
                    "assign": False,
                },
            },
        ),
    )
    page.route("**/api/v1/source-workspaces**", api.route)
    return api


def _open_workspace(page, base_url):
    page.goto(f"{base_url.rstrip('/')}/source-workspaces")
    page.get_by_role("button", name="Пространство A Статей: 0").click()
    expect(page.get_by_role("heading", name="Пространство A", exact=True)).to_be_visible()


def _open_document(page):
    page.get_by_role("button", name="Контрольный документ", exact=False).click()
    expect(page.get_by_role("heading", name="Контрольный документ", exact=True)).to_be_visible()


def _response(response, method, path):
    return response.request.method == method and urlsplit(response.url).path == path


def test_return_from_article_editor_refreshes_active_document(page, base_url, linked_api):
    _open_workspace(page, base_url)
    _open_document(page)
    page.get_by_role("button", name="Поиск и обработка статей", exact=True).click()
    page.get_by_role("button", name="Открыть черновик", exact=False).click()
    processor = page.locator("section").filter(has=page.get_by_role("heading", name="Обработка выбранных статей", exact=True)).last
    processor.get_by_role("button", name="Редактировать текст", exact=True).click()
    content = "# Хранение\nНовый сохранённый текст: 90 дней."
    page.get_by_role("textbox", name="Редактор результата", exact=True).fill(content)
    with page.expect_response(lambda response: _response(response, "PUT", f"{BASE}/a/drafts/doc")):
        page.get_by_role("button", name="Сохранить правки", exact=True).click()
    expect(page.get_by_role("button", name="Сохранить правки", exact=True)).to_be_enabled()
    assert linked_api.document["content"] == content
    with page.expect_response(lambda response: _response(response, "GET", DETAIL)):
        page.get_by_role("button", name="Документы", exact=True).click()
    expect(page.get_by_role("article", name="Хранение", exact=True)).to_contain_text("Новый сохранённый текст: 90 дней.")
    page.get_by_role("button", name="Редактировать текст", exact=True).click()
    page.get_by_role("textbox", name="Содержание документа", exact=True).fill(content + "\nПроверена новая версия.")
    with page.expect_response(lambda response: _response(response, "PUT", DETAIL)):
        page.get_by_role("button", name="Сохранить новую редакцию", exact=True).click()
    expect(page.get_by_role("article", name="Хранение", exact=True)).to_contain_text("Проверена новая версия.")
    assert linked_api.matching("PUT", DETAIL)[0]["body"]["expected_version"] == 2


def test_late_organization_response_keeps_current_workspace(page, base_url, linked_api):
    linked_api.hold_organization = True
    _open_workspace(page, base_url)
    page.get_by_text("Участники пространства", exact=True).click()
    with page.expect_request(lambda request: request.method == "PUT" and urlsplit(request.url).path == f"{BASE}/a/organization"):
        page.get_by_role("button", name="Назначить организацию", exact=True).click()
    expect(page.get_by_role("button", name="Назначить организацию", exact=True)).to_be_disabled()
    page.get_by_role("button", name="К рабочим пространствам", exact=True).click()
    page.get_by_role("button", name="Пространство B Статей: 0").click()
    expect(page.get_by_role("heading", name="Пространство B", exact=True)).to_be_visible()
    assert linked_api.held_organization is not None
    linked_api.workspaces[0].update(tenant_id="tenant", version=2)
    with page.expect_response(lambda response: _response(response, "PUT", f"{BASE}/a/organization")) as response:
        linked_api.respond(linked_api.held_organization, linked_api.organization(linked_api.workspaces[0]))
    response.value.finished()
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.get_by_role("heading", name="Пространство B", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Пространство A", exact=True)).to_have_count(0)


def test_polling_is_serial_and_old_response_cannot_overwrite_foreground_refresh(page, base_url, linked_api):
    page.clock.install(time="2026-10-18T10:00:00Z")
    linked_api.document["jobs"] = [{"id": "job", "status": "running", "stage": "planning", "result_id": None, "error": None}]
    _open_workspace(page, base_url)
    _open_document(page)
    expect(page.get_by_role("button", name="Проверить источники", exact=True)).to_be_enabled()
    linked_api.hold_next_document = True

    def observe_poll(route):
        linked_api.route(route)
        if linked_api.held_document is not None:
            page.evaluate("console.debug('linked-document-poll-held')")

    page.route("**/api/v1/source-workspaces/a/documents/doc", observe_poll)
    with page.expect_console_message(lambda message: message.text == "linked-document-poll-held"):
        page.clock.pause_at("2026-10-18T10:01:00Z")
    assert linked_api.held_document is not None
    assert len(linked_api.matching("GET", DETAIL)) == 2
    page.clock.run_for(6000)
    assert len(linked_api.matching("GET", DETAIL)) == 2, "Polling must wait for the outstanding GET"
    linked_api.update_content("# Хранение\nСвежая серверная редакция: 90 дней.")
    linked_api.document["jobs"] = []
    with page.expect_response(lambda response: _response(response, "POST", DETAIL + "/refresh")):
        page.get_by_role("button", name="Проверить источники", exact=True).click()
    expect(page.get_by_role("article", name="Хранение", exact=True)).to_contain_text("Свежая серверная редакция: 90 дней.")
    route, old_snapshot = linked_api.held_document
    with page.expect_response(lambda response: _response(response, "GET", DETAIL)) as response:
        linked_api.respond(route, old_snapshot)
    response.value.finished()
    page.clock.run_for(6000)
    expect(page.get_by_role("article", name="Хранение", exact=True)).to_contain_text("Свежая серверная редакция: 90 дней.")
    expect(page.get_by_role("article", name="Хранение", exact=True)).not_to_contain_text("Старый текст")
    assert len(linked_api.matching("GET", DETAIL)) == 2


@pytest.mark.parametrize("role", ["reader", "editor"])
def test_role_controls_and_manual_save_preserve_dirty_guard(page, base_url, linked_api, role):
    linked_api.workspaces = [_workspace("a", role), _workspace("b", role)]
    linked_api.document = _document(role)
    _open_workspace(page, base_url)
    _open_document(page)
    expect(page.get_by_role("button", name="Утвердить редакцию", exact=True)).to_be_disabled()
    edit = page.get_by_role("button", name="Редактировать текст", exact=True)
    if role == "reader":
        expect(edit).to_be_disabled()
        expect(page.get_by_role("button", name="Создать документ", exact=True)).to_be_disabled()
        assert linked_api.matching("PUT", DETAIL) == []
        return
    edit.click()
    content = "# Хранение\nПравка редактора."
    page.get_by_role("textbox", name="Содержание документа", exact=True).fill(content)
    expect(page.get_by_role("button", name="Поиск и обработка статей", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="К рабочим пространствам", exact=True)).to_be_disabled()
    with page.expect_response(lambda response: _response(response, "PUT", DETAIL)):
        page.get_by_role("button", name="Сохранить новую редакцию", exact=True).click()
    expect(page.get_by_role("article", name="Хранение", exact=True)).to_contain_text("Правка редактора.")
    expect(page.get_by_role("button", name="Поиск и обработка статей", exact=True)).to_be_enabled()
    assert linked_api.matching("PUT", DETAIL)[0]["body"] == {"content": content, "expected_version": 1}


def test_proposal_changes_only_apply_after_confirmation(page, base_url, linked_api):
    change = _change()
    linked_api.document["changes"] = [change]
    linked_api.document["dependencies"] = [change["inputs"][0]["policy"]]
    linked_api.document["change_policy"] = "approval"
    _open_workspace(page, base_url)
    _open_document(page)
    expect(page.get_by_role("article", name="Предложение: Хранение", exact=True)).to_contain_text("90 дней")
    assert linked_api.document["version"] == 1
    assert linked_api.matching("POST", DETAIL + "/changes/change/decision") == []
    page.get_by_role("button", name="Изменения", exact=True).click()
    with page.expect_response(lambda response: _response(response, "POST", DETAIL + "/changes/change/decision")):
        page.get_by_role("button", name="Подтвердить и применить", exact=True).click()
    page.get_by_role("button", name="Текст", exact=True).click()
    expect(page.get_by_role("article", name="Хранение", exact=True)).to_contain_text("Подтверждённый срок: 90 дней.")
    assert linked_api.document["version"] == 2
    assert linked_api.matching("POST", DETAIL + "/changes/change/decision")[0]["body"] == {"action": "accept", "comment": ""}


@pytest.mark.parametrize("status", ["blocked", "applied", "rejected", "stale"])
def test_non_current_proposal_does_not_offer_document_preview(page, base_url, linked_api, status):
    linked_api.document["changes"] = [_change("ready" if status == "stale" else status)]
    if status == "stale":
        linked_api.document["version"] = 2
    _open_workspace(page, base_url)
    _open_document(page)
    page.get_by_role("button", name="Изменения", exact=True).click()
    if status in {"applied", "rejected"}:
        page.get_by_role("button", name="Предложены правки", exact=False).click()
    expect(page.get_by_text("Предварительный просмотр доступен только", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Посмотреть правки в документе", exact=True)).to_have_count(0)
    if status == "stale":
        expect(page.get_by_role("button", name="Подтвердить и применить", exact=True)).to_be_disabled()
    assert linked_api.matching("POST", DETAIL + "/changes/change/decision") == []
