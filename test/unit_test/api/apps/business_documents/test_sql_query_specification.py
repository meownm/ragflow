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
    parse_schema_snapshot,
)
from business_documents.sql_query.project_compilation import ProjectCompilationError, build_project_compile_command


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


def test_accepted_project_artifacts_compile_to_the_same_sql():
    expected = _payload()
    accepted_text = f"- {expected['accepted_requirements']}"
    expected["accepted_requirements"] = accepted_text
    expected["schema_snapshot"]["original_requirements"] = accepted_text
    specification = expected["specification"]
    parameter_by_name = {item["name"]: item for item in specification["parameters"]}
    query_filters = []
    for item in specification["filters"]:
        parameter = parameter_by_name[item["parameter"]]
        query_filters.append({
            "id": item["id"],
            "column_id": item["column_id"],
            "operator": item["operator"],
            "parameter_name": parameter["name"],
            "parameter_type": parameter["type"],
            "parameter_value": str(parameter["value"]),
            "description": item["description"],
            "decision": "user",
            "confirmed": True,
        })
    query = {
        "base_entity_id": specification["from"]["entity_id"],
        "aliases": {
            specification["from"]["entity_id"]: specification["from"]["alias"],
            **{join["entity_id"]: join["alias"] for join in specification["joins"]},
        },
        "select": specification["select"],
        "joins": specification["joins"],
        "filters": query_filters,
        "order_by": specification["order_by"],
        "row_limit": 1000,
    }
    actual = build_project_compile_command(
        {"requirements": [{"statement": accepted_text.removeprefix("- ")}]},
        {"schema_snapshot": expected["schema_snapshot"], "accepted_schema": expected["accepted_schema"]},
        query,
    )
    actual_result = compile_query_payload(actual)
    assert actual_result["status"] == "READY", actual_result["blocking_issues"]
    assert actual_result["sql"] == compile_query_payload(expected)["sql"]

    query["joins"] = [{**join, "confirmed": False} for join in specification["joins"]]
    with pytest.raises(ProjectCompilationError, match="JOIN has not been confirmed"):
        build_project_compile_command(
            {"requirements": [{"statement": accepted_text.removeprefix("- ")}]},
            {"schema_snapshot": expected["schema_snapshot"], "accepted_schema": expected["accepted_schema"]},
            query,
        )


def test_openmetadata_database_name_with_hyphen_does_not_change_sql_relation():
    table = _table("pdf-job", "docker_postgres_pdf_ocr.pdf-ocr.public.pdf_ocr_jobs", ("id",))
    table["service"] = "docker_postgres_pdf_ocr"
    table["database"] = "pdf-ocr"
    table["schema"] = "public"
    snapshot = _snapshot()
    snapshot["requirements"] = [{"term": "job", "state": "CONFIRMED", "decision": "USER", "interpretation": None, "candidates": [], "selected_table": table}]
    parsed = parse_schema_snapshot(snapshot)
    assert parsed.tables[0].physical_relation == "public.pdf_ocr_jobs"


def _live_omd_payload() -> dict:
    fqn = "docker_postgres_bot.bot.public.llm_requests_log"
    table = _table(
        "491922b2-66ca-4c3d-9c7d-651a68d491b9",
        fqn,
        ("log_id", "request_timestamp_start", "duration_seconds", "is_success", "model_name"),
    )
    table.update(
        {
            "service": "docker_postgres_bot",
            "database": "bot",
            "schema": "public",
        }
    )
    snapshot = {
        "format": "ragflow-sql-schema-snapshot",
        "schema_version": "1",
        "status": "READY",
        "original_requirements": "Покажи самые долгие успешные запросы к LLM.",
        "source": {"stale": False},
        "requirements": [
            {
                "term": "запрос к LLM",
                "state": "CONFIRMED",
                "decision": "USER",
                "interpretation": None,
                "candidates": [],
                "selected_table": table,
            }
        ],
        "warnings": [],
    }
    return {
        "schema_version": "1",
        "schema_snapshot": snapshot,
        "accepted_requirements": snapshot["original_requirements"],
        "accepted_schema": _accepted(snapshot),
        "specification": {
            "dialect": "postgres",
            "from": {"entity_id": table["id"], "alias": "llm"},
            "select": [
                {
                    "id": "duration",
                    "kind": "column",
                    "column_id": f"{fqn}.duration_seconds",
                    "alias": "duration_seconds",
                    "grain": None,
                }
            ],
            "joins": [],
            "filters": [],
            "order_by": [{"select_item_id": "duration", "direction": "DESC"}],
            "parameters": [{"name": "row_limit", "type": "integer", "value": 20}],
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


def test_live_openmetadata_four_part_fqn_compiles_to_the_bound_postgres_relation():
    result = compile_query_payload(_live_omd_payload())

    assert result["status"] == "READY"
    assert "FROM public.llm_requests_log AS llm" in result["sql"]
    assert "docker_postgres_bot.bot" not in result["sql"]
    assert result["guard"]["tables"] == ["public.llm_requests_log"]


def test_openmetadata_fqn_must_match_the_explicit_catalog_identity():
    payload = _live_omd_payload()
    payload["schema_snapshot"]["requirements"][0]["selected_table"]["database"] = "other"

    with pytest.raises(QuerySpecificationValidationError, match="OpenMetadata catalog identity"):
        compile_query_payload(payload)


def test_openmetadata_four_part_fqn_requires_a_complete_catalog_identity():
    payload = _live_omd_payload()
    payload["schema_snapshot"]["requirements"][0]["selected_table"]["service"] = ""

    with pytest.raises(QuerySpecificationValidationError, match="requires service, database, and schema"):
        compile_query_payload(payload)


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
        "SELECT security_definer_udf() AS value FROM dwh.order_fact LIMIT :row_limit",
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


def _manual_payload(sql: str) -> dict:
    base = _payload()
    return {
        "schema_version": "1",
        "schema_snapshot": base["schema_snapshot"],
        "accepted_requirements": base["accepted_requirements"],
        "accepted_schema": base["accepted_schema"],
        "manual_sql": sql,
        "parameters": [{"name": "row_limit", "type": "integer", "value": 25}],
    }


@pytest.mark.parametrize("sql", [
    "SELECT o.order_id AS order_id FROM dwh.order_fact AS o LIMIT :row_limit",
    "WITH sales AS (SELECT o.order_id AS order_id FROM dwh.order_fact AS o) SELECT sales.order_id AS order_id FROM sales LIMIT :row_limit",
    "SELECT o.status_id AS status_id, COUNT(o.order_id) AS total FROM dwh.order_fact AS o GROUP BY o.status_id HAVING COUNT(o.order_id) > :minimum LIMIT :row_limit",
    "SELECT o.status_id AS status_id, COUNT(*) AS total FROM dwh.order_fact AS o GROUP BY o.status_id LIMIT :row_limit",
    "SELECT o.order_id AS order_id FROM dwh.order_fact AS o UNION SELECT c.customer_id AS order_id FROM dwh.customer_dim AS c LIMIT :row_limit",
    "SELECT o.order_id AS order_id, ROW_NUMBER() OVER (ORDER BY o.created_at) AS position FROM dwh.order_fact AS o LIMIT :row_limit",
])
def test_manual_advanced_sql_is_catalog_bound(sql):
    payload = _manual_payload(sql)
    if ":minimum" in sql:
        payload["parameters"].append({"name": "minimum", "type": "integer", "value": 1})
    result = compile_query_payload(payload)
    assert result["status"] == "READY"
    assert result["guard"]["status"] == "PASS"
    assert result["output_columns"]


@pytest.mark.parametrize("sql", [
    "SELECT o.missing AS missing FROM dwh.order_fact AS o LIMIT :row_limit",
    "SELECT o.order_id AS order_id FROM pg_catalog.pg_user AS o LIMIT :row_limit",
    "SELECT dangerous(o.order_id) AS value FROM dwh.order_fact AS o LIMIT :row_limit",
    "SELECT o.order_id AS order_id FROM dwh.order_fact AS o LIMIT 25",
    "WITH RECURSIVE sales AS (SELECT o.order_id AS order_id FROM dwh.order_fact AS o) SELECT sales.order_id AS order_id FROM sales LIMIT :row_limit",
])
def test_manual_advanced_sql_rejects_unbound_or_unknown_access(sql):
    with pytest.raises(SqlGuardError):
        compile_query_payload(_manual_payload(sql))
