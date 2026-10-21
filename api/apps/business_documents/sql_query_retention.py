"""Atomic removal of expired rows and their ephemeral result-derived copies."""

from collections.abc import Mapping

from api.db.db_models import BusinessDocumentSqlAgentCommand as Command, BusinessDocumentSqlQueryArtifact as Artifact, BusinessDocumentSqlQueryRun as Run
from business_documents.sql_query.result_retention import RESULT_RETENTION_MS, result_expires_at
from common.time_utils import current_timestamp

EXPIRED_RESPONSE = {"status": "EXPIRED", "error_code": "SQL_RESULT_EXPIRED"}


def _references(value, identities, field=None):
    if isinstance(value, str):
        return field in {"id", "run_id", "source_run_id", "proposal_id", "conclusion_id", "run_ids", "ready_run_ids"} and value in identities
    if isinstance(value, Mapping):
        return any(_references(item, identities, key) for key, item in value.items())
    if isinstance(value, list):
        return any(_references(item, identities, field) for item in value)
    return False


def expire_project_results(tenant_id: str, project_id: str, *, now_ms: int | None = None) -> int:
    """Called after request authorization, or by trusted worker maintenance."""
    now = current_timestamp() if now_ms is None else now_ms
    scope = (Run.tenant_id == tenant_id) & (Run.project_id == project_id)
    with Run._meta.database.atomic():
        ready = list(Run.select(Run.id, Run.kind, Run.source_run_id, Run.create_time).where(scope & (Run.status == "READY")))
        if not ready:
            return 0
        parent_ids = {run.source_run_id for run in ready if run.kind != "SQL" and run.source_run_id}
        parents = {
            run.id: run for run in Run.select(Run.id, Run.status, Run.kind, Run.create_time).where(scope & Run.id.in_(parent_ids))
        } if parent_ids else {}
        expired_ids = set()
        for run in ready:
            parent = parents.get(run.source_run_id)
            if result_expires_at(run.create_time) <= now or (
                parent is not None and parent.kind == "SQL"
                and (parent.status == "EXPIRED" or (parent.status == "READY" and result_expires_at(parent.create_time) <= now))
            ):
                expired_ids.add(run.id)
        if not expired_ids:
            return 0
        changed = Run.update(status="EXPIRED", rows=[], columns=[], checks={}, result_bytes=0, error=None).where(
            scope & Run.id.in_(expired_ids) & (Run.status == "READY")
        ).execute()
        if not changed:
            return 0
        expired_ids = {run.id for run in Run.select(Run.id).where(scope & Run.id.in_(expired_ids) & (Run.status == "EXPIRED"))}
        artifact_scope = (Artifact.tenant_id == tenant_id) & (Artifact.project_id == project_id) & (Artifact.kind == "CONCLUSION")
        conclusion_ids = {
            artifact.id for artifact in Artifact.select(Artifact.id, Artifact.payload).where(artifact_scope)
            if isinstance(artifact.payload, Mapping) and artifact.payload.get("source_run_id") in expired_ids
        }
        if conclusion_ids:
            Artifact.delete().where(artifact_scope & Artifact.id.in_(conclusion_ids)).execute()
        references = expired_ids | conclusion_ids
        command_scope = (Command.tenant_id == tenant_id) & (Command.project_id == project_id)
        for command in Command.select(Command.id, Command.response).where(command_scope).iterator():
            if _references(command.response, references):
                # Retain the key/hash so a retry cannot create a second effect.
                Command.update(response=dict(EXPIRED_RESPONSE)).where(command_scope & (Command.id == command.id)).execute()
        return changed


def expire_due_results(*, tenant_id: str | None = None, now_ms: int | None = None, batch_size: int = 100) -> int:
    """Bound each worker maintenance pass; the indexed clock never slides."""
    now = current_timestamp() if now_ms is None else now_ms
    parent = Run.alias("retention_source")
    expired_sources = parent.select(parent.id).where(
        (parent.status == "EXPIRED") & (parent.kind == "SQL")
        & (parent.tenant_id == Run.tenant_id) & (parent.project_id == Run.project_id)
    )
    candidates = Run.select(Run.tenant_id, Run.project_id).where(
        (Run.status == "READY") & ((Run.create_time <= now - RESULT_RETENTION_MS) | Run.create_time.is_null() | Run.source_run_id.in_(expired_sources))
    )
    if tenant_id is not None:
        candidates = candidates.where(Run.tenant_id == tenant_id)
    candidates = candidates.order_by(Run.create_time, Run.id).limit(batch_size)
    projects = {(run.tenant_id, run.project_id) for run in candidates}
    return sum(expire_project_results(tenant, project, now_ms=now) for tenant, project in projects)
