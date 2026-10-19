"""Owner namespaces, private-name non-disclosure and database conflicts."""

from pathlib import PurePath
import re
from types import SimpleNamespace
from unittest.mock import Mock

import peewee
import pytest

from api.db.resource_name_schema import install_resource_name_indexes, is_resource_name_conflict
from common.resource_names import validate_resource_name
from test.unit_test import test_team_resource_visibility as visibility_tests

resources = visibility_tests.resources
load_definitions = visibility_tests.load_definitions


@pytest.mark.parametrize("value", [None, 12, "", "   ", "x" * 256, "nul\x00", "surrogate\ud800"])
def test_reject_invalid_resource_names(value):
    with pytest.raises(ValueError):
        validate_resource_name(value, limit=255, label="Name")


def test_length_is_characters_and_bmp_is_dataset_specific():
    assert validate_resource_name(" Я ", limit=1, label="Name") == "Я"
    assert validate_resource_name("Я" * 255, limit=255, label="Name") == "Я" * 255
    assert validate_resource_name("🚀", limit=1, label="Name") == "🚀"
    with pytest.raises(ValueError):
        validate_resource_name("🚀", limit=128, label="Dataset name", bmp_only=True)


@pytest.mark.parametrize("title", ["owner-me", "member-me", "available", "OWNER-ME", ""])
async def test_member_rename_never_queries_hidden_names(resources, monkeypatch, title):
    r = resources
    lookup = Mock(side_effect=AssertionError("must not look up hidden titles"))
    monkeypatch.setattr(r.UserCanvasService, "name_exists", lookup)
    r.get_request_json.return_value = {"title": title}
    result = await r.update_agent("owner-team", "member")
    assert result["code"] != 0
    assert result["message"] == "Only the owner can rename or change the category of an agent."
    assert r.UserCanvas.get_by_id("owner-team").title == "owner-team"
    lookup.assert_not_called()


async def test_member_save_omits_unchanged_identity_fields(resources, monkeypatch):
    r = resources
    monkeypatch.setattr(r.UserCanvasService, "name_exists", Mock(side_effect=AssertionError("private namespace lookup")))
    original_update = r.UserCanvasService.update_by_id

    def concurrent_rename(identity, patch):
        assert "title" not in patch and "canvas_category" not in patch and "permission" not in patch
        r.UserCanvas.update(title="owner-renamed", permission="me").where(r.UserCanvas.id == identity).execute()
        return original_update(identity, patch)

    monkeypatch.setattr(r.UserCanvasService, "update_by_id", concurrent_rename)
    r.get_request_json.return_value = {"title": "owner-team", "canvas_category": "agent_canvas", "permission": "team", "description": "member edit"}
    assert (await r.update_agent("owner-team", "member"))["code"] == 0
    assert r.UserCanvas.get_by_id("owner-team").title == "owner-renamed"
    assert r.UserCanvas.get_by_id("owner-team").permission == "me"


async def test_owner_rename_checks_owner_category_and_excludes_self(resources):
    r = resources
    r.get_request_json.return_value = {"title": " OWNER-ME "}
    assert (await r.update_agent("owner-team", "owner"))["code"] == 102
    r.get_request_json.return_value = {"title": "OWNER-TEAM"}
    assert (await r.update_agent("owner-team", "owner"))["code"] == 0
    r.get_request_json.return_value = {"title": "member-me"}
    assert (await r.update_agent("owner-team", "owner"))["code"] == 0


@pytest.mark.parametrize("title", ["", "   ", 12, "x" * 256])
async def test_agent_create_and_owner_rename_reject_invalid_title(resources, title):
    r = resources
    r.namespace.update(_sync_switch_routes_from_graph=lambda value: value, CanvasReplicaService=SimpleNamespace(normalize_dsl=lambda value: value))
    r.get_request_json.return_value = {"title": title, "dsl": {}}
    assert (await r.create_agent("owner"))["code"] == 102
    r.get_request_json.return_value = {"title": title}
    assert (await r.update_agent("owner-team", "owner"))["code"] == 102


def test_database_enforces_owner_category_case_and_active_scope(resources):
    r = resources
    for model, fields in [
        ("Dialog", {"tenant_id": "owner", "name": " OWNER-ME "}),
        ("UserCanvas", {"user_id": "owner", "title": " OWNER-ME "}),
    ]:
        with pytest.raises(peewee.IntegrityError):
            r.insert(model, id="collision", **fields)
    r.insert("Dialog", id="reused", tenant_id="owner", name="deleted")
    r.insert("UserCanvas", id="other-category", user_id="owner", title="OWNER-ME", canvas_category="dataflow_canvas")
    r.insert("UserCanvas", id="other-owner", user_id="member", title="OWNER-ME")


@pytest.fixture
def datasets(resources):
    r = resources
    ns = r.namespace
    ns.update(PurePath=PurePath, re=re, DATASET_NAME_LIMIT=128, get_parser_config=lambda *args: {})
    load_definitions("api/db/services/__init__.py", ["duplicate_name", "_split_name_counter"], ns)
    load_definitions("api/db/services/knowledgebase_service.py", ["KnowledgebaseService"], ns, methods={"query_by_name_case_insensitive", "create_with_name", "save_with_unique_name"})
    return r, ns["KnowledgebaseService"]


def test_dataset_suffix_fits_unicode_and_extension_boundaries(datasets):
    r, service = datasets
    for i, name in enumerate(["Я" * 128, "x" * 128, "a." + "x" * 126, "Report"]):
        r.insert("Knowledgebase", id=f"existing-{i}", tenant_id="owner", name=name)
        ok, payload = service.create_with_name(name=name, tenant_id="owner")
        assert ok and len(payload["name"]) <= 128 and "(1)" in payload["name"]
        payload.update(id=f"created-{i}", embd_id="test")
        assert service.save_with_unique_name(payload)
    assert service.create_with_name(name="🚀", tenant_id="owner")[0] is False
    assert service.create_with_name(name="Я" * 65, tenant_id="owner")[0] is True
    ok, payload = service.create_with_name(name="report", tenant_id="owner")
    assert ok and payload["name"] == "report(2)"


def test_dataset_stale_candidates_retry_after_name_conflict(datasets):
    r, service = datasets
    ok, first = service.create_with_name(name="Concurrent", tenant_id="owner")
    ok2, second = service.create_with_name(name="CONCURRENT", tenant_id="owner")
    assert ok and ok2
    first["id"], second["id"] = "first", "second"
    first["embd_id"] = second["embd_id"] = "test"
    assert service.save_with_unique_name(first)
    assert service.save_with_unique_name(second)
    assert second["name"] == "CONCURRENT(1)"
    with pytest.raises(peewee.IntegrityError):
        service.save_with_unique_name(dict(second, name="unrelated-new-name"))


def test_migration_is_idempotent_and_does_not_rewrite_private_data(resources):
    r = resources
    install_resource_name_indexes(r.DB)
    r.DB.execute_sql("DROP INDEX dialog_owner_name_unique")
    r.insert("Dialog", id="old-duplicate", tenant_id="owner", name="OWNER-ME")
    with pytest.raises(RuntimeError, match="1 duplicate name groups in dialog") as failure:
        install_resource_name_indexes(r.DB)
    assert "OWNER-ME" not in str(failure.value)
    assert r.Dialog.get_by_id("old-duplicate").name == "OWNER-ME"


def test_conflict_classifier_does_not_mask_primary_key_failure():
    assert not is_resource_name_conflict(peewee.IntegrityError("UNIQUE constraint failed: dialog.id"), "dialog_owner_name_unique")
    cause = RuntimeError("not printed")
    cause.pgcode = "23505"
    cause.diag = SimpleNamespace(constraint_name="dialog_owner_name_unique")
    error = peewee.IntegrityError("not printed")
    error.__cause__ = cause
    assert is_resource_name_conflict(error, "dialog_owner_name_unique")
    assert not is_resource_name_conflict(error, "knowledgebase_owner_name_unique")


async def test_agent_racing_rename_returns_duplicate_response(resources, monkeypatch):
    r = resources
    monkeypatch.setattr(r.UserCanvasService, "name_exists", lambda *args, **kwargs: False)
    r.get_request_json.return_value = {"title": "OWNER-ME"}
    assert (await r.update_agent("owner-team", "owner"))["code"] == 102
    assert r.UserCanvas.get_by_id("owner-team").title == "owner-team"


@pytest.mark.parametrize("handler", ["update_chat", "patch_chat"])
async def test_chat_racing_rename_returns_duplicate_response(resources, monkeypatch, handler):
    r = resources
    monkeypatch.setattr(r.DialogService, "name_exists", lambda *args, **kwargs: False)
    r.get_request_json.return_value = {"name": "MEMBER-ME"}
    assert (await getattr(r, handler)("member-team"))["code"] == 102
    assert r.Dialog.get_by_id("member-team").name == "member-team"


async def test_dataset_case_only_rename_and_duplicate_response(datasets, monkeypatch):
    r, service = datasets
    r.insert("Knowledgebase", id="first", tenant_id="owner", name="First", created_by="owner")
    r.insert("Knowledgebase", id="second", tenant_id="owner", name="Second", created_by="owner")
    service.get_or_none = staticmethod(lambda **kwargs: r.Knowledgebase.get_or_none(**kwargs))
    service.accessible = staticmethod(lambda *args: True)
    r.namespace.update(
        Connector2KbService=SimpleNamespace(list_connectors=lambda *args: [], link_connectors=lambda *args: []),
        remap_dictionary_keys=lambda value: value,
    )
    load_definitions("api/apps/services/dataset_api_service.py", ["update_dataset"], r.namespace)
    update = r.namespace["update_dataset"]
    ok, result = await update("owner", "first", {"name": "FIRST"})
    assert ok and result["name"] == "FIRST"
    assert not (await update("owner", "first", {"name": "SECOND"}))[0]
    assert not (await update("owner", "first", {"name": "🚀"}))[0]
    query = service.query_by_name_case_insensitive
    monkeypatch.setattr(service, "query_by_name_case_insensitive", lambda **kwargs: query(**kwargs).where(r.Knowledgebase.id == "absent"))
    assert not (await update("owner", "first", {"name": "SECOND"}))[0]
    assert r.Knowledgebase.get_by_id("first").name == "FIRST"
    ok, result = await update("owner", "first", {"name": "Final", "ext": {"id": "stolen", "tenant_id": "peer", "created_by": "peer", "status": "0"}})
    assert ok and result["id"] == "first" and result["tenant_id"] == "owner" and result["status"] == "1"
    assert result["created_by"] == "owner"
