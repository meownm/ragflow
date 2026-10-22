"""Replays and admission/thinking experiments; none replace full qualification."""

import asyncio
import re
from types import SimpleNamespace
from uuid import uuid4

import aiohttp

from api.technical_specifications.alignment import failure_result, fingerprint, materialize_report, report_input, strict_json, validate_report
from api.technical_specifications.alignment_ai import SYSTEM, AlignmentAI, _ollama, ollama_request
from api.technical_specifications.alignment_budget import AlignmentBudget, plan_request
from api.technical_specifications.domain import SpecificationError
from test.evals.technical_specifications.alignment.corpus import CASES, inputs
from test.evals.technical_specifications.alignment.evaluate import case_observation, run_cases

KINDS = ("REPEATS", "ADMISSION", "REASONING")


def experiment_case_status(kind, item):
    """Recompute observations for saved nightly consumers, never trust PASS alone."""
    result = item.get("result", {})
    if not isinstance(result, dict):
        return "INCOMPLETE"
    audit = result.get("audit", {})
    if isinstance(audit, dict) and audit.get("input_integrity") == "TRUNCATED":
        return "INCOMPLETE"
    identifier = item.get("case_id", "")
    if kind == "ADMISSION" and identifier == "ADMISSION-CONTEXT-LOW":
        return (
            "PASS"
            if (
                item.get("evidence_kind") == "DETERMINISTIC" and type(item.get("inference_calls")) is int and item["inference_calls"] == 0 and result.get("reason_code") == "ALIGNMENT_CONTEXT_EXCEEDED"
            )
            else "INCOMPLETE"
        )
    if kind == "REASONING" and identifier == "REASONING-OUTPUT-LOW":
        audit = result.get("audit", {})
        options = audit.get("generation_parameters", {})
        try:
            document, basis = inputs(next(case for case in CASES if case[0] == "BTZ-28"))
            plan = {"context_limit": options["num_ctx"], "output_limit": 8, "think": audit["thinking_requested"]}
            wire = ollama_request(audit["model"], SYSTEM, report_input(document, basis), plan)
            valid = (
                audit.get("provider") == "Ollama"
                and audit.get("wire_sha256") == fingerprint(wire)
                and audit.get("evidence_kind") == "CONTROLLED_FAULT_REAL_MODEL"
                and (plan["think"] is True or isinstance(plan["think"], str) and bool(plan["think"]))
                and options == wire["options"]
                and type(options["num_ctx"]) is int
                and options["num_ctx"] >= 2048
                and audit.get("backend_completion_known") is True
                and audit.get("finish_reason") == "length"
                and audit.get("thinking_observed") is True
                and audit.get("model_digest_after") == audit.get("model_digest")
                and isinstance(audit.get("model_digest"), str)
                and re.fullmatch(r"[a-f0-9]{64}", audit["model_digest"])
                and audit.get("response_model") == audit["model"]
                and type(audit.get("completion_tokens")) is int
                and audit["completion_tokens"] == 8
                and type(audit.get("prompt_tokens")) is int
                and 0 < audit["prompt_tokens"] <= options["num_ctx"] - 8
            )
        except (AttributeError, KeyError, TypeError, ValueError, SpecificationError):
            valid = False
        return "PASS" if valid else "INCOMPLETE"
    key = identifier.rsplit("/", 1)[-1] if kind == "REPEATS" else "BTZ-02" if kind == "ADMISSION" else "BTZ-28"
    case = next((case for case in CASES if case[0] == key), None)
    if case is None:
        return "INCOMPLETE"
    if result.get("report_status") == "COMPLETE":
        try:
            document, basis = inputs(case)
            checked = validate_report(result.get("report"), document, basis)
        except (SpecificationError, KeyError, TypeError, ValueError):
            return "FAIL"
        status = case_observation(case, checked)["status"]
        if status == "PASS" and (item.get("status") != "PASS" or kind == "REASONING" and audit.get("thinking_observed") is not True):
            return "INCOMPLETE"
        return status
    return case_observation(case, result)["status"]


def experiment_case_ids(kind):
    if kind == "REPEATS":
        return [f"REPEAT-{index}/{case[0]}" for index in (1, 2) for case in CASES]
    if kind == "ADMISSION":
        return ["ADMISSION-CONTEXT-LOW", "ADMISSION-CONTEXT-EXACT", "ADMISSION-CONTEXT-HIGH"]
    if kind == "REASONING":
        return ["REASONING-OUTPUT-LOW", "REASONING-OUTPUT-SUFFICIENT"]
    raise ValueError("Unknown alignment experiment")


def repeat_summary(cases, expected_ids, count, *, comparable=True):
    """Unknown or absent attempts retain the fixed denominator for every case."""
    rows = []
    for identifier in expected_ids:
        attempts = [item for item in cases if item.get("natural_case_id") == identifier]
        statuses = [item["status"] for item in attempts]
        complete = comparable and len(attempts) == count and all(status in ("PASS", "FAIL") for status in statuses)
        signatures = [fingerprint(item["result"]["report"]) for item in attempts if item["result"].get("report_status") == "COMPLETE"]
        # Answer wording may vary without changing semantic labels. Both are visible.
        semantic = [
            fingerprint(
                {
                    "requirements": [(row["coverage"], row["consistency"]) for row in item["result"]["report"]["requirements"]],
                    "joint": item["result"]["report"]["joint"]["consistency"],
                    "additional_rules": sorted(row["kind"] for row in item["result"]["report"]["additional_rules"]),
                }
            )
            for item in attempts
            if item["result"].get("report_status") == "COMPLETE"
        ]
        reports_complete = complete and len(signatures) == count
        rows.append(
            {
                "case_id": identifier,
                "expected_attempts": count,
                "executed_attempts": len(attempts),
                "passed": statuses.count("PASS"),
                "failed": statuses.count("FAIL"),
                "unknown": statuses.count("INCOMPLETE"),
                "unexecuted": count - len(attempts),
                "failure_rate": statuses.count("FAIL") / count if complete else None,
                "label_variants": len(set(semantic)) if reports_complete else None,
                "answer_variants": len(set(signatures)) if reports_complete else None,
            }
        )
    return rows


def _finish(report, budget):
    complete = [item["case_id"] for item in report["cases"]] == report["expected_case_ids"]
    statuses = {item["status"] for item in report["cases"]}
    quality = "FAIL" if "FAIL" in statuses else "PASS" if complete and statuses == {"PASS"} else "UNKNOWN"
    report.update(quality_status=quality, status="FAIL" if quality == "FAIL" else "INCOMPLETE", evidence_status="INCOMPLETE", qualifies=False, budget=budget.snapshot())
    return report


def _report(kind, expected):
    return {
        "schema_version": 1,
        "document_type": "technical_specification",
        "suite_id": "bt-tz-alignment-experiment-v1",
        "scope": "EXPERIMENT",
        "experiment": kind,
        "expected_case_ids": expected,
        "cases": [],
        "status": "INCOMPLETE",
        "quality_status": "UNKNOWN",
        "evidence_status": "INCOMPLETE",
        "qualifies": False,
        "corpus_sha256": fingerprint(CASES),
        "prompt_sha256": fingerprint(SYSTEM),
    }


async def _repeats(ai, actor, checkpoint):
    # Both full replays share one ledger; unknown usage is never erased.
    count = 2
    budget = AlignmentBudget(seconds=len(CASES) * count * 300, calls=len(CASES) * count, token_limit=4_000_000)
    report = _report("REPEATS", experiment_case_ids("REPEATS"))
    identities, request_ids = set(), set()
    for index in range(1, count + 1):
        prior = list(report["cases"])

        def save(partial, *, prior=prior, index=index):
            report["cases"] = prior + [{**item, "natural_case_id": item["case_id"], "repeat": index, "case_id": f"REPEAT-{index}/{item['case_id']}"} for item in partial["cases"]]
            report.update(budget=budget.snapshot(), active_case_id=f"REPEAT-{index}/{partial['active_case_id']}" if partial.get("active_case_id") else None)
            if partial.get("active_audit"):
                report["active_audit"] = partial["active_audit"]
            else:
                report.pop("active_audit", None)
            if checkpoint:
                checkpoint(report)

        expected_identity = next(iter(identities))[1:] if identities else None
        partial = await run_cases(ai, actor, budget=budget, checkpoint=save, expected_identity=expected_identity)
        for item in partial["cases"]:
            audit = item["result"].get("audit", {})
            identities.add((audit.get("model"), audit.get("model_digest"), audit.get("profile_sha256")))
            identifier = audit.get("request_id")
            if not identifier or identifier in request_ids:
                report["reason_code"] = "REPLAY_REQUEST_ID_INVALID"
            request_ids.add(identifier)
        if len(partial["cases"]) != len(CASES) or budget.reserved_tokens or report.get("reason_code"):
            report.setdefault("reason_code", "REPLAY_INTERRUPTED_WITH_RETAINED_DENOMINATOR")
            break
        if len(identities) != 1 or any(not part for identity in identities for part in identity):
            report["reason_code"] = "REPLAY_PROFILE_CHANGED"
            break
    report["comparison_status"] = "INCOMPLETE" if report.get("reason_code") else "COMPLETE"
    report["repeat_summary"] = repeat_summary(report["cases"], [case[0] for case in CASES], count, comparable=not report.get("reason_code"))
    return _finish(report, budget)


def admission_context(model, document, basis):
    """Lowest admitted application context for the unchanged full serialized input.

    This is an application estimate boundary, not an exact backend token count.
    """
    payload = report_input(document, basis)

    def at(context):
        return SimpleNamespace(context_tokens=context, output_tokens=model.output_tokens, count_tokens=model.count_tokens, model_config=model.model_config)

    low, high = 2048, model.context_tokens
    plan_request(SYSTEM, payload, at(high))
    while low < high:
        middle = (low + high) // 2
        try:
            plan_request(SYSTEM, payload, at(middle))
        except SpecificationError as error:
            if error.code != "ALIGNMENT_CONTEXT_EXCEEDED":
                raise
            low = middle + 1
        else:
            high = middle
    return low, at


async def _admission(ai, actor, checkpoint):
    case = next(case for case in CASES if case[0] == "BTZ-02")
    document, basis = inputs(case)
    report = _report("ADMISSION", experiment_case_ids("ADMISSION"))
    budget = AlignmentBudget(seconds=630, calls=2, token_limit=1_000_000)
    async with asyncio.timeout(min(15, budget.remaining())):
        model = await ai.open_model(actor)
    boundary, at = admission_context(model, document, basis)
    report.update(application_context_boundary=boundary, tokenization="CONSERVATIVE_ESTIMATE", backend_token_boundary="UNVERIFIED")
    for identifier, delta in zip(report["expected_case_ids"], (-1, 0, 1), strict=True):
        if boundary + delta < 2048 or boundary + delta > model.context_tokens:
            report["reason_code"] = "BOUNDARY_OUTSIDE_SUPPORTED_CONTEXT"
            break

        async def opened(_actor, context=boundary + delta):
            return at(context)

        def reserved(audit, identifier=identifier):
            report.update(active_case_id=identifier, active_audit=audit, budget=budget.snapshot())
            if checkpoint:
                checkpoint(report)

        before = budget.spent_calls
        result = await AlignmentAI(opened).check(actor, document, basis, budget=budget, on_reserved=reserved)
        item = case_observation(case, result)
        item.update(
            case_id=identifier, context_delta=delta, context_limit=boundary + delta, inference_calls=budget.spent_calls - before, evidence_kind="DETERMINISTIC" if delta == -1 else "NATURAL_MODEL"
        )
        if delta == -1:
            item.update(
                status="PASS" if result.get("reason_code") == "ALIGNMENT_CONTEXT_EXCEEDED" and budget.spent_calls == before else "FAIL",
                errors=[] if result.get("reason_code") == "ALIGNMENT_CONTEXT_EXCEEDED" and budget.spent_calls == before else ["ADMISSION_GUARD_FAILED"],
            )
        report["cases"].append(item)
        report.update(active_case_id=None)
        report.pop("active_audit", None)
        if checkpoint:
            checkpoint(report)
        if budget.reserved_tokens:
            report["reason_code"] = "BACKEND_USAGE_UNKNOWN"
            break
    return _finish(report, budget)


async def _reasoning(ai, actor, checkpoint):
    case = next(case for case in CASES if case[0] == "BTZ-28")
    document, basis = inputs(case)
    payload = report_input(document, basis)
    report = _report("REASONING", experiment_case_ids("REASONING"))
    budget = AlignmentBudget(seconds=360, calls=2, token_limit=200_000)
    async with asyncio.timeout(min(15, budget.remaining())):
        model = await ai.open_model(actor)
    config = model.model_config
    if config.get("llm_factory") != "Ollama":
        report["reason_code"] = "THINKING_PROFILE_UNSUPPORTED"
        return _finish(report, budget)
    base = config["api_base"].rstrip("/").removesuffix("/v1")
    headers = {"Authorization": f"Bearer {config['api_key']}"} if config.get("api_key") else {}
    async with (
        aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=min(15, budget.remaining())), headers=headers) as session,
        session.post(base + "/api/show", json={"model": config["llm_name"].split("___")[0]}) as response,
    ):
        response.raise_for_status()
        capabilities = await response.json()
    report["thinking_metadata_sha256"] = fingerprint(capabilities)
    controls = capabilities.get("thinking", {}).get("values", []) if isinstance(capabilities.get("thinking"), dict) else []
    think = True if any(value is True for value in controls) else next((value for value in controls if isinstance(value, str) and value), None)
    if not controls and "thinking" in capabilities.get("capabilities", []):
        think = True
    report["thinking_control_evidence"] = "MODEL_METADATA" if controls else "CAPABILITY_ONLY"
    if think is None:
        report["reason_code"] = "THINKING_CAPABILITY_UNAVAILABLE"
        return _finish(report, budget)
    identity = None
    for identifier, output in zip(report["expected_case_ids"], (8, 8192), strict=True):
        plan = {"context_limit": min(model.context_tokens, 32768), "output_limit": output, "think": think}
        request_id = uuid4().hex
        claim = budget.claim(plan["context_limit"])
        report.update(active_case_id=identifier, active_audit={"request_id": request_id, "backend_completion_known": False}, budget=budget.snapshot())
        if checkpoint:
            checkpoint(report)

        def prepared(audit, identifier=identifier):
            report.update(active_case_id=identifier, active_audit=audit, budget=budget.snapshot())
            if checkpoint:
                checkpoint(report)

        try:
            async with asyncio.timeout(min(150, budget.remaining())):
                raw, audit = await _ollama(config, SYSTEM, payload, plan, budget, request_id=request_id, on_request=prepared)
            audit.update(evidence_kind="CONTROLLED_FAULT_REAL_MODEL" if output == 8 else "NATURAL_MODEL", backend_completion_known=audit.get("finish_reason") in {"stop", "length"})
            if audit["backend_completion_known"]:
                budget.settle(audit.get("prompt_tokens"), audit.get("completion_tokens"), claim_id=claim)
            current = (audit.get("model"), audit.get("model_digest"))
            wire = ollama_request(config["llm_name"].split("___")[0], SYSTEM, payload, plan)
            valid = (
                audit.get("wire_sha256") == fingerprint(wire)
                and audit.get("generation_parameters") == wire["options"]
                and audit.get("input_integrity") != "TRUNCATED"
                and audit.get("provider") == "Ollama"
                and audit.get("request_id") == request_id
                and audit.get("response_model") == current[0]
                and audit.get("model_digest_after") == current[1]
                and bool(current[1])
                and isinstance(current[1], str)
                and re.fullmatch(r"[a-f0-9]{64}", current[1])
                and (identity is None or identity == current)
                and audit.get("thinking_requested") == think
                and type(audit.get("completion_tokens")) is int
                and 0 < audit["completion_tokens"] <= output
                and type(audit.get("prompt_tokens")) is int
                and audit["prompt_tokens"] > 0
                and audit["prompt_tokens"] + audit["completion_tokens"] <= plan["context_limit"]
            )
            identity = current
            result = {**failure_result("MODEL_EVIDENCE_INCOMPLETE"), "audit": audit}
            if valid and output == 8192 and audit.get("finish_reason") == "stop":
                try:
                    model_report = strict_json(raw)
                    result = {
                        **validate_report(materialize_report(model_report, payload), document, basis),
                        "model_report": model_report,
                        "audit": audit,
                        "qualifies": False,
                        "evidence_status": "INCOMPLETE",
                    }
                except SpecificationError:
                    result = {**failure_result("ALIGNMENT_REPORT_INVALID", completed=True), "audit": audit}
            item = case_observation(case, result)
            if output == 8:
                observed = valid and experiment_case_status("REASONING", {"case_id": identifier, "result": result}) == "PASS"
                item.update(status="PASS" if observed else "INCOMPLETE", errors=[] if observed else ["THINKING_PRESSURE_NOT_OBSERVED"])
            if not valid:
                item.update(status="INCOMPLETE", errors=["MODEL_INPUT_TRUNCATED" if audit.get("input_integrity") == "TRUNCATED" else "THINKING_WIRE_OR_IDENTITY_INVALID"])
            elif audit.get("thinking_observed") is not True:
                item.update(status="INCOMPLETE", errors=["THINKING_NOT_OBSERVED"])
        except (SpecificationError, TimeoutError, aiohttp.ClientError) as error:
            item = {
                "case_id": identifier,
                "status": "INCOMPLETE",
                "errors": [error.code if isinstance(error, SpecificationError) else "THINKING_TRANSPORT_INCOMPLETE"],
                "result": {**failure_result("MODEL_EVIDENCE_INCOMPLETE"), "audit": report.get("active_audit", {"request_id": request_id})},
            }
        item.update(case_id=identifier)
        report["cases"].append(item)
        report.update(active_case_id=None)
        report.pop("active_audit", None)
        if checkpoint:
            checkpoint(report)
        if budget.reserved_tokens or budget.spent_tokens > budget.token_limit or item["errors"] in (["THINKING_WIRE_OR_IDENTITY_INVALID"], ["MODEL_INPUT_TRUNCATED"]):
            report["reason_code"] = "THINKING_INTERRUPTED"
            break
    report["reasoning_token_accounting"] = "NOT_ATTESTED"
    return _finish(report, budget)


async def run_experiment(ai, actor, kind, *, checkpoint=None):
    if kind not in KINDS:
        raise ValueError("Unknown alignment experiment")
    return await {"REPEATS": _repeats, "ADMISSION": _admission, "REASONING": _reasoning}[kind](ai, actor, checkpoint)
