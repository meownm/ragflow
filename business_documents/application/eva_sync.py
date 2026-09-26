"""Synchronize a document with EVA through the existing approval workflow."""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol

from business_documents.application.documents import MAX_EVA_MARKDOWN_SIZE
from business_documents.application.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from business_documents.application.persistence import DocumentWriter
from business_documents.application.queries import DocumentQueries
from business_documents.domain.access import BusinessDocumentRole, DocumentAccess
from business_documents.domain.workflow import is_operation_quiescent


Record = Mapping[str, Any]


class EvaReader(Protocol):
    def document(self, document_id: str) -> Record | None: ...
    def revision(self, document_id: str, revision_id: str) -> Record | None: ...
    def bindings(self, document_ids: set[str]) -> Mapping[str, Record]: ...


class EvaWriter(DocumentWriter, Protocol):
    def transaction(self) -> AbstractContextManager: ...
    def lock_document(self, document_id: str) -> Record | None: ...
    def store_binding(self, document_id: str, binding: Record, *, replace: bool = False) -> None: ...


class EvaGateway(Protocol):
    def read_connected_page(self, actor_id: str, binding: Record) -> tuple[Record, str]: ...
    def resolve_page_url(self, actor_id: str, page_url: str) -> Record: ...
    def create_change(self, tenant_id: str, actor_id: str, values: Record) -> dict[str, Any]: ...


def _expected_version(raw: object, code: str) -> int:
    if not isinstance(raw, dict) or isinstance(raw.get("expected_state_version"), bool) or not isinstance(raw.get("expected_state_version"), int):
        raise ValidationError(code, "expected_state_version must be an integer")
    return raw["expected_state_version"]


def _require_edit(document: Record | None, access: DocumentAccess, expected: int, message: str) -> Record:
    if document is None:
        raise NotFoundError()
    if not access.permissions(document["owner_id"])["edit"]:
        raise PermissionDeniedError("Only the document owner or a moderator can edit this business document")
    if document["state_version"] != expected:
        raise ConflictError("STATE_VERSION_CONFLICT", message, {"expected": expected, "actual": document["state_version"]})
    if not is_operation_quiescent(document["operation_state"]):
        raise ConflictError("OPERATION_IN_PROGRESS", "Another operation is already in progress", {"operation_state": document["operation_state"]})
    return document


def _require_pull(document: Record) -> None:
    if document["lifecycle_state"] not in {"AGREED", "REVIEW"} or not document["current_revision_id"]:
        raise ConflictError("EVA_SYNC_REVIEW_REQUIRED", "EVA content can be pulled only after the first local revision exists")


def _require_rebind(document: Record) -> None:
    if document["lifecycle_state"] == "ARCHIVED":
        raise ConflictError("LIFECYCLE_STATE_CONFLICT", "Archived documents cannot be reconnected to EVA")


def _pull_is_current(document: Record, binding: Record, revision: Record, remote_hash: str) -> bool:
    pending = document["lifecycle_state"] == "REVIEW" and binding.get("last_pull_review_cycle") == document["active_review_cycle"]
    consumed = binding.get("last_pull_event_id") in (revision["source_event_ids"] or [])
    return binding.get("last_pulled_content_hash") == remote_hash and (pending or consumed)


class EvaSynchronization:
    def __init__(self, reader: EvaReader, writer: EvaWriter, source: EvaGateway, queries: DocumentQueries, new_id: Callable[[], str], clock: Callable[[], int]):
        self._reader, self._writer, self._source, self._queries = reader, writer, source, queries
        self._new_id, self._clock = new_id, clock

    def _binding(self, document_id: str, capability: str | None = None) -> Record:
        binding = self._reader.bindings({document_id}).get(document_id)
        if capability is not None and (not binding or capability not in binding.get("capabilities", [])):
            raise ConflictError("EVA_SYNC_UNAVAILABLE", "The linked EVA page is not connected to an accessible connector")
        if not binding:
            raise ConflictError("EVA_BINDING_MISSING", "The document has no EVA page link")
        return binding

    def pull_from_eva(
        self, tenant_id: str, actor_id: str, document_id: str, raw: object, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR
    ) -> dict[str, Any]:
        expected = _expected_version(raw, "INVALID_EVA_SYNC")
        access = DocumentAccess(actor_id, access_role, is_admin)
        document = _require_edit(self._reader.document(document_id), access, expected, "The document changed since it was loaded")
        _require_pull(document)
        binding = self._binding(document_id, "PULL_FROM_EVA")
        try:
            remote, markdown = self._source.read_connected_page(actor_id, binding)
        except ValidationError as error:
            if error.code == "EVA_DOCUMENT_EMPTY":
                raise ValidationError(error.code, "The linked EVA document has no published content") from error
            raise
        if len(markdown) > MAX_EVA_MARKDOWN_SIZE:
            raise ValidationError("EVA_DOCUMENT_TOO_LARGE", "The linked EVA document is too large for governed synchronization", {"maximum": MAX_EVA_MARKDOWN_SIZE})
        remote_hash, remote_version = remote["remote_content_hash"], remote.get("remote_version")
        sync = {"changed": False, "direction": "FROM_EVA", "remote_version": remote_version}
        with self._writer.transaction():
            document = _require_edit(self._writer.lock_document(document_id), access, expected, "The document changed during EVA synchronization")
            _require_pull(document)
            current_binding = self._reader.bindings({document_id}).get(document_id)
            if not current_binding or any(current_binding.get(key) != binding.get(key) for key in ("connector_id", "document_id", "page_url")):
                raise ConflictError("EVA_BINDING_CONFLICT", "The EVA link changed during synchronization")
            if "PULL_FROM_EVA" not in current_binding.get("capabilities", []):
                raise ConflictError("EVA_SYNC_UNAVAILABLE", "The linked EVA page is not connected to an accessible connector")
            revision = self._reader.revision(document_id, document["current_revision_id"])
            if revision is None:
                raise NotFoundError()
            if not _pull_is_current(document, current_binding, revision, remote_hash):
                cycle = document["active_review_cycle"] + int(document["lifecycle_state"] == "AGREED")
                version = document["state_version"] + 1
                document = self._writer.update_document(document, {"lifecycle_state": "REVIEW", "active_review_cycle": cycle, "state_version": version, "last_error": None})
                event_id = self._writer.append_event(
                    document_id,
                    version,
                    "EvaDocumentPulled",
                    "USER",
                    actor_id,
                    {
                        "review_cycle": cycle,
                        "revision_id": document["current_revision_id"],
                        "page_url": binding["page_url"],
                        "connector_id": binding["connector_id"],
                        "document_id": binding["document_id"],
                        "remote_version": remote_version,
                        "remote_content_hash": remote_hash,
                        "remote_markdown": markdown,
                    },
                    self._new_id(),
                )
                self._writer.store_binding(
                    document_id,
                    {
                        **current_binding,
                        "remote_version": remote_version,
                        "remote_content_hash": remote_hash,
                        "last_pulled_content_hash": remote_hash,
                        "last_pulled_at": self._clock(),
                        "last_pull_event_id": event_id,
                        "last_pull_review_cycle": cycle,
                    },
                    replace=True,
                )
                sync.update(changed=True, event_id=event_id)
        return {"document": self._queries.project(document, access), "sync": sync}

    def check_eva_update(
        self, tenant_id: str, actor_id: str, document_id: str, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR
    ) -> dict[str, Any]:
        access = DocumentAccess(actor_id, access_role, is_admin)
        document = self._reader.document(document_id)
        if document is None:
            raise NotFoundError()
        binding = self._binding(document_id, "PULL_FROM_EVA")
        remote, _ = self._source.read_connected_page(actor_id, binding)
        remote_hash = remote.get("remote_content_hash")
        baseline_hash = binding.get("last_pulled_content_hash") or binding.get("remote_content_hash")
        return {
            "document_id": document_id,
            "changed": bool(remote_hash and remote_hash != baseline_hash),
            "direction": "FROM_EVA",
            "remote_version": remote.get("remote_version"),
            "baseline_version": binding.get("remote_version"),
            "can_pull": bool(access.permissions(document["owner_id"])["edit"] and document["current_revision_id"] and document["lifecycle_state"] in {"AGREED", "REVIEW"}),
        }

    def rebind_eva(
        self, tenant_id: str, actor_id: str, document_id: str, raw: object, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR
    ) -> dict[str, Any]:
        expected = _expected_version(raw, "INVALID_EVA_BINDING")
        access = DocumentAccess(actor_id, access_role, is_admin)
        document = _require_edit(self._reader.document(document_id), access, expected, "The document changed since it was loaded")
        _require_rebind(document)
        binding = self._binding(document_id)
        resolved = self._source.resolve_page_url(actor_id, binding["page_url"])
        if resolved.get("status") != "CONNECTED":
            raise ConflictError("EVA_BINDING_UNAVAILABLE", "No accessible EVA connector matches the linked page", {"document_code": resolved.get("document_code")})
        with self._writer.transaction():
            document = _require_edit(self._writer.lock_document(document_id), access, expected, "The document changed during EVA reconnection")
            _require_rebind(document)
            latest_binding = self._reader.bindings({document_id}).get(document_id)
            if not latest_binding or latest_binding.get("page_url") != binding.get("page_url"):
                raise ConflictError("EVA_BINDING_CONFLICT", "The EVA link changed during reconnection")
            version = document["state_version"] + 1
            document = self._writer.update_document(document, {"state_version": version, "last_error": None})
            self._writer.append_event(
                document_id, version, "EvaBindingResolved", "USER", actor_id, {"document_title": document["title"], "previous_eva_binding": latest_binding, "eva_binding": resolved}, self._new_id()
            )
            self._writer.store_binding(document_id, resolved, replace=True)
        return self._queries.project(document, access)

    def create_eva_change_from_revision(
        self, tenant_id: str, actor_id: str, document_id: str, raw: object, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR
    ) -> dict[str, Any]:
        expected = _expected_version(raw, "INVALID_EVA_SYNC")
        access = DocumentAccess(actor_id, access_role, is_admin)
        document = _require_edit(self._reader.document(document_id), access, expected, "The document changed since it was loaded")
        if document["lifecycle_state"] != "AGREED":
            raise ConflictError("LIFECYCLE_STATE_CONFLICT", "Command requires AGREED lifecycle state", {"actual": document["lifecycle_state"]})
        if not document["current_revision_id"]:
            raise ConflictError("AGREED_REVISION_REQUIRED", "An agreed local revision is required")
        binding = self._binding(document_id, "CREATE_EVA_CHANGE")
        revision = self._queries.revision(document, document["current_revision_id"])
        basis_summary = "; ".join(item["summary"] for item in revision["change_basis"][:3] if item.get("summary"))
        summary = f"Синхронизация ревизии {revision['revision_number']} документа «{document['title']}»"
        if basis_summary:
            summary = f"{summary}: {basis_summary}"
        return self._source.create_change(
            tenant_id,
            actor_id,
            {
                "connector_id": binding["connector_id"],
                "document_id": binding["document_id"],
                "document_name": document["title"],
                "change_summary": summary[:50_000],
                "draft_markdown": revision["body_markdown"],
            },
        )
