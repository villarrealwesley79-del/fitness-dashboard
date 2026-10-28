"""Reviewer tests for FIT-293 (PR #283): target RPE range contract and card copy."""

import copy
import json

import pytest

from js_runtime import run_app_js


@pytest.fixture
def scope(isolated_app):
    return isolated_app._workout_with_auth_scope


def test_mixed_targets_give_min_max_range_and_do_not_mutate(scope):
    plan = {"goal": "hypertrophy", "exercises": [
        {"exercise": "A", "rpe_target": 8.5},
        {"exercise": "B", "rpe_target": 6.5},
        {"exercise": "C", "rpe_target": "7"},
    ]}
    before = copy.deepcopy(plan)
    out = scope(plan)
    assert out["rpe_range"] == {"min": 6.5, "max": 8.5, "label": "6.5–8.5"}
    assert plan == before


def test_uniform_targets_show_single_value(scope):
    out = scope({"exercises": [{"rpe_target": 7}, {"rpe_target": 7.0}]})
    assert out["rpe_range"] == {"min": 7, "max": 7, "label": "7"}


@pytest.mark.parametrize("bad", [None, "", "hard", 0, 11, -1, float("nan"), float("inf")])
def test_invalid_targets_invent_no_range(scope, bad):
    out = scope({"exercises": [{"rpe_target": bad}]})
    assert "rpe_range" not in out


def test_non_dict_exercise_entries_do_not_crash_serialization(scope):
    # A malformed/legacy persisted plan must not 500 every dashboard endpoint.
    out = scope({"exercises": ["Chest Press", None, {"rpe_target": 8}]})
    assert out["rpe_range"]["label"] == "8"


def test_missing_exercises_key_is_fine(scope):
    assert "rpe_range" not in scope({"goal": "x"})
    assert "rpe_range" not in scope({"exercises": None})


def _paint(next_workout, reco=None):
    scenario = f"""
const mk = () => ({{ textContent: '—', hidden: true, innerHTML: '', classList: {{ toggle() {{}}, add() {{}}, remove() {{}} }}, setAttribute() {{}}, style: {{}} }});
['reco-intensity', 'reco-rpe', 'reco-time', 'reco-title'].forEach((id) => {{ sandbox.elements[id] = mk(); }});
e.state.dashboard = {{ next_workout: {json.dumps(next_workout)} }};
e.state.reco = {json.dumps(reco)};
let error = null;
try {{ e.paintDashboardFromState(); }} catch (err) {{ error = String(err && err.message || err); }}
process.stdout.write(JSON.stringify({{
  intensity: sandbox.elements['reco-intensity'].textContent,
  rpeHidden: sandbox.elements['reco-rpe'].hidden,
  rpeText: sandbox.elements['reco-rpe'].textContent,
  error,
}}));
"""
    return run_app_js(["paintDashboardFromState", "state"], scenario)


def test_card_labels_range_as_target_intensity():
    out = _paint({"focus": "upper", "rpe_range": {"min": 7, "max": 8, "label": "7–8"}},
                 {"recommendation": "intensity"})
    assert out["intensity"] == "Target intensity · RPE 7–8", out
    assert out["rpeHidden"] is True


def test_card_without_range_falls_back_and_invents_no_rpe():
    out = _paint({"focus": "upper"}, {"recommendation": "recovery"})
    assert "RPE" not in out["intensity"], out
    assert out["intensity"] == "upper · Low", out
    assert out["rpeHidden"] is True


def test_card_shows_readiness_word_and_rpe_range_together():
    scenario = """
const mk = () => ({ textContent: '', hidden: true, classList: { toggle() {}, add() {}, remove() {} }, setAttribute() {}, style: {} });
['reco-intensity', 'reco-readiness', 'reco-rpe', 'reco-time', 'reco-title'].forEach((id) => { sandbox.elements[id] = mk(); });
e.state.dashboard = { next_workout: { focus: 'upper', rpe_range: { min: 7, max: 8, label: '7–8' } } };
e.state.reco = { recommendation: 'intensity' };
e.paintDashboardFromState();
process.stdout.write(JSON.stringify({
  intensity: sandbox.elements['reco-intensity'].textContent,
  readiness: sandbox.elements['reco-readiness'].textContent,
  readinessHidden: sandbox.elements['reco-readiness'].hidden,
}));
"""
    out = run_app_js(["paintDashboardFromState", "state"], scenario)
    assert out == {"intensity": "Target intensity · RPE 7–8", "readiness": "upper · High", "readinessHidden": False}, out


def test_non_dict_and_boolean_targets_are_ignored(scope):
    out = scope({"exercises": ["x", True, False, {"rpe_target": True}, {"rpe_target": 9}]})
    assert out["rpe_range"]["label"] == "9"
    assert "rpe_range" not in scope({"exercises": [{"rpe_target": True}]})
