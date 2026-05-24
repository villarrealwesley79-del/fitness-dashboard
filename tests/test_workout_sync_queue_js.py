from pathlib import Path


APP_JS = Path("static/js/app.js").read_text()
APP_SW = Path("static/js/sw.js").read_text()
APP_HTML = Path("templates/index.html").read_text()


def _app_js_block(start: str, end: str) -> str:
    start_idx = APP_JS.index(start)
    end_idx = APP_JS.index(end, start_idx)
    return APP_JS[start_idx:end_idx]


def test_workout_queue_auth_and_server_failures_remain_retryable():
    assert "const WORKOUT_QUEUE_RETRYABLE_STATUSES = new Set(['pending', 'auth_required']);" in APP_JS
    assert "if (res.status === 401 || res.status === 403) syncStatus = 'auth_required';" in APP_JS
    assert "else if (res.status >= 500) syncStatus = 'pending';" in APP_JS
    assert ".filter((e) => WORKOUT_QUEUE_RETRYABLE_STATUSES.has(e.last_status || 'pending'))" in APP_JS
    assert "if (result.syncStatus === 'pending') {\n                    settleQueued('Server unavailable" in APP_JS


def test_workout_queue_surfaces_recoverable_failures_to_user():
    assert "function annotateWorkoutSyncReason(rawReason, syncStatus)" in APP_JS
    assert "Sign in with the account that saved this workout, then retry." in APP_JS
    assert "auth_required: 'Sign-in needed'" in APP_JS
    assert "['rejected', 'conflicted', 'auth_required'].includes(entry.last_status)" in APP_JS
    assert "Sign in, then retry the workout from the sync queue." in APP_JS
    assert "const pending = queue.filter((e) => (e.last_status || 'pending') === 'pending').length" in APP_JS


def test_active_workout_draft_uses_versioned_scoped_localstorage_wrapper():
    block = _app_js_block("const ACTIVE_WORKOUT_DRAFT_KEY", "function buildLoggedSets")

    assert "const ACTIVE_WORKOUT_DRAFT_KEY = 'fit168:active-workout-draft:v1';" in block
    assert "const ACTIVE_WORKOUT_DRAFT_VERSION = 1;" in block
    assert "function currentActiveWorkoutDraftScope()" in block
    assert "return String(cachedMealQueueAuthScope()).trim();" in block
    assert "version: ACTIVE_WORKOUT_DRAFT_VERSION" in block
    assert "auth_scope: currentActiveWorkoutDraftScope()" in block
    assert "JSON.stringify(draft)" in block
    assert "parsed.version === ACTIVE_WORKOUT_DRAFT_VERSION" in block
    assert "Array.isArray(workout.exercises)" in block
    assert "localStorage.removeItem(ACTIVE_WORKOUT_DRAFT_KEY)" in block


def test_active_workout_draft_restore_rejects_detectable_scope_mismatch():
    block = _app_js_block("function restoreActiveWorkoutDraft()", "function buildLoggedSets")

    assert "const draftScope = String(draft.auth_scope || '').trim();" in block
    assert "const currentScope = currentActiveWorkoutDraftScope();" in block
    assert "if (draftScope && currentScope && draftScope !== currentScope)" in block
    assert "Recovered unsaved workout details from this device." in block
    assert "renderActiveWorkout();" in block
    assert "toast('Recovered unsaved workout details')" in block


def test_active_workout_draft_persists_all_mutation_paths():
    recommendation_block = _app_js_block(
        "function setActiveWorkoutFromRecommendation",
        "function hasRecommendedCardio",
    )
    set_block = _app_js_block("function updateLoggedSetFromRow", "function countActiveWorkoutProgress")
    cardio_block = _app_js_block("function updateActiveCardio", "function renderActiveWorkout")
    remove_block = _app_js_block("function removeActiveExercise", "async function startWorkout")
    swap_block = _app_js_block("function _finalizeSwap", "function clearAdjustIntent")

    assert "saveActiveWorkoutDraft();" in recommendation_block
    assert "state.activeWorkout.dirty = true;\n        saveActiveWorkoutDraft();" in set_block
    assert "state.activeWorkout.dirty = true;\n        saveActiveWorkoutDraft();" in cardio_block
    assert "renderActiveWorkout();\n        saveActiveWorkoutDraft();" in remove_block
    assert "saveActiveWorkoutDraft();" in swap_block
    assert "if (state.swapContext && state.swapContext.source === 'active')" in swap_block
    assert "if (ctx.source === 'active')" in swap_block


def test_active_workout_draft_clear_points_are_explicit_and_error_states_are_preserved():
    equipment_block = _app_js_block("async function updateEquipment", "function recommendedRepsValue")
    cancel_block = _app_js_block("function cancelActiveWorkout", "function wireActiveWorkoutGuards")
    complete_block = _app_js_block("async function completeWorkout()", "function openWorkoutSavedConfirm")

    assert "state.activeWorkout = null;\n            clearActiveWorkoutDraft();" in equipment_block
    assert "state.activeWorkout = null;\n        clearActiveWorkoutDraft();" in cancel_block
    assert "enqueueOfflineWorkout(completePayload, 'pending');\n            clearActiveWorkoutDraft();" in complete_block
    assert "state.activeWorkout = null;\n                clearActiveWorkoutDraft();" in complete_block
    assert "Validation failed: log at least one set before completing this workout." in complete_block
    assert "aw.saveState = { message, variant: 'err' };\n            saveActiveWorkoutDraft();" in complete_block
    assert "aw.saveState = { message: msg, variant: 'err' };" in complete_block
    assert "setActiveWorkoutStatus(msg, 'err');\n                saveActiveWorkoutDraft();" in complete_block


def test_active_workout_draft_restores_after_auth_scope_refresh_and_cache_busts_js():
    boot_block = _app_js_block("function boot()", "function registerServiceWorker")

    assert ".finally(() => restoreActiveWorkoutDraft());" in boot_block
    assert "/static/js/app.js?v=20260524-fit168-active-draft-v1" in APP_HTML
    assert "const CACHE_NAME = 'fitness-dashboard-v20260524-fit168-active-draft';" in APP_SW
