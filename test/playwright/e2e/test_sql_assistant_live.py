"""Real assistant → qwen3.8 → OpenMetadata → read-only PostgreSQL bot.

Run only via the disposable live runner with QA_BOT_CONNECTOR_CONFIG injected
privately. The bot database is read only; session/profile state belongs to QA.
"""

from copy import deepcopy
from contextlib import closing
from datetime import datetime
from decimal import Decimal
import json
import os
import time
from uuid import uuid4
from zoneinfo import ZoneInfo
from pathlib import Path
import importlib.util

import psycopg2
import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_workbench_integrations_live import Live

PREFIX = "/api/v1/business-documents/sql-query"
ASSISTANT = "/api/v1/sql-assistant"
FQN = "docker_postgres_bot.bot.public.search_queries"
QUESTION = f"Используй только таблицу {FQN}. Посчитай число поисковых запросов по дням для created_at с 2026-08-01 включительно до 2026-09-01 не включительно. Одна строка — один день; группировка DATE_TRUNC day по created_at без изменения часового пояса. Поля day (день) и query_count (COUNT id). Сортировка day ASC, LIMIT 1000. Не выводи текст запроса, пользователей и tenant_id. Без JOIN и без изменения данных."
ORACLE_SQL = "SELECT DATE_TRUNC('day', created_at) AS day, COUNT(id) AS query_count FROM public.search_queries WHERE created_at >= %s AND created_at < %s GROUP BY DATE_TRUNC('day', created_at) ORDER BY day ASC LIMIT 1000"


def canonical(rows):
    return [[value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, Decimal) else value for value in row] for row in rows]


@pytest.fixture(scope="module")
def live(base_url):
    assert os.getenv("QA_DISPOSABLE_PROJECT", "").startswith("ragflow-t1-live-")
    assert os.environ.get("QA_BOT_CONNECTOR_CONFIG"), "bot connection must be privately injected by the task runner"
    value = Live(base_url.rstrip("/"))
    yield value
    value.http.close()


def test_sql_assistant_real_bot_model_worker_history_and_views(page, live):
    config = json.loads(os.environ["QA_BOT_CONNECTOR_CONFIG"])
    assert config["database"] == "bot"
    credential = config["credentials"]
    # Load the actual infrastructure adapter without bootstrapping the Quart app
    # in the host pytest process. The tested source file is mounted in QA too.
    spec = importlib.util.spec_from_file_location("assistant_postgres_probe", Path(__file__).resolve().parents[3] / "api/apps/business_documents/sql_query_postgres.py")
    postgres = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(postgres)
    probe_config = {**config, "host": "127.0.0.1" if config["host"] == "host.docker.internal" else config["host"]}
    probe = postgres.execute_postgres(
        probe_config,
        "SELECT current_setting(:setting) AS timezone LIMIT :row_limit",
        {"setting": "TimeZone", "row_limit": 1},
        timeout_ms=5000,
        max_rows=1,
        max_result_bytes=1000,
        timezone="Europe/Moscow",
    )
    assert probe["rows"] == [["Europe/Moscow"]]
    live.proof("assistant-timezone-probe", {"timezone": probe["rows"][0][0], "read_only": True})
    with closing(
        psycopg2.connect(
            host="127.0.0.1" if config["host"] == "host.docker.internal" else config["host"],
            port=config.get("port", 5432),
            dbname="bot",
            user=credential["username"],
            password=credential["password"],
            connect_timeout=10,
        )
    ) as connection:
        connection.set_session(readonly=True)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = 15000")
            cursor.execute("SHOW TimeZone")
            source_timezone = cursor.fetchone()[0]
            cursor.execute(ORACLE_SQL, ("2026-08-01", "2026-09-01"))
            expected = canonical(cursor.fetchall())
    assert expected, "The previous bot scenario period must contain real data"
    connector = live.api("POST", "/api/v1/connectors", {"name": "sql-assistant-bot-" + uuid4().hex, "source": "postgresql", "config": config})
    profile = live.api(
        "POST",
        PREFIX + "/execution-profiles",
        {
            "schema_version": "1",
            "name": "SQL assistant bot RO " + uuid4().hex,
            "connector_id": connector["id"],
            "dialect": "postgres",
            "allowed_schemas": ["public"],
            "statement_timeout_ms": 15000,
            "max_rows": 1000,
            "max_result_bytes": 5000000,
            "enabled": True,
        },
    )
    live.api(
        "POST",
        PREFIX + "/catalog-bindings",
        {"schema_version": "1", "catalog_service": "docker_postgres_bot", "catalog_database": "bot", "catalog_schema": "public", "execution_profile_id": profile["id"], "enabled": True},
    )
    visibility = live.api("GET", "/api/v1/admin/navigation")
    assert "sql_assistant" not in visibility["visible_sections"]
    live.api("PUT", "/api/v1/admin/navigation", {"visible_sections": [*visibility["visible_sections"], "sql_assistant"]})
    created = live.api("POST", ASSISTANT + "/sessions", {"question": QUESTION, "client_request_id": uuid4().hex})
    path = ASSISTANT + "/sessions/" + created["id"]
    jobs = []

    def fresh():
        return live.api("GET", path)

    def command(kind, payload=None):
        current = fresh()
        return live.api(
            "POST",
            path + "/commands",
            {"client_request_id": uuid4().hex, "expected_revision": current["revision"], "expected_project_version": current["project"]["state_version"], "type": kind, "payload": payload or {}},
        )

    def wait_proposal():
        deadline = time.monotonic() + 480
        while time.monotonic() < deadline:
            current = fresh()
            project = current["project"]
            assert not project.get("last_error"), "Actual SQL worker failed: " + str((project.get("last_error") or {}).get("code"))
            if project.get("current_job") and project["current_job"]["id"] not in jobs:
                jobs.append(project["current_job"]["id"])
            if project["operation_state"] == "REVIEW" and project.get("pending_proposal"):
                return current, project["pending_proposal"]["payload"]["agent_result"]
            time.sleep(2)
        raise AssertionError("Actual assistant worker timed out")

    current, result = wait_proposal()
    live.proof("assistant-requirements", {"session_id": created["id"], "agent_result": result})
    assert not result["proposal"].get("questions"), "Self-contained bot question unexpectedly required clarification; inspect the saved real proposal"
    command("decide", {"decision": "ACCEPT", "artifact_payload": {"answers": {}}})
    command("advance")
    current, result = wait_proposal()
    live.proof("assistant-schema", {"agent_result": result})
    resolution = next(item for item in result["resolutions"] if any(entity["fqn"] == FQN for entity in item["catalog_answer"].get("entities", [])))
    entity_id = next(entity["id"] for entity in resolution["catalog_answer"]["entities"] if entity["fqn"] == FQN)
    detail = live.api("POST", PREFIX + "/schema/entities", {"entity_ids": [entity_id], "locale": "ru"})["entities"][0]
    entity = detail["entity"]
    allowed = {
        "id",
        "fqn",
        "name",
        "display_name",
        "technical_name",
        "description",
        "version",
        "updated_at",
        "service",
        "database",
        "schema",
        "owners",
        "domains",
        "tags",
        "glossary_terms",
        "url",
        "matched_by",
        "column_count",
        "schema_loaded",
        "schema_fingerprint",
        "table_constraints",
    }
    selected = {key: value for key, value in entity.items() if key in allowed}
    selected.update(
        columns=[
            {
                **{key: value for key, value in column.items() if key in {"id", "name", "fqn", "data_type", "description", "constraint", "glossary_terms"}},
                "selected": column["name"] in {"id", "created_at"},
            }
            for column in entity["column_details"]
        ],
        loaded_column_count=len(entity["column_details"]),
        columns_truncated=False,
    )
    requirements = "\n".join("- " + item["statement"].strip() for item in current["project"]["artifacts"]["requirements"]["requirements"])
    snapshot = {
        "format": "ragflow-sql-schema-snapshot",
        "schema_version": "1",
        "status": "READY",
        "original_requirements": requirements,
        "source": {
            "type": "openmetadata",
            "catalog_snapshot_at": detail["freshness"]["snapshot_at"],
            "checked_at": detail["freshness"]["checked_at"],
            "stale": False,
            "retrieval": [detail["retrieval"]],
            "references": detail["sources"],
        },
        "requirements": [{"term": resolution["term"], "state": "CONFIRMED", "decision": "USER", "interpretation": resolution["interpretation"], "selected_table": selected}],
        "warnings": detail["warnings"],
    }
    command(
        "decide",
        {
            "decision": "ACCEPT",
            "artifact_payload": {"schema_snapshot": snapshot, "accepted_schema": [{"entity_id": entity_id, "version": entity["version"], "schema_fingerprint": entity["schema_fingerprint"]}]},
        },
    )
    command("advance")
    current, result = wait_proposal()
    live.proof("assistant-query-plan", {"agent_result": result})
    assert result["status"] == "PROPOSED"
    command(
        "decide",
        {
            "decision": "ACCEPT",
            "artifact_payload": {"confirmed_join_ids": [item["id"] for item in result["proposal"]["joins"]], "confirmed_filter_ids": [item["id"] for item in result["proposal"]["filters"]]},
        },
    )
    command("compile")
    preflight = live.api("GET", path + "/preflight")
    assert preflight["binding"]["status"] == "BOUND"
    command("execute", {"selected_profile_id": profile["id"]})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        current = fresh()
        run = current["project"].get("latest_run")
        if run and run["status"] == "READY":
            break
        assert not run or run["status"] not in {"FAILED", "EXPIRED", "PURGED"}, "Real SQL failed"
        time.sleep(2)
    assert run["status"] == "READY"
    command("select_result", {"run_id": run["id"]})
    actual = live.api("GET", path + "/results/" + run["id"])

    def normal_date(value):
        # The requested grain is a day; DATE and a midnight TIMESTAMP identify
        # the same bucket. Numeric values and bucket ordering remain exact.
        return str(value).replace(" ", "T")[:10]

    assert len(actual["rows"]) == len(expected)
    assert [(normal_date(row["values"][0]), int(row["values"][1])) for row in actual["rows"]] == [(normal_date(row[0]), int(row[1])) for row in expected]
    view = deepcopy(actual["view"])
    view["hidden"] = [actual["columns"][1]["id"]]
    command("update_view", {"run_id": run["id"], "expected_view_revision": 0, "view": view})
    after = live.api("GET", path + "/results/" + run["id"])
    assert (actual["sql_hash"], actual["run_id"], actual["rows"]) == (after["sql_hash"], after["run_id"], after["rows"])
    assert after["source_completeness"] == "UNKNOWN" and after["definition_status"] == "NOT_VALIDATED"

    # This approval exists only in the disposable application. It exercises the
    # administrator workflow; it does not certify a production bot business metric.
    current = fresh()
    query = deepcopy(current["project"]["artifacts"]["query"])
    assert "mode" not in query and len(query["select"]) == 2
    metric = next(item for item in query["select"] if item["kind"] == "count")
    assert metric["column_id"] == FQN + ".id"
    for identity, operator in (("qa_id_present", "is_not_null"), ("qa_id_absent", "is_null")):
        query["filters"].append(
            {
                "id": identity,
                "column_id": FQN + ".id",
                "operator": operator,
                "parameter_name": identity,
                "parameter_type": "integer",
                "parameter_value": "",
                "description": "QA nullable split",
                "confirmed": True,
                "decision": "user",
            }
        )
    original_filters = query["filters"][:-2]
    query["row_filter_tree"] = {
        "operator": "AND",
        "children": [*[{"filter_id": item["id"]} for item in original_filters], {"operator": "OR", "children": [{"filter_id": "qa_id_present"}, {"filter_id": "qa_id_absent"}]}],
    }
    query["aggregate_filters"] = [
        {
            "id": "qa_minimum",
            "select_item_id": metric["id"],
            "operator": "gte",
            "parameter_name": "qa_minimum",
            "parameter_type": "integer",
            "parameter_value": "0",
            "description": "QA COUNT >= 0",
            "confirmed": True,
            "decision": "user",
        }
    ]
    query["aggregate_filter_tree"] = {"filter_id": "qa_minimum"}
    query["timezone"] = source_timezone
    created_type = next(item["data_type"] for item in selected["columns"] if item["name"] == "created_at").upper()
    if "WITH TIME ZONE" in created_type or "TIMESTAMPTZ" in created_type:
        for condition in original_filters:
            if condition["column_id"] == FQN + ".created_at" and condition["operator"] not in {"is_null", "is_not_null"}:
                timestamp = datetime.fromisoformat(condition["parameter_value"].replace("Z", "+00:00"))
                if timestamp.utcoffset() is None:
                    timestamp = timestamp.replace(tzinfo=ZoneInfo(source_timezone))
                condition.update(parameter_type="datetime", parameter_value=timestamp.isoformat())
    else:
        for condition in original_filters:
            if condition["column_id"] == FQN + ".created_at" and condition["parameter_type"] == "datetime":
                timestamp = datetime.fromisoformat(condition["parameter_value"].replace("Z", "+00:00"))
                if timestamp.utcoffset() is not None:
                    condition["parameter_value"] = timestamp.astimezone(ZoneInfo(source_timezone)).replace(tzinfo=None).isoformat()
    approval = command(
        "publish_definition",
        {
            "definition_id": None,
            "expected_version": 0,
            "title": "QA bot daily count",
            "description": "Disposable QA approval: exact daily COUNT(id), fixed August 2026, source timezone",
            "query": query,
            "base_query_artifact_id": current["project"]["artifact_ids"]["query"],
            "contract": {
                "grain": [item["id"] for item in query["select"] if item["kind"] == "date_bucket"],
                "metrics": [{"select_item_id": metric["id"], "unit": "запросы", "null_policy": "EXCLUDE_NULLS_ZERO_IF_EMPTY", "reaggregation": "FORBIDDEN"}],
                "time_column_id": FQN + ".created_at",
                "timezone": source_timezone,
                "mutable_parameters": [],
            },
        },
    )["effect"]["definition"]
    command("use_definition", {"definition_id": approval["definition_id"], "version": approval["version"]})
    applied = command("apply_draft", {"confirmed": True, "execute": True})
    assert applied["session"]["selected_result_id"] == run["id"]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        current = fresh()
        agreed_run = current["project"]["latest_run"]
        if agreed_run["status"] == "READY":
            break
        assert agreed_run["status"] not in {"FAILED", "EXPIRED", "PURGED"}
        time.sleep(2)
    assert agreed_run["status"] == "READY"
    assert len(created["id"]) == 32 and all(char in "0123456789abcdef" for char in created["id"])
    jobs.extend(identity for identity in live.sql("SELECT json_agg(job_id) FROM business_document_sql_query_run WHERE project_id='" + created["id"] + "'") if identity not in jobs)
    agreed = live.api("GET", path + "/results/" + agreed_run["id"])
    assert agreed["definition_status"] == "DEFINITION_VALIDATED" and agreed["definition_freshness"] == "CURRENT"
    assert "HAVING COUNT" in agreed["sql"] and " OR " in agreed["sql"]
    assert [(normal_date(row["values"][0]), int(row["values"][1])) for row in agreed["rows"]] == [(normal_date(row[0]), int(row[1])) for row in expected]
    live.proof("assistant-approved-bot-result", {"definition": approval, "result": agreed, "oracle_rows": expected, "approval_scope": "disposable QA only"})
    live.browser(page, "/sql-assistant?session=" + created["id"])
    expect(page.get_by_test_id("sql-assistant-result")).to_be_visible(timeout=120000)
    page.get_by_role("tab", name="Конструктор", exact=True).click()
    expect(page.get_by_role("heading", name="Конструктор расчёта", exact=True)).to_be_visible()
    page.screenshot(path=str(live.out / "assistant-bot-live.png"), full_page=True)
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("sql-assistant-result")).to_be_visible(timeout=120000)
    live.proof(
        "assistant-bot-result",
        {"session_id": created["id"], "run_id": run["id"], "source": "bot", "oracle_sql": ORACLE_SQL, "oracle_rows": expected, "actual": actual, "jobs": jobs, "no_bot_writes": True},
    )
    live.worker_claims(jobs)
