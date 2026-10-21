from test.evals.sql_projects.schema import accepted_schema
from business_documents.sql_query.query_graph import catalog_relationships, snapshot_tables


def test_golden_catalog_carries_declared_fk_pairs_and_pk_instead_of_guessing_from_names():
    tables = snapshot_tables(accepted_schema())
    relationships = catalog_relationships(tables)
    orders = [item for item in relationships if item["from_entity_id"] == "orders"]
    assert {tuple((pair["from"], pair["to"]) for pair in item["column_pairs"]) for item in orders} == {(("customer_id", "id"),), (("payer_id", "id"),)}
    assert any(item["from_entity_id"] == item["to_entity_id"] == "employees" for item in relationships)
    assert all(next(column for column in table["columns"] if column["name"] == "id")["constraint"] == "PRIMARY_KEY" for table in tables)
