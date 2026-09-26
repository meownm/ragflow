"""Normalization and reference rules for generated questions and proposals."""

from collections.abc import Mapping
from typing import Any
import unicodedata

from business_documents.domain.errors import RuleViolation
from business_documents.domain.hashing import stable_hash


def canonical_tag(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def normalize_question(raw: object, stage: str, section_ids: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuleViolation("INVALID_QUESTION", "Question must be an object")
    text, options = raw.get("text"), raw.get("options")
    if raw.get("stage") != stage:
        raise RuleViolation("QUESTION_STAGE_CONFLICT", "Question stage does not match the active workflow stage")
    if raw.get("target_section_id") not in section_ids:
        raise RuleViolation("QUESTION_SECTION_NOT_FOUND", "Question target_section_id is not in the published template")
    if not isinstance(text, str) or not text.strip() or not isinstance(options, list) or not 2 <= len(options) <= 4:
        raise RuleViolation("INVALID_QUESTION", "Question requires text and 2 to 4 options")
    normalized_options, seen_ids = [], set()
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get("option_id"), str) or not option["option_id"] or not isinstance(option.get("label"), str) or not option["label"].strip():
            raise RuleViolation("INVALID_QUESTION_OPTION", "Each option requires a non-empty option_id and label")
        if option["option_id"] in seen_ids:
            raise RuleViolation("DUPLICATE_QUESTION_OPTION", "Question option ids must be unique")
        seen_ids.add(option["option_id"])
        normalized_options.append({"option_id": option["option_id"], "label": option["label"].strip()})
    return {
        "stage": stage,
        "target_section_id": raw.get("target_section_id"),
        "semantic_tag": canonical_tag(raw["semantic_tag"]),
        "text": text.strip(),
        "options": normalized_options,
        "allow_custom_answer": raw.get("allow_custom_answer", True) is True,
        "evidence_refs": raw.get("evidence_refs", []),
    }


def normalize_proposal(raw: object, section_ids: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not raw["text"].strip():
        raise RuleViolation("INVALID_PROPOSAL", "Proposal text must be non-empty")
    if raw.get("target_section_id") not in section_ids:
        raise RuleViolation("PROPOSAL_SECTION_NOT_FOUND", "Proposal target_section_id is not in the published template")
    fingerprint = stable_hash({"target_section_id": raw.get("target_section_id"), "text": " ".join(unicodedata.normalize("NFKC", raw["text"]).casefold().split())})
    scope = stable_hash({"source_event_ids": sorted(set(raw.get("source_event_ids", []))), "evidence_refs": sorted(set(raw.get("evidence_refs", [])))})
    return {
        "target_section_id": raw.get("target_section_id"),
        "text": raw["text"].strip(),
        "rationale": raw.get("rationale"),
        "evidence_refs": raw.get("evidence_refs", []),
        "fingerprint": fingerprint,
        "source_scope_hash": scope,
    }


def validate_comment_dispositions(output: Mapping[str, Any], comment_event_ids: frozenset[str], questions_by_key: Mapping[tuple[str, str], str], answered_ids: frozenset[str]) -> None:
    dispositions = output["comment_dispositions"]
    disposition_ids = [item["comment_event_id"] for item in dispositions]
    if len(disposition_ids) != len(set(disposition_ids)):
        raise RuleViolation("DUPLICATE_COMMENT_DISPOSITION", "Each active comment must have exactly one disposition")
    missing, unknown = sorted(comment_event_ids - set(disposition_ids)), sorted(set(disposition_ids) - comment_event_ids)
    if missing or unknown:
        raise RuleViolation("COMMENT_DISPOSITION_INCOMPLETE", "Review assessment must classify every and only active-cycle comments", {"missing_event_ids": missing, "unknown_event_ids": unknown})
    question_tags = {canonical_tag(question["semantic_tag"]) for question in output["questions"] if isinstance(question, dict) and isinstance(question.get("semantic_tag"), str)}
    for disposition in dispositions:
        if disposition["disposition"] != "NEEDS_QUESTION":
            continue
        tag = canonical_tag(disposition.get("question_semantic_tag", ""))
        if tag not in question_tags:
            raise RuleViolation(
                "COMMENT_DISPOSITION_QUESTION_NOT_FOUND", "NEEDS_QUESTION must reference a concrete question from the same review plan", {"comment_event_id": disposition["comment_event_id"]}
            )
        question_id = questions_by_key.get(("REVIEW", tag))
        if question_id in answered_ids:
            raise RuleViolation(
                "COMMENT_DISPOSITION_QUESTION_CLOSED",
                "NEEDS_QUESTION must reference an open question",
                {
                    "comment_event_id": disposition["comment_event_id"],
                    "question_id": question_id,
                    "question_semantic_tag": tag,
                    "required_action": "Use CONFIRMED_CHANGE or NO_CHANGE if the existing answer resolves the comment; otherwise emit a different question with a new semantic_tag",
                },
            )
