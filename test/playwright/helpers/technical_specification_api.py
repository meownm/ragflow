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

    @staticmethod
    def respond(route, value, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps({"code": 0 if status == 200 else status, "data": value}, ensure_ascii=False))

    def calls(self, action):
        return [item for item in self.requests if item[0] == "POST" and item[1] == DETAIL + "/" + action]

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
        body = request.post_data_json if request.post_data else None
        self.requests.append((request.method, path, body))
        if path == BASE:
            return self.respond(route, [self.workspace])
        if path == BASE + "/workspace":
            return self.respond(route, self.workspace)
        if path == COLLECTION:
            return self.respond(route, [self.document])
        if path == COLLECTION + "/eva-pages":
            return self.respond(
                route,
                {
                    "items": [
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
                old = {"id": message["message_id"], "role": "user", "author_id": ACTOR, "text": message["text"], "context": message["context"]}
                self.document["messages"].append(old)
            old["status"] = "failed" if self.fail_chat else "completed"
            self.document["version"] += 1
            if self.fail_chat:
                route.fulfill(status=504, content_type="application/json", body=json.dumps({"code": 504, "message": "Помощник не успел ответить", "data": {"error_code": "MODEL_TIMEOUT"}}))
                return
            answer = "Лимит не задан" if message["text"] == "Какой лимит?" else "Предлагаю уточнить кнопку"
            self.document["messages"].append({"id": "answer", "role": "assistant", "text": answer})
            if message["text"] != "Какой лимит?":
                self.document["proposals"] = [
                    {"id": "proposal", "source": "chat", "status": "ready", "base_revision": self.document["revision"], "sections": {**self.document["sections"], "5": "Кнопка Записать"}}
                ]
            return self.respond(route, self.document)
        if path == DETAIL + "/apply":
            assert body["proposal_id"] == "proposal"
            proposal = self.document["proposals"][0]
            self.document["sections"] = deepcopy(proposal["sections"])
            proposal["status"] = "applied"
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
                self.document["comments"].append({"id": body["message_id"], "author_id": ACTOR, "text": body["text"], "context": body["context"], "resolved": False, "replies": []})
        elif path == DETAIL + "/publish-eva":
            self.document["eva_export"] = {"status": "published", "revision": self.document["revision"]}
            self.document["eva"]["remote_version"] = "v2"
        elif path == DETAIL + "/resolve-eva":
            assert body["proposal_id"] == "proposal" and body["choice"] in {"local", "remote"}
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
        return self.respond(route, self.document)
