"""Administrator read access with real disposable project and TZ persistence."""

import inspect
from datetime import datetime

import pytest

from api.db.db_models import SourceWorkspace, SourceWorkspaceMember, User, UserTenant
from api.source_workbench.errors import SourceWorkspaceError
from api.source_workbench.linked_storage import LinkedDocumentRepository
from api.technical_specifications.domain import SpecificationError, empty_sections, parse_markdown
from test.unit_test.technical_specifications import test_http as http_harness


@pytest.fixture
def administrator(store):
    return User.create(id="administrator", nickname="Администратор", email="admin@example.test", is_superuser=True)


@pytest.fixture
def http(store, monkeypatch):
    return http_harness.http.__wrapped__(store, monkeypatch)


@pytest.mark.parametrize("tenant", [None, "foreign-organization"])
def test_administrator_reads_projects_specifications_and_history_without_membership(store, administrator, tenant):
    document = store.create("author", "workspace", "ТЗ", empty_sections())
    SourceWorkspace.update(tenant_id=tenant).where(SourceWorkspace.id == "workspace").execute()

    assert LinkedDocumentRepository().workspace(administrator.id, "workspace")["role"] == "reader"
    assert [item["id"] for item in store.list(administrator.id, "workspace")] == [document["id"]]
    assert store.get(administrator.id, "workspace", document["id"]) == document
    assert store.history(administrator.id, "workspace", document["id"], 1)["sections"] == document["sections"]
    with pytest.raises(SourceWorkspaceError):
        store.get("reader", "other", document["id"])
    with pytest.raises(SpecificationError, match="ТЗ не найдено"):
        store.get(administrator.id, "other", document["id"])
    with pytest.raises(SourceWorkspaceError, match="не найдено"):
        store.list(administrator.id, "missing-workspace")


def test_project_list_includes_all_projects_for_admin_and_preserves_normal_acl(store, administrator):
    from api.source_workbench.adapters import SourceWorkspaceRepository

    list_projects = inspect.unwrap(SourceWorkspaceRepository.list)
    SourceWorkspace.insert_many([{"id": f"foreign-{index}", "owner_id": "stranger", "title": f"Чужой {index}", "tenant_id": "foreign-organization"} for index in range(102)]).execute()
    SourceWorkspace.create(id="revoked", owner_id="reader", title="Отозванный", tenant_id="organization")
    SourceWorkspaceMember.create(workspace_id="revoked", user_id="author", role="editor")
    UserTenant.create(id="revoked-membership", user_id="author", tenant_id="organization", role="normal", invited_by="reader", status="0")
    # SQLite's DateTimeField does not parse timezone suffixes; production uses PostgreSQL.
    SourceWorkspace.update(created_at=datetime(2026, 10, 21), updated_at=datetime(2026, 10, 21)).execute()

    projects = list_projects(administrator.id)
    assert len(projects) == SourceWorkspace.select().count() > 100
    assert {item["id"] for item in list_projects("author")} == {"workspace", "sibling"}
    assert {item["id"] for item in list_projects("reader")} == {"workspace"}
    assert list_projects("missing-user") == []
    assert SourceWorkspaceMember.select().where(SourceWorkspaceMember.user_id == administrator.id).count() == 0


@pytest.mark.parametrize("attributes", [{"is_superuser": False}, {"is_active": "0"}, {"status": "0"}])
def test_inactive_or_nonadmin_accounts_do_not_get_global_visibility(store, administrator, attributes):
    from api.source_workbench.adapters import SourceWorkspaceRepository

    User.update(**attributes).where(User.id == administrator.id).execute()
    assert inspect.unwrap(SourceWorkspaceRepository.list)(administrator.id) == []
    with pytest.raises(SourceWorkspaceError) as error:
        LinkedDocumentRepository().workspace(administrator.id, "workspace")
    assert error.value.status == 404


@pytest.mark.parametrize("role", [None, "reader", "editor", "owner"])
def test_admin_preserves_project_role_and_gets_no_extra_write_capabilities(store, administrator, role):
    repository = LinkedDocumentRepository()
    if role == "owner":
        SourceWorkspace.update(owner_id=administrator.id).where(SourceWorkspace.id == "workspace").execute()
    elif role:
        SourceWorkspaceMember.create(workspace_id="workspace", user_id=administrator.id, role=role)
    assert repository.workspace(administrator.id, "workspace")["role"] == (role or "reader")
    for capability in ("edit", "approve", "manage"):
        if role == "owner" or (role == "editor" and capability == "edit"):
            assert repository.workspace(administrator.id, "workspace", capability)["role"] == role
        else:
            with pytest.raises(SourceWorkspaceError):
                repository.workspace(administrator.id, "workspace", capability)


def test_admin_with_revoked_tenant_membership_reads_as_reader_and_cannot_write(store, administrator):
    SourceWorkspace.update(tenant_id="organization").where(SourceWorkspace.id == "workspace").execute()
    SourceWorkspaceMember.create(workspace_id="workspace", user_id=administrator.id, role="editor")
    UserTenant.create(id="admin-membership", user_id=administrator.id, tenant_id="organization", role="normal", invited_by="author", status="0")
    repository = LinkedDocumentRepository()
    assert repository.workspace(administrator.id, "workspace")["role"] == "reader"
    with pytest.raises(SourceWorkspaceError) as error:
        repository.workspace(administrator.id, "workspace", "edit")
    assert error.value.status == 403
    UserTenant.update(status="1").where(UserTenant.id == "admin-membership").execute()
    assert repository.workspace(administrator.id, "workspace")["role"] == "editor"
    assert repository.workspace(administrator.id, "workspace", "edit")["role"] == "editor"


def test_admin_does_not_read_or_overwrite_authors_personal_draft(store, administrator):
    document = store.create("author", "workspace", "ТЗ", empty_sections())
    private = store.save_draft("author", "workspace", document["id"], 0, {"draft": {"text": "Личный текст автора"}})
    assert store.get_draft(administrator.id, "workspace", document["id"]) is None
    store.save_draft(administrator.id, "workspace", document["id"], 0, {"draft": {"text": "Личный текст администратора"}})
    assert store.get_draft("author", "workspace", document["id"]) == private


@pytest.mark.asyncio
async def test_http_admin_reads_and_exports_but_cannot_mutate_foreign_specification(http, store, administrator):
    client, actor = http
    document = store.create("author", "workspace", "ТЗ", empty_sections())
    actor.id = administrator.id
    base = http_harness.BASE
    url = base + "/" + document["id"]
    for path in (base, url, url + "/revisions", url + "/revisions/1"):
        assert (await client.get(path)).status_code == 200
    response = await client.post(url + "/export", json={"revision": document["revision"]})
    assert response.status_code == 200
    assert parse_markdown((await response.get_json())["data"]["content"]) == document["sections"]
    for method, path, data in [
        ("PUT", url, {"version": 1, "title": "Запрещено", "sections": document["sections"]}),
        ("POST", base, {"title": "Запрещено"}),
        ("POST", url + "/chat", {}),
        ("POST", url + "/publish-eva", {}),
        ("POST", url + "/apply", {}),
        ("POST", url + "/restore", {"revision": 1, "version": 1}),
    ]:
        assert (await client.open(path, method=method, json=data)).status_code in {403, 404}
    assert store.get("author", "workspace", document["id"]) == document
    User.update(is_superuser=False).where(User.id == administrator.id).execute()
    assert (await client.get(url)).status_code == 404
