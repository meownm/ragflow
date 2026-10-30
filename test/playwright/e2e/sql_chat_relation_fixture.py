"""Full owned golden schema, including buyer/payer and two fan-out children.

Metadata is derived from the actual PostgreSQL catalog, not a fabricated schema.
OpenMetadata FK payload follows the installed1.12.10 Table JSON schema:
https://github.com/open-metadata/OpenMetadata/blob/1.12.10-release/openmetadata-spec/src/main/resources/json/schema/entity/data/table.json
"""

import base64
from contextlib import contextmanager
import os
from decimal import Decimal
import re
import time

import pytest
import requests

from test.evals.sql_projects.discriminators import SETUP_SQL
from test.playwright.e2e import test_workbench_integrations_live as native

DESCRIPTIONS = {
    "regression_orders": "One row per order. customer_id identifies the buyer; payer_id identifies the payer. amount is the order total. Child items and payments can each contain multiple rows for one order.",
    "customers": "One row per customer. region is the buyer or payer region according to the chosen relationship. NULL is missing region; empty string is a separate value.",
    "employees": "One row per employee; manager_id links to another employee.",
    "categories": "One row per category; parent_id links to the parent category.",
    "products": "One row per product; category_id identifies its category.",
    "order_items": "One row per item, identified by id; order_id links to its order. An order can have several items.",
    "payments": "One row per payment, identified by id; order_id can be NULL. An order can have several payments.",
    "shipments": "One row per shipment; an order can have several shipments.",
}


@contextmanager
def metadata_session():
    base = os.environ.get("QA_OPENMETADATA_HOST_URL", "http://127.0.0.1:8585")
    with requests.Session() as metadata:
        token = os.environ.get("QA_OPENMETADATA_JWT_TOKEN", "")
        if not token:
            login = metadata.post(
                base + "/api/v1/users/login",
                json={"email": os.environ["QA_OPENMETADATA_USERNAME"], "password": base64.b64encode(os.environ["QA_OPENMETADATA_PASSWORD"].encode()).decode()},
                timeout=(10, 90),
            )
            assert login.status_code == 200, "Owned fixture metadata login failed"
            token = login.json()["accessToken"]
        metadata.headers["Authorization"] = "Bearer " + token
        yield metadata, base


def _metadata_columns(columns):
    types = {"bigint": "BIGINT", "text": "TEXT", "numeric": "DECIMAL", "timestamp with time zone": "TIMESTAMPZ"}
    output = []
    for column in columns:
        value = {"name": column["name"], "dataType": types[column["type"]]}
        value["constraint"] = "NOT_NULL" if not column["nullable"] else "NULL"
        if column["type"] == "numeric":
            value.update(precision=column["precision"], scale=column["scale"])
        if column["name"] == "customer_id":
            value["description"] = "Buyer customer identifier, distinct from payer_id."
        elif column["name"] == "payer_id":
            value["description"] = "Payer customer identifier, distinct from customer_id (buyer)."
        output.append(value)
    return output


@pytest.fixture(params=[False, True], ids=["golden-a", "golden-b"])
def real_sql_catalog(live, request):
    with contextmanager(native.real_sql_catalog.__wrapped__)(live) as fixture:
        schema = fixture["schema"]
        assert re.fullmatch(r"qa_[0-9a-f]{16}", schema)
        grant = live.sql(
            "SELECT json_build_object('role',min(grantee),'count',count(*)) FROM information_schema.role_table_grants "
            f"WHERE table_schema='{schema}' AND table_name='regression_orders' AND privilege_type='SELECT' AND grantee LIKE 'qa_ro_%';"
        )
        assert grant["count"] == 1 and re.fullmatch(r"qa_ro_[0-9a-f]{16}", grant["role"])
        corpus = native.ROOT / "test/evals/sql_projects/fixtures"
        ddl = (corpus / "schema.sql").read_text(encoding="utf-8")
        data = (corpus / "data.sql").read_text(encoding="utf-8")
        seed = re.sub(r"\borders\b", "regression_orders", ddl + "\n" + data + ("\n" + SETUP_SQL if request.param else ""))
        live.sql(
            f"SET search_path TO {schema},pg_catalog; DROP TABLE {schema}.regression_orders;\n"
            + seed
            + f"\nGRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {grant['role']};\n"
            + "SELECT json_build_object('full_golden_created',true);"
        )
        tables = live.sql(
            "SELECT coalesce(json_agg(json_build_object('name',t.table_name,'columns',"
            "(SELECT json_agg(json_build_object('name',c.column_name,'type',c.data_type,'precision',c.numeric_precision,'scale',c.numeric_scale,'nullable',c.is_nullable='YES') ORDER BY c.ordinal_position) "
            "FROM information_schema.columns c WHERE c.table_schema=t.table_schema AND c.table_name=t.table_name)) ORDER BY t.table_name),'[]'::json) "
            f"FROM information_schema.tables t WHERE t.table_schema='{schema}' AND t.table_type='BASE TABLE';"
        )
        assert {table["name"] for table in tables} == set(DESCRIPTIONS)
        constraints = live.sql(
            "SELECT coalesce(json_agg(json_build_object('table',t.relname,'kind',c.contype,"
            "'columns',(SELECT json_agg(a.attname ORDER BY s.i) FROM generate_subscripts(c.conkey,1) s(i) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[s.i]),"
            "'ref_table',r.relname,'ref_columns',(SELECT json_agg(a.attname ORDER BY s.i) FROM generate_subscripts(c.confkey,1) s(i) JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=c.confkey[s.i]))),'[]'::json) "
            "FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace LEFT JOIN pg_class r ON r.oid=c.confrelid "
            f"WHERE n.nspname='{schema}' AND c.contype IN ('p','f','u');"
        )
        scope = fixture["table"]["databaseSchema"]["fullyQualifiedName"]
        entities = {}
        with metadata_session() as (metadata, base):
            payloads = {}
            for table in tables:
                payload = {"name": table["name"], "databaseSchema": scope, "description": DESCRIPTIONS[table["name"]], "columns": _metadata_columns(table["columns"])}
                saved = metadata.put(base + "/api/v1/tables", json=payload, timeout=(10, 120))
                assert saved.status_code in (200, 201), "Owned golden table registration failed: " + table["name"]
                entities[table["name"]] = saved.json()
                payloads[table["name"]] = payload
            assert entities["regression_orders"]["id"] == fixture["table"]["id"]
            # All targets exist before FK references, including self references.
            for name, payload in payloads.items():
                values = []
                for constraint in constraints:
                    if constraint["table"] != name:
                        continue
                    value = {"constraintType": {"p": "PRIMARY_KEY", "f": "FOREIGN_KEY", "u": "UNIQUE"}[constraint["kind"]], "columns": constraint["columns"]}
                    if constraint["kind"] == "f":
                        assert constraint["ref_table"] in entities
                        value.update(referredColumns=[scope + "." + constraint["ref_table"] + "." + column for column in constraint["ref_columns"]], relationshipType="MANY_TO_ONE")
                    values.append(value)
                saved = metadata.put(base + "/api/v1/tables", json={**payload, "tableConstraints": values}, timeout=(10, 120))
                assert saved.status_code == 200, "Owned golden FK registration failed: " + name + ": " + saved.text[:1000]
                assert saved.json()["id"] == entities[name]["id"]
                entities[name] = saved.json()
            deadline = time.monotonic() + 120
            expected_ids = {entity["id"] for entity in entities.values()}
            while True:
                indexed = metadata.get(base + "/api/v1/search/query", params={"q": scope.split(".")[0], "index": "table_search_index", "size": 50}, timeout=(10, 60))
                assert indexed.status_code == 200, "Owned golden search preflight failed"
                actual_ids = {hit.get("_source", {}).get("id") for hit in indexed.json().get("hits", {}).get("hits", [])}
                if expected_ids.issubset(actual_ids):
                    break
                assert time.monotonic() < deadline, "Owned golden related tables were not indexed"
                time.sleep(2)
        fixture.update(table=entities["regression_orders"], related_tables=entities, calendar_discriminator=request.param)
        oracle = live.sql(
            f"SELECT json_build_object('ids',json_agg(o.id ORDER BY o.id),'total',sum(o.amount)) FROM {schema}.regression_orders o "
            "WHERE o.status='paid' AND o.created_at >= '2026-09-01T00:00:00Z'::timestamptz AND o.created_at < '2026-10-01T00:00:00Z'::timestamptz;"
        )
        expected_ids = [101, 102, 105, 107] + ([108, 109, 110] if request.param else [])
        assert oracle["ids"] == expected_ids and Decimal(str(oracle["total"])) == Decimal(490 if request.param else 240)
        children = live.sql(
            f"SELECT json_build_object('items',(SELECT count(*) FROM {schema}.order_items WHERE order_id=101),'payments',(SELECT count(*) FROM {schema}.payments WHERE order_id=101),'order_amount',(SELECT amount FROM {schema}.regression_orders WHERE id=101));"
        )
        assert children == {"items": 2, "payments": 2, "order_amount": 100}
        fixture["golden_oracle"] = oracle
        yield fixture
