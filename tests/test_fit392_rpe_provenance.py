from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text()
STYLE_CSS = (ROOT / "static" / "css" / "style.css").read_text()


@pytest.fixture()
def fitness_app(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "COMPLETED_WORKOUTS", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_notify_workout_logged", lambda *_args, **_kwargs: None)
    return module


def _payload(sets):
    return {
        "client_workout_id": "fit392-rpe-proof",
        "date": "2026-07-16",
        "session_type": "push",
        "exercises": [
            {
                "machine": "Chest Press",
                "muscle_group": "chest",
                "sets": sets,
            }
        ],
    }


def test_complete_workout_persists_mixed_observed_and_defaulted_rpe(fitness_app):
    response = fitness_app.app.test_client().post(
        "/api/complete-workout",
        json=_payload([
            {"weight_lbs": 100, "reps": 10, "rpe": 8, "rpe_observed": True},
            {"weight_lbs": 100, "reps": 10, "rpe": 7, "rpe_observed": False},
        ]),
    )

    assert response.status_code == 200
    stored_sets = fitness_app.WORKOUTS[-1]["exercises"][0]["sets"]
    assert [(row["rpe"], row["rpe_observed"]) for row in stored_sets] == [
        (8, True),
        (7, False),
    ]


@pytest.mark.parametrize("rpe", [None, "", "hard", 0, 11, True, 10 ** 400])
def test_complete_workout_invalid_rpe_never_blocks_and_cannot_be_observed(fitness_app, rpe):
    response = fitness_app.app.test_client().post(
        "/api/complete-workout",
        json=_payload([{"weight_lbs": 100, "reps": 10, "rpe": rpe, "rpe_observed": True}]),
    )

    assert response.status_code == 200
    stored = fitness_app.WORKOUTS[-1]["exercises"][0]["sets"][0]
    assert stored["rpe"] is None
    assert stored["rpe_observed"] is False


def test_complete_workout_missing_rpe_fields_are_defaulted_and_do_not_block(fitness_app):
    client = fitness_app.app.test_client()
    response = client.post(
        "/api/complete-workout",
        json=_payload([{"weight_lbs": 100, "reps": 10}]),
    )

    assert response.status_code == 200
    stored = fitness_app.WORKOUTS[-1]["exercises"][0]["sets"][0]
    assert stored["rpe"] is None
    assert stored["rpe_observed"] is False
    assert client.get("/api/dashboard").status_code == 200


def test_complete_workout_preserves_fractional_prescribed_rpe(fitness_app):
    response = fitness_app.app.test_client().post(
        "/api/complete-workout",
        json=_payload([{"weight_lbs": 100, "reps": 10, "rpe": 7.5, "rpe_observed": False}]),
    )

    assert response.status_code == 200
    stored = fitness_app.WORKOUTS[-1]["exercises"][0]["sets"][0]
    assert stored["rpe"] == 7.5
    assert stored["rpe_observed"] is False


def test_complete_workout_non_boolean_observed_flag_defaults_false(fitness_app):
    response = fitness_app.app.test_client().post(
        "/api/complete-workout",
        json=_payload([{"weight_lbs": 100, "reps": 10, "rpe": 8, "rpe_observed": "true"}]),
    )

    assert response.status_code == 200
    stored = fitness_app.WORKOUTS[-1]["exercises"][0]["sets"][0]
    assert stored == {
        "set_number": 1,
        "weight_lbs": 100,
        "reps": 10,
        "rpe": 8,
        "rpe_observed": False,
    }


@pytest.mark.parametrize("endpoint", ["/api/history", "/api/history-all"])
def test_history_reads_legacy_rpe_as_defaulted_without_mutating_storage(fitness_app, endpoint):
    legacy_set = {"set_number": 1, "weight_lbs": 100, "reps": 10, "rpe": 7}
    fitness_app.WORKOUTS.append({
        "id": "legacy-rpe",
        "date": "2026-07-15",
        "session_type": "push",
        "exercises": [{"machine": "Chest Press", "sets": [legacy_set]}],
    })

    response = fitness_app.app.test_client().get(endpoint)

    assert response.status_code == 200
    returned = response.get_json()["workouts"][0]["exercises"][0]["sets"][0]
    assert returned["rpe"] == 7
    assert returned["rpe_observed"] is False
    assert "rpe_observed" not in legacy_set


def test_dashboard_ignores_overflowing_legacy_rpe(fitness_app):
    fitness_app.WORKOUTS.append({
        "date": "2026-07-15",
        "exercises": [{
            "machine": "Chest Press",
            "sets": [{"weight_lbs": 100, "reps": 10, "rpe": 10 ** 400}],
        }],
    })

    assert fitness_app.app.test_client().get("/api/dashboard").status_code == 200


def _app_js_block(start: str, end: str) -> str:
    start_idx = APP_JS.index(start)
    end_idx = APP_JS.index(end, start_idx)
    return APP_JS[start_idx:end_idx]


def test_active_workout_rpe_helpers_enforce_provenance_invariant():
    if not shutil.which("node"):
        pytest.skip("FIT-392 frontend regression requires node")

    helper_source = _app_js_block("function prescribedRpeValue", "function buildLoggedSets")
    script = f"""
const vm = require('node:vm');
const sandbox = {{}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(helper_source)}, sandbox);
const result = vm.runInContext(`({{
  target: prescribedRpeValue({{ rpe_target: 7.5 }}),
  fallback: prescribedRpeValue({{}}),
  observed: normalizeActiveSetRpe('8', true),
  cleared: normalizeActiveSetRpe('', true),
  fractional: normalizeActiveSetRpe('7.5', true),
  passiveFocus: rpeInteractionObserves({{ type: 'focus', isTrusted: true }}),
  programmaticClick: rpeInteractionObserves({{ type: 'click', isTrusted: false }}),
  explicitConfirm: rpeInteractionObserves({{ type: 'click', isTrusted: true }}),
  trustedEdit: rpeInteractionObserves({{ type: 'input', isTrusted: true }}),
}})`, sandbox);
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    assert json.loads(result.stdout) == {
        "target": 7.5,
        "fallback": 7,
        "observed": {"rpe": 8, "rpe_observed": True},
        "cleared": {"rpe": None, "rpe_observed": False},
        "fractional": {"rpe": 7.5, "rpe_observed": True},
        "passiveFocus": False,
        "programmaticClick": False,
        "explicitConfirm": True,
        "trustedEdit": True,
    }


def test_guided_workout_uses_per_set_rpe_without_touching_quick_log():
    render_block = _app_js_block("function renderActiveWorkout()", "function activeWorkoutHasProgress")
    complete_block = _app_js_block("async function completeWorkout()", "function openWorkoutSavedConfirm")

    assert 'data-field="rpe"' in render_block
    assert 'aria-label="RPE for set ${sidx + 1}; tap to confirm prescribed value"' in render_block
    assert "rpeInteractionObserves(event)" in render_block
    assert "rpe_observed: rpe.rpe_observed" in complete_block
    assert "rpe: ex.rpe ? Number(ex.rpe) : null" not in complete_block
    assert "qsa('#rpe-row button')" in APP_JS


def test_rpe_survives_draft_and_adjusted_workout_paths():
    draft_block = _app_js_block("function syncActiveWorkoutInputsFromDom", "function saveActiveWorkoutDraft")
    build_block = _app_js_block("function buildLoggedSets", "function buildActiveExercise")
    adjust_block = _app_js_block("function buildAdjustedLoggedSets", "const EXERCISE_IDENTITY_ALIAS_GROUPS")

    assert 'input[data-field="rpe"]' in draft_block
    assert "Boolean(set.rpe_observed)" in draft_block
    assert "prescribedRpeValue(ex)" in build_block
    assert "previousRpe.rpe_observed" in build_block
    assert "rpe_observed" in adjust_block
    assert "prescribedRpeValue(newEx)" in adjust_block


def test_rpe_defaults_and_adjustment_reconciliation_run_in_javascript():
    if not shutil.which("node"):
        pytest.skip("FIT-392 frontend regression requires node")

    helper_source = _app_js_block("function prescribedRpeValue", "function buildActiveExercise")
    adjust_source = _app_js_block("function buildAdjustedLoggedSets", "const EXERCISE_IDENTITY_ALIAS_GROUPS")
    script = f"""
const vm = require('node:vm');
const sandbox = {{
  recommendedRepsValue: (ex) => String(ex.target_reps || 10),
  recommendedWeightValue: (ex) => String(ex.target_weight || 100),
  setCountForExercise: (ex) => Number(ex.target_sets || 1),
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(helper_source + adjust_source)}, sandbox);
const result = vm.runInContext(`(() => {{
  const fresh = buildLoggedSets({{ target_sets: 1, rpe_target: 8 }}, null)[0];
  const staleDefault = buildLoggedSets(
    {{ target_sets: 1, rpe_target: 7.5 }},
    [{{ reps: '10', weight: '100', done: false, notes: '', rpe: 7, rpe_observed: false }}]
  )[0];
  const cleared = buildLoggedSets(
    {{ target_sets: 1, rpe_target: 7.5 }},
    [{{ reps: '10', weight: '100', done: false, notes: '', rpe: null, rpe_observed: false }}]
  )[0];
  const adjusted = buildAdjustedLoggedSets(
    {{ target_sets: 3, target_reps: 8, target_weight: 80, rpe_target: 9 }},
    [
      {{ reps: '10', weight: '100', done: false, notes: '', rpe: 7, rpe_observed: false }},
      {{ reps: '10', weight: '100', done: false, notes: '', rpe: 8, rpe_observed: true }},
      {{ reps: '10', weight: '100', done: false, notes: '', rpe: null, rpe_observed: false }},
    ],
    {{ target_reps: 10, target_weight: 100, rpe_target: 7 }}
  );
  return {{ fresh, staleDefault, cleared, adjusted }};
}})()`, sandbox);
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    output = json.loads(result.stdout)

    assert output["fresh"]["rpe"] == 8
    assert output["fresh"]["rpe_observed"] is False
    assert output["staleDefault"]["rpe"] == 7.5
    assert output["staleDefault"]["rpe_observed"] is False
    assert output["cleared"]["rpe"] is None
    assert output["cleared"]["rpe_observed"] is False
    assert output["adjusted"][0]["rpe"] == 9
    assert output["adjusted"][0]["rpe_observed"] is False
    assert output["adjusted"][1]["rpe"] == 8
    assert output["adjusted"][1]["rpe_observed"] is True
    assert output["adjusted"][2]["rpe"] is None
    assert output["adjusted"][2]["rpe_observed"] is False


def test_set_rpe_control_has_mobile_safe_layout_and_new_asset_version():
    html = (ROOT / "templates" / "index.html").read_text()
    loader = (ROOT / "static" / "js" / "app-loader.js").read_text()
    service_worker = (ROOT / "static" / "js" / "sw.js").read_text()

    assert ".set-rpe-cell" in STYLE_CSS
    assert "min-height: 44px" in STYLE_CSS
    assert "@media (max-width: 420px)" in STYLE_CSS
    assert "/static/css/style.css?v=20260716-fit392-rpe" in html
    assert "/static/js/app-loader.js?v=20260716-fit392-rpe" in html
    assert "/static/js/app.js?v=20260716-fit392-rpe" in loader
    assert "fitness-dashboard-v20260716-fit392-rpe" in service_worker
