from __future__ import annotations

import importlib
import urllib.parse

import pytest

import whoop_client


@pytest.fixture()
def fitness_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "whoop-oauth-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "whoop-oauth-health-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    module.WHOOP_DB_FILE = str(tmp_path / "whoop.sqlite3")
    module.init_whoop_db(module.WHOOP_DB_FILE)
    return module


def _config():
    return whoop_client.WhoopConfig("client-id", "keychain-placeholder", "http://localhost/api/whoop/callback")


def test_whoop_status_missing_config(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_whoop_config_or_none", lambda: None)

    response = fitness_app.app.test_client().get("/api/whoop/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "missing_config"
    assert "secret" not in response.get_data(as_text=True)


def test_whoop_connect_start_and_callback_store_connection(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_whoop_redirect_uri", lambda: "http://localhost/api/whoop/callback")
    monkeypatch.setattr(fitness_app, "_whoop_config_for_redirect", lambda redirect_uri: _config())
    monkeypatch.setattr(
        fitness_app,
        "exchange_whoop_code",
        lambda config, code: {
            "access_token": "stored-access",
            "refresh_token": "stored-refresh",
            "expires_in": 3600,
            "scope": "offline read:recovery",
        },
    )

    client = fitness_app.app.test_client()
    start = client.post("/api/whoop/connect/start")

    assert start.status_code == 200
    start_payload = start.get_json()
    assert "authorization_url" in start_payload
    assert "stored-access" not in start.get_data(as_text=True)
    parsed = urllib.parse.urlparse(start_payload["authorization_url"])
    state = urllib.parse.parse_qs(parsed.query)["state"][0]
    assert len(state) >= 32
    assert start.headers["Cache-Control"] == "no-store"

    callback = client.get(f"/api/whoop/callback?state={state}&code=server-code")

    assert callback.status_code == 200
    payload = callback.get_json()
    assert payload["connection"]["status"] == "connected"
    assert callback.headers["Cache-Control"] == "no-store"
    assert "stored-access" not in callback.get_data(as_text=True)
    assert "stored-refresh" not in callback.get_data(as_text=True)


def test_whoop_callback_rejects_invalid_state(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_whoop_config_for_redirect", lambda redirect_uri: _config())

    response = fitness_app.app.test_client().get("/api/whoop/callback?state=badstate&code=server-code")

    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.get_json()
    assert payload["error"]["code"] == "invalid_state"


def test_whoop_disconnect_clears_connection(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_whoop_config_or_none", lambda: _config())
    fitness_app.save_connection_tokens(
        fitness_app.WHOOP_DB_FILE,
        {
            "access_token": "stored-access",
            "refresh_token": "stored-refresh",
            "expires_in": 3600,
        },
    )

    response = fitness_app.app.test_client().post("/api/whoop/disconnect")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["connection"]["status"] == "disconnected"
    assert "stored-access" not in response.get_data(as_text=True)
