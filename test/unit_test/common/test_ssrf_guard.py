import socket

import pytest

from common import ssrf_guard


def _private_addr_info(*_args, **_kwargs):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.65.254", 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fdc4:f303:9324::254", 0, 0, 0)),
    ]


def test_private_host_is_blocked_without_exact_allowlist(monkeypatch):
    monkeypatch.delenv("ALLOW_ANY_HOST", raising=False)
    monkeypatch.delenv("SSRF_ALLOWED_PRIVATE_DB_HOSTS", raising=False)
    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", _private_addr_info)

    with pytest.raises(ValueError, match="non-public"):
        ssrf_guard.assert_host_is_safe("host.docker.internal")


def test_exact_private_host_allowlist_preserves_dns_resolution(monkeypatch):
    monkeypatch.delenv("ALLOW_ANY_HOST", raising=False)
    monkeypatch.setenv(
        "SSRF_ALLOWED_PRIVATE_DB_HOSTS",
        " host.docker.internal.,postgres.internal ",
    )
    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", _private_addr_info)

    assert ssrf_guard.assert_host_is_safe("HOST.DOCKER.INTERNAL") == "192.168.65.254"
    with pytest.raises(ValueError, match="non-public"):
        ssrf_guard.assert_url_is_safe("http://host.docker.internal:9380/")


def test_allowlist_does_not_match_subdomains(monkeypatch):
    monkeypatch.delenv("ALLOW_ANY_HOST", raising=False)
    monkeypatch.setenv("SSRF_ALLOWED_PRIVATE_DB_HOSTS", "host.docker.internal")
    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", _private_addr_info)

    with pytest.raises(ValueError, match="non-public"):
        ssrf_guard.assert_host_is_safe("evil.host.docker.internal")
