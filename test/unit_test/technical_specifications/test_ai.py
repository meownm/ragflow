from copy import deepcopy

import pytest

from api.technical_specifications.ai import SpecificationAI
from api.technical_specifications.domain import empty_sections


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
        return {"answer": "Готово", "patches": {}}

    monkeypatch.setattr(ai, "_ask", ask)
    assert await ai.chat("author", document, message) == {"answer": "Готово", "patches": {}}
    assert document == original
