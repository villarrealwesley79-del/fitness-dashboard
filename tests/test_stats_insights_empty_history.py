from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit148-stats-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "RECOVERY_DATA", [])
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _insight_text(payload: dict) -> str:
    return " ".join(
        f"{item.get('title', '')} {item.get('detail', '')}"
        for item in payload.get("insights", [])
    )


def _workout(date: str, machine: str, muscle_group: str) -> dict:
    return {
        "date": date,
        "session_type": "strength",
        "exercises": [
            {
                "machine": machine,
                "muscle_group": muscle_group,
                "sets": [
                    {"set_number": 1, "weight_lbs": 100, "reps": 10, "rpe": 7},
                ],
            }
        ],
    }


def _run_fresh_import(script: str) -> dict:
    env = os.environ.copy()
    env.setdefault("SECRET_KEY", "fit148-subprocess-secret")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_stats_cold_open_does_not_seed_sample_workouts():
    script = r'''
import json
import os

real_exists = os.path.exists

def cold_open_exists(path):
    basename = os.path.basename(path)
    if basename in {"data_workouts.json", "data_soreness.json"}:
        return False
    if path.endswith("support/Workout_Log_LogTab_Past_Workouts.md"):
        return False
    return real_exists(path)

os.path.exists = cold_open_exists
os.environ.pop("FIT_LOAD_SAMPLE_DATA", None)

import app

app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
insights = app.app.test_client().get("/api/insights").get_json()
history = app.app.test_client().get("/api/history-all").get_json()
print(json.dumps({
    "workout_count": len(app.WORKOUTS),
    "soreness_count": len(app.SORENESS_DATA),
    "history_workouts": history.get("workouts"),
    "insights": insights.get("insights"),
    "charts": insights.get("charts"),
}))
'''
    payload = _run_fresh_import(script)

    assert payload["workout_count"] == 0
    assert payload["soreness_count"] == 0
    assert payload["history_workouts"] == []
    assert payload["insights"] == []
    assert payload["charts"]["e1rm_trends"] == []
    assert payload["charts"]["muscle_volume"] == []
    assert payload["charts"]["push_pull"] == {"push": 0, "pull": 0}


def test_stats_sample_workouts_require_explicit_opt_in():
    script = r'''
import json
import os

real_exists = os.path.exists

def cold_open_exists(path):
    basename = os.path.basename(path)
    if basename in {"data_workouts.json", "data_soreness.json"}:
        return False
    if path.endswith("support/Workout_Log_LogTab_Past_Workouts.md"):
        return False
    return real_exists(path)

os.path.exists = cold_open_exists
os.environ["FIT_LOAD_SAMPLE_DATA"] = "1"

import app

print(json.dumps({
    "workout_count": len(app.WORKOUTS),
    "soreness_count": len(app.SORENESS_DATA),
}))
'''
    payload = _run_fresh_import(script)

    assert payload["workout_count"] > 0
    assert payload["soreness_count"] > 0


def test_stats_progress_insights_guarded_on_no_history(fitness_app):
    response = fitness_app.app.test_client().get("/api/insights")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["insights"] == []
    assert payload["charts"]["e1rm_trends"] == []
    assert payload["charts"]["muscle_volume"] == []
    assert payload["charts"]["push_pull"] == {"push": 0, "pull": 0}


def test_stats_last_workout_elapsed_guarded_on_no_history(fitness_app):
    response = fitness_app.app.test_client().get("/api/insights")

    assert response.status_code == 200, response.get_data(as_text=True)
    text = _insight_text(response.get_json())
    assert "Time to train!" not in text
    assert "days since last workout" not in text
    assert "Push/Pull imbalance" not in text


def test_stats_progress_insights_require_repeated_exercise_history(fitness_app):
    fitness_app.WORKOUTS.extend([
        _workout("2026-03-01", "Chest Press", "chest"),
        _workout("2026-03-03", "Lat Pulldown", "back"),
    ])

    response = fitness_app.app.test_client().get("/api/insights")

    assert response.status_code == 200, response.get_data(as_text=True)
    text = _insight_text(response.get_json())
    assert "exercises progressing" not in text
    assert "Chest Press" not in text
    assert "Lat Pulldown" not in text
