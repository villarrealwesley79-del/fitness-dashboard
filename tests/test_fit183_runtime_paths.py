from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_probe(script: str, **env_updates: str) -> dict:
    env = os.environ.copy()
    env.update(env_updates)
    env.setdefault("SECRET_KEY", "fit183-test-secret")
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _under(root: Path, path: str) -> bool:
    return os.path.commonpath([str(root), path]) == str(root)


def _launchd_dry_run(tmp_path: Path, action: str) -> str:
    env = os.environ.copy()
    env.update(
        HOME=str(tmp_path / "home"),
        DATA_DIR=str(tmp_path / "runtime-data"),
        FITNESS_DASHBOARD_PUBLIC_BASE_URL="https://fitness.example.test:5050",
    )
    result = subprocess.run(
        [
            "bash",
            "scripts/install-launchd-agents.sh",
            action,
            "--dry-run",
            "--repo-dir",
            str(REPO_ROOT),
            "--python",
            "/opt/fitness/python",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _plists_from_dry_run(output: str) -> list[dict]:
    documents = re.findall(r"(<\?xml version=.*?</plist>)", output, flags=re.DOTALL)
    return [plistlib.loads(document.encode("utf-8")) for document in documents]


def test_data_dir_env_controls_json_and_sqlite_stores(tmp_path):
    script = r"""
import json
import os

import app
import auth
import data_store

stores = {
    "workouts_json": app.WORKOUTS_FILE,
    "soreness_json": app.SORENESS_FILE,
    "settings_json": app.SETTINGS_FILE,
    "cardio_json": app.CARDIO_FILE,
    "recovery_json": app.RECOVERY_FILE,
    "baselines_json": app.BASELINES_FILE,
    "body_json": app.BODY_FILE,
    "sleep_json": app.SLEEP_FILE,
    "wearable_facts_db": app.WEARABLE_FACTS_DB_FILE,
    "fitness_data_db": data_store.DATA_DB,
    "auth_db": auth.AUTH_DB,
}

json_defaults = {
    app.WORKOUTS_FILE: [],
    app.SORENESS_FILE: [],
    app.SETTINGS_FILE: {},
    app.CARDIO_FILE: [],
    app.RECOVERY_FILE: [],
    app.BASELINES_FILE: {},
    app.BODY_FILE: [],
    app.SLEEP_FILE: [],
}
for path, default in json_defaults.items():
    app.save_json(path, default)
app.app.config.update(LOGIN_DISABLED=True)
app.app.test_client().get("/api/freshness")

print(json.dumps({name: {"path": path, "exists": os.path.exists(path)} for name, path in stores.items()}))
"""

    payload = _run_probe(script, DATA_DIR=str(tmp_path))

    expected_filenames = {
        "workouts_json": "data_workouts.json",
        "soreness_json": "data_soreness.json",
        "settings_json": "data_settings.json",
        "cardio_json": "data_cardio.json",
        "recovery_json": "data_recovery.json",
        "baselines_json": "data_baselines.json",
        "body_json": "data_body.json",
        "sleep_json": "data_sleep.json",
        "wearable_facts_db": "wearable_facts.sqlite3",
        "fitness_data_db": "fitness_data.db",
        "auth_db": "auth.db",
    }
    assert set(payload) == set(expected_filenames)
    for name, expected_filename in expected_filenames.items():
        path = payload[name]["path"]
        assert payload[name]["exists"] is True
        assert Path(path).name == expected_filename
        assert _under(tmp_path, path)


def test_boot_and_requests_create_no_legacy_apple_health_files(tmp_path):
    script = r"""
import json
import os

import app

app.app.config.update(LOGIN_DISABLED=True)
client = app.app.test_client()
client.get("/api/freshness")
client.get("/api/dashboard")
print(json.dumps(sorted(os.listdir(os.environ["DATA_DIR"]))))
"""

    created = _run_probe(script, DATA_DIR=str(tmp_path))

    assert not [name for name in created if "apple" in name or name.startswith(".health-sync")]


def test_save_json_uses_unique_atomic_temp_file(tmp_path):
    target = tmp_path / "data.json"
    script = r"""
import json
import os

import app

target = os.environ["TARGET_FILE"]
replacements = []
real_replace = app.os.replace

def capture_replace(src, dst):
    replacements.append({"src": os.path.basename(src), "dst": dst})
    return real_replace(src, dst)

app.os.replace = capture_replace
app.save_json(target, {"ok": True})
leftovers = [name for name in os.listdir(os.path.dirname(target)) if name.endswith(".tmp")]
print(json.dumps({
    "saved": json.load(open(target)),
    "replacements": replacements,
    "leftovers": leftovers,
}))
"""

    payload = _run_probe(script, DATA_DIR=str(tmp_path), TARGET_FILE=str(target))

    assert payload["saved"] == {"ok": True}
    assert payload["leftovers"] == []
    assert len(payload["replacements"]) == 1
    assert payload["replacements"][0]["src"] != "data.json.tmp"
    assert payload["replacements"][0]["src"].startswith(".data.json.")


def test_canonical_public_base_url_feeds_push(tmp_path):
    script = r"""
import json

import app

with app.app.test_request_context("/api/push/test", base_url="http://127.0.0.1:5050"):
    print(json.dumps({
        "push_claims": app._push_vapid_claims(),
        "push_payload": app._push_test_payload(),
    }))
"""

    payload = _run_probe(
        script,
        DATA_DIR=str(tmp_path),
        FITNESS_DASHBOARD_PUBLIC_BASE_URL="fitness.example.test",
        FITNESS_PUSH_VAPID_SUBJECT="",
        VAPID_SUBJECT="",
    )

    assert payload["push_claims"] == {"sub": "https://fitness.example.test"}
    assert payload["push_payload"]["url"] == "https://fitness.example.test"


def test_vapid_claims_keep_mailto_fallback_for_local_http(tmp_path):
    script = r"""
import json

import app

with app.app.test_request_context("/api/push/test", base_url="http://127.0.0.1:5050"):
    print(json.dumps(app._push_vapid_claims()))
"""

    payload = _run_probe(
        script,
        DATA_DIR=str(tmp_path),
        FITNESS_DASHBOARD_PUBLIC_BASE_URL="",
        FITNESS_PUSH_VAPID_SUBJECT="",
        VAPID_SUBJECT="",
    )

    assert payload == {"sub": "mailto:admin@example.com"}


def test_auth_helpers_use_data_dir(tmp_path):
    script = r"""
import json

import app
import auth

print(json.dumps({"auth_db": auth.AUTH_DB}))
"""

    payload = _run_probe(script, DATA_DIR=str(tmp_path))

    assert _under(tmp_path, payload["auth_db"])


def test_launchd_installer_exports_data_dir_and_public_base_url(tmp_path):
    data_dir = tmp_path / "runtime-data"
    env = os.environ.copy()
    env["DATA_DIR"] = str(data_dir)
    env["FITNESS_DASHBOARD_PUBLIC_BASE_URL"] = "https://fitness.example.test:5050"
    result = subprocess.run(
        [
            "bash",
            "scripts/install-launchd-agents.sh",
            "install",
            "--dry-run",
            "--repo-dir",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "<key>DATA_DIR</key>" in result.stdout
    assert f"<string>{data_dir}</string>" in result.stdout
    assert "<key>FITNESS_DASHBOARD_PUBLIC_BASE_URL</key>" in result.stdout
    assert "<string>https://fitness.example.test:5050</string>" in result.stdout
    assert "apple_health_sync.db" not in result.stdout
    assert "admins-mac-mini.tail6c6490.ts.net" not in result.stdout


def test_launchd_installer_does_not_hardcode_public_base_url(tmp_path):
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "runtime-data")
    env.pop("FITNESS_DASHBOARD_PUBLIC_BASE_URL", None)
    result = subprocess.run(
        [
            "bash",
            "scripts/install-launchd-agents.sh",
            "install",
            "--dry-run",
            "--repo-dir",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "admins-mac-mini.tail6c6490.ts.net" not in result.stdout
    assert "<key>FITNESS_DASHBOARD_PUBLIC_BASE_URL</key>" in result.stdout


def test_launchd_install_and_reinstall_emit_valid_complete_plists(tmp_path):
    launch_agents = tmp_path / "home" / "Library" / "LaunchAgents"
    app_path = launch_agents / "com.fitness-dashboard.plist"
    data_dir = tmp_path / "runtime-data"

    for action in ("install", "reinstall"):
        output = _launchd_dry_run(tmp_path, action)
        plists = _plists_from_dry_run(output)

        assert len(plists) == 1
        [app_plist] = plists
        assert app_plist == {
            "Label": "com.fitness-dashboard",
            "WorkingDirectory": str(REPO_ROOT),
            "ProgramArguments": ["/opt/fitness/python", str(REPO_ROOT / "app.py")],
            "EnvironmentVariables": {
                "HOST": "127.0.0.1",
                "PORT": "5050",
                "FLASK_DEBUG": "0",
                "DATA_DIR": str(data_dir),
                "FITNESS_DASHBOARD_PUBLIC_BASE_URL": "https://fitness.example.test:5050",
                "PYTHONUNBUFFERED": "1",
            },
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": "/tmp/fitness-dashboard.log",
            "StandardErrorPath": "/tmp/fitness-dashboard.err.log",
        }
        assert "staleness" not in output
        assert output.count("[dry-run] write ") == 1
        assert output.count("[dry-run] launchctl bootstrap ") == 1
        assert output.count("[dry-run] launchctl kickstart -k ") == 1
        if action == "install":
            assert output.count("[dry-run] launchctl bootout ") == 1
            assert "[dry-run] rm -f " not in output
        else:
            assert output.count("[dry-run] launchctl bootout ") == 2
            assert f"[dry-run] rm -f {app_path}" in output
