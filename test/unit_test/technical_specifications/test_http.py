import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from quart import Blueprint, Quart, request

from api.technical_specifications.application import Specifications


async def run(method, *args, **kwargs):
    return method(*args, **kwargs)


class AI:
    async def chat(self, actor, document, message):
        return {"answer": "Уточнён сценарий", "patches": {"5": "Оператор загружает документ."}}

    async def verify(self, actor, before, after):
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
    return app.test_client(), apps.current_user


BASE = "/source-workspaces/workspace/technical-specifications"


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
