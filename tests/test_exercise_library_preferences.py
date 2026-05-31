from __future__ import annotations

import copy
import importlib
import json
from datetime import datetime

import pytest
import lm_studio_adapter


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


def test_swap_returns_structured_not_found_for_unknown_custom_exercise_name(fitness_app, monkeypatch):
    """FIT-117 contract: the swap modal's "Or type your own" input sends the
    typed string to /api/workout/swap. When the typed name isn't in the
    library, the endpoint MUST return a structured 404 with the
    'Unknown exercise name' message — that's what the JS
    `applyCustomSwap` matches on to render the friendly
    "…isn't in the exercise library yet" inline error rather than wiping
    the alternatives picker.
    """
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Seated Row",
                "muscle": "back",
                "is_compound": True,
                "target_weight": 80,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "Quantum Bicep Levitation",
        },
    )

    assert response.status_code == 404
    body = response.get_json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "Unknown exercise name"


def test_swap_returns_structured_muscle_mismatch_for_cross_group_custom_exercise(fitness_app, monkeypatch):
    """FIT-117 contract: when the typed name resolves but belongs to a
    different muscle group than the current exercise, the endpoint MUST
    return a structured 400 with the 'muscle group' message. The JS
    `applyCustomSwap` keys off the 'muscle group' substring to render
    the friendly "isn't a {muscle}-group exercise" inline error.
    """
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Seated Row",
                "muscle": "back",
                "is_compound": True,
                "target_weight": 80,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "Lateral Raise",
        },
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["code"] == "invalid_field"
    assert "muscle group" in body["error"]["message"]


def test_swap_accepts_custom_typed_same_muscle_exercise_name(fitness_app, monkeypatch):
    """FIT-117 happy path: typing the exact canonical name of an in-library,
    same-muscle exercise swaps successfully and returns the updated
    recommendation the JS pipes into the active workout / next workout
    rendering.
    """
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Seated Row",
                "muscle": "back",
                "is_compound": True,
                "target_weight": 80,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    # Lat Pulldown is back/machine, allowed by the fixture's machines_only
    # equipment preference. Cable / bodyweight back alternatives are
    # filtered out by the same preference so they're not the right
    # ground truth for the happy path under this fixture.
    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "Lat Pulldown",
        },
    )

    assert response.status_code == 200
    exercise = response.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Lat Pulldown"
    assert "Swapped from Seated Row" in exercise["rationale"]


def _swap_recommendation_fixture(fitness_app):
    return {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
            },
            {
                "exercise": "Tricep Pushdown",
                "muscle": "triceps",
                "is_compound": False,
                "target_weight": 50,
                "target_reps": 12,
                "target_sets": 3,
            },
            {
                "exercise": "Overhead Tricep Extension",
                "muscle": "triceps",
                "is_compound": False,
                "target_weight": 35,
                "target_reps": 12,
                "target_sets": 3,
            },
        ],
    }


def test_swap_recommend_endpoint_deterministic_without_lm_studio(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = _swap_recommendation_fixture(fitness_app)
    original = copy.deepcopy(recommendation)
    metrics = []
    monkeypatch.setattr(fitness_app, "_lm_studio", None)
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    monkeypatch.setattr(fitness_app, "_workout_plan_content_fingerprint", lambda _recommendation: "fp-test")
    monkeypatch.setattr(
        fitness_app,
        "_ai_metric_log",
        lambda outcome, **kwargs: metrics.append((outcome, kwargs)),
    )

    response = fitness_app.app.test_client().post(
        "/api/workout/swap/recommend",
        json={"workout_index": 0, "typed_target": "tricep extension"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["source"] == "deterministic"
    assert body["target_canonical"] == "Triceps Extension"
    assert body["target_muscle"] == "triceps"
    assert body["slot"] == {
        "exercise_index": 1,
        "name": "Tricep Pushdown",
        "muscle": "triceps",
    }
    assert body["commit"] == {
        "workout_index": 0,
        "exercise_index": 1,
        "new_exercise_name": "Triceps Extension",
        "plan_fingerprint": "fp-test",
    }
    assert body["recommended_load"] is None
    assert body["plan_fingerprint"] == "fp-test"
    assert recommendation == original
    assert metrics[0][0] == "fallback"
    assert metrics[0][1]["reason"] == "swap_recommend: adapter_missing"


def test_swap_recommend_endpoint_returns_similar_history_recommended_load(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Shoulder Press",
                "muscle": "shoulders",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "_lm_studio", None)
    monkeypatch.setattr(fitness_app, "BASELINES_DATA", {})
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    monkeypatch.setattr(fitness_app, "_workout_plan_content_fingerprint", lambda _recommendation: "fp-load")
    monkeypatch.setattr(
        fitness_app,
        "calculate_progression_status",
        lambda _workouts: {"Lateral Raise": {"current_e1rm": 100, "status": "On Track"}},
    )
    monkeypatch.setattr(fitness_app, "_ai_metric_log", lambda *_args, **_kwargs: None)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap/recommend",
        json={"workout_index": 0, "typed_target": "Machine Deltoid Raise"},
    )

    assert response.status_code == 200
    recommended_load = response.get_json()["recommended_load"]
    assert recommended_load == {
        "weight": 67,
        "source": "similar_history",
        "inferred_from": "Lateral Raise",
        "confidence": "low",
        "explanation": (
            "Started from your Lateral Raise history, adjusted for partial exercise "
            "similarity, then used 85% as the first-session start for RIR 3-4."
        ),
    }


def test_swap_recommend_endpoint_prefers_saved_baseline_over_similar_history_preview(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Shoulder Press",
                "muscle": "shoulders",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "_lm_studio", None)
    monkeypatch.setattr(fitness_app, "BASELINES_DATA", {"Machine Deltoid Raise": 70})
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    monkeypatch.setattr(fitness_app, "_workout_plan_content_fingerprint", lambda _recommendation: "fp-baseline")
    monkeypatch.setattr(
        fitness_app,
        "calculate_progression_status",
        lambda _workouts: {"Lateral Raise": {"current_e1rm": 100, "status": "On Track"}},
    )
    monkeypatch.setattr(fitness_app, "_ai_metric_log", lambda *_args, **_kwargs: None)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap/recommend",
        json={"workout_index": 0, "typed_target": "Machine Deltoid Raise"},
    )

    assert response.status_code == 200
    assert response.get_json()["recommended_load"] is None


def test_load_similarity_reads_singular_movement_pattern_metadata(fitness_app):
    biceps = fitness_app._resolve_exercise_definition("Biceps Curl")
    cable_biceps = fitness_app._resolve_exercise_definition("Cable Biceps Curl")

    assert "elbow_flexion" in fitness_app._exercise_movement_patterns(biceps)
    assert "elbow_flexion" in fitness_app._exercise_movement_patterns(cable_biceps)


def test_swap_recommend_endpoint_does_not_cross_triceps_extension_load_contamination(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = _swap_recommendation_fixture(fitness_app)
    monkeypatch.setattr(fitness_app, "_lm_studio", None)
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    monkeypatch.setattr(fitness_app, "_workout_plan_content_fingerprint", lambda _recommendation: "fp-triceps")
    monkeypatch.setattr(
        fitness_app,
        "calculate_progression_status",
        lambda _workouts: {"Overhead Tricep Extension": {"current_e1rm": 80, "status": "On Track"}},
    )
    monkeypatch.setattr(fitness_app, "_ai_metric_log", lambda *_args, **_kwargs: None)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap/recommend",
        json={"workout_index": 0, "typed_target": "tricep extension"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["target_canonical"] == "Triceps Extension"
    assert body["recommended_load"] is None


def test_swap_endpoint_rejects_stale_recommendation_fingerprint(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    original = copy.deepcopy(recommendation)
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "workout_index": 0,
            "exercise_index": 0,
            "new_exercise_name": "Incline Press",
            "plan_fingerprint": "old-plan",
        },
    )

    assert response.status_code == 409
    body = response.get_json()
    assert body["error"]["code"] == "stale_plan"
    assert "Refresh the recommendation" in body["error"]["message"]
    assert recommendation == original


def test_swap_endpoint_allows_matching_recommendation_fingerprint(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    current_plan = fitness_app._workout_plan_content_fingerprint(recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "workout_index": 0,
            "exercise_index": 0,
            "new_exercise_name": "Incline Press",
            "plan_fingerprint": current_plan,
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["resolved_exercise_name"] == "Incline Press"
    assert body["recommendation"]["exercises"][0]["exercise"] == "Incline Press"


def test_swap_endpoint_validates_fingerprint_against_indexed_recommendation(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    indexed_recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    different_last = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Lat Pulldown",
                "muscle": "back",
                "is_compound": True,
                "target_weight": 70,
                "target_reps": 8,
                "target_sets": 4,
            }
        ],
    }
    indexed_plan = fitness_app._workout_plan_content_fingerprint(indexed_recommendation)
    monkeypatch.setattr(fitness_app, "WORKOUT_RECOMMENDATIONS", [indexed_recommendation])
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", different_last)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "workout_index": 0,
            "exercise_index": 0,
            "new_exercise_name": "Incline Press",
            "plan_fingerprint": indexed_plan,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["recommendation"]["exercises"][0]["exercise"] == "Incline Press"
    assert different_last["exercises"][0]["exercise"] == "Lat Pulldown"


def test_swap_endpoint_rejects_plan_mutated_after_recommendation_fingerprint(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    stale_plan = fitness_app._workout_plan_content_fingerprint(recommendation)
    recommendation["exercises"][0]["exercise"] = "Pec Fly"
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "workout_index": 0,
            "exercise_index": 0,
            "new_exercise_name": "Incline Press",
            "plan_fingerprint": stale_plan,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "stale_plan"


def test_swap_endpoint_rejects_mesocycle_mutated_after_recommendation_fingerprint(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    stale_plan = fitness_app._workout_plan_content_fingerprint(recommendation)
    recommendation["mesocycle"] = {"week": 4}
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "workout_index": 0,
            "exercise_index": 0,
            "new_exercise_name": "Incline Press",
            "plan_fingerprint": stale_plan,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "stale_plan"


def test_swap_endpoint_rejects_any_recommendation_content_mutation(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
                "rationale": "original plan",
            }
        ],
    }
    stale_plan = fitness_app._workout_plan_content_fingerprint(recommendation)
    recommendation["exercises"][0]["rationale"] = "newer plan content"
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "workout_index": 0,
            "exercise_index": 0,
            "new_exercise_name": "Incline Press",
            "plan_fingerprint": stale_plan,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "stale_plan"


def test_swap_recommend_endpoint_projects_planned_exercise_keys_to_adapter(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = _swap_recommendation_fixture(fitness_app)
    captured = {}

    class FakeAdapter:
        class LmStudioError(Exception):
            pass

        def recommend_swap_target(self, typed_target, *, planned_exercises, library_candidates, equipment_pref):
            captured["typed_target"] = typed_target
            captured["planned_exercises"] = planned_exercises
            captured["library_candidates"] = library_candidates
            captured["equipment_pref"] = equipment_pref
            return {
                "target_canonical": "Triceps Extension",
                "slot_index": 0,
                "confidence": 0.91,
                "reason": "Closest triceps isolation slot.",
                "alternatives": [{"slot_index": 1, "reason": "Also triceps isolation."}],
            }

    monkeypatch.setattr(fitness_app, "_lm_studio", FakeAdapter())
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    monkeypatch.setattr(fitness_app, "_workout_recommendation_fingerprint", lambda: "fp-ai")
    monkeypatch.setattr(fitness_app, "_ai_metric_log", lambda *_args, **_kwargs: None)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap/recommend",
        json={"workout_index": 0, "typed_target": "tricep extension"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["source"] == "ai"
    assert body["target_canonical"] == "Triceps Extension"
    assert body["slot"]["exercise_index"] == 1
    assert body["alternatives"] == [
        {
            "exercise_index": 2,
            "name": "Overhead Tricep Extension",
            "muscle": "triceps",
            "reason": "Also triceps isolation.",
        }
    ]
    assert captured["typed_target"] == "tricep extension"
    assert captured["equipment_pref"] == "machines_and_cables"
    assert captured["planned_exercises"][0]["name"] == "Tricep Pushdown"
    assert captured["planned_exercises"][0]["compound"] is False
    assert captured["planned_exercises"][0]["exercise_index"] == 1
    assert {candidate["muscle"] for candidate in captured["library_candidates"]} == {"triceps"}


@pytest.mark.parametrize(
    ("message", "expected_reason", "expected_response_reason"),
    [
        ("busy: another inference request is still in flight", "swap_recommend: busy", "AI busy - used best match, confirm?"),
        ("all endpoints failed", "swap_recommend: outage", "AI outage - used best match, confirm?"),
    ],
)
def test_swap_recommend_endpoint_degrades_on_ai_failure(
    fitness_app,
    monkeypatch,
    message,
    expected_reason,
    expected_response_reason,
):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = _swap_recommendation_fixture(fitness_app)
    metrics = []

    class FakeAdapter:
        class LmStudioError(Exception):
            pass

        def recommend_swap_target(self, *_args, **_kwargs):
            raise self.LmStudioError(message)

    monkeypatch.setattr(fitness_app, "_lm_studio", FakeAdapter())
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    monkeypatch.setattr(fitness_app, "_workout_recommendation_fingerprint", lambda: "fp-fallback")
    monkeypatch.setattr(
        fitness_app,
        "_ai_metric_log",
        lambda outcome, **kwargs: metrics.append((outcome, kwargs)),
    )

    response = fitness_app.app.test_client().post(
        "/api/workout/swap/recommend",
        json={"workout_index": 0, "typed_target": "tricep extension"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["source"] == "deterministic"
    assert body["target_canonical"] == "Triceps Extension"
    assert body["reason"] == expected_response_reason
    assert metrics[0][0] == "fallback"
    assert metrics[0][1]["reason"] == expected_reason


def test_swap_recommend_endpoint_returns_no_match_without_same_muscle_slot(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "is_compound": True,
                "target_weight": 90,
                "target_reps": 10,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "_lm_studio", None)
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap/recommend",
        json={"workout_index": 0, "typed_target": "tricep extension"},
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "no_match"


def test_swap_resolves_semantic_triceps_extension_and_uses_backend_load(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Cable Pushdown",
                "muscle": "triceps",
                "is_compound": False,
                "target_weight": 55,
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
            "new_exercise_name": "Tricep extension",
        },
    )

    assert response.status_code == 200
    exercise = response.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Triceps Extension"
    assert exercise["target_weight"] > 0
    assert exercise["load_source"] in {"hardcoded", "baseline_json", "progression", "similar_history"}
    assert "Swapped from Cable Pushdown" in exercise["rationale"]


def test_swap_resolves_plural_triceps_extension_deterministically(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Cable Pushdown",
                "muscle": "triceps",
                "is_compound": False,
                "target_weight": 55,
                "target_reps": 12,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("plural triceps extension should resolve before LLM fallback")

    monkeypatch.setattr(fitness_app._lm_studio, "resolve_swap_candidate", fail_if_called)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "Triceps extension",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["recommendation"]["exercises"][0]["exercise"] == "Triceps Extension"


def test_swap_uses_adapter_fallback_for_inconclusive_free_text(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Cable Pushdown",
                "muscle": "triceps",
                "is_compound": False,
                "target_weight": 55,
                "target_reps": 12,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    calls = {}

    def fake_resolve(typed_name, *, current_exercise, target_muscle, candidates, **_kwargs):
        calls["typed_name"] = typed_name
        calls["current_exercise"] = current_exercise
        calls["target_muscle"] = target_muscle
        calls["candidate_names"] = [candidate["name"] for candidate in candidates]
        return {"canonical_name": "Overhead Tricep Extension", "confidence": 0.88, "reason": "mock"}

    monkeypatch.setattr(fitness_app._lm_studio, "resolve_swap_candidate", fake_resolve)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "arm straightener",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["recommendation"]["exercises"][0]["exercise"] == "Overhead Tricep Extension"
    assert calls == {
        "typed_name": "arm straightener",
        "current_exercise": "Cable Pushdown",
        "target_muscle": "triceps",
        "candidate_names": ["Seated Dip", "Triceps Extension", "Overhead Tricep Extension", "Tricep Pushdown"],
    }


def test_swap_adapter_failure_preserves_structured_not_found(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Cable Pushdown",
                "muscle": "triceps",
                "is_compound": False,
                "target_weight": 55,
                "target_reps": 12,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    def fail_resolve(*_args, **_kwargs):
        raise fitness_app._lm_studio.LmStudioError("timeout")

    monkeypatch.setattr(fitness_app._lm_studio, "resolve_swap_candidate", fail_resolve)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "arm straightener",
        },
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == {"code": "not_found", "message": "Unknown exercise name"}


def test_swap_adapter_candidate_outside_prefilter_is_rejected(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Cable Pushdown",
                "muscle": "triceps",
                "is_compound": False,
                "target_weight": 55,
                "target_reps": 12,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    def bad_resolve(*_args, **_kwargs):
        return {"canonical_name": "Lateral Raise", "confidence": 0.9, "reason": "wrong muscle"}

    monkeypatch.setattr(fitness_app._lm_studio, "resolve_swap_candidate", bad_resolve)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "arm straightener",
        },
    )

    assert response.status_code == 404
    assert response.get_json()["error"]["message"] == "Unknown exercise name"


def test_swap_adapter_normalizes_candidate_membership(fitness_app, monkeypatch):
    validations = []

    def fake_completion(_path, _payload, timeout, validate, preflighted_candidate=None):
        bad = {
            "action": "resolve",
            "canonical_name": "Lateral Raise",
            "confidence": 0.9,
            "reason": "wrong muscle",
            "clarification_question": None,
            "options": [],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(bad)
        good = {
            "action": "resolve",
            "canonical_name": " overhead tricep extension ",
            "confidence": 0.9,
            "reason": "case",
            "clarification_question": None,
            "options": [],
        }
        validate(good)
        validations.append(good)
        return good

    monkeypatch.setattr(lm_studio_adapter, "_completion_json", fake_completion)
    result = lm_studio_adapter.resolve_swap_candidate(
        "arm straightener",
        current_exercise="Cable Pushdown",
        target_muscle="triceps",
        candidates=[
            {"name": "Seated Dip", "equipment": "machine", "aliases": [], "compound": True},
            {"name": "Overhead Tricep Extension", "equipment": "cable", "aliases": [], "compound": False},
        ],
    )

    assert result["canonical_name"] == "Overhead Tricep Extension"
    assert validations == [
        {
            "action": "resolve",
            "canonical_name": "Overhead Tricep Extension",
            "confidence": 0.9,
            "reason": "case",
            "clarification_question": None,
            "options": [],
        }
    ]


def test_swap_adapter_accepts_clarify_payload_and_normalizes_options(monkeypatch):
    def fake_completion(_path, _payload, timeout, validate, preflighted_candidate=None):
        empty_options = {
            "action": "clarify",
            "canonical_name": None,
            "confidence": 0.4,
            "reason": "ambiguous crunch request",
            "clarification_question": "Which crunch?",
            "options": [],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(empty_options)

        one_distinct_option = {
            "action": "clarify",
            "canonical_name": None,
            "confidence": 0.4,
            "reason": "ambiguous crunch request",
            "clarification_question": "Which crunch?",
            "options": [
                {"name": "Cable Crunch", "distinction": "Cable station"},
                {"name": " cable crunch ", "distinction": "Duplicate"},
            ],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(one_distinct_option)

        missing_question = {
            "action": "clarify",
            "canonical_name": None,
            "confidence": 0.4,
            "reason": "ambiguous crunch request",
            "clarification_question": "",
            "options": [
                {"name": "Cable Crunch", "distinction": "Cable station"},
                {"name": "Crunch Machine", "distinction": "Machine"},
            ],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(missing_question)

        blank_distinction = {
            "action": "clarify",
            "canonical_name": None,
            "confidence": 0.4,
            "reason": "ambiguous crunch request",
            "clarification_question": "Which crunch?",
            "options": [
                {"name": "Cable Crunch", "distinction": " "},
                {"name": "Crunch Machine", "distinction": "Machine"},
            ],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(blank_distinction)

        parsed = {
            "action": "clarify",
            "canonical_name": None,
            "confidence": 0.4,
            "reason": "ambiguous crunch request",
            "clarification_question": "Which crunch?",
            "options": [
                {"name": " cable crunch ", "distinction": "Cable station"},
                {"name": "Cable Crunch", "distinction": "Duplicate"},
                {"name": "Crunch Machine", "distinction": "Machine"},
            ],
        }
        validate(parsed)
        return parsed

    monkeypatch.setattr(lm_studio_adapter, "_completion_json", fake_completion)

    result = lm_studio_adapter.resolve_swap_candidate(
        "crunch",
        current_exercise="Plank",
        target_muscle="core",
        candidates=[
            {"name": "Crunch Machine", "equipment": "machine"},
            {"name": "Cable Crunch", "equipment": "cable"},
        ],
    )

    assert result["action"] == "clarify"
    assert result["canonical_name"] is None
    assert result["options"] == [
        {"name": "Cable Crunch", "distinction": "Cable station"},
        {"name": "Crunch Machine", "distinction": "Machine"},
    ]


def test_swap_adapter_payload_preserves_movement_metadata(monkeypatch):
    seen = {}

    def fake_completion(_path, payload, timeout, validate, preflighted_candidate=None):
        seen["candidates"] = payload["messages"][-1]["content"]
        parsed = {
            "action": "resolve",
            "canonical_name": "Triceps Extension",
            "confidence": 0.9,
            "reason": "metadata",
            "clarification_question": None,
            "options": [],
        }
        validate(parsed)
        return parsed

    monkeypatch.setattr(lm_studio_adapter, "_completion_json", fake_completion)

    lm_studio_adapter.resolve_swap_candidate(
        "triceps extension",
        current_exercise="Cable Pushdown",
        target_muscle="triceps",
        candidates=[
            {
                "name": "Triceps Extension",
                "equipment": "machine",
                "aliases": ["Machine Triceps Extension"],
                "compound": False,
                "movement_pattern": "elbow_extension",
                "body_position": "seated",
                "shoulder_position": "neutral",
                "primary_emphasis": "triceps_overall",
                "match_tokens": ["machine"],
                "confusable_with": [{"name": "Overhead Tricep Extension", "distinction": "not overhead"}],
                "disambiguation": "Default machine variant.",
            }
        ],
    )

    payload = json.loads(seen["candidates"])
    candidate = payload["candidates"][0]
    assert candidate["movement_pattern"] == "elbow_extension"
    assert candidate["body_position"] == "seated"
    assert candidate["shoulder_position"] == "neutral"
    assert candidate["confusable_with"][0]["name"] == "Overhead Tricep Extension"


def test_swap_recommend_adapter_validates_and_dedupes_slots(monkeypatch):
    seen = {}

    def fake_completion(_path, payload, timeout, validate, preflighted_candidate=None):
        seen["payload"] = payload
        bad_target = {
            "target_canonical": "Imaginary Press",
            "slot_index": 0,
            "confidence": 0.8,
            "reason": "bad",
            "alternatives": [],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(bad_target)

        bad_slot = {
            "target_canonical": "Triceps Extension",
            "slot_index": 4,
            "confidence": 0.8,
            "reason": "bad",
            "alternatives": [],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(bad_slot)

        bool_slot = {
            "target_canonical": "Triceps Extension",
            "slot_index": True,
            "confidence": 0.8,
            "reason": "bad",
            "alternatives": [],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(bool_slot)

        bool_alternative = {
            "target_canonical": "Triceps Extension",
            "slot_index": 1,
            "confidence": 0.8,
            "reason": "bad",
            "alternatives": [{"slot_index": False, "reason": "bad"}],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(bool_alternative)

        partial_target = {
            "target_canonical": "Triceps Extension",
            "slot_index": None,
            "confidence": 0.4,
            "reason": "partial",
            "alternatives": [],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(partial_target)

        partial_slot = {
            "target_canonical": None,
            "slot_index": 1,
            "confidence": 0.4,
            "reason": "partial",
            "alternatives": [],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(partial_slot)

        wrong_muscle = {
            "target_canonical": "Pec Fly",
            "slot_index": 1,
            "confidence": 0.8,
            "reason": "wrong muscle",
            "alternatives": [],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(wrong_muscle)

        wrong_alternative_muscle = {
            "target_canonical": "Triceps Extension",
            "slot_index": 1,
            "confidence": 0.8,
            "reason": "wrong alt muscle",
            "alternatives": [{"slot_index": 2, "reason": "chest slot"}],
        }
        with pytest.raises(lm_studio_adapter.LmStudioError):
            validate(wrong_alternative_muscle)

        good = {
            "target_canonical": " triceps extension ",
            "slot_index": 1,
            "confidence": 0.86,
            "reason": "replace isolation slot",
            "alternatives": [
                {"slot_index": 1, "reason": "duplicate selected slot"},
                {"slot_index": 0, "reason": "other triceps slot"},
                {"slot_index": 0, "reason": "duplicate"},
            ],
        }
        validate(good)
        return good

    monkeypatch.setattr(lm_studio_adapter, "_completion_json", fake_completion)

    result = lm_studio_adapter.recommend_swap_target(
        "triceps extension",
        planned_exercises=[
            {"exercise": "Seated Dip", "muscle": "triceps", "is_compound": True},
            {"exercise": "Cable Pushdown", "muscle": "triceps", "is_compound": False},
            {"exercise": "Pec Fly", "muscle": "chest", "is_compound": False},
        ],
        library_candidates=[
            {"name": "Triceps Extension", "muscle": "triceps", "equipment": "machine"},
            {"name": "Overhead Tricep Extension", "muscle": "triceps", "equipment": "cable"},
            {"name": "Pec Fly", "muscle": "chest", "equipment": "machine"},
        ],
        equipment_pref="machines_and_cables",
    )

    payload = json.loads(seen["payload"]["messages"][-1]["content"])
    assert payload["library_candidates"][0]["muscle"] == "triceps"
    assert result["target_canonical"] == "Triceps Extension"
    assert result["slot_index"] == 1
    assert result["alternatives"] == [{"slot_index": 0, "reason": "other triceps slot"}]


def test_swap_recommend_adapter_allows_null_target_and_slot(monkeypatch):
    def fake_completion(_path, _payload, timeout, validate, preflighted_candidate=None):
        parsed = {
            "target_canonical": None,
            "slot_index": None,
            "confidence": 0.1,
            "reason": "unclear",
            "alternatives": [],
        }
        validate(parsed)
        return parsed

    monkeypatch.setattr(lm_studio_adapter, "_completion_json", fake_completion)

    result = lm_studio_adapter.recommend_swap_target(
        "something unclear",
        planned_exercises=[{"exercise": "Cable Pushdown", "muscle": "triceps"}],
        library_candidates=[{"name": "Triceps Extension", "equipment": "machine"}],
    )

    assert result["target_canonical"] is None
    assert result["slot_index"] is None


def test_swap_prefilters_equipment_before_adapter_resolution(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_only"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Cable Pushdown",
                "muscle": "triceps",
                "is_compound": False,
                "target_weight": 55,
                "target_reps": 12,
                "target_sets": 3,
            }
        ],
    }
    monkeypatch.setattr(fitness_app, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    seen = {}

    def fake_resolve(*_args, candidates, **_kwargs):
        seen["candidate_names"] = [candidate["name"] for candidate in candidates]
        seen["triceps_extension"] = next(candidate for candidate in candidates if candidate["name"] == "Triceps Extension")
        return {"canonical_name": "Overhead Tricep Extension", "confidence": 0.9, "reason": "mock"}

    monkeypatch.setattr(fitness_app._lm_studio, "resolve_swap_candidate", fake_resolve)

    response = fitness_app.app.test_client().post(
        "/api/workout/swap",
        json={
            "exercise_index": 0,
            "new_exercise_name": "arm straightener",
        },
    )

    assert response.status_code == 404
    assert response.get_json()["error"]["message"] == "Unknown exercise name"
    assert seen["candidate_names"] == ["Seated Dip", "Triceps Extension"]
    assert seen["triceps_extension"]["shoulder_position"] == "neutral"
    assert seen["triceps_extension"]["disambiguation"]


def test_swap_rejects_current_exercise_no_op(fitness_app, monkeypatch):
    fitness_app.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Cable Pushdown",
                "muscle": "triceps",
                "is_compound": False,
                "target_weight": 55,
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
            "new_exercise_name": "Cable Pushdown",
        },
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["code"] == "invalid_field"
    assert "different from the current exercise" in body["error"]["message"]


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


def test_exercise_library_has_joint_loading_metadata(fitness_app):
    missing = [ex["name"] for ex in fitness_app.EXERCISE_LIBRARY if not ex.get("joints_loaded")]

    assert missing == []
    chest_press = fitness_app.EXERCISE_LOOKUP["Chest Press"]
    leg_extension = fitness_app.EXERCISE_LOOKUP["Leg Extension"]
    biceps_curl = fitness_app.EXERCISE_LOOKUP["Biceps Curl"]
    romanian_deadlift = fitness_app.EXERCISE_LOOKUP["Romanian Deadlift"]
    hanging_leg_raise = fitness_app.EXERCISE_LOOKUP["Hanging Leg Raise"]
    assert set(chest_press["joints_loaded"]) == {"shoulder", "elbow"}
    assert leg_extension["joints_loaded"] == ["knee"]
    assert set(biceps_curl["joints_loaded"]) == {"elbow", "wrist"}
    assert set(romanian_deadlift["joints_loaded"]) == {"hip", "knee", "spine"}
    assert set(hanging_leg_raise["joints_loaded"]) == {"spine", "hip"}


def test_swap_library_models_triceps_extension_and_erectors(fitness_app):
    triceps_extension = fitness_app.EXERCISE_LOOKUP["Triceps Extension"]
    overhead = fitness_app.EXERCISE_LOOKUP["Overhead Tricep Extension"]
    back_extension = fitness_app.EXERCISE_LOOKUP["Back Extension"]

    assert triceps_extension["equipment"] == "machine"
    assert triceps_extension["joints_loaded"] == ["elbow"]
    assert triceps_extension["shoulder_position"] == "neutral"
    assert "Tricep Extension" in triceps_extension["aliases"]
    assert "Tricep Extension" not in overhead.get("aliases", [])
    assert "Triceps Extension" not in overhead.get("aliases", [])
    assert overhead["shoulder_position"] == "overhead"
    assert back_extension["muscle"] == "erectors"
    assert set(back_extension["joints_loaded"]) == {"spine", "hip"}


def test_generate_next_workout_programs_erectors_across_full_cycle(fitness_app):
    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.HYPERTROPHY.value,
        available_time=120,
        persist=False,
        include_open_wearables_readiness=False,
    )

    exercises_by_muscle = {
        exercise["muscle"]: exercise["exercise"]
        for exercise in recommendation["exercises"]
    }

    assert exercises_by_muscle["erectors"] == "Back Extension"


def test_legacy_back_extension_volume_counts_toward_erectors(fitness_app):
    today = datetime.now().strftime("%Y-%m-%d")
    volume = fitness_app.calculate_volume(
        [
            {
                "date": today,
                "exercises": [
                    {
                        "machine": "Back Extension",
                        "muscle_group": "back",
                        "sets": [
                            {"weight_lbs": 80, "reps": 10},
                            {"weight_lbs": 80, "reps": 10},
                        ],
                    }
                ],
            }
        ],
        weeks=4,
    )

    assert "erectors" in volume
    assert "back" not in volume
    assert volume["erectors"]["sets"] == 2


def test_legacy_low_back_volume_counts_toward_erectors(fitness_app):
    today = datetime.now().strftime("%Y-%m-%d")
    volume = fitness_app.calculate_volume(
        [
            {
                "date": today,
                "exercises": [
                    {
                        "machine": "Low Back",
                        "muscle_group": "back",
                        "sets": [
                            {"weight_lbs": 80, "reps": 10},
                        ],
                    }
                ],
            }
        ],
        weeks=4,
    )

    assert "erectors" in volume
    assert "back" not in volume
    assert volume["erectors"]["sets"] == 1


def test_ai_adjust_removes_exercises_loading_side_specific_avoided_joint(fitness_app):
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {"exercise": "Chest Press", "muscle": "chest", "target_sets": 3, "target_reps": 10},
            {"exercise": "Leg Extension", "muscle": "quads", "target_sets": 3, "target_reps": 10},
        ],
    }
    intent = {
        "avoid_joints": [{"side": "left", "joint": "shoulder"}],
        "avoid_muscles": [],
        "swap": [],
    }

    patched, notes = fitness_app._apply_intent_patch(
        recommendation,
        intent,
        fitness_app.GOAL_PARAMETERS[fitness_app.TrainingGoal.HYPERTROPHY.value],
        1,
        fitness_app.MESOCYCLE_PLAN[1],
        None,
        "machines_only",
    )

    assert [ex["exercise"] for ex in patched["exercises"]] == ["Leg Extension"]
    assert any("left shoulder" in note for note in notes)


def test_ai_adjust_swap_respects_avoided_joint_when_picking_replacement(fitness_app):
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {"exercise": "Shoulder Press", "muscle": "shoulders", "target_sets": 3, "target_reps": 10},
        ],
    }
    intent = {
        "avoid_joints": [{"side": "right", "joint": "knee"}],
        "avoid_muscles": [],
        "swap": [{"replace_exercise": "Shoulder Press", "target_muscle": "quads", "reason": "test"}],
    }

    patched, notes = fitness_app._apply_intent_patch(
        recommendation,
        intent,
        fitness_app.GOAL_PARAMETERS[fitness_app.TrainingGoal.HYPERTROPHY.value],
        1,
        fitness_app.MESOCYCLE_PLAN[1],
        None,
        "machines_only",
    )

    assert patched["exercises"][0]["exercise"] == "Shoulder Press"
    assert any("joint constraints" in note for note in notes)


def test_ai_adjust_swap_runs_before_joint_removal_for_same_source(fitness_app):
    recommendation = {
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "mesocycle": {"week": 1},
        "exercises": [
            {"exercise": "Chest Press", "muscle": "chest", "target_sets": 3, "target_reps": 10},
            {"exercise": "Leg Extension", "muscle": "quads", "target_sets": 3, "target_reps": 10},
        ],
    }
    intent = {
        "avoid_joints": [{"side": "left", "joint": "shoulder"}],
        "avoid_muscles": [],
        "swap": [{"replace_exercise": "Chest Press", "target_muscle": "biceps", "reason": "left shoulder sore"}],
    }

    patched, notes = fitness_app._apply_intent_patch(
        recommendation,
        intent,
        fitness_app.GOAL_PARAMETERS[fitness_app.TrainingGoal.HYPERTROPHY.value],
        1,
        fitness_app.MESOCYCLE_PLAN[1],
        None,
        "machines_only",
    )

    exercise_names = [ex["exercise"] for ex in patched["exercises"]]
    assert "Chest Press" not in exercise_names
    assert "Biceps Curl" in exercise_names
    assert "Leg Extension" in exercise_names
    assert any("Swapped: Chest Press" in note for note in notes)


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


def test_ai_adjust_cache_key_changes_when_joint_taxonomy_changes(fitness_app, monkeypatch):
    recommendation = {
        "id": "fit-21-cache",
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

    original_key = fitness_app._ai_cache_key(
        recommendation,
        "left shoulder sore",
        "2026-05-18",
        "test-model",
        "machines_only",
    )
    modified_library = copy.deepcopy(fitness_app.EXERCISE_LIBRARY)
    for exercise in modified_library:
        if exercise["name"] == "Shoulder Press":
            exercise["joints_loaded"] = ["shoulder", "elbow", "spine"]
            break
    monkeypatch.setattr(fitness_app, "EXERCISE_LIBRARY", modified_library)

    changed_key = fitness_app._ai_cache_key(
        recommendation,
        "left shoulder sore",
        "2026-05-18",
        "test-model",
        "machines_only",
    )

    assert changed_key != original_key


def test_adjust_intent_schema_accepts_side_specific_avoid_joints():
    schema_props = lm_studio_adapter.ADJUST_SCHEMA["properties"]["intent"]["properties"]

    assert "avoid_joints" in schema_props
    lm_studio_adapter._validate_adjust_intent({
        "summary": "Avoid the sore left shoulder.",
        "intent": {
            "avoid_muscles": [],
            "avoid_joints": [{"side": "left", "joint": "shoulder"}],
            "swap": [],
            "rpe_delta": 0,
            "sets_delta_pct": 0,
            "duration_cap_min": 0,
            "drop_cardio": False,
        },
    })
