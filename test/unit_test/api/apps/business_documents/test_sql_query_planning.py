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

from business_documents.application.errors import PermissionDeniedError, ValidationError
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


@pytest.mark.parametrize("cap", [True, 0, -1, 10001, "100", 100.5])
def test_source_row_limit_cap_requires_a_bounded_integer(cap):
    with pytest.raises(QueryPlanValidationError, match="row_limit_cap"):
        parse_plan_query_command({**_request(), "row_limit_cap": cap}, tenant_id="tenant")


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected", [(50, "PROPOSED"), (100, "PROPOSED"), (1000, "NEEDS_CLARIFICATION")])
async def test_source_cap_is_visible_to_planner_and_excess_requires_a_choice(requested, expected):
    command = parse_plan_query_command({**_request(), "row_limit_cap": 100}, tenant_id="tenant")
    assert planner_context(command)["row_limit_cap"] == 100
    raw = _raw_plan()
    raw["row_limit"] = requested
    result = await QueryPlanningScenario(ScriptedPlanner(raw)).run(command)
    assert result["status"] == expected
    assert result["proposal"]["row_limit"] == requested
    assert bool(result["clarification_questions"]) == (requested > 100)


@pytest.mark.asyncio
async def test_per_group_winners_survive_proposal_acceptance_and_catalog_guard():
    from business_documents.sql_query.project_compilation import build_project_compile_command

    request = _request()
    request["accepted_requirements"] = "- " + REQUIREMENTS
    request["schema_snapshot"]["original_requirements"] = "- " + REQUIREMENTS
    raw = _raw_plan()
    raw["top_per_group"] = {
        "partition_by": ["customer_name"],
        "order_by": [{"select_alias": "order_id", "direction": "DESC"}],
        "row_limit": 1,
    }
    result = await QueryPlanningScenario(ScriptedPlanner(raw)).run(parse_plan_query_command(request, tenant_id="tenant"))
    proposal = result["proposal"]
    assert result["status"] == "PROPOSED"
    assert proposal["top_per_group"] == {
        "partition_by": ["select-2"],
        "order_by": [{"select_item_id": "select-1", "direction": "DESC"}],
        "row_limit": 1,
    }
    # The test actor explicitly confirms the same proposal as the application.
    for kind in ("joins", "filters"):
        for item in proposal[kind]:
            item.update({"decision": "user", "confirmed": True})
    compiled = compile_query_payload(
        build_project_compile_command(
            {"requirements": [{"statement": REQUIREMENTS.removeprefix("- ")}]},
            {key: request[key] for key in ("schema_snapshot", "accepted_schema")},
            proposal,
        )
    )
    assert compiled["status"] == "READY"
    assert compiled["guard"]["status"] == "PASS"
    assert compiled["output_columns"] == ["order_id", "customer_name"]
    assert compiled["parameters"]["__ragflow_group_limit"] == 1
    assert compiled["parameters"]["row_limit"] == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        {"partition_by": ["secret"]},
        {"partition_by": ["customer_name", "customer_name"]},
        {"row_limit": 0},
        {"row_limit": True},
        {"order_by": [{"select_alias": "secret", "direction": "DESC"}]},
    ],
)
async def test_invalid_per_group_selection_never_becomes_an_executable_draft(damage):
    raw = _raw_plan()
    raw["top_per_group"] = {
        "partition_by": ["customer_name"],
        "order_by": [{"select_alias": "order_id", "direction": "DESC"}],
        "row_limit": 1,
        **damage,
    }
    result = await QueryPlanningScenario(ScriptedPlanner(raw)).run(parse_plan_query_command(_request(), tenant_id="tenant"))
    assert result["status"] == "FALLBACK"
    assert result["proposal"] is None


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
@pytest.mark.parametrize("clarifications", [[], [{"question": "Какой год и часовой пояс?", "answer": "2026, Pacific/Apia"}]])
async def test_tenant_planner_uses_one_bounded_contract_call(clarifications):
    calls = []

    class FakeLlm:
        async def async_generate(self, tenant_id, system_prompt, payload):
            calls.append((tenant_id, system_prompt, payload))
            return json.dumps(_raw_plan(), ensure_ascii=False)

    request = _request()
    request["clarifications"] = clarifications
    command = parse_plan_query_command(request, tenant_id="tenant-1")
    result = await TenantQueryPlanner(FakeLlm(), timeout_seconds=1).propose(command)

    assert result == _raw_plan()
    assert len(calls) == 1
    assert calls[0][0] == "tenant-1"
    assert calls[0][2]["job_input"]["task_type"] == "PLAN_SQL_QUERY"
    assert calls[0][2]["prompt"]["name"] == "sql_query_planner"
    assert calls[0][2]["prompt"]["content_hash"].startswith("sha256:")
    context_requirements = calls[0][2]["catalog_context"]["requirements"]
    assert context_requirements.startswith(REQUIREMENTS)
    if clarifications:
        assert clarifications[0]["answer"] in context_requirements
    else:
        assert context_requirements == REQUIREMENTS
    assert command.accepted_requirements == command.snapshot.original_requirements == REQUIREMENTS
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


@pytest.mark.asyncio
async def test_model_response_schema_limits_references_without_mutating_the_shared_contract():
    from jsonschema import ValidationError as SchemaError, validate
    from api.apps.business_documents.assets import contract_schema

    captured = []

    class Model:
        async def async_generate(self, tenant_id, system_prompt, payload):
            schema, _ = json.JSONDecoder().raw_decode(system_prompt[system_prompt.index("{") :])
            captured.append(schema)
            return _raw_plan()

    request = _request()
    await TenantQueryPlanner(Model()).propose(parse_plan_query_command(request, tenant_id="tenant"))
    validate(_raw_plan(), captured[0])
    invented = _raw_plan()
    invented["select"][0]["column_id"] = "orders.order_id"
    with pytest.raises(SchemaError):
        validate(invented, captured[0])
    # Request-scoped allowed IDs must not leak into a following tenant's cached contract.
    assert "enum" not in contract_schema("sql_query_plan")["properties"]["select"]["items"]["properties"]["column_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["datetime", "schema"])
async def test_invalid_model_format_gets_one_correction_with_original_context(invalid):
    request = _request()
    table = request["schema_snapshot"]["requirements"][0]["selected_table"]
    column = deepcopy(table["columns"][0])
    column.update(id="dwh.order_fact.created_at", name="created_at", data_type="TIMESTAMPZ", constraint="")
    table["columns"].append(column)
    good = _raw_plan()
    good["filters"][0].update(column_id=column["id"], parameter_type="datetime", parameter_value="2026-09-01T00:00:00+00:00")
    bad = deepcopy(good)
    if invalid == "datetime":
        # Exact invalid spelling observed in the real calendar Qwen response.
        bad["filters"][0]["parameter_value"] = "2026-09-01 00:00:00 UTC"
    else:
        bad["invented_field"] = True
    calls = []

    class Model:
        async def async_generate(self, tenant_id, system, payload):
            calls.append(deepcopy(payload))
            return bad if len(calls) == 1 else good

    command = parse_plan_query_command(request, tenant_id="tenant")
    result = await TenantQueryPlanner(Model()).propose(command)
    assert result == good and len(calls) == 2
    assert calls[1]["catalog_context"] == calls[0]["catalog_context"]
    assert calls[1]["prompt"] == calls[0]["prompt"]
    assert calls[1]["format_correction"]["error"]
    if invalid == "datetime":
        assert "ISO datetime" in calls[1]["format_correction"]["error"]
    assert bad == json.loads(calls[1]["format_correction"]["previous_response"])


@pytest.mark.asyncio
async def test_repeated_invalid_model_plan_stays_unavailable_without_a_third_call():
    calls = []
    request = _request()
    table = request["schema_snapshot"]["requirements"][0]["selected_table"]
    column = deepcopy(table["columns"][0])
    column.update(id="dwh.order_fact.created_at", name="created_at", data_type="TIMESTAMPZ", constraint="")
    table["columns"].append(column)

    class Model:
        async def async_generate(self, *_args):
            calls.append(True)
            raw = _raw_plan()
            raw["filters"][0].update(column_id=column["id"], parameter_type="datetime", parameter_value="2026-09-01 00:00:00 UTC")
            return raw

    command = parse_plan_query_command(request, tenant_id="tenant")
    result = await QueryPlanningScenario(TenantQueryPlanner(Model())).run(command)
    assert result["status"] == "FALLBACK" and result["proposal"] is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_model_timeout_is_not_retried_as_a_format_error():
    calls = []

    class Model:
        async def async_generate(self, *_args):
            calls.append(True)
            raise TimeoutError("upstream timeout")

    with pytest.raises(QueryPlanUnavailable):
        await TenantQueryPlanner(Model()).propose(parse_plan_query_command(_request(), tenant_id="tenant"))
    assert len(calls) == 1


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
