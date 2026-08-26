import logging
import re
from copy import copy
from dataclasses import dataclass
from io import BytesIO
from typing import IO
from urllib.parse import unquote, urlparse

import bs4

from common.data_source.config import (
    HTML_BASED_CONNECTOR_TRANSFORM_LINKS_STRATEGY,
    HtmlBasedConnectorTransformLinksStrategy,
    WEB_CONNECTOR_IGNORED_CLASSES,
    WEB_CONNECTOR_IGNORED_ELEMENTS,
    PARSE_WITH_TRAFILATURA,
)

MINTLIFY_UNWANTED = ["sticky", "hidden"]


@dataclass
class ParsedHTML:
    title: str | None
    cleaned_text: str


def strip_excessive_newlines_and_spaces(document: str) -> str:
    # collapse repeated spaces into one
    document = re.sub(r" +", " ", document)
    # remove trailing spaces
    document = re.sub(r" +[\n\r]", "\n", document)
    # remove repeated newlines
    document = re.sub(r"[\n\r]+", "\n", document)
    return document.strip()


def strip_newlines(document: str) -> str:
    # HTML might contain newlines which are just whitespaces to a browser
    return re.sub(r"[\n\r]+", " ", document)


def format_element_text(element_text: str, link_href: str | None) -> str:
    element_text_no_newlines = strip_newlines(element_text)

    if not link_href or HTML_BASED_CONNECTOR_TRANSFORM_LINKS_STRATEGY == HtmlBasedConnectorTransformLinksStrategy.STRIP:
        return element_text_no_newlines

    return f"[{element_text_no_newlines}]({link_href})"


def parse_html_with_trafilatura(html_content: str) -> str:
    """Parse HTML content using trafilatura."""
    import trafilatura  # type: ignore
    from trafilatura.settings import use_config  # type: ignore

    config = use_config()
    config.set("DEFAULT", "include_links", "True")
    config.set("DEFAULT", "include_tables", "True")
    config.set("DEFAULT", "include_images", "True")
    config.set("DEFAULT", "include_formatting", "True")

    extracted_text = trafilatura.extract(html_content, config=config)
    return strip_excessive_newlines_and_spaces(extracted_text) if extracted_text else ""


def format_document_soup(document: bs4.BeautifulSoup, table_cell_separator: str = "\t") -> str:
    """Format html to a flat text document.

    The following goals:
    - Newlines from within the HTML are removed (as browser would ignore them as well).
    - Repeated newlines/spaces are removed (as browsers would ignore them).
    - Newlines only before and after headlines and paragraphs or when explicit (br or pre tag)
    - Table columns/rows are separated by newline
    - List elements are separated by newline and start with a hyphen
    """
    ignored_tags = {"script", "style", "noscript", "template"}
    block_tags = {
        "address",
        "article",
        "aside",
        "blockquote",
        "details",
        "div",
        "dl",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "main",
        "nav",
        "ol",
        "p",
        "section",
        "summary",
        "ul",
    }

    def render_children(tag: bs4.element.Tag) -> str:
        return "".join(render_node(child) for child in tag.children)

    def render_image(tag: bs4.element.Tag) -> str:
        src_value = tag.get("src") or ""
        src = src_value[0] if isinstance(src_value, list) else str(src_value)
        label_value = tag.get("alt") or tag.get("title") or ""
        label = label_value[0] if isinstance(label_value, list) else str(label_value)
        if not label and src and not src.lower().startswith("data:"):
            label = unquote(urlparse(src).path.rsplit("/", 1)[-1])
        label = strip_newlines(label).strip() or "image"
        if not src or src.lower().startswith("data:"):
            return f"[Image: {label}]"
        return f"[Image: {label}]({src})"

    def render_table(tag: bs4.element.Tag) -> str:
        rows: list[str] = []
        for row in tag.find_all("tr"):
            if row.find_parent("table") is not tag:
                continue
            cells = row.find_all(["td", "th"], recursive=False)
            if not cells:
                cells = [cell for cell in row.find_all(["td", "th"]) if cell.find_parent("tr") is row]
            rendered_cells = [strip_excessive_newlines_and_spaces(render_children(cell)) for cell in cells]
            if rendered_cells:
                rows.append(table_cell_separator.join(rendered_cells))
        return "\n" + "\n".join(rows) + "\n" if rows else ""

    def render_node(node: bs4.element.PageElement) -> str:
        if isinstance(node, (bs4.element.Comment, bs4.element.Doctype)):
            return ""
        if isinstance(node, bs4.element.NavigableString):
            return str(node)
        if not isinstance(node, bs4.element.Tag):
            return ""

        name = (node.name or "").lower()
        if name in ignored_tags:
            return ""
        if name == "br":
            return "\n"
        if name == "img":
            return render_image(node)
        if name == "table":
            return render_table(node)
        if name == "pre":
            return f"\n{node.get_text()}\n"

        content = render_children(node)
        if name == "a":
            href_value = node.get("href")
            href = href_value[0] if isinstance(href_value, list) else href_value
            return format_element_text(content, str(href) if href else None)
        if name == "li":
            return f"\n- {content.strip()}\n"
        if name in block_tags:
            return f"\n{content}\n"
        return content

    return strip_excessive_newlines_and_spaces(render_children(document))


def parse_html_page_basic(text: str | BytesIO | IO[bytes]) -> str:
    soup = bs4.BeautifulSoup(text, "html.parser")
    return format_document_soup(soup)


def web_html_cleanup(
    page_content: str | bs4.BeautifulSoup,
    mintlify_cleanup_enabled: bool = True,
    additional_element_types_to_discard: list[str] | None = None,
) -> ParsedHTML:
    if isinstance(page_content, str):
        soup = bs4.BeautifulSoup(page_content, "html.parser")
    else:
        soup = page_content

    title_tag = soup.find("title")
    title = None
    if title_tag and title_tag.text:
        title = title_tag.text
        title_tag.extract()

    # Heuristics based cleaning of elements based on css classes
    unwanted_classes = copy(WEB_CONNECTOR_IGNORED_CLASSES)
    if mintlify_cleanup_enabled:
        unwanted_classes.extend(MINTLIFY_UNWANTED)
    for undesired_element in unwanted_classes:
        [tag.extract() for tag in soup.find_all(class_=lambda x: x and undesired_element in x.split())]

    for undesired_tag in WEB_CONNECTOR_IGNORED_ELEMENTS:
        [tag.extract() for tag in soup.find_all(undesired_tag)]

    if additional_element_types_to_discard:
        for undesired_tag in additional_element_types_to_discard:
            [tag.extract() for tag in soup.find_all(undesired_tag)]

    soup_string = str(soup)
    page_text = ""

    if PARSE_WITH_TRAFILATURA:
        try:
            page_text = parse_html_with_trafilatura(soup_string)
            if not page_text:
                raise ValueError("Empty content returned by trafilatura.")
        except Exception as e:
            logging.info(f"Trafilatura parsing failed: {e}. Falling back on bs4.")
            page_text = format_document_soup(soup)
    else:
        page_text = format_document_soup(soup)

    # 200B is ZeroWidthSpace which we don't care for
    cleaned_text = page_text.replace("\u200b", "")

    return ParsedHTML(title=title, cleaned_text=cleaned_text)
