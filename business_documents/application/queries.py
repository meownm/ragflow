"""Pure assembly of published document views from already loaded values."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from business_documents.application.content import DocumentContent
from business_documents.domain.content import render_section_text, section_hash
from business_documents.application.errors import BusinessDocumentError, ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from business_documents.domain.access import BusinessDocumentRole, DocumentAccess, normalize_role
from business_documents.domain.review import ReviewState
from business_documents.domain.workflow import available_commands


Record = Mapping[str, Any]


@dataclass(frozen=True)
class DocumentPage:
    rows: tuple[Record, ...]
    total: int
    users: Mapping[str, Record]
    revision_numbers: Mapping[str, int]
    bindings: Mapping[str, Record]
    latest_jobs: Mapping[str, Record]


@dataclass(frozen=True)
class RevisionHistory:
    rows: tuple[Record, ...]
    events: Mapping[str, Record]
    questions: Mapping[str, Record]
    answers: Mapping[str, Record]
    proposals: Mapping[str, Record]
    comments: Mapping[str, Record]
    jobs: Mapping[str, Record]
    users: Mapping[str, Record]


class DocumentReader(Protocol):
    def document(self, document_id: str) -> Record | None: ...
    def revision(self, document_id: str, revision_id: str) -> Record | None: ...
    def page(self, owner_id: str | None, page: int, page_size: int) -> DocumentPage: ...
    def revisions(self, document_id: str, revision_id: str | None = None) -> RevisionHistory: ...
    def review(self, document_id: str, review_cycle: int) -> ReviewState: ...
    def users(self, user_ids: set[str]) -> Mapping[str, Record]: ...
    def bindings(self, document_ids: set[str]) -> Mapping[str, Record]: ...
    def jobs(self, document_id: str) -> Iterable[Record]: ...
    def job(self, document_id: str, job_id: str) -> Record | None: ...
    def stream_events(self, job_id: str, after: int) -> tuple[list[dict[str, Any]], str | None]: ...
    def latest_job(self, document_id: str) -> Record | None: ...
    def preview(self, document: Record, now: int) -> Record | None: ...
    def exports(self, document_id: str) -> Iterable[Record]: ...
    def catalog(self) -> Iterable[Record]: ...
    def active_users(self) -> Iterable[Record]: ...


def project_job(row: Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    fields = ("job_type", "status", "progress", "progress_stage", "progress_message", "attempt", "max_attempts", "available_at", "lease_expires_at", "error", "create_time", "update_time")
    return {"job_id": row["id"], **{name: row[name] for name in fields}}


def _display_name(user_id: str | None, users: Mapping[str, Record]) -> str | None:
    return (users.get(user_id, {}).get("nickname") or user_id) if user_id else None


def _with_section_texts(revision: dict[str, Any]) -> dict[str, Any]:
    sections = revision["document_ast"].get("sections", []) if isinstance(revision["document_ast"], dict) else []
    return {
        **revision,
        "section_texts": {
            section["id"]: render_section_text(section) for section in sections if isinstance(section, dict) and isinstance(section.get("id"), str) and isinstance(section.get("blocks"), list)
        },
    }


def _revision_authors(history: RevisionHistory) -> dict[str, str]:
    authors = {row["id"]: row["author_id"] for row in history.rows if row.get("author_id")}
    revision_ids = {row["id"] for row in history.rows}
    for event in history.events.values():
        payload = event["payload"]
        if event["event_type"] not in {"DraftCreated", "ChangesApplied"} or not isinstance(payload, dict):
            continue
        revision_id = payload.get("revision_id")
        if revision_id not in revision_ids or revision_id in authors:
            continue
        requested_by = payload.get("requested_by_actor_id")
        if isinstance(requested_by, str) and requested_by:
            authors[revision_id] = requested_by
            continue
        job = history.jobs.get(payload.get("job_id"))
        if job and isinstance(job.get("payload"), dict):
            requested_by = job["payload"].get("requested_by_actor_id")
            if isinstance(requested_by, str) and requested_by:
                authors[revision_id] = requested_by
                continue
        if event["actor_type"] == "USER":
            authors[revision_id] = event["actor_id"]
    return authors


def _change_basis(document: Record, row: Record, history: RevisionHistory) -> list[dict[str, Any]]:
    basis = []
    for event_id in row.get("source_event_ids") or []:
        event = history.events.get(event_id) if isinstance(event_id, str) else None
        if event is None or event["event_type"] in {"ChangesApplied", "ReviewAgreedWithoutChanges"}:
            continue
        payload = event["payload"]
        item = {"event_id": event["id"], "actor_id": event["actor_id"], "actor_type": event["actor_type"], "created_at": event["create_time"]}
        requested_by = payload.get("requested_by_actor_id") if isinstance(payload, dict) else None
        if isinstance(requested_by, str) and requested_by:
            item["initiated_by_actor_id"] = requested_by
        kind = event["event_type"]
        if kind == "DraftCreated":
            item.update(type="INITIAL_DRAFT", title="Первичный черновик", summary=document["idea"], section_id=None)
        elif kind == "QuestionAnswered":
            question, answer = history.questions.get(payload.get("question_id")), history.answers.get(payload.get("answer_id"))
            if question is None or answer is None:
                continue
            option_label = next(
                (str(option.get("label") or option.get("option_id")) for option in question["options"] if isinstance(option, dict) and option.get("option_id") == answer["selected_option_id"]), None
            )
            item.update(type="QUESTION", title="Ответ на вопрос", summary=question["text"], details=answer["custom_answer"] or option_label, section_id=question["target_section_id"])
        elif kind == "ProposalDecided" and payload.get("decision") == "ACCEPTED":
            proposal = history.proposals.get(payload.get("proposal_id"))
            if proposal is None:
                continue
            item.update(type="PROPOSAL", title="Принятое предложение ИИ", summary=proposal["text"], details=proposal["rationale"], section_id=proposal["target_section_id"])
        elif kind == "AuthorCommentAdded":
            comment = history.comments.get(payload.get("comment_id"))
            if comment is None:
                continue
            anchor = comment.get("anchor")
            item.update(
                type="COMMENT", title="Комментарий автора", summary=comment["text"], details=anchor.get("selected_text") if isinstance(anchor, dict) else None, section_id=comment["section_id"]
            )
        elif kind in {"EvaDocumentImported", "EvaDocumentPulled"}:
            item.update(
                type="EVA_SYNC",
                title="Исходная версия из EVA" if kind == "EvaDocumentImported" else "Изменения из EVA",
                summary=str(payload.get("page_url") or "Связанная страница EVA"),
                details=str(payload.get("remote_version") or "") or None,
                section_id=None,
            )
        else:
            continue
        for prefix, user_id in (("initiated_by_actor", requested_by), ("actor", event["actor_id"] if event["actor_type"] == "USER" else None)):
            user = history.users.get(user_id)
            if user:
                item[f"{prefix}_name"] = user["nickname"] or None
                item[f"{prefix}_login"] = user["email"] or None
        basis.append(item)
    return basis


class DocumentQueries:
    """Read shared documents through bounded loaders and value-only projections."""

    def __init__(self, reader: DocumentReader, content: DocumentContent, clock: Callable[[], int]):
        self._reader = reader
        self._content = content
        self._clock = clock

    def _document(self, document_id: str) -> Record:
        document = self._reader.document(document_id)
        if document is None:
            raise NotFoundError()
        return document

    def list_catalog(self) -> dict[str, Any]:
        items = list(self._reader.catalog())
        return {"items": items, "total": len(items)}

    def list_documents(
        self, actor_id: str, page: int = 1, page_size: int = 20, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR, scope: str = "all"
    ) -> dict[str, Any]:
        if not isinstance(page, int) or not isinstance(page_size, int) or page < 1 or not 1 <= page_size <= 100:
            raise ValidationError("INVALID_PAGINATION", "page must be positive and page_size must be between 1 and 100")
        if scope not in {"mine", "all"}:
            raise ValidationError("INVALID_DOCUMENT_SCOPE", "scope must be mine or all")
        access = DocumentAccess(actor_id, access_role, is_admin)
        data = self._reader.page(actor_id if scope == "mine" else None, page, page_size)
        return {
            "items": [
                {
                    "document_id": row["id"],
                    "owner_id": row["owner_id"],
                    "owner_name": _display_name(row["owner_id"], data.users),
                    **{name: row[name] for name in ("catalog_entry_id", "title", "lifecycle_state", "operation_state", "state_version", "update_time")},
                    "current_revision_number": data.revision_numbers.get(row["current_revision_id"]),
                    "eva_page_url": data.bindings.get(row["id"], {}).get("page_url"),
                    "latest_job": project_job(data.latest_jobs.get(row["id"])),
                    "access_role": access.role.value,
                    "permissions": access.permissions(row["owner_id"]),
                }
                for row in data.rows
            ],
            "page": page,
            "page_size": page_size,
            "total": data.total,
            "scope": scope,
            "access_role": access.role.value,
            "capabilities": access.capabilities(),
        }

    def list_access_users(self, actor_id: str, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR) -> dict[str, Any]:
        if not DocumentAccess(actor_id, access_role, is_admin).capabilities()["assign"]:
            raise PermissionDeniedError("Only an extended moderator or administrator can assign business documents")
        return {
            "items": [
                {"user_id": row["id"], "nickname": row["nickname"], "email": row["email"], "role": normalize_role(row["business_document_role"], bool(row["is_superuser"])).value}
                for row in self._reader.active_users()
            ]
        }

    def _revisions(self, document: Record, history: RevisionHistory) -> list[dict[str, Any]]:
        result = []
        authors = _revision_authors(history)
        for row in history.rows:
            author_id = authors.get(row["id"])
            author = history.users.get(author_id) or {}
            result.append(
                {
                    "revision_id": row["id"],
                    "revision_number": row["revision_number"],
                    "author_id": author_id,
                    "author_name": author.get("nickname") or None,
                    "author_login": author.get("email") or None,
                    "document_ast": row["document_ast"],
                    "body_markdown": row["body_markdown"],
                    "content_hash": row["content_hash"],
                    "source_event_ids": row["source_event_ids"],
                    "created_at": row["create_time"],
                    "change_basis": _change_basis(document, row, history),
                }
            )
        return result

    def _revision(self, document: Record, revision_id: str) -> dict[str, Any]:
        rows = self._revisions(document, self._reader.revisions(document["id"], revision_id))
        if not rows:
            raise BusinessDocumentError("REVISION_NOT_FOUND", "Business document revision not found", 404)
        return rows[0]

    def revision(self, document: Record, revision_id: str) -> dict[str, Any]:
        return _with_section_texts(self._revision(document, revision_id))

    def revision_snapshot(self, document: Record, revision_id: str) -> dict[str, Any]:
        revision = self._revision(document, revision_id)
        revision["section_hashes"] = {
            section["id"]: section_hash(section) for section in revision["document_ast"].get("sections", []) if isinstance(section, dict) and isinstance(section.get("id"), str)
        }
        return revision

    def list_revisions(self, document_id: str) -> list[dict[str, Any]]:
        document = self._document(document_id)
        return [_with_section_texts(revision) for revision in self._revisions(document, self._reader.revisions(document_id))]

    def get_revision(self, document_id: str, revision_id: str) -> dict[str, Any]:
        return self.revision(self._document(document_id), revision_id)

    def get_document(self, document_id: str, actor_id: str, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR) -> dict[str, Any]:
        return self.project(self._document(document_id), DocumentAccess(actor_id, access_role, is_admin))

    def project(self, document: Record, access: DocumentAccess | None = None) -> dict[str, Any]:
        review = self._reader.review(document["id"], document["active_review_cycle"])
        access = access or DocumentAccess(document["owner_id"])
        permissions = access.permissions(document["owner_id"])
        preview = self._reader.preview(document, self._clock())
        commands = available_commands(document, review, preview=preview)
        fields = (
            "tenant_id",
            "owner_id",
            "chat_id",
            "document_type",
            "catalog_entry_id",
            "title",
            "idea",
            "dataset_ids",
            "template_version",
            "policy_version",
            "lifecycle_state",
            "operation_state",
            "state_version",
            "active_review_cycle",
            "last_error",
        )
        return {
            "document_id": document["id"],
            **{name: document[name] for name in fields},
            "access_role": access.role.value,
            "permissions": permissions,
            "owner_name": _display_name(document["owner_id"], self._reader.users({document["owner_id"]})),
            "current_revision": self.revision(document, document["current_revision_id"]) if document["current_revision_id"] else None,
            "protocol": project_protocol(review, document["current_revision_id"]),
            "allowed_commands": commands if permissions["edit"] else [],
            "latest_job": project_job(self._reader.latest_job(document["id"])),
            "change_preview": {"job_id": preview["id"], "base_revision_id": preview["base_revision_id"]} if preview else None,
            "latest_exports": list(self._reader.exports(document["id"])),
            "eva_binding": self._reader.bindings({document["id"]}).get(document["id"]),
        }

    def read_job_stream_events(
        self,
        tenant_id: str,
        actor_id: str,
        document_id: str,
        job_id: str,
        after: int,
        is_admin: bool = False,
        access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR,
    ) -> dict[str, Any]:
        document = self._document(document_id)
        if document["tenant_id"] != tenant_id:
            raise NotFoundError()
        if not DocumentAccess(actor_id, access_role, is_admin).permissions(document["owner_id"])["read"]:
            raise PermissionDeniedError("This role cannot read business documents")
        job = self._reader.job(document_id, job_id)
        if job is None or job["tenant_id"] != tenant_id:
            raise NotFoundError()
        if type(after) is not int or after < 0:
            raise ValidationError("INVALID_EVENT_CURSOR", "Event cursor must be a non-negative integer")
        events, status = self._reader.stream_events(job_id, after)
        return {"events": events, "status": status, "job_id": job_id}

    def list_jobs(self, document_id: str) -> list[dict[str, Any]]:
        self._document(document_id)
        return [project_job(row) for row in self._reader.jobs(document_id)]

    def get_change_preview(self, document_id: str, job_id: str) -> dict[str, Any]:
        document = self._document(document_id)
        job = self._reader.preview(document, self._clock())
        if job is None or job["id"] != job_id:
            raise ConflictError("CHANGE_PREVIEW_STALE", "The prepared changes are no longer current")
        result = job["result"] if isinstance(job["result"], dict) else {}
        output = result.get("output")
        plan = output.get("change_plan") if isinstance(output, dict) else None
        self._content.validate_contract("change_plan", plan)
        revision = self._reader.revision(document_id, document["current_revision_id"])
        if revision is None:
            raise BusinessDocumentError("REVISION_NOT_FOUND", "Business document revision not found", 404)
        base = revision["document_ast"]
        draft = self._content.apply_change_plan(base, plan)
        before, after = ({section["id"]: section for section in ast["sections"]} for ast in (base, draft))
        sources = PreviewSources.from_snapshot(job["payload"] if isinstance(job["payload"], dict) else {})
        return {
            "job_id": job["id"],
            "base_revision_id": job["base_revision_id"],
            "state_version": document["state_version"],
            "sections": [
                {
                    "section_id": operation["section_id"],
                    "title": before[operation["section_id"]]["title"],
                    "before": render_section_text(before[operation["section_id"]]),
                    "after": render_section_text(after[operation["section_id"]]),
                    "before_evidence_refs": before[operation["section_id"]].get("evidence_refs", []),
                    "after_evidence_refs": after[operation["section_id"]].get("evidence_refs", []),
                    "source_event_ids": operation["source_event_ids"],
                    "sources": sources.describe(operation["source_event_ids"]),
                }
                for operation in plan["operations"]
            ],
            "acknowledged_no_change_event_ids": plan["acknowledged_no_change_event_ids"],
            "acknowledged_no_change_sources": sources.describe(plan["acknowledged_no_change_event_ids"]),
        }


def project_protocol(review: ReviewState, current_revision_id: str | None) -> dict[str, Any]:
    questions = []
    for question in review.questions:
        answer = review.answers.get(question["id"])
        questions.append(
            {
                "question_id": question["id"],
                "stage": question["stage"],
                "review_cycle": question["review_cycle"],
                "target_section_id": question["target_section_id"],
                "semantic_tag": question["semantic_tag"],
                "text": question["text"],
                "options": question["options"],
                "allow_custom_answer": question["allow_custom_answer"],
                "evidence_refs": question["evidence_refs"],
                "source_event_ids": question["source_event_ids"],
                "answer": {
                    "answer_id": answer["id"],
                    "selected_option_id": answer["selected_option_id"],
                    "custom_answer": answer["custom_answer"],
                    "actor_id": answer["actor_id"],
                    "source_event_id": review.protocol_events["answers"].get(answer["id"]),
                }
                if answer
                else None,
                "status": "ANSWERED" if answer else "OPEN",
            }
        )
    return {
        "questions": questions,
        "proposals": [
            {
                "proposal_id": row["id"],
                "review_cycle": row["review_cycle"],
                "target_section_id": row["target_section_id"],
                "text": row["text"],
                "rationale": row["rationale"],
                "source_event_ids": row["source_event_ids"],
                "evidence_refs": row["evidence_refs"],
                "decision": review.decisions[row["id"]]["decision"] if row["id"] in review.decisions else "PENDING",
                "decision_event_id": review.protocol_events["decisions"].get(row["id"]),
            }
            for row in review.proposals
        ],
        "comments": [
            {
                "comment_id": row["id"],
                "review_cycle": row["review_cycle"],
                "revision_id": row["revision_id"],
                "section_id": row["section_id"],
                "text": row["text"],
                "anchor": row["anchor"],
                "anchor_status": "GENERAL" if row["anchor"] is None else "ANCHORED" if row["revision_id"] == current_revision_id else "ORPHANED",
                "source_event_id": review.protocol_events["comments"].get(row["id"]),
                "disposition": review.comment_dispositions.get(review.protocol_events["comments"].get(row["id"])),
            }
            for row in review.comments
        ],
    }


@dataclass(frozen=True)
class PreviewSources:
    events: Mapping[str, Mapping[str, Any]]
    questions: Mapping[str, Mapping[str, Any]]
    proposals: Mapping[str, Mapping[str, Any]]
    comments: Mapping[str, Mapping[str, Any]]

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> PreviewSources:
        protocol = snapshot.get("protocol") if isinstance(snapshot.get("protocol"), dict) else {}
        return cls(
            events={event.get("event_id"): event for event in snapshot.get("source_events", []) if isinstance(event, dict)},
            questions={item.get("question_id"): item for item in protocol.get("questions", []) if isinstance(item, dict)},
            proposals={item.get("proposal_id"): item for item in protocol.get("proposals", []) if isinstance(item, dict)},
            comments={item.get("comment_id"): item for item in protocol.get("comments", []) if isinstance(item, dict)},
        )

    def describe(self, event_ids: Iterable[str]) -> list[dict[str, Any]]:
        sources = []
        for event_id in event_ids:
            event = self.events.get(event_id) or {}
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            kind = event.get("event_type")
            if kind == "QuestionAnswered":
                source_kind, entity_id, label, item = "question", payload.get("question_id"), "Ответ на вопрос", self.questions.get(payload.get("question_id"))
            elif kind == "ProposalDecided":
                source_kind, entity_id, label, item = "proposal", payload.get("proposal_id"), "Принятое предложение", self.proposals.get(payload.get("proposal_id"))
            elif kind == "AuthorCommentAdded":
                source_kind, entity_id, label, item = "comment", payload.get("comment_id"), "Комментарий автора", self.comments.get(payload.get("comment_id"))
            else:
                source_kind, entity_id, label, item = "eva", None, "Загруженная версия EVA", None
            sources.append({"event_id": event_id, "kind": source_kind, "entity_id": entity_id, "label": label, "text": item.get("text") if item else None})
        return sources
