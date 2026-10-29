"""Typed research filter trees over the existing catalog-bound SQL compiler."""

from __future__ import annotations

from copy import deepcopy
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlglot
from sqlglot import exp
from sqlglot.dialects.postgres import Postgres

from business_documents.sql_assistant import fields
from business_documents.sql_query.project_compilation import build_project_compile_command
from business_documents.sql_query.query_specification import compile_query_payload, parse_schema_snapshot


class _BoundPostgresGenerator(Postgres.Generator):
    def placeholder_sql(self, expression):
        return f":{expression.name}"


def _check_type(item, data_type):
    if item["operator"] in {"is_null", "is_not_null"}:
        return
    kind = item["parameter_type"]
    data_type = data_type.upper()
    if re.search(r"\b(?:SMALLINT|BIGINT|INTEGER|INT\d*|DECIMAL|NUMERIC|REAL|FLOAT\d*|DOUBLE|SERIAL|BIGSERIAL|SMALLSERIAL|MONEY)\b", data_type):
        allowed = {"integer", "decimal", "integer_list"}
    elif "TIMESTAMP" in data_type or data_type == "DATETIME":
        allowed = {"datetime"} if "WITH TIME ZONE" in data_type or "TIMESTAMPTZ" in data_type else {"date", "datetime"}
    elif data_type == "DATE":
        allowed = {"date"}
    elif "BOOL" in data_type:
        allowed = {"boolean"}
    elif re.search(r"CHAR|TEXT|STRING|UUID", data_type):
        allowed = {"text", "text_list"}
    else:
        raise ValueError("Тип поля пока не поддерживается визуальным фильтром; используйте SQL")
    if kind not in allowed or (item["operator"] in {"like", "ilike"} and allowed != {"text", "text_list"}):
        raise ValueError("Тип значения или оператор не соответствует типу поля")


def _tree(raw, conditions: dict[str, exp.Expression]) -> exp.Expression | None:
    if raw is None:
        return exp.and_(*(item.copy() for item in conditions.values())) if conditions else None
    used: set[str] = set()
    nodes = 0

    def visit(node, depth):
        nonlocal nodes
        nodes += 1
        if depth > 5 or nodes > 100:
            raise ValueError("Дерево условий слишком большое: до 5 уровней и 100 узлов")
        if isinstance(node, dict) and "filter_id" in node:
            leaf = fields(node, {"filter_id"})
            key = leaf["filter_id"]
            if not isinstance(key, str) or key not in conditions or key in used:
                raise ValueError("Условие дерева отсутствует или повторяется")
            used.add(key)
            return conditions[key].copy()
        group = fields(node, {"operator", "children"})
        if not isinstance(group["operator"], str) or group["operator"] not in {"AND", "OR"} or not isinstance(group["children"], list) or not 1 <= len(group["children"]) <= 50:
            raise ValueError("Группа И/ИЛИ должна содержать от 1 до 50 условий")
        children = [visit(child, depth + 1) for child in group["children"]]
        return (exp.and_ if group["operator"] == "AND" else exp.or_)(*children)

    result = visit(raw, 0)
    if used != conditions.keys():
        raise ValueError("Дерево должно включать каждое условие ровно один раз")
    return result


def _leaves(expression):
    # Only the compiler-generated top-level conjunction is flattened. Negated
    # ANY/NULL expressions remain a single condition with their parentheses.
    if isinstance(expression, exp.And):
        return [*_leaves(expression.this), *_leaves(expression.expression)]
    return [expression]


def compile_assistant_query(requirements, schema, query):
    """Compile once through existing bindings, compose trees, then re-run guard.

    Aggregate predicates first use the normal typed filter compiler on their
    physical column. Only that generated column AST is replaced by the selected
    aggregate AST; values never become SQL text and aliases are never predicates.
    """
    if not isinstance(query, dict):
        raise ValueError("Требуется объект расчёта")
    if "timezone" in query:
        try:
            if not isinstance(query["timezone"], str):
                raise ValueError("Invalid timezone")
            ZoneInfo(query["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Укажите часовой пояс IANA, например UTC или Europe/Moscow") from error
    if query.get("mode") == "manual" or not {"row_filter_tree", "aggregate_filters", "aggregate_filter_tree", "timezone"} & query.keys():
        command = build_project_compile_command(requirements, schema, query)
        return command, compile_query_payload(command)
    aggregates = query.get("aggregate_filters", [])
    filters = query.get("filters")
    if not isinstance(aggregates, list) or not isinstance(filters, list) or len(aggregates) + len(filters) > 50:
        raise ValueError("Допустимо до 50 условий расчёта")
    selected = query.get("select")
    if not isinstance(selected, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in selected):
        raise ValueError("Некорректный состав выбранных полей")
    selections = {item["id"]: item for item in selected}
    translated = []
    for raw in aggregates:
        item = fields(raw, {"id", "select_item_id", "operator", "parameter_name", "parameter_type", "parameter_value", "description", "confirmed", "decision"})
        if not isinstance(item["select_item_id"], str):
            raise ValueError("Укажите выбранный агрегат")
        selection = selections.get(item["select_item_id"])
        if not selection or selection.get("kind") not in {"sum", "avg", "min", "max", "count", "count_distinct"}:
            raise ValueError("Условие HAVING должно ссылаться на выбранный агрегат")
        if selection["kind"] in {"sum", "avg", "count", "count_distinct"} and item["operator"] not in {"is_null", "is_not_null"}:
            if item["parameter_type"] not in {"integer", "decimal", "integer_list"} or item["operator"] in {"like", "ilike"}:
                raise ValueError("Числовой агрегат требует числовое условие")
        translated.append({key: value for key, value in item.items() if key != "select_item_id"} | {"column_id": selection["column_id"]})
    combined = [*filters, *translated]
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in combined) or len({item["id"] for item in combined}) != len(combined):
        raise ValueError("Условия должны иметь уникальные идентификаторы")
    command = build_project_compile_command(requirements, schema, {**query, "filters": combined})
    compiled = compile_query_payload(command)
    if compiled["status"] != "READY":
        return command, compiled
    snapshot = parse_schema_snapshot(schema["schema_snapshot"])
    for index, item in enumerate(combined):
        column = snapshot.column(item.get("column_id"))
        if column is None:
            raise ValueError("Поле условия отсутствует в принятой схеме")
        data_type = column[1].data_type
        if index >= len(filters):
            aggregate_kind = selections[aggregates[index - len(filters)]["select_item_id"]]["kind"]
            if aggregate_kind in {"count", "count_distinct"}:
                data_type = "BIGINT"
        _check_type(item, data_type)
    statement = sqlglot.parse_one(compiled["sql"], dialect="postgres")
    source = statement.args["with_"].expressions[0].this if query.get("top_per_group") is not None else statement
    where = source.args.get("where")
    predicates = _leaves(where.this) if where else []
    if len(predicates) != len(combined):
        raise ValueError("Состав условий не соответствует скомпилированному расчёту")
    row_conditions = {item["id"]: predicate for item, predicate in zip(filters, predicates[: len(filters)], strict=True)}
    aggregate_conditions = {}
    for item, predicate in zip(aggregates, predicates[len(filters) :], strict=True):
        selected_index = next(index for index, selection in enumerate(query["select"]) if selection["id"] == item["select_item_id"])
        aggregate = source.selects[selected_index].this
        aggregate_conditions[item["id"]] = predicate.transform(lambda node: aggregate.copy() if isinstance(node, exp.Column) else node)
    row = _tree(query.get("row_filter_tree"), row_conditions)
    having = _tree(query.get("aggregate_filter_tree"), aggregate_conditions)
    source.set("where", exp.Where(this=row) if row is not None else None)
    source.set("having", exp.Having(this=having) if having is not None else None)
    context = {key: deepcopy(value) for key, value in command.items() if key != "specification"}
    parameters = deepcopy(command["specification"]["parameters"])
    if "timezone" in query:
        timezone = query["timezone"]
        timezone_parameter = "__assistant_timezone"
        if any(item["name"] == timezone_parameter for item in parameters):
            raise ValueError("Имя параметра часового пояса зарезервировано компилятором")
        used_timezone = False
        for trunc in source.find_all(exp.TimestampTrunc, exp.DateTrunc):
            column = trunc.this
            if not isinstance(column, exp.Column):
                continue
            selected_column = next((item for table in snapshot.tables for item in table.columns if item.name == column.name and query["aliases"].get(table.id) == column.table), None)
            if selected_column and ("WITH TIME ZONE" in selected_column.data_type.upper() or "TIMESTAMPTZ" in selected_column.data_type.upper()):
                trunc.set("this", exp.AtTimeZone(this=column.copy(), zone=exp.Placeholder(this=timezone_parameter)))
                used_timezone = True
        if used_timezone:
            parameters.append({"name": timezone_parameter, "type": "text", "value": timezone})
    if query.get("top_per_group") is not None:
        parameters.append({"name": "__ragflow_group_limit", "type": "integer", "value": compiled["parameters"]["__ragflow_group_limit"]})
    manual_command = {**context, "manual_sql": _BoundPostgresGenerator().generate(statement), "parameters": parameters}
    return manual_command, compile_query_payload(manual_command)
