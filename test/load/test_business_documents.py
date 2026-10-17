"""Local harness checks. The fake API is not RAGFlow performance evidence."""

import asyncio
import json
import os
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import jsonschema
from openai import AsyncOpenAI
import pytest

from test.load.business_documents import EmulatedGPU, GPUProfile, MODEL, RESPONSE, mock_app, parser, percentiles, run_load
from test.load.run_disposable import lifecycle_error_count


FAST = GPUProfile(prefill_tps=1e9, decode_tps=1e9, aggregate_decode_tps=1e9, slots=8, min_output_tokens=1)


def test_disposable_load_rejects_async_resource_errors_per_log_record():
    assert lifecycle_error_count('INFO complete\n{"message":"Task exception was never retrieved: Event loop is closed", "exception":"Event loop is closed"}') == 1
    assert lifecycle_error_count("RuntimeWarning: coroutine was never awaited\nRuntimeError: attached to a different loop") == 2
    assert lifecycle_error_count("INFO HTTP 200\nINFO worker finished") == 0


def llm_body(document_id="document-1"):
    return {"model": MODEL, "messages": [{"role": "user", "content": json.dumps({"job_input": {"task_type": "ASSESS_INTAKE", "document_id": document_id}})}]}


def test_fixture_matches_current_intake_contract():
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "agent/business_requirements/contracts/question_batch.v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(RESPONSE, schema)


def test_profile_and_percentiles():
    profile = GPUProfile()
    assert profile.slots == 8 and profile.decode_tps == 85
    assert percentiles(list(range(1, 101))) == {"p50": 50, "p95": 95, "p99": 99}
    assert percentiles([])["p95"] is None
    for value in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            GPUProfile(decode_tps=value)


async def test_mock_fifty_parallel_calls_and_rejects_other_tasks():
    async with TestServer(mock_app(FAST)) as server, aiohttp.ClientSession() as session:

        async def call(index):
            async with session.post(server.make_url("/v1/chat/completions"), json=llm_body(str(index))) as response:
                assert response.status == 200
                assert json.loads((await response.json())["choices"][0]["message"]["content"]) == RESPONSE

        await asyncio.gather(*(call(index) for index in range(50)))
        async with session.get(server.make_url("/stats")) as response:
            stats = await response.json()
        assert len(stats["records"]) == 50
        assert stats["peak_active"] <= 8
        bad = llm_body()
        bad["stream"] = True
        async with session.post(server.make_url("/v1/chat/completions"), json=bad) as response:
            assert response.status == 400


async def test_registration_probe_uses_streaming_and_does_not_count_as_load():
    async with TestServer(mock_app(FAST)) as server, aiohttp.ClientSession() as session:
        body = {"model": MODEL, "stream": True, "messages": [{"role": "user", "content": "Hi"}]}
        async with session.post(server.make_url("/v1/chat/completions"), json=body) as response:
            assert response.status == 200
            assert response.content_type == "text/event-stream"
            assert "[DONE]" in await response.text()
        async with session.get(server.make_url("/stats")) as response:
            assert not (await response.json())["records"]


async def test_openai_client_accepts_probe_and_business_response():
    async with TestServer(mock_app(FAST)) as server:
        async with AsyncOpenAI(base_url=str(server.make_url("/v1")), api_key="synthetic", max_retries=0) as client:
            stream = await client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "Hi"}], stream=True)
            parts = [chunk.choices[0].delta.content or "" async for chunk in stream]
            assert "".join(parts) == "OK"
            response = await client.chat.completions.create(**llm_body())
            assert json.loads(response.choices[0].message.content) == RESPONSE


async def test_cancellation_releases_the_single_inference_slot():
    gpu = EmulatedGPU(GPUProfile(prefill_tps=1e9, decode_tps=1000, slots=1))
    pending = asyncio.create_task(gpu.generate(1, 1_000_000))
    await asyncio.sleep(0.02)
    assert gpu.active == gpu.decoders == 1
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    async with asyncio.timeout(1):
        await gpu.generate(1, 1)
    assert gpu.active == gpu.decoders == 0


async def test_injected_llm_failure_is_visible_in_stats():
    async with TestServer(mock_app(FAST, fail_every=1)) as server, aiohttp.ClientSession() as session:
        async with session.post(server.make_url("/v1/chat/completions"), json=llm_body()) as response:
            assert response.status == 503
        async with session.get(server.make_url("/stats")) as response:
            assert (await response.json())["records"][0]["status"] == "HTTP_503"


async def exercise(tmp_path, *, users=50, defect=None):
    documents, jobs, tasks = {}, {}, []
    async with TestServer(mock_app(FAST)) as mock, aiohttp.ClientSession() as llm_session:

        async def route(request):
            token = request.headers.get("Authorization")
            if not token:
                return web.json_response({"code": 401}, status=401)
            user = token if defect != "same-user" else "same-account"
            path = request.path
            if path == "/api/v1/users/me":
                return web.json_response({"code": 0, "data": {"id": user}})
            if path == "/api/v1/users/me/models":
                return web.json_response({"code": 0, "data": {"llm_id": MODEL + "@test@OpenAI-API-Compatible"}})
            if path == "/api/v1/business-documents":
                if defect == "http":
                    return web.json_response({"code": 429}, status=429)
                if defect == "envelope":
                    return web.json_response({"code": 422})
                body = await request.json()
                doc_id = body["title"]
                documents[doc_id] = {"document_id": doc_id, "state_version": 1, "protocol": {"questions": []}}
                return web.json_response({"code": 0, "data": documents[doc_id]}, status=201)
            doc_id = request.match_info["tail"].split("/")[0]
            if request.method == "DELETE":
                if defect == "cleanup":
                    return web.json_response({"code": 403}, status=403)
                assert token == "cleanup-admin"
                del documents[doc_id]
                return web.json_response({"code": 0, "data": {}})
            if path.endswith("/commands"):
                body = await request.json()
                assert body["expected_state_version"] == 1
                jobs[doc_id] = {"job_id": doc_id, "status": "RUNNING", "attempt": 1}

                async def work():
                    if defect != "missing-llm":
                        async with llm_session.post(mock.make_url("/v1/chat/completions"), json=llm_body(doc_id)) as response:
                            assert response.status == 200
                            await response.read()
                    if defect == "dead":
                        jobs[doc_id]["status"] = "DEAD"
                        return
                    if defect != "timeout":
                        if defect != "missing-question":
                            documents[doc_id]["protocol"]["questions"] = RESPONSE["questions"]
                        jobs[doc_id]["status"] = "COMPLETED"

                tasks.append(asyncio.create_task(work()))
                return web.json_response({"code": 0, "data": {"job_id": doc_id}}, status=202)
            if path.endswith("/jobs"):
                return web.json_response({"code": 0, "data": [jobs[doc_id]]})
            return web.json_response({"code": 0, "data": documents[doc_id]})

        app = web.Application()
        app.router.add_route("*", "/api/v1/users/{tail:.*}", route)
        app.router.add_route("*", "/api/v1/business-documents", route)
        app.router.add_route("*", "/api/v1/business-documents/{tail:.*}", route)
        async with TestServer(app) as server:
            args = parser().parse_args(
                [
                    "run",
                    "--base-url",
                    str(server.make_url("")).rstrip("/"),
                    "--mock-url",
                    str(mock.make_url("")).rstrip("/"),
                    "--users-file",
                    str(tmp_path / "unused.json"),
                    "--report",
                    str(tmp_path / "report.json"),
                    "--users",
                    str(users),
                    "--ramp-seconds",
                    "0",
                    "--poll-seconds",
                    "0.005",
                    "--job-timeout",
                    "0.1" if defect == "timeout" else "10",
                ]
            )
            try:
                report = await run_load(args, [f"session-{i}" for i in range(users)], "cleanup-admin")
            finally:
                await asyncio.gather(*tasks)
            if defect != "cleanup":
                assert not documents
            return report


async def test_fifty_distinct_users_complete_and_clean_up(tmp_path):
    report = await exercise(tmp_path)
    assert report["status"] == "PASS"
    assert report["scenarios"] == 50 and report["failures"] == 0
    assert report["cleanup_ok"] and report["emulator_evidence_ok"]
    assert len(report["llm_calls"]) == 50
    assert report["http_seconds"]["create"]["count"] == 50
    if path := os.environ.get("BD_LOAD_SELFTEST_REPORT"):
        report["scope"] = "Harness self-test only: fake HTTP API + real HTTP LLM emulator; not application capacity"
        Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("defect", ["http", "envelope", "cleanup", "missing-llm", "missing-question", "dead", "timeout"])
async def test_failures_cannot_produce_a_pass(tmp_path, defect):
    report = await exercise(tmp_path, users=2, defect=defect)
    assert report["status"] == "FAIL"


async def test_reused_identity_is_rejected_before_creating_documents(tmp_path):
    with pytest.raises(ValueError, match="different account"):
        await exercise(tmp_path, users=2, defect="same-user")
