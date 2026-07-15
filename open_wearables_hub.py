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

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import hashlib
import json
import math
import re
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


def _authoritative_collection_rows(payload: object) -> list[dict] | None:
    if isinstance(payload, list):
        return payload if all(isinstance(row, dict) for row in payload) else None
    if not isinstance(payload, dict):
        return None
    collections = [
        payload[key]
        for key in ("summaries", "events", "data", "items", "records", "days")
        if key in payload
    ]
    if not collections or not all(
        isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        for rows in collections
    ):
        return None
    return collections[0]


def _valid_body_snapshot(payload: object) -> bool:
    if payload is None:
        return True
    if not isinstance(payload, dict):
        return False
    if set(payload) != {"source", "slow_changing", "averaged", "latest"}:
        return False
    source = payload["source"]
    if (
        not isinstance(source, dict)
        or set(source) - {"provider", "device"}
        or not isinstance(source.get("provider"), str)
        or not source["provider"].strip()
        or (source.get("device") is not None and not isinstance(source.get("device"), str))
    ):
        return False

    def valid_number(value: object, *, integer: bool = False) -> bool:
        if value is None:
            return True
        if isinstance(value, bool):
            return False
        if integer:
            return isinstance(value, int)
        return isinstance(value, (int, float)) and math.isfinite(float(value))

    slow = payload["slow_changing"]
    slow_fields = {
        "weight_kg", "height_cm", "body_fat_percent", "muscle_mass_kg", "bmi", "age",
    }
    if (
        not isinstance(slow, dict)
        or set(slow) - slow_fields
        or any(
            not valid_number(value, integer=(key == "age"))
            for key, value in slow.items()
        )
    ):
        return False

    averaged = payload["averaged"]
    averaged_fields = {
        "period_days", "resting_heart_rate_bpm", "avg_hrv_sdnn_ms",
        "avg_hrv_rmssd_ms", "period_start", "period_end",
    }
    if not isinstance(averaged, dict) or set(averaged) - averaged_fields:
        return False
    if not valid_number(averaged.get("resting_heart_rate_bpm"), integer=True):
        return False
    if not valid_number(averaged.get("avg_hrv_sdnn_ms")):
        return False
    if not valid_number(averaged.get("avg_hrv_rmssd_ms")):
        return False
    period_days = averaged.get("period_days")
    period_start = _utc_temporal_value(averaged.get("period_start"))
    period_end = _utc_temporal_value(averaged.get("period_end"))
    if (
        isinstance(period_days, bool)
        or not isinstance(period_days, int)
        or not 1 <= period_days <= 7
        or period_start is None
        or period_end is None
        or period_start >= period_end
    ):
        return False

    latest = payload["latest"]
    latest_fields = {
        "body_temperature_celsius", "body_temperature_measured_at",
        "skin_temperature_celsius", "skin_temperature_measured_at",
        "blood_pressure", "blood_pressure_measured_at",
    }
    if not isinstance(latest, dict) or set(latest) - latest_fields:
        return False
    for value_key, measured_at_key in (
        ("body_temperature_celsius", "body_temperature_measured_at"),
        ("skin_temperature_celsius", "skin_temperature_measured_at"),
    ):
        value = latest.get(value_key)
        measured_at = latest.get(measured_at_key)
        if not valid_number(value):
            return False
        if (value is None) != (measured_at is None):
            return False
        if measured_at is not None and _utc_temporal_value(measured_at) is None:
            return False

    blood_pressure = latest.get("blood_pressure")
    blood_pressure_measured_at = latest.get("blood_pressure_measured_at")
    if blood_pressure is not None:
        blood_pressure_fields = {
            "avg_systolic_mmhg", "avg_diastolic_mmhg", "max_systolic_mmhg",
            "max_diastolic_mmhg", "min_systolic_mmhg", "min_diastolic_mmhg",
            "reading_count",
        }
        if (
            not isinstance(blood_pressure, dict)
            or set(blood_pressure) - blood_pressure_fields
            or not isinstance(blood_pressure.get("avg_systolic_mmhg"), int)
            or isinstance(blood_pressure.get("avg_systolic_mmhg"), bool)
            or not isinstance(blood_pressure.get("avg_diastolic_mmhg"), int)
            or isinstance(blood_pressure.get("avg_diastolic_mmhg"), bool)
            or any(not valid_number(value, integer=True) for value in blood_pressure.values())
        ):
            return False
    if (blood_pressure is None) != (blood_pressure_measured_at is None):
        return False
    if (
        blood_pressure_measured_at is not None
        and _utc_temporal_value(blood_pressure_measured_at) is None
    ):
        return False

    measurement_present = (
        any(value is not None for value in slow.values())
        or any(
            averaged.get(key) is not None
            for key in ("resting_heart_rate_bpm", "avg_hrv_sdnn_ms", "avg_hrv_rmssd_ms")
        )
        or any(
            latest.get(key) is not None
            for key in ("body_temperature_celsius", "skin_temperature_celsius", "blood_pressure")
        )
    )
    if not measurement_present:
        return False
    return True


def _first_value(row: dict, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _number(row: dict, *keys):
    value = _first_value(row, *keys)
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return number if number is not None and math.isfinite(number) else None


def _finite_fact_value(value: object):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    return value


def _temporal_value(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _utc_temporal_value(value: object) -> datetime | None:
    parsed = _temporal_value(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _temporal_text(value: object) -> str | None:
    return str(value) if _temporal_value(value) is not None else None


def _row_date(row: dict, fallback: str | None = None) -> str | None:
    value = _first_value(row, "date", "day", "summary_date", "period_end", "end", "end_time", "start", "start_time", "timestamp")
    parsed = _temporal_value(value if value is not None else fallback)
    return parsed.date().isoformat() if parsed is not None else None


def _source_provider(row: dict) -> str | None:
    source = row.get("source")
    value = source.get("provider") if isinstance(source, dict) else None
    value = value or row.get("provider") or row.get("provider_id")
    if value is None:
        return None
    source_name = str(value).lower()
    if "healthkit" in source_name:
        return "apple"
    for provider_id in (
        "apple", "samsung", "google", "garmin", "polar", "suunto",
        "whoop", "strava", "oura", "fitbit", "ultrahuman",
    ):
        if provider_id in source_name:
            return provider_id
    return None


def _provider_display_name(provider_id: str | None) -> str:
    if not provider_id or provider_id == "open_wearables":
        return "Open Wearables"
    canonical_labels = {
        "oura": "Oura",
        "whoop": "WHOOP",
        "apple": "Apple Health",
        "samsung": "Samsung Health",
        "google": "Google Health Connect",
        "garmin": "Garmin",
        "polar": "Polar",
        "suunto": "Suunto",
        "strava": "Strava",
        "fitbit": "Fitbit",
        "ultrahuman": "Ultrahuman",
    }
    if str(provider_id).lower() in canonical_labels:
        return canonical_labels[str(provider_id).lower()]
    return str(provider_id).replace("_", " ").strip().title()


def _fact_metric_domain(fact: WearableDailyFact) -> str:
    metric = fact.metric
    if metric == "workout_load":
        return "load"
    if metric.startswith("workout_"):
        return "training_history"
    if metric.startswith("sleep_"):
        return "sleep"
    if metric in {"weight", "body_fat_percent", "muscle_mass", "body_mass_index", "body_temperature"}:
        return "body"
    if metric == "skin_temperature":
        return "body"
    if metric in {
        "recovery_score", "heart_rate_variability", "resting_heart_rate", "blood_oxygen",
        "respiratory_rate", "skin_temperature", "temperature_deviation",
        "resting_heart_rate_average", "heart_rate_variability_sdnn_average",
        "heart_rate_variability_rmssd_average",
    }:
        return "recovery"
    return "activity"


def _workout_category(label: object) -> str | None:
    text = str(label or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    strength_types = {"strength_training", "core_training", "traditional_strength_training", "functional_strength_training"}
    cardio_types = {
        "running", "trail_running", "treadmill", "walking", "hiking", "trail_hiking",
        "cycling", "mountain_biking", "indoor_cycling", "cyclocross", "e_biking",
        "swimming", "pool_swimming", "open_water_swimming", "elliptical",
        "rowing", "rowing_machine", "stair_climbing", "cardio_training",
        "mixed_cardio", "hiit", "running_treadmill", "cycling_stationary",
        "swimming_pool", "swimming_open_water", "stair_climbing_machine",
    }
    if text in strength_types or any(word in text for word in ("strength", "lift", "weight", "resistance")):
        return "strength_training"
    if text in cardio_types:
        return "cardio"
    return "other"


def _workout_zone(zone_offset: object) -> timezone | None:
    offset_text = str(zone_offset or "").strip()
    if not re.fullmatch(r"[+-](?:0\d|1\d|2[0-3]):[0-5]\d", offset_text):
        return None
    sign = -1 if offset_text.startswith("-") else 1
    hours_text, minutes_text = offset_text[1:].split(":", 1)
    return timezone(sign * timedelta(hours=int(hours_text), minutes=int(minutes_text)))


def _workout_local_date(observed_at: str, zone_offset: object) -> str | None:
    try:
        observed = _temporal_value(observed_at)
        if observed is None:
            return None
        offset_text = str(zone_offset or "").strip()
        if offset_text:
            target_zone = _workout_zone(offset_text)
            if target_zone is None:
                return None
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=target_zone)
            observed = observed.astimezone(target_zone)
        return observed.date().isoformat()
    except (TypeError, ValueError):
        return None


def _workout_utc_value(observed_at: str, zone_offset: object) -> datetime | None:
    try:
        observed = _temporal_value(observed_at)
        if observed is None:
            return None
        if observed.tzinfo is None:
            target_zone = _workout_zone(zone_offset)
            if target_zone is None:
                return None
            observed = observed.replace(tzinfo=target_zone)
        return observed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _fact_freshness(date_s: str, fetched_at: str) -> str:
    try:
        observed_text = str(date_s).replace("Z", "+00:00")
        fetched_text = str(fetched_at).replace("Z", "+00:00")
        observed = datetime.fromisoformat(observed_text)
        fetched = datetime.fromisoformat(fetched_text)
        if observed.tzinfo is None:
            observed = observed.astimezone()
        if fetched.tzinfo is None:
            fetched = fetched.astimezone()
        observed = observed.astimezone(timezone.utc)
        fetched = fetched.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return "unknown"
    age_hours = (fetched - observed).total_seconds() / 3600.0
    if age_hours < -1:
        return "unknown"
    if age_hours < 24:
        return "fresh"
    if age_hours < 48:
        return "aging"
    return "stale"


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
        "sleep_summary": payload_marker(data.get("sleep_summary")),
        "recovery_summary": payload_marker(data.get("recovery_summary")),
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
    return 1 if payload else 0


def sync_error_code(key):
    stable_codes = {
        "auth": "open_wearables_auth_error",
        "config": "open_wearables_config_error",
        "sleep": "open_wearables_sync_error",
        "sleep_summary": "open_wearables_sync_error",
        "workouts": "open_wearables_sync_error",
        "activity_summary": "open_wearables_sync_error",
        "recovery_summary": "open_wearables_sync_error",
        "body_summary": "open_wearables_sync_error",
    }
    return stable_codes.get(str(key), "open_wearables_sync_error")


def public_error_key(key):
    public_names = {
        "auth", "config", "sleep", "sleep_summary", "workouts",
        "activity_summary", "recovery_summary", "body_summary",
    }
    name = str(key)
    return name if name in public_names else "sync"


def sync_metadata(data, *, now: Callable[[], datetime] = datetime.now) -> dict:
    data = data if isinstance(data, dict) else {}
    counts = {}
    for key in (
        "sleep", "sleep_summary", "workouts", "activity_summary",
        "recovery_summary", "body_summary",
    ):
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
    fetched_at = _temporal_text(data.get("fetched_at")) or now().isoformat()
    errors = data.get("errors") if isinstance(data.get("errors"), dict) else {}
    workout_rows = _authoritative_collection_rows(data.get("workouts"))
    workout_query = data.get("_workout_query") if isinstance(data.get("_workout_query"), dict) else {}
    workout_query_start = _temporal_text(workout_query.get("start_at"))
    workout_query_end = _temporal_text(workout_query.get("end_at"))
    workout_window_start = _utc_temporal_value(workout_query_start)
    workout_window_end = _utc_temporal_value(workout_query_end)
    workout_snapshot_replacement_safe = (
        "workouts" not in errors
        and data.get("_workout_snapshot_complete") is True
        and workout_rows is not None
        and workout_query_start is not None
        and workout_query_end is not None
        and workout_window_start is not None
        and workout_window_end is not None
        and workout_window_start < workout_window_end
    )
    body_payload = data.get("body_summary")
    body_snapshot_valid = _valid_body_snapshot(body_payload)
    body_snapshot_replacement_safe = (
        "body_summary" in data
        and "body_summary" not in errors
        and not ({"auth", "config"} & errors.keys())
        and body_snapshot_valid
    )
    body_rows = _payload_rows(body_payload) if body_payload is not None and body_snapshot_valid else []
    sleep_summary_rows = _authoritative_collection_rows(data.get("sleep_summary"))
    sleep_summary_query = (
        data.get("_sleep_summary_query")
        if isinstance(data.get("_sleep_summary_query"), dict)
        else {}
    )
    sleep_summary_query_start = _temporal_text(sleep_summary_query.get("start_date"))
    sleep_summary_query_end = _temporal_text(sleep_summary_query.get("end_date"))
    sleep_summary_window_start = _temporal_value(sleep_summary_query_start)
    sleep_summary_window_end = _temporal_value(sleep_summary_query_end)
    sleep_summary_snapshot_replacement_safe = (
        "sleep_summary" not in errors
        and data.get("_sleep_summary_snapshot_complete") is True
        and sleep_summary_rows is not None
        and sleep_summary_query_start is not None
        and sleep_summary_query_end is not None
        and sleep_summary_window_start < sleep_summary_window_end
    )
    replacement_source_domain_dates = {}

    def mark_replacement_sources(raw_row, date_s, domain):
        if not raw_row or not date_s or not domain:
            return
        for source_key in row_replacement_sources(raw_row):
            domain_dates = replacement_source_domain_dates.setdefault(source_key, {})
            if date_s > domain_dates.get(domain, ""):
                domain_dates[domain] = date_s

    facts = []
    activity = activity_extractor(data.get("activity_summary"))
    if activity:
        latest = sorted(activity, key=lambda row: row.get("date") or datetime.min.date())[-1]
        date_s = latest["date"].strftime("%Y-%m-%d")
        activity_raw = latest.get("raw") if isinstance(latest.get("raw"), dict) else {}
        activity_provenance = {
            "source_id": str(_first_value(activity_raw, "id", "summary_id") or "") or None,
            "source_provider": _source_provider(activity_raw),
            "observed_at": None,
            "source_record_kind": "summary",
        }
        added_activity_fact = False
        steps = _finite_fact_value(latest.get("steps"))
        if steps is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "steps", steps, "count", confidence="medium", freshness=_fact_freshness(date_s, fetched_at), **activity_provenance))
            added_activity_fact = True
        resting = _finite_fact_value(latest.get("resting"))
        if resting is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "resting_heart_rate", resting, "bpm", confidence="medium", freshness=_fact_freshness(date_s, fetched_at), **activity_provenance))
            added_activity_fact = True
        active_minutes = _finite_fact_value(latest.get("active_minutes"))
        if active_minutes is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "active_minutes", active_minutes, "min", confidence="medium", freshness=_fact_freshness(date_s, fetched_at), **activity_provenance))
            added_activity_fact = True
        active_calories = _finite_fact_value(latest.get("active_calories"))
        if active_calories is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "active_calories", active_calories, "kcal", confidence="medium", freshness=_fact_freshness(date_s, fetched_at), **activity_provenance))
            added_activity_fact = True
        distance = _finite_fact_value(latest.get("distance"))
        if distance is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "distance", distance, "m", confidence="medium", freshness=_fact_freshness(date_s, fetched_at), **activity_provenance))
            added_activity_fact = True
        if added_activity_fact:
            mark_replacement_sources(latest.get("raw"), date_s, "activity")

    sleep = sleep_extractor(data.get("sleep"))
    sleep_event_time = _temporal_text(sleep.get("event_time")) if sleep else None
    if sleep and sleep_event_time:
        date_s = _temporal_value(sleep_event_time).date().isoformat()
        sleep_raw = sleep.get("raw") if isinstance(sleep.get("raw"), dict) else {}
        observed_at = _temporal_text(sleep.get("observed_at") or sleep_event_time)
        sleep_provenance = {
            "source_id": str(_first_value(sleep_raw, "id", "event_id") or "") or None,
            "source_provider": _source_provider(sleep_raw),
            "observed_at": observed_at,
            "source_record_kind": "event",
            "metric_domain": "sleep",
        }
        sleep_observed_at = sleep_provenance["observed_at"]
        added_sleep_fact = False
        duration_min = _finite_fact_value(sleep.get("duration_min"))
        if duration_min is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "sleep_duration", duration_min, "min", confidence="medium", freshness=_fact_freshness(sleep_observed_at, fetched_at), **sleep_provenance))
            added_sleep_fact = True
        avg_hr = _finite_fact_value(sleep.get("avg_hr"))
        if avg_hr is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "sleep_avg_heart_rate", avg_hr, "bpm", confidence="medium", freshness=_fact_freshness(sleep_observed_at, fetched_at), **sleep_provenance))
            added_sleep_fact = True
        for stage, value in (sleep.get("stages_min") or {}).items():
            stage_value = _finite_fact_value(value)
            if stage_value is not None and stage in {"deep", "rem", "light", "awake"}:
                facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", f"sleep_{stage}_duration", stage_value, "min", confidence="medium", freshness=_fact_freshness(sleep_observed_at, fetched_at), **sleep_provenance))
                added_sleep_fact = True
        efficiency_percent = _finite_fact_value(sleep.get("efficiency_percent"))
        if efficiency_percent is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "sleep_efficiency", efficiency_percent, "%", confidence="medium", freshness=_fact_freshness(sleep_observed_at, fetched_at), **sleep_provenance))
            added_sleep_fact = True
        if sleep.get("is_nap") is not None:
            facts.append(WearableDailyFact(date_s, "open_wearables", "Open Wearables", "sleep_is_nap", sleep.get("is_nap"), "boolean", confidence="medium", freshness=_fact_freshness(sleep_observed_at, fetched_at), **sleep_provenance))
            added_sleep_fact = True
        if added_sleep_fact:
            mark_replacement_sources(sleep.get("raw"), date_s, "sleep")

    sleep_summary_mappings = {
        "sleep_duration": (("duration_minutes",), "min"),
        "sleep_time_in_bed": (("time_in_bed_minutes",), "min"),
        "sleep_efficiency": (("efficiency_percent",), "%"),
        "sleep_interruptions": (("interruptions_count",), "count"),
        "sleep_nap_count": (("nap_count",), "count"),
        "sleep_nap_duration": (("nap_duration_minutes",), "min"),
        "sleep_avg_heart_rate": (("avg_heart_rate_bpm",), "bpm"),
        "sleep_hrv_sdnn": (("avg_hrv_sdnn_ms",), "ms"),
        "sleep_hrv_rmssd": (("avg_hrv_rmssd_ms",), "ms"),
        "sleep_respiratory_rate": (("avg_respiratory_rate",), "breaths/min"),
        "sleep_blood_oxygen": (("avg_spo2_percent",), "%"),
    }

    def valid_optional_sleep_number(value):
        return (
            value is None
            or (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
                and float(value) >= 0
            )
        )

    for row in sleep_summary_rows or []:
        date_s = _row_date(row)
        provider = _source_provider(row)
        if date_s is None:
            sleep_summary_snapshot_replacement_safe = False
            continue
        row_date = _temporal_value(date_s)
        if (
            row_date is None
            or sleep_summary_window_start is None
            or sleep_summary_window_end is None
            or not (
                sleep_summary_window_start.date()
                <= row_date.date()
                < sleep_summary_window_end.date()
            )
        ):
            sleep_summary_snapshot_replacement_safe = False
        if provider is None:
            sleep_summary_snapshot_replacement_safe = False
        invalid_sleep_keys = {
            key
            for aliases, _unit in sleep_summary_mappings.values()
            for key in aliases
            if key in row and not valid_optional_sleep_number(row[key])
        }
        if invalid_sleep_keys:
            sleep_summary_snapshot_replacement_safe = False
        raw_stages = row.get("stages")
        invalid_stage_keys = set()
        if raw_stages is not None and not isinstance(raw_stages, dict):
            sleep_summary_snapshot_replacement_safe = False
        elif isinstance(raw_stages, dict):
            invalid_stage_keys = {
                key
                for key in ("awake_minutes", "light_minutes", "deep_minutes", "rem_minutes")
                if key in raw_stages and not valid_optional_sleep_number(raw_stages[key])
            }
            if invalid_stage_keys:
                sleep_summary_snapshot_replacement_safe = False
        supplied_sleep_instants = [
            row[key]
            for key in ("start_time", "start", "end_time", "end")
            if row.get(key) is not None
        ]
        if any(_utc_temporal_value(value) is None for value in supplied_sleep_instants):
            sleep_summary_snapshot_replacement_safe = False
        raw_observed_at = _first_value(row, "end_time", "end")
        observed_at = (
            str(raw_observed_at)
            if raw_observed_at is not None and _utc_temporal_value(raw_observed_at) is not None
            else date_s if raw_observed_at is None else None
        )
        provenance = {
            "source_id": "sleep-summary",
            "source_provider": provider,
            "observed_at": observed_at,
            "source_record_kind": "summary",
            "metric_domain": "sleep",
        }
        before_count = len(facts)
        for metric, (aliases, unit) in sleep_summary_mappings.items():
            if any(key in invalid_sleep_keys for key in aliases):
                continue
            value = _number(row, *aliases)
            if metric == "sleep_duration" and value is not None and value <= 0:
                value = None
            if value is not None:
                facts.append(WearableDailyFact(
                    date_s, "open_wearables", "Open Wearables", metric, value, unit,
                    confidence="medium",
                    freshness=_fact_freshness(observed_at or date_s, fetched_at),
                    **provenance,
                ))
        stages = row.get("stages") if isinstance(row.get("stages"), dict) else {}
        for stage in ("awake", "light", "deep", "rem"):
            stage_key = f"{stage}_minutes"
            if stage_key in invalid_stage_keys:
                continue
            value = _number(stages, stage_key)
            if value is not None:
                facts.append(WearableDailyFact(
                    date_s, "open_wearables", "Open Wearables",
                    f"sleep_{stage}_duration", value, "min", confidence="medium",
                    freshness=_fact_freshness(observed_at or date_s, fetched_at),
                    **provenance,
                ))
        if len(facts) > before_count:
            mark_replacement_sources(row, date_s, "sleep")

    metric_groups = (
        ("recovery_summary", {
            "recovery_score": (("recovery_score", "score", "readiness_score"), "score"),
            "heart_rate_variability": (("avg_hrv_sdnn_ms", "hrv", "hrv_ms", "heart_rate_variability"), "ms"),
            "resting_heart_rate": (("resting_heart_rate_bpm", "resting_heart_rate", "resting_hr", "rhr"), "bpm"),
            "blood_oxygen": (("avg_spo2_percent", "spo2", "blood_oxygen", "oxygen_saturation"), "%"),
            "sleep_efficiency": (("sleep_efficiency_percent",), "%"),
            "respiratory_rate": (("respiratory_rate", "breathing_rate"), "breaths/min"),
            "skin_temperature": (("skin_temperature",), "c"),
            "temperature_deviation": (("temperature_deviation", "temperature_delta"), "c"),
        }),
    )
    for payload_key, mappings in metric_groups:
        for row in _payload_rows(data.get(payload_key)):
            date_s = _row_date(row)
            if date_s is None:
                continue
            provider = _source_provider(row)
            observed_at = _temporal_text(_first_value(row, "recorded_at", "timestamp"))
            recovery_provenance = {
                "source_id": str(_first_value(row, "id", "summary_id") or "") or None,
                "source_provider": provider,
                "observed_at": observed_at,
                "source_record_kind": "summary",
            }
            before_count = len(facts)
            for metric, (aliases, unit) in mappings.items():
                value = _number(row, *aliases)
                if value is not None:
                    facts.append(WearableDailyFact(
                        date_s, "open_wearables", "Open Wearables", metric, value, unit,
                        confidence="medium", freshness=_fact_freshness(date_s, fetched_at),
                        metric_domain=("recovery" if metric == "skin_temperature" else None),
                        **recovery_provenance,
                    ))
            if payload_key == "recovery_summary":
                sleep_seconds = _number(row, "sleep_duration_seconds")
                if sleep_seconds is not None:
                    facts.append(WearableDailyFact(
                        date_s, "open_wearables", "Open Wearables", "sleep_duration",
                        sleep_seconds / 60.0, "min", confidence="medium", freshness=_fact_freshness(date_s, fetched_at),
                        **recovery_provenance,
                    ))
            if len(facts) > before_count:
                mark_replacement_sources(row, date_s, "recovery")

    for body in body_rows:
        averaged = body.get("averaged") if isinstance(body.get("averaged"), dict) else {}
        date_s = _row_date(averaged if averaged else body)
        # The body summary is composite: its top-level source can describe only
        # one constituent measurement, not every averaged/latest field.
        provider = None
        body_groups = (
            (body.get("slow_changing") if isinstance(body.get("slow_changing"), dict) else body, True, {
                "weight": (("weight_kg", "weight"), "kg"),
                "body_fat_percent": (("body_fat_percent", "body_fat"), "%"),
                "muscle_mass": (("muscle_mass_kg", "muscle_mass"), "kg"),
                "body_mass_index": (("bmi", "body_mass_index"), "kg/m2"),
            }),
            (averaged, False, {
                "resting_heart_rate_average": (("resting_heart_rate_bpm",), "bpm"),
                "heart_rate_variability_sdnn_average": (("avg_hrv_sdnn_ms",), "ms"),
                "heart_rate_variability_rmssd_average": (("avg_hrv_rmssd_ms",), "ms"),
            }),
        )
        for values, undated, mappings in body_groups:
            observed_at = None if undated else _temporal_text(_first_value(
                values, "period_end", "end", "end_time", "timestamp", "date",
            ))
            for metric, (aliases, unit) in mappings.items():
                value = _number(values, *aliases)
                if value is not None:
                    fact_date = fetched_at[:10] if undated else date_s
                    if fact_date is None:
                        continue
                    facts.append(WearableDailyFact(
                        fact_date, "open_wearables", "Open Wearables", metric, value, unit,
                        confidence=("low" if undated else "medium"),
                        freshness=("unknown" if undated else _fact_freshness(observed_at or date_s, fetched_at)),
                        source_id=("undated-latest" if undated else None), source_provider=provider,
                        observed_at=observed_at,
                        source_record_kind="summary",
                        metric_domain=("body" if undated else "recovery"),
                    ))
        latest = body.get("latest") if isinstance(body.get("latest"), dict) else {}
        for metric, value_key, measured_at_key in (
            ("body_temperature", "body_temperature_celsius", "body_temperature_measured_at"),
            ("skin_temperature", "skin_temperature_celsius", "skin_temperature_measured_at"),
        ):
            value = _number(latest, value_key)
            if value is not None:
                raw_measured_at = latest.get(measured_at_key)
                measured_at = _temporal_text(raw_measured_at)
                if raw_measured_at is None or measured_at is None:
                    continue
                measured = _temporal_value(measured_at)
                if measured is None:
                    continue
                measured_date = measured.date().isoformat()
                facts.append(WearableDailyFact(
                    measured_date, "open_wearables", "Open Wearables", metric, value, "c",
                    confidence="medium", freshness=_fact_freshness(measured_at or measured_date, fetched_at),
                    source_provider=provider, observed_at=measured_at,
                    source_record_kind="summary",
                    metric_domain="body",
                ))

    workout_replacement_domain_dates = {}
    for row in workout_rows or []:
        workout_source_id = _first_value(row, "id", "event_id", "workout_id")
        if workout_source_id is None or not str(workout_source_id).strip():
            workout_snapshot_replacement_safe = False
            continue
        observed_at = _temporal_text(_first_value(row, "start", "start_time", "date"))
        if observed_at is None:
            workout_snapshot_replacement_safe = False
            continue
        zone_offset = row.get("zone_offset")
        if str(zone_offset or "").strip() and _workout_zone(zone_offset) is None:
            workout_snapshot_replacement_safe = False
            continue
        workout_observed_at = _workout_utc_value(observed_at, zone_offset)
        if workout_observed_at is None:
            workout_snapshot_replacement_safe = False
            continue
        workout_end_at = _temporal_text(_first_value(row, "end", "end_time"))
        workout_end_observed_at = (
            _workout_utc_value(workout_end_at, zone_offset)
            if workout_end_at is not None
            else None
        )
        if workout_window_start is None or workout_window_end is None:
            workout_snapshot_replacement_safe = False
        elif (
            not workout_window_start <= workout_observed_at < workout_window_end
            or workout_end_observed_at is None
            or not workout_observed_at < workout_end_observed_at < workout_window_end
        ):
            workout_snapshot_replacement_safe = False
            continue
        stored_observed_at = (
            workout_observed_at.isoformat().replace("+00:00", "Z")
            if workout_observed_at is not None
            else observed_at
        )
        date_s = _workout_local_date(observed_at, zone_offset)
        if date_s is None:
            workout_snapshot_replacement_safe = False
            continue
        before_count = len(facts)
        canonical_type = _first_value(row, "type", "activity_type", "workout_type", "sport")
        workout_provider = _source_provider(row)
        if canonical_type is None or not str(canonical_type).strip() or workout_end_at is None or workout_provider is None:
            workout_snapshot_replacement_safe = False
        original_label = _first_value(row, "name", "activity_type", "workout_type", "sport", "type")
        provenance = {
            "category": _workout_category(canonical_type),
            "source_id": str(workout_source_id).strip(),
            "source_provider": workout_provider,
            "original_label": str(original_label) if original_label is not None else None,
            "observed_at": stored_observed_at,
            "source_record_kind": "event",
        }
        workout_metrics = {
            "workout_duration": ((_first_value(row, "duration_min", "duration_minutes")), "min"),
            "workout_distance": ((_first_value(row, "distance_meters", "distance")), "m"),
            "workout_active_calories": ((_first_value(row, "calories_kcal", "active_calories", "calories")), "kcal"),
            "workout_load": ((_first_value(row, "load", "strain", "training_load")), "score"),
            "workout_avg_heart_rate": ((_first_value(row, "avg_heart_rate_bpm", "avg_hr", "average_heart_rate")), "bpm"),
            "workout_max_heart_rate": ((_first_value(row, "max_heart_rate_bpm", "max_hr", "max_heart_rate")), "bpm"),
        }
        if workout_metrics["workout_duration"][0] is None:
            raw_duration_seconds = _first_value(row, "duration_seconds")
            duration_seconds = _number(row, "duration_seconds")
            if raw_duration_seconds is not None and duration_seconds is None:
                workout_snapshot_replacement_safe = False
            if raw_duration_seconds is None and workout_end_at is not None:
                if workout_end_observed_at is None:
                    duration_seconds = None
                    workout_snapshot_replacement_safe = False
                else:
                    duration_seconds = (
                        workout_end_observed_at - workout_observed_at
                    ).total_seconds()
                if duration_seconds is not None and (
                    not math.isfinite(duration_seconds) or duration_seconds <= 0
                ):
                    duration_seconds = None
                    workout_snapshot_replacement_safe = False
            workout_metrics["workout_duration"] = (
                duration_seconds / 60.0 if duration_seconds is not None else None,
                "min",
            )
        for metric, (raw_value, unit) in workout_metrics.items():
            try:
                value = float(raw_value) if raw_value is not None else None
            except (TypeError, ValueError):
                value = None
                workout_snapshot_replacement_safe = False
            if value is not None and not math.isfinite(value):
                value = None
                workout_snapshot_replacement_safe = False
            if metric == "workout_duration" and value is not None and value <= 0:
                value = None
                workout_snapshot_replacement_safe = False
            if metric == "workout_distance" and value is not None and value < 0:
                value = None
                workout_snapshot_replacement_safe = False
            if value is not None and math.isfinite(value):
                facts.append(WearableDailyFact(
                    date_s, "open_wearables", "Open Wearables", metric, value, unit,
                    confidence="medium", freshness=_fact_freshness(stored_observed_at, fetched_at), **provenance,
                ))
        if len(facts) > before_count:
            for source_key in row_replacement_sources(row):
                if date_s > workout_replacement_domain_dates.get(source_key, ""):
                    workout_replacement_domain_dates[source_key] = date_s
        else:
            workout_snapshot_replacement_safe = False

    if workout_snapshot_replacement_safe:
        for source_key, date_s in workout_replacement_domain_dates.items():
            domain_dates = replacement_source_domain_dates.setdefault(source_key, {})
            if date_s > domain_dates.get("workouts", ""):
                domain_dates["workouts"] = date_s

    facts = [
        fact for fact in facts
        if not isinstance(fact.value, float) or math.isfinite(fact.value)
    ]
    facts = [
        replace(
            fact,
            provider_id=fact.source_provider or "open_wearables",
            source_label=_provider_display_name(fact.source_provider),
            source_system="open_wearables",
            source_record_kind=fact.source_record_kind or "summary",
            metric_domain=fact.metric_domain or _fact_metric_domain(fact),
            capability_state="available",
            source_last_synced_at=fetched_at,
        )
        for fact in facts
    ]
    if (
        facts
        or workout_snapshot_replacement_safe
        or body_snapshot_replacement_safe
        or sleep_summary_snapshot_replacement_safe
    ):
        facts = [
            replace(fact, used_for_recommendation=fact.freshness in {"fresh", "aging"})
            for fact in facts
        ]
        upsert_daily_facts(
            db_file,
            facts,
            profile_key=profile_key,
            replace_fact_scopes=(
                {
                    ("open_wearables", "open_wearables", "weight", "body"),
                    ("open_wearables", "open_wearables", "body_fat_percent", "body"),
                    ("open_wearables", "open_wearables", "muscle_mass", "body"),
                    ("open_wearables", "open_wearables", "body_mass_index", "body"),
                    ("open_wearables", "open_wearables", "resting_heart_rate_average", "recovery"),
                    ("open_wearables", "open_wearables", "heart_rate_variability_sdnn_average", "recovery"),
                    ("open_wearables", "open_wearables", "heart_rate_variability_rmssd_average", "recovery"),
                    ("open_wearables", "open_wearables", "body_temperature", "body"),
                    ("open_wearables", "open_wearables", "skin_temperature", "body"),
                }
                if body_snapshot_replacement_safe
                else None
            ),
            replace_source_id_date_windows=(
                {(
                    "open_wearables",
                    "sleep-summary",
                    sleep_summary_query_start,
                    sleep_summary_query_end,
                )}
                if sleep_summary_snapshot_replacement_safe
                else None
            ),
            replace_provider_metric_observation_windows=(
                {(
                    "open_wearables",
                    "workout_",
                    workout_query_start,
                    workout_query_end,
                )}
                if workout_snapshot_replacement_safe
                else None
            ),
        )
    persisted_usable = list_recommendation_facts(
        db_file,
        limit=100,
        profile_key=profile_key,
        source_system="open_wearables",
        usable_only=True,
    )
    # FIT-350 is the slice-one normalized-fact expansion. Provider-specific
    # parity and retirement gates are separate follow-up issues, so domain
    # observations are diagnostic only and must not hide a direct fallback.
    replacement_source_dates = {}
    if errors and not facts:
        source_status = "error"
    elif any(fact["freshness"] == "fresh" for fact in persisted_usable):
        source_status = "fresh"
    elif persisted_usable:
        source_status = "aging"
    else:
        source_status = "stale"
    trusted_dates = list(replacement_source_dates.values())
    trusted_dates.extend(fact.date for fact in facts if fact.freshness != "unknown")
    trusted_dates.extend(fact["date"] for fact in persisted_usable)
    last_data_point = max(trusted_dates, default=fetched_at[:10])
    upsert_wearable_source(db_file, {
        "provider_id": "open_wearables",
        "label": "Open Wearables",
        "status": source_status,
        "last_data_point": last_data_point,
        "last_sync_attempt": fetched_at,
        "capabilities": {
            "metrics": True,
            "workouts": True,
            "history": True,
            "sync": True,
            "replacement_sources": sorted(replacement_source_dates),
            "replacement_source_dates": replacement_source_dates,
            "replacement_source_domain_dates": replacement_source_domain_dates,
        },
        "used_for_recommendation": bool(persisted_usable),
    }, profile_key=profile_key)
    return len({(fact.date, fact.provider_id, fact.metric, fact.source_id or "") for fact in facts})


def recommendation_facts(db_file: str, profile_key: str, limit: int = 20) -> list[dict]:
    query_args = {
        "profile_key": profile_key,
        "source_system": "open_wearables",
        "usable_only": True,
        "latest_per_metric": True,
    }
    display_facts = list_recommendation_facts(
        db_file,
        limit=limit,
        **query_args,
    )
    guard_facts = list_recommendation_facts(
        db_file,
        limit=2,
        metric_names={"sleep_duration", "active_minutes"},
        **query_args,
    )
    seen = {
        (fact["metric"], fact["provider_id"], fact["source_system"])
        for fact in display_facts
    }
    return display_facts + [
        fact for fact in guard_facts
        if (fact["metric"], fact["provider_id"], fact["source_system"]) not in seen
    ]


def conservative_modifier(facts) -> dict:
    facts = facts or []
    applied = []
    details = []
    sleep_values = []
    active_values = []
    for fact in facts:
        try:
            value = float(fact.get("value")) if fact.get("value") is not None else None
        except (TypeError, ValueError):
            continue
        if fact.get("metric") == "sleep_duration" and value is not None:
            sleep_values.append(value)
        elif fact.get("metric") == "active_minutes" and value is not None:
            active_values.append(value)
    sleep_min = min(sleep_values, default=None)
    if sleep_min is not None and sleep_min < 360:
        applied.append("sleep_caution")
        details.append(f"Open Wearables sleep duration {int(round(sleep_min))} min")

    active_min = max(active_values, default=None)
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
