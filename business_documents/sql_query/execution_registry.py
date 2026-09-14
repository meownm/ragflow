"""Pure execution-profile and catalog-binding contracts.

The registry resolves an accepted OpenMetadata snapshot to one safe execution
profile. It never reads credentials, opens a database connection, or executes
SQL; those responsibilities remain behind infrastructure adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from business_documents.sql_query.query_specification import (
    AcceptedSchemaEntry,
    QuerySpecificationValidationError,
    SchemaSnapshot,
    parse_accepted_schema,
    parse_schema_snapshot,
    schema_acceptance_issues,
)


EXECUTION_REGISTRY_VERSION = "1"
EXECUTION_DIALECT = "postgres"
MAX_ALLOWED_SCHEMAS = 64
MAX_STATEMENT_TIMEOUT_MS = 120_000
MAX_EXECUTION_ROWS = 10_000
MAX_RESULT_BYTES = 50_000_000

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class ExecutionRegistryValidationError(ValueError):
    """A registry command or stored record violates the closed contract."""


@dataclass(frozen=True, slots=True)
class ExecutionProfileInput:
    name: str
    connector_id: str
    dialect: str
    allowed_schemas: tuple[str, ...]
    statement_timeout_ms: int
    max_rows: int
    max_result_bytes: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class CatalogBindingInput:
    catalog_service: str
    catalog_database: str
    catalog_schema: str
    execution_profile_id: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class ExecutionProfileRecord:
    id: str
    name: str
    dialect: str
    allowed_schemas: tuple[str, ...]
    statement_timeout_ms: int
    max_rows: int
    max_result_bytes: int
    enabled: bool
    version: int
    policy_valid: bool
    connector_available: bool
    target_database: str

    @property
    def policy_fingerprint(self) -> str:
        encoded = json.dumps(
            {
                "id": self.id,
                "dialect": self.dialect,
                "allowed_schemas": list(self.allowed_schemas),
                "statement_timeout_ms": self.statement_timeout_ms,
                "max_rows": self.max_rows,
                "max_result_bytes": self.max_result_bytes,
                "enabled": self.enabled,
                "version": self.version,
                "target_database": self.target_database,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "dialect": self.dialect,
            "statement_timeout_ms": self.statement_timeout_ms,
            "max_rows": self.max_rows,
            "max_result_bytes": self.max_result_bytes,
            "version": self.version,
            "policy_fingerprint": self.policy_fingerprint,
            "target_database": self.target_database,
        }


@dataclass(frozen=True, slots=True)
class CatalogBindingRecord:
    id: str
    catalog_service: str
    catalog_database: str
    catalog_schema: str
    execution_profile_id: str
    enabled: bool
    version: int


@dataclass(frozen=True, slots=True)
class CatalogScope:
    service: str
    database: str
    schema: str
    table_ids: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str, str]:
        return self.service, self.database, self.schema

    def to_api(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "database": self.database,
            "schema": self.schema,
            "table_ids": list(self.table_ids),
        }


@dataclass(frozen=True, slots=True)
class ResolveExecutionProfileCommand:
    snapshot: SchemaSnapshot
    accepted_requirements: str
    accepted_schema: tuple[AcceptedSchemaEntry, ...]
    selected_profile_id: str | None


def _record(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionRegistryValidationError(f"{field} must be an object")
    return value


def _closed(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ExecutionRegistryValidationError(f"Unknown {field} fields: {', '.join(sorted(unknown))}")


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionRegistryValidationError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise ExecutionRegistryValidationError(f"{field} exceeds {maximum} characters")
    return result


def _safe_id(value: Any, field: str) -> str:
    result = _text(value, field, 32)
    if not _SAFE_ID.fullmatch(result):
        raise ExecutionRegistryValidationError(f"{field} must be a safe identifier")
    return result


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field, 128)
    if not _IDENTIFIER.fullmatch(result):
        raise ExecutionRegistryValidationError(f"{field} must be an unquoted SQL identifier")
    return result


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ExecutionRegistryValidationError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ExecutionRegistryValidationError(f"{field} must be a boolean")
    return value


def _version(value: Any, field: str = "expected_version") -> int:
    return _integer(value, field, 1, 2_147_483_647)


def _allowed_schemas(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_ALLOWED_SCHEMAS:
        raise ExecutionRegistryValidationError(f"allowed_schemas must contain from 1 to {MAX_ALLOWED_SCHEMAS} items")
    result = tuple(_identifier(item, f"allowed_schemas[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise ExecutionRegistryValidationError("allowed_schemas must be unique")
    return result


def parse_execution_profile_payload(value: Any, *, update: bool = False) -> tuple[ExecutionProfileInput, int | None]:
    payload = _record(value, "request")
    fields = {
        "schema_version",
        "name",
        "connector_id",
        "dialect",
        "allowed_schemas",
        "statement_timeout_ms",
        "max_rows",
        "max_result_bytes",
        "enabled",
    }
    if update:
        fields.add("expected_version")
    _closed(payload, fields, "request")
    if payload.get("schema_version") != EXECUTION_REGISTRY_VERSION:
        raise ExecutionRegistryValidationError("schema_version is unsupported")
    dialect = _text(payload.get("dialect"), "dialect", 32)
    if dialect != EXECUTION_DIALECT:
        raise ExecutionRegistryValidationError(f"dialect must be {EXECUTION_DIALECT}")
    profile = ExecutionProfileInput(
        name=_text(payload.get("name"), "name", 128),
        connector_id=_safe_id(payload.get("connector_id"), "connector_id"),
        dialect=dialect,
        allowed_schemas=_allowed_schemas(payload.get("allowed_schemas")),
        statement_timeout_ms=_integer(payload.get("statement_timeout_ms"), "statement_timeout_ms", 100, MAX_STATEMENT_TIMEOUT_MS),
        max_rows=_integer(payload.get("max_rows"), "max_rows", 1, MAX_EXECUTION_ROWS),
        max_result_bytes=_integer(payload.get("max_result_bytes"), "max_result_bytes", 1_024, MAX_RESULT_BYTES),
        enabled=_boolean(payload.get("enabled"), "enabled"),
    )
    return profile, _version(payload.get("expected_version")) if update else None


def parse_catalog_binding_payload(value: Any, *, update: bool = False) -> tuple[CatalogBindingInput, int | None]:
    payload = _record(value, "request")
    fields = {
        "schema_version",
        "catalog_service",
        "catalog_database",
        "catalog_schema",
        "execution_profile_id",
        "enabled",
    }
    if update:
        fields.add("expected_version")
    _closed(payload, fields, "request")
    if payload.get("schema_version") != EXECUTION_REGISTRY_VERSION:
        raise ExecutionRegistryValidationError("schema_version is unsupported")
    binding = CatalogBindingInput(
        catalog_service=_text(payload.get("catalog_service"), "catalog_service", 128),
        catalog_database=_text(payload.get("catalog_database"), "catalog_database", 128),
        catalog_schema=_identifier(payload.get("catalog_schema"), "catalog_schema"),
        execution_profile_id=_safe_id(payload.get("execution_profile_id"), "execution_profile_id"),
        enabled=_boolean(payload.get("enabled"), "enabled"),
    )
    return binding, _version(payload.get("expected_version")) if update else None


def parse_registry_disable_payload(value: Any) -> int:
    payload = _record(value, "request")
    _closed(payload, {"schema_version", "enabled", "expected_version"}, "request")
    if payload.get("schema_version") != EXECUTION_REGISTRY_VERSION:
        raise ExecutionRegistryValidationError("schema_version is unsupported")
    if _boolean(payload.get("enabled"), "enabled"):
        raise ExecutionRegistryValidationError("disable command requires enabled=false")
    return _version(payload.get("expected_version"))


def parse_resolve_execution_profile_command(value: Any) -> ResolveExecutionProfileCommand:
    payload = _record(value, "request")
    _closed(
        payload,
        {"schema_version", "schema_snapshot", "accepted_requirements", "accepted_schema", "selected_profile_id"},
        "request",
    )
    if payload.get("schema_version") != EXECUTION_REGISTRY_VERSION:
        raise ExecutionRegistryValidationError("schema_version is unsupported")
    accepted_requirements = _text(payload.get("accepted_requirements"), "accepted_requirements", 20_000)
    try:
        snapshot = parse_schema_snapshot(payload.get("schema_snapshot"))
        accepted_schema = parse_accepted_schema(payload.get("accepted_schema"))
    except QuerySpecificationValidationError as exc:
        raise ExecutionRegistryValidationError(str(exc)) from exc
    issues = schema_acceptance_issues(snapshot, accepted_requirements, accepted_schema)
    if issues:
        raise ExecutionRegistryValidationError(issues[0].message)
    selected = payload.get("selected_profile_id")
    return ResolveExecutionProfileCommand(
        snapshot=snapshot,
        accepted_requirements=accepted_requirements,
        accepted_schema=accepted_schema,
        selected_profile_id=None if selected is None else _safe_id(selected, "selected_profile_id"),
    )


def _catalog_scopes(snapshot: SchemaSnapshot) -> tuple[CatalogScope, ...]:
    table_ids: dict[tuple[str, str, str], list[str]] = {}
    for table in snapshot.tables:
        table_ids.setdefault((table.service, table.database, table.schema), []).append(table.id)
    return tuple(CatalogScope(service=key[0], database=key[1], schema=key[2], table_ids=tuple(ids)) for key, ids in table_ids.items())


def _binding_index(
    bindings: Sequence[CatalogBindingRecord],
    active_profiles: Mapping[str, ExecutionProfileRecord],
) -> dict[tuple[str, str, str], dict[str, CatalogBindingRecord]]:
    result: dict[tuple[str, str, str], dict[str, CatalogBindingRecord]] = {}
    seen: set[tuple[tuple[str, str, str], str]] = set()
    for binding in bindings:
        if not binding.enabled:
            continue
        scope = (binding.catalog_service, binding.catalog_database, binding.catalog_schema)
        binding_key = (scope, binding.execution_profile_id)
        if binding_key in seen:
            raise ExecutionRegistryValidationError("Registry contains duplicate active bindings for one catalog scope and profile")
        seen.add(binding_key)
        profile = active_profiles.get(binding.execution_profile_id)
        if profile is None or binding.catalog_schema not in profile.allowed_schemas:
            continue
        profiles = result.setdefault(scope, {})
        profiles[binding.execution_profile_id] = binding
    return result


def _resolution_response(
    command: ResolveExecutionProfileCommand,
    scopes: Sequence[CatalogScope],
    *,
    status: str,
    reason: str | None,
    unresolved: Sequence[dict[str, Any]] = (),
    candidates: Sequence[ExecutionProfileRecord] = (),
    selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": EXECUTION_REGISTRY_VERSION,
        "status": status,
        "reason": reason,
        "snapshot_fingerprint": command.snapshot.fingerprint,
        "catalog_scopes": [scope.to_api() for scope in scopes],
        "unresolved_catalog_scopes": list(unresolved),
        "candidates": [profile.to_public() for profile in candidates],
        "selection": selection,
    }


def _active_profile_index(
    profiles: Sequence[ExecutionProfileRecord],
) -> dict[str, ExecutionProfileRecord]:
    return {profile.id: profile for profile in profiles if profile.enabled and profile.policy_valid and profile.connector_available and profile.dialect == EXECUTION_DIALECT}


def _scope_candidate_index(
    scopes: Sequence[CatalogScope],
    binding_index: Mapping[tuple[str, str, str], Mapping[str, CatalogBindingRecord]],
) -> tuple[list[tuple[CatalogScope, dict[str, CatalogBindingRecord]]], list[dict[str, Any]]]:
    scope_candidates: list[tuple[CatalogScope, dict[str, CatalogBindingRecord]]] = []
    unresolved: list[dict[str, Any]] = []
    for scope in scopes:
        candidates = dict(binding_index.get(scope.key, {}))
        scope_candidates.append((scope, candidates))
        if not candidates:
            unresolved.append(scope.to_api())
    return scope_candidates, unresolved


def _common_profiles(
    active_profiles: Mapping[str, ExecutionProfileRecord],
    scope_candidates: Sequence[tuple[CatalogScope, Mapping[str, CatalogBindingRecord]]],
) -> tuple[set[str], list[ExecutionProfileRecord]]:
    common_ids = set(active_profiles)
    for _, candidates in scope_candidates:
        common_ids.intersection_update(candidates)
    candidates = sorted(
        (active_profiles[profile_id] for profile_id in common_ids),
        key=lambda profile: (profile.name.casefold(), profile.id),
    )
    return common_ids, candidates


def resolve_execution_profile(
    command: ResolveExecutionProfileCommand,
    profiles: Sequence[ExecutionProfileRecord],
    bindings: Sequence[CatalogBindingRecord],
) -> dict[str, Any]:
    """Resolve every table in a snapshot to one common active profile."""

    active_profiles = _active_profile_index(profiles)
    binding_index = _binding_index(bindings, active_profiles)
    scopes = _catalog_scopes(command.snapshot)
    missing_identity = [scope.to_api() for scope in scopes if not all(scope.key)]
    if missing_identity:
        return _resolution_response(
            command,
            scopes,
            status="UNAVAILABLE",
            reason="CATALOG_IDENTITY_MISSING",
            unresolved=missing_identity,
        )
    scope_candidates, unresolved = _scope_candidate_index(scopes, binding_index)
    common_ids, candidates = _common_profiles(active_profiles, scope_candidates)

    if unresolved:
        return _resolution_response(
            command,
            scopes,
            status="UNAVAILABLE",
            reason="CATALOG_BINDING_MISSING",
            unresolved=unresolved,
        )
    if not candidates:
        return _resolution_response(
            command,
            scopes,
            status="UNAVAILABLE",
            reason="CROSS_PROFILE_QUERY_UNSUPPORTED",
        )

    selected_id = command.selected_profile_id
    if selected_id is not None and selected_id not in common_ids:
        raise ExecutionRegistryValidationError("selected_profile_id is not compatible with every catalog scope")
    if selected_id is None and len(candidates) > 1:
        return _resolution_response(
            command,
            scopes,
            status="NEEDS_SELECTION",
            reason="MULTIPLE_EXECUTION_PROFILES",
            candidates=candidates,
        )

    selected = active_profiles[selected_id] if selected_id is not None else candidates[0]
    selected_bindings = [candidate_map[selected.id] for _, candidate_map in scope_candidates]
    return _resolution_response(
        command,
        scopes,
        status="BOUND",
        reason=None,
        candidates=candidates,
        selection={
            "decision": "user" if selected_id is not None else "automatic_exact",
            "profile": selected.to_public(),
            "bindings": [{"binding_id": binding.id, "version": binding.version} for binding in selected_bindings],
        },
    )
