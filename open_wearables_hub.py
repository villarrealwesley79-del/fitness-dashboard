"""Open Wearables hub orchestration.

Extracted from app.py (FIT-250). Keeps route code out of the details of
projecting Open Wearables hub payloads into sync markers, sync metadata,
wearable-source status, stored facts, and recommendation-guard modifiers.

Functions here are dependency-injected on purpose: app.py owns profile-key
resolution, module-level caches/state, and provider-status/stale-source
logic (``_open_wearables_profile_key``, ``_open_wearables_public_status``,
``_open_wearables_replacement_source_dates``, ``_recommendation_sources_payload``,
etc. all stay in app.py, untouched). This module only receives already-resolved
values and callables so it never has to import back from app.py.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Callable

from wearable_fact_store import (
    WearableDailyFact,
    list_recommendation_facts,
    upsert_daily_facts,
    upsert_wearable_source,
)


def _payload_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("summaries", "events", "data", "items", "records", "days"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return [payload]


def _first_value(row: dict, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _number(row: dict, *keys):
    value = _first_value(row, *keys)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _row_date(row: dict, fallback: str) -> str:
    value = _first_value(row, "date", "day", "summary_date", "end", "end_time", "start", "start_time", "timestamp")
    return str(value or fallback)[:10]


def _source_provider(row: dict) -> str | None:
    source = row.get("source")
    value = source.get("provider") if isinstance(source, dict) else None
    value = value or row.get("provider") or row.get("provider_id")
    return str(value) if value is not None else None


def _workout_category(label: object) -> str | None:
    text = str(label or "").strip().lower()
    if not text:
        return None
    if any(word in text for word in ("strength", "lift", "weight", "resistance")):
        return "strength_training"
    if any(word in text for word in ("run", "walk", "cycle", "bike", "swim", "cardio")):
        return "cardio"
    return "other"


def payload_marker(payload):
    """Return a stable marker for live hub inputs without carrying timestamps."""
    if payload is None:
        return None
    try:
        raw = json.dumps(payload, sort_keys=True, default=str)
    except TypeError:
        raw = str(payload)
    return {
        "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "bytes": len(raw),
    }


def workout_marker(data, *, sleep_extractor: Callable[[object], dict | None]) -> dict:
    data = data if isinstance(data, dict) else {}
    sleep = sleep_extractor(data.get("sleep"))
    return {
        "configured": True,
        "sleep": {
            "duration_min": sleep.get("duration_min") if sleep else None,
            "avg_hr": sleep.get("avg_hr") if sleep else None,
            "event_time": sleep.get("event_time") if sleep else None,
            "recent": sleep.get("recent") if sleep else None,
        },
        "workouts": payload_marker(data.get("workouts")),
        "activity_summary": payload_marker(data.get("activity_summary")),
    }


def sync_count(payload):
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return None
    for key in ("records", "samples", "events", "data", "items", "summaries", "days"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def sync_error_code(key):
    stable_codes = {
        "auth": "open_wearables_auth_error",
        "config": "open_wearables_config_error",
        "sleep": "open_wearables_sync_error",
        "workouts": "open_wearables_sync_error",
        "activity_summary": "open_wearables_sync_error",
        "recovery_summary": "open_wearables_sync_error",
        "body_summary": "open_wearables_sync_error",
    }
    return stable_codes.get(str(key), "open_wearables_sync_error")


def public_error_key(key):
    public_names = {"auth", "config", "sleep", "workouts", "activity_summary", "recovery_summary", "body_summary"}
    name = str(key)
    return name if name in public_names else "sync"


def sync_metadata(data, *, now: Callable[[], datetime] = datetime.now) -> dict:
    data = data if isinstance(data, dict) else {}
    counts = {}
    for key in ("sleep", "workouts", "activity_summary", "recovery_summary", "body_summary"):
        count = sync_count(data.get(key))
        if count is not None:
            counts[key] = count

    errors = {}
    raw_errors = data.get("errors")
    if isinstance(raw_errors, dict):
        for key, value in raw_errors.items():
            if value:
                errors[public_error_key(key)] = sync_error_code(key)

    fetched_at = data.get("fetched_at")
    if not isinstance(fetched_at, str):
        fetched_at = now().isoformat()

    return {
        "status": "success",
        "source": "open_wearables",
        "fetched_at": fetched_at,
        "counts": counts,
        "errors": errors,
    }


def status_source(status_payload: dict) -> dict:
    """Project an already-computed status payload (from
    app.py's ``_open_wearables_public_status``) into a wearable-source entry.

    This is a pure projection: all stale-source / replacement-source /
    setup_hint logic lives in app.py and is expected to already be baked
    into ``status_payload`` by the caller.
    """
    status_payload = status_payload or {}
    return {
        "source": "open_wearables",
        "provider_id": "open_wearables",
        "label": "Open Wearables",
        "status": status_payload.get("status"),
        "configured": bool(status_payload.get("configured")),
        "connected": status_payload.get("status") == "connected",
        "last_sync_attempt": status_payload.get("last_checked_at"),
        "last_data_point": None,
        "score_state": None,
        "source_kind": "hub",
        "providers": status_payload.get("providers") or [],
        "facts_ready": bool(status_payload.get("facts_ready")),
        "replacement_sources": status_payload.get("replacement_sources") or [],
        "replacement_source_dates": status_payload.get("replacement_source_dates") or {},
        "capabilities": {
            "providers": True,
            "metrics": True,
            "workouts": True,
            "history": True,
            "sync": True,
        },
        "used_for_recommendation": status_payload.get("status") == "connected",
        "detail": (
            "Open Wearables is configured as the wearable hub."
            if status_payload.get("configured")
            else "Open Wearables hub needs configuration before provider data can sync."
        ),
    }


def store_wearable_facts(
    data,
    *,
    db_file: str,
    profile_key: str,
    activity_extractor: Callable[[object], list[dict]],
    sleep_extractor: Callable[[object], dict | None],
    row_replacement_sources: Callable[[object], list[str]],
    now: Callable[[], datetime] = datetime.now,
) -> int:
    data = data if isinstance(data, dict) else {}
    fetched_at = data.get("fetched_at") if isinstance(data.get("fetched_at"), str) else now().isoformat()
    errors = data.get("errors") if isinstance(data.get("errors"), dict) else {}
    status = "error" if errors else "fresh"
    replacement_source_dates = {}

    def mark_replacement_sources(raw_row, date_s):
        if not raw_row or not date_s:
            return
        for source_key in row_replacement_sources(raw_row):
            if date_s > replacement_source_dates.get(source_key, ""):
                replacement_source_dates[source_key] = date_s

    facts = []
    activity = activity_extractor(data.get("activity_summary"))
    if activity:
        latest = sorted(activity, key=lambda row: row.get("date") or datetime.min.date())[-1]
        date_s = latest["date"].strftime("%Y-%m-%d")
        added_activity_fact = False
        if latest.get("steps") is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "steps", latest.get("steps"), "count", confidence="medium", freshness=status))
            added_activity_fact = True
        if latest.get("resting") is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "resting_heart_rate", latest.get("resting"), "bpm", confidence="medium", freshness=status))
            added_activity_fact = True
        if latest.get("active_minutes") is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "active_minutes", latest.get("active_minutes"), "min", confidence="medium", freshness=status))
            added_activity_fact = True
        if latest.get("active_calories") is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "active_calories", latest.get("active_calories"), "kcal", confidence="medium", freshness=status))
            added_activity_fact = True
        if latest.get("distance") is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "distance", latest.get("distance"), "m", confidence="medium", freshness=status))
            added_activity_fact = True
        if added_activity_fact:
            mark_replacement_sources(latest.get("raw"), date_s)

    sleep = sleep_extractor(data.get("sleep"))
    if sleep and sleep.get("event_time"):
        date_s = (sleep.get("event_time") or fetched_at)[:10]
        added_sleep_fact = False
        if sleep.get("duration_min") is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "sleep_duration", sleep.get("duration_min"), "min", confidence="medium", freshness=status))
            added_sleep_fact = True
        if sleep.get("avg_hr") is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "sleep_avg_heart_rate", sleep.get("avg_hr"), "bpm", confidence="medium", freshness=status))
            added_sleep_fact = True
        for stage, value in (sleep.get("stages_min") or {}).items():
            if value is not None and stage in {"deep", "rem", "light", "awake"}:
                facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", f"sleep_{stage}_duration", value, "min", confidence="medium", freshness=status))
                added_sleep_fact = True
        if added_sleep_fact:
            mark_replacement_sources(sleep.get("raw"), date_s)

    metric_groups = (
        ("recovery_summary", {
            "recovery_score": (("recovery_score", "score", "readiness_score"), "score"),
            "heart_rate_variability": (("avg_hrv_sdnn_ms", "hrv", "hrv_ms", "heart_rate_variability"), "ms"),
            "resting_heart_rate": (("resting_heart_rate_bpm", "resting_heart_rate", "resting_hr", "rhr"), "bpm"),
            "blood_oxygen": (("avg_spo2_percent", "spo2", "blood_oxygen", "oxygen_saturation"), "%"),
            "sleep_efficiency": (("sleep_efficiency_percent",), "%"),
            "respiratory_rate": (("respiratory_rate", "breathing_rate"), "breaths/min"),
            "skin_temperature": (("skin_temperature", "temperature_delta"), "c"),
        }),
    )
    for payload_key, mappings in metric_groups:
        for row in _payload_rows(data.get(payload_key)):
            date_s = _row_date(row, fetched_at)
            provider = _source_provider(row)
            before_count = len(facts)
            for metric, (aliases, unit) in mappings.items():
                value = _number(row, *aliases)
                if value is not None:
                    facts.append(WearableDailyFact(
                        date_s, "open_wearables", "Open Wearables", metric, value, unit,
                        confidence="medium", freshness=status, source_provider=provider,
                    ))
            if payload_key == "recovery_summary":
                sleep_seconds = _number(row, "sleep_duration_seconds")
                if sleep_seconds is not None:
                    facts.append(WearableDailyFact(
                        date_s, "open_wearables", "Open Wearables", "sleep_duration",
                        sleep_seconds / 60.0, "min", confidence="medium", freshness=status,
                        source_provider=provider,
                    ))
            if len(facts) > before_count:
                mark_replacement_sources(row, date_s)

    for body in _payload_rows(data.get("body_summary")):
        date_s = _row_date(body.get("averaged") if isinstance(body.get("averaged"), dict) else body, fetched_at)
        provider = _source_provider(body)
        before_count = len(facts)
        body_groups = (
            (body.get("slow_changing") if isinstance(body.get("slow_changing"), dict) else body, {
                "weight": (("weight_kg", "weight"), "kg"),
                "body_fat_percent": (("body_fat_percent", "body_fat"), "%"),
                "muscle_mass": (("muscle_mass_kg", "muscle_mass"), "kg"),
                "body_mass_index": (("bmi", "body_mass_index"), "kg/m2"),
            }),
            (body.get("averaged") if isinstance(body.get("averaged"), dict) else {}, {
                "resting_heart_rate": (("resting_heart_rate_bpm",), "bpm"),
                "heart_rate_variability_sdnn": (("avg_hrv_sdnn_ms",), "ms"),
                "heart_rate_variability_rmssd": (("avg_hrv_rmssd_ms",), "ms"),
            }),
            (body.get("latest") if isinstance(body.get("latest"), dict) else {}, {
                "body_temperature": (("body_temperature_celsius",), "c"),
                "skin_temperature": (("skin_temperature_celsius",), "c"),
            }),
        )
        for values, mappings in body_groups:
            for metric, (aliases, unit) in mappings.items():
                value = _number(values, *aliases)
                if value is not None:
                    facts.append(WearableDailyFact(
                        date_s, "open_wearables", "Open Wearables", metric, value, unit,
                        confidence="medium", freshness=status, source_provider=provider,
                    ))
        if len(facts) > before_count:
            mark_replacement_sources(body, date_s)

    for row in _payload_rows(data.get("workouts")):
        date_s = _row_date(row, fetched_at)
        before_count = len(facts)
        original_label = _first_value(row, "activity_type", "workout_type", "sport", "name", "type")
        provenance = {
            "category": _workout_category(original_label),
            "source_id": str(_first_value(row, "id", "event_id", "workout_id") or "") or None,
            "source_provider": _source_provider(row),
            "original_label": str(original_label) if original_label is not None else None,
        }
        workout_metrics = {
            "workout_duration": ((_first_value(row, "duration_min", "duration_minutes")), "min"),
            "workout_active_calories": ((_first_value(row, "active_calories", "calories")), "kcal"),
            "workout_load": ((_first_value(row, "load", "strain", "training_load")), "score"),
            "workout_avg_heart_rate": ((_first_value(row, "avg_hr", "average_heart_rate")), "bpm"),
            "workout_max_heart_rate": ((_first_value(row, "max_hr", "max_heart_rate")), "bpm"),
        }
        for metric, (raw_value, unit) in workout_metrics.items():
            try:
                value = float(raw_value) if raw_value is not None else None
            except (TypeError, ValueError):
                value = None
            if value is not None:
                facts.append(WearableDailyFact(
                    date_s, "open_wearables", "Open Wearables", metric, value, unit,
                    confidence="medium", freshness=status, **provenance,
                ))
        if len(facts) > before_count:
            mark_replacement_sources(row, date_s)

    upsert_wearable_source(db_file, {
        "provider_id": "open_wearables",
        "label": "Open Wearables",
        "status": status,
        "last_data_point": max(replacement_source_dates.values()) if replacement_source_dates else fetched_at[:10],
        "last_sync_attempt": fetched_at,
        "capabilities": {
            "metrics": True,
            "workouts": True,
            "history": True,
            "sync": True,
            "replacement_sources": sorted(replacement_source_dates),
            "replacement_source_dates": replacement_source_dates,
        },
        "used_for_recommendation": status != "error",
    }, profile_key=profile_key)

    if facts:
        upsert_daily_facts(db_file, facts, profile_key=profile_key)
    return len(facts)


def recommendation_facts(db_file: str, profile_key: str, limit: int = 20) -> list[dict]:
    return [
        fact for fact in list_recommendation_facts(db_file, limit=limit, profile_key=profile_key)
        if fact.get("provider_id") == "open_wearables"
        and fact.get("freshness") in {"fresh", "aging"}
    ]


def conservative_modifier(facts) -> dict:
    facts = facts or []
    latest_by_metric = {}
    for fact in facts:
        metric = fact.get("metric")
        if metric and metric not in latest_by_metric:
            latest_by_metric[metric] = fact

    applied = []
    details = []
    sleep = latest_by_metric.get("sleep_duration")
    try:
        sleep_min = float(sleep.get("value")) if sleep and sleep.get("value") is not None else None
    except Exception:
        sleep_min = None
    if sleep_min is not None and sleep_min < 360:
        applied.append("sleep_caution")
        details.append(f"Open Wearables sleep duration {int(round(sleep_min))} min")

    active = latest_by_metric.get("active_minutes")
    try:
        active_min = float(active.get("value")) if active and active.get("value") is not None else None
    except Exception:
        active_min = None
    if active_min is not None and active_min >= 90:
        applied.append("activity_caution")
        details.append(f"Open Wearables active minutes {int(round(active_min))}")

    return {
        "applied": bool(applied),
        "applied_modifiers": applied,
        "detail": "; ".join(details) + " -> recommendation held conservative." if details else None,
    }


def apply_recommendation_guard(recommendation, facts, *, downgrade_once: Callable[[str], str]):
    modifier = conservative_modifier(facts)
    if not modifier.get("applied"):
        return recommendation, modifier
    return downgrade_once(recommendation), modifier
