"""Opt-in read-only acceptance of the installed local dashboard and admin API.

Requires dedicated QA admin/reader tokens in environment variables. This lane
does not enqueue inference, alter accounts, or modify business documents.
"""

import json
import os
import re
from contextlib import ExitStack
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
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


def _refresh_loaded_dashboard(page, dashboard, product):
    paths = ["/runs", "/campaigns", "/models", "/health"]
    if product == "business_requirements":
        paths.append("")
    with ExitStack() as stack:
        responses = []
        for suffix in paths:
            path = "/api/v1/admin/document-quality" + suffix
            responses.append(
                stack.enter_context(
                    page.expect_response(
                        lambda response, path=path, suffix=suffix: urlsplit(response.url).path == path
                        and parse_qs(urlsplit(response.url).query).get("document_type") == ([product] if suffix else None),
                        timeout=60000,
                    )
                )
            )
        dashboard.get_by_role("button", name="Обновить", exact=True).click()
    for response in responses:
        assert response.value.status == 200 and response.value.json()["code"] == 0
    expect(dashboard.get_by_text("Загрузка данных…", exact=True)).to_be_hidden(timeout=60000)
    health = responses[3].value.json()["data"]
    assert health["document_type"] == product
    if health["status"] == "ALERT":
        expect(dashboard.get_by_role("alert").filter(has_text=health["reason_code"])).to_be_visible()
    return responses[0].value.json()["data"]["runs"]


def _assert_readable_product_selector(selector):
    contrast = selector.evaluate("""node => {
        const style = getComputedStyle(node);
        const rgba = value => {
            const channels = value.match(/[\\d.]+/g).map(Number);
            return [...channels.slice(0, 3), channels[3] ?? 1];
        };
        const over = (foreground, background) => foreground.slice(0, 3).map(
            (value, index) => value * foreground[3] + background[index] * (1 - foreground[3])
        );
        const ancestors = [];
        for (let current = node; current; current = current.parentElement) ancestors.unshift(current);
        const background = ancestors.reduce((color, element) => {
            const computed = getComputedStyle(element);
            if (computed.opacity !== '1' || computed.backgroundImage !== 'none') throw new Error('Unsupported contrast surface');
            return over(rgba(computed.backgroundColor), color);
        }, [255, 255, 255]);
        const luminance = color => color.map(value => {
            value /= 255;
            return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
        }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
        const foregroundLuminance = luminance(over(rgba(style.color), background));
        const backgroundLuminance = luminance(background);
        return (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) / (Math.min(foregroundLuminance, backgroundLuminance) + 0.05);
    }""")
    assert contrast >= 4.5, "Product selector text must be readable against its background"


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
            _refresh_loaded_dashboard(page, dashboard, "technical_specification")
            selector.select_option("business_requirements")
            expect(dashboard.get_by_role("heading", name="Качество документов", exact=True)).to_be_visible()
            _refresh_loaded_dashboard(page, dashboard, "business_requirements")
            selector.select_option("technical_specification")
            expect(dashboard.get_by_role("heading", name="Качество ТЗ и соответствие БТ", exact=True)).to_be_visible()
            output = Path(os.environ["DOCUMENT_QUALITY_LIVE_EVIDENCE"])
            output.mkdir(parents=True, exist_ok=True)
            for theme in ("light", "dark"):
                page.evaluate("theme => localStorage.setItem('ragflow-ui-theme', theme)", theme)
                page.reload()
                expect(page.locator("html")).to_have_class(re.compile(rf"\b{theme}\b"))
                _refresh_loaded_dashboard(page, dashboard, "technical_specification")
                _assert_readable_product_selector(selector)
                assert not failures
                page.screenshot(path=str(output / f"dashboard-{width}-{theme}.png"))
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_live_quality_incomplete_history_keeps_unknown_usage(live, width):
    run_id = os.environ.get("DOCUMENT_QUALITY_INCOMPLETE_RUN_ID")
    if not run_id:
        pytest.skip("An existing dedicated QA incomplete run must be explicitly selected")
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", run_id)
    base, tokens = live
    status, envelope = fetch(base, f"/api/v1/admin/document-quality/runs/{run_id}?document_type=technical_specification", tokens["admin"])
    assert status == 200 and envelope["code"] == 0
    run = envelope["data"]
    assert run["id"] == run_id and run["document_type"] == "technical_specification"
    assert run["status"] in {"FAIL", "INCOMPLETE"}
    report = run["report"]
    assert report["evidence_status"] == "INCOMPLETE"
    repeats = next(item for item in report["nightly_experiments"] if item["experiment"] == "REPEATS")
    assert repeats["total_tokens"] is None and repeats["evidence_status"] == "INCOMPLETE"
    assert repeats["executed_cases"] < repeats["expected_cases"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": 950})
        page = context.new_page()
        failures = []
        page.on("pageerror", lambda _error: failures.append("Unhandled page exception"))
        page.on("request", lambda request: failures.append("Unexpected quality mutation") if "/api/v1/admin/document-quality" in request.url and request.method != "GET" else None)
        page.add_init_script(
            "localStorage.setItem('Authorization', 'Bearer '+TOKEN); localStorage.setItem('token', TOKEN); "
            "localStorage.setItem('userInfo', JSON.stringify({nickname:'Document quality monitor QA'})); localStorage.setItem('lng','ru');".replace("TOKEN", json.dumps(tokens["admin"]))
        )
        try:
            page.goto(base + "/admin/document-quality?" + urlencode({"product": "technical_specification", "tab": "history", "trigger": run["trigger"], "status": run["status"]}))
            dashboard = page.get_by_test_id("business-documents-quality-admin")
            expect(dashboard.get_by_role("heading", name="Качество ТЗ и соответствие БТ", exact=True)).to_be_visible(timeout=60000)
            loaded = _refresh_loaded_dashboard(page, dashboard, "technical_specification")
            history = [item for item in loaded if item["trigger"] == run["trigger"] and item["status"] == run["status"]]
            index = next(i for i, item in enumerate(history) if item["id"] == run_id)
            with page.expect_response(lambda response: urlsplit(response.url).path == f"/api/v1/admin/document-quality/runs/{run_id}") as detail_response:
                dashboard.get_by_role("tabpanel").locator("button.grid.w-full").nth(index).click()
            assert detail_response.value.status == 200 and detail_response.value.json()["data"]["id"] == run_id
            detail = dashboard.locator('[aria-label="Результаты сценариев"]')
            expect(detail.get_by_role("heading", level=2)).to_contain_text(run_id)
            expect(detail.get_by_text(re.compile("Полнота доказательств: INCOMPLETE"))).to_be_visible(timeout=60000)
            experiment = detail.get_by_role("region", name="Дополнительные модельные прогоны").get_by_role("row", name=re.compile("Повторяемость"))
            expect(experiment).to_contain_text(f"{repeats['executed_cases']}/{repeats['expected_cases']}")
            expect(experiment).to_contain_text(repeats["quality_status"])
            expect(experiment).to_contain_text("INCOMPLETE")
            expect(experiment.get_by_role("cell").last).to_have_text("—")
            assert page.locator("main").evaluate("node => node.scrollWidth <= node.clientWidth")
            assert not failures
            output = Path(os.environ["DOCUMENT_QUALITY_LIVE_EVIDENCE"])
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / f"incomplete-history-{width}.png"))
        finally:
            context.close()
            browser.close()
