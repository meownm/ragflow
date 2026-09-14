"""Catalog-bound LLM proposal for the SQL constructor.

The planner may suggest a structured draft, but cannot produce executable SQL
or approve its own JOIN/WHERE decisions. Every identifier is checked against an
accepted schema snapshot before the proposal reaches the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol, Sequence

from business_documents.sql_query.query_specification import (
    AcceptedSchemaEntry,
    SchemaSnapshot,
    parse_accepted_schema,
    parse_parameter_value,
    parse_schema_snapshot,
    QuerySpecificationValidationError,
    schema_acceptance_issues,
)


QUERY_PLAN_VERSION = "1"
MAX_PLAN_SELECT_ITEMS = 100
MAX_PLAN_FILTERS = 64
MAX_PLAN_QUESTIONS = 12
MAX_PLAN_ROW_LIMIT = 10_000
MAX_PLANNER_CONTEXT_COLUMNS = 400

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_SELECT_KINDS = {"column", "sum", "avg", "min", "max", "count", "count_distinct", "date_bucket"}
_DATE_GRAINS = {"day", "week", "month", "quarter", "year"}
_JOIN_TYPES = {"INNER", "LEFT"}
_FILTER_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "like", "ilike", "in", "not_in", "is_null", "is_not_null"}
_PARAMETER_TYPES = {"text", "integer", "decimal", "boolean", "date", "datetime", "text_list", "integer_list"}
_LIST_OPERATORS = {"in", "not_in"}
_PARAMETERLESS_OPERATORS = {"is_null", "is_not_null"}
_ORDER_DIRECTIONS = {"ASC", "DESC"}


class QueryPlanValidationError(ValueError):
    """The planning request or untrusted proposal violates the contract."""


class QueryPlanUnavailable(RuntimeError):
    """The optional tenant planner could not return a usable proposal."""


class QueryPlanner(Protocol):
    async def propose(self, command: "PlanQueryCommand") -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PlanQueryCommand:
    tenant_id: str
    locale: str
    snapshot: SchemaSnapshot
    accepted_requirements: str
    accepted_schema: tuple[AcceptedSchemaEntry, ...]


def _record(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise QueryPlanValidationError(f"{field} must be an object")
    return value


def _closed(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise QueryPlanValidationError(f"Unknown {field} fields: {', '.join(sorted(unknown))}")


def _text(value: Any, field: str, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QueryPlanValidationError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise QueryPlanValidationError(f"{field} exceeds {maximum} characters")
    return result


def _optional_text(value: Any, field: str, maximum: int = 2_000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryPlanValidationError(f"{field} must be a string or null")
    result = value.strip()
    if len(result) > maximum:
        raise QueryPlanValidationError(f"{field} exceeds {maximum} characters")
    return result or None


def _array(value: Any, field: str, maximum: int, *, allow_empty: bool = True) -> Sequence[Any]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a non-empty" if not allow_empty else "an"
        raise QueryPlanValidationError(f"{field} must be {qualifier} array")
    if len(value) > maximum:
        raise QueryPlanValidationError(f"{field} must contain no more than {maximum} items")
    return value


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field, 128)
    if not _IDENTIFIER.fullmatch(result):
        raise QueryPlanValidationError(f"{field} must be a safe identifier")
    return result


def parse_plan_query_command(payload: Any, *, tenant_id: str) -> PlanQueryCommand:
    request = _record(payload, "request")
    _closed(request, {"schema_version", "schema_snapshot", "accepted_requirements", "accepted_schema", "locale"}, "request")
    if request.get("schema_version") != QUERY_PLAN_VERSION:
        raise QueryPlanValidationError("schema_version is unsupported")
    locale = _text(request.get("locale"), "locale", 8)
    if locale not in {"ru", "en"}:
        raise QueryPlanValidationError("locale must be ru or en")
    accepted_requirements = _text(request.get("accepted_requirements"), "accepted_requirements", 20_000)
    try:
        snapshot = parse_schema_snapshot(request.get("schema_snapshot"))
        accepted_schema = parse_accepted_schema(request.get("accepted_schema"))
    except QuerySpecificationValidationError as exc:
        raise QueryPlanValidationError(str(exc)) from exc
    issues = schema_acceptance_issues(snapshot, accepted_requirements, accepted_schema)
    if issues:
        raise QueryPlanValidationError(issues[0].message)
    return PlanQueryCommand(
        tenant_id=_text(tenant_id, "tenant_id", 500),
        locale=locale,
        snapshot=snapshot,
        accepted_requirements=accepted_requirements,
        accepted_schema=accepted_schema,
    )


def planner_context(command: PlanQueryCommand) -> dict[str, Any]:
    """Return a bounded, secret-free catalog context for the tenant LLM."""

    column_count = sum(len(table.columns) for table in command.snapshot.tables)
    if column_count > MAX_PLANNER_CONTEXT_COLUMNS:
        raise QueryPlanUnavailable(f"Accepted schema has {column_count} columns; planner limit is {MAX_PLANNER_CONTEXT_COLUMNS}")
    return {
        "schema_version": QUERY_PLAN_VERSION,
        "locale": command.locale,
        "requirements": command.accepted_requirements,
        "tables": [
            {
                "entity_id": table.id,
                "fqn": table.fqn,
                "description": table.description[:1_000],
                "columns": [
                    {
                        "column_id": column.id,
                        "name": column.name,
                        "data_type": column.data_type,
                        "description": column.description[:500],
                        "constraint": column.constraint,
                        "glossary_terms": list(column.glossary_terms),
                        "selected_for_output": column.selected,
                    }
                    for column in table.columns
                ],
                "table_constraints": [
                    {
                        "constraint_type": constraint.constraint_type,
                        "columns": list(constraint.columns),
                        "referred_columns": list(constraint.referred_columns),
                        "relationship_type": constraint.relationship_type,
                    }
                    for constraint in table.table_constraints
                ],
            }
            for table in command.snapshot.tables
        ],
    }


def _column_table(snapshot: SchemaSnapshot, column_id: str, field: str) -> str:
    resolved = snapshot.column(column_id)
    if resolved is None:
        raise QueryPlanValidationError(f"{field} is outside the accepted schema snapshot")
    return resolved[0].id


def _normalize_select(snapshot: SchemaSnapshot, values: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    items = _array(values, "proposal.select", MAX_PLAN_SELECT_ITEMS, allow_empty=False)
    result: list[dict[str, Any]] = []
    alias_to_id: dict[str, str] = {}
    for index, raw in enumerate(items):
        field = f"proposal.select[{index}]"
        item = _record(raw, field)
        _closed(item, {"column_id", "kind", "alias", "grain"}, field)
        column_id = _text(item.get("column_id"), f"{field}.column_id", 1_000)
        _column_table(snapshot, column_id, f"{field}.column_id")
        kind = _text(item.get("kind"), f"{field}.kind", 50)
        if kind not in _SELECT_KINDS:
            raise QueryPlanValidationError(f"{field}.kind is unsupported")
        alias = _identifier(item.get("alias"), f"{field}.alias")
        alias_key = alias.casefold()
        if alias_key in alias_to_id:
            raise QueryPlanValidationError("proposal.select aliases must be unique")
        grain = _optional_text(item.get("grain"), f"{field}.grain", 20)
        if (kind == "date_bucket" and grain not in _DATE_GRAINS) or (kind != "date_bucket" and grain is not None):
            raise QueryPlanValidationError(f"{field}.grain is invalid for {kind}")
        item_id = f"select-{index + 1}"
        result.append({"id": item_id, "column_id": column_id, "kind": kind, "alias": alias, "grain": grain})
        alias_to_id[alias_key] = item_id
    return result, alias_to_id


def _normalize_joins(
    snapshot: SchemaSnapshot,
    values: Any,
    *,
    base_entity_id: str,
    aliases: Mapping[str, str],
) -> list[dict[str, Any]]:
    items = _array(values, "proposal.joins", len(snapshot.tables))
    expected_entities = {table.id for table in snapshot.tables} - {base_entity_id}
    bound_entities = {base_entity_id}
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        field = f"proposal.joins[{index}]"
        item = _record(raw, field)
        _closed(item, {"join_type", "entity_id", "left_column_id", "right_column_id", "description"}, field)
        entity_id = _text(item.get("entity_id"), f"{field}.entity_id", 500)
        if entity_id not in expected_entities or entity_id in bound_entities:
            raise QueryPlanValidationError(f"{field}.entity_id is not an unbound accepted table")
        join_type = _text(item.get("join_type"), f"{field}.join_type", 20).upper()
        if join_type not in _JOIN_TYPES:
            raise QueryPlanValidationError(f"{field}.join_type is unsupported")
        left_column_id = _text(item.get("left_column_id"), f"{field}.left_column_id", 1_000)
        right_column_id = _text(item.get("right_column_id"), f"{field}.right_column_id", 1_000)
        if _column_table(snapshot, left_column_id, f"{field}.left_column_id") not in bound_entities:
            raise QueryPlanValidationError(f"{field}.left_column_id must belong to an already bound table")
        if _column_table(snapshot, right_column_id, f"{field}.right_column_id") != entity_id:
            raise QueryPlanValidationError(f"{field}.right_column_id must belong to entity_id")
        result.append(
            {
                "id": f"join-{index + 1}",
                "join_type": join_type,
                "entity_id": entity_id,
                "alias": aliases[entity_id],
                "left_column_id": left_column_id,
                "right_column_id": right_column_id,
                "description": _text(item.get("description"), f"{field}.description"),
                "confirmed": False,
            }
        )
        bound_entities.add(entity_id)
    if bound_entities != {table.id for table in snapshot.tables}:
        raise QueryPlanValidationError("proposal.joins must bind every accepted table exactly once")
    return result


def _parameter_value(value_type: str, value: Any, field: str) -> str:
    try:
        parsed = parse_parameter_value(value_type, value, field)
    except QuerySpecificationValidationError as exc:
        raise QueryPlanValidationError(str(exc)) from exc
    if isinstance(parsed, bool):
        return "true" if parsed else "false"
    if isinstance(parsed, list):
        return ", ".join(str(item) for item in parsed)
    return str(parsed)


def _normalize_filters(snapshot: SchemaSnapshot, values: Any) -> list[dict[str, Any]]:
    items = _array(values, "proposal.filters", MAX_PLAN_FILTERS)
    result: list[dict[str, Any]] = []
    parameter_names: set[str] = set()
    for index, raw in enumerate(items):
        field = f"proposal.filters[{index}]"
        item = _record(raw, field)
        _closed(item, {"column_id", "operator", "parameter_name", "parameter_type", "parameter_value", "description"}, field)
        column_id = _text(item.get("column_id"), f"{field}.column_id", 1_000)
        _column_table(snapshot, column_id, f"{field}.column_id")
        operator = _text(item.get("operator"), f"{field}.operator", 30)
        if operator not in _FILTER_OPERATORS:
            raise QueryPlanValidationError(f"{field}.operator is unsupported")
        parameterless = operator in _PARAMETERLESS_OPERATORS
        parameter_name = _optional_text(item.get("parameter_name"), f"{field}.parameter_name", 128)
        parameter_type = _optional_text(item.get("parameter_type"), f"{field}.parameter_type", 30)
        if parameterless:
            if parameter_name is not None or parameter_type is not None or item.get("parameter_value") is not None:
                raise QueryPlanValidationError(f"{field} must not define a parameter for {operator}")
            parameter_name = ""
            parameter_type = "text"
            parameter_value = ""
        else:
            parameter_name = _identifier(parameter_name, f"{field}.parameter_name")
            if parameter_name in parameter_names:
                raise QueryPlanValidationError("proposal filter parameter names must be unique")
            if parameter_type not in _PARAMETER_TYPES:
                raise QueryPlanValidationError(f"{field}.parameter_type is unsupported")
            if (operator in _LIST_OPERATORS) != parameter_type.endswith("_list"):
                raise QueryPlanValidationError(f"{field}.parameter_type does not match {operator}")
            parameter_value = _parameter_value(parameter_type, item.get("parameter_value"), f"{field}.parameter_value")
            parameter_names.add(parameter_name)
        result.append(
            {
                "id": f"filter-{index + 1}",
                "column_id": column_id,
                "operator": operator,
                "parameter_name": parameter_name,
                "parameter_type": parameter_type,
                "parameter_value": parameter_value,
                "description": _text(item.get("description"), f"{field}.description"),
                "confirmed": False,
            }
        )
    return result


def _normalize_order(values: Any, alias_to_id: Mapping[str, str]) -> list[dict[str, str]]:
    items = _array(values, "proposal.order_by", MAX_PLAN_SELECT_ITEMS, allow_empty=False)
    result: list[dict[str, str]] = []
    used: set[str] = set()
    for index, raw in enumerate(items):
        field = f"proposal.order_by[{index}]"
        item = _record(raw, field)
        _closed(item, {"select_alias", "direction"}, field)
        alias = _identifier(item.get("select_alias"), f"{field}.select_alias").casefold()
        select_id = alias_to_id.get(alias)
        if select_id is None or select_id in used:
            raise QueryPlanValidationError(f"{field}.select_alias is unknown or duplicate")
        direction = _text(item.get("direction"), f"{field}.direction", 10).upper()
        if direction not in _ORDER_DIRECTIONS:
            raise QueryPlanValidationError(f"{field}.direction is unsupported")
        result.append({"select_item_id": select_id, "direction": direction})
        used.add(select_id)
    return result


def normalize_query_plan(command: PlanQueryCommand, value: Any) -> tuple[dict[str, Any], list[str]]:
    proposal = _record(value, "proposal")
    _closed(
        proposal,
        {"schema_version", "base_entity_id", "select", "joins", "filters", "order_by", "row_limit", "clarification_questions"},
        "proposal",
    )
    if proposal.get("schema_version") != QUERY_PLAN_VERSION:
        raise QueryPlanValidationError("proposal.schema_version is unsupported")
    base_entity_id = _text(proposal.get("base_entity_id"), "proposal.base_entity_id", 500)
    if command.snapshot.table(base_entity_id) is None:
        raise QueryPlanValidationError("proposal.base_entity_id is outside the accepted schema snapshot")
    ordered_tables = [
        *[table for table in command.snapshot.tables if table.id == base_entity_id],
        *[table for table in command.snapshot.tables if table.id != base_entity_id],
    ]
    aliases = {table.id: f"t{index + 1}" for index, table in enumerate(ordered_tables)}
    select, alias_to_id = _normalize_select(command.snapshot, proposal.get("select"))
    joins = _normalize_joins(
        command.snapshot,
        proposal.get("joins"),
        base_entity_id=base_entity_id,
        aliases=aliases,
    )
    filters = _normalize_filters(command.snapshot, proposal.get("filters"))
    order_by = _normalize_order(proposal.get("order_by"), alias_to_id)
    row_limit = proposal.get("row_limit")
    if isinstance(row_limit, bool) or not isinstance(row_limit, int) or not 1 <= row_limit <= MAX_PLAN_ROW_LIMIT:
        raise QueryPlanValidationError(f"proposal.row_limit must be an integer from 1 to {MAX_PLAN_ROW_LIMIT}")
    questions = [
        _text(question, f"proposal.clarification_questions[{index}]", 1_000)
        for index, question in enumerate(_array(proposal.get("clarification_questions"), "proposal.clarification_questions", MAX_PLAN_QUESTIONS))
    ]
    return (
        {
            "base_entity_id": base_entity_id,
            "aliases": aliases,
            "select": select,
            "joins": joins,
            "filters": filters,
            "order_by": order_by,
            "row_limit": row_limit,
        },
        questions,
    )


class QueryPlanningScenario:
    def __init__(self, planner: QueryPlanner):
        self._planner = planner

    async def run(self, command: PlanQueryCommand) -> dict[str, Any]:
        try:
            raw = await self._planner.propose(command)
            proposal, questions = normalize_query_plan(command, raw)
        except (QueryPlanUnavailable, QueryPlanValidationError) as exc:
            return {
                "schema_version": QUERY_PLAN_VERSION,
                "status": "FALLBACK",
                "proposal": None,
                "clarification_questions": [],
                "warning": "LLM-план недоступен; продолжите ручную настройку.",
                "diagnostic": type(exc).__name__,
            }
        return {
            "schema_version": QUERY_PLAN_VERSION,
            "status": "NEEDS_CLARIFICATION" if questions else "PROPOSED",
            "proposal": proposal,
            "clarification_questions": questions,
            "warning": None,
            "diagnostic": None,
        }
