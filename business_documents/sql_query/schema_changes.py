"""Prepare bounded catalog additions without mutating accepted artifacts."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from business_documents.sql_query.query_graph import QueryGraphError, snapshot_tables
from business_documents.sql_query.query_specification import parse_schema_snapshot


def extend_schema(schema: Mapping[str, Any], entities: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result = deepcopy(dict(schema))
    snapshot = result["schema_snapshot"]
    existing = {table["id"] for table in snapshot_tables(schema)}
    for entity in entities:
        if entity["id"] in existing:
            continue
        if len(existing) >= 8 or entity.get("schema_loaded") is not True or not entity.get("schema_fingerprint") or not entity.get("column_details"):
            raise QueryGraphError("A complete current catalog schema is required; at most eight tables are supported")
        fields = {"id", "fqn", "name", "display_name", "technical_name", "description", "version", "updated_at", "service", "database", "schema", "owners", "domains", "tags", "glossary_terms", "url", "matched_by", "column_count", "schema_loaded", "schema_fingerprint", "table_constraints"}
        table = {key: deepcopy(value) for key, value in entity.items() if key in fields}
        column_fields = {"id", "name", "fqn", "data_type", "description", "constraint", "glossary_terms"}
        table.update(columns=[{**{key: value for key, value in column.items() if key in column_fields}, "selected": True} for column in entity["column_details"]], loaded_column_count=len(entity["column_details"]), columns_truncated=entity.get("column_count", 0) > len(entity["column_details"]))
        snapshot["requirements"].append({"term": entity.get("display_name") or entity["technical_name"], "state": "CONFIRMED", "decision": "USER", "selected_table": table})
        result["accepted_schema"].append({"entity_id": entity["id"], "version": entity["version"], "schema_fingerprint": entity["schema_fingerprint"]})
        existing.add(entity["id"])
    parse_schema_snapshot(snapshot)
    return result
