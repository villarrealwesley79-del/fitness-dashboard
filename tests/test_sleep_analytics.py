"""Focused source-precedence contracts for sleep analytics."""
from __future__ import annotations

import importlib

import pytest

from wearable_fact_store import WearableDailyFact, upsert_daily_facts


@pytest.fixture()
def sleep_analytics_api(isolated_app, monkeypatch):
    monkeypatch.setattr(isolated_app, "SLEEP_DATA", [])
    monkeypatch.setattr(isolated_app, "WORKOUTS", [])
    return isolated_app, isolated_app.app.test_client()


def _store_night(module, day, provider, *, wake, minutes, deep=None, rem=None, light=None, awake=None, in_bed=None,
                 efficiency=None, heart_rate=None, system="open_wearables"):
    metrics = {
        "sleep_duration": minutes, "sleep_deep_duration": deep, "sleep_rem_duration": rem,
        "sleep_light_duration": light, "sleep_awake_duration": awake, "sleep_time_in_bed": in_bed,
        "sleep_efficiency": efficiency, "sleep_avg_heart_rate": heart_rate,
    }
    upsert_daily_facts(module.WEARABLE_FACTS_DB_FILE, [
        WearableDailyFact(
            date=day, provider_id=provider, source_label=provider, metric=metric, value=float(value),
            source_provider=provider, source_system=system, source_id="sleep-summary", observed_at=wake, freshness="fresh",
        )
        for metric, value in metrics.items() if value is not None
    ])


def test_sleep_analytics_keeps_manual_nights_the_wearable_does_not_cover(sleep_analytics_api, monkeypatch):
    # FIT-341: a wearable wins only on overlapping dates; other manual nights stay visible.
    module, client = sleep_analytics_api
    _store_night(module, "2026-07-01", "oura", wake="2026-07-01T06:00:00Z", minutes=450, deep=90, rem=100,
                 light=260, awake=30, in_bed=480, efficiency=92.0, heart_rate=52.0)
    monkeypatch.setattr(
        module, "SLEEP_DATA", [{"date": "2026-07-02", "sleep_duration_min": 300, "source": "apple_watch"}],
    )

    response = client.get("/api/sleep/analytics")

    assert response.status_code == 200
    assert response.get_json()["history"] == [
        {
            "date": "2026-07-01", "sleep_start": "2026-06-30T22:00:00+00:00",
            "sleep_end": "2026-07-01T06:00:00Z", "sleep_duration_min": 450,
            "deep_sleep_min": 90, "rem_sleep_min": 100, "light_sleep_min": 260,
            "awake_min": 30, "sleep_score": None, "efficiency": 92.0,
            "avg_heart_rate": 52.0, "source": "oura", "via": "open_wearables",
        },
        {"date": "2026-07-02", "sleep_duration_min": 300, "source": "apple_watch"},
    ]


def test_sleep_analytics_shows_archive_nights_labelled_as_archive(sleep_analytics_api):
    module, client = sleep_analytics_api
    _store_night(module, "2026-01-05", "oura", wake="2026-01-05T07:00:00Z", minutes=415, system="archive")

    [row] = client.get("/api/sleep/analytics").get_json()["history"]

    assert (row["date"], row["source"], row["via"]) == ("2026-01-05", "oura", "archive")
