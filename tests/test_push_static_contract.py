from pathlib import Path

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]


def _push_render_fixture_script():
    return """
function pushNode() {
  return {
    hidden: false, disabled: false, textContent: '', className: '', dataset: {}, handlers: {},
    classList: { add(...names) { names.forEach((name) => { if (!this[name]) this[name] = true; }); }, remove(...names) { names.forEach((name) => { delete this[name]; }); } },
    addEventListener(name, handler) { this.handlers[name] = handler; },
  };
}
[
  'push-notifications-card', 'push-state-chip', 'btn-push-enable', 'btn-push-test', 'btn-push-disable',
  'push-state-detail', 'push-install-row', 'push-blocked-row', 'push-revoked-row', 'push-vapid-row',
  'push-dot', 'push-alerts-row', 'push-alerts-list', 'push-test-row', 'push-test-result',
].forEach((id) => { sandbox.elements[id] = pushNode(); });
"""


def test_service_worker_handles_push_and_notification_click():
    worker = (ROOT / "static" / "js" / "sw.js").read_text()
    for token in ("self.addEventListener('push'", "showNotification", "safety_critical", "self.addEventListener('notificationclick'", "clients.openWindow"):
        assert token in worker


def test_settings_exposes_preview_notification_controls_and_copy():
    template = (ROOT / "templates" / "index.html").read_text()
    for token in ('id="btn-push-test"', 'id="push-test-result"', "Test notifications", "Preview coaching alerts and verify push delivery. This does not schedule reminders."):
        assert token in template
    assert "Coaching reminders" not in template
    assert template.count('id="btn-push-enable"') == 1
    assert template.count('id="btn-push-test"') == 1
    assert template.count('id="btn-push-disable"') == 1
    assert template.count('id="push-alerts-row"') == 1


def test_vapid_endpoint_handles_missing_key_and_auth_failure_at_runtime():
    output = run_app_js(
        ["_pushGetVapidKey"],
        """
let mode = 'missing';
sandbox.__fitSet.fetch(async () => mode === 'missing'
  ? new Response(JSON.stringify({}), { status: 200, headers: { 'content-type': 'application/json' } })
  : new Response(JSON.stringify({}), { status: 401, headers: { 'content-type': 'application/json' } }));
const first = await e._pushGetVapidKey();
mode = 'auth';
const second = await e._pushGetVapidKey();
process.stdout.write(JSON.stringify({ first, second }));
""",
        mocks=["fetch"],
    )
    assert output["first"]["ok"] is False
    assert "VAPID public key is missing" in output["first"]["message"]
    assert output["second"]["ok"] is False
    assert "Sign in" in output["second"]["message"]


def test_send_push_test_gates_inactive_state_without_network_call():
    output = run_app_js(
        ["sendPushTest"],
        """
sandbox.elements['push-test-result'] = { textContent: '', hidden: false, className: '' };
let calls = 0;
sandbox.__fitSet._pushDetectState(async () => ({ name: 'default' }));
sandbox.__fitSet.fetch(async () => { calls += 1; return new Response('{}', { status: 200 }); });
await e.sendPushTest();
process.stdout.write(JSON.stringify({ calls, text: sandbox.elements['push-test-result'].textContent }));
""",
        mocks=["_pushDetectState", "fetch"],
    )
    assert output["calls"] == 0
    assert "Enable notifications first" in output["text"]


def test_send_push_test_posts_endpoint_hash_and_keeps_failures_retryable():
    output = run_app_js(
        ["sendPushTest"],
        """
const result = { textContent: '', hidden: false, className: '' };
const button = { disabled: false };
sandbox.elements['push-test-result'] = result;
sandbox.elements['btn-push-test'] = button;
sandbox.__fitSet._pushDetectState(async () => ({ name: 'granted_active', subs: [{ endpoint_hash: 'hash-1' }] }));
sandbox.__fitSet._pushCurrentEndpointHash(async () => 'hash-1');
let mode = 'delivered_false';
const requests = [];
sandbox.__fitSet.fetch(async (path, options) => {
  requests.push({ path, body: JSON.parse(options.body) });
  if (mode === 'delivered_false') return new Response(JSON.stringify({ status: 'delivered', delivered: false }), { status: 200, headers: { 'content-type': 'application/json' } });
  if (mode === 'server_error') return new Response(JSON.stringify({ error: 'server exploded' }), { status: 500, headers: { 'content-type': 'application/json' } });
  throw new Error('network down');
});
await e.sendPushTest();
const deliveredFalse = { text: result.textContent, disabled: button.disabled };
mode = 'server_error';
await e.sendPushTest();
const serverError = { text: result.textContent, disabled: button.disabled };
mode = 'network_error';
await e.sendPushTest();
const networkError = { text: result.textContent, disabled: button.disabled };
process.stdout.write(JSON.stringify({ requests, deliveredFalse, serverError, networkError }));
""",
        mocks=["_pushDetectState", "_pushCurrentEndpointHash", "fetch"],
    )
    assert output["requests"] == [
        {"path": "/api/push/test", "body": {"endpoint_hash": "hash-1"}},
        {"path": "/api/push/test", "body": {"endpoint_hash": "hash-1"}},
        {"path": "/api/push/test", "body": {"endpoint_hash": "hash-1"}},
    ]
    assert "Not delivered" in output["deliveredFalse"]["text"]
    assert "server exploded" in output["serverError"]["text"]
    assert output["networkError"]["text"] == "Not delivered: network or server error."
    assert output["deliveredFalse"]["disabled"] is False
    assert output["serverError"]["disabled"] is False
    assert output["networkError"]["disabled"] is False


def test_enable_push_permission_denial_rerenders_state_and_reenables_button():
    output = run_app_js(
        ["enablePush"],
        _push_render_fixture_script()
        + """
sandbox.PushManager = function PushManager() {};
sandbox.Notification = { permission: 'default', requestPermission: async () => 'denied' };
sandbox.navigator.serviceWorker = { ready: Promise.resolve(), getRegistration: async () => null };
sandbox.navigator.userAgent = '';
sandbox.navigator.standalone = false;
sandbox.matchMedia = () => ({ matches: false });
sandbox.__fitSet._pushDetectState(async () => ({ name: 'denied', subs: [] }));
sandbox.__fitSet.api(async () => ({ alerts: [] }));
sandbox.__fitSet.renderSettingsGroupSummaries(() => {});
sandbox.__fitSet.toast(() => {});
await e.enablePush();
process.stdout.write(JSON.stringify({
  result: sandbox.elements['push-test-result'].textContent,
  detail: sandbox.elements['push-state-detail'].textContent,
  enableDisabled: sandbox.elements['btn-push-enable'].disabled,
}));
""",
        mocks=["_pushDetectState", "api", "renderSettingsGroupSummaries", "toast"],
    )
    assert "permission was not granted" in output["result"]
    assert "Notifications are blocked" in output["detail"]
    assert output["enableDisabled"] is False


def test_enable_push_service_worker_failure_keeps_setup_error_after_rerender():
    output = run_app_js(
        ["enablePush"],
        _push_render_fixture_script()
        + """
sandbox.PushManager = function PushManager() {};
sandbox.Notification = { permission: 'default', requestPermission: async () => 'granted' };
sandbox.navigator.serviceWorker = { ready: Promise.reject(new Error('worker unavailable')), getRegistration: async () => null };
sandbox.navigator.userAgent = '';
sandbox.navigator.standalone = false;
sandbox.matchMedia = () => ({ matches: false });
sandbox.__fitSet._pushDetectState(async () => ({ name: 'prompt', subs: [] }));
sandbox.__fitSet.api(async () => ({ alerts: [] }));
sandbox.__fitSet.renderSettingsGroupSummaries(() => {});
sandbox.__fitSet.toast(() => {});
await e.enablePush();
process.stdout.write(JSON.stringify({
  result: sandbox.elements['push-test-result'].textContent,
  detail: sandbox.elements['push-state-detail'].textContent,
  enableDisabled: sandbox.elements['btn-push-enable'].disabled,
}));
""",
        mocks=["_pushDetectState", "api", "renderSettingsGroupSummaries", "toast"],
    )
    assert output["result"] == "Notifications could not be enabled: worker unavailable."
    assert output["detail"] == output["result"]
    assert output["enableDisabled"] is False


def test_enable_push_subscription_failure_keeps_setup_error_after_rerender():
    output = run_app_js(
        ["enablePush"],
        _push_render_fixture_script()
        + """
sandbox.PushManager = function PushManager() {};
sandbox.Notification = { permission: 'default', requestPermission: async () => 'granted' };
sandbox.navigator.serviceWorker = { ready: Promise.resolve({ pushManager: { subscribe: async () => { throw new Error('push manager rejected'); } } }), getRegistration: async () => null };
sandbox.navigator.userAgent = '';
sandbox.navigator.standalone = false;
sandbox.matchMedia = () => ({ matches: false });
sandbox.atob = () => String.fromCharCode(1, 2, 3);
sandbox.__fitSet._pushGetVapidKey(async () => ({ ok: true, publicKey: 'AQID' }));
sandbox.__fitSet._pushDetectState(async () => ({ name: 'prompt', subs: [] }));
sandbox.__fitSet.api(async () => ({ alerts: [] }));
sandbox.__fitSet.renderSettingsGroupSummaries(() => {});
sandbox.__fitSet.toast(() => {});
await e.enablePush();
process.stdout.write(JSON.stringify({
  result: sandbox.elements['push-test-result'].textContent,
  detail: sandbox.elements['push-state-detail'].textContent,
  enableDisabled: sandbox.elements['btn-push-enable'].disabled,
}));
""",
        mocks=["_pushGetVapidKey", "_pushDetectState", "api", "renderSettingsGroupSummaries", "toast"],
    )
    assert output["result"] == "Push subscription failed: push manager rejected."
    assert output["detail"] == output["result"]
    assert output["enableDisabled"] is False


def test_enable_push_server_persistence_failure_rolls_back_and_survives_rerender():
    output = run_app_js(
        ["enablePush"],
        _push_render_fixture_script()
        + """
sandbox.PushManager = function PushManager() {};
sandbox.Notification = { permission: 'default', requestPermission: async () => 'granted' };
let unsubscribed = false;
const subscription = {
  toJSON: () => ({ endpoint: 'https://push.example/subscription' }),
  unsubscribe: async () => { unsubscribed = true; return true; },
};
sandbox.navigator.serviceWorker = { ready: Promise.resolve({ pushManager: { subscribe: async () => subscription } }), getRegistration: async () => null };
sandbox.navigator.userAgent = '';
sandbox.navigator.standalone = false;
sandbox.matchMedia = () => ({ matches: false });
sandbox.atob = () => String.fromCharCode(1, 2, 3);
sandbox.__fitSet._pushGetVapidKey(async () => ({ ok: true, publicKey: 'AQID' }));
sandbox.__fitSet._pushDetectState(async () => ({ name: 'prompt', subs: [] }));
sandbox.__fitSet.api(async (path, options) => {
  if (path === '/api/push/subscriptions' && options && options.method === 'POST') throw new Error('database unavailable');
  return { alerts: [] };
});
sandbox.__fitSet.renderSettingsGroupSummaries(() => {});
sandbox.__fitSet.toast(() => {});
await e.enablePush();
process.stdout.write(JSON.stringify({
  result: sandbox.elements['push-test-result'].textContent,
  detail: sandbox.elements['push-state-detail'].textContent,
  unsubscribed,
  enableDisabled: sandbox.elements['btn-push-enable'].disabled,
}));
""",
        mocks=["_pushGetVapidKey", "_pushDetectState", "api", "renderSettingsGroupSummaries", "toast"],
    )
    assert "server could not save it" in output["result"]
    assert output["detail"] == output["result"]
    assert output["unsubscribed"] is True
    assert output["enableDisabled"] is False
