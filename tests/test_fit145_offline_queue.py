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
PRIVACY_DOC = (ROOT / "docs" / "FOOD_PHOTO_PRIVACY.md").read_text(encoding="utf-8")


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
    block = _fit145_storage_block()

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


def test_meal_queue_replays_existing_meal_intake_form_contract():
    block = _fit145_block()

    assert "fetch('/api/meal-intake'" in block
    assert "form.append('images', photo.blob, `meal-${idx + 1}.${extension}`);" in block
    assert "headers: { 'Accept': 'application/json' }" in block
    assert "credentials: 'same-origin'" in block
    assert "res.status === 409 ? 'conflicted'" in block
    assert "res.status >= 500 ? 'pending' : 'rejected'" in block


def test_success_and_discard_delete_queue_entry_and_photo_bytes():
    block = _fit145_block()

    assert "async function removeMealQueueEntry(clientId)" in block
    assert "photoIds.forEach((photoId) => photoStore.delete(photoId));" in block
    assert "mealStore.delete(clientId);" in block
    assert "await removeMealQueueEntry(clientId);" in block
    assert "last_status: 'eviction_failed'" in block
    assert "data-meal-sync-discard" in block


def test_meal_flush_has_duplicate_guards_and_online_hooks():
    assert "let _mealSyncFlushInFlight = false;" in APP_JS
    assert "const _mealSyncInFlightClientIds = new Set();" in APP_JS
    assert "if (!navigator.onLine || _mealSyncFlushInFlight) return;" in APP_JS
    assert "_mealSyncInFlightClientIds.has(clientId)" in APP_JS
    assert "window.addEventListener('online', () => {" in APP_JS
    assert "flushMealSyncQueue();" in APP_JS


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
    assert "never stores raw photo bytes" in PRIVACY_DOC
    assert "localStorage" in PRIVACY_DOC
    assert "deleted immediately after the server accepts" in PRIVACY_DOC
