import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "check-apple-health-staleness.sh"


def _run_watchdog(tmp_path, db_path, **environment):
    log_path = tmp_path / "staleness.log"
    first_seen_path = tmp_path / "first-sync"
    env = os.environ.copy()
    env.update(
        {
            "APPLE_HEALTH_SYNC_DB": str(db_path),
            "APPLE_HEALTH_FIRST_SEEN_FILE": str(first_seen_path),
            "APPLE_HEALTH_STALENESS_LOG_FILE": str(log_path),
            "STALE_AFTER_HOURS": "36",
        }
    )
    env.update(environment)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log_path.read_text() if log_path.exists() else "", first_seen_path


def _create_sync_db(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE ah_sync_events (created_at TEXT)")
        connection.execute("CREATE TABLE ah_sync_log (created_at TEXT)")


def _hours_ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def test_missing_database_stays_quiet_and_uses_isolated_log(tmp_path):
    db_path = tmp_path / "missing.sqlite3"

    result, log, first_seen = _run_watchdog(tmp_path, db_path)

    assert result.returncode == 0
    assert "INFO: sync database not found" in log
    assert str(db_path) in log
    assert not first_seen.exists()


def test_empty_sync_tables_stay_quiet(tmp_path):
    db_path = tmp_path / "empty.sqlite3"
    _create_sync_db(db_path)

    result, log, first_seen = _run_watchdog(tmp_path, db_path)

    assert result.returncode == 0
    assert "INFO: no sync has ever been recorded" in log
    assert not first_seen.exists()


@pytest.mark.parametrize("table", ["ah_sync_events", "ah_sync_log"])
def test_fresh_timestamp_from_each_sync_table_is_ok(tmp_path, table):
    db_path = tmp_path / f"fresh-{table}.sqlite3"
    _create_sync_db(db_path)
    timestamp = _hours_ago(1)
    with sqlite3.connect(db_path) as connection:
        connection.execute(f"INSERT INTO {table} (created_at) VALUES (?)", (timestamp,))

    result, log, first_seen = _run_watchdog(tmp_path, db_path)

    assert result.returncode == 0
    assert "OK: last Apple Health sync" in log
    assert first_seen.read_text().strip() == timestamp


def test_stale_timestamp_exits_one(tmp_path):
    db_path = tmp_path / "stale.sqlite3"
    _create_sync_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO ah_sync_events (created_at) VALUES (?)", (_hours_ago(48),))

    result, log, _ = _run_watchdog(tmp_path, db_path)

    assert result.returncode == 1
    assert "STALE: last Apple Health sync" in log
    assert "threshold 36h" in log


def test_malformed_timestamp_exits_three(tmp_path):
    db_path = tmp_path / "malformed.sqlite3"
    _create_sync_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO ah_sync_log (created_at) VALUES ('not-a-timestamp')")

    result, log, _ = _run_watchdog(tmp_path, db_path)

    assert result.returncode == 3
    assert "WARN: could not parse last_sync='not-a-timestamp'" in log


@pytest.mark.parametrize("missing_table", ["ah_sync_events", "ah_sync_log"])
def test_query_error_from_each_sync_table_exits_four(tmp_path, missing_table):
    db_path = tmp_path / f"missing-{missing_table}.sqlite3"
    other_table = "ah_sync_log" if missing_table == "ah_sync_events" else "ah_sync_events"
    with sqlite3.connect(db_path) as connection:
        connection.execute(f"CREATE TABLE {other_table} (created_at TEXT)")

    result, log, _ = _run_watchdog(tmp_path, db_path)

    assert result.returncode == 4
    assert f"ERROR: failed to query {missing_table}" in log


def test_stale_after_hours_override_controls_threshold(tmp_path):
    db_path = tmp_path / "override.sqlite3"
    _create_sync_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO ah_sync_events (created_at) VALUES (?)", (_hours_ago(2),))

    result, log, _ = _run_watchdog(tmp_path, db_path, STALE_AFTER_HOURS="1")

    assert result.returncode == 1
    assert "STALE: last Apple Health sync" in log
    assert "threshold 1h" in log
