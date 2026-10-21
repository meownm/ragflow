"""Versioned SQL export contracts and lossless, incremental result rendering."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import json
import math
import re
from typing import Any
import xml.etree.ElementTree as ET

from business_documents.sql_query.view_state import validate_view


class SqlExportError(ValueError):
    """The selected export or its stored data is invalid."""


@dataclass(frozen=True, slots=True)
class SqlExportCommand:
    kind: str
    format: str
    compilation_id: str | None = None
    query_artifact_id: str | None = None
    run_id: str | None = None
    proposal_id: str | None = None
    view: dict | None = None


def parse_export_command(raw: object) -> SqlExportCommand:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != "1":
        raise SqlExportError("A versioned export request is required")
    kind, output = raw.get("kind"), raw.get("format")
    fields = {"schema_version", "kind", "format"}
    if kind == "RESULT":
        fields |= {"run_id"}
        required = {"run_id"}
        formats = {"json"}
    elif kind in {"QUERY", "GRAPH"}:
        fields |= {"compilation_id", "query_artifact_id"}
        required = {"compilation_id", "query_artifact_id"}
        formats = {"json"} if kind == "QUERY" else {"json", "svg"}
        if kind == "GRAPH":
            fields |= {"proposal_id", "view"}
    else:
        raise SqlExportError("Choose a query, result or diagram export")
    if set(raw) - fields or not required <= set(raw) or not isinstance(output, str) or output not in formats:
        raise SqlExportError("Invalid export fields or format")
    for name in required | ({"proposal_id"} if raw.get("proposal_id") is not None else set()):
        value = raw.get(name)
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value) is None:
            raise SqlExportError(f"{name} must identify a saved source")
    view = None
    if kind == "GRAPH":
        try:
            view = validate_view(raw.get("view", {"positions": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}, "mode": "all", "depth": 1, "list_view": False}))
        except ValueError as error:
            raise SqlExportError(str(error)) from error
    return SqlExportCommand(kind, output, raw.get("compilation_id"), raw.get("query_artifact_id"), raw.get("run_id"), raw.get("proposal_id"), view)


def json_chunks(payload: Mapping[str, Any]) -> Iterable[bytes]:
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(payload):
        yield chunk.encode("utf-8")
    yield b"\n"


_POSTGRES_TEXT_TYPES = {
    20: "integer",
    21: "integer",
    23: "integer",
    26: "integer",
    1700: "decimal",
    1082: "date",
    1083: "time",
    1114: "timestamp",
    1184: "timestamp_with_timezone",
    1186: "interval",
    1266: "time_with_timezone",
}


def _typed_cell(value: Any, postgres_oid: int | None) -> dict[str, Any]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, Decimal) and value.is_finite():
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, (date, datetime)):
        kind = "timestamp_with_timezone" if isinstance(value, datetime) and value.utcoffset() is not None else "timestamp" if isinstance(value, datetime) else "date"
        return {"type": kind, "value": value.isoformat()}
    if isinstance(value, str):
        return {"type": _POSTGRES_TEXT_TYPES.get(postgres_oid, "string"), "value": value}
    if isinstance(value, float) and math.isfinite(value):
        return {"type": "number", "value": value}
    if isinstance(value, (list, dict)):
        _validate_json_value(value)
        return {"type": "json", "value": value}
    raise SqlExportError("Stored result contains an unsupported value")


def _validate_json_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_json_value(item)
        return
    raise SqlExportError("Stored JSON contains an unsupported value")


def result_json_chunks(header: Mapping[str, Any], columns: Sequence[str], rows: Sequence[list], column_types: Sequence[Mapping] | None = None) -> Iterable[bytes]:
    """Render one row at a time; integer/decimal text is never rounded by JS."""
    types = column_types if column_types is not None else [None] * len(columns)
    if len(types) != len(columns):
        raise SqlExportError("Stored column type metadata does not match the result")
    if column_types is not None and any(
        not isinstance(item, Mapping) or set(item) != {"postgres_oid"} or type(item["postgres_oid"]) is not int or not 0 < item["postgres_oid"] < 2**32 for item in types
    ):
        raise SqlExportError("Stored column type metadata is invalid")
    if not columns or any(not isinstance(column, str) or not column for column in columns):
        raise SqlExportError("Stored columns are invalid")
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise SqlExportError("Stored row does not match its columns")
        for value, kind in zip(row, types, strict=True):
            _typed_cell(value, kind.get("postgres_oid") if kind else None)
    return _result_json_stream(header, columns, rows, types, column_types is not None)


def _result_json_stream(header, columns, rows, types, types_known):
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    prefix = dict(header) | {
        "value_format": "typed-cells-v1",
        "postgres_types_known": types_known,
        "columns": [{"name": name, "postgres_oid": item.get("postgres_oid") if item else None} for name, item in zip(columns, types, strict=True)],
    }
    yield encoder.encode(prefix)[:-1].encode("utf-8") + b',"rows":['
    for index, row in enumerate(rows):
        if len(row) != len(columns):
            raise SqlExportError("Stored row does not match its columns")
        if index:
            yield b","
        values = [_typed_cell(value, kind.get("postgres_oid") if kind else None) for value, kind in zip(row, types, strict=True)]
        for chunk in encoder.iterencode(values):
            yield chunk.encode("utf-8")
    yield b"]}\n"


def public_graph(graph: Mapping[str, Any], *, only_query: bool) -> dict[str, Any]:
    """Export visible graph data, never raw catalog/configuration objects."""
    nodes = [node for node in graph["nodes"] if not only_query or node["membership"] == "QUERY"]
    node_ids = {node["id"] for node in nodes}
    entity_ids = {node.get("entity_id") for node in nodes}
    return {
        "query_hash": graph["query_hash"],
        "nodes": [
            {
                **{key: node[key] for key in ("id", "entity_id", "scope", "alias", "kind", "membership", "label", "technical_name", "schema")},
                "columns": [{key: column[key] for key in ("id", "name", "data_type", "constraint", "roles")} for column in node["columns"]],
            }
            for node in nodes
        ],
        "edges": [
            {
                **{key: edge[key] for key in ("id", "from", "to", "kind", "join_type", "condition", "cardinality", "source")},
                "column_pairs": [{key: pair[key] for key in ("from", "to")} for pair in edge["column_pairs"]],
            }
            for edge in graph["edges"]
            if edge["from"] in node_ids and edge["to"] in node_ids
        ],
        "relationships": [
            {**{key: relation[key] for key in ("id", "from_entity_id", "to_entity_id")}, "column_pairs": [{key: pair[key] for key in ("from", "to")} for pair in relation["column_pairs"]]}
            for relation in graph["relationships"]
            if relation["from_entity_id"] in entity_ids and relation["to_entity_id"] in entity_ids
        ],
        "depth": graph["depth"],
        "truncated": graph["truncated"],
        "paths_truncated": graph["paths_truncated"],
        "freshness": {
            key: value
            for key, value in (graph.get("freshness") or {}).items()
            if key in {"snapshot_at", "latest_entity_updated_at", "checked_at", "catalog_checked_at", "age_hours", "stale", "threshold_hours"}
        },
        "warnings": graph.get("warnings", []),
        "legend": {"QUERY": "В запросе", "RELATED": "Связанная таблица вне запроса", "JOIN": "Соединение SQL", "FOREIGN_KEY": "Внешний ключ каталога", "LINEAGE": "Происхождение данных"},
    }


def graph_svg(packet: Mapping[str, Any]) -> bytes:
    """Create a standalone SVG with safe text, source metadata and relationships."""
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)

    def element(parent, tag, attrs=None, text=None):
        node = ET.SubElement(parent, f"{{{namespace}}}{tag}", attrs or {})
        if text is not None:
            node.text = "".join(
                char if ord(char) in {9, 10, 13} or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF else "\ufffd" for char in str(text)
            )
        return node

    root = ET.Element(f"{{{namespace}}}svg", {"role": "img", "aria-labelledby": "export-title export-description", "font-family": "Arial, sans-serif", "font-size": "13"})
    element(root, "title", {"id": "export-title"}, "ER-диаграмма SQL-запроса")
    element(
        root, "desc", {"id": "export-description"}, "Синие таблицы входят в запрос; серые связаны с ним. Сплошная линия — JOIN, пунктир — внешний ключ. Источник и список связей сохранены в выгрузке."
    )
    element(root, "metadata", text=json.dumps(packet, ensure_ascii=False, separators=(",", ":")))
    defs = element(root, "defs")
    marker = element(defs, "marker", {"id": "arrow", "viewBox": "0 0 10 10", "refX": "9", "refY": "5", "markerWidth": "7", "markerHeight": "7", "orient": "auto-start-reverse"})
    element(marker, "path", {"d": "M 0 0 L 10 5 L 0 10 z", "fill": "#64748b"})
    cursor_y, full_width = 24.0, 1000.0
    source = packet["source"]
    element(
        root,
        "text",
        {"x": "24", "y": "24", "font-size": "17", "font-weight": "bold"},
        f"Проект {source['project_id']} · запрос {source['query']['revision']} · {packet['view']['mode']} · глубина {packet['view']['depth']}",
    )
    element(root, "text", {"x": "24", "y": "48", "fill": "#475569"}, f"Проверка {source['compilation']['id']} · схема {source['schema']['id']} · {packet['exported_at']}")
    cursor_y = 80
    for pane in packet["graphs"]:
        graph = pane["graph"]
        element(root, "text", {"x": "24", "y": str(cursor_y), "font-size": "16", "font-weight": "bold"}, pane["label"])
        element(root, "text", {"x": "24", "y": str(cursor_y + 24), "fill": "#475569"}, "В запросе: синий · вне запроса: серый · JOIN: сплошная линия · FK: пунктир")
        cursor_y += 48
        warnings = [*graph.get("warnings", [])]
        if graph["truncated"] or graph["paths_truncated"]:
            warnings.append("Граф или маршруты ограничены; выгрузка не является полным каталогом.")
        for warning in warnings:
            element(root, "text", {"x": "24", "y": str(cursor_y), "fill": "#92400e"}, warning)
            cursor_y += 20
        positions = {node["id"]: packet["view"]["positions"].get(node["id"], {"x": (index % 3) * 340, "y": (index // 3) * 245}) for index, node in enumerate(graph["nodes"])}
        min_x = min((value["x"] for value in positions.values()), default=0)
        min_y = min((value["y"] for value in positions.values()), default=0)
        placed = {key: (value["x"] - min_x + 24, value["y"] - min_y + cursor_y) for key, value in positions.items()}
        for index, edge in enumerate(graph["edges"]):
            x1, y1 = placed[edge["from"]]
            x2, y2 = placed[edge["to"]]
            offset = (index % 7 - 3) * 7
            if edge["from"] == edge["to"]:
                path = f"M {x1 + 300} {y1 + 40} C {x1 + 350} {y1 - 30}, {x1 + 350} {y1 + 140}, {x1 + 300} {y1 + 110}"
            else:
                start_x, end_x = (x1 + 300, x2) if x2 >= x1 else (x1, x2 + 300)
                middle = (start_x + end_x) / 2 + offset
                path = f"M {start_x} {y1 + 80} C {middle} {y1 + 80}, {middle} {y2 + 80}, {end_x} {y2 + 80}"
            link = element(
                root,
                "path",
                {
                    "d": path,
                    "fill": "none",
                    "stroke": "#2563eb" if edge["kind"] == "JOIN" else "#64748b",
                    "stroke-width": "2",
                    "stroke-dasharray": "none" if edge["kind"] == "JOIN" else "6 4",
                    "marker-end": "url(#arrow)",
                },
            )
            element(link, "title", text=f"{edge['kind']} {edge['join_type']} · {edge['cardinality']} · {edge['condition']}")
        for node in graph["nodes"]:
            x, y = placed[node["id"]]
            active = node["membership"] == "QUERY"
            group = element(root, "g", {"data-membership": node["membership"]})
            element(
                group,
                "rect",
                {"x": str(x), "y": str(y), "width": "300", "height": "220", "rx": "8", "fill": "#ffffff" if active else "#f1f5f9", "stroke": "#2563eb" if active else "#94a3b8", "stroke-width": "2"},
            )
            element(group, "title", text=f"{node['schema']}.{node['technical_name']} · {node['alias']} · {node['membership']}")
            element(group, "text", {"x": str(x + 12), "y": str(y + 24), "fill": "#0f172a" if active else "#526071", "font-weight": "bold"}, node["label"][:38])
            element(group, "text", {"x": str(x + 12), "y": str(y + 44), "fill": "#475569", "font-size": "11"}, f"{node['schema']}.{node['technical_name']} · {node['alias']}"[:48])
            element(group, "text", {"x": str(x + 12), "y": str(y + 64), "fill": "#2563eb" if active else "#526071", "font-size": "11"}, "В запросе" if active else "Связанная таблица вне запроса")
            columns = [column for column in node["columns"] if column["roles"] or "KEY" in column["constraint"]] or node["columns"]
            for number, column in enumerate(columns[:7]):
                item = element(
                    group, "text", {"x": str(x + 12), "y": str(y + 87 + number * 18), "font-size": "11", "fill": "#334155" if active else "#526071"}, f"{column['name']} · {column['data_type']}"[:48]
                )
                element(item, "title", text=f"{column['name']} · {column['data_type']} · {column['constraint']} · {', '.join(column['roles'])}")
            if len(columns) > 7:
                element(group, "text", {"x": str(x + 12), "y": str(y + 211), "font-size": "10", "fill": "#526071"}, f"Ещё полей: {len(columns) - 7}; полный список в metadata/JSON.")
        full_width = max(full_width, max((x for x, _ in placed.values()), default=24) + 380)
        cursor_y = max((y for _, y in placed.values()), default=cursor_y) + 250
        element(root, "text", {"x": "24", "y": str(cursor_y), "font-weight": "bold"}, f"Связи каталога: {len(graph['relationships'])}")
        cursor_y += 22
        for relation in graph["relationships"]:
            text = f"{relation['from_entity_id']} → {relation['to_entity_id']} · " + "; ".join(f"{pair['from']}={pair['to']}" for pair in relation["column_pairs"])
            item = element(root, "text", {"x": "24", "y": str(cursor_y), "font-size": "11"}, text[:140])
            element(item, "title", text=text)
            cursor_y += 18
        cursor_y += 28
    root.set("viewBox", f"0 0 {full_width} {cursor_y}")
    root.set("width", "1200")
    root.set("height", str(max(300, min(10000, round(cursor_y * 1200 / full_width)))))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
