from pathlib import Path

from fastapi.testclient import TestClient

from asr_service.main import app


def test_openai_models_lists_available_tone_model() -> None:
    descriptor = next(item for item in app.state.registry.items if item.key == "t-one")
    previous = descriptor.available
    descriptor.available = True
    try:
        with TestClient(app) as client:
            response = client.get("/v1/models")

        assert response.status_code == 200
        assert {model["id"] for model in response.json()["data"]} >= {"t-one"}
    finally:
        descriptor.available = previous


def test_openai_transcription_runs_tone_job(monkeypatch, tmp_path: Path) -> None:
    descriptor = next(item for item in app.state.registry.items if item.key == "t-one")
    previous_available = descriptor.available
    descriptor.available = True
    monkeypatch.setattr("asr_service.api.routes_openai.settings.upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr("asr_service.jobs.worker.preprocess_audio", lambda source_uri, settings, output_dir: (Path(source_uri), None))
    monkeypatch.setattr("asr_service.models.engines.tone_engine.ToneEngine.load", lambda self: setattr(self, "_loaded", True))
    monkeypatch.setattr(
        "asr_service.models.engines.tone_engine.ToneEngine.transcribe",
        lambda self, audio_path, language: {"transcript": "проверка t-one", "segments": []},
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("sample.wav", b"RIFF", "audio/wav")},
                data={"model": "t-one"},
            )

        assert response.status_code == 200
        assert response.json() == {"text": "проверка t-one"}
        assert list((tmp_path / "uploads").iterdir()) == []
    finally:
        descriptor.available = previous_available
