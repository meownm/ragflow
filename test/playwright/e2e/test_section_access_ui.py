"""Direct SPA routes with isolated policy responses; no live data or services."""

from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_navigation_visibility_admin import NavigationApiStub, _install_admin_session


@pytest.mark.p1
@pytest.mark.auth
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/datasets",
        "/dataset/configuration/foreign",
        "/chats",
        "/chat/foreign",
        "/searches",
        "/search/foreign",
        "/source-workspaces",
        "/agents",
        "/agent/foreign",
        "/memories",
        "/openmetadata",
        "/business-documents",
        "/technical-specifications",
        "/document-constructor",
        "/sql-projects",
        "/sql-sources",
        "/files",
        "/transcription",
        "/translator",
    ],
)
def test_direct_links_do_not_mount_closed_sections(page, base_url, path):
    _install_admin_session(page)
    stub = NavigationApiStub(visible_sections=[])
    requests = []

    def respond(route):
        requests.append(urlparse(route.request.url).path)
        stub(route)

    page.route("**/api/v1/**", respond)
    page.goto(base_url.rstrip("/") + path)
    expect(page.get_by_test_id("section-access-denied")).to_be_visible()
    assert "/api/v1/datasets" not in requests
    assert "/api/v1/business-documents" not in requests
    assert "/api/v1/business-documents/sql-query/projects" not in requests
    expect(page.locator("iframe")).to_have_count(0)
