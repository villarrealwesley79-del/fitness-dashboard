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


def test_fit233_rollout_invalidates_cached_get_only_clients():
    version = "20260713-fit233-adaptation-polling"
    html = INDEX_HTML.read_text()
    loader = (ROOT / "static" / "js" / "app-loader.js").read_text()
    service_worker = (ROOT / "static" / "js" / "sw.js").read_text()

    assert f"/static/js/app-loader.js?v={version}" in html
    assert f"/static/js/app.js?v={version}" in loader
    assert f"fitness-dashboard-v{version}" in service_worker


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


def test_adaptation_notice_gates_on_applied_change_and_today():
    js = APP_JS.read_text()
    gate = _block(
        js,
        "function workoutAdaptationIsRenderable(event)",
        "function workoutAdaptationSignalLabels",
    )

    # AC1/AC8: silent (no-change / low-confidence) renders nothing; only an
    # applied change to today's plan confirms. AC5: next-day never toasts here.
    assert "if (event.silent) return false;" in gate
    assert "if (event.status !== 'applied') return false;" in gate
    assert "if (event.change_type === 'none') return false;" in gate
    assert "if (event.applies_to !== 'today') return false;" in gate


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


def test_applied_evaluation_refreshes_visible_workout_state():
    js = APP_JS.read_text()
    refresh_block = _block(
        js,
        "async function refreshWorkoutAdaptationVisiblePlan()",
        "async function fetchWorkoutAdaptationNotices()",
    )
    fetch_block = _block(
        js,
        "async function fetchWorkoutAdaptationNotices()",
        "function newWorkoutId",
    )

    assert "Number(evaluation && evaluation.evaluated_count) > 0" in fetch_block
    assert "refreshWorkoutAdaptationVisiblePlan()" in fetch_block
    assert "await getDashboard(true);" in refresh_block
    assert "paintDashboardFromState();" in refresh_block
    assert "await renderNextWorkout();" in refresh_block


def test_future_adaptation_window_retries_after_clock_advance():
    if not shutil.which("node"):
        pytest.skip("FIT-233 clock-advance regression requires node")

    js = APP_JS.read_text()
    assert "function scheduleWorkoutAdaptationEvaluationRetry" in js
    retry_and_fetch_source = _block(
        js,
        "function scheduleWorkoutAdaptationEvaluationRetry",
        "function newWorkoutId",
    )
    source_json = json.dumps(retry_and_fetch_source)
    node_script = f"""
const vm = require('node:vm');
const source = {source_json};
const sandbox = {{ module: {{ exports: {{}} }}, URLSearchParams, URL, console }};
const runtimeSource = `
const DASHBOARD_FETCH_TIMEOUT_MS = 30000;
const WORKOUT_ADAPTATION_FAILURE_RETRY_MS = 60000;
const WORKOUT_ADAPTATION_IN_FLIGHT_RETRY_MS = 1000;
const timers = [];
const clearedTimers = [];
const calls = [];
let failEvaluation = false;
const workoutAdaptationNoticeState = {{ fetching: false, seen: new Set(), retryTimer: null }};
function setTimeout(callback, delay) {{
  timers.push({{ callback, delay }});
  return timers.length;
}}
function clearTimeout(timer) {{ clearedTimers.push(timer); }}
function withActiveWorkoutAdaptationParams(path) {{ return path; }}
function workoutAdaptationIsRenderable() {{ return false; }}
function showWorkoutAdaptationNotice() {{}}
    async function getDashboard() {{}}
    function paintDashboardFromState() {{}}
    async function getNextWorkout() {{ return {{ id: 'adapted-plan' }}; }}
    async function renderNextWorkout() {{}}
async function api(path, opts = {{}}) {{
  calls.push({{ path, method: opts.method || null }});
  if (String(path).startsWith('/api/workout-adaptation-events/evaluate')) {{
    if (failEvaluation) throw new Error('temporary evaluation failure');
    return {{ evaluated_count: 0, retry_after_ms: 180000 }};
  }}
  return {{ events: [] }};
}}
async function run() {{
  await fetchWorkoutAdaptationNotices();
  const initialDelay = timers[0] && timers[0].delay;
  failEvaluation = true;
  await fetchWorkoutAdaptationNotices();
  const failureReplacementDelay = timers[1] && timers[1].delay;
  failEvaluation = false;
  workoutAdaptationNoticeState.fetching = true;
  await timers[1].callback();
  const inFlightRetryDelay = timers[2] && timers[2].delay;
  workoutAdaptationNoticeState.fetching = false;
  await timers[2].callback();
  return {{
    initialDelay,
    failureReplacementDelay,
    clearedTimers,
    inFlightRetryDelay,
    evaluationCalls: calls.filter((call) => call.path.startsWith('/api/workout-adaptation-events/evaluate')).length,
    feedCalls: calls.filter((call) => call.path.startsWith('/api/workout-adaptation-events?')).length,
  }};
}}
module.exports = {{ run }};
` + source;
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
    outputs = json.loads(result.stdout)

    assert outputs["initialDelay"] == 180_000
    assert outputs["failureReplacementDelay"] == 60_000
    assert outputs["clearedTimers"] == [1]
    assert outputs["inFlightRetryDelay"] == 1_000
    assert outputs["evaluationCalls"] == 3
    assert outputs["feedCalls"] == 3


def test_failed_adapted_workout_refresh_retries_before_reading_feed():
    if not shutil.which("node"):
        pytest.skip("FIT-233 refresh retry regression requires node")

    js = APP_JS.read_text()
    retry_and_fetch_source = _block(
        js,
        "function scheduleWorkoutAdaptationEvaluationRetry",
        "function newWorkoutId",
    )
    source_json = json.dumps(retry_and_fetch_source)
    node_script = f"""
const vm = require('node:vm');
const source = {source_json};
const sandbox = {{ module: {{ exports: {{}} }}, console }};
const runtimeSource = `
const DASHBOARD_FETCH_TIMEOUT_MS = 30000;
const WORKOUT_ADAPTATION_FAILURE_RETRY_MS = 60000;
const WORKOUT_ADAPTATION_IN_FLIGHT_RETRY_MS = 1000;
const timers = [];
const calls = [];
let evaluationCount = 1;
let failRefresh = true;
let dashboardCalls = 0;
let nextWorkoutCalls = 0;
const workoutAdaptationNoticeState = {{
  fetching: false,
  seen: new Set(),
  retryTimer: null,
  refreshPending: false,
}};
function setTimeout(callback, delay) {{
  timers.push({{ callback, delay }});
  return timers.length;
}}
function clearTimeout() {{}}
function withActiveWorkoutAdaptationParams(path) {{ return path; }}
function workoutAdaptationIsRenderable() {{ return false; }}
function showWorkoutAdaptationNotice() {{}}
async function api(path, opts = {{}}) {{
  calls.push({{ path, method: opts.method || null }});
  if (String(path).startsWith('/api/workout-adaptation-events/evaluate')) {{
    const evaluated_count = evaluationCount;
    evaluationCount = 0;
    return {{ evaluated_count, retry_after_ms: null }};
  }}
  return {{ events: [] }};
}}
async function getDashboard() {{
  dashboardCalls += 1;
}}
function paintDashboardFromState() {{}}
async function getNextWorkout() {{
  nextWorkoutCalls += 1;
  if (failRefresh) throw new Error('next workout unavailable');
  return {{ id: 'adapted-plan' }};
}}
async function renderNextWorkout() {{
  try {{ await getNextWorkout(true); }} catch (_error) {{}}
}}
async function run() {{
  await fetchWorkoutAdaptationNotices();
  const feedCallsAfterFailure = calls.filter((call) => call.path.startsWith('/api/workout-adaptation-events?')).length;
  const refreshRetryDelay = timers[0] && timers[0].delay;
  failRefresh = false;
  await timers[0].callback();
  return {{
    feedCallsAfterFailure,
    refreshRetryDelay,
    finalFeedCalls: calls.filter((call) => call.path.startsWith('/api/workout-adaptation-events?')).length,
    dashboardCalls,
    nextWorkoutCalls,
  }};
}}
module.exports = {{ run }};
` + source;
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
    outputs = json.loads(result.stdout)

    assert outputs["feedCallsAfterFailure"] == 0
    assert outputs["refreshRetryDelay"] == 60_000
    assert outputs["finalFeedCalls"] == 1
    assert outputs["dashboardCalls"] == 2
    assert outputs["nextWorkoutCalls"] == 3


def test_ambiguous_evaluation_and_feed_failures_keep_retrying():
    if not shutil.which("node"):
        pytest.skip("FIT-233 failure-boundary regression requires node")

    js = APP_JS.read_text()
    retry_and_fetch_source = _block(
        js,
        "function scheduleWorkoutAdaptationEvaluationRetry",
        "function newWorkoutId",
    )
    source_json = json.dumps(retry_and_fetch_source)
    node_script = f"""
const vm = require('node:vm');
const source = {source_json};
const sandbox = {{ module: {{ exports: {{}} }}, console }};
const runtimeSource = `
const DASHBOARD_FETCH_TIMEOUT_MS = 30000;
const WORKOUT_ADAPTATION_FAILURE_RETRY_MS = 60000;
const WORKOUT_ADAPTATION_IN_FLIGHT_RETRY_MS = 1000;
const timers = [];
const calls = [];
let failEvaluation = true;
let failFeed = false;
let peerRevision = null;
let feedRevision = 0;
let feedEvents = [];
let dashboardCalls = 0;
const workoutAdaptationNoticeState = {{
  fetching: false,
  seen: new Set(),
  retryTimer: null,
  retryDeadlineMs: null,
  refreshPending: false,
  appliedPlanRevision: 0,
  pendingAppliedPlanRevision: null,
}};
function setTimeout(callback, delay) {{
  timers.push({{ callback, delay }});
  return timers.length;
}}
function clearTimeout() {{}}
function withActiveWorkoutAdaptationParams(path) {{ return path; }}
function workoutAdaptationIsRenderable() {{ return false; }}
function showWorkoutAdaptationNotice() {{}}
async function getDashboard() {{ dashboardCalls += 1; }}
function paintDashboardFromState() {{}}
async function getNextWorkout() {{ return {{ id: 'adapted-plan' }}; }}
async function renderNextWorkout() {{}}
async function api(path, opts = {{}}) {{
  calls.push({{ path, method: opts.method || null }});
  if (String(path).startsWith('/api/workout-adaptation-events/evaluate')) {{
    if (failEvaluation) throw new Error('response lost after commit');
    return {{
      evaluated_count: 0,
      retry_after_ms: null,
      applied_plan_revision: peerRevision,
    }};
  }}
  if (failFeed) throw new Error('feed temporarily unavailable');
  return {{ events: feedEvents, applied_plan_revision: feedRevision }};
}}
async function run() {{
  await fetchWorkoutAdaptationNotices();
  const ambiguousFailure = {{
    dashboardCalls,
    feedCalls: calls.filter((call) => call.path.startsWith('/api/workout-adaptation-events?')).length,
    retryDelay: timers[0] && timers[0].delay,
  }};
  failEvaluation = false;
  failFeed = true;
  await fetchWorkoutAdaptationNotices();
  failFeed = false;
  feedRevision = 1;
  feedEvents = [{{ id: 'between-event', status: 'applied' }}];
  await fetchWorkoutAdaptationNotices();
  return {{
    ambiguousFailure,
    feedFailureRetryDelay: timers[timers.length - 1] && timers[timers.length - 1].delay,
    dashboardCallsAfterBetweenEvent: dashboardCalls,
    seenCount: workoutAdaptationNoticeState.seen.size,
  }};
}}
module.exports = {{ run }};
` + source;
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
    outputs = json.loads(result.stdout)

    assert outputs["ambiguousFailure"]["dashboardCalls"] == 1
    assert outputs["ambiguousFailure"]["feedCalls"] == 1
    assert outputs["ambiguousFailure"]["retryDelay"] == 60_000
    assert outputs["feedFailureRetryDelay"] == 60_000
    assert outputs["dashboardCallsAfterBetweenEvent"] == 2
    assert outputs["seenCount"] == 1


def test_overlapping_adaptation_polls_collapse_to_one_follow_up():
    if not shutil.which("node"):
        pytest.skip("FIT-233 overlap regression requires node")

    js = APP_JS.read_text()
    retry_and_fetch_source = _block(
        js,
        "function scheduleWorkoutAdaptationEvaluationRetry",
        "function newWorkoutId",
    )
    source_json = json.dumps(retry_and_fetch_source)
    node_script = f"""
const vm = require('node:vm');
const source = {source_json};
const sandbox = {{ module: {{ exports: {{}} }}, console }};
const runtimeSource = `
const DASHBOARD_FETCH_TIMEOUT_MS = 30000;
const WORKOUT_ADAPTATION_FAILURE_RETRY_MS = 60000;
const WORKOUT_ADAPTATION_IN_FLIGHT_RETRY_MS = 1000;
const timers = new Map();
const calls = [];
let nextTimerId = 1;
let releaseFirstEvaluation;
const firstEvaluation = new Promise((resolve) => {{ releaseFirstEvaluation = resolve; }});
const workoutAdaptationNoticeState = {{
  fetching: false,
  rerunPending: false,
  seen: new Set(),
  retryTimer: null,
  retryDeadlineMs: null,
  refreshPending: false,
}};
function setTimeout(callback, delay) {{
  const id = nextTimerId++;
  timers.set(id, {{ callback, delay }});
  return id;
}}
function clearTimeout(id) {{ timers.delete(id); }}
function withActiveWorkoutAdaptationParams(path) {{ return path; }}
function workoutAdaptationIsRenderable() {{ return false; }}
function showWorkoutAdaptationNotice() {{}}
async function getDashboard() {{}}
function paintDashboardFromState() {{}}
async function getNextWorkout() {{ return {{ id: 'adapted-plan' }}; }}
async function renderNextWorkout() {{}}
async function api(path, opts = {{}}) {{
  calls.push({{ path, method: opts.method || null }});
  if (String(path).startsWith('/api/workout-adaptation-events/evaluate')) {{
    const evaluationIndex = calls.filter((call) => call.path.startsWith('/api/workout-adaptation-events/evaluate')).length;
    if (evaluationIndex === 1) return firstEvaluation;
    return {{ evaluated_count: 0, retry_after_ms: 180000 }};
  }}
  return {{ events: [] }};
}}
async function run() {{
  const first = fetchWorkoutAdaptationNotices();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.all([
    fetchWorkoutAdaptationNotices(),
    fetchWorkoutAdaptationNotices(),
    fetchWorkoutAdaptationNotices(),
  ]);
  releaseFirstEvaluation({{ evaluated_count: 0, retry_after_ms: null }});
  await first;
  return {{
    evaluationCalls: calls.filter((call) => call.path.startsWith('/api/workout-adaptation-events/evaluate')).length,
    feedCalls: calls.filter((call) => call.path.startsWith('/api/workout-adaptation-events?')).length,
    timerDelays: [...timers.values()].map((timer) => timer.delay),
    fetching: workoutAdaptationNoticeState.fetching,
    rerunPending: workoutAdaptationNoticeState.rerunPending,
  }};
}}
module.exports = {{ run }};
` + source;
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
    outputs = json.loads(result.stdout)

    assert outputs == {
        "evaluationCalls": 2,
        "feedCalls": 2,
        "timerDelays": [180_000],
        "fetching": False,
        "rerunPending": False,
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

    evaluation = outputs["evaluation"]
    assert evaluation["pathname"] == "/api/workout-adaptation-events/evaluate"
    assert evaluation["method"] == "POST"
    assert evaluation["active_workout_open"] == "true"
    assert evaluation["completed_sets"] == {"Chest Press": 2, "Squat": 1}

    notice = outputs["notice"]
    assert notice["pathname"] == "/api/workout-adaptation-events"
    assert notice["method"] is None
    assert notice["unacknowledged"] == "true"
    assert notice["limit"] == "10"

    assert outputs["evaluationFailurePaths"][0].startswith(
        "/api/workout-adaptation-events/evaluate?"
    )
    assert len(outputs["evaluationFailurePaths"]) == 1

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
        "function scheduleWorkoutAdaptationEvaluationRetry",
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
const WORKOUT_ADAPTATION_FAILURE_RETRY_MS = 60000;
const WORKOUT_ADAPTATION_IN_FLIGHT_RETRY_MS = 1000;
const calls = [];
let rendered = false;
let mergeCall = null;
let fetchedNextWorkout = false;
let failEvaluation = false;
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
const workoutAdaptationNoticeState = {{ fetching: false, seen: new Set(), retryTimer: null }};
function setTimeout() {{ return 1; }}
function clearTimeout() {{}}
function workoutAdaptationIsRenderable() {{ return false; }}
function showWorkoutAdaptationNotice() {{}}
async function api(path, opts = {{}}) {{
  calls.push({{ path, opts }});
  if (failEvaluation && String(path).startsWith('/api/workout-adaptation-events/evaluate')) {{
    throw new Error('evaluation unavailable');
  }}
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
    method: calls[index].opts.method || null,
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
  const evaluation = parseCall(0);
  const notice = parseCall(1);
  const nextWorkout = parseCall(2);
  calls.length = 0;
  failEvaluation = true;
  await fetchWorkoutAdaptationNotices();
  return {{
    evaluation,
    notice,
    nextWorkout,
    evaluationFailurePaths: calls.map((call) => call.path),
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
