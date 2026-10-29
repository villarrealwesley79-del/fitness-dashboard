"""PER-1591: stored Open Wearables workouts count toward training load, never less cautious than before."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import open_wearables_hub as hub
from js_runtime import run_app_js
from wearable_fact_store import WearableDailyFact, upsert_daily_facts

TODAY = datetime.now().date()


def day(n):
    return (TODAY - timedelta(days=n)).isoformat()


def lift(n, weight=100, reps=10, sets=3):
    return {"date": day(n), "session_type": "push", "exercises": [{"machine": "Bench", "sets": [{"weight_lbs": weight, "reps": reps}] * sets}]}


# Owner-like month: three logged lifts a week (see EMPTY_STORE_ACWR for the numbers before this change).
OWNER_LIFTS = [lift(n) for n in (2, 5, 9, 12, 16, 23)]


def store(module, n, label, minutes, *, provider="whoop", sid=None, hour=12, category=None):
    observed = f"{day(n)}T{hour:02d}:00:00Z"
    upsert_daily_facts(module.WEARABLE_FACTS_DB_FILE, [
        WearableDailyFact(
            date=day(n), provider_id=provider, source_label=provider, metric="workout_duration", value=float(minutes), unit="min",
            source_provider=provider, source_system="open_wearables", source_record_kind="event",
            source_id=sid or f"{provider}-{n}-{hour}-{label}", observed_at=observed, original_label=label,
            category=category or hub._workout_category(label),
        )
    ], profile_key=module._open_wearables_profile_key())


@pytest.fixture()
def app_module(isolated_app, monkeypatch):
    monkeypatch.setattr(isolated_app, "WORKOUTS", [])
    monkeypatch.setattr(isolated_app, "CARDIO_DATA", [])
    return isolated_app


def kept_ids(module, workouts):
    return [row["id"] for row in module._open_wearables_load_sessions(workouts)]


def test_empty_store_leaves_every_existing_key_as_it_was(app_module):
    # Values taken from the code before this change, on this same fixture.
    assert app_module.calculate_acwr(OWNER_LIFTS) == {
        "acwr": 1.143, "acute_load": 6000, "chronic_load": 5250, "risk": "optimal", "message": "ACWR is in the optimal range.",
        "acwr_series": {"tonnage": {"acwr": 1.143, "acute_load": 6000, "chronic_load": 5250}, "minutes": None, "used": "tonnage"},
    }
    assert app_module.calculate_acwr([])["acwr"] == 0.0
    assert app_module.calculate_acwr([lift(40)])["message"] == "No workouts in the last 28 days; ACWR indicates detraining."
    assert app_module._cardio_with_open_wearables() == []


def test_unreadable_store_behaves_like_an_empty_one(app_module, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("fact store unreachable")

    monkeypatch.setattr(hub, "stored_workouts", broken)
    assert app_module.calculate_acwr(OWNER_LIFTS)["acwr_series"]["used"] == "tonnage"
    assert app_module._open_wearables_load_sessions(OWNER_LIFTS) == []


def test_minutes_series_wins_only_when_its_ratio_is_higher(app_module):
    # Light logged lifting, but a heavy watch week on top of a quiet month: minutes is the more cautious series.
    store(app_module, 20, "Running", 30)
    for n in (1, 2, 3, 4):
        store(app_module, n, "Running", 90)
    result = app_module.calculate_acwr(OWNER_LIFTS)
    series = result["acwr_series"]
    assert series["used"] == "minutes"
    assert series["minutes"]["acwr"] > series["tonnage"]["acwr"] == 1.143
    assert result["acwr"] == series["minutes"]["acwr"] and result["risk"] == "high"
    assert (result["acute_load"], result["chronic_load"]) == (series["minutes"]["acute_load"], series["minutes"]["chronic_load"])

    # A quiet watch month on top of the same lifting: tonnage stays the reported series, exactly as before.
    quiet = app_module.calculate_acwr(OWNER_LIFTS, wearable_sessions=[{"date": day(2), "duration_minutes": 20}, {"date": day(16), "duration_minutes": 60}])
    assert quiet["acwr_series"]["used"] == "tonnage"
    assert {k: quiet[k] for k in ("acwr", "acute_load", "chronic_load", "risk", "message")} == {k: app_module.calculate_acwr(OWNER_LIFTS, wearable_sessions=[])[k] for k in ("acwr", "acute_load", "chronic_load", "risk", "message")}


def test_watch_sessions_alone_count_when_nothing_is_logged(app_module):
    store(app_module, 20, "Cycling", 40)
    for n in (1, 2, 3):
        store(app_module, n, "Cycling", 60)
    result = app_module.calculate_acwr([])
    assert result["acwr_series"]["used"] == "minutes" and result["acwr_series"]["tonnage"] is None
    assert result["acwr"] == result["acwr_series"]["minutes"]["acwr"] > 0


@pytest.mark.parametrize("recent_minutes", [0, 20, 60, 120, 240])
def test_the_plan_is_never_less_cautious_than_logged_data_alone(app_module, recent_minutes):
    """Same logged data: the reported ratio never drops and the training category never gets easier."""
    store(app_module, 20, "Running", 30)
    if recent_minutes:
        store(app_module, 1, "Running", recent_minutes)
    alone = app_module.calculate_acwr(OWNER_LIFTS, wearable_sessions=[])
    both = app_module.calculate_acwr(OWNER_LIFTS)
    assert both["acwr"] >= alone["acwr"]
    rank = {"recovery": 0, "moderate": 1, "intensity": 2}
    for readiness in (60, 75, 90):
        before, _ = app_module._training_recommendation_from_factors(readiness, acwr_data=alone)
        after, _ = app_module._training_recommendation_from_factors(readiness, acwr_data=both)
        assert rank[after] <= rank[before]


def test_a_logged_workout_absorbs_one_strength_session_and_never_a_cardio_one(app_module):
    store(app_module, 2, "Traditional Strength Training", 50, sid="a", hour=9)
    store(app_module, 2, "Functional Strength Training", 40, sid="b", hour=18)
    store(app_module, 2, "Running", 30, sid="c", hour=7)
    kept = app_module._open_wearables_load_sessions([lift(2)])
    # History walks rows newest first, so the 18:00 session is the one folded into the lift.
    assert sorted(r["activity_type"] for r in kept) == ["Running", "Traditional Strength Training"]
    # Two logged lifts that day absorb both sessions; with none logged both count.
    assert [r["activity_type"] for r in app_module._open_wearables_load_sessions([lift(2), lift(2)])] == ["Running"]
    assert len(app_module._open_wearables_load_sessions([])) == 3


def test_cardio_already_logged_is_not_counted_twice(app_module):
    store(app_module, 1, "Outdoor Run", 40, sid="r")
    store(app_module, 1, "Cycling", 40, sid="c")
    app_module.CARDIO_DATA.append({"date": day(1), "activity_type": "Running", "duration_minutes": 40})
    # Fatigue rows and the ACWR minutes both drop the logged copy only (one entry covers one session).
    assert [r["activity_type"] for r in app_module._unlogged_watch_cardio(app_module._open_wearables_load_sessions([]))] == ["Cycling"]
    assert [r["activity_type"] for r in app_module._cardio_with_open_wearables()] == ["Running", "Cycling"]


def test_load_keeps_exactly_the_sessions_history_shows_beside_logged_lifts(app_module):
    """Parity: the kept set equals the Open Wearables rows left visible by mergeStrengthHistorySources."""
    store(app_module, 2, "Traditional Strength Training", 50, sid="a", hour=9)
    store(app_module, 2, "Strength Training", 45, sid="b", hour=12, provider="apple")
    store(app_module, 2, "Functional Strength Training", 40, sid="c", hour=18)
    store(app_module, 5, "Weight Training", 30, sid="d")
    store(app_module, 7, "Traditional Strength Training", 30, sid="e")
    store(app_module, 5, "Running", 30, sid="f")
    store(app_module, 9, "Hiking", 90, sid="g")
    app_module.WORKOUTS.extend([lift(2), lift(2), lift(5)])
    client = app_module.app.test_client()
    lifts = client.get("/api/history").get_json()["workouts"]
    rows = client.get("/api/open-wearables/workouts").get_json()["workouts"]
    assert len(lifts) == 3 and len(rows) >= 6
    shown = run_app_js(
        ["mergeStrengthHistorySources", "normalizeOpenWearablesHistoryRow"],
        f"""
const lifts = {json.dumps(lifts)};
const rows = {json.dumps(rows)}.map(e.normalizeOpenWearablesHistoryRow);
const merged = e.mergeStrengthHistorySources(lifts, rows);
process.stdout.write(JSON.stringify(merged.filter((r) => r.source === 'open_wearables').map((r) => r.id)));
""",
    )
    assert sorted(shown) == sorted(kept_ids(app_module, app_module.WORKOUTS))
    assert 0 < len(shown) < len(rows)


def test_a_stored_run_raises_leg_fatigue(app_module):
    client = app_module.app.test_client()
    before = {m: d["cardio_fatigue"] for m, d in client.get("/api/muscle-fatigue").get_json().items()}
    store(app_module, 1, "Running", 60)
    after = {m: d["cardio_fatigue"] for m, d in client.get("/api/muscle-fatigue").get_json().items()}
    assert before["calves"] == 0 and after["calves"] > 0 and after["quads"] > 0
    assert after["chest"] == 0


def test_dashboard_and_smart_recommendation_report_both_series(app_module):
    store(app_module, 20, "Running", 30)
    for n in (1, 2, 3, 4):
        store(app_module, n, "Running", 90)
    app_module.WORKOUTS.extend(OWNER_LIFTS)
    client = app_module.app.test_client()
    assert client.get("/api/acwr").get_json()["acwr_series"]["used"] == "minutes"
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.get_json()["readiness_factors"]["acwr"]["acwr_series"]["minutes"]["acwr"] > 1.5


def test_fingerprint_moves_with_a_new_watch_session_and_not_without_one(app_module):
    first = app_module._workout_recommendation_fingerprint()
    assert app_module._workout_recommendation_fingerprint() == first
    store(app_module, 1, "Running", 60)
    assert app_module._workout_recommendation_fingerprint() != first


# Set validation (Related: PER-1588)

def _workout(sets, machine="Bench"):
    return {"date": day(0), "session_type": "push", "duration_minutes": 30, "exercises": [{"machine": machine, "sets": sets}]}


@pytest.mark.parametrize("sets", [[{}], [{"weight_lbs": 100}], [{"weight_lbs": None, "reps": 5}], [{"weight_lbs": "100", "reps": 5}], [{"weight_lbs": True, "reps": 5}], [5], "x"])
def test_add_workout_rejects_bad_sets_and_stores_nothing(app_module, sets):
    response = app_module.app.test_client().post("/api/add-workout", json=_workout(sets))
    assert response.status_code == 400
    assert app_module.WORKOUTS == []


def test_add_workout_still_takes_bodyweight_zero(app_module):
    response = app_module.app.test_client().post("/api/add-workout", json=_workout([{"weight_lbs": 0, "reps": 12}]))
    assert response.status_code == 200 and len(app_module.WORKOUTS) == 1


def test_complete_workout_keeps_accepting_bad_sets_and_every_reader_survives(app_module):
    client = app_module.app.test_client()
    for sets in ([{}], [{"weight_lbs": None, "reps": 8}], [{"weight_lbs": 0, "reps": 8}, {"weight_lbs": 135, "reps": 5}]):
        response = client.post("/api/complete-workout", json={**_workout(sets), "client_workout_id": f"w{len(str(sets))}"})
        assert response.status_code == 200, response.get_json()
    for url in ("/api/dashboard", "/api/acwr", "/api/history", "/api/history-all", "/api/export-md", "/api/muscle-fatigue", "/api/recommendation/smart"):
        assert client.get(url).status_code == 200, url
    prs = app_module.calculate_personal_records(app_module.WORKOUTS)
    assert prs["Bench"]["all_time"] == round(app_module.calculate_e1rm(135, 5), 1)


def test_readers_skip_an_exercise_without_a_string_machine(app_module):
    app_module.WORKOUTS.append({"date": day(1), "session_type": "push", "exercises": [
        {"sets": [{"weight_lbs": 50, "reps": 5}]},
        {"machine": {"x": 1}, "sets": [{"weight_lbs": 50, "reps": 5}]},
        {"machine": "Row", "sets": [{"weight_lbs": 50, "reps": 5}]},
    ]})
    client = app_module.app.test_client()
    export = client.get("/api/export-md")
    assert export.status_code == 200 and "| Row |" in export.get_data(as_text=True)
    assert list(app_module.calculate_personal_records(app_module.WORKOUTS)) == ["Row"]
    assert client.get("/api/dashboard").status_code == 200


# Round 2: the minutes series counts every session once

def timed_lift(n):
    return {**lift(n), "duration_minutes": 55}


# Owner-like month: 3 logged lifts a week, each also recorded by the watch (folded), one 30-minute run a week.
OWNER_LIFT_DAYS = (1, 3, 5, 8, 10, 12, 15, 17, 19, 22, 24, 26)


def owner_month(module):
    module.WORKOUTS[:] = [timed_lift(n) for n in OWNER_LIFT_DAYS]
    for n in OWNER_LIFT_DAYS:
        store(module, n, "Traditional Strength Training", 55, hour=18)
    for n in (9, 16, 23):
        store(module, n, "Running", 30, hour=7)


def test_one_extra_30_minute_run_does_not_force_a_recovery_day(app_module):
    owner_month(app_module)
    for n in (2, 4):
        store(app_module, n, "Running", 30, hour=7)
    acwr = app_module.calculate_acwr(app_module.WORKOUTS)
    assert acwr["acwr_series"]["minutes"]["acwr"] < 1.3  # watch-only minutes read 60/44 = 1.37
    assert app_module._training_recommendation_from_factors(None, acwr_data=acwr)[0] == "moderate"
    assert app_module.app.test_client().get("/api/recommendation/smart").get_json()["recommendation"] == "moderate"


def test_a_logged_lift_without_a_duration_counts_the_watch_session_folded_into_it(app_module):
    store(app_module, 2, "Traditional Strength Training", 50)
    store(app_module, 20, "Running", 30)
    result = app_module.calculate_acwr([lift(2)])
    assert result["acwr_series"]["minutes"]["acute_load"] == 50
    # No watch data at all: the minutes series stays out, so a logged-only user sees the same numbers as before.
    assert app_module.calculate_acwr([timed_lift(2)], wearable_sessions=[])["acwr_series"]["minutes"] is None


def test_one_logged_run_drops_only_one_same_day_watch_run(app_module):
    store(app_module, 1, "Running", 30, sid="am", hour=7)
    store(app_module, 1, "Running", 45, sid="pm", hour=18)
    app_module.CARDIO_DATA.append({"date": day(1), "activity_type": "Outdoor Run", "duration_minutes": 30})
    kept = app_module._unlogged_watch_cardio(app_module._open_wearables_load_sessions([]))
    assert [r["duration_minutes"] for r in kept] == [45.0]  # the closest duration is the duplicate
    assert [r["duration_minutes"] for r in app_module._cardio_with_open_wearables()] == [30, 45.0]


def test_a_watch_run_also_logged_by_hand_still_counts_toward_acwr(app_module):
    app_module.WORKOUTS[:] = [timed_lift(n) for n in (3, 10, 17, 24)]
    for n in (2, 9, 16, 23):
        store(app_module, n, "Running", 30, hour=7)
    watch_only = app_module.calculate_acwr(app_module.WORKOUTS)["acwr_series"]["minutes"]
    for n in (2, 9, 16, 23):
        app_module.CARDIO_DATA.append({"date": day(n), "activity_type": "Outdoor Run", "duration_minutes": 30})
    assert watch_only is not None
    assert app_module.calculate_acwr(app_module.WORKOUTS)["acwr_series"]["minutes"] == watch_only  # the run counts once


def test_a_run_logged_by_hand_alone_is_never_less_cautious_than_not_logging_it(app_module):
    store(app_module, 20, "Running", 30)
    for n in (1, 2, 3, 4):
        store(app_module, n, "Running", 90)
    before = app_module.calculate_acwr([])
    for n in (1, 2, 3, 4):
        app_module.CARDIO_DATA.append({"date": day(n), "activity_type": "Outdoor Run", "duration_minutes": 90})
    assert before["risk"] == "high"
    assert app_module.calculate_acwr([])["acwr"] >= before["acwr"] * 0.99  # logged by hand: the same minutes, counted once


def test_a_completed_workout_cardio_entry_is_not_counted_twice(app_module):
    app_module.WORKOUTS[:] = [{**timed_lift(2), "duration_minutes": 75}]  # 45 lifting + 30 cardio, as /api/complete-workout stores it
    app_module.CARDIO_DATA.append({"date": day(2), "activity_type": "Rower", "duration_minutes": 30, "workout_id": "w1"})
    store(app_module, 2, "Rowing", 30)
    store(app_module, 20, "Running", 30)  # a watch session that counts, so the minutes series exists
    assert app_module.calculate_acwr(app_module.WORKOUTS)["acwr_series"]["minutes"]["acute_load"] == 75


def test_a_watch_session_without_a_duration_adds_nothing(app_module):
    store(app_module, 1, "Running", 60)
    base = app_module.calculate_acwr([])["acwr_series"]["minutes"]
    upsert_daily_facts(app_module.WEARABLE_FACTS_DB_FILE, [WearableDailyFact(
        date=day(20), provider_id="whoop", source_label="whoop", metric="workout_active_calories", value=300.0, unit="kcal",
        source_provider="whoop", source_system="open_wearables", source_record_kind="event", source_id="cal-only",
        observed_at=f"{day(20)}T07:00:00Z", original_label="Running", category="cardio",
    )], profile_key=app_module._open_wearables_profile_key())
    assert app_module._open_wearables_load_sessions([]) != [] and len(app_module._open_wearables_load_sessions([])) == 1
    assert app_module.calculate_acwr([])["acwr_series"]["minutes"] == base


@pytest.mark.parametrize("label,canonical", [
    ("indoor_cycling", "cycling"), ("running_treadmill", "treadmill"), ("rowing_machine", "rowing"), ("trail_running", "running"),
    ("Stair Climbing", "stairmaster"), ("open-water-swimming", "swimming"), ("hiking", "walking"), ("Outdoor Run", "running"),
    ("trail_hiking", "walking"), ("cyclocross", "cycling"), ("e_biking", "cycling"), ("swimming_pool", "swimming"), ("swimming_open_water", "swimming"),
])
def test_watch_activity_names_map_to_a_fatigue_type(app_module, label, canonical):
    assert app_module._canonical_cardio_fatigue_type(label) == canonical
    app_module.CARDIO_DATA.append({"date": day(1), "activity_type": label, "duration_minutes": 60, "intensity": 5})
    assert app_module.get_cardio_muscle_impact(app_module.CARDIO_DATA, "back" if canonical in ("rowing", "swimming") else "quads") > 0


def test_a_named_watch_lift_folds_into_the_logged_lift(app_module):
    store(app_module, 1, "Evening Lift", 55, hour=18, category="strength_training")
    assert app_module._open_wearables_load_sessions([lift(1)]) == []


def test_smart_recommendation_names_the_watch_minutes_it_used(app_module):
    store(app_module, 20, "Running", 30)
    for n in (1, 2, 3, 4):
        store(app_module, n, "Running", 90, provider="apple")
    data = app_module.app.test_client().get("/api/recommendation/smart").get_json()
    assert data["readiness_factors"]["acwr"]["acwr_series"]["used"] == "minutes"
    assert "training minutes" in data["reasoning"]  # the series also holds logged minutes, so it is not called the watch's
    proof = data["recommendation_sources"]["source_proof"]["open_wearables"]
    assert proof["used_for_recommendation"] and proof["fields_used"] == ["workout_duration"] and proof["modifier_applied"]
    assert proof["providers"] and proof["observed_at"]
    assert data["recommendation_sources"]["open_wearables"]["role"] == "bounded modifier"


# ---- Round 3 ---------------------------------------------------------------------------------------------------

def spike(module):
    """Four hand-logged 90-minute runs this week, each also on the watch, after one quiet 30-minute watch run: minutes win."""
    store(module, 20, "Running", 30, hour=7)
    for n in (1, 2, 3, 4):
        store(module, n, "Running", 90, hour=7)
        module.CARDIO_DATA.append({"date": day(n), "activity_type": "Outdoor Run", "duration_minutes": 90, "intensity": 5})


@pytest.mark.parametrize("route", ["/api/dashboard", "/api/next-workout"])
def test_every_route_that_decides_on_the_minutes_ratio_says_so(app_module, route):
    spike(app_module)
    data = app_module.app.test_client().get(route).get_json()
    source = data["recommendation_sources"]["open_wearables"]
    assert source["used_for_recommendation"] is True
    assert "no fresh provider facts" not in source["detail"] and "workout minutes are counted" in source["detail"]


def test_logged_minutes_are_not_called_watch_minutes(app_module):
    # Hand-logged runs this week; the only watch session is an old walk, which only lowers the ratio.
    app_module.WORKOUTS[:] = [{**l, "duration_minutes": 55} for l in OWNER_LIFTS]
    app_module.CARDIO_DATA[:] = [{"date": day(n), "activity_type": "Outdoor Run", "duration_minutes": 60, "intensity": 5} for n in (0, 1, 2, 3)]
    store(app_module, 25, "Walking", 20, hour=7)
    data = app_module.app.test_client().get("/api/recommendation/smart").get_json()
    assert data["readiness_factors"]["acwr"]["acwr_series"]["used"] == "minutes"
    assert "watch minutes" not in data["reasoning"]
    assert "Open Wearables workout minutes set" not in data["recommendation_sources"]["open_wearables"]["detail"]


def test_a_non_finite_logged_cardio_duration_does_not_crash_the_dashboard(app_module):
    store(app_module, 2, "Running", 30)
    app_module.CARDIO_DATA.append({"date": day(1), "activity_type": "Outdoor Run", "duration_minutes": float("inf"), "intensity": 5})
    app_module.app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        assert app_module.app.test_client().get("/api/dashboard").status_code == 200
    finally:
        app_module.app.config["PROPAGATE_EXCEPTIONS"] = None


@pytest.mark.parametrize("timed_first", [True, False])
def test_a_timed_lift_takes_its_folded_watch_session_so_an_untimed_one_does_not_borrow_it(app_module, timed_first):
    timed, untimed = {**lift(2), "duration_minutes": 60}, {**lift(2), "duration_minutes": 0}
    store(app_module, 2, "Traditional Strength Training", 60, hour=18)
    store(app_module, 10, "Running", 30, hour=7)
    result = app_module.calculate_acwr([timed, untimed] if timed_first else [untimed, timed])
    # History hands the one watch session to the first lift in row order. Timed first: it keeps its own 60 and the untimed
    # lift gets nothing. Untimed first: that lift gets the watch's 60 and the timed lift adds its own.
    assert result["acwr_series"]["minutes"]["acute_load"] == (60 if timed_first else 120)


def test_the_plan_fingerprint_sees_folded_watch_durations(app_module):
    workouts = [{**lift(20), "duration_minutes": 40}, lift(1)]
    store(app_module, 1, "Traditional Strength Training", 40, sid="folded", hour=18)
    before = app_module._open_wearables_fingerprint_sessions(workouts)
    store(app_module, 1, "Traditional Strength Training", 240, sid="folded", hour=18)
    after = app_module._open_wearables_fingerprint_sessions(workouts)
    assert [r["duration_minutes"] for r in before] == [40] and [r["duration_minutes"] for r in after] == [240]


def test_logs_alone_never_get_a_minutes_series_credited_to_the_watch(app_module):
    # The watch lift folds into a timed lift and counts nothing; no watch run exists, so no watch session is counted.
    app_module.WORKOUTS[:] = [{**lift(1), "duration_minutes": 60}]
    store(app_module, 1, "Traditional Strength Training", 60, hour=18)
    app_module.CARDIO_DATA[:] = [{"date": day(2), "activity_type": "Outdoor Run", "duration_minutes": 30, "intensity": 5}]
    result = app_module.calculate_acwr(app_module.WORKOUTS)
    assert result["acwr_series"]["minutes"] is None and result["acwr_series"]["used"] == "tonnage"
    # Rule B: a hand-logged run the watch also recorded counts as the watch copy, exactly once (60 lift + 30 run).
    store(app_module, 2, "Running", 30, hour=7)
    assert app_module.calculate_acwr(app_module.WORKOUTS)["acwr_series"]["minutes"]["acute_load"] == 90


def _add_cardio(module, n, minutes):
    r = module.app.test_client().post("/api/add-cardio", json={"date": day(n), "activity_type": "Outdoor Run", "duration_minutes": minutes, "intensity": 5})
    assert r.status_code in (200, 201), r.get_json()


def test_hand_logging_every_watch_run_through_add_cardio_keeps_the_load(app_module):
    """Logging the runs a watch also recorded must not make the plan less cautious than not logging them."""
    store(app_module, 20, "Running", 30, hour=7)
    for n in (1, 2, 3, 4):
        store(app_module, n, "Running", 90, hour=7)
    client = app_module.app.test_client()
    before = app_module.calculate_acwr(app_module.WORKOUTS)
    smart_before = client.get("/api/recommendation/smart").get_json()["recommendation"]
    for n, mins in ((20, 30), (1, 90), (2, 90), (3, 90), (4, 90)):
        _add_cardio(app_module, n, mins)
    after = app_module.calculate_acwr(app_module.WORKOUTS)
    smart_after = client.get("/api/recommendation/smart").get_json()["recommendation"]
    assert (before["acwr"], before["risk"], smart_before) == (2.769, "high", "recovery")
    assert (after["acwr"], after["risk"], smart_after) == (before["acwr"], before["risk"], smart_before)


def test_hand_logging_the_one_old_watch_run_does_not_flip_a_recovery_day(app_module):
    """Four 90-min runs this week on the watch and by hand, one quiet 30-min watch run 20 days ago: logging that old run
    (chronic-only minutes) must not turn a recovery day into a moderate one."""
    pattern = [("Chest Press", "chest"), ("Seated Row", "back"), ("Leg Press", "quads")]
    app_module.WORKOUTS[:] = [
        {"date": day(n), "session_type": "push", "duration_minutes": 55,
         "exercises": [{"machine": pattern[i % 3][0], "muscle_group": pattern[i % 3][1], "sets": [{"weight_lbs": 100, "reps": 10}] * 3}]}
        for i, n in enumerate((1, 3, 5, 8, 10, 12, 15, 17, 19, 22, 24, 26))
    ]
    store(app_module, 20, "Running", 30, hour=7)
    for n in (1, 2, 3, 4):
        store(app_module, n, "Running", 90, hour=7)
        app_module.CARDIO_DATA.append({"date": day(n), "activity_type": "Outdoor Run", "duration_minutes": 90, "intensity": 5})
    client = app_module.app.test_client()
    before = (app_module.calculate_acwr(app_module.WORKOUTS)["acwr"], client.get("/api/recommendation/smart").get_json()["recommendation"])
    _add_cardio(app_module, 20, 30)
    after = (app_module.calculate_acwr(app_module.WORKOUTS)["acwr"], client.get("/api/recommendation/smart").get_json()["recommendation"])
    assert before == (1.929, "recovery")
    assert after == before


def test_logging_four_weekly_watch_runs_keeps_a_moderate_day_moderate(app_module):
    app_module.WORKOUTS[:] = [timed_lift(n) for n in (3, 10, 17, 24)]
    for n in (2, 9, 16, 23):
        store(app_module, n, "Running", 30, hour=7)
    watch_only = app_module.calculate_acwr(app_module.WORKOUTS)["acwr_series"]["minutes"]
    for n in (2, 9, 16, 23):
        app_module.CARDIO_DATA.append({"date": day(n), "activity_type": "Outdoor Run", "duration_minutes": 30})
    acwr = app_module.calculate_acwr(app_module.WORKOUTS)
    assert acwr["acwr_series"]["minutes"] == watch_only and watch_only["acwr"] < 1.3
    assert app_module._training_recommendation_from_factors(None, acwr_data=acwr)[0] == "moderate"


@pytest.mark.parametrize("sets", [None, 0, "x"])
def test_add_workout_rejects_a_non_list_sets_and_stores_nothing(app_module, sets):
    body = {"date": day(1), "session_type": "push", "exercises": [{"machine": "Bench", "sets": sets}]}
    assert app_module.app.test_client().post("/api/add-workout", json=body).status_code == 400
    assert app_module.WORKOUTS == []


RANK = {"recovery": 0, "moderate": 1, "intensity": 2}


def _plan_snapshot(module):
    acwr = module.calculate_acwr(module.WORKOUTS)
    smart = module.app.test_client().get("/api/recommendation/smart").get_json()["recommendation"]
    return acwr["acwr"], module._training_recommendation_from_factors(None, acwr_data=acwr)[0], smart


@pytest.mark.parametrize("logged, twin", [(120, 15), (60, 45), (45, 44), (30, 60)])
def test_a_shorter_watch_copy_of_a_logged_run_never_lowers_caution(app_module, logged, twin):
    """Another watch session already turns the minutes series on; the watch then also records the logged run, but saw less
    of it. The run still counts once, with the longer duration, so the ratio and plan never drop (and a longer watch copy counts)."""
    app_module.WORKOUTS[:] = [{**lift(20), "duration_minutes": 10}]
    app_module.CARDIO_DATA[:] = [{"date": day(2), "activity_type": "Outdoor Run", "duration_minutes": logged, "intensity": 5}]
    store(app_module, 10, "Cycling", 40, hour=7)
    before = _plan_snapshot(app_module)
    store(app_module, 2, "Running", twin, hour=7)
    after = _plan_snapshot(app_module)
    assert after[0] >= before[0] and RANK[after[1]] <= RANK[before[1]] and RANK[after[2]] <= RANK[before[2]], (before, after)
    if logged == 120:
        assert before == after == (2.118, "recovery", "recovery")


def test_a_non_finite_logged_duration_still_counts_its_watch_copy(app_module):
    app_module.CARDIO_DATA[:] = [{"date": day(2), "activity_type": "Outdoor Run", "duration_minutes": float("inf"), "intensity": 5}]
    store(app_module, 2, "Running", 30, hour=7)
    assert app_module.calculate_acwr(app_module.WORKOUTS)["acwr_series"]["minutes"]["acute_load"] == 30


@pytest.mark.parametrize("twins", [False, True])
def test_two_huge_imported_runs_keep_the_dashboard_up(app_module, twins):
    """Import does not cap durations: two finite 1e308-minute runs must not sum to inf and 500 the main routes."""
    app_module.WORKOUTS[:] = list(OWNER_LIFTS)
    for n in ((1, 2) if twins else (5,)):
        store(app_module, n, "Running" if twins else "Cycling", 30, hour=7)
    cardio = [{"date": day(n), "activity_type": "Outdoor Run", "duration_minutes": 1e308, "intensity": 5} for n in (1, 2)]
    client = app_module.app.test_client()
    assert client.post("/api/import-backup", json={"data": {"cardio": cardio}}).status_code == 200
    app_module.app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        codes = {r: client.get(r).status_code for r in ("/api/dashboard", "/api/acwr", "/api/recommendation/smart", "/api/next-workout")}
    finally:
        app_module.app.config["PROPAGATE_EXCEPTIONS"] = None
    assert set(codes.values()) == {200}, codes


# ---- Round 7 ---------------------------------------------------------------------------------------------------

def test_a_longer_watch_run_matched_to_workout_linked_cardio_counts_its_extra_minutes(app_module):
    """The linked 15 minutes are inside the 60-minute workout; the watch saw a 120-minute run, so 105 more minutes count."""
    app_module.WORKOUTS[:] = [{**timed_lift(2), "duration_minutes": 60}]
    app_module.CARDIO_DATA[:] = [{"date": day(2), "activity_type": "Outdoor Run", "duration_minutes": 15, "workout_id": "w1"}]
    store(app_module, 2, "Running", 120, hour=7)
    assert app_module.calculate_acwr(app_module.WORKOUTS)["acwr_series"]["minutes"]["acute_load"] == 165


def test_a_shorter_watch_copy_of_workout_linked_cardio_adds_nothing_but_keeps_the_series(app_module):
    app_module.WORKOUTS[:] = [{**timed_lift(2), "duration_minutes": 75}]
    app_module.CARDIO_DATA[:] = [{"date": day(2), "activity_type": "Outdoor Run", "duration_minutes": 30, "workout_id": "w1"}]
    store(app_module, 2, "Running", 20, hour=7)
    assert app_module.calculate_acwr(app_module.WORKOUTS)["acwr_series"]["minutes"]["acute_load"] == 75


def tonnage_wins(module):
    """A heavy-lifting month where a quiet watch run cannot lift the minutes ratio above the tonnage ratio."""
    module.WORKOUTS[:] = [lift(n, weight=200) for n in (1, 3, 5, 8, 10, 12, 15, 17, 19, 22, 24, 26)]


def _source_routes(module, **expect):
    client = module.app.test_client()
    for route in ("/api/dashboard", "/api/next-workout", "/api/recommendation/smart"):
        source = client.get(route).get_json()["recommendation_sources"]["open_wearables"]
        assert source["used_for_recommendation"] is expect["used"], route
        if expect["used"]:
            assert "muscle readiness" in source["detail"] and "training-load ratio" not in source["detail"], route
    return client


def test_a_watch_run_that_only_changes_muscle_readiness_is_credited_as_a_source(app_module):
    tonnage_wins(app_module)
    store(app_module, 1, "Running", 30, hour=7)
    acwr = app_module.calculate_acwr(app_module.WORKOUTS)
    assert acwr["acwr_series"]["used"] == "tonnage"
    client = _source_routes(app_module, used=True)
    proof = client.get("/api/recommendation/smart").get_json()["recommendation_sources"]["source_proof"]["open_wearables"]
    assert proof["used_for_recommendation"] and proof["providers"] == ["WHOOP"] and proof["observed_at"] and not proof["modifier_applied"]


def test_a_watch_run_outside_the_fatigue_window_is_not_credited_while_tonnage_wins(app_module):
    tonnage_wins(app_module)
    store(app_module, 10, "Running", 30, hour=7)
    assert app_module.calculate_acwr(app_module.WORKOUTS)["acwr_series"]["used"] == "tonnage"
    _source_routes(app_module, used=False)


def test_a_future_dated_watch_run_reaches_neither_fatigue_nor_the_source(app_module):
    tonnage_wins(app_module)
    client = app_module.app.test_client()
    before = client.get("/api/muscle-fatigue").get_json()
    store(app_module, -1, "Running", 90, hour=7)
    assert client.get("/api/muscle-fatigue").get_json() == before
    assert app_module._watch_load_source(app_module.calculate_acwr(app_module.WORKOUTS)) is None


@pytest.mark.parametrize("logged, watch, expected", [(30, 90, 90.0), (90, 30, 90), (float("inf"), 30, float("inf"))])
def test_fatigue_takes_the_longer_duration_of_a_logged_run_the_watch_also_recorded(app_module, logged, watch, expected):
    app_module.CARDIO_DATA[:] = [{"date": day(1), "activity_type": "Outdoor Run", "duration_minutes": logged, "intensity": 5}]
    store(app_module, 1, "Running", watch, hour=7)
    assert [r["duration_minutes"] for r in app_module._cardio_with_open_wearables()] == [expected]


def test_fatigue_also_takes_the_longer_watch_duration_for_workout_linked_cardio(app_module):
    app_module.CARDIO_DATA[:] = [{"date": day(1), "activity_type": "Outdoor Run", "duration_minutes": 15, "workout_id": "w1"}]
    store(app_module, 1, "Running", 60, hour=7)
    assert [r["duration_minutes"] for r in app_module._cardio_with_open_wearables()] == [60.0]


@pytest.mark.parametrize("alias", [{"duration_min": 120}, {"duration_minutes": 0, "duration_min": 120}, {"duration_minutes": None, "duration_min": 120}])
def test_a_shorter_watch_copy_never_replaces_a_longer_duration_min_logged_run(app_module, alias):
    # get_cardio_muscle_impact reads duration_minutes or duration_min; an imported backup row can carry only the alias
    app_module.CARDIO_DATA[:] = [{"date": day(1), "activity_type": "Outdoor Run", "intensity": 5, **alias}]
    tonnage_wins(app_module)
    store(app_module, 1, "Running", 30, hour=7)
    assert app_module._cardio_with_open_wearables() == app_module.CARDIO_DATA
    _source_routes(app_module, used=False)  # the run lowered nothing and added nothing, so it is not credited


def test_a_longer_watch_run_still_replaces_a_shorter_duration_min_logged_run(app_module):
    app_module.CARDIO_DATA[:] = [{"date": day(1), "activity_type": "Outdoor Run", "duration_min": 20, "intensity": 5}]
    store(app_module, 1, "Running", 60, hour=7)
    assert [r["duration_minutes"] for r in app_module._cardio_with_open_wearables()] == [60.0]


@pytest.mark.parametrize("linked", [{}, {"workout_id": "w1"}])
def test_a_longer_watch_copy_never_swaps_into_a_run_with_non_positive_intensity(app_module, linked):
    # an imported backup row can carry intensity -5; there, the watch's longer minutes would make legs read fresher
    app_module.CARDIO_DATA[:] = [{"date": day(1), "activity_type": "Outdoor Run", "duration_minutes": 10, "intensity": -5, **linked}]
    tonnage_wins(app_module)
    store(app_module, 1, "Running", 60, hour=7)
    assert app_module._cardio_with_open_wearables() == app_module.CARDIO_DATA
    _source_routes(app_module, used=False)
