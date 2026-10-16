"""CVE-2026-48710: reconstructed URLs must keep the routed ASGI path."""

import pytest
from starlette.requests import Request


@pytest.mark.parametrize("host", [b"attacker.example/allowed?x=", b"attacker.example#fragment", b"attacker.example?query="])
def test_malformed_host_cannot_replace_the_route_path(host):
    scope = {
        "type": "http",
        "scheme": "http",
        "server": ("127.0.0.1", 9011),
        "path": "/v1/asr/jobs",
        "query_string": b"limit=1",
        "headers": [(b"host", host)],
    }
    request = Request(scope)
    assert request.url.path == scope["path"]
    assert request.url.hostname == "127.0.0.1"
    assert request.url.query == "limit=1"


@pytest.mark.parametrize("host,hostname", [(b"asr.example:9011", "asr.example"), (b"[::1]:9011", "::1")])
def test_valid_host_preserves_supported_urls(host, hostname):
    scope = {
        "type": "http",
        "scheme": "http",
        "server": ("127.0.0.1", 9011),
        "path": "/v1/asr/jobs",
        "query_string": b"",
        "headers": [(b"host", host)],
    }
    request = Request(scope)
    assert request.url.path == scope["path"]
    assert request.url.hostname == hostname
