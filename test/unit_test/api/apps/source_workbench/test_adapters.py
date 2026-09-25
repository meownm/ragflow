"""Checks for indexed full-text loading at the RAGFlow adapter boundary."""

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


def test_full_document_loader_rejects_incomplete_index(monkeypatch):
    monkeypatch.setattr(KnowledgebaseService, "get_by_id", lambda dataset_id: (True, SimpleNamespace(tenant_id="tenant-a")))
    monkeypatch.setattr(settings, "retriever", SimpleNamespace(chunk_list=lambda *args, **kwargs: [{"content_with_weight": "First"}]), raising=False)
    with pytest.raises(SourceWorkspaceError) as error:
        RAGFlowSourceGateway.load_document({"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"})
    assert error.value.code == "DOCUMENT_CONTENT_UNAVAILABLE"
