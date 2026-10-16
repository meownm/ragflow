"""Worker audit normalization without database or server imports."""

import pytest

from business_documents.domain.errors import RuleViolation
from business_documents.domain.execution_audit import validate_ai_audit


def _ai_execution_audit():
    return {
        "provider": "Ollama",
        "model": "qualified-model",
        "model_type": "chat",
        "parameters": {"temperature": 0, "top_p": 0.1, "max_completion_tokens": 8192},
        "duration_ms": 1250.5,
        "token_usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
    }


def test_ai_execution_audit_is_canonicalized_for_persistence():
    validated = validate_ai_audit(_ai_execution_audit())

    assert validated == _ai_execution_audit()


@pytest.mark.parametrize("limit_parameter", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("limit", [1, 2047, 2048, 4096, 8192, 65535, 65536])
def test_supported_completion_parameters_are_preserved(limit_parameter, limit):
    audit = _ai_execution_audit()
    audit["parameters"] = {"temperature": 0, "top_p": 0.1, limit_parameter: limit}
    assert validate_ai_audit(audit) == audit


@pytest.mark.parametrize(
    "limits", [{}, {"max_tokens": 65536, "max_completion_tokens": 8192}, {"max_tokens": 65537}, {"max_completion_tokens": 0}, {"max_completion_tokens": True}, {"token_limit": 8192}]
)
def test_ambiguous_or_unsupported_completion_limits_fail_closed(limits):
    audit = _ai_execution_audit()
    audit["parameters"] = {"temperature": 0, "top_p": 0.1, **limits}
    with pytest.raises(RuleViolation, match="Worker AI parameters"):
        validate_ai_audit(audit)


def test_verification_retains_both_model_calls_without_replacing_generation_audit():
    audit = _ai_execution_audit()
    audit["verification"] = {**_ai_execution_audit(), "duration_ms": 300}
    assert validate_ai_audit(audit) == audit
    assert validate_ai_audit(audit)["duration_ms"] == 1250.5
    audit["verification"] = [_ai_execution_audit(), _ai_execution_audit()]
    assert len(validate_ai_audit(audit)["verification"]) == 2


def test_recovery_retains_generation_usage_separately_from_verification():
    audit = _ai_execution_audit()
    audit["recovery"] = [{**_ai_execution_audit(), "duration_ms": 200}]
    audit["verification"] = {**_ai_execution_audit(), "duration_ms": 300}
    assert validate_ai_audit(audit) == audit


@pytest.mark.parametrize("recovery", [[], "invalid", [{}], [{**_ai_execution_audit(), "model": ""}], [{**_ai_execution_audit(), "recovery": [_ai_execution_audit()]}]])
def test_recovery_cannot_hide_invalid_or_nested_model_audits(recovery):
    audit = {**_ai_execution_audit(), "recovery": recovery}
    with pytest.raises(RuleViolation):
        validate_ai_audit(audit)


@pytest.mark.parametrize("window", [65536, 131072, 262144])
def test_ollama_context_is_preserved_in_execution_audit(window):
    audit = _ai_execution_audit()
    audit["parameters"].update(max_completion_tokens=window // 2, extra_body={"num_ctx": window})
    if window == 262144:
        audit["parameters"]["max_completion_tokens"] = 65536
    assert validate_ai_audit(audit) == audit


@pytest.mark.parametrize("body", [{}, {"num_ctx": True}, {"num_ctx": 8192}, {"num_ctx": 65536, "other": 1}])
def test_invalid_native_context_is_rejected(body):
    audit = _ai_execution_audit()
    audit["parameters"]["extra_body"] = body
    with pytest.raises(RuleViolation):
        validate_ai_audit(audit)


@pytest.mark.parametrize(
    "provider,model,effort,accepted",
    [
        ("OpenAI-API-Compatible", "qwen3.8:latest", "none", True),
        ("OpenAI-API-Compatible", "other", "none", False),
        ("Ollama", "qwen3", "none", False),
        ("OpenAI-API-Compatible", "qwen3", "high", False),
    ],
)
def test_reasoning_mode_is_audited_only_for_supported_qwen_profile(provider, model, effort, accepted):
    audit = _ai_execution_audit()
    audit.update(provider=provider, model=model)
    audit["parameters"]["reasoning_effort"] = effort
    if accepted:
        assert validate_ai_audit(audit) == audit
    else:
        with pytest.raises(RuleViolation):
            validate_ai_audit(audit)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda audit: audit.update(model=""),
        lambda audit: audit["parameters"].update(temperature=0.7),
        lambda audit: audit.update(duration_ms=float("nan")),
        lambda audit: audit["token_usage"].update(total_tokens=999),
    ],
)
def test_ai_execution_audit_fails_closed_on_invalid_identity_parameters_timing_or_usage(mutation):
    audit = _ai_execution_audit()
    mutation(audit)

    with pytest.raises(RuleViolation, match="Worker AI"):
        validate_ai_audit(audit)
