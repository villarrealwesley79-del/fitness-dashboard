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


def _recommendation_for(module, exercises):
    return {
        "id": "fit-179-rec",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "goal_name": "Hypertrophy",
        "estimated_minutes": 45,
        "mesocycle": {"week": 1},
        "exercises": exercises,
    }


def _rec_exercise(name, muscle, weight=50, sets=3, reps=10):
    return {
        "exercise": name,
        "muscle": muscle,
        "target_sets": sets,
        "target_reps": reps,
        "target_weight": weight,
        "rpe_target": 7,
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


def test_adjust_deterministic_fallback_accepts_bare_tricep_extensions(monkeypatch):
    module = _module(monkeypatch)
    module.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    monkeypatch.setattr(module, "_lm_studio", None)
    monkeypatch.setattr(
        module,
        "WORKOUTS",
        [
            _workout("2026-05-01", "Cable Pushdown", 55, muscle="triceps"),
            _workout("2026-05-08", "Cable Pushdown", 60, muscle="triceps"),
        ],
    )
    monkeypatch.setattr(
        module,
        "LAST_WORKOUT_RECOMMENDATION",
        _recommendation_for(module, [_rec_exercise("Cable Pushdown", "triceps", 55)]),
    )

    response = module.app.test_client().post(
        "/api/workout/adjust",
        json={"constraint": "tricep extensions"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    exercise = payload["recommendation"]["exercises"][0]
    assert payload["status"] == "ok"
    assert payload["meta"]["mode"] == "deterministic_fallback"
    assert exercise["exercise"] == "Overhead Tricep Extension"
    assert exercise["load_source"] == "similar_history"
    assert exercise["load_inference"]["source_exercise"] == "Cable Pushdown"


def test_adjust_deterministic_parser_handles_must_pass_examples(monkeypatch):
    module = _module(monkeypatch)
    module.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    recommendation = _recommendation_for(
        module,
        [
            _rec_exercise("Chest Press", "chest", 100),
            _rec_exercise("Crunch Machine", "core", 60),
            _rec_exercise("Seated Row", "back", 90),
            _rec_exercise("Romanian Deadlift", "hamstrings", 95),
        ],
    )

    pectoral = module._build_deterministic_adjust_swap("pectoral fly", recommendation, "machines_and_cables")
    rotary = module._build_deterministic_adjust_swap("rotary torso", recommendation, "machines_and_cables")
    back_extension = module._build_deterministic_adjust_swap("back extension", recommendation, "all")
    chest_supported = module._build_deterministic_adjust_swap(
        "do chest-supported row instead", recommendation, "machines_and_cables"
    )

    assert pectoral["target_exercise"] == "Pec Fly"
    assert pectoral["replace_exercise"] == "Chest Press"
    assert rotary["target_exercise"] == "Rotary Torso"
    assert rotary["replace_exercise"] == "Crunch Machine"
    assert back_extension["target_exercise"] == "Back Extension"
    assert back_extension["replace_exercise"] == "Romanian Deadlift"
    assert chest_supported["target_exercise"] == "Chest-Supported Row"
    assert chest_supported["replace_exercise"] == "Seated Row"


def test_adjust_deterministic_tie_break_prefers_earlier_plan_slot(monkeypatch):
    module = _module(monkeypatch)
    recommendation = _recommendation_for(
        module,
        [
            _rec_exercise("Chest Press", "chest", 100),
            _rec_exercise("Incline Press", "chest", 95),
        ],
    )

    pectoral = module._build_deterministic_adjust_swap("pectoral fly", recommendation, "machines_and_cables")

    assert pectoral["target_exercise"] == "Pec Fly"
    assert pectoral["replace_exercise"] == "Chest Press"
    assert pectoral["replace_index"] == 0


def test_adjust_deterministic_target_requires_compatible_plan_slot(monkeypatch):
    module = _module(monkeypatch)
    recommendation = _recommendation_for(
        module,
        [
            _rec_exercise("Leg Press", "quads", 180),
            _rec_exercise("Seated Row", "back", 90),
        ],
    )

    triceps = module._build_deterministic_adjust_swap("tricep extensions", recommendation, "machines_and_cables")

    assert triceps is None


def test_adjust_swap_revalidates_replace_index_after_removals(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(module, "WORKOUTS", [])
    recommendation = _recommendation_for(
        module,
        [
            _rec_exercise("Chest Press", "chest", 100),
            _rec_exercise("Leg Extension", "quads", 80),
        ],
    )
    intent = {
        "avoid_muscles": ["chest"],
        "avoid_joints": [],
        "swap": [
            {
                "replace_exercise": "Chest Press",
                "replace_index": 0,
                "target_muscle": "triceps",
                "target_exercise": "Overhead Tricep Extension",
                "reason": "stale deterministic index",
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
        "machines_and_cables",
    )

    names = [ex["exercise"] for ex in patched["exercises"]]
    assert names == ["Leg Extension"]
    assert any("Removed: Chest Press" in note for note in notes)
    assert any("could not locate 'Chest Press'" in note for note in notes)


def test_adjust_deterministic_fallback_leaves_unclassified_request_unchanged(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(module, "_lm_studio", None)
    recommendation = _recommendation_for(module, [_rec_exercise("Chest Press", "chest", 100)])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = module.app.test_client().post(
        "/api/workout/adjust",
        json={"constraint": "replace with dragon press"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "fallback"
    assert payload["recommendation"]["exercises"][0]["exercise"] == "Chest Press"


def test_adjust_deterministic_rejects_generic_press_false_positive(monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setattr(module, "_lm_studio", None)
    recommendation = _recommendation_for(
        module,
        [
            _rec_exercise("Incline Press", "chest", 95),
            _rec_exercise("Seated Row", "back", 90),
        ],
    )
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", recommendation)

    response = module.app.test_client().post(
        "/api/workout/adjust",
        json={"constraint": "replace with dragon press"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "fallback"
    assert [ex["exercise"] for ex in payload["recommendation"]["exercises"]] == [
        "Incline Press",
        "Seated Row",
    ]


def test_adjust_direct_history_wins_over_similar_pattern_inference(monkeypatch):
    module = _module(monkeypatch)
    module.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    monkeypatch.setattr(module, "_lm_studio", None)
    monkeypatch.setattr(
        module,
        "WORKOUTS",
        [
            _workout("2026-05-01", "Cable Pushdown", 60, muscle="triceps"),
            _workout("2026-05-08", "Overhead Tricep Extension", 40, muscle="triceps"),
            _workout("2026-05-15", "Overhead Tricep Extension", 45, muscle="triceps"),
        ],
    )
    monkeypatch.setattr(
        module,
        "LAST_WORKOUT_RECOMMENDATION",
        _recommendation_for(module, [_rec_exercise("Cable Pushdown", "triceps", 55)]),
    )

    response = module.app.test_client().post(
        "/api/workout/adjust",
        json={"constraint": "replace with triceps extension"},
    )

    assert response.status_code == 200
    exercise = response.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Overhead Tricep Extension"
    assert exercise["load_source"] == "progression"
    assert "load_inference" not in exercise


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

    assert module._ADJUST_CACHE_VERSION == "fit179-movement-resolution-v1"


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
