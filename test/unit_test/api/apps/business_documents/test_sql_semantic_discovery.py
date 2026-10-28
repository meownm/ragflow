"""Requirement-driven discovery is bounded, authorized and grounded in catalog IDs."""

import asyncio
import json
from dataclasses import replace

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_schema_resolution import (
    FakeCatalog,
    FakeInterpreter,
    _answer,
    _entity,
    _interpretation,
    TenantSchemaInterpreter,
    SchemaResolutionScenario,
    SchemaInterpretationError,
    CatalogAccessDenied,
    parse_resolve_schema_command,
)
from business_documents.sql_query.schema_resolution import SchemaResolutionValidationError
from api.apps.business_documents import sql_query_schema as schema_adapter


def command(**changes):
    return parse_resolve_schema_command(
        {"requirements": "Выручка по клиентам с возвратами за 2026 год", **changes},
        tenant_id="tenant-1",
        actor_id="actor-1",
    )


class Planner(FakeInterpreter):
    def __init__(self, plan, **kwargs):
        super().__init__(**kwargs)
        self.plan = plan
        self.plans = []

    async def plan_search(self, **kwargs):
        self.plans.append(kwargs)
        return self.plan


def plan(*searches):
    return {"schema_version": "1", "searches": [{"term": term, "query": query} for term, query in searches]}


@pytest.mark.asyncio
async def test_accepted_requirements_and_answers_drive_search_without_table_input():
    interpreter = Planner(
        plan(("Возвраты", "payment refund")),
        response={
            "schema_version": "1",
            "resolutions": [_interpretation("Возвраты", "actual-catalog-id")],
        },
    )
    catalog = FakeCatalog({"payment refund": _answer(_entity("actual-catalog-id", "warehouse.public.chargeback"))})
    result = await SchemaResolutionScenario(catalog, interpreter).run(
        command(
            clarifications=[{"question": "Валюта?", "answer": "рубли"}],
        )
    )
    assert catalog.lookups == [("payment refund", "actor-1", "ru")]
    assert "рубли" in interpreter.plans[0]["requirements"]
    assert result["resolutions"][0]["term"] == "Возвраты"
    assert result["resolutions"][0]["interpretation"]["recommended_entity_id"] == "actual-catalog-id"


@pytest.mark.asyncio
async def test_denied_catalog_does_not_send_requirements_to_model():
    interpreter = Planner(plan(("Возвраты", "refund")))
    catalog = FakeCatalog({}, denied=True)
    with pytest.raises(CatalogAccessDenied):
        await SchemaResolutionScenario(catalog, interpreter).run(command())
    assert interpreter.plans == [] and catalog.lookups == []


@pytest.mark.parametrize(
    "bad_plan",
    [
        {},
        plan(),
        plan(*[(str(i), "refund") for i in range(9)]),
        plan(("Возвраты", "refund"), ("возвраты", "chargeback")),
        plan(("Возвраты", " ")),
        plan(("Возвраты", "x" * 201)),
        {**plan(("Возвраты", "refund")), "sql": "SELECT 1"},
        {"schema_version": "1", "searches": [{"term": "Возвраты", "query": "refund", "id": "invented"}]},
    ],
)
@pytest.mark.asyncio
async def test_invalid_plan_cannot_expand_catalog_reads(bad_plan):
    catalog = FakeCatalog({})
    with pytest.raises(SchemaInterpretationError):
        await SchemaResolutionScenario(catalog, Planner(bad_plan)).run(command())
    assert catalog.lookups == []


@pytest.mark.asyncio
async def test_search_planning_deadline_and_cancellation_stop_before_catalog():
    class Slow(Planner):
        async def plan_search(self, **kwargs):
            await asyncio.sleep(30)

    catalog = FakeCatalog({})
    scenario = SchemaResolutionScenario(catalog, Slow(None), overall_timeout_seconds=0.03)
    with pytest.raises(SchemaInterpretationError):
        await scenario.run(command())
    task = asyncio.create_task(SchemaResolutionScenario(catalog, Slow(None)).run(command()))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert catalog.lookups == []


@pytest.mark.asyncio
async def test_tenant_search_adapter_uses_closed_prompt_and_no_catalog_or_execution():
    calls = []

    class Llm:
        async def async_generate(self, tenant_id, system_prompt, payload):
            calls.append((tenant_id, system_prompt, payload))
            return json.dumps(plan(("Возвраты", "payment refund")))

    result = await TenantSchemaInterpreter(Llm(), timeout_seconds=1).plan_search(
        tenant_id="tenant-1",
        requirements="Возвраты по клиентам",
        locale="ru",
    )
    assert result == plan(("Возвраты", "payment refund"))
    assert len(calls) == 1 and calls[0][0] == "tenant-1"
    assert "{{" not in calls[0][1]
    assert calls[0][2]["prompt"]["content_hash"].startswith("sha256:")
    assert calls[0][2]["job_input"]["requirements"] == "Возвраты по клиентам"


@pytest.mark.parametrize("payload", [{"terms": []}, {"requirements": " "}, {"requirements": "report", "terms": None}])
def test_automatic_search_requires_requirements_and_does_not_accept_malformed_terms(payload):
    with pytest.raises(SchemaResolutionValidationError):
        parse_resolve_schema_command(payload, tenant_id="tenant", actor_id="actor")


@pytest.mark.asyncio
async def test_slow_optional_dataset_search_falls_back_to_live_catalog_within_lookup_deadline(monkeypatch):
    cancelled = []
    calls = []

    async def slow_dataset(*args):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    class Catalog:
        def run(self, question, **kwargs):
            calls.append((question, kwargs))
            return {**_answer(_entity("actual-refund", "warehouse.public.refund_event")), "warnings": [kwargs["dataset_warning"]]}

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(schema_adapter, "_DATASET_LOOKUP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(schema_adapter, "retrieve_openmetadata_dataset_hits", slow_dataset)
    monkeypatch.setattr(schema_adapter, "thread_pool_exec", run_sync)
    resolver = schema_adapter.OpenMetadataCatalogResolver(type("Service", (), {"catalog": Catalog(), "client": type("Client", (), {"search_tables": lambda *args: []})()})())
    answer = await asyncio.wait_for(resolver.resolve(term="payment refund", actor_id="actor-1", locale="ru"), timeout=0.2)
    assert cancelled == [True]
    assert answer.candidates[0].id == "actual-refund"
    assert answer.warnings and "Dataset" in answer.warnings[0]
    assert calls[0][0] == "payment refund"
    assert calls[0][1]["user_id"] == "actor-1" and calls[0][1]["forced_intent"] == "discovery"
    assert "dataset_hits" not in calls[0][1]


@pytest.mark.asyncio
async def test_exact_source_and_business_search_both_reach_the_catalog():
    fqn = "catalog.db.public.orders"
    interpreter = Planner(
        plan(("Заказы", fqn), ("Клиенты", "customer")), response={"schema_version": "1", "resolutions": [_interpretation("Заказы", "orders"), _interpretation("Клиенты", "customers")]}
    )
    catalog = FakeCatalog({fqn: _answer(_entity("orders", fqn)), "customer": _answer(_entity("customers", "catalog.db.public.customers"))})
    parsed = command(requirements=f"Заказы из {fqn} и ФИО клиента из справочника клиентов")

    result = await SchemaResolutionScenario(catalog, interpreter).run(parsed)

    assert len(interpreter.plans) == 1
    assert [row["term"] for row in result["resolutions"]] == ["Заказы", "Клиенты"]
    assert {term for term, _, _ in catalog.lookups} == {fqn, "customer"}


@pytest.mark.asyncio
async def test_original_context_remains_separate_and_does_not_force_excluded_source():
    fqn = "catalog.db.public.old_orders"
    interpreter = Planner(plan(("Заказы", "current order")), response={"schema_version": "1", "resolutions": [_interpretation("Заказы", "orders")]})
    catalog = FakeCatalog({"current order": _answer(_entity("orders", "catalog.db.public.orders"))})
    parsed = replace(command(requirements=f"Не используй {fqn}; найди актуальные заказы"), source_request=f"Используй только {fqn}")

    await SchemaResolutionScenario(catalog, interpreter).run(parsed)

    assert catalog.lookups == [("current order", "actor-1", "ru")]
    assert interpreter.plans[0]["requirements"] == parsed.requirements
    assert interpreter.plans[0]["source_request"] == parsed.source_request
    assert interpreter.calls[0]["requirements"] == parsed.requirements


@pytest.mark.asyncio
async def test_original_positive_reference_survives_canonical_business_requirements():
    fqn = "catalog.db.public.orders"
    interpreter = Planner(plan(("Заказы", fqn)), response={"schema_version": "1", "resolutions": [_interpretation("Заказы", "orders")]})
    catalog = FakeCatalog({fqn: _answer(_entity("orders", fqn))})
    parsed = replace(command(requirements="Покажи заказы"), source_request=f"Используй только {fqn}.")

    result = await SchemaResolutionScenario(catalog, interpreter).run(parsed)

    assert result["resolutions"][0]["term"] == "Заказы"
    assert catalog.lookups == [(fqn, "actor-1", "ru")]
    assert interpreter.plans[0]["source_request"] == parsed.source_request
    assert interpreter.plans[0]["requirements"] == parsed.requirements == "Покажи заказы"


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["Нужны заказы", "https://catalog.db.public.orders", "user@catalog.db.public.orders", "catalog.db.public.orders.id"])
async def test_invented_or_embedded_source_cannot_become_an_exact_catalog_query(context):
    catalog = FakeCatalog({})
    interpreter = Planner(plan(("Заказы", "catalog.db.public.orders")))

    with pytest.raises(SchemaInterpretationError):
        await SchemaResolutionScenario(catalog, interpreter).run(command(requirements=context))

    assert not catalog.lookups
