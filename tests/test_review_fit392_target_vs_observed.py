"""Reviewer tests (FIT-392): a prescribed (unobserved) RPE saved on a set must not
be read back as an observed effort by progression or injury-risk logic."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    for name in ("WORKOUTS", "COMPLETED_WORKOUTS", "CARDIO_DATA", "WORKOUT_RECOMMENDATIONS"):
        monkeypatch.setattr(module, name, [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_k: None)
    return module


def _complete(module, client_id, date, rpe, observed, n_sets=3):
    response = module.app.test_client().post("/api/complete-workout", json={
        "client_workout_id": client_id,
        "date": date,
        "session_type": "push",
        "exercises": [{
            "machine": "Chest Press",
            "muscle_group": "chest",
            "sets": [
                {"weight_lbs": 100, "reps": 10, "rpe": rpe, "rpe_observed": observed}
                for _ in range(n_sets)
            ],
        }],
    })
    assert response.status_code == 200, response.get_json()


def test_unobserved_prescribed_rpe_is_not_progression_evidence(fitness_app):
    # The guided UI pre-fills every set with the prescribed target and sends
    # rpe_observed=False when the user never touched the RPE field.
    _complete(fitness_app, "review-392-a", "2026-07-16", rpe=8, observed=False)
    perf = fitness_app._get_last_exercise_performance(fitness_app.WORKOUTS, "Chest Press")
    assert perf["avg_rpe"] is None, (
        f"prescribed target RPE was used as observed avg_rpe={perf['avg_rpe']}"
    )


def test_unobserved_prescribed_rpe_does_not_trigger_high_intensity_risk(fitness_app):
    _complete(fitness_app, "review-392-b", "2026-07-15", rpe=9, observed=False)
    _complete(fitness_app, "review-392-c", "2026-07-16", rpe=9, observed=False)
    risk = fitness_app.calculate_injury_risk(fitness_app.WORKOUTS, [])
    assert not any(r.get("type") == "High Intensity" for r in risk.get("risks", [])), risk


def test_observed_rpe_still_counts(fitness_app):
    _complete(fitness_app, "review-392-d", "2026-07-16", rpe=8.5, observed=True)
    perf = fitness_app._get_last_exercise_performance(fitness_app.WORKOUTS, "Chest Press")
    assert perf["avg_rpe"] == 8.5
