"""FIT-12 — Progress Loop: completed workout → next recommendation.

These tests lock in the round-trip the issue asks for:

* /api/complete-workout updates history immediately (already worked).
* The just-completed session feeds fatigue/readiness context into the next
  recommendation (was missing — recommendation never read `overall_fatigue`).
* Recent completed muscle volume shows up in the avoid list and reasoning.
* /api/workout/analyze still doesn't mutate plan state after a completion.
* /api/adherence counts completions linked to a recommendation, not stale
  in-process `WORKOUT_RECOMMENDATIONS` (which never gets populated on the
  live path).
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit12-progress-loop-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)

    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "COMPLETED_WORKOUTS", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "RECOVERY_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_notify_workout_logged", lambda *_a, **_kw: None)
    module.LAST_WORKOUT_RECOMMENDATION = None

    # Neutralize external side-effects on /api/recommendation/smart so we
    # exercise only the post-workout linkage under test.
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "upsert_oura_daily", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "compute_hrv_trend", lambda *_a, **_kw: "unknown")
    monkeypatch.setattr(module, "calculate_acwr", lambda *_a, **_kw: {"acwr": 1.0, "chronic_load": 0, "risk": "low"})
    monkeypatch.setattr(module, "calculate_sleep_debt", lambda *_a, **_kw: {"debt_minutes": 0, "status": "ok"})
    monkeypatch.setattr(module, "calculate_recovery_bonus", lambda *_a, **_kw: {"bonus_points": 0})
    monkeypatch.setattr(module, "_fetch_wttr", lambda *_a, **_kw: {"available": False})
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_kw: {
        "id": "next-rec-1",
        "exercises": [],
        "estimated_minutes": 30,
        "mesocycle": {"rpe_base": 7},
    })
    monkeypatch.setattr(module, "_compute_data_freshness", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "_nutrition_context_for_date", lambda *_a, **_kw: {"warnings": []})

    # OuraClient.get_today_metrics would otherwise raise; the route catches it,
    # but stubbing avoids the noisy traceback path.
    class _StubOura:
        def get_today_metrics(self, _today):
            return None, None, None, {}, {}
    monkeypatch.setattr(module, "OuraClient", lambda *_a, **_kw: _StubOura())

    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _workout_payload(*, recommendation_id=None, fatigue=5, muscle="chest", machine="Chest Press"):
    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "session_type": "push",
        "duration_minutes": 45,
        "fatigue": fatigue,
        "exercises": [
            {
                "machine": machine,
                "muscle_group": muscle,
                "sets": [
                    {"set_number": 1, "weight_lbs": 100, "reps": 8, "rpe": 8},
                    {"set_number": 2, "weight_lbs": 100, "reps": 8, "rpe": 8},
                    {"set_number": 3, "weight_lbs": 95, "reps": 8, "rpe": 9},
                ],
            }
        ],
    }
    if recommendation_id is not None:
        payload["recommendation_id"] = recommendation_id
    return payload


# ──────────────────────────────────────────────────────────────────
# summarize_recent_completion — pure helper
# ──────────────────────────────────────────────────────────────────

def test_summarize_recent_completion_returns_none_when_window_empty(fitness_app):
    assert fitness_app.summarize_recent_completion([], hours=24) is None
    stale = [{
        "created_at": (datetime.now() - timedelta(hours=72)).isoformat(timespec="seconds"),
        "date": (datetime.now() - timedelta(hours=72)).strftime("%Y-%m-%d"),
        "exercises": [{"muscle_group": "chest", "sets": [{"weight_lbs": 100, "reps": 8}]}],
        "overall_fatigue": 5,
    }]
    assert fitness_app.summarize_recent_completion(stale, hours=24) is None


def test_summarize_recent_completion_groups_muscles_by_set_count(fitness_app):
    now_iso = datetime.now().isoformat(timespec="seconds")
    workout = {
        "id": "wkt-x",
        "created_at": now_iso,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "session_type": "push",
        "overall_fatigue": 9,
        "exercises": [
            {"muscle_group": "chest", "sets": [{"weight_lbs": 100, "reps": 8}] * 4},
            {"muscle_group": "shoulders", "sets": [{"weight_lbs": 30, "reps": 10}] * 2},
            {"muscle_group": "unknown", "sets": [{"weight_lbs": 0, "reps": 0}]},
        ],
    }
    summary = fitness_app.summarize_recent_completion([workout], hours=24)
    assert summary is not None
    assert summary["overall_fatigue"] == 9
    assert summary["total_sets"] == 7
    assert summary["workout_id"] == "wkt-x"
    muscles = summary["muscles_trained"]
    # Ordered by set count desc; "unknown" filtered out.
    assert muscles[0]["muscle"] == "chest" and muscles[0]["sets"] == 4
    assert muscles[1]["muscle"] == "shoulders" and muscles[1]["sets"] == 2
    assert all(m["muscle"] != "unknown" for m in muscles)


def test_summarize_recent_completion_picks_most_recent(fitness_app):
    older = {
        "id": "older",
        "created_at": (datetime.now() - timedelta(hours=6)).isoformat(timespec="seconds"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "exercises": [{"muscle_group": "back", "sets": [{"weight_lbs": 100, "reps": 8}] * 3}],
        "overall_fatigue": 4,
    }
    newer = {
        "id": "newer",
        "created_at": (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "exercises": [{"muscle_group": "quads", "sets": [{"weight_lbs": 200, "reps": 10}] * 3}],
        "overall_fatigue": 7,
    }
    summary = fitness_app.summarize_recent_completion([older, newer], hours=24)
    assert summary["workout_id"] == "newer"
    assert summary["overall_fatigue"] == 7


# ──────────────────────────────────────────────────────────────────
# /api/complete-workout side-effects
# ──────────────────────────────────────────────────────────────────

def test_complete_workout_clears_last_recommendation(fitness_app):
    """A stale recommendation cached for swap/adjust must be invalidated when
    the user actually completes a session — otherwise a follow-up swap would
    edit the workout they just executed."""
    fitness_app.LAST_WORKOUT_RECOMMENDATION = {"id": "stale-rec", "exercises": []}
    res = fitness_app.app.test_client().post(
        "/api/complete-workout",
        data=json.dumps(_workout_payload()),
        content_type="application/json",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert fitness_app.LAST_WORKOUT_RECOMMENDATION is None
    assert len(fitness_app.WORKOUTS) == 1


def test_complete_workout_appears_immediately_in_history(fitness_app):
    """AC1: history view should reflect the just-saved workout without any
    cache invalidation step."""
    client = fitness_app.app.test_client()
    res = client.post(
        "/api/complete-workout",
        data=json.dumps(_workout_payload()),
        content_type="application/json",
    )
    assert res.status_code == 200
    history = client.get("/api/history").get_json()
    assert history["workouts"], "history must include the just-completed workout"
    assert history["workouts"][0]["session_type"] == "push"


# ──────────────────────────────────────────────────────────────────
# /api/recommendation/smart sees the just-completed session
# ──────────────────────────────────────────────────────────────────

def test_smart_recommendation_lists_recently_trained_muscle_in_avoid(fitness_app):
    """AC3: a chest session in the last 18h should put chest on the avoid
    list of the next recommendation."""
    client = fitness_app.app.test_client()
    res = client.post(
        "/api/complete-workout",
        data=json.dumps(_workout_payload(muscle="chest", machine="Chest Press", fatigue=6)),
        content_type="application/json",
    )
    assert res.status_code == 200

    smart = client.get("/api/recommendation/smart").get_json()
    assert smart["last_completed"] is not None
    assert smart["last_completed"]["overall_fatigue"] == 6
    assert "chest" in smart["avoid_muscles"]
    assert any(m["muscle"] == "chest" for m in smart["recently_trained"])


def test_smart_recommendation_downgrades_intensity_when_recent_fatigue_high(fitness_app, monkeypatch):
    """AC2/AC5: post-workout fatigue ≥8 within 18h dampens the next session
    by one notch. Bake in a high-readiness Oura day so the base recommendation
    starts at 'intensity', then check the downgrade pulls it back."""
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_a, **_kw: {"readiness_score": 90})
    client = fitness_app.app.test_client()
    res = client.post(
        "/api/complete-workout",
        data=json.dumps(_workout_payload(fatigue=9)),
        content_type="application/json",
    )
    assert res.status_code == 200
    smart = client.get("/api/recommendation/smart").get_json()
    assert smart["last_completed"]["overall_fatigue"] == 9
    assert smart["recommendation"] in ("moderate", "recovery"), (
        f"high recent fatigue should downgrade from 'intensity'; got {smart['recommendation']!r}"
    )
    assert "Last session" in smart["reasoning"], smart["reasoning"]


def test_smart_recommendation_unaffected_when_no_recent_completion(fitness_app):
    """No completed workouts → last_completed is null, avoid list comes only
    from soreness logs, recommendation is unchanged."""
    smart = fitness_app.app.test_client().get("/api/recommendation/smart").get_json()
    assert smart["last_completed"] is None
    assert smart["recently_trained"] == []
    # Soreness list is empty (fixture default) so no muscles flagged either.
    assert smart["avoid_muscles"] == []


# ──────────────────────────────────────────────────────────────────
# /api/workout/analyze stays non-mutating after a completion
# ──────────────────────────────────────────────────────────────────

def test_analyze_after_completion_does_not_resurrect_stale_plan(fitness_app, monkeypatch):
    """AC4: completing a workout clears LAST_WORKOUT_RECOMMENDATION; calling
    /api/workout/analyze on the freshly-saved session must not re-populate
    it (analysis is a read-only summary, never a plan rewrite)."""
    # Force the deterministic-fallback path so the test doesn't hit LM Studio.
    monkeypatch.setattr(fitness_app, "_lm_studio", None)
    client = fitness_app.app.test_client()
    complete = client.post(
        "/api/complete-workout",
        data=json.dumps(_workout_payload()),
        content_type="application/json",
    )
    workout_id = complete.get_json()["workout_id"]
    assert fitness_app.LAST_WORKOUT_RECOMMENDATION is None

    res = client.post(
        "/api/workout/analyze",
        data=json.dumps({"workout_id": workout_id}),
        content_type="application/json",
    )
    assert res.status_code == 200
    assert fitness_app.LAST_WORKOUT_RECOMMENDATION is None


# ──────────────────────────────────────────────────────────────────
# /api/adherence uses completed-with-recommendation as denominator
# ──────────────────────────────────────────────────────────────────

def test_adherence_counts_completions_with_recommendation_id(fitness_app):
    """AC1: adherence rate should reflect completed workouts that had a
    recommendation attached, regardless of whether the recommendation was
    persisted into WORKOUT_RECOMMENDATIONS (which the live path doesn't do)."""
    client = fitness_app.app.test_client()
    # Two completions tied to recommendations, one followed; one un-linked
    # completion that shouldn't count toward the denominator.
    client.post(
        "/api/complete-workout",
        data=json.dumps(_workout_payload(recommendation_id="rec-A", machine="Chest Press")),
        content_type="application/json",
    )
    client.post(
        "/api/complete-workout",
        data=json.dumps(_workout_payload(recommendation_id="rec-B", machine="Lat Pulldown", muscle="back")),
        content_type="application/json",
    )
    client.post(
        "/api/complete-workout",
        data=json.dumps(_workout_payload(machine="Leg Press", muscle="quads")),
        content_type="application/json",
    )

    # Lock in deterministic adherence shapes — the live path defaults to
    # followed=True when no matching recommendation exists, which would muddy
    # the denominator assertion below.
    fitness_app.COMPLETED_WORKOUTS[0]["adherence"] = {"followed": True, "skipped": [], "modified": [], "added": []}
    fitness_app.COMPLETED_WORKOUTS[1]["adherence"] = {"followed": False, "skipped": ["Skipped Ex"], "modified": [], "added": []}

    payload = client.get("/api/adherence").get_json()
    assert payload["total_completed"] == 3
    assert payload["linked_completions"] == 2
    assert payload["followed_count"] == 1
    assert payload["adherence_rate"] == 50  # 1/2 linked → 50%
    assert payload["last_completed_date"] == datetime.now().strftime("%Y-%m-%d")
