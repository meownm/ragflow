"""Real worker/application boundaries for stable block plans and previews."""

from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_accept_proposal import _document, _request_changes, database as proposal_database
from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR, TENANT, _command
from test.unit_test.api.apps.business_documents.test_structured_changes import PlanAdapter, request_plan
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.runtime import document_commands, document_queries
from api.apps.business_documents.worker import BusinessDocumentWorker
from api.db.db_models import BusinessDocumentJob, BusinessDocumentRevision


@pytest.fixture()
def database():
    yield from proposal_database.__wrapped__()


def block_plan(job):
    section = next(section for section in job.payload["current_revision"]["document_ast"]["sections"] if section["id"] == "5.5")
    target = section["blocks"][0]["id"]
    return {
        "schema_version": "1",
        "acknowledged_no_change_event_ids": [],
        "operations": [
            {
                "operation_id": "replace",
                "type": "REPLACE_BLOCK",
                "section_id": "5.5",
                "block_id": target,
                "source_event_ids": job.payload["active_change_input_event_ids"],
                "block": {"type": "paragraph", "text": "Контроль ошибок включён"},
            },
            {
                "operation_id": "insert",
                "type": "INSERT_BLOCK",
                "section_id": "5.5",
                "after_block_id": target,
                "source_event_ids": job.payload["active_change_input_event_ids"],
                "block": {"type": "paragraph", "text": "Порог предупреждения установлен"},
            },
        ],
    }


def test_worker_uses_block_schema_full_context_and_persists_stable_ids(database):
    requested, _ = request_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    before = deepcopy(job.payload["current_revision"]["document_ast"])
    adapter = PlanAdapter(block_plan(job), None)
    assert '"enum": ["ADD_SECTION", "DELETE_BLOCK", "INSERT_BLOCK", "REPLACE_BLOCK"]' in BusinessDocumentAI._prompt(job).system
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED", job.error
    current = document_queries.get_document(job.document_id, AUTHOR)["current_revision"]["document_ast"]
    original = next(section for section in before["sections"] if section["id"] == "5.5")
    changed = next(section for section in current["sections"] if section["id"] == "5.5")
    assert changed["blocks"][0]["id"] == original["blocks"][0]["id"]
    assert changed["blocks"][2:] == original["blocks"][1:]
    assert [section for section in current["sections"] if section["id"] != "5.5"] == [section for section in before["sections"] if section["id"] != "5.5"]
    assert adapter.calls[0]["job_input"]["current_revision"]["document_ast"] == before
    assert len(adapter.calls[0]["job_input"]["current_revision"]["document_ast"]["sections"]) == len(before["sections"])
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    assert job.result["output"]["change_plan"]["operations"][1]["block"]["id"] == changed["blocks"][1]["id"]


def test_preview_aggregates_block_pairs_then_confirms_atomically_and_replays(database):
    document = _document(open_questions=False)
    decision = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "ACCEPT_PROPOSAL", {"proposal_id": document["protocol"]["proposals"][0]["proposal_id"]}))
    document, requested = _request_changes(decision["document"], preview=True)
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    before_count = BusinessDocumentRevision.select().count()
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(PlanAdapter(block_plan(job), None))).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED", job.error
    preview = document_queries.get_change_preview(document["document_id"], job.id)
    assert len(preview["sections"]) == 1
    change = preview["sections"][0]
    assert [block["kind"] for block in change["blocks"]] == ["replace", "insert"]
    assert change["blocks"][0]["before"] and change["blocks"][0]["after"] == "Контроль ошибок включён"
    assert change["blocks"][1]["before"] == ""
    assert len(change["source_event_ids"]) == len(set(change["source_event_ids"]))
    assert BusinessDocumentRevision.select().count() == before_count
    prepared = document_queries.get_document(document["document_id"], AUTHOR)
    command = _command(prepared, "CONFIRM_PREPARED_CHANGES", {"job_id": job.id})
    applied = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    assert BusinessDocumentRevision.select().count() == before_count + 1
    assert document_commands.execute(TENANT, AUTHOR, document["document_id"], command)["idempotent_replay"]
    assert applied["document"]["current_revision"]["revision_id"] != document["current_revision"]["revision_id"]


@pytest.mark.parametrize("error", ["source", "target"])
def test_invalid_plan_does_not_write_a_revision(database, error):
    requested, _ = request_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    raw = block_plan(job)
    if error == "source":
        raw["operations"][0]["source_event_ids"] = ["foreign-event"]
    elif error == "target":
        raw["operations"][0]["block_id"] = "foreign-block"
    count = BusinessDocumentRevision.select().count()
    adapter = PlanAdapter(raw, None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status != "COMPLETED"
    assert BusinessDocumentRevision.select().count() == count


def test_block_plan_retains_existing_semantic_warning_behavior(database):
    requested, _ = request_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(PlanAdapter(block_plan(job), "missing"))).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED"
    assert job.result["output"]["requirement_coverage"]["status"] == "FAIL"
    current = document_queries.get_document(job.document_id, AUTHOR)
    assert any(warning["code"] == "REQUIREMENT_COVERAGE_FAILED" for warning in current["current_revision"]["document_ast"]["warnings"])


def test_streamed_block_operations_accumulate_without_losing_earlier_edits(database):
    requested, _ = request_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    raw = block_plan(job)
    sections = {section["id"]: section for section in job.payload["current_revision"]["document_ast"]["sections"]}
    preview = BusinessDocumentAI._preliminary_section(job, raw["operations"][1], sections, frozenset(job.payload["active_change_input_event_ids"]), raw["operations"])
    assert [block["kind"] for block in preview["blocks"]] == ["replace", "insert"]
    assert "Контроль ошибок включён" in preview["after"]
    assert "Порог предупреждения установлен" in preview["after"]


def test_legacy_revision_gets_ids_without_rewriting_history_then_uses_block_edits(database):
    document = _document(open_questions=False)
    saved = BusinessDocumentRevision.get_by_id(document["current_revision"]["revision_id"])
    legacy = deepcopy(saved.document_ast)
    for section in legacy["sections"]:
        for block in section["blocks"]:
            block.pop("id", None)
    BusinessDocumentRevision.update(document_ast=legacy).where(BusinessDocumentRevision.id == saved.id).execute()
    projected = document_queries.get_document(document["document_id"], AUTHOR)
    assert projected == document_queries.get_document(document["document_id"], AUTHOR)
    assert BusinessDocumentRevision.get_by_id(saved.id).document_ast == legacy
    decision = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(projected, "ACCEPT_PROPOSAL", {"proposal_id": projected["protocol"]["proposals"][0]["proposal_id"]}))
    _, requested = _request_changes(decision["document"], preview=False)
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(PlanAdapter(block_plan(job), None))).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED", job.error
    current = document_queries.get_document(document["document_id"], AUTHOR)
    assert current["current_revision"]["revision_id"] != saved.id
    assert BusinessDocumentRevision.get_by_id(saved.id).document_ast == legacy
    assert all(block["id"] for section in current["current_revision"]["document_ast"]["sections"] for block in section["blocks"])


def test_legacy_identity_projection_alone_does_not_create_an_empty_revision(database):
    requested, _ = request_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    saved = BusinessDocumentRevision.get_by_id(job.base_revision_id)
    legacy = deepcopy(saved.document_ast)
    for section in legacy["sections"]:
        for block in section["blocks"]:
            block.pop("id", None)
    BusinessDocumentRevision.update(document_ast=legacy).where(BusinessDocumentRevision.id == saved.id).execute()
    raw = block_plan(job)
    raw["operations"] = raw["operations"][:1]
    raw["operations"][0]["block"] = deepcopy(next(section for section in legacy["sections"] if section["id"] == "5.5")["blocks"][0])
    count = BusinessDocumentRevision.select().count()
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(PlanAdapter(raw, None))).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED", job.error
    assert BusinessDocumentRevision.select().count() == count
    assert BusinessDocumentRevision.get_by_id(saved.id).document_ast == legacy
