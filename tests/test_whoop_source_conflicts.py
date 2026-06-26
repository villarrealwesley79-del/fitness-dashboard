from __future__ import annotations

import importlib
from datetime import datetime

import pytest

import whoop_store
from whoop_recommendations import build_whoop_recommendation_signals, detect_wearable_source_conflicts


def test_detect_conflict_uses_conservative_source():
    signals = build_whoop_recommendation_signals(
        {
            "recovery_score": 34,
            "score_state": "SCORED",
        },
        freshness={"status": "fresh"},
    )

    conflict = detect_wearable_source_conflicts(
        oura_readiness=88,
        whoop_signals=signals,
    )

    assert conflict["has_conflict"] is True
    assert conflict["conservative_source"] == "whoop"
    assert "conservative" in conflict["explanation"].lower()


@pytest.fixture()
def fitness_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "whoop-conflict-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "whoop-conflict-health-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    module.WHOOP_DB_FILE = str(tmp_path / "whoop.sqlite3")
    module.init_whoop_db(module.WHOOP_DB_FILE)
    return module


def test_whoop_signal_endpoint_and_smart_recommendation_keep_apple_health_load_truth(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "get_oura_daily",
        lambda *_args, **_kwargs: {"readiness_score": 88, "sleep_score": 80, "hrv": 40},
    )
    monkeypatch.setattr(fitness_app, "get_oura_daily_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fitness_app, "compute_hrv_trend", lambda *_args, **_kwargs: "stable")
    monkeypatch.setattr(
        fitness_app,
        "calculate_acwr",
        lambda *_args, **_kwargs: {"acute_load": 0, "chronic_load": 0, "acwr": 0, "risk": "low"},
    )
    monkeypatch.setattr(
        fitness_app,
        "calculate_sleep_debt",
        lambda *_args, **_kwargs: {"debt_minutes": 0, "status": "ok"},
    )
    monkeypatch.setattr(
        fitness_app,
        "calculate_recovery_bonus",
        lambda *_args, **_kwargs: {"bonus_points": 0},
    )
    monkeypatch.setattr(fitness_app, "_cached_wttr", lambda *_args, **_kwargs: {"available": False})
    monkeypatch.setattr(fitness_app, "_food_log_entries_for_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_workout_looks_hard", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        fitness_app,
        "_apply_due_workout_adaptations_for_plan",
        lambda workout, **_kwargs: (workout, []),
    )
    monkeypatch.setattr(fitness_app, "_workout_with_auth_scope", lambda workout: workout)
    monkeypatch.setattr(
        fitness_app,
        "generate_next_workout",
        lambda *_args, **_kwargs: {
            "estimated_minutes": 60,
            "estimated_duration": "60 min",
            "mesocycle": {"volume_multiplier": 1.0},
            "exercises": [{"target_sets": 4, "rpe_target": 8, "rationale": "Base"}],
        },
    )

    whoop_store.upsert_whoop_records(
        fitness_app.WHOOP_DB_FILE,
        "recovery",
        [
            {
                "upstream_id": "rec-1",
                "local_date": "2026-06-25",
                "score_state": "SCORED",
                "recovery_score": 34,
            }
        ],
        imported_at=datetime(2026, 6, 25, 7, 0, 0),
    )
    whoop_store.project_whoop_daily_facts(fitness_app.WHOOP_DB_FILE)

    signal_response = fitness_app.app.test_client().get("/api/whoop/recommendation-signals")
    assert signal_response.status_code == 200
    signal_payload = signal_response.get_json()
    assert signal_payload["source_conflict"]["has_conflict"] is True
    assert signal_payload["signals"]["load_source"] == "apple_health"

    smart_response = fitness_app.app.test_client().get("/api/recommendation/smart")
    assert smart_response.status_code == 200
    smart_payload = smart_response.get_json()
    assert smart_payload["recommendation"] == "recovery"


def test_next_workout_cache_matches_whoop_adjusted_response(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "generate_next_workout",
        lambda *_args, **_kwargs: {
            "estimated_minutes": 60,
            "estimated_duration": "60 min",
            "mesocycle": {"volume_multiplier": 1.0},
            "exercises": [{"target_sets": 4, "rpe_target": 8, "rationale": "Base"}],
        },
    )
    monkeypatch.setattr(fitness_app, "_workout_with_auth_scope", lambda workout: workout)
    monkeypatch.setattr(fitness_app, "_food_log_entries_for_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_workout_looks_hard", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        fitness_app,
        "_apply_due_workout_adaptations_for_plan",
        lambda workout, **_kwargs: (workout, []),
    )
    fitness_app.LAST_WORKOUT_RECOMMENDATION = None
    fitness_app.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None

    whoop_store.upsert_whoop_records(
        fitness_app.WHOOP_DB_FILE,
        "recovery",
        [
            {
                "upstream_id": "rec-1",
                "local_date": fitness_app._today_str(),
                "score_state": "SCORED",
                "recovery_score": 38,
            }
        ],
        imported_at=datetime(2026, 6, 25, 7, 0, 0),
    )
    whoop_store.project_whoop_daily_facts(fitness_app.WHOOP_DB_FILE)

    response = fitness_app.app.test_client().get("/api/next-workout")

    assert response.status_code == 200
    payload = response.get_json()
    adjusted_sets = payload["next_workout"]["exercises"][0]["target_sets"]
    assert adjusted_sets < 4
    assert fitness_app.LAST_WORKOUT_RECOMMENDATION["exercises"][0]["target_sets"] == adjusted_sets

    second_response = fitness_app.app.test_client().get("/api/next-workout")
    second_payload = second_response.get_json()

    assert second_response.status_code == 200
    assert second_payload["next_workout"]["exercises"][0]["target_sets"] == adjusted_sets

    monkeypatch.setattr(
        fitness_app,
        "latest_whoop_freshness",
        lambda *_args, **_kwargs: {
            "status": "stale",
            "last_data_point": fitness_app._today_str(),
            "last_sync_attempt": None,
            "score_state": "SCORED",
            "connected": True,
        },
    )
    stale_response = fitness_app.app.test_client().get("/api/next-workout")
    stale_payload = stale_response.get_json()

    assert stale_response.status_code == 200
    assert stale_payload["next_workout"]["exercises"][0]["target_sets"] == 4
