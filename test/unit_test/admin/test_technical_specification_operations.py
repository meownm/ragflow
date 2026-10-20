"""Exact operational cohorts, SQL percentiles and missing-cost semantics."""

import sys
from pathlib import Path

import pytest
from peewee import SqliteDatabase

from api.db.db_models import TechnicalSpecificationAlignmentObservation as Observation

ADMIN = Path(__file__).resolve().parents[3] / "admin/server"
if str(ADMIN) not in sys.path:
    sys.path.insert(0, str(ADMIN))
import technical_specification_operations as operations

NOW = 1_800_000_000_000


@pytest.fixture
def observations(monkeypatch, tmp_path):
    database = SqliteDatabase(tmp_path / "observations.sqlite")
    monkeypatch.setattr(operations, "current_timestamp", lambda: NOW)
    with database.bind_ctx([Observation]):
        database.create_tables([Observation])
        yield database
    database.close()


def row(identifier, **changes):
    return {
        "id": identifier,
        "accepted_at_ms": NOW - 1000,
        "observed_at_ms": NOW - 1000,
        "state": "FINISHED",
        "execution_status": "COMPLETE",
        "report_complete": True,
        "calls": 1,
        "spent_tokens": 15,
        "reserved_tokens": 0,
        "usage_known": True,
        "duration_ms": 20,
        "model_duration_ms": 10,
        **changes,
    }


def test_empty_cohort_has_no_invented_rates_or_usage(observations):
    result = operations.alignment_operations_dashboard()
    assert result["scope"] == "RECORDED_ALIGNMENT_ATTEMPTS" and result["counts"]["total"] == 0
    assert result["total_tokens"] is result["execution_completion_rate"] is result["report_completion_rate"] is None
    assert result["durations"]["check"] == {"measured": 0, "mean_ms": None, "p95_ms": None}


def test_known_cost_prefix_and_reserved_debt_remain_separate_from_total(observations):
    Observation.create(**row("complete"))
    Observation.create(
        **row("cancelled", state="INCOMPLETE", execution_status="INCOMPLETE", report_complete=False, usage_known=False, spent_tokens=0, reserved_tokens=30, error_code="EXECUTION_CANCELLED")
    )
    Observation.create(
        **row("pending", state="PROCESSING", execution_status="UNKNOWN", report_complete=None, usage_known=False, spent_tokens=None, reserved_tokens=None, duration_ms=None, model_duration_ms=None)
    )
    Observation.create(**row("limit", execution_status="INCOMPLETE", report_complete=False, error_code="MODEL_INPUT_TRUNCATED"))
    result = operations.alignment_operations_dashboard(days=1)
    assert result["counts"]["total"] == 4 and result["terminal_attempts"] == 3
    assert result["counts"]["cancelled"] == result["counts"]["limit_failures"] == 1
    assert result["counts"]["usage_unknown"] == 2 and result["total_tokens"] is None
    assert result["known_spent_tokens"] == 30 and result["known_reserved_tokens"] == 30 and result["reserved_tokens"] is None
    assert result["execution_completion_rate"] == result["report_completion_rate"] == 1 / 3


@pytest.mark.parametrize("code", ["MODEL_PROVIDER_OVERLOADED", "MODEL_CONTEXT_REJECTED"])
def test_provider_resource_rejection_is_a_limit_failure_with_unknown_usage(observations, code):
    Observation.create(**row("overload", execution_status="INCOMPLETE", report_complete=False, error_code=code, usage_known=False, spent_tokens=0, reserved_tokens=30))
    result = operations.alignment_operations_dashboard()
    assert result["counts"]["limit_failures"] == 1
    assert result["counts"]["usage_unknown"] == 1 and result["total_tokens"] is None
    assert result["reserved_tokens"] == 30 and result["known_spent_tokens"] == 0
    assert result["reasons"] == [{"code": code, "count": 1}]


def test_boundary_and_full_cohort_percentile_are_not_a_bounded_sample(observations, monkeypatch):
    Observation.create(**row("outside", accepted_at_ms=NOW - operations.DAY_MS - 1))
    Observation.create(**row("future", accepted_at_ms=NOW + 1))
    data = [row(str(index), accepted_at_ms=NOW - operations.DAY_MS, duration_ms=index, model_duration_ms=None) for index in range(1, 5002)]
    for start in range(0, len(data), 250):
        Observation.insert_many(data[start : start + 250]).execute()
    queries = []
    original = observations.execute_sql

    def record(sql, params=None, *args, **kwargs):
        queries.append(sql)
        return original(sql, params, *args, **kwargs)

    monkeypatch.setattr(observations, "execute_sql", record)
    result = operations.alignment_operations_dashboard(days=1)
    assert result["counts"]["total"] == 5001 and result["total_tokens"] == 5001 * 15
    assert result["durations"]["check"] == {"measured": 5001, "mean_ms": 2501.0, "p95_ms": 4751}
    assert len(queries) <= 8


@pytest.mark.parametrize("days", [0, 2, True, 31, "7"])
def test_invalid_window_is_rejected_before_read(days):
    with pytest.raises(ValueError):
        operations.alignment_operations_dashboard(days=days)
