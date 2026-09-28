"""Read the dedicated QA tenant's Ollama inventory without changing its defaults."""

import json
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
from common.constants import LLMType


def _ollama_request(base: str, path: str, key: str, payload: dict | None = None) -> dict:
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("QA Ollama endpoint is invalid")
    root_path = parsed.path.rstrip("/")
    if root_path.endswith("/v1"):
        root_path = root_path[:-3]
    url = urlunsplit((parsed.scheme, parsed.netloc, root_path + path, "", ""))
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, headers=headers, method="POST" if body is not None else "GET"), timeout=10) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("QA Ollama returned an invalid response")
    return value


def quality_model_catalog(tenant_id: str) -> dict:
    config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
    if not isinstance(config, dict):
        raise ValueError("QA tenant has no Chat model")
    if config.get("llm_factory") != "Ollama":
        raise ValueError("The dedicated QA Chat model must use Ollama")
    base = config.get("api_base") or ""
    key = config.get("api_key") or ""
    tags = _ollama_request(base, "/api/tags", key).get("models")
    if not isinstance(tags, list):
        raise ValueError("QA Ollama model catalog is unavailable")
    by_digest: dict[str, dict] = {}
    errors: list[str] = []
    for item in tags:
        if not isinstance(item, dict):
            continue
        name, digest = item.get("name"), item.get("digest")
        if not isinstance(name, str) or not name or not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            errors.append("INVALID_MODEL_IDENTITY")
            continue
        digest = digest.lower()
        try:
            detail = _ollama_request(base, "/api/show", key, {"model": name})
        except (OSError, ValueError, TimeoutError):
            errors.append("MODEL_DETAIL_UNAVAILABLE")
            continue
        if "completion" not in (detail.get("capabilities") or []):
            continue
        entry = by_digest.setdefault(digest, {"name": name, "digest": digest, "aliases": []})
        entry["aliases"].append(name)
    models = sorted(by_digest.values(), key=lambda item: item["name"])
    for item in models:
        item["aliases"].sort()
    return {"models": models, "errors": sorted(set(errors))}


def quality_model_digest(tenant_id: str, name: str) -> str | None:
    config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
    if not isinstance(config, dict):
        return None
    if config.get("llm_factory") != "Ollama":
        return None
    tags = _ollama_request(config.get("api_base") or "", "/api/tags", config.get("api_key") or "").get("models")
    for item in tags if isinstance(tags, list) else []:
        if isinstance(item, dict) and item.get("name") == name:
            digest = item.get("digest")
            return digest.lower() if isinstance(digest, str) else None
    return None
