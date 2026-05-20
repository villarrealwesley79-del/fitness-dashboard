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
    assert "fetch('/api/push/test'" in app_js
