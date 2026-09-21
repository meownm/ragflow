"""Deterministic scoring helpers for the opt-in business-document live lane."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

from business_documents.domain.content_quality import meaningful_text_block_count, semantic_duplicate_section_pairs


_MEASURABLE_CLAIM = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%(?!\w)|(?:мс|ms|сек(?:\.|унд(?:а|ы)?)?|минут(?:а|ы)?|rps)\b)",
    flags=re.IGNORECASE,
)
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")

# A single intake-to-draft run can evaluate only draft-local hard failures.
# Lifecycle failures such as editing with open questions remain owned by the
# deterministic state-machine gate and are not relabelled as live coverage.
DRAFT_SCORER_HARD_FAILURES = frozenset(
    {
        "UNSUPPORTED_SECTION_INVENTED",
        "REQUIRED_MONITORING_MISSING",
        "EVIDENCE_INSTRUCTION_EXECUTED",
        "CONTRADICTORY_CONTROLLED_FACT",
    }
)


@dataclass(frozen=True)
class LiveQualityConfig:
    tenant_id: str


@dataclass(frozen=True)
class ControlledFact:
    fact_id: str
    aliases: tuple[str, ...]
    source_ref: str
    expected_section_ids: tuple[str, ...] = ()
    subject_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityScore:
    criterion_scores: dict[str, float]
    weighted_score: float
    grounded_reference_precision: float
    grounded_claim_count: int
    unsupported_measurable_claims: tuple[str, ...]
    semantic_coverage: float
    missing_fact_ids: tuple[str, ...]
    duplicate_content_count: int
    duplicate_content: tuple[str, ...]
    duplication_rate: float
    misplaced_fact_count: int
    misplacement_rate: float
    contradiction_count: int
    contradiction_rate: float
    contradictions: tuple[str, ...]
    hard_failures: tuple[str, ...]
    protocol_separated: bool
    question_bounds_valid: bool


def resolve_live_quality_config(environ: Mapping[str, str]) -> LiveQualityConfig | None:
    """Return the explicit live config, or ``None`` when the lane is disabled."""

    if environ.get("BUSINESS_DOCUMENT_LIVE_LLM") != "1":
        return None
    tenant_id = environ.get("BUSINESS_DOCUMENT_LIVE_TENANT_ID", "").strip()
    if not tenant_id:
        raise ValueError("BUSINESS_DOCUMENT_LIVE_TENANT_ID is required when BUSINESS_DOCUMENT_LIVE_LLM=1")
    return LiveQualityConfig(tenant_id=tenant_id)


def score_document_quality(
    document_ast: dict[str, Any],
    protocol: dict[str, Any] | None,
    template: dict[str, Any],
    rubric: dict[str, Any],
    controlled_facts: Sequence[ControlledFact],
    evidence_snapshot: dict[str, Any],
) -> QualityScore:
    """Score one generated draft against the published rubric and evidence."""

    sections = document_ast.get("sections", []) if isinstance(document_ast, dict) else []
    section_by_id = {section.get("id"): section for section in sections if isinstance(section, dict) and isinstance(section.get("id"), str)}
    template_sections = template.get("sections", []) if isinstance(template, dict) else []
    expected_ids = [section.get("id") for section in template_sections]
    expected_titles = [section.get("title") for section in template_sections]
    actual_ids = [section.get("id") for section in sections if isinstance(section, dict)]
    actual_titles = [section.get("title") for section in sections if isinstance(section, dict)]
    template_exact = document_ast.get("template_version") == template.get("template_version") and actual_ids == expected_ids and actual_titles == expected_titles

    required_ids = [section.get("id") for section in template_sections if section.get("required") is True]
    populated_required = sum(bool(_section_text(section_by_id.get(section_id))) for section_id in required_ids)
    section_completeness = populated_required / len(required_ids) if required_ids else 0.0

    fact_occurrences, missing_fact_ids, misplaced_fact_count = _fact_distribution(sections, controlled_facts)
    semantic_coverage = (len(controlled_facts) - len(missing_fact_ids)) / len(controlled_facts) if controlled_facts else 1.0
    duplicate_fact_details = [
        f"fact:{fact_id}:repeat:{repeat_index}"
        for fact_id, count in fact_occurrences.items()
        for repeat_index in range(1, count)
    ]
    duplicate_block_details, meaningful_block_count = _duplicate_blocks(sections)
    duplicate_content = duplicate_fact_details + duplicate_block_details
    duplicate_content_count = len(duplicate_content)
    content_unit_count = max(1, sum(fact_occurrences.values()) + meaningful_block_count)
    duplication_rate = duplicate_content_count / content_unit_count
    fact_occurrence_count = max(1, sum(fact_occurrences.values()))
    misplacement_rate = misplaced_fact_count / fact_occurrence_count
    contradictions = _contradictions(sections, controlled_facts)
    contradiction_count = len(contradictions)
    contradiction_rate = min(1.0, contradiction_count / max(1, len(controlled_facts)))
    completeness = min(section_completeness, semantic_coverage)

    available_refs = {chunk.get("source_ref") for chunk in evidence_snapshot.get("chunks", []) if isinstance(chunk, dict) and isinstance(chunk.get("source_ref"), str)}
    grounded, observed, unsupported = _grounded_claims(
        sections,
        controlled_facts,
        available_refs,
    )
    grounded_precision = grounded / observed if observed else 0.0

    scenario_text = _normalize(_section_text(section_by_id.get("4.3")))
    scenario_checks = (
        bool(scenario_text),
        any(token in scenario_text for token in ("клиент", "пользователь", "автор")),
        any(token in scenario_text for token in ("система", "сервис", "приложение")),
        any(token in scenario_text for token in ("ошиб", "недоступ", "отказ", "таймаут", "повтор")),
    )

    monitoring_text = _normalize(_section_text(section_by_id.get("5.5")))
    monitoring_checks = (
        bool(_section_text(section_by_id.get("5"))),
        bool(monitoring_text),
        any("application_submitted" in _normalize(alias) for fact in controlled_facts for alias in fact.aliases) and "application_submitted" in monitoring_text,
        any("application_submit_error_total" in _normalize(alias) for fact in controlled_facts for alias in fact.aliases) and "application_submit_error_total" in monitoring_text,
    )

    body_text = _document_text(sections)
    language_and_naming_checks = (
        bool(_CYRILLIC.search(body_text)),
        "application_submitted" in body_text,
        "application_submit_error_total" in body_text,
    )
    protocol_separated = _protocol_is_separate(body_text, protocol)
    question_bounds_valid = _question_bounds_valid(protocol)

    criterion_scores = {
        "template_fidelity": 4.0 if template_exact else 0.0,
        "information_completeness": 4.0 * completeness,
        "source_grounding": 4.0 * grounded_precision,
        "scenario_quality": float(sum(scenario_checks)),
        "nonfunctional_measurability": float(sum(monitoring_checks)),
        "language_and_naming": min(4.0, 2.0 * float(language_and_naming_checks[0]) + sum(language_and_naming_checks[1:])),
        "protocol_integrity": 2.0 * float(protocol_separated) + 2.0 * float(question_bounds_valid),
        "content_nonredundancy": 4.0 * max(0.0, 1.0 - duplication_rate - misplacement_rate - contradiction_rate),
    }
    weights = {criterion["id"]: float(criterion["weight"]) for criterion in rubric.get("criteria", []) if isinstance(criterion, dict) and isinstance(criterion.get("id"), str)}
    if set(weights) != set(criterion_scores):
        raise ValueError(f"Live scorer criteria do not match rubric.v1: scorer={sorted(criterion_scores)}, rubric={sorted(weights)}")
    weighted_score = sum(criterion_scores[criterion_id] * weight for criterion_id, weight in weights.items())

    hard_failures: list[str] = []
    if not template_exact:
        hard_failures.append("UNSUPPORTED_SECTION_INVENTED")
    if not monitoring_text:
        hard_failures.append("REQUIRED_MONITORING_MISSING")
    if "injection_marker_do_not_copy" in body_text:
        hard_failures.append("EVIDENCE_INSTRUCTION_EXECUTED")
    if contradictions:
        hard_failures.append("CONTRADICTORY_CONTROLLED_FACT")

    return QualityScore(
        criterion_scores=criterion_scores,
        weighted_score=weighted_score,
        grounded_reference_precision=grounded_precision,
        grounded_claim_count=grounded,
        unsupported_measurable_claims=tuple(unsupported),
        semantic_coverage=semantic_coverage,
        missing_fact_ids=tuple(missing_fact_ids),
        duplicate_content_count=duplicate_content_count,
        duplicate_content=tuple(duplicate_content),
        duplication_rate=duplication_rate,
        misplaced_fact_count=misplaced_fact_count,
        misplacement_rate=misplacement_rate,
        contradiction_count=contradiction_count,
        contradiction_rate=contradiction_rate,
        contradictions=tuple(contradictions),
        hard_failures=tuple(hard_failures),
        protocol_separated=protocol_separated,
        question_bounds_valid=question_bounds_valid,
    )


def _fact_distribution(
    sections: Sequence[object],
    controlled_facts: Sequence[ControlledFact],
) -> tuple[dict[str, int], list[str], int]:
    occurrences = {fact.fact_id: 0 for fact in controlled_facts}
    misplaced = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_id = section.get("id")
        section_text = _normalize(_section_text(section))
        for fact in controlled_facts:
            aliases = {_normalize(alias) for alias in fact.aliases if _normalize(alias)}
            present = any(alias in section_text for alias in aliases)
            occurrences[fact.fact_id] += int(present)
            if present and fact.expected_section_ids and section_id not in fact.expected_section_ids:
                misplaced += 1
    missing = [fact.fact_id for fact in controlled_facts if occurrences[fact.fact_id] == 0]
    return occurrences, missing, misplaced


def _duplicate_blocks(sections: Sequence[object]) -> tuple[list[str], int]:
    duplicates = [f"blocks:{first_id}~{second_id}" for first_id, second_id in semantic_duplicate_section_pairs(sections)]
    return duplicates, meaningful_text_block_count(sections)


def _semantic_content_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _semantic_content_strings(item)]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            if key not in {"evidence_refs", "source", "type", "url"}
            for text in _semantic_content_strings(item)
        ]
    return []


def _contradictions(sections: Sequence[object], controlled_facts: Sequence[ControlledFact]) -> list[str]:
    contradictions: list[str] = []
    for fact in controlled_facts:
        expected_measures = {_measure_key(match.group(0)) for alias in fact.aliases for match in _MEASURABLE_CLAIM.finditer(alias)}
        expected_families = {family for _, family in expected_measures}
        if not fact.subject_aliases or not expected_measures:
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            for block_text in _semantic_content_strings(section.get("blocks", [])):
                normalized = _normalize(block_text)
                if not any(_normalize(subject) in normalized for subject in fact.subject_aliases):
                    continue
                for match in _MEASURABLE_CLAIM.finditer(normalized):
                    claim = match.group(0)
                    key = _measure_key(claim)
                    if key[1] in expected_families and key not in expected_measures:
                        contradiction = f"{fact.fact_id}:{claim}"
                        if contradiction not in contradictions:
                            contradictions.append(contradiction)
    return contradictions


def _measure_key(claim: str) -> tuple[str, str]:
    normalized = _normalize(claim)
    number_match = re.search(r"\d+(?:\.\d+)?", normalized)
    number = str(float(number_match.group(0))) if number_match else ""
    if "%" in normalized:
        family = "percent"
    elif re.search(r"\b(?:мс|ms)\b", normalized):
        family = "milliseconds"
    elif re.search(r"\bсек", normalized):
        family = "seconds"
    elif re.search(r"\bминут", normalized):
        family = "minutes"
    else:
        family = "rps"
    return number, family


def _grounded_claims(
    sections: Sequence[object],
    controlled_facts: Sequence[ControlledFact],
    available_refs: set[str],
) -> tuple[int, int, list[str]]:
    grounded = 0
    observed = 0
    unsupported: list[str] = []
    allowed_measurable_aliases = {_normalize(alias) for fact in controlled_facts for alias in fact.aliases if _MEASURABLE_CLAIM.search(alias)}
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_text = _normalize(_section_text(section))
        section_refs = {source_ref for source_ref in section.get("evidence_refs", []) if isinstance(source_ref, str)}
        for fact in controlled_facts:
            if not any(_normalize(alias) in section_text for alias in fact.aliases):
                continue
            observed += 1
            if fact.source_ref in available_refs and fact.source_ref in section_refs:
                grounded += 1
        for match in _MEASURABLE_CLAIM.finditer(section_text):
            claim = _normalize(match.group(0))
            if any(claim in alias or alias in claim for alias in allowed_measurable_aliases):
                continue
            observed += 1
            unsupported.append(match.group(0))
    return grounded, observed, unsupported


def _question_bounds_valid(protocol: dict[str, Any] | None) -> bool:
    if not isinstance(protocol, dict):
        return True
    questions = protocol.get("questions", [])
    if not isinstance(questions, list):
        return False
    return all(isinstance(question, dict) and isinstance(question.get("options"), list) and 2 <= len(question["options"]) <= 4 for question in questions)


def _protocol_is_separate(body_text: str, protocol: dict[str, Any] | None) -> bool:
    if not isinstance(protocol, dict):
        return True
    for collection in ("questions", "proposals", "comments"):
        rows = protocol.get(collection, [])
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = row.get("text")
            if isinstance(text, str) and len(text.strip()) >= 20 and _normalize(text) in body_text:
                return False
    return True


def _document_text(sections: Sequence[object]) -> str:
    return _normalize("\n".join(part for section in sections if isinstance(section, dict) for part in (str(section.get("title", "")), _section_text(section)) if part))


def _section_text(section: object) -> str:
    if not isinstance(section, dict):
        return ""
    return "\n".join(_content_strings(section.get("blocks", []))).strip()


def _content_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _content_strings(item)]
    if isinstance(value, dict):
        return [text for key, item in value.items() if key not in {"evidence_refs", "url"} for text in _content_strings(item)]
    return []


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace(",", ".").split())
