from pathlib import Path

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]


def _open_wearables_node_script():
    return """
function owNode(value = '') {
  return {
    value, textContent: '', hidden: false, disabled: false, open: false, className: '', dataset: {}, handlers: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(name, handler) { this.handlers[name] = handler; },
    setAttribute(name, value) { this[name] = value; },
    focus() { this.focused = true; },
  };
}
"""


def test_settings_contains_open_wearables_hub_controls():
    html = (ROOT / "templates" / "index.html").read_text()
    gitignore = (ROOT / ".gitignore").read_text()
    for token in (
        "Open Wearables", 'id="open-wearables-state"', 'id="btn-open-wearables-setup"',
        'id="modal-open-wearables-setup"', 'id="open-wearables-base-url"',
        'id="open-wearables-username"', 'id="open-wearables-password"',
        'id="open-wearables-user-id"', 'id="btn-open-wearables-portal"',
        'id="btn-open-wearables-setup-save"', 'id="btn-open-wearables-setup-check"',
        'id="btn-sync-open-wearables"', 'id="open-wearables-mobile-invite"',
        "One-time code", "Garmin", "Suunto", "Polar", "Ultrahuman", "Samsung Health",
        "Google Health Connect", "https://github.com/the-momentum/open-wearables",
    ):
        assert token in html
    assert 'id="open-wearables-allowed-hosts"' not in html
    assert ">Open GitHub<" not in html
    assert "Base URL" not in html
    assert "open_wearables_config.json" in gitignore


def test_open_wearables_connection_and_sync_gates_execute_backend_shape():
    output = run_app_js(
        ["openWearablesIsConnected", "openWearablesCanSync"],
        """
const connected = e.openWearablesIsConnected({ status: 'ok', providers: [{ id: 'oura' }] }, 'ok');
const disconnected = e.openWearablesIsConnected({ status: 'blocked', providers: [] }, 'error');
const canSync = e.openWearablesCanSync({ status: 'ok', providers: [{ id: 'oura' }] }, { user_mapped: true }, 'ready_to_sync');
const blocked = e.openWearablesCanSync({ status: 'blocked', providers: [] }, { user_mapped: false }, 'blocked');
process.stdout.write(JSON.stringify({ connected, disconnected, canSync, blocked }));
""",
    )
    assert output == {"connected": True, "disconnected": False, "canSync": True, "blocked": False}


def test_open_wearables_setup_save_posts_local_fields_and_updates_connected_state():
    output = run_app_js(
        ["saveOpenWearablesSetup", "state"],
        _open_wearables_node_script()
        + """
['open-wearables-base-url', 'open-wearables-portal-url', 'open-wearables-username', 'open-wearables-password', 'open-wearables-user-id'].forEach((id) => { sandbox.elements[id] = owNode({
  'open-wearables-base-url': 'http://hub.local:8000',
  'open-wearables-portal-url': 'https://hub.local/admin',
  'open-wearables-username': 'wesley',
  'open-wearables-password': 'secret-token',
  'open-wearables-user-id': 'person-1',
}[id]); });
sandbox.elements['open-wearables-setup-status'] = owNode();
sandbox.elements['open-wearables-setup-modal-status'] = owNode();
sandbox.elements['btn-open-wearables-setup-save'] = owNode();
const requests = [];
sandbox.__fitSet.api(async (path, options) => {
  requests.push({ path, method: options.method, body: JSON.parse(options.body) });
  return { status: 'ok', open_wearables: { status: 'ok', providers: [{ id: 'oura' }] }, config: {} };
});
sandbox.__fitSet.populateOpenWearablesSetupFields(() => {});
sandbox.__fitSet.renderOpenWearablesDetail(() => {});
sandbox.__fitSet.renderSettings(async () => {});
sandbox.__fitSet.toast(() => {});
await e.saveOpenWearablesSetup();
process.stdout.write(JSON.stringify({ requests, status: sandbox.elements['open-wearables-setup-status'].textContent, saveDisabled: sandbox.elements['btn-open-wearables-setup-save'].disabled, state: e.state.openWearablesStatus.status }));
""",
        mocks=["api", "populateOpenWearablesSetupFields", "renderOpenWearablesDetail", "renderSettings", "toast"],
    )
    assert output["requests"] == [{
        "path": "/api/open-wearables/setup", "method": "POST",
        "body": {"base_url": "http://hub.local:8000", "portal_url": "https://hub.local/admin", "username": "wesley", "password": "secret-token", "user_id": "person-1"},
    }]
    assert output["status"] == "Saved locally. Open Wearables is connected."
    assert output["saveDisabled"] is False
    assert output["state"] == "ok"


def test_open_wearables_check_posts_check_endpoint_and_surfaces_ready_state():
    output = run_app_js(
        ["checkOpenWearables", "state"],
        _open_wearables_node_script()
        + """
sandbox.elements['open-wearables-setup-status'] = owNode();
sandbox.elements['open-wearables-setup-modal-status'] = owNode();
sandbox.elements['btn-open-wearables-setup-check'] = owNode();
e.state.openWearablesStatus = { status: 'blocked', providers: [] };
let request;
sandbox.__fitSet.api(async (path, options) => { request = { path, method: options.method, body: JSON.parse(options.body) }; return { status: 'ok', open_wearables: { status: 'ok', providers: [{ id: 'oura' }] }, config: {} }; });
sandbox.__fitSet.populateOpenWearablesSetupFields(() => {});
sandbox.__fitSet.renderOpenWearablesDetail(() => {});
sandbox.__fitSet.renderSettings(async () => {});
sandbox.__fitSet.toast(() => {});
await e.checkOpenWearables();
process.stdout.write(JSON.stringify({ request, status: sandbox.elements['open-wearables-setup-status'].textContent, checkDisabled: sandbox.elements['btn-open-wearables-setup-check'].disabled, state: e.state.openWearablesStatus.status }));
""",
        mocks=["api", "populateOpenWearablesSetupFields", "renderOpenWearablesDetail", "renderSettings", "toast"],
    )
    assert output == {
        "request": {"path": "/api/open-wearables/setup/check", "method": "POST", "body": {}},
        "status": "Device is visible. Sync now when ready.", "checkDisabled": False, "state": "ok",
    }


def test_open_wearables_sync_posts_manual_trigger_and_clears_cached_state():
    output = run_app_js(
        ["syncOpenWearables", "state"],
        _open_wearables_node_script()
        + """
sandbox.elements['btn-sync-open-wearables'] = owNode();
e.state.openWearablesStatus = { status: 'ok', providers: [{ id: 'oura' }] };
e.state.wearableSources = [{ key: 'oura' }];
e.state.dashboard = { stale: false };
let request;
sandbox.__fitSet.api(async (path, options) => { request = { path, method: options.method, body: JSON.parse(options.body) }; return {}; });
sandbox.__fitSet.renderOpenWearablesDetail(() => {});
sandbox.__fitSet.renderSettings(async () => {});
sandbox.__fitSet.toast(() => {});
await e.syncOpenWearables();
process.stdout.write(JSON.stringify({ request, syncInFlight: e.state.openWearablesUi.syncInFlight, openWearablesStatus: e.state.openWearablesStatus, wearableSources: e.state.wearableSources, dashboard: e.state.dashboard }));
""",
        mocks=["api", "renderOpenWearablesDetail", "renderSettings", "toast"],
    )
    assert output == {
        "request": {"path": "/api/open-wearables/sync", "method": "POST", "body": {"trigger": "manual"}},
        "syncInFlight": False, "openWearablesStatus": None, "wearableSources": None, "dashboard": None,
    }


def test_open_wearables_setup_modal_opens_and_loads_setup_before_returning():
    output = run_app_js(
        ["openOpenWearablesSetupModal"],
        _open_wearables_node_script()
        + """
const modal = owNode();
const advanced = owNode();
modal.querySelector = (selector) => selector === '.open-wearables-advanced' ? advanced : null;
sandbox.elements['modal-open-wearables-setup'] = modal;
sandbox.elements['open-wearables-setup-status'] = owNode();
sandbox.elements['open-wearables-setup-modal-status'] = owNode();
let loaded = 0;
let focused = 0;
sandbox.__fitSet.loadOpenWearablesSetup(async () => { loaded += 1; });
sandbox.__fitSet.focusOpenModal(() => { focused += 1; });
await e.openOpenWearablesSetupModal();
process.stdout.write(JSON.stringify({ hidden: modal.hidden, advancedOpen: advanced.open, loaded, focused }));
""",
        mocks=["loadOpenWearablesSetup", "focusOpenModal"],
    )
    assert output == {"hidden": False, "advancedOpen": False, "loaded": 1, "focused": 1}


def test_open_wearables_portal_rejects_invalid_url_then_opens_valid_admin_link():
    output = run_app_js(
        ["openOpenWearablesPortal"],
        _open_wearables_node_script()
        + """
sandbox.elements['open-wearables-portal-url'] = owNode('ftp://hub.local/admin');
sandbox.elements['open-wearables-setup-status'] = owNode();
sandbox.elements['open-wearables-setup-modal-status'] = owNode();
const opened = [];
sandbox.open = (url, target, features) => opened.push({ url, target, features });
e.openOpenWearablesPortal();
const invalid = sandbox.elements['open-wearables-setup-status'].textContent;
sandbox.elements['open-wearables-portal-url'].value = 'https://hub.local/admin';
e.openOpenWearablesPortal('Continue pairing.');
process.stdout.write(JSON.stringify({ invalid, opened, validStatus: sandbox.elements['open-wearables-setup-status'].textContent }));
""",
    )
    assert output["invalid"] == "Pairing portal link must start with http:// or https://."
    assert output["opened"] == [{"url": "https://hub.local/admin", "target": "_blank", "features": "noopener,noreferrer"}]
    assert output["validStatus"] == "Continue pairing."


def test_open_wearables_provider_pair_posts_endpoint_and_opens_authorization_url():
    output = run_app_js(
        ["pairOpenWearablesProvider", "state"],
        _open_wearables_node_script()
        + """
sandbox.elements['open-wearables-setup-status'] = owNode();
sandbox.elements['open-wearables-setup-modal-status'] = owNode();
const requests = [];
const opened = [];
sandbox.open = (url) => opened.push(url);
sandbox.__fitSet.api(async (path, options) => { requests.push({ path, method: options.method, body: JSON.parse(options.body) }); return { authorization_url: 'https://oura.example/authorize' }; });
sandbox.__fitSet.renderOpenWearablesProviderActions(() => {});
sandbox.__fitSet.renderOpenWearablesDetail(() => {});
sandbox.__fitSet.toast(() => {});
await e.pairOpenWearablesProvider('oura', 'Oura');
process.stdout.write(JSON.stringify({ requests, opened, pairInFlight: e.state.openWearablesUi.pairInFlight, selected: e.state.openWearablesUi.selectedProvider }));
""",
        mocks=["api", "renderOpenWearablesProviderActions", "renderOpenWearablesDetail", "toast"],
    )
    assert output == {
        "requests": [{"path": "/api/open-wearables/pair/oura", "method": "POST", "body": {}}],
        "opened": ["https://oura.example/authorize"], "pairInFlight": False, "selected": "Oura",
    }


def test_open_wearables_replacement_hides_direct_sources_only_when_connected():
    output = run_app_js(
        ["applyOpenWearablesDirectSourceVisibility"],
        _open_wearables_node_script()
        + """
['settings-row-oura', 'oura-detail', 'settings-row-apple', 'apple-detail'].forEach((id) => { sandbox.elements[id] = owNode(); });
const connected = { status: 'connected', providers: [{ id: 'oura' }, { id: 'apple_health' }], replacement_sources: ['oura', 'apple_health'] };
e.applyOpenWearablesDirectSourceVisibility(connected);
const replaced = ['settings-row-oura', 'oura-detail', 'settings-row-apple', 'apple-detail'].map((id) => sandbox.elements[id].hidden);
const disconnected = { status: 'blocked', providers: [{ id: 'oura' }, { id: 'apple_health' }], replacement_sources: ['oura', 'apple_health'] };
e.applyOpenWearablesDirectSourceVisibility(disconnected);
const fallback = ['settings-row-oura', 'oura-detail', 'settings-row-apple', 'apple-detail'].map((id) => sandbox.elements[id].hidden);
process.stdout.write(JSON.stringify({ replaced, fallback }));
""",
    )
    assert output == {"replaced": [True, True, True, True], "fallback": [False, False, False, False]}


def test_open_wearables_event_wiring_connects_setup_portal_pair_and_sync_controls():
    output = run_app_js(
        ["wireEvents"],
        _open_wearables_node_script()
        + """
const ids = ['btn-open-wearables-setup', 'btn-open-wearables-bootstrap', 'btn-open-wearables-portal', 'btn-open-wearables-portal-inline', 'btn-open-wearables-copy-link', 'btn-open-wearables-copy-link-inline', 'btn-open-wearables-setup-save', 'btn-open-wearables-setup-check', 'btn-sync-open-wearables'];
ids.forEach((id) => { sandbox.elements[id] = owNode(); });
sandbox.addEventListener = () => {};
const calls = [];
sandbox.__fitSet.openOpenWearablesSetupModal(() => calls.push('setup'));
sandbox.__fitSet.handleOpenWearablesPrimarySetupAction(() => calls.push('bootstrap'));
sandbox.__fitSet.openOpenWearablesPortal(() => calls.push('portal'));
sandbox.__fitSet.copyOpenWearablesPairingLink(() => calls.push('copy'));
sandbox.__fitSet.saveOpenWearablesSetup(async () => calls.push('save'));
sandbox.__fitSet.checkOpenWearables(async () => calls.push('check'));
sandbox.__fitSet.syncOpenWearables(async () => calls.push('sync'));
e.wireEvents();
for (const id of ids) {
  const handler = sandbox.elements[id].handlers.click;
  if (handler) await handler({ target: sandbox.elements[id] });
}
process.stdout.write(JSON.stringify({ calls, wired: ids.map((id) => sandbox.elements[id].dataset.wired || false) }));
""",
        mocks=["openOpenWearablesSetupModal", "handleOpenWearablesPrimarySetupAction", "openOpenWearablesPortal", "copyOpenWearablesPairingLink", "saveOpenWearablesSetup", "checkOpenWearables", "syncOpenWearables"],
    )
    assert output["calls"] == ["setup", "bootstrap", "portal", "portal", "copy", "copy", "save", "check", "sync"]


def test_history_merges_same_day_strength_sources_once():
    output = run_app_js(
        ["mergeStrengthHistorySources"],
        """
const lifts = [{ date: '2026-07-16', source: 'lifted', canonical_category: 'strength_training', total_sets: 3 }];
const watch = [{ date: '2026-07-16', source: 'apple_health', canonical_category: 'strength_training', total_sets: 3 }, { date: '2026-07-15', source: 'apple_health', canonical_category: 'strength_training', total_sets: 2 }];
process.stdout.write(JSON.stringify(e.mergeStrengthHistorySources(lifts, watch)));
""",
    )
    assert len(output) == 2
    assert output[0]["date"] == "2026-07-16"
    assert output[0]["source"] in ("lifted", "merged")


def test_ai_fact_query_markup_is_suggestion_safe():
    html = (ROOT / "templates" / "index.html").read_text()
    settings_html = html.split('id="tab-settings"', 1)[1].split('<nav class="tab-bar"', 1)[0]
    dashboard_html = html.split('id="tab-dashboard"', 1)[1].split('id="tab-vitals"', 1)[0]
    for token in ('id="modal-ai-fact-query"', 'id="ai-fact-question"', 'id="ai-fact-suggestion-actions"', 'id="btn-ai-suggestion-approve"', 'id="btn-ai-suggestion-reject"'):
        assert token in html
    assert 'id="ai-fact-question"' not in settings_html
    assert 'id="btn-open-ai-fact-query"' in dashboard_html


def test_ai_suggestion_resolution_posts_decision_without_mutating_records():
    output = run_app_js(
        ["resolveAiSuggestion", "state"],
        """
    let captured;
    e.state.aiFactUi.pendingSuggestionId = 'suggestion-1';
    sandbox.__fitSet.api(async (path, options) => { captured = { path, body: JSON.parse(options.body) }; return { suggestion: { status: 'approved' } }; });
await e.resolveAiSuggestion('approve');
process.stdout.write(JSON.stringify(captured));
""",
        mocks=["api"],
    )
    assert output == {"path": "/api/ai/suggestions/suggestion-1/approve", "body": {}}
