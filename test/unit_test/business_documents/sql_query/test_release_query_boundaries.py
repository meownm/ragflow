"""Untrusted constructor inputs and accepted-project compilation boundaries."""

from copy import deepcopy
import asyncio

import pytest

from business_documents.sql_query.project_compilation import ProjectCompilationError, build_project_compile_command
from business_documents.sql_query.query_planning import (
    QueryPlanningScenario,
    QueryPlanUnavailable,
    QueryPlanValidationError,
    normalize_query_plan,
    parse_plan_query_command,
    planner_context,
)
from business_documents.sql_query.query_specification import (
    QuerySpecificationValidationError,
    SqlGuardError,
    compile_query_payload,
    guard_read_only_sql,
    parse_schema_snapshot,
)


def _payload():
    tables = []
    for entity, names in (("orders", ("id", "customer_id", "amount")), ("customers", ("id", "name"))):
        tables.append(
            {
                "id": entity,
                "fqn": f"sales.{entity}",
                "technical_name": entity,
                "schema": "sales",
                "version": 2,
                "schema_fingerprint": f"sha256:{entity}-2",
                "schema_loaded": True,
                "columns_truncated": False,
                "columns": [{"id": f"sales.{entity}.{name}", "name": name, "selected": True} for name in names],
            }
        )
    snapshot = {
        "format": "ragflow-sql-schema-snapshot",
        "schema_version": "1",
        "status": "READY",
        "original_requirements": "- List orders and customer names",
        "source": {"stale": False},
        "requirements": [{"selected_table": table} for table in tables],
    }
    return {
        "schema_version": "1",
        "schema_snapshot": snapshot,
        "accepted_requirements": snapshot["original_requirements"],
        "accepted_schema": [{"entity_id": table["id"], "version": 2, "schema_fingerprint": table["schema_fingerprint"]} for table in tables],
        "specification": {
            "dialect": "postgres",
            "from": {"entity_id": "orders", "alias": "o"},
            "select": [{"id": "customer", "kind": "column", "column_id": "sales.customers.name", "alias": "customer"}],
            "joins": [
                {
                    "id": "customer",
                    "join_type": "LEFT",
                    "entity_id": "customers",
                    "alias": "c",
                    "left_column_id": "sales.orders.customer_id",
                    "right_column_id": "sales.customers.id",
                    "description": "Customer relation",
                    "decision": "user",
                    "confirmed": True,
                }
            ],
            "filters": [{"id": "amount", "column_id": "sales.orders.amount", "operator": "gte", "parameter": "minimum", "description": "Minimum amount", "decision": "user", "confirmed": True}],
            "parameters": [{"name": "minimum", "type": "integer", "value": 10}, {"name": "row_limit", "type": "integer", "value": 100}],
            "order_by": [{"select_item_id": "customer", "direction": "ASC"}],
            "limit_parameter": "row_limit",
        },
    }


def _replace(payload, path, value):
    current = payload
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value


@pytest.mark.parametrize(
    "path,value,message",
    [
        (("schema_version",), "2", "schema_version"),
        (("accepted_schema",), [], "non-empty"),
        (("accepted_requirements",), " ", "non-empty"),
        (("schema_snapshot", "source", "stale"), "false", "boolean"),
        (("schema_snapshot", "requirements", 0, "selected_table", "schema_loaded"), 1, "schema_loaded"),
        (("schema_snapshot", "requirements", 0, "selected_table", "columns_truncated"), None, "columns_truncated"),
        (("schema_snapshot", "requirements", 0, "selected_table", "columns"), [], "non-empty"),
        (("schema_snapshot", "requirements", 0, "selected_table", "columns", 0, "selected"), 1, "boolean"),
        (("schema_snapshot", "requirements", 0, "selected_table", "columns", 0, "name"), "id; DROP TABLE users", "safe SQL"),
        (("schema_snapshot", "requirements", 0, "selected_table", "version"), True, "finite number"),
        (("schema_snapshot", "requirements", 0, "selected_table", "version"), float("inf"), "finite number"),
        (("schema_snapshot", "requirements", 0, "selected_table", "schema_fingerprint"), "unverified", "sha256"),
        (("schema_snapshot", "requirements", 0, "selected_table", "fqn"), "other.orders", "does not match"),
        (("specification", "dialect"), "mysql", "postgres"),
        (("specification", "from", "alias"), "o--", "safe SQL"),
        (("specification", "select", 0, "kind"), "raw_sql", "unsupported"),
        (("specification", "select", 0, "grain"), "month", "grain"),
        (("specification", "select", 0, "column_id"), "sales.secret.value", "outside"),
        (("specification", "joins", 0, "join_type"), "CROSS", "unsupported"),
        (("specification", "joins", 0, "confirmed"), 1, "boolean"),
        (("specification", "joins", 0, "decision"), "llm", "unsupported"),
        (("specification", "joins", 0, "entity_id"), "secret", "outside"),
        (("specification", "joins", 0, "entity_id"), "orders", "already bound"),
        (("specification", "joins", 0, "alias"), "O", "duplicate"),
        (("specification", "joins", 0, "left_column_id"), "sales.customers.id", "already bound"),
        (("specification", "joins", 0, "right_column_id"), "sales.orders.id", "already bound"),
        (("specification", "joins", 0, "right_column_id"), "sales.customers.secret", "unknown column"),
        (("specification", "filters", 0, "operator"), "raw_sql", "unsupported"),
        (("specification", "filters", 0, "operator"), "is_null", "does not match"),
        (("specification", "filters", 0, "parameter"), None, "does not match"),
        (("specification", "filters", 0, "confirmed"), "true", "boolean"),
        (("specification", "filters", 0, "column_id"), "sales.secret.id", "outside"),
        (("specification", "order_by", 0, "direction"), "RANDOM", "unsupported"),
        (("specification", "order_by", 0, "select_item_id"), "missing", "unknown select"),
        (("specification", "parameters", 0, "type"), "sql", "unsupported"),
        (("specification", "parameters", 0, "value"), True, "integer"),
        (("specification", "parameters", 1, "value"), 0, "limit parameter"),
        (("specification", "parameters", 1, "value"), 10001, "limit parameter"),
    ],
)
def test_compiler_rejects_invalid_user_inputs(path, value, message):
    payload = _payload()
    _replace(payload, path, value)
    with pytest.raises(QuerySpecificationValidationError, match=message):
        compile_query_payload(payload)


@pytest.mark.parametrize(
    "path,value,code",
    [
        (("schema_snapshot", "status"), "NEEDS_CLARIFICATION", "SCHEMA_NOT_READY"),
        (("schema_snapshot", "source", "stale"), True, "SCHEMA_STALE"),
        (("accepted_requirements",), "New requirements", "REQUIREMENTS_CHANGED"),
        (("accepted_schema", 0, "version"), 3, "SCHEMA_VERSION_CHANGED"),
        (("accepted_schema", 0, "schema_fingerprint"), "sha256:changed", "SCHEMA_VERSION_CHANGED"),
        (("specification", "joins", 0, "decision"), None, "JOIN_DECISION_REQUIRED"),
        (("specification", "joins", 0, "confirmed"), False, "JOIN_DECISION_REQUIRED"),
        (("specification", "filters", 0, "decision"), None, "FILTER_DECISION_REQUIRED"),
        (("specification", "order_by"), [], "ORDER_BY_REQUIRED"),
    ],
)
def test_unaccepted_state_never_returns_executable_sql(path, value, code):
    payload = _payload()
    _replace(payload, path, value)
    result = compile_query_payload(payload)
    assert result["status"] == "NEEDS_CLARIFICATION"
    assert code in {issue["code"] for issue in result["blocking_issues"]}
    assert result["sql"] is None and result["parameters"] == {}
    assert result["guard"] == {"status": "NOT_RUN"}


def test_unjoined_filter_and_output_are_blocked_together():
    payload = _payload()
    payload["specification"]["joins"] = []
    payload["specification"]["filters"][0]["column_id"] = "sales.customers.name"
    result = compile_query_payload(payload)
    issues = result["blocking_issues"]
    assert [issue["code"] for issue in issues] == ["TABLE_NOT_JOINED", "TABLE_NOT_JOINED"]
    assert result["sql"] is None


@pytest.mark.parametrize(
    "collection,message", [("select", "must be unique"), ("joins", "duplicate ids"), ("filters", "duplicate ids"), ("parameters", "duplicate names"), ("order_by", "duplicate select")]
)
def test_duplicate_inputs_cannot_silently_overwrite_decisions(collection, message):
    payload = _payload()
    payload["specification"][collection].append(deepcopy(payload["specification"][collection][0]))
    with pytest.raises(QuerySpecificationValidationError, match=message):
        compile_query_payload(payload)


@pytest.mark.parametrize(
    "kind,value,operator,expected",
    [
        ("boolean", False, "eq", False),
        ("decimal", "12.50", "gte", "12.50"),
        ("date", "2026-10-11", "eq", "2026-10-11"),
        ("datetime", "2026-10-11T12:00:00Z", "gte", "2026-10-11T12:00:00Z"),
        ("text_list", ["O'Reilly", "Robert; DROP TABLE users"], "in", ["O'Reilly", "Robert; DROP TABLE users"]),
        ("integer_list", [1, 2], "not_in", [1, 2]),
    ],
)
def test_typed_values_remain_separate_from_read_only_sql(kind, value, operator, expected):
    payload = _payload()
    payload["specification"]["parameters"][0].update(type=kind, value=value)
    payload["specification"]["filters"][0]["operator"] = operator
    result = compile_query_payload(payload)
    assert result["status"] == "READY" and result["guard"]["status"] == "PASS"
    assert result["parameters"]["minimum"] == expected
    assert "LEFT JOIN sales.customers AS c" in result["sql"]
    if kind == "text_list":
        assert "Robert" not in result["sql"] and "O'Reilly" not in result["sql"]


@pytest.mark.parametrize(
    "kind,value,operator,message",
    [
        ("boolean", "true", "eq", "boolean"),
        ("decimal", {}, "gte", "decimal"),
        ("decimal", "not-a-number", "gte", "decimal"),
        ("date", "2026-02-30", "eq", "ISO date"),
        ("decimal", "NaN", "gte", "finite decimal"),
        ("decimal", "Infinity", "gte", "finite decimal"),
        ("decimal", "-Infinity", "gte", "finite decimal"),
        ("decimal", True, "gte", "decimal"),
        ("datetime", "yesterday", "eq", "ISO datetime"),
        ("text_list", [], "in", "non-empty"),
        ("integer_list", [True], "in", "integer"),
        ("text_list", ["x"] * 101, "in", "no more than 100"),
        ("integer", 2, "in", "list parameter"),
        ("integer_list", [2], "eq", "scalar parameter"),
    ],
)
def test_parameter_types_and_list_limits_are_enforced(kind, value, operator, message):
    payload = _payload()
    payload["specification"]["parameters"][0].update(type=kind, value=value)
    payload["specification"]["filters"][0]["operator"] = operator
    with pytest.raises(QuerySpecificationValidationError, match=message):
        compile_query_payload(payload)


def test_not_in_nullable_column_uses_negated_bound_array_without_coalescing_null():
    payload = _payload()
    payload["specification"]["filters"][0].update(operator="not_in", column_id="sales.customers.name")
    payload["specification"]["parameters"][0].update(type="text_list", value=["Archived", "Deleted"])
    result = compile_query_payload(payload)
    # A LEFT JOIN can produce NULL names. Negating ANY retains SQL three-valued
    # logic instead of silently including them with an invented COALESCE value.
    assert "WHERE NOT (c.name = ANY(:minimum))" in result["sql"]
    assert "COALESCE" not in result["sql"] and "Archived" not in result["sql"]
    assert result["parameters"]["minimum"] == ["Archived", "Deleted"]
    assert result["guard"]["status"] == "PASS"


@pytest.mark.parametrize(
    "sql,message",
    [
        ("SELECT o.id FROM sales.orders o LIMIT :row_limit;", "semicolons"),
        ("SELECT o.id FROM sales.orders o -- comment\n LIMIT :row_limit", "comments"),
        ("SELECT o.id FROM sales.orders o /* comment */ LIMIT :row_limit", "comments"),
        ("UPDATE sales.orders SET id = :row_limit", "SELECT"),
        ("SELECT * FROM sales.orders LIMIT :row_limit", "SELECT \\*"),
        ("SELECT o.id FROM sales.orders o", "row limit"),
        ("SELECT id FROM pg_catalog.users LIMIT :row_limit", "System schemas"),
        ("SELECT id FROM sales.secret LIMIT :row_limit", "outside"),
        ("SELECT pg_sleep(:row_limit) FROM sales.orders LIMIT :row_limit", "Function"),
        ("SELECT o.id = ALL(:row_limit) FROM sales.orders o LIMIT :row_limit", "Function"),
        ("SELECT o.id FROM sales.orders o WHERE o.id = 1 LIMIT :row_limit", "literals"),
        ("SELECT o.id FROM sales.orders o LIMIT :different", "placeholders"),
        ("SELECT o.id INTO sales.copy FROM sales.orders o LIMIT :row_limit", "prohibited"),
        ("SELECT o.id FROM sales.orders o LIMIT :row_limit FOR UPDATE", "prohibited"),
        ("SELECT ( FROM sales.orders LIMIT :row_limit", "parsed"),
    ],
)
def test_independent_guard_rejects_unsafe_statements(sql, message):
    with pytest.raises(SqlGuardError, match=message):
        guard_read_only_sql(sql, allowed_tables=["sales.orders"], parameter_names=["row_limit"])


def _plan_request():
    request = _payload()
    del request["specification"]
    request["locale"] = "en"
    return request


def _proposal():
    spec = _payload()["specification"]
    return {
        "schema_version": "1",
        "base_entity_id": "orders",
        "select": [{key: value for key, value in spec["select"][0].items() if key != "id"}],
        "joins": [{key: value for key, value in spec["joins"][0].items() if key not in {"id", "alias", "decision", "confirmed"}}],
        "filters": [{"column_id": "sales.orders.amount", "operator": "gte", "parameter_name": "minimum", "parameter_type": "integer", "parameter_value": 10, "description": "Minimum amount"}],
        "order_by": [{"select_alias": "customer", "direction": "ASC"}],
        "row_limit": 100,
        "clarification_questions": [],
    }


@pytest.mark.parametrize(
    "path,value,message",
    [
        (("schema_version",), "2", "schema_version"),
        (("base_entity_id",), "secret", "outside"),
        (("select",), [], "non-empty"),
        (("select", 0, "alias"), "customer;select", "safe identifier"),
        (("select", 0, "grain"), False, "string or null"),
        (("select", 0, "column_id"), "sales.secret.name", "outside"),
        (("select", 0, "kind"), "execute", "unsupported"),
        (("joins",), [], "every accepted table"),
        (("joins", 0, "entity_id"), "orders", "unbound accepted"),
        (("joins", 0, "join_type"), "CROSS", "unsupported"),
        (("joins", 0, "left_column_id"), "sales.customers.id", "already bound"),
        (("joins", 0, "right_column_id"), "sales.orders.id", "belong to entity"),
        (("filters", 0, "operator"), "sql", "unsupported"),
        (("filters", 0, "parameter_type"), "sql", "unsupported"),
        (("filters", 0, "parameter_value"), "ten", "integer"),
        (("filters", 0, "operator"), "in", "does not match"),
        (("filters", 0, "operator"), "is_null", "must not define"),
        (("order_by", 0, "select_alias"), "secret", "unknown or duplicate"),
        (("order_by", 0, "direction"), "SHUFFLE", "unsupported"),
        (("row_limit",), True, "row_limit"),
        (("row_limit",), 0, "row_limit"),
        (("clarification_questions",), ["Why?"] * 13, "no more than 12"),
    ],
)
def test_model_proposal_cannot_escape_accepted_constructor_contract(path, value, message):
    proposal = _proposal()
    _replace(proposal, path, value)
    command = parse_plan_query_command(_plan_request(), tenant_id="disposable-tenant")
    with pytest.raises(QueryPlanValidationError, match=message):
        normalize_query_plan(command, proposal)


@pytest.mark.parametrize("kind,value,operator,expected", [("boolean", True, "eq", "true"), ("integer_list", [10, 20], "in", "10, 20"), ("text_list", ["one", "two"], "not_in", "one, two")])
def test_planner_preserves_typed_parameter_values_without_approving_them(kind, value, operator, expected):
    proposal = _proposal()
    proposal["filters"][0].update(parameter_type=kind, parameter_value=value, operator=operator)
    result, questions = normalize_query_plan(parse_plan_query_command(_plan_request(), tenant_id="tenant"), proposal)
    assert questions == []
    assert result["filters"][0]["parameter_value"] == expected
    assert result["filters"][0]["confirmed"] is False
    assert result["joins"][0]["confirmed"] is False
    assert "sql" not in result and "decision" not in result["joins"][0]


@pytest.mark.parametrize("fault", [QueryPlanUnavailable("Model offline"), asyncio.CancelledError(), RuntimeError("Unexpected adapter failure")])
async def test_planner_expected_unavailability_falls_back_but_cancellation_and_defects_propagate(fault):
    class Planner:
        async def propose(self, command):
            assert command.tenant_id == "tenant"
            raise fault

    scenario = QueryPlanningScenario(Planner())
    command = parse_plan_query_command(_plan_request(), tenant_id="tenant")
    if isinstance(fault, QueryPlanUnavailable):
        result = await scenario.run(command)
        assert result["status"] == "FALLBACK" and result["proposal"] is None
        assert result["diagnostic"] == "QueryPlanUnavailable"
    else:
        with pytest.raises(type(fault)):
            await scenario.run(command)


def _project():
    payload = _payload()
    spec = payload["specification"]
    query = {
        "base_entity_id": "orders",
        "aliases": {"orders": "o", "customers": "c"},
        "select": spec["select"],
        "joins": spec["joins"],
        "order_by": spec["order_by"],
        "row_limit": 100,
        "filters": [
            {
                "id": "amount",
                "column_id": "sales.orders.amount",
                "operator": "gte",
                "parameter_name": "minimum",
                "parameter_type": "integer",
                "parameter_value": "10",
                "description": "Minimum amount",
                "confirmed": True,
                "decision": "user",
            }
        ],
    }
    return {"requirements": [{"statement": "List orders and customer names"}]}, {"schema_snapshot": payload["schema_snapshot"], "accepted_schema": payload["accepted_schema"]}, query


@pytest.mark.parametrize(
    "kind,value,operator,expected",
    [
        ("boolean", "false", "eq", False),
        ("decimal", "12.50", "gte", "12.50"),
        ("integer_list", "1, 2\n3,, ", "in", [1, 2, 3]),
        ("text_list", " first,second\nthird,, ", "not_in", ["first", "second", "third"]),
        ("text", " customer ", "eq", "customer"),
    ],
)
def test_accepted_project_round_trips_typed_user_values_to_compiler(kind, value, operator, expected):
    requirements, schema, query = _project()
    query["filters"][0].update(parameter_type=kind, parameter_value=value, operator=operator)
    result = compile_query_payload(build_project_compile_command(requirements, schema, query))
    assert result["status"] == "READY"
    assert result["parameters"] == {"minimum": expected, "row_limit": 100}


@pytest.mark.parametrize("collection,decision,confirmed", [("filters", "automatic_exact", True), ("filters", "user", 1), ("joins", "user", False), ("joins", None, True)])
def test_project_cannot_treat_machine_or_truthy_values_as_user_confirmation(collection, decision, confirmed):
    requirements, schema, query = _project()
    query[collection][0].update(decision=decision, confirmed=confirmed)
    with pytest.raises(ProjectCompilationError, match="confirmed by the user"):
        build_project_compile_command(requirements, schema, query)


@pytest.mark.parametrize(
    "path,value,message",
    [
        (("aliases",), None, "aliases"),
        (("aliases", "orders"), None, "alias"),
        (("filters",), {}, "complete accepted"),
        (("joins",), None, "complete accepted"),
        (("filters", 0, "parameter_name"), "", "parameter is missing"),
        (("filters", 0, "parameter_type"), None, "type is missing"),
        (("filters", 0, "parameter_value"), "one", "value is invalid"),
        (("row_limit",), True, "Row limit"),
    ],
)
def test_project_rejects_incomplete_accepted_artifacts(path, value, message):
    requirements, schema, query = _project()
    _replace(query, path, value)
    with pytest.raises(ProjectCompilationError, match=message):
        build_project_compile_command(requirements, schema, query)


def test_schema_conflicting_versions_and_duplicate_physical_tables_are_rejected():
    snapshot = _payload()["schema_snapshot"]
    duplicate = deepcopy(snapshot["requirements"][0])
    duplicate["selected_table"]["version"] = 3
    snapshot["requirements"].append(duplicate)
    with pytest.raises(QuerySpecificationValidationError, match="conflicting versions"):
        parse_schema_snapshot(snapshot)
    duplicate["selected_table"]["id"] = "orders-copy"
    with pytest.raises(QuerySpecificationValidationError, match="duplicate physical"):
        parse_schema_snapshot(snapshot)


def test_planner_context_has_a_bounded_column_budget():
    request = _plan_request()
    table = request["schema_snapshot"]["requirements"][0]["selected_table"]
    table["columns"] = [{"id": f"sales.orders.field_{index}", "name": f"field_{index}", "selected": False} for index in range(401)]
    command = parse_plan_query_command(request, tenant_id="tenant")
    with pytest.raises(QueryPlanUnavailable, match="planner limit is 400"):
        planner_context(command)


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("specification",),
        ("specification", "from"),
        ("specification", "select", 0),
        ("specification", "joins", 0),
        ("specification", "filters", 0),
        ("specification", "parameters", 0),
        ("accepted_schema", 0),
    ],
)
def test_unknown_wire_fields_cannot_inject_sql_or_rebind_accepted_schema(path):
    payload = _payload()
    target = payload
    for part in path:
        target = target[part]
    target["raw_sql"] = "DROP TABLE users"
    with pytest.raises(QuerySpecificationValidationError, match="Unknown"):
        compile_query_payload(payload)


def test_missing_filter_parameter_and_limit_require_clarification_without_values():
    payload = _payload()
    payload["specification"]["parameters"] = []
    result = compile_query_payload(payload)
    assert [issue["code"] for issue in result["blocking_issues"]] == ["FILTER_PARAMETER_REQUIRED", "LIMIT_REQUIRED"]
    assert result["sql"] is None and result["parameters"] == {}


def test_unused_parameter_is_rejected_instead_of_becoming_a_hidden_binding():
    payload = _payload()
    payload["specification"]["parameters"].append({"name": "unused", "type": "text", "value": "unexpected"})
    with pytest.raises(QuerySpecificationValidationError, match="Unused parameters.*unused"):
        compile_query_payload(payload)


@pytest.mark.parametrize("operator", ["is_null", "is_not_null"])
def test_project_parameterless_filter_discards_editor_parameter_and_compiles_null_check(operator):
    requirements, schema, query = _project()
    query["filters"][0].update(operator=operator, parameter_name="unused", parameter_value="invalid integer")
    result = compile_query_payload(build_project_compile_command(requirements, schema, query))
    assert result["parameters"] == {"row_limit": 100}
    assert ("o.amount IS NULL" if operator == "is_null" else "o.amount IS NOT NULL") in result["sql"]
    assert result["guard"]["status"] == "PASS"


@pytest.mark.parametrize("value", ["True", "1", "yes"])
def test_project_boolean_editor_requires_explicit_true_or_false(value):
    requirements, schema, query = _project()
    query["filters"][0].update(parameter_type="boolean", parameter_value=value)
    with pytest.raises(ProjectCompilationError, match="value is invalid"):
        build_project_compile_command(requirements, schema, query)


def test_planning_request_cannot_use_stale_or_mismatched_accepted_schema():
    request = _plan_request()
    request["schema_snapshot"]["source"]["stale"] = True
    with pytest.raises(QueryPlanValidationError):
        parse_plan_query_command(request, tenant_id="tenant")
    request["schema_snapshot"]["source"]["stale"] = False
    request["accepted_schema"][0]["version"] = 3
    with pytest.raises(QueryPlanValidationError):
        parse_plan_query_command(request, tenant_id="tenant")


async def test_model_questions_remain_clarification_and_all_decisions_unconfirmed():
    class Planner:
        async def propose(self, command):
            proposal = _proposal()
            proposal["clarification_questions"] = ["Include cancelled orders?"]
            return proposal

    result = await QueryPlanningScenario(Planner()).run(parse_plan_query_command(_plan_request(), tenant_id="tenant"))
    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["clarification_questions"] == ["Include cancelled orders?"]
    assert all(item["confirmed"] is False for item in result["proposal"]["joins"] + result["proposal"]["filters"])


@pytest.mark.parametrize(
    "path,value,message",
    [
        (("schema_snapshot", "format"), "unknown", "format or version"),
        (("schema_snapshot", "requirements", 0, "selected_table", "fqn"), "sales.orders;SELECT", "safe catalog"),
        (("accepted_requirements",), "x" * 20001, "exceeds 20000"),
        (("specification", "select", 0, "grain"), False, "string or null"),
        (("specification", "select", 0, "grain"), "x" * 21, "exceeds 20"),
    ],
)
def test_snapshot_and_identifier_wire_limits_are_not_silently_truncated(path, value, message):
    payload = _payload()
    _replace(payload, path, value)
    with pytest.raises(QuerySpecificationValidationError, match=message):
        compile_query_payload(payload)


def test_snapshot_unresolved_terms_are_ignored_but_missing_all_selections_is_invalid():
    snapshot = _payload()["schema_snapshot"]
    snapshot["requirements"].append({"term": "unknown", "selected_table": None})
    assert [table.id for table in parse_schema_snapshot(snapshot).tables] == ["orders", "customers"]
    snapshot["requirements"] = [{"selected_table": None}]
    with pytest.raises(QuerySpecificationValidationError, match="must contain selected tables"):
        parse_schema_snapshot(snapshot)


def test_duplicate_column_or_accepted_entity_cannot_shadow_an_existing_binding():
    payload = _payload()
    columns = payload["schema_snapshot"]["requirements"][0]["selected_table"]["columns"]
    columns.append(deepcopy(columns[0]))
    with pytest.raises(QuerySpecificationValidationError, match="columns contains duplicate ids"):
        compile_query_payload(payload)
    columns.pop()
    payload["accepted_schema"].append(deepcopy(payload["accepted_schema"][0]))
    with pytest.raises(QuerySpecificationValidationError, match="duplicate entity ids"):
        compile_query_payload(payload)


def test_empty_constructor_draft_reports_choices_instead_of_compiling():
    payload = _payload()
    payload["specification"].update(select=[], joins=[], filters=[], order_by=[], parameters=[{"name": "row_limit", "type": "integer", "value": 100}])
    payload["specification"]["from"]["entity_id"] = "missing"
    result = compile_query_payload(payload)
    assert [issue["code"] for issue in result["blocking_issues"]] == ["BASE_TABLE_REQUIRED", "SELECT_REQUIRED", "ORDER_BY_REQUIRED"]
    assert result["sql"] is None


def test_count_distinct_aggregate_without_plain_fields_does_not_invent_group_by():
    payload = _payload()
    payload["specification"]["select"][0].update(kind="count_distinct", column_id="sales.orders.id", alias="order_count")
    result = compile_query_payload(payload)
    assert "COUNT(DISTINCT o.id) AS order_count" in result["sql"]
    assert "GROUP BY" not in result["sql"]
    assert result["output_columns"] == ["order_count"]
    assert result["guard"]["status"] == "PASS"


@pytest.mark.parametrize(
    "path,value,message",
    [
        (("locale",), "fr", "locale"),
        (("locale",), "", "non-empty string"),
        (("accepted_requirements",), "x" * 20001, "exceeds 20000"),
        (("schema_snapshot",), None, "must be an object"),
    ],
)
def test_invalid_planning_request_is_rejected_before_model_execution(path, value, message):
    request = _plan_request()
    _replace(request, path, value)
    with pytest.raises(QueryPlanValidationError, match=message):
        parse_plan_query_command(request, tenant_id="tenant")


@pytest.mark.parametrize(
    "collection,changes,message",
    [
        ("select", {}, "aliases must be unique"),
        ("filters", {}, "parameter names must be unique"),
        ("select", {"alias": "other", "grain": "month"}, "grain"),
    ],
)
def test_model_cannot_duplicate_output_aliases_or_parameter_names(collection, changes, message):
    proposal = _proposal()
    duplicate = deepcopy(proposal[collection][0])
    duplicate.update(changes)
    proposal[collection].append(duplicate)
    with pytest.raises(QueryPlanValidationError, match=message):
        normalize_query_plan(parse_plan_query_command(_plan_request(), tenant_id="tenant"), proposal)


def test_model_parameterless_filter_has_no_synthetic_parameter_or_approval():
    proposal = _proposal()
    proposal["filters"][0].update(operator="is_null", parameter_name=None, parameter_type=None, parameter_value=None)
    result, _ = normalize_query_plan(parse_plan_query_command(_plan_request(), tenant_id="tenant"), proposal)
    assert result["filters"][0]["parameter_name"] == ""
    assert result["filters"][0]["parameter_value"] == ""
    assert result["filters"][0]["confirmed"] is False


def test_manual_project_preserves_accepted_snapshot_and_separate_parameters():
    requirements, schema, query = _project()
    query.update(mode="manual", sql="SELECT o.id FROM sales.orders o LIMIT :row_limit", parameters=[{"name": "row_limit", "type": "integer", "value": 100}])
    result = build_project_compile_command(requirements, schema, query)
    assert result["schema_snapshot"] == schema["schema_snapshot"]
    assert result["accepted_schema"] == schema["accepted_schema"]
    assert result["manual_sql"] == query["sql"] and result["parameters"] == query["parameters"]
    assert "specification" not in result
    with pytest.raises(ProjectCompilationError, match="accepted requirements and schema"):
        build_project_compile_command({"requirements": []}, schema, query)
