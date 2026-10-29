"""Workout recommendation cache fingerprint payloads.

Extracted from app.py's ``_workout_recommendation_fingerprint`` (FIT-250).
The orchestrator in app.py gathers the live inputs (workouts, settings,
wearable freshness, etc.) and calls ``build_fingerprint`` here to assemble
the deterministic payload and hash it. Keeping the assembly pure (no Flask,
no module-level state) makes it independently testable.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Iterable


SETTINGS_FIELDS = [
    "training_goal",
    "date_of_birth",
    "sex",
    "sessions_per_week_target",
    "available_time_minutes",
    "fatigue_threshold",
    "equipment_preference",
    "preferred_equipment_brands",
    "excluded_exercises",
    "volume_landmarks",
]


def file_marker(path: str) -> dict:
    try:
        stat = os.stat(path)
        return {"path": path, "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
    except OSError:
        return {"path": path, "missing": True}


def latest_marker(rows: Iterable[dict] | None) -> str:
    markers = [
        str((row or {}).get("created_at") or (row or {}).get("date") or "")
        for row in (rows or [])
    ]
    return max(markers) if markers else ""


def build_payload(
    *,
    auth_scope: str,
    day: str,
    workouts: list[dict] | None,
    soreness_data: list[dict] | None,
    cardio_data: list[dict] | None,
    recovery_data: list[dict] | None,
    user_settings: dict,
    weather_cache: dict,
    open_wearables_configured: bool,
    open_wearables_user_id,
    open_wearables_marker: dict,
    apple_health_enabled: bool,
    apple_health_hr_intensity_enabled: bool,
    apple_health_freshness: tuple,
    apple_health_sync_db_file: str,
    apple_health_workout_files: list[str],
    wearable_facts: list[dict] | None,
    readiness: int | None = None,
) -> dict:
    apple_status, apple_last_data, apple_last_sync = apple_health_freshness
    return {
        "auth_scope": auth_scope,
        "day": day,
        "workouts_count": len(workouts or []),
        "workouts_latest": latest_marker(workouts),
        "soreness_count": len(soreness_data or []),
        "soreness_latest": latest_marker(soreness_data),
        "cardio_count": len(cardio_data or []),
        "cardio_latest": latest_marker(cardio_data),
        "recovery_count": len(recovery_data or []),
        "recovery_latest": latest_marker(recovery_data),
        "settings": {field: user_settings.get(field) for field in SETTINGS_FIELDS},
        "weather": {
            "ts": weather_cache.get("ts"),
            "location": weather_cache.get("location"),
            "data": weather_cache.get("data"),
            "error": weather_cache.get("error"),
        },
        "open_wearables": {
            "configured": open_wearables_configured,
            "user_id": open_wearables_user_id,
            "marker": open_wearables_marker,
        },
        "apple_health": {
            "enabled": apple_health_enabled,
            "hr_intensity_enabled": apple_health_hr_intensity_enabled,
            "freshness": {
                "status": apple_status,
                "last_data": apple_last_data,
                "last_sync": apple_last_sync,
            },
            "sync_db": file_marker(apple_health_sync_db_file),
            "workout_files": [file_marker(path) for path in apple_health_workout_files[-3:]],
        },
        # Preferred sleep/recovery/activity facts: a new night or a changed value re-plans.
        "readiness": readiness,
        "wearable_facts": [
            {key: fact.get(key) for key in ("date", "metric", "provider", "value", "freshness", "conflict_state")}
            for fact in (wearable_facts or [])
        ],
    }


def fingerprint_payload(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_fingerprint(**payload_kwargs) -> str:
    return fingerprint_payload(build_payload(**payload_kwargs))
