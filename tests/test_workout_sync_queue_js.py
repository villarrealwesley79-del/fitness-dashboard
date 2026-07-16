"""Workout sync and active-draft runtime contracts."""

import json
from pathlib import Path

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static/js/app.js").read_text()
APP_LOADER_JS = (ROOT / "static/js/app-loader.js").read_text()
APP_SW = (ROOT / "static/js/sw.js").read_text()
APP_HTML = (ROOT / "templates/index.html").read_text()


def test_workout_queue_failure_statuses_and_user_reason_are_runtime_contracts():
    output = run_app_js(
        ["postCompleteWorkout", "annotateWorkoutSyncReason"],
        """
const responses = [
  new Response(JSON.stringify({ error: { message: 'login required' } }), { status: 401 }),
  new Response(JSON.stringify({ error: { message: 'server down' } }), { status: 503 }),
];
let index = 0;
sandbox.fetch = async () => responses[index++];
const auth = await e.postCompleteWorkout({ id: 'auth' });
const server = await e.postCompleteWorkout({ id: 'server' });
process.stdout.write(JSON.stringify({ statuses: [auth.syncStatus, server.syncStatus], reasons: [e.annotateWorkoutSyncReason(auth.reason, auth.syncStatus), e.annotateWorkoutSyncReason(server.reason, server.syncStatus)] }));
""",
    )
    assert output["statuses"] == ["auth_required", "pending"]
    assert "Sign in with the account that saved this workout" in output["reasons"][0]
    assert "retry automatically" in output["reasons"][1]


def test_workout_queue_flushes_and_renders_pending_and_auth_required_entries():
    output = run_app_js(
        ["flushSyncQueue", "renderSyncQueueModal"],
        """
const queue = [
  { client_workout_id: 'pending-1', last_status: 'pending', payload: { session_type: 'strength', date: '2026-07-15' } },
  { client_workout_id: 'auth-1', last_status: 'auth_required', payload: { session_type: 'cardio', date: '2026-07-14' } },
  { client_workout_id: 'rejected-1', last_status: 'rejected', payload: { session_type: 'strength', date: '2026-07-13' } },
];
sandbox.localStorage.getItem = (key) => key === 'fit51:sync-queue:v1' ? JSON.stringify(queue) : null;
const flushed = [];
sandbox.__fitSet.syncSingleEntry(async (id) => { flushed.push(id); return { ok: false, status: 'pending' }; });
sandbox.__fitSet.renderSyncQueueModal(() => {});
await e.flushSyncQueue();
const rows = [];
const host = { innerHTML: '', appendChild(row) { rows.push(row); }, querySelectorAll: () => [] };
sandbox.elements['sync-queue-list'] = host;
sandbox.document.createElement = () => ({ className: '', innerHTML: '' });
sandbox.__fitSet.listMealQueueEntries(async () => []);
await e.renderSyncQueueModal();
process.stdout.write(JSON.stringify({
  flushed,
  rendered: rows.map((row) => ({ className: row.className, retry: row.innerHTML.includes('data-sync-retry=') })),
}));
""",
        mocks=["syncSingleEntry", "renderSyncQueueModal", "listMealQueueEntries"],
    )
    assert output["flushed"] == ["pending-1", "auth-1"]
    rendered = {row["className"]: row["retry"] for row in output["rendered"]}
    assert rendered["sync-row sync-row-pending"] is True
    assert rendered["sync-row sync-row-auth_required"] is True


def test_active_workout_draft_background_save_syncs_live_inputs():
    output = run_app_js(
        ["saveActiveWorkoutDraftBeforePageHidden", "state"],
        """
const saved = [];
sandbox.localStorage.setItem = (key, value) => saved.push({ key, value: JSON.parse(value) });
const fields = {
  'input[data-field="weight"]': { value: '88' }, 'input[data-field="reps"]': { value: '12' },
  'input[data-field="done"]': { checked: true }, 'input[data-field="notes"]': { value: 'WHOOP switch proof' },
};
const row = { dataset: { ex: '0', set: '0' }, querySelector: (selector) => fields[selector] };
sandbox.elements['active-workout-body'] = { querySelectorAll: () => [row], querySelector: () => null };
sandbox.__fitSet.currentActiveWorkoutDraftScope(() => 'user:fit236');
e.state.activeWorkout = { id: 'workout-1', dirty: false, exercises: [{ exercise: 'Row', logged_sets: [{ weight: '66', reps: '20', done: false, notes: '' }] }] };
e.saveActiveWorkoutDraftBeforePageHidden();
process.stdout.write(JSON.stringify(saved[0]));
""",
        mocks=["currentActiveWorkoutDraftScope"],
    )
    assert output["key"] == "fit168:active-workout-draft:v1"
    assert output["value"]["auth_scope"] == "user:fit236"
    assert output["value"]["workout"]["dirty"] is True
    assert output["value"]["workout"]["exercises"][0]["logged_sets"][0] == {
        "weight": "88", "reps": "12", "done": True, "notes": "WHOOP switch proof",
    }


def test_active_workout_draft_restore_requires_matching_live_scope():
    output = run_app_js(
        ["restoreActiveWorkoutDraft", "state"],
        """
const draft = { version: 1, auth_scope: 'user:fit264', workout: { id: 'restored', exercises: [{ exercise: 'Row', logged_sets: [] }] } };
sandbox.localStorage.getItem = () => JSON.stringify(draft);
sandbox.__fitSet.currentActiveWorkoutDraftScope(() => 'user:other');
const calls = [];
sandbox.__fitSet.toast(() => calls.push('toast'));
e.restoreActiveWorkoutDraft();
process.stdout.write(JSON.stringify({ active: e.state.activeWorkout, calls }));
""",
        mocks=["currentActiveWorkoutDraftScope", "toast"],
    )
    assert output == {"active": None, "calls": []}


def test_active_workout_draft_restores_matching_scope_and_notifies_user():
    output = run_app_js(
        ["restoreActiveWorkoutDraft", "state"],
        """
const draft = { version: 1, auth_scope: 'user:fit264', saved_at: new Date().toISOString(), workout: { id: 'restored', exercises: [{ exercise: 'Row', logged_sets: [{ done: true }] }] } };
sandbox.localStorage.getItem = () => JSON.stringify(draft);
sandbox.__fitSet.currentActiveWorkoutDraftScope(() => 'user:fit264');
const calls = [];
sandbox.__fitSet.renderActiveWorkout(() => calls.push('render'));
sandbox.__fitSet.toast((message) => calls.push(message));
const restored = e.restoreActiveWorkoutDraft();
process.stdout.write(JSON.stringify({ restored, active: e.state.activeWorkout, calls }));
""",
        mocks=["currentActiveWorkoutDraftScope", "renderActiveWorkout", "toast"],
    )
    assert output["restored"] is True
    assert output["active"]["id"] == "restored"
    assert output["active"]["exercises"][0]["logged_sets"][0]["done"] is True
    assert output["active"]["saveState"] == {
        "message": "Recovered unsaved workout details from this device.",
        "variant": "warn",
    }
    assert output["calls"] == ["render", "Recovered unsaved workout details"]


def test_frontend_asset_versions_stay_in_sync():
    # Stable deployment/cache constants are intentionally source contracts.
    assert "/static/js/app-loader.js?v=20260713-fit233-adaptation-polling" in APP_HTML
    assert "/static/js/app.js?v=20260713-fit233-adaptation-polling" in APP_LOADER_JS
    assert "const CACHE_NAME = 'fitness-dashboard-v20260713-fit233-adaptation-polling';" in APP_SW
    assert "const ACTIVE_WORKOUT_DRAFT_KEY = 'fit168:active-workout-draft:v1';" in APP_JS
    assert "const ACTIVE_WORKOUT_DRAFT_VERSION = 1;" in APP_JS
