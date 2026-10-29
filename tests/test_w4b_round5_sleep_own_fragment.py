"""Round 5 (PR #360): a sleep conflict or gap fill uses the used provider's best plausible record,
never that provider's own sub-hour fragment row. Synthetic data only."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from wearable_fact_store import WearableDailyFact, preferred_daily_facts, upsert_daily_facts

TODAY = date.today().isoformat()
_RANK = {"intensity": 0, "moderate": 1, "recovery": 2, "rest": 3}
SLEEP = {"sleep_duration", "sleep_deep_duration", "sleep_avg_heart_rate", "sleep_efficiency"}


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



def _night(app_db, day=TODAY, metrics=SLEEP):
    db = app_db.WEARABLE_FACTS_DB_FILE
    return preferred_daily_facts(db, metrics, day, day, app_db._open_wearables_profile_key()).get(day, {})


def test_probe_live_oura_fragment_over_archive_oura_night(app_db):
    """Same provider, live 40 vs archive 420, nobody else: is the archive night used? (edge probe)"""
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(TODAY, "oura", "sleep_duration", 40),
        _fact(TODAY, "oura", "sleep_duration", 420, system="archive", kind="event", source_id="archive:oura_sleep")])
    night = _night(app_db)["sleep_duration"]
    assert night["value"] == 420, night


def test_probe_oura_summary_fragment_with_its_own_event_night_and_whoop(app_db):
    """Oura summary 40, Oura event 400 and WHOOP 420 on one date: some real night must be used (edge probe)."""
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(TODAY, "oura", "sleep_duration", 40),
        _fact(TODAY, "oura", "sleep_duration", 400, kind="event", source_id="e1"),
        _fact(TODAY, "whoop", "sleep_duration", 420)])
    night = _night(app_db)["sleep_duration"]
    assert night["value"] >= 60, night


def _hub_payload(day, fragment_seconds=2400, event_min=420, oura_min=None):
    payload = {
        "recovery_summary": [{"date": day, "source": {"provider": "whoop"}, "sleep_duration_seconds": fragment_seconds,
                              "recorded_at": f"{day}T12:00:00Z"}],
        "sleep": {"events": [{"id": "whoop-ev-1", "end_time": f"{day}T12:00:00Z", "duration_min": event_min,
                              "source": {"provider": "whoop"}, "is_nap": False}]},
    }
    if oura_min is not None:
        payload["sleep_summary"] = [{"date": day, "source": {"provider": "oura"}, "duration_minutes": oura_min,
                                     "end_time": f"{day}T12:00:00Z"}]
    return payload


def test_probe_hub_rows_for_one_provider_fragment_and_night(isolated_app):
    import sqlite3
    db = isolated_app.WEARABLE_FACTS_DB_FILE
    day = _day(1)
    isolated_app._store_wearable_facts_from_open_wearables(_hub_payload(day))
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT provider_id, source_provider, source_record_kind, source_id, value_json FROM wearable_daily_facts "
                            "WHERE metric = 'sleep_duration' AND date = ?", (day,)).fetchall()
    print("STORED", rows)
    got = preferred_daily_facts(db, {"sleep_duration"}, day, day, profile_key=isolated_app._open_wearables_profile_key())
    print("PICKED", got.get(day, {}).get("sleep_duration", {}).get("value"))
    assert got[day]["sleep_duration"]["value"] >= 60, rows


def test_probe_hub_conflict_branch_with_own_fragment(isolated_app):
    import sqlite3
    db = isolated_app.WEARABLE_FACTS_DB_FILE
    day = _day(1)
    isolated_app._store_wearable_facts_from_open_wearables(_hub_payload(day, oura_min=500))
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT provider_id, source_provider, source_record_kind, source_id, value_json FROM wearable_daily_facts "
                            "WHERE metric = 'sleep_duration' AND date = ?", (day,)).fetchall()
    print("STORED", rows)
    got = preferred_daily_facts(db, {"sleep_duration"}, day, day, profile_key=isolated_app._open_wearables_profile_key())
    print("PICKED", got.get(day, {}).get("sleep_duration"))
    assert got[day]["sleep_duration"]["value"] >= 60, rows


def _hub_shaped_night(day, whoop_summary, whoop_event, oura=None):
    """The rows the OW store writes for one date (see the hub probes above): WHOOP recovery-summary
    sleep (summary kind), WHOOP main sleep event (event kind), optionally an Oura sleep summary."""
    rows = [_fact(day, "whoop", "sleep_duration", whoop_summary, source_id=f"derived:summary:sleep:{day}T12:00:00Z"),
            _fact(day, "whoop", "sleep_duration", whoop_event, kind="event", source_id=f"whoop-ev-{day}")]
    if oura is not None:
        rows.append(_fact(day, "oura", "sleep_duration", oura, source_id="sleep-summary"))
    return rows


def test_conflict_with_the_used_providers_own_fragment_keeps_the_conservative_night(app_db):
    """Oura 500 vs WHOOP 300 is a conflict, so WHOOP's 300 must be used; WHOOP's own 40-min summary row must not."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    facts = []
    for i in range(7):
        facts += _hub_shaped_night(_day(i), 40, 300, oura=500)
    upsert_daily_facts(db, facts)
    night = _night(app_db, metrics={"sleep_duration"})["sleep_duration"]
    debt = app_db.calculate_sleep_debt(days=7)
    body = app_db.app.test_client().get("/api/sleep/summary").get_json()
    assert (night["value"], debt["nights_under"], debt["debt_minutes"]) == (300, 7, 840), (night, debt)
    assert body["data_quality"]["status"] != "inconsistent", body["data_quality"]


def test_conflict_with_own_fragment_does_not_lift_the_plan_end_to_end(app_db):
    """A: hub-shaped nights 2-6 (Oura 500, WHOOP event 300, WHOOP summary 40) + Oura 480 last two nights.
    B: the same without WHOOP's 40-min summary rows. A must be no less conservative than B."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    client = app_db.app.test_client()
    recent = [_fact(_day(i), "oura", "sleep_duration", 480) for i in (0, 1)]
    older_b = []
    for i in range(2, 7):
        older_b += [_fact(_day(i), "oura", "sleep_duration", 500, source_id="sleep-summary"),
                    _fact(_day(i), "whoop", "sleep_duration", 300, kind="event", source_id=f"whoop-ev-{_day(i)}")]
    upsert_daily_facts(db, recent + older_b)
    b = client.get("/api/recommendation/smart").get_json()
    upsert_daily_facts(db, [_fact(_day(i), "whoop", "sleep_duration", 40, source_id=f"derived:summary:sleep:{_day(i)}T12:00:00Z")
                            for i in range(2, 7)])
    a = client.get("/api/recommendation/smart").get_json()
    summary = lambda x: (x["recommendation"], x["readiness"], x["readiness_factors"]["sleep_debt"]["debt_minutes"])
    assert _RANK[a["recommendation"]] >= _RANK[b["recommendation"]], (summary(a), summary(b))
    assert a["readiness_factors"]["sleep_debt"]["debt_minutes"] >= b["readiness_factors"]["sleep_debt"]["debt_minutes"], (summary(a), summary(b))


def test_used_providers_plausible_record_brings_its_own_stages(app_db):
    """WHOOP's 40-min summary row carries its own stages; the night must be WHOOP's 300-min event record,
    stages included, or the stage-sum check rejects the night (duration_stage_conflict)."""
    day = _day(1)
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(day, "oura", "sleep_duration", 500, source_id="sleep-summary"),
        _fact(day, "whoop", "sleep_duration", 40, source_id="whoop-sum"),
        _fact(day, "whoop", "sleep_deep_duration", 10, source_id="whoop-sum"),
        _fact(day, "whoop", "sleep_rem_duration", 10, source_id="whoop-sum"),
        _fact(day, "whoop", "sleep_light_duration", 20, source_id="whoop-sum"),
        _fact(day, "whoop", "sleep_duration", 300, kind="event", source_id="whoop-ev"),
        _fact(day, "whoop", "sleep_deep_duration", 60, kind="event", source_id="whoop-ev"),
        _fact(day, "whoop", "sleep_rem_duration", 70, kind="event", source_id="whoop-ev"),
        _fact(day, "whoop", "sleep_light_duration", 170, kind="event", source_id="whoop-ev")])
    row = app_db._sleep_rows_from_facts(days=2)[0]
    assert (row["provider"], row["sleep_duration_min"], row["deep_sleep_min"]) == ("whoop", 300, 60), row
    assert app_db._is_valid_nightly_sleep_row(row), app_db._sleep_row_inconsistency_reason(row)


def test_fingerprint_follows_the_hrv_series_the_plan_reads(app_db):
    """Fails before round 5: sleep_hrv_rmssd (the series _hrv_facts reads) was not fingerprinted."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(_day(i), "oura", "sleep_hrv_rmssd", 50 + i) for i in range(5)])
    before = app_db._workout_recommendation_fingerprint()
    upsert_daily_facts(db, [_fact(_day(0), "oura", "sleep_hrv_rmssd", 30)])
    assert app_db._workout_recommendation_fingerprint() != before


def test_today_and_trends_hrv_come_from_the_series_the_plan_reads(app_db):
    """Fails before round 5: the first HRV metric with any fact won, so a stray old fact hid a fresh series."""
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(_day(i), "oura", "sleep_hrv_rmssd", 60 + i) for i in range(5)]
                       + [_fact(_day(6), "oura", "heart_rate_variability", 20)])
    client = app_db.app.test_client()
    assert client.get("/api/wearables/today").get_json()["hrv"] == 60
    series = {r["day"]: r["hrv"] for r in client.get("/api/wearables/trends").get_json()["series"]}
    assert [series.get(_day(i)) for i in (4, 3, 2, 1, 0)] == [64, 63, 62, 61, 60]
    assert series.get(_day(6)) is None
