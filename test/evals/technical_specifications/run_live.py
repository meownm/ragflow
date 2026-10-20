"""Run frozen synthetic model checks in the existing local application runtime."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODULES = {
    "api.source_workbench.adapters": "api/source_workbench/adapters.py",
    "api.document_exchange.identity": "api/document_exchange/identity.py",
    "api.document_exchange.merge": "api/document_exchange/merge.py",
    "api.source_workbench.document_standards": "api/source_workbench/document_standards.py",
    "api.technical_specifications.domain": "api/technical_specifications/domain.py",
    "api.technical_specifications.application": "api/technical_specifications/application.py",
    "api.technical_specifications.ai": "api/technical_specifications/ai.py",
    "quality_runner": "test/evals/technical_specifications/live_quality.py",
}
CORPUS = "test/evals/technical_specifications/golden.v1.json"

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
    emit({"event":"transport", "request_id":request_id, "cache_bypass":True, "response_format":generation.get("response_format")})
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
    signature = {"provider": config["llm_factory"], "name": config["llm_name"], "context": model.context_tokens, "output_limit": model.output_tokens, "digest":entry["digest"], "details":entry.get("details")}
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
    result = await sys.modules["quality_runner"].run_suite(ai, tenant.id, payload["corpus"], emit)
    await captured_open(tenant.id)
    paths = ["common/model_limits.py", "api/db/services/llm_service.py", "api/db/services/tenant_llm_service.py"]
    runtime_hashes = {path:hashlib.sha256(Path("/ragflow",path).read_bytes()).hexdigest() for path in paths}
    result.update(model=model_signature, calls=call_count, source_hashes=payload["source_hashes"], runtime_hashes=runtime_hashes)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new evidence directory outside the source checkout")
    container = "ragflow-local-ragflow-cpu-1"
    inspection = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
    labels = inspection["Config"]["Labels"]
    assert labels["com.docker.compose.project"] == "ragflow-local" and labels["com.docker.compose.service"] == "ragflow-cpu"
    modules = {name: {"path": path, "source": (ROOT / path).read_text(encoding="utf-8")} for name, path in MODULES.items()}
    for value in modules.values():
        value["sha256"] = hashlib.sha256(value["source"].encode()).hexdigest()
    source_hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in [*MODULES.values(), CORPUS, "test/evals/technical_specifications/run_live.py"]}
    payload = {"modules": modules, "corpus": json.loads((ROOT / CORPUS).read_text(encoding="utf-8")), "source_hashes": source_hashes}
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
    report = {
        "status": final["status"] if final and stable and ((result.returncode == 0) == (final["status"] == "PASS")) else "INCOMPLETE",
        "exit_code": result.returncode,
        "source_stable": stable,
        "runtime_image": inspection["Image"],
        "result": final,
        "boundary": "Frozen synthetic inputs and current AI/application source, real configured QA Ollama model. No document writes, browser, tenant authorization or deployment acceptance.",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
