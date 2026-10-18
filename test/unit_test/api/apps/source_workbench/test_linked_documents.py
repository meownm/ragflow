"""End-to-end application contracts with disposable persistence and model responses."""

import json
from types import SimpleNamespace

from peewee import SqliteDatabase
import pytest

from api.db.db_models import SourceWorkspace, SourceWorkspaceDraft, SourceDocumentRevision, SourceDocumentDependency, SourceDocumentJob, SourceWorkspaceMember, User, UserTenant, Tenant
from api.source_workbench.errors import SourceWorkspaceError
from api.source_workbench.linked_application import LinkedDocuments
from api.source_workbench.linked_domain import apply_plan, render, split_sections
from api.source_workbench.linked_migrations import LINKED_MODELS, migrate_linked_documents
from api.source_workbench.linked_storage import LinkedDocumentRepository


pytestmark = pytest.mark.p1


@pytest.mark.parametrize("operation", ["replace", "add"])
@pytest.mark.parametrize("ending", ["", "\n"])
def test_generated_sections_keep_markdown_boundaries(operation, ending):
    base = split_sections("# Требование\n30 дней\n# Ручной раздел\nНе менять.\n")
    original = [dict(section) for section in base]
    change = {"op": operation, "text": "# Требование\n90 дней" + ending, "source_ids": ["source"]}
    change.update({"section_id": base[0]["id"]} if operation == "replace" else {"after_id": base[1]["id"]})
    if operation == "add":
        base[1]["text"] = base[1]["text"].rstrip("\n")
        original = [dict(section) for section in base]
    plan = {
        "outcome": "changes_required",
        "summary": "Обновить срок",
        "operations": [change],
        "source_results": [{"dependency_id": "source", "disposition": "changed", "explanation": "Срок изменился"}],
    }
    sections = apply_plan(base, plan, [{"dependency_id": "source", "policy": {"target_scope": []}}])
    assert len(split_sections(render(sections))) == len(sections)
    assert "# Ручной раздел\nНе менять." in render(sections)
    assert base == original


class Sources:
    marker = "first"
    content = "# Хранение\nХранить данные 30 дней.\n"
    allowed = True

    def validate_datasets(self, actor, datasets):
        if not self.allowed:
            raise SourceWorkspaceError("DATASET_UNAVAILABLE", "Доступ отозван", 403)

    def document_revisions(self, references):
        return [{**reference, "revision": self.marker} for reference in references]

    def describe_chunks(self, references, datasets):
        return [{**reference, "title": "Бизнес-требования", "source_url": "https://example.test/requirements"} for reference in references]

    def load_document(self, reference):
        return self.content


class Models:
    verdict = "PASS"
    outcome = "changes_required"
    calls = 0
    context_tokens = 131072

    async def open_processor(self, actor):
        return self

    def count_tokens(self, text):
        return max(1, len(text) // 3)

    async def stream(self, system, payload, output_tokens):
        self.calls += 1
        if "verdict" in system:
            yield json.dumps({"verdict": self.verdict, "issues": [] if self.verdict == "PASS" else ["Не сохранено требование"]})
            return
        changed = self.outcome == "changes_required"
        dependency_id = payload["inputs"][0]["dependency_id"]
        yield json.dumps(
            {
                "outcome": self.outcome,
                "summary": "Источник требует хранить данные 90 дней",
                "operations": [{"op": "replace", "section_id": payload["sections"][0]["id"], "text": "# Хранение\nХранить данные 90 дней.\n", "source_ids": [dependency_id]}] if changed else [],
                "source_results": [{"dependency_id": item["dependency_id"], "disposition": "changed" if changed else "unchanged", "explanation": "Срок изменился"} for item in payload["inputs"]],
            },
            ensure_ascii=False,
        )


@pytest.fixture
def linked(tmp_path):
    database = SqliteDatabase(tmp_path / "linked.sqlite", pragmas={"journal_mode": "wal", "busy_timeout": 10000})
    models = [SourceWorkspace, SourceWorkspaceDraft, User, UserTenant, Tenant, *LINKED_MODELS]
    with database.bind_ctx(models):
        database.create_tables(models)
        SourceWorkspace.create(id="workspace", owner_id="author", title="Проект", tenant_id="org", dataset_ids=["dataset"])
        User.create(id="author", nickname="Автор", email="author@example.test")
        User.create(id="reviewer", nickname="Согласующий", email="reviewer@example.test")
        Tenant.create(id="org", name="Организация", llm_id="", embd_id="", asr_id="", img2txt_id="", rerank_id="", parser_ids="")
        for actor in ("author", "reviewer"):
            UserTenant.create(id=actor, user_id=actor, tenant_id="org", role="normal", invited_by="author")
        sources, provider = Sources(), Models()
        repository = LinkedDocumentRepository()
        service = LinkedDocuments(repository, sources, provider)
        yield SimpleNamespace(service=service, repo=repository, sources=sources, provider=provider, database=database)
        if not database.is_closed():
            database.close()


async def scenario(linked):
    source = await linked.service.import_source("author", "workspace", {"dataset_id": "dataset", "document_id": "source"})
    target = await linked.service.create(
        "author", "workspace", {"title": "ТЗ", "document_type": "technical_specification", "content": "# Хранение\nХранить данные 30 дней.\n# Ручной раздел\nАвторский текст не менять.\n"}
    )
    target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "relation": "requires", "expected_version": target["version"]})
    return source, target


async def prepare(linked, target):
    job = await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"]})
    claimed = linked.repo.claim()
    assert claimed["id"] == job["id"]
    await linked.service.execute(claimed)
    return (await linked.service.get("author", "workspace", target["id"]))["changes"][0]


@pytest.mark.asyncio
async def test_source_change_prepares_and_confirms_specification_without_losing_manual_text(linked):
    source, target = await scenario(linked)
    linked.sources.marker = "second"
    linked.sources.content = "# Хранение\nХранить данные 90 дней.\n"
    target = await linked.service.refresh("author", "workspace", target["id"])
    source = await linked.service.get("author", "workspace", source["id"])
    assert len(source["revisions"]) == 2
    assert target["content"].startswith("# Хранение\nХранить данные 30 дней.")
    proposal = await prepare(linked, target)
    assert proposal["status"] == "ready"
    unchanged = target["sections"][1]
    target = await linked.service.decide("author", "workspace", target["id"], proposal["id"], {"action": "accept"})
    assert "90 дней" in target["content"]
    assert target["sections"][1] == unchanged
    assert all(item["state"] == "resolved" for item in target["impacts"])
    old = await linked.service.history("author", "workspace", target["id"], proposal["base_revision_id"])
    assert "30 дней" in render(old["sections"])
    repeated = await linked.service.decide("author", "workspace", target["id"], proposal["id"], {"action": "accept"})
    assert len(repeated["revisions"]) == len(target["revisions"])
    assert linked.provider.calls == 2


@pytest.mark.asyncio
async def test_concurrent_manual_edit_invalidates_prepared_change(linked):
    _, target = await scenario(linked)
    proposal = await prepare(linked, target)
    await linked.service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "content": target["content"] + "Новое ручное требование\n"})
    with pytest.raises(SourceWorkspaceError, match="изменились"):
        await linked.service.decide("author", "workspace", target["id"], proposal["id"], {"action": "accept"})
    assert "Новое ручное" in (await linked.service.get("author", "workspace", target["id"]))["content"]


@pytest.mark.asyncio
async def test_changed_external_source_and_revoked_access_fail_closed(linked):
    _, target = await scenario(linked)
    proposal = await prepare(linked, target)
    linked.sources.marker = "third"
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.decide("author", "workspace", target["id"], proposal["id"], {"action": "accept"})
    assert error.value.code == "SOURCE_CHANGED"
    linked.sources.allowed = False
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.get("author", "workspace", target["id"])
    assert error.value.status == 403


@pytest.mark.asyncio
async def test_failed_verification_and_cancel_never_publish(linked):
    _, target = await scenario(linked)
    linked.provider.verdict = "FAIL"
    proposal = await prepare(linked, target)
    assert proposal["status"] == "blocked"
    with pytest.raises(SourceWorkspaceError):
        await linked.service.decide("author", "workspace", target["id"], proposal["id"], {"action": "accept"})
    assert len((await linked.service.get("author", "workspace", target["id"]))["revisions"]) == 1
    job = await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"], "prompt": "Другая задача"})
    claimed = linked.repo.claim()
    await linked.service.cancel("author", "workspace", target["id"], job["id"])
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.execute(claimed)
    assert error.value.code == "JOB_LEASE_LOST"


@pytest.mark.asyncio
async def test_unchanged_reindex_does_not_create_content_revision(linked):
    source, _ = await scenario(linked)
    linked.sources.marker = "reindex"
    source = await linked.service.refresh("author", "workspace", source["id"])
    assert len(source["revisions"]) == 1
    assert source["source_ref"]["revision"] == "reindex"


@pytest.mark.asyncio
async def test_locked_document_scope_and_foreign_workspace_are_rejected(linked):
    source, target = await scenario(linked)
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.edit("author", "workspace", source["id"], {"expected_version": source["version"], "content": "Изменение"})
    assert error.value.code == "DOCUMENT_LOCKED"
    with pytest.raises(SourceWorkspaceError):
        await linked.service.get("intruder", "workspace", target["id"])
    dependency = target["dependencies"][0]
    target = await linked.service.dependency(
        "author", "workspace", target["id"], {**dependency, "expected_version": target["version"], "target_scope": [target["sections"][1]["id"]]}, dependency["id"]
    )
    await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"]})
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.execute(linked.repo.claim())
    assert error.value.code == "SCOPE_VIOLATION"


def test_migration_is_idempotent_and_does_not_invent_source_history(linked):
    row = SourceWorkspaceDraft.create(
        id="old-draft",
        workspace_id="workspace",
        owner_id="author",
        content="# ТЗ\nПрежний текст\n",
        prompt="Задача",
        mode="all",
        source_version=3,
        sources=[{"dataset_id": "dataset", "document_id": "source", "revision": "lost"}],
    )
    migrate_linked_documents()
    first = SourceWorkspaceDraft.get_by_id(row.id)
    migrate_linked_documents()
    assert SourceWorkspaceDraft.get_by_id(row.id).current_revision_id == first.current_revision_id
    assert first.content == row.content and first.prompt == row.prompt and first.version == row.version
    assert first.provenance_state == "unresolved"
    assert SourceDocumentRevision.select().where(SourceDocumentRevision.document_id == row.id).count() == 1
    assert SourceDocumentDependency.select().count() == 0
    assert SourceDocumentJob.select().count() == 0


def test_migration_rejects_unsupported_database_before_ddl(linked, monkeypatch):
    monkeypatch.setattr(SourceWorkspaceDraft._meta, "database", object())
    with pytest.raises(RuntimeError, match="require PostgreSQL"):
        migrate_linked_documents()


def test_markdown_splitting_is_lossless_and_ignores_headings_in_code():
    markdown = "Введение\r\n# Требования\r\n```md\r\n# Не раздел\r\n```\r\nТекст\r\n# Итог\r\n"
    sections = split_sections(markdown)
    assert render(sections) == markdown and len(sections) == 3
    changed = split_sections(markdown.replace("Текст", "Новый текст"), sections)
    assert [item["id"] for item in changed] == [item["id"] for item in sections]


@pytest.mark.asyncio
async def test_no_impact_requires_confirmation_and_rejection_does_not_resolve_dependency(linked):
    _, target = await scenario(linked)
    linked.provider.outcome = "no_impact"
    change = await prepare(linked, target)
    assert (await linked.service.get("author", "workspace", target["id"]))["impacts"]
    await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "reject"})
    target = await linked.service.get("author", "workspace", target["id"])
    assert target["impacts"][0]["state"] == "pending"
    # Explicit retry is allowed; the automatic observer must not retry a rejected candidate.
    automatic = await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"], "retry": False})
    assert automatic["status"] == "done"
    change = await prepare(linked, target)
    target = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    assert target["impacts"] == [] and len(target["revisions"]) == 1
    target = await linked.service.approve("author", "workspace", target["id"], {"expected_version": target["version"]})
    assert target["approved_revision_id"] == target["current_revision_id"]


@pytest.mark.asyncio
async def test_approved_source_waits_for_explicit_approval(linked):
    source = await linked.service.create("author", "workspace", {"title": "Требования", "document_type": "business_requirements", "content": "# Источник\nПравило\n"})
    target = await linked.service.create("author", "workspace", {"title": "ТЗ"})
    target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "refresh_on": "approved", "expected_version": target["version"]})
    assert target["waiting"]
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"]})
    assert error.value.code == "SOURCE_NOT_APPROVED"
    await linked.service.approve("author", "workspace", source["id"], {"expected_version": source["version"]})
    target = await linked.service.get("author", "workspace", target["id"])
    assert not target["waiting"]


@pytest.mark.asyncio
async def test_assigned_reviewer_can_confirm_but_reader_cannot_edit_through_legacy_api(linked, monkeypatch):
    from api.source_workbench.adapters import SourceWorkspaceRepository
    import inspect

    _, target = await scenario(linked)
    SourceWorkspaceMember.create(workspace_id="workspace", user_id="reviewer", role="reviewer")
    target = await linked.service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "approver_id": "reviewer"})
    change = await prepare(linked, target)
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    assert error.value.code == "PERMISSION_DENIED"
    result = await linked.service.decide("reviewer", "workspace", target["id"], change["id"], {"action": "accept"})
    assert "90 дней" in result["content"]
    SourceWorkspaceMember.update(role="reader").where(SourceWorkspaceMember.user_id == "reviewer").execute()
    with pytest.raises(SourceWorkspaceError):
        await linked.service.edit("reviewer", "workspace", target["id"], {"expected_version": result["version"], "content": "Нельзя"})
    legacy = linked.repo._create_saved_document("author", "workspace", "Текст", "Задача", "all", 1, [])
    SourceWorkspaceDraft.update(owner_id="reviewer").where(SourceWorkspaceDraft.id == legacy.id).execute()
    import asyncio

    monkeypatch.setattr(SourceWorkspaceRepository, "get_draft", inspect.unwrap(SourceWorkspaceRepository.get_draft))
    monkeypatch.setattr("api.source_workbench.linked_runtime.build_linked_documents", lambda: linked.service)

    def update():
        with linked.database.connection_context():
            return SourceWorkspaceRepository.update_draft("reviewer", "workspace", legacy.id, legacy.version, "Обход")

    with pytest.raises(SourceWorkspaceError) as error:
        await asyncio.to_thread(update)
    assert error.value.status == 403


@pytest.mark.asyncio
async def test_explicit_baseline_does_not_claim_recovered_history(linked):
    row = SourceWorkspaceDraft.create(
        id="legacy",
        workspace_id="workspace",
        owner_id="author",
        content="ТЗ",
        prompt="Задача",
        mode="all",
        source_version=2,
        sources=[{"dataset_id": "dataset", "document_id": "source", "revision": "lost"}],
    )
    migrate_linked_documents()
    current = await linked.service.get("author", "workspace", row.id)
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.baseline("author", "workspace", row.id, {"expected_version": current["version"]})
    assert error.value.code == "BASELINE_CONFIRMATION_REQUIRED"
    result = await linked.service.baseline("author", "workspace", row.id, {"expected_version": current["version"], "confirm_current_sources": True})
    assert result["provenance_state"] == "resolved" and result["content"] == "ТЗ"
    assert len(result["revisions"]) == 2
    assert result["dependencies"][0]["required"] is False


@pytest.mark.asyncio
async def test_expired_worker_reuses_checkpoint_and_cancelled_worker_cannot_publish(linked):
    _, target = await scenario(linked)
    await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"]})
    original = linked.repo.claim()
    original_finish = linked.repo.finish_job

    def lost_before_save(*_args, **_kwargs):
        raise SourceWorkspaceError("JOB_LEASE_LOST", "Lost", 409)

    linked.repo.finish_job = lost_before_save
    with pytest.raises(SourceWorkspaceError):
        await linked.service.execute(original)
    assert linked.provider.calls == 2
    SourceDocumentJob.update(lease_until=0).where(SourceDocumentJob.id == original["id"]).execute()
    recovered = linked.repo.claim()
    assert recovered["checkpoint"] and recovered["lease_token"] != original["lease_token"]
    linked.repo.finish_job = original_finish
    await linked.service.execute(recovered)
    assert linked.provider.calls == 3  # Only verification, never another candidate generation.
    with pytest.raises(SourceWorkspaceError):
        linked.repo.finish_job(original["id"], original["lease_token"], error={"code": "old"})


@pytest.mark.asyncio
async def test_manual_change_after_verification_requires_new_dependency_review(linked):
    _, target = await scenario(linked)
    change = await prepare(linked, target)
    target = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    target = await linked.service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "content": target["content"].replace("90 дней", "1 день")})
    assert target["impacts"]
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.approve("author", "workspace", target["id"], {"expected_version": target["version"]})
    assert error.value.code == "UNRESOLVED_DEPENDENCY"


@pytest.mark.asyncio
async def test_selected_independent_changes_are_reverified_and_leave_deferred_sources_open(linked):
    class TwoSources(Models):
        async def stream(self, system, payload, output_tokens):
            self.calls += 1
            if "verdict" in system:
                yield json.dumps({"verdict": "PASS", "issues": []})
                return
            operations, results = [], []
            for item in payload["inputs"]:
                index = 1 if item["title"] == "Логирование" else 0
                operations.append(
                    {
                        "op": "replace",
                        "section_id": payload["sections"][index]["id"],
                        "text": "# Логирование\nДобавить журнал\n" if index else "# Хранение\n90 дней\n",
                        "source_ids": [item["dependency_id"]],
                    }
                )
                results.append({"dependency_id": item["dependency_id"], "disposition": "changed", "explanation": "Требуется правка"})
            yield json.dumps({"outcome": "changes_required", "summary": "Два независимых изменения", "operations": operations, "source_results": results})

    linked.provider = TwoSources()
    linked.service.models = linked.provider
    _, target = await scenario(linked)
    linked.sources.marker = "updated"
    linked.sources.content = "# Хранение\n90 дней\n"
    target = await linked.service.refresh("author", "workspace", target["id"])
    second = await linked.service.create("author", "workspace", {"title": "Логирование", "content": "# Требования\nДобавить журнал\n"})
    target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": second["id"], "expected_version": target["version"]})
    change = await prepare(linked, target)
    await linked.service.prepare_selection("author", "workspace", target["id"], change["id"], {"selected_sections": [target["sections"][0]["id"]]})
    assert linked.repo.document("workspace", target["id"])["content"] == target["content"]
    await linked.service.execute(linked.repo.claim())
    verified = (await linked.service.get("author", "workspace", target["id"]))["changes"][0]
    assert linked.provider.calls == 3  # Generator + whole-plan verifier + subset verifier.
    target = await linked.service.decide("author", "workspace", target["id"], verified["id"], {"action": "accept"})
    assert "90 дней" in target["content"] and "Авторский текст не менять" in target["content"]
    assert len(target["impacts"]) == 1
    assert next(item for item in target["dependencies"] if item["source_id"] == second["id"])["reviewed_revision_id"] is None


@pytest.mark.asyncio
async def test_tenant_revocation_and_mandatory_link_permissions(linked):
    source, target = await scenario(linked)
    members = await linked.service.set_member("author", "workspace", {"email": "reviewer@example.test", "role": "editor"})
    assert any(member["name"] == "Согласующий" for member in members)
    dependency = target["dependencies"][0]
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.dependency("reviewer", "workspace", target["id"], {**dependency, "required": False, "blocks_approval": False, "expected_version": target["version"]}, dependency["id"])
    assert error.value.status == 403
    with pytest.raises(SourceWorkspaceError):
        await linked.service.remove_dependency("reviewer", "workspace", target["id"], dependency["id"], {"expected_version": target["version"]})
    UserTenant.update(status="0").where(UserTenant.user_id == "reviewer").execute()
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.get("reviewer", "workspace", source["id"])
    assert error.value.status == 403


@pytest.mark.asyncio
async def test_removing_link_keeps_proposal_provenance_access_controlled(linked):
    _, target = await scenario(linked)
    await prepare(linked, target)
    dependency = target["dependencies"][0]
    target = await linked.service.remove_dependency("author", "workspace", target["id"], dependency["id"], {"expected_version": target["version"]})
    assert not target["dependencies"]
    linked.sources.allowed = False
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.get("author", "workspace", target["id"])
    assert error.value.code == "DATASET_UNAVAILABLE"


@pytest.mark.asyncio
async def test_legacy_private_workspace_requires_explicit_organization_before_sharing(linked):
    SourceWorkspace.update(tenant_id=None).where(SourceWorkspace.id == "workspace").execute()
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.set_member("author", "workspace", {"email": "reviewer@example.test", "role": "reader"})
    assert error.value.code == "WORKSPACE_TENANT_REQUIRED"
    context = await linked.service.organization("author", "workspace")
    assert context["items"] == [{"id": "org", "name": "Организация"}]
    result = await linked.service.organization("author", "workspace", {"tenant_id": "org", "expected_version": context["version"]})
    assert result["workspace"]["version"] == context["version"] + 1
    members = await linked.service.set_member("author", "workspace", {"email": "reviewer@example.test", "role": "reader"})
    assert len(members) == 2


@pytest.mark.asyncio
async def test_new_generated_draft_pins_real_snapshots_without_migration_warning(linked):
    row = await linked.service.save_generated("author", "workspace", "# Результат\nТекст", "Задача", "all", 1, [{"dataset_id": "dataset", "document_id": "source", "revision": "first"}])
    saved = await linked.service.get("author", "workspace", row["id"])
    assert saved["provenance_state"] == "resolved" and len(saved["revisions"]) == 1
    assert saved["source_ref"] is None
    assert saved["dependencies"][0]["required"] is False
    source = await linked.service.get("author", "workspace", saved["dependencies"][0]["source_id"])
    snapshot = await linked.service.history("author", "workspace", source["id"], source["current_revision_id"])
    assert snapshot["source_metadata"]["revision"] == "first" and snapshot["source_metadata"]["retrieved_at"]


@pytest.mark.asyncio
async def test_refresh_detects_changed_text_even_when_index_marker_does_not_change(linked):
    source, _ = await scenario(linked)
    linked.sources.content = "# Хранение\nНовые сведения при прежнем техническом маркере\n"
    updated = await linked.service.refresh("author", "workspace", source["id"])
    assert len(updated["revisions"]) == 2 and updated["source_ref"]["revision"] == "first"


@pytest.mark.asyncio
async def test_optional_unapproved_source_does_not_block_work_on_available_requirements(linked):
    _, target = await scenario(linked)
    source = await linked.service.create("author", "workspace", {"title": "Будущая справка", "content": "# Справка\nКонтекст\n"})
    target = await linked.service.dependency(
        "author", "workspace", target["id"], {"source_id": source["id"], "expected_version": target["version"], "required": False, "blocks_approval": False, "refresh_on": "approved"}
    )
    change = await prepare(linked, target)
    assert change["status"] == "ready" and len(change["inputs"]) == 1


def test_nested_markdown_headings_keep_identities_across_unrelated_changes():
    source = "# А\n## Требования\nПервое\n# Б\n## Требования\nВторое\n"
    original = split_sections(source)
    updated = split_sections(source.replace("Второе", "Уточнённое второе"), original)
    assert [section["id"] for section in original] == [section["id"] for section in updated]


@pytest.mark.asyncio
async def test_loading_twenty_sources_uses_batched_queries(linked, monkeypatch):
    target = await linked.service.create("author", "workspace", {"title": "ТЗ"})
    for number in range(20):
        source = await linked.service.create("author", "workspace", {"title": f"Источник {number}", "content": f"# Правило\nТребование {number}\n"})
        target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "expected_version": target["version"]})
    calls = []
    original = linked.database.execute_sql

    def counted(sql, *args, **kwargs):
        calls.append(sql)
        return original(sql, *args, **kwargs)

    monkeypatch.setattr(linked.database, "execute_sql", counted)
    inputs, waiting = await linked.service.inputs("author", "workspace", target["id"])
    assert len(inputs) == 20 and not waiting
    assert len(calls) <= 12
    print(f"20 sources: {len(calls)} SQL statements")


@pytest.mark.asyncio
async def test_removed_source_freshness_does_not_block_current_inputs_but_historical_acl_remains(linked):
    _, target = await scenario(linked)
    await prepare(linked, target)
    dependency = target["dependencies"][0]
    target = await linked.service.remove_dependency("author", "workspace", target["id"], dependency["id"], {"expected_version": target["version"]})
    current = await linked.service.create("author", "workspace", {"title": "Новое основание", "content": "# Требования\n90 дней\n"})
    target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": current["id"], "expected_version": target["version"]})
    linked.sources.marker = "removed-source-changed"
    change = await prepare(linked, target)
    assert change["status"] == "ready"
    target = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    assert "90 дней" in target["content"]
    linked.sources.allowed = False
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.get("author", "workspace", target["id"])
    assert error.value.code == "DATASET_UNAVAILABLE"


@pytest.mark.asyncio
async def test_only_automatic_impacts_trigger_jobs_with_manual_sources_retained_as_context(linked):
    linked.provider.outcome = "no_impact"
    automatic = await linked.service.create("author", "workspace", {"title": "A auto", "content": "A1"})
    manual = await linked.service.create("author", "workspace", {"title": "B manual", "content": "B1"})
    target = await linked.service.create("author", "workspace", {"title": "ТЗ", "content": "Текст ТЗ"})
    for source, mode in [(automatic, "auto"), (manual, "manual")]:
        target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "maintenance": mode, "expected_version": target["version"]})
    change = await prepare(linked, target)
    target = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    await linked.service.edit("author", "workspace", manual["id"], {"expected_version": manual["version"], "content": "B2"})
    assert await linked.service.inspect_automatic("author", "workspace", target["id"]) is None
    assert linked.provider.calls == 2 and SourceDocumentJob.select().count() == 1
    await linked.service.edit("author", "workspace", automatic["id"], {"expected_version": automatic["version"], "content": "A2"})
    job = await linked.service.inspect_automatic("author", "workspace", target["id"])
    assert job["status"] == "queued"
    claimed = linked.repo.claim()
    assert {item["source_id"] for item in claimed["payload"]["inputs"]} == {automatic["id"], manual["id"]}
    await linked.service.execute(claimed)
    unchanged = await linked.service.get("author", "workspace", target["id"])
    assert unchanged["content"] == "Текст ТЗ" and len(unchanged["revisions"]) == 1


@pytest.mark.asyncio
async def test_cancellation_returns_terminal_stage_and_fences_old_executor(linked):
    _, target = await scenario(linked)
    job = await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"]})
    claimed = linked.repo.claim()
    linked.repo.lease(job["id"], claimed["lease_token"], "planning")
    cancelled = await linked.service.cancel("author", "workspace", target["id"], job["id"])
    assert cancelled["status"] == cancelled["stage"] == "cancelled"
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.execute(claimed)
    assert error.value.code == "JOB_LEASE_LOST"
    # Normalize terminal projections from previously saved jobs as well.
    SourceDocumentJob.update(stage="planning").where(SourceDocumentJob.id == job["id"]).execute()
    target = await linked.service.get("author", "workspace", target["id"])
    assert target["jobs"][0]["stage"] == "cancelled" and len(target["revisions"]) == 1


@pytest.mark.asyncio
async def test_manual_approval_reject_accept_permissions_idempotency_and_pending_requirements(linked):
    await linked.service.set_member("author", "workspace", {"email": "reviewer@example.test", "role": "reviewer"})
    User.create(id="editor", nickname="Редактор", email="editor@example.test")
    UserTenant.create(id="editor", user_id="editor", tenant_id="org", role="normal", invited_by="author")
    await linked.service.set_member("author", "workspace", {"email": "editor@example.test", "role": "editor"})
    source = await linked.service.create("author", "workspace", {"title": "Основание", "content": "# Срок\n90 дней\n"})
    target = await linked.service.create("author", "workspace", {"title": "ТЗ", "change_policy": "approval", "content": "# Хранение\n30 дней\n# Ручной раздел\nНе менять\n"})
    target = await linked.service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "approver_id": "reviewer"})
    target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "expected_version": target["version"]})
    original, version = target["content"], target["version"]
    with pytest.raises(SourceWorkspaceError) as permission:
        await linked.service.edit("editor", "workspace", target["id"], {"expected_version": version, "change_policy": "working", "content": "Обход согласования"})
    assert permission.value.status == 403
    candidate = "# Ручной раздел\nНе менять\n# Хранение\n90 дней\n# Новый раздел\nРучное дополнение\n"
    submitted = await linked.service.edit("editor", "workspace", target["id"], {"expected_version": version, "content": candidate})
    change = submitted["changes"][0]
    assert submitted["content"] == original and submitted["version"] == version and len(submitted["revisions"]) == 1
    assert change["origin"] == "manual" and change["author_id"] == "editor"
    assert render(change["candidate_sections"]) == candidate
    repeated = await linked.service.edit("editor", "workspace", target["id"], {"expected_version": version, "content": candidate})
    assert len(repeated["changes"]) == 1
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    assert error.value.status == 403
    with pytest.raises(SourceWorkspaceError):
        await linked.service.prepare_selection("reviewer", "workspace", target["id"], change["id"], {"selected_sections": [target["sections"][0]["id"]]})
    rejected = await linked.service.decide("reviewer", "workspace", target["id"], change["id"], {"action": "reject", "comment": "Уточнить"})
    assert rejected["content"] == original and len(rejected["revisions"]) == 1
    submitted = await linked.service.edit("editor", "workspace", target["id"], {"expected_version": version, "content": candidate})
    change = submitted["changes"][0]
    accepted = await linked.service.decide("reviewer", "workspace", target["id"], change["id"], {"action": "accept", "comment": "Согласовано"})
    assert accepted["content"] == candidate and len(accepted["revisions"]) == 2
    assert accepted["impacts"] and accepted["dependencies"][0]["reviewed_revision_id"] is None
    from api.db.db_models import SourceDocumentInput

    evidence = SourceDocumentInput.get(SourceDocumentInput.revision_id == accepted["current_revision_id"])
    assert evidence.source_revision_id == source["current_revision_id"]
    assert accepted["changes"][0]["decisions"][0]["actor_id"] == "reviewer"
    repeated = await linked.service.decide("reviewer", "workspace", target["id"], change["id"], {"action": "accept"})
    assert len(repeated["revisions"]) == 2 and len(repeated["changes"][0]["decisions"]) == 1
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.approve("reviewer", "workspace", target["id"], {"expected_version": accepted["version"]})
    assert error.value.code == "UNRESOLVED_DEPENDENCY" and linked.provider.calls == 0


@pytest.mark.asyncio
async def test_manual_proposal_rejects_stale_source_target_policy_and_permission(linked):
    source = await linked.service.create("author", "workspace", {"title": "Основание", "content": "A1"})
    target = await linked.service.create("author", "workspace", {"title": "ТЗ", "change_policy": "approval", "content": "Original"})
    target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "expected_version": target["version"]})
    submitted = await linked.service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "content": "New"})
    change = submitted["changes"][0]
    await linked.service.edit("author", "workspace", source["id"], {"expected_version": source["version"], "content": "A2"})
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    assert error.value.code == "STALE_CHANGE"
    resubmitted = await linked.service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "content": "New"})
    assert len(resubmitted["changes"]) == 2
    change = resubmitted["changes"][0]
    target = await linked.service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "change_policy": "locked"})
    with pytest.raises(SourceWorkspaceError):
        await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    assert target["content"] == "Original"


@pytest.mark.asyncio
async def test_legacy_draft_edit_shares_manual_approval_and_ignores_search_selection(linked, monkeypatch):
    import inspect
    from api.source_workbench.adapters import SourceWorkspaceRepository, RAGFlowSourceGateway, KnowledgebaseService
    from api.source_workbench.service import SourceWorkspaceService
    from api.source_workbench import linked_runtime

    monkeypatch.setattr(linked_runtime, "build_linked_documents", lambda: linked.service)
    saved = await linked.service.save_generated("author", "workspace", "Saved", "Task", "all", 1, [{"dataset_id": "dataset", "document_id": "source", "revision": "first"}])
    workspace = await linked.service.access("author", "workspace")
    await linked.service.configure_search("author", "workspace", {"expected_version": workspace["version"], "dataset_ids": []})
    validations = []

    def by_ids(ids):
        validations.append(ids)
        return [SimpleNamespace(embd_id="embedding", tenant_id="org") for _ in ids]

    monkeypatch.setattr(KnowledgebaseService, "accessible", lambda *_args: True)
    monkeypatch.setattr(KnowledgebaseService, "get_by_ids", by_ids)

    def scoped(method):
        def invoke(*args):
            with linked.database.connection_context():
                return method(*args)

        return invoke

    repository = SimpleNamespace(
        get=linked.repo.workspace,
        list_drafts=scoped(inspect.unwrap(SourceWorkspaceRepository.list_drafts)),
        get_draft=scoped(inspect.unwrap(SourceWorkspaceRepository.get_draft)),
        update_draft=SourceWorkspaceRepository.update_draft,
    )
    service = SourceWorkspaceService(repository, RAGFlowSourceGateway())
    assert len(await service.list_drafts("author", "workspace")) == 1
    assert (await service.get_draft("author", "workspace", saved["id"]))["content"] == "Saved"
    working = await service.update_draft("author", "workspace", saved["id"], {"expected_version": saved["version"], "content": "Edited"})
    target = await linked.service.edit("author", "workspace", saved["id"], {"expected_version": working["version"], "change_policy": "approval"})
    pending = await service.update_draft("author", "workspace", saved["id"], {"expected_version": target["version"], "content": "Needs consent"})
    assert pending["content"] == "Edited" and pending["pending_change_id"]
    assert validations and all(ids == ["dataset"] for ids in validations)
    target = await linked.service.decide("author", "workspace", saved["id"], pending["pending_change_id"], {"action": "accept"})
    assert target["content"] == "Needs consent"
    monkeypatch.setattr(KnowledgebaseService, "accessible", lambda *_args: False)
    for read in (lambda: service.list_drafts("author", "workspace"), lambda: service.get_draft("author", "workspace", saved["id"])):
        with pytest.raises(SourceWorkspaceError) as denied:
            await read()
        assert denied.value.status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["source", "target", "dependency"])
async def test_stale_observer_cannot_supersede_current_impact(linked, changed):
    from api.db.db_models import SourceDocumentImpact

    source, target = await scenario(linked)
    old_inputs, _ = await linked.service.inputs("author", "workspace", target["id"], include_sections=False)
    old_base = target["current_revision_id"]
    if changed == "source":
        linked.sources.marker, linked.sources.content = "second", "# Updated source\nNew requirement"
        await linked.service.refresh("author", "workspace", source["id"])
    elif changed == "target":
        await linked.service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "content": "# Updated target\nManual edit"})
    else:
        dependency = target["dependencies"][0]
        await linked.service.dependency("author", "workspace", target["id"], {**dependency, "expected_version": target["version"], "relation": "constrains"}, dependency["id"])
    current = await linked.service.get("author", "workspace", target["id"])
    impact = current["impacts"][0]
    with pytest.raises(SourceWorkspaceError) as error:
        linked.repo.upsert_impacts("workspace", target["id"], old_base, old_inputs)
    assert error.value.code == "SOURCE_CHANGED"
    assert SourceDocumentImpact.get_by_id(impact["id"]).state == "pending"
    assert (await linked.service.get("author", "workspace", target["id"]))["impacts"][0]["id"] == impact["id"]


@pytest.mark.asyncio
async def test_observer_uses_current_review_state_after_confirmation(linked):
    _, target = await scenario(linked)
    inputs, _ = await linked.service.inputs("author", "workspace", target["id"], include_sections=False)
    linked.provider.outcome = "no_impact"
    change = await prepare(linked, target)
    current = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    assert current["current_revision_id"] == target["current_revision_id"]
    assert linked.repo.upsert_impacts("workspace", target["id"], target["current_revision_id"], inputs) == []
    from api.db.db_models import SourceDocumentImpact

    assert SourceDocumentImpact.get().state == "resolved"


@pytest.mark.asyncio
async def test_manual_impact_does_not_trigger_automatic_preparation(linked):
    auto = await linked.service.create("author", "workspace", {"title": "Auto source", "content": "Auto input"})
    manual = await linked.service.create("author", "workspace", {"title": "Manual source", "content": "Manual input"})
    target = await linked.service.create("author", "workspace", {"title": "Target", "content": "Current text"})
    for source, maintenance in ((auto, "auto"), (manual, "manual")):
        target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "maintenance": maintenance, "expected_version": target["version"]})
    for dependency in target["dependencies"]:
        source = auto if dependency["source_id"] == auto["id"] else manual
        SourceDocumentDependency.update(reviewed_revision_id=source["current_revision_id"], reviewed_target_revision_id=target["current_revision_id"]).where(
            SourceDocumentDependency.id == dependency["id"]
        ).execute()
    await linked.service.edit("author", "workspace", manual["id"], {"expected_version": manual["version"], "content": "Changed manual input"})
    assert await linked.service.inspect_automatic("author", "workspace", target["id"]) is None
    assert SourceDocumentJob.select().count() == 0
    await linked.service.edit("author", "workspace", auto["id"], {"expected_version": auto["version"], "content": "Changed automatic input"})
    job = await linked.service.inspect_automatic("author", "workspace", target["id"])
    assert job["status"] == "queued"
    payload = SourceDocumentJob.get_by_id(job["id"]).payload
    assert {item["source_id"] for item in payload["inputs"]} == {auto["id"], manual["id"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["cancel", "transfer", "expired"])
async def test_worker_closes_stalled_model_on_cancellation_or_lease_loss(linked, loss):
    import asyncio
    import threading
    from api.source_workbench.linked_worker import run_job

    _, target = await scenario(linked)
    entered, closed = threading.Event(), threading.Event()

    async def stalled(*args):
        try:
            entered.set()
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            closed.set()

    linked.provider.stream = stalled
    job = await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"]})
    claimed = linked.repo.claim()
    task = asyncio.create_task(asyncio.to_thread(run_job, linked.service, claimed))
    assert await asyncio.to_thread(entered.wait, 5)
    if loss == "cancel":
        await linked.service.cancel("author", "workspace", target["id"], job["id"])
    elif loss == "transfer":
        SourceDocumentJob.update(lease_token="other-worker").where(SourceDocumentJob.id == job["id"]).execute()
    else:
        SourceDocumentJob.update(lease_until=0).where(SourceDocumentJob.id == job["id"]).execute()
    await asyncio.wait_for(task, timeout=4)
    assert closed.is_set()
    assert not (await linked.service.get("author", "workspace", target["id"]))["changes"]
