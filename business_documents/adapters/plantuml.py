"""Read-only syntax checks against the configured internal PlantUML service."""

import os
import re
import xml.etree.ElementTree as ET

import httpx

from business_documents.application.diagram_repair import DiagramCheck
from business_documents.domain.diagram_repair import MAX_SOURCE_CHARS


class PlantUmlRenderer:
    def __init__(self, base_url: str | None = None):
        self._url = (base_url or os.environ.get("PLANTUML_SERVER_URL", "http://plantuml-server:8080")).rstrip("/") + "/svg/"

    def check(self, source: str) -> DiagramCheck:
        if len(source) > MAX_SOURCE_CHARS or re.search(r"^\s*!(?:include\w*|import)\b", source, re.MULTILINE | re.IGNORECASE):
            return DiagramCheck("unavailable")
        try:
            with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
                with client.stream("POST", self._url, content=source.encode("utf-8"), headers={"Content-Type": "text/plain; charset=utf-8"}) as response:
                    version = response.headers.get("x-powered-by", "")[:128]
                    error = response.headers.get("x-plantuml-diagram-error")
                    if response.status_code == 400 and error:
                        raw_line = response.headers.get("x-plantuml-diagram-error-line", "")
                        line = int(raw_line) + 1 if raw_line.isascii() and raw_line.isdecimal() and len(raw_line) < 8 and int(raw_line) < len(source.splitlines()) else None
                        return DiagramCheck("syntax_error", error[:800], line, version)
                    if response.status_code != 200 or error or response.headers.get("content-type", "").split(";", 1)[0] != "image/svg+xml":
                        return DiagramCheck("unavailable", version=version)
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            return DiagramCheck("unavailable", version=version)
                    root = ET.fromstring(body)
                    if root.tag.rsplit("}", 1)[-1] == "svg" and len(root):
                        return DiagramCheck("valid", version=version)
        except (httpx.HTTPError, ET.ParseError, ValueError):
            pass
        return DiagramCheck("unavailable")
