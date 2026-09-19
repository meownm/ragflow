import json
import time
from collections.abc import Iterator

from asr_service.jobs.job_models import JobStatus
from asr_service.jobs.job_store import JobStore


def iter_job_events(
    store: JobStore,
    job_id: str,
    *,
    poll_seconds: float,
    heartbeat_seconds: float,
) -> Iterator[dict]:
    """Yield progress and transcript changes until an ASR job is terminal."""

    previous_snapshot: tuple[str, str, int] | None = None
    previous_text = ""
    last_emit = time.monotonic()

    while True:
        job = store.get(job_id)
        if job is None:
            yield {"event": "error", "code": "Q-ASR-JOB-NOT-FOUND", "text": "ASR job was not found."}
            return

        snapshot = (job.status.value, job.stage, job.percent)
        if snapshot != previous_snapshot:
            yield {
                "event": "progress",
                "job_id": job.id,
                "status": job.status.value,
                "stage": job.stage,
                "percent": job.percent,
            }
            previous_snapshot = snapshot
            last_emit = time.monotonic()

        current_text = str((job.result or {}).get("transcript", ""))
        if current_text and current_text != previous_text:
            delta = current_text[len(previous_text) :] if current_text.startswith(previous_text) else current_text
            yield {
                "event": "delta",
                "job_id": job.id,
                "text": delta,
                "transcript": current_text,
                "percent": job.percent,
            }
            previous_text = current_text
            last_emit = time.monotonic()

        if job.status == JobStatus.done:
            yield {
                "event": "final",
                "job_id": job.id,
                "text": current_text,
                "transcript": current_text,
                "percent": 100,
            }
            return

        if job.status in {JobStatus.error, JobStatus.canceled, JobStatus.expired}:
            error = job.error or {}
            yield {
                "event": "error",
                "job_id": job.id,
                "status": job.status.value,
                "code": error.get("error_code", f"asr_{job.status.value}"),
                "text": error.get("message", f"ASR job ended with status '{job.status.value}'."),
            }
            return

        now = time.monotonic()
        if now - last_emit >= heartbeat_seconds:
            yield {"event": "heartbeat", "job_id": job.id, "status": job.status.value}
            last_emit = now

        time.sleep(poll_seconds)


def iter_job_sse(
    store: JobStore,
    job_id: str,
    *,
    poll_seconds: float,
    heartbeat_seconds: float,
) -> Iterator[str]:
    for event in iter_job_events(
        store,
        job_id,
        poll_seconds=poll_seconds,
        heartbeat_seconds=heartbeat_seconds,
    ):
        event_name = event.get("event", "message")
        yield f"event: {event_name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
