"""Context, discovery and typed objects share real persistence/authorization gates."""

# Imported pytest fixtures are intentionally requested by their registered names.
# ruff: noqa: F811

import pytest
from copy import deepcopy

from test.unit_test.api.apps.business_documents.test_sql_assistant_service import ACTOR, TENANT, approved, command, project, research, source  # noqa: F401
from test.unit_test.api.apps.business_documents.test_sql_result_dialogue import dialogue  # noqa: F401
from api.apps.sql_assistant import answer as adapter
from api.apps.sql_assistant.dialogue import resolve_catalog_references
from api.apps.sql_assistant.service import SqlAssistantService
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlQueryArtifact, BusinessDocumentSqlQueryProject, BusinessDocumentSqlQueryRun, SqlAssistantSession
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError
from business_documents.sql_dialogue import metric_matches, reference_keys


def test_stale_context_is_rejected_before_model(dialogue):
    service, session, run, calls = dialogue
    context = service.result(session.id, run.id)["dialogue_context"]
    context["parameters_hash"] = "forged"
    with pytest.raises(ConflictError) as failure:
        command(service, session, "answer_result", {"run_id": run.id, "text": "Значение?", "context": context})
    assert failure.value.code == "SQL_DIALOGUE_CONTEXT_CHANGED"
    assert not calls


def test_selected_row_beyond_sample_is_pinned_and_persisted(dialogue, monkeypatch):
    service, session, run, calls = dialogue
    column = service.result(session.id, run.id)["columns"][0]["id"]

    def answer(_tenant, context):
        assert context["dialogue"]["selected_row"] == {"row_index": 199, "dimension": column}
        assert next(row for row in context["rows"] if row["index"] == 199)["cells"][column]["value"] == "199"
        return {"kind": "ANSWER", "text": f"В выбранной строке: {{{{199:{column}}}}}."}

    monkeypatch.setattr(adapter, "plan_answer", answer)
    result = command(service, session, "answer_result", {"run_id": run.id, "text": "Выбранная строка?", "row_index": 199, "dimension": column})
    assert result["session"]["messages"][-1]["answer"]["text"] == "В выбранной строке: 199."
    assert service.get(session.id)["messages"][-1]["answer"]["citations"][0]["row_index"] == 199
    assert BusinessDocumentJob.select().count() == 0


def test_byte_budget_keeps_selected_row_and_explicit_first_index():
    from business_documents.sql_assistant import default_view, output_columns
    from business_documents.sql_result_answer import answer_context

    columns = output_columns("SELECT value AS value FROM t", ["value"])
    rows = [["я" * 500] for _ in range(200)]
    metadata = {"selected_row": {"row_index": 199, "dimension": columns[0]["id"]}}
    context = answer_context("Выбранная строка?", "run", rows, columns, default_view(columns), "SELECT value FROM t", {}, True, metadata=metadata)
    assert context["rows"][0]["index"] == 199
    assert context["view_first_row_index"] == 0
    assert context["context_limited"]


def test_options_and_mentions_use_current_accepted_identities(approved):
    service, session, run, definition, _, _ = approved
    options = service.dialogue_options(session.id, "Покажи сумму по клиентам")
    assert [item["id"] for item in options["metrics"]] == [definition["definition_id"]]
    table = next(item for item in options["references"] if item["kind"] == "TABLE")
    assert table["id"] and table["fqn"]
    with pytest.raises(BusinessDocumentError):
        SqlAssistantService("foreign", ACTOR).dialogue_options(session.id)


def test_metric_match_requires_explicit_choice_before_sql_job(approved):
    service, session, run, definition, _, _ = approved
    command(service, session, "select_result", {"run_id": run.id})
    before = BusinessDocumentSqlQueryArtifact.select().count()
    payload = {"run_id": run.id, "text": "Сумма по клиентам за другой период"}
    with pytest.raises(BusinessDocumentError) as failure:
        command(service, session, "follow_up", payload, key="metric-question")
    assert failure.value.code == "SQL_METRIC_SELECTION_REQUIRED"
    assert failure.value.details["metrics"][0]["id"] == definition["definition_id"]
    assert BusinessDocumentJob.select().count() == 0
    assert BusinessDocumentSqlQueryArtifact.select().count() == before
    response = command(service, session, "follow_up", {**payload, "metric_resolution": "EXPLORATORY"}, key="explore")
    assert response["session"]["mode"] == "EXPLORATORY"
    assert BusinessDocumentJob.get().job_type == "SQL_AGENT_REVISION"


def test_definition_can_prepare_first_query_without_model_or_execution(approved):
    service, session, _, definition, _, _ = approved
    BusinessDocumentSqlQueryProject.update(query_artifact_id=None, stage="QUERY").where(BusinessDocumentSqlQueryProject.id == session.id).execute()
    response = command(service, session, "use_definition", {"definition_id": definition["definition_id"], "version": definition["version"]}, key="first-query")
    assert response["session"]["draft"]["base_query_artifact_id"] is None
    assert response["session"]["draft"]["mode"] == "AGREED"
    assert not BusinessDocumentJob.select().count()
    applied = command(service, session, "apply_draft", {"confirmed": True, "execute": False}, key="apply-first")
    assert applied["session"]["definition_status"] == "DEFINITION_VALIDATED"
    assert applied["session"]["project"]["artifact_ids"]["query"]
    assert not BusinessDocumentJob.select().count()


@pytest.mark.parametrize(
    "bad",
    [
        [{"kind": "TABLE", "id": "foreign"}],
        [{"kind": "TABLE", "id": "orders", "title": "fake"}],
        [{"kind": "DEFINITION", "id": "foreign", "version": 1, "content_hash": "fake"}],
    ],
)
def test_forged_references_never_reach_model(dialogue, bad):
    service, session, run, calls = dialogue
    with pytest.raises(BusinessDocumentError):
        command(service, session, "answer_result", {"run_id": run.id, "text": "Значение?", "references": bad})
    assert not calls


def test_definition_version_is_rechecked_during_inference(approved, monkeypatch):
    service, session, run, definition, payload, _ = approved
    command(service, session, "select_result", {"run_id": run.id})
    ref = {"kind": "DEFINITION", "id": definition["definition_id"], "version": definition["version"], "content_hash": definition["content_hash"]}

    def answer(_tenant, _context):
        assert not session._meta.database.in_transaction()
        command(service, session, "publish_definition", {**payload, "definition_id": definition["definition_id"], "expected_version": 1}, key="supersede")
        return {"kind": "OVERVIEW", "text": ""}

    monkeypatch.setattr(adapter, "plan_answer", answer)
    with pytest.raises(ConflictError):
        command(service, session, "answer_result", {"run_id": run.id, "text": "Сумма?", "references": [ref]}, key="race")
    assert len(SqlAssistantSession.get_by_id(session.id).state["messages"]) == 1


def test_result_cards_expire_and_never_store_rows(research):
    service, session, run, _, _ = research
    command(service, session, "select_result", {"run_id": run.id})
    cards = service.get(session.id)["cards"]
    assert {item["kind"] for item in cards} == {"RESULT", "SOURCE"}
    assert cards[0]["context"] == service.result(session.id, run.id)["dialogue_context"]
    assert {item["entity_id"] for item in cards if item["kind"] == "SOURCE"} == set(service.runtime.result(session.id, run.id)[1].entity_ids)
    assert "cards" not in SqlAssistantSession.get_by_id(session.id).state
    BusinessDocumentSqlQueryRun.update(status="EXPIRED", rows=[]).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
    assert service.get(session.id)["cards"] == []


def test_initial_mentions_resolve_ids_and_reject_client_names(research):
    service, _, _, tables, _ = research
    entity = next(iter(tables.values()))
    refs = resolve_catalog_references(service.runtime, [{"kind": "TABLE", "id": entity["id"]}])
    assert refs[0]["title"] == entity["fqn"]
    with pytest.raises(ValueError):
        resolve_catalog_references(service.runtime, [{"kind": "TABLE", "id": entity["id"], "title": "fake"}])


@pytest.mark.parametrize("failure", [TimeoutError("private"), PermissionDeniedError("denied")])
def test_infrastructure_and_denial_are_not_retried(failure):
    class Model:
        calls = 0

        def generate(self, *_args, **_kwargs):
            self.calls += 1
            raise failure

    model = Model()
    with pytest.raises(BusinessDocumentError) as error:
        adapter.plan_answer(TENANT, {}, llm=model)
    assert model.calls == 1
    assert "private" not in error.value.message
    assert error.value.status == (403 if isinstance(failure, PermissionDeniedError) else 503)


@pytest.mark.parametrize("repair", [True, False])
def test_invalid_model_format_has_one_bounded_repair(repair):
    class Model:
        calls = 0

        def generate(self, _tenant, _system, data, **kwargs):
            self.calls += 1
            assert 0 < kwargs["timeout_seconds"] <= 60
            if self.calls == 2:
                assert data["format_correction"]
                assert data["format_correction"]["previous_response"] == "invalid JSON"
                assert data["format_correction"]["reason"]
                if repair:
                    return {"kind": "OVERVIEW", "text": ""}
            return "invalid JSON"

    model = Model()
    if repair:
        assert adapter.plan_answer(TENANT, {}, llm=model)["kind"] == "OVERVIEW"
    else:
        with pytest.raises(BusinessDocumentError) as error:
            adapter.plan_answer(TENANT, {}, llm=model)
        assert error.value.code == "SQL_RESULT_ANSWER_INVALID"
    assert model.calls == 2


def test_metric_discovery_keeps_ambiguity_and_excludes_retired():
    definitions = [
        {"title": title, "definition_id": str(index), "freshness": status}
        for index, (title, status) in enumerate([("Выручка", "CURRENT"), ("Выручка после возвратов", "CURRENT"), ("Выручка", "RETIRED")])
    ]
    assert len(metric_matches("Сравни выручку", definitions)) == 2
    with pytest.raises(ValueError):
        reference_keys([{"kind": "TABLE", "id": "x"}] * 2)


@pytest.mark.parametrize("failure", [PermissionDeniedError("Denied"), TimeoutError("private infrastructure diagnostic")])
def test_assistant_background_failure_finishes_without_automatic_retry(dialogue, failure):
    from api.apps.business_documents.worker import BusinessDocumentWorker
    from api.db.db_models import BusinessDocument, BusinessDocumentWorkRequest

    service, session, run, _ = dialogue
    command(service, session, "follow_up", {"run_id": run.id, "text": "Добавь разрез"}, key="worker-change")

    class Runner:
        def process(self, _job):
            raise failure

    database = session._meta.database
    with database.bind_ctx((BusinessDocument, BusinessDocumentWorkRequest), bind_refs=False, bind_backrefs=False):
        database.create_tables((BusinessDocument, BusinessDocumentWorkRequest))
        worker = BusinessDocumentWorker(worker_id="dialogue-worker", sql_agent_runner=Runner())
        assert worker.run_once()
        job = BusinessDocumentJob.get()
        assert job.status == "DEAD" and job.attempt == 1
        current = service.get(session.id)["project"]
        assert current["operation_state"] != "RUNNING"
        assert "private" not in str(current["last_error"])
        assert not worker.run_once()


def test_reading_long_history_does_not_repeat_catalog_or_registry_io(research, monkeypatch):
    service, session, run, _, catalog_calls = research
    command(service, session, "select_result", {"run_id": run.id})
    table = next(item for item in service.dialogue_options(session.id)["references"] if item["kind"] == "TABLE")
    monkeypatch.setattr(adapter, "plan_answer", lambda *_args: {"kind": "OVERVIEW", "text": ""})
    command(service, session, "answer_result", {"run_id": run.id, "text": "Обзор", "references": [{"kind": "TABLE", "id": table["id"]}]}, key="history-answer")
    stored = SqlAssistantSession.get_by_id(session.id)
    template = stored.state["messages"][-1]
    state = deepcopy(stored.state)
    state["messages"] = [{**template, "id": str(index)} for index in range(50)]
    SqlAssistantSession.update(state=state).where(SqlAssistantSession.id == session.id).execute()
    catalog_calls.clear()
    queries = []
    connection = session._meta.database.connection()
    connection.set_trace_callback(queries.append)
    try:
        response = service.get(session.id)
    finally:
        connection.set_trace_callback(None)
    assert len(response["messages"]) == 50
    assert all(message["answer"]["status"] == "READY" for message in response["messages"])
    assert len(catalog_calls) <= 4
    assert sum('FROM "sql_assistant_definition"' in query for query in queries) == 1
