from __future__ import annotations

from datetime import datetime
import importlib
import json
from urllib.parse import quote

import data_store
import pytest
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


@pytest.mark.parametrize("source_state", ["accepted", "manual"])
def test_ui_shaped_correction_preserves_omitted_source_metadata(
    monkeypatch,
    tmp_path,
    source_state,
):
    _module, client = _client(monkeypatch, tmp_path)
    client_id = f"ui-correction-{source_state}"
    portion_description = "1 bowl" if source_state == "accepted" else None
    accepted_estimate = (
        {
            "item_name": "Chicken bowl",
            "portion_description": portion_description,
            "meal_type": "dinner",
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
        if source_state == "accepted"
        else None
    )
    original = data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-24",
            "logged_at": "2026-05-24T12:00:00",
            "meal_id": "meal-1",
            "meal_type": "dinner",
            "item_name": "Chicken bowl",
            "portion_description": portion_description,
            "context_note": "Dinner after training",
            "calories": 500,
            "protein_g": 35,
            "carbs_g": 45,
            "fat_g": 18,
            "sodium_mg": 700,
            "fiber_g": 6,
            "confidence": 0.88,
            "source": "manual",
            "correction_state": source_state,
            "accepted_estimate": accepted_estimate,
        },
    )
    pending = data_store.enqueue_workout_adaptation_pending(
        1,
        date="2026-05-24",
        meal_id="meal-1",
        food_log_client_ids=[client_id],
        window_started_at="2026-05-24T12:00:00",
        window_closes_at="2026-05-24T12:03:00",
    )
    event = data_store.save_workout_adaptation_event(
        1,
        pending["id"],
        {
            "date": "2026-05-24",
            "status": "applied",
            "silent": False,
            "change_type": "reduce_volume",
            "applies_to": "today",
            "trigger": {
                "meal_ids": ["meal-1"],
                "food_log_client_ids": [client_id],
            },
            "created_at": "2026-05-24T12:03:01",
        },
    )

    ui_payload = {
        "client_id": original["client_id"],
        "date": original["date"],
        "logged_at": original["logged_at"],
        "source": original["source"],
        "correction_state": "corrected",
        "item_name": original["item_name"],
        "calories": original["calories"],
        "protein_g": original["protein_g"],
        "carbs_g": original["carbs_g"],
        "fat_g": original["fat_g"],
        "sodium_mg": original["sodium_mg"],
    }
    response = client.post("/api/add-nutrition", json=ui_payload)

    assert response.status_code == 200
    stored_log = response.get_json()["food_log"]
    stored_event = next(
        item
        for item in data_store.list_workout_adaptation_events(1, unacknowledged=True)
        if item["id"] == event["id"]
    )
    assert stored_log["meal_id"] == "meal-1"
    assert stored_log["meal_type"] == "dinner"
    assert stored_log["portion_description"] == portion_description
    assert stored_log["context_note"] == "Dinner after training"
    assert stored_log["fiber_g"] == original["fiber_g"]
    assert stored_log["confidence"] == original["confidence"]
    assert stored_event["status"] == "applied"

    if source_state == "accepted":
        clear_portion_response = client.post(
            "/api/add-nutrition",
            json={**ui_payload, "portion_description": None},
        )
        assert clear_portion_response.status_code == 200
        cleared_portion = clear_portion_response.get_json()["food_log"]
        assert cleared_portion["portion_description"] is None
        assert cleared_portion["accepted_estimate"]["portion_description"] is None
        cleared_portion_event = next(
            item
            for item in data_store.list_workout_adaptation_events(1, unacknowledged=True)
            if item["id"] == event["id"]
        )
        assert cleared_portion_event["status"] == "stale"

    clear_meal_type_response = client.post(
        "/api/add-nutrition",
        json={**ui_payload, "meal_type": None},
    )
    cleared_event = next(
        item
        for item in data_store.list_workout_adaptation_events(1, unacknowledged=True)
        if item["id"] == event["id"]
    )
    assert clear_meal_type_response.status_code == 200
    assert not clear_meal_type_response.get_json()["food_log"]["meal_type"]
    assert cleared_event["status"] == "stale"

    reassigned_response = client.post(
        "/api/add-nutrition",
        json={**ui_payload, "meal_id": "meal-2"},
    )
    assert reassigned_response.status_code == 200
    assert reassigned_response.get_json()["food_log"]["meal_id"] == "meal-2"

    clear_response = client.post(
        "/api/add-nutrition",
        json={**ui_payload, "meal_id": None, "context_note": None},
    )
    assert clear_response.status_code == 200
    assert clear_response.get_json()["food_log"]["meal_id"] is None
    assert clear_response.get_json()["food_log"]["context_note"] is None


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


def test_workout_adaptation_events_endpoint_exposes_incomplete_coverage_contract(monkeypatch, tmp_path):
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

    completed_sets = quote(json.dumps({"Chest Press": 1}))
    response = client.get(
        f"/api/workout-adaptation-events?active_workout_open=true&completed_sets={completed_sets}"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    event = payload["events"][0]
    assert event["status"] == "no_change"
    assert event["silent"] is True
    assert event["change_type"] == "none"
    assert event["active_workout"]["updated_live"] is False
    assert event["patch"]["estimated_minutes"] == 60
    assert event["confidence"]["no_change_reason"] == "incomplete_day_coverage"
    assert event["reason_metadata"]["fit137_contract"]["endpoint"] == "/api/workout-adaptation-events"
    assert "incomplete_day_coverage" in event["reason_metadata"]["fit137_contract"]["no_change_signal"]["confidence.no_change_reason"]
    assert generate_calls[0]["consume_cardio_rotation"] is False
    assert "include_open_wearables_readiness" not in generate_calls[0]
    assert "training_recommendation" in generate_calls[0]


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


def test_active_workout_poll_without_completed_sets_defers_pending_window(monkeypatch, tmp_path):
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

    response = client.get("/api/workout-adaptation-events?active_workout_open=true")

    assert response.status_code == 200
    assert response.get_json()["count"] == 0
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
