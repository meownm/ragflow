"""Provider output budgets use an independent, explicit ceiling."""

import pytest
from common.model_limits import output_token_limit


def test_known_provider_uses_output_metadata_and_respects_lower_configured_limit():
    config = {"llm_factory": "OpenAI", "llm_name": "gpt-4.1", "max_tokens": 1047576}
    assert output_token_limit(config) == 32768
    assert output_token_limit({**config, "max_output_tokens": 4096}) == 4096
    assert output_token_limit({**config, "max_output_tokens": 100000}) == 32768


@pytest.mark.parametrize("factory", ["Ollama", "VLLM", "LM-Studio", "Xinference", "LocalAI"])
def test_local_runtime_shared_context_has_no_arbitrary_output_ceiling(factory):
    config = {"llm_factory": factory, "llm_name": "private-local-model", "max_tokens": 262144}
    assert output_token_limit(config) is None
    assert output_token_limit({**config, "max_output_tokens": 100000}) == 100000


def test_compatible_endpoint_requires_identified_local_runtime_or_explicit_ceiling():
    config = {"llm_factory": "OpenAI-API-Compatible", "llm_name": "private-model", "max_tokens": 262144}
    with pytest.raises(ValueError, match="output token limit"):
        output_token_limit(config)
    assert output_token_limit(config, local_ollama=True) is None
    assert output_token_limit({**config, "max_output_tokens": 131072}) == 131072


@pytest.mark.parametrize("limit", [True, 0, -1, "32768"])
def test_invalid_explicit_limit_fails_before_metadata_or_provider_use(limit):
    with pytest.raises(ValueError, match="positive"):
        output_token_limit({"llm_factory": "Ollama", "max_output_tokens": limit})


def test_catalog_context_is_never_used_as_output_ceiling(monkeypatch):
    import litellm

    monkeypatch.setattr(litellm, "model_cost", {"openai/future": {"litellm_provider": "openai", "max_input_tokens": 1000000, "max_tokens": 1000000}})
    with pytest.raises(ValueError, match="metadata is unavailable"):
        output_token_limit({"llm_factory": "OpenAI", "llm_name": "future", "max_tokens": 1000000})


def test_bare_model_name_cannot_borrow_other_provider_metadata(monkeypatch):
    import litellm

    monkeypatch.setattr(litellm, "model_cost", {"shared": {"litellm_provider": "anthropic", "max_output_tokens": 50000}})
    with pytest.raises(ValueError):
        output_token_limit({"llm_factory": "OpenAI", "llm_name": "shared"})


@pytest.mark.parametrize("model,limit", [("gemini-2.5-pro", 65535), ("claude-sonnet-4-5", 64000)])
def test_google_cloud_uses_vertex_catalog_model_families(model, limit):
    assert output_token_limit({"llm_factory": "Google Cloud", "llm_name": model}) == limit


def test_azure_can_use_canonical_openai_catalog_for_unprefixed_model():
    assert output_token_limit({"llm_factory": "Azure-OpenAI", "llm_name": "gpt-3.5-turbo-16k"}) == 4096


@pytest.mark.parametrize("factory", ["n1n", "CometAPI", "302.AI", "OpenRouter", "Astraflow", "FuturMix"])
def test_declared_openai_routes_use_canonical_model_output_metadata(factory):
    assert output_token_limit({"llm_factory": factory, "llm_name": "gpt-4.1"}) == 32768


def test_native_metadata_precedes_canonical_transport_alias(monkeypatch):
    import litellm

    monkeypatch.setattr(
        litellm, "model_cost", {"minimax/shared": {"litellm_provider": "minimax", "max_output_tokens": 8192}, "openai/shared": {"litellm_provider": "openai", "max_output_tokens": 32768}}
    )
    assert output_token_limit({"llm_factory": "MiniMax", "llm_name": "shared"}) == 8192


def test_registered_openai_routes_resolve_provider_catalog_budget(monkeypatch):
    # Read the static mapping without importing rag.llm and all provider SDKs.
    import ast
    from pathlib import Path
    import litellm

    registered = ast.parse((Path(__file__).resolve().parents[3] / "rag/llm/__init__.py").read_text(encoding="utf-8"))
    names = {}
    for node in registered.body:
        if isinstance(node, ast.ClassDef) and node.name == "SupportedLiteLLMProvider":
            names = {item.targets[0].id: ast.literal_eval(item.value) for item in node.body if isinstance(item, ast.Assign)}
    routes = set()
    for node in registered.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "LITELLM_PROVIDER_PREFIX" for target in node.targets):
            routes = {names[key.attr] for key, value in zip(node.value.keys, node.value.values) if ast.literal_eval(value) == "openai/"}
    monkeypatch.setattr(litellm, "model_cost", {"openai/probe": {"litellm_provider": "openai", "max_output_tokens": 12345}})
    for factory in routes:
        assert output_token_limit({"llm_factory": factory, "llm_name": "probe"}) == 12345, factory


def test_openrouter_preserves_explicit_underlying_openai_model_namespace():
    assert output_token_limit({"llm_factory": "OpenRouter", "llm_name": "openai/gpt-4.1"}) == 32768
