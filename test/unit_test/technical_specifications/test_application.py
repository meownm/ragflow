from copy import deepcopy

import pytest

from api.technical_specifications.application import Specifications
from api.technical_specifications.domain import SpecificationError, empty_sections, fail


async def run(method, *args, **kwargs):
    return method(*args, **kwargs)


class Repository:
    def __init__(self):
        self.value = {"id": "doc", "title": "ТЗ", "version": 1, "revision": 1, "sections": empty_sections(), "messages": [], "comments": [], "proposals": [], "eva": None}
        self.revisions = [deepcopy(self.value["sections"])]

    def access(self, actor, workspace, capability="read"):
        if actor != "editor" or workspace != "workspace":
            fail("FORBIDDEN", "Нет доступа", 403)

    def get(self, actor, workspace, document_id):
        self.access(actor, workspace)
        assert document_id == "doc"
        return deepcopy(self.value)

    def change(self, actor, workspace, document_id, expected, mutate, reason, *, latest=False):
        value = self.get(actor, workspace, document_id)
        if not latest and value["version"] != expected:
            fail("VERSION_CONFLICT", "Версия изменилась", 409)
        mutate(value)
        if value["sections"] != self.value["sections"]:
            value["revision"] += 1
            self.revisions.append(deepcopy(value["sections"]))
        value["version"] += 1
        self.value = value
        return deepcopy(value)


class AI:
    def __init__(self):
        self.result = {"answer": "Предложение", "patches": {"5": "Новый сценарий"}}
        self.verification = {"valid": True, "issues": []}
        self.calls = []
        self.change_request = None

    async def chat(self, actor, document, message):
        self.calls.append((document, message))
        return deepcopy(self.result)

    async def verify(self, actor, before, after, *, change_request=None):
        self.change_request = deepcopy(change_request)
        self.calls.append((deepcopy(before), deepcopy(after)))
        return deepcopy(self.verification)


@pytest.fixture
def env():
    repository, ai = Repository(), AI()
    return Specifications(repository, ai, run), repository, ai


def message(scope="all"):
    return {"version": 1, "draft": {"message_id": "message", "text": "Уточни сценарий", "context": {"document_id": "doc", "scope": scope, "anchor": None, "revision": 1}}}


@pytest.mark.asyncio
async def test_message_retry_returns_existing_result_without_model_or_duplicates(env):
    service, repo, ai = env
    request = message()
    first = await service.chat("editor", "workspace", "doc", request)
    repeated = await service.chat("editor", "workspace", "doc", request)
    assert repeated == first and len(ai.calls) == 1 and len(repo.value["messages"]) == 2
    request["draft"]["text"] = "Другое содержимое с прежним ключом"
    with pytest.raises(SpecificationError) as error:
        await service.chat("editor", "workspace", "doc", request)
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
async def test_chat_then_one_apply_creates_one_revision_without_approval(env):
    service, repo, _ai = env
    proposal_state = await service.chat("editor", "workspace", "doc", message())
    assert proposal_state["sections"]["5"] == ""
    assert proposal_state["revision"] == 1
    request = {"version": proposal_state["version"], "proposal_id": proposal_state["proposals"][0]["id"]}
    result = await service.apply("editor", "workspace", "doc", request)
    assert result["sections"]["5"] == "Новый сценарий"
    assert result["revision"] == 2
    assert result["proposals"][0]["applied_revision"] == 2
    assert len(repo.revisions) == 2
    assert await service.apply("editor", "workspace", "doc", request) == result
    assert len(repo.revisions) == 2


@pytest.mark.asyncio
async def test_semantic_contradiction_blocks_apply_and_preserves_proposal(env):
    service, repo, ai = env
    ai.result["patches"] = {"4": "Лимит 35", "5": "Выбрать 50", "8": "Максимум 50"}
    state = await service.chat("editor", "workspace", "doc", message())
    ai.verification = {"valid": False, "issues": ["Разделы 4, 5, 8: разные значения одного лимита"]}
    before = deepcopy(repo.value)
    blocked = await service.apply("editor", "workspace", "doc", {"version": state["version"], "proposal_id": state["proposals"][0]["id"]})
    assert blocked["proposals"][0]["status"] == "needs_resolution"
    assert blocked["proposals"][0]["issues"] == ai.verification["issues"]
    assert repo.value["sections"] == before["sections"]
    assert len(ai.calls[-1][1]) == 9


@pytest.mark.asyncio
async def test_parallel_edit_while_verification_runs_cannot_be_overwritten(env):
    service, repo, ai = env
    state = await service.chat("editor", "workspace", "doc", message())

    async def racing_verify(actor, before, after, **kwargs):
        repo.change(actor, "workspace", "doc", repo.value["version"], lambda value: value["sections"].update({"9": "Параллельная правка"}), "edit")
        return {"valid": True, "issues": []}

    ai.verify = racing_verify
    with pytest.raises(SpecificationError) as error:
        await service.apply("editor", "workspace", "doc", {"version": state["version"], "proposal_id": state["proposals"][0]["id"]})
    assert error.value.code == "STALE_PROPOSAL"
    assert repo.value["sections"]["9"] == "Параллельная правка"
    assert repo.value["sections"]["5"] == ""


@pytest.mark.asyncio
async def test_apply_uses_linked_saved_request_not_latest_message_or_client_override(env):
    service, repo, ai = env
    first = message()
    state = await service.chat("editor", "workspace", "doc", first)
    proposal = state["proposals"][0]
    assert proposal["message_id"] == first["draft"]["message_id"]
    later = message()
    later["version"] = state["version"]
    later["draft"].update(message_id="later", text="Совсем другая правка")
    state = await service.chat("editor", "workspace", "doc", later)
    await service.apply("editor", "workspace", "doc", {"version": state["version"], "proposal_id": proposal["id"], "change_request": "Подмена клиента"})
    assert ai.change_request == {"text": first["draft"]["text"], "context": first["draft"]["context"], "history": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing", "author", "revision"])
async def test_broken_request_link_cannot_apply_or_consume_verifier(env, corruption):
    service, repo, ai = env
    state = await service.chat("editor", "workspace", "doc", message())
    if corruption == "missing":
        repo.value["messages"].clear()
    elif corruption == "author":
        repo.value["messages"][0]["author_id"] = "stranger"
    else:
        repo.value["messages"][0]["source_revision"] = 0
    with pytest.raises(SpecificationError) as error:
        await service.apply("editor", "workspace", "doc", {"version": state["version"], "proposal_id": state["proposals"][0]["id"]})
    assert error.value.code == "INVALID_PROPOSAL"
    assert len(ai.calls) == 1 and len(repo.revisions) == 1


@pytest.mark.asyncio
async def test_existing_proposal_without_request_link_remains_applicable(env):
    service, repo, ai = env
    state = await service.chat("editor", "workspace", "doc", message())
    repo.value["proposals"][0].pop("message_id")
    result = await service.apply("editor", "workspace", "doc", {"version": state["version"], "proposal_id": state["proposals"][0]["id"]})
    assert result["revision"] == 2 and ai.change_request is None


@pytest.mark.asyncio
async def test_corrective_request_keeps_prior_request_as_history(env):
    service, repo, ai = env
    first = message()
    first["draft"]["text"] = "Измени только API, оставив старую настройку"
    state = await service.chat("editor", "workspace", "doc", first)
    ai.verification = {"valid": False, "issues": ["Разные ограничения"]}
    state = await service.apply("editor", "workspace", "doc", {"version": state["version"], "proposal_id": state["proposals"][0]["id"]})
    corrective = message()
    corrective["version"] = state["version"]
    corrective["draft"].update(message_id="correction", text="Верни согласованное ограничение и замени сообщение ошибки")
    state = await service.chat("editor", "workspace", "doc", corrective)
    ai.verification = {"valid": True, "issues": []}
    result = await service.apply("editor", "workspace", "doc", {"version": state["version"], "proposal_id": state["proposals"][-1]["id"]})
    assert result["revision"] == 2 and len(repo.revisions) == 2
    assert result["proposals"][0]["status"] == "needs_resolution"
    assert ai.change_request == {"text": corrective["draft"]["text"], "context": corrective["draft"]["context"], "history": [{"text": first["draft"]["text"], "context": first["draft"]["context"]}]}


@pytest.mark.asyncio
async def test_scope_cannot_be_silently_expanded_by_model(env):
    service, repo, _ai = env
    before = deepcopy(repo.value)
    with pytest.raises(SpecificationError) as error:
        await service.chat("editor", "workspace", "doc", message("4"))
    assert error.value.code == "OUTSIDE_SCOPE"
    assert repo.value["sections"] == before["sections"]
    assert repo.value["messages"][0]["status"] == "failed"
    assert repo.value["messages"][0]["text"] == message("4")["draft"]["text"]


@pytest.mark.asyncio
async def test_stale_quote_blocks_model_call_without_losing_input(env):
    service, _repo, ai = env
    data = message("selection")
    data["draft"]["context"]["anchor"] = {"section": "5", "quote": "Удалённый фрагмент", "prefix": "", "suffix": ""}
    before = deepcopy(data)
    with pytest.raises(SpecificationError) as error:
        await service.chat("editor", "workspace", "doc", data)
    assert error.value.code == "STALE_CONTEXT"
    assert data == before
    assert not ai.calls


@pytest.mark.asyncio
async def test_authorization_precedes_model_use(env):
    service, _repo, ai = env
    with pytest.raises(SpecificationError):
        await service.chat("stranger", "workspace", "doc", message())
    assert not ai.calls


@pytest.mark.asyncio
async def test_comments_replies_and_resolution_do_not_change_document(env):
    service, repo, _ai = env
    data = {"version": 1, "message_id": "comment", "text": "Уточнить", "context": message()["draft"]["context"]}
    first = await service.comment("editor", "workspace", "doc", data)
    item = first["comments"][0]
    second = await service.comment("editor", "workspace", "doc", {"version": first["version"], "message_id": "reply", "action": "reply", "comment_id": item["id"], "text": "Ответ"})
    third = await service.comment("editor", "workspace", "doc", {"version": second["version"], "action": "resolve", "comment_id": item["id"], "resolved": True})
    assert third["comments"][0]["resolved"] is True
    assert third["comments"][0]["replies"][0]["text"] == "Ответ"
    assert third["revision"] == 1
    assert len(repo.revisions) == 1
    assert not third["messages"]


@pytest.mark.asyncio
async def test_comment_retry_keeps_one_comment_and_reply(env):
    service, repo, _ai = env
    data = {"version": 1, "message_id": "comment", "text": "Уточнить", "context": message()["draft"]["context"]}
    first = await service.comment("editor", "workspace", "doc", data)
    repeated = await service.comment("editor", "workspace", "doc", data)
    assert repeated == first
    reply = {"version": first["version"], "message_id": "reply", "action": "reply", "comment_id": "comment", "text": "Ответ"}
    second = await service.comment("editor", "workspace", "doc", reply)
    assert await service.comment("editor", "workspace", "doc", reply) == second
    assert len(second["comments"]) == len(second["comments"][0]["replies"]) == 1
    assert len(repo.revisions) == 1
    for changed in ({**data, "text": "Другой текст"}, {**reply, "comment_id": "другая ветка"}):
        with pytest.raises(SpecificationError) as error:
            await service.comment("editor", "workspace", "doc", changed)
        assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
