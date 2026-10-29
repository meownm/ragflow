"""Approved grammar rejects semantic drift instead of certifying arbitrary SQL."""

from copy import deepcopy

import pytest

from business_documents.sql_assistant_definitions import compile_defined_query, definition_payload
from test.evals.sql_projects.schema import accepted_requirements, accepted_schema
from test.unit_test.business_documents.test_sql_assistant_query import candidate


def contract():
    return {
        "grain": ["customer"],
        "metrics": [{"select_item_id": "total", "unit": "RUB", "null_policy": "IGNORE_NULLS_NULL_IF_EMPTY", "reaggregation": "FORBIDDEN"}],
        "time_column_id": None,
        "timezone": "UTC",
        "mutable_parameters": ["positive"],
    }


def definition():
    return definition_payload(accepted_requirements(), accepted_schema(), candidate(), contract(), "Сумма по клиентам", "Сумма положительных оплаченных или отменённых заказов")


def test_only_explicitly_mutable_values_and_output_limit_can_change():
    approved = definition()
    query = deepcopy(approved["query"])
    query["filters"][2]["parameter_value"] = "5"
    query["row_limit"] = 12
    _, compiled = compile_defined_query(approved, approved["requirements"], approved["schema"], query)
    assert compiled["status"] == "READY"
    assert compiled["parameters"]["positive"] == "5"
    assert compiled["parameters"]["row_limit"] == 12


@pytest.mark.parametrize("change", ["formula", "grain", "fixed-value", "filter-column", "operator", "or", "unit", "null", "timezone", "schema", "manual"])
def test_semantic_changes_do_not_inherit_definition_validation(change):
    approved = definition()
    query, schema = deepcopy(approved["query"]), deepcopy(approved["schema"])
    if change == "formula":
        query["select"][1]["kind"] = "avg"
    elif change == "grain":
        query["select"] = query["select"][1:]
    elif change == "fixed-value":
        query["filters"][0]["parameter_value"] = "pending"
    elif change == "filter-column":
        query["filters"][2]["column_id"] = "synthetic.qa.public.orders.id"
    elif change == "operator":
        query["filters"][2]["operator"] = "lt"
    elif change == "or":
        query["row_filter_tree"]["operator"] = "OR"
    elif change == "unit":
        approved["contract"]["metrics"][0]["unit"] = ""
    elif change == "null":
        approved["contract"]["metrics"][0]["null_policy"] = "ZERO"
    elif change == "timezone":
        query["timezone"] = "Europe/Moscow"
    elif change == "schema":
        schema["schema_snapshot"]["requirements"][0]["selected_table"]["version"] = 2
    else:
        query = {"mode": "manual", "sql": "SELECT SUM(amount) FROM public.orders LIMIT :row_limit"}
    with pytest.raises(ValueError):
        compile_defined_query(approved, approved["requirements"], schema, query)


def test_fanout_join_needs_catalog_unique_key_and_count_null_policy_is_explicit():
    query = candidate()
    query["joins"] = [
        {
            "id": "items",
            "join_type": "LEFT",
            "entity_id": "order_items",
            "alias": "i",
            "left_column_id": "synthetic.qa.public.orders.id",
            "right_column_id": "synthetic.qa.public.order_items.order_id",
            "description": "Строки заказа",
            "confirmed": True,
            "decision": "user",
        }
    ]
    with pytest.raises(ValueError, match="уникальност"):
        definition_payload(accepted_requirements(), accepted_schema(), query, contract(), "Сумма", "Описание")


def test_timezone_aware_bucket_is_explicit_and_not_session_dependent():
    schema = accepted_schema()
    table = next(item["selected_table"] for item in schema["schema_snapshot"]["requirements"] if item["selected_table"]["id"] == "orders")
    created = next(item for item in table["columns"] if item["name"] == "created_at")
    created["data_type"] = "TIMESTAMP WITH TIME ZONE"
    query = candidate()
    query["select"][0] = {"id": "customer", "kind": "date_bucket", "column_id": created["id"], "alias": "day", "grain": "day"}
    semantics = {**contract(), "time_column_id": created["id"], "timezone": "Europe/Moscow"}
    approved = definition_payload(accepted_requirements(), schema, query, semantics, "По дням", "Сумма по местному календарю")
    _, compiled = compile_defined_query(approved, approved["requirements"], schema, approved["query"])
    assert compiled["sql"].count("AT TIME ZONE :__assistant_timezone") == 2
    assert compiled["parameters"]["__assistant_timezone"] == "Europe/Moscow"


@pytest.mark.parametrize("aware,offset", [(True, True), (True, False), (False, True), (False, False)])
def test_datetime_offset_matches_the_catalog_storage_semantics(aware, offset):
    schema = accepted_schema()
    table = next(item["selected_table"] for item in schema["schema_snapshot"]["requirements"] if item["selected_table"]["id"] == "orders")
    column = next(item for item in table["columns"] if item["name"] == "created_at")
    column["data_type"] = "TIMESTAMP WITH TIME ZONE" if aware else "TIMESTAMP"
    query = candidate()
    query["filters"].append(
        {
            "id": "start",
            "column_id": column["id"],
            "operator": "gte",
            "parameter_name": "start",
            "parameter_type": "datetime",
            "parameter_value": "2026-08-01T00:00:00" + ("+03:00" if offset else ""),
            "description": "Начало периода",
            "confirmed": True,
            "decision": "user",
        }
    )
    query["row_filter_tree"]["children"].append({"filter_id": "start"})
    semantics = {**contract(), "time_column_id": column["id"], "timezone": "Europe/Moscow"}
    if aware != offset:
        with pytest.raises(ValueError, match="смещение|смещения"):
            definition_payload(accepted_requirements(), schema, query, semantics, "По периоду", "Локальный период")
    else:
        assert definition_payload(accepted_requirements(), schema, query, semantics, "По периоду", "Локальный период")["contract"]["timezone"] == "Europe/Moscow"
