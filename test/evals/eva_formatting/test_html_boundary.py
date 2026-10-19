"""Formatting retained at the HTML boundary cannot enable active content."""

from io import BytesIO

from bs4 import BeautifulSoup
from docx import Document
import pytest

from test.evals.eva_formatting.test_roundtrip import _html_to_markdown, _markdown_to_html, assert_formatting
from api.apps.business_documents.html_formatting import prepare_eva_html
from test.evals.eva_formatting.cases import ALL_CASES


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.id)
def test_native_eva_representation_is_equivalent_and_idempotent(case):
    prepared = prepare_eva_html(case.html)
    assert_formatting(prepared, case.html, stage="Native EVA editor representation")
    assert prepare_eva_html(prepared) == prepared
    assert_formatting(_markdown_to_html(_html_to_markdown(prepared)), case.html, stage="Native EVA editor import")


@pytest.mark.parametrize(
    "before,after",
    [
        ('<ul class="tox-checklist"><li class="tox-checklist--checked">A</li></ul>', '<ul class="tox-checklist"><li>A</li></ul>'),
        ('<ol reversed start="3"><li>A</li><li>B</li></ol>', '<ol start="3"><li>A</li><li>B</li></ol>'),
        ('<p><font size="5" color="red">A</font></p>', '<p><span style="font-size:large;color:red">A</span></p>'),
        ('<h2 id="target">A</h2><a href="#target">B</a>', '<h2 id="other">A</h2><a href="#target">B</a>'),
    ],
)
def test_editor_aliases_do_not_hide_formatting_loss(before, after):
    from test.evals.eva_formatting.test_roundtrip import semantic_html

    assert semantic_html(before) != semantic_html(after)


def test_numbered_heading_retains_anchor_and_governed_outline():
    from api.apps.business_documents.assets import import_document_markdown
    from api.apps.business_documents.exports import BusinessDocumentExportService
    from test.unit_test.api.apps.business_documents.test_business_document_service import _draft

    original = BusinessDocumentExportService._render_eva_wiki(_draft(), 1)
    soup = BeautifulSoup(original, "html.parser")
    heading = soup.find("h2")
    heading["id"] = "section"
    link = soup.new_tag("a", href="#section")
    link.string = "Jump"
    heading.insert_after(link)
    markdown = _html_to_markdown(str(soup))
    assert markdown.startswith("## 1. ")
    imported = import_document_markdown(markdown)
    exported = BeautifulSoup(BusinessDocumentExportService._render_eva_wiki(imported, 2), "html.parser")
    assert exported.find("h2", id="section") is not None
    assert exported.find("a", href="#section").text == "Jump"


@pytest.mark.parametrize(
    "source",
    [
        '<div style="text-align:right"><p>Body **literal**</p></div>',
        '<h2 style="color:red"><u>Title</u></h2>',
        '<pre style="color:red"><code class="language-python">one  \n</code></pre>',
        '<p title="Tooltip">Text</p>',
        '<table><tr><td><p style="color:red">Text</p></td></tr></table>',
    ],
)
def test_safe_block_formatting_and_literal_markers_survive(source):
    for _ in range(2):
        result = _markdown_to_html(_html_to_markdown(source))
        assert_formatting(result, source, stage="Safe HTML block")
        source = result


@pytest.mark.parametrize(
    "style",
    [
        "color:red;background-image:url(https://evil.test/x)",
        "color:red;background-color:URL(javascript:alert(1))",
        "color:red;font-family:expression(alert(1))",
        r"color:red;background-color:u\72l(https://evil.test/x)",
        "color:red;position:fixed;inset:0",
        "color:red;--remote:url(https://evil.test/x);background:var(--remote)",
    ],
)
def test_css_keeps_formatting_and_rejects_resources_and_overlays(style):
    source = f'<p style="{style}" onclick="evil()">Safe</p>'
    for renderer in (lambda value: _markdown_to_html(_html_to_markdown(value)), _markdown_to_html):
        soup = BeautifulSoup(renderer(source), "html.parser")
        assert soup.p["style"] == "color:red"
        assert not soup.select("[onclick]")
        assert soup.p.text == "Safe"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "java&#x09;script:alert(1)", "data:text/html,evil", "vbscript:evil"])
def test_raw_html_rejects_unsafe_resources_on_import_and_render(url):
    source = f'<p><a href="{url}" id="clobber">Label</a><img src="{url}" alt="Alt" onerror="evil()"></p><svg><script>evil()</script></svg><iframe>evil</iframe><form><input type="text"></form>'
    for renderer in (lambda value: _markdown_to_html(_html_to_markdown(value)), _markdown_to_html):
        soup = BeautifulSoup(renderer(source), "html.parser")
        assert not soup.select("[href], [src], [onerror], script, svg, iframe, form, input")
        assert "Label" in soup.text and "Alt" in soup.text


def test_docx_consumer_does_not_print_retained_html_markup():
    from api.apps.business_documents.exports import BusinessDocumentExportService

    ast = {"sections": [{"id": "1", "title": "Scope", "blocks": [{"type": "paragraph", "text": '<p><u>First</u><br><span style="color:red">Second</span></p>'}]}]}
    doc = Document(BytesIO(BusinessDocumentExportService._render_docx(ast)))
    assert doc.paragraphs[1].text == "First\nSecond"


@pytest.mark.parametrize("span", ["²", "9" * 5000, "-1", "invalid"])
def test_eva_export_handles_invalid_and_excessive_spans(span):
    from api.apps.business_documents.exports import BusinessDocumentExportService

    ast = {"sections": [{"id": "1", "title": "Scope", "blocks": [{"type": "paragraph", "text": f'<table><tr><td colspan="{span}" rowspan="{span}">A</td></tr></table>'}]}]}
    soup = BeautifulSoup(BusinessDocumentExportService._render_eva_wiki(ast, 1), "html.parser")
    assert soup.td["colspan"] == soup.td["rowspan"] == "1"


def test_eva_export_coordinates_respect_row_and_column_spans():
    from api.apps.business_documents.exports import BusinessDocumentExportService

    source = '<table><tr><td rowspan="2">A</td><td colspan="2">B</td></tr><tr><td>C</td><td>D</td></tr></table>'
    ast = {"sections": [{"id": "1", "title": "Scope", "blocks": [{"type": "paragraph", "text": source}]}]}
    soup = BeautifulSoup(BusinessDocumentExportService._render_eva_wiki(ast, 1), "html.parser")
    assert [(cell.text, cell["data-x"], cell["data-y"], cell["colspan"], cell["rowspan"]) for cell in soup.select("td")] == [
        ("A", "0", "0", "1", "2"),
        ("B", "1", "0", "2", "1"),
        ("C", "1", "1", "1", "1"),
        ("D", "2", "1", "1", "1"),
    ]
