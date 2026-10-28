"""Conflict nights stay whole and conservative; a stale preferred series does not hide a fresh fallback.

PR #360 review round 2 (synthetic data). Each test failed before the round-3 fix.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from wearable_fact_store import WearableDailyFact, upsert_daily_facts

TODAY = date.today().isoformat()


def _day(offset=0):
    return (date.today() - timedelta(days=offset)).isoformat()


def _fact(day, provider, metric, value, *, system="open_wearables", kind="summary", source_id="s", **extra):
    extra.setdefault("source_provider", provider)
    return WearableDailyFact(
        date=day, provider_id=provider, source_label=provider.title(), metric=metric, value=value,
        source_system=system, source_record_kind=kind, source_id=source_id,
        freshness="fresh", used_for_recommendation=True, **extra,
    )


@pytest.fixture


def app_db(isolated_app, monkeypatch):
    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", lambda **_: {"errors": {"config": "missing"}})
    monkeypatch.setattr(isolated_app, "_cached_wttr", lambda *_a, **_k: {"available": False})
    monkeypatch.setattr(isolated_app, "WORKOUTS", [])
    monkeypatch.setattr(isolated_app, "SORENESS_DATA", [])
    monkeypatch.setattr(isolated_app, "RECOVERY_DATA", [])
    monkeypatch.setattr(isolated_app, "_load_apple_health_recommendation_workouts", lambda **_k: [], raising=False)
    return isolated_app


_RANK = {"intensity": 0, "moderate": 1, "recovery": 2, "rest": 3}


@pytest.mark.parametrize("fragment", [40, 0], ids=["40min_fragment", "zero"])


def test_short_fragment_conflict_does_not_erase_sleep_debt(app_db, fragment):
    """Oura 5 h nights; WHOOP logs a strap-off fragment. The conflict must not drop the nights."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    facts = []
    for i in range(7):
        facts += [_fact(_day(i), "oura", "sleep_duration", 300), _fact(_day(i), "whoop", "sleep_duration", fragment)]
    upsert_daily_facts(db, facts)
    debt = app_db.calculate_sleep_debt(days=7)
    # Oura alone: 7 nights under, 840 min. A conflict may only make this more conservative.
    assert debt["nights_under"] >= 7 and debt["debt_minutes"] >= 840, debt


def test_older_fragment_conflicts_do_not_lift_the_plan_end_to_end(app_db):
    """Last night Oura 8 h (readiness 70, moderate); six older Oura 5 h nights (debt 720 -> downgrade).

    WHOOP strap-off fragments on the older nights (outside the guard's 48 h window) must not
    erase that debt and turn the recovery day into a moderate one.
    """
    db = app_db.WEARABLE_FACTS_DB_FILE
    client = app_db.app.test_client()
    upsert_daily_facts(db, [_fact(TODAY, "oura", "sleep_duration", 480)]
                       + [_fact(_day(i), "oura", "sleep_duration", 300) for i in range(1, 7)])
    base = client.get("/api/recommendation/smart").get_json()
    upsert_daily_facts(db, [_fact(_day(i), "whoop", "sleep_duration", 40) for i in range(2, 7)])
    conflict = client.get("/api/recommendation/smart").get_json()
    summary = lambda b: (b["recommendation"], b["readiness"], b["readiness_factors"]["sleep_debt"]["debt_minutes"])
    assert _RANK[conflict["recommendation"]] >= _RANK[base["recommendation"]], (summary(base), summary(conflict))


def test_short_fragment_conflict_keeps_last_nights_readiness(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(TODAY, "oura", "sleep_duration", 300), _fact(TODAY, "whoop", "sleep_duration", 30)])
    readiness, _basis = app_db._readiness_with_basis()
    # Oura alone gives 62 (a recovery day). The conflict must not make readiness unknown (a moderate day).
    assert readiness is not None and readiness <= 62, readiness


def test_conflict_night_keeps_the_used_providers_own_stages(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "sleep_duration", 480),
        _fact(TODAY, "oura", "sleep_deep_duration", 90),
        _fact(TODAY, "oura", "sleep_rem_duration", 110),
        _fact(TODAY, "oura", "sleep_light_duration", 280),
        _fact(TODAY, "whoop", "sleep_duration", 300),
        _fact(TODAY, "whoop", "sleep_deep_duration", 60),
        _fact(TODAY, "whoop", "sleep_rem_duration", 70),
        _fact(TODAY, "whoop", "sleep_light_duration", 170),
    ])
    row = app_db._sleep_rows_from_facts(days=1)[0]
    assert row["provider"] == "whoop" and row["sleep_duration_min"] == 300
    assert (row["sleep_deep_min"], row["sleep_rem_min"], row["sleep_light_min"]) == (60, 70, 170), row


def test_stale_preferred_hrv_does_not_hide_a_fresh_fallback_series(app_db):
    """Oura ring not worn for 3 days; WHOOP has a fresh, clearly declining week."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    facts = [_fact(_day(i), "oura", "heart_rate_variability", 60) for i in (3, 4, 5, 6)]
    whoop = {6: 70, 5: 70, 4: 70, 3: 70, 2: 50, 1: 50, 0: 50}
    facts += [_fact(_day(i), "whoop", "heart_rate_variability", v) for i, v in whoop.items()]
    upsert_daily_facts(db, facts)
    assert app_db.get_recent_hrv_trend() == "declining", [f["provider"] for f in app_db._hrv_facts(7)]


def test_conflict_night_readiness_uses_the_used_providers_sleep_heart_rate(app_db):
    """WHOOP 430 min (HR 72) vs Oura 500 min (HR 55): conflict -> WHOOP's night.

    WHOOP alone gives readiness 67 (a recovery day). The conflict night must be no less
    conservative, so it must keep WHOOP's own sleep heart rate.
    """
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "sleep_duration", 500), _fact(TODAY, "oura", "sleep_avg_heart_rate", 55),
        _fact(TODAY, "whoop", "sleep_duration", 430), _fact(TODAY, "whoop", "sleep_avg_heart_rate", 72),
    ])
    readiness, _ = app_db._readiness_with_basis()
    assert readiness is not None and readiness <= 67, readiness


def test_short_fragment_conflict_shows_false_inconsistent_warning(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(TODAY, "oura", "sleep_duration", 300), _fact(TODAY, "whoop", "sleep_duration", 40)])
    body = app_db.app.test_client().get("/api/sleep/summary").get_json()
    assert body["data_quality"]["status"] != "inconsistent", (body["data_quality"], body["last_night"])


@pytest.mark.parametrize("oura", [True, False], ids=["conflict_A", "whoop_only_B"])


def test_conflict_hr_night_end_to_end(app_db, oura):
    """A (Oura 500/HR55 + WHOOP 430/HR72) must be no less conservative than B (WHOOP alone)."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    facts = [_fact(TODAY, "whoop", "sleep_duration", 430), _fact(TODAY, "whoop", "sleep_avg_heart_rate", 72)]
    if oura:
        facts += [_fact(TODAY, "oura", "sleep_duration", 500), _fact(TODAY, "oura", "sleep_avg_heart_rate", 55)]
    upsert_daily_facts(db, facts)
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    # B: WHOOP alone -> readiness 67 -> recovery. A must be at least as conservative.
    assert body["recommendation"] in {"recovery", "rest"}, (body["readiness"], body["recommendation"])
