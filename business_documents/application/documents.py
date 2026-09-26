"""Document lifecycle scenarios with transactional, value-only boundaries."""

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol
import unicodedata

from business_documents.application.content import DocumentContent
from business_documents.application.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from business_documents.application.persistence import DocumentWriter
from business_documents.application.queries import DocumentQueries
from business_documents.domain.access import BusinessDocumentRole, DocumentAccess
from business_documents.domain.eva_binding import binding_keys
from business_documents.domain.names import normalize_title, title_key
from business_documents.domain.workflow import is_operation_quiescent


Record = Mapping[str, Any]
MAX_EVA_MARKDOWN_SIZE = 100_000


class DocumentCreationWriter(DocumentWriter, Protocol):
    def transaction(self) -> AbstractContextManager: ...
    def catalog_entry(self, entry_id: str) -> Record | None: ...
    def title_exists(self, key: str) -> bool: ...
    def chat_exists(self, chat_id: str) -> bool: ...
    def insert_document(self, values: Record) -> dict[str, Any]: ...
    def binding_occupancy(self, keys: set[str]) -> Mapping[str, Record]: ...
    def store_binding(self, document_id: str, binding: Record, *, replace: bool = False) -> None: ...


class DocumentCreationContent(DocumentContent, Protocol):
    def process_policy(self) -> Record: ...
    def import_document_markdown(self, markdown: object) -> dict[str, Any]: ...


class DatasetAccess(Protocol):
    def ensure_dataset_access(self, actor_id: str, dataset_ids: list[str]) -> None: ...
    def ensure_dataset_embedding_compatibility(self, dataset_ids: list[str]) -> None: ...


class EvaSource(Protocol):
    def find_title_matches(self, actor_id: str, title: str) -> list[dict[str, Any]]: ...
    def resolve_page_url(self, actor_id: str, page_url: object) -> dict[str, Any]: ...
    def read_connected_page(self, actor_id: str, binding: Record) -> tuple[dict[str, Any], str]: ...


@dataclass(frozen=True)
class _EvaInitialRevision:
    binding: dict[str, Any]
    document_ast: dict[str, Any]
    body_markdown: str
    event_id: str
    revision_id: str


class CreateDocument:
    def __init__(
        self, writer: DocumentCreationWriter, content: DocumentCreationContent, datasets: DatasetAccess, eva: EvaSource, queries: DocumentQueries, new_id: Callable[[], str], clock: Callable[[], int]
    ):
        self._writer, self._content, self._datasets, self._eva = writer, content, datasets, eva
        self._queries, self._new_id, self._clock = queries, new_id, clock

    def execute(self, tenant_id: str, actor_id: str, raw: object, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR) -> dict[str, Any]:
        if not DocumentAccess(actor_id, access_role, is_admin).capabilities()["create"]:
            raise PermissionDeniedError("This role cannot create business documents")
        if not isinstance(raw, dict):
            raise ValidationError("INVALID_DOCUMENT", "Request body must be a JSON object")
        if "chat_id" in raw:
            raise ValidationError("CHAT_ID_NOT_ALLOWED", "chat_id is assigned by the business document channel")
        schema_version = raw.get("schema_version")
        if schema_version not in {"2", "3"} and raw.get("eva_page_url"):
            raise ValidationError("EVA_BINDING_REQUIRES_SCHEMA_V2", "EVA page binding requires create-document schema version 2 or 3")
        if raw.get("eva_page_url"):
            decision = raw.get("eva_decision")
            if not isinstance(decision, dict) or decision.get("mode") != "BIND":
                raise ValidationError("EVA_BINDING_DECISION_REQUIRED", "Подтвердите выбор страницы EVA для импорта в новый документ")
            if schema_version == "2" and decision.get("confirm_replace") is not True:
                raise ValidationError("EVA_REPLACE_CONFIRMATION_REQUIRED", "Confirm that publishing this document will replace the current EVA page content")
        self._content.validate_contract({"2": "create_document_v2", "3": "create_document_v3"}.get(schema_version, "create_document"), raw)
        catalog = None
        if schema_version == "3":
            catalog = self._writer.catalog_entry(raw.get("catalog_entry_id"))
            if catalog is None or catalog["capability_level"] != "L5" or not catalog["is_active"]:
                raise ValidationError("DOCUMENT_CATALOG_ENTRY_NOT_ALLOWED", "catalog_entry_id must identify an active L5 business document")
        title = catalog["title"] if catalog else raw.get("title")
        idea, dataset_ids = raw.get("idea"), raw.get("dataset_ids", [])
        if not isinstance(title, str) or not title.strip():
            raise ValidationError("INVALID_TITLE", "title must be a non-empty string")
        if not isinstance(idea, str) or not idea.strip():
            raise ValidationError("INVALID_IDEA", "idea must be a non-empty string")
        display_title = " ".join(unicodedata.normalize("NFKC", title).strip().split())
        normalized_title = title_key(display_title)
        self._datasets.ensure_dataset_access(actor_id, dataset_ids)
        self._datasets.ensure_dataset_embedding_compatibility(dataset_ids)
        if self._writer.title_exists(normalized_title):
            raise ConflictError("DOCUMENT_TITLE_ALREADY_EXISTS", "A business document with this title already exists")
        document_id = self._new_id()
        chat_id = f"business-document:{document_id}"
        document_type = raw.get("document_type", "business_requirements")
        if document_type != "business_requirements":
            raise ValidationError("UNSUPPORTED_DOCUMENT_TYPE", "Only business_requirements is supported")
        template, policy = self._content.published_template(), self._content.process_policy()
        template_version, policy_version = raw.get("template_version", template["template_version"]), raw.get("policy_version", policy["policy_version"])
        if not all(isinstance(value, str) and value.strip() for value in (template_version, policy_version)):
            raise ValidationError("INVALID_CONFIGURATION_VERSION", "template_version and policy_version are required")
        if template_version != template["template_version"]:
            raise ValidationError("TEMPLATE_NOT_PUBLISHED", "Requested business requirements template version is not published")
        if policy_version != policy["policy_version"]:
            raise ValidationError("POLICY_NOT_PUBLISHED", "Requested business requirements policy version is not published")
        binding = self._resolve_binding(actor_id, raw, schema_version, display_title)
        imported = self._prepare_import(actor_id, schema_version, display_title, binding)
        if imported is not None:
            binding = imported.binding
        with self._writer.transaction():
            if self._writer.title_exists(normalized_title):
                raise ConflictError("DOCUMENT_TITLE_ALREADY_EXISTS", "A business document with this title already exists")
            if self._writer.chat_exists(chat_id):
                raise ConflictError("CHAT_ALREADY_BOUND", "The RAGFlow chat is already bound to a business document")
            document = self._writer.insert_document(
                {
                    "id": document_id,
                    "tenant_id": tenant_id,
                    "owner_id": actor_id,
                    "chat_id": chat_id,
                    "document_type": document_type,
                    "catalog_entry_id": catalog["id"] if catalog else None,
                    "title": display_title,
                    "title_key": normalized_title,
                    "idea": idea.strip(),
                    "dataset_ids": dataset_ids,
                    "template_version": template_version.strip(),
                    "policy_version": policy_version.strip(),
                    "lifecycle_state": "INTAKE",
                    "operation_state": "IDLE",
                    "state_version": 1,
                    "active_review_cycle": 0,
                }
            )
            if binding:
                self._writer.store_binding(document_id, binding)
            self._writer.append_event(
                document_id,
                1,
                "DocumentCreated",
                "USER",
                actor_id,
                {
                    "submitted_title": title,
                    "catalog_entry_id": catalog["id"] if catalog else None,
                    "title": display_title,
                    "chat_id": chat_id,
                    "document_type": document_type,
                    "idea": idea.strip(),
                    **({"eva_binding": binding} if binding else {}),
                },
                document_id,
            )
            if imported is not None:
                self._record_import(document, actor_id, imported)
        return self._queries.get_document(document_id, actor_id, is_admin, access_role)

    def _resolve_binding(self, actor_id, raw, schema_version, display_title):
        if schema_version not in {"2", "3"}:
            return None
        decision = raw.get("eva_decision")
        if isinstance(decision, dict) and decision.get("mode") == "SKIP":
            return None
        page_url = raw.get("eva_page_url")
        if not page_url:
            matches = self._matches_with_occupancy(self._eva.find_title_matches(actor_id, display_title))
            if schema_version == "3" and len(matches) == 1 and matches[0]["binding_available"]:
                page_url = matches[0].get("web_url")
            if not page_url:
                if matches:
                    raise ConflictError("EVA_BINDING_DECISION_REQUIRED", "В EVA найдены страницы с таким названием. Загрузите подходящую страницу или продолжите без привязки.", {"matches": matches})
                return None
        binding = self._eva.resolve_page_url(actor_id, page_url)
        if binding.get("status") not in ({"CONNECTED"} if schema_version == "3" else {"CONNECTED", "LINK_ONLY"}):
            raise ConflictError("EVA_BINDING_UNAVAILABLE", "The selected EVA page could not be verified")
        remote_title = str(binding.get("document_name") or "").strip()
        if remote_title and normalize_title(remote_title) != normalize_title(display_title):
            raise ConflictError("EVA_PAGE_TITLE_CHANGED", "The selected EVA page no longer has the document title", {"actual_title": binding.get("document_name")})
        return binding

    def _matches_with_occupancy(self, matches):
        keyed = [(match, *binding_keys(match)) for match in matches]
        occupied = self._writer.binding_occupancy({key for _, url_key, identity in keyed for key in (url_key, identity) if key})
        result = []
        for match, url_key, identity in keyed:
            owner = occupied.get(identity) or occupied.get(url_key)
            result.append({**match, "binding_available": owner is None, **({"linked_document": dict(owner)} if owner is not None else {})})
        return result

    def _prepare_import(self, actor_id, schema_version, display_title, binding):
        if schema_version != "3" or binding is None:
            return None
        if binding.get("status") != "CONNECTED":
            raise ConflictError("EVA_IMPORT_UNAVAILABLE", "Не удалось прочитать выбранную страницу EVA для импорта")
        refreshed, markdown = self._eva.read_connected_page(actor_id, binding)
        remote_title = str(refreshed.get("document_name") or "").strip()
        if remote_title and normalize_title(remote_title) != normalize_title(display_title):
            raise ConflictError("EVA_PAGE_TITLE_CHANGED", "Название выбранной страницы EVA изменилось", {"actual_title": remote_title})
        if len(markdown) > MAX_EVA_MARKDOWN_SIZE:
            raise ValidationError("EVA_DOCUMENT_TOO_LARGE", "Страница EVA слишком велика для импорта в бизнес-документ", {"maximum": MAX_EVA_MARKDOWN_SIZE})
        document_ast = self._content.import_document_markdown(markdown)
        event_id = self._new_id()
        return _EvaInitialRevision(
            {**refreshed, "last_pulled_content_hash": refreshed.get("remote_content_hash"), "last_pulled_at": self._clock(), "last_pull_event_id": event_id, "last_pull_review_cycle": 1},
            document_ast,
            self._content.render_document_ast(document_ast),
            event_id,
            self._new_id(),
        )

    def _record_import(self, document, actor_id, imported):
        binding = imported.binding
        self._writer.append_event(
            document["id"],
            2,
            "EvaDocumentImported",
            "USER",
            actor_id,
            {
                "review_cycle": 1,
                "revision_id": imported.revision_id,
                "page_url": binding["page_url"],
                "connector_id": binding["connector_id"],
                "document_id": binding["document_id"],
                "remote_version": binding.get("remote_version"),
                "remote_content_hash": binding.get("remote_content_hash"),
            },
            document["id"],
            event_id=imported.event_id,
        )
        self._writer.insert_revision(document["id"], 1, imported.document_ast, imported.body_markdown, [imported.event_id], actor_id, revision_id=imported.revision_id)
        self._writer.update_document(document, {"lifecycle_state": "REVIEW", "current_revision_id": imported.revision_id, "active_review_cycle": 1, "state_version": 2})


class DocumentRemovalWriter(Protocol):
    def transaction(self) -> AbstractContextManager: ...
    def lock_document(self, document_id: str) -> Mapping[str, Any] | None: ...
    def has_active_job(self, document_id: str) -> bool: ...
    def stage_artifact_cleanup(self, document_id: str) -> tuple[str, ...]: ...
    def delete_document_rows(self, document_id: str) -> None: ...


class ArtifactCleanup(Protocol):
    def after_delete(self, document_id: str, stage_ids: Sequence[str]) -> int: ...


class DeleteDocument:
    def __init__(self, writer: DocumentRemovalWriter, cleanup: ArtifactCleanup):
        self._writer = writer
        self._cleanup = cleanup

    def execute(self, actor_id: str, document_id: str, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR) -> dict[str, Any]:
        access = DocumentAccess(actor_id, access_role, is_admin)
        if not access.capabilities()["delete"]:
            raise PermissionDeniedError("Only an extended moderator or administrator can delete business documents")
        with self._writer.transaction():
            document = self._writer.lock_document(document_id)
            if document is None:
                raise NotFoundError()
            if not is_operation_quiescent(document["operation_state"], self._writer.has_active_job(document_id)):
                raise ConflictError("OPERATION_IN_PROGRESS", "A document with an active operation cannot be deleted")
            stage_ids = self._writer.stage_artifact_cleanup(document_id)
            self._writer.delete_document_rows(document_id)
        failures = self._cleanup.after_delete(document_id, stage_ids)
        return {"document_id": document_id, "deleted": True, "deleted_artifacts": len(stage_ids) - failures, "storage_cleanup_failures": failures}
