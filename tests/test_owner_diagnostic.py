import json
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTIC = REPO_ROOT / "support" / "owner_diagnostic.py"


def _auth_db(tmp_path):
    db_path = tmp_path / "auth.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                salt TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO users (id, username, password, salt) VALUES (?, ?, ?, ?)",
            [
                (3, "stale-owner", "secret-hash-3", "secret-salt-3"),
                (8, "real-owner", "secret-hash-8", "secret-salt-8"),
            ],
        )
    return db_path


def test_diagnostic_reports_default_owner_without_auth_secrets(tmp_path):
    result = subprocess.run(
        [sys.executable, str(DIAGNOSTIC), "--db", str(_auth_db(tmp_path))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": str(Path(sys.executable).parent)},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "accounts": [
            {"id": 3, "username": "stale-owner"},
            {"id": 8, "username": "real-owner"},
        ],
        "owner": {"id": 3, "username": "stale-owner"},
        "selection": "minimum_user_id",
        "single_user_mode": True,
        "status": "selected",
        "user_count": 2,
    }
    assert "secret-hash" not in result.stdout
    assert "secret-salt" not in result.stdout


def test_diagnostic_reports_valid_configured_owner(tmp_path):
    result = subprocess.run(
        [sys.executable, str(DIAGNOSTIC), "--db", str(_auth_db(tmp_path))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": str(Path(sys.executable).parent),
            "FITNESS_DASHBOARD_OWNER_USER_ID": "8",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["owner"] == {"id": 8, "username": "real-owner"}
    assert payload["selection"] == "configured_user_id"
    assert payload["status"] == "selected"


def test_diagnostic_reports_invalid_owner_configuration_without_echoing_value(tmp_path):
    result = subprocess.run(
        [sys.executable, str(DIAGNOSTIC), "--db", str(_auth_db(tmp_path))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": str(Path(sys.executable).parent),
            "FITNESS_DASHBOARD_OWNER_USER_ID": "not-an-id-secret",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["owner"] is None
    assert payload["selection"] == "configured_user_id"
    assert payload["status"] == "invalid_configuration"
    assert "not-an-id-secret" not in result.stdout


def test_diagnostic_reports_configured_owner_missing_from_database(tmp_path):
    result = subprocess.run(
        [sys.executable, str(DIAGNOSTIC), "--db", str(_auth_db(tmp_path))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": str(Path(sys.executable).parent),
            "FITNESS_DASHBOARD_OWNER_USER_ID": "99",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["owner"] is None
    assert payload["selection"] == "configured_user_id"
    assert payload["status"] == "configured_user_missing"


def test_diagnostic_does_not_create_configured_data_directory(tmp_path):
    missing_data_dir = tmp_path / "must-not-be-created"
    result = subprocess.run(
        [sys.executable, str(DIAGNOSTIC), "--db", str(_auth_db(tmp_path))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": str(Path(sys.executable).parent),
            "DATA_DIR": str(missing_data_dir),
        },
    )

    assert result.returncode == 0, result.stderr
    assert not missing_data_dir.exists()
