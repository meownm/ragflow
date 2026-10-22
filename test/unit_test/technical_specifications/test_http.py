import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from quart import Blueprint, Quart, request

from api.technical_specifications.application import Specifications
from test.unit_test.technical_specifications.test_auth_boundary import _install_application_error_handlers


async def run(method, *args, **kwargs):
    return method(*args, **kwargs)


class AI:
    async def chat(self, actor, document, message):
        return {"answer": "Уточнён сценарий", "patches": {"5": "Оператор загружает документ."}}

    async def verify(self, actor, before, after, *, change_request=None):
        return {"valid": True, "issues": []}


@pytest.fixture
def http(store, monkeypatch):
    apps = ModuleType("api.apps")
    apps.current_user = SimpleNamespace(id="author")
    apps.login_required = lambda method: method
    monkeypatch.setitem(sys.modules, "api.apps", apps)
    runtime = ModuleType("api.technical_specifications.runtime")
    eva = SimpleNamespace(
        search_sources=lambda actor, query, limit: {"items": [{"id": "page", "project_id": "project", "eva_origin": ["https", "eva.test", 443], "name": query, "version": "v1"}], "connectors": []}
    )
    runtime.build_specifications = lambda: Specifications(store, AI(), run, eva)
    monkeypatch.setitem(sys.modules, runtime.__name__, runtime)
    utilities = ModuleType("api.utils.api_utils")
    utilities.get_request_json = lambda: request.get_json()
    monkeypatch.setitem(sys.modules, utilities.__name__, utilities)
    path = Path(__file__).resolve().parents[3] / "api/apps/restful_apis/technical_specifications_api.py"
    spec = spec_from_file_location("specifications_http_test", path)
    module = module_from_spec(spec)
    module.manager = Blueprint("specifications_http_test", __name__)
    spec.loader.exec_module(module)
    app = Quart(__name__)
    app.register_blueprint(module.manager)
    _install_application_error_handlers(app)
    return app.test_client(), apps.current_user


BASE = "/source-workspaces/workspace/technical-specifications"


@pytest.mark.asyncio
async def test_http_import_cancellation_persists_before_a_delayed_request_and_is_personal(http):
    client, actor = http
    body = {"operation_id": "pending", "url": "https://eva.test/page?ignored=secret", "copy": True}
    response = await client.post(BASE + "/import-eva/cancel", json=body)
    assert response.status_code == 200
    value = (await response.get_json())["data"]
    assert value["state"] == "canceled" and value["request"]["url"] == "https://eva.test/page"
    status = await client.get(BASE + "/import-eva/pending")
    assert (await status.get_json())["data"] == value
    response = await client.post(BASE + "/import-eva", json=body)
    assert response.status_code == 409 and (await response.get_json())["data"]["error_code"] == "IMPORT_CANCELED"
    response = await client.post(BASE + "/import-eva/cancel", json={**body, "copy": False})
    assert response.status_code == 409
    response = await client.post(BASE + "/import-eva/cancel", json={**body, "url": "https://user:password@eva.test/page"})
    assert response.status_code == 422
    actor.id = "reader"
    assert (await client.get(BASE + "/import-eva/pending")).status_code == 403
    assert (await client.post(BASE + "/import-eva/cancel", json=body)).status_code == 403


@pytest.mark.asyncio
async def test_http_eva_picker_checks_project_permissions_and_search_limits(http):
    client, actor = http
    response = await client.get(BASE + "/eva-pages", query_string={"query": "Загрузка"})
    assert response.status_code == 200
    item = (await response.get_json())["data"]["items"][0]
    assert item["id"] == "page" and item["binding_available"] is True
    response = await client.get(BASE + "/eva-pages?query=" + "x" * 501)
    assert response.status_code == 422
    actor.id = "reader"
    assert (await client.get(BASE + "/eva-pages", query_string={"query": "Загрузка"})).status_code == 403


async def create(client):
    response = await client.post(BASE, json={"title": "ТЗ загрузки"})
    assert response.status_code == 200
    return (await response.get_json())["data"]


@pytest.mark.asyncio
async def test_export_checks_access_and_revision_and_preserves_nine_sections(http):
    from api.technical_specifications.domain import HEADINGS, parse_markdown

    client, actor = http
    document = await create(client)
    url = BASE + "/" + document["id"]
    content = {**document["sections"], "1": "Назначение: **загрузка**.", "5": "- Выбрать файл\n- Отправить", "8": '```json\n{"ok": true}\n```'}
    response = await client.put(url, json={"version": document["version"], "title": "CON:/ТЗ?*", "sections": content})
    saved = (await response.get_json())["data"]
    context = {"document_id": document["id"], "scope": "all", "revision": saved["revision"], "anchor": None}
    comment = await client.post(url + "/comments", json={"version": saved["version"], "message_id": "comment", "text": "Закрытый комментарий", "context": context})
    assert comment.status_code == 200
    draft = await client.put(url + "/draft", json={"version": 0, "draft": {"message_id": "private", "text": "Личный черновик", "context": context}})
    assert draft.status_code == 200
    actor.id = "reader"
    response = await client.post(url + "/export", json={"revision": saved["revision"]})
    assert response.status_code == 200
    file = (await response.get_json())["data"]
    assert file["filename"] == "ТЗ-CON__ТЗ__-r2.md"
    assert file["mime_type"].startswith("text/markdown")
    assert parse_markdown(file["content"]) == content
    assert "Закрытый комментарий" not in file["content"] and "Личный черновик" not in file["content"]
    assert all(f"## {number}. {heading}" in file["content"] for number, heading in enumerate(HEADINGS, 1))
    assert (await client.post(url + "/export", json={"revision": 1})).status_code == 409
    actor.id = "stranger"
    assert (await client.post(url + "/export", json={"revision": saved["revision"]})).status_code == 404
    actor.id = "reader"
    assert (await client.post(url + "/export", json={"revision": True})).status_code == 409


@pytest.mark.asyncio
async def test_http_chat_apply_and_history_without_approval(http):
    client, _actor = http
    document = await create(client)
    url = BASE + "/" + document["id"]
    response = await client.post(
        url + "/chat", json={"version": 1, "draft": {"message_id": "message", "text": "Уточни сценарий", "context": {"document_id": document["id"], "scope": "5", "revision": 1, "anchor": None}}}
    )
    assert response.status_code == 200
    proposal = (await response.get_json())["data"]
    assert proposal["sections"]["5"] == ""
    response = await client.post(url + "/apply", json={"version": proposal["version"], "proposal_id": proposal["proposals"][0]["id"]})
    assert response.status_code == 200
    result = (await response.get_json())["data"]
    assert result["revision"] == 2 and len(result["sections"]) == 9
    assert result["sections"]["5"] == "Оператор загружает документ."
    revisions = (await (await client.get(url + "/revisions")).get_json())["data"]
    assert len(revisions) == 2 and all("sections" not in item for item in revisions)
    first = (await (await client.get(url + "/revisions/1")).get_json())["data"]
    assert first["sections"]["5"] == ""


@pytest.mark.asyncio
async def test_http_large_personal_draft_roundtrip_retains_every_context_field(http):
    client, _actor = http
    document = await create(client)
    url = BASE + "/" + document["id"] + "/draft"
    draft = {
        "message_id": "message",
        "text": "уточнить " * 1000,
        "context": {"document_id": document["id"], "scope": "selection", "revision": 1, "anchor": {"section": "5", "quote": "Исходный фрагмент", "prefix": "До ", "suffix": " после"}},
    }
    response = await client.put(url, json={"version": 0, "draft": draft})
    assert response.status_code == 200
    restored = (await (await client.get(url)).get_json())["data"]
    assert restored == {"version": 1, **draft}
    conflict = await client.put(url, json={"version": 0, "draft": {**draft, "text": "Другое окно"}})
    assert conflict.status_code == 409
    assert (await (await client.get(url)).get_json())["data"] == restored


@pytest.mark.asyncio
async def test_http_restore_title_and_content_as_new_revision(http):
    client, _actor = http
    document = await create(client)
    url = BASE + "/" + document["id"]
    changed = {**document["sections"], "9": "Расширенный журнал"}
    saved = await client.put(url, json={"version": 1, "title": "Другое имя", "sections": changed})
    assert saved.status_code == 200
    latest = (await saved.get_json())["data"]
    restored = await client.post(url + "/restore", json={"version": latest["version"], "revision": 1})
    assert restored.status_code == 200
    value = (await restored.get_json())["data"]
    assert value["revision"] == 3 and value["title"] == document["title"]
    assert value["sections"] == document["sections"]


@pytest.mark.asyncio
async def test_http_timeout_keeps_the_accepted_message_without_claiming_a_saved_draft(http, monkeypatch):
    async def timeout(*args):
        raise TimeoutError()

    monkeypatch.setattr(AI, "chat", timeout)
    client, _actor = http
    document = await create(client)
    url = BASE + "/" + document["id"]
    response = await client.post(
        url + "/chat", json={"version": 1, "draft": {"message_id": "request", "text": "Вопрос", "context": {"document_id": document["id"], "scope": "all", "revision": 1, "anchor": None}}}
    )
    assert response.status_code == 504
    assert "сохранён" not in (await response.get_json())["message"]
    saved = (await (await client.get(url)).get_json())["data"]
    assert saved["messages"][0]["status"] == "failed"
    assert saved["messages"][0]["text"] == "Вопрос"


@pytest.mark.asyncio
async def test_http_acl_and_wrong_workspace_cannot_read_content_or_draft(http):
    client, actor = http
    document = await create(client)
    actor.id = "stranger"
    for suffix in ("", "/draft", "/revisions"):
        response = await client.get(BASE + "/" + document["id"] + suffix)
        assert response.status_code == 404
    response = await client.get(BASE.replace("workspace/", "other/") + "/" + document["id"])
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], "текст", None])
async def test_http_rejects_non_object_body(http, payload):
    client, _actor = http
    response = await client.post(BASE, json=payload)
    assert response.status_code == 422


# Use valid input for each action: validation errors must not accidentally pass
# an authorization check without exercising the protected scenario.
MUTATIONS = [
    ("POST", "", {"title": "Чужая правка"}),
    ("PUT", "/{id}", "edit"),
    ("POST", "/{id}/chat", "chat"),
    ("POST", "/{id}/comments", "comment"),
    ("POST", "/{id}/comments", {"version": 1, "action": "reply", "comment_id": "comment", "message_id": "reply", "text": "Ответ"}),
    ("POST", "/{id}/comments", {"version": 1, "action": "resolve", "comment_id": "comment", "resolved": True}),
    ("POST", "/{id}/apply", {"version": 1, "proposal_id": "proposal"}),
    ("POST", "/{id}/restore", {"version": 1, "revision": 1}),
    ("POST", "/{id}/pull", {"version": 1}),
    ("POST", "/{id}/resolve-eva", {"version": 1, "proposal_id": "proposal", "choice": "local"}),
    ("POST", "/{id}/connect-eva", {"version": 1, "url": "https://eva.test/page"}),
    ("POST", "/{id}/publish-eva", {"version": 1}),
    ("POST", "/import-eva", {"operation_id": "import", "url": "https://eva.test/page"}),
    ("POST", "/import-eva/cancel", {"operation_id": "import", "url": "https://eva.test/page"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_id", ["reader", "stranger"])
@pytest.mark.parametrize("method,suffix,payload", MUTATIONS)
async def test_http_every_mutation_rejects_reader_and_outsider_without_side_effects(http, store, monkeypatch, actor_id, method, suffix, payload):
    from api.db.db_models import DocumentExchangeBinding, DocumentExchangeOperation, TechnicalSpecificationRevision

    client, actor = http
    document = await create(client)
    context = {"document_id": document["id"], "scope": "all", "revision": 1, "anchor": None}
    payload = (
        {
            "edit": {"version": 1, "title": "Чужая правка", "sections": document["sections"]},
            "chat": {"version": 1, "draft": {"message_id": "chat", "text": "Измени ТЗ", "context": context}},
            "comment": {"version": 1, "message_id": "comment", "text": "Комментарий", "context": context},
        }.get(payload, payload)
        if isinstance(payload, str)
        else payload
    )

    async def forbidden_model(*args, **kwargs):
        pytest.fail("Unauthorized request reached the model")

    monkeypatch.setattr(AI, "chat", forbidden_model)
    monkeypatch.setattr(AI, "verify", forbidden_model)
    actor.id = actor_id
    response = await client.open(BASE + suffix.format(id=document["id"]), method=method, json=payload)
    assert response.status_code in {403, 404}
    error = await response.get_json()
    assert error["code"] == response.status_code and error["data"]["error_code"]
    assert document["title"] not in str(error) and document["id"] not in str(error)
    assert store.get("author", "workspace", document["id"]) == document
    assert TechnicalSpecificationRevision.select().count() == 1
    assert DocumentExchangeBinding.select().count() == DocumentExchangeOperation.select().count() == 0


@pytest.mark.asyncio
async def test_http_reader_can_read_history_and_export_but_drafts_remain_personal(http):
    client, actor = http
    document = await create(client)
    url = BASE + "/" + document["id"]
    context = {"document_id": document["id"], "scope": "all", "revision": 1, "anchor": None}
    private = {"message_id": "author-draft", "text": "Личный текст автора", "context": context}
    assert (await client.put(url + "/draft", json={"version": 0, "draft": private})).status_code == 200
    actor.id = "reader"
    for suffix in ("", "/revisions", "/revisions/1"):
        assert (await client.get(url + suffix)).status_code == 200
    assert (await (await client.get(url + "/draft")).get_json())["data"] is None
    reader_draft = {**private, "message_id": "reader-draft", "text": "Личный текст читателя"}
    assert (await client.put(url + "/draft", json={"version": 0, "draft": reader_draft})).status_code == 200
    assert (await client.post(url + "/export", json={"revision": 1})).status_code == 200
    actor.id = "author"
    assert (await (await client.get(url + "/draft")).get_json())["data"] == {"version": 1, **private}


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/draft", "/comments", "/chat", "/apply", "/restore", "/pull", "/resolve-eva", "/export", "/connect-eva", "/publish-eva"])
async def test_http_malformed_mutations_have_a_structured_error_and_preserve_document(http, suffix):
    client, _actor = http
    document = await create(client)
    url = BASE + "/" + document["id"]
    response = await client.open(url + suffix, method="PUT" if suffix in {"", "/draft"} else "POST", data="{broken", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    error = await response.get_json()
    assert error["code"] == 422 and error["data"]["error_code"] == "INVALID_INPUT"
    assert (await (await client.get(url)).get_json())["data"] == document


@pytest.mark.asyncio
async def test_http_unknown_action_cannot_dispatch_or_change_document(http):
    client, _actor = http
    document = await create(client)
    url = BASE + "/" + document["id"]
    response = await client.post(url + "/approve", json={"version": 1})
    assert response.status_code == 404
    assert (await response.get_json())["data"]["error_code"] == "NOT_FOUND"
    assert (await (await client.get(url)).get_json())["data"] == document


@pytest.mark.asyncio
async def test_http_unexpected_storage_failure_uses_safe_global_error_envelope_and_rolls_back(http, store, monkeypatch):
    from api.db.db_models import TechnicalSpecificationRevision
    from common.observability import bind_context

    client, _actor = http
    document = await create(client)
    url = BASE + "/" + document["id"]

    def interrupted(**kwargs):
        raise RuntimeError("private storage failure details")

    monkeypatch.setattr(TechnicalSpecificationRevision, "create", interrupted)
    with bind_context(request_id="synthetic-storage-request"):
        response = await client.put(url, json={"version": document["version"], "title": document["title"], "sections": {**document["sections"], "5": "Новая редакция"}})
    assert response.status_code == 500
    error = await response.get_json()
    assert error["code"] == 100 and error["message"] == "Internal server error"
    assert error["data"]["request_id"] == "synthetic-storage-request"
    assert error["data"]["error_id"] == response.headers["X-Error-ID"]
    assert len(error["data"]["error_id"]) == 32 and "error_code" not in error["data"]
    assert "private storage failure details" not in str(error)
    assert (await (await client.get(url)).get_json())["data"] == document
    assert TechnicalSpecificationRevision.select().count() == 1
