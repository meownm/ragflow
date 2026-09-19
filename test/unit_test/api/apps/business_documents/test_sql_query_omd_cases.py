#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.

from __future__ import annotations

import json
from pathlib import Path

from business_documents.sql_query.execution_registry import (
    CatalogBindingRecord,
    ExecutionProfileRecord,
    parse_resolve_execution_profile_command,
    resolve_execution_profile,
)
from business_documents.sql_query.query_specification import compile_query_payload


ROOT = Path(__file__).parents[5]
CASES_PATH = ROOT / "agent" / "business_requirements" / "golden_dialogs" / "sql_query_omd.v1.json"


def _cases() -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _column(table_fqn: str, name: str, data_type: str) -> dict:
    return {
        "id": f"{table_fqn}.{name}",
        "name": name,
        "fqn": f"{table_fqn}.{name}",
        "data_type": data_type,
        "description": name,
        "constraint": "PRIMARY_KEY" if name in {"id", "query_id"} else "",
        "glossary_terms": [],
        "selected": True,
    }


def _table(entity_id: str, version: float, name: str, columns: tuple[tuple[str, str], ...], constraints: list[dict]) -> dict:
    fqn = f"docker_postgres_bot.bot.public.{name}"
    return {
        "id": entity_id,
        "fqn": fqn,
        "name": name,
        "display_name": None,
        "technical_name": name,
        "description": f"Live OMD contract fixture for {fqn}",
        "version": version,
        "updated_at": "2026-09-14T00:00:00+00:00",
        "service": "docker_postgres_bot",
        "database": "bot",
        "schema": "public",
        "owners": [],
        "domains": [],
        "tags": [],
        "glossary_terms": [],
        "url": f"http://127.0.0.1:8585/table/{fqn}",
        "matched_by": ["openmetadata"],
        "column_count": len(columns),
        "loaded_column_count": len(columns),
        "columns_truncated": False,
        "schema_loaded": True,
        "schema_fingerprint": f"sha256:{entity_id}-omd-contract",
        "columns": [_column(fqn, column, data_type) for column, data_type in columns],
        "table_constraints": constraints,
    }


def _primary_payload() -> dict:
    case = _cases()["cases"][0]
    table_evidence = {table["entity_id"]: table for table in case["tables"]}
    queries = _table(
        "2bebfd51-ea82-4b8f-8b7b-eeec1d930b08",
        table_evidence["2bebfd51-ea82-4b8f-8b7b-eeec1d930b08"]["catalog_version"],
        "search_queries",
        (("id", "BIGINT"), ("created_at", "TIMESTAMP")),
        [],
    )
    results = _table(
        "c4757cc8-3934-480c-8203-ac4f719b18a5",
        table_evidence["c4757cc8-3934-480c-8203-ac4f719b18a5"]["catalog_version"],
        "search_results",
        (("query_id", "BIGINT"), ("latency_ms", "INTEGER"), ("used_chunks", "INTEGER")),
        [
            {
                "constraint_type": "FOREIGN_KEY",
                "columns": ["query_id"],
                "referred_columns": ["docker_postgres_bot.bot.public.search_queries.id"],
                "relationship_type": "ONE_TO_ONE",
            }
        ],
    )
    metrics = _table(
        "d170a5e5-5686-4c4e-a1d2-4442f03d93d0",
        table_evidence["d170a5e5-5686-4c4e-a1d2-4442f03d93d0"]["catalog_version"],
        "answer_context_metrics",
        (
            ("query_id", "BIGINT"),
            ("effective_mode", "TEXT"),
            ("noise_char_share", "DOUBLE"),
            ("cited_source_count", "INTEGER"),
        ),
        [
            {
                "constraint_type": "FOREIGN_KEY",
                "columns": ["query_id"],
                "referred_columns": ["docker_postgres_bot.bot.public.search_queries.id"],
                "relationship_type": "ONE_TO_ONE",
            }
        ],
    )
    tables = (queries, results, metrics)
    snapshot = {
        "format": "ragflow-sql-schema-snapshot",
        "schema_version": "1",
        "status": "READY",
        "original_requirements": case["accepted_requirements"],
        "source": {"stale": False},
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
    prefix = "docker_postgres_bot.bot.public"
    return {
        "schema_version": "1",
        "schema_snapshot": snapshot,
        "accepted_requirements": case["accepted_requirements"],
        "accepted_schema": [
            {
                "entity_id": table["id"],
                "version": table["version"],
                "schema_fingerprint": table["schema_fingerprint"],
            }
            for table in tables
        ],
        "specification": {
            "dialect": "postgres",
            "from": {"entity_id": queries["id"], "alias": "q"},
            "select": [
                {"id": "query_id", "kind": "column", "column_id": f"{prefix}.search_queries.id", "alias": "query_id", "grain": None},
                {"id": "requested_at", "kind": "column", "column_id": f"{prefix}.search_queries.created_at", "alias": "requested_at", "grain": None},
                {"id": "latency_ms", "kind": "column", "column_id": f"{prefix}.search_results.latency_ms", "alias": "latency_ms", "grain": None},
                {"id": "used_chunks", "kind": "column", "column_id": f"{prefix}.search_results.used_chunks", "alias": "used_chunks", "grain": None},
                {"id": "effective_mode", "kind": "column", "column_id": f"{prefix}.answer_context_metrics.effective_mode", "alias": "effective_mode", "grain": None},
                {"id": "noise_char_share", "kind": "column", "column_id": f"{prefix}.answer_context_metrics.noise_char_share", "alias": "noise_char_share", "grain": None},
                {"id": "cited_source_count", "kind": "column", "column_id": f"{prefix}.answer_context_metrics.cited_source_count", "alias": "cited_source_count", "grain": None},
            ],
            "joins": [
                {
                    "id": "join-results",
                    "join_type": "INNER",
                    "entity_id": results["id"],
                    "alias": "r",
                    "left_column_id": f"{prefix}.search_queries.id",
                    "right_column_id": f"{prefix}.search_results.query_id",
                    "description": "Результат принадлежит поисковому запросу",
                    "decision": "user",
                    "confirmed": True,
                },
                {
                    "id": "join-metrics",
                    "join_type": "INNER",
                    "entity_id": metrics["id"],
                    "alias": "m",
                    "left_column_id": f"{prefix}.search_queries.id",
                    "right_column_id": f"{prefix}.answer_context_metrics.query_id",
                    "description": "Метрики контекста принадлежат поисковому запросу",
                    "decision": "user",
                    "confirmed": True,
                },
            ],
            "filters": [
                {
                    "id": "date-from",
                    "column_id": f"{prefix}.search_queries.created_at",
                    "operator": "gte",
                    "parameter": "date_from",
                    "description": "Начало периода включительно",
                    "decision": "user",
                    "confirmed": True,
                },
                {
                    "id": "date-to",
                    "column_id": f"{prefix}.search_queries.created_at",
                    "operator": "lt",
                    "parameter": "date_to",
                    "description": "Конец периода исключительно",
                    "decision": "user",
                    "confirmed": True,
                },
                {
                    "id": "latency",
                    "column_id": f"{prefix}.search_results.latency_ms",
                    "operator": "gte",
                    "parameter": "latency_min_ms",
                    "description": "Медленный ответ от 1500 мс",
                    "decision": "user",
                    "confirmed": True,
                },
                {
                    "id": "noise",
                    "column_id": f"{prefix}.answer_context_metrics.noise_char_share",
                    "operator": "gte",
                    "parameter": "noise_min_share",
                    "description": "Доля шума от 0,25",
                    "decision": "user",
                    "confirmed": True,
                },
            ],
            "order_by": [
                {"select_item_id": "latency_ms", "direction": "DESC"},
                {"select_item_id": "query_id", "direction": "ASC"},
            ],
            "parameters": [
                {"name": "date_from", "type": "date", "value": "2026-08-01"},
                {"name": "date_to", "type": "date", "value": "2026-09-01"},
                {"name": "latency_min_ms", "type": "integer", "value": 1500},
                {"name": "noise_min_share", "type": "decimal", "value": "0.25"},
                {"name": "row_limit", "type": "integer", "value": 50},
            ],
            "limit_parameter": "row_limit",
        },
    }


def test_primary_real_omd_case_compiles_to_exact_physical_postgres_relations():
    cases = _cases()
    result = compile_query_payload(_primary_payload())

    assert result["status"] == "READY"
    assert result["sql"] == cases["cases"][0]["expected_sql"]
    assert result["parameters"] == cases["cases"][0]["parameters"]
    assert result["guard"] == {
        "status": "PASS",
        "dialect": "postgres",
        "statement_count": 1,
        "read_only": True,
        "tables": ["public.answer_context_metrics", "public.search_queries", "public.search_results"],
        "parameters": ["date_from", "date_to", "latency_min_ms", "noise_min_share", "row_limit"],
    }
    assert "docker_postgres_bot.bot" not in result["sql"]


def test_primary_real_omd_case_resolves_every_relation_through_one_exact_binding():
    payload = _primary_payload()
    payload.pop("specification")
    payload["selected_profile_id"] = None
    profile = ExecutionProfileRecord(
        id="profile-bot-ro",
        name="OMD bot PostgreSQL RO",
        dialect="postgres",
        allowed_schemas=("public", "imoex", "rtts"),
        statement_timeout_ms=15_000,
        max_rows=1_000,
        max_result_bytes=5_000_000,
        enabled=True,
        version=1,
        policy_valid=True,
        connector_available=True,
        target_database="bot",
    )
    binding = CatalogBindingRecord(
        id="binding-bot-public",
        catalog_service="docker_postgres_bot",
        catalog_database="bot",
        catalog_schema="public",
        execution_profile_id=profile.id,
        enabled=True,
        version=1,
    )

    result = resolve_execution_profile(parse_resolve_execution_profile_command(payload), [profile], [binding])

    assert result["status"] == "BOUND"
    assert result["selection"]["profile"]["target_database"] == "bot"
    assert result["selection"]["relations"] == [
        {
            "entity_id": "2bebfd51-ea82-4b8f-8b7b-eeec1d930b08",
            "catalog_fqn": "docker_postgres_bot.bot.public.search_queries",
            "physical_relation": "public.search_queries",
        },
        {
            "entity_id": "c4757cc8-3934-480c-8203-ac4f719b18a5",
            "catalog_fqn": "docker_postgres_bot.bot.public.search_results",
            "physical_relation": "public.search_results",
        },
        {
            "entity_id": "d170a5e5-5686-4c4e-a1d2-4442f03d93d0",
            "catalog_fqn": "docker_postgres_bot.bot.public.answer_context_metrics",
            "physical_relation": "public.answer_context_metrics",
        },
    ]
