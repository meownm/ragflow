"""Recovery cannot change author decisions, restart budgets or publish candidates."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from test.unit_test.api.apps.business_documents.test_structured_changes import PlanAdapter, request_plan, database as structured_database
from api.apps.business_documents.ai import BusinessDocumentAI, RAGFlowLLMAdapter
from api.apps.business_documents import recovery as recovery_module, worker as worker_module
from api.apps.business_documents.recovery import AIRecovery, is_transient_failure, retry_delay_ms
from api.apps.business_documents.worker import BusinessDocumentJobQueue, BusinessDocumentWorker
from api.apps.business_documents.runtime import document_queries
from api.db.db_models import BusinessDocumentJob, BusinessDocumentRevision, BusinessDocumentJobStreamEvent
from business_documents.application.errors import BusinessDocumentError
from business_documents.domain.hashing import stable_hash


@pytest.fixture()
def database():
    yield from structured_database.__wrapped__()


def _http_error(status, retry_after=None):
    response = httpx.Response(status, headers={"retry-after": retry_after} if retry_after else {}, request=httpx.Request("POST", "https://provider.invalid"))
    return httpx.HTTPStatusError("Provider failure", request=response.request, response=response)


@pytest.mark.parametrize(
    "error,transient",
    [
        (TimeoutError(), True),
        (ConnectionError(), True),
        (_http_error(429), True),
        (_http_error(503), True),
        (_http_error(401), False),
        (_http_error(403), False),
        (_http_error(400), False),
        (RuntimeError("bug"), False),
        (BusinessDocumentError("EVIDENCE_RETRIEVAL_FAILED", "unavailable", 503), True),
        (BusinessDocumentError("EVIDENCE_SNAPSHOT_CORRUPT", "corrupt", 500), False),
        (BusinessDocumentError("STALE_AI_RESULT", "stale", 409), False),
        (BusinessDocumentError("MODEL_CONTEXT_TOO_LARGE", "context", 422), False),
    ],
)
def test_only_known_transient_failures_are_retryable(error, transient):
    assert is_transient_failure(error) is transient


@pytest.mark.parametrize(
    "value,expected",
    [(None, 5000), ("30", 30000), ("0", 5000), ("bad", 5000), ("nan", 5000), ("inf", 5000), ("-inf", 5000), ("-1", 5000), ("172800", 172800000)],
)
def test_provider_retry_after_is_respected(value, expected):
    assert retry_delay_ms(_http_error(429, value), 5000) == expected


def test_provider_retry_after_preserves_long_http_date(monkeypatch):
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery_module, "datetime", SimpleNamespace(now=lambda _tz: now))
    assert retry_delay_ms(_http_error(429, "Mon, 19 Oct 2026 00:00:00 GMT"), 5000) == 172800000


def test_large_finite_retry_after_does_not_overflow():
    assert retry_delay_ms(_http_error(429, "1e308"), 5000) > 900000


@pytest.mark.parametrize("status,expected", [(401, "DEAD"), (429, "RETRY"), (503, "RETRY")])
def test_worker_classifies_provider_failure_without_changing_revision(database, status, expected):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    adapter.generate = lambda *_args, **_kwargs: (_ for _ in ()).throw(_http_error(status, "30"))
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == expected and job.attempt == 1
    if expected == "RETRY":
        from common.time_utils import current_timestamp

        assert job.available_at >= current_timestamp() + 28000
    assert BusinessDocumentRevision.select().count() == 1


def test_format_repair_receives_error_and_preserves_complete_original_context(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def first_invalid(tenant, system, payload, *, timeout_seconds=None):
        result = generate(tenant, system, payload, timeout_seconds=timeout_seconds)
        if payload["job_input"]["task_type"] == "PLAN_CHANGES" and "format_repair" not in payload:
            result["operations"][0]["type"] = "UNKNOWN_OPERATION"
        return result

    adapter.generate = first_invalid
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    assert len(adapter.calls) == 3 and job.attempt == 1
    assert adapter.calls[0]["job_input"] == adapter.calls[1]["job_input"]
    feedback = adapter.calls[1]["format_repair"]
    assert feedback["error"]["code"] == "INVALID_CHANGE_PLAN"
    assert "type" in feedback["error"]["details"]["path"]
    assert feedback["previous_response"]["operations"][0]["type"] == "UNKNOWN_OPERATION"
    assert job.result["output"]["change_plan"] == plan
    audit = job.result["execution"]["ai"]
    assert len(audit["recovery"]) == 1
    from test.evals.business_documents.test_live_model_quality import _aggregate_ai_audits

    assert _aggregate_ai_audits([audit])["execution_count"] == 3


def test_second_invalid_format_is_preserved_without_verification_or_revision(database):
    requested, plan = request_plan()
    plan["operations"].append(deepcopy(plan["operations"][0]))
    adapter = PlanAdapter(plan, None)
    worker = BusinessDocumentWorker(ai=BusinessDocumentAI(adapter))
    assert worker.run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED" and not job.error
    assert job.result["output"]["unapplied_changes"]
    assert job.result["output"]["warnings"][0]["code"] == "CHANGE_FORMAT_NOT_APPLIED"
    assert len(adapter.calls) == 2 and not worker.run_once()
    assert BusinessDocumentRevision.select().count() == 1


@pytest.mark.parametrize("response", ["Now let me write the final JSON plan.", '["planning", "notes"]'])
def test_direct_apply_repairs_non_object_response_before_verification_and_save(database, response):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def first_invalid(tenant, system, payload, *, timeout_seconds=None):
        result = generate(tenant, system, payload, timeout_seconds=timeout_seconds)
        if payload["job_input"]["task_type"] == "PLAN_CHANGES" and "format_repair" not in payload:
            return response
        return result

    adapter.generate = first_invalid
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    assert adapter.calls[0]["job_input"] == adapter.calls[1]["job_input"]
    assert adapter.calls[1]["format_repair"]["previous_response"] == response
    assert adapter.calls[1]["format_repair"]["error"]["code"] == "INVALID_AI_JSON"
    assert job.result["output"]["change_plan"] == plan
    assert job.result["output"]["requirement_coverage"]["status"] == "PASS"
    assert BusinessDocumentRevision.select().count() == 2


def test_direct_apply_preserves_second_non_object_response_without_saving(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def always_invalid(tenant, system, payload, *, timeout_seconds=None):
        generate(tenant, system, payload, timeout_seconds=timeout_seconds)
        return "Still planning the final JSON."

    adapter.generate = always_invalid
    worker = BusinessDocumentWorker(ai=BusinessDocumentAI(adapter))
    assert worker.run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    assert job.result["output"]["unapplied_changes"] == "Still planning the final JSON."
    assert job.result["output"]["warnings"][0]["code"] == "CHANGE_FORMAT_NOT_APPLIED"
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "PLAN_CHANGES"]
    assert BusinessDocumentRevision.select().count() == 1
    assert not worker.run_once()


def test_second_invalid_verdict_saves_advice_without_claiming_a_pass(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def invalid_verdict(tenant, system, payload, *, timeout_seconds=None):
        result = generate(tenant, system, payload, timeout_seconds=timeout_seconds)
        return {"status": "FAIL", "issues": []} if payload["job_input"]["task_type"] == "VERIFY_REQUIREMENT_COVERAGE" else result

    adapter.generate = invalid_verdict
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED" and not job.error
    assert job.result["output"]["requirement_coverage"]["status"] == "UNAVAILABLE"
    assert len(adapter.calls) == 3
    assert BusinessDocumentRevision.select().count() == 2


def test_checkpointed_candidate_is_not_a_published_revision_or_preview(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def crash_before_verification(*args, **kwargs):
        if args[2]["job_input"]["task_type"] == "VERIFY_REQUIREMENT_COVERAGE":
            raise SystemExit("Crash after candidate checkpoint")
        return generate(*args, **kwargs)

    adapter.generate = crash_before_verification
    with pytest.raises(SystemExit):
        BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "RUNNING" and job.result["recovery"]["candidate"]
    from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR

    view = document_queries.get_document(job.document_id, AUTHOR)
    assert view["current_revision"]["revision_id"] == job.base_revision_id
    assert "result" not in view["latest_job"]
    assert not BusinessDocumentJobStreamEvent.select().where(BusinessDocumentJobStreamEvent.event_type == "preview_ready").exists()


def test_crashed_worker_resumes_checkpointed_candidate_within_original_budget(database, monkeypatch):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def crash_before_verification(tenant, system, payload, *, timeout_seconds=None):
        if payload["job_input"]["task_type"] == "VERIFY_REQUIREMENT_COVERAGE":
            raise SystemExit("Simulated process exit before verification")
        return generate(tenant, system, payload, timeout_seconds=timeout_seconds)

    adapter.generate = crash_before_verification
    with pytest.raises(SystemExit):
        BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    state = deepcopy(job.result["recovery"])
    assert job.status == "RUNNING" and state["candidate"]
    assert BusinessDocumentRevision.select().count() == 1

    # First idle recovery poll after the crashed worker's default lease expires.
    resumed_at = job.lease_expires_at + 2000
    monkeypatch.setattr(worker_module, "current_timestamp", lambda: resumed_at)
    monkeypatch.setattr(worker_module, "AIRecovery", lambda *args, **kwargs: AIRecovery(*args, **kwargs, clock=lambda: resumed_at / 1000))
    assert BusinessDocumentJobQueue.recover_stale(now_ms=resumed_at) == (1, 0)

    def resume_verification(tenant, system, payload, *, timeout_seconds=None):
        resumed_state = BusinessDocumentJob.get_by_id(job.id).result["recovery"]
        assert resumed_state["started_at"] == state["started_at"]
        assert resumed_state["calls"] == 3
        assert resumed_state["candidate"] == state["candidate"]
        assert timeout_seconds == pytest.approx(900 - (resumed_at / 1000 - state["started_at"]))
        return generate(tenant, system, payload, timeout_seconds=timeout_seconds)

    adapter.generate = resume_verification
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once(now_ms=resumed_at)
    completed = BusinessDocumentJob.get_by_id(job.id)
    assert completed.status == "COMPLETED", completed.error
    assert completed.attempt == 2
    assert [call["job_input"]["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    assert completed.result["output"]["change_plan"] == plan
    assert BusinessDocumentRevision.select().count() == 2


def test_renewed_default_lease_protects_running_job_and_rejects_old_attempt(database, monkeypatch):
    requested, _plan = request_plan()
    worker = BusinessDocumentWorker(worker_id="worker")
    job = BusinessDocumentJobQueue.claim(worker.worker_id, lease_ms=worker.lease_ms)
    assert job.id == requested["job_id"]
    original_expiry = job.lease_expires_at
    clock = SimpleNamespace(now=original_expiry - worker.lease_ms // 3)
    monkeypatch.setattr(worker_module, "current_timestamp", lambda: clock.now)
    assert BusinessDocumentJobQueue.renew(job.id, worker.worker_id, job.lease_token, lease_ms=worker.lease_ms)
    clock.now = original_expiry + 2000
    assert BusinessDocumentJobQueue.recover_stale(now_ms=clock.now) == (0, 0)
    assert BusinessDocumentJobQueue.claim("other-worker", now_ms=clock.now) is None
    BusinessDocumentJobQueue.checkpoint_ai(job, worker.worker_id, AIRecovery().state)

    clock.now = BusinessDocumentJob.get_by_id(job.id).lease_expires_at + 2000
    assert BusinessDocumentJobQueue.recover_stale(now_ms=clock.now) == (1, 0)
    replacement = BusinessDocumentJobQueue.claim(worker.worker_id, now_ms=clock.now)
    assert replacement.id == job.id and replacement.lease_token != job.lease_token
    with pytest.raises(BusinessDocumentError) as caught:
        BusinessDocumentJobQueue.checkpoint_ai(job, worker.worker_id, AIRecovery().state)
    assert caught.value.code == "JOB_LEASE_LOST"


@pytest.mark.parametrize("damage", ["deadline", "calls", "snapshot"])
def test_restart_cannot_reset_limits_or_replace_snapshot(database, damage):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def interrupted_generation(*args, **kwargs):
        result = generate(*args, **kwargs)
        if len(adapter.calls) == 1:
            raise TimeoutError("Generation interrupted")
        return result

    adapter.generate = interrupted_generation
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    state = deepcopy(job.result["recovery"])
    expected = {"deadline": "AI_TIME_BUDGET_EXCEEDED", "calls": "AI_CALL_BUDGET_EXCEEDED", "snapshot": "INVALID_JOB_SNAPSHOT"}[damage]
    if damage == "deadline":
        state["started_at"] = 0
    elif damage == "calls":
        state["calls"] = 6
    else:
        state["snapshot_hash"] = stable_hash({"other": "snapshot"})
    BusinessDocumentJob.update(result={"recovery": state}).where(BusinessDocumentJob.id == job.id).execute()
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "DEAD" and job.error["code"] == expected
    assert len(adapter.calls) == 1
    assert BusinessDocumentRevision.select().count() == 1


def test_lease_loss_after_generation_stops_before_verifier(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def lose_lease(*args, **kwargs):
        result = generate(*args, **kwargs)
        BusinessDocumentJob.update(lease_expires_at=0).where(BusinessDocumentJob.id == requested["job_id"]).execute()
        return result

    adapter.generate = lose_lease
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    assert len(adapter.calls) == 1 and BusinessDocumentRevision.select().count() == 1
    assert BusinessDocumentJob.get_by_id(requested["job_id"]).status == "RUNNING"


def test_cancelled_call_has_no_repair_or_publication(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    count = 0

    def cancel(*_args, **_kwargs):
        nonlocal count
        count += 1
        raise asyncio.CancelledError()

    adapter.generate = cancel
    with pytest.raises(asyncio.CancelledError):
        BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert count == job.result["recovery"]["calls"] == 1
    assert job.status == "RUNNING" and BusinessDocumentRevision.select().count() == 1


def test_lease_guard_rejects_checkpoint_from_an_old_worker(database):
    requested, _plan = request_plan()
    job = BusinessDocumentJobQueue.claim("current")
    assert job.id == requested["job_id"]
    with pytest.raises(BusinessDocumentError) as caught:
        BusinessDocumentJobQueue.checkpoint_ai(job, "old-worker", AIRecovery().state)
    assert caught.value.code == "JOB_LEASE_LOST"
    assert not BusinessDocumentJob.get_by_id(job.id).result


@pytest.mark.parametrize("recorded", [False, True])
def test_adapter_deadline_cancels_active_request(monkeypatch, recorded):
    from test.evals.business_documents.metric_placement import RecordedModel

    cancelled = []
    loops = []

    async def slow(*_args):
        loops.append(asyncio.get_running_loop())
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.append(True)

    adapter = RAGFlowLLMAdapter()
    monkeypatch.setattr(adapter, "async_generate", slow)
    ai = BusinessDocumentAI(RecordedModel(adapter) if recorded else adapter)
    clock = SimpleNamespace(now=0)
    recovery = AIRecovery(clock=lambda: clock.now)
    clock.now = 899.99
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(TimeoutError):
            executor.submit(ai._generate, "tenant", "system", {"job_input": {"task_type": "PLAN_CHANGES"}}, "generation", recovery).result()
    assert cancelled == [True]
    assert len(loops) == 1 and loops[0].is_closed()


def test_elapsed_budget_stops_after_call_before_any_next_step():
    clock = SimpleNamespace(now=0)
    recovery = AIRecovery(clock=lambda: clock.now)
    assert recovery.before_call() == 900
    clock.now = 901
    with pytest.raises(BusinessDocumentError) as caught:
        recovery.after_call("generation", None)
    assert caught.value.code == "AI_TIME_BUDGET_EXCEEDED"


@pytest.mark.parametrize("retry_after", ["3600", "172800", "1e308", "Mon, 19 Oct 2099 00:00:00 GMT"])
def test_retry_after_cannot_keep_job_pending_beyond_deadline(database, retry_after):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    adapter.generate = lambda *_args, **_kwargs: (_ for _ in ()).throw(_http_error(429, retry_after))
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "DEAD" and job.error["code"] == "AI_TIME_BUDGET_EXCEEDED"
    assert job.result["recovery"]["calls"] == 1


def test_repair_budget_is_not_reset_after_transport_failure(database):
    requested, plan = request_plan()
    adapter = PlanAdapter(plan, None)
    generate = adapter.generate

    def invalid_then_timeout(tenant, system, payload, *, timeout_seconds=None):
        generate(tenant, system, payload, timeout_seconds=timeout_seconds)
        if len(adapter.calls) == 2:
            raise TimeoutError("Interrupted format repair")
        invalid = deepcopy(plan)
        invalid["operations"][0]["type"] = "UNKNOWN_OPERATION"
        return invalid

    adapter.generate = invalid_then_timeout
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "RETRY" and job.result["recovery"]["calls"] == 2
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), retry_base_ms=0).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED" and job.result["output"]["unapplied_changes"]
    assert len(adapter.calls) == 3 and len(job.result["execution"]["ai"]["recovery"]) == 2
    assert adapter.calls[1]["format_repair"] == adapter.calls[2]["format_repair"]
    assert BusinessDocumentRevision.select().count() == 1


def test_stream_repair_clears_preliminary_sections_before_validation(database):
    import json

    requested, plan = request_plan()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    payload = {**job.payload, "preview_only": True}
    BusinessDocumentJob.update(payload=payload).where(BusinessDocumentJob.id == job.id).execute()

    class StreamingAdapter(PlanAdapter):
        def generate_stream(self, tenant, system, payload, on_chunk, *, timeout_seconds=None):
            valid = self.generate(tenant, system, payload)
            valid["schema_version"] = "wrong"
            raw = json.dumps(valid)
            on_chunk(raw)
            return raw

    adapter = StreamingAdapter(plan, None)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED", job.error
    events = list(BusinessDocumentJobStreamEvent.select().where(BusinessDocumentJobStreamEvent.job_id == job.id).order_by(BusinessDocumentJobStreamEvent.sequence))
    kinds = [event.event_type for event in events]
    assert kinds.index("section_preview") < kinds.index("retry") < kinds.index("preview_ready")
    assert next(event.payload for event in events if event.event_type == "retry") == {"reason": "format_repair"}
    assert BusinessDocumentRevision.select().count() == 1
