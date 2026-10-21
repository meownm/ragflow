"""Downloads use immutable sources and freshly authorized catalog visibility."""

from copy import deepcopy
import asyncio
import json
import xml.etree.ElementTree as ET

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_access import ACTOR, project as stored_project, source as stored_source
from test.unit_test.api.apps.business_documents.test_business_document_api import route_app as isolated_routes
from api.apps.business_documents import sql_query_schema
from api.apps.business_documents.sql_query_agents import _stable_hash
from api.apps.business_documents.sql_query_exports import BusinessDocumentSqlExportService as Exports
from api.apps.business_documents.sql_query_runs import BusinessDocumentSqlRunService as Runs
from api.db.db_models import BusinessDocumentSqlAgentProposal as Proposal, BusinessDocumentSqlQueryArtifact as Artifact, BusinessDocumentSqlQueryRun as Run, BusinessDocumentSqlQueryThread as Thread
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError
from test.evals.sql_projects.golden import BASE


@pytest.fixture
def project():
    yield from stored_project.__wrapped__()


@pytest.fixture
def source(project, monkeypatch):
    yield from stored_source.__wrapped__(project, monkeypatch)


@pytest.fixture
def route_app(monkeypatch):
    yield from isolated_routes.__wrapped__(monkeypatch)


@pytest.fixture
def catalog(source, monkeypatch):
    _, tables, _ = source

    class VisibleCatalog:
        async def authorize(self, **_kwargs):
            return None

        async def visible_catalog(self, **_kwargs):
            return {"tables": list(tables.values()), "freshness": {"stale": False}, "warnings": []}

    monkeypatch.setattr(sql_query_schema, "OpenMetadataCatalogResolver", VisibleCatalog)
    return tables


def command(kind="QUERY", **changes):
    return {"schema_version": "1", "kind": kind, "format": "json", **({"run_id": "result"} if kind == "RESULT" else {"compilation_id": "c" * 32, "query_artifact_id": "q" * 32}), **changes}


def export(project, raw):
    return Exports.export(project.tenant_id, ACTOR, project.id, raw, access_role="READER")


def packet(artifact):
    return json.loads(b"".join(artifact.chunks))


def result(project, **changes):
    return Run.create(
        **{
            "id": "result",
            "project_id": project.id,
            "tenant_id": project.tenant_id,
            "compilation_id": "c" * 32,
            "profile_id": "original-profile",
            "profile_version": 7,
            "profile_name": "Original QA",
            "kind": "SQL",
            "status": "READY",
            "columns": ["id", "amount", "present"],
            "rows": [[9223372036854775807, "12345678901234567890.00100", False]],
            "row_count": 1,
            "checks": {"status": "PASS", "completeness": "FULL", "column_types": [{"postgres_oid": 20}, {"postgres_oid": 1700}, {"postgres_oid": 16}]},
            **changes,
        }
    )


def test_reader_exports_original_query_and_parameter_values_after_project_changes(source):
    project, _, _ = source
    project.query_artifact_id = "new-query"
    project.selected_profile_id = "new-profile"
    project.save()
    artifact = export(project, command())
    data = packet(artifact)
    assert artifact.filename == f"sql-query-{'q' * 32}.json"
    assert data["sql"] == BASE.oracle.sql
    assert {item["name"]: item["value"] for item in data["parameters"]} == BASE.values()
    assert data["source"]["is_current_query"] is False
    assert data["source"]["query"]["id"] == "q" * 32
    assert data["source"]["catalog_entity_ids"] == ["orders"]
    assert data["source"]["query"]["accepted_by"] == ACTOR


@pytest.mark.parametrize("kind", ["QUERY", "RESULT", "GRAPH"])
@pytest.mark.parametrize("identity", ["foreign-tenant", "foreign-owner"])
def test_export_rejects_foreign_identity_before_loading_catalog_or_rows(source, monkeypatch, kind, identity):
    project, _, _ = source

    async def forbidden(*_args):
        pytest.fail("Unauthorized export must not reach catalog dependencies")

    monkeypatch.setattr(sql_query_schema.BusinessDocumentSqlQuerySchemaService, "load_entities", forbidden)
    with pytest.raises(BusinessDocumentError) as error:
        Exports.export("foreign" if identity == "foreign-tenant" else project.tenant_id, "foreign" if identity == "foreign-owner" else ACTOR, project.id, command(kind), access_role="READER")
    assert error.value.status == (404 if identity == "foreign-tenant" else 403)


def test_result_export_keeps_original_profile_precise_values_and_source(source):
    project, _, _ = source
    result(project)
    project.selected_profile_id = "another-profile"
    project.query_artifact_id = "another-query"
    project.save()
    data = packet(export(project, command("RESULT")))
    assert data["run"]["profile_id"] == "original-profile" and data["run"]["profile_version"] == 7
    assert data["source"]["compilation"]["id"] == "c" * 32
    assert data["query"]["sql"] == BASE.oracle.sql
    assert data["rows"] == [[{"type": "integer", "value": "9223372036854775807"}, {"type": "decimal", "value": "12345678901234567890.00100"}, {"type": "boolean", "value": False}]]


def test_preview_preserves_large_integer_digits_without_changing_stored_rows(source, monkeypatch):
    project, _, _ = source
    stored = result(project, columns=["id", "json"], rows=[[9223372036854775807, {"nested": [9007199254740992, False, None]}]], checks={"status": "PASS"})
    preview = Runs.preview(project.tenant_id, ACTOR, project.id, stored.id, access_role="READER")
    assert preview["rows"] == [["9223372036854775807", {"nested": ["9007199254740992", False, None]}]]
    assert Run.get_by_id(stored.id).rows == stored.rows

    async def denied(*_args):
        raise PermissionDeniedError("Current catalog access revoked")

    monkeypatch.setattr(sql_query_schema.BusinessDocumentSqlQuerySchemaService, "load_entities", denied)
    with pytest.raises(PermissionDeniedError):
        Runs.preview(project.tenant_id, ACTOR, project.id, stored.id, access_role="READER")


@pytest.mark.parametrize("kind", ["QUERY", "RESULT", "GRAPH"])
def test_catalog_revocation_rejects_every_download_before_result_rows_are_read(source, monkeypatch, kind):
    project, _, _ = source
    result(project)
    selects = []
    database = Run._meta.database
    execute = database.execute_sql

    def tracked(sql, *args, **kwargs):
        selects.append(sql)
        return execute(sql, *args, **kwargs)

    async def denied(*_args):
        raise PermissionDeniedError("Source visibility revoked")

    monkeypatch.setattr(database, "execute_sql", tracked)
    monkeypatch.setattr(sql_query_schema.BusinessDocumentSqlQuerySchemaService, "load_entities", denied)
    with pytest.raises(PermissionDeniedError):
        export(project, command(kind))
    assert not any('"rows"' in sql and sql.startswith("SELECT") for sql in selects)


@pytest.mark.parametrize("damage", ["foreign-tenant", "foreign-project", "purged", "expired", "invalid-checks", "invalid-row", "invalid-types", "wrong-parent", "empty-columns", "duplicate-columns"])
def test_result_export_rejects_unavailable_or_unverified_data(source, damage):
    project, _, _ = source
    run = result(project)
    if damage == "foreign-tenant":
        run.tenant_id = "other"
    elif damage == "foreign-project":
        run.project_id = "other"
    elif damage in {"purged", "expired"}:
        run.status = damage.upper()
    elif damage == "invalid-checks":
        run.checks = {"status": "FAIL"}
    elif damage == "invalid-row":
        run.rows = [[1]]
    elif damage == "invalid-types":
        run.checks["column_types"] = [{"postgres_oid": 20}]
    elif damage == "empty-columns":
        run.columns, run.rows = [], [[]]
    elif damage == "duplicate-columns":
        run.columns = ["id", "ID", "amount"]
    else:
        run.kind = "PYTHON"
        run.source_run_id = "missing-parent"
    run.save()
    with pytest.raises(BusinessDocumentError) as error:
        export(project, command("RESULT"))
    assert error.value.status == (404 if damage in {"foreign-tenant", "foreign-project", "wrong-parent"} else 410 if damage == "expired" else 409)


def test_result_purged_during_catalog_check_is_not_released(source, monkeypatch):
    project, _, _ = source
    result(project)
    original = sql_query_schema.BusinessDocumentSqlQuerySchemaService.load_entities

    async def purge(*args):
        Run.update(rows=[], status="PURGED").where(Run.id == "result").execute()
        return await original(*args)

    monkeypatch.setattr(sql_query_schema.BusinessDocumentSqlQuerySchemaService, "load_entities", purge)
    with pytest.raises(ConflictError) as error:
        export(project, command("RESULT"))
    assert error.value.code == "SQL_RESULT_REMOVED"


@pytest.mark.parametrize("output", ["json", "svg"])
def test_graph_download_preserves_frozen_root_and_visible_related_tables(source, catalog, output):
    project, _, _ = source
    catalog["orders"].update(technical_name="renamed_orders", fqn="synthetic.qa.public.renamed_orders", version=2)
    artifact = export(project, command("GRAPH", format=output))
    raw = b"".join(artifact.chunks)
    if output == "svg":
        root = ET.fromstring(raw)
        data = json.loads(root.find("{http://www.w3.org/2000/svg}metadata").text)
    else:
        data = json.loads(raw)
    graph = data["graphs"][0]["graph"]
    assert {node["membership"] for node in graph["nodes"]} == {"QUERY", "RELATED"}
    assert next(node for node in graph["nodes"] if node["membership"] == "QUERY")["technical_name"] == "orders"
    assert data["source"]["catalog_changed_since_snapshot"] is True
    assert graph["relationships"] and graph["legend"]["RELATED"]


def test_query_only_graph_download_excludes_related_nodes(source, catalog):
    project, _, _ = source
    data = packet(export(project, command("GRAPH", view={"positions": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}, "mode": "query", "depth": 2, "list_view": False})))
    assert all(node["membership"] == "QUERY" for node in data["graphs"][0]["graph"]["nodes"])


def candidate(project):
    project.operation_state = "REVIEW"
    project.save()
    thread = Thread.create(id="thread", project_id=project.id, tenant_id=project.tenant_id, base_artifact_id="q" * 32, target="QUERY", anchor={"kind": "WHOLE"})
    payload = {
        "agent_result": {
            "source": {"target": "QUERY", "base_artifact_id": "q" * 32, "base_requirements_artifact_id": "r" * 32, "base_schema_artifact_id": "s" * 32, "thread_id": thread.id, "thread_version": 1},
            "proposal": {"sql": BASE.oracle.sql, "parameters": BASE.bindings(), "checks": {"status": "PASS"}},
        }
    }
    Proposal.create(id="proposal", project_id=project.id, tenant_id=project.tenant_id, job_id="job", kind="REVISION", source_state_version=1, payload=payload, content_hash=_stable_hash(payload))
    return thread


def test_candidate_graph_is_separate_and_explicitly_unaccepted(source, catalog):
    project, _, _ = source
    candidate(project)
    data = packet(export(project, command("GRAPH", proposal_id="proposal")))
    assert len(data["graphs"]) == 2
    assert data["graphs"][1]["label"] == "Предложение — ещё не принято"
    assert data["graphs"][1]["proposal_id"] == "proposal"
    assert Artifact.get_by_id("q" * 32).payload["sql"] == BASE.oracle.sql


@pytest.mark.parametrize("damage", ["thread-version", "thread-closed", "proposal-foreign", "proposal-accepted", "base-changed"])
def test_candidate_export_rejects_stale_or_foreign_proposals(source, catalog, damage):
    project, _, _ = source
    thread = candidate(project)
    if damage == "thread-version":
        thread.version = 2
        thread.save()
    elif damage == "thread-closed":
        thread.status = "RESOLVED"
        thread.save()
    else:
        proposal = Proposal.get_by_id("proposal")
        if damage == "proposal-foreign":
            proposal.tenant_id = "other"
        elif damage == "proposal-accepted":
            proposal.status = "ACCEPTED"
        else:
            proposal.payload["agent_result"]["source"]["base_artifact_id"] = "different-query"
        proposal.save()
    with pytest.raises(ConflictError) as error:
        export(project, command("GRAPH", proposal_id="proposal"))
    assert error.value.code == "SQL_PROPOSAL_STALE"


@pytest.mark.asyncio
async def test_http_export_is_an_attachment_with_private_headers_and_rechecks_revocation(source, route_app, monkeypatch):
    project, _, _ = source
    app, module = route_app
    for table in module.BusinessDocumentSqlAgentService.model_tables():
        if "tenant_id" in table._meta.fields:
            table.update(tenant_id=ACTOR).where(table.tenant_id == project.tenant_id).execute()
    monkeypatch.setattr(module.current_user, "id", ACTOR)
    monkeypatch.setattr(module.current_user, "business_document_role", "READER", raising=False)

    database = Run._meta.database
    connection = database.connection()

    async def dispatch(function, *args, **kwargs):
        def invoke():
            database._state.set_connection(connection)
            try:
                return function(*args, **kwargs)
            finally:
                database._state.reset()

        return await asyncio.to_thread(invoke)

    monkeypatch.setattr(module, "thread_pool_exec", dispatch)
    client = app.test_client()
    url = f"/business-documents/sql-query/projects/{project.id}/export"
    response = await client.post(url, json=deepcopy(command()))
    assert response.status_code == 200, await response.get_data()
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response.headers["Content-Security-Policy"]
    assert (await response.get_json())["sql"] == BASE.oracle.sql

    async def revoked(*_args):
        raise PermissionDeniedError("Source access revoked")

    monkeypatch.setattr(sql_query_schema.BusinessDocumentSqlQuerySchemaService, "load_entities", revoked)
    denied = await client.post(url, json=command())
    assert denied.status_code == 403
    assert "Content-Disposition" not in denied.headers
    invalid = await client.post(url, json=[])
    assert invalid.status_code == 422
    assert "Content-Disposition" not in invalid.headers
