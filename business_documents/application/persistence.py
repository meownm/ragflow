"""Writes shared by document scenarios within an already open transaction."""

from collections.abc import Mapping
from typing import Any, Protocol


Record = Mapping[str, Any]


class DocumentWriter(Protocol):
    def append_job_event(self, job_id: str, event_type: str, payload: Record) -> None: ...

    def insert_revision(
        self, document_id: str, revision_number: int, document_ast: Record, body: str, source_event_ids: list[str], author_id: str | None = None, *, revision_id: str | None = None
    ) -> str: ...
    def update_document(self, document: Record, changes: Record) -> dict[str, Any]: ...
    def append_event(self, document_id: str, sequence: int, event_type: str, actor_type: str, actor_id: str, payload: Record, correlation_id: str, *, event_id: str | None = None) -> str: ...
