"""Disposable storage: authorization, replay and source/document freshness."""

import asyncio

import pytest

from api.source_workbench.errors import SourceWorkspaceError
from api.technical_specifications.alignment_application import AlignmentApplication
from api.technical_specifications.domain import SpecificationError, empty_sections


async def run(method, *args, **kwargs):
    return method(*args, **kwargs)


class Sources:
    def __init__(self):
        self.calls = 0
        self.revision = "proof-1"
        self.allowed = True

    def read_many(self, actor, ids, **_options):
        self.calls += 1
        if not self.allowed:
            raise SourceWorkspaceError("PERMISSION_DENIED", "Нет доступа", 403)
        return (
            {"bt": {"document_id": "bt", "revision_id": "bt-1", "agreement_event_id": "agreed-1", "content_hash": "hash", "revision": self.revision, "body_markdown": "Требуется отмена заявки."}}
            if ids == ["bt"]
            else {}
        )

    def validate(self, actor, refs, *, fresh=False, lock=False):
        rows = self.read_many(actor, [ref["document_id"] for ref in refs])
        if fresh and any(ref["revision"] != self.revision for ref in refs):
            raise SourceWorkspaceError("SOURCE_CHANGED", "БТ изменились", 409)
        return rows


class AI:
    def __init__(self):
        self.calls = 0
        self.hook = None

    async def check(self, actor, document, basis, *, budget, on_reserved):
        self.calls += 1
        assert basis["sources"][0]["agreement_event_id"] == "agreed-1"
        if self.hook:
            await self.hook()
        return {"execution_status": "COMPLETE", "report_status": "COMPLETE", "quality_status": "PASS", "qualifies": True}


@pytest.fixture
def aligned(store):
    doc = store.create("author", "workspace", "ТЗ", empty_sections())
    sources, ai = Sources(), AI()
    return AlignmentApplication(store, sources, ai, run), sources, ai, doc


def request(doc):
    return {"version": doc["version"], "request_id": "check-1", "business_document_ids": ["bt"]}


@pytest.mark.asyncio
async def test_report_persists_without_content_revision_and_replay_consumes_no_model(aligned, store):
    service, _sources, ai, doc = aligned
    result = await service.check("author", "workspace", doc["id"], request(doc))
    assert result["revision"] == 1 and result["checks"][0]["qualifies"]
    assert store.get("author", "workspace", doc["id"])["sections"] == doc["sections"]
    again = await service.check("author", "workspace", doc["id"], request(doc))
    assert again == result and ai.calls == 1
    with pytest.raises(SpecificationError) as error:
        await service.check("author", "workspace", doc["id"], {**request(doc), "business_document_ids": ["other"]})
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["reader", "stranger"])
async def test_authorization_precedes_source_and_model_access(aligned, actor):
    service, sources, ai, doc = aligned
    with pytest.raises((SpecificationError, SourceWorkspaceError)):
        await service.check(actor, "workspace", doc["id"], request(doc))
    assert ai.calls == sources.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source", "document", "permission"])
async def test_race_during_inference_never_publishes_current_pass(aligned, store, change):
    service, sources, ai, doc = aligned

    async def mutate():
        if change == "source":
            sources.revision = "proof-2"
        elif change == "permission":
            sources.allowed = False
        else:
            current = store.get("author", "workspace", doc["id"])
            store.change("author", "workspace", doc["id"], current["version"], lambda value: value["sections"].update({"4": "Новая правка"}), "edit")

    ai.hook = mutate
    with pytest.raises((SpecificationError, SourceWorkspaceError)):
        await service.check("author", "workspace", doc["id"], request(doc))
    result = await service.get("author", "workspace", doc["id"])
    assert not result["checks"][0]["qualifies"]
    if change == "document":
        assert store.get("author", "workspace", doc["id"])["sections"]["4"] == "Новая правка"


@pytest.mark.asyncio
async def test_later_source_change_or_restore_invalidates_report_on_read(aligned, store):
    service, sources, _ai, doc = aligned
    await service.check("author", "workspace", doc["id"], request(doc))
    sources.revision = "proof-2"
    assert not (await service.get("author", "workspace", doc["id"]))["checks"][0]["qualifies"]
    sources.revision = "proof-1"
    current = store.get("author", "workspace", doc["id"])
    store.change("author", "workspace", doc["id"], current["version"], lambda value: value["sections"].update({"8": "Восстановленная редакция"}), "restore")
    assert not (await service.get("author", "workspace", doc["id"]))["checks"][0]["current"]


@pytest.mark.asyncio
async def test_cancellation_does_not_leave_request_processing(aligned, store):
    service, _sources, ai, doc = aligned

    async def cancel():
        raise asyncio.CancelledError

    ai.hook = cancel
    with pytest.raises(asyncio.CancelledError):
        await service.check("author", "workspace", doc["id"], request(doc))
    saved = store.alignment_document("author", "workspace", doc["id"])
    assert saved["alignment"][0]["status"] == "INCOMPLETE"
    assert saved["sections"] == doc["sections"]


@pytest.mark.asyncio
async def test_expired_worker_cannot_publish_or_retry_paid_call(aligned, store):
    from datetime import UTC, datetime, timedelta

    from api.db.db_models import TechnicalSpecificationAlignmentObservation

    service, _sources, ai, doc = aligned

    async def expire():
        def age(payload):
            payload["alignment"][0]["created_at"] = (datetime.now(UTC) - timedelta(minutes=7)).isoformat()

        store.change("author", "workspace", doc["id"], None, age, "synthetic-crash", latest=True)
        recovered = await service.get("reader", "workspace", doc["id"])
        assert recovered["checks"][0]["reason_code"] == "EXECUTION_LEASE_EXPIRED"
        assert recovered["revision"] == doc["revision"]
        assert recovered["checks"][0]["budget"]["reserved_calls"] == 1

    ai.hook = expire
    with pytest.raises(SpecificationError) as error:
        await service.check("author", "workspace", doc["id"], request(doc))
    assert error.value.code == "ALIGNMENT_ATTEMPT_STALE"
    replay = await service.check("author", "workspace", doc["id"], request(doc))
    assert ai.calls == 1 and not replay["checks"][0]["qualifies"]
    assert replay["checks"][0]["status"] == "INCOMPLETE"
    assert replay["document"]["sections"] == doc["sections"]
    observation = TechnicalSpecificationAlignmentObservation.get()
    assert observation.state == "INCOMPLETE" and observation.error_code == "EXECUTION_LEASE_EXPIRED"
    assert not observation.usage_known and TechnicalSpecificationAlignmentObservation.select().count() == 1


@pytest.mark.asyncio
async def test_comments_during_inference_do_not_invalidate_content_check(aligned, store):
    service, _sources, ai, doc = aligned

    async def comment():
        current = store.get("author", "workspace", doc["id"])
        store.change("author", "workspace", doc["id"], current["version"], lambda value: value["messages"].append({"id": "discussion", "role": "user", "text": "Обсуждение"}), "chat")

    ai.hook = comment
    result = await service.check("author", "workspace", doc["id"], request(doc))
    assert result["checks"][0]["qualifies"] and result["revision"] == 1


@pytest.mark.asyncio
async def test_replay_after_access_revocation_hides_source_metadata(aligned):
    service, sources, _ai, doc = aligned
    await service.check("author", "workspace", doc["id"], request(doc))
    sources.allowed = False
    result = await service.check("author", "workspace", doc["id"], request(doc))
    assert result["checks"] == [{"id": "check-1", "current": False, "qualifies": False, "reason_code": "SOURCE_UNAVAILABLE"}]


@pytest.mark.asyncio
async def test_normal_document_projection_does_not_bypass_source_acl(aligned, store):
    service, sources, _ai, doc = aligned
    await service.check("author", "workspace", doc["id"], request(doc))
    sources.allowed = False
    assert "alignment" not in store.get("author", "workspace", doc["id"])
    report = await service.get("author", "workspace", doc["id"])
    assert "alignment" not in report["document"]
    assert "references" not in report["checks"][0]


@pytest.mark.asyncio
async def test_admission_reserves_report_capacity_before_paid_inference(aligned, store, monkeypatch):
    from api.technical_specifications import storage

    service, _sources, ai, doc = aligned
    monkeypatch.setattr(storage, "MAX_PROJECTION_BYTES", storage.ALIGNMENT_RESULT_BYTES - 1)
    with pytest.raises(SpecificationError) as error:
        await service.check("author", "workspace", doc["id"], request(doc))
    assert error.value.code == "DOCUMENT_TOO_LARGE" and ai.calls == 0
    assert store.alignment_document("author", "workspace", doc["id"])["alignment"] == []
