"""Reviewer test for PR #259 (FIT-233): a swap that loses the plan race returns 409.

The food/adaptation cases that shared this file were removed with food logging
(PER-1582); only the workout-plan compare-and-set contract remains.
"""
from __future__ import annotations

import importlib

import data_store


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "review-259-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "USER_SETTINGS", {"available_time_minutes": 35})
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_kw: _recommendation())
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fp-review")
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_OWNER", None)
    return module, module.app.test_client()


def _recommendation():
    return {
        "id": "rec-review-259",
        "focus": "upper",
        "estimated_minutes": 60,
        "exercises": [
            {"machine": "Chest Press", "muscle_group": "chest", "target_sets": 4,
             "target_reps": 10, "time_per_set_minutes": 5},
            {"machine": "Lat Pulldown", "muscle_group": "back", "target_sets": 3,
             "target_reps": 10, "time_per_set_minutes": 5},
        ],
        "cardio": {"type": "walk", "duration_minutes": 15},
    }


def test_swap_losing_race_returns_409_and_keeps_peer_plan(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    plan = {
        "id": "swap-base", "goal": module.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 45, "mesocycle": {"week": 1},
        "exercises": [{"exercise": "Chest Press", "muscle": "chest", "target_sets": 3,
                       "target_reps": 10, "target_weight": 100, "rpe_target": 7}],
    }
    data_store.save_current_workout_plan(1, "fp-review", plan)
    peer_plan = dict(plan, id="peer-plan")
    real_save = module.save_current_workout_plan

    def racing_save(user_id, fingerprint, plan, **kwargs):
        # A peer request publishes between this request's read and its CAS write.
        real_save(user_id, fingerprint, peer_plan)
        return real_save(user_id, fingerprint, plan, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", racing_save)
    resp = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "swap-base", "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    stored = data_store.get_current_workout_plan(1)
    assert resp.status_code == 409, (resp.status_code, resp.get_json())
    assert stored["plan"]["id"] == "peer-plan"
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["error"]["code"] == "workout_plan_conflict"
