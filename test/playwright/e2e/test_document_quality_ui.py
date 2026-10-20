"""Built dashboard with synthetic admin responses; no live documents or model."""

import re
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_navigation_visibility_admin import NavigationApiStub, _envelope, _fulfill_json, _install_admin_session


@pytest.mark.p1
@pytest.mark.parametrize(("width", "complete"), [(1440, True), (1440, False), (390, True), (390, False)], ids=["desktop-complete", "desktop-incomplete", "mobile-complete", "mobile-incomplete"])
def test_alignment_quality_dashboard_keeps_semantic_denominators_and_unknown_evidence(page, base_url, width, complete):
    page.set_viewport_size({"width": width, "height": 900})
    _install_admin_session(page)
    fallback = NavigationApiStub()
    requests = []
    report = {
        "status": "INCOMPLETE",
        "quality_status": "FAIL",
        "evidence_status": "INCOMPLETE",
        "expected_cases": 4,
        "executed_cases": 4 if complete else 2,
        "alignment_metrics": {"model_cases_passed": 2, "model_cases_failed": 2 if complete else 0, "model_cases_unknown": 0, "model_cases_unexecuted": 0 if complete else 2},
        "alignment_quality": {
            "expected_fragments": 4,
            "scored_fragments": 4 if complete else 2,
            "unscored_fragments": 0 if complete else 2,
            "undetermined_fragments": 0,
            "contradiction_tp": 1,
            "contradiction_fp": 0,
            "contradiction_fn": 1 if complete else 0,
            "contradiction_tn": 2 if complete else 1,
            "measures": {"contradiction_recall": {"numerator": 1, "denominator": 2, "value": 0.5 if complete else None}, "unsupported_rule_recall": {"numerator": 0, "denominator": 0, "value": None}},
        },
    }

    current = {"id": "qa", "trigger": "MANUAL", "scope": "FULL", "status": "INCOMPLETE", "model_name": "synthetic-model", "source_revision": "synthetic", "report": report}
    report["nightly_experiments"] = [
        {
            "experiment": "REPEATS",
            "quality_status": "PASS" if complete else "UNKNOWN",
            "evidence_status": "INCOMPLETE",
            "expected_cases": 112,
            "executed_cases": 112 if complete else 6,
            "total_tokens": 100 if complete else None,
        }
    ]
    old_report = deepcopy(report)
    old_report.update(status="FAIL", executed_cases=4)
    old_report["alignment_quality"].update(scored_fragments=4, unscored_fragments=0, contradiction_tp=0, contradiction_fn=2, contradiction_tn=2)
    old_report["alignment_quality"]["measures"]["contradiction_recall"] = {"numerator": 0, "denominator": 2, "value": 0}
    old_report["nightly_experiments"][0].update(quality_status="FAIL", executed_cases=7, total_tokens=None)
    older = {**current, "id": "older", "status": "FAIL", "model_name": "synthetic-old", "source_revision": "previous", "report": old_report}

    def route(request_route):
        request = request_route.request
        url = urlsplit(request.url)
        if not url.path.startswith("/api/v1/admin/document-quality"):
            return fallback(request_route)
        requests.append(url.path)
        assert request.method == "GET" and parse_qs(url.query).get("document_type") == ["technical_specification"]
        if url.path.endswith("/runs"):
            payload = {"configured": True, "source_revision": "synthetic", "runs": [current, older]}
        elif url.path.endswith("/runs/older"):
            payload = older
        elif url.path.endswith("/campaigns"):
            payload = {"campaigns": []}
        elif url.path.endswith("/models"):
            payload = {"configured": True, "models": [], "case_ids": [], "errors": []}
        elif url.path.endswith("/health"):
            payload = {"status": "OK", "slot": "2026-10-20", "reason_code": None}
        else:
            raise AssertionError("Technical dashboard requested unrelated operational observations")
        _fulfill_json(request_route, _envelope(payload))

    page.route("**/api/v1/**", route)
    page.goto(base_url.rstrip("/") + "/admin/document-quality?product=technical_specification")
    panel = page.get_by_role("region", name="Смысловое качество по эталону")
    expect(panel).to_be_visible()
    recall = panel.get_by_role("row", name=re.compile("Recall противоречий"))
    expect(recall).to_contain_text("1/2")
    expect(recall).to_contain_text("50.0%" if complete else "—")
    if not complete:
        expect(recall).not_to_contain_text("50.0%")
    empty = panel.get_by_role("row", name=re.compile("Обнаружение необоснованных бизнес-правил"))
    expect(empty).to_contain_text("0/0")
    expect(empty).to_contain_text("—")
    expect(page.get_by_text(re.compile("Полнота доказательств: INCOMPLETE"))).to_be_visible()
    experiments = page.get_by_role("region", name="Дополнительные модельные прогоны")
    experiment = experiments.get_by_role("row", name=re.compile("Повторяемость"))
    expect(experiment).to_contain_text("112/112" if complete else "6/112")
    expect(experiment).to_contain_text("INCOMPLETE")
    expect(experiment.get_by_role("cell").last).to_have_text("100" if complete else "—")
    assert len(requests) >= 4
    page.get_by_role("button", name="Обновить", exact=True).click()
    page.get_by_role("tab", name="История проверок", exact=True).click()
    page.get_by_role("button", name=re.compile("synthetic-old")).click()
    historical = page.get_by_role("region", name="Смысловое качество по эталону")
    old_recall = historical.get_by_role("row", name=re.compile("Recall противоречий"))
    expect(old_recall).to_contain_text("0.0%")
    expect(old_recall).to_contain_text("0/2")
    old_experiment = page.get_by_role("region", name="Дополнительные модельные прогоны").get_by_role("row", name=re.compile("Повторяемость"))
    expect(old_experiment).to_contain_text("FAIL")
    expect(old_experiment).to_contain_text("7/112")
    expect(old_experiment.get_by_role("cell").last).to_have_text("—")
    assert "/api/v1/admin/document-quality/runs/older" in requests


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "mobile"])
def test_alignment_operational_cohort_preserves_unknown_cost_and_window_isolation(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    _install_admin_session(page)
    fallback = NavigationApiStub()
    windows = []
    failed = False

    def route(request_route):
        request = request_route.request
        url = urlsplit(request.url)
        if not url.path.startswith("/api/v1/admin/document-quality"):
            return fallback(request_route)
        query = parse_qs(url.query)
        assert request.method == "GET" and query.get("document_type") == ["technical_specification"]
        if url.path == "/api/v1/admin/document-quality":
            days = int(query["days"][0])
            windows.append(days)
            if failed:
                return _fulfill_json(request_route, {"code": 500, "message": "Synthetic failure", "data": None})
            payload = {
                "document_type": "technical_specification",
                "scope": "RECORDED_ALIGNMENT_ATTEMPTS",
                "days": days,
                "updated_at": 1789855354167,
                "earliest_observation_at": 1789855254167,
                "counts": {"total": 2, "finished": 1, "incomplete": 1, "execution_complete": 1, "reports_complete": 1, "usage_unknown": 1},
                "terminal_attempts": 2,
                "execution_completion_rate": 0.5,
                "report_completion_rate": 0.5,
                "total_tokens": None,
                "known_spent_tokens": 1234,
                "reserved_tokens": 30,
                "known_reserved_tokens": 30,
                "durations": {"check": {"measured": 1, "mean_ms": 20, "p95_ms": 20}, "model": {"measured": 0, "mean_ms": None, "p95_ms": None}},
                "reasons": [{"code": "EXECUTION_CANCELLED", "count": 1}],
            }
        elif url.path.endswith("/runs"):
            payload = {"configured": True, "source_revision": "synthetic", "runs": []}
        elif url.path.endswith("/campaigns"):
            payload = {"campaigns": []}
        elif url.path.endswith("/models"):
            payload = {"configured": True, "models": [], "case_ids": [], "errors": []}
        elif url.path.endswith("/health"):
            payload = {"status": "OK", "slot": "2026-10-20", "reason_code": None}
        else:
            raise AssertionError("Technical operations requested unrelated BT jobs")
        _fulfill_json(request_route, _envelope(payload))

    page.route("**/api/v1/**", route)
    page.goto(base_url.rstrip("/") + "/admin/document-quality?product=technical_specification")
    page.get_by_role("tab", name="Рабочие проверки ТЗ", exact=True).click()
    panel = page.get_by_role("region", name="Эксплуатационные показатели проверок ТЗ")
    expect(panel).to_be_visible()
    expect(panel.get_by_text("EXECUTION_CANCELLED: 1")).to_be_visible()
    total = panel.locator("div").filter(has=page.get_by_text("Полный расход токенов", exact=True)).last
    expect(total.locator("dd")).to_have_text("—")
    expect(panel).to_contain_text("50.0% · 1/2")
    for days, title in [(1, "24 часа"), (30, "30 дней")]:
        with page.expect_response(lambda response, days=days: urlsplit(response.url).path == "/api/v1/admin/document-quality" and parse_qs(urlsplit(response.url).query).get("days") == [str(days)]):
            page.get_by_role("button", name=title, exact=True).click()
        expect(page.get_by_role("button", name=title, exact=True)).to_have_attribute("aria-pressed", "true")
        expect(panel.get_by_text("EXECUTION_CANCELLED: 1")).to_be_visible()
    assert windows == [7, 1, 30]
    failed = True
    page.get_by_role("button", name="Обновить", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Не удалось загрузить")
    expect(panel).not_to_be_visible()
    assert windows == [7, 1, 30, 30]
