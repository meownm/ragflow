import sys
from pathlib import Path


ADMIN_SERVER = Path(__file__).parents[3] / "admin" / "server"
if str(ADMIN_SERVER) not in sys.path:
    sys.path.insert(0, str(ADMIN_SERVER))

from audit_feed import AuditFeed, _safe_business_details, classify_outcome


def _event(event_id, occurred_at, *, outcome="success", source="business_documents", correlation_id="chain-1"):
    return {
        "id": event_id,
        "occurred_at": occurred_at,
        "source": source,
        "action": "DocumentUpdated",
        "outcome": outcome,
        "summary": "Quarterly plan",
        "actor": {"id": "user-1", "type": "USER", "email": "owner@example.com", "nickname": "Owner"},
        "object": {"type": "business_document", "id": "doc-1", "label": "Quarterly plan"},
        "correlation_id": correlation_id,
        "causation_id": None,
        "error": None,
        "details": {},
    }


def test_classify_outcome_understands_persisted_task_statuses():
    assert classify_outcome("3") == "success"
    assert classify_outcome("4") == "failure"
    assert classify_outcome("1") == "pending"
    assert classify_outcome("2") == "cancelled"
    assert classify_outcome("", "BusinessDocumentJobFailed") == "failure"


def test_business_details_only_expose_allowlisted_scalars():
    details = _safe_business_details(
        {
            "revision_number": 4,
            "section_id": "scope",
            "document_ast": {"secret": "must not leak"},
            "body_markdown": "must not leak",
        },
        sequence=8,
    )

    assert details == {"sequence": 8, "revision_number": 4, "section_id": "scope"}


def test_list_events_filters_chain_and_paginates(monkeypatch):
    events = [
        _event("event-1", 1000),
        _event("event-2", 3000, outcome="failure"),
        _event("event-3", 2000, correlation_id="chain-2"),
    ]
    monkeypatch.setattr(AuditFeed, "_load_users", staticmethod(lambda: {}))
    monkeypatch.setattr(AuditFeed, "_application_events", staticmethod(lambda _since, _users, _correlation: []))
    monkeypatch.setattr(AuditFeed, "_business_events", staticmethod(lambda _since, _users, _correlation: events))
    monkeypatch.setattr(AuditFeed, "_pipeline_events", staticmethod(lambda _since, _users, _correlation: []))
    monkeypatch.setattr(AuditFeed, "_connector_events", staticmethod(lambda _since, _users, _correlation: []))

    result = AuditFeed.list_events(correlation_id="chain-1", page=1, page_size=1)

    assert result["total"] == 2
    assert result["stats"] == {"failures": 1, "sources": 1}
    assert [item["id"] for item in result["items"]] == ["event-2"]
    assert result["retention_days"] == 30
