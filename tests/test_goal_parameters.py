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
            "available_time_minutes": 120,
            "equipment_preference": "machines_only",
        }
    )
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    return module


def test_reconciled_resistance_goal_parameters(fitness_app):
    params = fitness_app.GOAL_PARAMETERS

    assert params[fitness_app.TrainingGoal.WEIGHT_LOSS.value]["intensity_pct"] == 70
    assert params[fitness_app.TrainingGoal.WEIGHT_LOSS.value]["rep_range"] == (8, 12)
    assert params[fitness_app.TrainingGoal.WEIGHT_LOSS.value]["rest_minutes"] == "1.5-3"

    assert params[fitness_app.TrainingGoal.TONING.value]["intensity_pct"] == 65
    assert params[fitness_app.TrainingGoal.TONING.value]["rep_range"] == (8, 15)
    assert params[fitness_app.TrainingGoal.TONING.value]["rest_minutes"] == "1-2"

    assert params[fitness_app.TrainingGoal.HYBRID_WEIGHT_LOSS_TONING.value]["intensity_pct"] == 65
    assert params[fitness_app.TrainingGoal.HYBRID_WEIGHT_LOSS_TONING.value]["rep_range"] == (8, 15)
    assert params[fitness_app.TrainingGoal.HYBRID_WEIGHT_LOSS_TONING.value]["rest_minutes"] == "1-2"

    assert params[fitness_app.TrainingGoal.HYBRID_HYPERTROPHY_ENDURANCE.value]["intensity_pct"] == 65


def test_unchanged_goal_parameters_stay_in_scope(fitness_app):
    params = fitness_app.GOAL_PARAMETERS

    assert params[fitness_app.TrainingGoal.STRENGTH.value]["rep_range"] == (1, 5)
    assert params[fitness_app.TrainingGoal.STRENGTH.value]["intensity_pct"] == 85
    assert params[fitness_app.TrainingGoal.HYPERTROPHY.value]["rep_range"] == (8, 12)
    assert params[fitness_app.TrainingGoal.HYPERTROPHY.value]["intensity_pct"] == 70
    assert params[fitness_app.TrainingGoal.ENDURANCE.value]["rep_range"] == (15, 25)
    assert params[fitness_app.TrainingGoal.ENDURANCE.value]["intensity_pct"] == 50
    assert params[fitness_app.TrainingGoal.HYBRID_STRENGTH_HYPERTROPHY.value]["intensity_pct"] == 78


def test_reconciled_rest_ranges_are_reflected_in_time_budget(fitness_app):
    params = fitness_app.GOAL_PARAMETERS

    assert params[fitness_app.TrainingGoal.WEIGHT_LOSS.value]["time_per_set_minutes"] >= 3
    assert params[fitness_app.TrainingGoal.TONING.value]["time_per_set_minutes"] >= 2
    assert params[fitness_app.TrainingGoal.HYBRID_WEIGHT_LOSS_TONING.value]["time_per_set_minutes"] >= 2


def test_reconciled_rest_labels_use_goal_rest_range(fitness_app):
    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.TONING.value,
        available_time=120,
    )

    assert recommendation["exercises"]
    assert {exercise["rest_label"] for exercise in recommendation["exercises"]} == {"1-2 min"}


def test_weight_loss_plan_reserves_recovery_cardio_with_longer_rest_budget(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: 60)

    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.WEIGHT_LOSS.value,
        available_time=120,
        training_recommendation="recovery",
    )

    assert recommendation["estimated_minutes"] <= 120
    assert recommendation["cardio"]["zone"] == "Zone 2"
    assert recommendation["cardio"]["duration_minutes"] >= 20


@pytest.mark.parametrize(
    "goal",
    [
        "weight_loss",
        "toning",
        "weight_loss_toning",
        "hypertrophy_endurance",
    ],
)
def test_reconciled_goals_still_generate_recommendations_in_rep_range(fitness_app, goal):
    recommendation = fitness_app.generate_next_workout([], [], goal=goal, available_time=120)
    min_reps, max_reps = fitness_app.GOAL_PARAMETERS[goal]["rep_range"]

    assert recommendation["exercises"]
    for exercise in recommendation["exercises"]:
        assert min_reps <= exercise["target_reps"] <= max_reps
