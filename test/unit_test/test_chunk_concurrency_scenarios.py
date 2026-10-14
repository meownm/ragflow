"""Concurrent API scenarios must surface write errors and verify search visibility."""

import ast
from concurrent.futures import Future, as_completed
import importlib
from pathlib import Path
import sys
from threading import Lock
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test/testcases"))
sys.path.insert(0, str(ROOT / "test/testcases/test_web_api"))
try:
    sdk_add = importlib.import_module("test.testcases.test_sdk_api.test_chunk_management_within_dataset.test_add_chunk")
    sdk_update = importlib.import_module("test.testcases.test_sdk_api.test_chunk_management_within_dataset.test_update_chunk")
    common_module = sys.modules.get("common")
    try:
        sys.modules["common"] = importlib.import_module("test.testcases.test_sdk_api.common")
        sdk_list = importlib.import_module("test.testcases.test_sdk_api.test_chunk_management_within_dataset.test_list_chunks")
    finally:
        if common_module is None:
            sys.modules.pop("common", None)
        else:
            sys.modules["common"] = common_module
    sdk_memory = importlib.import_module("test.testcases.test_sdk_api.test_message_management.test_list_message")
    web_add = importlib.import_module("test.testcases.test_web_api.test_chunk_app.test_create_chunk")
    web_update = importlib.import_module("test.testcases.test_web_api.test_chunk_app.test_update_chunk")
    web_memory = importlib.import_module("test.testcases.test_web_api.test_message_app.test_list_message")
finally:
    sys.path.pop(0)
    sys.path.pop(0)


@pytest.mark.parametrize("lane", ["sdk_add", "sdk_update", "web_add", "web_update"])
@pytest.mark.parametrize("fault", [None, "write_error", "foreign_id", "stale_content", "delayed_visibility"])
def test_concurrent_scenario_rejects_errors_and_missing_evidence(lane, fault, monkeypatch):
    module = {"sdk_add": sdk_add, "sdk_update": sdk_update, "web_add": web_add, "web_update": web_update}[lane]
    state = {str(index): "original" for index in range(5)}
    initial = dict(state)
    lock = Lock()
    calls = {"write": 0, "read": 0}

    def wait_for(_timeout, _interval, error):
        def decorator(read):
            def poll():
                for _attempt in range(3):
                    if read() is True:
                        return True
                raise AssertionError(error)

            return poll

        return decorator

    monkeypatch.setattr(module, "wait_for", wait_for)

    def write(identity, content):
        with lock:
            calls["write"] += 1
            if fault == "write_error":
                raise RuntimeError("injected write failure")
            if identity is None:
                identity = "created-" + content.rsplit(" ", 1)[1]
            state[identity] = content
            return identity

    def snapshot():
        with lock:
            calls["read"] += 1
            result = dict(state)
            if calls["write"]:
                if fault == "delayed_visibility" and calls["read"] == 2:
                    result = dict(initial)
                elif fault == "foreign_id":
                    value = result.pop(next(iter(result)))
                    result["foreign"] = value
                elif fault == "stale_content":
                    result = {identity: "stale" for identity in result}
            return result

    def sdk_chunks(**_kwargs):
        return [SimpleNamespace(id=identity, content=content) for identity, content in snapshot().items()]

    def sdk_write(*, content):
        return SimpleNamespace(id=write(None, content), content=content)

    def web_chunks(*_args, **_kwargs):
        result = snapshot()
        return {"code": 0, "data": {"doc": {"chunk_count": len(result)}, "chunks": [{"id": identity, "content": content} for identity, content in result.items()]}}

    def web_write(_auth, _dataset, _document, payload):
        identity = write(None, payload["content"])
        return {"code": 0, "data": {"chunk": {"id": identity}}}

    def web_change(_auth, _dataset, _document, identity, payload):
        write(identity, payload["content"])
        return {"code": 0}

    document = SimpleNamespace(list_chunks=sdk_chunks, add_chunk=sdk_write)
    chunks = [SimpleNamespace(id=str(index), update=lambda payload, identity=str(index): write(identity, payload["content"])) for index in range(4)]
    monkeypatch.setattr(web_add, "add_chunk", web_write)
    monkeypatch.setattr(web_add, "list_chunks", web_chunks)
    monkeypatch.setattr(web_update, "update_chunk", web_change)
    monkeypatch.setattr(web_update, "list_chunks", web_chunks)

    def scenario():
        if lane == "sdk_add":
            sdk_add.TestAddChunk().test_concurrent_add_chunk((None, document))
        elif lane == "sdk_update":
            sdk_update.TestUpdatedChunk().test_concurrent_update_chunk((None, document, chunks))
        elif lane == "web_add":
            web_add.TestAddChunk().test_concurrent_add_chunk(None, ("dataset", "document"))
        else:
            web_update.TestUpdateChunk().test_concurrent_update_chunk(None, ("dataset", "document", [chunk.id for chunk in chunks]))

    # Creation validates exact identities; update additionally validates persisted content.
    rejected = fault in {"write_error", "foreign_id"} or fault == "stale_content" and lane.endswith("update")
    if rejected:
        with pytest.raises(RuntimeError if fault == "write_error" else AssertionError):
            scenario()
    else:
        scenario()
    assert calls["write"] == 50
    if fault == "delayed_visibility":
        assert calls["read"] >= 3


@pytest.mark.parametrize("lane", ["sdk", "web"])
@pytest.mark.parametrize("engine", ["elasticsearch", "infinity"])
@pytest.mark.parametrize("wrong_filter", [False, True])
def test_memory_exact_and_substring_scenarios_execute_on_both_engines(lane, engine, wrong_filter, monkeypatch):
    module = sdk_memory if lane == "sdk" else web_memory
    monkeypatch.setattr(module, "get_doc_engine", lambda *_args: engine)
    session_id = "session-1234"
    query_calls = []

    def messages(params):
        keyword = params["keywords"]
        query_calls.append(keyword)
        if keyword == session_id:
            matches = [{"session_id": session_id}]
        else:
            substring_matches = engine != "infinity"
            if wrong_filter:
                substring_matches = not substring_matches
            matches = [{"session_id": session_id}] if substring_matches else []
        return {"messages": {"message_list": matches}}

    monkeypatch.setattr(sdk_memory, "Memory", lambda *_args: SimpleNamespace(list_memory_messages=lambda **params: messages(params)))
    monkeypatch.setattr(web_memory, "list_memory_message", lambda _auth, _memory, *, params: {"code": 0, "data": messages(params)})
    scenario = module.TestMessageList()
    scenario.memory_id = "memory"
    scenario.session_ids = [session_id]
    if wrong_filter:
        with pytest.raises(AssertionError):
            scenario.test_search_keyword(None)
    else:
        scenario.test_search_keyword(None)
    assert query_calls == [session_id, session_id[1:-1]]


def test_production_infinity_session_filter_is_exact_equality():
    path = ROOT / "common/doc_store/infinity_conn_base.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "InfinityConnectionBase")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "equivalent_condition_to_str")
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    connection = SimpleNamespace(field_keyword=lambda _field: False)
    assert namespace[method.name](connection, {"session_id": "ession-123"}) == "session_id='ession-123'"


@pytest.mark.parametrize("engine", ["elasticsearch", "infinity"])
def test_keyword_scenario_requires_matching_result_on_both_engines(engine, monkeypatch):
    monkeypatch.setattr(sdk_list, "get_doc_engine", lambda *_args: engine)
    document = SimpleNamespace(rag=None, list_chunks=lambda **_params: [SimpleNamespace(id="matching")])
    sdk_list.TestChunksList().test_keywords((None, document, []), {"keywords": "ragflow"}, 1)
    document.list_chunks = lambda **_params: [SimpleNamespace(id=str(index)) for index in range(5)]
    with pytest.raises(AssertionError):
        sdk_list.TestChunksList().test_keywords((None, document, []), {"keywords": "ragflow"}, 1)


def _remaining_concurrent_scenarios():
    covered = {
        "test_sdk_api/test_chunk_management_within_dataset/test_add_chunk.py",
        "test_sdk_api/test_chunk_management_within_dataset/test_update_chunk.py",
        "test_sdk_api/test_file_management_within_dataset/test_upload_documents.py",
        "test_web_api/test_chunk_app/test_create_chunk.py",
        "test_web_api/test_chunk_app/test_update_chunk.py",
    }
    cases = []
    for suite in ("test_sdk_api", "test_web_api"):
        for path in sorted((ROOT / "test/testcases" / suite).rglob("*.py")):
            relative = path.relative_to(ROOT / "test/testcases").as_posix()
            if relative in covered:
                continue
            text = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.FunctionDef) and ("concurrent" in node.name or node.name == "test_capability"):
                    faults = ["exception"]
                    if suite == "test_web_api" or relative.endswith("test_memory_management/test_list_memory.py"):
                        faults.append("rejected_response")
                    for fault in faults:
                        cases.append(pytest.param(relative, node.name, fault, id=f"{relative}::{node.name}-{fault}"))
    return cases


class _DisposableScenarioResource:
    id = "disposable"

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: _DisposableScenarioResource()


@pytest.mark.parametrize("relative,name,fault", _remaining_concurrent_scenarios())
def test_mandatory_concurrent_scenario_surfaces_a_failed_request(relative, name, fault, tmp_path):
    """Run each actual scenario with one failed request among completed successes."""
    web = relative.startswith("test_web_api/")
    code_aware_sdk = relative.endswith("test_memory_management/test_list_memory.py")
    error = "injected concurrent request failure"
    chunk_data = [{"id": str(index)} for index in range(5)]
    success = {"code": 0, "data": {"chunks": chunk_data, "docs": chunk_data}}

    class Executor:
        def __init__(self, **_kwargs):
            self.count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def submit(self, _request, *_args, **_kwargs):
            future = Future()
            if self.count == 0 and fault == "exception":
                future.set_exception(RuntimeError(error))
            elif self.count == 0:
                future.set_result({**success, "code": 102, "message": error})
            else:
                future.set_result(success if web or code_aware_sdk else chunk_data)
            self.count += 1
            return future

    path = ROOT / "test/testcases" / relative
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    node.decorator_list = []
    resource = _DisposableScenarioResource()
    namespace = {
        "ThreadPoolExecutor": Executor,
        "as_completed": as_completed,
        "DataSet": _DisposableScenarioResource,
        "randint": lambda _a, _b: 0,
        "batch_create_datasets": lambda _client, count: [resource] * count,
        "batch_add_chunks": lambda *args: [resource] * args[-1],
        "batch_add_sessions_with_chat_assistant": lambda _chat, count: [resource] * count,
        "bulk_upload_documents": lambda *args: [resource] * (args[2] if web else args[1]),
        "create_txt_file": lambda path: path,
        "wait_for": lambda *_args: lambda func: func,
    }
    for api in ("list_chunks", "delete_chunks", "create_document", "list_documents", "list_datasets", "parse_documents", "delete_document", "upload_documents", "list_memory"):
        namespace[api] = lambda *_args, **_kwargs: success
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    fixtures = {
        "self": resource,
        "client": resource,
        "WebApiAuth": None,
        "add_document": ("dataset", "document") if web else (resource, resource),
        "add_chunks": ("dataset", "document", ["chunk"] * 4) if web else (resource, resource, [resource] * 4),
        "add_documents": ("dataset", []) if web else (resource, []),
        "add_dataset": "dataset" if web else resource,
        "add_dataset_func": "dataset" if web else resource,
        "add_chat_assistants": (resource, resource, [resource]),
        "add_sessions_with_chat_assistant": (resource, [resource] * 5),
        "add_sessions_with_chat_assistant_func": (resource, [resource] * 5),
        "tmp_path": tmp_path,
    }
    arguments = [fixtures[arg.arg] for arg in node.args.args]
    with pytest.raises(RuntimeError if fault == "exception" else AssertionError, match=error):
        namespace[name](*arguments)
