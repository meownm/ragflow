"""Durable application service for the SQL document-constructor agent cycle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from peewee import IntegrityError

from api.apps.business_documents.authorization import BusinessDocumentAccess, BusinessDocumentRole
from api.apps.business_documents.errors import BusinessDocumentError, ConflictError, ValidationError
from api.db.db_models import (
    BusinessDocumentJob,
    BusinessDocumentSqlAgentCommand,
    BusinessDocumentSqlAgentProposal,
    BusinessDocumentSqlQueryArtifact,
    BusinessDocumentSqlQueryProject,
)
from business_documents.sql_query.agent_cycle import (
    AgentCycleConflict,
    AgentCycleValidationError,
    ProjectAgentState,
    next_agent_kind,
    parse_request_agent_command,
    require_next_agent,
)
from business_documents.sql_query.query_planning import QueryPlanValidationError, parse_plan_query_command
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
                cls._update_project_after_job(project, operation_state="FAILED", last_error=dict(error))
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
        if supplied is not None and supplied != value:
            raise ValidationError(
                "INVALID_SQL_AGENT_DECISION",
                "Query proposal must be edited by rerunning the agent before acceptance",
            )
        return dict(value)

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
        if include_payloads:
            artifacts = {name: BusinessDocumentSqlQueryArtifact.get_by_id(artifact_id).payload if artifact_id else None for name, artifact_id in artifact_ids.items()}
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
            "last_error": project.last_error,
            "capabilities": {
                "requirements_agent": True,
                "schema_agent": True,
                "query_agent": True,
                "result_agent": False,
                "python_agent": False,
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
