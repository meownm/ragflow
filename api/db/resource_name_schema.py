"""Database guarantees for owner-scoped resource names."""

from peewee import PostgresqlDatabase


def is_resource_name_conflict(error, index: str) -> bool:
    """Recognize only the named constraint, never an unrelated integrity error."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        diagnostic = getattr(error, "diag", None)
        if getattr(error, "pgcode", getattr(error, "sqlstate", None)) == "23505" and getattr(diagnostic, "constraint_name", None) == index:
            return True
        # SQLite reports expression indexes by name in isolated tests.
        if str(error) == f"UNIQUE constraint failed: index '{index}'":
            return True
        error = error.__cause__ or error.__context__
    return False


def install_resource_name_indexes(database):
    """Fail closed on existing collisions without logging private names or IDs."""
    resources = (
        ("dialog", "tenant_id", "name", "status = '1'", "dialog_owner_name_unique"),
        ("user_canvas", "user_id, canvas_category", "title", "1 = 1", "user_canvas_owner_title_unique"),
        ("knowledgebase", "tenant_id", "name", "status = '1'", "knowledgebase_owner_name_unique"),
    )
    with database.atomic():
        if isinstance(database, PostgresqlDatabase):
            database.execute_sql("LOCK TABLE dialog, user_canvas, knowledgebase IN SHARE ROW EXCLUSIVE MODE")
        for table, owner, field, active, index in resources:
            key = f"LOWER(TRIM({field}))"
            count = database.execute_sql(f"SELECT COUNT(*) FROM (SELECT {owner}, {key} FROM {table} WHERE {active} GROUP BY {owner}, {key} HAVING COUNT(*) > 1) AS collisions").fetchone()[0]
            if count:
                raise RuntimeError(f"Resolve {count} duplicate name groups in {table} before migration.")
            database.execute_sql(f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table} ({owner}, {key}) WHERE {active}")
