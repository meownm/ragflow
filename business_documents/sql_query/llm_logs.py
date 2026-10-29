"""The bounded LLM-log count and its period-scoped completeness contract."""

from datetime import datetime, timezone, timedelta

from business_documents.sql_assistant import fingerprint

FQN = "docker_postgres_bot.bot.public.llm_requests_log"
COLUMNS = ("log_id", "model_name", "request_timestamp_start")
METRIC_VERSION = "llm-log-count/1"


def instant(value):
    if not isinstance(value, str):
        raise ValueError("Укажите дату ISO 8601 со смещением часового пояса")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Дата должна содержать смещение часового пояса")
    return parsed.astimezone(timezone.utc)


def period(start, end):
    start, end = instant(start), instant(end)
    if start >= end:
        raise ValueError("Начало периода должно предшествовать концу")
    return {"from": start.isoformat(), "to": end.isoformat()}


def access_fingerprint(policy):
    return fingerprint({key: policy[key] for key in ("version", "profile_id", "entity_id", "db_role", "actors")})


def completeness(policy, requested, now):
    """Only trusted, fresh evidence covering both boundaries proves coverage."""
    result = {"status": "UNKNOWN", "period": requested, "evaluated_at": now.isoformat(), "reason": "NO_CURRENT_EVIDENCE"}
    evidence = policy.get("completeness")
    try:
        if (
            isinstance(evidence, dict)
            and evidence["status"] in {"COMPLETE", "PARTIAL", "STALE"}
            and isinstance(evidence["evidence_ref"], str)
            and 0 < len(evidence["evidence_ref"]) <= 500
            and instant(evidence["observed_at"]) <= now < instant(evidence["valid_until"])
            and now - instant(evidence["observed_at"]) <= timedelta(seconds=300)
            and instant(evidence["coverage_from"]) <= instant(requested["from"])
            and instant(evidence["coverage_to"]) >= instant(requested["to"])
        ):
            result.update(status=evidence["status"], reason="PERIOD_EVIDENCE", evidence=dict(evidence))
    except (KeyError, TypeError, ValueError):
        pass
    action = policy.get("unknown_action", "block") if result["status"] == "UNKNOWN" else policy.get("incomplete_action", "block") if result["status"] != "COMPLETE" else "allow"
    result["action"] = action if action in {"allow", "warn", "block"} else "block"
    if result["status"] != "COMPLETE" and result["action"] == "allow":
        result["action"] = "block"
    return result


def count_query(entity, requested):
    """Use the existing structured compiler and exact physical column bindings."""
    by_name = {item.get("technical_name") or item["name"]: item for item in entity["column_details"]}
    if entity["fqn"] != FQN or not set(COLUMNS) <= by_name.keys():
        raise ValueError("Каталог не содержит нужную таблицу и поля логов LLM")
    if "WITH TIME ZONE" not in by_name["request_timestamp_start"]["data_type"].upper():
        raise ValueError("Временное поле логов должно быть timestamp with time zone")

    def ref(name):
        return by_name[name]["fqn"]

    return {
        "base_entity_id": entity["id"],
        "aliases": {entity["id"]: "logs"},
        "joins": [],
        "select": [
            {"id": "model", "kind": "column", "column_id": ref("model_name"), "alias": "model_name", "grain": None},
            {"id": "count", "kind": "count", "column_id": ref("log_id"), "alias": "request_count", "grain": None},
        ],
        "filters": [
            {
                "id": name,
                "column_id": ref("request_timestamp_start"),
                "operator": operator,
                "parameter_name": "period_" + name,
                "parameter_type": "datetime",
                "parameter_value": requested[name],
                "description": "Граница периода начала запроса",
                "confirmed": True,
                "decision": "user",
            }
            for name, operator in (("from", "gte"), ("to", "lt"))
        ],
        "order_by": [{"select_item_id": "model", "direction": "ASC"}],
        "row_limit": 1000,
    }
