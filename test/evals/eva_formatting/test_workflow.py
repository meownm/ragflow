"""Approved change -> saved EVA draft -> publish -> connected-page import.

The existing EVA workflow fixtures own an in-memory SQLite database and fake
EVA page. These checks need neither application tables nor an LLM provider.
"""

from unittest.mock import patch

import pytest

from test.evals.eva_formatting.cases import MARKDOWN_CASES
from test.evals.eva_formatting.test_roundtrip import assert_formatting, _markdown_to_html
from test.unit_test.api.apps.business_documents.test_eva_document_changes import (
    AUTHOR,
    CONNECTOR,
    TENANT,
    FakeEvaClient,
    EvaDocumentChangeService,
    configured_documents_eva_space as configured_documents_eva_space,
    database as database,
    personal_mutation_client as personal_mutation_client,
    personal_read_credential as personal_read_credential,
)


@pytest.mark.parametrize("case", MARKDOWN_CASES, ids=lambda case: case.id)
@pytest.mark.usefixtures("database")
def test_approved_formatting_is_reloaded_without_loss(case):
    client = FakeEvaClient()
    CONNECTOR.mutation_client = client
    with patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)):
        created = EvaDocumentChangeService.create_change(
            TENANT,
            AUTHOR,
            {"connector_id": CONNECTOR.id, "document_id": client.document["id"], "change_summary": "Synthetic formatting regression", "draft_markdown": case.markdown},
            allow_prefilled_draft=True,
        )
        assert client.document["draft_html"] == "" and client.publish_calls == 0
        approved = EvaDocumentChangeService.approve(TENANT, AUTHOR, created["change_id"], {"expected_state_version": created["state_version"]})
        original_html = client.document["html"]
        prepared = EvaDocumentChangeService.prepare_eva_draft(TENANT, AUTHOR, created["change_id"], {"expected_state_version": approved["state_version"]})
        assert client.document["html"] == original_html and client.publish_calls == 0
        published = EvaDocumentChangeService.publish(TENANT, AUTHOR, created["change_id"], {"expected_state_version": prepared["state_version"]})
        assert published["workflow_state"] == "PUBLISHED" and client.publish_calls == 1
        binding, markdown = EvaDocumentChangeService.read_connected_page(AUTHOR, {"connector_id": CONNECTOR.id, "document_id": client.document["id"]})
        assert binding["document_id"] == client.document["id"]
        assert_formatting(_markdown_to_html(markdown), case.html, stage="Approved draft -> publish -> connected-page import")
