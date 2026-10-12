"""Catalog authority, untrusted model suggestions, and bounded async lookups."""

import asyncio
from copy import deepcopy

import pytest

from business_documents.sql_query.schema_resolution import (
    CatalogAccessDenied,
    CatalogLookupError,
    SchemaEntityDetailsScenario,
    SchemaInterpretationError,
    SchemaResolutionScenario,
    SchemaResolutionValidationError,
    catalog_result_from_mapping,
    parse_load_schema_entities_command,
    parse_resolve_schema_command,
)


def _answer(term="orders", *, include_all_columns=False):
    return catalog_result_from_mapping(
        term,
        {
            "freshness": {"stale": False, "age_hours": 1, "threshold_hours": 24},
            "entities": [{"id": "orders", "fqn": "sales.orders", "technical_name": "orders", "version": 2, "column_details": [{"name": "id", "data_type": "INT"}]}],
        },
        include_all_columns=include_all_columns,
    )


class Catalog:
    def __init__(self, outcomes=None, authorization_error=None):
        self.outcomes = outcomes or {}
        self.authorization_error = authorization_error
        self.calls = []

    async def authorize(self, **kwargs):
        self.calls.append(("authorize", kwargs))
        if self.authorization_error:
            raise self.authorization_error

    async def resolve(self, *, term, **kwargs):
        self.calls.append((term, kwargs))
        result = self.outcomes.get(term, _answer(term))
        if isinstance(result, BaseException):
            raise result
        return result

    async def load_entity(self, *, entity_id, **kwargs):
        return await self.resolve(term=entity_id, **kwargs)


class Interpreter:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def prompt_provenance(self):
        raise SchemaInterpretationError("No configured model")

    async def interpret(self, **kwargs):
        self.calls.append(kwargs)
        if self.response is None:
            raise SchemaInterpretationError("No configured model")
        return self.response


def _command(terms=None, locale="en"):
    return parse_resolve_schema_command({"terms": terms or ["orders"], "locale": locale}, tenant_id="tenant", actor_id="reader")


@pytest.mark.parametrize(
    "payload,message",
    [
        (None, "JSON object"),
        ({"terms": ["orders"], "sql": "SELECT"}, "Unknown request"),
        ({"terms": []}, "non-empty array"),
        ({"terms": ["orders", "ORDERS"]}, "unique ignoring case"),
        ({"terms": ["x"] * 9}, "no more than 8"),
        ({"terms": [""]}, "non-empty string"),
        ({"terms": ["x" * 201]}, "exceeds 200"),
        ({"terms": ["orders"], "requirements": True}, "string"),
        ({"terms": ["orders"], "requirements": "x" * 20001}, "exceeds 20000"),
        ({"terms": ["orders"], "locale": "fr"}, "locale"),
        ({"terms": ["orders"], "locale": []}, "locale"),
        ({"terms": ["orders"], "locale": {}}, "locale"),
    ],
)
def test_schema_search_rejects_ambiguous_or_unbounded_user_requests(payload, message):
    with pytest.raises(SchemaResolutionValidationError, match=message):
        parse_resolve_schema_command(payload, tenant_id="tenant", actor_id="reader")


@pytest.mark.parametrize(
    "payload,message",
    [
        ([], "JSON object"),
        ({"entity_ids": ["orders"], "tenant_id": "other"}, "Unknown request"),
        ({"entity_ids": []}, "non-empty array"),
        ({"entity_ids": ["orders", "ORDERS"]}, "unique ignoring case"),
        ({"entity_ids": ["x" * 501]}, "exceeds 500"),
        ({"entity_ids": ["orders"], "locale": "fr"}, "locale"),
        ({"entity_ids": ["orders"], "locale": []}, "locale"),
        ({"entity_ids": ["orders"], "locale": {}}, "locale"),
    ],
)
def test_schema_details_rejects_unknown_fields_and_invalid_entity_selection(payload, message):
    with pytest.raises(SchemaResolutionValidationError, match=message):
        parse_load_schema_entities_command(payload, actor_id="reader")


@pytest.mark.parametrize("details", [False, True])
async def test_denied_catalog_access_stops_before_lookup_or_model(details):
    catalog = Catalog(authorization_error=CatalogAccessDenied("reader forbidden"))
    interpreter = Interpreter()
    if details:
        scenario = SchemaEntityDetailsScenario(catalog)
        command = parse_load_schema_entities_command({"entity_ids": ["orders"]}, actor_id="reader")
    else:
        scenario = SchemaResolutionScenario(catalog, interpreter)
        command = _command()
    with pytest.raises(CatalogAccessDenied):
        await scenario.run(command)
    assert catalog.calls == [("authorize", {"actor_id": "reader", "is_admin": False})]
    assert interpreter.calls == []


@pytest.mark.parametrize("details", [False, True])
async def test_authorization_deadline_is_a_prerequisite_failure(details):
    class BlockedCatalog(Catalog):
        async def authorize(self, **kwargs):
            await asyncio.Event().wait()

    catalog = BlockedCatalog()
    if details:
        scenario = SchemaEntityDetailsScenario(catalog, lookup_timeout_seconds=0.001)
        command = parse_load_schema_entities_command({"entity_ids": ["orders"]}, actor_id="reader")
    else:
        scenario = SchemaResolutionScenario(catalog, Interpreter(), lookup_timeout_seconds=0.001)
        command = _command()
    with pytest.raises(CatalogLookupError, match="authorization timed out"):
        await scenario.run(command)
    assert catalog.calls == []


@pytest.mark.parametrize("locale", ["ru", "en"])
async def test_partial_lookup_failure_keeps_input_order_and_never_sends_failed_answers_to_model(locale):
    interpreter = Interpreter()
    catalog = Catalog({"offline": CatalogLookupError("connection failed"), "slow": CatalogLookupError("deadline exceeded")})
    result = await SchemaResolutionScenario(catalog, interpreter).run(_command(["offline", "orders", "slow"], locale))
    assert result["status"] == "DEGRADED"
    assert [item["term"] for item in result["resolutions"]] == ["offline", "orders", "slow"]
    assert [item["lookup"]["error_code"] for item in result["resolutions"]] == ["CATALOG_LOOKUP_FAILED", None, "CATALOG_TIMEOUT"]
    assert [term for term, _ in interpreter.calls[0]["catalog_answers"]] == ["orders"]
    assert interpreter.calls[0]["tenant_id"] == "tenant"
    assert all(call[1]["actor_id"] == "reader" for call in catalog.calls)
    assert result["llm"]["status"] == "FALLBACK"


async def test_batch_deadline_cancels_pending_catalog_requests():
    cancelled = asyncio.Event()

    class BlockedCatalog(Catalog):
        async def resolve(self, *, term, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    result = await SchemaResolutionScenario(BlockedCatalog(), Interpreter(), lookup_timeout_seconds=10, batch_timeout_seconds=0.001).run(_command())
    assert cancelled.is_set()
    assert result["status"] == "DEGRADED"
    assert result["resolutions"][0]["lookup"]["error_code"] == "CATALOG_TIMEOUT"
    assert result["llm"]["status"] == "SKIPPED"


@pytest.mark.parametrize("details", [False, True])
async def test_cancelling_scenario_releases_its_in_flight_catalog_requests(details):
    entered = asyncio.Event()
    requests = []
    closed = []

    class BlockedCatalog(Catalog):
        async def resolve(self, *, term, **kwargs):
            requests.append(asyncio.current_task())
            if len(requests) == 2:
                entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(term)

    catalog = BlockedCatalog()
    if details:
        scenario = SchemaEntityDetailsScenario(catalog)
        command = parse_load_schema_entities_command({"entity_ids": ["orders", "customers"]}, actor_id="reader")
    else:
        scenario = SchemaResolutionScenario(catalog, Interpreter())
        command = _command(["orders", "customers"])
    task = asyncio.create_task(scenario.run(command))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(request.done() for request in requests)
        assert set(closed) == {"orders", "customers"}
    finally:
        # The regression must also release its fixtures when the assertion
        # identifies a leak, without relying on pytest's loop shutdown.
        task.cancel()
        for request in requests:
            request.cancel()
        await asyncio.gather(task, *requests, return_exceptions=True)


async def test_unexpected_catalog_failure_preserves_error_and_closes_other_requests():
    started = asyncio.Event()
    closed = []

    class FailingCatalog(Catalog):
        async def resolve(self, *, term, **kwargs):
            try:
                if term == "broken":
                    await started.wait()
                    raise RuntimeError("Catalog adapter defect")
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.append(term)

    scenario = SchemaResolutionScenario(FailingCatalog(), Interpreter(), lookup_timeout_seconds=10, batch_timeout_seconds=0.01)
    with pytest.raises(RuntimeError, match="Catalog adapter defect"):
        await scenario.run(_command(["broken", "pending"]))
    assert set(closed) == {"broken", "pending"}


def _interpretation():
    return {
        "schema_version": "1",
        "resolutions": [
            {
                "term": "orders",
                "kind": "entity",
                "normalized_term": "Orders",
                "recommended_entity_id": "orders",
                "recommended_column_ids": [],
                "confidence": 0.8,
                "reason": "Catalog contains this table",
                "clarification_question": None,
            }
        ],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("term", "unknown"),
        ("kind", "sql"),
        ("recommended_entity_id", "secret"),
        ("kind", []),
        ("kind", {}),
        ("recommended_entity_id", 17),
        ("recommended_entity_id", "x" * 501),
        ("recommended_column_ids", ["sales.secret.id"]),
        ("recommended_column_ids", ["sales.orders.id"]),
        ("recommended_column_ids", ["sales.orders.id", "sales.orders.id"]),
        ("recommended_column_ids", [""]),
        ("recommended_column_ids", "sales.orders.id"),
        ("recommended_column_ids", ["sales.orders.id"] * 61),
        ("confidence", True),
        ("confidence", 1.01),
        ("confidence", -0.1),
        ("reason", ""),
        ("reason", "x" * 2001),
        ("clarification_question", False),
        ("normalized_term", "x" * 201),
    ],
)
async def test_invalid_model_recommendation_cannot_override_catalog_authority(field, value):
    response = _interpretation()
    response["resolutions"][0][field] = value
    result = await SchemaResolutionScenario(Catalog(), Interpreter(response)).run(_command())
    assert result["llm"]["status"] == "FALLBACK"
    interpretation = result["resolutions"][0]["interpretation"]
    assert interpretation["recommended_entity_id"] == "orders"
    assert interpretation["recommended_column_ids"] == []
    assert interpretation["confidence"] is None


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {"schema_version": "2", "resolutions": []},
        {"schema_version": "1", "resolutions": []},
        {"schema_version": "1", "resolutions": [{}]},
        {"schema_version": "1", "resolutions": [], "sql": "SELECT"},
    ],
)
async def test_model_response_requires_complete_closed_versioned_contract(response):
    result = await SchemaResolutionScenario(Catalog(), Interpreter(response)).run(_command())
    assert result["llm"]["status"] == "FALLBACK"
    assert result["status"] == "READY"
    assert result["resolutions"][0]["interpretation"]["recommended_entity_id"] == "orders"


async def test_catalog_backed_field_recommendation_can_be_applied_without_inventing_columns():
    response = _interpretation()
    response["resolutions"][0].update(kind="field", recommended_column_ids=["sales.orders.id"], confidence=None)
    result = await SchemaResolutionScenario(Catalog(), Interpreter(response)).run(_command())
    assert result["llm"]["status"] == "APPLIED"
    assert result["resolutions"][0]["interpretation"] == response["resolutions"][0]


async def test_details_wrong_entity_or_failed_lookup_cannot_look_successful():
    catalog = Catalog({"missing": _answer(include_all_columns=True), "offline": CatalogLookupError("offline")})
    command = parse_load_schema_entities_command({"entity_ids": ["missing", "orders", "offline"], "locale": "en"}, actor_id="reader")
    result = await SchemaEntityDetailsScenario(catalog).run(command)
    assert result["status"] == "DEGRADED"
    assert [item["entity_id"] for item in result["entities"]] == ["missing", "orders", "offline"]
    assert [item["lookup"]["status"] for item in result["entities"]] == ["ERROR", "OK", "ERROR"]
    assert result["entities"][0]["entity"] is None and result["entities"][2]["entity"] is None


def test_catalog_normalization_keeps_supported_provenance_and_constraints():
    answer = {
        "freshness": {"stale": "false", "threshold_hours": True},
        "entities": [
            None,
            {},
            {
                "id": "orders",
                "fqn": "sales.orders",
                "technical_name": "orders",
                "column_details": [None, {}, {"name": "Order ID", "technical_name": "id", "glossary_terms": ["Key", "key", "", None]}, {"name": "id"}],
                "table_constraints": [None, {}, {"constraint_type": "PRIMARY_KEY", "columns": ["id"], "relationship_type": "ONE_TO_ONE"}],
                "owners": ["Owner", "owner", ""],
                "column_count": True,
            },
        ],
        "sources": [None, {}, {"label": "Catalog", "url": "https://catalog.example", "dataset_id": "qa-catalog"}],
    }
    result = catalog_result_from_mapping("id", answer, include_all_columns=True).to_api()
    assert result["freshness"]["stale"] is True and result["freshness"]["threshold_hours"] == 0
    assert result["sources"] == [{"label": "Catalog", "url": "https://catalog.example", "dataset_id": "qa-catalog"}]
    entity = result["entities"][0]
    assert entity["owners"] == ["Owner"]
    assert entity["column_details"][0]["display_name"] == "Order ID"
    assert entity["column_details"][0]["glossary_terms"] == ["Key"]
    assert entity["table_constraints"] == [{"constraint_type": "PRIMARY_KEY", "columns": ["id"], "referred_columns": [], "relationship_type": "ONE_TO_ONE"}]
    assert len(entity["column_details"]) == 1 and entity["schema_fingerprint"].startswith("sha256:")


def test_column_search_ranks_exact_before_partial_and_falls_back_when_no_match():
    entity = {"id": "orders", "fqn": "sales.orders", "technical_name": "orders", "column_details": [{"name": "order_id"}, {"name": "id"}, {"name": "name"}, {"name": "other"}]}
    exact = catalog_result_from_mapping("id", {"entities": [entity]})
    assert [column.name for column in exact.candidates[0].columns] == ["id", "order_id"]
    fallback = catalog_result_from_mapping("unknown", {"entities": [entity]})
    assert [column.name for column in fallback.candidates[0].columns] == ["order_id", "id", "name"]
    assert fallback.candidates[0].schema_fingerprint is None


def test_loaded_catalog_schema_marks_truncation_and_fingerprint_changes():
    entity = {"id": "orders", "fqn": "sales.orders", "technical_name": "orders", "column_details": [{"name": f"column_{index}"} for index in range(501)]}
    loaded = catalog_result_from_mapping("orders", {"entities": [entity]}, include_all_columns=True)
    assert len(loaded.candidates[0].columns) == 500
    assert loaded.candidates[0].columns_truncated
    modified = deepcopy(entity)
    modified["column_details"][0]["data_type"] = "INT"
    changed = catalog_result_from_mapping("orders", {"entities": [modified]}, include_all_columns=True)
    assert loaded.candidates[0].schema_fingerprint != changed.candidates[0].schema_fingerprint


def test_non_object_catalog_response_fails_as_lookup_error():
    with pytest.raises(CatalogLookupError, match="invalid response"):
        catalog_result_from_mapping("orders", [])
