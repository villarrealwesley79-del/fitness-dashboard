from __future__ import annotations

import importlib

import pytest


GOAL_PARAMS = {
    "name": "Hypertrophy",
    "rep_range": (8, 12),
    "sets_per_exercise": 3,
    "intensity_pct": 80,
    "rest_minutes": 2,
}


def _entry(module, exercise_name, *, progression=None):
    exercise = next(item for item in module.EXERCISE_LIBRARY if item["name"] == exercise_name)
    return module._build_exercise_entry(
        exercise_name,
        exercise["muscle"],
        exercise["compound"],
        GOAL_PARAMS,
        1,
        1,
        None,
        {},
        [],
        progression or {},
        [],
        3,
    )


@pytest.fixture
def module(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(module, "BASELINES_DATA", {})
    return module


@pytest.mark.parametrize("exercise_name", ["Dips", "Pullups", "Hanging Leg Raise", "Plank"])
def test_fresh_bodyweight_exercises_emit_bodyweight_targets(module, exercise_name):
    entry = _entry(module, exercise_name)

    assert entry["load_source"] == "hardcoded"
    assert entry["target_weight"] == 0
    assert entry["bodyweight"] is True


def test_weighted_pullup_history_keeps_numeric_target(module):
    entry = _entry(
        module,
        "Pullups",
        progression={"Pullups": {"current_e1rm": 100, "status": "On Track"}},
    )

    assert entry["load_source"] == "progression"
    assert entry["target_weight"] == 85
    assert "bodyweight" not in entry


def test_non_bodyweight_target_still_uses_minimum_load_clamp(module, monkeypatch):
    low_load_cable = dict(module.EXERCISE_LOOKUP["Lateral Raise"])
    low_load_cable["baseline"] = 1
    monkeypatch.setattr(module, "EXERCISE_LOOKUP", {"Lateral Raise": low_load_cable})

    entry = _entry(module, "Lateral Raise")

    assert entry["target_weight"] == 5
    assert "bodyweight" not in entry
