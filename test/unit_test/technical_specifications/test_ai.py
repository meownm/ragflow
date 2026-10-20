from copy import deepcopy

import pytest

from api.technical_specifications.ai import RAGFlowAnswerGateway, SpecificationAI
from api.technical_specifications.domain import HEADINGS, SpecificationError, empty_sections, scoped_chat_result


@pytest.mark.asyncio
async def test_chat_preserves_full_context_and_proposal_issues_without_repeating_unchanged_sections(monkeypatch):
    content = empty_sections()
    content["1"] = "x" * 99_000
    proposal = {
        "id": "proposal",
        "base_revision": 2,
        "status": "needs_resolution",
        "source": "chat",
        "sections": {**content, "5": "Исправить ограничение"},
        "issues": ["Разделы 4 и 5 противоречат друг другу"],
    }
    document = {"revision": 2, "sections": content, "messages": [], "proposals": [proposal, {**proposal, "id": "old", "base_revision": 1}, {**proposal, "id": "applied", "status": "applied"}]}
    original = deepcopy(document)
    message = {"message_id": "message", "text": "Исправь предложение", "context": {"scope": "all", "revision": 2}}
    ai = SpecificationAI()

    async def ask(actor, system, payload):
        assert payload["sections"] == content and payload["message"] == message
        assert len(payload["proposals"]) == 1
        supplied = payload["proposals"][0]
        assert supplied["patches"] == {"5": "Исправить ограничение"} and "sections" not in supplied
        assert supplied["issues"] == proposal["issues"]
        assert supplied["base_revision"] == 2 and supplied["id"] == "proposal"
        return {"required_sections": [], "answer": "Готово", "patches": {}}

    monkeypatch.setattr(ai, "_ask", ask)
    assert await ai.chat("author", document, message) == {"answer": "Готово", "patches": {}}
    assert document == original


@pytest.mark.parametrize("context", [{"scope": "5"}, {"scope": "selection", "anchor": {"section": "5"}}])
def test_scope_mismatch_never_returns_partial_model_patches(context):
    result = {"required_sections": ["4", "5", "8"], "answer": "Можно частично изменить раздел 5", "patches": {"5": "Новый лимит"}}
    original = deepcopy(result)
    reply = scoped_chat_result(result, context)
    assert reply["patches"] == {} and "Всё ТЗ" in reply["answer"]
    assert result == original
    assert scoped_chat_result(result, {"scope": "all"}) == {"answer": result["answer"], "patches": result["patches"]}


@pytest.mark.parametrize("required", [None, "5", ["10"], [{}], []])
def test_missing_invalid_or_incomplete_declared_sections_fail_closed(required):
    with pytest.raises(SpecificationError) as error:
        scoped_chat_result({"required_sections": required, "answer": "Предлагаю", "patches": {"5": "Правка"}}, {"scope": "5"})
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_structured_call_filters_reasoning_and_never_retries_invalid_json(monkeypatch, valid):
    calls = []

    class Model:
        context_tokens = 4096
        output_tokens = None
        count_tokens = staticmethod(lambda value: max(1, len(value) // 2))

        async def stream(self, system, payload, limit, *, json_response=False):
            calls.append({"json_response": json_response, "payload": payload})
            yield "<think>Internal reasoning</think>"
            yield '{"valid":true,"issues":[]}' if valid else "No JSON answer"

    async def open_processor(_actor):
        return Model()

    monkeypatch.setattr(RAGFlowAnswerGateway, "open_processor", open_processor)
    if valid:
        assert await SpecificationAI()._ask("author", "Return JSON", {"case": "synthetic"}) == {"valid": True, "issues": []}
    else:
        with pytest.raises(SpecificationError) as error:
            await SpecificationAI()._ask("author", "Return JSON", {"case": "synthetic"})
        assert error.value.code == "MODEL_INVALID_OUTPUT"
    assert calls == [{"json_response": True, "payload": {"case": "synthetic"}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("merging", [False, True])
async def test_verification_separates_result_from_complete_source_versions(monkeypatch, merging):
    base = empty_sections()
    local = {**base, "4": "Локальная настройка", "9": "Локальный журнал"}
    remote = {**base, "4": "Настройка Eva", "9": "Независимая правка Eva"}
    candidate = {**remote, "4": local["4"]}
    original = deepcopy((base, local, remote, candidate))
    ai = SpecificationAI()

    async def ask(actor, _system, payload):
        assert actor == "author" and payload["choice"] == ("local" if merging else None)
        assert [item["section"] for item in payload["comparison"]] == list(candidate)
        for item in payload["comparison"]:
            key = item["section"]
            expected = {"base": base[key], "local": local[key], "remote": remote[key]} if merging else {"original": local[key]}
            assert item["result"] == candidate[key] and item["sources"] == expected
            assert item["heading"] == HEADINGS[int(key) - 1]
        return {"valid": True, "issues": []}

    monkeypatch.setattr(ai, "_ask", ask)
    merge = {"base": base, "remote": remote, "choice": "local"} if merging else None
    assert await ai.verify("author", local, candidate, merge) == {"valid": True, "issues": []}
    assert (base, local, remote, candidate) == original
