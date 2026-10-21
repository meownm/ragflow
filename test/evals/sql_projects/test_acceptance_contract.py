"""Traceability proves a test exists, never that it passed or covers more."""

import ast
from pathlib import Path

from test.evals.sql_projects.acceptance import AUTOMATION_SCOPE, BROWSER_SCENARIOS, REAL_SERVICE_SCENARIOS


def test_browser_plan_has_eighty_eight_explicit_observable_scenarios():
    assert [case.id for case in BROWSER_SCENARIOS] == [f"B{index:02}" for index in range(1, 89)]
    assert len({case.action for case in BROWSER_SCENARIOS}) == 88
    assert all(case.area and case.action and case.expected for case in BROWSER_SCENARIOS)
    assert len(REAL_SERVICE_SCENARIOS) == 8
    assert len({case[0] for case in REAL_SERVICE_SCENARIOS}) == 8


def test_automation_links_point_to_existing_native_browser_tests():
    root = Path(__file__).resolve().parents[2] / "playwright/e2e"
    functions = {node.name for path in root.glob("test_sql*.py") for node in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for case in BROWSER_SCENARIOS:
        if case.automated_by:
            assert case.automated_by in functions, case.id
            assert AUTOMATION_SCOPE[case.id]
    assert set(AUTOMATION_SCOPE) == {case.id for case in BROWSER_SCENARIOS if case.automated_by}
