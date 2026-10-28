"""A saved user layout survives graph-neighborhood changes without SQL writes."""

from copy import deepcopy
import re

import pytest
from playwright.sync_api import expect

from test.playwright.e2e.test_sql_workspace_ui import _no_query_mutations, _open, _open_sql_panels


def test_sql_dragged_node_keeps_its_saved_position_when_related_depth_changes(page, base_url):
    stub, graph = _open(page, base_url)
    expect(graph.get_by_role("status").filter(has_text="Вид сохранён")).to_be_visible(timeout=15000)
    initial = deepcopy(stub.view["view"]["positions"]["query:root:o"])
    table = graph.locator('[data-entity-id="orders"]')
    table.scroll_into_view_if_needed()
    heading = table.get_by_text("Заказы", exact=True).bounding_box()
    assert heading is not None
    with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/view") and response.request.post_data_json["view"]["positions"]["query:root:o"] != initial):
        page.mouse.move(heading["x"] + 20, heading["y"] + 6)
        page.mouse.down()
        page.mouse.move(heading["x"] + 100, heading["y"] + 56, steps=12)
        page.mouse.up()
    saved = deepcopy(stub.view["view"]["positions"]["query:root:o"])
    assert saved != initial
    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and response.url.endswith("/view")
        and response.request.post_data_json["view"]["depth"] == 2
        and response.request.post_data_json["view"]["positions"].get("query:root:o") == saved
    ):
        graph.get_by_label("Глубина связанных таблиц").select_option("2")
    expect(graph.locator('[data-entity-id="products"]')).to_have_count(1)
    actual = graph.locator('.react-flow__node[data-id="query:root:o"]').evaluate("node => {const matrix=new DOMMatrix(getComputedStyle(node).transform);return {x:matrix.m41,y:matrix.m42};}")
    assert actual == pytest.approx(saved, abs=0.01)
    page.reload(wait_until="commit")
    expect(page.locator("summary").filter(has_text=re.compile("^Сущности и связи$"))).to_be_visible(timeout=30000)
    _open_sql_panels(page)
    graph = page.get_by_test_id("sql-schema-graph")
    expect(graph.get_by_label("Глубина связанных таблиц")).to_have_value("2", timeout=30000)
    restored = graph.locator('.react-flow__node[data-id="query:root:o"]')
    expect(restored).to_have_count(1)
    actual = restored.evaluate("node => {const matrix=new DOMMatrix(getComputedStyle(node).transform);return {x:matrix.m41,y:matrix.m42};}")
    assert actual == pytest.approx(saved, abs=0.01)
    _no_query_mutations(stub)


def test_sql_metadata_refresh_keeps_dragged_coordinates_while_save_is_pending(page, base_url):
    stub, graph = _open(page, base_url)
    expect(graph.get_by_role("status").filter(has_text="Вид сохранён")).to_be_visible(timeout=15000)
    initial = deepcopy(stub.view["view"]["positions"]["query:root:o"])
    held = []

    def intercept(route):
        if route.request.method == "PUT" and not held:
            held.append(route)
        else:
            stub(route)

    page.route("**/sql-query/projects/*/view", intercept)
    heading = graph.locator('[data-entity-id="orders"]').get_by_text("Заказы", exact=True).bounding_box()
    assert heading is not None
    with page.expect_request(lambda request: request.method == "PUT" and request.url.endswith("/view")):
        page.mouse.move(heading["x"] + 20, heading["y"] + 6)
        page.mouse.down()
        page.mouse.move(heading["x"] + 100, heading["y"] + 56, steps=12)
        page.mouse.up()
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    assert len(held) == 1
    moved = held[0].request.post_data_json["view"]["positions"]["query:root:o"]
    assert moved != initial
    view_reads = sum(method == "GET" and path.endswith("/view") for method, path in stub.requests)
    with page.expect_response(lambda response: response.url.endswith("/sql-query/schema/graph")) as refreshed:
        graph.get_by_role("button", name="Обновить схему", exact=True).click()
    refreshed.value.finished()
    expect(graph.locator('[data-entity-id="orders"]')).to_have_count(1)
    actual = graph.locator('.react-flow__node[data-id="query:root:o"]').evaluate("node => {const m=new DOMMatrix(getComputedStyle(node).transform);return {x:m.m41,y:m.m42};}")
    assert actual == pytest.approx(moved, abs=0.01)
    assert sum(method == "GET" and path.endswith("/view") for method, path in stub.requests) == view_reads
    with page.expect_response(lambda response: response.request == held[0].request):
        stub(held[0])
    expect(graph.get_by_role("status").filter(has_text="Вид сохранён")).to_be_visible(timeout=15000)
    assert stub.view["view"]["positions"]["query:root:o"] == moved
    _no_query_mutations(stub)
