#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def om_module():
    repo_root = Path(__file__).resolve().parents[5]
    path = repo_root / "api" / "apps" / "services" / "openmetadata_copilot_service.py"
    spec = importlib.util.spec_from_file_location("test_openmetadata_copilot_service_module", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _raw_table(
    entity_id: str,
    name: str,
    *,
    service: str = "postgres",
    domain: str = "Data",
    description: str | None = None,
    updated_at: int = 1_780_000_000_000,
    processed_lineage: bool = False,
    columns: list[str] | None = None,
    owner: str = "data-owner",
    owner_display_name: str | None = None,
    fqn: str | None = None,
    table_constraints: list[dict] | None = None,
    glossary_terms: list[str] | None = None,
):
    table_fqn = fqn or f"{service}.db.public.{name}"
    tags = [{"tagFQN": "PII.Sensitive"}]
    tags.extend({"tagFQN": term, "source": "Glossary"} for term in glossary_terms or [])
    return {
        "id": entity_id,
        "name": name,
        "fullyQualifiedName": table_fqn,
        "version": 1.0,
        "updatedAt": updated_at,
        "updatedBy": "admin",
        "description": description,
        "columns": [
            {
                "name": column,
                "fullyQualifiedName": f"{table_fqn}.{column}",
                **({"description": "key"} if index == 0 else {}),
            }
            for index, column in enumerate(columns or ["id", "value"])
        ],
        "owners": [{"name": owner, "displayName": owner_display_name}],
        "domains": [{"name": domain}],
        "tags": tags,
        "service": {"name": service},
        "databaseSchema": {"name": "public"},
        "database": {"name": "db"},
        "processedLineage": processed_lineage,
        "tableConstraints": deepcopy(table_constraints or []),
    }


class FakeClient:
    def __init__(self, tables):
        self.tables = {table["id"]: deepcopy(table) for table in tables}
        self.patch_calls = []
        self.search_error = None
        self.test_case_count = 0
        self.test_cases = []
        self.lineage_payload = {"nodes": [], "upstreamEdges": [], "downstreamEdges": []}
        self.rdf_status_payload = {
            "enabled": True,
            "storageType": "FUSEKI",
            "inference": {"enabled": True, "defaultLevel": "NONE"},
        }
        self.knowledge_graph_payload = {"nodes": [], "edges": []}

    def list_tables(self, _max_entities):
        return [deepcopy(table) for table in self.tables.values()]

    def get(self, path, *, params=None):
        del params
        if path == "/api/v1/system/version":
            return {"version": "1.12.10"}
        if path == "/api/v1/dataQuality/testCases":
            return {"data": [], "paging": {"total": self.test_case_count}}
        counts = {
            "/api/v1/services/databaseServices": 2,
            "/api/v1/domains": 2,
            "/api/v1/glossaries": 3,
            "/api/v1/dashboards": 0,
            "/api/v1/pipelines": 0,
            "/api/v1/topics": 0,
            "/api/v1/mlmodels": 0,
            "/api/v1/dataProducts": 0,
        }
        if path in counts:
            return {"data": [], "paging": {"total": counts[path]}}
        raise AssertionError(f"Unexpected GET {path}")

    def search_tables(self, query, size):
        del size
        if self.search_error:
            raise self.search_error
        terms = query.casefold().split()
        return [deepcopy(table) for table in self.tables.values() if any(term in table["fullyQualifiedName"].casefold() for term in terms)]

    def get_table(self, entity_id):
        return deepcopy(self.tables[entity_id])

    def lineage(self, entity_id, depth):
        del entity_id, depth
        return deepcopy(self.lineage_payload)

    def list_test_cases(self, entity_fqn, max_results=100):
        prefix = f"<#E::table::{entity_fqn}"
        matches = [deepcopy(test_case) for test_case in self.test_cases if test_case.get("entityLink") == f"{prefix}>" or str(test_case.get("entityLink") or "").startswith(f"{prefix}::columns::")]
        return {
            "data": matches[:max_results],
            "total": len(matches),
            "truncated": len(matches) > max_results,
        }

    def rdf_status(self):
        return deepcopy(self.rdf_status_payload)

    def knowledge_graph(self, entity_id, depth):
        del entity_id, depth
        return deepcopy(self.knowledge_graph_payload)

    def patch(self, path, patch):
        entity_id = path.rsplit("/", 1)[-1]
        entity = self.tables[entity_id]
        assert patch[0] == {"op": "test", "path": "/version", "value": entity["version"]}
        for operation in patch[1:]:
            field = operation["path"].lstrip("/")
            if operation["op"] == "add":
                assert field not in entity
                entity[field] = operation["value"]
            elif operation["op"] == "replace":
                assert field in entity
                entity[field] = operation["value"]
            elif operation["op"] == "remove":
                assert field in entity
                entity.pop(field)
            else:
                raise AssertionError(f"Unexpected patch operation: {operation}")
        entity["version"] += 0.1
        self.patch_calls.append(deepcopy(patch))
        return deepcopy(entity)


def _config(module, *, write_enabled=False, dataset_id=""):
    return module.OpenMetadataConfig(
        base_url="http://omd.test:8585",
        public_url="http://omd.example",
        username="reader@example.test",
        password="secret",
        jwt_token="",
        timeout_seconds=1,
        retries=0,
        cache_ttl_seconds=900,
        stale_after_hours=1,
        max_entities=5000,
        max_results=25,
        write_enabled=write_enabled,
        confirmation_ttl_seconds=300,
        dataset_id=dataset_id,
    )


def _service(module, tables, *, write_enabled=False, dataset_id=""):
    client = FakeClient(tables)
    service = module.OpenMetadataCopilotService(
        _config(module, write_enabled=write_enabled, dataset_id=dataset_id),
        client=client,
        secret_key="unit-test-secret",
    )
    return service, client


def test_status_and_starter_questions_are_capability_gated(om_module):
    table = _raw_table(
        "11111111-1111-4111-8111-111111111111",
        "chunks",
        processed_lineage=True,
    )
    service, _client = _service(om_module, [table])

    status = service.status()
    starters = service.starter_questions.generate()

    assert status["connected"] is True
    assert status["capabilities"]["tables"] == 1
    assert status["capabilities"]["test_cases"] == 0
    assert status["freshness"]["stale"] is True
    assert status["knowledge_graph"]["enabled"] is True
    assert status["knowledge_graph"]["storage_type"] == "FUSEKI"
    assert {item["id"] for item in starters["questions"]} == {
        "missing-descriptions",
        "top-domain",
        "recent",
        "quality-gap",
    }
    assert all(item.get("action", {}).get("type") for item in starters["questions"])
    assert all("dashboard" not in item["question"].casefold() for item in starters["questions"])


def test_status_and_capability_answers_use_the_user_domain_scope(om_module, monkeypatch):
    finance = _raw_table(
        "12121212-1212-4212-8212-121212121212",
        "orders",
        domain="Finance",
    )
    hr = _raw_table(
        "13131313-1313-4313-8313-131313131313",
        "employees",
        domain="HR",
    )
    service, _client = _service(om_module, [finance, hr])
    monkeypatch.setenv("OPENMETADATA_USER_DOMAIN_MAP", '{"reader":["Finance"]}')

    status = service.status(user_id="reader")
    answer = service.catalog.run("catalog capabilities", user_id="reader", locale="en")
    starters = service.starter_questions.generate(user_id="reader", locale="en")

    assert status["capabilities"]["tables"] == 1
    assert status["capabilities"]["columns"] == 2
    assert status["capabilities"]["domains"] == 1
    assert status["capabilities"]["test_cases"] is None
    assert answer["capabilities"]["tables"] == 1
    assert "1 tables" in answer["answer"]
    assert "quality-gap" not in {item["id"] for item in starters["questions"]}


def test_password_login_retries_patch_once_after_401(om_module):
    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.content = b"{}"

        def json(self):
            return self._payload

    class Session:
        def __init__(self):
            self.request_calls = []
            self.login_calls = 0

        def post(self, *_args, **_kwargs):
            self.login_calls += 1
            return Response(200, {"accessToken": "fresh"})

        def request(self, method, *_args, **kwargs):
            self.request_calls.append((method, kwargs["headers"]["Authorization"]))
            if len(self.request_calls) == 1:
                return Response(401, {})
            return Response(200, {"ok": True})

    session = Session()
    client = om_module.OpenMetadataClient(_config(om_module), session=session)
    client._token = "expired"

    result = client.patch(
        "/api/v1/tables/11111111-1111-4111-8111-111111111111",
        [{"op": "test", "path": "/version", "value": 1.0}],
    )

    assert result == {"ok": True}
    assert session.login_calls == 1
    assert session.request_calls == [
        ("PATCH", "Bearer expired"),
        ("PATCH", "Bearer fresh"),
    ]


def test_client_lists_table_test_cases_with_entity_link_and_pagination(om_module):
    client = object.__new__(om_module.OpenMetadataClient)
    calls = []

    def fake_get(path, *, params=None):
        calls.append((path, deepcopy(params)))
        if len(calls) == 1:
            return {
                "data": [{"id": "case-1"}],
                "paging": {"total": 2, "after": "next-page"},
            }
        return {"data": [{"id": "case-2"}], "paging": {"total": 2}}

    client.get = fake_get

    result = client.list_test_cases("postgres.db.public.orders")

    assert result == {
        "data": [{"id": "case-1"}, {"id": "case-2"}],
        "total": 2,
        "truncated": False,
    }
    assert calls[0] == (
        "/api/v1/dataQuality/testCases",
        {
            "entityLink": "<#E::table::postgres.db.public.orders>",
            "fields": "testDefinition,testSuite,testCaseResult",
            "limit": 100,
        },
    )
    assert calls[1][1]["after"] == "next-page"


def test_structured_starters_execute_catalog_operations(om_module):
    older_missing = _raw_table(
        "10101010-1010-4010-8010-101010101010",
        "older_missing",
        updated_at=1_770_000_000_000,
    )
    newer_described = _raw_table(
        "20202020-2020-4020-8020-202020202020",
        "newer_described",
        description="Documented",
        updated_at=1_790_000_000_000,
    )
    service, _client = _service(om_module, [older_missing, newer_described])

    missing = service.catalog.run(
        "Which tables are missing descriptions?",
        action={"type": "missing_descriptions"},
        locale="en",
    )
    recent = service.catalog.run(
        "Which tables were updated most recently?",
        action={"type": "recent"},
        locale="en",
    )

    assert [entity["id"] for entity in missing["entities"]] == [older_missing["id"]]
    assert missing["total_matches"] == 1
    assert recent["entities"][0]["id"] == newer_described["id"]
    assert "most recently updated" in recent["answer"]


def test_follow_up_uses_previous_entity_context(om_module):
    missing = _raw_table("30303030-3030-4030-8030-303030303030", "missing")
    described = _raw_table(
        "40404040-4040-4040-8040-404040404040",
        "described",
        description="Documented",
    )
    unrelated = _raw_table("50505050-5050-4050-8050-505050505050", "unrelated")
    service, _client = _service(om_module, [missing, described, unrelated])

    result = service.catalog.run(
        "А какие из них без описания?",
        context=[{"question": "Покажи таблицы домена Data", "entity_ids": [missing["id"], described["id"]]}],
    )

    assert result["context_applied"] is True
    assert [entity["id"] for entity in result["entities"]] == [missing["id"]]
    assert unrelated["id"] not in {entity["id"] for entity in result["entities"]}


def test_follow_up_uses_only_most_recent_non_empty_turn(om_module):
    older = _raw_table("51515151-5151-4151-8151-515151515151", "evawiki")
    latest_missing = _raw_table("52525252-5252-4252-8252-525252525252", "moex_missing")
    latest_described = _raw_table(
        "53535353-5353-4353-8353-535353535353",
        "moex_described",
        description="Documented",
    )
    service, _client = _service(om_module, [older, latest_missing, latest_described])

    result = service.catalog.run(
        "Какая из них без описания?",
        context=[
            {"question": "Покажи EvaWiki", "entity_ids": [older["id"]]},
            {"question": "Покажи MOEX", "entity_ids": [latest_missing["id"], latest_described["id"]]},
        ],
    )

    assert result["context_applied"] is True
    assert [entity["id"] for entity in result["entities"]] == [latest_missing["id"]]


def test_empty_discovery_query_supports_pagination(om_module):
    tables = [
        _raw_table("60606060-6060-4060-8060-606060606060", "alpha"),
        _raw_table("70707070-7070-4070-8070-707070707070", "beta"),
        _raw_table("80808080-8080-4080-8080-808080808080", "gamma"),
    ]
    service, _client = _service(om_module, tables)

    result = service.discovery.search("", limit=1, offset=1, sort="fqn")

    assert result["total_matches"] == 3
    assert result["offset"] == 1
    assert [entity["technical_name"] for entity in result["entities"]] == ["beta"]


def test_untyped_column_relationships_do_not_enable_relationship_starter(om_module):
    table = _raw_table(
        "12121212-1212-4212-8212-121212121212",
        "foreign_key_only",
    )
    table["upstreamEntityRelationship"] = [{"entity": {"type": "table"}}]
    service, _client = _service(om_module, [table])

    starters = service.starter_questions.generate()

    assert "relationships" not in {item["id"] for item in starters["questions"]}


def test_starter_requires_a_real_visible_lineage_edge(om_module):
    source = _raw_table(
        "13131313-1313-4313-8313-131313131313",
        "source",
        processed_lineage=True,
    )
    target = _raw_table("14141414-1414-4414-8414-141414141414", "target")
    service, client = _service(om_module, [source, target])

    without_edges = service.starter_questions.generate()
    client.lineage_payload = {
        "nodes": [],
        "upstreamEdges": [{"fromEntity": source["id"], "toEntity": target["id"]}],
        "downstreamEdges": [],
    }
    service.projection.invalidate()
    with_edge = service.starter_questions.generate()

    assert "relationships" not in {item["id"] for item in without_edges["questions"]}
    assert "relationships" in {item["id"] for item in with_edge["questions"]}


def test_starter_does_not_offer_recent_updates_without_timestamps(om_module):
    table = _raw_table(
        "15151515-1515-4515-8515-151515151515",
        "undated",
        updated_at=None,
    )
    service, _client = _service(om_module, [table])

    starters = service.starter_questions.generate()

    assert "recent" not in {item["id"] for item in starters["questions"]}


def test_discovery_deduplicates_remote_and_projection_results(om_module):
    table = _raw_table(
        "22222222-2222-4222-8222-222222222222",
        "orders",
        description="Orders fact table",
    )
    service, _client = _service(om_module, [table])

    result = service.discovery.search("orders")

    assert len(result["entities"]) == 1
    assert result["entities"][0]["matched_by"] == ["catalog_projection", "omd_search"]
    assert result["retrieval"] == "hybrid_rrf"


def test_dataset_retrieval_is_fused_but_cannot_reintroduce_stale_entities(om_module):
    table = _raw_table(
        "23232323-2323-4232-8232-232323232323",
        "orders",
        description="Commercial transactions",
    )
    service, _client = _service(om_module, [table])
    result = service.discovery.search(
        "commercial transactions",
        dataset_hits=[
            {
                "doc_id": "doc-stale-version",
                "similarity": 1.0,
                "metadata": {
                    "omd_entity_id": table["id"],
                    "omd_fqn": table["fullyQualifiedName"],
                    "omd_updated_at_epoch": table["updatedAt"] - 1,
                },
            },
            {
                "doc_id": "doc-current",
                "similarity": 0.91,
                "metadata": {
                    "omd_entity_id": table["id"],
                    "omd_fqn": table["fullyQualifiedName"],
                    "omd_updated_at_epoch": table["updatedAt"],
                },
            },
            {
                "doc_id": "doc-deleted",
                "similarity": 0.99,
                "metadata": {
                    "omd_entity_id": "24242424-2424-4242-8242-242424242424",
                    "omd_fqn": "postgres.db.public.deleted_table",
                },
            },
        ],
    )

    assert [entity["id"] for entity in result["entities"]] == [table["id"]]
    assert "ragflow_dataset" in result["entities"][0]["matched_by"]
    assert result["retrieval"] == "omd_dataset_hybrid_rrf"


def test_dataset_retrieval_respects_current_domain_scope(om_module, monkeypatch):
    finance = _raw_table("25252525-2525-4252-8252-252525252525", "ledger", domain="Finance")
    hr = _raw_table("26262626-2626-4262-8262-262626262626", "payroll", domain="HR")
    service, _client = _service(om_module, [finance, hr])
    monkeypatch.setenv("OPENMETADATA_USER_DOMAIN_MAP", '{"reader-a":["Finance"]}')

    result = service.discovery.search(
        "sensitive records",
        user_id="reader-a",
        dataset_hits=[
            {"similarity": 0.8, "metadata": {"omd_entity_id": finance["id"], "omd_updated_at_epoch": finance["updatedAt"]}},
            {"similarity": 0.99, "metadata": {"omd_entity_id": hr["id"], "omd_updated_at_epoch": hr["updatedAt"]}},
        ],
    )

    assert [entity["id"] for entity in result["entities"]] == [finance["id"]]


def test_dataset_retrieval_role_uses_only_current_semantic_hits(om_module):
    semantic = _raw_table(
        "27272727-2727-4272-8272-272727272727",
        "semantic_match",
        description="Commercial transactions",
    )
    lexical = _raw_table(
        "28282828-2828-4282-8282-282828282828",
        "commercial_transactions",
    )
    service, _client = _service(om_module, [semantic, lexical], dataset_id="dataset-1")

    result = service.run_agent(
        "dataset_retrieval",
        "commercial transactions",
        dataset_hits=[
            {
                "similarity": 0.91,
                "metadata": {
                    "omd_entity_id": semantic["id"],
                    "omd_updated_at_epoch": semantic["updatedAt"],
                },
            }
        ],
    )

    assert [entity["id"] for entity in result["entities"]] == [semantic["id"]]
    assert result["retrieval"] == "ragflow_dataset"


def test_sources_include_dataset_only_when_it_contributes_to_the_intent(om_module):
    table = _raw_table(
        "29292929-2929-4292-8292-292929292929",
        "orders",
        processed_lineage=True,
    )
    service, _client = _service(om_module, [table], dataset_id="dataset-1")

    impact = service.run_agent(
        "impact_quality",
        f"Покажи lineage {table['fullyQualifiedName']}",
        dataset_hits=[],
    )
    discovery = service.run_agent(
        "discovery",
        "orders",
        dataset_hits=[
            {
                "similarity": 0.9,
                "metadata": {
                    "omd_entity_id": table["id"],
                    "omd_updated_at_epoch": table["updatedAt"],
                },
            }
        ],
    )

    assert [source["label"] for source in impact["sources"]] == ["OpenMetadata"]
    assert [source["label"] for source in discovery["sources"]] == [
        "OpenMetadata",
        "RAGFlow Dataset",
    ]


def test_discovery_fails_closed_for_unmapped_user_domains(om_module, monkeypatch):
    finance = _raw_table("33333333-3333-4333-8333-333333333333", "orders", domain="Finance")
    hr = _raw_table("44444444-4444-4444-8444-444444444444", "orders", service="warehouse", domain="HR")
    service, _client = _service(om_module, [finance, hr])
    monkeypatch.setenv("OPENMETADATA_USER_DOMAIN_MAP", '{"reader-a":["Finance"]}')

    allowed = service.discovery.search("orders", user_id="reader-a")
    denied = service.discovery.search("orders", user_id="unknown-user")

    assert [entity["domains"] for entity in allowed["entities"]] == [["Finance"]]
    assert denied["entities"] == []
    assert denied["total_visible_candidates"] == 0


def test_impact_requires_clarification_for_duplicate_short_name(om_module):
    first = _raw_table("55555555-5555-4555-8555-555555555555", "orders", service="erp")
    second = _raw_table("66666666-6666-4666-8666-666666666666", "orders", service="warehouse")
    service, _client = _service(om_module, [first, second])

    result = service.impact_quality.impact("Что зависит от таблицы orders?")

    assert result["needs_clarification"] is True
    assert len(result["entities"]) == 2


def test_english_dependency_question_is_routed_to_impact(om_module):
    first = _raw_table("57575757-5757-4757-8757-575757575757", "orders", service="erp")
    second = _raw_table("58585858-5858-4858-8858-585858585858", "orders", service="warehouse")
    service, _client = _service(om_module, [first, second])

    result = service.catalog.run("What depends on orders?", locale="en")

    assert result["intent"] == "impact"
    assert result["needs_clarification"] is True
    assert len(result["entities"]) == 2


def test_selected_clarification_candidate_is_used(om_module):
    first = _raw_table("91919191-9191-4191-8191-919191919191", "orders", service="erp")
    second = _raw_table("92929292-9292-4292-8292-929292929292", "orders", service="warehouse")
    service, _client = _service(om_module, [first, second])

    result = service.catalog.run(
        "Что зависит от таблицы orders?",
        selected_entity_id=second["id"],
    )

    assert result.get("needs_clarification") is not True
    assert result["entity"]["id"] == second["id"]


def test_impact_does_not_guess_a_fuzzy_result_and_honors_filters(om_module):
    fuzzy = _raw_table(
        "93939393-9393-4393-8393-939393939393",
        "accounts",
        description="Customer data records",
    )
    finance = _raw_table(
        "94949494-9494-4494-8494-949494949494",
        "orders",
        service="erp",
        domain="Finance",
    )
    hr = _raw_table(
        "95959595-9595-4595-8595-959595959595",
        "orders",
        service="warehouse",
        domain="HR",
    )
    service, _client = _service(om_module, [fuzzy, finance, hr])

    fuzzy_result = service.catalog.run("What depends on customer data?", locale="en")
    filtered_result = service.catalog.run(
        "What depends on orders?",
        filters={"domain": "Finance"},
        locale="en",
    )

    assert fuzzy_result["needs_clarification"] is True
    assert [entity["id"] for entity in fuzzy_result["entities"]] == [fuzzy["id"]]
    assert filtered_result.get("needs_clarification") is not True
    assert filtered_result["entity"]["id"] == finance["id"]


def test_lineage_deduplicates_cycles_without_inference(om_module):
    table = _raw_table("77777777-7777-4777-8777-777777777777", "chunks", processed_lineage=True)
    service, client = _service(om_module, [table])
    other_id = "88888888-8888-4888-8888-888888888888"
    client.lineage_payload = {
        "nodes": [{"id": other_id, "type": "table", "name": "results", "fullyQualifiedName": "pg.db.public.results"}],
        "upstreamEdges": [
            {
                "fromEntity": other_id,
                "toEntity": table["id"],
                "lineageDetails": {
                    "source": {"type": "Manual"},
                    "columnsLineage": [
                        {
                            "fromColumns": ["pg.db.public.results.result_id"],
                            "toColumn": "postgres.db.public.chunks.id",
                        }
                    ],
                },
            },
            {"fromEntity": other_id, "toEntity": table["id"], "lineageDetails": {"source": {"type": "Manual"}}},
        ],
        "downstreamEdges": [{"fromEntity": table["id"], "toEntity": other_id, "lineageDetails": {"source": {"type": "Manual"}}}],
    }

    result = service.impact_quality.impact("lineage chunks", depth=3)

    assert len(result["nodes"]) == 2
    assert len(result["upstream"]) == 1
    assert len(result["downstream"]) == 1
    assert result["upstream"][0]["source"] == "Manual"
    assert result["upstream"][0]["column_lineage"] == [
        {
            "from_columns": ["pg.db.public.results.result_id"],
            "to_column": "postgres.db.public.chunks.id",
        }
    ]


def test_impact_reports_foreign_keys_and_shared_glossary_relations(om_module):
    customers = _raw_table(
        "73737373-7373-4373-8373-737373737373",
        "customers",
        columns=["id", "name"],
        glossary_terms=["Business.Customer"],
    )
    orders = _raw_table(
        "74747474-7474-4474-8474-747474747474",
        "orders",
        columns=["id", "customer_id"],
        glossary_terms=["Business.Customer"],
        table_constraints=[
            {
                "constraintType": "FOREIGN_KEY",
                "columns": ["customer_id"],
                "referredColumns": [f"{customers['fullyQualifiedName']}.id"],
                "relationshipType": "MANY_TO_ONE",
            }
        ],
    )
    service, client = _service(om_module, [customers, orders])
    glossary_id = "75757575-7575-4575-8575-757575757575"
    orders_uri = f"https://open-metadata.org/entity/table/{orders['id']}"
    customers_uri = f"https://open-metadata.org/entity/table/{customers['id']}"
    glossary_uri = f"https://open-metadata.org/entity/glossaryTerm/{glossary_id}"
    client.knowledge_graph_payload = {
        "nodes": [
            {
                "id": orders_uri,
                "entityId": orders["id"],
                "type": "table",
                "label": "orders",
                "fullyQualifiedName": orders["fullyQualifiedName"],
            },
            {
                "id": customers_uri,
                "entityId": customers["id"],
                "type": "table",
                "label": "customers",
                "fullyQualifiedName": customers["fullyQualifiedName"],
            },
            {
                "id": glossary_uri,
                "entityId": glossary_id,
                "type": "glossaryTerm",
                "label": "Customer",
                "fullyQualifiedName": "Business.Customer",
            },
        ],
        "edges": [
            {"from": orders_uri, "to": glossary_uri, "label": "Has Glossary Term"},
            {"from": customers_uri, "to": glossary_uri, "label": "Has Glossary Term"},
        ],
    }

    entity = service.discovery.search("orders")["entities"][0]
    result = service.impact_quality.impact("orders", entity=entity)

    assert len(result["foreign_keys"]) == 1
    foreign_key = result["foreign_keys"][0]
    assert foreign_key["from"]["id"] == orders["id"]
    assert foreign_key["to"]["id"] == customers["id"]
    assert foreign_key["relationship_type"] == "foreign_key"
    assert foreign_key["source"] == "OpenMetadata table constraint"
    assert foreign_key["from_columns"] == ["customer_id"]
    assert foreign_key["to_columns"] == ["id"]
    assert foreign_key["cardinality"] == "MANY_TO_ONE"
    assert result["semantic_relations"][0]["to"]["id"] == customers["id"]
    assert result["semantic_relations"][0]["shared_terms"] == ["Business.Customer"]
    assert {node["id"] for node in result["knowledge_graph"]["nodes"]} == {
        orders["id"],
        customers["id"],
        glossary_id,
    }
    assert result["knowledge_graph"]["edges"] == [
        {"from": orders["id"], "to": glossary_id, "label": "Has Glossary Term"},
        {"from": customers["id"], "to": glossary_id, "label": "Has Glossary Term"},
    ]
    assert result["relationship_counts"] == {
        "lineage_upstream": 0,
        "lineage_downstream": 0,
        "foreign_key_outbound": 1,
        "foreign_key_inbound": 0,
        "semantic": 1,
        "knowledge_graph_edges": 2,
    }


def test_lineage_drops_nodes_outside_the_user_domain_scope(om_module, monkeypatch):
    finance = _raw_table(
        "81818181-8181-4181-8181-818181818181",
        "finance_orders",
        domain="Finance",
    )
    hr = _raw_table(
        "82828282-8282-4282-8282-828282828282",
        "employee_records",
        domain="HR",
    )
    service, client = _service(om_module, [finance, hr])
    monkeypatch.setenv("OPENMETADATA_USER_DOMAIN_MAP", '{"reader":["Finance"]}')
    visible_finance = service.discovery.search("finance_orders", user_id="reader")["entities"][0]
    client.lineage_payload = {
        "nodes": [hr],
        "upstreamEdges": [],
        "downstreamEdges": [
            {"fromEntity": finance["id"], "toEntity": hr["id"]},
        ],
    }

    result = service.impact_quality.impact(
        "finance_orders",
        user_id="reader",
        entity=visible_finance,
    )

    assert [node["id"] for node in result["nodes"]] == [finance["id"]]
    assert result["downstream"] == []


def test_zero_quality_tests_is_not_reported_as_green(om_module):
    table = _raw_table("99999999-9999-4999-8999-999999999999", "events")
    service, _client = _service(om_module, [table])

    result = service.impact_quality.quality("Есть ли проверки качества?")

    assert result["status"] == "not_configured"
    assert "не означает" in result["message"]


def test_quality_returns_only_test_cases_for_the_selected_table(om_module):
    events = _raw_table("98989898-9898-4989-8989-989898989898", "events")
    orders = _raw_table("97979797-9797-4979-8979-979797979797", "orders")
    service, client = _service(om_module, [events, orders])
    client.test_cases = [
        {
            "id": "event-test",
            "name": "event_id_not_null",
            "fullyQualifiedName": f"{events['fullyQualifiedName']}.event_id_not_null",
            "entityLink": f"<#E::table::{events['fullyQualifiedName']}::columns::id>",
            "testDefinition": {"name": "columnValuesToBeNotNull"},
            "testSuite": {"name": "events.testSuite"},
            "testCaseResult": {"testCaseStatus": "Success", "timestamp": 1_780_000_000_000},
        },
        {
            "id": "order-test",
            "name": "order_id_not_null",
            "entityLink": f"<#E::table::{orders['fullyQualifiedName']}::columns::id>",
        },
    ]

    result = service.impact_quality.quality(f"Какие test cases настроены для {events['fullyQualifiedName']}?")

    assert result["entity"]["id"] == events["id"]
    assert result["test_case_count"] == 1
    assert len(result["test_cases"]) == 1
    assert result["test_cases"][0] | {"result_timestamp": None} == {
        "id": "event-test",
        "name": "event_id_not_null",
        "fqn": f"{events['fullyQualifiedName']}.event_id_not_null",
        "entity_link": f"<#E::table::{events['fullyQualifiedName']}::columns::id>",
        "definition": "columnValuesToBeNotNull",
        "suite": "events.testSuite",
        "status": "Success",
        "result_timestamp": None,
    }
    assert result["test_cases"][0]["result_timestamp"]
    assert events["fullyQualifiedName"] in result["message"]


def test_governance_preview_confirm_and_replay_guard(om_module, monkeypatch):
    table = _raw_table("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "customers", description="old")
    service, client = _service(om_module, [table], write_enabled=True)
    used = set()
    monkeypatch.setattr(
        service.governance,
        "_consume_nonce",
        lambda nonce: False if nonce in used else (used.add(nonce) is None),
    )

    preview = service.governance.preview(
        user_id="admin",
        entity_id=table["id"],
        changes={"description": "new"},
    )
    result = service.governance.confirm(
        user_id="admin",
        confirmation_token=preview["confirmation_token"],
    )

    assert preview["diff"] == [{"field": "description", "before": "old", "after": "new"}]
    assert result["applied"] is True
    assert result["entity"]["description"] == "new"
    assert len(client.patch_calls) == 1
    with pytest.raises(om_module.OpenMetadataConflictError, match="уже использовано"):
        service.governance.confirm(user_id="admin", confirmation_token=preview["confirmation_token"])


def test_governance_uses_add_for_absent_fields_and_remove_when_clearing(om_module, monkeypatch):
    missing = _raw_table("a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1", "missing")
    missing.pop("description")
    service, client = _service(om_module, [missing], write_enabled=True)
    monkeypatch.setattr(service.governance, "_consume_nonce", lambda _nonce: True)

    preview = service.governance.preview(
        user_id="admin",
        entity_id=missing["id"],
        changes={"description": "Documented"},
    )
    service.governance.confirm(user_id="admin", confirmation_token=preview["confirmation_token"])

    assert client.patch_calls[0][1] == {"op": "add", "path": "/description", "value": "Documented"}

    clear_preview = service.governance.preview(
        user_id="admin",
        entity_id=missing["id"],
        changes={"description": None},
    )
    service.governance.confirm(user_id="admin", confirmation_token=clear_preview["confirmation_token"])

    assert client.patch_calls[1][1] == {"op": "remove", "path": "/description"}


def test_governance_rejects_wrong_user_and_version_conflict(om_module, monkeypatch):
    table = _raw_table("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "customers", description="old")
    service, client = _service(om_module, [table], write_enabled=True)
    monkeypatch.setattr(service.governance, "_consume_nonce", lambda _nonce: True)
    preview = service.governance.preview(
        user_id="admin",
        entity_id=table["id"],
        changes={"description": "new"},
    )

    with pytest.raises(om_module.OpenMetadataPermissionError, match="другому пользователю"):
        service.governance.confirm(user_id="reader", confirmation_token=preview["confirmation_token"])

    client.tables[table["id"]]["version"] = 2.0
    with pytest.raises(om_module.OpenMetadataConflictError, match="изменилась после preview"):
        service.governance.confirm(user_id="admin", confirmation_token=preview["confirmation_token"])
    assert client.patch_calls == []


def test_governance_is_disabled_and_fields_are_allowlisted(om_module):
    table = _raw_table("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "customers")
    disabled, _client = _service(om_module, [table], write_enabled=False)
    with pytest.raises(om_module.OpenMetadataPermissionError, match="отключены"):
        disabled.governance.preview(user_id="admin", entity_id=table["id"], changes={"description": "x"})

    enabled, _client = _service(om_module, [table], write_enabled=True)
    with pytest.raises(ValueError, match="Поля не разрешены"):
        enabled.governance.preview(user_id="admin", entity_id=table["id"], changes={"deleted": True})


def test_governance_preserves_nullable_fields_and_rejects_noop(om_module):
    table = _raw_table("dddddddd-dddd-4ddd-8ddd-dddddddddddd", "customers")
    service, _client = _service(om_module, [table], write_enabled=True)
    normalized = om_module.normalize_table(table, service.config.public_url)

    assert normalized["display_name"] is None
    assert normalized["description"] is None
    with pytest.raises(ValueError, match="совпадают"):
        service.governance.preview(
            user_id="admin",
            entity_id=table["id"],
            changes={"displayName": None, "description": None},
        )


def test_structural_column_queries_return_only_complete_matches(om_module):
    llm_logs = _raw_table(
        "e1111111-1111-4111-8111-111111111111",
        "llm_logs",
        domain="ApplicationDocs",
        columns=["id", "request_id", "model", "input_json", "output_json", "success", "error"],
    )
    request_log = _raw_table(
        "e2222222-2222-4222-8222-222222222222",
        "llm_requests_log",
        domain="KnowledgeSearch",
        columns=["id", "model_name", "prompt", "raw_response", "is_success", "error_message"],
    )
    service, _client = _service(om_module, [llm_logs, request_log])

    result = service.run_agent(
        "dataset_retrieval",
        "Где хранятся model, input_json, output_json, success и error для LLM-запросов?",
    )

    assert result["agent"] == "dataset_retrieval"
    assert result["intent"] == "discovery"
    assert [entity["id"] for entity in result["entities"]] == [llm_logs["id"]]
    assert result["entities"][0]["matched_columns"] == ["model", "input_json", "output_json", "success", "error"]
    assert "точная таблица" in result["answer"]


def test_natural_owner_domain_and_column_filters_are_strict(om_module):
    cdr = _raw_table(
        "e3333333-3333-4333-8333-333333333333",
        "cdr",
        domain="Telephony",
        owner="owner_telephony",
        owner_display_name="Telephony",
        columns=["id", "src", "dst", "billsec", "disposition", "linkedid"],
    )
    cel = _raw_table(
        "e4444444-4444-4444-8444-444444444444",
        "cel",
        domain="Telephony",
        owner="owner_telephony",
        owner_display_name="Telephony",
        columns=["id", "eventtype", "linkedid"],
    )
    unrelated = _raw_table(
        "e5555555-5555-4555-8555-555555555555",
        "calls",
        domain="Other",
        owner="owner_other",
        columns=["id", "src", "dst", "billsec", "disposition", "linkedid"],
    )
    service, _client = _service(om_module, [cdr, cel, unrelated])

    owner_result = service.run_agent(
        "discovery",
        "Найди все таблицы владельца owner_telephony в домене Telephony",
    )
    column_result = service.run_agent(
        "catalog_copilot",
        "Найди таблицу домена Telephony с полями src, dst, billsec, disposition и linkedid",
    )

    assert {entity["id"] for entity in owner_result["entities"]} == {cdr["id"], cel["id"]}
    assert [entity["id"] for entity in column_result["entities"]] == [cdr["id"]]
    assert owner_result["constraints"] == {"domain": "Telephony", "owner": "owner_telephony"}


def test_domain_column_query_excludes_tables_without_requested_column(om_module):
    api_logs = _raw_table(
        "e6666666-6666-4666-8666-666666666666",
        "api_request_logs",
        domain="ApplicationDocs",
        columns=["id", "request_id"],
    )
    errors = _raw_table(
        "e7777777-7777-4777-8777-777777777777",
        "app_error_logs",
        domain="ApplicationDocs",
        columns=["id", "request_id", "error_message"],
    )
    nfc = _raw_table(
        "e8888888-8888-4888-8888-888888888888",
        "nfc_scans",
        domain="ApplicationDocs",
        columns=["scan_id", "passport_json"],
    )
    chunks = _raw_table(
        "e9999999-9999-4999-8999-999999999999",
        "chunks",
        domain="KnowledgeSearch",
        columns=["chunk_id", "text"],
    )
    service, _client = _service(om_module, [api_logs, errors, nfc, chunks])

    result = service.run_agent(
        "discovery",
        "Какие таблицы домена ApplicationDocs содержат поле request_id?",
    )

    assert {entity["id"] for entity in result["entities"]} == {api_logs["id"], errors["id"]}
    assert result["total_matches"] == 2


def test_metadata_labels_are_not_misread_as_natural_domain_filters(om_module):
    table = _raw_table(
        "eaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "calendar_connections",
        domain="MeetingsScheduling",
        fqn="docker_postgres_meets.meets.public.calendar_connections",
    )
    service, _client = _service(om_module, [table])

    result = service.run_agent(
        "discovery",
        "Покажи docker_postgres_meets.meets.public.calendar_connections, её владельца, домен и теги",
    )

    assert [entity["id"] for entity in result["entities"]] == [table["id"]]
    assert result["constraints"] == {"fqn": table["fullyQualifiedName"]}
    assert "Владелец:" in result["answer"]
    assert "Домен: MeetingsScheduling" in result["answer"]


def test_governance_role_prepares_preview_payload_without_writing(om_module):
    table = _raw_table(
        "f1111111-1111-4111-8111-111111111111",
        "llm_logs",
        domain="ApplicationDocs",
        columns=["id", "model", "input_json", "output_json", "success", "error"],
        fqn="docker_postgres_docs.docs.public.llm_logs",
    )
    service, client = _service(om_module, [table], write_enabled=True)

    result = service.run_agent(
        "governance",
        "Добавь таблице docker_postgres_docs.docs.public.llm_logs описание: журнал LLM-запросов",
    )

    assert result["agent"] == "governance"
    assert result["intent"] == "governance"
    assert result["governance_request"] == {
        "entity_id": table["id"],
        "changes": {"description": "журнал LLM-запросов"},
        "preview_endpoint": "/api/v1/openmetadata/governance/preview",
    }
    assert result["entities"][0]["id"] == table["id"]
    assert client.patch_calls == []


def test_starter_role_honors_domain_scope_in_the_question(om_module):
    meetings = _raw_table(
        "f2222222-2222-4222-8222-222222222222",
        "meetings",
        domain="MeetingsScheduling",
    )
    finance = _raw_table(
        "f3333333-3333-4333-8333-333333333333",
        "ledger",
        domain="Finance",
    )
    service, _client = _service(om_module, [meetings, finance])

    result = service.run_agent(
        "starter_questions",
        "Сформируй стартовые вопросы для домена MeetingsScheduling",
    )

    assert result["intent"] == "starter_questions"
    assert result["constraints"] == {"domain": "MeetingsScheduling"}
    assert "MeetingsScheduling" in result["answer"]
    assert "Finance" not in result["answer"]
