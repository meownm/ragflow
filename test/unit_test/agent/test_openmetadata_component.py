#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def openmetadata_class(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    for package_name, package_path in (
        ("agent", repo_root / "agent"),
        ("agent.component", repo_root / "agent" / "component"),
    ):
        package = ModuleType(package_name)
        package.__path__ = [str(package_path)]
        monkeypatch.setitem(sys.modules, package_name, package)

    base_module = ModuleType("agent.component.base")
    base_module.ComponentBase = type("ComponentBase", (), {})
    base_module.ComponentParamBase = type("ComponentParamBase", (), {})
    monkeypatch.setitem(sys.modules, "agent.component.base", base_module)

    module_path = repo_root / "agent" / "component" / "openmetadata.py"
    spec = importlib.util.spec_from_file_location("agent.component.openmetadata", module_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "agent.component.openmetadata", module)
    spec.loader.exec_module(module)
    return module.OpenMetadata


def _component_with_canvas_query(openmetadata_class, canvas_query=""):
    component = object.__new__(openmetadata_class)
    component._canvas = type("Canvas", (), {"get_sys_query": lambda self: canvas_query})()
    component._canvas.globals = {}
    return component


def test_question_reads_resolved_sys_query_parameter(openmetadata_class):
    component = _component_with_canvas_query(openmetadata_class)

    assert component._question({"sys.query": "  Какие таблицы относятся к MOEX?  "}) == "Какие таблицы относятся к MOEX?"


def test_question_uses_runtime_query_when_parameter_is_unavailable(openmetadata_class):
    component = _component_with_canvas_query(openmetadata_class)

    assert component._question({"query": "MOEX"}) == "MOEX"


def test_question_falls_back_to_canvas_global(openmetadata_class):
    component = _component_with_canvas_query(openmetadata_class, "EvaWiki")

    assert component._question({}) == "EvaWiki"


def test_question_rejects_empty_input(openmetadata_class):
    component = _component_with_canvas_query(openmetadata_class)

    with pytest.raises(ValueError, match="OpenMetadata query is empty"):
        component._question({})


def test_run_dispatches_the_configured_agent_role(openmetadata_class):
    calls = []

    class Service:
        def run_agent(self, role, question, **kwargs):
            calls.append((role, question, kwargs))
            return {"answer": "ok", "entities": []}

    component = _component_with_canvas_query(openmetadata_class)
    component._param = type("Param", (), {"role": "governance", "locale": "ru"})()

    result = component._run("Установи displayName", Service(), "user-1", dataset_hits=[{"id": "hit"}])

    assert result["answer"] == "ok"
    assert calls == [
        (
            "governance",
            "Установи displayName",
            {
                "user_id": "user-1",
                "locale": "ru",
                "dataset_hits": [{"id": "hit"}],
                "dataset_warning": None,
                "context": [],
            },
        )
    ]


def test_format_result_includes_requested_metadata_and_warnings(openmetadata_class):
    content = openmetadata_class._format_result(
        {
            "answer": "Найдена точная таблица.",
            "entities": [
                {
                    "id": "table-1",
                    "fqn": "postgres.db.public.orders",
                    "url": "http://omd/table/orders",
                    "owners": ["Commerce"],
                    "domains": ["Sales"],
                    "tags": ["PII"],
                    "matched_columns": ["order_id"],
                }
            ],
            "warnings": ["Снимок устарел"],
        },
        "ru",
    )

    assert "владелец: Commerce" in content
    assert "домен: Sales" in content
    assert "теги: PII" in content
    assert "совпавшие колонки: order_id" in content
    assert "Снимок устарел" in content


def test_format_result_exposes_relationships_quality_governance_and_sources(openmetadata_class):
    content = openmetadata_class._format_result(
        {
            "answer": "Найдены зарегистрированные связи.",
            "entity": {"id": "orders", "fqn": "postgres.db.public.orders"},
            "upstream": [
                {
                    "from": {"fqn": "postgres.db.public.customers"},
                    "to": {"fqn": "postgres.db.public.orders"},
                    "column_lineage": [{"from_columns": ["customers.id"], "to_column": "orders.customer_id"}],
                }
            ],
            "foreign_keys": [
                {
                    "from": {"fqn": "postgres.db.public.orders"},
                    "to": {"fqn": "postgres.db.public.customers"},
                    "from_columns": ["customer_id"],
                    "to_columns": ["id"],
                }
            ],
            "semantic_relations": [
                {
                    "to": {"fqn": "postgres.db.public.invoices"},
                    "shared_terms": ["Commerce.Order"],
                }
            ],
            "quality": {
                "test_cases": [
                    {
                        "id": "case-1",
                        "name": "customer_id_not_null",
                        "definition": "columnValuesToBeNotNull",
                        "status": "Success",
                    }
                ]
            },
            "governance_request": {
                "entity_id": "orders",
                "changes": {"description": "Orders"},
            },
            "sources": [{"label": "OpenMetadata", "url": "http://omd.example"}],
        },
        "ru",
    )

    assert "postgres.db.public.customers → postgres.db.public.orders" in content
    assert "customer_id → id" in content
    assert "Commerce.Order" in content
    assert "customer_id_not_null — columnValuesToBeNotNull, Success" in content
    assert "[Открыть Governance-форму](/openmetadata)" in content
    assert "[OpenMetadata](http://omd.example)" in content


def test_component_persists_and_reuses_eight_entity_context_turns(openmetadata_class):
    calls = []

    class Service:
        def run_agent(self, role, question, **kwargs):
            calls.append(kwargs)
            return {"answer": "ok", "entities": []}

    component = _component_with_canvas_query(openmetadata_class)
    component._param = type("Param", (), {"role": "catalog_copilot", "locale": "ru"})()
    component._canvas.globals["sys.openmetadata_context"] = [{"question": f"q-{index}", "entity_ids": [f"entity-{index}"]} for index in range(9)]
    outputs = {}
    component.set_output = outputs.__setitem__

    component._run("follow-up", Service(), "user-1", context=component._context())
    component._store_result(
        "follow-up",
        {"answer": "ok", "entities": [{"id": "entity-new", "fqn": "db.public.new"}]},
    )

    assert [turn["question"] for turn in calls[0]["context"]] == [f"q-{index}" for index in range(1, 9)]
    assert len(component._canvas.globals["sys.openmetadata_context"]) == 8
    assert component._canvas.globals["sys.openmetadata_context"][-1] == {
        "question": "follow-up",
        "entity_ids": ["entity-new"],
    }


def test_component_without_dataset_allows_only_ragflow_admin(openmetadata_class, monkeypatch):
    user_service_module = ModuleType("api.db.services.user_service")
    user_service_module.UserService = type("UserService", (), {"is_admin": staticmethod(lambda user_id: user_id == "admin")})
    monkeypatch.setitem(sys.modules, "api.db.services.user_service", user_service_module)
    component = _component_with_canvas_query(openmetadata_class)
    service = type("Service", (), {"config": type("Config", (), {"dataset_id": ""})()})()

    component._authorize(service, "admin")
    with pytest.raises(PermissionError, match="not available"):
        component._authorize(service, "reader")
