from __future__ import annotations

import copy
import importlib

import pytest


def _module(monkeypatch):
    module = importlib.import_module("app")
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update({
        "training_goal": module.TrainingGoal.HYPERTROPHY.value,
        "available_time_minutes": 60,
        "equipment_preference": "machines_only",
    })
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "RECOVERY_DATA", [])
    monkeypatch.setattr(module, "BASELINES_DATA", {})
    monkeypatch.setattr(module, "COMPLETED_WORKOUTS", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "_notify_workout_logged", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    return module


def _workout(date, machine, weight, reps=10, muscle="shoulders"):
    return {
        "date": date,
        "session_type": "upper",
        "exercises": [
            {
                "machine": machine,
                "muscle_group": muscle,
                "sets": [
                    {"set_number": 1, "weight_lbs": weight, "reps": reps, "rpe": 7},
                    {"set_number": 2, "weight_lbs": weight, "reps": reps, "rpe": 8},
                ],
            }
        ],
    }


def _recommendation(module):
    return {
        "id": "fit-103-rec",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "goal_name": "Hypertrophy",
        "estimated_minutes": 45,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Shoulder Press",
                "muscle": "shoulders",
                "target_sets": 3,
                "target_reps": 10,
                "target_weight": 50,
                "rpe_target": 7,
            }
        ],
    }


def _chest_recommendation(module):
    recommendation = copy.deepcopy(_recommendation(module))
    recommendation["exercises"][0].update({
        "exercise": "Chest Press",
        "muscle": "chest",
        "target_weight": 100,
    })
    return recommendation


def test_swap_to_no_history_machine_infers_from_similar_history(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(
        module,
        "WORKOUTS",
        [
            _workout("2026-05-01", "Lateral Raise", 20),
            _workout("2026-05-08", "Lateral Raise", 25),
        ],
    )
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", _recommendation(module))

    response = module.app.test_client().post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Machine Deltoid Raise"},
    )

    assert response.status_code == 200
    exercise = response.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Machine Deltoid Raise"
    assert exercise["load_source"] == "similar_history"
    assert exercise["load_inference"]["source_exercise"] == "Lateral Raise"
    assert "Estimated from Lateral Raise history" in exercise["load_inference"]["message"]
    assert exercise["target_weight"] > 0


def test_direct_history_wins_over_similar_machine_inference(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(
        module,
        "WORKOUTS",
        [
            _workout("2026-05-01", "Lateral Raise", 35),
            _workout("2026-05-03", "Machine Deltoid Raise", 15),
            _workout("2026-05-10", "Machine Deltoid Raise", 20),
        ],
    )
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", _recommendation(module))

    response = module.app.test_client().post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Machine Deltoid Raise"},
    )

    assert response.status_code == 200
    exercise = response.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Machine Deltoid Raise"
    assert exercise["load_source"] == "progression"
    assert "load_inference" not in exercise


def test_alias_named_history_counts_as_direct_progression(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(
        module,
        "WORKOUTS",
        [
            _workout("2026-05-01", "Deltoid Raise", 15),
            _workout("2026-05-08", "Deltoid Raise", 20),
            _workout("2026-05-10", "Lateral Raise", 35),
        ],
    )
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", _recommendation(module))

    response = module.app.test_client().post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Machine Deltoid Raise"},
    )

    assert response.status_code == 200
    exercise = response.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Machine Deltoid Raise"
    assert exercise["load_source"] == "progression"
    assert exercise["load_source_detail"] == "progression:Deltoid Raise"
    assert "load_inference" not in exercise


def test_no_similar_history_falls_back_to_hardcoded_baseline(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", _recommendation(module))

    response = module.app.test_client().post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Machine Deltoid Raise"},
    )

    assert response.status_code == 200
    exercise = response.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Machine Deltoid Raise"
    assert exercise["load_source"] == "hardcoded"
    assert "load_inference" not in exercise


def test_loose_same_muscle_history_does_not_infer_starter_load(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(
        module,
        "WORKOUTS",
        [
            _workout("2026-05-01", "Shoulder Press", 70),
            _workout("2026-05-08", "Shoulder Press", 75),
        ],
    )
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", _recommendation(module))

    response = module.app.test_client().post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Machine Deltoid Raise"},
    )

    assert response.status_code == 200
    exercise = response.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Machine Deltoid Raise"
    assert exercise["load_source"] == "hardcoded"
    assert "load_inference" not in exercise


def test_alternatives_with_load_hints_skip_legacy_partial_history(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(
        module,
        "WORKOUTS",
        [
            {
                "date": "2026-05-01",
                "session_type": "upper",
                "exercises": [
                    {"muscle_group": "shoulders", "sets": [{"reps": 10}]},
                    {"machine": "Lateral Raise", "sets": [{"weight_lbs": "bad", "reps": 10}]},
                    {"machine": "Lateral Raise", "sets": [{"weight_lbs": 20, "reps": 10}]},
                ],
            }
        ],
    )

    response = module.app.test_client().get("/api/exercises/alternatives/shoulders")

    assert response.status_code == 200
    names = [alt["name"] for alt in response.get_json()["alternatives"]]
    assert "Machine Deltoid Raise" in names
    deltoid = next(alt for alt in response.get_json()["alternatives"] if alt["name"] == "Machine Deltoid Raise")
    assert deltoid["equipment"] == "machine"
    assert deltoid["load_hint"]["inferred_from"] == "Lateral Raise"


def test_alternatives_hide_load_hint_when_direct_history_exists(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(
        module,
        "WORKOUTS",
        [
            _workout("2026-05-01", "Machine Deltoid Raise", 20),
            _workout("2026-05-08", "Lateral Raise", 25),
        ],
    )

    response = module.app.test_client().get("/api/exercises/alternatives/shoulders")

    assert response.status_code == 200
    deltoid = next(
        alt for alt in response.get_json()["alternatives"]
        if alt["name"] == "Machine Deltoid Raise"
    )
    assert deltoid["load_hint"] is None


def test_ai_adjust_can_request_named_untracked_machine_and_get_inferred_load(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(
        module,
        "WORKOUTS",
        [
            _workout("2026-05-01", "Lateral Raise", 20),
            _workout("2026-05-08", "Lateral Raise", 25),
        ],
    )
    recommendation = _recommendation(module)
    intent = {
        "avoid_muscles": [],
        "avoid_joints": [],
        "swap": [
            {
                "replace_exercise": "Shoulder Press",
                "target_muscle": "shoulders",
                "target_exercise": "Deltoid Raise",
                "reason": "user requested the deltoid machine",
            }
        ],
        "rpe_delta": 0,
        "sets_delta_pct": 0,
        "duration_cap_min": 0,
        "drop_cardio": False,
    }

    patched, notes = module._apply_intent_patch(
        recommendation,
        intent,
        module.GOAL_PARAMETERS[module.TrainingGoal.HYPERTROPHY.value],
        1,
        module.MESOCYCLE_PLAN[1],
        None,
        "machines_only",
    )

    exercise = patched["exercises"][0]
    assert exercise["exercise"] == "Machine Deltoid Raise"
    assert exercise["load_source"] == "similar_history"
    assert exercise["load_inference"]["source_exercise"] == "Lateral Raise"
    assert any("Shoulder Press" in note and "Machine Deltoid Raise" in note for note in notes)


def test_ai_adjust_rejects_unknown_dragon_press_target(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(module, "WORKOUTS", [])
    recommendation = _recommendation(module)
    intent = {
        "avoid_muscles": [],
        "avoid_joints": [],
        "swap": [
            {
                "replace_exercise": "Shoulder Press",
                "target_muscle": "chest",
                "target_exercise": "Dragon Press",
                "reason": "user typed an unknown exercise",
            }
        ],
        "rpe_delta": 0,
        "sets_delta_pct": 0,
        "duration_cap_min": 0,
        "drop_cardio": False,
    }

    patched, notes = module._apply_intent_patch(
        recommendation,
        intent,
        module.GOAL_PARAMETERS[module.TrainingGoal.HYPERTROPHY.value],
        1,
        module.MESOCYCLE_PLAN[1],
        None,
        "machines_only",
    )

    assert patched["exercises"][0]["exercise"] == "Shoulder Press"
    assert patched["exercises"][0]["exercise"] != "Chest Press"
    assert any("unknown target exercise 'Dragon Press'" in note for note in notes)


def test_swap_endpoint_rejects_single_generic_press_token(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", _chest_recommendation(module))

    response = module.app.test_client().post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "press"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == {"code": "not_found", "message": "Unknown exercise name"}


def test_swap_endpoint_allows_distinctive_single_token_incline(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", _chest_recommendation(module))

    response = module.app.test_client().post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "incline"},
    )

    assert response.status_code == 200
    assert response.get_json()["recommendation"]["exercises"][0]["exercise"] == "Incline Press"


def test_swap_endpoint_allows_singular_one_word_plural_dip(monkeypatch):
    module = _module(monkeypatch)
    module.USER_SETTINGS["equipment_preference"] = "all"
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", _chest_recommendation(module))

    response = module.app.test_client().post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "dip"},
    )

    assert response.status_code == 200
    assert response.get_json()["recommendation"]["exercises"][0]["exercise"] == "Dips"


def test_adjust_intent_schema_accepts_optional_target_exercise():
    adapter = importlib.import_module("lm_studio_adapter")

    adapter._validate_adjust_intent({
        "summary": "Use the requested deltoid machine.",
        "intent": {
            "avoid_muscles": [],
            "avoid_joints": [],
            "swap": [
                {
                    "replace_exercise": "Shoulder Press",
                    "target_muscle": "shoulders",
                    "target_exercise": "Deltoid Raise",
                    "reason": "user asked for this machine",
                }
            ],
            "rpe_delta": 0,
            "sets_delta_pct": 0,
            "duration_cap_min": 0,
            "drop_cardio": False,
        },
    })


def test_adjust_intent_strict_schema_requires_nullable_target_exercise():
    adapter = importlib.import_module("lm_studio_adapter")

    swap_schema = adapter.ADJUST_SCHEMA["properties"]["intent"]["properties"]["swap"]["items"]
    assert "target_exercise" in swap_schema["required"]
    assert swap_schema["properties"]["target_exercise"]["type"] == ["string", "null"]
    adapter._validate_adjust_intent({
        "summary": "Use the requested muscle but no named machine.",
        "intent": {
            "avoid_muscles": [],
            "avoid_joints": [],
            "swap": [
                {
                    "replace_exercise": "Shoulder Press",
                    "target_muscle": "shoulders",
                    "target_exercise": None,
                    "reason": "keep it shoulder focused",
                }
            ],
            "rpe_delta": 0,
            "sets_delta_pct": 0,
            "duration_cap_min": 0,
            "drop_cardio": False,
        },
    })


def test_adjust_cache_version_invalidates_pre_target_exercise_entries(monkeypatch):
    module = _module(monkeypatch)

    assert module._ADJUST_CACHE_VERSION == "fit103-target-exercise-v1"


def test_complete_workout_maps_machine_deltoid_raise_to_shoulders(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(module, "WORKOUTS", [])

    response = module.app.test_client().post(
        "/api/complete-workout",
        json={
            "date": "2026-05-20",
            "duration_minutes": 30,
            "fatigue": 5,
            "exercises": [
                {
                    "machine": "Machine Deltoid Raise",
                    "sets": [{"weight_lbs": 20, "reps": 10, "rpe": 7}],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert module.WORKOUTS[-1]["exercises"][0]["muscle_group"] == "shoulders"
