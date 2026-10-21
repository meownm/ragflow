"""Reload during a pending personal-view save cancels transport without SQL writes."""

from playwright.sync_api import expect

from test.playwright.e2e.test_sql_workspace_ui import _no_query_mutations, _open


def test_sql_reload_cancels_inflight_view_save_without_unhandled_browser_error(page, base_url):
    stub, graph = _open(page, base_url)
    expect(graph.get_by_role("status").filter(has_text="Вид сохранён")).to_be_visible(timeout=15000)
    held = []

    def intercept(route):
        if route.request.method == "PUT" and not held:
            held.append(route)
        else:
            stub(route)

    page.route("**/sql-query/projects/*/view", intercept)
    with page.expect_request(lambda request: request.method == "PUT" and request.url.endswith("/view")) as saving:
        graph.get_by_label("Только запрос", exact=True).check()
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    assert len(held) == 1
    with page.expect_event("requestfailed", predicate=lambda request: request == saving.value) as cancelled:
        # Observe application cancellation before navigation can cancel it itself.
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    assert cancelled.value.failure
    page.reload(wait_until="commit")
    graph = page.get_by_test_id("sql-schema-graph")
    expect(graph.locator('[data-entity-id="orders"]')).to_have_count(1, timeout=30000)
    expect(graph.get_by_label("Только запрос", exact=True)).not_to_be_checked()
    _no_query_mutations(stub)
    assert page._diag["page_errors"] == []
