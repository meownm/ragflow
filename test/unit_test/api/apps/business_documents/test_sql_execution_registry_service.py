#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

import pytest
from peewee import SqliteDatabase


if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents import sql_execution_registry as registry_module
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError, ValidationError
from api.apps.business_documents.sql_execution_registry import BusinessDocumentSqlExecutionRegistryService
from api.db.db_models import (
    BusinessDocumentSqlCatalogBinding,
    BusinessDocumentSqlExecutionProfile,
    Connector,
)


TENANT = "tenant-1"
ADMIN = "admin-1"


def test_registry_requires_one_configured_central_owner(monkeypatch):
    calls = []

    def missing_owner(*args):
        calls.append(args)
        raise registry_module.ManagedResourceConfigurationError("missing")

    monkeypatch.setattr(registry_module.ManagedResourceService, "owner_id", missing_owner)

    with pytest.raises(BusinessDocumentError) as unavailable:
        registry_module._registry_tenant_id()

    assert calls == [()]
    assert unavailable.value.code == "SQL_EXECUTION_REGISTRY_UNAVAILABLE"
    assert unavailable.value.status == 503


@pytest.fixture()
def database(monkeypatch):
    database = SqliteDatabase(":memory:")
    tables = (
        Connector,
        BusinessDocumentSqlExecutionProfile,
        BusinessDocumentSqlCatalogBinding,
    )
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        monkeypatch.setattr(registry_module, "_registry_tenant_id", lambda: TENANT)
        monkeypatch.setattr(registry_module, "_catalog_access_allowed", lambda _actor_id, _is_admin: True)
        monkeypatch.setattr(registry_module, "record_audit_event", lambda **_kwargs: "audit-1")
        yield database
        database.drop_tables(tables)
        database.close()


def _connector(*, source: str = "postgresql", complete: bool = True) -> Connector:
    credentials = {"username": "reader", "password": "secret"} if complete else {}
    return Connector.create(
        id="connector-1",
        tenant_id=TENANT,
        name="Warehouse PostgreSQL",
        source=source,
        input_type="poll",
        config={
            "host": "database.internal" if complete else "",
            "port": 5432,
            "database": "analytics" if complete else "",
            "query": "DELETE FROM must_never_be_used",
            "credentials": credentials,
        },
    )


def _profile_payload(**overrides) -> dict:
    return {
        "schema_version": "1",
        "name": "Warehouse RO",
        "connector_id": "connector-1",
        "dialect": "postgres",
        "allowed_schemas": ["dwh", "ref"],
        "statement_timeout_ms": 30_000,
        "max_rows": 1_000,
        "max_result_bytes": 5_000_000,
        "enabled": True,
        **overrides,
    }


def _binding_payload(profile_id: str, *, schema: str = "dwh", **overrides) -> dict:
    return {
        "schema_version": "1",
        "catalog_service": "warehouse",
        "catalog_database": "analytics",
        "catalog_schema": schema,
        "execution_profile_id": profile_id,
        "enabled": True,
        **overrides,
    }


def _table(entity_id: str, fqn: str) -> dict:
    schema, name = fqn.split(".")
    return {
        "id": entity_id,
        "fqn": fqn,
        "name": name,
        "display_name": None,
        "technical_name": name,
        "description": f"Fixture {fqn}",
        "version": 1,
        "updated_at": "2026-09-14T08:00:00+00:00",
        "service": "warehouse",
        "database": "analytics",
        "schema": schema,
        "owners": [],
        "domains": [],
        "tags": [],
        "glossary_terms": [],
        "url": "",
        "matched_by": ["fixture"],
        "column_count": 1,
        "loaded_column_count": 1,
        "columns_truncated": False,
        "schema_loaded": True,
        "schema_fingerprint": f"sha256:{entity_id}",
        "columns": [
            {
                "id": f"{fqn}.id",
                "name": "id",
                "fqn": f"{fqn}.id",
                "data_type": "INTEGER",
                "description": "Identifier",
                "constraint": "PRIMARY_KEY",
                "glossary_terms": [],
                "selected": True,
            }
        ],
        "table_constraints": [],
    }


def _resolve_payload() -> dict:
    tables = [_table("orders", "dwh.order_fact"), _table("statuses", "ref.order_status")]
    snapshot = {
        "format": "ragflow-sql-schema-snapshot",
        "schema_version": "1",
        "status": "READY",
        "original_requirements": "Покажи заказы с названием статуса.",
        "source": {"stale": False},
        "requirements": [
            {
                "term": table["name"],
                "state": "CONFIRMED",
                "decision": "USER",
                "interpretation": None,
                "candidates": [],
                "selected_table": table,
            }
            for table in tables
        ],
        "warnings": [],
    }
    return {
        "schema_version": "1",
        "schema_snapshot": snapshot,
        "accepted_requirements": snapshot["original_requirements"],
        "accepted_schema": [
            {
                "entity_id": table["id"],
                "version": table["version"],
                "schema_fingerprint": table["schema_fingerprint"],
            }
            for table in tables
        ],
        "selected_profile_id": None,
    }


def test_management_requires_admin_before_parsing_or_reading_registry(database):
    with pytest.raises(PermissionDeniedError):
        BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, {"malformed": True}, False)

    assert BusinessDocumentSqlExecutionProfile.select().count() == 0


def test_resolve_requires_catalog_acl_before_parsing_or_reading_registry(database, monkeypatch):
    monkeypatch.setattr(registry_module, "_catalog_access_allowed", lambda _actor_id, _is_admin: False)

    with pytest.raises(PermissionDeniedError):
        BusinessDocumentSqlExecutionRegistryService.resolve(
            "author-1",
            {"malformed": True},
            False,
            "AUTHOR_CREATOR",
        )

    assert BusinessDocumentSqlExecutionProfile.select().count() == 0


def test_resolve_maps_catalog_acl_failure_without_reading_registry(database, monkeypatch):
    def failed_check(_actor_id, _is_admin):
        raise RuntimeError("ACL backend unavailable")

    monkeypatch.setattr(registry_module, "_catalog_access_allowed", failed_check)

    with pytest.raises(BusinessDocumentError) as unavailable:
        BusinessDocumentSqlExecutionRegistryService.resolve(
            "author-1",
            _resolve_payload(),
            False,
            "AUTHOR_CREATOR",
        )

    assert unavailable.value.code == "SQL_EXECUTION_CATALOG_AUTH_UNAVAILABLE"
    assert unavailable.value.status == 503


def test_profile_rejects_non_postgres_or_incomplete_connector(database):
    _connector(source="mysql")
    with pytest.raises(ValidationError) as rejected_type:
        BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)
    assert rejected_type.value.code == "INVALID_SQL_EXECUTION_CONNECTOR"

    Connector.delete().execute()
    _connector(complete=False)
    with pytest.raises(ValidationError) as rejected_config:
        BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)
    assert rejected_config.value.code == "INVALID_SQL_EXECUTION_CONNECTOR"


def test_profile_list_is_safe_and_never_serializes_credentials_or_ingestion_query(database):
    _connector()
    created = BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)

    listed = BusinessDocumentSqlExecutionRegistryService.list_profiles(ADMIN, True)
    encoded = str(listed)

    assert created["version"] == 1
    assert created["connector"] == {
        "id": "connector-1",
        "name": "Warehouse PostgreSQL",
        "source": "postgresql",
        "database": "analytics",
    }
    assert "secret" not in encoded
    assert "DELETE" not in encoded
    assert "host" not in encoded


def test_binding_requires_profile_allowlist_and_uses_optimistic_versions(database):
    _connector()
    profile = BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)

    with pytest.raises(ValidationError) as rejected:
        BusinessDocumentSqlExecutionRegistryService.create_binding(
            ADMIN,
            _binding_payload(profile["id"], schema="private"),
            True,
        )
    assert rejected.value.code == "SQL_CATALOG_SCHEMA_NOT_ALLOWED"

    binding = BusinessDocumentSqlExecutionRegistryService.create_binding(
        ADMIN,
        _binding_payload(profile["id"]),
        True,
    )
    updated = BusinessDocumentSqlExecutionRegistryService.update_binding(
        ADMIN,
        binding["id"],
        _binding_payload(profile["id"], expected_version=1, enabled=False),
        True,
    )
    assert updated["version"] == 2
    assert updated["enabled"] is False

    with pytest.raises(ConflictError) as stale:
        BusinessDocumentSqlExecutionRegistryService.update_binding(
            ADMIN,
            binding["id"],
            _binding_payload(profile["id"], expected_version=1),
            True,
        )
    assert stale.value.code == "SQL_CATALOG_BINDING_VERSION_CONFLICT"


def test_profile_update_cannot_orphan_an_active_binding(database):
    _connector()
    profile = BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)
    BusinessDocumentSqlExecutionRegistryService.create_binding(
        ADMIN,
        _binding_payload(profile["id"], schema="ref"),
        True,
    )

    with pytest.raises(ValidationError) as rejected:
        BusinessDocumentSqlExecutionRegistryService.update_profile(
            ADMIN,
            profile["id"],
            _profile_payload(allowed_schemas=["dwh"], expected_version=1),
            True,
        )
    assert rejected.value.code == "SQL_EXECUTION_PROFILE_ORPHANS_BINDINGS"


def test_creator_resolves_registry_without_receiving_connector_identity(database):
    _connector()
    profile = BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)
    for schema in ("dwh", "ref"):
        BusinessDocumentSqlExecutionRegistryService.create_binding(
            ADMIN,
            _binding_payload(profile["id"], schema=schema),
            True,
        )

    result = BusinessDocumentSqlExecutionRegistryService.resolve(
        "author-1",
        _resolve_payload(),
        False,
        "AUTHOR_CREATOR",
    )

    assert result["status"] == "BOUND"
    encoded = str(result)
    assert "connector-1" not in encoded
    assert "secret" not in encoded


def test_deleted_connector_invalidates_resolution_without_deleting_registry_history(database):
    _connector()
    profile = BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)
    for schema in ("dwh", "ref"):
        BusinessDocumentSqlExecutionRegistryService.create_binding(
            ADMIN,
            _binding_payload(profile["id"], schema=schema),
            True,
        )
    Connector.delete().execute()

    result = BusinessDocumentSqlExecutionRegistryService.resolve(
        "author-1",
        _resolve_payload(),
        False,
        "AUTHOR_CREATOR",
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "CATALOG_BINDING_MISSING"
    assert BusinessDocumentSqlExecutionProfile.get_by_id(profile["id"]).enabled is True


def test_deleted_connector_does_not_block_emergency_deactivation(database):
    _connector()
    profile = BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)
    binding = BusinessDocumentSqlExecutionRegistryService.create_binding(
        ADMIN,
        _binding_payload(profile["id"]),
        True,
    )
    Connector.delete().execute()

    disabled_binding = BusinessDocumentSqlExecutionRegistryService.update_binding(
        ADMIN,
        binding["id"],
        _binding_payload(profile["id"], expected_version=1, enabled=False),
        True,
    )
    disabled_profile = BusinessDocumentSqlExecutionRegistryService.update_profile(
        ADMIN,
        profile["id"],
        _profile_payload(expected_version=1, enabled=False),
        True,
    )

    assert disabled_binding["enabled"] is False
    assert disabled_profile["enabled"] is False


def test_missing_connector_cannot_be_hidden_by_retargeting_a_disabled_profile(database):
    _connector()
    profile = BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)
    Connector.delete().execute()

    with pytest.raises(ValidationError) as rejected:
        BusinessDocumentSqlExecutionRegistryService.update_profile(
            ADMIN,
            profile["id"],
            _profile_payload(
                connector_id="missing-connector",
                expected_version=1,
                enabled=False,
            ),
            True,
        )

    assert rejected.value.code == "INVALID_SQL_EXECUTION_CONNECTOR"


def test_connector_target_drift_requires_explicit_profile_version_update(database):
    connector = _connector()
    profile = BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)
    for schema in ("dwh", "ref"):
        BusinessDocumentSqlExecutionRegistryService.create_binding(
            ADMIN,
            _binding_payload(profile["id"], schema=schema),
            True,
        )

    rotated = dict(connector.config)
    rotated["credentials"] = {"username": "reader", "password": "rotated-secret"}
    Connector.update(config=rotated).where(Connector.id == connector.id).execute()
    assert BusinessDocumentSqlExecutionRegistryService.resolve("author-1", _resolve_payload(), False, "AUTHOR_CREATOR")["status"] == "BOUND"

    retargeted = dict(rotated)
    retargeted["host"] = "new-database.internal"
    Connector.update(config=retargeted).where(Connector.id == connector.id).execute()

    unavailable = BusinessDocumentSqlExecutionRegistryService.resolve(
        "author-1",
        _resolve_payload(),
        False,
        "AUTHOR_CREATOR",
    )
    listed = BusinessDocumentSqlExecutionRegistryService.list_profiles(ADMIN, True)["items"][0]
    assert unavailable["status"] == "UNAVAILABLE"
    assert listed["connector_available"] is True
    assert listed["connector_identity_matches"] is False
    assert listed["available"] is False

    refreshed = BusinessDocumentSqlExecutionRegistryService.update_profile(
        ADMIN,
        profile["id"],
        _profile_payload(expected_version=1),
        True,
    )
    assert refreshed["version"] == 2
    assert refreshed["connector_identity_matches"] is True
    assert BusinessDocumentSqlExecutionRegistryService.resolve("author-1", _resolve_payload(), False, "AUTHOR_CREATOR")["status"] == "BOUND"


def test_corrupt_stored_policy_is_visible_to_admin_but_never_resolved(database):
    _connector()
    profile = BusinessDocumentSqlExecutionRegistryService.create_profile(ADMIN, _profile_payload(), True)
    for schema in ("dwh", "ref"):
        BusinessDocumentSqlExecutionRegistryService.create_binding(
            ADMIN,
            _binding_payload(profile["id"], schema=schema),
            True,
        )
    BusinessDocumentSqlExecutionProfile.update(max_rows=50_000).where(BusinessDocumentSqlExecutionProfile.id == profile["id"]).execute()

    result = BusinessDocumentSqlExecutionRegistryService.resolve(
        "author-1",
        _resolve_payload(),
        False,
        "AUTHOR_CREATOR",
    )
    listed = BusinessDocumentSqlExecutionRegistryService.list_profiles(ADMIN, True)["items"][0]

    assert result["status"] == "UNAVAILABLE"
    assert listed["policy_valid"] is False
    assert listed["available"] is False

    disabled = BusinessDocumentSqlExecutionRegistryService.update_profile(
        ADMIN,
        profile["id"],
        {"schema_version": "1", "enabled": False, "expected_version": 1},
        True,
    )
    assert disabled["enabled"] is False
    assert disabled["version"] == 2
