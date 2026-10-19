from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

from api.document_exchange.pages import EvaPageError, EvaPages


@pytest.fixture
def pages():
    remote = {"id": "page", "name": "Загрузка", "code": "TZ-1", "project_id": "project", "version": "v1", "web_url": "https://eva.test/project/Document/TZ-1", "html": "Опубликованный текст"}
    client = Mock(api_base_url="https://eva.test", web_base_url="https://eva.test")
    client.search_documents.return_value = [remote]
    client.get_document_for_edit.return_value = remote
    client.find_documents_by_exact_title.return_value = [remote]

    @contextmanager
    def connection(actor, connector=None):
        assert actor == "author" and connector in {None, "connector"}
        try:
            yield SimpleNamespace(id="connector", name="Eva"), client
        finally:
            client.close()

    def origin(url):
        value = urlparse(url)
        return value.scheme, value.hostname, value.port or 443

    return EvaPages(connection, lambda value: value, origin), client


def test_search_and_exact_title_reads_close_the_connection(pages):
    source, client = pages
    found = source.search_sources("author", "Загрузка")
    assert found["items"][0]["eva_origin"] == ("https", "eva.test", 443)
    assert source.find_title_matches("author", "Загрузка")[0]["id"] == "page"
    assert client.close.call_count == 2


def test_failed_search_closes_the_connection(pages):
    source, client = pages
    client.search_documents.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        source.search_sources("author", "Загрузка")
    client.close.assert_called_once()


def test_url_search_and_connected_read_keep_verified_identity_and_version(pages):
    source, client = pages
    found = source.search_sources("author", "https://eva.test/project/Document/TZ-1?ignored=" + "x" * 600)
    item = found["items"][0]
    assert (item["id"], item["version"]) == ("page", "v1")
    binding, text = source.read_connected_page("author", item)
    assert binding["remote_content_hash"].startswith("sha256:") and text == "Опубликованный текст"
    assert client.close.call_count == 2


def test_foreign_url_cannot_trigger_a_remote_page_read(pages):
    source, client = pages
    with pytest.raises(EvaPageError, match="вне"):
        source.resolve_page_url("author", "https://other.test/project/Document/TZ-1")
    client.search_documents.assert_not_called()
    client.get_document_for_edit.assert_not_called()
    client.close.assert_called_once()


@pytest.mark.parametrize("url", ["https://user:secret@eva.test/page", "https://eva.test:bad/page", "file:///page", "relative"])
def test_invalid_page_url_is_rejected_before_connecting(pages, url):
    source, client = pages
    with pytest.raises(EvaPageError):
        source.resolve_page_url("author", url)
    client.search_documents.assert_not_called()


@pytest.mark.parametrize("query,limit", [("q" * 501, 20), ("q", True), ("q", 101)])
def test_invalid_search_does_not_open_an_eva_connection(pages, query, limit):
    source, client = pages
    with pytest.raises(EvaPageError):
        source.search_sources("author", query, limit)
    client.search_documents.assert_not_called()
