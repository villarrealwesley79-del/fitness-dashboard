from __future__ import annotations

import importlib


def test_nutrition_context_uses_only_accepted_entries_and_flags_next_day_context(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(
        module,
        "NUTRITION_DATA",
        [
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T21:10:00",
                "calories": 1200,
                "protein_g": 40,
                "carbs_g": 100,
                "fat_g": 50,
                "sodium_mg": 2600,
            },
            {
                "date": "2026-05-18",
                "status": "pending_review",
                "calories": 800,
                "protein_g": 60,
                "sodium_mg": 300,
            },
        ],
    )

    context = module._nutrition_context_for_date("2026-05-18", hard_training_planned=True)
    warning_codes = {warning["code"] for warning in context["warnings"]}

    assert context["totals"]["calories"] == 1200
    assert context["totals"]["protein_g"] == 40
    assert context["totals"]["sodium_mg"] == 2600
    assert context["remaining"]["calories"] == 1000
    assert context["remaining"]["protein_g"] == 108
    assert context["accepted_entries_count"] == 1
    assert context["pending_review_count"] == 1
    assert "under_fueled_hard_workout" in warning_codes
    assert "food_pending_review" in warning_codes
    assert context["next_day_context"]["high_sodium"] is True
    assert context["next_day_context"]["late_meal"] is True
    assert context["plan_adjustment"]["allowed"] is False
    assert context["uses_only_accepted_entries"] is True


def test_nutrition_context_does_not_warn_under_fueled_without_food(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(module, "NUTRITION_DATA", [])

    context = module._nutrition_context_for_date("2026-05-18", hard_training_planned=True)

    assert "under_fueled_hard_workout" not in {warning["code"] for warning in context["warnings"]}
    assert context["totals"]["entries_count"] == 0


def test_nutrition_context_counts_late_meal_from_food_log_timestamp(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(
        module,
        "NUTRITION_DATA",
        [{"date": "2026-05-18", "calories": 700, "protein_g": 35, "sodium_mg": 500}],
    )

    context = module._nutrition_context_for_date(
        "2026-05-18",
        food_log_entries=[{"date": "2026-05-18", "logged_at": "2026-05-18T21:30:00"}],
    )

    assert context["next_day_context"]["late_meal"] is True
    assert context["next_day_context"]["late_entries_count"] == 1


def test_nutrition_context_uses_food_log_totals_when_json_is_empty(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(module, "NUTRITION_DATA", [])

    context = module._nutrition_context_for_date(
        "2026-05-18",
        food_log_entries=[
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T21:30:00",
                "calories": 900,
                "protein_g": 45,
                "carbs_g": 100,
                "fat_g": 30,
                "sodium_mg": 2400,
            }
        ],
    )

    assert context["totals"]["calories"] == 900
    assert context["totals"]["protein_g"] == 45
    assert context["totals"]["sodium_mg"] == 2400
    assert context["accepted_entries_count"] == 1
    assert context["next_day_context"]["high_sodium"] is True
    assert context["next_day_context"]["late_meal"] is True


def test_nutrition_context_excludes_pending_food_log_rows(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(module, "NUTRITION_DATA", [])

    context = module._nutrition_context_for_date(
        "2026-05-18",
        food_log_entries=[
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T12:30:00",
                "calories": 900,
                "protein_g": 45,
                "correction_state": "accepted",
            },
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T13:00:00",
                "calories": 700,
                "protein_g": 35,
                "correction_state": "pending_review",
            },
        ],
    )

    assert context["totals"]["calories"] == 900
    assert context["totals"]["protein_g"] == 45
    assert context["accepted_entries_count"] == 1
    assert context["pending_review_count"] == 1


def test_nutrition_context_does_not_fallback_to_legacy_when_food_logs_are_pending(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(
        module,
        "NUTRITION_DATA",
        [{"date": "2026-05-18", "calories": 700, "protein_g": 35}],
    )

    context = module._nutrition_context_for_date(
        "2026-05-18",
        food_log_entries=[
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T13:00:00",
                "calories": 700,
                "protein_g": 35,
                "correction_state": "pending_review",
            },
        ],
    )

    assert context["totals"]["calories"] == 0
    assert context["totals"]["protein_g"] == 0
    assert context["accepted_entries_count"] == 0
    assert context["pending_review_count"] == 1


def test_nutrition_today_exposes_remaining_targets_and_context(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-18")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(
        module,
        "NUTRITION_DATA",
        [{"date": "2026-05-18", "calories": 1800, "protein_g": 100, "carbs_g": 150, "fat_g": 60}],
    )
    monkeypatch.setattr(module, "get_food_logs", lambda *_args, **_kwargs: [])

    response = module.app.test_client().get("/api/nutrition-today")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["calories_remaining"] == 400
    assert payload["protein_gap_g"] == 48
    assert payload["coaching_context"]["remaining"]["calories"] == 400
    assert payload["coaching_context"]["plan_adjustment"]["allowed"] is False


def test_nutrition_today_top_level_uses_food_log_totals_when_json_is_empty(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-18")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(
        module,
        "get_food_logs",
        lambda *_args, **_kwargs: [
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T12:30:00",
                "calories": 900,
                "protein_g": 45,
                "carbs_g": 100,
                "fat_g": 30,
                "sodium_mg": 900,
            }
        ],
    )

    response = module.app.test_client().get("/api/nutrition-today")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["calories"] == 900
    assert payload["protein_g"] == 45
    assert payload["entries_count"] == 1
    assert payload["calories_remaining"] == 1300
    assert payload["coaching_context"]["totals"]["calories"] == 900


def test_dashboard_nutrition_top_level_uses_food_log_totals_when_json_is_empty(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-18")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(
        module,
        "get_food_logs",
        lambda *_args, **_kwargs: [
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T12:30:00",
                "calories": 900,
                "protein_g": 45,
                "carbs_g": 100,
                "fat_g": 30,
                "sodium_mg": 900,
            }
        ],
    )
    monkeypatch.setattr(
        module,
        "_compute_data_freshness",
        lambda *_args, **_kwargs: {
            "oura": {"status": "fresh"},
            "apple_health": {"status": "fresh"},
            "food": {"status": "fresh"},
        },
    )

    response = module.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    nutrition_today = response.get_json()["nutrition_today"]
    assert nutrition_today["calories"] == 900
    assert nutrition_today["protein_g"] == 45
    assert nutrition_today["entries_count"] == 1
    assert nutrition_today["calories_remaining"] == 1300
    assert nutrition_today["coaching_context"]["totals"]["calories"] == 900


def test_food_freshness_targets_use_food_log_totals_when_json_is_empty(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(
        module,
        "get_food_logs",
        lambda *_args, **_kwargs: [
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T12:30:00",
                "calories": 900,
                "protein_g": 45,
                "carbs_g": 100,
                "fat_g": 30,
                "sodium_mg": 900,
            }
        ],
    )

    freshness = module._latest_food_freshness(now=module.datetime(2026, 5, 18, 13, 0, 0))
    target_state = module._food_target_state(module.datetime(2026, 5, 18, 13, 0, 0))

    assert freshness == ("fresh", "2026-05-18", None)
    assert target_state["target_state"] == "under"
    assert target_state["calories"] == 900
    assert target_state["protein_g"] == 45
    assert target_state["calories_remaining"] == 1300
    assert target_state["protein_gap_g"] == 103


def test_food_freshness_ignores_pending_food_logs(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(
        module,
        "get_food_logs",
        lambda *_args, **_kwargs: [
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T13:00:00",
                "calories": 700,
                "protein_g": 35,
                "correction_state": "pending_review",
            },
            {
                "date": "2026-05-17",
                "logged_at": "2026-05-17T12:00:00",
                "calories": 900,
                "protein_g": 45,
                "correction_state": "accepted",
            },
        ],
    )

    freshness = module._latest_food_freshness(now=module.datetime(2026, 5, 18, 13, 0, 0))

    assert freshness == ("aging", "2026-05-17", None)


def test_food_pending_review_state_uses_food_logs(monkeypatch):
    module = importlib.import_module("app")
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(
        module,
        "get_food_logs",
        lambda *_args, **_kwargs: [
            {
                "date": "2026-05-18",
                "logged_at": "2026-05-18T13:00:00",
                "calories": 700,
                "protein_g": 35,
                "correction_state": "pending_review",
            },
        ],
    )

    assert module._food_pending_review_state(module.datetime(2026, 5, 18, 13, 0, 0)) is True


def test_smart_recommendation_keeps_plan_but_adds_under_fueled_context(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"daily_calorie_target": 2200, "daily_protein_target_g": 148},
    )
    monkeypatch.setattr(
        module,
        "NUTRITION_DATA",
        [{"date": "2026-05-18", "calories": 500, "protein_g": 20}],
    )
    monkeypatch.setattr(module, "get_food_logs", lambda *_args, **_kwargs: [])
    real_datetime = module.datetime

    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 18, 10, 0, 0)

    monkeypatch.setattr(module, "datetime", FixedDateTime)
    monkeypatch.setattr(
        module,
        "get_oura_daily",
        lambda *_args, **_kwargs: {"readiness_score": 92, "sleep_score": 85, "hrv": 50},
    )
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_args, **_kwargs: [{"hrv": 50}])
    monkeypatch.setattr(module, "compute_hrv_trend", lambda *_args, **_kwargs: "stable")
    monkeypatch.setattr(module, "calculate_acwr", lambda *_args, **_kwargs: {"acwr": 1.0})
    monkeypatch.setattr(module, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0, "status": "ok"})
    monkeypatch.setattr(module, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(module, "_fetch_wttr", lambda *_args, **_kwargs: {"available": False})
    monkeypatch.setattr(
        module,
        "_compute_data_freshness",
        lambda *_args, **_kwargs: {
            "oura": {"status": "fresh"},
            "apple_health": {"status": "fresh"},
            "food": {"status": "fresh"},
        },
    )

    response = module.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    payload = response.get_json()
    warning_codes = {warning["code"] for warning in payload["nutrition_context"]["warnings"]}
    assert payload["recommendation"] == "intensity"
    assert "under_fueled_hard_workout" in warning_codes
    assert payload["nutrition_context"]["plan_adjustment"]["allowed"] is False
