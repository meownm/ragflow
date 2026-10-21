"""Persistence boundary for SQL workspace presentation preferences."""

from collections.abc import Mapping

from peewee import IntegrityError

from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService
from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.db.db_models import BusinessDocumentSqlQueryView
from business_documents.application.errors import ConflictError, ValidationError
from business_documents.sql_query.view_state import validate_view
from common.misc_utils import get_uuid


class BusinessDocumentSqlWorkspaceService:
    @staticmethod
    def view(tenant_id, actor_id, project_id, is_admin=False, access_role="AUTHOR_CREATOR"):
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        BusinessDocumentAccess(actor_id, access_role, is_admin).require_view_sql(project.owner_id)
        saved = BusinessDocumentSqlQueryView.get_or_none(
            (BusinessDocumentSqlQueryView.tenant_id == tenant_id) & (BusinessDocumentSqlQueryView.project_id == project_id) & (BusinessDocumentSqlQueryView.actor_id == actor_id)
        )
        return {"version": saved.version if saved else 0, "view": saved.view if saved else None}

    @classmethod
    def save_view(cls, tenant_id, actor_id, project_id, raw, is_admin=False, access_role="AUTHOR_CREATOR"):
        if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "expected_version", "view"} or raw["schema_version"] != "1" or type(raw["expected_version"]) is not int or raw["expected_version"] < 0:
            raise ValidationError("INVALID_SQL_VIEW", "A versioned diagram view is required")
        try:
            value = validate_view(raw["view"])
        except ValueError as exc:
            raise ValidationError("INVALID_SQL_VIEW", str(exc)) from exc
        with BusinessDocumentSqlQueryView._meta.database.atomic():
            current = cls.view(tenant_id, actor_id, project_id, is_admin, access_role)
            if current["view"] == value:
                return current
            if current["version"] != raw["expected_version"]:
                raise ConflictError("SQL_VIEW_VERSION_CONFLICT", "Diagram view changed in another tab")
            if current["version"] == 0:
                try:
                    BusinessDocumentSqlQueryView.create(id=get_uuid(), tenant_id=tenant_id, project_id=project_id, actor_id=actor_id, view=value)
                except IntegrityError as exc:
                    raise ConflictError("SQL_VIEW_VERSION_CONFLICT", "Diagram view was saved in another tab") from exc
            else:
                changed = BusinessDocumentSqlQueryView.update(view=value, version=current["version"] + 1).where(
                    (BusinessDocumentSqlQueryView.tenant_id == tenant_id) & (BusinessDocumentSqlQueryView.project_id == project_id) & (BusinessDocumentSqlQueryView.actor_id == actor_id) & (BusinessDocumentSqlQueryView.version == current["version"])
                ).execute()
                if changed != 1:
                    raise ConflictError("SQL_VIEW_VERSION_CONFLICT", "Diagram view changed in another tab")
            return {"version": current["version"] + 1, "view": value}
