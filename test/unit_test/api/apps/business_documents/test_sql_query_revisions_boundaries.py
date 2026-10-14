"""Revision workspaces preserve accepted data when review commands are rejected."""

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_revisions import (
    ACTOR,
    TENANT,
    SQL,
    project as revision_project,
)
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Agents
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService as Service
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import (
    BusinessDocumentJob,
    BusinessDocumentSqlAgentCommand,
    BusinessDocumentSqlAgentProposal,
    BusinessDocumentSqlQueryArtifact,
    BusinessDocumentSqlQueryDraft,
    BusinessDocumentSqlQueryProject,
    BusinessDocumentSqlQueryRun,
    BusinessDocumentSqlQueryThread,
)
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def project():
    yield from revision_project.__wrapped__()


def thread_command(**changes):
    return {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "thread-boundary", "target": "QUERY", "anchor": {"kind": "WHOLE"}, "text": "Clarify the query", **changes}


def draft_command(**changes):
    return {"schema_version": "1", "expected_state_version": 1, "expected_draft_version": 0, "steps": [{"title": "Draft", "sql": SQL, "parameters": "[]"}], **changes}


@pytest.mark.parametrize(
    "damage, code",
    [
        ("stale_project", "SQL_PROJECT_VERSION_CONFLICT"),
        ("nonexistent_draft_version", "SQL_DRAFT_VERSION_CONFLICT"),
        ("no_steps", "INVALID_SQL_DRAFT"),
        ("too_many_steps", "INVALID_SQL_DRAFT"),
        ("too_large_sql", "INVALID_SQL_DRAFT"),
    ],
)
def test_invalid_draft_never_creates_or_updates_persisted_draft(project, damage, code):
    changes = {
        "stale_project": {"expected_state_version": 2},
        "nonexistent_draft_version": {"expected_draft_version": 1},
        "no_steps": {"steps": []},
        "too_many_steps": {"steps": draft_command()["steps"] * 13},
        "too_large_sql": {"steps": [{"title": "Draft", "sql": "x" * 20_001, "parameters": "[]"}]},
    }[damage]
    with pytest.raises(BusinessDocumentError) as caught:
        Service.save_draft(TENANT, ACTOR, project, draft_command(**changes))
    assert caught.value.code == code
    assert BusinessDocumentSqlQueryDraft.select().count() == 0
    assert BusinessDocumentSqlAgentCommand.select().count() == 0


def test_changed_query_base_requires_explicit_draft_reset(project):
    original = Service.save_draft(TENANT, ACTOR, project, draft_command())
    BusinessDocumentSqlQueryProject.update(query_artifact_id="new-query").where(BusinessDocumentSqlQueryProject.id == project).execute()
    with pytest.raises(BusinessDocumentError) as caught:
        Service.save_draft(TENANT, ACTOR, project, draft_command(expected_draft_version=original["version"]))
    assert caught.value.code == "SQL_DRAFT_VERSION_CONFLICT"
    preserved = BusinessDocumentSqlQueryDraft.get()
    assert preserved.version == original["version"] and preserved.base_query_artifact_id == original["base_query_artifact_id"]
    reset = Service.save_draft(TENANT, ACTOR, project, draft_command(expected_draft_version=0))
    assert reset["version"] == 2 and reset["base_query_artifact_id"] == "new-query"


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"target": "OTHER"}, "INVALID_SQL_REVISION"),
        ({"base_artifact_id": "short-id"}, "INVALID_SQL_REVISION"),
        ({"anchor": {"kind": "UNKNOWN"}}, "INVALID_SQL_ANCHOR"),
        ({"text": " "}, "INVALID_SQL_REVISION"),
        ({"expected_state_version": 2}, "SQL_PROJECT_VERSION_CONFLICT"),
    ],
)
def test_invalid_thread_does_not_leave_comment_or_command_ledger(project, changes, code):
    with pytest.raises(BusinessDocumentError) as caught:
        Service.create_thread(TENANT, ACTOR, project, thread_command(**changes))
    assert caught.value.code == code
    assert BusinessDocumentSqlQueryThread.select().count() == 0
    assert BusinessDocumentSqlAgentCommand.select().count() == 0


def test_missing_compilation_blocks_comment_without_trusting_saved_query_text(project):
    BusinessDocumentSqlQueryArtifact.delete().where(BusinessDocumentSqlQueryArtifact.kind == "COMPILATION").execute()
    with pytest.raises(BusinessDocumentError) as caught:
        Service.create_thread(TENANT, ACTOR, project, thread_command())
    assert caught.value.code == "SQL_REVISION_SOURCE_MISSING"
    assert BusinessDocumentSqlQueryThread.select().count() == 0


@pytest.mark.parametrize("foreign", ["tenant", "owner"])
def test_revision_draft_and_thread_reject_foreign_actor_without_writes(project, foreign):
    tenant, actor = ("foreign-tenant", ACTOR) if foreign == "tenant" else (TENANT, "foreign-owner")
    for execute, payload in ((Service.save_draft, draft_command()), (Service.create_thread, thread_command())):
        with pytest.raises(BusinessDocumentError) as caught:
            execute(tenant, actor, project, payload)
        assert caught.value.status == (404 if foreign == "tenant" else 403)
    assert BusinessDocumentSqlQueryDraft.select().count() == 0
    assert BusinessDocumentSqlQueryThread.select().count() == 0


@pytest.mark.parametrize("damage, code", [("missing", "SQL_THREAD_NOT_FOUND"), ("closed", "SQL_THREAD_VERSION_CONFLICT"), ("stale", "SQL_THREAD_VERSION_CONFLICT")])
def test_reply_does_not_change_missing_closed_or_stale_thread(project, damage, code):
    thread = Service.create_thread(TENANT, ACTOR, project, thread_command())
    if damage == "closed":
        BusinessDocumentSqlQueryThread.update(status="CLOSED").where(BusinessDocumentSqlQueryThread.id == thread["id"]).execute()
    before = BusinessDocumentSqlQueryThread.get_by_id(thread["id"]).__data__.copy()
    with pytest.raises(BusinessDocumentError) as caught:
        Service.reply(
            TENANT,
            ACTOR,
            project,
            "missing" if damage == "missing" else thread["id"],
            {"schema_version": "1", "expected_thread_version": 2 if damage == "stale" else 1, "idempotency_key": "reply-boundary", "text": "Late clarification"},
        )
    assert caught.value.code == code
    assert BusinessDocumentSqlQueryThread.get_by_id(thread["id"]).__data__ == before
    assert BusinessDocumentSqlAgentCommand.select().count() == 1


def test_active_result_blocks_revision_before_mutating_thread_version(project):
    thread = Service.create_thread(TENANT, ACTOR, project, thread_command())
    BusinessDocumentSqlQueryRun.create(id="active-result", project_id=project, tenant_id=TENANT, kind="SQL", status="READY", compilation_id="c" * 32, profile_id="profile", profile_version=1)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "active-revision"})
    assert caught.value.code == "SQL_RESULT_ACTIVE"
    assert BusinessDocumentSqlQueryThread.get_by_id(thread["id"]).version == 1
    assert BusinessDocumentJob.select().count() == 0
    assert BusinessDocumentSqlQueryProject.get_by_id(project).operation_state == "IDLE"


def test_pending_revision_cancellation_is_idempotent_and_prevents_claim(project):
    thread = Service.create_thread(TENANT, ACTOR, project, thread_command())
    started = Service.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "pending-revision"})
    command = {"schema_version": "1", "expected_state_version": started["state_version"], "idempotency_key": "cancel-pending"}
    first = Service.cancel_change(TENANT, ACTOR, project, command)
    assert Service.cancel_change(TENANT, ACTOR, project, command) == first
    assert first["operation_state"] == "IDLE" and first["current_job"] is None
    assert BusinessDocumentJob.get().status == "STALE"
    assert BusinessDocumentJobQueue.claim("must-not-run-canceled-revision") is None
    assert BusinessDocumentSqlAgentProposal.select().count() == 0


def test_cancel_without_active_revision_preserves_project(project):
    before = BusinessDocumentSqlQueryProject.get_by_id(project).__data__.copy()
    with pytest.raises(BusinessDocumentError) as caught:
        Service.cancel_change(TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "cancel-absent"})
    assert caught.value.code == "SQL_PROJECT_VERSION_CONFLICT"
    assert BusinessDocumentSqlQueryProject.get_by_id(project).__data__ == before


def test_document_wording_cannot_change_verified_numeric_facts(project):
    artifact = BusinessDocumentSqlQueryArtifact.create(
        id="d" * 32,
        project_id=project,
        tenant_id=TENANT,
        kind="DOCUMENT",
        revision=1,
        payload={"run_id": "completed-run", "confirmed_conclusion": {"text": "There are 10 orders"}},
        content_hash="sha256:document",
        source_proposal_id="p" * 32,
        accepted_by=ACTOR,
    )
    thread = Service.create_thread(TENANT, ACTOR, project, thread_command(target="DOCUMENT"))
    Service.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "document-revision"})
    job = BusinessDocumentJobQueue.claim("document-boundary-worker", lease_ms=60_000)
    Agents.complete_job(
        job,
        job.lease_owner,
        job.lease_token,
        {"proposal": {"document_text": "There are 20 orders", "checks": {"status": "PASS"}}, "source": {"target": "DOCUMENT", "base_artifact_id": artifact.id, "thread_id": thread["id"]}},
    )
    reviewed = Agents.get_project(TENANT, ACTOR, project)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.decide_change(
            TENANT,
            ACTOR,
            project,
            reviewed["pending_proposal"]["id"],
            {"schema_version": "1", "expected_state_version": reviewed["state_version"], "idempotency_key": "accept-new-facts", "decision": "ACCEPT", "confirmed_no_new_facts": True},
        )
    assert caught.value.code == "SQL_DOCUMENT_FACTS_CHANGED"
    assert BusinessDocumentSqlQueryArtifact.select().where(BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT").count() == 1
    assert BusinessDocumentSqlQueryArtifact.get_by_id(artifact.id).payload["confirmed_conclusion"]["text"] == "There are 10 orders"
    assert BusinessDocumentSqlAgentProposal.get_by_id(reviewed["pending_proposal"]["id"]).status == "PENDING"


def test_revision_request_preserves_types_of_compiled_parameters(project):
    query = BusinessDocumentSqlQueryArtifact.get_by_id("q" * 32)
    query.payload = {key: value for key, value in query.payload.items() if key != "parameters"}
    query.save()
    compiled = BusinessDocumentSqlQueryArtifact.get_by_id("c" * 32)
    values = {"row_limit": 10, "include_active": True, "threshold": 1.5, "ids": [1, 2], "names": ["one", "two"], "flags": [True, False], "label": "orders"}
    compiled.payload = {**compiled.payload, "result": {**compiled.payload["result"], "parameters": values}}
    compiled.save()
    thread = Service.create_thread(TENANT, ACTOR, project, thread_command())
    Service.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "typed-parameters"})
    parameters = BusinessDocumentJob.get().payload["request"]["parameters"]
    assert {item["name"]: item["type"] for item in parameters} == {
        "row_limit": "integer",
        "include_active": "boolean",
        "threshold": "decimal",
        "ids": "integer_list",
        "names": "text_list",
        "flags": "text_list",
        "label": "text",
    }
    assert {item["name"]: item["value"] for item in parameters} == values
    assert BusinessDocumentSqlQueryArtifact.get_by_id(compiled.id).payload == compiled.payload


def test_schema_reopen_with_active_rows_rolls_back_pending_proposal_decision(project):
    BusinessDocumentSqlQueryProject.update(operation_state="REVIEW").where(BusinessDocumentSqlQueryProject.id == project).execute()
    proposal = BusinessDocumentSqlAgentProposal.create(
        id="blocked-proposal",
        project_id=project,
        tenant_id=TENANT,
        job_id="finished-job",
        kind="REVISION",
        source_state_version=1,
        payload={"agent_result": {"proposal": {"checks": {"status": "NEEDS_SCHEMA"}}}},
        content_hash="sha256:blocked",
    )
    BusinessDocumentSqlQueryRun.create(
        id="active-result", project_id=project, tenant_id=TENANT, kind="SQL", status="READY", compilation_id="c" * 32, profile_id="profile", profile_version=1, rows=[[10]]
    )
    before = BusinessDocumentSqlQueryProject.get_by_id(project).__data__.copy()
    with pytest.raises(BusinessDocumentError) as caught:
        Service.reopen_schema(TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "reopen-active"})
    assert caught.value.code == "SQL_RESULT_ACTIVE"
    assert BusinessDocumentSqlAgentProposal.get_by_id(proposal.id).status == "PENDING"
    assert BusinessDocumentSqlQueryProject.get_by_id(project).__data__ == before
    assert BusinessDocumentSqlQueryDraft.select().count() == 0


def test_reply_metadata_failure_rolls_back_thread_message_and_version(project, monkeypatch):
    thread = Service.create_thread(TENANT, ACTOR, project, thread_command())
    before = BusinessDocumentSqlQueryThread.get_by_id(thread["id"]).__data__.copy()

    def reject_ledger(*_args, **_kwargs):
        raise RuntimeError("Reply ledger unavailable")

    monkeypatch.setattr(Agents, "_record_command", reject_ledger)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        Service.reply(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_thread_version": 1, "idempotency_key": "reply-rollback", "text": "Must not be saved"})
    assert BusinessDocumentSqlQueryThread.get_by_id(thread["id"]).__data__ == before
