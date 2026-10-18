"""Block edits preserve neighboring content, identity and snapshot fencing."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from business_documents.domain.block_diff import block_changes, with_block_ids
from business_documents.domain.content import apply_change_plan, bind_change_plan_snapshot, validate_contract
from business_documents.domain.content import render_section_text
from business_documents.domain.content_quality import meaningful_text_block_count, semantic_duplicate_section_pairs
from business_documents.domain.errors import RuleViolation


CONTRACTS = Path(__file__).resolve().parents[4] / "agent/business_requirements/contracts"
SCHEMA = json.loads((CONTRACTS / "document_draft.v1.schema.json").read_text())
PLAN_SCHEMA = json.loads((CONTRACTS / "change_plan.v1.schema.json").read_text())
TEMPLATE = {"template_version": "1", "sections": [{"id": "1", "title": "Purpose", "required": True, "allowed_blocks": ["paragraph", "list", "table"]}]}


def document():
    return with_block_ids(
        {
            "schema_version": "1",
            "document_type": "business_requirements",
            "template_version": "1",
            "sections": [
                {
                    "id": "1",
                    "title": "Purpose",
                    "blocks": [
                        {"type": "paragraph", "text": "Same"},
                        {"type": "paragraph", "text": "Same"},
                        {"type": "list", "items": ["Keep me"]},
                    ],
                }
            ],
        }
    )


def plan(base, *operations):
    raw = {
        "schema_version": "1",
        "acknowledged_no_change_event_ids": [],
        "operations": [{"operation_id": f"op-{index}", "section_id": "1", "source_event_ids": ["confirmed"], **operation} for index, operation in enumerate(operations)],
    }
    bound = bind_change_plan_snapshot(base, raw, base_revision_id="revision-1", source_state_version=3)
    validate_contract("change_plan", bound, PLAN_SCHEMA)
    return bound


def apply(base, *operations):
    return apply_change_plan(base, plan(base, *operations), TEMPLATE, SCHEMA)


def test_legacy_ids_are_deterministic_distinct_and_do_not_mutate_saved_document():
    raw = document()
    for block in raw["sections"][0]["blocks"]:
        block.pop("id")
    identified = with_block_ids(raw)
    assert identified == with_block_ids(raw) == with_block_ids(identified)
    assert len({block["id"] for block in identified["sections"][0]["blocks"]}) == 3
    assert all("id" not in block for block in raw["sections"][0]["blocks"])


def test_replace_insert_delete_preserves_untouched_blocks_and_identity_across_revisions():
    base = document()
    original = deepcopy(base)
    first, second, third = base["sections"][0]["blocks"]
    result = apply(
        base,
        {"type": "REPLACE_BLOCK", "block_id": first["id"], "block": {"type": "paragraph", "text": "Changed"}},
        {"type": "INSERT_BLOCK", "after_block_id": first["id"], "block": {"type": "table", "headers": ["Metric"], "rows": [[10]]}},
        {"type": "DELETE_BLOCK", "block_id": second["id"]},
    )
    a, inserted, c = result["sections"][0]["blocks"]
    assert a == {**first, "text": "Changed"}
    assert c == third
    assert inserted["id"] not in {first["id"], second["id"], third["id"]}
    changed_again = apply(result, {"type": "REPLACE_BLOCK", "block_id": inserted["id"], "block": {"type": "paragraph", "text": "Table replaced"}})
    assert changed_again["sections"][0]["blocks"][1]["id"] == inserted["id"]
    assert base == original
    assert result == apply_change_plan(
        base,
        plan(
            base,
            {"type": "REPLACE_BLOCK", "block_id": first["id"], "block": {"type": "paragraph", "text": "Changed"}},
            {"type": "INSERT_BLOCK", "after_block_id": first["id"], "block": {"type": "table", "headers": ["Metric"], "rows": [[10]]}},
            {"type": "DELETE_BLOCK", "block_id": second["id"]},
        ),
        TEMPLATE,
        SCHEMA,
    )


def test_multiple_insertions_use_original_anchor_order_even_if_anchor_is_deleted():
    base = document()
    anchor = base["sections"][0]["blocks"][0]["id"]
    result = apply(
        base,
        {"type": "INSERT_BLOCK", "after_block_id": None, "block": {"type": "paragraph", "text": "Start"}},
        {"type": "DELETE_BLOCK", "block_id": anchor},
        *[{"type": "INSERT_BLOCK", "after_block_id": anchor, "block": {"type": "paragraph", "text": text}} for text in ("A", "B")],
    )
    assert [block.get("text") for block in result["sections"][0]["blocks"]] == ["Start", "A", "B", "Same", None]


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "DELETE_BLOCK", "block_id": "unknown"},
        {"type": "INSERT_BLOCK", "after_block_id": "unknown", "block": {"type": "paragraph", "text": "New"}},
        {"type": "DELETE_BLOCK", "section_id": "2", "block_id": "unknown"},
    ],
)
def test_unknown_block_section_and_anchor_fail_without_changing_base(operation):
    base = document()
    original = deepcopy(base)
    with pytest.raises(RuleViolation) as caught:
        apply(base, operation)
    assert caught.value.code in {"CHANGE_BLOCK_NOT_FOUND", "CHANGE_SECTION_NOT_FOUND"}
    assert base == original


def test_duplicate_target_and_operation_ids_are_rejected():
    base = document()
    operation = {"type": "DELETE_BLOCK", "block_id": base["sections"][0]["blocks"][0]["id"]}
    with pytest.raises(RuleViolation, match="only be edited once"):
        apply(base, operation, operation)
    bound = plan(base, operation, {**operation, "block_id": base["sections"][0]["blocks"][1]["id"]})
    bound["operations"][1]["operation_id"] = bound["operations"][0]["operation_id"]
    with pytest.raises(RuleViolation) as caught:
        apply_change_plan(base, bound, TEMPLATE, SCHEMA)
    assert caught.value.code == "DUPLICATE_CHANGE_OPERATION"


def test_stale_content_is_rejected_instead_of_rebasing_the_patch():
    base = document()
    bound = plan(base, {"type": "DELETE_BLOCK", "block_id": base["sections"][0]["blocks"][0]["id"]})
    modified = deepcopy(base)
    modified["sections"][0]["blocks"][1]["text"] = "Concurrent change"
    with pytest.raises(RuleViolation) as caught:
        apply_change_plan(modified, bound, TEMPLATE, SCHEMA)
    assert caught.value.code == "SECTION_HASH_CONFLICT"


def test_evidence_from_multiple_edits_does_not_invalidate_the_original_hash():
    base = document()
    ref = "ragflow://dataset/a/document/b/chunk/c"
    result = apply(
        base,
        *[
            {"type": "REPLACE_BLOCK", "block_id": block["id"], "block": {"type": "paragraph", "text": f"Changed {index}"}, "evidence_refs": [ref]}
            for index, block in enumerate(base["sections"][0]["blocks"][:2])
        ],
    )
    assert result["sections"][0]["evidence_refs"] == [ref]


def test_add_subsection_and_preview_include_only_changed_blocks():
    base = document()
    result = apply(base, {"type": "ADD_SECTION", "section_id": "1.1", "title": "Details", "content": {"blocks": [{"type": "paragraph", "text": "Detail"}]}})
    assert result["sections"][0] == base["sections"][0]
    assert result["sections"][1]["blocks"][0]["id"]
    changed = apply(base, {"type": "REPLACE_BLOCK", "block_id": base["sections"][0]["blocks"][1]["id"], "block": {"type": "paragraph", "text": "Changed"}})
    pairs = block_changes(base["sections"][0], changed["sections"][0])
    assert len(pairs) == 1 and pairs[0]["kind"] == "replace"
    assert pairs[0]["block_id"] == base["sections"][0]["blocks"][1]["id"]


@pytest.mark.parametrize("identifier", ["", "x" * 81, 42, None])
def test_invalid_identity_is_rejected(identifier):
    base = document()
    base["sections"][0]["blocks"][0]["id"] = identifier
    with pytest.raises(RuleViolation) as caught:
        with_block_ids(base)
    assert caught.value.code == "INVALID_BLOCK_ID"


def test_identity_does_not_change_rendering_or_semantic_quality_signals():
    legacy = {"id": "1", "blocks": [{"type": "bpmn", "source": "<definitions/>"}]}
    assert render_section_text(legacy) == render_section_text(with_block_ids({"sections": [legacy]})["sections"][0])
    sections = [{"id": str(index), "blocks": [{"type": "paragraph", "text": "Пользователь выбирает запись клиента и подтверждает бронирование слота."}]} for index in (1, 2)]
    identified = with_block_ids({"sections": sections})["sections"]
    assert meaningful_text_block_count(sections) == meaningful_text_block_count(identified)
    assert semantic_duplicate_section_pairs(sections) == semantic_duplicate_section_pairs(identified)


def test_forged_replacement_identity_and_duplicate_document_ids_are_rejected():
    base = document()
    with pytest.raises(RuleViolation) as caught:
        plan(base, {"type": "REPLACE_BLOCK", "block_id": base["sections"][0]["blocks"][0]["id"], "block": {"id": "forged", "type": "paragraph", "text": "New"}})
    assert caught.value.code == "BLOCK_ID_CONFLICT"
    base["sections"][0]["blocks"][1]["id"] = base["sections"][0]["blocks"][0]["id"]
    with pytest.raises(RuleViolation) as caught:
        with_block_ids(base)
    assert caught.value.code == "INVALID_BLOCK_ID"


@pytest.mark.parametrize("before,after", [(True, 1), (False, 0)])
def test_preview_distinguishes_boolean_and_numeric_cells_with_the_same_block_id(before, after):
    old = {"id": "1", "blocks": [{"id": "table", "type": "table", "headers": ["Value"], "rows": [[before]]}]}
    new = deepcopy(old)
    new["blocks"][0]["rows"] = [[after]]
    pairs = block_changes(old, new)
    assert len(pairs) == 1 and pairs[0]["kind"] == "replace"


@pytest.mark.parametrize("kind", ["REPLACE_SECTION_CONTENT", "REPLACE_BLOCK"])
def test_snapshot_binding_hashes_only_targets_and_preserves_legacy_hashes(monkeypatch, kind):
    from business_documents.domain import content

    base = document()
    for block in base["sections"][0]["blocks"]:
        block.pop("id")
    base["sections"].append({"id": "2", "title": "Untouched", "blocks": [{"type": "paragraph", "text": "Keep this content"}]})
    original = deepcopy(base)
    identified = with_block_ids(base)
    target = (identified if kind == "REPLACE_BLOCK" else base)["sections"][0]
    hash_section = content.section_hash
    expected_hash = hash_section(target)
    calls = []

    def counted_hash(section):
        calls.append(section["id"])
        return hash_section(section)

    monkeypatch.setattr(content, "section_hash", counted_hash)
    raw = {"operations": []}
    for index, block in enumerate(identified["sections"][0]["blocks"][:2]):
        replacement = {"type": "paragraph", "text": "Changed"}
        fields = {"block_id": block["id"], "block": replacement} if kind == "REPLACE_BLOCK" else {"content": {"blocks": [replacement]}}
        raw["operations"].append({"operation_id": f"op-{index}", "type": kind, "section_id": "1", "source_event_ids": ["confirmed"], **fields})
    raw_before = deepcopy(raw)
    bound = bind_change_plan_snapshot(base, raw, base_revision_id="revision-1", source_state_version=3)
    assert calls == ["1"]
    assert [operation["expected_section_hash"] for operation in bound["operations"]] == [expected_hash, expected_hash]
    assert bound["base_revision_id"] == "revision-1" and bound["source_state_version"] == 3
    assert base == original and raw == raw_before


def test_block_application_hashes_only_targets_and_rechecks_each_snapshot(monkeypatch):
    from business_documents.domain import block_diff

    base = document()
    base["sections"].append({"id": "2", "title": "Untouched", "blocks": [{"id": "untouched", "type": "paragraph", "text": "Keep this content"}]})
    original = deepcopy(base)
    ref = "ragflow://dataset/a/document/b/chunk/c"
    bound = plan(
        base,
        *[
            {"type": "REPLACE_BLOCK", "block_id": block["id"], "block": {"type": "paragraph", "text": f"Changed {index}"}, "evidence_refs": [ref]}
            for index, block in enumerate(base["sections"][0]["blocks"][:2])
        ],
    )
    calls = []
    hash_value = block_diff.stable_hash

    def counted_hash(value):
        if isinstance(value, dict) and "blocks" in value:
            calls.append(value["id"])
        return hash_value(value)

    monkeypatch.setattr(block_diff, "stable_hash", counted_hash)
    changed = block_diff.apply_block_operations(base, bound["operations"])
    assert calls == ["1"]
    assert changed["sections"][1] == original["sections"][1]
    assert [block.get("text") for block in changed["sections"][0]["blocks"]] == ["Changed 0", "Changed 1", None]
    assert changed["sections"][0]["evidence_refs"] == [ref]
    assert base == original
    concurrent = deepcopy(base)
    concurrent["sections"][0]["blocks"][0]["text"] = "Concurrent edit"
    concurrent_before = deepcopy(concurrent)
    with pytest.raises(RuleViolation) as caught:
        block_diff.apply_block_operations(concurrent, bound["operations"])
    assert caught.value.code == "SECTION_HASH_CONFLICT"
    assert calls == ["1", "1"]
    assert concurrent == concurrent_before
