"""Production runners for durable SQL constructor agent jobs."""

from __future__ import annotations

from typing import Any

from business_documents.async_runtime import run_in_worker_loop
from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.apps.business_documents.sql_query_planner import TenantQueryPlanner, query_planner_provenance
from api.apps.business_documents.sql_query_revision_catalog import propose_catalog_revision
from api.apps.business_documents.sql_query_requirements import TenantRequirementsAnalyst, requirements_prompt_provenance
from api.apps.business_documents.sql_query_schema import OpenMetadataCatalogResolver, TenantSchemaInterpreter
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlQueryProject, User
from business_documents.sql_query.query_planning import QueryPlanningScenario, parse_plan_query_command
from business_documents.sql_query.requirements_analysis import RequirementsAnalysisScenario, parse_analyze_requirements_command
from business_documents.sql_query.schema_resolution import SchemaResolutionScenario, parse_resolve_schema_command


class BusinessDocumentSqlAgentRunner:
    """Dispatch one immutable job snapshot to its bounded agent capability."""

    def process(self, job: BusinessDocumentJob) -> dict[str, Any]:
        if job.job_type == "SQL_AGENT_DIALOGUE":
            from api.apps.sql_assistant.conversation import plan_job

            actor_id, is_admin, access_role = self._authorize(job)
            return plan_job(job, actor_id, is_admin, access_role)
        return run_in_worker_loop(self._process(job))

    def _authorize(self, job: BusinessDocumentJob) -> tuple[str, bool, str]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        actor_id = str(payload.get("actor_id") or "")
        user = User.get_or_none(User.id == actor_id)
        if user is None or str(user.status) != "1" or str(user.is_active) != "1":
            raise RuntimeError("SQL agent actor is no longer active")
        is_admin = bool(user.is_superuser)
        access_role = str(user.business_document_role or "AUTHOR_EDITOR")
        project = BusinessDocumentSqlQueryProject.get_or_none((BusinessDocumentSqlQueryProject.id == job.document_id) & (BusinessDocumentSqlQueryProject.tenant_id == job.tenant_id))
        if project is None:
            raise RuntimeError("SQL agent project no longer exists")
        access = BusinessDocumentAccess(actor_id, access_role, is_admin)
        if job.job_type == "SQL_AGENT_DIALOGUE" and ((payload.get("request") or {}).get("payload") or {}).get("kind") != "DEFINITION_BINDING":
            access.require_view_sql(project.owner_id)
        else:
            access.require_edit(project.owner_id)
        return actor_id, is_admin, access_role

    async def _process(self, job: BusinessDocumentJob) -> dict[str, Any]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        actor_id, is_admin, access_role = self._authorize(job)
        request = payload.get("request")
        if job.job_type == "SQL_AGENT_REQUIREMENTS":
            command = parse_analyze_requirements_command(request, tenant_id=job.tenant_id)
            result = await RequirementsAnalysisScenario(TenantRequirementsAnalyst()).run(command)
            return {
                **result,
                "llm": {
                    "status": "APPLIED" if result["proposal"] is not None else "FALLBACK",
                    "prompt": requirements_prompt_provenance(),
                    "warning": result["warning"],
                },
            }
        if job.job_type == "SQL_AGENT_SCHEMA":
            command = parse_resolve_schema_command(
                request,
                tenant_id=job.tenant_id,
                actor_id=actor_id,
                is_admin=is_admin,
                source_request=payload.get("source_request", ""),
            )
            resolver = OpenMetadataCatalogResolver()
            result = await SchemaResolutionScenario(resolver, TenantSchemaInterpreter()).run(command)
            from api.apps.sql_assistant.conversation import enrich_schema

            return await enrich_schema(job, result, resolver, actor_id, is_admin, command.locale)
        if job.job_type == "SQL_AGENT_QUERY":
            command = parse_plan_query_command(request, tenant_id=job.tenant_id)
            result = await QueryPlanningScenario(TenantQueryPlanner()).run(command)
            return {
                **result,
                "llm": {
                    "status": "APPLIED" if result["proposal"] is not None else "FALLBACK",
                    "prompt": query_planner_provenance(),
                    "warning": result["warning"],
                },
            }
        if job.job_type == "SQL_AGENT_REVISION":
            if not isinstance(request, dict):
                raise RuntimeError("SQL revision request is missing")
            return await propose_catalog_revision(job.tenant_id, actor_id, request, is_admin, access_role)
        raise RuntimeError(f"Unsupported SQL agent job type: {job.job_type}")
