"""Browser-authored acceptance for the single-composer SQL chat.

Run through live_ragflow/runner.py with a matching frontend, qwen3.8, worker and
real OpenMetadata. No conversation command or result is manufactured through API.
The imported fixture owns three synthetic orders in the disposable QA stack.
"""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import re
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import expect
from sqlglot import exp, parse_one

from test.playwright.e2e import test_workbench_integrations_live as native_live

live = native_live.live
real_sql_catalog = native_live.real_sql_catalog

SESSIONS = "/api/v1/sql-assistant/sessions"
PAID = [[1, "paid", "12.50"], [3, "paid", "7.25"]]
ALL = [[1, "paid", "12.50"], [2, "cancelled", "99.00"], [3, "paid", "7.25"]]
COLUMNS = ["id", "status", "amount"]
TIMEOUT_SECONDS = 480


@pytest.fixture
def chat_probe(live, real_sql_catalog):
    navigation = live.api("GET", "/api/v1/admin/navigation")
    visible = navigation["visible_sections"]
    if "sql_assistant" not in visible:
        # Fixture setup only, inside the runner's disposable application.
        live.api("PUT", "/api/v1/admin/navigation", {"visible_sections": [*visible, "sql_assistant"]})
    probe = {"fqn": real_sql_catalog["table"]["fullyQualifiedName"], "schema": real_sql_catalog["schema"], "jobs": set(), "turns": [], "observations": [], "proof_id": "chat-" + uuid4().hex}
    try:
        yield probe
    finally:
        # Preserve partial progress too; a failed oracle must remain diagnosable.
        live.proof(
            probe["proof_id"] + "-progress",
            {
                "session_id": probe.get("id"),
                "turns": probe["turns"],
                "observations": probe["observations"],
                "last_observed": probe.get("last_observed"),
                "last_error_code": probe.get("last_error_code"),
                "failure_snapshot": probe.get("failure_snapshot"),
                "jobs": sorted(probe["jobs"]),
            },
        )


def _clarification_question(value):
    turn = value["session"]["conversation"]
    if turn.get("question"):
        return turn["question"]["text"]
    pending = turn.get("pending") or {}
    assert pending.get("kind") == "ROUTER", "The waiting turn must expose its actual clarification"
    message = next(item for item in value["session"]["messages"] if item["id"] == pending["message_id"])
    answer = message["answer"]
    assert answer["status"] == "READY" and answer["kind"] == "CLARIFICATION"
    return answer["text"]


def _root(page):
    root = page.get_by_test_id("sql-assistant")
    expect(root).to_be_visible()
    return root


def _send(page, probe, text):
    root = _root(page)
    composer = root.locator("textarea:visible")
    expect(composer).to_have_count(1)
    # Automatic intent routing is part of the new product contract.
    expect(root.get_by_role("radio", name="Изменить запрос", exact=True)).to_have_count(0)
    expect(root.get_by_role("radio", name="Спросить о результате", exact=True)).to_have_count(0)
    composer.fill(text)
    form = composer.locator("xpath=ancestor::form[1]")
    submit = form.locator('button[type="submit"]')
    expect(submit).to_have_count(1)
    expect(submit).to_be_enabled()
    probe["turns"].append({"text": text, "session_id": probe.get("id"), "started_monotonic": time.monotonic()})
    submit.click()


def _start(page, live, probe, question=None):
    live.browser(page, "/sql-assistant")
    _send(
        page,
        probe,
        question
        or (
            f"Используй только {probe['fqn']}. Покажи заказы со status = paid, "
            "поля id, status, amount в этом порядке, сортировка id ASC, LIMIT 100. "
            "Одна строка — один заказ. Других условий нет. Только чтение."
        ),
    )
    expect(page).to_have_url(re.compile(r"/sql-assistant\?[^#]*session=[a-f0-9]{32}"), timeout=30000)
    probe["id"] = parse_qs(urlsplit(page.url).query)["session"][0]
    probe["turns"][-1]["session_id"] = probe["id"]
    probe["url"] = page.url


def _snapshot(live, probe):
    session = live.api("GET", SESSIONS + "/" + probe["id"])
    project = session["project"]
    job = project.get("current_job")
    if job:
        probe["jobs"].add(job["id"])
    conversation = session.get("conversation") or {}
    error = project.get("last_error") or (conversation.get("error") if conversation.get("phase") == "ERROR" else None)
    probe["last_error_code"] = error.get("code", "UNKNOWN") if isinstance(error, dict) else "UNKNOWN" if error else None
    if error:
        probe["failure_snapshot"] = session
    assert not error, "Real chat worker failed: " + str(probe["last_error_code"])
    result = None
    if session.get("selected_result_id"):
        result = live.api("GET", SESSIONS + "/" + probe["id"] + "/results/" + session["selected_result_id"])
    probe["last_observed"] = {
        "session_id": session["id"],
        "selected_result_id": session.get("selected_result_id"),
        "operation_state": project.get("operation_state"),
        "identity": _identity({"result": result}) if result else None,
        "rows": deepcopy(result["rows"]) if result else None,
        "view": deepcopy(result["view"]) if result else None,
    }
    return {"session": session, "result": result}


def _wait(page, live, probe, predicate):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        value = _snapshot(live, probe)
        saved_texts = {message["text"] for message in value["session"]["messages"]}
        prompts_saved = all(turn["text"] in saved_texts for turn in probe["turns"] if turn.get("session_id") == probe["id"])
        if predicate(value) and prompts_saved:
            probe["turns"][-1].setdefault("outcome_seconds", round(time.monotonic() - probe["turns"][-1]["started_monotonic"], 3))
            result = value["result"]
            probe["observations"].append(
                {
                    "observed_at": datetime.now(UTC).isoformat(),
                    "session_id": value["session"]["id"],
                    "turn": len(probe["turns"]),
                    "selected_result_id": value["session"]["selected_result_id"],
                    "query_artifact_id": value["session"]["project"]["artifact_ids"].get("query"),
                    "identity": _identity(value) if result else None,
                    "view": deepcopy(result["view"]) if result else None,
                    "rows": deepcopy(result["rows"]) if result else None,
                    "message_ids": [message["id"] for message in value["session"]["messages"]],
                    "structured_query": deepcopy(value["session"].get("structured_query")),
                }
            )
            return value
        # Condition polling, not a fixed guess about model latency.
        page.wait_for_timeout(500)
    raise AssertionError("Live chat did not reach the authored outcome within 480 seconds")


def _equal(actual, expected):
    if expected is None:
        return actual is None
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    try:
        return Decimal(str(actual).replace(",", ".")) == Decimal(str(expected))
    except InvalidOperation:
        return actual == expected


def _matches(result, columns, rows):
    if not result or [column["name"] for column in result["columns"]] != columns:
        return False
    values = [row["values"] for row in result["rows"]]
    return len(values) == len(rows) and all(len(actual) == len(expected) and all(_equal(a, e) for a, e in zip(actual, expected, strict=True)) for actual, expected in zip(values, rows, strict=True))


def _source_projection_matches(result, schema):
    if not result:
        return False
    statement = parse_one(result["sql"], dialect="postgres")
    columns = [item.this if isinstance(item, exp.Alias) else item for item in statement.expressions]
    tables = list(statement.find_all(exp.Table))
    return (
        all(isinstance(item, exp.Column) for item in columns) and [item.name for item in columns] == COLUMNS and len(tables) == 1 and tables[0].db == schema and tables[0].name == "regression_orders"
    )


def _rows(page, live, probe, rows=PAID, columns=COLUMNS):
    def matches(item):
        result = item["result"]
        names = columns
        if names is None:
            # A plain question names source fields, without prescribing AS labels.
            if not _source_projection_matches(result, probe["schema"]):
                return False
            names = [column["name"] for column in result["columns"]]
        return _matches(result, names, rows) and _idle(item["session"]["project"])

    value = _wait(page, live, probe, matches)
    table = _root(page).get_by_role("table", name="Данные выбранного результата", exact=True)
    expect(table).to_be_visible(timeout=30000)
    rendered = table.locator("tbody tr")
    expect(rendered).to_have_count(len(rows))
    for index, expected in enumerate(rows):
        cells = rendered.nth(index).locator("td")
        # The existing result renderer has an additional drill action column.
        for position, expected_cell in enumerate(expected):
            expect(cells.nth(position)).to_contain_text(re.compile(re.escape(str(expected_cell)).replace(r"\.", "[.,]")))
    screenshot = probe["proof_id"] + "-state-" + str(len(probe["observations"])) + ".png"
    page.screenshot(path=str(live.out / screenshot), full_page=True)
    probe["observations"][-1]["screenshot"] = screenshot
    return value


def _identity(value):
    result = value["result"]
    return {key: deepcopy(result[key]) for key in ("run_id", "sql_hash", "sql", "parameters")}


def _idle(project):
    # Independent observation of the wire protocol, including retained terminal
    # job metadata. This is not the application's transition-policy function.
    return project["operation_state"] != "RUNNING" and (project.get("current_job") or {}).get("status") not in {"PENDING", "RETRY", "RUNNING"}


def _complete(page, live, probe, value):
    expect(_root(page).locator("textarea:visible")).to_have_count(1)
    expect(_root(page).get_by_role("radio", name="Изменить запрос", exact=True)).to_have_count(0)
    expect(_root(page).get_by_role("radio", name="Спросить о результате", exact=True)).to_have_count(0)
    page.screenshot(path=str(live.out / (probe["proof_id"] + ".png")), full_page=True)
    # This reads the real proxy's upstream audit, including cache/dedup flags.
    inference = live.inference([probe["fqn"]], 1)
    tables = parse_one(value["result"]["sql"], dialect="postgres").find_all(exp.Table)
    assert any(table.db == probe["schema"] and table.name == "regression_orders" for table in tables), "Result SQL must read the owned fixture, not fabricated constants"
    claims = live.worker_claims(sorted(probe["jobs"])) if probe["jobs"] else []
    live.proof(
        probe["proof_id"],
        {
            "scope": "single-composer browser acceptance; synthetic data only",
            "session_id": probe["id"],
            "turns": probe["turns"],
            "observations": probe["observations"],
            "selected_result_id": value["session"]["selected_result_id"],
            "query_artifact_id": value["session"]["project"]["artifact_ids"]["query"],
            "identity": _identity(value),
            "view": value["result"]["view"],
            "structured_query": value["session"].get("structured_query"),
            "columns": value["result"]["columns"],
            "rows": value["result"]["rows"],
            "upstream_transactions": inference,
            "worker_claims": claims,
        },
    )


def test_chat_fast_answer_and_server_restore_in_fresh_context(page, live, chat_probe):
    """C01/C05: no staged API setup of the question, then real server restore."""
    _start(page, live, chat_probe, f"Покажи оплаченные заказы из {chat_probe['fqn']}: id, status, amount. Отсортируй по id по возрастанию.")
    before = _rows(page, live, chat_probe, columns=None)
    page.reload(wait_until="domcontentloaded")
    assert _identity(_rows(page, live, chat_probe, columns=None)) == _identity(before)
    context = page.context.browser.new_context()
    errors = []
    try:
        restored = context.new_page()
        restored.on("pageerror", lambda error: errors.append(str(error)))
        # Only authentication is injected. No result, messages or analysis state.
        live.browser(restored, "/sql-assistant?session=" + chat_probe["id"])
        after = _rows(restored, live, chat_probe, columns=None)
        assert _identity(after) == _identity(before)
        assert after["result"]["view"] == before["result"]["view"]
        assert [message["id"] for message in after["session"]["messages"]] == [message["id"] for message in before["session"]["messages"]]
        _send(restored, chat_probe, "Теперь оставь только заказы с amount не меньше 10.")
        after = _rows(restored, live, chat_probe, [PAID[0]], columns=None)
        _complete(restored, live, chat_probe, after)
        assert not errors
    finally:
        context.close()


def test_chat_clarifies_undefined_metric_before_running(page, live, chat_probe):
    """C02: ambiguity produces a question, not a fabricated number."""
    _start(page, live, chat_probe, f"Покажи итог по таблице {chat_probe['fqn']}.")
    value = _wait(page, live, chat_probe, lambda item: bool(item["session"]["project"].get("pending_proposal")))
    assert value["session"]["selected_result_id"] is None
    assert not value["session"]["project"].get("runs")
    proposal = value["session"]["project"]["pending_proposal"]["payload"]["agent_result"]
    questions = proposal.get("proposal", {}).get("questions")
    assert questions, "Undefined metric must be clarified"
    assert any(re.search("итог|сумм|количеств|показател", question["question"], re.IGNORECASE) for question in questions)
    active_question = _clarification_question(value)
    assert active_question in {question["question"] for question in questions}
    expect(_root(page)).to_contain_text(active_question)
    _send(page, chat_probe, "Итог — сумма amount только для status = paid за все имеющиеся строки. Одна колонка total, без DISTINCT, без JOIN.")
    value = _rows(page, live, chat_probe, [["19.75"]], ["total"])
    _complete(page, live, chat_probe, value)


def test_chat_filter_replacement_and_removal_keep_paid_scope(page, live, chat_probe):
    """C03: inherited status excludes the 99.00 cancelled order on every turn."""
    _start(page, live, chat_probe)
    _rows(page, live, chat_probe)
    for text, expected in (
        ("Оставь только заказы с amount не меньше 10.", [PAID[0]]),
        ("Вместо предыдущего порога оставь amount меньше 10.", [PAID[1]]),
        ("Убери только ограничение по amount.", PAID),
    ):
        _send(page, chat_probe, text)
        value = _rows(page, live, chat_probe, expected)
    _complete(page, live, chat_probe, value)


def test_chat_explicit_source_limit_requires_a_choice(page, live, chat_probe):
    """A source cap cannot silently replace the user's explicit output limit."""
    _start(
        page,
        live,
        chat_probe,
        f"Используй только {chat_probe['fqn']}. Покажи заказы со status = paid: id, status, amount; "
        "сортировка id ASC. Лимит выдачи 200 строк. Одна строка — один заказ, других условий нет. Только чтение.",
    )
    waiting = _wait(page, live, chat_probe, lambda item: item["session"]["conversation"]["phase"] == "WAITING")
    assert not waiting["session"]["project"].get("runs")
    question = _clarification_question(waiting)
    assert re.search(r"(?<!\d)100(?!\d)", question), "Must explain the actual source cap"
    expect(_root(page)).to_contain_text(question)
    _send(page, chat_probe, "Используй лимит 100 строк, остальные условия сохрани.")
    value = _rows(page, live, chat_probe)
    assert value["result"]["parameters"]["row_limit"] == 100
    _complete(page, live, chat_probe, value)


def test_chat_answer_uses_saved_rows_without_new_sql_run(page, live, chat_probe):
    """C04: ordinary prose question has a numerical oracle and no SQL side effect."""
    _start(page, live, chat_probe)
    before = _rows(page, live, chat_probe)
    known = {message["id"] for message in before["session"]["messages"]}
    _send(page, chat_probe, "Какова общая сумма amount в уже показанных строках? Новый SQL не запускай.")
    value = _wait(page, live, chat_probe, lambda item: any(message["id"] not in known and message.get("answer", {}).get("status") == "READY" for message in item["session"]["messages"]))
    answer = next(message["answer"] for message in value["session"]["messages"] if message["id"] not in known and message.get("answer", {}).get("status") == "READY")
    assert re.search(r"(?<![\d.])19[,.]75(?!\d)", answer["text"])
    expect(_root(page)).to_contain_text(re.compile(r"19[,.]75"))
    assert _identity(value) == _identity(before)
    assert [run["id"] for run in value["session"]["project"].get("runs", [])] == [run["id"] for run in before["session"]["project"].get("runs", [])]
    _complete(page, live, chat_probe, value)


def test_chat_group_and_drill_keep_selected_status(page, live, chat_probe):
    """C06: grouped paid total 19.75 drills back to the same two orders."""
    _start(page, live, chat_probe)
    _rows(page, live, chat_probe)
    _send(page, chat_probe, "Сгруппируй текущую выборку по status. Колонки status и total = SUM(amount), сортировка status ASC.")
    grouped = _rows(page, live, chat_probe, [["paid", "19.75"]], ["status", "total"])
    sql = _root(page).get_by_label("SQL выбранного результата", exact=True)
    expect(sql).to_have_count(1)
    sql.locator("summary").first.click()
    expect(sql).to_contain_text(grouped["result"]["sql"])
    sql.locator("summary").first.click()
    expect(_root(page).get_by_text("Измерение для детализации", exact=True)).to_be_hidden()
    row_action = _root(page).get_by_role("table", name="Данные выбранного результата", exact=True).get_by_role("button", name="Уточнить по этой строке", exact=True)
    expect(row_action).to_be_enabled()
    row_action.focus()
    row_action.press("Enter")
    expect(_root(page).get_by_role("button", name="Убрать выбранную группу", exact=True)).to_be_visible()
    _send(page, chat_probe, "Для группы paid покажи исходные заказы: id, status, amount, сортировка id ASC.")
    value = _rows(page, live, chat_probe)
    _complete(page, live, chat_probe, value)


def test_chat_chart_changes_view_without_reexecuting_query(page, live, chat_probe):
    """C07: representation changes, underlying data and query identity do not."""
    _start(page, live, chat_probe, f"В {chat_probe['fqn']} по всем строкам посчитай SUM(amount) AS total по status. Колонки status, total, сортировка status ASC. Только чтение.")
    before = _rows(page, live, chat_probe, [["cancelled", "99.00"], ["paid", "19.75"]], ["status", "total"])
    assert before["result"]["view"].get("chart") is None
    _send(page, chat_probe, "Покажи текущий результат столбчатым графиком: status по X, total по Y.")
    value = _wait(page, live, chat_probe, lambda item: bool(item["result"] and item["result"]["view"].get("chart")) and _idle(item["session"]["project"]))
    assert _identity(value) == _identity(before)
    assert value["result"]["rows"] == before["result"]["rows"]
    assert [run["id"] for run in value["session"]["project"].get("runs", [])] == [run["id"] for run in before["session"]["project"].get("runs", [])]
    expect(_root(page).locator(".recharts-surface")).to_be_visible(timeout=30000)
    page.reload(wait_until="domcontentloaded")
    expect(_root(page).locator(".recharts-surface")).to_be_visible(timeout=30000)
    restored = _snapshot(live, chat_probe)
    assert _identity(restored) == _identity(before)
    assert restored["result"]["view"] == value["result"]["view"]
    _complete(page, live, chat_probe, restored)


def _all_orders(value, schema):
    result = value["result"]
    if not result or not _idle(value["session"]["project"]):
        return False
    names = [column["name"] for column in result["columns"]]
    if not _matches(result, names, ALL):
        return False
    return _source_projection_matches(result, schema) and parse_one(result["sql"], dialect="postgres").args.get("where") is None


def test_chat_new_conversation_does_not_inherit_previous_filters(page, live, chat_probe):
    """C08: A is paid and >=10; B must still contain the cancelled order."""
    _start(page, live, chat_probe)
    _rows(page, live, chat_probe)
    _send(page, chat_probe, "Оставь только amount не меньше 10.")
    first = _rows(page, live, chat_probe, [PAID[0]])
    first_id = chat_probe["id"]
    _start(page, live, chat_probe, f"В {chat_probe['fqn']} покажи все заказы без фильтров: id, status, amount; сортировка id ASC, LIMIT 100. Только чтение.")
    assert chat_probe["id"] != first_id
    second_id = chat_probe["id"]
    ready = _wait(page, live, chat_probe, lambda value: _all_orders(value, chat_probe["schema"]))
    names = [column["name"] for column in ready["result"]["columns"]]
    value = _rows(page, live, chat_probe, ALL, names)
    assert value["result"]["run_id"] != first["result"]["run_id"]
    chat_probe["id"] = first_id
    live.browser(page, "/sql-assistant?session=" + first_id)
    restored = _rows(page, live, chat_probe, [PAID[0]])
    assert _identity(restored) == _identity(first)
    live.proof(
        chat_probe["proof_id"] + "-isolation",
        {
            "first_session": first_id,
            "second_session": second_id,
            "first_identity": _identity(first),
            "second_identity": _identity(value),
            "second_columns": value["result"]["columns"],
            "second_rows": value["result"]["rows"],
            "physical_projection": COLUMNS,
            "second_has_no_where": True,
            "first_restored_unchanged": True,
        },
    )
    _complete(page, live, chat_probe, restored)


def test_chat_lost_response_replays_one_delivery_after_result_changes(page, live, chat_probe):
    """C15: abort a real committed response; retry must not create another turn."""
    _start(page, live, chat_probe)
    _rows(page, live, chat_probe)
    question = "Теперь оставь только заказы с amount не меньше 10."
    endpoint = re.compile(r"/api/v1/sql-assistant/sessions/" + chat_probe["id"] + r"/commands(?:\?.*)?$")
    wires = []

    def delivery(route):
        body = route.request.post_data_json
        if body.get("type") == "message" and body.get("payload", {}).get("text") == question:
            wires.append(deepcopy(body))
            if len(wires) == 1:
                response = route.fetch()
                assert response.ok and response.json()["code"] == 0
                # The application accepted the real browser command. Only its
                # transport response is lost; no model/result is substituted.
                route.abort("failed")
                return
        route.continue_()

    page.route(endpoint, delivery)
    try:
        _send(page, chat_probe, question)
        accepted = _rows(page, live, chat_probe, [PAID[0]])
        revision = accepted["session"]["revision"]
        runs = [item["id"] for item in accepted["session"]["project"]["runs"]]
        retry = _root(page).get_by_role("button", name="Повторить отправку", exact=True)
        expect(retry).to_be_visible()
        retry.click()
        expect(_root(page).get_by_label("Сообщение SQL-помощнику")).to_have_value("", timeout=30000)
        after = _snapshot(live, chat_probe)
        assert len(wires) == 2 and wires[0] == wires[1]
        assert after["session"]["revision"] == revision
        assert [item["id"] for item in after["session"]["project"]["runs"]] == runs
        assert sum(message["text"] == question for message in after["session"]["messages"]) == 1
        assert _identity(after) == _identity(accepted)
        assert _idle(after["session"]["project"])
        live.proof(chat_probe["proof_id"] + "-delivery", {"same_wire": True, "client_request_id": wires[0]["client_request_id"], "revision": revision, "run_ids": runs})
        _complete(page, live, chat_probe, after)
    finally:
        page.unroute(endpoint, delivery)


def test_chat_registered_metric_binds_the_requested_parameter(page, live, chat_probe):
    """C02: registry setup is configuration; all dialogue/results come from UI+LLM."""
    from business_documents.sql_query.query_specification import parse_schema_snapshot

    _start(page, live, chat_probe)
    seeded = _rows(page, live, chat_probe)
    session = seeded["session"]
    snapshot = parse_schema_snapshot(session["project"]["artifacts"]["schema"]["schema_snapshot"])
    table = snapshot.tables[0]
    columns = {column.name: column.id for column in table.columns}

    def condition(name, column, value, value_type, operator):
        return {
            "id": name,
            "column_id": columns[column],
            "operator": operator,
            "parameter_name": name,
            "parameter_type": value_type,
            "parameter_value": value,
            "description": "Условие методики",
            "confirmed": True,
            "decision": "user",
        }

    title = "Оплаченная сумма"
    query = {
        "base_entity_id": table.id,
        "aliases": {table.id: "o"},
        "joins": [],
        "select": [{"id": "total", "kind": "sum", "column_id": columns["amount"], "alias": "total", "grain": None}],
        "filters": [condition("paid_status", "status", "paid", "text", "eq"), condition("minimum_amount", "amount", "0", "decimal", "gte")],
        "row_filter_tree": {"operator": "AND", "children": [{"filter_id": "paid_status"}, {"filter_id": "minimum_amount"}]},
        "aggregate_filters": [],
        "aggregate_filter_tree": None,
        "order_by": [{"select_item_id": "total", "direction": "ASC"}],
        "row_limit": 100,
    }
    # Synthetic admin publishes only registry metadata, never a conversation
    # answer or a fabricated Run. Its default 0 deliberately differs from 10.
    published_response = live.response(
        "POST",
        SESSIONS + "/" + session["id"] + "/commands",
        {
            "client_request_id": "fixture-definition-" + uuid4().hex,
            "expected_revision": session["revision"],
            "expected_project_version": session["project"]["state_version"],
            "type": "publish_definition",
            "payload": {
                "definition_id": None,
                "expected_version": 0,
                "title": title,
                "description": "Сумма amount оплаченных заказов с минимальным порогом",
                "query": query,
                "base_query_artifact_id": session["project"]["artifact_ids"]["query"],
                "contract": {
                    "grain": [],
                    "metrics": [{"select_item_id": "total", "unit": "RUB", "null_policy": "IGNORE_NULLS_NULL_IF_EMPTY", "reaggregation": "FORBIDDEN"}],
                    "time_column_id": None,
                    "timezone": "UTC",
                    "mutable_parameters": ["minimum_amount"],
                },
            },
        },
    )
    published_body = published_response.json()
    assert published_response.status_code == 200 and published_body.get("code") == 0, (
        "Synthetic definition registry setup failed: " + str(published_response.status_code) + " " + str(published_body)[:3000]
    )
    published = published_body["data"]["effect"]["definition"]
    _start(
        page,
        live,
        chat_probe,
        f"Рассчитай показатель {title}. Источник только {chat_probe['fqn']}. Сумма amount только для status = paid и amount не меньше 10. Одна строка, одна колонка total. Без JOIN и DISTINCT.",
    )
    waiting = _wait(page, live, chat_probe, lambda value: ((value["session"].get("conversation") or {}).get("pending") or {}).get("kind") == "METRIC")
    assert not waiting["session"]["project"]["runs"]
    chat_probe["turns"].append({"text": title, "session_id": chat_probe["id"], "started_monotonic": time.monotonic()})
    _root(page).get_by_role("button", name=title, exact=True).click()
    bound = _wait(
        page,
        live,
        chat_probe,
        lambda value: value["session"].get("selected_result_id") is not None or (((value["session"].get("conversation") or {}).get("pending") or {}).get("kind") == "DEFINITION_CONFIRM"),
    )
    pending = (bound["session"].get("conversation") or {}).get("pending") or {}
    if pending.get("kind") == "DEFINITION_CONFIRM":
        assert Decimal(str(pending["plan"]["parameters"]["minimum_amount"]["value"])) == Decimal("10")
        chat_probe["turns"].append({"text": "Да, выполнить", "session_id": chat_probe["id"], "started_monotonic": time.monotonic()})
        _root(page).get_by_role("button", name="Да, выполнить", exact=True).click()
    result = _rows(page, live, chat_probe, [["12.50"]], ["total"])
    assert result["session"]["definition_status"] == "DEFINITION_VALIDATED"
    assert Decimal(str(result["result"]["parameters"]["minimum_amount"])) == Decimal("10")
    assert len(result["session"]["project"]["runs"]) == 1
    live.proof(
        chat_probe["proof_id"] + "-metric-binding",
        {"definition_id": published["definition_id"], "version": published["version"], "parameters": result["result"]["parameters"], "expected_total": "12.50"},
    )
    _complete(page, live, chat_probe, result)
