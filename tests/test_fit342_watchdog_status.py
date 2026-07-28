from pathlib import Path

import app as fitness_app
import apple_health_parser
import pytest


@pytest.mark.parametrize(
    ("line", "state", "label", "detail"),
    [
        (
            "[2026-07-14T22:00:00Z] INFO: no sync has ever been recorded; skipping staleness check\n",
            "quiet",
            "Quiet",
            "No first sync yet. Watchdog is waiting for data.",
        ),
        (
            "[2026-07-14T22:00:00Z] OK: last Apple Health sync 1.25h ago\n",
            "ok",
            "OK",
            "Last sync 1.25h ago.",
        ),
        (
            "[2026-07-14T22:00:00Z] STALE: last Apple Health sync 48.00h ago (threshold 36h)\n",
            "stale",
            "Stale",
            "No Apple Health sync for 48.00h (36h threshold).",
        ),
        (
            "[2026-07-14T22:00:00Z] WARN: could not parse last_sync='not-a-timestamp'\n",
            "parse_error",
            "Parse error",
            "Latest watchdog result could not be parsed.",
        ),
    ],
)
def test_watchdog_status_normalizes_current_log_state(
    monkeypatch, tmp_path, line, state, label, detail
):
    log_path = tmp_path / "apple-health-staleness.log"
    log_path.write_text(line)
    monkeypatch.setenv("APPLE_HEALTH_STALENESS_LOG_FILE", str(log_path))

    status = apple_health_parser._apple_health_watchdog_status()

    assert status == {
        "state": state,
        "label": label,
        "detail": detail,
        "checked_at": "2026-07-14T22:00:00Z",
    }


def test_watchdog_status_is_quiet_before_the_log_exists(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "APPLE_HEALTH_STALENESS_LOG_FILE",
        str(tmp_path / "missing-apple-health-staleness.log"),
    )

    assert apple_health_parser._apple_health_watchdog_status() == {
        "state": "quiet",
        "label": "Quiet",
        "detail": "No first sync yet. Watchdog is waiting for data.",
        "checked_at": None,
    }


@pytest.mark.parametrize(
    "line",
    [
        "local path and secret-like data must not leave the host\n",
        "[secret-like timestamp] OK: last Apple Health sync 1.25h ago\n",
    ],
)
def test_watchdog_status_fails_closed_without_echoing_unparsed_log_text(
    monkeypatch, tmp_path, line
):
    log_path = tmp_path / "apple-health-staleness.log"
    log_path.write_text(line)
    monkeypatch.setenv("APPLE_HEALTH_STALENESS_LOG_FILE", str(log_path))

    status = apple_health_parser._apple_health_watchdog_status()

    assert status["state"] == "parse_error"
    assert "secret-like" not in str(status)
    assert "local path" not in str(status)


def test_apple_health_status_endpoint_exposes_only_normalized_watchdog_fields(
    monkeypatch, tmp_path
):
    log_path = tmp_path / "apple-health-staleness.log"
    log_path.write_text(
        "[2026-07-14T22:00:00Z] STALE: last Apple Health sync 48.00h ago (threshold 36h)\n"
    )
    monkeypatch.setenv("APPLE_HEALTH_STALENESS_LOG_FILE", str(log_path))
    fitness_app.app.config.update(TESTING=True, LOGIN_DISABLED=True)

    response = fitness_app.app.test_client().get("/api/apple-health/sync/status")

    assert response.status_code == 200
    watchdog = response.get_json()["watchdog"]
    assert set(watchdog) == {"state", "label", "detail", "checked_at"}
    assert watchdog["state"] == "stale"
    assert str(log_path) not in str(watchdog)


def test_settings_renders_one_current_watchdog_summary_in_apple_health_detail():
    html = Path("templates/index.html").read_text()
    source = Path("static/js/app.js").read_text()

    assert html.count('id="apple-detail-watchdog-row"') == 1
    assert 'id="apple-watchdog-state"' in html
    assert 'id="apple-watchdog-detail"' in html
    assert "function renderAppleHealthWatchdogStatus(watchdog)" in source
    assert "renderAppleHealthWatchdogStatus(ah && ah.watchdog);" in source
