"""Reviewer round-3 tests for PR #360 (W4b) at a7387ae. Synthetic data only.

Written from the contract: Oura first for sleep/recovery, the next provider fills gaps,
conflicts never raise demands, the proof names the provider behind each consumed field.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from wearable_fact_store import WearableDailyFact, preferred_daily_facts, upsert_daily_facts

TODAY = date.today().isoformat()
_RANK = {"intensity": 0, "moderate": 1, "recovery": 2, "rest": 3}


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


# --- 1. The preferred provider's fragment is a gap; the next provider must fill it ----------

@pytest.mark.parametrize("fragment", [40, 0], ids=["oura_40min", "oura_zero"])
def test_preferred_provider_fragment_does_not_erase_sleep_debt(app_db, fragment):
    """Ring not worn / dead battery: Oura logs a fragment, WHOOP has the real 5 h night."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    facts = []
    for i in range(7):
        facts += [_fact(_day(i), "whoop", "sleep_duration", 300), _fact(_day(i), "oura", "sleep_duration", fragment)]
    upsert_daily_facts(db, facts)
    debt = app_db.calculate_sleep_debt(days=7)
    # WHOOP alone: 7 nights under, 840 min. Oura's unusable fragment must not lower that.
    assert debt["nights_under"] >= 7 and debt["debt_minutes"] >= 840, debt


def test_preferred_provider_fragment_whoop_only_control(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(_day(i), "whoop", "sleep_duration", 300) for i in range(7)])
    debt = app_db.calculate_sleep_debt(days=7)
    assert debt["nights_under"] == 7 and debt["debt_minutes"] == 840, debt


def test_preferred_provider_fragment_keeps_last_nights_readiness(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(TODAY, "whoop", "sleep_duration", 300), _fact(TODAY, "oura", "sleep_duration", 30)])
    readiness, _basis = app_db._readiness_with_basis()
    # WHOOP alone gives 62 (a recovery day); unknown readiness lets the plan stay moderate.
    assert readiness is not None and readiness <= 62, readiness


def test_preferred_provider_fragment_no_false_inconsistent_warning(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(TODAY, "whoop", "sleep_duration", 300), _fact(TODAY, "oura", "sleep_duration", 40)])
    body = app_db.app.test_client().get("/api/sleep/summary").get_json()
    assert body["data_quality"]["status"] != "inconsistent", (body["data_quality"], body["last_night"])


def test_preferred_provider_fragments_do_not_lift_the_plan_end_to_end(app_db):
    """The last two nights Oura 8 h; five older WHOOP 5 h nights (debt 600). Oura fragments on the
    older nights (outside the guard's 48 h window) must not erase WHOOP's debt and lift the day."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    client = app_db.app.test_client()
    upsert_daily_facts(db, [_fact(_day(i), "oura", "sleep_duration", 480) for i in (0, 1)]
                       + [_fact(_day(i), "whoop", "sleep_duration", 300) for i in range(2, 7)])
    base = client.get("/api/recommendation/smart").get_json()
    upsert_daily_facts(db, [_fact(_day(i), "oura", "sleep_duration", 40) for i in range(2, 7)])
    after = client.get("/api/recommendation/smart").get_json()
    summary = lambda b: (b["recommendation"], b["readiness"], b["readiness_factors"]["sleep_debt"]["debt_minutes"])
    assert _RANK[after["recommendation"]] >= _RANK[base["recommendation"]], (summary(base), summary(after))


# --- 2. Round-3 HRV fallback: proof names the provider whose series fed the trend ----------

def test_hrv_fallback_series_is_named_in_the_recovery_proof(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    facts = [_fact(_day(i), "oura", "heart_rate_variability", 60) for i in (3, 4, 5, 6)]
    whoop = {6: 70, 5: 70, 4: 70, 3: 70, 2: 50, 1: 50, 0: 50}
    facts += [_fact(_day(i), "whoop", "heart_rate_variability", v) for i, v in whoop.items()]
    upsert_daily_facts(db, facts)
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    proof = body["recommendation_sources"]["source_proof"]["recovery"]
    assert body["hrv_trend"] == "declining", body.get("hrv_trend")
    assert proof["provider"] == "whoop" and proof["used_for_recommendation"], proof


def test_all_stale_hrv_series_feed_nothing(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    facts = [_fact(_day(i), "oura", "heart_rate_variability", v) for i, v in zip((3, 4, 5, 6), (40, 60, 70, 80))]
    facts += [_fact(_day(i), "whoop", "heart_rate_variability", v) for i, v in zip((3, 4, 5, 6), (40, 60, 70, 80))]
    upsert_daily_facts(db, facts)
    assert app_db._hrv_facts(7) == []


def test_hrv_series_is_one_provider_only(app_db):
    """Oura wins days 0-3, WHOOP fills 4-6: the trend series must not splice the two."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    facts = [_fact(_day(i), "oura", "heart_rate_variability", 40) for i in (0, 1, 2, 3)]
    facts += [_fact(_day(i), "whoop", "heart_rate_variability", 90) for i in range(7)]
    upsert_daily_facts(db, facts)
    series = app_db._hrv_facts(7)
    assert len({f["provider"] for f in series}) == 1, [(f["date"], f["provider"]) for f in series]


# --- 3. Whole-night rule with three providers ----------------------------------------------

def test_three_provider_conflict_never_borrows_a_third_providers_stages(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "sleep_duration", 480), _fact(TODAY, "oura", "sleep_deep_duration", 90),
        _fact(TODAY, "whoop", "sleep_duration", 300),
        _fact(TODAY, "apple", "sleep_duration", 400), _fact(TODAY, "apple", "sleep_deep_duration", 70),
    ])
    facts = preferred_daily_facts(db, {"sleep_duration", "sleep_deep_duration"}, TODAY, TODAY, app_db._open_wearables_profile_key())
    night = facts[TODAY]
    assert night["sleep_duration"]["provider"] == "whoop" and night["sleep_duration"]["value"] == 300
    assert "sleep_deep_duration" not in night, night
    assert night["sleep_duration"]["conflict_state"] == "conservative_value_used"


def test_fragment_never_triggers_a_conflict_but_two_real_nights_do(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    pk = app_db._open_wearables_profile_key()
    upsert_daily_facts(db, [_fact(TODAY, "oura", "sleep_duration", 420), _fact(TODAY, "whoop", "sleep_duration", 59.9)])
    night = preferred_daily_facts(db, {"sleep_duration"}, TODAY, TODAY, pk)[TODAY]["sleep_duration"]
    assert (night["provider"], night["conflict_state"]) == ("oura", None), night
    upsert_daily_facts(db, [_fact(TODAY, "whoop", "sleep_duration", 60.0, source_id="s2")])
    night = preferred_daily_facts(db, {"sleep_duration"}, TODAY, TODAY, pk)[TODAY]["sleep_duration"]
    # WHOOP now also has a plausible 60.0 (s2): 420 vs 60 is a conflict, so the lower real night is used.
    assert (night["provider"], night["value"], night["conflict_state"]) == ("whoop", 60.0, "conservative_value_used"), night


# --- 5. Round-1 SHOULD-FIX 3 restated without the obsolete readiness==80 precondition ---------

def test_recovery_proof_names_only_a_provider_that_fed_a_used_field(app_db):
    """Oura recovery_score yesterday, WHOOP resting HR today; neither feeds readiness (sleep estimate)."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(_day(1), "oura", "recovery_score", 80), _fact(TODAY, "whoop", "resting_heart_rate", 52)])
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    proof = body["recommendation_sources"]["source_proof"]["recovery"]
    assert not proof["used_for_recommendation"] and not proof.get("fields_used"), proof


# --- 4. Contract gates restated at this head -------------------------------------------------

def test_seven_five_hour_oura_nights_give_seven_nights_under(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(_day(i), "oura", "sleep_duration", 300) for i in range(7)])
    debt = app_db.calculate_sleep_debt(days=7)
    assert debt["nights_under"] == 7 and debt["debt_minutes"] == 840, debt


def test_hub_stores_a_sub_hour_oura_sleep_summary(isolated_app):
    """Realism: the OW sleep_summary path only drops values <= 0, so a 40-minute Oura night is stored."""
    db = isolated_app.WEARABLE_FACTS_DB_FILE
    payload = {"sleep_summary": [{"date": _day(3), "source": {"provider": "oura"}, "duration_minutes": 40,
                                  "end_time": f"{_day(3)}T07:00:00Z"}]}
    isolated_app._store_wearable_facts_from_open_wearables(payload)
    got = preferred_daily_facts(db, {"sleep_duration"}, _day(3), _day(3), profile_key=isolated_app._open_wearables_profile_key())
    assert got[_day(3)]["sleep_duration"]["value"] == 40 and got[_day(3)]["sleep_duration"]["provider"] == "oura", got


def test_gap_filling_provider_brings_its_whole_night(app_db):
    """Oura fragment plus stages, WHOOP real night plus stages: WHOOP fills the gap with its own stages."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "sleep_duration", 40), _fact(TODAY, "oura", "sleep_deep_duration", 10),
        _fact(TODAY, "whoop", "sleep_duration", 300), _fact(TODAY, "whoop", "sleep_deep_duration", 60),
        _fact(TODAY, "whoop", "sleep_avg_heart_rate", 72),
    ])
    row = app_db._sleep_rows_from_facts(days=1)[0]
    assert (row["provider"], row["sleep_duration_min"], row["sleep_deep_min"], row["avg_heart_rate"]) == ("whoop", 300, 60, 72), row


def test_sleep_summary_reports_missing_heart_rate_and_efficiency_as_none(app_db):
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [_fact(TODAY, "oura", "sleep_duration", 420)])
    body = app_db.app.test_client().get("/api/sleep/summary").get_json()
    assert body["last_night"]["avg_heart_rate"] is None and body["last_night"]["efficiency"] is None, body["last_night"]
    assert body["week_average"]["avg_heart_rate"] is None, body["week_average"]
