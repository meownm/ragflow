"""Resolve dialogue references through the existing catalog and definition gates."""

from business_documents.sql_dialogue import metric_matches, reference_keys
from business_documents.sql_query.query_specification import parse_schema_snapshot
from business_documents.application.errors import ConflictError, ValidationError
from api.apps.sql_assistant.definitions import definitions_for_schema


def options(project, question=""):
    schema = project.get("artifacts", {}).get("schema")
    if not schema:
        return {"references": [], "metrics": []}
    definitions = definitions_for_schema(schema, project.get("catalog_changed", False))
    tables = [{"kind": "TABLE", "id": table.id, "title": table.fqn, "fqn": table.fqn} for table in parse_schema_snapshot(schema["schema_snapshot"]).tables]
    metrics = [
        {
            "kind": "DEFINITION",
            "id": item["definition_id"],
            "title": item["title"],
            "version": item["version"],
            "content_hash": item["content_hash"],
            "description": item["description"],
            "freshness": item["freshness"],
        }
        for item in definitions
    ]
    matches = metric_matches(question, definitions)
    return {
        "references": tables + metrics,
        "metrics": [item for item in metrics if any(match["definition_id"] == item["id"] for match in matches)],
    }


def resolve_references(project, raw, *, available=None):
    keys = reference_keys(raw)
    if not keys:
        return []
    if project.get("catalog_changed"):
        raise ConflictError("SQL_REFERENCE_STALE", "Схема упомянутых объектов изменилась. Обновите выбор источников.")
    if available is None:
        available = options(project)["references"]
    resolved = []
    for key in keys:
        item = next((item for item in available if item["kind"] == key["kind"] and item["id"] == key["id"]), None)
        if item is None:
            raise ValidationError("SQL_REFERENCE_UNAVAILABLE", "Упомянутый объект недоступен в принятой схеме. Обновите выбор источников.")
        if key["kind"] == "DEFINITION" and (key["version"] != item["version"] or key["content_hash"] != item["content_hash"] or item["freshness"] != "CURRENT"):
            raise ConflictError("SQL_DEFINITION_STALE", "Упомянутое определение изменилось. Выберите актуальную версию.")
        resolved.append(item)
    return resolved


def resolve_catalog_references(runtime, raw):
    """Initial questions have no accepted schema; use the current entity ACL gate."""
    from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService
    from business_documents.async_runtime import run_in_worker_loop
    from business_documents.application.errors import PermissionDeniedError, BusinessDocumentError

    keys = reference_keys(raw)
    if any(item["kind"] != "TABLE" for item in keys):
        raise ValueError("Сначала выберите схему, чтобы использовать согласованное определение")
    if not keys:
        return []
    response = run_in_worker_loop(
        BusinessDocumentSqlQuerySchemaService.load_entities(runtime.actor_id, {"entity_ids": [item["id"] for item in keys], "locale": "ru"}, runtime.is_admin, runtime.access_role)
    )
    entities = {item["entity_id"]: item for item in response["entities"]}
    result = []
    for key in keys:
        found = entities.get(key["id"])
        if found is None:
            raise PermissionDeniedError("Упомянутая таблица больше недоступна")
        if found.get("lookup", {}).get("status") != "OK":
            raise BusinessDocumentError("SQL_SCHEMA_CATALOG_UNAVAILABLE", "Не удалось проверить упомянутую таблицу", 503)
        entity = found.get("entity")
        if not entity or entity.get("deleted") or entity.get("id") != key["id"]:
            raise PermissionDeniedError("Упомянутая таблица больше недоступна")
        result.append({"kind": "TABLE", "id": entity["id"], "title": entity["fqn"], "fqn": entity["fqn"]})
    return result


def require_metric_choice(project, question, payload):
    candidates = options(project, question)["metrics"]
    choice = payload.get("metric_resolution")
    if choice is not None and choice != "EXPLORATORY":
        raise ValueError("Выберите определение отдельно или продолжите исследование")
    if candidates and choice != "EXPLORATORY":
        raise ValidationError("SQL_METRIC_SELECTION_REQUIRED", "Найдены согласованные определения. Выберите определение или явно продолжите исследование.", {"metrics": candidates})
    return candidates
