"""Bounded syntax recovery through injected model, renderer and checkpoint ports."""

from copy import deepcopy
from dataclasses import dataclass
import json
import re
from typing import Any, Callable

from business_documents.domain.diagram_repair import apply_syntax_edits, syntax_context
from business_documents.domain.errors import RuleViolation
from business_documents.domain.content import render_section_text, with_document_warnings


@dataclass(frozen=True)
class DiagramCheck:
    status: str
    message: str = ""
    line: int | None = None
    version: str = ""


@dataclass(frozen=True)
class DiagramRepairResult:
    source: str
    status: str
    requires_review: bool = False


def repair_diagram(
    source: str,
    kind: str,
    scenario_text: str,
    *,
    check: Callable[[str], DiagramCheck],
    generate: Callable[[dict[str, Any]], object],
    state: dict[str, Any],
    checkpoint: Callable[[], None],
) -> DiagramRepairResult:
    original = source
    current = original
    for patch in state.get("patches", []):
        current = apply_syntax_edits(current, patch, kind)
    if state.get("repaired"):
        checked = check(current)
        if checked.status == "valid":
            return DiagramRepairResult(current, "repaired")
        # Do not revert an already verified patch when the renderer becomes unavailable.
        return DiagramRepairResult(current, "exhausted", True) if checked.status == "syntax_error" else DiagramRepairResult(current, "unavailable")
    rendered = check(source)
    if rendered.status != "syntax_error":
        return DiagramRepairResult(source, "unchanged" if rendered.status == "valid" else "unavailable")
    try:
        syntax_context(original, kind)
    except RuleViolation:
        return DiagramRepairResult(original, "unverifiable", True)
    attempts = state.setdefault("attempts", [])
    if attempts and attempts[-1]["status"] == "reserved":
        # A crashed/cancelled request may have reached the provider. Never resend it.
        return DiagramRepairResult(original, "uncertain", True)
    if current != source:
        rendered = check(current)
    while len(attempts) < 2:
        context = syntax_context(current, kind)
        payload = {
            "job_input": {"task_type": "REPAIR_PLANTUML"},
            "attempt": len(attempts) + 1,
            "expected_diagram_type": kind,
            "scenario_text": scenario_text,
            "original_source": original,
            "source_lines": [{"line": n, "code": line} for n, line in enumerate(current.splitlines(), 1)],
            "editable_lines": context["editable_lines"],
            "multiway_groups": context["multiway_groups"],
            "renderer_error": {"message": rendered.message[:800], "line": rendered.line, "version": rendered.version},
        }
        if attempts:
            payload["previous_attempt"] = deepcopy(attempts[-1])
        attempt: dict[str, Any] = {"status": "reserved"}
        attempts.append(attempt)
        checkpoint()
        raw = generate(payload)
        attempt["response"] = raw
        try:
            if isinstance(raw, str):
                wrapped = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", raw, re.DOTALL)
                patch = json.loads(wrapped[1] if wrapped else raw)
            else:
                patch = raw
            if patch == {"edits": []}:
                attempt["status"] = "declined"
                checkpoint()
                return DiagramRepairResult(original, "declined", True)
            fixed = apply_syntax_edits(current, patch, kind)
            rendered = check(fixed)
            attempt.update(status=rendered.status, diagnostic={"message": rendered.message[:800], "line": rendered.line})
            state.setdefault("patches", []).append(patch)
            if rendered.status == "valid":
                state["repaired"] = True
                checkpoint()
                return DiagramRepairResult(fixed, "repaired")
            if rendered.status != "syntax_error":
                checkpoint()
                return DiagramRepairResult(original, "unavailable", True)
            current = fixed
        except (RuleViolation, ValueError, TypeError, RecursionError) as error:
            attempt.update(status="unsafe", diagnostic={"message": str(error)[:800]})
        checkpoint()
    return DiagramRepairResult(original, "exhausted", True)


def repair_generated_diagrams(
    output: dict[str, Any],
    job_type: str,
    *,
    base_document: dict[str, Any],
    final_document: dict[str, Any] | None,
    repair: Callable[[str, str, str, str], DiagramRepairResult],
) -> dict[str, Any]:
    """Repair only generated/changed blocks; failed plans remain unapplied atomically."""
    result = deepcopy(output)
    warning_code = "PLANTUML_REPAIR_NOT_APPLIED"
    if job_type == "GENERATE_DRAFT":
        document = result["draft"]
        targets = [(section["id"], f"draft/{section['id']}/{index}", block) for section in document["sections"] for index, block in enumerate(section["blocks"])]
    elif job_type == "PLAN_CHANGES" and "change_plan" in result:
        document = final_document or {}
        targets = []
        for operation in result["change_plan"]["operations"]:
            blocks = [operation["block"]] if "block" in operation else operation.get("content", {}).get("blocks", [])
            targets.extend((operation["section_id"], f"plan/{operation['operation_id']}/{index}", block) for index, block in enumerate(blocks))
    else:
        return result
    holder = document if job_type == "GENERATE_DRAFT" else result
    if any(warning.get("code") == warning_code for warning in holder.get("warnings", [])):
        holder["warnings"] = [warning for warning in holder["warnings"] if warning.get("code") != warning_code]
        if not holder["warnings"]:
            holder.pop("warnings")
    sections = {section["id"]: section for section in document.get("sections", [])}
    old_sources = {block["id"]: block.get("source") for section in base_document.get("sections", []) for block in section["blocks"] if block.get("id") and block.get("type") == "plantuml"}
    warnings = []
    requires_review = False
    for section_id, position, block in targets:
        if block.get("type") != "plantuml" or section_id not in {"4.1", "4.3"}:
            continue
        source = block["source"]
        if job_type == "PLAN_CHANGES" and old_sources.get(block.get("id")) == source:
            continue
        section = sections.get(section_id, {"id": section_id, "blocks": []})
        context = render_section_text({**section, "blocks": [item for item in section["blocks"] if item.get("type") != "plantuml"]})
        if not context.strip() and section_id == "4.1":
            context = "\n".join(
                render_section_text({**item, "blocks": [b for b in item["blocks"] if b.get("type") != "plantuml"]}) for sid in ("1", "3.1", "3.2", "4.2") if (item := sections.get(sid))
            )
        outcome = repair(source, "component" if section_id == "4.1" else "activity", context, position)
        block["source"] = outcome.source
        if outcome.status not in {"repaired", "unchanged"}:
            requires_review = requires_review or outcome.requires_review
            message = (
                "Не удалось проверить схему. Исходный код сохранён."
                if outcome.status == "unavailable"
                else "Не удалось безопасно исправить схему автоматически. Исходный код сохранён для ручной проверки."
            )
            warnings.append({"code": warning_code, "section_id": section_id, "message": message})
    if job_type == "GENERATE_DRAFT":
        result["draft"] = with_document_warnings(document, warnings)
    elif warnings and requires_review:
        return {"unapplied_changes": json.dumps(result["change_plan"], ensure_ascii=False), "warnings": [*result.get("warnings", []), *warnings]}
    elif warnings:
        result["warnings"] = [*result.get("warnings", []), *warnings]
    return result
