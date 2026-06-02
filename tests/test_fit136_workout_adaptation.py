from __future__ import annotations

from datetime import datetime, timedelta

import data_store
import workout_adaptation


def _isolated_db(monkeypatch, tmp_path):
    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()


def _food_log(client_id: str, *, user_id: int = 1, date: str = "2026-05-24", **overrides):
    record = {
        "client_id": client_id,
        "date": date,
        "logged_at": f"{date}T12:00:00",
        "meal_id": "meal-1",
        "item_name": "Chicken bowl",
        "portion_description": "1 bowl",
        "calories": 500,
        "protein_g": 35,
        "carbs_g": 45,
        "fat_g": 18,
        "sodium_mg": 700,
        "fiber_g": 6,
        "confidence": 0.88,
        "source": "manual_review_estimate",
        "correction_state": "accepted",
    }
    record.update(overrides)
    return data_store.add_food_log(user_id, record)


def _recommendation():
    return {
        "id": "rec-1",
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


def _nutrition_context(*, calories_pct=100, protein_pct=100, sodium_mg=700):
    return {
        "totals": {
            "calories": int(2200 * calories_pct / 100),
            "protein_g": round(150 * protein_pct / 100, 1),
            "carbs_g": 200,
            "fat_g": 70,
            "sodium_mg": sodium_mg,
            "entries_count": 1,
        },
        "targets": {
            "calories": 2200,
            "protein_g": 150,
            "carbs_g": 250,
            "fat_g": 73,
        },
        "remaining": {
            "calories": int(2200 - (2200 * calories_pct / 100)),
            "protein_g": round(150 - (150 * protein_pct / 100), 1),
            "carbs_g": 50,
            "fat_g": 3,
        },
        "percentages": {
            "calories": calories_pct,
            "protein": protein_pct,
            "carbs": 80,
            "fat": 95,
        },
    }


def test_coalescing_window_uses_injected_clock_without_sleep(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    first = _food_log("meal-a", meal_id="meal-window")
    second = _food_log("meal-b", meal_id="meal-window")

    pending = workout_adaptation.enqueue_accepted_food_logs(1, [first], clock=start)
    assert pending["window_closes_at"] == "2026-05-24T12:03:00"

    merged = workout_adaptation.enqueue_accepted_food_logs(
        1,
        [second],
        clock=start + timedelta(minutes=2),
    )
    assert merged["id"] == pending["id"]
    assert sorted(merged["food_log_client_ids"]) == ["meal-a", "meal-b"]
    assert merged["window_closes_at"] == "2026-05-24T12:03:00"

    late = _food_log("meal-c", meal_id="meal-later")
    new_pending = workout_adaptation.enqueue_accepted_food_logs(
        1,
        [late],
        clock=start + timedelta(minutes=4),
    )
    assert new_pending["id"] != pending["id"]


def test_low_confidence_no_change_is_silent_contract(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    row = _food_log("low-confidence", confidence=0.4, calories=300)
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=100, protein_pct=100),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    assert patched["estimated_minutes"] == 60
    assert len(events) == 1
    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["silent"] is True
    assert event["change_type"] == "none"
    assert event["confidence"]["no_change_reason"] == "low_confidence"


def test_under_fueled_adaptation_reduces_and_clamps_to_available_time(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    row = _food_log("under-fueled", calories=300, protein_g=8, confidence=0.9)
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 35},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    assert len(events) == 1
    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "applied"
    assert event["change_type"] == "reduce_volume"
    assert patched["estimated_minutes"] <= 35
    assert event["patch"]["estimated_minutes"] <= 35
    assert any(op["op"].startswith("clamp") for op in event["patch"]["operations"])


def test_high_calorie_meal_alone_does_not_add_burn_off_cardio(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    row = _food_log("high-calorie", calories=1100, protein_g=40, confidence=0.95)
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=130, protein_pct=100),
        settings={"available_time_minutes": 75},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["silent"] is True
    assert patched["cardio"]["duration_minutes"] == 15
    assert "burn" not in str(event).lower()


def test_next_day_alcohol_signal_can_shift_to_recovery_without_punitive_work(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 22, 0, 0)
    row = _food_log(
        "late-wine",
        date="2026-05-24",
        logged_at="2026-05-24T22:00:00",
        item_name="Wine with dinner",
        sodium_mg=300,
        confidence=0.92,
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=100, protein_pct=100),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-25",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "applied"
    assert event["applies_to"] == "next_day"
    assert event["change_type"] == "rest_recovery"
    assert patched["training_recommendation"] == "recovery"
    assert "punitive" not in str(event).lower()


def test_next_day_high_sodium_uses_full_day_total(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 19, 0, 0)
    row = _food_log(
        "salty-dinner",
        date="2026-05-24",
        logged_at="2026-05-24T19:00:00",
        sodium_mg=900,
        confidence=0.92,
    )
    other_row = _food_log(
        "salty-lunch",
        date="2026-05-24",
        logged_at="2026-05-24T12:00:00",
        sodium_mg=1600,
        confidence=0.92,
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row, other_row],
        nutrition_context=_nutrition_context(calories_pct=100, protein_pct=100, sodium_mg=0),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-25",
        clock=start + timedelta(hours=6),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["change_type"] == "rest_recovery"
    assert patched["training_recommendation"] == "recovery"


def test_pending_day_rows_do_not_create_high_sodium_recovery_signal(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 19, 0, 0)
    row = _food_log(
        "accepted-low-sodium",
        date="2026-05-24",
        logged_at="2026-05-24T19:00:00",
        sodium_mg=300,
        confidence=0.92,
    )
    pending_row = _food_log(
        "pending-high-sodium",
        date="2026-05-24",
        logged_at="2026-05-24T20:00:00",
        sodium_mg=3000,
        confidence=0.92,
        correction_state="pending_review",
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row, pending_row],
        nutrition_context=_nutrition_context(calories_pct=100, protein_pct=100, sodium_mg=0),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-25",
        clock=start + timedelta(hours=6),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["change_type"] == "none"
    assert patched.get("training_recommendation") != "recovery"


def test_next_day_only_signal_is_not_consumed_same_night(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 22, 0, 0)
    row = _food_log(
        "same-night-wine",
        date="2026-05-24",
        logged_at="2026-05-24T22:00:00",
        item_name="Wine with dinner",
        calories=250,
        protein_g=5,
        confidence=0.92,
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    same_day_patched, same_day_events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    assert same_day_events == []
    assert same_day_patched.get("training_recommendation") != "recovery"
    assert len(data_store.list_pending_workout_adaptation_windows(1)) == 1

    next_day_patched, next_day_events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=100, protein_pct=100),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-25",
        clock=start + timedelta(hours=10),
    )

    assert workout_adaptation.project_event(next_day_events[0])["change_type"] == "rest_recovery"
    assert next_day_patched["training_recommendation"] == "recovery"


def test_alcohol_signal_does_not_match_substrings_inside_food_names(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 18, 0, 0)
    row = _food_log(
        "drumstick",
        date="2026-05-24",
        logged_at="2026-05-24T18:00:00",
        item_name="Chicken drumstick",
        sodium_mg=300,
        confidence=0.92,
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=100, protein_pct=100),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-25",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["change_type"] == "none"
    assert patched.get("training_recommendation") != "recovery"


def test_active_workout_patch_preserves_completed_sets(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    row = _food_log("active-under-fueled", calories=300, protein_g=8, confidence=0.9)
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    recommendation = _recommendation()
    recommendation["exercises"][0]["target_sets"] = 3
    recommendation["exercises"][0]["sets"] = 3
    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        recommendation,
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        active_workout_open=True,
        completed_sets_by_exercise={"Chest Press": 3},
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    chest = next(ex for ex in patched["exercises"] if ex["machine"] == "Chest Press")
    lat = next(ex for ex in patched["exercises"] if ex["machine"] == "Lat Pulldown")
    assert chest["target_sets"] == 3
    assert lat["target_sets"] == 2
    assert event["active_workout"]["updated_live"] is True
    assert event["active_workout"]["preserve_completed_work"] is True


def test_guardrail_metadata_has_required_citations_and_neutral_language(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    row = _food_log("guardrail", calories=300, protein_g=8, confidence=0.9)
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    _patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    citations = event["reason_metadata"]["citations"]
    pmids = {citation["pmid"] for citation in citations}
    assert {"26920240", "28919842", "28642676", "19204579"}.issubset(pmids)
    event_text = str(event).lower()
    for banned in workout_adaptation.MORAL_LABELS:
        assert banned not in event_text


def test_neutral_language_guard_ignores_user_controlled_ids(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    row = _food_log("bad-photo-1", calories=300, protein_g=8, confidence=0.9)
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    _patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    assert workout_adaptation.project_event(events[0])["status"] == "applied"


def test_clamp_preserves_generated_non_default_set_timing(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    row = _food_log("strength-under-fueled", calories=300, protein_g=8, confidence=0.9)
    recommendation = _recommendation()
    for exercise in recommendation["exercises"]:
        exercise.pop("time_per_set_minutes", None)
    recommendation["exercises"][0]["estimated_time"] = 20
    recommendation["exercises"][1]["estimated_time"] = 15
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        recommendation,
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 35},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    assert patched["estimated_minutes"] <= 35
    chest = next(ex for ex in patched["exercises"] if ex["machine"] == "Chest Press")
    assert chest["time_per_set_minutes"] == 5
    assert workout_adaptation.project_event(events[0])["patch"]["estimated_minutes"] <= 35


def test_pending_window_claim_prevents_duplicate_events(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    pending = data_store.enqueue_workout_adaptation_pending(
        1,
        date="2026-05-24",
        meal_id="meal-claim",
        food_log_client_ids=["claim-1"],
        window_started_at="2026-05-24T12:00:00",
        window_closes_at="2026-05-24T12:03:00",
    )
    event = {
        "date": "2026-05-24",
        "status": "no_change",
        "silent": True,
        "change_type": "none",
        "applies_to": "today",
        "reason": "Preserved the workout.",
        "confidence": {"level": "low", "score": 0.0, "no_change_reason": "low_confidence"},
        "trigger": {"food_log_client_ids": ["claim-1"]},
        "patch": {"available_time_minutes": 60, "estimated_minutes": 60, "operations": []},
        "created_at": "2026-05-24T12:03:01",
    }

    first = data_store.save_workout_adaptation_event(1, pending["id"], dict(event))
    second = data_store.save_workout_adaptation_event(1, pending["id"], dict(event))
    events = data_store.list_workout_adaptation_events(1, unacknowledged=False)

    assert first is not None
    assert second is not None
    assert first["id"] == second["id"]
    assert len(events) == 1


def test_processed_food_log_client_id_cannot_schedule_duplicate_window(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    row = _food_log("retry-client", calories=300, protein_g=8, confidence=0.9)
    first_pending = workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)
    workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    retried = workout_adaptation.enqueue_accepted_food_logs(
        1,
        [row],
        clock=start + timedelta(minutes=5),
    )

    assert retried["id"] == first_pending["id"]
    assert data_store.list_pending_workout_adaptation_windows(1) == []


def test_multiple_due_windows_do_not_stack_volume_reductions_in_one_poll(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    first = _food_log("due-one", calories=250, protein_g=5, confidence=0.9)
    second = _food_log("due-two", meal_id="meal-2", calories=250, protein_g=5, confidence=0.9)
    workout_adaptation.enqueue_accepted_food_logs(1, [first], clock=datetime(2026, 5, 24, 12, 0, 0))
    workout_adaptation.enqueue_accepted_food_logs(1, [second], clock=datetime(2026, 5, 24, 13, 0, 0))

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[first, second],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=datetime(2026, 5, 24, 13, 3, 1),
    )

    assert len(events) == 2
    chest = next(ex for ex in patched["exercises"] if ex["machine"] == "Chest Press")
    assert chest["target_sets"] == 3


def test_later_no_change_window_does_not_erase_prior_applied_patch(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    applied = _food_log("due-applied", calories=250, protein_g=5, confidence=0.9)
    no_change = _food_log("due-no-change", meal_id="meal-no-change", calories=1100, protein_g=40, confidence=0.4)
    workout_adaptation.enqueue_accepted_food_logs(1, [applied], clock=datetime(2026, 5, 24, 12, 0, 0))
    workout_adaptation.enqueue_accepted_food_logs(1, [no_change], clock=datetime(2026, 5, 24, 13, 0, 0))

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[applied, no_change],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=datetime(2026, 5, 24, 13, 3, 1),
    )

    assert [workout_adaptation.project_event(event)["status"] for event in events] == ["applied", "no_change"]
    chest = next(ex for ex in patched["exercises"] if ex["machine"] == "Chest Press")
    assert chest["target_sets"] == 3


def test_missing_trigger_rows_do_not_consume_pending_window(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    pending = data_store.enqueue_workout_adaptation_pending(
        1,
        date="2026-05-24",
        meal_id="meal-missing",
        food_log_client_ids=["missing-client"],
        window_started_at="2026-05-24T12:00:00",
        window_closes_at="2026-05-24T12:03:00",
    )

    _patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=datetime(2026, 5, 24, 12, 3, 1),
    )

    assert events == []
    still_pending = data_store.list_pending_workout_adaptation_windows(1)
    assert len(still_pending) == 1
    assert still_pending[0]["id"] == pending["id"]


def test_stale_pending_window_expires_instead_of_adapting_later_plan(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 22, 0, 0)
    row = _food_log(
        "old-wine",
        date="2026-05-24",
        logged_at="2026-05-24T22:00:00",
        item_name="Wine with dinner",
        confidence=0.92,
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=100, protein_pct=100),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-27",
        clock=start + timedelta(days=3),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["applies_to"] == "expired"
    assert event["confidence"]["no_change_reason"] == "stale_window"
    assert patched.get("training_recommendation") != "recovery"


def test_previous_day_ordinary_meal_does_not_use_today_underfuel_for_volume_cut(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    row = _food_log(
        "yesterday-lunch",
        date="2026-05-24",
        logged_at="2026-05-24T12:00:00",
        item_name="Chicken bowl",
        confidence=0.92,
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(calories_pct=20, protein_pct=20),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-25",
        clock=start + timedelta(hours=24),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["change_type"] == "none"
    assert patched["estimated_minutes"] == 60
