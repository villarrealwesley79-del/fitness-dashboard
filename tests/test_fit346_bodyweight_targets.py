from __future__ import annotations

import importlib

import pytest

from js_runtime import run_app_js


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
    result = run_app_js(
        ["exerciseTargetText"],
        """
process.stdout.write(JSON.stringify({
  bodyweight: e.exerciseTargetText({ target_sets: 3, target_reps: 10, target_weight: 0, bodyweight: true, rpe_target: 7 }),
  weighted: e.exerciseTargetText({ target_sets: 3, target_reps: 10, target_weight: 85, rpe_target: 7 }),
}));
""",
    )

    assert result == {
        "bodyweight": "3 × 10 · BW · RPE 7",
        "weighted": "3 × 10 · 85 lb · RPE 7",
    }


def test_actual_active_and_preview_renderers_emit_bodyweight_and_weighted_targets():
    result = run_app_js(
        ["renderActiveWorkout", "renderAdjustedPlanPreview", "state"],
        """
function fakeNode() {
  return { className: '', innerHTML: '', textContent: '', hidden: false, children: [], handlers: {},
    appendChild(child) { this.children.push(child); },
    querySelector(selector) { return { addEventListener: (name, fn) => { this.handlers[selector] = fn; } }; },
    querySelectorAll() { return []; },
    addEventListener(name, fn) { this.handlers[name] = fn; },
  };
}
sandbox.elements['active-workout-title'] = fakeNode();
sandbox.elements['active-workout-body'] = fakeNode();
sandbox.elements['active-workout-status'] = fakeNode();
sandbox.elements['modal-active'] = Object.assign(fakeNode(), { querySelector: () => null, removeEventListener() {} });
sandbox.elements['adjust-plan-preview'] = fakeNode();
sandbox.document.createElement = () => fakeNode();
e.state.activeWorkout = {
  focus: 'strength',
  exercises: [
    { exercise: 'Dips', bodyweight: true, target_sets: 3, target_reps: 10, logged_sets: [{ weight: '', reps: '', done: false, notes: '' }] },
    { exercise: 'Bench Press', target_weight: 85, target_sets: 3, target_reps: 8, logged_sets: [{ weight: '', reps: '', done: false, notes: '' }] },
  ],
};
e.renderActiveWorkout();
const activeHtml = e.state.activeWorkout.exercises.map((_, index) => sandbox.elements['active-workout-body'].children[index + 1].innerHTML);
e.renderAdjustedPlanPreview(e.state.activeWorkout);
process.stdout.write(JSON.stringify({ activeHtml, previewHtml: sandbox.elements['adjust-plan-preview'].innerHTML }));
""",
    )
    assert any("BW" in html for html in result["activeHtml"])
    assert any("85 lb" in html for html in result["activeHtml"])
    assert "BW" in result["previewHtml"]
    assert "85 lb" in result["previewHtml"]
