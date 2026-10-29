"""Narrow integration with SQL projects, immutable plans and their executor."""

from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.apps.business_documents.sql_query_access import authorize_catalog_sources, authorized_result, read_sql_project
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService as Revisions
from api.apps.business_documents.sql_query_runs import BusinessDocumentSqlRunService as Runs
from business_documents.sql_assistant_query import compile_assistant_query

__all__ = ["Projects", "Revisions", "Runs", "SqlRuntime"]


class SqlRuntime:
    def __init__(self, tenant_id, actor_id, is_admin=False, access_role="AUTHOR_CREATOR"):
        self.tenant_id, self.actor_id = tenant_id, actor_id
        self.is_admin, self.access_role = is_admin, access_role
        self.access = BusinessDocumentAccess(actor_id, access_role, is_admin)

    def call(self, method, project_id, *args):
        return method(self.tenant_id, self.actor_id, project_id, *args, self.is_admin, self.access_role)

    def project(self, project_id):
        result = self.call(Projects.get_project, project_id)
        result["catalog_changed"] = False
        schema = (result.get("artifacts") or {}).get("schema")
        if schema:
            project = read_sql_project(self.tenant_id, self.actor_id, project_id, self.is_admin, self.access_role)
            _, result["catalog_changed"] = authorize_catalog_sources(project, self.actor_id, schema, None, is_admin=self.is_admin, access_role=self.access_role, include_schema_tables=True)
        return result

    def result(self, project_id, run_id):
        project = read_sql_project(self.tenant_id, self.actor_id, project_id, self.is_admin, self.access_role)
        return authorized_result(project, self.actor_id, run_id, is_admin=self.is_admin, access_role=self.access_role)

    def compile_draft(self, project, query):
        artifacts = project.get("artifacts") or {}
        return compile_assistant_query(artifacts["requirements"], artifacts["schema"], query)

    def follow_up(self, project, run_id, message, command):
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
        )
        return self.call(Revisions.request_change, project["id"], thread["id"], {**command, "idempotency_key": command["idempotency_key"] + ":revision"})
