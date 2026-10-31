from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest
from peewee import SqliteDatabase

if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents import sql_query_revisions
from api.apps.business_documents.sql_query_revision_ai import propose_revision
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import (
    BusinessDocumentJob,
    BusinessDocumentSqlAgentProposal,
    BusinessDocumentSqlQueryArtifact,
    BusinessDocumentSqlQueryDraft,
    BusinessDocumentSqlQueryProject,
    BusinessDocumentSqlQueryThread,
)
from business_documents.application.errors import BusinessDocumentError, ConflictError, ValidationError
from business_documents.sql_query.revisions import locate_anchor, preserves_document_facts, query_blocks, validate_anchor
from business_documents.sql_query.project_compilation import build_project_compile_command
from business_documents.sql_query.query_specification import compile_query_payload
from test.unit_test.api.apps.business_documents.test_sql_query_specification import _snapshot
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService
from api.apps.business_documents.sql_query_agents import _stable_hash

TENANT = "tenant-revision"
ACTOR = "author-revision"
SQL = "SELECT o.order_id AS order_id FROM dwh.order_fact AS o LIMIT :row_limit"


def test_structured_revision_bindings_keep_declared_date_and_decimal_types():
    query = BusinessDocumentSqlQueryArtifact(
        payload={
            "filters": [
                {"parameter_name": "period_start", "parameter_type": "datetime"},
                {"parameter_name": "report_date", "parameter_type": "date"},
                {"parameter_name": "minimum_amount", "parameter_type": "decimal"},
                {"parameter_name": "code", "parameter_type": "text"},
            ]
        }
    )
    values = {"period_start": "2026-09-01T00:00:00Z", "report_date": "2026-09-01", "minimum_amount": "0.10", "code": "2026-09-01", "row_limit": 100}
    parameters = sql_query_revisions._parameters(query, {"parameters": values})
    assert {item["name"]: item["type"] for item in parameters} == {"period_start": "datetime", "report_date": "date", "minimum_amount": "decimal", "code": "text", "row_limit": "integer"}
    assert {item["name"]: item["value"] for item in parameters} == values


def test_manual_revision_bindings_preserve_saved_types_and_values():
    saved = [{"name": "start", "type": "datetime", "value": "2026-09-01T00:00:00"}, {"name": "zone", "type": "text", "value": "Europe/Moscow"}]
    query = BusinessDocumentSqlQueryArtifact(payload={"mode": "manual", "parameters": saved})
    assert sql_query_revisions._parameters(query, {"parameters": {"start": "other"}}) == saved


@pytest.fixture()
def project():
    database = SqliteDatabase(":memory:", check_same_thread=False)
    tables = BusinessDocumentSqlAgentService.model_tables()
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        created = BusinessDocumentSqlAgentService.create_project(TENANT, ACTOR, {"schema_version": "1", "title": "Orders", "source_request": "Show orders", "locale": "en"})

        def artifact(kind, artifact_id, payload):
            return BusinessDocumentSqlQueryArtifact.create(
                id=artifact_id, project_id=created["id"], tenant_id=TENANT, kind=kind, revision=1, payload=payload, content_hash=_stable_hash(payload), source_proposal_id="p" * 32, accepted_by=ACTOR
            )

        requirements = artifact("REQUIREMENTS", "r" * 32, {"requirements": [{"statement": "Show orders"}]})
        snapshot = _snapshot()
        snapshot["original_requirements"] = "- Show orders"
        source_tables = {item["selected_table"]["id"]: item["selected_table"] for item in snapshot["requirements"]}
        schema = artifact(
            "SCHEMA",
            "s" * 32,
            {
                "schema_snapshot": snapshot,
                "accepted_schema": [{"entity_id": table["id"], "version": table["version"], "schema_fingerprint": table["schema_fingerprint"]} for table in source_tables.values()],
            },
        )
        query = artifact("QUERY", "q" * 32, {"mode": "manual", "sql": SQL, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}], "confirmed_alignment": True})
        compiled = compile_query_payload(build_project_compile_command(requirements.payload, schema.payload, query.payload))
        assert compiled["status"] == "READY"
        artifact("COMPILATION", "c" * 32, {"source_artifact_ids": [requirements.id, schema.id, query.id], "result": compiled})
        BusinessDocumentSqlQueryProject.update(stage="COMPLETE", requirements_artifact_id=requirements.id, schema_artifact_id=schema.id, query_artifact_id=query.id).where(
            BusinessDocumentSqlQueryProject.id == created["id"]
        ).execute()

        async def visible(_actor, request, _is_admin, _role):
            return {"entities": [{"entity_id": identity, "lookup": {"status": "OK"}, "entity": source_tables[identity]} for identity in request["entity_ids"]]}

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(BusinessDocumentSqlQuerySchemaService, "load_entities", visible)
            yield created["id"]
        database.drop_tables(tables)
        database.close()


def test_semantic_and_text_anchors_reanchor_only_when_unambiguous():
    sql = "WITH recent AS (SELECT o.id AS id FROM dwh.orders AS o) SELECT recent.id AS id FROM recent LIMIT :row_limit"
    blocks = query_blocks(sql)
    assert {item["kind"] for item in blocks} >= {"CTE", "FIELD"}
    block = next(item for item in blocks if item["kind"] == "CTE")
    anchor = validate_anchor({"kind": "BLOCK", "block_id": block["id"]}, target="QUERY", sql=sql, blocks=blocks)
    assert locate_anchor(anchor, same_revision=False, sql=sql, blocks=blocks)["status"] == "CURRENT"
    assert locate_anchor(anchor, same_revision=False, sql="SELECT 1 AS id LIMIT :row_limit", blocks=query_blocks("SELECT 1 AS id LIMIT :row_limit"))["status"] == "UNRESOLVED"
    text = validate_anchor({"kind": "TEXT", "start": 0, "end": 4}, target="QUERY", sql=sql, blocks=blocks)
    assert locate_anchor(text, same_revision=False, sql="WITH x AS (SELECT 1 AS id) SELECT x.id AS id FROM x LIMIT :row_limit", blocks=[])["status"] == "CURRENT"
    duplicated = "SELECT 1 AS id UNION SELECT 1 AS id LIMIT :row_limit"
    duplicated_blocks = query_blocks(duplicated)
    duplicated_anchor = validate_anchor({"kind": "BLOCK", "block_id": duplicated_blocks[0]["id"]}, target="QUERY", sql=duplicated, blocks=duplicated_blocks)
    assert locate_anchor(duplicated_anchor, same_revision=False, sql=duplicated, blocks=duplicated_blocks)["status"] == "UNRESOLVED"
    assert not preserves_document_facts("Total: 10", "Total: 11")
    assert preserves_document_facts("Total: 10", "The total is 10")


def test_server_draft_and_comment_are_versioned_and_tenant_scoped(project):
    workspace = BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)
    assert workspace["sql"] == SQL
    assert {item["kind"] for item in workspace["history"]} == {"REQUIREMENTS", "SCHEMA", "QUERY", "COMPILATION"}
    with pytest.raises(ValidationError, match="Draft version"):
        BusinessDocumentSqlRevisionService.save_draft(
            TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "expected_draft_version": False, "steps": [{"title": "First", "sql": SQL, "parameters": "[]"}]}
        )
    draft = BusinessDocumentSqlRevisionService.save_draft(
        TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "expected_draft_version": 0, "steps": [{"title": "First", "sql": SQL, "parameters": "[]"}]}
    )
    assert draft["version"] == 1
    with pytest.raises(ConflictError):
        BusinessDocumentSqlRevisionService.save_draft(TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "expected_draft_version": 0, "steps": draft["steps"]})
    block = workspace["blocks"][0]
    thread_command = {
        "schema_version": "1",
        "expected_state_version": 1,
        "idempotency_key": "thread-1",
        "target": "QUERY",
        "anchor": {"kind": "BLOCK", "block_id": block["id"]},
        "text": "Add latest orders",
    }
    thread = BusinessDocumentSqlRevisionService.create_thread(TENANT, ACTOR, project, thread_command)
    assert thread["anchor_status"] == "CURRENT"
    assert BusinessDocumentSqlRevisionService.create_thread(TENANT, ACTOR, project, thread_command) == thread
    reply_command = {"schema_version": "1", "expected_thread_version": 1, "idempotency_key": "reply-1", "text": "Only active customers"}
    reply = BusinessDocumentSqlRevisionService.reply(TENANT, ACTOR, project, thread["id"], reply_command)
    assert len(reply["messages"]) == 2
    assert BusinessDocumentSqlRevisionService.reply(TENANT, ACTOR, project, thread["id"], reply_command) == reply
    with pytest.raises(BusinessDocumentError):
        BusinessDocumentSqlRevisionService.workspace("another-tenant", ACTOR, project)
    with pytest.raises(BusinessDocumentError):
        BusinessDocumentSqlRevisionService.create_thread(
            TENANT, "another-user", project, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "denied-1", "target": "QUERY", "anchor": {"kind": "WHOLE"}, "text": "Change it"}
        )
    BusinessDocumentSqlQueryProject.update(query_artifact_id="new-query", state_version=2).where(BusinessDocumentSqlQueryProject.id == project).execute()
    reset = BusinessDocumentSqlRevisionService.save_draft(
        TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 2, "expected_draft_version": None, "steps": [{"title": "Next", "sql": SQL, "parameters": "[]"}]}
    )
    assert reset["version"] == 2
    assert reset["base_query_artifact_id"] == "new-query"


def test_ambiguous_or_changed_anchor_requires_explicit_reattachment(project):
    workspace = BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)
    field = next(item for item in workspace["blocks"] if item["kind"] == "FIELD")
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "reattach-thread",
            "target": "QUERY",
            "anchor": {"kind": "BLOCK", "block_id": field["id"]},
            "text": "Change the output",
        },
    )
    next_sql = SQL.replace("order_id AS order_id", "customer_id AS customer_id")
    BusinessDocumentSqlQueryArtifact.create(
        id="z" * 32,
        project_id=project,
        tenant_id=TENANT,
        kind="QUERY",
        revision=2,
        payload={"mode": "manual", "sql": next_sql, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}]},
        content_hash="sha256:next",
        source_proposal_id="p" * 32,
        accepted_by=ACTOR,
    )
    BusinessDocumentSqlQueryArtifact.create(
        id="x" * 32,
        project_id=project,
        tenant_id=TENANT,
        kind="COMPILATION",
        revision=2,
        payload={"source_artifact_ids": ["r" * 32, "s" * 32, "z" * 32], "result": {"sql": next_sql, "parameters": {"row_limit": 10}, "guard": {"status": "PASS"}}},
        content_hash="sha256:next-compiled",
        source_proposal_id="p" * 32,
        accepted_by=ACTOR,
    )
    BusinessDocumentSqlQueryProject.update(query_artifact_id="z" * 32, state_version=2).where(BusinessDocumentSqlQueryProject.id == project).execute()
    current = BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)
    assert current["threads"][0]["anchor_status"] == "UNRESOLVED"
    with pytest.raises(ConflictError, match="Reattach"):
        BusinessDocumentSqlRevisionService.request_change(
            TENANT,
            ACTOR,
            project,
            thread["id"],
            {
                "schema_version": "1",
                "expected_state_version": 2,
                "idempotency_key": "stale-thread-request",
            },
        )
    new_field = next(item for item in current["blocks"] if item["kind"] == "FIELD")
    command = {"schema_version": "1", "expected_state_version": 2, "expected_thread_version": 1, "idempotency_key": "reattach-command", "anchor": {"kind": "BLOCK", "block_id": new_field["id"]}}
    rebound = BusinessDocumentSqlRevisionService.reattach(TENANT, ACTOR, project, thread["id"], command)
    assert rebound["base_artifact_id"] == "z" * 32
    assert rebound["rebindings"][0]["base_artifact_id"] == "q" * 32
    assert BusinessDocumentSqlRevisionService.reattach(TENANT, ACTOR, project, thread["id"], command) == rebound
    started = BusinessDocumentSqlRevisionService.request_change(
        TENANT,
        ACTOR,
        project,
        thread["id"],
        {
            "schema_version": "1",
            "expected_state_version": 2,
            "idempotency_key": "rebound-request",
        },
    )
    assert started["operation_state"] == "RUNNING"


def test_revision_proposal_rejection_preserves_accepted_query(project):
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "thread-2", "target": "QUERY", "anchor": {"kind": "WHOLE"}, "text": "Add an order filter"}
    )
    started = BusinessDocumentSqlRevisionService.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "request-1"})
    assert started["operation_state"] == "RUNNING"
    assert BusinessDocumentSqlQueryThread.get_by_id(thread["id"]).version == 2
    with pytest.raises(ConflictError, match="Discussion changed"):
        BusinessDocumentSqlRevisionService.reply(
            TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_thread_version": 1, "idempotency_key": "stale-reply", "text": "Late clarification"}
        )
    assert BusinessDocumentSqlRevisionService.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "request-1"}) == started
    job = BusinessDocumentJobQueue.claim("revision-test")
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "revision-test",
        job.lease_token,
        {
            "proposal": {"sql": SQL, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}], "summary": "No change", "checks": {"status": "PASS"}},
            "source": {"target": "QUERY", "base_artifact_id": "q" * 32, "thread_id": thread["id"]},
        },
    )
    reviewed = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    assert reviewed["operation_state"] == "REVIEW"
    rejected = BusinessDocumentSqlRevisionService.decide_change(
        TENANT, ACTOR, project, reviewed["pending_proposal"]["id"], {"schema_version": "1", "expected_state_version": reviewed["state_version"], "idempotency_key": "reject-1", "decision": "REJECT"}
    )
    assert rejected["artifact_ids"]["query"] == "q" * 32
    assert rejected["operation_state"] == "IDLE"
    history = BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)["threads"][0]["proposals"]
    assert history[0]["status"] == "REJECTED"
    assert history[0]["proposal"]["summary"] == "No change"


def test_reply_after_proposal_request_requires_a_new_proposal(project):
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "late-thread",
            "target": "QUERY",
            "anchor": {"kind": "WHOLE"},
            "text": "Filter orders",
        },
    )
    BusinessDocumentSqlRevisionService.request_change(
        TENANT,
        ACTOR,
        project,
        thread["id"],
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "late-request",
        },
    )
    job = BusinessDocumentJobQueue.claim("late-test")
    assert job.payload["request"]["thread_version"] == 2
    BusinessDocumentSqlRevisionService.reply(
        TENANT,
        ACTOR,
        project,
        thread["id"],
        {
            "schema_version": "1",
            "expected_thread_version": 2,
            "idempotency_key": "late-reply",
            "text": "Exclude cancelled orders too",
        },
    )
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "late-test",
        job.lease_token,
        {
            "proposal": {"sql": SQL, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}], "summary": "Filter orders", "checks": {"status": "PASS"}},
            "source": {"target": "QUERY", "base_artifact_id": "q" * 32, "thread_id": thread["id"]},
        },
    )
    reviewed = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    proposal_id = reviewed["pending_proposal"]["id"]
    with pytest.raises(ConflictError, match="Discussion changed"):
        BusinessDocumentSqlRevisionService.decide_change(
            TENANT,
            ACTOR,
            project,
            proposal_id,
            {
                "schema_version": "1",
                "expected_state_version": reviewed["state_version"],
                "idempotency_key": "late-accept",
                "decision": "ACCEPT",
            },
        )
    rejected = BusinessDocumentSqlRevisionService.decide_change(
        TENANT,
        ACTOR,
        project,
        proposal_id,
        {
            "schema_version": "1",
            "expected_state_version": reviewed["state_version"],
            "idempotency_key": "late-reject",
            "decision": "REJECT",
        },
    )
    assert rejected["artifact_ids"]["query"] == "q" * 32


def test_reply_during_acceptance_rolls_back_new_artifacts(project, monkeypatch):
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "racing-thread",
            "target": "QUERY",
            "anchor": {"kind": "WHOLE"},
            "text": "Filter orders",
        },
    )
    BusinessDocumentSqlRevisionService.request_change(
        TENANT,
        ACTOR,
        project,
        thread["id"],
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "racing-request",
        },
    )
    job = BusinessDocumentJobQueue.claim("racing-test")
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "racing-test",
        job.lease_token,
        {
            "proposal": {"sql": SQL, "parameters": [], "checks": {"status": "PASS"}},
            "source": {"target": "QUERY", "base_artifact_id": "q" * 32, "thread_id": thread["id"]},
        },
    )
    reviewed = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    artifact_count = BusinessDocumentSqlQueryArtifact.select().count()

    def compile_after_reply(_command):
        BusinessDocumentSqlRevisionService.reply(
            TENANT,
            ACTOR,
            project,
            thread["id"],
            {
                "schema_version": "1",
                "expected_thread_version": 2,
                "idempotency_key": "racing-reply",
                "text": "Exclude cancelled orders too",
            },
        )
        return {"status": "READY", "sql": SQL, "parameters": {}, "guard": {"status": "PASS"}}

    monkeypatch.setattr(sql_query_revisions, "compile_query_payload", compile_after_reply)
    with pytest.raises(ConflictError, match="Discussion changed"):
        BusinessDocumentSqlRevisionService.decide_change(
            TENANT,
            ACTOR,
            project,
            reviewed["pending_proposal"]["id"],
            {
                "schema_version": "1",
                "expected_state_version": reviewed["state_version"],
                "idempotency_key": "racing-accept",
                "decision": "ACCEPT",
            },
        )
    assert BusinessDocumentSqlQueryArtifact.select().count() == artifact_count
    assert BusinessDocumentSqlAgentProposal.get_by_id(reviewed["pending_proposal"]["id"]).status == "PENDING"
    assert BusinessDocumentSqlQueryProject.get_by_id(project).state_version == reviewed["state_version"]


def test_cancelling_running_revision_makes_late_result_stale(project):
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "cancel-thread",
            "target": "QUERY",
            "anchor": {"kind": "WHOLE"},
            "text": "Change the query",
        },
    )
    started = BusinessDocumentSqlRevisionService.request_change(
        TENANT,
        ACTOR,
        project,
        thread["id"],
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "cancel-request",
        },
    )
    job = BusinessDocumentJobQueue.claim("cancel-test")
    cancelled = BusinessDocumentSqlRevisionService.cancel_change(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": started["state_version"],
            "idempotency_key": "cancel-command",
        },
    )
    assert cancelled["operation_state"] == "IDLE"
    assert (
        BusinessDocumentSqlRevisionService.cancel_change(
            TENANT,
            ACTOR,
            project,
            {
                "schema_version": "1",
                "expected_state_version": started["state_version"],
                "idempotency_key": "cancel-command",
            },
        )
        == cancelled
    )
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "cancel-test",
        job.lease_token,
        {
            "proposal": {"sql": SQL, "parameters": [], "summary": "Too late"},
        },
    )
    assert BusinessDocumentJob.get_by_id(job.id).status == "STALE"
    assert BusinessDocumentSqlAgentProposal.select().where(BusinessDocumentSqlAgentProposal.project_id == project).count() == 0


def test_blocked_revision_reopens_schema_and_preserves_old_query(project):
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "schema-thread",
            "target": "QUERY",
            "anchor": {"kind": "WHOLE"},
            "text": "Add payments",
        },
    )
    BusinessDocumentSqlRevisionService.request_change(
        TENANT,
        ACTOR,
        project,
        thread["id"],
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "schema-request",
        },
    )
    job = BusinessDocumentJobQueue.claim("schema-test")
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "schema-test",
        job.lease_token,
        {
            "proposal": {"sql": SQL, "parameters": [], "summary": "Needs payments", "requested_sources": ["payments"], "checks": {"status": "NEEDS_SCHEMA"}},
            "source": {"target": "QUERY", "base_artifact_id": "q" * 32, "thread_id": thread["id"]},
        },
    )
    reviewed = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    with pytest.raises(ValidationError, match="did not pass"):
        BusinessDocumentSqlRevisionService.decide_change(
            TENANT,
            ACTOR,
            project,
            reviewed["pending_proposal"]["id"],
            {
                "schema_version": "1",
                "expected_state_version": reviewed["state_version"],
                "idempotency_key": "invalid-schema-accept",
                "decision": "ACCEPT",
            },
        )
    reopened = BusinessDocumentSqlRevisionService.reopen_schema(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": reviewed["state_version"],
            "idempotency_key": "schema-reopen",
        },
    )
    assert reopened["stage"] == "SCHEMA"
    assert reopened["artifact_ids"] == {"requirements": "r" * 32, "schema": None, "query": None}
    assert BusinessDocumentSqlQueryArtifact.get_by_id("q" * 32).payload["sql"] == SQL
    workspace = BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)
    assert workspace["draft"]["steps"][0]["sql"] == SQL
    assert workspace["threads"][0]["proposals"][0]["status"] == "REJECTED"


def test_reopening_schema_keeps_existing_server_draft_steps(project):
    steps = [
        {"title": "Base", "sql": SQL, "parameters": "[]"},
        {"title": "New join", "sql": SQL.replace("LIMIT", "WHERE o.status = :status LIMIT"), "parameters": "[]"},
    ]
    BusinessDocumentSqlRevisionService.save_draft(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "expected_draft_version": 0,
            "steps": steps,
        },
    )
    BusinessDocumentSqlRevisionService.reopen_schema(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "keep-draft",
        },
    )
    draft = BusinessDocumentSqlQueryDraft.get(BusinessDocumentSqlQueryDraft.project_id == project)
    assert draft.steps == steps
    assert draft.base_query_artifact_id is None
    assert draft.version == 2


def test_historical_query_revision_can_be_selected_with_current_schema(project, monkeypatch):
    newer_sql = SQL.replace("order_id AS order_id", "order_id AS purchase_id")
    BusinessDocumentSqlQueryArtifact.create(
        id="z" * 32,
        project_id=project,
        tenant_id=TENANT,
        kind="QUERY",
        revision=2,
        payload={"mode": "manual", "sql": newer_sql, "parameters": [{"name": "row_limit", "type": "integer", "value": 20}]},
        content_hash="sha256:new-query",
        source_proposal_id="p" * 32,
        accepted_by=ACTOR,
    )
    BusinessDocumentSqlQueryArtifact.create(
        id="c" * 31 + "2",
        project_id=project,
        tenant_id=TENANT,
        kind="COMPILATION",
        revision=2,
        payload={"source_artifact_ids": ["r" * 32, "s" * 32, "z" * 32], "result": {"sql": newer_sql, "parameters": {"row_limit": 20}, "guard": {"status": "PASS"}}},
        content_hash="sha256:new-compile",
        source_proposal_id="p" * 32,
        accepted_by=ACTOR,
    )
    BusinessDocumentSqlQueryProject.update(query_artifact_id="z" * 32, state_version=2).where(BusinessDocumentSqlQueryProject.id == project).execute()
    workspace = BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)
    assert {item["id"] for item in workspace["query_revisions"]} == {"q" * 32, "z" * 32}
    old_revision = next(item for item in workspace["query_revisions"] if item["id"] == "q" * 32)
    old_field = next(item for item in old_revision["blocks"] if item["kind"] == "FIELD")
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 2,
            "idempotency_key": "historical-thread",
            "target": "QUERY",
            "base_artifact_id": "q" * 32,
            "anchor": {"kind": "BLOCK", "block_id": old_field["id"]},
            "text": "Use the original output name",
        },
    )
    assert thread["anchor"]["pinned_revision"] is True
    assert BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)["threads"][0]["anchor_status"] == "CURRENT"
    BusinessDocumentSqlRevisionService.request_change(
        TENANT,
        ACTOR,
        project,
        thread["id"],
        {
            "schema_version": "1",
            "expected_state_version": 2,
            "idempotency_key": "historical-request",
        },
    )
    job = BusinessDocumentJobQueue.claim("historical-test")
    assert job.payload["request"]["sql"] == SQL
    assert job.payload["request"]["parameters"][0]["value"] == 10
    candidate = SQL.replace("order_id AS order_id", "order_id AS original_order_id")
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "historical-test",
        job.lease_token,
        {
            "proposal": {"sql": candidate, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}], "summary": "Original output", "checks": {"status": "PASS"}},
            "source": {"target": "QUERY", "base_artifact_id": "q" * 32, "thread_id": thread["id"]},
        },
    )
    monkeypatch.setattr(sql_query_revisions, "build_project_compile_command", lambda *args: {})
    monkeypatch.setattr(sql_query_revisions, "compile_query_payload", lambda *args: {"status": "READY", "sql": candidate, "parameters": {"row_limit": 10}, "guard": {"status": "PASS"}})
    reviewed = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    accepted = BusinessDocumentSqlRevisionService.decide_change(
        TENANT,
        ACTOR,
        project,
        reviewed["pending_proposal"]["id"],
        {
            "schema_version": "1",
            "expected_state_version": reviewed["state_version"],
            "idempotency_key": "historical-accept",
            "decision": "ACCEPT",
        },
    )
    assert accepted["artifact_ids"]["query"] not in {"q" * 32, "z" * 32}
    assert BusinessDocumentSqlQueryArtifact.get_by_id("z" * 32).payload["sql"] == newer_sql
    assert BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)["threads"][0]["anchor_status"] == "CURRENT"


def test_historical_query_remains_readable_but_needs_its_confirmed_schema_for_revision(project):
    BusinessDocumentSqlQueryProject.update(schema_artifact_id="n" * 32, state_version=2).where(BusinessDocumentSqlQueryProject.id == project).execute()
    history = BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)["query_revisions"]
    assert len(history) == 1 and history[0]["id"] == "q" * 32
    assert history[0]["matches_current_context"] is False
    assert history[0]["source_artifact_ids"] == ["r" * 32, "s" * 32]
    with pytest.raises(ConflictError, match="confirmed schema"):
        BusinessDocumentSqlRevisionService.create_thread(
            TENANT,
            ACTOR,
            project,
            {
                "schema_version": "1",
                "expected_state_version": 2,
                "idempotency_key": "old-schema-thread",
                "target": "QUERY",
                "base_artifact_id": "q" * 32,
                "anchor": {"kind": "WHOLE"},
                "text": "Revise the old query",
            },
        )


def test_accept_query_creates_new_immutable_artifacts(project, monkeypatch):
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "thread-3", "target": "QUERY", "anchor": {"kind": "WHOLE"}, "text": "Rename the output"}
    )
    BusinessDocumentSqlRevisionService.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "request-2"})
    job = BusinessDocumentJobQueue.claim("accept-test")
    candidate = SQL.replace("order_id AS order_id", "order_id AS purchase_id")
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "accept-test",
        job.lease_token,
        {
            "proposal": {"sql": candidate, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}], "summary": "Renamed output", "checks": {"status": "PASS"}},
            "source": {"target": "QUERY", "base_artifact_id": "q" * 32, "thread_id": thread["id"]},
        },
    )
    monkeypatch.setattr(sql_query_revisions, "build_project_compile_command", lambda *args: {})
    monkeypatch.setattr(sql_query_revisions, "compile_query_payload", lambda *args: {"status": "READY", "sql": candidate, "parameters": {"row_limit": 10}, "guard": {"status": "PASS"}})
    reviewed = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    accepted = BusinessDocumentSqlRevisionService.decide_change(
        TENANT, ACTOR, project, reviewed["pending_proposal"]["id"], {"schema_version": "1", "expected_state_version": reviewed["state_version"], "idempotency_key": "accept-1", "decision": "ACCEPT"}
    )
    assert accepted["artifact_ids"]["query"] != "q" * 32
    assert BusinessDocumentSqlQueryArtifact.get_by_id("q" * 32).payload["sql"] == SQL
    assert accepted["compilation"]["result"]["sql"] == candidate
    assert BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)["threads"][0]["anchor_status"] == "CURRENT"
    repeated = BusinessDocumentSqlRevisionService.request_change(
        TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": accepted["state_version"], "idempotency_key": "request-after-accept"}
    )
    assert repeated["operation_state"] == "RUNNING"


def test_saved_document_wording_can_be_revised_without_erasing_old_revision(project):
    original = BusinessDocumentSqlQueryArtifact.create(
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
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "document-thread",
            "target": "DOCUMENT",
            "anchor": {"kind": "WHOLE"},
            "text": "Make the wording clearer",
        },
    )
    BusinessDocumentSqlRevisionService.request_change(
        TENANT,
        ACTOR,
        project,
        thread["id"],
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "document-request",
        },
    )
    job = BusinessDocumentJobQueue.claim("document-test")
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "document-test",
        job.lease_token,
        {
            "proposal": {"document_text": "The total is 10 orders", "summary": "Clearer wording", "checks": {"status": "PASS"}},
            "source": {"target": "DOCUMENT", "base_artifact_id": original.id, "thread_id": thread["id"]},
        },
    )
    reviewed = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    with pytest.raises(ValidationError, match="Confirm"):
        BusinessDocumentSqlRevisionService.decide_change(
            TENANT,
            ACTOR,
            project,
            reviewed["pending_proposal"]["id"],
            {
                "schema_version": "1",
                "expected_state_version": reviewed["state_version"],
                "idempotency_key": "document-unreviewed",
                "decision": "ACCEPT",
            },
        )
    accepted = BusinessDocumentSqlRevisionService.decide_change(
        TENANT,
        ACTOR,
        project,
        reviewed["pending_proposal"]["id"],
        {
            "schema_version": "1",
            "expected_state_version": reviewed["state_version"],
            "idempotency_key": "document-accept",
            "decision": "ACCEPT",
            "confirmed_no_new_facts": True,
        },
    )
    assert len(accepted["documents"]) == 2
    assert accepted["document"]["payload"]["confirmed_conclusion"]["text"] == "The total is 10 orders"
    assert accepted["document"]["payload"]["wording_review_confirmed_by"] == ACTOR
    assert BusinessDocumentSqlQueryArtifact.get_by_id(original.id).payload["confirmed_conclusion"]["text"] == "There are 10 orders"
    with pytest.raises(ConflictError, match="Save a query or document"):
        BusinessDocumentSqlRevisionService.create_thread(
            TENANT,
            ACTOR,
            project,
            {
                "schema_version": "1",
                "expected_state_version": accepted["state_version"],
                "idempotency_key": "foreign-document-thread",
                "target": "DOCUMENT",
                "base_artifact_id": "x" * 32,
                "anchor": {"kind": "WHOLE"},
                "text": "Edit another document",
            },
        )
    old_thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": accepted["state_version"],
            "idempotency_key": "old-document-thread",
            "target": "DOCUMENT",
            "base_artifact_id": original.id,
            "anchor": {"kind": "WHOLE"},
            "text": "Clarify the original wording",
        },
    )
    started = BusinessDocumentSqlRevisionService.request_change(
        TENANT,
        ACTOR,
        project,
        old_thread["id"],
        {
            "schema_version": "1",
            "expected_state_version": accepted["state_version"],
            "idempotency_key": "old-document-request",
        },
    )
    job = BusinessDocumentJobQueue.claim("old-document-test")
    assert job.payload["request"]["document"]["confirmed_conclusion"]["text"] == "There are 10 orders"
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "old-document-test",
        job.lease_token,
        {
            "proposal": {"document_text": "Exactly 10 orders were recorded", "summary": "Clarify original", "checks": {"status": "PASS"}},
            "source": {"target": "DOCUMENT", "base_artifact_id": original.id, "thread_id": old_thread["id"]},
        },
    )
    reviewed = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    branched = BusinessDocumentSqlRevisionService.decide_change(
        TENANT,
        ACTOR,
        project,
        reviewed["pending_proposal"]["id"],
        {
            "schema_version": "1",
            "expected_state_version": reviewed["state_version"],
            "idempotency_key": "old-document-accept",
            "decision": "ACCEPT",
            "confirmed_no_new_facts": True,
        },
    )
    assert started["operation_state"] == "RUNNING"
    assert len(branched["documents"]) == 3
    assert branched["document"]["payload"]["text_revision_of"] == original.id


def test_data_dependent_document_proposal_cannot_be_accepted_without_run(project):
    original = BusinessDocumentSqlQueryArtifact.create(
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
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "new-data-thread",
            "target": "DOCUMENT",
            "anchor": {"kind": "WHOLE"},
            "text": "Use current data",
        },
    )
    BusinessDocumentSqlRevisionService.request_change(
        TENANT,
        ACTOR,
        project,
        thread["id"],
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "new-data-request",
        },
    )
    job = BusinessDocumentJobQueue.claim("new-data-test")
    BusinessDocumentSqlAgentService.complete_job(
        job,
        "new-data-test",
        job.lease_token,
        {
            "proposal": {"requires_new_run": True, "summary": "Need current totals", "checks": {"status": "NEEDS_RUN"}},
            "source": {"target": "DOCUMENT", "base_artifact_id": original.id, "thread_id": thread["id"]},
        },
    )
    reviewed = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    with pytest.raises(ValidationError, match="new verified run"):
        BusinessDocumentSqlRevisionService.decide_change(
            TENANT,
            ACTOR,
            project,
            reviewed["pending_proposal"]["id"],
            {
                "schema_version": "1",
                "expected_state_version": reviewed["state_version"],
                "idempotency_key": "new-data-accept",
                "decision": "ACCEPT",
                "confirmed_no_new_facts": True,
            },
        )
    assert BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project) & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")).count() == 1


def test_saved_project_question_can_be_reopened_without_erasing_accepted_artifacts(project):
    revised = BusinessDocumentSqlAgentService.revise_question(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "new-question",
            "source_request": "Show paid orders",
        },
    )
    assert revised["stage"] == "REQUIREMENTS"
    assert revised["source_request"] == "Show paid orders"
    assert revised["artifact_ids"] == {"requirements": None, "schema": None, "query": None}
    assert BusinessDocumentSqlQueryArtifact.get_by_id("q" * 32).payload["sql"] == SQL


@pytest.mark.asyncio
async def test_revision_separates_unconfirmed_questions_from_human_decisions_and_keeps_catalog_constraints():
    from copy import deepcopy

    calls = []
    constraints = [{"constraint_type": "FOREIGN_KEY", "columns": ["customer_id"], "referred_columns": ["public.customers.id"]}]
    table = {
        "id": "orders",
        "name": "orders",
        "schema": "public",
        "table_constraints": constraints,
        "columns": [{"id": "public.orders.created_at", "name": "created_at", "data_type": "timestamptz", "constraint": "NOT_NULL"}],
    }
    request = {
        "target": "QUERY",
        "sql": SQL.replace(" LIMIT", " ORDER BY order_id ASC LIMIT"),
        "schema": {"accepted_schema": [{"entity_id": "orders"}], "schema_snapshot": {"requirements": [{"selected_table": table}, {"selected_table": {"id": "unaccepted"}}]}},
        "comments": [
            {"text": "По месяцам для выбранной группы"},
            {
                "role": "assistant",
                "text": "Уточните условия",
                "questions": [{"id": "sort", "text": "Сменить сортировку?", "options": ["Да", "Нет"]}, {"id": "null", "text": "Что делать с NULL?", "options": ["Исключить", "Неизвестно"]}],
            },
            {"text": "Неизвестно"},
        ],
    }
    before = deepcopy(request)

    class FakeLLM:
        async def async_generate(self, tenant_id, system, payload):
            calls.append(payload["context"])
            return {"response_kind": "EXPLANATION", "answer": "Исходная сортировка сохранена"}

    await propose_revision(TENANT, request, llm=FakeLLM())
    context = calls[0]
    assert [message["text"] for message in context["discussion"]] == ["По месяцам для выбранной группы", "Неизвестно"]
    assert context["previous_questions"][0]["questions"] == request["comments"][1]["questions"][1:]
    assert context["current_ordering"] == "ORDER BY order_id ASC"
    assert context["accepted_schema"]["tables"] == [table]
    assert request == before
    request["sql"] = "SELECT FROM"
    await propose_revision(TENANT, request, llm=FakeLLM())
    assert calls[-1]["current_ordering"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [{"response_kind": "CLARIFICATION", "answer": "Продолжим", "questions": []}, {"sql": "SELECT 1"}, "[]"])
async def test_revision_repairs_invalid_model_contract_once(invalid):
    calls = []
    valid = {"response_kind": "EXPLANATION", "answer": "Сохранённые условия не изменены"}

    class FakeLLM:
        async def async_generate(self, tenant_id, system, payload):
            calls.append(payload)
            return invalid if len(calls) == 1 else valid

    request = {"target": "QUERY", "sql": SQL, "comments": [{"text": "По месяцам для выбранной модели"}]}
    assert await propose_revision(TENANT, request, llm=FakeLLM()) == valid
    assert len(calls) == 2
    assert calls[0]["context"] == calls[1]["context"]
    assert calls[1]["format_correction"]["reason"]


@pytest.mark.asyncio
async def test_revision_invalid_repair_is_precise_terminal_error():
    calls = []

    class FakeLLM:
        async def async_generate(self, *_):
            calls.append(True)
            return {"response_kind": "CLARIFICATION", "answer": "Продолжим", "questions": []}

    with pytest.raises(BusinessDocumentError) as failure:
        await propose_revision(TENANT, {"target": "QUERY", "sql": SQL}, llm=FakeLLM())
    assert failure.value.code == "SQL_REVISION_INVALID_RESPONSE"
    assert failure.value.status == 422
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_revision_does_not_retry_unavailable_provider():
    calls = []

    class FakeLLM:
        async def async_generate(self, *_):
            calls.append(True)
            raise ConnectionError("private provider diagnostic")

    with pytest.raises(BusinessDocumentError) as failure:
        await propose_revision(TENANT, {"target": "QUERY", "sql": SQL}, llm=FakeLLM())
    assert failure.value.code == "SQL_REVISION_UNAVAILABLE"
    assert failure.value.status == 503
    assert "private" not in failure.value.message
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_revision_repair_uses_remaining_time_budget(monkeypatch):
    import asyncio

    from api.apps.business_documents import sql_query_revision_ai

    monkeypatch.setattr(sql_query_revision_ai, "MODEL_TIMEOUT_SECONDS", 0.08)

    class FakeLLM:
        async def async_generate(self, *_):
            await asyncio.sleep(0.05)
            return {"response_kind": "CLARIFICATION", "answer": "Продолжим", "questions": []}

    with pytest.raises(BusinessDocumentError) as failure:
        await propose_revision(TENANT, {"target": "QUERY", "sql": SQL}, llm=FakeLLM())
    assert failure.value.code == "SQL_REVISION_UNAVAILABLE"
    assert isinstance(failure.value.__cause__, TimeoutError)


@pytest.mark.asyncio
async def test_ai_revision_is_only_a_checked_proposal(monkeypatch):
    calls = []

    class FakeLLM:
        async def async_generate(self, tenant_id, system_prompt, payload):
            calls.append((tenant_id, system_prompt, payload))
            return {"sql": SQL, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}], "summary": "Keep the query"}

    from api.apps.business_documents import sql_query_revision_ai

    monkeypatch.setattr(sql_query_revision_ai, "build_project_compile_command", lambda *args: {})
    monkeypatch.setattr(sql_query_revision_ai, "compile_query_payload", lambda *args: {"status": "READY", "guard": {"status": "PASS"}, "output_columns": ["order_id"]})
    result = await propose_revision(
        TENANT,
        {
            "target": "QUERY",
            "base_artifact_id": "q" * 32,
            "thread_id": "t" * 32,
            "sql": SQL,
            "parameters": [],
            "requirements": {},
            "schema": {},
            "comments": [{"text": "Keep it"}],
            "anchor": {"kind": "WHOLE"},
        },
        llm=FakeLLM(),
    )
    assert result["proposal"]["checks"]["status"] == "PASS"
    assert result["source"]["base_artifact_id"] == "q" * 32
    assert calls[0][0] == TENANT
    assert calls[0][2]["context"]["current_sql"] == SQL


@pytest.mark.asyncio
async def test_ai_revision_exposes_parameter_only_change(monkeypatch):
    from api.apps.business_documents import sql_query_revision_ai

    class FakeLLM:
        async def async_generate(self, *_):
            return {"sql": SQL, "parameters": [{"name": "row_limit", "type": "integer", "value": 20}], "summary": "More rows"}

    monkeypatch.setattr(sql_query_revision_ai, "build_project_compile_command", lambda *args: {})
    monkeypatch.setattr(sql_query_revision_ai, "compile_query_payload", lambda *args: {"status": "READY", "guard": {"status": "PASS"}, "output_columns": ["order_id"]})
    result = await propose_revision(
        TENANT,
        {
            "target": "QUERY",
            "sql": SQL,
            "parameters": [{"name": "row_limit", "type": "integer", "value": 10}],
            "requirements": {},
            "schema": {},
        },
        llm=FakeLLM(),
    )
    assert result["proposal"]["sql_diff"] == ""
    assert '"value": 10' in result["proposal"]["parameter_diff"]
    assert '"value": 20' in result["proposal"]["parameter_diff"]


@pytest.mark.asyncio
async def test_ai_revision_surfaces_unconfirmed_schema_without_accepting_sql(monkeypatch):
    from api.apps.business_documents import sql_query_revision_ai

    candidate = "SELECT p.id AS id FROM dwh.payments AS p LIMIT :row_limit"

    class FakeLLM:
        async def async_generate(self, *_):
            return {"sql": candidate, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}], "summary": "Use payments"}

    monkeypatch.setattr(sql_query_revision_ai, "build_project_compile_command", lambda *args: {})
    monkeypatch.setattr(sql_query_revision_ai, "compile_query_payload", lambda *args: (_ for _ in ()).throw(sql_query_revision_ai.SqlGuardError("Table is outside accepted schema")))
    result = await propose_revision(TENANT, {"target": "QUERY", "sql": SQL, "requirements": {}, "schema": {}, "parameters": []}, llm=FakeLLM())
    assert result["proposal"]["checks"]["status"] == "BLOCKED"
    assert "outside accepted schema" in result["proposal"]["checks"]["reason"]
    assert "payments" in result["proposal"]["sql_diff"]
    assert result["proposal"]["affected_blocks"]


@pytest.mark.asyncio
async def test_ai_can_request_new_catalog_sources_without_changing_sql(monkeypatch):
    from api.apps.business_documents import sql_query_revision_ai

    class FakeLLM:
        async def async_generate(self, *_):
            return {"sql": SQL, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}], "summary": "Add payments", "requested_sources": ["платежи"]}

    monkeypatch.setattr(sql_query_revision_ai, "build_project_compile_command", lambda *args: {})
    monkeypatch.setattr(sql_query_revision_ai, "compile_query_payload", lambda *args: {"status": "READY", "guard": {"status": "PASS"}, "output_columns": ["order_id"]})
    result = await propose_revision(TENANT, {"target": "QUERY", "sql": SQL, "requirements": {}, "schema": {}, "parameters": []}, llm=FakeLLM())
    assert result["proposal"]["checks"]["status"] == "NEEDS_SCHEMA"
    assert result["proposal"]["requested_sources"] == ["платежи"]


@pytest.mark.asyncio
async def test_ai_document_revision_requires_new_run_for_new_numeric_claim():
    class FakeLLM:
        async def async_generate(self, *_):
            return {"document_text": "There are 11 orders", "summary": "Changed count"}

    result = await propose_revision(TENANT, {"target": "DOCUMENT", "document": {"confirmed_conclusion": {"text": "There are 10 orders"}}}, llm=FakeLLM())
    assert result["proposal"]["checks"]["status"] == "NEEDS_RUN"


@pytest.mark.asyncio
async def test_ai_document_revision_can_request_a_new_data_run():
    class FakeLLM:
        async def async_generate(self, *_):
            return {"requires_new_run": True, "summary": "Include the current payment total"}

    result = await propose_revision(TENANT, {"target": "DOCUMENT", "document": {"confirmed_conclusion": {"text": "There are 10 orders"}}}, llm=FakeLLM())
    assert result["proposal"]["checks"]["status"] == "NEEDS_RUN"


@pytest.mark.parametrize("outcome", ["accept", "cancel", "revoked", "reject_revoked"])
def test_selected_compilation_keeps_historical_scope_when_continuing_after_source_change(project, outcome, monkeypatch):
    from copy import deepcopy
    from test.unit_test.api.apps.business_documents.test_sql_query_specification import _table

    old_schema = BusinessDocumentSqlQueryArtifact.get_by_id("s" * 32)
    old_requirements = BusinessDocumentSqlQueryArtifact.get_by_id("r" * 32)
    old_payloads = {kind: deepcopy(BusinessDocumentSqlQueryArtifact.get_by_id(kind * 32).payload) for kind in ("r", "s", "q", "c")}
    table = _table("newscope", "archive.orders", ("order_id",))
    visible_tables = {item["selected_table"]["id"]: item["selected_table"] for item in old_schema.payload["schema_snapshot"]["requirements"]}
    visible_tables[table["id"]] = table

    async def visible(_actor, request, _admin, _role):
        return {"entities": [{"entity_id": identity, "lookup": {"status": "OK"}, "entity": visible_tables[identity]} for identity in request["entity_ids"] if identity in visible_tables]}

    monkeypatch.setattr(BusinessDocumentSqlQuerySchemaService, "load_entities", visible)
    newer_schema = deepcopy(old_schema.payload)
    newer_schema["schema_snapshot"]["original_requirements"] = "- New source and a different basis"
    newer_schema["schema_snapshot"]["requirements"] = [{"term": "New source", "state": "CONFIRMED", "decision": "USER", "selected_table": table}]
    newer_schema["accepted_schema"] = [{"entity_id": table["id"], "version": table["version"], "schema_fingerprint": table["schema_fingerprint"]}]
    payloads = {
        "REQUIREMENTS": {"requirements": [{"statement": "New source and a different basis"}]},
        "SCHEMA": newer_schema,
        "QUERY": {
            "mode": "manual",
            "sql": "SELECT n.order_id AS order_id FROM archive.orders AS n LIMIT :row_limit",
            "parameters": [{"name": "row_limit", "type": "integer", "value": 20}],
            "confirmed_alignment": True,
        },
    }
    newer = {}
    for kind, payload in payloads.items():
        newer[kind] = BusinessDocumentSqlQueryArtifact.create(
            id=kind[0].lower() * 31 + "2",
            project_id=project,
            tenant_id=TENANT,
            kind=kind,
            revision=2,
            payload=payload,
            content_hash=_stable_hash(payload),
            source_proposal_id="p" * 32,
            accepted_by=ACTOR,
        )
    compiled = compile_query_payload(build_project_compile_command(payloads["REQUIREMENTS"], payloads["SCHEMA"], payloads["QUERY"]))
    assert compiled["status"] == "READY", compiled
    packet = {"source_artifact_ids": [newer[k].id for k in ("REQUIREMENTS", "SCHEMA", "QUERY")], "result": compiled}
    newer_compilation = BusinessDocumentSqlQueryArtifact.create(
        id="c" * 31 + "2", project_id=project, tenant_id=TENANT, kind="COMPILATION", revision=2, payload=packet, content_hash=_stable_hash(packet), source_proposal_id="p" * 32, accepted_by=ACTOR
    )
    BusinessDocumentSqlQueryProject.update(
        requirements_artifact_id=newer["REQUIREMENTS"].id, schema_artifact_id=newer["SCHEMA"].id, query_artifact_id=newer["QUERY"].id, execution_profile_id="new-source-profile", state_version=2
    ).where(BusinessDocumentSqlQueryProject.id == project).execute()
    command = {
        "schema_version": "1",
        "expected_state_version": 2,
        "idempotency_key": "pinned-compilation-thread",
        "target": "QUERY",
        "base_artifact_id": "q" * 32,
        "anchor": {"kind": "WHOLE"},
        "text": "Rename the original output; keep the original scope",
    }
    with pytest.raises(ConflictError, match="does not match"):
        BusinessDocumentSqlRevisionService.create_thread(TENANT, ACTOR, project, command, compilation_id=newer_compilation.id)
    thread = BusinessDocumentSqlRevisionService.create_thread(TENANT, ACTOR, project, command, compilation_id="c" * 32)
    started = BusinessDocumentSqlRevisionService.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "pinned-compilation-job"})
    job = BusinessDocumentJobQueue.claim("pinned-source-worker")
    request = job.payload["request"]
    assert request["requirements"] == old_requirements.payload
    assert request["schema"] == old_schema.payload and request["proposed_schema"] == old_schema.payload
    assert request["sql"] == SQL and request["parameters"][0]["value"] == 10
    assert request["base_schema_artifact_id"] == newer["SCHEMA"].id
    assert request["base_requirements_artifact_id"] == newer["REQUIREMENTS"].id
    if outcome == "cancel":
        BusinessDocumentSqlRevisionService.cancel_change(TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": started["state_version"], "idempotency_key": "pinned-source-stop"})
    candidate = SQL.replace("order_id AS order_id", "order_id AS original_id")
    BusinessDocumentSqlAgentService.complete_job(
        job, "pinned-source-worker", job.lease_token, {"proposal": {"sql": candidate, "parameters": request["parameters"], "summary": "Original basis", "checks": {"status": "PASS"}}}
    )
    current = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    if outcome == "cancel":
        assert current["artifact_ids"]["requirements"] == newer["REQUIREMENTS"].id
        assert current["artifact_ids"]["schema"] == newer["SCHEMA"].id and current["execution_profile_id"] == "new-source-profile"
        assert current["pending_proposal"] is None
    elif outcome in {"revoked", "reject_revoked"}:
        from business_documents.application.errors import PermissionDeniedError

        from api.apps.sql_assistant.runtime import SqlRuntime

        for identity in list(visible_tables):
            if identity != table["id"]:
                del visible_tables[identity]
        before = BusinessDocumentSqlQueryProject.get_by_id(project).__data__.copy()
        with pytest.raises(PermissionDeniedError):
            SqlRuntime(TENANT, ACTOR).project(project)
        with pytest.raises(PermissionDeniedError):
            BusinessDocumentSqlRevisionService.decide_change(
                TENANT,
                ACTOR,
                project,
                current["pending_proposal"]["id"],
                {"schema_version": "1", "expected_state_version": current["state_version"], "idempotency_key": "pinned-source-revoked", "decision": "ACCEPT"},
            )
        assert BusinessDocumentSqlQueryProject.get_by_id(project).__data__ == before
        if outcome == "reject_revoked":
            rejected = BusinessDocumentSqlRevisionService.decide_change(
                TENANT,
                ACTOR,
                project,
                current["pending_proposal"]["id"],
                {"schema_version": "1", "expected_state_version": current["state_version"], "idempotency_key": "pinned-source-reject-revoked", "decision": "REJECT"},
            )
            assert rejected["operation_state"] == "IDLE" and rejected["pending_proposal"] is None
            assert rejected["artifact_ids"] == current["artifact_ids"] and rejected["execution_profile_id"] == "new-source-profile"
    else:
        accepted = BusinessDocumentSqlRevisionService.decide_change(
            TENANT,
            ACTOR,
            project,
            current["pending_proposal"]["id"],
            {"schema_version": "1", "expected_state_version": current["state_version"], "idempotency_key": "pinned-source-accept", "decision": "ACCEPT"},
        )
        assert accepted["artifact_ids"]["requirements"] == old_requirements.id
        assert BusinessDocumentSqlQueryArtifact.get_by_id(accepted["artifact_ids"]["schema"]).payload == old_schema.payload
        assert accepted["execution_profile_id"] is None
        assert accepted["compilation"]["result"]["sql"] == candidate
    assert {kind: BusinessDocumentSqlQueryArtifact.get_by_id(kind * 32).payload for kind in old_payloads} == old_payloads
    assert BusinessDocumentSqlQueryArtifact.get_by_id(newer["QUERY"].id).payload == payloads["QUERY"]


def test_human_source_selection_survives_clarification_without_promoting_cell_or_model_text(project):
    raw_source = "Use only actual.catalog.orders"
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "source-evidence-thread",
            "target": "QUERY",
            "anchor": {"kind": "WHOLE"},
            "text": raw_source + "\nSelected cell context: untrusted.cell.source",
        },
    )
    BusinessDocumentSqlRevisionService.request_change(
        TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "source-evidence-first"}, source_text=raw_source
    )
    first = BusinessDocumentJobQueue.claim("source-evidence-worker")
    assert first.payload["request"]["catalog_source_text"] == raw_source
    BusinessDocumentSqlAgentService.complete_job(
        first,
        "source-evidence-worker",
        first.lease_token,
        {"response_kind": "CLARIFICATION", "answer": "invented.model.source: what year?", "questions": [{"id": "year", "text": "What year?", "options": []}]},
    )
    BusinessDocumentSqlRevisionService.reply(
        TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_thread_version": 3, "idempotency_key": "source-evidence-reply", "text": "2026 UTC"}
    )
    current = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)
    BusinessDocumentSqlRevisionService.request_change(
        TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": current["state_version"], "idempotency_key": "source-evidence-next"}
    )
    second = BusinessDocumentJobQueue.claim("source-evidence-worker")
    evidence = second.payload["request"]["catalog_source_text"]
    assert raw_source in evidence and "2026 UTC" in evidence
    assert "untrusted.cell.source" not in evidence and "invented.model.source" not in evidence
    assert all("catalog_source_text" not in message for message in second.payload["request"]["comments"])
