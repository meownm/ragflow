"""Verify catalog additions before correcting a blocked SQL revision."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from api.apps.business_documents.sql_query_revision_ai import propose_revision, query_revision_checks
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService, OpenMetadataCatalogResolver
from business_documents.adapters.llm import MODEL_TIMEOUT_SECONDS
from business_documents.sql_query.query_graph import QueryGraphError, project_query_graph, related_catalog, snapshot_tables


async def propose_catalog_revision(
    tenant_id: str,
    actor_id: str,
    request: Mapping[str, Any],
    is_admin: bool,
    access_role: str,
    *,
    llm: Any = None,
) -> dict[str, Any]:
    """Keep valid edits cheap; repair a guarded proposal at most once.

    Catalog I/O runs in the worker, before the completion transaction. A model
    never supplies a schema: additions come from complete, authorized live
    entities connected by actual FK metadata to the pinned schema.
    """
    async with asyncio.timeout(MODEL_TIMEOUT_SECONDS):
        first = await propose_revision(tenant_id, request, llm=llm)
        proposal = first.get("proposal") or {}
        if request.get("target") != "QUERY" or proposal.get("checks", {}).get("status") not in {"BLOCKED", "NEEDS_SCHEMA"}:
            return first
        schema = request["schema"]
        tables = snapshot_tables(schema)
        requested = proposal.get("requested_sources", [])
        unknown_source = False
        try:
            project_query_graph(proposal["sql"], tables)
        except QueryGraphError:
            unknown_source = True
        additions = set()
        if unknown_source or requested:
            catalog = OpenMetadataCatalogResolver()
            await catalog.authorize(actor_id=actor_id, is_admin=is_admin)
            await BusinessDocumentSqlQuerySchemaService.verify_snapshot(actor_id, schema["schema_snapshot"], is_admin, access_role)
            visible = await catalog.visible_catalog(actor_id=actor_id)
            try:
                neighborhood = related_catalog(visible["tables"], [table["id"] for table in tables], depth=1)
                graph = project_query_graph(proposal["sql"], neighborhood["tables"])
            except QueryGraphError:
                # Ambiguous, unrelated and invented tables remain unapproved.
                return first
            existing = {table["id"] for table in tables}
            additions = {node["entity_id"] for node in graph["nodes"] if node.get("entity_id") and node["membership"] == "QUERY"} - existing
            for term in requested:
                matches = [
                    table
                    for table in neighborhood["tables"]
                    if term.strip().casefold()
                    in {str(table.get(field) or "").casefold() for field in ("fqn", "technical_name", "name", "display_name")}
                    | {(str(table.get("schema", "")) + "." + str(table.get("technical_name") or table.get("name", ""))).casefold()}
                ]
                if len(matches) != 1:
                    return first
                additions.add(matches[0]["id"])
            additions -= existing
            if additions:
                schema = await BusinessDocumentSqlQuerySchemaService.revision_schema(actor_id, schema, sorted(additions), is_admin, access_role)
        verified_request = {**request, "schema": schema}
        checks = query_revision_checks(verified_request, proposal)
        if additions and checks["status"] == "PASS":
            # Missing metadata does not justify rewriting already valid SQL.
            repaired = {**first, "proposal": {**proposal, "checks": checks}}
        else:
            feedback = {
                "reason": checks.get("reason", "Requested source metadata is verified; produce the complete requested SQL edit"),
                "previous_proposal": {key: proposal[key] for key in ("sql", "parameters", "summary")},
                "verified_additional_entity_ids": sorted(additions),
            }
            repaired = await propose_revision(
                tenant_id,
                verified_request,
                llm=llm,
                validation_feedback=feedback,
            )
        if additions and (repaired.get("proposal") or {}).get("checks", {}).get("status") == "PASS":
            graph = project_query_graph(repaired["proposal"]["sql"], snapshot_tables(schema))
            used = {node["entity_id"] for node in graph["nodes"] if node.get("entity_id") and node["membership"] == "QUERY"}
            unused = set(additions) - used
            if unused == set(additions):
                return repaired
            if unused:
                schema = {
                    **schema,
                    "schema_snapshot": {
                        **schema["schema_snapshot"],
                        "requirements": [item for item in schema["schema_snapshot"]["requirements"] if (item.get("selected_table") or {}).get("id") not in unused],
                    },
                    "accepted_schema": [item for item in schema["accepted_schema"] if item["entity_id"] not in unused],
                }
            # Private worker output, consumed before persistence. Raw model JSON
            # has a closed contract and cannot manufacture this authority.
            repaired["verified_catalog_schema"] = schema
        return repaired
