"""Production Eva text codec loaded without the API application's bootstrap.

Only import wiring is replaced; renderer and decoder bodies execute unchanged.
The codec is shared by isolated backend and browser regression fixtures.
"""

import ast
import importlib.util
import json
from pathlib import Path

from bs4 import BeautifulSoup

from api.document_exchange.content import canonical_markdown
from business_documents.domain import content as document_content

_ROOT = Path(__file__).resolve().parents[3]
_FORMAT = _ROOT / "api/apps/business_documents/html_formatting.py"
_SPEC = importlib.util.spec_from_file_location("test_eva_html_formatting", _FORMAT)
_formatting = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_formatting)
_scope = {
    "BeautifulSoup": BeautifulSoup,
    "json": json,
    "_canonical_markdown": canonical_markdown,
    **{name: getattr(_formatting, name) for name in ("is_document_html", "safe_url", "sanitize_html", "EvaMarkdownConverter", "restore_eva_formatting")},
    **{name: getattr(document_content, name) for name in ("decode_document_warnings", "document_warning_texts", "render_document_warning_appendix", "with_document_warnings")},
}
_renderer = ast.parse((_ROOT / "api/apps/business_documents/markdown_rendering.py").read_text(encoding="utf-8"))
_renderer.body = [node for node in _renderer.body if not (isinstance(node, ast.ImportFrom) and node.module == "api.apps.business_documents.html_formatting")]
exec(compile(_renderer, "production_eva_renderer", "exec"), _scope)
for _filename, _name in (("eva_diagrams.py", "restore_diagram_sources"), ("eva_changes.py", "_html_to_markdown")):
    _tree = ast.parse((_ROOT / "api/apps/business_documents" / _filename).read_text(encoding="utf-8"))
    _function = next(node for node in _tree.body if isinstance(node, ast.FunctionDef) and node.name == _name)
    exec(compile(ast.Module(body=[_function], type_ignores=[]), "production_eva_decoder", "exec"), _scope)


def eva_roundtrip(source):
    return _scope["_html_to_markdown"](_formatting.prepare_eva_html(_scope["render_markdown"](source)))
