"""C11/C12 on real buyer/payer metadata and both complete golden datasets."""

from decimal import Decimal
import re

from playwright.sync_api import expect

from test.playwright.e2e import sql_chat_relation_fixture as relations
from test.playwright.e2e import test_sql_chat_calendar_live as calendar
from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
real_sql_catalog = relations.real_sql_catalog
chat_probe = chat.chat_probe


def _explanation(page, live, probe, before, text, required_words):
    known = {message["id"] for message in before["session"]["messages"]}
    chat._send(page, probe, text)
    after = chat._wait(page, live, probe, lambda value: any(message["id"] not in known and message.get("answer", {}).get("status") == "READY" for message in value["session"]["messages"]))
    answer = next(message["answer"] for message in after["session"]["messages"] if message["id"] not in known and message.get("answer", {}).get("status") == "READY")
    for required in required_words:
        assert re.search(required, answer["text"], re.IGNORECASE), "Explanation must answer the actual question"
    assert chat._identity(after) == chat._identity(before)
    assert after["result"]["rows"] == before["result"]["rows"] and after["result"]["view"] == before["result"]["view"]
    assert {run["id"] for run in after["session"]["project"]["runs"]} == {run["id"] for run in before["session"]["project"]["runs"]}
    expect(chat._root(page)).to_contain_text(answer["text"])
    return after


def _groups(page, live, probe, expected):
    def normalized(rows):
        values = [(row[0], Decimal(str(row[1]))) for row in rows]
        return sorted(values, key=lambda row: (row[0] is not None, row[0] or ""))

    def ready(value):
        result = value["result"]
        return (
            result
            and [column["name"] for column in result["columns"]] == ["buyer_region", "total"]
            and normalized([row["values"] for row in result["rows"]]) == normalized(expected)
            and chat._idle(value["session"]["project"])
        )

    value = chat._wait(page, live, probe, ready)
    table = chat._root(page).get_by_role("table", name="Данные выбранного результата", exact=True)
    expect(table.locator("tbody tr")).to_have_count(len(expected))
    # NULL, empty string and zero are compared as typed API values above.
    # Each real displayed total must also correspond to its persisted row.
    for index, row in enumerate(value["result"]["rows"]):
        cells = table.locator("tbody tr").nth(index).locator("td")
        if row["values"][0] is None:
            expect(cells.nth(0)).to_contain_text("NULL")
        elif row["values"][0]:
            expect(cells.nth(0)).to_contain_text(row["values"][0])
        expect(cells.nth(1)).to_contain_text(re.compile(re.escape(str(row["values"][1])).replace(r"\.", "[.,]")))
    return value


def test_chat_five_turns_explain_then_keep_buyer_grain(page, live, chat_probe, real_sql_catalog):
    question = calendar._question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC")
    question = question.replace("Используй только", "Основная таблица —").replace("Других условий нет, без JOIN", "Других условий нет; связанные таблицы того же каталога разрешены")
    chat._start(page, live, chat_probe, question)
    initial = chat._rows(page, live, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
    _explanation(page, live, chat_probe, initial, "Почему такой период? Поясни действующее условие и его основание, данные не меняй.", [r"сентябр|2026-09", "2026", r"UTC|\+00:00|Z\b"])
    chat._send(page, chat_probe, "Оставь amount не меньше 50. Остальные условия сохрани.")
    high = [[101, 100], [102, 100]] + ([[108, 50], [109, 50], [110, 150]] if real_sql_catalog["calendar_discriminator"] else [])
    chat._rows(page, live, chat_probe, high, ["id", "amount"])
    chat._send(
        page,
        chat_probe,
        "Сгруппируй по региону покупателя, не плательщика: связь customer_id, а не payer_id. "
        "Выходные имена buyer_region и total=SUM(amount) без DISTINCT. Не теряй заказы без покупателя; NULL и пустую строку не объединяй. Остальные условия сохрани.",
    )
    high_groups = [["North", 200]] + ([[None, 150], ["South", 100]] if real_sql_catalog["calendar_discriminator"] else [])
    grouped = _groups(page, live, chat_probe, high_groups)
    _explanation(page, live, chat_probe, grouped, "Поясни NULL в регионе. Не выдумывай причину по отсутствующим сведениям и не меняй данные.", [r"NULL|отсутств|неопредел"])
    chat._send(page, chat_probe, "Убери только порог по amount. Группировку по региону покупателя, paid, период UTC и поля сохрани.")
    expected = [[None, 190 if real_sql_catalog["calendar_discriminator"] else 40], ["", 0], ["North", 200]] + ([["South", 100]] if real_sql_catalog["calendar_discriminator"] else [])
    final = _groups(page, live, chat_probe, expected)
    chat._complete(page, live, chat_probe, final)


def test_chat_explicit_threshold_replacement_is_not_an_impossible_and(page, live, chat_probe, real_sql_catalog):
    chat._start(page, live, chat_probe, calendar._question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    chat._rows(page, live, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
    chat._send(page, chat_probe, "Оставь amount не меньше 50. Остальные условия сохрани.")
    high = [[101, 100], [102, 100]] + ([[108, 50], [109, 50], [110, 150]] if real_sql_catalog["calendar_discriminator"] else [])
    before = chat._rows(page, live, chat_probe, high, ["id", "amount"])
    chat._send(page, chat_probe, "Добавь ещё условие amount меньше 50.")

    def needs_clarification(value):
        assert chat._identity(value) == chat._identity(before), "An unresolved contradiction must not replace the confirmed result"
        assert {run["id"] for run in value["session"]["project"]["runs"]} == {run["id"] for run in before["session"]["project"]["runs"]}, "Do not run an impossible AND before clarifying replacement"
        return value["session"]["conversation"]["phase"] == "WAITING"

    waiting = chat._wait(page, live, chat_probe, needs_clarification)
    question = chat._clarification_question(waiting)
    assert re.search(r"замен|противореч|порог|сохран", question, re.IGNORECASE)
    expect(chat._root(page)).to_contain_text(question)
    chat._send(page, chat_probe, "Теперь amount меньше 50 вместо прежнего порога. Остальные условия сохрани.")
    low = chat._rows(page, live, chat_probe, [[105, 40], [107, 0]], ["id", "amount"])
    assert chat._identity(low) != chat._identity(before)
    historical = live.api("GET", chat.SESSIONS + "/" + chat_probe["id"] + "/results/" + before["result"]["run_id"])
    assert chat._identity({"result": historical}) == chat._identity(before)
    chat._complete(page, live, chat_probe, low)
