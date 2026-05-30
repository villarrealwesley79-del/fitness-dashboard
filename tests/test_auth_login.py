from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask


def _make_auth_app(tmp_path, monkeypatch):
    import auth

    auth_db = tmp_path / "auth.db"
    monkeypatch.setattr(auth, "AUTH_DB", str(auth_db))
    monkeypatch.setenv("SECRET_KEY", "fit185-auth-test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("FITNESS_DASHBOARD_SINGLE_USER", "true")
    monkeypatch.delenv("FITNESS_DASHBOARD_OWNER_USER_ID", raising=False)
    auth._rate_fail_log.clear()

    templates = Path(__file__).resolve().parents[1] / "templates"
    app = Flask(__name__, template_folder=str(templates))

    @app.route("/")
    def index():
        return "dashboard"

    @app.route("/protected")
    def protected():
        return "protected"

    auth.init_auth(app)
    return app, auth


def test_owner_correct_password_authenticates(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password", email="owner@example.test")

    user = auth.User.authenticate("Wesley1226", "existing-password")

    assert user is not None
    assert user.username == "Wesley1226"
    assert user.is_pro is False


def test_wrong_password_returns_none_and_records_rate_limit_on_login(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")

    assert auth.User.authenticate("Wesley1226", "wrong-password") is None

    response = app.test_client().post(
        "/login",
        data={"username": "Wesley1226", "password": "wrong-password"},
        environ_base={"REMOTE_ADDR": "198.51.100.10"},
    )

    assert response.status_code == 200
    assert b"Invalid username or password." in response.data
    assert len(auth._rate_fail_log["198.51.100.10"]) == 1


def test_login_success_sets_session_and_reaches_protected_route(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")
    client = app.test_client()

    response = client.post(
        "/login",
        data={"username": "Wesley1226", "password": "existing-password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "Set-Cookie" in response.headers
    assert client.get("/protected").status_code == 200


def test_login_db_unavailable_returns_503_not_invalid_credentials(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)

    def fail_authenticate(_username, _password):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(auth.User, "authenticate", fail_authenticate)

    response = app.test_client().post(
        "/login",
        data={"username": "Wesley1226", "password": "existing-password"},
        environ_base={"REMOTE_ADDR": "198.51.100.11"},
    )

    assert response.status_code == 503
    assert b"Login service temporarily unavailable." in response.data
    assert b"Invalid username or password." not in response.data
    assert auth._rate_fail_log["198.51.100.11"] == []
