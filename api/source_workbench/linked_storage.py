"""Peewee transactions and projections for versioned workspace documents."""

from contextlib import nullcontext
from datetime import datetime, timezone
from functools import wraps
from functools import reduce
from operator import or_
import time

from peewee import IntegrityError, SqliteDatabase, fn

from api.db.db_models import (
    SourceWorkspace,
    SourceWorkspaceDraft as Document,
    SourceWorkspaceMember,
    SourceDocumentRevision as Revision,
    SourceDocumentDependency as Dependency,
    SourceDocumentInput as RevisionInput,
    SourceDocumentImpact as Impact,
    SourceDocumentChange as Change,
    SourceDocumentDecision as Decision,
    SourceDocumentJob as Job,
    SourceDocumentEvent as Event,
    UserTenant,
    User,
    Tenant,
)
from .linked_domain import (
    MAX_DEPENDENCIES,
    WORKSPACE_CAPABILITIES,
    assert_approval,
    assert_approver,
    assert_current_inputs,
    assert_editable,
    assert_version,
    confirmation_inputs,
    digest,
    fail,
    heading,
    identifier,
    input_manifest,
    manual_candidate,
    manual_plan,
    render,
    split_sections,
    input_reviewed,
)
from .linked_graph import assert_acyclic_link, cyclic_components


def connected(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self.connection():
            return method(self, *args, **kwargs)

    return invoke


def record(row):
    values = dict(row.__data__)
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in values.items() if key not in {"create_time", "create_date", "update_time", "update_date"}}


class LinkedDocumentRepository:
    def __init__(self, business=None):
        self.business = business

    def _guard_business(self, actor, inputs):
        if not self.business:
            return
        ids = [item["source_id"] for item in inputs]
        documents = {row.id: row for row in Document.select(Document.id, Document.source_kind, Document.source_ref).where(Document.id.in_(ids))}
        references = [
            {**documents[item["source_id"]].source_ref, "revision": item["source_marker"]}
            for item in inputs
            if documents.get(item["source_id"]) and documents[item["source_id"]].source_kind == "business"
        ]
        if references:
            self.business.validate(actor, references, lock=True, fresh=True)

    def _lock_workspace(self, workspace_id):
        query = SourceWorkspace.select(SourceWorkspace.id).where(SourceWorkspace.id == workspace_id)
        if not isinstance(self.database, SqliteDatabase):
            query = query.for_update()
        if query.first() is None:
            fail("NOT_FOUND", "Рабочее пространство не найдено", 404)

    @connected
    def graph_data(self, workspace_id):
        with self.database.atomic():
            if not isinstance(self.database, SqliteDatabase):
                self.database.execute_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            documents = [
                record(row) for row in Document.select(*[field for name, field in Document._meta.fields.items() if name not in {"content", "prompt"}]).where(Document.workspace_id == workspace_id)
            ]
            links = [record(row) for row in Dependency.select().where(Dependency.workspace_id == workspace_id)]

            def latest(model, names):
                ranked = (
                    model.select(*[getattr(model, name) for name in names], fn.ROW_NUMBER().over(partition_by=[model.document_id], order_by=[model.created_at.desc(), model.id.desc()]).alias("rank"))
                    .where(model.workspace_id == workspace_id)
                    .cte("latest")
                )
                return list(ranked.select_from(*[getattr(ranked.c, name) for name in names]).where(ranked.c.rank == 1).dicts())

            changes = latest(Change, ["document_id", "document_version", "status"])
            jobs = latest(Job, ["document_id", "status"])
            users = {row.id: row.nickname for row in User.select(User.id, User.nickname).where(User.id.in_({row[key] for row in documents for key in ("responsible_id", "approver_id")}))}
            return documents, links, changes, jobs, users

    @connected
    def assert_no_cycle(self, workspace_id, document_id):
        links = [record(row) for row in Dependency.select().where(Dependency.workspace_id == workspace_id)]
        if any(document_id in component for component in cyclic_components(links)):
            fail("DEPENDENCY_CYCLE", "Устраните цикл актуализации в источниках и связях", 409)

    @connected
    def implementation_history(self, document_id):
        rows = list(
            Event.select().where((Event.document_id == document_id) & Event.kind.in_(["implementation_confirmed", "implementation_revoked"])).order_by(Event.created_at.desc(), Event.id.desc())
        )
        users = {row.id: row.nickname for row in User.select(User.id, User.nickname).where(User.id.in_({row.actor_id for row in rows}))} if rows else {}
        return [{**record(row), "actor_name": users.get(row.actor_id, "Пользователь удалён")} for row in rows]

    @connected
    def implementation(self, actor, workspace_id, document_id, version, *, revision_id=None, event_id=None, comment="", evidence_url=""):
        with self.database.atomic():
            self.workspace(actor, workspace_id, "edit")
            row = self._lock_documents([document_id]).get(document_id)
            if row is None or row.workspace_id != workspace_id:
                fail("DOCUMENT_NOT_FOUND", "Документ не найден", 404)
            active = User.select(User.id).where((User.id == actor) & (User.status == "1") & (User.is_active == "1")).exists()
            if row.source_kind != "local" or row.responsible_id != actor or not active:
                fail("PERMISSION_DENIED", "Реализацию подтверждает ответственный за документ", 403)
            if revision_id:
                if revision_id != row.approved_revision_id:
                    fail("REVISION_NOT_APPROVED", "Выберите последнюю утверждённую редакцию", 409)
                if row.implemented_revision_id == revision_id and row.implementation_event_id:
                    return record(row)
            else:
                if any(item["payload"].get("implementation_event_id") == event_id for item in self.implementation_history(document_id) if item["kind"] == "implementation_revoked"):
                    return record(row)
                if not event_id or event_id != row.implementation_event_id:
                    fail("IMPLEMENTATION_CHANGED", "Подтверждение реализации изменилось", 409)
            assert_version(record(row), version)
            event = Event.create(
                id=identifier(),
                workspace_id=workspace_id,
                document_id=document_id,
                actor_id=actor,
                kind="implementation_confirmed" if revision_id else "implementation_revoked",
                payload={"revision_id": revision_id or row.implemented_revision_id, "implementation_event_id": event_id, "comment": comment, "evidence_url": evidence_url},
            )
            row.implemented_revision_id = revision_id
            row.implementation_event_id = event.id if revision_id else None
            row.version += 1
            row.updated_at = datetime.now(timezone.utc)
            row.save()
            return record(row)

    @property
    def database(self):
        return Document._meta.database

    def connection(self):
        return self.database.connection_context() if self.database.is_closed() else nullcontext()

    def _lock_documents(self, ids, *, include_content=True):
        fields = list(Document._meta.fields.values()) if include_content else [field for name, field in Document._meta.fields.items() if name not in {"content", "prompt"}]
        query = Document.select(*fields).where(Document.id.in_(sorted(set(ids)))).order_by(Document.id)
        if not isinstance(self.database, SqliteDatabase):
            query = query.for_update()
        return {row.id: row for row in query}

    @connected
    def workspace(self, actor, workspace_id, capability="read"):
        # Every mutating transaction orders workspace before membership/document locks.
        if capability != "read" and self.database.in_transaction():
            self._lock_workspace(workspace_id)
        row = SourceWorkspace.get_or_none(SourceWorkspace.id == workspace_id)
        if row is None:
            fail("NOT_FOUND", "Рабочее пространство не найдено", 404)
        role = "owner" if row.owner_id == actor else None
        if role is None:
            members = SourceWorkspaceMember.select().where((SourceWorkspaceMember.workspace_id == workspace_id) & (SourceWorkspaceMember.user_id == actor))
            if capability != "read" and self.database.in_transaction() and not isinstance(self.database, SqliteDatabase):
                members = members.for_update()
            member = members.first()
            role = member.role if member else None
        tenant_access = not row.tenant_id or UserTenant.select().where((UserTenant.user_id == actor) & (UserTenant.tenant_id == row.tenant_id) & (UserTenant.status == "1")).exists()
        if capability == "read" and (role not in WORKSPACE_CAPABILITIES["read"] or not tenant_access):
            administrator = User.select().where((User.id == actor) & User.is_superuser & (User.is_active == "1") & (User.status == "1")).exists()
            if administrator:
                return {**record(row), "role": "reader"}
        if role not in WORKSPACE_CAPABILITIES[capability]:
            fail("NOT_FOUND" if role is None else "PERMISSION_DENIED", "Недостаточно прав для работы с документами", 404 if role is None else 403)
        if not tenant_access:
            fail("PERMISSION_DENIED", "Доступ к организации рабочего пространства отозван", 403)
        return {**record(row), "role": role}

    @connected
    def members(self, actor, workspace_id):
        workspace = self.workspace(actor, workspace_id)
        members = [{"user_id": workspace["owner_id"], "role": "owner"}, *[record(row) for row in SourceWorkspaceMember.select().where(SourceWorkspaceMember.workspace_id == workspace_id)]]
        users = {row.id: row for row in User.select(User.id, User.email, User.nickname).where(User.id.in_([member["user_id"] for member in members]))}
        return [
            {**member, "email": users[member["user_id"]].email if member["user_id"] in users else "", "name": users[member["user_id"]].nickname if member["user_id"] in users else member["user_id"]}
            for member in members
        ]

    @connected
    def user_by_email(self, actor, workspace_id, email):
        workspace = self.workspace(actor, workspace_id, "manage")
        if not workspace["tenant_id"]:
            fail("WORKSPACE_TENANT_REQUIRED", "Выберите организацию пространства перед добавлением участников")
        members = UserTenant.select(UserTenant.user_id).where((UserTenant.tenant_id == workspace["tenant_id"]) & (UserTenant.status == "1"))
        user = User.get_or_none((User.email == email.strip().lower()) & User.id.in_(members) & (User.status == "1") & (User.is_active == "1"))
        if user is None:
            fail("INVALID_MEMBER", "Активный участник выбранной организации не найден")
        return user.id

    @connected
    def configure_search(self, actor, workspace_id, version, datasets):
        with self.database.atomic():
            workspace = self.workspace(actor, workspace_id, "edit")
            selection = [item for item in workspace["selected_documents"] if item["dataset_id"] in datasets]
            changed = (
                SourceWorkspace.update(dataset_ids=datasets, selected_documents=selection, version=version + 1)
                .where((SourceWorkspace.id == workspace_id) & (SourceWorkspace.version == version))
                .execute()
            )
            if not changed:
                fail("VERSION_CONFLICT", "Подборка изменилась; обновите её", 409)
            return self.workspace(actor, workspace_id)

    @connected
    def set_member(self, actor, workspace_id, user_id, role):
        workspace = self.workspace(actor, workspace_id, "manage")
        if user_id == workspace["owner_id"]:
            fail("INVALID_MEMBER", "Полномочия владельца нельзя изменить")
        if not workspace["tenant_id"]:
            fail("WORKSPACE_TENANT_REQUIRED", "Выберите организацию пространства перед добавлением участников")
        if not UserTenant.select().where((UserTenant.user_id == user_id) & (UserTenant.status == "1") & (UserTenant.tenant_id == workspace["tenant_id"])).exists():
            fail("INVALID_MEMBER", "Участник должен принадлежать вашей организации")
        with self.database.atomic():
            SourceWorkspaceMember.delete().where((SourceWorkspaceMember.workspace_id == workspace_id) & (SourceWorkspaceMember.user_id == user_id)).execute()
            SourceWorkspaceMember.create(workspace_id=workspace_id, user_id=user_id, role=role)
        return self.members(actor, workspace_id)

    @connected
    def organization(self, actor, workspace_id, tenant_id=None, version=None):
        workspace = self.workspace(actor, workspace_id, "manage")
        memberships = UserTenant.select(UserTenant.tenant_id).where((UserTenant.user_id == actor) & (UserTenant.status == "1"))
        choices = [{"id": row.id, "name": row.name} for row in Tenant.select(Tenant.id, Tenant.name).where(Tenant.id.in_(memberships))]
        if tenant_id is not None:
            if tenant_id not in {choice["id"] for choice in choices}:
                fail("PERMISSION_DENIED", "Выбранная организация недоступна", 403)
            if workspace["tenant_id"] and workspace["tenant_id"] != tenant_id:
                fail("WORKSPACE_TENANT_FIXED", "Организацию существующего пространства нельзя заменить", 409)
            updated = SourceWorkspace.update(tenant_id=tenant_id, version=version + 1).where((SourceWorkspace.id == workspace_id) & (SourceWorkspace.version == version)).execute()
            if not updated:
                fail("VERSION_CONFLICT", "Пространство изменилось; обновите настройки", 409)
            workspace = self.workspace(actor, workspace_id)
        return {"tenant_id": workspace["tenant_id"], "version": workspace["version"], "items": choices, "workspace": workspace}

    @connected
    def documents(self, workspace_id):
        fields = [field for name, field in Document._meta.fields.items() if name not in {"content", "prompt"}]
        return [record(row) for row in Document.select(*fields).where(Document.workspace_id == workspace_id).order_by(Document.updated_at.desc())]

    @connected
    def document(self, workspace_id, document_id, include_content=True):
        fields = list(Document._meta.fields.values()) if include_content else [field for name, field in Document._meta.fields.items() if name not in {"content", "prompt"}]
        row = Document.select(*fields).where((Document.id == document_id) & (Document.workspace_id == workspace_id)).first()
        if row is None:
            fail("DOCUMENT_NOT_FOUND", "Документ не найден", 404)
        return record(row)

    @connected
    def document_by_origin(self, workspace_id, origin_key):
        fields = [field for name, field in Document._meta.fields.items() if name not in {"content", "prompt"}]
        row = Document.select(*fields).where((Document.workspace_id == workspace_id) & (Document.origin_key == origin_key)).first()
        return record(row) if row else None

    @connected
    def source_documents(self, workspace_id, ids):
        fields = [field for name, field in Document._meta.fields.items() if name not in {"content", "prompt"}]
        return {row.id: record(row) for row in Document.select(*fields).where((Document.workspace_id == workspace_id) & Document.id.in_(ids))}

    @connected
    def source_revisions(self, ids, include_sections=False):
        fields = list(Revision._meta.fields.values()) if include_sections else [field for name, field in Revision._meta.fields.items() if name != "sections"]
        return {row.id: record(row) for row in Revision.select(*fields).where(Revision.id.in_(ids))}

    @connected
    def revision(self, document_id, revision_id, include_sections=True):
        fields = list(Revision._meta.fields.values()) if include_sections else [field for name, field in Revision._meta.fields.items() if name != "sections"]
        row = Revision.select(*fields).where((Revision.id == revision_id) & (Revision.document_id == document_id)).first()
        if row is None:
            fail("REVISION_NOT_FOUND", "Редакция не найдена", 404)
        return record(row)

    @connected
    def revisions(self, document_id):
        fields = [field for name, field in Revision._meta.fields.items() if name != "sections"]
        return [record(row) for row in Revision.select(*fields).where(Revision.document_id == document_id).order_by(Revision.number.desc())]

    @connected
    def dependencies(self, workspace_id, target_id=None):
        query = Dependency.select().where(Dependency.workspace_id == workspace_id)
        if target_id is not None:
            query = query.where(Dependency.target_id == target_id)
        return [record(row) for row in query.order_by(Dependency.id)]

    def _event(self, actor, document, kind, payload):
        Event.create(id=identifier(), workspace_id=document.workspace_id, document_id=document.id, actor_id=actor, kind=kind, payload=payload)

    def _append_revision(self, actor, row, sections, source_revision=None, inputs=None):
        previous_id = row.current_revision_id
        previous = Revision.get_by_id(previous_id) if previous_id else None
        revision = Revision.create(
            id=identifier(),
            document_id=row.id,
            number=previous.number + 1 if previous else 1,
            parent_id=previous_id,
            sections=sections,
            content_hash=digest(render(sections)),
            text_length=len(render(sections)),
            author_id=actor,
            source_revision=source_revision,
            source_metadata=row.source_ref if row.source_kind in {"indexed", "business"} else None,
        )
        row.content = render(sections)
        row.current_revision_id = revision.id
        row.updated_at = datetime.now(timezone.utc)
        row.version += 1
        row.save()
        inherited = [record(item) for item in RevisionInput.select().where(RevisionInput.revision_id == previous_id)] if previous_id else []
        by_dependency = {item["dependency_id"]: item for item in inherited}
        by_dependency.update({item["dependency_id"]: item for item in inputs or []})
        for item in by_dependency.values():
            RevisionInput.create(
                revision_id=revision.id,
                dependency_id=item["dependency_id"],
                source_revision_id=item["source_revision_id"],
                source_state_token=item.get("source_state_token", ""),
                policy=item["policy"],
            )
        self._event(actor, row, "revision_created", {"revision_id": revision.id, "parent_id": previous_id})
        return revision

    @connected
    def create_document(self, actor, workspace_id, values, sections):
        try:
            with self.database.atomic():
                workspace = self.workspace(actor, workspace_id, "edit")
                row = Document.create(
                    id=identifier(),
                    workspace_id=workspace_id,
                    owner_id=actor,
                    content="",
                    prompt="",
                    mode="all",
                    source_version=0,
                    responsible_id=actor,
                    approver_id=workspace["owner_id"],
                    provenance_state="resolved",
                    **values,
                )
                self._append_revision(actor, row, sections, (row.source_ref or {}).get("revision"))
                return record(row)
        except IntegrityError:
            if values.get("origin_key"):
                existing = Document.get_or_none((Document.workspace_id == workspace_id) & (Document.origin_key == values["origin_key"]))
                if existing:
                    return record(existing)
            raise

    @connected
    def edit_document(self, actor, workspace_id, document_id, version, sections=None, settings=None, *, source_revision=None, manual_inputs=None):
        with self.database.atomic():
            self.workspace(actor, workspace_id, "edit")
            rows = self._lock_documents([document_id, *[item["source_id"] for item in manual_inputs or []]])
            row = rows.get(document_id)
            if row is None or row.workspace_id != workspace_id:
                fail("DOCUMENT_NOT_FOUND", "Документ не найден", 404)
            assert_version(record(row), version)
            if sections is not None and source_revision is None:
                assert_editable(record(row))
            if settings:
                for key, value in settings.items():
                    setattr(row, key, value)
            manual = sections is not None and source_revision is None and row.change_policy == "approval"
            if manual:
                if manual_inputs is None:
                    fail("APPROVAL_REQUIRED", "Ручные правки этого документа необходимо представить на согласование", 409)
                dependencies = {item.id: record(item) for item in Dependency.select().where(Dependency.target_id == document_id)}
                assert_current_inputs(manual_inputs, {key: record(value) for key, value in rows.items()}, dependencies)
            appended = False
            if sections is not None:
                current = Revision.get_by_id(row.current_revision_id)
                evidence_changed = row.source_kind == "business" and source_revision is not None and current.source_revision != source_revision
                if (current.content_hash != digest(render(sections)) or evidence_changed) and not manual:
                    self._append_revision(actor, row, sections, source_revision)
                    appended = True
            if settings and not appended:
                row.version += 1
                row.updated_at = datetime.now(timezone.utc)
                row.save()
            if settings:
                self._event(actor, row, "settings_changed", settings)
            if manual and current.content_hash != digest(render(sections)):
                plan = manual_plan(current.sections, sections)
                existing = Change.select().where(
                    (Change.document_id == document_id) & (Change.origin == "manual") & (Change.author_id == actor) & (Change.document_version == row.version) & (Change.status == "ready")
                )
                if not any(item.plan.get("candidate_hash") == plan["candidate_hash"] and digest(item.inputs) == digest(input_manifest(manual_inputs)) for item in existing):
                    proposal = Change.create(
                        id=identifier(),
                        workspace_id=workspace_id,
                        document_id=document_id,
                        origin="manual",
                        author_id=actor,
                        base_revision_id=row.current_revision_id,
                        document_version=row.version,
                        candidate_sections=sections,
                        inputs=input_manifest(manual_inputs),
                        plan=plan,
                        verification={"verdict": "HUMAN_REQUIRED", "issues": []},
                    )
                    self._event(actor, row, "manual_change_proposed", {"change_id": proposal.id})
            return record(row)

    @connected
    def establish_baseline(self, actor, workspace_id, document_id, version, inputs):
        with self.database.atomic():
            self.workspace(actor, workspace_id, "manage")
            locked = self._lock_documents([document_id, *[item["source_id"] for item in inputs]])
            row = locked.get(document_id)
            if row is None or row.workspace_id != workspace_id:
                fail("DOCUMENT_NOT_FOUND", "Документ не найден", 404)
            assert_version(record(row), version)
            if row.provenance_state != "unresolved":
                return record(row)
            dependencies = {item.id: record(item) for item in Dependency.select().where(Dependency.target_id == document_id)}
            assert_current_inputs(inputs, {key: record(value) for key, value in locked.items()}, dependencies)
            row.provenance_state = "resolved"
            # Evidence changes are a new revision even when the document text is identical.
            revision = Revision.get_by_id(row.current_revision_id)
            self._append_revision(actor, row, revision.sections, inputs=inputs)
            self._event(actor, row, "baseline_established", {"historical_sources_recovered": False, "inputs": inputs})
            return record(row)

    @connected
    def _create_saved_document(self, actor, workspace_id, content, prompt, mode, source_version, sources):
        with self.database.atomic():
            workspace = self.workspace(actor, workspace_id, "edit")
            sections = split_sections(content)
            row = Document.create(
                id=identifier(),
                workspace_id=workspace_id,
                owner_id=actor,
                title=heading(sections[0])[:255] if heading(sections[0]) != "Введение" else "Черновик",
                content=content,
                prompt=prompt,
                mode=mode,
                source_version=source_version,
                sources=sources,
                responsible_id=actor,
                approver_id=workspace["owner_id"],
                provenance_state="unresolved" if sources else "none",
            )
            # Keep the published draft version starting at 1.
            row.version = 0
            self._append_revision(actor, row, sections)
            return row

    @connected
    def generated_draft(self, actor, workspace_id, content, prompt, mode, source_version, sources, registered):
        with self.database.atomic():
            self.workspace(actor, workspace_id, "edit")
            workspaces = SourceWorkspace.select().where(SourceWorkspace.id == workspace_id)
            if not isinstance(self.database, SqliteDatabase):
                workspaces = workspaces.for_update()
            workspace = workspaces.get()
            if workspace.version != source_version:
                fail("VERSION_CONFLICT", "Подборка изменилась перед сохранением результата", 409)
            locked = self._lock_documents([item["source_id"] for item in registered])
            if any(locked[item["source_id"]].current_revision_id != item["source_revision_id"] for item in registered):
                fail("SOURCE_CHANGED", "Источники изменились перед сохранением результата", 409)
            row = self._create_saved_document(actor, workspace_id, content, prompt, mode, source_version, sources)
            row.provenance_state = "resolved"
            row.save()
            for item in registered:
                dependency = Dependency.create(id=identifier(), workspace_id=workspace_id, source_id=item["source_id"], target_id=row.id, relation="reference", required=False, blocks_approval=False)
                RevisionInput.create(revision_id=row.current_revision_id, dependency_id=dependency.id, source_revision_id=item["source_revision_id"], policy=record(dependency))
            self._event(actor, row, "generation_sources_pinned", {"sources": registered})
            return record(row)

    @connected
    def approve_document(self, actor, workspace_id, document_id, version, inputs):
        with self.database.atomic():
            self._lock_workspace(workspace_id)
            self.workspace(actor, workspace_id, "approve")
            self.assert_no_cycle(workspace_id, document_id)
            self._guard_business(actor, inputs)
            locked = self._lock_documents([document_id, *[item["source_id"] for item in inputs]])
            row = locked.get(document_id)
            if row is None or row.workspace_id != workspace_id:
                fail("DOCUMENT_NOT_FOUND", "Документ не найден", 404)
            dependencies = {item.id: record(item) for item in Dependency.select().where(Dependency.target_id == document_id)}
            assert_approval(actor, record(row), version, inputs, {key: record(value) for key, value in locked.items()}, dependencies)
            row.approved_revision_id = row.current_revision_id
            row.version += 1
            row.save()
            self._event(actor, row, "revision_approved", {"revision_id": row.current_revision_id})
            return record(row)

    @connected
    def save_dependency(self, actor, workspace_id, target_id, target_version, values, dependency_id=None):
        with self.database.atomic():
            self._lock_workspace(workspace_id)
            self.workspace(actor, workspace_id, "edit")
            documents = self._lock_documents([target_id, values["source_id"]])
            if set(documents) != {target_id, values["source_id"]} or any(row.workspace_id != workspace_id for row in documents.values()):
                fail("DOCUMENT_NOT_FOUND", "Документ связи не найден", 404)
            target = documents[target_id]
            if target.version != target_version:
                fail("VERSION_CONFLICT", "Документ или его связи изменились", 409)
            links = [record(item) for item in Dependency.select().where(Dependency.workspace_id == workspace_id) if item.id != dependency_id]
            titles = dict(Document.select(Document.id, Document.title).where(Document.workspace_id == workspace_id).tuples())
            assert_acyclic_link(links, {**values, "target_id": target_id}, titles)
            if dependency_id:
                row = Dependency.get_or_none((Dependency.id == dependency_id) & (Dependency.target_id == target_id) & (Dependency.workspace_id == workspace_id))
                if row is None or row.source_id != values["source_id"]:
                    fail("DEPENDENCY_NOT_FOUND", "Связь не найдена", 404)
                for key, value in values.items():
                    setattr(row, key, value)
                row.version += 1
                row.reviewed_revision_id = None
                row.reviewed_target_revision_id = None
                row.reviewed_source_state_token = None
                row.save()
            else:
                if Dependency.select().where((Dependency.source_id == values["source_id"]) & (Dependency.target_id == target_id)).exists():
                    fail("DEPENDENCY_EXISTS", "Эти документы уже связаны", 409)
                if Dependency.select().where(Dependency.target_id == target_id).count() >= MAX_DEPENDENCIES:
                    fail("TOO_MANY_DEPENDENCIES", "У документа может быть до 100 непосредственных оснований")
                row = Dependency.create(id=identifier(), workspace_id=workspace_id, target_id=target_id, **values)
            target.version += 1
            target.save()
            self._event(actor, target, "dependency_changed", {"dependency_id": row.id, "version": row.version})
            return record(row)

    @connected
    def upsert_impacts(self, workspace_id, target_id, base_id, inputs):
        with self.database.atomic():
            locked = self._lock_documents([target_id, *[item["source_id"] for item in inputs]], include_content=False)
            target = locked.get(target_id)
            if not target or target.workspace_id != workspace_id or target.current_revision_id != base_id:
                fail("SOURCE_CHANGED", "Документ изменился во время проверки оснований", 409)
            dependencies = {row.id: record(row) for row in Dependency.select().where((Dependency.workspace_id == workspace_id) & (Dependency.target_id == target_id))}
            assert_current_inputs(inputs, {key: record(row) for key, row in locked.items() if row.workspace_id == workspace_id}, dependencies)
            # Confirmations can update reviewed fields without changing the link version.
            inputs = [{**item, "policy": dependencies[item["dependency_id"]]} for item in inputs]
            active_ids = []
            pending = [item for item in inputs if not input_reviewed(item["policy"], item["source_revision_id"], base_id, item.get("source_state_token", ""))]
            clauses = [
                (Impact.dependency_id == item["dependency_id"])
                & (Impact.source_revision_id == item["source_revision_id"])
                & (Impact.target_revision_id == base_id)
                & (Impact.dependency_version == item["policy"]["version"])
                & (Impact.source_state_token == item.get("source_state_token", ""))
                for item in pending
            ]
            existing = {(row.dependency_id, row.source_state_token): row for row in Impact.select().where(reduce(or_, clauses))} if clauses else {}
            for item in pending:
                impact = existing.get((item["dependency_id"], item.get("source_state_token", "")))
                if impact is None:
                    impact, _ = Impact.get_or_create(
                        dependency_id=item["dependency_id"],
                        source_revision_id=item["source_revision_id"],
                        target_revision_id=base_id,
                        dependency_version=item["policy"]["version"],
                        source_state_token=item.get("source_state_token", ""),
                        defaults={"id": identifier(), "previous_revision_id": item["policy"]["reviewed_revision_id"]},
                    )
                active_ids.append(impact.id)
            if inputs:
                Impact.update(state="superseded").where(Impact.dependency_id.in_([item["dependency_id"] for item in inputs]) & (Impact.state == "pending") & ~Impact.id.in_(active_ids)).execute()
            return [record(row) for row in Impact.select().where(Impact.id.in_(active_ids)).order_by(Impact.id)]

    @connected
    def remove_dependency(self, actor, workspace_id, target_id, dependency_id, version):
        with self.database.atomic():
            self._lock_workspace(workspace_id)
            self.workspace(actor, workspace_id, "edit")
            target = self._lock_documents([target_id]).get(target_id)
            if not target or target.workspace_id != workspace_id:
                fail("DOCUMENT_NOT_FOUND", "Документ не найден", 404)
            if target.version != version:
                fail("VERSION_CONFLICT", "Документ или его связи изменились", 409)
            dependency = Dependency.get_or_none((Dependency.id == dependency_id) & (Dependency.target_id == target_id) & (Dependency.workspace_id == workspace_id))
            if not dependency:
                fail("DEPENDENCY_NOT_FOUND", "Связь не найдена", 404)
            self._event(actor, target, "dependency_removed", record(dependency))
            Impact.update(state="superseded").where((Impact.dependency_id == dependency_id) & (Impact.state == "pending")).execute()
            dependency.delete_instance()
            target.version += 1
            target.save()

    @connected
    def enqueue(self, actor, workspace_id, document_id, payload, retry=False):
        request_key = digest({"actor": actor, "workspace": workspace_id, "document": document_id, "payload": payload})
        row, _ = Job.get_or_create(request_key=request_key, defaults={"id": identifier(), "actor_id": actor, "workspace_id": workspace_id, "document_id": document_id, "payload": payload})
        if retry:
            result = Change.get_or_none(Change.id == row.result_id) if row.result_id else None
            if row.status in {"failed", "cancelled"} or (row.status == "done" and result and result.status in {"blocked", "rejected"}):
                Job.update(status="queued", stage="queued", checkpoint=None, result_id=None, error=None, attempts=0).where((Job.id == row.id) & (Job.status == row.status)).execute()
                row = Job.get_by_id(row.id)
        return record(row)

    @connected
    def jobs(self, workspace_id, document_id):
        fields = [field for name, field in Job._meta.fields.items() if name not in {"payload", "checkpoint", "lease_token"}]
        return [record(row) for row in Job.select(*fields).where((Job.workspace_id == workspace_id) & (Job.document_id == document_id)).order_by(Job.created_at.desc()).limit(20)]

    @connected
    def changes(self, workspace_id, document_id):
        rows = list(Change.select().where((Change.workspace_id == workspace_id) & (Change.document_id == document_id)).order_by(Change.created_at.desc()).limit(50))
        decisions = list(Decision.select().where(Decision.change_id.in_([row.id for row in rows])).order_by(Decision.created_at)) if rows else []
        user_ids = {item.actor_id for item in decisions} | {item.author_id for item in rows if item.author_id}
        users = {row.id: row.nickname for row in User.select(User.id, User.nickname).where(User.id.in_(user_ids))} if user_ids else {}
        by_change = {}
        for decision in decisions:
            by_change.setdefault(decision.change_id, []).append({**record(decision), "actor_name": users.get(decision.actor_id, "Пользователь удалён")})
        return [
            {
                **{key: value for key, value in record(row).items() if key != "candidate_sections" or row.origin == "manual"},
                "author_name": users.get(row.author_id, "Пользователь удалён"),
                "decisions": by_change.get(row.id, []),
            }
            for row in rows
        ]

    @connected
    def change(self, workspace_id, document_id, change_id):
        row = Change.get_or_none((Change.id == change_id) & (Change.workspace_id == workspace_id) & (Change.document_id == document_id))
        if row is None:
            fail("CHANGE_NOT_FOUND", "Предложение не найдено", 404)
        return record(row)

    @connected
    def job(self, workspace_id, document_id, job_id):
        row = Job.get_or_none((Job.id == job_id) & (Job.workspace_id == workspace_id) & (Job.document_id == document_id))
        if row is None:
            fail("JOB_NOT_FOUND", "Задание не найдено", 404)
        return record(row)

    @connected
    def cancel(self, actor, workspace_id, document_id, job_id):
        self.workspace(actor, workspace_id, "edit")
        self.job(workspace_id, document_id, job_id)
        Job.update(status="cancelled", stage="cancelled", lease_token=None, lease_until=0).where((Job.id == job_id) & Job.status.in_(["queued", "running"])).execute()
        return self.job(workspace_id, document_id, job_id)

    @connected
    def claim(self):
        now = int(time.time())
        for job in Job.select().where((Job.status == "queued") | ((Job.status == "running") & (Job.lease_until < now))).order_by(Job.created_at).limit(20):
            token = identifier()
            if job.attempts >= 3:
                Job.update(status="failed", error={"code": "JOB_EXHAUSTED", "message": "Задание не завершилось после восстановления"}).where(
                    (Job.id == job.id) & (Job.attempts == job.attempts) & ((Job.status == "queued") | ((Job.status == "running") & (Job.lease_until < now)))
                ).execute()
                continue
            changed = (
                Job.update(status="running", stage="preparing", lease_token=token, lease_until=now + 60, attempts=job.attempts + 1)
                .where((Job.id == job.id) & (Job.attempts == job.attempts) & ((Job.status == "queued") | ((Job.status == "running") & (Job.lease_until < now))))
                .execute()
            )
            if changed:
                return record(Job.get_by_id(job.id))
        return None

    @connected
    def check_lease(self, job_id, token):
        if not Job.select(Job.id).where((Job.id == job_id) & (Job.status == "running") & (Job.lease_token == token) & (Job.lease_until > int(time.time()))).exists():
            fail("JOB_LEASE_LOST", "Задание отменено или передано другому исполнителю", 409)

    @connected
    def lease(self, job_id, token, stage=None):
        values = {"lease_until": int(time.time()) + 60}
        if stage:
            values["stage"] = stage
        if not Job.update(**values).where((Job.id == job_id) & (Job.status == "running") & (Job.lease_token == token) & (Job.lease_until > int(time.time()))).execute():
            fail("JOB_LEASE_LOST", "Задание отменено или передано другому исполнителю", 409)

    @connected
    def finish_job(self, job_id, token, change=None, error=None):
        with self.database.atomic():
            pending = Job.get_by_id(job_id)
            self._lock_workspace(pending.workspace_id)
            self.lease(job_id, token)
            row = Job.get_by_id(job_id)
            if change:
                self.assert_no_cycle(row.workspace_id, row.document_id)
                self._guard_business(row.actor_id, change["inputs"])
                locked = self._lock_documents([row.document_id, *[item["source_id"] for item in change["inputs"]]])
                target = locked[row.document_id]
                assert_version(record(target), change["document_version"], code="STALE_CHANGE")
                dependencies = {item.id: record(item) for item in Dependency.select().where(Dependency.target_id == row.document_id)}
                assert_current_inputs(change["inputs"], {key: record(value) for key, value in locked.items()}, dependencies)
                result = Change.create(id=identifier(), workspace_id=row.workspace_id, document_id=row.document_id, author_id=row.actor_id, **change)
                row.result_id = result.id
            row.status = "failed" if error else "done"
            row.stage = row.status
            row.error = error
            row.lease_token = None
            row.lease_until = 0
            row.save()

    @connected
    def checkpoint(self, job_id, token, plan):
        with self.database.atomic():
            self.lease(job_id, token)
            Job.update(checkpoint=plan).where((Job.id == job_id) & (Job.lease_token == token) & (Job.status == "running")).execute()

    @connected
    def finalize(self, actor, workspace_id, document_id, change_id, sections, inputs, comment, action):
        with self.database.atomic():
            self._lock_workspace(workspace_id)
            self.workspace(actor, workspace_id, "approve")
            query = Change.select().where((Change.id == change_id) & (Change.workspace_id == workspace_id) & (Change.document_id == document_id))
            if not isinstance(self.database, SqliteDatabase):
                query = query.for_update()
            change = query.first()
            if change is None:
                fail("CHANGE_NOT_FOUND", "Предложение не найдено", 404)
            if action == "accept" and change.status != "applied":
                self._guard_business(actor, inputs)
            locked = self._lock_documents([document_id, *[item["source_id"] for item in inputs]])
            target = locked[document_id]
            assert_approver(actor, record(target))
            if change.status == "applied" and action == "accept":
                return record(target)
            dependencies = {item.id: record(item) for item in Dependency.select().where(Dependency.target_id == document_id)}
            resolved_inputs = confirmation_inputs(actor, record(target), record(change), {key: record(value) for key, value in locked.items()}, dependencies, action)
            if change.origin == "manual" and sections != manual_candidate(record(change)):
                fail("INVALID_MANUAL_CHANGE", "Текст ручной правки изменился", 409)
            if action == "accept":
                if render(sections) != target.content:
                    revision = self._append_revision(actor, target, sections, inputs=change.inputs if change.origin == "manual" else resolved_inputs)
                    change.applied_revision_id = revision.id
                else:
                    change.applied_revision_id = target.current_revision_id
                    target.version += 1
                    target.save()
                for item in resolved_inputs:
                    Dependency.update(
                        reviewed_revision_id=item["source_revision_id"], reviewed_target_revision_id=target.current_revision_id, reviewed_source_state_token=item.get("source_state_token", "")
                    ).where(Dependency.id == item["dependency_id"]).execute()
                    Impact.update(state="resolved", explanation=change.plan["summary"]).where(
                        (Impact.dependency_id == item["dependency_id"])
                        & (Impact.source_revision_id == item["source_revision_id"])
                        & (Impact.target_revision_id == change.base_revision_id)
                        & (Impact.dependency_version == item["policy"]["version"])
                        & (Impact.source_state_token == item.get("source_state_token", ""))
                    ).execute()
                change.status = "applied"
            else:
                change.status = "rejected"
            change.save()
            Decision.create(id=identifier(), change_id=change.id, actor_id=actor, action=action, comment=comment)
            self._event(actor, target, "change_" + change.status, {"change_id": change.id, "revision_id": change.applied_revision_id, "comment": comment})
            return record(target)

    @connected
    def automatic_targets(self):
        return list(Dependency.select(Dependency.workspace_id, Dependency.target_id).where(Dependency.maintenance == "auto").distinct().tuples())

    @connected
    def provenance_documents(self, workspace_id, document_id):
        """Historical inputs remain access-controlled even after a link is edited."""
        visited, pending = set(), {document_id} if isinstance(document_id, str) else set(document_id)
        documents = []
        while pending:
            batch = pending - visited
            if not batch:
                break
            visited.update(batch)
            documents.extend(
                record(row) for row in Document.select(Document.id, Document.source_kind, Document.source_ref, Document.sources).where((Document.workspace_id == workspace_id) & Document.id.in_(batch))
            )
            dependencies = Dependency.select().where((Dependency.workspace_id == workspace_id) & Dependency.target_id.in_(batch))
            pending = {row.source_id for row in dependencies}
            revision_ids = Revision.select(Revision.id).where(Revision.document_id.in_(batch))
            source_revisions = RevisionInput.select(RevisionInput.source_revision_id).where(RevisionInput.revision_id.in_(revision_ids))
            pending.update(row.document_id for row in Revision.select(Revision.document_id).where(Revision.id.in_(source_revisions)))
            for change in Change.select(Change.inputs).where((Change.workspace_id == workspace_id) & Change.document_id.in_(batch)):
                pending.update(item["source_id"] for item in change.inputs)
        return documents
