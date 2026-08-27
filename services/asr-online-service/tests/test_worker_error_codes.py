import subprocess
import time

from asr_service.jobs.job_models import CreateJobRequest, JobStatus
from asr_service.jobs.job_queue import JobQueue
from asr_service.jobs.job_store import JobStore
from asr_service.jobs.worker import Worker
from asr_service.models.model_manager import ModelManager
from asr_service.models.model_registry import default_registry
from asr_service.pipeline.preprocess import ToolInvocationError
from asr_service.settings import Settings


def _settings() -> Settings:
    return Settings(ASR_JOB_TTL_SECONDS=5, ASR_OLLAMA_BASE_URL="http://localhost:11434")


def _wait_status(store: JobStore, job_id: str, status: JobStatus) -> None:
    for _ in range(50):
        current = store.get(job_id)
        if current and current.status == status:
            return
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach status {status}")


def test_worker_maps_ffmpeg_not_found(monkeypatch):
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.load", lambda self: setattr(self, "_loaded", True))
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.transcribe", lambda self, audio_path, language: {"transcript": "ok", "segments": []})

    def _fail(*args, **kwargs):
        raise ToolInvocationError(
            tool="ffmpeg",
            tool_path="ffmpeg",
            cmd=["ffmpeg", "-i", "input.wav", "out.wav"],
            returncode=None,
            stderr="",
            cause=FileNotFoundError("ffmpeg not found"),
        )

    monkeypatch.setattr("asr_service.jobs.worker.preprocess_audio", _fail)

    store = JobStore()
    queue = JobQueue()
    worker = Worker(store=store, queue=queue, registry=default_registry(), manager=ModelManager(), settings=_settings())
    worker.start()
    try:
        job = store.create(CreateJobRequest(model_key="whisper-large-v3", language="ru", source_uri="memory://sample.wav"))
        queue.put(job.id)
        _wait_status(store, job.id, JobStatus.error)
        current = store.get(job.id)
        assert current is not None
        assert current.error["error_code"] == "W-ASR-FFMPEG-NOT-FOUND"
    finally:
        worker.stop()

def test_worker_maps_ffmpeg_failed(monkeypatch):
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.load", lambda self: setattr(self, "_loaded", True))
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.transcribe", lambda self, audio_path, language: {"transcript": "ok", "segments": []})

    def _fail(*args, **kwargs):
        raise ToolInvocationError(
            tool="ffmpeg",
            tool_path="ffmpeg",
            cmd=["ffmpeg", "-i", "input.wav", "out.wav"],
            returncode=1,
            stderr="x" * 500,
            cause=subprocess.CalledProcessError(returncode=1, cmd=["ffmpeg"]),
        )

    monkeypatch.setattr("asr_service.jobs.worker.preprocess_audio", _fail)

    store = JobStore()
    queue = JobQueue()
    worker = Worker(store=store, queue=queue, registry=default_registry(), manager=ModelManager(), settings=_settings())
    worker.start()
    try:
        job = store.create(CreateJobRequest(model_key="whisper-large-v3", language="ru", source_uri="memory://sample.wav"))
        queue.put(job.id)
        _wait_status(store, job.id, JobStatus.error)
        current = store.get(job.id)
        assert current is not None
        assert current.error["error_code"] == "W-ASR-FFMPEG-FAILED"
        assert current.error["details"]["tool"] == "ffmpeg"
        assert current.error["details"]["stderr_len"] == 500
        assert len(current.error["details"]["stderr_excerpt"]) == 400
    finally:
        worker.stop()


def test_worker_maps_sox_not_found(monkeypatch):
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.load", lambda self: setattr(self, "_loaded", True))
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.transcribe", lambda self, audio_path, language: {"transcript": "ok", "segments": []})

    def _fail(*args, **kwargs):
        raise ToolInvocationError(
            tool="sox",
            tool_path="sox",
            cmd=["sox", "in.wav", "out.wav"],
            returncode=None,
            stderr="",
            cause=FileNotFoundError("sox not found"),
        )

    monkeypatch.setattr("asr_service.jobs.worker.preprocess_audio", _fail)

    store = JobStore()
    queue = JobQueue()
    worker = Worker(store=store, queue=queue, registry=default_registry(), manager=ModelManager(), settings=_settings())
    worker.start()
    try:
        job = store.create(CreateJobRequest(model_key="whisper-large-v3", language="ru", source_uri="memory://sample.wav"))
        queue.put(job.id)
        _wait_status(store, job.id, JobStatus.error)
        current = store.get(job.id)
        assert current is not None
        assert current.error["error_code"] == "W-ASR-SOX-NOT-FOUND"
    finally:
        worker.stop()


def test_worker_maps_sox_failed(monkeypatch):
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.load", lambda self: setattr(self, "_loaded", True))
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.transcribe", lambda self, audio_path, language: {"transcript": "ok", "segments": []})

    def _fail(*args, **kwargs):
        raise ToolInvocationError(
            tool="sox",
            tool_path="sox",
            cmd=["sox", "in.wav", "out.wav"],
            returncode=2,
            stderr="bad sox",
            cause=subprocess.CalledProcessError(returncode=2, cmd=["sox"]),
        )

    monkeypatch.setattr("asr_service.jobs.worker.preprocess_audio", _fail)

    store = JobStore()
    queue = JobQueue()
    worker = Worker(store=store, queue=queue, registry=default_registry(), manager=ModelManager(), settings=_settings())
    worker.start()
    try:
        job = store.create(CreateJobRequest(model_key="whisper-large-v3", language="ru", source_uri="memory://sample.wav"))
        queue.put(job.id)
        _wait_status(store, job.id, JobStatus.error)
        current = store.get(job.id)
        assert current is not None
        assert current.error["error_code"] == "W-ASR-SOX-FAILED"
        assert current.error["details"]["returncode"] == 2
    finally:
        worker.stop()
