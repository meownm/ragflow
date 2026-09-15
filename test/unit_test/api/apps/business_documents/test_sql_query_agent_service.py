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

from api.apps.business_documents.errors import ConflictError
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService
from api.apps.business_documents.sql_query_requirements import TenantRequirementsAnalyst
from api.apps.business_documents.worker import BusinessDocumentJobQueue, BusinessDocumentWorker
from api.db.db_models import (
    BusinessDocumentJob,
    BusinessDocumentSqlAgentCommand,
    BusinessDocumentSqlAgentProposal,
    BusinessDocumentSqlQueryArtifact,
    BusinessDocumentSqlQueryProject,
)
from business_documents.sql_query.requirements_analysis import AnalyzeRequirementsCommand

TENANT = "tenant-1"
ACTOR = "author-1"


@pytest.fixture()
def database():
    database = SqliteDatabase(":memory:", check_same_thread=False)
    tables = BusinessDocumentSqlAgentService.model_tables()
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        yield database
        database.drop_tables(tables)
        database.close()


class RequirementsRunner:
    def process(self, job):
        assert job.job_type == "SQL_AGENT_REQUIREMENTS"
        assert job.payload["request"]["source_request"] == "Покажи заказы за период"
        return {
            "schema_version": "1",
            "status": "PROPOSED",
            "proposal": {
                "requirements": [
                    {
                        "id": "REQ-OUT-001",
                        "kind": "output",
                        "statement": "Вывести заказы",
                        "source_quote": "Покажи заказы",
                        "rationale": "Определяет результат",
                        "status": "PROPOSED",
                    }
                ],
                "questions": [],
            },
            "warning": None,
            "diagnostic": None,
        }


@pytest.mark.asyncio
async def test_tenant_requirements_analyst_uses_one_bounded_contract_call():
    calls = []

    class FakeLLM:
        async def async_generate(self, tenant_id, system_prompt, payload):
            calls.append((tenant_id, system_prompt, payload))
            return {
                "schema_version": "1",
                "requirements": [
                    {
                        "kind": "output",
                        "statement": "Вывести заказы",
                        "source_quote": "Покажи заказы",
                        "rationale": "Определяет результат",
                    }
                ],
                "questions": [],
            }

    result = await TenantRequirementsAnalyst(FakeLLM(), timeout_seconds=1).propose(AnalyzeRequirementsCommand(tenant_id=TENANT, locale="ru", source_request="Покажи заказы"))

    assert result["schema_version"] == "1"
    assert len(calls) == 1
    tenant_id, system_prompt, payload = calls[0]
    assert tenant_id == TENANT
    assert "{{context_json}}" not in system_prompt
    assert "additionalProperties" in system_prompt
    assert payload["context"]["source_request"] == "Покажи заказы"
    assert payload["job_input"] == {"task_type": "ANALYZE_SQL_REQUIREMENTS"}


def _create_project():
    return BusinessDocumentSqlAgentService.create_project(
        TENANT,
        ACTOR,
        {
            "schema_version": "1",
            "title": "Заказы",
            "source_request": "Покажи заказы за период",
            "locale": "ru",
        },
    )


def test_durable_requirements_agent_cycle_is_idempotent_and_human_gated(database):
    project = _create_project()
    request = {
        "schema_version": "1",
        "expected_state_version": 1,
        "idempotency_key": "run-requirements-1",
        "kind": "REQUIREMENTS",
        "payload": {},
    }

    queued = BusinessDocumentSqlAgentService.request_agent(TENANT, ACTOR, project["id"], request)
    replay = BusinessDocumentSqlAgentService.request_agent(TENANT, ACTOR, project["id"], request)

    assert queued == replay
    assert queued["operation_state"] == "RUNNING"
    assert queued["state_version"] == 2
    assert queued["current_job"]["status"] == "PENDING"
    assert BusinessDocumentJob.select().count() == 1
    assert BusinessDocumentSqlAgentCommand.select().count() == 1

    assert BusinessDocumentWorker(worker_id="sql-agent-test", sql_agent_runner=RequirementsRunner()).run_once()
    review = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project["id"])

    assert review["operation_state"] == "REVIEW"
    assert review["pending_proposal"]["kind"] == "REQUIREMENTS"
    assert review["artifacts"]["requirements"] is None
    proposal_id = review["pending_proposal"]["id"]

    accepted = BusinessDocumentSqlAgentService.decide_proposal(
        TENANT,
        ACTOR,
        project["id"],
        proposal_id,
        {
            "schema_version": "1",
            "expected_state_version": 2,
            "idempotency_key": "accept-requirements-1",
            "decision": "ACCEPT",
            "artifact_payload": None,
        },
    )

    assert accepted["stage"] == "SCHEMA"
    assert accepted["operation_state"] == "IDLE"
    assert accepted["next_agent"] == "SCHEMA"
    assert accepted["artifacts"]["requirements"]["requirements"][0]["statement"] == "Вывести заказы"
    assert BusinessDocumentSqlQueryArtifact.select().count() == 1
    assert BusinessDocumentSqlAgentProposal.get_by_id(proposal_id).status == "ACCEPTED"

    with pytest.raises(ConflictError, match="next agent must be SCHEMA"):
        BusinessDocumentSqlAgentService.request_agent(
            TENANT,
            ACTOR,
            project["id"],
            {
                "schema_version": "1",
                "expected_state_version": 3,
                "idempotency_key": "wrong-agent-1",
                "kind": "QUERY",
                "payload": {},
            },
        )


def test_rejected_proposal_can_be_rerun_without_creating_an_artifact(database):
    project = _create_project()
    BusinessDocumentSqlAgentService.request_agent(
        TENANT,
        ACTOR,
        project["id"],
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "run-1",
            "kind": "REQUIREMENTS",
            "payload": {},
        },
    )
    BusinessDocumentWorker(worker_id="sql-agent-test", sql_agent_runner=RequirementsRunner()).run_once()
    review = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project["id"])

    rejected = BusinessDocumentSqlAgentService.decide_proposal(
        TENANT,
        ACTOR,
        project["id"],
        review["pending_proposal"]["id"],
        {
            "schema_version": "1",
            "expected_state_version": 2,
            "idempotency_key": "reject-1",
            "decision": "REJECT",
            "artifact_payload": None,
        },
    )

    assert rejected["operation_state"] == "IDLE"
    assert rejected["next_agent"] == "REQUIREMENTS"
    assert BusinessDocumentSqlQueryArtifact.select().count() == 0


def test_stale_agent_result_cannot_publish_a_proposal(database):
    project = _create_project()
    queued = BusinessDocumentSqlAgentService.request_agent(
        TENANT,
        ACTOR,
        project["id"],
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "run-stale-1",
            "kind": "REQUIREMENTS",
            "payload": {},
        },
    )
    job = BusinessDocumentJobQueue.claim("stale-worker", lease_ms=60_000)
    assert job is not None
    BusinessDocumentSqlQueryProject.update(state_version=3, current_job_id=None, operation_state="IDLE").where(BusinessDocumentSqlQueryProject.id == project["id"]).execute()

    BusinessDocumentSqlAgentService.complete_job(
        job,
        "stale-worker",
        job.lease_token,
        RequirementsRunner().process(job),
    )

    assert BusinessDocumentJob.get_by_id(queued["current_job"]["id"]).status == "STALE"
    assert BusinessDocumentSqlAgentProposal.select().count() == 0
    current = BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project["id"])
    assert current["operation_state"] == "IDLE"
