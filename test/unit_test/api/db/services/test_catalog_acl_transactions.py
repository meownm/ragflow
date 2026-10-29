"""Dataset authorization retains the caller's transaction and current grants."""

import inspect

import pytest
from peewee import OperationalError, SqliteDatabase

from api.db.db_models import AccessGroupDataset, AccessGroupSection, AccessGroupUser, Knowledgebase, User
from api.db.services.access_group_service import AccessGroupService
from api.db.services import access_group_service, knowledgebase_service
from api.db.services.common_service import CommonService
from api.db.services.knowledgebase_service import KnowledgebaseService


@pytest.fixture
def acl_database(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(knowledgebase_service, "DB", database)
    monkeypatch.setattr(access_group_service, "DB", database)
    # Rebind captured connection decorators, retaining their lifecycle behavior.
    # Stripping them would conceal the nested-transaction regression.
    for service, name, descriptor in (
        (KnowledgebaseService, "accessible", classmethod),
        (CommonService, "get_by_id", classmethod),
        (AccessGroupService, "effective_policy", staticmethod),
    ):
        method = getattr(service, name)
        function = getattr(method, "__func__", method)
        if hasattr(function, "__wrapped__"):
            monkeypatch.setattr(service, name, descriptor(database.connection_context()(inspect.unwrap(function))))
    tables = [Knowledgebase, User, AccessGroupUser, AccessGroupDataset, AccessGroupSection]
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.create_tables(tables)
        try:
            User.create(id="admin", nickname="Admin", email="admin@example.test", is_superuser=True)
            User.create(id="reader", nickname="Reader", email="reader@example.test")
            Knowledgebase.create(id="dataset", tenant_id="tenant", name="Original", embd_id="synthetic", created_by="admin")
            Knowledgebase.create(id="deleted", tenant_id="tenant", name="Deleted", embd_id="synthetic", created_by="admin", status="0")
            AccessGroupUser.create(group_id="group", user_id="reader")
            AccessGroupDataset.create(group_id="group", dataset_id="dataset")
            yield database
        finally:
            database.close()


@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize(
    ("actor", "dataset_id", "allowed"),
    [("admin", "dataset", True), ("reader", "dataset", True), ("outsider", "dataset", False), ("admin", "deleted", False), ("admin", "missing", False)],
)
def test_catalog_acl_preserves_nested_commit_and_rollback(acl_database, rollback, actor, dataset_id, allowed):
    with acl_database.atomic() as transaction:
        with acl_database.atomic():
            Knowledgebase.update(name="Changed").where(Knowledgebase.id == "dataset").execute()
            assert KnowledgebaseService.accessible(dataset_id, actor) is allowed
            assert acl_database.in_transaction()
            assert not acl_database.is_closed()
        assert Knowledgebase.get_by_id("dataset").name == "Changed"
        if rollback:
            transaction.rollback()
    assert Knowledgebase.get_by_id("dataset").name == ("Original" if rollback else "Changed")


def test_catalog_acl_observes_grant_revocation_and_rollback(acl_database):
    with acl_database.atomic() as transaction:
        AccessGroupDataset.delete().where(AccessGroupDataset.group_id == "group").execute()
        assert not KnowledgebaseService.accessible("dataset", "reader")
        transaction.rollback()
    assert KnowledgebaseService.accessible("dataset", "reader")


@pytest.mark.parametrize(
    ("read", "args"),
    [(KnowledgebaseService.accessible, ("dataset", "reader")), (AccessGroupService.effective_policy, ("reader",))],
)
def test_catalog_acl_releases_its_connection_on_backend_failure(acl_database, read, args):
    # Closing an in-memory database drops the schema: the next read must fail
    # closed and still return its newly acquired connection to the pool.
    acl_database.close()
    with pytest.raises(OperationalError, match="no such table"):
        read(*args)
    assert acl_database.is_closed()
