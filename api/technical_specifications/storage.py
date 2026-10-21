"""Transactional persistence. No network or model calls inside transactions."""

import json
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime
from functools import wraps

from peewee import IntegrityError

from api.db.db_models import TechnicalSpecification as Document
from api.db.db_models import TechnicalSpecificationAlignmentObservation as Observation
from api.db.db_models import TechnicalSpecificationDraft as Draft
from api.db.db_models import TechnicalSpecificationRevision as Revision
from api.db.db_models import User
from api.document_exchange import operations
from api.document_exchange.bindings import BindingConflict, occupancy, reserve
from api.source_workbench.errors import SourceWorkspaceError
from api.source_workbench.linked_storage import LinkedDocumentRepository

from .alignment import MAX_REPORT_BYTES, expire_checks
from .domain import SpecificationError, eva_identity, fail, identifier, version
from .telemetry import observation

MAX_PROJECTION_BYTES = 2_000_000
# JSON can use six bytes per content character: 100k candidate + 30k answer,
# plus result metadata. Every accepted request reserves its own completion.
CHAT_RESULT_BYTES = 800_000
ALIGNMENT_RESULT_BYTES = MAX_REPORT_BYTES + 16 * 1024


def _record_alignment_observations(before, after):
    prior = {item.get("attempt"): item for item in before}
    for item in after:
        if prior.get(item.get("attempt")) == item:
            continue
        values = observation(item)
        if values is None:
            continue
        attempt = values.pop("id")
        if not Observation.update(**values).where(Observation.id == attempt).execute():
            Observation.create(id=attempt, **values)


def _proposal_bases(document_id, numbers):
    return {row.number: row.sections for row in Revision.select(Revision.number, Revision.sections).where((Revision.document_id == document_id) & Revision.number.in_(numbers))} if numbers else {}


def _stored_payload(value):
    payload = {key: deepcopy(value[key]) for key in ("sections", "messages", "comments", "proposals", "eva", "origin", "eva_export")}
    if "alignment" in value:
        payload["alignment"] = deepcopy(value["alignment"])
    proposals = [item for item in payload["proposals"] if item.get("source") == "chat"]
    bases = _proposal_bases(value["id"], {item["base_revision"] for item in proposals})
    for item in proposals:
        base = bases[item["base_revision"]]
        item["patches"] = {key: content for key, content in item.pop("sections").items() if content != base[key]}
    return payload


def _payload_budget(payload):
    pending = sum(item.get("status") == "processing" for item in payload["messages"])
    pending_alignment = sum(item.get("status") == "PROCESSING" for item in payload.get("alignment", []))
    return len(json.dumps(payload, ensure_ascii=False).encode()) + pending * CHAT_RESULT_BYTES + pending_alignment * ALIGNMENT_RESULT_BYTES


def record(row):
    payload = deepcopy(row.payload)
    payload.setdefault("eva_export", None)
    compact = [item for item in payload["proposals"] if "patches" in item]
    bases = _proposal_bases(row.id, {item["base_revision"] for item in compact})
    for item in compact:
        item["sections"] = {**bases[item["base_revision"]], **item.pop("patches")}
    return {"id": row.id, "workspace_id": row.workspace_id, "tenant_id": row.tenant_id, "title": row.title, "version": row.version, "revision": row.revision, **payload}


def projection(row):
    value = record(row)
    value.pop("alignment", None)
    authors = {item["author_id"] for item in value["messages"] if item.get("author_id")}
    for item in value["comments"]:
        authors.add(item["author_id"])
        authors.update(reply["author_id"] for reply in item["replies"])
    value["people"] = {user.id: user.nickname for user in User.select(User.id, User.nickname).where(User.id.in_(authors))} if authors else {}
    return value


def connected(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        try:
            with self.database.connection_context() if self.database.is_closed() else nullcontext():
                return method(self, *args, **kwargs)
        except operations.OperationError as error:
            fail(error.code, error.message, 409)

    return invoke


class SpecificationRepository:
    def __init__(self, workspace_access=None):
        self.workspace_access = workspace_access or LinkedDocumentRepository().workspace

    @property
    def database(self):
        return Document._meta.database

    def access(self, actor, workspace, capability="read"):
        return self.workspace_access(actor, workspace, capability)

    @connected
    def list(self, actor, workspace):
        self.access(actor, workspace)
        return [
            {"id": row.id, "title": row.title, "revision": row.revision, "version": row.version}
            for row in Document.select(Document.id, Document.title, Document.version, Document.revision).where(Document.workspace_id == workspace).order_by(Document.updated_at.desc())
        ]

    def _row(self, workspace, document_id):
        row = Document.get_or_none((Document.id == document_id) & (Document.workspace_id == workspace))
        if row is None:
            fail("NOT_FOUND", "ТЗ не найдено", 404)
        return row

    @connected
    def get(self, actor, workspace, document_id):
        self.access(actor, workspace)
        return projection(self._row(workspace, document_id))

    @connected
    def alignment_document(self, actor, workspace, document_id):
        self.access(actor, workspace)
        for _attempt in range(5):
            row = self._row(workspace, document_id)
            payload = deepcopy(row.payload)
            now = datetime.now(UTC)
            if not expire_checks(payload.get("alignment", []), now):
                return {**projection(row), "alignment": deepcopy(payload.get("alignment", []))}
            # Recovery changes only expired operational state. A reader cannot
            # use this path to alter content, sources, verdicts or source access.
            with self.database.atomic():
                changed = Document.update(payload=payload, version=row.version + 1, updated_at=now).where((Document.id == row.id) & (Document.version == row.version)).execute()
                if changed:
                    _record_alignment_observations(row.payload.get("alignment", []), payload.get("alignment", []))
                    row = self._row(workspace, document_id)
                    return {**projection(row), "alignment": deepcopy(row.payload.get("alignment", []))}
        fail("VERSION_CONFLICT", "ТЗ изменилось во время восстановления проверки", 409)

    @connected
    def find_origin(self, actor, workspace, origin_key):
        project = self.access(actor, workspace)
        return self._origin(actor, project["tenant_id"] or project["owner_id"], origin_key)

    @connected
    def origin_matches(self, actor, workspace, origin_keys):
        project = self.access(actor, workspace)
        tenant = project["tenant_id"] or project["owner_id"]
        rows = Document.select(Document.id, Document.workspace_id, Document.origin_key).where((Document.tenant_id == tenant) & Document.origin_key.in_(origin_keys))
        access, result = {}, {}
        for row in rows:
            if row.workspace_id not in access:
                try:
                    self.access(actor, row.workspace_id)
                    access[row.workspace_id] = True
                except SourceWorkspaceError:
                    access[row.workspace_id] = False
            result[row.origin_key] = {
                "binding_available": False,
                "linked_document": {"id": row.id, "workspace_id": row.workspace_id} if access[row.workspace_id] else None,
            }
        for key, owner in occupancy(tenant, origin_keys).items():
            if owner["kind"] != "technical_specification":
                result[key] = {"binding_available": False, "linked_document": None}
        return result

    def _origin(self, actor, tenant_id, origin_key):
        occupied = occupancy(tenant_id, {origin_key}).get(origin_key)
        if occupied and occupied["kind"] != "technical_specification":
            fail("EVA_BINDING_EXISTS", "Страница уже связана с другим документом в организации. Для независимого ТЗ выберите копию", 409)
        row = Document.get_or_none((Document.tenant_id == tenant_id) & (Document.origin_key == origin_key))
        if row is None:
            return None
        try:
            self.access(actor, row.workspace_id)
        except SourceWorkspaceError:
            fail("EVA_BINDING_EXISTS", "Страница уже связана с ТЗ в организации. Для независимого документа выберите копию", 409)
        return projection(row)

    def _scope(self, actor, workspace):
        project = self.access(actor, workspace, "edit")
        return project["tenant_id"] or project["owner_id"]

    def _import_view(self, actor, value):
        if value and value["state"] == "completed":
            try:
                value = {**value, "document": self.get(actor, value["result"]["workspace_id"], value["result"]["id"])}
            except (SourceWorkspaceError, SpecificationError) as error:
                if error.status not in {403, 404}:
                    raise
                value = {**value, "result": {}, "document_unavailable": True}
        return value

    @connected
    def begin_import(self, actor, workspace, request_id, request):
        with self.database.atomic():
            value, attempt = operations.claim(self._scope(actor, workspace), "technical_specification", actor, request_id, workspace, request)
            return {"operation": self._import_view(actor, value), "attempt": attempt}

    @connected
    def import_operation(self, actor, workspace, request_id):
        with self.database.atomic():
            value = operations.status(self._scope(actor, workspace), "technical_specification", actor, request_id, workspace)
            return self._import_view(actor, value)

    @connected
    def cancel_import(self, actor, workspace, request_id, request):
        with self.database.atomic():
            value = operations.cancel(self._scope(actor, workspace), "technical_specification", actor, request_id, workspace, request)
            return self._import_view(actor, value)

    @connected
    def save_import(self, actor, workspace, request_id, attempt, values):
        with self.database.atomic():
            row = operations.guard(self._scope(actor, workspace), "technical_specification", actor, request_id, attempt)
            document = self.get(actor, values["workspace_id"], values["id"]) if "id" in values else self.create(actor, workspace, **values)
            operations.complete(row, {"workspace_id": document["workspace_id"], "id": document["id"]})
            return document

    @connected
    def fail_import(self, actor, workspace, request_id, attempt, error):
        with self.database.atomic():
            operations.failed(self._scope(actor, workspace), "technical_specification", actor, request_id, attempt, error)

    @connected
    def create(self, actor, workspace, title, sections, *, binding=None, origin_key=None, provenance=None):
        with self.database.atomic():
            project = self.access(actor, workspace, "edit")
            tenant = project["tenant_id"] or project["owner_id"]
            payload = {
                "sections": sections,
                "messages": [],
                "comments": [],
                "proposals": [],
                "eva": binding,
                "origin": {**provenance, "received_at": datetime.now(UTC).isoformat()} if provenance else None,
            }
            try:
                with self.database.atomic():
                    row = Document.create(id=identifier(), workspace_id=workspace, tenant_id=tenant, title=title, payload=payload, origin_key=origin_key)
                    if binding:
                        reserve(tenant, "technical_specification", row.id, binding)
                    Revision.create(document_id=row.id, number=1, title=title, sections=sections, author_id=actor, reason="import" if binding else "create")
            except BindingConflict:
                existing = self._origin(actor, tenant, origin_key)
                if existing is None:
                    fail("EVA_BINDING_EXISTS", "Страница уже связана с другим документом в организации. Для независимого ТЗ выберите копию", 409)
                return existing
            except IntegrityError:
                if not origin_key:
                    raise
                existing = self._origin(actor, tenant, origin_key)
                if existing is None:
                    raise
                return existing
            return projection(row)

    @connected
    def bind_eva(self, actor, workspace, document_id, expected, binding):
        try:
            with self.database.atomic():
                scope = self._scope(actor, workspace)

                def update(doc):
                    if doc["eva"]:
                        fail("EVA_ALREADY_BOUND", "ТЗ уже связано со страницей Eva", 409)
                    doc["eva"] = binding
                    doc["eva_export"] = None

                reserve(scope, "technical_specification", document_id, binding)
                result = self._change(actor, workspace, document_id, expected, update, "eva_bind", False)
                Document.update(origin_key=eva_identity(binding)).where(Document.id == document_id).execute()
                return result
        except (BindingConflict, IntegrityError):
            fail("EVA_BINDING_EXISTS", "Страница Eva уже связана с другим документом", 409)

    @connected
    def change(self, actor, workspace, document_id, expected_version, mutate, reason, *, latest=False):
        # Internal discussion updates merge into the latest projection. The
        # callback checks the request/attempt identity, never a client version.
        for attempt in range(5 if latest else 1):
            try:
                return self._change(actor, workspace, document_id, expected_version, mutate, reason, latest)
            except SpecificationError as error:
                if not latest or error.code != "VERSION_CONFLICT" or attempt == 4:
                    raise

    def _change(self, actor, workspace, document_id, expected_version, mutate, reason, latest):
        """CAS protects the entire projection; content changes append one revision."""
        with self.database.atomic():
            self.access(actor, workspace, "edit")
            row = self._row(workspace, document_id)
            if not latest and row.version != version(expected_version):
                fail("VERSION_CONFLICT", "ТЗ изменилось. Обновите его; ваши правки сохранены в редакторе", 409)
            old = row.payload
            value = record(row)
            before = deepcopy(value)
            mutate(value)
            if value == before:
                return projection(row)
            payload = _stored_payload(value)
            budget = _payload_budget(payload)
            if budget > MAX_PROJECTION_BYTES and budget > _payload_budget(old):
                fail("DOCUMENT_TOO_LARGE", "Недостаточно места в истории ТЗ для результата. Данные не были удалены")
            if payload == old and value["title"] == row.title:
                return projection(row)
            content_changed = payload["sections"] != old["sections"] or value["title"] != row.title
            number = row.revision + int(content_changed)
            changed = (
                Document.update(payload=payload, title=value["title"], revision=number, version=row.version + 1, updated_at=datetime.now(UTC))
                .where((Document.id == row.id) & (Document.version == row.version))
                .execute()
            )
            if not changed:
                fail("VERSION_CONFLICT", "Параллельная запись: обновите ТЗ", 409)
            if payload.get("alignment") != old.get("alignment"):
                _record_alignment_observations(old.get("alignment", []), payload.get("alignment", []))
            if content_changed:
                Revision.create(document_id=row.id, number=number, title=value["title"], sections=payload["sections"], author_id=actor, reason=reason)
            return projection(self._row(workspace, document_id))

    @connected
    def history(self, actor, workspace, document_id, number=None):
        self.access(actor, workspace)
        self._row(workspace, document_id)
        fields = [Revision.number, Revision.title, Revision.author_id, Revision.reason, Revision.created_at]
        query = Revision.select(*(fields + ([Revision.sections] if number is not None else []))).where(Revision.document_id == document_id).order_by(Revision.number.desc())
        if number is not None:
            query = query.where(Revision.number == number)
        rows = [
            {
                "number": row.number,
                "title": row.title,
                "author_id": row.author_id,
                "reason": row.reason,
                "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else str(row.created_at),
                **({"sections": row.sections} if number is not None else {}),
            }
            for row in query
        ]
        if number is not None and not rows:
            fail("NOT_FOUND", "Редакция не найдена", 404)
        return rows[0] if number is not None else rows

    @connected
    def get_draft(self, actor, workspace, document_id):
        self.access(actor, workspace)
        self._row(workspace, document_id)
        row = Draft.get_or_none((Draft.document_id == document_id) & (Draft.author_id == actor))
        return {"version": row.version, **row.payload} if row else None

    @connected
    def save_draft(self, actor, workspace, document_id, expected, payload):
        if type(expected) is not int or expected < 0:
            fail("INVALID_INPUT", "Укажите версию черновика")
        with self.database.atomic():
            self.access(actor, workspace)
            self._row(workspace, document_id)
            query = (Draft.document_id == document_id) & (Draft.author_id == actor)
            if expected == 0:
                try:
                    with self.database.atomic():
                        Draft.create(document_id=document_id, author_id=actor, payload=payload)
                except IntegrityError:
                    fail("DRAFT_CONFLICT", "Черновик изменён в другом окне. Локальный текст сохранён", 409)
                return {"version": 1, **payload}
            version(expected)
            if not Draft.update(payload=payload, version=expected + 1).where(query & (Draft.version == expected)).execute():
                fail("DRAFT_CONFLICT", "Черновик изменён в другом окне. Локальный текст сохранён", 409)
            return {"version": expected + 1, **payload}
