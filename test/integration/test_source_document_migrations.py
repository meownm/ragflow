"""Real DB upgrade/recovery and source-change acceptance, on disposable DBs only."""

from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace

from peewee import CharField, IntegerField, PostgresqlDatabase, TextField
import pytest

from api.db.db_models import (
    DataBaseModel,
    JSONField,
    LongTextField,
    SourceWorkspace,
    SourceWorkspaceDraft,
    SourceDocumentRevision,
    SourceDocumentEvent,
    User,
)
from api.source_workbench.errors import SourceWorkspaceError
from api.source_workbench.linked_application import LinkedDocuments
from api.source_workbench.linked_domain import split_sections
from api.source_workbench.linked_migrations import LINKED_MODELS, migrate_linked_documents
from api.source_workbench.linked_storage import LinkedDocumentRepository
from test.unit_test.api.apps.source_workbench.test_linked_documents import Sources, Models, scenario, prepare


class PreviousDraft(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    workspace_id = CharField(max_length=32)
    owner_id = CharField(max_length=32)
    content = LongTextField()
    prompt = TextField()
    mode = CharField(max_length=16)
    source_version = IntegerField()
    sources = JSONField(default=list)
    version = IntegerField(default=1)
    created_at = TextField(default="2026-10-01T10:00:00+00:00")
    updated_at = TextField(default="2026-10-01T10:00:00+00:00")

    class Meta:
        table_name = "source_workspace_draft"


@pytest.fixture
def real_database():
    if os.getenv("RAGFLOW_LINKED_TEST_DISPOSABLE") != "1":
        pytest.skip("requires dedicated disposable linked_test databases")
    port = os.getenv("RAGFLOW_LINKED_TEST_POSTGRES_PORT")
    if not port:
        pytest.fail("Missing disposable PostgreSQL port")
    database = PostgresqlDatabase(
        "linked_test",
        host="127.0.0.1",
        port=int(port),
        user="postgres",
        password="linked_test_only",
        field_types={"LONGTEXT": "TEXT"},
    )
    models = [SourceWorkspace, SourceWorkspaceDraft, User, *LINKED_MODELS]
    with database.bind_ctx([*models, PreviousDraft]):
        database.connect()
        database.drop_tables(models, safe=True)
        database.create_tables([SourceWorkspace, PreviousDraft, User])
        User.create(id="author", nickname="Автор", email="author@example.test")
        SourceWorkspace.create(id="workspace", owner_id="author", title="Проект", dataset_ids=["dataset"])
        database.execute_sql("ALTER TABLE source_workspace DROP COLUMN tenant_id")
        yield database
        database.drop_tables(models, safe=True)
        database.close()


def test_real_upgrade_preserves_text_and_recovers_after_backfill_failure(real_database, monkeypatch):
    body = "# Старое ТЗ\r\nСохранить 30 дней.\r\n\r\n| Поле | Тип |\r\n| --- | --- |\r\n| Код | Строка |\r\n"
    body += "Сохранённая строка\r\n" * 4000
    PreviousDraft.create(
        id="legacy",
        workspace_id="workspace",
        owner_id="author",
        content=body,
        prompt="Исходная задача",
        mode="all",
        source_version=9,
        sources=[{"dataset_id": "dataset", "document_id": "source", "revision": "missing-history"}],
    )
    assert PreviousDraft.get_by_id("legacy").content == body
    assert "current_revision_id" not in {column.name for column in real_database.get_columns("source_workspace_draft")}
    original = SourceDocumentEvent.create

    def interrupted(**_kwargs):
        raise RuntimeError("injected backfill interruption")

    monkeypatch.setattr(SourceDocumentEvent, "create", interrupted)
    with pytest.raises(RuntimeError, match="injected"):
        migrate_linked_documents()
    assert SourceWorkspaceDraft.get_by_id("legacy").current_revision_id is None
    assert SourceDocumentRevision.select().count() == 0
    monkeypatch.setattr(SourceDocumentEvent, "create", original)
    migrate_linked_documents()
    first = SourceWorkspaceDraft.get_by_id("legacy")
    migrate_linked_documents()
    recovered = SourceWorkspaceDraft.get_by_id("legacy")
    assert recovered.content == body and recovered.prompt == "Исходная задача"
    assert recovered.current_revision_id == first.current_revision_id and recovered.version == 1
    assert recovered.provenance_state == "unresolved"
    assert SourceDocumentRevision.select().count() == 1
    assert "".join(section["text"] for section in SourceDocumentRevision.get().sections) == body


@pytest.mark.asyncio
async def test_real_database_source_change_and_concurrent_edits(real_database):
    migrate_linked_documents()
    repository = LinkedDocumentRepository()
    linked = SimpleNamespace(repo=repository, sources=Sources(), provider=Models())
    linked.service = LinkedDocuments(repository, linked.sources, linked.provider)
    _, target = await scenario(linked)
    linked.sources.marker = "new-requirement"
    linked.sources.content = "# Хранение\nХранить данные 90 дней.\n"
    target = await linked.service.refresh("author", "workspace", target["id"])
    change = await prepare(linked, target)
    target = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    assert "90 дней" in target["content"] and "Авторский текст не менять" in target["content"]
    assert len(target["revisions"]) == 2
    version = target["version"]

    def edit(suffix):
        try:
            repository.edit_document("author", "workspace", target["id"], version, split_sections(target["content"] + suffix, target["sections"]))
            return "saved"
        except SourceWorkspaceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(edit, ["Первая правка", "Вторая правка"]))
    assert sorted(outcomes) == ["VERSION_CONFLICT", "saved"]
    assert SourceDocumentRevision.select().where(SourceDocumentRevision.document_id == target["id"]).count() == 3


@pytest.mark.asyncio
async def test_existing_change_table_upgrade_and_manual_approval_on_postgres(real_database):
    from api.db.db_models import SourceDocumentChange

    migrate_linked_documents()
    original = SourceDocumentChange.create(
        id="existing-change", workspace_id="workspace", document_id="existing-document", base_revision_id="existing-revision", document_version=1, plan={"summary": "Old candidate"}
    )
    for name in ("origin", "author_id", "candidate_sections"):
        real_database.execute_sql("ALTER TABLE source_document_change DROP COLUMN " + name)
    migrate_linked_documents()
    migrate_linked_documents()
    recovered = SourceDocumentChange.get_by_id(original.id)
    assert recovered.origin == "model" and recovered.author_id is None and recovered.plan == {"summary": "Old candidate"}
    service = LinkedDocuments(LinkedDocumentRepository(), Sources(), Models())
    target = await service.create("author", "workspace", {"title": "Ручное ТЗ", "change_policy": "approval", "content": "# Текст\n30 дней\n"})
    candidate = "# Текст\n90 дней\n"
    submitted = await service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "content": candidate})
    assert submitted["content"] == target["content"] and len(submitted["revisions"]) == 1
    proposal = submitted["changes"][0]
    assert proposal["origin"] == "manual"
    result = await service.decide("author", "workspace", target["id"], proposal["id"], {"action": "accept", "comment": "Согласовано"})
    assert result["content"] == candidate and len(result["revisions"]) == 2
    repeated = await service.decide("author", "workspace", target["id"], proposal["id"], {"action": "accept"})
    assert len(repeated["revisions"]) == 2


@pytest.mark.asyncio
async def test_real_database_concurrent_observers_preserve_current_pending_impact(real_database):
    from api.db.db_models import SourceDocumentImpact

    migrate_linked_documents()
    repository = LinkedDocumentRepository()
    linked = SimpleNamespace(repo=repository, sources=Sources(), provider=Models())
    linked.service = LinkedDocuments(repository, linked.sources, linked.provider)
    source, target = await scenario(linked)
    stale, _ = await linked.service.inputs("author", "workspace", target["id"], include_sections=False)
    linked.sources.marker, linked.sources.content = "changed", "# Updated source\nChanged requirement"
    await linked.service.refresh("author", "workspace", source["id"])
    current, _ = await linked.service.inputs("author", "workspace", target["id"], include_sections=False)

    def observe(inputs):
        try:
            return repository.upsert_impacts("workspace", target["id"], target["current_revision_id"], inputs)
        except SourceWorkspaceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(observe, [current, stale, current]))
    assert results[1] == "SOURCE_CHANGED"
    assert results[0][0]["id"] == results[2][0]["id"]
    assert SourceDocumentImpact.get_by_id(results[0][0]["id"]).state == "pending"
