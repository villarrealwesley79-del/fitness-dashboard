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


def _full_day_fueling_logs(trigger_row: dict, *, client_id: str) -> list[dict]:
    earlier_row = _food_log(
        client_id,
        meal_id=f"meal-{client_id}",
        logged_at="2026-05-24T08:00:00",
        calories=690,
        protein_g=29.5,
        confidence=0.9,
    )
    return [earlier_row, trigger_row]


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


def _nutrition_context(*, calories_pct=100, protein_pct=100, sodium_mg=700, entries_count=1):
    return {
        "totals": {
            "calories": int(2200 * calories_pct / 100),
            "protein_g": round(150 * protein_pct / 100, 1),
            "carbs_g": 200,
            "fat_g": 70,
            "sodium_mg": sodium_mg,
            "entries_count": entries_count,
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
    start = datetime(2026, 5, 24, 18, 0, 0)
    earlier_row = _food_log(
        "under-fueled-earlier",
        calories=690,
        protein_g=29.5,
        confidence=0.9,
        meal_id="meal-under-fueled-earlier",
        logged_at="2026-05-24T08:00:00",
    )
    row = _food_log(
        "under-fueled-evening",
        calories=300,
        protein_g=8,
        confidence=0.9,
        logged_at="2026-05-24T18:00:00",
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[earlier_row, row],
        nutrition_context=_nutrition_context(
            calories_pct=45,
            protein_pct=25,
            entries_count=2,
        ),
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
    coverage = event["reason_metadata"]["same_day_fueling_coverage"]
    assert coverage["sufficient"] is True
    assert coverage["mode"] == "full_day"
    assert coverage["entries_count"] == 2
    assert coverage["meal_windows_count"] == 2
    assert coverage["first_coverage_hour"] == 8
    assert coverage["first_coverage_minute"] == 8 * 60
    assert coverage["coverage_hour"] == 18
    assert coverage["coverage_span_hours"] == 10
    assert coverage["coverage_minute"] == 18 * 60
    assert coverage["coverage_span_minutes"] == 600
    assert coverage["target_fraction"] == 1.0
    assert coverage["effective_calorie_pct_threshold"] == 60.0
    assert coverage["effective_protein_pct_threshold"] == 80.0


def test_late_day_single_partial_meal_skips_volume_reduction(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 17, 43, 0)
    row = _food_log(
        "partial-day-late-meal",
        calories=1100,
        protein_g=75,
        confidence=0.9,
        logged_at="2026-05-24T17:43:00",
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[row],
        nutrition_context=_nutrition_context(
            calories_pct=50,
            protein_pct=50,
            entries_count=1,
        ),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["silent"] is True
    assert event["change_type"] == "none"
    assert event["confidence"]["no_change_reason"] == "incomplete_day_coverage"
    assert event["patch"]["estimated_minutes"] == 60
    assert patched["estimated_minutes"] == 60
    assert "incomplete_day_coverage" not in {
        signal["code"] for signal in event["nutrition_context"]["signals"]
    }
    coverage = event["reason_metadata"]["same_day_fueling_coverage"]
    assert coverage["sufficient"] is False
    assert coverage["mode"] == "incomplete"
    assert coverage["entries_count"] == 1
    assert coverage["meal_windows_count"] == 1
    assert coverage["first_coverage_hour"] == 17
    assert coverage["first_coverage_minute"] == (17 * 60) + 43
    assert coverage["coverage_hour"] == 17
    assert coverage["coverage_span_hours"] == 0
    assert coverage["coverage_minute"] == (17 * 60) + 43
    assert coverage["coverage_span_minutes"] == 0


def test_multi_item_meal_does_not_satisfy_same_day_coverage(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 18, 0, 0)
    first_row = _food_log(
        "multi-item-first",
        calories=690,
        protein_g=29.5,
        confidence=0.9,
        meal_id="meal-multi-item",
        logged_at="2026-05-24T18:00:00",
    )
    trigger_row = _food_log(
        "multi-item-trigger",
        calories=300,
        protein_g=8,
        confidence=0.9,
        meal_id="meal-multi-item",
        logged_at="2026-05-24T18:00:00",
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [trigger_row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[first_row, trigger_row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25, entries_count=2),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["confidence"]["no_change_reason"] == "incomplete_day_coverage"
    assert patched["estimated_minutes"] == 60
    coverage = event["reason_metadata"]["same_day_fueling_coverage"]
    assert coverage["entries_count"] == 2
    assert coverage["meal_windows_count"] == 1
    assert coverage["first_coverage_hour"] == 18
    assert coverage["first_coverage_minute"] == 18 * 60
    assert coverage["coverage_hour"] == 18
    assert coverage["coverage_span_hours"] == 0
    assert coverage["coverage_minute"] == 18 * 60
    assert coverage["coverage_span_minutes"] == 0


def test_late_two_hour_observation_does_not_satisfy_full_day_coverage(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 18, 0, 0)
    earlier_row = _food_log(
        "late-first",
        calories=690,
        protein_g=29.5,
        confidence=0.9,
        meal_id="meal-late-first",
        logged_at="2026-05-24T17:00:00",
    )
    trigger_row = _food_log(
        "late-trigger",
        calories=300,
        protein_g=8,
        confidence=0.9,
        meal_id="meal-late-trigger",
        logged_at="2026-05-24T18:00:00",
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [trigger_row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[earlier_row, trigger_row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=25, entries_count=2),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["confidence"]["no_change_reason"] == "incomplete_day_coverage"
    assert patched["estimated_minutes"] == 60
    coverage = event["reason_metadata"]["same_day_fueling_coverage"]
    assert coverage["mode"] == "incomplete"
    assert coverage["first_coverage_minute"] == 17 * 60
    assert coverage["coverage_minute"] == 18 * 60
    assert coverage["coverage_span_minutes"] == 60
    assert coverage["first_coverage_hour"] == 17
    assert coverage["coverage_hour"] == 18
    assert coverage["coverage_span_hours"] == 1.0


def test_one_minute_prorated_span_does_not_satisfy_coverage(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 9, 0, 0)
    earlier_row = _food_log(
        "minute-first",
        calories=100,
        protein_g=2,
        confidence=0.9,
        meal_id="meal-minute-first",
        logged_at="2026-05-24T08:59:00",
    )
    trigger_row = _food_log(
        "minute-trigger",
        calories=100,
        protein_g=2,
        confidence=0.9,
        meal_id="meal-minute-trigger",
        logged_at="2026-05-24T09:00:00",
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [trigger_row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[earlier_row, trigger_row],
        nutrition_context=_nutrition_context(calories_pct=1, protein_pct=1, entries_count=2),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["confidence"]["no_change_reason"] == "incomplete_day_coverage"
    assert patched["estimated_minutes"] == 60
    coverage = event["reason_metadata"]["same_day_fueling_coverage"]
    assert coverage["mode"] == "incomplete"
    assert coverage["first_coverage_minute"] == (8 * 60) + 59
    assert coverage["coverage_minute"] == 9 * 60
    assert coverage["coverage_span_minutes"] == 1
    assert coverage["first_coverage_hour"] == 8
    assert coverage["coverage_hour"] == 9
    assert coverage["coverage_span_hours"] == 0.017


def test_morning_full_entry_count_requires_prorated_deficit(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    earlier_row = _food_log(
        "morning-first",
        calories=500,
        protein_g=25,
        meal_id="meal-morning-first",
        logged_at="2026-05-24T08:00:00",
    )
    row = _food_log(
        "morning-second",
        calories=490,
        protein_g=50,
        logged_at="2026-05-24T12:00:00",
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[earlier_row, row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=50, entries_count=2),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["confidence"]["no_change_reason"] == "incomplete_day_coverage"
    assert patched["estimated_minutes"] == 60
    assert event["reason_metadata"]["same_day_fueling_coverage"] == {
        "sufficient": False,
        "mode": "prorated",
        "entries_count": 2,
        "meal_windows_count": 2,
        "first_coverage_hour": 8,
        "first_coverage_minute": 8 * 60,
        "coverage_hour": 12,
        "coverage_span_hours": 4,
        "coverage_minute": 12 * 60,
        "coverage_span_minutes": 240,
        "target_fraction": 0.4,
        "effective_calorie_pct_threshold": 24.0,
        "effective_protein_pct_threshold": 32.0,
    }


def test_midday_prorated_deficit_reduces_volume(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    earlier_row = _food_log(
        "midday-first",
        calories=200,
        protein_g=10,
        meal_id="meal-midday-first",
        logged_at="2026-05-24T08:00:00",
    )
    row = _food_log(
        "midday-second",
        calories=240,
        protein_g=20,
        logged_at="2026-05-24T12:00:00",
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[earlier_row, row],
        nutrition_context=_nutrition_context(calories_pct=20, protein_pct=20, entries_count=2),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "applied"
    assert event["change_type"] == "reduce_volume"
    assert patched["estimated_minutes"] < 60
    coverage = event["reason_metadata"]["same_day_fueling_coverage"]
    assert coverage["sufficient"] is True
    assert coverage["mode"] == "prorated"
    assert coverage["meal_windows_count"] == 2
    assert coverage["first_coverage_hour"] == 8
    assert coverage["first_coverage_minute"] == 8 * 60
    assert coverage["coverage_hour"] == 12
    assert coverage["coverage_span_hours"] == 4
    assert coverage["coverage_minute"] == 12 * 60
    assert coverage["coverage_span_minutes"] == 240
    assert coverage["target_fraction"] == 0.4


def test_prorated_coverage_ignores_zero_percentage_without_a_signal(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 12, 0, 0)
    earlier_row = _food_log(
        "zero-protein-first",
        calories=500,
        protein_g=25,
        meal_id="meal-zero-protein-first",
        logged_at="2026-05-24T08:00:00",
    )
    row = _food_log(
        "zero-protein-second",
        calories=490,
        protein_g=0,
        logged_at="2026-05-24T12:00:00",
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[earlier_row, row],
        nutrition_context=_nutrition_context(calories_pct=45, protein_pct=0, entries_count=2),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    event = workout_adaptation.project_event(events[0])
    assert event["status"] == "no_change"
    assert event["confidence"]["no_change_reason"] == "incomplete_day_coverage"
    assert patched["estimated_minutes"] == 60
    assert event["reason_metadata"]["same_day_fueling_coverage"]["sufficient"] is False


def test_clamp_emits_cap_exceeded_marker_when_floor_exceeds_available_time():
    recommendation = {
        "id": "rec-fit224",
        "focus": "lower",
        "estimated_minutes": 0,
        "exercises": [
            {
                "machine": "Back Squat",
                "muscle_group": "legs",
                "target_sets": 7,
                "target_reps": 3,
                "time_per_set_minutes": 3,
            },
            {
                "machine": "Bench Press",
                "muscle_group": "chest",
                "target_sets": 5,
                "target_reps": 3,
                "time_per_set_minutes": 3,
            },
        ],
    }
    completed = {"Back Squat": 7, "Bench Press": 5}
    patched, operations = workout_adaptation._clamp_to_available_time(
        recommendation,
        30,
        completed_sets_by_exercise=completed,
    )

    squat = next(ex for ex in patched["exercises"] if ex["machine"] == "Back Squat")
    bench = next(ex for ex in patched["exercises"] if ex["machine"] == "Bench Press")

    assert squat["target_sets"] == 7
    assert bench["target_sets"] == 5
    assert not any(op["op"] == "clamp_reduce_sets" for op in operations)
    assert patched["estimated_minutes"] == 46
    assert patched["estimated_minutes"] > 30
    cap_ops = [op for op in operations if op["op"] == "cap_exceeded"]
    assert len(cap_ops) == 1
    cap = cap_ops[0]
    assert cap["available_time_minutes"] == 30
    assert cap["floor_minutes"] == 46


def test_clamp_no_cap_exceeded_marker_when_cap_reachable():
    recommendation = {
        "id": "rec-fit224-control",
        "focus": "lower",
        "estimated_minutes": 0,
        "exercises": [
            {
                "machine": "Back Squat",
                "muscle_group": "legs",
                "target_sets": 7,
                "target_reps": 3,
                "time_per_set_minutes": 3,
            },
            {
                "machine": "Bench Press",
                "muscle_group": "chest",
                "target_sets": 5,
                "target_reps": 3,
                "time_per_set_minutes": 3,
            },
        ],
    }
    completed = {}
    patched, operations = workout_adaptation._clamp_to_available_time(
        recommendation,
        30,
        completed_sets_by_exercise=completed,
    )

    assert patched["estimated_minutes"] <= 30
    assert any(op["op"] == "clamp_reduce_sets" for op in operations)
    assert not any(op["op"] == "cap_exceeded" for op in operations)


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
    start = datetime(2026, 5, 24, 18, 0, 0)
    row = _food_log(
        "active-under-fueled",
        calories=300,
        protein_g=8,
        confidence=0.9,
        logged_at="2026-05-24T18:00:00",
    )
    day_logs = _full_day_fueling_logs(row, client_id="active-under-fueled-earlier")
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    recommendation = _recommendation()
    recommendation["exercises"][0]["target_sets"] = 3
    recommendation["exercises"][0]["sets"] = 3
    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        recommendation,
        food_log_entries=day_logs,
        nutrition_context=_nutrition_context(
            calories_pct=45,
            protein_pct=25,
            entries_count=2,
        ),
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
    user_facing_text = " ".join(
        [
            event["reason"],
            *(signal["label"] for signal in event["nutrition_context"]["signals"]),
            *(citation["title"] for citation in citations),
        ]
    ).lower()
    for banned in workout_adaptation.MORAL_LABELS:
        assert banned not in user_facing_text


def test_neutral_language_guard_ignores_user_controlled_ids(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 18, 0, 0)
    row = _food_log(
        "bad-photo-1",
        calories=300,
        protein_g=8,
        confidence=0.9,
        logged_at="2026-05-24T18:00:00",
    )
    day_logs = _full_day_fueling_logs(row, client_id="bad-photo-1-earlier")
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    _patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=day_logs,
        nutrition_context=_nutrition_context(
            calories_pct=45,
            protein_pct=25,
            entries_count=2,
        ),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=start + timedelta(minutes=3, seconds=1),
    )

    assert workout_adaptation.project_event(events[0])["status"] == "applied"


def test_clamp_preserves_generated_non_default_set_timing(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    start = datetime(2026, 5, 24, 18, 0, 0)
    row = _food_log(
        "strength-under-fueled",
        calories=300,
        protein_g=8,
        confidence=0.9,
        logged_at="2026-05-24T18:00:00",
    )
    day_logs = _full_day_fueling_logs(row, client_id="strength-under-fueled-earlier")
    recommendation = _recommendation()
    for exercise in recommendation["exercises"]:
        exercise.pop("time_per_set_minutes", None)
    recommendation["exercises"][0]["estimated_time"] = 20
    recommendation["exercises"][1]["estimated_time"] = 15
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=start)

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        recommendation,
        food_log_entries=day_logs,
        nutrition_context=_nutrition_context(
            calories_pct=45,
            protein_pct=25,
            entries_count=2,
        ),
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


def _saved_adaptation_event(user_id: int, *, client_id: str, created_at: str, status: str = "applied"):
    pending = data_store.enqueue_workout_adaptation_pending(
        user_id,
        date="2026-05-24",
        meal_id="meal-1",
        food_log_client_ids=[client_id],
        window_started_at="2026-05-24T12:00:00",
        window_closes_at="2026-05-24T12:03:00",
    )
    return data_store.save_workout_adaptation_event(
        user_id,
        pending["id"],
        {
            "date": "2026-05-24",
            "status": status,
            "silent": status != "applied",
            "change_type": "reduce_volume" if status == "applied" else "none",
            "applies_to": "today",
            "reason": "Adjusted the workout." if status == "applied" else "Preserved the workout.",
            "trigger": {"meal_ids": ["meal-1"], "food_log_client_ids": [client_id]},
            "created_at": created_at,
        },
    )


def test_unacknowledged_feed_prioritizes_applied_events_over_newer_silent_rows(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    applied = _saved_adaptation_event(
        1,
        client_id="applied-source",
        created_at="2026-05-24T12:03:01",
    )
    for index in range(11):
        _saved_adaptation_event(
            1,
            client_id=f"silent-source-{index}",
            created_at=f"2026-05-24T13:{index:02d}:00",
            status="no_change",
        )

    events = data_store.list_workout_adaptation_events(1, unacknowledged=True, limit=10)

    assert applied["id"] in {event["id"] for event in events}
    assert events[0]["id"] == applied["id"]


def test_correcting_source_food_log_marks_unacknowledged_adaptation_stale(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("source-meal", meal_id="meal-1")
    event = _saved_adaptation_event(
        1,
        client_id="source-meal",
        created_at="2026-05-24T12:03:01",
    )

    _food_log(
        "source-meal",
        meal_id="meal-1",
        correction_state="corrected",
        calories=650,
    )

    stored = next(
        item
        for item in data_store.list_workout_adaptation_events(1, unacknowledged=True)
        if item["id"] == event["id"]
    )
    assert stored["status"] == "stale"
    assert stored["reason"] == "Source meal changed; this workout update is no longer current."


def test_identical_corrected_food_log_replay_keeps_adaptation_applied(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    original = _food_log("source-meal", meal_id="meal-1")
    event = _saved_adaptation_event(
        1,
        client_id="source-meal",
        created_at="2026-05-24T12:03:01",
    )

    replay = {
        key: original.get(key)
        for key in (
            "client_id", "date", "logged_at", "meal_id", "item_name",
            "portion_description", "meal_type", "calories", "protein_g",
            "carbs_g", "fat_g", "sodium_mg", "fiber_g", "confidence", "source",
        )
    }
    replay["correction_state"] = "corrected"
    data_store.add_food_log(1, replay)

    stored = next(
        item
        for item in data_store.list_workout_adaptation_events(1, unacknowledged=True)
        if item["id"] == event["id"]
    )
    assert stored["status"] == "applied"


def test_corrected_food_log_meal_reassignment_marks_adaptation_stale(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    original = _food_log("source-meal", meal_id="meal-1")
    event = _saved_adaptation_event(
        1,
        client_id="source-meal",
        created_at="2026-05-24T12:03:01",
    )

    replay = {
        key: original.get(key)
        for key in (
            "client_id", "date", "logged_at", "item_name", "portion_description",
            "meal_type", "calories", "protein_g", "carbs_g", "fat_g",
            "sodium_mg", "fiber_g", "confidence", "source",
        )
    }
    replay.update(meal_id="meal-2", correction_state="corrected")
    data_store.add_food_log(1, replay)

    stored = next(
        item
        for item in data_store.list_workout_adaptation_events(1, unacknowledged=True)
        if item["id"] == event["id"]
    )
    assert stored["status"] == "stale"


def test_deleting_source_food_log_marks_unacknowledged_adaptation_stale(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("source-meal", meal_id="meal-1")
    event = _saved_adaptation_event(
        1,
        client_id="source-meal",
        created_at="2026-05-24T12:03:01",
    )

    assert data_store.delete_food_log_by_client_id(1, "source-meal") is True

    stored = next(
        item
        for item in data_store.list_workout_adaptation_events(1, unacknowledged=True)
        if item["id"] == event["id"]
    )
    assert stored["status"] == "stale"
    assert stored["reason"] == "Source meal was deleted; this workout update is no longer current."


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
    earlier = _food_log("due-coverage", meal_id="meal-due-coverage", logged_at="2026-05-24T08:00:00")
    first = _food_log("due-one", calories=250, protein_g=5, confidence=0.9, logged_at="2026-05-24T18:00:00")
    second = _food_log("due-two", meal_id="meal-2", calories=250, protein_g=5, confidence=0.9, logged_at="2026-05-24T19:00:00")
    workout_adaptation.enqueue_accepted_food_logs(1, [first], clock=datetime(2026, 5, 24, 18, 0, 0))
    workout_adaptation.enqueue_accepted_food_logs(1, [second], clock=datetime(2026, 5, 24, 19, 0, 0))

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[earlier, first, second],
        nutrition_context=_nutrition_context(
            calories_pct=45,
            protein_pct=25,
            entries_count=3,
        ),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=datetime(2026, 5, 24, 19, 3, 1),
    )

    assert len(events) == 2
    chest = next(ex for ex in patched["exercises"] if ex["machine"] == "Chest Press")
    assert chest["target_sets"] == 3


def test_later_no_change_window_does_not_erase_prior_applied_patch(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    earlier = _food_log("due-later-coverage", meal_id="meal-due-later-coverage", logged_at="2026-05-24T08:00:00")
    applied = _food_log("due-applied", calories=250, protein_g=5, confidence=0.9, logged_at="2026-05-24T18:00:00")
    no_change = _food_log("due-no-change", meal_id="meal-no-change", calories=1100, protein_g=40, confidence=0.4, logged_at="2026-05-24T19:00:00")
    workout_adaptation.enqueue_accepted_food_logs(1, [applied], clock=datetime(2026, 5, 24, 18, 0, 0))
    workout_adaptation.enqueue_accepted_food_logs(1, [no_change], clock=datetime(2026, 5, 24, 19, 0, 0))

    patched, events = workout_adaptation.apply_due_adaptations(
        1,
        _recommendation(),
        food_log_entries=[earlier, applied, no_change],
        nutrition_context=_nutrition_context(
            calories_pct=45,
            protein_pct=25,
            entries_count=3,
        ),
        settings={"available_time_minutes": 60},
        plan_date="2026-05-24",
        clock=datetime(2026, 5, 24, 19, 3, 1),
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
