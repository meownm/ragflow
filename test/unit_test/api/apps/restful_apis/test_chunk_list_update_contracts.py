"""List/update wire contracts against the real REST handlers and public SDK."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from beartype.roar import BeartypeCallHintParamViolation
import pytest
from ragflow_sdk import Document, Chunk
from ragflow_sdk.modules.chunk import ChunkUpdateError


@pytest.fixture
def handler(monkeypatch):
    # Reuse the existing disposable handler harness; decorators and service
    # ports are replaced, while list/update execute the actual owning module.
    root = Path(__file__).resolve().parents[5]
    path = root / "test/testcases/test_web_api/test_chunk_app/test_chunk_routes_unit.py"
    spec = importlib.util.spec_from_file_location("chunk_contract_harness", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    module = harness._load_chunk_api_module(monkeypatch)
    module.request = SimpleNamespace(args={}, headers={})
    return module


@pytest.mark.parametrize(
    "params,message",
    [
        ({"page": "0"}, "page must be greater than or equal to 1"),
        ({"page": "-1"}, "page must be greater than or equal to 1"),
        ({"page": "a"}, "page must be an integer"),
        ({"page": "1.5"}, "page must be an integer"),
        ({"page_size": "-1"}, "page_size must be greater than or equal to 0"),
        ({"page_size": "a"}, "page_size must be an integer"),
        ({"page_size": "101"}, "page_size must be less than or equal to 100"),
    ],
)
async def test_invalid_pagination_returns_data_error_before_search(handler, params, message):
    handler.request.args = params

    async def forbidden_search(*args, **kwargs):
        pytest.fail("Invalid pagination reached search")

    handler.settings.retriever.search = forbidden_search
    result = await handler.list_chunks("tenant-1", "kb-1", "doc-1")
    assert result["code"] == 102 and result["message"] == message


async def test_zero_page_size_returns_empty_without_engine_slice(handler):
    handler.request.args = {"page_size": "0"}

    async def forbidden_search(*args, **kwargs):
        pytest.fail("Zero page size reached engine slicing")

    handler.settings.retriever.search = forbidden_search
    result = await handler.list_chunks("tenant-1", "kb-1", "doc-1")
    assert result["code"] == 0 and result["data"]["chunks"] == []
    assert result["data"]["doc"]["id"] == "doc-1"


@pytest.mark.parametrize("params,page,size", [({}, 1, 30), ({"page": "3", "page_size": "2"}, 3, 2), ({"page_size": "100"}, 1, 100)])
async def test_list_passes_valid_pagination_and_document_scope_to_retriever(handler, params, page, size):
    handler.request.args = params
    original_search = handler.settings.retriever.search
    observed = []

    async def search(query, index, datasets, **kwargs):
        observed.append((query, index, datasets))
        return await original_search(query, index, datasets, **kwargs)

    handler.settings.retriever.search = search
    result = await handler.list_chunks("tenant-1", "kb-1", "doc-1")
    assert result["code"] == 0
    query, index, datasets = observed[0]
    assert (query["page"], query["size"]) == (page, size)
    assert query["doc_ids"] == ["doc-1"] and datasets == ["kb-1"] and index == "idx-tenant-1"


@pytest.mark.parametrize("chunk", [None, {"doc_id": "other-doc"}, {"doc_id": "doc-1", "compile_kwd": "structure"}])
async def test_specific_missing_foreign_or_structural_chunk_is_not_listed(handler, chunk):
    handler.request.args = {"id": "unknown"}
    handler.settings.docStoreConn.chunk = chunk
    result = await handler.list_chunks("tenant-1", "kb-1", "doc-1")
    assert result["code"] == 102
    assert result["message"] == "Chunk not found: kb-1/unknown"


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"content": 1}, "`content` must be a string"),
        ({"content": []}, "`content` must be a string"),
        ({"content": {}}, "`content` must be a string"),
        ({"important_keywords": [1]}, "`important_keywords` must be a list of strings"),
        ({"important_keywords": [None]}, "`important_keywords` must be a list of strings"),
        ({"questions": [1]}, "`questions` must be a list of strings"),
        ({"questions": [{}]}, "`questions` must be a list of strings"),
        ({"available": "True"}, "`available` must be a boolean or 0 or 1"),
        ({"available": "False"}, "`available` must be a boolean or 0 or 1"),
        ({"available": 2}, "`available` must be a boolean or 0 or 1"),
        ({"available": -1}, "`available` must be a boolean or 0 or 1"),
        ({"available": 1.0}, "`available` must be a boolean or 0 or 1"),
        ({"available": None}, "`available` must be a boolean or 0 or 1"),
    ],
)
async def test_malformed_update_is_rejected_before_embedding_or_write(handler, monkeypatch, payload, message):
    async def request_json():
        return payload

    monkeypatch.setattr(handler, "get_request_json", request_json)
    monkeypatch.setattr(handler.DocumentService, "get_embd_id", lambda *args: pytest.fail("Invalid update reached embedding"))
    result = await handler.update_chunk("tenant-1", "kb-1", "doc-1", "chunk-1")
    assert result["code"] == 102 and result["message"] == message
    assert handler.settings.docStoreConn.updated == []


@pytest.mark.parametrize("available", [True, False, 0, 1])
async def test_supported_availability_updates_preserve_content(handler, monkeypatch, available):
    async def request_json():
        return {"content": None, "available": available}

    monkeypatch.setattr(handler, "get_request_json", request_json)
    result = await handler.update_chunk("tenant-1", "kb-1", "doc-1", "chunk-1")
    assert result["code"] == 0
    condition, update = handler.settings.docStoreConn.updated[0]
    assert condition == {"id": "chunk-1"}
    assert update["content_with_weight"] == "chunk content" and update["available_int"] == int(available)


@pytest.mark.parametrize("payload", [{}, {"content": None}, {"unknown_key": "ignored"}])
async def test_empty_and_partial_updates_keep_existing_content(handler, monkeypatch, payload):
    async def request_json():
        return payload

    monkeypatch.setattr(handler, "get_request_json", request_json)
    result = await handler.update_chunk("tenant-1", "kb-1", "doc-1", "chunk-1")
    assert result["code"] == 0
    assert handler.settings.docStoreConn.updated[0][1]["content_with_weight"] == "chunk content"


@pytest.mark.parametrize("operation", ["list", "update"])
async def test_ownership_is_checked_before_untrusted_payload(handler, monkeypatch, operation):
    handler.request.args = {"page": "invalid"}
    monkeypatch.setattr(handler.KnowledgebaseService, "accessible", lambda **kwargs: False)

    async def forbidden_json():
        pytest.fail("Forbidden actor reached request body")

    monkeypatch.setattr(handler, "get_request_json", forbidden_json)
    if operation == "list":
        result = await handler.list_chunks("foreign-tenant", "kb-1", "doc-1")
    else:
        result = await handler.update_chunk("foreign-tenant", "kb-1", "doc-1", "chunk-1")
    assert result["code"] == 102 and result["message"] == "You don't own the dataset kb-1."
    assert handler.settings.docStoreConn.updated == []


class SDKTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, path, params):
        self.calls.append(("GET", path, params))
        return SimpleNamespace(json=lambda: self.response)

    def patch(self, path, payload):
        self.calls.append(("PATCH", path, payload))
        return SimpleNamespace(json=lambda: self.response)


@pytest.mark.parametrize("payload", [None, [], "invalid"])
def test_sdk_update_wrong_container_type_is_rejected_before_http(payload):
    transport = SDKTransport({"code": 0})
    chunk = Chunk(transport, {"id": "chunk-1", "document_id": "doc-1", "dataset_id": "kb-1"})
    with pytest.raises(BeartypeCallHintParamViolation):
        chunk.update(payload)
    assert transport.calls == []


def test_sdk_preserves_structured_update_error_without_mutating_local_chunk():
    transport = SDKTransport({"code": 102, "message": "`content` must be a string", "details": {"field": "content"}})
    chunk = Chunk(transport, {"id": "chunk-1", "document_id": "doc-1", "dataset_id": "kb-1", "content": "old"})
    with pytest.raises(ChunkUpdateError) as error:
        chunk.update({"content": 1})
    assert (error.value.code, error.value.message, error.value.details) == (102, "`content` must be a string", {"field": "content"})
    assert chunk.content == "old"
    assert transport.calls == [("PATCH", "/datasets/kb-1/documents/doc-1/chunks/chunk-1", {"content": 1})]


def test_sdk_missing_id_surfaces_data_error_instead_of_empty_or_attribute_error():
    transport = SDKTransport({"code": 102, "message": "Chunk not found: kb-1/unknown"})
    document = Document(transport, {"id": "doc-1", "dataset_id": "kb-1"})
    with pytest.raises(Exception, match="Chunk not found: kb-1/unknown"):
        document.list_chunks(id="unknown")


def test_sdk_zero_size_success_is_an_empty_list_and_keeps_explicit_query_value():
    transport = SDKTransport({"code": 0, "data": {"chunks": []}})
    document = Document(transport, {"id": "doc-1", "dataset_id": "kb-1"})
    assert document.list_chunks(page_size=0) == []
    assert transport.calls[0][2]["page_size"] == 0


@pytest.fixture
def retrieval_handler(handler, monkeypatch):
    kb = SimpleNamespace(id="kb-1", tenant_id="tenant-1", embd_id="embed")
    monkeypatch.setattr(handler.KnowledgebaseService, "get_by_ids", lambda ids: [kb], raising=False)
    monkeypatch.setattr(handler.KnowledgebaseService, "get_by_id", lambda dataset_id: (True, kb))
    return handler


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("page", 0, "page must be greater than or equal to 1"),
        ("page", -1, "page must be greater than or equal to 1"),
        ("page", "a", "page must be an integer"),
        ("page", None, "page must be an integer"),
        ("page", True, "page must be an integer"),
        ("page", 1.5, "page must be an integer"),
        ("page_size", -1, "page_size must be greater than or equal to 0"),
        ("page_size", "a", "page_size must be an integer"),
        ("page_size", 101, "page_size must be less than or equal to 100"),
        ("page_size", False, "page_size must be an integer"),
        ("vector_similarity_weight", "invalid", "vector_similarity_weight must be a finite number"),
        ("vector_similarity_weight", "NaN", "vector_similarity_weight must be a finite number"),
        ("vector_similarity_weight", float("inf"), "vector_similarity_weight must be a finite number"),
        ("vector_similarity_weight", True, "vector_similarity_weight must be a finite number"),
        ("similarity_threshold", {}, "similarity_threshold must be a finite number"),
        ("similarity_threshold", "-Infinity", "similarity_threshold must be a finite number"),
        ("similarity_threshold", None, "similarity_threshold must be a finite number"),
        ("top_k", "a", "`top_k` must be an integer"),
        ("top_k", [], "`top_k` must be an integer"),
        ("top_k", False, "`top_k` must be an integer"),
        ("top_k", 1.5, "`top_k` must be an integer"),
        ("top_k", 0, "`top_k` must be greater than 0"),
        ("top_k", -1, "`top_k` must be greater than 0"),
    ],
)
async def test_retrieval_invalid_numeric_inputs_fail_before_models_and_search(retrieval_handler, monkeypatch, field, value, message):
    async def request_json():
        return {"dataset_ids": ["kb-1"], "question": "Find orders", field: value}

    monkeypatch.setattr(retrieval_handler, "get_request_json", request_json)
    monkeypatch.setattr(retrieval_handler, "get_model_config_from_provider_instance", lambda *args: pytest.fail("Invalid retrieval reached model lookup"))
    result = await retrieval_handler.retrieval_test("tenant-1")
    assert result["code"] == 102 and result["message"] == message


@pytest.mark.parametrize("size", [0, "0"])
async def test_zero_retrieval_size_returns_empty_without_model_or_engine(retrieval_handler, monkeypatch, size):
    async def request_json():
        return {"dataset_ids": ["kb-1"], "question": "Find orders", "page_size": size}

    monkeypatch.setattr(retrieval_handler, "get_request_json", request_json)
    monkeypatch.setattr(retrieval_handler, "get_model_config_from_provider_instance", lambda *args: pytest.fail("Zero size retrieval reached model lookup"))
    result = await retrieval_handler.retrieval_test("tenant-1")
    assert result["code"] == 0 and result["data"] == {"total": 0, "chunks": [], "doc_aggs": {}}


@pytest.mark.parametrize("weight", [10, "10", -2, "0.75"])
async def test_retrieval_keeps_finite_weights_and_integer_strings_supported(retrieval_handler, monkeypatch, weight):
    async def request_json():
        return {"dataset_ids": ["kb-1"], "question": "Find orders", "page": "2", "page_size": "10", "top_k": "50", "vector_similarity_weight": weight, "similarity_threshold": "0.3"}

    observed = []

    async def retrieval(*args, **kwargs):
        observed.append(args)
        return {"total": 0, "chunks": [], "doc_aggs": {}}

    monkeypatch.setattr(retrieval_handler, "get_request_json", request_json)
    monkeypatch.setattr(retrieval_handler.settings.retriever, "retrieval", retrieval, raising=False)
    monkeypatch.setattr(retrieval_handler.settings.retriever, "retrieval_by_children", lambda chunks, tenants: chunks, raising=False)
    result = await retrieval_handler.retrieval_test("tenant-1")
    assert result["code"] == 0, result
    assert observed[0][4:9] == (2, 10, 0.3, float(weight), 50)


async def test_retrieval_checks_dataset_access_before_numeric_validation(retrieval_handler, monkeypatch):
    async def request_json():
        return {"dataset_ids": ["foreign"], "question": "Find orders", "page": "invalid"}

    monkeypatch.setattr(retrieval_handler, "get_request_json", request_json)
    monkeypatch.setattr(retrieval_handler.KnowledgebaseService, "accessible", lambda **kwargs: False)
    result = await retrieval_handler.retrieval_test("tenant-1")
    assert result["code"] == 102 and result["message"] == "You don't own the dataset foreign."
