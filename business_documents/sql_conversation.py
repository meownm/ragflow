"""Single-composer SQL conversation policy; no runtime or infrastructure imports."""

from copy import deepcopy
import json
import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from business_documents import sql_assistant as results
from business_documents.sql_result_answer import validate_answer
from business_documents.sql_query.query_specification import parse_schema_snapshot

INTENTS = {"CHANGE", "ANSWER", "AGGREGATE", "VIEW", "CLARIFICATION", "EXPLAIN"}
EXPLANATION_TOPICS = {"conditions", "null", "scope"}
AGGREGATIONS = {"rows", "sum", "avg", "min", "max", "count", "distinct"}
APPEARANCE_FIELDS = {"display", "density", "striped", "wrap"}
VIEW_FIELDS = {"column_order", "hidden", "labels", "formats", "sort", "chart"} | APPEARANCE_FIELDS
ACTIVE_JOB_STATUSES = {"PENDING", "RETRY", "RUNNING"}


def preparing(project):
    return project.get("operation_state") == "RUNNING" or (project.get("current_job") or {}).get("status") in ACTIVE_JOB_STATUSES


def conversation_status(turn, job, runs):
    """Terminal leases/runs remain authoritative after a worker restart."""
    current = deepcopy(turn)
    if not current or current.get("phase") not in {"ROUTING", "PREPARING", "EXECUTING", "CANCELLING"}:
        return current
    run = next((item for item in runs if item["id"] == current.get("pending_run_id")), {})
    if run.get("status") == "UNKNOWN":
        current.update(phase="RECONCILE", job_id=None)
    elif run.get("status") == "CANCELLED" or (job or {}).get("status") == "CANCELLED":
        current.update(phase="CANCELLED", job_id=None)
    elif run.get("status") in {"FAILED", "EXPIRED", "PURGED"} or (job or {}).get("status") in {"DEAD", "STALE"}:
        current.update(phase="ERROR", job_id=None, error={"code": "SQL_CONVERSATION_STOPPED", "message": "Не удалось завершить запрос. Последний успешный результат сохранён."})
        error = (job or {}).get("error") or {}
        if error.get("code") in {"SQL_REVISION_INVALID_RESPONSE", "SQL_REVISION_UNAVAILABLE"}:
            current["error"] = deepcopy(error)
    return current


def automatic_sources(resolution):
    """Only a unique catalog resolution can progress without a source question."""
    if resolution.get("status") != "READY" or not resolution.get("resolutions"):
        return []
    selected = []
    for item in resolution["resolutions"]:
        entities = item.get("catalog_answer", {}).get("entities", [])
        if item.get("needs_clarification") or item.get("lookup", {}).get("status") != "OK" or len(entities) != 1:
            return []
        if entities[0]["id"] not in selected:
            selected.append(entities[0]["id"])
    return selected


def automatic_schema_payload(resolution, details, requirements):
    selected = automatic_sources(resolution)
    if not selected:
        return None
    if details.get("status") != "READY":
        raise ValueError("Полная схема источника недоступна")
    loaded = {item["entity_id"]: item for item in details["entities"]}
    table_fields = {
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
    column_fields = {"id", "name", "fqn", "data_type", "description", "constraint", "glossary_terms"}
    tables, references, retrieval, warnings = {}, [], [], []
    freshness = None
    for identity in selected:
        item = loaded.get(identity) or {}
        entity = item.get("entity") or {}
        fresh = item.get("freshness") or {}
        columns = entity.get("column_details") or []
        if (
            item.get("lookup", {}).get("status") != "OK"
            or entity.get("id") != identity
            or not entity.get("schema_loaded")
            or fresh.get("stale") is not False
            or not fresh.get("snapshot_at")
            or not fresh.get("checked_at")
            or entity.get("columns_truncated")
            or not columns
            or len(columns) != entity.get("column_count")
        ):
            raise ValueError("Схема неполная или устарела; автоматический расчёт недоступен")
        table = {key: deepcopy(value) for key, value in entity.items() if key in table_fields}
        table.update(
            columns=[{**{key: deepcopy(value) for key, value in column.items() if key in column_fields}, "selected": True} for column in columns],
            loaded_column_count=len(columns),
            columns_truncated=False,
        )
        tables[identity] = table
        freshness = fresh
        references.extend(item.get("sources", []))
        retrieval.append(item.get("retrieval", ""))
        warnings.extend(item.get("warnings", []))
    snapshot = {
        "format": "ragflow-sql-schema-snapshot",
        "schema_version": "1",
        "status": "READY",
        "original_requirements": requirements,
        "source": {
            "type": "openmetadata",
            "catalog_snapshot_at": freshness["snapshot_at"],
            "checked_at": freshness["checked_at"],
            "stale": False,
            "references": references,
            "retrieval": list(dict.fromkeys(retrieval)),
        },
        "requirements": [
            {"term": item["term"], "state": "CONFIRMED", "decision": "CATALOG", "interpretation": item.get("interpretation"), "selected_table": tables[item["catalog_answer"]["entities"][0]["id"]]}
            for item in resolution["resolutions"]
        ],
        "warnings": warnings,
    }
    parse_schema_snapshot(snapshot)
    return {
        "schema_snapshot": snapshot,
        "accepted_schema": [{"entity_id": identity, "version": table["version"], "schema_fingerprint": table["schema_fingerprint"]} for identity, table in tables.items()],
    }


def literal_parameter(value, source):
    """A quoted 0 cannot attest 10, nor paid attest unpaid or 5 attest -5."""
    value = str(value)
    if not value.strip():
        return False
    numeric = re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", value)
    before = r"(?<![\w+.,-])" if numeric else r"(?<!\w)"
    after = r"(?!\w|[.,]\d|%)" if numeric else r"(?!\w)"
    return re.search(before + re.escape(value) + after, source) is not None


def definition_binding(plan, context):
    """Only declared parameters may change; derived values need human review."""
    if not isinstance(plan, dict):
        raise ValueError("Неверный ответ о параметрах")
    if plan.get("intent") == "CLARIFICATION" and set(plan) == {"intent", "text"}:
        results.text(plan["text"], "Уточнение", 2000)
        return plan
    if set(plan) != {"intent", "parameters"} or plan["intent"] != "BIND_PARAMETERS" or not isinstance(plan["parameters"], dict):
        raise ValueError("Для определения разрешены только параметры или уточнение")
    if set(plan["parameters"]) != set(context["mutable_parameters"]):
        raise ValueError("Укажите все и только изменяемые параметры определения")
    user_texts = [item["text"] for item in context["discussion"] if item["role"] == "user"]
    normalized = {}
    for name, supplied in plan["parameters"].items():
        if not isinstance(supplied, dict) or set(supplied) != {"value", "source_quote"}:
            raise ValueError("Для параметра нужны значение и цитата пользователя")
        quote = results.text(supplied["source_quote"], "Цитата", 2000)
        if not any(literal_parameter(quote, text) for text in user_texts) or type(supplied["value"]) not in (str, int, float, bool, type(None)):
            raise ValueError("Параметр не подтверждён исходной репликой")
        json.dumps(supplied["value"], allow_nan=False)
        normalized[name] = deepcopy(supplied)
    return {"intent": "BIND_PARAMETERS", "parameters": normalized}


def bind_definition_parameters(query, plan):
    updated = deepcopy(query)
    for item in updated["filters"]:
        if item.get("parameter_name") in plan["parameters"]:
            item["parameter_value"] = plan["parameters"][item["parameter_name"]]["value"]
    return updated


def confirms_definition(text):
    return text.strip().casefold().rstrip(".!") in {"да", "да, выполнить", "верно", "подтверждаю", "хорошо", "ок", "выполняй", "давай"}


def parse_intent(raw, context):
    """A model selects a bounded capability; it never supplies executable SQL."""
    plan = json.loads(raw) if isinstance(raw, str) else raw
    if context and context.get("kind") == "DEFINITION_BINDING":
        return definition_binding(plan, context)
    if not isinstance(plan, dict) or not isinstance(plan.get("intent"), str) or plan["intent"] not in INTENTS:
        raise ValueError("Неверное намерение диалога")
    kind = plan["intent"]
    fields = {
        "CHANGE": {"intent"},
        "ANSWER": {"intent", "answer"},
        "AGGREGATE": {"intent", "operation", "column_id", "scope"},
        "VIEW": {"intent", "patch"},
        "CLARIFICATION": {"intent", "text"},
        "EXPLAIN": {"intent", "topic"},
    }[kind]
    if set(plan) != fields:
        raise ValueError("Неверный состав полей намерения")
    if kind == "CLARIFICATION":
        results.text(plan["text"], "Уточнение", 2000)
        if context:
            validate_answer({"kind": "CLARIFICATION", "text": plan["text"]}, context)
    elif kind != "CHANGE":
        if not context or not context.get("run_id"):
            raise ValueError("Сначала получите результат")
        if kind == "ANSWER":
            validate_answer(plan["answer"], context)
        elif kind == "EXPLAIN":
            if not isinstance(plan["topic"], str) or plan["topic"] not in EXPLANATION_TOPICS:
                raise ValueError("Неизвестная тема объяснения расчёта")
        elif kind == "AGGREGATE":
            if plan["scope"] != "SAVED_RESULT" or not isinstance(plan["operation"], str) or plan["operation"] not in AGGREGATIONS:
                raise ValueError("Сводка разрешена только по сохранённому результату")
            visible = {column["id"] for column in context["columns"]}
            if (plan["operation"] == "rows" and plan["column_id"] is not None) or (plan["operation"] != "rows" and (not isinstance(plan["column_id"], str) or plan["column_id"] not in visible)):
                raise ValueError("Колонка сводки отсутствует в выбранном результате")
        elif not isinstance(plan["patch"], dict) or not plan["patch"] or set(plan["patch"]) - VIEW_FIELDS:
            raise ValueError("Неверные настройки представления")
    return deepcopy(plan)


def change_view(current, patch, columns, sql):
    """Merge presentation fields without silently discarding unrelated settings."""
    if not isinstance(patch, dict) or not patch or set(patch) - VIEW_FIELDS:
        raise ValueError("Неверные настройки представления")
    view = results.restore_view(deepcopy(current), columns)
    for key, value in patch.items():
        if key in APPEARANCE_FIELDS:
            view["appearance"][key] = deepcopy(value)
        elif key in {"labels", "formats"}:
            if not isinstance(value, dict):
                raise ValueError("Неверные настройки представления")
            view[key] = {**view.get(key, {}), **deepcopy(value)}
        else:
            view[key] = deepcopy(value)
    if "chart" in patch and "display" not in patch:
        view["appearance"]["display"] = "chart" if patch["chart"] else "table"
    return results.validate_view(view, columns, sql)


def saved_aggregate(plan, rows, columns, view, sql, *, truncated):
    """Use the existing exact-decimal engine on the complete saved filtered set."""
    visible = [column for column in columns if column["id"] in view["column_order"] and column["id"] not in view["hidden"]]
    parsed = parse_intent(plan, {"run_id": "saved", "columns": visible})
    if parsed["intent"] != "AGGREGATE":
        raise ValueError("Ожидалась сводка сохранённого результата")
    metric = {"id": "conversation", "title": "По сохранённым строкам", "kind": parsed["operation"], "column_id": parsed["column_id"], "row_index": None}
    settings = {**view, "metrics": [metric]}
    value = results.result_metrics(rows, columns, settings, sql)[0]
    return {**value, "scope": "LIMITED" if truncated else "SAVED_RESULT", "source_completeness": "UNKNOWN"}


def render_aggregate(plan, rows, columns, view, sql, *, truncated):
    value = saved_aggregate(plan, rows, columns, view, sql, truncated=truncated)
    label = {"rows": "Число строк", "sum": "Сумма", "avg": "Среднее", "min": "Минимум", "max": "Максимум", "count": "Число значений", "distinct": "Число разных значений"}[plan["operation"]]
    if value["status"] == "OK":
        text = f"{label} по сохранённым строкам: {'≈ ' if value['rounded'] else ''}{value['value']}."
    elif value["status"] == "NULL":
        text = "В выбранных строках нет значений для этой сводки."
    else:
        text = "Для этой сводки нужен новый расчёт по источнику."
    return {
        "status": "READY",
        "kind": "ANSWER" if value["status"] in {"OK", "NULL"} else "NEEDS_QUERY",
        "text": text,
        "scope": value["scope"],
        "source_completeness": "UNKNOWN",
        "context_limited": False,
        "citations": [],
        "calculation": {"operation": plan["operation"], "column_id": plan["column_id"]},
    }


def render_explanation(plan, sql, parameters, view, columns, *, execution_timezone=None, truncated=False):
    """Explain saved query metadata; model prose never supplies factual values."""
    if not isinstance(plan, dict) or plan.get("intent") != "EXPLAIN" or not isinstance(plan.get("topic"), str) or plan["topic"] not in EXPLANATION_TOPICS or set(plan) != {"intent", "topic"}:
        raise ValueError("Неверное объяснение расчёта")
    topic = plan["topic"]
    limited = False
    if topic == "null":
        text = "NULL означает отсутствие значения. Это не пустая строка и не ноль. По одному NULL нельзя установить причину отсутствия значения или наличие связанной записи."
    elif topic == "scope":
        text = "Ответы по результату относятся к сохранённым строкам с настройками на момент вопроса. Полнота всего источника не подтверждена. Полный итог по источнику требует отдельного расчёта."
        if truncated:
            text += " Сохранённая выдача ограничена лимитом."
    else:
        unavailable = "Не удалось кратко разобрать все условия этого SQL. Проверьте их в разделе «SQL и основания». Выбранный результат не изменён."
        if not isinstance(sql, str) or len(sql) > 16_000 or not isinstance(parameters, dict):
            text, limited = unavailable, True
        else:
            try:
                json.dumps(parameters, allow_nan=False)
                statements = parse(sql, dialect="postgres")
                statement = statements[0] if len(statements) == 1 else None
                if not isinstance(statement, exp.Select) or len(list(statement.find_all(exp.Select))) != 1 or statement.args.get("joins") or statement.args.get("offset"):
                    raise ValueError("Сложная область SQL")

                def bind(node):
                    if isinstance(node, exp.Placeholder):
                        if node.name not in parameters:
                            raise ValueError("Параметр SQL не сохранён")
                        return exp.convert(parameters[node.name])
                    return node

                statement = statement.transform(bind)
                where = statement.args.get("where")
                lines = ["Условия выбранного расчёта: " + (where.this.sql(dialect="postgres") if where else "фильтров WHERE нет") + "."]
                having = statement.args.get("having")
                if having:
                    lines.append("Условия после группировки: " + having.this.sql(dialect="postgres") + ".")
                limit = statement.args.get("limit")
                if limit:
                    lines.append("Лимит выдачи: " + limit.expression.sql(dialect="postgres") + ".")
                if execution_timezone:
                    lines.append("Часовой пояс исполнения: " + str(execution_timezone) + ".")
                names = {column["id"]: view.get("labels", {}).get(column["id"], column["name"]) for column in columns}
                if view.get("filters"):
                    operators = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "contains": "содержит", "is_null": "NULL", "is_not_null": "не NULL"}
                    filters = [
                        names[item["column_id"]]
                        + " "
                        + operators[item["operator"]]
                        + ("" if item["operator"] in {"is_null", "is_not_null"} else " " + json.dumps(item.get("value"), ensure_ascii=False, allow_nan=False))
                        for item in view["filters"]
                    ]
                    lines.append("Дополнительные фильтры сохранённых строк: " + "; ".join(filters) + ".")
                lines.append("По самим условиям SQL нельзя установить бизнес-причину выбора периода; здесь показаны действующие условия расчёта.")
                text = "\n".join(lines)
                if len(text) > 4000:
                    text, limited = unavailable, True
            except (ParseError, ValueError, KeyError, TypeError):
                text, limited = unavailable, True
    return {"status": "READY", "kind": "ANSWER", "text": text, "scope": "SAVED_RESULT", "source_completeness": "UNKNOWN", "context_limited": limited, "citations": []}


def preparation_action(project, *, answers=None, pending_run_id=None):
    """Choose one server step; an unrelated historical READY run never wins."""
    if pending_run_id:
        run = next((item for item in project.get("runs", []) if item["id"] == pending_run_id), None)
        if run is None:
            return "ERROR"
        if run["status"] == "READY" and preparing(project):
            return "WAIT"
        return {
            "READY": "SELECT_RESULT",
            "FAILED": "ERROR",
            "CANCELLED": "CANCELLED",
            "EXPIRED": "ERROR",
            "PURGED": "ERROR",
            "UNKNOWN": "RECONCILE",
            "SUCCEEDED": "ERROR",
            "QUEUED": "WAIT",
            "RUNNING": "WAIT",
            "CANCEL_REQUESTED": "WAIT",
        }.get(run["status"], "ERROR")
    if preparing(project):
        return "WAIT"
    if project.get("last_error"):
        return "ERROR"
    proposal = project.get("pending_proposal")
    if proposal:
        result = proposal.get("payload", {}).get("agent_result") or {}
        kind = proposal["kind"]
        if kind == "REQUIREMENTS":
            value = result.get("proposal")
            if not value:
                return "ERROR"
            blocked = [question for question in value.get("questions", []) if question.get("blocking") and not (answers or {}).get(str(question["id"]), "").strip()]
            return "ASK_REQUIREMENTS" if blocked else "ACCEPT_REQUIREMENTS"
        if kind == "SCHEMA":
            if result.get("status") == "DEGRADED":
                return "ERROR"
            return "ACCEPT_SCHEMA" if result.get("status") == "READY" and result.get("conversation_schema") else "ASK_SOURCE"
        if kind == "QUERY":
            return "ACCEPT_QUERY" if result.get("status") == "PROPOSED" and result.get("proposal") else "ASK_QUERY" if result.get("status") == "NEEDS_CLARIFICATION" else "ERROR"
        if kind == "REVISION":
            return "ACCEPT_REVISION" if result.get("proposal") and result["proposal"].get("checks", {}).get("status") == "PASS" else "ERROR"
        return "ERROR"
    next_agent = project.get("next_agent")
    if next_agent in {"REQUIREMENTS", "SCHEMA", "QUERY"}:
        return "REQUEST_" + next_agent
    compilation = project.get("compilation")
    if not compilation:
        return "COMPILE" if project.get("artifact_ids", {}).get("query") else "ERROR"
    compiled = compilation.get("result") or {}
    return "EXECUTE" if compiled.get("status") == "READY" and compiled.get("guard", {}).get("status") == "PASS" else "ERROR"
