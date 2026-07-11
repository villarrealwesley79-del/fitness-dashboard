"""Executable replacements for FIT-264 frontend source-string contracts."""

from js_runtime import run_app_js


def test_vitals_render_clears_stale_hr_zone_when_heart_rate_is_missing():
    output = run_app_js(
        ["renderVitals"],
        """
const ids = [
  'v-rhr', 'v-hrv', 'v-hr-zone', 'v-hr-zone-sub', 'v-temp', 'v-temp-delta',
  'v-steps', 'v-steps-goal', 'v-active-cal', 'v-active-cal-goal', 'v-total-cal',
  'v-total-cal-goal', 'v-active-min', 'v-active-min-goal', 'spark-steps',
  'spark-active-min', 'spark-sleep', 'v-sleep-dur', 'v-sleep-dur-sub',
  'v-sleep-score', 'v-sleep-score-sub', 'v-weight', 'v-bf', 'v-weight-delta',
  'v-bf-delta', 'v-rhr-delta', 'v-hrv-delta',
];
ids.forEach((id) => { sandbox.elements[id] = { textContent: 'stale', className: '' }; });
sandbox.__fitSet.getVitals(async () => ({}));
sandbox.__fitSet.getOuraStatus(async () => ({}));
sandbox.__fitSet.getOuraSleep(async () => ({}));
sandbox.__fitSet.getBody(async () => ({ history: [] }));
sandbox.__fitSet.getOuraTrends(async () => ({ series: [] }));
sandbox.__fitSet.sparkline(() => {});
await e.renderVitals();
process.stdout.write(JSON.stringify({
  zone: sandbox.elements['v-hr-zone'].textContent,
  subtitle: sandbox.elements['v-hr-zone-sub'].textContent,
}));
""",
        mocks=[
            "getVitals", "getOuraStatus", "getOuraSleep", "getBody", "getOuraTrends",
            "sparkline",
        ],
    )

    assert output == {"zone": "--", "subtitle": ""}


def test_meal_refresh_request_ids_use_runtime_uuid_when_available():
    output = run_app_js(
        ["mealV2GenerateRequestId"],
        """
sandbox.crypto = { randomUUID: () => 'runtime-uuid' };
process.stdout.write(JSON.stringify(e.mealV2GenerateRequestId()));
""",
    )

    assert output == "runtime-uuid"


def test_api_merges_caller_headers_with_csrf_and_same_origin_credentials():
    output = run_app_js(
        ["api"],
        """
let captured;
sandbox.fetch = async (path, options) => {
  captured = { path, options };
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
};
await e.api('/api/proof', { method: 'POST', headers: { 'X-Proof': 'yes' } });
process.stdout.write(JSON.stringify(captured));
""",
    )

    assert output["path"] == "/api/proof"
    assert output["options"]["method"] == "POST"
    assert output["options"]["credentials"] == "same-origin"
    assert output["options"]["headers"]["X-Proof"] == "yes"
    assert output["options"]["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_equipment_update_clears_all_recommendation_caches_before_rerender():
    output = run_app_js(
        ["updateEquipment", "state"],
        """
e.state.settings = { cached: true };
e.state.dashboard = { cached: true };
e.state.nextWorkout = { cached: true };
e.state.reco = { cached: true };
e.state.activeWorkout = { cached: true };
const calls = [];
sandbox.__fitSet.api(async () => ({}));
sandbox.__fitSet.toast(() => calls.push('toast'));
sandbox.__fitSet.clearActiveWorkoutDraft(() => calls.push('clear-draft'));
sandbox.__fitSet.renderSettings(async () => calls.push('settings'));
sandbox.__fitSet.renderDashboard(async () => calls.push('dashboard'));
await e.updateEquipment('full_gym');
process.stdout.write(JSON.stringify({ state: e.state, calls }));
""",
        mocks=["api", "toast", "clearActiveWorkoutDraft", "renderSettings", "renderDashboard"],
    )

    assert output["state"]["settings"] is None
    assert output["state"]["dashboard"] is None
    assert output["state"]["nextWorkout"] is None
    assert output["state"]["reco"] is None
    assert output["state"]["activeWorkout"] is None
    assert output["calls"] == ["toast", "clear-draft", "settings", "dashboard"]


def test_swap_and_adjust_responses_update_both_recommendation_caches():
    output = run_app_js(
        ["_finalizeSwap", "renderAdjustResult", "state"],
        """
const calls = [];
sandbox.elements['modal-swap'] = { hidden: false };
['adjust-state', 'adjust-result', 'adjust-summary', 'adjust-notes', 'adjust-meta'].forEach((id) => {
  sandbox.elements[id] = { textContent: '', innerHTML: '', className: '', hidden: true };
});
sandbox.__fitSet.closeModal(() => {});
sandbox.__fitSet.toast(() => {});
sandbox.__fitSet.renderNextWorkout(() => calls.push('render-next'));
sandbox.__fitSet.renderAdjustedPlanPreview(() => calls.push('render-adjusted'));
const swapped = { id: 'swap-plan' };
e._finalizeSwap({ recommendation: swapped }, 'Old', 'New');
const swapState = {
  dashboard: e.state.dashboard.next_workout.id,
  next: e.state.nextWorkout.id,
};
const adjusted = { id: 'adjust-plan' };
e.renderAdjustResult({ result_kind: 'changed', recommendation: adjusted, applied_notes: [] });
process.stdout.write(JSON.stringify({
  swapState,
  adjustState: {
    dashboard: e.state.dashboard.next_workout.id,
    next: e.state.nextWorkout.id,
    adjusted: e.state.adjustedWorkout.id,
  },
  calls,
}));
""",
        mocks=["closeModal", "toast", "renderNextWorkout", "renderAdjustedPlanPreview"],
    )

    assert output["swapState"] == {"dashboard": "swap-plan", "next": "swap-plan"}
    assert output["adjustState"] == {
        "dashboard": "adjust-plan",
        "next": "adjust-plan",
        "adjusted": "adjust-plan",
    }
    assert output["calls"] == ["render-next", "render-adjusted"]


def test_active_workout_mutations_persist_and_cancel_clears_draft():
    output = run_app_js(
        ["updateLoggedSetFromRow", "updateActiveCardio", "removeActiveExercise", "cancelActiveWorkout", "state"],
        """
const calls = [];
sandbox.__fitSet.saveActiveWorkoutDraft(() => calls.push('save'));
sandbox.__fitSet.renderActiveWorkout(() => calls.push('render'));
sandbox.__fitSet.toast(() => calls.push('toast'));
sandbox.__fitSet.clearActiveWorkoutDraft(() => calls.push('clear-draft'));
sandbox.__fitSet.clearAdjustIntent(() => calls.push('clear-adjust'));
e.state.activeWorkout = {
  exercises: [{ name: 'Row', logged_sets: [{ weight: '', reps: '', done: false, notes: '' }] }],
  cardio: { completed: false, activity_type: '', duration_minutes: '', notes: '' },
  dirty: false,
};
const setFields = {
  'input[data-field="weight"]': { value: '80' },
  'input[data-field="reps"]': { value: '10' },
  'input[data-field="done"]': { checked: true },
  'input[data-field="notes"]': { value: 'clean' },
};
e.updateLoggedSetFromRow({ dataset: { ex: '0', set: '0' }, querySelector: (s) => setFields[s] });
const cardioFields = {
  'input[data-cardio-field="completed"]': { checked: true },
  'input[data-cardio-field="activity_type"]': { value: 'Bike' },
  'input[data-cardio-field="duration_minutes"]': { value: '20' },
  'textarea[data-cardio-field="notes"]': { value: 'steady' },
};
const cardioCard = { querySelector: (s) => cardioFields[s] };
sandbox.elements['active-workout-body'] = { querySelector: (s) => s === '.active-cardio' ? cardioCard : null };
e.updateActiveCardio();
e.removeActiveExercise(0, 'Row');
sandbox.elements['modal-active'] = { hidden: false };
e.cancelActiveWorkout();
process.stdout.write(JSON.stringify({ calls, activeWorkout: e.state.activeWorkout }));
""",
        mocks=[
            "saveActiveWorkoutDraft", "renderActiveWorkout", "toast",
            "clearActiveWorkoutDraft", "clearAdjustIntent",
        ],
    )

    assert output["calls"] == [
        "save", "save", "render", "save", "toast", "clear-draft", "clear-adjust"
    ]
    assert output["activeWorkout"] is None


def test_offline_workout_completion_queues_then_clears_draft():
    output = run_app_js(
        ["completeWorkout", "state"],
        """
const calls = [];
const fields = {
  'input[data-field="reps"]': { value: '8' },
  'input[data-field="weight"]': { value: '100' },
  'input[data-field="done"]': { checked: true },
  'input[data-field="notes"]': { value: '' },
};
const row = { querySelector: (s) => fields[s] };
sandbox.elements['active-workout-body'] = { querySelectorAll: () => [row] };
sandbox.elements['btn-complete-workout'] = { disabled: false, textContent: '' };
sandbox.elements['modal-active'] = { hidden: false };
e.state.activeWorkout = {
  id: 'workout-1', focus: 'strength', recommendation_id: 'rec-1',
  exercises: [{ exercise: 'Squat', muscle_group: 'legs', logged_sets: [{}] }],
  cardio: null,
};
sandbox.navigator.onLine = false;
sandbox.__fitSet.setActiveWorkoutStatus(() => {});
sandbox.__fitSet.saveActiveWorkoutDraft(() => calls.push('save'));
sandbox.__fitSet.enqueueOfflineWorkout(() => calls.push('enqueue'));
sandbox.__fitSet.clearActiveWorkoutDraft(() => calls.push('clear-draft'));
sandbox.__fitSet.clearAdjustIntent(() => calls.push('clear-adjust'));
sandbox.__fitSet.invalidateCaches(() => calls.push('invalidate'));
sandbox.__fitSet.loadTab(() => calls.push('load-tab'));
sandbox.__fitSet.openWorkoutSavedConfirm(() => calls.push('confirm'));
sandbox.__fitSet.toast(() => calls.push('toast'));
await e.completeWorkout();
process.stdout.write(JSON.stringify({ calls, activeWorkout: e.state.activeWorkout }));
""",
        mocks=[
            "setActiveWorkoutStatus", "saveActiveWorkoutDraft", "enqueueOfflineWorkout",
            "clearActiveWorkoutDraft", "clearAdjustIntent", "invalidateCaches", "loadTab",
            "openWorkoutSavedConfirm", "toast",
        ],
    )

    assert output["calls"] == [
        "save", "enqueue", "clear-draft", "clear-adjust", "invalidate", "load-tab", "confirm", "toast"
    ]
    assert output["activeWorkout"] is None


def test_server_failure_workout_completion_stays_retryable():
    output = run_app_js(
        ["completeWorkout", "state"],
        """
const calls = [];
const fields = {
  'input[data-field="reps"]': { value: '8' },
  'input[data-field="weight"]': { value: '100' },
  'input[data-field="done"]': { checked: true },
  'input[data-field="notes"]': { value: '' },
};
const row = { querySelector: (s) => fields[s] };
sandbox.elements['active-workout-body'] = { querySelectorAll: () => [row] };
sandbox.elements['btn-complete-workout'] = { disabled: false, textContent: '' };
sandbox.elements['modal-active'] = { hidden: false };
e.state.activeWorkout = {
  id: 'workout-server-failure', focus: 'strength', recommendation_id: 'rec-1',
  exercises: [{ exercise: 'Squat', muscle_group: 'legs', logged_sets: [{}] }],
  cardio: null,
};
sandbox.__fitSet.postCompleteWorkout(async () => ({
  ok: false, status: 503, syncStatus: 'pending', reason: 'unavailable',
}));
sandbox.__fitSet.setActiveWorkoutStatus(() => {});
sandbox.__fitSet.saveActiveWorkoutDraft(() => calls.push('save'));
sandbox.__fitSet.enqueueOfflineWorkout((_payload, status) => calls.push(`enqueue:${status}`));
sandbox.__fitSet.clearActiveWorkoutDraft(() => calls.push('clear-draft'));
sandbox.__fitSet.clearAdjustIntent(() => {});
sandbox.__fitSet.invalidateCaches(() => {});
sandbox.__fitSet.loadTab(() => {});
sandbox.__fitSet.openWorkoutSavedConfirm(() => {});
sandbox.__fitSet.toast(() => {});
await e.completeWorkout();
process.stdout.write(JSON.stringify({ calls, activeWorkout: e.state.activeWorkout }));
""",
        mocks=[
            "postCompleteWorkout", "setActiveWorkoutStatus", "saveActiveWorkoutDraft",
            "enqueueOfflineWorkout", "clearActiveWorkoutDraft", "clearAdjustIntent",
            "invalidateCaches", "loadTab", "openWorkoutSavedConfirm", "toast",
        ],
    )

    assert output == {
        "calls": ["save", "enqueue:pending", "clear-draft"],
        "activeWorkout": None,
    }


def test_auth_required_workout_save_keeps_review_state_and_clears_local_draft():
    output = run_app_js(
        ["completeWorkout", "state"],
        """
const calls = [];
const fields = {
  'input[data-field="reps"]': { value: '8' },
  'input[data-field="weight"]': { value: '100' },
  'input[data-field="done"]': { checked: true },
  'input[data-field="notes"]': { value: '' },
};
const row = { querySelector: (s) => fields[s] };
sandbox.elements['active-workout-body'] = { querySelectorAll: () => [row] };
sandbox.elements['btn-complete-workout'] = { disabled: false, textContent: '' };
e.state.activeWorkout = {
  id: 'workout-auth-required', focus: 'strength', recommendation_id: 'rec-1',
  exercises: [{ exercise: 'Squat', muscle_group: 'legs', logged_sets: [{}] }],
  cardio: null,
};
sandbox.__fitSet.postCompleteWorkout(async () => ({
  ok: false, status: 401, syncStatus: 'auth_required', reason: 'sign in', body: {},
}));
sandbox.__fitSet.setActiveWorkoutStatus(() => {});
sandbox.__fitSet.saveActiveWorkoutDraft(() => calls.push('save'));
sandbox.__fitSet.enqueueOfflineWorkout((_payload, status) => calls.push(`enqueue:${status}`));
sandbox.__fitSet.updateQueueEntry(() => calls.push('update-queue'));
sandbox.__fitSet.clearActiveWorkoutDraft(() => calls.push('clear-draft'));
sandbox.__fitSet.toast(() => {});
await e.completeWorkout();
process.stdout.write(JSON.stringify({
  calls,
  queuedForSyncReview: e.state.activeWorkout.queuedForSyncReview,
  saveState: e.state.activeWorkout.saveState,
}));
""",
        mocks=[
            "postCompleteWorkout", "setActiveWorkoutStatus", "saveActiveWorkoutDraft",
            "enqueueOfflineWorkout", "updateQueueEntry", "clearActiveWorkoutDraft", "toast",
        ],
    )

    assert output["calls"] == ["save", "enqueue:auth_required", "update-queue", "clear-draft"]
    assert output["queuedForSyncReview"] is True
    assert output["saveState"] == {
        "message": "Sign in, then retry the workout from the sync queue.",
        "variant": "err",
    }


def test_active_workout_draft_save_defers_until_auth_scope_settles():
    output = run_app_js(
        ["saveActiveWorkoutDraft", "settleActiveWorkoutDraftAfterAuthScope", "state"],
        """
const saved = [];
sandbox.localStorage.setItem = (key, value) => saved.push({ key, value: JSON.parse(value) });
e.state.activeWorkout = { id: 'deferred-workout', exercises: [], queuedForSyncReview: false };
sandbox.__fitSet.activeWorkoutDraftScopeForWorkout(() => '');
e.saveActiveWorkoutDraft({ syncDom: false });
sandbox.__fitSet.activeWorkoutDraftScopeForWorkout(() => 'user:owner');
sandbox.__fitSet.clearMealQueueAuthScopeRetry(() => {});
sandbox.__fitSet.restoreActiveWorkoutDraft(() => {});
e.settleActiveWorkoutDraftAfterAuthScope({ ok: true });
process.stdout.write(JSON.stringify(saved));
""",
        mocks=[
            "activeWorkoutDraftScopeForWorkout", "clearMealQueueAuthScopeRetry",
            "restoreActiveWorkoutDraft",
        ],
    )

    assert len(output) == 1
    assert output[0]["key"] == "fit168:active-workout-draft:v1"
    assert output[0]["value"]["auth_scope"] == "user:owner"
    assert output[0]["value"]["workout"]["id"] == "deferred-workout"


def test_whoop_disconnect_invalidates_dashboard_and_rerenders_settings():
    output = run_app_js(
        ["disconnectWhoop", "state"],
        """
e.state.dashboard = { cached: true };
e.state.reco = { cached: true };
e.state.whoopStatus = { connected: true };
const calls = [];
sandbox.__fitSet.api(async () => ({}));
sandbox.__fitSet.renderWhoopFreshnessDetail(() => {});
sandbox.__fitSet.toast(() => calls.push('toast'));
sandbox.__fitSet.renderSettings(async () => calls.push('settings'));
await e.disconnectWhoop();
process.stdout.write(JSON.stringify({ state: e.state, calls }));
""",
        mocks=["api", "renderWhoopFreshnessDetail", "toast", "renderSettings"],
    )

    assert output["state"]["dashboard"] is None
    assert output["state"]["reco"] is None
    assert output["state"]["whoopStatus"]["connected"] is False
    assert output["calls"] == ["toast", "settings"]


def test_auth_scope_settlement_restores_then_flushes_draft():
    output = run_app_js(
        ["settleActiveWorkoutDraftAfterAuthScope"],
        """
const calls = [];
sandbox.__fitSet.clearMealQueueAuthScopeRetry(() => calls.push('clear-retry'));
sandbox.__fitSet.restoreActiveWorkoutDraft(() => calls.push('restore'));
sandbox.__fitSet.flushPendingActiveWorkoutDraftSave(() => calls.push('flush'));
e.settleActiveWorkoutDraftAfterAuthScope({ ok: true });
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["clearMealQueueAuthScopeRetry", "restoreActiveWorkoutDraft", "flushPendingActiveWorkoutDraftSave"],
    )

    assert output == ["clear-retry", "restore", "flush"]


def test_meal_undo_clears_status_before_toast_and_always_refreshes_macros():
    output = run_app_js(
        ["postMealUndo"],
        """
const calls = [];
sandbox.__fitSet.api(async () => calls.push('delete'));
sandbox.__fitSet.clearMealComposerStatus(() => calls.push('clear-status'));
sandbox.__fitSet.toast(() => calls.push('toast'));
sandbox.__fitSet.refreshMacroCard(() => calls.push('refresh'));
await e.postMealUndo('meal-1');
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["api", "clearMealComposerStatus", "toast", "refreshMacroCard"],
    )

    assert output == ["delete", "clear-status", "toast", "refresh"]


def test_pending_meal_review_renders_warning_before_macro_refresh():
    output = run_app_js(
        ["handleMealIntakeResponse"],
        """
const calls = [];
sandbox.__fitSet.isMealV2Payload(() => false);
sandbox.__fitSet.upsertMealPendingEntry(() => calls.push('upsert'));
sandbox.__fitSet.clearMealComposerInputs(() => calls.push('clear-inputs'));
sandbox.__fitSet.clearMealDraft(() => calls.push('clear-draft'));
sandbox.__fitSet.clearMealComposerStatus(() => calls.push('clear-status'));
sandbox.__fitSet.renderMealPendingList(() => calls.push('render'));
sandbox.__fitSet.toast(() => calls.push('toast'));
sandbox.__fitSet.refreshMacroCard(() => calls.push('refresh'));
e.handleMealIntakeResponse({ status: 'pending_review', estimate: {} }, {
  clientId: 'meal-1', imageFiles: [], localTime: {},
});
process.stdout.write(JSON.stringify(calls));
""",
        mocks=[
            "isMealV2Payload", "upsertMealPendingEntry", "clearMealComposerInputs",
            "clearMealDraft", "clearMealComposerStatus", "renderMealPendingList",
            "toast", "refreshMacroCard",
        ],
    )

    assert output == [
        "upsert", "clear-inputs", "clear-draft", "clear-status", "render", "toast", "refresh"
    ]
