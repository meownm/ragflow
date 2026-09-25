"""Source workspace selection and workflow contract."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]
pytestmark = pytest.mark.p1


@pytest.fixture()
def source_module():
    path = REPO_ROOT / "api" / "source_workbench" / "service.py"
    spec = spec_from_file_location("source_workbench_service_boundary_test", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workspace(selection=None, version=3):
    return {
        "id": "workspace-id",
        "dataset_ids": ["dataset-a"],
        "selected_documents": selection or [],
        "version": version,
    }


def _gateway(calls, revision="hash:1:2"):
    def document_revisions(documents):
        calls.append(("revisions", documents))
        return [{**item, "revision": revision} for item in documents]

    async def search(owner, datasets, query, document_ids=None):
        calls.append(("search", owner, datasets, query, document_ids))
        return [{"document_id": "doc-1", "content_with_weight": "Selected evidence"}]

    return SimpleNamespace(
        validate_datasets=lambda owner, datasets: calls.append(("datasets", datasets)),
        document_revisions=document_revisions,
        describe_chunks=lambda chunks, datasets: [{"dataset_id": "dataset-a", "document_id": "doc-1", "title": "Article"}],
        load_document=lambda document: "Full indexed text",
        search=search,
    )


@pytest.mark.asyncio
async def test_selection_pins_authoritative_revision(source_module):
    calls = []
    saved = []
    repository = SimpleNamespace(
        get=lambda owner, workspace: _workspace(saved),
        replace_selection=lambda owner, workspace, version, documents: saved.extend(documents),
    )
    service = source_module.SourceWorkspaceService(repository, _gateway(calls))
    result = await service.select(
        "owner-a",
        "workspace-id",
        {
            "expected_version": 3,
            "selected_documents": [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "forged"}],
        },
    )
    assert saved == [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    assert result["selected_documents"] == saved


@pytest.mark.asyncio
async def test_retrieve_searches_only_pinned_documents(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    repository = SimpleNamespace(get=lambda owner, workspace: _workspace(selection))
    service = source_module.SourceWorkspaceService(repository, _gateway(calls))
    result = await service.retrieve("owner-a", "workspace-id", "question", 3)
    assert ("search", "owner-a", ["dataset-a"], "question", ["doc-1"]) in calls
    assert result["version"] == 3
    assert [entry[0] for entry in calls].count("revisions") == 2


@pytest.mark.asyncio
async def test_retrieve_rejects_changed_document_before_search(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "old"}]
    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace(selection)),
        _gateway(calls),
    )
    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.retrieve("owner-a", "workspace-id", "question", 3)
    assert error.value.code == "SOURCE_CHANGED"
    assert not any(entry[0] == "search" for entry in calls)


@pytest.mark.asyncio
async def test_retrieve_rejects_stale_selection_version(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace(selection)),
        _gateway(calls),
    )
    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.retrieve("owner-a", "workspace-id", "question", 2)
    assert error.value.code == "VERSION_CONFLICT"
    assert calls == []


@pytest.mark.asyncio
async def test_load_selected_documents_checks_revisions_after_loading(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway(calls)
    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace(selection)),
        gateway,
    )
    result = await service.load_selected_documents("owner-a", "workspace-id", 3)
    assert result["documents"] == [{**selection[0], "source": {"dataset_id": "dataset-a", "document_id": "doc-1", "title": "Article"}, "text": "Full indexed text"}]

    def change_revision(document):
        gateway.document_revisions = lambda documents: [{**documents[0], "revision": "new"}]
        return "changed content"

    gateway.load_document = change_revision
    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.load_selected_documents("owner-a", "workspace-id", 3)
    assert error.value.code == "SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_chat_uses_previous_question_without_assistant_history(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    repository = SimpleNamespace(get=lambda owner, workspace: _workspace(selection))
    answer_calls = []

    async def answer(owner, question, previous_question, evidence):
        answer_calls.append((question, previous_question, evidence))
        return "Answer [1]"

    service = source_module.SourceWorkspaceService(repository, _gateway(calls), SimpleNamespace(answer=answer))
    result = await service.chat(
        "owner-a",
        "workspace-id",
        {
            "question": "Question",
            "previous_question": "Prior question",
            "expected_version": 3,
        },
    )
    assert ("search", "owner-a", ["dataset-a"], "Prior question Question", ["doc-1"]) in calls
    assert answer_calls[0][1] == "Prior question"
    assert answer_calls[0][2] == [{"number": "1", "document_id": "doc-1", "text": "Selected evidence"}]
    assert result["sources"][0]["citation_numbers"] == [1]


@pytest.mark.asyncio
async def test_selection_rejects_document_from_other_dataset(source_module):
    calls = []
    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace()),
        _gateway(calls),
    )
    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.select(
            "owner-a",
            "workspace-id",
            {
                "expected_version": 3,
                "selected_documents": [{"dataset_id": "dataset-b", "document_id": "doc-1"}],
            },
        )
    assert error.value.code == "INVALID_DOCUMENTS"


@pytest.mark.asyncio
async def test_search_can_request_next_page_without_repeating_query_history(source_module):
    calls = []

    async def search(owner, datasets, query, document_ids=None, page=1):
        calls.append(("search", page))
        # Child hits can collapse to one parent after a full raw page.
        return [{"document_id": "doc-1"}]

    gateway = _gateway(calls)
    gateway.search = search
    repository = SimpleNamespace(
        get=lambda owner, workspace: _workspace(),
        add_query=lambda owner, workspace, query: calls.append(("saved", query)),
    )
    service = source_module.SourceWorkspaceService(repository, gateway)
    result = await service.search("owner-a", "workspace-id", "question", page=2)
    assert result["has_more"] is True
    assert ("search", 2) in calls
    assert not any(call[0] == "saved" for call in calls)
