"""Idempotent upgrade from saved drafts to versioned working documents."""

from peewee import PostgresqlDatabase, SqliteDatabase
from playhouse.migrate import PostgresqlMigrator, SqliteMigrator, migrate

from api.db.db_models import (
    SourceDocumentChange,
    SourceDocumentDecision,
    SourceDocumentDependency,
    SourceDocumentEvent,
    SourceDocumentImpact,
    SourceDocumentInput,
    SourceDocumentJob,
    SourceDocumentRevision,
    SourceWorkspace,
    SourceWorkspaceDraft,
    SourceWorkspaceMember,
)
from .linked_domain import digest, heading, identifier, split_sections


LINKED_MODELS = (
    SourceWorkspaceMember,
    SourceDocumentRevision,
    SourceDocumentDependency,
    SourceDocumentInput,
    SourceDocumentImpact,
    SourceDocumentChange,
    SourceDocumentDecision,
    SourceDocumentJob,
    SourceDocumentEvent,
)
DOCUMENT_COLUMNS = (
    "title",
    "document_type",
    "change_policy",
    "source_kind",
    "source_ref",
    "origin_key",
    "current_revision_id",
    "approved_revision_id",
    "required_for_readiness",
    "implemented_revision_id",
    "implementation_event_id",
    "responsible_id",
    "approver_id",
    "provenance_state",
)


def migrate_linked_documents():
    """DDL is resumable; every migrated document/revision pair is atomic."""
    database = SourceWorkspaceDraft._meta.database
    if isinstance(database, PostgresqlDatabase):
        migrator = PostgresqlMigrator(database)
    elif isinstance(database, SqliteDatabase):
        # SQLite is used by isolated unit and HTTP fixtures.
        migrator = SqliteMigrator(database)
    else:
        raise RuntimeError("Linked documents require PostgreSQL; SQLite is supported only for isolated tests")
    if not SourceWorkspaceDraft.table_exists():
        SourceWorkspaceDraft.create_table()
    database.create_tables([model for model in LINKED_MODELS if not model.table_exists()], safe=True)
    workspace_columns = {column.name for column in database.get_columns("source_workspace")}
    if "tenant_id" not in workspace_columns:
        migrate(migrator.add_column("source_workspace", "tenant_id", SourceWorkspace._meta.fields["tenant_id"].clone()))
    columns = {column.name for column in database.get_columns("source_workspace_draft")}
    for name in DOCUMENT_COLUMNS:
        if name not in columns:
            migrate(migrator.add_column("source_workspace_draft", name, SourceWorkspaceDraft._meta.fields[name].clone()))
    for model, names in ((SourceDocumentDependency, ("reviewed_source_state_token",)), (SourceDocumentImpact, ("source_state_token",)), (SourceDocumentInput, ("source_state_token",))):
        existing = {column.name for column in database.get_columns(model._meta.table_name)}
        for name in names:
            if name not in existing:
                migrate(migrator.add_column(model._meta.table_name, name, model._meta.fields[name].clone()))
    impact_columns = ["dependency_id", "source_revision_id", "target_revision_id", "dependency_version", "source_state_token"]
    # Install the stronger key before retiring the old key; interruption is resumable.
    if not any(index.unique and list(index.columns) == impact_columns for index in database.get_indexes("source_document_impact")):
        database.execute_sql("CREATE UNIQUE INDEX source_document_impact_state ON source_document_impact (" + ", ".join(impact_columns) + ")")
    for index in database.get_indexes("source_document_impact"):
        if index.unique and list(index.columns) == impact_columns[:-1]:
            database.execute_sql('DROP INDEX "' + index.name.replace('"', '""') + '"')
    change_columns = {column.name for column in database.get_columns("source_document_change")}
    for name in ("origin", "author_id", "candidate_sections"):
        if name not in change_columns:
            migrate(migrator.add_column("source_document_change", name, SourceDocumentChange._meta.fields[name].clone()))
    index_name = "source_document_origin"
    if not any(index.unique and list(index.columns) == ["workspace_id", "origin_key"] for index in database.get_indexes("source_workspace_draft")):
        database.execute_sql(f"CREATE UNIQUE INDEX {index_name} ON source_workspace_draft (workspace_id, origin_key)")
    while True:
        rows = list(SourceWorkspaceDraft.select().where(SourceWorkspaceDraft.current_revision_id.is_null()).order_by(SourceWorkspaceDraft.id).limit(100))
        if not rows:
            break
        for row in rows:
            with database.atomic():
                revision_id = identifier()
                sections = split_sections(row.content)
                title = heading(sections[0])
                updated = (
                    SourceWorkspaceDraft.update(
                        current_revision_id=revision_id,
                        responsible_id=row.owner_id,
                        approver_id=row.owner_id,
                        provenance_state="unresolved" if row.sources else "none",
                        title=row.title if row.title != "Черновик" or title == "Введение" else title[:255],
                    )
                    .where((SourceWorkspaceDraft.id == row.id) & SourceWorkspaceDraft.current_revision_id.is_null())
                    .execute()
                )
                if not updated:
                    continue
                SourceDocumentRevision.create(
                    id=revision_id,
                    document_id=row.id,
                    number=1,
                    sections=sections,
                    content_hash=digest(row.content),
                    text_length=len(row.content),
                    author_id=row.owner_id,
                )
                SourceDocumentEvent.create(
                    id=identifier(),
                    workspace_id=row.workspace_id,
                    document_id=row.id,
                    actor_id=row.owner_id,
                    kind="draft_migrated",
                    payload={"revision_id": revision_id, "historical_sources_available": not bool(row.sources)},
                )
