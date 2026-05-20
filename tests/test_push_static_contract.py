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
