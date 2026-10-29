"""Capability boundaries and state transitions of the single-input SQL chat."""

from copy import deepcopy
from decimal import Decimal

import pytest

from business_documents.sql_assistant import default_view
from business_documents.sql_conversation import automatic_schema_payload, automatic_sources, change_view, literal_parameter, parse_intent, preparation_action, saved_aggregate
from business_documents.sql_query.query_specification import parse_schema_snapshot
from business_documents.sql_result_answer import answer_context
from test.evals.sql_projects.schema import accepted_schema

AMOUNT = "out_aaaaaaaaaa_1"
STATUS = "out_bbbbbbbbbb_1"
COLUMNS = [{"id": AMOUNT, "name": "amount"}, {"id": STATUS, "name": "status"}]
SQL = "SELECT amount, status FROM public.orders"


@pytest.mark.parametrize(
    "value,quote,expected", [("0", "10", False), ("paid", "unpaid", False), (5, "-5", False), (5, "5%", False), ("10", "порог 10.", True), ("2026-10-01", "2026-10-01T00:00", False)]
)
def test_parameter_evidence_is_a_complete_literal(value, quote, expected):
    assert literal_parameter(value, quote) is expected


@pytest.mark.parametrize(
    "parameters", [{}, {"other": {"value": "10", "source_quote": "10"}}, {"minimum": {"value": "0", "source_quote": "0"}}, {"minimum": {"value": float("nan"), "source_quote": "10"}}]
)
def test_definition_binding_rejects_missing_unknown_and_unattested_parameters(parameters):
    source = {"kind": "DEFINITION_BINDING", "mutable_parameters": ["minimum"], "discussion": [{"role": "user", "text": "Порог 10"}]}
    with pytest.raises(ValueError):
        parse_intent({"intent": "BIND_PARAMETERS", "parameters": parameters}, source)


def aggregate(operation="sum", column=AMOUNT):
    return {"intent": "AGGREGATE", "operation": operation, "column_id": column, "scope": "SAVED_RESULT"}


def context(rows=None):
    return answer_context("Что в результате?", "r1", rows or [["12.50", "paid"]], COLUMNS, default_view(COLUMNS), SQL, {}, False)


def schema_candidate():
    snapshot = accepted_schema()["schema_snapshot"]
    table = deepcopy(snapshot["requirements"][0]["selected_table"])
    entity = {key: value for key, value in table.items() if key not in {"columns", "loaded_column_count", "columns_truncated"}}
    entity.update(schema_loaded=True, column_count=len(table["columns"]), column_details=[{key: value for key, value in column.items() if key != "selected"} for column in table["columns"]])
    resolution = {"status": "READY", "resolutions": [{"term": "orders", "needs_clarification": False, "lookup": {"status": "OK"}, "catalog_answer": {"entities": [{"id": table["id"]}]}}]}
    details = {
        "status": "READY",
        "entities": [
            {
                "entity_id": table["id"],
                "lookup": {"status": "OK"},
                "entity": entity,
                "freshness": {"stale": False, "snapshot_at": "2026-10-29T00:00:00Z", "checked_at": "2026-10-29T00:00:01Z"},
                "sources": [],
                "retrieval": "live",
            }
        ],
    }
    return resolution, details, snapshot["original_requirements"]


def test_unique_full_catalog_schema_builds_a_real_compiler_snapshot():
    resolution, details, requirements = schema_candidate()
    payload = automatic_schema_payload(resolution, details, requirements)
    parsed = parse_schema_snapshot(payload["schema_snapshot"])
    assert parsed.status == "READY" and parsed.original_requirements == requirements
    assert len(parsed.tables) == 1
    assert payload["accepted_schema"][0]["entity_id"] == parsed.tables[0].id
    assert payload["schema_snapshot"]["requirements"][0]["decision"] == "CATALOG"


def test_model_recommendation_does_not_resolve_catalog_ambiguity():
    resolution, details, requirements = schema_candidate()
    item = resolution["resolutions"][0]
    item["catalog_answer"]["entities"].append({"id": "another"})
    item["interpretation"] = {"recommended_entity_id": item["catalog_answer"]["entities"][0]["id"], "confidence": 1}
    assert automatic_sources(resolution) == []
    assert automatic_schema_payload(resolution, details, requirements) is None


@pytest.mark.parametrize("axis", ["stale", "truncated", "partial", "missing"])
def test_full_schema_is_required_before_automatic_acceptance(axis):
    resolution, details, requirements = schema_candidate()
    entry = details["entities"][0]
    if axis == "stale":
        entry["freshness"]["stale"] = True
    elif axis == "truncated":
        entry["entity"]["columns_truncated"] = True
    elif axis == "partial":
        entry["entity"]["column_details"].pop()
    else:
        details["entities"] = []
    with pytest.raises(ValueError):
        automatic_schema_payload(resolution, details, requirements)


@pytest.mark.parametrize(
    "raw", [None, [], {"intent": []}, {"intent": "SQL", "sql": "SELECT 1"}, {"intent": "CHANGE", "sql": "DROP TABLE orders"}, {"intent": "ANSWER", "answer": {"kind": "ANSWER", "text": "Итог 240"}}]
)
def test_model_cannot_supply_sql_or_unverified_literal_facts(raw):
    with pytest.raises(ValueError):
        parse_intent(raw, context())


def test_answer_keeps_existing_cell_reference_validation():
    valid = {"intent": "ANSWER", "answer": {"kind": "ANSWER", "text": "Значение {{0:" + AMOUNT + "}}."}}
    assert parse_intent(valid, context()) == valid
    invalid = deepcopy(valid)
    invalid["answer"]["text"] = "{{55:" + AMOUNT + "}}"
    with pytest.raises(ValueError):
        parse_intent(invalid, context())


@pytest.mark.parametrize("plan", [aggregate("sum", "foreign"), aggregate([], AMOUNT), aggregate("rows", AMOUNT), aggregate("sum", []), {**aggregate(), "scope": "DATABASE"}])
def test_aggregate_requires_an_explicit_saved_scope_and_visible_column(plan):
    with pytest.raises(ValueError):
        parse_intent(plan, context())


def test_aggregate_uses_all_saved_rows_not_the_llm_sample():
    rows = [["0.10", "paid"] for _ in range(1001)]
    bounded = context(rows)
    assert len(bounded["rows"]) < len(rows)
    actual = saved_aggregate(aggregate(), rows, COLUMNS, default_view(COLUMNS), SQL, truncated=True)
    assert actual["value"] == "100.10"
    assert actual["scope"] == "LIMITED" and actual["source_completeness"] == "UNKNOWN"


def test_aggregate_respects_view_filter_null_and_exact_decimal():
    view = default_view(COLUMNS)
    view["filters"] = [{"column_id": STATUS, "operator": "eq", "value": "paid"}]
    rows = [["9007199254740993.12", "paid"], ["0.01", "paid"], [None, "paid"], ["99.00", "cancelled"]]
    result = saved_aggregate(aggregate(), rows, COLUMNS, view, SQL, truncated=False)
    assert result["value"] == "9007199254740993.13"
    assert Decimal(result["value"]) > Decimal("9007199254740993.12")
    assert saved_aggregate(aggregate("count"), rows, COLUMNS, view, SQL, truncated=False)["value"] == "2"
    assert saved_aggregate(aggregate("rows", None), rows, COLUMNS, view, SQL, truncated=False)["value"] == "3"


def test_saved_sql_aggregates_require_a_new_query_for_reaggregation():
    actual = saved_aggregate(aggregate(), [["240.00", "paid"]], COLUMNS, default_view(COLUMNS), "SELECT SUM(amount), status FROM orders GROUP BY status", truncated=False)
    assert actual["status"] == "REQUIRES_QUERY" and actual["value"] is None


def test_empty_sum_and_count_do_not_confuse_null_and_zero():
    view = default_view(COLUMNS)
    assert saved_aggregate(aggregate(), [], COLUMNS, view, SQL, truncated=False)["status"] == "NULL"
    assert saved_aggregate(aggregate("rows", None), [], COLUMNS, view, SQL, truncated=False)["value"] == "0"


def test_chart_patch_preserves_other_settings_and_does_not_mutate_inputs():
    current = default_view(COLUMNS)
    current["formats"][AMOUNT] = "number"
    current["filters"] = [{"column_id": STATUS, "operator": "eq", "value": "paid"}]
    old = deepcopy(current)
    changed = change_view(current, {"chart": {"kind": "bar", "x": STATUS, "y": AMOUNT}}, COLUMNS, SQL)
    assert current == old
    assert changed["appearance"]["display"] == "chart"
    assert changed["filters"] == old["filters"] and changed["formats"] == old["formats"]
    assert change_view(changed, {"chart": None}, COLUMNS, SQL)["appearance"]["display"] == "table"


def test_legacy_view_without_appearance_can_switch_to_chart():
    old = default_view(COLUMNS)
    del old["appearance"]
    assert change_view(old, {"chart": {"kind": "line", "x": STATUS, "y": AMOUNT}}, COLUMNS, SQL)["appearance"]["display"] == "chart"


@pytest.mark.parametrize("patch", [{"sql": "SELECT 1"}, {"filters": []}, {"chart": {"kind": "bar", "x": "foreign", "y": AMOUNT}}, {"appearance": {"display": "unknown"}}, {"labels": []}])
def test_view_intent_cannot_mutate_data_or_use_foreign_columns(patch):
    with pytest.raises(ValueError):
        change_view(default_view(COLUMNS), patch, COLUMNS, SQL)


def proposal(kind, result):
    return {"operation_state": "REVIEW", "pending_proposal": {"kind": kind, "payload": {"agent_result": result}}}


def test_blocking_requirement_waits_for_nonblank_answer():
    value = proposal("REQUIREMENTS", {"proposal": {"requirements": [{}], "questions": [{"id": "year", "blocking": True}]}})
    assert preparation_action(value) == "ASK_REQUIREMENTS"
    assert preparation_action(value, answers={"year": "  "}) == "ASK_REQUIREMENTS"
    assert preparation_action(value, answers={"year": "2026 UTC"}) == "ACCEPT_REQUIREMENTS"


def test_degraded_or_ambiguous_catalog_never_auto_accepts():
    assert preparation_action(proposal("SCHEMA", {"status": "DEGRADED", "conversation_schema": {"schema_snapshot": {}}})) == "ERROR"
    assert preparation_action(proposal("SCHEMA", {"status": "NEEDS_CLARIFICATION", "conversation_schema": {"schema_snapshot": {}}})) == "ASK_SOURCE"
    assert preparation_action(proposal("SCHEMA", {"status": "READY", "conversation_schema": {"schema_snapshot": {"status": "READY"}}})) == "ACCEPT_SCHEMA"


@pytest.mark.parametrize(
    ("status", "action"), [("READY", "SELECT_RESULT"), ("FAILED", "ERROR"), ("CANCELLED", "CANCELLED"), ("UNKNOWN", "RECONCILE"), ("CANCEL_REQUESTED", "WAIT"), ("EXPIRED", "ERROR")]
)
def test_only_this_turns_ready_run_can_be_selected(status, action):
    project = {"operation_state": "IDLE", "runs": [{"id": "previous", "status": "READY"}, {"id": "candidate", "status": status}]}
    assert preparation_action(project, pending_run_id="candidate") == action
    assert preparation_action(project, pending_run_id="missing") == "ERROR"


def test_ready_run_does_not_publish_while_another_job_owns_the_project():
    project = {"operation_state": "RUNNING", "current_job": {"id": "newer"}, "runs": [{"id": "old", "status": "READY"}]}
    assert preparation_action(project, pending_run_id="old") == "WAIT"


def test_terminal_job_metadata_does_not_stall_preparation_or_selection():
    project = proposal("REQUIREMENTS", {"proposal": {"requirements": [{}], "questions": []}})
    project["current_job"] = {"id": "completed", "status": "COMPLETED"}
    assert preparation_action(project) == "ACCEPT_REQUIREMENTS"
    project = {"operation_state": "IDLE", "current_job": {"status": "COMPLETED"}, "runs": [{"id": "ready", "status": "READY"}]}
    assert preparation_action(project, pending_run_id="ready") == "SELECT_RESULT"


def test_compile_and_execute_are_distinct_and_require_real_guard_pass():
    project = {"operation_state": "IDLE", "artifact_ids": {"query": "q1"}}
    assert preparation_action(project) == "COMPILE"
    project["compilation"] = {"result": {"status": "READY", "guard": {"status": "BLOCKED"}}}
    assert preparation_action(project) == "ERROR"
    project["compilation"]["result"]["guard"]["status"] = "PASS"
    assert preparation_action(project) == "EXECUTE"
