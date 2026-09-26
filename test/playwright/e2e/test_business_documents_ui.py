import json
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect

from test.playwright.helpers._next_apps_helpers import RESULT_TIMEOUT_MS, _goto_home


AXE_SCRIPT = Path(__file__).resolve().parents[3] / "web" / "node_modules" / "axe-core" / "axe.min.js"


def _envelope(data):
    return {"code": 0, "data": data}


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
        "allowed_commands": allowed_commands or ["ANSWER_QUESTION", "DECIDE_PROPOSAL", "ADD_COMMENT", "APPLY_CHANGES"],
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
    ):
        page.route(
            pattern,
            lambda route, response=data: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_envelope(response)),
            ),
        )


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
        if request.url.split("?", 1)[0].endswith("/catalog"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _envelope(
                        {
                            "items": [
                                {
                                    "id": "L2-01.01.04.01.01",
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

    _goto_home(page, base_url)
    expect(page.locator("[data-testid='nav-business-documents']").first).to_have_attribute("href", "/business-documents")
    page.goto(f"{base_url.rstrip('/')}/business-documents")
    expect(page.locator("[data-testid='business-documents-create']")).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(page.locator("[data-testid='business-document-list-item']")).to_contain_text("Сохранённые требования")

    create_panel = page.locator("[data-testid='business-document-create-panel']")
    submit = create_panel.locator("button[type='submit']")
    expect(submit).to_be_disabled()
    page.locator("[data-testid='business-document-catalog-select']").select_option("L2-01.01.04.01.01")
    create_panel.locator("textarea").fill("  Согласовать единый процесс.  ")
    expect(submit).to_be_enabled()
    submit.click()

    expect(page).to_have_url(re.compile(r"/business-documents/doc-ui-1$"))
    assert created_payloads == [
        {
            "schema_version": "3",
            "document_type": "business_requirements",
            "catalog_entry_id": "L2-01.01.04.01.01",
            "idea": "Согласовать единый процесс.",
            "dataset_ids": [],
        }
    ]


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
    expect(preview).to_contain_text("Новая измеримая цель.")
    expect(preview.get_by_role("button", name="Подтвердить применение")).to_have_count(0)
    expect(page.get_by_test_id("business-document-pane")).to_contain_text("Сократить время перевода до одной минуты.")

    page.reload()
    expect(preview).to_contain_text("Новая измеримая цель.", timeout=RESULT_TIMEOUT_MS)


@pytest.mark.p1
def test_prepared_document_changes_are_visible_before_confirmation(page, base_url):
    _install_session(page)
    _install_common_routes(page)
    projection = _projection(allowed_commands=["PREPARE_CHANGES"])
    submitted = []

    def route_document(route):
        nonlocal projection
        request = route.request
        if request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(_envelope(projection), ensure_ascii=False))
            return
        command = request.post_data_json
        submitted.append(command)
        if command["type"] == "PREPARE_CHANGES":
            projection = {
                **projection,
                "state_version": 20,
                "change_preview": {"job_id": "job-preview", "base_revision_id": "revision-3"},
                "allowed_commands": ["CONFIRM_PREPARED_CHANGES", "DISCARD_PREPARED_CHANGES"],
            }
        elif command["type"] == "CONFIRM_PREPARED_CHANGES":
            revision = projection["current_revision"]
            projection = {
                **projection,
                "state_version": 21,
                "lifecycle_state": "AGREED",
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
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(_envelope({"accepted": True, "document_id": "doc-ui-1", "state_version": projection["state_version"]}), ensure_ascii=False)
        )

    def route_preview(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _envelope(
                    {
                        "job_id": "job-preview",
                        "base_revision_id": "revision-3",
                        "state_version": 20,
                        "sections": [{"section_id": "1", "title": "Цель", "before": "Сократить время перевода до одной минуты.", "after": "Новая измеримая цель.", "source_event_ids": ["event-1"]}],
                        "acknowledged_no_change_event_ids": [],
                    }
                ),
                ensure_ascii=False,
            ),
        )

    page.route("**/api/v1/business-documents/doc-ui-1/change-previews/job-preview", route_preview)
    page.route("**/api/v1/business-documents/doc-ui-1", route_document)
    page.route("**/api/v1/business-documents/doc-ui-1/commands", route_document)
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    page.get_by_role("button", name="Подготовить исправления").click()

    preview = page.get_by_test_id("business-document-change-preview")
    expect(preview).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(preview).to_contain_text("ещё не применено")
    expect(preview).to_contain_text("Новая измеримая цель.")
    expect(page.get_by_test_id("business-document-pane")).to_contain_text("Сократить время перевода до одной минуты.")
    assert submitted[0]["type"] == "PREPARE_CHANGES"
    assert submitted[0]["payload"] == {"base_revision_id": "revision-3"}

    page.reload()
    expect(preview).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    expect(page.get_by_test_id("business-document-pane")).to_contain_text("Сократить время перевода до одной минуты.")
    preview.get_by_role("button", name="Подтвердить применение").click()
    expect(page.get_by_test_id("business-document-pane")).to_contain_text("Новая измеримая цель.", timeout=RESULT_TIMEOUT_MS)
    assert submitted[1]["type"] == "CONFIRM_PREPARED_CHANGES"
    assert submitted[1]["payload"] == {"job_id": "job-preview"}
