"""PER-1588 Codex P2 follow-up: the record that decides a sleep conflict is the record reported, for every caller. Synthetic data only."""

from __future__ import annotations

from datetime import date

import pytest

from wearable_fact_store import WearableDailyFact, preferred_daily_facts, upsert_daily_facts

DAY = date.today().isoformat()
SLEEP_ROWS_METRICS = {  # app._sleep_rows_from_facts (readiness, sleep debt, workout guidance)
    "sleep_duration", "sleep_deep_duration", "sleep_rem_duration", "sleep_light_duration",
    "sleep_awake_duration", "sleep_efficiency", "sleep_avg_heart_rate", "sleep_time_in_bed",
}
TODAY_ENDPOINT_METRICS = {  # app.wearables_today -> _domain_sources(facts): the sources line
    "resting_heart_rate", "steps", "active_calories", "active_minutes", "temperature_deviation",
    "sleep_duration", "recovery_score", "heart_rate_variability", "sleep_hrv_rmssd", "sleep_hrv_sdnn",
}
PROOF_METRICS = {  # app._PROOF_FACT_METRICS
    "sleep_duration", "recovery_score", "resting_heart_rate", "steps", "active_minutes", "active_calories",
    "heart_rate_variability", "sleep_hrv_rmssd", "sleep_hrv_sdnn",
}
FINGERPRINT_METRICS = {"recovery_score", "resting_heart_rate", "sleep_duration", "sleep_efficiency", "active_minutes", "steps"}


def _fact(provider, metric, value, *, sid, updated, system="open_wearables"):
    return WearableDailyFact(
        date=DAY, provider_id=provider, source_label=provider.title(), metric=metric, value=value,
        source_system=system, source_record_kind="summary", source_id=sid, source_provider=provider,
        freshness="fresh", used_for_recommendation=True, updated_at=f"2026-10-29T00:00:{updated:02d}",
    )


def _read(db, metrics):
    return preferred_daily_facts(db, set(metrics), DAY, DAY).get(DAY, {})


# 1. The no-conflict decision must be justified by the duration that is reported.
def test_an_unlabelled_night_never_reports_a_duration_that_conflicts_with_another_provider(tmp_path):
    db = str(tmp_path / "f.db")
    upsert_daily_facts(db, [
        _fact("oura", "sleep_duration", 470, sid="o-full", updated=1),
        _fact("oura", "sleep_efficiency", 91, sid="o-full", updated=1),
        _fact("oura", "sleep_avg_heart_rate", 52, sid="o-full", updated=1),
        _fact("oura", "sleep_duration", 600, sid="o-thin", updated=5),  # newest, duration only
        _fact("whoop", "sleep_duration", 480, sid="w", updated=2),
    ])
    d = _read(db, SLEEP_ROWS_METRICS)["sleep_duration"]
    # oura's first record (600) is 120 min over whoop, so the conflict is decided on it and the shorter whoop night is used;
    # an unlabelled 600 would exceed what justified the choice.
    assert d["value"] in (470, 480), d
    if d["value"] == 480:
        assert d["conflict_state"] == "conservative_value_used"


# 2. One night, one answer: the provider and label must not depend on which metrics a caller asked for.
@pytest.mark.parametrize("other", [TODAY_ENDPOINT_METRICS, PROOF_METRICS, FINGERPRINT_METRICS, {"sleep_duration"}])
def test_the_codex_night_has_the_same_provider_and_label_for_every_caller(tmp_path, other):
    db = str(tmp_path / "f.db")
    upsert_daily_facts(db, [
        _fact("whoop", "sleep_duration", 540, sid="full", updated=1),
        _fact("whoop", "sleep_efficiency", 90, sid="full", updated=1),
        _fact("whoop", "sleep_avg_heart_rate", 55, sid="full", updated=1),
        _fact("oura", "sleep_duration", 480, sid="o", updated=1),
        _fact("whoop", "sleep_duration", 300, sid="thin", updated=5),
    ])
    readiness = _read(db, SLEEP_ROWS_METRICS)["sleep_duration"]
    shown = _read(db, other)["sleep_duration"]
    pick = lambda f: (f["provider"], f["value"], f["conflict_state"])
    assert pick(shown) == pick(readiness)


# 3. The bot's night under every ordering: never 540, and a conservative label always carries the minimum.
@pytest.mark.parametrize("thin_newer", [True, False])
@pytest.mark.parametrize("full_system", ["open_wearables", "archive"])
@pytest.mark.parametrize("third", [None, 350, 700])
def test_the_codex_night_never_reports_540_under_any_ordering(tmp_path, thin_newer, full_system, third):
    db = str(tmp_path / "f.db")
    facts = [
        _fact("whoop", "sleep_duration", 540, sid="archive:full" if full_system == "archive" else "full", updated=3, system=full_system),
        _fact("whoop", "sleep_efficiency", 90, sid="archive:full" if full_system == "archive" else "full", updated=3, system=full_system),
        _fact("whoop", "sleep_avg_heart_rate", 55, sid="archive:full" if full_system == "archive" else "full", updated=3, system=full_system),
        _fact("oura", "sleep_duration", 480, sid="o", updated=2),
        _fact("whoop", "sleep_duration", 300, sid="thin", updated=5 if thin_newer else 1),
    ]
    if third:
        facts.append(_fact("apple", "sleep_duration", third, sid="a", updated=2))
    upsert_daily_facts(db, facts)
    d = _read(db, SLEEP_ROWS_METRICS)["sleep_duration"]
    assert d["value"] != 540
    assert d["value"] <= 480 or d["provider"] == "apple" and d["value"] == third <= 480


# 4. End to end: the sources line on /api/wearables/today must name the provider whose night is shown.
def test_wearables_today_sources_line_names_the_provider_of_the_shown_night(isolated_app, isolated_client, monkeypatch):
    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", lambda **_: {"errors": {"config": "missing"}})
    monkeypatch.setattr(isolated_app, "_cached_wttr", lambda *_a, **_k: {"available": False})
    pk = isolated_app._open_wearables_profile_key()
    upsert_daily_facts(isolated_app.WEARABLE_FACTS_DB_FILE, [
        _fact("whoop", "sleep_duration", 540, sid="full", updated=1),
        _fact("whoop", "sleep_efficiency", 90, sid="full", updated=1),
        _fact("whoop", "sleep_avg_heart_rate", 55, sid="full", updated=1),
        _fact("oura", "sleep_duration", 480, sid="o", updated=1),
        _fact("whoop", "sleep_duration", 300, sid="thin", updated=5),
    ], profile_key=pk)
    body = isolated_client.get("/api/wearables/today").get_json()
    shown = body["sleep_duration_min"]
    src = body["sources"].get("sleep") or {}
    print("shown", shown, "sources.sleep", src)
    assert shown in (300, 480)
    assert src.get("provider") == ("oura" if shown == 480 else "whoop"), (shown, src)
    assert (src.get("conflict_state") == "conservative_value_used") == (shown == 300), (shown, src)


# On an unforced night a provider's longer, fuller record must not lift the duration above its first stored record.
def test_an_unforced_night_never_reports_more_than_the_providers_first_stored_record(tmp_path):
    db = str(tmp_path / "f.db")
    upsert_daily_facts(db, [
        _fact("oura", "sleep_duration", 380, sid="o-summary", updated=9),  # newest: first in the stored order
        _fact("oura", "sleep_duration", 430, sid="o-session", updated=1),  # fuller: carries efficiency and heart rate
        _fact("oura", "sleep_efficiency", 90, sid="o-session", updated=1),
        _fact("oura", "sleep_avg_heart_rate", 55, sid="o-session", updated=1),
    ])
    assert _read(db, SLEEP_ROWS_METRICS)["sleep_duration"]["value"] == 380


@pytest.mark.parametrize("other", [None, 420, 470])
def test_a_providers_longer_session_record_never_lifts_the_plan(isolated_app, monkeypatch, other):
    """Oura's recovery summary says 380 min, its session 430; the plan must not move up from 'recovery'."""
    from datetime import timedelta
    rank = {"intensity": 0, "moderate": 1, "recovery": 2, "rest": 3}
    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", lambda **_: {"errors": {"config": "missing"}})
    monkeypatch.setattr(isolated_app, "_cached_wttr", lambda *_a, **_k: {"available": False})
    monkeypatch.setattr(isolated_app, "WORKOUTS", [])
    monkeypatch.setattr(isolated_app, "SORENESS_DATA", [])
    monkeypatch.setattr(isolated_app, "RECOVERY_DATA", [])
    recovery = [{"id": "oura-rec-1", "date": DAY, "recorded_at": f"{DAY}T08:00:00Z",
                 "source": {"provider": "oura"}, "sleep_duration_seconds": 380 * 60}]
    session = {"id": "oura-ev-1", "end_time": f"{DAY}T07:00:00Z", "duration_min": 430, "avg_hr": 55,
               "efficiency_percent": 90, "source": {"provider": "oura"}, "is_nap": False}
    extra = {"sleep_summary": [{"date": DAY, "source": {"provider": "whoop"}, "duration_minutes": other, "avg_heart_rate_bpm": 56,
                                "time_in_bed_minutes": other + 25, "efficiency_percent": 91, "end_time": f"{DAY}T07:00:00Z"}]} if other else {}
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    def plan(payload):
        isolated_app._store_wearable_facts_from_open_wearables({"fetched_at": f"{tomorrow}T12:00:00Z", **payload})
        return isolated_app.app.test_client().get("/api/recommendation/smart").get_json()["recommendation"]

    alone = plan({"recovery_summary": recovery, **extra})
    both = plan({"recovery_summary": recovery, "sleep": {"events": [session]}, **extra})
    assert rank[both] >= rank[alone], (alone, both)
