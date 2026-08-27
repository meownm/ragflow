import subprocess

from asr_service.jobs.job_models import CreateJobRequest, JobStatus
from asr_service.jobs.job_queue import JobQueue
from asr_service.jobs.job_store import JobStore
from asr_service.jobs.worker import Worker
from asr_service.models.model_manager import ModelManager
from asr_service.models.model_registry import default_registry
from asr_service.settings import Settings


def test_windows_smoke_job_ffmpeg_fail(monkeypatch):
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.load", lambda self: setattr(self, "_loaded", True))
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.transcribe", lambda self, audio_path, language: {"transcript": "never", "segments": []})

    def _fail(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=["ffmpeg", "-i", "input.wav", "out.wav"])

    monkeypatch.setattr("asr_service.jobs.worker.preprocess_audio", _fail)

    store = JobStore()
    queue = JobQueue()
    worker = Worker(store=store, queue=queue, registry=default_registry(), manager=ModelManager(), settings=Settings())

    job = store.create(CreateJobRequest(model_key="whisper-large-v3", language="ru", source_uri="memory://sample.wav"))
    queue.put(job.id)
    worker._process_job(job.id)

    updated = store.get(job.id)
    assert updated is not None
    assert updated.status == JobStatus.error
    assert updated.error["error_code"] == "W-ASR-FFMPEG-FAILED"
