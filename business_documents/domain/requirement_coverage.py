"""Validate the bounded verdict of one semantic review of a structured diff."""

from collections.abc import Mapping

from business_documents.domain.errors import RuleViolation
from business_documents.domain.content_quality import semantic_duplicate_section_pairs


def changed_section_repetitions(before: Mapping, after: Mapping) -> tuple[tuple[str, str], ...]:
    """Locate possible repetitions for review against confirmed author intent.

    Similar content is not itself a violation: references, independent uses and
    explicitly requested copies are legitimate. The semantic review decides
    whether a candidate contradicts a confirmed relocation.
    """
    previous = {section["id"]: section["blocks"] for section in before["sections"]}
    changed = {section["id"] for section in after["sections"] if previous.get(section["id"]) != section["blocks"]}
    if not changed:
        return ()
    comparable = []
    for section in after["sections"]:
        blocks = []
        for block in section["blocks"]:
            if block["type"] == "list":
                blocks.extend({"type": "paragraph", "text": item} for item in block["items"])
            elif block["type"] == "table":
                blocks.extend({"type": "table", "rows": [row]} for row in block["rows"])
            elif block["type"] == "paragraph":
                blocks.append(block)
        comparable.append({"id": section["id"], "blocks": blocks})
    pairs = ((left["id"], right["id"]) for index, left in enumerate(comparable) for right in comparable[index + 1 :] if left["id"] in changed or right["id"] in changed)
    return semantic_duplicate_section_pairs(comparable, section_pairs=pairs)


def validate_requirement_coverage(report: object, sources: Mapping[str, Mapping[str, str]], sections: Mapping[str, str]) -> dict:
    def invalid():
        raise RuleViolation("INVALID_REQUIREMENT_COVERAGE", "Semantic verification must return PASS without issues or FAIL with up to four source-scoped issues")

    if not isinstance(report, dict) or set(report) != {"status", "issues"}:
        invalid()
    status, issues = report["status"], report["issues"]
    if status not in ("PASS", "FAIL") or not isinstance(issues, list) or len(issues) > 4 or (status == "PASS") != (len(issues) == 0):
        invalid()
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {"source_id", "section_id", "reason"}:
            invalid()
        source_id, section_id, reason = issue["source_id"], issue["section_id"], issue["reason"]
        if (
            not isinstance(source_id, str)
            or source_id not in sources
            or not isinstance(section_id, str)
            or section_id not in sections
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 200
        ):
            invalid()
    if issues:
        raise RuleViolation(
            "REQUIREMENT_COVERAGE_FAILED",
            "Generated changes omit, contradict or misplace confirmed requirements",
            {"requirements": [{**issue, "text": sources[issue["source_id"]]["text"]} for issue in issues]},
        )
    return report
