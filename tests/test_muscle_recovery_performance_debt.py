from __future__ import annotations

import copy
import importlib
from datetime import datetime

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit104-muscle-recovery-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "COMPLETED_WORKOUTS", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_notify_workout_logged", lambda *_args, **_kwargs: None)
    module.LAST_WORKOUT_RECOMMENDATION = None
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _payload(*, reps, recommendation_id="fit104-rec", include_planned_targets=False):
    exercise = {
        "machine": "Chest Press",
        "muscle_group": "chest",
        "sets": [
            {"set_number": index + 1, "weight_lbs": 100, "reps": rep, "rpe": 8}
            for index, rep in enumerate(reps)
        ],
    }
    if include_planned_targets:
        exercise.update(
            {
                "planned_target_weight": 100,
                "planned_target_reps": 10,
                "planned_target_sets": 3,
            }
        )
    return {
        "id": f"fit104-{min(reps)}",
        "client_workout_id": f"fit104-{min(reps)}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "session_type": "push",
        "duration_minutes": 45,
        "exercises": [exercise],
        "recommendation_id": recommendation_id,
    }


def _recommendation():
    return {
        "id": "fit104-rec",
        "exercises": [
            {
                "exercise": "Chest Press",
                "target_weight": 100,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }


def test_muscle_recovery_stays_at_recent_training_debt_when_reps_completed(fitness_app):
    fitness_app.LAST_WORKOUT_RECOMMENDATION = _recommendation()

    response = fitness_app.app.test_client().post(
        "/api/complete-workout",
        json=_payload(reps=[10, 10, 10]),
    )
    assert response.status_code == 200

    fatigue = fitness_app.app.test_client().get("/api/muscle-fatigue").get_json()
    chest = fatigue["chest"]
    assert chest["readiness"] == 8
    assert chest["recovery_debt"] == 2
    assert chest["performance_debt"] == 0
    assert chest["performance_debt_reason"] is None


def test_muscle_recovery_drops_when_recent_planned_reps_were_missed(fitness_app):
    fitness_app.LAST_WORKOUT_RECOMMENDATION = _recommendation()

    response = fitness_app.app.test_client().post(
        "/api/complete-workout",
        json=_payload(reps=[10, 8, 6]),
    )
    assert response.status_code == 200

    stored = fitness_app.WORKOUTS[-1]
    exercise = stored["exercises"][0]
    assert exercise["planned_target_reps"] == 10
    assert exercise["planned_target_sets"] == 3
    assert stored["adherence"]["followed"] is False
    assert stored["adherence"]["modified"][0]["reason"] == "missed reps"

    fatigue = fitness_app.app.test_client().get("/api/muscle-fatigue").get_json()
    chest = fatigue["chest"]
    assert chest["readiness"] == 5
    assert chest["recovery_debt"] == 2
    assert chest["performance_debt"] == 3
    assert chest["performance_debt_reason"] == "missed 4 planned reps"
    assert "missed 4 planned reps" in chest["recommendation"]


def test_recommended_workout_retry_remains_idempotent_after_planned_targets_are_stored(fitness_app):
    fitness_app.LAST_WORKOUT_RECOMMENDATION = _recommendation()
    payload = _payload(reps=[10, 8, 6])

    client = fitness_app.app.test_client()
    response = client.post("/api/complete-workout", json=copy.deepcopy(payload))
    assert response.status_code == 200

    stored = fitness_app.WORKOUTS[-1]
    exercise = stored["exercises"][0]
    assert exercise["planned_target_reps"] == 10
    assert exercise["planned_target_sets"] == 3
    assert fitness_app.LAST_WORKOUT_RECOMMENDATION is None

    retry = client.post("/api/complete-workout", json=copy.deepcopy(payload))
    assert retry.status_code == 200
    retry_body = retry.get_json()
    assert retry_body["sync_status"] == "already_synced"
    assert retry_body["duplicate"] is True
    assert len(fitness_app.WORKOUTS) == 1


def test_completion_payload_targets_feed_recovery_after_recommendation_cache_is_gone(fitness_app):
    response = fitness_app.app.test_client().post(
        "/api/complete-workout",
        json=_payload(
            reps=[10, 8, 6],
            recommendation_id="fit104-rec-after-restart",
            include_planned_targets=True,
        ),
    )
    assert response.status_code == 200

    stored = fitness_app.WORKOUTS[-1]
    exercise = stored["exercises"][0]
    assert exercise["planned_target_reps"] == 10
    assert exercise["planned_target_sets"] == 3
    assert stored["adherence"]["followed"] is None

    fatigue = fitness_app.app.test_client().get("/api/muscle-fatigue").get_json()
    chest = fatigue["chest"]
    assert chest["readiness"] == 5
    assert chest["performance_debt"] == 3
    assert chest["performance_debt_reason"] == "missed 4 planned reps"


def test_recommended_sets_payload_field_survives_actual_set_rows(fitness_app):
    payload = _payload(reps=[10, 10], recommendation_id=None)
    exercise = payload["exercises"][0]
    exercise["recommended_reps"] = 10
    exercise["recommended_sets"] = 3

    response = fitness_app.app.test_client().post("/api/complete-workout", json=payload)
    assert response.status_code == 200

    stored = fitness_app.WORKOUTS[-1]
    assert stored["exercises"][0]["planned_target_reps"] == 10
    assert stored["exercises"][0]["planned_target_sets"] == 3

    fatigue = fitness_app.app.test_client().get("/api/muscle-fatigue").get_json()
    chest = fatigue["chest"]
    assert chest["performance_debt"] == 1
    assert chest["performance_debt_reason"] == "missed 1 planned set"


def test_next_workout_fallback_does_not_readd_low_readiness_muscle(fitness_app):
    fitness_app.WORKOUTS.append(
        {
            "id": "fit104-low-readiness",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "created_at": datetime.now().isoformat(),
            "exercises": [
                {
                    "machine": "Chest Press",
                    "muscle_group": "chest",
                    "planned_target_reps": 10,
                    "planned_target_sets": 3,
                    "sets": [
                        {"set_number": 1, "weight_lbs": 100, "reps": 6},
                        {"set_number": 2, "weight_lbs": 100, "reps": 6},
                        {"set_number": 3, "weight_lbs": 100, "reps": 6},
                    ],
                }
            ],
        }
    )

    recommendation = fitness_app.generate_next_workout(
        fitness_app.WORKOUTS,
        fitness_app.SORENESS_DATA,
        available_time=45,
    )
    muscles = {(exercise.get("muscle") or exercise.get("muscle_group") or "").lower() for exercise in recommendation["exercises"]}
    assert "chest" not in muscles


def test_incomplete_prefilled_set_rows_do_not_count_as_completed_work(fitness_app):
    fitness_app.LAST_WORKOUT_RECOMMENDATION = _recommendation()
    payload = _payload(reps=[10, 10, 10])
    payload["id"] = "fit104-incomplete-row"
    payload["client_workout_id"] = "fit104-incomplete-row"
    payload["exercises"][0]["sets"][2]["completed"] = False

    response = fitness_app.app.test_client().post("/api/complete-workout", json=payload)
    assert response.status_code == 200

    stored = fitness_app.WORKOUTS[-1]
    assert stored["adherence"]["followed"] is False
    modified = stored["adherence"]["modified"][0]
    assert modified["actual_sets"] == 2
    assert modified["reason"] == "missed sets"

    fatigue = fitness_app.app.test_client().get("/api/muscle-fatigue").get_json()
    chest = fatigue["chest"]
    assert chest["readiness"] == 7
    assert chest["performance_debt"] == 1
    assert chest["performance_debt_reason"] == "missed 1 planned set"


def test_muscle_recovery_drops_when_planned_sets_were_missed(fitness_app):
    fitness_app.LAST_WORKOUT_RECOMMENDATION = _recommendation()

    response = fitness_app.app.test_client().post(
        "/api/complete-workout",
        json=_payload(reps=[10]),
    )
    assert response.status_code == 200

    fatigue = fitness_app.app.test_client().get("/api/muscle-fatigue").get_json()
    chest = fatigue["chest"]
    assert chest["readiness"] == 6
    assert chest["performance_debt"] == 2
    assert chest["performance_debt_reason"] == "missed 2 planned sets"
