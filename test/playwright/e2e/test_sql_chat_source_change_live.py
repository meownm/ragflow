"""C17: changing an actual bound QA source preserves historical lineage."""

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
import re

from sqlglot import exp, parse_one

from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

from test.playwright.e2e import sql_chat_relation_fixture as catalog_fixture
from test.playwright.e2e import test_sql_chat_calendar_live as calendar

live = native.live
real_sql_catalog = catalog_fixture.real_sql_catalog
chat_probe = chat.chat_probe


def _export(live, session_id, run_id):
    response = live.response("POST", native.PREFIX + "/projects/" + session_id + "/export", {"schema_version": "1", "kind": "RESULT", "format": "json", "run_id": run_id})
    assert response.status_code == 200
    return response.json()


def test_chat_source_change_keeps_the_old_result_on_its_original_profile(page, live, chat_probe, real_sql_catalog):
    chat._start(page, live, chat_probe, calendar._question(chat_probe, "календарный сентябрь 2026, часовой пояс UTC"))
    original = chat._rows(page, live, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
    original_packet = _export(live, chat_probe["id"], original["result"]["run_id"])
    assert original_packet["run"]["profile_id"] == real_sql_catalog["profile"]["id"]
    with contextmanager(catalog_fixture.real_sql_catalog.__wrapped__)(live, SimpleNamespace(param=real_sql_catalog["calendar_discriminator"])) as second:
        schema = second["schema"]
        assert re.fullmatch(r"qa_[0-9a-f]{16}", schema) and schema != real_sql_catalog["schema"]
        assert second["profile"]["id"] != real_sql_catalog["profile"]["id"]
        live.sql(
            f"TRUNCATE {schema}.regression_orders CASCADE; INSERT INTO {schema}.regression_orders (id,status,amount,created_at) VALUES (801,'paid',101.25,'2026-09-02T00:00:00Z'),(802,'cancelled',999.99,'2026-09-02T00:00:00Z'),(803,'paid',703.35,'2026-09-03T00:00:00Z'); SELECT json_build_object('distinct_source_created',true);"
        )
        fqn = second["table"]["fullyQualifiedName"]
        chat._send(
            page,
            chat_probe,
            f"Теперь используй только {fqn} вместо прежнего источника. Покажи оплаченные заказы status=paid. Сохрани календарный сентябрь 2026 UTC по created_at. Выходные имена строго id и amount в этом порядке, одна строка на заказ, id ASC, LIMIT 100. Других условий нет, только чтение.",
        )
        switched = chat._rows(page, live, chat_probe, [[801, "101.25"], [803, "703.35"]], ["id", "amount"])
        assert chat._identity(switched) != chat._identity(original)
        tables = list(parse_one(switched["result"]["sql"], dialect="postgres").find_all(exp.Table))
        assert len(tables) == 1 and tables[0].db == schema and tables[0].name == "regression_orders"
        second_packet = _export(live, chat_probe["id"], switched["result"]["run_id"])
        assert second_packet["run"]["profile_id"] == second["profile"]["id"]
        assert second["table"]["id"] in second_packet["source"]["catalog_entity_ids"]
        assert real_sql_catalog["table"]["id"] not in second_packet["source"]["catalog_entity_ids"]
        history = chat._root(page).locator("details.sa-result-history")
        if history.get_attribute("open") is None:
            history.locator("summary").click()
        runs = switched["session"]["project"]["runs"]
        index = next(index for index, run in enumerate(runs) if run["id"] == original["result"]["run_id"])
        history.get_by_role("button", name=re.compile(r"^R" + str(len(runs) - index) + r"\s*·")).click()
        restored = chat._rows(page, live, chat_probe, calendar._september(real_sql_catalog), ["id", "amount"])
        assert chat._identity(restored) == chat._identity(original)
        restored_packet = _export(live, chat_probe["id"], restored["result"]["run_id"])
        for field in ("profile_id", "profile_version", "profile_name"):
            assert restored_packet["run"][field] == original_packet["run"][field]
        assert restored_packet["query"] == original_packet["query"]
        assert restored_packet["source"]["catalog_entity_ids"] == original_packet["source"]["catalog_entity_ids"]
        assert restored_packet["rows"] == original_packet["rows"]
        chat._send(page, chat_probe, "Оставь amount не меньше 50. Продолжай именно выбранный старый результат; остальные условия сохрани.")
        expected_original = [row for row in calendar._september(real_sql_catalog) if Decimal(str(row[1])) >= 50]
        continued = chat._rows(page, live, chat_probe, expected_original, ["id", "amount"])
        continued_packet = _export(live, chat_probe["id"], continued["result"]["run_id"])
        assert continued_packet["run"]["profile_id"] == real_sql_catalog["profile"]["id"]
        assert real_sql_catalog["table"]["id"] in continued_packet["source"]["catalog_entity_ids"]
        assert second["table"]["id"] not in continued_packet["source"]["catalog_entity_ids"]
        for saved, packet in ((original, original_packet), (switched, second_packet)):
            exported = _export(live, chat_probe["id"], saved["result"]["run_id"])
            assert exported["query"] == packet["query"] and exported["rows"] == packet["rows"]
            assert exported["source"]["catalog_entity_ids"] == packet["source"]["catalog_entity_ids"]
            for field in ("profile_id", "profile_version", "profile_name"):
                assert exported["run"][field] == packet["run"][field]
        # Include both own FQNs in the real upstream inventory; no model calls
        # are inferred from HTTP health or a fabricated result.
        first_calls = live.inference([chat_probe["fqn"]], minimum=1)
        second_calls = live.inference([fqn], minimum=1)
        live.proof(
            chat_probe["proof_id"] + "-source-change",
            {
                "first_profile_id": real_sql_catalog["profile"]["id"],
                "second_profile_id": second["profile"]["id"],
                "first_identity": chat._identity(original),
                "second_identity": chat._identity(switched),
                "restored_unchanged": True,
                "continued_from_original_identity": chat._identity(continued),
                "first_upstream": first_calls,
                "second_upstream": second_calls,
            },
        )
        chat._complete(page, live, chat_probe, continued)
