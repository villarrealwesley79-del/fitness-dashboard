"""Round 8 (PR #360): a day whose only sleep is a sub-hour record contributes no night HRV, so it cannot
mask a real decline. Synthetic data only."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from wearable_fact_store import WearableDailyFact, upsert_daily_facts

def _day(offset=0):
    return (date.today() - timedelta(days=offset)).isoformat()


def _fact(day, provider, metric, value, *, source_id="s", **extra):
    return WearableDailyFact(
        date=day, provider_id=provider, source_label=provider.title(), metric=metric, value=value,
        source_system="open_wearables", source_record_kind="summary", source_id=source_id,
        source_provider=provider, freshness="fresh", used_for_recommendation=True, **extra,
    )


@pytest.fixture
def app_db(isolated_app, monkeypatch):
    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", lambda **_: {"errors": {"config": "missing"}})
    monkeypatch.setattr(isolated_app, "_cached_wttr", lambda *_a, **_k: {"available": False})
    monkeypatch.setattr(isolated_app, "WORKOUTS", [])
    monkeypatch.setattr(isolated_app, "SORENESS_DATA", [])
    monkeypatch.setattr(isolated_app, "RECOVERY_DATA", [])
    monkeypatch.setattr(isolated_app, "SLEEP_DATA", [])
    monkeypatch.setitem(isolated_app._WEARABLE_REFRESH_STATE, "at", None)
    return isolated_app


def test_a_sub_hour_only_days_hrv_does_not_mask_a_real_decline(app_db, monkeypatch):
    """Fails at eb9c06a and 2a63993: the 25-minute day's HRV (90) turns 'declining' into 'stable' and the plan
    goes from recovery to moderate on /api/recommendation/smart and /api/next-workout."""
    facts = []
    for i, hrv in ((5, 64), (4, 62), (3, 58), (1, 50), (0, 48)):
        facts += [_fact(_day(i), "oura", "sleep_duration", 420, source_id=f"n{i}"),
                  _fact(_day(i), "oura", "sleep_hrv_rmssd", hrv, source_id=f"n{i}")]
    # what the OW sleep-summary mapper stores for a 25-minute day row carrying avg_hrv_rmssd_ms
    facts += [_fact(_day(2), "oura", "sleep_duration", 25, source_id="sleep-summary"),
              _fact(_day(2), "oura", "sleep_hrv_rmssd", 90, source_id="sleep-summary")]
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, facts)
    assert app_db._hrv_series(7) == [64.0, 62.0, 58.0, 50.0, 48.0]
    assert app_db.get_recent_hrv_trend() == "declining"
    assert app_db._current_workout_training_recommendation() == "recovery"  # what /api/next-workout uses
    assert app_db.app.test_client().get("/api/recommendation/smart").get_json()["recommendation"] == "recovery"


def test_mirror_a_sub_hour_days_low_hrv_still_makes_the_plan_more_cautious(app_db):
    """Fails at 3ea5420, passes at eb9c06a. Fresh real nights through yesterday (HRV 60) and a 25-min record
    today with HRV 30: base and main (Oura kept a nap-only day's average_hrv) say 'declining' and next-workout
    is 'recovery'; dropping the short day outright turns it 'stable' and the plan 'moderate'."""
    facts = []
    for i in (4, 3, 2, 1):
        facts += [_fact(_day(i), "oura", "sleep_duration", 420, source_id=f"n{i}"),
                  _fact(_day(i), "oura", "sleep_hrv_rmssd", 60, source_id=f"n{i}")]
    facts += [_fact(_day(0), "oura", "sleep_duration", 25, source_id="sleep-summary"),
              _fact(_day(0), "oura", "sleep_hrv_rmssd", 30, source_id="sleep-summary")]
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, facts)
    assert app_db.get_recent_hrv_trend() == "declining"
    assert app_db._current_workout_training_recommendation() == "recovery"  # what /api/next-workout uses
    client = app_db.app.test_client()
    assert client.get("/api/recommendation/smart").get_json()["recommendation"] == "recovery"
    assert client.get("/api/next-workout").status_code == 200


def test_mirror_stale_nights_and_a_fresh_short_low_hrv_today_stay_cautious(app_db):
    """Same rule when the real nights are stale: base says 'declining'; the plan must not relax."""
    facts = []
    for i in (6, 5, 4, 3):
        facts += [_fact(_day(i), "oura", "sleep_duration", 420, source_id=f"n{i}"),
                  _fact(_day(i), "oura", "sleep_hrv_rmssd", 60, source_id=f"n{i}")]
    facts += [_fact(_day(0), "oura", "sleep_duration", 25, source_id="sleep-summary"),
              _fact(_day(0), "oura", "sleep_hrv_rmssd", 30, source_id="sleep-summary")]
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, facts)
    assert app_db._current_workout_training_recommendation() == "recovery"
    assert app_db.app.test_client().get("/api/recommendation/smart").get_json()["recommendation"] == "recovery"
