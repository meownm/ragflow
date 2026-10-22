import json
from copy import deepcopy

import pytest

from api.technical_specifications.ai import RAGFlowAnswerGateway, SpecificationAI
from api.technical_specifications.domain import HEADINGS, SpecificationError, empty_sections, scoped_chat_result
from api.document_exchange import merge as text_merge
from api.technical_specifications.ai import _changes


@pytest.mark.asyncio
async def test_chat_generation_requires_edits_and_preserves_literal_changes(monkeypatch):
    from jsonschema import Draft202012Validator

    calls, counted = [], []

    class Model:
        context_tokens = 32768

        def count_tokens(self, value):
            counted.append(value)
            return max(1, len(value) // 3)

        async def stream(self, _system, payload, _limit, *, json_response=False, thinking=False):
            assert thinking is True
            assert json_response == payload["response_schema"]
            validator = Draft202012Validator(json_response)
            observed_failure = {"base_proposal_id": None, "required_sections": ["5"], "answer": "Подготовлено предложение"}
            assert not validator.is_valid(observed_failure)
            response = {**observed_failure, "edits": [{"section": "5", "before": "Выберите CSV.", "after": "Выберите файл CSV с заявками."}]}
            validator.validate(response)
            calls.append(payload)
            yield json.dumps(response, ensure_ascii=False)

    async def open_processor(_actor):
        return Model()

    monkeypatch.setattr(RAGFlowAnswerGateway, "open_processor", open_processor)
    content = {**empty_sections(), "5": "Выберите CSV."}
    document = {"revision": 1, "sections": content, "messages": [], "proposals": []}
    message = {"message_id": "message", "text": "Уточни пояснение", "context": {"scope": "all", "revision": 1}}
    result = await SpecificationAI().chat("author", document, message)
    assert result["patches"] == {"5": "Выберите файл CSV с заявками."}
    assert len(calls) == 1 and any('"response_schema"' in value for value in counted)


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

    async def ask(actor, system, payload, *, thinking=False):
        assert thinking is True
        assert payload["sections"] == content and payload["message"] == message
        assert len(payload["proposals"]) == 1
        supplied = payload["proposals"][0]
        assert supplied["patches"] == {"5": "Исправить ограничение"} and "sections" not in supplied
        assert supplied["issues"] == proposal["issues"]
        assert supplied["base_revision"] == 2 and supplied["id"] == "proposal"
        return {"required_sections": [], "answer": "Готово", "edits": []}

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


@pytest.mark.parametrize("required", [None, "5", ["10"], ["4.2"], [{}], []])
def test_missing_invalid_or_incomplete_declared_sections_fail_closed(required):
    with pytest.raises(SpecificationError) as error:
        scoped_chat_result({"required_sections": required, "answer": "Предлагаю", "patches": {"5": "Правка"}}, {"scope": "5"})
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("thinking", [False, True])
async def test_structured_call_filters_reasoning_and_never_retries_invalid_json(monkeypatch, valid, thinking):
    calls = []

    class Model:
        context_tokens = 4096
        output_tokens = None
        count_tokens = staticmethod(lambda value: max(1, len(value) // 2))

        async def stream(self, system, payload, limit, *, json_response=False, thinking=False):
            calls.append({"json_response": json_response, "payload": payload, "thinking": thinking})
            yield "<think>Internal reasoning</think>"
            yield '{"valid":true,"issues":[]}' if valid else "No JSON answer"

    async def open_processor(_actor):
        return Model()

    monkeypatch.setattr(RAGFlowAnswerGateway, "open_processor", open_processor)
    if valid:
        assert await SpecificationAI()._ask("author", "Return JSON", {"case": "synthetic"}, thinking=thinking) == {"valid": True, "issues": []}
    else:
        with pytest.raises(SpecificationError) as error:
            await SpecificationAI()._ask("author", "Return JSON", {"case": "synthetic"}, thinking=thinking)
        assert error.value.code == "MODEL_INVALID_OUTPUT"
    assert calls == [{"json_response": True, "payload": {"case": "synthetic"}, "thinking": thinking}]


@pytest.mark.asyncio
@pytest.mark.parametrize("merging", [False, True])
async def test_verification_separates_result_from_complete_source_versions(monkeypatch, merging):
    base = empty_sections()
    local = {**base, "4": "Локальная настройка", "9": "Локальный журнал"}
    remote = {**base, "4": "Настройка Eva", "9": "Независимая правка Eva"}
    candidate = {**remote, "4": local["4"]}
    original = deepcopy((base, local, remote, candidate))
    ai = SpecificationAI()

    async def ask(actor, _system, payload, *, thinking=False):
        assert thinking is True
        assert actor == "author" and "choice" not in payload
        if merging:
            assert payload["conflict_resolution"] == {"only_when": "both_sides_changed_same_fact", "preferred_source": "local"}
        else:
            assert "conflict_resolution" not in payload
            assert payload["change_request"] is None
        assert [item["section"] for item in payload["comparison"]] == list(candidate)
        for item in payload["comparison"]:
            key = item["section"]
            expected = {"base": base[key], "local": local[key], "remote": remote[key]} if merging else {"original": local[key]}
            assert item["result"] == candidate[key] and item["sources"] == expected
            assert item["heading"] == HEADINGS[int(key) - 1]
            if not merging:
                assert "changes" not in item
        return {"valid": True, "issues": []}

    monkeypatch.setattr(ai, "_ask", ask)
    merge = {"base": base, "remote": remote, "choice": "local"} if merging else None
    assert await ai.verify("author", local, candidate, merge) == {"valid": True, "issues": []}
    assert (base, local, remote, candidate) == original


@pytest.mark.asyncio
async def test_verification_receives_exact_author_request_without_mixing_it_with_document_sources(monkeypatch):
    original = {**empty_sections(), "8": 'LIMIT_EXCEEDED: "Слишком большой файл."'}
    candidate = {**original, "8": 'LIMIT_EXCEEDED: "Размер CSV превышает 20 МБ."'}
    request = {"text": "Замени сообщение ошибки во всех упоминаниях", "context": {"scope": "8", "revision": 2}, "history": []}
    snapshot = deepcopy((original, candidate, request))
    ai = SpecificationAI()

    async def ask(actor, system, payload, *, thinking=False):
        assert thinking is True
        assert payload["change_request"] == request
        assert payload["comparison"][7]["sources"] == {"original": original["8"]}
        assert payload["comparison"][7]["result"] == candidate["8"]
        return {"valid": True, "issues": []}

    monkeypatch.setattr(ai, "_ask", ask)
    assert await ai.verify("author", original, candidate, change_request=request) == {"valid": True, "issues": []}
    assert (original, candidate, request) == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["all", "5", "selection"])
async def test_chat_literal_edits_preserve_the_other_occurrence_and_surrounding_text(monkeypatch, scope):
    content = {**empty_sections(), "5": "Первый: кнопка.\nВторой: кнопка.\nХвост."}
    document = {"revision": 1, "sections": content, "messages": [], "proposals": []}
    context = {"scope": scope, "anchor": {"section": "5", "quote": "кнопка", "prefix": "Второй: ", "suffix": ".\nХвост."} if scope == "selection" else None}
    message = {"message_id": "request", "text": "Замени второе вхождение", "context": context}
    ai = SpecificationAI()

    async def ask(actor, system, payload, *, thinking=False):
        assert thinking is (scope == "all")
        return {"required_sections": ["5"], "answer": "Предлагаю", "edits": [{"section": "5", "before": "Второй: кнопка.", "after": "Второй: новая кнопка."}]}

    monkeypatch.setattr(ai, "_ask", ask)
    result = await ai.chat("author", document, message)
    from api.technical_specifications.application import Specifications

    candidate = Specifications._chat_candidate(document, message, result)
    assert candidate == {**content, "5": "Первый: кнопка.\nВторой: новая кнопка.\nХвост."}
    assert document["sections"] == content


@pytest.mark.asyncio
async def test_chat_does_not_ignore_an_undeclared_noop_edit(monkeypatch):
    document = {"revision": 1, "sections": {**empty_sections(), "8": "API"}, "messages": [], "proposals": []}
    ai = SpecificationAI()

    async def ask(*args, **kwargs):
        return {"required_sections": [], "answer": "Ответ", "edits": [{"section": "8", "before": "API", "after": "API"}]}

    monkeypatch.setattr(ai, "_ask", ask)
    with pytest.raises(SpecificationError) as error:
        await ai.chat("author", document, {"text": "Вопрос", "context": {"scope": "all"}})
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["all", "6"])
async def test_correcting_a_proposal_retains_all_earlier_edits_and_checks_their_scope(monkeypatch, scope):
    saved = {**empty_sections(), "6": "Старая логика", "7": "Старая модель", "8": "Прежний API"}
    proposed = {**saved, "6": "Новая логика с ошибкой", "7": "Новая модель", "8": "Новый API"}
    document = {"revision": 2, "sections": saved, "messages": [], "proposals": [{"id": "prior", "source": "chat", "base_revision": 2, "status": "needs_resolution", "sections": proposed}]}
    original = deepcopy(document)
    ai = SpecificationAI()

    async def ask(*args, **kwargs):
        return {"base_proposal_id": "prior", "required_sections": ["6"], "answer": "Предлагаю исправить", "edits": [{"section": "6", "before": "с ошибкой", "after": "без ошибки"}]}

    monkeypatch.setattr(ai, "_ask", ask)
    result = await ai.chat("author", document, {"text": "Исправь предложение", "context": {"scope": scope}})
    if scope == "all":
        assert result["patches"] == {"6": "Новая логика без ошибки", "7": "Новая модель", "8": "Новый API"}
    else:
        assert result["patches"] == {} and "Всё ТЗ" in result["answer"]
    assert document == original


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"base_revision": 1}, {"status": "applied"}, {"source": "eva"}, {"id": "other"}])
async def test_chat_cannot_use_a_stale_applied_foreign_or_eva_proposal_as_its_base(monkeypatch, changed):
    saved = {**empty_sections(), "6": "Логика"}
    proposal = {"id": "prior", "base_revision": 2, "source": "chat", "status": "ready", "sections": saved, **changed}
    document = {"revision": 2, "sections": saved, "messages": [], "proposals": [proposal]}
    ai = SpecificationAI()

    async def ask(*args, **kwargs):
        return {"base_proposal_id": "prior", "required_sections": ["6"], "answer": "Предлагаю", "edits": [{"section": "6", "before": "Логика", "after": "Новая логика"}]}

    monkeypatch.setattr(ai, "_ask", ask)
    with pytest.raises(SpecificationError) as error:
        await ai.chat("author", document, {"text": "Исправь", "context": {"scope": "all"}})
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.asyncio
async def test_merge_and_verifier_receive_explicit_one_sided_changes_and_deletions(monkeypatch):
    base = {**empty_sections(), "4": "ttl=1\nrows=10\n", "9": "keep\nremove\n"}
    local = {**base, "4": "ttl=1\nrows=20\n", "9": "keep\n"}
    remote = {**base, "4": "ttl=3\nrows=10\n"}
    candidate = {**local, "4": "ttl=3\nrows=20\n"}
    expected = {"local": [{"before": "10", "after": "20", "prefix": "ttl=1\nrows=", "suffix": "\n"}], "remote": [{"before": "1", "after": "3", "prefix": "ttl=", "suffix": "\nrows=10\n"}]}
    ai, calls = SpecificationAI(), []

    async def ask(actor, system, payload, *, thinking=False):
        assert actor == "author"
        assert thinking is True
        calls.append(payload)
        if "comparison" in payload:
            assert thinking is True
            rows = {item["section"]: item for item in payload["comparison"]}
            assert rows["4"]["changes"] == expected
            assert rows["9"]["changes"] == {"local": [{"before": "remove\n", "after": "", "prefix": "keep\n", "suffix": ""}], "remote": []}
            return {"valid": True, "issues": []}
        assert payload["changes"]["4"] == expected
        assert payload["changes"]["9"] == {"local": [{"before": "remove\n", "after": "", "prefix": "keep\n", "suffix": ""}], "remote": []}
        assert payload["merged"] == candidate
        return {"edits": []}

    monkeypatch.setattr(ai, "_ask", ask)
    assert await ai.resolve_merge("author", base, local, remote, "remote") == candidate
    assert await ai.verify("author", local, candidate, merge={"base": base, "remote": remote, "choice": "remote"}) == {"valid": True, "issues": []}
    assert len(calls) == 2


def test_change_hints_keep_all_edits_at_the_content_limit(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A large unmatched middle reached the quadratic matcher")

    monkeypatch.setattr(text_merge, "SequenceMatcher", forbidden)
    before = "a\n" * 50_000
    after = "l\n" + "a\n" * 49_998 + "r\n"
    changes = _changes(before, after)
    assert len(changes) == 1
    assert changes[0]["before"] == before.strip()
    assert changes[0]["after"] == after.strip()
    assert changes[0]["suffix"] == "\n"


@pytest.mark.asyncio
async def test_large_repeated_merge_and_verification_preserve_independent_edits(monkeypatch):
    original_matcher = text_merge.SequenceMatcher

    def bounded(junk, left, right, **kwargs):
        assert len(left) * len(right) <= 65_536
        return original_matcher(junk, left, right, **kwargs)

    monkeypatch.setattr(text_merge, "SequenceMatcher", bounded)
    base = {**empty_sections(), "8": "header\n" + "a\n" * 20_000 + "ttl=1\nrows=10\n" + "a\n" * 20_000 + "audit=base\n"}
    local = {**base, "8": base["8"].replace("ttl=1", "ttl=2").replace("rows=10", "rows=20")}
    remote = {**base, "8": base["8"].replace("ttl=1", "ttl=3").replace("audit=base", "audit=remote")}
    expected = {**local, "8": local["8"].replace("ttl=2", "ttl=3").replace("audit=base", "audit=remote")}
    snapshots = deepcopy((base, local, remote))
    ai, calls = SpecificationAI(), []

    async def ask(actor, system, payload, *, thinking=False):
        calls.append(payload)
        if "merged" in payload:
            assert "rows=20" in payload["L"]["8"] and "audit=remote" in payload["R"]["8"]
            assert any("20" in item["after"] for item in payload["changes"]["8"]["local"])
            assert any("audit=remote" in item["after"] for item in payload["changes"]["8"]["remote"])
            return {"edits": [{"section": "8", "before": "ttl=2", "after": "ttl=3"}, {"section": "8", "before": "audit=base", "after": "audit=remote"}]}
        row = next(item for item in payload["comparison"] if item["section"] == "8")
        assert row["result"] == expected["8"]
        assert row["sources"] == {"base": base["8"], "local": local["8"], "remote": remote["8"]}
        return {"valid": True, "issues": []}

    monkeypatch.setattr(ai, "_ask", ask)
    candidate = await ai.resolve_merge("author", base, local, remote, "remote")
    assert candidate == expected
    assert await ai.verify("author", local, candidate, merge={"base": base, "remote": remote, "choice": "remote"}) == {"valid": True, "issues": []}
    assert len(calls) == 2 and (base, local, remote) == snapshots


@pytest.mark.asyncio
async def test_literal_json_edit_is_visible_inside_a_long_unchanged_paragraph(monkeypatch):
    paragraph = "Неизменённое пояснение. " * 100 + 'Тело: {"status":"created"}. ' + "Другие требования. " * 100
    base = {**empty_sections(), "8": paragraph}
    local = {**base, "8": paragraph.replace('"created"', '"accepted"')}
    ai = SpecificationAI()

    async def ask(actor, system, payload, *, thinking=False):
        assert thinking is True
        changes = payload["changes"]["8"]
        assert changes["remote"] == []
        assert len(changes["local"]) == 1
        change = changes["local"][0]
        assert (change["before"], change["after"]) == ("created", "accepted")
        assert '"status":"' in change["prefix"]
        assert len(change["prefix"] + change["suffix"]) < 300
        assert payload["merged"] == local
        return {"edits": []}

    monkeypatch.setattr(ai, "_ask", ask)
    assert await ai.resolve_merge("author", base, local, base, "remote") == local


@pytest.mark.asyncio
async def test_merge_applies_only_the_models_literal_edits_to_the_complete_seed(monkeypatch):
    base = {**empty_sections(), "4": "limit=20\nsize=10\n", "8": "X-Event-Id, X-Sequence, X-Signature", "9": "audit"}
    local = {**base, "4": "limit=35\nsize=10\n"}
    remote = {**base, "4": "limit=50\nsize=40\n", "9": "audit\nlatency"}
    ai, calls = SpecificationAI(), []

    async def ask(actor, system, payload, *, thinking=False):
        assert thinking is True
        calls.append(payload)
        assert payload["merged"] == {**local, "9": remote["9"]}
        return {"edits": [{"section": "4", "before": "limit=35", "after": "limit=50"}, {"section": "4", "before": "size=10", "after": "size=40"}]}

    monkeypatch.setattr(ai, "_ask", ask)
    assert await ai.resolve_merge("author", base, local, remote, "remote") == remote
    assert len(calls) == 1


def escaped_quote(value):
    return json.dumps(value, ensure_ascii=False)[1:-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("literal_backslashes", [False, True])
async def test_merge_preserves_json_and_literal_backslashes_without_decoding_again(monkeypatch, literal_backslashes):
    def fragment(value):
        result = f'"ttl_seconds":{value},"label":"download"'
        return escaped_quote(result) if literal_backslashes else result

    base, local, remote = ({**empty_sections(), "8": "Body: {" + fragment(value) + "}"} for value in (300, 450, 600))
    before, after = fragment(450), fragment(600)
    edits = [{"section": "8", "before": before, "after": after}]
    original = deepcopy((base, local, remote, edits))
    ai = SpecificationAI()

    async def ask(*_args, **_kwargs):
        return {"edits": edits}

    monkeypatch.setattr(ai, "_ask", ask)
    assert await ai.resolve_merge("author", base, local, remote, "remote") == remote
    assert (base, local, remote, edits) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing", "ambiguous", "extra_layer", "two_layers", "malformed", "mixed_after", "overlap"])
async def test_merge_rejects_invalid_literal_quotes_without_repair(monkeypatch, defect):
    def fragment(value):
        return f'"ttl_seconds":{value},"label":"download"'

    base, local, remote = ({**empty_sections(), "8": "Body: {" + fragment(value) + "}"} for value in (300, 450, 600))
    edits = [{"section": "8", "before": fragment(450), "after": fragment(600)}]
    if defect == "missing":
        edits[0]["before"] = escaped_quote('"missing":450')
    elif defect == "ambiguous":
        for document in (base, local, remote):
            document["8"] += document["8"]
    elif defect in {"extra_layer", "two_layers", "mixed_after"}:
        for _ in range(2 if defect == "two_layers" else 1):
            edits[0].update(before=escaped_quote(edits[0]["before"]), after=escaped_quote(edits[0]["after"]))
        if defect == "mixed_after":
            edits[0]["after"] = fragment(600)
    elif defect == "malformed":
        edits[0]["before"] = r"\q"
    elif defect == "overlap":
        edits.append({"section": "8", "before": '"ttl_seconds":450', "after": '"ttl_seconds":600'})
    original = deepcopy((base, local, remote, edits))
    ai = SpecificationAI()

    async def ask(*_args, **_kwargs):
        return {"edits": edits}

    monkeypatch.setattr(ai, "_ask", ask)
    with pytest.raises(SpecificationError) as error:
        await ai.resolve_merge("author", base, local, remote, "remote")
    assert error.value.code == "MODEL_INVALID_OUTPUT"
    assert (base, local, remote, edits) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("neighbor", [r"LOG_PATH=C:\temp\reports", r"PATTERN=\btoken\b", r"EXAMPLE=\n\t\r"])
@pytest.mark.parametrize("extra_before_layer", [False, True])
async def test_merge_never_decodes_independent_neighbor_text(monkeypatch, neighbor, extra_before_layer):
    base, local, remote = ({**empty_sections(), "8": f"TTL={value}; {neighbor}"} for value in (300, 450, 600))
    edits = [{"section": "8", "before": escaped_quote(local["8"]) if extra_before_layer else local["8"], "after": remote["8"]}]
    original = deepcopy((base, local, remote, edits))
    ai = SpecificationAI()

    async def ask(*_args, **_kwargs):
        return {"edits": edits}

    monkeypatch.setattr(ai, "_ask", ask)
    if extra_before_layer:
        with pytest.raises(SpecificationError) as error:
            await ai.resolve_merge("author", base, local, remote, "remote")
        assert error.value.code == "MODEL_INVALID_OUTPUT"
    else:
        assert await ai.resolve_merge("author", base, local, remote, "remote") == remote
    assert (base, local, remote, edits) == original
