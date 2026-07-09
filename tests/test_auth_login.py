from __future__ import annotations

import logging
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


def test_invalid_owner_id_locks_authenticated_non_owner_and_logs_error(tmp_path, monkeypatch, caplog):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("owner", "existing-password")
    auth.User.create("member", "existing-password")
    member = auth.User.get_by_username("member")
    monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", "not-an-integer")

    client = app.test_client()
    assert client.post(
        "/login", data={"username": "member", "password": "existing-password"}
    ).status_code == 302

    with caplog.at_level(logging.ERROR, logger=auth.__name__):
        response = client.get("/protected")

    assert response.status_code == 403
    assert auth._is_owner_user_id(member.id) is False
    assert "FITNESS_DASHBOARD_OWNER_USER_ID is set but not an integer" in caplog.text


def test_unset_owner_id_uses_lowest_user_id(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("owner", "existing-password")
    auth.User.create("member", "existing-password")
    owner = auth.User.get_by_username("owner")
    member = auth.User.get_by_username("member")

    assert auth._is_owner_user_id(owner.id) is True
    assert auth._is_owner_user_id(member.id) is False


def test_valid_owner_id_allows_only_configured_user(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("first", "existing-password")
    auth.User.create("owner", "existing-password")
    first_user = auth.User.get_by_username("first")
    configured_owner = auth.User.get_by_username("owner")
    monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", str(configured_owner.id))

    assert auth._is_owner_user_id(configured_owner.id) is True
    assert auth._is_owner_user_id(first_user.id) is False


def test_no_users_remains_permissive_for_first_run_setup(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)

    assert auth._is_owner_user_id("first-run") is True


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
