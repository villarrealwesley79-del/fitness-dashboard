"""Reviewer checks for PR #341: Navy tape flow keeps history and data shape."""
from __future__ import annotations

import copy
import json
import math

import app as fitness_app


EXISTING = [
    {"date": "2024-05-01", "weight_lbs": 188.0, "body_fat_pct": 23.5, "neck_in": 16.0,
     "waist_in": 36.5, "chest_in": 42, "hips_in": 40.0, "arms": "11.37/13.62",
     "legs": "22.08/23.85", "notes": "", "created_at": "2024-05-01T08:00:00"},
]


def _client(monkeypatch, tmp_path):
    body_file = tmp_path / "body.json"
    body_file.write_text(json.dumps(EXISTING), encoding="utf-8")
    monkeypatch.setattr(fitness_app, "BODY_DATA", copy.deepcopy(EXISTING))
    monkeypatch.setattr(fitness_app, "BODY_FILE", str(body_file))
    monkeypatch.setitem(fitness_app.app.config, "TESTING", True)
    monkeypatch.setitem(fitness_app.app.config, "LOGIN_DISABLED", True)
    return fitness_app.app.test_client(), body_file


def test_navy_male_formula_matches_published_inch_equation(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    response = client.post("/api/body/navy-calc", json={"sex": "male", "height_in": 70, "neck_in": 16, "waist_in": 34})
    expected = 86.010 * math.log10(34 - 16) - 70.041 * math.log10(70) + 36.76
    assert response.status_code == 200
    assert response.get_json()["body_fat_pct"] == round(expected, 2)


def test_navy_save_appends_without_touching_existing_rows(monkeypatch, tmp_path):
    client, body_file = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/add-body-measurement",
        json={"date": "2026-07-11", "weight_lbs": 184.0, "body_fat_pct": 18.2,
              "sex": "male", "height_in": 70, "neck_in": 16, "waist_in": 34, "hip_in": None},
    )
    assert response.status_code == 200
    saved = json.loads(body_file.read_text(encoding="utf-8"))
    assert saved[0] == EXISTING[0]
    assert saved[-1]["neck_in"] == 16.0 and saved[-1]["waist_in"] == 34.0


def test_female_navy_save_uses_existing_hips_in_key(monkeypatch, tmp_path):
    """Finding demonstrator: history uses hips_in; Navy saves write hip_in."""
    client, body_file = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/add-body-measurement",
        json={"date": "2026-07-11", "weight_lbs": 150.0, "sex": "female",
              "height_in": 65, "neck_in": 13, "waist_in": 30, "hip_in": 40},
    )
    assert response.status_code == 200
    saved = json.loads(body_file.read_text(encoding="utf-8"))
    assert saved[-1].get("hips_in") == 40.0
