"""Real-prompt metric relocation with an independent, clause-level oracle."""

from copy import deepcopy
import hashlib
import json
import re
import unicodedata
from uuid import uuid4

from json_repair import repair_json

from business_documents.domain.content import apply_change_plan, render_section_text, validate_contract
from business_documents.domain.errors import RuleViolation
from business_documents.domain.job_recovery import MAX_AI_CALLS
from business_documents.domain.requirement_coverage import changed_section_repetitions, validate_requirement_coverage


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RecordedModel:
    """Keep the exact model exchanges; never supply a plan or a verdict."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.calls = []

    def generate(self, tenant_id, system_prompt, input_payload, *, timeout_seconds=None):
        call = {
            "task_type": input_payload["job_input"]["task_type"],
            "system_prompt": system_prompt,
            "input_payload": deepcopy(input_payload),
            "system_sha256": _hash(system_prompt),
            "input_sha256": _hash(_json(input_payload)),
        }
        self.calls.append(call)
        try:
            response = self.adapter.generate(tenant_id, system_prompt, input_payload, timeout_seconds=timeout_seconds)
        except Exception as error:
            from api.apps.business_documents.recovery import is_transient_failure

            call["error_type"] = type(error).__name__
            call["retryable"] = is_transient_failure(error)
            raise
        call["response"] = response
        call["response_sha256"] = _hash(response if isinstance(response, str) else _json(response))
        return response

    def consume_execution_audit(self):
        return self.adapter.consume_execution_audit()


def _text(value):
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(normalized.split())


def placement_failures(case, document_ast):
    """Check each definition in its own table row, independently of the LLM judge."""
    sections = {section["id"]: section for section in document_ast["sections"]}
    failures = []
    for requirement in case["seed"]["requirements"]:
        section = sections.get(requirement["section_id"])
        if section is None:
            failures.append(f"missing_section:{requirement['section_id']}")
            continue
        if requirement.get("label_pattern"):
            matches = [
                " ".join(str(cell) for cell in row)
                for block in section["blocks"]
                if block.get("type") == "table"
                for row in block["rows"]
                if re.fullmatch(requirement["label_pattern"], _text(str(row[0])))
            ]
            if len(matches) != 1:
                failures.append(f"metric_row_count:{requirement['id']}:{len(matches)}")
                continue
            text = _text(matches[0])
            for other_id, other in sections.items():
                if other_id != requirement["section_id"] and re.search(requirement["label_pattern"], _text(render_section_text(other))):
                    failures.append(f"misplaced_metric:{requirement['id']}:{other_id}")
        else:
            text = _text(render_section_text(section))
        for check in requirement["checks"]:
            if not re.search(check["pattern"], text):
                failures.append(f"requirement:{requirement['id']}:{check['id']}")
    return list(dict.fromkeys(failures))


def _command(document, kind, payload=None):
    return {
        "schema_version": "1",
        "command_id": uuid4().hex,
        "idempotency_key": uuid4().hex,
        "expected_state_version": document["state_version"],
        "type": kind,
        "payload": payload or {},
    }


def _execute(tenant, document, kind, payload=None):
    from api.apps.business_documents.runtime import document_commands

    return document_commands.execute(tenant, tenant, document["document_id"], _command(document, kind, payload))


def _seed_completion(tenant, document, kind, output):
    """Persist the fixed starting protocol; target change generation is never seeded."""
    from api.apps.business_documents.runtime import job_completion
    from api.apps.business_documents.worker import BusinessDocumentJobQueue

    requested = _execute(tenant, document, kind)
    job = BusinessDocumentJobQueue.claim("metric-placement-seed")
    assert job is not None and job.id == requested["job_id"]
    return job_completion.complete(tenant, "metric-placement-seed", job.id, output, job.lease_token)


def prepare_metric_placement(case, tenant):
    from api.apps.business_documents.runtime import document_creation, document_queries
    from api.db.db_models import BusinessDocumentEvent

    seed = case["seed"]
    document = document_creation.execute(
        tenant,
        tenant,
        {
            "schema_version": "1",
            "document_type": "business_requirements",
            "title": case["title"],
            "idea": case["idea"],
        },
    )
    document = _seed_completion(tenant, document, "REQUEST_INTAKE_ASSESSMENT", {"schema_version": "1", "outcome": "COMPLETE", "questions": []})
    created = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "DocumentCreated"))
    document = _seed_completion(
        tenant,
        document,
        "REQUEST_DRAFT",
        {
            "draft": deepcopy(seed["base_document"]),
            "review_questions": {
                "schema_version": "1",
                "outcome": "NEEDS_INPUT",
                "questions": [
                    {
                        "semantic_tag": "metrics_reporting_placement",
                        "stage": "REVIEW",
                        "target_section_id": seed["question_section_id"],
                        "text": seed["question"],
                        "options": seed["options"],
                        "allow_custom_answer": True,
                    }
                ],
            },
            "proposals": [
                {
                    "target_section_id": seed["proposal_section_id"],
                    "text": "Мониторинг включает:\n" + "\n".join(r["text"] for r in seed["requirements"]),
                    "rationale": "Подтверждённые определения метрик и независимый мониторинг.",
                    "source_event_ids": [created.id],
                }
            ],
        },
    )
    _execute(tenant, document, "DECIDE_PROPOSAL", {"proposal_id": document["protocol"]["proposals"][0]["proposal_id"], "decision": "ACCEPTED"})
    document = document_queries.get_document(document["document_id"], tenant)
    _execute(
        tenant,
        document,
        "ANSWER_QUESTION",
        {
            "question_id": document["protocol"]["questions"][0]["question_id"],
            "selected_option_id": seed["selected_option_id"],
            "custom_answer": None,
        },
    )
    document = document_queries.get_document(document["document_id"], tenant)
    document = _seed_completion(tenant, document, "REQUEST_REVIEW_ASSESSMENT", {"schema_version": "1", "questions": [], "proposals": [], "comment_dispositions": []})
    assert not document["protocol"]["comments"]
    return document


def run_metric_placement(case, tenant, adapter):
    from api.apps.business_documents.ai import BusinessDocumentAI
    from api.apps.business_documents.assets import prompt_text
    from api.apps.business_documents.assets import contract_schema, published_template
    from api.apps.business_documents.runtime import document_commands, document_queries
    from api.apps.business_documents.worker import BusinessDocumentWorker
    from api.db.db_models import BusinessDocumentJob

    document = prepare_metric_placement(case, tenant)
    base = deepcopy(document["current_revision"])
    proposal = document["protocol"]["proposals"][0]["decision_event_id"]
    answer = document["protocol"]["questions"][0]["answer"]["source_event_id"]
    requested = _execute(tenant, document, "PREPARE_CHANGES", {"base_revision_id": base["revision_id"]})
    recorded = RecordedModel(adapter)
    worker = BusinessDocumentWorker(worker_id="metric-placement-model", ai=BusinessDocumentAI(recorded), retry_base_ms=0)
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    for _ in range(job.max_attempts):
        assert worker.run_once()
        job = BusinessDocumentJob.get_by_id(job.id)
        if job.status in {"COMPLETED", "DEAD"}:
            break
    result = {
        "case_id": case["id"],
        "priority": case["priority"],
        "status": "FAIL",
        "failures": [],
        "metrics": {},
        "model_calls": recorded.calls,
        "fixture_sha256": _hash(_json(case)),
        "attempts": job.attempt,
        "expected_sources": {"5": [answer], "5.5": [proposal]},
    }
    if job.status != "COMPLETED":
        code = (job.error or {}).get("code", "UNFINISHED_JOB")
        result["failures"] = [f"job:{code}"]
        if code not in {"REQUIREMENT_COVERAGE_FAILED", "INVALID_CHANGE_PLAN", "INVALID_DOCUMENT_DRAFT", "DUPLICATE_REQUIREMENT_CONTENT"}:
            result["status"] = "INCOMPLETE"
        return result, []
    failures = result["failures"]
    output = job.result["output"]
    document = document_queries.get_document(document["document_id"], tenant)
    if document["current_revision"] != base:
        failures.append("preview_mutated_revision")
    confirm = _command(document, "CONFIRM_PREPARED_CHANGES", {"job_id": job.id})
    final = document_commands.execute(tenant, tenant, document["document_id"], confirm)["document"]
    ast = final["current_revision"]["document_ast"]
    if final["current_revision"]["revision_number"] != base["revision_number"] + 1:
        failures.append("revision_count")
    if proposal not in final["current_revision"]["source_event_ids"]:
        failures.append("proposal_not_applied")
    if not document_commands.execute(tenant, tenant, document["document_id"], confirm).get("idempotent_replay"):
        failures.append("confirmation_not_idempotent")
    result.update(final_ast=ast, change_plan=output["change_plan"], requirement_ids=[r["id"] for r in case["seed"]["requirements"]])
    evidence = {key: value for key, value in result.items() if key not in {"case_id", "priority", "status", "failures", "metrics"}}
    failures.extend(
        evidence_failures(
            case, evidence, {name: prompt_text(name) for name in ("change_planner", "requirement_coverage")}, published_template(), contract_schema("document_draft_v2"), contract_schema("change_plan")
        )
    )
    result["status"] = "FAIL" if failures else "PASS"
    return result, [job.result["execution"]["ai"]]


def _response(call):
    raw = call.get("response")
    return deepcopy(raw) if isinstance(raw, dict) else repair_json(raw, return_objects=True) if isinstance(raw, str) else None


def _verification_evidence_failures(calls, job_input, sources, titles):
    """Replay one verification stage, including its single format repair."""
    failures, feedback, passed = [], None, False
    for call in calls:
        if passed:
            failures.append("unexpected_model_sequence")
        expected = {"job_input": job_input, **({"format_repair": feedback} if feedback is not None else {})}
        if call.get("input_payload") != expected:
            failures.append("verification_input_mismatch")
        if "error_type" in call:
            if call.get("retryable") is not True:
                failures.append("nonretryable_model_error")
            continue
        raw = call.get("response")
        try:
            if not isinstance(raw, dict) and (not isinstance(raw, str) or not raw.strip()):
                raise RuleViolation("EMPTY_AI_RESPONSE", "AI response is empty")
            try:
                verdict = _response(call)
            except Exception as error:
                raise RuleViolation("INVALID_AI_JSON", "AI response is not valid JSON") from error
            if not isinstance(verdict, dict):
                raise RuleViolation("INVALID_AI_JSON", "AI response must be a JSON object")
            validate_requirement_coverage(verdict, sources, titles)
        except RuleViolation as error:
            if feedback is not None or error.code not in {"EMPTY_AI_RESPONSE", "INVALID_AI_JSON", "INVALID_REQUIREMENT_COVERAGE"}:
                failures.append("invalid_final_verdict")
            else:
                feedback = {"previous_response": raw, "error": {"code": error.code, "message": error.message[:800], "details": {}}}
        else:
            passed = True
    if not passed:
        failures.append("invalid_final_verdict")
    return failures


def evidence_failures(case, evidence, prompts, template, schema, change_schema):
    """Fail closed when a saved model-quality report loses its replayable proof."""
    if not isinstance(evidence, dict) or evidence.get("fixture_sha256") != _hash(_json(case)):
        return ["fixture_identity"]
    required = {"fixture_sha256", "model_calls", "expected_sources", "final_ast", "change_plan", "requirement_ids", "attempts"}
    if set(evidence) != required:
        return ["incomplete_placement_evidence"]
    if not isinstance(evidence["model_calls"], list) or not 2 <= len(evidence["model_calls"]) <= MAX_AI_CALLS:
        return ["invalid_model_call_budget"]
    failures = placement_failures(case, evidence["final_ast"])
    if evidence["requirement_ids"] != [r["id"] for r in case["seed"]["requirements"]]:
        failures.append("incomplete_requirement_inventory")
    sources = evidence["expected_sources"]
    if set(sources) != {"5", "5.5"} or any(not isinstance(v, list) or len(v) != 1 or not isinstance(v[0], str) for v in sources.values()) or sources["5"] == sources["5.5"]:
        return [*failures, "invalid_source_inventory"]
    plan = evidence["change_plan"]
    if plan["acknowledged_no_change_event_ids"]:
        failures.append("unapplied_confirmed_source")
    if len(plan["operations"]) != 2 or {o["section_id"] for o in plan["operations"]} != set(sources):
        failures.append("unexpected_operations")
    # The inventory retains original locations for replay, not section limits.
    allowed_sources = set(sources["5"] + sources["5.5"])
    used_sources = set()
    for operation in plan["operations"]:
        operation_sources = set(operation["source_event_ids"])
        if not operation_sources or len(operation_sources) != len(operation["source_event_ids"]) or not operation_sources <= allowed_sources:
            failures.append("invalid_change_source")
        used_sources.update(operation_sources)
    if used_sources != allowed_sources:
        failures.append("unapplied_confirmed_source")
    try:
        validate_contract("change_plan", plan, change_schema)
        replayed = apply_change_plan(case["seed"]["base_document"], plan, template, schema)
    except RuleViolation:
        failures.append("invalid_saved_plan")
    else:
        if replayed != evidence["final_ast"]:
            failures.append("plan_ast_mismatch")
    final = {s["id"]: s for s in evidence["final_ast"]["sections"]}
    base = case["seed"]["base_document"]["sections"]
    base_ids = {section["id"] for section in base}
    if set(final) != base_ids | {"5.4"}:
        failures.append("section_inventory")
    for section in base:
        if section["id"] != "5.5" and final.get(section["id"]) != section:
            failures.append(f"unrelated_section_changed:{section['id']}")
    last_plan, verification_calls = None, []
    for call in evidence["model_calls"]:
        fields = {"task_type", "system_prompt", "input_payload", "system_sha256", "input_sha256"}
        fields.update({"error_type", "retryable"} if "error_type" in call else {"response", "response_sha256"})
        if set(call) != fields:
            failures.append("invalid_model_exchange")
        task = call["task_type"]
        if task == "PLAN_CHANGES":
            if verification_calls:
                failures.append("unexpected_model_sequence")
            last_plan = call
            if "error_type" in call and call.get("retryable") is not True:
                failures.append("nonretryable_model_error")
        elif task == "VERIFY_REQUIREMENT_COVERAGE":
            if last_plan is None or "error_type" in last_plan:
                failures.append("unexpected_model_sequence")
            verification_calls.append(call)
        name = "change_planner" if task == "PLAN_CHANGES" else "requirement_coverage" if task == "VERIFY_REQUIREMENT_COVERAGE" else None
        if name is None or not call["system_prompt"].startswith(prompts[name].split("# Вход")[0]):
            failures.append("unexpected_prompt")
        if call["system_sha256"] != _hash(call["system_prompt"]) or call["input_sha256"] != _hash(_json(call["input_payload"])):
            failures.append("model_input_hash")
        if "error_type" not in call and call["response_sha256"] != _hash(call["response"] if isinstance(call["response"], str) else _json(call["response"])):
            failures.append("model_response_hash")
    if last_plan is None or not verification_calls or "error_type" in last_plan or "error_type" in verification_calls[-1]:
        failures.append("missing_real_model_exchanges")
    else:
        inputs = last_plan["input_payload"]["job_input"]
        protocol = inputs.get("protocol") or {}
        answers = [
            q["answer"]["source_event_id"]
            for q in protocol.get("questions", [])
            if q.get("target_section_id") == "5" and (q.get("answer") or {}).get("selected_option_id") == case["seed"]["selected_option_id"]
        ]
        proposals = [p["decision_event_id"] for p in protocol.get("proposals", []) if p.get("target_section_id") == "5.5" and p.get("decision") == "ACCEPTED"]
        if answers != sources["5"] or proposals != sources["5.5"] or protocol.get("comments") or set(inputs.get("active_change_input_event_ids", [])) != set(answers + proposals):
            failures.append("source_input_mismatch")
        expected_sources = {
            sources["5"][0]: {"text": next(option["label"] for option in case["seed"]["options"] if option["option_id"] == case["seed"]["selected_option_id"]), "context": case["seed"]["question"]},
            sources["5.5"][0]: {"text": "Мониторинг включает:\n" + "\n".join(requirement["text"] for requirement in case["seed"]["requirements"])},
        }
        expected_fragments = {
            f"{source_id}:{index}": {"source_id": source_id, "text": text.strip(), **({"context": source["context"]} if "context" in source else {})}
            for source_id, source in expected_sources.items()
            for index, text in enumerate(re.split(r"(?<=[.!?;])\s+|\n+", source["text"]))
            if text.strip()
        }
        if inputs.get("confirmed_requirement_fragments") != expected_fragments or inputs.get("current_revision", {}).get("document_ast") != case["seed"]["base_document"]:
            failures.append("source_input_mismatch")
        generated = _response(last_plan)
        if isinstance(generated, dict) and "change_plan" in generated:
            nested = generated["change_plan"]
            generated = nested if "operations" not in generated else {key: value for key, value in generated.items() if key != "change_plan"}
            if isinstance(generated, dict) and "schema_version" not in generated and isinstance(nested, dict) and {"$schema", "properties", "type"} <= nested.keys():
                generated = {"schema_version": nested.get("properties", {}).get("schema_version", {}).get("const"), **generated}
        if isinstance(generated, dict):
            if generated.get("schema_version") == 1:
                generated["schema_version"] = "1"
            aliases = {q["question_id"]: q["answer"]["source_event_id"] for q in protocol.get("questions", []) if q.get("question_id") and (q.get("answer") or {}).get("source_event_id")}
            aliases.update({p["proposal_id"]: p["decision_event_id"] for p in protocol.get("proposals", []) if p.get("proposal_id") and p.get("decision_event_id")})
            hashes = {operation["section_id"]: operation["expected_section_hash"] for operation in plan["operations"]}
            for operation in generated.get("operations", []) if isinstance(generated.get("operations"), list) else []:
                if isinstance(operation, dict) and isinstance(operation.get("section_id"), str):
                    if operation["section_id"] in hashes:
                        operation["expected_section_hash"] = hashes[operation["section_id"]]
                    if isinstance(operation.get("source_event_ids"), list):
                        operation["source_event_ids"] = [aliases.get(value, value) if isinstance(value, str) else value for value in operation["source_event_ids"]]
        if _json(generated) != _json(plan):
            failures.append("model_plan_mismatch")
        changed = {operation["section_id"] for operation in plan["operations"]}
        before = {section["id"]: render_section_text(section) for section in base if section["id"] in changed}
        after, context = {}, {}
        for section in evidence["final_ast"]["sections"]:
            (after if section["id"] in changed or section["id"] not in base_ids else context)[section["id"]] = render_section_text(section)
        titles = {section["id"]: section["title"] for section in template["sections"]} | {section["id"]: section["title"] for section in evidence["final_ast"]["sections"]}
        verification_input = {
            "task_type": "VERIFY_REQUIREMENT_COVERAGE",
            "sources": expected_sources,
            "before_sections": before,
            "after_sections": after,
            "context_sections": context,
            "section_titles": titles,
        }
        verification_input["cross_section_repetitions"] = [list(pair) for pair in changed_section_repetitions(case["seed"]["base_document"], evidence["final_ast"])]
        failures.extend(_verification_evidence_failures(verification_calls, verification_input, {**expected_sources, **{f"section:{key}": {"text": value} for key, value in before.items()}}, titles))
    return list(dict.fromkeys(failures))
