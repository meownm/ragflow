"""Search reading uses active team membership, independently of deletion."""

import inspect

import pytest
from peewee import SqliteDatabase

from api.db.db_models import Search, User, UserTenant
from api.db.services.search_service import SearchService


@pytest.fixture
def searches(monkeypatch):
    database = SqliteDatabase(":memory:")
    for name in ("get_detail", "accessible4deletion"):
        monkeypatch.setattr(SearchService, name, classmethod(inspect.unwrap(getattr(SearchService, name).__func__)))
    with database.bind_ctx([Search, User, UserTenant], bind_refs=False, bind_backrefs=False):
        database.create_tables([Search, User, UserTenant])
        User.create(id="owner", nickname="Owner", email="owner@example.test")
        Search.create(id="search", tenant_id="owner", created_by="owner", name="Search", search_config={"kb_ids": ["assigned"]})
        UserTenant.create(id="membership", user_id="member", tenant_id="owner", role="normal", invited_by="owner")
        yield
        database.close()


@pytest.mark.parametrize("actor,allowed", [("owner", True), ("member", True), ("outsider", False)])
def test_read_access_is_independent_from_delete_access(searches, actor, allowed):
    detail = SearchService.get_detail("search", actor)
    assert bool(detail) is allowed
    if allowed:
        assert detail["search_config"] == {"kb_ids": ["assigned"]}
    assert SearchService.accessible4deletion("search", actor) is (actor == "owner")


@pytest.mark.parametrize("entity", [Search, User, UserTenant])
def test_revoked_membership_or_inactive_search_owner_is_not_readable(searches, entity):
    assert SearchService.get_detail("search", "member")
    entity.update(status="0").execute()
    assert SearchService.get_detail("search", "member") == {}


def test_missing_search_does_not_fall_back_to_other_config(searches):
    assert SearchService.get_detail("missing", "member") == {}


@pytest.mark.parametrize("role,allowed", [("owner", True), ("admin", True), ("normal", True), ("invite", False)])
def test_pending_invitation_does_not_grant_team_read_access(searches, role, allowed):
    UserTenant.update(role=role).execute()
    assert bool(SearchService.get_detail("search", "member")) is allowed


async def test_mindmap_team_reader_reaches_generation_without_delete_permission(searches, monkeypatch):
    from api.db.services import dialog_service

    async def inline(function, *args):
        return function(*args)

    monkeypatch.setattr(dialog_service, "thread_pool_exec", inline)
    monkeypatch.setattr(dialog_service.KnowledgebaseService, "accessible", lambda kb_id, actor: kb_id == "assigned" and actor == "member")
    monkeypatch.setattr(dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: [])
    assert not SearchService.accessible4deletion("search", "member")
    assert await dialog_service.gen_mindmap("q", ["assigned"], "member", "search") == {"error": "No KB selected"}
