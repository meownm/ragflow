"""C13: a genuine SQL permission error preserves the confirmed analysis.

Only the disposable fixture's own SELECT grant is revoked/restored. The real
worker rejects the new query at physical preflight or execution; no API/model/
result response is substituted. An EXPLAIN rejection need not create a Run.
"""

import re
import time

from playwright.sync_api import expect

from test.playwright.e2e import test_sql_chat_calendar_live as calendar
from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
real_sql_catalog = calendar.real_sql_catalog
chat_probe = chat.chat_probe


def test_chat_real_execution_error_keeps_previous_result_until_success(page, live, chat_probe, real_sql_catalog):
    schema = real_sql_catalog["schema"]
    assert re.fullmatch(r"qa_[0-9a-f]{16}", schema)
    question = (
        f"Используй только {chat_probe['fqn']}. Покажи заказы со status = paid за календарный сентябрь 2026 UTC по created_at. "
        "Выходные имена колонок строго id и amount, одна строка — один заказ, сортировка id ASC, LIMIT 100. Других условий нет, без JOIN, только чтение."
    )
    chat._start(page, live, chat_probe, question)
    baseline_rows = [[101, 100], [102, 100], [105, 40], [107, 0]]
    if real_sql_catalog["calendar_discriminator"]:
        baseline_rows += [[108, 50], [109, 50], [110, 150]]
    before = chat._rows(page, live, chat_probe, baseline_rows, ["id", "amount"])
    grant = live.sql(
        "SELECT json_build_object('role',min(grantee),'count',count(*)) FROM information_schema.role_table_grants "
        f"WHERE table_schema='{schema}' AND table_name='regression_orders' AND privilege_type='SELECT' AND grantee LIKE 'qa_ro_%';"
    )
    assert grant["count"] == 1 and re.fullmatch(r"qa_ro_[0-9a-f]{16}", grant["role"])
    role = grant["role"]
    initial_runs = {run["id"] for run in before["session"]["project"]["runs"]}
    failed_run = None
    try:
        live.sql(f"REVOKE SELECT ON {schema}.regression_orders FROM {role}; SELECT json_build_object('own_select_revoked',true);")
        denial = live.sql(f"SELECT json_build_object('allowed',has_table_privilege('{role}','{schema}.regression_orders','SELECT'));")
        assert denial["allowed"] is False
        chat._send(page, chat_probe, "Оставь amount не меньше 50. Остальные условия сохрани.")
        deadline = time.monotonic() + chat.TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            session = live.api("GET", chat.SESSIONS + "/" + chat_probe["id"])
            job = session["project"].get("current_job") or {}
            if job.get("id"):
                chat_probe["jobs"].add(job["id"])
            failed_run = next((run for run in session["project"].get("runs", []) if run["id"] not in initial_runs and run["status"] == "FAILED"), None)
            error = session["project"].get("last_error") or session.get("conversation", {}).get("error") or {}
            if session.get("conversation", {}).get("phase") == "ERROR":
                assert error.get("code") in {"SQL_PHYSICAL_SCHEMA_INVALID", "SQL_EXECUTION_FAILED"}, "A model/planning failure is not evidence of the injected PostgreSQL denial"
                if chat._idle(session["project"]):
                    break
            page.wait_for_timeout(500)
        assert error.get("code") in {"SQL_PHYSICAL_SCHEMA_INVALID", "SQL_EXECUTION_FAILED"}, "The genuine PostgreSQL denial must be reported"
        assert all(run["status"] == "FAILED" for run in session["project"].get("runs", []) if run["id"] not in initial_runs), "A denied query cannot publish a ready result"
        assert chat._idle(session["project"]), "Failure acceptance requires the actual worker to finish"
        assert session["selected_result_id"] == before["result"]["run_id"]
        retained = live.api("GET", chat.SESSIONS + "/" + chat_probe["id"] + "/results/" + before["result"]["run_id"])
        assert chat._identity({"result": retained}) == chat._identity(before)
        assert chat._matches(retained, ["id", "amount"], baseline_rows)
        expect(chat._root(page).get_by_role("alert").first).to_be_visible()
        table = chat._root(page).get_by_role("table", name="Данные выбранного результата", exact=True)
        expect(table.locator("tbody tr")).to_have_count(len(baseline_rows))
        page.screenshot(path=str(live.out / (chat_probe["proof_id"] + "-failed-sql.png")), full_page=True)
        live.proof(
            chat_probe["proof_id"] + "-real-sql-failure",
            {
                "fault": "REVOKE SELECT on the exact owned fixture table and role",
                "failed_run": failed_run,
                "error_code": error["code"],
                "physical_select_denied": denial["allowed"] is False,
                "retained_identity": chat._identity({"result": retained}),
                "retained_rows": retained["rows"],
                "scope": "genuine PostgreSQL failure; no synthetic API success",
            },
        )
    finally:
        live.sql(f"GRANT SELECT ON {schema}.regression_orders TO {role}; SELECT json_build_object('own_select_restored',true);")
    chat._send(page, chat_probe, "После исправления ошибки снова оставь amount не меньше 50. Период, paid, источник и поля сохрани.")
    expected = [[101, 100], [102, 100]]
    if real_sql_catalog["calendar_discriminator"]:
        expected += [[108, 50], [109, 50], [110, 150]]
    recovered = chat._rows(page, live, chat_probe, expected, ["id", "amount"])
    assert recovered["result"]["run_id"] != before["result"]["run_id"]
    historical = live.api("GET", chat.SESSIONS + "/" + chat_probe["id"] + "/results/" + before["result"]["run_id"])
    assert chat._identity({"result": historical}) == chat._identity(before)
    assert chat._matches(historical, ["id", "amount"], baseline_rows)
    chat._complete(page, live, chat_probe, recovered)
