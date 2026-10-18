"""Resolve provider output ceilings independently of model context windows."""

from __future__ import annotations

from typing import Any


def output_token_limit(config: dict[str, Any], *, local_ollama: bool = False) -> int | None:
    """Use an explicit ceiling or the installed provider catalog; never guess one.

    Local inference runtimes expose a shared input/output context rather than an independent
    output ceiling. A compatible endpoint is treated that way only after its
    Ollama runtime metadata has identified the selected model.
    """
    configured = config.get("max_output_tokens")
    if configured is not None:
        if type(configured) is not int or configured <= 0:
            raise ValueError("Configure a positive model output token limit")
    if config.get("llm_factory") in {"Ollama", "VLLM", "LM-Studio", "Xinference", "LocalAI"} or local_ollama:
        return configured

    # Read only the installed catalog. get_model_info may query remote provider
    # metadata; budget planning must not contact a model or depend on credentials.
    from litellm import model_cost

    factory = str(config.get("llm_factory") or "").lower()
    provider = {"azure-openai": "azure", "tongyi-qianwen": "dashscope", "cohere": "cohere_chat", "togetherai": "together_ai", "nvidia": "nvidia_nim", "google cloud": "vertex_ai"}.get(factory, factory)
    name = str(config.get("llm_name") or config.get("model_name") or "").split("___")[0]
    # These factories route named models through LiteLLM's OpenAI transport.
    # Keep their own catalog namespace first; their native entries may supply
    # a provider-specific ceiling (for example MiniMax).
    openai_routes = {
        "longcat",
        "cometapi",
        "siliconflow",
        "openrouter",
        "stepfun",
        "ppio",
        "perfxcloud",
        "upstage",
        "novitaai",
        "01.ai",
        "giteeai",
        "302.ai",
        "jiekou.ai",
        "zhipu-ai",
        "minimax",
        "deerapi",
        "gpustack",
        "n1n",
        "tencent hunyuan",
        "avian",
        "astraflow",
        "astraflow-cn",
        "futurmix",
    }
    providers = [provider]
    if factory in openai_routes or factory == "azure-openai":
        providers.append("openai")
    accepted_providers = set(providers)
    if factory == "google cloud":
        # The installed catalog uses a bare Gemini model name with this
        # provider tag; Claude-on-Vertex entries keep a vertex_ai/ prefix.
        accepted_providers.update({"vertex_ai-language-models", "vertex_ai-anthropic_models"})
    qualified_candidates = [f"{namespace}/{name}" for namespace in providers]
    candidates = [*qualified_candidates, name]
    if factory in openai_routes and name.startswith("openai/"):
        candidates.append(name.removeprefix("openai/"))
    for candidate in candidates:
        metadata = model_cost.get(candidate) or {}
        # Bare-name collisions must not borrow a different provider's limits.
        if candidate not in qualified_candidates and provider not in {"openai-api-compatible", "vllm"} and metadata.get("litellm_provider") not in accepted_providers:
            continue
        limit = metadata.get("max_output_tokens")
        if type(limit) is int and limit > 0:
            return min(configured, limit) if configured is not None else limit
    if configured is not None:
        return configured
    raise ValueError("Configure the selected model's output token limit; provider metadata is unavailable")
