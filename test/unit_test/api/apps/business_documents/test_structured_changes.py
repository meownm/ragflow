"""Bounded structured generation and resumable semantic verification."""

from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_accept_proposal import _document, _plan, _request_changes, database as proposal_database
from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _complete_review_assessment,
    _create,
    _draft,
    _question_batch,
    _request_and_complete_draft,
)
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.evidence import BusinessDocumentEvidence
from api.apps.business_documents import worker as worker_module
from api.apps.business_documents.runtime import document_commands, document_queries
from api.apps.business_documents.worker import BusinessDocumentWorker
from api.db.db_models import BusinessDocument, BusinessDocumentEvent, BusinessDocumentEvidenceSnapshot, BusinessDocumentJob, BusinessDocumentRevision
from business_documents.domain.content import section_hash
from business_documents.domain.block_diff import with_block_ids
from business_documents.application.errors import ValidationError


@pytest.mark.parametrize("kind", ["PREPARE_CHANGES", "APPLY_CHANGES"])
def test_saved_unsupported_block_survives_command_replay_and_worker_application(database, kind):
    document = _document(open_questions=False)
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "ACCEPTED"}))
    document = _complete_review_assessment(document_queries.get_document(document["document_id"], AUTHOR))
    revision = BusinessDocumentRevision.get_by_id(document["current_revision"]["revision_id"])
    invalid_ast = deepcopy(revision.document_ast)
    next(section for section in invalid_ast["sections"] if section["id"] == "4.3")["blocks"].append({"type": "bpmn", "source": "<definitions/>"})
    BusinessDocumentRevision.update(document_ast=invalid_ast).where(BusinessDocumentRevision.id == revision.id).execute()
    jobs_before = BusinessDocumentJob.select().count()
    command = _command(document, kind, {"base_revision_id": revision.id})

    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    assert document_commands.execute(TENANT, AUTHOR, document["document_id"], command)["idempotent_replay"]
    adapter = PlanAdapter(_plan(document, requested)["change_plan"], None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()

    current = document_queries.get_document(document["document_id"], AUTHOR)
    assert current["operation_state"] == "IDLE"
    assert BusinessDocumentJob.select().count() == jobs_before + 1
    assert BusinessDocumentJob.get_by_id(requested["job_id"]).status == "COMPLETED"
    old_scenario = next(section for section in with_block_ids(invalid_ast)["sections"] if section["id"] == "4.3")
    assert next(section for section in current["current_revision"]["document_ast"]["sections"] if section["id"] == "4.3") == old_scenario
    assert "<definitions/>" in current["current_revision"]["section_texts"]["4.3"]
    assert BusinessDocumentRevision.get_by_id(revision.id).document_ast == invalid_ast


@pytest.fixture()
def database():
    yield from proposal_database.__wrapped__()


class PlanAdapter:
    def __init__(self, plan, failure="transport"):
        self.plan, self.failure = plan, failure
        self.calls = []
        self.audit = None

    def generate(self, tenant_id, system, payload, *, timeout_seconds=None):
        task = payload["job_input"]["task_type"]
        self.calls.append(deepcopy(payload))
        self.audit = {
            "provider": "Ollama",
            "model": "test",
            "model_type": "chat",
            "parameters": {
                "temperature": 0,
                "top_p": 0.1,
                "max_completion_tokens": 65536 if task == "PLAN_CHANGES" else 2048,
            },
            "duration_ms": 10,
            "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        if task == "PLAN_CHANGES":
            return deepcopy(self.plan)
        if self.failure == "transport":
            self.failure = None
            raise TimeoutError("Verifier temporarily unavailable")
        if self.failure == "malformed":
            self.failure = None
            return {"status": "FAIL", "issues": []}
        if self.failure == "missing":
            self.failure = None
            source_id = next(key for key in payload["job_input"]["sources"] if not key.startswith("section:"))
            return {"status": "FAIL", "issues": [{"source_id": source_id, "section_id": "5.5", "reason": "Не выполнено подтверждённое требование."}]}
        return {"status": "PASS", "issues": []}

    def consume_execution_audit(self):
        audit, self.audit = self.audit, None
        return audit


def request_plan():
    document = _document()
    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "DECIDE_PROPOSAL",
            {
                "proposal_id": document["protocol"]["proposals"][0]["proposal_id"],
                "decision": "ACCEPTED",
            },
        ),
    )
    document, requested = _request_changes(document, preview=False)
    return requested, _plan(document, requested)["change_plan"]


@pytest.mark.parametrize("known_reference", [True, False])
def test_change_plan_citations_use_the_pinned_chunk_provenance(database, known_reference):
    requested, plan = request_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    snapshot = BusinessDocumentEvidence._normalize(job, "query", ["dataset"], [{"dataset_id": "dataset", "document_id": "source", "chunk_id": "chunk", "content": "Controls"}])
    pinned_ref = snapshot["chunks"][0]["source_ref"]
    reference = pinned_ref if known_reference else pinned_ref + "-invented"
    plan["operations"][0]["evidence_refs"] = [reference]
    # An unrelated top-level list must not authorize a citation outside chunks.
    snapshot["source_refs"] = [] if known_reference else [reference]
    if known_reference:
        assert BusinessDocumentAI._validate(job, plan, snapshot)["change_plan"]["operations"][0]["evidence_refs"] == [pinned_ref]
    else:
        with pytest.raises(ValidationError) as caught:
            BusinessDocumentAI._validate(job, plan, snapshot)
        assert caught.value.code == "EVIDENCE_REF_NOT_IN_SNAPSHOT"


@pytest.mark.parametrize("with_evidence", [False, True])
def test_verifier_outage_saves_the_candidate_with_advice(database, monkeypatch, with_evidence):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, "transport")

    class Evidence:
        audit = staticmethod(BusinessDocumentEvidence.audit)

        def retrieve(self, job):
            row = BusinessDocumentEvidenceSnapshot.get_or_none(BusinessDocumentEvidenceSnapshot.job_id == job.id)
            if row is not None:
                return row.snapshot
            snapshot = BusinessDocumentEvidence._normalize(job, "query", ["dataset"], [{"dataset_id": "dataset", "document_id": "source", "chunk_id": "chunk", "content": "Controls"}])
            return BusinessDocumentEvidence._pin(job, snapshot)

    if with_evidence:
        job = BusinessDocumentJob.get_by_id(requested["job_id"])
        payload = deepcopy(job.payload)
        payload["dataset_ids"] = ["dataset"]
        BusinessDocumentJob.update(payload=payload).where(BusinessDocumentJob.id == job.id).execute()
        monkeypatch.setattr(worker_module, "related_file_search_enabled", lambda: True)
    evidence = Evidence() if with_evidence else None
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), evidence=evidence, retry_base_ms=0).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED"
    assert job.result["output"]["change_plan"] == plan
    assert job.result["output"]["requirement_coverage"]["status"] == "UNAVAILABLE"
    assert job.result["output"]["warnings"][0]["code"] == "REQUIREMENT_VERIFICATION_UNAVAILABLE"
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    assert job.result["execution"]["ai"]["verification"]["duration_ms"] == 10
    if with_evidence:
        assert job.result["execution"]["retrieval"]["attempt"] == 1
    assert BusinessDocumentRevision.select().count() == 2


def test_malformed_verdict_is_repaired_without_rewriting_candidate(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, "malformed")
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED" and job.attempt == 1
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE", "VERIFY_REQUIREMENT_COVERAGE"]
    assert adapter.calls[1]["job_input"] == adapter.calls[2]["job_input"]
    assert adapter.calls[2]["format_repair"]["error"]["code"] == "INVALID_REQUIREMENT_COVERAGE"
    assert job.result["output"]["change_plan"] == plan


def test_semantic_issues_are_saved_with_the_document_for_review(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, "missing")
    worker = BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0)
    assert worker.run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED" and not job.error
    assert job.result["output"]["requirement_coverage"]["status"] == "FAIL"
    current = document_queries.get_document(job.document_id, AUTHOR)
    assert current["lifecycle_state"] == "REVIEW"
    assert current["current_revision"]["document_ast"]["warnings"][0]["code"] == "REQUIREMENT_COVERAGE_FAILED"
    assert not worker.run_once()
    assert len(adapter.calls) == 2
    assert BusinessDocumentRevision.select().count() == 2


def test_invalid_sources_fail_before_semantic_verification(database):
    requested, plan = request_plan()
    plan["operations"][0]["source_event_ids"] = ["invented-event"]
    adapter = PlanAdapter(plan, failure=None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "DEAD"
    assert job.error["code"] == "SOURCE_NOT_IN_JOB_SNAPSHOT"
    assert job.result["recovery"]["candidate"] is None
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES"]
    assert BusinessDocumentRevision.select().count() == 1


@pytest.mark.parametrize(
    "defect,code",
    [
        ("omitted", "ACCEPTED_PROPOSAL_OMITTED"),
        ("conflicting-disposition", "CHANGE_INPUT_DISPOSITION_CONFLICT"),
        ("accepted-no-change", "ACCEPTED_PROPOSAL_ACKNOWLEDGED_NO_CHANGE"),
    ],
)
def test_incomplete_or_conflicting_plan_stops_before_semantic_verification(database, defect, code):
    requested, plan = request_plan()
    sources = list(plan["operations"][0]["source_event_ids"])
    plan["acknowledged_no_change_event_ids"] = [] if defect == "omitted" else sources
    if defect != "conflicting-disposition":
        plan["operations"] = []
    adapter = PlanAdapter(plan, None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "DEAD" and job.error["code"] == code
    assert job.result["recovery"]["candidate"] is None
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES"]
    assert BusinessDocumentRevision.select().count() == 1


def test_foreign_document_source_fails_before_semantic_verification(database):
    foreign = _request_and_complete_draft(_create(title="Другой документ"))
    comment = document_commands.execute(
        TENANT,
        AUTHOR,
        foreign["document_id"],
        _command(foreign, "ADD_COMMENT", {"revision_id": foreign["current_revision"]["revision_id"], "section_id": None, "anchor": None, "text": "Требование другого документа."}),
    )
    _complete_review_assessment(document_queries.get_document(foreign["document_id"], AUTHOR))
    requested, plan = request_plan()
    plan["operations"][0]["source_event_ids"].append(comment["event_id"])
    adapter = PlanAdapter(plan, None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.error["code"] == "SOURCE_NOT_IN_JOB_SNAPSHOT"
    assert job.result["recovery"]["candidate"] is None
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES"]
    assert BusinessDocumentRevision.select().count() == 2


@pytest.mark.parametrize("preview_only", [False, True])
@pytest.mark.parametrize("disposition,acknowledge", [("CONFIRMED_CHANGE", True), ("NO_CHANGE", False)])
def test_comment_disposition_conflict_fails_before_verification_and_is_logged(database, caplog, disposition, acknowledge, preview_only):
    document = _request_and_complete_draft(_create())
    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": document["current_revision"]["revision_id"], "section_id": None, "anchor": None, "text": "Конфиденциальный текст замечания."}),
    )
    document = _complete_review_assessment(document_queries.get_document(document["document_id"], AUTHOR), comment_disposition=disposition)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "PREPARE_CHANGES" if preview_only else "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    comment_id = document["protocol"]["comments"][0]["source_event_id"]
    plan = _plan(document, requested)["change_plan"]
    if acknowledge:
        plan["operations"] = []
        plan["acknowledged_no_change_event_ids"] = [comment_id]
    adapter = PlanAdapter(plan, None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()

    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    code = "COMMENT_NO_CHANGE_NOT_CONFIRMED" if acknowledge else "COMMENT_CHANGE_NOT_CONFIRMED"
    assert job.status == "DEAD" and job.error["code"] == code
    assert job.attempt == 1
    assert job.result["recovery"]["candidate"] is None
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES"]
    assert adapter.calls[0]["job_input"]["active_comment_dispositions"] == {comment_id: disposition}
    assert document_queries.get_document(document["document_id"], AUTHOR)["current_revision"] == document["current_revision"]
    assert BusinessDocumentRevision.select().count() == 1
    assert not BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    records = [record for record in caplog.records if "Business document job failed" in record.getMessage()]
    assert len(records) == 1
    for value in (job.id, job.document_id, job.job_type, code, job.correlation_id):
        assert value in records[0].getMessage()
    assert "Конфиденциальный текст замечания" not in records[0].getMessage()


def test_stale_document_cannot_commit_a_new_diff(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def interrupted_generation(*args, **kwargs):
        result = generate(*args, **kwargs)
        if len(adapter.calls) == 1:
            raise TimeoutError("Generation interrupted")
        return result

    adapter.generate = interrupted_generation
    worker = BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0)
    assert worker.run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    BusinessDocument.update(state_version=job.source_state_version + 1, operation_state="IDLE").where(BusinessDocument.id == job.document_id).execute()
    assert worker.run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.error["code"] == "STALE_AI_RESULT"
    assert len(adapter.calls) == 1
    assert job.status == "DEAD"
    current = BusinessDocument.get_by_id(job.document_id)
    assert current.state_version == job.source_state_version + 1 and current.operation_state == "IDLE"
    assert BusinessDocumentRevision.select().count() == 1


def test_lease_lost_during_verification_prevents_commit(database):
    requested, plan = request_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    payload = deepcopy(job.payload)
    payload["protocol"]["proposals"][0]["text"] = " ".join(f"Requirement {index}." for index in range(60))
    BusinessDocumentJob.update(payload=payload).where(BusinessDocumentJob.id == job.id).execute()
    adapter = PlanAdapter(plan, failure=None)
    generate = adapter.generate

    def expire_after_verification(tenant, system, payload, *, timeout_seconds=None):
        result = generate(tenant, system, payload, timeout_seconds=timeout_seconds)
        if payload["job_input"]["task_type"] == "VERIFY_REQUIREMENT_COVERAGE":
            BusinessDocumentJob.update(lease_expires_at=0).where(BusinessDocumentJob.id == job.id).execute()
        return result

    adapter.generate = expire_after_verification
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0).run_once()
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    assert BusinessDocumentJob.get_by_id(job.id).status == "RUNNING"
    assert BusinessDocumentRevision.select().count() == 1


def test_successful_worker_builds_ast_only_for_review_and_guarded_application(database, monkeypatch):
    from api.apps.business_documents import ai as ai_module, assets

    requested, plan = request_plan()
    calls = []
    apply = assets.apply_change_plan

    def build(base, diff):
        calls.append(diff)
        return apply(base, diff)

    monkeypatch.setattr(ai_module, "apply_change_plan", build)
    monkeypatch.setattr(assets, "apply_change_plan", build)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(PlanAdapter(plan, None))).run_once()
    assert BusinessDocumentJob.get_by_id(requested["job_id"]).status == "COMPLETED"
    assert len(calls) == 2


@pytest.mark.parametrize("before,after", [(True, 1), (False, 0)])
@pytest.mark.parametrize("preview_only", [False, True])
def test_boolean_to_number_change_creates_a_revision(database, before, after, preview_only):
    base = _draft()
    next(section for section in base["sections"] if section["id"] == "5.5")["blocks"] = [{"type": "table", "headers": ["Value"], "rows": [[before]]}]
    document = _request_and_complete_draft(_create(), draft=base)
    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": document["current_revision"]["revision_id"], "section_id": None, "anchor": None, "text": f"Заменить {before} числом {after}."}),
    )
    document = _complete_review_assessment(document_queries.get_document(document["document_id"], AUTHOR))
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "PREPARE_CHANGES" if preview_only else "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    plan = _plan(document, requested)["change_plan"]
    plan["operations"][0]["content"]["blocks"] = [{"type": "table", "headers": ["Value"], "rows": [[after]]}]
    adapter = PlanAdapter(plan, None)

    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    current = document_queries.get_document(document["document_id"], AUTHOR)
    if preview_only:
        assert current["current_revision"] == document["current_revision"]
        preview = document_queries.get_change_preview(document["document_id"], job.id)
        assert preview["sections"][0]["before"] != preview["sections"][0]["after"]
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(current, "CONFIRM_PREPARED_CHANGES", {"job_id": job.id}))
        current = document_queries.get_document(document["document_id"], AUTHOR)
    value = next(section for section in current["current_revision"]["document_ast"]["sections"] if section["id"] == "5.5")["blocks"][0]["rows"][0][0]
    assert type(value) is int
    assert value == after
    assert current["current_revision"]["body_markdown"] != document["current_revision"]["body_markdown"]
    assert BusinessDocumentRevision.select().count() == 2
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]


@pytest.mark.parametrize("preview_only", [False, True])
def test_empty_diff_resolves_no_change_without_hashing_or_building_ast(database, monkeypatch, preview_only):
    from api.apps.business_documents import ai as ai_module, assets
    from business_documents.domain import content

    document = _request_and_complete_draft(_create())
    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": document["current_revision"]["revision_id"], "section_id": None, "anchor": None, "text": "Оставить текущий текст."}),
    )
    document = _complete_review_assessment(document_queries.get_document(document["document_id"], AUTHOR), comment_disposition="NO_CHANGE")
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "PREPARE_CHANGES" if preview_only else "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    source_ids = BusinessDocumentJob.get_by_id(requested["job_id"]).payload["active_change_input_event_ids"]
    plan = {
        "schema_version": "1",
        "base_revision_id": document["current_revision"]["revision_id"],
        "source_state_version": requested["state_version"],
        "operations": [],
        "acknowledged_no_change_event_ids": source_ids,
    }

    def unexpected(*args, **kwargs):
        pytest.fail("An empty diff must not hash sections, build or normalize an AST")

    monkeypatch.setattr(content, "section_hash", unexpected)
    monkeypatch.setattr(content, "normalize_document_ast", unexpected)
    monkeypatch.setattr(ai_module, "apply_change_plan", unexpected)
    monkeypatch.setattr(assets, "apply_change_plan", unexpected)
    adapter = PlanAdapter(plan, None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    current = document_queries.get_document(document["document_id"], AUTHOR)
    if preview_only:
        preview = document_queries.get_change_preview(document["document_id"], job.id)
        assert preview["sections"] == []
        assert preview["acknowledged_no_change_event_ids"] == source_ids
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(current, "CONFIRM_PREPARED_CHANGES", {"job_id": job.id}))
        current = document_queries.get_document(document["document_id"], AUTHOR)
    assert current["current_revision"] == document["current_revision"]
    event = BusinessDocumentEvent.select().order_by(BusinessDocumentEvent.sequence.desc()).first()
    assert event.payload["acknowledged_no_change_event_ids"] == source_ids
    assert BusinessDocumentRevision.select().count() == 1
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES"]


@pytest.mark.parametrize("preview_only", [False, True])
def test_mixed_diff_and_already_satisfied_answer_keep_full_context(database, preview_only):
    batch = _question_batch("REVIEW")
    selected = batch["questions"][0]["options"][0]
    base = _draft()
    next(section for section in base["sections"] if section["id"] == "3.1")["blocks"] = [{"type": "paragraph", "text": selected["label"]}]
    document = _request_and_complete_draft(_create(), review_questions=batch, draft=base)
    comment_text = "Добавить порог предупреждения."
    for kind, payload in [
        ("ANSWER_QUESTION", {"question_id": document["protocol"]["questions"][0]["question_id"], "selected_option_id": selected["option_id"], "custom_answer": None}),
        ("ADD_COMMENT", {"revision_id": document["current_revision"]["revision_id"], "section_id": None, "anchor": None, "text": comment_text}),
    ]:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, kind, payload))
        document = document_queries.get_document(document["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    answer = document["protocol"]["questions"][0]["answer"]["source_event_id"]
    comment = document["protocol"]["comments"][0]["source_event_id"]
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "PREPARE_CHANGES" if preview_only else "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    old = next(section for section in document["current_revision"]["document_ast"]["sections"] if section["id"] == "5.5")
    plan = {
        "schema_version": "1",
        "base_revision_id": document["current_revision"]["revision_id"],
        "source_state_version": requested["state_version"],
        "acknowledged_no_change_event_ids": [answer],
        "operations": [
            {
                "operation_id": "monitoring",
                "type": "REPLACE_SECTION_CONTENT",
                "section_id": "5.5",
                "expected_section_hash": section_hash(old),
                "source_event_ids": [comment],
                "content": {"blocks": [*deepcopy(old["blocks"]), {"type": "paragraph", "text": comment_text}]},
            }
        ],
    }

    class MixedAdapter(PlanAdapter):
        def generate(self, tenant, system, payload, *, timeout_seconds=None):
            if payload["job_input"]["task_type"] == "VERIFY_REQUIREMENT_COVERAGE":
                verification = payload["job_input"]
                source = verification["sources"][answer + ":0"]
                assert source["source_id"] == answer
                assert source["text"] == selected["label"]
                assert verification["context_sections"]["3.1"] == selected["label"]
                assert "3.1" not in verification["after_sections"]
                assert comment_text in verification["after_sections"]["5.5"]
            return super().generate(tenant, system, payload)

    adapter = MixedAdapter(plan, None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    current = document_queries.get_document(document["document_id"], AUTHOR)
    if preview_only:
        assert current["current_revision"]["revision_id"] == document["current_revision"]["revision_id"]
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(current, "CONFIRM_PREPARED_CHANGES", {"job_id": job.id}))
        current = document_queries.get_document(document["document_id"], AUTHOR)
    assert current["current_revision"]["section_texts"]["3.1"] == selected["label"]
    assert comment_text in current["current_revision"]["section_texts"]["5.5"]
    assert BusinessDocumentRevision.select().count() == 2


@pytest.mark.parametrize("source_kind", ["answer", "proposal"])
@pytest.mark.parametrize("preview_only", [False, True])
@pytest.mark.parametrize("before,after", [(True, 1), (False, 0)])
def test_one_confirmed_source_changes_other_sections_and_parent_with_new_child(database, source_kind, preview_only, before, after):
    base = _draft()
    next(section for section in base["sections"] if section["id"] == "3.1")["blocks"] = [{"type": "table", "headers": ["Value"], "rows": [[before]]}]
    text = f"Заменить {before} числом {after} в 3.1, добавить подраздел 3.1.1 и уточнить потребности в 3.3."
    batch = _question_batch("REVIEW")
    batch["questions"][0]["target_section_id"] = "5.5"
    document = _request_and_complete_draft(
        _create(),
        draft=base,
        review_questions=batch if source_kind == "answer" else None,
        proposals=[{"text": text, "target_section_id": "5.5"}] if source_kind == "proposal" else None,
    )
    if source_kind == "answer":
        kind, payload = "ANSWER_QUESTION", {"question_id": document["protocol"]["questions"][0]["question_id"], "selected_option_id": None, "custom_answer": text}
    else:
        kind, payload = "DECIDE_PROPOSAL", {"proposal_id": document["protocol"]["proposals"][0]["proposal_id"], "decision": "ACCEPTED"}
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, kind, payload))
    document = _complete_review_assessment(document_queries.get_document(document["document_id"], AUTHOR))
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "PREPARE_CHANGES" if preview_only else "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    source_ids = job.payload["active_change_input_event_ids"]
    assert len(source_ids) == 1
    plan = {
        "schema_version": "1",
        "base_revision_id": job.base_revision_id,
        "source_state_version": job.source_state_version,
        "acknowledged_no_change_event_ids": [],
        "operations": [
            {
                "operation_id": "parent-and-child",
                "type": "REPLACE_SECTION_CONTENT",
                "section_id": "3.1",
                "expected_section_hash": "sha256:" + "0" * 64,
                "source_event_ids": source_ids,
                "content": {
                    "blocks": [
                        {"type": "table", "headers": ["Value"], "rows": [[after]]},
                        {"id": "3.1.1", "title": "Уточнения", "blocks": [{"type": "paragraph", "text": "Новый подраздел."}]},
                    ]
                },
            },
            {
                "operation_id": "another-section",
                "type": "REPLACE_SECTION_CONTENT",
                "section_id": "3.3",
                "expected_section_hash": "sha256:" + "0" * 64,
                "source_event_ids": source_ids,
                "content": {"blocks": [{"type": "paragraph", "text": "Уточнённые потребности."}]},
            },
        ],
    }
    adapter = PlanAdapter(plan, None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED", job.error
    verification = adapter.calls[1]["job_input"]
    assert {source["source_id"] for source in verification["sources"].values()} == set(source_ids)
    assert set(verification["after_sections"]) == {"3.1", "3.1.1", "3.3"}
    current = document_queries.get_document(document["document_id"], AUTHOR)
    if preview_only:
        assert current["current_revision"] == document["current_revision"]
        preview = document_queries.get_change_preview(document["document_id"], job.id)
        assert {section["section_id"] for section in preview["sections"]} == {"3.1", "3.1.1", "3.3"}
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(current, "CONFIRM_PREPARED_CHANGES", {"job_id": job.id}))
        current = document_queries.get_document(document["document_id"], AUTHOR)
    final = {section["id"]: section for section in current["current_revision"]["document_ast"]["sections"]}
    assert type(final["3.1"]["blocks"][0]["rows"][0][0]) is int
    assert final["3.1"]["blocks"][0]["rows"][0][0] == after
    assert final["3.1.1"]["blocks"][0]["text"] == "Новый подраздел."
    assert final["3.3"]["blocks"][0]["text"] == "Уточнённые потребности."
    assert final["5.5"] == next(section for section in document["current_revision"]["document_ast"]["sections"] if section["id"] == "5.5")
    assert BusinessDocumentRevision.select().count() == 2
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
