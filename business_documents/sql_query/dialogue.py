"""Normalize the fields used by a non-mutating assistant turn."""

from collections.abc import Mapping


def dialogue_response(value: object) -> dict | None:
    if not isinstance(value, Mapping) or "response_kind" not in value:
        return None
    kind = value.get("response_kind")
    if not isinstance(kind, str) or kind not in {"EXPLANATION", "CLARIFICATION"} or not isinstance(value.get("answer"), str) or not value["answer"].strip():
        raise ValueError("Invalid SQL dialogue response")
    result = {"response_kind": kind, "answer": value["answer"]}
    if kind == "CLARIFICATION":
        questions = value.get("questions")
        if not isinstance(questions, list) or not questions:
            raise ValueError("Clarification requires a question")
        result["questions"] = []
        seen = set()
        for question in questions:
            if not isinstance(question, Mapping):
                raise ValueError("Invalid clarification question")
            if not isinstance(question.get("id"), str) or not question["id"].strip() or question["id"] in seen or not isinstance(question.get("text"), str) or not question["text"].strip():
                raise ValueError("Clarification questions require distinct identities and text")
            options = question.get("options", [])
            if not isinstance(options, list) or any(not isinstance(option, str) or not option.strip() for option in options):
                raise ValueError("Clarification choices are invalid")
            seen.add(question["id"])
            result["questions"].append({"id": question["id"], "text": question["text"], "options": list(dict.fromkeys(options))})
    return result
