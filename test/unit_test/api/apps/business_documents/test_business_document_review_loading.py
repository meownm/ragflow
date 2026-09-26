"""Current-cycle loading preserves protocol, audit history and stale-source errors."""

import pytest
from playhouse.test_utils import count_queries

from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR, _create, database as service_database
from api.apps.business_documents.adapters.review import load_review_state
from api.db.db_models import (
    BusinessDocumentAnswer,
    BusinessDocumentComment,
    BusinessDocumentEvent,
    BusinessDocumentProposal,
    BusinessDocumentProposalDecision,
    BusinessDocumentQuestion,
)
from business_documents.application.queries import project_protocol
from business_documents.domain.change_plan import validate_change_inputs
from business_documents.domain.errors import RuleViolation
from business_documents.domain.review import build_review_state
from business_documents.domain.workflow import available_commands


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def populate_cycle(document_id, cycle, size, *, sequence=10):
    rows = {model: [] for model in (BusinessDocumentQuestion, BusinessDocumentAnswer, BusinessDocumentProposal, BusinessDocumentProposalDecision, BusinessDocumentComment, BusinessDocumentEvent)}
    sources = []
    for i in range(size):
        key = f"{cycle}-{i}"
        common = {"document_id": document_id, "create_time": sequence + i}
        rows[BusinessDocumentQuestion].append(
            {**common, "id": f"q-{key}", "review_cycle": cycle, "stage": "REVIEW", "semantic_tag": key, "target_section_id": "3.1", "text": f"Question {key}", "options": [], "source_event_ids": []}
        )
        rows[BusinessDocumentAnswer].append({**common, "id": f"a-{key}", "question_id": f"q-{key}", "actor_id": AUTHOR, "custom_answer": "Answer " * 50})
        rows[BusinessDocumentProposal].append(
            {
                **common,
                "id": f"p-{key}",
                "review_cycle": cycle,
                "target_section_id": "3.1",
                "text": "Proposal " * 50,
                "rationale": "Rationale",
                "source_event_ids": [],
                "fingerprint": key,
                "source_scope_hash": key,
            }
        )
        rows[BusinessDocumentProposalDecision].append({**common, "id": f"d-{key}", "proposal_id": f"p-{key}", "actor_id": AUTHOR, "decision": "ACCEPTED"})
        rows[BusinessDocumentComment].append({**common, "id": f"c-{key}", "review_cycle": cycle, "revision_id": "revision", "actor_id": AUTHOR, "section_id": "3.1", "text": "Comment " * 50})
        for offset, (kind, payload) in enumerate(
            (
                ("QuestionAnswered", {"question_id": f"q-{key}", "answer_id": f"a-{key}"}),
                ("ProposalDecided", {"proposal_id": f"p-{key}", "decision": "ACCEPTED"}),
                ("AuthorCommentAdded", {"comment_id": f"c-{key}"}),
            )
        ):
            event_id = f"e-{key}-{offset}"
            sources.append(event_id)
            rows[BusinessDocumentEvent].append(
                {**common, "id": event_id, "sequence": sequence + i * 3 + offset, "event_type": kind, "actor_type": "USER", "actor_id": AUTHOR, "payload": payload, "correlation_id": "review-loading"}
            )
    for model, values in rows.items():
        for offset in range(0, len(values), 25):
            model.insert_many(values[offset : offset + 25]).execute()
    return sources


def full_history(document_id, cycle):
    def records(model):
        return model.select().where(model.document_id == document_id).order_by(model.create_time.asc()).dicts()

    return build_review_state(
        document_id,
        cycle,
        questions=records(BusinessDocumentQuestion),
        answers=records(BusinessDocumentAnswer),
        proposals=records(BusinessDocumentProposal),
        decisions=records(BusinessDocumentProposalDecision),
        comments=records(BusinessDocumentComment),
        events=BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document_id).order_by(BusinessDocumentEvent.sequence.asc()).dicts(),
    )


def test_current_cycle_load_preserves_published_results_and_historical_source_errors(database):
    document_id = _create()["document_id"]
    old = populate_cycle(document_id, 1, 30)
    current = populate_cycle(document_id, 2, 2, sequence=1000)
    BusinessDocumentEvent.create(
        id="assessment",
        document_id=document_id,
        sequence=1100,
        event_type="ReviewAssessed",
        actor_type="AI",
        actor_id="worker",
        correlation_id="test",
        payload={
            "review_cycle": 2,
            "outcome": "COMPLETE",
            "comment_dispositions": [{"comment_event_id": event_id, "disposition": "CONFIRMED_CHANGE"} for event_id in current if event_id.endswith("-2")],
        },
    )
    baseline = full_history(document_id, 2)
    with count_queries(only_select=True) as queries:
        actual = load_review_state(document_id, 2)
    assert queries.count == 6
    assert len(actual.answers) == len(actual.decisions) == len(actual.questions) == len(actual.proposals) == len(actual.comments) == 2
    assert len(baseline.answers) == len(baseline.decisions) == 32
    assert actual.events == baseline.events
    assert actual.active_inputs == baseline.active_inputs == set(current)
    assert project_protocol(actual, "revision") == project_protocol(baseline, "revision")
    document = {"lifecycle_state": "REVIEW", "operation_state": "IDLE", "current_revision_id": "revision"}
    assert available_commands(document, actual) == available_commands(document, baseline)

    def validate(review, ids):
        try:
            return validate_change_inputs(
                review,
                {"operations": [{"section_id": "3.1", "source_event_ids": ids}], "acknowledged_no_change_event_ids": []},
                snapshot_source_ids=frozenset([*old, *current, "foreign"]),
                evidence_source_refs=frozenset(),
            )
        except RuleViolation as error:
            return error.code, error.message, error.details

    for ids in (current, [old[0]], [old[1]], [old[2]], ["foreign"]):
        assert validate(actual, ids) == validate(baseline, ids)
    assert validate(actual, [old[0]])[0] == "CHANGE_SOURCE_REVIEW_CYCLE_CONFLICT"
    assert validate(actual, [old[1]])[0] == "CHANGE_SOURCE_REVIEW_CYCLE_CONFLICT"
    assert validate(actual, [old[2]])[0] == "COMMENT_CHANGE_NOT_CONFIRMED"
