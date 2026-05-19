from __future__ import annotations

import base64
import importlib
import json
from pathlib import Path

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


def test_vapid_public_key_requires_auth(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit87-vapid-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=False)

    res = module.app.test_client().get("/api/push/vapid-public-key")

    assert res.status_code == 401


def test_vapid_public_key_is_stable_and_web_push_shaped(monkeypatch, tmp_path):
    key_file = tmp_path / ".vapid_keys.json"
    monkeypatch.setenv("VAPID_KEYS_FILE", str(key_file))
    module = _app(monkeypatch, tmp_path)
    client = module.app.test_client()

    first = client.get("/api/push/vapid-public-key")
    second = client.get("/api/push/vapid-public-key")

    assert first.status_code == 200
    assert second.status_code == 200
    public_key = first.get_json()["public_key"]
    assert second.get_json()["public_key"] == public_key
    raw = base64.urlsafe_b64decode(public_key + "=" * (-len(public_key) % 4))
    assert len(raw) == 65
    assert raw[0] == 4
    assert key_file.exists()
    assert "private_key_pem" in key_file.read_text()


def test_vapid_key_file_is_ignored_by_git_and_docker():
    repo_root = Path(__file__).resolve().parents[1]
    assert ".vapid_keys.json" in (repo_root / ".gitignore").read_text()
    assert ".vapid_keys.json" in (repo_root / ".dockerignore").read_text()


def test_vapid_default_key_file_uses_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("VAPID_KEYS_FILE", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    vapid_keys = importlib.import_module("vapid_keys")

    public_key = vapid_keys.get_vapid_public_key()
    key_file = tmp_path / "data" / ".vapid_keys.json"

    assert key_file.exists()
    assert json.loads(key_file.read_text())["public_key"] == public_key


def test_vapid_concurrent_first_write_reads_existing_complete_file(monkeypatch, tmp_path):
    key_file = tmp_path / ".vapid_keys.json"
    existing_payload = {
        "private_key_pem": "already-written",
        "public_key": "existing-public-key",
        "created_at": "2026-05-19T00:00:00",
    }
    monkeypatch.setenv("VAPID_KEYS_FILE", str(key_file))
    vapid_keys = importlib.import_module("vapid_keys")

    def competing_link(_temp_path, target_path):
        Path(target_path).write_text(json.dumps(existing_payload), encoding="utf-8")
        raise FileExistsError

    monkeypatch.setattr(vapid_keys.os, "link", competing_link)

    assert vapid_keys.get_vapid_public_key() == "existing-public-key"
    assert not list(tmp_path.glob(".*.tmp"))


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
