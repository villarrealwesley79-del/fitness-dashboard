import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit32-contract-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit32-contract-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True)
    yield module.app
    module.app.config.update(LOGIN_DISABLED=False)


def test_setup_url_uses_configured_public_base_url(fitness_app, monkeypatch):
    monkeypatch.setenv("FITNESS_DASHBOARD_PUBLIC_BASE_URL", "https://fitness.example.test")
    monkeypatch.delenv("APPLE_HEALTH_WEBHOOK_URL", raising=False)
    fitness_app.config.update(LOGIN_DISABLED=True)

    response = fitness_app.test_client().get("/api/apple-health/sync/setup-url")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {
        "webhook_url": "https://fitness.example.test/api/apple-health/sync",
        "has_token": True,
    }


def test_setup_url_uses_explicit_webhook_url(fitness_app, monkeypatch):
    monkeypatch.setenv("FITNESS_DASHBOARD_PUBLIC_BASE_URL", "https://fitness.example.test")
    monkeypatch.setenv(
        "APPLE_HEALTH_WEBHOOK_URL",
        "https://hooks.example.test/health?source=hae&token=legacy-token&secret=fit32-contract-token&auth=Bearer%20fit32-contract-token",
    )
    fitness_app.config.update(LOGIN_DISABLED=True)

    response = fitness_app.test_client().get("/api/apple-health/sync/setup-url")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["webhook_url"] == "https://hooks.example.test/health?source=hae"
    assert "legacy-token" not in payload["webhook_url"]
    assert "fit32-contract-token" not in payload["webhook_url"]
    assert payload["has_token"] is True


def test_setup_url_falls_back_when_encoded_token_remains(fitness_app, monkeypatch):
    monkeypatch.setenv("FITNESS_DASHBOARD_PUBLIC_BASE_URL", "https://fitness.example.test")
    monkeypatch.setenv(
        "APPLE_HEALTH_WEBHOOK_URL",
        "https://hooks.example.test/%66it32-contract-token/health?source=hae",
    )
    fitness_app.config.update(LOGIN_DISABLED=True)

    response = fitness_app.test_client().get("/api/apple-health/sync/setup-url")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["webhook_url"] == "https://fitness.example.test/api/apple-health/sync"
    assert "fit32-contract-token" not in payload["webhook_url"]
    assert "%66it32-contract-token" not in payload["webhook_url"]


def test_setup_url_falls_back_when_recursively_encoded_token_remains(fitness_app, monkeypatch):
    monkeypatch.setenv("FITNESS_DASHBOARD_PUBLIC_BASE_URL", "https://fitness.example.test")
    monkeypatch.setenv(
        "APPLE_HEALTH_WEBHOOK_URL",
        "https://hooks.example.test/%2566it32-contract-token/health?source=hae",
    )
    fitness_app.config.update(LOGIN_DISABLED=True)

    response = fitness_app.test_client().get("/api/apple-health/sync/setup-url")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["webhook_url"] == "https://fitness.example.test/api/apple-health/sync"
    assert "%2566it32-contract-token" not in payload["webhook_url"]


def test_sync_endpoint_rejects_missing_and_invalid_token(fitness_app, monkeypatch):
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit32-contract-token")

    client = fitness_app.test_client()
    missing = client.post("/api/apple-health/sync", json={"steps": []})
    invalid = client.post("/api/apple-health/sync?token=wrong", json={"steps": []})
    query_only = client.post("/api/apple-health/sync?token=fit32-contract-token", json={"steps": []})
    valid = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit32-contract-token"},
        json={"steps": []},
    )

    assert missing.status_code == 401
    assert missing.get_json()["error"] == "invalid or missing sync token"
    assert invalid.status_code == 401
    assert invalid.get_json()["error"] == "invalid or missing sync token"
    assert query_only.status_code == 401
    assert query_only.get_json()["error"] == "invalid or missing sync token"
    assert valid.status_code == 200


def test_sync_status_remains_auth_gated(fitness_app):
    fitness_app.config.update(LOGIN_DISABLED=False)

    response = fitness_app.test_client().get(
        "/api/apple-health/sync/status",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "Unauthorized"


def test_sync_status_reports_setup_separately_from_export_data(fitness_app, monkeypatch):
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit32-contract-token")
    fitness_app.config.update(LOGIN_DISABLED=True)

    response = fitness_app.test_client().get("/api/apple-health/sync/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["setup_configured"] is True
    assert payload["has_token"] is True
    assert "total_records" in payload
