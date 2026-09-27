"""Build a compiler command from accepted SQL project artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ProjectCompilationError(ValueError):
    """Accepted project artifacts cannot form a compiler command."""


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectCompilationError(f"{name} is missing or invalid")
    return value


def _parameter_value(kind: str, value: Any) -> Any:
    source = str(value).strip()
    if kind == "integer":
        return int(source)
    if kind == "decimal":
        return source
    if kind == "boolean":
        if source not in {"true", "false"}:
            raise ProjectCompilationError("Boolean parameter must be true or false")
        return source == "true"
    if kind == "integer_list":
        return [int(part.strip()) for part in source.replace("\n", ",").split(",") if part.strip()]
    if kind == "text_list":
        return [part.strip() for part in source.replace("\n", ",").split(",") if part.strip()]
    return source


def build_project_compile_command(
    requirements: Mapping[str, Any],
    schema: Mapping[str, Any],
    query: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate accepted artifacts without trusting a browser-supplied snapshot."""

    statements = [str(item.get("statement", "")).strip() for item in requirements.get("requirements", []) if isinstance(item, Mapping)]
    statements = [statement for statement in statements if statement]
    snapshot = _object(schema.get("schema_snapshot"), "schema snapshot")
    accepted_schema = schema.get("accepted_schema")
    if query.get("mode") == "manual":
        if not statements or not isinstance(accepted_schema, list):
            raise ProjectCompilationError("Project does not contain accepted requirements and schema")
        return {
            "schema_version": "1",
            "schema_snapshot": dict(snapshot),
            "accepted_requirements": "\n".join(f"- {statement}" for statement in statements),
            "accepted_schema": accepted_schema,
            "manual_sql": query.get("sql"),
            "parameters": query.get("parameters"),
        }
    aliases = _object(query.get("aliases"), "aliases")
    filters = query.get("filters")
    joins = query.get("joins")
    if not statements or not isinstance(accepted_schema, list) or not isinstance(filters, list) or not isinstance(joins, list):
        raise ProjectCompilationError("Project does not contain complete accepted artifacts")
    base = query.get("base_entity_id")
    if not isinstance(base, str) or not isinstance(aliases.get(base), str):
        raise ProjectCompilationError("Base table alias is missing")
    parameters: list[dict[str, Any]] = []
    compiled_filters: list[dict[str, Any]] = []
    for raw in filters:
        item = _object(raw, "filter")
        if item.get("confirmed") is not True or item.get("decision") != "user":
            raise ProjectCompilationError("A filter has not been confirmed by the user")
        operator = item.get("operator")
        parameter_name = item.get("parameter_name")
        parameter = None if operator in {"is_null", "is_not_null"} else parameter_name
        if parameter is not None:
            if not isinstance(parameter, str) or not parameter:
                raise ProjectCompilationError("Filter parameter is missing")
            kind = item.get("parameter_type")
            if not isinstance(kind, str):
                raise ProjectCompilationError("Filter parameter type is missing")
            try:
                value = _parameter_value(kind, item.get("parameter_value"))
            except (TypeError, ValueError) as exc:
                raise ProjectCompilationError("Filter parameter value is invalid") from exc
            parameters.append({"name": parameter, "type": kind, "value": value})
        compiled_filters.append(
            {
                "id": item.get("id"),
                "column_id": item.get("column_id"),
                "operator": operator,
                "parameter": parameter,
                "description": item.get("description"),
                "decision": "user",
                "confirmed": item["confirmed"],
            }
        )
    row_limit = query.get("row_limit")
    if isinstance(row_limit, bool) or not isinstance(row_limit, int):
        raise ProjectCompilationError("Row limit is invalid")
    parameters.append({"name": "row_limit", "type": "integer", "value": row_limit})
    compiled_joins = []
    for raw in joins:
        item = _object(raw, "join")
        if item.get("confirmed") is not True or item.get("decision") != "user":
            raise ProjectCompilationError("A JOIN has not been confirmed by the user")
        compiled_joins.append(dict(item))
    return {
        "schema_version": "1",
        "schema_snapshot": dict(snapshot),
        "accepted_requirements": "\n".join(f"- {statement}" for statement in statements),
        "accepted_schema": accepted_schema,
        "specification": {
            "dialect": "postgres",
            "from": {"entity_id": base, "alias": aliases[base]},
            "select": query.get("select"),
            "joins": compiled_joins,
            "filters": compiled_filters,
            "order_by": query.get("order_by"),
            "parameters": parameters,
            "limit_parameter": "row_limit",
        },
    }
