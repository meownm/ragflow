"""Trusted pilot configuration and admission over the existing SQL sources."""

from datetime import datetime, timezone
import json
import os

from business_documents.application.errors import ConflictError, PermissionDeniedError
from business_documents.sql_query.llm_logs import COLUMNS, METRIC_VERSION, access_fingerprint, completeness


def pilot_policy(actor_id):
    try:
        policy = json.loads(os.environ.get("SQL_LLM_LOGS_POLICY", "{}"))
        if not isinstance(policy, dict) or not all(isinstance(policy.get(key), str) and policy[key] for key in ("version", "profile_id", "entity_id", "db_role")):
            raise ValueError("Invalid policy")
        if not isinstance(policy.get("actors"), list) or not policy["actors"] or any(not isinstance(actor, str) for actor in policy["actors"]):
            raise ValueError("Invalid principals")
        if actor_id not in policy["actors"]:
            raise PermissionDeniedError("Вы не включены в группу пилота логов LLM")
        if policy.get("unknown_action") not in {"warn", "block"} or policy.get("incomplete_action") not in {"warn", "block"}:
            raise ValueError("Explicit completeness policy required")
        return policy
    except (ValueError, TypeError):
        raise PermissionDeniedError("Пилот логов LLM не настроен администратором") from None


def require_llm_logs_admission(payload, actor_id, profile_id=None):
    marker = payload.get("llm_logs")
    if marker is None:
        return None
    policy = pilot_policy(actor_id)
    if marker.get("metric_version") != METRIC_VERSION or marker.get("access_fingerprint") != access_fingerprint(policy) or (profile_id is not None and profile_id != policy["profile_id"]):
        raise PermissionDeniedError("Политика доступа пилота изменилась; подготовьте новый расчёт")
    coverage = completeness(policy, marker["period"], datetime.now(timezone.utc))
    if coverage["action"] == "block":
        raise ConflictError("SQL_SOURCE_INCOMPLETE", "Полнота источника за выбранный период не подтверждена; запуск или выдача заблокированы")
    return {
        "db_role": policy["db_role"],
        "schema": "public",
        "table": "llm_requests_log",
        "columns": list(COLUMNS),
        "profile_id": policy["profile_id"],
        "access_fingerprint": access_fingerprint(policy),
        "source_completeness": coverage,
    }


def authorize_pilot_result(source, actor_id, profile_id, profile_version, is_admin, access_role):
    policy = require_llm_logs_admission(source.compilation.payload, actor_id, profile_id)
    if policy is None:
        return None
    from api.apps.business_documents.sql_execution_registry import BusinessDocumentSqlExecutionRegistryService
    from api.apps.business_documents.sql_query_postgres import check_source_access
    from api.db.db_models import BusinessDocumentSqlExecutionProfile, Connector
    from api.db.services.managed_resource_service import ManagedResourceService

    owner = ManagedResourceService.owner_id()
    profile = BusinessDocumentSqlExecutionProfile.get_or_none(
        (BusinessDocumentSqlExecutionProfile.id == policy["profile_id"]) & (BusinessDocumentSqlExecutionProfile.tenant_id == owner) & BusinessDocumentSqlExecutionProfile.enabled
    )
    resolution = BusinessDocumentSqlExecutionRegistryService.resolve(
        actor_id,
        {
            "schema_version": "1",
            "schema_snapshot": source.command["schema_snapshot"],
            "accepted_requirements": source.command["accepted_requirements"],
            "accepted_schema": source.command["accepted_schema"],
            "selected_profile_id": profile_id,
        },
        is_admin,
        access_role,
    )
    if profile is None or profile.version != profile_version or resolution["status"] != "BOUND" or resolution["selection"]["profile"]["version"] != profile.version:
        raise PermissionDeniedError("Подключение пилота недоступно")
    connector = Connector.get_or_none((Connector.id == profile.connector_id) & (Connector.tenant_id == owner))
    if connector is None:
        raise PermissionDeniedError("Подключение пилота недоступно")
    check_source_access(connector.config, policy)
    return policy


def create_count(service, raw):
    """Seed the existing immutable artifacts; no model and no query dispatch."""
    from copy import deepcopy
    from business_documents import sql_assistant as rules
    from business_documents.async_runtime import run_in_worker_loop
    from business_documents.application.errors import ValidationError
    from business_documents.sql_query.llm_logs import count_query, period
    from business_documents.sql_query.project_compilation import build_project_compile_command
    from business_documents.sql_query.query_specification import compile_query_payload
    from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService
    from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects, _stable_hash
    from api.db.db_models import BusinessDocumentSqlQueryArtifact as Artifact, BusinessDocumentSqlQueryProject as Project, SqlAssistantSession as Session
    from common.misc_utils import get_uuid
    from common.time_utils import current_timestamp

    service.runtime.access.require_create()
    actor = service.runtime.actor_id
    policy = pilot_policy(actor)
    try:
        data = rules.fields(raw, {"client_request_id", "from", "to"})
        key = rules.text(data["client_request_id"], "Ключ команды", 128)
        requested = period(data["from"], data["to"])
    except (ValueError, TypeError) as error:
        raise ValidationError("INVALID_LLM_LOG_PERIOD", str(error)) from error
    marker = {"metric_version": METRIC_VERSION, "access_fingerprint": access_fingerprint(policy), "period": requested}
    request_hash = rules.fingerprint(data)
    existing = Session.get_or_none((Session.tenant_id == service.runtime.tenant_id) & (Session.owner_id == actor) & (Session.create_request_id == key))
    if existing:
        if existing.create_request_hash != request_hash:
            raise ConflictError("SQL_COMMAND_CONFLICT", "Ключ уже использован для другого расчёта")
        return service.get(existing.id)
    loaded = run_in_worker_loop(
        BusinessDocumentSqlQuerySchemaService.load_entities(actor, {"entity_ids": [policy["entity_id"]], "locale": "ru"}, service.runtime.is_admin, service.runtime.access_role)
    )
    item = next((value for value in loaded["entities"] if value["entity_id"] == policy["entity_id"]), None)
    if not item or item.get("lookup", {}).get("status") != "OK":
        raise PermissionDeniedError("Разрешённая таблица логов недоступна в каталоге")
    entity = item["entity"]
    try:
        query = count_query(entity, requested)
    except (ValueError, KeyError) as error:
        raise ValidationError("SQL_LOG_SCHEMA_INVALID", "Схема логов не соответствует метрике") from error
    table = {key: deepcopy(entity[key]) for key in ("id", "fqn", "technical_name", "service", "database", "schema", "version", "schema_fingerprint")}
    table.update(schema_loaded=True, columns_truncated=False, column_count=3, loaded_column_count=3, table_constraints=[])
    table["columns"] = [
        {
            "id": column["fqn"],
            "fqn": column["fqn"],
            "name": column.get("technical_name") or column["name"],
            "data_type": column["data_type"],
            "constraint": column.get("constraint", ""),
            "selected": True,
        }
        for column in entity["column_details"]
        if (column.get("technical_name") or column["name"]) in COLUMNS
    ]
    statement = "Число записей журнала LLM по модели за заданный период начала; все попытки, без JOIN"
    requirements = {"requirements": [{"statement": statement}]}
    schema = {
        "schema_snapshot": {
            "format": "ragflow-sql-schema-snapshot",
            "schema_version": "1",
            "status": "READY",
            "original_requirements": "- " + statement,
            "source": {"type": "openmetadata", "stale": False},
            "requirements": [{"selected_table": table}],
        },
        "accepted_schema": [{"entity_id": table["id"], "version": table["version"], "schema_fingerprint": table["schema_fingerprint"]}],
    }
    compiled = compile_query_payload(build_project_compile_command(requirements, schema, query))
    if compiled["status"] != "READY":
        raise ValidationError("SQL_LOG_QUERY_BLOCKED", "Расчёт не прошёл проверку компилятора")
    admission = require_llm_logs_admission({"llm_logs": marker}, actor)
    with Session._meta.database.atomic():
        existing = Session.get_or_none((Session.tenant_id == service.runtime.tenant_id) & (Session.owner_id == actor) & (Session.create_request_id == key))
        if existing:
            if existing.create_request_hash != request_hash:
                raise ConflictError("SQL_COMMAND_CONFLICT", "Ключ уже использован")
            return service.get(existing.id)
        result = Projects.create_project(
            service.runtime.tenant_id,
            actor,
            {"schema_version": "1", "title": "Записи LLM по модели", "source_request": statement, "locale": "ru"},
            service.runtime.is_admin,
            service.runtime.access_role,
        )
        ids = []
        for kind, value in (("REQUIREMENTS", requirements), ("SCHEMA", schema), ("QUERY", query)):
            identity = get_uuid()
            ids.append(identity)
            Artifact.create(
                id=identity,
                project_id=result["id"],
                tenant_id=service.runtime.tenant_id,
                kind=kind,
                revision=1,
                payload=value,
                content_hash=_stable_hash(value),
                source_proposal_id="metric:" + identity[:25],
                accepted_by=actor,
            )
        value = {"source_artifact_ids": ids, "result": compiled, "llm_logs": marker}
        Artifact.create(
            id=get_uuid(),
            project_id=result["id"],
            tenant_id=service.runtime.tenant_id,
            kind="COMPILATION",
            revision=1,
            payload=value,
            content_hash=_stable_hash(value),
            source_proposal_id="metric:" + ids[2][:25],
            accepted_by=actor,
        )
        Project.update(stage="COMPLETE", requirements_artifact_id=ids[0], schema_artifact_id=ids[1], query_artifact_id=ids[2], execution_profile_id=policy["profile_id"]).where(
            Project.id == result["id"]
        ).execute()
        Session.create(
            id=result["id"],
            tenant_id=service.runtime.tenant_id,
            owner_id=actor,
            create_request_id=key,
            create_request_hash=request_hash,
            state={
                "messages": [{"id": get_uuid(), "text": statement, "created_at": current_timestamp(), "parent_result_id": None}],
                "selected_result_id": None,
                "views": {},
                "draft": None,
                "definitions": {},
                "mode": "EXPLORATORY",
                "structured_queries": {ids[2]: query},
                "execution_timezones": {ids[2]: "UTC"},
                "llm_logs": marker,
                "source_completeness": admission["source_completeness"],
            },
        )
        return service.get(result["id"])


def validate_metric_compilation(project, compilation, marker):
    from api.db.db_models import BusinessDocumentSqlQueryArtifact as Artifact
    from business_documents.sql_query.llm_logs import count_query
    from business_documents.sql_query.project_compilation import build_project_compile_command
    from business_documents.sql_query.query_specification import compile_query_payload

    if compilation.payload.get("llm_logs") != marker:
        raise ConflictError("SQL_LOG_METRIC_CHANGED", "Расчёт больше не соответствует метрике логов LLM")
    require_llm_logs_admission(compilation.payload, project.owner_id)
    sources = []
    for identity in compilation.payload["source_artifact_ids"][:2]:
        artifact = Artifact.get_or_none((Artifact.id == identity) & (Artifact.tenant_id == project.tenant_id) & (Artifact.project_id == project.id))
        if artifact is None:
            raise ConflictError("SQL_LOG_METRIC_CHANGED", "Источник расчёта недоступен")
        sources.append(artifact.payload)
    try:
        table = sources[1]["schema_snapshot"]["requirements"][0]["selected_table"]
        query = count_query({**table, "column_details": table["columns"]}, marker["period"])
        expected = compile_query_payload(build_project_compile_command(*sources, query))
        if expected != compilation.payload["result"]:
            raise ValueError("Metric changed")
    except (KeyError, ValueError, TypeError) as error:
        raise ConflictError("SQL_LOG_METRIC_CHANGED", "SQL не соответствует версии метрики логов LLM") from error
    return "UTC"
