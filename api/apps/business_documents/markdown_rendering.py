"""Markdown representation shared by document exports and EVA drafts."""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlsplit

import mistune
from mistune.plugins.table import table_in_list, table_in_quote

from business_documents.application.errors import ValidationError
from api.apps.business_documents.html_formatting import is_document_html, safe_url, sanitize_html


def validate_external_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("UNSAFE_EXPORT_URL", "Only absolute HTTP(S) resources are allowed in exported documents")
    return value


class _MarkdownRenderer(mistune.HTMLRenderer):
    def inline_html(self, value: str) -> str:
        return value if is_document_html(value) else html.escape(value)

    def softbreak(self) -> str:
        return self.linebreak()

    def text(self, text: str) -> str:
        return html.escape(html.unescape(text), quote=False)

    def link(self, text: str, url: str, title: str | None = None) -> str:
        return super().link(text, url, title) if safe_url(url) else text

    def image(self, text: str, url: str, title: str | None = None) -> str:
        return super().image(text, url, title) if safe_url(url, image=True) else html.escape(text)

    def block_text(self, text: str) -> str:
        return self.paragraph(text)

    def table_cell(self, text: str, align: str | None = None, head: bool = False) -> str:
        tag = "th" if head else "td"
        alignment = f' style="text-align:{align}"' if align else ""
        return f"<{tag}{alignment}><p>{text or '&#160;'}</p></{tag}>\n"


_MARKDOWN = mistune.create_markdown(renderer=_MarkdownRenderer(escape=False), plugins=["table", table_in_list, table_in_quote, "task_lists", "strikethrough", "url"])


def markdown_environment(source: str) -> dict[str, Any]:
    # Definitions belong to the complete section, even when AST blocks retain
    # separate export nodes and stable identities.
    return _MARKDOWN.parse(source)[1].env


def render_markdown(source: str, environment: dict[str, Any] | None = None) -> str:
    state = mistune.BlockState()
    if environment is not None:
        state.env.update(environment)
    return sanitize_html(_MARKDOWN.parse(source, state)[0])


def render_inline_markdown(value: object, environment: dict[str, Any] | None = None) -> str:
    tokens = _MARKDOWN.inline(str(value), environment if environment is not None else {})
    return sanitize_html(_MARKDOWN.renderer(tokens, mistune.BlockState()))
