"""Drive an isolated evaluation queue within the job's original recovery budget."""

import time

from business_documents.domain.job_recovery import MAX_AI_SECONDS
from common.time_utils import current_timestamp


def wait_for_job(worker, job_id):
    from api.db.db_models import BusinessDocumentJob

    job = BusinessDocumentJob.get_by_id(job_id)
    deadline = time.monotonic() + MAX_AI_SECONDS
    while job.status not in {"COMPLETED", "DEAD"}:
        remaining = deadline - time.monotonic()
        recovery = (job.result or {}).get("recovery")
        if recovery is not None:
            remaining = min(remaining, recovery["started_at"] + MAX_AI_SECONDS - current_timestamp() / 1000)
        if remaining <= 0:
            raise TimeoutError("Live job exceeded its existing recovery budget")
        if job.status not in {"PENDING", "RETRY"} or job.attempt >= job.max_attempts:
            raise AssertionError({"check_id": "job_claimable", "status": job.status, "attempt": job.attempt})
        delay = max(0, job.available_at - current_timestamp()) / 1000
        if delay >= remaining:
            raise TimeoutError("Live job retry cannot fit its existing recovery budget")
        if delay:
            time.sleep(min(delay, 1))
            job = BusinessDocumentJob.get_by_id(job.id)
            continue
        previous_attempt = job.attempt
        if not worker.run_once():
            raise AssertionError({"check_id": "job_claimed", "job_id": job.id})
        job = BusinessDocumentJob.get_by_id(job.id)
        if job.attempt <= previous_attempt:
            raise AssertionError({"check_id": "requested_job_processed", "job_id": job.id})
    return job
