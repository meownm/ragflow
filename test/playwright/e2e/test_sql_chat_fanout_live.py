"""C24: two independent child relations must preserve actual order grain."""

from decimal import Decimal

from test.playwright.e2e import sql_chat_relation_fixture as relations
from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
real_sql_catalog = relations.real_sql_catalog
chat_probe = chat.chat_probe


def test_chat_two_children_keep_order_amount_and_source_total(page, live, chat_probe, real_sql_catalog):
    scope = chat_probe["fqn"].rsplit(".", 1)[0]
    chat._start(
        page,
        live,
        chat_probe,
        f"Основная таблица {chat_probe['fqn']}; связанные {scope}.order_items и {scope}.payments разрешены. "
        "Покажи оплаченный заказ id=101 за календарный сентябрь 2026 UTC по created_at. "
        "Одна строка на заказ: id, amount — сумма самого заказа, item_count — количество его позиций, "
        "payment_count — количество его оплат. Выходные имена строго id, amount, item_count, payment_count в этом порядке. "
        "Не теряй заказы без позиций или оплат. Только чтение, сортировка id ASC, LIMIT 100.",
    )
    first = chat._rows(page, live, chat_probe, [[101, 100, 2, 2]], ["id", "amount", "item_count", "payment_count"])
    assert real_sql_catalog["golden_oracle"]["total"] == (490 if real_sql_catalog["calendar_discriminator"] else 240)
    schema = real_sql_catalog["schema"]
    # Independent correlated counts do not multiply one child by the other.
    oracle = live.sql(
        "SELECT json_build_object('rows',json_agg(json_build_array(id,amount,item_count,payment_count) ORDER BY id)) FROM ("
        f"SELECT o.id,o.amount,(SELECT count(*) FROM {schema}.order_items i WHERE i.order_id=o.id) item_count,"
        f"(SELECT count(*) FROM {schema}.payments p WHERE p.order_id=o.id) payment_count FROM {schema}.regression_orders o "
        "WHERE o.status='paid' AND o.created_at >= '2026-09-01T00:00:00Z'::timestamptz "
        "AND o.created_at < '2026-10-01T00:00:00Z'::timestamptz) counted;"
    )
    assert oracle["rows"][0] == [101, 100, 2, 2]
    assert sum(Decimal(str(row[1])) for row in oracle["rows"]) == Decimal(str(real_sql_catalog["golden_oracle"]["total"]))
    chat._send(
        page, chat_probe, "Теперь убери только ограничение id=101. Покажи все оплаченные заказы того же периода, по одной строке на заказ с теми же полями и счётчиками. Остальные условия сохрани."
    )
    all_orders = chat._rows(page, live, chat_probe, oracle["rows"], ["id", "amount", "item_count", "payment_count"])
    assert chat._identity(all_orders) != chat._identity(first)
    chat._send(
        page,
        chat_probe,
        "Теперь измени SQL на общий итог SUM(amount) самих заказов без DISTINCT по всему источнику с теми же paid и периодом. Одна строка, одна колонка total. Не суммируй ограниченный сохранённый набор и не размножай сумму количеством позиций или оплат.",
    )
    total = chat._rows(page, live, chat_probe, [[real_sql_catalog["golden_oracle"]["total"]]], ["total"])
    assert chat._identity(total) != chat._identity(all_orders)
    historical = live.api("GET", chat.SESSIONS + "/" + chat_probe["id"] + "/results/" + first["result"]["run_id"])
    assert chat._identity({"result": historical}) == chat._identity(first)
    assert historical["rows"] == first["result"]["rows"]
    chat._complete(page, live, chat_probe, total)
