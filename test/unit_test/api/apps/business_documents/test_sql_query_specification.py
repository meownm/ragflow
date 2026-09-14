#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#

from __future__ import annotations

from copy import deepcopy

import pytest

from business_documents.sql_query.query_specification import (
    QuerySpecificationValidationError,
    SqlGuardError,
    compile_query_payload,
    guard_read_only_sql,
)


def _table(entity_id: str, fqn: str, fields: tuple[str, ...]) -> dict:
    technical_name = fqn.rsplit(".", 1)[-1]
    return {
        "id": entity_id,
        "fqn": fqn,
        "name": technical_name,
        "display_name": None,
        "technical_name": technical_name,
        "description": f"Fixture {fqn}",
        "version": 7,
        "updated_at": "2026-09-14T08:00:00+00:00",
        "service": "warehouse",
        "database": "analytics",
        "schema": fqn.split(".", 1)[0],
        "owners": ["DWH"],
        "domains": ["Sales"],
        "tags": ["gold"],
        "glossary_terms": [],
        "url": f"https://metadata.example/{fqn}",
        "matched_by": ["fixture"],
        "column_count": len(fields),
        "loaded_column_count": len(fields),
        "columns_truncated": False,
        "schema_loaded": True,
        "schema_fingerprint": f"sha256:{entity_id}-v7",
        "columns": [
            {
                "id": f"{fqn}.{name}",
                "name": name,
                "fqn": f"{fqn}.{name}",
                "data_type": "TIMESTAMP" if name == "created_at" else "TEXT",
                "description": name,
                "constraint": "",
                "glossary_terms": [],
                "selected": True,
            }
            for name in fields
        ],
        "table_constraints": [],
    }


def _snapshot() -> dict:
    tables = (
        _table(
            "orders",
            "dwh.order_fact",
            ("order_id", "customer_id", "status_id", "created_at", "paid_amount_rub"),
        ),
        _table("customers", "dwh.customer_dim", ("customer_id", "customer_type_code")),
        _table("statuses", "ref.order_status", ("status_id", "status_code", "status_name_ru")),
    )
    return {
        "format": "ragflow-sql-schema-snapshot",
        "schema_version": "1",
        "status": "READY",
        "original_requirements": ("Покажи по месяцам 2026 года сумму оплаченных завершённых заказов корпоративных клиентов. Статус выведи названием."),
        "source": {
            "type": "openmetadata",
            "catalog_snapshot_at": "2026-09-14T08:00:00+00:00",
            "checked_at": "2026-09-14T08:05:00+00:00",
            "stale": False,
            "retrieval": ["fixture"],
            "references": [{"label": "OpenMetadata"}],
        },
        "requirements": [
            {
                "term": table["name"],
                "state": "CONFIRMED",
                "decision": "USER",
                "interpretation": None,
                "candidates": [],
                "selected_table": table,
            }
            for table in tables
        ],
        "warnings": [],
    }


def _accepted(snapshot: dict) -> list[dict]:
    return [
        {
            "entity_id": requirement["selected_table"]["id"],
            "version": requirement["selected_table"]["version"],
            "schema_fingerprint": requirement["selected_table"]["schema_fingerprint"],
        }
        for requirement in snapshot["requirements"]
    ]


def _payload() -> dict:
    snapshot = _snapshot()
    return {
        "schema_version": "1",
        "schema_snapshot": snapshot,
        "accepted_requirements": snapshot["original_requirements"],
        "accepted_schema": _accepted(snapshot),
        "specification": {
            "dialect": "postgres",
            "from": {"entity_id": "orders", "alias": "o"},
            "select": [
                {
                    "id": "month",
                    "kind": "date_bucket",
                    "column_id": "dwh.order_fact.created_at",
                    "alias": "month",
                    "grain": "month",
                },
                {
                    "id": "status_name",
                    "kind": "column",
                    "column_id": "ref.order_status.status_name_ru",
                    "alias": "status_name",
                    "grain": None,
                },
                {
                    "id": "paid_amount",
                    "kind": "sum",
                    "column_id": "dwh.order_fact.paid_amount_rub",
                    "alias": "paid_amount_rub",
                    "grain": None,
                },
            ],
            "joins": [
                {
                    "id": "join-customers",
                    "join_type": "INNER",
                    "entity_id": "customers",
                    "alias": "c",
                    "left_column_id": "dwh.order_fact.customer_id",
                    "right_column_id": "dwh.customer_dim.customer_id",
                    "description": "Заказ принадлежит клиенту",
                    "decision": "user",
                    "confirmed": True,
                },
                {
                    "id": "join-statuses",
                    "join_type": "INNER",
                    "entity_id": "statuses",
                    "alias": "s",
                    "left_column_id": "dwh.order_fact.status_id",
                    "right_column_id": "ref.order_status.status_id",
                    "description": "ID статуса расшифровывается справочником",
                    "decision": "user",
                    "confirmed": True,
                },
            ],
            "filters": [
                {
                    "id": "date-from",
                    "column_id": "dwh.order_fact.created_at",
                    "operator": "gte",
                    "parameter": "date_from",
                    "description": "Дата заказа не раньше начала 2026 года",
                    "decision": "user",
                    "confirmed": True,
                },
                {
                    "id": "date-to",
                    "column_id": "dwh.order_fact.created_at",
                    "operator": "lt",
                    "parameter": "date_to",
                    "description": "Дата заказа раньше начала 2027 года",
                    "decision": "user",
                    "confirmed": True,
                },
                {
                    "id": "customer-type",
                    "column_id": "dwh.customer_dim.customer_type_code",
                    "operator": "eq",
                    "parameter": "customer_type_code",
                    "description": "Только корпоративные клиенты",
                    "decision": "user",
                    "confirmed": True,
                },
                {
                    "id": "status-code",
                    "column_id": "ref.order_status.status_code",
                    "operator": "eq",
                    "parameter": "status_code",
                    "description": "Только завершённые заказы",
                    "decision": "user",
                    "confirmed": True,
                },
            ],
            "order_by": [
                {"select_item_id": "month", "direction": "ASC"},
                {"select_item_id": "status_name", "direction": "ASC"},
            ],
            "parameters": [
                {"name": "date_from", "type": "date", "value": "2026-01-01"},
                {"name": "date_to", "type": "date", "value": "2027-01-01"},
                {"name": "customer_type_code", "type": "text", "value": "CORPORATE"},
                {"name": "status_code", "type": "text", "value": "COMPLETED"},
                {"name": "row_limit", "type": "integer", "value": 1000},
            ],
            "limit_parameter": "row_limit",
        },
    }


def test_gsql_01_compiles_the_confirmed_structure_and_separate_parameters():
    result = compile_query_payload(_payload())

    assert result["status"] == "READY"
    assert result["blocking_issues"] == []
    assert result["guard"]["status"] == "PASS"
    assert result["parameters"] == {
        "date_from": "2026-01-01",
        "date_to": "2027-01-01",
        "customer_type_code": "CORPORATE",
        "status_code": "COMPLETED",
        "row_limit": 1000,
    }
    assert (
        result["sql"]
        == """SELECT
    date_trunc('month', o.created_at)::date AS month,
    s.status_name_ru AS status_name,
    SUM(o.paid_amount_rub) AS paid_amount_rub
FROM dwh.order_fact AS o
JOIN dwh.customer_dim AS c
    ON o.customer_id = c.customer_id
JOIN ref.order_status AS s
    ON o.status_id = s.status_id
WHERE o.created_at >= :date_from
  AND o.created_at < :date_to
  AND c.customer_type_code = :customer_type_code
  AND s.status_code = :status_code
GROUP BY
    date_trunc('month', o.created_at)::date,
    s.status_name_ru
ORDER BY month ASC, status_name ASC
LIMIT :row_limit"""
    )


def test_gsql_02_open_join_decision_returns_no_executable_sql():
    payload = _payload()
    payload["specification"]["joins"][0]["confirmed"] = False
    payload["specification"]["joins"][0]["decision"] = None

    result = compile_query_payload(payload)

    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["sql"] is None
    assert result["parameters"] == {}
    assert result["guard"] == {"status": "NOT_RUN"}
    assert [issue["code"] for issue in result["blocking_issues"]] == ["JOIN_DECISION_REQUIRED"]


def test_gsql_06_changed_snapshot_version_invalidates_the_acceptance():
    payload = _payload()
    payload["schema_snapshot"]["requirements"][0]["selected_table"]["version"] = 8
    payload["schema_snapshot"]["requirements"][0]["selected_table"]["schema_fingerprint"] = "sha256:orders-v8"

    result = compile_query_payload(payload)

    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["sql"] is None
    assert [issue["code"] for issue in result["blocking_issues"]] == ["SCHEMA_VERSION_CHANGED"]


def test_same_physical_table_can_satisfy_multiple_business_terms():
    payload = _payload()
    duplicate = deepcopy(payload["schema_snapshot"]["requirements"][0])
    duplicate["term"] = "Оплаченный заказ"
    payload["schema_snapshot"]["requirements"].append(duplicate)

    result = compile_query_payload(payload)

    assert result["status"] == "READY"
    assert result["guard"]["status"] == "PASS"


def test_conflicting_versions_of_one_table_are_rejected():
    payload = _payload()
    duplicate = deepcopy(payload["schema_snapshot"]["requirements"][0])
    duplicate["term"] = "Оплаченный заказ"
    duplicate["selected_table"]["version"] = 8
    duplicate["selected_table"]["schema_fingerprint"] = "sha256:orders-v8"
    payload["schema_snapshot"]["requirements"].append(duplicate)

    with pytest.raises(QuerySpecificationValidationError, match="conflicting versions"):
        compile_query_payload(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload["specification"]["from"].update(alias="o;DROP"), "safe SQL identifier"),
        (lambda payload: payload["specification"]["parameters"].append({"name": "unused", "type": "text", "value": "x"}), "Unused parameters"),
        (lambda payload: payload["specification"]["select"][0].update(column_id="secret.users.password"), "outside the schema snapshot"),
        (lambda payload: payload["specification"]["parameters"][-1].update(value=10001), "from 1 to 10000"),
    ],
)
def test_invalid_or_unbounded_specs_fail_closed(mutate, message):
    payload = _payload()
    mutate(payload)

    with pytest.raises(QuerySpecificationValidationError, match=message):
        compile_query_payload(payload)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM dwh.order_fact LIMIT :row_limit",
        "SELECT o.order_id FROM dwh.order_fact AS o; DELETE FROM x",
        "SELECT pg_read_file(:path) AS value FROM dwh.order_fact LIMIT :row_limit",
        "SELECT p.rolname AS name FROM pg_catalog.pg_roles AS p LIMIT :row_limit",
        "WITH gone AS (DELETE FROM dwh.order_fact RETURNING order_id) SELECT gone.order_id AS order_id FROM gone LIMIT :row_limit",
    ],
)
def test_sql_guard_rejects_adversarial_read_paths(sql):
    with pytest.raises(SqlGuardError):
        guard_read_only_sql(
            sql,
            allowed_tables=["dwh.order_fact"],
            parameter_names=["row_limit", "path"],
        )


def test_request_contract_rejects_unknown_fields_and_non_date_values():
    unknown = _payload()
    unknown["unexpected"] = True
    invalid_date = deepcopy(_payload())
    invalid_date["specification"]["parameters"][0]["value"] = "tomorrow"

    with pytest.raises(QuerySpecificationValidationError, match="Unknown request fields"):
        compile_query_payload(unknown)
    with pytest.raises(QuerySpecificationValidationError, match="ISO date"):
        compile_query_payload(invalid_date)
