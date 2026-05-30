from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile


TEST_DB_PATH = Path(tempfile.gettempdir()) / "fitness-dashboard-fit29-apple-health-sync.db"


def _app_module(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit29-contract-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit29-health-token")
    monkeypatch.setenv("APPLE_HEALTH_SYNC_DB", str(TEST_DB_PATH))
    if "app" not in sys.modules and TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    if TEST_DB_PATH.exists():
        conn = sqlite3.connect(TEST_DB_PATH)
        try:
            conn.execute("DELETE FROM ah_sync_log")
            conn.execute("DELETE FROM ah_sync_events")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
    return module


def _record_dates(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT record_type, record_date, data_json FROM ah_sync_log ORDER BY record_type, record_date"
    ).fetchall()
    conn.close()
    return rows


def test_hae_normalizer_uses_timestamp_offset_local_date(monkeypatch):
    module = _app_module(monkeypatch)
    parser = importlib.import_module("apple_health_parser")

    response = module.app.test_client().post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "data": {
                "workouts": [
                    {
                        "name": "Traditional Strength Training",
                        "start": "2026-05-18T23:45:00-05:00",
                        "duration": 3600,
                    }
                ],
                "metrics": [
                    {
                        "name": "step_count",
                        "data": [{"date": "2026-05-18T23:55:00-05:00", "qty": 1200}],
                    },
                    {
                        "name": "heart_rate_variability",
                        "data": [{"date": "2026-05-19T00:15:00+09:00", "avg": 42}],
                    },
                ],
            }
        },
    )

    assert response.status_code == 200
    rows = _record_dates(TEST_DB_PATH)
    assert [(row[0], row[1]) for row in rows] == [
        ("hrv", "2026-05-19"),
        ("steps", "2026-05-18"),
        ("workouts", "2026-05-18"),
    ]
    assert parser.health_data_available() is True
    assert [rec["date"] for rec in parser._get_sync_records("steps")] == ["2026-05-18"]


def test_hae_normalizer_preserves_existing_cdt_date(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "data": {
                "metrics": [
                    {
                        "name": "step_count",
                        "data": [{"date": "2026-05-18T06:00:00-05:00", "qty": 1000}],
                    }
                ]
            }
        },
    )

    assert response.status_code == 200
    rows = _record_dates(TEST_DB_PATH)
    assert [(row[0], row[1]) for row in rows] == [("steps", "2026-05-18")]


def test_hae_normalizer_accepts_z_suffixed_timestamp(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "data": {
                "metrics": [
                    {
                        "name": "step_count",
                        "data": [{"date": "2026-05-19T04:55:00Z", "qty": 900}],
                    }
                ]
            }
        },
    )

    assert response.status_code == 200
    rows = _record_dates(TEST_DB_PATH)
    assert [(row[0], row[1]) for row in rows] == [("steps", "2026-05-19")]


def test_flat_sync_payload_normalizes_date_and_dashboard_freshness(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={"steps": [{"date": "2026-05-18T23:55:00-05:00", "value": 1200}]},
    )

    assert response.status_code == 200
    rows = _record_dates(TEST_DB_PATH)
    assert [(row[0], row[1]) for row in rows] == [("steps", "2026-05-18")]
    parser = importlib.import_module("apple_health_parser")
    assert [rec["date"] for rec in parser._get_sync_records("steps")] == ["2026-05-18"]
    status, last_data, last_sync = module._latest_apple_health_freshness(
        now=module.datetime(2026, 5, 18, 12, 0, 0)
    )
    assert status == "fresh"
    assert last_data == "2026-05-18"
    assert last_sync


def test_sync_routes_honor_runtime_db_env_after_app_import(monkeypatch):
    module = _app_module(monkeypatch)
    alt_db_path = Path(tempfile.gettempdir()) / "fitness-dashboard-fit29-runtime-apple-health-sync.db"
    if alt_db_path.exists():
        alt_db_path.unlink()
    monkeypatch.setenv("APPLE_HEALTH_SYNC_DB", str(alt_db_path))

    response = module.app.test_client().post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={"steps": [{"date": "2026-05-18T23:55:00-05:00", "value": 1200}]},
    )

    assert response.status_code == 200
    rows = _record_dates(alt_db_path)
    assert [(row[0], row[1]) for row in rows] == [("steps", "2026-05-18")]


def test_workouts_endpoint_preserves_basketball_and_filters_other(monkeypatch):
    module = _app_module(monkeypatch)
    parser = importlib.import_module("apple_health_parser")
    monkeypatch.setattr(parser, "parse_workouts", lambda: [])
    client = module.app.test_client()
    recent_date = "2026-05-30"

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(parser, "datetime", FrozenDateTime)

    response = client.post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "workouts": [
                {
                    "date": recent_date,
                    "startDate": f"{recent_date} 12:28:05 -0500",
                    "workoutActivityType": 37,
                    "duration_minutes": 95.3,
                    "total_energy_kcal": 1035.4,
                    "distance_m": 4.25,
                    "avgHeartRate": {"qty": 164},
                },
                {
                    "date": recent_date,
                    "startDate": f"{recent_date} 23:50:00 -0500",
                    "workoutActivityType": 25,
                    "duration_minutes": 14.0,
                    "total_energy_kcal": 67.5,
                },
            ]
        },
    )
    assert response.status_code == 200

    payload = client.get("/api/apple-health/workouts?days=0").get_json()

    assert payload["total"] == 1
    assert payload["workouts"][0]["date"] == recent_date
    assert payload["workouts"][0]["activity"] == "Basketball"
    assert payload["workouts"][0]["activity_type"] == "Basketball"
    assert payload["workouts"][0]["duration_min"] == 95.3
    assert payload["workouts"][0]["avg_heart_rate"] == 164

    summary = client.get("/api/apple-health/summary").get_json()
    assert summary["workouts_total"] == 1
    assert summary["workouts_7d"] == 1
    assert summary["workouts_30d"] == 1


def test_sync_status_initializes_runtime_db_env_after_app_import(monkeypatch):
    module = _app_module(monkeypatch)
    alt_db_path = Path(tempfile.gettempdir()) / "fitness-dashboard-fit29-runtime-status.db"
    if alt_db_path.exists():
        alt_db_path.unlink()
    monkeypatch.setenv("APPLE_HEALTH_SYNC_DB", str(alt_db_path))

    response = module.app.test_client().get("/api/apple-health/sync/status")

    assert response.status_code == 200
    assert response.get_json()["total_records"] == 0


def test_sleep_duration_falls_back_to_phase_sum_during_hae_normalization(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "data": {
                "metrics": [
                    {
                        "name": "sleep_analysis",
                        "data": [
                            {
                                "date": "2026-04-22",
                                "deep": 1.25,
                                "rem": 1.5,
                                "core": 4.75,
                                "awake": 0.25,
                                "inBed": 8.0,
                            }
                        ],
                    }
                ]
            }
        },
    )

    assert response.status_code == 200
    rows = _record_dates(TEST_DB_PATH)
    assert [(row[0], row[1]) for row in rows] == [("sleep", "2026-04-22")]
    stored = json.loads(rows[0][2])
    assert stored["duration_minutes"] == 450.0
    assert stored["duration_minutes_source"] == "computed_phase_sum"
    assert stored["deep_minutes"] == 75.0
    assert stored["rem_minutes"] == 90.0
    assert stored["core_minutes"] == 285.0


def test_sleep_phase_sum_reingest_updates_existing_daily_sleep_row(monkeypatch):
    module = _app_module(monkeypatch)
    client = module.app.test_client()

    first = client.post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "data": {
                "metrics": [
                    {
                        "name": "sleep_analysis",
                        "data": [{"date": "2026-04-22", "awake": 0.25, "inBed": 8.0}],
                    }
                ]
            }
        },
    )
    assert first.status_code == 200
    stored = json.loads(_record_dates(TEST_DB_PATH)[0][2])
    assert stored["duration_minutes"] is None

    second = client.post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "data": {
                "metrics": [
                    {
                        "name": "sleep_analysis",
                        "data": [
                            {
                                "date": "2026-04-22",
                                "deep": 1.25,
                                "rem": 1.5,
                                "core": 4.75,
                                "awake": 0.25,
                                "inBed": 8.0,
                            }
                        ],
                    }
                ]
            }
        },
    )

    assert second.status_code == 200
    rows = _record_dates(TEST_DB_PATH)
    assert len(rows) == 1
    stored = json.loads(rows[0][2])
    assert stored["duration_minutes"] == 450.0
    assert stored["duration_minutes_source"] == "computed_phase_sum"


def test_sleep_reingest_does_not_erase_existing_duration_with_partial_row(monkeypatch):
    module = _app_module(monkeypatch)
    client = module.app.test_client()

    first = client.post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "data": {
                "metrics": [
                    {
                        "name": "sleep_analysis",
                        "data": [
                            {
                                "date": "2026-04-22",
                                "deep": 1.25,
                                "rem": 1.5,
                                "core": 4.75,
                                "awake": 0.25,
                                "inBed": 8.0,
                            }
                        ],
                    }
                ]
            }
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "data": {
                "metrics": [
                    {
                        "name": "sleep_analysis",
                        "data": [{"date": "2026-04-22", "awake": 0.25, "inBed": 8.0}],
                    }
                ]
            }
        },
    )

    assert second.status_code == 200
    rows = _record_dates(TEST_DB_PATH)
    assert len(rows) == 1
    stored = json.loads(rows[0][2])
    assert stored["duration_minutes"] == 450.0
    assert stored["duration_minutes_source"] == "computed_phase_sum"
    assert stored["deep_minutes"] == 75.0


def test_sleep_reingest_preserves_legacy_duration_fields_and_updates_timestamp(monkeypatch):
    module = _app_module(monkeypatch)
    with sqlite3.connect(TEST_DB_PATH) as conn:
        conn.execute(
            """INSERT INTO ah_sync_log
               (source, record_type, record_date, record_key, data_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "health_auto_export",
                "sleep",
                "2026-04-22",
                "",
                json.dumps({"date": "2026-04-22", "hours": 7.5, "duration_hours": 7.5}),
                "2026-01-01 00:00:00",
            ),
        )
        conn.commit()

    response = module.app.test_client().post(
        "/api/apple-health/sync?token=fit29-health-token",
        json={
            "data": {
                "metrics": [
                    {
                        "name": "sleep_analysis",
                        "data": [{"date": "2026-04-22", "awake": 0.25, "inBed": 8.0}],
                    }
                ]
            }
        },
    )

    assert response.status_code == 200
    with sqlite3.connect(TEST_DB_PATH) as conn:
        data_json, created_at = conn.execute(
            "SELECT data_json, created_at FROM ah_sync_log WHERE record_type = 'sleep'"
        ).fetchone()
    stored = json.loads(data_json)
    assert stored["hours"] == 7.5
    assert stored["duration_hours"] == 7.5
    assert stored["duration_minutes"] is None
    assert created_at != "2026-01-01 00:00:00"
