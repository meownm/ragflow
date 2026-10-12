"""Configure real local models in the explicitly disposable test tenant."""

import os
from urllib.parse import urljoin


def _response_data(payload):
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
        raise ValueError("Local model configuration failed")
    return payload.get("data") or {}


def configure_ollama(base_url: str, auth_header: str, ollama_url: str, request_json) -> None:
    if os.getenv("RAGFLOW_TEST_DISPOSABLE") != "1":
        raise ValueError("Local model provisioning requires RAGFLOW_TEST_DISPOSABLE=1")
    headers = {"Authorization": auth_header}
    models = [
        ("qwen3.8:latest", "chat"),
        ("qwen3.6:27b", "chat"),
        ("bge-m3:latest", "embedding"),
    ]
    for model_name, model_type in models:
        _, payload = request_json(
            urljoin(base_url.rstrip("/") + "/", "/v1/llm/add_llm"),
            method="POST",
            payload={"llm_factory": "Ollama", "llm_name": model_name, "model_type": model_type, "api_base": ollama_url, "max_tokens": 4096},
            headers=headers,
            timeout_s=120,
        )
        _response_data(payload)

    _, payload = request_json(urljoin(base_url.rstrip("/") + "/", "/api/v1/providers"), method="PUT", payload={"provider_name": "Ollama"}, headers=headers)
    if not (payload.get("code") == 102 and payload.get("message") == "Provider Ollama already exists"):
        _response_data(payload)
    _, payload = request_json(
        urljoin(base_url.rstrip("/") + "/", "/api/v1/providers/Ollama/instances"),
        method="POST",
        payload={
            "instance_name": "Local",
            "api_key": "",
            "base_url": ollama_url,
            "region": "default",
            "model_info": [{"model_name": name, "model_type": [kind], "max_tokens": 4096} for name, kind in models],
        },
        headers=headers,
    )
    if not (payload.get("code") == 102 and "already exists" in str(payload.get("message", ""))):
        _response_data(payload)
    for model_name, model_type in (models[0], models[-1]):
        _, payload = request_json(
            urljoin(base_url.rstrip("/") + "/", "/api/v1/models/default"),
            method="PATCH",
            payload={"model_provider": "Ollama", "model_instance": "Local", "model_name": model_name, "model_type": model_type},
            headers=headers,
        )
        _response_data(payload)

    _, payload = request_json(urljoin(base_url.rstrip("/") + "/", "/api/v1/users/me/models"), headers=headers)
    tenant_id = _response_data(payload).get("tenant_id")
    if not tenant_id:
        raise ValueError("Ollama browser setup could not resolve the test tenant")
    _, payload = request_json(
        urljoin(base_url.rstrip("/") + "/", "/api/v1/users/me/models"),
        method="PATCH",
        payload={
            "tenant_id": tenant_id,
            "llm_id": f"{models[0][0]}@Local@Ollama",
            "embd_id": f"{models[-1][0]}@Local@Ollama",
            "img2txt_id": "",
            "asr_id": "",
            "rerank_id": "",
            "tts_id": "",
        },
        headers=headers,
    )
    _response_data(payload)
    _, payload = request_json(urljoin(base_url.rstrip("/") + "/", "/api/v1/users/me/models"), headers=headers)
    configured = _response_data(payload)
    expected = {"tenant_id": tenant_id, "llm_id": f"{models[0][0]}@Local@Ollama", "embd_id": f"{models[-1][0]}@Local@Ollama"}
    if not isinstance(configured, dict) or any(configured.get(key) != value for key, value in expected.items()):
        raise ValueError("Disposable tenant did not retain the required Ollama defaults")
