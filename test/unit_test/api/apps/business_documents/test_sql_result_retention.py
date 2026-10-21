"""Result lifetime is enforced on retained data, not just a status label."""

import asyncio
import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_exports import (
    ACTOR, Runs, command, export, project as stored_project, result as stored_result,
    source as stored_source,
)
from api.db.db_models import BusinessDocumentSqlQueryRun as Run
from api.db.db_models import BusinessDocumentSqlQueryArtifact as Artifact, BusinessDocumentSqlAgentCommand as Command
from api.apps.business_documents import sql_query_retention as retention
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Agents, _stable_hash
from api.apps.business_documents import sql_query_schema
from business_documents.sql_query.result_retention import RESULT_RETENTION_MS, result_expires_at
from business_documents.application.errors import BusinessDocumentError
from common.time_utils import current_timestamp
from test.unit_test.api.apps.business_documents.test_business_document_api import route_app as stored_routes


def result(project, **changes):
    created_at = changes.pop("create_time", None)
    run = stored_result(project, **changes)
    if created_at is not None:
        Run.update(create_time=created_at).where(Run.id == run.id).execute()
    return Run.get_by_id(run.id)


@pytest.fixture
def project():
    yield from stored_project.__wrapped__()


@pytest.fixture
def source(project, monkeypatch):
    yield from stored_source.__wrapped__(project, monkeypatch)


@pytest.fixture
def route_app(monkeypatch):
    yield from stored_routes.__wrapped__(monkeypatch)


@pytest.mark.parametrize("consumer", ["preview", "export"])
def test_old_ready_rows_expire_before_reading_without_a_prior_cleanup(source, consumer):
    project, _, _ = source
    result(project, create_time=current_timestamp() - 31 * 24 * 60 * 60 * 1000)
    with pytest.raises(BusinessDocumentError) as raised:
        if consumer == "preview":
            Runs.preview(project.tenant_id, ACTOR, project.id, "result", access_role="READER")
        else:
            export(project, command("RESULT"))
    assert (raised.value.code, raised.value.status) == ("SQL_RESULT_EXPIRED", 410)
    run = Run.get_by_id("result")
    assert run.status == "EXPIRED" and run.rows == [] and run.columns == [] and run.checks == {}


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "at", "after"])
def test_exact_deadline_is_exclusive_and_reads_do_not_slide_it(source, monkeypatch, offset):
    project, _, _ = source
    deadline = current_timestamp()
    created = deadline - RESULT_RETENTION_MS
    result(project, create_time=created)
    monkeypatch.setattr(retention, "current_timestamp", lambda: deadline + offset)
    if offset < 0:
        preview = Runs.preview(project.tenant_id, ACTOR, project.id, "result")
        assert preview["expires_at"] == deadline and preview["rows"]
        assert Run.get_by_id("result").create_time == created
    else:
        with pytest.raises(BusinessDocumentError, match="expired"):
            Runs.preview(project.tenant_id, ACTOR, project.id, "result")
        assert Run.get_by_id("result").status == "EXPIRED"


def test_cleanup_cascades_to_fresh_derivatives_and_result_copies_but_preserves_approved_history(source):
    project, _, _ = source
    now = current_timestamp()
    result(project, create_time=now - RESULT_RETENTION_MS)
    result(project, id="python-result", kind="PYTHON", source_run_id="result", create_time=now)
    result(project, id="lookup-result", kind="LOOKUP", source_run_id="result", create_time=now)
    result(project, id="fresh-result", create_time=now)
    result(project, id="other-tenant", tenant_id="foreign-tenant", create_time=now - RESULT_RETENTION_MS)
    result(project, id="other-project", project_id="foreign-project", create_time=now - RESULT_RETENTION_MS)
    saved = {row.id: dict(row.__data__) for row in Artifact.select()}
    for revision, (identity, kind, payload) in enumerate([
        ("draft-conclusion", "CONCLUSION", {"source_run_id": "result", "status": "DRAFT", "text": "private draft"}),
        ("confirmed-conclusion", "CONCLUSION", {"source_run_id": "result", "status": "CONFIRMED", "text": "private approved draft"}),
        ("fresh-conclusion", "CONCLUSION", {"source_run_id": "fresh-result", "text": "keep fresh"}),
        ("approved-document", "DOCUMENT", {"run_id": "result", "confirmed_conclusion": {"text": "immutable report"}}),
    ], start=10):
        Artifact.create(id=identity, project_id=project.id, tenant_id=project.tenant_id, kind=kind, revision=revision,
                        payload=payload, content_hash=_stable_hash(payload), source_proposal_id="synthetic", accepted_by=ACTOR)
    responses = {
        "conclusion-copy": {"proposal_id": "draft-conclusion", "text": "private draft"},
        "old-project-copy": {"latest_run": {"id": "result"}, "latest_conclusion": {"payload": {"text": "private draft"}}},
        "derived-copy": {"run_id": "python-result", "source_run_id": "result"},
        "fresh-copy": {"run_id": "fresh-result", "status": "READY"},
        "unrelated-command": {"state_version": 1, "artifact_id": "q" * 32, "text": "result", "parameters": [{"name": "business_value", "value": "result"}]},
    }
    for identity, response in responses.items():
        Command.create(id=identity, project_id=project.id, tenant_id=project.tenant_id, idempotency_key=identity,
                       request_hash="same-hash", response=response)
    assert retention.expire_project_results(project.tenant_id, project.id, now_ms=now) == 3
    for identity in ("result", "python-result", "lookup-result"):
        run = Run.get_by_id(identity)
        assert (run.status, run.rows, run.columns, run.checks, run.result_bytes) == ("EXPIRED", [], [], {}, 0)
    assert all(Run.get_by_id(identity).rows for identity in ("fresh-result", "other-tenant", "other-project"))
    assert Artifact.get_or_none(Artifact.id == "draft-conclusion") is None
    assert Artifact.get_or_none(Artifact.id == "confirmed-conclusion") is None
    assert Artifact.get_by_id("fresh-conclusion").payload["text"] == "keep fresh"
    assert Artifact.get_by_id("approved-document").payload == {"run_id": "result", "confirmed_conclusion": {"text": "immutable report"}}
    assert all(Artifact.get_by_id(identity).__data__ == before for identity, before in saved.items())
    for identity in ("conclusion-copy", "old-project-copy", "derived-copy"):
        stored = Command.get_by_id(identity)
        assert stored.response == retention.EXPIRED_RESPONSE and stored.request_hash == "same-hash"
        with pytest.raises(BusinessDocumentError) as raised:
            Agents._command_replay(project.tenant_id, project.id, identity, "same-hash")
        assert (raised.value.code, raised.value.status) == ("SQL_RESULT_EXPIRED", 410)
        with pytest.raises(BusinessDocumentError) as conflict:
            Agents._command_replay(project.tenant_id, project.id, identity, "different-hash")
        assert conflict.value.code == "SQL_AGENT_IDEMPOTENCY_CONFLICT"
    for identity in ("fresh-copy", "unrelated-command"):
        assert Agents._command_replay(project.tenant_id, project.id, identity, "same-hash") == responses[identity]
    assert retention.expire_project_results(project.tenant_id, project.id, now_ms=now) == 0


@pytest.mark.parametrize("identity", ["foreign-tenant", "foreign-owner"])
def test_denied_read_does_not_clean_another_owners_data(source, identity):
    project, _, _ = source
    result(project, create_time=current_timestamp() - RESULT_RETENTION_MS)
    with pytest.raises(BusinessDocumentError) as raised:
        Runs.preview("foreign" if identity == "foreign-tenant" else project.tenant_id,
                     "foreign" if identity == "foreign-owner" else ACTOR, project.id, "result", access_role="READER")
    assert raised.value.status == (404 if identity == "foreign-tenant" else 403)
    assert Run.get_by_id("result").status == "READY" and Run.get_by_id("result").rows


def test_deadline_is_rechecked_after_catalog_wait_before_loading_rows(source, monkeypatch):
    project, _, _ = source
    deadline = current_timestamp()
    result(project, create_time=deadline - RESULT_RETENTION_MS)
    clock = [deadline - 1]
    monkeypatch.setattr(retention, "current_timestamp", lambda: clock[0])
    load = sql_query_schema.BusinessDocumentSqlQuerySchemaService.load_entities

    async def delayed(*args, **kwargs):
        answer = await load(*args, **kwargs)
        clock[0] = deadline
        return answer

    monkeypatch.setattr(sql_query_schema.BusinessDocumentSqlQuerySchemaService, "load_entities", delayed)
    with pytest.raises(BusinessDocumentError) as raised:
        Runs.preview(project.tenant_id, ACTOR, project.id, "result")
    assert raised.value.code == "SQL_RESULT_EXPIRED"
    assert Run.get_by_id("result").rows == []


def test_project_projection_expires_old_result_and_exposes_inherited_deadlines(source):
    project, _, _ = source
    now = current_timestamp()
    result(project, create_time=now - RESULT_RETENTION_MS)
    fresh = result(project, id="fresh-result", create_time=now)
    result(project, id="fresh-derived", kind="PYTHON", source_run_id=fresh.id, create_time=now + 10_000)
    projection = Agents.get_project(project.tenant_id, ACTOR, project.id)
    assert next(run for run in projection["runs"] if run["id"] == "result")["status"] == "EXPIRED"
    assert next(run for run in projection["runs"] if run["id"] == fresh.id)["expires_at"] == result_expires_at(now)
    assert projection["derived_runs"][0]["expires_at"] == result_expires_at(now)


def test_background_pass_has_a_batch_limit_and_does_not_select_raw_rows(source, monkeypatch):
    project, _, _ = source
    now = current_timestamp()
    result(project, create_time=now - RESULT_RETENTION_MS)
    result(project, id="other-due", project_id="other-project", create_time=now - RESULT_RETENTION_MS)
    statements = []
    execute = Run._meta.database.execute_sql

    def observe(query, *args, **kwargs):
        statements.append(query)
        return execute(query, *args, **kwargs)

    monkeypatch.setattr(Run._meta.database, "execute_sql", observe)
    assert retention.expire_due_results(now_ms=now, batch_size=1) == 1
    monkeypatch.setattr(Run._meta.database, "execute_sql", execute)
    selects = [query for query in statements if query.startswith("SELECT") and "business_document_sql_query_run" in query]
    assert selects and all('"rows"' not in query for query in selects)
    assert Run.select().where(Run.status == "READY").count() == 1
    assert retention.expire_due_results(now_ms=now, batch_size=1) == 1
    assert retention.expire_due_results(now_ms=now, batch_size=1) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["preview", "export"])
async def test_real_http_reads_change_from_available_to_expired_without_caching(source, route_app, monkeypatch, consumer):
    project, _, _ = source
    deadline = current_timestamp() + 1000
    result(project, create_time=deadline - RESULT_RETENTION_MS)
    for table in Agents.model_tables():
        if "tenant_id" in table._meta.fields:
            table.update(tenant_id=ACTOR).where(table.tenant_id == project.tenant_id).execute()
    app, module = route_app
    monkeypatch.setattr(module.current_user, "id", ACTOR)
    clock = [deadline - 1]
    monkeypatch.setattr(retention, "current_timestamp", lambda: clock[0])
    database = Run._meta.database
    connection = database.connection()

    async def dispatch(function, *args, **kwargs):
        def invoke():
            database._state.set_connection(connection)
            try:
                return function(*args, **kwargs)
            finally:
                database._state.reset()
        return await asyncio.to_thread(invoke)

    monkeypatch.setattr(module, "thread_pool_exec", dispatch)
    client = app.test_client()

    async def read():
        prefix = f"/business-documents/sql-query/projects/{project.id}"
        if consumer == "preview":
            return await client.get(prefix + "/runs/result/preview")
        return await client.post(prefix + "/export", json=command("RESULT"))

    available = await read()
    assert available.status_code == 200, await available.get_data()
    assert available.headers["Cache-Control"] == "no-store"
    clock[0] = deadline
    expired = await read()
    assert expired.status_code == 410, await expired.get_data()
    assert expired.headers["Cache-Control"] == "no-store"
    assert (await expired.get_json())["data"]["error_code"] == "SQL_RESULT_EXPIRED"
    assert Run.get_by_id("result").rows == []
    assert (await read()).status_code == 410


def test_expiring_an_older_derived_result_does_not_expire_its_fresh_parent(source):
    project, _, _ = source
    now = current_timestamp()
    result(project, create_time=now)
    result(project, id="old-derived", kind="PYTHON", source_run_id="result", create_time=now - RESULT_RETENTION_MS)
    assert retention.expire_project_results(project.tenant_id, project.id, now_ms=now) == 1
    assert Run.get_by_id("old-derived").status == "EXPIRED"
    assert Run.get_by_id("result").status == "READY"
    assert Runs.preview(project.tenant_id, ACTOR, project.id, "result")["rows"]


def test_background_cleanup_catches_a_late_child_of_an_already_expired_parent(source):
    project, _, _ = source
    now = current_timestamp()
    result(project, create_time=now - RESULT_RETENTION_MS)
    assert retention.expire_project_results(project.tenant_id, project.id, now_ms=now) == 1
    result(project, id="late-derived", kind="PYTHON", source_run_id="result")
    assert retention.expire_due_results(now_ms=current_timestamp()) == 1
    assert Run.get_by_id("late-derived").status == "EXPIRED"
    assert Run.get_by_id("late-derived").rows == []
