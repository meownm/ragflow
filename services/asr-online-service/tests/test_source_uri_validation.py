from pathlib import Path

from fastapi.testclient import TestClient

from asr_service.main import app


def _base_job_payload(source_uri: str) -> dict:
    return {
        "model_key": "whisper-large-v3",
        "language": "ru",
        "source_uri": source_uri,
    }


def test_create_job_rejects_parent_traversal_source_uri() -> None:
    descriptor = next(item for item in app.state.registry.items if item.key == "whisper-large-v3")
    previous = descriptor.available
    descriptor.available = True
    try:
        with TestClient(app) as client:
            response = client.post("/v1/asr/jobs", json=_base_job_payload("../x.wav"))
    finally:
        descriptor.available = previous

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "Q-ASR-SOURCE-URI-INVALID"


def test_create_job_rejects_absolute_windows_source_uri() -> None:
    descriptor = next(item for item in app.state.registry.items if item.key == "whisper-large-v3")
    previous = descriptor.available
    descriptor.available = True
    try:
        with TestClient(app) as client:
            response = client.post("/v1/asr/jobs", json=_base_job_payload(r"C:\\Windows\\win.ini"))
    finally:
        descriptor.available = previous

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "Q-ASR-SOURCE-URI-INVALID"


def test_create_job_rejects_unsafe_memory_source_uri() -> None:
    descriptor = next(item for item in app.state.registry.items if item.key == "whisper-large-v3")
    previous = descriptor.available
    descriptor.available = True
    try:
        with TestClient(app) as client:
            response = client.post("/v1/asr/jobs", json=_base_job_payload("memory://../evil.wav"))
    finally:
        descriptor.available = previous

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "Q-ASR-SOURCE-URI-INVALID"


def test_create_job_accepts_safe_memory_source_uri(monkeypatch) -> None:
    monkeypatch.setattr("asr_service.jobs.worker.preprocess_audio", lambda source_uri, settings, output_dir: (Path("sample.wav"), Path("sample.wav")))
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.load", lambda self: setattr(self, "_loaded", True))
    monkeypatch.setattr("asr_service.models.engines.whisper_engine.WhisperEngine.transcribe", lambda self, audio_path, language: {"transcript": "ok", "segments": []})

    descriptor = next(item for item in app.state.registry.items if item.key == "whisper-large-v3")
    previous = descriptor.available
    descriptor.available = True
    try:
        with TestClient(app) as client:
            response = client.post("/v1/asr/jobs", json=_base_job_payload("memory://sample.wav"))
    finally:
        descriptor.available = previous

    assert response.status_code == 201
    assert response.json()["status"] == "queued"
