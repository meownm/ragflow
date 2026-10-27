"""Render real generated BT diagrams through an explicitly selected loopback app."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import xml.etree.ElementTree as ET

import pytest


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


@pytest.fixture(scope="module")
def render_http():
    base = os.environ.get("RAGFLOW_PLANTUML_HTTP_BASE_URL")
    if not base:
        pytest.skip("Set RAGFLOW_PLANTUML_HTTP_BASE_URL for the live renderer lane")
    url = urlsplit(base)
    assert url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}
    assert not url.username and not url.password and not url.query and not url.fragment
    opener = build_opener(ProxyHandler({}), _NoRedirect())

    def render(source):
        request = Request(base.rstrip("/") + "/plantuml/svg/", data=source.encode("utf-8"), headers={"Content-Type": "text/plain; charset=utf-8"}, method="POST")
        try:
            response = opener.open(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            return response.status, dict(response.headers), response.read()

    return render


def test_generated_business_document_diagrams_render(render_http, tmp_path):
    source = Path(os.environ["RAGFLOW_PLANTUML_AST_DIR"])
    assert source.is_dir()
    target = Path(os.environ.get("RAGFLOW_PLANTUML_HTTP_EVIDENCE", str(tmp_path)))
    target.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(source.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("document_type") != "business_requirements":
            continue
        for section in document["sections"]:
            for index, block in enumerate(section["blocks"]):
                if block.get("type") != "plantuml":
                    continue
                status, headers, svg = render_http(block["source"])
                error = next((value for key, value in headers.items() if key.lower() == "x-plantuml-diagram-error"), None)
                mime = next((value for key, value in headers.items() if key.lower() == "content-type"), "")
                name = f"{path.stem}-section-{section['id']}-block-{index}"
                (target / (name + ".svg")).write_bytes(svg)
                results.append({"name": name, "http": status, "renderer_error": error})
                (target / "render-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                assert status == 200 and not error, f"{name}: HTTP {status}, renderer error={error}"
                assert mime.split(";", 1)[0] == "image/svg+xml", name
                root = ET.fromstring(svg)
                assert root.tag.rsplit("}", 1)[-1] == "svg" and len(root), name
    assert results, "No actual generated PlantUML sources were selected"


def test_invalid_mixed_component_and_sequence_is_rejected(render_http, tmp_path):
    source = '@startuml\nactor "Читатель" as Reader\ncomponent "Портал" as Portal\nalt Одобрение\nReader --> Portal : Запрос\nelse Возврат\nPortal --> Reader : Данные\nend\n@enduml'
    status, headers, _ = render_http(source)
    error = next((value for key, value in headers.items() if key.lower() == "x-plantuml-diagram-error"), None)
    target = Path(os.environ.get("RAGFLOW_PLANTUML_HTTP_EVIDENCE", str(tmp_path)))
    target.mkdir(parents=True, exist_ok=True)
    (target / "invalid-source-result.json").write_text(json.dumps({"http": status, "renderer_error": error}, ensure_ascii=False), encoding="utf-8")
    assert status == 400, "Expected the renderer's syntax error, not success, a redirect or an outage"
