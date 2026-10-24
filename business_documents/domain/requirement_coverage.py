"""Validate the bounded verdict of one semantic review of a structured diff."""

from collections.abc import Collection, Mapping
import re
from typing import Any

from business_documents.domain.change_plan import active_change_input_event_ids
from business_documents.domain.errors import RuleViolation
from business_documents.domain.content_quality import semantic_duplicate_section_pairs


def agreement_decisions(snapshot: Mapping) -> dict:
    """Project answer contents and latest proposal decisions from pinned, ordered events.

    This is review context, not change authorization. Textual supersession stays
    a semantic question; a later event alone never retires another requirement.
    """
    history, protocol = snapshot.get("agreement_history", {}), snapshot.get("protocol", {})
    questions = {row["id"]: row for row in history.get("questions", [])}
    answers = {row["id"]: row for row in history.get("answers", [])}
    proposals = {row["id"]: row for row in history.get("proposals", [])}
    for question in protocol.get("questions", []):
        if question.get("question_id"):
            questions.setdefault(question["question_id"], question)
        answer = question.get("answer") or {}
        if answer.get("answer_id"):
            answers.setdefault(answer["answer_id"], answer)
    for proposal in protocol.get("proposals", []):
        if proposal.get("proposal_id"):
            proposals.setdefault(proposal["proposal_id"], proposal)
    answered, decided = {}, {}
    for event in snapshot.get("source_events", []):
        payload = event.get("payload") or {}
        kind = event["event_type"]
        if kind == "QuestionAnswered":
            question_id = payload.get("question_id")
            question = questions.get(question_id, {})
            answer = answers.get(payload.get("answer_id"), {})
            selected = next((option for option in question.get("options", []) if option.get("option_id") == answer.get("selected_option_id")), {})
            answered[question_id] = {
                "question_id": question_id,
                "event_id": event["event_id"],
                "sequence": event.get("sequence"),
                "review_cycle": question.get("review_cycle"),
                "context": question.get("text"),
                "text": answer.get("custom_answer") or selected.get("label"),
            }
        elif kind == "ProposalDecided":
            proposal_id = payload["proposal_id"]
            decided[proposal_id] = {
                "proposal_id": proposal_id,
                "event_id": event["event_id"],
                "sequence": event.get("sequence"),
                "review_cycle": proposals.get(proposal_id, {}).get("review_cycle"),
                "status": payload["decision"],
                "text": proposals.get(proposal_id, {}).get("text"),
            }
        elif kind == "ProposalAcceptanceCancelled":
            decision = decided.get(payload.get("proposal_id"))
            if decision and decision["event_id"] == payload.get("decision_event_id"):
                decision.update(status="CANCELLED", cancellation_event_id=event["event_id"])
    return {"answers": list(answered.values()), "proposals": list(decided.values())}


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


def validate_requirement_coverage(report: object, sources: Mapping[str, Mapping[str, str]], sections: Mapping[str, str], *, history_event_ids: Collection[str] = ()) -> dict:
    def invalid():
        raise RuleViolation("INVALID_REQUIREMENT_COVERAGE", "Semantic verification must return PASS without issues or FAIL with source-scoped issues")

    if not isinstance(report, dict):
        invalid()
    status, issues = report.get("status"), report.get("issues")
    if status not in ("PASS", "FAIL") or not isinstance(issues, list) or (status == "PASS") != (len(issues) == 0):
        invalid()
    for issue in issues:
        if not isinstance(issue, dict) or not {"source_id", "section_id", "reason"} <= issue.keys():
            invalid()
        source_id, section_id, reason = issue["source_id"], issue["section_id"], issue["reason"]
        if (
            not isinstance(source_id, str)
            or (source_id not in sources and not (source_id.startswith("history:") and source_id.removeprefix("history:") in history_event_ids))
            or not isinstance(section_id, str)
            or section_id not in sections
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            invalid()
    return {"status": status, "issues": [{key: issue[key] for key in ("source_id", "section_id", "reason")} for issue in issues]}


def confirmed_change_sources(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    active = set(active_change_input_event_ids(payload))
    protocol = payload.get("protocol") or {}
    sources: dict[str, str] = {}
    contexts: dict[str, str] = {}
    section_titles = {section["id"]: section["title"] for section in payload.get("current_revision", {}).get("document_ast", {}).get("sections", [])}
    for comment in protocol.get("comments", []):
        event_id = comment.get("source_event_id")
        if event_id in active and (comment.get("disposition") or {}).get("disposition") == "CONFIRMED_CHANGE":
            sources[event_id] = comment["text"]
            if comment.get("section_id"):
                section_id = comment["section_id"]
                contexts[event_id] = f"Комментарий к разделу {section_id} «{section_titles.get(section_id, '')}»."
                if quote := (comment.get("anchor") or {}).get("selected_text"):
                    contexts[event_id] += f" Выделенный фрагмент: {quote}"
    for proposal in protocol.get("proposals", []):
        event_id = proposal.get("decision_event_id")
        if event_id in active and proposal.get("decision") == "ACCEPTED":
            sources[event_id] = proposal["text"]
            if proposal.get("target_section_id"):
                section_id = proposal["target_section_id"]
                contexts[event_id] = f"Предложение к разделу {section_id} «{section_titles.get(section_id, '')}»."
    for question in protocol.get("questions", []):
        answer = question.get("answer") or {}
        event_id = answer.get("source_event_id")
        if event_id in active:
            selected = next((option for option in question.get("options", []) if option.get("option_id") == answer.get("selected_option_id")), {})
            sources[event_id] = answer.get("custom_answer") or selected.get("label") or ""
            contexts[event_id] = question["text"]
    return {event_id: {"text": text, **({"context": contexts[event_id]} if event_id in contexts else {})} for event_id, text in sources.items()}


def active_eva_update_event_ids(payload: dict[str, Any]) -> list[str]:
    active = set(active_change_input_event_ids(payload))
    return [
        event["event_id"]
        for event in payload.get("source_events", [])
        if event.get("event_id") in active and event.get("event_type") == "EvaDocumentPulled" and isinstance(event.get("payload", {}).get("remote_markdown"), str)
    ]


def requirement_fragments(sources: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        f"{source_id}:{index}": {"source_id": source_id, "text": text.strip(), **({"context": source["context"]} if "context" in source else {})}
        for source_id, source in sources.items()
        for index, text in enumerate(re.split(r"(?<=[.!?;])\s+|\n+", source["text"]))
        if text.strip()
    }
