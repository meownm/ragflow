"""C22: browser downloads of a historical run retain identity and typed values."""

from decimal import Decimal
import json
import re

from test.playwright.e2e import sql_chat_relation_fixture as relations
from test.playwright.e2e.test_sql_chat_relational_history_live import _groups
from test.playwright.e2e import test_sql_assistant_chat_live as chat
from test.playwright.e2e import test_workbench_integrations_live as native

live = native.live
real_sql_catalog = relations.real_sql_catalog
chat_probe = chat.chat_probe


def _download(page, live, probe, label, suffix):
    with page.expect_download() as pending:
        chat._root(page).get_by_role("button", name=label, exact=True).click()
    download = pending.value
    assert download.suggested_filename.endswith(".json")
    target = live.out / (probe["proof_id"] + suffix + ".json")
    download.save_as(target)
    return json.loads(target.read_text(encoding="utf-8"))


def _assert_query(packet, result):
    assert packet["sql"] == result["sql"]
    assert {item["name"]: item["value"] for item in packet["parameters"]} == result["parameters"]


def test_chat_historical_download_keeps_null_empty_zero_and_decimal(page, live, chat_probe, real_sql_catalog):
    scope = chat_probe["fqn"].rsplit(".", 1)[0]
    chat._start(
        page,
        live,
        chat_probe,
        f"Основная таблица {chat_probe['fqn']}; таблица {scope}.customers разрешена. "
        "За календарный сентябрь 2026 UTC по created_at, только status=paid, сгруппируй по региону покупателя customer_id, не плательщика payer_id. "
        "Заказы без покупателя сохрани; NULL и пустую строку не объединяй. Выходные имена buyer_region, total=SUM(amount) без DISTINCT. "
        "Одна строка на регион, сортировка buyer_region ASC NULLS FIRST, LIMIT 100. Только чтение.",
    )
    expected = [[None, 190 if real_sql_catalog["calendar_discriminator"] else 40], ["", 0], ["North", 200]] + ([["South", 100]] if real_sql_catalog["calendar_discriminator"] else [])
    original = _groups(page, live, chat_probe, expected)
    chat._send(page, chat_probe, "Оставь только заказы amount не меньше 50 до группировки. Остальные условия, связь покупателя, поля и группировку сохрани.")
    high = [["North", 200]] + ([[None, 150], ["South", 100]] if real_sql_catalog["calendar_discriminator"] else [])
    current = _groups(page, live, chat_probe, high)
    assert chat._identity(current) != chat._identity(original)
    history = chat._root(page).locator("details.sa-result-history")
    if history.get_attribute("open") is None:
        history.locator("summary").click()
    runs = current["session"]["project"]["runs"]
    index = next(index for index, run in enumerate(runs) if run["id"] == original["result"]["run_id"])
    history.get_by_role("button", name=re.compile(r"^R" + str(len(runs) - index) + r"\s*·")).click()
    restored = _groups(page, live, chat_probe, expected)
    assert chat._identity(restored) == chat._identity(original)
    result = restored["result"]
    packet = _download(page, live, chat_probe, "Скачать результат", "-historical-result-download")
    assert packet["format"] == "ragflow-sql-result-export" and packet["value_format"] == "typed-cells-v1" and packet["postgres_types_known"]
    assert packet["run"]["id"] == result["run_id"] != current["result"]["run_id"]
    assert packet["source"]["query"]["id"] == result["query_artifact_id"]
    assert packet["source"]["compilation"]["id"] == result["compilation_id"]
    assert not packet["source"]["is_current_query"]
    assert [column["name"] for column in packet["columns"]] == ["buyer_region", "total"]
    assert packet["run"]["row_count"] == len(expected)
    _assert_query(packet["query"], result)
    exported = {row[0]["value"]: row for row in packet["rows"]}
    assert set(exported) == {row[0] for row in expected}
    assert exported[None][0] == {"type": "null", "value": None}
    assert exported[""][0] == {"type": "string", "value": ""}
    assert exported[""][1]["type"] == "decimal" and Decimal(exported[""][1]["value"]) == 0
    for region, total in expected:
        assert exported[region][1]["type"] == "decimal"
        assert isinstance(exported[region][1]["value"], str) and Decimal(exported[region][1]["value"]) == Decimal(total)
    basis = chat._root(page).locator("details").filter(has=page.locator("summary", has_text=re.compile(r"^SQL и основания$")))
    if basis.get_attribute("open") is None:
        basis.locator(":scope > summary").click()
    query_packet = _download(page, live, chat_probe, "Скачать SQL и параметры", "-historical-query-download")
    assert query_packet["source"]["query"]["id"] == result["query_artifact_id"]
    assert query_packet["source"]["compilation"]["id"] == result["compilation_id"]
    _assert_query(query_packet, result)
    after = chat._wait(page, live, chat_probe, lambda value: chat._identity(value) == chat._identity(original))
    assert {run["id"] for run in after["session"]["project"]["runs"]} == {run["id"] for run in current["session"]["project"]["runs"]}
    chat._complete(page, live, chat_probe, after)
