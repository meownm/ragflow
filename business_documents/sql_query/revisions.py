"""Version-scoped SQL review anchors and factual text revision checks."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

import sqlglot
from sqlglot import exp


def selected_group(sql: str, position: int, value: Any) -> dict[str, Any]:
    """Pin the expression and value of an authorized saved result dimension."""
    expression = sqlglot.parse_one(sql, read="postgres").selects[position]
    if isinstance(expression, exp.Alias):
        expression = expression.this
    return {"expression": expression.sql(dialect="postgres"), "value": value}


def revision_review_changes(before_sql: str, before_parameters: list, proposal: Mapping[str, Any], selection: Mapping[str, Any] | None = None) -> list[str]:
    """Identify changes to accepted meaning that need explicit application.

    This is a comparison with the saved query, not another SQL safety guard.
    Adding dimensions and the pinned selected-group filter stays automatic.
    Other changes to accepted choices are reviewed rather than prohibited.
    """
    try:
        before = sqlglot.parse_one(before_sql, read="postgres")
        after = sqlglot.parse_one(proposal["sql"], read="postgres")
    except sqlglot.errors.ParseError:
        return ["структура SQL"]
    if not isinstance(before, exp.Select) or not isinstance(after, exp.Select):
        return [] if before == after and before_parameters == proposal["parameters"] else ["структура SQL"]
    old_values = {item["name"]: item["value"] for item in before_parameters}
    new_values = {item["name"]: item["value"] for item in proposal["parameters"]}

    def key(expression, values):
        if expression is None:
            return ""
        expression = expression.copy().transform(lambda node: exp.convert(values[node.name]) if isinstance(node, exp.Placeholder) and node.name in values else node)
        expression = expression.transform(lambda node: node.this if isinstance(node, exp.Paren) else node)
        for equality in expression.find_all(exp.EQ):
            if equality.left.sql() > equality.right.sql():
                left, right = equality.left.copy(), equality.right.copy()
                equality.set("this", right)
                equality.set("expression", left)
        return expression.sql(dialect="postgres", normalize=True)

    def terms(expression, values):
        if expression is None:
            return Counter()
        if isinstance(expression, (exp.Where, exp.Having, exp.Paren)):
            return terms(expression.this, values)
        if isinstance(expression, exp.And):
            return terms(expression.left, values) + terms(expression.right, values)
        return Counter([key(expression, values)])

    changes = []
    if any(key(before.args.get(name), old_values) != key(after.args.get(name), new_values) for name in ("from_", "with_", "distinct")) or [
        key(item, old_values) for item in before.args.get("joins") or []
    ] != [key(item, new_values) for item in after.args.get("joins") or []]:
        changes.append("источники или соединения")
    old_filters, new_filters = terms(before.args.get("where"), old_values), terms(after.args.get("where"), new_values)
    added_filters = new_filters - old_filters
    if terms(before.args.get("having"), old_values) != terms(after.args.get("having"), new_values):
        changes.append("отбор сгруппированных строк")
    if selection:
        dimension = sqlglot.parse_one(selection["expression"], read="postgres")
        selection_key = key(dimension, old_values)
        predicate = exp.Is(this=dimension, expression=exp.Null()) if selection["value"] is None else exp.EQ(this=dimension, expression=exp.convert(selection["value"]))
        if key(predicate, {}) not in new_filters:
            changes.append("отбор выбранной группы")
        added_filters.pop(key(predicate, {}), None)
    if old_filters - new_filters or added_filters:
        changes.append("прежний отбор строк")
    old_metrics = Counter(key(item.this if isinstance(item, exp.Alias) else item, old_values) for item in before.selects)
    new_metrics = Counter(key(item.this if isinstance(item, exp.Alias) else item, new_values) for item in after.selects)
    if selection:
        old_metrics.pop(selection_key, None)
    if old_metrics - new_metrics:
        changes.append("принятые показатели")
    old_groups = Counter(key(item, old_values) for item in (before.args.get("group").expressions if before.args.get("group") else []))
    new_groups = Counter(key(item, new_values) for item in (after.args.get("group").expressions if after.args.get("group") else []))
    if selection:
        old_groups.pop(selection_key, None)
    if old_groups - new_groups:
        changes.append("прежняя группировка")

    def ordering(statement, values):
        order = statement.args.get("order")
        if order is None:
            return []
        result = []
        outputs = {item.alias_or_name: item.this if isinstance(item, exp.Alias) else item for item in statement.selects}
        for item in order.expressions:
            item = item.copy()
            expression = item.this
            if isinstance(expression, exp.Literal) and expression.is_int and 1 <= int(expression.this) <= len(statement.selects):
                expression = statement.selects[int(expression.this) - 1]
                expression = expression.this if isinstance(expression, exp.Alias) else expression
            elif isinstance(expression, exp.Column) and not expression.table and expression.name in outputs:
                expression = outputs[expression.name]
            if not selection or key(expression, values) != selection_key:
                item.set("this", expression.copy())
                result.append(key(item, values))
        return result

    order = ordering(before, old_values)
    if ordering(after, new_values)[: len(order)] != order:
        changes.append("прежняя сортировка")
    if key(before.args.get("limit"), old_values) != key(after.args.get("limit"), new_values):
        changes.append("число сохраняемых строк")
    return changes


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
