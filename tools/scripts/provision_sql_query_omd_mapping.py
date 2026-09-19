#!/usr/bin/env python3
"""Provision the governed OpenMetadata-to-PostgreSQL SQL registry mapping.

Run this script in a configured RAGFlow application environment. The target
database password is accepted only through ``RAGFLOW_SQL_READER_PASSWORD`` so
it does not appear in the command line or in the sanitized JSON result.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path.cwd() if __file__ == "<stdin>" else Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CONNECTOR_NAME = "OMD bot PostgreSQL"
DEFAULT_PROFILE_NAME = "OMD bot PostgreSQL RO"
DEFAULT_SCHEMAS = ("public", "imoex", "rtts")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="host.docker.internal")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default="bot")
    parser.add_argument("--username", default="ragflow_sql_reader")
    parser.add_argument("--catalog-service", default="docker_postgres_bot")
    parser.add_argument("--catalog-database", default="bot")
    parser.add_argument("--connector-name", default=DEFAULT_CONNECTOR_NAME)
    parser.add_argument("--profile-name", default=DEFAULT_PROFILE_NAME)
    parser.add_argument("--schema", action="append", dest="schemas")
    return parser.parse_args()


def _password() -> str:
    value = os.getenv("RAGFLOW_SQL_READER_PASSWORD", "")
    if not value:
        raise RuntimeError("RAGFLOW_SQL_READER_PASSWORD must be set")
    return value


def _connector_config(args: argparse.Namespace, password: str) -> dict[str, Any]:
    return {
        "host": args.host,
        "port": args.port,
        "database": args.database,
        "query": "",
        "credentials": {"username": args.username, "password": password},
    }


def _upsert_connector(args: argparse.Namespace, tenant_id: str, password: str) -> tuple[Any, str]:
    from api.db import InputType
    from api.db.db_models import Connector
    from api.db.services.connector_service import ConnectorService
    from common.constants import TaskStatus
    from common.data_source.config import DocumentSource
    from common.misc_utils import get_uuid

    connector = Connector.get_or_none((Connector.tenant_id == tenant_id) & (Connector.source == DocumentSource.POSTGRESQL.value) & (Connector.name == args.connector_name))
    config = _connector_config(args, password)
    if connector is None:
        connector_id = get_uuid()
        ConnectorService.save(
            id=connector_id,
            tenant_id=tenant_id,
            name=args.connector_name,
            source=DocumentSource.POSTGRESQL.value,
            input_type=InputType.POLL.value,
            config=config,
            refresh_freq=0,
            prune_freq=0,
            timeout_secs=15,
            status=TaskStatus.UNSTART.value,
        )
        return Connector.get_by_id(connector_id), "created"

    desired = {
        "input_type": InputType.POLL.value,
        "config": config,
        "refresh_freq": 0,
        "prune_freq": 0,
        "timeout_secs": 15,
        "status": TaskStatus.UNSTART.value,
    }
    if any(getattr(connector, key) != value for key, value in desired.items()):
        ConnectorService.update_by_id(connector.id, desired)
        return Connector.get_by_id(connector.id), "updated"
    return connector, "unchanged"


def _profile_payload(args: argparse.Namespace, connector_id: str, schemas: tuple[str, ...]) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "name": args.profile_name,
        "connector_id": connector_id,
        "dialect": "postgres",
        "allowed_schemas": list(schemas),
        "statement_timeout_ms": 15_000,
        "max_rows": 1_000,
        "max_result_bytes": 5_000_000,
        "enabled": True,
    }


def _upsert_profile(
    args: argparse.Namespace,
    tenant_id: str,
    actor_id: str,
    connector_id: str,
    schemas: tuple[str, ...],
) -> tuple[dict[str, Any], str]:
    from api.apps.business_documents.sql_execution_registry import BusinessDocumentSqlExecutionRegistryService
    from api.db.db_models import BusinessDocumentSqlExecutionProfile

    profile = BusinessDocumentSqlExecutionProfile.get_or_none((BusinessDocumentSqlExecutionProfile.tenant_id == tenant_id) & (BusinessDocumentSqlExecutionProfile.name == args.profile_name))
    payload = _profile_payload(args, connector_id, schemas)
    if profile is None:
        return BusinessDocumentSqlExecutionRegistryService.create_profile(actor_id, payload, True), "created"

    current = BusinessDocumentSqlExecutionRegistryService.list_profiles(actor_id, True)["items"]
    public_profile = next(item for item in current if item["id"] == profile.id)
    comparable = {
        "name": public_profile["name"],
        "connector_id": public_profile["connector"]["id"],
        "dialect": public_profile["dialect"],
        "allowed_schemas": public_profile["allowed_schemas"],
        "statement_timeout_ms": public_profile["statement_timeout_ms"],
        "max_rows": public_profile["max_rows"],
        "max_result_bytes": public_profile["max_result_bytes"],
        "enabled": public_profile["enabled"],
    }
    expected = {key: value for key, value in payload.items() if key != "schema_version"}
    if comparable == expected and public_profile["connector_identity_matches"]:
        return public_profile, "unchanged"
    return (
        BusinessDocumentSqlExecutionRegistryService.update_profile(
            actor_id,
            profile.id,
            {**payload, "expected_version": profile.version},
            True,
        ),
        "updated",
    )


def _upsert_binding(
    args: argparse.Namespace,
    tenant_id: str,
    actor_id: str,
    profile_id: str,
    schema: str,
) -> tuple[dict[str, Any], str]:
    from api.apps.business_documents.sql_execution_registry import BusinessDocumentSqlExecutionRegistryService
    from api.db.db_models import BusinessDocumentSqlCatalogBinding

    scope = (
        (BusinessDocumentSqlCatalogBinding.tenant_id == tenant_id)
        & (BusinessDocumentSqlCatalogBinding.catalog_service == args.catalog_service)
        & (BusinessDocumentSqlCatalogBinding.catalog_database == args.catalog_database)
        & (BusinessDocumentSqlCatalogBinding.catalog_schema == schema)
    )
    conflicts = list(
        BusinessDocumentSqlCatalogBinding.select().where(
            scope & (BusinessDocumentSqlCatalogBinding.execution_profile_id != profile_id) & (BusinessDocumentSqlCatalogBinding.enabled == True)  # noqa: E712
        )
    )
    if conflicts:
        raise RuntimeError(f"Catalog scope {args.catalog_service}.{args.catalog_database}.{schema} has another active profile")

    binding = BusinessDocumentSqlCatalogBinding.get_or_none(scope & (BusinessDocumentSqlCatalogBinding.execution_profile_id == profile_id))
    payload = {
        "schema_version": "1",
        "catalog_service": args.catalog_service,
        "catalog_database": args.catalog_database,
        "catalog_schema": schema,
        "execution_profile_id": profile_id,
        "enabled": True,
    }
    if binding is None:
        return BusinessDocumentSqlExecutionRegistryService.create_binding(actor_id, payload, True), "created"
    if binding.enabled:
        items = BusinessDocumentSqlExecutionRegistryService.list_bindings(actor_id, True)["items"]
        return next(item for item in items if item["id"] == binding.id), "unchanged"
    return (
        BusinessDocumentSqlExecutionRegistryService.update_binding(
            actor_id,
            binding.id,
            {**payload, "expected_version": binding.version},
            True,
        ),
        "updated",
    )


def main() -> None:
    from api.db.services.managed_resource_service import ManagedResourceService

    args = _arguments()
    schemas = tuple(dict.fromkeys(args.schemas or DEFAULT_SCHEMAS))
    password = _password()
    actor_id = ManagedResourceService.owner_id()
    if not ManagedResourceService.user_is_superuser(actor_id):
        raise RuntimeError("The managed-resource owner must be an active superuser")
    tenant_id = actor_id

    connector, connector_action = _upsert_connector(args, tenant_id, password)
    profile, profile_action = _upsert_profile(args, tenant_id, actor_id, connector.id, schemas)
    bindings = []
    for schema in schemas:
        binding, action = _upsert_binding(args, tenant_id, actor_id, profile["id"], schema)
        bindings.append({"id": binding["id"], "schema": schema, "action": action})

    print(
        json.dumps(
            {
                "status": "READY",
                "connector": {"id": connector.id, "name": connector.name, "action": connector_action},
                "profile": {"id": profile["id"], "name": profile["name"], "action": profile_action},
                "bindings": bindings,
                "mapping_rule": "catalog service.database.schema.table -> PostgreSQL schema.table",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
