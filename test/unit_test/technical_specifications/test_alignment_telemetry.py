"""Recorded attempts: transaction ownership, cancellation and unknown spending."""

import asyncio
import json

import pytest
from peewee import OperationalError

from api.db.db_models import TechnicalSpecificationAlignmentObservation as Observation
from api.technical_specifications import storage
from api.technical_specifications.telemetry import observation
from test.unit_test.technical_specifications import test_alignment_application as scenarios

aligned = scenarios.aligned


def test_metadata_projection_never_serializes_documents_or_provider_text():
    item = {
        "attempt": "a" * 32,
        "created_at": "2026-10-20T02:00:00+03:00",
        "finished_at": "2026-10-20T02:00:01+03:00",
        "status": "FINISHED",
        "private": "secret document",
        "budget": {"calls": 1, "spent_tokens": 15, "reserved_tokens": 0, "elapsed_ms": 20},
        "result": {
            "execution_status": "COMPLETE",
            "report_status": "COMPLETE",
            "reason_code": "private exception",
            "report": {"private": "secret document"},
            "audit": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "backend_completion_known": True,
                "reasoning_token_accounting": "NO_REASONING",
                "provider_duration_ms": 10,
                "api_key": "secret-token",
            },
        },
    }
    result = observation(item)
    assert result["usage_known"] and result["spent_tokens"] == 15 and result["duration_ms"] == 20
    assert result["error_code"] is None and "secret" not in json.dumps(result)
    item["result"]["audit"]["reasoning_token_accounting"] = "NOT_ATTESTED"
    assert not observation(item)["usage_known"] and observation(item)["spent_tokens"] == 15
    item["budget"]["reserved_tokens"] = 10
    assert not observation(item)["usage_known"]
    item["budget"]["reserved_tokens"] = None
    assert observation(item)["reserved_tokens"] is None
    item["reason_code"] = "EXECUTION_LEASE_EXPIRED"
    assert observation(item)["duration_ms"] is None
    item["created_at"] = "2026-10-20T02:00:00"
    assert observation(item) is None


class RecordedAI:
    def __init__(self, hook=None):
        self.calls = 0
        self.hook = hook

    async def check(self, actor, document, basis, *, budget, on_reserved):
        claim = budget.claim(30)
        await on_reserved({"request_id": "r" * 32, "input_sha256": "f" * 64, "api_key": "secret-token"})
        pending = Observation.get()
        assert pending.state == "PROCESSING" and pending.reserved_tokens == 30 and not pending.usage_known
        self.calls += 1
        if self.hook:
            await self.hook()
        budget.settle(10, 5, claim_id=claim)
        return {
            "execution_status": "COMPLETE",
            "report_status": "COMPLETE",
            "quality_status": "PASS",
            "qualifies": False,
            "audit": {"backend_completion_known": True, "reasoning_token_accounting": "NO_REASONING", "prompt_tokens": 10, "completion_tokens": 5, "provider_duration_ms": 5},
        }


@pytest.mark.asyncio
async def test_reservation_precedes_dispatch_and_replay_does_not_duplicate_observation(aligned):
    service, _sources, _ai, document = aligned
    service.ai = ai = RecordedAI()
    result = await service.check("author", "workspace", document["id"], scenarios.request(document))
    saved = Observation.get()
    assert saved.state == "FINISHED" and saved.usage_known and saved.spent_tokens == 15 and saved.reserved_tokens == 0
    assert saved.duration_ms >= 0 and saved.model_duration_ms == 5
    assert result["checks"][0]["request_audit"]["request_id"] == "r" * 32
    assert "secret-token" not in json.dumps(result)
    await service.check("author", "workspace", document["id"], scenarios.request(document))
    assert ai.calls == Observation.select().count() == 1


@pytest.mark.asyncio
async def test_cancel_keeps_unknown_reservation_and_distinct_cancellation_code(aligned):
    service, _sources, _ai, document = aligned

    async def cancel():
        raise asyncio.CancelledError

    service.ai = ai = RecordedAI(cancel)
    with pytest.raises(asyncio.CancelledError):
        await service.check("author", "workspace", document["id"], scenarios.request(document))
    saved = Observation.get()
    assert saved.error_code == "EXECUTION_CANCELLED" and saved.state == "INCOMPLETE"
    assert saved.reserved_tokens == 30 and saved.spent_tokens == 0 and not saved.usage_known
    await service.check("author", "workspace", document["id"], scenarios.request(document))
    assert ai.calls == Observation.select().count() == 1


@pytest.mark.asyncio
async def test_failed_observation_write_rolls_back_acceptance_before_model(aligned, store, monkeypatch):
    service, _sources, ai, document = aligned

    def fail(*args, **kwargs):
        raise OperationalError("synthetic telemetry failure")

    monkeypatch.setattr(Observation, "create", fail)
    with pytest.raises(OperationalError):
        await service.check("author", "workspace", document["id"], scenarios.request(document))
    saved = store.alignment_document("author", "workspace", document["id"])
    assert saved["version"] == document["version"] and saved["alignment"] == []
    assert ai.calls == 0 and Observation.select().count() == 0


@pytest.mark.asyncio
async def test_failed_terminal_observation_cannot_publish_result_or_retry_paid_call(aligned, store, monkeypatch):
    service, _sources, _ai, document = aligned
    service.ai = ai = RecordedAI()
    actual = storage._record_alignment_observations

    def fail_terminal(before, after):
        if after[-1]["status"] == "FINISHED":
            raise OperationalError("synthetic terminal telemetry failure")
        return actual(before, after)

    monkeypatch.setattr(storage, "_record_alignment_observations", fail_terminal)
    with pytest.raises(OperationalError):
        await service.check("author", "workspace", document["id"], scenarios.request(document))
    saved = store.alignment_document("author", "workspace", document["id"])["alignment"][0]
    assert saved["status"] == "PROCESSING" and "result" not in saved
    assert Observation.get().state == "PROCESSING" and Observation.get().reserved_tokens == 30
    await service.check("author", "workspace", document["id"], scenarios.request(document))
    assert ai.calls == 1
