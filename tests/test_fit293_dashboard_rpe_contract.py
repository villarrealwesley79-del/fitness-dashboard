import importlib


def test_workout_contract_exposes_exercise_rpe_range():
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)

    workout = {
        "goal": "hypertrophy",
        "exercises": [
            {"exercise": "Chest Press", "rpe_target": 7},
            {"exercise": "Lateral Raise", "rpe_target": 8},
            {"exercise": "Cable Fly", "rpe_target": None},
        ],
    }

    scoped = module._workout_with_auth_scope(workout)

    assert scoped["rpe_range"] == {"min": 7, "max": 8, "label": "7–8"}
    assert "rpe_range" not in workout


def test_workout_contract_omits_rpe_range_without_exercise_targets():
    module = importlib.import_module("app")

    scoped = module._workout_with_auth_scope(
        {"goal": "hypertrophy", "exercises": [{"exercise": "Chest Press"}]}
    )

    assert "rpe_range" not in scoped
