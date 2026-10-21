"""Frozen SQL lineage is readable only while its actual sources remain visible."""

from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_revisions import ACTOR, project as accepted_project
from api.apps.business_documents.sql_query_access import authorize_sql_source
from api.apps.business_documents.sql_query_agents import _stable_hash
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService as Schema
from api.db.db_models import BusinessDocumentSqlQueryArtifact as Artifact, BusinessDocumentSqlQueryProject as Project
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError
from business_documents.sql_query.project_compilation import build_project_compile_command
from business_documents.sql_query.query_specification import compile_query_payload
from test.evals.sql_projects.golden import BASE
from test.evals.sql_projects.schema import accepted_requirements, accepted_schema


@pytest.fixture
def project():
    yield from accepted_project.__wrapped__()


@pytest.fixture
def source(project, monkeypatch):
    project_id = project
    requirements, schema = accepted_requirements(), accepted_schema()
    query = {"mode": "manual", "sql": BASE.oracle.sql, "parameters": BASE.bindings(), "confirmed_alignment": True}
    compiled = compile_query_payload(build_project_compile_command(requirements, schema, query))
    assert compiled["status"] == "READY"
    values = {"r" * 32: requirements, "s" * 32: schema, "q" * 32: query, "c" * 32: {"source_artifact_ids": ["r" * 32, "s" * 32, "q" * 32], "result": compiled}}
    for identity, payload in values.items():
        Artifact.update(payload=payload, content_hash=_stable_hash(payload)).where(Artifact.id == identity).execute()
    tables = {item["selected_table"]["id"]: deepcopy(item["selected_table"]) for item in schema["schema_snapshot"]["requirements"]}
    calls = []

    async def visible(actor, request, is_admin, role):
        calls.append((actor, deepcopy(request), is_admin, role))
        return {"entities": [{"entity_id": identity, "lookup": {"status": "OK"}, "entity": tables[identity]} for identity in request["entity_ids"]]}

    monkeypatch.setattr(Schema, "load_entities", visible)
    yield Project.get_by_id(project_id), tables, calls


def test_owner_reader_checks_only_physical_query_sources_not_unused_accepted_entities(source):
    project, _, calls = source
    result = authorize_sql_source(project, ACTOR, "c" * 32, access_role="READER")
    assert result.entity_ids == ("orders",)
    assert result.compilation.id == "c" * 32
    assert result.query.payload["sql"] == BASE.oracle.sql
    assert result.catalog_changed is False
    assert calls == [(ACTOR, {"entity_ids": ["orders"], "locale": "en"}, False, "READER")]


def test_old_schema_and_query_remain_readable_when_current_project_and_catalog_versions_change(source):
    project, tables, _ = source
    project.query_artifact_id = "new-query"
    project.schema_artifact_id = "new-schema"
    tables["orders"].update(version=2, schema_fingerprint="sha256:new", fqn="synthetic.qa.public.renamed_orders")
    result = authorize_sql_source(project, ACTOR, "c" * 32)
    assert result.query.id == "q" * 32
    assert result.schema.id == "s" * 32
    assert result.catalog_changed is True
    assert result.compiled["parameters"] == BASE.values()


def test_current_catalog_revocation_is_not_cached_after_a_successful_read(source, monkeypatch):
    project, _, _ = source
    authorize_sql_source(project, ACTOR, "c" * 32)

    async def revoked(*_args):
        raise PermissionDeniedError("Catalog access revoked")

    monkeypatch.setattr(Schema, "load_entities", revoked)
    with pytest.raises(PermissionDeniedError):
        authorize_sql_source(project, ACTOR, "c" * 32)


@pytest.mark.parametrize(
    "damage", ["foreign-compilation", "missing-source", "foreign-schema", "wrong-kind", "changed-query", "changed-compilation", "missing-guard", "compilation-list", "schema-list"]
)
def test_invalid_or_foreign_source_chain_is_rejected_before_catalog_io(source, damage):
    project, _, calls = source
    if damage == "foreign-compilation":
        Artifact.update(tenant_id="different-tenant").where(Artifact.id == "c" * 32).execute()
    elif damage == "missing-source":
        Artifact.delete().where(Artifact.id == "q" * 32).execute()
    elif damage == "foreign-schema":
        Artifact.update(project_id="different-project").where(Artifact.id == "s" * 32).execute()
    elif damage == "wrong-kind":
        Artifact.update(kind="DOCUMENT").where(Artifact.id == "q" * 32).execute()
    elif damage in {"compilation-list", "schema-list"}:
        Artifact.update(payload=[]).where(Artifact.id == ("c" * 32 if damage == "compilation-list" else "s" * 32)).execute()
    else:
        identity = "q" * 32 if damage == "changed-query" else "c" * 32
        artifact = Artifact.get_by_id(identity)
        if damage == "changed-query":
            artifact.payload["sql"] = BASE.oracle.sql.replace("o.id", "o.amount")
        elif damage == "changed-compilation":
            artifact.payload["result"]["parameters"]["paid"] = "cancelled"
        else:
            artifact.payload["result"]["guard"] = {}
        artifact.save()
    with pytest.raises(ConflictError) as error:
        authorize_sql_source(project, ACTOR, "c" * 32)
    assert error.value.code == "SQL_RESULT_SOURCE_UNAVAILABLE"
    assert calls == []


@pytest.mark.parametrize("failure,status", [("missing", 403), ("deleted", 403), ("wrong-id", 403), ("lookup-error", 503), ("network", 503)])
def test_catalog_failures_never_release_an_unverified_source(source, monkeypatch, failure, status):
    project, tables, _ = source

    async def unavailable(*_args):
        if failure == "network":
            raise BusinessDocumentError("SQL_SCHEMA_CATALOG_UNAVAILABLE", "Unavailable", 503)
        if failure == "missing":
            return {"entities": []}
        entity = deepcopy(tables["orders"])
        if failure == "deleted":
            entity["deleted"] = True
        elif failure == "wrong-id":
            entity["id"] = "customers"
        return {"entities": [{"entity_id": "orders", "lookup": {"status": "ERROR" if failure == "lookup-error" else "OK"}, "entity": entity}]}

    monkeypatch.setattr(Schema, "load_entities", unavailable)
    with pytest.raises(BusinessDocumentError) as error:
        authorize_sql_source(project, ACTOR, "c" * 32)
    assert error.value.status == status


def test_lookup_result_also_requires_current_access_to_its_enrichment_source(source):
    project, _, calls = source
    result = authorize_sql_source(project, ACTOR, "c" * 32, extra_entity_ids=("customers",))
    assert result.entity_ids == ("customers", "orders")
    assert calls[0][1]["entity_ids"] == ["customers", "orders"]
