"""Real session/ACL routes, disposable persistence and the review worker."""

import ast
from collections.abc import Awaitable, Callable, Iterable
from functools import wraps
import hashlib
import hmac
from importlib.util import module_from_spec, spec_from_file_location
import logging
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import ParamSpec, TypeVar

from itsdangerous.url_safe import URLSafeTimedSerializer
import pytest
from quart import Blueprint, Quart, current_app, g, request, session
from quart_auth import Unauthorized
from werkzeug.local import LocalProxy

from test.unit_test.api.apps.business_documents import test_business_document_service as service
from test.unit_test.api.apps.business_documents.test_structured_changes import PlanAdapter
from test.unit_test.technical_specifications.test_auth_boundary import _install_application_error_handlers
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.worker import BusinessDocumentWorker
from api.apps.business_documents.stream_events import BusinessDocumentStreamEvents
from api.db.db_models import BusinessDocumentCommand, BusinessDocumentJob, BusinessDocumentRevision, User, migrate_business_document_catalog
from api.db.services.access_group_service import AccessGroupService
from api.utils.api_utils import get_json_result
from common.constants import RetCode, StatusEnum

ROOT = Path(__file__).resolve().parents[5]
BASE = "/api/v1/business-documents"


@pytest.fixture
def database():
    yield from service.database.__wrapped__()


@pytest.fixture
def authenticated_app(database, monkeypatch):
    users = {}
    for actor, role in ((service.AUTHOR, "AUTHOR_CREATOR"), ("reader", "READER"), ("other-author", "AUTHOR_CREATOR"), ("moderator", "MODERATOR_CREATOR")):
        users[actor] = User.create(
            id=actor, nickname=actor, email=actor + "@invalid.example", access_token="synthetic-credential-" + actor + "-" * 40, business_document_role=role, status=StatusEnum.VALID.value
        )

    def query_users(**filters):
        query = User.select()
        for field, value in filters.items():
            query = query.where(getattr(User, field) == value)
        return list(query)

    path = ROOT / "api/apps/__init__.py"
    names = {"_normalize_auth_types", "_load_user_from_session", "_load_user", "login_required"}
    functions = [node for node in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    namespace = {
        "logging": logging,
        "os": os,
        "time": time,
        "hashlib": hashlib,
        "hmac": hmac,
        "wraps": wraps,
        "Callable": Callable,
        "Iterable": Iterable,
        "Awaitable": Awaitable,
        "P": ParamSpec("P"),
        "T": TypeVar("T"),
        "request": request,
        "g": g,
        "session": session,
        "current_app": current_app,
        "QuartAuthUnauthorized": Unauthorized,
        "AUTH_JWT": "JWT",
        "AUTH_API": "API",
        "AUTH_BETA": "BETA",
        "DEFAULT_AUTH_TYPES": ("JWT", "API"),
        "StatusEnum": StatusEnum,
        "UserService": SimpleNamespace(query=query_users),
        "APIToken": SimpleNamespace(query=lambda **_: []),
        "Serializer": URLSafeTimedSerializer,
        "settings": SimpleNamespace(get_secret_key=lambda: "synthetic-session-only-secret"),
        "RetCode": RetCode,
        "get_json_result": get_json_result,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    apps = sys.modules["api.apps"]
    monkeypatch.setattr(apps, "current_user", LocalProxy(namespace["_load_user"]), raising=False)
    monkeypatch.setattr(apps, "login_required", namespace["login_required"], raising=False)
    monkeypatch.setattr(AccessGroupService, "effective_policy", staticmethod(lambda _: {"dataset_ids": set(), "sections": {"business_documents"}}))
    name = "api.apps.restful_apis.business_document_authenticated_test"
    spec = spec_from_file_location(name, ROOT / "api/apps/restful_apis/business_document_api.py")
    module = module_from_spec(spec)
    module.manager = Blueprint("business_document_api", name)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(module, "thread_pool_exec", inline)
    monkeypatch.setattr(module, "wake_business_document_worker", lambda: None)
    app = Quart(__name__)
    app.secret_key = "synthetic-disposable-review-http"
    app.register_blueprint(module.manager, url_prefix="/api/v1")
    _install_application_error_handlers(app)
    return app, users


async def sign_in(client, user):
    async with client.session_transaction() as cookie:
        cookie["_user_id"] = user.id
        cookie["_token_fingerprint"] = hashlib.sha256(user.access_token.encode()).hexdigest()


@pytest.mark.p0
@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["events", "events/stream"])
@pytest.mark.parametrize("actor", [service.AUTHOR, "reader", "moderator"])
async def test_job_progress_uses_document_tenant_for_authorized_shared_reads(authenticated_app, database, monkeypatch, suffix, actor):
    app, users = authenticated_app
    document = service._request_and_complete_draft(service._create())
    job = BusinessDocumentJob.get(BusinessDocumentJob.document_id == document["document_id"])
    assert job.tenant_id != actor
    assert BusinessDocumentStreamEvents.append(job.id, "stage", {"stage": "progress-evidence"})
    client = app.test_client()
    await sign_in(client, users[actor])
    statements = []
    execute = database.execute_sql

    def record(sql, *args, **kwargs):
        statements.append(sql)
        return execute(sql, *args, **kwargs)

    monkeypatch.setattr(database, "execute_sql", record)
    response = await client.get(f"{BASE}/{document['document_id']}/jobs/{job.id}/{suffix}")
    assert response.status_code == 200, await response.get_data()
    if suffix == "events":
        data = (await response.get_json())["data"]
        assert data["job_id"] == job.id and data["status"] == "COMPLETED"
        assert any(event["payload"].get("stage") == "progress-evidence" for event in data["events"])
    else:
        assert response.mimetype == "text/event-stream"
        assert b"progress-evidence" in await response.get_data()
    # Includes session authentication and, for SSE, readiness plus the first batch.
    # Loading the document projection here adds at least 21 unrelated queries.
    assert len(statements) <= 10, statements


@pytest.mark.p0
@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["events", "events/stream"])
@pytest.mark.parametrize("denied", ["anonymous", "revoked-section", "wrong-document", "wrong-job", "wrong-job-tenant"])
async def test_job_progress_does_not_expose_events_outside_authorized_scope(authenticated_app, monkeypatch, suffix, denied):
    app, users = authenticated_app
    document = service._request_and_complete_draft(service._create())
    job = BusinessDocumentJob.get(BusinessDocumentJob.document_id == document["document_id"])
    assert BusinessDocumentStreamEvents.append(job.id, "stage", {"stage": "private-progress-evidence"})
    document_id, job_id = document["document_id"], job.id
    client = app.test_client()
    if denied != "anonymous":
        await sign_in(client, users[service.AUTHOR])
    if denied == "revoked-section":
        monkeypatch.setattr(AccessGroupService, "effective_policy", staticmethod(lambda _: {"dataset_ids": set(), "sections": set()}))
    elif denied == "wrong-document":
        document_id = service._create(title="Другой документ для проверки прогресса")["document_id"]
    elif denied == "wrong-job":
        job_id = "missing-job"
    elif denied == "wrong-job-tenant":
        BusinessDocumentJob.update(tenant_id="foreign-tenant").where(BusinessDocumentJob.id == job.id).execute()
    response = await client.get(f"{BASE}/{document_id}/jobs/{job_id}/{suffix}")
    assert response.status_code == (401 if denied == "anonymous" else 403 if denied == "revoked-section" else 404)
    assert b"private-progress-evidence" not in await response.get_data()


def reviewed_document(disposition="NO_CHANGE", text="Проверить требования"):
    document = service._request_and_complete_draft(service._create())
    base = document["current_revision"]
    service.document_commands.execute(
        service.TENANT, service.AUTHOR, document["document_id"], service._command(document, "ADD_COMMENT", {"revision_id": base["revision_id"], "section_id": None, "text": text, "anchor": None})
    )
    return service._complete_review_assessment(service.document_queries.get_document(document["document_id"], service.AUTHOR), comment_disposition=disposition)


@pytest.mark.p0
@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["anonymous", "missing-fingerprint", "wrong-fingerprint", "nonstring-fingerprint", "rotated", "revoked", "short-token", "invalidated"])
async def test_invalid_session_rejects_before_command_parsing_and_enqueue(authenticated_app, invalid):
    app, users = authenticated_app
    client = app.test_client()
    author = users[service.AUTHOR]
    if invalid != "anonymous":
        await sign_in(client, author)
        if invalid.endswith("fingerprint"):
            async with client.session_transaction() as cookie:
                if invalid == "missing-fingerprint":
                    del cookie["_token_fingerprint"]
                else:
                    cookie["_token_fingerprint"] = 123 if invalid == "nonstring-fingerprint" else "wrong"
        elif invalid == "revoked":
            User.update(status="0").where(User.id == author.id).execute()
        else:
            token = "x" if invalid == "short-token" else "INVALID_" + "x" * 40 if invalid == "invalidated" else "rotated-" + "x" * 40
            User.update(access_token=token).where(User.id == author.id).execute()
    response = await client.post(BASE + "/unknown/commands", json=[])
    assert response.status_code == 401, await response.get_json()
    assert BusinessDocumentCommand.select().count() == BusinessDocumentJob.select().count() == BusinessDocumentRevision.select().count() == 0


@pytest.mark.p0
@pytest.mark.asyncio
@pytest.mark.parametrize("actor,allowed", [("reader", False), ("other-author", False), ("moderator", True)])
async def test_shared_catalog_and_cross_owner_mutation_contract(authenticated_app, actor, allowed):
    app, users = authenticated_app
    document = reviewed_document()
    client = app.test_client()
    await sign_in(client, users[actor])
    migrate_business_document_catalog()
    catalog = await client.get(BASE + "/catalog")
    assert catalog.status_code == 200 and (await catalog.get_json())["data"]["items"]
    listing = await client.get(BASE)
    assert listing.status_code == 200, await listing.get_json()
    (listed,) = [item for item in (await listing.get_json())["data"]["items"] if item["document_id"] == document["document_id"]]
    assert listed["permissions"]["read"] and listed["permissions"]["edit"] is allowed
    response = await client.get(BASE + "/" + document["document_id"])
    assert response.status_code == 200, await response.get_json()
    before = BusinessDocumentJob.select().count(), BusinessDocumentRevision.select().count(), BusinessDocumentCommand.select().count()
    response = await client.post(
        BASE + "/" + document["document_id"] + "/commands", json=service._command(document, "PREPARE_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]})
    )
    assert response.status_code == (202 if allowed else 403), await response.get_json()
    if allowed:
        job = BusinessDocumentJob.get_by_id((await response.get_json())["data"]["job_id"])
        assert job.tenant_id == service.TENANT and job.payload["requested_by_actor_id"] == actor
    else:
        assert (BusinessDocumentJob.select().count(), BusinessDocumentRevision.select().count(), BusinessDocumentCommand.select().count()) == before


@pytest.mark.p0
@pytest.mark.asyncio
async def test_real_section_mapping_denies_before_mutation(authenticated_app, monkeypatch):
    app, users = authenticated_app
    client = app.test_client()
    await sign_in(client, users[service.AUTHOR])
    monkeypatch.setattr(AccessGroupService, "effective_policy", staticmethod(lambda _: {"dataset_ids": set(), "sections": set()}))
    response = await client.post(BASE, json=[])
    assert response.status_code == 403, await response.get_json()
    assert BusinessDocumentCommand.select().count() == BusinessDocumentJob.select().count() == 0


@pytest.mark.p0
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["CONFIRM_PREPARED_CHANGES", "DISCARD_PREPARED_CHANGES", "REVOKE_BEFORE_CONFIRM", "ROLE_REVOKED_BEFORE_CONFIRM"])
async def test_real_session_http_worker_no_change_and_revocation(authenticated_app, action):
    app, users = authenticated_app
    document = reviewed_document()
    base = document["current_revision"]
    revisions = BusinessDocumentRevision.select().count()
    client = app.test_client()
    author = users[service.AUTHOR]
    await sign_in(client, author)
    path = BASE + "/" + document["document_id"]
    response = await client.post(path + "/commands", json=service._command(document, "PREPARE_CHANGES", {"base_revision_id": base["revision_id"]}))
    assert response.status_code == 202, await response.get_json()
    requested = (await response.get_json())["data"]
    plan = {
        "schema_version": "1",
        "base_revision_id": base["revision_id"],
        "source_state_version": requested["state_version"],
        "acknowledged_no_change_event_ids": [document["protocol"]["comments"][0]["source_event_id"]],
        "operations": [],
    }
    adapter = PlanAdapter(plan, None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    assert BusinessDocumentJob.get_by_id(requested["job_id"]).status == "COMPLETED"
    preview = await client.get(path + "/change-previews/" + requested["job_id"])
    assert preview.status_code == 200 and (await preview.get_json())["data"]["sections"] == []
    current = service.document_queries.get_document(document["document_id"], service.AUTHOR)
    if action == "REVOKE_BEFORE_CONFIRM":
        User.update(access_token="rotated-after-prepare-" + "x" * 40).where(User.id == author.id).execute()
    elif action == "ROLE_REVOKED_BEFORE_CONFIRM":
        User.update(business_document_role="READER").where(User.id == author.id).execute()
    revoked = action in {"REVOKE_BEFORE_CONFIRM", "ROLE_REVOKED_BEFORE_CONFIRM"}
    response = await client.post(path + "/commands", json=service._command(current, "CONFIRM_PREPARED_CHANGES" if revoked else action, {"job_id": requested["job_id"]}))
    assert response.status_code == (401 if action == "REVOKE_BEFORE_CONFIRM" else 403 if revoked else 200), await response.get_json()
    current = service.document_queries.get_document(document["document_id"], service.AUTHOR)
    assert current["current_revision"]["revision_id"] == base["revision_id"]
    assert BusinessDocumentRevision.select().count() == revisions
    assert (current["change_preview"] is not None) == revoked


@pytest.mark.p0
@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["PREPARE_CHANGES", "APPLY_CHANGES"])
async def test_http_worker_rejects_contradictory_acknowledgement_and_logs_once(authenticated_app, caplog, command):
    app, users = authenticated_app
    secret = "confidential-review-canary"
    document = reviewed_document("CONFIRMED_CHANGE", text=secret)
    client = app.test_client()
    await sign_in(client, users[service.AUTHOR])
    path = BASE + "/" + document["document_id"]
    base = document["current_revision"]
    revisions = BusinessDocumentRevision.select().count()
    response = await client.post(path + "/commands", json=service._command(document, command, {"base_revision_id": base["revision_id"]}))
    assert response.status_code == 202, await response.get_json()
    requested = (await response.get_json())["data"]
    plan = {
        "schema_version": "1",
        "base_revision_id": base["revision_id"],
        "source_state_version": requested["state_version"],
        "acknowledged_no_change_event_ids": [document["protocol"]["comments"][0]["source_event_id"]],
        "operations": [],
    }
    adapter = PlanAdapter(plan, None)
    worker = BusinessDocumentWorker(ai=BusinessDocumentAI(adapter))
    assert worker.run_once() and not worker.run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "DEAD" and job.error["code"] == "COMMENT_NO_CHANGE_NOT_CONFIRMED"
    assert job.result["recovery"]["candidate"] is None
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES"]
    assert BusinessDocumentRevision.select().count() == revisions
    current = await client.get(path)
    assert (await current.get_json())["data"]["current_revision"]["revision_id"] == base["revision_id"]
    errors = [record.getMessage() for record in caplog.records if "Business document job failed" in record.getMessage()]
    assert len(errors) == 1 and job.correlation_id in errors[0] and "COMMENT_NO_CHANGE_NOT_CONFIRMED" in errors[0]
    assert all(secret not in message for message in errors)
