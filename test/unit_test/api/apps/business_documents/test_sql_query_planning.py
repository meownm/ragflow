#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.errors import PermissionDeniedError, ValidationError
from api.apps.business_documents.sql_query_planner import (
    BusinessDocumentSqlQueryPlanningService,
    TenantQueryPlanner,
)
from business_documents.sql_query.query_planning import (
    QueryPlanUnavailable,
    QueryPlanningScenario,
    QueryPlanValidationError,
    parse_plan_query_command,
    planner_context,
)
from business_documents.sql_query.query_specification import compile_query_payload


REQUIREMENTS = "Вывести заказы с клиентами, начиная с ID 100."


def _table(entity_id: str, fqn: str, fields: tuple[str, ...]) -> dict:
    technical_name = fqn.rsplit(".", 1)[-1]
    return {
        "id": entity_id,
        "fqn": fqn,
        "technical_name": technical_name,
        "description": f"Описание {technical_name}",
        "version": 3,
        "schema_fingerprint": f"sha256:{entity_id}-v3",
        "schema_loaded": True,
        "columns_truncated": False,
        "columns": [
            {
                "id": f"{fqn}.{name}",
                "name": name,
                "data_type": "BIGINT" if name.endswith("_id") else "TEXT",
                "description": f"Описание {name}",
                "constraint": "PRIMARY_KEY" if name == fields[0] else "",
                "glossary_terms": [],
                "selected": True,
            }
            for name in fields
        ],
    }


def _request() -> dict:
    tables = (
        _table("orders", "dwh.order_fact", ("order_id", "customer_id")),
        _table("customers", "dwh.customer_dim", ("customer_id", "name")),
    )
    tables[0]["table_constraints"] = [
        {
            "constraint_type": "FOREIGN_KEY",
            "columns": ["customer_id"],
            "referred_columns": ["dwh.customer_dim.customer_id"],
            "relationship_type": "MANY_TO_ONE",
        }
    ]
    snapshot = {
        "format": "ragflow-sql-schema-snapshot",
        "schema_version": "1",
        "status": "READY",
        "original_requirements": REQUIREMENTS,
        "source": {"stale": False},
        "requirements": [{"selected_table": table} for table in tables],
        "warnings": [],
    }
    return {
        "schema_version": "1",
        "schema_snapshot": snapshot,
        "accepted_requirements": REQUIREMENTS,
        "accepted_schema": [
            {
                "entity_id": table["id"],
                "version": table["version"],
                "schema_fingerprint": table["schema_fingerprint"],
            }
            for table in tables
        ],
        "locale": "ru",
    }


def _raw_plan() -> dict:
    return {
        "schema_version": "1",
        "base_entity_id": "orders",
        "select": [
            {
                "column_id": "dwh.order_fact.order_id",
                "kind": "column",
                "alias": "order_id",
                "grain": None,
            },
            {
                "column_id": "dwh.customer_dim.name",
                "kind": "column",
                "alias": "customer_name",
                "grain": None,
            },
        ],
        "joins": [
            {
                "join_type": "INNER",
                "entity_id": "customers",
                "left_column_id": "dwh.order_fact.customer_id",
                "right_column_id": "dwh.customer_dim.customer_id",
                "description": "Заказ принадлежит клиенту.",
            }
        ],
        "filters": [
            {
                "column_id": "dwh.order_fact.order_id",
                "operator": "gte",
                "parameter_name": "minimum_order_id",
                "parameter_type": "integer",
                "parameter_value": 100,
                "description": "ID заказа не меньше 100.",
            }
        ],
        "order_by": [{"select_alias": "order_id", "direction": "ASC"}],
        "row_limit": 1000,
        "clarification_questions": [],
    }


class ScriptedPlanner:
    def __init__(self, value: dict):
        self.value = value
        self.calls = []

    async def propose(self, command):
        self.calls.append(command)
        return self.value


@pytest.mark.asyncio
async def test_planner_returns_only_catalog_bound_unconfirmed_draft():
    command = parse_plan_query_command(_request(), tenant_id="tenant-1")
    planner = ScriptedPlanner(_raw_plan())

    result = await QueryPlanningScenario(planner).run(command)

    assert result["status"] == "PROPOSED"
    assert len(planner.calls) == 1
    assert result["proposal"]["aliases"] == {"orders": "t1", "customers": "t2"}
    assert result["proposal"]["joins"][0]["confirmed"] is False
    assert result["proposal"]["filters"][0]["confirmed"] is False
    assert result["proposal"]["filters"][0]["parameter_value"] == "100"
    assert "sql" not in result
    assert "decision" not in result["proposal"]["joins"][0]


@pytest.mark.asyncio
async def test_hallucinated_identifier_and_invalid_typed_value_fail_to_manual_fallback():
    command = parse_plan_query_command(_request(), tenant_id="tenant-1")
    hallucinated = _raw_plan()
    hallucinated["select"][0]["column_id"] = "secret.users.password"
    invalid_value = _raw_plan()
    invalid_value["filters"][0]["parameter_value"] = "one hundred"

    hallucinated_result = await QueryPlanningScenario(ScriptedPlanner(hallucinated)).run(command)
    invalid_value_result = await QueryPlanningScenario(ScriptedPlanner(invalid_value)).run(command)

    assert hallucinated_result["status"] == "FALLBACK"
    assert hallucinated_result["proposal"] is None
    assert invalid_value_result["status"] == "FALLBACK"
    assert invalid_value_result["proposal"] is None
    assert hallucinated_result["diagnostic"] == "QueryPlanValidationError"


def test_planning_request_rejects_stale_or_changed_schema_acceptance():
    stale = _request()
    stale["schema_snapshot"]["source"]["stale"] = True
    changed = _request()
    changed["accepted_requirements"] = "Другие требования"

    with pytest.raises(QueryPlanValidationError, match="устарел"):
        parse_plan_query_command(stale, tenant_id="tenant-1")
    with pytest.raises(QueryPlanValidationError, match="изменились"):
        parse_plan_query_command(changed, tenant_id="tenant-1")


@pytest.mark.asyncio
async def test_confirmed_planner_draft_is_compatible_with_the_independent_compiler():
    request = _request()
    result = await QueryPlanningScenario(ScriptedPlanner(_raw_plan())).run(parse_plan_query_command(request, tenant_id="tenant-1"))
    proposal = result["proposal"]
    compile_request = {key: request[key] for key in ("schema_version", "schema_snapshot", "accepted_requirements", "accepted_schema")}
    compile_request["specification"] = {
        "dialect": "postgres",
        "from": {"entity_id": proposal["base_entity_id"], "alias": proposal["aliases"][proposal["base_entity_id"]]},
        "select": proposal["select"],
        "joins": [
            {
                **join,
                "decision": "user",
                "confirmed": True,
            }
            for join in proposal["joins"]
        ],
        "filters": [
            {
                "id": item["id"],
                "column_id": item["column_id"],
                "operator": item["operator"],
                "parameter": item["parameter_name"],
                "description": item["description"],
                "decision": "user",
                "confirmed": True,
            }
            for item in proposal["filters"]
        ],
        "order_by": proposal["order_by"],
        "parameters": [
            {
                "name": proposal["filters"][0]["parameter_name"],
                "type": proposal["filters"][0]["parameter_type"],
                "value": 100,
            },
            {"name": "row_limit", "type": "integer", "value": proposal["row_limit"]},
        ],
        "limit_parameter": "row_limit",
    }

    compiled = compile_query_payload(compile_request)

    assert compiled["status"] == "READY"
    assert compiled["guard"]["status"] == "PASS"
    assert "JOIN dwh.customer_dim AS t2" in compiled["sql"]


@pytest.mark.asyncio
async def test_tenant_planner_uses_one_bounded_contract_call():
    calls = []

    class FakeLlm:
        async def async_generate(self, tenant_id, system_prompt, payload):
            calls.append((tenant_id, system_prompt, payload))
            return json.dumps(_raw_plan(), ensure_ascii=False)

    command = parse_plan_query_command(_request(), tenant_id="tenant-1")
    result = await TenantQueryPlanner(FakeLlm(), timeout_seconds=1).propose(command)

    assert result == _raw_plan()
    assert len(calls) == 1
    assert calls[0][0] == "tenant-1"
    assert calls[0][2]["job_input"]["task_type"] == "PLAN_SQL_QUERY"
    assert calls[0][2]["prompt"]["name"] == "sql_query_planner"
    assert calls[0][2]["prompt"]["content_hash"].startswith("sha256:")
    assert calls[0][2]["catalog_context"]["requirements"] == REQUIREMENTS
    assert calls[0][2]["catalog_context"]["tables"][0]["columns"][0]["description"] == "Описание order_id"
    assert calls[0][2]["catalog_context"]["tables"][0]["table_constraints"][0] == {
        "constraint_type": "FOREIGN_KEY",
        "columns": ["customer_id"],
        "referred_columns": ["dwh.customer_dim.customer_id"],
        "relationship_type": "MANY_TO_ONE",
    }
    assert "{{" not in calls[0][1]
    assert '"additionalProperties": false' in calls[0][1]
    assert "sql" not in calls[0][2]["catalog_context"]


def test_planner_context_refuses_an_unbounded_schema_instead_of_truncating_it():
    request = _request()
    large_table = _table(
        "wide",
        "dwh.wide_fact",
        tuple(f"field_{index}" for index in range(401)),
    )
    request["schema_snapshot"]["requirements"] = [{"selected_table": large_table}]
    request["accepted_schema"] = [
        {
            "entity_id": "wide",
            "version": 3,
            "schema_fingerprint": "sha256:wide-v3",
        }
    ]
    command = parse_plan_query_command(request, tenant_id="tenant-1")

    with pytest.raises(QueryPlanUnavailable, match="planner limit"):
        planner_context(command)


@pytest.mark.asyncio
async def test_planning_service_checks_acl_before_payload_and_maps_invalid_request():
    with pytest.raises(PermissionDeniedError):
        await BusinessDocumentSqlQueryPlanningService.plan(
            "tenant-1",
            "actor-1",
            {},
            False,
            "AUTHOR_EDITOR",
        )

    invalid = deepcopy(_request())
    invalid["unexpected"] = True
    with pytest.raises(ValidationError) as raised:
        await BusinessDocumentSqlQueryPlanningService.plan(
            "tenant-1",
            "actor-1",
            invalid,
            False,
            "AUTHOR_CREATOR",
        )

    assert raised.value.code == "INVALID_SQL_QUERY_PLAN_REQUEST"


@pytest.mark.asyncio
async def test_planning_service_exposes_prompt_provenance_without_self_approval():
    scenario = QueryPlanningScenario(ScriptedPlanner(_raw_plan()))

    result = await BusinessDocumentSqlQueryPlanningService.plan(
        "tenant-1",
        "actor-1",
        _request(),
        False,
        "AUTHOR_CREATOR",
        scenario=scenario,
    )

    assert result["llm"]["status"] == "APPLIED"
    assert result["llm"]["prompt"]["name"] == "sql_query_planner"
    assert result["proposal"]["joins"][0]["confirmed"] is False
    assert result["proposal"]["filters"][0]["confirmed"] is False
