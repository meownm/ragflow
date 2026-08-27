from fastapi.testclient import TestClient

from asr_service.main import app


def test_windows_smoke_ready_not_ready_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr("asr_service.settings.settings.ffmpeg_path", "definitely_missing_ffmpeg_binary")

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["checks"]["ffmpeg"]["ok"] is False
