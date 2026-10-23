"""Translate PlantUML code and EVA's editable diagram macros at the API boundary."""

import hashlib
import json
from collections import Counter
from collections.abc import Callable

from bs4 import BeautifulSoup

from common.data_source.exceptions import ConnectorValidationError
from business_documents.application.errors import BusinessDocumentError


_SVG_TAGS = frozenset(
    {"svg", "g", "defs", "clippath", "rect", "path", "line", "polyline", "polygon", "circle", "ellipse", "text", "tspan", "title", "desc", "lineargradient", "radialgradient", "stop"}
)
_SVG_ATTRIBUTES = frozenset(
    {
        "xmlns",
        "width",
        "height",
        "viewbox",
        "preserveaspectratio",
        "x",
        "y",
        "x1",
        "x2",
        "y1",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "points",
        "transform",
        "id",
        "fill",
        "fill-opacity",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "stroke-dasharray",
        "stroke-linecap",
        "stroke-linejoin",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-anchor",
        "textlength",
        "lengthadjust",
        "dominant-baseline",
        "offset",
        "stop-color",
        "stop-opacity",
        "gradientunits",
        "gradienttransform",
    }
)


def _safe_svg(value: str) -> str:
    # EVA embeds this as inline HTML, including SVG returned with an XML prolog.
    soup = BeautifulSoup(f"<div>{value}</div>", "html.parser")
    svg = soup.find("svg")
    if svg is None:
        raise ConnectorValidationError("EVA Wiki returned an invalid PlantUML image")
    # PlantUML emits geometry, text and presentation attributes. Executable SVG,
    # embedded HTML, remote resources and CSS are not needed for these diagrams.
    for tag in [svg, *svg.find_all(True)]:
        if tag.name is None:
            continue
        if tag.name not in _SVG_TAGS:
            tag.decompose()
            continue
        for attribute in list(tag.attrs):
            if attribute == "style":
                for declaration in str(tag[attribute]).split(";"):
                    name, _, content = declaration.partition(":")
                    if name.strip() in _SVG_ATTRIBUTES and "url(" not in content.casefold():
                        tag[name.strip()] = content.strip()
            if attribute not in _SVG_ATTRIBUTES or "url(" in str(tag[attribute]).casefold():
                del tag[attribute]
    return str(svg)


def restore_diagram_sources(soup: BeautifulSoup) -> None:
    """Discard macro chrome and rendered labels, retaining exact editable UML."""
    for macro in soup.select('[data-macros="plantuml"]'):
        try:
            source = json.loads(macro.get("data-macros-parameters", ""))["uml"]
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(source, str) or not source.strip():
            continue
        code = soup.new_tag("code", attrs={"class": "language-plantuml"})
        code.string = source + "\n"
        pre = soup.new_tag("pre")
        pre.append(code)
        macro.replace_with(pre)


def rendered_diagrams_match(expected_html: str, actual_html: str) -> bool:
    """Keep editable SVG macros when deciding whether publication already happened.

    Markdown equality intentionally ignores diagram previews. It cannot establish
    that a prepared SVG replaced the published source-code fallback.
    """

    def sources(value: str) -> Counter[str]:
        rendered = Counter()
        for macro in BeautifulSoup(value, "html.parser").select('[data-macros="plantuml"]'):
            if macro.find("svg") is None:
                continue
            try:
                source = json.loads(macro.get("data-macros-parameters", ""))["uml"]
            except (ValueError, KeyError, TypeError):
                continue
            if isinstance(source, str) and source.strip():
                rendered[source.replace("\r\n", "\n").strip()] += 1
        return rendered

    expected = sources(expected_html)
    return not expected or expected <= sources(actual_html)


def render_eva_diagrams(value: str, render: Callable[[str], str]) -> str:
    soup = BeautifulSoup(value, "html.parser")
    rendered = {}
    for index, pre in enumerate(soup.select("pre")):
        code = pre.find("code")
        source = (code or pre).get_text().strip()
        language = set(pre.get("class", [])) | set(code.get("class", []) if code is not None else [])
        if not language.intersection({"language-plantuml", "language-puml", "language-startuml"}) and not (source.startswith("@startuml") and source.endswith("@enduml")):
            continue
        try:
            if source not in rendered:
                rendered[source] = _safe_svg(render(source))
        except ConnectorValidationError as error:
            raise BusinessDocumentError("EVA_DIAGRAM_RENDER_FAILED", "Не удалось построить схему PlantUML для EVA. Документ не отправлен. Проверьте схему и повторите выгрузку.", 502) from error
        identifier = f"plantuml+{index}-{hashlib.sha256(source.encode()).hexdigest()[:16]}"
        macro = soup.new_tag(
            "div",
            attrs={
                "id": identifier,
                "class": "macros-panel plantuml",
                "contenteditable": "false",
                "data-macros": "plantuml",
                "data-macros-parameters": json.dumps({"uml": source}, ensure_ascii=False),
            },
        )
        title = soup.new_tag("div", attrs={"data-layout": "default", "class": "extension-container", "contenteditable": "false"})
        label = soup.new_tag("span", attrs={"class": "extension-title"})
        excerpt = soup.new_tag("span", attrs={"class": "excerpt-title"})
        excerpt.string = "PlantUML"
        label.append(excerpt)
        title.append(label)
        actions = soup.new_tag("span", attrs={"class": "macros-actions"})
        edit = soup.new_tag("span", attrs={"class": "macros-edit", "data-macros-action": "macros-edit"})
        edit.string = "Изменить"
        actions.append(edit)
        title.append(actions)
        macro.append(title)
        preview = soup.new_tag("div", attrs={"class": "macros-preview"})
        preview.append(BeautifulSoup(rendered[source], "html.parser").svg)
        macro.append(preview)
        pre.replace_with(macro)
    return str(soup)
