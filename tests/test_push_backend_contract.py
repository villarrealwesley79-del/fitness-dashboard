from __future__ import annotations

import importlib

import data_store


def _app(monkeypatch, tmp_path):
    db_path = tmp_path / "push_contract.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    return module


def _subscription():
    return {
        "endpoint": "https://push.example.test/send/abc",
        "keys": {"p256dh": "public-key", "auth": "auth-secret"},
    }


def test_push_subscription_save_list_and_revoke_never_exposes_keys(monkeypatch, tmp_path):
    module = _app(monkeypatch, tmp_path)
    client = module.app.test_client()

    saved = client.post(
        "/api/push/subscriptions",
        json={"subscription": _subscription(), "permission_state": "granted", "pwa_installed": True},
    )
    assert saved.status_code == 200, saved.get_data(as_text=True)
    body = saved.get_json()
    summary = body["subscription"]
    assert summary["endpoint_host"] == "push.example.test"
    assert summary["keys_present"] is True
    assert "auth-secret" not in str(body)
    assert "public-key" not in str(body)

    listed = client.get("/api/push/subscriptions")
    assert listed.status_code == 200
    listed_body = listed.get_json()
    assert listed_body["subscriptions"][0]["endpoint_hash"] == summary["endpoint_hash"]
    assert "auth-secret" not in str(listed_body)

    revoked = client.delete(f"/api/push/subscriptions/{summary['endpoint_hash']}")
    assert revoked.status_code == 200
    assert revoked.get_json()["revoked"] is True
    assert client.get("/api/push/subscriptions").get_json()["subscriptions"] == []


def test_push_subscription_rejects_missing_secret_keys(monkeypatch, tmp_path):
    module = _app(monkeypatch, tmp_path)
    res = module.app.test_client().post(
        "/api/push/subscriptions",
        json={"endpoint": "https://push.example.test/send/abc", "keys": {"p256dh": "public-key"}},
    )

    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == "invalid_push_subscription"


def test_push_reminder_preview_surfaces_stale_and_pending_review(monkeypatch, tmp_path):
    module = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_compute_data_freshness", lambda now=None: {
        "oura": {"status": "stale", "last_data_point": "2026-05-17", "last_sync_attempt": "2026-05-17T07:00:00"},
        "apple_health": {"status": "fresh", "last_data_point": "2026-05-18", "last_sync_attempt": "2026-05-18T07:00:00"},
        "food": {"status": "fresh", "pending_review": True},
    })
    client = module.app.test_client()
    client.post("/api/push/subscriptions", json={"subscription": _subscription()})

    res = client.get("/api/push/reminders/preview")

    assert res.status_code == 200
    body = res.get_json()
    assert body["support_state"] == "ready"
    assert body["delivery"] == "preview_only"
    assert body["safety_critical"] is False
    assert [alert["type"] for alert in body["alerts"]] == [
        "stale_wearable_data",
        "pending_food_estimate_review",
    ]
    assert all(alert["safety_critical"] is False for alert in body["alerts"])


def test_push_reminder_preview_gracefully_degrades_without_subscription(monkeypatch, tmp_path):
    module = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_compute_data_freshness", lambda now=None: {
        "oura": {"status": "fresh"},
        "apple_health": {"status": "fresh"},
        "food": {"status": "fresh", "pending_review": False},
    })

    body = module.app.test_client().get("/api/push/reminders/preview?permission=denied").get_json()

    assert body["support_state"] == "permission_denied"
    assert body["subscription_count"] == 0
    assert body["alerts"] == []


def test_push_reminder_preview_uses_stored_subscription_degradation_state(monkeypatch, tmp_path):
    module = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_compute_data_freshness", lambda now=None: {
        "oura": {"status": "fresh"},
        "apple_health": {"status": "fresh"},
        "food": {"status": "fresh", "pending_review": False},
    })
    client = module.app.test_client()
    client.post(
        "/api/push/subscriptions",
        json={"subscription": _subscription(), "permission_state": "granted", "pwa_installed": False},
    )

    body = client.get("/api/push/reminders/preview").get_json()

    assert body["support_state"] == "not_installed"
