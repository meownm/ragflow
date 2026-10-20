"""Admin access and request contract for the document quality endpoints."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin

ADMIN_SERVER = Path(__file__).parents[3] / "admin" / "server"
if str(ADMIN_SERVER) not in sys.path:
    sys.path.insert(0, str(ADMIN_SERVER))

import auth
import routes
from common.constants import ActiveEnum


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    manager = LoginManager(app)

    class User(UserMixin):
        id = "admin"
        email = "admin@example.com"

    @manager.request_loader
    def load_user(request):
        return User() if request.headers.get("Authorization") else None

    monkeypatch.setattr(auth.UserService, "filter_by_id", lambda _id: SimpleNamespace(is_superuser=True, is_active=ActiveEnum.ACTIVE.value))
    app.register_blueprint(routes.admin_bp)
    return app.test_client()


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/maintenance"),
        ("get", "/document-quality"),
        ("get", "/document-quality?document_type=technical_specification"),
        ("get", "/document-quality/health"),
        ("get", "/document-quality/runs"),
        ("get", "/document-quality/jobs"),
        ("post", "/document-quality/runs"),
        ("get", "/document-quality/runs/run-1"),
        ("get", "/document-quality/campaigns"),
        ("get", "/document-quality/campaigns/campaign-1"),
        ("get", "/document-quality/models"),
        ("get", "/document-quality/runs/run-1/diagnostics"),
        ("get", "/document-quality/campaigns/campaign-1/diagnostics"),
    ],
)
@pytest.mark.parametrize(
    "user,expected_status,expected_message",
    [
        (SimpleNamespace(is_superuser=False, is_active=ActiveEnum.ACTIVE.value), 403, "Not admin"),
        (SimpleNamespace(is_superuser=True, is_active=ActiveEnum.INACTIVE.value), 403, "User admin@example.com inactive"),
        (None, 404, "User 'admin@example.com' not found"),
    ],
)
def test_quality_routes_require_admin(client, monkeypatch, method, path, user, expected_status, expected_message):
    send = getattr(client, method)
    url = "/api/v1/admin" + path
    assert send(url).status_code == 401
    monkeypatch.setattr(auth.UserService, "filter_by_id", lambda _id: user)
    response = send(url, headers={"Authorization": "test"})
    assert response.status_code == expected_status
    assert response.json == {"code": expected_status, "message": expected_message, "data": None}


def test_version_route_uses_admin_authorization_handler(client, monkeypatch):
    monkeypatch.setattr(auth.UserService, "filter_by_id", lambda _id: SimpleNamespace(is_superuser=False, is_active=ActiveEnum.ACTIVE.value))
    response = client.get("/api/v1/admin/version", headers={"Authorization": "test"})
    assert response.status_code == 403
    assert response.json == {"code": 403, "message": "Not admin", "data": None}


@pytest.mark.parametrize("collection,kind,lookup", [("runs", "run", "get_quality_run"), ("campaigns", "campaign", "get_quality_campaign")])
def test_admin_downloads_private_evidence_archive(client, monkeypatch, tmp_path, collection, kind, lookup):
    import io
    from zipfile import ZipFile
    from document_quality_evidence import record_quality_evidence

    monkeypatch.setenv("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setattr(routes, lookup, lambda _: {"id": "execution"})
    url = f"/api/v1/admin/document-quality/{collection}/execution/diagnostics"
    headers = {"Authorization": "test"}
    assert client.get(url, headers=headers).status_code == 404
    with record_quality_evidence(kind, "execution", {}) as directory:
        (directory / "report.calls.jsonl").write_text("private response", encoding="utf-8")
    response = client.get(url, headers=headers)
    assert response.status_code == 200 and response.mimetype == "application/zip"
    assert response.headers["Cache-Control"] == "no-store"
    with ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.read("report.calls.jsonl") == b"private response"
    response.close()
    monkeypatch.setattr(routes, lookup, lambda _: None)
    assert client.get(url, headers=headers).status_code == 404


def test_archive_io_failure_does_not_expose_details(client, monkeypatch):
    monkeypatch.setattr(routes, "get_quality_run", lambda _: {"id": "execution"})

    def unavailable(*_):
        raise OSError("private filesystem detail")

    monkeypatch.setattr(routes, "evidence_archive", unavailable)
    response = client.get("/api/v1/admin/document-quality/runs/execution/diagnostics", headers={"Authorization": "test"})
    assert response.status_code == 503
    assert b"private filesystem detail" not in response.data


def test_manual_route_validates_body_before_queueing(client, monkeypatch):
    enqueue = Mock(return_value={"id": "run-1", "status": "PENDING"})
    monkeypatch.setattr(routes, "enqueue_quality_run", enqueue)
    url = "/api/v1/admin/document-quality/runs"
    headers = {"Authorization": "test"}
    for payload in ([], {"model": {"api_base": "http://foreign"}}, {"scope": "CASE", "case_id": ["M04"]}, {"scope": "PARTIAL"}):
        assert client.post(url, json=payload, headers=headers).status_code == 400
    assert client.post(url, data="{broken", content_type="application/json", headers=headers).status_code == 400
    assert enqueue.call_count == 0
    result = client.post(url, json={"model": "qwen", "scope": "CASE", "case_id": "M04"}, headers=headers)
    assert result.status_code == 200
    enqueue.assert_called_once_with("MANUAL", requested_by="admin", model="qwen", scope="CASE", case_id="M04")


def test_invalid_operational_window_and_offset_are_rejected(client):
    headers = {"Authorization": "test"}
    for path in ("/document-quality?days=bad", "/document-quality/jobs?days=bad", "/document-quality/jobs?days=2", "/document-quality/jobs?offset=-1"):
        assert client.get("/api/v1/admin" + path, headers=headers).status_code == 400


def test_technical_operations_dispatch_does_not_read_business_jobs(client, monkeypatch):
    technical = Mock(return_value={"document_type": "technical_specification", "scope": "RECORDED_ALIGNMENT_ATTEMPTS"})
    monkeypatch.setattr(routes, "alignment_operations_dashboard", technical)
    business = Mock(side_effect=AssertionError("Unrelated business jobs must not be read"))
    monkeypatch.setattr(routes, "document_quality_dashboard", business)
    headers = {"Authorization": "test"}
    result = client.get("/api/v1/admin/document-quality?document_type=technical_specification&days=30", headers=headers)
    assert result.status_code == 200 and result.json["data"]["document_type"] == "technical_specification"
    technical.assert_called_once_with(days=30)
    assert not business.called
    rejected = client.get("/api/v1/admin/document-quality?document_type=unknown", headers=headers)
    assert rejected.status_code == rejected.json["code"] == 400
    assert rejected.json["message"] == "Unknown document quality product"


def test_watchdog_is_product_scoped_and_rejects_unknown_product(client, monkeypatch):
    inspect = Mock(return_value={"status": "ALERT", "reason_code": "NIGHTLY_MISSING"})
    monkeypatch.setattr(routes, "schedule_health", inspect)
    headers = {"Authorization": "test"}
    url = "/api/v1/admin/document-quality/health"
    response = client.get(url + "?document_type=technical_specification", headers=headers)
    assert response.status_code == 200 and response.json["data"]["reason_code"] == "NIGHTLY_MISSING"
    inspect.assert_called_once_with(document_type="technical_specification")
    assert client.get(url + "?document_type=unknown", headers=headers).status_code == 400


def test_maintenance_reports_unconfigured_and_corrupt_state(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "__file__", str(tmp_path / "admin/server/routes.py"))
    url = "/api/v1/admin/maintenance"
    headers = {"Authorization": "test"}
    assert client.get(url, headers=headers).json["data"] == {"configured": False}
    report = tmp_path / "logs/maintenance/status.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"schema_version": 1, "configured": true}', encoding="utf-8")
    assert client.get(url, headers=headers).json["data"]["configured"] is True
    for value in ("[]", "{broken", '"' + "a" * (256 * 1024) + '"'):
        report.write_text(value, encoding="utf-8")
        assert client.get(url, headers=headers).status_code == 503
