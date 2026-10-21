"""Human-approved facts reach model planning without changing saved schema identity."""
import json
from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_history import Agents, project as stored_project, source as stored_source
from api.apps.business_documents.sql_query_agents import _stable_hash
from api.db.db_models import BusinessDocumentSqlQueryArtifact as Artifact
from business_documents.sql_query.requirements_analysis import accept_requirements_proposal, normalize_requirements_proposal
from business_documents.sql_query.query_planning import QueryPlanValidationError, parse_plan_query_command, planner_context
from business_documents.sql_query.schema_resolution import SchemaResolutionValidationError, parse_resolve_schema_command
from business_documents.sql_query.query_specification import parse_schema_snapshot
from business_documents.application.errors import ConflictError


@pytest.fixture
def project():
    yield from stored_project.__wrapped__()


@pytest.fixture
def source(project, monkeypatch):
    yield from stored_source.__wrapped__(project, monkeypatch)


ANSWER = '2026, часовой пояс Pacific/Apia'


def accepted_payload():
    proposal = normalize_requirements_proposal({
        'schema_version': '1',
        'requirements': [{'kind': 'filter', 'statement': 'Заказы за согласованный отчётный год'}],
        'questions': [{'question': 'Какой год и часовой пояс?', 'reason': 'Год и часовой пояс не указаны', 'options': ['2025 UTC', '2026 UTC'], 'allow_custom_answer': True, 'blocking': True}],
    })
    return accept_requirements_proposal(proposal, {'Q-001': ANSWER})


@pytest.mark.parametrize('kind', ['SCHEMA', 'QUERY'])
def test_approved_year_and_timezone_are_forwarded_to_the_next_agent(source, kind):
    project, _, _ = source
    artifact = Artifact.get_by_id(project.requirements_artifact_id)
    artifact.payload = accepted_payload()
    artifact.content_hash = _stable_hash(artifact.payload)
    artifact.save()
    request = Agents._job_request(project, kind, {'terms': ['orders']} if kind == 'SCHEMA' else {})
    assert ANSWER in json.dumps(request, ensure_ascii=False)


def test_canonical_requirements_and_accepted_schema_identity_are_not_reinterpreted(source):
    project, _, _ = source
    artifact = Artifact.get_by_id(project.requirements_artifact_id)
    artifact.payload = accepted_payload()
    artifact.content_hash = _stable_hash(artifact.payload)
    artifact.save()
    canonical = '- ' + artifact.payload['requirements'][0]['statement']
    schema = Artifact.get_by_id(project.schema_artifact_id)
    schema.payload['schema_snapshot']['original_requirements'] = canonical
    schema.content_hash = _stable_hash(schema.payload)
    schema.save()
    before = deepcopy((Artifact.get_by_id(artifact.id).__data__, Artifact.get_by_id(schema.id).__data__))
    fingerprint = parse_schema_snapshot(schema.payload['schema_snapshot']).fingerprint
    request = Agents._job_request(project, 'QUERY', {})
    command = parse_plan_query_command(request, tenant_id=project.tenant_id)
    assert command.accepted_requirements == command.snapshot.original_requirements == canonical
    assert command.snapshot.fingerprint == fingerprint
    assert command.clarifications[0].answer == ANSWER
    assert ANSWER in planner_context(command)['requirements']
    assert (Artifact.get_by_id(artifact.id).__data__, Artifact.get_by_id(schema.id).__data__) == before


@pytest.mark.parametrize('kind', ['SCHEMA', 'QUERY'])
def test_malformed_answered_artifact_is_a_domain_error_before_job_work(source, kind):
    project, _, _ = source
    artifact = Artifact.get_by_id(project.requirements_artifact_id)
    artifact.payload = accepted_payload()
    artifact.payload['questions'][0]['answer'] = False
    artifact.content_hash = _stable_hash(artifact.payload)
    artifact.save()
    before = deepcopy(Artifact.get_by_id(artifact.id).__data__)
    with pytest.raises(ConflictError) as error:
        Agents._job_request(project, kind, {'terms': ['orders']} if kind == 'SCHEMA' else {})
    assert error.value.code == 'SQL_REQUIREMENTS_ARTIFACT_INVALID'
    assert Artifact.get_by_id(artifact.id).__data__ == before


@pytest.mark.parametrize('bad', [None, [{'question': 'q', 'answer': False}], [{'question': 'q', 'answer': 'a', 'extra': True}]])
@pytest.mark.parametrize('kind', ['SCHEMA', 'QUERY'])
def test_transport_parsers_reject_bad_clarifications_with_their_own_error(source, bad, kind):
    project, _, _ = source
    request = Agents._job_request(project, kind, {'terms': ['orders']} if kind == 'SCHEMA' else {})
    request['clarifications'] = bad
    with pytest.raises(SchemaResolutionValidationError if kind == 'SCHEMA' else QueryPlanValidationError):
        if kind == 'SCHEMA':
            parse_resolve_schema_command(request, tenant_id=project.tenant_id, actor_id=project.owner_id)
        else:
            parse_plan_query_command(request, tenant_id=project.tenant_id)
