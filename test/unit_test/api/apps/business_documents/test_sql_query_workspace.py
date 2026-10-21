from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_revisions import ACTOR, TENANT, project as revision_project
from test.unit_test.business_documents.sql_query.test_view_state import VIEW
from api.apps.business_documents.sql_query_workspace import BusinessDocumentSqlWorkspaceService as Service
from api.db.db_models import BusinessDocumentSqlQueryProject, BusinessDocumentSqlQueryView
from business_documents.application.errors import ConflictError, PermissionDeniedError, ValidationError


@pytest.fixture
def project():
    yield from revision_project.__wrapped__()


def test_view_is_persistent_idempotent_and_independent_from_sql_revision(project):
    assert Service.view(TENANT, ACTOR, project) == {"version": 0, "view": None}
    request = {"schema_version": "1", "expected_version": 0, "view": deepcopy(VIEW)}
    saved = Service.save_view(TENANT, ACTOR, project, request)
    assert saved == {"version": 1, "view": VIEW}
    assert Service.save_view(TENANT, ACTOR, project, request) == saved
    assert Service.view(TENANT, ACTOR, project) == saved
    assert BusinessDocumentSqlQueryProject.get_by_id(project).state_version == 1
    assert BusinessDocumentSqlQueryView.select().count() == 1


def test_two_tabs_cannot_overwrite_changed_positions(project):
    Service.save_view(TENANT, ACTOR, project, {"schema_version": "1", "expected_version": 0, "view": VIEW})
    with pytest.raises(ConflictError):
        Service.save_view(TENANT, ACTOR, project, {"schema_version": "1", "expected_version": 0, "view": {**VIEW, "mode": "query"}})
    assert Service.view(TENANT, ACTOR, project)["view"] == VIEW


@pytest.mark.parametrize("action", ["read", "write"])
def test_foreign_actor_cannot_access_view_in_same_tenant(project, action):
    with pytest.raises(PermissionDeniedError):
        if action == "read":
            Service.view(TENANT, "foreign", project)
        else:
            Service.save_view(TENANT, "foreign", project, {"schema_version": "1", "expected_version": 0, "view": VIEW})
    assert BusinessDocumentSqlQueryView.select().count() == 0


def test_reader_can_save_personal_layout_without_editing_query(project):
    saved = Service.save_view(TENANT, ACTOR, project, {"schema_version": "1", "expected_version": 0, "view": VIEW}, access_role="READER")
    assert saved["version"] == 1
    assert BusinessDocumentSqlQueryProject.get_by_id(project).state_version == 1


def test_invalid_view_never_creates_a_record(project):
    with pytest.raises(ValidationError):
        Service.save_view(TENANT, ACTOR, project, {"schema_version": "1", "expected_version": 0, "view": {**VIEW, "actor_id": "foreign"}})
    assert BusinessDocumentSqlQueryView.select().count() == 0


def test_two_authorized_actors_keep_independent_layout_versions(project):
    first = Service.save_view(TENANT, ACTOR, project, {"schema_version": "1", "expected_version": 0, "view": VIEW})
    alternate = {**deepcopy(VIEW), "mode": "query", "viewport": {"x": 50, "y": 80, "zoom": .7}}
    second = Service.save_view(TENANT, "qa-admin", project, {"schema_version": "1", "expected_version": 0, "view": alternate}, is_admin=True)
    assert first["version"] == second["version"] == 1
    assert Service.view(TENANT, ACTOR, project)["view"] == VIEW
    assert Service.view(TENANT, "qa-admin", project, is_admin=True)["view"] == alternate
    assert BusinessDocumentSqlQueryView.select().count() == 2
    assert BusinessDocumentSqlQueryProject.get_by_id(project).state_version == 1
