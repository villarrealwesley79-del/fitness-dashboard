"""Reviewer checks for PR #260: valid nightly sleep must not be hidden."""
from __future__ import annotations

import importlib

import oura_sleep_sync


VALID_NIGHT = {
    "day": "2026-06-03",
    "type": "long_sleep",
    "bedtime_start": "2026-06-02T23:10:00-05:00",
    "total_sleep_min": 450,
    "deep_sleep_min": 90,
    "rem_sleep_min": 100,
    "light_sleep_min": 260,
    "sleep_score": 84,
}

# Pre-migration oura_daily row: sleep_type is NULL, and main's get_daily_range
# let a later same-day nap overwrite the night (short duration, no score).
LEGACY_DAILY_NAP = {
    "day": "2026-06-03",
    "sleep_type": None,
    "sleep_duration_min": 25,
    "sleep_deep_min": 0,
    "sleep_rem_min": 0,
    "sleep_light_min": 25,
    "sleep_score": None,
}


def _summary(monkeypatch, daily_range):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [VALID_NIGHT])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [VALID_NIGHT])
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: daily_range)
    return module.app.test_client().get("/api/oura/sleep-summary").get_json()


def test_valid_night_counts_when_daily_cache_agrees(monkeypatch):
    payload = _summary(monkeypatch, [])
    assert payload["data_quality"] == {"status": "ok"}
    assert payload["week_average"]["duration_min"] == 450
    assert [row["date"] for row in payload["trend_data"]] == ["2026-06-03"]


def test_legacy_daily_nap_row_does_not_hide_valid_oura_sleep_night(monkeypatch):
    """Finding demonstrator: a stale daily-cache nap hides the real night."""
    payload = _summary(monkeypatch, [LEGACY_DAILY_NAP])
    assert payload["last_night"]["total_sleep_min"] == 450
    assert [row["date"] for row in payload["trend_data"]] == ["2026-06-03"]
    assert payload["week_average"]["duration_min"] == 450
