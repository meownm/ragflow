"""Group persistence with real queries and disposable SQLite transactions."""

import inspect
import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from peewee import IntegrityError, SqliteDatabase

from api.db.db_models import AccessGroup, AccessGroupDataset, AccessGroupSection, AccessGroupUser, Knowledgebase, User
from api.db.services import access_group_service as service
from api.db.services.navigation_visibility_service import NAVIGATION_SECTIONS


@pytest.fixture
def group_database(monkeypatch):
    database = SqliteDatabase(":memory:")
    tables = [AccessGroup, AccessGroupDataset, AccessGroupSection, AccessGroupUser, Knowledgebase, User]
    # Retain service bodies; replace only decorators bound to the configured pool.
    # This lane validates persistence/transactions, not connection-pool lifecycle.
    for name in ("create", "update"):
        monkeypatch.setattr(service.AccessGroupService, name, classmethod(inspect.unwrap(getattr(service.AccessGroupService, name).__func__)))
    for name in ("list_groups", "options", "delete", "effective_policy"):
        monkeypatch.setattr(service.AccessGroupService, name, staticmethod(inspect.unwrap(getattr(service.AccessGroupService, name))))
    monkeypatch.setattr(service, "DB", database)
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        User.create(id="member", nickname="Member", email="member@example.test")
        Knowledgebase.create(id="dataset", tenant_id="tenant", name="Dataset", embd_id="synthetic", created_by="member")
        yield database
        database.close()


def _group(name, sections):
    return service.AccessGroupService.create({"name": name, "user_ids": ["member"], "dataset_ids": ["dataset"], "sections": sections})


def test_persisted_group_union_repeated_update_and_delete(group_database):
    first = _group("First", ["dataset"])
    second = _group("Second", ["chat"])
    assert service.AccessGroupService.effective_policy("member") == {"sections": {"dataset", "chat"}, "dataset_ids": {"dataset"}}
    for _ in range(2):
        service.AccessGroupService.update(first["id"], {"sections": ["agent"], "dataset_ids": []})
    assert AccessGroupSection.select().where(AccessGroupSection.group_id == first["id"]).count() == 1
    assert service.AccessGroupService.effective_policy("member") == {"sections": {"agent", "chat"}, "dataset_ids": {"dataset"}}
    service.AccessGroupService.delete(second["id"])
    assert service.AccessGroupService.effective_policy("member") == {"sections": {"agent"}, "dataset_ids": set()}
    for table in (AccessGroupDataset, AccessGroupUser, AccessGroupSection):
        assert table.select().where(table.group_id == second["id"]).count() == 0


def test_administrator_can_be_selected_added_and_removed(group_database):
    User.create(id="admin", nickname="Administrator", email="admin@example.test", is_superuser=True)
    options = service.AccessGroupService.options()
    assert next(user for user in options["users"] if user["id"] == "admin")["is_superuser"]

    group = service.AccessGroupService.create({"name": "Administrators", "user_ids": ["member", "admin"], "dataset_ids": ["dataset"], "sections": ["dataset"]})
    assert group["user_ids"] == ["admin", "member"]
    for user_ids in (["member"], ["member", "admin"]):
        updated = service.AccessGroupService.update(group["id"], {"user_ids": user_ids})
        assert updated["user_ids"] == sorted(user_ids)
        assert updated["dataset_ids"] == ["dataset"]
        assert updated["sections"] == ["dataset"]
        assert User.get_by_id("admin").is_superuser


def test_group_link_failure_rolls_back_name_and_all_grants(group_database, monkeypatch):
    group = _group("Original", ["dataset"])

    def fail_section_write(*_args, **_kwargs):
        raise IntegrityError("synthetic link write failure")

    monkeypatch.setattr(AccessGroupSection, "insert_many", fail_section_write)
    with pytest.raises(IntegrityError, match="synthetic link write failure"):
        service.AccessGroupService.update(group["id"], {"name": "Changed", "dataset_ids": [], "sections": ["chat"]})
    assert AccessGroup.get_by_id(group["id"]).name == "Original"
    assert service.AccessGroupService.effective_policy("member") == {"sections": {"dataset"}, "dataset_ids": {"dataset"}}


@pytest.mark.parametrize("field", ["user_ids", "dataset_ids"])
def test_group_unknown_reference_does_not_replace_existing_grants(group_database, field):
    group = _group("Original", ["dataset"])
    with pytest.raises(service.AccessGroupValidationError, match="Unknown"):
        service.AccessGroupService.update(group["id"], {field: ["missing"], "sections": ["chat"]})
    assert service.AccessGroupService.effective_policy("member") == {"sections": {"dataset"}, "dataset_ids": {"dataset"}}


def _add_deleted_dataset_link(group_id):
    Knowledgebase.create(id="deleted", tenant_id="tenant", name="Deleted", embd_id="synthetic", created_by="member")
    AccessGroupDataset.create(group_id=group_id, dataset_id="deleted")
    Knowledgebase.delete_by_id("deleted")


@pytest.mark.parametrize("full_form", [False, True])
def test_saving_sections_removes_deleted_datasets_and_preserves_live_grants(group_database, full_form):
    group = _group("Original", ["dataset"])
    _add_deleted_dataset_link(group["id"])
    payload = {"sections": ["business_documents", "translator"]}
    if full_form:
        payload.update(name=group["name"], description="", user_ids=group["user_ids"], dataset_ids=["dataset", "deleted"])

    updated = service.AccessGroupService.update(group["id"], payload)

    assert updated["sections"] == ["business_documents", "translator"]
    assert updated["dataset_ids"] == ["dataset"]
    assert updated["user_ids"] == ["member"]
    assert not AccessGroupDataset.select().where(AccessGroupDataset.dataset_id == "deleted").exists()


def test_new_unknown_dataset_is_rejected_without_cleaning_or_changing_grants(group_database):
    group = _group("Original", ["dataset"])
    _add_deleted_dataset_link(group["id"])
    with pytest.raises(service.AccessGroupValidationError, match="Unknown dataset IDs: new-missing$"):
        service.AccessGroupService.update(group["id"], {"name": "Changed", "sections": ["chat"], "dataset_ids": ["dataset", "deleted", "new-missing"]})
    assert AccessGroup.get_by_id(group["id"]).name == "Original"
    assert service.AccessGroupService.effective_policy("member") == {"sections": {"dataset"}, "dataset_ids": {"dataset", "deleted"}}


def test_successful_save_does_not_clean_or_change_another_group(group_database):
    first = _group("First", ["dataset"])
    second = _group("Second", ["chat"])
    _add_deleted_dataset_link(first["id"])
    AccessGroupDataset.create(group_id=second["id"], dataset_id="deleted")
    service.AccessGroupService.update(first["id"], {"sections": ["translator"]})
    groups = {group["id"]: group for group in service.AccessGroupService.list_groups()}
    assert groups[first["id"]]["dataset_ids"] == ["dataset"]
    assert groups[second["id"]]["dataset_ids"] == ["dataset", "deleted"]
    assert groups[second["id"]]["sections"] == ["chat"]


def test_deleted_dataset_link_in_another_group_does_not_authorize_unknown_id(group_database):
    first = _group("First", ["dataset"])
    second = _group("Second", ["chat"])
    _add_deleted_dataset_link(first["id"])
    with pytest.raises(service.AccessGroupValidationError, match="Unknown dataset IDs: deleted$"):
        service.AccessGroupService.update(second["id"], {"dataset_ids": ["deleted"]})
    assert next(group for group in service.AccessGroupService.list_groups() if group["id"] == second["id"])["dataset_ids"] == ["dataset"]


def test_create_still_rejects_deleted_dataset_even_when_another_group_keeps_a_link(group_database):
    group = _group("Original", ["dataset"])
    _add_deleted_dataset_link(group["id"])
    with pytest.raises(service.AccessGroupValidationError, match="Unknown dataset IDs: deleted$"):
        service.AccessGroupService.create({"name": "New", "dataset_ids": ["deleted"], "sections": ["chat"]})
    assert AccessGroup.select().count() == 1


def test_orphan_cleanup_failure_rolls_back_saved_sections_and_group_fields(group_database):
    group = _group("Original", ["dataset"])
    _add_deleted_dataset_link(group["id"])
    group_database.execute_sql(
        "CREATE TRIGGER refuse_orphan_cleanup BEFORE DELETE ON access_group_dataset WHEN OLD.dataset_id = 'deleted' BEGIN SELECT RAISE(ABORT, 'synthetic orphan cleanup failure'); END"
    )
    with pytest.raises(IntegrityError, match="synthetic orphan cleanup failure"):
        service.AccessGroupService.update(group["id"], {"name": "Changed", "sections": ["chat"]})
    assert AccessGroup.get_by_id(group["id"]).name == "Original"
    assert service.AccessGroupService.effective_policy("member") == {"sections": {"dataset"}, "dataset_ids": {"dataset", "deleted"}}


@pytest.mark.parametrize("section", NAVIGATION_SECTIONS)
def test_every_section_is_independently_persisted_and_listed(group_database, section):
    group = _group("Independent", [section])
    assert group["sections"] == [section]
    assert service.AccessGroupService.list_groups()[0]["sections"] == [section]
    assert service.AccessGroupService.effective_policy("member")["sections"] == {section}
    assert service.AccessGroupService.options()["sections"] == list(NAVIGATION_SECTIONS)


def test_persisted_group_visibility_union_empty_policy_and_global_fallback(group_database, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(service, "get_visible_sections", lambda: ["chat"])
    user = SimpleNamespace(id="member", is_superuser=False)
    assert service.AccessGroupService.visible_sections(user) == ["chat"]
    first = _group("First", ["home"])
    second = _group("Second", ["sql_projects"])
    assert service.AccessGroupService.visible_sections(user) == ["home", "sql_projects"]
    service.AccessGroupService.delete(second["id"])
    service.AccessGroupService.update(first["id"], {"sections": []})
    assert service.AccessGroupService.visible_sections(user) == []
    service.AccessGroupService.delete(first["id"])
    assert service.AccessGroupService.visible_sections(user) == ["chat"]


def test_source_choices_only_return_assigned_labels_and_support_search(group_database, monkeypatch):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.utils.api_utils import remap_dictionary_keys

    _group("Search only", ["search"])
    Knowledgebase.create(id="not-assigned", tenant_id="tenant", name="Hidden", embd_id="synthetic", created_by="member")
    monkeypatch.setattr(KnowledgebaseService, "get_list", classmethod(inspect.unwrap(KnowledgebaseService.get_list.__func__)))
    root = Path(__file__).parents[5]
    namespace = {
        "KnowledgebaseService": KnowledgebaseService,
        "TenantService": SimpleNamespace(get_joined_tenants_by_user_id=lambda _actor: []),
        "UserService": SimpleNamespace(get_by_ids=lambda ids: list(User.select().where(User.id.in_(ids)))),
        "remap_dictionary_keys": remap_dictionary_keys,
    }

    def load_function(path, name, scope):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), scope)

    load_function(root / "api/apps/services/dataset_api_service.py", "list_datasets", namespace)
    args = {"ext": {}}
    route = {
        "request": object(),
        "ListDatasetReq": object(),
        "validate_and_parse_request_args": lambda *_args: (args, None),
        "dataset_api_service": SimpleNamespace(list_datasets=namespace["list_datasets"]),
        "get_result": lambda **kwargs: kwargs,
        "get_error_data_result": lambda **kwargs: kwargs,
        "get_error_argument_result": lambda error: {"error": error},
        "logging": logging,
    }
    load_function(root / "api/apps/restful_apis/dataset_api.py", "list_dataset_choices", route)
    response = route["list_dataset_choices"]("member")
    assert response["total"] == 1
    assert response["data"][0]["id"] == "dataset"
    assert set(response["data"][0]) == {"id", "name", "nickname", "avatar", "embedding_model", "chunk_method", "chunk_count"}
    args["ext"] = {"keywords": "Hidden"}
    assert route["list_dataset_choices"]("member") == {"data": [], "total": 0}
    args["ext"] = {"keywords": "DATA"}
    assert route["list_dataset_choices"]("member")["data"][0]["id"] == "dataset"
    service.AccessGroupService.update(service.AccessGroupService.list_groups()[0]["id"], {"dataset_ids": []})
    assert route["list_dataset_choices"]("member") == {"data": [], "total": 0}
