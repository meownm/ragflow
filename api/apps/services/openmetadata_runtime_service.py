"""Process-local provider for the shared OpenMetadata catalog service."""

from __future__ import annotations

import threading

from api.apps.services.openmetadata_copilot_service import OpenMetadataCopilotService


_SERVICE: OpenMetadataCopilotService | None = None
_SERVICE_LOCK = threading.Lock()


def get_openmetadata_service() -> OpenMetadataCopilotService:
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = OpenMetadataCopilotService()
        return _SERVICE


def openmetadata_catalog_accessible(service: OpenMetadataCopilotService, actor_id: str, is_admin: bool) -> bool:
    """Check the Dataset ACL that governs the shared OpenMetadata catalog."""

    dataset_id = str(getattr(service.config, "dataset_id", "") or "").strip()
    if dataset_id:
        from api.db.services.knowledgebase_service import KnowledgebaseService

        return bool(KnowledgebaseService.accessible(dataset_id, actor_id))
    return bool(is_admin)
