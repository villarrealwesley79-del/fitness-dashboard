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
const workoutAdaptationNoticeState = {{ fetching: false, seen: new Set(), statuses: new Map(), dismissed: new Set() }};
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


@pytest.mark.parametrize(
    ("ack_fails", "expected"),
    [
        (False, {"appended": 1, "active": 0, "feed_calls": 3, "kicker": None}),
        (True, {"appended": 2, "active": 1, "feed_calls": 3, "kicker": "Workout update stale"}),
    ],
    ids=["ack-succeeds", "ack-fails"],
)
def test_acknowledging_adaptation_reconciles_inflight_stale_replacement(ack_fails, expected):
    js = APP_JS.read_text()
    state_block = _block(
        js,
        "const workoutAdaptationNoticeState = {",
        "function workoutAdaptationIsRenderable",
    )
    notice_block = _block(
        js,
        "function showWorkoutAdaptationNotice(event)",
        "async function fetchWorkoutAdaptationNotices",
    )
    fetch_block = _block(
        js,
        "async function fetchWorkoutAdaptationNotices()",
        "function newWorkoutId",
    )

    if not shutil.which("node"):
        pytest.skip("adaptation acknowledgement race regression requires node")
    script = f"""
const vm = require('node:vm');
const source = `
let resolveFeed;
let resolveAck;
let rejectAck;
let feedCalls = 0;
const staleFeed = new Promise((resolve) => {{ resolveFeed = resolve; }});
const ackRequest = new Promise((resolve, reject) => {{ resolveAck = resolve; rejectAck = reject; }});
const appendedCards = [];
function element() {{
  return {{
    children: [],
    dataset: {{}},
    disabled: false,
    removed: false,
    appendChild(child) {{ this.children.push(child); }},
    setAttribute() {{}},
    addEventListener(type, handler) {{ this[type] = handler; }},
    remove() {{ this.removed = true; }},
  }};
}}
const host = {{
  hidden: true,
  get children() {{ return appendedCards.filter((card) => !card.removed); }},
  appendChild(card) {{ appendedCards.push(card); }},
  querySelectorAll() {{ return this.children; }},
}};
const document = {{ createElement: element }};
function $(id) {{ return id === 'workout-adaptation-host' ? host : null; }}
function escapeHtml(value) {{ return value; }}
function workoutAdaptationIsRenderable() {{ return true; }}
function workoutAdaptationSignalLabels() {{ return []; }}
function workoutAdaptationRemainingPlanRows() {{ return ''; }}
function applyWorkoutAdaptationToActiveWorkout() {{}}
function withActiveWorkoutAdaptationParams(path) {{ return path; }}
async function api(path) {{
  if (path.includes('/ack')) return ackRequest;
  feedCalls += 1;
  if (feedCalls === 1) return {{ events: [{{ id: 'event-1', status: 'applied' }}] }};
  if (feedCalls === 2) return staleFeed;
  return {{ events: [{{ id: 'event-1', status: 'stale' }}] }};
}}
${{{json.dumps(state_block)}}}
${{{json.dumps(notice_block)}}}
${{{json.dumps(fetch_block)}}}
module.exports = async () => {{
  await fetchWorkoutAdaptationNotices();
  const secondPoll = fetchWorkoutAdaptationNotices();
  const dismissButton = host.children[0].children[0].children[1];
  const dismissal = dismissButton.click();
  resolveFeed({{ events: [{{ id: 'event-1', status: 'stale' }}] }});
  await secondPoll;
  if ({json.dumps(ack_fails)}) rejectAck(new Error('ack failed'));
  else resolveAck({{ ok: true }});
  await dismissal;
  await Promise.resolve();
  const current = host.children[0];
  return {{
    appended: appendedCards.length,
    active: host.children.length,
    feed_calls: feedCalls,
    kicker: current ? current.children[0].children[0].textContent : null,
  }};
}};
`;
const sandbox = {{ module: {{ exports: {{}} }}, console }};
vm.runInNewContext(source, sandbox);
sandbox.module.exports().then((result) => process.stdout.write(JSON.stringify(result)));
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == expected


def test_successful_adaptation_ack_refills_bounded_feed_during_inflight_poll():
    js = APP_JS.read_text()
    state_block = _block(
        js,
        "const workoutAdaptationNoticeState = {",
        "function workoutAdaptationIsRenderable",
    )
    notice_block = _block(
        js,
        "function showWorkoutAdaptationNotice(event)",
        "async function fetchWorkoutAdaptationNotices",
    )
    fetch_block = _block(
        js,
        "async function fetchWorkoutAdaptationNotices()",
        "function newWorkoutId",
    )

    if not shutil.which("node"):
        pytest.skip("adaptation acknowledgement refill regression requires node")
    script = f"""
const vm = require('node:vm');
const source = `
let resolveFeed;
let feedCalls = 0;
const staleFeed = new Promise((resolve) => {{ resolveFeed = resolve; }});
const appendedCards = [];
function element() {{
  return {{
    children: [],
    dataset: {{}},
    disabled: false,
    removed: false,
    appendChild(child) {{ this.children.push(child); }},
    setAttribute() {{}},
    addEventListener(type, handler) {{ this[type] = handler; }},
    remove() {{ this.removed = true; }},
  }};
}}
const host = {{
  hidden: true,
  get children() {{ return appendedCards.filter((card) => !card.removed); }},
  appendChild(card) {{ appendedCards.push(card); }},
  querySelectorAll() {{ return this.children; }},
}};
const document = {{ createElement: element }};
function $(id) {{ return id === 'workout-adaptation-host' ? host : null; }}
function escapeHtml(value) {{ return value; }}
function workoutAdaptationIsRenderable() {{ return true; }}
function workoutAdaptationSignalLabels() {{ return []; }}
function workoutAdaptationRemainingPlanRows() {{ return ''; }}
function applyWorkoutAdaptationToActiveWorkout() {{}}
function withActiveWorkoutAdaptationParams(path) {{ return path; }}
async function api(path) {{
  if (path.includes('/ack')) return {{ ok: true }};
  feedCalls += 1;
  if (feedCalls === 1) return {{ events: [{{ id: 'event-1', status: 'applied' }}] }};
  if (feedCalls === 2) return staleFeed;
  return {{ events: [{{ id: 'event-2', status: 'applied' }}] }};
}}
${{{json.dumps(state_block)}}}
${{{json.dumps(notice_block)}}}
${{{json.dumps(fetch_block)}}}
module.exports = async () => {{
  await fetchWorkoutAdaptationNotices();
  const inFlightPoll = fetchWorkoutAdaptationNotices();
  const dismissButton = host.children[0].children[0].children[1];
  await dismissButton.click();
  resolveFeed({{ events: [{{ id: 'event-1', status: 'applied' }}] }});
  await inFlightPoll;
  await new Promise(setImmediate);
  const current = host.children[0];
  return {{
    appended: appendedCards.length,
    active: host.children.length,
    feed_calls: feedCalls,
    current_id: current ? current.dataset.workoutAdaptationId : null,
  }};
}};
`;
const sandbox = {{ module: {{ exports: {{}} }}, console, setImmediate }};
vm.runInNewContext(source, sandbox);
sandbox.module.exports().then((result) => process.stdout.write(JSON.stringify(result)));
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {
        "appended": 2,
        "active": 1,
        "feed_calls": 3,
        "current_id": "event-2",
    }


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


def test_historical_notice_does_not_reapply_adaptation_to_active_workout():
    js = APP_JS.read_text()
    apply_block = _block(
        js,
        "function applyWorkoutAdaptationToActiveWorkout(event)",
        "function showWorkoutAdaptationNotice(event)",
    )

    if not shutil.which("node"):
        pytest.skip("historical adaptation regression requires node")
    script = f"""
const vm = require('node:vm');
const source = `
const state = {{ activeWorkout: {{ exercises: [] }} }};
let nextWorkoutCalls = 0;
function today() {{ return '2026-07-11'; }}
function getNextWorkout() {{ nextWorkoutCalls += 1; return Promise.resolve(null); }}
function applyAdjustedRecommendationToActiveWorkout() {{}}
function renderActiveWorkout() {{}}
${{{json.dumps(apply_block)}}}
applyWorkoutAdaptationToActiveWorkout({{
  date: '2026-07-10',
  active_workout: {{ updated_live: true }},
}});
module.exports = nextWorkoutCalls;
`;
const sandbox = {{ module: {{ exports: null }}, console }};
vm.runInNewContext(source, sandbox);
process.stdout.write(JSON.stringify(sandbox.module.exports));
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == 0


def test_current_next_day_notice_updates_active_workout():
    js = APP_JS.read_text()
    apply_block = _block(
        js,
        "function applyWorkoutAdaptationToActiveWorkout(event)",
        "function showWorkoutAdaptationNotice(event)",
    )

    if not shutil.which("node"):
        pytest.skip("next-day adaptation regression requires node")
    script = f"""
const vm = require('node:vm');
const source = `
const state = {{ activeWorkout: {{ exercises: [] }} }};
let nextWorkoutCalls = 0;
function today() {{ return '2026-07-11'; }}
function getNextWorkout() {{ nextWorkoutCalls += 1; return Promise.resolve(null); }}
function applyAdjustedRecommendationToActiveWorkout() {{}}
function renderActiveWorkout() {{}}
${{{json.dumps(apply_block)}}}
applyWorkoutAdaptationToActiveWorkout({{
  date: '2026-07-10',
  created_at: '2026-07-11T00:03:01',
  applies_to: 'next_day',
  active_workout: {{ updated_live: true }},
}});
module.exports = nextWorkoutCalls;
`;
const sandbox = {{ module: {{ exports: null }}, console }};
vm.runInNewContext(source, sandbox);
process.stdout.write(JSON.stringify(sandbox.module.exports));
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == 1


def test_meal_editor_sends_null_when_portion_is_cleared():
    js = APP_JS.read_text()
    save_block = _block(
        js,
        "async function saveMealCorrection(entry, modal, saveBtn)",
        "// ===================== FIT-107: View food log sheet",
    )

    if not shutil.which("node"):
        pytest.skip("meal editor payload regression requires node")
    script = f"""
const vm = require('node:vm');
const source = `
const values = {{
  'meal-edit-item': 'Chicken bowl',
  'meal-edit-portion': '',
  'meal-edit-cal': '500',
  'meal-edit-pro': '35',
  'meal-edit-carb': '45',
  'meal-edit-fat': '18',
  'meal-edit-sodium': '700',
}};
function $(id) {{ return {{ value: values[id] || '', hidden: false, textContent: '' }}; }}
let sentPayload = null;
let adaptationFetches = 0;
async function api(_path, options) {{ sentPayload = JSON.parse(options.body); }}
function toast() {{}}
function renderBodyInterpretationAndNutritionTrend() {{}}
function fetchWorkoutAdaptationNotices() {{ adaptationFetches += 1; return Promise.resolve(); }}
${{{json.dumps(save_block)}}}
(async () => {{
  await saveMealCorrection(
    {{ client_id: 'meal-1', date: '2026-05-24', logged_at: '2026-05-24T12:00:00' }},
    {{ hidden: false }},
    {{ disabled: false }},
  );
  module.exports = {{ portion: sentPayload.portion_description, adaptationFetches }};
}})();
`;
const sandbox = {{ module: {{ exports: 'unset' }}, console }};
vm.runInNewContext(source, sandbox);
setImmediate(() => process.stdout.write(JSON.stringify(sandbox.module.exports)));
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {"portion": None, "adaptationFetches": 1}


def test_meal_delete_refreshes_visible_adaptation_notice():
    js = APP_JS.read_text()
    detail_block = _block(
        js,
        "function openMealDetailModal(entry, listContainer)",
        "function setMealDetailMode(mode)",
    )
    delete_success = _block(
        detail_block,
        "await api(`/api/meal-intake/${encodeURIComponent(entry.client_id)}`",
        "} catch (err)",
    )

    assert "fetchWorkoutAdaptationNotices()" in delete_success


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
    assert "workoutAdaptationNoticeState.dismissed.delete(event.id);" in notice
    assert "fetchWorkoutAdaptationNotices().catch" in notice


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
function today() {{ return '2026-07-11'; }}
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
  applyWorkoutAdaptationToActiveWorkout({{
    date: '2026-07-11',
    active_workout: {{ updated_live: true }},
  }});
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
