"""Durable application service for the SQL document-constructor agent cycle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from peewee import IntegrityError

from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.db.db_models import (
    BusinessDocumentJob,
    BusinessDocumentSqlAgentCommand,
    BusinessDocumentSqlAgentProposal,
    BusinessDocumentSqlQueryArtifact,
    BusinessDocumentSqlQueryProject,
    BusinessDocumentSqlQueryRun,
)
from business_documents.application.errors import BusinessDocumentError, ConflictError, ValidationError
from business_documents.domain.access import BusinessDocumentRole
from business_documents.sql_query.agent_cycle import (
    AgentCycleConflict,
    AgentCycleValidationError,
    ProjectAgentState,
    next_agent_kind,
    parse_request_agent_command,
    require_next_agent,
)
from business_documents.sql_query.project_compilation import ProjectCompilationError, build_project_compile_command
from business_documents.sql_query.query_planning import QueryPlanValidationError, parse_plan_query_command
from business_documents.sql_query.query_specification import QuerySpecificationValidationError, SqlGuardError, compile_query_payload
from business_documents.sql_query.requirements_analysis import (
    RequirementsAnalysisValidationError,
    accept_requirements_proposal,
)
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp

_JOB_PREFIX = "SQL_AGENT_"
_PROJECT_FIELDS = {"schema_version", "title", "source_request", "locale"}
_DECISION_FIELDS = {
    "schema_version",
    "expected_state_version",
    "idempotency_key",
    "decision",
    "artifact_payload",
}
_REQUEST_PAYLOAD_FIELDS = {
    "REQUIREMENTS": {"locale"},
    "SCHEMA": {"locale", "terms"},
    "QUERY": {"locale"},
}


def _stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("INVALID_SQL_AGENT_REQUEST", f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise ValidationError("INVALID_SQL_AGENT_REQUEST", f"{field} exceeds {maximum} characters")
    return result


def _closed(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValidationError(
            "INVALID_SQL_AGENT_REQUEST",
            f"Unknown {field} fields: {', '.join(sorted(unknown))}",
        )


def _requirements_text(payload: Mapping[str, Any]) -> str:
    requirements = payload.get("requirements")
    if not isinstance(requirements, list):
        raise ConflictError("SQL_REQUIREMENTS_ARTIFACT_INVALID", "Accepted requirements artifact is invalid")
    statements = [str(item.get("statement") or "").strip() for item in requirements if isinstance(item, Mapping)]
    result = "\n".join(f"- {statement}" for statement in statements if statement)
    if not result:
        raise ConflictError("SQL_REQUIREMENTS_ARTIFACT_INVALID", "Accepted requirements artifact is empty")
    return result


class BusinessDocumentSqlAgentService:
    """Own projects, immutable proposals/artifacts, and durable job commands."""

    @classmethod
    def model_tables(cls):
        return (
            BusinessDocumentSqlQueryProject,
            BusinessDocumentSqlQueryArtifact,
            BusinessDocumentSqlQueryRun,
            BusinessDocumentSqlAgentProposal,
            BusinessDocumentSqlAgentCommand,
            BusinessDocumentJob,
        )

    @classmethod
    def create_project(
        cls,
        tenant_id: str,
        actor_id: str,
        raw: object,
        is_admin: bool = False,
        access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR,
    ) -> dict[str, Any]:
        BusinessDocumentAccess(actor_id, access_role, is_admin).require_create()
        if not isinstance(raw, Mapping):
            raise ValidationError("INVALID_SQL_AGENT_PROJECT", "Request body must be a JSON object")
        _closed(raw, _PROJECT_FIELDS, "project")
        if raw.get("schema_version") != "1":
            raise ValidationError("INVALID_SQL_AGENT_PROJECT", "schema_version is unsupported")
        locale = raw.get("locale", "ru")
        if locale not in {"ru", "en"}:
            raise ValidationError("INVALID_SQL_AGENT_PROJECT", "locale must be ru or en")
        project = BusinessDocumentSqlQueryProject.create(
            id=get_uuid(),
            tenant_id=_text(tenant_id, "tenant_id", 32),
            owner_id=_text(actor_id, "actor_id", 32),
            title=_text(raw.get("title"), "title", 255),
            source_request=_text(raw.get("source_request"), "source_request", 20_000),
            locale=locale,
        )
        return cls._project(project)

    @classmethod
    def revise_question(
        cls,
        tenant_id: str,
        actor_id: str,
        project_id: str,
        raw: object,
        is_admin: bool = False,
        access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR,
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValidationError("INVALID_SQL_QUESTION", "Request body must be an object")
        _closed(raw, {"schema_version", "expected_state_version", "idempotency_key", "source_request"}, "question revision")
        version = raw.get("expected_state_version")
        if raw.get("schema_version") != "1" or isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValidationError("INVALID_SQL_QUESTION", "A current project version is required")
        key = _text(raw.get("idempotency_key"), "idempotency_key", 128)
        question = _text(raw.get("source_request"), "source_request", 20_000)
        request_hash = _stable_hash({"type": "REVISE_QUESTION", "request": raw})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
            replay = cls._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != version or project.stage == "COMPLETE" or project.operation_state == "RUNNING":
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Question cannot be changed in the current project state")
            now = current_timestamp()
            BusinessDocumentSqlAgentProposal.update(status="REJECTED", decided_by=actor_id, decided_at=now).where(
                (BusinessDocumentSqlAgentProposal.project_id == project_id) & (BusinessDocumentSqlAgentProposal.tenant_id == tenant_id) & (BusinessDocumentSqlAgentProposal.status == "PENDING")
            ).execute()
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    source_request=question,
                    requirements_artifact_id=None,
                    schema_artifact_id=None,
                    query_artifact_id=None,
                    stage="REQUIREMENTS",
                    operation_state="IDLE",
                    current_job_id=None,
                    last_error=None,
                    state_version=version + 1,
                    update_time=now,
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.tenant_id == tenant_id) & (BusinessDocumentSqlQueryProject.state_version == version))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Question changed while it was being saved")
            response = cls._project(BusinessDocumentSqlQueryProject.get_by_id(project_id))
            cls._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def list_projects(
        cls,
        tenant_id: str,
        actor_id: str,
        is_admin: bool = False,
        access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR,
    ) -> list[dict[str, Any]]:
        access = BusinessDocumentAccess(actor_id, access_role, is_admin)
        query = BusinessDocumentSqlQueryProject.select().where(BusinessDocumentSqlQueryProject.tenant_id == tenant_id)
        if not access.capabilities()["edit_all"]:
            query = query.where(BusinessDocumentSqlQueryProject.owner_id == actor_id)
        return [cls._project(project, include_payloads=False) for project in query.order_by(BusinessDocumentSqlQueryProject.update_time.desc())]

    @classmethod
    def get_project(
        cls,
        tenant_id: str,
        actor_id: str,
        project_id: str,
        is_admin: bool = False,
        access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR,
    ) -> dict[str, Any]:
        project = cls._get_project(tenant_id, project_id)
        BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
        return cls._project(project)

    @classmethod
    def compile_project(
        cls,
        tenant_id: str,
        actor_id: str,
        project_id: str,
        raw: object,
        is_admin: bool = False,
        access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR,
    ) -> dict[str, Any]:
        """Compile accepted server artifacts and retain the exact revision."""
        if not isinstance(raw, Mapping):
            raise ValidationError("INVALID_SQL_PROJECT_COMPILATION", "Request body must be a JSON object")
        _closed(raw, {"schema_version", "expected_state_version", "idempotency_key"}, "compilation")
        if raw.get("schema_version") != "1":
            raise ValidationError("INVALID_SQL_PROJECT_COMPILATION", "schema_version is unsupported")
        expected_version = raw.get("expected_state_version")
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ValidationError("INVALID_SQL_PROJECT_COMPILATION", "expected_state_version must be a positive integer")
        key = _text(raw.get("idempotency_key"), "idempotency_key", 128)
        request_hash = _stable_hash({"type": "COMPILE_PROJECT", "request": raw})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
            replay = cls._command_replay(tenant_id, project.id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != expected_version or project.stage != "COMPLETE" or project.operation_state != "IDLE":
                raise ConflictError("SQL_PROJECT_COMPILATION_CONFLICT", "Project is not ready at the expected version")
            ids = (project.requirements_artifact_id, project.schema_artifact_id, project.query_artifact_id)
            if any(value is None for value in ids):
                raise ConflictError("SQL_PROJECT_ARTIFACT_MISSING", "Accepted project artifacts are incomplete")
            artifacts = [BusinessDocumentSqlQueryArtifact.get_by_id(value) for value in ids]
            if any(artifact.project_id != project.id or artifact.tenant_id != tenant_id for artifact in artifacts):
                raise ConflictError("SQL_PROJECT_ARTIFACT_MISMATCH", "Accepted artifacts do not belong to this project")
            try:
                command = build_project_compile_command(*(artifact.payload for artifact in artifacts))
                compiled = compile_query_payload(command)
            except (ProjectCompilationError, QuerySpecificationValidationError) as exc:
                raise ValidationError("INVALID_SQL_PROJECT_COMPILATION", str(exc)) from exc
            except SqlGuardError as exc:
                raise ValidationError("SQL_QUERY_BLOCKED", "SQL не прошёл read-only проверку.", {"reason": str(exc)}) from exc
            if compiled.get("status") != "READY" or compiled.get("guard", {}).get("status") != "PASS":
                return {"compilation": compiled, "project": cls._project(project)}
            revision = (
                BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project.id) & (BusinessDocumentSqlQueryArtifact.kind == "COMPILATION")).count() + 1
            )
            payload = {
                "result": compiled,
                "source_artifact_ids": list(ids),
                "source_state_version": project.state_version,
            }
            artifact = BusinessDocumentSqlQueryArtifact.create(
                id=get_uuid(),
                project_id=project.id,
                tenant_id=tenant_id,
                kind="COMPILATION",
                revision=revision,
                payload=payload,
                content_hash=_stable_hash(payload),
                source_proposal_id=artifacts[2].source_proposal_id,
                accepted_by=actor_id,
            )
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    state_version=project.state_version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project.id) & (BusinessDocumentSqlQueryProject.state_version == project.state_version))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_AGENT_VERSION_CONFLICT", "Project changed while SQL was compiled")
            response = {"compilation_id": artifact.id, "compilation": compiled, "project": cls._project(BusinessDocumentSqlQueryProject.get_by_id(project.id))}
            cls._record_command(tenant_id, project.id, key, request_hash, response)
            return response

    @classmethod
    def save_manual_query(
        cls,
        tenant_id: str,
        actor_id: str,
        project_id: str,
        raw: object,
        is_admin: bool = False,
        access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR,
    ) -> dict[str, Any]:
        """Accept expert SQL as another query artifact under the same compiler and run gate."""
        if not isinstance(raw, Mapping):
            raise ValidationError("INVALID_SQL_MANUAL_QUERY", "Request body must be an object")
        _closed(raw, {"schema_version", "expected_state_version", "idempotency_key", "sql", "parameters", "confirmed_alignment"}, "manual query")
        version = raw.get("expected_state_version")
        if raw.get("schema_version") != "1" or isinstance(version, bool) or not isinstance(version, int) or version < 1 or raw.get("confirmed_alignment") is not True:
            raise ValidationError("INVALID_SQL_MANUAL_QUERY", "Version and explicit task alignment confirmation are required")
        key = _text(raw.get("idempotency_key"), "idempotency_key", 128)
        request_hash = _stable_hash({"type": "SAVE_MANUAL_QUERY", "request": raw})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
            replay = cls._command_replay(tenant_id, project.id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != version or project.stage not in {"QUERY", "COMPLETE"} or project.operation_state == "RUNNING":
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project is not ready for expert SQL at the expected version")
            if (
                BusinessDocumentSqlQueryRun.select()
                .where(
                    (BusinessDocumentSqlQueryRun.project_id == project.id)
                    & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id)
                    & (BusinessDocumentSqlQueryRun.status.in_(("QUEUED", "RUNNING", "CANCEL_REQUESTED", "READY")))
                )
                .exists()
            ):
                raise ConflictError("SQL_RESULT_ACTIVE", "Finish or cancel active results before editing SQL")
            requirements = cls._artifact(project.requirements_artifact_id, "REQUIREMENTS")
            schema = cls._artifact(project.schema_artifact_id, "SCHEMA")
            query_payload = {"mode": "manual", "sql": raw.get("sql"), "parameters": raw.get("parameters"), "confirmed_alignment": True}
            try:
                command = build_project_compile_command(requirements.payload, schema.payload, query_payload)
                compiled = compile_query_payload(command)
            except (ProjectCompilationError, QuerySpecificationValidationError) as exc:
                raise ValidationError("INVALID_SQL_MANUAL_QUERY", str(exc)) from exc
            except SqlGuardError as exc:
                raise ValidationError("SQL_QUERY_BLOCKED", "Manual SQL did not pass the read-only catalog guard", {"reason": str(exc)}) from exc
            if compiled.get("status") != "READY" or compiled.get("guard", {}).get("status") != "PASS":
                raise ValidationError("INVALID_SQL_MANUAL_QUERY", "Accepted schema must be current before saving manual SQL", {"blocking_issues": compiled.get("blocking_issues")})
            BusinessDocumentSqlAgentProposal.update(status="REJECTED", decided_by=actor_id, decided_at=current_timestamp()).where(
                (BusinessDocumentSqlAgentProposal.project_id == project.id) & (BusinessDocumentSqlAgentProposal.status == "PENDING")
            ).execute()
            query_id = get_uuid()
            query_revision = (
                BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project.id) & (BusinessDocumentSqlQueryArtifact.kind == "QUERY")).count() + 1
            )
            query = BusinessDocumentSqlQueryArtifact.create(
                id=query_id,
                project_id=project.id,
                tenant_id=tenant_id,
                kind="QUERY",
                revision=query_revision,
                payload=query_payload,
                content_hash=_stable_hash(query_payload),
                source_proposal_id=f"manual:{query_id[:25]}",
                accepted_by=actor_id,
            )
            compilation_payload = {
                "result": compiled,
                "source_artifact_ids": [requirements.id, schema.id, query.id],
                "source_state_version": version,
            }
            compilation_revision = (
                BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project.id) & (BusinessDocumentSqlQueryArtifact.kind == "COMPILATION")).count() + 1
            )
            artifact = BusinessDocumentSqlQueryArtifact.create(
                id=get_uuid(),
                project_id=project.id,
                tenant_id=tenant_id,
                kind="COMPILATION",
                revision=compilation_revision,
                payload=compilation_payload,
                content_hash=_stable_hash(compilation_payload),
                source_proposal_id=query.source_proposal_id,
                accepted_by=actor_id,
            )
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    query_artifact_id=query.id,
                    stage="COMPLETE",
                    operation_state="IDLE",
                    current_job_id=None,
                    last_error=None,
                    state_version=version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project.id) & (BusinessDocumentSqlQueryProject.state_version == version))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed while expert SQL was saved")
            response = {"compilation_id": artifact.id, "compilation": compiled, "project": cls._project(BusinessDocumentSqlQueryProject.get_by_id(project.id))}
            cls._record_command(tenant_id, project.id, key, request_hash, response)
            return response

    @classmethod
    def request_agent(
        cls,
        tenant_id: str,
        actor_id: str,
        project_id: str,
        raw: object,
        is_admin: bool = False,
        access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR,
    ) -> dict[str, Any]:
        try:
            command = parse_request_agent_command(raw)
        except AgentCycleValidationError as exc:
            raise ValidationError("INVALID_SQL_AGENT_REQUEST", str(exc)) from exc
        request_hash = _stable_hash({"type": "REQUEST_AGENT", "request": raw})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
            replay = cls._command_replay(tenant_id, project.id, command.idempotency_key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != command.expected_state_version:
                raise ConflictError(
                    "SQL_AGENT_VERSION_CONFLICT",
                    "Project state changed; reload before retrying",
                    {"current_state_version": project.state_version},
                )
            state = ProjectAgentState(
                stage=project.stage,
                operation_state=project.operation_state,
                requirements_artifact_id=project.requirements_artifact_id,
                schema_artifact_id=project.schema_artifact_id,
                query_artifact_id=project.query_artifact_id,
            )
            try:
                require_next_agent(state, command.kind)
            except AgentCycleConflict as exc:
                raise ConflictError("SQL_AGENT_STAGE_CONFLICT", str(exc)) from exc
            job_request = cls._job_request(project, command.kind, command.payload)
            new_version = project.state_version + 1
            job_id = get_uuid()
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    operation_state="RUNNING",
                    state_version=new_version,
                    current_job_id=job_id,
                    last_error=None,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where(
                    (BusinessDocumentSqlQueryProject.id == project.id)
                    & (BusinessDocumentSqlQueryProject.state_version == project.state_version)
                    & (BusinessDocumentSqlQueryProject.operation_state == "IDLE")
                )
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_AGENT_VERSION_CONFLICT", "Project state changed; reload before retrying")
            BusinessDocumentJob.create(
                id=job_id,
                document_id=project.id,
                tenant_id=tenant_id,
                job_type=f"{_JOB_PREFIX}{command.kind}",
                dedupe_key=_stable_hash({"project_id": project.id, "kind": command.kind, "state_version": new_version}),
                source_state_version=new_version,
                payload={
                    "schema_version": "1",
                    "project_id": project.id,
                    "actor_id": actor_id,
                    "is_admin": bool(is_admin),
                    "access_role": str(access_role),
                    "request": job_request,
                },
                available_at=current_timestamp(),
                correlation_id=get_uuid(),
            )
            response = cls._project(BusinessDocumentSqlQueryProject.get_by_id(project.id))
            cls._record_command(tenant_id, project.id, command.idempotency_key, request_hash, response)
            return response

    @classmethod
    def decide_proposal(
        cls,
        tenant_id: str,
        actor_id: str,
        project_id: str,
        proposal_id: str,
        raw: object,
        is_admin: bool = False,
        access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR,
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValidationError("INVALID_SQL_AGENT_DECISION", "Request body must be a JSON object")
        _closed(raw, _DECISION_FIELDS, "decision")
        if raw.get("schema_version") != "1":
            raise ValidationError("INVALID_SQL_AGENT_DECISION", "schema_version is unsupported")
        expected_version = raw.get("expected_state_version")
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ValidationError("INVALID_SQL_AGENT_DECISION", "expected_state_version must be a positive integer")
        idempotency_key = _text(raw.get("idempotency_key"), "idempotency_key", 128)
        decision = _text(raw.get("decision"), "decision", 16).upper()
        if decision not in {"ACCEPT", "REJECT"}:
            raise ValidationError("INVALID_SQL_AGENT_DECISION", "decision must be ACCEPT or REJECT")
        request_hash = _stable_hash({"type": "DECIDE_PROPOSAL", "proposal_id": proposal_id, "request": raw})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._get_project(tenant_id, project_id)
            BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
            replay = cls._command_replay(tenant_id, project.id, idempotency_key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != expected_version:
                raise ConflictError(
                    "SQL_AGENT_VERSION_CONFLICT",
                    "Project state changed; reload before retrying",
                    {"current_state_version": project.state_version},
                )
            proposal = BusinessDocumentSqlAgentProposal.get_or_none(
                (BusinessDocumentSqlAgentProposal.id == proposal_id) & (BusinessDocumentSqlAgentProposal.project_id == project.id) & (BusinessDocumentSqlAgentProposal.tenant_id == tenant_id)
            )
            if proposal is None:
                raise BusinessDocumentError("SQL_AGENT_PROPOSAL_NOT_FOUND", "SQL agent proposal not found", 404)
            if proposal.status != "PENDING" or project.operation_state != "REVIEW":
                raise ConflictError("SQL_AGENT_PROPOSAL_ALREADY_DECIDED", "This proposal is no longer pending")
            now = current_timestamp()
            if decision == "ACCEPT":
                accepted = cls._accepted_payload(project, proposal, raw.get("artifact_payload"))
                revision = (
                    BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project.id) & (BusinessDocumentSqlQueryArtifact.kind == proposal.kind)).count() + 1
                )
                artifact_id = get_uuid()
                BusinessDocumentSqlQueryArtifact.create(
                    id=artifact_id,
                    project_id=project.id,
                    tenant_id=tenant_id,
                    kind=proposal.kind,
                    revision=revision,
                    payload=accepted,
                    content_hash=_stable_hash(accepted),
                    source_proposal_id=proposal.id,
                    accepted_by=actor_id,
                )
                project_values = cls._accepted_project_values(project, proposal.kind, artifact_id)
            else:
                project_values = {"operation_state": "IDLE", "current_job_id": None, "last_error": None}
            new_version = project.state_version + 1
            project_values.update(
                state_version=new_version,
                update_time=now,
                update_date=datetime.now(),
            )
            changed = (
                BusinessDocumentSqlQueryProject.update(**project_values)
                .where(
                    (BusinessDocumentSqlQueryProject.id == project.id)
                    & (BusinessDocumentSqlQueryProject.state_version == project.state_version)
                    & (BusinessDocumentSqlQueryProject.operation_state == "REVIEW")
                )
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_AGENT_VERSION_CONFLICT", "Project state changed; reload before retrying")
            proposal.status = "ACCEPTED" if decision == "ACCEPT" else "REJECTED"
            proposal.decided_by = actor_id
            proposal.decided_at = now
            proposal.save()
            response = cls._project(BusinessDocumentSqlQueryProject.get_by_id(project.id))
            cls._record_command(tenant_id, project.id, idempotency_key, request_hash, response)
            return response

    @classmethod
    def complete_job(cls, job: BusinessDocumentJob, worker_id: str, lease_token: str, result: Mapping[str, Any]) -> None:
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            current_job = BusinessDocumentJob.get_by_id(job.id)
            cls._require_job_lease(current_job, worker_id, lease_token)
            project = cls._get_project(job.tenant_id, job.document_id)
            if project.current_job_id != job.id or project.state_version != job.source_state_version:
                cls._finish_job(current_job, "STALE", result=result)
                return
            proposal_payload = result.get("proposal")
            if proposal_payload is None and current_job.job_type == "SQL_AGENT_SCHEMA":
                proposal_payload = result
            if not isinstance(proposal_payload, Mapping):
                error = {
                    "code": "SQL_AGENT_NO_PROPOSAL",
                    "message": str(result.get("warning") or "Agent did not produce a proposal")[:1000],
                }
                cls._update_project_after_job(project, operation_state="IDLE", last_error=error)
                cls._finish_job(current_job, "COMPLETED", result=result)
                return
            kind = current_job.job_type.removeprefix(_JOB_PREFIX)
            proposal = {
                "schema_version": "1",
                "kind": kind,
                "agent_result": dict(result),
            }
            BusinessDocumentSqlAgentProposal.create(
                id=get_uuid(),
                project_id=project.id,
                tenant_id=project.tenant_id,
                job_id=current_job.id,
                kind=kind,
                source_state_version=project.state_version,
                payload=proposal,
                content_hash=_stable_hash(proposal),
            )
            cls._update_project_after_job(project, operation_state="REVIEW", last_error=None)
            cls._finish_job(current_job, "COMPLETED", result=result)

    @classmethod
    def fail_job(cls, job: BusinessDocumentJob, worker_id: str, lease_token: str, error: Mapping[str, Any]) -> None:
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            current_job = BusinessDocumentJob.get_by_id(job.id)
            cls._require_job_lease(current_job, worker_id, lease_token)
            project = cls._get_project(job.tenant_id, job.document_id)
            if project.current_job_id == job.id:
                cls._update_project_after_job(project, operation_state="IDLE", last_error=dict(error))
            cls._finish_job(current_job, "DEAD", error=dict(error))

    @staticmethod
    def _get_project(tenant_id: str, project_id: str) -> BusinessDocumentSqlQueryProject:
        project = BusinessDocumentSqlQueryProject.get_or_none((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.tenant_id == tenant_id))
        if project is None:
            raise BusinessDocumentError("SQL_AGENT_PROJECT_NOT_FOUND", "SQL query project not found", 404)
        return project

    @classmethod
    def _job_request(cls, project: BusinessDocumentSqlQueryProject, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        _closed(payload, _REQUEST_PAYLOAD_FIELDS[kind], "agent payload")
        locale = payload.get("locale", project.locale)
        if locale not in {"ru", "en"}:
            raise ValidationError("INVALID_SQL_AGENT_REQUEST", "locale must be ru or en")
        if kind == "REQUIREMENTS":
            return {"schema_version": "1", "locale": locale, "source_request": project.source_request}
        requirements = cls._artifact(project.requirements_artifact_id, "REQUIREMENTS")
        accepted_requirements = _requirements_text(requirements.payload)
        if kind == "SCHEMA":
            terms = payload.get("terms")
            if not isinstance(terms, list) or not 1 <= len(terms) <= 8:
                raise ValidationError("INVALID_SQL_AGENT_REQUEST", "terms must contain from 1 to 8 items")
            normalized_terms = [_text(term, "term", 200) for term in terms]
            if len({term.casefold() for term in normalized_terms}) != len(normalized_terms):
                raise ValidationError("INVALID_SQL_AGENT_REQUEST", "terms must be unique")
            return {"requirements": accepted_requirements, "terms": normalized_terms, "locale": locale}
        schema = cls._artifact(project.schema_artifact_id, "SCHEMA")
        return {
            "schema_version": "1",
            "locale": locale,
            "accepted_requirements": accepted_requirements,
            "schema_snapshot": schema.payload["schema_snapshot"],
            "accepted_schema": schema.payload["accepted_schema"],
        }

    @classmethod
    def _accepted_payload(
        cls,
        project: BusinessDocumentSqlQueryProject,
        proposal: BusinessDocumentSqlAgentProposal,
        supplied: Any,
    ) -> dict[str, Any]:
        result = proposal.payload.get("agent_result")
        if not isinstance(result, Mapping):
            raise ConflictError("SQL_AGENT_PROPOSAL_INVALID", "Stored agent proposal is invalid")
        if proposal.kind == "REQUIREMENTS":
            value = result.get("proposal")
            if not isinstance(value, Mapping):
                raise ConflictError("SQL_AGENT_PROPOSAL_INVALID", "Stored requirements proposal is invalid")
            answers = None
            if supplied is not None:
                if not isinstance(supplied, Mapping):
                    raise ValidationError("INVALID_SQL_AGENT_DECISION", "Requirements artifact_payload must be an object")
                _closed(supplied, {"answers"}, "requirements decision")
                answers = supplied.get("answers")
            try:
                accepted = accept_requirements_proposal(value, answers)
            except RequirementsAnalysisValidationError as exc:
                raise ValidationError("INVALID_SQL_AGENT_DECISION", str(exc)) from exc
            _requirements_text(accepted)
            return accepted
        if proposal.kind == "SCHEMA":
            if not isinstance(supplied, Mapping):
                raise ValidationError(
                    "INVALID_SQL_AGENT_DECISION",
                    "Schema acceptance requires artifact_payload with schema_snapshot and accepted_schema",
                )
            requirements = cls._artifact(project.requirements_artifact_id, "REQUIREMENTS")
            try:
                parse_plan_query_command(
                    {
                        "schema_version": "1",
                        "locale": project.locale,
                        "accepted_requirements": _requirements_text(requirements.payload),
                        "schema_snapshot": supplied.get("schema_snapshot"),
                        "accepted_schema": supplied.get("accepted_schema"),
                    },
                    tenant_id=project.tenant_id,
                )
            except QueryPlanValidationError as exc:
                raise ValidationError("INVALID_SQL_AGENT_DECISION", str(exc)) from exc
            _closed(supplied, {"schema_snapshot", "accepted_schema"}, "schema artifact")
            return dict(supplied)
        value = result.get("proposal")
        if not isinstance(value, Mapping):
            raise ConflictError("SQL_AGENT_PROPOSAL_INVALID", "Stored query proposal is invalid")
        if not isinstance(supplied, Mapping):
            raise ValidationError("INVALID_SQL_AGENT_DECISION", "Query acceptance requires explicit JOIN and filter confirmations")
        _closed(supplied, {"confirmed_join_ids", "confirmed_filter_ids"}, "query decision")
        accepted = dict(value)
        for kind, field in (("joins", "confirmed_join_ids"), ("filters", "confirmed_filter_ids")):
            items = value.get(kind)
            ids = supplied.get(field)
            if not isinstance(items, list) or not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
                raise ValidationError("INVALID_SQL_AGENT_DECISION", f"{field} must list the confirmed IDs")
            expected = [item.get("id") for item in items if isinstance(item, Mapping)]
            if len(expected) != len(items) or len(ids) != len(set(ids)) or set(ids) != set(expected):
                raise ValidationError("INVALID_SQL_AGENT_DECISION", f"{field} must confirm every proposed item exactly once")
            accepted[kind] = [{**item, "decision": "user", "confirmed": True} for item in items]
        return accepted

    @staticmethod
    def _accepted_project_values(project: BusinessDocumentSqlQueryProject, kind: str, artifact_id: str) -> dict[str, Any]:
        if kind == "REQUIREMENTS":
            return {
                "requirements_artifact_id": artifact_id,
                "schema_artifact_id": None,
                "query_artifact_id": None,
                "stage": "SCHEMA",
                "operation_state": "IDLE",
                "current_job_id": None,
                "last_error": None,
            }
        if kind == "SCHEMA":
            return {
                "schema_artifact_id": artifact_id,
                "query_artifact_id": None,
                "stage": "QUERY",
                "operation_state": "IDLE",
                "current_job_id": None,
                "last_error": None,
            }
        return {
            "query_artifact_id": artifact_id,
            "stage": "COMPLETE",
            "operation_state": "IDLE",
            "current_job_id": None,
            "last_error": None,
        }

    @staticmethod
    def _artifact(artifact_id: str | None, kind: str) -> BusinessDocumentSqlQueryArtifact:
        artifact = BusinessDocumentSqlQueryArtifact.get_or_none(BusinessDocumentSqlQueryArtifact.id == artifact_id)
        if artifact is None or artifact.kind != kind:
            raise ConflictError("SQL_AGENT_ARTIFACT_MISSING", f"Accepted {kind.lower()} artifact is required")
        return artifact

    @classmethod
    def _project(cls, project: BusinessDocumentSqlQueryProject, *, include_payloads: bool = True) -> dict[str, Any]:
        state = ProjectAgentState(
            stage=project.stage,
            operation_state=project.operation_state,
            requirements_artifact_id=project.requirements_artifact_id,
            schema_artifact_id=project.schema_artifact_id,
            query_artifact_id=project.query_artifact_id,
        )
        current_job = BusinessDocumentJob.get_or_none(BusinessDocumentJob.id == project.current_job_id)
        pending = (
            BusinessDocumentSqlAgentProposal.select()
            .where((BusinessDocumentSqlAgentProposal.project_id == project.id) & (BusinessDocumentSqlAgentProposal.status == "PENDING"))
            .order_by(BusinessDocumentSqlAgentProposal.create_time.desc())
            .first()
        )
        artifact_ids = {
            "requirements": project.requirements_artifact_id,
            "schema": project.schema_artifact_id,
            "query": project.query_artifact_id,
        }
        artifacts = None
        compilation = None
        latest_compilation = (
            BusinessDocumentSqlQueryArtifact.select()
            .where(
                (BusinessDocumentSqlQueryArtifact.project_id == project.id)
                & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id)
                & (BusinessDocumentSqlQueryArtifact.kind == "COMPILATION")
            )
            .order_by(BusinessDocumentSqlQueryArtifact.revision.desc())
            .first()
        )
        compilation_current = latest_compilation is not None and latest_compilation.payload.get("source_artifact_ids") == list(artifact_ids.values())
        runs = list(
            BusinessDocumentSqlQueryRun.select()
            .where((BusinessDocumentSqlQueryRun.project_id == project.id) & (BusinessDocumentSqlQueryRun.tenant_id == project.tenant_id) & (BusinessDocumentSqlQueryRun.kind == "SQL"))
            .order_by(BusinessDocumentSqlQueryRun.create_time.desc(), BusinessDocumentSqlQueryRun.id.desc())
        )
        run = runs[0] if runs else None
        ready_run = next((item for item in runs if item.status == "READY"), None)
        ready_run_ids = [item.id for item in runs if item.status == "READY"]
        run_summaries = [
            {
                "id": run.id,
                "status": run.status,
                "row_count": run.row_count,
                "duration_ms": run.duration_ms,
                "result_bytes": run.result_bytes,
                "columns": run.columns,
                "compilation_id": run.compilation_id,
                "checks": run.checks,
                "error": run.error,
            }
            for run in runs
        ]
        latest_run = run_summaries[0] if run_summaries else None
        derived_runs = (
            [
                {"id": item.id, "kind": item.kind, "source_run_id": item.source_run_id, "status": item.status, "row_count": item.row_count, "checks": item.checks}
                for item in BusinessDocumentSqlQueryRun.select()
                .where(
                    (BusinessDocumentSqlQueryRun.project_id == project.id)
                    & (BusinessDocumentSqlQueryRun.tenant_id == project.tenant_id)
                    & (BusinessDocumentSqlQueryRun.source_run_id.in_(ready_run_ids))
                )
                .order_by(BusinessDocumentSqlQueryRun.create_time.desc())
            ]
            if ready_run_ids
            else []
        )
        documents = list(
            BusinessDocumentSqlQueryArtifact.select()
            .where(
                (BusinessDocumentSqlQueryArtifact.project_id == project.id) & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id) & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")
            )
            .order_by(BusinessDocumentSqlQueryArtifact.revision.desc())
        )
        latest_document = documents[0] if documents else None
        document = {"id": latest_document.id, "revision": latest_document.revision, "payload": latest_document.payload if include_payloads else None} if latest_document is not None else None
        latest_conclusion = (
            BusinessDocumentSqlQueryArtifact.select()
            .where(
                (BusinessDocumentSqlQueryArtifact.project_id == project.id)
                & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id)
                & (BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION")
            )
            .order_by(BusinessDocumentSqlQueryArtifact.revision.desc())
            .first()
        )
        conclusions_by_run = {}
        if include_payloads and ready_run_ids:
            for item in (
                BusinessDocumentSqlQueryArtifact.select()
                .where(
                    (BusinessDocumentSqlQueryArtifact.project_id == project.id)
                    & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id)
                    & (BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION")
                )
                .order_by(BusinessDocumentSqlQueryArtifact.revision.desc())
            ):
                source_run_id = item.payload.get("source_run_id")
                if source_run_id in ready_run_ids and source_run_id not in conclusions_by_run:
                    conclusions_by_run[source_run_id] = {"id": item.id, "payload": item.payload}
        if include_payloads:
            artifacts = {name: BusinessDocumentSqlQueryArtifact.get_by_id(artifact_id).payload if artifact_id else None for name, artifact_id in artifact_ids.items()}
            if compilation_current:
                compilation = {"id": latest_compilation.id, "result": latest_compilation.payload.get("result")}
        next_action = (
            "WAIT"
            if project.operation_state == "RUNNING" or (run is not None and run.status in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"})
            else "CONFIRM_DECISIONS"
            if project.operation_state == "REVIEW"
            else "VIEW_RESULT"
            if ready_run is not None
            else "OPEN_DOCUMENT"
            if latest_document is not None
            else "COMPILE"
            if project.stage == "COMPLETE" and not compilation_current
            else "EXECUTE"
            if project.stage == "COMPLETE"
            else "CONTINUE"
        )
        blockers = []
        if project.last_error:
            blockers.append({"code": project.last_error.get("code", "SQL_AGENT_FAILED"), "message": project.last_error.get("message", "Analysis failed"), "action": "RETRY_ANALYSIS"})
        if run is not None and run.status == "FAILED" and run.error:
            blockers.append({"code": run.error.get("code", "SQL_RUN_FAILED"), "message": run.error.get("message", "Query failed"), "action": "CHECK_QUERY"})
        if project.stage == "COMPLETE" and not compilation_current:
            blockers.append({"code": "SQL_COMPILATION_REQUIRED", "message": "Compile the accepted SQL before execution", "action": "COMPILE"})
        return {
            "schema_version": "1",
            "id": project.id,
            "title": project.title,
            "source_request": project.source_request if include_payloads else None,
            "locale": project.locale,
            "stage": project.stage,
            "operation_state": project.operation_state,
            "state_version": project.state_version,
            "next_agent": next_agent_kind(state),
            "current_job": cls._job(current_job) if current_job else None,
            "pending_proposal": cls._proposal(pending) if pending else None,
            "artifact_ids": artifact_ids,
            "artifacts": artifacts,
            "compilation": compilation,
            "latest_run": latest_run,
            "runs": run_summaries,
            "derived_runs": derived_runs,
            "document": document,
            "documents": [{"id": item.id, "revision": item.revision, "payload": item.payload} for item in documents] if include_payloads else None,
            "latest_conclusion": (
                {"id": latest_conclusion.id, "payload": latest_conclusion.payload}
                if latest_conclusion is not None and run is not None and latest_conclusion.payload.get("source_run_id") == run.id
                else None
            )
            if include_payloads
            else None,
            "conclusions": list(conclusions_by_run.values()) if include_payloads else None,
            "next_action": next_action,
            "blockers": blockers,
            "last_error": project.last_error,
            "capabilities": {
                "requirements_agent": True,
                "schema_agent": True,
                "query_agent": True,
                "result_agent": ready_run is not None,
                "python_agent": any(item.status == "READY" and item.result_bytes <= 10_000_000 for item in runs),
            },
        }

    @staticmethod
    def _job(job: BusinessDocumentJob) -> dict[str, Any]:
        return {
            "id": job.id,
            "kind": job.job_type.removeprefix(_JOB_PREFIX),
            "status": job.status,
            "progress": job.progress,
            "progress_stage": job.progress_stage,
            "progress_message": job.progress_message,
            "attempt": job.attempt,
            "max_attempts": job.max_attempts,
            "error": job.error,
        }

    @staticmethod
    def _proposal(proposal: BusinessDocumentSqlAgentProposal) -> dict[str, Any]:
        return {
            "id": proposal.id,
            "kind": proposal.kind,
            "status": proposal.status,
            "source_state_version": proposal.source_state_version,
            "payload": proposal.payload,
        }

    @staticmethod
    def _command_replay(tenant_id: str, project_id: str, key: str, request_hash: str) -> dict[str, Any] | None:
        command = BusinessDocumentSqlAgentCommand.get_or_none(
            (BusinessDocumentSqlAgentCommand.tenant_id == tenant_id) & (BusinessDocumentSqlAgentCommand.project_id == project_id) & (BusinessDocumentSqlAgentCommand.idempotency_key == key)
        )
        if command is None:
            return None
        if command.request_hash != request_hash:
            raise ConflictError("SQL_AGENT_IDEMPOTENCY_CONFLICT", "Idempotency key was already used for another command")
        return command.response

    @staticmethod
    def _record_command(tenant_id: str, project_id: str, key: str, request_hash: str, response: dict[str, Any]) -> None:
        try:
            BusinessDocumentSqlAgentCommand.create(
                id=get_uuid(),
                tenant_id=tenant_id,
                project_id=project_id,
                idempotency_key=key,
                request_hash=request_hash,
                response=response,
            )
        except IntegrityError as exc:
            raise ConflictError("SQL_AGENT_IDEMPOTENCY_CONFLICT", "Concurrent command used this idempotency key") from exc

    @staticmethod
    def _require_job_lease(job: BusinessDocumentJob, worker_id: str, lease_token: str) -> None:
        if job.status != "RUNNING" or job.lease_owner != worker_id or job.lease_token != lease_token or not job.lease_expires_at or job.lease_expires_at <= current_timestamp():
            raise ConflictError("SQL_AGENT_JOB_LEASE_LOST", "SQL agent job lease is no longer valid")

    @staticmethod
    def _update_project_after_job(
        project: BusinessDocumentSqlQueryProject,
        *,
        operation_state: str,
        last_error: dict[str, Any] | None,
    ) -> None:
        changed = (
            BusinessDocumentSqlQueryProject.update(
                operation_state=operation_state,
                last_error=last_error,
                update_time=current_timestamp(),
                update_date=datetime.now(),
            )
            .where(
                (BusinessDocumentSqlQueryProject.id == project.id)
                & (BusinessDocumentSqlQueryProject.state_version == project.state_version)
                & (BusinessDocumentSqlQueryProject.current_job_id == project.current_job_id)
            )
            .execute()
        )
        if changed != 1:
            raise ConflictError("SQL_AGENT_VERSION_CONFLICT", "Project changed while agent result was being saved")

    @staticmethod
    def _finish_job(
        job: BusinessDocumentJob,
        status: str,
        *,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        BusinessDocumentJob.update(
            status=status,
            progress=1.0 if status == "COMPLETED" else job.progress,
            progress_stage="COMPLETED" if status == "COMPLETED" else status,
            progress_message="Результат агента готов" if status == "COMPLETED" else None,
            result=dict(result) if result is not None else None,
            error=dict(error) if error is not None else None,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            update_time=current_timestamp(),
            update_date=datetime.now(),
        ).where(BusinessDocumentJob.id == job.id).execute()
