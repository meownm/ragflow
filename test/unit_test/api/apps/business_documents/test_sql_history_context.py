"""Full historical context is lazy, immutable and scoped to all exposed tables."""

from copy import deepcopy
import json

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_history import ACTOR, Revisions, Schema, next_query, project as stored_project, snapshot, source as stored_source
from api.apps.business_documents.sql_query_access import authorize_sql_source
from api.apps.business_documents.sql_query_agents import _stable_hash
from api.db.db_models import BusinessDocumentSqlQueryArtifact as Artifact, BusinessDocumentSqlQueryProject as Project
from business_documents.application.errors import BusinessDocumentError, ConflictError
from business_documents.sql_query.project_compilation import build_project_compile_command
from business_documents.sql_query.query_specification import compile_query_payload


@pytest.fixture
def project():
    yield from stored_project.__wrapped__()


@pytest.fixture
def source(project, monkeypatch):
    yield from stored_source.__wrapped__(project, monkeypatch)


def request(compilation="c" * 32, query="q" * 32):
    return {"schema_version": "1", "compilation_id": compilation, "query_artifact_id": query}


def context(project, raw=None, actor=ACTOR, tenant=None, role="READER"):
    return Revisions.history_context(tenant or project.tenant_id, actor, project.id, request() if raw is None else raw, access_role=role)


def save(artifact):
    artifact.content_hash = _stable_hash(artifact.payload)
    artifact.save()


def test_context_has_complete_old_requirements_answers_schema_and_identity_without_mutation(source):
    project, tables, calls = source
    requirements = Artifact.get_by_id("r" * 32)
    long_text = "Полное условие " + "я" * 1100
    requirements.payload = {"requirements": [{"statement": long_text + str(i)} for i in range(15)], "questions": [{"text": "Уточнение " + str(i), "answer": "Ответ " + "ю" * 1800, "status": "ANSWERED"} for i in range(4)]}
    requirements.accepted_by = "old-author"
    requirements.source_proposal_id = "old-proposal"
    save(requirements)
    schema = Artifact.get_by_id("s" * 32)
    # Unknown transport data are outside the parsed, accepted catalog snapshot.
    schema.payload["schema_snapshot"]["source"]["transport_secret"] = "must-not-be-disclosed"
    schema.payload["schema_snapshot"]["original_requirements"] = "\n".join("- " + item["statement"] for item in requirements.payload["requirements"])
    save(schema)
    compilation = Artifact.get_by_id("c" * 32)
    compilation.payload["result"] = compile_query_payload(build_project_compile_command(requirements.payload, schema.payload, Artifact.get_by_id("q" * 32).payload))
    save(compilation)
    next_query(project)
    project = Project.get_by_id(project.id)
    tables["orders"].update(version=2, fqn="synthetic.qa.public.renamed_orders")
    before = snapshot()
    result = context(project)
    assert result["source"] == {"compilation_id": "c" * 32, "query_artifact_id": "q" * 32, "requirements_artifact_id": "r" * 32, "schema_artifact_id": "s" * 32}
    assert len(json.dumps(result["requirements"], ensure_ascii=False)) > 20_000
    assert result["requirements"]["text"].endswith(long_text + "14")
    assert result["requirements"]["questions"] == requirements.payload["questions"]
    assert result["requirements"]["accepted_by"] == "old-author"
    assert result["requirements"]["content_hash"] == requirements.content_hash
    assert result["requirements"]["source_proposal_id"] == "old-proposal"
    assert len(result["schema"]["snapshot"]["tables"]) == 8
    old_orders = next(table for table in result["schema"]["snapshot"]["tables"] if table["id"] == "orders")
    assert old_orders["technical_name"] == "orders" and old_orders["version"] == 1
    assert len(old_orders["columns"]) == len(tables["orders"]["columns"])
    assert old_orders["table_constraints"]
    assert result["catalog_changed"] is True
    assert "must-not-be-disclosed" not in str(result)
    assert calls[-1][1]["entity_ids"] == sorted(tables)
    assert snapshot() == before


def test_context_remains_readable_in_archive_without_current_artifacts(source):
    project, _, _ = source
    Project.update(archived_at=1234, query_artifact_id=None, schema_artifact_id=None, requirements_artifact_id=None).where(Project.id == project.id).execute()
    before = snapshot()
    assert context(Project.get_by_id(project.id))["requirements"]["id"] == "r" * 32
    assert snapshot() == before


@pytest.mark.parametrize("failure", ["missing", "deleted", "wrong-id", "lookup-error"])
@pytest.mark.parametrize("standalone", [False, True], ids=["compiled", "standalone-schema"])
def test_context_checks_unused_accepted_table_permission_on_every_read(source, monkeypatch, failure, standalone):
    project, tables, _ = source
    context(project)

    async def changed(_actor, command, *_args):
        entities = []
        for identity in command["entity_ids"]:
            entity = deepcopy(tables[identity])
            if identity == "customers":
                if failure == "missing":
                    continue
                entity.update(deleted=failure == "deleted", id="wrong" if failure == "wrong-id" else identity)
            entities.append({"entity_id": identity, "entity": entity, "lookup": {"status": "ERROR" if identity == "customers" and failure == "lookup-error" else "OK"}})
        return {"entities": entities}

    monkeypatch.setattr(Schema, "load_entities", changed)
    before = snapshot()
    # The SQL itself uses orders only and remains readable.
    assert authorize_sql_source(project, ACTOR, "c" * 32).entity_ids == ("orders",)
    with pytest.raises(BusinessDocumentError) as error:
        context(project, {"schema_version": "1", "artifact_id": "s" * 32} if standalone else None)
    assert error.value.status == (503 if failure == "lookup-error" else 403)
    assert snapshot() == before


@pytest.mark.parametrize("case", ["tenant", "owner", "foreign-compilation", "missing-source", "hash", "mismatch"])
def test_context_rejects_unauthorized_or_unverifiable_sources(source, case):
    project, _, calls = source
    if case == "foreign-compilation":
        Artifact.update(tenant_id="foreign").where(Artifact.id == "c" * 32).execute()
    if case == "missing-source":
        Artifact.delete().where(Artifact.id == "s" * 32).execute()
    if case == "hash":
        Artifact.update(payload={"requirements": [{"statement": "changed"}]}).where(Artifact.id == "r" * 32).execute()
    before = snapshot()
    with pytest.raises(BusinessDocumentError) as error:
        context(project, request(query="different") if case == "mismatch" else None, actor="foreign" if case == "owner" else ACTOR, tenant="foreign" if case == "tenant" else None)
    if case == "mismatch":
        assert isinstance(error.value, ConflictError) and error.value.code == "SQL_HISTORY_SOURCE_MISMATCH"
    else:
        assert calls == []
    assert snapshot() == before


@pytest.mark.parametrize("raw", [[], {}, {"schema_version": "2"}, request(query=""), request(query="x" * 33), {**request(), "sql": "SELECT secret"}, {**request(), "requirements_artifact_id": "foreign"}])
def test_context_command_is_closed_and_requires_saved_source_ids(source, raw):
    project, _, calls = source
    before = snapshot()
    with pytest.raises(BusinessDocumentError):
        context(project, raw)
    assert calls == [] and snapshot() == before


@pytest.mark.parametrize("kind,identity", [("REQUIREMENTS", "r" * 32), ("SCHEMA", "s" * 32)])
@pytest.mark.parametrize("role", ["READER", "AUTHOR_CREATOR"])
def test_accepted_artifact_is_readable_without_any_query_or_compilation(source, kind, identity, role):
    project, tables, calls = source
    Artifact.delete().where(Artifact.kind.in_(("QUERY", "COMPILATION"))).execute()
    Project.update(requirements_artifact_id=None, schema_artifact_id=None, query_artifact_id=None, stage="SCHEMA", archived_at=1234).where(Project.id == project.id).execute()
    before = snapshot()
    result = context(project, {"schema_version": "1", "artifact_id": identity}, role=role)
    assert result["source"]["artifact_id"] == identity
    if kind == "REQUIREMENTS":
        assert result["requirements"]["id"] == identity and result["schema"] is None
        assert calls == []
    else:
        assert result["schema"]["id"] == identity and result["requirements"] is None
        assert calls[-1][1]["entity_ids"] == sorted(tables)
    assert snapshot() == before


@pytest.mark.parametrize("case", ["wrong-kind", "foreign-owner", "foreign-tenant", "foreign-artifact", "hash", "invalid-payload", "mixed-selection"])
def test_standalone_artifact_fails_closed_without_mutation(source, case):
    project, _, calls = source
    raw = {"schema_version": "1", "artifact_id": "q" * 32 if case == "wrong-kind" else "r" * 32}
    if case == "foreign-artifact":
        Artifact.update(project_id="foreign").where(Artifact.id == "r" * 32).execute()
    if case in {"hash", "invalid-payload"}:
        artifact = Artifact.get_by_id("r" * 32)
        artifact.payload = {"wrong": "Invalid accepted requirements"}
        if case == "invalid-payload":
            save(artifact)
        else:
            artifact.save()
    if case == "mixed-selection":
        raw.update(compilation_id="c" * 32, query_artifact_id="q" * 32)
    before = snapshot()
    with pytest.raises(BusinessDocumentError):
        context(project, raw, actor="foreign" if case == "foreign-owner" else ACTOR, tenant="foreign" if case == "foreign-tenant" else None)
    assert calls == [] and snapshot() == before
