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
