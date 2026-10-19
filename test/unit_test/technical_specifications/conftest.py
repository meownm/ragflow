import pytest
from peewee import SqliteDatabase


@pytest.fixture
def store(tmp_path):
    from api.db.db_models import SourceWorkspace, SourceWorkspaceDraft, SourceWorkspaceMember, TechnicalSpecification, TechnicalSpecificationDraft, TechnicalSpecificationRevision, User, UserTenant
    from api.technical_specifications.storage import SpecificationRepository

    database = SqliteDatabase(tmp_path / "specifications.sqlite", pragmas={"journal_mode": "wal", "busy_timeout": 10000})
    models = [SourceWorkspace, SourceWorkspaceDraft, SourceWorkspaceMember, User, UserTenant, TechnicalSpecification, TechnicalSpecificationDraft, TechnicalSpecificationRevision]
    with database.bind_ctx(models):
        database.create_tables(models)
        SourceWorkspace.create(id="workspace", owner_id="author", title="Проект")
        SourceWorkspace.create(id="other", owner_id="stranger", title="Другой проект")
        SourceWorkspace.create(id="sibling", owner_id="author", title="Второй проект")
        SourceWorkspaceMember.create(workspace_id="workspace", user_id="reader", role="reader")
        User.create(id="author", nickname="Автор", email="author@example.test")
        User.create(id="reader", nickname="Читатель", email="reader@example.test")
        yield SpecificationRepository()
        if not database.is_closed():
            database.close()
