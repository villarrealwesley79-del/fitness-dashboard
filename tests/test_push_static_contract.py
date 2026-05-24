from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_service_worker_handles_push_and_notification_click():
    worker = (ROOT / "static" / "js" / "sw.js").read_text()

    assert "self.addEventListener('push'" in worker
    assert "showNotification" in worker
    assert "safety_critical" in worker
    assert "self.addEventListener('notificationclick'" in worker
    assert "clients.openWindow" in worker


def test_settings_exposes_test_notification_controls():
    template = (ROOT / "templates" / "index.html").read_text()
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    assert 'id="btn-push-test"' in template
    assert 'id="push-test-result"' in template
    assert "Subscribed, no scheduled reminders yet" in app_js
    assert "async function sendPushTest()" in app_js
    assert "async function _pushCurrentEndpointHash()" in app_js
    assert "navigator.serviceWorker.getRegistration()" in app_js
    current_hash_body = app_js.split("async function _pushCurrentEndpointHash()", 1)[1].split("async function enablePush()", 1)[0]
    assert "navigator.serviceWorker.ready" not in current_hash_body
    assert "window.crypto.subtle.digest('SHA-256'" in app_js
    assert "subs.some((sub) => sub && sub.endpoint_hash === endpointHash)" in app_js
    assert "body: JSON.stringify({ endpoint_hash: endpointHash })" in app_js
    assert "fetch('/api/push/test'" in app_js


def test_enable_push_surfaces_subscription_setup_failures():
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    enable_body = app_js.split("async function enablePush()", 1)[1].split("async function disablePush()", 1)[0]

    assert "_pushSetTestResult('Enabling notifications...')" in enable_body
    assert "Notifications permission was not granted" in enable_body
    assert "VAPID public key is missing" in enable_body
    assert "Push subscription failed:" in enable_body
    assert "server could not save it" in enable_body
    assert "Notifications enabled. Send a test notification to verify delivery." in enable_body
    assert "console.warn('pushManager.subscribe failed:'" in enable_body
    assert "_pushSetTestResult(`Notifications could not be enabled:" in enable_body


def test_send_push_test_gates_inactive_state_and_surfaces_non_delivery():
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    send_body = app_js.split("async function sendPushTest()", 1)[1].split("// ── FIT-16", 1)[0]

    assert "const state = await _pushDetectState();" in send_body
    assert "state.name !== 'granted_active'" in send_body
    assert "Enable notifications first, then send a test notification." in send_body
    assert "body.status === 'delivered' && body.delivered !== false" in send_body
    assert "_pushResponseMessage(body, 'Server could not send the test notification.')" in send_body
    assert "_pushSetTestResult(`Not delivered: ${msg}`, 'err')" in send_body
    assert "_pushSetTestResult('Not delivered: network or server error.', 'err')" in send_body
