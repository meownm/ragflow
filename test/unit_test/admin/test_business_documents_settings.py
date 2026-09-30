"""HTTP contracts with real Flask routes/auth and isolated settings storage."""

import json
import sys
from io import BytesIO
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
from api.db.services import business_document_settings_service as service
from business_documents.domain.catalog_import import CatalogImportError
from common.constants import ActiveEnum


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    manager = LoginManager(app)

    class User(UserMixin):
        id = "test-admin"
        email = "test@example.com"

    @manager.request_loader
    def load_user(request):
        return User() if request.headers.get("Authorization") else None

    @app.errorhandler(AdminException)
    def admin_error(error):
        return {"code": error.code, "message": error.message}, error.code

    monkeypatch.setattr(auth.UserService, "filter_by_id", lambda _id: SimpleNamespace(is_superuser=True, is_active=ActiveEnum.ACTIVE.value))
    values = {}
    monkeypatch.setattr(service.SystemSettingsService, "get_by_name", lambda name: [SimpleNamespace(value=values[name])] if name in values else [])
    # Exercise the route envelope/validation without touching a shared database.
    monkeypatch.setattr(routes.SettingsMgr, "update_by_name", lambda name, value: values.__setitem__(name, value))
    monkeypatch.setenv("RAGFLOW_CREDENTIALS_KEY", "test-settings-key")
    monkeypatch.setattr(service.Connector, "get_or_none", Mock(side_effect=AssertionError("No connector required")))
    app.register_blueprint(routes.admin_bp)
    return app, values


def test_admin_saves_reads_and_disables_standalone_settings(app):
    application, values = app
    client = application.test_client()
    headers = {"Authorization": "test"}
    payload = {"eva_connection": {"api_base_url": "https://eva.example", "project_id": "CmfProject:docs", "eva_api_token": "secret-value"}}
    response = client.put("/api/v1/admin/business-documents", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json["data"]["eva_connection"]["token_configured"]
    assert "secret-value" not in response.text
    assert "secret-value" not in repr(values)
    assert list(values) == [service.BUSINESS_DOCUMENTS_EVA_CONNECTION_SETTING]
    response = client.get("/api/v1/admin/business-documents", headers=headers)
    assert response.json["data"]["eva_connection"]["project_id"] == "CmfProject:docs"
    assert "secret-value" not in response.text
    response = client.put("/api/v1/admin/business-documents", json={"eva_connection": None}, headers=headers)
    assert response.status_code == 200
    assert json.loads(values[service.BUSINESS_DOCUMENTS_EVA_CONNECTION_SETTING]) == {}


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/business-documents"),
        ("put", "/business-documents"),
        ("post", "/business-documents/eva-spaces"),
        ("get", "/business-documents/catalog"),
        ("post", "/business-documents/catalog"),
    ],
)
def test_connection_routes_require_login_and_admin(app, monkeypatch, method, path):
    application, values = app
    client = application.test_client()
    send = getattr(client, method)
    assert send("/api/v1/admin" + path).status_code == 401
    monkeypatch.setattr(auth.UserService, "filter_by_id", lambda _id: SimpleNamespace(is_superuser=False, is_active=ActiveEnum.ACTIVE.value))
    assert send("/api/v1/admin" + path, headers={"Authorization": "test"}).status_code == 403
    assert not values


def test_invalid_input_does_not_write_and_discovery_does_not_save(app, monkeypatch):
    application, values = app
    client = application.test_client()
    headers = {"Authorization": "test"}
    for payload in ({}, {"eva_connection": []}, {"eva_connection": {"api_base_url": "file:///etc"}}):
        assert client.put("/api/v1/admin/business-documents", json=payload, headers=headers).status_code == 400
    discovery = Mock(return_value=[{"id": "CmfProject:docs", "name": "Documents"}])
    monkeypatch.setattr(routes, "discover_business_documents_eva_spaces", discovery)
    response = client.post("/api/v1/admin/business-documents/eva-spaces", json={"eva_connection": {"api_base_url": "https://eva.example"}}, headers=headers)
    assert response.status_code == 200
    assert response.json["data"]["items"][0]["name"] == "Documents"
    assert not values


def test_admin_reads_and_uploads_catalog(app, monkeypatch):
    application, _values = app
    client = application.test_client()
    headers = {"Authorization": "test"}
    status = {
        "source_id": "BCM_Bank",
        "source_version": "v24",
        "source_sha256": "a" * 64,
        "filename": "BCM_v24.json",
        "active_items": 70,
    }
    get_status = Mock(return_value=status)
    imported = Mock(return_value={**status, "source_version": "v25", "active_items": 72})
    monkeypatch.setattr(routes, "get_business_document_catalog_status", get_status)
    monkeypatch.setattr(routes, "import_business_document_catalog", imported)

    response = client.get("/api/v1/admin/business-documents/catalog", headers=headers)
    assert response.status_code == 200
    assert response.json["data"]["active_items"] == 70
    response = client.post(
        "/api/v1/admin/business-documents/catalog",
        data={"file": (BytesIO(b'{"L1": []}'), "BCM_v25.json")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.json["data"]["source_version"] == "v25"
    assert imported.call_args.kwargs == {
        "filename": "BCM_v25.json",
        "content": b'{"L1": []}',
        "actor_id": "test-admin",
    }


def test_catalog_upload_requires_a_valid_file(app, monkeypatch):
    application, _values = app
    client = application.test_client()
    headers = {"Authorization": "test"}
    imported = Mock(side_effect=CatalogImportError("L1 must be a non-empty array"))
    monkeypatch.setattr(routes, "import_business_document_catalog", imported)

    assert client.post("/api/v1/admin/business-documents/catalog", headers=headers).status_code == 400
    response = client.post(
        "/api/v1/admin/business-documents/catalog",
        data={"file": (BytesIO(b"{}"), "BCM_v25.json")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.json["message"] == "L1 must be a non-empty array"
