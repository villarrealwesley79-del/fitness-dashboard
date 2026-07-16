"""FIT-145 executable contracts for the browser-only offline meal queue."""

from __future__ import annotations

from pathlib import Path

from js_runtime import run_app_js


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
APP_SW = (ROOT / "static" / "js" / "sw.js").read_text(encoding="utf-8")
PRIVACY_DOC = (ROOT / "docs" / "FOOD_PHOTO_PRIVACY.md").read_text(encoding="utf-8")
APP_PY = (ROOT / "app.py").read_text(encoding="utf-8")


# Minimal deterministic IndexedDB implementation for Node scenarios.  It is
# intentionally test-only and persists state for the duration of one scenario.
_INDEXED_DB_FAKE = r"""
const records = new Map();
const photos = new Map();
const storeNames = new Set();
const schemaEvents = [];
const operationLog = [];
const makeRequest = (result) => {
  const request = { result, error: null, onsuccess: null, onerror: null };
  queueMicrotask(() => request.onsuccess && request.onsuccess());
  return request;
};
const storeFor = (name) => {
  const target = name === 'queued_meals' ? records : photos;
  return {
    put: (value) => { operationLog.push({ op: 'put', store: name }); target.set(name === 'queued_meals' ? value.client_id : value.photo_id, structuredClone(value)); return makeRequest(value); },
    get: (key) => { operationLog.push({ op: 'get', store: name, key }); return makeRequest(target.get(key) || undefined); },
    getAll: () => { operationLog.push({ op: 'getAll', store: name }); return makeRequest([...target.values()].map((value) => structuredClone(value))); },
    delete: (key) => { operationLog.push({ op: 'delete', store: name, key }); target.delete(key); return makeRequest(undefined); },
    index: (indexName) => ({
      getAll: (key) => makeRequest([...target.values()].filter((value) => indexName === 'client_id' && value.client_id === key).map((value) => structuredClone(value))),
    }),
    createIndex: (name, keyPath, options) => schemaEvents.push({ type: 'index', name, keyPath, options }),
  };
};
const makeTransaction = (names) => {
  const tx = {
    objectStore: (name) => storeFor(name),
    error: null,
    onabort: null,
    onerror: null,
  };
  Object.defineProperty(tx, 'oncomplete', {
    set: (handler) => queueMicrotask(() => handler && handler()),
  });
  return tx;
};
const db = {
  objectStoreNames: { contains: (name) => storeNames.has(name) },
  createObjectStore: (name, options) => {
    storeNames.add(name);
    schemaEvents.push({ type: 'store', name, options });
    return storeFor(name);
  },
  transaction: (names) => makeTransaction(names),
  close: () => {},
};
sandbox.__fitIndexedDb = { records, photos, schemaEvents, operationLog };
sandbox.indexedDB = {
  open: () => {
    const request = { result: db, error: null, onupgradeneeded: null, onsuccess: null, onerror: null, onblocked: null };
    queueMicrotask(() => {
      request.onupgradeneeded && request.onupgradeneeded();
      queueMicrotask(() => request.onsuccess && request.onsuccess());
    });
    return request;
  },
};
"""


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
    output = run_app_js(
        ["openMealQueueDb"],
        _INDEXED_DB_FAKE
        + """
await e.openMealQueueDb();
process.stdout.write(JSON.stringify(sandbox.__fitIndexedDb.schemaEvents));
""",
    )

    assert "const MEAL_QUEUE_DB_NAME = 'fitMealIntakeQueueDB';" in APP_JS
    assert "const MEAL_QUEUE_DB_VERSION = 1;" in APP_JS
    assert "const MEAL_QUEUE_STORE = 'queued_meals';" in APP_JS
    assert "const MEAL_PHOTO_STORE = 'meal_photos';" in APP_JS
    assert output == [
        {"type": "store", "name": "queued_meals", "options": {"keyPath": "client_id"}},
        {"type": "store", "name": "meal_photos", "options": {"keyPath": "photo_id"}},
        {"type": "index", "name": "client_id", "keyPath": "client_id", "options": {"unique": False}},
    ]


def test_meal_queue_stores_blobs_in_indexeddb_not_localstorage():
    output = run_app_js(
        ["enqueueMealIntakeOffline", "getQueuedMealWithPhotos"],
        _INDEXED_DB_FAKE
        + """
const writes = [];
sandbox.localStorage.getItem = (key) => key === 'fit145:meal-queue-auth-scope:v1' ? 'user:1' : null;
sandbox.localStorage.setItem = (key, value) => writes.push({ key, value });
const file = { name: 'secret.jpg', type: 'image/png', size: 4, slice: () => new Blob(['raw'], { type: 'image/png' }) };
await e.enqueueMealIntakeOffline({ textValue: 'oats', files: [file], clientId: 'meal-1', localTime: {} });
const queued = await e.getQueuedMealWithPhotos('meal-1');
process.stdout.write(JSON.stringify({
  entry: queued.entry,
  photo: { type: queued.photos[0].blob.type, size: queued.photos[0].blob.size, fileName: queued.photos[0].name || null },
  writes,
}));
""",
    )

    assert output["entry"]["image_count"] == 1
    assert output["entry"]["aggregate_bytes"] == 4
    assert output["photo"] == {"type": "image/png", "size": 3, "fileName": None}
    assert output["writes"] == []


def test_offline_submit_enqueues_with_original_client_id_and_timestamps():
    output = run_app_js(
        ["submitMealComposer", "getQueuedMealWithPhotos", "mealComposerState"],
        _INDEXED_DB_FAKE
        + """
sandbox.navigator.onLine = false;
sandbox.localStorage.getItem = (key) => key === 'fit145:meal-queue-auth-scope:v1' ? 'user:1' : null;
sandbox.elements['meal-composer-text'] = { value: '  oats  ' };
sandbox.elements['meal-composer-submit'] = { disabled: false, textContent: '' };
sandbox.elements['meal-composer-status'] = { hidden: true, textContent: '', classList: { remove: () => {} } };
sandbox.__fitSet.toast(() => {});
e.mealComposerState.imageFiles = [{ type: 'image/jpeg', size: 3, slice: () => new Blob(['abc'], { type: 'image/jpeg' }) }];
e.mealComposerState.draftClientId = 'meal-client-145';
await e.submitMealComposer();
const queued = await e.getQueuedMealWithPhotos('meal-client-145');
process.stdout.write(JSON.stringify({
  submitLabel: sandbox.elements['meal-composer-submit'].textContent,
  entry: queued.entry,
}));
""",
        mocks=["toast"],
    )

    assert output["submitLabel"] == "Save offline"
    assert output["entry"]["client_id"] == "meal-client-145"
    assert output["entry"]["text"] == "oats"
    assert output["entry"]["local_timestamp"]
    assert output["entry"]["local_date"]
    assert output["entry"]["local_iso"]
    assert "return Response(" in APP_PY
    assert "render_template('index.html')" in APP_PY
    assert '"Cache-Control": "no-store, max-age=0"' in APP_PY
    assert "data-auth-scope" not in INDEX_HTML


def test_meal_queue_replays_existing_meal_intake_form_contract():
    output = run_app_js(
        ["postQueuedMealIntake"],
        """
const captured = [];
sandbox.fetch = async (path, options) => {
  const fields = [];
  for (const [name, value] of options.body.entries()) {
    fields.push({ name, value: typeof value === 'string' ? value : { name: value.name, type: value.type, size: value.size } });
  }
  captured.push({ path, options: { credentials: options.credentials, headers: options.headers, fields } });
  return new Response(JSON.stringify({ status: 'logged' }), { status: 200, headers: { 'content-type': 'application/json' } });
};
const result = await e.postQueuedMealIntake(
  { client_id: 'meal-1', text: 'oats', local_timestamp: 'timestamp', local_date: '2026-07-16', local_iso: '2026-07-16T10:00:00' },
  [{ type: 'image/png', blob: new Blob(['abc'], { type: 'image/png' }) }],
);
process.stdout.write(JSON.stringify({ result, captured }));
""",
    )

    assert output["result"] == {"ok": True, "status": 200, "body": {"status": "logged"}}
    request = output["captured"][0]
    assert request["path"] == "/api/meal-intake"
    assert request["options"]["credentials"] == "same-origin"
    assert request["options"]["headers"]["Accept"] == "application/json"
    assert request["options"]["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert request["options"]["fields"] == [
        {"name": "text", "value": "oats"},
        {"name": "images", "value": {"name": "meal-1.png", "type": "image/png", "size": 3}},
        {"name": "client_id", "value": "meal-1"},
        {"name": "local_timestamp", "value": "timestamp"},
        {"name": "local_date", "value": "2026-07-16"},
        {"name": "local_iso", "value": "2026-07-16T10:00:00"},
    ]

    failure = run_app_js(
        ["postQueuedMealIntake"],
        """
sandbox.fetch = async () => new Response(JSON.stringify({ error: { message: 'temporary' } }), { status: 503, headers: { 'content-type': 'application/json' } });
const result = await e.postQueuedMealIntake({ client_id: 'meal-1' }, []);
process.stdout.write(JSON.stringify(result));
""",
    )
    assert failure["ok"] is False
    assert failure["status"] == 503
    assert failure["syncStatus"] == "pending"


def test_auth_failures_stay_visible_retryable_and_do_not_post_on_scope_mismatch():
    output = run_app_js(
        ["enqueueMealIntakeOffline", "getQueuedMealWithPhotos", "syncSingleMealQueueEntry"],
        _INDEXED_DB_FAKE
        + """
const requests = [];
let authMode = 'mismatch';
sandbox.localStorage.getItem = (key) => key === 'fit145:meal-queue-auth-scope:v1' ? 'user:owner' : null;
sandbox.fetch = async (path) => {
  requests.push(path);
  if (authMode === 'forbidden') return new Response('{}', { status: 403 });
  return new Response(JSON.stringify({ auth_scope: 'user:other' }), { status: 200, headers: { 'content-type': 'application/json' } });
};
await e.enqueueMealIntakeOffline({ textValue: 'mismatch', files: [], clientId: 'meal-mismatch', localTime: {} });
const mismatch = await e.syncSingleMealQueueEntry('meal-mismatch');
const mismatchEntry = (await e.getQueuedMealWithPhotos('meal-mismatch')).entry;
authMode = 'forbidden';
await e.enqueueMealIntakeOffline({ textValue: 'forbidden', files: [], clientId: 'meal-forbidden', localTime: {} });
const forbidden = await e.syncSingleMealQueueEntry('meal-forbidden');
const forbiddenEntry = (await e.getQueuedMealWithPhotos('meal-forbidden')).entry;
process.stdout.write(JSON.stringify({ requests, mismatch, mismatchEntry, forbidden, forbiddenEntry }));
""",
    )

    assert "@app.route('/api/auth/scope')" in APP_PY
    assert 'return jsonify({"auth_scope": _current_auth_scope()})' in APP_PY
    assert 'scoped["auth_scope"] = _current_auth_scope()' in APP_PY
    assert output["requests"] == ["/api/auth/scope", "/api/auth/scope"]
    assert output["mismatch"] == {"ok": False, "status": "auth_required"}
    assert output["mismatchEntry"]["last_status"] == "auth_required"
    assert output["forbidden"] == {"ok": False, "status": "auth_required"}
    assert output["forbiddenEntry"]["last_status"] == "auth_required"
    assert "sync-status-auth_required" in APP_CSS


def test_service_worker_offline_auth_scope_fallback_stays_retryable():
    output = run_app_js(
        ["fetchCurrentMealQueueAuthScope"],
        """
const responses = [
  new Response('{}', { status: 503 }),
  new Response(JSON.stringify({ auth_scope: '' }), { status: 200, headers: { 'content-type': 'application/json' } }),
  new Response('{}', { status: 401 }),
];
sandbox.fetch = async () => responses.shift();
const results = [
  await e.fetchCurrentMealQueueAuthScope(),
  await e.fetchCurrentMealQueueAuthScope(),
  await e.fetchCurrentMealQueueAuthScope(),
];
process.stdout.write(JSON.stringify(results));
""",
    )

    assert "JSON.stringify({ error: 'Offline' })" in APP_SW
    assert output == [
        {"ok": False, "status": "pending", "reason": "Could not verify the current sign-in before syncing this meal (503)."},
        {"ok": False, "status": "pending", "reason": "Could not verify the current sign-in before syncing this meal."},
        {"ok": False, "status": "auth_required", "reason": "Sign in with the account that saved this offline meal, then retry."},
    ]


def test_meal_sync_rechecks_queue_and_blocks_discard_while_uploading():
    output = run_app_js(
        ["enqueueMealIntakeOffline", "syncSingleMealQueueEntry", "renderSyncQueueModal", "getQueuedMealWithPhotos"],
        _INDEXED_DB_FAKE
        + """
const makeNode = () => ({
  className: '', innerHTML: '', hidden: false, children: [],
  classList: { add: () => {}, toggle: () => {} },
  appendChild(node) { this.children.push(node); },
  querySelectorAll: () => [],
});
const host = makeNode();
sandbox.elements['sync-queue-list'] = host;
sandbox.document.createElement = () => makeNode();
sandbox.localStorage.getItem = (key) => key === 'fit145:meal-queue-auth-scope:v1' ? 'user:owner' : null;
sandbox.__fitSet.handleMealIntakeResponse(() => {});
sandbox.__fitSet.toast(() => {});
await e.enqueueMealIntakeOffline({ textValue: 'queued meal', files: [], clientId: 'meal-in-flight', localTime: {} });
let resolveAuth;
const requests = [];
sandbox.fetch = async (path) => {
  requests.push(path);
  if (path === '/api/auth/scope') {
    return new Promise((resolve) => { resolveAuth = () => resolve(new Response(JSON.stringify({ auth_scope: 'user:owner' }), { status: 200, headers: { 'content-type': 'application/json' } })); });
  }
  return new Response(JSON.stringify({ status: 'logged' }), { status: 200, headers: { 'content-type': 'application/json' } });
};
const firstPromise = e.syncSingleMealQueueEntry('meal-in-flight');
await new Promise((resolve) => setTimeout(resolve, 0));
const duplicate = await e.syncSingleMealQueueEntry('meal-in-flight');
await e.renderSyncQueueModal();
const rendered = host.children.map((node) => node.innerHTML).join('');
resolveAuth();
const first = await firstPromise;
const remaining = await e.getQueuedMealWithPhotos('meal-in-flight');
process.stdout.write(JSON.stringify({
  first, duplicate, rendered, requests,
  gets: sandbox.__fitIndexedDb.operationLog.filter((entry) => entry.op === 'get' && entry.store === 'queued_meals').length,
  remaining: remaining.entry,
}));
""",
        mocks=["handleMealIntakeResponse", "toast"],
    )

    assert output["first"] == {"ok": True, "status": "synced"}
    assert output["duplicate"] == {"ok": False, "status": "pending"}
    assert output["requests"] == ["/api/auth/scope", "/api/meal-intake"]
    assert output["gets"] >= 3
    assert output["remaining"] is None
    assert 'data-meal-sync-discard="meal-in-flight"' in output["rendered"]
    assert 'data-meal-sync-retry="meal-in-flight"' in output["rendered"]
    assert 'disabled aria-disabled="true"' in output["rendered"]
    assert 'Syncing...' in output["rendered"]


def test_success_and_discard_delete_queue_entry_and_photo_bytes():
    output = run_app_js(
        ["enqueueMealIntakeOffline", "getQueuedMealWithPhotos", "syncSingleMealQueueEntry"],
        _INDEXED_DB_FAKE
        + """
sandbox.localStorage.getItem = (key) => key === 'fit145:meal-queue-auth-scope:v1' ? 'user:owner' : null;
sandbox.__fitSet.handleMealIntakeResponse(() => {});
sandbox.__fitSet.toast(() => {});
const file = { type: 'image/jpeg', size: 3, slice: () => new Blob(['abc'], { type: 'image/jpeg' }) };
await e.enqueueMealIntakeOffline({ textValue: 'success', files: [file], clientId: 'meal-success', localTime: {} });
let requests = [];
sandbox.fetch = async (path) => {
  requests.push(path);
  if (path === '/api/auth/scope') return new Response(JSON.stringify({ auth_scope: 'user:owner' }), { status: 200, headers: { 'content-type': 'application/json' } });
  return new Response(JSON.stringify({ status: 'logged' }), { status: 200, headers: { 'content-type': 'application/json' } });
};
const synced = await e.syncSingleMealQueueEntry('meal-success');
const afterSync = await e.getQueuedMealWithPhotos('meal-success');
await e.enqueueMealIntakeOffline({ textValue: 'eviction', files: [file], clientId: 'meal-eviction', localTime: {} });
sandbox.__fitSet.removeMealQueueEntry(() => { throw new Error('delete failed'); });
const eviction = await e.syncSingleMealQueueEntry('meal-eviction');
const afterEviction = (await e.getQueuedMealWithPhotos('meal-eviction')).entry;
process.stdout.write(JSON.stringify({ synced, afterSync, requests, eviction, afterEviction }));
""",
        mocks=["handleMealIntakeResponse", "toast", "removeMealQueueEntry"],
    )

    assert output["synced"] == {"ok": True, "status": "synced"}
    assert output["afterSync"] == {"entry": None, "photos": []}
    assert output["requests"] == ["/api/auth/scope", "/api/meal-intake", "/api/auth/scope", "/api/meal-intake"]
    assert output["eviction"]["ok"] is False
    assert output["eviction"]["status"] == "eviction_failed"
    assert output["afterEviction"]["last_status"] == "eviction_failed"


def test_meal_flush_has_duplicate_guards_and_online_hooks():
    output = run_app_js(
        ["enqueueMealIntakeOffline", "flushMealSyncQueue", "getQueuedMealWithPhotos"],
        _INDEXED_DB_FAKE
        + """
sandbox.localStorage.getItem = (key) => key === 'fit145:meal-queue-auth-scope:v1' ? 'user:owner' : null;
sandbox.__fitSet.handleMealIntakeResponse(() => {});
sandbox.__fitSet.toast(() => {});
await e.enqueueMealIntakeOffline({ textValue: 'queued', files: [], clientId: 'meal-flush', localTime: {} });
const requests = [];
sandbox.fetch = async (path) => {
  requests.push(path);
  if (path === '/api/auth/scope') return new Response(JSON.stringify({ auth_scope: 'user:owner' }), { status: 200, headers: { 'content-type': 'application/json' } });
  return new Response(JSON.stringify({ status: 'logged' }), { status: 200, headers: { 'content-type': 'application/json' } });
};
sandbox.navigator.onLine = false;
await e.flushMealSyncQueue();
const offline = await e.getQueuedMealWithPhotos('meal-flush');
sandbox.navigator.onLine = true;
await e.flushMealSyncQueue();
const online = await e.getQueuedMealWithPhotos('meal-flush');
process.stdout.write(JSON.stringify({ requests, offline: offline.entry && offline.entry.last_status, online }));
""",
        mocks=["handleMealIntakeResponse", "toast"],
    )

    assert output["requests"] == ["/api/auth/scope", "/api/meal-intake"]
    assert output["offline"] == "pending"
    assert output["online"] == {"entry": None, "photos": []}
    assert "const CACHE_NAME = 'fitness-dashboard-v20260713-fit270-oura-detail';" in APP_SW
    assert "cache.addAll" not in APP_SW
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
