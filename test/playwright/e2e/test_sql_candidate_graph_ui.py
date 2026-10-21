"""Candidate overlays through the actual workbench; transport is controlled."""

import re

import pytest
from playwright.sync_api import expect

from business_documents.sql_query.project_compilation import build_project_compile_command
from business_documents.sql_query.query_specification import compile_query_payload
from test.playwright.e2e.test_sql_workspace_ui import SQL, SqlWorkspaceBrowserStub, _no_query_mutations, _open


@pytest.mark.parametrize("change", ["ADDING", "REMOVING"])
def test_sql_candidate_membership_is_not_presented_as_already_applied(page, base_url, change):
    joined = SQL.replace(" WHERE", " LEFT JOIN public.customers c ON o.customer_id=c.id WHERE")
    current, candidate = (SQL, joined) if change == "ADDING" else (joined, SQL)
    stub = SqlWorkspaceBrowserStub(current, accepted_ids=["orders", "customers"])
    project = stub.sql_agent_project
    artifacts = project["artifacts"]
    parameters = artifacts["query"]["parameters"]
    checked = compile_query_payload(build_project_compile_command(artifacts["requirements"], artifacts["schema"], {"mode": "manual", "sql": candidate, "parameters": parameters, "confirmed_alignment": True}))
    assert checked["status"] == "READY" and checked["guard"]["status"] == "PASS"
    project.update(operation_state="REVIEW", pending_proposal={"id": "visual-revision-1", "kind": "REVISION", "status": "PENDING", "payload": {"agent_result": {
        "proposal": {"sql": candidate, "parameters": parameters, "summary": "Изменить соединение с клиентом", "checks": {"status": "PASS", "guard": checked["guard"]}},
        "source": {"target": "QUERY", "base_artifact_id": "query-1"}}}})
    stub, graph = _open(page, base_url, stub=stub)
    table = graph.locator(f'[data-entity-id="customers"][data-change="{change}"]')
    expect(table).to_have_count(1)
    expect(graph.locator('[data-entity-id="customers"]')).to_have_count(1)
    expect(table).to_have_attribute("data-membership", "RELATED" if change == "ADDING" else "QUERY")
    expect(table).to_contain_text("Предлагается добавить" if change == "ADDING" else "Предлагается удалить")
    expect(table).to_have_class(re.compile(r".*border-dashed.*"))
    assert stub.sql == current
    _no_query_mutations(stub)
