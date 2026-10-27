"""Background review journeys on the built SPA with controlled API responses."""

from copy import deepcopy
import os
import json
from pathlib import Path
import time

import pytest
from playwright.sync_api import expect

from business_documents.domain.content import validate_contract
from test.playwright.e2e.test_business_documents_ui import _projection, _install_session, _install_common_routes, _envelope
from test.playwright.helpers._next_apps_helpers import RESULT_TIMEOUT_MS


def setup(page, base_url):
    _install_session(page)
    page.route("**/api/v1/**", lambda route: route.fulfill(json=_envelope({})))
    _install_common_routes(page)
    projection = _projection(allowed_commands=["ANSWER_QUESTION", "ACCEPT_PROPOSAL", "DECIDE_PROPOSAL", "CANCEL_PROPOSAL_ACCEPTANCE", "ADD_COMMENT", "REQUEST_REVIEW_ASSESSMENT"])
    projection["protocol"]["questions"] = []
    projection["protocol"]["proposals"].append({**projection["protocol"]["proposals"][0], "proposal_id": "proposal-2", "text": "Уточнить способ измерения"})
    projection["background_work"] = []
    requests = []
    schema = json.loads((Path(__file__).resolve().parents[3] / "agent/business_requirements/contracts/command.v1.schema.json").read_text(encoding="utf-8"))

    def command(route):
        request = route.request.post_data_json
        validate_contract("command", request, schema)
        requests.append(request)
        projection["state_version"] += 1
        if request["type"] == "ACCEPT_PROPOSAL":
            proposal = next(item for item in projection["protocol"]["proposals"] if item["proposal_id"] == request["payload"]["proposal_id"])
            status = "QUEUED" if any(item["status"] == "RUNNING" for item in projection["background_work"]) else "RUNNING"
            proposal.update(decision="ACCEPTED", applied=False, application={"status": "QUEUED" if status == "QUEUED" else "RUNNING"})
            projection["background_work"] = [item for item in projection["background_work"] if item.get("proposal_id") != proposal["proposal_id"]]
            projection["background_work"].append({"request_id": proposal["proposal_id"], "kind": "PLAN_CHANGES", "status": status, "proposal_id": proposal["proposal_id"], "job_id": "job-1"})
            projection["operation_state"] = "APPLYING_CHANGES"
            projection["latest_job"] = {
                "job_id": "job-1",
                "job_type": "PLAN_CHANGES",
                "status": "RUNNING",
                "attempt": 1,
                "max_attempts": 3,
                "progress_stage": "GENERATING",
                "create_time": int(time.time() * 1000),
            }
        elif request["type"] == "ADD_COMMENT":
            projection["protocol"]["comments"].append(
                {"comment_id": "late-comment", **request["payload"], "anchor_status": "ORPHANED" if request["payload"]["revision_id"] != projection["current_revision"]["revision_id"] else "ANCHORED"}
            )
        elif request["type"] == "REQUEST_REVIEW_ASSESSMENT":
            projection.update(operation_state="ANALYZING_REVIEW", background_work=[{"request_id": "analysis", "kind": "ASSESS_REVIEW", "status": "RUNNING", "job_id": "analysis-job"}])
            projection["latest_job"] = {
                "job_id": "analysis-job",
                "job_type": "ASSESS_REVIEW",
                "status": "RUNNING",
                "attempt": 1,
                "max_attempts": 3,
                "progress_stage": "VALIDATING",
                "create_time": int(time.time() * 1000),
            }
        route.fulfill(json=_envelope({"accepted": True, "document_id": projection["document_id"], "state_version": projection["state_version"], "document": projection}))

    page.route("**/api/v1/business-documents/doc-ui-1", lambda route: route.fulfill(json=_envelope(projection)))
    page.route("**/api/v1/business-documents/doc-ui-1/commands", command)
    page.route("**/api/v1/business-documents/doc-ui-1/jobs/*/events*", lambda route: route.fulfill(json=_envelope({"events": [], "status": "RUNNING"})))
    page.goto(f"{base_url.rstrip('/')}/business-documents/doc-ui-1")
    expect(page.get_by_test_id("business-document-protocol")).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    return projection, requests


def screenshot(page, name):
    path = Path(os.environ["PW_ARTIFACTS_DIR"]) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=True)


def open_activity(page):
    status = page.get_by_test_id("business-document-status-toggle")
    if status.get_attribute("aria-expanded") != "true":
        status.click()


@pytest.mark.p1
@pytest.mark.parametrize("theme,pane_width", [("dark", 360), ("light", 360), ("dark", 460), ("light", 460)])
def test_comment_sending_preserves_draft_and_button_geometry(page, base_url, theme, pane_width):
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.add_init_script(f"localStorage.setItem('ragflow-ui-theme', '{theme}'); localStorage.setItem('ragflow.business-documents.protocol-pane-width', '{pane_width}')")
    state, _ = setup(page, base_url)
    state.update(
        operation_state="ANALYZING_REVIEW", latest_job={"job_id": "analysis-job", "job_type": "ASSESS_REVIEW", "status": "RUNNING", "attempt": 1, "max_attempts": 3, "progress_stage": "MODEL_REQUEST"}
    )
    page.reload()
    composer = page.get_by_test_id("business-document-comment-composer")
    text = composer.get_by_role("textbox", name="Комментарий", exact=True)
    send = composer.get_by_role("button", name="Добавить комментарий", exact=True)
    held = []
    page.route("**/api/v1/business-documents/doc-ui-1/commands", lambda route: held.append(route))
    wheel = composer.get_by_role("button", name="Идёт обработка документа", exact=True)
    expect(wheel).to_be_disabled()
    expect(wheel.locator("svg.animate-spin")).to_have_count(1)
    empty_bounds = wheel.bounding_box()
    output = Path(os.environ["PW_ARTIFACTS_DIR"]) / f"comment-empty-{theme}-{pane_width}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    composer.screenshot(path=str(output))
    text.fill("Отправленный комментарий")
    expect(send).to_be_enabled()
    expect(send).to_have_attribute("aria-busy", "false")
    before = send.bounding_box()
    for dimension in ("x", "y", "width", "height"):
        assert abs(empty_bounds[dimension] - before[dimension]) < 1
    send.click()
    expect(send).to_have_attribute("aria-busy", "false")
    expect(send).to_be_disabled()
    expect(send.locator("svg")).to_have_count(1)
    expect(send.locator("svg.animate-spin")).to_have_count(0)
    expect(text).to_be_editable()
    during = send.bounding_box()
    for dimension in ("x", "y", "width", "height"):
        assert abs(before[dimension] - during[dimension]) < 1, (before, during)
    assert send.evaluate("node => node.scrollWidth <= node.clientWidth + 1")
    text.fill("Следующий черновик")
    text.press("Control+Enter")
    expect(composer.get_by_role("button", name="Сохранить в заметки", exact=True)).to_be_enabled()
    assert len(held) == 1
    submitted = held[0].request.post_data_json
    assert submitted["payload"]["text"] == "Отправленный комментарий"
    output = Path(os.environ["PW_ARTIFACTS_DIR"]) / f"comment-sending-{theme}-{pane_width}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    composer.screenshot(path=str(output))
    state["state_version"] += 1
    state["protocol"]["comments"].append({"comment_id": "sent-comment", **submitted["payload"], "anchor_status": "GENERAL"})
    held[0].fulfill(
        json=_envelope(
            {
                "accepted": True,
                "document_id": state["document_id"],
                "state_version": state["state_version"],
                "lifecycle_state": state["lifecycle_state"],
                "operation_state": state["operation_state"],
                "document": state,
            }
        )
    )
    expect(send).to_have_attribute("aria-busy", "false")
    expect(send).to_be_enabled()
    expect(text).to_have_value("Следующий черновик")
    expect(page.get_by_test_id("business-document-comment").filter(has_text="Отправленный комментарий")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


@pytest.mark.p1
@pytest.mark.parametrize("width,theme", [(1440, "dark"), (1280, "light"), (700, "dark"), (390, "dark"), (390, "light")])
def test_compact_header_status_keyboard_and_states(page, base_url, width, theme):
    page.set_viewport_size({"width": width, "height": 1000})
    page.add_init_script(f"localStorage.setItem('ragflow-ui-theme', '{theme}')")
    state, requests = setup(page, base_url)
    state.update(
        title="Очень длинное название бизнес-требований для проверки компактного статуса " * 3,
        operation_state="APPLYING_CHANGES",
        allowed_commands=[],
        permissions={"edit": False},
        latest_job={
            "job_id": "compact-job",
            "job_type": "PLAN_CHANGES",
            "status": "RUNNING",
            "attempt": 1,
            "max_attempts": 3,
            "progress_stage": "GENERATING",
            "create_time": int(time.time() * 1000) - 9000,
        },
        background_work=[{"request_id": "work", "kind": "PLAN_CHANGES", "status": "RUNNING"}],
        last_analysis={"event_id": "old-analysis", "applied": True, "superseded": True, "created_at": int(time.time() * 1000)},
    )
    page.reload()
    header = page.get_by_test_id("business-document-header")
    status = header.get_by_test_id("business-document-status-toggle")
    expect(status).to_contain_text("Готовим исправления", timeout=RESULT_TIMEOUT_MS)
    expect(status).to_contain_text("2/4")
    expect(status).not_to_contain_text("Попытка")
    expect(page.get_by_role("button", name="Подробнее", exact=True)).to_have_count(0)
    expect(page.get_by_test_id("business-document-activity")).to_have_count(0)
    assert status.bounding_box()["height"] == 24
    assert status.evaluate("node => node.scrollWidth <= node.clientWidth + 1")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    screenshot(page, f"compact-status-{width}-{theme}.png")
    status.focus()
    status.press("Enter")
    details = page.get_by_role("dialog", name="Подробности фоновой работы")
    expect(details).to_be_visible()
    expect(details).to_contain_text("Попытка 1 из 3")
    expect(details).to_contain_text("Результат предыдущего анализа устарел")
    expect(details).to_contain_text("Работа идёт в фоне")
    assert details.evaluate("node => node.scrollWidth <= node.clientWidth + 1")
    screenshot(page, f"compact-details-{width}-{theme}.png")
    page.keyboard.press("Escape")
    expect(details).to_have_count(0)
    expect(status).to_be_focused()
    status.press("Space")
    expect(details).to_be_visible()
    header.get_by_role("heading", level=1).click()
    expect(details).to_have_count(0)
    state["latest_job"].update(status="RETRY", attempt=2)
    state["background_work"].append({"request_id": "queued", "kind": "PLAN_CHANGES", "status": "QUEUED"})
    expect(status).to_contain_text("2/3", timeout=10000)
    expect(status).to_contain_text("В очереди: 1")
    assert status.evaluate("node => node.scrollWidth <= node.clientWidth + 1")
    screenshot(page, f"compact-retry-{width}-{theme}.png")
    state.update(operation_state="IDLE", background_work=[], last_analysis={"event_id": "done", "applied": True, "created_at": int(time.time() * 1000), "question_count": 0, "proposal_count": 1})
    state["latest_job"].update(status="COMPLETED", progress_stage="COMPLETED", error=None)
    expect(status).to_contain_text("Обработка завершена", timeout=10000)
    expect(status).not_to_contain_text("Новые")
    open_activity(page)
    expect(page.get_by_test_id("business-document-analysis-result")).to_contain_text("предложений — 1")
    page.keyboard.press("Escape")
    state.update(operation_state="FAILED", last_error={"message": "Модель недоступна"})
    state["latest_job"].update(status="DEAD", progress_stage="FAILED")
    state["background_work"] = [{"request_id": "failed", "kind": "PLAN_CHANGES", "status": "FAILED"}]
    # Idle documents stop polling; a reload reads the next server state.
    page.reload()
    expect(status).to_contain_text("Не удалось завершить работу", timeout=RESULT_TIMEOUT_MS)
    expect(status).not_to_contain_text("2/4")
    assert not requests, "Inspecting progress must not issue document commands"


@pytest.mark.p1
@pytest.mark.parametrize("width,theme", [(1440, "light"), (1280, "light"), (1440, "dark")])
def test_background_queue_comments_reload_and_revision_change(page, base_url, width, theme):
    page.set_viewport_size({"width": width, "height": 1000})
    page.add_init_script(f"localStorage.setItem('ragflow-ui-theme', '{theme}')")
    state, requests = setup(page, base_url)
    original_revision = state["current_revision"]["revision_id"]
    page.get_by_test_id("accept-proposal-proposal-1").click()
    expect(page.get_by_test_id("business-document-status-toggle")).to_contain_text("2/4")
    expect(page.get_by_test_id("accept-proposal-proposal-2")).to_be_enabled()
    page.get_by_test_id("accept-proposal-proposal-2").click()
    expect(page.get_by_test_id("business-document-status-toggle")).to_contain_text("В очереди: 1")
    section = page.locator('[data-section-id="1"][data-section-text]')
    expect(section).to_have_attribute("data-background-state", "possible")
    # Select a quote in saved content while its next version is being prepared.
    section.locator("[data-section-body] span").first.evaluate("""node => {
      const text = node.firstChild;
      const range = document.createRange(); range.setStart(text, 0); range.setEnd(text, 9);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }""")
    section.dispatch_event("mouseup")
    composer = page.get_by_role("textbox", name="Комментарий", exact=True)
    composer.fill("Комментарий к сохранённой цитате")
    # Autosave arrives while the author is typing: keep the draft and old quote.
    state["current_revision"] = deepcopy(state["current_revision"])
    state["current_revision"].update(revision_id="revision-4", revision_number=4)
    state["current_revision"]["section_texts"]["1"] = "Новое сохранённое требование"
    state["current_revision"]["document_ast"]["sections"][0]["blocks"][0]["text"] = "Новое сохранённое требование"
    state["state_version"] += 1
    expect(section).to_contain_text("Новое сохранённое требование", timeout=10000)
    expect(composer).to_have_value("Комментарий к сохранённой цитате")
    send = page.get_by_role("button", name="Добавить комментарий", exact=True)
    expect(send).to_be_enabled()
    send.click()
    expect(page.get_by_test_id("business-document-comment")).to_contain_text("Комментарий к сохранённой цитате")
    assert requests[-1]["payload"]["revision_id"] == original_revision
    assert requests[-1]["payload"]["anchor"]["selected_text"]
    assert [item["type"] for item in requests] == ["ACCEPT_PROPOSAL", "ACCEPT_PROPOSAL", "ADD_COMMENT"]
    composer.fill("Незавершённый комментарий")
    page.reload()
    expect(page.get_by_test_id("business-document-status-toggle")).to_contain_text("В очереди: 1", timeout=RESULT_TIMEOUT_MS)
    expect(page.get_by_role("textbox", name="Комментарий", exact=True)).to_have_value("Незавершённый комментарий")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    screenshot(page, f"background-queue-{width}-{theme}.png")


@pytest.mark.p1
def test_background_failure_retry_and_analysis_result(page, base_url):
    page.set_viewport_size({"width": 1440, "height": 1000})
    state, requests = setup(page, base_url)
    page.get_by_test_id("accept-proposal-proposal-1").click()
    state["operation_state"] = "FAILED"
    state["latest_job"].update(status="DEAD", progress_stage="FAILED")
    state["background_work"][0].update(status="FAILED", error={"code": "MODEL_UNAVAILABLE", "message": "Модель недоступна"})
    state["protocol"]["proposals"][0]["application"] = {"status": "DEAD", "error": {"code": "MODEL_UNAVAILABLE", "message": "Модель недоступна"}}
    expect(page.get_by_role("button", name="Подготовить изменения")).to_be_visible(timeout=10000)
    expect(page.get_by_role("textbox", name="Комментарий", exact=True)).to_be_editable()
    screenshot(page, "background-failed.png")
    with page.expect_response("**/api/v1/business-documents/doc-ui-1/commands"):
        page.get_by_role("button", name="Подготовить изменения").click()
    expect(page.get_by_test_id("business-document-status-toggle")).to_contain_text("2/4")
    assert requests[-1]["type"] == "ACCEPT_PROPOSAL"
    state["state_version"] += 1
    state["protocol"]["proposals"][0].update(applied=True, application=None)
    state.update(operation_state="IDLE", background_work=[], last_application={"revision_id": state["current_revision"]["revision_id"], "section_ids": ["1"]})
    state["latest_job"]["status"] = "COMPLETED"
    expect(page.get_by_test_id("accept-proposal-proposal-1")).to_have_count(0)
    expect(page.locator('[data-section-id="1"][data-section-text]')).to_have_attribute("data-background-state", "saved", timeout=10000)
    page.get_by_role("button", name="Проанализировать замечания").click()
    expect(page.get_by_test_id("business-document-status-toggle")).to_contain_text("3/4")
    state["state_version"] += 1
    state.update(
        operation_state="IDLE", background_work=[], last_analysis={"event_id": "analysis-done", "created_at": int(time.time() * 1000), "applied": True, "question_count": 0, "proposal_count": 0}
    )
    state["latest_job"]["status"] = "COMPLETED"
    open_activity(page)
    expect(page.get_by_test_id("business-document-analysis-result")).to_contain_text("Анализ завершён: новых вопросов — 0, предложений — 0", timeout=10000)
    screenshot(page, "background-analysis-done.png")
    page.reload()
    expect(page.get_by_test_id("business-document-status-toggle")).to_be_visible(timeout=RESULT_TIMEOUT_MS)
    open_activity(page)
    expect(page.get_by_test_id("business-document-analysis-result")).to_be_visible(timeout=RESULT_TIMEOUT_MS)


@pytest.mark.p1
@pytest.mark.parametrize("width", [1440, 390])
def test_section_collapse_during_background_work(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 1000})
    state, requests = setup(page, base_url)
    section = page.locator('[data-section-id="1"][data-section-text]')
    toggle = section.get_by_role("button", name="1. Цель", exact=True)
    body = section.locator("[data-section-body]")
    expect(body).to_be_visible()
    toggle.focus()
    toggle.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(body).to_be_hidden()
    page.get_by_test_id("accept-proposal-proposal-1").click()
    page.get_by_test_id("accept-proposal-proposal-2").click()
    expect(section).to_have_attribute("data-background-state", "possible")
    expect(section.get_by_text("Возможно изменение", exact=False)).to_be_visible()
    expect(body).to_be_hidden()
    # A new saved revision must preserve the reader's collapsed section.
    state["current_revision"] = deepcopy(state["current_revision"])
    state["current_revision"].update(revision_id="revision-4", revision_number=4)
    state["current_revision"]["section_texts"]["1"] = "Обновлённое сохранённое требование"
    state["current_revision"]["document_ast"]["sections"][0]["blocks"][0]["text"] = "Обновлённое сохранённое требование"
    state["state_version"] += 1
    expect(section).to_have_attribute("data-section-text", "Обновлённое сохранённое требование", timeout=10000)
    expect(body).to_be_hidden()
    screenshot(page, f"collapsed-background-{width}.png")
    # Navigation from the queue opens the target before scrolling to it.
    open_activity(page)
    page.get_by_test_id("business-document-background-work").get_by_role("button", name="§ 1", exact=True).click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(body).to_be_visible()
    toggle.focus()
    toggle.press("Space")
    expect(body).to_be_hidden()
    toggle.press("Space")
    expect(body).to_be_visible()
    body.locator("span").first.evaluate("""node => {
      const text = node.firstChild;
      const range = document.createRange(); range.setStart(text, 0); range.setEnd(text, 11);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }""")
    section.dispatch_event("mouseup")
    page.get_by_role("textbox", name="Комментарий", exact=True).fill("Комментарий после раскрытия")
    page.get_by_role("button", name="Добавить комментарий", exact=True).click()
    expect(page.get_by_test_id("business-document-comment")).to_contain_text("Комментарий после раскрытия")
    assert requests[-1]["payload"]["anchor"]["section_id"] == "1"
    assert requests[-1]["payload"]["revision_id"] == "revision-4"
    state.update(operation_state="IDLE", background_work=[], last_application={"revision_id": "revision-4", "section_ids": ["1"]})
    state["latest_job"]["status"] = "COMPLETED"
    state["state_version"] += 1
    toggle.click()
    expect(section).to_have_attribute("data-background-state", "saved", timeout=10000)
    expect(body).to_be_hidden()
    expect(section.get_by_text("Изменения сохранены в этой редакции", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    screenshot(page, f"collapsed-saved-{width}.png")


@pytest.mark.p1
def test_unapplied_analysis_displays_failure_until_later_feedback(page, base_url):
    state, requests = setup(page, base_url)
    page.get_by_role("button", name="Проанализировать замечания").click()
    expect(page.get_by_test_id("business-document-status-toggle")).to_contain_text("3/4")
    state["state_version"] += 1
    state["operation_state"] = "IDLE"
    state["latest_job"].update(status="COMPLETED", progress_stage="COMPLETED")
    state["background_work"][0].update(status="FAILED", error={"code": "RESULT_NOT_APPLIED", "message": "Результат требует уточнения"})
    state["last_analysis"] = {"event_id": "analysis-not-applied", "applied": False, "superseded": False, "created_at": int(time.time() * 1000)}
    open_activity(page)
    result = page.get_by_test_id("business-document-analysis-result")
    expect(result).to_have_text("Анализ завершён без применения результата. Проверьте замечания.", timeout=10000)
    expect(result).not_to_contain_text("устарел")
    screenshot(page, "analysis-not-applied.png")
    page.keyboard.press("Escape")
    page.get_by_role("textbox", name="Комментарий", exact=True).fill("Новое замечание после анализа")
    # The server projection marks the older result stale after this new comment.
    state["last_analysis"]["superseded"] = True
    page.get_by_role("button", name="Добавить комментарий", exact=True).click()
    expect(page.get_by_test_id("business-document-comment")).to_contain_text("Новое замечание после анализа")
    open_activity(page)
    expect(result).to_have_text("Результат предыдущего анализа устарел после новых замечаний.")
    assert requests[-1]["type"] == "ADD_COMMENT"
