"""A project-scoped connection check cannot execute, repair or accept user SQL."""

from types import SimpleNamespace

import pytest

from test.unit_test.api.apps.business_documents.test_sql_project_connection import database as connection_database
from api.apps.business_documents import sql_query_runs as owner
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError, ValidationError


@pytest.fixture
def database(monkeypatch):
    yield from connection_database.__wrapped__(monkeypatch)


def check(project, selected="profile-1", **options):
    return owner.BusinessDocumentSqlRunService.check_connection("tenant-1", "author-1", project, {"schema_version": "1", "selected_profile_id": selected}, **options)


def test_health_uses_accepted_schema_even_without_query_or_compilation(database, monkeypatch):
    owner.BusinessDocumentSqlQueryProject.update(stage="QUERY", execution_profile_id="profile-1").where(owner.BusinessDocumentSqlQueryProject.id == database).execute()
    before = Projects.get_project("tenant-1", "author-1", database)
    seen = []
    monkeypatch.setattr(owner, "_source", lambda *args: pytest.fail("Health must not compile user SQL"))
    monkeypatch.setattr(owner, "explain_postgres", lambda *args, **kwargs: pytest.fail("Health must not EXPLAIN user SQL"))
    monkeypatch.setattr(owner, "execute_postgres", lambda *args, **kwargs: pytest.fail("Health must not execute user SQL"))
    monkeypatch.setattr(owner, "_binding", lambda *args: (seen.append(args[-1]) or {"status": "BOUND", "selection": {"profile": {"id": "profile-2", "version": 4}}}, SimpleNamespace(id="profile-2", statement_timeout_ms=9000), SimpleNamespace(config={"qa": True})))
    probes = []
    monkeypatch.setattr(owner, "check_postgres_connection", lambda config, **options: probes.append((config, options)) or 3)
    result = check(database, None)
    assert result["status"] == "AVAILABLE" and result["query_checked"] is False
    assert result["checked_at"] > 0 and result["duration_ms"] == 3
    assert seen == [None]  # Explicit automatic selection does not reuse the saved ID.
    assert probes == [({"qa": True}, {"timeout_ms": 5000})]
    assert Projects.get_project("tenant-1", "author-1", database) == before
    assert owner.BusinessDocumentSqlQueryRun.select().count() == owner.BusinessDocumentJob.select().count() == 0
    assert owner.BusinessDocumentSqlQueryArtifact.select().count() == 2


def test_profile_can_be_saved_before_query_compilation_without_running_sql(database, monkeypatch):
    owner.BusinessDocumentSqlQueryProject.update(stage="QUERY").where(owner.BusinessDocumentSqlQueryProject.id == database).execute()
    monkeypatch.setattr(owner, "_source", lambda *args: pytest.fail("Connection choice does not require a query"))
    monkeypatch.setattr(owner, "check_postgres_connection", lambda *args, **kwargs: pytest.fail("Saving a profile is not a probe"))
    result = owner.BusinessDocumentSqlRunService.configure_connection("tenant-1", "author-1", database, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "before-query", "selected_profile_id": "profile-1"})
    assert result["execution_profile_id"] == "profile-1" and result["state_version"] == 2
    assert result["artifact_ids"]["query"] is None
    assert owner.BusinessDocumentSqlQueryArtifact.select().count() == 2
    assert owner.BusinessDocumentSqlQueryRun.select().count() == owner.BusinessDocumentJob.select().count() == 0


@pytest.mark.parametrize("role", ["READER", "AUTHOR_EDITOR"])
def test_health_rejects_roles_before_catalog_or_network(database, monkeypatch, role):
    monkeypatch.setattr(owner, "_schema_source", lambda *args: pytest.fail("Denied roles cannot inspect schema"))
    monkeypatch.setattr(owner, "check_postgres_connection", lambda *args, **kwargs: pytest.fail("Denied roles cannot probe sources"))
    with pytest.raises(PermissionDeniedError):
        check(database, access_role=role)


@pytest.mark.parametrize("tenant,actor", [("other", "author-1"), ("tenant-1", "other")])
def test_health_rejects_foreign_project_before_connection(database, monkeypatch, tenant, actor):
    monkeypatch.setattr(owner, "check_postgres_connection", lambda *args, **kwargs: pytest.fail("Foreign project cannot probe"))
    with pytest.raises(BusinessDocumentError) as caught:
        owner.BusinessDocumentSqlRunService.check_connection(tenant, actor, database, {"schema_version": "1", "selected_profile_id": None})
    assert caught.value.status == (404 if tenant != "tenant-1" else 403)


@pytest.mark.parametrize("payload", [{}, {"schema_version": "2", "selected_profile_id": None}, {"schema_version": "1", "selected_profile_id": True}, {"schema_version": "1", "selected_profile_id": " x"}, {"schema_version": "1", "selected_profile_id": "x" * 33}, {"schema_version": "1", "selected_profile_id": None, "sql": "DROP TABLE orders"}])
def test_health_command_is_closed_and_never_accepts_sql(database, monkeypatch, payload):
    monkeypatch.setattr(owner, "check_postgres_connection", lambda *args, **kwargs: pytest.fail("Invalid command cannot connect"))
    with pytest.raises(ValidationError):
        owner.BusinessDocumentSqlRunService.check_connection("tenant-1", "author-1", database, payload)


def test_revoked_saved_profile_offers_candidates_without_probing_an_alternative(database, monkeypatch):
    owner.BusinessDocumentSqlQueryProject.update(execution_profile_id="profile-1").where(owner.BusinessDocumentSqlQueryProject.id == database).execute()

    def binding(*args):
        if args[-1] == "profile-1":
            raise ValidationError("INVALID_SQL_EXECUTION_BINDING_REQUEST", "Profile was disabled")
        return {"status": "BOUND", "candidates": [{"id": "profile-2"}], "selection": {"profile": {"id": "profile-2"}}}, SimpleNamespace(id="profile-2"), SimpleNamespace(config={})

    monkeypatch.setattr(owner, "_binding", binding)
    monkeypatch.setattr(owner, "check_postgres_connection", lambda *args, **kwargs: pytest.fail("Do not probe another profile implicitly"))
    result = check(database)
    assert result["status"] == "UNAVAILABLE" and result["binding"]["selection"] is None
    assert result["checked_at"] is None and result["duration_ms"] is None
    assert result["binding"]["candidates"] == [{"id": "profile-2"}]


def test_current_catalog_denial_blocks_health(database, monkeypatch):
    def denied(*args):
        raise PermissionDeniedError()

    monkeypatch.setattr(owner, "_verify_schema", denied)
    monkeypatch.setattr(owner, "check_postgres_connection", lambda *args, **kwargs: pytest.fail("No connector access after catalog denial"))
    with pytest.raises(PermissionDeniedError):
        check(database)


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "kind"])
def test_health_rejects_artifacts_from_another_scope(database, monkeypatch, field):
    owner.BusinessDocumentSqlQueryArtifact.update(**{field: "other"}).where(owner.BusinessDocumentSqlQueryArtifact.id == "health-schema").execute()
    monkeypatch.setattr(owner, "check_postgres_connection", lambda *args, **kwargs: pytest.fail("Mismatched artifacts cannot connect"))
    with pytest.raises(ConflictError) as caught:
        check(database)
    assert caught.value.code == "SQL_PROJECT_ARTIFACT_MISMATCH"
