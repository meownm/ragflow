from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from test.playwright.helpers._next_apps_helpers import RESULT_TIMEOUT_MS

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_TEMPLATE = ROOT / "web" / "src" / "pages" / "business-documents" / "constructor" / "templates" / "sql-query-step-by-step.v1.json"
GOLDEN_SCHEMA_SNAPSHOT = ROOT / "test" / "playwright" / "golden" / "sql-schema-snapshot.v1.json"
NAVIGATION_TIMEOUT_MS = 120_000
ACTOR_ID = "constructor-golden-user"
TENANT_ID = "constructor-golden-tenant"
STORAGE_KEY = f"ragflow.experimental-document-constructor.v2:{TENANT_ID}:{ACTOR_ID}"
SCHEMA_STORAGE_KEY = f"ragflow-sql-schema-workspace.v1:{TENANT_ID}:{ACTOR_ID}"
VISIBLE_SECTIONS = [
    "home",
    "dataset",
    "chat",
    "search",
    "agent",
    "memory",
    "catalog",
    "business_documents",
    "file_manager",
]


def _envelope(data):
    return {"code": 0, "data": data, "message": ""}


def _fulfill_json(route, data):
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(data, ensure_ascii=False),
    )


def _install_session(page):
    user_info = json.dumps(
        {
            "access_token": "constructor-golden-token",
            "id": ACTOR_ID,
            "email": "constructor-golden@example.test",
            "nickname": "Constructor Golden",
            "is_superuser": False,
        }
    )
    page.add_init_script(
        f"""
        (() => {{
          const userInfo = {user_info};
          localStorage.setItem('Authorization', 'Bearer constructor-golden-token');
          localStorage.setItem('token', 'constructor-golden-token');
          localStorage.setItem('userInfo', JSON.stringify(userInfo));
          localStorage.setItem('lng', 'ru');
        }})()
        """
    )


class DocumentConstructorStub:
    def __init__(self):
        self.mutations = []
        self.catalog_queries = []
        self.schema_entity_queries = []
        self.query_plan_requests = []
        self.query_compile_requests = []
        self.execution_binding_requests = []
        self.sql_agent_project = None

    @staticmethod
    def _sql_agent_capabilities():
        return {
            "requirements_agent": True,
            "schema_agent": True,
            "query_agent": True,
            "result_agent": False,
            "python_agent": False,
        }

    def _create_sql_agent_project(self, payload):
        self.sql_agent_project = {
            "schema_version": "1",
            "id": "sql-agent-project-1",
            "title": payload["title"],
            "source_request": payload["source_request"],
            "locale": payload.get("locale", "ru"),
            "stage": "REQUIREMENTS",
            "operation_state": "IDLE",
            "state_version": 1,
            "next_agent": "REQUIREMENTS",
            "current_job": None,
            "pending_proposal": None,
            "artifact_ids": {"requirements": None, "schema": None, "query": None},
            "artifacts": {"requirements": None, "schema": None, "query": None},
            "last_error": None,
            "capabilities": self._sql_agent_capabilities(),
        }
        return self.sql_agent_project

    def _run_sql_agent(self, payload):
        kind = payload["kind"]
        project = self.sql_agent_project
        project["state_version"] += 1
        project["operation_state"] = "REVIEW"
        project["next_agent"] = None
        if kind == "REQUIREMENTS":
            result = {
                "schema_version": "1",
                "status": "NEEDS_CLARIFICATION",
                "proposal": {
                    "requirements": [
                        {
                            "id": "REQ-OUT-001",
                            "kind": "output",
                            "statement": "Вывести идентификатор заказа и оплаченную сумму.",
                            "source_quote": "идентификатор заказа и сумму",
                            "rationale": "Поля явно указаны в запросе.",
                            "status": "PROPOSED",
                        },
                        {
                            "id": "REQ-FLT-001",
                            "kind": "filter",
                            "statement": "Ограничить выборку завершёнными заказами.",
                            "source_quote": "завершённые заказы",
                            "rationale": "Условие определяет состав результата.",
                            "status": "PROPOSED",
                        },
                        {
                            "id": "REQ-LIMIT-001",
                            "kind": "limit",
                            "statement": "Вернуть не более 1000 строк.",
                            "source_quote": "не более 1000 строк",
                            "rationale": "Лимит задан явно.",
                            "status": "PROPOSED",
                        },
                    ],
                    "questions": [
                        {
                            "id": "Q-001",
                            "question": "Как трактовать период?",
                            "reason": "От выбора зависит условие WHERE.",
                            "options": ["Календарный месяц", "Последние 30 дней"],
                            "allow_custom_answer": True,
                            "blocking": True,
                            "status": "OPEN",
                        }
                    ],
                },
                "warning": None,
                "diagnostic": None,
            }
        elif kind == "SCHEMA":
            result = self._schema_resolution_answer()
        else:
            result = {
                "schema_version": "1",
                "status": "PROPOSED",
                "proposal": {
                    "base_entity_id": "orders",
                    "aliases": {"orders": "t1"},
                    "select": [
                        {
                            "id": "select-1",
                            "kind": "column",
                            "column_id": "dwh.order_fact.order_id",
                            "alias": "order_id",
                            "grain": None,
                        },
                        {
                            "id": "select-2",
                            "kind": "sum",
                            "column_id": "dwh.order_fact.paid_amount_rub",
                            "alias": "paid_amount_rub",
                            "grain": None,
                        },
                    ],
                    "joins": [],
                    "filters": [
                        {
                            "id": "filter-1",
                            "column_id": "dwh.order_fact.status_id",
                            "operator": "eq",
                            "parameter_name": "completed_status_id",
                            "parameter_type": "integer",
                            "parameter_value": "9",
                            "description": "Оставить только завершённые заказы.",
                            "confirmed": False,
                        }
                    ],
                    "order_by": [{"select_item_id": "select-2", "direction": "DESC"}],
                    "row_limit": 1000,
                },
                "clarification_questions": [],
                "warning": None,
                "diagnostic": None,
            }
        project["pending_proposal"] = {
            "id": f"proposal-{kind.lower()}",
            "kind": kind,
            "status": "PENDING",
            "source_state_version": project["state_version"],
            "payload": {"agent_result": result},
        }
        return project

    def _decide_sql_agent(self, payload):
        project = self.sql_agent_project
        proposal = project["pending_proposal"]
        kind = proposal["kind"]
        project["state_version"] += 1
        project["operation_state"] = "IDLE"
        project["pending_proposal"] = None
        if payload["decision"] == "REJECT":
            project["next_agent"] = kind
            return project
        if kind == "REQUIREMENTS":
            artifact = deepcopy(proposal["payload"]["agent_result"]["proposal"])
            for requirement in artifact["requirements"]:
                requirement["status"] = "ACCEPTED"
            for question in artifact["questions"]:
                question["answer"] = payload["artifact_payload"]["answers"].get(question["id"])
                question["status"] = "ANSWERED"
            project["artifacts"]["requirements"] = artifact
            project["artifact_ids"]["requirements"] = "artifact-requirements"
            project["stage"] = "SCHEMA"
            project["next_agent"] = "SCHEMA"
        elif kind == "SCHEMA":
            project["artifacts"]["schema"] = payload["artifact_payload"]
            project["artifact_ids"]["schema"] = "artifact-schema"
            project["stage"] = "QUERY"
            project["next_agent"] = "QUERY"
        else:
            project["artifacts"]["query"] = proposal["payload"]["agent_result"]["proposal"]
            project["artifact_ids"]["query"] = "artifact-query"
            project["stage"] = "COMPLETE"
            project["next_agent"] = None
        return project

    @staticmethod
    def _catalog_table(entity_id, fqn, columns):
        technical_name = fqn.rsplit(".", 1)[-1]
        return {
            "id": entity_id,
            "type": "table",
            "name": technical_name,
            "display_name": None,
            "technical_name": technical_name,
            "fqn": fqn,
            "description": f"Описание {fqn}",
            "version": 4,
            "updated_at": "2026-09-14T08:00:00+00:00",
            "service": "warehouse",
            "schema": "dwh",
            "database": "analytics",
            "owners": ["DWH"],
            "domains": ["Sales"],
            "tags": ["gold"],
            "glossary_terms": ["Заказ"],
            "columns": [name for name, _ in columns],
            "column_details": [
                {
                    "name": name,
                    "fqn": f"{fqn}.{name}",
                    "data_type": data_type,
                    "description": f"Поле {name}",
                    "constraint": "PRIMARY_KEY" if name.endswith("_id") else "",
                    "glossary_terms": [],
                }
                for name, data_type in columns
            ],
            "table_constraints": [
                {
                    "constraint_type": "PRIMARY_KEY",
                    "columns": [columns[0][0]],
                    "referred_columns": [],
                }
            ],
            "column_count": len(columns),
            "described_column_count": len(columns),
            "url": f"https://metadata.example/table/{fqn}",
            "matched_by": ["catalog_projection", "ragflow_dataset"],
        }

    def _catalog_answer(self):
        return {
            "agent": "catalog_copilot",
            "intent": "discovery",
            "question": "заказ",
            "answer": "Найдены две таблицы-кандидата.",
            "needs_clarification": True,
            "clarification": "Выберите физическую таблицу.",
            "freshness": {
                "snapshot_at": "2026-09-14T08:00:00+00:00",
                "checked_at": "2026-09-14T08:05:00+00:00",
                "stale": False,
                "threshold_hours": 168,
            },
            "retrieval": "omd_dataset_hybrid_rrf",
            "sources": [
                {"label": "OpenMetadata", "url": "https://metadata.example"},
                {"label": "RAGFlow Dataset", "dataset_id": "dataset-1"},
            ],
            "warnings": [],
            "entities": [
                self._catalog_table(
                    "orders",
                    "dwh.order_fact",
                    [
                        ("order_id", "BIGINT"),
                        ("status_id", "BIGINT"),
                        ("paid_amount_rub", "NUMERIC(18,2)"),
                    ],
                ),
                self._catalog_table(
                    "events",
                    "dwh.order_event_fact",
                    [("event_id", "BIGINT")],
                ),
            ],
        }

    def _schema_resolution_answer(self):
        return {
            "schema_version": "1",
            "status": "NEEDS_CLARIFICATION",
            "resolutions": [
                {
                    "term": "Заказ",
                    "lookup": {
                        "status": "OK",
                        "error_code": None,
                        "retryable": False,
                        "message": None,
                    },
                    "catalog_answer": self._catalog_answer(),
                    "interpretation": {
                        "term": "Заказ",
                        "kind": "entity",
                        "normalized_term": "заказ",
                        "recommended_entity_id": "orders",
                        "recommended_column_ids": [],
                        "confidence": 0.82,
                        "reason": "Таблица содержит факты заказов.",
                        "clarification_question": "Выберите факты заказов или события заказов.",
                    },
                    "needs_clarification": True,
                }
            ],
            "llm": {
                "status": "APPLIED",
                "prompt": {
                    "name": "sql_schema_interpreter",
                    "version": "1",
                    "content_hash": "sha256:test-prompt",
                },
                "warning": None,
            },
        }

    def _schema_entity_details_answer(self):
        entity = self._catalog_table(
            "orders",
            "dwh.order_fact",
            [
                ("order_id", "BIGINT"),
                ("status_id", "BIGINT"),
                ("paid_amount_rub", "NUMERIC(18,2)"),
            ],
        )
        entity["schema_loaded"] = True
        entity["schema_fingerprint"] = "sha256:orders-v4"
        catalog = self._catalog_answer()
        return {
            "schema_version": "1",
            "status": "READY",
            "entities": [
                {
                    "entity_id": "orders",
                    "lookup": {
                        "status": "OK",
                        "error_code": None,
                        "retryable": False,
                        "message": None,
                    },
                    "entity": entity,
                    "freshness": catalog["freshness"],
                    "retrieval": catalog["retrieval"],
                    "sources": catalog["sources"],
                    "warnings": [],
                }
            ],
        }

    @staticmethod
    def _query_plan_answer():
        return {
            "schema_version": "1",
            "status": "PROPOSED",
            "proposal": {
                "base_entity_id": "orders",
                "aliases": {"orders": "t1", "customers": "t2"},
                "select": [
                    {
                        "id": "select-1",
                        "kind": "column",
                        "column_id": "dwh.order_fact.order_id",
                        "alias": "order_id",
                        "grain": None,
                    },
                    {
                        "id": "select-2",
                        "kind": "column",
                        "column_id": "dwh.order_fact.customer_id",
                        "alias": "customer_id",
                        "grain": None,
                    },
                    {
                        "id": "select-3",
                        "kind": "column",
                        "column_id": "dwh.customer_dim.customer_id",
                        "alias": "customer_id_2",
                        "grain": None,
                    },
                    {
                        "id": "select-4",
                        "kind": "column",
                        "column_id": "dwh.customer_dim.name",
                        "alias": "name",
                        "grain": None,
                    },
                ],
                "joins": [
                    {
                        "id": "join-1",
                        "join_type": "INNER",
                        "entity_id": "customers",
                        "alias": "t2",
                        "left_column_id": "dwh.order_fact.customer_id",
                        "right_column_id": "dwh.customer_dim.customer_id",
                        "description": "Соединить dwh.order_fact.customer_id и dwh.customer_dim.customer_id",
                        "confirmed": False,
                    }
                ],
                "filters": [
                    {
                        "id": "filter-1",
                        "column_id": "dwh.order_fact.order_id",
                        "operator": "eq",
                        "parameter_name": "value_1",
                        "parameter_type": "integer",
                        "parameter_value": "100",
                        "description": "ID заказа равен заданному значению",
                        "confirmed": False,
                    }
                ],
                "order_by": [{"select_item_id": "select-1", "direction": "ASC"}],
                "row_limit": 1000,
            },
            "clarification_questions": [],
            "warning": None,
            "diagnostic": None,
            "llm": {
                "status": "APPLIED",
                "prompt": {
                    "name": "sql_query_planner",
                    "version": "1",
                    "content_hash": "sha256:browser-planner",
                },
                "warning": None,
            },
        }

    @staticmethod
    def _query_compile_answer():
        return {
            "schema_version": "1",
            "status": "READY",
            "snapshot_fingerprint": "sha256:browser-golden",
            "blocking_issues": [],
            "sql": "\n".join(
                [
                    "SELECT",
                    "    t1.order_id AS order_id,",
                    "    t1.customer_id AS customer_id,",
                    "    t2.customer_id AS customer_id_2,",
                    "    t2.name AS name",
                    "FROM dwh.order_fact AS t1",
                    "JOIN dwh.customer_dim AS t2",
                    "    ON t1.customer_id = t2.customer_id",
                    "WHERE t1.order_id = :value_1",
                    "ORDER BY order_id ASC",
                    "LIMIT :row_limit",
                ]
            ),
            "parameters": {"value_1": 100, "row_limit": 1000},
            "guard": {
                "status": "PASS",
                "dialect": "postgres",
                "statement_count": 1,
                "read_only": True,
                "tables": ["dwh.customer_dim", "dwh.order_fact"],
                "parameters": ["row_limit", "value_1"],
            },
        }

    @staticmethod
    def _execution_binding_answer():
        return {
            "schema_version": "1",
            "status": "BOUND",
            "reason": None,
            "snapshot_fingerprint": "sha256:browser-golden",
            "catalog_scopes": [
                {
                    "service": "warehouse",
                    "database": "analytics",
                    "schema": "dwh",
                    "table_ids": ["orders", "customers"],
                }
            ],
            "unresolved_catalog_scopes": [],
            "candidates": [],
            "selection": {
                "decision": "automatic_exact",
                "profile": {
                    "id": "profile-1",
                    "name": "Warehouse RO",
                    "dialect": "postgres",
                    "statement_timeout_ms": 30000,
                    "max_rows": 1000,
                    "max_result_bytes": 5000000,
                    "version": 1,
                    "policy_fingerprint": "sha256:browser-policy",
                    "target_database": "analytics",
                },
                "bindings": [{"binding_id": "binding-1", "version": 1}],
                "relations": [
                    {
                        "entity_id": "orders",
                        "catalog_fqn": "warehouse.analytics.dwh.order_fact",
                        "physical_relation": "dwh.order_fact",
                    },
                    {
                        "entity_id": "customers",
                        "catalog_fqn": "warehouse.analytics.dwh.customer_dim",
                        "physical_relation": "dwh.customer_dim",
                    },
                ],
            },
        }

    def __call__(self, route):
        request = route.request
        path = urlparse(request.url).path.rstrip("/")
        if path == "/api/v1/business-documents/sql-query/projects":
            if request.method == "POST":
                payload = json.loads(request.post_data or "{}")
                _fulfill_json(route, _envelope(self._create_sql_agent_project(payload)))
            else:
                items = [self.sql_agent_project] if self.sql_agent_project else []
                _fulfill_json(route, _envelope(items))
            return
        if re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+", path) and request.method == "GET":
            _fulfill_json(route, _envelope(self.sql_agent_project))
            return
        if re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+/agent-jobs", path) and request.method == "POST":
            payload = json.loads(request.post_data or "{}")
            _fulfill_json(route, _envelope(self._run_sql_agent(payload)))
            return
        if re.fullmatch(r"/api/v1/business-documents/sql-query/projects/[^/]+/proposals/[^/]+/decision", path) and request.method == "POST":
            payload = json.loads(request.post_data or "{}")
            _fulfill_json(route, _envelope(self._decide_sql_agent(payload)))
            return
        if path == "/api/v1/business-documents/sql-query/schema/resolve" and request.method == "POST":
            self.catalog_queries.append(json.loads(request.post_data or "{}"))
            _fulfill_json(route, _envelope(self._schema_resolution_answer()))
            return
        if path == "/api/v1/business-documents/sql-query/schema/entities" and request.method == "POST":
            self.schema_entity_queries.append(json.loads(request.post_data or "{}"))
            _fulfill_json(route, _envelope(self._schema_entity_details_answer()))
            return
        if path == "/api/v1/business-documents/sql-query/plan" and request.method == "POST":
            self.query_plan_requests.append(json.loads(request.post_data or "{}"))
            _fulfill_json(route, _envelope(self._query_plan_answer()))
            return
        if path == "/api/v1/business-documents/sql-query/compile" and request.method == "POST":
            self.query_compile_requests.append(json.loads(request.post_data or "{}"))
            _fulfill_json(route, _envelope(self._query_compile_answer()))
            return
        if path == "/api/v1/business-documents/sql-query/execution-binding/resolve" and request.method == "POST":
            self.execution_binding_requests.append(json.loads(request.post_data or "{}"))
            _fulfill_json(route, _envelope(self._execution_binding_answer()))
            return
        if request.method != "GET":
            self.mutations.append((request.method, path, request.post_data))

        if path == "/api/v1/system/config":
            _fulfill_json(
                route,
                _envelope(
                    {
                        "registerEnabled": 0,
                        "disablePasswordLogin": False,
                        "visibleSections": VISIBLE_SECTIONS,
                    }
                ),
            )
            return
        if path == "/api/v1/users/me":
            _fulfill_json(
                route,
                _envelope(
                    {
                        "id": ACTOR_ID,
                        "email": "constructor-golden@example.test",
                        "nickname": "Constructor Golden",
                        "language": "ru",
                        "avatar": None,
                    }
                ),
            )
            return
        if path == "/api/v1/users/me/models":
            _fulfill_json(
                route,
                _envelope(
                    {
                        "tenant_id": TENANT_ID,
                        "llm_id": "",
                        "asr_id": "",
                        "embd_id": "",
                    }
                ),
            )
            return
        if path == "/api/v1/business-documents/capabilities":
            _fulfill_json(
                route,
                _envelope(
                    {
                        "access_role": "AUTHOR_CREATOR",
                        "capabilities": {
                            "read": True,
                            "create": True,
                            "edit_own": True,
                            "edit_all": False,
                            "delete": False,
                            "assign": False,
                        },
                    }
                ),
            )
            return
        if path == "/api/v1/business-documents":
            _fulfill_json(
                route,
                _envelope(
                    {
                        "items": [],
                        "page": 1,
                        "page_size": 20,
                        "total": 0,
                        "scope": "mine",
                        "access_role": "AUTHOR_CREATOR",
                        "capabilities": {
                            "read": True,
                            "create": True,
                            "edit_own": True,
                            "edit_all": False,
                            "delete": False,
                            "assign": False,
                        },
                    }
                ),
            )
            return
        if path == "/api/v1/business-documents/catalog":
            _fulfill_json(route, _envelope({"items": [], "total": 0}))
            return
        if path == "/api/v1/business-documents/eva/changes":
            _fulfill_json(
                route,
                _envelope({"items": [], "page": 1, "page_size": 20, "total": 0}),
            )
            return
        if path in {
            "/api/v1/datasets",
            "/api/v1/tenants",
            "/api/v1/users/me/eva-credentials",
        }:
            _fulfill_json(route, _envelope([]))
            return
        _fulfill_json(route, _envelope({}))


def _query_workspace_candidate(entity_id, fqn, fields):
    technical_name = fqn.rsplit(".", 1)[-1]
    return {
        "id": entity_id,
        "name": technical_name,
        "displayName": None,
        "technicalName": technical_name,
        "fqn": fqn,
        "description": f"Fixture {fqn}",
        "version": 3,
        "updatedAt": "2026-09-14T08:00:00+00:00",
        "service": "warehouse",
        "schema": fqn.split(".", 1)[0],
        "database": "analytics",
        "owners": ["DWH"],
        "domains": ["Sales"],
        "tags": ["gold"],
        "glossaryTerms": [],
        "url": "",
        "matchedBy": ["fixture"],
        "columns": [
            {
                "id": f"{fqn}.{name}",
                "name": name,
                "fqn": f"{fqn}.{name}",
                "dataType": data_type,
                "description": f"Поле {name}",
                "constraint": "",
                "glossaryTerms": [],
            }
            for name, data_type in fields
        ],
        "columnCount": len(fields),
        "columnsTruncated": False,
        "tableConstraints": [],
        "schemaStatus": "loaded",
        "schemaFingerprint": f"sha256:{entity_id}-v3",
        "schemaError": None,
    }


def _ready_query_workspace():
    freshness = {
        "snapshot_at": "2026-09-14T08:00:00+00:00",
        "checked_at": "2026-09-14T08:05:00+00:00",
        "stale": False,
        "threshold_hours": 168,
    }
    sources = [{"label": "OpenMetadata"}]
    orders = _query_workspace_candidate(
        "orders",
        "dwh.order_fact",
        [("order_id", "BIGINT"), ("customer_id", "BIGINT")],
    )
    customers = _query_workspace_candidate(
        "customers",
        "dwh.customer_dim",
        [("customer_id", "BIGINT"), ("name", "TEXT")],
    )

    def resolution(term, candidate):
        return {
            "term": term,
            "status": "confirmed",
            "decision": "user",
            "candidates": [candidate],
            "selectedEntityId": candidate["id"],
            "selectedColumnIds": [column["id"] for column in candidate["columns"]],
            "freshness": freshness,
            "retrieval": "fixture",
            "sources": sources,
            "warnings": [],
            "interpretation": None,
        }

    return {
        "input": "Заказ\nКлиент",
        "requirements": "Вывести заказ и имя клиента для указанного ID заказа.",
        "resolutions": [
            resolution("Заказ", orders),
            resolution("Клиент", customers),
        ],
    }


def _install_ready_query_workspace(page):
    envelope = {
        "format": "ragflow-sql-schema-workspace",
        "schemaVersion": 2,
        "owner": {"userId": ACTOR_ID, "tenantId": TENANT_ID},
        "workspace": _ready_query_workspace(),
    }
    key_json = json.dumps(SCHEMA_STORAGE_KEY)
    envelope_json = json.dumps(envelope, ensure_ascii=False)
    page.add_init_script(f"localStorage.setItem({key_json}, JSON.stringify({envelope_json}));")


def _expected_query_compile_request():
    workspace = _ready_query_workspace()
    tables = [resolution["candidates"][0] for resolution in workspace["resolutions"]]

    def candidate_summary(table):
        return {
            "id": table["id"],
            "fqn": table["fqn"],
            "name": table["name"],
            "display_name": table["displayName"],
            "description": table["description"],
            "version": table["version"],
            "updated_at": table["updatedAt"],
            "url": table["url"],
            "matched_by": table["matchedBy"],
            "column_count": table["columnCount"],
            "loaded_column_count": len(table["columns"]),
            "columns_truncated": table["columnsTruncated"],
            "schema_status": "LOADED",
            "schema_fingerprint": table["schemaFingerprint"],
        }

    def table_snapshot(table):
        return {
            "id": table["id"],
            "fqn": table["fqn"],
            "name": table["name"],
            "display_name": table["displayName"],
            "technical_name": table["technicalName"],
            "description": table["description"],
            "version": table["version"],
            "updated_at": table["updatedAt"],
            "service": table["service"],
            "database": table["database"],
            "schema": table["schema"],
            "owners": table["owners"],
            "domains": table["domains"],
            "tags": table["tags"],
            "glossary_terms": table["glossaryTerms"],
            "url": table["url"],
            "matched_by": table["matchedBy"],
            "column_count": table["columnCount"],
            "loaded_column_count": len(table["columns"]),
            "columns_truncated": False,
            "schema_loaded": True,
            "schema_fingerprint": table["schemaFingerprint"],
            "columns": [
                {
                    "id": column["id"],
                    "name": column["name"],
                    "fqn": column["fqn"],
                    "data_type": column["dataType"],
                    "description": column["description"],
                    "constraint": column["constraint"],
                    "glossary_terms": column["glossaryTerms"],
                    "selected": True,
                }
                for column in table["columns"]
            ],
            "table_constraints": [],
        }

    requirements = []
    for resolution, table in zip(workspace["resolutions"], tables, strict=True):
        requirements.append(
            {
                "term": resolution["term"],
                "state": "CONFIRMED",
                "decision": "USER",
                "interpretation": None,
                "candidates": [candidate_summary(table)],
                "selected_table": table_snapshot(table),
            }
        )
    return {
        "schema_version": "1",
        "schema_snapshot": {
            "format": "ragflow-sql-schema-snapshot",
            "schema_version": "1",
            "status": "READY",
            "original_requirements": workspace["requirements"],
            "source": {
                "type": "openmetadata",
                "catalog_snapshot_at": "2026-09-14T08:00:00+00:00",
                "checked_at": "2026-09-14T08:05:00+00:00",
                "stale": False,
                "retrieval": ["fixture"],
                "references": [{"label": "OpenMetadata"}],
            },
            "requirements": requirements,
            "warnings": [],
        },
        "accepted_requirements": workspace["requirements"],
        "accepted_schema": [
            {
                "entity_id": table["id"],
                "version": table["version"],
                "schema_fingerprint": table["schemaFingerprint"],
            }
            for table in tables
        ],
        "specification": {
            "dialect": "postgres",
            "from": {"entity_id": "orders", "alias": "t1"},
            "select": [
                {
                    "id": "select-1",
                    "kind": "column",
                    "column_id": "dwh.order_fact.order_id",
                    "alias": "order_id",
                    "grain": None,
                },
                {
                    "id": "select-2",
                    "kind": "column",
                    "column_id": "dwh.order_fact.customer_id",
                    "alias": "customer_id",
                    "grain": None,
                },
                {
                    "id": "select-3",
                    "kind": "column",
                    "column_id": "dwh.customer_dim.customer_id",
                    "alias": "customer_id_2",
                    "grain": None,
                },
                {
                    "id": "select-4",
                    "kind": "column",
                    "column_id": "dwh.customer_dim.name",
                    "alias": "name",
                    "grain": None,
                },
            ],
            "joins": [
                {
                    "id": "join-1",
                    "join_type": "INNER",
                    "entity_id": "customers",
                    "alias": "t2",
                    "left_column_id": "dwh.order_fact.customer_id",
                    "right_column_id": "dwh.customer_dim.customer_id",
                    "description": ("Соединить dwh.order_fact.customer_id и dwh.customer_dim.customer_id"),
                    "decision": "user",
                    "confirmed": True,
                }
            ],
            "filters": [
                {
                    "id": "filter-1",
                    "column_id": "dwh.order_fact.order_id",
                    "operator": "eq",
                    "parameter": "value_1",
                    "description": "ID заказа равен заданному значению",
                    "decision": "user",
                    "confirmed": True,
                }
            ],
            "order_by": [{"select_item_id": "select-1", "direction": "ASC"}],
            "parameters": [
                {"name": "value_1", "type": "integer", "value": 100},
                {"name": "row_limit", "type": "integer", "value": 1000},
            ],
            "limit_parameter": "row_limit",
        },
    }


def _expected_query_plan_request():
    compile_request = _expected_query_compile_request()
    return {key: compile_request[key] for key in ("schema_version", "schema_snapshot", "accepted_requirements", "accepted_schema")} | {"locale": "ru"}


def _expected_execution_binding_request():
    compile_request = _expected_query_compile_request()
    return {key: compile_request[key] for key in ("schema_version", "schema_snapshot", "accepted_requirements", "accepted_schema")} | {"selected_profile_id": None}


def _open_constructor(page, base_url):
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.goto(
        f"{base_url.rstrip('/')}/business-documents",
        wait_until="commit",
        timeout=NAVIGATION_TIMEOUT_MS,
    )
    expect(page.get_by_test_id("open-document-constructor")).to_have_count(0)
    constructor_link = page.locator("[data-testid='nav-document-constructor']:visible")
    expect(constructor_link).to_be_visible(timeout=NAVIGATION_TIMEOUT_MS)
    constructor_link.click()
    expect(page).to_have_url(
        re.compile(r"/document-constructor$"),
        timeout=RESULT_TIMEOUT_MS,
    )
    expect(page.locator("[data-testid='nav-document-constructor']:visible")).to_have_attribute("aria-current", "page")
    expect(page.get_by_test_id("document-constructor-page")).to_be_visible()
    page.wait_for_load_state("load", timeout=NAVIGATION_TIMEOUT_MS)
    page.get_by_test_id("document-constructor-template-surface").click()


@pytest.mark.p1
@pytest.mark.auth
def test_document_constructor_build_preview_persist_export_import_golden(
    page,
    base_url,
    tmp_path,
):
    golden = json.loads(GOLDEN_TEMPLATE.read_text(encoding="utf-8"))
    project_name = "Golden: SQL по завершённым заказам"
    section_title = "Контроль результата golden-запроса"
    section_purpose = "Зафиксировать проверяемые признаки итогового набора данных."
    section_requirement = "Сверить число строк, набор колонок и отсутствие дублей"
    section_instruction = "Привести только фактически проверенные результаты."
    expected = deepcopy(golden)
    expected["name"] = project_name
    expected["sections"].append(
        {
            "id": "7",
            "title": section_title,
            "purpose": section_purpose,
            "required": True,
            "allowed_blocks": ["paragraph", "list"],
            "requirements": [section_requirement],
            "generator_instructions": section_instruction,
        }
    )

    stub = DocumentConstructorStub()
    _install_session(page)
    page.route("**/api/v1/**", stub)

    _open_constructor(page, base_url)
    expect(page.get_by_label("Название шаблона")).to_have_value(golden["name"])
    section_rows = page.get_by_test_id("document-constructor-section-row")
    expect(section_rows).to_have_count(len(golden["sections"]))
    python_row = section_rows.filter(has_text="Постобработка результатов на Python")
    expect(python_row).to_have_count(1)
    expect(python_row).to_contain_text("Обязательный")

    page.get_by_label("Название шаблона").fill(project_name)
    page.get_by_role("button", name="Раздел", exact=True).click()
    inspector = page.get_by_test_id("document-constructor-inspector")
    inspector.get_by_label("Название раздела").fill(section_title)
    inspector.get_by_label("Назначение раздела").fill(section_purpose)
    inspector.get_by_role("button", name="Добавить", exact=True).click()
    inspector.get_by_role("textbox", name="Требование 1", exact=True).fill(section_requirement)
    inspector.get_by_label("Инструкция генератору").fill(section_instruction)

    page.wait_for_function(
        "([key, value]) => (localStorage.getItem(key) || '').includes(value)",
        arg=[STORAGE_KEY, section_title],
    )
    page.get_by_role("tab", name="Предпросмотр").click()
    preview = page.get_by_test_id("document-constructor-preview")
    expect(preview).to_contain_text(project_name)
    expect(preview).to_contain_text(re.compile(r"6\s*Постобработка результатов на Python"))
    expect(preview).to_contain_text(section_title)
    expect(preview).to_contain_text(section_requirement)

    with page.expect_download() as download_info:
        page.get_by_test_id("document-constructor-export").click()
    download = download_info.value
    assert download.suggested_filename == "sql_query_plan-1.2.0.json"
    exported_path = tmp_path / download.suggested_filename
    download.save_as(exported_path)
    assert json.loads(exported_path.read_text(encoding="utf-8")) == expected

    page.get_by_role("button", name="Новый", exact=True).click()
    reset_dialog = page.get_by_role("alertdialog")
    expect(reset_dialog).to_contain_text("Создать проект из SQL-шаблона?")
    reset_dialog.get_by_role("button", name="Создать SQL-проект").click()
    expect(page.get_by_label("Название шаблона")).to_have_value(golden["name"])
    expect(section_rows).to_have_count(len(golden["sections"]))

    page.get_by_label("Импортировать JSON проекта конструктора").set_input_files(exported_path)
    expect(page.get_by_role("status")).to_contain_text(f"Шаблон «{project_name}» импортирован.")
    expect(page.get_by_label("Название шаблона")).to_have_value(project_name)
    expect(section_rows).to_have_count(len(expected["sections"]))
    page.wait_for_function(
        "([key, value]) => (localStorage.getItem(key) || '').includes(value)",
        arg=[STORAGE_KEY, section_title],
    )

    page.reload(
        wait_until="commit",
        timeout=NAVIGATION_TIMEOUT_MS,
    )
    page.wait_for_load_state("load", timeout=NAVIGATION_TIMEOUT_MS)
    expect(page.get_by_label("Название шаблона")).to_have_value(
        project_name,
        timeout=NAVIGATION_TIMEOUT_MS,
    )
    expect(section_rows).to_have_count(len(expected["sections"]))
    assert stub.mutations == []
    assert page._diag["page_errors"] == []
    assert page._diag["console_errors"] == []
    # Firefox cancels the duplicate favicon fetch during a document reload.
    actionable_request_failures = [entry for entry in page._diag["request_failed"] if not entry.endswith("/app-icon.png -> NS_BINDING_ABORTED")]
    assert actionable_request_failures == []


@pytest.mark.p1
@pytest.mark.auth
def test_sql_agent_mvp_golden_from_request_to_document(page, base_url, tmp_path):
    stub = DocumentConstructorStub()
    _install_session(page)
    page.route("**/api/v1/**", stub)

    _open_constructor(page, base_url)
    page.get_by_test_id("document-constructor-sql-surface").click()
    workbench = page.get_by_test_id("sql-agent-workbench")
    expect(workbench).to_be_visible()
    expect(workbench).to_contain_text("Соберите SQL-запрос по требованиям")

    workbench.get_by_role("button", name="Новый SQL-проект").last.click()
    workbench.get_by_label("Название проекта").fill("Golden: завершённые заказы")
    workbench.get_by_label("Исходные требования").fill("Вывести идентификатор и сумму завершённых заказов за месяц, не более 1000 строк.")
    workbench.get_by_role("button", name="Создать и продолжить").click()
    expect(workbench).to_contain_text("Разобрать исходные требования")

    workbench.get_by_test_id("sql-agent-run-requirements").click()
    requirements = workbench.get_by_test_id("sql-agent-requirements-review")
    expect(requirements).to_contain_text("Вывести идентификатор заказа")
    requirements.get_by_role("button", name="Календарный месяц").click()
    requirements.get_by_role("button", name="Подтвердить требования").click()

    workbench.get_by_label("Сущности и понятия").fill("Заказ")
    workbench.get_by_test_id("sql-agent-run-schema").click()
    schema = workbench.get_by_test_id("sql-agent-schema-review")
    expect(schema).to_contain_text("Выберите таблицы и нужные поля")
    schema.get_by_role("button", name=re.compile(r"order_fact")).first.click()
    expect(schema).to_contain_text("paid_amount_rub")
    schema.get_by_role("button", name="Выбрать все").click()
    expect(schema).to_contain_text("выбрано 3")
    schema.get_by_role("button", name="Подтвердить схему").click()

    workbench.get_by_test_id("sql-agent-run-query").click()
    query = workbench.get_by_test_id("sql-agent-query-review")
    expect(query).to_contain_text("Оставить только завершённые заказы")
    query.get_by_role("button", name="Подтвердить и собрать SQL").click()

    completed = workbench.get_by_test_id("sql-agent-complete")
    expect(completed).to_contain_text("SQL и спецификация собраны")
    expect(completed).to_contain_text("Read-only · проверка пройдена")
    expect(completed.locator("pre")).to_contain_text("SELECT")
    expect(completed).to_contain_text("Постобработка на Python")

    with page.expect_download() as download_info:
        completed.get_by_role("button", name="Документ .md").click()
    artifact = tmp_path / download_info.value.suggested_filename
    download_info.value.save_as(artifact)
    markdown = artifact.read_text(encoding="utf-8")
    assert "## 1. Исходные требования" in markdown
    assert "## 5. SQL-запрос" in markdown
    assert "## 6. Постобработка результатов на Python" in markdown
    assert stub.sql_agent_project["stage"] == "COMPLETE"
    assert len(stub.query_compile_requests) == 1
    assert page._diag["page_errors"] == []
    assert page._diag["console_errors"] == []


@pytest.mark.p1
@pytest.mark.auth
def test_document_constructor_resolves_schema_ambiguity_and_exports_snapshot(
    page,
    base_url,
    tmp_path,
):
    stub = DocumentConstructorStub()
    _install_session(page)
    page.route("**/api/v1/**", stub)
    _open_constructor(page, base_url)

    page.get_by_test_id("open-schema-workspace").click()
    workspace = page.get_by_test_id("schema-workspace")
    expect(workspace).to_be_visible()
    workspace.get_by_label("Исходные требования к запросу").fill("Вывести оплаченные заказы со статусом и суммой.")
    workspace.get_by_label("Нужные бизнес-сущности").fill("Заказ")
    workspace.get_by_role("button", name="Найти в базе знаний").click()

    status = page.get_by_test_id("schema-snapshot-status")
    expect(status).to_have_text("Нужно уточнение")
    expect(workspace.get_by_text("Интерпретация LLM")).to_be_visible()
    order_table = workspace.get_by_role("radio", name="Выбрать dwh.order_fact")
    event_table = workspace.get_by_role(
        "radio",
        name="Выбрать dwh.order_event_fact",
    )
    expect(order_table).not_to_be_checked()
    expect(event_table).not_to_be_checked()

    order_table.check()
    expect(status).to_have_text("Нужно уточнение")
    expect(workspace.get_by_role("checkbox", name="Поле status_id")).to_be_visible()
    workspace.get_by_role("checkbox", name="Поле status_id").check()
    expect(status).to_have_text("Снимок готов")
    workspace.get_by_role("checkbox", name="Поле paid_amount_rub").check()
    page.wait_for_function(
        """
        ([key, expected]) => {
          try {
            const stored = JSON.parse(localStorage.getItem(key) || 'null');
            const selected = stored?.workspace?.resolutions?.[0]?.selectedColumnIds || [];
            return expected.every((columnId) => selected.includes(columnId));
          } catch {
            return false;
          }
        }
        """,
        arg=[
            SCHEMA_STORAGE_KEY,
            [
                "dwh.order_fact.status_id",
                "dwh.order_fact.paid_amount_rub",
            ],
        ],
    )

    with page.expect_download() as download_info:
        page.get_by_test_id("schema-snapshot-export").click()
    download = download_info.value
    assert download.suggested_filename == "sql-schema-snapshot.v1.json"
    exported_path = tmp_path / download.suggested_filename
    download.save_as(exported_path)
    snapshot = json.loads(exported_path.read_text(encoding="utf-8"))
    golden_snapshot = json.loads(GOLDEN_SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot == golden_snapshot

    page.reload(wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
    page.wait_for_load_state("load", timeout=NAVIGATION_TIMEOUT_MS)
    page.get_by_test_id("open-schema-workspace").click()
    restored = page.get_by_test_id("schema-workspace")
    expect(restored.get_by_role("radio", name="Выбрать dwh.order_fact")).to_be_checked()
    expect(restored.get_by_role("checkbox", name="Поле status_id")).to_be_checked()
    expect(restored.get_by_role("checkbox", name="Поле paid_amount_rub")).to_be_checked()
    expect(restored.get_by_label("Исходные требования к запросу")).to_have_value("Вывести оплаченные заказы со статусом и суммой.")

    assert len(stub.catalog_queries) == 1
    assert stub.catalog_queries[0] == {
        "terms": ["Заказ"],
        "requirements": "Вывести оплаченные заказы со статусом и суммой.",
        "locale": "ru",
    }
    assert stub.schema_entity_queries == [{"entity_ids": ["orders"], "locale": "ru"}]
    assert stub.mutations == []
    assert page._diag["page_errors"] == []
    assert page._diag["console_errors"] == []
    actionable_request_failures = [entry for entry in page._diag["request_failed"] if not entry.endswith("/app-icon.png -> NS_BINDING_ABORTED")]
    assert actionable_request_failures == []


@pytest.mark.p1
@pytest.mark.auth
def test_document_constructor_confirms_query_decisions_and_compiles_golden(
    page,
    base_url,
):
    stub = DocumentConstructorStub()
    _install_session(page)
    _install_ready_query_workspace(page)
    page.route("**/api/v1/**", stub)
    _open_constructor(page, base_url)

    page.get_by_test_id("open-query-specification").click()
    workspace = page.get_by_test_id("query-specification-workspace")
    expect(workspace).to_be_visible()
    compile_button = page.get_by_test_id("query-compile")
    expect(page.get_by_test_id("query-compile-status")).to_have_text("NEEDS_CLARIFICATION")
    expect(compile_button).to_be_disabled()
    assert stub.query_compile_requests == []

    workspace.get_by_test_id("query-execution-binding-resolve").click()
    expect(workspace.get_by_test_id("query-execution-binding-status")).to_have_text("BOUND")
    expect(workspace.get_by_test_id("query-execution-binding-selection")).to_contain_text("Warehouse RO")
    expect(workspace.get_by_test_id("query-execution-relation-mappings")).to_contain_text("warehouse.analytics.dwh.order_fact → dwh.order_fact")
    assert stub.execution_binding_requests == [_expected_execution_binding_request()]

    workspace.get_by_role("checkbox", name="Подтвердить JOIN 1").check()
    expect(compile_button).to_be_enabled()

    workspace.get_by_role("button", name="Условие WHERE").click()
    expect(compile_button).to_be_disabled()
    workspace.get_by_label("Описание WHERE 1").fill("ID заказа равен заданному значению")
    workspace.get_by_label("Тип параметра WHERE 1").select_option("integer")
    workspace.get_by_label("Значение параметра WHERE 1").fill("100")
    workspace.get_by_role("checkbox", name="Подтвердить WHERE 1").check()
    expect(compile_button).to_be_enabled()

    compile_button.click()
    expect(page.get_by_test_id("query-compile-status")).to_have_text("READY")
    sql = page.get_by_test_id("query-sql-output")
    expect(sql).to_contain_text("FROM dwh.order_fact AS t1")
    expect(sql).to_contain_text("JOIN dwh.customer_dim AS t2")
    expect(sql).to_contain_text("WHERE t1.order_id = :value_1")
    expect(workspace.get_by_text('"status": "PASS"')).to_be_visible()

    assert stub.query_compile_requests == [_expected_query_compile_request()]
    assert stub.catalog_queries == []
    assert stub.schema_entity_queries == []
    assert stub.mutations == []
    assert page._diag["page_errors"] == []
    assert page._diag["console_errors"] == []
    actionable_request_failures = [entry for entry in page._diag["request_failed"] if not entry.endswith("/app-icon.png -> NS_BINDING_ABORTED")]
    assert actionable_request_failures == []


@pytest.mark.p1
@pytest.mark.auth
def test_document_constructor_applies_llm_plan_then_requires_human_confirmation(
    page,
    base_url,
):
    stub = DocumentConstructorStub()
    _install_session(page)
    _install_ready_query_workspace(page)
    page.route("**/api/v1/**", stub)
    _open_constructor(page, base_url)

    page.get_by_test_id("open-query-specification").click()
    workspace = page.get_by_test_id("query-specification-workspace")
    workspace.get_by_test_id("query-plan").click()

    expect(workspace.get_by_test_id("query-plan-status")).to_have_text("PROPOSED")
    expect(workspace.get_by_label("Значение параметра WHERE 1")).to_have_value("100")
    compile_button = workspace.get_by_test_id("query-compile")
    expect(compile_button).to_be_disabled()
    expect(workspace.get_by_test_id("query-sql-output")).to_have_count(0)
    assert stub.query_plan_requests == [_expected_query_plan_request()]
    assert stub.query_compile_requests == []

    join_confirmation = workspace.get_by_role("checkbox", name="Подтвердить JOIN 1")
    filter_confirmation = workspace.get_by_role("checkbox", name="Подтвердить WHERE 1")
    expect(join_confirmation).not_to_be_checked()
    expect(filter_confirmation).not_to_be_checked()
    join_confirmation.check()
    filter_confirmation.check()
    expect(compile_button).to_be_enabled()

    compile_button.click()
    expect(workspace.get_by_test_id("query-compile-status")).to_have_text("READY")
    expect(workspace.get_by_test_id("query-sql-output")).to_contain_text("JOIN dwh.customer_dim AS t2")
    expect(workspace.get_by_text('"status": "PASS"')).to_be_visible()

    assert stub.query_compile_requests == [_expected_query_compile_request()]
    assert stub.catalog_queries == []
    assert stub.schema_entity_queries == []
    assert stub.mutations == []
    assert page._diag["page_errors"] == []
    assert page._diag["console_errors"] == []
    actionable_request_failures = [entry for entry in page._diag["request_failed"] if not entry.endswith("/app-icon.png -> NS_BINDING_ABORTED")]
    assert actionable_request_failures == []
