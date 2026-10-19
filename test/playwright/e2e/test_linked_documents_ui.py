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
        "implemented_revision_id": None,
        "implementation_event_id": None,
        "implementation_history": [],
        "can_implement": role in {"owner", "editor"},
        "required_for_readiness": False,
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
        self.fail_next_implementation = False
        self.hold_business = False
        self.held_business = None
        self.extra_documents = []
        self.cycle_error = False
        self.document_unavailable = False
        self.hold_implementation = False
        self.held_implementation = None

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
        if parts[1] == "document-overview":
            state = (
                "preparing"
                if any(job["status"] in {"queued", "running"} for job in self.document["jobs"])
                else "awaiting_decision"
                if any(change["status"] == "ready" for change in self.document["changes"])
                else "needs_review"
            )
            item = {
                **self.document,
                "state": state,
                "action": "text",
                "responsible_name": "QA",
                "reasons": [{"message": "Требуется проверка"}],
                "blockers": [],
                "source_ids": [],
                "dependent_ids": [],
                "mine": True,
            }
            required = self.document["required_for_readiness"]
            agreed = required and self.document["approved_revision_id"] == self.document["current_revision_id"]
            others = [
                {**other, "state": "current", "action": "text", "responsible_name": "QA", "reasons": [], "blockers": [], "source_ids": [], "dependent_ids": [], "mine": True}
                for other in self.extra_documents
            ]
            return self.respond(
                route,
                {
                    "items": [item, *others],
                    "can_manage": workspace["role"] == "owner",
                    "readiness": {"configured": required, "required_count": int(required), "documents_agreed": agreed, "maintenance_complete": agreed, "blockers": []},
                },
            )
        if parts[1] == "business-document-sources":
            if self.hold_business:
                self.held_business = route
                return
            return self.respond(route, {"items": [{"document_id": "native", "title": "Бизнес-требования", "revision_number": 2}], "page": 1, "has_more": False})
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
                if request.method == "GET" and parts[2] != "doc":
                    return self.respond(route, next(item for item in self.extra_documents if item["id"] == parts[2]))
                if request.method == "GET" and self.document_unavailable:
                    return route.fulfill(
                        status=409, content_type="application/json", body=json.dumps({"code": 409, "message": "Согласованный источник недоступен", "data": {"error_code": "DOCUMENT_UNAVAILABLE"}})
                    )
                if request.method == "GET" and self.hold_next_document:
                    self.hold_next_document = False
                    self.held_document = (route, deepcopy(self.document))
                    return
                if request.method == "PUT":
                    assert payload["expected_version"] == self.document["version"]
                    if "content" in payload:
                        self.update_content(payload["content"])
                    else:
                        self.document.update({key: value for key, value in payload.items() if key != "expected_version"})
                        self.document["version"] += 1
                return self.respond(route, self.document)
            if parts[3] == "implementation":
                if self.hold_implementation:
                    self.held_implementation = route
                    return
                if self.fail_next_implementation:
                    self.fail_next_implementation = False
                    return route.fulfill(status=503, content_type="application/json", body=json.dumps({"code": 503, "message": "Временная ошибка сети"}))
                assert payload["expected_version"] == self.document["version"]
                revoke = parts[-1] == "revoke"
                if revoke:
                    assert payload["implementation_event_id"] == self.document["implementation_event_id"] and payload["reason"].strip()
                    self.document.update(implemented_revision_id=None, implementation_event_id=None)
                else:
                    assert payload["revision_id"] == self.document["approved_revision_id"]
                    self.document.update(implemented_revision_id=payload["revision_id"], implementation_event_id="confirmation")
                self.document["version"] += 1
                self.document["implementation_history"].insert(
                    0,
                    {
                        "id": str(self.document["version"]),
                        "actor_id": "user",
                        "actor_name": "QA",
                        "created_at": NOW,
                        "kind": "implementation_revoked" if revoke else "implementation_confirmed",
                        "payload": {"revision_id": "rev1", "comment": payload.get("reason", payload.get("comment", ""))},
                    },
                )
                return self.respond(route, self.document)
            if parts[3] == "dependencies" and self.cycle_error:
                return route.fulfill(
                    status=409,
                    content_type="application/json",
                    body=json.dumps(
                        {"code": 409, "message": "Связь создаёт цикл: Контрольный документ → Основание → Контрольный документ", "data": {"error_code": "DEPENDENCY_CYCLE"}}, ensure_ascii=False
                    ),
                )
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


def test_implementation_confirmation_revoke_and_history_survive_reload(page, base_url, linked_api):
    linked_api.document["approved_revision_id"] = "rev1"
    _open_workspace(page, base_url)
    _open_document(page)
    panel = page.get_by_role("region", name="Реализация документа")
    panel.get_by_role("button", name="Реализация и история", exact=True).click()
    expect(panel.get_by_text("Подтвердить реализацию утверждённой редакции 1 целиком.")).to_be_visible()
    panel.get_by_role("textbox", name="Комментарий о реализации").fill("Проверено на стенде")
    linked_api.fail_next_implementation = True
    with page.expect_response(lambda response: _response(response, "POST", DETAIL + "/implementation")):
        panel.get_by_role("button", name="Подтвердить реализацию", exact=True).click()
    expect(page.get_by_role("alert").filter(has_text="Временная ошибка сети")).to_be_visible()
    expect(panel.get_by_role("textbox", name="Комментарий о реализации")).to_have_value("Проверено на стенде")
    with page.expect_response(lambda response: _response(response, "POST", DETAIL + "/implementation")):
        panel.get_by_role("button", name="Подтвердить реализацию", exact=True).click()
    expect(panel.get_by_text("Реализация: подтверждена редакция 1.", exact=True)).to_be_visible()
    expect(panel.get_by_role("button", name="Отменить подтверждение реализации")).to_be_disabled()
    panel.get_by_role("textbox", name="Причина отмены реализации").fill("Найдено несоответствие")
    with page.expect_response(lambda response: _response(response, "POST", DETAIL + "/implementation/revoke")):
        panel.get_by_role("button", name="Отменить подтверждение реализации").click()
    expect(panel.get_by_text("Реализация: не подтверждена.", exact=True)).to_be_visible()
    assert len(linked_api.matching("POST", DETAIL + "/implementation")) == 2
    assert len(linked_api.matching("POST", DETAIL + "/implementation/revoke")) == 1
    page.reload()
    page.get_by_role("button", name="Пространство A Статей: 0").click()
    _open_document(page)
    panel.get_by_text("История реализации", exact=True).click()
    expect(panel.get_by_text("Найдено несоответствие", exact=True)).to_be_visible()
    expect(panel.get_by_text("Проверено на стенде", exact=True)).to_be_visible()


def test_overview_search_required_set_and_business_picker_on_narrow_screen(page, base_url, linked_api):
    page.set_viewport_size({"width": 620, "height": 900})
    _open_workspace(page, base_url)
    overview = page.get_by_role("region", name="Обзор актуализации")
    expect(overview.get_by_text("Комплект не настроен.", exact=False)).to_be_visible()
    overview.get_by_role("textbox", name="Найти документ по названию").fill("Нет такого")
    expect(overview.get_by_text("Нет документов по выбранному фильтру.")).to_be_visible()
    overview.get_by_role("button", name="Показать все документы").click()
    _open_document(page)
    page.get_by_role("button", name="Источники и связи", exact=True).click()
    page.get_by_text("Свойства документа", exact=True).click()
    with page.expect_response(lambda response: _response(response, "PUT", DETAIL)):
        page.get_by_role("checkbox", name="Входит в обязательный комплект").click()
    expect(page.get_by_role("checkbox", name="Входит в обязательный комплект")).to_be_checked()
    expect(overview.get_by_text("Обязательных документов: 1")).to_be_visible()
    page.get_by_text("Подключить Business Documents", exact=True).click()
    with page.expect_response(lambda response: _response(response, "GET", f"{BASE}/a/business-document-sources")):
        page.get_by_role("button", name="Найти источники", exact=True).click()
    expect(page.get_by_text("Бизнес-требования · согласованная редакция 2")).to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")


def test_late_business_catalog_cannot_replace_new_workspace(page, base_url, linked_api):
    linked_api.hold_business = True
    _open_workspace(page, base_url)
    page.get_by_text("Подключить Business Documents", exact=True).click()
    with page.expect_request(lambda request: urlsplit(request.url).path == f"{BASE}/a/business-document-sources"):
        page.get_by_role("button", name="Найти источники", exact=True).click()
    expect(page.get_by_role("button", name="Найти источники", exact=True)).to_be_disabled()
    page.get_by_role("button", name="К рабочим пространствам", exact=True).click()
    page.get_by_role("button", name="Пространство B Статей: 0").click()
    assert linked_api.held_business is not None
    with page.expect_response(lambda response: _response(response, "GET", f"{BASE}/a/business-document-sources")) as response:
        linked_api.respond(linked_api.held_business, {"items": [{"document_id": "closed", "title": "Закрытый старый источник", "revision_number": 2}], "page": 1, "has_more": False})
    response.value.finished()
    page.get_by_text("Подключить Business Documents", exact=True).click()
    expect(page.get_by_text("Закрытый старый источник", exact=False)).to_have_count(0)
    expect(page.get_by_role("heading", name="Пространство B", exact=True)).to_be_visible()


def test_worker_completion_updates_overview_without_user_action(page, base_url, linked_api):
    page.clock.install(time="2026-10-18T10:00:00Z")
    linked_api.document["jobs"] = [{"id": "job", "status": "running", "stage": "planning", "result_id": None, "error": None}]
    _open_workspace(page, base_url)
    _open_document(page)
    expect(page.get_by_role("button", name="Проверить источники", exact=True)).to_be_enabled()
    overview = page.get_by_role("region", name="Обзор актуализации")
    expect(overview.get_by_text("Техническое задание · Подготовка предложений")).to_be_visible()
    linked_api.document["changes"] = [_change()]
    linked_api.document["jobs"][0].update(status="done", stage="done", result_id="change")
    with page.expect_response(lambda response: _response(response, "GET", f"{BASE}/a/document-overview")):
        page.clock.run_for(2100)
    expect(overview.get_by_text("Техническое задание · Ожидает согласования")).to_be_visible()


def test_cycle_path_keeps_dependency_form_and_current_text(page, base_url, linked_api):
    linked_api.cycle_error = True
    source = {**_document("owner"), "id": "source", "title": "Основание"}
    linked_api.extra_documents = [source]
    _open_workspace(page, base_url)
    _open_document(page)
    page.get_by_role("button", name="Источники и связи", exact=True).click()
    page.get_by_role("combobox", name="Документ-источник").select_option("source")
    with page.expect_response(lambda response: _response(response, "POST", DETAIL + "/dependencies")):
        page.get_by_role("button", name="Сохранить связь", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Контрольный документ → Основание → Контрольный документ")
    expect(page.get_by_role("combobox", name="Документ-источник")).to_have_value("source")
    assert linked_api.document["version"] == 1 and not linked_api.document["dependencies"]


def test_access_loss_removes_cached_document_and_does_not_reopen_it(page, base_url, linked_api):
    _open_workspace(page, base_url)
    _open_document(page)
    expect(page.get_by_role("article", name="Хранение", exact=True)).to_contain_text("30 дней")
    linked_api.document_unavailable = True
    with page.expect_response(lambda response: _response(response, "GET", DETAIL)):
        page.get_by_role("button", name="Контрольный документ", exact=False).click()
    expect(page.get_by_role("alert")).to_contain_text("Согласованный источник недоступен")
    expect(page.get_by_role("article", name="Хранение", exact=True)).to_have_count(0)
    with page.expect_response(lambda response: _response(response, "GET", DETAIL)):
        page.get_by_role("button", name="Контрольный документ", exact=False).press("Enter")
    expect(page.get_by_role("article", name="Хранение", exact=True)).to_have_count(0)


def test_double_click_submits_one_implementation_and_keeps_pending_form(page, base_url, linked_api):
    linked_api.hold_implementation = True
    linked_api.document["approved_revision_id"] = "rev1"
    _open_workspace(page, base_url)
    _open_document(page)
    panel = page.get_by_role("region", name="Реализация документа")
    panel.get_by_role("button", name="Реализация и история", exact=True).click()
    submit = panel.get_by_role("button", name="Подтвердить реализацию", exact=True)
    with page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == DETAIL + "/implementation"):
        submit.dblclick()
    expect(submit).to_be_disabled()
    assert len(linked_api.matching("POST", DETAIL + "/implementation")) == 1
    assert linked_api.held_implementation is not None
    linked_api.document.update(version=2, implemented_revision_id="rev1", implementation_event_id="confirmed")
    with page.expect_response(lambda response: _response(response, "POST", DETAIL + "/implementation")):
        linked_api.respond(linked_api.held_implementation, linked_api.document)
    expect(panel.get_by_text("Реализация: подтверждена редакция 1.", exact=True)).to_be_visible()
