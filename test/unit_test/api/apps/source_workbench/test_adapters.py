"""Checks for source-workspace RAGFlow adapter boundaries."""

import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

from common import settings
from api.source_workbench.adapters import KnowledgebaseService, RAGFlowSourceGateway
from api.source_workbench.service import SourceWorkspaceError


pytestmark = pytest.mark.p1


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
    assert chunks == [{"kb_id": "dataset-a", "document_id": "doc-1", "content": "Allowed"}]
