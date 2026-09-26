"""Prepared export values and the publication boundary used by job completion."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class PreparedExport:
    artifact_id: str
    document_id: str
    tenant_id: str
    owner_id: str
    revision_id: str
    revision_number: int
    export_format: str
    filename: str
    mime_type: str
    size: int
    content_hash: str
    storage_bucket: str
    storage_key: str
    create_time: int
    create_date: datetime
    created_blob: bool
    stage_id: str | None = None
    lease_token: str | None = None
    replaced_artifact_id: str | None = None
    replaced_storage_bucket: str | None = None
    replaced_storage_key: str | None = None
    replaced_content_hash: str | None = None


class ExportCommitter(Protocol):
    def commit(self, prepared: object, *, document: Mapping[str, Any], job: Mapping[str, Any]) -> dict[str, Any]: ...
