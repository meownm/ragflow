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
from test.unit_test.technical_specifications.test_auth_boundary import _install_application_error_handlers
from api.apps.business_documents.stream_events import BusinessDocumentStreamEvents
from api.db.db_models import BusinessDocumentJob, User
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
        document_id = service._create(title="Р”СЂСѓРіРѕР№ РґРѕРєСѓРјРµРЅС‚ РґР»СЏ РїСЂРѕРІРµСЂРєРё РїСЂРѕРіСЂРµСЃСЃР°")["document_id"]
    elif denied == "wrong-job":
        job_id = "missing-job"
    elif denied == "wrong-job-tenant":
        BusinessDocumentJob.update(tenant_id="foreign-tenant").where(BusinessDocumentJob.id == job.id).execute()
    response = await client.get(f"{BASE}/{document_id}/jobs/{job_id}/{suffix}")
    assert response.status_code == (401 if denied == "anonymous" else 403 if denied == "revoked-section" else 404)
    assert b"private-progress-evidence" not in await response.get_data()
