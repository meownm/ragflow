"""Opt-in read-only acceptance of the installed local dashboard and admin API.

Requires dedicated QA admin/reader tokens in environment variables. This lane
does not enqueue inference, alter accounts, or modify business documents.
"""

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.fixture(scope="module")
def live():
    if os.environ.get("DOCUMENT_QUALITY_LIVE") != "1":
        pytest.skip("Explicit local QA acceptance opt-in required")
    base = os.environ.get("DOCUMENT_QUALITY_LIVE_URL", "http://127.0.0.1:9380")
    address = urlsplit(base)
    if address.hostname not in {"localhost", "127.0.0.1", "::1"} or address.scheme != "http" or address.path not in {"", "/"}:
        pytest.fail("This lane requires the explicitly authorized loopback runtime")
    tokens = {role: os.environ.get(f"DOCUMENT_QUALITY_{role.upper()}_TOKEN") for role in ("admin", "reader")}
    if not all(tokens.values()):
        pytest.fail("Dedicated QA admin and reader credentials are required")
    return base.rstrip("/"), tokens


def fetch(base, path, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    try:
        with urlopen(Request(base + path, headers=headers), timeout=30) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        status = error.code
        error.close()
        return status, None


def test_live_quality_api_authorization_and_product_isolation(live):
    base, tokens = live
    for product in ("business_requirements", "technical_specification"):
        query = "?" + urlencode({"document_type": product})
        for suffix in ("", "/health", "/runs", "/campaigns"):
            path = "/api/v1/admin/document-quality" + suffix + query
            assert fetch(base, path)[0] == 401
            assert fetch(base, path, tokens["reader"])[0] == 403
            status, envelope = fetch(base, path, tokens["admin"])
            assert status == 200 and envelope["code"] == 0
            data = envelope["data"]
            if suffix == "/health":
                assert data["document_type"] == product
                assert data["status"] in {"OK", "ALERT"}
            elif suffix in {"/runs", "/campaigns"}:
                assert all(row["document_type"] == product for row in data[suffix[1:]])
            elif product == "technical_specification":
                assert data["document_type"] == product
    for days in (1, 7, 30):
        status, envelope = fetch(base, f"/api/v1/admin/document-quality?document_type=technical_specification&days={days}", tokens["admin"])
        assert status == 200 and envelope["data"]["document_type"] == "technical_specification"


@pytest.mark.parametrize("width", [1440, 390])
def test_live_quality_dashboard_switches_products_without_mocked_api(live, width):
    base, tokens = live
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": 950})
        page = context.new_page()
        failures = []
        page.on("pageerror", lambda _error: failures.append("Unhandled page exception"))
        page.add_init_script(
            "localStorage.setItem('Authorization', 'Bearer '+TOKEN); localStorage.setItem('token', TOKEN); "
            "localStorage.setItem('userInfo', JSON.stringify({nickname:'Document quality monitor QA'})); localStorage.setItem('lng','ru');".replace("TOKEN", json.dumps(tokens["admin"]))
        )
        try:
            page.goto(base + "/admin/document-quality?product=technical_specification")
            dashboard = page.get_by_test_id("business-documents-quality-admin")
            expect(dashboard.get_by_role("heading", name="Качество ТЗ и соответствие БТ", exact=True)).to_be_visible(timeout=60000)
            selector = dashboard.get_by_role("combobox", name="Тип документа", exact=True)
            selector.select_option("business_requirements")
            expect(dashboard.get_by_role("heading", name="Качество документов", exact=True)).to_be_visible()
            selector.select_option("technical_specification")
            expect(dashboard.get_by_role("heading", name="Качество ТЗ и соответствие БТ", exact=True)).to_be_visible()
            assert not failures
            output = Path(os.environ["DOCUMENT_QUALITY_LIVE_EVIDENCE"])
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / f"dashboard-{width}.png"))
        finally:
            context.close()
            browser.close()
