"""Built SPA alignment journeys; synthetic APIs, no real business documents."""

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_business_documents_ui import _install_common_routes, _install_session
from test.playwright.helpers.technical_specification_api import COLLECTION, DETAIL, LOCATION, SpecificationApi


class AlignmentApi(SpecificationApi):
    def __init__(self, role="owner"):
        super().__init__(role)
        self.checks = []
        self.source_reads = 0
        self.model_calls = 0
        self.lost = False
        self.fail_first = False

    def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        if url.path == COLLECTION + "/business-sources":
            self.source_reads += 1
            page = int(parse_qs(url.query).get("page", ["1"])[0])
            return self.respond(route, {"items": [{"document_id": f"bt-{page}", "title": f"Согласованные БТ {page}", "revision_number": 2}], "page": page, "has_more": page == 1})
        if url.path == DETAIL + "/alignment":
            if request.method == "POST":
                payload = request.post_data_json
                self.requests.append((request.method, url.path, deepcopy(payload)))
                if not self.checks:
                    self.model_calls += 1
                    self.document["version"] += 2
                    self.checks = [
                        {
                            "id": payload["request_id"],
                            "status": "FINISHED",
                            "current": True,
                            "qualifies": False,
                            "result": {"quality_status": "PASS", "evidence_status": "INCOMPLETE", "reason_code": "MODEL_EVIDENCE_INCOMPLETE"},
                        }
                    ]
                if self.fail_first and not self.lost:
                    self.lost = True
                    return self.respond(route, {"error_code": "RESPONSE_LOST"}, status=502)
            return self.respond(route, {"document": self.document, "checks": self.checks})
        return super().route(route)


def open_alignment(page, base_url, api):
    _install_session(page)
    _install_common_routes(page)
    page.route("**/api/v1/source-workspaces**", api.route)
    page.goto(base_url + LOCATION)
    page.get_by_role("button", name="Соответствие БТ", exact=True).click()
    return page.get_by_role("region", name="Соответствие бизнес-требованиям")


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
def test_alignment_source_pages_retry_and_unverified_pass(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    api = AlignmentApi()
    api.fail_first = True
    panel = open_alignment(page, base_url, api)
    panel.get_by_role("checkbox", name="Согласованные БТ 1 · редакция 2").check()
    panel.get_by_role("button", name="Следующие БТ").click()
    panel.get_by_role("checkbox", name="Согласованные БТ 2 · редакция 2").check()
    expect(panel).to_contain_text("Выбрано оснований: 2 из 16")
    panel.get_by_role("button", name="Проверить соответствие").click()
    expect(panel.get_by_role("alert")).to_contain_text("Повтор использует тот же запрос")
    panel.get_by_role("button", name="Проверить соответствие").click()
    expect(panel.get_by_role("status")).to_have_text("Соответствие не подтверждено")
    expect(panel).to_contain_text("Смысловая оценка: PASS · доказательства: INCOMPLETE")
    calls = api.calls("alignment")
    assert len(calls) == 2 and calls[0][2] == calls[1][2]
    assert calls[0][2]["business_document_ids"] == ["bt-1", "bt-2"]
    assert api.model_calls == 1 and api.document["revision"] == 1


@pytest.mark.p1
def test_alignment_reader_cannot_start(page, base_url):
    api = AlignmentApi("reader")
    panel = open_alignment(page, base_url, api)
    expect(panel.get_by_role("button", name="Проверить соответствие")).to_be_disabled()
    expect(panel).to_contain_text("Проверок ещё нет")
    assert api.source_reads == api.model_calls == 0


@pytest.mark.p1
def test_alignment_requires_saving_manual_edits(page, base_url):
    api = AlignmentApi()
    api.checks = [{"id": "previous", "status": "FINISHED", "current": True, "qualifies": True}]
    panel = open_alignment(page, base_url, api)
    expect(panel.get_by_role("status")).to_have_text("Соответствие подтверждено")
    panel.get_by_role("checkbox").check()
    page.get_by_role("textbox", name="Раздел 1", exact=True).fill("Несохранённая правка")
    expect(panel.get_by_role("button", name="Проверить соответствие")).to_be_disabled()
    expect(panel).to_contain_text("Перед проверкой сохраните ручные правки")
    expect(panel.get_by_role("status")).to_have_text("Соответствие не подтверждено")
    assert api.model_calls == 0
