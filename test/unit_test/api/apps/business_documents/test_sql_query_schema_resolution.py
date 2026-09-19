#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#

from __future__ import annotations

import json
from pathlib import Path
import sys
import asyncio
from types import ModuleType

import pytest


if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents import sql_query_schema as schema_adapter_module
from api.apps.business_documents.errors import PermissionDeniedError, ValidationError
from api.apps.business_documents.sql_query_schema import (
    BusinessDocumentSqlQuerySchemaService,
    OpenMetadataCatalogResolver,
    TenantSchemaInterpreter,
)
from business_documents.sql_query.schema_resolution import (
    CatalogAccessDenied,
    CatalogLookupError,
    LoadSchemaEntitiesCommand,
    PromptProvenance,
    ResolveSchemaCommand,
    SchemaEntityDetailsScenario,
    SchemaInterpretationError,
    SchemaResolutionScenario,
    SchemaResolutionValidationError,
    catalog_result_from_mapping,
    parse_resolve_schema_command,
)


def _entity(entity_id: str, fqn: str, *, column_count: int = 1) -> dict:
    return {
        "id": entity_id,
        "name": fqn.rsplit(".", 1)[-1],
        "technical_name": fqn.rsplit(".", 1)[-1],
        "fqn": fqn,
        "description": f"Description for {fqn}",
        "column_details": [
            {
                "name": f"field_{index}",
                "data_type": "BIGINT",
                "description": "Field description",
                "constraint": "",
            }
            for index in range(column_count)
        ],
    }


def _answer(*entities: dict, needs_clarification: bool = False) -> dict:
    return {
        "agent": "catalog_copilot",
        "intent": "discovery",
        "answer": "Catalog evidence",
        "entities": list(entities),
        "needs_clarification": needs_clarification,
        "warnings": [],
    }


class FakeCatalog:
    def __init__(self, answers: dict[str, dict], *, denied: bool = False):
        self.answers = answers
        self.denied = denied
        self.authorizations = []
        self.lookups = []

    async def authorize(self, *, actor_id: str, is_admin: bool) -> None:
        self.authorizations.append((actor_id, is_admin))
        if self.denied:
            raise CatalogAccessDenied

    async def resolve(self, *, term: str, actor_id: str, locale: str):
        self.lookups.append((term, actor_id, locale))
        answer = self.answers[term]
        if isinstance(answer, Exception):
            raise answer
        return catalog_result_from_mapping(term, answer)

    async def load_entity(self, *, entity_id: str, actor_id: str, locale: str):
        self.lookups.append((entity_id, actor_id, locale))
        answer = self.answers[entity_id]
        if isinstance(answer, Exception):
            raise answer
        return catalog_result_from_mapping(entity_id, answer, include_all_columns=True)


class FakeInterpreter:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def prompt_provenance(self):
        return PromptProvenance(
            name="test_sql_schema_interpreter",
            version="7",
            content_hash="sha256:test",
        )

    async def interpret(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _command(*terms: str) -> ResolveSchemaCommand:
    return ResolveSchemaCommand(
        tenant_id="tenant-1",
        actor_id="actor-1",
        terms=tuple(terms),
        requirements="Нужны заказы и их статусы",
        locale="ru",
    )


def _interpretation(term: str, recommended_entity_id: str | None) -> dict:
    return {
        "term": term,
        "kind": "entity",
        "normalized_term": term.casefold(),
        "recommended_entity_id": recommended_entity_id,
        "recommended_column_ids": [],
        "confidence": 0.82,
        "reason": "Кандидат подтверждён каталогом.",
        "clarification_question": "Какую физическую таблицу выбрать?",
    }


def test_request_contract_is_closed_bounded_and_case_insensitively_unique():
    command = parse_resolve_schema_command(
        {"terms": [" Заказ ", "Клиент"], "requirements": " Отчёт ", "locale": "ru"},
        tenant_id="tenant-1",
        actor_id="actor-1",
    )

    assert command.terms == ("Заказ", "Клиент")
    assert command.requirements == "Отчёт"
    with pytest.raises(SchemaResolutionValidationError, match="Unknown request fields"):
        parse_resolve_schema_command(
            {"terms": ["Заказ"], "unexpected": True},
            tenant_id="tenant-1",
            actor_id="actor-1",
        )
    with pytest.raises(SchemaResolutionValidationError, match="unique"):
        parse_resolve_schema_command(
            {"terms": ["Заказ", "заказ"]},
            tenant_id="tenant-1",
            actor_id="actor-1",
        )


@pytest.mark.asyncio
async def test_llm_recommendation_never_closes_catalog_ambiguity():
    catalog = FakeCatalog(
        {
            "Заказ": _answer(
                _entity("orders", "dwh.order_fact"),
                _entity("events", "dwh.order_event_fact"),
                needs_clarification=True,
            )
        }
    )
    interpreter = FakeInterpreter({"schema_version": "1", "resolutions": [_interpretation("Заказ", "orders")]})

    result = await SchemaResolutionScenario(catalog, interpreter).run(_command("Заказ"))

    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["llm"]["status"] == "APPLIED"
    assert result["llm"]["prompt"] == {
        "name": "test_sql_schema_interpreter",
        "version": "7",
        "content_hash": "sha256:test",
    }
    assert result["resolutions"][0]["needs_clarification"] is True
    assert result["resolutions"][0]["interpretation"]["recommended_entity_id"] == "orders"
    assert catalog.authorizations == [("actor-1", False)]
    assert len(interpreter.calls) == 1


@pytest.mark.asyncio
async def test_hallucinated_candidate_falls_back_to_catalog_decision():
    catalog = FakeCatalog({"Заказ": _answer(_entity("orders", "dwh.order_fact"))})
    interpreter = FakeInterpreter({"schema_version": "1", "resolutions": [_interpretation("Заказ", "invented-table")]})

    result = await SchemaResolutionScenario(catalog, interpreter).run(_command("Заказ"))

    assert result["status"] == "READY"
    assert result["llm"]["status"] == "FALLBACK"
    assert result["llm"]["warning"]
    assert result["resolutions"][0]["interpretation"]["recommended_entity_id"] == "orders"


@pytest.mark.asyncio
async def test_one_exact_name_can_resolve_a_multi_candidate_catalog_result():
    catalog = FakeCatalog(
        {
            "order_fact": _answer(
                _entity("orders", "dwh.order_fact"),
                _entity("events", "dwh.order_event_fact"),
            )
        }
    )
    interpreter = FakeInterpreter(error=SchemaInterpretationError("offline"))

    result = await SchemaResolutionScenario(catalog, interpreter).run(_command("order_fact"))

    assert result["status"] == "READY"
    assert result["resolutions"][0]["needs_clarification"] is False
    assert result["resolutions"][0]["interpretation"]["recommended_entity_id"] == "orders"


@pytest.mark.asyncio
async def test_interpreter_failure_falls_back_but_empty_catalog_skips_llm():
    failed = await SchemaResolutionScenario(
        FakeCatalog({"Заказ": _answer(_entity("orders", "dwh.order_fact"))}),
        FakeInterpreter(error=SchemaInterpretationError("offline")),
    ).run(_command("Заказ"))
    empty_interpreter = FakeInterpreter()
    empty = await SchemaResolutionScenario(
        FakeCatalog({"Неизвестно": _answer()}),
        empty_interpreter,
    ).run(_command("Неизвестно"))

    assert failed["llm"]["status"] == "FALLBACK"
    assert empty["status"] == "NEEDS_CLARIFICATION"
    assert empty["llm"]["status"] == "SKIPPED"
    assert empty["resolutions"][0]["interpretation"]["kind"] == "unknown"
    assert empty_interpreter.calls == []


@pytest.mark.asyncio
async def test_catalog_authorization_precedes_all_lookup_and_llm_calls():
    catalog = FakeCatalog({"Заказ": _answer()}, denied=True)
    interpreter = FakeInterpreter()

    with pytest.raises(CatalogAccessDenied):
        await SchemaResolutionScenario(catalog, interpreter).run(_command("Заказ"))

    assert catalog.lookups == []
    assert interpreter.calls == []


@pytest.mark.asyncio
async def test_tenant_interpreter_uses_one_bounded_json_call():
    calls = []

    class FakeLlm:
        async def async_generate(self, tenant_id, system_prompt, payload):
            calls.append((tenant_id, system_prompt, payload))
            return json.dumps(
                {"schema_version": "1", "resolutions": [_interpretation("Заказ", "orders")]},
                ensure_ascii=False,
            )

    answer = catalog_result_from_mapping("Заказ", _answer(_entity("orders", "dwh.order_fact", column_count=45)))
    result = await TenantSchemaInterpreter(FakeLlm(), timeout_seconds=1).interpret(
        tenant_id="tenant-1",
        requirements="Нужны заказы",
        locale="ru",
        catalog_answers=[("Заказ", answer)],
    )

    assert result["resolutions"][0]["recommended_entity_id"] == "orders"
    assert len(calls) == 1
    assert calls[0][0] == "tenant-1"
    assert calls[0][2]["job_input"]["task_type"] == "RESOLVE_SQL_SCHEMA"
    assert calls[0][2]["prompt"]["name"] == "sql_schema_interpreter"
    assert calls[0][2]["prompt"]["content_hash"].startswith("sha256:")
    assert "{{" not in calls[0][1]
    assert '"additionalProperties": false' in calls[0][1]
    assert calls[0][2]["job_input"]["requirements"] == "Нужны заказы"
    assert len(calls[0][2]["catalog_answers"][0]["candidates"][0]["columns"]) == 3


@pytest.mark.asyncio
async def test_openmetadata_adapter_reuses_shared_service_and_enforces_dataset_acl(monkeypatch):
    calls = []

    class Catalog:
        def run(self, question, **kwargs):
            calls.append(("catalog", question, kwargs))
            return _answer(_entity("orders", "dwh.order_fact"))

    class Service:
        config = type("Config", (), {"dataset_id": "dataset-1"})()
        catalog = Catalog()

    class KnowledgebaseService:
        @staticmethod
        def accessible(dataset_id, actor_id):
            calls.append(("acl", dataset_id, actor_id))
            return True

    knowledgebase_module = ModuleType("api.db.services.knowledgebase_service")
    knowledgebase_module.KnowledgebaseService = KnowledgebaseService
    monkeypatch.setitem(sys.modules, knowledgebase_module.__name__, knowledgebase_module)

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def retrieve(service, question, actor_id):
        calls.append(("dataset", service, question, actor_id))
        return ([{"content": "verified"}], None)

    service = Service()
    monkeypatch.setattr(schema_adapter_module, "thread_pool_exec", run_sync)
    monkeypatch.setattr(schema_adapter_module, "retrieve_openmetadata_dataset_hits", retrieve)
    monkeypatch.setattr(schema_adapter_module, "get_openmetadata_service", lambda: service)
    resolver = OpenMetadataCatalogResolver()

    await resolver.authorize(actor_id="actor-1", is_admin=False)
    answer = await resolver.resolve(term="Заказ", actor_id="actor-1", locale="ru")
    details = await resolver.load_entity(entity_id="orders", actor_id="actor-1", locale="ru")

    assert resolver._service is service
    assert answer.candidates[0].id == "orders"
    assert answer.candidates[0].schema_loaded is False
    assert calls[0] == ("acl", "dataset-1", "actor-1")
    assert calls[1][0] == "dataset"
    assert calls[2][0] == "catalog"
    assert calls[2][2]["dataset_hits"] == [{"content": "verified"}]
    assert calls[2][2]["forced_intent"] == "discovery"
    assert details.candidates[0].schema_loaded is True
    assert calls[3][2]["selected_entity_id"] == "orders"
    assert calls[3][2]["forced_intent"] == "discovery"


@pytest.mark.asyncio
async def test_transport_facade_maps_validation_acl_and_command_context():
    captured = []

    class RecordingScenario:
        async def run(self, command):
            captured.append(command)
            return {"status": "READY"}

    result = await BusinessDocumentSqlQuerySchemaService.resolve(
        "tenant-1",
        "actor-1",
        {"terms": ["Заказ"], "requirements": "", "locale": "ru"},
        False,
        "AUTHOR_CREATOR",
        scenario=RecordingScenario(),
    )

    assert result == {"status": "READY"}
    assert captured[0].tenant_id == "tenant-1"
    with pytest.raises(ValidationError) as invalid:
        await BusinessDocumentSqlQuerySchemaService.resolve(
            "tenant-1",
            "actor-1",
            {"terms": []},
            False,
            "AUTHOR_CREATOR",
            scenario=RecordingScenario(),
        )
    assert invalid.value.code == "INVALID_SQL_SCHEMA_REQUEST"
    with pytest.raises(PermissionDeniedError):
        await BusinessDocumentSqlQuerySchemaService.resolve(
            "tenant-1",
            "actor-1",
            {"terms": ["Заказ"]},
            False,
            "AUTHOR_EDITOR",
            scenario=RecordingScenario(),
        )


@pytest.mark.asyncio
async def test_partial_catalog_failure_is_degraded_not_not_found():
    catalog = FakeCatalog(
        {
            "Заказ": _answer(_entity("orders", "dwh.order_fact")),
            "Клиент": CatalogLookupError("offline"),
        }
    )
    interpreter = FakeInterpreter({"schema_version": "1", "resolutions": [_interpretation("Заказ", "orders")]})

    result = await SchemaResolutionScenario(catalog, interpreter).run(_command("Заказ", "Клиент"))

    assert result["status"] == "DEGRADED"
    assert result["resolutions"][0]["lookup"]["status"] == "OK"
    assert result["resolutions"][1]["lookup"] == {
        "status": "ERROR",
        "error_code": "CATALOG_LOOKUP_FAILED",
        "retryable": True,
        "message": "Не удалось выполнить поиск по каталогу.",
    }
    assert result["resolutions"][1]["catalog_answer"]["entities"] == []


@pytest.mark.asyncio
async def test_field_recommendation_is_limited_to_catalog_column_ids():
    answer = _answer(_entity("orders", "dwh.order_fact"))
    catalog = FakeCatalog({"field_0": answer})
    interpretation = _interpretation("field_0", "orders")
    interpretation.update(
        {
            "kind": "field",
            "recommended_column_ids": ["dwh.order_fact.field_0"],
        }
    )

    result = await SchemaResolutionScenario(
        catalog,
        FakeInterpreter({"schema_version": "1", "resolutions": [interpretation]}),
    ).run(_command("field_0"))

    assert result["llm"]["status"] == "APPLIED"
    assert result["resolutions"][0]["interpretation"]["recommended_column_ids"] == ["dwh.order_fact.field_0"]


@pytest.mark.asyncio
async def test_lookup_batch_is_ordered_concurrent_and_time_bounded():
    class ConcurrentCatalog(FakeCatalog):
        def __init__(self):
            super().__init__({})
            self.active = 0
            self.peak = 0

        async def resolve(self, *, term: str, actor_id: str, locale: str):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.01 if term != "timeout" else 0.1)
                return catalog_result_from_mapping(term, _answer(_entity(term, f"dwh.{term}")))
            finally:
                self.active -= 1

    catalog = ConcurrentCatalog()
    result = await SchemaResolutionScenario(
        catalog,
        FakeInterpreter(error=SchemaInterpretationError("offline")),
        max_concurrency=2,
        lookup_timeout_seconds=0.03,
        batch_timeout_seconds=0.08,
    ).run(_command("first", "timeout", "third"))

    assert catalog.peak == 2
    assert [item["term"] for item in result["resolutions"]] == ["first", "timeout", "third"]
    assert result["resolutions"][1]["lookup"]["error_code"] == "CATALOG_TIMEOUT"
    assert result["status"] == "DEGRADED"


@pytest.mark.asyncio
async def test_selected_entity_batch_loads_full_schema_and_fingerprint():
    catalog = FakeCatalog(
        {
            "orders": _answer(_entity("orders", "dwh.order_fact", column_count=45)),
            "broken": CatalogLookupError("offline"),
        }
    )
    result = await SchemaEntityDetailsScenario(catalog).run(
        LoadSchemaEntitiesCommand(
            actor_id="actor-1",
            entity_ids=("orders", "broken"),
            locale="ru",
        )
    )

    assert result["status"] == "DEGRADED"
    assert result["entities"][0]["entity"]["schema_loaded"] is True
    assert len(result["entities"][0]["entity"]["column_details"]) == 45
    assert result["entities"][0]["entity"]["schema_fingerprint"].startswith("sha256:")
    assert result["entities"][1]["lookup"]["status"] == "ERROR"
