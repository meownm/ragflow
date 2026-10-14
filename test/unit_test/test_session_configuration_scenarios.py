"""Restored SDK scenarios cross real validation handlers with disposable storage."""

import ast
import asyncio
from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from ragflow_sdk import RAGFlow
from ragflow_sdk.modules.chat import Chat
from ragflow_sdk.modules.session import Session


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test/testcases"))
try:
    chat_create = importlib.import_module("test.testcases.test_sdk_api.test_chat_assistant_management.test_create_chat_assistant")
    session_create = importlib.import_module("test.testcases.test_sdk_api.test_session_management.test_create_session_with_chat_assistant")
    session_update = importlib.import_module("test.testcases.test_sdk_api.test_session_management.test_update_session_with_chat_assistant")
finally:
    sys.path.pop(0)


@pytest.fixture
def handlers():
    path = ROOT / "api/apps/restful_apis/chat_api.py"
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"_validate_name", "create_session", "update_session"}]
    for function in functions:
        function.decorator_list = []
    stored = {"id": "session", "dialog_id": "chat", "name": "existing"}
    writes = []

    async def owned(_chat):
        return True

    async def request_json():
        return deepcopy(namespace["payload"])

    def save(**values):
        writes.append(deepcopy(values))
        stored.update(values)

    def update(_identifier, values):
        writes.append(deepcopy(values))
        stored.update(values)
        return 1

    conversation = SimpleNamespace(
        save=save,
        update_by_id=update,
        query=lambda **_: True,
        get_by_id=lambda _: (True, SimpleNamespace(to_dict=lambda: deepcopy(stored))),
        model=SimpleNamespace(_meta=SimpleNamespace(fields=dict.fromkeys(stored))),
    )
    namespace = {
        "_ensure_owned_chat": owned,
        "get_request_json": request_json,
        "DialogService": SimpleNamespace(get_by_id=lambda _: (True, SimpleNamespace(prompt_config={"prologue": "hello"}))),
        "ConversationService": conversation,
        "get_uuid": lambda: "session",
        "current_user": SimpleNamespace(id="tenant"),
        "_build_session_response": lambda data: {**data, "chat_id": data["dialog_id"]},
        "get_data_error_result": lambda **kwargs: {"code": 102, **kwargs},
        "get_json_result": lambda **kwargs: {"code": 0, **kwargs},
        "RetCode": SimpleNamespace(AUTHENTICATION_ERROR=109),
        "server_error_response": lambda error: (_ for _ in ()).throw(error),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace, stored, writes


def cases(cls, method):
    mark = next(mark for mark in getattr(cls, method).pytestmark if mark.name == "parametrize")
    return [pytest.param(*entry.values, id=str(index)) if hasattr(entry, "values") else pytest.param(*entry, id=str(index)) for index, entry in enumerate(mark.args[1])]


@pytest.mark.parametrize("name,expected", cases(chat_create.TestChatAssistantCreate, "test_name"))
def test_restored_chat_name_scenario(name, expected, handlers, monkeypatch):
    namespace, _, writes = handlers
    client = RAGFlow("disposable", "http://no-network.invalid")
    names = set()

    def post(_path, payload, **_kwargs):
        checked, error = namespace["_validate_name"](payload["name"])
        if not error and checked.lower() in names:
            error = "Duplicated chat name in creating chat."
        if error:
            return SimpleNamespace(json=lambda: {"code": 102, "message": error})
        names.add(checked.lower())
        writes.append(checked)
        return SimpleNamespace(json=lambda: {"code": 0, "data": {"id": "chat", "name": checked}})

    monkeypatch.setattr(client, "post", post)
    chat_create.TestChatAssistantCreate().test_name(client, name, expected)
    if not isinstance(name, str) or len(name) > 255 or name == "":
        assert not writes


@pytest.mark.parametrize("name,expected", cases(session_create.TestSessionWithChatAssistantCreate, "test_name"))
def test_restored_session_create_scenario(name, expected, handlers, monkeypatch):
    namespace, stored, writes = handlers
    client = RAGFlow("disposable", "http://no-network.invalid")
    chat = Chat(client, {"id": "chat"})

    def post(_path, payload, **_kwargs):
        namespace["payload"] = payload
        with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
            result = runner.run(namespace["create_session"]("chat"))
        return SimpleNamespace(json=lambda: result)

    monkeypatch.setattr(client, "post", post)
    monkeypatch.setattr(client, "get", lambda *_args: SimpleNamespace(json=lambda: {"code": 0, "data": [namespace["_build_session_response"](stored)]}))
    session_create.TestSessionWithChatAssistantCreate().test_name((None, None, [chat]), name, expected)
    if not isinstance(name, str) or name == "":
        assert not writes


@pytest.mark.parametrize("payload,expected", cases(session_update.TestSessionWithChatAssistantUpdate, "test_name"))
def test_restored_session_update_scenario(payload, expected, handlers, monkeypatch):
    namespace, stored, writes = handlers
    client = RAGFlow("disposable", "http://no-network.invalid")
    chat = Chat(client, {"id": "chat"})
    session = Session(client, {"id": "session", "chat_id": "chat"})

    def patch(_path, value):
        namespace["payload"] = value
        with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
            result = runner.run(namespace["update_session"]("chat", "session"))
        return SimpleNamespace(json=lambda: result)

    monkeypatch.setattr(client, "patch", patch)
    monkeypatch.setattr(client, "get", lambda *_args: SimpleNamespace(json=lambda: {"code": 0, "data": [namespace["_build_session_response"](stored)]}))
    session_update.TestSessionWithChatAssistantUpdate().test_name((chat, [session]), payload, expected)
    if expected:
        assert not writes and stored["name"] == "existing"


@pytest.mark.parametrize("value", [None, [], "invalid", 1])
def test_invalid_session_update_never_reaches_transport(value, monkeypatch):
    client = RAGFlow("disposable", "http://no-network.invalid")
    session = Session(client, {"id": "session", "chat_id": "chat"})
    monkeypatch.setattr(client, "patch", lambda *_args: pytest.fail("invalid payload reached HTTP transport"))
    with pytest.raises(ValueError, match="must be a dict"):
        session.update(value)
