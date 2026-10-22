"""One-time, resumable enqueue of accepted changes waiting before this upgrade."""

import logging

from api.apps.business_documents.adapters.review import load_review_state
from api.apps.business_documents.runtime import document_commands
from api.db.db_models import BusinessDocument, BusinessDocumentAcceptedChangesMigration, BusinessDocumentProposal, BusinessDocumentProposalDecision


MIGRATION_ID = "bt-accepted-proposals-20261022"


def _capture_targets():
    document, proposal, decision = BusinessDocument, BusinessDocumentProposal, BusinessDocumentProposalDecision
    candidates = list(
        decision.select(
            decision.id.alias("decision_id"),
            proposal.id.alias("proposal_id"),
            document.id.alias("document_id"),
            document.tenant_id,
            document.owner_id,
            document.active_review_cycle,
        )
        .join(proposal, on=(decision.proposal_id == proposal.id) & (decision.document_id == proposal.document_id))
        .join(document, on=(proposal.document_id == document.id))
        .where(
            (decision.decision == "ACCEPTED")
            & (document.document_type == "business_requirements")
            & (document.lifecycle_state == "REVIEW")
            & document.current_revision_id.is_null(False)
            & (proposal.review_cycle == document.active_review_cycle)
        )
        .order_by(document.id, decision.create_time, decision.id)
        .dicts()
    )
    targets = []
    review = None
    for candidate in candidates:
        if review is None or review.document_id != candidate["document_id"]:
            review = load_review_state(candidate["document_id"], candidate["active_review_cycle"])
        proposal_id = candidate["proposal_id"]
        event_id = review.protocol_events["decisions"].get(proposal_id)
        if review.decisions.get(proposal_id, {}).get("id") == candidate["decision_id"] and event_id in review.accepted_inputs & review.active_inputs:
            targets.append({key: value for key, value in candidate.items() if key != "decision_id"} | {"decision_event_id": event_id})
    return targets


def _locked_state():
    query = BusinessDocumentAcceptedChangesMigration.select().where(BusinessDocumentAcceptedChangesMigration.name == MIGRATION_ID)
    if getattr(BusinessDocumentAcceptedChangesMigration._meta.database, "for_update", False):
        query = query.for_update()
    return query.get()


def resume_accepted_changes(*, batch_size: int = 20) -> int:
    """Freeze once, then retry busy documents without enrolling later decisions.

    The marker lock precedes document locks. Enqueue and removing a target share
    one transaction, so a crash or another worker cannot schedule it twice.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    model = BusinessDocumentAcceptedChangesMigration
    database = model._meta.database
    # Commit the cohort before dispatch: a failed dispatch must not enroll new
    # approvals on the next attempt. get_or_create handles competing startups.
    with database.atomic():
        _, created = model.get_or_create(name=MIGRATION_ID)
        state = _locked_state()
        if created:
            pending = _capture_targets()
            model.update(pending=pending, finished=not pending).where(model.name == MIGRATION_ID).execute()
            logging.info("Business document migration %s captured %d confirmed proposals", MIGRATION_ID, len(pending))
        if state.finished:
            return 0
    enqueued = 0
    with database.atomic():
        state = _locked_state()
        if state.finished:
            return 0
        remaining = list(state.pending[batch_size:])
        for target in state.pending[:batch_size]:
            result = document_commands.resume_confirmed_proposal(target, MIGRATION_ID)
            if result["status"] == "DEFERRED":
                remaining.append(target)
            elif result["status"] == "ENQUEUED":
                enqueued += 1
            elif result["status"] != "SKIPPED":
                raise RuntimeError("Unknown accepted-proposal migration outcome")
        if remaining != state.pending:
            model.update(pending=remaining, finished=not remaining).where(model.name == MIGRATION_ID).execute()
    if enqueued:
        logging.info("Business document migration %s enqueued %d proposals; %d remain", MIGRATION_ID, enqueued, len(remaining))
    return enqueued
