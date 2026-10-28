from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from js_runtime import run_app_js


ROOT = Path(__file__).resolve().parents[1]
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


def test_active_workout_rpe_helpers_enforce_provenance_invariant():
    output = run_app_js(
        ["prescribedRpeValue", "normalizeActiveSetRpe", "rpeInteractionObserves"],
        """
process.stdout.write(JSON.stringify({
  target: e.prescribedRpeValue({ rpe_target: 7.5 }),
  fallback: e.prescribedRpeValue({}),
  observed: e.normalizeActiveSetRpe('8', true),
  cleared: e.normalizeActiveSetRpe('', true),
  fractional: e.normalizeActiveSetRpe('7.5', true),
  passiveFocus: e.rpeInteractionObserves({ type: 'focus', isTrusted: true }),
  programmaticClick: e.rpeInteractionObserves({ type: 'click', isTrusted: false }),
  explicitConfirm: e.rpeInteractionObserves({ type: 'click', isTrusted: true }),
  trustedEdit: e.rpeInteractionObserves({ type: 'input', isTrusted: true }),
}));
""",
    )

    assert output == {
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


def test_guided_workout_renders_a_per_set_rpe_input_prefilled_with_the_target():
    output = run_app_js(
        ["renderActiveWorkout", "state"],
        """
const made = [];
sandbox.document.createElement = () => {
  const el = { className: '', id: '', textContent: '', innerHTML: '', dataset: {}, appendChild() {}, classList: { toggle() {} } };
  made.push(el);
  return el;
};
sandbox.elements['active-workout-title'] = { textContent: '' };
sandbox.elements['active-workout-body'] = { innerHTML: '', appendChild() {} };
sandbox.__fitSet.countActiveWorkoutProgress(() => ({ nextIncomplete: null, doneSets: 0, totalSets: 2 }));
sandbox.__fitSet.formatActiveWorkoutProgress(() => 'progress');
e.state.activeWorkout = {
  focus: 'push',
  exercises: [{
    exercise: 'Chest Press', muscle: 'chest', target_sets: 2, target_reps: 10, rpe_target: 8,
    logged_sets: [
      { reps: '10', weight: '100', done: false, notes: '' },
      { reps: '10', weight: '100', done: true, notes: '', rpe: 9, rpe_observed: true },
    ],
  }],
  cardio: null,
};
try { e.renderActiveWorkout(); } catch (err) { /* rows are built before wiring DOM listeners */ }
process.stdout.write(JSON.stringify({
  html: made.map((el) => el.innerHTML).join(''),
  sets: e.state.activeWorkout.exercises[0].logged_sets.map((s) => [s.rpe, s.rpe_observed]),
}));
""",
        mocks=["countActiveWorkoutProgress", "formatActiveWorkoutProgress"],
    )

    assert output["html"].count('data-field="rpe"') == 2
    assert "tap to confirm prescribed value" in output["html"]
    assert output["sets"] == [[8, False], [9, True]]


def test_complete_workout_sends_per_set_rpe_with_observed_flag():
    output = run_app_js(
        ["completeWorkout", "state"],
        """
const field = (value) => ({ value });
const rowFor = (set, rpe, reps = '10') => ({
  dataset: { set: String(set) },
  querySelector: (s) => ({
    'input[data-field="reps"]': field(reps),
    'input[data-field="weight"]': field('100'),
    'input[data-field="done"]': { checked: true },
    'input[data-field="notes"]': field(''),
    'input[data-field="rpe"]': field(rpe),
  })[s],
});
const rows = [rowFor(0, '8.5'), rowFor(1, '7'), rowFor(2, '')];
sandbox.elements['active-workout-body'] = { querySelectorAll: () => rows };
sandbox.elements['btn-complete-workout'] = { disabled: false, textContent: '' };
sandbox.elements['modal-active'] = { hidden: false };
e.state.activeWorkout = {
  id: 'w1', focus: 'push', recommendation_id: 'rec-1',
  exercises: [{
    exercise: 'Chest Press', muscle_group: 'chest', rpe_target: 7,
    logged_sets: [
      { rpe: 8.5, rpe_observed: true },
      { rpe: 7, rpe_observed: false },
      { rpe: null, rpe_observed: false },
    ],
  }],
  cardio: null,
};
sandbox.navigator.onLine = false;
let captured = null;
sandbox.__fitSet.setActiveWorkoutStatus(() => {});
sandbox.__fitSet.saveActiveWorkoutDraft(() => {});
sandbox.__fitSet.enqueueOfflineWorkout((payload) => { captured = payload; });
sandbox.__fitSet.clearActiveWorkoutDraft(() => {});
sandbox.__fitSet.clearAdjustIntent(() => {});
sandbox.__fitSet.invalidateCaches(() => {});
sandbox.__fitSet.loadTab(() => {});
sandbox.__fitSet.openWorkoutSavedConfirm(() => {});
sandbox.__fitSet.toast(() => {});
await e.completeWorkout();
process.stdout.write(JSON.stringify(captured.exercises[0].sets));
""",
        mocks=[
            "setActiveWorkoutStatus", "saveActiveWorkoutDraft", "enqueueOfflineWorkout",
            "clearActiveWorkoutDraft", "clearAdjustIntent", "invalidateCaches", "loadTab",
            "openWorkoutSavedConfirm", "toast",
        ],
    )

    assert [(row["rpe"], row["rpe_observed"]) for row in output] == [
        (8.5, True),
        (7, False),
        (None, False),
    ]


def test_rpe_defaults_and_adjustment_reconciliation_run_in_javascript():
    output = run_app_js(
        ["buildLoggedSets", "buildAdjustedLoggedSets"],
        """
const fresh = e.buildLoggedSets({ target_sets: 1, rpe_target: 8 }, null)[0];
const staleDefault = e.buildLoggedSets(
  { target_sets: 1, rpe_target: 7.5 },
  [{ reps: '10', weight: '100', done: false, notes: '', rpe: 7, rpe_observed: false }]
)[0];
const cleared = e.buildLoggedSets(
  { target_sets: 1, rpe_target: 7.5 },
  [{ reps: '10', weight: '100', done: false, notes: '', rpe: null, rpe_observed: false }]
)[0];
const adjusted = e.buildAdjustedLoggedSets(
  { target_sets: 3, target_reps: 8, target_weight: 80, rpe_target: 9 },
  [
    { reps: '10', weight: '100', done: false, notes: '', rpe: 7, rpe_observed: false },
    { reps: '10', weight: '100', done: false, notes: '', rpe: 8, rpe_observed: true },
    { reps: '10', weight: '100', done: false, notes: '', rpe: null, rpe_observed: false },
  ],
  { target_reps: 10, target_weight: 100, rpe_target: 7 }
);
process.stdout.write(JSON.stringify({ fresh, staleDefault, cleared, adjusted }));
""",
    )

    assert (output["fresh"]["rpe"], output["fresh"]["rpe_observed"]) == (8, False)
    assert (output["staleDefault"]["rpe"], output["staleDefault"]["rpe_observed"]) == (7.5, False)
    assert (output["cleared"]["rpe"], output["cleared"]["rpe_observed"]) == (None, False)
    assert [(row["rpe"], row["rpe_observed"]) for row in output["adjusted"]] == [
        (9, False),
        (8, True),
        (None, False),
    ]


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
