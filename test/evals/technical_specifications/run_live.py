"""Run frozen synthetic model checks in the existing local application runtime."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODULES = {
    "api.source_workbench.adapters": "api/source_workbench/adapters.py",
    "document_exchange.identity": "document_exchange/identity.py",
    "api.document_exchange.merge": "api/document_exchange/merge.py",
    "api.source_workbench.document_standards": "api/source_workbench/document_standards.py",
    "api.technical_specifications.domain": "api/technical_specifications/domain.py",
    "api.technical_specifications.application": "api/technical_specifications/application.py",
    "api.technical_specifications.ai": "api/technical_specifications/ai.py",
    "quality_runner": "test/evals/technical_specifications/live_quality.py",
}
CORPUS = "test/evals/technical_specifications/golden.v2.json"

CHILD = """
import asyncio, hashlib, json, logging, sys, types, os
from pathlib import Path
import aiohttp
logging.disable(logging.CRITICAL)
from common import settings
settings.init_settings()
from api.db.db_models import Tenant
tenant = Tenant.get_or_none(Tenant.name == "Business Documents nightly QA")
assert tenant is not None, "Dedicated local QA tenant missing"
payload = json.loads(PAYLOAD)
for name, item in payload["modules"].items():
    assert hashlib.sha256(item["source"].encode()).hexdigest() == item["sha256"]
    module = types.ModuleType(name)
    module.__package__ = name.rpartition(".")[0]
    module.__file__ = "<frozen-source>/" + item["path"]
    sys.modules[name] = module
    exec(compile(item["source"], module.__file__, "exec"), module.__dict__)
from api.source_workbench.adapters import RAGFlowAnswerGateway, RAGFlowProcessModel
from api.db.services.llm_service import LLMBundle
from contextlib import aclosing
from uuid import uuid4

def emit(event):
    print("QUALITY_EVENT=" + json.dumps(event, ensure_ascii=True), flush=True)

original_open = RAGFlowAnswerGateway.open_processor
original_stream = RAGFlowProcessModel.stream
original_bundle_stream = LLMBundle.async_chat_streamly_delta
frozen_config = None
model_signature = None
call_count = 0

async def uncached_bundle_stream(self, system, history, generation, **kwargs):
    request_id = str(uuid4())
    generation = {**generation, "extra_headers":{**generation.get("extra_headers",{}), "x-ollama-proxy-bypass-dedup":"1", "x-ollama-proxy-request-id":request_id}}
    emit({"event":"transport", "request_id":request_id, "cache_bypass":True, "response_format":generation.get("response_format"), "thinking":generation.get("extra_body",{}).get("think",False), "with_reasoning":kwargs.get("with_reasoning",True)})
    async with aclosing(original_bundle_stream(self, system, history, generation, **kwargs)) as stream:
        async for delta in stream:
            yield delta

async def captured_open(actor):
    global frozen_config, model_signature
    model = await original_open(actor)
    assert model.model_config["llm_factory"] == "Ollama", "This lane uses the dedicated local Ollama profile"
    config = model.model_config
    base = config["api_base"].rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    headers = {"Authorization":"Bearer " + config["api_key"]} if config.get("api_key") else {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.get(base + "/api/tags", headers=headers) as response:
            response.raise_for_status()
            tags = await response.json()
    name = config["llm_name"].split("___")[0]
    entry = next(item for item in tags["models"] if name in {item.get("name"),item.get("model")})
    assert entry.get("digest"), "The real model digest is required for a frozen run"
    signature = {"provider": config["llm_factory"], "name": config["llm_name"], "context": model.context_tokens, "output_limit": model.output_tokens, "digest":entry["digest"], "details":entry.get("details"), "supports_thinking":getattr(model,"supports_thinking",False)}
    if frozen_config is None:
        frozen_config = dict(model.model_config)
        model_signature = signature
        emit({"event":"model", **signature})
    assert frozen_config == model.model_config and signature == model_signature, "Model configuration changed during the frozen run"
    return model

async def captured_stream(self, system, data, limit, **options):
    global call_count
    call_count += 1
    number = call_count
    emit({"event":"request", "call":number, "system":system, "payload":data, "output_budget":limit, "options":options})
    chunks = []
    complete = False
    try:
        async for chunk in original_stream(self, system, data, limit, **options):
            chunks.append(chunk)
            yield chunk
        complete = True
    finally:
        emit({"event":"response", "call":number, "complete":complete, "text":"".join(chunks)})

RAGFlowAnswerGateway.open_processor = staticmethod(captured_open)
RAGFlowProcessModel.stream = captured_stream
LLMBundle.async_chat_streamly_delta = uncached_bundle_stream

async def main():
    ai = sys.modules["api.technical_specifications.ai"].SpecificationAI()
    result = await sys.modules["quality_runner"].run_suite(ai, tenant.id, payload["corpus"], emit, payload["case_ids"])
    selected = [case for case in payload["corpus"]["cases"] if payload["case_ids"] is None or case["id"] in payload["case_ids"]]
    expected_calls = sum(2 if case["kind"] == "merge" else 0 if case.get("expect_error") else 1 for case in selected)
    if result["status"] == "PASS":
        assert call_count == expected_calls, "Missing or repeated real-model calls"
    await captured_open(tenant.id)
    paths = ["common/model_limits.py", "api/db/services/llm_service.py", "api/db/services/tenant_llm_service.py"]
    runtime_hashes = {path:hashlib.sha256(Path("/ragflow",path).read_bytes()).hexdigest() for path in paths}
    result.update(model=model_signature, calls=call_count, expected_calls=expected_calls, source_hashes=payload["source_hashes"], runtime_hashes=runtime_hashes)
    emit({"event":"suite_finished", **result})
    return result["status"] == "PASS"

try:
    passed = asyncio.run(main())
except Exception as error:
    emit({"event":"suite_incomplete", "error_type":type(error).__name__})
    passed = False
sys.stdout.flush()
os._exit(0 if passed else 1)
"""


def verify_upstream(events, final):
    """Read this run's metadata and wire controls; bodies stay inside the proxy."""
    if not final or type(final["model"].get("supports_thinking")) is not bool:
        raise ValueError("Missing model capability evidence")
    expected, calls, pending = {}, set(), None
    for event in events:
        if event["event"] == "request":
            if pending is not None or type(event["call"]) is not int or event["call"] in calls:
                raise ValueError("Missing transport or duplicate model call")
            calls.add(event["call"])
            if type(event["output_budget"]) is not int or event["output_budget"] <= 0 or event["options"].get("json_response") is not True or type(event["options"].get("thinking", False)) is not bool:
                raise ValueError("Invalid expected generation controls")
            pending = {"thinking": event["options"].get("thinking", False) and final["model"]["supports_thinking"], "output_budget": event["output_budget"]}
        elif event["event"] == "transport":
            identity = event["request_id"]
            if pending is None or not isinstance(identity, str) or not identity or identity in expected:
                raise ValueError("Missing or duplicate request identities")
            if (
                event.get("cache_bypass") is not True
                or event.get("with_reasoning") is not False
                or event.get("thinking") is not pending["thinking"]
                or event.get("response_format") != {"type": "json_object"}
            ):
                raise ValueError("Transport controls differ from the requested mode")
            expected[identity], pending = pending, None
    ids = list(expected)
    if pending is not None or not ids or len(ids) != final["calls"] or calls != set(range(1, final["calls"] + 1)):
        raise ValueError("Incomplete model call inventory")
    script = """
import asyncio,json,sys
from sqlalchemy import select
from app.database import get_engine
from app.models import HttpTransaction as T
async def main():
    engine=get_engine()
    fields=[T.request_id,T.status,T.source_type,T.dedup_hit,T.upstream_id,T.model_name,T.response_status_code,T.request_body_raw]
    async with engine.connect() as connection:
        result=await connection.execute(select(*fields).where(T.request_id.in_(json.loads(sys.argv[1]))))
        rows=[dict(row._mapping) for row in result]
    for row in rows:
        body=json.loads(row.pop("request_body_raw"))
        options=body.get("options",{})
        row.update(thinking=body.get("think"),format=body.get("format"),temperature=options.get("temperature"),context=options.get("num_ctx"),output_budget=options.get("num_predict"))
    await engine.dispose()
    print(json.dumps(rows))
asyncio.run(main())
"""
    process = subprocess.run(["docker", "exec", "-w", "/app", "ollama-proxy", "python", "-c", script, json.dumps(ids)], capture_output=True, timeout=30, check=True)
    rows = json.loads(process.stdout)
    if len(rows) != len(ids) or {row["request_id"] for row in rows} != set(ids):
        raise ValueError("Proxy request inventory mismatch")
    model = final["model"]["name"].split("___")[0]
    if any(
        row["source_type"] != "upstream" or row["dedup_hit"] is not False or row["status"] != "done" or row["response_status_code"] != 200 or not row["upstream_id"] or row["model_name"] != model
        for row in rows
    ):
        raise ValueError("Fresh successful inference on the frozen model not proven")
    for row in rows:
        control = expected[row["request_id"]]
        if (
            row["thinking"] is not control["thinking"]
            or row["format"] != "json"
            or type(row["temperature"]) not in (int, float)
            or row["temperature"] != 0
            or type(row["context"]) is not int
            or row["context"] != final["model"]["context"]
            or type(row["output_budget"]) is not int
            or row["output_budget"] != control["output_budget"]
        ):
            raise ValueError("Actual proxy request controls differ from the frozen run")
    controls = {
        "status": "PASS",
        "requests": len(rows),
        "thinking": sum(row["thinking"] is True for row in rows),
        "without_thinking": sum(row["thinking"] is False for row in rows),
        "native_reasoning_visibility": False,
        "boundary": "Wire flags are read inside the proxy for only this run's IDs; bodies and headers are not exported. Reasoning visibility is a client-side flag.",
    }
    return {"status": "PASS", "expected_request_ids": ids, "transactions": rows, "generation_controls": controls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--case-id", action="append", help="Diagnostic selection only; never qualifies the corpus")
    parser.add_argument("--container", default="ragflow-local-ragflow-cpu-1", help="Local application runtime or an isolated runtime labelled ragflow.qa=technical-specifications")
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new evidence directory outside the source checkout")
    container = args.container
    inspection = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
    labels = inspection["Config"]["Labels"]
    local_service = labels.get("com.docker.compose.project") == "ragflow-local" and labels.get("com.docker.compose.service") == "ragflow-cpu"
    if not local_service and labels.get("ragflow.qa") != "technical-specifications":
        raise ValueError("Use the local application or an explicitly labelled isolated QA runtime")
    modules = {name: {"path": path, "source": (ROOT / path).read_text(encoding="utf-8")} for name, path in MODULES.items()}
    for value in modules.values():
        value["sha256"] = hashlib.sha256(value["source"].encode()).hexdigest()
    source_hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in [*MODULES.values(), CORPUS, "test/evals/technical_specifications/run_live.py"]}
    payload = {"modules": modules, "corpus": json.loads((ROOT / CORPUS).read_text(encoding="utf-8")), "source_hashes": source_hashes, "case_ids": args.case_id}
    output.mkdir(parents=True)
    (output / "input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    script = "PAYLOAD = " + repr(json.dumps(payload)) + "\n" + CHILD
    command = ["docker", "exec", "-i", container, "/ragflow/.venv/bin/python", "-B", "-"]
    with (output / "run.log").open("wb") as log:
        result = subprocess.run(command, input=script.encode(), stdout=log, stderr=subprocess.STDOUT, check=False)
    events = []
    for line in (output / "run.log").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("QUALITY_EVENT="):
            events.append(json.loads(line.split("=", 1)[1]))
    final = next((event for event in reversed(events) if event["event"] == "suite_finished"), None)
    stable = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest for path, digest in source_hashes.items())
    try:
        provenance = verify_upstream(events, final)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        provenance = {"status": "INCOMPLETE", "error_type": type(error).__name__}
    (output / "proxy-provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    controls = provenance.get("generation_controls", {"status": "INCOMPLETE"})
    (output / "generation-controls.json").write_text(json.dumps(controls, indent=2), encoding="utf-8")
    report = {
        "status": final["status"] if final and stable and provenance["status"] == "PASS" and ((result.returncode == 0) == (final["status"] == "PASS")) else "INCOMPLETE",
        "exit_code": result.returncode,
        "source_stable": stable,
        "runtime_image": inspection["Image"],
        "result": final,
        "provenance": provenance["status"],
        "generation_controls": controls["status"],
        "diagnostic": bool(args.case_id),
        "boundary": "Frozen synthetic inputs and current AI/application source, real configured QA Ollama model. No document writes, browser, tenant authorization or deployment acceptance.",
    }
    if args.case_id and report["status"] == "PASS":
        report["status"] = "DIAGNOSTIC"
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] in {"PASS", "DIAGNOSTIC"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
