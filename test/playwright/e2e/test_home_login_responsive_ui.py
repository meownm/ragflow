"""Home/navigation/login usability against the built SPA and an isolated API."""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_navigation_visibility_admin import (
    NavigationApiStub,
    _install_admin_session,
    _install_russian_locale,
)


class HomeLoginApi(NavigationApiStub):
    def __init__(self, sections=None, *, can_create=True, sso_only=False):
        super().__init__(sections)
        if sections is None:
            self.visible_sections.append("sql_assistant")
        self.can_create = can_create
        self.sso_only = sso_only
        self.login_requests = []

    def __call__(self, route):
        path = urlparse(route.request.url).path.rstrip("/")
        data = None
        if path == "/api/v1/system/config":
            data = {"registerEnabled": 1, "disablePasswordLogin": self.sso_only, "visibleSections": self.visible_sections}
        elif path == "/api/v1/auth/login/channels":
            data = [{"channel": "company", "display_name": "Company SSO"}] if self.sso_only else []
        elif path == "/api/v1/auth/login":
            self.login_requests.append(route.request.post_data_json)
            route.fulfill(json={"code": 100, "data": None, "message": "Invalid credentials"})
            return
        elif path == "/api/v1/business-documents/capabilities":
            data = {"capabilities": {"create": self.can_create}}
        elif path == "/api/v1/chats":
            data = {"chats": [], "total": 0}
        elif path == "/api/v1/datasets":
            data = [
                {"id": "qa-dataset", "name": "База знаний с длинным названием для проверки мобильного интерфейса", "document_count": 12, "create_time": 1700000000000, "update_time": 1700000000000}
            ]
        elif path == "/api/v1/searches":
            data = {"search_apps": [], "total": 0}
        elif path == "/api/v1/memories":
            data = {"memory_list": [], "total": 0}
        elif path in {"/v1/canvas/list", "/api/v1/agents"}:
            data = {"canvas": [], "total": 0}
        if data is None:
            return super().__call__(route)
        route.fulfill(json={"code": 0, "data": data, "message": "", "total_datasets": 1})


def _setup(page, theme, *, authenticated=False, api=None):
    if authenticated:
        _install_admin_session(page)
    else:
        _install_russian_locale(page)
    page.add_init_script(f"localStorage.setItem('ragflow-ui-theme', {json.dumps(theme)});")
    api = api or HomeLoginApi()
    page.route(re.compile(r".*/(?:api|v1)/.*"), api)
    return api


def _no_horizontal_scroll(page):
    overflow = page.evaluate("""() => [...document.querySelectorAll('body, #root, #root *')]
      .filter(e => {
        const r = e.getBoundingClientRect(), c = getComputedStyle(e);
        return e.clientWidth > 0 && r.x >= 0 && r.x < innerWidth &&
          ['auto', 'scroll'].includes(c.overflowX) && e.scrollWidth > e.clientWidth + 1;
      }).map(e => ({tag: e.tagName, class: e.className, width: e.clientWidth, scroll: e.scrollWidth}))""")
    assert not overflow, overflow
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def _shot(page, name):
    import os

    root = Path(os.environ["PW_ARTIFACTS_DIR"]) / "responsive"
    root.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(root / name), full_page=True, animations="disabled")


@pytest.mark.parametrize("size", [(320, 568), (375, 812), (390, 844), (1280, 900)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_home_and_grouped_menu_fit_viewport_and_support_keyboard(page, base_url, size, theme):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    _setup(page, theme, authenticated=True)
    page.goto(f"{base_url}/")
    expect(page.locator("html")).to_have_class(re.compile(rf"\b{theme}\b"))
    expect(page.get_by_role("button", name="Создать чат", exact=True)).to_be_visible()
    expect(page.locator("h2 img")).to_have_count(2)
    for icon in page.locator("h2 img").all():
        expect(icon).to_have_attribute("src", re.compile(".+"))
    page.wait_for_function("[...document.querySelectorAll('h2 img')].every(i => i.complete && i.naturalWidth > 0)")
    _no_horizontal_scroll(page)
    _shot(page, f"home-{size[0]}-{theme}.png")
    page.get_by_role("button", name="Создать чат", exact=True).scroll_into_view_if_needed()
    _shot(page, f"home-action-{size[0]}-{theme}.png")

    menu_button = page.get_by_role("button", name="Меню", exact=True)
    menu_button.click()
    menu = page.get_by_role("dialog", name="Меню", exact=True)
    expect(menu.get_by_role("heading", name="Документы", exact=True)).to_be_visible()
    expect(menu.get_by_role("heading", name="Данные", exact=True)).to_be_visible()
    expect(menu.get_by_role("heading", name="ИИ-инструменты", exact=True)).to_be_visible()
    expect(menu.get_by_test_id("nav-home")).to_have_attribute("aria-current", "page")
    expect(menu.get_by_role("link")).to_have_count(17)
    expect(menu.get_by_test_id("nav-sql-assistant")).to_be_visible()
    _no_horizontal_scroll(page)
    _shot(page, f"menu-{size[0]}-{theme}.png")
    menu.get_by_test_id("nav-translator").focus()
    expect(menu.get_by_test_id("nav-translator")).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).not_to_be_visible()
    expect(menu_button).to_be_focused()
    menu_button.click()
    menu.get_by_role("button", name="Закрыть", exact=True).click()
    expect(menu).not_to_be_visible()

    for name in ("Поиск", "Агент", "Память", "Чат"):
        page.get_by_role("group", name="Тип приложения").get_by_role("button", name=name, exact=True).click()
        _no_horizontal_scroll(page)
    action = page.get_by_role("button", name="Создать чат", exact=True)
    action.press("Space" if theme == "dark" else "Enter")
    expect(page).to_have_url(re.compile(r"/chats\?isCreate=true$"))


@pytest.mark.parametrize("size", [(320, 568), (375, 812), (390, 844), (1280, 900)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_login_validation_labels_and_registration_fit_viewport(page, base_url, size, theme):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    api = _setup(page, theme)
    page.goto(f"{base_url}/login")
    expect(page.locator("html")).to_have_class(re.compile(rf"\b{theme}\b"))
    password = page.get_by_label("Пароль", exact=True)
    expect(password).to_be_visible()
    expect(password).to_have_attribute("autocomplete", "current-password")
    expect(password).to_have_attribute("aria-invalid", "false")
    expect(page.get_by_test_id("auth-email")).to_have_attribute("type", "email")
    for control in (password, page.get_by_test_id("auth-email"), page.get_by_test_id("auth-submit")):
        assert control.bounding_box()["height"] >= 44
    _no_horizontal_scroll(page)
    _shot(page, f"login-{size[0]}-{theme}.png")
    password.fill("Example123!")
    page.get_by_role("button", name="Показать пароль", exact=True).click()
    expect(password).to_have_attribute("type", "text")
    expect(password).to_have_value("Example123!")
    page.get_by_role("button", name="Скрыть пароль", exact=True).click()
    expect(password).to_have_attribute("type", "password")
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_text("Введите электронную почту", exact=True)).to_be_visible()
    page.get_by_test_id("auth-email").fill("bad-address")
    page.get_by_test_id("auth-submit").click()
    expect(page.get_by_text("Введите корректный адрес электронной почты", exact=True)).to_be_visible()
    expect(page.get_by_text("Invalid email", exact=True)).to_have_count(0)
    assert api.login_requests == []
    _no_horizontal_scroll(page)
    _shot(page, f"login-error-{size[0]}-{theme}.png")
    page.get_by_test_id("auth-toggle-register").click()
    heading = page.get_by_role("heading", level=1)
    expect(heading).to_be_focused()
    assert heading.bounding_box()["y"] >= 0
    expect(page.get_by_test_id("auth-nickname")).to_be_visible()
    expect(page.get_by_test_id("auth-form")).to_have_count(1)
    expect(page.get_by_test_id("auth-email")).to_have_value("bad-address")
    _no_horizontal_scroll(page)
    _shot(page, f"register-{size[0]}-{theme}.png")
    page.get_by_test_id("auth-toggle-login").click()
    expect(page.get_by_test_id("auth-nickname")).to_have_count(0)
    expect(heading).to_be_focused()
    assert heading.bounding_box()["y"] >= 0


def test_grouping_preserves_hidden_sections_and_create_capabilities(page, base_url):
    page.set_viewport_size({"width": 375, "height": 812})
    api = HomeLoginApi(["home", "business_documents", "document_constructor", "sql_projects"], can_create=False)
    _setup(page, "dark", authenticated=True, api=api)
    page.goto(f"{base_url}/")
    page.get_by_role("button", name="Меню", exact=True).click()
    menu = page.get_by_role("dialog", name="Меню", exact=True)
    expect(menu.get_by_test_id("nav-business-documents")).to_be_visible()
    expect(menu.get_by_test_id("nav-document-constructor")).to_have_count(0)
    expect(menu.get_by_test_id("nav-sql-projects")).to_have_count(0)
    expect(menu.get_by_role("heading", name="Данные", exact=True)).to_have_count(0)
    expect(menu.get_by_test_id("nav-chat")).to_have_count(0)


def test_sso_only_login_keeps_company_sign_in_without_password_form(page, base_url):
    page.set_viewport_size({"width": 320, "height": 568})
    _setup(page, "light", api=HomeLoginApi(sso_only=True))
    page.goto(f"{base_url}/login")
    expect(page.get_by_role("button", name="Войти через Company SSO")).to_be_visible()
    expect(page.get_by_test_id("auth-form")).to_have_count(0)
    expect(page.get_by_test_id("auth-toggle-register")).to_have_count(0)
    _no_horizontal_scroll(page)


def test_login_stays_scrollable_when_available_height_shrinks(page, base_url):
    page.set_viewport_size({"width": 375, "height": 430})
    _setup(page, "dark")
    page.goto(f"{base_url}/login")
    page.get_by_label("Пароль", exact=True).fill("Example123!")
    submit = page.get_by_test_id("auth-submit")
    submit.scroll_into_view_if_needed()
    box = submit.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 430
    _no_horizontal_scroll(page)
    _shot(page, "login-reduced-height.png")
