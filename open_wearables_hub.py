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
import hashlib
import json
import math
from typing import Callable

from wearable_fact_store import (
    WearableDailyFact,
    list_recommendation_facts,
    upsert_daily_facts,
    upsert_wearable_source,
)


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


def _workout_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "events", "records", "items", "workouts"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _workout_value(row: dict, *keys):
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _workout_number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else round(number, 2)


def _workout_local_date(row: dict, start_time) -> str:
    explicit_date = _workout_value(row, "date", "day")
    if explicit_date is not None:
        return str(explicit_date)[:10]

    raw_start = str(start_time or "")
    try:
        parsed = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
    except ValueError:
        return raw_start[:10]

    offset = row.get("zone_offset")
    if isinstance(offset, str) and len(offset) == 6 and offset[0] in "+-" and offset[3] == ":":
        try:
            hours = int(offset[1:3])
            minutes = int(offset[4:6])
        except ValueError:
            hours = minutes = -1
        if 0 <= hours <= 23 and 0 <= minutes <= 59:
            delta = timedelta(hours=hours, minutes=minutes)
            if offset[0] == "-":
                delta = -delta
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            parsed += delta
    return parsed.date().isoformat()


def extract_workouts(payload) -> list[dict]:
    """Project Open Wearables events into safe per-workout history rows.

    Only the public fields needed by History and AI are copied. Provider raw
    payloads, samples, user identifiers, and credentials never cross this
    boundary.
    """
    workouts = []
    for row in _workout_rows(payload):
        start_time = _workout_value(
            row,
            "start_time",
            "start_datetime",
            "start",
            "start_date",
            "startDate",
        )
        date_s = _workout_local_date(row, start_time)
        if len(date_s) != 10 or date_s[4:5] != "-" or date_s[7:8] != "-":
            continue

        session_type = str(_workout_value(
            row,
            "type",
            "activity_type",
            "activity",
            "sport_type",
        ) or "workout")
        activity_type = str(_workout_value(
            row,
            "name",
            "title",
            "activity_type",
            "activity",
            "sport_type",
            "type",
        ) or "Workout")

        duration_seconds = _workout_number(_workout_value(row, "duration_seconds", "elapsed_time"))
        duration_minutes = _workout_number(_workout_value(row, "duration_minutes", "duration_min"))
        if duration_minutes is None and duration_seconds is not None:
            duration_minutes = _workout_number(duration_seconds / 60)

        calories = _workout_number(_workout_value(
            row,
            "calories_kcal",
            "calories_burned",
            "calories",
            "energy_kcal",
            "total_energy_kcal",
            "energy_burned",
        ))
        if calories is None:
            kilojoules = _workout_number(_workout_value(row, "kilojoules", "kilojoule"))
            if kilojoules is not None:
                calories = _workout_number(kilojoules * 0.239)

        external_id = _workout_value(row, "id", "external_id", "workout_id", "event_id")
        if external_id is None:
            identity = json.dumps(
                {
                    "date": date_s,
                    "start_time": start_time,
                    "type": session_type,
                    "duration_minutes": duration_minutes,
                },
                sort_keys=True,
                default=str,
            )
            external_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        external_id = str(external_id)

        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        provider_source = source.get("provider") or _workout_value(row, "provider", "provider_id")
        device = source.get("device") or row.get("device")
        notes = row.get("notes") if isinstance(row.get("notes"), str) else None
        end_time = _workout_value(
            row,
            "end_time",
            "end_datetime",
            "end",
            "end_date",
            "endDate",
        )

        workouts.append({
            "id": f"open_wearables:{external_id}",
            "external_id": external_id,
            "source": "open_wearables",
            "provider": "Open Wearables",
            "provider_source": str(provider_source) if provider_source else None,
            "device": str(device) if device else None,
            "date": date_s,
            "start_time": str(start_time) if start_time is not None else None,
            "end_time": str(end_time) if end_time is not None else None,
            "activity_type": activity_type,
            "session_type": session_type,
            "duration_minutes": duration_minutes,
            "calories_burned": calories,
            "avg_heart_rate": _workout_number(_workout_value(
                row,
                "avg_heart_rate_bpm",
                "avg_heart_rate",
                "average_heart_rate",
                "avg_hr",
                "heart_rate_avg",
            )),
            "max_heart_rate": _workout_number(_workout_value(
                row,
                "max_heart_rate_bpm",
                "max_heart_rate",
                "max_hr",
                "heart_rate_max",
            )),
            "notes": notes,
        })
    return workouts


def sync_error_code(key):
    stable_codes = {
        "auth": "open_wearables_auth_error",
        "config": "open_wearables_config_error",
        "sleep": "open_wearables_sync_error",
        "workouts": "open_wearables_sync_error",
        "activity_summary": "open_wearables_sync_error",
    }
    return stable_codes.get(str(key), "open_wearables_sync_error")


def public_error_key(key):
    public_names = {"auth", "config", "sleep", "workouts", "activity_summary"}
    name = str(key)
    return name if name in public_names else "sync"


def sync_metadata(data, *, now: Callable[[], datetime] = datetime.now) -> dict:
    data = data if isinstance(data, dict) else {}
    counts = {}
    for key in ("sleep", "workouts", "activity_summary"):
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
        if added_sleep_fact:
            mark_replacement_sources(sleep.get("raw"), date_s)

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
