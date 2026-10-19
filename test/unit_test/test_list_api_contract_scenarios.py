"""List scenarios exercise SDK type checks and actual handlers without a live tenant."""

import ast
from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from ragflow_sdk import RAGFlow
from ragflow_sdk.modules.chat import Chat
from ragflow_sdk.modules.dataset import DataSet
from werkzeug.datastructures import MultiDict

from api.utils.pagination_utils import validate_rest_list_args


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test/testcases"))
sys.path.insert(0, str(ROOT / "test/testcases/test_web_api"))
try:
    sdk_chat = importlib.import_module("test.testcases.test_sdk_api.test_chat_assistant_management.test_list_chat_assistants")
    sdk_document = importlib.import_module("test.testcases.test_sdk_api.test_file_management_within_dataset.test_list_documents")
    sdk_session = importlib.import_module("test.testcases.test_sdk_api.test_session_management.test_list_sessions_with_chat_assistant")
    web_document = importlib.import_module("test.testcases.test_web_api.test_document_app.test_list_documents")
finally:
    sys.path.pop(0)
    sys.path.pop(0)


def _load_handler(filename, name, namespace):
    """Execute the production handler body; replace only framework/storage boundaries."""
    path = ROOT / "api/apps/restful_apis" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def _cases():
    cases = []
    for lane, cls in (
        ("sdk_chat", sdk_chat.TestChatAssistantsList),
        ("sdk_document", sdk_document.TestDocumentsList),
        ("sdk_session", sdk_session.TestSessionsWithChatAssistantList),
        ("web_document", web_document.TestDocumentsList),
    ):
        for name in ("test_page", "test_page_size", "test_orderby", "test_desc"):
            method = getattr(cls, name)
            mark = next(mark for mark in method.pytestmark if mark.name == "parametrize")
            for index, values in enumerate(mark.args[1]):
                cases.append(pytest.param(lane, method, values, id=f"{lane}-{name}-{index}"))
    return cases


@pytest.fixture
def list_handlers():
    rows = [{"id": str(index), "name": f"document-{index}", "create_time": index, "update_time": 5 - index, "size": index, "chunk_num": index} for index in range(5)]
    calls = []
    service = SimpleNamespace(model=SimpleNamespace(_meta=SimpleNamespace(fields={key: object() for key in rows[0]})))

    def fetch(_identity, page, size, orderby, desc, *_args, **_kwargs):
        calls.append((page, size, orderby, desc))
        result = sorted(deepcopy(rows), key=lambda row: row[orderby], reverse=desc)
        if page and size:
            result = result[(page - 1) * size : page * size]
        return result, len(rows)

    service.get_by_kb_id = fetch
    service.get_by_tenant_ids = lambda _owners, identity, *args, **kwargs: fetch(identity, *args, **kwargs)

    def fetch_sessions(_chat, page, size, orderby, desc, *_args):
        calls.append((page, size, orderby, desc))
        result = sorted(deepcopy(rows), key=lambda row: row[orderby], reverse=desc)
        if size > 0:
            first = (page - 1 if page > 0 else page) * size
            result = result[first : first + size]
        return result

    conversations = SimpleNamespace(model=service.model, get_list=fetch_sessions)
    ret = SimpleNamespace(SUCCESS=0, DATA_ERROR=102)
    namespace = {
        "validate_rest_list_args": validate_rest_list_args,
        "DocumentService": service,
        "DialogService": service,
        "UserTenantService": SimpleNamespace(get_team_owner_ids=lambda user_id: [user_id]),
        "ConversationService": conversations,
        "RetCode": ret,
        "current_user": SimpleNamespace(id="tenant"),
        "_parse_run_status_filter": lambda _q: ([], []),
        "_parse_doc_id_filter_with_metadata": lambda _q, _dataset: (0, "", None, False),
        "_build_chat_response": lambda row: row,
        "get_json_result": lambda **kwargs: {"code": 0, **kwargs},
        "get_data_error_result": lambda **kwargs: {"code": 102, **kwargs},
        "server_error_response": lambda _exc: {"code": 100, "message": "Internal server error"},
    }

    async def thread_pool_exec(func, *args, **kwargs):
        return func(*args, **kwargs)

    namespace["thread_pool_exec"] = thread_pool_exec

    async def ensure_owned_chat(_chat):
        return True

    namespace["_ensure_owned_chat"] = ensure_owned_chat

    async def accessible_chat(chat_id):
        return [SimpleNamespace(tenant_id="tenant")] if await namespace["_ensure_owned_chat"](chat_id) else []

    namespace["_ensure_accessible_chat"] = accessible_chat
    _load_handler("chat_api.py", "_session_scope", namespace)
    document = _load_handler("document_api.py", "_get_docs_with_request", namespace)
    chat = _load_handler("chat_api.py", "list_chats", namespace)
    _load_handler("chat_api.py", "_build_session_response", namespace)
    session = _load_handler("chat_api.py", "list_sessions", namespace)

    def args(params):
        return MultiDict((key, str(value)) for key, value in params.items() if value is not None)

    def documents(params):
        code, message, docs, total = document(SimpleNamespace(args=args(params)), "dataset")
        return {"code": code, "message": message, "data": {"docs": docs, "total": total}}

    async def chats(params):
        namespace["request"] = SimpleNamespace(args=args(params))
        return await chat()

    async def sessions(params):
        namespace["request"] = SimpleNamespace(args=args(params))
        return await session("chat")

    return SimpleNamespace(documents=documents, chats=chats, sessions=sessions, calls=calls, namespace=namespace)


@pytest.mark.parametrize("lane,method,values", _cases())
def test_restored_list_scenario(lane, method, values, list_handlers, monkeypatch):
    import asyncio

    client = RAGFlow(api_key="disposable", base_url="http://no-network.invalid")
    transport_calls = []

    def get(path, params):
        transport_calls.append((path, params))
        if path == "/chats":
            with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
                result = runner.run(list_handlers.chats(params))
        elif path.endswith("/sessions"):
            with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
                result = runner.run(list_handlers.sessions(params))
        else:
            result = list_handlers.documents(params)
        return SimpleNamespace(json=lambda: deepcopy(result))

    monkeypatch.setattr(client, "get", get)
    if lane == "sdk_chat":
        method(sdk_chat.TestChatAssistantsList(), client, *values)
    elif lane == "sdk_document":
        dataset = DataSet(client, {"id": "dataset"})
        method(sdk_document.TestDocumentsList(), (dataset, []), *values)
    elif lane == "sdk_session":
        method(sdk_session.TestSessionsWithChatAssistantList(), (Chat(client, {"id": "chat"}), []), *values)
    else:
        monkeypatch.setattr(web_document, "list_documents", lambda _auth, params: list_handlers.documents(params))
        method(web_document.TestDocumentsList(), None, ("dataset", []), *values)
    if "not instance of" in values:
        assert transport_calls == []
        assert list_handlers.calls == []
    elif any(isinstance(value, str) and value.startswith(("page", "Invalid orderby")) for value in values):
        assert list_handlers.calls == []
    else:
        assert len(list_handlers.calls) == 1


@pytest.mark.parametrize("lane", ["document", "chat", "session"])
@pytest.mark.parametrize(
    "params,message",
    [
        ({"page": "a"}, "page must be an integer"),
        ({"page_size": "a"}, "page_size must be an integer"),
        ({"page": "-1"}, "page must be greater than or equal to 0"),
        ({"page_size": "-1"}, "page_size must be greater than or equal to 0"),
        ({"page_size": "101"}, "page_size must be less than or equal to 100"),
        ({"orderby": "unknown"}, "Invalid orderby field: unknown"),
        ({"orderby": "delete"}, "Invalid orderby field: delete"),
    ],
)
async def test_invalid_rest_inputs_fail_before_storage(lane, params, message, list_handlers):
    result = list_handlers.documents(params) if lane == "document" else await getattr(list_handlers, lane + "s")(params)
    assert result["code"] == 102
    assert result["message"] == message
    assert list_handlers.calls == []


@pytest.mark.parametrize("lane", ["document", "chat", "session"])
@pytest.mark.parametrize("params", [{"page": "0", "page_size": "2"}, {"page": "2", "page_size": "0"}, {"orderby": "size", "desc": "false"}])
async def test_supported_storage_sort_fields_and_zero_pagination(lane, params, list_handlers):
    result = list_handlers.documents(params) if lane == "document" else await getattr(list_handlers, lane + "s")(params)
    assert result["code"] == 0
    rows = result["data"] if lane == "session" else result["data"]["docs" if lane == "document" else "chats"]
    expected_count = (2 if params.get("page") == "0" else 0 if params.get("page_size") == "0" else 5) if lane == "session" else 5
    assert len(rows) == expected_count
    if "orderby" in params:
        assert [row["size"] for row in rows] == list(range(5))


@pytest.mark.parametrize("lane", ["sdk_chat", "sdk_document", "sdk_session", "web_document"])
def test_name_order_scenario_rejects_wrong_order(lane, list_handlers, monkeypatch):
    import asyncio

    original = list_handlers.documents

    def wrong_order(params):
        result = original(params)
        result["data"]["docs"].reverse()
        return result

    client = RAGFlow(api_key="disposable", base_url="http://no-network.invalid")

    def get(path, params):
        if path == "/chats":
            with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
                result = runner.run(list_handlers.chats(params))
            result["data"]["chats"].reverse()
        elif path.endswith("/sessions"):
            with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
                result = runner.run(list_handlers.sessions(params))
            result["data"].reverse()
        else:
            result = wrong_order(params)
        return SimpleNamespace(json=lambda: result)

    monkeypatch.setattr(client, "get", get)
    with pytest.raises(AssertionError):
        if lane == "sdk_chat":
            sdk_chat.TestChatAssistantsList().test_orderby(client, {"orderby": "name", "desc": False}, "")
        elif lane == "sdk_document":
            sdk_document.TestDocumentsList().test_orderby((DataSet(client, {"id": "dataset"}), []), {"orderby": "name", "desc": False}, "")
        elif lane == "sdk_session":
            sdk_session.TestSessionsWithChatAssistantList().test_orderby((Chat(client, {"id": "chat"}), []), {"orderby": "name", "desc": False}, "")
        else:
            monkeypatch.setattr(web_document, "list_documents", lambda _auth, params: wrong_order(params))
            web_document.TestDocumentsList().test_orderby(
                None,
                ("dataset", []),
                {"orderby": "name", "desc": "false"},
                0,
                lambda result: [row["name"] for row in result["data"]["docs"]] == sorted(row["name"] for row in result["data"]["docs"]),
                "",
            )


async def test_session_list_rejects_foreign_chat_before_query_validation_or_storage(list_handlers):
    async def deny(_chat):
        return False

    list_handlers.namespace["_ensure_owned_chat"] = deny
    list_handlers.namespace["RetCode"].AUTHENTICATION_ERROR = 109
    result = await list_handlers.sessions({"page": "-1"})
    assert result == {"code": 109, "message": "No authorization.", "data": False}
    assert list_handlers.calls == []


def test_live_session_file_does_not_shadow_authentication_fixtures():
    assert not hasattr(sdk_session, "auth")
    assert not hasattr(sdk_session, "set_tenant_info")


@pytest.fixture
def session_update_handler(list_handlers):
    state = {"id": "session", "dialog_id": "chat", "user_id": "tenant", "name": "existing", "create_time": 1, "update_time": 2, "message": [], "reference": []}
    reads, writes = [], []

    def query(**filters):
        reads.append(filters)
        return [state] if filters == {"id": "session", "dialog_id": "chat", "user_id": "tenant"} else []

    def update(identity, values):
        writes.append((identity, deepcopy(values)))
        state.update(values)
        return True

    service = SimpleNamespace(
        model=SimpleNamespace(_meta=SimpleNamespace(fields={key: object() for key in state})),
        query=query,
        update_by_id=update,
        get_by_id=lambda _identity: (True, SimpleNamespace(to_dict=lambda: deepcopy(state))),
    )
    namespace = list_handlers.namespace
    namespace["ConversationService"] = service
    handler = _load_handler("chat_api.py", "update_session", namespace)

    async def invoke(payload):
        async def body():
            return deepcopy(payload)

        namespace["get_request_json"] = body
        return await handler("chat", "session")

    return SimpleNamespace(invoke=invoke, reads=reads, writes=writes, state=state, namespace=namespace)


@pytest.mark.parametrize("payload", [None, [], ["name"], "name", 1, False])
async def test_session_update_non_object_returns_data_error_without_storage(payload, session_update_handler):
    result = await session_update_handler.invoke(payload)
    assert result == {"code": 102, "message": "request must be an object"}
    assert session_update_handler.reads == []
    assert session_update_handler.writes == []


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"unknown": 1}, "Unknown session fields: unknown"),
        ({"z": 1, "a": 2, "name": "new"}, "Unknown session fields: a, z"),
        ({"messages": []}, "`messages` cannot be changed."),
        ({"message": []}, "`messages` cannot be changed."),
        ({"reference": []}, "`reference` cannot be changed."),
    ],
)
async def test_session_update_rejects_invalid_fields_without_write(payload, message, session_update_handler):
    before = deepcopy(session_update_handler.state)
    result = await session_update_handler.invoke(payload)
    assert result == {"code": 102, "message": message}
    assert session_update_handler.writes == []
    assert session_update_handler.state == before


async def test_session_update_supported_fields_and_identity_protection(session_update_handler):
    result = await session_update_handler.invoke({"name": " new ", "update_time": 3, "id": "foreign", "dialog_id": "foreign", "chat_id": "foreign", "user_id": "foreign"})
    assert result["code"] == 0
    assert session_update_handler.writes == [("session", {"name": "new", "update_time": 3})]
    assert session_update_handler.state["id"] == "session"
    assert session_update_handler.state["dialog_id"] == "chat"
    assert session_update_handler.state["user_id"] == "tenant"


async def test_session_update_unauthorized_does_not_read_body_or_write(session_update_handler):
    async def deny(_chat):
        return False

    session_update_handler.namespace["_ensure_owned_chat"] = deny
    session_update_handler.namespace["RetCode"].AUTHENTICATION_ERROR = 109
    result = await session_update_handler.invoke(None)
    assert result == {"code": 109, "message": "No authorization.", "data": False}
    assert session_update_handler.reads == []
    assert session_update_handler.writes == []
