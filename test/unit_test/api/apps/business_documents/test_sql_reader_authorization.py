"""Reader/foreign identities cannot mutate SQL, rows, jobs or command receipts."""

from copy import deepcopy

import pytest

# The fixture module installs the isolated api.apps package before API imports.
from test.unit_test.api.apps.business_documents.test_sql_query_revisions import ACTOR, TENANT, SQL, project as accepted_project
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Agents
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService as Revisions
from api.apps.business_documents.sql_query_runs import BusinessDocumentSqlRunService as Runs
from api.db.db_models import BusinessDocumentSqlQueryRun
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture
def project():
    yield from accepted_project.__wrapped__()


def command(**fields):
    return {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "reader-boundary", **fields}


PARAMETERS = [{"name": "row_limit", "type": "integer", "value": 10}]
ATTEMPTS = (
    (Agents, "revise_question", (), command(source_request="Changed question")),
    (Agents, "update_project", (), command(title="New title")),
    (Agents, "copy_project", (), command()),
    (Agents, "compile_project", (), command()),
    (Agents, "validate_manual_query", (), {"schema_version": "1", "expected_state_version": 1, "sql": SQL, "parameters": PARAMETERS}),
    (Agents, "save_manual_query", (), command(sql=SQL, parameters=PARAMETERS, confirmed_alignment=True)),
    (Agents, "restore_query", (), command(compilation_id="c" * 32)),
    (Agents, "request_agent", (), command(kind="QUERY", payload={})),
    (Agents, "decide_proposal", ("proposal",), command(decision="ACCEPT")),
    (Revisions, "create_thread", (), command(target="QUERY", anchor={"kind": "WHOLE"}, text="Change filter")),
    (Revisions, "save_draft", (), {"schema_version": "1", "expected_state_version": 1, "expected_draft_version": 0, "steps": [{"title": "Draft", "sql": SQL, "parameters": "[]"}]}),
    (Revisions, "reply", ("thread",), {"schema_version": "1", "expected_thread_version": 1, "idempotency_key": "reader-boundary", "text": "A new comment"}),
    (Revisions, "reattach", ("thread",), command(expected_thread_version=1, anchor={"kind": "WHOLE"})),
    (Revisions, "request_change", ("thread",), command()),
    (Revisions, "cancel_change", (), command()),
    (Revisions, "reopen_schema", (), command()),
    (Revisions, "decide_change", ("proposal",), command(decision="ACCEPT")),
    (Runs, "configure_connection", (), command(selected_profile_id="profile")),
    (Runs, "check_connection", (), {"schema_version": "1", "selected_profile_id": "profile"}),
    (Runs, "run", (), command(selected_profile_id="profile")),
    (Runs, "complete", (), command(run_id="result")),
    (Runs, "cancel_result", (), command(run_id="result")),
    (Runs, "run_python", ("result",), command(code="def main(columns, rows):\n    return {'columns': columns, 'rows': rows}")),
    (Runs, "run_lookup", ("result",), command(source_column="order_id", target_entity_id="orders", target_key_column_id="id", target_value_column_ids=["status"])),
    (Runs, "propose_conclusion", ("result",), command()),
    (Runs, "confirm_conclusion", ("result",), command(proposal_id="proposal", text="There is one order")),
)


def database_snapshot():
    return {table.__name__: deepcopy(list(table.select().order_by(table.id).dicts())) for table in Agents.model_tables()}


@pytest.mark.parametrize("identity", ["reader", "foreign-owner", "foreign-tenant"])
@pytest.mark.parametrize("owner,method,prefix,payload", ATTEMPTS, ids=[item[1] for item in ATTEMPTS])
def test_mutations_reject_identity_before_any_effect(project, monkeypatch, identity, owner, method, prefix, payload):
    from api.apps.business_documents import sql_query_runs, sql_query_revisions

    def forbidden(*_args, **_kwargs):
        pytest.fail("An unauthorized identity must not reach catalog/compiler/network/model dependencies")

    monkeypatch.setattr(Agents, "_compile_manual_query", forbidden)
    monkeypatch.setattr(sql_query_runs, "_source", forbidden)
    monkeypatch.setattr(sql_query_runs, "_binding", forbidden)
    monkeypatch.setattr(sql_query_runs, "check_postgres_connection", forbidden)
    monkeypatch.setattr(sql_query_revisions, "compile_query_payload", forbidden)
    BusinessDocumentSqlQueryRun.create(
        id="result",
        tenant_id=TENANT,
        project_id=project,
        status="READY",
        compilation_id="c" * 32,
        profile_id="profile",
        profile_version=1,
        columns=["order_id"],
        rows=[[42]],
        checks={"status": "PASS"},
    )
    before = database_snapshot()
    tenant = "different-tenant" if identity == "foreign-tenant" else TENANT
    actor = "different-owner" if identity == "foreign-owner" else ACTOR
    role = "READER" if identity == "reader" else "AUTHOR_CREATOR"
    with pytest.raises(BusinessDocumentError) as rejected:
        getattr(owner, method)(tenant, actor, project, *prefix, deepcopy(payload), access_role=role)
    assert rejected.value.status == (404 if identity == "foreign-tenant" else 403)
    assert database_snapshot() == before


def test_owner_reader_can_read_sql_history_and_rows(project):
    BusinessDocumentSqlQueryRun.create(
        id="result",
        tenant_id=TENANT,
        project_id=project,
        status="READY",
        compilation_id="c" * 32,
        profile_id="profile",
        profile_version=1,
        columns=["order_id"],
        rows=[[42]],
        row_count=1,
        checks={"status": "PASS"},
    )
    before = database_snapshot()
    saved = Agents.get_project(TENANT, ACTOR, project, access_role="READER")
    assert saved["capabilities"]["edit"] is False and saved["capabilities"]["execute"] is False
    workspace = Revisions.workspace(TENANT, ACTOR, project, access_role="READER")
    assert workspace["sql"] == SQL
    assert {artifact["kind"] for artifact in workspace["history"]} >= {"REQUIREMENTS", "SCHEMA", "QUERY"}
    assert Runs.preview(TENANT, ACTOR, project, "result", access_role="READER")["rows"] == [[42]]
    assert database_snapshot() == before


def test_reader_cannot_replay_a_command_accepted_before_role_revocation(project):
    request = command(target="QUERY", anchor={"kind": "WHOLE"}, text="Change filter")
    accepted = Revisions.create_thread(TENANT, ACTOR, project, request)
    before = database_snapshot()
    with pytest.raises(BusinessDocumentError) as rejected:
        Revisions.create_thread(TENANT, ACTOR, project, request, access_role="READER")
    assert rejected.value.status == 403
    assert database_snapshot() == before
    assert Revisions.workspace(TENANT, ACTOR, project, access_role="READER")["threads"][0]["id"] == accepted["id"]
