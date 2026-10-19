"""Real HTTP/application/persistence boundary; login and model transport are controlled."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from quart import Blueprint, Quart, request
import pytest

from test.unit_test.api.apps.source_workbench import test_linked_documents as harness


pytestmark = [pytest.mark.p1, pytest.mark.asyncio]


@pytest.fixture
def linked(tmp_path):
    yield from harness.linked.__wrapped__(tmp_path)


@pytest.fixture
def linked_client(linked, monkeypatch):
    apps = ModuleType("api.apps")
    apps.current_user = SimpleNamespace(id="author")
    apps.login_required = lambda method: method
    monkeypatch.setitem(sys.modules, "api.apps", apps)
    adapters = ModuleType("api.source_workbench.adapters")
    adapters.build_source_workspace_service = lambda: pytest.fail("Unexpected legacy workspace route")
    monkeypatch.setitem(sys.modules, adapters.__name__, adapters)
    utilities = ModuleType("api.utils.api_utils")
    utilities.get_request_json = lambda: request.get_json()
    monkeypatch.setitem(sys.modules, "api.utils.api_utils", utilities)
    path = Path(__file__).resolve().parents[5] / "api/apps/restful_apis/source_workbench_api.py"
    spec = spec_from_file_location("linked_http_test", path)
    module = module_from_spec(spec)
    module.manager = Blueprint("linked_http_test", __name__)
    spec.loader.exec_module(module)
    module._linked = lambda: linked.service
    app = Quart(__name__)
    app.register_blueprint(module.manager)
    return app.test_client(), apps.current_user


async def test_http_source_update_to_confirmed_specification(linked, linked_client):
    client, current_user = linked_client
    base = "/source-workspaces/workspace/documents"
    source = (await (await client.post(base + "/import", json={"dataset_id": "dataset", "document_id": "source"})).get_json())["data"]
    target = (await (await client.post(base, json={"title": "ТЗ", "document_type": "technical_specification", "content": "# Хранение\n30 дней\n# Ручной раздел\nРучной текст\n"})).get_json())["data"]
    target_url = base + "/" + target["id"]
    target = (await (await client.post(target_url + "/dependencies", json={"source_id": source["id"], "expected_version": target["version"]})).get_json())["data"]
    linked.sources.marker, linked.sources.content = "new", "# Хранение\n90 дней\n"
    response = await client.post(target_url + "/refresh")
    assert response.status_code == 200
    target = (await response.get_json())["data"]
    assert "30 дней" in target["content"]
    response = await client.post(target_url + "/prepare", json={"expected_version": target["version"]})
    assert response.status_code == 202
    job = (await response.get_json())["data"]
    assert not ({"payload", "lease_token", "checkpoint"} & set(job))
    assert job["error"] is None
    await linked.service.execute(linked.repo.claim())
    target = (await (await client.get(target_url)).get_json())["data"]
    change = target["changes"][0]
    assert target["jobs"][0]["status"] == "done" and target["jobs"][0]["error"] is None
    assert change["status"] == "ready" and "30 дней" in target["content"]
    assert "sections" not in change["inputs"][0]
    response = await client.post(target_url + f"/changes/{change['id']}/decision", json={"action": "accept"})
    assert response.status_code == 200
    updated = (await response.get_json())["data"]
    assert "90 дней" in updated["content"] and "Ручной текст" in updated["content"]
    assert len(updated["revisions"]) == 2
    previous = await client.get(target_url + "/revisions/" + change["base_revision_id"])
    assert "30 дней" in "".join(item["text"] for item in (await previous.get_json())["data"]["sections"])
    current_user.id = "outsider"
    response = await client.get(target_url)
    assert response.status_code == 404 and (await response.get_json())["data"]["error_code"] == "NOT_FOUND"


async def test_http_invalid_payload_and_revision_ownership(linked_client):
    client, _ = linked_client
    base = "/source-workspaces/workspace/documents"
    response = await client.post(base, json=[])
    assert response.status_code == 422
    assert (await response.get_json())["data"]["error_code"] == "INVALID_INPUT"
    first = (await (await client.post(base, json={"title": "Первый"})).get_json())["data"]
    second = (await (await client.post(base, json={"title": "Второй"})).get_json())["data"]
    response = await client.get(f"{base}/{first['id']}/revisions/{second['current_revision_id']}")
    assert response.status_code == 404


async def test_http_implementation_permissions_conflict_and_revoke_reason(linked, linked_client):
    from api.db.db_models import SourceWorkspaceMember

    client, actor = linked_client
    base = "/source-workspaces/workspace/documents"
    document = await linked.service.create("author", "workspace", {"title": "ТЗ"})
    document = await linked.service.approve("author", "workspace", document["id"], {"expected_version": document["version"]})
    url = base + "/" + document["id"]
    payload = {"revision_id": document["approved_revision_id"], "expected_version": document["version"]}
    for role in ("reviewer", "reader"):
        SourceWorkspaceMember.replace(workspace_id="workspace", user_id="reviewer", role=role).execute()
        actor.id = "reviewer"
        response = await client.post(url + "/implementation", json=payload)
        assert response.status_code == 403
    actor.id = "outsider"
    assert (await client.post(url + "/implementation", json=payload)).status_code == 404
    actor.id = "author"
    assert (await client.post(url + "/implementation", json={**payload, "expected_version": 999})).status_code == 409
    response = await client.post(url + "/implementation", json=payload)
    assert response.status_code == 200
    current = (await response.get_json())["data"]
    repeated = await client.post(url + "/implementation", json=payload)
    assert (await repeated.get_json())["data"]["implementation_event_id"] == current["implementation_event_id"]
    revoke = {"expected_version": current["version"], "implementation_event_id": current["implementation_event_id"], "reason": ""}
    assert (await client.post(url + "/implementation/revoke", json=revoke)).status_code == 422
    assert (await client.post(url + "/implementation/revoke", json={**revoke, "reason": "Ошибка"})).status_code == 200
    assert (await client.get("/source-workspaces/workspace/document-overview")).status_code == 200


@pytest.mark.asyncio
async def test_http_manual_approval_never_replaces_text_before_reviewer_decision(linked, linked_client):
    client, current_user = linked_client
    await linked.service.set_member("author", "workspace", {"email": "reviewer@example.test", "role": "reviewer"})
    base = "/source-workspaces/workspace/documents"
    original = "# Исходный\nТекст до согласования\n"
    target = (await (await client.post(base, json={"title": "Согласуемое ТЗ", "change_policy": "approval", "content": original})).get_json())["data"]
    url = base + "/" + target["id"]
    target = (await (await client.put(url, json={"expected_version": target["version"], "approver_id": "reviewer"})).get_json())["data"]
    candidate = "# Исправленный\nРучная редакция\n"
    submitted = (await (await client.put(url, json={"expected_version": target["version"], "content": candidate})).get_json())["data"]
    assert submitted["content"] == original and len(submitted["revisions"]) == 1
    change = submitted["changes"][0]
    assert change["origin"] == "manual" and change["verification"]["verdict"] == "HUMAN_REQUIRED"
    assert (await client.post(url + "/changes/" + change["id"] + "/decision", json={"action": "accept"})).status_code == 403
    current_user.id = "reviewer"
    response = await client.post(url + "/changes/" + change["id"] + "/decision", json={"action": "accept", "comment": "Согласовано"})
    assert response.status_code == 200
    accepted = (await response.get_json())["data"]
    assert accepted["content"] == candidate and len(accepted["revisions"]) == 2
    response = await client.post(url + "/changes/" + change["id"] + "/decision", json={"action": "accept"})
    assert len((await response.get_json())["data"]["revisions"]) == 2
