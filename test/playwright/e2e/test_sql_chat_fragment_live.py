"""C23: a saved two-row subtotal cannot masquerade as a full source total."""

import re
from decimal import Decimal

from playwright.sync_api import expect

from test.playwright.e2e import test_sql_chat_calendar_live as calendar
from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
real_sql_catalog = calendar.real_sql_catalog
chat_probe = chat.chat_probe


def test_chat_saved_fragment_and_full_source_totals_have_distinct_scope(page, live, chat_probe, real_sql_catalog):
    question = calendar._question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC").replace("LIMIT 100", "LIMIT 2")
    chat._start(page, live, chat_probe, question)
    fragment = chat._rows(page, live, chat_probe, [[101, 100], [102, 100]], ["id", "amount"])
    assert fragment["result"]["source_completeness"] == "UNKNOWN"
    known = {message["id"] for message in fragment["session"]["messages"]}
    chat._send(page, chat_probe, "Какова сумма amount только двух уже показанных строк? Новый SQL не запускай. Это итог видимого фрагмента.")
    subtotal = chat._wait(page, live, chat_probe, lambda value: any(message["id"] not in known and message.get("answer", {}).get("status") == "READY" for message in value["session"]["messages"]))
    answer = next(message["answer"] for message in subtotal["session"]["messages"] if message["id"] not in known and message.get("answer", {}).get("status") == "READY")
    numbers = re.findall(r"[+-]?\d+(?:[,.]\d+)?", answer["text"])
    assert numbers and Decimal(numbers[-1].replace(",", ".")) == Decimal("200.00")
    assert answer["calculation"]["operation"] == "sum"
    assert re.search("сохран|показан|фрагмент", answer["text"], re.IGNORECASE), "The subtotal must declare its saved-data scope"
    expect(chat._root(page)).to_contain_text(answer["text"])
    assert chat._identity(subtotal) == chat._identity(fragment)
    assert {run["id"] for run in subtotal["session"]["project"]["runs"]} == {run["id"] for run in fragment["session"]["project"]["runs"]}
    chat._send(
        page,
        chat_probe,
        "Теперь посчитай полный итог SUM(amount) без DISTINCT по всему источнику за тот же сентябрь 2026 UTC, только paid. "
        "Ограничение первых двух заказов убери. Новый SQL разрешён и нужен: одна выходная колонка total, одна строка без GROUP BY. Источник и остальные условия сохрани.",
    )
    expected = 490 if real_sql_catalog["calendar_discriminator"] else 240
    full = chat._rows(page, live, chat_probe, [[expected]], ["total"])
    assert full["result"]["run_id"] != fragment["result"]["run_id"]
    historical = live.api("GET", chat.SESSIONS + "/" + chat_probe["id"] + "/results/" + fragment["result"]["run_id"])
    assert chat._identity({"result": historical}) == chat._identity(fragment)
    assert chat._matches(historical, ["id", "amount"], [[101, 100], [102, 100]])
    live.proof(
        chat_probe["proof_id"] + "-fragment-scope",
        {"fragment_identity": chat._identity(fragment), "saved_subtotal": 200, "saved_answer": answer, "full_identity": chat._identity(full), "full_total": expected, "old_fragment_unchanged": True},
    )
    chat._complete(page, live, chat_probe, full)
