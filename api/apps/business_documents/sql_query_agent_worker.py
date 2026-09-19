"""Production runners for durable SQL constructor agent jobs."""

from __future__ import annotations

import asyncio
from typing import Any

from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.apps.business_documents.sql_query_planner import TenantQueryPlanner, query_planner_provenance
from api.apps.business_documents.sql_query_requirements import TenantRequirementsAnalyst, requirements_prompt_provenance
from api.apps.business_documents.sql_query_schema import OpenMetadataCatalogResolver, TenantSchemaInterpreter
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlQueryProject, User
from business_documents.sql_query.query_planning import QueryPlanningScenario, parse_plan_query_command
from business_documents.sql_query.requirements_analysis import RequirementsAnalysisScenario, parse_analyze_requirements_command
from business_documents.sql_query.schema_resolution import SchemaResolutionScenario, parse_resolve_schema_command


class BusinessDocumentSqlAgentRunner:
    """Dispatch one immutable job snapshot to its bounded agent capability."""

    def process(self, job: BusinessDocumentJob) -> dict[str, Any]:
        return asyncio.run(self._process(job))

    async def _process(self, job: BusinessDocumentJob) -> dict[str, Any]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        actor_id = str(payload.get("actor_id") or "")
        user = User.get_or_none(User.id == actor_id)
        if user is None or str(user.status) != "1":
            raise RuntimeError("SQL agent actor is no longer active")
        is_admin = bool(user.is_superuser)
        access_role = str(user.business_document_role or "AUTHOR_EDITOR")
        project = BusinessDocumentSqlQueryProject.get_or_none((BusinessDocumentSqlQueryProject.id == job.document_id) & (BusinessDocumentSqlQueryProject.tenant_id == job.tenant_id))
        if project is None:
            raise RuntimeError("SQL agent project no longer exists")
        BusinessDocumentAccess(actor_id, access_role, is_admin).require_edit(project.owner_id)
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
            )
            return await SchemaResolutionScenario(OpenMetadataCatalogResolver(), TenantSchemaInterpreter()).run(command)
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
        raise RuntimeError(f"Unsupported SQL agent job type: {job.job_type}")
