from copy import deepcopy

import pytest

from business_documents.sql_query.view_state import validate_view


VIEW = {"positions": {"query:0:o": {"x": 10, "y": -20}}, "viewport": {"x": 0, "y": 0, "zoom": 1}, "mode": "all", "depth": 1, "list_view": False}


def test_view_preserves_coordinates_without_mutating_input():
    original = deepcopy(VIEW)
    assert validate_view(original) == VIEW
    assert original == VIEW


@pytest.mark.parametrize("damage", [
    {"depth": True}, {"depth": 0}, {"depth": 4}, {"mode": []}, {"mode": "all-projects"}, {"list_view": 1},
    {"positions": {"node": {"x": float("nan"), "y": 0}}},
    {"positions": {"node": {"x": True, "y": 0}}},
    {"positions": {"node": {"x": 1, "y": 0, "sql": "SELECT 1"}}},
    {"viewport": {"x": 0, "y": 0, "zoom": 100}}, {"viewport": {"x": 0, "y": 0}},
    {"positions": {f"node-{index}": {"x": 0, "y": 0} for index in range(101)}},
])
def test_view_rejects_unbounded_or_non_cosmetic_inputs(damage):
    with pytest.raises(ValueError):
        validate_view({**VIEW, **damage})
