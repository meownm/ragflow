import sys
from pathlib import Path


ADMIN_SERVER = Path(__file__).parents[3] / "admin" / "server"
if str(ADMIN_SERVER) not in sys.path:
    sys.path.insert(0, str(ADMIN_SERVER))

import document_quality_models as models


def test_catalog_uses_completion_capability_and_deduplicates_aliases(monkeypatch):
    monkeypatch.setattr(models, "get_tenant_default_model_by_type", lambda *_: {"llm_factory": "Ollama", "api_base": "http://qa-ollama:11434", "api_key": ""})

    def request(_base, path, _key, payload=None):
        if path == "/api/tags":
            return {"models": [{"name": "qwen", "digest": "a" * 64}, {"name": "qwen:alias", "digest": "a" * 64}, {"name": "embed", "digest": "b" * 64}]}
        return {"capabilities": ["embedding"] if payload["model"] == "embed" else ["completion", "vision"]}

    monkeypatch.setattr(models, "_ollama_request", request)
    catalog = models.quality_model_catalog("qa")
    assert catalog == {"models": [{"name": "qwen", "digest": "a" * 64, "aliases": ["qwen", "qwen:alias"]}], "errors": []}
    assert models.quality_model_digest("qa", "qwen:alias") == "a" * 64


def test_configuration_fingerprint_tracks_temporal_changes_without_exposing_secrets(monkeypatch):
    config = {"llm_factory": "Ollama", "api_base": "http://qa:11434", "api_key": "private-key", "max_tokens": 4096}
    metadata = {"model_info": {"qwen.context_length": 262144}, "parameters": "temperature 0"}
    monkeypatch.setattr(models, "get_tenant_default_model_by_type", lambda *_: config)
    monkeypatch.setattr(models, "_ollama_request", lambda *_: metadata)
    first = models.quality_model_configuration("qa", "qwen")
    assert len(first) == 64 and "private" not in first
    assert first == models.quality_model_configuration("qa", "qwen")
    metadata["model_info"]["qwen.context_length"] = 131072
    assert first != models.quality_model_configuration("qa", "qwen")
    metadata["model_info"]["qwen.context_length"] = 262144
    config["max_tokens"] = 8192
    assert first != models.quality_model_configuration("qa", "qwen")
