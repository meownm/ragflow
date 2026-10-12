"""Exercise document selection through the shipped SDK and HTTP encoder."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from ragflow_sdk.modules.dataset import DataSet


@pytest.mark.parametrize("ids", [["doc-a"], ["doc-a", "doc-b"], []])
def test_document_ids_reach_repeated_query_parameters(ids):
    captured = []

    def get(path, params):
        captured.append(requests.Request("GET", "https://disposable.invalid" + path, params=params).prepare())
        return SimpleNamespace(json=lambda: {"code": 0, "data": {"docs": [{"id": identity} for identity in ids]}})

    dataset = DataSet(SimpleNamespace(get=get), {"id": "dataset"})
    documents = dataset.list_documents(ids=ids, name="report", page=2, page_size=10, desc=False)
    request = captured[0]
    assert urlsplit(request.url).path == "/datasets/dataset/documents"
    query = parse_qs(urlsplit(request.url).query)
    assert query.get("ids", []) == ids
    assert query["name"] == ["report"]
    assert query["page"] == ["2"]
    assert query["page_size"] == ["10"]
    assert query["desc"] == ["False"]
    assert [document.id for document in documents] == ids


def test_conflicting_document_selectors_do_not_issue_request():
    def get(*args, **kwargs):
        pytest.fail("Conflicting selectors reached transport")

    dataset = DataSet(SimpleNamespace(get=get), {"id": "dataset"})
    with pytest.raises(ValueError, match="Cannot use both 'id' and 'ids'"):
        dataset.list_documents(id="doc-a", ids=["doc-b"])


def test_document_selection_preserves_permission_error():
    dataset = DataSet(SimpleNamespace(get=lambda *args: SimpleNamespace(json=lambda: {"code": 109, "message": "Access denied"})), {"id": "dataset"})
    with pytest.raises(Exception, match="Access denied"):
        dataset.list_documents(ids=["foreign-document"])
