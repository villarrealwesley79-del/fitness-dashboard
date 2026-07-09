from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS = (Path(__file__).resolve().parents[1] / "static" / "js" / "app.js").read_text()


GOAL_PARAMS = {
    "name": "Hypertrophy",
    "rep_range": (8, 12),
    "sets_per_exercise": 3,
    "intensity_pct": 80,
    "rest_minutes": 2,
}


def _entry(module, exercise_name, *, progression=None, workouts=None):
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
        workouts or [],
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
    assert entry["rationale"] == "Hypertrophy: +5 lbs progression · Baseline: no recent history"
    assert "bodyweight" not in entry


@pytest.mark.parametrize("exercise_name", ["Dips", "Pullups"])
def test_zero_weight_bodyweight_history_keeps_bodyweight_guidance(module, exercise_name):
    entry = _entry(
        module,
        exercise_name,
        workouts=[
            {
                "date": "2026-07-08",
                "exercises": [
                    {
                        "machine": exercise_name,
                        "sets": [{"weight_lbs": 0, "reps": 10, "rpe": 6}],
                    }
                ],
            }
        ],
    )

    assert entry["target_weight"] == 0
    assert entry["bodyweight"] is True
    assert entry["rationale"] == "Hypertrophy: Bodyweight — prioritize controlled reps, form, and effort"


def test_non_bodyweight_target_still_uses_minimum_load_clamp(module, monkeypatch):
    low_load_cable = dict(module.EXERCISE_LOOKUP["Lateral Raise"])
    low_load_cable["baseline"] = 1
    monkeypatch.setattr(module, "EXERCISE_LOOKUP", {"Lateral Raise": low_load_cable})

    entry = _entry(module, "Lateral Raise")

    assert entry["target_weight"] == 5
    assert entry["rationale"] == "Hypertrophy: Starting weight — log your first session to calibrate · Baseline: no recent history"
    assert "bodyweight" not in entry


def test_bodyweight_target_display_uses_bw_for_active_and_preview_cards():
    if not shutil.which("node"):
        pytest.skip("FIT-346 display regression requires node to execute app.js")

    active_workout_block = APP_JS.split("function renderActiveWorkout()", 1)[1]
    assert "const target = exerciseTargetText(ex);" in active_workout_block

    helper_source = APP_JS.split("function exerciseTargetText(ex)", 1)[1].split("// FIT-105", 1)[0]
    node_script = f"""
const vm = require('node:vm');
const helperSource = {json.dumps(helper_source)};
const sandbox = {{ module: {{ exports: {{}} }} }};
vm.runInNewContext(`
function exerciseTargetText(ex) {{${{helperSource}}}}
module.exports = {{ exerciseTargetText }};
`, sandbox);
process.stdout.write(JSON.stringify({{
  bodyweight: sandbox.module.exports.exerciseTargetText({{ target_sets: 3, target_reps: 10, target_weight: 0, bodyweight: true, rpe_target: 7 }}),
  weighted: sandbox.module.exports.exerciseTargetText({{ target_sets: 3, target_reps: 10, target_weight: 85, rpe_target: 7 }}),
}}));
"""
    result = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "bodyweight": "3 × 10 · BW · RPE 7",
        "weighted": "3 × 10 · 85 lb · RPE 7",
    }
