"""Explicitly enabled local EVA contour; creates and removes one private QA page."""

import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from test.evals.eva_formatting.cases import ALL_CASES, MARKDOWN_CASES
from test.evals.eva_formatting.test_roundtrip import assert_formatting, _html_to_markdown, _markdown_to_html
from api.apps.business_documents.html_formatting import prepare_eva_html


@pytest.fixture(scope="module")
def eva_page(record_testsuite_property):
    if os.environ.get("RAGFLOW_LOCAL_DOCKER_CONNECTOR_TEST") != "1":
        pytest.skip("Requires explicit local non-production EVA contour opt-in")
    from api.db.db_models import Connector
    from common.data_source.eva_wiki_connector import EvaWikiConnector, EvaWikiMutationClient

    row = Connector.get_or_none(Connector.name == "EVA Wiki native smoke")
    assert row is not None and row.source == "eva_wiki", "Dedicated local QA EVA connector is missing"
    config = dict(row.config)
    assert urlsplit(config["api_base_url"]).hostname in {"host.docker.internal", "eva-wiki-http", "eva-wiki", "localhost", "127.0.0.1"}, "The QA connector must target the local EVA contour"
    config["retry_count"] = 0
    credentials = config.pop("credentials", None)
    assert isinstance(credentials, dict) and credentials.get("eva_api_token"), "Local QA EVA credentials are missing"
    config.pop("sync_deleted_files", None)
    reader = EvaWikiConnector(**config)
    reader.load_credentials(credentials)
    writer = EvaWikiMutationClient(api_base_url=config["api_base_url"], project_id=config["project_id"], verify_ssl=config.get("verify_ssl", True), retry_count=0)
    writer.load_credentials(credentials)
    key = uuid4().hex[:12]
    name = f"regression-probe-formatting-{key}"
    # A unique page is the only entity this suite is allowed to change.
    existing = list(reader._iter_entities("CmfDocument", ["id"], extra_filters=[["name", "==", name]]))
    assert not existing
    page_id = reader._rpc("CmfDocument.create", {}, call_kwargs={"project": config["project_id"], "name": name, "code": f"FMT-{key}", "text_draft": "<p>Synthetic baseline</p>"})
    assert isinstance(page_id, str) and page_id.startswith("CmfDocument:"), "Unexpected synthetic page identifier"
    try:
        writer.publish_document(page_id)
        assert "Synthetic baseline" in reader.get_document_for_edit(page_id)["html"]
        yield reader, writer, page_id
    finally:
        reader._rpc("CmfDocument.delete", {}, args=[page_id])
        assert reader._get_entity_by_id("CmfDocument", reader._EDITABLE_DOCUMENT_FIELDS, page_id) is None, "Synthetic EVA page cleanup failed"
        record_testsuite_property("eva_temporary_page_cleanup", "verified_absent")


@pytest.mark.parametrize("case", MARKDOWN_CASES, ids=lambda case: case.id)
def test_live_markdown_draft_publish_and_reload(eva_page, case):
    reader, writer, page_id = eva_page
    sent = prepare_eva_html(_markdown_to_html(case.markdown))
    before = reader.get_document_for_edit(page_id)
    writer.update_document_draft(page_id, sent)
    saved = reader.get_document_for_edit(page_id)
    # Saving must not implicitly publish; perform publication even if formatting
    # comparison later fails, so every case exercises all three boundaries.
    assert saved["html"] == before["html"]
    writer.publish_document(page_id)
    published = reader.get_document_for_edit(page_id)
    failures = []
    for stage, actual in (("draft", saved["draft_html"]), ("published", published["html"]), ("reimport", _markdown_to_html(_html_to_markdown(published["html"])))):
        try:
            assert_formatting(actual, case.html, stage=stage)
        except AssertionError as error:
            failures.append(str(error))
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.id)
def test_live_native_eva_html_import(eva_page, case):
    reader, writer, page_id = eva_page
    writer.update_document_draft(page_id, prepare_eva_html(case.html))
    writer.publish_document(page_id)
    remote = reader.get_document_for_edit(page_id)
    assert_formatting(remote["html"], case.html, stage="Native HTML through EVA")
    assert_formatting(_markdown_to_html(_html_to_markdown(remote["html"])), case.html, stage="Native EVA -> Markdown -> HTML")


def test_live_governed_heading_anchors(eva_page):
    from bs4 import BeautifulSoup
    from api.apps.business_documents.assets import import_document_markdown, published_template
    from api.apps.business_documents.exports import BusinessDocumentExportService

    reader, writer, page_id = eva_page
    document = {
        "sections": [{"id": section["id"], "title": section["title"], "blocks": [{"type": "paragraph", "text": f"Synthetic section {section['id']}"}]} for section in published_template()["sections"]]
    }
    original = BeautifulSoup(BusinessDocumentExportService._render_eva_wiki(document, 1), "html.parser")
    original.find("h2")["id"] = "section"
    link = original.new_tag("a", href="#section")
    link.string = "Jump"
    original.find("h2").insert_after(link)
    writer.update_document_draft(page_id, prepare_eva_html(str(original)))
    writer.publish_document(page_id)
    remote = reader.get_document_for_edit(page_id)
    imported = import_document_markdown(_html_to_markdown(remote["html"]))
    exported = BeautifulSoup(BusinessDocumentExportService._render_eva_wiki(imported, 2), "html.parser")
    assert exported.find("h2", id="section") is not None
    assert exported.find("a", href="#section").text == "Jump"
