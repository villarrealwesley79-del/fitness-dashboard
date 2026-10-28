"""Reviewer checks for PR #270: legacy body history must survive reads and writes.

Rows mirror the shapes found in the owner's real data_body.json (float weights,
blank tape strings, left/right "a/b" arm and leg strings, a stored weight_change)
plus older passthrough shapes the add route used to accept.
"""
from __future__ import annotations

import copy
import json

import pytest

import app as module


LEGACY_ROWS = [
    {"date": "2022-10-13", "weight_lbs": 191.2, "body_fat_pct": None, "neck_in": None,
     "waist_in": None, "chest_in": None, "hips_in": None, "arms": "", "legs": "",
     "notes": "", "created_at": "2022-10-13T08:00:00"},
    {"date": "2024-05-01", "weight_lbs": 188.0, "body_fat_pct": 23.5, "neck_in": 16.0,
     "waist_in": 36.5, "chest_in": 42, "hips_in": 40.0, "arms": "11.37/13.62",
     "legs": "22.08/23.85", "notes": "tape day", "created_at": "2024-05-01T08:00:00",
     "weight_change": -0.4},
    {"date": "2026-03-06", "weight_lbs": 185.7, "body_fat_pct": 22.0, "neck_in": None,
     "waist_in": None, "chest_in": None, "hips_in": None, "arms": "", "legs": "",
     "notes": "", "created_at": "2026-03-06T13:20:08", "weight_change": 1.3},
    # Older passthrough shapes the pre-FIT-279 add route could have stored.
    {"date": "2025-01-02", "weight_lbs": "186.4", "body_fat_pct": "22.9"},
    {"date": "2025-01-03T07:30:00", "weight_lbs": 186.0},
]


@pytest.fixture
def legacy_client(monkeypatch, tmp_path):
    body_file = tmp_path / "data_body.json"
    body_file.write_text(json.dumps(LEGACY_ROWS), encoding="utf-8")
    monkeypatch.setattr(module, "BODY_FILE", str(body_file))
    monkeypatch.setattr(module, "BODY_DATA", copy.deepcopy(LEGACY_ROWS))
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    return module.app.test_client(), body_file


def test_body_history_keeps_every_legacy_row(legacy_client):
    client, _ = legacy_client
    history = client.get("/api/body-history").get_json()["history"]
    assert len(history) == len(LEGACY_ROWS)
    assert sorted(row["created_at"] or "" for row in history if row.get("created_at")) == sorted(
        row["created_at"] for row in LEGACY_ROWS if row.get("created_at")
    )
    weights = sorted(row["weight_lbs"] for row in history)
    assert weights == sorted(float(row["weight_lbs"]) for row in LEGACY_ROWS)


def test_body_history_reads_do_not_rewrite_file_or_memory(legacy_client):
    client, body_file = legacy_client
    before = body_file.read_bytes()
    client.get("/api/body-history")
    client.get("/api/body-recomp")
    client.get("/api/vitals")
    assert body_file.read_bytes() == before
    assert module.BODY_DATA == LEGACY_ROWS


def test_add_measurement_preserves_existing_rows_byte_for_byte(legacy_client):
    client, body_file = legacy_client
    response = client.post(
        "/api/add-body-measurement",
        json={"date": "2026-07-11", "weight_lbs": 184.2},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 200
    saved = json.loads(body_file.read_text(encoding="utf-8"))
    assert saved[: len(LEGACY_ROWS)] == LEGACY_ROWS
    assert len(saved) == len(LEGACY_ROWS) + 1


def test_body_history_still_shows_left_right_tape_strings(legacy_client):
    """Finding demonstrator: the real file has 'a/b' arm and leg values."""
    client, _ = legacy_client
    history = client.get("/api/body-history").get_json()["history"]
    tape_day = next(row for row in history if row.get("notes") == "tape day")
    assert tape_day["arms"] == "11.37/13.62"
    assert tape_day["legs"] == "22.08/23.85"
