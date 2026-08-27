from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import ValidationError

from asr_service.main import app
from asr_service.settings import Settings


def test_log_data_mode_allows_masked() -> None:
    cfg = Settings(LOG_DATA_MODE="masked")
    assert cfg.log_data_mode == "masked"


def test_log_data_mode_rejects_invalid_value() -> None:
    try:
        Settings(LOG_DATA_MODE="secret")
        assert False
    except ValidationError:
        assert True


def test_models_endpoint_uses_app_state_registry() -> None:
    with TestClient(app) as client:
        response = client.get("/v1/asr/models")
        assert response.status_code == 200
        payload = response.json()
        assert "models" in payload
        assert isinstance(payload["models"], list)
