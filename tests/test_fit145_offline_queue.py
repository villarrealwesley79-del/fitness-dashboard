"""FIT-145 static guards for the browser-only offline meal queue.

The app does not have a JavaScript test runner, so these tests follow the
existing source-contract pattern used by nearby meal review UI tests.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
APP_SW = (ROOT / "static" / "js" / "sw.js").read_text(encoding="utf-8")
PRIVACY_DOC = (ROOT / "docs" / "FOOD_PHOTO_PRIVACY.md").read_text(encoding="utf-8")
APP_PY = (ROOT / "app.py").read_text(encoding="utf-8")


def _fit145_block() -> str:
    start = APP_JS.find("// FIT-145: meal-intake offline queue.")
    end = APP_JS.find("function completeWorkout()", start)
    assert start != -1 and end != -1, "FIT-145 queue block markers not found"
    return APP_JS[start:end]


def _fit145_storage_block() -> str:
    start = APP_JS.find("function mealQueueRequest(")
    end = APP_JS.find("function renderSyncBanner()", start)
    assert start != -1 and end != -1, "FIT-145 storage block markers not found"
    return APP_JS[start:end]


def _app_js_block(start_marker: str, end_marker: str) -> str:
    start = APP_JS.find(start_marker)
    end = APP_JS.find(end_marker, start)
    assert start != -1 and end != -1, f"block markers not found: {start_marker}"
    return APP_JS[start:end]


def test_meal_queue_uses_versioned_indexeddb_schema():
    block = _fit145_storage_block()

    assert "const MEAL_QUEUE_DB_NAME = 'fitMealIntakeQueueDB';" in APP_JS
    assert "const MEAL_QUEUE_DB_VERSION = 1;" in APP_JS
    assert "const MEAL_QUEUE_STORE = 'queued_meals';" in APP_JS
    assert "const MEAL_PHOTO_STORE = 'meal_photos';" in APP_JS
    assert "db.createObjectStore(MEAL_QUEUE_STORE, { keyPath: 'client_id' });" in block
    assert "db.createObjectStore(MEAL_PHOTO_STORE, { keyPath: 'photo_id' });" in block
    assert "photoStore.createIndex('client_id', 'client_id', { unique: false });" in block


def test_meal_queue_stores_blobs_in_indexeddb_not_localstorage():
    start = APP_JS.find("async function enqueueMealIntakeOffline")
    end = APP_JS.find("async function updateMealQueueEntry", start)
    assert start != -1 and end != -1, "FIT-145 enqueue block markers not found"
    block = APP_JS[start:end]

    assert "blob:" in block
    assert "file.slice(0, file.size, type)" in block
    assert "photoStore.put(photo)" in block
    assert "localStorage" not in block
    assert "readAsDataURL" not in block
    assert "base64" not in block.lower()
    assert "file.name" not in block


def test_offline_submit_enqueues_with_original_client_id_and_timestamps():
    assert "submit.textContent = online ? 'Log' : 'Save offline';" in APP_JS
    assert "await enqueueMealIntakeOffline({ textValue, files, clientId, localTime });" in APP_JS
    assert "form.append('client_id', entry.client_id);" in APP_JS
    assert "form.append('local_timestamp', entry.local_timestamp);" in APP_JS
    assert "form.append('local_date', entry.local_date);" in APP_JS
    assert "form.append('local_iso', entry.local_iso);" in APP_JS
    assert "return render_template('index.html')" in APP_PY
    assert "data-auth-scope" not in INDEX_HTML
    assert "const MEAL_QUEUE_AUTH_SCOPE_KEY = 'fit145:meal-queue-auth-scope:v1';" in APP_JS
    assert "let _mealQueueAuthScope = '';" in APP_JS
    assert "localStorage.setItem(MEAL_QUEUE_AUTH_SCOPE_KEY, normalized);" in APP_JS
    assert "persistedMealQueueAuthScope()" in APP_JS
    assert "refreshMealQueueAuthScope().catch" in APP_JS
    assert "auth_scope: authScope" in APP_JS
    assert "const authGate = await mealQueueAuthGate(entry);" in APP_JS


def test_meal_queue_replays_existing_meal_intake_form_contract():
    block = _fit145_block()

    assert "fetch('/api/meal-intake'" in block
    assert "form.append('images', photo.blob, `meal-${idx + 1}.${extension}`);" in block
    assert "headers: { 'Accept': 'application/json' }" in block
    assert "credentials: 'same-origin'" in block
    assert "syncStatus = 'conflicted';" in block
    assert "syncStatus = 'pending';" in block


def test_auth_failures_stay_visible_retryable_and_do_not_post_on_scope_mismatch():
    block = _fit145_block()

    assert "@app.route('/api/auth/scope')" in APP_PY
    assert 'return jsonify({"auth_scope": f"user:{_current_data_user_id()}"})' in APP_PY
    assert "fetch('/api/auth/scope'" in block
    assert "const MEAL_QUEUE_RETRYABLE_STATUSES = new Set(['pending', 'auth_required']);" in APP_JS
    assert "res.status === 401 || res.status === 403" in block
    assert "syncStatus = 'auth_required';" in block
    assert "status: 'pending'" in block
    assert "Could not verify the current sign-in before syncing this meal." in block
    assert "MEAL_QUEUE_RETRYABLE_STATUSES.has(e.last_status || 'pending')" in block
    assert "await updateMealQueueEntry(clientId, {" in block
    assert "last_status: authStatus" in block
    assert "return { ok: false, status: authStatus };" in block
    assert "const latestQueued = await getQueuedMealWithPhotos(clientId);" in block
    assert "if (!latestQueued.entry)" in block
    assert "const result = await postQueuedMealIntake(latestQueued.entry, latestQueued.photos);" in block
    auth_gate_idx = block.find("const authGate = await mealQueueAuthGate(entry);")
    post_idx = block.find("const result = await postQueuedMealIntake(latestQueued.entry, latestQueued.photos);")
    assert auth_gate_idx < post_idx
    assert "auth_required: 'Sign-in needed'" in block
    assert "sync-status-auth_required" in APP_CSS


def test_service_worker_offline_auth_scope_fallback_stays_retryable():
    block = _app_js_block(
        "async function fetchCurrentMealQueueAuthScope()",
        "async function refreshMealQueueAuthScope()",
    )
    missing_scope_block = _app_js_block("if (!scope) {", "return { ok: true, scope };")

    assert "JSON.stringify({ error: 'Offline' })" in APP_SW
    assert "const scope = String(body && body.auth_scope || '').trim();" in block
    assert "status: 'pending'" in missing_scope_block
    assert "Could not verify the current sign-in before syncing this meal." in missing_scope_block
    assert "status: 'auth_required'" not in missing_scope_block


def test_meal_sync_rechecks_queue_and_blocks_discard_while_uploading():
    sync_block = _app_js_block(
        "async function syncSingleMealQueueEntry(clientId)",
        "async function flushMealSyncQueue()",
    )
    modal_block = _app_js_block(
        "async function renderSyncQueueModal()",
        "async function completeWorkout()",
    )
    discard_block = _app_js_block(
        "host.querySelectorAll('[data-meal-sync-discard]')",
        "async function completeWorkout()",
    )

    assert "const latestQueued = await getQueuedMealWithPhotos(clientId);" in sync_block
    assert "if (!latestQueued.entry)" in sync_block
    assert "return { ok: false, status: 'discarded' };" in sync_block
    assert "const result = await postQueuedMealIntake(latestQueued.entry, latestQueued.photos);" in sync_block

    assert "const inFlight = _mealSyncInFlightClientIds.has(entry.client_id);" in modal_block
    assert "data-meal-sync-discard" in modal_block
    assert "data-meal-sync-retry" in modal_block
    assert 'disabled aria-disabled="true"' in modal_block

    assert "if (_mealSyncInFlightClientIds.has(clientId))" in discard_block
    assert "Meal is syncing" in discard_block
    assert discard_block.count("if (_mealSyncInFlightClientIds.has(clientId))") >= 2


def test_success_and_discard_delete_queue_entry_and_photo_bytes():
    block = _fit145_block()

    assert "async function removeMealQueueEntry(clientId)" in block
    assert "photoIds.forEach((photoId) => photoStore.delete(photoId));" in block
    assert "mealStore.delete(clientId);" in block
    assert "await removeMealQueueEntry(clientId);" in block
    assert "last_status: 'eviction_failed'" in block
    assert "data-meal-sync-discard" in block
    assert "_mealSyncInFlightClientIds.has(entry.client_id)" in block
    assert "_mealSyncInFlightClientIds.has(clientId)" in block
    assert "Meal is syncing" in block


def test_meal_flush_has_duplicate_guards_and_online_hooks():
    assert "let _mealSyncFlushInFlight = false;" in APP_JS
    assert "const _mealSyncInFlightClientIds = new Set();" in APP_JS
    assert "if (!navigator.onLine || _mealSyncFlushInFlight) return;" in APP_JS
    assert "_mealSyncInFlightClientIds.has(clientId)" in APP_JS
    assert "window.addEventListener('online', () => {" in APP_JS
    assert "flushMealSyncQueue();" in APP_JS
    assert "const CACHE_NAME = 'fitness-dashboard-v20260524-fit168-active-draft';" in APP_SW
    assert "sync" not in APP_SW.lower()


def test_sync_ui_includes_meal_retry_discard_and_privacy_copy():
    assert "Meals and workouts saved on this device while offline" in INDEX_HTML
    assert "clears any offline meal photos" in INDEX_HTML
    assert "data-meal-sync-retry" in APP_JS
    assert "data-meal-sync-discard" in APP_JS
    assert "Meal · ${escapeHtml(titleText)}" in APP_JS
    assert "sync-row-meal" in APP_CSS
    assert "sync-status-eviction_failed" in APP_CSS


def test_privacy_doc_documents_temporary_indexeddb_carveout():
    assert "Temporary Offline Queue Storage" in PRIVACY_DOC
    assert "fitMealIntakeQueueDB" in PRIVACY_DOC
    assert "meal_photos" in PRIVACY_DOC
    assert "stores raw photo bytes only as temporary `Blob` records in" in PRIVACY_DOC
    assert "does not store raw photo bytes" in PRIVACY_DOC
    assert "localStorage" in PRIVACY_DOC
    assert "deleted immediately after the server accepts" in PRIVACY_DOC
