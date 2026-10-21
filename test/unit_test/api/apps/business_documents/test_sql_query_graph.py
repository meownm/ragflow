"""Catalog access and graph projection never execute or mutate a SQL project."""

import sys
from pathlib import Path
from types import ModuleType

import pytest

if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService
from business_documents.application.errors import BusinessDocumentError, PermissionDeniedError, ValidationError
from business_documents.sql_query.schema_resolution import CatalogAccessDenied, CatalogLookupError


class Catalog:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def authorize(self, **kwargs):
        self.calls.append(("authorize", kwargs))
        if self.error:
            raise self.error

    async def visible_catalog(self, **kwargs):
        self.calls.append(("visible", kwargs))
        return {"tables": [{"id": "orders", "technical_name": "orders", "schema": "public", "columns": [{"id": "public.orders.id", "name": "id"}]}], "freshness": {"stale": False}, "warnings": []}


@pytest.mark.asyncio
async def test_graph_checks_dataset_access_before_catalog_and_never_runs_model():
    resolver = Catalog()
    response = await BusinessDocumentSqlQuerySchemaService.graph("actor", {"schema_version": "1", "entity_ids": ["orders"], "sql": "SELECT o.id FROM public.orders o"}, False, "AUTHOR_CREATOR", resolver=resolver)
    assert resolver.calls == [("authorize", {"actor_id": "actor", "is_admin": False}), ("visible", {"actor_id": "actor"})]
    assert response["nodes"][0]["membership"] == "QUERY"
    assert response["freshness"] == {"stale": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [{"actor_id": "other"}, {"depth": True}, {"entity_ids": []}, {"entity_ids": ["orders", "orders"]}, {"sql": 3}])
async def test_invalid_graph_request_does_not_touch_catalog(damage):
    resolver = Catalog()
    with pytest.raises(ValidationError):
        await BusinessDocumentSqlQuerySchemaService.graph("actor", {"schema_version": "1", "entity_ids": ["orders"], **damage}, False, "AUTHOR_CREATOR", resolver=resolver)
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_forbidden_catalog_does_not_become_an_empty_success():
    resolver = Catalog(CatalogAccessDenied("denied"))
    with pytest.raises(PermissionDeniedError):
        await BusinessDocumentSqlQuerySchemaService.graph("actor", {"schema_version": "1", "entity_ids": ["orders"]}, False, "AUTHOR_CREATOR", resolver=resolver)
    assert len(resolver.calls) == 1


@pytest.mark.asyncio
async def test_unavailable_catalog_is_503_not_not_found():
    with pytest.raises(BusinessDocumentError) as failure:
        await BusinessDocumentSqlQuerySchemaService.graph("actor", {"schema_version": "1", "entity_ids": ["orders"]}, False, "AUTHOR_CREATOR", resolver=Catalog(CatalogLookupError("offline")))
    assert failure.value.status == 503


@pytest.mark.asyncio
async def test_invisible_table_cannot_be_included_in_graph():
    with pytest.raises(ValidationError):
        await BusinessDocumentSqlQuerySchemaService.graph("actor", {"schema_version": "1", "entity_ids": ["hidden"]}, False, "AUTHOR_CREATOR", resolver=Catalog())
