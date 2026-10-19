"""Lossless formatting acceptance. Failures are losses, never expected failures.

Run separately from unit lanes: see README.md. No live I/O is used here.
"""

import json
import re
from pathlib import Path
import sys
from types import ModuleType
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString, Tag
import pytest
import requests

# Avoid API route discovery/bootstrap, like the existing EVA service unit suite.
if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[3] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.eva_changes import _html_to_markdown, _markdown_to_html
from common.data_source.eva_wiki_connector import EvaWikiConnector, EvaWikiMutationClient
from test.evals.eva_formatting.cases import ALL_CASES, MARKDOWN_CASES


def semantic_html(source):
    """Compare content, order and formatting; ignore serializer-only wrappers.

    Code whitespace and language are significant. A table's empty-cell NBSP
    and a single paragraph inside a cell/list item are editor serialization.
    CSS, checkbox state, link/image attributes and numbering are significant.
    """
    soup = BeautifulSoup(source, "html.parser")
    # The EVA editor draws checkboxes from list classes rather than inputs.
    for listing in soup.select("ul.tox-checklist, ol.tox-checklist"):
        for item in listing.find_all("li", recursive=False):
            if "tox-checklist--hidden" not in item.get("class", []):
                box = soup.new_tag("input", attrs={"type": "checkbox", "disabled": ""})
                if "tox-checklist--checked" in item.get("class", []):
                    box["checked"] = ""
                paragraph = item.find("p", recursive=False)
                (paragraph if paragraph is not None else item).insert(0, box)
    for anchor in soup.select('a[cmf_converted][href^="#mcetoc_"]'):
        target = soup.find(id=unquote(anchor["href"][1:]))
        if target is not None:
            target["id"] = unquote(anchor["href"][8:])
        anchor["href"] = "#" + anchor["href"][8:]
    # Compare rendered counter sequences, including resets on individual items.
    for listing in soup.select("ol"):
        items = listing.find_all("li", recursive=False)
        step = -1 if listing.has_attr("reversed") else 1
        number = int(listing.get("start", len(items) if step == -1 else 1))
        for item in items:
            number = int(item.get("value", number))
            item["value"] = str(number)
            number += step
        listing.attrs.pop("start", None)
        listing.attrs.pop("reversed", None)
    for font in soup.select("font"):
        css = []
        if font.has_attr("face"):
            css.append(f"font-family:{font.attrs.pop('face')}")
        if font.has_attr("color"):
            css.append(f"color:{font.attrs.pop('color')}")
        size = str(font.attrs.pop("size", ""))
        if re.fullmatch(r"[+-][0-9]{1,2}", size):
            size = str(max(1, min(7, 3 + int(size))))
        if size in "1234567" and size:
            css.append("font-size:" + ["x-small", "small", "medium", "large", "x-large", "xx-large", "xxx-large"][int(size) - 1])
        css.append(str(font.get("style", "")))
        font.name = "span"
        if any(css):
            font["style"] = ";".join(css)
    for node in soup.select("td, th"):
        if not node.get_text().replace("\xa0", "").strip() and not node.select("img, code, input"):
            node.clear()
    for node in list(soup.select("li > p, td > p, th > p")):
        if not node.attrs and len(node.parent.find_all("p", recursive=False)) == 1:
            node.unwrap()
    for node in list(soup.select("thead, tbody, tfoot")):
        node.unwrap()
    for node in list(soup.select("div")):
        children = [child for child in node.contents if not isinstance(child, NavigableString) or child.strip()]
        if not node.attrs and len(children) == 1 and isinstance(children[0], Tag) and children[0].name == "table":
            node.unwrap()
    for node in list(soup.select("b, i, s, strike")):
        node.name = {"b": "strong", "i": "em", "s": "del", "strike": "del"}[node.name]
    # Both equivalent nesting orders represent bold+italic text.
    for node in list(soup.select("strong > em")):
        parent = node.parent
        if len(parent.contents) == 1:
            parent.name, node.name = "em", "strong"

    def walk(node, verbatim=False):
        if isinstance(node, NavigableString):
            text = str(node)
            if not verbatim:
                if not text.strip() and node.parent.name in {"[document]", "table", "tr", "ul", "ol"}:
                    return None
                blocks = {"p", "ul", "ol", "li", "blockquote", "pre", "table", "tr", "th", "td", "hr", "br", "h1", "h2", "h3", "h4", "h5", "h6"}
                text = re.sub(r"[ \t\r\n]+", " ", text)
                if node.previous_sibling is None or isinstance(node.previous_sibling, Tag) and node.previous_sibling.name in blocks:
                    text = text.lstrip(" ")
                if node.next_sibling is None or isinstance(node.next_sibling, Tag) and node.next_sibling.name in blocks:
                    text = text.rstrip(" ")
            return ("text", text) if text else None
        if not isinstance(node, Tag):
            return None
        attrs = {}
        for name in (
            "id",
            "href",
            "src",
            "alt",
            "title",
            "start",
            "reversed",
            "value",
            "colspan",
            "rowspan",
            "type",
            "checked",
            "disabled",
            "style",
            "dir",
            "width",
            "height",
            "face",
            "size",
            "color",
        ):
            if name in node.attrs:
                attrs[name] = node[name]
        if node.name == "ol" and attrs.get("start") == "1":
            attrs.pop("start")
        for name in ("colspan", "rowspan"):
            if attrs.get(name) == "1":
                attrs.pop(name)
        if "style" in attrs:
            attrs["style"] = tuple(sorted(part.strip().replace(": ", ":") for part in attrs["style"].split(";") if part.strip()))
        if str(attrs.get("href", "")).startswith("#"):
            attrs["href"] = unquote(attrs["href"])
        language = [name for name in node.get("class", []) if name.startswith("language-")]
        if language:
            attrs["language"] = tuple(language)
        children = tuple(value for child in node.children if (value := walk(child, verbatim or node.name in {"pre", "code"})) is not None)
        return node.name, tuple(sorted(attrs.items())), children

    return tuple(value for child in soup.children if (value := walk(child)) is not None)


def assert_formatting(actual, expected, *, stage):
    assert semantic_html(actual) == semantic_html(expected), f"{stage}\nExpected HTML: {expected}\nActual HTML: {actual}"


@pytest.mark.parametrize("case", MARKDOWN_CASES, ids=lambda case: case.id)
def test_markdown_export(case):
    assert_formatting(_markdown_to_html(case.markdown), case.html, stage="Markdown -> EVA HTML")


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.id)
def test_eva_html_import(case):
    markdown = _html_to_markdown(case.html)
    assert_formatting(_markdown_to_html(markdown), case.html, stage=f"EVA HTML -> Markdown -> HTML\nMarkdown: {markdown}")


@pytest.mark.parametrize("case", MARKDOWN_CASES, ids=lambda case: case.id)
def test_two_roundtrips(case):
    html = _markdown_to_html(case.markdown)
    losses = []
    for cycle in (1, 2):
        html = _markdown_to_html(_html_to_markdown(html))
        if semantic_html(html) != semantic_html(case.html):
            losses.append(f"Cycle {cycle}: {html}")
    assert not losses, "\n".join(losses)


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.id)
def test_rpc_preserves_html_without_publishing(monkeypatch, case):
    """Real writer/reader and JSON serialization; only HTTP EVA is emulated."""
    page = {"id": "CmfDocument:qa", "project_id": "CmfProject:qa", "name": "QA", "code": "QA", "text": case.html, "text_draft": ""}
    methods = []

    def post(_session, _url, **kwargs):
        payload = json.loads(json.dumps(kwargs["json"], ensure_ascii=False))
        methods.append(payload["method"])
        if payload["method"] == "CmfDocument.update":
            assert payload["args"] == [page["id"]]
            page["text_draft"] = payload["kwargs"]["text_draft"]
            result = True
        else:
            assert payload["method"] == "CmfDocument.list"
            result = [page]
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"result": result}, ensure_ascii=False).encode()
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    settings = {"api_base_url": "https://eva.example.test", "project_id": page["project_id"], "retry_count": 0}
    writer = EvaWikiMutationClient(**settings)
    reader = EvaWikiConnector(**settings)
    writer.load_credentials({"eva_api_token": "synthetic-personal-token"})
    reader.load_credentials({"eva_api_token": "synthetic-personal-token"})
    writer.update_document_draft(page["id"], case.html)
    remote = reader.get_document_for_edit(page["id"])
    assert remote["draft_html"] == remote["html"] == case.html
    assert methods == ["CmfDocument.update", "CmfDocument.list"]


@pytest.mark.parametrize("unsafe", ["javascript:alert(1)", "data:text/html,evil", "vbscript:evil"])
def test_import_removes_active_content_and_keeps_safe_formatting(unsafe):
    source = f'<script>bad</script><style>bad</style><iframe>bad</iframe><p onmouseover="evil()"><strong>Safe</strong><a href="{unsafe}">label</a></p>'
    actual = _markdown_to_html(_html_to_markdown(source))
    soup = BeautifulSoup(actual, "html.parser")
    assert not soup.select("script, style, iframe, [onmouseover]")
    assert all(not str(node.get("href", "")).startswith(("javascript:", "data:", "vbscript:")) for node in soup.select("a"))
    assert soup.strong.text == "Safe"
    assert "label" in soup.text and "bad" not in soup.text


@pytest.mark.parametrize(
    "before,after",
    [
        ('<a href="https://example.test/a">A</a>', '<a href="https://example.test/b">A</a>'),
        ('<p style="text-align:right">A</p>', "<p>A</p>"),
        ("<p><strong>A</strong> B</p>", "<p><strong>A</strong>B</p>"),
        ("<p><strong>A</strong> <em>B</em></p>", "<p><strong>A</strong><em>B</em></p>"),
        ("<pre><code>a  \n</code></pre>", "<pre><code>a\n</code></pre>"),
        ('<pre><code class="language-json">{}</code></pre>', "<pre><code>{}</code></pre>"),
        ('<ol start="3"><li>A</li></ol>', "<ol><li>A</li></ol>"),
        ('<input type="checkbox" checked>', '<input type="checkbox">'),
        ('<table><tr><td colspan="2">A</td></tr></table>', "<table><tr><td>A</td></tr></table>"),
        ('<table><tr><td><p style="color:red">A</p></td></tr></table>', "<table><tr><td><p>A</p></td></tr></table>"),
    ],
)
def test_formatting_oracle_rejects_data_and_style_loss(before, after):
    assert semantic_html(before) != semantic_html(after)


def test_formatting_oracle_accepts_table_editor_serialization():
    expected = "<table><tr><th>A</th></tr><tr><td></td></tr></table>"
    editor = '<div><table>\n<thead>\n<tr>\n<th colspan="1"><p>A</p>\n</th>\n</tr>\n</thead>\n<tbody>\n<tr><td rowspan="1"><p>&#160;</p></td></tr>\n</tbody>\n</table></div>'
    assert semantic_html(editor) == semantic_html(expected)


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.id)
def test_governed_import_and_eva_export_preserve_body(case):
    from api.apps.business_documents.assets import import_document_markdown
    from api.apps.business_documents.exports import BusinessDocumentExportService
    from business_documents.domain.content import render_section_text
    from test.unit_test.api.apps.business_documents.test_business_document_service import _draft

    draft = _draft()
    draft["sections"][0]["blocks"] = [{"type": "paragraph", "text": case.markdown if case.markdown is not None else case.html}]
    html = BusinessDocumentExportService._render_eva_wiki(draft, 1)
    imported = import_document_markdown(_html_to_markdown(html))
    assert_formatting(_markdown_to_html(render_section_text(imported["sections"][0])), case.html, stage="AST -> EVA export -> governed AST import")
    reexported = BeautifulSoup(BusinessDocumentExportService._render_eva_wiki(imported, 2), "html.parser")
    # Assert spans on the actual export consumer, not just the direct converter.
    expected = BeautifulSoup(case.html, "html.parser")
    if expected.select("[colspan], [rowspan]"):
        actual_cells = reexported.select("[colspan], [rowspan]")
        for cell in expected.select("[colspan], [rowspan]"):
            assert any(candidate.text.strip() == cell.text.strip() and all(candidate.get(key) == cell[key] for key in ("colspan", "rowspan") if key in cell.attrs) for candidate in actual_cells)
