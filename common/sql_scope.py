"""Constrain document-index SQL to one authorized source before execution."""

import uuid

import sqlglot
from sqlglot import exp


def scope_retrieval_sql(sql: str, table_name: str, engine: str, kb_ids: list[str]) -> str:
    """Return a read-only query scoped to its trusted table and knowledge bases."""
    return _scoped_select(sql, table_name, engine, kb_ids).sql(comments=False)


def retrieval_source_sql(sql: str, table_name: str, engine: str, kb_ids: list[str]) -> str:
    """Project bounded source citations from the same authorized predicate."""
    query = _scoped_select(sql, table_name, engine, kb_ids)
    qualifier = next(query.find_all(exp.Table)).alias or None
    columns = ["doc_id", "docnm" if engine == "infinity" else "docnm_kwd"]
    if len(kb_ids) > 1:
        columns.append("kb_id")
    query.set("expressions", [exp.column(column, table=qualifier) for column in columns])
    for key in ("group", "having", "distinct", "order", "limit", "offset", "windows"):
        query.set(key, None)
    return query.limit(20).sql(comments=False)


def _scoped_select(sql: str, table_name: str, engine: str, kb_ids: list[str]) -> exp.Select:
    """Accept a single-table SELECT and impose scope outside its full predicate.

    Retrieval prompts describe a single index. Joins, nested queries, CTEs and
    compound statements are rejected rather than inheriting scope implicitly.
    """
    if not kb_ids or any(not isinstance(kid, str) for kid in kb_ids):
        raise ValueError("SQL retrieval requires an authorized knowledge base")
    for kid in kb_ids:
        uuid.UUID(kid)
    if engine == "infinity" and len(kb_ids) != 1:
        raise ValueError("Infinity SQL retrieval requires exactly one knowledge base")
    try:
        statements = sqlglot.parse(sql)
    except sqlglot.errors.ParseError as exc:
        raise ValueError("Invalid retrieval SQL") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("SQL retrieval requires a single SELECT")
    query = statements[0]
    tables = list(query.find_all(exp.Table))
    if (
        len(list(query.find_all(exp.Select))) != 1
        or len(tables) != 1
        or not isinstance(tables[0].this, exp.Identifier)
        or tables[0].name != table_name
        or tables[0].db
        or tables[0].catalog
        or any(query.args.get(key) for key in ("with_", "joins", "into", "locks", "laterals"))
    ):
        raise ValueError("SQL retrieval must read only the authorized table")
    # SQL-capable backends may expose functions with I/O or side effects.
    functions = {
        "COUNT",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
        "COALESCE",
        "NULLIF",
        "ROUND",
        "ABS",
        "LOWER",
        "UPPER",
        "LENGTH",
        "SUBSTRING",
        "TRIM",
        "CONCAT",
        "JSON_EXTRACT_STRING",
        "JSON_EXTRACT_ISNULL",
        "CAST",
        "AND",
        "OR",
        "NOT",
        "CASE",
        "IF",
    }
    for function in query.find_all(exp.Func):
        name = function.name.upper() if isinstance(function, exp.Anonymous) else function.sql_name()
        if name not in functions:
            raise ValueError("Unsupported retrieval SQL function")
    if engine != "infinity":
        qualifier = tables[0].alias or None
        scope = exp.In(
            this=exp.column("kb_id", table=qualifier),
            expressions=[exp.Literal.string(kid) for kid in kb_ids],
        )
        original = query.args.get("where")
        query.set("where", exp.Where(this=exp.and_(scope, original.this) if original else scope))
    return query
