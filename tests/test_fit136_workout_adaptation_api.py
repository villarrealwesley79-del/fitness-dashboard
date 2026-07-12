from __future__ import annotations

from datetime import datetime
import importlib
import json
from urllib.parse import quote

import data_store
import workout_adaptation


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit136-secret")
    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module.personal_vocab, "record_accept", lambda *_a, **_kw: None)
    monkeypatch.setattr(module.personal_vocab, "record_correct", lambda *_a, **_kw: None)
    return module, module.app.test_client()


def _estimate(**overrides):
    estimate = {
        "item_name": "Chicken bowl",
        "portion_description": "1 bowl",
        "meal_type": "lunch",
        "calories": 500,
        "protein_g": 35,
        "carbs_g": 45,
        "fat_g": 18,
        "sodium_mg": 700,
        "fiber_g": 6,
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "manual_review_estimate",
    }
    estimate.update(overrides)
    return estimate


def _recommendation():
    return {
        "id": "rec-fit136",
        "focus": "upper",
        "estimated_minutes": 60,
        "exercises": [
            {
                "machine": "Chest Press",
                "muscle_group": "chest",
                "target_sets": 4,
                "target_reps": 10,
                "time_per_set_minutes": 5,
            },
            {
                "machine": "Lat Pulldown",
                "muscle_group": "back",
                "target_sets": 3,
                "target_reps": 10,
                "time_per_set_minutes": 5,
            },
        ],
        "cardio": {"type": "walk", "duration_minutes": 15},
    }


def _add_earlier_coverage_entry(client_id: str):
    return data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-24",
            "logged_at": "2026-05-24T08:00:00",
            "meal_id": f"meal-{client_id}",
            "item_name": "Breakfast",
            "calories": 690,
            "protein_g": 29.5,
            "carbs_g": 80,
            "fat_g": 25,
            "sodium_mg": 500,
            "confidence": 0.9,
            "correction_state": "accepted",
        },
    )


def test_accepting_food_schedules_workout_adaptation_window(monkeypatch, tmp_path):
    _module, client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/meal-intake/fit136-single/accept",
        json={
            "estimate": _estimate(),
            "local_date": "2026-05-24",
            "local_timestamp": "2026-05-24T12:00:00",
        },
    )

    assert response.status_code == 200
    pending = data_store.list_pending_workout_adaptation_windows(1)
    assert len(pending) == 1
    assert pending[0]["date"] == "2026-05-24"
    assert pending[0]["food_log_client_ids"] == ["fit136-single"]
    assert pending[0]["window_closes_at"] > pending[0]["window_started_at"]


def test_manual_add_nutrition_schedules_workout_adaptation_window(monkeypatch, tmp_path):
    _module, client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/add-nutrition",
        json={
            "client_id": "manual-fit136",
            "date": "2026-05-24",
            "calories": 500,
            "protein_g": 35,
            "carbs_g": 45,
            "fat_g": 18,
            "sodium_mg": 700,
            "confidence": 0.88,
            "source": "manual",
        },
    )

    assert response.status_code == 200
    pending = data_store.list_pending_workout_adaptation_windows(1)
    assert len(pending) == 1
    assert pending[0]["food_log_client_ids"] == ["manual-fit136"]


def test_pending_manual_nutrition_does_not_schedule_workout_adaptation(monkeypatch, tmp_path):
    _module, client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/add-nutrition",
        json={
            "client_id": "manual-pending-fit136",
            "date": "2026-05-24",
            "calories": 500,
            "protein_g": 35,
            "correction_state": "pending_review",
            "confidence": 0.5,
            "source": "manual",
        },
    )

    assert response.status_code == 200
    assert data_store.list_pending_workout_adaptation_windows(1) == []


def test_workout_adaptation_events_endpoint_is_read_only(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "USER_SETTINGS", {"available_time_minutes": 35, "daily_calorie_target": 2200, "daily_protein_target_g": 150})
    generate_calls = []

    def fake_generate_next_workout(*_args, **kwargs):
        generate_calls.append(kwargs)
        return _recommendation()

    monkeypatch.setattr(module, "generate_next_workout", fake_generate_next_workout)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", {"id": "stale-cached-plan", "estimated_minutes": 999, "exercises": []})
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT", "stale")
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fresh")

    row = data_store.add_food_log(
        1,
        {
            "client_id": "fit136-due",
            "date": "2026-05-24",
            "logged_at": "2026-05-24T12:00:00",
            "meal_id": "meal-due",
            "item_name": "Small snack",
            "portion_description": "1 small snack",
            "calories": 250,
            "protein_g": 5,
            "carbs_g": 40,
            "fat_g": 4,
            "sodium_mg": 200,
            "fiber_g": 2,
            "confidence": 0.9,
            "source": "manual_review_estimate",
            "correction_state": "accepted",
        },
    )
    workout_adaptation.enqueue_accepted_food_logs(
        1,
        [row],
        clock=datetime(2026, 5, 24, 12, 0, 0),
    )

    before_events = data_store.list_workout_adaptation_events(1, unacknowledged=False)
    before_pending = data_store.list_pending_workout_adaptation_windows(1)
    response = client.get("/api/workout-adaptation-events?unacknowledged=true")
    repeated = client.get("/api/workout-adaptation-events?unacknowledged=true")

    assert response.status_code == 200
    assert repeated.status_code == 200
    assert response.get_json() == {"count": 0, "events": []}
    assert repeated.get_json() == response.get_json()
    assert data_store.list_workout_adaptation_events(1, unacknowledged=False) == before_events
    assert data_store.list_pending_workout_adaptation_windows(1) == before_pending
    assert generate_calls == []


def test_workout_adaptation_evaluation_endpoint_processes_due_windows_idempotently(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "USER_SETTINGS", {"available_time_minutes": 35, "daily_calorie_target": 2200, "daily_protein_target_g": 150})
    generate_calls = []

    def fake_generate_next_workout(*_args, **kwargs):
        generate_calls.append(kwargs)
        return _recommendation()

    monkeypatch.setattr(module, "generate_next_workout", fake_generate_next_workout)
    monkeypatch.setattr(module, "_current_workout_training_recommendation", lambda: "train")
    monkeypatch.setattr(module, "_open_wearables_recommendation_facts", lambda: {"risk": "high"})
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, facts: ("recovery", {"detail": "guarded"}),
    )
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)

    row = data_store.add_food_log(
        1,
        {
            "client_id": "fit233-explicit-evaluate",
            "date": "2026-05-24",
            "logged_at": "2026-05-24T12:00:00",
            "meal_id": "meal-explicit-evaluate",
            "item_name": "Small snack",
            "calories": 250,
            "protein_g": 5,
            "carbs_g": 40,
            "fat_g": 4,
            "sodium_mg": 200,
            "confidence": 0.9,
            "correction_state": "accepted",
        },
    )
    workout_adaptation.enqueue_accepted_food_logs(
        1,
        [row],
        clock=datetime(2026, 5, 24, 12, 0, 0),
    )

    first = client.post("/api/workout-adaptation-events/evaluate")
    repeated = client.post("/api/workout-adaptation-events/evaluate")
    feed = client.get("/api/workout-adaptation-events?unacknowledged=true")

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert first.get_json()["evaluated_count"] == 1
    assert repeated.get_json()["evaluated_count"] == 0
    assert feed.get_json()["count"] == 1
    assert len(data_store.list_workout_adaptation_events(1, unacknowledged=False)) == 1
    assert data_store.list_pending_workout_adaptation_windows(1) == []
    assert generate_calls[0]["training_recommendation"] == "recovery"


def test_workout_adaptation_evaluation_preserves_same_day_canonical_plan(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "new-fingerprint")
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda _fingerprint: None)
    canonical = {**_recommendation(), "id": "user-customized-plan"}
    monkeypatch.setattr(
        module,
        "get_current_workout_plan",
        lambda _user_id, **_kwargs: {
            "plan": canonical,
            "fingerprint": "old-fingerprint",
            "updated_at": "2026-05-24T18:30:00",
        },
    )
    monkeypatch.setattr(
        module,
        "generate_next_workout",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must preserve same-day plan")),
    )

    response = client.post("/api/workout-adaptation-events/evaluate")

    assert response.status_code == 200
    assert module.LAST_WORKOUT_RECOMMENDATION["id"] == "user-customized-plan"


def test_next_workout_route_replays_due_adaptation_even_with_cached_plan(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "USER_SETTINGS", {"available_time_minutes": 35, "daily_calorie_target": 2200, "daily_protein_target_g": 150})
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    cached = _recommendation()
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", cached)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT", module._workout_recommendation_fingerprint())
    _add_earlier_coverage_entry("fit136-next-workout-earlier")

    row = data_store.add_food_log(
        1,
        {
            "client_id": "fit136-next-workout",
            "date": "2026-05-24",
            "logged_at": "2026-05-24T18:00:00",
            "meal_id": "meal-next-workout",
            "item_name": "Small snack",
            "portion_description": "1 small snack",
            "calories": 250,
            "protein_g": 5,
            "carbs_g": 40,
            "fat_g": 4,
            "sodium_mg": 200,
            "fiber_g": 2,
            "confidence": 0.9,
            "source": "manual_review_estimate",
            "correction_state": "accepted",
        },
    )
    workout_adaptation.enqueue_accepted_food_logs(
        1,
        [row],
        clock=datetime(2026, 5, 24, 18, 0, 0),
    )

    response = client.get("/api/next-workout?active_workout_open=false")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["workout_adaptation_events"][0]["status"] == "applied"
    assert payload["next_workout"]["estimated_minutes"] <= 35
    assert "_fit136_lightweight_no_ow" not in payload["next_workout"]


def test_active_workout_evaluation_without_completed_sets_defers_pending_window(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "USER_SETTINGS", {"available_time_minutes": 35, "daily_calorie_target": 2200, "daily_protein_target_g": 150})
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_kw: _recommendation())
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)

    row = data_store.add_food_log(
        1,
        {
            "client_id": "fit136-defer",
            "date": "2026-05-24",
            "logged_at": "2026-05-24T12:00:00",
            "meal_id": "meal-defer",
            "item_name": "Small snack",
            "portion_description": "1 small snack",
            "calories": 250,
            "protein_g": 5,
            "carbs_g": 40,
            "fat_g": 4,
            "sodium_mg": 200,
            "fiber_g": 2,
            "confidence": 0.9,
            "source": "manual_review_estimate",
            "correction_state": "accepted",
        },
    )
    workout_adaptation.enqueue_accepted_food_logs(
        1,
        [row],
        clock=datetime(2026, 5, 24, 12, 0, 0),
    )

    response = client.post("/api/workout-adaptation-events/evaluate?active_workout_open=true")

    assert response.status_code == 200
    assert response.get_json()["evaluated_count"] == 0
    assert len(data_store.list_pending_workout_adaptation_windows(1)) == 1


def test_repeated_adaptation_windows_use_cached_base_plan_not_prior_patch(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "USER_SETTINGS", {"available_time_minutes": 60, "daily_calorie_target": 2200, "daily_protein_target_g": 150})
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_kw: _recommendation())
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    _add_earlier_coverage_entry("fit136-repeated-earlier")

    first = data_store.add_food_log(
        1,
        {
            "client_id": "fit136-first-window",
            "date": "2026-05-24",
            "logged_at": "2026-05-24T18:00:00",
            "meal_id": "meal-first-window",
            "item_name": "Small snack",
            "calories": 250,
            "protein_g": 5,
            "carbs_g": 40,
            "fat_g": 4,
            "sodium_mg": 200,
            "confidence": 0.9,
            "correction_state": "accepted",
        },
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [first], clock=datetime(2026, 5, 24, 18, 0, 0))
    first_response = client.get("/api/next-workout?active_workout_open=false")
    first_sets = first_response.get_json()["next_workout"]["exercises"][0]["target_sets"]

    second = data_store.add_food_log(
        1,
        {
            "client_id": "fit136-second-window",
            "date": "2026-05-24",
            "logged_at": "2026-05-24T19:00:00",
            "meal_id": "meal-second-window",
            "item_name": "Small snack",
            "calories": 250,
            "protein_g": 5,
            "carbs_g": 40,
            "fat_g": 4,
            "sodium_mg": 200,
            "confidence": 0.9,
            "correction_state": "accepted",
        },
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [second], clock=datetime(2026, 5, 24, 19, 0, 0))
    second_response = client.get("/api/next-workout?active_workout_open=false")
    second_sets = second_response.get_json()["next_workout"]["exercises"][0]["target_sets"]

    assert first_sets == 3
    assert second_sets == 3

    third_response = client.get("/api/next-workout?active_workout_open=false")
    third_sets = third_response.get_json()["next_workout"]["exercises"][0]["target_sets"]
    assert third_sets == 3


def test_repeated_adaptation_preserves_user_edited_cached_plan(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "USER_SETTINGS", {"available_time_minutes": 60, "daily_calorie_target": 2200, "daily_protein_target_g": 150})
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_kw: _recommendation())
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    _add_earlier_coverage_entry("fit136-user-edit-earlier")

    first = data_store.add_food_log(
        1,
        {
            "client_id": "fit136-user-edit-first",
            "date": "2026-05-24",
            "logged_at": "2026-05-24T18:00:00",
            "meal_id": "meal-user-edit-first",
            "item_name": "Small snack",
            "calories": 250,
            "protein_g": 5,
            "carbs_g": 40,
            "fat_g": 4,
            "sodium_mg": 200,
            "confidence": 0.9,
            "correction_state": "accepted",
        },
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [first], clock=datetime(2026, 5, 24, 18, 0, 0))
    first_response = client.get("/api/next-workout?active_workout_open=false")
    assert first_response.status_code == 200

    edited_plan = module.LAST_WORKOUT_RECOMMENDATION
    edited_plan["exercises"][0]["machine"] = "Incline Press"
    edited_plan["exercises"][0]["target_sets"] = 5
    edited_plan["estimated_minutes"] = 65

    second = data_store.add_food_log(
        1,
        {
            "client_id": "fit136-user-edit-second",
            "date": "2026-05-24",
            "logged_at": "2026-05-24T19:00:00",
            "meal_id": "meal-user-edit-second",
            "item_name": "Small snack",
            "calories": 250,
            "protein_g": 5,
            "carbs_g": 40,
            "fat_g": 4,
            "sodium_mg": 200,
            "confidence": 0.9,
            "correction_state": "accepted",
        },
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [second], clock=datetime(2026, 5, 24, 19, 0, 0))
    second_response = client.get("/api/next-workout?active_workout_open=false")

    assert second_response.status_code == 200
    adapted_plan = second_response.get_json()["next_workout"]
    assert adapted_plan["exercises"][0]["machine"] == "Incline Press"
    assert adapted_plan["exercises"][0]["target_sets"] == 4
