"""A fixed metric uses the actual shared artifacts, queue, access and lifecycle."""

from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json

import pytest
from peewee import SqliteDatabase

from test.unit_test.api.apps.business_documents.test_sql_query_runs import actual_current_actor_access  # noqa: F401 - isolated api.apps
from api.apps.business_documents import sql_query_runs as runs, sql_execution_registry as registry
from api.apps.business_documents import sql_query_postgres as postgres
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService as Schema
from api.apps.business_documents.worker import BusinessDocumentJobQueue as Queue
from api.apps.sql_assistant.service import SqlAssistantService
from api.db.db_models import Connector, BusinessDocumentSqlExecutionProfile, BusinessDocumentSqlCatalogBinding, BusinessDocumentJob, BusinessDocumentSqlQueryRun as Run
from api.db.services.managed_resource_service import ManagedResourceService
from business_documents.application.errors import BusinessDocumentError
from business_documents.sql_query.llm_logs import FQN, completeness, period

ACTOR, TENANT = "author-1", "tenant-1"
ENTITY = "491922b2-66ca-4c3d-9c7d-651a68d491b9"
PERIOD = {"from": "2026-08-01T00:00:00+00:00", "to": "2026-09-01T00:00:00+00:00"}


def entity():
    columns = [
        {"fqn": FQN + "." + name, "name": name, "technical_name": name, "data_type": kind, "constraint": "PRIMARY_KEY" if name == "log_id" else ""}
        for name, kind in (
            ("log_id", "BIGINT"),
            ("model_name", "VARCHAR"),
            ("request_timestamp_start", "TIMESTAMP WITH TIME ZONE"),
            ("prompt", "TEXT"),
        )
    ]
    return {
        "id": ENTITY,
        "fqn": FQN,
        "technical_name": "llm_requests_log",
        "service": "docker_postgres_bot",
        "database": "bot",
        "schema": "public",
        "version": 0.6,
        "schema_fingerprint": "sha256:" + "a" * 64,
        "column_details": columns,
    }


@pytest.fixture
def pilot_config():
    return {"host": "127.0.0.1", "port": 5432, "database": "bot", "credentials": {"username": "llm_reader", "password": "qa-only"}}


@pytest.fixture
def pilot_database():
    return SqliteDatabase(":memory:", check_same_thread=False)


@pytest.fixture
def pilot(monkeypatch, pilot_config, pilot_database):
    database = pilot_database
    tables = (*Projects.model_tables(), Connector, BusinessDocumentSqlExecutionProfile, BusinessDocumentSqlCatalogBinding)
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False), database.connection_context():
        database.create_tables(tables)
        monkeypatch.setattr(ManagedResourceService, "owner_id", classmethod(lambda cls: TENANT))
        monkeypatch.setattr(registry, "_audit", lambda *args: None)
        monkeypatch.setattr(registry, "_catalog_access_allowed", lambda *args: True)
        config = pilot_config
        Connector.create(id="connector-1", tenant_id=TENANT, name="pilot", source="postgresql", input_type="poll", config=config)
        profile = registry.BusinessDocumentSqlExecutionRegistryService.create_profile(
            ACTOR,
            {
                "schema_version": "1",
                "name": "pilot",
                "connector_id": "connector-1",
                "dialect": "postgres",
                "allowed_schemas": ["public"],
                "statement_timeout_ms": 15000,
                "max_rows": 1000,
                "max_result_bytes": 5000000,
                "enabled": True,
            },
            True,
        )
        registry.BusinessDocumentSqlExecutionRegistryService.create_binding(
            ACTOR,
            {"schema_version": "1", "catalog_service": "docker_postgres_bot", "catalog_database": "bot", "catalog_schema": "public", "execution_profile_id": profile["id"], "enabled": True},
            True,
        )
        policy = {"version": "1", "profile_id": profile["id"], "entity_id": ENTITY, "db_role": "llm_reader", "actors": [ACTOR], "unknown_action": "warn", "incomplete_action": "block"}
        monkeypatch.setenv("SQL_LLM_LOGS_POLICY", json.dumps(policy))

        async def load(*args, **kwargs):
            return {"entities": [{"entity_id": ENTITY, "entity": entity(), "lookup": {"status": "OK"}}]}

        monkeypatch.setattr(Schema, "load_entities", load)

        async def verify(*args, **kwargs):
            return False

        monkeypatch.setattr(Schema, "verify_snapshot", verify)
        monkeypatch.setattr(runs, "_current_actor_access", lambda actor: ("AUTHOR_CREATOR", False))
        monkeypatch.setattr(runs, "explain_postgres", lambda *args, **kwargs: None)
        monkeypatch.setattr(postgres, "check_source_access", lambda *args: None)

        def execute(*args, **kwargs):
            kwargs["on_dispatch"]({"pid": 123, "backend_start": "1", "application_name": "qa"})
            kwargs["on_completed"]()
            return {"columns": ["model_name", "request_count"], "rows": [["qwen", 2], [None, 1]], "row_count": 2, "result_bytes": 30, "duration_ms": 1}

        monkeypatch.setattr(runs, "execute_postgres", execute)
        yield SqlAssistantService(TENANT, ACTOR), policy
        database.drop_tables(tables)
        database.close()


def create(service, key="create-1"):
    return service.create_llm_count({"client_request_id": key, **PERIOD})


def queue(service, session):
    raw = {
        "client_request_id": "execute-1",
        "expected_revision": session["revision"],
        "expected_project_version": session["project"]["state_version"],
        "type": "execute",
        "payload": {"selected_profile_id": session["project"]["execution_profile_id"]},
    }
    service.command(session["id"], raw)
    return Queue.claim("pilot-worker"), raw


def test_metric_is_structured_private_and_has_no_implicit_model_or_execution(pilot):
    service, policy = pilot
    session = create(service)
    assert BusinessDocumentJob.select().count() == 0
    assert create(service)["id"] == session["id"]
    assert "prompt" not in json.dumps(session["project"]["artifacts"])
    query = session["project"]["artifacts"]["query"]
    assert query["select"][1]["kind"] == "count" and query["joins"] == []
    assert [item["operator"] for item in query["filters"]] == ["gte", "lt"]
    job, raw = queue(service, session)
    service.command(session["id"], raw)
    assert BusinessDocumentJob.select().count() == 1
    output = runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
    runs.BusinessDocumentSqlRunService.complete_job(job, "pilot-worker", job.lease_token, output)
    result = service.result(session["id"], job.payload["run_id"])
    assert [item["values"] for item in result["rows"]] == [["qwen", 2], [None, 1]]
    assert result["source_completeness"] == "UNKNOWN"
    assert Run.get_by_id(job.payload["run_id"]).execution["phase"] == "SUCCEEDED"


@pytest.mark.parametrize("operation", ["answer_result", "save_draft", "publish_definition"])
def test_pilot_does_not_enable_free_sql_or_model_calls(pilot, monkeypatch, operation):
    service, _ = pilot
    session = create(service)
    monkeypatch.setattr(service, "_prepare_answer", lambda *args: pytest.fail("Pilot must not call the model"))
    with pytest.raises(BusinessDocumentError) as failure:
        service.command(session["id"], {"client_request_id": "unsupported", "expected_revision": 1, "expected_project_version": 1, "type": operation, "payload": {}})
    assert failure.value.code == "SQL_LOG_OPERATION_UNSUPPORTED"
    assert BusinessDocumentJob.select().count() == 0


@pytest.mark.parametrize("change", ["actor", "policy", "profile", "grants", "coverage"])
def test_revocation_blocks_results_after_execution(pilot, monkeypatch, change):
    service, policy = pilot
    session = create(service)
    job, _ = queue(service, session)
    output = runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
    runs.BusinessDocumentSqlRunService.complete_job(job, "pilot-worker", job.lease_token, output)
    if change == "profile":
        BusinessDocumentSqlExecutionProfile.update(version=2).execute()
    elif change == "grants":

        def denied(*args):
            raise BusinessDocumentError("SQL_SOURCE_ACCESS_DENIED", "Revoked", 403)

        monkeypatch.setattr(postgres, "check_source_access", denied)
    else:
        if change == "actor":
            policy["actors"] = ["other"]
        if change == "policy":
            policy["version"] = "2"
        if change == "coverage":
            policy["unknown_action"] = "block"
        monkeypatch.setenv("SQL_LLM_LOGS_POLICY", json.dumps(policy))
    with pytest.raises(BusinessDocumentError):
        service.result(session["id"], job.payload["run_id"])


def test_lost_ack_is_unknown_not_retry_and_completion_receipt_is_not_lost(pilot, monkeypatch):
    service, _ = pilot
    session = create(service)
    job, _ = queue(service, session)
    calls = []

    def lost(*args, **kwargs):
        calls.append(True)
        kwargs["on_dispatch"]({"pid": 123, "backend_start": "1", "application_name": "qa"})
        raise BusinessDocumentError("SQL_EXECUTION_UNKNOWN", "Lost ACK", 503)

    monkeypatch.setattr(runs, "execute_postgres", lost)
    with pytest.raises(BusinessDocumentError) as failure:
        runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
    runs.BusinessDocumentSqlRunService.fail_job(job, "pilot-worker", job.lease_token, {"code": failure.value.code})
    run = Run.get_by_id(job.payload["run_id"])
    assert run.status == "UNKNOWN" and run.execution["backend"]["pid"] == 123
    with pytest.raises(BusinessDocumentError):
        runs.BusinessDocumentSqlRunService.run(TENANT, ACTOR, session["id"], {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "new", "selected_profile_id": None})
    assert len(calls) == 1


def test_expired_lease_with_cancel_request_does_not_claim_database_cancellation(pilot):
    service, _ = pilot
    session = create(service)
    job, _ = queue(service, session)
    Run.update(status="CANCEL_REQUESTED", execution={"phase": "DISPATCHING", "attempt_id": "once"}).where(Run.id == job.payload["run_id"]).execute()
    BusinessDocumentJob.update(lease_expires_at=1, max_attempts=3).where(BusinessDocumentJob.id == job.id).execute()
    assert Queue.recover_stale() == (0, 1)
    assert Run.get_by_id(job.payload["run_id"]).status == "UNKNOWN"
    assert BusinessDocumentJob.get_by_id(job.id).status == "FAILED"


@pytest.mark.parametrize("phase,expected", [("DISPATCHING", "UNKNOWN"), ("SUCCEEDED", "SUCCEEDED"), (None, "UNKNOWN")])
def test_redelivery_of_cancel_request_does_not_claim_a_database_stop(pilot, monkeypatch, phase, expected):
    service, _ = pilot
    session = create(service)
    job, _ = queue(service, session)
    run = Run.get_by_id(job.payload["run_id"])
    Run.update(status="CANCEL_REQUESTED", execution={**run.execution, "phase": phase}).where(Run.id == run.id).execute()
    monkeypatch.setattr(runs, "execute_postgres", lambda *args, **kwargs: pytest.fail("Never redispatch"))
    with pytest.raises(BusinessDocumentError) as failure:
        runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
    assert failure.value.code == "SQL_RUN_STATE_CHANGED"
    runs.BusinessDocumentSqlRunService.fail_job(job, "pilot-worker", job.lease_token, {"code": failure.value.code})
    assert Run.get_by_id(run.id).status == expected


def test_receipt_of_completed_sql_survives_worker_loss(pilot):
    service, _ = pilot
    session = create(service)
    job, _ = queue(service, session)
    runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
    runs.BusinessDocumentSqlRunService.fail_job(job, "pilot-worker", job.lease_token, {"code": "JOB_LEASE_EXPIRED"})
    run = Run.get_by_id(job.payload["run_id"])
    assert run.status == "SUCCEEDED" and run.rows == [] and run.execution["output_completeness"] == "unavailable"


@pytest.mark.parametrize("same_attempt", [True, False])
def test_only_original_late_completion_can_resolve_unknown(pilot, monkeypatch, same_attempt):
    service, _ = pilot
    session = create(service)
    job, _ = queue(service, session)
    callbacks = []

    def interrupted(*args, **kwargs):
        kwargs["on_dispatch"]({"pid": 123, "backend_start": "1", "application_name": "qa"})
        callbacks.append(kwargs["on_completed"])
        raise BusinessDocumentError("SQL_EXECUTION_UNKNOWN", "Lost reply", 503)

    monkeypatch.setattr(runs, "execute_postgres", interrupted)
    with pytest.raises(BusinessDocumentError):
        runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
    runs.BusinessDocumentSqlRunService.fail_job(job, "pilot-worker", job.lease_token, {"code": "SQL_EXECUTION_UNKNOWN"})
    if not same_attempt:
        run = Run.get_by_id(job.payload["run_id"])
        Run.update(execution={**run.execution, "attempt_id": "different"}).where(Run.id == run.id).execute()
        with pytest.raises(BusinessDocumentError):
            callbacks[0]()
    else:
        callbacks[0]()
    run = Run.get_by_id(job.payload["run_id"])
    assert run.status == ("SUCCEEDED" if same_attempt else "UNKNOWN")
    assert run.rows == [] and len(callbacks) == 1


@pytest.mark.parametrize("start,end", [("2026-01-01", "2026-01-02"), ("2026-01-02T00:00Z", "2026-01-01T00:00Z")])
def test_period_needs_offsets_and_positive_range(start, end):
    with pytest.raises(ValueError):
        period(start, end)


def test_completeness_needs_current_evidence_for_both_period_boundaries():
    now = datetime.now(timezone.utc)
    policy = {
        "unknown_action": "block",
        "incomplete_action": "warn",
        "completeness": {
            "status": "COMPLETE",
            "observed_at": now.isoformat(),
            "valid_until": (now + timedelta(minutes=2)).isoformat(),
            "evidence_ref": "qa-ingestion:1",
            "coverage_from": PERIOD["from"],
            "coverage_to": PERIOD["to"],
        },
    }
    assert completeness(policy, PERIOD, now)["status"] == "COMPLETE"
    for key, value in (
        ("valid_until", (now - timedelta(seconds=1)).isoformat()),
        ("coverage_to", "2026-08-31T00:00Z"),
        ("observed_at", (now - timedelta(minutes=6)).isoformat()),
        ("evidence_ref", ""),
    ):
        changed = deepcopy(policy)
        changed["completeness"][key] = value
        result = completeness(changed, PERIOD, now)
        assert result["status"] == "UNKNOWN" and result["action"] == "block"


@pytest.mark.parametrize("status,action", [("COMPLETE", "allow"), ("PARTIAL", "warn"), ("STALE", "warn")])
def test_completeness_warning_preserves_the_source_status(status, action):
    now = datetime.now(timezone.utc)
    policy = {
        "incomplete_action": "warn",
        "completeness": {
            "status": status,
            "observed_at": now.isoformat(),
            "valid_until": (now + timedelta(minutes=1)).isoformat(),
            "evidence_ref": "qa:coverage",
            "coverage_from": PERIOD["from"],
            "coverage_to": PERIOD["to"],
        },
    }
    result = completeness(policy, PERIOD, now)
    assert (result["status"], result["action"]) == (status, action)
