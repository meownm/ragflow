"""Read-only missing-night watchdog, independent from the scheduling worker."""

from datetime import datetime

from api.db.db_models import BusinessDocumentQualityCampaign
from common.document_quality_schedule import MOSCOW, NIGHTLY_MAX_DURATION, expected_night_slot


def schedule_health(document_type="business_requirements", now=None):
    if document_type not in ("business_requirements", "technical_specification"):
        raise ValueError("Unknown document quality product")
    local = (now or datetime.now(MOSCOW)).astimezone(MOSCOW)
    slot = expected_night_slot(local)
    prefix = "tz-nightly" if document_type == "technical_specification" else "nightly"
    campaign = BusinessDocumentQualityCampaign.get_or_none(
        (BusinessDocumentQualityCampaign.document_type == document_type) & (BusinessDocumentQualityCampaign.schedule_key == f"{prefix}:{slot.date().isoformat()}")
    )
    code = (
        "NIGHTLY_MISSING"
        if campaign is None
        else "NIGHTLY_INCOMPLETE"
        if campaign.status in ("PARTIAL", "INCOMPLETE")
        else "NIGHTLY_OVERDUE"
        if campaign.status in ("PENDING", "RUNNING") and local > slot + NIGHTLY_MAX_DURATION
        else None
    )
    return {
        "document_type": document_type,
        "slot": slot.isoformat(),
        "observed_at": local.isoformat(),
        "status": "ALERT" if code else "OK",
        "reason_code": code,
        "campaign_id": campaign.id if campaign else None,
        "campaign_status": campaign.status if campaign else None,
    }
