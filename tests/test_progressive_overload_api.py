from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit327-progressive-overload-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _workout(date: str, machine: str, *weights: int) -> dict:
    return {
        "date": date,
        "exercises": [
            {
                "machine": machine,
                "sets": [{"weight_lbs": weight} for weight in weights],
            }
        ],
    }


def test_progressive_overload_documents_api_only_fixed_exercise_scope(fitness_app):
    fitness_app.WORKOUTS.extend(
        [
            _workout("2026-07-01", "Chest Press", 90, 100),
            _workout("2026-07-08", "Chest Press", 105),
            _workout("2026-07-08", "Incline Press", 110),
        ]
    )

    response = fitness_app.app.test_client().get("/api/progressive-overload")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["scope"] == {
        "visibility": "api_only",
        "exercise_source": "fixed_list",
        "tracked_exercises": [
            "Chest Press",
            "Lat Pulldown",
            "Mid Row",
            "Leg Press",
            "Leg Curl",
            "Seated Dip",
            "Shoulder Press",
            "Biceps Curl",
        ],
    }

    by_name = {item["exercise"]: item for item in payload["exercises"]}
    assert by_name["Chest Press"]["history"] == [
        {"date": "2026-07-01", "weight": 100},
        {"date": "2026-07-08", "weight": 105},
    ]
    assert "Incline Press" not in by_name


def test_progressive_overload_empty_history_returns_documented_empty_rows(fitness_app):
    response = fitness_app.app.test_client().get("/api/progressive-overload")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert [item["exercise"] for item in payload["exercises"]] == payload["scope"]["tracked_exercises"]
    assert all(item["history"] == [] for item in payload["exercises"])
    assert all(item["last_weight"] is None for item in payload["exercises"])
