import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from business_documents.domain.comment_anchor import validate_comment_anchor
from business_documents.domain.content import render_document_ast, render_section_text
from test.playwright.helpers._next_apps_helpers import RESULT_TIMEOUT_MS, _goto_home


AXE_SCRIPT = Path(os.environ.get("RAGFLOW_AXE_SCRIPT", str(Path(__file__).resolve().parents[3] / "web" / "node_modules" / "axe-core" / "axe.min.js")))


def _envelope(data):
    return {"code": 0, "data": data}


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 1280, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_business_document_comment_to_saved_edition_pilot(page, base_url, width, theme):
    """Synthetic UI journey; responses do not qualify real inference/storage."""
    from copy import deepcopy

    page.set_viewport_size({"width": width, "height": 900 if width > 1000 else 844})
    _install_session(page)
    page.add_init_script(f"localStorage.setItem('ragflow-ui-theme', '{theme}')")
    page.route("**/api/v1/**", lambda route: route.fulfill(json=_envelope({})))
    _install_common_routes(page)
    initial_text = "Срок удержания записи — 15 минут."
    updated_text = "Срок удержания записи — 20 минут."
    comment_text = "Изменить срок удержания записи с 15 на 20 минут."
    projection = _projection(allowed_commands=["ADD_COMMENT", "REQUEST_REVIEW_ASSESSMENT"])
    projection.update(title="Запись в отделение · пилот", state_version=11, latest_exports=[])
    revision = projection["current_revision"]
    template = json.loads((Path(__file__).resolve().parents[3] / "agent/business_requirements/templates/business_requirements.v1.json").read_text(encoding="utf-8"))
    outline = template["sections"]
    revision["document_ast"]["sections"] = [{"id": item["id"], "title": item["title"], "blocks": [{"type": "paragraph", "text": initial_text}] if item["id"] == "1" else []} for item in outline]
    revision.update(
        revision_id="revision-1",
        revision_number=1,
        section_texts={item["id"]: render_section_text(item) for item in revision["document_ast"]["sections"]},
        body_markdown=render_document_ast(revision["document_ast"], template),
    )
    projection["protocol"] = {"questions": [], "proposals": [], "comments": []}
    revisions = [deepcopy(revision)]
    commands = []

    def respond(route, data):
        route.fulfill(json=_envelope(data))

    def documents(route):
        nonlocal projection
        request = route.request
        path = urlparse(request.url).path
        if path.endswith(("/capabilities", "/eva/access")):
            route.fallback()
        elif path.endswith("/catalog"):
            respond(route, {"items": [], "total": 0})
        elif path.endswith("/revisions"):
            respond(route, revisions)
        elif path == "/api/v1/business-documents":
            respond(
                route,
                {
                    "items": [
                        {
                            "document_id": "doc-ui-1",
                            "title": projection["title"],
                            "lifecycle_state": "REVIEW",
                            "operation_state": "IDLE",
                            "current_revision_number": projection["current_revision"]["revision_number"],
                            "update_time": 1_756_000_000,
                        }
                    ],
                    "page": 1,
                    "page_size": 20,
                    "total": 1,
                },
            )
        elif request.method == "GET":
            respond(route, projection)
        else:
            command = request.post_data_json
            commands.append(command)
            assert command["expected_state_version"] == projection["state_version"]
            projection["state_version"] += 1
            kind = command["type"]
            protocol = projection["protocol"]
            if kind == "ADD_COMMENT":
                payload = command["payload"]
                assert payload["text"] == comment_text and payload["revision_id"] == "revision-1"
                protocol["comments"].append(
                    {
                        "comment_id": "comment-pilot",
                        "text": payload["text"],
                        "anchor": payload["anchor"],
                        "anchor_status": "ANCHORED",
                        "revision_id": payload["revision_id"],
                        "section_id": payload["section_id"],
                    }
                )
            elif kind == "REQUEST_REVIEW_ASSESSMENT":
                protocol["proposals"] = [
                    {
                        "proposal_id": "proposal-pilot",
                        "target_section_id": "1",
                        "text": "Удерживать запись 20 минут.",
                        "rationale": "Подтверждённое замечание автора",
                        "decision": "PENDING",
                        "applied": False,
                    }
                ]
                projection["allowed_commands"] = ["ADD_COMMENT", "DECIDE_PROPOSAL", "ACCEPT_PROPOSAL", "APPLY_CHANGES"]
            elif kind == "ACCEPT_PROPOSAL":
                protocol["proposals"][0]["decision"] = "ACCEPTED"
                protocol["proposals"][0]["decision_event_id"] = "event-decision-pilot"
            elif kind == "APPLY_CHANGES":
                assert command["payload"] == {"base_revision_id": "revision-1"}
                assert protocol["proposals"][0]["decision"] == "ACCEPTED"
                protocol["proposals"][0]["applied"] = True
                saved = deepcopy(projection["current_revision"])
                saved.update(
                    revision_id="revision-2",
                    revision_number=2,
                    section_texts={**projection["current_revision"]["section_texts"], "1": updated_text},
                    source_event_ids=["event-comment-pilot", "event-decision-pilot"],
                    change_basis=[
                        {
                            "event_id": "event-comment-pilot",
                            "type": "COMMENT",
                            "title": "Замечание к сроку удержания",
                            "summary": comment_text,
                            "actor_type": "AI",
                            "actor_id": "opaque-worker-pilot",
                            "initiated_by_actor_id": "business-documents-ui-user",
                            "initiated_by_actor_name": "Автор",
                            "section_id": "1",
                        }
                    ],
                )
                saved["document_ast"]["sections"][0]["blocks"][0]["text"] = updated_text
                saved["body_markdown"] = render_document_ast(saved["document_ast"], template)
                revisions.append(saved)
                projection.update(
                    current_revision=saved,
                    protocol_history=[{**deepcopy(protocol), "review_cycle": 1}],
                    protocol={"questions": [], "proposals": [], "comments": []},
                    allowed_commands=["REQUEST_EXPORT", "RESTORE_REVISION"],
                    last_application={"revision_id": "revision-2", "section_ids": ["1"]},
                )
            elif kind == "REQUEST_EXPORT":
                assert command["payload"]["revision_id"] == "revision-2"
            else:
                pytest.fail(f"Unexpected pilot command: {kind}")
            respond(
                route,
                {
                    "accepted": True,
                    "document_id": "doc-ui-1",
                    "state_version": projection["state_version"],
                    "lifecycle_state": projection["lifecycle_state"],
                    "operation_state": "IDLE",
                    "allowed_commands": projection["allowed_commands"],
                    "document": deepcopy(projection),
                },
            )

    page.route("**/api/v1/business-documents**", documents)
    evidence = Path(os.environ["PW_ARTIFACTS_DIR"]) / "pilot" / f"{width}-{theme}"
    evidence.mkdir(parents=True, exist_ok=True)

    def capture(name):
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert page.evaluate("innerWidth") == width
        assert page.evaluate("innerHeight") == (900 if width > 1000 else 844)
        assert page.locator("html").evaluate("node => node.classList.contains('dark')") == (theme == "dark")
        if name != "01-list":
            sections = page.get_by_test_id("business-document-section")
            assert sections.evaluate_all("nodes => nodes.map(node => node.dataset.sectionId)") == [item["id"] for item in outline]
            for item in outline:
                section_node = sections.filter(has=page.get_by_role("button", name=f"{item['id']}. {item['title']}", exact=True))
                expect(section_node).to_have_count(1)
                if item["id"] != "1":
                    expect(section_node.locator("[data-section-body]")).to_have_text("Требований нет")
            (evidence / f"{name}.json").write_text(
                json.dumps({"template_version": template["template_version"], "sections": [{"id": item["id"], "title": item["title"]} for item in outline]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        page.screenshot(path=str(evidence / f"{name}.png"), full_page=True)

    page.goto(f"{base_url.rstrip('/')}/business-documents")
    document_link = page.get_by_role("link", name=re.compile("Запись в отделение · пилот"))
    expect(document_link).to_contain_text("Редакция 1", timeout=RESULT_TIMEOUT_MS)
    capture("01-list")
    document_link.click()
    pane = page.get_by_test_id("business-document-pane")
    expect(pane).to_contain_text(initial_text, timeout=RESULT_TIMEOUT_MS)
    expect(page.get_by_test_id("business-document-header")).not_to_contain_text("v11")
    page.locator('[data-section-id="5.5"]').scroll_into_view_if_needed()
    capture("01-complete-outline")
    section = page.locator('[data-section-id="1"]')
    section.locator("[data-section-body]").evaluate("""body => {
        const range = document.createRange();
        range.selectNodeContents(body.querySelector('p') || body);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
    }""")
    section.dispatch_event("mouseup")
    expect(page.get_by_test_id("business-document-comment-scope-selector")).to_contain_text("выделенный фрагмент")
    field = page.get_by_role("textbox", name="Комментарий", exact=True)
    field.fill(comment_text)
    page.get_by_role("button", name="Сохранить в заметки", exact=True).click()
    expect(page.get_by_test_id("business-document-saved-prompts")).to_contain_text("не отправляет комментарий")
    assert commands == []
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Добавить комментарий", exact=True).click()
    expect(field).to_have_value("")
    assert commands[0]["payload"]["anchor"]["selected_text"] == initial_text
    page.get_by_role("button", name="Проанализировать замечания", exact=True).click()
    proposal_card = page.get_by_test_id("business-document-proposal")
    proposal_card.get_by_role("button", name="Принять предложение", exact=True).click()
    expect(pane).to_contain_text(initial_text)
    expect(page.get_by_test_id("proposal-status-proposal-pilot")).to_have_text("Принято к применению")
    section.scroll_into_view_if_needed()
    capture("02-document")
    proposal_card.get_by_role("button", name="Подготовить изменения", exact=True).click(trial=True)
    capture("02-review")
    page.get_by_role("button", name="Применить исправления", exact=True).click()
    expect(pane).to_contain_text(updated_text)
    expect(pane).to_contain_text("Редакция 2")
    expect(page.get_by_role("button", name="Подтвердить применение")).to_have_count(0)
    page.reload()
    expect(pane).to_contain_text(updated_text, timeout=RESULT_TIMEOUT_MS)
    expect(page.get_by_test_id("business-document-protocol-empty")).to_contain_text("В работе пока ничего нет")
    capture("03-saved")
    page.get_by_role("tab", name="Завершённые обсуждения · 2", exact=True).click()
    expect(page.get_by_role("tabpanel")).to_contain_text(comment_text)
    expect(page.get_by_role("tabpanel")).to_contain_text("Внесено в редакцию 2")
    expect(page.get_by_role("tabpanel")).not_to_contain_text("Внесение в документ не подтверждено")
    capture("03-discussion")
    page.get_by_role("button", name="История документа", exact=True).click()
    history = page.get_by_test_id("business-document-history")
    expect(history).to_contain_text("Исполнитель: Помощник")
    expect(history).not_to_contain_text("opaque-worker-pilot")
    page.get_by_label("Редакция для просмотра").select_option("revision-1")
    expect(pane).to_contain_text(initial_text)
    with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/commands")):
        page.get_by_role("button", name="Markdown · текущая редакция 2", exact=True).click()
    assert commands[-1]["type"] == "REQUEST_EXPORT"
    capture("04-history")
    assert [item["type"] for item in commands] == ["ADD_COMMENT", "REQUEST_REVIEW_ASSESSMENT", "ACCEPT_PROPOSAL", "APPLY_CHANGES", "REQUEST_EXPORT"]


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
def test_compact_eva_resizing_and_review_history(page, base_url, width):
    from copy import deepcopy

    page.set_viewport_size({"width": width, "height": 1000 if width > 1000 else 844})
    _install_session(page)
    page.route("**/api/v1/**", lambda route: route.fulfill(json=_envelope({})))
    _install_common_routes(page)
    projection = _projection()
    projection["protocol"]["proposals"][0].update(decision="ACCEPTED", applied=False)
    revision = deepcopy(projection["current_revision"])
    revision.update(revision_id="revision-2", revision_number=2, source_event_ids=["old-decision"], section_texts={"1": "Запись удерживается 15 минут"})
    previous = deepcopy(revision)
    previous.update(revision_id="revision-1", revision_number=1, source_event_ids=[], section_texts={"1": "Запись сразу подтверждается"})
    projection["protocol_history"] = [
        {
            "review_cycle": 1,
            "questions": [
                {"question_id": "old-question", "text": "Как долго удерживать запись?", "status": "ANSWERED", "options": [], "allow_custom_answer": True, "answer": {"custom_answer": "15 минут"}}
            ],
            "proposals": [
                {"proposal_id": "old-proposal", "text": "Удерживать выбранную запись", "target_section_id": "1", "decision": "ACCEPTED", "applied": True, "decision_event_id": "old-decision"},
                {"proposal_id": "old-undecided", "text": "Добавить отчётность", "decision": "PENDING"},
            ],
            "comments": [],
        }
    ]
    page.route("**/api/v1/business-documents/doc-ui-1", lambda route: route.fulfill(json=_envelope(projection)))
    page.route("**/api/v1/business-documents/doc-ui-1/revisions", lambda route: route.fulfill(json=_envelope([previous, revision, projection["current_revision"]])))
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    expect(page.get_by_test_id("business-document-protocol")).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(page.get_by_test_id("business-document-eva-binding")).to_have_count(0)
    expect(page.get_by_test_id("proposal-status-proposal-1")).to_have_text("Принято к применению")
    input_field = page.get_by_role("textbox", name="Комментарий", exact=True)
    input_field.scroll_into_view_if_needed()
    input_field.fill("Замечание сохранится при изменении размеров")
    if width > 1000:
        handle = page.get_by_test_id("business-document-protocol-resizer")
        before = page.get_by_test_id("business-document-protocol").bounding_box()["width"]
        box = handle.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] - 140, box["y"] + box["height"] / 2, steps=10)
        page.mouse.up()
        assert page.get_by_test_id("business-document-protocol").bounding_box()["width"] > before + 100
        box = handle.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(0, box["y"] + box["height"] / 2, steps=10)
        page.mouse.up()
        expect(handle).to_have_attribute("aria-valuenow", str(int(width * 0.8)))
        assert abs(page.get_by_test_id("business-document-protocol").bounding_box()["width"] - width * 0.8) <= 1
        assert page.get_by_test_id("business-document-pane").bounding_box()["width"] > 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.set_viewport_size({"width": 1280, "height": 1000})
        expect(handle).to_have_attribute("aria-valuenow", "1024")
        expect(handle).to_have_attribute("aria-valuemax", "1024")
        page.set_viewport_size({"width": width, "height": 1000})
        expect(handle).to_have_attribute("aria-valuemax", str(int(width * 0.8)))
        handle.dblclick()
        expect(handle).to_have_attribute("aria-valuenow", "460")
        expect(input_field).to_have_value("Замечание сохранится при изменении размеров")
        composer = page.get_by_test_id("business-document-comment-composer")
        old_height = composer.bounding_box()["height"]
        resize = page.get_by_test_id("business-document-composer-resizer").bounding_box()
        page.mouse.move(resize["x"] + 30, resize["y"] + 6)
        page.mouse.down()
        page.mouse.move(resize["x"] + 30, resize["y"] - 100, steps=10)
        page.mouse.up()
        assert composer.bounding_box()["height"] > old_height + 80
    page.get_by_role("tab", name=re.compile("Завершённые обсуждения")).click()
    expect(page.get_by_text("Ответ: 15 минут", exact=True)).to_be_visible()
    expect(page.get_by_text("Не принято", exact=True)).to_be_visible()
    old_proposal = page.locator('[data-protocol-id="old-proposal"]')
    old_proposal.locator("summary").click()
    expect(old_proposal.get_by_test_id("revision-text-diff")).to_contain_text("Запись удерживается 15 минут")
    expect(old_proposal.get_by_test_id("revision-text-diff")).to_contain_text("Запись сразу подтверждается")
    expect(page.get_by_role("button", name="Принять предложение", exact=True)).to_have_count(0)
    expect(input_field).to_have_value("Замечание сохранится при изменении размеров")
    history_path = Path(os.environ.get("PW_ARTIFACTS_DIR", "test/playwright/artifacts")) / f"compact-bt-history-{width}.png"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(history_path), full_page=True)
    page.get_by_role("tab", name=re.compile("В работе")).click()
    expect(page.get_by_test_id("answer-question-question-1")).to_be_visible()
    if width > 1000:
        path = Path(os.environ.get("PW_ARTIFACTS_DIR", "test/playwright/artifacts")) / "compact-bt-desktop.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def _projection(*, allowed_commands=None):
    return {
        "document_id": "doc-ui-1",
        "title": "Переводы одной кнопкой",
        "document_type": "business_requirements",
        "state_version": 18,
        "lifecycle_state": "REVIEW",
        "operation_state": "IDLE",
        "current_revision": {
            "revision_id": "revision-3",
            "revision_number": 3,
            "document_ast": {
                "schema_version": "1",
                "document_type": "business_requirements",
                "template_version": "1.0.0",
                "sections": [
                    {
                        "id": "1",
                        "title": "Цель",
                        "blocks": [
                            {
                                "type": "paragraph",
                                "text": "Сократить время перевода до одной минуты.",
                            }
                        ],
                    }
                ],
            },
            "section_texts": {"1": "Сократить время перевода до одной минуты."},
            "body_markdown": "## 1. Цель\nСократить время перевода до одной минуты.",
            "content_hash": "sha256:ui-test",
        },
        "active_review_cycle": 2,
        "protocol": {
            "questions": [
                {
                    "question_id": "question-1",
                    "sequence_number": 1,
                    "target_section_id": "1",
                    "text": "Как измеряется успех?",
                    "options": [
                        {"option_id": "time", "label": "По времени"},
                        {"option_id": "quality", "label": "По качеству"},
                    ],
                    "allow_custom_answer": True,
                    "status": "OPEN",
                }
            ],
            "proposals": [
                {
                    "proposal_id": "proposal-1",
                    "target_section_id": "1",
                    "text": "Добавить целевую метрику",
                    "rationale": "Требование должно быть измеримым",
                    "decision": "PENDING",
                }
            ],
            "comments": [],
        },
        "allowed_commands": allowed_commands if allowed_commands is not None else ["ANSWER_QUESTION", "DECIDE_PROPOSAL", "ACCEPT_PROPOSAL", "ADD_COMMENT", "APPLY_CHANGES"],
        "latest_exports": [
            {
                "artifact_id": "artifact-md-r3",
                "format": "MARKDOWN",
                "filename": "requirements.md",
                "revision_id": "revision-3",
                "revision_number": 3,
                "created_at": 1,
            }
        ],
    }


def _install_session(page):
    user_info = json.dumps(
        {
            "access_token": "business-documents-ui-token",
            "id": "business-documents-ui-user",
            "email": "business-documents-ui@example.test",
            "nickname": "Business Documents UI",
            "is_superuser": False,
        }
    )
    page.add_init_script(
        f"""
        (() => {{
          const userInfo = {user_info};
          localStorage.setItem('Authorization', 'Bearer business-documents-ui-token');
          localStorage.setItem('token', 'business-documents-ui-token');
          localStorage.setItem('userInfo', JSON.stringify(userInfo));
          localStorage.setItem('lng', 'ru');
        }})()
        """
    )


def _install_common_routes(page):
    page.route(
        "**/api/v1/system/config",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _envelope(
                    {
                        "registerEnabled": 0,
                        "disablePasswordLogin": False,
                        "visibleSections": [
                            "home",
                            "dataset",
                            "chat",
                            "search",
                            "agent",
                            "memory",
                            "catalog",
                            "business_documents",
                            "technical_specifications",
                            "file_manager",
                        ],
                    }
                )
            ),
        ),
    )
    page.route(
        "**/api/v1/users/me",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _envelope(
                    {
                        "id": "business-documents-ui-user",
                        "email": "business-documents-ui@example.test",
                        "nickname": "Business Documents UI",
                        "language": "ru",
                        "avatar": None,
                    }
                )
            ),
        ),
    )
    for pattern, data in (
        ("**/api/v1/tenants", []),
        ("**/api/v1/users/me/eva-credentials", {"items": []}),
        ("**/api/v1/users/me/models", {"tenant_id": "business-documents-ui-tenant", "llm_id": "", "asr_id": "", "embd_id": ""}),
        (
            "**/api/v1/business-documents/eva/access",
            {"state": "NOT_CONFIGURED", "connector_id": None, "expires_at": None, "expiry_checked_at": None},
        ),
        (
            "**/api/v1/business-documents/capabilities",
            {
                "access_role": "AUTHOR_CREATOR",
                "capabilities": {"read": True, "create": True, "edit_own": True, "edit_all": False, "delete": False, "assign": False},
            },
        ),
    ):
        page.route(
            pattern,
            lambda route, _request, response=data: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_envelope(response)),
            ),
        )


@pytest.mark.p1
@pytest.mark.parametrize("width,theme", [(1440, "light"), (390, "light"), (1440, "dark")])
def test_business_document_catalog_availability_and_theme(page, base_url, width, theme):
    page.set_viewport_size({"width": width, "height": 1000 if width > 1000 else 844})
    _install_session(page)
    page.add_init_script(f"localStorage.setItem('ragflow-ui-theme', '{theme}')")
    page.route("**/api/v1/**", lambda route: route.fulfill(json=_envelope({})))
    _install_common_routes(page)
    capabilities = {"read": True, "create": True, "edit_own": True, "edit_all": False, "delete": False, "assign": False}
    page.route("**/api/v1/business-documents?*", lambda route: route.fulfill(json=_envelope({"items": [], "total": 0, "page": 1, "page_size": 20, "capabilities": capabilities})))
    title = "12.06.06.01.01 Ведение и согласование табеля рабочего времени"
    page.route(
        "**/api/v1/business-documents/catalog",
        lambda route: route.fulfill(
            json=_envelope(
                {
                    "items": [
                        {"id": "free", "title": "Новая тема", "existing_documents": []},
                        {"id": "occupied", "title": title, "existing_documents": [{"document_id": "existing-doc", "title": title, "lifecycle_state": "REVIEW"}]},
                    ],
                    "total": 2,
                }
            )
        ),
    )
    page.goto(f"{base_url.rstrip('/')}/business-documents")
    select = page.get_by_test_id("business-document-catalog-select")
    expect(select).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(select.locator('optgroup[label="Без документов"] option')).to_have_count(1)
    expect(select.locator('optgroup[label="Документ уже есть"] option')).to_contain_text("документ уже есть")
    select.select_option("occupied")
    notice = page.get_by_test_id("business-document-existing-topic")
    expect(notice).to_contain_text("По этой теме документ уже есть")
    expect(notice.get_by_role("link")).to_have_attribute("href", "/business-documents/existing-doc")
    expect(page.get_by_role("button", name="Создать ещё один документ", exact=True)).to_be_enabled()
    colors = select.evaluate("""el => {
      const menu = getComputedStyle(el.querySelector('option[value="occupied"]'));
      return {background: menu.backgroundColor, color: menu.color, scheme: getComputedStyle(el).colorScheme};
    }""")

    def luminance(color):
        rgb = [int(value) / 255 for value in re.findall(r"\d+", color)[:3]]
        channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb]
        return sum(value * weight for value, weight in zip(channels, (0.2126, 0.7152, 0.0722)))

    background, foreground = luminance(colors["background"]), luminance(colors["color"])
    assert colors["scheme"] == theme
    assert background > 0.8 if theme == "light" else background < 0.15
    assert (max(background, foreground) + 0.05) / (min(background, foreground) + 0.05) >= 4.5
    link_colors = notice.evaluate("""el => {
      const backgrounds = [];
      for (let node = el; node; node = node.parentElement) backgrounds.push(getComputedStyle(node).backgroundColor);
      return {backgrounds: backgrounds.reverse(), color: getComputedStyle(el.querySelector('a')).color};
    }""")
    effective_background = [1.0, 1.0, 1.0]
    for color in link_colors["backgrounds"]:
        channels = [float(value) for value in re.findall(r"[\d.]+", color)]
        alpha = channels[3] if len(channels) > 3 else 1.0
        effective_background = [channel / 255 * alpha + background * (1 - alpha) for channel, background in zip(channels[:3], effective_background)]
    link_background = luminance("rgb(" + ",".join(str(round(channel * 255)) for channel in effective_background) + ")")
    link_foreground = luminance(link_colors["color"])
    assert (max(link_background, link_foreground) + 0.05) / (min(link_background, link_foreground) + 0.05) >= 4.5
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    notice.scroll_into_view_if_needed()
    artifacts = Path(os.environ["PW_ARTIFACTS_DIR"])
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / f"catalog-{theme}-{width}.png"))
    select.select_option("free")
    expect(notice).to_have_count(0)
    expect(page.get_by_role("button", name="Начать работу", exact=True)).to_be_enabled()


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
def test_business_document_protocol_markdown_and_decisions(page, base_url, width):
    _install_session(page)
    page.route("**/api/v1/**", lambda route: route.fulfill(json=_envelope({})))
    _install_common_routes(page)
    projection = _projection()
    projection["protocol"]["warnings"] = [{"code": "REVIEW_NOTE", "message": "**Замечание проверки**"}]
    question = projection["protocol"]["questions"][0]
    question["text"] = "**Согласовать** *сроки*?\n\n| Этап | Срок |\n| --- | --- |\n| Проверка | 1 день |\n\n<script>alert(1)</script>"
    question["options"][0]["label"] = "**По времени** [условия](https://example.test/conditions)"
    question["options"][1]["label"] = "*По качеству*"
    proposal = projection["protocol"]["proposals"][0]
    proposal["text"] = "**Добавить метрику**\n\n- Измерить время\n- Проверить результат"
    proposal["rationale"] = "*Обоснование* и `идентификатор`"
    projection["protocol"]["comments"] = [{"comment_id": "comment-md", "text": "> **Комментарий автора**", "section_id": "1"}]
    page.route("**/api/v1/business-documents/doc-ui-1", lambda route: route.fulfill(json=_envelope(projection)))
    page.route("**/api/v1/business-documents/doc-ui-1/commands", lambda route: route.fulfill(json=_envelope({"accepted": True, "state_version": 19})))
    page.set_viewport_size({"width": width, "height": 960})
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    card = page.get_by_test_id("business-document-question")
    expect(card.locator("strong").first).to_have_text("Согласовать", timeout=RESULT_TIMEOUT_MS)
    expect(card.locator("em").first).to_have_text("сроки")
    expect(card.get_by_role("table")).to_contain_text("Проверка")
    expect(card.locator("script")).to_have_count(0)
    expect(card.get_by_role("link", name="условия")).to_have_attribute("href", "https://example.test/conditions")
    warning_panel = page.get_by_test_id("business-document-warnings")
    warning_panel.locator("summary").click()
    expect(warning_panel.locator("strong")).to_have_text("Замечание проверки")
    radio = card.get_by_role("radio", name="По времени условия")
    radio.focus()
    radio.press("ArrowDown", delay=100)
    expect(card.get_by_role("radio", name="По качеству")).to_be_checked()
    card.get_by_text("По времени", exact=True).click()
    expect(radio).to_be_checked()
    with page.expect_request("**/api/v1/business-documents/doc-ui-1/commands") as answer_request:
        card.get_by_role("button", name="Ответить", exact=True).click()
    answer = answer_request.value.post_data_json
    assert answer["type"] == "ANSWER_QUESTION"
    assert answer["payload"] == {"question_id": "question-1", "selected_option_id": "time", "custom_answer": None}
    proposal_card = page.get_by_test_id("business-document-proposal")
    expect(proposal_card.locator("strong")).to_have_text("Добавить метрику")
    expect(proposal_card.get_by_role("listitem")).to_have_count(2)
    expect(proposal_card.locator("em")).to_have_text("Обоснование")
    expect(proposal_card.locator("code")).to_have_text("идентификатор")
    expect(page.get_by_test_id("business-document-comment").locator("blockquote strong")).to_have_text("Комментарий автора")
    with page.expect_request("**/api/v1/business-documents/doc-ui-1/commands") as decision_request:
        proposal_card.get_by_role("button", name="Принять предложение", exact=True).click()
    assert decision_request.value.post_data_json["type"] == "ACCEPT_PROPOSAL"
    assert decision_request.value.post_data_json["payload"] == {"proposal_id": "proposal-1"}


@pytest.mark.p1
@pytest.mark.auth
def test_business_documents_list_validation_and_create_navigation(
    page,
    base_url,
):
    _install_session(page)
    _install_common_routes(page)
    created_payloads = []

    def route_business_documents(route):
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith(("/capabilities", "/eva/access")):
            route.fallback()
            return
        if request.method == "GET" and path.endswith("/doc-ui-1"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(_envelope(_projection()), ensure_ascii=False))
            return
        if request.url.split("?", 1)[0].endswith("/catalog"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _envelope(
                        {
                            "items": [
                                {
                                    "id": "01.01.04.01.01",
                                    "title": "Новый регламент",
                                    "title_en": "New regulation",
                                    "description": "Разрешённый L5-документ",
                                    "capability_level": "L5",
                                    "capability_type": "Core",
                                    "hierarchy": {},
                                }
                            ],
                            "total": 1,
                        }
                    ),
                    ensure_ascii=False,
                ),
            )
            return
        if request.method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _envelope(
                        {
                            "items": [
                                {
                                    "document_id": "doc-ui-1",
                                    "title": "Сохранённые требования",
                                    "lifecycle_state": "REVIEW",
                                    "operation_state": "IDLE",
                                    "current_revision_number": 2,
                                    "update_time": 1_756_000_000,
                                }
                            ],
                            "page": 1,
                            "page_size": 20,
                            "total": 1,
                            "scope": "mine",
                            "access_role": "AUTHOR_CREATOR",
                            "capabilities": {
                                "read": True,
                                "create": True,
                                "edit_own": True,
                                "edit_all": False,
                                "delete": False,
                                "assign": False,
                            },
                        }
                    ),
                    ensure_ascii=False,
                ),
            )
            return
        created_payloads.append(request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_envelope(_projection()), ensure_ascii=False),
        )

    page.route("**/api/v1/business-documents**", route_business_documents)
    page.route(
        "**/api/v1/datasets**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_envelope([])),
        ),
    )

    with (
        page.expect_response(lambda response: response.request.method == "GET" and urlparse(response.url).path == "/api/v1/business-documents/capabilities") as capabilities,
        page.expect_response(lambda response: response.request.method == "GET" and urlparse(response.url).path == "/api/v1/tenants") as tenants,
    ):
        _goto_home(page, base_url)
    for response in (capabilities.value, tenants.value):
        assert response.finished() is None
        assert response.status == 200
    expect(page.locator("[data-testid='nav-business-documents']").first).to_have_attribute("href", "/business-documents")
    page.goto(f"{base_url.rstrip('/')}/business-documents")
    expect(page.locator("[data-testid='business-documents-create']")).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(page.locator("[data-testid='business-document-list-item']")).to_contain_text("Сохранённые требования")

    create_panel = page.locator("[data-testid='business-document-create-panel']")
    submit = create_panel.locator("button[type='submit']")
    expect(submit).to_be_disabled()
    page.locator("[data-testid='business-document-catalog-select']").select_option("01.01.04.01.01")
    create_panel.locator("textarea").fill("  Согласовать единый процесс.  ")
    expect(submit).to_be_enabled()
    submit.click()

    expect(page).to_have_url(re.compile(r"/business-documents/doc-ui-1$"))
    expect(page.get_by_role("heading", name="Переводы одной кнопкой", exact=True)).to_be_visible()
    assert created_payloads == [
        {
            "schema_version": "4",
            "document_type": "business_requirements",
            "catalog_entry_id": "01.01.04.01.01",
            "idea": "Согласовать единый процесс.",
            "dataset_ids": [],
        }
    ]


@pytest.mark.p1
@pytest.mark.auth
@pytest.mark.parametrize("operation_state", ["FAILED", "APPLYING_CHANGES"])
def test_business_document_soar_requirement_error_details(page, base_url, operation_state):
    _install_session(page)
    _install_common_routes(page)
    suite = json.loads((Path(__file__).resolve().parents[3] / "agent/business_requirements/golden_model_quality/v2.json").read_text(encoding="utf-8"))
    case = next(case for case in suite["cases"] if case["workflow"] == "metric_placement")
    metrics = [requirement["text"] for requirement in case["seed"]["requirements"] if requirement["section_id"] == "5.4"]
    assert len(metrics) == 8
    error = {
        "code": "REQUIREMENT_COVERAGE_FAILED",
        "message": "Generated changes omit, contradict or misplace confirmed requirements",
        "details": {"requirements": [{"text": text, "status": "MISSING"} for text in metrics]},
    }
    projection = _projection()
    projection.update(
        operation_state=operation_state,
        last_error=error,
        latest_job={
            "job_id": "soar-failed-plan",
            "job_type": "PLAN_CHANGES",
            "status": "DEAD" if operation_state == "FAILED" else "RETRY",
            "attempt": 3,
            "max_attempts": 3,
            "error": error,
        },
    )
    page.route(
        "**/api/v1/business-documents/doc-ui-1",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_envelope(projection), ensure_ascii=False),
        ),
    )
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    panel = page.get_by_test_id("business-document-operation-error" if operation_state == "FAILED" else "business-document-operation")
    expect(panel).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(panel).to_contain_text("План изменений не сохранил подтверждённые требования.")
    expect(panel.get_by_role("listitem")).to_have_count(8)
    for metric in metrics:
        expect(panel).to_contain_text(metric)
    expect(panel).not_to_contain_text(error["message"])
    for width in [1280, 390]:
        page.set_viewport_size({"width": width, "height": 844})
        assert panel.evaluate("element => element.scrollWidth <= element.clientWidth"), "Error details overflow the viewport"
        panel.get_by_role("listitem").last.scroll_into_view_if_needed()
        expect(panel.get_by_role("listitem").last).to_be_in_viewport()
    page.reload()
    expect(panel.get_by_role("listitem")).to_have_count(8)
    if operation_state == "FAILED":
        page.get_by_role("button", name="Закрыть сообщение об ошибке").click()
        expect(panel).to_have_count(0)


@pytest.mark.p1
@pytest.mark.auth
def test_business_document_workbench_commands_and_mobile_layout(
    page,
    base_url,
):
    _install_session(page)
    _install_common_routes(page)
    commands = []

    def route_document(route):
        request = route.request
        if request.method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_envelope(_projection()), ensure_ascii=False),
            )
            return
        commands.append(request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _envelope(
                    {
                        "accepted": True,
                        "document_id": "doc-ui-1",
                        "state_version": 19,
                        "lifecycle_state": "REVIEW",
                        "operation_state": "IDLE",
                    }
                )
            ),
        )

    page.route("**/api/v1/business-documents/doc-ui-1", route_document)
    page.route("**/api/v1/business-documents/doc-ui-1/commands", route_document)
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")

    workbench = page.locator("[data-testid='business-document-workbench']")
    expect(workbench).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(page.get_by_role("heading", name="Переводы одной кнопкой")).to_be_visible()
    expect(page.locator("[data-testid='business-document-pane']")).to_contain_text("Сократить время перевода")
    expect(page.locator("[data-testid='business-document-protocol']")).to_contain_text("Как измеряется успех?")
    expect(page.get_by_role("link", name="Markdown r3")).to_have_attribute(
        "href",
        "/api/v1/business-documents/doc-ui-1/exports/artifact-md-r3/download",
    )

    page.get_by_label("По времени").check()
    with page.expect_request("**/api/v1/business-documents/doc-ui-1/commands") as request_info:
        page.locator("[data-testid='answer-question-question-1']").click()
    submitted_command = request_info.value.post_data_json
    assert submitted_command["type"] == "ANSWER_QUESTION"
    assert submitted_command["expected_state_version"] == 18
    assert submitted_command["payload"] == {
        "question_id": "question-1",
        "selected_option_id": "time",
        "custom_answer": None,
    }
    assert commands == [submitted_command]

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator("[data-testid='business-document-header']")).to_be_visible()
    expect(page.locator("[data-testid='business-document-actions']")).to_be_visible()
    expect(page.locator("[data-testid='apply-changes-button']")).to_be_visible()


@pytest.mark.p1
@pytest.mark.parametrize("width,height", [(1000, 800), (390, 844)])
def test_narrow_workbench_protocol_controls_remain_reachable(page, base_url, width, height):
    page.set_viewport_size({"width": width, "height": height})
    _install_session(page)
    _install_common_routes(page)
    page.route("**/api/v1/users/me/models", lambda route: route.fulfill(json=_envelope({"tenant_id": "narrow-workbench-tenant"})))
    page.route("**/api/v1/business-documents/doc-ui-1", lambda route: route.fulfill(json=_envelope(_projection())))
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    panes = page.get_by_test_id("business-document-panes")
    expect(panes).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    answer = page.get_by_test_id("answer-question-question-1")
    expect(page.get_by_test_id("toggle-question-question-1")).to_have_attribute("aria-expanded", "true")
    content = page.locator('[data-protocol-id="question-1"] [data-state="open"][id]')
    page.wait_for_function(
        """content => {
          const bounds = content.getBoundingClientRect();
          const animating = content.getAnimations().some(animation =>
            animation.animationName?.startsWith('accordion-') &&
            Number.isFinite(animation.effect?.getComputedTiming().endTime) &&
            (animation.pending || animation.playState === 'running')
          );
          return bounds.height > 0 && content.scrollHeight <= Math.ceil(bounds.height) && !animating;
        }""",
        arg=content.element_handle(),
    )
    answer.scroll_into_view_if_needed()
    expect(answer).to_be_in_viewport()
    page.get_by_label("По времени").check()
    expect(answer).to_be_enabled()
    proposal = page.get_by_text("Добавить целевую метрику", exact=True)
    proposal.scroll_into_view_if_needed()
    expect(proposal).to_be_in_viewport()
    comment = page.get_by_role("textbox", name="Комментарий", exact=True)
    comment.scroll_into_view_if_needed()
    expect(comment).to_be_in_viewport()
    comment.fill("Проверка доступности обсуждения")
    expect(page.get_by_role("button", name="Добавить комментарий", exact=True)).to_be_enabled()
    assert panes.evaluate("el => el.scrollTop > 0"), "Narrow panes must actually scroll to the protocol"


_PARAGRAPH_BLOCKS = [
    {"type": "paragraph", "text": "Раздел описывает клиентские сценарии 😀."},
    {"type": "paragraph", "text": "Просмотр и поиск **статьи**."},
    {"type": "paragraph", "text": "Создание и публикация статьи."},
]


@pytest.mark.p1
@pytest.mark.parametrize(
    "section_id,source,selected,canonical_selected",
    [
        ("4", "Обычный текст перед схемой.\n\n```plantuml\n@startuml\nA -> B\n@enduml\n```\n\nТекст после схемы.", "Текст после схемы", "Текст после схемы"),
        ("4.1", "| Правило | Значение |\n| --- | --- |\n| Группа | A\\|B |\n\nСледующее правило.", "A|B", "A\\|B"),
        ("4.3", "Условие: имя\\_поля &amp; значение.\n\nКонец раздела.", "имя_поля & значение", "имя\\_поля &amp; значение"),
        ("4.3", "[Ссылка](https://example.test/повтор) и повтор.", "повтор", "повтор"),
        ("4.1", "Первый **важный** пункт.\n\nВторой пункт.", "Первый важный пункт", "Первый **важный** пункт"),
        ("4.3", "Метка 😀: A&nbsp;B &#x1F600;.", "A\u00a0B 😀", "A&nbsp;B &#x1F600;"),
        ("4.1", "Код: `имя\\_поля &amp; значение`.", "имя\\_поля &amp; значение", "имя\\_поля &amp; значение"),
        ("4.3", _PARAGRAPH_BLOCKS, "Раздел описывает клиентские сценарии 😀", "Раздел описывает клиентские сценарии 😀"),
        ("4.3", _PARAGRAPH_BLOCKS, "поиск статьи.", "поиск **статьи**."),
        ("4.3", _PARAGRAPH_BLOCKS, "статьи.\nСоздание", "статьи**.\n\nСоздание"),
    ],
    ids=[
        "diagram-prose",
        "escaped-table-cell",
        "escape-and-entity",
        "link-destination",
        "inline-formatting",
        "unicode-entities",
        "literal-code",
        "paragraph-first-block",
        "paragraph-formatted-block",
        "paragraph-cross-block",
    ],
)
def test_business_document_markdown_selection_anchor(page, base_url, section_id, source, selected, canonical_selected):
    _install_session(page)
    page.route("**/api/v1/**", lambda route: route.fulfill(json=_envelope({})))
    _install_common_routes(page)
    page.route("**/api/v1/users/me/models", lambda route: route.fulfill(json=_envelope({"tenant_id": "selection-tenant"})))
    projection = _projection()
    revision = projection["current_revision"]
    blocks = source if isinstance(source, list) else [{"type": "paragraph", "text": source}]
    section_ast = {"id": section_id, "title": "Логика", "blocks": blocks}
    source = render_section_text(section_ast)
    if len(blocks) > 1:
        assert source == "\n\n".join(block["text"] for block in blocks)
    revision["document_ast"]["sections"] = [section_ast]
    revision["section_texts"] = {section_id: source}
    revision["body_markdown"] = f"## {section_id}. Логика\n{source}"
    page.route("**/api/v1/business-documents/doc-ui-1", lambda route: route.fulfill(json=_envelope(projection)))
    page.route("**/api/v1/business-documents/doc-ui-1/commands", lambda route: route.fulfill(json=_envelope({"accepted": True, "state_version": 19})))
    page.route("**/plantuml/svg/", lambda route: route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg"><text>Метка визуализатора</text></svg>'))
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    section = page.locator(f'[data-section-id="{section_id}"]')
    expect(section).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    if "plantuml" in source:
        expect(section.locator('[data-diagram-kind="plantuml"] svg')).to_be_visible()
    if len(blocks) > 1:
        line_tops = section.locator("[data-section-body]").evaluate(
            """(body, prefixes) => prefixes.map(prefix => {
                const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const offset = node.textContent.indexOf(prefix);
                    if (offset < 0) continue;
                    const range = document.createRange();
                    range.setStart(node, offset);
                    range.setEnd(node, offset + 1);
                    return range.getBoundingClientRect().top;
                }
                throw new Error('Missing paragraph: ' + prefix);
            })""",
            [block["text"][:8] for block in blocks],
        )
        assert line_tops[0] < line_tops[1] < line_tops[2], "Paragraph blocks must remain visually separate"
    section.locator("[data-section-body]").evaluate(
        """(body, selected) => {
            const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
            const nodes = [];
            let text = '', node;
            while ((node = walker.nextNode())) {
                nodes.push({node, start: text.length});
                text += node.textContent;
            }
            const start = text.indexOf(selected);
            if (start < 0) throw new Error('Missing rendered selection: ' + selected);
            const end = start + selected.length;
            const first = nodes.find(({node, start: offset}) => offset <= start && offset + node.length > start);
            const last = nodes.find(({node, start: offset}) => offset < end && offset + node.length >= end);
            const range = document.createRange();
            range.setStart(first.node, start - first.start);
            range.setEnd(last.node, end - last.start);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            body.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
        }""",
        selected,
    )
    expect(page.get_by_test_id("business-document-selection-error")).to_have_count(0)
    expect(page.get_by_test_id("business-document-comment-scope-selector")).to_contain_text(f"выделенный фрагмент · § {section_id}")
    page.get_by_role("textbox", name="Комментарий", exact=True).fill("Уточнить правило")
    with page.expect_request("**/api/v1/business-documents/doc-ui-1/commands") as submitted:
        page.get_by_role("button", name="Добавить комментарий", exact=True).click()
    anchor = submitted.value.post_data_json["payload"]["anchor"]
    start = source.rindex(canonical_selected)
    end = start + len(canonical_selected)
    assert anchor == {
        "revision_id": "revision-3",
        "section_id": section_id,
        "selected_text": canonical_selected,
        "start_offset": len(source[:start].encode("utf-16-le")) // 2,
        "end_offset": len(source[:end].encode("utf-16-le")) // 2,
        "prefix": source[max(0, start - 64) : start],
        "suffix": source[end : end + 64],
    }
    validate_comment_anchor(anchor, "revision-3", section_id, source)


@pytest.mark.p1
@pytest.mark.parametrize("width", [1280, 768])
def test_paragraph_after_list_resets_indent_and_keeps_block_spacing(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 1000})
    _install_session(page)
    page.route("**/api/v1/**", lambda route: route.fulfill(json=_envelope({})))
    _install_common_routes(page)
    projection = _projection()
    section = {
        "id": "4.3",
        "title": "Клиентские сценарии",
        "blocks": [
            {"type": "paragraph", "text": "Просмотр статьи."},
            {"type": "list", "items": ["Поиск статьи.", "Проверка доступа."]},
            {"type": "paragraph", "text": "Создание и публикация статьи."},
            {"type": "list", "items": ["Сохранение черновика.", "Публикация."]},
            {"type": "paragraph", "text": "Конец сценариев."},
        ],
    }
    revision = projection["current_revision"]
    revision["document_ast"]["sections"] = [section]
    revision["section_texts"] = {"4.3": render_section_text(section)}
    page.route("**/api/v1/business-documents/doc-ui-1", lambda route: route.fulfill(json=_envelope(projection)))
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    body = page.locator('[data-section-id="4.3"] [data-section-body]')
    expect(body).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(body.locator(":scope > p")).to_have_count(3)
    expect(body.locator(":scope > ul")).to_have_count(2)
    expect(body.locator("li")).to_have_count(4)
    geometry = body.evaluate(
        """body => [...body.children].map(element => {
            const bounds = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return {left: bounds.left, top: bounds.top, bottom: bounds.bottom,
                    indent: style.textIndent, padding: style.paddingInlineStart};
        })"""
    )
    assert geometry[0]["left"] == geometry[2]["left"] == geometry[4]["left"]
    assert all(geometry[index]["indent"] == "0px" and geometry[index]["padding"] == "0px" for index in (0, 2, 4))
    gaps = [following["top"] - current["bottom"] for current, following in zip(geometry, geometry[1:])]
    assert all(abs(gap - gaps[0]) < 1 for gap in gaps) and gaps[0] > 0, gaps


@pytest.mark.p1
def test_business_document_workbench_has_no_wcag_aa_violations(page, base_url):
    _install_session(page)
    _install_common_routes(page)
    page.route(
        "**/api/v1/business-documents/doc-ui-1",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(_envelope(_projection()), ensure_ascii=False)),
    )
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    expect(page.locator("[data-testid='business-document-workbench']")).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    assert AXE_SCRIPT.is_file(), "Install frontend dependencies before accessibility checks"
    page.add_script_tag(path=str(AXE_SCRIPT))
    violations = page.evaluate(
        """async () => {
          const root = document.querySelector('[data-testid="business-document-workbench"]');
          const result = await axe.run(root, {
            runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] }
          });
          return result.violations.map(item => ({
            id: item.id,
            impact: item.impact,
            nodes: item.nodes.map(node => ({ target: node.target, html: node.html, failure: node.failureSummary }))
          }));
        }"""
    )
    assert not violations, violations


def test_streamed_document_section_stays_preliminary_after_reload(page, base_url):
    _install_session(page)
    _install_common_routes(page)
    projection = {
        **_projection(allowed_commands=[]),
        "operation_state": "APPLYING_CHANGES",
        "latest_job": {"job_id": "job-stream", "job_type": "PLAN_CHANGES", "status": "RUNNING", "attempt": 1, "max_attempts": 3},
    }
    item = {
        "id": 1,
        "job_id": "job-stream",
        "attempt": 1,
        "base_revision_id": "revision-3",
        "type": "section_preview",
        "payload": {"section_id": "1", "title": "Цель", "before": "Сократить время перевода до одной минуты.", "after": "Новая измеримая цель.", "source_event_ids": ["event-1"]},
    }
    stream_body = f"id: 1\nevent: section_preview\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
    page.route(
        "**/api/v1/business-documents/doc-ui-1/jobs/job-stream/events/stream?after=*",
        lambda route: route.fulfill(status=200, content_type="text/event-stream", body=stream_body),
    )
    page.route(
        "**/api/v1/business-documents/doc-ui-1/jobs/job-stream/events?after=*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(_envelope({"events": [], "status": "COMPLETED", "job_id": "job-stream"}))),
    )
    page.route(
        "**/api/v1/business-documents/doc-ui-1",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(_envelope(projection), ensure_ascii=False)),
    )

    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    preview = page.get_by_test_id("business-document-change-preview")
    expect(preview).to_contain_text("Предварительные изменения", timeout=RESULT_TIMEOUT_MS)
    section = page.locator('[data-section-id="1"]')
    body = section.locator("[data-section-body]")
    expect(body).to_contain_text("Сократить время перевода до одной минуты.")
    expect(body).not_to_contain_text("Новая измеримая цель.")
    expect(preview.get_by_role("button", name="Подтвердить применение")).to_have_count(0)
    expect(section.get_by_text("Основания изменений", exact=False)).to_be_visible()
    difference = section.get_by_test_id("revision-text-diff")
    expect(difference.locator("pre").nth(0)).to_have_text("Сократить время перевода до одной минуты.")
    expect(difference.locator("pre").nth(1)).to_have_text("Новая измеримая цель.")
    expect(section.locator("details")).not_to_have_attribute("open", "")
    section.locator("summary").click()
    expect(section.locator("details")).to_have_attribute("open", "")

    page.reload()
    expect(body).to_contain_text("Сократить время перевода до одной минуты.", timeout=RESULT_TIMEOUT_MS)
    expect(preview).to_contain_text("Предварительные изменения")
    expect(section).to_have_attribute("data-section-preview", "false")
    expect(difference.locator("pre").nth(0)).to_have_text("Сократить время перевода до одной минуты.")
    expect(difference.locator("pre").nth(1)).to_have_text("Новая измеримая цель.")
    expect(section.locator("details")).not_to_have_attribute("open", "")


@pytest.mark.p1
def test_document_changes_are_saved_and_visible_without_confirmation(page, base_url):
    _install_session(page)
    _install_common_routes(page)
    projection = _projection(allowed_commands=["APPLY_CHANGES", "PREPARE_CHANGES"])
    submitted = []
    job_id = "job-apply"
    completed = False
    streams = []

    def finish_job():
        nonlocal projection, completed
        completed = True
        revision = projection["current_revision"]
        projection = {
            **projection,
            "state_version": 21,
            "lifecycle_state": "AGREED",
            "operation_state": "IDLE",
            "latest_job": {"job_id": job_id, "job_type": "PLAN_CHANGES", "status": "COMPLETED", "attempt": 1, "max_attempts": 3},
            "change_preview": None,
            "allowed_commands": [],
            "current_revision": {
                **revision,
                "revision_id": "revision-4",
                "revision_number": 4,
                "document_ast": {**revision["document_ast"], "sections": [{**revision["document_ast"]["sections"][0], "blocks": [{"type": "paragraph", "text": "Новая измеримая цель."}]}]},
                "section_texts": {"1": "Новая измеримая цель."},
                "body_markdown": "## 1. Цель\nНовая измеримая цель.",
            },
        }

    def job_event():
        return {
            "id": 2 if completed else 1,
            "job_id": job_id,
            "attempt": 1,
            "base_revision_id": "revision-3",
            "type": "applied" if completed else "section_preview",
            "payload": {"revision_id": "revision-4"}
            if completed
            else {
                "section_id": "1",
                "title": "Цель",
                "before": "Сократить время перевода до одной минуты.",
                "after": "Новая измеримая цель.",
                "source_event_ids": ["event-1"],
            },
        }

    def stream_job(route):
        streams.append(route.request.url)
        event = job_event()
        route.fulfill(status=200, content_type="text/event-stream", body=f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n")

    def route_document(route):
        nonlocal projection
        request = route.request
        if request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(_envelope(projection), ensure_ascii=False))
            return
        command = request.post_data_json
        submitted.append(command)
        assert command["type"] == "APPLY_CHANGES"
        projection = {
            **projection,
            "state_version": 19,
            "operation_state": "APPLYING_CHANGES",
            "latest_job": {"job_id": job_id, "job_type": "PLAN_CHANGES", "status": "RUNNING", "attempt": 1, "max_attempts": 3},
            "allowed_commands": [],
        }
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _envelope({"accepted": True, "document_id": "doc-ui-1", "state_version": 19, "operation_state": "APPLYING_CHANGES", "job_id": job_id, "allowed_commands": []}), ensure_ascii=False
            ),
        )

    page.route(f"**/api/v1/business-documents/doc-ui-1/jobs/{job_id}/events/stream?after=*", stream_job)
    page.route(
        f"**/api/v1/business-documents/doc-ui-1/jobs/{job_id}/events?after=*",
        lambda route: route.fulfill(json=_envelope({"events": [job_event()], "status": "COMPLETED" if completed else "RUNNING", "job_id": job_id})),
    )
    page.route("**/api/v1/business-documents/doc-ui-1", route_document)
    page.route("**/api/v1/business-documents/doc-ui-1/commands", route_document)
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    page.get_by_role("button", name="Применить исправления").click()
    section = page.locator('[data-section-id="1"]')
    expect(page.get_by_test_id("business-document-change-preview")).to_contain_text("Предварительные изменения", timeout=RESULT_TIMEOUT_MS)
    expect(section.locator("[data-section-body]")).to_contain_text("Сократить время перевода до одной минуты.")
    expect(section).to_have_attribute("data-section-preview", "false")
    assert projection["current_revision"]["revision_id"] == "revision-3"
    page.reload()
    expect(section).to_have_attribute("data-section-preview", "false", timeout=RESULT_TIMEOUT_MS)
    expect(section.locator("[data-section-body]")).to_contain_text("Сократить время перевода до одной минуты.")
    assert projection["current_revision"]["revision_id"] == "revision-3"
    assert len(submitted) == 1 and len(streams) >= 2

    finish_job()
    expect(section).to_have_attribute("data-section-preview", "false")
    expect(section.locator("[data-section-body]")).to_contain_text("Новая измеримая цель.")
    expect(page.get_by_role("button", name="Подтвердить применение")).to_have_count(0)
    expect(page.get_by_test_id("business-document-change-preview")).to_have_count(0)
    assert len(submitted) == 1
    assert submitted[0]["type"] == "APPLY_CHANGES"
    assert submitted[0]["payload"] == {"base_revision_id": "revision-3"}
    page.reload()
    expect(section.locator("[data-section-body]")).to_contain_text("Новая измеримая цель.", timeout=RESULT_TIMEOUT_MS)


@pytest.mark.p1
@pytest.mark.parametrize("decision", ["confirm", "discard"])
def test_existing_prepared_changes_keep_confirm_and_discard_contracts(page, base_url, decision):
    _install_session(page)
    _install_common_routes(page)
    projection = {
        **_projection(allowed_commands=["CONFIRM_PREPARED_CHANGES", "DISCARD_PREPARED_CHANGES"]),
        "change_preview": {"job_id": "job-prepared", "base_revision_id": "revision-3"},
    }
    detail_reads, commands = [], []

    def detail(route):
        detail_reads.append(route.request.url)
        route.fulfill(json=_envelope(projection))

    def decide(route):
        command = route.request.post_data_json
        commands.append(command)
        expected_type = "CONFIRM_PREPARED_CHANGES" if decision == "confirm" else "DISCARD_PREPARED_CHANGES"
        assert command["type"] == expected_type
        assert command["payload"] == {"job_id": "job-prepared"}
        projection.update(change_preview=None, state_version=19, allowed_commands=[])
        if decision == "confirm":
            revision = projection["current_revision"]
            projection["current_revision"] = {
                **revision,
                "revision_id": "revision-4",
                "revision_number": 4,
                "document_ast": {**revision["document_ast"], "sections": [{"id": "1", "title": "Цель", "blocks": [{"type": "paragraph", "text": "Новая измеримая цель."}]}]},
                "section_texts": {"1": "Новая измеримая цель."},
                "body_markdown": "## 1. Цель\nНовая измеримая цель.",
            }
        route.fulfill(json=_envelope({"accepted": True, "document_id": "doc-ui-1", "state_version": 19, "document": projection}))

    page.route("**/api/v1/business-documents/doc-ui-1", detail)
    page.route("**/api/v1/business-documents/doc-ui-1/commands", decide)
    page.route(
        "**/api/v1/business-documents/doc-ui-1/change-previews/job-prepared",
        lambda route: route.fulfill(
            json=_envelope(
                {
                    "job_id": "job-prepared",
                    "base_revision_id": "revision-3",
                    "state_version": 18,
                    "sections": [{"section_id": "1", "title": "Цель", "before": "Сократить время перевода до одной минуты.", "after": "Новая измеримая цель.", "source_event_ids": []}],
                    "acknowledged_no_change_event_ids": [],
                }
            )
        ),
    )
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    section = page.locator('[data-section-id="1"]')
    expect(section).to_have_attribute("data-section-preview", "true", timeout=RESULT_TIMEOUT_MS)
    expect(section.locator("[data-section-body]")).to_contain_text("Новая измеримая цель.")
    reads_before_command = len(detail_reads)
    page.get_by_role("button", name="Применить исправления" if decision == "confirm" else "Отказаться", exact=True).click()
    expect(section).to_have_attribute("data-section-preview", "false")
    expected_text = "Новая измеримая цель." if decision == "confirm" else "Сократить время перевода до одной минуты."
    expect(section.locator("[data-section-body]")).to_contain_text(expected_text)
    assert len(commands) == 1 and len(detail_reads) == reads_before_command
    page.reload()
    expect(section.locator("[data-section-body]")).to_contain_text(expected_text, timeout=RESULT_TIMEOUT_MS)
    expect(section).to_have_attribute("data-section-preview", "false")


@pytest.mark.p1
def test_revision_history_restore(page, base_url):
    """Browse, inspect a word diff, cancel, restore and reload a historical snapshot."""
    from copy import deepcopy

    _install_session(page)
    _install_common_routes(page)
    page.set_viewport_size({"width": 1440, "height": 1000})
    projection = _projection(allowed_commands=["RESTORE_REVISION"])
    current = projection["current_revision"]
    previous = deepcopy(current)
    previous.update(revision_id="revision-2", revision_number=2, section_texts={"1": "Сократить время перевода до пяти минут."})
    previous["document_ast"]["sections"][0]["blocks"][0]["text"] = previous["section_texts"]["1"]
    previous["body_markdown"] = "## 1. Цель\n" + previous["section_texts"]["1"]
    revisions = [previous, current]
    submitted, detail_reads = [], []

    def detail(route):
        detail_reads.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(_envelope(projection), ensure_ascii=False))

    def restore(route):
        command = route.request.post_data_json
        submitted.append(command)
        assert command["type"] == "RESTORE_REVISION"
        assert command["payload"] == {"revision_id": "revision-2"}
        assert command["expected_state_version"] == projection["state_version"]
        restored = {**previous, "revision_id": "revision-4", "revision_number": 4}
        projection.update(current_revision=restored, state_version=projection["state_version"] + 1, active_review_cycle=3)
        revisions.append(restored)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_envelope({"accepted": True, "document_id": projection["document_id"], "state_version": projection["state_version"], "document": projection}), ensure_ascii=False),
        )

    page.route("**/api/v1/business-documents/doc-ui-1", detail)
    page.route("**/api/v1/business-documents/doc-ui-1/revisions", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(_envelope(revisions), ensure_ascii=False)))
    page.route("**/api/v1/business-documents/doc-ui-1/commands", restore)
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    page.get_by_test_id("business-document-history-toggle").click()
    selector = page.get_by_label("Редакция для просмотра")
    expect(selector).to_have_value("revision-3")
    section = page.locator('[data-section-id="1"]')
    expect(section.get_by_test_id("revision-text-diff")).to_have_count(1)
    expect(section.locator("del")).to_have_text(["пяти", "минут"])
    expect(section.locator("ins")).to_have_text(["одной", "минуты"])
    evidence = Path(os.environ.get("PW_ARTIFACTS_DIR", "output/playwright/revision-history"))
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / "revision-history.png"), full_page=True)
    selector.select_option("revision-2")
    expect(section.locator("[data-section-body]")).to_contain_text(previous["section_texts"]["1"])
    page.get_by_role("button", name="Восстановить эту редакцию").click()
    page.get_by_role("button", name="Отмена", exact=True).click()
    assert submitted == []
    page.get_by_role("button", name="Восстановить эту редакцию").click()
    page.get_by_role("button", name="Восстановить документ", exact=True).click()
    expect(page.get_by_test_id("business-document-history")).to_have_count(0)
    expect(page.get_by_text("Редакция 4", exact=True)).to_be_visible()
    expect(section.locator("[data-section-body]")).to_contain_text(previous["section_texts"]["1"])
    assert len(submitted) == 1
    assert len(detail_reads) == 1
    page.reload()
    expect(page.get_by_text("Редакция 4", exact=True)).to_be_visible()
    expect(section.locator("[data-section-body]")).to_contain_text(previous["section_texts"]["1"])
