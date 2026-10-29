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


def store(module, n, label, minutes, *, provider="whoop", sid=None, hour=12):
    observed = f"{day(n)}T{hour:02d}:00:00Z"
    upsert_daily_facts(module.WEARABLE_FACTS_DB_FILE, [
        WearableDailyFact(
            date=day(n), provider_id=provider, source_label=provider, metric="workout_duration", value=float(minutes), unit="min",
            source_provider=provider, source_system="open_wearables", source_record_kind="event",
            source_id=sid or f"{provider}-{n}-{hour}-{label}", observed_at=observed, original_label=label,
            category=hub._workout_category(label),
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
    assert [r["activity_type"] for r in app_module._open_wearables_load_sessions([])] == ["Cycling"]


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
