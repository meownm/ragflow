"""Managed definition versions and the shared API/worker semantic admission gate."""

from copy import deepcopy

from peewee import IntegrityError

from api.db.db_models import BusinessDocumentSqlQueryArtifact, SqlAssistantDefinition, SqlAssistantDefinitionVersion, SqlAssistantSession
from api.db.services.managed_resource_service import ManagedResourceConfigurationError, ManagedResourceService
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError
from business_documents.sql_assistant import fingerprint, positive_integer
from business_documents.sql_assistant_definitions import DEFINITION_CHECKS, compile_defined_query, definition_payload, definition_scope, dependency_signature
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


def _tenant():
    try:
        return ManagedResourceService.owner_id()
    except ManagedResourceConfigurationError as error:
        raise BusinessDocumentError("SQL_DEFINITION_REGISTRY_UNAVAILABLE", "Владелец реестра определений не настроен", 503) from error


def reference(version):
    return {"definition_id": version.definition_id, "version": version.version, "version_id": version.id, "content_hash": version.content_hash}


def read_definition(definition_id, version=None):
    head = SqlAssistantDefinition.get_or_none((SqlAssistantDefinition.id == definition_id) & (SqlAssistantDefinition.tenant_id == _tenant()))
    if head is None:
        raise BusinessDocumentError("SQL_DEFINITION_NOT_FOUND", "Определение не найдено", 404)
    stored = SqlAssistantDefinitionVersion.get_or_none(
        (SqlAssistantDefinitionVersion.definition_id == head.id)
        & (SqlAssistantDefinitionVersion.tenant_id == head.tenant_id)
        & (SqlAssistantDefinitionVersion.version == (head.version if version is None else version))
    )
    if stored is None or fingerprint(stored.payload) != stored.content_hash:
        raise ConflictError("SQL_DEFINITION_UNVERIFIABLE", "Сохранённую версию определения не удалось проверить")
    return head, stored


def freshness(head, version, catalog_changed=False):
    return "RETIRED" if not head.enabled else "SUPERSEDED" if version.version != head.version else "METADATA_CHANGED" if catalog_changed else "CURRENT"


def public_definition(head, version, catalog_changed=False):
    return {
        **reference(version),
        "title": version.payload["title"],
        "description": version.payload["description"],
        "contract": version.payload["contract"],
        "approved_by": version.approved_by,
        "approved_at": version.create_time,
        "freshness": freshness(head, version, catalog_changed),
        "checks": DEFINITION_CHECKS,
    }


def definitions_for_schema(schema, catalog_changed=False):
    result = []
    signature = dependency_signature(schema)
    for head in (
        SqlAssistantDefinition.select()
        .where((SqlAssistantDefinition.tenant_id == _tenant()) & (SqlAssistantDefinition.scope_hash == definition_scope(schema)))
        .order_by(SqlAssistantDefinition.update_time.desc())
        .limit(100)
    ):
        _, version = read_definition(head.id)
        result.append(public_definition(head, version, catalog_changed or dependency_signature(version.payload["schema"]) != signature))
    return result


def publish_definition(project, payload, actor_id, is_admin):
    if not is_admin:
        raise PermissionDeniedError("Утверждать определения расчётов может только администратор")
    positive_integer(payload["expected_version"], "Версия определения", zero=True)
    approved = definition_payload(project["artifacts"]["requirements"], project["artifacts"]["schema"], payload["query"], payload["contract"], payload["title"], payload["description"])
    tenant = _tenant()
    expected = payload["expected_version"]
    try:
        with SqlAssistantDefinition._meta.database.atomic():
            if payload["definition_id"] is None:
                if expected != 0:
                    raise ValueError("Для нового определения ожидается версия 0")
                head = SqlAssistantDefinition.create(id=get_uuid(), tenant_id=tenant, version=1, scope_hash=definition_scope(approved["schema"]))
            else:
                head, _ = read_definition(payload["definition_id"])
                changed = (
                    SqlAssistantDefinition.update(version=expected + 1, enabled=True, scope_hash=definition_scope(approved["schema"]), update_time=current_timestamp())
                    .where((SqlAssistantDefinition.id == head.id) & (SqlAssistantDefinition.version == expected))
                    .execute()
                )
                if changed != 1:
                    raise ConflictError("SQL_DEFINITION_VERSION_CONFLICT", "Определение уже изменено; обновите список версий")
                head.version, head.enabled = expected + 1, True
            version = SqlAssistantDefinitionVersion.create(
                id=get_uuid(), definition_id=head.id, tenant_id=tenant, version=head.version, payload=approved, content_hash=fingerprint(approved), approved_by=actor_id
            )
            return public_definition(head, version)
    except IntegrityError as error:
        raise ConflictError("SQL_DEFINITION_VERSION_CONFLICT", "Версия определения уже опубликована") from error


def retire_definition(definition_id, expected_version, is_admin, schema):
    if not is_admin:
        raise PermissionDeniedError("Отзывать определения может только администратор")
    positive_integer(expected_version, "Версия определения")
    head, version = read_definition(definition_id)
    if definition_scope(version.payload["schema"]) != definition_scope(schema):
        raise ValueError("Определение не относится к выбранной схеме")
    changed = (
        SqlAssistantDefinition.update(enabled=False, update_time=current_timestamp())
        .where(SqlAssistantDefinition.id == head.id, SqlAssistantDefinition.version == expected_version, SqlAssistantDefinition.enabled)
        .execute()
    )
    if changed != 1:
        raise ConflictError("SQL_DEFINITION_VERSION_CONFLICT", "Определение изменено или уже отозвано")


def compile_definition_draft(project, query, ref):
    head, version = read_definition(ref["definition_id"], ref["version"])
    if ref != reference(version):
        raise ConflictError("SQL_DEFINITION_UNVERIFIABLE", "Ссылка на версию определения изменилась")
    if freshness(head, version, project.get("catalog_changed", False)) != "CURRENT":
        raise ConflictError("SQL_DEFINITION_STALE", "Версия определения или схема устарела; выберите актуальное определение")
    return compile_defined_query(version.payload, project["artifacts"]["requirements"], project["artifacts"]["schema"], query)


def definition_info(state, query_id, catalog_changed=False):
    binding = state.get("definitions", {}).get(query_id)
    if not binding:
        return {"definition_status": "NOT_VALIDATED", "definition_freshness": "NOT_APPLICABLE", "definition": None}
    try:
        head, version = read_definition(binding["reference"]["definition_id"], binding["reference"]["version"])
        if binding["reference"] != reference(version):
            raise ValueError("Reference changed")
        return {"definition_status": "DEFINITION_VALIDATED", "definition_freshness": freshness(head, version, catalog_changed), "definition": public_definition(head, version, catalog_changed)}
    except (BusinessDocumentError, ValueError, KeyError, TypeError):
        return {"definition_status": "NOT_VALIDATED", "definition_freshness": "UNKNOWN", "definition": None}


def require_definition_admission(project, compilation):
    """Same gate for preflight, API queueing and actual worker execution.

    This reads the immutable compilation's source query, never the selected
    result or the browser's status. Unrelated SQL projects do not use the gate.
    """
    session = SqlAssistantSession.get_or_none((SqlAssistantSession.id == project.id) & (SqlAssistantSession.tenant_id == project.tenant_id))
    if session is None:
        return None
    if session.state.get("llm_logs"):
        from api.apps.business_documents.sql_llm_logs import validate_metric_compilation

        return validate_metric_compilation(project, compilation, session.state["llm_logs"])
    query_id = compilation.payload["source_artifact_ids"][2]
    binding = session.state.get("definitions", {}).get(query_id)
    if not binding:
        if session.state.get("mode") == "AGREED":
            raise ConflictError("SQL_DEFINITION_REQUIRED", "Этот расчёт не связан с согласованным определением; примените определение или явно создайте исследовательскую версию")
        return session.state.get("execution_timezones", {}).get(query_id)
    source = {}
    for kind, identity in zip(("requirements", "schema"), compilation.payload["source_artifact_ids"][:2], strict=True):
        artifact = BusinessDocumentSqlQueryArtifact.get_or_none(
            (BusinessDocumentSqlQueryArtifact.id == identity) & (BusinessDocumentSqlQueryArtifact.project_id == project.id) & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id)
        )
        if artifact is None:
            raise ConflictError("SQL_DEFINITION_UNVERIFIABLE", "Источники определения не удалось проверить")
        source[kind] = artifact.payload
    try:
        _, compiled = compile_definition_draft({"artifacts": source}, binding["query"], binding["reference"])
        actual = compilation.payload["result"]
        expected = {"sql": compiled["sql"], "parameters": compiled["parameters"]}
        if compiled["status"] != "READY" or expected != {"sql": actual["sql"], "parameters": actual["parameters"]} or fingerprint(expected) != binding["compiled_hash"]:
            raise ValueError("Compiled definition changed")
        return binding["query"]["timezone"]
    except (ValueError, KeyError, TypeError) as error:
        raise ConflictError("SQL_DEFINITION_MISMATCH", "SQL или параметры больше не соответствуют согласованному определению") from error


def binding_for_draft(draft):
    return {
        "reference": deepcopy(draft["definition_ref"]),
        "query": deepcopy(draft["query"]),
        "compiled_hash": fingerprint({"sql": draft["compiled"]["sql"], "parameters": draft["compiled"]["parameters"]}),
        "validated_at": current_timestamp(),
    }
