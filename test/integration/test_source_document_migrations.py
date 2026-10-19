"""Real DB upgrade/recovery and source-change acceptance, on disposable DBs only."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import os
from types import SimpleNamespace

from peewee import CharField, IntegerField, TextField
from playhouse.migrate import PostgresqlMigrator
import pytest

from api.db.db_models import (
    DataBaseModel,
    RetryingPooledPostgresqlDatabase,
    JSONField,
    LongTextField,
    SourceWorkspace,
    SourceWorkspaceDraft,
    SourceDocumentRevision,
    SourceDocumentEvent,
    User,
    _migrate_user_external_credential_expiry,
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
    database = RetryingPooledPostgresqlDatabase(
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
        database.close_all()


def test_eva_expiry_upgrade_is_repeatable_and_preserves_credentials(real_database):
    real_database.execute_sql("CREATE TABLE user_external_credential (id VARCHAR(32) PRIMARY KEY, encrypted_secret TEXT NOT NULL, credential_version INTEGER NOT NULL)")
    try:
        real_database.execute_sql("INSERT INTO user_external_credential VALUES (%s, %s, %s)", ("existing", "opaque-encrypted-secret", 7))
        migrator = PostgresqlMigrator(real_database)
        _migrate_user_external_credential_expiry(migrator)
        _migrate_user_external_credential_expiry(migrator)
        columns = {column.name: column for column in real_database.get_columns("user_external_credential")}
        assert columns["expires_at"].null and columns["expiry_checked_at"].null
        assert columns["expires_at"].data_type == columns["expiry_checked_at"].data_type == "bigint"
        row = real_database.execute_sql("SELECT encrypted_secret, credential_version, expires_at, expiry_checked_at FROM user_external_credential WHERE id = %s", ("existing",)).fetchone()
        assert row == ("opaque-encrypted-secret", 7, None, None)
        expiry, checked = 1_900_000_000_000, 1_800_000_000_000
        real_database.execute_sql("UPDATE user_external_credential SET expires_at = %s, expiry_checked_at = %s WHERE id = %s", (expiry, checked, "existing"))
        assert real_database.execute_sql("SELECT expires_at, expiry_checked_at FROM user_external_credential WHERE id = %s", ("existing",)).fetchone() == (expiry, checked)
    finally:
        real_database.execute_sql("DROP TABLE user_external_credential")


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


def test_state_token_upgrade_recovers_after_index_interruption(real_database, monkeypatch):
    from api.db.db_models import SourceDocumentDependency, SourceDocumentImpact, SourceDocumentInput

    migrate_linked_documents()
    row = SourceWorkspaceDraft.create(id="preserved", workspace_id="workspace", owner_id="author", content="Исходный текст", prompt="", mode="all", source_version=1)
    for index in real_database.get_indexes("source_document_impact"):
        if index.unique and "source_state_token" in index.columns:
            real_database.execute_sql('DROP INDEX "' + index.name + '"')
    real_database.execute_sql("CREATE UNIQUE INDEX old_impact ON source_document_impact (dependency_id, source_revision_id, target_revision_id, dependency_version)")
    for model, names in (
        (SourceDocumentDependency, ("reviewed_source_state_token",)),
        (SourceDocumentInput, ("source_state_token",)),
        (SourceDocumentImpact, ("source_state_token",)),
        (SourceWorkspaceDraft, ("required_for_readiness", "implemented_revision_id", "implementation_event_id")),
    ):
        for name in names:
            real_database.execute_sql(f'ALTER TABLE "{model._meta.table_name}" DROP COLUMN "{name}"')
    original = real_database.execute_sql

    def interrupted(sql, params=None, **kwargs):
        if sql.startswith('DROP INDEX "old_impact"'):
            raise RuntimeError("injected index migration interruption")
        return original(sql, params, **kwargs)

    monkeypatch.setattr(real_database, "execute_sql", interrupted)
    with pytest.raises(RuntimeError, match="injected index"):
        migrate_linked_documents()
    monkeypatch.setattr(real_database, "execute_sql", original)
    migrate_linked_documents()
    migrate_linked_documents()
    recovered = SourceWorkspaceDraft.get_by_id(row.id)
    assert recovered.content == row.content and not recovered.required_for_readiness
    assert recovered.implemented_revision_id is None and recovered.implementation_event_id is None
    indexes = real_database.get_indexes("source_document_impact")
    assert not any(index.name == "old_impact" for index in indexes)
    assert any(index.unique and len(index.columns) == 5 for index in indexes)


@pytest.mark.asyncio
async def test_concurrent_graph_changes_cannot_create_cycle(real_database):
    from threading import Barrier
    from api.db.db_models import SourceDocumentDependency

    migrate_linked_documents()
    repository = LinkedDocumentRepository()
    service = LinkedDocuments(repository, Sources(), Models())
    a = await service.create("author", "workspace", {"title": "А"})
    b = await service.create("author", "workspace", {"title": "Б"})
    barrier = Barrier(2)

    def connect(pair):
        source, target = pair
        barrier.wait(timeout=10)
        try:
            repository.save_dependency(
                "author",
                "workspace",
                target["id"],
                target["version"],
                {"source_id": source["id"], "relation": "requires", "required": True, "blocks_approval": True, "source_scope": [], "target_scope": [], "refresh_on": "saved", "maintenance": "auto"},
            )
            return "saved"
        except SourceWorkspaceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(connect, [(a, b), (b, a)]))
    assert sorted(results) == ["DEPENDENCY_CYCLE", "saved"]
    assert SourceDocumentDependency.select().count() == 1


@pytest.mark.asyncio
async def test_overview_reads_one_snapshot_during_graph_change(real_database, monkeypatch):
    from threading import Event, get_ident

    migrate_linked_documents()
    repository = LinkedDocumentRepository()
    service = LinkedDocuments(repository, Sources(), Models())
    a = await service.create("author", "workspace", {"title": "А"})
    read, written = Event(), Event()
    reader_thread = get_ident()
    original = real_database.execute_sql

    def between_reads(sql, params=None, **kwargs):
        cursor = original(sql, params, **kwargs)
        if get_ident() == reader_thread and 'FROM "source_workspace_draft"' in sql and not read.is_set():
            read.set()
            assert written.wait(10), "Concurrent graph writer did not finish"
        return cursor

    def writer():
        assert read.wait(10)
        try:
            b = repository.create_document("author", "workspace", {"title": "Б"}, split_sections("Новый документ"))
            repository.save_dependency(
                "author",
                "workspace",
                b["id"],
                b["version"],
                {"source_id": a["id"], "relation": "requires", "required": True, "blocks_approval": True, "source_scope": [], "target_scope": [], "refresh_on": "saved", "maintenance": "manual"},
            )
        finally:
            written.set()

    monkeypatch.setattr(real_database, "execute_sql", between_reads)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(writer)
        documents, links, *_ = repository.graph_data("workspace")
        future.result(timeout=10)
    assert len(documents) == 1 and not links
    assert len(repository.graph_data("workspace")[0]) == 2


def test_native_agreement_guard_waits_for_owner_transaction(real_database):
    from threading import Event
    from time import monotonic, sleep
    from api.db.db_models import BusinessDocument, BusinessDocumentEvent, BusinessDocumentRevision
    from test.unit_test.api.apps.source_workbench.test_linked_business_sources import business, seed

    migrate_linked_documents()
    linked = SimpleNamespace(repo=LinkedDocumentRepository(), database=real_database)
    linked.service = LinkedDocuments(linked.repo, Sources(), Models())
    native_models = [BusinessDocument, BusinessDocumentEvent, BusinessDocumentRevision]
    try:
        with business(linked) as gateway:
            seed()
            old = gateway.read_many("author", ["native"])["native"]
            started = Event()

            def validate():
                with real_database.connection_context(), real_database.atomic():
                    started.set()
                    try:
                        gateway.validate("author", [old], lock=True, fresh=True)
                        return "accepted"
                    except SourceWorkspaceError as error:
                        return error.code

            with ThreadPoolExecutor(max_workers=1) as executor:
                with real_database.atomic():
                    BusinessDocument.select().where(BusinessDocument.id == "native").for_update().get()
                    future = executor.submit(validate)
                    assert started.wait(10)
                    deadline = monotonic() + 10
                    while True:
                        waiting = real_database.execute_sql("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND pid<>pg_backend_pid()").fetchone()[0]
                        if waiting:
                            break
                        assert monotonic() < deadline, "Native source lock was not acquired on caller connection"
                        sleep(0.02)
                    seed(sequence=2)
                assert future.result(timeout=10) == "SOURCE_CHANGED"
    finally:
        with real_database.bind_ctx(native_models):
            real_database.drop_tables(native_models, safe=True)


@pytest.mark.asyncio
async def test_worker_restart_and_inflight_cancel_preserve_postgres_checkpoint(real_database, monkeypatch):
    from threading import Event
    from api.db.db_models import SourceDocumentJob, SourceDocumentChange
    from api.source_workbench.linked_worker import run_job

    migrate_linked_documents()
    linked = SimpleNamespace(repo=LinkedDocumentRepository(), sources=Sources(), provider=Models())
    linked.service = LinkedDocuments(linked.repo, linked.sources, linked.provider)
    _, target = await scenario(linked)
    await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"]})
    original = linked.repo.claim()
    finish = linked.repo.finish_job

    def lost_before_save(*_args, **_kwargs):
        raise SourceWorkspaceError("JOB_LEASE_LOST", "Interrupted before publication", 409)

    monkeypatch.setattr(linked.repo, "finish_job", lost_before_save)
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(run_job, linked.service, original).result(timeout=15)
    assert linked.provider.calls == 2 and SourceDocumentChange.select().count() == 0
    SourceDocumentJob.update(lease_until=0).where(SourceDocumentJob.id == original["id"]).execute()
    monkeypatch.setattr(linked.repo, "finish_job", finish)
    recovered = linked.repo.claim()
    assert recovered["checkpoint"] and recovered["lease_token"] != original["lease_token"]
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(run_job, linked.service, recovered).result(timeout=15)
    assert linked.provider.calls == 3 and SourceDocumentChange.select().count() == 1
    assert SourceDocumentJob.get_by_id(original["id"]).status == "done"
    with pytest.raises(SourceWorkspaceError, match="Задание отменено"):
        finish(original["id"], original["lease_token"], error={"code": "old_worker"})

    entered, closed = Event(), Event()

    class StalledModels(Models):
        async def stream(self, *_args):
            entered.set()
            try:
                while True:
                    await asyncio.sleep(0.01)
                    yield ""
            finally:
                closed.set()

    linked.service.models = StalledModels()
    await linked.service.prepare("author", "workspace", target["id"], {"expected_version": target["version"], "prompt": "Отдельная отменяемая подготовка"})
    cancelled = linked.repo.claim()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_job, linked.service, cancelled)
        assert entered.wait(10)
        linked.repo.cancel("author", "workspace", target["id"], cancelled["id"])
        future.result(timeout=10)
    assert closed.is_set()
    row = SourceDocumentJob.get_by_id(cancelled["id"])
    assert row.status == row.stage == "cancelled" and row.result_id is None
    assert SourceDocumentChange.select().count() == 1
    assert SourceWorkspaceDraft.get_by_id(target["id"]).content == target["content"]
