from __future__ import annotations

import sqlite3
from datetime import datetime

import whoop_store


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
