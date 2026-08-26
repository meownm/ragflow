from common.data_source import html_utils
from common.data_source.html_utils import parse_html_page_basic


def test_link_scope_ends_with_anchor(monkeypatch):
    monkeypatch.setattr(html_utils, "HTML_BASED_CONNECTOR_TRANSFORM_LINKS_STRATEGY", "markdown")
    result = parse_html_page_basic('<p>Before <a href="/target">linked text</a> after link.</p><p>Final paragraph.</p>')

    assert result == "Before [linked text](/target) after link.\nFinal paragraph."


def test_table_scope_ends_with_table():
    result = parse_html_page_basic("<table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>1</td></tr></table><p>After table.</p>")

    assert result == "Name\tValue\nA\t1\nAfter table."


def test_images_keep_searchable_context_without_copying_data_uri():
    result = parse_html_page_basic('<p><img alt="Architecture" src="https://eva.example/diagram.png"></p><img src="data:image/png;base64,AAAA">')

    assert "[Image: Architecture](https://eva.example/diagram.png)" in result
    assert "[Image: image]" in result
    assert "base64" not in result


def test_ignored_elements_do_not_leak_into_text():
    result = parse_html_page_basic("<style>.secret {}</style><script>alert('x')</script><p>Visible</p>")

    assert result == "Visible"
