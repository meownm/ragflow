"""Batch loading of document protocol records for pure review calculations."""

from api.db.db_models import (
    BusinessDocumentAnswer,
    BusinessDocumentComment,
    BusinessDocumentEvent,
    BusinessDocumentProposal,
    BusinessDocumentProposalDecision,
    BusinessDocumentQuestion,
)
from business_documents.domain.review import ReviewState, build_review_state


def load_answered_section_ids(document_id: str) -> frozenset[str]:
    """Load section identities across cycles without historical answer bodies."""
    return frozenset(
        section_id
        for (section_id,) in BusinessDocumentAnswer.select(BusinessDocumentQuestion.target_section_id)
        .join(BusinessDocumentQuestion, on=BusinessDocumentAnswer.question_id == BusinessDocumentQuestion.id)
        .where((BusinessDocumentAnswer.document_id == document_id) & (BusinessDocumentQuestion.document_id == document_id))
        .distinct()
        .tuples()
    )


def load_review_state(document_id: str, review_cycle: int) -> ReviewState:
    def current(model):
        return model.select().where((model.document_id == document_id) & (model.review_cycle == review_cycle))

    def records(query):
        return query.order_by(query.model.create_time.asc()).dicts()

    questions = current(BusinessDocumentQuestion)
    proposals = current(BusinessDocumentProposal)
    # Keep every event: snapshots and stale-source errors need historical IDs.
    # Entity bodies and answer/decision values are used only by the current cycle.
    answers = BusinessDocumentAnswer.select().where((BusinessDocumentAnswer.document_id == document_id) & BusinessDocumentAnswer.question_id.in_(questions.select(BusinessDocumentQuestion.id)))
    decisions = BusinessDocumentProposalDecision.select().where(
        (BusinessDocumentProposalDecision.document_id == document_id) & BusinessDocumentProposalDecision.proposal_id.in_(proposals.select(BusinessDocumentProposal.id))
    )

    return build_review_state(
        document_id,
        review_cycle,
        questions=records(questions),
        answers=records(answers),
        proposals=records(proposals),
        decisions=records(decisions),
        comments=records(current(BusinessDocumentComment)),
        events=BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document_id).order_by(BusinessDocumentEvent.sequence.asc()).dicts(),
        answered_sections=load_answered_section_ids(document_id),
    )


def load_review_history(document_id: str, active_cycle: int) -> tuple[ReviewState, ...]:
    """Read previous cycles in a fixed number of document-scoped queries."""
    from collections import defaultdict

    if active_cycle <= 0:
        return ()
    groups = {}
    for name, model in (("questions", BusinessDocumentQuestion), ("proposals", BusinessDocumentProposal), ("comments", BusinessDocumentComment)):
        grouped = defaultdict(list)
        for row in model.select().where((model.document_id == document_id) & (model.review_cycle < active_cycle)).order_by(model.create_time.asc(), model.id.asc()).dicts():
            grouped[row["review_cycle"]].append(row)
        groups[name] = grouped
    cycles = sorted(set(groups["questions"]) | set(groups["proposals"]) | set(groups["comments"]), reverse=True)
    if not cycles:
        return ()
    historical_questions = BusinessDocumentQuestion.select(BusinessDocumentQuestion.id).where(
        (BusinessDocumentQuestion.document_id == document_id) & (BusinessDocumentQuestion.review_cycle < active_cycle)
    )
    historical_proposals = BusinessDocumentProposal.select(BusinessDocumentProposal.id).where(
        (BusinessDocumentProposal.document_id == document_id) & (BusinessDocumentProposal.review_cycle < active_cycle)
    )
    answers = tuple(BusinessDocumentAnswer.select().where((BusinessDocumentAnswer.document_id == document_id) & BusinessDocumentAnswer.question_id.in_(historical_questions)).dicts())
    decisions = tuple(
        BusinessDocumentProposalDecision.select().where((BusinessDocumentProposalDecision.document_id == document_id) & BusinessDocumentProposalDecision.proposal_id.in_(historical_proposals)).dicts()
    )
    events = tuple(BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document_id).order_by(BusinessDocumentEvent.sequence.asc()).dicts())
    return tuple(
        build_review_state(
            document_id, cycle, questions=groups["questions"][cycle], proposals=groups["proposals"][cycle], comments=groups["comments"][cycle], answers=answers, decisions=decisions, events=events
        )
        for cycle in cycles
    )
