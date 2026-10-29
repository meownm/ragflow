"""Logical precedence and aggregate filtering keep values bound and history separate."""

from copy import deepcopy
from contextlib import closing
import sqlite3

import pytest
import sqlglot
from sqlglot import exp

from business_documents.sql_assistant_query import compile_assistant_query
from test.evals.sql_projects.schema import accepted_requirements, accepted_schema


def condition(identity, column, value, kind="text", operator="eq"):
    return {
        "id": identity,
        "column_id": "synthetic.qa.public.orders." + column,
        "operator": operator,
        "parameter_name": identity,
        "parameter_type": kind,
        "parameter_value": value,
        "description": "Условие пользователя",
        "confirmed": True,
        "decision": "user",
    }


def leaf(identity):
    return {"filter_id": identity}


def group(operator, *children):
    return {"operator": operator, "children": list(children)}


def candidate():
    amount = condition("minimum", "amount", "10", "decimal", "gt")
    del amount["column_id"]
    amount["select_item_id"] = "total"
    return {
        "base_entity_id": "orders",
        "aliases": {"orders": "o"},
        "joins": [],
        "select": [
            {"id": "customer", "kind": "column", "column_id": "synthetic.qa.public.orders.customer_id", "alias": "customer", "grain": None},
            {"id": "total", "kind": "sum", "column_id": "synthetic.qa.public.orders.amount", "alias": "total", "grain": None},
        ],
        "filters": [condition("paid", "status", "paid"), condition("cancelled", "status", "cancelled"), condition("positive", "amount", "0", "decimal", "gt")],
        "row_filter_tree": group("AND", group("OR", leaf("paid"), leaf("cancelled")), leaf("positive")),
        "aggregate_filters": [amount],
        "aggregate_filter_tree": leaf("minimum"),
        "order_by": [{"select_item_id": "customer", "direction": "ASC"}],
        "row_limit": 100,
    }


def compile(query):
    return compile_assistant_query(accepted_requirements(), accepted_schema(), query)


def test_nested_precedence_and_having_execute_after_grouping_with_bound_values():
    query = candidate()
    before = deepcopy(query)
    command, result = compile(query)
    assert query == before
    assert result["status"] == "READY"
    assert result["parameters"] == {"paid": "paid", "cancelled": "cancelled", "positive": "0", "minimum": "10", "row_limit": 100}
    parsed = sqlglot.parse_one(result["sql"], dialect="postgres")
    assert isinstance(parsed.args["where"].this, exp.And)
    assert parsed.args["having"].find(exp.Sum) is not None
    assert "minimum" not in parsed.args["where"].sql()
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("ATTACH DATABASE ':memory:' AS public")
        db.execute("CREATE TABLE public.orders (customer_id INTEGER, status TEXT, amount DECIMAL)")
        db.executemany("INSERT INTO public.orders VALUES (?, ?, ?)", [(1, "paid", 5), (1, "cancelled", 8), (1, "paid", -100), (2, "pending", 50), (3, "cancelled", -5), (4, "paid", 3)])
        # SQLite has no PostgreSQL numeric placeholder inference for aggregate
        # comparisons; supply the independently typed numeric bindings explicitly.
        bindings = {**result["parameters"], "positive": 0, "minimum": 10}
        assert db.execute(command["manual_sql"], bindings).fetchall() == [(1, 13)]


def test_aggregate_or_and_count_type_follow_selected_expression_not_alias():
    query = candidate()
    query["select"].append({"id": "count", "kind": "count_distinct", "column_id": "synthetic.qa.public.orders.id", "alias": "other_label", "grain": None})
    condition = {**query["aggregate_filters"][0], "id": "many", "select_item_id": "count", "parameter_name": "many", "parameter_type": "integer", "parameter_value": "2"}
    query["aggregate_filters"].append(condition)
    query["aggregate_filter_tree"] = group("OR", leaf("minimum"), leaf("many"))
    _, result = compile(query)
    having = sqlglot.parse_one(result["sql"], dialect="postgres").args["having"]
    assert having.find(exp.Or) is not None
    assert having.find(exp.Count) is not None
    assert "other_label" not in having.sql()


@pytest.mark.parametrize(
    "tree",
    [
        leaf("missing"),
        group("OR", leaf("paid"), leaf("paid")),
        group("AND", leaf("paid")),
        {"operator": "XOR", "children": [leaf("paid")]},
        {"filter_id": "paid", "sql": "1=1"},
        {"operator": "OR", "children": []},
    ],
)
def test_invalid_or_incomplete_tree_never_drops_conditions(tree):
    query = candidate()
    query["row_filter_tree"] = tree
    with pytest.raises(ValueError):
        compile(query)


@pytest.mark.parametrize("damage", ["dimension", "type", "operator", "unconfirmed", "duplicate", "injection", "deep", "aggregate-tree-missing", "wrong-row-type"])
def test_invalid_typed_predicates_are_rejected(damage):
    query = candidate()
    aggregate = query["aggregate_filters"][0]
    if damage == "dimension":
        aggregate["select_item_id"] = "customer"
    elif damage == "type":
        aggregate["parameter_type"] = "text"
    elif damage == "operator":
        aggregate["operator"] = "ilike"
    elif damage == "unconfirmed":
        aggregate["confirmed"] = False
    elif damage == "duplicate":
        aggregate["id"] = "paid"
    elif damage == "injection":
        aggregate["parameter_value"] = "0 OR 1=1"
    elif damage == "deep":
        tree = leaf("minimum")
        for _ in range(7):
            tree = group("AND", tree)
        query["aggregate_filter_tree"] = tree
    elif damage == "aggregate-tree-missing":
        query["aggregate_filter_tree"] = leaf("other")
    else:
        query["filters"][0]["parameter_type"] = "integer"
    with pytest.raises(ValueError):
        compile(query)


def test_null_aggregate_needs_no_parameter_and_text_value_is_never_sql():
    query = candidate()
    query["aggregate_filters"][0]["operator"] = "is_null"
    query["filters"][0]["parameter_value"] = "paid' OR 1=1 --"
    _, result = compile(query)
    assert "minimum" not in result["parameters"]
    assert "paid' OR" not in result["sql"]
    assert result["parameters"]["paid"] == "paid' OR 1=1 --"


def test_row_groups_preserve_supported_top_per_group_cte_and_bound_limit():
    query = candidate()
    query["select"][1] = {"id": "order", "kind": "column", "column_id": "synthetic.qa.public.orders.id", "alias": "order_id", "grain": None}
    query["aggregate_filters"] = []
    query["aggregate_filter_tree"] = None
    query["top_per_group"] = {"partition_by": ["customer"], "order_by": [{"select_item_id": "order", "direction": "ASC"}], "row_limit": 2}
    _, result = compile(query)
    assert result["status"] == "READY"
    assert result["parameters"]["__ragflow_group_limit"] == 2
    parsed = sqlglot.parse_one(result["sql"], dialect="postgres")
    inner = parsed.args["with_"].expressions[0].this
    assert inner.args["where"].find(exp.Or) is not None
    assert "__ragflow_group_limit" in parsed.args["where"].sql()
