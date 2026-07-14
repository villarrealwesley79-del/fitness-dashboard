import logging
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, jsonify, render_template_string, session
from flask_login import current_user

import auth


def _make_auth_app(tmp_path, monkeypatch, *, no_login=None):
    monkeypatch.setattr(auth, "AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("SECRET_KEY", "fit386-test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("FITNESS_DASHBOARD_SINGLE_USER", "true")
    monkeypatch.delenv("FITNESS_DASHBOARD_OWNER_USER_ID", raising=False)
    if no_login is None:
        monkeypatch.delenv("FITNESS_DASHBOARD_NO_LOGIN", raising=False)
    else:
        monkeypatch.setenv("FITNESS_DASHBOARD_NO_LOGIN", no_login)
    auth._no_login_owner_error_logged = False

    templates = Path(__file__).resolve().parents[1] / "templates"
    app = Flask(__name__, template_folder=str(templates))
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)

    @app.get("/protected")
    def protected():
        return jsonify(
            user_id=current_user.get_id(),
            username=current_user.username,
            email=current_user.email,
            session_user_id=session.get("_user_id"),
        )

    @app.get("/api/protected")
    def api_protected():
        return jsonify(user_id=current_user.get_id())

    @app.get("/protected-template")
    def protected_template():
        return render_template_string("owner={{ current_user.get_id() }}")

    auth.init_auth(app)
    return app


@pytest.mark.parametrize("value", [None, "", "false", "1", "yes", "tru"])
def test_no_login_requires_explicit_true(tmp_path, monkeypatch, value):
    app = _make_auth_app(tmp_path, monkeypatch, no_login=value)
    auth.User.create("owner", "existing-password", email="owner@example.test")

    response = app.test_client().get("/protected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/protected")


@pytest.mark.parametrize("value", ["true", " TRUE ", "True"])
def test_no_login_accepts_true_case_insensitively(tmp_path, monkeypatch, value):
    app = _make_auth_app(tmp_path, monkeypatch, no_login=value)
    auth.User.create("owner", "existing-password", email="owner@example.test")

    response = app.test_client().get("/protected")

    assert response.status_code == 200


def test_enabled_mode_uses_existing_owner_without_auth_session(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password", email="owner@example.test")
    owner = auth.User.get_by_username("owner")
    client = app.test_client()

    response = client.get("/protected")

    assert response.status_code == 200
    assert response.get_json() == {
        "user_id": str(owner.id),
        "username": "owner",
        "email": "owner@example.test",
        "session_user_id": None,
    }

    rendered = client.get("/protected-template")
    assert rendered.status_code == 200
    assert rendered.get_data(as_text=True) == f"owner={owner.id}"
    assert "Set-Cookie" not in rendered.headers


def test_enabled_mode_uses_configured_owner_not_first_user(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("first", "existing-password")
    auth.User.create("owner", "existing-password", email="owner@example.test")
    owner = auth.User.get_by_username("owner")
    monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", str(owner.id))

    response = app.test_client().get("/protected")

    assert response.status_code == 200
    assert response.get_json()["user_id"] == str(owner.id)
    assert response.get_json()["email"] == "owner@example.test"


def test_enabled_mode_overrides_non_owner_session_only_while_enabled(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("owner", "existing-password")
    auth.User.create("member", "existing-password")
    member = auth.User.get_by_username("member")
    client = app.test_client()
    with client.session_transaction() as stored_session:
        stored_session["_user_id"] = str(member.id)
        stored_session["_fresh"] = True

    monkeypatch.setenv("FITNESS_DASHBOARD_NO_LOGIN", "true")
    enabled = client.get("/protected")
    monkeypatch.delenv("FITNESS_DASHBOARD_NO_LOGIN")
    disabled = client.get("/protected")

    assert enabled.status_code == 200
    assert enabled.get_json()["username"] == "owner"
    assert disabled.status_code == 403


def test_default_keeps_browser_redirect_api_401_and_non_owner_403(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("owner", "existing-password")
    auth.User.create("member", "existing-password")
    anonymous = app.test_client()

    assert anonymous.get("/protected").status_code == 302
    assert anonymous.get("/api/protected").status_code == 401

    member = app.test_client()
    member_user = auth.User.get_by_username("member")
    with member.session_transaction() as stored_session:
        stored_session["_user_id"] = str(member_user.id)
        stored_session["_fresh"] = True
    assert member.get("/protected").status_code == 403


@pytest.mark.parametrize("owner_value", [None, "not-an-integer", "999"])
def test_enabled_mode_falls_back_to_login_without_valid_owner(
    tmp_path, monkeypatch, owner_value
):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    if owner_value is not None:
        monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", owner_value)

    response = app.test_client().get("/protected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/protected")


def test_enabled_mode_falls_back_when_owner_id_lookup_hits_database_error(
    tmp_path, monkeypatch, caplog
):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")

    def fail_owner_id_lookup():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(auth, "_owner_user_id", fail_owner_id_lookup)
    with caplog.at_level(logging.ERROR, logger=auth.__name__):
        response = app.test_client().get("/protected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/protected")
    assert "normal authentication remains enabled" in caplog.text


def test_enabled_mode_falls_back_when_owner_row_lookup_hits_database_error(
    tmp_path, monkeypatch, caplog
):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")

    def fail_owner_row_lookup(_user_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(auth.User, "get_by_id", fail_owner_row_lookup)
    with caplog.at_level(logging.ERROR, logger=auth.__name__):
        response = app.test_client().get("/protected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/protected")
    assert "normal authentication remains enabled" in caplog.text


def test_database_error_does_not_reload_stored_session_user(tmp_path, monkeypatch, caplog):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")
    owner = auth.User.get_by_username("owner")
    client = app.test_client()
    with client.session_transaction() as stored_session:
        stored_session["_user_id"] = str(owner.id)
        stored_session["_fresh"] = True

    lookup_calls = 0

    def fail_owner_row_lookup(_user_id):
        nonlocal lookup_calls
        lookup_calls += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(auth.User, "get_by_id", fail_owner_row_lookup)
    with caplog.at_level(logging.ERROR, logger=auth.__name__):
        response = client.get("/protected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/protected")
    assert lookup_calls == 1


def test_validated_owner_is_not_looked_up_again_in_login_guard(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")
    owner = auth.User.get_by_username("owner")
    lookup_calls = 0

    def owner_id_once():
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls > 1:
            raise sqlite3.OperationalError("database is locked")
        return owner.id

    monkeypatch.setattr(auth, "_owner_user_id", owner_id_once)

    response = app.test_client().get("/protected")

    assert response.status_code == 200
    assert response.get_json()["user_id"] == str(owner.id)
    assert lookup_calls == 1


@pytest.mark.parametrize(
    ("base_url", "remote_addr"),
    [
        ("http://localhost:5050", "127.0.0.1"),
        ("http://127.0.0.1:5050", "127.0.0.1"),
        ("http://[::1]:5050", "::1"),
        ("http://100.90.15.93:5050", "100.90.15.93"),
    ],
)
def test_enabled_mode_accepts_only_localhost_or_tailnet_hosts(
    tmp_path, monkeypatch, base_url, remote_addr
):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")

    response = app.test_client().get(
        "/protected",
        base_url=base_url,
        environ_overrides={"REMOTE_ADDR": remote_addr},
    )

    assert response.status_code == 200
    assert response.get_json()["username"] == "owner"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://evil.example:5050",
        "http://evil.ts.net.attacker.example:5050",
        "http://192.168.1.20:5050",
    ],
)
def test_enabled_mode_rejects_untrusted_hosts(tmp_path, monkeypatch, base_url):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")
    client = app.test_client()

    browser_response = client.get("/protected", base_url=base_url)
    api_response = client.get("/api/protected", base_url=base_url)

    assert browser_response.status_code == 302
    assert browser_response.headers["Location"].endswith("/login?next=/protected")
    assert api_response.status_code == 401


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:5050",
        "http://admins-mac-mini.tail6c6490.ts.net:5050",
    ],
)
def test_enabled_mode_rejects_spoofed_trusted_host_from_untrusted_peer(
    tmp_path, monkeypatch, base_url
):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")

    response = app.test_client().get(
        "/api/protected",
        base_url=base_url,
        environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
    )

    assert response.status_code == 401


def test_enabled_mode_accepts_tailnet_peer(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")

    response = app.test_client().get(
        "/api/protected",
        base_url="http://admins-mac-mini.tail6c6490.ts.net:5050",
        environ_overrides={"REMOTE_ADDR": "100.90.15.93"},
    )

    assert response.status_code == 200


def test_enabled_mode_rejects_loopback_proxy_to_tailnet_host(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")

    response = app.test_client().get(
        "/api/protected",
        base_url="http://admins-mac-mini.tail6c6490.ts.net:5050",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 401


def test_enabled_mode_rejects_forwarded_loopback_request(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")

    response = app.test_client().get(
        "/api/protected",
        base_url="http://localhost:5050",
        headers={"X-Forwarded-For": "203.0.113.10"},
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_enabled_mode_rejects_cross_origin_reads(tmp_path, monkeypatch, headers):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")

    response = app.test_client().get(
        "/api/protected",
        base_url="http://localhost:5050",
        headers=headers,
    )

    assert response.status_code == 401


def test_enabled_mode_does_not_trust_configured_public_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("FITNESS_DASHBOARD_PUBLIC_BASE_URL", "https://public.example")
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")

    response = app.test_client().get(
        "/api/protected",
        base_url="http://localhost:5050",
        headers={
            "Origin": "https://public.example",
            "Sec-Fetch-Site": "same-site",
        },
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://localhost:5050"},
        {"Sec-Fetch-Site": "same-origin"},
    ],
)
def test_enabled_mode_accepts_same_origin_reads(tmp_path, monkeypatch, headers):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password")

    response = app.test_client().get(
        "/api/protected",
        base_url="http://localhost:5050",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["user_id"] == "1"


def test_factory_preview_does_not_enable_no_login():
    config = (Path(__file__).resolve().parents[1] / ".agents" / "factory.yaml").read_text()

    assert "FITNESS_DASHBOARD_NO_LOGIN" not in config
