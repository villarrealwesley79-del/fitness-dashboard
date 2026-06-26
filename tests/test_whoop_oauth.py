from __future__ import annotations

import importlib
import fcntl
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
    module.WHOOP_SYNC_LOCK_FILE = str(tmp_path / "whoop-sync.lock")
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


def test_whoop_callback_redirects_browser_navigation_after_success(fitness_app, monkeypatch):
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
    state = urllib.parse.parse_qs(
        urllib.parse.urlparse(client.post("/api/whoop/connect/start").get_json()["authorization_url"]).query
    )["state"][0]

    response = client.get(
        f"/api/whoop/callback?state={state}&code=server-code",
        headers={"Accept": "text/html,application/xhtml+xml"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/#settings"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "stored-access" not in response.get_data(as_text=True)


def test_whoop_callback_rejects_partial_token_payload(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_whoop_redirect_uri", lambda: "http://localhost/api/whoop/callback")
    monkeypatch.setattr(fitness_app, "_whoop_config_for_redirect", lambda redirect_uri: _config())
    monkeypatch.setattr(
        fitness_app,
        "exchange_whoop_code",
        lambda config, code: {"access_token": "stored-access", "expires_in": 3600},
    )

    client = fitness_app.app.test_client()
    state = urllib.parse.parse_qs(
        urllib.parse.urlparse(client.post("/api/whoop/connect/start").get_json()["authorization_url"]).query
    )["state"][0]

    response = client.get(f"/api/whoop/callback?state={state}&code=server-code")

    assert response.status_code == 502
    payload = response.get_json()
    assert payload["error"]["code"] == "invalid_whoop_token_payload"
    assert fitness_app.get_whoop_connection_status(fitness_app.WHOOP_DB_FILE)["status"] == "disconnected"
    assert "stored-access" not in response.get_data(as_text=True)


def test_whoop_callback_rejects_invalid_state(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_whoop_config_for_redirect", lambda redirect_uri: _config())

    response = fitness_app.app.test_client().get("/api/whoop/callback?state=badstate&code=server-code")

    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.get_json()
    assert payload["error"]["code"] == "invalid_state"


def test_whoop_callback_rejects_cross_process_lock(fitness_app, monkeypatch, tmp_path):
    monkeypatch.setattr(fitness_app, "_whoop_redirect_uri", lambda: "http://localhost/api/whoop/callback")
    monkeypatch.setattr(fitness_app, "_whoop_config_for_redirect", lambda redirect_uri: _config())
    monkeypatch.setattr(
        fitness_app,
        "exchange_whoop_code",
        lambda config, code: {
            "access_token": "stored-access",
            "refresh_token": "stored-refresh",
            "expires_in": 3600,
        },
    )
    lock_path = tmp_path / "held-whoop-callback.lock"
    fitness_app.WHOOP_SYNC_LOCK_FILE = str(lock_path)
    client = fitness_app.app.test_client()
    state = urllib.parse.parse_qs(
        urllib.parse.urlparse(client.post("/api/whoop/connect/start").get_json()["authorization_url"]).query
    )["state"][0]

    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            response = client.get(f"/api/whoop/callback?state={state}&code=server-code")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "whoop_sync_in_progress"
    assert fitness_app.get_whoop_connection_status(fitness_app.WHOOP_DB_FILE)["status"] == "disconnected"


def test_whoop_disconnect_invalidates_pending_oauth_state(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_whoop_redirect_uri", lambda: "http://localhost/api/whoop/callback")
    monkeypatch.setattr(fitness_app, "_whoop_config_for_redirect", lambda redirect_uri: _config())
    monkeypatch.setattr(
        fitness_app,
        "exchange_whoop_code",
        lambda config, code: {
            "access_token": "stored-access",
            "refresh_token": "stored-refresh",
            "expires_in": 3600,
        },
    )
    client = fitness_app.app.test_client()
    state = urllib.parse.parse_qs(
        urllib.parse.urlparse(client.post("/api/whoop/connect/start").get_json()["authorization_url"]).query
    )["state"][0]

    disconnect = client.post("/api/whoop/disconnect")
    callback = client.get(f"/api/whoop/callback?state={state}&code=server-code")

    assert disconnect.status_code == 200
    assert callback.status_code == 400
    assert callback.get_json()["error"]["code"] == "invalid_state"
    assert fitness_app.get_whoop_connection_status(fitness_app.WHOOP_DB_FILE)["status"] == "disconnected"


def test_whoop_disconnect_clears_connection(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_whoop_config_or_none", lambda: _config())
    monkeypatch.setattr(fitness_app, "_whoop_config_for_redirect", lambda redirect_uri: _config())
    revoked = []
    monkeypatch.setattr(
        fitness_app,
        "revoke_whoop_access",
        lambda config, *, session_value: revoked.append(session_value),
    )
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
    assert payload["revocation"]["status"] == "revoked"
    assert revoked == ["stored-access"]
    assert "stored-access" not in response.get_data(as_text=True)


def test_whoop_disconnect_rejects_cross_process_lock(fitness_app, tmp_path):
    lock_path = tmp_path / "held-whoop-disconnect.lock"
    fitness_app.WHOOP_SYNC_LOCK_FILE = str(lock_path)
    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            response = fitness_app.app.test_client().post("/api/whoop/disconnect")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "whoop_sync_in_progress"


def test_whoop_disconnect_purges_local_tokens_when_revoke_fails(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_whoop_config_for_redirect", lambda redirect_uri: _config())

    def fail_revoke(config, *, session_value):
        raise fitness_app.WhoopApiError("revoke failed access_token=secret", status_code=500, retryable=True)

    monkeypatch.setattr(fitness_app, "revoke_whoop_access", fail_revoke)
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
    assert payload["revocation"]["status"] == "failed"
    assert "stored-access" not in response.get_data(as_text=True)
    assert fitness_app.load_connection_token_material(fitness_app.WHOOP_DB_FILE) == {}


def test_whoop_disconnect_reports_local_token_delete_failure(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "disconnect_whoop",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("locked token file")),
    )

    response = fitness_app.app.test_client().post("/api/whoop/disconnect")

    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == "whoop_disconnect_failed"
