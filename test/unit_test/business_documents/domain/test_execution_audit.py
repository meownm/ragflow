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
@pytest.mark.parametrize("limit", [4096, 8192, 65536])
def test_supported_completion_parameters_are_preserved(limit_parameter, limit):
    audit = _ai_execution_audit()
    audit["parameters"] = {"temperature": 0, "top_p": 0.1, limit_parameter: limit}
    assert validate_ai_audit(audit) == audit


@pytest.mark.parametrize("limits", [{}, {"max_tokens": 65536, "max_completion_tokens": 8192}, {"max_tokens": 65535}, {"max_completion_tokens": True}, {"token_limit": 8192}])
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
