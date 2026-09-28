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
from api.common.exceptions import AdminException
from common.constants import ActiveEnum


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    manager = LoginManager(app)

    class User(UserMixin):
        id = "admin"

    @manager.request_loader
    def load_user(request):
        return User() if request.headers.get("Authorization") else None

    @app.errorhandler(AdminException)
    def admin_error(error):
        return {"code": error.code, "message": error.message}, error.code

    monkeypatch.setattr(auth.UserService, "filter_by_id", lambda _id: SimpleNamespace(is_superuser=True, is_active=ActiveEnum.ACTIVE.value))
    app.register_blueprint(routes.admin_bp)
    return app.test_client()


@pytest.mark.parametrize("method,path", [
    ("get", "/document-quality"), ("get", "/document-quality/runs"),
    ("get", "/document-quality/jobs"),
    ("post", "/document-quality/runs"), ("get", "/document-quality/runs/run-1"),
    ("get", "/document-quality/campaigns"), ("get", "/document-quality/campaigns/campaign-1"),
    ("get", "/document-quality/models"),
])
def test_quality_routes_require_admin(client, monkeypatch, method, path):
    send = getattr(client, method)
    url = "/api/v1/admin" + path
    assert send(url).status_code == 401
    monkeypatch.setattr(auth.UserService, "filter_by_id", lambda _id: SimpleNamespace(is_superuser=False, is_active=ActiveEnum.ACTIVE.value))
    assert send(url, headers={"Authorization": "test"}).status_code == 403


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
    for path in ("/document-quality?days=bad", "/document-quality/jobs?days=bad",
                 "/document-quality/jobs?days=2", "/document-quality/jobs?offset=-1"):
        assert client.get("/api/v1/admin" + path, headers=headers).status_code == 400
