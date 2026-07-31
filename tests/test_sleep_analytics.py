"""Focused source-precedence contracts for sleep analytics."""
from __future__ import annotations

import importlib
import sqlite3

import pytest


@pytest.fixture()
def sleep_analytics_api(monkeypatch, tmp_path):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    db_file = tmp_path / "oura.db"
    monkeypatch.setattr(module, "OURA_DB_FILE", str(db_file))
    monkeypatch.setattr(module, "SLEEP_DATA", [])
    monkeypatch.setattr(module, "WORKOUTS", [])
    return module, module.app.test_client(), db_file


def _create_oura_sleep(db_file, rows):
    with sqlite3.connect(db_file) as db:
        db.execute(
            """CREATE TABLE oura_sleep (
                day TEXT, type TEXT, bedtime_start TEXT, bedtime_end TEXT,
                total_sleep_min INTEGER, deep_sleep_min INTEGER,
                rem_sleep_min INTEGER, light_sleep_min INTEGER,
                awake_time_min INTEGER, sleep_score INTEGER, efficiency REAL,
                avg_heart_rate REAL, avg_hrv REAL
            )"""
        )
        db.executemany(
            "INSERT INTO oura_sleep VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def test_sleep_analytics_prefers_oura_over_manual_history(
    sleep_analytics_api, monkeypatch
):
    module, client, db_file = sleep_analytics_api
    _create_oura_sleep(
        db_file,
        [
            (
                "2026-07-01", "long_sleep", "2026-06-30T22:00:00", "2026-07-01T06:00:00",
                450, 90, 100, 260, 30, 88, 92.0, 52.0, 45.0,
            )
        ],
    )
    monkeypatch.setattr(
        module,
        "SLEEP_DATA",
        [{"date": "2026-07-02", "sleep_duration_min": 300, "source": "apple_watch"}],
    )

    response = client.get("/api/sleep/analytics")

    assert response.status_code == 200
    assert response.get_json()["history"] == [
        {
            "date": "2026-07-01", "sleep_start": "2026-06-30T22:00:00",
            "sleep_end": "2026-07-01T06:00:00", "sleep_duration_min": 450,
            "deep_sleep_min": 90, "rem_sleep_min": 100, "light_sleep_min": 260,
            "awake_min": 30, "sleep_score": 88, "efficiency": 92.0,
            "avg_heart_rate": 52.0, "avg_hrv": 45.0, "source": "oura",
        }
    ]


def test_sleep_analytics_falls_back_to_manual_and_prefers_apple_watch_duplicate(
    sleep_analytics_api, monkeypatch
):
    module, client, _db_file = sleep_analytics_api
    monkeypatch.setattr(
        module,
        "SLEEP_DATA",
        [
            {"date": "2026-07-02", "sleep_duration_min": 400, "source": "manual"},
            {"date": "2026-07-01", "sleep_duration_min": 390, "source": "manual"},
            {"date": "2026-07-02", "sleep_duration_min": 440, "source": "apple_watch"},
        ],
    )

    response = client.get("/api/sleep/analytics")

    assert response.status_code == 200
    history = response.get_json()["history"]
    assert [entry["date"] for entry in history] == ["2026-07-01", "2026-07-02"]
    assert history[-1] == {
        "date": "2026-07-02", "sleep_duration_min": 440, "source": "apple_watch"
    }
