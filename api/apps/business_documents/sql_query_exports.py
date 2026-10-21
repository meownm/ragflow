"""Authorized downloads of selected SQL versions, retained rows and diagrams."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from api.apps.business_documents.async_runtime import run_in_worker_loop
from api.apps.business_documents.sql_query_access import SqlReadSource, authorize_catalog_sources, authorize_sql_source, authorized_result, read_sql_project
from api.apps.business_documents.sql_query_agents import _stable_hash
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService
from api.db.db_models import BusinessDocumentSqlAgentProposal, BusinessDocumentSqlQueryThread
from business_documents.application.errors import ConflictError, ValidationError
from business_documents.sql_query.exports import SqlExportError, graph_svg, json_chunks, parse_export_command, public_graph, result_json_chunks
from business_documents.sql_query.project_compilation import ProjectCompilationError, build_project_compile_command
from business_documents.sql_query.query_specification import QuerySpecificationValidationError, SqlGuardError, compile_query_payload


@dataclass(frozen=True, slots=True)
class SqlExportArtifact:
    filename: str
    content_type: str
    chunks: Iterable[bytes]


def _artifact_identity(artifact):
    return {"id": artifact.id, "revision": artifact.revision, "accepted_by": artifact.accepted_by, "created_at": artifact.create_time, "content_hash": artifact.content_hash}


def _source_identity(project, source: SqlReadSource):
    return {
        "project_id": project.id,
        "project_title": project.title,
        "project_state_version": project.state_version,
        "requirements": _artifact_identity(source.requirements),
        "schema": _artifact_identity(source.schema),
        "query": _artifact_identity(source.query),
        "compilation": _artifact_identity(source.compilation),
        "snapshot_fingerprint": source.compiled["snapshot_fingerprint"],
        "catalog_entity_ids": list(source.entity_ids),
        "catalog_changed_since_snapshot": source.catalog_changed,
        "is_current_query": source.query.id == project.query_artifact_id,
    }


def _query_data(command, compiled):
    parameters = command["parameters"] if "manual_sql" in command else command["specification"]["parameters"]
    return {
        "sql": compiled["sql"],
        "parameters": [{"name": item["name"], "type": item["type"], "value": compiled["parameters"][item["name"]]} for item in parameters],
        "guard": compiled["guard"],
    }


def _graph(actor_id, command, compiled, entity_ids, view, is_admin, access_role):
    result = run_in_worker_loop(BusinessDocumentSqlQuerySchemaService.graph_source(actor_id, command, compiled, entity_ids, view["depth"], is_admin, access_role))
    return public_graph(result, only_query=view["mode"] == "query")


def _candidate(project, source, proposal_id, actor_id, view, is_admin, access_role):
    proposal = BusinessDocumentSqlAgentProposal.get_or_none(
        (BusinessDocumentSqlAgentProposal.id == proposal_id)
        & (BusinessDocumentSqlAgentProposal.project_id == project.id)
        & (BusinessDocumentSqlAgentProposal.tenant_id == project.tenant_id)
        & (BusinessDocumentSqlAgentProposal.kind == "REVISION")
        & (BusinessDocumentSqlAgentProposal.status == "PENDING")
    )
    if proposal is None or project.operation_state != "REVIEW" or proposal.content_hash != _stable_hash(proposal.payload):
        raise ConflictError("SQL_PROPOSAL_STALE", "The selected proposal is no longer pending")
    result = proposal.payload.get("agent_result") or {}
    candidate, origin = result.get("proposal") or {}, result.get("source") or {}
    thread = BusinessDocumentSqlQueryThread.get_or_none(
        (BusinessDocumentSqlQueryThread.id == origin.get("thread_id")) & (BusinessDocumentSqlQueryThread.project_id == project.id) & (BusinessDocumentSqlQueryThread.tenant_id == project.tenant_id)
    )
    if (
        origin.get("target") != "QUERY"
        or origin.get("base_artifact_id") != source.query.id
        or origin.get("base_requirements_artifact_id") != source.requirements.id
        or origin.get("base_schema_artifact_id") != source.schema.id
        or thread is None
        or thread.status != "OPEN"
        or thread.version != origin.get("thread_version")
        or candidate.get("checks", {}).get("status") != "PASS"
    ):
        raise ConflictError("SQL_PROPOSAL_STALE", "The proposal no longer matches the selected source or discussion")
    schema = origin.get("proposed_schema") if isinstance(origin.get("proposed_schema"), Mapping) else source.schema.payload
    try:
        command = build_project_compile_command(
            source.requirements.payload, schema, {"mode": "manual", "sql": candidate.get("sql"), "parameters": candidate.get("parameters"), "confirmed_alignment": True}
        )
        compiled = compile_query_payload(command)
        if compiled.get("status") != "READY" or compiled.get("guard", {}).get("status") != "PASS":
            raise ValueError("Proposal is not verified")
    except (ProjectCompilationError, QuerySpecificationValidationError, SqlGuardError, ValueError) as error:
        raise ConflictError("SQL_QUERY_BLOCKED", "The selected proposal did not pass current checks") from error
    ids, changed = authorize_catalog_sources(project, actor_id, command, compiled, is_admin=is_admin, access_role=access_role)
    return {
        "label": "Предложение — ещё не принято",
        "proposal_id": proposal.id,
        "catalog_changed_since_snapshot": changed,
        "query": _query_data(command, compiled),
        "graph": _graph(actor_id, command, compiled, ids, view, is_admin, access_role),
    }


class BusinessDocumentSqlExportService:
    @classmethod
    def export(cls, tenant_id, actor_id, project_id, raw, is_admin=False, access_role="AUTHOR_CREATOR") -> SqlExportArtifact:
        try:
            command = parse_export_command(raw)
        except SqlExportError as error:
            raise ValidationError("INVALID_SQL_EXPORT", str(error)) from error
        project = read_sql_project(tenant_id, actor_id, project_id, is_admin, access_role)
        exported_at = datetime.now(timezone.utc).isoformat()
        if command.kind == "RESULT":
            run, source, deadline = authorized_result(project, actor_id, command.run_id, is_admin=is_admin, access_role=access_role)
            header = {
                "format": "ragflow-sql-result-export",
                "schema_version": "1",
                "exported_at": exported_at,
                "source": _source_identity(project, source),
                "query": _query_data(source.command, source.compiled),
                "run": {
                    "id": run.id,
                    "kind": run.kind,
                    "source_run_id": run.source_run_id,
                    "created_at": run.create_time,
                    "expires_at": deadline,
                    "profile_id": run.profile_id,
                    "profile_version": run.profile_version,
                    "profile_name": run.profile_name,
                    "row_count": run.row_count,
                    "duration_ms": run.duration_ms,
                    "completeness": run.checks.get("completeness"),
                },
            }
            try:
                chunks = result_json_chunks(header, run.columns, run.rows, run.checks.get("column_types"))
            except SqlExportError as error:
                raise ConflictError("SQL_RESULT_CHECK_REQUIRED", str(error)) from error
            return SqlExportArtifact(f"sql-result-{run.id}.json", "application/json", chunks)
        source = authorize_sql_source(project, actor_id, command.compilation_id, is_admin=is_admin, access_role=access_role)
        if source.query.id != command.query_artifact_id:
            raise ConflictError("SQL_EXPORT_SOURCE_MISMATCH", "The selected query does not match its compilation")
        packet = {"format": "ragflow-sql-query-export", "schema_version": "1", "exported_at": exported_at, "source": _source_identity(project, source), **_query_data(source.command, source.compiled)}
        filename = f"sql-query-{source.query.id}.json"
        if command.kind == "GRAPH":
            packet = {
                "format": "ragflow-sql-graph-export",
                "schema_version": "1",
                "exported_at": exported_at,
                "source": _source_identity(project, source),
                "query": _query_data(source.command, source.compiled),
                "view": command.view,
                "schema_basis": "Saved schema for query tables; currently visible catalog for related tables",
                "graphs": [{"label": "Выбранная проверенная версия", "graph": _graph(actor_id, source.command, source.compiled, source.entity_ids, command.view, is_admin, access_role)}],
            }
            if command.proposal_id:
                packet["graphs"].append(_candidate(project, source, command.proposal_id, actor_id, command.view, is_admin, access_role))
            filename = f"sql-er-{source.query.id}.{command.format}"
            if command.format == "svg":
                return SqlExportArtifact(filename, "image/svg+xml", (graph_svg(packet),))
        return SqlExportArtifact(filename, "application/json", json_chunks(packet))
