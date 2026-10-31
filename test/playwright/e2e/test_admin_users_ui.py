"""Admin users layout and recovery on the real SPA with controlled HTTP only."""

import re
from urllib.parse import unquote, urlparse

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_document_constructor_ui import DocumentConstructorStub, _envelope, _fulfill_json, _install_session


class UsersApi(DocumentConstructorStub):
    def __init__(self):
        super().__init__()
        self.mode = "ready"
        self.failure = False
        self.writes = []
        self.users = [
            dict(id=f"qa-{i}", email=f"analyst-{i}@example.test", nickname=f"Аналитик {i}", is_active="1" if i % 2 else "0", is_superuser=False, business_document_role="AUTHOR_CREATOR")
            for i in range(21)
        ]

    def __call__(self, route):
        path = unquote(urlparse(route.request.url).path)
        if not path.startswith("/api/v1/admin/"):
            return super().__call__(route)
        if route.request.method != "GET":
            self.writes.append((route.request.method, path))
            if self.failure:
                return route.fulfill(status=400, json={"code": 500, "message": "Тестовый сбой изменения", "data": None})
            assert route.request.method == "DELETE"
            self.users = [item for item in self.users if not path.endswith(item["email"])]
            return _fulfill_json(route, _envelope(True))
        if path == "/api/v1/admin/version":
            return _fulfill_json(route, _envelope({"version": "UI QA"}))
        assert path == "/api/v1/admin/users", path
        if self.mode == "network":
            return route.abort("failed")
        if self.mode in {"forbidden", "server"}:
            status = 403 if self.mode == "forbidden" else 503
            return route.fulfill(status=status, json={"code": status, "message": "Тестовая недоступность", "data": None})
        return _fulfill_json(route, _envelope([] if self.mode == "empty" else self.users))


def open_users(page, base_url, *, api=None, width=1280, height=720, theme="light"):
    api = api or UsersApi()
    _install_session(page)
    page.add_init_script(f"localStorage.setItem('ragflow-ui-theme','{theme}')")
    page.set_viewport_size({"width": width, "height": height})
    page.route("**/api/**", api)
    page.goto(base_url + "/admin/users")
    expect(page.locator("html")).to_have_class(re.compile(rf"(^|\s){theme}(\s|$)"))
    return api


@pytest.mark.parametrize("width,height", [(1280, 720), (1440, 720), (390, 844)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_users_table_scrolls_without_page_overflow_and_preserves_readable_controls(page, base_url, tmp_path, width, height, theme):
    api = open_users(page, base_url, width=width, height=height, theme=theme)
    table = page.get_by_role("table")
    expect(table).to_be_visible()
    expect(page.get_by_text("analyst-0@example.test", exact=True)).to_be_visible()
    assert page.locator("html").evaluate("e=>e.scrollWidth<=innerWidth")
    assert page.locator('td button[role="combobox"]').evaluate_all('es=>es.every(e=>getComputedStyle(e).whiteSpace==="nowrap")')
    page.screenshot(path=str(tmp_path / f"users-{width}-{theme}-top.png"))
    scroll = table.locator("..")
    if width < 1400:
        assert scroll.evaluate("e=>e.scrollWidth>e.clientWidth")
        scroll.evaluate("e=>e.scrollLeft=e.scrollWidth")
        expect(page.get_by_role("button", name="Удалить пользователя analyst-0@example.test", exact=True)).to_be_in_viewport()
    action = page.get_by_role("button", name="Удалить пользователя analyst-0@example.test", exact=True)
    action.focus()
    expect(action.locator("..")).to_have_css("opacity", "1")
    viewport = page.locator(".rf-admin-users [data-radix-scroll-area-viewport]")
    assert viewport.evaluate("e=>e.scrollHeight>e.clientHeight")
    viewport.evaluate("e=>e.scrollTop=e.scrollHeight")
    expect(page.get_by_role("navigation", name="Постраничная навигация", exact=True)).to_be_in_viewport()
    page.screenshot(path=str(tmp_path / f"users-{width}-{theme}.png"))
    assert not api.writes


@pytest.mark.parametrize(
    "mode,title",
    [("empty", "Пользователей пока нет"), ("forbidden", "Нет доступа к пользователям"), ("server", "Не удалось загрузить пользователей"), ("network", "Не удалось загрузить пользователей")],
)
def test_users_states_recover_without_a_false_empty_list(page, base_url, mode, title):
    api = UsersApi()
    api.mode = mode
    open_users(page, base_url, api=api)
    expect(page.get_by_role("region", name=title, exact=True)).to_be_visible()
    if mode != "empty":
        expect(page.get_by_role("table")).to_have_count(0)
        expect(page.get_by_role("button", name="Новый пользователь", exact=True)).to_be_disabled()
        api.mode = "ready"
        page.get_by_role("button", name="Повторить", exact=True).click()
        expect(page.get_by_role("table")).to_be_visible()
    assert not api.writes


def test_users_delete_cancel_and_server_failure_preserve_the_user(page, base_url):
    api = open_users(page, base_url)
    trigger = page.get_by_role("button", name="Удалить пользователя analyst-0@example.test", exact=True)
    trigger.click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="Отмена", exact=True).click()
    assert not api.writes
    trigger.click()
    api.failure = True
    dialog.get_by_role("button", name="Удалить", exact=True).click()
    expect(page.get_by_text("Тестовый сбой изменения", exact=False)).to_be_visible()
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Удалить", exact=True)).to_be_enabled()
    assert len(api.writes) == 1 and len(api.users) == 21


def test_create_invalid_email_and_password_mismatch_never_reach_the_api(page, base_url):
    api = open_users(page, base_url)
    page.get_by_role("button", name="Новый пользователь", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Электронная почта", exact=True).fill("not-email")
    dialog.get_by_label("Пароль", exact=True).fill("example123")
    dialog.get_by_label("Подтвердите пароль", exact=True).fill("different123")
    dialog.get_by_role("button", name="Подтвердить", exact=True).click()
    email = dialog.get_by_label("Электронная почта", exact=True)
    expect(dialog.get_by_text("Пожалуйста, введите действительный адрес электронной почты!", exact=True)).to_be_visible()
    assert not api.writes
    email.fill("qa@example.test")
    dialog.get_by_role("button", name="Подтвердить", exact=True).click()
    expect(dialog.get_by_text("Введенные вами пароли не совпадают!", exact=True)).to_be_visible()
    expect(dialog).to_be_visible()
    assert not api.writes
