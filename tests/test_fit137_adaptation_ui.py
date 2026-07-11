from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "js" / "app.js"
INDEX_HTML = ROOT / "templates" / "index.html"
STYLE_CSS = ROOT / "static" / "css" / "style.css"


def _block(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def test_adaptation_notice_uses_backend_event_feed_and_ack_endpoint():
    js = APP_JS.read_text()

    # Reads FIT-136's frozen contract feed and acks like the FIT-139 notice;
    # never invents a client-side adaptation.
    assert "/api/workout-adaptation-events?unacknowledged=true&limit=10" in js
    assert "/api/workout-adaptation-events/${encodeURIComponent(event.id)}/ack" in js
    assert "const workoutAdaptationNoticeState = {" in js
    assert "seen: new Set()" in js


def test_adaptation_notice_keeps_unacknowledged_applied_and_stale_events_visible():
    js = APP_JS.read_text()
    gate = _block(
        js,
        "function workoutAdaptationIsRenderable(event)",
        "function workoutAdaptationSignalLabels",
    )

    # Applied updates remain visible until acknowledgement even after their
    # original day. A stale source-meal marker remains visible and dismissable.
    assert "if (event.silent) return false;" in gate
    assert "if (!['applied', 'stale'].includes(event.status)) return false;" in gate
    assert "if (event.change_type === 'none') return false;" in gate
    assert "event.applies_to !== 'today'" not in gate


def test_adaptation_fetch_swallows_silent_and_nextday_events():
    js = APP_JS.read_text()
    fetch_block = _block(
        js,
        "async function fetchWorkoutAdaptationNotices()",
        "function newWorkoutId",
    )

    # Non-renderable events are marked seen but never shown — no empty card.
    assert "workoutAdaptationNoticeState.seen.add(event.id)" in fetch_block
    assert "if (!workoutAdaptationIsRenderable(event)) continue;" in fetch_block
    assert "showWorkoutAdaptationNotice(event)" in fetch_block


def test_adaptation_poll_replaces_card_when_server_status_changes():
    js = APP_JS.read_text()
    state_block = _block(
        js,
        "const workoutAdaptationNoticeState = {",
        "function workoutAdaptationIsRenderable",
    )
    fetch_block = _block(
        js,
        "async function fetchWorkoutAdaptationNotices()",
        "function newWorkoutId",
    )

    assert "statuses: new Map()" in state_block
    assert "previousStatus === event.status" in fetch_block
    assert "card.dataset.workoutAdaptationId === event.id" in fetch_block
    assert "card.remove()" in fetch_block

    if not shutil.which("node"):
        pytest.skip("adaptation status transition regression requires node")
    script = f"""
const vm = require('node:vm');
const fetchSource = {json.dumps(fetch_block)};
const source = `
let poll = 0;
const shown = [];
const cards = [];
const host = {{
  querySelectorAll() {{ return cards.filter((card) => !card.removed); }},
}};
const workoutAdaptationNoticeState = {{ fetching: false, seen: new Set(), statuses: new Map() }};
function withActiveWorkoutAdaptationParams(path) {{ return path; }}
function workoutAdaptationIsRenderable() {{ return true; }}
function $(id) {{ return id === 'workout-adaptation-host' ? host : null; }}
function showWorkoutAdaptationNotice(event) {{
  shown.push(event.status);
  cards.push({{
    dataset: {{ workoutAdaptationId: event.id }},
    removed: false,
    remove() {{ this.removed = true; }},
  }});
}}
async function api() {{
  poll += 1;
  return {{ events: [{{ id: 'event-1', status: poll === 1 ? 'applied' : 'stale' }}] }};
}}
${{fetchSource}}
module.exports = async () => {{
  await fetchWorkoutAdaptationNotices();
  await fetchWorkoutAdaptationNotices();
  return {{ shown, firstRemoved: cards[0].removed }};
}};
`;
const sandbox = {{ module: {{ exports: {{}} }} }};
vm.runInNewContext(source, sandbox);
sandbox.module.exports().then((result) => process.stdout.write(JSON.stringify(result)));
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {"shown": ["applied", "stale"], "firstRemoved": True}


def test_adaptation_notice_renders_neutral_reason_and_collapsed_details():
    js = APP_JS.read_text()
    notice = _block(
        js,
        "function showWorkoutAdaptationNotice(event)",
        "async function fetchWorkoutAdaptationNotices",
    )

    # AC2: concise neutral reason string straight from FIT-136.
    assert "event.reason" in notice
    # AC3: per-meal/item specifics behind a collapsed native <details>.
    assert "document.createElement('details')" in notice
    assert "workout-adaptation-details" in notice
    assert "View details" in notice
    # Neutral signal labels (not moral labels) surface inside the disclosure.
    assert "workoutAdaptationSignalLabels(event)" in notice
    # Accessible, passive confirmation (mirror FIT-139 tone).
    assert "role', 'status'" in notice
    assert "aria-live', 'polite'" in notice
    assert "workout-adaptation-dismiss" in notice
    assert "if (event.status === 'applied') applyWorkoutAdaptationToActiveWorkout(event);" in notice


def test_adaptation_dismiss_failure_does_not_duplicate_card():
    js = APP_JS.read_text()
    notice = _block(
        js,
        "function showWorkoutAdaptationNotice(event)",
        "async function fetchWorkoutAdaptationNotices",
    )

    # On a failed ack the card must stay and the event must remain in `seen`,
    # otherwise the next poll re-renders a duplicate card. Guard the fix:
    assert "workoutAdaptationNoticeState.seen.delete" not in notice
    assert "dismiss.disabled = false;" in notice  # button stays retry-able


def test_adaptation_does_not_surface_audit_log():
    js = APP_JS.read_text()
    block = _block(
        js,
        "const workoutAdaptationNoticeState = {",
        "function newWorkoutId",
    )

    # AC: the internal audit history is backend-only — the visible render path
    # must not fetch or render the audit-only event fields (reason_metadata /
    # rules / citations) or hit any audit endpoint.
    assert "reason_metadata" not in block
    assert "citations" not in block
    assert ".rules" not in block
    assert "/audit" not in block
    assert "audit-log" not in block
    assert "audit_log" not in block


def test_adaptation_requests_include_active_workout_params_runtime():
    outputs = _run_fit257_runtime_fixtures_in_node()

    notice = outputs["notice"]
    assert notice["pathname"] == "/api/workout-adaptation-events"
    assert notice["unacknowledged"] == "true"
    assert notice["limit"] == "10"
    assert notice["active_workout_open"] == "true"
    assert notice["completed_sets"] == {"Chest Press": 2, "Squat": 1}

    next_workout = outputs["nextWorkout"]
    assert next_workout["pathname"] == "/api/next-workout"
    assert next_workout["active_workout_open"] == "true"
    assert next_workout["completed_sets"] == {"Chest Press": 2, "Squat": 1}


def test_adaptation_preserves_completed_active_work_via_identity_merge():
    outputs = _run_fit257_runtime_fixtures_in_node()

    merge = outputs["merge"]
    assert merge["fetchedNextWorkout"] is True
    assert merge["rendered"] is True
    assert merge["previousDone"] is True
    assert merge["previousReps"] == "8"


def test_adaptation_fetch_is_hooked_to_dashboard_surfaces():
    js = APP_JS.read_text()

    # Polled from the same passive surfaces as the FIT-139 refresh notice.
    assert js.count("fetchWorkoutAdaptationNotices().catch") >= 3


def test_adaptation_host_lives_in_dashboard_tab():
    html = INDEX_HTML.read_text()
    # Host is inside the Dash tab panel (appears before the next tab section).
    assert 'id="workout-adaptation-host"' in html
    host_index = html.index('id="workout-adaptation-host"')
    dash_index = html.index('id="tab-dashboard"')
    assert dash_index < host_index
    next_tab_index = html.index('id="tab-workout"') if 'id="tab-workout"' in html else len(html)
    assert host_index < next_tab_index


def test_adaptation_styles_present_and_calm():
    css = STYLE_CSS.read_text()
    block = _block(css, ".workout-adaptation-host {", ".analyze-section {")

    assert ".workout-adaptation-card {" in block
    assert ".workout-adaptation-reason {" in block
    assert ".workout-adaptation-details {" in block
    assert ".workout-adaptation-chip {" in block
    assert "overflow-wrap: anywhere" in block


def _run_fit257_runtime_fixtures_in_node() -> dict:
    if not shutil.which("node"):
        pytest.skip("FIT-257 runtime regression requires node to execute app.js")

    js = APP_JS.read_text()
    helper_source = _block(
        js,
        "function exerciseName(ex)",
        "function exerciseMuscle",
    )
    fetch_source = _block(
        js,
        "async function fetchWorkoutAdaptationNotices()",
        "function newWorkoutId",
    )
    next_workout_source = _block(
        js,
        "async function getNextWorkout(force = false)",
        "async function getVitals",
    )
    merge_source = _block(
        js,
        "function applyWorkoutAdaptationToActiveWorkout(event)",
        "function showWorkoutAdaptationNotice",
    )
    helper_source_json = json.dumps(helper_source)
    fetch_source_json = json.dumps(fetch_source)
    next_workout_source_json = json.dumps(next_workout_source)
    merge_source_json = json.dumps(merge_source)
    node_script = f"""
const vm = require('node:vm');
const helperSource = {helper_source_json};
const fetchSource = {fetch_source_json};
const nextWorkoutSource = {next_workout_source_json};
const mergeSource = {merge_source_json};
const sandbox = {{ module: {{ exports: {{}} }}, URLSearchParams, URL, console }};
const runtimeSource = `
const DASHBOARD_FETCH_TIMEOUT_MS = 30000;
const calls = [];
let rendered = false;
let mergeCall = null;
let fetchedNextWorkout = false;
const state = {{
  activeWorkout: {{
    exercises: [
      {{ name: 'Chest Press', logged_sets: [
        {{ done: true, reps: '8', weight: '100' }},
        {{ done: true, reps: '8', weight: '100' }},
        {{ done: false, reps: '8', weight: '100' }},
      ] }},
      {{ exercise: 'Squat', logged_sets: [{{ done: true, reps: '5', weight: '185' }}] }},
      {{ name: 'Rows', logged_sets: [{{ done: false, reps: '10', weight: '80' }}] }},
    ],
  }},
  nextWorkout: null,
}};
const workoutAdaptationNoticeState = {{ fetching: false, seen: new Set() }};
function workoutAdaptationIsRenderable() {{ return false; }}
function showWorkoutAdaptationNotice() {{}}
async function api(path, opts = {{}}) {{
  calls.push({{ path, opts }});
  if (String(path).startsWith('/api/next-workout')) {{
    fetchedNextWorkout = true;
    return {{ next_workout: {{ id: 'adapted-plan', exercises: [{{ name: 'Chest Press', target_sets: 2 }}] }} }};
  }}
  return {{ events: [] }};
}}
function applyAdjustedRecommendationToActiveWorkout(nw, previous) {{
  mergeCall = {{ nw, previous }};
}}
function renderActiveWorkout() {{ rendered = true; }}
function parseCall(index) {{
  const url = new URL(calls[index].path, 'https://fitness.local');
  const completedRaw = url.searchParams.get('completed_sets');
  return {{
    pathname: url.pathname,
    unacknowledged: url.searchParams.get('unacknowledged'),
    limit: url.searchParams.get('limit'),
    active_workout_open: url.searchParams.get('active_workout_open'),
    completed_sets: completedRaw ? JSON.parse(completedRaw) : null,
  }};
}}
async function run() {{
  await fetchWorkoutAdaptationNotices();
  state.nextWorkout = null;
  await getNextWorkout(true);
  applyWorkoutAdaptationToActiveWorkout({{ active_workout: {{ updated_live: true }} }});
  await Promise.resolve();
  await Promise.resolve();
  return {{
    notice: parseCall(0),
    nextWorkout: parseCall(1),
    merge: {{
      fetchedNextWorkout,
      rendered,
      previousDone: Boolean(mergeCall && mergeCall.previous[0].logged_sets[0].done),
      previousReps: mergeCall && mergeCall.previous[0].logged_sets[0].reps,
    }},
  }};
}}
module.exports = {{ run }};
` + helperSource + '\\n' + fetchSource + '\\n' + nextWorkoutSource + '\\n' + mergeSource;
vm.runInNewContext(runtimeSource, sandbox);
sandbox.module.exports.run().then((outputs) => {{
  process.stdout.write(JSON.stringify(outputs));
}}).catch((error) => {{
  console.error(error && error.stack ? error.stack : error);
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
