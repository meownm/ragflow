"""Real loopback HTTP proves the external watchdog reads without scheduling."""

import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.quality.check_document_quality_night import check_nights

NOW = datetime(2026, 10, 20, 10, tzinfo=UTC)


@pytest.fixture
def health_server():
    state = {"mode": "ok", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append((self.command, self.path, self.headers.get("Authorization")))
            query = parse_qs(urlsplit(self.path).query)
            product = query["document_type"][0]
            data = {
                "document_type": product,
                "observed_at": NOW.isoformat(),
                "slot": "2026-10-20T02:00:00+03:00",
                "status": "OK",
                "reason_code": None,
                "campaign_id": "synthetic",
                "campaign_status": "COMPLETE",
            }
            mode = state["mode"]
            if mode == "alert" and product == "technical_specification":
                data.update(status="ALERT", reason_code="NIGHTLY_MISSING", campaign_id=None, campaign_status=None)
            if mode == "stale":
                data["observed_at"] = (NOW - timedelta(minutes=6)).isoformat()
            if mode == "future":
                data["observed_at"] = (NOW + timedelta(minutes=2)).isoformat()
            if mode == "wrong-product":
                data["document_type"] = "other"
            if mode == "naive":
                data["observed_at"] = NOW.replace(tzinfo=None).isoformat()
            if mode == "contradictory":
                data["reason_code"] = "NIGHTLY_MISSING"
            data.update(state.get("override", {}))
            body = json.dumps({"code": 0, "data": data}).encode()
            if mode == "oversized":
                body = b"x" * (64 * 1024 + 1)
            if mode == "private-error":
                body = b'{"code":500,"message":"private token and document"}'
            if mode == "duplicate":
                body = b'{"code":1,"code":0,"data":' + json.dumps(data).encode() + b"}"
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "/forbidden")
                self.end_headers()
                return
            self.send_response(401 if mode == "unauthorized" else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("ok", 0),
        ("alert", 1),
        ("stale", 2),
        ("future", 2),
        ("wrong-product", 2),
        ("naive", 2),
        ("contradictory", 2),
        ("oversized", 2),
        ("private-error", 2),
        ("unauthorized", 2),
        ("redirect", 2),
        ("duplicate", 2),
    ],
)
def test_monitor_rejects_missing_or_stale_proof_and_never_calls_scheduler(health_server, mode, expected):
    state, base = health_server
    state["mode"] = mode
    code, report = check_nights(base, "synthetic-admin-token", now=NOW)
    assert code == expected
    assert len(state["requests"]) == 2
    assert all(method == "GET" and urlsplit(path).path == "/api/v1/admin/document-quality/health" and token == "synthetic-admin-token" for method, path, token in state["requests"])
    assert {item["document_type"] for item in report["checks"]} == {"business_requirements", "technical_specification"}
    assert "private" not in str(report) and "synthetic-admin-token" not in str(report)


@pytest.mark.parametrize(
    "url,token",
    [("https://user:password@example.com", "synthetic"), ("file:///private", "synthetic"), ("http://localhost?secret=x", "synthetic"), ("http://localhost", None), ("http://localhost", "bad\ntoken")],
)
def test_bad_monitor_configuration_is_rejected_before_network(url, token):
    with pytest.raises(ValueError, match="Configure"):
        check_nights(url, token, now=NOW)


@pytest.mark.parametrize(
    "now,slot,expected",
    [
        ("2026-10-20T02:14:59+03:00", "2026-10-19T02:00:00+03:00", 0),
        ("2026-10-20T02:15:00+03:00", "2026-10-19T02:00:00+03:00", 2),
        ("2026-10-20T03:00:00+03:00", "2026-10-19T02:00:00+03:00", 2),
        ("2026-10-20T02:15:00+03:00", "2026-10-20T02:00:00+03:00", 0),
        ("2026-10-19T23:15:00+00:00", "2026-10-19T23:00:00+00:00", 0),
        ("2026-10-20T02:14:59+03:00", "2026-10-20T02:00:00+03:00", 2),
    ],
)
def test_monitor_requires_exact_due_slot_across_grace_and_timezones(health_server, now, slot, expected):
    state, base = health_server
    state["override"] = {"observed_at": now, "slot": slot}
    code, report = check_nights(base, "synthetic", now=datetime.fromisoformat(now))
    assert code == expected
    assert all(item["reason_code"] == ("HEALTH_SLOT_MISMATCH" if expected else None) for item in report["checks"])


@pytest.mark.parametrize("status", ["PENDING", "RUNNING"])
@pytest.mark.parametrize("now,expected", [("2026-10-20T14:00:00+03:00", 0), ("2026-10-20T14:00:01+03:00", 1)])
def test_monitor_detects_overdue_even_if_health_wrongly_says_ok(health_server, status, now, expected):
    state, base = health_server
    state["override"] = {"observed_at": now, "campaign_status": status}
    code, report = check_nights(base, "synthetic", now=datetime.fromisoformat(now))
    assert code == expected
    assert all(item["reason_code"] == ("NIGHTLY_OVERDUE" if expected else None) for item in report["checks"])
