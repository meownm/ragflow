"""View persistence and metric values use the existing result access boundary."""

# Imported pytest fixtures are used by their registered names.
# ruff: noqa: F811
import pytest

from test.unit_test.api.apps.business_documents.test_sql_assistant_service import command, research, source, project  # noqa: F401
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlQueryRun, SqlAssistantCommand
from business_documents.application.errors import BusinessDocumentError, ConflictError


def test_result_builder_persists_view_and_computes_across_all_pages(research):
    service, session, run, _, _ = research
    result = service.result(session.id, run.id)
    view = result["view"]
    view["appearance"].update(density="compact", wrap=True)
    view["labels"][result["columns"][0]["id"]] = "Показатель"
    view["metrics"] = [{"id": "count", "title": "Сохранённые строки", "kind": "rows", "column_id": None, "row_index": None}]
    payload = {"run_id": run.id, "expected_view_revision": 0, "view": view}
    command(service, session, "update_view", payload, key="builder")
    saved = service.result(session.id, run.id, 100)
    assert saved["view"]["appearance"]["wrap"] is True
    assert saved["metrics"][0]["value"] == "250"
    assert saved["sql"] == result["sql"] and saved["parameters"] == result["parameters"]
    assert saved["expires_at"] == result["expires_at"]
    assert BusinessDocumentJob.select().count() == 0
    assert "value" not in SqlAssistantCommand.get().effect
    with pytest.raises(ConflictError):
        command(service, session, "update_view", payload, key="late")
    BusinessDocumentSqlQueryRun.update(status="EXPIRED", rows=[]).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
    with pytest.raises(BusinessDocumentError):
        service.result(session.id, run.id)


def test_cell_metric_rejects_a_nonexistent_source_row(research):
    service, session, run, _, _ = research
    result = service.result(session.id, run.id)
    result["view"]["metrics"] = [{"id": "v", "title": "Значение", "kind": "value", "column_id": result["columns"][0]["id"], "row_index": 999}]
    with pytest.raises(BusinessDocumentError):
        command(service, session, "update_view", {"run_id": run.id, "expected_view_revision": 0, "view": result["view"]})


def test_legacy_chart_update_preserves_display_and_source(research):
    service, session, run, _, _ = research
    original = service.result(session.id, run.id)
    view = original["view"]
    view.pop("appearance")
    view.pop("metrics")
    view["chart"] = {"kind": "bar", "x": original["columns"][0]["id"], "y": original["columns"][0]["id"]}
    command(service, session, "update_view", {"run_id": run.id, "expected_view_revision": 0, "view": view})
    saved = service.result(session.id, run.id)
    assert saved["view"]["appearance"]["display"] == "both"
    assert saved["view"]["chart"] == view["chart"]
    assert saved["expires_at"] == original["expires_at"] and saved["sql"] == original["sql"]
    assert BusinessDocumentJob.select().count() == 0
