"""Production Peewee queries and HTTP handlers over a disposable SQLite database."""

import ast
from copy import deepcopy
from functools import reduce
from functools import wraps
import json
import logging
from operator import or_
import os
from pathlib import Path
import sys
from uuid import uuid4
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import peewee
from playhouse.sqlite_ext import JSONField
from playhouse.migrate import SqliteMigrator, migrate
import pytest
from werkzeug.datastructures import MultiDict

from api.db import TenantPermission, UserTenantRole
from api.utils.pagination_utils import validate_rest_list_args
from common.constants import ParserType, RetCode, StatusEnum
from common.resource_names import validate_resource_name
from api.db.resource_name_schema import install_resource_name_indexes, is_resource_name_conflict


ROOT = Path(__file__).resolve().parents[2]


def load_definitions(path, names, namespace, *, methods=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    for node in nodes:
        if isinstance(node, ast.ClassDef) and methods is not None:
            node.body = [child for child in node.body if isinstance(child, ast.Assign) or getattr(child, "name", None) in methods]
        elif not isinstance(node, ast.ClassDef):
            node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)


@pytest.fixture
def resources(tmp_path, monkeypatch):
    test_db = peewee.SqliteDatabase(tmp_path / "team.sqlite")
    test_db.register_function(lambda value: value.lower() if value is not None else None, "lower", 1, deterministic=True)

    class Base(peewee.Model):
        create_time = peewee.IntegerField(default=1)
        update_time = peewee.IntegerField(default=1)
        create_date = peewee.TextField(default="")
        update_date = peewee.TextField(default="")

        class Meta:
            database = test_db

        def to_dict(self):
            return dict(self.__data__)

        @classmethod
        def getter_by(cls, name):
            return getattr(cls, name)

    namespace = {**vars(peewee), "DataBaseModel": Base, "AuthUser": object, "JSONField": JSONField, "LongTextField": peewee.TextField, "os": os, "ParserType": ParserType}
    model_names = ["User", "Tenant", "UserTenant", "Dialog", "Conversation", "API4Conversation", "UserCanvas", "UserCanvasVersion", "Knowledgebase"]
    load_definitions("api/db/db_models.py", model_names, namespace, methods={"Meta"})
    models = [namespace[name] for name in model_names]
    test_db.create_tables(models)
    install_resource_name_indexes(test_db)

    class CommonService:
        @classmethod
        def query(cls, **kwargs):
            return list(cls.model.select().filter(**kwargs))

        @classmethod
        def get_by_id(cls, identity):
            row = cls.model.get_or_none(cls.model.id == identity)
            return row is not None, row

        @classmethod
        def save(cls, **values):
            return cls.model.create(**values)

        @classmethod
        def update_by_id(cls, identity, values):
            return cls.model.update(values).where(cls.model.id == identity).execute()

        @classmethod
        def delete_by_id(cls, identity):
            return cls.model.delete().where(cls.model.id == identity).execute()

    namespace.update(DB=test_db, CommonService=CommonService, StatusEnum=StatusEnum, UserTenantRole=UserTenantRole, TenantPermission=TenantPermission, logging=logging, reduce=reduce, or_=or_)
    load_definitions("api/db/services/user_service.py", ["UserTenantService"], namespace, methods={"get_team_owner_ids"})
    load_definitions("api/db/services/dialog_service.py", ["DialogService"], namespace, methods={"get_accessible_chat", "get_by_tenant_ids", "name_exists"})
    load_definitions("api/db/services/canvas_service.py", ["UserCanvasService"], namespace, methods={"name_exists", "get_by_canvas_id", "get_by_tenant_ids", "accessible", "list_tags", "get_agent_dsl_with_release"})
    load_definitions("api/db/services/conversation_service.py", ["ConversationService"], namespace, methods={"get_list"})
    load_definitions(
        "api/db/services/api_service.py",
        ["API4ConversationService"],
        namespace,
        methods={"get_owned_session", "get_session_for_message", "get_list", "get_names", "_normalize_query_date", "append_message"},
    )
    user_module = ModuleType("api.db.services.user_service")
    user_module.UserTenantService = namespace["UserTenantService"]
    monkeypatch.setitem(sys.modules, user_module.__name__, user_module)
    models_module = ModuleType("api.db.db_models")
    for name in model_names:
        setattr(models_module, name, namespace[name])
    monkeypatch.setitem(sys.modules, models_module.__name__, models_module)

    def insert(model_name, **values):
        model = namespace[model_name]
        for name, field in model._meta.fields.items():
            if name not in values and not field.null and field.default is None:
                values[name] = "" if isinstance(field, (peewee.CharField, peewee.TextField)) else 0
        return model.create(**values)

    for user in ("owner", "member", "peer", "admin", "invite", "removed", "outsider", "disabled"):
        insert("User", id=user, email=f"{user}@example.test", nickname=user, status="0" if user == "disabled" else "1")
    for team in ("owner", "other", "inactive"):
        insert("Tenant", id=team, status="0" if team == "inactive" else "1")
    for user, role, status, team in (
        ("owner", "owner", "1", "owner"),
        ("member", "normal", "1", "owner"),
        ("peer", "normal", "1", "owner"),
        ("admin", "admin", "1", "owner"),
        ("invite", "invite", "1", "owner"),
        ("removed", "normal", "0", "owner"),
        ("disabled", "normal", "1", "owner"),
        ("outsider", "owner", "1", "other"),
        ("peer", "normal", "1", "other"),
        ("member", "normal", "1", "inactive"),
        ("outsider", "owner", "1", "inactive"),
    ):
        insert("UserTenant", id=f"{user}-{team}", user_id=user, tenant_id=team, role=role, status=status)
    for user in ("owner", "member", "peer", "outsider", "invite", "removed"):
        for permission in ("me", "team"):
            identity = f"{user}-{permission}"
            insert("Dialog", id=identity, tenant_id=user, name=identity, permission=permission)
            insert("UserCanvas", id=identity, user_id=user, title=identity, permission=permission, tags=user)
    insert("Dialog", id="deleted", tenant_id="owner", name="deleted", permission="team", status="0")
    for user in ("owner", "member", "peer"):
        insert("Conversation", id=f"session-{user}", dialog_id="owner-team", user_id=user, name=user, message=[], reference=[])
        insert(
            "API4Conversation",
            id=f"agent-session-{user}",
            dialog_id="owner-team",
            user_id="external-user",
            exp_user_id="external-user",
            created_by=user,
            name=user,
            message=[{"id": f"message-{user}", "content": f"private-{user}"}],
            source="agent",
        )
    insert("API4Conversation", id="legacy-unattributed", dialog_id="owner-team", user_id="member", created_by=None, message=[], source="agent")

    async def thread_pool_exec(function, *args, **kwargs):
        return function(*args, **kwargs)

    namespace.update(
        current_user=SimpleNamespace(id="member"),
        request=SimpleNamespace(args=MultiDict()),
        thread_pool_exec=thread_pool_exec,
        validate_rest_list_args=validate_rest_list_args,
        RetCode=RetCode,
        get_json_result=lambda **kwargs: {"code": 0, **kwargs},
        get_data_error_result=lambda **kwargs: {"code": 102, **kwargs},
        server_error_response=lambda exc: (_ for _ in ()).throw(exc),
        get_request_json=AsyncMock(return_value={}),
        _build_chat_response=lambda row: row.to_dict() if hasattr(row, "to_dict") else dict(row),
        chunks_format=lambda ref: ref,
        get_uuid=lambda: "new-session",
        deepcopy=deepcopy,
        check_duplicate_ids=lambda ids, kind: (ids, []),
        json=json,
        wraps=wraps,
        get_result=lambda **kwargs: {"code": 0, **kwargs},
        get_error_data_result=lambda message: {"code": 102, "message": message},
        validate_resource_name=validate_resource_name,
        is_resource_name_conflict=is_resource_name_conflict,
    )
    namespace["TenantService"] = SimpleNamespace(get_by_id=lambda user_id: (True, SimpleNamespace(llm_id="test-model")))
    tree = ast.parse((ROOT / "api/apps/restful_apis/chat_api.py").read_text(encoding="utf-8"))
    constants = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in {"_PERSISTED_FIELDS", "_READONLY_FIELDS", "_DEFAULT_PROMPT_CONFIG"} for target in node.targets)
    ]
    exec(compile(ast.Module(body=constants, type_ignores=[]), "chat-constants", "exec"), namespace)
    chat_functions = [
        "_ensure_owned_chat",
        "_ensure_accessible_chat",
        "_session_scope",
        "_validate_permission",
        "_validate_name",
        "_apply_prompt_defaults",
        "_build_session_response",
        "create",
        "update_chat",
        "patch_chat",
        "list_chats",
        "get_chat",
        "create_session",
        "list_sessions",
        "get_session",
        "update_session",
        "delete_sessions",
        "delete_session_message",
        "update_message_feedback",
        "delete_chat",
        "bulk_delete_chats",
    ]
    load_definitions("api/apps/restful_apis/chat_api.py", chat_functions, namespace)
    namespace.update(CanvasCategory=SimpleNamespace(Agent="agent_canvas"), _get_user_nickname=lambda user_id: user_id)
    load_definitions(
        "api/apps/restful_apis/agent_api.py",
        [
            "list_agents",
            "create_agent",
            "update_agent",
            "_require_canvas_access_sync",
            "_require_canvas_access_async",
            "get_agent_session",
            "delete_agent_session_item",
            "delete_agent_session",
            "agent_chat_completion",
            "get_agent_logs",
        ],
        namespace,
    )
    for name in ("get_agent_session", "delete_agent_session_item"):
        namespace[name] = namespace["_require_canvas_access_sync"](namespace[name])
    for name in ("delete_agent_session", "get_agent_logs"):
        namespace[name] = namespace["_require_canvas_access_async"](namespace[name])
    namespace["jsonify"] = lambda value: value

    async def completion_openai(*args, **kwargs):
        yield {"actor": args[0], "agent": args[1], "session_id": kwargs.get("session_id")}

    namespace["completion_openai"] = completion_openai
    namespace["validate_rest_api_page_size"] = lambda size: size
    yield SimpleNamespace(**namespace, insert=insert, namespace=namespace)
    test_db.close()


def test_accepted_team_is_symmetric_without_transitive_or_inactive_members(resources):
    teams = resources.UserTenantService
    assert teams.get_team_owner_ids("member") == ["admin", "member", "owner", "peer"]
    assert teams.get_team_owner_ids("owner") == ["admin", "member", "owner", "peer"]
    assert teams.get_team_owner_ids("admin") == ["admin", "member", "owner", "peer"]
    assert teams.get_team_owner_ids("invite") == ["invite"]
    assert teams.get_team_owner_ids("removed") == ["removed"]
    assert teams.get_team_owner_ids("disabled") == ["disabled"]


@pytest.mark.parametrize("actor", ["owner", "member", "peer", "admin", "outsider", "invite", "removed"])
def test_lists_and_direct_access_agree_and_never_share_personal_resources(resources, actor):
    r = resources
    owners = r.UserTenantService.get_team_owner_ids(actor)
    expected = {f"{user}-team" for user in owners if user in {"owner", "member", "peer", "outsider", "invite", "removed"}}
    if actor != "admin":
        expected.add(f"{actor}-me")
    chats, count = r.DialogService.get_by_tenant_ids(owners, actor, 0, 0, "create_time", True, "")
    agents, total = r.UserCanvasService.get_by_tenant_ids(owners, actor, 0, 0, "create_time", True, "")
    assert {row["id"] for row in chats} == expected
    assert {row["id"] for row in agents} == expected
    assert count == total == len(expected)
    for identity in (f"{user}-{permission}" for user in ("owner", "member", "peer", "outsider", "invite", "removed") for permission in ("me", "team")):
        assert bool(r.DialogService.get_accessible_chat(identity, actor)) == (identity in expected)
        assert r.UserCanvasService.accessible(identity, actor) == (identity in expected)


async def test_chat_owner_filter_is_authorized_and_applied_before_pagination(resources):
    r = resources
    r.request.args = MultiDict([("owner_ids", "owner"), ("page", "1"), ("page_size", "1")])
    result = await r.list_chats()
    assert result["data"]["total"] == 1
    assert [row["id"] for row in result["data"]["chats"]] == ["owner-team"]
    r.request.args = MultiDict([("owner_ids", "outsider")])
    assert (await r.list_chats())["code"] == RetCode.AUTHENTICATION_ERROR
    r.request.args = MultiDict([("id", "owner-me")])
    assert (await r.list_chats())["data"]["total"] == 0


async def test_agent_list_and_tags_include_peer_shared_agents(resources):
    r = resources
    result = r.list_agents("member")
    assert {row["id"] for row in result["data"]["canvas"]} == {"member-me", "member-team", "peer-team", "owner-team"}
    assert r.UserCanvasService.list_tags(r.UserTenantService.get_team_owner_ids("member"), "member") == {"member": 2, "peer": 1, "owner": 1}
    r.request.args = MultiDict([("owner_ids", "outsider")])
    assert r.list_agents("member")["code"] == RetCode.OPERATING_ERROR


async def test_revoked_membership_and_private_toggle_take_effect_immediately(resources):
    r = resources
    assert (await r.get_chat("owner-team"))["code"] == 0
    r.Dialog.update(permission="me").where(r.Dialog.id == "owner-team").execute()
    assert (await r.get_chat("owner-team"))["code"] == RetCode.AUTHENTICATION_ERROR
    r.UserTenant.update(status="0").where(r.UserTenant.id == "member-owner").execute()
    assert not r.UserCanvasService.accessible("owner-team", "member")
    assert not r.UserCanvasService.accessible("member-team", "owner")
    assert r.UserTenantService.get_team_owner_ids("member") == ["member"]


async def test_team_chat_use_does_not_expose_another_members_sessions_or_allow_chat_deletion(resources):
    r = resources
    r.request.args = MultiDict([("user_id", "peer")])
    result = await r.list_sessions("owner-team")
    assert [row["id"] for row in result["data"]] == ["session-member"]
    assert (await r.get_session("owner-team", "session-member"))["code"] == 0
    assert (await r.get_session("owner-team", "session-peer"))["code"] == RetCode.AUTHENTICATION_ERROR
    assert (await r.update_session("owner-team", "session-peer"))["code"] == RetCode.DATA_ERROR
    assert (await r.delete_session_message("owner-team", "session-peer", "message"))["code"] == RetCode.AUTHENTICATION_ERROR
    assert (await r.update_message_feedback("owner-team", "session-peer", "message"))["code"] == RetCode.AUTHENTICATION_ERROR
    assert (await r.create_session("owner-team"))["code"] == 0
    assert r.Conversation.get_by_id("new-session").user_id == "member"
    r.get_request_json.return_value = {"delete_all": True}
    assert (await r.delete_sessions("owner-team"))["code"] == 0
    assert {row.id for row in r.Conversation.select()} == {"session-owner", "session-peer"}
    assert (await r.delete_chat("owner-team"))["code"] == RetCode.AUTHENTICATION_ERROR
    r.get_request_json.return_value = {"chat_id": "owner-team"}
    assert (await r.bulk_delete_chats())["code"] == RetCode.AUTHENTICATION_ERROR
    assert r.Dialog.get_by_id("owner-team").status == "1"


@pytest.mark.parametrize("actor", ["owner", "member", "peer"])
async def test_chat_owner_and_teammates_only_access_their_own_sessions(resources, actor):
    r = resources
    r.current_user.id = actor
    r.request.args = MultiDict([("user_id", "peer" if actor != "peer" else "owner")])
    assert [item["id"] for item in (await r.list_sessions("owner-team"))["data"]] == [f"session-{actor}"]
    other = "member" if actor == "owner" else "owner"
    assert (await r.get_session("owner-team", f"session-{other}"))["code"] == RetCode.AUTHENTICATION_ERROR
    r.get_request_json.return_value = {"name": "forged"}
    assert (await r.update_session("owner-team", f"session-{other}"))["code"] == RetCode.DATA_ERROR


@pytest.mark.parametrize("actor", ["owner", "member", "peer"])
async def test_agent_owner_and_teammates_only_access_their_own_sessions(resources, actor):
    r = resources
    other = "member" if actor == "owner" else "owner"
    total, rows = r.API4ConversationService.get_list("owner-team", actor, 1, 30, "create_time", True, user_id="external-user")
    assert total == 1
    assert [row["id"] for row in rows] == [f"agent-session-{actor}"]
    assert r.API4ConversationService.get_names("owner-team", "external-user", actor) == [{"id": f"agent-session-{actor}", "name": actor}]
    assert r.get_agent_session(agent_id="owner-team", session_id=f"agent-session-{actor}", tenant_id=actor)["code"] == 0
    assert r.get_agent_session(agent_id="owner-team", session_id=f"agent-session-{other}", tenant_id=actor)["code"] == RetCode.DATA_ERROR
    assert r.delete_agent_session_item(agent_id="owner-team", session_id=f"agent-session-{other}", tenant_id=actor)["code"] == RetCode.DATA_ERROR
    assert not r.API4ConversationService.get_owned_session("legacy-unattributed", "owner-team", actor)[0]
    assert r.API4ConversationService.get_session_for_message("owner-team", f"message-{actor}", actor)
    assert not r.API4ConversationService.get_session_for_message("owner-team", f"message-{other}", actor)
    assert (await r.get_agent_logs(agent_id="owner-team", message_id=f"message-{other}", tenant_id=actor))["code"] == RetCode.DATA_ERROR
    r.get_request_json.return_value = {"delete_all": True}
    assert (await r.delete_agent_session(agent_id="owner-team", tenant_id=actor))["code"] == 0
    assert not r.API4Conversation.get_or_none(r.API4Conversation.id == f"agent-session-{actor}")
    assert r.API4Conversation.get_or_none(r.API4Conversation.id == f"agent-session-{other}")


@pytest.mark.parametrize("selector", ["session_id", "id", "metadata", "new"])
async def test_openai_authors_and_revoked_members_cannot_bypass_authorization(resources, selector):
    r = resources
    request = {"agent_id": "owner-team", "openai-compatible": True, "messages": [{"role": "user", "content": "question"}], "stream": False}
    if selector == "metadata":
        request["metadata"] = {"id": "agent-session-member"}
    elif selector != "new":
        request[selector] = "agent-session-member"
    r.get_request_json.return_value = request
    result = await r.agent_chat_completion("member")
    assert result["actor"] == "member"
    assert result["session_id"] == (None if selector == "new" else "agent-session-member")
    if selector != "new":
        assert (await r.agent_chat_completion("owner"))["code"] == RetCode.DATA_ERROR
    r.UserTenant.delete().where(r.UserTenant.id == "member-owner").execute()
    assert (await r.agent_chat_completion("member"))["code"] == RetCode.OPERATING_ERROR
    assert not r.API4ConversationService.get_owned_session("agent-session-member", "peer-team", "member")[0]


@pytest.mark.parametrize("selectors", [{"session_id": "agent-session-member", "id": "agent-session-owner"}, {"id": []}, {"metadata": "invalid"}])
async def test_openai_rejects_ambiguous_or_invalid_session_selectors(resources, selectors):
    r = resources
    r.get_request_json.return_value = {"agent_id": "owner-team", "openai-compatible": True, "messages": [{"role": "user", "content": "question"}], **selectors}
    assert (await r.agent_chat_completion("member"))["code"] == RetCode.DATA_ERROR


async def test_completion_service_preserves_external_identity_and_enforces_authenticated_creator(resources):
    r = resources
    ns = r.namespace

    class Canvas:
        error = ""

        def __init__(self, *args, **kwargs):
            pass

        def reset(self):
            pass

        async def run(self, **kwargs):
            yield {"event": "message", "message_id": "runtime-message", "data": {"content": "Synthetic answer"}}

        def close(self):
            pass

        def get_reference(self):
            return {}

        def __str__(self):
            return "{}"

    ns.update(Canvas=Canvas, uuid4=uuid4, time=SimpleNamespace(time=lambda: 1), UserCanvasVersionService=SimpleNamespace(get_latest_version_title=lambda *_args, **_kwargs: "version"))
    load_definitions("api/db/services/canvas_service.py", ["completion"], ns)
    completion = ns["completion"]
    assert [event async for event in completion("member", "owner-team", user_id="external-user", created_by="owner")]
    created = r.API4Conversation.get_by_id("new-session")
    assert created.created_by == "member"
    assert created.user_id == "external-user"
    assert r.API4ConversationService.get_session_for_message("owner-team", "runtime-message", "member").id == "new-session"
    assert not r.API4ConversationService.get_session_for_message("owner-team", "runtime-message", "owner")
    assert [event async for event in completion("member", "owner-team", session_id="new-session")]
    with pytest.raises(LookupError, match="Session not found"):
        _ = [event async for event in completion("owner", "owner-team", session_id="new-session")]
    r.UserTenant.delete().where(r.UserTenant.id == "member-owner").execute()
    with pytest.raises(PermissionError):
        _ = [event async for event in completion("member", "owner-team", session_id="new-session")]


async def test_beta_logs_cannot_read_teammate_history_or_survive_membership_revocation(resources, monkeypatch):
    r = resources
    ns = r.namespace
    ns.update(_get_sdk_authorization_token=lambda: "synthetic-token", logger=logging, hashlib=SimpleNamespace(sha256=lambda _: SimpleNamespace(hexdigest=lambda: "fingerprint")))
    ns["APIToken"] = SimpleNamespace(query=lambda **_: [SimpleNamespace(dialog_id="owner-team", tenant_id="member")])
    cache_reads = []
    redis_module = ModuleType("rag.utils.redis_conn")
    redis_module.REDIS_CONN = SimpleNamespace(get=lambda key: cache_reads.append(key) or b'{"synthetic": true}')
    monkeypatch.setitem(sys.modules, redis_module.__name__, redis_module)
    load_definitions("api/apps/restful_apis/bot_api.py", ["agent_bot_logs"], ns)
    logs = ns["agent_bot_logs"]
    assert (await logs("share", "message-owner"))["code"] == RetCode.DATA_ERROR
    assert cache_reads == []
    assert (await logs("share", "message-member"))["data"] == {"synthetic": True}
    assert len(cache_reads) == 1
    r.UserTenant.delete().where(r.UserTenant.id == "member-owner").execute()
    assert (await logs("share", "message-member"))["code"] == RetCode.DATA_ERROR
    assert len(cache_reads) == 1


def test_unknown_session_authorship_is_never_inferred_from_external_user_id(resources):
    r = resources
    assert r.API4Conversation.get_by_id("legacy-unattributed").created_by is None
    assert not r.API4ConversationService.get_owned_session("legacy-unattributed", "owner-team", "member")[0]
    assert not r.API4ConversationService.get_owned_session("legacy-unattributed", "owner-team", None)[0]
    assert r.API4ConversationService.get_list("owner-team", None, 1, 30, "create_time", True) == (0, [])
    assert not r.API4ConversationService.get_session_for_message("owner-team", "message", "member")


@pytest.mark.parametrize("permission", [None, "", "public", [], {}])
def test_invalid_permission_is_rejected(resources, permission):
    assert resources._validate_permission({"permission": permission})


def test_existing_and_new_chats_default_to_private(resources):
    assert resources.Dialog._meta.fields["permission"].default == "me"
    assert resources.insert("Dialog", id="default-private", tenant_id="owner").permission == "me"


async def test_chat_visibility_choice_persists_through_create_put_and_patch(resources):
    r = resources
    r.get_request_json.return_value = {"name": "Shared chat", "permission": "team"}
    result = await r.create()
    assert result["data"]["permission"] == "team"
    assert r.Dialog.get_by_id("new-session").permission == "team"
    r.get_request_json.return_value = {"permission": "me"}
    assert (await r.update_chat("new-session"))["data"]["permission"] == "me"
    assert not r.DialogService.get_accessible_chat("new-session", "peer")
    r.get_request_json.return_value = {"permission": "team"}
    assert (await r.patch_chat("new-session"))["data"]["permission"] == "team"
    assert r.DialogService.get_accessible_chat("new-session", "peer")
    r.get_request_json.return_value = {"permission": "public"}
    assert (await r.patch_chat("new-session"))["code"] == RetCode.DATA_ERROR
    assert r.Dialog.get_by_id("new-session").permission == "team"
    assert (await r.patch_chat("owner-team"))["code"] == RetCode.AUTHENTICATION_ERROR


async def test_agent_collaboration_preserves_owner_and_only_owner_changes_visibility(resources):
    r = resources
    r.get_request_json.return_value = {"permission": "team", "user_id": "member", "id": "stolen", "description": "Updated by teammate"}
    assert (await r.update_agent("owner-team", "member"))["code"] == 0
    assert r.UserCanvas.get_by_id("owner-team").user_id == "owner"
    assert not r.UserCanvas.get_or_none(r.UserCanvas.id == "stolen")
    r.get_request_json.return_value = {"permission": "me"}
    assert (await r.update_agent("owner-team", "member"))["code"] == RetCode.OPERATING_ERROR
    assert r.UserCanvas.get_by_id("owner-team").permission == "team"
    assert (await r.update_agent("owner-team", "owner"))["code"] == 0
    assert not r.UserCanvasService.accessible("owner-team", "member")
    r.get_request_json.return_value = {"permission": "public"}
    assert (await r.update_agent("owner-team", "owner"))["code"] == RetCode.DATA_ERROR


@pytest.mark.parametrize("table,column,expected", [("dialog", "permission", "me"), ("api_4_conversation", "created_by", None)])
def test_privacy_migrations_preserve_existing_rows_and_are_repeatable(tmp_path, table, column, expected):
    db = peewee.SqliteDatabase(tmp_path / "upgrade.sqlite")
    db.execute_sql(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, name TEXT)")
    db.execute_sql(f"INSERT INTO {table} VALUES ('old', 'Existing private history')")
    namespace = {**vars(peewee), "migrate": migrate, "logging": logging, "settings": SimpleNamespace(DATABASE_TYPE="sqlite"), "migrator": SqliteMigrator(db)}
    load_definitions("api/db/db_models.py", ["alter_db_add_column"], namespace)
    tree = ast.parse((ROOT / "api/db/db_models.py").read_text(encoding="utf-8"))
    statement = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "alter_db_add_column"
        and [getattr(arg, "value", None) for arg in node.value.args[1:3]] == [table, column]
    )
    code = compile(ast.Module(body=[statement], type_ignores=[]), "chat-permission-migration", "exec")
    exec(code, namespace)
    exec(code, namespace)
    assert db.execute_sql(f"SELECT id, name, {column} FROM {table}").fetchall() == [("old", "Existing private history", expected)]
    db.execute_sql(f"INSERT INTO {table} (id, name) VALUES ('new', 'Insert without the new column')")
    assert db.execute_sql(f"SELECT {column} FROM {table} WHERE id = 'new'").fetchone() == (expected,)
    db.close()


def test_chat_permission_default_repairs_existing_column_without_changing_sharing(tmp_path):
    db = peewee.SqliteDatabase(tmp_path / "existing-column.sqlite")
    db.execute_sql("CREATE TABLE dialog (id TEXT PRIMARY KEY, permission TEXT NOT NULL)")
    db.execute_sql("INSERT INTO dialog VALUES ('shared', 'team')")
    tree = ast.parse((ROOT / "api/db/db_models.py").read_text(encoding="utf-8"))
    statement = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "migrate"
        and node.value.args
        and isinstance(node.value.args[0], ast.Call)
        and getattr(node.value.args[0].func, "attr", None) == "add_column_default"
        and [getattr(arg, "value", None) for arg in node.value.args[0].args] == ["dialog", "permission", "me"]
    )
    code = compile(ast.Module(body=[statement], type_ignores=[]), "chat-permission-default-migration", "exec")
    namespace = {"migrate": migrate, "migrator": SqliteMigrator(db)}
    exec(code, namespace)
    exec(code, namespace)
    db.execute_sql("INSERT INTO dialog (id) VALUES ('private')")
    assert db.execute_sql("SELECT id, permission FROM dialog ORDER BY id").fetchall() == [("private", "me"), ("shared", "team")]
    db.close()


@pytest.mark.parametrize("stream", [False, True])
async def test_workflow_runtime_logs_are_bound_to_the_private_session_once_per_turn(resources, monkeypatch, stream):
    r = resources
    ns = r.namespace
    messages = [{"id": "workflow-turn", "role": "user", "content": "question"}]
    r.insert("API4Conversation", id="workflow-session", dialog_id="owner-team", user_id="external-user", created_by="member", message=messages, source="workflow")
    conv = r.API4Conversation.get_by_id("workflow-session").to_dict()

    class Canvas:
        async def run(self, **kwargs):
            yield {"event": "message", "message_id": "runtime-workflow-turn", "data": {"content": "answer"}}
            yield {"event": "message_end", "message_id": "runtime-workflow-turn", "data": {}}

        def cancel_task(self):
            raise AssertionError("Synthetic workflow must succeed")

        def __str__(self):
            return "{}"

    calls = []
    update = r.API4ConversationService.update_by_id

    def capture_update(identity, values):
        calls.append(set(values))
        return update(identity, values)

    monkeypatch.setattr(r.API4ConversationService, "update_by_id", capture_update)
    ns.update(
        copy=SimpleNamespace(deepcopy=deepcopy),
        time=SimpleNamespace(time=lambda: 1),
        CanvasReplicaService=SimpleNamespace(commit_after_run=lambda **_: True),
        _normalize_agent_reference_entry=lambda reference: reference,
        _build_sse_response=lambda body: SimpleNamespace(body=body),
    )
    load_definitions("api/apps/restful_apis/agent_api.py", ["_canvas_json_default", "_run_workflow_session"], ns)
    result = await ns["_run_workflow_session"](
        tenant_id="member",
        agent_id="owner-team",
        workflow_conv=conv,
        canvas=Canvas(),
        query="question",
        files=[],
        inputs={},
        user_id="external-user",
        session_id="workflow-session",
        custom_header="",
        canvas_title="agent",
        canvas_category="agent_canvas",
        return_trace=False,
        stream=stream,
    )
    if stream:
        assert [item async for item in result.body]
    else:
        assert result["code"] == 0
    assert calls.count({"message"}) == 1
    assert r.API4ConversationService.get_session_for_message("owner-team", "runtime-workflow-turn", "member").id == "workflow-session"
    assert r.API4ConversationService.get_session_for_message("owner-team", "runtime-workflow-turn", "owner") is None
