"""Real admin routes/auth with synthetic actors and no shared account storage."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin

SERVER = Path(__file__).parents[3] / "admin" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
import auth
import routes
from common.constants import ActiveEnum

OPERATIONS = [
    ("get", "/users"),
    ("post", "/users"),
    ("delete", "/users/qa@example.test"),
    ("put", "/users/qa@example.test/password"),
    ("put", "/users/qa@example.test/activate"),
    ("put", "/users/qa@example.test/admin"),
    ("delete", "/users/qa@example.test/admin"),
]


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-qa")
    login = LoginManager(app)

    class Actor(UserMixin):
        id = "qa-admin"
        email = "self@example.test"

    @login.request_loader
    def load(request):
        return Actor() if request.headers.get("Authorization") else None

    actor = SimpleNamespace(is_superuser=True, is_active=ActiveEnum.ACTIVE.value)
    monkeypatch.setattr(auth.UserService, "filter_by_id", lambda _: actor)
    manager = Mock()
    monkeypatch.setattr(routes, "UserMgr", manager)
    app.register_blueprint(routes.admin_bp)
    return app.test_client(), actor, manager


@pytest.mark.parametrize("method,path", OPERATIONS)
@pytest.mark.parametrize("persona", ["anonymous", "reader", "inactive"])
def test_unauthorized_requests_cannot_reach_user_services(client, method, path, persona):
    http, actor, manager = client
    headers = {} if persona == "anonymous" else {"Authorization": "synthetic"}
    if persona == "reader":
        actor.is_superuser = False
    if persona == "inactive":
        actor.is_active = ActiveEnum.INACTIVE.value
    response = getattr(http, method)("/api/v1/admin" + path, data="broken-json", content_type="application/json", headers=headers)
    assert response.status_code == (401 if persona == "anonymous" else 403)
    assert not manager.mock_calls


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/users", {}),
        ("/users", []),
        ("/users", "username password"),
        ("/users", {"username": 5, "password": "encrypted"}),
        ("/users", {"username": "qa@example.test", "password": ""}),
        ("/users/qa@example.test/password", {}),
        ("/users/qa@example.test/password", {"new_password": None}),
        ("/users/qa@example.test/password", {"new_password": " "}),
        ("/users/qa@example.test/activate", {}),
        ("/users/qa@example.test/activate", {"activate_status": "other"}),
        ("/users/qa@example.test/activate", {"activate_status": True}),
    ],
)
def test_invalid_bodies_do_not_mutate_accounts(client, path, payload):
    http, _, manager = client
    response = http.open("/api/v1/admin" + path, method="POST" if path == "/users" else "PUT", json=payload, headers={"Authorization": "synthetic"})
    assert response.status_code == 400
    assert response.json["code"] == 400
    assert not manager.mock_calls


@pytest.mark.parametrize("payload", [[], [{"id": "qa", "email": "qa@example.test"}]])
def test_populated_and_empty_lists_are_success(client, payload):
    http, _, manager = client
    manager.get_all_users.return_value = payload
    response = http.get("/api/v1/admin/users", headers={"Authorization": "synthetic"})
    assert response.status_code == 200 and response.json["data"] == payload


def test_create_strips_password_and_retains_the_default_role(client):
    http, _, manager = client
    manager.create_user.return_value = {"success": True, "user_info": {"id": "qa", "password": "secret-marker"}}
    response = http.post("/api/v1/admin/users", json={"username": "qa@example.test", "password": "encrypted"}, headers={"Authorization": "synthetic"})
    assert response.status_code == 200 and response.json["data"] == {"id": "qa"}
    assert "secret-marker" not in response.text
    manager.create_user.assert_called_once_with("qa@example.test", "encrypted", "user")


@pytest.mark.parametrize(
    "method,action,payload",
    [
        ("delete", "", None),
        ("put", "/password", {"new_password": "encrypted"}),
        ("put", "/activate", {"activate_status": "on"}),
        ("put", "/activate", {"activate_status": "off"}),
        ("put", "/admin", None),
        ("delete", "/admin", None),
    ],
)
def test_service_failures_never_report_success(client, method, action, payload):
    http, _, manager = client
    for name in ["delete_user", "update_user_password", "update_user_activate_status", "grant_admin", "revoke_admin"]:
        getattr(manager, name).side_effect = RuntimeError("QA service unavailable")
    response = getattr(http, method)("/api/v1/admin/users/qa@example.test" + action, json=payload, headers={"Authorization": "synthetic"})
    assert response.status_code == 400 and response.json["code"] != 0


@pytest.mark.parametrize("method", ["put", "delete"])
def test_current_admin_cannot_grant_or_revoke_themselves(client, method):
    http, _, manager = client
    response = getattr(http, method)("/api/v1/admin/users/self@example.test/admin", headers={"Authorization": "synthetic"})
    assert response.status_code == 400 and response.json["code"] == 409
    assert not manager.mock_calls
