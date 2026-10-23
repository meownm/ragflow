"""Figma workspace interactions against a built SPA and controlled API state."""

from copy import deepcopy

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_business_documents_ui import _install_common_routes, _install_session
from test.playwright.helpers.technical_specification_api import ACTOR, DETAIL, LOCATION, SpecificationApi


def open_workspace(page, base_url, width, theme="dark", api=None):
    api = api or SpecificationApi()
    page.set_viewport_size({"width": width, "height": 960})
    _install_session(page)
    _install_common_routes(page)
    page.add_init_script("localStorage.setItem('theme', '" + theme + "')")
    page.route("**/api/v1/source-workspaces**", api.route)
    page.goto(base_url + LOCATION)
    expect(page.get_by_role("textbox", name="Раздел 1", exact=True)).to_be_visible()
    return api


@pytest.mark.p1
def test_collapsing_assistant_retains_the_reading_position(page, base_url):
    api = SpecificationApi()
    api.document["messages"] = [{"id": f"answer-{index}", "role": "assistant", "text": f"Ответ {index}. " + "Подробное объяснение. " * 40} for index in range(20)]
    open_workspace(page, base_url, 1440, api=api)
    history = page.get_by_role("region", name="История чата")
    history.evaluate("e=>e.scrollTop=0")
    expect(page.get_by_role("button", name="К последнему сообщению")).to_be_visible()
    page.get_by_role("complementary", name="Помощник по ТЗ").get_by_role("button", name="Свернуть помощника").click()
    page.get_by_role("button", name="Открыть помощника", exact=True).click()
    assert history.evaluate("e=>e.scrollTop") == 0
    expect(page.get_by_role("button", name="К последнему сообщению")).to_be_visible()
    assert api.model_calls == 0


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 1280, 1149, 761, 760, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_workspace_assistant_resizes_and_retains_draft_when_collapsed(page, base_url, tmp_path, width, theme):
    api = open_workspace(page, base_url, width, theme)
    if width <= 760:
        page.get_by_role("button", name="Открыть помощника", exact=True).click()
    assistant = page.get_by_role("complementary", name="Помощник по ТЗ")
    expect(assistant).to_be_visible()
    text = page.get_by_role("textbox", name="Сообщение помощнику")
    text.fill("Не отправленный вопрос по разделу")
    page.get_by_label("Контекст чата").select_option("6")
    height = page.get_by_role("separator", name="Высота поля ввода помощника")
    height.focus()
    page.keyboard.press("ArrowUp")
    expect(height).to_have_attribute("aria-valuenow", "104")
    assert text.evaluate("e=>e.getBoundingClientRect().height") == 104
    if width > 760:
        separator = page.get_by_role("separator", name="Ширина правой панели ТЗ")
        separator.focus()
        for _ in range(9):
            page.keyboard.press("ArrowLeft")
        expect(separator).to_have_attribute("aria-valuenow", "480")
        for _ in range(12):
            page.keyboard.press("ArrowRight")
        expect(separator).to_have_attribute("aria-valuenow", "280")
        page.keyboard.press("Home")
        expect(separator).to_have_attribute("aria-valuenow", "336")
        box = separator.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 90)
        page.mouse.down()
        page.mouse.move(box["x"] - 60, box["y"] + 90)
        page.mouse.up()
        assert int(separator.get_attribute("aria-valuenow")) > 380
    page.get_by_role("button", name="Свернуть помощника", exact=True).last.click()
    expect(assistant).not_to_be_visible()
    page.get_by_role("button", name="Открыть помощника", exact=True).click()
    expect(text).to_have_value("Не отправленный вопрос по разделу")
    expect(page.get_by_label("Контекст чата")).to_have_value("6")
    expect(height).to_have_attribute("aria-valuenow", "104")
    assert api.model_calls == 0
    assert page.locator("html").evaluate("e=>e.scrollWidth<=innerWidth")
    page.screenshot(path=str(tmp_path / f"workspace-{theme}-{width}.png"), full_page=True)
    page.reload()
    if width <= 760:
        page.get_by_role("button", name="Открыть помощника", exact=True).click()
    expect(text).to_have_value("Не отправленный вопрос по разделу")
    expect(height).to_have_attribute("aria-valuenow", "104")


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("conflict", [False, True])
def test_attention_compares_all_changes_with_one_guarded_apply(page, base_url, tmp_path, width, conflict):
    api = SpecificationApi()
    api.document["proposals"] = [
        {
            "id": "proposal",
            "source": "chat",
            "base_revision": 1,
            "status": "needs_resolution" if conflict else "ready",
            "issues": ["Лимит в разделах 4 и 5 противоречит друг другу"] if conflict else [],
            "sections": {**api.document["sections"], "5": "Кнопка Записать"},
        }
    ]
    open_workspace(page, base_url, width, api=api)
    page.get_by_role("button", name="Требует внимания", exact=False).click()
    page.get_by_role("button", name="Посмотреть изменения", exact=True).click()
    preview = page.get_by_role("region", name="Сравнение предложенных изменений")
    expect(preview).to_be_visible()
    expect(preview.get_by_text("Сейчас", exact=True)).to_be_visible()
    expect(preview.get_by_text("После применения", exact=True)).to_be_visible()
    difference = preview.get_by_test_id("revision-text-diff")
    expect(difference.locator("del")).to_have_text("Сохранить")
    expect(difference.locator("ins")).to_have_text("Записать")
    if conflict:
        expect(preview.get_by_role("button", name="Применить изменения")).to_have_count(0)
        expect(preview.get_by_text("Лимит в разделах 4 и 5 противоречит друг другу")).to_be_visible()
        page.screenshot(path=str(tmp_path / f"conflict-{width}.png"), full_page=True)
        preview.get_by_role("button", name="Обсудить всё ТЗ").click()
        expect(page.get_by_role("textbox", name="Сообщение помощнику")).to_be_visible()
        assert api.document["revision"] == 1
    else:
        expect(page.get_by_role("button", name="Применить изменения")).to_have_count(1)
        page.screenshot(path=str(tmp_path / f"proposal-{width}.png"), full_page=True)
        preview.get_by_role("button", name="Применить изменения").click()
        expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
        assert len(api.calls("apply")) == 1
        assert api.document["sections"]["5"] == "Кнопка Записать"


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
def test_comment_threads_show_quote_and_decision_without_changing_document(page, base_url, tmp_path, width):
    api = SpecificationApi()
    api.document["comments"] = [
        {
            "id": "thread",
            "author_id": ACTOR,
            "text": "Уточнить исходный фрагмент",
            "created_at": "2026-10-21T10:30:00Z",
            "resolved": True,
            "resolved_by": ACTOR,
            "resolved_at": "2026-10-21T10:38:00Z",
            "context": {
                "document_id": "doc",
                "revision": 1,
                "scope": "selection",
                "anchor": {
                    "section": "1",
                    "quote": "цитата",
                    "prefix": "До ",
                    "suffix": " после",
                },
            },
            "replies": [{"id": "reply", "author_id": ACTOR, "text": "Ответ сохранён", "created_at": "2026-10-21T10:32:00Z"}],
        }
    ]
    other = deepcopy(api.document["comments"][0])
    other.update(id="other-thread", text="Обсуждение другого раздела", replies=[], context={"document_id": "doc", "revision": 1, "scope": "2", "anchor": None})
    api.document["comments"].append(other)
    original = deepcopy(api.document)
    open_workspace(page, base_url, width, api=api)
    if width <= 760:
        page.get_by_role("button", name="Открыть помощника", exact=True).click()
    page.get_by_role("button", name="Комментарии", exact=False).click()
    page.get_by_label("Показывать решённые").check()
    page.get_by_label("Контекст комментариев").select_option("1")
    expect(page.get_by_role("complementary", name="Комментарии")).to_contain_text("Решённые · 1")
    expect(page.get_by_text("Ответ сохранён", exact=False)).to_be_visible()
    expect(page.get_by_text("Обсуждение решено", exact=False)).to_be_visible()
    page.screenshot(path=str(tmp_path / f"comments-{width}.png"), full_page=True)
    page.get_by_role("button", name="Показать в документе", exact=False).click()
    if width <= 760:
        expect(page.get_by_role("complementary", name="Комментарии")).not_to_be_visible()
        expect(page.get_by_role("complementary", name="Помощник по ТЗ")).not_to_be_visible()
    expect(page.locator("#tz-section-1 .tz-discussion-quote")).to_be_visible()
    assert api.document == original
    assert api.model_calls == 0


@pytest.mark.p1
def test_sources_are_a_read_only_summary_and_do_not_run_alignment(page, base_url, tmp_path):
    api = SpecificationApi()
    open_workspace(page, base_url, 1440, api=api)
    calls = []

    def alignment(route):
        calls.append(route.request.method)
        api.respond(
            route,
            {
                "document": api.document,
                "checks": [
                    {
                        "id": "saved",
                        "current": True,
                        "qualifies": True,
                        "references": [{"document_id": "bt-one", "title": "БТ загрузки заявок", "revision": "source-proof-sha256", "revision_number": 4}],
                    }
                ],
            },
        )

    page.route("**" + DETAIL + "/alignment", alignment)
    page.get_by_role("button", name="Источники", exact=True).click()
    link = page.get_by_role("link", name="БТ загрузки заявок")
    expect(link).to_have_attribute("href", "/business-documents/bt-one")
    expect(page.get_by_text("Согласованная редакция 4")).to_be_visible()
    expect(page.get_by_role("button", name="Проверить соответствие")).to_have_count(0)
    page.screenshot(path=str(tmp_path / "sources-1440.png"), full_page=True)
    assert calls and all(method == "GET" for method in calls)
    assert api.model_calls == 0
