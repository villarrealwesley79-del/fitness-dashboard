from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "remove_synthetic_history_rows.py"


def _run(args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_cleanup_script_removes_only_explicit_runtime_rows(tmp_path):
    rows = [
        {"id": "synthetic-a", "date": "2026-04-12", "session_type": "push", "total_volume": 1000},
        {"id": "synthetic-b", "date": "2026-04-13", "session_type": "pull", "total_volume": 2000},
        {"id": "real-row", "date": "2026-04-13", "session_type": "legs", "total_volume": 3000},
        {"id": "later-row", "date": "2026-04-20", "session_type": "push", "total_volume": 4000},
    ]
    (tmp_path / "data_workouts.json").write_text(json.dumps(rows))
    (tmp_path / "data_cardio.json").write_text("[]")
    (tmp_path / "data_recovery.json").write_text("[]")

    dry_run = _run([
        "--data-dir",
        str(tmp_path),
        "--remove",
        "workout:synthetic-a",
        "--remove",
        "workout:synthetic-b",
        "--expect-count",
        "2",
    ])

    assert dry_run.returncode == 0, dry_run.stderr
    assert "Dry run: would remove 2 explicit row(s)." in dry_run.stdout
    assert len(json.loads((tmp_path / "data_workouts.json").read_text())) == 4

    applied = _run([
        "--data-dir",
        str(tmp_path),
        "--remove",
        "workout:synthetic-a",
        "--remove",
        "workout:synthetic-b",
        "--expect-count",
        "2",
        "--apply",
    ])

    assert applied.returncode == 0, applied.stderr
    remaining = json.loads((tmp_path / "data_workouts.json").read_text())
    assert [row["id"] for row in remaining] == ["real-row", "later-row"]
    assert sum(row["total_volume"] for row in remaining) == 7000


def test_no_tracked_runtime_fixture_rows_for_apr_12_14():
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.splitlines()
    data_files = [
        path for path in tracked
        if path in {"data_workouts.json", "data_cardio.json", "data_recovery.json"}
        or path.startswith("fixtures/")
        or path.startswith("tests/fixtures/")
    ]

    assert data_files == []
