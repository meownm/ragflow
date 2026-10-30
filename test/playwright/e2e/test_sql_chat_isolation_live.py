"""C08 checks source-column identity, not an unspecified output alias.

The original c121 test overconstrained the new conversation's header names.
This replacement retains all its isolation/restore assertions and additionally
checks the physical SQL projection and the absence of an inherited WHERE.
"""

from sqlglot import exp, parse_one

from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
real_sql_catalog = native.real_sql_catalog
chat_probe = chat.chat_probe


def _all_orders(value, schema):
    result = value["result"]
    if not result or not chat._idle(value["session"]["project"]):
        return False
    names = [column["name"] for column in result["columns"]]
    if not chat._matches(result, names, chat.ALL):
        return False
    statement = parse_one(result["sql"], dialect="postgres")
    columns = [item.this if isinstance(item, exp.Alias) else item for item in statement.expressions]
    tables = list(statement.find_all(exp.Table))
    return (
        all(isinstance(item, exp.Column) for item in columns)
        and [item.name for item in columns] == chat.COLUMNS
        and statement.args.get("where") is None
        and len(tables) == 1
        and tables[0].db == schema
        and tables[0].name == "regression_orders"
    )


def test_chat_new_conversation_does_not_inherit_previous_filters(page, live, chat_probe):
    chat._start(page, live, chat_probe)
    chat._rows(page, live, chat_probe)
    chat._send(page, chat_probe, "Оставь только amount не меньше 10.")
    first = chat._rows(page, live, chat_probe, [chat.PAID[0]])
    first_id = chat_probe["id"]
    chat._start(page, live, chat_probe, f"В {chat_probe['fqn']} покажи все заказы без фильтров: id, status, amount; сортировка id ASC, LIMIT 100. Только чтение.")
    assert chat_probe["id"] != first_id
    second_id = chat_probe["id"]
    ready = chat._wait(page, live, chat_probe, lambda value: _all_orders(value, chat_probe["schema"]))
    names = [column["name"] for column in ready["result"]["columns"]]
    second = chat._rows(page, live, chat_probe, chat.ALL, names)
    assert second["result"]["run_id"] != first["result"]["run_id"]
    chat_probe["id"] = first_id
    live.browser(page, "/sql-assistant?session=" + first_id)
    restored = chat._rows(page, live, chat_probe, [chat.PAID[0]])
    assert chat._identity(restored) == chat._identity(first)
    live.proof(
        chat_probe["proof_id"] + "-isolation",
        {
            "first_session": first_id,
            "second_session": second_id,
            "first_identity": chat._identity(first),
            "second_identity": chat._identity(second),
            "second_columns": second["result"]["columns"],
            "second_rows": second["result"]["rows"],
            "physical_projection": chat.COLUMNS,
            "second_has_no_where": True,
            "first_restored_unchanged": True,
        },
    )
    chat._complete(page, live, chat_probe, restored)
