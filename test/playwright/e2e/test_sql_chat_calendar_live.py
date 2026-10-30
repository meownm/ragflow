"""C09/C10/C21: real chat on owned calendar boundary rows, two data sets.

Run with live_ragflow/runner.py against the frozen application candidate.
Only fixture setup writes PostgreSQL/OpenMetadata; all user actions use the SPA.
All scenarios preserve the complete eight-table golden schema and real FKs.
This external harness deliberately does not modify the frozen application source.
"""

import re

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
