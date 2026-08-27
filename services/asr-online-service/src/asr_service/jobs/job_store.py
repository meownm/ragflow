import time
import uuid
from threading import Lock

from .job_models import CreateJobRequest, Job, JobStatus


class JobStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._jobs: dict[str, tuple[Job, float]] = {}
        self._lock = Lock()
        self._ttl = ttl_seconds

    def create(self, request: CreateJobRequest) -> Job:
        job = Job(
            id=str(uuid.uuid4()),
            model_key=request.model_key,
            language=request.language,
            source_uri=request.source_uri,
            options=request.options,
        )
        with self._lock:
            self._jobs[job.id] = (job, time.time())
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            pair = self._jobs.get(job_id)
            if not pair:
                return None
            job, created_at = pair
            self._expire_if_needed(job, created_at)
            return job
    _TERMINAL = frozenset({JobStatus.done, JobStatus.error, JobStatus.canceled, JobStatus.expired})
    _ACTIVE = frozenset({JobStatus.queued, JobStatus.processing})

    def _expire_if_needed(self, job: Job, created_at: float) -> None:
        if time.time() - created_at > self._ttl and job.status in self._ACTIVE:
            job.status = JobStatus.expired
            job.stage = "expired"
            job.percent = 100

    def reap_expired(self) -> None:
        with self._lock:
            to_delete = []
            for job_id, (job, created_at) in self._jobs.items():
                self._expire_if_needed(job, created_at)
                if job.status in self._TERMINAL and time.time() - created_at > self._ttl:
                    to_delete.append(job_id)
            for job_id in to_delete:
                del self._jobs[job_id]

    def update(self, job: Job) -> None:
        with self._lock:
            if job.id in self._jobs:
                _, created_at = self._jobs[job.id]
                self._jobs[job.id] = (job, created_at)

    def cancel(self, job_id: str) -> Job | None:
        with self._lock:
            pair = self._jobs.get(job_id)
            if not pair:
                return None
            job, created_at = pair
            self._expire_if_needed(job, created_at)
            job.cancel_requested = True
            if job.status == JobStatus.queued:
                job.status = JobStatus.canceled
                job.stage = "canceled"
                job.percent = 100
            return job
