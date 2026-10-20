"""Alignment HTTP consumers over disposable repository state, no model network."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from quart import Blueprint, Quart, request

from api.source_workbench.errors import SourceWorkspaceError
from api.technical_specifications.alignment_application import AlignmentApplication
from api.technical_specifications.application import Specifications

BASE = "/source-workspaces/workspace/technical-specifications"


@pytest.fixture
def alignment_http(store, monkeypatch):
    state = SimpleNamespace(calls=0, allowed=True, pages=[])

    async def run(method, *args, **kwargs):
        return method(*args, **kwargs)

    class Sources:
        def list(self, actor, query, page):
            state.pages.append((query, page))
            return {"items": [{"document_id": "bt", "title": "БТ", "revision_number": 2}], "page": page, "has_more": False}

        def read_many(self, actor, ids, **options):
            if not state.allowed:
                raise SourceWorkspaceError("PERMISSION_DENIED", "Источник недоступен", 403)
            return (
                {"bt": {"document_id": "bt", "revision_id": "r1", "agreement_event_id": "agreed1", "revision": "proof1", "content_hash": "hash", "body_markdown": "Отмена бесплатна."}}
                if ids == ["bt"]
                else {}
            )

        def validate(self, actor, references, **options):
            return self.read_many(actor, [row["document_id"] for row in references])

    class AI:
        async def check(self, actor, document, basis, *, budget, on_reserved):
            state.calls += 1
            return {"quality_status": "PASS", "report_status": "COMPLETE", "evidence_status": "INCOMPLETE", "qualifies": False}

    apps = ModuleType("api.apps")
    apps.current_user = SimpleNamespace(id="author")
    apps.login_required = lambda method: method
    monkeypatch.setitem(sys.modules, apps.__name__, apps)
    runtime = ModuleType("api.technical_specifications.runtime")
    runtime.build_specifications = lambda: Specifications(store, AI(), run, alignment=AlignmentApplication(store, Sources(), AI(), run))
    monkeypatch.setitem(sys.modules, runtime.__name__, runtime)
    utilities = ModuleType("api.utils.api_utils")
    utilities.get_request_json = lambda: request.get_json()
    monkeypatch.setitem(sys.modules, utilities.__name__, utilities)
    path = Path(__file__).resolve().parents[3] / "api/apps/restful_apis/technical_specifications_api.py"
    spec = spec_from_file_location("alignment_http_test", path)
    module = module_from_spec(spec)
    module.manager = Blueprint("alignment_http_test", __name__)
    spec.loader.exec_module(module)
    app = Quart(__name__)
    app.register_blueprint(module.manager)
    return app.test_client(), apps.current_user, state


@pytest.mark.asyncio
async def test_catalogue_search_page_and_editor_authorization(alignment_http):
    client, actor, state = alignment_http
    response = await client.get(BASE + "/business-sources", query_string={"page": "2", "query": "Отмена"})
    assert response.status_code == 200 and state.pages == [("Отмена", 2)]
    for params in ({"page": "0"}, {"page": "invalid"}, {"page": "1001"}, {"query": "x" * 256}):
        assert (await client.get(BASE + "/business-sources", query_string=params)).status_code == 422
    actor.id = "reader"
    assert (await client.get(BASE + "/business-sources")).status_code == 403


@pytest.mark.asyncio
async def test_http_complete_semantics_never_overrides_incomplete_evidence(alignment_http):
    client, actor, state = alignment_http
    document = (await (await client.post(BASE, json={"title": "ТЗ"})).get_json())["data"]
    url = BASE + "/" + document["id"] + "/alignment"
    payload = {"version": document["version"], "request_id": "request1", "business_document_ids": ["bt"]}
    response = await client.post(url, json=payload)
    assert response.status_code == 200
    result = (await response.get_json())["data"]
    assert not result["checks"][0]["qualifies"]
    assert result["checks"][0]["result"]["quality_status"] == "PASS"
    assert (await client.post(url, json=payload)).status_code == 200 and state.calls == 1
    normal = (await (await client.get(BASE + "/" + document["id"])).get_json())["data"]
    assert "alignment" not in normal
    actor.id = "reader"
    assert (await client.post(url, json=payload)).status_code == 403
    assert (await client.get(url)).status_code == 200
    state.allowed = False
    hidden = (await (await client.get(url)).get_json())["data"]["checks"][0]
    assert hidden == {"id": "request1", "current": False, "qualifies": False, "reason_code": "SOURCE_UNAVAILABLE"}
    actor.id = "stranger"
    assert (await client.get(url)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, [], {"version": 1, "request_id": "r", "business_document_ids": []}, {"version": 1, "request_id": "r", "business_document_ids": ["bt", "bt"]}])
async def test_malformed_alignment_never_calls_model(alignment_http, payload):
    client, _actor, state = alignment_http
    document = (await (await client.post(BASE, json={"title": "ТЗ"})).get_json())["data"]
    response = await client.post(BASE + "/" + document["id"] + "/alignment", json=payload)
    assert response.status_code in (400, 422)
    assert state.calls == 0
