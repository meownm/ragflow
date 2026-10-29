"""Semantic contracts for administrator-approved, structured calculations."""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from business_documents.sql_assistant import fields, fingerprint, text
from business_documents.sql_assistant_query import compile_assistant_query
from business_documents.sql_query.query_specification import parse_schema_snapshot

DEFINITION_CHECKS = ["formula", "grain", "filters", "time", "null_policy", "units", "joins", "parameters"]
QUERY_FIELDS = {"base_entity_id", "aliases", "select", "joins", "filters", "order_by", "row_limit", "row_filter_tree", "aggregate_filters", "aggregate_filter_tree", "top_per_group", "timezone"}
AGGREGATES = {"sum", "avg", "min", "max", "count", "count_distinct"}
COMPILER_VERSION = "sql-assistant-definitions/1"


def dependency_signature(schema):
    snapshot = parse_schema_snapshot(schema["schema_snapshot"])
    return fingerprint(sorted((table.id, table.version, table.schema_fingerprint, table.fqn) for table in snapshot.tables))


def definition_scope(schema):
    snapshot = parse_schema_snapshot(schema["schema_snapshot"])
    return fingerprint(sorted((table.id, table.fqn) for table in snapshot.tables))


def null_policy(kind):
    return "EXCLUDE_NULLS_ZERO_IF_EMPTY" if kind in {"count", "count_distinct"} else "IGNORE_NULLS_NULL_IF_EMPTY"


def _unique(snapshot, column_id):
    table, column = snapshot.column(column_id)
    return column.constraint in {"PRIMARY_KEY", "UNIQUE"} or any(item.constraint_type in {"PRIMARY_KEY", "UNIQUE"} and item.columns == (column.name,) for item in table.table_constraints)


def validate_contract(query, schema, raw):
    if not isinstance(query, dict) or query.keys() - QUERY_FIELDS or query.get("top_per_group") is not None:
        raise ValueError("Согласованный режим поддерживает структурированные поля, агрегации, WHERE/HAVING и сортировку; ручной SQL и Top N по группе используйте в исследовании")
    contract = fields(raw, {"grain", "metrics", "time_column_id", "timezone", "mutable_parameters"})
    snapshot = parse_schema_snapshot(schema["schema_snapshot"])
    grain = [item["id"] for item in query["select"] if item["kind"] not in AGGREGATES]
    metrics = {item["id"]: item for item in query["select"] if item["kind"] in AGGREGATES}
    if contract["grain"] != grain or not metrics:
        raise ValueError("Зерно определения должно совпадать с выбранными измерениями; требуется хотя бы один показатель")
    if not isinstance(contract["metrics"], list) or len(contract["metrics"]) != len(metrics):
        raise ValueError("Укажите единицы и правила NULL для каждого показателя")
    found = set()
    for raw_metric in contract["metrics"]:
        metric = fields(raw_metric, {"select_item_id", "unit", "null_policy", "reaggregation"})
        item = metrics.get(metric["select_item_id"])
        if item is None or item["id"] in found or metric["null_policy"] != null_policy(item["kind"]) or metric["reaggregation"] != "FORBIDDEN":
            raise ValueError("Правила NULL и повторной агрегации не соответствуют формуле")
        text(metric["unit"], "Единица показателя", 100)
        found.add(item["id"])
        column = snapshot.column(item["column_id"])
        if column is None:
            raise ValueError("Поле показателя отсутствует в принятой схеме")
        if item["kind"] in {"sum", "avg"} and not any(kind in column[1].data_type.upper() for kind in ("INT", "NUMERIC", "DECIMAL", "DOUBLE", "REAL", "FLOAT", "MONEY")):
            raise ValueError("Сумма и среднее требуют числовое поле")
    timezone = text(contract["timezone"], "Часовой пояс", 100)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Укажите часовой пояс IANA, например UTC или Europe/Moscow") from error
    if query.get("timezone") != timezone:
        raise ValueError("Часовой пояс расчёта должен совпадать с определением")
    temporal = {item["column_id"] for item in query["filters"] if item["parameter_type"] in {"date", "datetime"}}
    temporal.update(item["column_id"] for item in query["select"] if item["kind"] == "date_bucket")
    if temporal and temporal != {contract["time_column_id"]}:
        raise ValueError("Временное поле расчёта не соответствует определению")
    if contract["time_column_id"] is not None:
        column = snapshot.column(contract["time_column_id"])
        if column is None or not any(kind in column[1].data_type.upper() for kind in ("DATE", "TIMESTAMP")):
            raise ValueError("Выберите временное поле принятой схемы")
    parameters = {item["parameter_name"] for item in query["filters"] if item["operator"] not in {"is_null", "is_not_null"}}
    mutable = contract["mutable_parameters"]
    if not isinstance(mutable, list) or any(not isinstance(name, str) for name in mutable) or len(set(mutable)) != len(mutable) or not set(mutable) <= parameters:
        raise ValueError("Изменяемыми могут быть только параметры исходных фильтров определения")
    for join in query["joins"]:
        if not _unique(snapshot, join["right_column_id"]):
            raise ValueError("Для согласованного JOIN каталог должен подтверждать уникальность правого ключа; иначе возможен повторный учёт фактов")
    return deepcopy(contract)


def query_signature(query, mutable_parameters):
    value = deepcopy(query)
    value["row_limit"] = 0  # Presentation bound, not metric population.
    for item in value.get("filters", []):
        if item["parameter_name"] in mutable_parameters:
            item["parameter_value"] = None
    return fingerprint(value)


def compile_defined_query(definition, requirements, schema, query):
    if definition.get("compiler_version") != COMPILER_VERSION:
        raise ValueError("Изменилась версия семантического компилятора; требуется новая версия определения")
    contract = validate_contract(query, schema, definition["contract"])
    if dependency_signature(schema) != dependency_signature(definition["schema"]):
        raise ValueError("Версии источников не совпадают с определением; требуется новая версия определения")
    mutable = contract["mutable_parameters"]
    if query_signature(query, mutable) != query_signature(definition["query"], mutable):
        raise ValueError("Изменена согласованная формула, зерно, дата, фильтр или JOIN. Для такого изменения выберите исследовательский режим")
    for item in query["filters"]:
        if item["parameter_type"] == "datetime" and item["operator"] not in {"is_null", "is_not_null"}:
            column = parse_schema_snapshot(schema["schema_snapshot"]).column(item["column_id"])[1]
            parsed = datetime.fromisoformat(str(item["parameter_value"]).replace("Z", "+00:00"))
            if "WITH TIME ZONE" in column.data_type.upper() or "TIMESTAMPTZ" in column.data_type.upper():
                if parsed.utcoffset() is None:
                    raise ValueError("Для временного поля с часовым поясом укажите смещение в значении параметра")
            elif parsed.utcoffset() is not None:
                raise ValueError("Поле хранит локальное время без часового пояса; укажите локальное значение без смещения, чтобы PostgreSQL не отбросил его молча")
    return compile_assistant_query(requirements, schema, query)


def definition_payload(requirements, schema, query, contract, title, description):
    # Compiling before persistence excludes unbound columns, unconfirmed JOINs,
    # invalid typed parameters and unsupported expressions from approval.
    contract = fields(contract, {"grain", "metrics", "time_column_id", "timezone", "mutable_parameters"})
    normalized = {**deepcopy(query), "timezone": contract["timezone"]}
    payload = {
        "title": text(title, "Название определения", 200),
        "description": text(description, "Смысл определения", 2000),
        "requirements": deepcopy(requirements),
        "schema": deepcopy(schema),
        "query": normalized,
        "contract": validate_contract(normalized, schema, contract),
        "compiler_version": COMPILER_VERSION,
    }
    _, compiled = compile_defined_query(payload, requirements, schema, normalized)
    if compiled["status"] != "READY":
        raise ValueError("Определение содержит неразрешённые вопросы расчёта")
    return payload
