"""Normalization and reference rules for generated questions and proposals."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from typing import Any
import unicodedata

from business_documents.domain.errors import RuleViolation
from business_documents.domain.hashing import stable_hash
from business_documents.domain.content import SECTION_CONTENT_WARNING_CODES, UNDEFINED_REQUIREMENTS_TEXT, render_section_text, with_document_warnings


def canonical_tag(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def empty_section_questions(
    document: Mapping[str, Any],
    template: Mapping[str, Any],
    questions: Sequence[Mapping[str, Any]],
    known_questions: Sequence[Mapping[str, Any]] = (),
    *,
    answered_section_ids: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Ask for missing information without declaring its absence on the author's behalf."""
    sections = {section["id"]: section for section in document.get("sections", [])}
    covered = {question["target_section_id"] for question in questions}
    known_tags = {canonical_tag(question["semantic_tag"]) for question in known_questions}
    result = []
    for section in template["sections"]:
        section_id = section["id"]
        tag = f"empty_section:{section_id}"
        text = render_section_text(sections.get(section_id, {"blocks": []})).strip()
        if section_id in covered or tag in known_tags or text not in {"", UNDEFINED_REQUIREMENTS_TEXT} or (text == UNDEFINED_REQUIREMENTS_TEXT and section_id in answered_section_ids):
            continue
        result.append(
            {
                "semantic_tag": tag,
                "stage": "REVIEW",
                "target_section_id": section_id,
                "text": f"По разделу {section_id} «{section['title']}» нет сведений. Предоставьте информацию или подтвердите её отсутствие/неприменимость.",
                "options": [{"option_id": "ABSENT", "label": "Подтверждаю отсутствие информации"}, {"option_id": "NOT_APPLICABLE", "label": "Раздел неприменим"}],
                "allow_custom_answer": True,
            }
        )
    return result


def without_answered_gap_warnings(document: dict[str, Any], answered_section_ids: frozenset[str]) -> dict[str, Any]:
    """Keep substantive warnings; an answered placeholder needs no structural reminder.

    The model still owns follow-up questions. Open questions independently prevent
    agreement even if an earlier answer exists for the same section.
    """
    sections = {section["id"] for section in document.get("sections", []) if section["id"] in answered_section_ids and render_section_text(section).strip() == UNDEFINED_REQUIREMENTS_TEXT}
    warnings = [item for item in document.get("warnings", []) if item["code"] not in SECTION_CONTENT_WARNING_CODES or item.get("section_id") not in sections]
    if len(warnings) == len(document.get("warnings", [])):
        return document
    result = deepcopy({**document, "warnings": warnings})
    if not warnings:
        result.pop("warnings", None)
    return result


def normalize_assessment_output(output: dict[str, Any], stage: str, schema: Mapping[str, Any], *, section_ids: frozenset[str], known_questions=()) -> dict[str, Any]:
    """Preserve presentation deviations without making decisions for the author."""
    result, warnings = deepcopy(output), []

    def note(value, section_id=None):
        warnings.append(
            {
                "code": "ASSESSMENT_FORMAT_NORMALIZED",
                **({"section_id": section_id} if isinstance(section_id, str) else {}),
                "message": "Оформление уточнений упрощено; исходные данные: " + json.dumps(value, ensure_ascii=False),
            }
        )

    supplied = result.pop("warnings", [])
    if isinstance(supplied, list) and all(
        isinstance(w, dict)
        and isinstance(w.get("code"), str)
        and isinstance(w.get("message"), str)
        and set(w) <= {"code", "message", "section_id"}
        and ("section_id" not in w or isinstance(w["section_id"], str))
        for w in supplied
    ):
        warnings.extend(supplied)
    else:
        note(supplied)
    extra = {key: result.pop(key) for key in list(result) if key not in schema["properties"]}
    if extra:
        note(extra)
    if result.get("schema_version") != "1":
        note({"schema_version": result.get("schema_version")})
    result["schema_version"] = "1"
    questions = result.get("questions", [])
    if not isinstance(questions, list):
        note(questions)
        questions = [questions]
    if stage == "INTAKE" and not questions and result.get("outcome") != "COMPLETE":
        note(output)
        questions = [{"text": "Уточните исходные данные с учётом замечаний модели."}]
    question_fields = schema["properties"]["questions"]["items"]["properties"]
    option_fields = question_fields["options"]["items"]["properties"]

    def fingerprint(question):
        return stable_hash({"section": question.get("target_section_id"), "text": " ".join(unicodedata.normalize("NFKC", question.get("text", "")).split()).casefold()})

    normalized = []
    tag_aliases = {}
    tags = {canonical_tag(q["semantic_tag"]): fingerprint(q) for q in known_questions if isinstance(q, Mapping) and isinstance(q.get("semantic_tag"), str)}
    for raw in questions:
        question = dict(raw) if isinstance(raw, dict) else {"text": raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)}
        extra = {key: question.pop(key) for key in list(question) if key not in question_fields and key != "stage"}
        if extra:
            note(extra, question.get("target_section_id"))
        if "stage" in question_fields:
            question["stage"] = stage
        else:
            question.pop("stage", None)
        question.setdefault("target_section_id", "1")
        section_id = question["target_section_id"]
        if type(section_id) in {int, float} and str(section_id) in section_ids:
            note({"target_section_id": section_id})
            question["target_section_id"] = str(section_id)
        elif not isinstance(section_id, str) or section_id not in section_ids:
            note({"question_with_unknown_section": raw, "question_shown_in_section": "1"})
            question["target_section_id"] = "1"
        if "reason" in question and not isinstance(question["reason"], str):
            note({"reason": question.pop("reason")}, question["target_section_id"])
        if not isinstance(question.get("text"), str) or not question["text"].strip():
            note(raw, question.get("target_section_id"))
            question["text"] = "Уточните вводные по сохранённым замечаниям модели."
        tag = question.get("semantic_tag")
        if not isinstance(tag, str) or not tag.strip() or len(tag) > 128:
            note({"semantic_tag": tag}, question.get("target_section_id"))
            question["semantic_tag"] = "question:" + stable_hash({"section": question["target_section_id"], "text": question["text"]}).removeprefix("sha256:")
        original_tag = canonical_tag(question["semantic_tag"])
        question_fingerprint = fingerprint(question)
        if original_tag in tags and tags[original_tag] != question_fingerprint:
            note(raw, question.get("target_section_id"))
            question["semantic_tag"] = "question:" + question_fingerprint.removeprefix("sha256:")
        tags[canonical_tag(question["semantic_tag"])] = question_fingerprint
        tag_aliases.setdefault(tag if isinstance(tag, str) else question["semantic_tag"], set()).add(question["semantic_tag"])
        options = question.get("options", [])
        if not isinstance(options, list):
            note({"options": options}, question.get("target_section_id"))
            options = []
        normalized_options, seen = [], set()
        for index, raw_option in enumerate(options):
            option = dict(raw_option) if isinstance(raw_option, dict) else {"label": str(raw_option)}
            extra = {key: option.pop(key) for key in list(option) if key not in option_fields}
            identifier = option.get("option_id")
            if extra or not isinstance(identifier, str) or not identifier or len(identifier) > 64 or identifier in seen or not isinstance(option.get("label"), str) or not option["label"].strip():
                note(raw_option, question.get("target_section_id"))
                option = {
                    "option_id": f"option-{index + 1}-" + question_fingerprint[-8:],
                    "label": option.get("label") if isinstance(option.get("label"), str) and option["label"].strip() else json.dumps(raw_option, ensure_ascii=False),
                }
            while option["option_id"] in seen:
                option["option_id"] = "option-" + stable_hash([index, option["option_id"]])[-32:]
            seen.add(option["option_id"])
            normalized_options.append(option)
        if not 2 <= len(options) <= 4:
            note({"options_count": len(options), "free_text_answer_available": True}, question.get("target_section_id"))
        question["options"], question["allow_custom_answer"] = normalized_options, True
        normalized.append(question)
    if len(normalized) > 4:
        note({"question_count": len(normalized), "message": "Все вопросы сохранены; лимит на итерацию является рекомендацией."})
    result["questions"] = normalized
    if "outcome" in schema["properties"]:
        outcome = "NEEDS_INPUT" if normalized else "COMPLETE"
        if result.get("outcome") != outcome:
            note({"outcome": result.get("outcome")})
        result["outcome"] = outcome
    if "proposals" in schema["properties"]:
        fields = schema["properties"]["proposals"]["items"]["properties"]
        proposals = result.setdefault("proposals", [])
        if isinstance(proposals, list):
            for proposal in proposals:
                if isinstance(proposal, dict):
                    extra = {key: proposal.pop(key) for key in list(proposal) if key not in fields}
                    if extra:
                        note(extra, proposal.get("target_section_id"))
        result.setdefault("comment_dispositions", [])
        dispositions = result["comment_dispositions"]
        if isinstance(dispositions, list):
            fields = schema["properties"]["comment_dispositions"]["items"]["properties"]
            for disposition in dispositions:
                if isinstance(disposition, dict):
                    extra = {key: disposition.pop(key) for key in list(disposition) if key not in fields}
                    if extra:
                        note({"comment_event_id": disposition.get("comment_event_id"), **extra})
                    tag = disposition.get("question_semantic_tag")
                    if isinstance(tag, str) and tag in tag_aliases:
                        if len(tag_aliases[tag]) != 1:
                            raise RuleViolation("INVALID_REVIEW_PLAN", "Comment references an ambiguous model question tag", {"path": "comment_dispositions"})
                        disposition["question_semantic_tag"] = next(iter(tag_aliases[tag]))
    return with_document_warnings(result, warnings)


def normalize_question(raw: object, stage: str, section_ids: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuleViolation("INVALID_QUESTION", "Question must be an object")
    text, options = raw.get("text"), raw.get("options")
    if raw.get("stage") != stage:
        raise RuleViolation("QUESTION_STAGE_CONFLICT", "Question stage does not match the active workflow stage")
    if raw.get("target_section_id") not in section_ids:
        raise RuleViolation("QUESTION_SECTION_NOT_FOUND", "Question target_section_id is not in the published template")
    if not isinstance(text, str) or not text.strip() or not isinstance(options, list):
        raise RuleViolation("INVALID_QUESTION", "Question requires text and an array of options; a free-text answer is always available")
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
