"""Catalog additions are staged independently from accepted schema revisions."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from business_documents.sql_query.query_graph import QueryGraphError, snapshot_tables
from business_documents.sql_query.schema_changes import extend_schema


@pytest.fixture
def schema():
    snapshot = json.loads((Path(__file__).resolve().parents[3] / "playwright/golden/sql-schema-snapshot.v1.json").read_text(encoding="utf-8"))
    selected = [item["selected_table"] for item in snapshot["requirements"] if item.get("selected_table")]
    return {"schema_snapshot": snapshot, "accepted_schema": [{"entity_id": table["id"], "version": table["version"], "schema_fingerprint": table["schema_fingerprint"]} for table in selected]}


def extra_table(schema):
    table = deepcopy(snapshot_tables(schema)[0])
    old_fqn = table["fqn"]
    table.update(id="related", name="related_table", technical_name="related_table", fqn="dwh.related_table")
    table["column_details"] = [{key: value.replace(old_fqn, table["fqn"]) if key in {"id", "fqn"} else value for key, value in column.items() if key != "selected"} for column in table.pop("columns")]
    table.pop("loaded_column_count", None)
    return table


def test_extending_catalog_context_is_immutable_and_keeps_existing_decisions(schema):
    original = deepcopy(schema)
    result = extend_schema(schema, [extra_table(schema)])
    assert schema == original
    assert len(result["accepted_schema"]) == len(original["accepted_schema"]) + 1
    assert result["schema_snapshot"]["requirements"][:-1] == original["schema_snapshot"]["requirements"]
    assert result["schema_snapshot"]["requirements"][-1]["selected_table"]["columns"][0]["selected"] is True


@pytest.mark.parametrize("damage", [{"schema_loaded": False}, {"schema_fingerprint": None}, {"column_details": []}])
def test_incomplete_catalog_evidence_is_not_promoted_to_accepted_schema(schema, damage):
    entity = extra_table(schema)
    entity.update(damage)
    with pytest.raises(QueryGraphError):
        extend_schema(schema, [entity])


def test_repeated_existing_table_does_not_replace_accepted_version(schema):
    table = extra_table(schema)
    table["id"] = snapshot_tables(schema)[0]["id"]
    assert extend_schema(schema, [table]) == schema
