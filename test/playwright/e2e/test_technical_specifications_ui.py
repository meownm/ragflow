"""Built SPA journeys with synthetic HTTP responses, never a live Eva or model.

These checks own browser state and request contracts. Server authorization,
semantic decisions and transaction races are covered by the corresponding
technical_specifications units and document_exchange PostgreSQL lane.
"""

from copy import deepcopy
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_business_documents_ui import _install_common_routes, _install_session


from test.playwright.helpers.technical_specification_api import COLLECTION, DETAIL, LOCATION, SpecificationApi
from test.playwright.helpers.technical_specification_eva import CodecSpecificationApi


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
def test_specification_undo_after_blur_preserves_text_and_autosaves(page, base_url, tmp_path, width):
    page.set_viewport_size({"width": width, "height": 960})
    api = open_specification(page, base_url)
    page.get_by_text("Название документа", exact=True).click()
    area = page.get_by_role("textbox", name="Раздел 1", exact=True)
    original = area.inner_text()
    area.click()
    area.press("Control+End")
    page.keyboard.insert_text(" добавлено")
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    page.get_by_role("textbox", name="Название документа", exact=True).focus()
    area.focus()
    area.press("Control+z")
    expect(area).to_have_text(original)
    expect(page.get_by_text("Редакция 3", exact=True)).to_be_visible()
    area.press("Control+Shift+z")
    expect(area).to_have_text(original + " добавлено")
    expect(page.get_by_text("Редакция 4", exact=True)).to_be_visible()
    page.keyboard.type("X")
    expect(page.get_by_text("Редакция 5", exact=True)).to_be_visible()
    page.get_by_role("textbox", name="Название документа", exact=True).focus()
    area.focus()
    area.press("Control+z")
    expect(area).to_have_text(original + " добавлено")
    expect(page.get_by_text("Редакция 6", exact=True)).to_be_visible()
    page.reload()
    expect(area).to_have_text(original + " добавлено")
    assert api.document["sections"]["1"] == original + " добавлено"
    page.screenshot(path=str(tmp_path / f"tz-undo-{width}.png"), full_page=True)


@pytest.mark.p1
@pytest.mark.parametrize("lost", [False, True])
def test_specification_poll_echo_before_save_response_keeps_later_typing(page, base_url, lost):
    api = open_specification(page, base_url)
    api.chat_processing = True
    page.get_by_role("button", name="Чат", exact=True).click()
    page.get_by_role("textbox", name="Сообщение помощнику").fill("Подготовь правки")
    page.get_by_role("button", name="Отправить", exact=True).click()
    expect(page.get_by_text("Запрос сохранён · помощник готовит ответ")).to_be_visible()
    page.get_by_role("button", name="Документ", exact=True).click()
    api.hold_save_response = True
    area = page.get_by_role("textbox", name="Раздел 1", exact=True)
    area.fill("Первый ввод")
    # Wait for the intercepted write, whose response deliberately remains open.
    for _ in range(50):
        if api.pending_save_response is not None:
            break
        page.wait_for_timeout(50)
    assert api.pending_save_response is not None
    area.fill("Более новый ввод")
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible(timeout=10000)
    expect(page.get_by_role("region", name="Конфликт ручных правок")).to_have_count(0)
    api.release_save_response(lost=lost)
    expect(page.get_by_text("Редакция 3", exact=True)).to_be_visible()
    expect(area).to_have_text("Более новый ввод")
    assert api.document["sections"]["1"] == "Более новый ввод"
    assert len([row for row in api.requests if row[0] == "PUT" and row[1] == DETAIL]) == 2


@pytest.mark.p1
def test_specification_formatted_eva_roundtrip_and_independent_remote_change(page, base_url, tmp_path):
    page.set_viewport_size({"width": 1440, "height": 960})
    api = CodecSpecificationApi()
    api.document["sections"]["1"] = "**Важно**: исходный текст"
    _install_session(page)
    _install_common_routes(page)
    page.route("**/api/v1/source-workspaces**", api.route)
    page.goto(base_url + LOCATION)
    area = page.get_by_role("textbox", name="Раздел 1", exact=True)
    area.locator("strong").select_text()
    page.keyboard.type("Обязательно")
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    assert "<strong>Обязательно</strong>" in api.document["sections"]["1"]
    page.get_by_role("button", name="Eva", exact=True).click()
    page.get_by_role("button", name="Обновить в Eva", exact=True).click()
    expect(page.get_by_text("Страница Eva обновлена", exact=True)).to_be_visible()
    assert "**Обязательно**" in api.eva.remote
    page.get_by_role("button", name="Получить изменения", exact=True).click()
    expect(page.get_by_text("Новых изменений в Eva нет", exact=True)).to_be_visible()
    assert not api.document["proposals"]
    page.get_by_role("button", name="Документ", exact=True).click()
    area.focus()
    area.press("Control+End")
    page.keyboard.type(" локальное")
    expect(page.get_by_text("Редакция 3", exact=True)).to_be_visible()
    local = api.document["sections"]["1"]
    page.get_by_role("button", name="Получить изменения", exact=True).click()
    expect(page.get_by_text("Новых изменений в Eva нет", exact=True)).to_be_visible()
    assert not api.document["proposals"] and api.document["sections"]["1"] == local
    from api.technical_specifications.domain import markdown, parse_markdown

    remote = parse_markdown(api.eva.remote)
    remote["9"] = "Настоящее изменение Eva"
    api.eva.remote = markdown(remote)
    api.eva.version += 1
    page.get_by_role("button", name="Получить изменения", exact=True).click()
    preview = page.get_by_role("region", name="Предложенные изменения")
    expect(preview).to_contain_text("Настоящее изменение Eva")
    assert api.document["proposals"][-1]["conflicts"] == []
    preview.get_by_role("button", name="Применить изменения", exact=True).click()
    expect(page.get_by_text("Изменения применены", exact=True)).to_be_visible()
    assert api.document["sections"]["1"] == local and api.document["sections"]["9"] == remote["9"]
    page.get_by_role("button", name="Документ", exact=True).click()
    expect(area.locator("strong")).to_have_text("Обязательно")
    page.screenshot(path=str(tmp_path / "tz-eva-codec.png"), full_page=True)


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
def test_specification_mockup_document_formatting_navigation_and_autosave(page, base_url, tmp_path, width):
    page.set_viewport_size({"width": width, "height": 960})
    api = SpecificationApi()
    api.document["sections"]["5"] = "### Основной сценарий\n\n**Проверить права**\n\n- Открыть форму\n- Отправить данные\n\n| Поле | Тип |\n| --- | --- |\n| id | UUID |"
    _install_session(page)
    _install_common_routes(page)
    page.route("**/api/v1/source-workspaces**", api.route)
    page.goto(base_url + LOCATION)
    section = page.get_by_role("textbox", name="Раздел 5", exact=True)
    expect(section.locator("strong")).to_have_text("Проверить права")
    expect(section.locator("li")).to_have_count(2)
    expect(section.locator("td")).to_have_count(2)
    expect(page.locator('textarea[aria-label^="Раздел "]')).to_have_count(0)
    assert not [row for row in api.requests if row[0] == "PUT"]
    if width > 760:
        toc = page.get_by_role("navigation", name="Оглавление ТЗ")
        expect(toc.get_by_role("link")).to_have_count(9)
        toc.get_by_role("link", name="9. Журналирование").click()
    else:
        page.get_by_label("Перейти к разделу").select_option("9")
    target = page.locator("#tz-section-9")
    expect(target).to_be_in_viewport()
    area = page.get_by_role("textbox", name="Раздел 9", exact=True)
    area.fill("Журналировать автора и итог операции")
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    expect(page.get_by_text("Сохранено", exact=True)).to_be_visible()
    assert api.document["sections"]["9"] == "Журналировать автора и итог операции"
    page.reload()
    expect(page.get_by_role("textbox", name="Раздел 9", exact=True)).to_have_text("Журналировать автора и итог операции")
    expect(section.locator("strong")).to_have_text("Проверить права")
    assert page.locator("html").evaluate("element => element.scrollWidth <= window.innerWidth")
    assert page.locator("main.tz-workspace").evaluate("element => element.scrollWidth <= element.clientWidth + 1 && element.scrollLeft === 0")
    assert page.get_by_role("article", name="Полный текст технического задания").evaluate(
        "element => { const box = element.getBoundingClientRect(); return box.left >= 0 && box.right <= window.innerWidth; }"
    )
    page.get_by_role("button", name="Документ", exact=True).click()
    page.locator("#tz-section-1").scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / f"tz-document-{width}.png"), full_page=True)


@pytest.mark.p1
def test_specification_autosave_failure_keeps_new_input_and_explicit_retry(page, base_url):
    api = open_specification(page, base_url)
    attempts = []
    fail_save = True

    def save_route(route):
        if route.request.method == "PUT":
            attempts.append(deepcopy(route.request.post_data_json))
            if fail_save:
                return api.reject(route, "STORAGE_UNAVAILABLE", "Сохранение временно недоступно", 503)
        return api.route(route)

    page.route("**" + DETAIL, save_route)
    area = page.get_by_role("textbox", name="Раздел 9", exact=True)
    area.fill("До ошибки")
    expect(page.get_by_role("button", name="Повторить сохранение")).to_be_visible()
    expect(page.get_by_text("Не удалось сохранить", exact=True)).to_be_visible()
    area.fill("Новый ввод после ошибки")
    page.get_by_role("button", name="Чат", exact=True).click()
    expect(page.get_by_role("button", name="Отправить", exact=True)).to_be_disabled()
    assert api.document["revision"] == 1
    fail_save = False
    page.get_by_role("button", name="Повторить сохранение").click()
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    assert len(attempts) == 2
    assert attempts[-1]["sections"]["9"] == "Новый ввод после ошибки"
    page.get_by_role("button", name="Документ", exact=True).click()
    expect(area).to_have_text("Новый ввод после ошибки")


@pytest.mark.p1
def test_specification_inline_proposal_and_arbitrary_history_pair(page, base_url, tmp_path):
    api = open_specification(page, base_url)
    page.get_by_role("button", name="Чат", exact=True).click()
    page.get_by_label("Контекст чата").select_option("5")
    page.get_by_role("textbox", name="Сообщение помощнику").fill("Переименуй кнопку")
    page.get_by_role("button", name="Отправить", exact=True).click()
    expect(page.get_by_role("region", name="Предложенные изменения")).to_be_visible()
    assert api.calls("chat")[0][2]["draft"]["context"]["scope"] == "5"
    page.get_by_role("button", name="Документ", exact=True).click()
    preview = page.locator("#tz-section-5").get_by_role("region", name="Предложенные изменения")
    expect(preview).to_contain_text("Кнопка Записать")
    assert api.document["revision"] == 1
    preview.scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / "tz-inline-proposal.png"), full_page=True)
    preview.get_by_role("button", name="Применить изменения").click()
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    page.get_by_role("textbox", name="Раздел 9", exact=True).fill("Только в третьей редакции")
    expect(page.get_by_text("Редакция 3", exact=True)).to_be_visible()
    page.get_by_role("button", name="История", exact=True).click()
    history = page.get_by_role("complementary", name="История редакций")
    history.get_by_label("Прежняя редакция").select_option("1")
    history.get_by_label("Следующая редакция").select_option("2")
    expect(history).to_contain_text("Редакция 1 → редакция 2")
    expect(history).not_to_contain_text("Только в третьей редакции")
    page.screenshot(path=str(tmp_path / "tz-history-pair.png"), full_page=True)
    assert len(api.calls("apply")) == 1 and api.document["revision"] == 3


def open_specification(page, base_url, role="owner"):
    _install_session(page)
    _install_common_routes(page)
    api = SpecificationApi(role)
    page.route("**/api/v1/source-workspaces**", api.route)
    page.goto(base_url + LOCATION)
    expect(page.get_by_role("heading", name="ТЗ загрузки", exact=True)).to_be_visible(timeout=30_000)
    return api


def open_specification_list(page, base_url, api):
    _install_session(page)
    _install_common_routes(page)
    page.route("**/api/v1/source-workspaces**", api.route)
    page.goto(base_url + "/technical-specifications?workspace=workspace")
    expect(page.get_by_role("heading", name="Проект ТЗ", exact=True)).to_be_visible(timeout=30_000)


@pytest.mark.p1
def test_specification_list_loading_empty_and_create_open_nine_sections(page, base_url):
    api = SpecificationApi()
    api.documents, api.hold_list_response = [], True
    open_specification_list(page, base_url, api)
    expect(page.get_by_role("status").filter(has_text="Загрузка…")).to_be_visible()
    assert api.pending_list_response is not None
    api.hold_list_response = False
    api.release_list_response()
    expect(page.get_by_text("В проекте пока нет ТЗ.", exact=True)).to_be_visible()
    page.locator("summary").filter(has_text="Создать ТЗ из Eva").click()
    expect(page.get_by_role("region", name="Импорт из Eva", exact=True)).to_be_visible()
    page.get_by_role("button", name="Создать ТЗ", exact=True).click()
    create = page.get_by_role("button", name="Создать ТЗ", exact=True)
    expect(create).to_be_disabled()
    page.get_by_role("textbox", name="Название ТЗ", exact=True).fill("Новое техническое задание")
    create.click()
    expect(page.get_by_role("heading", name="Новое техническое задание", exact=True)).to_be_visible()
    assert api.created_documents == 1
    for number in range(1, 10):
        expect(page.get_by_role("textbox", name=f"Раздел {number}", exact=True)).to_be_visible()
    assert api.document["revision"] == 1 and api.document["eva"] is None
    assert api.document["messages"] == api.document["comments"] == api.document["proposals"] == []


@pytest.mark.p1
def test_specification_list_and_create_failure_preserve_title_for_retry(page, base_url):
    api = SpecificationApi()
    api.documents, api.list_error = [], True
    open_specification_list(page, base_url, api)
    expect(page.get_by_role("alert")).to_contain_text("Список временно недоступен", timeout=30_000)
    expect(page.get_by_text("В проекте пока нет ТЗ.", exact=True)).to_have_count(0)
    api.list_error = False
    page.reload()
    expect(page.get_by_text("В проекте пока нет ТЗ.", exact=True)).to_be_visible()
    page.get_by_role("button", name="Создать ТЗ", exact=True).click()
    name = page.get_by_role("textbox", name="Название ТЗ", exact=True)
    name.fill("Сохранить название после ошибки")
    api.create_error = True
    page.get_by_role("button", name="Создать ТЗ", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Создание временно недоступно")
    expect(name).to_have_value("Сохранить название после ошибки")
    assert api.created_documents == 0 and api.documents == []
    api.create_error = False
    page.get_by_role("button", name="Создать ТЗ", exact=True).click()
    expect(page.get_by_role("heading", name="Сохранить название после ошибки", exact=True)).to_be_visible()
    attempts = [body for method, path, body in api.requests if method == "POST" and path == COLLECTION]
    assert attempts == [{"title": "Сохранить название после ошибки"}] * 2
    assert api.created_documents == 1


@pytest.mark.p1
def test_specification_creation_goal_uses_the_same_autosave_path(page, base_url, tmp_path):
    api = SpecificationApi()
    api.documents = []
    open_specification_list(page, base_url, api)
    page.get_by_role("button", name="Создать ТЗ", exact=True).click()
    page.get_by_role("textbox", name="Название ТЗ", exact=True).fill("ТЗ с целью")
    page.get_by_text("Добавить цель и источники", exact=True).click()
    page.get_by_role("textbox", name="Цель и границы", exact=True).fill("Сократить время обработки заявлений")
    page.screenshot(path=str(tmp_path / "tz-create.png"), full_page=True)
    page.get_by_role("button", name="Создать ТЗ", exact=True).click()
    expect(page.get_by_role("heading", name="ТЗ с целью", exact=True)).to_be_visible()
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="Раздел 1", exact=True)).to_have_text("Сократить время обработки заявлений")
    assert api.created_documents == 1
    assert api.document["sections"]["1"] == "Сократить время обработки заявлений"


@pytest.mark.p1
def test_specification_reader_empty_list_cannot_create_or_import(page, base_url):
    api = SpecificationApi("reader")
    api.documents = []
    open_specification_list(page, base_url, api)
    expect(page.get_by_text("В проекте пока нет ТЗ.", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="Название ТЗ", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Создать ТЗ", exact=True)).to_be_disabled()
    expect(page.get_by_role("region", name="Импорт из Eva", exact=True)).to_have_count(0)
    assert not [item for item in api.requests if item[0] == "POST"]


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
def test_specification_chat_send_and_apply_with_keyboard(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    api = open_specification(page, base_url)
    initial = deepcopy(api.document["sections"])
    mode = page.get_by_role("button", name="Чат", exact=True)
    mode.focus()
    mode.press("Space")
    message = page.get_by_role("textbox", name="Сообщение помощнику")
    message.focus()
    message.press_sequentially("Переименуй кнопку")
    send = page.get_by_role("button", name="Отправить", exact=True)
    # Traverse actual focusable controls; a pointer click cannot activate this path.
    for _ in range(6):
        page.keyboard.press("Tab")
        if send.evaluate("element => element === document.activeElement"):
            break
    expect(send).to_be_focused()
    send.press("Enter")
    preview = page.get_by_role("region", name="Предложенные изменения")
    expect(preview).to_contain_text("Кнопка Записать")
    assert api.document["sections"] == initial
    apply = preview.get_by_role("button", name="Применить изменения")
    apply.focus()
    apply.press("Enter")
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    assert len(api.calls("chat")) == len(api.calls("apply")) == 1
    assert api.document["sections"] == {**initial, "5": "Кнопка Записать"}
    assert page.locator("html").evaluate("element => element.scrollWidth <= window.innerWidth")


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
def test_specification_chat_draft_question_and_single_apply(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    api = open_specification(page, base_url)
    initial = deepcopy(api.document["sections"])
    area = page.get_by_role("textbox", name="Раздел 1", exact=True)
    area.click()
    area.press("ControlOrMeta+Home")
    for _ in range(3):
        area.press("ArrowRight")
    for _ in range(6):
        area.press("Shift+ArrowRight")
    page.get_by_role("heading", name="1. Назначение и цель").locator("..").get_by_role("button", name="В чат").click()
    expect(page.get_by_label("Контекст чата")).to_have_value("selection")
    message = page.get_by_role("textbox", name="Сообщение помощнику")
    message.fill("Полный контекст сохраняется")
    expect(page.get_by_text("Черновик сохранён", exact=True)).to_be_visible()
    saved = deepcopy(api.draft)
    assert saved["context"] == {"document_id": "doc", "revision": 1, "scope": "selection", "anchor": {"section": "1", "quote": "цитата", "prefix": "До ", "suffix": " после"}}
    page.reload()
    page.get_by_role("button", name="Чат", exact=True).click()
    expect(message).to_have_value(saved["text"])
    expect(page.get_by_label("Контекст чата")).to_have_value("selection")
    page.get_by_label("Контекст чата").select_option("all")
    message.fill("Какой лимит?")
    page.get_by_role("button", name="Отправить", exact=True).click()
    expect(page.get_by_text("Лимит не задан", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Применить изменения")).to_have_count(0)
    assert api.document["revision"] == 1 and api.document["sections"] == initial
    page.get_by_label("Контекст чата").select_option("5")
    message.fill("Переименуй кнопку")
    page.get_by_role("button", name="Отправить", exact=True).click()
    preview = page.get_by_role("region", name="Предложенные изменения")
    expect(preview).to_contain_text("Кнопка Записать")
    assert api.document["sections"] == initial
    preview.get_by_role("button", name="Применить изменения").click()
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    assert len(api.calls("apply")) == 1 and api.document["revision"] == 2
    assert api.document["sections"] == {**initial, "5": "Кнопка Записать"}
    expect(page.get_by_role("button", name="Применить изменения")).to_have_count(0)
    assert page.locator("html").evaluate("el => el.scrollWidth <= window.innerWidth")


@pytest.mark.p1
def test_specification_comments_keep_scope_reply_and_document_separate(page, base_url):
    api = open_specification(page, base_url)
    initial = deepcopy(api.document["sections"])
    page.get_by_role("button", name="Комментарии · 0").click()
    panel = page.get_by_role("complementary", name="Комментарии")
    scope = panel.get_by_label("Контекст комментариев")
    entry = panel.get_by_role("textbox", name="Текст комментария")
    scope.select_option("1")
    entry.fill("Черновик раздела 1")
    scope.select_option("5")
    entry.fill("Проверить кнопку")
    scope.select_option("1")
    expect(entry).to_have_value("Черновик раздела 1")
    scope.select_option("5")
    expect(entry).to_have_value("Проверить кнопку")
    panel.get_by_role("button", name="Добавить комментарий").click()
    expect(panel.get_by_text("Проверить кнопку", exact=True)).to_be_visible()
    parent_id = api.document["comments"][0]["id"]
    panel.get_by_role("button", name="Ответить", exact=True).click()
    entry.fill("Ответ в этой ветке")
    panel.get_by_role("button", name="Добавить комментарий").click()
    expect(panel.locator("p").filter(has_text="Ответ в этой ветке")).to_be_visible()
    reply = api.calls("comments")[-1][2]
    assert reply["action"] == "reply" and reply["comment_id"] == parent_id
    panel.get_by_role("button", name="Решён", exact=True).click()
    expect(panel.locator("article")).to_have_count(0)
    panel.get_by_role("checkbox", name="Показывать решённые").check()
    expect(panel.locator("article")).to_have_count(1)
    scope.select_option("1")
    expect(entry).to_have_value("Черновик раздела 1")
    assert api.document["sections"] == initial and api.document["revision"] == 1


@pytest.mark.p1
def test_specification_eva_failure_preserves_draft_and_requires_explicit_retry(page, base_url):
    api = open_specification(page, base_url)
    api.fail_publish = True
    page.get_by_role("button", name="Чат", exact=True).click()
    message = page.get_by_role("textbox", name="Сообщение помощнику")
    message.fill("Не терять этот вопрос")
    page.get_by_role("button", name="Eva", exact=True).click()
    page.get_by_role("button", name="Обновить в Eva").click()
    expect(page.get_by_role("alert")).to_contain_text("Ответ Eva не подтверждён")
    assert len(api.calls("publish-eva")) == 1
    expect(page.get_by_text("Страница Eva обновлена", exact=True)).to_have_count(0)
    api.fail_publish = False
    page.get_by_role("button", name="Обновить в Eva").click()
    expect(page.get_by_text("В Eva выгружена редакция 1.", exact=True)).to_be_visible()
    assert len(api.calls("publish-eva")) == 2
    assert all(call[1] == DETAIL + "/publish-eva" for call in api.calls("publish-eva"))
    expect(message).to_have_value("Не терять этот вопрос")
    page.get_by_role("button", name="Документ", exact=True).click()
    page.get_by_role("textbox", name="Раздел 9", exact=True).fill("Ещё не сохранено")
    expect(page.get_by_role("button", name="Обновить в Eva")).to_be_disabled()
    expect(page.get_by_role("button", name="Получить изменения")).to_be_disabled()


@pytest.mark.p1
def test_specification_reader_cannot_launch_mutations_but_can_keep_personal_draft(page, base_url):
    api = open_specification(page, base_url, "reader")
    expect(page.get_by_role("textbox", name="Раздел 1", exact=True)).not_to_be_editable()
    expect(page.get_by_text("Только чтение", exact=True)).to_be_visible()
    page.get_by_role("button", name="Чат", exact=True).click()
    page.get_by_role("textbox", name="Сообщение помощнику").fill("Личный вопрос читателя")
    expect(page.get_by_role("button", name="Отправить", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Eva", exact=True).click()
    expect(page.get_by_role("button", name="Обновить в Eva")).to_be_disabled()
    expect(page.get_by_role("button", name="Получить изменения")).to_be_disabled()
    page.reload()
    page.get_by_role("button", name="Чат", exact=True).click()
    expect(page.get_by_role("textbox", name="Сообщение помощнику")).to_have_value("Личный вопрос читателя")
    assert not [item for item in api.requests if item[0] == "POST"]


@pytest.mark.p1
def test_specification_history_restore_keeps_old_revision(page, base_url):
    api = open_specification(page, base_url)
    initial = deepcopy(api.document["sections"])
    page.get_by_role("textbox", name="Раздел 5", exact=True).fill("Новый сценарий")
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    page.get_by_role("button", name="История", exact=True).click()
    history = page.get_by_role("complementary", name="История редакций")
    history.get_by_role("button", name="Редакция 1 ·").click()
    expect(history).to_contain_text("Кнопка Сохранить")
    history.get_by_role("button", name="Восстановить как новую редакцию").click()
    expect(page.get_by_text("Редакция 3", exact=True)).to_be_visible()
    assert api.document["sections"] == initial
    assert api.revisions[2]["sections"]["5"] == "Новый сценарий"
    assert len(api.calls("restore")) == 1 and sorted(api.revisions) == [1, 2, 3]


@pytest.mark.p1
def test_specification_chat_timeout_retries_saved_message_without_duplication(page, base_url):
    api = open_specification(page, base_url)
    api.fail_chat = True
    page.get_by_role("button", name="Чат", exact=True).click()
    page.get_by_label("Контекст чата").select_option("5")
    page.get_by_role("textbox", name="Сообщение помощнику").fill("Переименуй кнопку")
    page.get_by_role("button", name="Отправить", exact=True).click()
    expect(page.get_by_role("button", name="Повторить сообщение")).to_be_visible()
    first = deepcopy(api.calls("chat")[0][2]["draft"])
    assert api.document["revision"] == 1
    api.fail_chat = False
    page.get_by_role("button", name="Повторить сообщение").click()
    expect(page.get_by_role("button", name="Применить изменения")).to_be_visible()
    assert len(api.calls("chat")) == 2
    assert api.calls("chat")[1][2]["draft"] == first
    assert len([item for item in api.document["messages"] if item["role"] == "user"]) == 1
    assert api.document["revision"] == 1


@pytest.mark.p1
def test_specification_import_lost_response_resumes_after_reload_without_second_import(page, base_url):
    api = open_specification(page, base_url)
    page.get_by_role("button", name="Технические задания", exact=True).click()
    page.locator("summary").filter(has_text="Создать ТЗ из Eva").click()
    page.get_by_role("textbox", name="Название или ссылка на страницу Eva").fill("https://eva.test/page")
    page.get_by_role("button", name="Найти в Eva", exact=True).click()
    page.get_by_role("radio", name="Тестовая страница").check()
    page.get_by_role("button", name="Импортировать выбранную страницу").click()
    expect(page.get_by_role("alert")).to_contain_text("Ответ импорта потерян")
    pending = deepcopy(api.import_request)
    assert pending["url"] == "https://eva.test/page" and pending["copy"] is False
    assert pending["selection"] == {"id": "page", "project_id": "eva-project", "version": "v1"}
    page.reload()
    page.get_by_role("button", name="Продолжить импорт").click()
    expect(page.get_by_role("heading", name="ТЗ загрузки", exact=True)).to_be_visible()
    expect(page).to_have_url(base_url + LOCATION)
    imports = [item for item in api.requests if item[0] == "POST" and item[1] == COLLECTION + "/import-eva"]
    assert len(imports) == 1 and imports[0][2] == pending


@pytest.mark.p1
@pytest.mark.parametrize("choice,label,limit", [("local", "Сохранить мои значения", "35"), ("remote", "Использовать значения Eva", "50")], ids=["local", "remote"])
def test_specification_eva_conflict_requires_choice_then_single_apply(page, base_url, choice, label, limit):
    api = open_specification(page, base_url)
    api.document["sections"].update({"4": "Лимит 35 файлов. Размер 40 МБ.", "5": "Выбрать до 35 файлов", "8": "Отклонить более 35 файлов"})
    api.document["proposals"] = [
        {
            "id": "proposal",
            "base_revision": 1,
            "source": "eva",
            "status": "needs_resolution",
            "sections": deepcopy(api.document["sections"]),
            "issues": ["Разные лимиты в разделах 4 и 5"],
            "conflicts": [{"section": "4", "base": "20 файлов", "local": "35 файлов", "remote": "50 файлов"}],
        }
    ]
    page.reload()
    page.get_by_role("button", name="Чат", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Разные лимиты в разделах 4 и 5")
    expect(page.get_by_role("button", name="Применить изменения")).to_have_count(0)
    page.get_by_role("button", name=label, exact=True).click()
    preview = page.get_by_role("region", name="Предложенные изменения")
    # Only changed sections appear in the preview; choosing local values keeps
    # sections 4 and 5 unchanged. Inspect the saved result for both choices.
    expect(preview).to_contain_text("Журнал Eva сохранён")
    assert api.calls("resolve-eva")[0][2] == {"version": 1, "proposal_id": "proposal", "choice": choice}
    assert api.document["revision"] == 1
    preview.get_by_role("button", name="Применить изменения").click()
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    assert len(api.calls("apply")) == 1
    page.get_by_role("button", name="Документ", exact=True).click()
    expect(page.get_by_role("textbox", name="Раздел 4", exact=True)).to_have_text(f"Лимит {limit} файлов. Размер 40 МБ.")
    expect(page.get_by_role("textbox", name="Раздел 5", exact=True)).to_have_text(f"Выбрать до {limit} файлов")
    expect(page.get_by_role("textbox", name="Раздел 8", exact=True)).to_have_text(f"Отклонить более {limit} файлов. Журнал Eva сохранён.")


@pytest.mark.p1
def test_specification_eva_pull_previews_once_and_repeat_preserves_document(page, base_url):
    api = open_specification(page, base_url)
    original = deepcopy(api.document["sections"])
    api.pull_sections = {**original, "9": "Независимый журнал Eva"}
    page.get_by_role("button", name="Eva", exact=True).click()
    page.get_by_role("button", name="Получить изменения").click()
    preview = page.get_by_role("region", name="Предложенные изменения")
    expect(preview).to_contain_text("Независимый журнал Eva")
    assert api.document["sections"] == original and api.document["revision"] == 1
    page.get_by_role("button", name="Получить изменения").click()
    expect(page.get_by_text("Новых изменений в Eva нет", exact=True)).to_be_visible()
    assert api.pull_preparations == 1 and len(api.document["proposals"]) == 1
    preview.get_by_role("button", name="Применить изменения").click()
    expect(page.get_by_text("Редакция 2", exact=True)).to_be_visible()
    assert api.document["sections"] == api.pull_sections
    assert api.document["eva"]["remote_version"] == "v2" and len(api.calls("apply")) == 1
    page.get_by_role("button", name="Получить изменения").click()
    expect(page.get_by_text("Новых изменений в Eva нет", exact=True)).to_be_visible()
    assert api.pull_preparations == 1 and api.document["revision"] == 2


@pytest.mark.p1
def test_specification_eva_linked_page_opens_existing_document_without_reimporting_text(page, base_url):
    api = open_specification(page, base_url)
    original = deepcopy(api.document)
    api.import_existing = True
    api.eva_pages = [
        {
            "id": "page",
            "project_id": "eva-project",
            "version": "v1",
            "name": "Связанная страница",
            "web_url": "https://eva.test/page",
            "binding_available": False,
            "linked_document": {"id": "doc", "workspace_id": "workspace"},
        }
    ]
    page.get_by_role("button", name="Технические задания", exact=True).click()
    page.locator("summary").filter(has_text="Создать ТЗ из Eva").click()
    page.get_by_role("textbox", name="Название или ссылка на страницу Eva").fill("Связанная страница")
    page.get_by_role("button", name="Найти в Eva", exact=True).click()
    page.get_by_role("radio", name="Связанная страница").check()
    page.get_by_role("button", name="Открыть ТЗ", exact=True).click()
    expect(page).to_have_url(base_url + LOCATION)
    expect(page.get_by_role("heading", name="ТЗ загрузки", exact=True)).to_be_visible()
    assert api.document == original and api.created_documents == 0
    imports = [call for call in api.requests if call[0] == "POST" and call[1] == COLLECTION + "/import-eva"]
    assert len(imports) == 1 and imports[0][2]["copy"] is False
    assert imports[0][2]["selection"] == {"id": "page", "project_id": "eva-project", "version": "v1"}


@pytest.mark.p1
def test_specification_processing_chat_survives_reload_and_check_does_not_regenerate(page, base_url):
    api = open_specification(page, base_url)
    api.chat_processing = True
    page.get_by_role("button", name="Чат", exact=True).click()
    page.get_by_role("textbox", name="Сообщение помощнику").fill("Уточни кнопку")
    page.get_by_role("button", name="Отправить", exact=True).click()
    expect(page.get_by_text("Запрос сохранён · помощник готовит ответ", exact=True)).to_be_visible()
    original = deepcopy(api.calls("chat")[0][2]["draft"])
    page.reload()
    page.get_by_role("button", name="Чат", exact=True).click()
    page.get_by_role("button", name="Проверить запрос", exact=True).click()
    expect(page.get_by_text("Запрос сохранён · помощник готовит ответ", exact=True)).to_be_visible()
    assert len(api.calls("chat")) == 2 and api.calls("chat")[1][2]["draft"] == original
    assert api.model_calls == 1 and len(api.document["messages"]) == 1
    api.complete_chat()
    expect(page.get_by_text("Предлагаю уточнить кнопку", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Применить изменения")).to_be_visible()
    assert api.model_calls == 1 and len(api.document["messages"]) == 2


@pytest.mark.p1
def test_specification_comment_lost_response_retries_same_id_without_duplication(page, base_url):
    api = open_specification(page, base_url)
    initial = deepcopy(api.document["sections"])
    api.lose_comment_response_once = True
    page.get_by_role("button", name="Комментарии · 0").click()
    panel = page.get_by_role("complementary", name="Комментарии")
    entry = panel.get_by_role("textbox", name="Текст комментария")
    entry.fill("Комментарий с потерянным ответом")
    panel.get_by_role("button", name="Добавить комментарий").click()
    expect(page.get_by_role("alert")).to_contain_text("Ответ комментария потерян")
    expect(entry).to_have_value("Комментарий с потерянным ответом")
    first = deepcopy(api.calls("comments")[0][2])
    panel.get_by_role("button", name="Добавить комментарий").click()
    expect(entry).to_have_value("")
    repeated = api.calls("comments")[1][2]
    assert all(repeated[key] == first[key] for key in ["message_id", "text", "context"])
    assert len(api.document["comments"]) == 1 and api.document["sections"] == initial and api.document["revision"] == 1


@pytest.mark.p1
def test_specification_comment_response_preserves_text_typed_during_request(page, base_url):
    api = open_specification(page, base_url)
    initial = deepcopy(api.document["sections"])
    api.hold_comment_response = True
    page.get_by_role("button", name="Комментарии · 0").click()
    panel = page.get_by_role("complementary", name="Комментарии")
    entry = panel.get_by_role("textbox", name="Текст комментария")
    entry.fill("Первый комментарий")
    panel.get_by_role("button", name="Добавить комментарий").click()
    try:
        expect(panel.get_by_role("button", name="Добавить комментарий")).to_be_disabled()
        entry.fill("Новый ввод во время запроса")
        api.release_comment_response()
    finally:
        if api.pending_comment_response is not None:
            api.release_comment_response()
    expect(panel.get_by_text("Первый комментарий", exact=True)).to_be_visible()
    expect(entry).to_have_value("Новый ввод во время запроса")
    panel.get_by_role("button", name="Добавить комментарий").click()
    expect(entry).to_have_value("")
    assert [item["text"] for item in api.document["comments"]] == ["Первый комментарий", "Новый ввод во время запроса"]
    assert api.calls("comments")[0][2]["message_id"] != api.calls("comments")[1][2]["message_id"]
    assert api.document["sections"] == initial and api.document["revision"] == 1


@pytest.mark.p1
def test_specification_markdown_download_contains_saved_nine_sections_only(page, base_url):
    api = open_specification(page, base_url)
    api.document["messages"] = [{"id": "answer", "role": "assistant", "text": "CHAT_ONLY"}]
    api.document["comments"] = [
        {"id": "comment", "author_id": "qa", "text": "COMMENT_ONLY", "context": {"document_id": "doc", "scope": "all", "revision": 1, "anchor": None}, "resolved": False, "replies": []}
    ]
    api.document["proposals"] = [{"id": "proposal", "source": "chat", "base_revision": 1, "status": "ready", "sections": {**api.document["sections"], "9": "PROPOSAL_ONLY"}}]
    page.reload()
    page.get_by_role("button", name="Чат", exact=True).click()
    page.get_by_role("textbox", name="Сообщение помощнику").fill("DRAFT_ONLY")
    with page.expect_download() as received:
        page.get_by_role("button", name="Выгрузить .md", exact=True).click()
    download = received.value
    assert download.suggested_filename == "ТЗ-загрузки-r1.md"
    body = Path(download.path()).read_text(encoding="utf-8")
    assert len(re.findall(r"^## [1-9]\. ", body, re.MULTILINE)) == 9
    assert "## 1. Назначение и цель" in body and "## 9. Журналирование" in body
    assert all(value in body for value in api.document["sections"].values())
    assert all(marker not in body for marker in ["CHAT_ONLY", "COMMENT_ONLY", "PROPOSAL_ONLY", "DRAFT_ONLY"])
    assert api.calls("export")[0][2] == {"revision": 1}
    page.get_by_role("button", name="Документ", exact=True).click()
    page.get_by_role("textbox", name="Раздел 9", exact=True).fill("Не сохранено")
    expect(page.get_by_role("button", name="Выгрузить .md", exact=True)).to_be_disabled()


@pytest.mark.p1
def test_specification_markdown_conflict_creates_no_download_and_preserves_drafts(page, base_url):
    api = open_specification(page, base_url)
    original = deepcopy(api.document["sections"])
    api.export_conflict = True
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    page.get_by_role("button", name="Чат", exact=True).click()
    entry = page.get_by_role("textbox", name="Сообщение помощнику")
    entry.fill("Вопрос после ошибки экспорта")
    page.get_by_role("button", name="Выгрузить .md", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Обновите документ перед выгрузкой")
    expect(entry).to_have_value("Вопрос после ошибки экспорта")
    assert not downloads and len(api.calls("export")) == 1
    assert api.document["sections"] == original and api.document["revision"] == 1


@pytest.mark.p1
@pytest.mark.parametrize("quote_state", ["removed", "ambiguous"])
def test_specification_stale_quote_requires_explicit_context_change(page, base_url, quote_state):
    api = open_specification(page, base_url)
    area = page.get_by_role("textbox", name="Раздел 1", exact=True)
    area.click()
    area.press("ControlOrMeta+Home")
    for _ in range(3):
        area.press("ArrowRight")
    for _ in range(6):
        area.press("Shift+ArrowRight")
    page.get_by_role("heading", name="1. Назначение и цель").locator("..").get_by_role("button", name="В чат").click()
    entry = page.get_by_role("textbox", name="Сообщение помощнику")
    entry.fill("Вопрос к исходному фрагменту")
    expect(page.get_by_text("Черновик сохранён", exact=True)).to_be_visible()
    original = deepcopy(api.draft)
    api.document["sections"]["1"] = "Другой текст" if quote_state == "removed" else "До цитата после / До цитата после"
    api.document["revision"], api.document["version"] = 2, api.document["version"] + 1
    page.reload()
    page.get_by_role("button", name="Чат", exact=True).click()
    expect(entry).to_have_value(original["text"])
    expect(page.get_by_label("Контекст чата")).to_have_value("selection")
    expect(page.get_by_role("alert")).to_contain_text("Исходный фрагмент изменён или удалён")
    expect(page.get_by_role("button", name="Отправить", exact=True)).to_be_disabled()
    assert api.draft["context"] == original["context"] and not api.calls("chat")
    page.get_by_role("button", name="Использовать весь раздел", exact=True).click()
    expect(page.get_by_label("Контекст чата")).to_have_value("1")
    expect(entry).to_have_value(original["text"])
    expect(page.get_by_role("button", name="Отправить", exact=True)).to_be_enabled()


@pytest.mark.p1
def test_specification_late_chat_answer_keeps_new_draft_and_cannot_apply_old_revision(page, base_url):
    api = open_specification(page, base_url)
    api.chat_processing = True
    page.get_by_role("button", name="Чат", exact=True).click()
    entry = page.get_by_role("textbox", name="Сообщение помощнику")
    entry.fill("Уточни исходную редакцию")
    page.get_by_role("button", name="Отправить", exact=True).click()
    expect(page.get_by_text("Запрос сохранён · помощник готовит ответ", exact=True)).to_be_visible()
    entry.fill("Следующий вопрос с полным контекстом")
    expect(page.get_by_text("Черновик сохранён", exact=True)).to_be_visible()
    saved = deepcopy(api.draft)
    api.document["sections"]["9"] = "Параллельная редакция 2"
    api.document["revision"], api.document["version"] = 2, api.document["version"] + 1
    current = deepcopy(api.document["sections"])
    api.complete_chat()
    expect(page.get_by_text("Предлагаю уточнить кнопку", exact=True)).to_be_visible()
    expect(page.get_by_text(re.compile("Относится к редакции 1")).first).to_be_visible()
    expect(page.get_by_role("button", name="Применить изменения")).to_have_count(0)
    expect(entry).to_have_value(saved["text"])
    assert api.draft["context"] == saved["context"] and api.document["sections"] == current
    assert api.model_calls == 1 and not api.calls("apply")


@pytest.mark.p1
@pytest.mark.parametrize("action", ["chat", "publish"])
def test_specification_revoked_access_rejects_action_without_success_or_draft_loss(page, base_url, action):
    api = open_specification(page, base_url)
    original = deepcopy(api.document)
    page.get_by_role("button", name="Чат", exact=True).click()
    entry = page.get_by_role("textbox", name="Сообщение помощнику")
    entry.fill("Сохранить при отзыве доступа")
    expect(page.get_by_text("Черновик сохранён", exact=True)).to_be_visible()
    api.access_revoked = True
    if action == "chat":
        page.get_by_role("button", name="Отправить", exact=True).click()
    else:
        page.get_by_role("button", name="Eva", exact=True).click()
        page.get_by_role("button", name="Обновить в Eva", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Доступ к проекту отозван")
    expect(entry).to_have_value("Сохранить при отзыве доступа")
    expect(page.get_by_text("В Eva выгружена редакция 1.", exact=True)).to_have_count(0)
    assert api.document == original and api.model_calls == 0


@pytest.mark.p1
@pytest.mark.parametrize("failure", ["stale_eva", "contradiction"])
def test_specification_failed_eva_apply_preserves_candidate_document_and_draft(page, base_url, failure):
    api = open_specification(page, base_url)
    original = deepcopy(api.document["sections"])
    api.pull_sections = {**original, "9": "Независимая правка Eva"}
    page.get_by_role("button", name="Eva", exact=True).click()
    page.get_by_role("button", name="Получить изменения", exact=True).click()
    preview = page.get_by_role("region", name="Предложенные изменения")
    expect(preview).to_be_visible()
    entry = page.get_by_role("textbox", name="Сообщение помощнику")
    entry.fill("Черновик перед применением")
    api.apply_failure = failure
    preview.get_by_role("button", name="Применить изменения").click()
    expected = "Страница Eva изменилась" if failure == "stale_eva" else "Разные лимиты в разделах 4 и 5"
    expect(page.get_by_role("alert")).to_contain_text(expected)
    expect(entry).to_have_value("Черновик перед применением")
    assert len(api.calls("apply")) == 1 and api.document["revision"] == 1
    assert api.document["sections"] == original and api.document["proposals"][0]["sections"] == api.pull_sections


@pytest.mark.p1
def test_specification_failed_eva_resolution_keeps_choice_and_draft(page, base_url):
    api = open_specification(page, base_url)
    original = deepcopy(api.document["sections"])
    api.document["proposals"] = [
        {
            "id": "proposal",
            "source": "eva",
            "base_revision": 1,
            "status": "needs_resolution",
            "sections": original,
            "conflicts": [{"section": "9", "base": "База", "local": "Локальный журнал", "remote": "Журнал Eva"}],
        }
    ]
    api.resolve_error = True
    page.reload()
    page.get_by_role("button", name="Чат", exact=True).click()
    entry = page.get_by_role("textbox", name="Сообщение помощнику")
    entry.fill("Не терять при смене Eva")
    page.get_by_role("button", name="Сохранить мои значения", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Страница Eva изменилась")
    expect(entry).to_have_value("Не терять при смене Eva")
    expect(page.get_by_role("button", name="Применить изменения")).to_have_count(0)
    assert len(api.calls("resolve-eva")) == 1 and not api.calls("apply")
    assert api.document["sections"] == original and api.document["revision"] == 1
    assert api.document["proposals"][0]["status"] == "needs_resolution"
