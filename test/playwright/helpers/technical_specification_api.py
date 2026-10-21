"""Synthetic technical specification HTTP fixture shared by browser journeys."""

import json
from copy import deepcopy
from urllib.parse import urlsplit

ACTOR = "business-documents-ui-user"
BASE = "/api/v1/source-workspaces"
COLLECTION = BASE + "/workspace/technical-specifications"
DETAIL = COLLECTION + "/doc"
LOCATION = "/technical-specifications?workspace=workspace&document=doc"


class SpecificationApi:
    def __init__(self, role="owner"):
        self.workspace = {"id": "workspace", "title": "Проект ТЗ", "role": role}
        self.document = {
            "id": "doc",
            "workspace_id": "workspace",
            "title": "ТЗ загрузки",
            "version": 1,
            "revision": 1,
            "sections": {str(n): "" for n in range(1, 10)},
            "messages": [],
            "comments": [],
            "proposals": [],
            "people": {ACTOR: "QA"},
            "eva": {"page_url": "https://eva.test/page", "remote_version": "v1", "document_name": "ТЗ загрузки"},
        }
        self.document["sections"].update({"1": "До цитата после", "4": "### 4.1. Конфигурации сервисов (дополнительные)\n\n### 4.2. Права, ограничения и управление правами", "5": "Кнопка Сохранить"})
        self.requests = []
        self.draft = None
        self.fail_publish = False
        self.fail_chat = False
        self.import_request = None
        self.revisions = {1: deepcopy(self.document)}
        self.access_revoked = False
        self.chat_processing = False
        self.model_calls = 0
        self.lose_comment_response_once = False
        self.hold_comment_response = False
        self.pending_comment_response = None
        self.import_existing = False
        self.created_documents = 0
        self.eva_pages = None
        self.pull_sections = None
        self.pull_remote_version = "v2"
        self.pulled_remote_version = None
        self.pull_preparations = 0
        self.resolve_error = False
        self.apply_failure = None
        self.export_conflict = False
        self.documents = None
        self.list_error = False
        self.create_error = False
        self.hold_list_response = False
        self.pending_list_response = None
        self.hold_save_response = False
        self.pending_save_response = None

    @staticmethod
    def respond(route, value, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps({"code": 0 if status == 200 else status, "data": value}, ensure_ascii=False))

    def calls(self, action):
        return [item for item in self.requests if item[0] == "POST" and item[1] == DETAIL + "/" + action]

    @staticmethod
    def reject(route, code, message, status=409):
        route.fulfill(status=status, content_type="application/json", body=json.dumps({"code": status, "message": message, "data": {"error_code": code}}, ensure_ascii=False))

    def complete_chat(self):
        message = next(item for item in reversed(self.document["messages"]) if item["role"] == "user" and item["status"] == "processing")
        message["status"] = "completed"
        answer = "Лимит не задан" if message["text"] == "Какой лимит?" else "Предлагаю уточнить кнопку"
        self.document["messages"].append({"id": "answer-" + message["id"], "reply_to": message["id"], "role": "assistant", "text": answer, "source_revision": message["source_revision"]})
        if message["text"] != "Какой лимит?":
            self.document["proposals"] = [
                {"id": "proposal", "source": "chat", "status": "ready", "base_revision": message["source_revision"], "sections": {**self.document["sections"], "5": "Кнопка Записать"}}
            ]
        self.document["version"] += 1

    def release_comment_response(self):
        route, self.pending_comment_response = self.pending_comment_response, None
        assert route is not None
        self.respond(route, self.document)

    def release_list_response(self):
        route, self.pending_list_response = self.pending_list_response, None
        assert route is not None
        self.respond(route, self.documents if self.documents is not None else [self.document])

    def release_save_response(self, lost=False):
        route, snapshot = self.pending_save_response
        self.pending_save_response = None
        self.hold_save_response = False
        if lost:
            return self.reject(route, "RESPONSE_LOST", "Ответ сохранения потерян", 502)
        self.respond(route, snapshot)

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
        body = request.post_data_json if request.post_data else None
        self.requests.append((request.method, path, body))
        if self.access_revoked:
            return self.reject(route, "FORBIDDEN", "Доступ к проекту отозван", 403)
        if path == BASE:
            return self.respond(route, [self.workspace])
        if path == BASE + "/workspace":
            return self.respond(route, self.workspace)
        if path == COLLECTION:
            if request.method == "POST":
                if self.workspace["role"] not in {"owner", "editor"}:
                    return self.reject(route, "FORBIDDEN", "Недостаточно прав", 403)
                if self.create_error:
                    return self.reject(route, "STORAGE_UNAVAILABLE", "Создание временно недоступно", 503)
                self.document.update(title=body["title"], sections={str(n): self.document["sections"]["4"] if n == 4 else "" for n in range(1, 10)}, eva=None)
                self.documents = [deepcopy(self.document)]
                self.revisions = {1: deepcopy(self.document)}
                self.created_documents += 1
                return self.respond(route, self.document)
            assert request.method == "GET"
            if self.list_error:
                return self.reject(route, "STORAGE_UNAVAILABLE", "Список временно недоступен", 503)
            if self.hold_list_response:
                self.pending_list_response = route
                return
            return self.respond(route, self.documents if self.documents is not None else [self.document])
        if path == COLLECTION + "/eva-pages":
            return self.respond(
                route,
                {
                    "items": self.eva_pages
                    if self.eva_pages is not None
                    else [
                        {
                            "id": "page",
                            "project_id": "eva-project",
                            "version": "v1",
                            "name": "Тестовая страница",
                            "web_url": "https://eva.test/page",
                            "binding_available": True,
                            "linked_document": None,
                        }
                    ]
                },
            )
        if path == COLLECTION + "/import-eva":
            self.import_request = deepcopy(body)
            if self.import_existing:
                return self.respond(route, self.document)
            self.created_documents += 1
            # The local transaction committed; only the browser response failed.
            route.fulfill(status=502, content_type="application/json", body=json.dumps({"code": 502, "message": "Ответ импорта потерян", "data": {"error_code": "IMPORT_FAILED"}}))
            return
        if path.startswith(COLLECTION + "/import-eva/"):
            assert self.import_request and path.endswith(self.import_request["operation_id"])
            return self.respond(route, {"id": self.import_request["operation_id"], "state": "completed", "document": self.document})
        if path == DETAIL and request.method == "GET":
            return self.respond(route, self.document)
        if path == DETAIL + "/draft":
            if request.method == "PUT":
                self.draft = {"version": body["version"] + 1, **deepcopy(body["draft"])}
            return self.respond(route, self.draft)
        if path == DETAIL + "/revisions":
            return self.respond(
                route, [{"number": number, "title": value["title"], "author_id": ACTOR, "reason": "edit", "created_at": "2026-10-20T00:00:00Z"} for number, value in self.revisions.items()]
            )
        if path.startswith(DETAIL + "/revisions/"):
            number = int(path.rsplit("/", 1)[1])
            return self.respond(route, {"number": number, **self.revisions[number]})
        if path == DETAIL + "/publish-eva" and self.fail_publish:
            route.fulfill(status=502, content_type="application/json", body=json.dumps({"code": 502, "message": "Ответ Eva не подтверждён", "data": {"error_code": "EVA_EXPORT_FAILED"}}))
            return
        if path == DETAIL + "/chat":
            message = body["draft"]
            old = next((item for item in self.document["messages"] if item["id"] == message["message_id"]), None)
            if old is None:
                old = {"id": message["message_id"], "role": "user", "author_id": ACTOR, "text": message["text"], "context": message["context"], "source_revision": self.document["revision"]}
                self.document["messages"].append(old)
            elif old.get("status") in {"processing", "completed"}:
                assert old["text"] == message["text"] and old["context"] == message["context"]
                return self.respond(route, self.document)
            self.model_calls += 1
            old["status"] = "processing"
            if self.fail_chat:
                old["status"] = "failed"
                self.document["version"] += 1
                route.fulfill(status=504, content_type="application/json", body=json.dumps({"code": 504, "message": "Помощник не успел ответить", "data": {"error_code": "MODEL_TIMEOUT"}}))
                return
            if self.chat_processing:
                self.document["version"] += 1
            else:
                self.complete_chat()
            return self.respond(route, self.document)
        if path == DETAIL + "/pull":
            assert body["version"] == self.document["version"]
            if self.pull_sections is None or self.pulled_remote_version == self.pull_remote_version:
                return self.respond(route, self.document)
            self.pulled_remote_version = self.pull_remote_version
            self.pull_preparations += 1
            self.document["proposals"].append(
                {
                    "id": "proposal",
                    "source": "eva",
                    "status": "ready",
                    "base_revision": self.document["revision"],
                    "sections": deepcopy(self.pull_sections),
                    "binding": {"remote_version": self.pull_remote_version},
                }
            )
            self.document["version"] += 1
            return self.respond(route, self.document)
        if path == DETAIL + "/export":
            assert body == {"revision": self.document["revision"]}
            if self.export_conflict:
                return self.reject(route, "VERSION_CONFLICT", "ТЗ изменилось. Обновите документ перед выгрузкой")
            headings = [
                "Назначение и цель",
                "Общие сведения, термины, сокращения и ссылки",
                "Компоненты подлежащие доработки с описанием изменений",
                "Конфигурирование (настройки)",
                "Прототипы интерфейсов и сценарии использования",
                "Логика работы",
                "Модель данных",
                "Сигнатура взаимодействия",
                "Журналирование",
            ]
            content = "\n\n".join(f"## {number}. {heading}\n\n{self.document['sections'][str(number)]}" for number, heading in enumerate(headings, 1))
            return self.respond(
                route, {"filename": f"ТЗ-загрузки-r{self.document['revision']}.md", "mime_type": "text/markdown;charset=utf-8", "content": content, "revision": self.document["revision"]}
            )
        if path == DETAIL + "/apply":
            assert body["proposal_id"] == "proposal"
            proposal = self.document["proposals"][0]
            if self.apply_failure == "stale_eva":
                return self.reject(route, "EVA_CHANGED", "Страница Eva изменилась. Получите изменения заново")
            if self.apply_failure == "contradiction":
                proposal.update(status="needs_resolution", issues=["Разные лимиты в разделах 4 и 5"])
                self.document["version"] += 1
                return self.respond(route, self.document)
            self.document["sections"] = deepcopy(proposal["sections"])
            proposal["status"] = "applied"
            if proposal.get("binding"):
                self.document["eva"]["remote_version"] = proposal["binding"]["remote_version"]
            self.document["revision"] += 1
            self.revisions[self.document["revision"]] = deepcopy(self.document)
        elif path == DETAIL + "/comments":
            if body.get("action") == "reply":
                parent = next(item for item in self.document["comments"] if item["id"] == body["comment_id"])
                parent["replies"].append({"id": body["message_id"], "author_id": ACTOR, "text": body["text"]})
            elif body.get("action") == "resolve":
                parent = next(item for item in self.document["comments"] if item["id"] == body["comment_id"])
                parent["resolved"] = body["resolved"]
            else:
                old = next((item for item in self.document["comments"] if item["id"] == body["message_id"]), None)
                if old:
                    assert old["text"] == body["text"] and old["context"] == body["context"]
                    return self.respond(route, self.document)
                self.document["comments"].append({"id": body["message_id"], "author_id": ACTOR, "text": body["text"], "context": body["context"], "resolved": False, "replies": []})
            assert body["version"] == self.document["version"]
            self.document["version"] += 1
            if self.lose_comment_response_once:
                self.lose_comment_response_once = False
                return self.reject(route, "COMMENT_RESPONSE_LOST", "Ответ комментария потерян", 502)
            if self.hold_comment_response:
                self.hold_comment_response = False
                self.pending_comment_response = route
                return
            return self.respond(route, self.document)
        elif path == DETAIL + "/publish-eva":
            self.document["eva_export"] = {"status": "published", "revision": self.document["revision"]}
            self.document["eva"]["remote_version"] = "v2"
        elif path == DETAIL + "/resolve-eva":
            assert body["proposal_id"] == "proposal" and body["choice"] in {"local", "remote"}
            if self.resolve_error:
                return self.reject(route, "EVA_CHANGED", "Страница Eva изменилась. Получите изменения заново")
            proposal = self.document["proposals"][0]
            value = "35" if body["choice"] == "local" else "50"
            proposal.update(
                status="ready",
                sections={**self.document["sections"], "4": f"Лимит {value} файлов. Размер 40 МБ.", "5": f"Выбрать до {value} файлов", "8": f"Отклонить более {value} файлов. Журнал Eva сохранён."},
            )
        elif path == DETAIL and request.method == "PUT":
            self.document.update(title=body["title"], sections=deepcopy(body["sections"]), revision=self.document["revision"] + 1)
            self.revisions[self.document["revision"]] = deepcopy(self.document)
        elif path == DETAIL + "/restore":
            previous = self.revisions[body["revision"]]
            self.document.update(title=previous["title"], sections=deepcopy(previous["sections"]), revision=self.document["revision"] + 1)
            self.revisions[self.document["revision"]] = deepcopy(self.document)
        else:
            raise AssertionError(f"Unexpected specification request: {request.method} {path}")
        assert body["version"] == self.document["version"]
        self.document["version"] += 1
        if path == DETAIL and request.method == "PUT" and self.hold_save_response:
            self.pending_save_response = route, deepcopy(self.document)
            return
        return self.respond(route, self.document)
