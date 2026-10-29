"""Bounded, transactional PostgreSQL upgrade for the SQL-assistant foundation."""

from __future__ import annotations

import hashlib
import json

from playhouse.migrate import PostgresqlMigrator

from api.db import db_models as models

RECIPE = "sql-assistant-base-v1"
TABLES = (
    models.SqlAssistantSession,
    models.SqlAssistantCommand,
    models.SqlAssistantDefinition,
    models.SqlAssistantDefinitionVersion,
)


def catalog(database) -> dict:
    """Return schema metadata only; never read or expose application values."""
    result = {}
    for table in [model._meta.table_name for model in TABLES] + ["business_document_sql_query_run"]:
        columns = database.execute_sql(
            "SELECT column_name,udt_name,is_nullable,column_default,character_maximum_length "
            "FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        if table == "business_document_sql_query_run":
            columns = [row for row in columns if row[0] == "execution"]
        indexes = []
        if table != "business_document_sql_query_run" and columns:
            indexes = sorted((tuple(index.columns), index.unique) for index in database.get_indexes(table))
        result[table] = {"columns": columns, "indexes": indexes}
    return result


def schema_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def verify(database) -> dict:
    value = catalog(database)
    types = {
        "BIGINT": "int8",
        "INT": "int4",
        "INTEGER": "int4",
        "VARCHAR": "varchar",
        "TEXT": "text",
        "BOOL": "bool",
        "BOOLEAN": "bool",
        "DATETIME": "timestamp",
        "TIMESTAMP": "timestamp",
        "JSON": "json",
        "JSONB": "jsonb",
    }
    for model in TABLES:
        table = model._meta.table_name
        actual = {row[0]: row for row in value[table]["columns"]}
        expected = {field.column_name: field for field in model._meta.sorted_fields}
        if set(actual) != set(expected):
            raise ValueError(f"Unexpected columns in {table}")
        for name, field in expected.items():
            row = actual[name]
            if row[1] != types[field.field_type] or (row[2] == "YES") != field.null:
                raise ValueError(f"Unexpected type or nullability in {table}.{name}")
            if field.field_type == "VARCHAR" and row[4] != field.max_length:
                raise ValueError(f"Unexpected length in {table}.{name}")
        required = {(tuple(fields), unique) for fields, unique in model._meta.indexes}
        required.update(((field.column_name,), bool(field.unique or field.primary_key)) for field in expected.values() if field.index or field.unique or field.primary_key)
        if not required <= set(value[table]["indexes"]):
            raise ValueError(f"Missing required indexes in {table}")
    execution = value["business_document_sql_query_run"]["columns"]
    if len(execution) != 1 or execution[0][1:3] != ("text", "NO") or execution[0][3] != "'{}'::text":
        raise ValueError("Serialized execution metadata requires TEXT NOT NULL DEFAULT '{}'::text")
    return {"recipe": RECIPE, "objects_sha256": schema_digest(value), "tables": [model._meta.table_name for model in TABLES]}


def migrate(database) -> dict:
    """Apply only the four owned tables and additive execution metadata, atomically."""
    with database.bind_ctx(TABLES, bind_refs=False, bind_backrefs=False), database.atomic():
        database.execute_sql("SET LOCAL lock_timeout='5s'")
        database.execute_sql("SET LOCAL statement_timeout='60s'")
        database.execute_sql("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (RECIPE,))
        for model in TABLES:
            model.create_table(safe=True)
        models._migrate_sql_project_execution(PostgresqlMigrator(database))
        return verify(database)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with models.DB.connection_context():
        result = migrate(models.DB) if args.apply else verify(models.DB)
    print("SQL_ASSISTANT_SCHEMA_RESULT=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
