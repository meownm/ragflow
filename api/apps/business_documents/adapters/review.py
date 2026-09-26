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
    )
