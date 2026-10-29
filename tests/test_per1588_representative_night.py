"""PER-1588 Codex P2 follow-up: a sleep night is judged and reported from each provider's representative record, for every caller. Synthetic data only."""

from __future__ import annotations

import itertools
import random
from datetime import date

import pytest

from wearable_fact_store import WearableDailyFact, preferred_daily_facts, upsert_daily_facts

DAY = date.today().isoformat()
TOL = 60.0
MIN_NIGHT = 60.0
PREF = ["oura", "whoop", "apple"]
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
    # oura's representative (470) is within tolerance of whoop 480, so "no conflict" was decided on 470;
    # reporting 600 unlabelled exceeds what justified the choice and is 120 min over whoop.
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
    if d["conflict_state"] == "conservative_value_used":
        reps = [480, 300 if full_system == "archive" else 540] + ([third] if third else [])
        assert d["value"] == min(reps)


# 4. Randomised invariants against an independent oracle of the stated rule.
def _oracle_reps(records, requested):
    sleep_req = {m for m in requested if m.startswith("sleep_")}
    reps = {}
    for p in PREF:
        mine = [r for r in records if r["provider"] == p and r["metrics"].get("sleep_duration", 0) >= MIN_NIGHT]
        if not mine:
            continue
        key = lambda r: (r["archive"], -len(sleep_req & set(r["metrics"])), -r["updated"], r["sid"])
        reps[p] = min(mine, key=key)
    return reps


def _random_night(rng):
    """Each record's values are unique, so a reported value names the record that supplied it."""
    records, n = [], 0
    plausible = iter(rng.sample(range(200, 640), 9))
    for p in rng.sample(PREF, rng.randint(1, 3)):
        for _ in range(rng.randint(1, 3)):
            n += 1
            metrics = {}
            if rng.random() < 0.85:
                metrics["sleep_duration"] = 10 + n if rng.random() < 0.2 else next(plausible)
            for m, off in (("sleep_efficiency", 1), ("sleep_avg_heart_rate", 2), ("sleep_deep_duration", 3)):
                if rng.random() < 0.5:
                    metrics[m] = n * 1000 + off
            if metrics:
                archive = rng.random() < 0.25
                records.append({"provider": p, "sid": f"{'archive:' if archive else ''}{p}-{n}", "archive": int(archive),
                                "updated": rng.randint(1, 50), "metrics": metrics})
    return records


def _store(db, records):
    upsert_daily_facts(db, [
        _fact(r["provider"], m, v, sid=r["sid"], updated=r["updated"], system="archive" if r["archive"] else "open_wearables")
        for r in records for m, v in r["metrics"].items()
    ])


@pytest.mark.parametrize("seed", range(400))
def test_random_nights_keep_the_conservative_invariants(tmp_path, seed):
    rng = random.Random(seed)
    records = _random_night(rng)
    if not records:
        return
    tag = {(m, v): r["sid"] for r in records for m, v in r["metrics"].items()}
    db = str(tmp_path / "f.db")
    _store(db, records)
    requested = {"sleep_duration", "sleep_efficiency", "sleep_avg_heart_rate", "sleep_deep_duration"}
    night = _read(db, requested)
    reps = _oracle_reps(records, requested)
    d = night.get("sleep_duration")
    if not reps:
        return
    assert d is not None, (seed, records)
    rep_minutes = {p: r["metrics"]["sleep_duration"] for p, r in reps.items()}
    # (d) a fragment never feeds the night
    assert d["value"] >= MIN_NIGHT, (seed, records, night)
    if d["conflict_state"] == "conservative_value_used":
        # (b) the label carries the minimum of the competing representatives
        assert d["value"] == min(rep_minutes.values()), (seed, records, night)
        # (a) every sleep metric of a forced night comes from the record that supplied the duration
        for m, f in night.items():
            if m != "sleep_duration":
                assert tag[(m, f["value"])] == reps[d["provider"]]["sid"], (seed, m, records, night)
    else:
        # (c) an unlabelled duration never exceeds another provider's representative by more than the tolerance
        for p, v in rep_minutes.items():
            if p != d["provider"]:
                assert d["value"] - v <= TOL, (seed, p, records, night)


# 5. End to end: the sources line on /api/wearables/today must name the provider whose night is shown.
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


# Round 3: on an unforced night a provider's longer, fuller record must not lift the duration above its first stored record.
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
