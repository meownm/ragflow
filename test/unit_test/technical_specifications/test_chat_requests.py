import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from api.technical_specifications.application import Specifications
from api.technical_specifications.domain import SpecificationError, empty_sections


async def run(method, *args, **kwargs):
    return method(*args, **kwargs)


def setup(store, ai):
    doc = store.create("author", "workspace", "ТЗ", empty_sections())
    request = {"version": doc["version"], "draft": {"message_id": "request", "text": "Уточни сценарий", "context": {"document_id": doc["id"], "scope": "all", "revision": 1, "anchor": None}}}
    return Specifications(store, ai, run), doc, request


@pytest.mark.asyncio
async def test_comment_during_generation_preserves_the_accepted_request_and_answer(store):
    class AI:
        async def chat(self, actor, document, message):
            current = store.get(actor, "workspace", document["id"])
            assert current["messages"][0]["status"] == "processing"
            await service.comment(actor, "workspace", document["id"], {"version": current["version"], "message_id": "comment", "text": "Параллельное обсуждение", "context": message["context"]})
            return {"answer": "Готовый ответ", "patches": {"5": "Новый сценарий"}}

    service, doc, request = setup(store, AI())
    result = await service.chat("author", "workspace", doc["id"], request)
    assert len(result["messages"]) == 2 and len(result["comments"]) == 1
    assert result["messages"][0]["status"] == "completed"
    assert result["messages"][1]["text"] == "Готовый ответ"
    assert result["sections"] == doc["sections"] and result["revision"] == 1


@pytest.mark.asyncio
async def test_failed_request_survives_and_retries_without_duplicate_messages(store):
    class AI:
        calls = 0

        async def chat(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError()
            return {"answer": "Повтор завершён", "patches": {}}

    ai = AI()
    service, doc, request = setup(store, ai)
    with pytest.raises(TimeoutError):
        await service.chat("author", "workspace", doc["id"], request)
    saved = store.get("author", "workspace", doc["id"])
    assert saved["messages"][0]["status"] == "failed"
    assert saved["messages"][0]["context"] == request["draft"]["context"]
    result = await service.chat("author", "workspace", doc["id"], request)
    assert len(result["messages"]) == 2 and ai.calls == 2
    assert await service.chat("author", "workspace", doc["id"], request) == result
    assert ai.calls == 2


@pytest.mark.asyncio
async def test_retry_while_request_is_running_does_not_call_the_model_twice(store):
    entered, release = asyncio.Event(), asyncio.Event()

    class AI:
        calls = 0

        async def chat(self, *args):
            self.calls += 1
            entered.set()
            await release.wait()
            return {"answer": "Ответ", "patches": {}}

    ai = AI()
    service, doc, request = setup(store, ai)
    first = asyncio.create_task(service.chat("author", "workspace", doc["id"], request))
    await entered.wait()
    try:
        pending = await service.chat("author", "workspace", doc["id"], request)
        assert pending["messages"][0]["status"] == "processing" and ai.calls == 1
    finally:
        release.set()
    result = await first
    assert len(result["messages"]) == 2 and ai.calls == 1


@pytest.mark.asyncio
async def test_expired_processing_request_can_be_reclaimed_after_restart(store):
    class AI:
        async def chat(self, *args):
            return {"answer": "Восстановленный запрос", "patches": {}}

    service, doc, request = setup(store, AI())

    def seed(value):
        message = request["draft"]
        value["messages"].append(
            {
                "id": message["message_id"],
                "role": "user",
                "author_id": "author",
                "text": message["text"],
                "context": message["context"],
                "status": "processing",
                "attempt": "interrupted",
                "source_revision": 1,
                "started_at": (datetime.now(UTC) - timedelta(minutes=7)).isoformat(),
            }
        )

    store.change("author", "workspace", doc["id"], doc["version"], seed, "chat-accepted")
    result = await service.chat("author", "workspace", doc["id"], request)
    assert result["messages"][0]["status"] == "completed"
    assert len(result["messages"]) == 2


@pytest.mark.asyncio
async def test_document_edit_during_chat_keeps_answer_but_stale_proposal_cannot_apply(store):
    class AI:
        async def chat(self, actor, document, message):
            current = store.get(actor, "workspace", document["id"])
            store.change(actor, "workspace", document["id"], current["version"], lambda value: value["sections"].update({"9": "Другая правка"}), "edit")
            return {"answer": "Ответ к прежней редакции", "patches": {"5": "Сценарий"}}

    service, doc, request = setup(store, AI())
    result = await service.chat("author", "workspace", doc["id"], request)
    assert result["revision"] == 2 and result["sections"]["9"] == "Другая правка"
    assert result["messages"][0]["status"] == "completed"
    assert result["proposals"][0]["base_revision"] == 1
    with pytest.raises(SpecificationError) as error:
        await service.apply("author", "workspace", doc["id"], {"version": result["version"], "proposal_id": result["proposals"][0]["id"]})
    assert error.value.code == "STALE_PROPOSAL"


@pytest.mark.asyncio
async def test_cancellation_keeps_the_original_message_and_allows_retry(store):
    entered = asyncio.Event()

    class AI:
        calls = 0

        async def chat(self, *args):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                await asyncio.Event().wait()
            return {"answer": "Повтор после отмены", "patches": {}}

    service, doc, request = setup(store, AI())
    task = asyncio.create_task(service.chat("author", "workspace", doc["id"], request))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get("author", "workspace", doc["id"])["messages"][0]["status"] == "failed"
    result = await service.chat("author", "workspace", doc["id"], request)
    assert len(result["messages"]) == 2


@pytest.mark.asyncio
async def test_abandoned_attempt_cannot_replace_the_result_of_a_new_attempt(store):
    entered, release = asyncio.Event(), asyncio.Event()

    class AI:
        calls = 0

        async def chat(self, *args):
            self.calls += 1
            number = self.calls
            if number == 1:
                entered.set()
                await release.wait()
            return {"answer": f"Попытка {number}", "patches": {}}

    service, doc, request = setup(store, AI())
    first = asyncio.create_task(service.chat("author", "workspace", doc["id"], request))
    await entered.wait()
    try:
        current = store.get("author", "workspace", doc["id"])
        store.change(
            "author", "workspace", doc["id"], current["version"], lambda value: value["messages"][0].update(started_at=(datetime.now(UTC) - timedelta(minutes=7)).isoformat()), "expired-test-attempt"
        )
        second = await service.chat("author", "workspace", doc["id"], request)
        assert second["messages"][1]["text"] == "Попытка 2"
    finally:
        release.set()
    await first
    saved = store.get("author", "workspace", doc["id"])
    assert len(saved["messages"]) == 2 and saved["messages"][1]["text"] == "Попытка 2"


@pytest.mark.asyncio
async def test_result_write_failure_marks_request_failed_and_retry_is_idempotent(store, monkeypatch):
    class AI:
        calls = 0

        async def chat(self, *args):
            self.calls += 1
            return {"answer": "Ответ", "patches": {"5": "Новый сценарий"}}

    ai = AI()
    service, doc, request = setup(store, ai)
    change = store.change

    def interrupted(actor, workspace, document_id, expected, mutate, reason, **kwargs):
        if reason == "chat-completed":
            raise RuntimeError("result write interrupted")
        return change(actor, workspace, document_id, expected, mutate, reason, **kwargs)

    monkeypatch.setattr(store, "change", interrupted)
    with pytest.raises(RuntimeError, match="result write interrupted"):
        await service.chat("author", "workspace", doc["id"], request)
    saved = store.get("author", "workspace", doc["id"])
    assert len(saved["messages"]) == 1 and saved["proposals"] == []
    assert saved["messages"][0]["status"] == "failed"
    assert saved["messages"][0]["error"]["code"] == "RESULT_SAVE_FAILED"
    assert saved["messages"][0]["context"] == request["draft"]["context"]
    monkeypatch.setattr(store, "change", change)
    result = await service.chat("author", "workspace", doc["id"], request)
    assert result["messages"][0]["status"] == "completed"
    assert len(result["messages"]) == 2 and len(result["proposals"]) == 1
    assert await service.chat("author", "workspace", doc["id"], request) == result
    assert ai.calls == 2


@pytest.mark.asyncio
async def test_large_document_can_prepare_twenty_changes_without_copying_unchanged_sections(store):
    import json

    from api.db.db_models import TechnicalSpecification

    class AI:
        calls = 0

        async def chat(self, *args):
            self.calls += 1
            return {"answer": "Ответ", "patches": {"5": f"Сценарий {self.calls}"}}

    content = {str(i): "" for i in range(1, 10)}
    content["1"] = "x" * 99_000
    doc = store.create("author", "workspace", "ТЗ", content)
    ai = AI()
    service = Specifications(store, ai, run)
    for number in range(20):
        request = {"version": doc["version"], "draft": {"message_id": str(number), "text": "Уточни сценарий", "context": {"document_id": doc["id"], "scope": "all", "revision": 1, "anchor": None}}}
        doc = await service.chat("author", "workspace", doc["id"], request)
    assert ai.calls == 20 and len(doc["messages"]) == 40 and len(doc["proposals"]) == 20
    assert all(item["status"] == "completed" for item in doc["messages"] if item["role"] == "user")
    assert all(item["sections"]["1"] == content["1"] for item in doc["proposals"])
    stored = TechnicalSpecification.get_by_id(doc["id"]).payload
    assert len(json.dumps(stored).encode()) < 140_000
    assert all("sections" not in item and set(item["patches"]) == {"5"} for item in stored["proposals"])


@pytest.mark.asyncio
async def test_insufficient_result_capacity_rejects_request_before_model_and_keeps_existing_history(store):
    from api.technical_specifications.storage import CHAT_RESULT_BYTES, MAX_PROJECTION_BYTES

    class AI:
        calls = 0

        async def chat(self, *args):
            self.calls += 1
            return {"answer": "Ответ", "patches": {}}

    ai = AI()
    service, doc, request = setup(store, ai)

    def fill(value):
        value["messages"].append({"id": "earlier", "role": "assistant", "text": "x" * (MAX_PROJECTION_BYTES - CHAT_RESULT_BYTES)})

    saved = store.change("author", "workspace", doc["id"], 1, fill, "seed-history")
    request["version"] = saved["version"]
    with pytest.raises(SpecificationError) as error:
        await service.chat("author", "workspace", doc["id"], request)
    assert error.value.code == "DOCUMENT_TOO_LARGE" and ai.calls == 0
    assert store.get("author", "workspace", doc["id"]) == saved


@pytest.mark.asyncio
async def test_pending_request_reserves_space_against_parallel_history_growth(store):
    from api.technical_specifications.storage import MAX_PROJECTION_BYTES

    class AI:
        async def chat(self, actor, document, message):
            current = store.get(actor, "workspace", document["id"])

            def fill(value):
                value["messages"].append({"id": "too-large", "role": "assistant", "text": "x" * (MAX_PROJECTION_BYTES - 1000)})

            with pytest.raises(SpecificationError) as error:
                store.change(actor, "workspace", document["id"], current["version"], fill, "parallel-growth")
            assert error.value.code == "DOCUMENT_TOO_LARGE"
            # Exercise the maximum JSON escaping, not only ordinary UTF-8.
            return {"answer": "\x00" * 30_000, "patches": {"5": "\x00" * 99_000}}

    service, doc, request = setup(store, AI())
    result = await service.chat("author", "workspace", doc["id"], request)
    assert result["messages"][0]["status"] == "completed"
    assert len(result["messages"]) == 2 and len(result["proposals"]) == 1
