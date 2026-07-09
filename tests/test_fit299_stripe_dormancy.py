from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dormant_stripe_paths_require_login_and_are_not_registered(tmp_path):
    script = r'''
import json

import app

client = app.app.test_client()
responses = {
    path: client.get(path, follow_redirects=False)
    for path in ("/pricing", "/success", "/cancel")
}
webhook = client.post("/webhook", follow_redirects=False)

print(json.dumps({
    "get_statuses": {path: response.status_code for path, response in responses.items()},
    "get_locations": {path: response.headers.get("Location") for path, response in responses.items()},
    "webhook_status": webhook.status_code,
    "webhook_location": webhook.headers.get("Location"),
    "webhook_registered": any(rule.rule == "/webhook" for rule in app.app.url_map.iter_rules()),
}))
'''
    env = os.environ.copy()
    env.update({
        "DATA_DIR": str(tmp_path),
        "SECRET_KEY": "fit299-test-secret",
        "SESSION_COOKIE_SECURE": "false",
        "PYTHONPATH": str(REPO_ROOT),
    })
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["get_statuses"] == {
        "/pricing": 302,
        "/success": 302,
        "/cancel": 302,
    }
    assert {
        path: parse_qs(urlparse(location).query).get("next")
        for path, location in payload["get_locations"].items()
    } == {
        "/pricing": ["/pricing"],
        "/success": ["/success"],
        "/cancel": ["/cancel"],
    }
    assert payload["webhook_status"] in {302, 401}
    if payload["webhook_status"] == 302:
        assert parse_qs(urlparse(payload["webhook_location"]).query) == {"next": ["/webhook"]}
    else:
        assert payload["webhook_location"] is None
    assert payload["webhook_registered"] is False
