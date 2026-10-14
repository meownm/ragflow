"""Version-scoped SQL review anchors and factual text revision checks."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

import sqlglot
from sqlglot import exp


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _title(kind: str, node: exp.Expression) -> str:
    if kind == "FIELD":
        return f"Поле результата «{node.alias_or_name}»" if node.alias_or_name else "Вычисляемое поле результата"
    if kind == "JOIN":
        source = node.this
        name = source.name if isinstance(source, exp.Table) else source.alias_or_name
        return f"Соединение с «{name}»" if name else "Соединение с подзапросом"
    if kind == "CTE":
        return f"Промежуточная выборка «{node.alias_or_name}»"
    if kind == "SUBQUERY":
        return "Вложенная выборка"
    return {"FILTER": "Отбор строк", "GROUP": "Группировка результата", "HAVING": "Отбор сгруппированных строк", "ORDER": "Порядок результата"}[kind]


def query_blocks(sql: str) -> list[dict[str, str]]:
    """Project useful SQL clauses without changing the executable statement."""
    statement = sqlglot.parse_one(sql, read="postgres")
    parts: list[tuple[str, exp.Expression]] = []
    for cte in statement.find_all(exp.CTE):
        parts.append(("CTE", cte))
    for select in statement.find_all(exp.Select):
        parts.extend(("FIELD", item) for item in select.expressions)
        parts.extend(("JOIN", item) for item in select.args.get("joins") or [])
        for key, kind in (("where", "FILTER"), ("group", "GROUP"), ("having", "HAVING"), ("order", "ORDER")):
            item = select.args.get(key)
            if item is not None:
                parts.append((kind, item))
        if select is not statement and not isinstance(select.parent, exp.CTE):
            parts.append(("SUBQUERY", select))
    blocks: list[dict[str, str]] = []
    for index, (kind, node) in enumerate(parts):
        fragment = node.sql(dialect="postgres")
        fingerprint = _digest(f"{kind}:{fragment}")
        blocks.append({"id": f"{kind.lower()}:{index}:{fingerprint}", "kind": kind, "title": _title(kind, node), "sql": fragment, "fingerprint": fingerprint})
    return blocks


def validate_anchor(anchor: Any, *, target: str, sql: str, blocks: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(anchor, Mapping):
        raise ValueError("Anchor must be an object")
    kind = anchor.get("kind")
    if kind == "WHOLE":
        if set(anchor) != {"kind"}:
            raise ValueError("Whole-query anchor has unsupported fields")
        return {"kind": "WHOLE"}
    if target != "QUERY":
        raise ValueError("Document comments must target the whole document")
    if kind == "BLOCK":
        if set(anchor) != {"kind", "block_id"}:
            raise ValueError("Block anchor has unsupported fields")
        block = next((item for item in blocks if item["id"] == anchor.get("block_id")), None)
        if block is None:
            raise ValueError("Selected SQL block is not in the current revision")
        return {"kind": "BLOCK", "block_id": block["id"], "fingerprint": block["fingerprint"], "sql": block["sql"]}
    if kind == "TEXT":
        if set(anchor) != {"kind", "start", "end"}:
            raise ValueError("Text anchor has unsupported fields")
        start, end = anchor.get("start"), anchor.get("end")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(sql):
            raise ValueError("Selected SQL range is invalid")
        return {"kind": "TEXT", "start": start, "end": end, "quote": sql[start:end], "fingerprint": _digest(sql[start:end])}
    raise ValueError("Anchor kind is unsupported")


def locate_anchor(anchor: Mapping[str, Any], *, same_revision: bool, sql: str, blocks: list[dict[str, str]]) -> dict[str, Any]:
    kind = anchor.get("kind")
    if kind == "WHOLE":
        return {"status": "CURRENT", "anchor": {"kind": "WHOLE"}}
    if same_revision:
        return {"status": "CURRENT", "anchor": dict(anchor)}
    if kind == "BLOCK":
        matches = [item for item in blocks if item["fingerprint"] == anchor.get("fingerprint") and item["sql"] == anchor.get("sql")]
        if len(matches) == 1:
            return {"status": "CURRENT", "anchor": {"kind": "BLOCK", "block_id": matches[0]["id"], "fingerprint": matches[0]["fingerprint"], "sql": matches[0]["sql"]}}
    if kind == "TEXT":
        quote = anchor.get("quote")
        if isinstance(quote, str) and quote and sql.count(quote) == 1:
            start = sql.index(quote)
            return {"status": "CURRENT", "anchor": {**anchor, "start": start, "end": start + len(quote)}}
    return {"status": "UNRESOLVED", "anchor": dict(anchor)}


def preserves_document_facts(before: str, after: str) -> bool:
    """A row-free stylistic edit may not introduce or remove numeric claims."""

    def numbers(value: str) -> list[str]:
        return sorted(re.findall(r"(?<!\w)[+-]?\d[\d.,%]*(?!\w)", value))

    return numbers(before) == numbers(after)
