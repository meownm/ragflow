"""Repeated text cannot turn merge preparation into unbounded line matching."""

import pytest

from api.document_exchange import merge


@pytest.fixture
def bounded_matcher(monkeypatch):
    original = merge.SequenceMatcher

    def bounded(junk, left, right, **kwargs):
        assert len(left) * len(right) <= 65_536, "Quadratic matcher received an unbounded middle"
        return original(junk, left, right, **kwargs)

    monkeypatch.setattr(merge, "SequenceMatcher", bounded)


@pytest.mark.parametrize("position", [0, 25_000, 49_999])
def test_single_change_in_maximum_repeated_text_is_exact_and_bounded(bounded_matcher, position):
    before = "a\n" * 50_000
    lines = before.splitlines(keepends=True)
    lines[position] = "b\n"
    after = "".join(lines)
    edits = merge.line_changes(before, after)
    assert edits == [(position, position + 1, ["b\n"])]
    for start, end, replacement in reversed(edits):
        lines = before.splitlines(keepends=True)
        lines[start:end] = replacement
        assert "".join(lines) == after


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_large_unmatched_middle_retains_every_change_and_byte(bounded_matcher, newline):
    before = ("a" + newline) * 20_000
    after = "left" + newline + ("a" + newline) * 19_998 + "right" + newline
    lines = before.splitlines(keepends=True)
    edits = merge.line_changes(before, after)
    assert len(edits) == 1
    for start, end, replacement in reversed(edits):
        lines[start:end] = replacement
    assert "".join(lines) == after


def test_large_repeated_document_merges_independent_sides_without_loss(bounded_matcher):
    base = "header\n" + "a\n" * 20_000 + "left=base\n" + "a\n" * 20_000 + "right=base\n"
    local, remote = base.replace("left=base", "left=local"), base.replace("right=base", "right=remote")
    expected = base.replace("left=base", "left=local").replace("right=base", "right=remote")
    assert merge.merge_text(base, local, remote) == {"text": expected, "conflict": False}
    assert merge.merge_text(base, remote, local) == {"text": expected, "conflict": False}


def test_coarse_overlapping_ranges_keep_all_three_complete_versions(bounded_matcher):
    base = "header\n" + "a\n" * 20_000 + "middle=base\n" + "a\n" * 20_000 + "tail=base\n"
    local = base.replace("header", "local header").replace("tail=base", "tail=local")
    remote = base.replace("middle=base", "middle=remote")
    assert merge.merge_text(base, local, remote) == {"conflict": True, "base": base, "local": local, "remote": remote}


@pytest.mark.parametrize("blank", ["\n", "\r\n", " \n", "\t\r\n"])
@pytest.mark.parametrize("anchors", [False, True])
@pytest.mark.parametrize("swap", [False, True])
def test_ambiguous_blank_insertion_keeps_versions_instead_of_adding_an_occurrence(bounded_matcher, blank, anchors, swap):
    base, local, remote = blank, blank * 2, "B\n" + blank * 2
    if anchors:
        base, local, remote = ("header\n" + value + "footer\n" for value in (base, local, remote))
    if swap:
        local, remote = remote, local
    assert merge.merge_text(base, local, remote) == {"conflict": True, "base": base, "local": local, "remote": remote}


@pytest.mark.parametrize("blank", ["\n", "\r\n", " \n", "\t\r\n"])
@pytest.mark.parametrize("swap", [False, True])
def test_ambiguous_blank_deletion_does_not_lose_the_other_sides_replacement(blank, swap):
    base = "header\n" + blank * 2 + "footer\n"
    local = "header\n" + blank + "footer\n"
    remote = "header\nREMOTE\n" + blank + "footer\n"
    if swap:
        local, remote = remote, local
    assert merge.merge_text(base, local, remote) == {"conflict": True, "base": base, "local": local, "remote": remote}


@pytest.mark.parametrize("blank", ["\n", "\r\n", " \n", "\t\r\n"])
@pytest.mark.parametrize("swap", [False, True])
def test_independent_edits_preserve_blank_lines_exactly(blank, swap):
    base = "left=base\n" + blank * 2 + "right=base\n"
    local, remote = base.replace("left=base", "left=local"), base.replace("right=base", "right=remote")
    if swap:
        local, remote = remote, local
    assert merge.merge_text(base, local, remote) == {"conflict": False, "text": base.replace("left=base", "left=local").replace("right=base", "right=remote")}


def test_whitespace_fast_paths_and_unambiguous_deletion_remain_exact():
    base, changed = "\n", "\n\n"
    assert merge.merge_text(base, changed, changed) == {"conflict": False, "text": changed}
    assert merge.merge_text(base, base, changed) == {"conflict": False, "text": changed}
    assert merge.merge_text(base, changed, base) == {"conflict": False, "text": changed}
    base = "left=base\n\nright=base\n"
    assert merge.merge_text(base, base.replace("\n\n", "\n"), base.replace("right=base", "right=remote")) == {"conflict": False, "text": "left=base\nright=remote\n"}


@pytest.mark.parametrize("anchors", [False, True])
@pytest.mark.parametrize("swap", [False, True])
def test_ambiguous_common_insertion_never_creates_an_extra_duplicate(bounded_matcher, anchors, swap):
    base, local, remote = "A\n", "A\nA\n", "B\nA\nA\n"
    if anchors:
        base, local, remote = ("header\n" + text + "footer\n" for text in (base, local, remote))
    if swap:
        local, remote = remote, local
    assert merge.merge_text(base, local, remote) == {"conflict": True, "base": base, "local": local, "remote": remote}
