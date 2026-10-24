"""Run authored dialogues through the real revision adapter and PostgreSQL.

Run in a configured local QA application environment, with an explicitly chosen
QA tenant and the dedicated loopback disposable PostgreSQL DSN. This creates no
application projects, jobs, or documents and never changes connection profiles.
"""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from time import monotonic
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
OUTPUT_TOKEN_BUDGET = 4096
if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(ROOT / "api/apps")]
    sys.modules["api.apps"] = api_apps

from business_documents.adapters.llm import RAGFlowLLMAdapter
from api.apps.business_documents.sql_query_postgres import execute_postgres
from api.apps.business_documents.sql_query_revision_ai import propose_revision
from test.evals.sql_projects.database import disposable_database
from test.evals.sql_projects.golden import GOLDEN_CASES
from test.evals.sql_projects.schema import accepted_requirements, accepted_schema
from test.evals.sql_projects.scoring import response_kind, score_turn
from test.evals.sql_projects.discriminators import PACK_ID, SETUP_SQL


def digest(value):
    return "sha256:" + sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def source_identity():
    paths = [
        "api/apps/business_documents/ai.py",
        "api/apps/business_documents/sql_query_revision_ai.py",
        "api/apps/business_documents/sql_query_postgres.py",
        "rag/llm/chat_model.py",
        "business_documents/sql_query/dialogue.py",
        "business_documents/sql_query/query_specification.py",
        "business_documents/sql_query/advanced_query.py",
        "business_documents/sql_query/project_compilation.py",
        "test/evals/sql_projects/golden.py",
        "test/evals/sql_projects/corpus.py",
        "test/evals/sql_projects/scoring.py",
        "test/evals/sql_projects/schema.py",
        "test/evals/sql_projects/database.py",
        "test/evals/sql_projects/discriminators.py",
        "test/evals/sql_projects/run_live.py",
        "test/evals/sql_projects/fixtures/schema.sql",
        "test/evals/sql_projects/fixtures/data.sql",
    ]
    return {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "sources": {path: "sha256:" + sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}}


class AuditedModel:
    def __init__(self, model, events):
        self.model = model
        self.events = events

    async def async_generate(self, tenant_id, system, payload):
        event = {"system_hash": digest(system), "input_hash": digest(payload), "input": payload, "started_at": datetime.now(timezone.utc).isoformat()}
        self.events.append(event)
        try:
            response = await self.model.async_generate(tenant_id, system, payload)
            event["response"] = response
            return response
        finally:
            audit = self.model.consume_execution_audit()
            if isinstance(audit, dict):
                event["model"] = {key: audit.get(key) for key in ("provider", "model", "model_type", "duration_ms", "token_usage")}
                event["model"]["parameters"] = {
                    key: value for key, value in audit.get("parameters", {}).items() if key in {"temperature", "top_p", "max_tokens", "max_completion_tokens", "reasoning_effort"}
                }
                enable_thinking = audit.get("parameters", {}).get("enable_thinking")
                if type(enable_thinking) is bool:
                    event["model"]["parameters"]["enable_thinking"] = enable_thinking
            else:
                event["audit_error"] = "Real model execution audit is missing"


async def run_cases(tenant_id, database, *, model, discriminator_database=None, cases=GOLDEN_CASES, repeats=3, emit=lambda event: None):
    schema = accepted_schema()
    results = []
    signature = None
    for repetition in range(1, repeats + 1):
        for case in cases:
            current_sql = case.initial.oracle.sql
            current_parameters = case.initial.bindings()
            discussion = []
            for number, turn in enumerate(case.turns, 1):
                events = []
                discussion.append({"role": "user", "text": turn.text})
                record = {
                    "case_id": case.id,
                    "category": case.category,
                    "repetition": repetition,
                    "turn": number,
                    "request": turn.text,
                    "expected_kind": turn.kind,
                    "review_criteria": turn.review,
                    "events": events,
                }
                started = monotonic()
                try:
                    response = await propose_revision(
                        tenant_id,
                        {
                            "target": "QUERY",
                            "locale": "ru",
                            "sql": current_sql,
                            "parameters": current_parameters,
                            "requirements": accepted_requirements(),
                            "schema": schema,
                            "anchor": {"kind": "WHOLE"},
                            "comments": list(discussion),
                        },
                        llm=AuditedModel(model, events),
                    )
                    record["response"] = response
                    result = None
                    discriminator_results = {}
                    if response_kind(response) == "PROPOSAL" and response["proposal"].get("checks", {}).get("status") == "PASS":
                        proposal = response["proposal"]
                        values = {item["name"]: item["value"] for item in proposal["parameters"]}
                        result = await asyncio.to_thread(execute_postgres, database, proposal["sql"], values, timeout_ms=5000, max_rows=100, max_result_bytes=100000)
                        record["result"] = result
                        if discriminator_database is not None:
                            discriminator_results[PACK_ID] = await asyncio.to_thread(
                                execute_postgres, discriminator_database, proposal["sql"], values, timeout_ms=5000, max_rows=100, max_result_bytes=100000
                            )
                            record["discriminator_results"] = discriminator_results
                    errors = score_turn(turn, response, result=result, discriminator_results=discriminator_results)
                    if len(events) != 1 or "model" not in events[0] or any(not events[0]["model"].get(key) or events[0]["model"][key] == "unknown" for key in ("provider", "model")):
                        errors.append("One application-audited generation per turn is required")
                    else:
                        controls = events[0]["model"].get("parameters", {})
                        if any(key not in controls for key in ("temperature", "top_p")) or not any(
                            type(controls.get(key)) is int and controls[key] > 0 for key in ("max_tokens", "max_completion_tokens")
                        ):
                            errors.append("Generation controls or output budget are missing")
                        budget = controls.get("max_tokens", controls.get("max_completion_tokens"))
                        current_signature = (
                            events[0]["model"]["provider"],
                            events[0]["model"]["model"],
                            controls.get("temperature"),
                            controls.get("top_p"),
                            controls.get("reasoning_effort"),
                            controls.get("enable_thinking"),
                            budget,
                        )
                        if signature is None:
                            signature = current_signature
                        elif signature != current_signature:
                            errors.append("Model identity or sampling controls changed during the run")
                    record["errors"] = errors
                    if not errors:
                        discussion.append({"role": "assistant", "text": response.get("answer") or json.dumps(response["proposal"], ensure_ascii=False)})
                        if turn.kind == "PROPOSAL":
                            record["test_action"] = "APPLY_ACCEPTED_PROPOSAL_TO_NEXT_TURN"
                            current_sql = response["proposal"]["sql"]
                            current_parameters = response["proposal"]["parameters"]
                except Exception as error:
                    # Provider exceptions can contain URLs or credentials; raw
                    # exception messages do not belong in evidence artifacts.
                    record["errors"] = ["Execution failed: " + type(error).__name__]
                record["duration_ms"] = round((monotonic() - started) * 1000)
                record["status"] = "FAIL" if record["errors"] else "REVIEW_REQUIRED" if turn.review else "AUTOMATED_PASS"
                results.append(record)
                emit(record)
                if record["errors"]:
                    break
    return results


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Dedicated QA tenant configured for a real model")
    parser.add_argument("--out", type=Path, required=True, help="New evidence directory outside the source checkout")
    parser.add_argument("--case", action="append", choices=[case.id for case in GOLDEN_CASES])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model", help="QA-only model override on the configured Ollama endpoint; does not change the saved profile")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    output = args.out.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("Evidence must be outside the source checkout")
    output.mkdir(parents=True, exist_ok=False)
    identity = source_identity()
    selected = tuple(case for case in GOLDEN_CASES if not args.case or case.id in args.case)
    results = []
    execution_error = None
    cleanup = "NOT_STARTED"
    with (output / "turns.jsonl").open("x", encoding="utf-8") as stream:

        def emit(record):
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            stream.flush()
            print(json.dumps({key: record[key] for key in ("case_id", "repetition", "turn", "status")}), flush=True)

        try:
            configure_model_catalog()
            cleanup = "UNVERIFIED"
            with disposable_database() as database, disposable_database(extra_sql=SETUP_SQL) as boundary:
                results = await run_cases(
                    args.tenant_id,
                    database,
                    model=RAGFlowLLMAdapter(args.model, max_output_tokens=OUTPUT_TOKEN_BUDGET),
                    discriminator_database=boundary,
                    cases=selected,
                    repeats=args.repeats,
                    emit=emit,
                )
            cleanup = "VERIFIED"
        except Exception as error:
            execution_error = type(error).__name__
    counts = dict(Counter(record["status"] for record in results))
    complete = len(selected) == 80 and args.repeats >= 3 and len(results) == args.repeats * sum(len(case.turns) for case in selected)
    unchanged = source_identity() == identity
    summary = {
        "format": "sql-project-golden-run",
        "schema_version": "1",
        "source": identity,
        "source_unchanged": unchanged,
        "case_ids": [case.id for case in selected],
        "repeats": args.repeats,
        "counts": counts,
        "complete_matrix": complete,
        "cleanup": cleanup,
        "execution_error": execution_error,
        "fresh_inference_verified": False,
        "output_token_budget": OUTPUT_TOKEN_BUDGET,
        "requested_model": args.model,
        "status": "FAIL" if execution_error or counts.get("FAIL") or not unchanged else "INCOMPLETE" if complete else "PARTIAL",
        "limits": [
            "Explanation and clarification semantics require an independent semantic scorer; review_criteria are an automation gap.",
            "Fresh upstream inference, cache/dedup status and request identity are not independently verified by the application audit.",
            "This lane does not prove API authorization, browser behavior, application persistence, or model weight digest.",
        ],
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if execution_error or counts.get("FAIL") or not unchanged else 2


def configure_model_catalog():
    """Load model metadata without starting search/storage or initializing auth."""
    from common import settings

    settings.FACTORY_LLM_INFOS = json.loads((ROOT / "conf/llm_factories.json").read_text(encoding="utf-8"))["factory_llm_infos"]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
