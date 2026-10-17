"""Business Documents HTTP load and an isolated OpenAI-compatible LLM emulator."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import aiohttp
from aiohttp import web


ROOT = Path(__file__).resolve().parents[2]
RESPONSE = json.loads(Path(__file__).with_name("intake_response.json").read_text(encoding="utf-8"))
MODEL = "bd-loadtest-qwen3.8-27b"
FIXTURE_TEXT = RESPONSE["questions"][0]["text"]


@dataclass(frozen=True)
class GPUProfile:
    # Planning estimate, not a hardware benchmark. See README for provenance.
    name: str = "H200 x1 / Qwen3.8-27B FP8 / non-thinking / estimated"
    prefill_tps: float = 5000
    decode_tps: float = 85
    aggregate_decode_tps: float = 600
    slots: int = 8
    min_output_tokens: int = 512

    def __post_init__(self):
        values = (self.prefill_tps, self.decode_tps, self.aggregate_decode_tps, self.slots, self.min_output_tokens)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("GPU rates, slots and output tokens must be positive and finite")


class EmulatedGPU:
    """One shared prefill stage and a bounded shared decode budget."""

    def __init__(self, profile: GPUProfile):
        self.profile = profile
        self.slots = asyncio.Semaphore(profile.slots)
        self.prefill = asyncio.Lock()
        self.decoders = 0
        self.peak_active = 0
        self.active = 0

    async def generate(self, input_tokens: int, output_tokens: int) -> dict:
        started = time.perf_counter()
        async with self.slots:
            admitted = time.perf_counter()
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            try:
                async with self.prefill:
                    await asyncio.sleep(input_tokens / self.profile.prefill_tps)
                first_token = time.perf_counter()
                self.decoders += 1
                try:
                    remaining = float(output_tokens)
                    while remaining > 0:
                        rate = min(self.profile.decode_tps, self.profile.aggregate_decode_tps / self.decoders)
                        duration = min(0.05, remaining / rate)
                        tick_started = time.perf_counter()
                        await asyncio.sleep(duration)
                        # Account for actual scheduler time (Windows timers can
                        # oversleep); nominal tick counts distort token rates.
                        remaining -= rate * (time.perf_counter() - tick_started)
                finally:
                    self.decoders -= 1
                return {"queue_seconds": admitted - started, "ttft_seconds": first_token - started, "total_seconds": time.perf_counter() - started}
            finally:
                self.active -= 1


def mock_app(profile: GPUProfile, model: str = MODEL, fail_every: int = 0) -> web.Application:
    gpu = EmulatedGPU(profile)
    records = []
    app = web.Application()

    async def stats(_request):
        return web.json_response({"model": model, "profile": asdict(profile), "peak_active": gpu.peak_active, "records": records})

    async def models(_request):
        return web.json_response({"object": "list", "data": [{"id": model, "object": "model", "owned_by": "load-test"}]})

    async def completion(request):
        body = await request.json()
        try:
            message = next(item["content"] for item in reversed(body["messages"]) if item["role"] == "user")
            # RAGFlow's existing model-registration check sends exactly "Hi"
            # with streaming enabled. It is not a Business Documents job.
            if body.get("model") == model and message == "Hi":
                if body.get("stream"):
                    chunk = {
                        "id": "load-probe",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": None}],
                    }
                    final = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                    stream = "".join("data: " + json.dumps(item) + "\n\n" for item in (chunk, final)) + "data: [DONE]\n\n"
                    return web.Response(text=stream, content_type="text/event-stream")
                return web.json_response(
                    {
                        "id": "load-probe",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
                    }
                )
            payload = json.loads(message)["job_input"]
            if body.get("model") != model or payload.get("task_type") != "ASSESS_INTAKE" or body.get("stream"):
                raise ValueError
            document_id = payload["document_id"]
        except (KeyError, ValueError, TypeError, StopIteration, AttributeError):
            return web.json_response({"error": {"message": "Expected non-streaming ASSESS_INTAKE with the load-test model"}}, status=400)
        record = {"document_id": document_id, "request_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(), "status": "RUNNING"}
        records.append(record)
        if fail_every and len(records) % fail_every == 0:
            record["status"] = "HTTP_503"
            return web.json_response({"error": {"message": "Injected load-test failure"}}, status=503)
        # Approximate token count, explicitly recorded; no tokenizer/model download.
        input_tokens = max(1, math.ceil(sum(len(str(item.get("content", ""))) for item in body["messages"]) / 3))
        output = json.dumps(RESPONSE, ensure_ascii=False)
        output_tokens = max(profile.min_output_tokens, math.ceil(len(output) / 3))
        record.update(input_tokens_estimate=input_tokens, output_tokens_emulated=output_tokens)
        try:
            record.update(await gpu.generate(input_tokens, output_tokens))
            record["status"] = "COMPLETED"
        except asyncio.CancelledError:
            record["status"] = "CANCELLED"
            raise
        return web.json_response(
            {
                "id": "load-" + uuid4().hex,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": output}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
            }
        )

    app.router.add_get("/stats", stats)
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", completion)
    return app


def percentiles(values: list[float]) -> dict:
    ordered = sorted(values)
    return {f"p{p}": ordered[max(0, math.ceil(len(ordered) * p / 100) - 1)] if ordered else None for p in (50, 95, 99)}


class APIError(RuntimeError):
    pass


async def api(session, base, token, method, path, timings, payload=None):
    started = time.perf_counter()
    try:
        async with session.request(method, base + path, headers={"Authorization": token}, json=payload, allow_redirects=False) as response:
            # Never include provider/server bodies or credentials in errors/reports.
            if response.status >= 300:
                raise APIError(f"{method} HTTP {response.status}")
            body = await response.json()
            if body.get("code") != 0:
                raise APIError(f"{method} envelope code={body.get('code')}")
            return body["data"]
    finally:
        timings.append(time.perf_counter() - started)


async def run_load(args, tokens: list[str], cleanup_token: str) -> dict:
    run_id = uuid4().hex
    results, timings = [], defaultdict(list)
    timeout = aiohttp.ClientTimeout(total=args.request_timeout)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(limit=max(100, args.users * 2))) as session:

        async def request(token, method, path, label, payload=None):
            return await api(session, args.base_url.rstrip("/"), token, method, path, timings[label], payload)

        # Preflight is read-only. Distinct tokens alone do not prove distinct users.
        users = [await request(token, "GET", "/api/v1/users/me", "preflight") for token in tokens]
        if len({user["id"] for user in users}) != args.users:
            raise ValueError("Each virtual user must authenticate as a different account")
        for token in tokens:
            config = await request(token, "GET", "/api/v1/users/me/models", "preflight")
            if config.get("llm_id", "").split("@")[0] != args.model:
                raise ValueError("Every test user's default chat model must be the load-test model")
        async with session.get(args.mock_url.rstrip("/") + "/stats") as response:
            response.raise_for_status()
            mock_before = await response.json()
        if mock_before["model"] != args.model:
            raise ValueError("LLM emulator model does not match --model")

        async def person(index, token):
            await asyncio.sleep(args.ramp_seconds * index / max(1, args.users - 1))
            for round_index in range(args.rounds):
                result = {"user": index + 1, "round": round_index + 1, "ok": False}
                results.append(result)
                started = time.perf_counter()
                document_id = None
                try:
                    document = await request(
                        token,
                        "POST",
                        "/api/v1/business-documents",
                        "create",
                        {
                            "schema_version": "1",
                            "document_type": "business_requirements",
                            "title": f"load-{run_id}-{index}-{round_index}",
                            "idea": "Создать сервис переводов для физических и юридических лиц.",
                            "dataset_ids": [],
                        },
                    )
                    document_id = result["document_id"] = document["document_id"]
                    command = {
                        "schema_version": "1",
                        "command_id": uuid4().hex,
                        "idempotency_key": uuid4().hex,
                        "expected_state_version": document["state_version"],
                        "type": "REQUEST_INTAKE_ASSESSMENT",
                        "payload": {},
                    }
                    submitted = await request(token, "POST", f"/api/v1/business-documents/{document_id}/commands", "submit", command)
                    result["job_id"] = submitted["job_id"]
                    async with asyncio.timeout(args.job_timeout):
                        while True:
                            jobs = await request(token, "GET", f"/api/v1/business-documents/{document_id}/jobs", "poll")
                            job = next((job for job in jobs if job["job_id"] == result["job_id"]), None)
                            if job and job["status"] == "COMPLETED":
                                result["attempts"] = job["attempt"]
                                break
                            if job and job["status"] in {"DEAD", "CANCELLED", "FAILED"}:
                                raise APIError("job status=" + job["status"])
                            await asyncio.sleep(args.poll_seconds)
                    final = await request(token, "GET", f"/api/v1/business-documents/{document_id}", "read")
                    questions = final["protocol"]["questions"]
                    if final["document_id"] != document_id or not any(question.get("text") == FIXTURE_TEXT for question in questions):
                        raise APIError("Completed job has no expected emulator question in the document")
                    result["ok"] = True
                except (APIError, aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError, StopIteration) as error:
                    result["error"] = str(error) if isinstance(error, APIError) else type(error).__name__
                finally:
                    result["seconds"] = time.perf_counter() - started
                    if document_id:
                        try:
                            await request(cleanup_token, "DELETE", f"/api/v1/business-documents/{document_id}", "cleanup")
                            result["cleaned"] = True
                        except (APIError, aiohttp.ClientError, TimeoutError, ValueError, KeyError) as error:
                            result["cleaned"] = False
                            result["cleanup_error"] = str(error) if isinstance(error, APIError) else type(error).__name__
                if round_index + 1 < args.rounds:
                    await asyncio.sleep(args.think_seconds)

        started = time.perf_counter()
        await asyncio.gather(*(person(index, token) for index, token in enumerate(tokens)))
        elapsed = time.perf_counter() - started
        try:
            async with session.get(args.mock_url.rstrip("/") + "/stats") as response:
                response.raise_for_status()
                mock_after = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            # Retain created IDs, run_id and cleanup evidence if telemetry dies.
            mock_after = {"records": [], "profile": mock_before["profile"]}
    document_ids = {item["document_id"] for item in results if "document_id" in item}
    records = [item for item in mock_after["records"] if item["document_id"] in document_ids]
    completed_ids = {item["document_id"] for item in records if item["status"] == "COMPLETED"}
    failures = sum(not item["ok"] for item in results)
    p = percentiles([item["seconds"] for item in results])
    cleanup_ok = all(item.get("cleaned", True) for item in results)
    evidence_ok = document_ids == completed_ids and len(document_ids) == args.users * args.rounds
    passed = failures == 0 and cleanup_ok and evidence_ok
    if args.max_p95_seconds is not None:
        passed = passed and p["p95"] <= args.max_p95_seconds
    return {
        "status": "PASS" if passed else "FAIL",
        "scope": "HTTP create + intake assessment + durable worker + document read; emulated LLM",
        "run_id": run_id,
        "users": args.users,
        "rounds": args.rounds,
        "elapsed_seconds": elapsed,
        "scenarios": len(results),
        "failures": failures,
        "error_rate": failures / len(results),
        "successful_scenarios_per_second": (len(results) - failures) / elapsed,
        "scenario_seconds": p,
        "http_seconds": {key: {"count": len(values), **percentiles(values)} for key, values in timings.items()},
        "cleanup_ok": cleanup_ok,
        "emulator_evidence_ok": evidence_ok,
        "llm_calls": records,
        "profile": mock_after["profile"],
        "thresholds": {"max_error_rate": 0, "max_p95_seconds": args.max_p95_seconds},
        "results": results,
    }


def source_identity() -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

    return {
        "head": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(Path(__file__).with_name("intake_response.json").read_bytes()).hexdigest(),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="mode", required=True)
    mock = sub.add_parser("mock-llm")
    mock.add_argument("--host", default="127.0.0.1")
    mock.add_argument("--port", type=int, default=18081)
    mock.add_argument("--model", default=MODEL)
    for name, default in (("prefill-tps", 5000), ("decode-tps", 85), ("aggregate-decode-tps", 600)):
        mock.add_argument("--" + name, type=float, default=default)
    mock.add_argument("--slots", type=int, default=8)
    mock.add_argument("--output-tokens", type=int, default=512)
    mock.add_argument("--fail-every", type=int, default=0)
    run = sub.add_parser("run")
    run.add_argument("--base-url", required=True)
    run.add_argument("--mock-url", default="http://127.0.0.1:18081")
    run.add_argument("--model", default=MODEL)
    run.add_argument("--users-file", type=Path, required=True, help="JSON array of distinct web-session Authorization values")
    run.add_argument("--cleanup-token-env", default="BD_LOAD_CLEANUP_TOKEN")
    run.add_argument("--users", type=int, default=50)
    run.add_argument("--rounds", type=int, default=1)
    run.add_argument("--ramp-seconds", type=float, default=5)
    run.add_argument("--think-seconds", type=float, default=2)
    run.add_argument("--poll-seconds", type=float, default=2)
    run.add_argument("--job-timeout", type=float, default=1800)
    run.add_argument("--request-timeout", type=float, default=60)
    run.add_argument("--max-p95-seconds", type=float)
    run.add_argument("--report", type=Path, required=True)
    return result


def main() -> int:
    cli = parser()
    args = cli.parse_args()
    if args.mode == "mock-llm":
        profile = GPUProfile(prefill_tps=args.prefill_tps, decode_tps=args.decode_tps, aggregate_decode_tps=args.aggregate_decode_tps, slots=args.slots, min_output_tokens=args.output_tokens)
        if args.fail_every < 0:
            cli.error("--fail-every must be nonnegative")
        web.run_app(mock_app(profile, args.model, args.fail_every), host=args.host, port=args.port, access_log=None, handler_cancellation=True)
        return 0
    if args.users <= 0 or args.rounds <= 0:
        cli.error("users/rounds must be positive")
    if args.max_p95_seconds is not None and (not math.isfinite(args.max_p95_seconds) or args.max_p95_seconds <= 0):
        cli.error("max-p95-seconds must be positive and finite")
    for name in ("ramp_seconds", "think_seconds", "poll_seconds", "job_timeout", "request_timeout"):
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0 or (name not in {"ramp_seconds", "think_seconds"} and value == 0):
            cli.error(f"Invalid {name}")
    tokens = json.loads(args.users_file.read_text(encoding="utf-8-sig"))
    if not isinstance(tokens, list) or len(tokens) != args.users or any(not isinstance(token, str) or not token.strip() for token in tokens) or len(set(tokens)) != args.users:
        cli.error("users-file must contain exactly --users distinct nonempty Authorization strings")
    cleanup = os.environ.get(args.cleanup_token_env)
    if not cleanup:
        cli.error("Set the cleanup administrator's token in " + args.cleanup_token_env)
    identity = source_identity()
    try:
        report = asyncio.run(run_load(args, tokens, cleanup))
    except (APIError, aiohttp.ClientError, TimeoutError, ValueError, KeyError) as error:
        report = {"status": "INCOMPLETE", "error": str(error) if isinstance(error, (APIError, ValueError)) else type(error).__name__}
    report["source"] = identity
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "users", "scenarios", "failures", "scenario_seconds") if key in report}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
