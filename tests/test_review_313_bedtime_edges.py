"""Reviewer checks for PR #313: bedtime dispersion across midnight."""
from __future__ import annotations

import importlib

import pytest


def _rows(*times):
    return [{"bedtime_start": t} for t in times]


@pytest.mark.parametrize(
    ("times", "expected"),
    [
        # Unwrapped -60, -30, +30, +60 min: linear SD 47.4; circular SD ~48.
        (("2026-07-10T23:00:00-05:00", "2026-07-11T23:30:00-05:00",
          "2026-07-13T00:30:00-05:00", "2026-07-14T01:00:00-05:00"), 48),
        (("2026-07-10T23:59:00-05:00", "2026-07-12T00:01:00-05:00"), 1),
        # Same spread shifted away from midnight must give the same answer.
        (("2026-07-10T21:00:00-05:00", "2026-07-11T21:30:00-05:00",
          "2026-07-12T22:30:00-05:00", "2026-07-13T23:00:00-05:00"), 48),
    ],
)
def test_circular_sd_is_shift_invariant(monkeypatch, times, expected):
    module = importlib.import_module("oura_sleep_sync")
    monkeypatch.setattr(module, "get_sleep_range", lambda *_a, **_kw: _rows(*times))
    assert module.calculate_bedtime_variance("unused.sqlite3") == expected


def test_sleep_summary_rates_midnight_straddling_bedtimes_excellent(monkeypatch, tmp_path):
    fitness_app = importlib.import_module("app")
    fitness_app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(fitness_app, "OURA_DB_FILE", str(tmp_path / "oura.sqlite3"))
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_a, **_kw: None)
    monkeypatch.setattr(fitness_app, "get_oura_daily_range", lambda *_a, **_kw: [])
    sleep_sync = importlib.import_module("oura_sleep_sync")
    monkeypatch.setattr(sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [])
    rows = _rows("2026-07-10T23:50:00-05:00", "2026-07-12T00:10:00-05:00")
    monkeypatch.setattr(sleep_sync, "get_sleep_range", lambda *_a, **_kw: rows)

    consistency = fitness_app.app.test_client().get("/api/oura/sleep-summary").get_json()["consistency"]

    assert consistency["status"] == "excellent"
