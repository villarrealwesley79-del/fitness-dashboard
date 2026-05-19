from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    workouts = [
        {
            "id": "recent-push",
            "date": "2026-05-10",
            "session_type": "push",
            "total_sets": 3,
            "total_volume": 3600,
            "exercises": [
                {
                    "machine": "Chest Press",
                    "muscle_group": "chest",
                    "sets": [{"weight_lbs": 120, "reps": 10, "rpe": 8}],
                }
            ],
        },
        {
            "id": "target-workout",
            "date": "2026-05-18",
            "created_at": "2026-05-18T15:45:00",
            "session_type": "push",
            "duration_minutes": 52,
            "total_sets": 4,
            "total_volume": 3060,
            "exercises": [
                {
                    "machine": "Chest Press",
                    "muscle_group": "chest",
                    "sets": [
                        {"weight_lbs": 45, "reps": 17, "rpe": 8, "notes": "Felt good"},
                        {"weight_lbs": 45, "reps": 17, "rpe": 8, "notes": "Felt sore at 15"},
                    ],
                },
                {
                    "machine": "Mid Row",
                    "muscle_group": "back",
                    "sets": [
                        {"weight_lbs": 45, "reps": 17, "rpe": 8, "notes": "Sore at 13"},
                        {"weight_lbs": 45, "reps": 17, "rpe": 9, "notes": "Sore at 11 struggled at 15"},
                    ],
                },
            ],
        },
    ]
    monkeypatch.setattr(module, "WORKOUTS", workouts)
    monkeypatch.setattr(module, "USER_SETTINGS", {"training_goal": module.TrainingGoal.HYPERTROPHY.value})
    monkeypatch.setattr(module, "calculate_progression_status", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "get_oura_daily", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_ai_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_ai_cache_put", lambda *_args, **_kwargs: None)
    return module


def test_analyze_returns_deterministic_fallback_when_adapter_missing(fitness_app, monkeypatch):
    metrics = []
    monkeypatch.setattr(fitness_app, "_lm_studio", None)
    monkeypatch.setattr(fitness_app, "_ai_metric_log", lambda *args, **kwargs: metrics.append((args, kwargs)))

    response = fitness_app.app.test_client().post("/api/workout/analyze", json={"latest": True})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "fallback"
    assert payload["analysis"]["summary"]
    assert payload["analysis"]["wins"]
    assert payload["analysis"]["comparison"]
    assert payload["analysis"]["next_session_cue"]
    assert payload["meta"]["analysis_source"] == "deterministic"
    assert payload["meta"]["fallback_reason"] == "LM Studio adapter not available"
    assert payload["context_used"]["set_note_count"] == 4
    assert metrics
    assert metrics[0][0][0] == "fallback"


def test_analyze_returns_deterministic_fallback_when_model_fails(fitness_app, monkeypatch):
    class FakeLmStudio:
        LM_STUDIO_MODEL_VERSION = "fake-model"
        ANALYZE_PROMPT_VERSION = "fake-prompt"

        class LmStudioError(Exception):
            pass

        @staticmethod
        def analyze_workout(_workout, _context):
            raise FakeLmStudio.LmStudioError("primary and fallback unavailable")

    metrics = []
    monkeypatch.setattr(fitness_app, "_lm_studio", FakeLmStudio)
    monkeypatch.setattr(fitness_app, "_ai_metric_log", lambda *args, **kwargs: metrics.append((args, kwargs)))

    response = fitness_app.app.test_client().post("/api/workout/analyze", json={"workout_id": "target-workout"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "fallback"
    assert "LM Studio" in payload["analysis"]["summary"]
    assert payload["meta"]["analysis_source"] == "deterministic"
    assert payload["meta"]["model_version"] == "fake-model"
    assert any("soreness" in concern.lower() or "struggle" in concern.lower() for concern in payload["analysis"]["concerns"])
    assert metrics
    assert metrics[0][0][0] == "fallback"
