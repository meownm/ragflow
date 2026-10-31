"""C09/C10/C21: real chat on owned calendar boundary rows, two data sets.

Run with live_ragflow/runner.py against the frozen application candidate.
Only fixture setup writes PostgreSQL/OpenMetadata; all user actions use the SPA.
All scenarios preserve the complete eight-table golden schema and real FKs.
This external harness deliberately does not modify the frozen application source.
"""

from datetime import datetime
import re
import time

from test.playwright.e2e import sql_chat_relation_fixture as catalog_fixture
from playwright.sync_api import expect

from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
chat_probe = chat.chat_probe
real_sql_catalog = catalog_fixture.real_sql_catalog


def _question(probe, period):
    return (
        f"Используй только {probe['fqn']}. Покажи заказы со status = paid за {period} по created_at. "
        "Выходные имена колонок строго id и amount в этом порядке; одна строка — один заказ, сортировка id ASC, LIMIT 100. "
        "Других условий нет, без JOIN, только чтение."
    )


def _september(fixture):
    return [[101, 100], [102, 100], [105, 40], [107, 0]] + ([[108, 50], [109, 50], [110, 150]] if fixture["calendar_discriminator"] else [])


def test_chat_utc_period_and_exact_sum(page, live, chat_probe, real_sql_catalog):
    """C09: the exact catalog case, including source-level SUM without DISTINCT."""
    chat._start(page, live, chat_probe, _question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    chat._rows(page, live, chat_probe, _september(real_sql_catalog), ["id", "amount"])
    chat._send(page, chat_probe, "Измени SQL на SUM(amount) без DISTINCT по тем же условиям. Одна колонка total, одна строка, без GROUP BY. Период, paid и источник сохрани.")
    total = 490 if real_sql_catalog["calendar_discriminator"] else 240
    summed = chat._rows(page, live, chat_probe, [[total]], ["total"])
    chat._complete(page, live, chat_probe, summed)


def test_chat_calendar_zone_then_period_keep_paid_scope(page, live, chat_probe, real_sql_catalog):
    """Additional timezone boundary counterexample; no cancelled order leaks."""
    chat._start(page, live, chat_probe, _question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    initial = chat._rows(page, live, chat_probe, _september(real_sql_catalog), ["id", "amount"])
    chat._send(page, chat_probe, "Сохрани сентябрь 2026, но календарные границы считай в Europe/Moscow вместо UTC. Все остальные условия сохрани.")
    moscow_rows = [[101, 100], [105, 40], [106, 30], [107, 0]] + ([[108, 50], [109, 50], [110, 150]] if real_sql_catalog["calendar_discriminator"] else [])
    moscow = chat._rows(page, live, chat_probe, moscow_rows, ["id", "amount"])
    assert chat._identity(moscow) != chat._identity(initial)
    chat._send(page, chat_probe, "Теперь календарный октябрь 2026 в том же Europe/Moscow. Остальные условия сохрани.")
    october = chat._rows(page, live, chat_probe, [[102, 100], [103, 80]], ["id", "amount"])
    assert chat._identity(october) != chat._identity(moscow)
    chat._complete(page, live, chat_probe, october)


def test_chat_missing_year_requires_an_ordinary_reply(page, live, chat_probe, real_sql_catalog):
    """C10: an unresolved replacement period preserves the successful result."""
    chat._start(page, live, chat_probe, _question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    before = chat._rows(page, live, chat_probe, _september(real_sql_catalog), ["id", "amount"])
    chat._send(page, chat_probe, "Оставь октябрь; год нового периода пока не согласован. Остальные условия сохрани.")
    waiting = chat._wait(page, live, chat_probe, lambda value: value["session"]["conversation"]["phase"] == "WAITING")
    assert chat._identity(waiting) == chat._identity(before)
    assert [run["id"] for run in waiting["session"]["project"].get("runs", [])] == [run["id"] for run in before["session"]["project"].get("runs", [])]
    question = chat._clarification_question(waiting)
    assert re.search("год|году|year", question, re.IGNORECASE)
    expect(chat._root(page)).to_contain_text(question)
    chat._send(page, chat_probe, "2026, UTC. Остальные условия сохрани.")
    result = chat._rows(page, live, chat_probe, [[103, 80]], ["id", "amount"])
    chat._complete(page, live, chat_probe, result)


def test_chat_empty_period_is_success_without_expanding_scope(page, live, chat_probe, real_sql_catalog):
    """C21: a successful empty result cannot silently broaden the period."""
    chat._start(page, live, chat_probe, _question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    before = chat._rows(page, live, chat_probe, _september(real_sql_catalog), ["id", "amount"])
    chat._send(page, chat_probe, "Выбери календарный январь 2028 UTC вместо прежнего периода. Все остальные условия сохрани.")
    empty = chat._wait(
        page,
        live,
        chat_probe,
        lambda value: chat._matches(value["result"], ["id", "amount"], []) and chat._idle(value["session"]["project"]),
    )
    assert chat._identity(empty) != chat._identity(before)
    assert empty["result"]["execution_status"] == "READY"
    expect(chat._root(page)).to_contain_text("Показано 0 из 0 строк сохранённого набора.")
    page.reload(wait_until="domcontentloaded")
    restored = chat._wait(page, live, chat_probe, lambda value: chat._matches(value["result"], ["id", "amount"], []))
    assert chat._identity(restored) == chat._identity(empty)
    chat._complete(page, live, chat_probe, restored)


def test_chat_selected_group_monthly_drilldown_preserves_measures_and_history(page, live, chat_probe, real_sql_catalog):
    """The failing LLM-log journey on independent rows and a NOT NULL timestamp."""
    extra = real_sql_catalog["calendar_discriminator"]
    chat._start(
        page,
        live,
        chat_probe,
        f"Используй только {chat_probe['fqn']}. За весь доступный период посчитай COUNT(id) AS request_count и AVG(amount) AS avg_amount по status. "
        "Исключи заказ id = 101. Колонки status, request_count, avg_amount. Сортировка request_count DESC, LIMIT 100. Без JOIN, только чтение.",
    )
    groups = [["paid", 8, "62.5000000000000000"], ["cancelled", 3, "75.0000000000000000"]] if extra else [["paid", 5, "50.0000000000000000"], ["cancelled", 1, "70.0000000000000000"]]
    before = chat._rows(page, live, chat_probe, groups, ["status", "request_count", "avg_amount"])
    table = chat._root(page).get_by_role("table", name="Данные выбранного результата", exact=True)
    table.locator("tbody tr").filter(has_text="paid").get_by_role("button", name="Уточнить по этой строке", exact=True).click()
    chat._send(page, chat_probe, "Детализируй выбранную группу по месяцам created_at. Часовой пояс UTC. Остальные условия и обе метрики сохрани.")

    def monthly(value):
        result = value["result"]
        return bool(result and result["run_id"] != before["result"]["run_id"] and chat._idle(value["session"]["project"]))

    value = chat._wait(page, live, chat_probe, monthly)
    result = value["result"]
    names = [column["name"] for column in result["columns"]]
    assert {"request_count", "avg_amount"} <= set(names)
    time_columns = [name for name in names if name not in {"status", "request_count", "avg_amount"}]
    assert len(time_columns) == 1
    observed = {}
    for row in result["rows"]:
        cells = dict(zip(names, row["values"], strict=True))
        if "status" in cells:
            assert cells["status"] == "paid", "Selected group must filter the source"
        stamp = datetime.fromisoformat(cells[time_columns[0]].replace("Z", "+00:00"))
        assert stamp.day == 1 and stamp.hour == stamp.minute == stamp.second == 0
        observed[stamp.strftime("%Y-%m")] = [cells["request_count"], cells["avg_amount"]]
    expected = {"2026-08": [1, "30"], "2026-09": [6, "65"], "2026-10": [1, "80"]} if extra else {"2026-08": [1, "30"], "2026-09": [3, "46.6666666666666667"], "2026-10": [1, "80"]}
    assert len(result["rows"]) == len(expected)
    assert observed.keys() == expected.keys()
    assert all(chat._equal(a, b) for month, pair in expected.items() for a, b in zip(observed[month], pair, strict=True))
    counts = [dict(zip(names, row["values"], strict=True))["request_count"] for row in result["rows"]]
    assert counts == sorted(counts, reverse=True), "Accepted aggregate sorting must remain"
    assert "TO_CHAR" not in result["sql"].upper()
    assert len(value["session"]["messages"]) == len(before["session"]["messages"]) + 2, "Fully specified drill-down must not ask redundant questions"
    assert before["result"]["run_id"] in {run["id"] for run in value["session"]["project"]["runs"]}
    page.reload(wait_until="domcontentloaded")
    restored = chat._wait(page, live, chat_probe, lambda item: item["result"] and chat._identity(item) == chat._identity(value))
    assert restored["result"]["rows"] == result["rows"]
    table = chat._root(page).get_by_role("table", name="Данные выбранного результата", exact=True)
    expect(table).to_be_visible(timeout=30000)
    expect(table.locator("tbody tr")).to_have_count(3)
    if not extra:
        replacement = "Замени только AVG(amount) AS avg_amount на SUM(amount) AS total_amount. COUNT, месяцы, фильтры, сортировку и лимит сохрани."

        def waiting(item):
            turn = item["session"]["conversation"]
            return turn["phase"] == "WAITING" and turn["pending"]["kind"] == "REVISION_REVIEW"

        def choose(text):
            chat_probe["turns"].append({"text": text, "session_id": chat_probe["id"], "started_monotonic": time.monotonic()})
            chat._root(page).get_by_role("button", name=text, exact=True).click()

        chat._send(page, chat_probe, replacement)
        review = chat._wait(page, live, chat_probe, waiting)
        assert chat._identity(review) == chat._identity(restored)
        chat._root(page).get_by_text("Изменения SQL и параметров", exact=True).click()
        expect(chat._root(page).locator("details[open] pre").first).to_be_visible()
        choose("Оставить прежний расчёт")
        unchanged = chat._wait(page, live, chat_probe, lambda item: item["session"]["conversation"]["phase"] == "READY")
        assert chat._identity(unchanged) == chat._identity(restored)
        chat._send(page, chat_probe, replacement)
        chat._wait(page, live, chat_probe, waiting)
        choose("Применить правку")
        restored = chat._wait(page, live, chat_probe, lambda item: item["result"] and item["result"]["run_id"] != result["run_id"] and chat._idle(item["session"]["project"]))
        columns = [column["name"] for column in restored["result"]["columns"]]
        assert "total_amount" in columns and "avg_amount" not in columns
        totals = sorted(dict(zip(columns, row["values"], strict=True))["total_amount"] for row in restored["result"]["rows"])
        assert all(chat._equal(a, b) for a, b in zip(sorted(totals, key=float), ["30", "80", "140"], strict=True))
        expect(table).to_be_visible(timeout=30000)
        expect(table.locator("tbody tr")).to_have_count(3)
    chat._complete(page, live, chat_probe, restored)
