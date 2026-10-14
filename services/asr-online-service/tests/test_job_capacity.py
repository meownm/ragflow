from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from asr_service.jobs.job_models import CreateJobRequest, JobStatus
from asr_service.jobs.job_store import JobCapacityError, JobStore
from asr_service.main import app
from asr_service.settings import Settings


def _request() -> CreateJobRequest:
    return CreateJobRequest(model_key="t-one", language="ru")


def test_five_active_jobs_are_admitted_atomically_and_terminal_jobs_release_capacity() -> None:
    store = JobStore(max_active_jobs=5)

    def create_one():
        try:
            return store.create(_request())
        except JobCapacityError:
            return None

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(lambda _: create_one(), range(12)))
    admitted = [job for job in outcomes if job is not None]
    assert len(admitted) == 5
    assert len(outcomes) - len(admitted) == 7

    store.cancel(admitted[0].id)
    replacement = store.create(_request())
    replacement.status = JobStatus.done
    store.update(replacement)
    assert store.create(_request()).status == JobStatus.queued


def test_settings_cap_active_jobs_at_five() -> None:
    assert Settings().max_active_jobs == 5


def test_expired_job_releases_capacity_before_next_admission(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("asr_service.jobs.job_store.time.time", lambda: now[0])
    store = JobStore(ttl_seconds=10, max_active_jobs=1)
    first = store.create(_request())
    now[0] += 11
    assert store.create(_request()).status == JobStatus.queued
    assert first.status == JobStatus.expired


def test_both_creation_routes_reject_sixth_job_and_discard_rejected_upload(monkeypatch, tmp_path) -> None:
    store = JobStore(max_active_jobs=5)
    for _ in range(5):
        store.create(_request())
    previous_store = app.state.job_store
    descriptor = next(item for item in app.state.registry.items if item.key == "t-one")
    previous_available = descriptor.available
    app.state.job_store = store
    descriptor.available = True
    monkeypatch.setattr("asr_service.api.routes_openai.settings.upload_dir", str(tmp_path))
    try:
        with TestClient(app) as client:
            job_response = client.post("/v1/asr/jobs", json=_request().model_dump())
            transcription_response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("sample.wav", b"RIFF", "audio/wav")},
                data={"model": "t-one", "stream": "true"},
            )
    finally:
        app.state.job_store = previous_store
        descriptor.available = previous_available

    assert job_response.status_code == 429
    assert job_response.json()["detail"]["error_code"] == "Q-ASR-CAPACITY-REACHED"
    assert transcription_response.status_code == 429
    assert transcription_response.json()["detail"]["error"]["code"] == "Q-ASR-CAPACITY-REACHED"
    assert list(tmp_path.iterdir()) == []
