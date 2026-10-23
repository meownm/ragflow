"""Safe HTML retained when Markdown cannot represent EVA formatting exactly."""

import html
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Comment
from bs4.formatter import HTMLFormatter
from markdownify import MarkdownConverter


_POLICY = json.loads(Path(__file__).with_name("html_policy.json").read_text(encoding="utf-8"))
_CSS_VALUE = re.compile(_POLICY["cssValuePattern"])
_RICH_TAGS = {"u", "sub", "sup", "mark", "kbd", "abbr", "font", "dl", "details", "table"}
_FONT_SIZES = {"1": "x-small", "2": "small", "3": "medium", "4": "large", "5": "x-large", "6": "xx-large", "7": "xxx-large"}


def restore_eva_formatting(soup: BeautifulSoup) -> None:
    """Normalize EVA editor markup before the shared HTML safety boundary."""
    for anchor in soup.select("a[data-document-heading-anchor][id]"):
        container = anchor.parent if anchor.parent.name == "p" else anchor
        heading = container.find_previous_sibling()
        if heading is not None and re.fullmatch(r"h[1-6]", heading.name) and not container.get_text(strip=True) and len(container.find_all(True)) == (1 if container is not anchor else 0):
            heading["id"] = anchor["id"]
            container.decompose()
    for listing in soup.select("ul.tox-checklist, ol.tox-checklist"):
        for item in listing.find_all("li", recursive=False):
            classes = item.get("class", [])
            if "tox-checklist--hidden" not in classes:
                checkbox = soup.new_tag("input", attrs={"type": "checkbox", "disabled": ""})
                if "tox-checklist--checked" in classes:
                    checkbox["checked"] = ""
                paragraph = item.find("p", recursive=False)
                (paragraph if paragraph is not None else item).insert(0, checkbox)
            item["class"] = [name for name in classes if name not in {"tox-checklist--checked", "tox-checklist--hidden"}]
            if not item["class"]:
                del item["class"]
        listing["class"] = [name for name in listing["class"] if name != "tox-checklist"]
        if not listing["class"]:
            del listing["class"]
    for anchor in soup.select('a[cmf_converted][href^="#mcetoc_"]'):
        target = soup.find(id=unquote(anchor["href"][1:]))
        if target is not None:
            target["id"] = unquote(anchor["href"][8:])
        anchor["href"] = "#" + anchor["href"][8:]
        del anchor["cmf_converted"]
    # EVA drops the reversed attribute and presentational font attributes.
    # Explicit item values and CSS have the same visual meaning and survive.
    for listing in soup.select("ol[reversed]"):
        items = listing.find_all("li", recursive=False)
        raw_start = str(listing.get("start", len(items)))
        number = int(raw_start) if re.fullmatch(r"-?[0-9]{1,9}", raw_start) else len(items)
        for item in items:
            raw_value = str(item.get("value", ""))
            if re.fullmatch(r"-?[0-9]{1,9}", raw_value):
                number = int(raw_value)
            item["value"] = str(number)
            number -= 1
        del listing["reversed"]
    for font in soup.select("font"):
        declarations = []
        for attribute, prop in (("face", "font-family"), ("color", "color")):
            if attribute in font.attrs:
                declarations.append(f"{prop}:{font[attribute]}")
                del font[attribute]
        size = str(font.attrs.pop("size", ""))
        if re.fullmatch(r"[+-][0-9]{1,2}", size):
            size = str(max(1, min(7, 3 + int(size))))
        if size in _FONT_SIZES:
            declarations.append(f"font-size:{_FONT_SIZES[size]}")
        declarations.append(str(font.get("style", "")))
        font.name = "span"
        style = sanitize_style(";".join(declarations))
        if style:
            font["style"] = style


def prepare_eva_html(value: str) -> str:
    """Use the native editor representations accepted by EVA's save renderer."""
    soup = BeautifulSoup(value, "html.parser")
    if not soup.select('table, input[type="checkbox"], .tox-checklist, ol[reversed], font, a[cmf_converted], a[data-document-heading-anchor]'):
        return value
    restore_eva_formatting(soup)
    for index, table in enumerate(soup.select("table"), start=1):
        # The EVA table renderer collapses an unspecified width to its smallest
        # columns. Match the native editor's full-container default, including
        # nested tables, while retaining authored table and cell dimensions.
        style = str(table.get("style", ""))
        if not table.get("width") and not re.search(r"(?:^|;)\s*width\s*:", style, re.IGNORECASE):
            table["style"] = ";".join(part for part in (style.rstrip(";"), "width:100%") if part)
        if table.find_parent(attrs={"data-macros": "wikitables"}) is None:
            node_id = str(table.get("data-id", f"eva-table-{index}"))
            wrapper = soup.new_tag("div", attrs={"class": "tableView-content-wrap cmf-scrollbar", "data-macros": "wikitables", "data-id": f"{node_id}-wrap"})
            table.wrap(wrapper)
        occupied_until = {}
        rows = (row for row in table.find_all("tr") if row.find_parent("table") is table)
        for y, row in enumerate(rows):
            x = 0
            for cell in row.find_all(["th", "td"], recursive=False):
                while occupied_until.get(x, 0) > y:
                    x += 1
                for attribute in ("colspan", "rowspan"):
                    raw = str(cell.get(attribute, "1"))
                    cell[attribute] = str(max(1, min(1000, int(raw)))) if re.fullmatch(r"[0-9]{1,6}", raw) else "1"
                cell.attrs.update({"data-x": str(x), "data-y": str(y)})
                colspan, rowspan = int(cell["colspan"]), int(cell["rowspan"])
                for column in range(x, x + colspan):
                    occupied_until[column] = y + rowspan
                x += colspan
    for listing in soup.select("ul, ol"):
        items = listing.find_all("li", recursive=False)
        boxes = [next((box for box in item.select('input[type="checkbox"]') if box.find_parent("li") is item), None) for item in items]
        if not any(box is not None for box in boxes):
            continue
        listing["class"] = [*listing.get("class", []), "tox-checklist"]
        for item, box in zip(items, boxes):
            classes = list(item.get("class", []))
            if box is None:
                classes.append("tox-checklist--hidden")
            else:
                if box.has_attr("checked"):
                    classes.append("tox-checklist--checked")
                box.decompose()
            if classes:
                item["class"] = classes
    # Keep literal-source export escaping when native formatting requires a
    # serialization pass over a document that also contains code blocks.
    return soup.decode(formatter=HTMLFormatter(entity_substitution=lambda text: html.escape(text, quote=True)))


def is_document_html(value: str) -> bool:
    match = re.match(r"</?([a-zA-Z][\w-]*)", value)
    return bool(match and (match[1].lower() in _POLICY["tags"] or match[1].lower() in _POLICY["dropContent"]))


def safe_url(value: str, *, image: bool = False) -> bool:
    normalized = re.sub(r"[\x00-\x20\x7f]", "", html.unescape(value))
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return False
    if image:
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)
    return parsed.scheme.lower() in {"", "http", "https", "mailto"}


def sanitize_style(value: str) -> str:
    declarations = []
    for part in value.split(";"):
        name, _, content = part.partition(":")
        name, content = name.strip().lower(), content.strip()
        if name in _POLICY["cssProperties"] and _CSS_VALUE.fullmatch(content):
            declarations.append(f"{name}:{content}")
    return ";".join(declarations)


def sanitize_html(value: str) -> str:
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for wrapper in soup.select('div[data-macros="wikitables"]'):
        elements = wrapper.find_all(recursive=False)
        if (
            wrapper.attrs.keys() <= {"class", "data-macros", "data-id"}
            and set(wrapper.get("class", [])) <= {"tableView-content-wrap", "cmf-scrollbar"}
            and len(elements) == 1
            and elements[0].name == "table"
            and not any(text.strip() for text in wrapper.find_all(string=True, recursive=False))
        ):
            # This editor shell is reconstructed on export. Retaining it as a
            # generic div would add another shell on every import/export cycle.
            wrapper.unwrap()
    restore_eva_formatting(soup)
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for tag in list(soup.find_all(True)):
        if tag.name is None:
            continue
        if tag.name in _POLICY["dropContent"] or tag.name == "input" and str(tag.get("type", "")).lower() != "checkbox":
            tag.decompose()
            continue
        if tag.name not in _POLICY["tags"]:
            tag.unwrap()
            continue
        allowed = {*_POLICY["attributes"]["*"], *_POLICY["attributes"].get(tag.name, [])}
        for name in list(tag.attrs):
            value = tag[name]
            if name not in allowed:
                del tag[name]
            elif name in {"href", "src"} and not safe_url(str(value), image=name == "src"):
                del tag[name]
            elif name == "style":
                style = sanitize_style(str(value))
                if style:
                    tag[name] = style
                else:
                    del tag[name]
            elif name == "class":
                classes = [item for item in value if re.fullmatch(r"language-[\w+-]+", item) or tag.name == "aside" and item == "business-document-warnings"]
                if classes:
                    tag[name] = classes
                else:
                    del tag[name]
        if tag.name == "input":
            tag["type"] = "checkbox"
            tag["disabled"] = ""
        if tag.name == "img" and not tag.get("src"):
            tag.replace_with(str(tag.get("alt", "")))
    return str(soup)


class EvaMarkdownConverter(MarkdownConverter):
    """Use Markdown for text/code and safe HTML for nonrepresentable formatting."""

    def process_tag(self, node, parent_tags=None):
        # Preserve a whole block so inline HTML does not reinterpret literal
        # Markdown markers in its text. Never wrap the document/section outline.
        if node.name in {"p", "div", "ul", "ol", "blockquote", "table", "dl", "details", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}:
            descendants = [node, *node.find_all(True)]
            numbered_heading = re.fullmatch(r"h[1-6]", node.name) and re.match(r"^\d+(?:\.\d+)*\.\s", node.get_text())
            if (
                any(tag.name in _RICH_TAGS or tag.attrs.keys() & {"style", "reversed", "value", "type", "width", "height", "dir", "title", "id"} for tag in descendants)
                and not node.find(re.compile(r"^h[1-6]$"))
                and not numbered_heading
            ):
                return "\n\n" + str(node) + "\n\n"
            if node.name == "p" and not node.get_text().strip() and node.find("br"):
                return "\n\n" + str(node) + "\n\n"
        return super().process_tag(node, parent_tags)

    def process_text(self, el, parent_tags=None):
        text = super().process_text(el, parent_tags)
        return text if parent_tags and "_noformat" in parent_tags else html.escape(text, quote=False)

    def convert_hN(self, n, el, text, parent_tags):
        heading = super().convert_hN(n, el, text, parent_tags)
        if el.get("id"):
            heading += f'<a id="{html.escape(el["id"], quote=True)}" data-document-heading-anchor=""></a>\n\n'
        return heading

    def convert_a(self, el, text, parent_tags):
        if el.get("id"):
            return str(el)
        return super().convert_a(el, text, parent_tags)

    def convert_pre(self, el, text, parent_tags):
        code = el.find("code")
        source = (code or el).get_text()
        if not source.endswith("\n"):
            return "\n\n" + str(el) + "\n\n"
        language = next((name.removeprefix("language-") for name in (code or el).get("class", []) if name.startswith("language-")), "")
        fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", source)), default=0))
        return f"\n\n{fence}{language}\n{source}{fence}\n\n"

    def convert_strike(self, el, text, parent_tags):
        return self.convert_del(el, text, parent_tags)
