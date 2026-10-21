import pytest

from api.technical_specifications.application import Specifications
from test.unit_test.technical_specifications.test_application import AI, Repository, message, run


@pytest.mark.asyncio
async def test_proposals_keep_the_originating_request_after_later_answers_and_retry():
    repository, ai = Repository(), AI()
    service = Specifications(repository, ai, run)
    first = message()
    result = await service.chat("editor", "workspace", "doc", first)
    second = message()
    second["version"] = result["version"]
    second["draft"].update(message_id="later", text="Исправь предложение")
    result = await service.chat("editor", "workspace", "doc", second)
    assert [proposal["message_id"] for proposal in result["proposals"]] == ["message", "later"]
    assert [item["reply_to"] for item in result["messages"] if item["role"] == "assistant"] == ["message", "later"]
    assert result["sections"] == repository.revisions[0]
    assert await service.chat("editor", "workspace", "doc", second) == result
    assert len(ai.calls) == 2
