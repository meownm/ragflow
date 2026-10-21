"""Real SQL HTTP handlers and services reject a controlled reader identity.

Authentication identity and the thread dispatch are controlled; this does not
qualify login/session middleware or pooled PostgreSQL transactions.
"""

from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_api import route_app as isolated_routes
from test.unit_test.api.apps.business_documents.test_sql_reader_authorization import ATTEMPTS, ACTOR, TENANT, Agents, database_snapshot, project as accepted_project


@pytest.fixture
def route_app(monkeypatch):
    yield from isolated_routes.__wrapped__(monkeypatch)


@pytest.fixture
def project():
    yield from accepted_project.__wrapped__()


# Explicit reviewed HTTP paths, independently matched to the service inventory.
ROUTES = {
    "revise_question": ("POST", "/question"),
    "update_project": ("PATCH", ""),
    "copy_project": ("POST", "/copy"),
    "compile_project": ("POST", "/compilations"),
    "validate_manual_query": ("POST", "/manual-sql/validate"),
    "save_manual_query": ("POST", "/manual-sql"),
    "restore_query": ("POST", "/query-history/restore"),
    "request_agent": ("POST", "/agent-jobs"),
    "decide_proposal": ("POST", "/proposals/proposal/decision"),
    "create_thread": ("POST", "/threads"),
    "save_draft": ("PUT", "/draft"),
    "reply": ("POST", "/threads/thread/messages"),
    "reattach": ("POST", "/threads/thread/reattach"),
    "request_change": ("POST", "/threads/thread/proposal"),
    "cancel_change": ("POST", "/revision-proposals/cancel"),
    "reopen_schema": ("POST", "/schema/reopen"),
    "decide_change": ("POST", "/revision-proposals/proposal/decision"),
    "configure_connection": ("PUT", "/connection"),
    "check_connection": ("POST", "/connection/check"),
    "run": ("POST", "/runs"),
    "complete": ("POST", "/complete"),
    "cancel_result": ("POST", "/runs/result/cancel"),
    "run_python": ("POST", "/runs/result/python"),
    "run_lookup": ("POST", "/runs/result/lookup"),
    "propose_conclusion": ("POST", "/runs/result/conclusion"),
    "confirm_conclusion": ("POST", "/runs/result/conclusion/confirm"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", ATTEMPTS, ids=[item[1] for item in ATTEMPTS])
async def test_http_mutation_rechecks_reader_role_before_services_have_effects(project, route_app, monkeypatch, attempt):
    app, module = route_app
    assert set(ROUTES) == {item[1] for item in ATTEMPTS}
    for table in Agents.model_tables():
        if "tenant_id" in table._meta.fields:
            table.update(tenant_id=ACTOR).where(table.tenant_id == TENANT).execute()
    monkeypatch.setattr(module.current_user, "id", ACTOR)
    monkeypatch.setattr(module.current_user, "business_document_role", "READER", raising=False)

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(module, "thread_pool_exec", inline)
    before = database_snapshot()
    _, service_method, _, payload = attempt
    method, suffix = ROUTES[service_method]
    response = await app.test_client().open(f"/business-documents/sql-query/projects/{project}{suffix}", method=method, json=deepcopy(payload))
    assert response.status_code == 403, (service_method, await response.get_json())
    assert database_snapshot() == before
