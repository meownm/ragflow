"""Check semantic verdicts and resolve citations against the actual result blocks."""

from collections.abc import Mapping
import re

from business_documents.domain.errors import RuleViolation


def requirement_fragments(sources: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """Keep every source sentence, including enumerations, in the check inventory."""
    return {
        f"{source_id}:{index}": {"source_id": source_id, "text": text.strip()}
        for source_id, source in sources.items()
        for index, text in enumerate(re.split(r"(?<=[.!?;])\s+|\n+", source))
        if text.strip()
    }


def validate_requirement_coverage(report: object, fragments: Mapping[str, Mapping[str, str]], blocks: Mapping[str, str]) -> dict:
    def invalid(message):
        raise RuleViolation("INVALID_REQUIREMENT_COVERAGE", message)

    if not isinstance(report, dict) or set(report) != {"checks"} or not isinstance(report["checks"], list):
        invalid("Requirement verification must return checks")
    seen = set()
    missing = []
    for check in report["checks"]:
        if not isinstance(check, dict) or set(check) != {"fragment_id", "status", "matches", "superseded_by", "required_section_ids"}:
            invalid("Requirement verification fields are invalid")
        fragment_id = check["fragment_id"]
        if not isinstance(fragment_id, str) or fragment_id not in fragments or fragment_id in seen:
            invalid("Requirement verification contains unknown or duplicate fragments")
        seen.add(fragment_id)
        status = check["status"]
        if not isinstance(status, str) or status not in {"PRESENT", "MISSING", "CONTRADICTED", "NOT_REQUIREMENT", "SUPERSEDED", "REMOVAL"} or not isinstance(check["matches"], list):
            invalid("Requirement verification status is invalid")
        for block_id in check["matches"]:
            if not isinstance(block_id, str) or block_id not in blocks or not blocks[block_id].strip():
                invalid("Requirement verification must cite an existing nonempty final-document block")
        if status == "PRESENT" and not check["matches"]:
            invalid("A present requirement needs a final-document citation")
        required_sections = check["required_section_ids"]
        if (
            not isinstance(required_sections, list)
            or any(not isinstance(section_id, str) or section_id not in {"5.4", "5.5"} for section_id in required_sections)
            or len(required_sections) != len(set(required_sections))
        ):
            invalid("Requirement placement must name reporting, monitoring, or neither")
        cited_sections = {block_id.rsplit(":", 1)[0] for block_id in check["matches"]}
        if status == "PRESENT" and not set(required_sections) <= cited_sections:
            missing.append({"fragment_id": fragment_id, **fragments[fragment_id], "status": "MISSING", "reason": "WRONG_SECTION", "required_section_ids": required_sections})
        if status == "REMOVAL" and (fragments[fragment_id]["source_id"].startswith("section:") or check["matches"]):
            invalid("Only an active instruction with no retained text may authorize removal")
        superseded_by = check["superseded_by"]
        if status == "SUPERSEDED":
            if (
                not fragments[fragment_id]["source_id"].startswith("section:")
                or not isinstance(superseded_by, str)
                or superseded_by not in fragments
                or fragments[superseded_by]["source_id"].startswith("section:")
            ):
                invalid("Only an explicit active input may supersede an existing requirement")
        elif superseded_by is not None:
            invalid("Only superseded requirements may reference a replacement")
        if status in {"MISSING", "CONTRADICTED"}:
            missing.append({"fragment_id": fragment_id, **fragments[fragment_id], "status": status})
    if seen != set(fragments):
        invalid("Requirement verification omitted source fragments")
    statuses = {check["fragment_id"]: check["status"] for check in report["checks"]}
    if any(check["status"] == "SUPERSEDED" and statuses[check["superseded_by"]] not in {"PRESENT", "REMOVAL"} for check in report["checks"]):
        invalid("A superseded requirement needs a present replacement or an explicit removal")
    if missing:
        raise RuleViolation("REQUIREMENT_COVERAGE_FAILED", "Generated changes omit, contradict or misplace confirmed requirements", {"requirements": missing})
    # The model selects evidence; only the server supplies its actual text.
    return {"checks": [{**check, "matches": [{"block_id": block_id, "quote": blocks[block_id]} for block_id in check["matches"]]} for check in report["checks"]]}
