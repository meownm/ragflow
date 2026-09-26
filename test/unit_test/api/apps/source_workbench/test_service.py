"""Source workspace selection and workflow contract."""

from importlib import import_module
from types import SimpleNamespace

import pytest
from common.token_utils import num_tokens_from_string


pytestmark = pytest.mark.p1


@pytest.fixture()
def source_module():
    return import_module("api.source_workbench.service")


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
async def test_revoked_dataset_access_stops_retrieval_before_search_or_model(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway(calls)

    def deny_access(owner, datasets):
        calls.append(("denied", owner, datasets))
        raise source_module.SourceWorkspaceError("DATASET_UNAVAILABLE", "Access was revoked", 403)

    gateway.validate_datasets = deny_access
    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace(selection)),
        gateway,
        SimpleNamespace(answer=lambda *args: calls.append(("answer",))),
    )
    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.chat("owner-a", "workspace-id", {"question": "Question", "expected_version": 3})

    assert error.value.code == "DATASET_UNAVAILABLE"
    assert calls == [("denied", "owner-a", ["dataset-a"])]


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
@pytest.mark.parametrize(
    ("answer_text", "expected_code"),
    [
        ("Answer [1]", None),
        ("Answer [0001]", None),
        ("Answer without citations", None),
        ("Answer [2]", "INVALID_CITATION"),
        ("Answer [0]", "INVALID_CITATION"),
        ("Answer [" + "9" * 5000 + "]", "INVALID_CITATION"),
    ],
)
async def test_chat_checks_only_citation_numbers_outside_evidence(source_module, answer_text, expected_code):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]

    async def answer(*args):
        calls.append(("answer",))
        return answer_text

    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace(selection)),
        _gateway(calls),
        SimpleNamespace(answer=answer),
    )
    request = {"question": "Question", "expected_version": 3}
    if expected_code:
        with pytest.raises(source_module.SourceWorkspaceError) as error:
            await service.chat("owner-a", "workspace-id", request)
        assert error.value.code == expected_code
        assert error.value.status == 502
    else:
        result = await service.chat("owner-a", "workspace-id", request)
        assert result["answer"] == answer_text
    assert ("answer",) in calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign_chunk",
    [
        {"dataset_id": "dataset-b", "document_id": "doc-1", "content": "Foreign dataset"},
        {"dataset_id": "dataset-a", "document_id": "doc-2", "content": "Unselected document"},
    ],
)
async def test_chat_rejects_out_of_selection_search_hits_before_model(source_module, foreign_chunk):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway(calls)

    async def search(*args):
        calls.append(("search",))
        return [foreign_chunk]

    async def answer(*args):
        calls.append(("answer",))
        return "Answer [1]"

    gateway.search = search
    service = source_module.SourceWorkspaceService(SimpleNamespace(get=lambda owner, workspace: _workspace(selection)), gateway, SimpleNamespace(answer=answer))
    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.chat("owner-a", "workspace-id", {"question": "Question", "expected_version": 3})
    assert error.value.code == "SOURCE_CHANGED"
    assert not any(call[0] == "answer" for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "expected_code"),
    [("version", "VERSION_CONFLICT"), ("revision", "SOURCE_CHANGED")],
)
async def test_chat_rechecks_selection_immediately_before_model(source_module, change, expected_code):
    calls = []
    state = {"version": 3, "revision": "hash:1:2"}
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway(calls)

    def describe(chunks, datasets):
        state[change] = 4 if change == "version" else "new"
        return [{"dataset_id": "dataset-a", "document_id": "doc-1", "title": "Article"}]

    def revisions(documents):
        return [{**item, "revision": state["revision"]} for item in documents]

    async def answer(*args):
        calls.append(("answer",))
        return "Answer [1]"

    gateway.describe_chunks = describe
    gateway.document_revisions = revisions
    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace(selection, state["version"])),
        gateway,
        SimpleNamespace(answer=answer),
    )
    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.chat("owner-a", "workspace-id", {"question": "Question", "expected_version": 3})
    assert error.value.code == expected_code
    assert not any(call[0] == "answer" for call in calls)


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


def _processor(calls, context_tokens=8192):
    async def stream(system, payload, output_tokens):
        calls.append((system, payload, output_tokens))
        if "fragment" in payload or "notes" in payload:
            yield "Relevant note [1]"
        else:
            yield "Article "
            yield "[1]"

    async def open_processor(owner):
        calls.append(("open", owner))
        return SimpleNamespace(context_tokens=context_tokens, count_tokens=num_tokens_from_string, stream=stream)

    return SimpleNamespace(open_processor=open_processor)


@pytest.mark.asyncio
async def test_process_streams_complete_selected_articles_in_one_call(source_module):
    calls = []
    selection = [
        {"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"},
        {"dataset_id": "dataset-a", "document_id": "doc-2", "revision": "hash:1:2"},
    ]
    gateway = _gateway(calls)
    gateway.describe_chunks = lambda documents, datasets: [{"dataset_id": "dataset-a", "document_id": item["document_id"], "title": item["document_id"]} for item in reversed(documents)]
    gateway.load_document = lambda document: f"Text of {document['document_id']}"
    service = source_module.SourceWorkspaceService(SimpleNamespace(get=lambda owner, workspace: _workspace(selection)), gateway, _processor(calls))
    stream = await service.process_stream("owner-a", "workspace-id", {"mode": "all", "prompt": "Create", "draft": "Existing", "expected_version": 3})
    events = [event async for event in stream]
    assert "".join(event["text"] for event in events if event["event"] == "delta") == "Article [1]"
    assert events[-1]["event"] == "done"
    assert events[-1]["text"] == "Article [1]"
    call = next(item for item in calls if isinstance(item[0], str) and "Выполни задачу" in item[0])
    assert [article["text"] for article in call[1]["articles"]] == ["Text of doc-1", "Text of doc-2"]
    assert [entry[0] for entry in calls].count("revisions") == 4


@pytest.mark.asyncio
async def test_all_mode_rejects_context_overflow_without_model_call(source_module):
    from api.source_workbench.processing import ProcessingError

    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway(calls)
    gateway.load_document = lambda document: "Длинная статья с фактами. " * 1500
    service = source_module.SourceWorkspaceService(SimpleNamespace(get=lambda owner, workspace: _workspace(selection)), gateway, _processor(calls, 4096))
    stream = await service.process_stream("owner-a", "workspace-id", {"mode": "all", "prompt": "Create", "expected_version": 3})
    with pytest.raises(ProcessingError) as error:
        [event async for event in stream]
    assert error.value.code == "MODEL_CONTEXT_EXCEEDED"
    assert not any(len(call) == 3 and isinstance(call[1], dict) for call in calls)


@pytest.mark.asyncio
async def test_sequential_mode_analyzes_long_article_in_parts_and_streams_final(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway(calls)
    gateway.load_document = lambda document: "Длинная статья с фактами. " * 1500
    service = source_module.SourceWorkspaceService(SimpleNamespace(get=lambda owner, workspace: _workspace(selection)), gateway, _processor(calls, 4096))
    stream = await service.process_stream("owner-a", "workspace-id", {"mode": "sequential", "prompt": "Create", "expected_version": 3})
    events = [event async for event in stream]
    assert any(event["event"] == "status" and event["stage"] == "extract" for event in events)
    assert events[-2]["event"] == "step_done"
    assert events[-2]["strategy"] == "task_relevant_notes"
    assert events[-1]["text"] == "Article [1]"
    assert sum(1 for call in calls if len(call) == 3 and isinstance(call[1], dict) and "fragment" in call[1]) > 1


@pytest.mark.asyncio
async def test_sequential_mode_passes_section_and_table_context_to_each_fragment(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway(calls)
    header = "| Name | Amount |"
    gateway.load_document = lambda document: "# Финансы\n\n" + header + "\n| --- | --- |\n" + "".join(f"| item-{index} | {index * 7} |\n" for index in range(500))
    service = source_module.SourceWorkspaceService(SimpleNamespace(get=lambda owner, workspace: _workspace(selection)), gateway, _processor(calls, 4096))
    stream = await service.process_stream("owner-a", "workspace-id", {"mode": "sequential", "prompt": "Create", "expected_version": 3})
    events = [event async for event in stream]
    extraction_payloads = [call[1] for call in calls if len(call) == 3 and isinstance(call[1], dict) and "fragment" in call[1]]
    assert events[-1]["event"] == "done"
    assert len(extraction_payloads) > 1
    assert all(payload.get("section_path") == ["Финансы"] for payload in extraction_payloads)
    assert any(payload.get("table_columns") == header for payload in extraction_payloads[1:])


@pytest.mark.asyncio
async def test_sequential_mode_reduces_many_notes_before_final_answer(source_module):
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway([])
    gateway.load_document = lambda document: "Длинная статья с разными фактами и деталями. " * 4000
    calls = []

    async def model_stream(system, payload, output_tokens):
        calls.append(payload)
        if "fragment" in payload:
            yield "Заметка [1] " * 100
        elif "notes" in payload:
            yield "Сведённая заметка [1]"
        else:
            yield "Итог [1]"

    async def open_processor(owner):
        return SimpleNamespace(context_tokens=4096, count_tokens=num_tokens_from_string, stream=model_stream)

    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace(selection)),
        gateway,
        SimpleNamespace(open_processor=open_processor),
    )
    stream = await service.process_stream("owner-a", "workspace-id", {"mode": "sequential", "prompt": "Create", "expected_version": 3})
    events = [event async for event in stream]
    assert any(event["event"] == "status" and event["stage"] == "reduce" for event in events)
    assert events[-1]["text"] == "Итог [1]"
    assert sum("fragment" in call for call in calls) > sum("notes" in call for call in calls) > 0


@pytest.mark.asyncio
async def test_process_stream_rejects_stale_selection_before_model_open(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    service = source_module.SourceWorkspaceService(SimpleNamespace(get=lambda owner, workspace: _workspace(selection)), _gateway(calls), _processor(calls))
    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.process_stream("owner-a", "workspace-id", {"mode": "all", "prompt": "Create", "expected_version": 2})
    assert error.value.code == "VERSION_CONFLICT"
    assert not any(call[0] == "open" for call in calls)


@pytest.mark.asyncio
async def test_sequential_mode_rejects_draft_that_cannot_fit_before_chunk_processing(source_module):
    from api.source_workbench.processing import ProcessingError

    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway(calls)
    gateway.load_document = lambda document: "Статья с фактами. " * 1500
    service = source_module.SourceWorkspaceService(SimpleNamespace(get=lambda owner, workspace: _workspace(selection)), gateway, _processor(calls, 4096))
    with pytest.raises(ProcessingError) as error:
        await service.process_stream("owner-a", "workspace-id", {"mode": "sequential", "prompt": "Revise", "draft": "Черновик. " * 3000, "expected_version": 3})
    assert error.value.code == "DRAFT_TOO_LARGE_FOR_MODEL"
    assert not any(len(call) == 3 and isinstance(call[1], dict) for call in calls)


@pytest.mark.asyncio
async def test_processing_rejects_draft_that_fits_input_but_not_full_output(source_module):
    from api.source_workbench.processing import ProcessingError

    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    service = source_module.SourceWorkspaceService(SimpleNamespace(get=lambda owner, workspace: _workspace(selection)), _gateway(calls), _processor(calls, 8192))
    with pytest.raises(ProcessingError) as error:
        await service.process_stream("owner-a", "workspace-id", {"mode": "sequential", "prompt": "Revise", "draft": "word " * 1800, "expected_version": 3})
    assert error.value.code == "DRAFT_TOO_LARGE_FOR_MODEL"
    assert not any(call[0] == "load" for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "length", "code"), [("prompt", 20_001, "INVALID_INPUT"), ("draft", 100_001, "INVALID_DRAFT")])
async def test_processing_rejects_oversized_fields_before_loading(source_module, field, length, code):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    service = source_module.SourceWorkspaceService(SimpleNamespace(get=lambda owner, workspace: _workspace(selection)), _gateway(calls), _processor(calls))
    request = {"mode": "all", "prompt": "Create", "expected_version": 3, field: "x" * length}
    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.process_stream("owner-a", "workspace-id", request)
    assert error.value.code == code
    assert calls == []


@pytest.mark.asyncio
async def test_processing_does_not_mark_output_near_limit_complete(source_module):
    from api.source_workbench.processing import ProcessingError

    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]

    async def model_stream(system, payload, output_tokens):
        yield "word " * 2000

    async def open_processor(owner):
        return SimpleNamespace(context_tokens=8192, count_tokens=num_tokens_from_string, stream=model_stream)

    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace(selection)),
        _gateway(calls),
        SimpleNamespace(open_processor=open_processor),
    )
    stream = await service.process_stream("owner-a", "workspace-id", {"mode": "sequential", "prompt": "Create", "expected_version": 3})
    events = []
    with pytest.raises(ProcessingError) as error:
        async for event in stream:
            events.append(event)
    assert error.value.code == "MODEL_OUTPUT_LIMIT_REACHED"
    assert not any(event["event"] in {"step_done", "done"} for event in events)


@pytest.mark.asyncio
async def test_closing_process_stream_closes_model_generator(source_module):
    calls = []
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    gateway = _gateway(calls)
    gateway.load_document = lambda document: "Article text"

    async def model_stream(system, payload, output_tokens):
        try:
            yield "Visible text that continues"
            yield "more"
        finally:
            calls.append(("closed",))

    async def open_processor(owner):
        return SimpleNamespace(context_tokens=8192, count_tokens=num_tokens_from_string, stream=model_stream)

    service = source_module.SourceWorkspaceService(
        SimpleNamespace(get=lambda owner, workspace: _workspace(selection)),
        gateway,
        SimpleNamespace(open_processor=open_processor),
    )
    stream = await service.process_stream("owner-a", "workspace-id", {"mode": "all", "prompt": "Create", "expected_version": 3})
    while (await anext(stream))["event"] != "delta":
        pass
    await stream.aclose()
    assert ("closed",) in calls


@pytest.mark.asyncio
async def test_saved_draft_pins_server_selection_and_rejects_stale_version(source_module):
    selection = [{"dataset_id": "dataset-a", "document_id": "doc-1", "revision": "hash:1:2"}]
    created = []
    repository = SimpleNamespace(
        get=lambda owner, workspace: _workspace(selection),
        create_draft=lambda *args: created.append(args) or {"id": "draft-1", "sources": args[-1]},
    )
    service = source_module.SourceWorkspaceService(repository, _gateway([]))

    draft = await service.save_draft("owner-a", "workspace-id", {"content": "Reviewed text", "prompt": "Create", "mode": "all", "expected_version": 3})
    assert draft["sources"] == selection
    assert created[0][:2] == ("owner-a", "workspace-id")

    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.save_draft("owner-a", "workspace-id", {"content": "Old result", "prompt": "Create", "mode": "all", "expected_version": 2})
    assert error.value.code == "VERSION_CONFLICT"
    assert len(created) == 1


@pytest.mark.asyncio
async def test_draft_edit_requires_version_and_workspace_owner(source_module):
    calls = []

    def get(owner, workspace):
        if owner != "owner-a":
            raise source_module.SourceWorkspaceError("NOT_FOUND", "Workspace not found", 404)
        return _workspace()

    repository = SimpleNamespace(
        get=get,
        update_draft=lambda *args: calls.append(args) or {"id": args[2], "version": args[3] + 1},
    )
    service = source_module.SourceWorkspaceService(repository, _gateway([]))
    updated = await service.update_draft("owner-a", "workspace-id", "draft-1", {"content": "Edited", "expected_version": 1})
    assert updated["version"] == 2
    assert calls == [("owner-a", "workspace-id", "draft-1", 1, "Edited")]

    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.update_draft("other", "workspace-id", "draft-1", {"content": "Stolen", "expected_version": 1})
    assert error.value.status == 404
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_revoked_dataset_access_blocks_saved_draft_reads(source_module):
    def denied(_owner, _datasets):
        raise source_module.SourceWorkspaceError("DATASET_UNAVAILABLE", "Dataset unavailable", 403)

    repository = SimpleNamespace(
        get=lambda owner, workspace: _workspace(),
        list_drafts=lambda *_args: pytest.fail("Draft text must not be read"),
    )
    gateway = _gateway([])
    gateway.validate_datasets = denied
    service = source_module.SourceWorkspaceService(repository, gateway)

    with pytest.raises(source_module.SourceWorkspaceError) as error:
        await service.list_drafts("owner-a", "workspace-id")
    assert error.value.status == 403
