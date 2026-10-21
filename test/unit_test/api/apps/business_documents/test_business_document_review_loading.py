"""Current-cycle loading preserves protocol, audit history and stale-source errors."""

import pytest
from playhouse.test_utils import count_queries

from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR, _create, database as service_database
from api.apps.business_documents.adapters.review import load_review_history, load_review_state
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
    assert queries.count == 7  # Six current-cycle record types plus cross-cycle section identities.
    assert len(actual.answers) == len(actual.decisions) == len(actual.questions) == len(actual.proposals) == len(actual.comments) == 2
    assert len(baseline.answers) == len(baseline.decisions) == 32
    assert actual.events == baseline.events
    assert actual.answered_section_ids == baseline.answered_section_ids
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


def test_history_reads_previous_cycles_without_changing_current_state_or_query_count(database):
    document_id = _create()["document_id"]
    populate_cycle(document_id, 0, 2)
    previous_sources = populate_cycle(document_id, 1, 3, sequence=100)
    populate_cycle(document_id, 2, 1, sequence=200)
    foreign_id = _create(title="Другой документ")["document_id"]
    BusinessDocumentQuestion.create(
        id="foreign-question", document_id=foreign_id, review_cycle=1, stage="REVIEW", semantic_tag="foreign", target_section_id="1", text="Private question", options=[], source_event_ids=[]
    )
    BusinessDocumentEvent.create(
        id="previous-applied",
        document_id=document_id,
        sequence=250,
        event_type="ChangesApplied",
        actor_type="USER",
        actor_id=AUTHOR,
        correlation_id="history",
        payload={"source_event_ids": previous_sources},
    )
    with count_queries(only_select=True) as queries:
        history = load_review_history(document_id, 2)
    assert queries.count == 6
    assert [review.review_cycle for review in history] == [1, 0]
    assert [len(review.questions) for review in history] == [3, 2]
    assert all(row["document_id"] == document_id for review in history for row in (*review.questions, *review.proposals, *review.comments))
    assert all(project_protocol(review, "revision")["questions"][0]["answer"]["custom_answer"] for review in history)
    for review in history:
        assert project_protocol(review, "revision") == project_protocol(full_history(document_id, review.review_cycle), "revision")
    assert all(proposal["applied"] for proposal in project_protocol(history[0], "revision")["proposals"])
    assert len(load_review_state(document_id, 2).questions) == 1


def test_later_cycle_assessment_preserves_complete_historical_protocol(database):
    document_id = _create()["document_id"]
    populate_cycle(document_id, 1, 1)

    def assess(cycle, sequence, disposition):
        BusinessDocumentEvent.create(
            id=f"assessment-{sequence}",
            document_id=document_id,
            sequence=sequence,
            event_type="ReviewAssessed",
            actor_type="AI",
            actor_id="worker",
            correlation_id="history",
            payload={"review_cycle": cycle, "outcome": "COMPLETE", "comment_dispositions": [{"comment_event_id": f"e-{cycle}-0-2", "disposition": disposition}]},
        )

    assess(1, 20, "CONFIRMED_CHANGE")
    before = project_protocol(load_review_state(document_id, 1), "revision")
    populate_cycle(document_id, 2, 1, sequence=30)
    assess(2, 40, "NO_CHANGE")

    history = load_review_history(document_id, 2)
    assert len(history) == 1
    assert project_protocol(history[0], "revision") == before
    assert before["comments"][0]["disposition"]["disposition"] == "CONFIRMED_CHANGE"
    current = project_protocol(load_review_state(document_id, 2), "revision")
    assert current["comments"][0]["disposition"]["disposition"] == "NO_CHANGE"
