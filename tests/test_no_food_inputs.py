"""PER-1582: food logging is gone and the workout engine has no food input."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FOOD_TERMS = re.compile(
    r"meal|nutrition|food_log|branded_food|nutritionix|usda_fdc|open_food|heb_product"
    r"|personal_vocab|barcode|workout_adaptation"
)


@pytest.fixture
def empty_app(isolated_app, monkeypatch):
    for name in ("WORKOUTS", "SORENESS_DATA", "CARDIO_DATA", "RECOVERY_DATA", "BODY_DATA", "SLEEP_DATA"):
        monkeypatch.setattr(isolated_app, name, [])
    monkeypatch.setattr(isolated_app, "BASELINES_DATA", {})
    monkeypatch.setattr(isolated_app, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(isolated_app, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(isolated_app, "_cached_wttr", lambda *_a, **_kw: {"available": False})
    return isolated_app


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_dashboard_and_smart_recommendation_have_no_food_fields(empty_app):
    client = empty_app.app.test_client()
    dashboard = client.get("/api/dashboard")
    smart = client.get("/api/recommendation/smart")

    assert dashboard.status_code == 200
    assert smart.status_code == 200
    for response in (dashboard, smart):
        payload = response.get_json()
        assert "nutrition_today" not in payload
        assert "nutrition_context" not in payload
        assert "workout_adaptation_events" not in payload
        assert not [key for key in _walk_keys(payload) if FOOD_TERMS.search(str(key))]
    assert "food" not in smart.get_json()["recommendation_sources"]["source_proof"]
    assert "food" not in smart.get_json()["freshness"]


def test_next_workout_matches_base_plan_with_no_food_logged(empty_app, monkeypatch):
    base = empty_app.generate_next_workout(
        [], [], training_recommendation="train", consume_cardio_rotation=False
    )
    monkeypatch.setattr(empty_app, "generate_next_workout", lambda *_a, **_kw: json.loads(json.dumps(base)))
    client = empty_app.app.test_client()

    plan = client.get("/api/dashboard").get_json()["next_workout"]

    assert plan["estimated_minutes"] == base["estimated_minutes"]
    assert [ex["target_sets"] for ex in plan["exercises"]] == [ex["target_sets"] for ex in base["exercises"]]


def test_no_food_logged_is_never_under_fueled_and_never_cuts_volume(empty_app):
    client = empty_app.app.test_client()
    first = client.get("/api/recommendation/smart").get_json()["next_workout"]
    second = client.get("/api/recommendation/smart").get_json()["next_workout"]

    assert first["estimated_minutes"] == second["estimated_minutes"]
    assert not any("fuel" in str(key).lower() for key in _walk_keys(first))
    assert not hasattr(empty_app, "_nutrition_context_for_date")


def test_old_backup_with_food_keys_imports_and_ignores_them(empty_app, tmp_path):
    client = empty_app.app.test_client()
    backup = {
        "version": "1.1",
        "data": {
            "workouts": [],
            "nutrition": [{"date": "2026-01-01", "calories": 2000}],
            "food_logs": [{"client_id": "old-1", "date": "2026-01-01", "calories": 500}],
            "meal_acceptance_events": [{"meal_id": "m1"}],
            "meal_review_snapshots": [{"meal_id": "m1"}],
            "personal_vocab": [{"normalized_input": "x"}],
            "settings": {"daily_calorie_target": 2200, "daily_protein_target_g": 150},
        },
    }

    response = client.post("/api/import-backup", json=backup)

    assert response.status_code == 200, response.get_json()
    imported = response.get_json()["imported"]
    assert not [key for key in imported if FOOD_TERMS.search(key)]
    exported = client.get("/api/export-backup").get_json()["data"]
    assert not [key for key in exported if FOOD_TERMS.search(key)]
    assert "daily_calorie_target" not in exported["settings"]
    assert "daily_protein_target_g" not in exported["settings"]


def test_food_routes_are_gone(empty_app):
    client = empty_app.app.test_client()
    for path in (
        "/api/nutrition-today",
        "/api/nutrition-history",
        "/api/food-logs/by-date/2026-01-01",
        "/api/workout-adaptation-events",
        "/api/personal-vocab",
        "/api/food-log-refresh-events",
    ):
        assert client.get(path).status_code == 404, path
    assert client.post("/api/add-nutrition", json={}).status_code == 404
    assert client.post("/api/meal-intake", json={}).status_code == 404


def test_settings_have_no_calorie_or_protein_targets(empty_app):
    client = empty_app.app.test_client()
    payload = client.get("/api/settings").get_json()
    assert "daily_calorie_target" not in payload
    assert "daily_protein_target_g" not in payload
    assert "daily_calorie_target" not in empty_app.DEFAULT_SETTINGS


def test_shipped_code_has_no_food_terms():
    offenders = []
    for path in ROOT.glob("*.py"):
        if FOOD_TERMS.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    for rel in ("templates/index.html", "static/js/app.js", "static/css/style.css"):
        if FOOD_TERMS.search((ROOT / rel).read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == []


def test_init_data_db_leaves_existing_food_tables_untouched(tmp_path, monkeypatch):
    import data_store

    db_path = tmp_path / "fitness_data.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE food_logs (id INTEGER PRIMARY KEY, user_id INTEGER, client_id TEXT)")
    conn.executemany(
        "INSERT INTO food_logs (user_id, client_id) VALUES (1, ?)", [("dup",), ("dup",)]
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))

    data_store.init_data_db()

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*), COUNT(client_id) FROM food_logs").fetchone() == (2, 2)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"body_data", "current_workout_plans"} <= tables
