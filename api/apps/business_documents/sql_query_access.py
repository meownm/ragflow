"""Read authorization for immutable SQL sources, independent of current edits."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from business_documents.async_runtime import run_in_worker_loop
from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService, _stable_hash
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService
from api.apps.business_documents.sql_query_retention import expire_project_results
from business_documents.sql_query.result_retention import result_expires_at
from api.db.db_models import BusinessDocumentSqlQueryArtifact, BusinessDocumentSqlQueryProject, BusinessDocumentSqlQueryRun
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError
from business_documents.sql_query.project_compilation import ProjectCompilationError, build_project_compile_command
from business_documents.sql_query.query_specification import QuerySpecificationValidationError, SqlGuardError, parse_parameter_value, parse_schema_snapshot


@dataclass(frozen=True, slots=True)
class SqlReadSource:
    compilation: BusinessDocumentSqlQueryArtifact
    requirements: BusinessDocumentSqlQueryArtifact
    schema: BusinessDocumentSqlQueryArtifact
    query: BusinessDocumentSqlQueryArtifact
    command: dict[str, Any]
    compiled: dict[str, Any]
    entity_ids: tuple[str, ...]
    catalog_changed: bool


def authorize_sql_source(
    project: BusinessDocumentSqlQueryProject,
    actor_id: str,
    compilation_id: str,
    *,
    is_admin: bool = False,
    access_role: str = "AUTHOR_CREATOR",
    extra_entity_ids: tuple[str, ...] = (),
    include_schema_tables: bool = False,
) -> SqlReadSource:
    BusinessDocumentAccess(actor_id, access_role, is_admin).require_view_sql(project.owner_id)
    compilation = BusinessDocumentSqlQueryArtifact.get_or_none(
        (BusinessDocumentSqlQueryArtifact.id == compilation_id)
        & (BusinessDocumentSqlQueryArtifact.project_id == project.id)
        & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id)
        & (BusinessDocumentSqlQueryArtifact.kind == "COMPILATION")
    )
    if compilation is None:
        raise ConflictError("SQL_RESULT_SOURCE_UNAVAILABLE", "The saved source of this SQL version is unavailable")
    ids = compilation.payload.get("source_artifact_ids") if isinstance(compilation.payload, Mapping) else None
    if not isinstance(ids, list) or len(ids) != 3 or any(not isinstance(value, str) or not value for value in ids):
        raise ConflictError("SQL_RESULT_SOURCE_UNAVAILABLE", "The saved source chain is incomplete")
    artifacts = []
    for artifact_id, kind in zip(ids, ("REQUIREMENTS", "SCHEMA", "QUERY"), strict=True):
        artifact = BusinessDocumentSqlQueryArtifact.get_or_none(
            (BusinessDocumentSqlQueryArtifact.id == artifact_id)
            & (BusinessDocumentSqlQueryArtifact.project_id == project.id)
            & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id)
            & (BusinessDocumentSqlQueryArtifact.kind == kind)
        )
        if artifact is None:
            raise ConflictError("SQL_RESULT_SOURCE_UNAVAILABLE", "The saved source chain is unavailable")
        artifacts.append(artifact)
    requirements, schema, query = artifacts
    try:
        if any(not isinstance(artifact.payload, Mapping) for artifact in artifacts):
            raise ValueError("Stored source is not an object")
        if any(artifact.content_hash != _stable_hash(artifact.payload) for artifact in (compilation, *artifacts)):
            raise ValueError("Stored source content changed")
        command = build_project_compile_command(requirements.payload, schema.payload, query.payload)
        compiled = compilation.payload.get("result")
        guard = compiled.get("guard") if isinstance(compiled, Mapping) else None
        if (
            not isinstance(compiled, Mapping)
            or compiled.get("status") != "READY"
            or not isinstance(guard, Mapping)
            or guard.get("status") != "PASS"
            or not isinstance(compiled.get("sql"), str)
            or not compiled["sql"]
            or not isinstance(compiled.get("snapshot_fingerprint"), str)
            or not isinstance(guard.get("tables"), list)
            or not guard["tables"]
            or any(not isinstance(name, str) or not name for name in guard["tables"])
        ):
            raise ValueError("Stored compilation is not verified")
        parameters = command["parameters"] if "manual_sql" in command else command["specification"]["parameters"]
        values = {item["name"]: parse_parameter_value(item["type"], item["value"], item["name"]) for item in parameters}
        if values != compiled.get("parameters"):
            raise ValueError("Stored parameter values do not match accepted decisions")
    except (ProjectCompilationError, QuerySpecificationValidationError, SqlGuardError, KeyError, TypeError, ValueError) as error:
        raise ConflictError("SQL_RESULT_SOURCE_UNAVAILABLE", "The saved SQL source cannot be verified") from error
    entity_ids, changed = authorize_catalog_sources(
        project, actor_id, command, compiled, is_admin=is_admin, access_role=access_role, extra_entity_ids=extra_entity_ids, include_schema_tables=include_schema_tables
    )
    return SqlReadSource(compilation, requirements, schema, query, command, compiled, tuple(sorted(entity_ids)), changed)


def authorize_catalog_sources(project, actor_id, command, compiled, *, is_admin=False, access_role="AUTHOR_CREATOR", extra_entity_ids=(), include_schema_tables=False):
    """Require current visibility of exposed catalog identities, allowing old versions."""
    BusinessDocumentAccess(actor_id, access_role, is_admin).require_view_sql(project.owner_id)
    try:
        snapshot = parse_schema_snapshot(command["schema_snapshot"])
        entity_ids = set(extra_entity_ids)
        accepted_ids = {entry["entity_id"] for entry in command["accepted_schema"]}
        if include_schema_tables:
            entity_ids.update(table.id for table in snapshot.tables)
        if any(snapshot.table(entity_id) is None or entity_id not in accepted_ids for entity_id in entity_ids):
            raise ValueError("A derived source is outside the accepted schema")
        for physical in compiled["guard"]["tables"] if compiled is not None else ():
            matches = [table for table in snapshot.tables if table.physical_relation.casefold() == physical.casefold()]
            if len(matches) != 1:
                raise ValueError("Physical source is missing or ambiguous")
            entity_ids.add(matches[0].id)
        if not entity_ids:
            raise ValueError("The SQL source has no catalog identity")
    except (QuerySpecificationValidationError, KeyError, TypeError, ValueError) as error:
        raise ConflictError("SQL_RESULT_SOURCE_UNAVAILABLE", "The saved SQL source cannot be verified") from error
    current = run_in_worker_loop(
        BusinessDocumentSqlQuerySchemaService.load_entities(
            actor_id,
            {"entity_ids": sorted(entity_ids), "locale": project.locale},
            is_admin,
            access_role,
        )
    )
    entities = {item["entity_id"]: item for item in current["entities"]}
    changed = False
    for entity_id in entity_ids:
        item = entities.get(entity_id)
        if item is None:
            raise PermissionDeniedError("The source of this result is no longer visible in the catalog")
        if item.get("lookup", {}).get("status") != "OK":
            raise BusinessDocumentError("SQL_SCHEMA_CATALOG_UNAVAILABLE", "Current source visibility could not be verified", 503)
        entity = item.get("entity")
        if not isinstance(entity, Mapping) or entity.get("id") != entity_id or entity.get("deleted"):
            raise PermissionDeniedError("The source of this result is no longer visible in the catalog")
        original = snapshot.table(entity_id)
        changed |= entity.get("version") != original.version or entity.get("schema_fingerprint") != original.schema_fingerprint or entity.get("fqn") != original.fqn
    return tuple(sorted(entity_ids)), changed


def read_sql_project(tenant_id: str, actor_id: str, project_id: str, is_admin: bool, access_role: str) -> BusinessDocumentSqlQueryProject:
    project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
    BusinessDocumentAccess(actor_id, access_role, is_admin).require_view_sql(project.owner_id)
    return project


def _run_metadata(project, run_id):
    run = (
        BusinessDocumentSqlQueryRun.select(
            BusinessDocumentSqlQueryRun.id,
            BusinessDocumentSqlQueryRun.compilation_id,
            BusinessDocumentSqlQueryRun.kind,
            BusinessDocumentSqlQueryRun.source_run_id,
            BusinessDocumentSqlQueryRun.status,
            BusinessDocumentSqlQueryRun.checks,
            BusinessDocumentSqlQueryRun.profile_id,
            BusinessDocumentSqlQueryRun.profile_version,
            BusinessDocumentSqlQueryRun.profile_name,
            BusinessDocumentSqlQueryRun.columns,
            BusinessDocumentSqlQueryRun.row_count,
            BusinessDocumentSqlQueryRun.create_time,
        )
        .where((BusinessDocumentSqlQueryRun.id == run_id) & (BusinessDocumentSqlQueryRun.project_id == project.id) & (BusinessDocumentSqlQueryRun.tenant_id == project.tenant_id))
        .first()
    )
    if run is None:
        raise BusinessDocumentError("SQL_RUN_NOT_FOUND", "Result was not found", 404)
    if run.status == "EXPIRED":
        raise BusinessDocumentError("SQL_RESULT_EXPIRED", "Result rows have expired", 410)
    if run.status != "READY":
        raise ConflictError("SQL_RESULT_REMOVED", "Result rows were removed")
    if not isinstance(run.checks, Mapping) or run.checks.get("status") != "PASS" or run.kind not in {"SQL", "PYTHON", "LOOKUP"}:
        raise ConflictError("SQL_RESULT_CHECK_REQUIRED", "A verified result is required")
    return run


def authorized_result(project, actor_id, run_id, *, is_admin=False, access_role="AUTHOR_CREATOR"):
    """Authorize the source before loading rows; recheck retained state afterwards."""
    expire_project_results(project.tenant_id, project.id)
    run = _run_metadata(project, run_id)
    parent = _run_metadata(project, run.source_run_id) if run.kind != "SQL" else run
    if parent.kind != "SQL" or parent.compilation_id != run.compilation_id or (parent.profile_id, parent.profile_version) != (run.profile_id, run.profile_version):
        raise ConflictError("SQL_RESULT_SOURCE_UNAVAILABLE", "The result source chain cannot be verified")
    extra = (run.checks.get("lookup_entity_id"),) if run.kind == "LOOKUP" else ()
    source = authorize_sql_source(project, actor_id, parent.compilation_id, is_admin=is_admin, access_role=access_role, extra_entity_ids=extra)
    expire_project_results(project.tenant_id, project.id)
    _run_metadata(project, run_id)
    current = BusinessDocumentSqlQueryRun.get_or_none(
        (BusinessDocumentSqlQueryRun.id == run.id)
        & (BusinessDocumentSqlQueryRun.project_id == project.id)
        & (BusinessDocumentSqlQueryRun.tenant_id == project.tenant_id)
        & (BusinessDocumentSqlQueryRun.status == "READY")
    )
    if current is None:
        raise ConflictError("SQL_RESULT_REMOVED", "Result rows were removed")
    if (current.compilation_id, current.source_run_id, current.profile_id, current.profile_version, current.kind, current.columns, current.checks) != (
        run.compilation_id,
        run.source_run_id,
        run.profile_id,
        run.profile_version,
        run.kind,
        run.columns,
        run.checks,
    ):
        raise ConflictError("SQL_RESULT_SOURCE_UNAVAILABLE", "The result changed during its access check")
    if (
        parent.id != current.id
        and not BusinessDocumentSqlQueryRun.select()
        .where(
            (BusinessDocumentSqlQueryRun.id == parent.id)
            & (BusinessDocumentSqlQueryRun.project_id == project.id)
            & (BusinessDocumentSqlQueryRun.tenant_id == project.tenant_id)
            & (BusinessDocumentSqlQueryRun.status == "READY")
        )
        .exists()
    ):
        raise ConflictError("SQL_RESULT_REMOVED", "The source rows were removed")
    if (
        not isinstance(current.columns, list)
        or not current.columns
        or any(not isinstance(column, str) or not column for column in current.columns)
        or len({column.casefold() for column in current.columns}) != len(current.columns)
        or not isinstance(current.rows, list)
        or len(current.rows) != current.row_count
        or any(not isinstance(row, list) or len(row) != len(current.columns) for row in current.rows)
    ):
        raise ConflictError("SQL_RESULT_CHECK_REQUIRED", "Stored result rows do not match their schema")
    deadline = result_expires_at(current.create_time, source_deadline=result_expires_at(parent.create_time))
    return current, source, deadline
