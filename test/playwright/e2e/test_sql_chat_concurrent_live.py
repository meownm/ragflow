"""C16: real SPA commands race from the same persisted version.

The browser transport is held until both commands exist, then both original
requests go to the real API. No API response/model/result is manufactured.
"""

from copy import deepcopy
import re
import time

from playwright.sync_api import expect

from test.playwright.e2e import test_sql_chat_calendar_live as calendar
from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
real_sql_catalog = calendar.real_sql_catalog
chat_probe = chat.chat_probe


def test_chat_two_tabs_reject_one_stale_command_without_losing_the_winner(page, live, chat_probe, real_sql_catalog):
    chat._start(page, live, chat_probe, calendar._question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    before = chat._rows(page, live, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
    second_context = page.context.browser.new_context()
    second = second_context.new_page()
    second_probe = deepcopy(chat_probe)
    held = []
    outcomes = []
    endpoint = re.compile(r"/api/v1/sql-assistant/sessions/" + chat_probe["id"] + r"/commands(?:\?.*)?$")

    def hold(route):
        wire = route.request.post_data_json
        if wire.get("type") == "message":
            held.append({"route": route, "wire": deepcopy(wire), "released": False})
        else:
            route.continue_()

    def observe(response):
        if endpoint.search(response.url) and response.request.method == "POST":
            outcomes.append({"status": response.status, "wire": deepcopy(response.request.post_data_json), "body": response.json()})

    page.on("response", observe)
    second.on("response", observe)
    page.route(endpoint, hold)
    second.route(endpoint, hold)
    try:
        live.browser(second, "/sql-assistant?session=" + chat_probe["id"])
        restored = chat._rows(second, live, second_probe, calendar._september(real_sql_catalog), ["id", "amount"])
        assert chat._identity(restored) == chat._identity(before)
        assert restored["session"]["revision"] == before["session"]["revision"]
        first_text = "Вместо прежнего порога оставь amount не меньше 50. Остальные условия сохрани."
        second_text = "Вместо прежнего порога оставь amount меньше 50. Остальные условия сохрани."
        chat._send(page, chat_probe, first_text)
        chat._send(second, second_probe, second_text)
        deadline = time.monotonic() + 15
        while len(held) < 2 and time.monotonic() < deadline:
            page.wait_for_timeout(25)
        assert len(held) == 2, "Both real browser commands must exist before either reaches the API"
        assert held[0]["wire"]["expected_revision"] == held[1]["wire"]["expected_revision"] == before["session"]["revision"]
        assert held[0]["wire"]["expected_project_version"] == held[1]["wire"]["expected_project_version"] == before["session"]["project"]["state_version"]
        assert held[0]["wire"]["client_request_id"] != held[1]["wire"]["client_request_id"]
        for item in held:
            item["released"] = True
            item["route"].continue_()
        deadline = time.monotonic() + 30
        while len(outcomes) < 2 and time.monotonic() < deadline:
            page.wait_for_timeout(25)
        assert len(outcomes) == 2
        page.unroute(endpoint, hold)
        second.unroute(endpoint, hold)
        assert sorted(item["status"] for item in outcomes) == [200, 409]
        winner = next(item for item in outcomes if item["status"] == 200)
        loser = next(item for item in outcomes if item["status"] == 409)
        assert winner["body"]["code"] == 0
        assert "SQL_SESSION_VERSION_CONFLICT" in str(loser["body"])
        winner_text = winner["wire"]["payload"]["text"]
        winner_page, loser_page = (page, second) if winner_text == first_text else (second, page)
        if winner_text == second_text:
            chat_probe["turns"] = second_probe["turns"]
        high = [[101, 100], [102, 100]] + ([[108, 50], [109, 50], [110, 150]] if real_sql_catalog["calendar_discriminator"] else [])
        low = [[105, 40], [107, 0]]
        expected = high if winner_text == first_text else low
        accepted = chat._rows(winner_page, live, chat_probe, expected, ["id", "amount"])
        messages = [message["text"] for message in accepted["session"]["messages"]]
        assert messages.count(winner_text) == 1
        assert loser["wire"]["payload"]["text"] not in messages
        initial_runs = {run["id"] for run in before["session"]["project"]["runs"]}
        final_runs = {run["id"] for run in accepted["session"]["project"]["runs"]}
        assert initial_runs.issubset(final_runs) and len(final_runs - initial_runs) == 1
        expect(chat._root(loser_page).get_by_role("alert").first).to_be_visible()
        loser_text = loser["wire"]["payload"]["text"]
        composer = chat._root(loser_page).locator("textarea:visible")
        expect(composer).to_have_value(loser_text)
        chat._root(loser_page).get_by_role("button", name="Обновить состояние", exact=True).click()
        assert chat._identity(chat._rows(loser_page, live, chat_probe, expected, ["id", "amount"])) == chat._identity(accepted)
        expect(composer).to_have_value(loser_text)
        chat_probe["turns"].append({"text": loser_text, "session_id": chat_probe["id"], "started_monotonic": time.monotonic()})
        # Submit the retained draft as-is: refilling it would conceal text loss.
        submit = composer.locator("xpath=ancestor::form[1]").locator('button[type="submit"]')
        expect(submit).to_be_enabled()
        submit.click()
        retried = chat._rows(loser_page, live, chat_probe, low if winner_text == first_text else high, ["id", "amount"])
        assert len(outcomes) == 3 and outcomes[-1]["status"] == 200
        retry_wire = outcomes[-1]["wire"]
        assert retry_wire["client_request_id"] != loser["wire"]["client_request_id"]
        assert retry_wire["expected_revision"] == accepted["session"]["revision"]
        assert retry_wire["payload"]["run_id"] == accepted["result"]["run_id"]
        historical = live.api("GET", chat.SESSIONS + "/" + chat_probe["id"] + "/results/" + accepted["result"]["run_id"])
        assert chat._identity({"result": historical}) == chat._identity(accepted)
        assert sum(message["text"] == loser_text for message in retried["session"]["messages"]) == 1
        live.proof(
            chat_probe["proof_id"] + "-concurrent-commands",
            {
                "requests": [{"status": item["status"], "wire": item["wire"]} for item in outcomes],
                "winning_run_id": accepted["result"]["run_id"],
                "one_new_run_before_explicit_retry": True,
                "draft_retained_and_resubmitted": True,
            },
        )
        chat._complete(loser_page, live, chat_probe, retried)
    finally:
        for item in held:
            if not item["released"]:
                item["route"].abort("failed")
        page.unroute(endpoint, hold)
        page.remove_listener("response", observe)
        second_context.close()
