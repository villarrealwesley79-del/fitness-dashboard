"""PER-1583: the in-app AI is gone. Adjust is a manual swap or preset edit,
it never opens a socket, and the old AI routes are 404."""

from __future__ import annotations

import copy
import importlib
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import data_store

ROOT = Path(__file__).resolve().parents[1]


def _plan(module):
    return {
        "cardio": {"include_cardio": False, "duration_minutes": 0},
        "id": "per-1583-plan",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "goal_name": "Hypertrophy",
        "estimated_minutes": 45,
        "mesocycle": {"week": 1},
        "exercises": [
            {"exercise": "Chest Press", "muscle": "chest", "target_sets": 3,
             "target_reps": 10, "target_weight": 100, "rpe_target": 7},
            {"exercise": "Lat Pulldown", "muscle": "back", "target_sets": 3,
             "target_reps": 10, "target_weight": 90, "rpe_target": 7},
        ],
    }


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "per1583-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update({"training_goal": module.TrainingGoal.HYPERTROPHY.value,
                     "available_time_minutes": 60,
                     "equipment_preference": "machines_and_cables"})
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_k: _plan(module))

    def no_socket(*_args, **_kwargs):
        raise AssertionError("adjust must not open a socket")

    # Stricter than the autouse guard: even loopback is refused.
    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", no_socket)
    test_client = module.app.test_client()
    assert test_client.get("/api/next-workout").status_code == 200
    return test_client


def _adjust(client, body):
    return client.post("/api/workout/adjust", json=body)


def test_swap_via_adjust_returns_edited_plan_without_a_socket(client):
    response = _adjust(client, {"constraint": "pectoral fly"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["result_kind"] == "changed"
    assert [ex["exercise"] for ex in payload["recommendation"]["exercises"]] == ["Pec Fly", "Lat Pulldown"]


def test_set_edit_via_adjust_returns_edited_plan_without_a_socket(client):
    response = _adjust(client, {
        "constraint": "Low energy: lower RPE and sets",
        "intent": {"rpe_delta": -1, "sets_delta_pct": -20},
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result_kind"] == "changed"
    for exercise in payload["recommendation"]["exercises"]:
        assert exercise["target_sets"] == 2
        assert exercise["rpe_target"] == 6.0


def test_unrecognised_text_leaves_plan_unchanged(client):
    response = _adjust(client, {"constraint": "make this easier"})

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["status"] == "fallback"
    assert [ex["target_sets"] for ex in payload["recommendation"]["exercises"]] == [3, 3]


def test_minimum_time_box_does_not_empty_the_plan(client):
    response = _adjust(client, {"constraint": "10 minutes", "intent": {"duration_cap_min": 10}})

    assert response.status_code == 200
    assert len(client.get("/api/next-workout").get_json()["next_workout"]["exercises"]) >= 1


def test_drop_cardio_on_a_plan_without_cardio_reports_nothing(client):
    response = _adjust(client, {"constraint": "No cardio today", "intent": {"drop_cardio": True}})

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["result_kind"] != "changed"
    assert not any("Cardio" in note for note in payload.get("applied_notes") or [])


@pytest.mark.parametrize("intent", [
    "rpe_delta",                      # not an object
    {"rpe_delta": 5},                 # outside the allowed range
    {"sets_delta_pct": "20"},         # wrong type
    {"drop_cardio": 1},               # not a bool
    {"duration_cap_min": 1},          # below the minimum time box
    {"avoid_muscles": "shoulders"},   # not a list
    {"swap": []},                     # field clients may not send
])
def test_adjust_rejects_malformed_intent(client, intent):
    response = _adjust(client, {"constraint": "x", "intent": intent})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_intent"


@pytest.mark.parametrize("method,path", [
    ("get", "/api/ai/health"),
    ("get", "/api/ai/metrics"),
    ("get", "/api/ai/facts/context"),
    ("post", "/api/ai/facts/query"),
    ("post", "/api/workout/analyze"),
])
def test_removed_ai_routes_are_404(client, method, path):
    assert getattr(client, method)(path, json={}).status_code == 404


def test_importing_app_starts_no_background_thread(tmp_path):
    code = (
        "import threading\n"
        "before = {t.name for t in threading.enumerate()}\n"
        "import app\n"
        "print(sorted({t.name for t in threading.enumerate()} - before))\n"
    )
    env = {**os.environ, "DATA_DIR": str(tmp_path), "TMPDIR": str(tmp_path), "SECRET_KEY": "x"}
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "[]"
