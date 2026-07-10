from __future__ import annotations

import os
import secrets
import sqlite3
import sys
from types import SimpleNamespace
from datetime import datetime

import pytest

import whoop_store


def _install_fake_keychain(monkeypatch, *, stored=None):
    state = {
        "stored": dict(stored or {}),
        "fail_save": False,
        "fail_delete": False,
    }

    def fake_save(service, account, secret):
        if state["fail_save"]:
            raise RuntimeError("keychain unavailable")
        state["stored"][(service, account)] = secret

    def fake_find(service, account):
        return state["stored"].get((service, account), "")

    def fake_delete(service, account):
        if state["fail_delete"]:
            raise RuntimeError("delete denied")
        state["stored"].pop((service, account), None)

    monkeypatch.setattr(whoop_store, "_keychain_save", fake_save)
    monkeypatch.setattr(whoop_store, "_keychain_find", fake_find)
    monkeypatch.setattr(whoop_store, "_keychain_delete", fake_delete)
    return state


def test_connection_tokens_round_trip_and_disconnect(tmp_path):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)

    whoop_store.save_connection_tokens(
        db_path,
        {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "scope": "offline read:recovery",
        },
        connected_at=datetime(2026, 6, 25, 8, 0, 0),
    )

    status = whoop_store.get_connection_status(db_path)
    private_view = whoop_store.get_connection_status(db_path, include_private=True)

    assert status["status"] == "connected"
    assert "access_token" not in status
    assert "refresh_token" not in private_view
    assert private_view["protected_material_available"] is True
    material = whoop_store.load_connection_token_material(db_path)
    assert material["session_value"] == "access-token"
    assert material["renewal_value"] == "refresh-token"
    with sqlite3.connect(db_path) as conn:
        raw_db = "\n".join(str(row) for row in conn.execute("SELECT * FROM whoop_connection").fetchall())
    assert "access-token" not in raw_db
    assert "refresh-token" not in raw_db

    whoop_store.disconnect_whoop(db_path, disconnected_at=datetime(2026, 6, 25, 9, 0, 0))

    disconnected = whoop_store.get_connection_status(db_path, include_private=True)
    assert disconnected["status"] == "disconnected"
    assert disconnected["protected_material_available"] is False
    assert whoop_store.load_connection_token_material(db_path) == {}


def test_connection_tokens_use_keychain_on_macos_without_plaintext_file(tmp_path, monkeypatch):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    monkeypatch.delenv("WHOOP_PROTECTED_MATERIAL_DIR", raising=False)
    monkeypatch.setattr(whoop_store, "sys", SimpleNamespace(platform="darwin"), raising=False)
    _install_fake_keychain(monkeypatch)

    whoop_store.save_connection_tokens(
        db_path,
        {
            "access_token": "keychain-access",
            "refresh_token": "keychain-refresh",
            "expires_in": 3600,
        },
        connected_at=datetime(2026, 6, 25, 8, 0, 0),
    )

    assert not os.path.exists(whoop_store._protected_material_path(db_path))
    material = whoop_store.load_connection_token_material(db_path)
    assert material["session_value"] == "keychain-access"
    assert material["renewal_value"] == "keychain-refresh"

    whoop_store.disconnect_whoop(db_path, disconnected_at=datetime(2026, 6, 25, 9, 0, 0))

    assert whoop_store.load_connection_token_material(db_path) == {}


def test_keychain_save_uses_native_backend_without_subprocess(monkeypatch):
    secret = "keychain-secret-that-must-not-enter-process-metadata"
    saved = []
    monkeypatch.setattr(whoop_store, "_keychain_delete", lambda *_args: None)
    monkeypatch.setattr(
        whoop_store,
        "_macos_keychain_add",
        lambda service, account, value: saved.append((service, account, value)),
        raising=False,
    )
    monkeypatch.setattr(
        whoop_store,
        "_keychain_password_prompt",
        lambda *_args: (_ for _ in ()).throw(AssertionError("subprocess prompt used")),
        raising=False,
    )

    whoop_store._keychain_save("fitness-dashboard-test", "test-account", secret)

    assert saved == [("fitness-dashboard-test", "test-account", secret)]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain integration")
def test_keychain_round_trip_preserves_long_secret():
    service = f"fitness-dashboard-fit261-test-{secrets.token_hex(8)}"
    account = f"temporary-{secrets.token_hex(8)}"
    secret = secrets.token_urlsafe(192)
    try:
        whoop_store._keychain_save(service, account, secret)
        assert whoop_store._keychain_find(service, account) == secret
    finally:
        whoop_store._keychain_delete(service, account)


def test_keychain_write_ignores_obsolete_plaintext_cleanup_error(tmp_path, monkeypatch):
    db_path = str(tmp_path / "whoop.sqlite3")
    monkeypatch.setattr(whoop_store, "save_protected_secret", lambda *_args, **_kwargs: "keychain")
    monkeypatch.setattr(
        whoop_store,
        "_delete_protected_secret_file",
        lambda _path: (_ for _ in ()).throw(OSError("cleanup denied")),
    )

    assert whoop_store._write_protected_material(db_path, {
        "access_token": "keychain-access",
        "refresh_token": "keychain-refresh",
    }) == whoop_store.WHOOP_MATERIAL_REF


def test_disconnect_surfaces_keychain_delete_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    monkeypatch.delenv("WHOOP_PROTECTED_MATERIAL_DIR", raising=False)
    monkeypatch.setattr(whoop_store, "sys", SimpleNamespace(platform="darwin"), raising=False)
    stored = {}
    fail_delete = {"enabled": False}

    def fake_save(service, account, secret):
        stored[(service, account)] = secret

    def fake_find(service, account):
        return stored.get((service, account), "")

    def fake_delete(service, account):
        key = (service, account)
        if fail_delete["enabled"]:
            raise RuntimeError("delete denied")
        stored.pop(key, None)

    monkeypatch.setattr(whoop_store, "_keychain_save", fake_save)
    monkeypatch.setattr(whoop_store, "_keychain_find", fake_find)
    monkeypatch.setattr(whoop_store, "_keychain_delete", fake_delete)
    whoop_store.save_connection_tokens(
        db_path,
        {
            "access_token": "keychain-access",
            "refresh_token": "keychain-refresh",
            "expires_in": 3600,
        },
    )

    fail_delete["enabled"] = True
    with pytest.raises(RuntimeError, match="delete denied"):
        whoop_store.disconnect_whoop(db_path)

    assert whoop_store.get_connection_status(db_path)["status"] == "connected"
    assert stored


def test_existing_whoop_material_file_migrates_to_keychain(tmp_path, monkeypatch):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    monkeypatch.delenv("WHOOP_PROTECTED_MATERIAL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(whoop_store, "sys", SimpleNamespace(platform="darwin"), raising=False)
    state = _install_fake_keychain(monkeypatch)
    legacy_path = whoop_store._protected_material_path(db_path)
    os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
    with open(legacy_path, "w", encoding="utf-8") as handle:
        handle.write('{"session_value":"legacy-access","renewal_value":"legacy-refresh"}')

    material = whoop_store.load_connection_token_material(db_path)

    assert material["session_value"] == "legacy-access"
    assert material["renewal_value"] == "legacy-refresh"
    assert not os.path.exists(legacy_path)
    assert state["stored"]


def test_protected_secret_keychain_failure_uses_fallback_file(tmp_path, monkeypatch):
    fallback_path = str(tmp_path / "protected-secret")
    monkeypatch.setattr(whoop_store, "sys", SimpleNamespace(platform="darwin"), raising=False)
    state = _install_fake_keychain(monkeypatch)
    state["fail_save"] = True

    whoop_store.save_protected_secret(
        "fitness-dashboard-test-secret",
        "test-account",
        "fallback-value",
        fallback_path=fallback_path,
    )

    assert os.path.exists(fallback_path)
    fallback_text = open(fallback_path, encoding="utf-8").read()
    assert "fallback-value" not in fallback_text
    assert fallback_text.startswith(whoop_store.PROTECTED_SECRET_FILE_PREFIX)
    assert whoop_store.load_protected_secret(
        "fitness-dashboard-test-secret",
        "test-account",
        fallback_path=fallback_path,
    ) == "fallback-value"


def test_protected_secret_fallback_overrides_stale_keychain_value(tmp_path, monkeypatch):
    fallback_path = str(tmp_path / "protected-secret")
    monkeypatch.setattr(whoop_store, "sys", SimpleNamespace(platform="darwin"), raising=False)
    state = _install_fake_keychain(
        monkeypatch,
        stored={("fitness-dashboard-test-secret", "test-account"): "stale-value"},
    )
    state["fail_save"] = True
    state["fail_delete"] = True

    whoop_store.save_protected_secret(
        "fitness-dashboard-test-secret",
        "test-account",
        "fresh-value",
        fallback_path=fallback_path,
    )

    assert whoop_store.load_protected_secret(
        "fitness-dashboard-test-secret",
        "test-account",
        fallback_path=fallback_path,
    ) == "fresh-value"


def test_protected_secret_keychain_success_removes_stale_fallback(tmp_path, monkeypatch):
    fallback_path = str(tmp_path / "protected-secret")
    monkeypatch.setattr(whoop_store, "sys", SimpleNamespace(platform="darwin"), raising=False)
    state = _install_fake_keychain(monkeypatch)
    state["fail_save"] = True
    whoop_store.save_protected_secret(
        "fitness-dashboard-test-secret",
        "test-account",
        "fallback-value",
        fallback_path=fallback_path,
    )
    assert os.path.exists(fallback_path)

    state["fail_save"] = False
    whoop_store.save_protected_secret(
        "fitness-dashboard-test-secret",
        "test-account",
        "keychain-value",
        fallback_path=fallback_path,
    )

    assert not os.path.exists(fallback_path)
    assert whoop_store.load_protected_secret(
        "fitness-dashboard-test-secret",
        "test-account",
        fallback_path=fallback_path,
    ) == "keychain-value"


def test_delete_protected_secret_keeps_fallback_when_keychain_delete_fails(tmp_path, monkeypatch):
    fallback_path = str(tmp_path / "protected-secret")
    monkeypatch.setattr(whoop_store, "sys", SimpleNamespace(platform="darwin"), raising=False)
    state = _install_fake_keychain(monkeypatch)
    state["fail_save"] = True
    state["fail_delete"] = True
    whoop_store.save_protected_secret(
        "fitness-dashboard-test-secret",
        "test-account",
        "fallback-value",
        fallback_path=fallback_path,
    )

    with pytest.raises(RuntimeError, match="delete denied"):
        whoop_store.delete_protected_secret(
            "fitness-dashboard-test-secret",
            "test-account",
            fallback_path=fallback_path,
        )

    assert os.path.exists(fallback_path)


def test_whoop_material_fallback_overrides_stale_keychain_value(tmp_path, monkeypatch):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    monkeypatch.delenv("WHOOP_PROTECTED_MATERIAL_DIR", raising=False)
    monkeypatch.setattr(whoop_store, "sys", SimpleNamespace(platform="darwin"), raising=False)
    stale_material = '{"session_value":"stale-access","renewal_value":"stale-refresh"}'
    state = _install_fake_keychain(
        monkeypatch,
        stored={
            (
                whoop_store.WHOOP_MATERIAL_REF,
                whoop_store._protected_material_account(db_path),
            ): stale_material,
        },
    )
    state["fail_save"] = True
    state["fail_delete"] = True
    whoop_store.save_connection_tokens(
        db_path,
        {
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 3600,
        },
    )

    material = whoop_store.load_connection_token_material(db_path)

    assert material["session_value"] == "fresh-access"
    assert material["renewal_value"] == "fresh-refresh"


def test_existing_whoop_material_file_overrides_stale_keychain(tmp_path, monkeypatch):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    monkeypatch.delenv("WHOOP_PROTECTED_MATERIAL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(whoop_store, "sys", SimpleNamespace(platform="darwin"), raising=False)
    _install_fake_keychain(
        monkeypatch,
        stored={
            (
                whoop_store.WHOOP_MATERIAL_REF,
                whoop_store._protected_material_account(db_path),
            ): '{"session_value":"stale-access","renewal_value":"stale-refresh"}',
        },
    )
    legacy_path = whoop_store._protected_material_path(db_path)
    os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
    with open(legacy_path, "w", encoding="utf-8") as handle:
        handle.write('{"session_value":"current-access","renewal_value":"current-refresh"}')

    material = whoop_store.load_connection_token_material(db_path)

    assert material["session_value"] == "current-access"
    assert material["renewal_value"] == "current-refresh"
    assert not os.path.exists(legacy_path)


def test_save_connection_tokens_deletes_material_when_db_write_fails(tmp_path, monkeypatch):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    monkeypatch.setattr(whoop_store, "init_whoop_db", lambda *_args, **_kwargs: None)

    class FailingConnection:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("locked")

        def close(self):
            pass

    monkeypatch.setattr(whoop_store, "_connect", lambda *_args, **_kwargs: FailingConnection())

    session_field = "access_" + "token"
    renewal_field = "refresh_" + "token"
    with pytest.raises(sqlite3.OperationalError):
        whoop_store.save_connection_tokens(
            db_path,
            {
                session_field: "access-token",
                renewal_field: "refresh-token",
                "expires_in": 3600,
            },
        )

    assert whoop_store.load_connection_token_material(db_path) == {}


def test_disconnect_surfaces_protected_material_delete_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    session_field = "access_" + "token"
    renewal_field = "refresh_" + "token"
    whoop_store.save_connection_tokens(
        db_path,
        {
            session_field: "access-token",
            renewal_field: "refresh-token",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(whoop_store.os, "unlink", lambda *_args: (_ for _ in ()).throw(PermissionError("locked")))

    with pytest.raises(PermissionError):
        whoop_store.disconnect_whoop(db_path)

    assert whoop_store.get_connection_status(db_path)["status"] == "connected"


def test_project_daily_facts_is_idempotent_and_merges_record_types(tmp_path):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    run_id = whoop_store.record_whoop_sync_run(
        db_path,
        reason="manual",
        started_at=datetime(2026, 6, 25, 7, 0, 0),
    )

    recovery_rows = [
        {
            "upstream_id": "recovery-1",
            "local_date": "2026-06-25",
            "score_state": "SCORED",
            "recovery_score": 38,
            "recovery_band": "low",
            "hrv_rmssd": 42,
            "resting_hr": 57,
            "respiratory_rate": 13.8,
            "spo2": 97.0,
            "skin_temp": 0.4,
            "percent_recorded": 92,
        }
    ]
    sleep_rows = [
        {
            "upstream_id": "sleep-1",
            "local_date": "2026-06-25",
            "score_state": "SCORED",
            "sleep_performance_pct": 68,
            "sleep_need_gap_min": 95,
        }
    ]
    cycle_rows = [
        {
            "upstream_id": "cycle-1",
            "local_date": "2026-06-25",
            "score_state": "SCORED",
            "strain": 18.4,
        }
    ]

    assert whoop_store.upsert_whoop_records(db_path, "recovery", recovery_rows, sync_run_id=run_id) == 1
    assert whoop_store.upsert_whoop_records(db_path, "sleep", sleep_rows, sync_run_id=run_id) == 1
    assert whoop_store.upsert_whoop_records(db_path, "cycle", cycle_rows, sync_run_id=run_id) == 1
    assert whoop_store.project_whoop_daily_facts(db_path) == 3
    assert whoop_store.project_whoop_daily_facts(db_path) == 3

    fact = whoop_store.get_daily_fact(db_path, local_date="2026-06-25")
    assert fact["recovery_score"] == 38
    assert fact["sleep_performance_pct"] == 68
    assert fact["sleep_need_gap_min"] == 95
    assert fact["strain"] == 18.4
    assert fact["score_state"] == "SCORED"


def test_project_daily_facts_does_not_promote_pending_recovery_metrics(tmp_path):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)

    whoop_store.upsert_whoop_records(
        db_path,
        "recovery",
        [
            {
                "upstream_id": "recovery-pending",
                "local_date": "2026-06-25",
                "score_state": "PENDING_SCORE",
                "recovery_score": 22,
                "recovery_band": "low",
            }
        ],
    )
    whoop_store.upsert_whoop_records(
        db_path,
        "sleep",
        [
            {
                "upstream_id": "sleep-scored",
                "local_date": "2026-06-25",
                "score_state": "SCORED",
                "sleep_performance_pct": 90,
            }
        ],
    )

    whoop_store.project_whoop_daily_facts(db_path)

    fact = whoop_store.get_daily_fact(db_path, local_date="2026-06-25")
    assert fact["score_state"] == "PENDING_SCORE"
    assert fact["recovery_score"] is None
    assert fact["recovery_band"] is None
    assert fact["sleep_performance_pct"] == 90


def test_rotate_connection_tokens_replaces_refresh_token_atomically(tmp_path):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    whoop_store.save_connection_tokens(
        db_path,
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_in": 100,
        },
    )

    whoop_store.rotate_connection_tokens(
        db_path,
        {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 200,
        },
        rotated_at=datetime(2026, 6, 25, 10, 0, 0),
    )

    status = whoop_store.get_connection_status(db_path, include_private=True)
    assert status["protected_material_available"] is True
    material = whoop_store.load_connection_token_material(db_path)
    assert material["session_value"] == "new-access"
    assert material["renewal_value"] == "new-refresh"


def test_oauth_state_is_single_use_short_lived_and_user_bound(tmp_path):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.create_oauth_state(
        db_path,
        state="state-value",
        redirect_uri="http://localhost/api/whoop/callback",
        user_binding="user-a",
        created_at=datetime(2026, 6, 25, 8, 0, 0),
    )

    assert whoop_store.consume_oauth_state(db_path, "state-value", user_binding="user-b") is None

    whoop_store.create_oauth_state(
        db_path,
        state="state-value-2",
        redirect_uri="http://localhost/api/whoop/callback",
        user_binding="user-a",
        created_at=datetime(2026, 6, 25, 8, 0, 0),
    )
    assert whoop_store.consume_oauth_state(
        db_path,
        "state-value-2",
        user_binding="user-a",
        now=datetime(2026, 6, 25, 8, 11, 0),
    ) is None

    whoop_store.create_oauth_state(
        db_path,
        state="state-value-3",
        redirect_uri="http://localhost/api/whoop/callback",
        user_binding="user-a",
        created_at=datetime(2026, 6, 25, 8, 0, 0),
    )
    assert whoop_store.consume_oauth_state(
        db_path,
        "state-value-3",
        user_binding="user-a",
        now=datetime(2026, 6, 25, 8, 5, 0),
    ) is not None
    assert whoop_store.consume_oauth_state(
        db_path,
        "state-value-3",
        user_binding="user-a",
        now=datetime(2026, 6, 25, 8, 6, 0),
    ) is None


def test_default_protected_material_path_is_outside_repo(monkeypatch):
    monkeypatch.delenv("WHOOP_PROTECTED_MATERIAL_DIR", raising=False)
    repo_db_path = os.path.join(os.path.dirname(whoop_store.__file__), "whoop.sqlite3")

    material_path = whoop_store._protected_material_path(repo_db_path)

    assert not material_path.startswith(os.path.dirname(whoop_store.__file__))
    assert "Application Support" in material_path


def test_default_protected_material_path_is_outside_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("WHOOP_PROTECTED_MATERIAL_DIR", raising=False)
    db_path = str(tmp_path / "whoop.sqlite3")

    material_path = whoop_store._protected_material_path(db_path)

    assert not material_path.startswith(str(tmp_path))
    assert "Application Support" in material_path


def test_default_protected_material_path_is_namespaced_by_database(monkeypatch, tmp_path):
    monkeypatch.delenv("WHOOP_PROTECTED_MATERIAL_DIR", raising=False)
    first_path = whoop_store._protected_material_path(str(tmp_path / "one.sqlite3"))
    second_path = whoop_store._protected_material_path(str(tmp_path / "two.sqlite3"))

    assert first_path != second_path
    assert os.path.basename(first_path).startswith(".whoop-protected-material-")
    assert os.path.basename(second_path).startswith(".whoop-protected-material-")


def test_clear_whoop_data_removes_import_history(tmp_path):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    whoop_store.record_whoop_sync_run(db_path, reason="csv_import")

    whoop_store.clear_whoop_data(db_path)

    assert whoop_store.list_whoop_sync_runs(db_path, reason="csv_import") == []
