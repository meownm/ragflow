"""Persistence and authorization adapter for the SQL execution registry."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping

from peewee import IntegrityError

from api.apps.business_documents.authorization import BusinessDocumentAccess
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError, ValidationError
from api.apps.services.openmetadata_runtime_service import get_openmetadata_service, openmetadata_catalog_accessible
from api.db.db_models import (
    BusinessDocumentSqlCatalogBinding,
    BusinessDocumentSqlExecutionProfile,
    Connector,
)
from api.db.services.audit_service import record_audit_event
from api.db.services.managed_resource_service import ManagedResourceConfigurationError, ManagedResourceService
from business_documents.sql_query.execution_registry import (
    CatalogBindingInput,
    CatalogBindingRecord,
    ExecutionProfileInput,
    ExecutionProfileRecord,
    ExecutionRegistryValidationError,
    parse_catalog_binding_payload,
    parse_execution_profile_payload,
    parse_registry_disable_payload,
    parse_resolve_execution_profile_command,
    resolve_execution_profile,
)
from common.misc_utils import get_uuid


_POSTGRES_SOURCE = "postgresql"


@dataclass(frozen=True, slots=True)
class _ConnectorTarget:
    database: str
    identity_fingerprint: str


def _require_admin(is_admin: bool) -> None:
    if not is_admin:
        raise PermissionDeniedError("Only an administrator can manage SQL execution profiles and catalog bindings")


def _registry_tenant_id() -> str:
    try:
        return ManagedResourceService.owner_id()
    except ManagedResourceConfigurationError as exc:
        raise BusinessDocumentError(
            "SQL_EXECUTION_REGISTRY_UNAVAILABLE",
            "The managed-resource owner is not configured correctly",
            503,
        ) from exc


def _catalog_access_allowed(actor_id: str, is_admin: bool) -> bool:
    return openmetadata_catalog_accessible(get_openmetadata_service(), actor_id, is_admin)


def _require_catalog_access(
    actor_id: str,
    is_admin: bool,
    checker: Callable[[str, bool], bool] | None = None,
) -> None:
    try:
        allowed = (checker or _catalog_access_allowed)(actor_id, is_admin)
    except Exception as exc:
        raise BusinessDocumentError(
            "SQL_EXECUTION_CATALOG_AUTH_UNAVAILABLE",
            "OpenMetadata Dataset access could not be verified",
            503,
        ) from exc
    if not allowed:
        raise PermissionDeniedError("OpenMetadata Dataset access is required to resolve an SQL execution profile")


def _not_found(kind: str) -> BusinessDocumentError:
    return BusinessDocumentError(
        f"SQL_{kind.upper()}_NOT_FOUND",
        f"SQL {kind.replace('_', ' ')} was not found",
        404,
    )


def _connector_target(connector: Connector | None, tenant_id: str) -> _ConnectorTarget | None:
    if connector is None or connector.tenant_id != tenant_id or connector.source != _POSTGRES_SOURCE:
        return None
    config = connector.config if isinstance(connector.config, dict) else {}
    credentials = config.get("credentials") if isinstance(config.get("credentials"), dict) else {}
    host = str(config.get("host") or "").strip()
    database = str(config.get("database") or "").strip()
    username = str(credentials.get("username") or "").strip()
    password = str(credentials.get("password") or "").strip()
    raw_port = config.get("port")
    if isinstance(raw_port, bool):
        return None
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        return None
    if not all((host, database, username, password)) or not 1 <= port <= 65_535:
        return None
    # Password rotation must not invalidate a profile; changing its target or
    # database role requires an explicit profile update and version bump.
    identity = json.dumps(
        {
            "source": connector.source,
            "host": host.casefold(),
            "port": port,
            "database": database,
            "username": username,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _ConnectorTarget(
        database=database,
        identity_fingerprint=f"sha256:{hashlib.sha256(identity).hexdigest()}",
    )


def _require_connector(connector_id: str, tenant_id: str) -> tuple[Connector, _ConnectorTarget]:
    connector = Connector.get_or_none((Connector.id == connector_id) & (Connector.tenant_id == tenant_id))
    target = _connector_target(connector, tenant_id)
    if connector is None or target is None:
        raise ValidationError(
            "INVALID_SQL_EXECUTION_CONNECTOR",
            "Execution profile requires a complete PostgreSQL connector owned by the managed tenant",
        )
    return connector, target


def _connector_map(tenant_id: str, connector_ids: Iterable[str]) -> dict[str, Connector]:
    ids = sorted(set(connector_ids))
    if not ids:
        return {}
    return {connector.id: connector for connector in Connector.select().where((Connector.tenant_id == tenant_id) & (Connector.id.in_(ids)))}


def _profile_record(
    profile: BusinessDocumentSqlExecutionProfile,
    connector: Connector | None,
) -> ExecutionProfileRecord:
    target = _connector_target(connector, profile.tenant_id)
    allowed = profile.allowed_schemas if isinstance(profile.allowed_schemas, list) else []
    policy_valid = _profile_policy_valid(profile)
    identity_matches = bool(target and target.identity_fingerprint == profile.connector_identity_fingerprint)
    return ExecutionProfileRecord(
        id=profile.id,
        name=profile.name,
        dialect=profile.dialect,
        allowed_schemas=tuple(str(schema) for schema in allowed),
        statement_timeout_ms=profile.statement_timeout_ms,
        max_rows=profile.max_rows,
        max_result_bytes=profile.max_result_bytes,
        enabled=bool(profile.enabled),
        version=profile.version,
        policy_valid=policy_valid,
        connector_available=identity_matches,
        target_database=target.database if target is not None else "",
    )


def _binding_record(binding: BusinessDocumentSqlCatalogBinding) -> CatalogBindingRecord:
    return CatalogBindingRecord(
        id=binding.id,
        catalog_service=binding.catalog_service,
        catalog_database=binding.catalog_database,
        catalog_schema=binding.catalog_schema,
        execution_profile_id=binding.execution_profile_id,
        enabled=bool(binding.enabled),
        version=binding.version,
    )


def _serialize_profile(
    profile: BusinessDocumentSqlExecutionProfile,
    connector: Connector | None,
) -> dict[str, Any]:
    record = _profile_record(profile, connector)
    target = _connector_target(connector, profile.tenant_id)
    return {
        **record.to_public(),
        "allowed_schemas": list(record.allowed_schemas),
        "enabled": bool(profile.enabled),
        "available": bool(profile.enabled) and record.policy_valid and record.connector_available,
        "policy_valid": record.policy_valid,
        "connector_available": target is not None,
        "connector_identity_matches": bool(target and target.identity_fingerprint == profile.connector_identity_fingerprint),
        "connector": {
            "id": profile.connector_id,
            "name": connector.name if connector is not None else "",
            "source": connector.source if connector is not None else "",
            "database": record.target_database,
        },
        "created_by": profile.created_by,
        "updated_by": profile.updated_by,
    }


def _serialize_binding(binding: BusinessDocumentSqlCatalogBinding) -> dict[str, Any]:
    return {
        "id": binding.id,
        "catalog_service": binding.catalog_service,
        "catalog_database": binding.catalog_database,
        "catalog_schema": binding.catalog_schema,
        "execution_profile_id": binding.execution_profile_id,
        "enabled": bool(binding.enabled),
        "version": binding.version,
        "created_by": binding.created_by,
        "updated_by": binding.updated_by,
    }


def _profile_policy_valid(profile: BusinessDocumentSqlExecutionProfile) -> bool:
    try:
        parse_execution_profile_payload(
            {
                "schema_version": "1",
                "name": profile.name,
                "connector_id": profile.connector_id,
                "dialect": profile.dialect,
                "allowed_schemas": profile.allowed_schemas,
                "statement_timeout_ms": profile.statement_timeout_ms,
                "max_rows": profile.max_rows,
                "max_result_bytes": profile.max_result_bytes,
                "enabled": bool(profile.enabled),
            }
        )
    except ExecutionRegistryValidationError:
        return False
    return True


def _profile_values(
    value: ExecutionProfileInput,
    actor_id: str,
    connector_identity_fingerprint: str,
) -> dict[str, Any]:
    return {
        "name": value.name,
        "connector_id": value.connector_id,
        "connector_identity_fingerprint": connector_identity_fingerprint,
        "dialect": value.dialect,
        "allowed_schemas": list(value.allowed_schemas),
        "statement_timeout_ms": value.statement_timeout_ms,
        "max_rows": value.max_rows,
        "max_result_bytes": value.max_result_bytes,
        "enabled": value.enabled,
        "updated_by": actor_id,
    }


def _is_binding_disable_only(
    binding: BusinessDocumentSqlCatalogBinding,
    value: CatalogBindingInput,
) -> bool:
    return not value.enabled and (
        value.catalog_service,
        value.catalog_database,
        value.catalog_schema,
        value.execution_profile_id,
    ) == (
        binding.catalog_service,
        binding.catalog_database,
        binding.catalog_schema,
        binding.execution_profile_id,
    )


def _binding_values(value: CatalogBindingInput, actor_id: str) -> dict[str, Any]:
    return {
        "catalog_service": value.catalog_service,
        "catalog_database": value.catalog_database,
        "catalog_schema": value.catalog_schema,
        "execution_profile_id": value.execution_profile_id,
        "enabled": value.enabled,
        "updated_by": actor_id,
    }


def _is_disable_payload(payload: Any) -> bool:
    return isinstance(payload, Mapping) and set(payload) == {"schema_version", "enabled", "expected_version"}


def _disable_version(payload: Any, validation_code: str) -> int:
    try:
        return parse_registry_disable_payload(payload)
    except ExecutionRegistryValidationError as exc:
        raise ValidationError(validation_code, str(exc)) from exc


def _disable_record(
    model,
    record_id: str,
    tenant_id: str,
    actor_id: str,
    expected_version: int,
    *,
    kind: str,
    conflict_code: str,
    conflict_message: str,
):
    updated = (
        model.update(
            enabled=False,
            updated_by=actor_id,
            version=model.version + 1,
        )
        .where((model.id == record_id) & (model.tenant_id == tenant_id) & (model.version == expected_version))
        .execute()
    )
    if updated == 1:
        return model.get_by_id(record_id)
    current = model.get_or_none((model.id == record_id) & (model.tenant_id == tenant_id))
    if current is None:
        raise _not_found(kind)
    raise ConflictError(
        conflict_code,
        conflict_message,
        {"current_version": current.version},
    )


def _audit(action: str, actor_id: str, tenant_id: str, object_type: str, object_id: str) -> None:
    record_audit_event(
        action=action,
        outcome="SUCCESS",
        actor_id=actor_id,
        actor_type="USER",
        tenant_id=tenant_id,
        object_type=object_type,
        object_id=object_id,
        metadata={"component": "business_documents", "stage": "sql_execution_registry"},
    )


class BusinessDocumentSqlExecutionRegistryService:
    """Administer profiles/bindings and resolve safe public selections."""

    @classmethod
    def list_connectors(cls, actor_id: str, is_admin: bool) -> dict[str, Any]:
        _require_admin(is_admin)
        tenant_id = _registry_tenant_id()
        items = []
        for connector in Connector.select().where((Connector.tenant_id == tenant_id) & (Connector.source == _POSTGRES_SOURCE)):
            target = _connector_target(connector, tenant_id)
            items.append(
                {
                    "id": connector.id,
                    "name": connector.name,
                    "source": connector.source,
                    "database": target.database if target is not None else "",
                    "available": target is not None,
                }
            )
        items.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        return {"schema_version": "1", "items": items}

    @classmethod
    def list_profiles(cls, actor_id: str, is_admin: bool) -> dict[str, Any]:
        _require_admin(is_admin)
        tenant_id = _registry_tenant_id()
        profiles = list(BusinessDocumentSqlExecutionProfile.select().where(BusinessDocumentSqlExecutionProfile.tenant_id == tenant_id).order_by(BusinessDocumentSqlExecutionProfile.name.asc()))
        connectors = _connector_map(tenant_id, (profile.connector_id for profile in profiles))
        return {
            "schema_version": "1",
            "items": [_serialize_profile(profile, connectors.get(profile.connector_id)) for profile in profiles],
        }

    @classmethod
    def create_profile(cls, actor_id: str, payload: Any, is_admin: bool) -> dict[str, Any]:
        _require_admin(is_admin)
        try:
            value, _ = parse_execution_profile_payload(payload)
        except ExecutionRegistryValidationError as exc:
            raise ValidationError("INVALID_SQL_EXECUTION_PROFILE", str(exc)) from exc
        tenant_id = _registry_tenant_id()
        connector, target = _require_connector(value.connector_id, tenant_id)
        profile_id = get_uuid()
        try:
            with BusinessDocumentSqlExecutionProfile._meta.database.atomic():
                BusinessDocumentSqlExecutionProfile.create(
                    id=profile_id,
                    tenant_id=tenant_id,
                    created_by=actor_id,
                    version=1,
                    **_profile_values(value, actor_id, target.identity_fingerprint),
                )
        except IntegrityError as exc:
            raise ConflictError("SQL_EXECUTION_PROFILE_EXISTS", "An execution profile with this name already exists") from exc
        profile = BusinessDocumentSqlExecutionProfile.get_by_id(profile_id)
        _audit("sql_execution_profile.created", actor_id, tenant_id, "sql_execution_profile", profile_id)
        return _serialize_profile(profile, connector)

    @classmethod
    def update_profile(cls, actor_id: str, profile_id: str, payload: Any, is_admin: bool) -> dict[str, Any]:
        _require_admin(is_admin)
        if _is_disable_payload(payload):
            expected_version = _disable_version(payload, "INVALID_SQL_EXECUTION_PROFILE")
            tenant_id = _registry_tenant_id()
            profile = _disable_record(
                BusinessDocumentSqlExecutionProfile,
                profile_id,
                tenant_id,
                actor_id,
                expected_version,
                kind="execution_profile",
                conflict_code="SQL_EXECUTION_PROFILE_VERSION_CONFLICT",
                conflict_message="Execution profile changed; reload it before saving",
            )
            connectors = _connector_map(tenant_id, [profile.connector_id])
            _audit("sql_execution_profile.updated", actor_id, tenant_id, "sql_execution_profile", profile_id)
            return _serialize_profile(profile, connectors.get(profile.connector_id))
        try:
            value, expected_version = parse_execution_profile_payload(payload, update=True)
        except ExecutionRegistryValidationError as exc:
            raise ValidationError("INVALID_SQL_EXECUTION_PROFILE", str(exc)) from exc
        tenant_id = _registry_tenant_id()
        profile = BusinessDocumentSqlExecutionProfile.get_or_none((BusinessDocumentSqlExecutionProfile.id == profile_id) & (BusinessDocumentSqlExecutionProfile.tenant_id == tenant_id))
        if profile is None:
            raise _not_found("execution_profile")
        connector = Connector.get_or_none((Connector.id == value.connector_id) & (Connector.tenant_id == tenant_id))
        target = _connector_target(connector, tenant_id)
        if target is None:
            if value.enabled or value.connector_id != profile.connector_id:
                raise ValidationError(
                    "INVALID_SQL_EXECUTION_CONNECTOR",
                    "Execution profile requires a complete PostgreSQL connector owned by the managed tenant",
                )
            connector_identity_fingerprint = profile.connector_identity_fingerprint
        else:
            connector_identity_fingerprint = target.identity_fingerprint
        active_binding_schemas = {
            binding.catalog_schema
            for binding in BusinessDocumentSqlCatalogBinding.select(BusinessDocumentSqlCatalogBinding.catalog_schema).where(
                (BusinessDocumentSqlCatalogBinding.tenant_id == tenant_id)
                & (BusinessDocumentSqlCatalogBinding.execution_profile_id == profile_id)
                & (BusinessDocumentSqlCatalogBinding.enabled == True)  # noqa: E712
            )
        }
        missing = sorted(active_binding_schemas - set(value.allowed_schemas))
        if missing:
            raise ValidationError(
                "SQL_EXECUTION_PROFILE_ORPHANS_BINDINGS",
                f"allowed_schemas would orphan active bindings: {', '.join(missing)}",
            )
        try:
            updated = (
                BusinessDocumentSqlExecutionProfile.update(
                    **_profile_values(value, actor_id, connector_identity_fingerprint),
                    version=BusinessDocumentSqlExecutionProfile.version + 1,
                )
                .where(
                    (BusinessDocumentSqlExecutionProfile.id == profile_id)
                    & (BusinessDocumentSqlExecutionProfile.tenant_id == tenant_id)
                    & (BusinessDocumentSqlExecutionProfile.version == expected_version)
                )
                .execute()
            )
        except IntegrityError as exc:
            raise ConflictError("SQL_EXECUTION_PROFILE_EXISTS", "An execution profile with this name already exists") from exc
        if updated != 1:
            current = BusinessDocumentSqlExecutionProfile.get_or_none((BusinessDocumentSqlExecutionProfile.id == profile_id) & (BusinessDocumentSqlExecutionProfile.tenant_id == tenant_id))
            raise ConflictError(
                "SQL_EXECUTION_PROFILE_VERSION_CONFLICT",
                "Execution profile changed; reload it before saving",
                {"current_version": current.version if current is not None else None},
            )
        profile = BusinessDocumentSqlExecutionProfile.get_by_id(profile_id)
        _audit("sql_execution_profile.updated", actor_id, tenant_id, "sql_execution_profile", profile_id)
        return _serialize_profile(profile, connector)

    @classmethod
    def list_bindings(cls, actor_id: str, is_admin: bool) -> dict[str, Any]:
        _require_admin(is_admin)
        tenant_id = _registry_tenant_id()
        bindings = (
            BusinessDocumentSqlCatalogBinding.select()
            .where(BusinessDocumentSqlCatalogBinding.tenant_id == tenant_id)
            .order_by(
                BusinessDocumentSqlCatalogBinding.catalog_service.asc(),
                BusinessDocumentSqlCatalogBinding.catalog_database.asc(),
                BusinessDocumentSqlCatalogBinding.catalog_schema.asc(),
            )
        )
        return {"schema_version": "1", "items": [_serialize_binding(binding) for binding in bindings]}

    @classmethod
    def _require_binding_profile(
        cls,
        value: CatalogBindingInput,
        tenant_id: str,
    ) -> BusinessDocumentSqlExecutionProfile:
        profile = BusinessDocumentSqlExecutionProfile.get_or_none((BusinessDocumentSqlExecutionProfile.id == value.execution_profile_id) & (BusinessDocumentSqlExecutionProfile.tenant_id == tenant_id))
        if profile is None:
            raise _not_found("execution_profile")
        allowed = profile.allowed_schemas if isinstance(profile.allowed_schemas, list) else []
        if value.catalog_schema not in allowed:
            raise ValidationError(
                "SQL_CATALOG_SCHEMA_NOT_ALLOWED",
                "Catalog schema is not allowed by the selected execution profile",
            )
        connector, _ = _require_connector(profile.connector_id, tenant_id)
        record = _profile_record(profile, connector)
        if not record.policy_valid or not record.connector_available:
            raise ValidationError(
                "SQL_EXECUTION_PROFILE_UNAVAILABLE",
                "Execution profile policy or connector identity must be refreshed before binding it",
            )
        return profile

    @classmethod
    def create_binding(cls, actor_id: str, payload: Any, is_admin: bool) -> dict[str, Any]:
        _require_admin(is_admin)
        try:
            value, _ = parse_catalog_binding_payload(payload)
        except ExecutionRegistryValidationError as exc:
            raise ValidationError("INVALID_SQL_CATALOG_BINDING", str(exc)) from exc
        tenant_id = _registry_tenant_id()
        cls._require_binding_profile(value, tenant_id)
        binding_id = get_uuid()
        try:
            with BusinessDocumentSqlCatalogBinding._meta.database.atomic():
                BusinessDocumentSqlCatalogBinding.create(
                    id=binding_id,
                    tenant_id=tenant_id,
                    created_by=actor_id,
                    version=1,
                    **_binding_values(value, actor_id),
                )
        except IntegrityError as exc:
            raise ConflictError("SQL_CATALOG_BINDING_EXISTS", "This catalog scope is already bound to the profile") from exc
        binding = BusinessDocumentSqlCatalogBinding.get_by_id(binding_id)
        _audit("sql_catalog_binding.created", actor_id, tenant_id, "sql_catalog_binding", binding_id)
        return _serialize_binding(binding)

    @classmethod
    def update_binding(cls, actor_id: str, binding_id: str, payload: Any, is_admin: bool) -> dict[str, Any]:
        _require_admin(is_admin)
        if _is_disable_payload(payload):
            expected_version = _disable_version(payload, "INVALID_SQL_CATALOG_BINDING")
            tenant_id = _registry_tenant_id()
            binding = _disable_record(
                BusinessDocumentSqlCatalogBinding,
                binding_id,
                tenant_id,
                actor_id,
                expected_version,
                kind="catalog_binding",
                conflict_code="SQL_CATALOG_BINDING_VERSION_CONFLICT",
                conflict_message="Catalog binding changed; reload it before saving",
            )
            _audit("sql_catalog_binding.updated", actor_id, tenant_id, "sql_catalog_binding", binding_id)
            return _serialize_binding(binding)
        try:
            value, expected_version = parse_catalog_binding_payload(payload, update=True)
        except ExecutionRegistryValidationError as exc:
            raise ValidationError("INVALID_SQL_CATALOG_BINDING", str(exc)) from exc
        tenant_id = _registry_tenant_id()
        binding = BusinessDocumentSqlCatalogBinding.get_or_none((BusinessDocumentSqlCatalogBinding.id == binding_id) & (BusinessDocumentSqlCatalogBinding.tenant_id == tenant_id))
        if binding is None:
            raise _not_found("catalog_binding")
        if not _is_binding_disable_only(binding, value):
            cls._require_binding_profile(value, tenant_id)
        try:
            updated = (
                BusinessDocumentSqlCatalogBinding.update(
                    **_binding_values(value, actor_id),
                    version=BusinessDocumentSqlCatalogBinding.version + 1,
                )
                .where(
                    (BusinessDocumentSqlCatalogBinding.id == binding_id) & (BusinessDocumentSqlCatalogBinding.tenant_id == tenant_id) & (BusinessDocumentSqlCatalogBinding.version == expected_version)
                )
                .execute()
            )
        except IntegrityError as exc:
            raise ConflictError("SQL_CATALOG_BINDING_EXISTS", "This catalog scope is already bound to the profile") from exc
        if updated != 1:
            current = BusinessDocumentSqlCatalogBinding.get_or_none((BusinessDocumentSqlCatalogBinding.id == binding_id) & (BusinessDocumentSqlCatalogBinding.tenant_id == tenant_id))
            raise ConflictError(
                "SQL_CATALOG_BINDING_VERSION_CONFLICT",
                "Catalog binding changed; reload it before saving",
                {"current_version": current.version if current is not None else None},
            )
        binding = BusinessDocumentSqlCatalogBinding.get_by_id(binding_id)
        _audit("sql_catalog_binding.updated", actor_id, tenant_id, "sql_catalog_binding", binding_id)
        return _serialize_binding(binding)

    @classmethod
    def resolve(
        cls,
        actor_id: str,
        payload: Any,
        is_admin: bool,
        access_role: str,
        *,
        catalog_access_check: Callable[[str, bool], bool] | None = None,
    ) -> dict[str, Any]:
        BusinessDocumentAccess(
            actor_id=actor_id,
            assigned_role=access_role,
            is_admin=is_admin,
        ).require_create()
        _require_catalog_access(actor_id, is_admin, catalog_access_check)
        try:
            command = parse_resolve_execution_profile_command(payload)
        except ExecutionRegistryValidationError as exc:
            raise ValidationError("INVALID_SQL_EXECUTION_BINDING_REQUEST", str(exc)) from exc
        tenant_id = _registry_tenant_id()
        profile_models = list(BusinessDocumentSqlExecutionProfile.select().where(BusinessDocumentSqlExecutionProfile.tenant_id == tenant_id))
        connectors = _connector_map(tenant_id, (profile.connector_id for profile in profile_models))
        profiles = [_profile_record(profile, connectors.get(profile.connector_id)) for profile in profile_models]
        bindings = [_binding_record(binding) for binding in BusinessDocumentSqlCatalogBinding.select().where(BusinessDocumentSqlCatalogBinding.tenant_id == tenant_id)]
        try:
            return resolve_execution_profile(command, profiles, bindings)
        except ExecutionRegistryValidationError as exc:
            raise ValidationError("INVALID_SQL_EXECUTION_BINDING_REQUEST", str(exc)) from exc
