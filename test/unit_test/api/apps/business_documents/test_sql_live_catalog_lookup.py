"""SQL lookup never waits for the dashboard's complete catalog projection."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_schema_resolution import schema_adapter_module as adapter
from business_documents.sql_query.schema_resolution import CatalogAccessDenied, CatalogLookupError
from api.apps.services.openmetadata_copilot_service import OpenMetadataAuthenticationError, OpenMetadataNotFoundError, OpenMetadataPermissionError

FQN = "postgres.bot.public.search_queries"


def raw_table():
    return {
        "id": "table-1",
        "name": "search_queries",
        "fullyQualifiedName": FQN,
        "version": 3,
        "columns": [{"name": "id", "dataType": "BIGINT"}],
        "service": {"name": "postgres"},
        "database": {"name": "bot"},
        "databaseSchema": {"name": "public"},
    }


@pytest.fixture
def service(monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError("Full catalog projection/Dataset must not load for a live hit")

    current = raw_table()
    client = SimpleNamespace(
        search_tables=lambda query, size: calls.append(("search", query, size)) or [{**deepcopy(current), "version": 1}],
        get_table_by_name=lambda fqn: calls.append(("name", fqn)) or deepcopy(current),
        get_table=lambda identity: calls.append(("id", identity)) or deepcopy(current),
    )
    value = SimpleNamespace(
        client=client,
        config=SimpleNamespace(public_url="https://catalog.example"),
        catalog=SimpleNamespace(run=forbidden),
        projection=SimpleNamespace(get=forbidden),
        is_entity_visible=lambda entity, user_id: calls.append(("acl", entity["version"], user_id)) or True,
    )

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(adapter, "thread_pool_exec", run_sync)
    monkeypatch.setattr(adapter, "retrieve_openmetadata_dataset_hits", forbidden)
    return value, calls, current


@pytest.mark.asyncio
async def test_index_lookup_refreshes_each_candidate_without_loading_projection(service):
    value, calls, _ = service
    result = await adapter.OpenMetadataCatalogResolver(value).resolve(term="search query", actor_id="actor", locale="ru")
    assert result.candidates[0].fqn == FQN
    assert result.candidates[0].version == 3
    assert result.retrieval == "omd_live_schema"
    assert ("acl", 1, "actor") in calls and ("acl", 3, "actor") in calls
    assert ("id", "table-1") in calls


@pytest.mark.asyncio
async def test_exact_table_uses_name_endpoint_and_does_not_search_neighbors(service):
    value, calls, _ = service
    result = await adapter.OpenMetadataCatalogResolver(value).resolve(term=FQN, actor_id="actor", locale="ru")
    assert result.candidates[0].id == "table-1"
    assert not any(call[0] == "search" for call in calls)
    assert ("name", FQN) in calls


@pytest.mark.asyncio
async def test_missing_exact_table_is_empty_and_does_not_expand_search(service):
    value, calls, _ = service

    def missing(*args):
        raise OpenMetadataNotFoundError("gone")

    value.client.get_table_by_name = missing
    result = await adapter.OpenMetadataCatalogResolver(value).resolve(term=FQN, actor_id="actor", locale="ru")
    assert not result.candidates and result.needs_clarification
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["identity", "deleted", "acl"])
async def test_loading_schema_rechecks_current_identity_deletion_and_acl(service, change):
    value, calls, current = service
    if change == "identity":
        current["id"] = "different"
    elif change == "deleted":
        current["deleted"] = True
    else:
        value.is_entity_visible = lambda *args, **kwargs: False
    with pytest.raises(CatalogAccessDenied if change == "acl" else CatalogLookupError):
        await adapter.OpenMetadataCatalogResolver(value).load_entity(entity_id="table-1", actor_id="actor", locale="ru")
    assert not any(call[0] in {"search", "name"} for call in calls)


@pytest.mark.asyncio
async def test_domain_revocation_between_search_and_live_read_fails_closed(service):
    value, _, _ = service
    value.is_entity_visible = lambda entity, user_id: entity["version"] == 1
    with pytest.raises(CatalogAccessDenied):
        await adapter.OpenMetadataCatalogResolver(value).resolve(term="search query", actor_id="actor", locale="ru")


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [OpenMetadataAuthenticationError, OpenMetadataPermissionError])
async def test_auth_failure_cannot_fall_back_to_cached_catalog(service, error_type):
    value, _, _ = service

    def denied(*args):
        raise error_type("denied")

    value.client.search_tables = denied
    with pytest.raises(CatalogAccessDenied if error_type is OpenMetadataPermissionError else CatalogLookupError):
        await adapter.OpenMetadataCatalogResolver(value).resolve(term="search query", actor_id="actor", locale="ru")
