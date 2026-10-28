from __future__ import annotations

import importlib

import data_store


def _warning_codes(context):
    return {warning["code"] for warning in context["warnings"]}


def test_dashboard_and_smart_recommendation_share_hard_workout_food_warning(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit48-contract-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    data_store.init_data_db()
    today = module._today_str()
    hard_workout = {
        "estimated_minutes": 45,
        "mesocycle": {"rpe_base": 8},
        "exercises": [],
    }
    food_logs = [
        {
            "date": today,
            "logged_at": f"{today}T08:00:00",
            "calories": 500,
            "protein_g": 20,
            "carbs_g": 55,
            "fat_g": 12,
            "sodium_mg": 400,
            "accepted": True,
        }
    ]

    monkeypatch.setattr(module, "generate_next_workout", lambda *_args, **_kwargs: hard_workout)
    monkeypatch.setattr(module, "_same_day_preserved_workout_plan", lambda _today: None)
    monkeypatch.setattr(module, "get_food_logs", lambda *_args, **_kwargs: food_logs)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "USER_SETTINGS", {"daily_calorie_target": 2200, "daily_protein_target_g": 148})
    monkeypatch.setattr(module, "get_oura_daily", lambda *_args, **_kwargs: {"readiness_score": 82})
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_args, **_kwargs: [{"hrv": 41}])
    monkeypatch.setattr(module, "compute_hrv_trend", lambda *_args, **_kwargs: "stable")
    monkeypatch.setattr(module, "calculate_acwr", lambda *_args, **_kwargs: {"acwr": 1.0})
    monkeypatch.setattr(module, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0, "status": "ok"})
    monkeypatch.setattr(module, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(module, "_fetch_wttr", lambda *_args, **_kwargs: {"available": False})

    client = module.app.test_client()
    dashboard = client.get("/api/dashboard")
    smart = client.get("/api/recommendation/smart")

    assert dashboard.status_code == 200
    assert smart.status_code == 200
    dashboard_context = dashboard.get_json()["nutrition_today"]["coaching_context"]
    smart_context = smart.get_json()["nutrition_context"]
    assert "under_fueled_hard_workout" in _warning_codes(dashboard_context)
    assert "under_fueled_hard_workout" in _warning_codes(smart_context)
