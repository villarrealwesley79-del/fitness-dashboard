import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fitness-status.sh"
WATCHDOG = ROOT / "scripts" / "check-apple-health-staleness.sh"


def _write_command(path, body):
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run_status(tmp_path, *, launchctl_body, lsof_body, log_line, data_dir=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_command(bin_dir / "launchctl", launchctl_body)
    _write_command(bin_dir / "lsof", lsof_body)
    log_path = tmp_path / "staleness.log"
    log_path.write_text(log_line + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "HOME": str(tmp_path),
        "APPLE_HEALTH_STALENESS_LOG": str(log_path),
        "COOKIE": "fit296-session-secret",
        "HEALTH_SYNC_TOKEN": "fit296-health-secret",
    })
    if data_dir:
        env["DATA_DIR"] = str(tmp_path / "runtime")
    else:
        env.pop("DATA_DIR", None)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_status_reports_running_services_listener_and_redacts_log(tmp_path):
    result = _run_status(
        tmp_path,
        launchctl_body="printf 'state = running\\n'",
        lsof_body="printf '4321\\n'",
        log_line="WARN callback=https://host/path?token=abc123 session=owner-cookie stale",
    )

    assert result.returncode == 0, result.stderr
    assert "app_launchd=running" in result.stdout
    assert "staleness_launchd=running" in result.stdout
    assert "listener_pid=4321" in result.stdout
    assert "staleness_last=[REDACTED]" in result.stdout
    assert "abc123" not in result.stdout
    assert "owner-cookie" not in result.stdout
    assert "fit296-session-secret" not in result.stdout + result.stderr
    assert "fit296-health-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("log_line", "secret_values"),
    [
        (
            "WARN TOKEN=upper-secret COOKIE=upper-cookie HEALTH_SYNC_TOKEN=sync-secret",
            ("upper-secret", "upper-cookie", "sync-secret"),
        ),
        (
            "WARN Authorization: Bearer authorization-secret",
            ("authorization-secret",),
        ),
        (
            "WARN Authorization=Bearer assignment-authorization-secret",
            ("assignment-authorization-secret",),
        ),
        (
            "WARN callback=https://host/path?ACCESS_TOKEN=url-secret&STATE=state-secret",
            ("url-secret", "state-secret"),
        ),
        (
            "WARN callback=https://host/path?jwt=jwt-secret",
            ("jwt-secret",),
        ),
        (
            "WARN callback=https://host/path?X-Amz-Signature=signed-secret",
            ("signed-secret",),
        ),
        (
            "WARN Set-Cookie: session=header-secret; Secure",
            ("header-secret",),
        ),
        (
            'WARN {"access_token":"json-secret"}',
            ("json-secret",),
        ),
        (
            'WARN {"Authorization":"Bearer json-authorization-secret"}',
            ("json-authorization-secret",),
        ),
    ],
)
def test_status_redacts_case_header_and_url_variants(tmp_path, log_line, secret_values):
    result = _run_status(
        tmp_path,
        launchctl_body="exit 1",
        lsof_body="exit 0",
        log_line=log_line,
    )

    assert result.returncode == 0, result.stderr
    assert "staleness_last=[REDACTED]" in result.stdout
    for secret_value in secret_values:
        assert secret_value not in result.stdout + result.stderr


def test_status_reports_missing_services_without_failing(tmp_path):
    result = _run_status(
        tmp_path,
        launchctl_body="exit 1",
        lsof_body="exit 0",
        log_line="INFO no sync has ever been recorded",
    )

    assert result.returncode == 0, result.stderr
    assert "app_launchd=missing" in result.stdout
    assert "staleness_launchd=missing" in result.stdout
    assert "listener_pid=missing" in result.stdout
    assert "data_dir=" in result.stdout
    assert "smoke_curl=" in result.stdout
    assert "smoke_python3=" in result.stdout
    assert "smoke_lsof=" in result.stdout
    assert "smoke_auth=ready" in result.stdout


def test_default_staleness_log_matches_watchdog_output_path():
    status_source = SCRIPT.read_text(encoding="utf-8")
    watchdog_source = WATCHDOG.read_text(encoding="utf-8")

    assert (
        "APPLE_HEALTH_STALENESS_LOG_FILE:-${APPLE_HEALTH_STALENESS_LOG:-/tmp/apple-health-staleness.log}"
        in status_source
    )
    assert 'LOG_DIR="/tmp"' in watchdog_source
    assert 'LOG_FILE="${APPLE_HEALTH_STALENESS_LOG_FILE:-${LOG_DIR}/apple-health-staleness.log}"' in watchdog_source


def test_status_reads_data_dir_from_installed_app_plist(tmp_path):
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    runtime_dir = tmp_path / "installed-runtime"
    (launch_agents / "com.fitness-dashboard.plist").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict><key>EnvironmentVariables</key><dict>
<key>DATA_DIR</key><string>{runtime_dir}</string>
</dict></dict></plist>
""",
        encoding="utf-8",
    )

    result = _run_status(
        tmp_path,
        launchctl_body="exit 1",
        lsof_body="exit 0",
        log_line="INFO idle",
        data_dir=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"data_dir={runtime_dir}" in result.stdout
