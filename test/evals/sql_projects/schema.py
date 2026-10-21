"""Synthetic catalog derived from the same frozen DDL used by PostgreSQL."""

from hashlib import sha256
from pathlib import Path

import sqlglot
from sqlglot import exp

from business_documents.sql_query.query_specification import parse_schema_snapshot

REQUIREMENT = "Использовать согласованные условия пользователя и подтверждённую схему."


def accepted_requirements():
    return {"requirements": [{"statement": REQUIREMENT}]}


def qualification_schema():
    """Column ownership for independent PostgreSQL ORDER BY resolution."""
    return {"public": {item["selected_table"]["technical_name"]: {
        column["name"]: column["data_type"] for column in item["selected_table"]["columns"]
    } for item in accepted_schema()["schema_snapshot"]["requirements"]}}


def accepted_schema():
    ddl = (Path(__file__).parent / "fixtures/schema.sql").read_text(encoding="utf-8")
    tables = []
    for statement in sqlglot.parse(ddl, read="postgres"):
        name = statement.this.this.name
        columns, constraints = [], []
        for column in statement.this.expressions:
            if not isinstance(column, exp.ColumnDef):
                continue
            kinds = [constraint.args["kind"] for constraint in column.args.get("constraints") or []]
            primary = any(isinstance(kind, exp.PrimaryKeyColumnConstraint) for kind in kinds)
            foreign = [kind for kind in kinds if isinstance(kind, exp.Reference)]
            columns.append({"id": f"synthetic.qa.public.{name}.{column.name}", "fqn": f"synthetic.qa.public.{name}.{column.name}", "name": column.name, "data_type": column.args["kind"].sql(dialect="postgres"), "constraint": "PRIMARY_KEY" if primary else "FOREIGN_KEY" if foreign else "", "selected": True})
            if primary:
                constraints.append({"constraint_type": "PRIMARY_KEY", "columns": [column.name], "referred_columns": []})
            for reference in foreign:
                target = reference.this.this.name
                constraints.append({"constraint_type": "FOREIGN_KEY", "columns": [column.name], "referred_columns": [f"synthetic.qa.public.{target}.{field.name}" for field in reference.this.expressions]})
        tables.append({"id": name, "fqn": f"synthetic.qa.public.{name}", "technical_name": name, "service": "synthetic", "database": "qa", "schema": "public", "version": 1,
                       "schema_loaded": True, "columns_truncated": False, "column_count": len(columns), "loaded_column_count": len(columns), "columns": columns, "table_constraints": constraints,
                       "schema_fingerprint": "sha256:" + sha256(statement.sql(dialect="postgres").encode()).hexdigest()})
    snapshot = {"format": "ragflow-sql-schema-snapshot", "schema_version": "1", "status": "READY", "original_requirements": "- " + REQUIREMENT, "source": {"type": "openmetadata", "stale": False}, "requirements": [{"selected_table": table} for table in tables]}
    parse_schema_snapshot(snapshot)
    return {"accepted_schema": [{"entity_id": table["id"], "version": table["version"], "schema_fingerprint": table["schema_fingerprint"]} for table in tables], "schema_snapshot": snapshot}
