"""Delayed metadata must not replace a newer graph or its presentation state."""

import json

from playwright.sync_api import expect

from test.playwright.e2e.test_sql_workspace_ui import _no_query_mutations, _open


def test_sql_late_deeper_graph_response_does_not_replace_current_neighborhood(page, base_url):
    stub, graph = _open(page, base_url)
    held = []

    def hold_deeper(route):
        if json.loads(route.request.post_data)["depth"] == 2:
            held.append(route)
        else:
            stub(route)

    page.route("**/sql-query/schema/graph", hold_deeper)
    depth = graph.get_by_label("Глубина связанных таблиц")
    with page.expect_request(lambda request: request.url.endswith("/sql-query/schema/graph") and json.loads(request.post_data)["depth"] == 2):
        depth.select_option("2")
    # Pump Playwright until the intercepted request reaches the Python handler.
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    assert len(held) == 1
    with page.expect_response(lambda response: response.url.endswith("/sql-query/schema/graph") and response.request.post_data_json["depth"] == 1) as newer:
        depth.select_option("1")
    newer.value.finished()
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(graph.locator('[data-entity-id="orders"]')).to_have_count(1)
    expect(graph.locator('[data-entity-id="products"]')).to_have_count(0)
    with page.expect_response(lambda response: response.request == held[0].request):
        stub(held[0])
    # The obsolete response has arrived; observe a rendering frame before assertions.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(depth).to_have_value("1")
    expect(graph.locator('[data-entity-id="products"]')).to_have_count(0)
    expect(graph.locator('[data-entity-id="orders"]')).to_have_count(1)
    _no_query_mutations(stub)
