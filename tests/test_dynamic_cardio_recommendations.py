from __future__ import annotations

import copy
import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    module = importlib.import_module("app")
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update(
        {
            "training_goal": module.TrainingGoal.WEIGHT_LOSS.value,
            "available_time_minutes": 120,
            "equipment_preference": "machines_only",
        }
    )
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "CARDIO_ROTATION_CURSOR", {})
    monkeypatch.setattr(module, "_WEATHER_CACHE", {"location": "San_Antonio"})
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "_fetch_wttr", lambda *_args, **_kwargs: {"available": False})
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    return module


def _cardio_type(recommendation):
    cardio = recommendation.get("cardio") or {}
    assert cardio.get("type"), recommendation
    return cardio["type"]


def test_same_goal_rotates_cardio_without_completed_cardio(fitness_app):
    recommendations = [
        fitness_app.generate_next_workout([], [], goal=fitness_app.TrainingGoal.WEIGHT_LOSS.value, available_time=120)
        for _ in range(3)
    ]

    modalities = [_cardio_type(rec) for rec in recommendations]
    assert len(set(modalities)) >= 2
    assert "rail" not in (recommendations[1]["cardio"].get("technique") or "").lower()


def test_recovery_readiness_forces_zone_two_walk_class_cardio(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: 60)

    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.WEIGHT_LOSS.value,
        available_time=120,
        training_recommendation="recovery",
    )

    cardio = recommendation["cardio"]
    assert cardio["zone"] == "Zone 2"
    assert cardio["heart_rate_range"] == "114-133 BPM"
    assert "walk" in cardio["type"].lower()
    assert 20 <= cardio["duration_minutes"] <= 30


def test_final_intensity_signal_forces_zone_four_even_with_mid_readiness(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: 78)

    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.WEIGHT_LOSS.value,
        available_time=120,
        training_recommendation="intensity",
    )

    cardio = recommendation["cardio"]
    assert cardio["zone"] == "Zone 4"
    assert cardio["heart_rate_range"] == "152-171 BPM"


def test_final_intensity_signal_overrides_raw_low_readiness(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: 60)

    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.WEIGHT_LOSS.value,
        available_time=120,
        training_recommendation="intensity",
    )

    cardio = recommendation["cardio"]
    assert cardio["zone"] == "Zone 4"
    assert cardio["heart_rate_range"] == "152-171 BPM"


def test_final_moderate_signal_overrides_raw_low_readiness(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: 65)

    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.WEIGHT_LOSS.value,
        available_time=120,
        training_recommendation="moderate",
    )

    cardio = recommendation["cardio"]
    assert cardio["zone"] == "Zone 3"
    assert cardio["heart_rate_range"] == "133-152 BPM"


def test_hidden_context_generation_does_not_consume_cardio_rotation(fitness_app):
    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.WEIGHT_LOSS.value,
        available_time=120,
        consume_cardio_rotation=False,
    )

    assert _cardio_type(recommendation)
    assert fitness_app.CARDIO_ROTATION_CURSOR == {}


def test_machine_only_endurance_cardio_stays_machine_safe(fitness_app):
    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.ENDURANCE.value,
        available_time=120,
    )

    assert "outdoor" not in _cardio_type(recommendation).lower()


def test_machine_and_cables_endurance_cardio_stays_gym_safe(fitness_app, monkeypatch):
    settings = dict(fitness_app.USER_SETTINGS)
    settings["equipment_preference"] = "machines_and_cables"
    monkeypatch.setattr(fitness_app, "USER_SETTINGS", settings)

    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.ENDURANCE.value,
        available_time=120,
    )

    assert "outdoor" not in _cardio_type(recommendation).lower()


def test_recent_stairmaster_history_prefers_different_cardio(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "CARDIO_DATA",
        [
            {"date": "2026-05-20", "activity_type": "Stairmaster", "duration_minutes": 20},
            {"date": "2026-05-19", "activity_type": "Bike", "duration_minutes": 20},
            {"date": "2026-05-18", "activity_type": "Stairmaster", "duration_minutes": 15},
        ],
    )

    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.WEIGHT_LOSS.value,
        available_time=120,
    )

    assert _cardio_type(recommendation) != "Stairmaster"


def test_recent_repeated_non_default_cardio_is_also_avoided(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "CARDIO_DATA",
        [
            {"date": "2026-05-20", "activity_type": "Bike", "duration_minutes": 20},
            {"date": "2026-05-19", "activity_type": "Stairmaster", "duration_minutes": 20},
            {"date": "2026-05-18", "activity_type": "Bike", "duration_minutes": 15},
        ],
    )
    monkeypatch.setattr(
        fitness_app,
        "CARDIO_ROTATION_CURSOR",
        {fitness_app.TrainingGoal.WEIGHT_LOSS.value: 0},
    )

    recommendation = fitness_app.generate_next_workout(
        [],
        [],
        goal=fitness_app.TrainingGoal.WEIGHT_LOSS.value,
        available_time=120,
    )

    assert _cardio_type(recommendation) != "Bike"


@pytest.mark.parametrize(
    ("activity_type", "muscle"),
    [
        ("Bike", "quads"),
        ("Rower", "back"),
        ("Treadmill incline walk", "calves"),
    ],
)
def test_dynamic_cardio_labels_count_toward_followup_fatigue(fitness_app, activity_type, muscle):
    impact = fitness_app.get_cardio_muscle_impact(
        [
            {
                "date": fitness_app._today_str(),
                "activity_type": activity_type,
                "duration_minutes": 30,
                "intensity": 5,
            }
        ],
        muscle,
    )

    assert impact > 0


def test_dashboard_high_readiness_surfaces_intensity_cardio(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "get_oura_daily",
        lambda *_args, **_kwargs: {"readiness_score": 90},
    )
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: 90)
    monkeypatch.setattr(
        fitness_app,
        "_compute_data_freshness",
        lambda *_args, **_kwargs: {
            "oura": {"status": "fresh"},
            "apple_health": {"status": "fresh"},
            "food": {"status": "missing"},
        },
    )
    monkeypatch.setattr(fitness_app, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0})
    monkeypatch.setattr(fitness_app, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})

    response = fitness_app.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    cardio = response.get_json()["next_workout"]["cardio"]
    assert cardio["zone"] == "Zone 4"
    assert cardio["heart_rate_range"] == "152-171 BPM"
    assert fitness_app.CARDIO_ROTATION_CURSOR == {}

    response = fitness_app.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    assert fitness_app.CARDIO_ROTATION_CURSOR == {}


def test_dashboard_readiness_computes_each_volume_muscle_once(fitness_app, monkeypatch):
    volume = {
        "quads": {
            "sets": 8,
            "status": "Minimum Effective",
            "status_color": "yellow",
            "last_trained": "2026-08-01",
        },
        "back": {
            "sets": 12,
            "status": "Optimal",
            "status_color": "green",
            "last_trained": "2026-08-02",
        },
    }
    readiness = {
        "quads": {"score": 8, "color": "green"},
        "back": {"score": 6, "color": "yellow"},
    }
    calls = []

    def fake_readiness(muscle, *_args):
        calls.append(muscle)
        return readiness[muscle]

    monkeypatch.setattr(fitness_app, "calculate_volume", lambda *_args, **_kwargs: volume)
    monkeypatch.setattr(fitness_app, "calculate_progression_status", lambda *_args: {})
    monkeypatch.setattr(fitness_app, "get_readiness_score", fake_readiness)
    monkeypatch.setattr(
        fitness_app,
        "generate_next_workout",
        lambda *_args, **_kwargs: {"name": "Test", "focus": "full_body", "exercises": [], "cardio": {}},
    )
    monkeypatch.setattr(fitness_app, "_persist_current_workout_plan", lambda plan, *_args: plan)

    response = fitness_app.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()
    assert calls == ["quads", "back"]
    assert payload["headline"]["avg_readiness"] == 7.0
    assert [(row["muscle"], row["readiness"]) for row in payload["muscles"]] == [
        ("Quads", 8),
        ("Back", 6),
    ]


def test_dashboard_missing_readiness_does_not_force_recovery_cardio(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fitness_app,
        "_compute_data_freshness",
        lambda *_args, **_kwargs: {
            "oura": {"status": "missing"},
            "apple_health": {"status": "missing"},
            "food": {"status": "missing"},
        },
    )
    monkeypatch.setattr(fitness_app, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0})
    monkeypatch.setattr(fitness_app, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})

    response = fitness_app.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    cardio = response.get_json()["next_workout"]["cardio"]
    assert cardio["zone"] == "Zone 3"


def test_dashboard_recomp_command_uses_null_for_unavailable_readiness(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fitness_app,
        "_compute_data_freshness",
        lambda *_args, **_kwargs: {
            "oura": {"status": "missing"},
            "apple_health": {"status": "missing"},
            "food": {"status": "missing"},
        },
    )
    monkeypatch.setattr(fitness_app, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0})
    monkeypatch.setattr(fitness_app, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})

    response = fitness_app.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    command = response.get_json()["recomp_command"]
    assert command["readiness"] is None
    assert "Readiness unavailable" in command["reason"]


def test_dashboard_recomp_command_preserves_real_zero_readiness(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args, **_kwargs: {"readiness_score": 0})
    monkeypatch.setattr(
        fitness_app,
        "_compute_data_freshness",
        lambda *_args, **_kwargs: {
            "oura": {"status": "fresh"},
            "apple_health": {"status": "missing"},
            "food": {"status": "missing"},
        },
    )
    monkeypatch.setattr(fitness_app, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0})
    monkeypatch.setattr(fitness_app, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})

    response = fitness_app.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    command = response.get_json()["recomp_command"]
    assert command["readiness"] == 0
    assert "Readiness 0" in command["reason"]


def test_dashboard_uses_final_smart_signal_for_visible_cardio(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "get_oura_daily",
        lambda *_args, **_kwargs: {"readiness_score": 90},
    )
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: 90)
    monkeypatch.setattr(
        fitness_app,
        "_compute_data_freshness",
        lambda *_args, **_kwargs: {
            "oura": {"status": "fresh"},
            "apple_health": {"status": "fresh"},
            "food": {"status": "missing"},
        },
    )
    monkeypatch.setattr(fitness_app, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 360})
    monkeypatch.setattr(fitness_app, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})

    response = fitness_app.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    cardio = response.get_json()["next_workout"]["cardio"]
    assert cardio["zone"] == "Zone 3"


def test_dashboard_uses_cached_weather_without_blocking_fetch(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "get_oura_daily",
        lambda *_args, **_kwargs: {"readiness_score": 90},
    )
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: 90)
    monkeypatch.setattr(
        fitness_app,
        "_compute_data_freshness",
        lambda *_args, **_kwargs: {
            "oura": {"status": "fresh"},
            "apple_health": {"status": "fresh"},
            "food": {"status": "missing"},
        },
    )
    monkeypatch.setattr(fitness_app, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0})
    monkeypatch.setattr(fitness_app, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        fitness_app,
        "_WEATHER_CACHE",
        {
            "location": "San_Antonio",
            "ts": int(fitness_app.time.time()),
            "data": {"temp_f": 98, "feelslike_f": 98, "humidity_pct": 50, "condition": "Hot"},
        },
    )
    monkeypatch.setattr(
        fitness_app,
        "_fetch_wttr",
        lambda *_args, **_kwargs: pytest.fail("dashboard should not block on live weather fetch"),
    )

    response = fitness_app.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    cardio = response.get_json()["next_workout"]["cardio"]
    assert cardio["zone"] == "Zone 3"


def test_dashboard_recomputes_plan_when_readiness_changes(fitness_app, monkeypatch):
    readiness = {"value": None}
    monkeypatch.setattr(
        fitness_app,
        "get_oura_daily",
        lambda *_args, **_kwargs: (
            {"readiness_score": readiness["value"]} if readiness["value"] is not None else None
        ),
    )
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: readiness["value"])
    monkeypatch.setattr(
        fitness_app,
        "_compute_data_freshness",
        lambda *_args, **_kwargs: {
            "oura": {"status": "fresh"},
            "apple_health": {"status": "fresh"},
            "food": {"status": "missing"},
        },
    )
    monkeypatch.setattr(fitness_app, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0})
    monkeypatch.setattr(fitness_app, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})

    client = fitness_app.app.test_client()
    first = client.get("/api/dashboard")
    assert first.status_code == 200
    assert first.get_json()["next_workout"]["cardio"]["zone"] == "Zone 3"

    readiness["value"] = 90
    second = client.get("/api/dashboard")

    assert second.status_code == 200
    assert second.get_json()["next_workout"]["cardio"]["zone"] == "Zone 4"
