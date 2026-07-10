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

from recommendation_sources import build_open_wearables_recommendation_source
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


def recommendation_source_payload(freshness, *, db_file: str, profile_key: str, facts=None, modifier=None) -> dict:
    facts = recommendation_facts(db_file, profile_key) if facts is None else facts
    modifier = conservative_modifier(facts) if modifier is None else modifier
    source_freshness = freshness
    if isinstance(freshness, dict) and "open_wearables" in freshness:
        source_freshness = freshness.get("open_wearables")
    return build_open_wearables_recommendation_source(
        source_freshness,
        facts=facts,
        modifier=modifier,
    )
