"""Frozen synthetic scenarios with independent, exact content oracles."""

import re
import time
from copy import deepcopy

from api.technical_specifications.application import Specifications
from api.technical_specifications.domain import SpecificationError, assert_context, context, sections

_CASE_IDS = {*(f"C{n:02}" for n in range(1, 18)), *(f"V{n:02}" for n in range(1, 19)), *(f"M{n:02}" for n in range(1, 13))}


def changed(base, overrides=None):
    return sections({**deepcopy(base), **deepcopy(overrides or {})})


def validate_corpus(corpus):
    if corpus.get("schema") != 2 or not corpus.get("documents") or not corpus.get("cases"):
        raise ValueError("A nonempty version-2 corpus is required")
    identifiers = set()
    for base in corpus["documents"].values():
        sections(base)
    for case in corpus["cases"]:
        if case["id"] in identifiers or case["kind"] not in {"chat", "verify", "merge"}:
            raise ValueError("Duplicate case or unsupported scenario")
        identifiers.add(case["id"])
        base = corpus["documents"][case["document"]]
        for key in ("document_changes", "before_changes", "candidate_changes", "local_changes", "remote_changes", "expected_changes"):
            changed(base, case.get(key))
        if case["kind"] == "merge" and (case.get("choice") not in {"local", "remote"} or "expected_changes" not in case):
            raise ValueError("Merge requires an explicit choice and frozen expected content")
        if case["kind"] == "verify" and type(case.get("valid")) is not bool:
            raise ValueError("Verification requires an independent verdict")
        for key in ("answer_all", "answer_any", "answer_forbidden", "issues_all"):
            for pattern in case.get(key, []):
                re.compile(pattern, re.IGNORECASE)
        facts = case.get("expected_facts", [])
        if not isinstance(facts, list) or (case["id"] in {"V14", "V17", "V18"} and not facts):
            raise ValueError("Directed verification requires independent fact expectations")
        for fact in facts:
            if (
                not isinstance(fact, dict)
                or set(fact) != {"requirement", "sections", "expected", "actual"}
                or not isinstance(fact["sections"], list)
                or not fact["sections"]
                or any(not isinstance(key, str) or key not in base for key in fact["sections"])
            ):
                raise ValueError("Invalid directed fact expectation")
            for key in ("requirement", "expected", "actual"):
                if not isinstance(fact[key], str) or not fact[key]:
                    raise ValueError("Fact expectations require nonempty patterns")
                re.compile(fact[key], re.IGNORECASE)
    if identifiers != _CASE_IDS:
        raise ValueError("Frozen case inventory is incomplete or contains extra cases")


def chat_input(base, case):
    document = {
        "id": "synthetic-spec",
        "revision": 1,
        "sections": changed(base, case.get("document_changes")),
        "messages": deepcopy(case.get("history", [])),
        "proposals": deepcopy(case.get("proposals", [])),
    }
    if case.get("source_note"):
        document["sections"]["9"] += case["source_note"]
    scope = {"document_id": document["id"], "revision": 1, "scope": case["scope"], "anchor": None}
    if scope["scope"] == "selection":
        if "anchor" in case:
            scope["anchor"] = deepcopy(case["anchor"])
        else:
            content, quote = document["sections"][case["section"]], case["quote"]
            start = -1
            for _ in range(case.get("occurrence", 0) + 1):
                start = content.index(quote, start + 1)
            scope["anchor"] = {"section": case["section"], "quote": quote, "prefix": content[max(0, start - 64) : start], "suffix": content[start + len(quote) : start + len(quote) + 64]}
    return document, {"message_id": case["id"], "text": case["text"], "context": scope}


def score_content(candidate, expected):
    candidate = sections(candidate)
    return [f"Section {key}: content differs from the frozen expected result" for key in expected if candidate[key] != expected[key]]


def score_chat(case, document, message, response):
    if not isinstance(response.get("answer"), str) or not response["answer"].strip():
        return ["No nonempty answer"]
    candidate = Specifications._chat_candidate(document, message, response)
    errors = score_content(candidate, changed(document["sections"], case.get("expected_changes")))
    answer = response["answer"]
    for pattern in case.get("answer_all", []):
        if not re.search(pattern, answer, re.IGNORECASE):
            errors.append("Missing controlled answer fact: " + pattern)
    if case.get("answer_any") and not any(re.search(pattern, answer, re.IGNORECASE) for pattern in case["answer_any"]):
        errors.append("Missing explanation or requested next action")
    for pattern in case.get("answer_forbidden", []):
        if re.search(pattern, answer, re.IGNORECASE):
            errors.append("Forbidden or invented answer fact: " + pattern)
    return errors


def score_verification(response, *, valid, issues_all=(), expected_facts=()):
    if response.get("valid") is not valid or not isinstance(response.get("issues"), list) or any(not isinstance(issue, str) or not issue.strip() for issue in response.get("issues", [])):
        return ["Incorrect verification verdict or structure"]
    if valid and response["issues"]:
        return ["Consistent controlled document was rejected"]
    if not valid and not response["issues"]:
        return ["Contradiction has no actionable explanation"]
    explanation = " ".join(response["issues"])
    errors = ["Contradiction explanation omits: " + pattern for pattern in issues_all if not re.search(pattern, explanation, re.IGNORECASE)]
    directed = [re.fullmatch(r"(.+): ожидается «([^»]+)», фактически «([^»]+)»\. Разделы: ([1-9](?:,\s*[1-9])*)\.", issue) for issue in response["issues"]]
    if expected_facts and (len(directed) != len(expected_facts) or not all(directed)):
        errors.append("Incorrect directed explanation count or structure")
    for expected in expected_facts:
        facts = response.get("facts")
        matches = (
            [
                fact
                for fact in facts
                if isinstance(fact, dict)
                and isinstance(fact.get("requirement"), str)
                and re.fullmatch(expected["requirement"], fact["requirement"], re.IGNORECASE)
                and isinstance(fact.get("sections"), list)
                and all(isinstance(key, str) for key in fact["sections"])
                and set(expected["sections"]) <= set(fact["sections"])
            ]
            if isinstance(facts, list)
            else []
        )
        if len(matches) != 1 or any(not isinstance(matches[0].get(key), str) or not re.fullmatch(expected[key], matches[0][key].strip(), re.IGNORECASE) for key in ("expected", "actual")):
            errors.append("Missing or incorrect directed fact: " + expected["requirement"])
        if not any(
            item
            and len(matches) == 1
            and item[1] == matches[0]["requirement"]
            and re.fullmatch(expected["expected"], item[2], re.IGNORECASE)
            and re.fullmatch(expected["actual"], item[3], re.IGNORECASE)
            and {key.strip() for key in item[4].split(",")} == set(expected["sections"])
            for item in directed
        ):
            errors.append("Explanation omits the directed expected/actual values: " + expected["requirement"])
    return errors


async def run_case(ai, actor, corpus, case):
    base = corpus["documents"][case["document"]]
    if case["kind"] == "chat":
        document, message = chat_input(base, case)
        try:
            assert_context(context(message["context"], document["id"]), document["sections"])
        except SpecificationError as error:
            if error.code == case.get("expect_error"):
                return {"phase": "context_guard", "errors": [], "error_code": error.code}
            raise
        if case.get("expect_error"):
            return {"phase": "context_guard", "errors": ["Invalid context reached the model boundary"]}
        response = await ai.chat(actor, document, message)
        return {"phase": "model", "response": response, "errors": score_chat(case, document, message, response)}
    if case["kind"] == "verify":
        before, candidate = changed(base, case.get("before_changes")), changed(base, case.get("candidate_changes"))
        merge = case.get("merge")
        if merge:
            merge = {"base": base, "remote": changed(base, merge["remote_changes"]), "choice": merge["choice"]}
        response = await ai.verify(actor, before, candidate, **({"merge": merge} if merge else {}))
        return {
            "phase": "model",
            "response": response,
            "errors": score_verification(response, valid=case["valid"], issues_all=case.get("issues_all", []), expected_facts=case.get("expected_facts", [])),
        }
    local, remote = changed(base, case["local_changes"]), changed(base, case["remote_changes"])
    candidate = await ai.resolve_merge(actor, base, local, remote, case["choice"])
    errors = score_content(candidate, changed(base, case["expected_changes"]))
    verification = await ai.verify(actor, local, candidate, merge={"base": base, "remote": remote, "choice": case["choice"]})
    errors += score_verification(verification, valid=True)
    return {"phase": "model", "sections": candidate, "verification": verification, "errors": errors}


async def run_suite(ai, actor, corpus, emit, case_ids=None):
    validate_corpus(corpus)
    if case_ids is not None and (not case_ids or len(set(case_ids)) != len(case_ids) or set(case_ids) - _CASE_IDS):
        raise ValueError("Invalid diagnostic case selection")
    results = []
    for case in corpus["cases"]:
        if case_ids is not None and case["id"] not in case_ids:
            continue
        started = time.monotonic()
        emit({"event": "case_started", "id": case["id"], "document": case["document"], "kind": case["kind"]})
        try:
            result = await run_case(ai, actor, corpus, case)
            result.update(id=case["id"], status="FAIL" if result["errors"] else "PASS")
        except Exception as error:  # noqa: BLE001 - isolate scenarios; every exception fails the report.
            result = {"id": case["id"], "status": "ERROR", "error_type": type(error).__name__, "error_code": getattr(error, "code", None)}
        result["seconds"] = round(time.monotonic() - started, 2)
        results.append(result)
        emit({"event": "case_finished", **result})
    return {"status": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL", "diagnostic": case_ids is not None, "cases": results}
