from __future__ import annotations

import copy
import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    module = importlib.import_module("app")
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update(
        {
            "equipment_preference": "machines_only",
            "preferred_equipment_brands": ["Hoist", "Nautilus"],
            "excluded_exercises": ["Preacher Curl"],
            "available_time_minutes": 120,
        }
    )
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    return module


def test_machine_biceps_curl_is_available_with_hoist_nautilus_metadata(fitness_app):
    response = fitness_app.app.test_client().get("/api/exercises?include_excluded=1")

    assert response.status_code == 200
    exercises = {ex["name"]: ex for ex in response.get_json()["exercises"]}
    assert exercises["Biceps Curl"]["equipment"] == "machine"
    assert exercises["Biceps Curl"]["equipment_brands"] == ["Hoist", "Nautilus"]
    assert "Hoist Biceps Curl" in exercises["Biceps Curl"]["aliases"]
    assert exercises["Preacher Curl"]["disabled_by_default"] is True
    assert exercises["Preacher Curl"]["excluded"] is True


def test_exercise_dropdown_hides_excluded_preacher_curl_by_default(fitness_app):
    response = fitness_app.app.test_client().get("/api/exercises")

    assert response.status_code == 200
    names = {ex["name"] for ex in response.get_json()["exercises"]}
    assert "Biceps Curl" in names
    assert "Preacher Curl" not in names


def test_machine_filter_includes_biceps_curl_and_excludes_preacher_curl(fitness_app):
    names = [ex["name"] for ex in fitness_app._filtered_exercise_library("machines_only")]

    assert "Biceps Curl" in names
    assert "Preacher Curl" not in names
    assert "Cable Biceps Curl" not in names


def test_preferred_brand_settings_rank_matching_machine_options_first(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "EXERCISE_LIBRARY",
        [
            {"name": "Generic Machine Curl", "muscle": "biceps", "equipment": "machine"},
            {
                "name": "Hoist Machine Curl",
                "muscle": "biceps",
                "equipment": "machine",
                "equipment_brands": ["Hoist"],
            },
        ],
    )
    fitness_app.USER_SETTINGS["excluded_exercises"] = []
    fitness_app.USER_SETTINGS["preferred_equipment_brands"] = ["Hoist"]

    names = [ex["name"] for ex in fitness_app._filtered_exercise_library("machines_only")]

    assert names == ["Hoist Machine Curl", "Generic Machine Curl"]


def test_alternatives_preserve_preferred_brand_order(fitness_app):
    response = fitness_app.app.test_client().get("/api/exercises/alternatives/quads")

    assert response.status_code == 200
    names = [ex["name"] for ex in response.get_json()["alternatives"]]
    assert names.index("Leg Press") < names.index("Leg Extension")
    assert names.index("Leg Extension") < names.index("Hack Squat")


def test_settings_accept_case_insensitive_exercise_exclusions(fitness_app):
    response = fitness_app.app.test_client().post(
        "/api/settings",
        json={
            "preferred_equipment_brands": ["Hoist", "hoist", "Nautilus"],
            "excluded_exercises": ["preacher curl"],
        },
    )

    assert response.status_code == 200
    assert response.get_json()["settings"]["preferred_equipment_brands"] == ["Hoist", "Nautilus"]
    assert response.get_json()["settings"]["excluded_exercises"] == ["Preacher Curl"]


def test_next_workout_can_recommend_biceps_without_preacher_curl(fitness_app):
    recommendation = fitness_app.generate_next_workout([], [], available_time=240)
    exercise_names = [ex["exercise"] for ex in recommendation["exercises"]]

    assert "Biceps Curl" in exercise_names
    assert "Preacher Curl" not in exercise_names


def test_swap_rejects_excluded_preacher_curl(fitness_app, monkeypatch):
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Biceps Curl",
                "muscle": "biceps",
                "is_compound": False,
                "target_weight": 40,
                "target_reps": 12,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "Preacher Curl",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "New exercise is excluded by current exercise preferences"


def test_swap_accepts_biceps_curl_alias_as_canonical_machine_curl(fitness_app, monkeypatch):
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Hammer Curl",
                "muscle": "biceps",
                "is_compound": False,
                "target_weight": 40,
                "target_reps": 12,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "Hoist Biceps Curl",
        },
    )

    assert response.status_code == 200
    exercise = response.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Biceps Curl"


def test_ai_adjust_swap_preserves_preferred_brand_order(fitness_app):
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Shoulder Press",
                "muscle": "shoulders",
                "is_compound": True,
                "target_weight": 60,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    intent = {
        "swap": [
            {
                "replace_exercise": "Shoulder Press",
                "target_muscle": "quads",
                "reason": "test",
            }
        ]
    }

    patched, _notes = fitness_app._apply_intent_patch(
        recommendation,
        intent,
        fitness_app.GOAL_PARAMETERS[fitness_app.TrainingGoal.HYPERTROPHY.value],
        1,
        fitness_app.MESOCYCLE_PLAN[1],
        None,
        "machines_only",
    )

    assert patched["exercises"][0]["exercise"] == "Leg Press"


def test_import_backup_reapplies_new_equipment_defaults(fitness_app):
    response = fitness_app.app.test_client().post(
        "/api/import-backup",
        json={
            "data": {
                "settings": {
                    "training_goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
                    "equipment_preference": "machines_only",
                }
            }
        },
    )

    assert response.status_code == 200
    assert fitness_app.USER_SETTINGS["preferred_equipment_brands"] == ["Hoist", "Nautilus"]
    assert fitness_app.USER_SETTINGS["excluded_exercises"] == ["Preacher Curl"]
    names = [ex["name"] for ex in fitness_app._filtered_exercise_library("machines_only")]
    assert "Preacher Curl" not in names


def test_ai_adjust_cache_key_changes_when_brand_preferences_change(fitness_app):
    recommendation = {
        "id": "fit-63-cache",
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 60,
        "exercises": [
            {
                "exercise": "Shoulder Press",
                "muscle": "shoulders",
                "target_sets": 3,
                "target_reps": 10,
                "target_weight": 60,
                "rpe_target": 8,
            }
        ],
    }

    fitness_app.USER_SETTINGS["preferred_equipment_brands"] = ["Hoist"]
    hoist_key = fitness_app._ai_cache_key(
        recommendation,
        "swap shoulders to quads",
        "2026-05-18",
        "test-model",
        "machines_only",
    )
    fitness_app.USER_SETTINGS["preferred_equipment_brands"] = ["Nautilus"]
    nautilus_key = fitness_app._ai_cache_key(
        recommendation,
        "swap shoulders to quads",
        "2026-05-18",
        "test-model",
        "machines_only",
    )

    assert hoist_key != nautilus_key
