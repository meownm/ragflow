"""Catalog relationships and scope-aware projections of the current SQL query.

Browsing the catalog never changes a query. A catalog table and each occurrence
of that table in SQL have distinct identities; unknown SQL sources are errors,
not catalog relationships inferred from names.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections import deque
import hashlib
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

MAX_QUERY_GRAPH_INSTANCES = 100
MAX_QUERY_GRAPH_EDGES = 500


class QueryGraphError(ValueError):
    """A query cannot be projected against the supplied catalog evidence."""


def parse_graph_request(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != "1" or set(raw) - {"schema_version", "entity_ids", "sql", "depth"}:
        raise QueryGraphError("Invalid SQL graph request")
    entity_ids, sql, depth = raw.get("entity_ids"), raw.get("sql", ""), raw.get("depth", 1)
    if not isinstance(entity_ids, list) or not 1 <= len(entity_ids) <= 8 or any(not isinstance(value, str) or not 1 <= len(value) <= 500 for value in entity_ids) or len(set(entity_ids)) != len(entity_ids):
        raise QueryGraphError("Select 1 to 8 distinct catalog tables")
    if not isinstance(sql, str) or len(sql) > 60_000:
        raise QueryGraphError("SQL graph input exceeds the supported query size")
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 3:
        raise QueryGraphError("Relationship depth must be from 1 to 3")
    return {"entity_ids": entity_ids, "sql": sql, "depth": depth}


def snapshot_tables(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read only accepted tables from the immutable project schema artifact."""
    accepted = {item["entity_id"] for item in schema.get("accepted_schema", []) if isinstance(item, Mapping) and isinstance(item.get("entity_id"), str)}
    tables = {}
    for item in (schema.get("schema_snapshot") or {}).get("requirements", []):
        table = item.get("selected_table") if isinstance(item, Mapping) else None
        if isinstance(table, Mapping) and table.get("id") in accepted:
            tables[table["id"]] = dict(table)
    return list(tables.values())


def _columns(table: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys: dict[str, list[str]] = {}
    for constraint in table.get("table_constraints") or []:
        kind = constraint.get("constraint_type")
        if kind in {"PRIMARY_KEY", "FOREIGN_KEY", "UNIQUE"}:
            for name in constraint.get("columns") or []:
                keys.setdefault(str(name), []).append(kind)
    return [
        {
            "id": str(column.get("id") or column.get("fqn") or f"{table.get('fqn', table['id'])}.{column['name']}"),
            "name": column["name"],
            "data_type": str(column.get("data_type") or ""),
            "constraint": " · ".join(dict.fromkeys([*([str(column["constraint"])] if column.get("constraint") else []), *keys.get(column["name"], [])])),
            "roles": [],
        }
        for column in table.get("column_details", table.get("columns", []))
        if isinstance(column, Mapping) and isinstance(column.get("name"), str)
    ]


def catalog_relationships(tables: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project FK evidence without inventing cardinality or losing key order."""
    owners = {}
    for table in tables:
        for column in _columns(table):
            owners[column["id"].casefold()] = (str(table["id"]), column["name"])
        for column in table.get("column_details", table.get("columns", [])):
            if isinstance(column, Mapping) and column.get("fqn"):
                owners[str(column["fqn"]).casefold()] = (str(table["id"]), column["name"])
    edges = {}
    for table in tables:
        for constraint in table.get("table_constraints", []):
            if constraint.get("constraint_type") != "FOREIGN_KEY":
                continue
            left = constraint.get("columns", [])
            right = constraint.get("referred_columns", [])
            if not left or len(left) != len(right):
                continue
            targets = [owners.get(str(column).casefold()) for column in right]
            if any(target is None for target in targets) or len({target[0] for target in targets}) != 1:
                continue
            target_id = targets[0][0]
            pairs = [{"from": str(source), "to": target[1]} for source, target in zip(left, targets, strict=True)]
            key = f"{table['id']}:{target_id}:{pairs}"
            edges[key] = {
                "id": "fk:" + hashlib.sha256(key.encode()).hexdigest()[:20],
                "from_entity_id": str(table["id"]),
                "to_entity_id": target_id,
                "kind": "FOREIGN_KEY",
                "source": "OpenMetadata table constraint",
                "cardinality": str(constraint.get("relationship_type") or "UNKNOWN"),
                "column_pairs": pairs,
            }
    return list(edges.values())


def related_catalog(
    tables: Sequence[Mapping[str, Any]], seed_ids: Sequence[str], *, depth: int = 1, max_nodes: int = 100
) -> dict[str, Any]:
    """Return a bounded visible neighborhood; the caller owns catalog access."""
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 3:
        raise QueryGraphError("Relationship depth must be from 1 to 3")
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or not 1 <= max_nodes <= 100 or not 1 <= len(seed_ids) <= 8:
        raise QueryGraphError("Select 1 to 8 source tables and at most 100 graph nodes")
    by_id = {str(table["id"]): table for table in tables}
    if any(entity_id not in by_id for entity_id in seed_ids):
        raise QueryGraphError("A selected table is not available in the visible catalog")
    edges = catalog_relationships(tables)
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from_entity_id"], []).append(edge)
        adjacency.setdefault(edge["to_entity_id"], []).append(edge)
    selected = dict.fromkeys(seed_ids)
    frontier = set(seed_ids)
    truncated = False
    for _ in range(depth):
        following = set()
        for entity_id in sorted(frontier):
            for edge in adjacency.get(entity_id, []):
                other = edge["to_entity_id"] if edge["from_entity_id"] == entity_id else edge["from_entity_id"]
                if other in selected:
                    continue
                if len(selected) >= max_nodes:
                    truncated = True
                    continue
                selected[other] = None
                following.add(other)
        frontier = following
    return {
        "tables": [dict(by_id[entity_id]) for entity_id in selected],
        "relationships": [edge for edge in edges if edge["from_entity_id"] in selected and edge["to_entity_id"] in selected],
        "depth": depth,
        "truncated": truncated,
    }


def relationship_paths(relationships: Sequence[Mapping[str, Any]], source_ids: Sequence[str], *, max_hops: int = 3) -> dict[str, Any]:
    """Offer bounded simple FK routes, keeping parallel key choices distinct."""
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in relationships:
        left, right = edge["from_entity_id"], edge["to_entity_id"]
        adjacency.setdefault(left, []).append((right, edge["id"]))
        adjacency.setdefault(right, []).append((left, edge["id"]))
    roots = set(source_ids)
    queue = deque(([root], []) for root in sorted(roots))
    paths: dict[str, list[dict[str, Any]]] = {}
    examined = 0
    truncated = False
    while queue:
        entities, edges = queue.popleft()
        examined += 1
        if examined > 2000:
            truncated = True
            break
        if len(edges) >= max_hops:
            continue
        for neighbor, edge_id in adjacency.get(entities[-1], []):
            if neighbor in entities:
                continue
            next_entities, next_edges = [*entities, neighbor], [*edges, edge_id]
            if neighbor not in roots:
                options = paths.setdefault(neighbor, [])
                if len(options) < 5:
                    options.append({"entity_ids": next_entities, "relationship_ids": next_edges})
                else:
                    truncated = True
            if len(next_edges) < max_hops:
                queue.append((next_entities, next_edges))
    return {"paths": paths, "paths_truncated": truncated}


def _role(column: exp.Column) -> str:
    parent = column.parent
    while parent is not None:
        if isinstance(parent, exp.Join):
            return "JOIN"
        if isinstance(parent, exp.Where):
            return "WHERE"
        if isinstance(parent, exp.Having):
            return "HAVING"
        if isinstance(parent, exp.Group):
            return "GROUP BY"
        if isinstance(parent, exp.Order):
            return "WINDOW ORDER" if isinstance(parent.parent, exp.Window) else "ORDER BY"
        if isinstance(parent, exp.Window):
            if any(column is item or any(child is column for child in item.find_all(exp.Column)) for item in parent.args.get("partition_by") or []):
                return "PARTITION BY"
        if isinstance(parent, exp.Select):
            return "SELECT"
        parent = parent.parent
    return "SELECT"


def _scope_id(scope: Scope) -> str:
    if scope.parent is None:
        return "root"
    parent_id = _scope_id(scope.parent)
    parent_expression = scope.expression.parent
    if scope.is_cte:
        return f"{parent_id}/cte:{parent_expression.alias_or_name}"
    if scope in scope.parent.union_scopes:
        return f"{parent_id}/union:{scope.parent.union_scopes.index(scope)}"
    if isinstance(parent_expression, exp.Subquery) and parent_expression.alias:
        return f"{parent_id}/derived:{parent_expression.alias}"
    if scope in scope.parent.subquery_scopes:
        return f"{parent_id}/subquery:{scope.parent.subquery_scopes.index(scope)}"
    if scope in scope.parent.derived_table_scopes:
        return f"{parent_id}/derived:{scope.parent.derived_table_scopes.index(scope)}"
    raise QueryGraphError("This SQL table-function scope cannot be represented as a catalog entity")


def project_query_graph(sql: str, tables: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Render actual SQL participation, separate from the accepted schema set."""
    by_id = {str(table["id"]): table for table in tables}
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    used_entities: set[str] = set()
    warnings: list[str] = []
    scopes: list[Scope] = []
    if sql.strip():
        try:
            statements = sqlglot.parse(sql, read="postgres")
            if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
                raise QueryGraphError("Only one read-only query can be projected")
            scopes = list(traverse_scope(normalize_identifiers(statements[0], dialect="postgres")))
        except (sqlglot.errors.ParseError, sqlglot.errors.OptimizeError) as exc:
            raise QueryGraphError("SQL cannot be represented as a query graph") from exc
    scope_nodes: dict[int, dict[str, dict[str, Any]]] = {}
    for scope_index, scope in enumerate(scopes):
        scope_name = _scope_id(scope)
        aliases = {}
        scope_nodes[id(scope)] = aliases
        try:
            sources = scope.selected_sources
        except sqlglot.errors.OptimizeError as exc:
            raise QueryGraphError("SQL source aliases must be unambiguous") from exc
        for alias, (_, source) in sources.items():
            if isinstance(source, exp.Table):
                matches = [
                    table for table in tables
                    if str(table.get("technical_name") or table.get("name", "")) == source.name
                    and (not source.db or str(table.get("schema", "")) == source.db)
                    and (not source.catalog or str(table.get("database", "")) == source.catalog)
                ]
                if len(matches) != 1:
                    raise QueryGraphError("SQL contains a source absent from or ambiguous in the supplied catalog")
                table = matches[0]
                entity_id = str(table["id"])
                used_entities.add(entity_id)
                node = {
                    "id": f"query:{scope_name}:{alias}", "scope": scope_name, "entity_id": entity_id, "alias": alias,
                    "kind": "TABLE", "membership": "QUERY", "label": str(table.get("display_name") or table.get("technical_name") or table.get("name")),
                    "technical_name": str(table.get("technical_name") or table.get("name")),
                    "schema": str(table.get("schema") or ""), "columns": _columns(table),
                }
            else:
                node = {
                    "id": f"query:{scope_name}:{alias}", "scope": scope_name, "entity_id": None, "alias": alias,
                    "kind": "CTE" if source.is_cte else "SUBQUERY",
                    "membership": "QUERY", "label": alias, "technical_name": alias, "schema": "",
                    "columns": [{"id": f"{alias}.{name}", "name": name, "data_type": "", "constraint": "", "roles": []} for name in source.expression.named_selects],
                }
                for origin in scope_nodes.get(id(source), {}).values():
                    edges.append({"id": f"lineage:{origin['id']}:{node['id']}", "from": origin["id"], "to": node["id"], "kind": "LINEAGE", "source": "SQL", "condition": "", "join_type": "", "column_pairs": [], "cardinality": "UNKNOWN"})
            aliases[alias] = node
            nodes.append(node)
            if len(nodes) > MAX_QUERY_GRAPH_INSTANCES:
                raise QueryGraphError("Query graph exceeds 100 SQL source instances; narrow the diagram input")
    for scope_index, scope in enumerate(scopes):
        aliases = scope_nodes[id(scope)]
        for column in scope.columns:
            owner = scope
            candidates = []
            while owner is not None and not candidates:
                available = scope_nodes.get(id(owner), {})
                candidates = [node for alias, node in available.items() if (not column.table or alias == column.table) and any(item["name"] == column.name for item in node["columns"])]
                owner = owner.parent
            if len(candidates) == 1:
                target = next(item for item in candidates[0]["columns"] if item["name"] == column.name)
                role = _role(column)
                if role not in target["roles"]:
                    target["roles"].append(role)
        for join_index, join in enumerate(scope.expression.args.get("joins") or []):
            right = aliases.get(join.this.alias_or_name)
            if right is None:
                continue
            predicate = join.args.get("on")
            participants = {column.table for column in predicate.find_all(exp.Column)} if predicate is not None else set()
            lefts = [node for alias, node in aliases.items() if alias in participants and node is not right]
            if not lefts:
                preceding = list(aliases.values())[:list(aliases.values()).index(right)]
                lefts = preceding[-1:]
            pairs = []
            using = join.args.get("using") or []
            if using:
                for node in [*lefts, right]:
                    for column in node["columns"]:
                        if column["name"] in {item.name for item in using} and "JOIN" not in column["roles"]:
                            column["roles"].append("JOIN")
            if predicate is not None:
                for equality in predicate.find_all(exp.EQ):
                    if isinstance(equality.left, exp.Column) and isinstance(equality.right, exp.Column):
                        pairs.append({"from": equality.left.sql(dialect="postgres"), "to": equality.right.sql(dialect="postgres")})
            for left in lefts:
                edges.append({
                    "id": f"join:{_scope_id(scope)}:{join_index}:{left['id']}", "from": left["id"], "to": right["id"],
                    "kind": "JOIN", "source": "SQL", "condition": predicate.sql(dialect="postgres") if predicate is not None else ("USING (" + ", ".join(item.sql() for item in join.args["using"]) + ")" if join.args.get("using") else ""),
                    "join_type": " ".join(str(join.args.get(key) or "") for key in ("method", "side", "kind")).strip() or ("INNER" if predicate is not None or using else "CROSS"),
                    "column_pairs": pairs, "cardinality": "UNKNOWN",
                })
            if not predicate and not join.args.get("using"):
                warnings.append("Соединение без условия может увеличить количество строк.")
    if len(edges) > MAX_QUERY_GRAPH_EDGES:
        raise QueryGraphError("Query graph exceeds 500 relationships; narrow the diagram input")
    catalog_truncated = False
    for entity_id, table in by_id.items():
        if entity_id not in used_entities:
            if len(nodes) >= MAX_QUERY_GRAPH_INSTANCES:
                catalog_truncated = True
                continue
            nodes.append({
                "id": f"catalog:{entity_id}", "scope": None, "entity_id": entity_id, "alias": "", "kind": "TABLE", "membership": "RELATED",
                "label": str(table.get("display_name") or table.get("technical_name") or table.get("name")),
                "technical_name": str(table.get("technical_name") or table.get("name")), "schema": str(table.get("schema") or ""), "columns": _columns(table),
            })
    entity_nodes: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if node["entity_id"] is not None:
            entity_nodes.setdefault(node["entity_id"], []).append(node)
    for relationship in catalog_relationships(tables):
        for left in entity_nodes.get(relationship["from_entity_id"], []):
            for right in entity_nodes.get(relationship["to_entity_id"], []):
                edges.append({**relationship, "id": f"{relationship['id']}:{left['id']}:{right['id']}", "from": left["id"], "to": right["id"], "condition": "", "join_type": ""})
                if len(edges) > MAX_QUERY_GRAPH_EDGES:
                    raise QueryGraphError("Query graph exceeds 500 relationships; narrow the diagram input")
    if catalog_truncated:
        warnings.append("Показано не более 100 узлов: часть связанных таблиц скрыта. Уточните исходные таблицы или глубину связей.")
    return {"schema_version": "1", "nodes": nodes, "edges": edges, "warnings": list(dict.fromkeys(warnings)), "query_hash": "sha256:" + hashlib.sha256(sql.encode()).hexdigest(), **({"truncated": True} if catalog_truncated else {})}
