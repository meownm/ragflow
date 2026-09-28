"""Catalog-bound manual PostgreSQL query specification."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.errors import OptimizeError

from business_documents.sql_query.query_specification import (
    MAX_PARAMETERS,
    MAX_QUERY_ROW_LIMIT,
    QUERY_DIALECT,
    QUERY_SPECIFICATION_VERSION,
    QuerySpecificationValidationError,
    SqlGuardError,
    parse_accepted_schema,
    parse_parameter_value,
    parse_schema_snapshot,
    schema_acceptance_issues,
)


_PARAMETER_TYPES = {"text", "integer", "decimal", "boolean", "date", "datetime", "text_list", "integer_list"}
_ALLOWED_FUNCTIONS = {
    "and", "or", "avg", "cast", "coalesce", "count", "date_trunc", "dense_rank",
    "first_value", "lag", "last_value", "lead", "max", "min", "nullif",
    "rank", "round", "row_number", "sum", "timestamp_trunc",
}
_PROHIBITED_NODES = {
    "Alter", "Analyze", "Call", "Command", "Copy", "Create", "Delete", "Drop",
    "Execute", "Grant", "Insert", "LoadData", "Lock", "Merge", "Pragma",
    "Revoke", "Set", "Transaction", "TruncateTable", "Update", "Use",
}
_PROHIBITED_SCHEMAS = {"pg_catalog", "information_schema"}


def _manual_parameters(value: Any) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) > MAX_PARAMETERS:
        raise QuerySpecificationValidationError("manual parameters must be a bounded array")
    parsed: dict[str, Any] = {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {"name", "type", "value"}:
            raise QuerySpecificationValidationError(f"manual parameters[{index}] is invalid")
        name, kind = item["name"], item["type"]
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None or name in parsed or kind not in _PARAMETER_TYPES:
            raise QuerySpecificationValidationError(f"manual parameters[{index}] name or type is invalid")
        parsed[name] = parse_parameter_value(kind, item["value"], f"manual parameters[{index}].value")
    row_limit = parsed.get("row_limit")
    if isinstance(row_limit, bool) or not isinstance(row_limit, int) or not 1 <= row_limit <= MAX_QUERY_ROW_LIMIT:
        raise QuerySpecificationValidationError("manual SQL requires integer row_limit from 1 to 10000")
    offset = parsed.get("offset")
    if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000):
        raise QuerySpecificationValidationError("manual SQL offset must be a non-negative bounded integer")
    return parsed


def guard_advanced_sql(sql: str, snapshot, parameter_names: list[str]) -> dict[str, Any]:
    """Check the full AST and qualify every referenced column against catalog tables."""
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20_000 or any(marker in sql for marker in (";", "--", "/*")):
        raise SqlGuardError("Manual SQL must be one bounded statement without comments or semicolons")
    try:
        statements = sqlglot.parse(sql, read=QUERY_DIALECT)
    except ParseError as exc:
        raise SqlGuardError("Manual SQL could not be parsed") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise SqlGuardError("Only one SELECT or UNION query is allowed")
    statement = statements[0]
    if statement.args.get("limit") is None or not isinstance(statement.args["limit"].expression, exp.Placeholder) or statement.args["limit"].expression.this != "row_limit":
        raise SqlGuardError("The outer query must use LIMIT :row_limit")
    if statement.args.get("offset") is not None and not isinstance(statement.args["offset"].expression, exp.Placeholder):
        raise SqlGuardError("Pagination must use a bound OFFSET parameter")
    for node in statement.walk():
        if type(node).__name__ in _PROHIBITED_NODES or (isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count)):
            raise SqlGuardError("Manual SQL contains a prohibited statement or wildcard")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise SqlGuardError("Recursive CTEs are not supported")
        if isinstance(node, exp.Select) and (node.args.get("into") is not None or node.args.get("locks")):
            raise SqlGuardError("SELECT INTO and row locks are prohibited")
        if isinstance(node, exp.Func) and node.sql_name().casefold() not in _ALLOWED_FUNCTIONS:
            raise SqlGuardError(f"Function {node.sql_name()} is not allowed")
        if isinstance(node, exp.Literal) and (not node.is_string or str(node.this).casefold() not in {"day", "week", "month", "quarter", "year"}):
            raise SqlGuardError("Values must use bound parameters")
    placeholders = {str(node.this) for node in statement.find_all(exp.Placeholder)}
    if placeholders != set(parameter_names):
        raise SqlGuardError("Manual SQL placeholders do not match the parameter list")
    cte_names = {cte.alias_or_name.casefold() for cte in statement.find_all(exp.CTE)}
    allowed_tables = {table.physical_relation.casefold() for table in snapshot.tables}
    observed: set[str] = set()
    for table in statement.find_all(exp.Table):
        parts = [part.name for part in table.parts]
        if len(parts) == 1 and parts[0].casefold() in cte_names:
            continue
        name = ".".join(parts).casefold()
        if len(parts) != 2 or parts[0].casefold() in _PROHIBITED_SCHEMAS or name not in allowed_tables:
            raise SqlGuardError("Manual SQL references a table outside the accepted catalog schema")
        observed.add(name)
    if not observed:
        raise SqlGuardError("Manual SQL must read an accepted catalog table")
    schema: dict[str, dict[str, dict[str, str]]] = {}
    for table in snapshot.tables:
        schema.setdefault(table.schema, {})[table.technical_name] = {column.name: "UNKNOWN" for column in table.columns}
    try:
        qualify(statement.copy(), schema=schema, dialect=QUERY_DIALECT, validate_qualify_columns=True, identify=False)
    except (OptimizeError, ValueError, TypeError) as exc:
        raise SqlGuardError("Manual SQL has unknown or ambiguous catalog fields") from exc
    first_select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if first_select is None or not first_select.expressions or any(not isinstance(item, exp.Alias) for item in first_select.expressions):
        raise SqlGuardError("Every output column must have an explicit alias")
    output_columns = [item.alias for item in first_select.expressions]
    if len({alias.casefold() for alias in output_columns}) != len(output_columns):
        raise SqlGuardError("Output column aliases must be unique")
    return {
        "status": "PASS", "dialect": QUERY_DIALECT, "statement_count": 1,
        "read_only": True, "tables": sorted(observed), "parameters": sorted(placeholders),
        "output_columns": output_columns, "mode": "manual",
    }


def compile_manual_query_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {"schema_version", "schema_snapshot", "accepted_requirements", "accepted_schema", "manual_sql", "parameters"} or payload.get("schema_version") != QUERY_SPECIFICATION_VERSION:
        raise QuerySpecificationValidationError("Manual SQL request has unsupported fields or version")
    snapshot = parse_schema_snapshot(payload["schema_snapshot"])
    accepted_requirements = payload["accepted_requirements"]
    if not isinstance(accepted_requirements, str):
        raise QuerySpecificationValidationError("accepted_requirements must be text")
    accepted_schema = parse_accepted_schema(payload["accepted_schema"])
    issues = schema_acceptance_issues(snapshot, accepted_requirements, accepted_schema)
    response = {
        "schema_version": QUERY_SPECIFICATION_VERSION,
        "status": "NEEDS_CLARIFICATION" if issues else "READY",
        "snapshot_fingerprint": snapshot.fingerprint,
        "blocking_issues": [issue.to_api() for issue in issues],
        "sql": None, "parameters": {}, "guard": {"status": "NOT_RUN"},
    }
    if issues:
        return response
    parameters = _manual_parameters(payload["parameters"])
    sql = payload["manual_sql"]
    guard = guard_advanced_sql(sql, snapshot, list(parameters))
    response.update({"sql": sql, "parameters": parameters, "guard": guard, "output_columns": guard["output_columns"]})
    return response
