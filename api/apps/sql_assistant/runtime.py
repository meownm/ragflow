"""Narrow integration with SQL projects, immutable plans and their executor."""

from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.apps.business_documents.sql_query_access import authorize_catalog_sources, authorized_result, read_sql_project
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService as Revisions
from api.apps.business_documents.sql_query_runs import BusinessDocumentSqlRunService as Runs
from business_documents.sql_assistant_query import compile_assistant_query
from business_documents.sql_query.project_compilation import build_project_schema_command
from business_documents.sql_query.query_planning import MAX_PLAN_ROW_LIMIT

__all__ = ["Projects", "Revisions", "Runs", "SqlRuntime"]


class SqlRuntime:
    def __init__(self, tenant_id, actor_id, is_admin=False, access_role="AUTHOR_CREATOR"):
        self.tenant_id, self.actor_id = tenant_id, actor_id
        self.is_admin, self.access_role = is_admin, access_role
        self.access = BusinessDocumentAccess(actor_id, access_role, is_admin)

    def call(self, method, project_id, *args, **kwargs):
        return method(self.tenant_id, self.actor_id, project_id, *args, self.is_admin, self.access_role, **kwargs)

    def project(self, project_id):
        result = self.call(Projects.get_project, project_id)
        result["catalog_changed"] = False
        schema = (result.get("artifacts") or {}).get("schema")
        if schema:
            project = read_sql_project(self.tenant_id, self.actor_id, project_id, self.is_admin, self.access_role)
            _, result["catalog_changed"] = authorize_catalog_sources(project, self.actor_id, schema, None, is_admin=self.is_admin, access_role=self.access_role, include_schema_tables=True)
            pending = (result.get("pending_proposal") or {}).get("payload", {}).get("agent_result", {}).get("source", {}).get("proposed_schema")
            if pending is not None and pending != schema:
                _, changed = authorize_catalog_sources(project, self.actor_id, pending, None, is_admin=self.is_admin, access_role=self.access_role, include_schema_tables=True)
                result["catalog_changed"] |= changed
        return result

    def result(self, project_id, run_id):
        project = read_sql_project(self.tenant_id, self.actor_id, project_id, self.is_admin, self.access_role)
        return authorized_result(project, self.actor_id, run_id, is_admin=self.is_admin, access_role=self.access_role)

    def compile_draft(self, project, query):
        artifacts = project.get("artifacts") or {}
        return compile_assistant_query(artifacts["requirements"], artifacts["schema"], query)

    def planning_row_limit(self, project):
        from api.apps.business_documents.sql_execution_registry import BusinessDocumentSqlExecutionRegistryService

        artifacts = project["artifacts"]
        scope = build_project_schema_command(artifacts["requirements"], artifacts["schema"])
        binding = BusinessDocumentSqlExecutionRegistryService.resolve(self.actor_id, {**scope, "selected_profile_id": project.get("execution_profile_id")}, self.is_admin, self.access_role)
        if binding["status"] != "BOUND":
            return None
        return min(binding["selection"]["profile"]["max_rows"], MAX_PLAN_ROW_LIMIT)

    def follow_up(self, project, run_id, message, command, *, source_text=None, selection=None):
        run, source, _ = self.result(project["id"], run_id)
        thread = self.call(
            Revisions.create_thread,
            project["id"],
            {
                **command,
                "target": "QUERY",
                "base_artifact_id": source.query.id,
                "anchor": {"kind": "WHOLE"},
                "text": message,
            },
            compilation_id=source.compilation.id,
            selected_group=selection,
        )
        return self.call(
            Revisions.request_change,
            project["id"],
            thread["id"],
            {**command, "idempotency_key": command["idempotency_key"] + ":revision"},
            source_text=source_text,
        )
