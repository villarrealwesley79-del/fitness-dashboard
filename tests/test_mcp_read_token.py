"""PER-1585: the loopback service token that lets the read-only MCP server call Flask."""
import os

import pytest
from flask import Flask, jsonify
from flask_login import current_user

import auth
import runtime_config

TOKEN = "unit-test-read-token"
READ_PATHS = sorted(auth._MCP_READ_PATHS)


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def build(*, token=TOKEN, mode=0o600):
        monkeypatch.setattr(runtime_config, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "AUTH_DB", str(tmp_path / "auth.db"))
        monkeypatch.setenv("SECRET_KEY", "mcp-read-token-test-secret")
        monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
        monkeypatch.delenv("FITNESS_DASHBOARD_NO_LOGIN", raising=False)
        monkeypatch.delenv("FITNESS_DASHBOARD_OWNER_USER_ID", raising=False)
        if token is not None:
            path = tmp_path / ".mcp-read-token"
            path.write_text(token + "\n", encoding="utf-8")
            os.chmod(path, mode)

        app = Flask(__name__)
        app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)

        def who():
            return jsonify(user=current_user.username)

        for path in READ_PATHS + ["/api/export-backup", "/api/next-workout"]:
            app.add_url_rule(path, path, who, methods=["GET", "POST"])
        auth.init_auth(app)
        auth.User.create("owner", "existing-password")
        return app.test_client()

    return build


def bearer(token=TOKEN, **extra):
    return {"Authorization": f"Bearer {token}", **extra}


@pytest.mark.parametrize("path", READ_PATHS)
def test_allowlisted_get_with_token_runs_as_owner(make_client, path):
    response = make_client().get(path, headers=bearer())
    assert response.status_code == 200
    assert response.get_json() == {"user": "owner"}


def test_wrong_or_missing_token_is_401(make_client):
    client = make_client()
    assert client.get(READ_PATHS[0], headers=bearer("nope")).status_code == 401
    assert client.get(READ_PATHS[0]).status_code == 401


@pytest.mark.parametrize(
    "header",
    ["X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded", "Tailscale-User-Login"],
)
def test_proxy_headers_disable_the_token(make_client, header):
    response = make_client().get(READ_PATHS[0], headers=bearer(**{header: "203.0.113.9"}))
    assert response.status_code == 401


def test_non_loopback_peer_or_host_is_401(make_client):
    client = make_client()
    remote = client.get(READ_PATHS[0], headers=bearer(), environ_overrides={"REMOTE_ADDR": "100.64.0.9"})
    assert remote.status_code == 401
    served = client.get(READ_PATHS[0], headers=bearer(Host="frankbox.example.ts.net"))
    assert served.status_code == 401


def test_post_with_token_is_401(make_client):
    assert make_client().post(READ_PATHS[0], headers=bearer()).status_code == 401


@pytest.mark.parametrize("path", ["/api/export-backup", "/api/next-workout"])
def test_routes_outside_the_allowlist_are_401(make_client, path):
    assert make_client().get(path, headers=bearer()).status_code == 401


def test_without_token_file_the_feature_is_off(make_client):
    assert make_client(token=None).get(READ_PATHS[0], headers=bearer()).status_code == 401


def test_empty_token_file_is_off(make_client):
    assert make_client(token="").get(READ_PATHS[0], headers=bearer("anything")).status_code == 401


def test_token_file_readable_by_others_is_off(make_client):
    assert make_client(mode=0o644).get(READ_PATHS[0], headers=bearer()).status_code == 401
