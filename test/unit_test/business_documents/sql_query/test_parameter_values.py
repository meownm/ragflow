"""Bound data preserves empty strings and whitespace separately from NULL."""

import pytest

from business_documents.sql_query.query_specification import QuerySpecificationValidationError, parse_parameter_value


@pytest.mark.parametrize("value", ["", " ", " North ", "O'Reilly", "' OR true --", "Клиент", "x" * 5000])
def test_text_bind_values_preserve_exact_content(value):
    assert parse_parameter_value("text", value, "value") == value


@pytest.mark.parametrize("value", [None, 0, False, [], {}, "x" * 5001])
def test_invalid_text_bind_values_are_not_coerced(value):
    with pytest.raises(QuerySpecificationValidationError):
        parse_parameter_value("text", value, "value")


def test_text_list_preserves_empty_elements():
    assert parse_parameter_value("text_list", ["", " North "], "value") == ["", " North "]
