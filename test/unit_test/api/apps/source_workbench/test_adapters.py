"""Checks for source-workspace RAGFlow adapter boundaries."""

import inspect
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest
from peewee import SqliteDatabase

from common import settings
from api.db.db_models import SourceWorkspaceDraft
from api.source_workbench.adapters import DocMetadataService, Document, KnowledgebaseService, RAGFlowAnswerGateway, RAGFlowProcessModel, RAGFlowSourceGateway, SourceWorkspaceRepository
from api.source_workbench.service import SourceWorkspaceError


pytestmark = pytest.mark.p1


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ["Ollama", "OpenAI-API-Compatible"])
async def test_processor_schema_reaches_native_ollama_without_changing_compatible_json_mode(monkeypatch, factory):
    from litellm.llms.ollama.chat.transformation import OllamaChatConfig

    from api.db.services import llm_service

    calls = []
    schema = {"type": "object", "properties": {"edits": {"type": "array", "items": {"type": "string"}}}, "required": ["edits"], "additionalProperties": False}

    class Bundle:
        def __init__(self, *_args, **_kwargs):
            self.mdl = SimpleNamespace(async_chat_streamly=True)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        async def async_chat_streamly_delta(self, _system, _history, generation, **options):
            assert options == {"with_reasoning": False}
            calls.append(generation)
            yield '{"edits":[]}'

    monkeypatch.setattr(llm_service, "LLMBundle", Bundle)
    model = RAGFlowProcessModel("owner", {"llm_factory": factory, "llm_name": "qwen3.8:latest", "max_tokens": 32768, "max_output_tokens": 4096})
    assert [part async for part in model.stream("system", {"response_schema": schema}, 1024, json_response=schema)] == ['{"edits":[]}']
    response_format = calls[0]["response_format"]
    if factory == "Ollama":
        native = OllamaChatConfig().map_openai_params({"response_format": response_format}, {}, "qwen3.8:latest", False)
        assert native["format"] == schema
    else:
        assert response_format == {"type": "json_object"}


@pytest.mark.asyncio
async def test_answer_uses_full_discovered_context_without_truncating_large_response(monkeypatch):
    from common.token_utils import num_tokens_from_string

    calls = []
    answer = "Complete answer with evidence [1]. " * 2000

    async def stream(system, payload, output_tokens):
        calls.append((payload, output_tokens))
        yield "<think>private reasoning</think>"
        yield answer

    async def open_processor(owner):
        assert owner == "owner"
        return SimpleNamespace(context_tokens=262144, count_tokens=num_tokens_from_string, stream=stream)

    monkeypatch.setattr(RAGFlowAnswerGateway, "open_processor", open_processor)
    evidence = [{"text": "Full evidence", "source": "1"}]
    assert await RAGFlowAnswerGateway.answer("owner", "Question", "Previous", evidence) == answer.strip()
    assert calls[0][0]["evidence"] == evidence
    assert calls[0][1] > 65536


def test_source_dataset_choices_use_authenticated_dataset_visibility(monkeypatch):
    calls = []

    def get_list(*args):
        calls.append(args)
        return [
            {"id": "kb-1", "name": "Articles", "tenant_id": "tenant-a", "embd_id": "model-a", "chunk_num": 3},
            {"id": "kb-empty", "name": "Empty", "tenant_id": "tenant-a", "embd_id": "model-a", "chunk_num": 0},
        ], 2

    monkeypatch.setattr(KnowledgebaseService, "get_list", get_list)
    assert RAGFlowSourceGateway.list_datasets("reader-a") == [{"id": "kb-1", "name": "Articles", "tenant_id": "tenant-a", "embd_id": "model-a"}]
    assert calls == [([], "reader-a", 1, 100, "name", False, None, None, "")]


def test_source_dataset_choices_include_later_pages_after_empty_sources(monkeypatch):
    def get_list(_tenants, _owner, page, *_args):
        if page == 1:
            return [{"id": f"empty-{index}", "name": "Empty", "tenant_id": "tenant-a", "embd_id": "model-a", "chunk_num": 0} for index in range(100)], 101
        return [{"id": "kb-last", "name": "Article", "tenant_id": "tenant-a", "embd_id": "model-a", "chunk_num": 1}], 101

    monkeypatch.setattr(KnowledgebaseService, "get_list", get_list)
    assert [item["id"] for item in RAGFlowSourceGateway.list_datasets("reader-a")] == ["kb-last"]


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", ["Ollama", "OpenAI-API-Compatible"])
@pytest.mark.parametrize("json_response", [False, True])
@pytest.mark.parametrize("thinking_supported", [False, True])
@pytest.mark.parametrize("thinking_requested", [False, True])
async def test_ollama_processor_uses_discovered_context_for_planning_and_generation(monkeypatch, factory, json_response, thinking_supported, thinking_requested):
    import aiohttp
    from api.db.services import llm_service

    calls = []

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def raise_for_status(self):
            pass

        async def json(self):
            if factory == "OpenAI-API-Compatible":
                return {"models": [{"name": "other", "context_length": 8192}, {"model": "test:latest", "context_length": 131072}]}
            return {"details": {"family": "test"}, "model_info": {"test.context_length": 131072}, "capabilities": ["completion", "thinking"] if thinking_supported else ["completion"]}

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    class Bundle:
        def __init__(self, *_args, **_kwargs):
            assert _kwargs["timeout"] == 1800
            self.mdl = SimpleNamespace(async_chat_streamly=True)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        async def async_chat_streamly_delta(self, _system, _history, generation, **options):
            calls.append(generation)
            assert options == ({"with_reasoning": False} if json_response else {})
            if json_response:
                if options.get("with_reasoning", True):
                    yield "<think>first</think>"
                    yield "second</think>"
                yield '{"valid":true,"issues":[]}'
            else:
                yield "answer"

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    monkeypatch.setattr(llm_service, "LLMBundle", Bundle)
    config = {"llm_factory": factory, "llm_name": "test:latest", "api_base": "http://ollama:11434/v1/", "api_key": "", "max_tokens": 4096}
    model = RAGFlowProcessModel("owner", config)
    await model.resolve_context()
    assert model.context_tokens == 131072
    assert model.supports_thinking is (factory == "Ollama" and thinking_supported)
    assert [part async for part in model.stream("system", {"task": "create"}, 32768, json_response=json_response, thinking=thinking_requested)] == (
        ['{"valid":true,"issues":[]}'] if json_response else ["answer"]
    )
    assert config["max_tokens"] == 4096
    assert calls[0][0] == f"http://ollama:11434/api/{'show' if factory == 'Ollama' else 'ps'}"
    if factory == "Ollama":
        assert calls[1]["extra_body"] == {"num_ctx": 131072, **({"think": True} if thinking_requested and thinking_supported else {})}
        import litellm
        from litellm.llms.ollama.chat.transformation import OllamaChatConfig

        from rag.llm.chat_model import _apply_model_family_policies, _move_litellm_provider_body_fields

        configured, kwargs = _apply_model_family_policies("qwen3.8:latest", backend="litellm", provider="Ollama", gen_conf=calls[1])
        completion = _move_litellm_provider_body_fields("Ollama", {**configured, **kwargs})
        options = litellm.get_optional_params(model="ollama_chat/qwen3.8:latest", custom_llm_provider="ollama_chat", **completion)
        native = OllamaChatConfig().transform_request(model="qwen3.8:latest", messages=[{"role": "user", "content": "control"}], optional_params=options, litellm_params={}, headers={})
        assert native["think"] is (thinking_requested and thinking_supported)
        assert native["options"]["num_ctx"] == 131072
    assert calls[1]["max_tokens" if factory == "OpenAI-API-Compatible" else "max_completion_tokens"] == 32768
    assert calls[1].get("response_format") == ({"type": "json_object"} if json_response else None)


def test_saved_draft_persists_provenance_and_rejects_stale_or_foreign_edit(monkeypatch, tmp_path):
    from api.source_workbench.linked_migrations import LINKED_MODELS
    from api.db.db_models import SourceWorkspace
    from api.source_workbench.linked_application import LinkedDocuments
    from api.source_workbench.linked_storage import LinkedDocumentRepository
    from api.source_workbench import linked_runtime

    database = SqliteDatabase(tmp_path / "saved-drafts.sqlite")
    with database.bind_ctx([SourceWorkspace, SourceWorkspaceDraft, *LINKED_MODELS]), database.connection_context():
        database.create_tables([SourceWorkspace, SourceWorkspaceDraft, *LINKED_MODELS])
        SourceWorkspace.create(id="workspace-1", owner_id="owner-a", title="Workspace", dataset_ids=["kb-1"], version=3)
        gateway = SimpleNamespace(
            validate_datasets=lambda *_args: None,
            document_revisions=lambda refs: [{**ref, "revision": "hash:1:2"} for ref in refs],
            describe_chunks=lambda refs, _datasets: [{**ref, "title": "Article"} for ref in refs],
            load_document=lambda _ref: "Source text",
        )
        monkeypatch.setattr(linked_runtime, "build_linked_documents", lambda: LinkedDocuments(LinkedDocumentRepository(), gateway, None))
        create = inspect.unwrap(SourceWorkspaceRepository.create_draft)
        list_drafts = inspect.unwrap(SourceWorkspaceRepository.list_drafts)
        get = inspect.unwrap(SourceWorkspaceRepository.get_draft)
        update = inspect.unwrap(SourceWorkspaceRepository.update_draft)
        sources = [{"dataset_id": "kb-1", "document_id": "doc-1", "revision": "hash:1:2"}]
        saved = create("owner-a", "workspace-1", "First text", "Create", "all", 3, sources)
        assert "content" not in list_drafts("owner-a", "workspace-1")[0]
        assert get("owner-a", "workspace-1", saved["id"])["sources"] == sources
        edited = update("owner-a", "workspace-1", saved["id"], 1, "Revised text")
        assert edited["content"] == "Revised text"
        assert edited["version"] == 2
        with pytest.raises(SourceWorkspaceError) as stale:
            update("owner-a", "workspace-1", saved["id"], 1, "Overwrite")
        assert stale.value.code == "VERSION_CONFLICT"
        with pytest.raises(SourceWorkspaceError) as foreign:
            get("owner-b", "workspace-1", saved["id"])
        assert foreign.value.status == 404


def test_candidate_similarity_uses_highest_finite_chunk_score(monkeypatch):
    document = SimpleNamespace(id="doc-1", kb_id="dataset-a", status="1", source_type="file", name="Example.txt", content_hash="hash")

    class Query:
        def where(self, _condition):
            return [document]

    monkeypatch.setattr(Document, "select", lambda *_args: Query())
    monkeypatch.setattr(DocMetadataService, "get_metadata_for_documents", lambda *_args: {})
    describe = RAGFlowSourceGateway.describe_chunks.__wrapped__
    candidates = describe(
        [
            {"document_id": "doc-1", "dataset_id": "dataset-a", "content": "first", "similarity": 0.42},
            {"document_id": "doc-1", "dataset_id": "dataset-a", "content": "second", "similarity": float("nan")},
            {"document_id": "doc-1", "dataset_id": "dataset-a", "content": "third", "similarity": 0.73},
        ],
        ["dataset-a"],
    )
    assert len(candidates) == 1
    assert candidates[0]["similarity"] == 0.73


def test_uploaded_article_uses_readable_title_instead_of_storage_path(monkeypatch):
    document = SimpleNamespace(
        id="doc-1",
        kb_id="dataset-a",
        status="1",
        source_type="file",
        name="f4f461eb-00c3-41f9-9b82-3f2b77792942/eva-wiki-all-projects/99b92a37-16f2-57a6-84c2-99fe5ce36099/portal-kompanii__Версия_2_11_EvaProject_и_EvaWiki.html",
        content_hash="hash",
    )

    class Query:
        def where(self, _condition):
            return [document]

    monkeypatch.setattr(Document, "select", lambda *_args: Query())
    monkeypatch.setattr(DocMetadataService, "get_metadata_for_documents", lambda *_args: {})
    describe = RAGFlowSourceGateway.describe_chunks.__wrapped__
    candidate = describe([{"document_id": "doc-1", "dataset_id": "dataset-a"}], ["dataset-a"])[0]
    assert candidate["title"] == "Версия 2.11 EvaProject и EvaWiki"
    assert candidate["path"] == ["eva-wiki-all-projects", "Версия 2.11 EvaProject и EvaWiki"]


def test_full_document_loader_requests_ordered_indexed_chunks(monkeypatch):
    calls = []
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", lambda dataset_id: (True, SimpleNamespace(tenant_id="tenant-a")))

    def chunk_list(*args, **kwargs):
        calls.append((args, kwargs))
        return [
            {"content_with_weight": "First"},
            {"content_with_weight": "Second"},
            {"content_with_weight": "Compiled artifact", "compile_kwd": "artifact_page"},
        ]

    monkeypatch.setattr(settings, "retriever", SimpleNamespace(chunk_list=chunk_list), raising=False)
    text = RAGFlowSourceGateway.load_document({"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"})
    assert text == "First\n\nSecond"
    assert calls[0][0] == ("doc-1", "tenant-a", ["dataset-a"])
    assert calls[0][1]["sort_by_position"] is True
    assert calls[0][1]["max_count"] == 5001


def test_full_document_loader_keeps_table_rows_across_index_chunks(monkeypatch):
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", lambda dataset_id: (True, SimpleNamespace(tenant_id="tenant-a")))
    chunks = [
        {"content_with_weight": "| Name | Amount |\n| --- | --- |\n| Alice | 10 |"},
        {"content_with_weight": "| Bob | 20 |"},
        {"content_with_weight": "| Carol | 30 |\n\nNext paragraph"},
    ]
    monkeypatch.setattr(settings, "retriever", SimpleNamespace(chunk_list=lambda *args, **kwargs: chunks), raising=False)
    text = RAGFlowSourceGateway.load_document({"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:3"})
    assert text == "| Name | Amount |\n| --- | --- |\n| Alice | 10 |\n| Bob | 20 |\n| Carol | 30 |\n\nNext paragraph"


def test_full_document_loader_keeps_split_table_header_and_separator(monkeypatch):
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", lambda dataset_id: (True, SimpleNamespace(tenant_id="tenant-a")))
    chunks = [
        {"content_with_weight": "| Name | Amount |"},
        {"content_with_weight": "| --- | --- |"},
        {"content_with_weight": "| Alice | 10 |"},
    ]
    monkeypatch.setattr(settings, "retriever", SimpleNamespace(chunk_list=lambda *args, **kwargs: chunks), raising=False)
    text = RAGFlowSourceGateway.load_document({"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:3"})
    assert text == "| Name | Amount |\n| --- | --- |\n| Alice | 10 |"


def test_full_document_loader_keeps_list_items_across_index_chunks(monkeypatch):
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", lambda dataset_id: (True, SimpleNamespace(tenant_id="tenant-a")))
    chunks = [{"content_with_weight": "- First item"}, {"content_with_weight": "- Second item"}]
    monkeypatch.setattr(settings, "retriever", SimpleNamespace(chunk_list=lambda *args, **kwargs: chunks), raising=False)
    text = RAGFlowSourceGateway.load_document({"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"})
    assert text == "- First item\n- Second item"


def test_full_document_loader_rejects_incomplete_index(monkeypatch):
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", lambda dataset_id: (True, SimpleNamespace(tenant_id="tenant-a")))
    monkeypatch.setattr(settings, "retriever", SimpleNamespace(chunk_list=lambda *args, **kwargs: [{"content_with_weight": "First"}]), raising=False)
    with pytest.raises(SourceWorkspaceError) as error:
        RAGFlowSourceGateway.load_document({"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"})
    assert error.value.code == "DOCUMENT_CONTENT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_search_discards_hits_outside_selected_documents_and_datasets(monkeypatch):
    requests = []
    search_module = ModuleType("api.apps.services.dataset_api_service")

    async def search_datasets(owner_id, request):
        requests.append((owner_id, request))
        return True, {
            "chunks": [
                {"kb_id": "dataset-a", "document_id": "doc-1", "content": "Allowed"},
                {"kb_id": "dataset-a", "document_id": "doc-2", "content": "Unselected"},
                {"kb_id": "dataset-b", "document_id": "doc-1", "content": "Other tenant"},
                {"dataset_id": "dataset-a", "kb_id": "dataset-b", "document_id": "doc-1", "content": "Conflicting scope"},
                {"document_id": "doc-1", "content": "Missing scope"},
                "malformed hit",
            ]
        }

    search_module.search_datasets = search_datasets
    monkeypatch.setitem(sys.modules, search_module.__name__, search_module)
    chunks = await RAGFlowSourceGateway.search("owner-a", ["dataset-a"], "question", ["doc-1"])

    assert requests[0][0] == "owner-a"
    assert requests[0][1]["dataset_ids"] == ["dataset-a"]
    assert requests[0][1]["doc_ids"] == ["doc-1"]
    assert requests[0][1]["similarity_threshold"] == 0.4
    assert chunks == [{"kb_id": "dataset-a", "document_id": "doc-1", "content": "Allowed"}]


@pytest.mark.asyncio
async def test_search_similarity_threshold_comes_from_server_environment(monkeypatch):
    requests = []
    search_module = ModuleType("api.apps.services.dataset_api_service")

    async def search_datasets(_owner_id, request):
        requests.append(request)
        return True, {"chunks": []}

    search_module.search_datasets = search_datasets
    monkeypatch.setitem(sys.modules, search_module.__name__, search_module)
    monkeypatch.setenv("SOURCE_WORKBENCH_SIMILARITY_THRESHOLD", "0.62")
    await RAGFlowSourceGateway.search("owner-a", ["dataset-a"], "question")
    assert requests[0]["similarity_threshold"] == 0.62

    monkeypatch.setenv("SOURCE_WORKBENCH_SIMILARITY_THRESHOLD", "nan")
    with pytest.raises(SourceWorkspaceError) as error:
        await RAGFlowSourceGateway.search("owner-a", ["dataset-a"], "question")
    assert error.value.code == "SEARCH_CONFIG_INVALID"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_openai_processor_caps_planned_and_wire_output_without_shrinking_context(monkeypatch):
    from api.db.services import llm_service
    from api.source_workbench.processing import ModelBudget

    calls = []

    class Bundle:
        def __init__(self, *_args, **_kwargs):
            self.mdl = SimpleNamespace(async_chat_streamly=True)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        async def async_chat_streamly_delta(self, _system, _history, generation):
            calls.append(generation)
            yield "answer"

    monkeypatch.setattr(llm_service, "LLMBundle", Bundle)
    model = RAGFlowProcessModel("owner", {"llm_factory": "OpenAI", "llm_name": "gpt-4.1", "max_tokens": 1047576})
    await model.resolve_context()
    model.resolve_output_limit()
    budget = ModelBudget.from_context(model.context_tokens, model.count_tokens, output_limit=model.output_tokens)
    assert budget.response_tokens("system", {}) == 32768
    assert model.context_tokens == 1047576
    assert [part async for part in model.stream("system", {}, 1000000)] == ["answer"]
    assert calls == [{"temperature": 0, "max_completion_tokens": 32768}]


@pytest.mark.asyncio
async def test_unknown_processor_output_ceiling_fails_before_bundle(monkeypatch):
    from api.db.services import llm_service

    def unexpected(*_args, **_kwargs):
        pytest.fail("Provider bundle must not be opened")

    monkeypatch.setattr(llm_service, "LLMBundle", unexpected)
    model = RAGFlowProcessModel("owner", {"llm_factory": "OpenAI", "llm_name": "unregistered-future-model", "max_tokens": 1047576})
    with pytest.raises(SourceWorkspaceError) as error:
        _ = [part async for part in model.stream("system", {}, 1000000)]
    assert error.value.code == "MODEL_OUTPUT_LIMIT_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded", [[], [{"name": "another-model", "context_length": 4096}]])
async def test_unloaded_compatible_processor_sets_native_context_and_thinking(monkeypatch, loaded):
    import aiohttp
    from api.db.services import llm_service

    requests = []
    generations = []

    class Response:
        def __init__(self, data):
            self.data = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def raise_for_status(self):
            pass

        async def json(self):
            return self.data

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response({"models": loaded})

        def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response({"details": {"family": "qwen35"}, "model_info": {"qwen35.context_length": 262144}, "capabilities": ["completion", "thinking"]})

    class Bundle:
        def __init__(self, owner, config, **_kwargs):
            assert owner == "owner" and config["llm_factory"] == "Ollama"
            assert config["api_key"] == "tenant-secret" and config["api_base"] == "http://provider"
            self.mdl = SimpleNamespace(async_chat_streamly=True)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        async def async_chat_streamly_delta(self, _system, _history, generation):
            generations.append(generation)
            yield "answer"

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    monkeypatch.setattr(llm_service, "LLMBundle", Bundle)
    config = {"llm_factory": "OpenAI-API-Compatible", "llm_name": "qwen3.8:latest", "api_base": "http://provider/v1/", "api_key": "tenant-secret", "max_tokens": 8192}
    original = dict(config)
    model = RAGFlowProcessModel("owner", config)
    await model.resolve_context()
    model.resolve_output_limit()
    assert model.context_tokens == 262144 and model.output_tokens is None
    assert model.supports_thinking
    assert requests == [
        ("http://provider/api/ps", {"headers": {"Authorization": "Bearer tenant-secret"}}),
        ("http://provider/api/show", {"headers": {"Authorization": "Bearer tenant-secret"}, "json": {"model": "qwen3.8:latest"}}),
    ]
    assert [part async for part in model.stream("system", {}, 20000, thinking=True)] == ["answer"]
    assert generations == [{"temperature": 0, "max_completion_tokens": 20000, "extra_body": {"num_ctx": 262144, "think": True}, "enable_thinking": True}]
    assert config == original


@pytest.mark.asyncio
async def test_context_discovery_timeout_never_downgrades_requested_thinking(monkeypatch):
    import aiohttp
    from api.db.services import llm_service

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            raise TimeoutError()

        async def __aexit__(self, *_args):
            pass

    def unexpected(*_args, **_kwargs):
        pytest.fail("Provider must not run with an unconfirmed thinking capability")

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    monkeypatch.setattr(llm_service, "LLMBundle", unexpected)
    model = RAGFlowProcessModel("owner", {"llm_factory": "Ollama", "llm_name": "test", "api_base": "http://unused", "max_tokens": 4096})
    await model.resolve_context()
    with pytest.raises(SourceWorkspaceError) as error:
        _ = [part async for part in model.stream("system", {}, 1000, json_response=True, thinking=True)]
    assert error.value.code == "MODEL_CAPABILITIES_UNAVAILABLE"
