from __future__ import annotations

from copy import deepcopy

import pytest

from common.data_source.exceptions import ConnectorMissingCredentialError, ConnectorValidationError
from common.data_source.openmetadata_connector import OpenMetadataConnector


def _table(entity_id="11111111-1111-4111-8111-111111111111", *, description="Orders", service="warehouse", domain="Sales"):
    return {
        "id": entity_id,
        "name": "orders",
        "fullyQualifiedName": f"{service}.analytics.orders",
        "description": description,
        "updatedAt": 1_750_000_000_000,
        "service": {"name": service},
        "database": {"name": "analytics"},
        "databaseSchema": {"name": "public"},
        "domains": [{"name": domain}],
        "owners": [{"name": "data-team"}],
        "tags": [{"tagFQN": "PII.Sensitive"}],
        "columns": [
            {"name": "order_id", "dataType": "BIGINT", "description": "Primary key"},
            {"name": "amount", "dataTypeDisplay": "DECIMAL(12,2)", "description": "Order amount"},
        ],
    }


class _Client:
    def __init__(self, tables):
        self.tables = {table["id"]: deepcopy(table) for table in tables}
        self.lineage_payload = {"nodes": [], "upstreamEdges": [], "downstreamEdges": []}

    def get(self, path, params=None):
        del params
        if path == "/api/v1/system/version":
            return {"version": "1.12.10"}
        return {"hits": {"hits": []}}

    def list_tables(self, max_entities):
        return [deepcopy(table) for table in self.tables.values()][:max_entities]

    def get_table(self, entity_id):
        return deepcopy(self.tables[entity_id])

    def get_lineage(self, entity_id):
        del entity_id
        return deepcopy(self.lineage_payload)


def _connector(tables=None, **kwargs):
    connector = OpenMetadataConnector(
        "http://omd.test:8585",
        "https://catalog.example.test",
        batch_size=2,
        **kwargs,
    )
    connector.load_credentials(
        {
            "openmetadata_username": "reader@example.test",
            "openmetadata_password": "secret",
        }
    )
    client = _Client([_table()] if tables is None else tables)
    connector._test_client = client
    connector._request = client.get
    connector._list_tables = lambda: client.list_tables(connector.max_entities)
    connector._get_table = client.get_table
    connector._get_lineage = client.get_lineage
    return connector


def test_table_becomes_stable_markdown_document_with_metadata_and_fingerprint():
    connector = _connector()

    records = list(connector.list_keys())
    document = connector.get_value(records[0].key)

    assert records[0].key == "11111111-1111-4111-8111-111111111111"
    assert len(records[0].fingerprint) == 32
    assert records[0].fingerprint == document.fingerprint
    assert document.id == records[0].key
    assert document.extension == ".md"
    assert document.metadata["omd_fqn"] == "warehouse.analytics.orders"
    assert document.metadata["omd_updated_at_epoch"] == _table()["updatedAt"]
    assert document.metadata["omd_domains"] == ["Sales"]
    text = document.blob.decode("utf-8")
    assert "# warehouse.analytics.orders" in text
    assert "**amount**: DECIMAL(12,2); Order amount" in text
    assert "https://catalog.example.test/table/warehouse.analytics.orders" in text


def test_document_indexes_openmetadata_relationships():
    table = _table()
    table["processedLineage"] = True
    table["tags"].append({"tagFQN": "Business.Order", "source": "Glossary"})
    table["tableConstraints"] = [
        {
            "constraintType": "FOREIGN_KEY",
            "columns": ["order_id"],
            "referredColumns": ["warehouse.analytics.customers.customer_id"],
            "relationshipType": "MANY_TO_ONE",
        }
    ]
    connector = _connector([table])
    connector._test_client.lineage_payload = {
        "nodes": [
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "name": "raw_orders",
                "fullyQualifiedName": "warehouse.raw.raw_orders",
            }
        ],
        "upstreamEdges": [
            {
                "fromEntity": "22222222-2222-4222-8222-222222222222",
                "toEntity": table["id"],
                "lineageDetails": {
                    "columnsLineage": [
                        {
                            "fromColumns": ["warehouse.raw.raw_orders.id"],
                            "toColumn": "warehouse.analytics.orders.order_id",
                        }
                    ]
                },
            }
        ],
        "downstreamEdges": [],
    }

    record = list(connector.list_keys())[0]
    document = connector.get_value(record.key)
    text = document.blob.decode("utf-8")

    assert "- Glossary concepts: Business.Order" in text
    assert "- Foreign key: order_id -> warehouse.analytics.customers.customer_id (MANY_TO_ONE)" in text
    assert "warehouse.raw.raw_orders -> warehouse.analytics.orders" in text
    assert "warehouse.raw.raw_orders.id -> warehouse.analytics.orders.order_id" in text
    assert document.metadata["omd_glossary_terms"] == ["Business.Order"]
    assert document.metadata["omd_foreign_keys"] == ["warehouse.analytics.customers.customer_id"]
    assert document.metadata["omd_has_lineage"] is True


def test_fingerprint_changes_only_when_entity_projection_changes():
    original = _table()
    connector = _connector([original])
    first = list(connector.list_keys())[0].fingerprint

    connector._test_client.tables[original["id"]]["description"] = "Updated business meaning"
    second = list(connector.list_keys())[0].fingerprint

    assert first != second
    assert second == connector.get_value(original["id"]).fingerprint


def test_scope_filters_are_exact_case_insensitive_and_prune_snapshot_is_complete():
    included = _table()
    excluded = _table("22222222-2222-4222-8222-222222222222", service="other", domain="Finance")
    connector = _connector([included, excluded], services=["WAREHOUSE"], domains=["sales"])

    assert [record.key for record in connector.list_keys()] == [included["id"]]
    slim = [doc for batch in connector.retrieve_all_slim_docs_perm_sync() for doc in batch]
    assert [doc.id for doc in slim] == [included["id"]]


def test_full_load_batches_and_can_exclude_columns():
    tables = [
        _table("11111111-1111-4111-8111-111111111111"),
        _table("22222222-2222-4222-8222-222222222222"),
        _table("33333333-3333-4333-8333-333333333333"),
    ]
    connector = _connector(tables, include_columns=False)

    batches = list(connector.load_from_state())

    assert [len(batch) for batch in batches] == [2, 1]
    assert "## Columns" not in batches[0][0].blob.decode("utf-8")


def test_credentials_and_bounds_fail_closed():
    connector = OpenMetadataConnector("http://omd.test:8585")
    with pytest.raises(ConnectorMissingCredentialError):
        connector._validate_config()
    with pytest.raises(ConnectorValidationError, match="batch_size"):
        OpenMetadataConnector("http://omd.test:8585", batch_size=0)
    with pytest.raises(ConnectorValidationError, match="poll start"):
        list(_connector().poll_source(10, 1))


def test_deleted_and_malformed_entities_are_not_indexed():
    deleted = _table()
    deleted["deleted"] = True
    malformed = {"id": "", "name": "missing-id"}
    connector = _connector([deleted, malformed])

    assert list(connector.list_keys()) == []
