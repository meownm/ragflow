"""Actual SQL membership is independent from visible catalog context."""

from copy import deepcopy

import pytest

from business_documents.sql_query.query_graph import QueryGraphError, catalog_relationships, project_query_graph, related_catalog, relationship_paths, snapshot_tables


@pytest.fixture
def tables():
    def table(name, columns, constraints=()):
        return {"id": name, "fqn": f"warehouse.analytics.public.{name}", "schema": "public", "technical_name": name,
                "columns": [{"id": f"warehouse.analytics.public.{name}.{column}", "name": column, "data_type": "BIGINT"} for column in columns],
                "table_constraints": list(constraints)}
    return [
        table("orders", ["id", "customer_id", "payer_id", "amount"], [
            {"constraint_type": "FOREIGN_KEY", "columns": [key], "referred_columns": ["warehouse.analytics.public.customers.id"]}
            for key in ("customer_id", "payer_id")
        ]),
        table("customers", ["id", "manager_id", "region"]),
        table("items", ["id", "order_id"], [{"constraint_type": "FOREIGN_KEY", "columns": ["order_id"], "referred_columns": ["warehouse.analytics.public.orders.id"], "relationship_type": "MANY_TO_ONE"}]),
        table("unrelated", ["id"]),
    ]


def test_schema_selection_and_graph_browsing_do_not_mean_query_participation(tables):
    before = deepcopy(tables)
    graph = project_query_graph("SELECT o.id FROM public.orders o LIMIT :row_limit", tables)
    assert [(node["entity_id"], node["membership"]) for node in graph["nodes"]] == [("orders", "QUERY"), ("customers", "RELATED"), ("items", "RELATED"), ("unrelated", "RELATED")]
    assert tables == before
    assert all(edge["kind"] == "FOREIGN_KEY" for edge in graph["edges"])
    assert next(node for node in graph["nodes"] if node["entity_id"] == "orders")["columns"][0]["roles"] == ["SELECT"]


def test_self_join_roles_and_alternative_catalog_keys_remain_distinct(tables):
    graph = project_query_graph("SELECT buyer.region, payer.region FROM public.orders o LEFT JOIN public.customers buyer ON o.customer_id=buyer.id JOIN public.customers payer ON o.payer_id=payer.id WHERE o.amount>0", tables)
    customers = [node for node in graph["nodes"] if node["entity_id"] == "customers"]
    assert {node["alias"] for node in customers} == {"buyer", "payer"}
    assert len({node["id"] for node in customers}) == 2
    joins = [edge for edge in graph["edges"] if edge["kind"] == "JOIN"]
    assert [(edge["join_type"], edge["condition"]) for edge in joins] == [("LEFT", "o.customer_id = buyer.id"), ("INNER", "o.payer_id = payer.id")]
    foreign_keys = [edge for edge in graph["edges"] if edge["kind"] == "FOREIGN_KEY"]
    assert len(foreign_keys) == 5
    assert all(edge["cardinality"] == "UNKNOWN" for edge in foreign_keys if edge["from_entity_id"] == "orders")


def test_cte_shadowing_and_nested_scopes_do_not_invent_physical_tables(tables):
    graph = project_query_graph("WITH orders AS (SELECT c.id FROM public.customers c) SELECT x.id FROM orders x JOIN public.orders physical ON physical.customer_id=x.id", tables)
    assert {node["alias"] for node in graph["nodes"] if node["entity_id"] == "orders" and node["membership"] == "QUERY"} == {"physical"}
    assert any(node["entity_id"] is None and node["alias"] == "x" for node in graph["nodes"])
    assert any(edge["kind"] == "LINEAGE" for edge in graph["edges"])
    assert next(node for node in graph["nodes"] if node["alias"] == "x")["kind"] == "CTE"


def test_correlated_reference_marks_outer_filter_column(tables):
    graph = project_query_graph("SELECT o.id FROM public.orders o WHERE EXISTS(SELECT i.id FROM public.items i WHERE i.order_id=o.id)", tables)
    order = next(node for node in graph["nodes"] if node["alias"] == "o")
    assert "WHERE" in next(column for column in order["columns"] if column["name"] == "id")["roles"]


@pytest.mark.parametrize("sql", ["SELECT * FROM absent", "SELECT 1; SELECT 2", "DELETE FROM public.orders", "SELECT ( FROM public.orders"])
def test_unknown_or_invalid_source_never_becomes_a_successful_graph(tables, sql):
    with pytest.raises(QueryGraphError):
        project_query_graph(sql, tables)


def test_composite_fk_keeps_order_and_rejects_incomplete_evidence(tables):
    tables[2]["columns"].append({"id": "warehouse.analytics.public.items.tenant_id", "name": "tenant_id"})
    tables[0]["columns"].append({"id": "warehouse.analytics.public.orders.tenant_id", "name": "tenant_id"})
    constraint = {"constraint_type": "FOREIGN_KEY", "columns": ["tenant_id", "order_id"], "referred_columns": ["warehouse.analytics.public.orders.tenant_id", "warehouse.analytics.public.orders.id"]}
    tables[2]["table_constraints"] = [constraint]
    edge = next(edge for edge in catalog_relationships(tables) if edge["from_entity_id"] == "items")
    assert edge["column_pairs"] == [{"from": "tenant_id", "to": "tenant_id"}, {"from": "order_id", "to": "id"}]
    constraint["referred_columns"].pop()
    assert not any(edge["from_entity_id"] == "items" for edge in catalog_relationships(tables))


def test_catalog_shape_with_column_names_and_details(tables):
    for table in tables:
        table["column_details"] = table["columns"]
        table["columns"] = [column["name"] for column in table["column_details"]]
    assert len(catalog_relationships(tables)) == 3
    assert project_query_graph("SELECT o.id FROM public.orders o", tables)["nodes"][0]["columns"][0]["name"] == "id"


def test_related_catalog_incoming_outgoing_limits_and_invisible_tables(tables):
    graph = related_catalog(tables, ["orders"])
    assert {table["id"] for table in graph["tables"]} == {"orders", "customers", "items"}
    assert len(graph["relationships"]) == 3
    assert related_catalog(tables, ["orders"], max_nodes=1)["truncated"]
    visible = [table for table in tables if table["id"] != "customers"]
    graph = related_catalog(visible, ["orders"])
    assert {table["id"] for table in graph["tables"]} == {"orders", "items"}
    with pytest.raises(QueryGraphError):
        related_catalog(visible, ["customers"])


@pytest.mark.parametrize("depth", [True, 0, 4, "1", None])
def test_relationship_depth_has_a_closed_bounded_contract(tables, depth):
    with pytest.raises(QueryGraphError):
        related_catalog(tables, ["orders"], depth=depth)


def test_snapshot_uses_only_accepted_entities(tables):
    schema = {"accepted_schema": [{"entity_id": "orders"}], "schema_snapshot": {"requirements": [{"selected_table": table} for table in tables]}}
    assert snapshot_tables(schema) == [tables[0]]


def test_postgres_unquoted_identifiers_fold_but_quoted_identifiers_stay_exact(tables):
    graph = project_query_graph("SELECT T.ID FROM PUBLIC.ORDERS T", tables)
    assert graph["nodes"][0]["alias"] == "t"
    assert graph["nodes"][0]["columns"][0]["roles"] == ["SELECT"]
    with pytest.raises(QueryGraphError):
        project_query_graph('SELECT T.id FROM public."ORDERS" T', tables)


def test_paths_keep_alternative_fk_roles_and_bridge_tables(tables):
    relationships = catalog_relationships(tables)
    result = relationship_paths(relationships, ["items"])
    routes = result["paths"]["customers"]
    assert len(routes) == 2
    assert all(route["entity_ids"] == ["items", "orders", "customers"] for route in routes)
    assert routes[0]["relationship_ids"] != routes[1]["relationship_ids"]
    assert "unrelated" not in result["paths"]
    assert "customers" not in relationship_paths(relationships, ["items"], max_hops=1)["paths"]


def test_paths_are_simple_even_with_cycles():
    relationships = [{"id": f"fk{index}", "from_entity_id": left, "to_entity_id": right} for index, (left, right) in enumerate((("a", "b"), ("b", "c"), ("c", "a")))]
    result = relationship_paths(relationships, ["a"])
    assert all(len(route["entity_ids"]) == len(set(route["entity_ids"])) for routes in result["paths"].values() for route in routes)
    assert len(result["paths"]["c"]) == 2


def test_root_node_identity_survives_addition_of_a_cte(tables):
    before = project_query_graph("SELECT o.id FROM public.orders o", tables)
    after = project_query_graph("WITH c AS(SELECT c.id FROM public.customers c) SELECT o.id FROM public.orders o WHERE EXISTS(SELECT c.id FROM c WHERE c.id=o.customer_id)", tables)
    assert next(node["id"] for node in before["nodes"] if node["alias"] == "o") == next(node["id"] for node in after["nodes"] if node["alias"] == "o")


def test_window_order_is_not_reported_as_result_order(tables):
    graph = project_query_graph("SELECT SUM(o.amount) OVER(PARTITION BY o.customer_id ORDER BY o.id) AS running FROM public.orders o", tables)
    columns = {column["name"]: column for column in graph["nodes"][0]["columns"]}
    assert columns["id"]["roles"] == ["WINDOW ORDER"]
    assert columns["customer_id"]["roles"] == ["PARTITION BY"]
def test_comma_join_is_cross_and_does_not_claim_an_inner_predicate(tables):
    graph = project_query_graph("SELECT o.id FROM public.orders o, public.customers c", tables)
    join = next(edge for edge in graph["edges"] if edge["kind"] == "JOIN")
    assert join["join_type"] == "CROSS"
    assert not join["condition"]
    assert graph["warnings"]


def test_using_columns_have_join_role_on_each_instance(tables):
    graph = project_query_graph("SELECT o.id FROM public.orders o JOIN public.customers c USING(id)", tables)
    for node in graph["nodes"]:
        if node["membership"] == "QUERY":
            assert "JOIN" in next(column for column in node["columns"] if column["name"] == "id")["roles"]


def test_query_alias_expansion_has_a_hard_bound_instead_of_rendering_thousands(tables):
    sql = "SELECT o0.id FROM public.orders o0 " + " ".join(f"CROSS JOIN public.orders o{index}" for index in range(1, 101))
    with pytest.raises(QueryGraphError, match="100 SQL source instances"):
        project_query_graph(sql, tables)


def test_catalog_self_fk_cross_product_is_bounded_before_reaching_the_browser(tables):
    tables[1]["table_constraints"] = [{"constraint_type": "FOREIGN_KEY", "columns": ["manager_id"], "referred_columns": ["warehouse.analytics.public.customers.id"]}]
    sql = "SELECT c0.id FROM public.customers c0 " + " ".join(f"CROSS JOIN public.customers c{index}" for index in range(1, 30))
    with pytest.raises(QueryGraphError, match="500 relationships"):
        project_query_graph(sql, tables)


def test_duplicate_alias_is_a_domain_error_not_an_unhandled_optimizer_exception(tables):
    with pytest.raises(QueryGraphError, match="unambiguous"):
        project_query_graph("SELECT o.id FROM public.orders o JOIN public.orders o ON o.id=o.id", tables)


def test_unused_related_nodes_expose_declared_fk_columns_without_implying_query_use(tables):
    graph = project_query_graph("SELECT o.id FROM public.orders o", tables)
    items = next(node for node in graph["nodes"] if node["entity_id"] == "items")
    order_key = next(column for column in items["columns"] if column["name"] == "order_id")
    assert items["membership"] == "RELATED"
    assert order_key["constraint"] == "FOREIGN_KEY"
    assert order_key["roles"] == []


def test_sql_instances_take_priority_over_catalog_neighbors_within_view_storage_bound(tables):
    sql = "SELECT o0.id FROM public.orders o0 " + " ".join(f"CROSS JOIN public.orders o{index}" for index in range(1, 98))
    graph = project_query_graph(sql, tables)
    assert len(graph["nodes"]) == 100
    assert sum(node["membership"] == "QUERY" for node in graph["nodes"]) == 98
    assert graph["truncated"] is True
    assert any("100 узлов" in warning for warning in graph["warnings"])
