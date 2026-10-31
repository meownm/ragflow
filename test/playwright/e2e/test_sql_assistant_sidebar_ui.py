"""Full SQL assistant page layout with controlled APIs and no model calls."""

import json

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_document_constructor_ui import _envelope, _fulfill_json, _install_session
from test.playwright.e2e.test_sql_assistant_ui import AssistantBrowserStub, screenshot


def prepare_assistant(page, stub, width, height, theme):
    _install_session(page)
    page.add_init_script(f"localStorage.setItem('ragflow-ui-theme', {json.dumps(theme)})")
    page.set_viewport_size({"width": width, "height": height})
    page.route("**/api/v1/**", stub)


@pytest.mark.parametrize("width,height,theme", [(1440, 1000, "light"), (900, 600, "dark")])
def test_recent_research_scrolls_without_overlapping_footer(page, base_url, width, height, theme):
    stub = AssistantBrowserStub()
    prepare_assistant(page, stub, width, height, theme)
    sessions = [{"id": f"recent-{i}", "title": f"Исследование {i}: " + "docker_postgres_bot.bot.public.search_queries " * 6, "revision": 1, "updated_at": 1} for i in range(30)]
    page.route("**/api/v1/sql-assistant/sessions", lambda route: _fulfill_json(route, _envelope({"sessions": sessions})))
    page.goto(base_url + "/sql-assistant", wait_until="domcontentloaded")
    expect(page.locator("html")).to_have_class(theme)
    recent = page.locator(".sa-recent")
    expect(recent.locator("button")).to_have_count(30)
    footer = page.locator(".sa-sidebar-footer")
    create = page.get_by_role("button", name="Новое исследование", exact=True)
    assert create.evaluate("e => e.scrollWidth <= e.clientWidth")
    nav = page.get_by_role("navigation", name="Разделы помощника")
    footer_before, nav_before = footer.bounding_box(), nav.bounding_box()
    sizes = recent.evaluate("el => ({height: el.clientHeight, scroll: el.scrollHeight, width: el.clientWidth, contentWidth: el.scrollWidth})")
    assert sizes["scroll"] > sizes["height"] > 0
    assert sizes["contentWidth"] == sizes["width"]
    assert recent.bounding_box()["y"] + recent.bounding_box()["height"] <= footer_before["y"]
    assert footer_before["y"] + footer_before["height"] <= height
    title = recent.locator(".sa-recent-title").first
    assert title.evaluate("el => el.scrollHeight > el.clientHeight")
    recent.hover()
    page.mouse.wheel(0, 600)
    page.wait_for_function("document.querySelector('.sa-recent').scrollTop > 0")
    last = recent.locator("button").last
    last.focus()
    expect(last).to_be_focused()
    assert last.bounding_box()["y"] + last.bounding_box()["height"] <= footer.bounding_box()["y"]
    assert footer.bounding_box() == footer_before
    assert nav.bounding_box() == nav_before
    page.keyboard.press("Enter")
    expect(last).to_have_attribute("aria-current", "page")
    screenshot(page, f"sql-sidebar-{width}-{height}-{theme}")

    page.route("**/api/v1/sql-assistant/sessions", lambda route: _fulfill_json(route, _envelope({"sessions": []})))
    page.reload(wait_until="domcontentloaded")
    expect(recent).to_contain_text("Здесь появятся сохранённые вопросы.")
    expect(footer).to_be_visible()


@pytest.mark.parametrize("width,height,theme", [(1440, 1000, "light"), (900, 600, "dark")])
def test_sql_parameters_scroll_and_edit_inside_full_page(page, base_url, width, height, theme):
    stub = AssistantBrowserStub()
    parameters = [{"name": "row_limit", "type": "integer", "value": 1000}] + [{"name": f"filter_{index}", "type": "text", "value": "long_source_name_" * 20} for index in range(15)]
    stub.sql_agent_project["artifacts"]["query"] = {"mode": "manual", "sql": stub.sql, "parameters": parameters}
    stub.session["selected_result_id"] = "run-1"
    prepare_assistant(page, stub, width, height, theme)
    page.goto(base_url + "/sql-assistant?session=" + stub.session["id"], wait_until="domcontentloaded")
    expect(page.locator("html")).to_have_class(theme)
    page.get_by_text("Изменить расчёт", exact=True).click()
    editor = page.get_by_role("textbox", name="Параметры SQL", exact=True)
    editor.scroll_into_view_if_needed()
    expect(editor).to_be_visible()
    assert json.loads(editor.input_value()) == parameters
    geometry = editor.evaluate("e => ({height: e.clientHeight, scroll: e.scrollHeight, overflow: getComputedStyle(e).overflowY, resize: getComputedStyle(e).resize})")
    assert geometry["scroll"] > geometry["height"] >= 158
    assert geometry["overflow"] == "auto"
    assert geometry["resize"] == "vertical"
    editor.hover()
    page.mouse.wheel(0, 600)
    page.wait_for_function("document.querySelector('[aria-label=\"Параметры SQL\"]').scrollTop > 0")
    editor.focus()
    page.keyboard.press("Control+End")
    page.wait_for_function(
        """() => {
            const editor = document.querySelector('[aria-label="Параметры SQL"]');
            const bottomInset = parseFloat(getComputedStyle(editor).paddingBottom) +
                editor.offsetHeight - editor.clientHeight;
            return editor.selectionEnd === editor.value.length &&
                editor.scrollTop + editor.clientHeight >= editor.scrollHeight - bottomInset - 1;
        }"""
    )
    page.keyboard.insert_text(" ")
    assert json.loads(editor.input_value()) == parameters
    expect(editor).to_have_value(json.dumps(parameters, ensure_ascii=False, indent=2) + " ")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert stub.commands == []
    screenshot(page, f"sql-parameters-{width}-{height}-{theme}")
