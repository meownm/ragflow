"""Coordinate one revision deadline, correction and authorized catalog discovery."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import re
from typing import Any

from api.apps.business_documents.sql_query_revision_ai import InvalidRevisionResponse, propose_revision, query_revision_checks
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService, OpenMetadataCatalogResolver
from business_documents.adapters.llm import MODEL_TIMEOUT_SECONDS
from business_documents.application.errors import BusinessDocumentError
from business_documents.sql_query.query_graph import QueryGraphError, project_query_graph, related_catalog, snapshot_tables
from business_documents.sql_query.revisions import revision_review_changes
from business_documents.sql_query.schema_resolution import CatalogAccessDenied, explicit_table_reference


async def _extend_schema(actor_id, request, proposal, is_admin, access_role):
    """Resolve missing metadata; model output never supplies catalog authority."""
    schema = request["schema"]
    tables = snapshot_tables(schema)
    requested = proposal.get("requested_sources", [])
    try:
        project_query_graph(proposal["sql"], tables)
        unknown_source = False
    except QueryGraphError:
        unknown_source = True
    if not unknown_source and not requested:
        return request, set(), set()
    catalog = OpenMetadataCatalogResolver()
    await catalog.authorize(actor_id=actor_id, is_admin=is_admin)
    await BusinessDocumentSqlQuerySchemaService.verify_snapshot(actor_id, schema["schema_snapshot"], is_admin, access_role)
    visible = await catalog.visible_catalog(actor_id=actor_id)
    mentioned = {name.casefold(): name for name in re.findall(r"[\w-]+(?:\.[\w-]+)+", str(request.get("catalog_source_text") or ""))}
    exact_names = {name for name, original in mentioned.items() if explicit_table_reference(original)}
    if len(exact_names) > 8:
        return None
    explicit = {table["id"]: table for table in visible["tables"] if str(table.get("fqn") or "").casefold() in mentioned.keys() - exact_names}
    for name in sorted(exact_names):
        answer = await catalog.resolve(term=mentioned[name], actor_id=actor_id, locale=request.get("locale") or "ru")
        matches = [entity for entity in answer.candidates if entity.fqn.casefold() == name]
        if len(matches) != 1:
            return None
        entity = matches[0].to_api()
        explicit[entity["id"]] = entity
    current = [table for table in visible["tables"] if str(table.get("fqn") or "").casefold() not in exact_names]
    available = list({table["id"]: table for table in [*tables, *current, *explicit.values()]}.values())
    try:
        neighborhood = related_catalog(available, [table["id"] for table in tables], depth=1)
        neighborhood["tables"] = list({table["id"]: table for table in [*neighborhood["tables"], *explicit.values()]}.values())
        graph = project_query_graph(proposal["sql"], neighborhood["tables"])
    except QueryGraphError:
        return None
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
            return None
        additions.add(matches[0]["id"])
    additions -= existing
    if additions:
        schema = await BusinessDocumentSqlQuerySchemaService.revision_schema(actor_id, schema, sorted(additions), is_admin, access_role)
    return {**request, "schema": schema}, additions, set(explicit)


async def propose_catalog_revision(tenant_id: str, actor_id: str, request: Mapping[str, Any], is_admin: bool, access_role: str, *, llm: Any = None) -> dict[str, Any]:
    """At most two model calls share one deadline, including catalog I/O."""
    try:
        async with asyncio.timeout(MODEL_TIMEOUT_SECONDS):
            if request.get("pinned_compilation_id"):
                await BusinessDocumentSqlQuerySchemaService.verify_snapshot(actor_id, request["schema"]["schema_snapshot"], is_admin, access_role)
            original = request
            feedback = None
            additions, explicit = set(), set()
            for attempt in range(2):
                try:
                    result = await propose_revision(tenant_id, request, llm=llm, validation_feedback=feedback)
                except InvalidRevisionResponse as error:
                    if attempt:
                        raise BusinessDocumentError(
                            "SQL_REVISION_INVALID_RESPONSE",
                            "Помощник не смог подготовить корректный ответ после исправления формата. Ваш ответ и последний успешный результат сохранены. Повторите отправку или уточните запрос.",
                            422,
                        ) from None
                    feedback = {"reason": str(error), "previous_response": error.response}
                    continue
                proposal = result.get("proposal") or {}
                if request.get("target") != "QUERY" or proposal.get("checks", {}).get("status") not in {"BLOCKED", "NEEDS_SCHEMA"}:
                    break
                verified = await _extend_schema(actor_id, request, proposal, is_admin, access_role)
                if verified is None:
                    return result
                request, added, named = verified
                additions |= added
                explicit |= named
                checks = query_revision_checks(request, proposal)
                if additions and checks["status"] == "PASS":
                    proposal["checks"] = checks
                    proposal["review_changes"] = revision_review_changes(original["sql"], original.get("parameters") or [], proposal, original.get("selected_group"))
                    break
                if attempt:
                    break
                feedback = {
                    "reason": checks.get("reason", "Source metadata is verified; produce the complete requested edit"),
                    "previous_proposal": {key: proposal[key] for key in ("sql", "parameters", "summary")},
                    "verified_additional_entity_ids": sorted(additions),
                }
            if additions and (result.get("proposal") or {}).get("checks", {}).get("status") == "PASS":
                schema = request["schema"]
                graph = project_query_graph(result["proposal"]["sql"], snapshot_tables(schema))
                used = {node["entity_id"] for node in graph["nodes"] if node.get("entity_id") and node["membership"] == "QUERY"}
                unused = additions - used
                existing = {table["id"] for table in snapshot_tables(original["schema"])}
                if unused == additions:
                    return result
                if used & explicit and not used & existing:
                    unused |= existing - used
                if unused:
                    schema = {
                        **schema,
                        "schema_snapshot": {
                            **schema["schema_snapshot"],
                            "requirements": [item for item in schema["schema_snapshot"]["requirements"] if (item.get("selected_table") or {}).get("id") not in unused],
                        },
                        "accepted_schema": [item for item in schema["accepted_schema"] if item["entity_id"] not in unused],
                    }
                result["verified_catalog_schema"] = schema
            return result
    except (BusinessDocumentError, CatalogAccessDenied):
        raise
    except Exception as error:
        raise BusinessDocumentError(
            "SQL_REVISION_UNAVAILABLE", "Сервис подготовки SQL временно недоступен. Ваш ответ и последний успешный результат сохранены. Повторите отправку позже.", 503
        ) from error
