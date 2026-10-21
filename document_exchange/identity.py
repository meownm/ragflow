"""Canonical page keys shared by document products and saved bindings."""

import hashlib
import json
from urllib.parse import urlsplit


def binding_keys(binding):
    page_url = str(binding.get("page_url") or binding.get("web_url") or "").strip().rstrip("/").casefold()
    origin = binding.get("eva_origin")
    if isinstance(origin, str) and origin.strip():
        parsed = urlsplit(origin.strip())
        origin = (parsed.scheme, parsed.hostname, parsed.port)
    identity = None
    project = str(binding.get("project_id") or "").strip()
    document = str(binding.get("document_id") or binding.get("id") or "").strip()
    if isinstance(origin, (list, tuple)) and len(origin) == 3 and project and document:
        scheme, host, port = origin
        if scheme in {"http", "https"} and isinstance(host, str) and host:
            value = [[scheme, host.casefold(), port or (443 if scheme == "https" else 80)], project, document]
            identity = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return hashlib.sha256(page_url.encode()).hexdigest(), identity
