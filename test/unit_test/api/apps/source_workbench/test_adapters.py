"""Checks for source-workspace RAGFlow adapter boundaries."""

import inspect
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest
from peewee import SqliteDatabase

from common import settings
from api.db.db_models import SourceWorkspaceDraft
from api.source_workbench.adapters import DocMetadataService, Document, KnowledgebaseService, RAGFlowSourceGateway, SourceWorkspaceRepository
from api.source_workbench.service import SourceWorkspaceError


pytestmark = pytest.mark.p1


def test_saved_draft_persists_provenance_and_rejects_stale_or_foreign_edit():
    database = SqliteDatabase(":memory:")
    with database.bind_ctx([SourceWorkspaceDraft]), database.connection_context():
        database.create_tables([SourceWorkspaceDraft])
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
