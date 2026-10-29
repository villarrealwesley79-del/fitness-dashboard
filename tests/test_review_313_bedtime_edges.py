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
    module = importlib.import_module("app")
    assert module._bedtime_variance_from_rows(_rows(*times)) == expected


def test_sleep_summary_rates_midnight_straddling_bedtimes_excellent(isolated_app, monkeypatch):
    from datetime import date, timedelta
    from wearable_fact_store import WearableDailyFact, upsert_daily_facts

    # Bedtime is estimated as wake time minus time in bed: 23:50 and 00:10 bedtimes.
    facts = []
    for offset, wake in ((1, "07:50:00"), (0, "08:10:00")):
        day = (date.today() - timedelta(days=offset)).isoformat()
        for metric in ("sleep_duration", "sleep_time_in_bed"):
            facts.append(WearableDailyFact(
                date=day, provider_id="oura", source_label="Oura", metric=metric, value=480.0,
                source_provider="oura", source_system="open_wearables", source_id="sleep-summary",
                observed_at=f"{day}T{wake}Z", freshness="fresh",
            ))
    upsert_daily_facts(isolated_app.WEARABLE_FACTS_DB_FILE, facts)

    consistency = isolated_app.app.test_client().get("/api/sleep/summary").get_json()["consistency"]

    assert consistency["status"] == "excellent"
