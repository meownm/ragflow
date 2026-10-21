"""Non-mutating assistant turns have a closed contract distinct from edits."""

from collections.abc import Mapping
from copy import deepcopy


def dialogue_response(value: object) -> dict | None:
    if not isinstance(value, Mapping) or "response_kind" not in value:
        return None
    kind = value.get("response_kind")
    fields = {"response_kind", "answer", "questions"} if kind == "CLARIFICATION" else {"response_kind", "answer"}
    if not isinstance(kind, str) or kind not in {"EXPLANATION", "CLARIFICATION"} or set(value) != fields or not isinstance(value.get("answer"), str) or not 1 <= len(value["answer"].strip()) <= 12000:
        raise ValueError("Invalid SQL dialogue response")
    if kind == "CLARIFICATION":
        questions = value["questions"]
        if not isinstance(questions, list) or not 1 <= len(questions) <= 5:
            raise ValueError("Ask from one to five focused clarification questions")
        seen = set()
        for question in questions:
            if not isinstance(question, Mapping) or set(question) != {"id", "text", "options"}:
                raise ValueError("Invalid clarification question")
            if not isinstance(question["id"], str) or not 1 <= len(question["id"]) <= 128 or question["id"] in seen or not isinstance(question["text"], str) or not 1 <= len(question["text"].strip()) <= 1000:
                raise ValueError("Clarification questions require distinct identities and text")
            options = question["options"]
            if not isinstance(options, list) or len(options) > 6 or any(not isinstance(option, str) or not 1 <= len(option.strip()) <= 500 for option in options) or len(set(options)) != len(options):
                raise ValueError("Clarification choices are invalid")
            seen.add(question["id"])
    return deepcopy(dict(value))
