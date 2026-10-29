"""Research presentation and bounded numeric analysis; no infrastructure imports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, Inexact, Context, localcontext
from statistics import median

import sqlglot
from sqlglot import exp


def fingerprint(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def text(value: object, name: str, maximum: int = 20_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name}: требуется непустой текст длиной до {maximum} символов")
    return value.strip()


def fields(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, Mapping) or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("Неверный состав полей команды")
    return dict(value)


def positive_integer(value: object, name: str, *, zero: bool = False) -> int:
    if type(value) is not int or value < (0 if zero else 1):
        raise ValueError(f"{name}: требуется целое положительное число")
    return value


def output_columns(sql: str, names: list[str]) -> list[dict]:
    """Identity follows the expression, never a display label or its position."""
    statement = sqlglot.parse_one(sql, dialect="postgres")
    relations = sorted((table.catalog, table.db, table.name) for table in statement.find_all(exp.Table))
    context = statement.args.get("with_")
    projections = statement.selects
    if len(projections) != len(names):
        raise ValueError("Схема результата не соответствует сохранённому SQL")
    identities = []
    occurrences: dict[str, int] = {}
    for name, expression in zip(names, projections, strict=True):
        expression = expression.this if isinstance(expression, exp.Alias) else expression
        signature = fingerprint(
            [expression.sql(dialect="postgres"), relations, context.sql(dialect="postgres") if context else None, statement.sql(dialect="postgres") if not isinstance(statement, exp.Select) else None]
        )[7:31]
        dimension = expression
        while isinstance(dimension, (exp.Cast, exp.Paren)):
            dimension = dimension.this
        occurrences[signature] = occurrences.get(signature, 0) + 1
        identities.append({"id": f"out_{signature}_{occurrences[signature]}", "name": name, "drillable": isinstance(dimension, (exp.Column, exp.DateTrunc, exp.TimestampTrunc))})
    return identities


def default_view(columns: list[dict]) -> dict:
    return {
        "column_order": [column["id"] for column in columns],
        "hidden": [],
        "labels": {},
        "formats": {},
        "filters": [],
        "sort": None,
        "chart": None,
        "appearance": {"display": "table", "density": "comfortable", "striped": False, "wrap": False},
        "metrics": [],
    }


def restore_view(saved: dict, columns: list[dict]) -> dict:
    """Restore optional settings while preserving the display of older charts."""
    value = {**default_view(columns), **saved}
    if "appearance" not in saved and value["chart"]:
        value["appearance"]["display"] = "both"
    return value


def validate_view(raw: object, columns: list[dict], sql: str | None = None) -> dict:
    value = restore_view(fields(raw, {"column_order", "hidden", "labels", "formats", "filters", "sort", "chart"}, {"appearance", "metrics"}), columns)
    ids = {item["id"] for item in columns}
    for key in ("column_order", "hidden"):
        items = value[key]
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items) or len(items) != len(set(items)) or not set(items) <= ids:
            raise ValueError("Колонки представления не соответствуют результату")
    if set(value["column_order"]) != ids:
        raise ValueError("Порядок должен содержать все колонки результата")
    for key in ("labels", "formats"):
        entries = value[key]
        if not isinstance(entries, Mapping) or not entries.keys() <= ids:
            raise ValueError("Настройки относятся к неизвестной колонке")
        if key == "labels":
            for label in entries.values():
                text(label, "Название колонки", 120)
        elif any(not isinstance(kind, str) or kind not in {"raw", "number", "currency", "percent"} for kind in entries.values()):
            raise ValueError("Неизвестный формат колонки")
    filters = value["filters"]
    if not isinstance(filters, list) or len(filters) > 20:
        raise ValueError("Допустимо не более 20 фильтров результата")
    for item in filters:
        fields(item, {"column_id", "operator", "value"})
        if item["column_id"] not in ids or item["operator"] not in {"eq", "ne", "contains", "gt", "gte", "lt", "lte", "is_null", "is_not_null"}:
            raise ValueError("Неизвестная колонка или операция фильтра")
        if item["value"] is not None and (isinstance(item["value"], (list, dict)) or len(str(item["value"])) > 1000):
            raise ValueError("Значение фильтра слишком велико или имеет неподдерживаемый тип")
    if value["sort"] is not None:
        fields(value["sort"], {"column_id", "direction"})
        if value["sort"]["column_id"] not in ids or value["sort"]["direction"] not in {"ASC", "DESC"}:
            raise ValueError("Неверная сортировка результата")
    if value["chart"] is not None:
        fields(value["chart"], {"kind", "x", "y"})
        if value["chart"]["kind"] not in {"bar", "line"} or value["chart"]["x"] not in ids or value["chart"]["y"] not in ids:
            raise ValueError("Неверные параметры графика")
    appearance = fields(value["appearance"], {"display", "density", "striped", "wrap"})
    if appearance["display"] not in {"table", "chart", "both"} or appearance["density"] not in {"comfortable", "compact"} or any(type(appearance[key]) is not bool for key in ("striped", "wrap")):
        raise ValueError("Неверный внешний вид результата")
    if appearance["display"] != "table" and value["chart"] is None:
        raise ValueError("Выберите оси графика")
    metrics = value["metrics"]
    if not isinstance(metrics, list) or len(metrics) > 8:
        raise ValueError("Допустимо не более восьми показателей")
    metric_ids = set()
    for metric in metrics:
        fields(metric, {"id", "title", "kind", "column_id", "row_index"})
        identity = text(metric["id"], "Код показателя", 64)
        text(metric["title"], "Название показателя", 120)
        if identity in metric_ids or metric["kind"] not in {"value", "rows", "sum", "avg", "min", "max", "count", "distinct"}:
            raise ValueError("Неверный показатель результата")
        metric_ids.add(identity)
        if metric["kind"] != "rows" and metric["column_id"] not in ids:
            raise ValueError("Поле показателя отсутствует в результате")
        if metric["kind"] == "rows" and metric["column_id"] is not None:
            raise ValueError("Число строк не привязывается к колонке")
        if metric["kind"] == "value":
            positive_integer(metric["row_index"], "Индекс строки", zero=True)
        elif metric["row_index"] is not None:
            raise ValueError("Сводный показатель не привязывается к строке")
        if metric["kind"] not in {"value", "rows"} and not metric_aggregation_allowed(sql):
            raise ValueError("Повторная агрегация результата запрещена. Подготовьте новый расчёт SQL.")
    return value


def transfer_view(view: dict, columns: list[dict]) -> tuple[dict, list[str]]:
    view = restore_view(view, columns)
    ids = {column["id"] for column in columns}
    previous = set(view["column_order"])
    changed = sorted(previous - ids)
    result = default_view(columns)
    result["column_order"] = [item for item in view["column_order"] if item in ids] + [item for item in result["column_order"] if item not in previous]
    result["hidden"] = [item for item in view["hidden"] if item in ids]
    result["appearance"] = dict(view.get("appearance", result["appearance"]))
    for field in ("labels", "formats"):
        result[field] = {key: value for key, value in view[field].items() if key in ids}
    result["filters"] = [item for item in view["filters"] if item["column_id"] in ids]
    if view["sort"] and view["sort"]["column_id"] in ids:
        result["sort"] = view["sort"]
    if view["chart"] and {view["chart"]["x"], view["chart"]["y"]} <= ids:
        result["chart"] = view["chart"]
    elif result["appearance"]["display"] != "table":
        result["appearance"]["display"] = "table"
    # Row positions and aggregation eligibility belong to a particular run.
    changed.extend(f"metric:{item['id']}" for item in view.get("metrics", []))
    return result, changed


def metric_aggregation_allowed(sql: str | None) -> bool:
    if not sql:
        return False
    statement = sqlglot.parse_one(sql, dialect="postgres")
    return isinstance(statement, exp.Select) and not any(statement.find_all(exp.AggFunc, exp.Window, exp.Group, exp.Distinct, exp.Union, exp.Intersect, exp.Except))


def result_metrics(rows, columns, view, sql, *, presented=None):
    """Summarize only the saved, filtered rows. SQL aggregates cannot be reaggregated."""
    if not view.get("metrics"):
        return []
    if presented is None:
        presented = present_rows(rows, columns, view)
    positions = {column["id"]: index for index, column in enumerate(columns)}
    eligible = metric_aggregation_allowed(sql)
    result = []
    for metric in view.get("metrics", []):
        kind = metric["kind"]
        item = {"id": metric["id"], "title": metric["title"], "kind": kind, "value": None, "status": "OK", "rounded": False}
        if kind == "rows":
            item["value"] = str(len(presented))
        elif kind == "value":
            source = next((row for index, row in presented if index == metric["row_index"]), None)
            if source is None:
                item["status"] = "FILTERED_OUT"
            else:
                item["value"] = source[positions[metric["column_id"]]]
                item["status"] = "NULL" if item["value"] is None else "OK"
        elif not eligible:
            item["status"] = "REQUIRES_QUERY"
        else:
            values = [row[positions[metric["column_id"]]] for _, row in presented if row[positions[metric["column_id"]]] is not None]
            if kind == "count":
                item["value"] = str(len(values))
            elif kind == "distinct":
                item["value"] = str(len({fingerprint(cell) for cell in values}))
            elif not values:
                item["status"] = "NULL"
            else:
                numeric = [number(cell) for cell in values]
                if any(cell is None for cell in numeric):
                    item["status"] = "NON_NUMERIC"
                elif any(len(cell.as_tuple().digits) + abs(cell.as_tuple().exponent) > 2048 for cell in numeric):
                    item["status"] = "OUT_OF_RANGE"
                else:
                    span = max(cell.adjusted() for cell in numeric) - min(cell.as_tuple().exponent for cell in numeric) + 1
                    precision = max(50, span + len(str(len(numeric))) + 8)
                    with localcontext(Context(prec=precision)) as context:
                        value = sum(numeric, Decimal(0)) if kind in {"sum", "avg"} else min(numeric) if kind == "min" else max(numeric)
                        if kind == "avg":
                            value /= len(numeric)
                        item["value"] = str(value)
                        item["rounded"] = context.flags[Inexact]
        result.append(item)
    return result


def number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _matches(cell, operator, expected):
    if operator == "is_null":
        return cell is None
    if operator == "is_not_null":
        return cell is not None
    if cell is None or expected is None:
        return False
    if operator == "contains":
        return str(expected).casefold() in str(cell).casefold()
    left, right = number(cell), number(expected)
    if left is None or right is None:
        left, right = str(cell), str(expected)
    return {"eq": lambda: left == right, "ne": lambda: left != right, "gt": lambda: left > right, "gte": lambda: left >= right, "lt": lambda: left < right, "lte": lambda: left <= right}[operator]()


def present_rows(rows: list[list], columns: list[dict], view: dict) -> list[tuple[int, list]]:
    positions = {column["id"]: index for index, column in enumerate(columns)}
    result = [(index, row) for index, row in enumerate(rows) if all(_matches(row[positions[item["column_id"]]], item["operator"], item["value"]) for item in view["filters"])]
    if view["sort"]:
        position = positions[view["sort"]["column_id"]]
        nulls = [item for item in result if item[1][position] is None]
        result = [item for item in result if item[1][position] is not None]

        def sort_key(item):
            cell = item[1][position]
            numeric = number(cell)
            return (0, numeric) if numeric is not None else (1, str(cell).casefold())

        result.sort(key=sort_key, reverse=view["sort"]["direction"] == "DESC")
        result.extend(nulls)
    return result


def compare_values(rows: list[list], first: int, second: int) -> list[dict]:
    result = []
    for index, row in enumerate(rows):
        a, b = number(row[first]), number(row[second])
        difference = a - b if a is not None and b is not None else None
        percent = difference / abs(b) * 100 if difference is not None and b else None
        result.append(
            {
                "row_index": index,
                "current": str(a) if a is not None else None,
                "baseline": str(b) if b is not None else None,
                "delta": str(difference) if difference is not None else None,
                "percent": str(percent) if percent is not None else None,
                "status": "MISSING" if difference is None else "ZERO_BASELINE" if not b else "OK",
            }
        )
    return result


def analyze_series(rows: list[list], position: int) -> dict:
    values = [(index, number(row[position])) for index, row in enumerate(rows)]
    numeric = [value for _, value in values if value is not None]
    if len(numeric) < 5:
        raise ValueError("Для оценки отклонений нужно не менее пяти числовых наблюдений")
    center = median(numeric)
    deviation = median([abs(value - center) for value in numeric])
    threshold = Decimal("3.5")
    observations = []
    for index, value in values:
        if value is None:
            continue
        score = Decimal("0.6745") * abs(value - center) / deviation if deviation else None
        if (score is not None and score > threshold) or (not deviation and value != center):
            observations.append({"row_index": index, "value": str(value), "score": str(score) if score is not None else None})
    return {
        "method": "median_mad",
        "method_version": "1",
        "median": str(center),
        "mad": str(deviation),
        "threshold": str(threshold),
        "sample_count": len(numeric),
        "missing_count": len(values) - len(numeric),
        "observations": observations,
        "limitations": [
            "Несезонная оценка по выбранной колонке; порядок и регулярность ряда не подтверждены.",
            "Отклонение не доказывает причину изменения.",
            "Полнота источника неизвестна.",
            *(["MAD равен нулю; отмечены значения, отличающиеся от медианы, без статистической оценки."] if not deviation else []),
        ],
    }
