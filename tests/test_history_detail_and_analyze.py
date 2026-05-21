"""Contract tests for the FIT-14 workout history detail + AI analysis surfaces.

The history detail modal in the JS reads ``adherence`` (added / skipped /
modified lists) per workout from the history endpoints and uses
``/api/workout/analyze`` for the "Analyze workout" affordance. These
tests lock in the contract the UI relies on:

* /api/history exposes ``adherence`` so the detail modal can label
  exercises as planned / added / modified and surface skipped ones.
* /api/history-all carries the same field for the analytics surface.
* /api/workout/analyze accepts a historical reference (``workout_date``
  or ``workout_id``) and does NOT mutate ``LAST_WORKOUT_RECOMMENDATION``
  — the detail-modal Analyze button must be safe to use on past
  workouts.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit14-history-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    # Stub the LM Studio call path so analyze tests don't burn 10+s
    # per test on a real timeout to the local model that isn't running
    # in the test environment. The route's deterministic-fallback path
    # is the realistic CI shape anyway.
    if getattr(module, "_lm_studio", None) is not None:
        import lm_studio_adapter
        def _raise(*_a, **_kw):
            raise lm_studio_adapter.LmStudioError("test stub: LM Studio unreachable")
        monkeypatch.setattr(lm_studio_adapter, "_completion_json", _raise, raising=False)
        monkeypatch.setattr(lm_studio_adapter, "_preflight_candidate", _raise, raising=False)
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _seed_workout(module, *, adherence=None):
    """Seed a single completed workout with a known adherence shape."""
    record = {
        "id": "wkt-fit14-test-1",
        "date": "2026-05-18",
        "session_type": "push",
        "duration_minutes": 45,
        "exercises": [
            {
                "machine": "Chest Press",
                "muscle_group": "chest",
                "sets": [
                    {"set_number": 1, "weight_lbs": 50, "reps": 10, "rpe": 7, "notes": "Felt good"},
                    {"set_number": 2, "weight_lbs": 50, "reps": 10, "rpe": 8, "notes": ""},
                ],
            },
            {
                "machine": "Lateral Raise",
                "muscle_group": "shoulders",
                "sets": [
                    {"set_number": 1, "weight_lbs": 15, "reps": 12, "rpe": 8, "notes": ""},
                ],
            },
        ],
        "notes": "",
    }
    if adherence is not None:
        record["adherence"] = adherence
    module.WORKOUTS.clear()
    module.WORKOUTS.append(record)
    return record


# ──────────────────────────────────────────────────────────────────
# /api/history exposes adherence
# ──────────────────────────────────────────────────────────────────

def test_history_response_carries_adherence_field(fitness_app):
    """The detail modal labels exercises as planned / added / modified
    using these lists. /api/history must expose them per workout.
    """
    _seed_workout(fitness_app, adherence={
        "followed": False,
        "skipped": ["Tricep Pushdown"],
        "modified": [{"exercise": "Chest Press", "delta": "−5 lb"}],
        "added": ["Lateral Raise"],
    })
    res = fitness_app.app.test_client().get("/api/history")
    payload = res.get_json()
    assert payload["workouts"], "seeded workout should be present"
    w = payload["workouts"][0]
    assert "adherence" in w, "history response must expose adherence per workout"
    assert w["adherence"]["followed"] is False
    assert w["adherence"]["added"] == ["Lateral Raise"]
    assert w["adherence"]["skipped"] == ["Tricep Pushdown"]
    assert isinstance(w["adherence"]["modified"], list)


def test_history_response_defaults_adherence_when_missing(fitness_app):
    """Older workouts pre-dating the adherence tracker still render in
    the detail modal — the endpoint must default to an empty-but-shaped
    adherence dict so the JS doesn't need null checks for every field.
    """
    _seed_workout(fitness_app, adherence=None)
    res = fitness_app.app.test_client().get("/api/history")
    w = res.get_json()["workouts"][0]
    a = w.get("adherence") or {}
    assert a.get("followed") is True
    assert a.get("skipped") == []
    assert a.get("modified") == []
    assert a.get("added") == []


def test_history_all_response_carries_adherence(fitness_app):
    """The /api/history-all endpoint feeds the same detail modal from
    the All view; must expose adherence too.
    """
    _seed_workout(fitness_app, adherence={
        "followed": False, "skipped": ["X"], "modified": [], "added": ["Y"],
    })
    res = fitness_app.app.test_client().get("/api/history-all")
    payload = res.get_json()
    workouts = payload.get("workouts") or []
    assert workouts, "history-all should list the seeded workout"
    assert "adherence" in workouts[0]
    assert workouts[0]["adherence"]["added"] == ["Y"]


def test_history_all_workouts_shape_for_history_tab_charts(fitness_app):
    """FIT-115: The History tab `renderHistory()` in static/js/app.js
    pipes /api/history-all `workouts` into bucket aggregation
    (frequency bars + volume line). The JS expects three things per
    workout:
      * `workouts` is a LIST (renderHistory calls `.map`/`.filter`)
      * `date` is a zero-padded ISO `YYYY-MM-DD` string (the bucket
        keys and `new Date(date + 'T00:00:00')` parsing both depend on
        it).
      * `total_volume` is a number (the volume aggregation does
        `Number(w.total_volume || 0)`).

    A regression in any of these would empty the charts despite the
    payload looking "fine" at a glance — which is exactly the failure
    mode FIT-115 caught in the FIT-106 mobile QA matrix.
    """
    _seed_workout(fitness_app)
    res = fitness_app.app.test_client().get("/api/history-all")
    assert res.status_code == 200, res.get_data(as_text=True)
    payload = res.get_json()
    workouts = payload.get("workouts")
    assert isinstance(workouts, list), (
        "history-all must return a list under `workouts`; "
        "renderHistory() calls .map/.filter on it."
    )
    assert workouts, "the seeded workout should appear"
    for w in workouts:
        date = w.get("date")
        assert isinstance(date, str) and len(date) == 10 and date[4] == "-" and date[7] == "-", (
            f"workout date must be a zero-padded YYYY-MM-DD string for the JS "
            f"bucket lookup; got {date!r}"
        )
        assert isinstance(w.get("total_volume"), (int, float)), (
            f"total_volume must be numeric for the volume line aggregation; "
            f"got {type(w.get('total_volume')).__name__}"
        )


def test_history_workout_includes_per_exercise_sets(fitness_app):
    """The detail modal renders sets/reps/weight/RPE/notes per exercise.
    The endpoint must keep exposing the full sets array per exercise.
    """
    _seed_workout(fitness_app)
    res = fitness_app.app.test_client().get("/api/history")
    w = res.get_json()["workouts"][0]
    assert w["exercises"], "exercises must be returned"
    ex0 = w["exercises"][0]
    assert "sets" in ex0 and ex0["sets"], "sets must be returned per exercise"
    s0 = ex0["sets"][0]
    for key in ("set_number", "weight_lbs", "reps", "rpe"):
        assert key in s0, f"set must expose {key!r}"


# ──────────────────────────────────────────────────────────────────
# /api/workout/analyze is read-only and accepts historical refs
# ──────────────────────────────────────────────────────────────────

def test_workout_analyze_accepts_workout_date_for_historical_ref(fitness_app):
    """The in-modal Analyze button posts {workout_date: ...}. The
    endpoint must accept that shape and return a 200 (analysis or
    fallback). FIT-14 acceptance: "AI analysis can summarize completed
    or historical workouts."
    """
    _seed_workout(fitness_app)
    res = fitness_app.app.test_client().post(
        "/api/workout/analyze",
        data=json.dumps({"workout_date": "2026-05-18"}),
        content_type="application/json",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    payload = res.get_json()
    assert payload.get("status") in ("ok", "fallback")
    assert "analysis" in payload, "response must carry analysis block"


def test_workout_analyze_does_not_mutate_recommendation_state(fitness_app):
    """FIT-14 acceptance: "AI analysis can summarize completed or
    historical workouts WITHOUT mutating plan state." Lock that in
    with a sentinel — if a future refactor wires analyze into the
    recommendation regeneration path, this catches it.
    """
    module = fitness_app
    _seed_workout(module)
    sentinel = {"name": "fit14-analyze-sentinel-plan"}
    original = getattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    module.LAST_WORKOUT_RECOMMENDATION = sentinel
    try:
        module.app.test_client().post(
            "/api/workout/analyze",
            data=json.dumps({"workout_date": "2026-05-18"}),
            content_type="application/json",
        )
        assert module.LAST_WORKOUT_RECOMMENDATION is sentinel, (
            "LAST_WORKOUT_RECOMMENDATION was replaced by /api/workout/analyze"
        )
    finally:
        module.LAST_WORKOUT_RECOMMENDATION = original


def test_workout_analyze_accepts_workout_id_for_historical_ref(fitness_app):
    """The in-modal Analyze button prefers ``workout_id`` when the
    history payload has one. Endpoint must accept that too.
    """
    _seed_workout(fitness_app)
    res = fitness_app.app.test_client().post(
        "/api/workout/analyze",
        data=json.dumps({"workout_id": "wkt-fit14-test-1"}),
        content_type="application/json",
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload.get("status") in ("ok", "fallback")
