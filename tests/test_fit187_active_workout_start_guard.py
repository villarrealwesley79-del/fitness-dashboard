from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text()


def test_restored_adjust_preview_does_not_mutate_active_workout():
    body = APP_JS.split("function renderAdjustResult(payload, opts = {})", 1)[1].split(
        "async function submitAdjust()",
        1,
    )[0]

    assert "renderAdjustResult(saved, { restored: true" in APP_JS
    assert "state.activeWorkout && kind === 'changed' && !opts.restored" in body


def test_start_workout_confirms_before_discarding_logged_active_sets():
    if not shutil.which("node"):
        pytest.skip("FIT-187 runtime regression requires node to execute app.js")

    outputs = _run_start_guard_fixture()

    cancel = outputs["cancelStart"]
    assert cancel["confirmCalls"] == [
        "You have an in-progress workout. Discard logged sets and restart?"
    ]
    assert cancel["activeName"] == "Chest Press"
    assert cancel["keptNotes"] == "keep me"
    assert cancel["renderCount"] == 0

    confirm = outputs["confirmStart"]
    assert confirm["confirmCalls"] == [
        "You have an in-progress workout. Discard logged sets and restart?"
    ]
    assert confirm["activeName"] == "Incline Press"
    assert confirm["dirty"] is False
    assert confirm["oldNotesPresent"] is False
    assert confirm["renderCount"] == 1

    no_progress = outputs["noProgressStart"]
    assert no_progress["confirmCalls"] == []
    assert no_progress["activeName"] == "Incline Press"
    assert no_progress["renderCount"] == 1

    adjusted_cancel = outputs["cancelAdjustedStart"]
    assert adjusted_cancel["confirmCalls"] == [
        "You have an in-progress workout. Discard logged sets and restart?"
    ]
    assert adjusted_cancel["activeName"] == "Chest Press"
    assert adjusted_cancel["keptNotes"] == "keep me"
    assert adjusted_cancel["adjustModalHidden"] is False
    assert adjusted_cancel["renderCount"] == 0

    adjusted_confirm = outputs["confirmAdjustedStart"]
    assert adjusted_confirm["activeName"] == "Hack Squat"
    assert adjusted_confirm["dirty"] is False
    assert adjusted_confirm["oldNotesPresent"] is False
    assert adjusted_confirm["adjustModalHidden"] is True
    assert adjusted_confirm["renderCount"] == 1

    shifted = outputs["shiftedAdjustment"]
    assert shifted["exerciseCount"] == 3
    assert shifted["firstName"] == "Seated Row"
    assert shifted["firstNotes"] == "row edit"
    assert shifted["firstWeight"] == "80"
    assert shifted["secondName"] == "Lat Pulldown"
    assert shifted["secondNotes"] == ""
    assert shifted["removedName"] == "Chest Press"
    assert shifted["removedNotes"] == "chest done"
    assert shifted["dirty"] is True

    assert outputs["aliasCanonicalAdjustment"] == {
        "exerciseCount": 1,
        "firstName": "Pec Fly",
        "firstNotes": "alias done",
        "firstWeight": "50",
    }

    assert outputs["unstartedRemoval"] == {
        "exerciseCount": 1,
        "onlyName": "Seated Row",
    }
    assert outputs["completedCardioRemoval"] == {
        "completed": True,
        "notes": "done cardio",
        "duration": "10",
    }
    assert outputs["freshCardioRecommendation"] == {
        "completed": False,
        "activity": "Bike",
        "duration": "10",
        "notes": "",
    }

    reduced = outputs["setReduction"]
    assert reduced["rowCount"] == 3
    assert reduced["firstNotes"] == "keep first"
    assert reduced["completedNotes"] == "completed third"
    assert reduced["droppedFourth"] is True
    assert outputs["freshTargets"] == {"weight": "85", "reps": "8", "rowCount": 1}
    assert outputs["indexedPreservation"] == {
        "firstWeight": "85",
        "firstNotes": "",
        "secondWeight": "90",
        "secondNotes": "second edit",
    }


def _run_start_guard_fixture() -> dict:
    helper_source = (
        "function prescribedRpeValue"
        + _slice_between(APP_JS, "function prescribedRpeValue", "function buildLoggedSets")
        + "function setActiveWorkoutFromRecommendation"
        + _slice_between(
            APP_JS,
            "function setActiveWorkoutFromRecommendation",
            "const SYNC_QUEUE_KEY",
        )
    )
    node_script = f"""
const vm = require('node:vm');
const helperSource = {json.dumps(helper_source)};
const sandbox = {{ module: {{ exports: {{}} }} }};
vm.runInNewContext(`
const state = {{
  activeWorkout: null,
  adjustedWorkout: null,
  dashboard: null,
}};
const elements = {{
  'modal-adjust': {{ hidden: false }},
  'active-workout-body': null,
}};
let confirmCalls = [];
let confirmResponses = [];
let renderCount = 0;
let dashboardNext = null;
const window = {{
  confirm(message) {{
    confirmCalls.push(message);
    return confirmResponses.length ? confirmResponses.shift() : true;
  }},
}};
const $ = (id) => elements[id] || null;
const qs = () => ({{ value: '', checked: false }});
const qsa = () => [];
const toast = () => {{}};
const saveActiveWorkoutDraft = () => {{}};
const getDashboard = async () => ({{ next_workout: dashboardNext }});
const newWorkoutId = (id) => 'new-' + (id || 'generated');
const exerciseName = (ex) => ex.exercise || ex.name || ex.machine || '';
const numericInputValue = (value) => {{
  if (value == null || value === '') return '';
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : '';
}};
const buildActiveExercise = (ex, previous) => ({{
  ...ex,
  logged_sets: previous && Array.isArray(previous.logged_sets)
    ? previous.logged_sets
    : [{{ weight: ex.target_weight || '', reps: ex.target_reps || '', done: false, notes: '' }}],
}});
const currentActiveWorkoutDraftScope = () => 'user:fit187';
${{helperSource}}
renderActiveWorkout = () => {{ renderCount += 1; }};
function activeWithProgress() {{
  return {{
    id: 'existing-workout',
    recommendation_id: 'old-rec',
    focus: 'Old',
    dirty: true,
    exercises: [{{
      exercise: 'Chest Press',
      logged_sets: [{{ weight: '100', reps: '10', done: true, notes: 'keep me' }}],
    }}],
    cardio: {{ completed: true, activity_type: 'Bike', duration_minutes: '10', notes: 'old cardio' }},
  }};
}}
function plannedWorkout(name, id) {{
  return {{
    id,
    workout_id: id + '-workout',
    focus: 'Next',
    auth_scope: 'user:fit187',
    exercises: [{{
      exercise: name,
      target_sets: 1,
      target_reps: 8,
      target_weight: 50,
    }}],
  }};
}}
function resetHarness() {{
  confirmCalls = [];
  confirmResponses = [];
  renderCount = 0;
  elements['modal-adjust'].hidden = false;
  dashboardNext = null;
}}
module.exports = {{
  state,
  elements,
  startWorkout,
  startAdjustedWorkout,
  applyAdjustedRecommendationToActiveWorkout,
  activeWithProgress,
  plannedWorkout,
  resetHarness,
  setConfirmResponses(values) {{ confirmResponses = values.slice(); }},
  setDashboardNext(value) {{ dashboardNext = value; }},
  confirmCalls() {{ return confirmCalls.slice(); }},
  renderCount() {{ return renderCount; }},
}};
`, sandbox);
const api = sandbox.module.exports;

async function run() {{
  const outputs = {{}};

  api.resetHarness();
  api.state.activeWorkout = api.activeWithProgress();
  api.setDashboardNext(api.plannedWorkout('Incline Press', 'cancel-start'));
  api.setConfirmResponses([false]);
  await api.startWorkout();
  outputs.cancelStart = {{
    confirmCalls: api.confirmCalls(),
    activeName: api.state.activeWorkout.exercises[0].exercise,
    keptNotes: api.state.activeWorkout.exercises[0].logged_sets[0].notes,
    renderCount: api.renderCount(),
  }};

  api.resetHarness();
  api.state.activeWorkout = api.activeWithProgress();
  api.setDashboardNext(api.plannedWorkout('Incline Press', 'confirm-start'));
  api.setConfirmResponses([true]);
  await api.startWorkout();
  outputs.confirmStart = {{
    confirmCalls: api.confirmCalls(),
    activeName: api.state.activeWorkout.exercises[0].exercise,
    dirty: api.state.activeWorkout.dirty,
    oldNotesPresent: JSON.stringify(api.state.activeWorkout).includes('keep me'),
    renderCount: api.renderCount(),
  }};

  api.resetHarness();
  api.state.activeWorkout = null;
  api.setDashboardNext(api.plannedWorkout('Incline Press', 'no-progress'));
  api.setConfirmResponses([false]);
  await api.startWorkout();
  outputs.noProgressStart = {{
    confirmCalls: api.confirmCalls(),
    activeName: api.state.activeWorkout.exercises[0].exercise,
    renderCount: api.renderCount(),
  }};

  api.resetHarness();
  api.state.activeWorkout = api.activeWithProgress();
  api.state.adjustedWorkout = api.plannedWorkout('Hack Squat', 'cancel-adjusted');
  api.setConfirmResponses([false]);
  await api.startAdjustedWorkout();
  outputs.cancelAdjustedStart = {{
    confirmCalls: api.confirmCalls(),
    activeName: api.state.activeWorkout.exercises[0].exercise,
    keptNotes: api.state.activeWorkout.exercises[0].logged_sets[0].notes,
    adjustModalHidden: api.elements['modal-adjust'].hidden,
    renderCount: api.renderCount(),
  }};

  api.resetHarness();
  api.state.activeWorkout = api.activeWithProgress();
  api.state.adjustedWorkout = api.plannedWorkout('Hack Squat', 'confirm-adjusted');
  api.setConfirmResponses([true]);
  await api.startAdjustedWorkout();
  outputs.confirmAdjustedStart = {{
    confirmCalls: api.confirmCalls(),
    activeName: api.state.activeWorkout.exercises[0].exercise,
    dirty: api.state.activeWorkout.dirty,
    oldNotesPresent: JSON.stringify(api.state.activeWorkout).includes('keep me'),
    adjustModalHidden: api.elements['modal-adjust'].hidden,
    renderCount: api.renderCount(),
  }};

  api.resetHarness();
  api.state.activeWorkout = {{
    id: 'shifted-existing',
    recommendation_id: 'old-rec',
    focus: 'Old',
    dirty: true,
    exercises: [{{
      exercise: 'Chest Press',
      logged_sets: [{{ weight: '100', reps: '10', done: true, notes: 'chest done' }}],
    }}, {{
      exercise: 'Seated Row',
      logged_sets: [{{ weight: '80', reps: '10', done: false, notes: 'row edit' }}],
    }}],
  }};
  const previousExercises = api.state.activeWorkout.exercises;
  api.applyAdjustedRecommendationToActiveWorkout({{
    id: 'shifted-rec',
    workout_id: 'shifted-workout',
    focus: 'Adjusted',
    exercises: [
      {{ exercise: 'Seated Row', target_sets: 1, target_reps: 8, target_weight: 85 }},
      {{ exercise: 'Lat Pulldown', target_sets: 1, target_reps: 10, target_weight: 70 }},
    ],
  }}, previousExercises);
  outputs.shiftedAdjustment = {{
    exerciseCount: api.state.activeWorkout.exercises.length,
    firstName: api.state.activeWorkout.exercises[0].exercise,
    firstNotes: api.state.activeWorkout.exercises[0].logged_sets[0].notes,
    firstWeight: api.state.activeWorkout.exercises[0].logged_sets[0].weight,
    secondName: api.state.activeWorkout.exercises[1].exercise,
    secondNotes: api.state.activeWorkout.exercises[1].logged_sets[0].notes,
    removedName: api.state.activeWorkout.exercises[2].exercise,
    removedNotes: api.state.activeWorkout.exercises[2].logged_sets[0].notes,
    dirty: api.state.activeWorkout.dirty,
  }};

  api.resetHarness();
  api.state.activeWorkout = {{
    id: 'alias-canonical-existing',
    dirty: true,
    exercises: [{{
      exercise: 'Pectoral Fly',
      target_sets: 1,
      target_reps: 10,
      target_weight: 50,
      logged_sets: [{{ weight: '50', reps: '10', done: true, notes: 'alias done' }}],
    }}],
  }};
  api.applyAdjustedRecommendationToActiveWorkout({{
    id: 'alias-canonical-rec',
    workout_id: 'alias-canonical-workout',
    focus: 'Adjusted',
    exercises: [
      {{ exercise: 'Pec Fly', target_sets: 1, target_reps: 8, target_weight: 55 }},
    ],
  }}, api.state.activeWorkout.exercises);
  outputs.aliasCanonicalAdjustment = {{
    exerciseCount: api.state.activeWorkout.exercises.length,
    firstName: api.state.activeWorkout.exercises[0].exercise,
    firstNotes: api.state.activeWorkout.exercises[0].logged_sets[0].notes,
    firstWeight: api.state.activeWorkout.exercises[0].logged_sets[0].weight,
  }};

  api.resetHarness();
  api.state.activeWorkout = {{
    id: 'unstarted-removed-existing',
    dirty: false,
    exercises: [{{
      exercise: 'Chest Press',
      target_sets: 1,
      target_reps: 10,
      target_weight: 100,
      logged_sets: [{{ weight: '100', reps: '10', done: false, notes: '' }}],
    }}],
  }};
  api.applyAdjustedRecommendationToActiveWorkout({{
    id: 'unstarted-removed-rec',
    workout_id: 'unstarted-removed-workout',
    focus: 'Adjusted',
    exercises: [
      {{ exercise: 'Seated Row', target_sets: 1, target_reps: 8, target_weight: 85 }},
    ],
  }}, api.state.activeWorkout.exercises);
  outputs.unstartedRemoval = {{
    exerciseCount: api.state.activeWorkout.exercises.length,
    onlyName: api.state.activeWorkout.exercises[0].exercise,
  }};

  api.resetHarness();
  api.state.activeWorkout = {{
    id: 'completed-cardio-existing',
    dirty: true,
    exercises: [{{
      exercise: 'Seated Row',
      target_sets: 1,
      target_reps: 10,
      target_weight: 80,
      logged_sets: [{{ weight: '80', reps: '10', done: false, notes: '' }}],
    }}],
    cardio: {{
      recommendation: {{ type: 'Bike', duration_minutes: 10 }},
      completed: true,
      activity_type: 'Bike',
      duration_minutes: '10',
      notes: 'done cardio',
    }},
  }};
  api.applyAdjustedRecommendationToActiveWorkout({{
    id: 'completed-cardio-rec',
    workout_id: 'completed-cardio-workout',
    focus: 'Adjusted',
    exercises: [
      {{ exercise: 'Seated Row', target_sets: 1, target_reps: 8, target_weight: 85 }},
    ],
  }}, api.state.activeWorkout.exercises);
  outputs.completedCardioRemoval = {{
    completed: api.state.activeWorkout.cardio.completed,
    notes: api.state.activeWorkout.cardio.notes,
    duration: api.state.activeWorkout.cardio.duration_minutes,
  }};

  api.resetHarness();
  api.state.activeWorkout = {{
    id: 'fresh-cardio-existing',
    dirty: false,
    exercises: [{{
      exercise: 'Seated Row',
      target_sets: 1,
      target_reps: 10,
      target_weight: 80,
      logged_sets: [{{ weight: '80', reps: '10', done: false, notes: '' }}],
    }}],
    cardio: {{
      recommendation: {{ type: 'Treadmill', duration_minutes: 20 }},
      completed: false,
      activity_type: 'Treadmill',
      duration_minutes: '20',
      notes: '',
    }},
  }};
  api.applyAdjustedRecommendationToActiveWorkout({{
    id: 'fresh-cardio-rec',
    workout_id: 'fresh-cardio-workout',
    focus: 'Adjusted',
    exercises: [
      {{ exercise: 'Seated Row', target_sets: 1, target_reps: 8, target_weight: 85 }},
    ],
    cardio: {{ type: 'Bike', duration_minutes: 10 }},
  }}, api.state.activeWorkout.exercises);
  outputs.freshCardioRecommendation = {{
    completed: api.state.activeWorkout.cardio.completed,
    activity: api.state.activeWorkout.cardio.activity_type,
    duration: api.state.activeWorkout.cardio.duration_minutes,
    notes: api.state.activeWorkout.cardio.notes,
  }};

  api.resetHarness();
  api.state.activeWorkout = {{
    id: 'reduced-existing',
    recommendation_id: 'old-rec',
    focus: 'Old',
    dirty: true,
    exercises: [{{
      exercise: 'Seated Row',
      logged_sets: [
        {{ weight: '80', reps: '10', done: false, notes: 'keep first' }},
        {{ weight: '82', reps: '9', done: false, notes: '' }},
        {{ weight: '85', reps: '8', done: true, notes: 'completed third' }},
        {{ weight: '87', reps: '7', done: false, notes: 'drop fourth' }},
      ],
    }}],
  }};
  api.applyAdjustedRecommendationToActiveWorkout({{
    id: 'reduced-rec',
    workout_id: 'reduced-workout',
    focus: 'Adjusted',
    exercises: [
      {{ exercise: 'Seated Row', target_sets: 2, target_reps: 8, target_weight: 85 }},
    ],
  }}, api.state.activeWorkout.exercises);
  outputs.setReduction = {{
    rowCount: api.state.activeWorkout.exercises[0].logged_sets.length,
    firstNotes: api.state.activeWorkout.exercises[0].logged_sets[0].notes,
    completedNotes: api.state.activeWorkout.exercises[0].logged_sets[2].notes,
    droppedFourth: !JSON.stringify(api.state.activeWorkout).includes('drop fourth'),
  }};

  api.resetHarness();
  api.state.activeWorkout = {{
    id: 'fresh-target-existing',
    dirty: false,
    exercises: [{{
      exercise: 'Seated Row',
      target_sets: 1,
      target_reps: 10,
      target_weight: 80,
      logged_sets: [{{ weight: '80', reps: '10', done: false, notes: '' }}],
    }}],
  }};
  api.applyAdjustedRecommendationToActiveWorkout({{
    id: 'fresh-target-rec',
    workout_id: 'fresh-target-workout',
    focus: 'Adjusted',
    exercises: [
      {{ exercise: 'Seated Row', target_sets: 1, target_reps: 8, target_weight: 85 }},
    ],
  }}, api.state.activeWorkout.exercises);
  outputs.freshTargets = {{
    weight: api.state.activeWorkout.exercises[0].logged_sets[0].weight,
    reps: api.state.activeWorkout.exercises[0].logged_sets[0].reps,
    rowCount: api.state.activeWorkout.exercises[0].logged_sets.length,
  }};

  api.resetHarness();
  api.state.activeWorkout = {{
    id: 'indexed-existing',
    dirty: true,
    exercises: [{{
      exercise: 'Seated Row',
      target_sets: 2,
      target_reps: 10,
      target_weight: 80,
      logged_sets: [
        {{ weight: '80', reps: '10', done: false, notes: '' }},
        {{ weight: '90', reps: '8', done: false, notes: 'second edit' }},
      ],
    }}],
  }};
  api.applyAdjustedRecommendationToActiveWorkout({{
    id: 'indexed-rec',
    workout_id: 'indexed-workout',
    focus: 'Adjusted',
    exercises: [
      {{ exercise: 'Seated Row', target_sets: 2, target_reps: 8, target_weight: 85 }},
    ],
  }}, api.state.activeWorkout.exercises);
  outputs.indexedPreservation = {{
    firstWeight: api.state.activeWorkout.exercises[0].logged_sets[0].weight,
    firstNotes: api.state.activeWorkout.exercises[0].logged_sets[0].notes,
    secondWeight: api.state.activeWorkout.exercises[0].logged_sets[1].weight,
    secondNotes: api.state.activeWorkout.exercises[0].logged_sets[1].notes,
  }};

  process.stdout.write(JSON.stringify(outputs));
}}
run().catch((err) => {{
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}});
"""
    result = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _slice_between(source: str, start_marker: str, end_marker: str) -> str:
    assert start_marker in source, f"{start_marker!r} missing from app.js"
    assert end_marker in source, f"{end_marker!r} missing from app.js"
    return source.split(start_marker, 1)[1].split(end_marker, 1)[0]
