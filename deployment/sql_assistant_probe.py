"""Run inside an immutable application image; output hashes and schema metadata only."""

import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from api.db import db_models as models

TABLE_NAMES = {"sql_assistant_session", "sql_assistant_command", "sql_assistant_definition", "sql_assistant_definition_version"}


def database_identity():
    name, identifier = models.DB.execute_sql("SELECT current_database(),(pg_control_system()).system_identifier::text").fetchone()
    return {"name": name, "system_identifier": identifier}


class _CanonicalStrings(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            try:
                node.value = node.value.encode("cp1251").decode("utf-8")
            except UnicodeError:
                pass
        return node


def signature():
    database = models.DB
    tables = database.get_tables()
    result = {"tables": {}, "sequences": []}
    with database.atomic():
        database.execute_sql("SET LOCAL TIME ZONE 'UTC'")
        for table in sorted(tables):
            if table in TABLE_NAMES:
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            value = "to_jsonb(t) - 'execution'" if table == "business_document_sql_query_run" else "to_jsonb(t)"
            result["tables"][table] = database.execute_sql(f"SELECT COUNT(*),md5(COALESCE(string_agg(md5(({value})::text),'' ORDER BY md5(({value})::text)),'')) FROM {quoted} AS t").fetchone()
        result["sequences"] = database.execute_sql(
            "SELECT sequencename,start_value,min_value,max_value,increment_by,cycle,cache_size,last_value FROM pg_sequences WHERE schemaname=current_schema() ORDER BY sequencename"
        ).fetchall()
    return result


def contract():
    root = Path("/ragflow")
    paths = set()
    for base in ("api", "admin"):
        paths.update(p for p in (root / base).rglob("*.py") if "models" in p.stem or "migration" in str(p.relative_to(root)))
    paths.update(root / name for name in ("api/db/init_data.py", "api/db/db_utils.py", "api/db/__init__.py"))
    optional = root / "api/db/sql_assistant_schema.py"
    if optional.exists():
        paths.add(optional)
    source = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    semantics = {str(p.relative_to(root)): hashlib.sha256(ast.dump(_CanonicalStrings().visit(ast.parse(p.read_text(encoding="utf-8")))).encode()).hexdigest() for p in sorted(paths)}
    ddl = {}
    for _, model in inspect.getmembers(models, inspect.isclass):
        if model is models.DataBaseModel or not issubclass(model, models.DataBaseModel):
            continue
        table = model._meta.table_name
        sql, params = model._schema._create_table(safe=True).query()
        if table == "business_document_sql_query_run":
            sql = sql.replace(', "execution" TEXT NOT NULL', "")
        ddl[table] = [sql, params]
    return {"schema_sha256": hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(), "files": source, "canonical_ast": semantics, "ddl": ddl}


def exercise():
    from api.db import sql_assistant_schema as owned

    before = signature()
    original_catalog = owned.catalog(models.DB)
    if any(original_catalog[table]["columns"] for table in TABLE_NAMES) or original_catalog["business_document_sql_query_run"]["columns"]:
        raise ValueError("Rehearsal requires a pre-migration backup")
    original = models._migrate_sql_project_execution

    def fail(_):
        models.DB.execute_sql("SELECT 1/0")

    try:
        models._migrate_sql_project_execution = fail
        try:
            owned.migrate(models.DB)
        except Exception:
            pass
        else:
            raise AssertionError("Injected database failure did not fail")
    finally:
        models._migrate_sql_project_execution = original
    assert owned.catalog(models.DB) == original_catalog and signature() == before, "Failed migration must roll back DDL and preserve data"
    # An existing malformed table must be rejected, without creating other objects.
    with models.DB.atomic() as transaction:
        models.DB.execute_sql("CREATE TABLE sql_assistant_command(id text PRIMARY KEY)")
        try:
            owned.migrate(models.DB)
        except Exception:
            pass
        else:
            raise AssertionError("Malformed pre-existing schema was accepted")
        assert not models.DB.table_exists("sql_assistant_session")
        transaction.rollback()
    result = owned.migrate(models.DB)
    assert owned.migrate(models.DB) == result, "Repeated migration changes the schema"
    with models.DB.atomic() as transaction:
        model = models.BusinessDocumentSqlQueryRun
        row = model.create(**metadata_values(model))
        assert model.get_by_id(row.id).execution == {}, "New ORM must read serialized execution metadata"
        model.update(execution={"phase": "SUCCEEDED"}).where(model.id == row.id).execute()
        assert model.get_by_id(row.id).execution == {"phase": "SUCCEEDED"}, "New ORM must round-trip execution metadata"
        transaction.rollback()
    assert signature() == before, "Migration changed existing row multisets or sequences"
    return {**result, "transactional_failure": "PASS", "malformed_schema": "PASS", "repeat": "PASS", "existing_data": "PASS", "new_orm_roundtrip": "PASS", "baseline": before}


def metadata_values(model):
    values = {}
    for name, field in model._meta.fields.items():
        if field.null or field.default is not None:
            continue
        if field.field_type in {"VARCHAR", "TEXT"}:
            values[name] = uuid4().hex
        elif field.field_type in {"INT", "INTEGER", "BIGINT"}:
            values[name] = 1
        else:
            raise ValueError(f"Unsupported old required metadata field: {name}")
    return values


def compatibility():
    model = models.BusinessDocumentSqlQueryRun
    assert "execution" not in model._meta.fields, "This check requires the previous binary"
    with models.DB.atomic() as transaction:
        list(model.select().limit(1))
        row = model.create(**metadata_values(model))
        execution = models.DB.execute_sql("SELECT execution FROM business_document_sql_query_run WHERE id=%s", (row.id,)).fetchone()[0]
        assert json.loads(execution) == {}, "Previous binary inserts must receive the serialized JSON server default"
        transaction.rollback()
    return {"old_read": "PASS", "old_insert": "PASS", "rollback": "PASS"}


def main():
    mode = sys.argv[1]
    if os.environ.get("SQL_ASSISTANT_REHEARSAL") == "1" and models.DB.database != "qa":
        raise ValueError("Rehearsal must use the isolated qa database")
    if mode == "contract":
        result = contract()
    else:
        with models.DB.connection_context():
            if mode == "signature":
                result = signature()
            elif mode == "database":
                result = database_identity()
            elif mode == "exercise":
                result = exercise()
            elif mode == "compatibility":
                result = compatibility()
            elif mode in {"apply", "verify"}:
                from api.db import sql_assistant_schema as owned

                result = owned.migrate(models.DB) if mode == "apply" else owned.verify(models.DB)
                result["database_identity"] = database_identity()
            else:
                raise ValueError("Unknown probe mode")
    print("SQL_ASSISTANT_MAINTENANCE_RESULT=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
