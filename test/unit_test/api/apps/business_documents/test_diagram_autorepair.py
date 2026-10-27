"""Actual job completion with injected generation and bounded diagram recovery."""

import asyncio
from copy import deepcopy

import pytest
import httpx
from peewee import SqliteDatabase

from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _complete,
    _create,
    _draft,
)
from test.unit_test.api.apps.business_documents.test_structured_changes import request_plan
from test.unit_test.business_documents.test_diagram_repair import PATCH, SOURCE
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.recovery import AIRecovery
from api.apps.business_documents.runtime import document_commands, document_queries, job_completion
from api.apps.business_documents.worker import BusinessDocumentJobQueue, BusinessDocumentWorker
from api.db.db_models import BusinessDocumentJob, BusinessDocumentRevision, User
from api.apps.business_documents.adapters.persistence import DOCUMENT_TABLES
from business_documents.application.diagram_repair import DiagramCheck
from business_documents.domain.content import section_hash
from business_documents.domain.diagram_repair import syntax_context

CONTEXT = "Принять, исправить или отклонить заявку — три взаимоисключающих исхода."
# The live acceptance harness injects the actual renderer/model here without
# changing the application code or writing any production document.
CHECKER = None
REPAIR_MODEL = None


@pytest.fixture()
def database(tmp_path):
    # A real worker heartbeat uses another connection while live repair awaits the model.
    database = SqliteDatabase(str(tmp_path / "documents.sqlite"))
    tables = (*DOCUMENT_TABLES, User)
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        yield database
        database.drop_tables(tables)
        database.close()


class Renderer:
    def check(self, source):
        if CHECKER:
            return CHECKER(source)
        return DiagramCheck("syntax_error", "Cannot find if", 8) if source == SOURCE else DiagramCheck("valid")


class Adapter:
    def __init__(self, draft=None, plan=None, unsafe=False, before_repair=None):
        self.draft, self.plan, self.unsafe = draft, plan, unsafe
        self.before_repair = before_repair
        self.calls = []
        self.audit = None

    def generate(self, tenant, system, payload, *, timeout_seconds=None):
        assert tenant == TENANT
        task = payload["job_input"]["task_type"]
        self.calls.append(deepcopy(payload))
        self.audit = {
            "provider": "QA",
            "model": "injected-generation",
            "model_type": "chat",
            "parameters": {"temperature": 0, "top_p": 0.1, "max_completion_tokens": 4096},
            "duration_ms": 0,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        if task == "GENERATE_DRAFT":
            return {"draft": deepcopy(self.draft)}
        if task == "PLAN_CHANGES":
            return deepcopy(self.plan)
        if task == "VERIFY_REQUIREMENT_COVERAGE":
            return {"status": "PASS", "issues": []}
        assert task == "REPAIR_PLANTUML"
        if self.before_repair:
            self.before_repair()
        if self.unsafe:
            return {"edits": [{"line": 5, "replacement": ":Потерять заявку;"}]}
        if REPAIR_MODEL:
            raw, self.audit = REPAIR_MODEL(payload)
            return raw
        return deepcopy(PATCH)

    def consume_execution_audit(self):
        audit, self.audit = self.audit, None
        return audit


def requested_draft():
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(requested["job_id"], {"schema_version": "1", "outcome": "COMPLETE", "questions": []})
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))
    draft = _draft()
    section = next(section for section in draft["sections"] if section["id"] == "4.3")
    section["blocks"] = [{"type": "paragraph", "text": CONTEXT}, {"type": "plantuml", "source": SOURCE}]
    return requested, draft


def diagram(document):
    return next(block["source"] for section in document["sections"] if section["id"] == "4.3" for block in section["blocks"] if block["type"] == "plantuml")


def test_worker_repairs_draft_before_committing_one_revision(database):
    requested, draft = requested_draft()
    adapter = Adapter(draft=draft)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter, diagram_renderer=Renderer())).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    output = job.result["output"]["draft"]
    assert diagram(output) != SOURCE
    assert syntax_context(diagram(output), "activity")["structure"] == syntax_context(SOURCE, "activity")["structure"]
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["GENERATE_DRAFT", "REPAIR_PLANTUML"]
    assert adapter.calls[1]["scenario_text"] == CONTEXT
    assert BusinessDocumentRevision.select().count() == 1
    expected = BusinessDocumentAI._validate(job, {"draft": draft})["draft"]
    assert [s for s in output["sections"] if s["id"] != "4.3"] == [s for s in expected["sections"] if s["id"] != "4.3"]


def test_two_unsafe_patch_attempts_preserve_draft_and_warning(database):
    requested, draft = requested_draft()
    adapter = Adapter(draft=draft, unsafe=True)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter, diagram_renderer=Renderer())).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    assert diagram(job.result["output"]["draft"]) == SOURCE
    assert any(w["code"] == "PLANTUML_REPAIR_NOT_APPLIED" for w in job.result["output"]["draft"]["warnings"])
    assert len(adapter.calls) == 3 and BusinessDocumentRevision.select().count() == 1


def diagram_plan():
    requested, plan = request_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    base = job.payload["current_revision"]["document_ast"]
    section = next(s for s in base["sections"] if s["id"] == "4.3")
    plan["operations"][0].update(section_id="4.3", expected_section_hash=section_hash(section), content={"blocks": [{"type": "paragraph", "text": CONTEXT}, {"type": "plantuml", "source": SOURCE}]})
    return requested, plan, base


def test_worker_repairs_plan_before_coverage_and_guarded_application(database):
    requested, plan, base = diagram_plan()
    adapter = Adapter(plan=plan)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter, diagram_renderer=Renderer())).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "REPAIR_PLANTUML", "VERIFY_REQUIREMENT_COVERAGE"]
    current = document_queries.get_document(job.document_id, AUTHOR)["current_revision"]["document_ast"]
    assert syntax_context(diagram(current), "activity")["structure"] == syntax_context(SOURCE, "activity")["structure"]
    assert all(section == next(old for old in base["sections"] if old["id"] == section["id"]) for section in current["sections"] if section["id"] != "4.3")


def test_unsafe_plan_remains_unapplied_and_preserves_original_revision(database):
    requested, plan, base = diagram_plan()
    adapter = Adapter(plan=plan, unsafe=True)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter, diagram_renderer=Renderer())).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    assert "unapplied_changes" in job.result["output"] and len(adapter.calls) == 3
    assert document_queries.get_document(job.document_id, AUTHOR)["current_revision"]["document_ast"] == base
    assert BusinessDocumentRevision.select().count() == 1


def test_restart_after_repair_checkpoint_does_not_regenerate_or_repair_again(database):
    requested, draft = requested_draft()
    job = BusinessDocumentJobQueue.claim("worker")
    assert job.id == requested["job_id"]
    saved = []

    def interrupt(state):
        saved.append(deepcopy(state))
        if any(entry["repair"].get("repaired") for entry in state.get("diagram_repairs", {}).values()):
            raise RuntimeError("process interrupted")

    adapter = Adapter(draft=draft)
    with pytest.raises(RuntimeError, match="process interrupted"):
        BusinessDocumentAI(adapter, diagram_renderer=Renderer()).process(job, recovery=AIRecovery(save=interrupt))
    output = BusinessDocumentAI(adapter, diagram_renderer=Renderer()).process(job, recovery=AIRecovery(saved[-1]))
    assert len(adapter.calls) == 2 and diagram(output["draft"]) != SOURCE
    job_completion.complete(TENANT, "worker", job.id, output, job.lease_token)
    assert BusinessDocumentRevision.select().count() == 1


def test_cancellation_during_patch_cannot_publish_candidate(database):
    requested, draft = requested_draft()
    job = BusinessDocumentJobQueue.claim("worker")
    assert job.id == requested["job_id"]

    def cancel():
        raise asyncio.CancelledError()

    recovery = AIRecovery()
    adapter = Adapter(draft=draft, before_repair=cancel)
    with pytest.raises(asyncio.CancelledError):
        BusinessDocumentAI(adapter, diagram_renderer=Renderer()).process(job, recovery=recovery)
    assert BusinessDocumentRevision.select().count() == 0
    assert recovery.state["candidate"]["draft"] and len(adapter.calls) == 2
    assert any(attempt["status"] == "reserved" for entry in recovery.state["diagram_repairs"].values() for attempt in entry["repair"].get("attempts", []))


def test_stale_document_during_patch_cannot_be_committed(database):
    requested, plan, base = diagram_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])

    def edit():
        current = document_queries.get_document(job.document_id, AUTHOR)
        document_commands.execute(
            TENANT,
            AUTHOR,
            job.document_id,
            _command(current, "ADD_COMMENT", {"revision_id": current["current_revision"]["revision_id"], "section_id": None, "text": "Новые требования", "anchor": None}),
        )

    adapter = Adapter(plan=plan, before_repair=edit)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter, diagram_renderer=Renderer())).run_once()
    assert document_queries.get_document(job.document_id, AUTHOR)["current_revision"]["document_ast"] == base
    assert BusinessDocumentRevision.select().count() == 1


def test_shared_global_budget_is_not_reset_for_diagram_repair(database):
    requested, draft = requested_draft()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    recovery = AIRecovery()
    recovery.state["calls"] = 5
    adapter = Adapter(draft=draft)
    output = BusinessDocumentAI(adapter, diagram_renderer=Renderer()).process(job, recovery=recovery)
    assert len(adapter.calls) == 1 and recovery.state["calls"] == 6
    assert diagram(output["draft"]) == SOURCE
    assert any(w["code"] == "PLANTUML_REPAIR_NOT_APPLIED" for w in output["draft"]["warnings"])


def test_renderer_outage_preserves_plan_without_requesting_model_repair(database):
    requested, plan, _ = diagram_plan()
    adapter = Adapter(plan=plan)

    class Unavailable:
        def check(self, source):
            return DiagramCheck("unavailable")

    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter, diagram_renderer=Unavailable())).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    assert "change_plan" in job.result["output"]
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    assert any(w["code"] == "PLANTUML_REPAIR_NOT_APPLIED" for w in job.result["output"]["warnings"])


def test_provider_failure_resume_preserves_candidate_and_never_resends_uncertain_patch(database):
    requested, draft = requested_draft()
    job = BusinessDocumentJobQueue.claim("worker")
    assert job.id == requested["job_id"]
    saved = []

    def fail_provider():
        request = httpx.Request("POST", "https://model.invalid")
        raise httpx.HTTPStatusError("Unavailable", request=request, response=httpx.Response(503, request=request))

    adapter = Adapter(draft=draft, before_repair=fail_provider)
    with pytest.raises(httpx.HTTPStatusError):
        BusinessDocumentAI(adapter, diagram_renderer=Renderer()).process(job, recovery=AIRecovery(save=lambda state: saved.append(state)))
    adapter.before_repair = None
    output = BusinessDocumentAI(adapter, diagram_renderer=Renderer()).process(job, recovery=AIRecovery(saved[-1]))
    assert len(adapter.calls) == 2
    assert diagram(output["draft"]) == SOURCE
    assert any(w["code"] == "PLANTUML_REPAIR_NOT_APPLIED" for w in output["draft"]["warnings"])
    job_completion.complete(TENANT, "worker", job.id, output, job.lease_token)
    assert BusinessDocumentRevision.select().count() == 1
