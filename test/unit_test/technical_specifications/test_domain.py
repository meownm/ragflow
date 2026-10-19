from copy import deepcopy

import pytest

from api.document_exchange.merge import merge_text
from api.technical_specifications.domain import SpecificationError, assert_context, context, draft, empty_sections, markdown, merge_sections, parse_markdown, sections


def selected(quote="результат", prefix="", suffix=""):
    return {"document_id": "doc", "scope": "selection", "revision": 2, "anchor": {"section": "5", "quote": quote, "prefix": prefix, "suffix": suffix}}


def test_format_roundtrip_preserves_all_nine_sections_and_code():
    content = empty_sections()
    content["5"] = "Сценарий\n\n```markdown\n## 1. Не раздел ТЗ\n```\nТекст"
    content["8"] = "| Поле | Тип |\n| --- | --- |\n| limit | int |"
    assert parse_markdown(markdown(content)) == content
    assert len(sections(content)) == 9


@pytest.mark.parametrize("edit", [lambda s: s.pop("9"), lambda s: s.update({"10": "лишний"}), lambda s: s.update({"2": "## Другой верхний раздел"})])
def test_rejects_outline_changes(edit):
    content = empty_sections()
    edit(content)
    with pytest.raises(SpecificationError, match="раздел"):
        sections(content)


def test_import_does_not_silently_discard_content_or_renumber_sections():
    value = markdown(empty_sections())
    for invalid in ("Важное основание\n" + value, value.replace("## 5.", "## 6."), value.replace("Назначение и цель", "Другой раздел")):
        with pytest.raises(SpecificationError):
            parse_markdown(invalid)


def test_independent_changes_in_same_section_both_survive():
    base = "Количество: 20\nРазмер: 10 МБ\n"
    assert merge_text(base, base.replace("10 МБ", "40 МБ"), base.replace("20", "50")) == {"text": "Количество: 50\nРазмер: 40 МБ\n", "conflict": False}


@pytest.mark.parametrize("local,remote", [("35\n", "50\n"), ("insert\n20\n", "other\n20\n"), ("", "updated\n")])
def test_conflicts_retain_all_three_texts(local, remote):
    result = merge_text("20\n", local, remote)
    assert result == {"conflict": True, "base": "20\n", "local": local, "remote": remote}


def test_reordering_does_not_merge_blocks_by_position():
    base = "A\nB\nC\n"
    result = merge_text(base, "C\nB\nA\n", "A\nchanged B\nC\n")
    assert result["conflict"] or "changed B" in result["text"]
    if not result["conflict"]:
        assert "B\n" not in result["text"].replace("changed B\n", "")


@pytest.mark.parametrize(
    "base,local,remote",
    [
        ("A\nsame\nA\nsame\n", "A\nL\nA\nsame\n", "A\nsame\n"),
        ("same\nsame\n", "L\nsame\n", "same\n"),
    ],
)
def test_deleting_an_ambiguous_occurrence_never_loses_the_deletion(base, local, remote):
    assert merge_text(base, local, remote) == {"conflict": True, "base": base, "local": local, "remote": remote}


def test_merge_separates_conflicting_and_independent_sections():
    base = empty_sections()
    local, remote = deepcopy(base), deepcopy(base)
    local["4"], remote["4"] = "Лимит 35", "Лимит 50"
    remote["8"] = "Контракт 50"
    result = merge_sections(base, local, remote)
    assert result["sections"]["8"] == "Контракт 50"
    assert [item["section"] for item in result["conflicts"]] == ["4"]


def test_draft_retains_full_context_and_long_text():
    value = {"message_id": "message", "text": "а" * 10_000, "context": selected(prefix="До ", suffix=" после")}
    restored = draft(value, "doc")
    assert restored == value
    restored["context"]["anchor"]["quote"] = "другое"
    assert value["context"]["anchor"]["quote"] == "результат"


def test_anchor_follows_shift_but_never_expands_when_deleted_or_ambiguous():
    content = empty_sections()
    content["5"] = "Добавленный текст. До результат после."
    ctx = context(selected(prefix="До ", suffix=" после"), "doc")
    assert_context(ctx, content)
    for text in ("Цитата удалена", "До результат после. До результат после."):
        content["5"] = text
        with pytest.raises(SpecificationError) as error:
            assert_context(ctx, content)
        assert error.value.code == "STALE_CONTEXT"
        assert ctx["scope"] == "selection"


@pytest.mark.parametrize("change", [{"scope": []}, {"document_id": "other"}, {"revision": True}, {"scope": "all"}])
def test_context_rejects_wrong_document_and_malformed_scope(change):
    value = {**selected(), **change}
    with pytest.raises(SpecificationError):
        context(value, "doc")
