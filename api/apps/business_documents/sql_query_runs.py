"""Project-scoped SQL execution and result lifecycle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from peewee import SqliteDatabase

from business_documents.async_runtime import run_in_worker_loop
from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.apps.business_documents.sql_execution_registry import BusinessDocumentSqlExecutionRegistryService
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService, _stable_hash
from api.apps.business_documents.sql_query_access import authorized_result, read_sql_project
from api.apps.business_documents.sql_query_conclusions import propose_conclusion
from api.apps.business_documents.sql_query_postgres import check_postgres_connection, execute_postgres, explain_postgres, observe_postgres
from api.apps.business_documents.sql_query_python import execute_result_python
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService
from api.apps.business_documents.sql_query_retention import expire_due_results, expire_project_results
from api.apps.sql_assistant.definitions import require_definition_admission
from api.apps.business_documents.sql_llm_logs import require_llm_logs_admission
from api.db.db_models import (
    BusinessDocumentJob,
    BusinessDocumentSqlExecutionProfile,
    BusinessDocumentSqlQueryArtifact,
    BusinessDocumentSqlQueryProject,
    BusinessDocumentSqlQueryRun,
    Connector,
    User,
)
from api.db.services.managed_resource_service import ManagedResourceService
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError, ValidationError
from business_documents.sql_query.conclusion import ConclusionValidationError, validate_conclusion
from business_documents.sql_query import IN_PROGRESS_RUN_STATUSES
from business_documents.sql_query.project_compilation import ProjectCompilationError, build_project_compile_command, build_project_schema_command
from business_documents.sql_query.query_specification import compile_query_payload, guard_read_only_sql, parse_schema_snapshot
from business_documents.sql_query.result_validation import ResultValidationError, preview_rows, validate_result
from business_documents.sql_query.result_retention import result_expires_at
from business_documents.sql_query.execution import failed_outcome
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp

_MAX_PYTHON_INPUT_BYTES = 10_000_000
_MAX_OWNER_ACTIVE_RESULTS = 20
_MAX_OWNER_STORED_BYTES = 100_000_000


def _locked(model, identity):
    query = model.select().where(model.id == identity)
    if not isinstance(model._meta.database, SqliteDatabase):
        query = query.for_update()
    return query.get()


def _execution_event(job, worker_id, lease_token, phase, *, expected_attempt=None, **values):
    with BusinessDocumentJob._meta.database.atomic():
        current_job = _locked(BusinessDocumentJob, job.id)
        run = _locked(BusinessDocumentSqlQueryRun, job.payload["run_id"])
        try:
            BusinessDocumentSqlAgentService._require_job_lease(current_job, worker_id, lease_token)
        except ConflictError:
            # The original attempt may acknowledge completion after its lease
            # was recovered. It can resolve UNKNOWN, never publish rows or
            # acknowledge a different attempt.
            if phase != "SUCCEEDED" or run.status != "UNKNOWN" or expected_attempt is None or run.execution.get("attempt_id") != expected_attempt or not run.execution.get("backend"):
                raise
            execution = {**run.execution, **values, "phase": "SUCCEEDED", "output_completeness": "unavailable", "updated_at": current_timestamp()}
            BusinessDocumentSqlQueryRun.update(status="SUCCEEDED", execution=execution).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
            return execution
        if phase == "DISPATCHING":
            if run.status == "CANCEL_REQUESTED":
                raise BusinessDocumentError("SQL_CANCELED", "Query canceled before dispatch", 409)
            if run.status != "RUNNING" or run.execution.get("phase") != "PREPARED":
                raise ConflictError("SQL_DISPATCH_FENCED", "This SQL attempt cannot be sent again")
        if run.status not in {"RUNNING", "CANCEL_REQUESTED"}:
            raise ConflictError("SQL_RUN_STATE_CHANGED", "SQL run state changed")
        execution = {"attempt_id": get_uuid(), **run.execution, **values, "phase": phase, "updated_at": current_timestamp()}
        BusinessDocumentSqlQueryRun.update(execution=execution).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
        return execution


def _command(raw: object, fields: set[str]) -> tuple[int, str]:
    if not isinstance(raw, Mapping) or set(raw) != fields | {"schema_version", "expected_state_version", "idempotency_key"}:
        raise ValidationError("INVALID_SQL_RUN_COMMAND", "Invalid project command")
    if raw.get("schema_version") != "1":
        raise ValidationError("INVALID_SQL_RUN_COMMAND", "Unsupported schema version")
    version = raw.get("expected_state_version")
    key = raw.get("idempotency_key")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1 or not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise ValidationError("INVALID_SQL_RUN_COMMAND", "Version and idempotency key are required")
    return version, key


def _selected_profile(value: object) -> str | None:
    if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 32 or value.strip() != value):
        raise ValidationError("INVALID_SQL_CONNECTION", "Select an available source or automatic selection")
    return value


def _current_actor_access(actor_id: str) -> tuple[str, bool]:
    user = User.get_or_none(User.id == actor_id)
    if user is None or str(user.is_active) != "1" or str(user.status) != "1":
        raise PermissionDeniedError("The query owner is no longer active")
    return str(user.business_document_role or "AUTHOR_EDITOR"), bool(user.is_superuser)


def _project_artifacts(project: BusinessDocumentSqlQueryProject, kinds: tuple[str, ...]) -> list[BusinessDocumentSqlQueryArtifact]:
    ids = {"REQUIREMENTS": project.requirements_artifact_id, "SCHEMA": project.schema_artifact_id, "QUERY": project.query_artifact_id}
    if not all(ids[kind] for kind in kinds):
        raise ConflictError("SQL_PROJECT_INCOMPLETE", "Confirm the project decisions before execution")
    artifacts = [BusinessDocumentSqlQueryArtifact.get_or_none(BusinessDocumentSqlQueryArtifact.id == ids[kind]) for kind in kinds]
    if any(item is None or item.project_id != project.id or item.tenant_id != project.tenant_id or item.kind != kind for item, kind in zip(artifacts, kinds, strict=True)):
        raise ConflictError("SQL_PROJECT_ARTIFACT_MISMATCH", "Project artifacts have changed")
    return artifacts


def _schema_source(project: BusinessDocumentSqlQueryProject) -> dict[str, Any]:
    requirements, schema = _project_artifacts(project, ("REQUIREMENTS", "SCHEMA"))
    try:
        return build_project_schema_command(requirements.payload, schema.payload)
    except ProjectCompilationError as error:
        raise ConflictError("SQL_PROJECT_INCOMPLETE", "Confirm the project requirements and schema first") from error


def _source(project: BusinessDocumentSqlQueryProject) -> tuple[BusinessDocumentSqlQueryArtifact, dict[str, Any]]:
    if project.stage != "COMPLETE":
        raise ConflictError("SQL_PROJECT_INCOMPLETE", "Confirm the project decisions before execution")
    artifacts = _project_artifacts(project, ("REQUIREMENTS", "SCHEMA", "QUERY"))
    ids = [item.id for item in artifacts]
    compilation = (
        BusinessDocumentSqlQueryArtifact.select()
        .where((BusinessDocumentSqlQueryArtifact.project_id == project.id) & (BusinessDocumentSqlQueryArtifact.kind == "COMPILATION"))
        .order_by(BusinessDocumentSqlQueryArtifact.revision.desc())
        .first()
    )
    if compilation is None or compilation.payload.get("source_artifact_ids") != ids:
        raise ConflictError("SQL_COMPILATION_STALE", "Compile the current project decisions first")
    command = build_project_compile_command(*(item.payload for item in artifacts))
    fresh = compile_query_payload(command)
    if fresh.get("status") != "READY" or fresh.get("guard", {}).get("status") != "PASS" or fresh != compilation.payload.get("result"):
        raise ConflictError("SQL_COMPILATION_STALE", "Compiled SQL no longer matches accepted decisions")
    require_definition_admission(project, compilation)
    return compilation, command


def _binding(actor_id: str, is_admin: bool, access_role: str, command: dict[str, Any], selected: str | None):
    resolution = BusinessDocumentSqlExecutionRegistryService.resolve(
        actor_id,
        {
            "schema_version": "1",
            "schema_snapshot": command["schema_snapshot"],
            "accepted_requirements": command["accepted_requirements"],
            "accepted_schema": command["accepted_schema"],
            "selected_profile_id": selected,
        },
        is_admin,
        access_role,
    )
    if resolution["status"] != "BOUND":
        return resolution, None, None
    selection = resolution["selection"]
    public_profile = selection["profile"]
    registry_tenant = ManagedResourceService.owner_id()
    profile = BusinessDocumentSqlExecutionProfile.get_or_none(
        (BusinessDocumentSqlExecutionProfile.id == public_profile["id"])
        & (BusinessDocumentSqlExecutionProfile.tenant_id == registry_tenant)
        & (BusinessDocumentSqlExecutionProfile.version == public_profile["version"])
    )
    if profile is None or not profile.enabled:
        raise ConflictError("SQL_PROFILE_STALE", "Execution profile changed; check the source again")
    connector = Connector.get_or_none((Connector.id == profile.connector_id) & (Connector.tenant_id == registry_tenant))
    if connector is None:
        raise ConflictError("SQL_SOURCE_UNAVAILABLE", "PostgreSQL source is unavailable")
    return resolution, profile, connector


def _project_binding(project: BusinessDocumentSqlQueryProject, command: dict[str, Any], actor_id: str, is_admin: bool, access_role: str, selected: str | None):
    try:
        return _binding(actor_id, is_admin, access_role, command, selected)
    except ValidationError as error:
        if selected is None or selected != project.execution_profile_id or error.code != "INVALID_SQL_EXECUTION_BINDING_REQUEST":
            raise
        available, _, _ = _binding(actor_id, is_admin, access_role, command, None)
        return {**available, "status": "UNAVAILABLE", "reason": "SAVED_PROFILE_UNAVAILABLE", "selection": None}, None, None


def _verify_schema(command: dict[str, Any], actor_id: str, is_admin: bool, access_role: str) -> bool:
    """Recheck catalog ACL and accepted table versions immediately before use."""
    return run_in_worker_loop(BusinessDocumentSqlQuerySchemaService.verify_snapshot(actor_id, command["schema_snapshot"], is_admin, access_role))


def _purge_conclusion_drafts(project_id: str, tenant_id: str, source_run_id: str) -> None:
    """Remove result-derived text when the source rows leave the workspace."""
    for artifact in BusinessDocumentSqlQueryArtifact.select().where(
        (BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id) & (BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION")
    ):
        if artifact.payload.get("source_run_id") == source_run_id:
            artifact.delete_instance()


def _check_owner_result_quota(tenant_id: str, *, new_result: bool, additional_bytes: int = 0) -> None:
    """Serialize quota decisions for one signed-in owner in the metadata DB."""
    database = BusinessDocumentSqlQueryRun._meta.database
    if not isinstance(database, SqliteDatabase):
        User.select(User.id).where(User.id == tenant_id).for_update().get()
    expire_due_results(tenant_id=tenant_id)
    active = BusinessDocumentSqlQueryRun.select(
        BusinessDocumentSqlQueryRun.status,
        BusinessDocumentSqlQueryRun.result_bytes,
    ).where((BusinessDocumentSqlQueryRun.tenant_id == tenant_id) & (BusinessDocumentSqlQueryRun.status.in_((*IN_PROGRESS_RUN_STATUSES, "READY"))))
    count = 0
    stored_bytes = 0
    for item in active:
        count += 1
        if item.status == "READY":
            stored_bytes += item.result_bytes
    if count + int(new_result) > _MAX_OWNER_ACTIVE_RESULTS or stored_bytes + additional_bytes > _MAX_OWNER_STORED_BYTES:
        raise ConflictError("SQL_RESULT_OWNER_QUOTA", "Finish or cancel older results before creating another")


def _require_run_lifetime(run: BusinessDocumentSqlQueryRun) -> None:
    if result_expires_at(run.create_time) <= current_timestamp():
        raise BusinessDocumentError("SQL_RESULT_EXPIRED", "Result rows have expired; run the query again", 410)


def _retained_sql_result(project: BusinessDocumentSqlQueryProject, run_id: str) -> BusinessDocumentSqlQueryRun | None:
    expire_project_results(project.tenant_id, project.id)
    scope = (
        (BusinessDocumentSqlQueryRun.id == run_id)
        & (BusinessDocumentSqlQueryRun.project_id == project.id)
        & (BusinessDocumentSqlQueryRun.tenant_id == project.tenant_id)
        & (BusinessDocumentSqlQueryRun.kind == "SQL")
    )
    metadata_query = BusinessDocumentSqlQueryRun.select(BusinessDocumentSqlQueryRun.id, BusinessDocumentSqlQueryRun.status, BusinessDocumentSqlQueryRun.create_time).where(scope)
    database = BusinessDocumentSqlQueryRun._meta.database
    if database.in_transaction() and not isinstance(database, SqliteDatabase):
        # Keep the final source check and publication ahead of competing cleanup.
        metadata_query = metadata_query.for_update()
    metadata = metadata_query.first()
    if metadata is not None and metadata.status == "EXPIRED":
        raise BusinessDocumentError("SQL_RESULT_EXPIRED", "Result rows have expired; run the query again", 410)
    if metadata is None or metadata.status != "READY":
        return None
    _require_run_lifetime(metadata)
    return BusinessDocumentSqlQueryRun.get_or_none(scope & (BusinessDocumentSqlQueryRun.status == "READY"))


class BusinessDocumentSqlRunService:
    """One verified run and immutable document revision per project action."""

    @classmethod
    def configure_connection(cls, tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        version, key = _command(raw, {"selected_profile_id"})
        selected = _selected_profile(raw["selected_profile_id"])
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        access = BusinessDocumentAccess(actor_id, access_role, is_admin)
        access.require_edit(project.owner_id)
        BusinessDocumentSqlAgentService._require_active(project)
        access.require_execute_sql()
        request_hash = _stable_hash({"type": "CONFIGURE_CONNECTION", "request": raw})
        replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
        if replay is not None:
            return replay
        if project.state_version != version or project.operation_state != "IDLE":
            raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Reload the project before changing its source")
        if selected is not None:
            command = _schema_source(project)
            _verify_schema(command, actor_id, is_admin, access_role)
            resolution, profile, connector = _binding(actor_id, is_admin, access_role, command, selected)
            if profile is None or connector is None or profile.id != selected:
                raise ConflictError("SQL_EXECUTION_BINDING_REQUIRED", "Select an available PostgreSQL source", {"binding": resolution})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            current = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
            access.require_edit(current.owner_id)
            BusinessDocumentSqlAgentService._require_active(current)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if current.state_version != version or current.operation_state != "IDLE":
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed while its source was being checked")
            active = BusinessDocumentSqlQueryRun.select().where(
                (BusinessDocumentSqlQueryRun.tenant_id == tenant_id) & (BusinessDocumentSqlQueryRun.project_id == project_id) & (BusinessDocumentSqlQueryRun.status.in_(IN_PROGRESS_RUN_STATUSES))
            )
            if active.exists():
                raise ConflictError("SQL_RUN_ACTIVE", "Wait for the current run before changing its source")
            if current.execution_profile_id != selected:
                changed = (
                    BusinessDocumentSqlQueryProject.update(
                        execution_profile_id=selected,
                        state_version=version + 1,
                        update_time=current_timestamp(),
                        update_date=datetime.now(),
                    )
                    .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.tenant_id == tenant_id) & (BusinessDocumentSqlQueryProject.state_version == version))
                    .execute()
                )
                if changed != 1:
                    raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed while its source was being saved")
            response = BusinessDocumentSqlAgentService._project(BusinessDocumentSqlAgentService._get_project(tenant_id, project_id), access=access)
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def preflight(cls, tenant_id: str, actor_id: str, project_id: str, selected_profile_id: str | None, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        access = BusinessDocumentAccess(actor_id, access_role, is_admin)
        access.require_view_sql(project.owner_id)
        BusinessDocumentSqlAgentService._require_active(project)
        compilation, command = _source(project)
        if not access.can_execute_sql():
            return {
                "compilation_id": compilation.id,
                "state_version": project.state_version,
                "binding": {"status": "UNAVAILABLE"},
                "blocker": {"code": "SQL_EXECUTION_FORBIDDEN", "message": "Your role can save the verified SQL but cannot execute it"},
            }
        catalog_age_warning = _verify_schema(command, actor_id, is_admin, access_role)
        selected = selected_profile_id if selected_profile_id is not None else project.execution_profile_id
        resolution, profile, connector = _project_binding(project, command, actor_id, is_admin, access_role, selected)
        if resolution.get("reason") == "SAVED_PROFILE_UNAVAILABLE":
            return {
                "compilation_id": compilation.id,
                "state_version": project.state_version,
                "binding": resolution,
                "blocker": {"code": "SQL_PROFILE_UNAVAILABLE", "message": "Сохранённый источник недоступен. Выберите другой профиль."},
            }
        result = {"compilation_id": compilation.id, "state_version": project.state_version, "binding": resolution}
        if catalog_age_warning:
            result["warning"] = "OpenMetadata has not recorded a recent metadata change; the live PostgreSQL shape is checked separately."
        if profile is not None:
            limit = compilation.payload["result"]["parameters"]["row_limit"]
            if limit > profile.max_rows:
                result["blocker"] = {"code": "SQL_ROW_LIMIT_EXCEEDED", "message": "Reduce the row limit to fit the source profile"}
            elif connector is not None:
                compiled = compilation.payload["result"]
                timezone = require_definition_admission(project, compilation)
                policy = require_llm_logs_admission(compilation.payload, actor_id, profile.id)
                if policy is not None:
                    from api.apps.business_documents.sql_query_postgres import check_source_access

                    check_source_access(connector.config, policy)
                    result["source_completeness"] = policy["source_completeness"]
                explain_postgres(connector.config, compiled["sql"], compiled["parameters"], timeout_ms=profile.statement_timeout_ms, **({"timezone": timezone} if timezone else {}))
        return result

    @classmethod
    def check_connection(cls, tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "selected_profile_id"} or raw.get("schema_version") != "1":
            raise ValidationError("INVALID_SQL_CONNECTION", "Invalid connection check command")
        selected = _selected_profile(raw["selected_profile_id"])
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        access = BusinessDocumentAccess(actor_id, access_role, is_admin)
        access.require_view_sql(project.owner_id)
        access.require_execute_sql()
        BusinessDocumentSqlAgentService._require_active(project)
        command = _schema_source(project)
        _verify_schema(command, actor_id, is_admin, access_role)
        resolution, profile, connector = _project_binding(project, command, actor_id, is_admin, access_role, selected)
        result = {
            "schema_version": "1",
            "project_id": project_id,
            "state_version": project.state_version,
            "status": resolution["status"],
            "binding": resolution,
            "query_checked": False,
            "checked_at": None,
            "duration_ms": None,
        }
        if profile is not None and connector is not None:
            result.update(status="AVAILABLE", duration_ms=check_postgres_connection(connector.config, timeout_ms=min(profile.statement_timeout_ms, 5000)), checked_at=current_timestamp())
        return result

    @classmethod
    def run(cls, tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        version, key = _command(raw, {"selected_profile_id"})
        request_hash = _stable_hash({"type": "RUN", "request": raw})
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        access = BusinessDocumentAccess(actor_id, access_role, is_admin)
        access.require_edit(project.owner_id)
        BusinessDocumentSqlAgentService._require_active(project)
        access.require_execute_sql()
        replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
        if replay is not None:
            return replay
        if project.state_version != version or project.operation_state != "IDLE":
            raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before execution")
        compilation, command = _source(project)
        _verify_schema(command, actor_id, is_admin, access_role)
        selected = raw["selected_profile_id"] if raw["selected_profile_id"] is not None else project.execution_profile_id
        if selected is not None and (not isinstance(selected, str) or not selected):
            raise ValidationError("INVALID_SQL_RUN_COMMAND", "selected_profile_id is invalid")
        resolution, profile, connector = _binding(actor_id, is_admin, access_role, command, selected)
        if profile is None or connector is None:
            raise ConflictError("SQL_EXECUTION_BINDING_REQUIRED", "Select an available PostgreSQL source", {"binding": resolution})
        compiled = compilation.payload["result"]
        if compiled["parameters"]["row_limit"] > profile.max_rows:
            raise ValidationError("SQL_ROW_LIMIT_EXCEEDED", "Reduce the row limit to fit the source profile")
        timezone = require_definition_admission(project, compilation)
        source_policy = require_llm_logs_admission(compilation.payload, actor_id, profile.id)
        if source_policy is not None:
            from api.apps.business_documents.sql_query_postgres import check_source_access

            check_source_access(connector.config, source_policy)
        explain_postgres(connector.config, compiled["sql"], compiled["parameters"], timeout_ms=profile.statement_timeout_ms, **({"timezone": timezone} if timezone else {}))
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            _check_owner_result_quota(tenant_id, new_result=True)
            current = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(current.owner_id)
            BusinessDocumentSqlAgentService._require_active(current)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if current.state_version != version or current.operation_state != "IDLE":
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed before the query was queued")
            active_runs = BusinessDocumentSqlQueryRun.select().where(
                (BusinessDocumentSqlQueryRun.project_id == project_id) & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id) & (BusinessDocumentSqlQueryRun.status.in_(IN_PROGRESS_RUN_STATUSES))
            )
            if active_runs.exists():
                raise ConflictError("SQL_ACTIVE_RESULT_QUOTA", "Finish or cancel the active query before another run")
            job_id = get_uuid()
            run = BusinessDocumentSqlQueryRun.create(
                id=get_uuid(),
                project_id=project_id,
                tenant_id=tenant_id,
                compilation_id=compilation.id,
                profile_id=profile.id,
                profile_version=profile.version,
                profile_name=getattr(profile, "name", None),
                job_id=job_id,
                status="QUEUED",
                columns=[],
                rows=[],
                checks={},
                execution={"phase": "REGISTERED", "attempt_id": get_uuid()},
            )
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    state_version=version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.state_version == version))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed while the query was queued")
            BusinessDocumentJob.create(
                id=job_id,
                document_id=project_id,
                tenant_id=tenant_id,
                job_type="SQL_QUERY_RUN",
                dedupe_key=_stable_hash({"project_id": project_id, "run_id": run.id}),
                source_state_version=version + 1,
                payload={"run_id": run.id, "actor_id": actor_id, "selected_profile_id": profile.id},
                available_at=current_timestamp(),
                max_attempts=1,
                correlation_id=get_uuid(),
            )
            response = {"run_id": run.id, "status": "QUEUED", "state_version": version + 1}
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
        from api.apps.business_documents.worker import wake_business_document_worker

        wake_business_document_worker()
        return response

    @classmethod
    def execute_job(cls, job: BusinessDocumentJob, worker_id: str, lease_token: str) -> dict[str, Any]:
        """Run the same project scenario as the API, then let the lease holder publish."""
        BusinessDocumentSqlAgentService._require_job_lease(job, worker_id, lease_token)
        payload = job.payload
        run = BusinessDocumentSqlQueryRun.get_by_id(payload["run_id"])
        if run.tenant_id != job.tenant_id or run.project_id != job.document_id or run.job_id != job.id:
            raise ConflictError("SQL_RUN_JOB_MISMATCH", "SQL run job no longer matches its project")
        if run.status == "PURGED":
            return {"status": "PURGED"}
        if run.status == "CANCEL_REQUESTED" and run.execution.get("phase") in {"REGISTERED", "PREPARED"}:
            return {"status": "CANCELED"}
        changed = BusinessDocumentSqlQueryRun.update(status="RUNNING").where((BusinessDocumentSqlQueryRun.id == run.id) & (BusinessDocumentSqlQueryRun.status == "QUEUED")).execute()
        if changed != 1:
            current = BusinessDocumentSqlQueryRun.get_by_id(run.id)
            if current.status == "CANCEL_REQUESTED" and current.execution.get("phase") in {"REGISTERED", "PREPARED"}:
                return {"status": "CANCELED"}
            raise ConflictError("SQL_RUN_STATE_CHANGED", "SQL run state changed before execution")
        actor_id = payload["actor_id"]
        access_role, is_admin = _current_actor_access(actor_id)
        project = BusinessDocumentSqlAgentService._get_project(job.tenant_id, job.document_id)
        access = BusinessDocumentAccess(actor_id, access_role, is_admin)
        access.require_edit(project.owner_id)
        BusinessDocumentSqlAgentService._require_active(project)
        access.require_execute_sql()
        compilation, command = _source(project)
        if compilation.id != run.compilation_id:
            raise ConflictError("SQL_COMPILATION_STALE", "Compiled SQL changed before execution")
        _verify_schema(command, actor_id, is_admin, access_role)
        resolution, profile, connector = _binding(actor_id, is_admin, access_role, command, payload["selected_profile_id"])
        if profile is None or connector is None or profile.id != run.profile_id or profile.version != run.profile_version:
            raise ConflictError("SQL_PROFILE_STALE", "Execution profile changed before execution", {"binding": resolution})
        compiled = compilation.payload["result"]
        row_limit = compiled["parameters"]["row_limit"]
        if row_limit > profile.max_rows:
            raise ConflictError("SQL_ROW_LIMIT_EXCEEDED", "Source row limit changed before execution")
        _require_run_lifetime(run)
        timezone = require_definition_admission(project, compilation)
        explain_postgres(connector.config, compiled["sql"], compiled["parameters"], timeout_ms=profile.statement_timeout_ms, **({"timezone": timezone} if timezone else {}))

        def canceled() -> bool:
            with BusinessDocumentSqlQueryRun._meta.database.connection_context():
                current = BusinessDocumentSqlQueryRun.get_by_id(run.id)
                current_job = BusinessDocumentJob.get_by_id(job.id)
                return current.status in {"CANCEL_REQUESTED", "PURGED"} or current_job.lease_token != lease_token or current_job.status != "RUNNING"

        timezone = require_definition_admission(project, compilation)
        _execution_event(job, worker_id, lease_token, "PREPARED")
        attempt_id = BusinessDocumentSqlQueryRun.get_by_id(run.id).execution["attempt_id"]
        source_policy = require_llm_logs_admission(compilation.payload, actor_id, profile.id)
        if source_policy is not None:
            _execution_event(job, worker_id, lease_token, "PREPARED", source_completeness=source_policy["source_completeness"], access_fingerprint=source_policy["access_fingerprint"])

        def dispatch(identity):
            current_role, current_admin = _current_actor_access(actor_id)
            current_access = BusinessDocumentAccess(actor_id, current_role, current_admin)
            current_access.require_edit(project.owner_id)
            current_access.require_execute_sql()
            require_definition_admission(project, compilation)
            current_policy = require_llm_logs_admission(compilation.payload, actor_id, profile.id)
            _execution_event(job, worker_id, lease_token, "DISPATCHING", backend=identity, **({"source_completeness": current_policy["source_completeness"]} if current_policy else {}))

        result = execute_postgres(
            connector.config,
            compiled["sql"],
            {**compiled["parameters"], "row_limit": row_limit + 1},
            timeout_ms=profile.statement_timeout_ms,
            max_rows=row_limit + 1,
            max_result_bytes=profile.max_result_bytes,
            should_cancel=canceled,
            on_dispatch=dispatch,
            on_completed=lambda: _execution_event(job, worker_id, lease_token, "SUCCEEDED", expected_attempt=attempt_id, completed_at=current_timestamp()),
            source_policy={**source_policy, "attempt_name": "ragflow-sql:" + run.execution["attempt_id"]} if source_policy else None,
            **({"timezone": timezone} if timezone else {}),
        )
        _execution_event(job, worker_id, lease_token, "SUCCEEDED", expected_attempt=attempt_id, completed_at=current_timestamp())
        try:
            checked = validate_result(
                result,
                expected_columns=compiled["output_columns"],
                row_limit=row_limit,
                max_result_bytes=profile.max_result_bytes,
            )
        except ResultValidationError as exc:
            raise BusinessDocumentError("SQL_RESULT_CHECK_FAILED", str(exc), 422) from exc
        return {"status": "READY", "result": checked, "duration_ms": result["duration_ms"]}

    @classmethod
    def complete_job(cls, job: BusinessDocumentJob, worker_id: str, lease_token: str, output: dict[str, Any]) -> None:
        with BusinessDocumentJob._meta.database.atomic():
            if output["status"] == "READY":
                _check_owner_result_quota(job.tenant_id, new_result=False, additional_bytes=output["result"]["result_bytes"])
            current_job = _locked(BusinessDocumentJob, job.id)
            BusinessDocumentSqlAgentService._require_job_lease(current_job, worker_id, lease_token)
            run = _locked(BusinessDocumentSqlQueryRun, job.payload["run_id"])
            if run.status == "PURGED" or output["status"] != "READY":
                BusinessDocumentSqlQueryRun.update(rows=[], columns=[], checks={}, status="PURGED" if run.status == "PURGED" else "CANCELLED", execution={**run.execution, "phase": "CANCELLED"}).where(
                    BusinessDocumentSqlQueryRun.id == run.id
                ).execute()
                status = "CANCELED"
            elif run.status in {"RUNNING", "CANCEL_REQUESTED"}:
                _require_run_lifetime(run)
                checked = output["result"]
                BusinessDocumentSqlQueryRun.update(
                    status="READY",
                    rows=checked["rows"],
                    columns=checked["columns"],
                    row_count=checked["row_count"],
                    result_bytes=checked["result_bytes"],
                    duration_ms=output["duration_ms"],
                    checks=checked["checks"],
                ).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
                status = "COMPLETED"
            else:
                raise ConflictError("SQL_RUN_STATE_CHANGED", "SQL run state changed while result was being saved")
            BusinessDocumentJob.update(
                status=status,
                result={"run_id": run.id, "status": status},
                progress=1.0,
                progress_stage=status,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                update_time=current_timestamp(),
                update_date=datetime.now(),
            ).where(BusinessDocumentJob.id == job.id).execute()

    @classmethod
    def fail_job(cls, job: BusinessDocumentJob, worker_id: str, lease_token: str, error: dict[str, Any]) -> None:
        with BusinessDocumentJob._meta.database.atomic():
            current_job = _locked(BusinessDocumentJob, job.id)
            BusinessDocumentSqlAgentService._require_job_lease(current_job, worker_id, lease_token)
            run = _locked(BusinessDocumentSqlQueryRun, job.payload["run_id"])
            original_phase = run.execution.get("phase")
            if original_phase is None and run.status in {"RUNNING", "CANCEL_REQUESTED"}:
                original_phase = "DISPATCHING"
            outcome, phase = failed_outcome(original_phase, error.get("code", "WORKER_FAILURE"))
            if run.status == "PURGED":
                outcome = "PURGED"
            canceled, expired = outcome in {"PURGED", "CANCELLED"}, outcome == "EXPIRED"
            BusinessDocumentSqlQueryRun.update(
                status=outcome,
                rows=[],
                columns=[],
                checks={},
                error=None if canceled or expired else error,
                execution={**run.execution, "phase": phase, "updated_at": current_timestamp(), "output_completeness": "unavailable"},
            ).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
            BusinessDocumentJob.update(
                status="CANCELED" if canceled else "COMPLETED" if outcome == "SUCCEEDED" else "FAILED",
                error=None if canceled else error,
                progress_stage="CANCELED" if canceled else "FAILED",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                update_time=current_timestamp(),
                update_date=datetime.now(),
            ).where(BusinessDocumentJob.id == job.id).execute()

    @classmethod
    def cancel_result(cls, tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR", *, discard: bool = False) -> dict[str, Any]:
        version, key = _command(raw, {"run_id"})
        request_hash = _stable_hash({"type": "DISCARD_RESULT" if discard else "CANCEL_RESULT", "request": raw})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
            BusinessDocumentSqlAgentService._require_active(project)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != version:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before cancellation")
            run = BusinessDocumentSqlQueryRun.get_or_none(
                (BusinessDocumentSqlQueryRun.id == raw["run_id"])
                & (BusinessDocumentSqlQueryRun.project_id == project_id)
                & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id)
                & (BusinessDocumentSqlQueryRun.status.in_(("QUEUED", "RUNNING", "READY", "CANCEL_REQUESTED", "UNKNOWN", "SUCCEEDED")))
            )
            if run is None:
                raise ConflictError("SQL_RESULT_MISSING", "Active result was not found")
            if run.job_id:
                _locked(BusinessDocumentJob, run.job_id)
            run = _locked(BusinessDocumentSqlQueryRun, run.id)
            if discard and run.status != "READY":
                raise ConflictError("SQL_RUN_ACTIVE", "Only a saved result can be removed; execution must be resolved first")
            status = run.status if run.status in {"UNKNOWN", "SUCCEEDED", "READY", "CANCEL_REQUESTED"} else "CANCELLED"
            if run.status == "RUNNING" and run.execution.get("phase") == "SUCCEEDED":
                status = "RUNNING"
            elif run.status == "RUNNING":
                status = "CANCEL_REQUESTED"
            elif run.status == "QUEUED":
                canceled_job = (
                    BusinessDocumentJob.update(status="CANCELED", progress_stage="CANCELED")
                    .where((BusinessDocumentJob.id == run.job_id) & (BusinessDocumentJob.status.in_(("PENDING", "RETRY"))))
                    .execute()
                )
                if canceled_job != 1:
                    status = "CANCEL_REQUESTED"
            if discard:
                status = "PURGED"
            BusinessDocumentSqlQueryRun.update(
                status=status, **({"rows": [], "execution": {**run.execution, "phase": "CANCELLED"}} if status == "CANCELLED" else {"rows": []} if status == "PURGED" else {})
            ).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
            if run.kind == "SQL" and status == "PURGED":
                _purge_conclusion_drafts(project_id, tenant_id, run.id)
                BusinessDocumentSqlQueryRun.update(rows=[], status="PURGED").where(
                    (BusinessDocumentSqlQueryRun.project_id == project_id)
                    & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id)
                    & (BusinessDocumentSqlQueryRun.source_run_id == run.id)
                    & (BusinessDocumentSqlQueryRun.status == "READY")
                ).execute()
            BusinessDocumentSqlQueryProject.update(
                state_version=version + 1,
                update_time=current_timestamp(),
                update_date=datetime.now(),
            ).where(BusinessDocumentSqlQueryProject.id == project_id).execute()
            response = {"run_id": run.id, "rows_status": status, "state_version": version + 1}
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
        if status == "UNKNOWN" and run.execution.get("backend"):
            response["observation"] = cls.execution_status(tenant_id, actor_id, project_id, run.id, is_admin, access_role, cancel=True).get("observation")
        return response

    @classmethod
    def execution_status(cls, tenant_id, actor_id, project_id, run_id, is_admin=False, access_role="AUTHOR_CREATOR", *, cancel=False):
        project = read_sql_project(tenant_id, actor_id, project_id, is_admin, access_role)
        run = BusinessDocumentSqlQueryRun.get_or_none(
            (BusinessDocumentSqlQueryRun.id == run_id) & (BusinessDocumentSqlQueryRun.project_id == project.id) & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id)
        )
        if run is None:
            raise BusinessDocumentError("SQL_RUN_NOT_FOUND", "SQL run was not found", 404)
        result = {"run_id": run.id, "status": run.status, "phase": run.execution.get("phase", "UNKNOWN"), "resend_allowed": False}
        if run.status == "UNKNOWN" and run.execution.get("backend"):
            compilation, command = _source(project)
            _, profile, connector = _binding(actor_id, is_admin, access_role, command, run.profile_id)
            if profile is None or profile.version != run.profile_version or connector is None:
                raise ConflictError("SQL_PROFILE_STALE", "The original execution profile is unavailable")
            require_llm_logs_admission(compilation.payload, actor_id, profile.id)
            result["observation"] = observe_postgres(connector.config, run.execution["backend"], cancel=cancel)
        return result

    @classmethod
    def preview(cls, tenant_id: str, actor_id: str, project_id: str, run_id: str, offset: int = 0, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        project = read_sql_project(tenant_id, actor_id, project_id, is_admin, access_role)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValidationError("INVALID_SQL_PREVIEW", "offset must be a non-negative integer")
        run, _, deadline = authorized_result(project, actor_id, run_id, is_admin=is_admin, access_role=access_role)
        return {
            "run_id": run.id,
            "expires_at": deadline,
            "columns": run.columns,
            "rows": preview_rows(run.rows[offset : offset + 100]),
            "offset": offset,
            "row_count": run.row_count,
            "duration_ms": run.duration_ms,
            "result_bytes": run.result_bytes,
            "checks": run.checks,
        }

    @classmethod
    def run_python(cls, tenant_id: str, actor_id: str, project_id: str, source_run_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        version, key = _command(raw, {"code"})
        code = raw["code"]
        if not isinstance(code, str) or not 1 <= len(code) <= 20_000 or "def main(" not in code:
            raise ValidationError("INVALID_SQL_PYTHON_CODE", "Python must define main(columns, rows) and stay within 20,000 characters")
        request_hash = _stable_hash({"type": "RUN_PYTHON", "source_run_id": source_run_id, "request": raw})
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
        BusinessDocumentSqlAgentService._require_active(project)
        replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
        if replay is not None:
            return replay
        if project.state_version != version:
            raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before Python transformation")
        source = _retained_sql_result(project, source_run_id)
        if source is None or source.checks.get("status") != "PASS":
            raise ConflictError("SQL_VERIFIED_RESULT_REQUIRED", "A verified SQL result is required for Python")
        source_bytes = len(json.dumps({"columns": source.columns, "rows": source.rows}, ensure_ascii=False, default=str).encode("utf-8"))
        if source_bytes > _MAX_PYTHON_INPUT_BYTES:
            raise ValidationError("SQL_PYTHON_INPUT_TOO_LARGE", "Result is too large for isolated Python transformation")
        output = execute_result_python(code, source.columns, source.rows)
        if not isinstance(output.get("columns"), list):
            raise ValidationError("SQL_PYTHON_RESULT_INVALID", "Python must return columns and rows")
        try:
            checked = validate_result(
                output,
                expected_columns=output["columns"],
                row_limit=10_000,
                max_result_bytes=_MAX_PYTHON_INPUT_BYTES,
            )
        except ResultValidationError as exc:
            raise ValidationError("SQL_PYTHON_RESULT_INVALID", str(exc)) from exc
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            _check_owner_result_quota(tenant_id, new_result=True, additional_bytes=checked["result_bytes"])
            current = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(current.owner_id)
            BusinessDocumentSqlAgentService._require_active(current)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            fresh_source = _retained_sql_result(current, source_run_id)
            if current.state_version != version or fresh_source is None:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "SQL result changed while Python was running")
            checks = {**checked["checks"], "code_hash": f"sha256:{hashlib.sha256(code.encode('utf-8')).hexdigest()}"}
            derived = BusinessDocumentSqlQueryRun.create(
                id=get_uuid(),
                project_id=project_id,
                tenant_id=tenant_id,
                compilation_id=source.compilation_id,
                profile_id=source.profile_id,
                profile_version=source.profile_version,
                profile_name=source.profile_name,
                kind="PYTHON",
                source_run_id=source.id,
                status="READY",
                columns=checked["columns"],
                rows=checked["rows"],
                row_count=checked["row_count"],
                result_bytes=checked["result_bytes"],
                checks=checks,
            )
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    state_version=version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.state_version == version))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed while Python was running")
            response = {"run_id": derived.id, "source_run_id": source.id, "status": "READY", "row_count": derived.row_count, "checks": checks, "state_version": version + 1}
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def run_lookup(cls, tenant_id: str, actor_id: str, project_id: str, source_run_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        version, key = _command(raw, {"source_column", "target_entity_id", "target_key_column_id", "target_value_column_ids"})
        source_column = raw["source_column"]
        target_id = raw["target_entity_id"]
        target_key_id = raw["target_key_column_id"]
        value_ids = raw["target_value_column_ids"]
        if (
            not all(isinstance(value, str) and value for value in (source_column, target_id, target_key_id))
            or not isinstance(value_ids, list)
            or not 1 <= len(value_ids) <= 10
            or any(not isinstance(value, str) or not value for value in value_ids)
            or len(set(value_ids)) != len(value_ids)
        ):
            raise ValidationError("INVALID_SQL_LOOKUP", "Select the source key, catalog table, target key and up to 10 fields")
        request_hash = _stable_hash({"type": "RUN_LOOKUP", "source_run_id": source_run_id, "request": raw})
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
        BusinessDocumentSqlAgentService._require_active(project)
        replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
        if replay is not None:
            return replay
        if project.state_version != version:
            raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before lookup")
        source = _retained_sql_result(project, source_run_id)
        if source is None or source.checks.get("status") != "PASS" or source_column not in source.columns:
            raise ConflictError("SQL_VERIFIED_RESULT_REQUIRED", "Select a key from a verified SQL result")
        compilation, command = _source(project)
        if compilation.id != source.compilation_id:
            raise ConflictError("SQL_RESULT_STALE", "SQL result belongs to another compilation")
        _verify_schema(command, actor_id, is_admin, access_role)
        snapshot = parse_schema_snapshot(command["schema_snapshot"])
        accepted_ids = {item["entity_id"] for item in command["accepted_schema"]}
        table = snapshot.table(target_id)
        if table is None or target_id not in accepted_ids:
            raise ValidationError("SQL_LOOKUP_TARGET_UNACCEPTED", "Choose a table accepted in this project")
        lookup_key = table.column(target_key_id)
        values = [table.column(value_id) for value_id in value_ids]
        if lookup_key is None or any(value is None for value in values):
            raise ValidationError("SQL_LOOKUP_COLUMN_UNAVAILABLE", "Selected lookup fields are outside the accepted schema")
        resolution, profile, connector = _binding(actor_id, is_admin, access_role, command, source.profile_id)
        if profile is None or connector is None or profile.id != source.profile_id or profile.version != source.profile_version:
            raise ConflictError("SQL_PROFILE_STALE", "Lookup source profile changed", {"binding": resolution})
        source_index = source.columns.index(source_column)
        keys: list[Any] = []
        seen_keys: set[str] = set()
        for row in source.rows:
            value = row[source_index]
            if value is None:
                continue
            if not isinstance(value, (str, int, float, bool)):
                raise ValidationError("SQL_LOOKUP_KEY_INVALID", "Lookup keys must be scalar values")
            normalized = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if normalized not in seen_keys:
                seen_keys.add(normalized)
                keys.append(value)
        if len(keys) > profile.max_rows:
            raise ValidationError("SQL_LOOKUP_TOO_MANY_KEYS", "Reduce the result before lookup")
        output_columns = [*source.columns, *(f"lookup_{table.technical_name}_{value.name}" for value in values)]
        lookup_rows: list[list[Any]] = []
        if keys:
            sql = (
                "SELECT t."
                + lookup_key.name
                + " AS lookup_key, "
                + ", ".join(f"t.{value.name} AS value_{index}" for index, value in enumerate(values))
                + f" FROM {table.physical_relation} AS t WHERE t.{lookup_key.name} = ANY(:keys) LIMIT :row_limit"
            )
            parameters = {"keys": keys, "row_limit": profile.max_rows + 1}
            guard_read_only_sql(sql, allowed_tables=[table.physical_relation], parameter_names=list(parameters))
            result = execute_postgres(
                connector.config,
                sql,
                parameters,
                timeout_ms=profile.statement_timeout_ms,
                max_rows=profile.max_rows + 1,
                max_result_bytes=profile.max_result_bytes,
            )
            lookup_rows = result["rows"]
            if len(lookup_rows) > profile.max_rows:
                raise ValidationError("SQL_LOOKUP_RESULT_TOO_LARGE", "Lookup returned more rows than the source profile permits")
        lookup: dict[str, list[Any]] = {}
        for row in lookup_rows:
            normalized = json.dumps(row[0], ensure_ascii=False, default=str)
            if normalized in lookup:
                raise ValidationError("SQL_LOOKUP_DUPLICATE_KEY", "Lookup key is not unique in the selected table")
            lookup[normalized] = row[1:]
        combined = [[*row, *lookup.get(json.dumps(row[source_index], ensure_ascii=False, default=str), [None] * len(values))] for row in source.rows]
        try:
            checked = validate_result(
                {"columns": output_columns, "rows": combined},
                expected_columns=output_columns,
                row_limit=source.row_count,
                max_result_bytes=profile.max_result_bytes,
            )
        except ResultValidationError as exc:
            raise ValidationError("SQL_LOOKUP_RESULT_INVALID", str(exc)) from exc
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            _check_owner_result_quota(tenant_id, new_result=True, additional_bytes=checked["result_bytes"])
            current = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(current.owner_id)
            BusinessDocumentSqlAgentService._require_active(current)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            fresh_source = _retained_sql_result(current, source_run_id)
            if current.state_version != version or fresh_source is None:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "SQL result changed while lookup was running")
            checks = {
                **checked["checks"],
                "lookup_entity_id": target_id,
                "lookup_schema_fingerprint": table.schema_fingerprint,
                "unmatched_rows": sum(json.dumps(row[source_index], ensure_ascii=False, default=str) not in lookup for row in source.rows),
            }
            derived = BusinessDocumentSqlQueryRun.create(
                id=get_uuid(),
                project_id=project_id,
                tenant_id=tenant_id,
                compilation_id=source.compilation_id,
                profile_id=source.profile_id,
                profile_version=source.profile_version,
                profile_name=source.profile_name,
                kind="LOOKUP",
                source_run_id=source.id,
                status="READY",
                columns=checked["columns"],
                rows=checked["rows"],
                row_count=checked["row_count"],
                result_bytes=checked["result_bytes"],
                checks=checks,
            )
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    state_version=version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.state_version == version))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed while lookup was running")
            response = {"run_id": derived.id, "source_run_id": source.id, "status": "READY", "row_count": derived.row_count, "checks": checks, "state_version": version + 1}
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def propose_conclusion(cls, tenant_id: str, actor_id: str, project_id: str, source_run_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        version, key = _command(raw, set())
        request_hash = _stable_hash({"type": "PROPOSE_CONCLUSION", "source_run_id": source_run_id, "request": raw})
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
        BusinessDocumentSqlAgentService._require_active(project)
        replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
        if replay is not None:
            return replay
        if project.state_version != version:
            raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before generating a conclusion")
        source = _retained_sql_result(project, source_run_id)
        if source is None or source.checks.get("status") != "PASS":
            raise ConflictError("SQL_VERIFIED_RESULT_REQUIRED", "A verified SQL result is required for a conclusion")
        proposed = propose_conclusion(
            tenant_id,
            project.source_request,
            source.columns,
            source.rows,
            source.row_count,
            source.checks.get("completeness", "LIMITED"),
        )
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            current = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(current.owner_id)
            BusinessDocumentSqlAgentService._require_active(current)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            fresh_source = _retained_sql_result(current, source_run_id)
            if current.state_version != version or fresh_source is None:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Result changed while the conclusion was generated")
            if (
                BusinessDocumentSqlQueryArtifact.select()
                .where(
                    (BusinessDocumentSqlQueryArtifact.project_id == project_id)
                    & (BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION")
                    & (BusinessDocumentSqlQueryArtifact.source_proposal_id == source_run_id)
                )
                .count()
                >= 5
            ):
                raise ConflictError("SQL_CONCLUSION_QUOTA", "Finish the existing conclusion before generating more drafts")
            revision = (
                BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION")).count() + 1
            )
            proposal_payload = {"status": "DRAFT", "source_run_id": source_run_id, **proposed}
            artifact = BusinessDocumentSqlQueryArtifact.create(
                id=get_uuid(),
                project_id=project_id,
                tenant_id=tenant_id,
                kind="CONCLUSION",
                revision=revision,
                payload=proposal_payload,
                content_hash=_stable_hash(proposal_payload),
                source_proposal_id=source_run_id,
                accepted_by=actor_id,
            )
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    state_version=version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.state_version == version))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed while the conclusion was saved")
            response = {"proposal_id": artifact.id, "text": proposed["text"], "citations": proposed["citations"], "state_version": version + 1}
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def confirm_conclusion(cls, tenant_id: str, actor_id: str, project_id: str, source_run_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        version, key = _command(raw, {"proposal_id", "text"})
        request_hash = _stable_hash({"type": "CONFIRM_CONCLUSION", "source_run_id": source_run_id, "request": raw})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
            BusinessDocumentSqlAgentService._require_active(project)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != version:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before confirming a conclusion")
            source = _retained_sql_result(project, source_run_id)
            proposal = BusinessDocumentSqlQueryArtifact.get_or_none(
                (BusinessDocumentSqlQueryArtifact.id == raw["proposal_id"])
                & (BusinessDocumentSqlQueryArtifact.project_id == project_id)
                & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id)
                & (BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION")
            )
            if source is None or source.checks.get("status") != "PASS" or proposal is None or proposal.payload.get("status") != "DRAFT" or proposal.payload.get("source_run_id") != source_run_id:
                raise ConflictError("SQL_CONCLUSION_STALE", "Conclusion or verified result is no longer current")
            latest = (
                BusinessDocumentSqlQueryArtifact.select()
                .where(
                    (BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id) & (BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION")
                )
                .order_by(BusinessDocumentSqlQueryArtifact.revision.desc())
                .first()
            )
            if latest is None or latest.id != proposal.id:
                raise ConflictError("SQL_CONCLUSION_STALE", "Review the latest conclusion before confirming it")
            try:
                checked = validate_conclusion(raw["text"], proposal.payload["citations"], source.columns[:12], [row[:12] for row in source.rows[:25]], source.row_count)
            except ConclusionValidationError as exc:
                raise ValidationError("SQL_CONCLUSION_CHECK_FAILED", str(exc)) from exc
            if source.checks.get("completeness") == "LIMITED" and not any(word in checked["text"].casefold() for word in ("непол", "огранич", "част")):
                raise ValidationError("SQL_CONCLUSION_CHECK_FAILED", "Conclusion must disclose that the result is limited")
            revision = (
                BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION")).count() + 1
            )
            confirmed = {"status": "CONFIRMED", "source_run_id": source_run_id, "source_proposal_id": proposal.id, "confirmed_by": actor_id, **checked}
            artifact = BusinessDocumentSqlQueryArtifact.create(
                id=get_uuid(),
                project_id=project_id,
                tenant_id=tenant_id,
                kind="CONCLUSION",
                revision=revision,
                payload=confirmed,
                content_hash=_stable_hash(confirmed),
                source_proposal_id=proposal.id,
                accepted_by=actor_id,
            )
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    state_version=version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.state_version == version))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed while the conclusion was confirmed")
            response = {"conclusion_id": artifact.id, "text": checked["text"], "citations": checked["citations"], "state_version": version + 1}
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def complete(cls, tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        version, key = _command(raw, {"run_id"} if isinstance(raw, Mapping) and "run_id" in raw else set())
        request_hash = _stable_hash({"type": "COMPLETE", "request": raw})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
            BusinessDocumentSqlAgentService._require_active(project)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != version or project.operation_state != "IDLE":
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before completion")
            run_id = raw.get("run_id")
            if "run_id" in raw and (not isinstance(run_id, str) or not run_id):
                raise ValidationError("INVALID_SQL_RUN_COMMAND", "run_id must identify a verified result")
            run = None
            if run_id is not None:
                run = _retained_sql_result(project, run_id)
                if run is None or run.checks.get("status") != "PASS":
                    raise ConflictError("SQL_RESULT_MISSING", "A verified result is required")
            if (
                BusinessDocumentSqlQueryRun.select()
                .where(
                    (BusinessDocumentSqlQueryRun.project_id == project_id) & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id) & (BusinessDocumentSqlQueryRun.status.in_(IN_PROGRESS_RUN_STATUSES))
                )
                .exists()
            ):
                raise ConflictError("SQL_RUN_ACTIVE", "Wait for the active query to finish before completing")
            compilation, command = _source(project)
            if run is not None and run.compilation_id != compilation.id:
                raise ConflictError("SQL_RESULT_STALE", "Result belongs to another SQL revision")
            revision = BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")).count() + 1
            confirmed_conclusion = None
            if run is not None:
                confirmed_conclusion = next(
                    (
                        item.payload
                        for item in BusinessDocumentSqlQueryArtifact.select()
                        .where(
                            (BusinessDocumentSqlQueryArtifact.project_id == project_id)
                            & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id)
                            & (BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION")
                        )
                        .order_by(BusinessDocumentSqlQueryArtifact.revision.desc())
                        if item.payload.get("status") == "CONFIRMED" and item.payload.get("source_run_id") == run.id
                    ),
                    None,
                )
            derived_runs = (
                [
                    {"id": item.id, "kind": item.kind, "source_run_id": item.source_run_id, "row_count": item.row_count, "checks": item.checks}
                    for item in BusinessDocumentSqlQueryRun.select().where(
                        (BusinessDocumentSqlQueryRun.project_id == project_id)
                        & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id)
                        & (BusinessDocumentSqlQueryRun.source_run_id == run.id)
                        & (BusinessDocumentSqlQueryRun.status == "READY")
                    )
                ]
                if run
                else []
            )
            payload = {
                "run_id": run.id if run else None,
                "execution_status": "READY" if run else "NOT_EXECUTED",
                "python_status": "READY" if any(item["kind"] == "PYTHON" for item in derived_runs) else "NO_SAVED_RESULT" if run else "NOT_EXECUTED",
                "source_artifact_ids": compilation.payload.get("source_artifact_ids", []),
                "sql": compilation.payload["result"]["sql"],
                "requirements": command["accepted_requirements"],
                "snapshot_fingerprint": compilation.payload["result"]["snapshot_fingerprint"],
                "guard": compilation.payload["result"]["guard"],
                "profile_id": run.profile_id if run else None,
                "profile_version": run.profile_version if run else None,
                "row_count": run.row_count if run else None,
                "duration_ms": run.duration_ms if run else None,
                "result_bytes": run.result_bytes if run else None,
                "checks": run.checks if run else {"status": "NOT_EXECUTED"},
                "confirmed_conclusion": confirmed_conclusion,
                "derived_runs": derived_runs,
            }
            document = BusinessDocumentSqlQueryArtifact.create(
                id=get_uuid(),
                project_id=project_id,
                tenant_id=tenant_id,
                kind="DOCUMENT",
                revision=revision,
                payload=payload,
                content_hash=_stable_hash(payload),
                source_proposal_id=compilation.source_proposal_id,
                accepted_by=actor_id,
            )
            if run is not None:
                BusinessDocumentSqlQueryRun.update(rows=[], status="PURGED").where(
                    (BusinessDocumentSqlQueryRun.project_id == project_id) & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id) & (BusinessDocumentSqlQueryRun.status == "READY")
                ).execute()
                _purge_conclusion_drafts(project_id, tenant_id, run.id)
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    state_version=version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.state_version == version))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed during completion")
            response = {"document_id": document.id, "revision": revision, "document": payload, "rows_status": "PURGED" if run else "NOT_EXECUTED", "state_version": version + 1}
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response
