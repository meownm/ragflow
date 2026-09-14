"""Closed query specification, decision gate, compiler, and SQL guard.

The language model and browser may propose a structured query, but neither is
trusted to produce executable SQL.  This module accepts only catalog-backed
identifiers from an accepted schema snapshot and compiles one PostgreSQL
``SELECT`` statement with separately bound parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

import sqlglot
from sqlglot import exp


QUERY_SPECIFICATION_VERSION = "1"
QUERY_DIALECT = "postgres"
MAX_QUERY_TABLES = 8
MAX_QUERY_COLUMNS_PER_TABLE = 500
MAX_SELECT_ITEMS = 100
MAX_JOINS = 16
MAX_FILTERS = 64
MAX_PARAMETERS = 128
MAX_LIST_PARAMETER_ITEMS = 100
MAX_QUERY_ROW_LIMIT = 10_000

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_PARAMETER_TYPES = {
    "text",
    "integer",
    "decimal",
    "boolean",
    "date",
    "datetime",
    "text_list",
    "integer_list",
}
_SELECT_KINDS = {
    "column",
    "sum",
    "avg",
    "min",
    "max",
    "count",
    "count_distinct",
    "date_bucket",
}
_AGGREGATE_KINDS = {"sum", "avg", "min", "max", "count", "count_distinct"}
_DATE_GRAINS = {"day", "week", "month", "quarter", "year"}
_JOIN_TYPES = {"INNER", "LEFT"}
_DECISIONS = {"user", "automatic_exact"}
_FILTER_OPERATORS = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "like": "LIKE",
    "ilike": "ILIKE",
    "in": "= ANY",
    "not_in": "<> ALL",
    "is_null": "IS NULL",
    "is_not_null": "IS NOT NULL",
}
_PARAMETERLESS_OPERATORS = {"is_null", "is_not_null"}
_LIST_OPERATORS = {"in", "not_in"}
_ORDER_DIRECTIONS = {"ASC", "DESC"}
_PROHIBITED_AST_NODES = {
    "Alter",
    "Analyze",
    "Call",
    "Command",
    "Copy",
    "Create",
    "Delete",
    "Drop",
    "Execute",
    "Grant",
    "Insert",
    "LoadData",
    "Lock",
    "Merge",
    "Pragma",
    "Revoke",
    "Set",
    "Transaction",
    "TruncateTable",
    "Update",
    "Use",
}
_PROHIBITED_FUNCTIONS = {
    "current_setting",
    "dblink",
    "dblink_connect",
    "lo_export",
    "lo_import",
    "pg_ls_dir",
    "pg_read_binary_file",
    "pg_read_file",
    "pg_sleep",
    "query_to_xml",
}
_PROHIBITED_SCHEMAS = {"information_schema", "pg_catalog"}


class QuerySpecificationValidationError(ValueError):
    """The request does not satisfy the closed query contract."""


class SqlGuardError(ValueError):
    """Compiled SQL violates the independent read-only policy."""


@dataclass(frozen=True, slots=True)
class BlockingIssue:
    code: str
    path: str
    message: str

    def to_api(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True, slots=True)
class SnapshotColumn:
    id: str
    name: str
    data_type: str
    description: str
    constraint: str
    glossary_terms: tuple[str, ...]
    selected: bool


@dataclass(frozen=True, slots=True)
class SnapshotTableConstraint:
    constraint_type: str
    columns: tuple[str, ...]
    referred_columns: tuple[str, ...]
    relationship_type: str | None


@dataclass(frozen=True, slots=True)
class SnapshotTable:
    id: str
    fqn: str
    technical_name: str
    description: str
    service: str
    database: str
    schema: str
    version: float
    schema_fingerprint: str
    columns: tuple[SnapshotColumn, ...]
    table_constraints: tuple[SnapshotTableConstraint, ...]

    def column(self, column_id: str) -> SnapshotColumn | None:
        return next((column for column in self.columns if column.id == column_id), None)

    @property
    def physical_relation(self) -> str:
        """Return the PostgreSQL relation behind the catalog entity.

        OpenMetadata identifies a table as ``service.database.schema.table``.
        PostgreSQL connections are already bound to one database by the
        execution profile, so executable SQL must use only ``schema.table``.
        """

        if self.schema:
            return f"{self.schema}.{self.technical_name}"
        return ".".join(self.fqn.split(".")[-2:])


@dataclass(frozen=True, slots=True)
class AcceptedSchemaEntry:
    entity_id: str
    version: float
    schema_fingerprint: str


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    status: str
    original_requirements: str
    stale: bool
    tables: tuple[SnapshotTable, ...]
    fingerprint: str

    def table(self, entity_id: str) -> SnapshotTable | None:
        return next((table for table in self.tables if table.id == entity_id), None)

    def column(self, column_id: str) -> tuple[SnapshotTable, SnapshotColumn] | None:
        for table in self.tables:
            column = table.column(column_id)
            if column is not None:
                return table, column
        return None


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    value_type: str
    value: Any


@dataclass(frozen=True, slots=True)
class SelectItem:
    id: str
    kind: str
    column_id: str
    alias: str
    grain: str | None

    @property
    def aggregate(self) -> bool:
        return self.kind in _AGGREGATE_KINDS


@dataclass(frozen=True, slots=True)
class JoinItem:
    id: str
    join_type: str
    entity_id: str
    alias: str
    left_column_id: str
    right_column_id: str
    description: str
    decision: str | None
    confirmed: bool


@dataclass(frozen=True, slots=True)
class FilterItem:
    id: str
    column_id: str
    operator: str
    parameter: str | None
    description: str
    decision: str | None
    confirmed: bool


@dataclass(frozen=True, slots=True)
class OrderItem:
    select_item_id: str
    direction: str


@dataclass(frozen=True, slots=True)
class QuerySpecification:
    base_entity_id: str
    base_alias: str
    select: tuple[SelectItem, ...]
    joins: tuple[JoinItem, ...]
    filters: tuple[FilterItem, ...]
    order_by: tuple[OrderItem, ...]
    parameters: tuple[Parameter, ...]
    limit_parameter: str


@dataclass(frozen=True, slots=True)
class CompileQueryCommand:
    snapshot: SchemaSnapshot
    accepted_requirements: str
    accepted_schema: tuple[AcceptedSchemaEntry, ...]
    specification: QuerySpecification


def _record(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise QuerySpecificationValidationError(f"{field} must be an object")
    return value


def _closed(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise QuerySpecificationValidationError(f"Unknown {field} fields: {', '.join(sorted(unknown))}")


def _text(value: Any, field: str, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuerySpecificationValidationError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise QuerySpecificationValidationError(f"{field} exceeds {maximum} characters")
    return result


def _optional_text(value: Any, field: str, maximum: int = 2_000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise QuerySpecificationValidationError(f"{field} must be a string or null")
    result = value.strip()
    if len(result) > maximum:
        raise QuerySpecificationValidationError(f"{field} exceeds {maximum} characters")
    return result or None


def _array(value: Any, field: str, maximum: int, *, allow_empty: bool = True) -> Sequence[Any]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a non-empty" if not allow_empty else "an"
        raise QuerySpecificationValidationError(f"{field} must be {qualifier} array")
    if len(value) > maximum:
        raise QuerySpecificationValidationError(f"{field} must contain no more than {maximum} items")
    return value


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field, 128)
    if not _IDENTIFIER.fullmatch(result):
        raise QuerySpecificationValidationError(f"{field} must be a safe SQL identifier")
    return result


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QuerySpecificationValidationError(f"{field} must be a finite number")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise QuerySpecificationValidationError(f"{field} must be a finite number")
    return result


def _safe_fqn(value: Any, field: str) -> str:
    result = _text(value, field, 1_000)
    if len(result.split(".")) not in {2, 3, 4} or any(not _IDENTIFIER.fullmatch(part) for part in result.split(".")):
        raise QuerySpecificationValidationError(f"{field} must contain from two to four safe identifier parts")
    return result


def _string_tuple(value: Any, field: str, maximum: int, item_maximum: int) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(_text(item, f"{field}[{index}]", item_maximum) for index, item in enumerate(_array(value, field, maximum)))


def _snapshot_column(value: Any, field: str) -> SnapshotColumn:
    item = _record(value, field)
    _closed(
        item,
        {"id", "name", "fqn", "data_type", "description", "constraint", "glossary_terms", "selected"},
        field,
    )
    selected = item.get("selected")
    if not isinstance(selected, bool):
        raise QuerySpecificationValidationError(f"{field}.selected must be a boolean")
    return SnapshotColumn(
        id=_text(item.get("id"), f"{field}.id", 1_000),
        name=_identifier(item.get("name"), f"{field}.name"),
        data_type=str(item.get("data_type") or "").strip()[:500],
        description=str(item.get("description") or "").strip()[:2_000],
        constraint=str(item.get("constraint") or "").strip()[:500],
        glossary_terms=_string_tuple(item.get("glossary_terms"), f"{field}.glossary_terms", 50, 300),
        selected=selected,
    )


def _snapshot_table_constraint(value: Any, field: str) -> SnapshotTableConstraint:
    item = _record(value, field)
    _closed(item, {"constraint_type", "columns", "referred_columns", "relationship_type"}, field)
    return SnapshotTableConstraint(
        constraint_type=_text(item.get("constraint_type"), f"{field}.constraint_type", 200),
        columns=_string_tuple(item.get("columns"), f"{field}.columns", 32, 300),
        referred_columns=_string_tuple(item.get("referred_columns"), f"{field}.referred_columns", 32, 1_000),
        relationship_type=_optional_text(item.get("relationship_type"), f"{field}.relationship_type", 200),
    )


def _snapshot_table_identity(item: Mapping[str, Any], field: str) -> tuple[str, str, str, str, str]:
    fqn = _safe_fqn(item.get("fqn"), f"{field}.fqn")
    technical_name = _identifier(item.get("technical_name"), f"{field}.technical_name")
    service = _optional_text(item.get("service"), f"{field}.service", 128) or ""
    database = _optional_text(item.get("database"), f"{field}.database", 128) or ""
    schema_value = _optional_text(item.get("schema"), f"{field}.schema", 128)
    schema = _identifier(schema_value, f"{field}.schema") if schema_value else ""
    fqn_parts = fqn.split(".")
    if schema and tuple(part.casefold() for part in fqn_parts[-2:]) != (schema.casefold(), technical_name.casefold()):
        raise QuerySpecificationValidationError(f"{field}.fqn does not match schema and technical_name")
    if len(fqn_parts) == 4:
        if not all((service, database, schema)):
            raise QuerySpecificationValidationError(f"{field} requires service, database, and schema for an OpenMetadata FQN")
        expected = (service.casefold(), database.casefold(), schema.casefold(), technical_name.casefold())
        if tuple(part.casefold() for part in fqn_parts) != expected:
            raise QuerySpecificationValidationError(f"{field}.fqn does not match its OpenMetadata catalog identity")
    return fqn, technical_name, service, database, schema


def _snapshot_table(value: Any, field: str) -> SnapshotTable:
    item = _record(value, field)
    _closed(
        item,
        {
            "id",
            "fqn",
            "name",
            "display_name",
            "technical_name",
            "description",
            "version",
            "updated_at",
            "service",
            "database",
            "schema",
            "owners",
            "domains",
            "tags",
            "glossary_terms",
            "url",
            "matched_by",
            "column_count",
            "loaded_column_count",
            "columns_truncated",
            "schema_loaded",
            "schema_fingerprint",
            "columns",
            "table_constraints",
        },
        field,
    )
    if item.get("schema_loaded") is not True:
        raise QuerySpecificationValidationError(f"{field}.schema_loaded must be true")
    if item.get("columns_truncated") is not False:
        raise QuerySpecificationValidationError(f"{field}.columns_truncated must be false")
    raw_columns = _array(item.get("columns"), f"{field}.columns", MAX_QUERY_COLUMNS_PER_TABLE, allow_empty=False)
    columns = tuple(_snapshot_column(column, f"{field}.columns[{index}]") for index, column in enumerate(raw_columns))
    raw_constraints = _array(item.get("table_constraints") or [], f"{field}.table_constraints", 100)
    table_constraints = tuple(_snapshot_table_constraint(constraint, f"{field}.table_constraints[{index}]") for index, constraint in enumerate(raw_constraints))
    column_ids = [column.id for column in columns]
    if len(column_ids) != len(set(column_ids)):
        raise QuerySpecificationValidationError(f"{field}.columns contains duplicate ids")
    fingerprint = _text(item.get("schema_fingerprint"), f"{field}.schema_fingerprint", 200)
    if not fingerprint.startswith("sha256:"):
        raise QuerySpecificationValidationError(f"{field}.schema_fingerprint must be sha256 provenance")
    fqn, technical_name, service, database, schema = _snapshot_table_identity(item, field)
    return SnapshotTable(
        id=_text(item.get("id"), f"{field}.id", 500),
        fqn=fqn,
        technical_name=technical_name,
        description=str(item.get("description") or "").strip()[:2_000],
        service=service,
        database=database,
        schema=schema,
        version=_number(item.get("version"), f"{field}.version"),
        schema_fingerprint=fingerprint,
        columns=columns,
        table_constraints=table_constraints,
    )


def parse_schema_snapshot(value: Any) -> SchemaSnapshot:
    snapshot = _record(value, "schema_snapshot")
    _closed(
        snapshot,
        {"format", "schema_version", "status", "original_requirements", "source", "requirements", "warnings"},
        "schema_snapshot",
    )
    if snapshot.get("format") != "ragflow-sql-schema-snapshot" or snapshot.get("schema_version") != "1":
        raise QuerySpecificationValidationError("schema_snapshot format or version is unsupported")
    status = _text(snapshot.get("status"), "schema_snapshot.status", 50)
    requirements = _text(snapshot.get("original_requirements"), "schema_snapshot.original_requirements", 20_000)
    source = _record(snapshot.get("source"), "schema_snapshot.source")
    stale = source.get("stale")
    if not isinstance(stale, bool):
        raise QuerySpecificationValidationError("schema_snapshot.source.stale must be a boolean")
    raw_requirements = _array(snapshot.get("requirements"), "schema_snapshot.requirements", MAX_QUERY_TABLES, allow_empty=False)
    tables_by_id: dict[str, SnapshotTable] = {}
    for index, raw_requirement in enumerate(raw_requirements):
        requirement = _record(raw_requirement, f"schema_snapshot.requirements[{index}]")
        selected_table = requirement.get("selected_table")
        if selected_table is not None:
            table = _snapshot_table(selected_table, f"schema_snapshot.requirements[{index}].selected_table")
            existing = tables_by_id.get(table.id)
            if existing is not None and existing != table:
                raise QuerySpecificationValidationError("schema_snapshot contains conflicting versions of one table")
            tables_by_id[table.id] = table
    tables = list(tables_by_id.values())
    if not tables:
        raise QuerySpecificationValidationError("schema_snapshot must contain selected tables")
    fqns = [table.fqn.casefold() for table in tables]
    column_ids = [column.id for table in tables for column in table.columns]
    if len(fqns) != len(set(fqns)) or len(column_ids) != len(set(column_ids)):
        raise QuerySpecificationValidationError("schema_snapshot contains duplicate physical table or column identifiers")
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return SchemaSnapshot(
        status=status,
        original_requirements=requirements,
        stale=stale,
        tables=tuple(tables),
        fingerprint=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    )


def parse_accepted_schema(value: Any) -> tuple[AcceptedSchemaEntry, ...]:
    values = _array(value, "accepted_schema", MAX_QUERY_TABLES, allow_empty=False)
    result: list[AcceptedSchemaEntry] = []
    for index, raw in enumerate(values):
        item = _record(raw, f"accepted_schema[{index}]")
        _closed(item, {"entity_id", "version", "schema_fingerprint"}, f"accepted_schema[{index}]")
        result.append(
            AcceptedSchemaEntry(
                entity_id=_text(item.get("entity_id"), f"accepted_schema[{index}].entity_id", 500),
                version=_number(item.get("version"), f"accepted_schema[{index}].version"),
                schema_fingerprint=_text(item.get("schema_fingerprint"), f"accepted_schema[{index}].schema_fingerprint", 200),
            )
        )
    if len({entry.entity_id for entry in result}) != len(result):
        raise QuerySpecificationValidationError("accepted_schema contains duplicate entity ids")
    return tuple(result)


def parse_parameter_value(value_type: str, value: Any, field: str) -> Any:
    if value_type == "text":
        return _text(value, field, 5_000)
    if value_type == "boolean":
        if not isinstance(value, bool):
            raise QuerySpecificationValidationError(f"{field} must be a boolean")
        return value
    if value_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise QuerySpecificationValidationError(f"{field} must be an integer")
        return value
    if value_type == "decimal":
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise QuerySpecificationValidationError(f"{field} must be a decimal")
        try:
            return str(Decimal(str(value)))
        except InvalidOperation as exc:
            raise QuerySpecificationValidationError(f"{field} must be a decimal") from exc
    if value_type in {"date", "datetime"}:
        text = _text(value, field, 100)
        try:
            date.fromisoformat(text) if value_type == "date" else datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QuerySpecificationValidationError(f"{field} must be an ISO {value_type}") from exc
        return text
    if value_type in {"text_list", "integer_list"}:
        items = _array(value, field, MAX_LIST_PARAMETER_ITEMS, allow_empty=False)
        scalar_type = "text" if value_type == "text_list" else "integer"
        return [parse_parameter_value(scalar_type, item, f"{field}[{index}]") for index, item in enumerate(items)]
    raise QuerySpecificationValidationError(f"{field} has an unsupported type")


def _parameters(value: Any) -> tuple[Parameter, ...]:
    values = _array(value, "specification.parameters", MAX_PARAMETERS)
    result: list[Parameter] = []
    for index, raw in enumerate(values):
        item = _record(raw, f"specification.parameters[{index}]")
        _closed(item, {"name", "type", "value"}, f"specification.parameters[{index}]")
        name = _identifier(item.get("name"), f"specification.parameters[{index}].name")
        value_type = _text(item.get("type"), f"specification.parameters[{index}].type", 50)
        if value_type not in _PARAMETER_TYPES:
            raise QuerySpecificationValidationError(f"specification.parameters[{index}].type is unsupported")
        result.append(
            Parameter(
                name,
                value_type,
                parse_parameter_value(value_type, item.get("value"), f"specification.parameters[{index}].value"),
            )
        )
    if len({parameter.name for parameter in result}) != len(result):
        raise QuerySpecificationValidationError("specification.parameters contains duplicate names")
    return tuple(result)


def _select_items(value: Any) -> tuple[SelectItem, ...]:
    values = _array(value, "specification.select", MAX_SELECT_ITEMS)
    result: list[SelectItem] = []
    for index, raw in enumerate(values):
        item = _record(raw, f"specification.select[{index}]")
        _closed(item, {"id", "kind", "column_id", "alias", "grain"}, f"specification.select[{index}]")
        kind = _text(item.get("kind"), f"specification.select[{index}].kind", 50)
        if kind not in _SELECT_KINDS:
            raise QuerySpecificationValidationError(f"specification.select[{index}].kind is unsupported")
        grain = _optional_text(item.get("grain"), f"specification.select[{index}].grain", 20)
        if (kind == "date_bucket" and grain not in _DATE_GRAINS) or (kind != "date_bucket" and grain is not None):
            raise QuerySpecificationValidationError(f"specification.select[{index}].grain is invalid for {kind}")
        result.append(
            SelectItem(
                id=_text(item.get("id"), f"specification.select[{index}].id", 200),
                kind=kind,
                column_id=_text(item.get("column_id"), f"specification.select[{index}].column_id", 1_000),
                alias=_identifier(item.get("alias"), f"specification.select[{index}].alias"),
                grain=grain,
            )
        )
    if len({item.id for item in result}) != len(result) or len({item.alias.casefold() for item in result}) != len(result):
        raise QuerySpecificationValidationError("specification.select ids and aliases must be unique")
    return tuple(result)


def _decision(value: Any, field: str) -> str | None:
    decision = _optional_text(value, field, 50)
    if decision is not None and decision not in _DECISIONS:
        raise QuerySpecificationValidationError(f"{field} is unsupported")
    return decision


def _joins(value: Any) -> tuple[JoinItem, ...]:
    values = _array(value, "specification.joins", MAX_JOINS)
    result: list[JoinItem] = []
    for index, raw in enumerate(values):
        item = _record(raw, f"specification.joins[{index}]")
        _closed(
            item,
            {"id", "join_type", "entity_id", "alias", "left_column_id", "right_column_id", "description", "decision", "confirmed"},
            f"specification.joins[{index}]",
        )
        join_type = _text(item.get("join_type"), f"specification.joins[{index}].join_type", 20).upper()
        if join_type not in _JOIN_TYPES:
            raise QuerySpecificationValidationError(f"specification.joins[{index}].join_type is unsupported")
        confirmed = item.get("confirmed")
        if not isinstance(confirmed, bool):
            raise QuerySpecificationValidationError(f"specification.joins[{index}].confirmed must be a boolean")
        result.append(
            JoinItem(
                id=_text(item.get("id"), f"specification.joins[{index}].id", 200),
                join_type=join_type,
                entity_id=_text(item.get("entity_id"), f"specification.joins[{index}].entity_id", 500),
                alias=_identifier(item.get("alias"), f"specification.joins[{index}].alias"),
                left_column_id=_text(item.get("left_column_id"), f"specification.joins[{index}].left_column_id", 1_000),
                right_column_id=_text(item.get("right_column_id"), f"specification.joins[{index}].right_column_id", 1_000),
                description=_text(item.get("description"), f"specification.joins[{index}].description", 2_000),
                decision=_decision(item.get("decision"), f"specification.joins[{index}].decision"),
                confirmed=confirmed,
            )
        )
    if len({item.id for item in result}) != len(result):
        raise QuerySpecificationValidationError("specification.joins contains duplicate ids")
    return tuple(result)


def _filters(value: Any) -> tuple[FilterItem, ...]:
    values = _array(value, "specification.filters", MAX_FILTERS)
    result: list[FilterItem] = []
    for index, raw in enumerate(values):
        item = _record(raw, f"specification.filters[{index}]")
        _closed(item, {"id", "column_id", "operator", "parameter", "description", "decision", "confirmed"}, f"specification.filters[{index}]")
        operator = _text(item.get("operator"), f"specification.filters[{index}].operator", 30)
        if operator not in _FILTER_OPERATORS:
            raise QuerySpecificationValidationError(f"specification.filters[{index}].operator is unsupported")
        parameter = _optional_text(item.get("parameter"), f"specification.filters[{index}].parameter", 128)
        if parameter is not None:
            parameter = _identifier(parameter, f"specification.filters[{index}].parameter")
        if (operator in _PARAMETERLESS_OPERATORS) != (parameter is None):
            raise QuerySpecificationValidationError(f"specification.filters[{index}].parameter does not match its operator")
        confirmed = item.get("confirmed")
        if not isinstance(confirmed, bool):
            raise QuerySpecificationValidationError(f"specification.filters[{index}].confirmed must be a boolean")
        result.append(
            FilterItem(
                id=_text(item.get("id"), f"specification.filters[{index}].id", 200),
                column_id=_text(item.get("column_id"), f"specification.filters[{index}].column_id", 1_000),
                operator=operator,
                parameter=parameter,
                description=_text(item.get("description"), f"specification.filters[{index}].description", 2_000),
                decision=_decision(item.get("decision"), f"specification.filters[{index}].decision"),
                confirmed=confirmed,
            )
        )
    if len({item.id for item in result}) != len(result):
        raise QuerySpecificationValidationError("specification.filters contains duplicate ids")
    return tuple(result)


def _order_items(value: Any) -> tuple[OrderItem, ...]:
    values = _array(value, "specification.order_by", MAX_SELECT_ITEMS)
    result: list[OrderItem] = []
    for index, raw in enumerate(values):
        item = _record(raw, f"specification.order_by[{index}]")
        _closed(item, {"select_item_id", "direction"}, f"specification.order_by[{index}]")
        direction = _text(item.get("direction"), f"specification.order_by[{index}].direction", 10).upper()
        if direction not in _ORDER_DIRECTIONS:
            raise QuerySpecificationValidationError(f"specification.order_by[{index}].direction is unsupported")
        result.append(OrderItem(_text(item.get("select_item_id"), f"specification.order_by[{index}].select_item_id", 200), direction))
    if len({item.select_item_id for item in result}) != len(result):
        raise QuerySpecificationValidationError("specification.order_by contains duplicate select items")
    return tuple(result)


def _specification(value: Any) -> QuerySpecification:
    spec = _record(value, "specification")
    _closed(spec, {"dialect", "from", "select", "joins", "filters", "order_by", "parameters", "limit_parameter"}, "specification")
    if spec.get("dialect") != QUERY_DIALECT:
        raise QuerySpecificationValidationError("Only the postgres dialect is supported")
    source = _record(spec.get("from"), "specification.from")
    _closed(source, {"entity_id", "alias"}, "specification.from")
    return QuerySpecification(
        base_entity_id=_text(source.get("entity_id"), "specification.from.entity_id", 500),
        base_alias=_identifier(source.get("alias"), "specification.from.alias"),
        select=_select_items(spec.get("select")),
        joins=_joins(spec.get("joins")),
        filters=_filters(spec.get("filters")),
        order_by=_order_items(spec.get("order_by")),
        parameters=_parameters(spec.get("parameters")),
        limit_parameter=_identifier(spec.get("limit_parameter"), "specification.limit_parameter"),
    )


def parse_compile_query_command(payload: Any) -> CompileQueryCommand:
    request = _record(payload, "request")
    _closed(request, {"schema_version", "schema_snapshot", "accepted_requirements", "accepted_schema", "specification"}, "request")
    if request.get("schema_version") != QUERY_SPECIFICATION_VERSION:
        raise QuerySpecificationValidationError("schema_version is unsupported")
    return CompileQueryCommand(
        snapshot=parse_schema_snapshot(request.get("schema_snapshot")),
        accepted_requirements=_text(request.get("accepted_requirements"), "accepted_requirements", 20_000),
        accepted_schema=parse_accepted_schema(request.get("accepted_schema")),
        specification=_specification(request.get("specification")),
    )


def schema_acceptance_issues(
    snapshot: SchemaSnapshot,
    accepted_requirements: str,
    accepted_schema: Sequence[AcceptedSchemaEntry],
) -> list[BlockingIssue]:
    issues: list[BlockingIssue] = []
    if snapshot.status != "READY":
        issues.append(BlockingIssue("SCHEMA_NOT_READY", "schema_snapshot.status", "Снимок схемы ещё не готов."))
    if snapshot.stale:
        issues.append(BlockingIssue("SCHEMA_STALE", "schema_snapshot.source.stale", "Снимок схемы устарел; выполните сопоставление повторно."))
    if accepted_requirements != snapshot.original_requirements:
        issues.append(BlockingIssue("REQUIREMENTS_CHANGED", "accepted_requirements", "Исходные требования изменились после согласования схемы."))

    current = {table.id: (table.version, table.schema_fingerprint) for table in snapshot.tables}
    accepted = {entry.entity_id: (entry.version, entry.schema_fingerprint) for entry in accepted_schema}
    if current != accepted:
        issues.append(BlockingIssue("SCHEMA_VERSION_CHANGED", "accepted_schema", "Версия или fingerprint согласованной схемы изменились."))
    return issues


def _snapshot_binding_issues(command: CompileQueryCommand) -> list[BlockingIssue]:
    return schema_acceptance_issues(command.snapshot, command.accepted_requirements, command.accepted_schema)


def _bind_tables(snapshot: SchemaSnapshot, spec: QuerySpecification) -> tuple[list[BlockingIssue], dict[str, str], set[str]]:
    issues: list[BlockingIssue] = []
    base = snapshot.table(spec.base_entity_id)
    if base is None:
        issues.append(BlockingIssue("BASE_TABLE_REQUIRED", "specification.from.entity_id", "Выберите базовую таблицу из снимка схемы."))
    aliases: dict[str, str] = {}
    bound_entities: set[str] = set()
    if base is not None:
        aliases[base.id] = spec.base_alias
        bound_entities.add(base.id)
    used_aliases = {spec.base_alias.casefold()}
    for index, join in enumerate(spec.joins):
        path = f"specification.joins[{index}]"
        table = snapshot.table(join.entity_id)
        if table is None:
            raise QuerySpecificationValidationError(f"{path}.entity_id is outside the schema snapshot")
        if join.entity_id in bound_entities:
            raise QuerySpecificationValidationError(f"{path}.entity_id is already bound")
        if join.alias.casefold() in used_aliases:
            raise QuerySpecificationValidationError(f"{path}.alias is duplicate")
        left = snapshot.column(join.left_column_id)
        right = snapshot.column(join.right_column_id)
        if left is None or right is None:
            raise QuerySpecificationValidationError(f"{path} references an unknown column")
        if left[0].id not in bound_entities or right[0].id != join.entity_id:
            raise QuerySpecificationValidationError(f"{path} must join an already bound table to its entity_id")
        if not join.confirmed or join.decision is None:
            issues.append(BlockingIssue("JOIN_DECISION_REQUIRED", path, f"Подтвердите соединение «{join.description}»."))
        aliases[join.entity_id] = join.alias
        used_aliases.add(join.alias.casefold())
        bound_entities.add(join.entity_id)
    return issues, aliases, bound_entities


def _select_binding_issues(snapshot: SchemaSnapshot, spec: QuerySpecification, bound_entities: set[str]) -> list[BlockingIssue]:
    issues: list[BlockingIssue] = []
    if not spec.select:
        issues.append(BlockingIssue("SELECT_REQUIRED", "specification.select", "Добавьте хотя бы одно поле вывода."))
    for index, item in enumerate(spec.select):
        resolved = snapshot.column(item.column_id)
        if resolved is None:
            raise QuerySpecificationValidationError(f"specification.select[{index}].column_id is outside the schema snapshot")
        if resolved[0].id not in bound_entities:
            issues.append(BlockingIssue("TABLE_NOT_JOINED", f"specification.select[{index}]", f"Таблица для поля {item.column_id} не соединена с базовой."))
    return issues


def _filter_binding_issues(
    snapshot: SchemaSnapshot,
    spec: QuerySpecification,
    bound_entities: set[str],
    parameter_by_name: Mapping[str, Parameter],
) -> tuple[list[BlockingIssue], set[str]]:
    issues: list[BlockingIssue] = []
    used_parameters: set[str] = set()
    for index, item in enumerate(spec.filters):
        path = f"specification.filters[{index}]"
        resolved = snapshot.column(item.column_id)
        if resolved is None:
            raise QuerySpecificationValidationError(f"{path}.column_id is outside the schema snapshot")
        if resolved[0].id not in bound_entities:
            issues.append(BlockingIssue("TABLE_NOT_JOINED", path, f"Таблица для фильтра {item.column_id} не соединена с базовой."))
        if not item.confirmed or item.decision is None:
            issues.append(BlockingIssue("FILTER_DECISION_REQUIRED", path, f"Подтвердите условие «{item.description}»."))
        if item.parameter is not None:
            parameter = parameter_by_name.get(item.parameter)
            if parameter is None:
                issues.append(BlockingIssue("FILTER_PARAMETER_REQUIRED", path, f"Укажите параметр {item.parameter}."))
            else:
                used_parameters.add(parameter.name)
                if item.operator in _LIST_OPERATORS and not parameter.value_type.endswith("_list"):
                    raise QuerySpecificationValidationError(f"{path}.parameter must reference a list parameter")
                if item.operator not in _LIST_OPERATORS and parameter.value_type.endswith("_list"):
                    raise QuerySpecificationValidationError(f"{path}.parameter must reference a scalar parameter")
    return issues, used_parameters


def _ordering_and_limit_issues(
    spec: QuerySpecification,
    parameter_by_name: Mapping[str, Parameter],
) -> tuple[list[BlockingIssue], set[str]]:
    issues: list[BlockingIssue] = []
    used_parameters: set[str] = set()
    select_ids = {item.id for item in spec.select}
    if not spec.order_by:
        issues.append(BlockingIssue("ORDER_BY_REQUIRED", "specification.order_by", "Добавьте детерминированную сортировку."))
    for index, item in enumerate(spec.order_by):
        if item.select_item_id not in select_ids:
            raise QuerySpecificationValidationError(f"specification.order_by[{index}] references an unknown select item")
    limit = parameter_by_name.get(spec.limit_parameter)
    if limit is None:
        issues.append(BlockingIssue("LIMIT_REQUIRED", "specification.limit_parameter", "Укажите параметр ограничения строк."))
    elif limit.value_type != "integer" or not 1 <= limit.value <= MAX_QUERY_ROW_LIMIT:
        raise QuerySpecificationValidationError(f"limit parameter must be an integer from 1 to {MAX_QUERY_ROW_LIMIT}")
    else:
        used_parameters.add(limit.name)
    return issues, used_parameters


def _binding_issues(command: CompileQueryCommand) -> tuple[list[BlockingIssue], dict[str, str]]:
    snapshot = command.snapshot
    spec = command.specification
    issues = _snapshot_binding_issues(command)
    table_issues, aliases, bound_entities = _bind_tables(snapshot, spec)
    issues.extend(table_issues)
    issues.extend(_select_binding_issues(snapshot, spec, bound_entities))
    parameter_by_name = {parameter.name: parameter for parameter in spec.parameters}
    filter_issues, filter_parameters = _filter_binding_issues(
        snapshot,
        spec,
        bound_entities,
        parameter_by_name,
    )
    order_issues, limit_parameters = _ordering_and_limit_issues(spec, parameter_by_name)
    issues.extend(filter_issues)
    issues.extend(order_issues)
    used_parameters = filter_parameters | limit_parameters
    unused = sorted(set(parameter_by_name) - used_parameters)
    if unused:
        raise QuerySpecificationValidationError(f"Unused parameters are not allowed: {', '.join(unused)}")
    return issues, aliases


def _column_sql(snapshot: SchemaSnapshot, aliases: Mapping[str, str], column_id: str) -> str:
    resolved = snapshot.column(column_id)
    if resolved is None or resolved[0].id not in aliases:
        raise QuerySpecificationValidationError(f"Column is not bound: {column_id}")
    return f"{aliases[resolved[0].id]}.{resolved[1].name}"


def _select_sql(snapshot: SchemaSnapshot, aliases: Mapping[str, str], item: SelectItem) -> str:
    column = _column_sql(snapshot, aliases, item.column_id)
    if item.kind == "column":
        expression = column
    elif item.kind == "date_bucket":
        expression = f"date_trunc('{item.grain}', {column})::date"
    elif item.kind == "count_distinct":
        expression = f"COUNT(DISTINCT {column})"
    else:
        expression = f"{item.kind.upper()}({column})"
    return f"{expression} AS {item.alias}"


def _filter_sql(snapshot: SchemaSnapshot, aliases: Mapping[str, str], item: FilterItem) -> str:
    column = _column_sql(snapshot, aliases, item.column_id)
    operator = _FILTER_OPERATORS[item.operator]
    if item.operator in _PARAMETERLESS_OPERATORS:
        return f"{column} {operator}"
    if item.operator in _LIST_OPERATORS:
        return f"{column} {operator}(:{item.parameter})"
    return f"{column} {operator} :{item.parameter}"


def _compile_sql(command: CompileQueryCommand, aliases: Mapping[str, str]) -> str:
    snapshot = command.snapshot
    spec = command.specification
    base = snapshot.table(spec.base_entity_id)
    if base is None:
        raise QuerySpecificationValidationError("Base table is not available")
    lines = ["SELECT"]
    rendered_select = [_select_sql(snapshot, aliases, item) for item in spec.select]
    lines.extend(f"    {value}{',' if index < len(rendered_select) - 1 else ''}" for index, value in enumerate(rendered_select))
    lines.append(f"FROM {base.physical_relation} AS {spec.base_alias}")
    for join in spec.joins:
        table = snapshot.table(join.entity_id)
        assert table is not None
        keyword = "JOIN" if join.join_type == "INNER" else "LEFT JOIN"
        lines.extend(
            [
                f"{keyword} {table.physical_relation} AS {join.alias}",
                f"    ON {_column_sql(snapshot, aliases, join.left_column_id)} = {_column_sql(snapshot, aliases, join.right_column_id)}",
            ]
        )
    if spec.filters:
        lines.append("WHERE " + _filter_sql(snapshot, aliases, spec.filters[0]))
        lines.extend(f"  AND {_filter_sql(snapshot, aliases, item)}" for item in spec.filters[1:])
    group_items = [item for item in spec.select if not item.aggregate]
    if group_items and any(item.aggregate for item in spec.select):
        lines.append("GROUP BY")
        rendered_group = [_select_sql(snapshot, aliases, item).rsplit(" AS ", 1)[0] for item in group_items]
        lines.extend(f"    {value}{',' if index < len(rendered_group) - 1 else ''}" for index, value in enumerate(rendered_group))
    select_by_id = {item.id: item for item in spec.select}
    lines.append("ORDER BY " + ", ".join(f"{select_by_id[item.select_item_id].alias} {item.direction}" for item in spec.order_by))
    lines.append(f"LIMIT :{spec.limit_parameter}")
    return "\n".join(lines)


def _parse_guarded_select(sql: str) -> exp.Select:
    if not isinstance(sql, str) or not sql.strip() or ";" in sql or "--" in sql or "/*" in sql:
        raise SqlGuardError("SQL must be one non-empty statement without comments or semicolons")
    try:
        statements = sqlglot.parse(sql, read=QUERY_DIALECT)
    except sqlglot.errors.ParseError as exc:
        raise SqlGuardError("SQL could not be parsed") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise SqlGuardError("Only one SELECT statement is allowed")
    return statements[0]


def _guard_statement_shape(statement: exp.Select) -> None:
    for node in statement.walk():
        if type(node).__name__ in _PROHIBITED_AST_NODES:
            raise SqlGuardError(f"SQL node {type(node).__name__} is prohibited")
    if any(True for _ in statement.find_all(exp.Star)):
        raise SqlGuardError("SELECT * is prohibited")
    if statement.args.get("into") is not None or statement.args.get("locks"):
        raise SqlGuardError("SELECT INTO and row locks are prohibited")
    if statement.args.get("limit") is None:
        raise SqlGuardError("A row limit is required")


def _guard_tables(statement: exp.Select, allowed_tables: Sequence[str]) -> set[str]:
    observed_tables: set[str] = set()
    for table in statement.find_all(exp.Table):
        parts = [part.name for part in table.parts]
        fqn = ".".join(parts)
        if parts and parts[0].casefold() in _PROHIBITED_SCHEMAS:
            raise SqlGuardError("System schemas are prohibited")
        observed_tables.add(fqn.casefold())
    expected_tables = {table.casefold() for table in allowed_tables}
    if not observed_tables or not observed_tables.issubset(expected_tables):
        raise SqlGuardError("SQL references a table outside the accepted schema snapshot")
    return observed_tables


def _guard_functions_and_literals(statement: exp.Select) -> None:
    for function in statement.find_all(exp.Func):
        sql_name = function.sql_name().casefold()
        name = str(getattr(function, "name", "") or sql_name).casefold()
        if name in _PROHIBITED_FUNCTIONS:
            raise SqlGuardError(f"Function {name} is prohibited")
    allowed_literals = _DATE_GRAINS
    for literal in statement.find_all(exp.Literal):
        if not literal.is_string or str(literal.this).casefold() not in allowed_literals:
            raise SqlGuardError("User literals are prohibited; use bound parameters")


def _guard_parameters(statement: exp.Select, parameter_names: Sequence[str]) -> set[str]:
    placeholders = {str(node.this) for node in statement.find_all(exp.Placeholder)}
    if placeholders != set(parameter_names):
        raise SqlGuardError("SQL placeholders do not match the separate parameter object")
    return placeholders


def guard_read_only_sql(
    sql: str,
    *,
    allowed_tables: Sequence[str],
    parameter_names: Sequence[str],
) -> dict[str, Any]:
    """Parse and independently validate one generated read-only statement."""

    statement = _parse_guarded_select(sql)
    _guard_statement_shape(statement)
    observed_tables = _guard_tables(statement, allowed_tables)
    _guard_functions_and_literals(statement)
    placeholders = _guard_parameters(statement, parameter_names)
    return {
        "status": "PASS",
        "dialect": QUERY_DIALECT,
        "statement_count": 1,
        "read_only": True,
        "tables": sorted(observed_tables),
        "parameters": sorted(placeholders),
    }


def compile_query(command: CompileQueryCommand) -> dict[str, Any]:
    issues, aliases = _binding_issues(command)
    response: dict[str, Any] = {
        "schema_version": QUERY_SPECIFICATION_VERSION,
        "status": "NEEDS_CLARIFICATION" if issues else "READY",
        "snapshot_fingerprint": command.snapshot.fingerprint,
        "blocking_issues": [issue.to_api() for issue in issues],
        "sql": None,
        "parameters": {},
        "guard": {"status": "NOT_RUN"},
    }
    if issues:
        return response
    sql = _compile_sql(command, aliases)
    parameter_values = {parameter.name: parameter.value for parameter in command.specification.parameters}
    guard = guard_read_only_sql(
        sql,
        allowed_tables=[table.physical_relation for table in command.snapshot.tables],
        parameter_names=list(parameter_values),
    )
    response.update({"sql": sql, "parameters": parameter_values, "guard": guard})
    return response


def compile_query_payload(payload: Any) -> dict[str, Any]:
    """Parse the untrusted wire payload and compile it when every gate closes."""

    return compile_query(parse_compile_query_command(payload))
