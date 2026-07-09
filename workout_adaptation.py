"""FIT-136 nutrition-to-workout adaptation engine.

The engine is intentionally timer-free. Accepted food saves create a persisted
pending window, and normal workout/event reads evaluate due windows with an
injectable clock.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import re
from typing import Callable, Iterable
import uuid

from data_store import (
    enqueue_workout_adaptation_pending,
    list_due_workout_adaptation_pending,
    save_workout_adaptation_event,
)

COALESCING_WINDOW_SECONDS = 180
MIN_WORKOUT_CONFIDENCE = 0.65
UNDER_FUELED_CALORIES_PCT = 60
UNDER_FUELED_PROTEIN_PCT = 50
LOW_PROTEIN_PCT = 80
SODIUM_RECOVERY_CONTEXT_MG = 2300
LATE_MEAL_HOUR = 20
# Multiple accepted entries avoid treating a single meal as full-day intake.
MIN_SAME_DAY_FUELING_ENTRIES = 2
# Typical fueling is expected to begin by the morning meal window.
TYPICAL_FUELING_WINDOW_START_HOUR = 8
# Eighteen hundred permits same-day decisions before late-meal deferral begins.
FULL_DAY_FUELING_COVERAGE_HOUR = 18
HEAVY_MEAL_CALORIES = 900

SCIENCE_CITATIONS = {
    "academy_dc_acsm_nutrition_performance": {
        "title": "Position of the Academy of Nutrition and Dietetics, Dietitians of Canada, and the American College of Sports Medicine: Nutrition and Athletic Performance",
        "pmid": "26920240",
        "doi": "10.1016/j.jand.2015.12.006",
        "url": "https://pubmed.ncbi.nlm.nih.gov/26920240/",
    },
    "issn_nutrient_timing": {
        "title": "International society of sports nutrition position stand: nutrient timing",
        "pmid": "28919842",
        "doi": "10.1186/s12970-017-0189-4",
        "url": "https://pubmed.ncbi.nlm.nih.gov/28919842/",
    },
    "issn_protein_exercise": {
        "title": "International Society of Sports Nutrition Position Stand: protein and exercise",
        "pmid": "28642676",
        "doi": "10.1186/s12970-017-0177-8",
        "url": "https://pubmed.ncbi.nlm.nih.gov/28642676/",
    },
    "acsm_resistance_progression": {
        "title": "American College of Sports Medicine position stand. Progression models in resistance training for healthy adults",
        "pmid": "19204579",
        "doi": "10.1249/MSS.0b013e3181915670",
        "url": "https://pubmed.ncbi.nlm.nih.gov/19204579/",
    },
}

MORAL_LABELS = {"bad", "cheat", "guilt", "punish", "punitive", "unhealthy"}
ALCOHOL_TERMS = {
    "alcohol",
    "beer",
    "wine",
    "cocktail",
    "margarita",
    "whiskey",
    "vodka",
    "tequila",
    "bourbon",
    "rum",
    "hard seltzer",
}
ALCOHOL_PATTERNS = tuple(
    re.compile(r"(?<![a-z0-9])" + re.escape(term).replace(r"\ ", r"\s+") + r"(?![a-z0-9])")
    for term in sorted(ALCOHOL_TERMS, key=len, reverse=True)
)


def _clock_now(clock: Callable[[], datetime] | datetime | None = None) -> datetime:
    if isinstance(clock, datetime):
        return clock
    if callable(clock):
        return clock()
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def enqueue_accepted_food_logs(
    user_id: int,
    food_logs: Iterable[dict],
    *,
    clock: Callable[[], datetime] | datetime | None = None,
) -> dict | None:
    """Record that accepted food should be considered after the coalescing window."""
    rows = [row for row in food_logs if isinstance(row, dict)]
    if not rows:
        return None
    now = _clock_now(clock)
    first = rows[0]
    date_s = str(first.get("date") or now.date().isoformat())
    meal_id = first.get("meal_id") or first.get("client_id")
    client_ids = [str(row.get("client_id")) for row in rows if row.get("client_id")]
    return enqueue_workout_adaptation_pending(
        user_id,
        date=date_s,
        meal_id=str(meal_id) if meal_id else None,
        food_log_client_ids=client_ids,
        window_started_at=_iso(now),
        window_closes_at=_iso(now + timedelta(seconds=COALESCING_WINDOW_SECONDS)),
    )


def freeze_fit137_contract() -> dict:
    """Return the stable event contract consumed by FIT-137."""
    return {
        "endpoint": "/api/workout-adaptation-events",
        "ack_endpoint": "/api/workout-adaptation-events/{event_id}/ack",
        "delivery": "pollable_event_feed",
        "no_change_signal": {
            "status": "no_change",
            "silent": True,
            "change_type": "none",
            "confidence.no_change_reason": (
                "low_confidence | incomplete_day_coverage | no_science_supported_change"
            ),
        },
        "event_fields": [
            "id",
            "status",
            "silent",
            "change_type",
            "date",
            "applies_to",
            "reason",
            "confidence",
            "trigger",
            "nutrition_context",
            "patch",
            "before_remaining_plan",
            "after_remaining_plan",
            "active_workout",
            "reason_metadata",
            "acknowledged_at",
            "created_at",
        ],
    }


def plan_adjustment_contract() -> dict:
    """Public nutrition-context plan_adjustment block after FIT-136."""
    return {
        "allowed": True,
        "mode": "accepted_food_only",
        "delivery": "pollable_event_feed",
        "events_endpoint": freeze_fit137_contract()["endpoint"],
        "coalescing_window_seconds": COALESCING_WINDOW_SECONDS,
        "no_change_signal": freeze_fit137_contract()["no_change_signal"],
    }


def apply_due_adaptations(
    user_id: int,
    recommendation: dict,
    *,
    food_log_entries: list[dict],
    nutrition_context: dict,
    settings: dict,
    plan_date: str,
    active_workout_open: bool = False,
    completed_sets_by_exercise: dict[str, int] | None = None,
    clock: Callable[[], datetime] | datetime | None = None,
) -> tuple[dict, list[dict]]:
    """Evaluate closed coalescing windows and return a patched recommendation."""
    now = _clock_now(clock)
    patched = copy.deepcopy(recommendation or {})
    events: list[dict] = []
    base_recommendation = copy.deepcopy(recommendation or {})
    for pending in list_due_workout_adaptation_pending(user_id, now_iso=_iso(now)):
        if not _pending_applies_to_plan(pending, plan_date):
            event = _expired_pending_event(pending, settings=settings, evaluated_at=now)
            saved = save_workout_adaptation_event(user_id, pending["id"], event)
            if saved:
                events.append(saved)
            continue
        relevant_logs = _trigger_logs(food_log_entries, pending)
        if not relevant_logs:
            continue
        day_logs = _day_logs(food_log_entries, pending.get("date"))
        if _should_defer_for_next_day(pending, plan_date, relevant_logs, day_logs, nutrition_context):
            continue
        event, event_patched = _evaluate_pending_window(
            pending,
            base_recommendation,
            relevant_logs=relevant_logs,
            day_logs=day_logs,
            nutrition_context=nutrition_context,
            settings=settings,
            plan_date=plan_date,
            active_workout_open=active_workout_open,
            completed_sets_by_exercise=completed_sets_by_exercise or {},
            evaluated_at=now,
        )
        saved = save_workout_adaptation_event(user_id, pending["id"], event)
        if saved:
            events.append(saved)
            if event.get("status") == "applied":
                patched = event_patched
    return patched, events


def _should_defer_for_next_day(
    pending: dict,
    plan_date: str,
    relevant_logs: list[dict],
    day_logs: list[dict],
    nutrition_context: dict,
) -> bool:
    if str(pending.get("date")) != str(plan_date):
        return False
    signals = _nutrition_signals(relevant_logs, nutrition_context, day_logs=day_logs)
    codes = {signal["code"] for signal in signals}
    next_day_only = {"alcohol", "late_meal", "high_sodium"}
    return bool(codes & next_day_only)


def _pending_applies_to_plan(pending: dict, plan_date: str) -> bool:
    pending_dt = _parse_iso(str(pending.get("date") or ""))
    plan_dt = _parse_iso(str(plan_date or ""))
    if pending_dt is None or plan_dt is None:
        return str(pending.get("date")) == str(plan_date)
    age_days = (plan_dt.date() - pending_dt.date()).days
    return age_days in {0, 1}


def _expired_pending_event(pending: dict, *, settings: dict, evaluated_at: datetime) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "date": pending.get("date"),
        "status": "no_change",
        "silent": True,
        "change_type": "none",
        "applies_to": "expired",
        "reason": "Preserved the workout; this saved-meal adaptation window is stale.",
        "confidence": {
            "level": "low",
            "score": 0.0,
            "no_change_reason": "stale_window",
        },
        "trigger": {
            "meal_ids": pending.get("meal_ids") or [],
            "food_log_client_ids": pending.get("food_log_client_ids") or [],
            "window_started_at": pending.get("window_started_at"),
            "window_closes_at": pending.get("window_closes_at"),
            "evaluated_at": _iso(evaluated_at),
        },
        "nutrition_context": {"totals": {}, "targets": {}, "deltas": {}, "signals": []},
        "patch": {
            "available_time_minutes": int(settings.get("available_time_minutes") or 60),
            "estimated_minutes": 0,
            "operations": [],
        },
        "before_remaining_plan": {},
        "after_remaining_plan": {},
        "active_workout": {
            "was_open": False,
            "updated_live": False,
            "preserve_completed_work": True,
        },
        "reason_metadata": {
            "rules": ["stale_window"],
            "citations": [],
            "fit137_contract": freeze_fit137_contract(),
        },
        "created_at": _iso(evaluated_at),
    }


def project_event(event: dict) -> dict:
    """Project a stored event to the frozen FIT-137 public contract."""
    return {
        "id": event.get("id"),
        "status": event.get("status"),
        "silent": bool(event.get("silent")),
        "change_type": event.get("change_type"),
        "date": event.get("date"),
        "applies_to": event.get("applies_to"),
        "reason": event.get("reason"),
        "confidence": event.get("confidence") or {},
        "trigger": event.get("trigger") or {},
        "nutrition_context": event.get("nutrition_context") or {},
        "patch": event.get("patch") or {},
        "before_remaining_plan": event.get("before_remaining_plan") or {},
        "after_remaining_plan": event.get("after_remaining_plan") or {},
        "active_workout": event.get("active_workout") or {},
        "reason_metadata": event.get("reason_metadata") or {},
        "acknowledged_at": event.get("acknowledged_at"),
        "created_at": event.get("created_at"),
    }


def _trigger_logs(food_log_entries: list[dict], pending: dict) -> list[dict]:
    client_ids = set(pending.get("food_log_client_ids") or [])
    meal_ids = set(pending.get("meal_ids") or [])
    return [
        row
        for row in food_log_entries or []
        if _accepted_food_log(row)
        and (
            (row.get("client_id") in client_ids)
            or (row.get("meal_id") and row.get("meal_id") in meal_ids)
        )
    ]


def _day_logs(food_log_entries: list[dict], date_s: str | None) -> list[dict]:
    return [
        row
        for row in food_log_entries or []
        if str(row.get("date") or "") == str(date_s or "") and _accepted_food_log(row)
    ]


def _accepted_food_log(row: dict) -> bool:
    status = str(row.get("correction_state") or row.get("status") or "").strip().lower()
    if status in {"pending", "pending_review", "needs_review", "review"}:
        return False
    if row.get("pending_review") is True or row.get("accepted") is False:
        return False
    return True


def _evaluate_pending_window(
    pending: dict,
    recommendation: dict,
    *,
    relevant_logs: list[dict],
    day_logs: list[dict],
    nutrition_context: dict,
    settings: dict,
    plan_date: str,
    active_workout_open: bool,
    completed_sets_by_exercise: dict[str, int],
    evaluated_at: datetime,
) -> tuple[dict, dict]:
    before = _remaining_plan_snapshot(recommendation)
    signals = _nutrition_signals(relevant_logs, nutrition_context, day_logs=day_logs)
    confidence = _workout_confidence(relevant_logs, signals)
    applies_to = "next_day" if str(plan_date) > str(pending.get("date")) else "today"
    same_day_fueling_coverage = _same_day_fueling_coverage(
        nutrition_context,
        day_logs,
        signals=signals,
    )
    decision = _decide_change(
        signals,
        confidence,
        applies_to,
        same_day_fueling_coverage=same_day_fueling_coverage,
    )
    patched = copy.deepcopy(recommendation or {})
    operations: list[dict] = []
    if decision["status"] == "applied":
        patched, operations = _apply_conservative_patch(
            patched,
            change_type=decision["change_type"],
            completed_sets_by_exercise=completed_sets_by_exercise,
        )
        patched, clamp_ops = _clamp_to_available_time(
            patched,
            int(settings.get("available_time_minutes") or 60),
            completed_sets_by_exercise=completed_sets_by_exercise,
        )
        operations.extend(clamp_ops)
    after = _remaining_plan_snapshot(patched)
    event = {
        "id": str(uuid.uuid4()),
        "date": pending.get("date"),
        "status": decision["status"],
        "silent": decision["silent"],
        "change_type": decision["change_type"],
        "applies_to": applies_to,
        "reason": decision["reason"],
        "confidence": {
            "level": confidence["level"],
            "score": confidence["score"],
            "no_change_reason": decision.get("no_change_reason"),
        },
        "trigger": {
            "meal_ids": pending.get("meal_ids") or [],
            "food_log_client_ids": pending.get("food_log_client_ids") or [],
            "window_started_at": pending.get("window_started_at"),
            "window_closes_at": pending.get("window_closes_at"),
            "evaluated_at": _iso(evaluated_at),
        },
        "nutrition_context": _nutrition_context_contract(nutrition_context, signals),
        "patch": {
            "available_time_minutes": int(settings.get("available_time_minutes") or 60),
            "estimated_minutes": int(patched.get("estimated_minutes") or 0),
            "operations": operations,
        },
        "before_remaining_plan": before,
        "after_remaining_plan": after,
        "active_workout": {
            "was_open": bool(active_workout_open),
            "updated_live": bool(active_workout_open and decision["status"] == "applied"),
            "preserve_completed_work": True,
        },
        "reason_metadata": _reason_metadata(
            decision,
            applies_to=applies_to,
            same_day_fueling_coverage=same_day_fueling_coverage,
        ),
        "created_at": _iso(evaluated_at),
    }
    _assert_neutral_event(event)
    return event, patched


def _nutrition_signals(
    relevant_logs: list[dict],
    nutrition_context: dict,
    *,
    day_logs: list[dict] | None = None,
) -> list[dict]:
    totals = nutrition_context.get("totals") or {}
    percentages = nutrition_context.get("percentages") or {}
    signals: list[dict] = []
    calories_pct = float(percentages.get("calories") or 0)
    protein_pct = float(percentages.get("protein") or 0)
    if calories_pct and calories_pct < UNDER_FUELED_CALORIES_PCT:
        signals.append({"code": "under_fueled", "label": "Below fueling target"})
    if protein_pct and protein_pct < LOW_PROTEIN_PCT:
        signals.append({"code": "low_protein", "label": "Lower protein progress"})
    sodium_total = max(
        sum(int(row.get("sodium_mg") or 0) for row in relevant_logs),
        sum(int(row.get("sodium_mg") or 0) for row in (day_logs or [])),
        int(totals.get("sodium_mg") or 0),
    )
    if sodium_total >= SODIUM_RECOVERY_CONTEXT_MG:
        signals.append({"code": "high_sodium", "label": "High sodium"})
    if any(_logged_hour(row) is not None and _logged_hour(row) >= LATE_MEAL_HOUR for row in relevant_logs):
        signals.append({"code": "late_meal", "label": "Late meal"})
    if any(_contains_alcohol(row) for row in relevant_logs):
        signals.append({"code": "alcohol", "label": "Alcohol"})
    if any(int(row.get("calories") or 0) >= HEAVY_MEAL_CALORIES for row in relevant_logs):
        signals.append({"code": "heavy_meal", "label": "Heavy meal"})
    if any(_mostly_carbs(row) for row in relevant_logs):
        signals.append({"code": "mostly_carbs", "label": "Mostly carbs"})
    return signals


def _workout_confidence(relevant_logs: list[dict], signals: list[dict]) -> dict:
    confidences = [
        float(row.get("confidence"))
        for row in relevant_logs
        if isinstance(row.get("confidence"), (int, float)) and not isinstance(row.get("confidence"), bool)
    ]
    score = round(min(confidences), 3) if confidences else 0.0
    if score >= 0.8 and signals:
        level = "high"
    elif score >= MIN_WORKOUT_CONFIDENCE and signals:
        level = "medium"
    else:
        level = "low"
    return {"score": score, "level": level}


def _same_day_fueling_coverage(
    nutrition_context: dict,
    day_logs: list[dict],
    *,
    signals: list[dict],
) -> dict:
    totals = nutrition_context.get("totals") or {}
    percentages = nutrition_context.get("percentages") or {}
    entries_count = int(totals.get("entries_count") or 0)
    logged_hours = [hour for row in day_logs for hour in [_logged_hour(row)] if hour is not None]
    coverage_hour = max(logged_hours) if logged_hours else None
    target_fraction = _fueling_target_fraction(coverage_hour)
    effective_calorie_pct_threshold = round(UNDER_FUELED_CALORIES_PCT * target_fraction, 3)
    effective_protein_pct_threshold = round(LOW_PROTEIN_PCT * target_fraction, 3)
    calories_pct = float(percentages.get("calories") or 0)
    protein_pct = float(percentages.get("protein") or 0)
    signal_codes = {signal["code"] for signal in signals}

    if entries_count < MIN_SAME_DAY_FUELING_ENTRIES or coverage_hour is None:
        mode = "incomplete"
        sufficient = False
    elif coverage_hour >= FULL_DAY_FUELING_COVERAGE_HOUR:
        mode = "full_day"
        sufficient = True
        effective_calorie_pct_threshold = float(UNDER_FUELED_CALORIES_PCT)
        effective_protein_pct_threshold = float(LOW_PROTEIN_PCT)
    else:
        mode = "prorated"
        sufficient = (
            (
                "under_fueled" in signal_codes
                and calories_pct < effective_calorie_pct_threshold
            )
            or (
                "low_protein" in signal_codes
                and protein_pct < effective_protein_pct_threshold
            )
        )

    return {
        "sufficient": sufficient,
        "mode": mode,
        "entries_count": entries_count,
        "coverage_hour": coverage_hour,
        "target_fraction": target_fraction,
        "effective_calorie_pct_threshold": effective_calorie_pct_threshold,
        "effective_protein_pct_threshold": effective_protein_pct_threshold,
    }


def _fueling_target_fraction(coverage_hour: int | None) -> float:
    if coverage_hour is None:
        return 0.0
    window_duration = FULL_DAY_FUELING_COVERAGE_HOUR - TYPICAL_FUELING_WINDOW_START_HOUR
    elapsed_hours = coverage_hour - TYPICAL_FUELING_WINDOW_START_HOUR
    return round(max(0.0, min(1.0, elapsed_hours / window_duration)), 3)


def _decide_change(
    signals: list[dict],
    confidence: dict,
    applies_to: str,
    *,
    same_day_fueling_coverage: dict | None = None,
) -> dict:
    codes = {signal["code"] for signal in signals}
    if confidence["score"] < MIN_WORKOUT_CONFIDENCE:
        return _no_change("low_confidence", "Saved nutrition confidence is below the workout-change threshold.")
    if applies_to == "next_day":
        if {"alcohol", "late_meal", "high_sodium"} & codes:
            return {
                "status": "applied",
                "silent": False,
                "change_type": "rest_recovery",
                "reason": "Shifted tomorrow toward recovery based on saved nutrition timing and recovery context.",
                "rules": ["next_day_recovery_context", *sorted(codes)],
            }
        return _no_change("no_science_supported_change", "Preserved the workout; saved nutrition did not support a next-day change.")
    if {"under_fueled", "low_protein"} & codes:
        if same_day_fueling_coverage is not None and not same_day_fueling_coverage["sufficient"]:
            return _no_change(
                "incomplete_day_coverage",
                "Preserved the workout because today's nutrition log does not yet cover enough of the day.",
            )
        return {
            "status": "applied",
            "silent": False,
            "change_type": "reduce_volume",
            "reason": "Reduced remaining volume because saved nutrition is below today's fueling targets.",
            "rules": ["under_fueled_no_increase", *sorted(codes)],
        }
    if codes == {"heavy_meal"} or codes == {"mostly_carbs"} or codes == {"heavy_meal", "mostly_carbs"}:
        return _no_change("no_science_supported_change", "Preserved the workout; saved nutrition alone did not justify a change.")
    if not codes:
        return _no_change("no_science_supported_change", "Preserved the workout; no recovery-relevant nutrition signal was present.")
    return _no_change("no_science_supported_change", "Preserved the workout; conservative rules did not support a change.")


def _no_change(reason_code: str, reason: str) -> dict:
    return {
        "status": "no_change",
        "silent": True,
        "change_type": "none",
        "reason": reason,
        "no_change_reason": reason_code,
        "rules": [reason_code],
    }


def _reason_metadata(
    decision: dict,
    *,
    applies_to: str,
    same_day_fueling_coverage: dict,
) -> dict:
    metadata = {
        "rules": decision["rules"],
        "citations": _citations_for_rules(decision["rules"]),
        "fit137_contract": freeze_fit137_contract(),
    }
    if applies_to == "today":
        metadata["same_day_fueling_coverage"] = copy.deepcopy(same_day_fueling_coverage)
    return metadata


def _apply_conservative_patch(
    recommendation: dict,
    *,
    change_type: str,
    completed_sets_by_exercise: dict[str, int],
) -> tuple[dict, list[dict]]:
    patched = copy.deepcopy(recommendation or {})
    operations: list[dict] = []
    if change_type in {"reduce_volume", "rest_recovery", "remove_strength_sets"}:
        for exercise in patched.get("exercises") or []:
            name = _exercise_name(exercise)
            current = int(exercise.get("target_sets") or exercise.get("sets") or 1)
            _preserve_time_per_set_minutes(exercise, current)
            completed = int(completed_sets_by_exercise.get(name, 0))
            floor = max(1, completed)
            new_sets = max(floor, current - 1)
            if new_sets < current:
                exercise["target_sets"] = new_sets
                exercise["sets"] = new_sets
                operations.append({
                    "op": "reduce_sets",
                    "exercise": name,
                    "from": current,
                    "to": new_sets,
                    "preserved_completed_sets": completed,
                })
        if change_type == "rest_recovery":
            patched["training_recommendation"] = "recovery"
            patched["recovery_note"] = "Nutrition timing and recovery context favor a lower-stress session."
            operations.append({"op": "set_recovery_note", "scope": "next_day"})
    _recompute_estimated_minutes(patched)
    return patched, operations


def _clamp_to_available_time(
    recommendation: dict,
    available_time_minutes: int,
    *,
    completed_sets_by_exercise: dict[str, int],
) -> tuple[dict, list[dict]]:
    patched = copy.deepcopy(recommendation or {})
    operations: list[dict] = []
    _recompute_estimated_minutes(patched)
    while int(patched.get("estimated_minutes") or 0) > available_time_minutes:
        exercises = patched.get("exercises") or []
        changed = False
        for exercise in reversed(exercises):
            name = _exercise_name(exercise)
            current = int(exercise.get("target_sets") or exercise.get("sets") or 1)
            _preserve_time_per_set_minutes(exercise, current)
            completed = int(completed_sets_by_exercise.get(name, 0))
            if current > max(1, completed):
                exercise["target_sets"] = current - 1
                exercise["sets"] = current - 1
                operations.append({
                    "op": "clamp_reduce_sets",
                    "exercise": name,
                    "from": current,
                    "to": current - 1,
                    "available_time_minutes": available_time_minutes,
                    "preserved_completed_sets": completed,
                })
                changed = True
                break
        if not changed:
            cardio = patched.get("cardio") if isinstance(patched.get("cardio"), dict) else None
            duration = int((cardio or {}).get("duration_minutes") or 0)
            if cardio and duration > 0:
                new_duration = max(0, duration - 5)
                cardio["duration_minutes"] = new_duration
                operations.append({
                    "op": "clamp_cardio_minutes",
                    "from": duration,
                    "to": new_duration,
                    "available_time_minutes": available_time_minutes,
                })
                changed = True
        if not changed:
            break
        _recompute_estimated_minutes(patched)
    residual = int(patched.get("estimated_minutes") or 0)
    if residual > available_time_minutes:
        operations.append({
            "op": "cap_exceeded",
            "floor_minutes": residual,
            "available_time_minutes": available_time_minutes,
        })
    return patched, operations


def _recompute_estimated_minutes(recommendation: dict) -> None:
    exercises = recommendation.get("exercises") or []
    if not exercises:
        return
    total = 10
    for exercise in exercises:
        sets = int(exercise.get("target_sets") or exercise.get("sets") or 1)
        per_set = _exercise_time_per_set_minutes(exercise, sets)
        exercise["estimated_time"] = int(round(sets * per_set))
        total += exercise["estimated_time"]
    cardio = recommendation.get("cardio") if isinstance(recommendation.get("cardio"), dict) else None
    if cardio:
        total += int(cardio.get("duration_minutes") or 0)
    recommendation["estimated_minutes"] = int(total)
    recommendation["estimated_duration"] = f"{int(total)} min"


def _exercise_time_per_set_minutes(exercise: dict, sets: int) -> float:
    if exercise.get("time_per_set_minutes") is not None:
        return float(exercise.get("time_per_set_minutes") or 3)
    estimated = exercise.get("estimated_time")
    if isinstance(estimated, (int, float)) and not isinstance(estimated, bool) and sets > 0:
        return max(1.0, float(estimated) / float(sets))
    return 3.0


def _preserve_time_per_set_minutes(exercise: dict, sets: int) -> None:
    if exercise.get("time_per_set_minutes") is not None:
        return
    estimated = exercise.get("estimated_time")
    if isinstance(estimated, (int, float)) and not isinstance(estimated, bool) and sets > 0:
        exercise["time_per_set_minutes"] = round(max(1.0, float(estimated) / float(sets)), 3)


def _remaining_plan_snapshot(recommendation: dict) -> dict:
    return {
        "estimated_minutes": recommendation.get("estimated_minutes"),
        "focus": recommendation.get("focus") or recommendation.get("goal_name"),
        "exercises": [
            {
                "name": _exercise_name(exercise),
                "target_sets": exercise.get("target_sets") or exercise.get("sets"),
                "target_reps": exercise.get("target_reps") or exercise.get("reps"),
            }
            for exercise in (recommendation.get("exercises") or [])
        ],
        "cardio": copy.deepcopy(recommendation.get("cardio")),
    }


def _nutrition_context_contract(nutrition_context: dict, signals: list[dict]) -> dict:
    totals = nutrition_context.get("totals") or {}
    targets = nutrition_context.get("targets") or {}
    remaining = nutrition_context.get("remaining") or {}
    return {
        "totals": copy.deepcopy(totals),
        "targets": copy.deepcopy(targets),
        "deltas": copy.deepcopy(remaining),
        "signals": signals,
    }


def _citations_for_rules(rules: list[str]) -> list[dict]:
    keys = {
        "academy_dc_acsm_nutrition_performance",
        "issn_nutrient_timing",
        "issn_protein_exercise",
        "acsm_resistance_progression",
    }
    if any("alcohol" in rule or "late_meal" in rule or "high_sodium" in rule for rule in rules):
        keys.add("issn_nutrient_timing")
    if any("protein" in rule or "under_fueled" in rule for rule in rules):
        keys.add("issn_protein_exercise")
    return [SCIENCE_CITATIONS[key] for key in sorted(keys)]


def _logged_hour(row: dict) -> int | None:
    for key in ("logged_at", "local_timestamp", "created_at"):
        dt = _parse_iso(row.get(key))
        if dt is not None:
            return dt.hour
    return None


def _contains_alcohol(row: dict) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in ("item_name", "portion_description", "context_note", "meal_type", "source")
    ).lower()
    return any(pattern.search(text) for pattern in ALCOHOL_PATTERNS)


def _mostly_carbs(row: dict) -> bool:
    carbs = float(row.get("carbs_g") or 0)
    protein = float(row.get("protein_g") or 0)
    fat = float(row.get("fat_g") or 0)
    calories = float(row.get("calories") or 0)
    return calories >= 250 and carbs >= 45 and carbs >= (protein + fat) * 2


def _exercise_name(exercise: dict) -> str:
    return str(
        exercise.get("name")
        or exercise.get("machine")
        or exercise.get("exercise")
        or exercise.get("muscle_group")
        or "exercise"
    )


def _assert_neutral_event(event: dict) -> None:
    generated_text = [
        str(event.get("reason") or ""),
        *[
            str(signal.get("label") or "")
            for signal in ((event.get("nutrition_context") or {}).get("signals") or [])
            if isinstance(signal, dict)
        ],
        *[str(rule) for rule in ((event.get("reason_metadata") or {}).get("rules") or [])],
    ]
    text = " ".join(generated_text).lower()
    found = sorted(
        label
        for label in MORAL_LABELS
        if re.search(r"(?<![a-z0-9])" + re.escape(label) + r"(?![a-z0-9])", text)
    )
    if found:
        raise ValueError(f"workout adaptation event contains non-neutral labels: {found}")
