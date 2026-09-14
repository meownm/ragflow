#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.

from __future__ import annotations

from copy import deepcopy

import pytest

from business_documents.sql_query.execution_registry import (
    CatalogBindingRecord,
    ExecutionProfileRecord,
    ExecutionRegistryValidationError,
    parse_catalog_binding_payload,
    parse_execution_profile_payload,
    parse_registry_disable_payload,
    parse_resolve_execution_profile_command,
    resolve_execution_profile,
)


def _table(entity_id: str, fqn: str) -> dict:
    schema, name = fqn.split(".")
    return {
        "id": entity_id,
        "fqn": fqn,
        "name": name,
        "display_name": None,
        "technical_name": name,
        "description": f"Fixture {fqn}",
        "version": 7,
        "updated_at": "2026-09-14T08:00:00+00:00",
        "service": "warehouse",
        "database": "analytics",
        "schema": schema,
        "owners": ["DWH"],
        "domains": ["Sales"],
        "tags": ["gold"],
        "glossary_terms": [],
        "url": f"https://metadata.example/{fqn}",
        "matched_by": ["fixture"],
        "column_count": 1,
        "loaded_column_count": 1,
        "columns_truncated": False,
        "schema_loaded": True,
        "schema_fingerprint": f"sha256:{entity_id}-v7",
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


def _profile(profile_id: str, *, name: str = "Warehouse RO", available: bool = True) -> ExecutionProfileRecord:
    return ExecutionProfileRecord(
        id=profile_id,
        name=name,
        dialect="postgres",
        allowed_schemas=("dwh", "ref"),
        statement_timeout_ms=30_000,
        max_rows=1_000,
        max_result_bytes=5_000_000,
        enabled=True,
        version=3,
        policy_valid=True,
        connector_available=available,
        target_database="analytics",
    )


def _bindings(profile_id: str, *, prefix: str = "binding") -> list[CatalogBindingRecord]:
    return [
        CatalogBindingRecord(
            id=f"{prefix}-dwh",
            catalog_service="warehouse",
            catalog_database="analytics",
            catalog_schema="dwh",
            execution_profile_id=profile_id,
            enabled=True,
            version=2,
        ),
        CatalogBindingRecord(
            id=f"{prefix}-ref",
            catalog_service="warehouse",
            catalog_database="analytics",
            catalog_schema="ref",
            execution_profile_id=profile_id,
            enabled=True,
            version=2,
        ),
    ]


def test_profile_and_binding_payloads_are_closed_and_versioned():
    profile, expected_version = parse_execution_profile_payload(
        {
            "schema_version": "1",
            "name": "Warehouse read only",
            "connector_id": "connector-1",
            "dialect": "postgres",
            "allowed_schemas": ["dwh", "ref"],
            "statement_timeout_ms": 30_000,
            "max_rows": 1_000,
            "max_result_bytes": 5_000_000,
            "enabled": True,
            "expected_version": 4,
        },
        update=True,
    )
    binding, _ = parse_catalog_binding_payload(
        {
            "schema_version": "1",
            "catalog_service": "warehouse",
            "catalog_database": "analytics",
            "catalog_schema": "dwh",
            "execution_profile_id": "profile-1",
            "enabled": True,
        }
    )

    assert expected_version == 4
    assert profile.allowed_schemas == ("dwh", "ref")
    assert binding.catalog_schema == "dwh"

    with pytest.raises(ExecutionRegistryValidationError, match="Unknown request fields"):
        parse_catalog_binding_payload({"schema_version": "1", "secret": "must-not-pass"})

    assert parse_registry_disable_payload({"schema_version": "1", "enabled": False, "expected_version": 7}) == 7
    with pytest.raises(ExecutionRegistryValidationError, match="requires enabled=false"):
        parse_registry_disable_payload({"schema_version": "1", "enabled": True, "expected_version": 7})


def test_one_common_profile_is_bound_automatically_without_exposing_connector_id():
    command = parse_resolve_execution_profile_command(_resolve_payload())

    result = resolve_execution_profile(command, [_profile("profile-1")], _bindings("profile-1"))

    assert result["status"] == "BOUND"
    assert result["selection"]["decision"] == "automatic_exact"
    assert result["selection"]["profile"]["id"] == "profile-1"
    assert result["selection"]["profile"]["policy_fingerprint"].startswith("sha256:")
    assert "connector_id" not in result["selection"]["profile"]
    assert "allowed_schemas" not in result["selection"]["profile"]
    assert result["selection"]["bindings"] == [
        {"binding_id": "binding-dwh", "version": 2},
        {"binding_id": "binding-ref", "version": 2},
    ]


def test_multiple_common_profiles_require_explicit_selection():
    payload = _resolve_payload()
    command = parse_resolve_execution_profile_command(payload)
    profiles = [_profile("profile-1", name="Primary"), _profile("profile-2", name="Replica")]
    bindings = [*_bindings("profile-1", prefix="primary"), *_bindings("profile-2", prefix="replica")]

    unresolved = resolve_execution_profile(command, profiles, bindings)

    assert unresolved["status"] == "NEEDS_SELECTION"
    assert [profile["id"] for profile in unresolved["candidates"]] == ["profile-1", "profile-2"]
    assert unresolved["selection"] is None

    payload["selected_profile_id"] = "profile-2"
    selected = resolve_execution_profile(parse_resolve_execution_profile_command(payload), profiles, bindings)
    assert selected["status"] == "BOUND"
    assert selected["selection"]["decision"] == "user"
    assert selected["selection"]["profile"]["id"] == "profile-2"


def test_missing_scope_and_disjoint_profiles_fail_closed():
    command = parse_resolve_execution_profile_command(_resolve_payload())
    profile_one = _profile("profile-1")

    missing = resolve_execution_profile(command, [profile_one], _bindings("profile-1")[:1])
    assert missing["status"] == "UNAVAILABLE"
    assert missing["reason"] == "CATALOG_BINDING_MISSING"
    assert missing["unresolved_catalog_scopes"][0]["schema"] == "ref"

    profiles = [profile_one, _profile("profile-2")]
    disjoint = resolve_execution_profile(
        command,
        profiles,
        [_bindings("profile-1")[0], _bindings("profile-2")[1]],
    )
    assert disjoint["status"] == "UNAVAILABLE"
    assert disjoint["reason"] == "CROSS_PROFILE_QUERY_UNSUPPORTED"


def test_disabled_or_missing_connector_profile_is_not_a_candidate():
    command = parse_resolve_execution_profile_command(_resolve_payload())

    result = resolve_execution_profile(
        command,
        [_profile("profile-1", available=False)],
        _bindings("profile-1"),
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "CATALOG_BINDING_MISSING"


def test_selected_profile_must_cover_every_catalog_scope():
    payload = _resolve_payload()
    payload["selected_profile_id"] = "profile-2"
    command = parse_resolve_execution_profile_command(payload)

    with pytest.raises(ExecutionRegistryValidationError, match="not compatible"):
        resolve_execution_profile(command, [_profile("profile-1")], _bindings("profile-1"))


def test_stale_snapshot_is_rejected_before_registry_lookup():
    payload = deepcopy(_resolve_payload())
    payload["schema_snapshot"]["source"]["stale"] = True

    with pytest.raises(ExecutionRegistryValidationError, match="устарел"):
        parse_resolve_execution_profile_command(payload)


def test_legacy_snapshot_without_catalog_identity_can_still_parse_but_cannot_bind():
    payload = _resolve_payload()
    for requirement in payload["schema_snapshot"]["requirements"]:
        requirement["selected_table"].pop("service")
        requirement["selected_table"].pop("database")
        requirement["selected_table"].pop("schema")

    result = resolve_execution_profile(
        parse_resolve_execution_profile_command(payload),
        [_profile("profile-1")],
        _bindings("profile-1"),
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "CATALOG_IDENTITY_MISSING"
