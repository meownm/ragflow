"""Interpret selected-model metadata for Ollama's native chat transport."""

from typing import Any


def native_ollama_config(config: dict[str, Any], metadata: Any) -> dict[str, Any] | None:
    """Return a request-local config with an explicit, model-supported context.

    /api/show works for unloaded models. Its maximum context is usable only
    with native requests that explicitly set num_ctx, not as a discovered
    runtime window for OpenAI-compatible requests.
    """
    if not isinstance(metadata, dict) or not isinstance(metadata.get("model_info"), dict):
        return None
    info = metadata["model_info"]
    details = metadata.get("details")
    family = details.get("family", "") if isinstance(details, dict) else ""
    context = info.get(f"{family}.context_length") or info.get("general.context_length")
    if context is None:
        context = next((value for key, value in info.items() if key.endswith(".context_length") and type(value) is int and value > 0), None)
    if type(context) is not int or context <= 0:
        return None
    base = str(config.get("api_base") or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    name = str(config.get("llm_name") or config.get("model_name") or "").split("___")[0]
    return {**config, "llm_factory": "Ollama", "llm_name": name, "api_base": base, "max_tokens": context}
