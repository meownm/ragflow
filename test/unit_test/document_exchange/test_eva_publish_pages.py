from contextlib import contextmanager
from copy import deepcopy

import pytest

from api.document_exchange.content import content_hash
from api.document_exchange.pages import EvaPageError, EvaPages


@pytest.fixture
def exchange():
    class Client:
        api_base_url = "https://eva.test"

        def __init__(self):
            self.remote = {"id": "page", "project_id": "project", "version": "v1", "name": "Before", "html": "Before", "draft_html": "Before"}
            self.saves, self.publishes, self.closed = 0, 0, 0
            self.after_save = lambda: None
            self.after_publish = lambda: None

        def get_document_for_edit(self, page):
            assert page == "page"
            return deepcopy(self.remote)

        def update_document_draft(self, page, html, name):
            assert page == "page"
            self.saves += 1
            self.remote.update(draft_html=html, name=name, version="v2")
            self.after_save()

        def publish_document(self, page):
            assert page == "page"
            self.publishes += 1
            self.remote.update(html=self.remote["draft_html"], version="v3")
            self.after_publish()

    client = Client()

    @contextmanager
    def write(actor, connector):
        assert (actor, connector) == ("actor", "connector")
        try:
            yield None, client, client
        finally:
            client.closed += 1

    pages = EvaPages(None, lambda value: value, lambda url: ["https", "eva.test", 443], write_connection=write, encode_markdown=lambda value, _reader: value)
    binding = {"document_id": "page", "project_id": "project", "connector_id": "connector", "remote_version": "v1", "remote_content_hash": content_hash("Before"), "page_url": "https://eva.test/page"}
    return pages, client, binding


def test_same_page_update_and_repeat_do_not_create_another_publication(exchange):
    pages, client, binding = exchange
    result = pages.publish_connected_page("actor", binding, "After", "After")
    assert result["document_id"] == "page" and result["remote_version"] == "v3"
    assert result["remote_content_hash"] == content_hash("After")
    assert pages.publish_connected_page("actor", binding, "After", "After") == result
    assert (client.saves, client.publishes, client.closed) == (1, 1, 2)


@pytest.mark.parametrize("field,value", [("version", "new"), ("html", "Foreign change"), ("draft_html", "Foreign draft"), ("id", "other"), ("project_id", "other")])
def test_conflict_before_write_leaves_eva_untouched(exchange, field, value):
    pages, client, binding = exchange
    client.remote[field] = value
    original = deepcopy(client.remote)
    with pytest.raises(EvaPageError):
        pages.publish_connected_page("actor", binding, "After", "After")
    assert client.remote == original
    assert (client.saves, client.publishes, client.closed) == (0, 0, 1)


@pytest.mark.parametrize("field", ["html", "draft_html", "name", "id", "project_id"])
def test_change_during_draft_save_prevents_publication(exchange, field):
    pages, client, binding = exchange
    client.after_save = lambda: client.remote.update({field: "Foreign change"})
    with pytest.raises(EvaPageError):
        pages.publish_connected_page("actor", binding, "After", "After")
    assert (client.saves, client.publishes, client.closed) == (1, 0, 1)


@pytest.mark.parametrize("field", ["html", "name", "id"])
def test_unexpected_result_is_never_reported_as_success(exchange, field):
    pages, client, binding = exchange
    client.after_publish = lambda: client.remote.update({field: "Unexpected"})
    with pytest.raises(EvaPageError):
        pages.publish_connected_page("actor", binding, "After", "After")
    assert client.publishes == 1 and client.closed == 1


def test_lost_publication_response_is_checked_before_manual_retry(exchange):
    pages, client, binding = exchange

    def lost():
        raise TimeoutError("Response lost after publishing")

    client.after_publish = lost
    with pytest.raises(TimeoutError):
        pages.publish_connected_page("actor", binding, "After", "After")
    result = pages.publish_connected_page("actor", binding, "After", "After")
    assert result["remote_version"] == "v3"
    assert client.saves == client.publishes == 1
