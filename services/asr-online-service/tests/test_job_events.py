from asr_service.jobs.job_events import iter_job_events
from asr_service.jobs.job_models import CreateJobRequest, JobStatus
from asr_service.jobs.job_store import JobStore


def test_job_events_emit_progress_delta_and_final() -> None:
    store = JobStore()
    job = store.create(CreateJobRequest(model_key="t-one", language="ru"))
    job.status = JobStatus.done
    job.stage = "done"
    job.percent = 100
    job.result = {"transcript": "готовый текст"}
    store.update(job)

    events = list(iter_job_events(store, job.id, poll_seconds=0.001, heartbeat_seconds=1))

    assert [event["event"] for event in events] == ["progress", "delta", "final"]
    assert events[1]["text"] == "готовый текст"
    assert events[-1]["transcript"] == "готовый текст"


def test_job_events_emit_terminal_error() -> None:
    store = JobStore()
    job = store.create(CreateJobRequest(model_key="t-one", language="ru"))
    job.status = JobStatus.error
    job.stage = "error"
    job.percent = 100
    job.error = {"error_code": "W-ASR-TEST", "message": "failure"}
    store.update(job)

    events = list(iter_job_events(store, job.id, poll_seconds=0.001, heartbeat_seconds=1))

    assert events[-1] == {
        "event": "error",
        "job_id": job.id,
        "status": "error",
        "code": "W-ASR-TEST",
        "text": "failure",
    }
