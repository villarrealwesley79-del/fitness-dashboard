import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "docs" / "qa" / "fit-304-push" / "browser_qa.mjs"
NOTES = ROOT / "docs" / "qa" / "fit-304-push" / "README.md"


def _run_harness() -> dict:
    if not shutil.which("node"):
        pytest.skip("FIT-304 browser-runtime QA requires Node.js")
    result = subprocess.run(
        ["node", str(HARNESS)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_push_browser_runtime_covers_required_setup_and_delivery_states():
    evidence = _run_harness()

    assert evidence["setup_states"] == {
        "unsupported": "unsupported",
        "denied": "denied",
        "ios_not_installed": "needs_install",
        "granted_inactive": "granted_inactive",
        "granted_active": "granted_active",
    }
    assert evidence["test_delivery"] == {
        "request_url": "/api/push/test",
        "request_method": "POST",
        "request_credentials": "same-origin",
        "request_csrf": "fixture-token",
        "request_endpoint_hash": "device-endpoint-hash",
        "result": "Delivered. This device should show the test notification now.",
        "toast": "Test notification sent",
    }
    assert evidence["setup_flow"] == {
        "permission_requested": True,
        "subscribed": True,
        "server_path": "/api/push/subscriptions",
        "server_method": "POST",
        "wired_event": "click",
        "subscription_options": {
            "user_visible_only": True,
            "application_server_key": [1, 2, 3],
        },
        "persisted_payload": {
            "subscription": {"endpoint": "https://push.test/device"},
            "permission_state": "granted",
            "pwa_installed": True,
        },
        "result": "Notifications enabled. Send a test notification to verify delivery.",
        "button_reenabled": True,
    }


def test_notification_click_focuses_matching_window_or_opens_expected_url():
    evidence = _run_harness()["notification_click"]

    assert evidence["focus"] == {
        "closed": True,
        "focused_url": "https://fitness.test/settings?from=push",
        "opened_urls": [],
    }
    assert evidence["open"] == {
        "closed": True,
        "focused_url": None,
        "opened_urls": ["https://fitness.test/settings?from=push"],
    }


def test_qa_notes_state_real_platform_limits():
    notes = NOTES.read_text()

    assert "does not prove OS notification display" in notes
    assert "installed iOS PWA" in notes
    assert "real VAPID push delivery" in notes
