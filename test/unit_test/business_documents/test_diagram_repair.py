from copy import deepcopy
import json

import pytest

from business_documents.application.diagram_repair import DiagramCheck, DiagramRepairResult, repair_diagram, repair_generated_diagrams
from business_documents.domain.diagram_repair import apply_syntax_edits, syntax_context
from business_documents.domain.errors import RuleViolation

SOURCE = """@startuml
start
partition "Проверка" {
if (Решение?) then (Принять)
:Принять заявку;
else (Исправить)
:Исправить заявку;
else (Отклонить)
:Отклонить заявку;
endif
}
stop
@enduml"""
PATCH = {
    "edits": [
        {"line": 4, "replacement": "switch (Решение?)\ncase (Принять)"},
        {"line": 6, "replacement": "case (Исправить)"},
        {"line": 8, "replacement": "case (Отклонить)"},
        {"line": 10, "replacement": "endswitch"},
    ]
}


def test_multiway_patch_preserves_complete_tree_and_protected_lines():
    fixed = apply_syntax_edits(SOURCE, PATCH, "activity")
    assert syntax_context(SOURCE, "activity")["structure"] == syntax_context(fixed, "activity")["structure"]
    assert ":Принять заявку;\n" in fixed and ":Отклонить заявку;\n" in fixed
    assert syntax_context(fixed, "activity")["multiway_groups"] == []


@pytest.mark.parametrize("damage", ["swap", "change_role", "change_action", "drop", "duplicate", "foreign", "empty", "whole_source"])
def test_repair_cannot_change_business_structure(damage):
    patch = deepcopy(PATCH)
    if damage == "swap":
        patch["edits"][1]["replacement"] = "case (Отклонить)"
        patch["edits"][2]["replacement"] = "case (Исправить)"
    elif damage == "change_role":
        patch["edits"].append({"line": 3, "replacement": 'partition "Другой исполнитель" {'})
    elif damage == "change_action":
        patch["edits"].append({"line": 5, "replacement": ":Удалить заявку;"})
    elif damage == "drop":
        patch["edits"][2]["replacement"] = ""
    elif damage == "duplicate":
        patch["edits"].append(patch["edits"][0])
    elif damage == "foreign":
        patch["edits"][0]["line"] = 999
    elif damage == "empty":
        patch["edits"] = []
    else:
        patch = {"source": SOURCE}
    with pytest.raises(RuleViolation):
        apply_syntax_edits(SOURCE, patch, "activity")


def test_nested_if_and_early_terminal_must_not_move_to_another_branch():
    original = SOURCE.replace(":Исправить заявку;", "if (Можно восстановить?) then (да)\n:Восстановить;\nelse (нет)\n:Данные утрачены;\nstop\nendif")
    group = syntax_context(original, "activity")["multiway_groups"][0]
    assert group["labels"] == ["Принять", "Исправить", "Отклонить"]
    patch = {
        "edits": [
            {"line": group["opening_line"], "replacement": "switch (Решение?)\ncase (Принять)"},
            *[{"line": n, "replacement": "case (" + label + ")"} for n, label in zip(group["branch_lines"], group["labels"][1:])],
            {"line": group["closing_line"], "replacement": "endswitch"},
        ]
    }
    fixed = apply_syntax_edits(original, patch, "activity")
    assert "else (нет)\n:Данные утрачены;\nstop\nendif" in fixed


def test_declaration_quote_repair_preserves_all_component_links():
    source = '@startuml\nactor "Клиент" as Client\ncomponent "Портал as Portal\nClient --> Portal : заявка\n@enduml'
    fixed = apply_syntax_edits(source, {"edits": [{"line": 3, "replacement": 'component "Портал" as Portal'}]}, "component")
    assert "Client --> Portal : заявка" in fixed
    with pytest.raises(RuleViolation):
        apply_syntax_edits(source, {"edits": [{"line": 3, "replacement": 'component "Иная система" as Portal'}]}, "component")


@pytest.mark.parametrize("status", ["valid", "unavailable"])
def test_non_syntax_does_not_call_model(status):
    result = repair_diagram(SOURCE, "activity", "scenario", check=lambda _: DiagramCheck(status), generate=lambda _: pytest.fail("model called"), state={}, checkpoint=lambda: None)
    assert result.source == SOURCE
    assert result.status == ("unchanged" if status == "valid" else "unavailable")


def test_real_success_is_checkpointed_and_replayed_without_another_model_call():
    state, saves, inputs = {}, [], []

    def check(source):
        return DiagramCheck("valid" if "switch" in source else "syntax_error", "Cannot find if", 8)

    def generate(payload):
        inputs.append(payload)
        return json.dumps(PATCH)

    for _ in range(2):
        result = repair_diagram(SOURCE, "activity", "text", check=check, generate=generate, state=state, checkpoint=lambda: saves.append(deepcopy(state)))
        assert result.status == "repaired" and "switch" in result.source
    assert len(inputs) == 1 and saves[0]["attempts"][0]["status"] == "reserved"
    assert inputs[0]["source_lines"][3] == {"line": 4, "code": "if (Решение?) then (Принять)"}
    assert inputs[0]["scenario_text"] == "text" and inputs[0]["original_source"] == SOURCE


def test_unsafe_first_attempt_then_second_receives_feedback_and_is_repaired():
    inputs, state = [], {}

    def generate(payload):
        inputs.append(deepcopy(payload))
        return {"edits": [{"line": 5, "replacement": ":Удалить заявку;"}]} if len(inputs) == 1 else PATCH

    result = repair_diagram(SOURCE, "activity", "text", check=lambda s: DiagramCheck("valid" if "switch" in s else "syntax_error"), generate=generate, state=state, checkpoint=lambda: None)
    assert result.status == "repaired" and len(inputs) == 2
    assert inputs[1]["previous_attempt"]["status"] == "unsafe"
    assert inputs[1]["original_source"] == SOURCE and inputs[1]["scenario_text"] == "text"


def test_two_failed_calls_are_not_reset_on_reentry():
    state, calls = {}, []

    def generate(_):
        calls.append(1)
        return "invalid json"

    for _ in range(3):
        result = repair_diagram(SOURCE, "activity", "text", check=lambda _: DiagramCheck("syntax_error"), generate=generate, state=state, checkpoint=lambda: None)
        assert result.status == "exhausted" and result.source == SOURCE
    assert len(calls) == 2


def test_uncertain_dispatch_cannot_be_resent_after_crash():
    state = {"attempts": [{"status": "reserved"}]}
    result = repair_diagram(SOURCE, "activity", "text", check=lambda _: DiagramCheck("syntax_error"), generate=lambda _: pytest.fail("resent"), state=state, checkpoint=lambda: None)
    assert result.status == "uncertain" and result.source == SOURCE


def test_ambiguous_unbalanced_structure_is_preserved_without_model_guess():
    original = SOURCE.replace('partition "Проверка" {', 'partition "Проверка" {').replace("\n}\n", "\n")
    result = repair_diagram(original, "activity", "text", check=lambda _: DiagramCheck("syntax_error"), generate=lambda _: pytest.fail("guess"), state={}, checkpoint=lambda: None)
    assert result.status == "unverifiable" and result.source == original


def test_cancel_or_lease_loss_propagates_with_reserved_attempt():
    state = {}

    def generate(_):
        raise RuntimeError("lease lost")

    with pytest.raises(RuntimeError, match="lease lost"):
        repair_diagram(SOURCE, "activity", "text", check=lambda _: DiagramCheck("syntax_error"), generate=generate, state=state, checkpoint=lambda: None)
    assert state["attempts"][0]["status"] == "reserved"


def test_complete_json_fence_is_decoded_without_editing_its_payload():
    raw = "```json\n" + json.dumps(PATCH) + "\n```"
    state = {}
    result = repair_diagram(SOURCE, "activity", "text", check=lambda s: DiagramCheck("valid" if "switch" in s else "syntax_error"), generate=lambda _: raw, state=state, checkpoint=lambda: None)
    assert result.status == "repaired" and state["attempts"][0]["response"] == raw


def test_prose_around_json_is_not_silently_discarded():
    raw = "I changed the process.\n```json\n" + json.dumps(PATCH) + "\n```"
    result = repair_diagram(SOURCE, "activity", "text", check=lambda _: DiagramCheck("syntax_error"), generate=lambda _: raw, state={}, checkpoint=lambda: None)
    assert result.status == "exhausted" and result.source == SOURCE


def test_renderer_outage_does_not_revert_a_previously_verified_patch():
    state = {}
    fixed = repair_diagram(SOURCE, "activity", "text", check=lambda s: DiagramCheck("valid" if "switch" in s else "syntax_error"), generate=lambda _: PATCH, state=state, checkpoint=lambda: None)
    resumed = repair_diagram(SOURCE, "activity", "text", check=lambda _: DiagramCheck("unavailable"), generate=lambda _: pytest.fail("resent"), state=state, checkpoint=lambda: None)
    assert resumed.source == fixed.source and resumed.status == "unavailable" and not resumed.requires_review


def test_only_changed_plan_diagram_is_repaired_with_current_scenario_text():
    old = {"id": "old", "type": "plantuml", "source": SOURCE}
    new = {"id": "new", "type": "plantuml", "source": SOURCE}
    text = {"type": "paragraph", "text": "Подтверждённый сценарий новой редакции"}
    section = {"id": "4.3", "blocks": [text, old, new]}
    base = {"sections": [{"id": "4.3", "blocks": [old]}]}
    output = {"change_plan": {"operations": [{"operation_id": "op1", "section_id": "4.3", "content": {"blocks": [text, old, new]}}]}}
    untouched = deepcopy(output)
    calls = []
    fixed = apply_syntax_edits(SOURCE, PATCH, "activity")

    def repair(source, kind, context, position):
        calls.append((source, kind, context, position))
        return DiagramRepairResult(fixed, "repaired")

    result = repair_generated_diagrams(output, "PLAN_CHANGES", base_document=base, final_document={"sections": [section]}, repair=repair)
    blocks = result["change_plan"]["operations"][0]["content"]["blocks"]
    assert calls == [(SOURCE, "activity", text["text"], "plan/op1/2")]
    assert blocks == [text, old, {**new, "source": fixed}]
    assert output == untouched


def test_excessive_source_nesting_is_rejected_without_model_guess():
    source = "@startuml\n" + 'partition "Роль" {\n' * 70 + ":Действие;\n" + "}\n" * 70 + "@enduml"
    result = repair_diagram(source, "activity", "text", check=lambda _: DiagramCheck("syntax_error"), generate=lambda _: pytest.fail("model called"), state={}, checkpoint=lambda: None)
    assert result.source == source and result.status == "unverifiable" and result.requires_review


def test_excessively_nested_model_json_consumes_only_two_attempts():
    state = {}
    raw = "[" * 2000 + "0" + "]" * 2000
    result = repair_diagram(SOURCE, "activity", "text", check=lambda _: DiagramCheck("syntax_error"), generate=lambda _: raw, state=state, checkpoint=lambda: None)
    assert result.source == SOURCE and result.status == "exhausted" and result.requires_review
    assert len(state["attempts"]) == 2
