"""Semantic review pins historical bodies without expanding change authority."""

from copy import deepcopy
import json

import pytest
from playhouse.test_utils import count_queries

from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _complete,
    _complete_review_assessment,
    _create,
    _question_batch,
    _request_and_complete_draft,
    database as service_database,
)
from test.unit_test.api.apps.business_documents.test_business_document_review_loading import populate_cycle
from test.unit_test.api.apps.business_documents.test_business_document_ai_export_boundaries import CoverageAdapter, _coverage_job, _coverage_plan
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.runtime import document_commands, document_queries, document_writer
from api.db.db_models import BusinessDocumentAnswer, BusinessDocumentJob
from business_documents.application.errors import ValidationError
from business_documents.domain.errors import RuleViolation
from business_documents.domain.requirement_coverage import validate_requirement_coverage


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def test_history_bodies_include_all_cycles_and_only_the_requested_document(database):
    document_id = _create()["document_id"]
    foreign_id = _create(title="Другой документ")["document_id"]
    for cycle in range(3):
        populate_cycle(document_id, cycle, 12, sequence=10 + cycle * 100)
    populate_cycle(foreign_id, 9, 1)

    with count_queries(only_select=True) as queries:
        history = document_writer.agreement_history(document_id)

    assert queries.count == 4
    assert all(len(rows) == 36 for rows in history.values())
    assert {row["review_cycle"] for row in history["questions"]} == {0, 1, 2}
    assert all(row["custom_answer"] == "Answer " * 50 for row in history["answers"])
    assert all(row["text"] == "Proposal " * 50 for row in history["proposals"])
    assert all(row["text"] == "Comment " * 50 for row in history["comments"])
    assert not any(row["id"].split("-")[1] == "9" for rows in history.values() for row in rows)
    json.dumps(history, ensure_ascii=False)  # No ORM/date objects enter the job JSON.


def test_queued_semantic_review_uses_pinned_intake_answer_and_complete_history(database):
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(requested["job_id"], _question_batch())
    question_id = document["protocol"]["questions"][0]["question_id"]
    document_commands.execute(
        TENANT, AUTHOR, document["document_id"], _command(document, "ANSWER_QUESTION", {"question_id": question_id, "selected_option_id": None, "custom_answer": "Только сотрудники банка."})
    )
    document = _request_and_complete_draft(document_queries.get_document(document["document_id"], AUTHOR))
    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": document["current_revision"]["revision_id"], "section_id": None, "text": "Добавить срок хранения 90 дней.", "anchor": None}),
    )
    document = _complete_review_assessment(document_queries.get_document(document["document_id"], AUTHOR))
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "PREPARE_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}))
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    snapshot = deepcopy(job.payload)
    old_answer = snapshot["agreement_history"]["answers"][0]
    old_event = next(event["event_id"] for event in snapshot["source_events"] if event["payload"].get("answer_id") == old_answer["id"])
    assert old_event not in snapshot["active_change_input_event_ids"]
    assert snapshot["protocol"]["questions"] == []
    assert snapshot["agreement_history"]["questions"][0]["id"] == question_id
    assert snapshot["agreement_history"]["questions"][0]["options"] == _question_batch()["questions"][0]["options"]
    BusinessDocumentAnswer.update(custom_answer="Позднее внешнее изменение").where(BusinessDocumentAnswer.id == old_answer["id"]).execute()

    plan = _coverage_plan()
    plan["operations"][0]["source_event_ids"] = snapshot["active_change_input_event_ids"]
    failure = {"status": "FAIL", "issues": [{"source_id": f"history:{old_event}", "section_id": "1", "reason": "Утрачено ограничение доступа сотрудниками банка."}]}

    class Adapter:
        def __init__(self):
            self.calls = []

        def generate(self, tenant, system, payload, *, timeout_seconds=None):
            self.calls.append(deepcopy(payload["job_input"]))
            return deepcopy(plan if payload["job_input"]["task_type"] == "PLAN_CHANGES" else failure)

    adapter = Adapter()
    with count_queries() as queries:
        result = BusinessDocumentAI(adapter).process(job)

    assert queries.count == 0
    assert [call["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    verification = adapter.calls[1]
    assert verification["agreement_history"] == snapshot["agreement_history"]
    assert verification["source_events"] == snapshot["source_events"]
    assert verification["protocol"] == snapshot["protocol"]
    assert verification["active_review_cycle"] == 1
    assert verification["agreement_history"]["answers"][0]["custom_answer"] == "Только сотрудники банка."
    assert verification["agreement_decisions"]["answers"][0]["text"] == "Только сотрудники банка."
    assert verification["agreement_decisions"]["answers"][0]["event_id"] == old_event
    assert {source["source_id"] for source in verification["sources"].values()} == set(snapshot["active_change_input_event_ids"])
    assert result["requirement_coverage"] == failure
    assert result["warnings"][0]["code"] == "REQUIREMENT_COVERAGE_FAILED"
    assert job.payload == BusinessDocumentJob.get_by_id(job.id).payload == snapshot


def test_semantic_history_preserves_cancellation_reacceptance_and_applied_events():
    job = _coverage_job()
    job.payload["source_events"] = [
        {"event_id": "old-acceptance", "sequence": 1, "event_type": "ProposalDecided", "payload": {"proposal_id": "p", "decision": "ACCEPTED"}},
        {"event_id": "cancelled", "sequence": 2, "event_type": "ProposalAcceptanceCancelled", "payload": {"proposal_id": "p", "decision_event_id": "old-acceptance"}},
        {"event_id": "reaccepted", "sequence": 3, "event_type": "ProposalDecided", "payload": {"proposal_id": "p", "decision": "ACCEPTED"}},
        {"event_id": "applied", "sequence": 4, "event_type": "ChangesApplied", "payload": {"source_event_ids": ["reaccepted"]}},
        *job.payload["source_events"],
    ]
    job.payload["agreement_history"] = {"proposals": [{"id": "p", "review_cycle": 0, "text": "Не разрешать гостевой доступ."}]}
    snapshot = deepcopy(job.payload)
    adapter = CoverageAdapter()
    BusinessDocumentAI(adapter)._verify_requirements(job, _coverage_plan())
    verification = adapter.calls[0]["job_input"]
    assert verification["source_events"] == snapshot["source_events"]
    assert verification["agreement_history"] == snapshot["agreement_history"]
    assert verification["agreement_decisions"]["proposals"] == [
        {"proposal_id": "p", "event_id": "reaccepted", "sequence": 3, "review_cycle": 0, "status": "ACCEPTED", "text": "Не разрешать гостевой доступ."}
    ]
    assert set(verification["sources"]) == {"comment-event:0", "comment-event:1"}
    assert {source["source_id"] for source in verification["sources"].values()} == {"comment-event"}
    verification["source_events"][0]["payload"]["decision"] = "REJECTED"
    verification["agreement_history"]["proposals"][0]["text"] = "Changed by adapter"
    assert job.payload == snapshot


@pytest.mark.parametrize("source_id,valid", [("history:old-answer", True), ("old-answer", False), ("history:invented", False), ("history:question-id", False)])
def test_historical_diagnostics_require_a_pinned_event(source_id, valid):
    report = {"status": "FAIL", "issues": [{"source_id": source_id, "section_id": "1", "reason": "Нарушено прежнее решение."}]}
    if valid:
        assert validate_requirement_coverage(report, {}, {"1": "Цель"}, history_event_ids={"old-answer"}) == report
    else:
        with pytest.raises(RuleViolation, match="Semantic verification"):
            validate_requirement_coverage(report, {}, {"1": "Цель"}, history_event_ids={"old-answer"})


def test_history_does_not_authorize_a_new_change():
    job = _coverage_job()
    job.payload["source_events"].append({"event_id": "old-answer", "event_type": "QuestionAnswered", "payload": {}})
    plan = _coverage_plan()
    plan["operations"][0]["source_event_ids"] = ["old-answer"]
    with pytest.raises(ValidationError) as caught:
        BusinessDocumentAI._validate(job, plan)
    assert caught.value.code == "SOURCE_NOT_IN_JOB_SNAPSHOT"
