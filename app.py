#!/usr/bin/env python3
"""
Fitness Intelligence System - Mobile Web App
Evidence-based resistance training optimization for iOS/Android.
"""

from flask import Flask, has_request_context, render_template, jsonify, request, Response, redirect
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from contextlib import closing
import copy
import csv
import io
import json
import logging
import os
import socket
import sqlite3
import hashlib
import glob
import html
import math
import urllib.request
import urllib.error
import urllib.parse
import base64
import time
import re
import secrets
import uuid
import tempfile
import threading
import fcntl
import shutil
from subprocess import SubprocessError

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - exercised only when optional dependency is absent.
    WebPushException = None
    webpush = None

# Load .env file if present (for OURA_API_TOKEN etc.)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
from data_loader import parse_workout_log, get_workout_summary
from runtime_config import DATA_DIR, data_path, public_base_url as _runtime_public_base_url
from oura_client import (
    OuraClient,
    init_oura_db,
    upsert_oura_daily,
    get_oura_daily,
    get_oura_daily_range,
    compute_hrv_trend,
)
from whoop_client import (
    WhoopApiError,
    WhoopClient,
    create_whoop_authorization_url,
    exchange_whoop_code,
    load_whoop_config,
    redact_whoop_error,
    revoke_whoop_access,
)
from whoop_recommendations import (
    apply_wearable_modifiers,
    build_whoop_recommendation_signals,
    detect_wearable_source_conflicts,
)
from whoop_store import (
    clear_whoop_data,
    consume_oauth_state,
    create_oauth_state,
    delete_protected_secret,
    disconnect_whoop,
    get_connection_status as get_whoop_connection_status,
    get_daily_fact as get_whoop_daily_fact,
    init_whoop_db,
    invalidate_oauth_states,
    latest_whoop_fact_source_reason,
    latest_whoop_freshness,
    list_whoop_daily_facts,
    list_whoop_sync_runs,
    load_protected_secret,
    load_connection_token_material,
    finish_whoop_sync_run,
    mark_reauth_required,
    rotate_connection_tokens,
    save_protected_secret,
    record_whoop_sync_run,
    save_connection_tokens,
    upsert_whoop_records,
    project_whoop_daily_facts,
)
from ai_fact_query import answer_fact_question, build_ai_fact_context, create_pending_suggestion
from history_normalization import canonical_training_category, normalize_history_item
from open_wearables_adapter import (
    build_open_wearables_status,
    providers_from_payload,
    redact_open_wearables_error,
    redacted_base_url,
    validate_open_wearables_base_url,
)
from recommendation_sources import build_open_wearables_recommendation_source
from wearable_fact_store import (
    WearableDailyFact,
    delete_provider_data,
    list_recommendation_facts,
    list_wearable_sources,
    upsert_daily_facts,
    upsert_wearable_source,
)
from data_store import (
    init_data_db,
    add_food_log,
    get_food_logs,
    sanitize_accepted_estimate,
    get_meal_acceptance_event,
    get_meal_review_snapshot,
    list_meal_review_snapshots,
    import_meal_acceptance_event,
    import_meal_review_snapshot,
    backfill_food_log_client_id,
    claim_food_log_vocab_learning,
    delete_meal_acceptance_event,
    delete_meal_review_snapshot,
    delete_personal_vocab_entry,
    import_personal_vocab_entry,
    list_meal_acceptance_events,
    list_personal_vocab_entries,
    delete_food_log_by_client_id,
    delete_food_logs_by_meal_id,
    acknowledge_food_log_refresh_event,
    acknowledge_workout_adaptation_event,
    delete_current_workout_plan,
    get_current_workout_plan,
    get_push_subscription_for_delivery,
    list_push_subscriptions,
    list_food_log_refresh_events,
    list_workout_adaptation_events,
    revoke_push_subscription,
    save_push_subscription,
    save_current_workout_plan,
    save_meal_acceptance_event,
    save_meal_review_snapshot,
)
from meal_estimate_schema import (
    MealEstimateValidationError,
    manual_review_estimate,
    sanitize_meal_estimate,
)
import branded_food_lookup
import personal_vocab
import vision_estimator
import workout_adaptation
import open_wearables_hub
import workout_recommendation_fingerprint
from meal_text_parser import FALLBACK_REASON_VALUES, parse_meal_text
from meal_log_policy import (
    CALORIE_MAX,
    CORRECTION_STATE_ACCEPTED,
    CORRECTION_STATE_PENDING_REVIEW,
    MACRO_GRAM_MAX,
    MEDIUM_CONFIDENCE_THRESHOLD,
    SODIUM_MG_MAX,
    STATUS_LOGGED,
    STATUS_PENDING_REVIEW,
    evaluate_meal_log,
)

app = Flask(__name__)

# ── Auth (must be after app creation) ──────────────────────────
from auth import CSRF_HEADER_NAME, CSRF_HEADER_VALUE, data_user_id_for, init_auth
init_auth(app)

# ── Health route registration (Apple Health + HealthKit ingest) ──
try:
    from health_ingest import register_health_routes
    register_health_routes(app)
except Exception as _e:
    print(f"WARN: health_ingest routes not registered: {_e}")

try:
    from apple_health_parser import register_apple_health_routes
    register_apple_health_routes(app)
except Exception as _e:
    print(f"WARN: apple_health_parser routes not registered: {_e}")

# ── AI coach layer (LM Studio adapter) ──
try:
    import lm_studio_adapter as _lm_studio
except Exception as _e:
    print(f"WARN: lm_studio_adapter not loaded: {_e}")
    _lm_studio = None


# ==================== DATA PERSISTENCE ====================
# Store runtime data under DATA_DIR when configured, otherwise next to the app.
JSON_DATA_LOCK = threading.RLock()
WHOOP_SYNC_LOCK = threading.Lock()
WHOOP_SYNC_LOCK_FILE = data_path("whoop_sync.lock")
WORKOUTS_FILE = data_path("data_workouts.json")
SORENESS_FILE = data_path("data_soreness.json")
SETTINGS_FILE = data_path("data_settings.json")
CARDIO_FILE = data_path("data_cardio.json")
RECOVERY_FILE = data_path("data_recovery.json")
BASELINES_FILE = data_path("data_baselines.json")
BODY_FILE = data_path("data_body.json")
SLEEP_FILE = data_path("data_sleep.json")
NUTRITION_FILE = data_path("data_nutrition.json")
OURA_DB_FILE = data_path("oura_daily.sqlite3")
WHOOP_DB_FILE = data_path("whoop.sqlite3")
WEARABLE_FACTS_DB_FILE = data_path("wearable_facts.sqlite3")
OPEN_WEARABLES_CONFIG_FILE = data_path("open_wearables_config.json")
WHOOP_CSV_MAX_BYTES = 512 * 1024
WHOOP_CSV_MAX_ROWS = 5000
AI_PENDING_SUGGESTIONS = {}
_VISION_KEEP_WARM_THREAD = None

# ==================== WEATHER (wttr.in) ====================
# Lightweight cache to avoid hammering the free endpoint.
_WEATHER_CACHE = {
    "ts": 0,
    "location": "San_Antonio",
    "data": None,
    "error": None,
}


def load_json(filepath, default):
    """Load data from JSON file.

    If the file is missing: return default.
    If the file is malformed: move it aside (".corrupt-<ts>.json") and recreate with default.

    This prevents a single bad write from bricking the whole app.
    """
    if not os.path.exists(filepath):
        return default

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        try:
            ts = datetime.now().strftime('%Y%m%d-%H%M%S')
            corrupt_path = f"{filepath}.corrupt-{ts}.json"
            os.replace(filepath, corrupt_path)
            print(f"Warning: {filepath} was malformed JSON; moved to {corrupt_path} ({e})")
            save_json(filepath, default)
        except Exception as e2:
            print(f"Warning: Could not recover malformed JSON file {filepath}: {e2}")
        return default
    except IOError as e:
        print(f"Warning: Could not load {filepath}: {e}")
        return default


def save_json(filepath, data):
    """Save data to JSON file (atomic best-effort)."""
    tmp = None
    try:
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        base = os.path.basename(filepath)
        with JSON_DATA_LOCK:
            fd, tmp = tempfile.mkstemp(
                prefix=f".{base}.",
                suffix=".tmp",
                dir=os.path.dirname(os.path.abspath(filepath)),
            )
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, filepath)
            tmp = None
    except OSError as e:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        print(f"Warning: Could not save {filepath}: {e}")


def public_base_url():
    return _runtime_public_base_url(request if has_request_context() else None)


def _json_restore_sequence(target, value):
    target.clear()
    if isinstance(target, dict):
        target.update(value)
    else:
        target.extend(value)


def _notify_workout_logged(workout_entry):
    """Fire-and-forget: run workout analysis and write to pending file for OpenClaw pickup."""
    import threading, subprocess
    def _do():
        try:
            result = subprocess.run(
                ['python3', os.path.expanduser('~/.openclaw/workspace/scripts/workout-analysis.py')],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                analysis_path = os.path.expanduser('~/.openclaw/workspace/.pending-workout-analysis.txt')
                with open(analysis_path, 'w') as f:
                    f.write(result.stdout.strip())
                print(f"[fitness] Analysis written to {analysis_path} — cron will pick it up")
        except Exception as e:
            print(f"[fitness] Workout notification failed (non-fatal): {e}")
    threading.Thread(target=_do, daemon=True).start()


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _vision_keep_warm_interval_seconds() -> float:
    raw = os.environ.get("VISION_LM_STUDIO_KEEP_WARM_INTERVAL_SEC", "900")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 900.0


def _run_vision_keep_warm_loop(*, interval_seconds: float | None = None) -> None:
    interval = _vision_keep_warm_interval_seconds() if interval_seconds is None else interval_seconds
    while True:
        try:
            result = vision_estimator.local_vision_adapter.warm_all_candidates()
            print(f"[fitness] Vision keep-warm finished: {result.get('status')}")
        except Exception as exc:
            print(f"[fitness] Vision keep-warm failed (non-fatal): {exc}")
        if interval <= 0:
            return
        time.sleep(interval)


def _start_vision_keep_warm_daemon():
    """Opt-in LM Studio vision warmup loop for each app worker process."""
    global _VISION_KEEP_WARM_THREAD
    if vision_estimator.configured_provider() != "lm_studio":
        return None
    if not _env_flag_enabled("VISION_LM_STUDIO_KEEP_WARM"):
        return None
    thread_is_alive = getattr(_VISION_KEEP_WARM_THREAD, "is_alive", lambda: False)
    if _VISION_KEEP_WARM_THREAD is not None and thread_is_alive():
        return _VISION_KEEP_WARM_THREAD

    def _do():
        _run_vision_keep_warm_loop()

    thread = threading.Thread(target=_do, name="vision-keep-warm", daemon=True)
    thread.start()
    _VISION_KEEP_WARM_THREAD = thread
    return thread


_start_vision_keep_warm_daemon()


# ==================== TRAINING GOAL CONFIGURATION ====================

class TrainingGoal(Enum):
    STRENGTH = "strength"
    HYPERTROPHY = "hypertrophy"
    ENDURANCE = "endurance"
    WEIGHT_LOSS = "weight_loss"
    TONING = "toning"
    HYBRID_STRENGTH_HYPERTROPHY = "strength_hypertrophy"
    HYBRID_HYPERTROPHY_ENDURANCE = "hypertrophy_endurance"
    HYBRID_WEIGHT_LOSS_TONING = "weight_loss_toning"

# Goal-specific parameters
GOAL_PARAMETERS = {
    TrainingGoal.STRENGTH.value: {
        "name": "Strength",
        "description": "Maximize 1RM, heavy loads, low reps",
        "rep_range": (1, 5),
        "sets_per_exercise": 5,
        "rest_minutes": "3-5",
        "rpe_target": 8.5,
        "intensity_pct": 85,  # % of 1RM
        "volume_multiplier": 0.8,
        "time_per_set_minutes": 5  # Long rest between heavy sets
    },
    TrainingGoal.HYPERTROPHY.value: {
        "name": "Hypertrophy",
        "description": "Muscle growth, moderate loads, higher volume",
        "rep_range": (8, 12),
        "sets_per_exercise": 4,
        "rest_minutes": "1.5-2",
        "rpe_target": 7.5,
        "intensity_pct": 70,
        "volume_multiplier": 1.2,
        "time_per_set_minutes": 3
    },
    TrainingGoal.ENDURANCE.value: {
        "name": "Muscular Endurance",
        "description": "Stamina, light loads, high reps",
        "rep_range": (15, 25),
        "sets_per_exercise": 3,
        "rest_minutes": "0.5-1",
        "rpe_target": 6.5,
        "intensity_pct": 50,
        "volume_multiplier": 1.0,
        "time_per_set_minutes": 2
    },
    TrainingGoal.WEIGHT_LOSS.value: {
        "name": "Weight Loss",
        "description": "Calorie burn, circuit-style, minimal rest",
        "rep_range": (8, 12),
        "sets_per_exercise": 3,
        "rest_minutes": "1.5-3",
        "rpe_target": 7,
        "intensity_pct": 70,
        "volume_multiplier": 1.3,
        "time_per_set_minutes": 3
    },
    TrainingGoal.HYBRID_STRENGTH_HYPERTROPHY.value: {
        "name": "Strength + Hypertrophy",
        "description": "Build strength with muscle size",
        "rep_range": (5, 8),
        "sets_per_exercise": 4,
        "rest_minutes": "2-3",
        "rpe_target": 8,
        "intensity_pct": 78,
        "volume_multiplier": 1.0,
        "time_per_set_minutes": 4
    },
    TrainingGoal.HYBRID_HYPERTROPHY_ENDURANCE.value: {
        "name": "Hypertrophy + Endurance",
        "description": "Muscle size with conditioning",
        "rep_range": (10, 15),
        "sets_per_exercise": 4,
        "rest_minutes": "1-1.5",
        "rpe_target": 7,
        "intensity_pct": 65,
        "volume_multiplier": 1.1,
        "time_per_set_minutes": 2.5  # Per set including rest
    },
    TrainingGoal.TONING.value: {
        "name": "Toning",
        "description": "Define muscles, moderate weight, higher reps",
        "rep_range": (8, 15),
        "sets_per_exercise": 3,
        "rest_minutes": "1-2",
        "rpe_target": 6.5,
        "intensity_pct": 65,
        "volume_multiplier": 1.0,
        "time_per_set_minutes": 2
    },
    TrainingGoal.HYBRID_WEIGHT_LOSS_TONING.value: {
        "name": "Weight Loss + Toning",
        "description": "Burn fat while defining muscle",
        "rep_range": (8, 15),
        "sets_per_exercise": 3,
        "rest_minutes": "1-2",
        "rpe_target": 7,
        "intensity_pct": 65,
        "volume_multiplier": 1.2,
        "time_per_set_minutes": 2
    }
}

# Default user settings
DEFAULT_SETTINGS = {
    "training_goal": TrainingGoal.HYBRID_STRENGTH_HYPERTROPHY.value,
    "date_of_birth": "",
    "sex": "",
    "sessions_per_week_target": 3,
    "available_time_minutes": 75,
    "target_weight_lbs": 175,
    "target_body_fat_pct": 18,
    "daily_calorie_target": 2200,
    "daily_protein_target_g": 148,
    "fatigue_threshold": 72,
    "equipment_preference": "machines_only",
    "preferred_equipment_brands": ["Hoist", "Nautilus"],
    "excluded_exercises": ["Preacher Curl"],
    "volume_landmarks": {
        "default": {"mv": 6, "mev": 9, "mav_min": 12, "mav_max": 18, "mrv": 22}
    }
}


def _settings_with_defaults(settings):
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    merged.update(settings or {})
    return merged


def _coerce_profile_date_of_birth(value):
    if value is None:
        return ""
    cleaned = str(value).strip()
    if not cleaned:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return None
    try:
        parsed = datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        return None
    if parsed > datetime.now().date():
        return None
    return parsed.isoformat()


def _coerce_profile_sex(value):
    if value is None:
        return ""
    cleaned = str(value).strip().lower()
    allowed = {"", "female", "male", "nonbinary", "prefer_not_to_say"}
    return cleaned if cleaned in allowed else None


# Load user settings from file (or use defaults)
USER_SETTINGS = _settings_with_defaults(load_json(SETTINGS_FILE, DEFAULT_SETTINGS.copy()))

# Time options for workouts (in minutes)
TIME_OPTIONS = [
    {"value": 20, "label": "20 min", "description": "Quick session - 2 exercises"},
    {"value": 30, "label": "30 min", "description": "Short session - 3 exercises"},
    {"value": 45, "label": "45 min", "description": "Standard session - 4 exercises"},
    {"value": 60, "label": "60 min", "description": "Full session - 5-6 exercises"},
    {"value": 75, "label": "75 min", "description": "Extended session - 6-7 exercises"},
    {"value": 90, "label": "90 min", "description": "Long session - 7-8 exercises"},
]

# Track recommended vs actual workouts
WORKOUT_RECOMMENDATIONS = []  # Stores what was recommended
COMPLETED_WORKOUTS = []  # Stores what was actually done
LAST_WORKOUT_RECOMMENDATION = None  # Most recent recommendation for swap actions
LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None
LAST_WORKOUT_RECOMMENDATION_OWNER = None
CURRENT_WORKOUT_PLAN_LOCK = threading.RLock()
OPEN_WEARABLES_WORKOUT_MARKER_CACHE = {}

# ==================== CARDIO RECOMMENDATIONS ====================
# Heart rate zones based on % of max HR (220 - age, assume age 30 for now = 190 max HR)
# Zone 1: 50-60% - Recovery/Warmup (95-114 BPM)
# Zone 2: 60-70% - Fat Burning/Endurance Base (114-133 BPM)
# Zone 3: 70-80% - Aerobic/Cardio (133-152 BPM)
# Zone 4: 80-90% - Threshold/Performance (152-171 BPM)
# Zone 5: 90-100% - Maximum Effort (171-190 BPM)

CARDIO_RECOMMENDATIONS = {
    TrainingGoal.STRENGTH.value: {
        "include_cardio": False,
        "reason": "Cardio can interfere with maximal strength recovery. Skip or do light walking on off days.",
        "type": None,
        "duration_minutes": 0,
        "zone": None,
        "heart_rate_range": None
    },
    TrainingGoal.HYPERTROPHY.value: {
        "include_cardio": False,
        "reason": "Post-workout cardio can interfere with muscle protein synthesis. Consider low-intensity cardio on rest days.",
        "type": None,
        "duration_minutes": 0,
        "zone": None,
        "heart_rate_range": None
    },
    TrainingGoal.ENDURANCE.value: {
        "include_cardio": True,
        "reason": "Cardio complements muscular endurance training and improves overall conditioning.",
        "type": "Stairmaster",
        "duration_minutes": 20,
        "zone": "Zone 2-3",
        "zone_description": "Fat Burning to Aerobic",
        "heart_rate_range": "114-152 BPM",
        "intensity": "Steady state, conversational pace",
        "technique": "Full steps, engage glutes, maintain upright posture"
    },
    TrainingGoal.WEIGHT_LOSS.value: {
        "include_cardio": True,
        "reason": "Cardio after weights maximizes fat oxidation - glycogen depleted state enhances fat burning.",
        "type": "Stairmaster",
        "duration_minutes": 15,
        "zone": "Zone 2",
        "zone_description": "Fat Burning Zone",
        "heart_rate_range": "114-133 BPM",
        "intensity": "Steady, sustainable pace you can maintain",
        "technique": "Light grip on rails, step through heels, keep core engaged"
    },
    TrainingGoal.TONING.value: {
        "include_cardio": True,
        "reason": "Light cardio helps with calorie expenditure and muscle definition.",
        "type": "Stairmaster",
        "duration_minutes": 10,
        "zone": "Zone 2",
        "zone_description": "Fat Burning Zone",
        "heart_rate_range": "114-133 BPM",
        "intensity": "Easy, recovery pace",
        "technique": "Natural stride, minimal rail support"
    },
    TrainingGoal.HYBRID_STRENGTH_HYPERTROPHY.value: {
        "include_cardio": False,
        "reason": "Focus on recovery between sessions. Light walking on rest days is acceptable.",
        "type": None,
        "duration_minutes": 0,
        "zone": None,
        "heart_rate_range": None
    },
    TrainingGoal.HYBRID_HYPERTROPHY_ENDURANCE.value: {
        "include_cardio": True,
        "reason": "Concurrent training goal - moderate cardio supports endurance adaptation.",
        "type": "Stairmaster",
        "duration_minutes": 15,
        "zone": "Zone 2-3",
        "zone_description": "Fat Burning to Aerobic",
        "heart_rate_range": "114-152 BPM",
        "intensity": "Moderate effort, slightly elevated breathing",
        "technique": "Skip every other step for glute emphasis, or single steps for quads"
    },
    TrainingGoal.HYBRID_WEIGHT_LOSS_TONING.value: {
        "include_cardio": True,
        "reason": "Cardio is essential for creating calorie deficit while preserving muscle tone.",
        "type": "Stairmaster",
        "duration_minutes": 20,
        "zone": "Zone 2-3",
        "zone_description": "Fat Burning to Aerobic",
        "heart_rate_range": "114-152 BPM",
        "intensity": "Moderate steady state with intervals",
        "technique": "Alternate 2 min easy / 1 min faster pace"
    }
}

CARDIO_MODALITY_POOLS = {
    TrainingGoal.ENDURANCE.value: [
        "Outdoor run",
        "Treadmill run",
        "Bike",
        "Rower",
        "Stairmaster",
    ],
    TrainingGoal.WEIGHT_LOSS.value: [
        "Stairmaster",
        "Treadmill incline walk",
        "Bike",
        "Elliptical",
        "Rower",
    ],
    TrainingGoal.TONING.value: [
        "Treadmill incline walk",
        "Elliptical",
        "Bike",
        "Stairmaster",
    ],
    TrainingGoal.HYBRID_HYPERTROPHY_ENDURANCE.value: [
        "Stairmaster",
        "Bike",
        "Rower",
        "Treadmill incline walk",
        "Elliptical",
    ],
    TrainingGoal.HYBRID_WEIGHT_LOSS_TONING.value: [
        "Stairmaster",
        "Treadmill incline walk",
        "Bike",
        "Elliptical",
        "Rower",
    ],
}

CARDIO_RECOVERY_MODALITIES = [
    "Treadmill incline walk",
    "Bike",
    "Elliptical",
    "Outdoor walk",
]

OUTDOOR_CARDIO_MODALITIES = {"outdoor run", "outdoor walk"}

CARDIO_TECHNIQUE_BY_MODALITY = {
    "bike": "Smooth cadence, light grip, keep hips steady",
    "elliptical": "Tall posture, even foot pressure, steady handles",
    "outdoor run": "Relax shoulders, quick cadence, keep effort controlled",
    "outdoor walk": "Brisk pace, relaxed shoulders, consistent stride",
    "rower": "Legs-drive first, neutral spine, smooth recovery",
    "stairmaster": "Full steps, engage glutes, maintain upright posture",
    "treadmill incline walk": "Upright posture, steady stride, hands relaxed",
    "treadmill run": "Quick cadence, relaxed shoulders, controlled foot strike",
}

CARDIO_ROTATION_CURSOR = {}


def _normalize_cardio_type(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def _canonical_cardio_fatigue_type(value):
    activity = _normalize_cardio_type(value)
    aliases = {
        "bike": "cycling",
        "rower": "rowing",
        "treadmill incline walk": "treadmill",
        "treadmill run": "treadmill",
        "outdoor run": "running",
        "outdoor walk": "walking",
        "walk": "walking",
    }
    return aliases.get(activity, activity)


def _recent_cardio_types(cardio_data, limit=3):
    rows = sorted(cardio_data or [], key=lambda row: row.get("date", ""), reverse=True)
    recent = []
    for row in rows:
        cardio_type = row.get("activity_type") or row.get("type")
        if cardio_type:
            recent.append(str(cardio_type))
        if len(recent) >= limit:
            break
    return recent


def _next_cardio_rotation_index(goal, pool):
    if not pool:
        return 0
    current = CARDIO_ROTATION_CURSOR.get(goal, -1) + 1
    CARDIO_ROTATION_CURSOR[goal] = current % len(pool)
    return CARDIO_ROTATION_CURSOR[goal]


def _filter_cardio_pool_for_equipment(pool, equipment_preference):
    if equipment_preference == "all":
        return pool
    machine_safe = [
        option
        for option in pool
        if _normalize_cardio_type(option) not in OUTDOOR_CARDIO_MODALITIES
    ]
    return machine_safe or pool


def _choose_dynamic_cardio_recommendation(
    goal,
    base_rec,
    oura_readiness=None,
    training_recommendation=None,
    consume_rotation=True,
    equipment_preference=None,
):
    if not base_rec.get("include_cardio"):
        return base_rec

    pool = list(CARDIO_MODALITY_POOLS.get(goal) or [base_rec.get("type") or "Cardio"])
    pool = _filter_cardio_pool_for_equipment(pool, equipment_preference)
    recent_types = _recent_cardio_types(CARDIO_DATA)
    recent_normalized = [_normalize_cardio_type(t) for t in recent_types]
    repeated_recent = {
        recent_type
        for recent_type in set(recent_normalized)
        if recent_type and recent_normalized.count(recent_type) >= 2
    }
    if repeated_recent and len(pool) > 1:
        pool = [p for p in pool if _normalize_cardio_type(p) not in repeated_recent] or pool

    intensity_day = training_recommendation == "intensity"
    recovery_day = training_recommendation == "recovery" or (
        training_recommendation is None and oura_readiness is not None and oura_readiness < 70
    )

    selected_type = None
    if recovery_day:
        recovery_pool = _filter_cardio_pool_for_equipment(CARDIO_RECOVERY_MODALITIES, equipment_preference)
        for option in recovery_pool:
            if any(_normalize_cardio_type(option) == _normalize_cardio_type(p) for p in pool):
                selected_type = option
                break
        if selected_type is None:
            selected_type = pool[0]
    else:
        if consume_rotation:
            selected_type = pool[_next_cardio_rotation_index(goal, pool)]
        else:
            seed = f"{goal}:{_today_str()}:{len(WORKOUTS)}:{len(CARDIO_DATA)}"
            next_index = sum(ord(char) for char in seed) % len(pool)
            selected_type = pool[next_index]

    rec = dict(base_rec)
    rec["type"] = selected_type
    if recovery_day:
        rec.update({
            "duration_minutes": min(30, max(20, int(base_rec.get("duration_minutes") or 20))),
            "zone": "Zone 2",
            "zone_description": "Fat Burning / Recovery",
            "heart_rate_range": "114-133 BPM",
            "intensity": "Easy conversational pace; leave fresher than you started",
            "technique": "Smooth cadence, nasal-breathing pace, no hard intervals",
        })
    elif intensity_day:
        rec.update({
            "duration_minutes": min(20, max(12, int(base_rec.get("duration_minutes") or 15))),
            "zone": "Zone 4",
            "zone_description": "Threshold / Performance",
            "heart_rate_range": "152-171 BPM",
            "intensity": "Intervals: hard efforts with full easy recoveries",
            "technique": "Keep reps crisp; stop intervals if form or breathing falls apart",
        })
    else:
        rec.update({
            "zone": "Zone 3",
            "zone_description": "Aerobic",
            "heart_rate_range": "133-152 BPM",
            "intensity": "Steady state with controlled breathing",
            "technique": CARDIO_TECHNIQUE_BY_MODALITY.get(
                _normalize_cardio_type(selected_type),
                "Keep posture tall and effort controlled",
            ),
        })
    return rec

# ==================== EXERCISE LIBRARY ====================

EXERCISE_DISAMBIGUATION_METADATA_FIELDS = (
    "movement_pattern",
    "body_position",
    "shoulder_position",
    "primary_emphasis",
    "match_tokens",
    "confusable_with",
    "disambiguation",
)

EXERCISE_DISAMBIGUATION_DISTINGUISHING_FIELDS = (
    "movement_pattern",
    "body_position",
    "shoulder_position",
    "primary_emphasis",
    "confusable_with",
    "disambiguation",
)

EXERCISE_DISAMBIGUATION_CLUSTERS = {
    "triceps_extension": ("Overhead Tricep Extension",),
    "calf_raise": ("Calf Raise", "Calf Raise (Seated)"),
    "biceps_curl": ("Biceps Curl", "Cable Biceps Curl"),
    "crunch": ("Crunch Machine", "Cable Crunch"),
    "back_row": ("Seated Row", "Mid Row", "Cable Row", "Chest-Supported Row"),
    "chest_press": ("Chest Press", "Incline Press"),
    "shoulder_press": ("Shoulder Press", "Arnold Press"),
    "shoulder_delt_fly": ("Deltoid Fly", "Rear Delt Fly"),
}

EXERCISE_LIBRARY = [
    # Chest
    {"name": "Chest Press", "muscle": "chest", "compound": True, "baseline": 100, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "movement_patterns": ["horizontal_press"], "movement_pattern": "horizontal_press", "body_position": "seated", "shoulder_position": "neutral", "primary_emphasis": "chest_sternal", "match_tokens": ["flat", "horizontal"], "confusable_with": [{"name": "Incline Press", "distinction": "Horizontal machine press; the incline variant biases the upper chest."}], "disambiguation": "Default for a bare chest press: horizontal machine press, not the incline variant."},
    {"name": "Incline Press", "muscle": "chest", "compound": True, "baseline": 95, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "movement_patterns": ["incline_press", "horizontal_press"], "movement_pattern": "incline_press", "body_position": "incline", "shoulder_position": "incline", "primary_emphasis": "chest_upper_clavicular", "match_tokens": ["incline"], "confusable_with": [{"name": "Chest Press", "distinction": "Incline machine press; choose only when incline or upper-chest language is present."}], "disambiguation": "Pick when 'incline' or upper-chest press is requested."},
    {"name": "Cable Crossover", "muscle": "chest", "compound": False, "baseline": 40, "equipment": "cable", "movement_patterns": ["fly", "chest_isolation"]},
    {"name": "Pec Fly", "muscle": "chest", "compound": False, "baseline": 50, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "aliases": ["Pectoral Fly"], "movement_patterns": ["fly", "chest_isolation"]},
    {"name": "Dips", "muscle": "chest", "compound": True, "baseline": 50, "equipment": "bodyweight"},
    # Back
    {"name": "Lat Pulldown", "muscle": "back", "compound": True, "baseline": 100, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "movement_patterns": ["vertical_pull"]},
    {"name": "Seated Row", "muscle": "back", "compound": True, "baseline": 90, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "movement_patterns": ["horizontal_pull", "row"], "movement_pattern": "horizontal_pull", "body_position": "seated", "primary_emphasis": "mid_back_lats", "match_tokens": ["seated"], "confusable_with": [{"name": "Mid Row", "distinction": "Seated row machine; mid row uses a different machine geometry."}, {"name": "Cable Row", "distinction": "Seated row machine; cable row is a cable station row."}, {"name": "Chest-Supported Row", "distinction": "Seated row machine; chest-supported row fixes the torso against a pad."}], "disambiguation": "Default machine row when the user says seated row."},
    {"name": "Mid Row", "muscle": "back", "compound": True, "baseline": 80, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "movement_patterns": ["horizontal_pull", "row"], "movement_pattern": "horizontal_pull", "body_position": "seated", "primary_emphasis": "mid_back_lats", "match_tokens": ["mid"], "confusable_with": [{"name": "Seated Row", "distinction": "Mid-row machine geometry, distinct from the generic seated row."}, {"name": "Cable Row", "distinction": "Mid-row machine, not a cable row."}, {"name": "Chest-Supported Row", "distinction": "Mid-row machine; chest-supported row fixes the torso against a pad."}], "disambiguation": "Pick when 'mid row' is requested."},
    {"name": "Cable Row", "muscle": "back", "compound": False, "baseline": 70, "equipment": "cable", "movement_patterns": ["horizontal_pull", "row"], "movement_pattern": "horizontal_pull", "body_position": "seated", "primary_emphasis": "mid_back_lats", "match_tokens": ["cable"], "confusable_with": [{"name": "Seated Row", "distinction": "Cable-station row, not the machine row."}, {"name": "Mid Row", "distinction": "Cable-station row, not the mid-row machine."}, {"name": "Chest-Supported Row", "distinction": "Cable-station row; chest-supported row fixes the torso against a pad."}], "disambiguation": "Pick when 'cable row' is requested."},
    {"name": "Chest-Supported Row", "muscle": "back", "compound": True, "baseline": 85, "equipment": "machine", "aliases": ["Chest Supported Row"], "movement_patterns": ["horizontal_pull", "row"], "movement_pattern": "horizontal_pull", "body_position": "chest_supported", "primary_emphasis": "mid_back_lats", "match_tokens": ["chest-supported", "supported"], "confusable_with": [{"name": "Seated Row", "distinction": "Torso is fixed against a chest pad, unlike the generic seated row."}, {"name": "Mid Row", "distinction": "Torso is fixed against a chest pad, unlike the mid-row machine."}, {"name": "Cable Row", "distinction": "Chest-supported machine row, not a cable station row."}], "disambiguation": "Pick when chest-supported row is requested."},
    {"name": "Face Pulls", "muscle": "back", "compound": False, "baseline": 35, "equipment": "cable"},
    {"name": "Pullups", "muscle": "back", "compound": True, "baseline": 50, "equipment": "bodyweight"},
    # Shoulders
    {"name": "Shoulder Press", "muscle": "shoulders", "compound": True, "baseline": 60, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "movement_pattern": "vertical_press", "body_position": "seated", "shoulder_position": "overhead", "match_tokens": ["shoulder"], "confusable_with": [{"name": "Arnold Press", "distinction": "Machine shoulder press; Arnold press is a free-weight rotational press."}], "disambiguation": "Default for a bare shoulder press: machine shoulder press, not Arnold press."},
    {"name": "Arnold Press", "muscle": "shoulders", "compound": False, "baseline": 50, "equipment": "free_weight", "movement_pattern": "rotational_vertical_press", "body_position": "seated", "shoulder_position": "overhead", "match_tokens": ["arnold"], "confusable_with": [{"name": "Shoulder Press", "distinction": "Free-weight rotational press; choose only when Arnold is requested."}], "disambiguation": "Pick only when 'Arnold' is typed."},
    {"name": "Lateral Raise", "muscle": "shoulders", "compound": False, "baseline": 20, "equipment": "cable"},
    {"name": "Front Raise", "muscle": "shoulders", "compound": False, "baseline": 20, "equipment": "cable"},
    {"name": "Deltoid Fly", "muscle": "shoulders", "compound": False, "baseline": 30, "equipment": "cable", "movement_pattern": "shoulder_fly", "body_position": "standing", "match_tokens": ["deltoid"], "confusable_with": [{"name": "Rear Delt Fly", "distinction": "General cable deltoid fly; rear delt fly is the rear-delt-specific variant."}], "disambiguation": "Default for a bare deltoid fly; rear-delt wording should pick Rear Delt Fly."},
    {"name": "Machine Deltoid Raise", "muscle": "shoulders", "compound": False, "baseline": 30, "equipment": "machine", "aliases": ["Deltoid Raise", "Rear Delt Raise"]},
    {"name": "Rear Delt Fly", "muscle": "shoulders", "compound": False, "baseline": 25, "equipment": "cable", "movement_pattern": "shoulder_fly", "body_position": "standing", "primary_emphasis": "rear_delts", "match_tokens": ["rear"], "confusable_with": [{"name": "Deltoid Fly", "distinction": "Rear-delt-specific fly; choose when rear delt is requested."}], "disambiguation": "Pick when 'rear delt' or posterior-delt fly is requested."},
    # Legs
    {"name": "Leg Press", "muscle": "quads", "compound": True, "baseline": 180, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "movement_patterns": ["squat", "knee_dominant"]},
    {"name": "Hack Squat", "muscle": "quads", "compound": True, "baseline": 135, "equipment": "machine", "movement_patterns": ["squat", "knee_dominant"]},
    {"name": "Bulgarian Split Squat", "muscle": "quads", "compound": True, "baseline": 40, "equipment": "free_weight"},
    {"name": "Leg Extension", "muscle": "quads", "compound": False, "baseline": 80, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"]},
    {"name": "Romanian Deadlift", "muscle": "hamstrings", "compound": True, "baseline": 135, "equipment": "free_weight", "movement_patterns": ["hinge", "posterior_chain"]},
    {"name": "Leg Curl", "muscle": "hamstrings", "compound": False, "baseline": 80, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "movement_patterns": ["knee_flexion", "hamstring_isolation"]},
    {"name": "Back Extension", "muscle": "hamstrings", "compound": False, "baseline": 85, "equipment": "machine", "aliases": ["Low Back Extension", "Lower Back Extension"], "movement_patterns": ["hinge", "posterior_chain"]},
    {"name": "Calf Raise", "muscle": "calves", "compound": False, "baseline": 120, "equipment": "machine", "movement_pattern": "plantar_flexion", "body_position": "standing", "primary_emphasis": "gastrocnemius_bias", "match_tokens": ["standing"], "confusable_with": [{"name": "Calf Raise (Seated)", "distinction": "Standing calf raise biases gastrocnemius; seated calf raise biases soleus."}], "disambiguation": "Default for a bare calf raise: standing machine calf raise, not seated."},
    {"name": "Calf Raise (Seated)", "muscle": "calves", "compound": False, "baseline": 90, "equipment": "machine", "movement_pattern": "plantar_flexion", "body_position": "seated", "primary_emphasis": "soleus_bias", "match_tokens": ["seated"], "confusable_with": [{"name": "Calf Raise", "distinction": "Seated calf raise biases soleus; standing calf raise biases gastrocnemius."}], "disambiguation": "Pick only when seated calf raise is requested."},
    {"name": "Hip Abductor", "muscle": "glutes", "compound": False, "baseline": 100, "equipment": "machine"},
    {"name": "Hip Adductor", "muscle": "adductors", "compound": False, "baseline": 100, "equipment": "machine"},
    # Arms
    {"name": "Biceps Curl", "muscle": "biceps", "compound": False, "baseline": 50, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "aliases": ["Hoist Biceps Curl", "Hoist Roc-It Biceps Curl", "Nautilus Biceps Curl", "Nautilus ONE Biceps Curl"], "movement_pattern": "elbow_flexion", "body_position": "seated", "primary_emphasis": "biceps_general", "match_tokens": ["machine"], "confusable_with": [{"name": "Cable Biceps Curl", "distinction": "Selectorized machine curl; cable biceps curl uses a cable station."}], "disambiguation": "Default for a bare biceps curl when machines are available; cable variant needs 'cable'."},
    {"name": "Cable Biceps Curl", "muscle": "biceps", "compound": False, "baseline": 45, "equipment": "cable", "movement_pattern": "elbow_flexion", "body_position": "standing", "primary_emphasis": "biceps_general", "match_tokens": ["cable"], "confusable_with": [{"name": "Biceps Curl", "distinction": "Cable-station curl; machine biceps curl is selectorized."}], "disambiguation": "Pick when 'cable biceps curl' is requested."},
    {"name": "Hammer Curl", "muscle": "biceps", "compound": False, "baseline": 40, "equipment": "free_weight"},
    {"name": "Preacher Curl", "muscle": "biceps", "compound": False, "baseline": 45, "equipment": "machine", "disabled_by_default": True, "avoid_reason": "User does not perform preacher curls"},
    {"name": "Seated Dip", "muscle": "triceps", "compound": True, "baseline": 100, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"]},
    {"name": "Tricep Pushdown", "muscle": "triceps", "compound": False, "baseline": 50, "equipment": "cable", "movement_patterns": ["elbow_extension", "triceps_extension"]},
    {"name": "Cable Pushdown", "muscle": "triceps", "compound": False, "baseline": 55, "equipment": "cable", "movement_patterns": ["elbow_extension", "triceps_extension"]},
    {"name": "Overhead Tricep Extension", "muscle": "triceps", "compound": False, "baseline": 45, "equipment": "cable", "aliases": ["Tricep Extension", "Triceps Extension", "Tricep Extensions", "Triceps Extensions"], "movement_patterns": ["elbow_extension", "triceps_extension"], "movement_pattern": "elbow_extension", "body_position": "standing", "shoulder_position": "overhead", "primary_emphasis": "triceps_long_head", "match_tokens": ["overhead"], "confusable_with": [{"name": "Triceps Extension", "distinction": "Standing cable, arms overhead, long-head bias; the future seated machine entry has shoulders neutral."}], "disambiguation": "Pick only when 'overhead' is typed."},
    # Core
    {"name": "Crunch Machine", "muscle": "core", "compound": False, "baseline": 60, "equipment": "machine", "equipment_brands": ["Hoist", "Nautilus"], "movement_patterns": ["trunk_flexion", "core_isolation"], "movement_pattern": "trunk_flexion", "body_position": "seated", "primary_emphasis": "rectus_abdominis_flexion", "match_tokens": ["machine"], "confusable_with": [{"name": "Cable Crunch", "distinction": "Selectorized crunch machine; cable crunch uses a cable station."}], "disambiguation": "Default for a bare crunch machine request; cable crunch needs 'cable'."},
    {"name": "Cable Crunch", "muscle": "core", "compound": False, "baseline": 55, "equipment": "cable", "movement_patterns": ["trunk_flexion", "core_isolation"], "movement_pattern": "trunk_flexion", "body_position": "kneeling", "primary_emphasis": "rectus_abdominis_flexion", "match_tokens": ["cable"], "confusable_with": [{"name": "Crunch Machine", "distinction": "Cable-station crunch; crunch machine is selectorized."}], "disambiguation": "Pick when 'cable crunch' is requested."},
    {"name": "Rotary Torso", "muscle": "core", "compound": False, "baseline": 60, "equipment": "machine", "aliases": ["Torso Rotation", "Rotary Torso Machine"], "movement_patterns": ["rotation", "core_isolation"]},
    {"name": "Hanging Leg Raise", "muscle": "core", "compound": True, "baseline": 40, "equipment": "bodyweight", "movement_patterns": ["hip_flexion", "core"]},
    {"name": "Plank", "muscle": "core", "compound": False, "baseline": 0, "equipment": "bodyweight", "movement_patterns": ["isometric", "core"]},
]


def _exercise_disambiguation_annotation_gaps(library=None):
    exercises_by_name = {
        exercise.get("name"): exercise
        for exercise in (library or EXERCISE_LIBRARY)
    }
    gaps = []
    for cluster_name, exercise_names in EXERCISE_DISAMBIGUATION_CLUSTERS.items():
        for exercise_name in exercise_names:
            exercise = exercises_by_name.get(exercise_name)
            if not exercise:
                gaps.append((cluster_name, exercise_name, "missing_entry"))
                continue
            if not exercise.get("match_tokens"):
                gaps.append((cluster_name, exercise_name, "match_tokens"))
            if not any(exercise.get(field) for field in EXERCISE_DISAMBIGUATION_DISTINGUISHING_FIELDS):
                gaps.append((cluster_name, exercise_name, "distinguishing_field"))
    return gaps


EXERCISE_LOOKUP = {}
for _exercise in EXERCISE_LIBRARY:
    _name = _exercise["name"].lower()
    _muscle = _exercise["muscle"]
    _joints = {
        "chest": ["shoulder", "elbow"],
        "back": ["shoulder", "elbow"],
        "shoulders": ["shoulder", "elbow"],
        "quads": ["knee", "hip"],
        "hamstrings": ["hip", "knee"],
        "calves": ["ankle"],
        "glutes": ["hip"],
        "adductors": ["hip"],
        "biceps": ["elbow", "wrist"],
        "triceps": ["elbow", "shoulder"],
        "core": ["spine", "hip"],
    }.get(_muscle, [])
    if ("raise" in _name and _muscle == "shoulders") or "fly" in _name or "crossover" in _name:
        _joints = ["shoulder"]
    if "romanian deadlift" in _name or "back extension" in _name:
        _joints = ["hip", "knee", "spine"]
    if "rotary torso" in _name:
        _joints = ["spine"]
    if "curl" in _name and _muscle == "biceps":
        _joints = ["elbow", "wrist"]
    if "leg extension" in _name or "leg curl" in _name:
        _joints = ["knee"]
    if "calf" in _name:
        _joints = ["ankle"]
    _exercise.setdefault("joints_loaded", _joints)
    EXERCISE_LOOKUP[_exercise["name"]] = _exercise
    for _alias in _exercise.get("aliases", []):
        EXERCISE_LOOKUP[_alias] = _exercise


class ProgressionStatus(Enum):
    ON_TRACK = "On Track"
    PLATEAU = "Plateau"
    REGRESSION = "Regression"


@dataclass
class SetData:
    set_number: int
    weight_lbs: float
    reps: int
    rpe: float = 7.0
    notes: str = ""


@dataclass
class ExerciseData:
    machine: str
    muscle_group: str
    sets: list = field(default_factory=list)
    rest_seconds: int = 120


@dataclass
class WorkoutEntry:
    date: str
    session_type: str
    duration_minutes: int
    exercises: list = field(default_factory=list)
    overall_fatigue: int = 5
    notes: str = ""


@dataclass
class SorenessEntry:
    date: str
    muscle: str
    soreness_level: int
    notes: str = ""


# Load data from JSON files (persists across restarts)
WORKOUTS = load_json(WORKOUTS_FILE, [])
# Backfill stable IDs for legacy workouts (pre-id-tracking). Persist once.
_id_backfill_needed = False
if isinstance(WORKOUTS, list):
    import uuid as _uuid_boot
    for _w in WORKOUTS:
        if isinstance(_w, dict) and not _w.get("id"):
            _w["id"] = _uuid_boot.uuid4().hex[:12]
            _id_backfill_needed = True
    if _id_backfill_needed:
        try:
            # save_json isn't defined until later in the file, so do it raw.
            with open(WORKOUTS_FILE, "w") as _fh:
                json.dump(WORKOUTS, _fh, indent=2, default=str)
            print(f"INFO: backfilled stable IDs on {sum(1 for w in WORKOUTS if w.get('id'))} workouts")
        except Exception as _exc:
            print(f"WARN: workout-id backfill save failed: {_exc}")

SORENESS_DATA = load_json(SORENESS_FILE, [])
CARDIO_DATA = load_json(CARDIO_FILE, [])
RECOVERY_DATA = load_json(RECOVERY_FILE, [])
BASELINES_DATA = load_json(BASELINES_FILE, {})
BODY_DATA = load_json(BODY_FILE, [])
SLEEP_DATA = load_json(SLEEP_FILE, [])
NUTRITION_DATA = load_json(NUTRITION_FILE, [])

# Initialize Oura SQLite storage (safe no-op if file exists)
init_oura_db(OURA_DB_FILE)
init_whoop_db(WHOOP_DB_FILE)
init_data_db()


def _normalize_exercise_name(name):
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _lookup_by_exercise_name(mapping, exercise_name):
    if not isinstance(mapping, dict):
        return None, None
    if exercise_name in mapping:
        return exercise_name, mapping[exercise_name]
    wanted = _normalize_exercise_name(exercise_name)
    for key, value in mapping.items():
        if _normalize_exercise_name(key) == wanted:
            return key, value
    return None, None


def _resolve_exercise_definition(exercise_name):
    _, exercise = _lookup_by_exercise_name(EXERCISE_LOOKUP, exercise_name)
    if not exercise:
        return None
    return exercise


def _canonical_exercise_name(exercise_name):
    exercise = _resolve_exercise_definition(exercise_name)
    return exercise.get("name") if exercise else str(exercise_name or "")


def _positive_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _exercise_name_tokens(name):
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", str(name or "").lower())
        if token and token not in {"machine", "cable", "seated", "standing"}
    }
    return tokens


def _exercise_movement_patterns(exercise):
    return {
        str(pattern).strip().lower()
        for pattern in (exercise or {}).get("movement_patterns", [])
        if str(pattern).strip()
    }


def _similar_exercise_load_source(exercise_name, progression):
    target_ex = _resolve_exercise_definition(exercise_name)
    if not target_ex or not isinstance(progression, dict):
        return None

    target_baseline = _positive_float(target_ex.get("baseline"))
    if target_baseline is None:
        return None
    target_tokens = _exercise_name_tokens(target_ex.get("name"))
    target_patterns = _exercise_movement_patterns(target_ex)
    candidates = []
    for source_name, source_progression in progression.items():
        source_e1rm = _positive_float((source_progression or {}).get("current_e1rm"))
        if source_e1rm is None:
            continue
        source_ex = _resolve_exercise_definition(source_name)
        if not source_ex or source_ex is target_ex:
            continue
        if source_ex.get("muscle") != target_ex.get("muscle"):
            continue
        source_baseline = _positive_float(source_ex.get("baseline"))
        if source_baseline is None:
            continue

        source_tokens = _exercise_name_tokens(source_ex.get("name"))
        source_patterns = _exercise_movement_patterns(source_ex)
        shared_tokens = sorted(target_tokens.intersection(source_tokens))
        shared_patterns = sorted(target_patterns.intersection(source_patterns))
        if not shared_tokens and not shared_patterns:
            continue
        score = len(shared_tokens) * 2 + len(shared_patterns) * 4
        if source_ex.get("compound") == target_ex.get("compound"):
            score += 3
        if source_ex.get("equipment") == target_ex.get("equipment"):
            score += 2
        if not source_ex.get("compound") and not target_ex.get("compound"):
            score += 1
        if score <= 0:
            continue

        scaled_e1rm = source_e1rm * (target_baseline / source_baseline)
        candidates.append({
            "source_name": source_ex.get("name") or source_name,
            "source_e1rm": source_e1rm,
            "estimated_e1rm": scaled_e1rm,
            "score": score,
            "shared_tokens": shared_tokens,
            "shared_patterns": shared_patterns,
        })

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item["score"], -item["source_e1rm"], item["source_name"]))
    best = candidates[0]
    return {
        "e1rm": best["estimated_e1rm"],
        "status": "Similar History",
        "source": "similar_history",
        "detail": (
            f"similar_history:{best['source_name']}->{target_ex.get('name')}; "
            f"scaled {round(best['source_e1rm'], 1)} e1RM by baseline ratio"
        ),
        "inferred_from": best["source_name"],
        "inference_confidence": "medium" if best["score"] >= 5 else "low",
    }


def _has_direct_exercise_progression(exercise_name, progression):
    if not isinstance(progression, dict):
        return False
    wanted = _normalize_exercise_name(_canonical_exercise_name(exercise_name))
    for key, value in progression.items():
        if _positive_float((value or {}).get("current_e1rm")) is None:
            continue
        if _normalize_exercise_name(_canonical_exercise_name(key)) == wanted:
            return True
    return False


def _select_recommendation_e1rm(exercise_name, ex_progression, progression=None):
    """Pick the load source for a recommendation and expose why it won."""
    if not isinstance(ex_progression, dict):
        ex_progression = {}
    progression_key = exercise_name
    if not ex_progression and isinstance(progression, dict):
        wanted = _normalize_exercise_name(_canonical_exercise_name(exercise_name))
        for key, value in progression.items():
            if _normalize_exercise_name(_canonical_exercise_name(key)) == wanted:
                progression_key = key
                ex_progression = value if isinstance(value, dict) else {}
                break

    progression_e1rm = _positive_float(ex_progression.get("current_e1rm"))
    progression_status = ex_progression.get("status", "On Track")

    baseline_key, baseline_value = _lookup_by_exercise_name(BASELINES_DATA, exercise_name)
    baseline_e1rm = _positive_float(baseline_value)

    lookup_key, lookup_exercise = _lookup_by_exercise_name(EXERCISE_LOOKUP, exercise_name)
    hardcoded_e1rm = _positive_float((lookup_exercise or {}).get("baseline"))

    if baseline_e1rm is not None and progression_e1rm is not None and progression_e1rm < baseline_e1rm * 0.95:
        return {
            "e1rm": baseline_e1rm,
            "status": "Calibrated Baseline",
            "source": "baseline_json",
            "detail": (
                f"baseline_json:{baseline_key}; ignored stale progression "
                f"{round(progression_e1rm, 1)} below calibrated baseline {round(baseline_e1rm, 1)}"
            ),
        }

    if progression_e1rm is not None:
        return {
            "e1rm": progression_e1rm,
            "status": progression_status,
            "source": "progression",
            "detail": f"progression:{progression_key}",
        }

    if baseline_e1rm is not None:
        return {
            "e1rm": baseline_e1rm,
            "status": "Calibrated Baseline",
            "source": "baseline_json",
            "detail": f"baseline_json:{baseline_key}",
        }

    similar_source = _similar_exercise_load_source(exercise_name, progression or {})
    if similar_source:
        return similar_source

    return {
        "e1rm": hardcoded_e1rm if hardcoded_e1rm is not None else 100,
        "status": "Baseline",
        "source": "hardcoded",
        "detail": f"hardcoded:{lookup_key or exercise_name}",
    }


# ==================== API HELPERS / VALIDATION ====================

def api_error(message: str, status: int = 400, code: str = "bad_request", details=None):
    payload = {"status": "error", "error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return jsonify(payload), status


def get_json_body(required: bool = True):
    """Parse JSON body safely.

    Returns (data, error_response) where error_response is a Flask response or None.
    """
    data = request.get_json(silent=True)
    if data is None:
        if required:
            return None, api_error("Expected JSON body", status=400, code="invalid_json")
        return {}, None
    if not isinstance(data, dict):
        return None, api_error("JSON body must be an object", status=400, code="invalid_json")
    return data, None


def _coerce_int(v, field_name: str, min_v=None, max_v=None, allow_none: bool = False):
    if v is None and allow_none:
        return None, None
    try:
        iv = int(v)
    except Exception:
        return None, api_error(f"{field_name} must be an integer", 400, code="invalid_field")
    if min_v is not None and iv < min_v:
        return None, api_error(f"{field_name} must be >= {min_v}", 400, code="invalid_field")
    if max_v is not None and iv > max_v:
        return None, api_error(f"{field_name} must be <= {max_v}", 400, code="invalid_field")
    return iv, None


def _coerce_str(v, field_name: str, required: bool = True, max_len: int | None = 2000):
    if v is None:
        if required:
            return None, api_error(f"Missing field: {field_name}", 400, code="missing_field")
        return "", None
    if not isinstance(v, str):
        return None, api_error(f"{field_name} must be a string", 400, code="invalid_field")
    s = v.strip()
    if required and not s:
        return None, api_error(f"{field_name} cannot be empty", 400, code="invalid_field")
    if max_len is not None and len(s) > max_len:
        return None, api_error(f"{field_name} is too long (max {max_len} chars)", 400, code="invalid_field")
    return s, None


def _coerce_float(v, field_name: str, min_v=None, max_v=None, allow_none: bool = False):
    if v is None:
        if allow_none:
            return None, None
        return None, api_error(f"Missing field: {field_name}", 400, code="missing_field")
    try:
        fv = float(v)
    except Exception:
        return None, api_error(f"{field_name} must be a number", 400, code="invalid_field")
    if min_v is not None and fv < min_v:
        return None, api_error(f"{field_name} must be >= {min_v}", 400, code="invalid_field")
    if max_v is not None and fv > max_v:
        return None, api_error(f"{field_name} must be <= {max_v}", 400, code="invalid_field")
    return fv, None


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _parse_iso_to_local_datetime(iso_str):
    """Parse an ISO 8601 timestamp and return a naive server-local datetime.

    The composer sends ``new Date().toISOString()`` (UTC, e.g.
    ``2026-05-19T03:00:00.000Z``). Downstream code in
    ``_nutrition_entry_logged_hour`` reads ``.hour`` directly on the
    parsed value without TZ conversion, so storing the raw UTC string
    misreports a 10 PM CT meal as hour 3. Converting incoming UTC to the
    server's local timezone (via ``astimezone()``) and dropping the
    offset matches the pre-FIT-59 behavior of storing naive local time.

    Returns None when ``iso_str`` is empty or unparseable so callers can
    fall back to their default.
    """
    if not iso_str:
        return None
    s = iso_str.strip()
    if not s:
        return None
    # datetime.fromisoformat tolerates a trailing 'Z' from 3.11+, but
    # normalize for safety and older runtimes.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt  # already naive — assume server-local
    return dt.astimezone().replace(tzinfo=None)


def _local_date_from_iso(iso_str):
    """Return the server-local YYYY-MM-DD for an ISO timestamp, or None.

    See ``_parse_iso_to_local_datetime`` for the parsing/TZ rules.
    """
    dt = _parse_iso_to_local_datetime(iso_str)
    return dt.date().isoformat() if dt is not None else None


def _local_iso_from_iso(iso_str):
    """Return a naive server-local ISO timestamp for an ISO input, or None.

    See ``_parse_iso_to_local_datetime`` for the parsing/TZ rules.
    """
    dt = _parse_iso_to_local_datetime(iso_str)
    return dt.isoformat(timespec="seconds") if dt is not None else None


def _fit95_number(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _apple_health_recommendation_enabled():
    # Tests should not read the owner's real Apple Health DB unless the test
    # explicitly points APPLE_HEALTH_SYNC_DB at an isolated fixture.
    if app.config.get("TESTING") and not os.environ.get("APPLE_HEALTH_SYNC_DB"):
        return False
    return True


APPLE_HEALTH_WORKOUT_HR_INTENSITY_ENV = "APPLE_HEALTH_WORKOUT_HR_INTENSITY"
APPLE_HEALTH_WORKOUT_HR_MIN = 30
APPLE_HEALTH_WORKOUT_HR_MAX = 230


def _apple_health_hr_intensity_enabled():
    value = os.environ.get(APPLE_HEALTH_WORKOUT_HR_INTENSITY_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _apple_health_avg_heart_rate(row):
    if not isinstance(row, dict):
        return None
    value = row.get("avg_heart_rate")
    if value is None:
        value = row.get("avgHeartRate")
    if isinstance(value, dict):
        value = (
            value.get("avg")
            or value.get("average")
            or value.get("qty")
            or value.get("value")
        )
    avg_hr = _fit95_number(value)
    if avg_hr < APPLE_HEALTH_WORKOUT_HR_MIN or avg_hr > APPLE_HEALTH_WORKOUT_HR_MAX:
        return None
    return avg_hr


def _apple_health_intensity_from_avg_hr(avg_hr):
    if avg_hr is None:
        return None
    if avg_hr >= 170:
        return 8
    if avg_hr >= 150:
        return 7
    if avg_hr >= 120:
        return 6
    return 5


def _apple_health_duration_minutes(row):
    for key in ("duration_minutes", "duration_min"):
        value = row.get(key)
        if value is not None:
            return _fit95_number(value)
    duration_sec = row.get("duration_sec")
    if duration_sec is not None:
        return round(_fit95_number(duration_sec) / 60, 1)
    duration = row.get("duration")
    if duration is not None:
        return round(_fit95_number(duration) / 60, 1)
    return 0.0


def _apple_health_activity(row):
    activity = (
        row.get("activity_type")
        or row.get("activity")
        or row.get("type")
        or row.get("workoutActivityType")
        or row.get("name")
        or "Other"
    )
    try:
        from apple_health_parser import ACTIVITY_MAP
        return ACTIVITY_MAP.get(int(activity), str(activity).strip())
    except (ImportError, TypeError, ValueError):
        return str(activity).strip()


def _ignore_apple_health_workout(row):
    return _apple_health_activity(row).strip().lower() == "other"


def _apple_health_start_iso(row):
    return (
        row.get("startDate")
        or row.get("start")
        or row.get("start_time")
        or row.get("created_at")
        or ""
    )


def _normalise_apple_health_muscle_groups(row):
    groups = row.get("muscle_groups") or row.get("muscles") or []
    if isinstance(groups, dict):
        groups = [
            {"muscle": muscle, **(data if isinstance(data, dict) else {})}
            for muscle, data in groups.items()
        ]
    exercises = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        muscle = (group.get("muscle") or group.get("muscle_group") or group.get("name") or "").strip().lower()
        if not muscle:
            continue
        sets_count = max(1, int(round(_fit95_number(group.get("sets") or group.get("set_count"), 1))))
        volume_load = _fit95_number(group.get("volume_load") or group.get("volume") or group.get("load"))
        weight = volume_load / sets_count if volume_load > 0 else 0
        exercises.append({
            "machine": "Apple Health Strength",
            "muscle_group": muscle,
            "sets": [
                {"set_number": idx + 1, "weight_lbs": weight, "reps": 1}
                for idx in range(sets_count)
            ],
        })
    return exercises


def _apple_health_exercise_volume_load(exercises):
    volume_load = 0.0
    for exercise in exercises:
        for set_row in exercise.get("sets") or []:
            volume_load += _fit95_number(set_row.get("weight_lbs")) * _fit95_number(set_row.get("reps"))
    return volume_load


def _apple_health_recommendation_load(row, exercises, duration_minutes):
    volume_load = _apple_health_exercise_volume_load(exercises)
    if volume_load > 0:
        return volume_load
    intensity = _fit95_number(row.get("intensity"), 1.0)
    if intensity > 10:
        intensity = intensity / 10
    hr_intensity = _apple_health_intensity_from_avg_hr(_apple_health_avg_heart_rate(row))
    if _apple_health_hr_intensity_enabled() and hr_intensity is not None:
        intensity = max(intensity, hr_intensity)
    intensity = min(10.0, intensity)
    return duration_minutes * max(1.0, intensity)


def _apple_health_base_intensity(row):
    intensity = _fit95_number(row.get("intensity"), 1.0)
    if intensity > 10:
        intensity = intensity / 10
    return min(10.0, intensity)


def _normalise_apple_health_workout(row):
    if not isinstance(row, dict):
        return None
    if _ignore_apple_health_workout(row):
        return None
    start_iso = _apple_health_start_iso(row)
    date_s = _local_date_from_iso(start_iso) or row.get("date")
    if not date_s:
        return None
    duration_minutes = _apple_health_duration_minutes(row)
    activity = _apple_health_activity(row)
    exercises = _normalise_apple_health_muscle_groups(row)
    volume_load = _apple_health_exercise_volume_load(exercises)
    avg_hr = _apple_health_avg_heart_rate(row)
    hr_intensity = _apple_health_intensity_from_avg_hr(avg_hr)
    hr_intensity_applied = (
        _apple_health_hr_intensity_enabled()
        and hr_intensity is not None
        and hr_intensity > max(1.0, _apple_health_base_intensity(row))
        and volume_load <= 0
    )
    load = _apple_health_recommendation_load(row, exercises, duration_minutes)
    return normalize_history_item({
        "id": f"apple-health:{start_iso or date_s}:{activity}:{duration_minutes}",
        "date": date_s,
        "created_at": _local_iso_from_iso(start_iso) or start_iso,
        "session_type": activity,
        "duration_minutes": duration_minutes,
        "exercises": exercises,
        "avg_heart_rate": avg_hr,
        "recommendation_load": load,
        "source": "apple_health",
        "apple_health": {
            "activity_type": activity,
            "start": start_iso,
            "duration_minutes": duration_minutes,
            "avg_heart_rate": avg_hr,
            "hr_intensity": hr_intensity,
            "hr_intensity_applied": hr_intensity_applied,
        },
    }, source="apple_health")


def _load_apple_health_recommendation_workouts(days=28):
    if not _apple_health_recommendation_enabled():
        return []
    try:
        from apple_health_parser import _get_sync_records, parse_workouts as _parse_ah_file_workouts
    except Exception:
        return []
    rows = []
    try:
        rows.extend(_parse_ah_file_workouts() or [])
    except Exception:
        pass
    try:
        rows.extend(_get_sync_records("workouts", days) or [])
    except Exception:
        pass
    normalised = []
    for row in rows:
        workout = _normalise_apple_health_workout(row)
        if workout:
            normalised.append(workout)
    return normalised


def _workout_start_dt(workout):
    for key in ("startDate", "start", "start_time", "created_at"):
        dt = _parse_iso_to_local_datetime(str(workout.get(key) or ""))
        if dt is not None:
            return dt
    apple_health = workout.get("apple_health") if isinstance(workout, dict) else None
    if isinstance(apple_health, dict):
        return _parse_iso_to_local_datetime(str(apple_health.get("start") or ""))
    return None


def _workout_duration_minutes(workout):
    duration = _fit95_number(workout.get("duration_minutes") or workout.get("duration_min"))
    if duration:
        return duration
    cardio = workout.get("cardio") if isinstance(workout, dict) else None
    if isinstance(cardio, dict):
        return _fit95_number(cardio.get("duration_minutes") or cardio.get("duration_min"))
    return 0.0


def _workout_activity_for_dedupe(workout):
    if not isinstance(workout, dict):
        return ""
    cardio = workout.get("cardio")
    if isinstance(cardio, dict):
        value = cardio.get("activity_type") or cardio.get("type")
        if value:
            return _canonical_cardio_fatigue_type(value)
    apple_health = workout.get("apple_health")
    if isinstance(apple_health, dict):
        value = apple_health.get("activity_type")
        if value:
            return _canonical_cardio_fatigue_type(value)
    value = workout.get("activity_type") or workout.get("type") or workout.get("session_type")
    return _canonical_cardio_fatigue_type(value)


def _workout_has_explicit_start(workout):
    if not isinstance(workout, dict):
        return False
    if any(workout.get(key) for key in ("startDate", "start", "start_time")):
        return True
    apple_health = workout.get("apple_health")
    return isinstance(apple_health, dict) and bool(apple_health.get("start"))


def _same_date_activity_for_recommendation_dedupe(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    left_activity = _workout_activity_for_dedupe(left)
    right_activity = _workout_activity_for_dedupe(right)
    return (
        left.get("date") == right.get("date")
        and bool(left_activity)
        and left_activity == right_activity
    )


def _is_same_workout_for_recommendations(left, right):
    left_start = _workout_start_dt(left)
    right_start = _workout_start_dt(right)
    duration_delta = abs(_workout_duration_minutes(left) - _workout_duration_minutes(right))
    if duration_delta > 5:
        return False
    left_activity = _workout_activity_for_dedupe(left)
    right_activity = _workout_activity_for_dedupe(right)
    if left_activity and right_activity and left_activity != right_activity:
        return False
    if left_start is None or right_start is None:
        return _same_date_activity_for_recommendation_dedupe(left, right)
    start_delta = abs((left_start - right_start).total_seconds())
    if start_delta <= 5 * 60:
        return True
    if not (_workout_has_explicit_start(left) and _workout_has_explicit_start(right)):
        return _same_date_activity_for_recommendation_dedupe(left, right)
    return False


def _recommendation_workouts_with_apple_health(workouts, days=28):
    merged = list(workouts or [])
    for apple_workout in _load_apple_health_recommendation_workouts(days=days):
        if not any(_is_same_workout_for_recommendations(existing, apple_workout) for existing in merged):
            merged.append(apple_workout)
    return merged


def _cardio_data_with_apple_health(cardio_data, workouts=None):
    merged = list(cardio_data or [])

    def same_date_cardio(existing, apple_workout):
        if not isinstance(existing, dict):
            return False
        if _workout_start_dt(existing) is not None:
            return False
        if existing.get("date") != apple_workout.get("date"):
            return False
        existing_activity = _canonical_cardio_fatigue_type(existing.get("activity_type") or existing.get("type"))
        apple_activity = _canonical_cardio_fatigue_type((apple_workout.get("apple_health") or {}).get("activity_type") or apple_workout.get("session_type"))
        if existing_activity != apple_activity:
            return False
        return abs(_workout_duration_minutes(existing) - _workout_duration_minutes(apple_workout)) <= 5

    for workout in _recommendation_workouts_with_apple_health(workouts if workouts is not None else WORKOUTS):
        if workout.get("source") != "apple_health":
            continue
        activity = ((workout.get("apple_health") or {}).get("activity_type") or workout.get("session_type") or "").strip()
        if not activity:
            continue
        if any(
            _is_same_workout_for_recommendations(existing, workout) or same_date_cardio(existing, workout)
            for existing in merged
            if isinstance(existing, dict)
        ):
            continue
        apple_health = workout.get("apple_health") or {}
        hr_intensity = apple_health.get("hr_intensity") if isinstance(apple_health, dict) else None
        intensity = 5
        if (
            _apple_health_hr_intensity_enabled()
            and isinstance(apple_health, dict)
            and apple_health.get("hr_intensity_applied")
            and hr_intensity is not None
        ):
            intensity = max(intensity, min(10, _fit95_number(hr_intensity, 5)))
        merged.append({
            "date": workout.get("date"),
            "activity_type": activity,
            "duration_minutes": workout.get("duration_minutes") or 0,
            "intensity": intensity,
            "avg_heart_rate": workout.get("avg_heart_rate"),
            "source": "apple_health",
            "created_at": workout.get("created_at"),
        })
    return merged


def _apple_health_hr_intensity_summary(days=7):
    if not _apple_health_hr_intensity_enabled():
        return {"applied_count": 0}
    applied = []
    for workout in _load_apple_health_recommendation_workouts(days=days):
        apple_health = workout.get("apple_health") or {}
        if not isinstance(apple_health, dict) or not apple_health.get("hr_intensity_applied"):
            continue
        applied.append({
            "avg_heart_rate": workout.get("avg_heart_rate"),
            "hr_intensity": apple_health.get("hr_intensity"),
        })
    if not applied:
        return {"applied_count": 0}
    max_item = max(applied, key=lambda item: _fit95_number(item.get("avg_heart_rate")))
    return {
        "applied_count": len(applied),
        "max_avg_heart_rate": max_item.get("avg_heart_rate"),
        "max_hr_intensity": max_item.get("hr_intensity"),
    }


def _browser_local_date_from_value(local_date):
    """Return a YYYY-MM-DD browser-local date string, or None.

    FIT-66 adds an explicit browser-local calendar date so meal dates do
    not depend on the Flask server's timezone. Keep this strict: callers
    fall back to older timestamp behavior when the client omits it.
    """
    if not local_date:
        return None
    s = str(local_date).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _browser_local_datetime_from_iso(local_iso):
    """Parse browser-local ISO and drop tzinfo without server conversion.

    ``local_iso`` carries the user's wall-clock time plus browser offset
    (for example ``2026-05-18T22:00:00-05:00``). The offset is evidence,
    not an instruction to convert through the server timezone. Persist the
    wall-clock portion so date and hour match what the user submitted.
    """
    if not local_iso:
        return None
    s = str(local_iso).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


def _browser_local_iso_from_iso(local_iso):
    dt = _browser_local_datetime_from_iso(local_iso)
    return dt.isoformat(timespec="seconds") if dt is not None else None


def _browser_local_date_from_iso(local_iso):
    dt = _browser_local_datetime_from_iso(local_iso)
    return dt.date().isoformat() if dt is not None else None


def _current_data_user_id():
    try:
        from flask_login import current_user
        authenticated = bool(current_user and current_user.is_authenticated)
    except RuntimeError:
        authenticated = False
    if authenticated:
        return data_user_id_for(current_user.get_id())
    return 1


def _get_latest_weight():
    if not BODY_DATA:
        return None
    sorted_body = sorted(BODY_DATA, key=lambda x: x.get("date") or "", reverse=True)
    return sorted_body[0].get("weight_lbs")


def _get_nutrition_targets():
    calories_target = USER_SETTINGS.get("daily_calorie_target")
    if calories_target is None or calories_target == "":
        calories_target = 2200
    try:
        calories_target = int(calories_target)
    except Exception:
        calories_target = 2200

    protein_target = USER_SETTINGS.get("daily_protein_target_g")
    try:
        protein_target = float(protein_target)
    except Exception:
        protein_target = None
    if not protein_target or protein_target <= 0:
        latest_weight = _get_latest_weight()
        if latest_weight:
            protein_target = round(float(latest_weight) * 0.8)
        else:
            protein_target = 148
    return int(calories_target), float(protein_target)


def _compute_carb_fat_targets(calories_target: int, protein_target: float):
    # No per-user carb/fat target today (FIT-23 keeps scope tight); derive from
    # remaining calories after protein, split 55% carbs / 45% fat by calories.
    protein_cal = float(protein_target) * 4.0
    remaining = max(float(calories_target) - protein_cal, 0.0)
    carbs_target = round((remaining * 0.55) / 4.0, 1)
    fat_target = round((remaining * 0.45) / 9.0, 1)
    return carbs_target, fat_target


SODIUM_NEXT_DAY_CONTEXT_MG = 2300
LATE_MEAL_CONTEXT_HOUR = 20
UNDER_FUELED_CALORIES_PCT = 60
UNDER_FUELED_PROTEIN_PCT = 50


def _nutrition_entry_logged_hour(entry):
    raw = None
    if isinstance(entry, dict):
        raw = entry.get("logged_at") or entry.get("source_timestamp") or entry.get("timestamp")
    dt = _parse_iso_date_or_datetime(raw) if raw else None
    return dt.hour if dt else None


_food_log_read_failure_logged = False


def _food_log_entries_for_context(since=None, limit=None):
    global _food_log_read_failure_logged
    try:
        return get_food_logs(_current_data_user_id(), limit=limit, since=since)
    except Exception:
        if not _food_log_read_failure_logged:
            app.logger.warning(
                "food_logs read failed; falling back to legacy nutrition JSON",
                exc_info=True,
            )
            _food_log_read_failure_logged = True
        return []


def _legacy_nutrition_client_id(entry: dict, index: int) -> str:
    payload = {
        "index": index,
        "date": entry.get("date"),
        "logged_at": entry.get("logged_at"),
        "calories": entry.get("calories"),
        "protein_g": entry.get("protein_g"),
        "carbs_g": entry.get("carbs_g"),
        "fat_g": entry.get("fat_g"),
        "sodium_mg": entry.get("sodium_mg"),
        "notes": entry.get("notes"),
        "item_name": entry.get("item_name"),
    }
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"legacy-nutrition-{digest}"


def _legacy_nutrition_food_log_match(entry: dict) -> dict | None:
    date_s = _nutrition_entry_day(entry)
    if not date_s:
        return None
    identity_fields = {}
    notes = str(entry.get("notes") or "").strip()
    if notes:
        identity_fields["context_note"] = notes
    for key in ("context_note", "item_name", "portion_description", "meal_type", "source_timestamp"):
        value = str(entry.get(key) or "").strip()
        if value:
            identity_fields[key] = value
    if not identity_fields:
        return None
    return {
        "date": date_s,
        "calories": entry.get("calories"),
        "protein_g": entry.get("protein_g"),
        "carbs_g": entry.get("carbs_g"),
        "fat_g": entry.get("fat_g"),
        "sodium_mg": entry.get("sodium_mg"),
        **identity_fields,
    }


def backfill_legacy_nutrition_client_ids(user_id: int | None = None) -> dict:
    """Backfill stable client_ids onto legacy NUTRITION_DATA rows.

    Food-log linking is intentionally conservative: only rows with an
    extra identity field such as notes/context_note or item_name can claim
    a clientless food_log. Macro-only matches are left unlinked so two
    distinct same-day meals with identical macros are not collapsed.
    """
    if not isinstance(NUTRITION_DATA, list):
        return {"legacy_backfilled": 0, "food_logs_linked": 0}
    user_id = user_id if user_id is not None else _current_data_user_id()
    changed = False
    backfilled = 0
    linked = 0
    used_client_ids = {
        str(entry.get("client_id"))
        for entry in NUTRITION_DATA
        if isinstance(entry, dict) and entry.get("client_id")
    }
    for index, entry in enumerate(NUTRITION_DATA):
        if not isinstance(entry, dict):
            continue
        client_id = str(entry.get("client_id") or "").strip()
        if not client_id:
            client_id = _legacy_nutrition_client_id(entry, index)
            suffix = 1
            base_client_id = client_id
            while client_id in used_client_ids:
                suffix += 1
                client_id = f"{base_client_id}-{suffix}"
            entry["client_id"] = client_id
            used_client_ids.add(client_id)
            changed = True
            backfilled += 1
        match = _legacy_nutrition_food_log_match(entry)
        if match:
            try:
                if backfill_food_log_client_id(user_id, client_id, match):
                    linked += 1
            except Exception:
                app.logger.warning("legacy nutrition food_log client_id backfill failed", exc_info=True)
    if changed:
        save_json(NUTRITION_FILE, NUTRITION_DATA)
    return {"legacy_backfilled": backfilled, "food_logs_linked": linked}


def _nutrition_context_for_date(
    date_s: str,
    now=None,
    hard_training_planned: bool = False,
    food_log_entries: list[dict] | None = None,
):
    """Backend contract for food-aware daily coaching context.

    FIT-136 allows accepted food logs to schedule workout adaptation; pending
    and unaccepted estimates are still excluded from totals and adaptation.
    """
    food_log_day_entries = [
        entry for entry in (food_log_entries or [])
        if _nutrition_entry_day(entry) == date_s and _nutrition_entry_accepted(entry)
    ]
    food_log_day_candidates = [
        entry for entry in (food_log_entries or [])
        if _nutrition_entry_day(entry) == date_s
    ]
    has_accepted_food_log_day_entries = food_log_entries is not None and food_log_day_entries
    if has_accepted_food_log_day_entries:
        totals = _summarize_nutrition_entries_for_date(food_log_day_entries, date_s)
    else:
        totals = _summarize_nutrition_for_date(date_s)
    calories_target, protein_target = _get_nutrition_targets()
    carbs_target, fat_target = _compute_carb_fat_targets(calories_target, protein_target)

    calories_pct = int(round((totals["calories"] / calories_target) * 100)) if calories_target else 0
    protein_pct = int(round((totals["protein_g"] / protein_target) * 100)) if protein_target else 0
    carbs_pct = int(round((totals["carbs_g"] / carbs_target) * 100)) if carbs_target else 0
    fat_pct = int(round((totals["fat_g"] / fat_target) * 100)) if fat_target else 0

    calories_remaining = int(calories_target - totals["calories"])
    protein_gap_g = round(float(protein_target) - float(totals["protein_g"]), 1)
    carbs_remaining_g = round(float(carbs_target) - float(totals["carbs_g"]), 1)
    fat_remaining_g = round(float(fat_target) - float(totals["fat_g"]), 1)

    accepted_entries = [
        entry for entry in (NUTRITION_DATA if isinstance(NUTRITION_DATA, list) else [])
        if _nutrition_entry_day(entry) == date_s and _nutrition_entry_accepted(entry)
    ]
    context_entries = food_log_day_entries if has_accepted_food_log_day_entries else accepted_entries
    pending_candidates = (
        food_log_day_candidates
        if food_log_entries is not None and food_log_day_candidates
        else list(NUTRITION_DATA if isinstance(NUTRITION_DATA, list) else [])
    )
    pending_review_count = sum(
        1 for entry in pending_candidates
        if _nutrition_entry_day(entry) == date_s and _nutrition_entry_pending_review(entry)
    )
    late_entries_count = sum(
        1 for entry in context_entries
        if (_nutrition_entry_logged_hour(entry) or -1) >= LATE_MEAL_CONTEXT_HOUR
    )

    warnings = []
    if calories_remaining < 0:
        warnings.append({
            "code": "calories_over_target",
            "message": "Calories are over target for today.",
            "severity": "warning",
        })
    elif totals["entries_count"] > 0 and calories_pct < 80:
        warnings.append({
            "code": "calories_remaining",
            "message": "Calories remain below today's target.",
            "severity": "info",
        })
    if protein_gap_g > 0 and totals["entries_count"] > 0:
        warnings.append({
            "code": "protein_gap",
            "message": "Protein is still below today's target.",
            "severity": "info",
        })
    if totals["entries_count"] > 0 and hard_training_planned and (
        calories_pct < UNDER_FUELED_CALORIES_PCT or protein_pct < UNDER_FUELED_PROTEIN_PCT
    ):
        warnings.append({
            "code": "under_fueled_hard_workout",
            "message": "Hard training is planned while food intake is still low.",
            "severity": "warning",
        })
    if pending_review_count:
        warnings.append({
            "code": "food_pending_review",
            "message": "Pending food estimates are excluded until accepted.",
            "severity": "info",
        })

    next_day_notes = []
    high_sodium = int(totals["sodium_mg"]) >= SODIUM_NEXT_DAY_CONTEXT_MG
    if high_sodium:
        next_day_notes.append("High sodium today may affect tomorrow's scale/readiness interpretation.")
    if late_entries_count:
        next_day_notes.append("Late meal timing may affect tomorrow's scale/readiness interpretation.")

    return {
        "date": date_s,
        "totals": {
            "calories": totals["calories"],
            "protein_g": round(totals["protein_g"], 1),
            "carbs_g": round(totals["carbs_g"], 1),
            "fat_g": round(totals["fat_g"], 1),
            "sodium_mg": int(totals["sodium_mg"]),
            "entries_count": totals["entries_count"],
        },
        "targets": {
            "calories": calories_target,
            "protein_g": round(protein_target, 1),
            "carbs_g": carbs_target,
            "fat_g": fat_target,
        },
        "remaining": {
            "calories": calories_remaining,
            "protein_g": protein_gap_g,
            "carbs_g": carbs_remaining_g,
            "fat_g": fat_remaining_g,
        },
        "percentages": {
            "calories": calories_pct,
            "protein": protein_pct,
            "carbs": carbs_pct,
            "fat": fat_pct,
        },
        "accepted_entries_count": len(context_entries),
        "pending_review_count": pending_review_count,
        "warnings": warnings,
        "next_day_context": {
            "high_sodium": high_sodium,
            "late_meal": late_entries_count > 0,
            "late_entries_count": late_entries_count,
            "notes": next_day_notes,
        },
        "plan_adjustment": workout_adaptation.plan_adjustment_contract(),
        "uses_only_accepted_entries": True,
    }


def _public_nutrition_coaching_context(context: dict) -> dict:
    """Return only coaching-only fields for public nutrition payloads.

    Totals, targets, remaining values, and percentages are exposed as flat
    fields on ``nutrition_today``. Keeping them out of ``coaching_context`` makes
    those flat fields the public source of truth while preserving the richer
    internal context for server-side callers.
    """
    return {
        "accepted_entries_count": context["accepted_entries_count"],
        "pending_review_count": context["pending_review_count"],
        "warnings": context["warnings"],
        "next_day_context": context["next_day_context"],
        "plan_adjustment": context["plan_adjustment"],
        "uses_only_accepted_entries": context["uses_only_accepted_entries"],
    }


def _nutrition_today_public_payload(date_s: str, nutrition_context: dict) -> dict:
    totals = nutrition_context["totals"]
    targets = nutrition_context["targets"]
    remaining = nutrition_context["remaining"]
    percentages = nutrition_context["percentages"]
    return {
        "date": date_s,
        "calories": totals["calories"],
        "protein_g": round(totals["protein_g"], 1),
        "carbs_g": round(totals["carbs_g"], 1),
        "fat_g": round(totals["fat_g"], 1),
        "sodium_mg": int(totals["sodium_mg"]),
        "calories_target": targets["calories"],
        "protein_target_g": round(targets["protein_g"], 1),
        "carbs_target_g": targets["carbs_g"],
        "fat_target_g": targets["fat_g"],
        "calories_remaining": remaining["calories"],
        "protein_gap_g": remaining["protein_g"],
        "carbs_remaining_g": remaining["carbs_g"],
        "fat_remaining_g": remaining["fat_g"],
        "calories_pct": percentages["calories"],
        "protein_pct": percentages["protein"],
        "carbs_pct": percentages["carbs"],
        "fat_pct": percentages["fat"],
        "entries_count": totals["entries_count"],
        "coaching_context": _public_nutrition_coaching_context(nutrition_context),
    }


def _apply_due_workout_adaptations_for_plan(
    next_workout: dict,
    *,
    date_s: str,
    food_log_entries: list[dict],
    nutrition_context: dict,
    active_workout_open: bool = False,
    completed_sets_by_exercise: dict[str, int] | None = None,
) -> tuple[dict, list[dict]]:
    """Evaluate closed FIT-136 windows against a generated remaining plan."""
    current_visible_plan = _fit136_visible_workout_plan(next_workout or {})
    last_adapted_plan = (next_workout or {}).get("_fit136_last_adapted_plan")
    cached_base = (next_workout or {}).get("_fit136_base_recommendation")
    if cached_base and last_adapted_plan == current_visible_plan:
        adaptation_base = copy.deepcopy(cached_base)
    else:
        adaptation_base = copy.deepcopy(current_visible_plan)
    try:
        patched, events = workout_adaptation.apply_due_adaptations(
            _current_data_user_id(),
            adaptation_base,
            food_log_entries=food_log_entries,
            nutrition_context=nutrition_context,
            settings=USER_SETTINGS,
            plan_date=date_s,
            active_workout_open=active_workout_open,
            completed_sets_by_exercise=completed_sets_by_exercise or {},
        )
        if not events:
            return next_workout, []
        if any(event.get("status") == "applied" for event in events):
            patched["_fit136_base_recommendation"] = adaptation_base
            patched["_fit136_last_adapted_plan"] = _fit136_visible_workout_plan(patched)
        elif events:
            return next_workout, events
        return patched, events
    except Exception:
        app.logger.warning("workout adaptation evaluation failed", exc_info=True)
        return next_workout, []


def _fit136_visible_workout_plan(plan: dict) -> dict:
    if not isinstance(plan, dict):
        return {}
    return {
        key: copy.deepcopy(value)
        for key, value in plan.items()
        if not str(key).startswith("_fit136_")
    }


def _enqueue_workout_adaptation_after_accept(user_id: int, food_logs: list[dict]) -> dict | None:
    """Schedule FIT-136 adaptation from accepted/saved normalized food rows only."""
    try:
        return workout_adaptation.enqueue_accepted_food_logs(user_id, food_logs)
    except Exception:
        app.logger.warning("workout adaptation enqueue failed", exc_info=True)
        return None


def _completed_sets_query_param(raw_value: str | None) -> dict[str, int]:
    if not raw_value:
        return {}
    try:
        payload = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    completed: dict[str, int] = {}
    for key, value in payload.items():
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if key and count > 0:
            completed[str(key)] = count
    return completed


def _workout_looks_hard(recommendation) -> bool:
    if not isinstance(recommendation, dict):
        return False
    try:
        if int(recommendation.get("estimated_minutes") or 0) >= 60:
            return True
    except Exception:
        pass
    meso = recommendation.get("mesocycle") or {}
    try:
        return float(meso.get("rpe_base") or 0) >= 8
    except Exception:
        return False


def _summarize_nutrition_for_date(date_s: str):
    return _summarize_nutrition_entries_for_date(NUTRITION_DATA or [], date_s)


def _summarize_nutrition_entries_for_date(entries, date_s: str):
    totals = {
        "calories": 0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "sodium_mg": 0,
        "entries_count": 0,
    }
    for entry in entries or []:
        if _nutrition_entry_day(entry) != date_s:
            continue
        if _nutrition_entry_pending_review(entry):
            continue
        totals["calories"] += int(entry.get("calories") or 0)
        totals["protein_g"] += float(entry.get("protein_g") or 0)
        totals["carbs_g"] += float(entry.get("carbs_g") or 0)
        totals["fat_g"] += float(entry.get("fat_g") or 0)
        totals["sodium_mg"] += int(entry.get("sodium_mg") or 0)
        totals["entries_count"] += 1
    return totals


def _nutrition_entry_day(entry):
    if not isinstance(entry, dict):
        return None
    raw = entry.get("date") or entry.get("day") or entry.get("logged_at")
    if not raw:
        return None
    return str(raw)[:10]


def _nutrition_entry_pending_review(entry):
    if not isinstance(entry, dict):
        return False
    status = str(
        entry.get("status")
        or entry.get("review_state")
        or entry.get("estimate_status")
        or entry.get("correction_state")
        or ""
    ).strip().lower()
    if status in {"pending", "pending_review", "needs_review", "review"}:
        return True
    if entry.get("pending_review") is True:
        return True
    if entry.get("accepted") is False:
        return True
    return False


def _nutrition_entry_accepted(entry):
    return isinstance(entry, dict) and not _nutrition_entry_pending_review(entry)


def _food_log_import_record(food_log):
    """Return a copy safe for idempotent backup replay."""
    record = dict(food_log)
    if record.get("correction_state") == CORRECTION_STATE_PENDING_REVIEW:
        original = record.get("original_estimate")
        record["original_estimate"] = {
            **(original if isinstance(original, dict) else {}),
            "_imported_pending_untrusted": True,
        }
    if record.get("client_id"):
        return record
    identity = "|".join(
        str(record.get(key) or "")
        for key in ("logged_at", "source_timestamp", "date", "item_name", "calories")
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    record["client_id"] = f"backup-food-log-{digest}"
    return record


def calculate_e1rm(weight: float, reps: int) -> float:
    """Calculate estimated 1RM: weight × (1 + reps/30)."""
    return weight * (1 + reps / 30)


def get_sample_data():
    """Generate sample workout data."""
    workouts = [
        {
            "date": "2026-01-06", "session_type": "upper", "duration_minutes": 55,
            "exercises": [
                {"machine": "Bench Press", "muscle_group": "chest",
                 "sets": [{"set_number": 1, "weight_lbs": 135, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 145, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 145, "reps": 7, "rpe": 8.5}]},
                {"machine": "Lat Pulldown", "muscle_group": "back",
                 "sets": [{"set_number": 1, "weight_lbs": 120, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 130, "reps": 8, "rpe": 7.5},
                          {"set_number": 3, "weight_lbs": 130, "reps": 8, "rpe": 8}]},
                {"machine": "Overhead Press", "muscle_group": "shoulders",
                 "sets": [{"set_number": 1, "weight_lbs": 75, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 85, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 85, "reps": 6, "rpe": 9}]}
            ]
        },
        {
            "date": "2026-01-09", "session_type": "lower", "duration_minutes": 50,
            "exercises": [
                {"machine": "Leg Press", "muscle_group": "quads",
                 "sets": [{"set_number": 1, "weight_lbs": 270, "reps": 12, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 315, "reps": 10, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 315, "reps": 8, "rpe": 8.5}]},
                {"machine": "Romanian Deadlift", "muscle_group": "hamstrings",
                 "sets": [{"set_number": 1, "weight_lbs": 135, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 155, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 155, "reps": 8, "rpe": 8}]}
            ]
        },
        {
            "date": "2026-01-13", "session_type": "upper", "duration_minutes": 60,
            "exercises": [
                {"machine": "Bench Press", "muscle_group": "chest",
                 "sets": [{"set_number": 1, "weight_lbs": 145, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 155, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 155, "reps": 6, "rpe": 9}]},
                {"machine": "Lat Pulldown", "muscle_group": "back",
                 "sets": [{"set_number": 1, "weight_lbs": 130, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 140, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 140, "reps": 7, "rpe": 8.5}]},
                {"machine": "Overhead Press", "muscle_group": "shoulders",
                 "sets": [{"set_number": 1, "weight_lbs": 85, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 90, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 90, "reps": 6, "rpe": 9}]}
            ]
        },
        {
            "date": "2026-01-16", "session_type": "lower", "duration_minutes": 55,
            "exercises": [
                {"machine": "Leg Press", "muscle_group": "quads",
                 "sets": [{"set_number": 1, "weight_lbs": 315, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 340, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 340, "reps": 8, "rpe": 8.5}]},
                {"machine": "Romanian Deadlift", "muscle_group": "hamstrings",
                 "sets": [{"set_number": 1, "weight_lbs": 155, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 165, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 165, "reps": 7, "rpe": 8.5}]}
            ]
        },
        {
            "date": "2026-01-20", "session_type": "upper", "duration_minutes": 55,
            "exercises": [
                {"machine": "Bench Press", "muscle_group": "chest",
                 "sets": [{"set_number": 1, "weight_lbs": 155, "reps": 8, "rpe": 7.5},
                          {"set_number": 2, "weight_lbs": 160, "reps": 6, "rpe": 8.5},
                          {"set_number": 3, "weight_lbs": 160, "reps": 5, "rpe": 9}]},
                {"machine": "Lat Pulldown", "muscle_group": "back",
                 "sets": [{"set_number": 1, "weight_lbs": 140, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 145, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 145, "reps": 7, "rpe": 8.5}]},
                {"machine": "Overhead Press", "muscle_group": "shoulders",
                 "sets": [{"set_number": 1, "weight_lbs": 90, "reps": 8, "rpe": 7.5},
                          {"set_number": 2, "weight_lbs": 95, "reps": 6, "rpe": 8.5},
                          {"set_number": 3, "weight_lbs": 95, "reps": 5, "rpe": 9}]}
            ]
        },
        {
            "date": "2026-01-23", "session_type": "lower", "duration_minutes": 50,
            "exercises": [
                {"machine": "Leg Press", "muscle_group": "quads",
                 "sets": [{"set_number": 1, "weight_lbs": 340, "reps": 10, "rpe": 7.5},
                          {"set_number": 2, "weight_lbs": 360, "reps": 8, "rpe": 8.5},
                          {"set_number": 3, "weight_lbs": 360, "reps": 6, "rpe": 9}]},
                {"machine": "Romanian Deadlift", "muscle_group": "hamstrings",
                 "sets": [{"set_number": 1, "weight_lbs": 165, "reps": 10, "rpe": 7},
                          {"set_number": 2, "weight_lbs": 175, "reps": 8, "rpe": 8},
                          {"set_number": 3, "weight_lbs": 175, "reps": 7, "rpe": 8.5}]}
            ]
        }
    ]

    soreness = [
        {"date": "2026-01-24", "muscle": "chest", "soreness_level": 4, "notes": "Mild DOMS"},
        {"date": "2026-01-24", "muscle": "back", "soreness_level": 3, "notes": "Light soreness"},
        {"date": "2026-01-24", "muscle": "quads", "soreness_level": 6, "notes": "Moderate DOMS"},
        {"date": "2026-01-24", "muscle": "hamstrings", "soreness_level": 5, "notes": "Moderate"},
        {"date": "2026-01-24", "muscle": "shoulders", "soreness_level": 2, "notes": "Minimal"}
    ]

    return workouts, soreness


def calculate_progression_status(workouts):
    """Track progression status for each exercise."""
    exercise_history = {}

    for workout in workouts:
        for exercise in workout.get("exercises", []):
            sets = exercise.get("sets", [])
            if not sets:
                continue
            machine = exercise.get("machine") or exercise.get("exercise") or exercise.get("name")
            if not machine:
                continue
            valid_e1rms = []
            for s in sets:
                weight = _positive_float((s or {}).get("weight_lbs"))
                reps = _positive_float((s or {}).get("reps"))
                if weight is None or reps is None:
                    continue
                valid_e1rms.append(calculate_e1rm(weight, reps))
            if not valid_e1rms:
                continue
            best_e1rm = max(valid_e1rms)
            if machine not in exercise_history:
                exercise_history[machine] = []
            exercise_history[machine].append({
                "date": workout.get("date") or "",
                "e1rm": best_e1rm
            })

    results = {}
    for exercise, history in exercise_history.items():
        if len(history) < 2:
            results[exercise] = {
                "status": "On Track",
                "current_e1rm": round(history[-1]["e1rm"], 1) if history else 0,
                "peak_e1rm": round(history[-1]["e1rm"], 1) if history else 0,
                "trend_pct": 0,
                "history": history
            }
            continue

        current_e1rm = history[-1]["e1rm"]
        peak_e1rm = max(h["e1rm"] for h in history)
        recent_3 = [h["e1rm"] for h in history[-3:]]

        if current_e1rm < peak_e1rm * 0.95:
            status = "Regression"
        elif len(recent_3) >= 3 and recent_3[-1] <= recent_3[0]:
            status = "Plateau"
        else:
            status = "On Track"

        # Calculate trend from peak (for status-consistent display)
        if peak_e1rm > 0:
            trend_pct = ((current_e1rm - peak_e1rm) / peak_e1rm) * 100
        else:
            trend_pct = 0

        # Also track overall progress from first workout
        if history[0]["e1rm"] > 0:
            total_progress_pct = ((current_e1rm - history[0]["e1rm"]) / history[0]["e1rm"]) * 100
        else:
            total_progress_pct = 0

        results[exercise] = {
            "status": status,
            "current_e1rm": round(current_e1rm, 1),
            "peak_e1rm": round(peak_e1rm, 1),
            "trend_pct": round(trend_pct, 1),  # vs peak (for status consistency)
            "total_progress_pct": round(total_progress_pct, 1),  # vs first workout
            "history": history
        }

    return results


def calculate_volume(workouts, weeks=4):
    """Calculate volume load per muscle group over recent weeks."""
    workouts = _recommendation_workouts_with_apple_health(workouts, days=weeks * 7)
    cutoff = datetime.now() - timedelta(days=weeks * 7)
    muscle_volume = {}

    for workout in workouts:
        # Be resilient to partial/legacy entries
        date_s = workout.get("date")
        if not date_s:
            continue
        try:
            workout_date = datetime.strptime(date_s, '%Y-%m-%d')
        except Exception:
            continue
        if workout_date < cutoff:
            continue

        for exercise in workout.get("exercises", []) or []:
            if not isinstance(exercise, dict):
                continue
            muscle = (exercise.get("muscle_group") or "unknown")
            if muscle not in muscle_volume:
                muscle_volume[muscle] = {"sets": 0, "volume_load": 0, "last_trained": date_s}

            for s in exercise.get("sets", []) or []:
                if not isinstance(s, dict):
                    continue
                w = s.get("weight_lbs") or 0
                r = s.get("reps") or 0
                try:
                    w = float(w)
                    r = float(r)
                except Exception:
                    w, r = 0, 0
                muscle_volume[muscle]["sets"] += 1
                muscle_volume[muscle]["volume_load"] += w * r

            muscle_volume[muscle]["last_trained"] = max(muscle_volume[muscle]["last_trained"], date_s)

    for muscle, data in muscle_volume.items():
        sets = data["sets"]
        if sets < 6:
            data["status"] = "Below MEV"
            data["status_color"] = "red"
        elif sets <= 10:
            data["status"] = "Minimum Effective"
            data["status_color"] = "yellow"
        elif sets <= 15:
            data["status"] = "Optimal"
            data["status_color"] = "green"
        elif sets <= 20:
            data["status"] = "High Volume"
            data["status_color"] = "yellow"
        else:
            data["status"] = "Above MRV"
            data["status_color"] = "red"

    return muscle_volume


def get_cardio_muscle_impact(cardio_data, muscle, days=2):
    """Calculate cardio impact on a specific muscle group."""
    # Mapping of cardio activities to affected muscles with fatigue factors
    CARDIO_MUSCLE_IMPACT = {
        "stairmaster": {"quads": 2, "glutes": 2, "calves": 1.5, "hamstrings": 1},
        "treadmill": {"quads": 1.5, "hamstrings": 1.5, "calves": 2, "glutes": 1},
        "running": {"quads": 1.5, "hamstrings": 1.5, "calves": 2, "glutes": 1},
        "basketball": {"quads": 2, "calves": 2, "core": 1, "hamstrings": 1},
        "cycling": {"quads": 2, "hamstrings": 1.5, "calves": 1, "glutes": 1},
        "elliptical": {"quads": 1, "glutes": 1, "hamstrings": 1, "calves": 0.5},
        "rowing": {"back": 2, "biceps": 1.5, "core": 1.5, "quads": 1},
        "swimming": {"back": 1.5, "shoulders": 1.5, "core": 1, "triceps": 1},
        "walking": {"quads": 1, "hamstrings": 0.5, "calves": 1, "glutes": 0.5}
    }
    cutoff = datetime.now() - timedelta(days=days)
    total_impact = 0

    for session in cardio_data:
        try:
            session_date = datetime.strptime(session.get("date", ""), '%Y-%m-%d')
            if session_date < cutoff:
                continue

            activity = _canonical_cardio_fatigue_type(session.get("activity_type", ""))
            duration = _fit95_number(session.get("duration_minutes") or session.get("duration_min"))
            intensity = _fit95_number(session.get("intensity"), 5)

            if activity in CARDIO_MUSCLE_IMPACT:
                muscle_factor = CARDIO_MUSCLE_IMPACT[activity].get(muscle, 0)
                # Scale impact by duration (30 min = baseline) and intensity
                duration_factor = duration / 30
                intensity_factor = intensity / 5  # 5 = baseline intensity
                total_impact += muscle_factor * duration_factor * intensity_factor
        except (ValueError, TypeError):
            continue

    return min(3, total_impact)  # Cap at 3 points of fatigue


def _parse_soreness_timestamp(entry):
    """Best-effort parse of soreness entry timestamp.

    Supports:
      - created_at (ISO)
      - timestamp (ISO)
      - date (YYYY-MM-DD) (assumed local noon)
    """
    ts = entry.get("created_at") or entry.get("timestamp")
    if ts:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

    d = entry.get("date")
    if d:
        try:
            # Assume noon local time to avoid accidental exclusion for "today" logs
            return datetime.strptime(d, "%Y-%m-%d") + timedelta(hours=12)
        except Exception:
            return None
    return None


def filter_recent_soreness(soreness_data, hours=24):
    cutoff = datetime.now() - timedelta(hours=hours)
    recent = []
    for s in soreness_data or []:
        ts = _parse_soreness_timestamp(s)
        if ts and ts >= cutoff:
            recent.append(s)
    return recent


def _parse_workout_completion_timestamp(workout):
    """Best-effort parse of when a workout was completed, in server-local time.

    Prefers `created_at` (precise ISO timestamp) so multiple workouts on the same
    day still sort correctly. Delegates to `_parse_iso_to_local_datetime` so a
    `Z`/offset timestamp from sync/import is converted to local before we strip
    tzinfo — otherwise `hours_ago` could be off by the server's UTC offset and
    flip recent completions in/out of the window incorrectly. Falls back to
    `date` at noon local.
    """
    ts = workout.get("created_at")
    if ts:
        dt = _parse_iso_to_local_datetime(str(ts))
        if dt is not None:
            return dt
    d = workout.get("date")
    if d:
        try:
            return datetime.strptime(d, "%Y-%m-%d") + timedelta(hours=12)
        except Exception:
            return None
    return None


def summarize_recent_completion(workouts, hours=24):
    """Summarize the most-recent completed workout within `hours`.

    Returns ``None`` when no completion falls inside the window. Otherwise
    returns a dict the recommendation engine uses to dampen intensity and
    flag freshly-trained muscles in the avoid list.
    """
    cutoff = datetime.now() - timedelta(hours=hours)
    candidates = []
    for w in workouts or []:
        if not isinstance(w, dict):
            continue
        ts = _parse_workout_completion_timestamp(w)
        if ts and ts >= cutoff:
            candidates.append((ts, w))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    ts, w = candidates[0]

    sets_by_muscle = {}
    total_sets = 0
    total_volume = 0.0
    for ex in w.get("exercises") or []:
        if not isinstance(ex, dict):
            continue
        muscle = (ex.get("muscle_group") or "").strip().lower()
        sets = ex.get("sets") or []
        n_sets = len(sets)
        total_sets += n_sets
        if muscle and muscle != "unknown":
            sets_by_muscle[muscle] = sets_by_muscle.get(muscle, 0) + n_sets
        for s in sets:
            if not isinstance(s, dict):
                continue
            try:
                total_volume += float(s.get("weight_lbs") or 0) * float(s.get("reps") or 0)
            except Exception:
                continue
    muscles_trained = sorted(sets_by_muscle.items(), key=lambda kv: (-kv[1], kv[0]))

    try:
        overall_fatigue = int(w.get("overall_fatigue")) if w.get("overall_fatigue") is not None else None
    except Exception:
        overall_fatigue = None

    hours_ago = round((datetime.now() - ts).total_seconds() / 3600, 1)
    return {
        "workout_id": w.get("id"),
        "date": w.get("date"),
        "created_at": w.get("created_at"),
        "hours_ago": hours_ago,
        "overall_fatigue": overall_fatigue,
        "session_focus": w.get("session_type"),
        "total_sets": total_sets,
        "total_volume_lbs": round(total_volume),
        "muscles_trained": [{"muscle": m, "sets": n} for m, n in muscles_trained],
    }


def _positive_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _nonnegative_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _planned_targets_from_exercise(exercise):
    if not isinstance(exercise, dict):
        return {}
    target_weight = _positive_number(
        exercise.get("planned_target_weight")
        or exercise.get("target_weight")
        or exercise.get("target_weight_lbs")
        or exercise.get("recommended_weight")
    )
    target_reps = _positive_number(
        exercise.get("planned_target_reps")
        or exercise.get("target_reps")
        or exercise.get("reps")
        or exercise.get("recommended_reps")
    )
    target_sets = _positive_number(
        exercise.get("planned_target_sets")
        or exercise.get("target_sets")
        or exercise.get("recommended_sets")
        or exercise.get("sets")
    )
    planned_targets = {}
    if target_weight is not None:
        planned_targets["planned_target_weight"] = target_weight
    if target_reps is not None:
        planned_targets["planned_target_reps"] = target_reps
    if target_sets is not None:
        planned_targets["planned_target_sets"] = target_sets
    return planned_targets


def _completed_set_rows(sets):
    return [
        row
        for row in (sets or [])
        if isinstance(row, dict)
        and row.get("completed") is not False
        and row.get("done") is not False
    ]


def _recent_muscle_performance_debt(muscle, workouts, hours=72):
    """Penalty for recent sessions where planned reps/sets were not completed."""
    cutoff = datetime.now() - timedelta(hours=hours)
    muscle = (muscle or "").strip().lower()
    if not muscle:
        return {"debt": 0, "reason": None}

    debt = 0
    reason = None
    for workout in workouts or []:
        if not isinstance(workout, dict):
            continue
        ts = _parse_workout_completion_timestamp(workout)
        if ts is None or ts < cutoff:
            continue
        for exercise in workout.get("exercises") or []:
            if not isinstance(exercise, dict):
                continue
            if (exercise.get("muscle_group") or "").strip().lower() != muscle:
                continue
            sets = _completed_set_rows(exercise.get("sets"))
            target_sets = _positive_number(
                exercise.get("planned_target_sets")
                or exercise.get("target_sets")
                or exercise.get("recommended_sets")
            )
            target_reps = _positive_number(
                exercise.get("planned_target_reps")
                or exercise.get("target_reps")
                or exercise.get("recommended_reps")
            )

            if target_sets is not None and len(sets) < int(round(target_sets)):
                missed_sets = int(round(target_sets)) - len(sets)
                candidate = 2 if missed_sets >= 2 else 1
                if candidate > debt:
                    debt = candidate
                    reason = f"missed {missed_sets} planned set{'s' if missed_sets != 1 else ''}"

            if target_reps is None:
                continue
            rep_values = []
            for row in sets:
                reps = _nonnegative_number(row.get("reps"))
                if reps is not None:
                    rep_values.append(reps)
            if not rep_values:
                continue
            min_reps = min(rep_values)
            rep_ratio = min_reps / target_reps
            if rep_ratio < 0.7:
                candidate = 3
            elif rep_ratio < 0.9:
                candidate = 2
            elif rep_ratio < 1:
                candidate = 1
            else:
                candidate = 0
            if candidate > debt:
                missed_reps = max(0, int(round(target_reps - min_reps)))
                debt = candidate
                reason = f"missed {missed_reps} planned rep{'s' if missed_reps != 1 else ''}"

    return {"debt": debt, "reason": reason}


def _muscles_trained_for_workout(workout):
    muscles = set()
    for entry in workout.get("muscles_trained") or []:
        if isinstance(entry, dict):
            muscle = entry.get("muscle")
        else:
            muscle = entry
        if isinstance(muscle, str) and muscle.strip().lower() != "unknown":
            muscles.add(muscle.strip().lower())

    if muscles:
        return muscles

    for exercise in workout.get("exercises") or []:
        if not isinstance(exercise, dict):
            continue
        muscle = (exercise.get("muscle_group") or "").strip().lower()
        if not muscle or muscle == "unknown":
            continue
        if _completed_set_rows(exercise.get("sets")):
            muscles.add(muscle)
    return muscles


def _recent_workout_fatigue_debt(muscle, workouts, hours=48):
    cutoff = datetime.now() - timedelta(hours=hours)
    muscle = (muscle or "").strip().lower()
    if not muscle:
        return 0

    debt = 0
    for workout in workouts or []:
        if not isinstance(workout, dict):
            continue
        ts = _parse_workout_completion_timestamp(workout)
        if ts is None or ts < cutoff:
            continue
        if muscle not in _muscles_trained_for_workout(workout):
            continue
        try:
            overall_fatigue = int(workout.get("overall_fatigue") or 0)
        except (TypeError, ValueError):
            overall_fatigue = 0
        if overall_fatigue >= 8:
            candidate = 3
        elif overall_fatigue >= 6:
            candidate = 2
        elif overall_fatigue >= 4:
            candidate = 1
        else:
            candidate = 0
        debt = max(debt, candidate)
    return debt


def get_readiness_score(muscle, soreness_data, volume_data, cardio_data=None, workouts=None):
    """Calculate readiness score for a muscle group.

    Note: only soreness entries from the last 24h are considered (time-decay).
    """
    if cardio_data is None:
        cardio_data = []
    cardio_data = _cardio_data_with_apple_health(cardio_data, workouts)

    recent_soreness = filter_recent_soreness(soreness_data, hours=24)
    muscle_soreness = [s for s in recent_soreness if s.get("muscle") == muscle]
    soreness_level = muscle_soreness[-1].get("soreness_level", 0) if muscle_soreness else 0

    last_trained = volume_data.get(muscle, {}).get("last_trained")
    recovery_debt = 0
    if last_trained:
        days_since = (datetime.now() - datetime.strptime(last_trained, '%Y-%m-%d')).days
        if days_since < 2:
            recovery_debt = 2

    # Add cardio impact to fatigue calculation
    cardio_fatigue = get_cardio_muscle_impact(cardio_data, muscle)
    performance = _recent_muscle_performance_debt(muscle, WORKOUTS if workouts is None else workouts)
    performance_debt = performance["debt"]
    fatigue_debt = _recent_workout_fatigue_debt(muscle, WORKOUTS if workouts is None else workouts)

    readiness = 10 - soreness_level - recovery_debt - cardio_fatigue - performance_debt - fatigue_debt

    if readiness < 5:
        recommendation = "Skip or reduced volume"
        color = "red"
    elif readiness <= 7:
        recommendation = "Proceed with caution"
        color = "yellow"
    else:
        recommendation = "Full capacity"
        color = "green"
    if performance_debt and performance.get("reason"):
        recommendation = f"{recommendation}: recent workout {performance['reason']}"

    return {
        "score": max(0, readiness),  # Don't go below 0
        "soreness": soreness_level,
        "recovery_debt": recovery_debt,
        "fatigue_debt": fatigue_debt,
        "performance_debt": performance_debt,
        "performance_debt_reason": performance.get("reason"),
        "cardio_fatigue": round(cardio_fatigue, 1),
        "recommendation": recommendation,
        "color": color
    }


def _get_oura_readiness_today(include_open_wearables=True):
    today_s = _today_str()
    cached = get_oura_daily(OURA_DB_FILE, today_s)
    readiness = None
    if cached:
        readiness = cached.get("readiness_score")
        try:
            readiness = int(readiness)
        except Exception:
            readiness = None

    ow_sleep = None
    if include_open_wearables:
        try:
            ow_data = fetch_open_wearables_data()
            ow_sleep = _extract_open_wearables_sleep(ow_data.get("sleep"))
        except Exception:
            ow_sleep = None

    if not ow_sleep or not ow_sleep.get("recent"):
        return readiness

    duration_min = ow_sleep.get("duration_min")
    avg_hr = ow_sleep.get("avg_hr")

    def _duration_adjust(d_min, base_scale):
        if d_min is None:
            return 0
        if d_min < 360:
            return -5 * base_scale
        if d_min < 420:
            return -3 * base_scale
        if d_min >= 540:
            return 3 * base_scale
        return 0

    def _hr_adjust(hr, base_scale):
        if hr is None:
            return 0
        if hr >= 75:
            return -4 * base_scale
        if hr >= 70:
            return -2 * base_scale
        if hr <= 55:
            return 2 * base_scale
        return 0

    if readiness is None:
        base = 70
        adj = _duration_adjust(duration_min, 1.5) + _hr_adjust(avg_hr, 1.5)
        return int(max(0, min(100, round(base + adj))))

    adj = _duration_adjust(duration_min, 1.0) + _hr_adjust(avg_hr, 1.0)
    return int(max(0, min(100, round(readiness + adj))))


def _equipment_allowed(exercise, preference: str):
    equipment = exercise.get("equipment")
    if preference == "all":
        return True
    if preference == "machines_and_cables":
        return equipment in ("machine", "cable")
    if preference == "machines_only":
        return equipment == "machine"
    return True


def _exercise_user_allowed(exercise, settings=None):
    settings = settings or USER_SETTINGS
    excluded = {
        str(name).strip().lower()
        for name in settings.get("excluded_exercises", [])
        if str(name).strip()
    }
    name = (exercise.get("name") or "").strip().lower()
    if name in excluded:
        return False
    return True


def _exercise_brand_preference_rank(exercise, settings=None):
    settings = settings or USER_SETTINGS
    preferred = {
        str(brand).strip().lower()
        for brand in settings.get("preferred_equipment_brands", [])
        if str(brand).strip()
    }
    if not preferred:
        return 0
    brands = {
        str(brand).strip().lower()
        for brand in exercise.get("equipment_brands", [])
        if str(brand).strip()
    }
    return 0 if preferred.intersection(brands) else 1


def _filtered_exercise_library(preference: str):
    matches = [
        ex
        for ex in EXERCISE_LIBRARY
        if _equipment_allowed(ex, preference) and _exercise_user_allowed(ex)
    ]
    return sorted(
        matches,
        key=lambda ex: (
            _exercise_brand_preference_rank(ex),
            not bool(ex.get("compound")),
            ex.get("name", ""),
        ),
    )


def _same_exercise_definition(left, right):
    if not left or not right:
        return False
    return _normalize_exercise_name(left.get("name")) == _normalize_exercise_name(right.get("name"))


def _swap_resolution_candidates(old_ex, equipment_pref):
    old_muscle = (old_ex.get("muscle") or "").lower()
    old_definition = _resolve_exercise_definition(old_ex.get("exercise"))
    candidates = []
    for exercise in _filtered_exercise_library(equipment_pref):
        if exercise.get("muscle") != old_muscle:
            continue
        if old_definition and _same_exercise_definition(exercise, old_definition):
            continue
        candidates.append(exercise)
    return candidates


def _swap_candidate_token_sets(exercise):
    values = [exercise.get("name")]
    values.extend(exercise.get("aliases") or [])
    token_sets = []
    for value in values:
        tokens = _swap_match_tokens(value)
        if tokens:
            token_sets.append(tokens)
    return token_sets


def _swap_match_tokens(name):
    tokens = set()
    for token in _exercise_name_tokens(name):
        tokens.add(token)
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            tokens.add(token[:-1])
    return tokens


_AMBIGUOUS_SWAP_TOKENS = {"press", "row", "curl"}


def _is_ambiguous_single_swap_token(typed_name):
    typed_base_tokens = _exercise_name_tokens(typed_name)
    return len(typed_base_tokens) == 1 and bool(_swap_match_tokens(typed_name).intersection(_AMBIGUOUS_SWAP_TOKENS))


def _deterministic_swap_candidate(typed_name, candidates):
    if _is_ambiguous_single_swap_token(typed_name):
        return None
    typed_tokens = _swap_match_tokens(typed_name)
    if not typed_tokens:
        return None

    scored = []
    for exercise in candidates:
        best = None
        for candidate_tokens in _swap_candidate_token_sets(exercise):
            shared = typed_tokens.intersection(candidate_tokens)
            if not shared:
                continue
            is_superset = typed_tokens.issubset(candidate_tokens)
            coverage = len(shared) / len(typed_tokens)
            candidate_coverage = len(shared) / len(candidate_tokens)
            score = (100 if is_superset else 0) + (coverage * 20) + (candidate_coverage * 5)
            token_score = {
                "score": score,
                "shared": shared,
                "is_superset": is_superset,
            }
            if not best or token_score["score"] > best["score"]:
                best = token_score
        if not best:
            continue
        if best["is_superset"] or len(best["shared"]) >= min(2, len(typed_tokens)):
            scored.append(
                {
                    "exercise": exercise,
                    "score": best["score"],
                    "brand_rank": _exercise_brand_preference_rank(exercise),
                }
            )

    if not scored:
        return None

    scored.sort(key=lambda item: (-item["score"], item["brand_rank"], item["exercise"].get("name", "")))
    return scored[0]["exercise"]


def _resolve_custom_swap_exercise(typed_name, old_ex, equipment_pref):
    candidates = _swap_resolution_candidates(old_ex, equipment_pref)
    deterministic = _deterministic_swap_candidate(typed_name, candidates)
    if deterministic:
        return deterministic
    if _is_ambiguous_single_swap_token(typed_name):
        return None

    if not _lm_studio or not hasattr(_lm_studio, "resolve_swap_candidate") or not candidates:
        return None

    candidate_payload = [
        {
            "name": exercise.get("name"),
            "equipment": exercise.get("equipment"),
            "aliases": exercise.get("aliases") or [],
            "compound": bool(exercise.get("compound")),
        }
        for exercise in candidates
    ]
    try:
        resolution = _lm_studio.resolve_swap_candidate(
            typed_name,
            current_exercise=old_ex.get("exercise") or "",
            target_muscle=(old_ex.get("muscle") or "").lower(),
            candidates=candidate_payload,
        )
    except _lm_studio.LmStudioError as exc:
        print(f"WARN: swap resolver LLM failed: {exc}")
        return None

    canonical_name = resolution.get("canonical_name") if isinstance(resolution, dict) else None
    if not canonical_name:
        return None

    wanted = _normalize_exercise_name(canonical_name)
    for exercise in candidates:
        if _normalize_exercise_name(exercise.get("name")) == wanted:
            return exercise
    return None


def _adjust_target_phrase(constraint):
    text = re.sub(r"\s+", " ", str(constraint or "").strip())
    if not text:
        return ""
    if _adjust_phrase_has_negation(text):
        return ""
    patterns = [
        r"(?:^|\b)replace(?:\s+.+?)?\s+with\s+(.+)$",
        r"(?:^|\b)swap(?:\s+.+?)?\s+(?:to|with|for)\s+(.+)$",
        r"(?:^|\b)use\s+(.+?)\s+instead\s+of\s+.+$",
        r"(?:^|\b)do\s+(.+?)\s+instead\s+of\s+.+$",
        r"(?:^|\b)do\s+(.+?)\s+instead$",
        r"(?:^|\b)use\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            target = _clean_adjust_target_phrase(match.group(1))
            return "" if _adjust_phrase_has_negation(target) else target
    return _clean_adjust_target_phrase(text)


def _adjust_source_phrase(constraint):
    text = re.sub(r"\s+", " ", str(constraint or "").strip())
    if not text or _adjust_phrase_has_negation(text):
        return ""
    patterns = [
        r"(?:^|\b)replace(?:\s+it)?\s+with\s+.+?\s+instead\s+of\s+(.+)$",
        r"(?:^|\b)swap(?:\s+it)?\s+(?:to|with|for)\s+.+?\s+instead\s+of\s+(.+)$",
        r"(?:^|\b)replace\s+(.+?)\s+with\s+.+$",
        r"(?:^|\b)swap\s+(.+?)\s+(?:to|with|for)\s+.+$",
        r"(?:^|\b)use\s+.+?\s+instead\s+of\s+(.+)$",
        r"(?:^|\b)do\s+.+?\s+instead\s+of\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            source = _clean_adjust_target_phrase(match.group(1))
            return "" if _adjust_phrase_has_negation(source) else source
    return ""


def _adjust_phrase_has_negation(text):
    return bool(re.search(
        r"(?:^|\b)(?:do\s+not|don't|dont|not|no|avoid|remove|skip|without)\b",
        str(text or ""),
        flags=re.IGNORECASE,
    ))


def _clean_adjust_target_phrase(phrase):
    phrase = re.sub(r"[.!?]+$", "", str(phrase or "").strip())
    phrase = re.sub(r"\s+instead\s+of\s+.+$", "", phrase, flags=re.IGNORECASE).strip()
    phrase = re.sub(r"\s+instead$", "", phrase, flags=re.IGNORECASE).strip()
    phrase = re.sub(r"^(?:a|an|the)\s+", "", phrase, flags=re.IGNORECASE).strip()
    return phrase[:128]


def _resolve_adjust_target_exercise(constraint, equipment_pref):
    phrase = _adjust_target_phrase(constraint)
    if not phrase:
        return None

    candidates = _filtered_exercise_library(equipment_pref)
    wanted = _normalize_exercise_name(phrase)
    for exercise in candidates:
        names = [exercise.get("name")]
        names.extend(exercise.get("aliases") or [])
        if any(_normalize_exercise_name(name) == wanted for name in names):
            return exercise

    if len(_exercise_name_tokens(phrase)) < 2:
        return None

    return _deterministic_swap_candidate(phrase, candidates)


def _plan_exercise_definition(plan_ex):
    resolved = _resolve_exercise_definition(
        plan_ex.get("exercise") or plan_ex.get("machine") or plan_ex.get("name")
    )
    if resolved:
        return resolved
    return {
        "name": plan_ex.get("exercise") or plan_ex.get("machine") or plan_ex.get("name") or "",
        "muscle": plan_ex.get("muscle") or plan_ex.get("muscle_group") or "",
        "compound": bool(plan_ex.get("is_compound")),
        "movement_patterns": [],
    }


def _select_adjust_replacement_slot(exercises, target_exercise):
    if not exercises or not target_exercise:
        return None

    target_muscle = (target_exercise.get("muscle") or "").lower()
    target_patterns = _exercise_movement_patterns(target_exercise)
    scored = []
    for idx, plan_ex in enumerate(exercises):
        current = _plan_exercise_definition(plan_ex)
        if _same_exercise_definition(current, target_exercise):
            continue
        current_muscle = (current.get("muscle") or "").lower()
        current_patterns = _exercise_movement_patterns(current)
        shared_patterns = target_patterns.intersection(current_patterns)
        score = 0
        if current_muscle == target_muscle and shared_patterns:
            score = 100 + len(shared_patterns)
        elif current_muscle == target_muscle:
            score = 80
        elif shared_patterns:
            score = 50 + len(shared_patterns)
        else:
            continue
        scored.append({
            "idx": idx,
            "score": score,
            "current": current,
            "shared_patterns": sorted(shared_patterns),
        })

    if not scored:
        return None

    # Deterministic tie-break: later plan slots are treated as lower priority.
    scored.sort(key=lambda item: (-item["score"], item["idx"]))
    return scored[0]


def _build_deterministic_adjust_swap(constraint, recommendation, equipment_pref):
    target = _resolve_adjust_target_exercise(constraint, equipment_pref)
    if not target:
        return None
    if not _equipment_allowed(target, equipment_pref) or not _exercise_user_allowed(target):
        return None

    exercises = list((recommendation or {}).get("exercises") or [])
    source_phrase = _adjust_source_phrase(constraint)
    if source_phrase:
        wanted_source = _normalize_exercise_name(source_phrase)
        source_definition = _resolve_exercise_definition(source_phrase)
        source_idx = None
        exact_source_matches = []
        partial_source_matches = []
        for idx, plan_ex in enumerate(exercises):
            if source_definition and _same_exercise_definition(_plan_exercise_definition(plan_ex), source_definition):
                source_idx = idx
                break
            source_name = plan_ex.get("exercise") or plan_ex.get("machine") or plan_ex.get("name") or ""
            source_norm = _normalize_exercise_name(source_name)
            if source_norm == wanted_source:
                exact_source_matches.append(idx)
            elif wanted_source and wanted_source in source_norm:
                partial_source_matches.append(idx)
        if source_idx is None and not source_definition:
            if len(exact_source_matches) == 1:
                source_idx = exact_source_matches[0]
            elif not exact_source_matches and len(partial_source_matches) == 1:
                source_idx = partial_source_matches[0]
        if source_idx is None:
            return None
        if _same_exercise_definition(_plan_exercise_definition(exercises[source_idx]), target):
            return None
        slot = {"idx": source_idx}
    else:
        slot = _select_adjust_replacement_slot(exercises, target)
        if not slot:
            return None

    old_ex = exercises[slot["idx"]]
    old_name = old_ex.get("exercise") or old_ex.get("machine") or old_ex.get("name") or ""
    if not old_name:
        return None

    return {
        "replace_exercise": old_name,
        "replace_index": slot["idx"],
        "target_muscle": target.get("muscle"),
        "target_exercise": target.get("name"),
        "reason": "deterministic movement request",
        "_deterministic": True,
    }


def _merge_deterministic_adjust_swap(intent, deterministic_swap):
    merged = dict(intent or {})
    if deterministic_swap:
        merged["swap"] = [deterministic_swap]
    return merged


def _deterministic_adjust_payload(
    recommendation,
    constraint,
    deterministic_swap,
    goal_params,
    meso_week,
    meso_plan,
    oura_readiness,
    equipment_pref,
    reason,
):
    patched = json.loads(json.dumps(recommendation, default=str))
    intent = _merge_deterministic_adjust_swap({}, deterministic_swap)
    patched, applied_notes = _apply_intent_patch(
        patched, intent, goal_params, meso_week, meso_plan, oura_readiness, equipment_pref
    )
    result_kind = "changed" if applied_notes else "unchanged"
    return {
        "status": "ok",
        "result_kind": result_kind,
        "recommendation": patched,
        "summary": "Applied the recognized exercise request without the local coach model.",
        "applied_notes": applied_notes,
        "constraint": constraint,
        "meta": {"mode": "deterministic_fallback", "reason": reason},
        "cache_hit": False,
    }


def _build_exercise_entry(
    exercise_name,
    muscle,
    is_compound,
    goal_params,
    meso_week,
    volume_multiplier,
    oura_readiness,
    volume_data,
    soreness_data,
    progression,
    workouts,
    time_per_set,
):
    min_reps, max_reps = goal_params["rep_range"]
    target_sets = goal_params["sets_per_exercise"]
    intensity_pct = goal_params["intensity_pct"] / 100
    rest_time = goal_params["rest_minutes"]

    ex_progression = progression.get(exercise_name, {})
    status = ex_progression.get("status", "On Track")
    readiness_index, days_since, soreness_level = _muscle_readiness_index(
        muscle, soreness_data, volume_data, oura_readiness
    )

    load_source = _select_recommendation_e1rm(exercise_name, ex_progression, progression)
    current_e1rm = load_source["e1rm"]
    status = load_source["status"]
    _, exercise = _lookup_by_exercise_name(EXERCISE_LOOKUP, exercise_name)
    is_unweighted_bodyweight = (
        (exercise or {}).get("equipment") == "bodyweight"
        and load_source["source"] == "hardcoded"
    )

    if status == "Baseline":
        target_weight = round(current_e1rm * intensity_pct * 0.9, 0)
        rationale = f"{goal_params['name']}: Starting weight — log your first session to calibrate"
        sets = target_sets
    elif status == "Calibrated Baseline":
        target_weight = round(current_e1rm * intensity_pct, 0)
        rationale = f"{goal_params['name']}: Calibrated from saved baseline"
        sets = target_sets
    elif status == "Similar History":
        target_weight = round(current_e1rm * intensity_pct * 0.9, 0)
        source_name = load_source.get("inferred_from") or "similar history"
        rationale = f"{goal_params['name']}: Conservative estimate from {source_name}"
        sets = target_sets
    elif status == "On Track":
        target_weight = round(current_e1rm * intensity_pct + 5, 0)
        rationale = f"{goal_params['name']}: +5 lbs progression"
        sets = target_sets
    elif status == "Plateau":
        target_weight = round(current_e1rm * intensity_pct, 0)
        rationale = f"{goal_params['name']}: Adding volume to break plateau"
        sets = target_sets + 1
    else:
        target_weight = round(current_e1rm * (intensity_pct * 0.85), 0)
        rationale = f"{goal_params['name']}: Deload - focus on form"
        sets = target_sets - 1

    target_reps = (min_reps + max_reps) // 2
    rpe_target = _calculate_rpe_target(is_compound, readiness_index, oura_readiness, soreness_level, days_since, meso_week)
    volume_adjusted_sets = max(1, round(sets * volume_multiplier))

    last_perf = _get_last_exercise_performance(workouts, exercise_name)
    if not is_unweighted_bodyweight:
        target_weight, target_reps, overload_note = _apply_progressive_overload(
            target_weight, target_reps, rpe_target, is_compound, last_perf
        )
        rationale = f"{rationale} · {overload_note}"

    if exercise_name == "Plank":
        target_weight = 0
        target_reps = 45
        rationale = f"{goal_params['name']}: Timed core stability"

    if is_unweighted_bodyweight:
        target_weight = 0
        rationale = f"{goal_params['name']}: Bodyweight — prioritize controlled reps, form, and effort"

    rest_label = f"{rest_time} min"
    entry = {
        "exercise": exercise_name,
        "muscle": muscle,
        "is_compound": is_compound,
        "target_weight": 0 if is_unweighted_bodyweight else max(5, target_weight),
        "target_reps": target_reps,
        "target_sets": max(2, volume_adjusted_sets),
        "rationale": rationale,
        "rest_minutes": rest_time,
        "rest_label": rest_label,
        "rpe_target": rpe_target,
        "estimated_time": round(max(2, volume_adjusted_sets) * time_per_set),
        "days_since_trained": days_since,
        "soreness": soreness_level,
        "oura_readiness": oura_readiness if oura_readiness is not None else None,
        "load_source": load_source["source"],
        "load_e1rm": round(current_e1rm, 1),
        "load_source_detail": load_source["detail"],
    }
    if is_unweighted_bodyweight:
        entry["bodyweight"] = True
    if load_source.get("inferred_from"):
        entry["load_inference"] = {
            "source_exercise": load_source["inferred_from"],
            "confidence": load_source.get("inference_confidence", "low"),
            "message": f"Estimated from {load_source['inferred_from']} history; adjust after first set.",
        }
    return entry


def _get_latest_soreness_for_muscle(muscle, soreness_data, hours=72):
    recent = filter_recent_soreness(soreness_data, hours=hours)
    if not recent:
        return 0
    muscle_entries = [s for s in recent if s.get("muscle") == muscle]
    if not muscle_entries:
        return 0
    # Prefer most recent by timestamp if present
    muscle_entries.sort(key=_parse_soreness_timestamp, reverse=True)
    return int(muscle_entries[0].get("soreness_level") or 0)


def _get_days_since_trained(muscle, volume_data):
    last_trained = volume_data.get(muscle, {}).get("last_trained")
    if not last_trained:
        return 7
    try:
        return max(0, (datetime.now() - datetime.strptime(last_trained, '%Y-%m-%d')).days)
    except Exception:
        return 7


def _muscle_readiness_index(muscle, soreness_data, volume_data, oura_readiness):
    days_since = _get_days_since_trained(muscle, volume_data)
    soreness_level = _get_latest_soreness_for_muscle(muscle, soreness_data, hours=72)
    days_score = min(days_since / 4, 1)
    soreness_score = max(0, 1 - (soreness_level / 10))
    if oura_readiness is None:
        oura_score = 0.5
    else:
        oura_score = max(0, min(1, oura_readiness / 100))
    readiness_index = (0.4 * oura_score) + (0.35 * days_score) + (0.25 * soreness_score)
    return readiness_index, days_since, soreness_level


def _get_mesocycle_week(workouts, sessions_per_week):
    if sessions_per_week <= 0:
        sessions_per_week = 3
    week_index = (len(workouts) // sessions_per_week) % 4
    return week_index + 1


MESOCYCLE_PLAN = {
    1: {"name": "Accumulation", "volume_multiplier": 1.0, "rpe_base": 7.0},
    2: {"name": "Overreach", "volume_multiplier": 1.2, "rpe_base": 7.5},
    3: {"name": "Intensification", "volume_multiplier": 0.8, "rpe_base": 8.5},
    4: {"name": "Deload", "volume_multiplier": 0.5, "rpe_base": 5.5},
}


def _calculate_rpe_target(is_compound, readiness_index, oura_readiness, soreness_level, days_since, meso_week):
    # Base ranges by exercise type
    type_min, type_max = (7.0, 9.0) if is_compound else (6.0, 8.0)

    # Mesocycle ranges
    if meso_week == 4:
        min_rpe, max_rpe = 5.0, 6.0
        target = min_rpe + (max_rpe - min_rpe) * readiness_index
        if oura_readiness is not None and oura_readiness < 60:
            target = min(target, 7.0)
        target = max(min_rpe, min(max_rpe, target))
        return round(target * 2) / 2

    if meso_week == 1:
        meso_min, meso_max = 6.5, 7.5
    elif meso_week == 2:
        meso_min, meso_max = 7.0, 8.0
    else:
        meso_min, meso_max = 8.0, 9.0

    min_rpe = max(type_min, meso_min)
    max_rpe = min(type_max, meso_max)

    target = min_rpe + (max_rpe - min_rpe) * readiness_index
    # Small adjustments for soreness and time since trained
    target -= max(0, soreness_level - 5) * 0.1
    target += max(0, days_since - 3) * 0.05

    if oura_readiness is not None:
        if oura_readiness < 60:
            target = min(target, 7.0)
        elif oura_readiness > 80 and is_compound and meso_week in (2, 3):
            max_rpe = max(max_rpe, 9.0)
        elif oura_readiness <= 80 and is_compound and meso_week != 4:
            target = min(target, 8.0)

    target = max(min_rpe, min(max_rpe, target))
    # Round to nearest 0.5
    return round(target * 2) / 2


def _get_last_exercise_performance(workouts, exercise_name):
    if not workouts:
        return None
    sorted_workouts = sorted(
        [w for w in workouts if w.get("date")],
        key=lambda x: x.get("date", ""),
        reverse=True
    )
    for w in sorted_workouts:
        for ex in w.get("exercises", []) or []:
            if ex.get("machine") != exercise_name:
                continue
            sets = ex.get("sets", []) or []
            if not sets:
                continue
            rpes = [s.get("rpe") for s in sets if s.get("rpe") is not None]
            avg_rpe = round(sum(rpes) / len(rpes), 2) if rpes else None
            min_reps = min((s.get("reps") or 0) for s in sets)
            max_weight = max((s.get("weight_lbs") or 0) for s in sets)
            best_e1rm = max(calculate_e1rm(s.get("weight_lbs", 0), s.get("reps", 0)) for s in sets)
            return {
                "date": w.get("date"),
                "avg_rpe": avg_rpe,
                "min_reps": min_reps,
                "max_weight": max_weight,
                "best_e1rm": round(best_e1rm, 1),
                "sets": sets
            }
    return None


def _apply_progressive_overload(base_weight, base_reps, target_rpe, is_compound, last_perf):
    if last_perf is None:
        return base_weight, base_reps, "Baseline: no recent history"

    weight_step = 5 if is_compound else 2.5
    avg_rpe = last_perf.get("avg_rpe")
    min_reps = last_perf.get("min_reps", 0)
    completed_reps = min_reps >= base_reps

    if avg_rpe is None:
        return base_weight, base_reps, "Maintain load: RPE missing"

    if avg_rpe < target_rpe and completed_reps:
        return base_weight + weight_step, base_reps, f"Progression: +{weight_step} lbs"
    if abs(avg_rpe - target_rpe) <= 0.25 and completed_reps:
        return base_weight, base_reps + 1, "Progression: +1 rep"
    if avg_rpe > target_rpe + 0.25 or not completed_reps:
        adjusted = max(5, base_weight - weight_step)
        return adjusted, base_reps, "Auto-regulated: reduce load"

    return base_weight, base_reps, "Maintain load"


def generate_next_workout(
    workouts,
    soreness_data,
    goal=None,
    available_time=None,
    persist=False,
    training_recommendation=None,
    consume_cardio_rotation=True,
    include_open_wearables_readiness=True,
):
    """Generate optimal workout prescription based on training goal and available time.

    Args:
        persist: If True, store recommendation for adherence tracking. False for read-only views.
    """
    if goal is None:
        goal = USER_SETTINGS.get("training_goal", TrainingGoal.HYPERTROPHY.value)

    if available_time is None:
        available_time = USER_SETTINGS.get("available_time_minutes", 60)

    goal_params = GOAL_PARAMETERS.get(goal, GOAL_PARAMETERS[TrainingGoal.HYPERTROPHY.value])
    time_per_set = goal_params.get("time_per_set_minutes", 3)
    sessions_per_week = USER_SETTINGS.get("sessions_per_week_target", 3)
    meso_week = _get_mesocycle_week(workouts, sessions_per_week)
    meso_plan = MESOCYCLE_PLAN.get(meso_week, MESOCYCLE_PLAN[1])
    try:
        deload_status = detect_deload_need(workouts, soreness_data)
    except (KeyError, TypeError, ValueError):
        app.logger.warning(
            "deload detector could not evaluate legacy workout data; continuing without forced deload",
            exc_info=True,
        )
        deload_status = {"needed": False, "indicators": []}
    deload_forced = bool(deload_status.get("needed"))
    if deload_forced:
        meso_week = 4
        meso_plan = MESOCYCLE_PLAN[4]
    try:
        oura_readiness = _get_oura_readiness_today(
            include_open_wearables=include_open_wearables_readiness
        )
    except TypeError:
        # Several focused tests monkeypatch this hook with a no-arg lambda.
        # Preserve that test hook while production can skip slow OW reads.
        if getattr(_get_oura_readiness_today, "__name__", "") == "_get_oura_readiness_today":
            raise
        oura_readiness = _get_oura_readiness_today()
    equipment_pref = USER_SETTINGS.get("equipment_preference", "machines_only")
    base_cardio_rec = CARDIO_RECOMMENDATIONS.get(goal, CARDIO_RECOMMENDATIONS[TrainingGoal.HYPERTROPHY.value])
    cardio_rec = _choose_dynamic_cardio_recommendation(
        goal,
        base_cardio_rec,
        oura_readiness=oura_readiness,
        training_recommendation=training_recommendation,
        consume_rotation=consume_cardio_rotation,
        equipment_preference=equipment_pref,
    )

    # Calculate max exercises based on available time
    # Account for warmup (5 min) and cooldown (5 min)
    effective_time = available_time - 10
    sets_per_exercise = goal_params["sets_per_exercise"]
    volume_multiplier = meso_plan["volume_multiplier"]
    if oura_readiness is not None and oura_readiness < 60:
        volume_multiplier *= 0.8
    adjusted_sets_for_timing = max(2, round(sets_per_exercise * volume_multiplier))
    time_per_exercise = time_per_set * adjusted_sets_for_timing
    cardio_duration = int(cardio_rec.get("duration_minutes") or 0)
    minimum_resistance_time = 2 * time_per_exercise
    cardio_reservation = 0
    if cardio_rec.get("include_cardio") and cardio_duration >= 10:
        if effective_time - cardio_duration >= minimum_resistance_time:
            cardio_reservation = cardio_duration
        elif effective_time - 10 >= minimum_resistance_time:
            cardio_reservation = min(cardio_duration, effective_time - minimum_resistance_time)
    resistance_time_budget = max(minimum_resistance_time, effective_time - cardio_reservation)
    max_exercises = max(2, int(resistance_time_budget / time_per_exercise))

    volume_data = calculate_volume(workouts, weeks=4)
    muscle_groups = list(volume_data.keys()) or ["chest", "back", "quads", "shoulders"]
    default_muscles = ["chest", "back", "quads", "shoulders", "hamstrings", "glutes", "adductors", "biceps", "triceps", "core", "calves"]

    readiness_scores = {}
    for muscle in default_muscles:
        readiness_scores[muscle] = get_readiness_score(muscle, soreness_data, volume_data, CARDIO_DATA, workouts)

    available_muscles = [
        m
        for m, r in readiness_scores.items()
        if r["score"] >= 5 and not (r.get("performance_debt") and r["score"] <= 5)
    ]

    # If not enough muscles available, add default ones
    for m in default_muscles:
        if m in readiness_scores and (
            readiness_scores[m]["score"] < 5
            or (readiness_scores[m].get("performance_debt") and readiness_scores[m]["score"] <= 5)
        ):
            continue
        if m not in available_muscles and len(available_muscles) < max_exercises:
            available_muscles.append(m)

    progression = calculate_progression_status(workouts)

    # Multiple exercises per muscle — rotates based on what was done most recently
    exercise_pool = {}
    for ex in _filtered_exercise_library(equipment_pref):
        muscle = ex["muscle"]
        exercise_pool.setdefault(muscle, []).append((ex["name"], ex["compound"]))

    # Find last time each exercise was done to enable rotation
    recent_exercises = {}
    for w in workouts[:6]:  # Look back 6 sessions
        for ex in w.get("exercises", []):
            machine = ex.get("machine")
            if machine and machine not in recent_exercises:
                recent_exercises[machine] = w["date"]

    def pick_exercise(muscle):
        """Pick exercise for a muscle group, preferring least-recently-used."""
        options = exercise_pool.get(muscle, [])
        if not options:
            return None
        if len(options) == 1:
            return options[0]
        # Sort by last done date (ascending) — pick the one done longest ago
        def last_done(ex_tuple):
            return recent_exercises.get(ex_tuple[0], "0000-00-00")
        return sorted(options, key=last_done)[0]

    exercise_map = {muscle: pick_exercise(muscle) for muscle in exercise_pool if pick_exercise(muscle)}

    exercises = []
    # Prioritize compound exercises when time is limited
    sorted_muscles = sorted(available_muscles, key=lambda m: (
        0 if exercise_map.get(m, (None, False))[1] else 1,  # Compounds first
        readiness_scores.get(m, {}).get("score", 0) * -1     # Higher readiness
    ))

    for muscle in sorted_muscles[:max_exercises]:
        if muscle not in exercise_map:
            continue

        exercise_name, is_compound = exercise_map[muscle]
        exercises.append(_build_exercise_entry(
            exercise_name=exercise_name,
            muscle=muscle,
            is_compound=is_compound,
            goal_params=goal_params,
            meso_week=meso_week,
            volume_multiplier=volume_multiplier,
            oura_readiness=oura_readiness,
            volume_data=volume_data,
            soreness_data=soreness_data,
            progression=progression,
            workouts=workouts,
            time_per_set=time_per_set,
        ))

    exercises.sort(key=lambda x: (not x["is_compound"], x["muscle"]))

    avoid_muscles = [
        {"muscle": m.title(), "reason": f"Readiness {r['score']}/10"}
        for m, r in readiness_scores.items() if r["score"] < 5
    ]

    upper = {"chest", "back", "shoulders", "biceps", "triceps"}
    lower = {"quads", "hamstrings", "glutes", "calves", "adductors"}
    has_upper = any(m in upper for m in available_muscles)
    has_lower = any(m in lower for m in available_muscles)

    if has_upper and has_lower:
        focus = "Full Body"
    elif has_upper:
        focus = "Upper Body"
    elif has_lower:
        focus = "Lower Body"
    else:
        focus = "General"

    # Calculate actual estimated duration
    total_exercise_time = sum(e.get("estimated_time", 10) for e in exercises)
    total_time = total_exercise_time + 10  # Add warmup/cooldown

    cardio_data = None

    # Calculate remaining time for cardio (ensure we don't exceed available time)
    remaining_time = available_time - total_time

    if cardio_rec.get("include_cardio") and remaining_time >= 10:
        # Adjust cardio duration if needed to fit within time frame
        actual_cardio_duration = min(cardio_duration, remaining_time)

        # Only include cardio if we have at least 10 minutes
        if actual_cardio_duration >= 10:
            cardio_data = {
                "type": cardio_rec.get("type"),
                "duration_minutes": actual_cardio_duration,
                "zone": cardio_rec.get("zone"),
                "zone_description": cardio_rec.get("zone_description"),
                "heart_rate_range": cardio_rec.get("heart_rate_range"),
                "intensity": cardio_rec.get("intensity"),
                "technique": cardio_rec.get("technique"),
                "reason": cardio_rec.get("reason")
            }
            # Add note if duration was reduced
            if actual_cardio_duration < cardio_duration:
                cardio_data["reason"] = f"Time-adjusted: {actual_cardio_duration} min (normally {cardio_duration} min). " + cardio_rec.get("reason", "")
            total_time += actual_cardio_duration
        else:
            cardio_data = {
                "include_cardio": False,
                "reason": f"Skipped cardio - only {remaining_time} min remaining. {cardio_rec.get('reason', '')}"
            }
    elif cardio_rec.get("include_cardio") and remaining_time < 10:
        # Not enough time for cardio
        cardio_data = {
            "include_cardio": False,
            "reason": f"No time for cardio today ({remaining_time} min remaining). Consider cardio on a rest day."
        }
    elif cardio_rec.get("reason"):
        # Include reason even if not including cardio
        cardio_data = {
            "include_cardio": False,
            "reason": cardio_rec.get("reason")
        }

    duration_str = f"{total_time} min"

    mesocycle = {
        "week": meso_week,
        "phase": meso_plan["name"],
        "volume_multiplier": round(volume_multiplier, 2),
        "rpe_base": meso_plan["rpe_base"],
    }
    if deload_forced:
        mesocycle["deload_forced"] = True
        mesocycle["indicators"] = deload_status.get("indicators", [])

    recommendation = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "created_at": datetime.now().isoformat(),
        "focus": focus,
        "goal": goal,
        "goal_name": goal_params["name"],
        "estimated_duration": duration_str,
        "estimated_minutes": total_time,
        "available_time": available_time,
        "mesocycle": mesocycle,
        "exercises": exercises,
        "cardio": cardio_data,
        "muscles_to_avoid": avoid_muscles,
        "time_adjusted": available_time < 60  # Flag if workout was time-constrained
    }

    # Store recommendation for tracking (only when explicitly requested)
    if persist:
        WORKOUT_RECOMMENDATIONS.append(recommendation)

    return recommendation


def generate_alerts(workouts, soreness_data):
    # Suppress regression alerts if no recent workouts (>30 days)
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    recent_workouts = [w for w in workouts if w.get('date', '') >= cutoff]
    if not recent_workouts:
        return [{
            'type': 'info',
            'title': 'Welcome Back',
            'message': 'No workouts in 30+ days. Your first session back will recalibrate everything.',
            'actions': ['Start light', 'Focus on form', 'RPE auto-adjusted']
        }]
    # If fewer than 6 recent workouts, suppress regression alerts (still ramping up)
    suppress_regression = len(recent_workouts) < 6
    """Generate alerts based on current data."""
    alerts = []
    progression = calculate_progression_status(workouts)
    volume = calculate_volume(workouts, weeks=4)
    cutoff_date = datetime.now().date() - timedelta(days=30)

    for exercise, data in progression.items():
        if data["status"] == "Plateau":
            alerts.append({
                "priority": "HIGH",
                "type": "Plateau Detected",
                "message": f"{exercise}: No progression in recent sessions",
                "action": "Consider deload or exercise variation",
                "color": "orange"
            })
        elif data["status"] == "Regression" and not suppress_regression:
            history = data.get("history", [])
            if history:
                dated_history = []
                for h in history:
                    try:
                        h_date = datetime.strptime(h.get("date", ""), "%Y-%m-%d").date()
                    except Exception:
                        continue
                    dated_history.append((h_date, h.get("e1rm")))
                if dated_history:
                    last_date = max(d[0] for d in dated_history)
                    if last_date >= cutoff_date:
                        recent_e1rms = [e1rm for d, e1rm in dated_history if d >= cutoff_date and e1rm is not None]
                        if recent_e1rms:
                            peak_recent = max(recent_e1rms)
                            current_e1rm = data.get("current_e1rm") or 0
                            if peak_recent > 0 and current_e1rm < peak_recent * 0.95:
                                alerts.append({
                                    "priority": "HIGH",
                                    "type": "Regression",
                                    "message": f"{exercise}: e1RM down from 30-day peak",
                                    "action": "Check form, sleep, nutrition",
                                    "color": "red"
                                })

    for muscle, data in volume.items():
        if data["status"] == "Above MRV":
            alerts.append({
                "priority": "HIGH",
                "type": "Overtraining Risk",
                "message": f"{muscle.title()}: Volume exceeds MRV",
                "action": "Reduce volume or take rest day",
                "color": "red"
            })

    for exercise, data in progression.items():
        if data["current_e1rm"] >= data["peak_e1rm"] and len(data.get("history", [])) > 1:
            alerts.append({
                "priority": "🏆 PR",
                "type": "PR Achieved",
                "message": f"{exercise}: New e1RM of {data['current_e1rm']} lbs",
                "action": "Great work!",
                "color": "green"
            })

    return alerts


# ==================== ADVANCED KPIs (Training Log Analyzer) ====================

def calculate_personal_records(workouts: list) -> dict:
    """Calculate all-time and recent PRs per exercise."""
    exercise_prs = {}

    for workout in workouts:
        for exercise in workout.get("exercises", []):
            machine = exercise["machine"]
            if machine not in exercise_prs:
                exercise_prs[machine] = {"all_time": 0, "all_time_date": "", "recent_30": 0, "recent_30_date": ""}

            for s in exercise.get("sets", []):
                e1rm = calculate_e1rm(s["weight_lbs"], s["reps"])
                if e1rm > exercise_prs[machine]["all_time"]:
                    exercise_prs[machine]["all_time"] = round(e1rm, 1)
                    exercise_prs[machine]["all_time_date"] = workout["date"]

                # Check if within last 30 days
                workout_date = datetime.strptime(workout["date"], '%Y-%m-%d')
                if (datetime.now() - workout_date).days <= 30:
                    if e1rm > exercise_prs[machine]["recent_30"]:
                        exercise_prs[machine]["recent_30"] = round(e1rm, 1)
                        exercise_prs[machine]["recent_30_date"] = workout["date"]

    return exercise_prs


def calculate_training_consistency(workouts: list) -> dict:
    """Calculate training streaks and consistency metrics."""
    if not workouts:
        return {"current_streak": 0, "longest_streak": 0, "weekly_avg": 0, "consistency_pct": 0, "days_since_last": None}

    dates = sorted([datetime.strptime(w["date"], '%Y-%m-%d') for w in workouts])

    # Calculate weekly frequency
    if len(dates) >= 2:
        total_weeks = max(1, (dates[-1] - dates[0]).days / 7)
        weekly_avg = len(dates) / total_weeks
    else:
        weekly_avg = len(dates)

    # Calculate streaks (training within 7 days = continuing streak)
    current_streak = 1
    longest_streak = 1
    streak = 1

    for i in range(1, len(dates)):
        gap = (dates[i] - dates[i-1]).days
        if gap <= 7:
            streak += 1
            longest_streak = max(longest_streak, streak)
        else:
            streak = 1

    # Current streak (from most recent)
    if dates:
        days_since_last = (datetime.now() - dates[-1]).days
        if days_since_last <= 7:
            current_streak = streak
        else:
            current_streak = 0

    # Consistency % (sessions per week target = 2)
    consistency_pct = min(100, (weekly_avg / 2) * 100)

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "weekly_avg": round(weekly_avg, 1),
        "consistency_pct": round(consistency_pct),
        "days_since_last": (datetime.now() - dates[-1]).days if dates else 999
    }


def calculate_push_pull_ratio(workouts: list, weeks: int = 4) -> dict:
    """Calculate push/pull muscle balance ratio."""
    cutoff = datetime.now() - timedelta(days=weeks * 7)

    push_sets = 0  # chest, shoulders, triceps
    pull_sets = 0  # back, biceps

    push_muscles = {"chest", "shoulders", "triceps"}
    pull_muscles = {"back", "biceps"}

    for workout in workouts:
        date_s = workout.get("date")
        if not date_s:
            continue
        try:
            workout_date = datetime.strptime(date_s, '%Y-%m-%d')
        except Exception:
            continue
        if workout_date < cutoff:
            continue

        for exercise in workout.get("exercises", []) or []:
            if not isinstance(exercise, dict):
                continue
            muscle = exercise.get("muscle_group") or "unknown"
            num_sets = len(exercise.get("sets", []) or [])

            if muscle in push_muscles:
                push_sets += num_sets
            elif muscle in pull_muscles:
                pull_sets += num_sets

    ratio = push_sets / pull_sets if pull_sets > 0 else 0

    # Ideal ratio is around 1:1
    if 0.8 <= ratio <= 1.2:
        balance_status = "Balanced"
        balance_color = "green"
    elif ratio > 1.2:
        balance_status = "Push Heavy"
        balance_color = "yellow"
    else:
        balance_status = "Pull Heavy"
        balance_color = "yellow"

    if ratio > 1.5 or ratio < 0.67:
        balance_color = "red"

    return {
        "push_sets": push_sets,
        "pull_sets": pull_sets,
        "ratio": round(ratio, 2) if ratio else 0,
        "status": balance_status,
        "color": balance_color
    }


def detect_deload_need(workouts: list, soreness_data: list) -> dict:
    """Detect if a deload week is recommended."""
    if len(workouts) < 4:
        return {"needed": False, "reason": "Insufficient data", "recommendation": ""}

    # Check for consecutive weeks of training
    recent_workouts = workouts[-8:] if len(workouts) >= 8 else workouts
    dates = [datetime.strptime(w["date"], '%Y-%m-%d') for w in recent_workouts]

    if not dates:
        return {"needed": False, "reason": "No recent workouts", "recommendation": ""}

    weeks_training = (dates[-1] - dates[0]).days / 7 if len(dates) > 1 else 0
    four_weeks_ago = datetime.now() - timedelta(days=28)
    recent_4w_sessions = [
        d for d in dates
        if d >= four_weeks_ago
    ]
    avg_sessions_per_week = len(recent_4w_sessions) / 4
    if avg_sessions_per_week < 2.0:
        return {
            "needed": False,
            "reason": "Training frequency too low for deload to be warranted",
            "indicators": [],
            "weeks_since_deload": round(weeks_training, 1),
            "recommendation": "Training frequency too low for deload to be warranted"
        }

    # Check progression status for regression
    progression = calculate_progression_status(workouts)
    regressions = sum(1 for d in progression.values() if d["status"] == "Regression")
    plateaus = sum(1 for d in progression.values() if d["status"] == "Plateau")

    # Check average soreness
    recent_soreness = [s for s in soreness_data if s.get("soreness_level", 0) >= 6]

    # Deload indicators
    indicators = []
    if weeks_training >= 4:
        indicators.append("4+ weeks continuous training")
    if regressions >= 2:
        indicators.append(f"{regressions} exercises regressing")
    if plateaus >= 3:
        indicators.append(f"{plateaus} exercises plateaued")
    if len(recent_soreness) >= 3:
        indicators.append("High soreness levels")

    needed = len(indicators) >= 2

    return {
        "needed": needed,
        "indicators": indicators,
        "weeks_since_deload": round(weeks_training, 1),
        "recommendation": "Consider 50% volume, 70% intensity for 1 week" if needed else "Continue training"
    }


def calculate_injury_risk(workouts: list, soreness_data: list) -> dict:
    """Identify potential injury risk indicators."""
    risks = []

    # Check for sudden volume increases
    if len(workouts) >= 4:
        recent_volume = sum(
            len(e.get("sets", []))
            for w in workouts[-2:]
            for e in w.get("exercises", [])
        )
        previous_volume = sum(
            len(e.get("sets", []))
            for w in workouts[-4:-2]
            for e in w.get("exercises", [])
        )
        if previous_volume > 0:
            volume_increase = (recent_volume - previous_volume) / previous_volume * 100
            if volume_increase > 30:
                risks.append({
                    "type": "Volume Spike",
                    "message": f"Volume increased {volume_increase:.0f}% recently",
                    "severity": "medium"
                })

    # Check for high RPE patterns
    high_rpe_count = 0
    for workout in workouts[-4:]:
        for exercise in workout.get("exercises", []):
            for s in exercise.get("sets", []):
                if s.get("rpe", 7) >= 9:
                    high_rpe_count += 1

    if high_rpe_count >= 6:
        risks.append({
            "type": "High Intensity",
            "message": f"{high_rpe_count} sets at RPE 9+ recently",
            "severity": "medium"
        })

    # Check soreness patterns
    persistent_soreness = {}
    for s in soreness_data:
        muscle = s.get("muscle", "")
        if s.get("soreness_level", 0) >= 6:
            persistent_soreness[muscle] = persistent_soreness.get(muscle, 0) + 1

    for muscle, count in persistent_soreness.items():
        if count >= 2:
            risks.append({
                "type": "Persistent Soreness",
                "message": f"{muscle.title()}: High soreness {count} times",
                "severity": "high" if count >= 3 else "medium"
            })

    # Overall risk score
    risk_score = sum(2 if r["severity"] == "high" else 1 for r in risks)
    if risk_score >= 4:
        overall = "High"
        color = "red"
    elif risk_score >= 2:
        overall = "Moderate"
        color = "yellow"
    else:
        overall = "Low"
        color = "green"

    return {
        "overall": overall,
        "color": color,
        "score": risk_score,
        "risks": risks
    }


def calculate_workout_summary_stats(workouts: list) -> dict:
    """Calculate comprehensive workout statistics."""
    if not workouts:
        return {}

    total_sessions = len(workouts)
    total_sets = sum(len(e.get("sets", [])) for w in workouts for e in w.get("exercises", []))
    total_volume = sum(
        s["weight_lbs"] * s["reps"]
        for w in workouts
        for e in w.get("exercises", [])
        for s in e.get("sets", [])
    )

    # Exercise frequency
    exercise_freq = {}
    for w in workouts:
        for e in w.get("exercises", []):
            machine = e["machine"]
            exercise_freq[machine] = exercise_freq.get(machine, 0) + 1

    top_exercises = sorted(exercise_freq.items(), key=lambda x: -x[1])[:5]

    # Session types
    session_types = {}
    for w in workouts:
        st = w.get("session_type", "other")
        session_types[st] = session_types.get(st, 0) + 1

    # Date range
    dates = [w["date"] for w in workouts]

    return {
        "total_sessions": total_sessions,
        "total_sets": total_sets,
        "total_volume": round(total_volume),
        "avg_sets_per_session": round(total_sets / total_sessions, 1) if total_sessions else 0,
        "date_range": f"{min(dates)} to {max(dates)}" if dates else "N/A",
        "top_exercises": [{"exercise": e, "count": c} for e, c in top_exercises],
        "session_types": session_types
    }


# ==================== READINESS FACTORS (ACWR / SLEEP DEBT / RECOVERY) ====================

def _parse_iso_date_or_datetime(s: str | None):
    if not s:
        return None
    try:
        # Handles YYYY-MM-DD and full ISO timestamps
        return datetime.fromisoformat(s)
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None


def calculate_acwr(workouts: list) -> dict:
    """Calculate Acute:Chronic Workload Ratio (ACWR) from logged workouts.

    Load definition: sum over all sets of (1 * reps * weight), plus
    duration-derived load for cardio-style app and Apple Health workouts.
    Acute: sum of last 7 days.
    Chronic: average 7-day load over the last 28 days (uses available days if < 28).
    """
    today = datetime.now().date()
    workouts = _recommendation_workouts_with_apple_health(workouts, days=28)
    if not workouts:
        return {
            "acwr": 0.0,
            "acute_load": 0,
            "chronic_load": 0,
            "risk": "detraining",
            "message": "No workouts logged yet; ACWR unavailable."
        }

    def workout_load(w: dict) -> float:
        total = 0.0
        for ex in w.get("exercises", []) or []:
            for s in ex.get("sets", []) or []:
                reps = s.get("reps")
                weight = s.get("weight_lbs")
                try:
                    reps_f = float(reps) if reps is not None else 0.0
                    weight_f = float(weight) if weight is not None else 0.0
                except Exception:
                    reps_f, weight_f = 0.0, 0.0
                total += reps_f * weight_f
        if total <= 0:
            total = _fit95_number(w.get("recommendation_load"))
        if total <= 0:
            total = _workout_duration_minutes(w)
        return total

    # Build daily loads for the last 28 days
    start_28 = today - timedelta(days=27)
    daily = {}
    min_seen = None
    for w in workouts:
        d_s = w.get("date")
        try:
            d = datetime.strptime(d_s, "%Y-%m-%d").date()
        except Exception:
            continue
        if d < start_28 or d > today:
            continue
        if min_seen is None or d < min_seen:
            min_seen = d
        daily[d] = daily.get(d, 0.0) + workout_load(w)

    if not daily:
        return {
            "acwr": 0.0,
            "acute_load": 0,
            "chronic_load": 0,
            "risk": "detraining",
            "message": "No workouts in the last 28 days; ACWR indicates detraining."
        }

    # Acute = last 7 days sum (inclusive)
    start_7 = today - timedelta(days=6)
    acute_total = sum(v for d, v in daily.items() if d >= start_7)

    # Chronic = average weekly load over last 28 days
    observed_days = min(28, (today - (min_seen or start_28)).days + 1)
    chronic_total = sum(daily.values())
    chronic_weekly_avg = (chronic_total / max(1, observed_days)) * 7.0

    acute_i = int(round(acute_total))
    chronic_i = int(round(chronic_weekly_avg))

    if chronic_weekly_avg <= 0:
        acwr = 0.0
    else:
        acwr = float(acute_total / chronic_weekly_avg)

    # Risk bands
    if acwr < 0.8:
        risk = "detraining"
        msg = "Training load is low vs recent baseline (detraining risk)."
    elif acwr <= 1.3:
        risk = "optimal"
        msg = "ACWR is in the optimal range."
    elif acwr <= 1.5:
        risk = "caution"
        msg = "ACWR is elevated; consider moderating intensity/volume."
    else:
        risk = "high"
        msg = "ACWR is high; elevated injury risk—prioritize recovery/deload."

    return {
        "acwr": round(acwr, 3),
        "acute_load": acute_i,
        "chronic_load": chronic_i,
        "risk": risk,
        "message": msg,
    }


def calculate_sleep_debt(oura_db_file: str, days: int = 7) -> dict:
    """Calculate accumulated sleep debt over the last N days from Oura SQLite cache."""
    target = 420  # minutes
    days = max(1, int(days or 7))

    try:
        conn = sqlite3.connect(oura_db_file)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT day, sleep_duration_min FROM oura_daily WHERE sleep_duration_min IS NOT NULL ORDER BY day DESC LIMIT ?",
                (days,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        return {
            "debt_minutes": 0,
            "debt_hours": 0.0,
            "nights_under": 0,
            "avg_sleep_min": 0.0,
            "status": "good",
            "message": f"Sleep debt unavailable (DB error: {str(e)}).",
        }

    if not rows:
        return {
            "debt_minutes": 0,
            "debt_hours": 0.0,
            "nights_under": 0,
            "avg_sleep_min": 0.0,
            "status": "good",
            "message": "No recent sleep-duration data available from Oura cache.",
        }

    debt = 0
    nights_under = 0
    total_sleep = 0
    n = 0
    for r in rows:
        sd = r.get("sleep_duration_min")
        try:
            sd_i = int(sd)
        except Exception:
            continue
        n += 1
        total_sleep += sd_i
        if sd_i < target:
            nights_under += 1
            debt += (target - sd_i)

    avg_sleep = (total_sleep / n) if n else 0.0

    if debt < 60:
        status = "good"
    elif debt <= 180:
        status = "mild"
    elif debt <= 300:
        status = "moderate"
    else:
        status = "severe"

    msg = f"{status.title()} sleep debt: {debt} min over last {n} nights (avg {avg_sleep:.0f} min)."

    return {
        "debt_minutes": int(debt),
        "debt_hours": round(debt / 60.0, 2),
        "nights_under": int(nights_under),
        "avg_sleep_min": round(avg_sleep, 1),
        "status": status,
        "message": msg,
    }


def calculate_recovery_bonus(recovery_data: list, hours: int = 48) -> dict:
    """Compute recovery modality bonus points based on entries in the last N hours."""
    hours = max(1, int(hours or 48))
    cutoff = datetime.now() - timedelta(hours=hours)

    weights = {
        "cold_plunge": 5,
        "ice_bath": 5,
        "sauna": 3,
        "massage": 2,
        "foam_roll": 2,
        "stretching": 1,
        "yoga": 1,
    }

    used = []
    bonus = 0

    for r in recovery_data or []:
        dt = _parse_iso_date_or_datetime(r.get("created_at") or r.get("date"))
        if not dt:
            continue
        if dt < cutoff:
            continue
        rt = (r.get("recovery_type") or "").strip().lower()
        if not rt:
            continue

        # Normalize a few common variations
        rt_norm = rt.replace("-", "_").replace(" ", "_")
        if rt_norm in weights:
            bonus += weights[rt_norm]
            used.append(rt_norm)

    used_unique = sorted(set(used))
    if bonus:
        msg = f"Recovery modalities in last {hours}h: {', '.join(used_unique)} (+{bonus})."
    else:
        msg = f"No recovery modalities logged in last {hours}h."

    return {
        "bonus_points": int(bonus),
        "modalities_used": used_unique,
        "message": msg,
    }


# Initialize with real data if available, otherwise start empty.
# Data loading priority:
# 1. JSON files (user's saved data) - these persist across restarts
# 2. Markdown workout log (initial import)
# 3. Explicit sample data opt-in (demo mode)
if WORKOUTS:
    print(f"Loaded {len(WORKOUTS)} workouts from saved JSON data")
else:
    WORKOUT_LOG_PATH = os.path.join(os.path.dirname(__file__), "support/Workout_Log_LogTab_Past_Workouts.md")
    if os.path.exists(WORKOUT_LOG_PATH):
        print(f"Loading workout data from {WORKOUT_LOG_PATH}")
        WORKOUTS, SORENESS_DATA = parse_workout_log(WORKOUT_LOG_PATH)
        summary = get_workout_summary(WORKOUTS)
        print(f"Loaded {summary['total_sessions']} sessions, {summary['total_sets']} sets")
        # Save to JSON for future use
        save_json(WORKOUTS_FILE, WORKOUTS)
        save_json(SORENESS_FILE, SORENESS_DATA)
    elif os.environ.get("FIT_LOAD_SAMPLE_DATA") == "1":
        print("FIT_LOAD_SAMPLE_DATA=1, using sample data")
        WORKOUTS, SORENESS_DATA = get_sample_data()
    else:
        print("No saved data found, starting empty")
        WORKOUTS, SORENESS_DATA = [], []


@app.route('/')
def index():
    """Main dashboard page."""
    return Response(
        render_template('index.html'),
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.route('/api/auth/scope')
def auth_scope():
    """Return the current signed-in scope for client-side queue ownership checks."""
    return jsonify({"auth_scope": _current_auth_scope()})


def _current_auth_scope() -> str:
    return f"user:{_current_data_user_id()}"


def _workout_with_auth_scope(recommendation: dict | None) -> dict | None:
    if recommendation is None:
        return None
    scoped = dict(recommendation)
    scoped["auth_scope"] = _current_auth_scope()
    return scoped


def _persist_current_workout_plan(recommendation: dict, fingerprint: str) -> dict:
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT, LAST_WORKOUT_RECOMMENDATION_OWNER
    with CURRENT_WORKOUT_PLAN_LOCK:
        LAST_WORKOUT_RECOMMENDATION = recommendation
        LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = fingerprint
        LAST_WORKOUT_RECOMMENDATION_OWNER = {
            "user_id": _current_data_user_id(),
            "fingerprint": fingerprint,
            "plan_id": id(recommendation),
        }
        save_current_workout_plan(_current_data_user_id(), fingerprint, recommendation)
    return recommendation


def _wearable_adjusted_for_display(base_plan, guarded_recommendation, whoop_context) -> dict:
    """Apply wearable (WHOOP deload/caution) modifiers to a base plan for
    DISPLAY, given an already-built wearable context. Fail-open: if the modifier
    transform raises, degrade to serving the base plan rather than 500-ing.

    apply_wearable_modifiers deep-copies its input, so ``base_plan`` (and the
    persisted canonical row it may point at) is never mutated. The returned dict
    always carries ``next_workout``; the caller must NOT persist it (FIT-256
    finding 2: the durable plan stays the base/programmed plan).
    """
    whoop_context = whoop_context or {}
    try:
        return apply_wearable_modifiers(
            guarded_recommendation,
            base_plan,
            whoop_signals=whoop_context.get("signals"),
            source_conflict=whoop_context.get("source_conflict"),
        )
    except Exception:
        return {"next_workout": base_plan, "load_source": None}


def _wearable_display_adjusted_plan(base_plan):
    """Self-contained display-time wearable transform for surfaces that render a
    persisted plan but don't already build the wearable context themselves
    (``/gym-now``, ``/api/workout/swap``, ``/api/workout/adjust``).

    FIT-256 finding 2 follow-up: the canonical persisted plan is always the
    base/programmed plan (persisting the modifier-clamped output would ratchet a
    transient wearable reading into a permanent set reduction). So every surface
    that returns a plan to the user must re-derive the wearable-adjusted *view*
    on the way out, or it would show un-clamped base sets/RPE during an active
    deload/caution while ``/api/dashboard`` and ``/api/next-workout`` show the
    clamped values -- the opposite of a recovery signal.

    Mirrors the inputs ``/api/next-workout`` uses so those surfaces render
    identical clamped values. Best-effort; returns ``base_plan`` unchanged on any
    failure. Never persist the result.
    """
    if not isinstance(base_plan, dict) or not base_plan:
        return base_plan
    try:
        training_recommendation = _current_workout_training_recommendation()
        open_wearables_facts = _open_wearables_recommendation_facts()
        guarded_recommendation, _open_wearables_modifier = _apply_open_wearables_recommendation_guard(
            training_recommendation,
            open_wearables_facts,
        )
        readiness_score = (get_oura_daily(OURA_DB_FILE, _today_str()) or {}).get("readiness_score")
        whoop_context = _whoop_recommendation_context(readiness_score)
    except Exception:
        return base_plan
    adjusted = _wearable_adjusted_for_display(base_plan, guarded_recommendation, whoop_context)
    return adjusted.get("next_workout") or base_plan


def _workout_with_display_and_auth_scope(base_plan):
    """Display-time wearable transform + auth scope, for user-facing plan
    responses on the swap/adjust/gym surfaces. See _wearable_display_adjusted_plan."""
    return _workout_with_auth_scope(_wearable_display_adjusted_plan(base_plan))


def _payload_with_recommendation_display_scope(payload: dict) -> dict:
    """Like _payload_with_recommendation_auth_scope, but also applies the
    display-time wearable transform to the embedded recommendation so
    swap/adjust responses match /api/next-workout during an active deload."""
    scoped = dict(payload)
    if scoped.get("recommendation"):
        scoped["recommendation"] = _workout_with_display_and_auth_scope(scoped["recommendation"])
    return scoped


def _is_lightweight_current_workout_plan(recommendation: dict | None) -> bool:
    return bool((recommendation or {}).get("_fit136_lightweight_no_ow"))


def _current_workout_plan_for_fingerprint(fingerprint: str, *, allow_stale_unsaved: bool = False) -> dict | None:
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT, LAST_WORKOUT_RECOMMENDATION_OWNER
    with CURRENT_WORKOUT_PLAN_LOCK:
        if LAST_WORKOUT_RECOMMENDATION and not _is_lightweight_current_workout_plan(LAST_WORKOUT_RECOMMENDATION):
            owner = LAST_WORKOUT_RECOMMENDATION_OWNER or {}
            owner_matches_plan = owner.get("plan_id") == id(LAST_WORKOUT_RECOMMENDATION)
            if owner_matches_plan:
                if owner.get("user_id") == _current_data_user_id():
                    if owner.get("fingerprint") == fingerprint:
                        return LAST_WORKOUT_RECOMMENDATION
                    if allow_stale_unsaved:
                        return _persist_current_workout_plan(LAST_WORKOUT_RECOMMENDATION, fingerprint)
            elif LAST_WORKOUT_RECOMMENDATION_FINGERPRINT == fingerprint:
                return _persist_current_workout_plan(LAST_WORKOUT_RECOMMENDATION, fingerprint)
            elif allow_stale_unsaved and (
                LAST_WORKOUT_RECOMMENDATION_FINGERPRINT is None
                or LAST_WORKOUT_RECOMMENDATION_FINGERPRINT != fingerprint
            ):
                return _persist_current_workout_plan(LAST_WORKOUT_RECOMMENDATION, fingerprint)
        row = get_current_workout_plan(_current_data_user_id(), fingerprint=fingerprint)
        if row and row.get("plan") and not _is_lightweight_current_workout_plan(row.get("plan")):
            LAST_WORKOUT_RECOMMENDATION = row["plan"]
            LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = fingerprint
            LAST_WORKOUT_RECOMMENDATION_OWNER = {
                "user_id": _current_data_user_id(),
                "fingerprint": fingerprint,
                "plan_id": id(LAST_WORKOUT_RECOMMENDATION),
            }
            return LAST_WORKOUT_RECOMMENDATION
        if (
            allow_stale_unsaved
            and LAST_WORKOUT_RECOMMENDATION
            and not _is_lightweight_current_workout_plan(LAST_WORKOUT_RECOMMENDATION)
            and not (
                (LAST_WORKOUT_RECOMMENDATION_OWNER or {}).get("plan_id") == id(LAST_WORKOUT_RECOMMENDATION)
            )
        ):
            return _persist_current_workout_plan(LAST_WORKOUT_RECOMMENDATION, fingerprint)
        if allow_stale_unsaved:
            row = get_current_workout_plan(_current_data_user_id())
            if row and row.get("plan") and not _is_lightweight_current_workout_plan(row.get("plan")):
                return _persist_current_workout_plan(row["plan"], fingerprint)
        return None


def _payload_with_recommendation_auth_scope(payload: dict) -> dict:
    scoped = dict(payload)
    if scoped.get("recommendation"):
        scoped["recommendation"] = _workout_with_auth_scope(scoped["recommendation"])
    return scoped


def get_recent_hrv_trend(days=7, minimum_samples=None):
    """Return the recent Oura HRV trend, or unknown when data is sparse/unavailable."""
    try:
        end = datetime.now().date()
        start = end - timedelta(days=days - 1)
        rows = get_oura_daily_range(
            OURA_DB_FILE, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
        hrv_values = [row.get("hrv") for row in rows if row.get("hrv") is not None]
        if minimum_samples is not None and len(hrv_values) < minimum_samples:
            return "unknown"
        return compute_hrv_trend(hrv_values)
    except Exception:
        return "unknown"


def _current_workout_training_recommendation():
    """Mirror the dashboard's readiness context for lightweight workout loads."""
    today_s = _today_str()
    cached_oura = get_oura_daily(OURA_DB_FILE, today_s)
    readiness_val = cached_oura.get("readiness_score") if cached_oura else None
    try:
        readiness_val = int(readiness_val) if readiness_val is not None else None
    except Exception:
        readiness_val = None

    recent_soreness = filter_recent_soreness(SORENESS_DATA, hours=24)
    max_soreness = max((s.get("soreness_level") or 0) for s in recent_soreness) if recent_soreness else 0
    signal = "TRAIN" if (readiness_val is not None and readiness_val >= 70 and max_soreness < 7) else "RECOVER"

    hrv_trend = get_recent_hrv_trend()

    last_completed = summarize_recent_completion(WORKOUTS, hours=24)
    last_hours_ago = last_completed.get("hours_ago") if last_completed else None
    recommendation, _ = _training_recommendation_from_factors(
        readiness_val,
        recovery_bonus=calculate_recovery_bonus(RECOVERY_DATA, hours=48),
        hrv_trend=hrv_trend,
        sleep_debt=calculate_sleep_debt(OURA_DB_FILE, days=7),
        acwr_data=calculate_acwr(WORKOUTS),
        last_completed=last_completed,
        last_hours_ago=last_hours_ago,
        weather=_cached_wttr(_WEATHER_CACHE.get("location") or "San_Antonio"),
    )
    if signal == "RECOVER" and max_soreness >= 7:
        recommendation = "recovery"
    return recommendation


def _open_wearables_workout_inputs_live():
    return not _missing_open_wearables_config()


def _store_open_wearables_recommendation_marker(data):
    global OPEN_WEARABLES_WORKOUT_MARKER_CACHE
    marker = open_wearables_hub.workout_marker(
        data,
        sleep_extractor=_extract_open_wearables_sleep,
    )
    if not isinstance(OPEN_WEARABLES_WORKOUT_MARKER_CACHE, dict):
        OPEN_WEARABLES_WORKOUT_MARKER_CACHE = {}
    OPEN_WEARABLES_WORKOUT_MARKER_CACHE[_open_wearables_profile_key()] = marker
    return marker


def _open_wearables_recommendation_marker(refresh=False):
    if not _open_wearables_workout_inputs_live():
        return {"configured": False}
    cache = OPEN_WEARABLES_WORKOUT_MARKER_CACHE if isinstance(OPEN_WEARABLES_WORKOUT_MARKER_CACHE, dict) else {}
    profile_key = _open_wearables_profile_key()
    if not refresh and profile_key in cache:
        return cache[profile_key]
    if not refresh:
        return {"configured": True, "status": "unfetched"}
    try:
        data = fetch_open_wearables_data()
        return _store_open_wearables_recommendation_marker(data)
    except Exception as exc:
        if profile_key in cache:
            return cache[profile_key]
        return {
            "configured": True,
            "error_type": type(exc).__name__,
        }


def _workout_recommendation_fingerprint():
    """Fingerprint inputs that should force a new next-workout plan."""
    today_s = _today_str()
    cached_oura = get_oura_daily(OURA_DB_FILE, today_s) or {}
    oura_rows = []
    try:
        end = datetime.now().date()
        start = end - timedelta(days=6)
        oura_rows = get_oura_daily_range(OURA_DB_FILE, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")) or []
    except Exception:
        oura_rows = []

    health_workout_files = sorted(
        glob.glob(os.path.expanduser("~/Documents/Health/healthkit_samples_workout_*.json"))
    )
    apple_status, apple_last_data, apple_last_sync = _latest_apple_health_freshness()
    whoop_connection = get_whoop_connection_status(WHOOP_DB_FILE)
    whoop_fact = get_whoop_daily_fact(WHOOP_DB_FILE, local_date=today_s) or get_whoop_daily_fact(WHOOP_DB_FILE)
    whoop_freshness = latest_whoop_freshness(WHOOP_DB_FILE)

    return workout_recommendation_fingerprint.build_fingerprint(
        auth_scope=_current_auth_scope(),
        day=today_s,
        workouts=WORKOUTS,
        soreness_data=SORENESS_DATA,
        cardio_data=CARDIO_DATA,
        recovery_data=RECOVERY_DATA,
        user_settings=USER_SETTINGS,
        weather_cache=_WEATHER_CACHE,
        open_wearables_configured=_open_wearables_workout_inputs_live(),
        open_wearables_user_id=_open_wearables_user_id(),
        open_wearables_marker=_open_wearables_recommendation_marker(),
        apple_health_enabled=_apple_health_recommendation_enabled(),
        apple_health_hr_intensity_enabled=_apple_health_hr_intensity_enabled(),
        apple_health_freshness=(apple_status, apple_last_data, apple_last_sync),
        apple_health_sync_db_file=_apple_health_sync_db_file(),
        apple_health_workout_files=health_workout_files,
        cached_oura=cached_oura,
        oura_rows=oura_rows,
        oura_db_file=OURA_DB_FILE,
        whoop_connection=whoop_connection,
        whoop_freshness=whoop_freshness,
        whoop_fact=whoop_fact,
        whoop_db_file=WHOOP_DB_FILE,
    )


@app.route('/api/next-workout')
def api_next_workout():
    """Return only the active workout prescription for gym execution."""
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT
    fingerprint = _workout_recommendation_fingerprint()
    current_plan = _current_workout_plan_for_fingerprint(fingerprint)
    training_recommendation = _current_workout_training_recommendation()
    open_wearables_facts = _open_wearables_recommendation_facts()
    guarded_training_recommendation, open_wearables_modifier = _apply_open_wearables_recommendation_guard(
        training_recommendation,
        open_wearables_facts,
    )
    if not current_plan:
        current_plan = generate_next_workout(
            WORKOUTS,
            SORENESS_DATA,
            training_recommendation=guarded_training_recommendation,
            consume_cardio_rotation=False,
        )
        _persist_current_workout_plan(current_plan, fingerprint)
    active_open_raw = str(request.args.get("active_workout_open", "false")).strip().lower()
    active_open_requested = active_open_raw in {"1", "true", "yes"}
    completed_sets_by_exercise = _completed_sets_query_param(request.args.get("completed_sets"))
    can_evaluate_active = not active_open_requested or bool(completed_sets_by_exercise)
    today_s = _today_str()
    food_log_entries = _food_log_entries_for_context(since=today_s)
    adaptation_food_entries = _food_log_entries_for_context()
    nutrition_context = _nutrition_context_for_date(
        today_s,
        hard_training_planned=_workout_looks_hard(current_plan or {}),
        food_log_entries=food_log_entries,
    )
    workout_adaptation_events = []
    if can_evaluate_active:
        current_plan, workout_adaptation_events = _apply_due_workout_adaptations_for_plan(
            current_plan or {},
            date_s=today_s,
            food_log_entries=adaptation_food_entries,
            nutrition_context=nutrition_context,
            active_workout_open=active_open_requested,
            completed_sets_by_exercise=completed_sets_by_exercise,
        )
        _persist_current_workout_plan(current_plan, fingerprint)
    today_oura = get_oura_daily(OURA_DB_FILE, today_s) or {}
    freshness = _compute_data_freshness()
    whoop_context = _whoop_recommendation_context(today_oura.get("readiness_score"))
    # Wearable modifiers (WHOOP deload/caution) are a display-time transform
    # only -- current_plan (the base/programmed plan) was already persisted
    # above. Do NOT persist the modifier-mutated output: apply_wearable_modifiers
    # one-directionally clamps target_sets/rpe/etc, and re-persisting it as the
    # canonical durable plan would ratchet a transient wearable reading into a
    # permanent reduction across restarts (FIT-256 finding 2). Fail-open so a
    # modifier error degrades to the base plan instead of a 500.
    whoop_adjusted = _wearable_adjusted_for_display(
        current_plan, guarded_training_recommendation, whoop_context
    )
    display_workout = whoop_adjusted["next_workout"]
    return jsonify({
        "next_workout": _workout_with_auth_scope(display_workout),
        "workout_adaptation_events": [workout_adaptation.project_event(event) for event in workout_adaptation_events],
        "recommendation_sources": {
            **_recommendation_sources_payload(
                freshness,
                whoop_context,
                open_wearables_facts,
                open_wearables_modifier,
                whoop_adjusted["load_source"],
            ),
            "wearable_sources": _wearable_sources_payload(freshness, whoop_context),
        },
    })


@app.route('/gym-now')
def gym_now():
    """Emergency no-app-shell workout view for stale mobile/PWA caches."""
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT
    fingerprint = _workout_recommendation_fingerprint()
    workout = _current_workout_plan_for_fingerprint(fingerprint)
    if not workout:
        workout = generate_next_workout(
            WORKOUTS,
            SORENESS_DATA,
            training_recommendation=_current_workout_training_recommendation(),
            consume_cardio_rotation=False,
            include_open_wearables_readiness=False,
        )
        workout["_fit136_lightweight_no_ow"] = True
    # Render the wearable-adjusted (deload/caution-clamped) view so /gym-now
    # shows the same sets/RPE as /api/next-workout during an active modifier;
    # the persisted plan stays the base plan (FIT-256 finding 2, display-only).
    workout = _wearable_display_adjusted_plan(workout)
    focus = html.escape(str(workout.get("focus") or workout.get("title") or "Workout"))
    estimated = workout.get("estimated_minutes") or workout.get("estimated_duration") or workout.get("available_time")
    rows = []
    for idx, ex in enumerate(workout.get("exercises") or [], 1):
        name = html.escape(str(ex.get("exercise") or ex.get("name") or ex.get("machine") or "Exercise"))
        muscle = html.escape(str(ex.get("muscle") or ex.get("muscle_group") or "").upper())
        sets = html.escape(str(ex.get("target_sets") or ex.get("sets") or "-"))
        reps = html.escape(str(ex.get("target_reps") or ex.get("reps") or "-"))
        weight = html.escape(str(ex.get("target_weight") or ex.get("target_weight_lbs") or "-"))
        rpe = html.escape(str(ex.get("rpe_target") or ex.get("rpe") or "-"))
        rows.append(
            f"<li><strong>{idx}. {name}</strong><span>{muscle}</span>"
            f"<div>{sets} x {reps} @ {weight} lb · RPE {rpe}</div></li>"
        )
    body = "\n".join(rows) or "<li>No workout generated. Try refreshing.</li>"
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="robots" content="noindex">
  <title>Gym Now</title>
  <style>
    body {{ margin:0; background:#050815; color:#f8fafc; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
    main {{ max-width:680px; margin:0 auto; padding:22px 18px 40px; }}
    h1 {{ font-size:30px; margin:10px 0 2px; letter-spacing:0; }}
    .meta {{ color:#a7b0c4; margin-bottom:18px; }}
    ol {{ list-style:none; padding:0; margin:0; display:grid; gap:10px; }}
    li {{ background:#111827; border:1px solid #263148; border-radius:8px; padding:14px; }}
    strong {{ display:block; font-size:18px; margin-bottom:4px; }}
    span {{ display:inline-block; color:#93c5fd; font-size:12px; letter-spacing:.08em; margin-bottom:8px; }}
    div {{ color:#dbeafe; font-size:16px; }}
    a {{ color:#93c5fd; }}
  </style>
</head>
<body>
  <main>
    <a href="/?v=fit181-force-live">Back to app</a>
    <h1>{focus}</h1>
    <div class="meta">{html.escape(str(estimated or ''))} min · live server-rendered workout</div>
    <ol>{body}</ol>
  </main>
</body>
</html>"""
    return Response(page, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.route('/api/dashboard')
def api_dashboard():
    """API endpoint for dashboard data."""
    volume = calculate_volume(WORKOUTS, weeks=4)
    progression = calculate_progression_status(WORKOUTS)

    total_sets = sum(d["sets"] for d in volume.values())
    improving = sum(1 for d in progression.values() if d["status"] == "On Track")
    total_exercises = len(progression)

    readiness_scores = [get_readiness_score(m, SORENESS_DATA, volume, CARDIO_DATA, WORKOUTS)["score"] for m in volume.keys()]
    avg_readiness = sum(readiness_scores) / len(readiness_scores) if readiness_scores else 7

    muscle_data = []
    for muscle, data in volume.items():
        readiness = get_readiness_score(muscle, SORENESS_DATA, volume, CARDIO_DATA, WORKOUTS)
        muscle_data.append({
            "muscle": muscle.title(),
            "sets": data["sets"],
            "status": data["status"],
            "status_color": data["status_color"],
            "last_trained": data["last_trained"],
            "readiness": readiness["score"],
            "readiness_color": readiness["color"]
        })

    exercise_data = []
    for exercise, data in progression.items():
        trend_icon = "+" if data["trend_pct"] > 0 else "" if data["trend_pct"] < 0 else ""
        status_color = "green" if data["status"] == "On Track" else "yellow" if data["status"] == "Plateau" else "red"
        trend_color = "green" if data["trend_pct"] > 0 else "red" if data["trend_pct"] < 0 else "gray"
        exercise_data.append({
            "exercise": exercise,
            "peak_e1rm": data["peak_e1rm"],
            "current_e1rm": data["current_e1rm"],
            "trend_pct": data["trend_pct"],
            "trend_icon": trend_icon,
            "trend_color": trend_color,
            "status": data["status"],
            "status_color": status_color,
            "history": [{"date": h["date"], "e1rm": round(h["e1rm"], 1)} for h in data.get("history", [])]
        })

    # Advanced KPIs
    prs = calculate_personal_records(WORKOUTS)
    consistency = calculate_training_consistency(WORKOUTS)
    push_pull = calculate_push_pull_ratio(WORKOUTS)
    deload = detect_deload_need(WORKOUTS, SORENESS_DATA)
    injury_risk = calculate_injury_risk(WORKOUTS, SORENESS_DATA)
    summary_stats = calculate_workout_summary_stats(WORKOUTS)

    # Readiness factors (new)
    acwr = calculate_acwr(WORKOUTS)
    sleep_debt = calculate_sleep_debt(OURA_DB_FILE, days=7)
    recovery_bonus = calculate_recovery_bonus(RECOVERY_DATA, hours=48)
    hrv_trend = get_recent_hrv_trend()

    # Body stats
    body_stats = {}
    if BODY_DATA:
        sorted_body = sorted(BODY_DATA, key=lambda x: x.get("date") or "", reverse=True)
        latest = sorted_body[0]
        body_stats["latest_weight"] = latest.get("weight_lbs")
        body_stats["latest_body_fat"] = latest.get("body_fat_pct")

        # 30-day weight change
        today = datetime.now().date()
        thirty_days_ago = today - timedelta(days=30)
        old_entries = [
            e for e in BODY_DATA
            if e.get("date") and datetime.strptime(e["date"], "%Y-%m-%d").date() <= thirty_days_ago
        ]
        if old_entries:
            oldest_in_range = sorted(old_entries, key=lambda x: x.get("date") or "")[-1]
            old_weight = oldest_in_range.get("weight_lbs")
            if old_weight and body_stats["latest_weight"]:
                body_stats["weight_change_30d"] = round(body_stats["latest_weight"] - old_weight, 1)

        # Trend direction
        if body_stats.get("weight_change_30d") is not None:
            weight_change = body_stats["weight_change_30d"]
            if weight_change > 0.5:
                body_stats["trend"] = "increasing"
            elif weight_change < -0.5:
                body_stats["trend"] = "decreasing"
            else:
                body_stats["trend"] = "stable"
        else:
            recent_7 = sorted_body[:7]
            if len(recent_7) >= 3:
                weights = [e.get("weight_lbs") for e in recent_7 if e.get("weight_lbs")]
                if len(weights) >= 3:
                    x = list(range(len(weights)))
                    n = len(weights)
                    x_mean = sum(x) / n
                    y_mean = sum(weights) / n
                    numerator = sum((x[i] - x_mean) * (weights[i] - y_mean) for i in range(n))
                    denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
                    if denominator != 0:
                        slope = numerator / denominator
                        body_stats["trend"] = "increasing" if slope > 0.1 else "decreasing" if slope < -0.1 else "stable"
                    else:
                        body_stats["trend"] = "unknown"

    # Recomp command center signal
    today_s = _today_str()
    cached_oura = get_oura_daily(OURA_DB_FILE, today_s)
    readiness_val = cached_oura.get("readiness_score") if cached_oura else None
    try:
        readiness_val = int(readiness_val) if readiness_val is not None else None
    except Exception:
        readiness_val = None
    recent_soreness = filter_recent_soreness(SORENESS_DATA, hours=24)
    max_soreness = max((s.get("soreness_level") or 0) for s in recent_soreness) if recent_soreness else 0
    signal = "TRAIN" if (readiness_val is not None and readiness_val >= 70 and max_soreness < 7) else "RECOVER"
    reason_bits = []
    if readiness_val is None:
        reason_bits.append("Readiness unavailable")
    else:
        reason_bits.append(f"Readiness {readiness_val}")
    if recent_soreness:
        reason_bits.append(f"Max soreness {max_soreness}")
    else:
        reason_bits.append("No soreness logged in last 24h")

    last_completed = summarize_recent_completion(WORKOUTS, hours=24)
    last_hours_ago = last_completed.get("hours_ago") if last_completed else None
    weather = _cached_wttr(_WEATHER_CACHE.get("location") or "San_Antonio")
    dashboard_training_recommendation, _ = _training_recommendation_from_factors(
        readiness_val,
        recovery_bonus=recovery_bonus,
        hrv_trend=hrv_trend,
        sleep_debt=sleep_debt,
        acwr_data=acwr,
        last_completed=last_completed,
        last_hours_ago=last_hours_ago,
        weather=weather,
    )
    if signal == "RECOVER" and max_soreness >= 7:
        dashboard_training_recommendation = "recovery"
    open_wearables_facts = _open_wearables_recommendation_facts()
    guarded_dashboard_recommendation, open_wearables_modifier = _apply_open_wearables_recommendation_guard(
        dashboard_training_recommendation,
        open_wearables_facts,
    )
    if open_wearables_modifier.get("detail"):
        reason_bits.append(open_wearables_modifier["detail"])
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT
    fingerprint = _workout_recommendation_fingerprint()
    next_workout = _current_workout_plan_for_fingerprint(fingerprint)
    if not next_workout or next_workout.get("_fit136_lightweight_no_ow"):
        next_workout = generate_next_workout(
            WORKOUTS,
            SORENESS_DATA,
            training_recommendation=guarded_dashboard_recommendation,
            consume_cardio_rotation=False,
        )
    food_log_entries = _food_log_entries_for_context(since=today_s)
    adaptation_food_entries = _food_log_entries_for_context()
    nutrition_context = _nutrition_context_for_date(
        today_s,
        hard_training_planned=_workout_looks_hard(next_workout),
        food_log_entries=food_log_entries,
    )
    workout_adaptation_events = []
    active_open_raw = str(request.args.get("active_workout_open", "false")).strip().lower()
    active_open_requested = active_open_raw in {"1", "true", "yes"}
    completed_sets_by_exercise = _completed_sets_query_param(request.args.get("completed_sets"))
    if not active_open_requested or completed_sets_by_exercise:
        next_workout, workout_adaptation_events = _apply_due_workout_adaptations_for_plan(
            next_workout,
            date_s=today_s,
            food_log_entries=adaptation_food_entries,
            nutrition_context=nutrition_context,
            active_workout_open=active_open_requested,
            completed_sets_by_exercise=completed_sets_by_exercise,
        )
        if workout_adaptation_events:
            nutrition_context = _nutrition_context_for_date(
                today_s,
                hard_training_planned=_workout_looks_hard(next_workout),
                food_log_entries=food_log_entries,
            )
    freshness = _compute_data_freshness()
    whoop_context = _whoop_recommendation_context(readiness_val)
    # Persist the base/programmed plan (post-adaptation, pre-wearable-modifier)
    # as the canonical durable plan BEFORE applying wearable modifiers.
    # apply_wearable_modifiers one-directionally clamps target_sets/rpe/etc
    # for display; re-persisting its output would ratchet a transient WHOOP
    # deload/caution reading into a permanent reduction across restarts
    # (FIT-256 finding 2). Wearable modifiers are applied as a display-time
    # transform on the way out only. Persisting the base first is safe: the base
    # is the correct canonical value regardless, and the fail-open display
    # transform below degrades to the base plan rather than 500-ing.
    _persist_current_workout_plan(next_workout, fingerprint)
    whoop_adjusted = _wearable_adjusted_for_display(
        next_workout, guarded_dashboard_recommendation, whoop_context
    )
    next_workout = whoop_adjusted["next_workout"]
    nutrition_context = _nutrition_context_for_date(
        today_s,
        hard_training_planned=_workout_looks_hard(next_workout),
        food_log_entries=food_log_entries,
    )
    nutrition_today_payload = _nutrition_today_public_payload(today_s, nutrition_context)
    return jsonify({
        "headline": {
            "total_sets": total_sets,
            "improving": improving,
            "total_exercises": total_exercises,
            "avg_readiness": round(avg_readiness, 1),
            "sessions": len(WORKOUTS)
        },
        "muscles": muscle_data,
        "exercises": exercise_data,
        "alerts": generate_alerts(WORKOUTS, SORENESS_DATA),
        "next_workout": _workout_with_auth_scope(next_workout),
        "workout_adaptation_events": [workout_adaptation.project_event(event) for event in workout_adaptation_events],
        "readiness_factors": {
            "acwr": acwr,
            "sleep_debt": sleep_debt,
            "recovery_bonus": recovery_bonus,
        },
        "body_stats": body_stats,
        "recomp_command": {
            "signal": signal,
            "readiness": readiness_val,
            "reason": "; ".join(reason_bits)
        },
        "nutrition_today": nutrition_today_payload,
        "advanced_kpis": {
            "personal_records": prs,
            "consistency": consistency,
            "push_pull_balance": push_pull,
            "deload_check": deload,
            "injury_risk": injury_risk,
            "summary_stats": summary_stats
        },
        "freshness": freshness,
        "wearable_sources": _wearable_sources_payload(freshness, whoop_context),
        "recommendation_sources": _recommendation_sources_payload(
            freshness,
            whoop_context,
            open_wearables_facts,
            open_wearables_modifier,
            whoop_adjusted["load_source"],
        ),
    })


@app.route('/api/vitals')
def api_vitals():
    """Vitals panel data: weight (local), HR/sleep/activity (Open Wearables)."""
    current_weight = None
    body_fat = None
    if BODY_DATA:
        sorted_body = sorted(BODY_DATA, key=lambda x: x.get("date") or "", reverse=True)
        latest = sorted_body[0]
        try:
            current_weight = float(latest.get("weight_lbs")) if latest.get("weight_lbs") is not None else None
        except Exception:
            current_weight = None
        try:
            body_fat = float(latest.get("body_fat_pct")) if latest.get("body_fat_pct") is not None else None
        except Exception:
            body_fat = None

    trend_7d = _body_trend(7)
    trend_30d = _body_trend(30)
    change_7d = _trend_change(trend_7d)
    change_30d = _trend_change(trend_30d)

    ow_data = fetch_open_wearables_data()
    activity_summaries = _extract_open_wearables_activity_summaries(ow_data.get("activity_summary"))
    sleep_events = _extract_open_wearables_sleep_events(ow_data.get("sleep"))

    today = datetime.now().date()
    start = today - timedelta(days=6)

    activity_7d = [a for a in activity_summaries if a.get("date") and start <= a["date"] <= today]
    activity_7d.sort(key=lambda x: x["date"])

    def _latest_activity():
        if not activity_7d:
            return None
        today_entry = next((a for a in activity_7d if a["date"] == today), None)
        return today_entry or activity_7d[-1]

    latest_activity = _latest_activity()

    steps_avg_7d = None
    steps_vals = [a.get("steps") for a in activity_7d if a.get("steps") is not None]
    if steps_vals:
        steps_avg_7d = int(round(sum(steps_vals) / len(steps_vals)))

    hr_trend = [
        {
            "date": a["date"].strftime("%Y-%m-%d"),
            "resting": a.get("resting"),
            "average": a.get("average"),
        }
        for a in activity_7d
    ]

    sleep_7d = [s for s in sleep_events if s.get("event_time") and start <= s["event_time"].date() <= today]
    last_night, avg_7d_hours = _sleep_metrics_from_events(sleep_7d)

    resting_bpm = latest_activity.get("resting") if latest_activity else None
    average_bpm = latest_activity.get("average") if latest_activity else None
    steps_today = latest_activity.get("steps") if latest_activity else None
    active_calories_today = latest_activity.get("active_calories") if latest_activity else None
    active_minutes_today = latest_activity.get("active_minutes") if latest_activity else None
    quality_score = None
    sources = {
        "heart_rate": "open_wearables" if resting_bpm is not None else None,
        "activity": "open_wearables" if steps_today is not None else None,
        "sleep": "open_wearables" if last_night else None,
    }

    # Fallback from oura_daily.sqlite3 when Open Wearables is unavailable/empty.
    try:
        today_s = today.strftime("%Y-%m-%d")
        start_s = start.strftime("%Y-%m-%d")
        oura_today = get_oura_daily(OURA_DB_FILE, today_s)
        oura_week = get_oura_daily_range(OURA_DB_FILE, start_s, today_s) or []

        def _latest_with(field):
            for r in reversed(oura_week):
                if r.get(field) is not None:
                    return r
            return None

        if resting_bpm is None:
            row = (oura_today if oura_today and oura_today.get("resting_hr") is not None
                   else _latest_with("resting_hr"))
            if row:
                resting_bpm = row.get("resting_hr")
                sources["heart_rate"] = "oura"

        if steps_today is None:
            row = (oura_today if oura_today and oura_today.get("steps") is not None
                   else _latest_with("steps"))
            if row:
                steps_today = row.get("steps")
                sources["activity"] = "oura"

        if active_calories_today is None:
            row = (oura_today if oura_today and oura_today.get("active_calories") is not None
                   else _latest_with("active_calories"))
            if row:
                active_calories_today = row.get("active_calories")
                sources["activity"] = "oura"

        # Backfill active calories from raw Oura daily_activity payloads saved before
        # we promoted active_calories to a first-class SQLite column.
        if active_calories_today is None:
            for row in ([oura_today] if oura_today else []) + list(reversed(oura_week)):
                try:
                    raw = row.get("raw_json") if row else None
                    if isinstance(raw, str):
                        raw = json.loads(raw)
                    acts = (raw or {}).get("daily_activity") or []
                    if acts:
                        val = acts[-1].get("active_calories")
                        if val is not None:
                            active_calories_today = int(round(float(val)))
                            sources["activity"] = "oura_raw"
                            break
                except Exception:
                    continue

        if steps_avg_7d is None:
            step_vals = [r.get("steps") for r in oura_week if r.get("steps") is not None]
            if step_vals:
                steps_avg_7d = int(round(sum(step_vals) / len(step_vals)))

        if not last_night:
            row = (oura_today if oura_today and oura_today.get("sleep_duration_min") is not None
                   else _latest_with("sleep_duration_min"))
            if row:
                dur_min = row.get("sleep_duration_min") or 0
                last_night = {
                    "date": row.get("day"),
                    "total_sleep_min": dur_min,
                    "total_hours": round(dur_min / 60.0, 2) if dur_min else None,
                    "deep_sleep_min": row.get("sleep_deep_min"),
                    "rem_sleep_min": row.get("sleep_rem_min"),
                    "light_sleep_min": row.get("sleep_light_min"),
                    "awake_min": row.get("sleep_awake_min"),
                    "sleep_score": row.get("sleep_score"),
                    "quality_score": row.get("sleep_score"),
                }
                quality_score = row.get("sleep_score")
                sources["sleep"] = "oura"

        if avg_7d_hours is None:
            dur_vals = [r.get("sleep_duration_min") for r in oura_week if r.get("sleep_duration_min") is not None]
            if dur_vals:
                avg_7d_hours = round((sum(dur_vals) / len(dur_vals)) / 60.0, 2)

        if quality_score is None and oura_today and oura_today.get("sleep_score") is not None:
            quality_score = oura_today.get("sleep_score")
    except Exception:
        pass

    try:
        whoop_fact = get_whoop_daily_fact(WHOOP_DB_FILE)
        whoop_freshness = latest_whoop_freshness(WHOOP_DB_FILE)
        if whoop_fact and whoop_freshness.get("status") in {"fresh", "aging"}:
            if resting_bpm is None and whoop_fact.get("resting_hr") is not None:
                resting_bpm = whoop_fact.get("resting_hr")
                sources["heart_rate"] = "whoop"
            if not last_night and whoop_fact.get("sleep_performance_pct") is not None:
                last_night = {
                    "date": whoop_fact.get("local_date"),
                    "total_sleep_min": None,
                    "total_hours": None,
                    "sleep_score": whoop_fact.get("sleep_performance_pct"),
                    "quality_score": whoop_fact.get("sleep_performance_pct"),
                    "sleep_need_gap_min": whoop_fact.get("sleep_need_gap_min"),
                }
                quality_score = whoop_fact.get("sleep_performance_pct")
                sources["sleep"] = "whoop"
    except Exception:
        pass

    return jsonify({
        "weight": {
            "current_lbs": round(current_weight, 1) if current_weight is not None else None,
            "trend_7d": trend_7d,
            "trend_30d": trend_30d,
            "change_7d": change_7d,
            "change_30d": change_30d,
            "body_fat_pct": round(body_fat, 1) if body_fat is not None else None,
        },
        "heart_rate": {
            "resting_bpm": resting_bpm,
            "average_bpm": average_bpm,
            "trend_7d": hr_trend,
        },
        "sleep": {
            "last_night": last_night,
            "avg_7d_hours": avg_7d_hours,
            "quality_score": quality_score,
            "sleep_score": quality_score,
        },
        "activity": {
            "steps_today": steps_today,
            "steps_avg_7d": steps_avg_7d,
            "active_calories_today": active_calories_today,
            "active_minutes_today": active_minutes_today,
        },
        "source": sources,
    })


@app.route('/api/acwr')
def api_acwr():
    """Return ACWR (Acute:Chronic Workload Ratio) computed from recent workouts."""
    return jsonify(calculate_acwr(WORKOUTS))


@app.route('/api/add-workout', methods=['POST'])
def add_workout():
    """Add a new workout (legacy endpoint).

    Prefer /api/complete-workout for the primary flow.
    """
    global LAST_WORKOUT_RECOMMENDATION
    data, err = get_json_body(required=True)
    if err:
        return err

    # Minimal validation
    date_s, err2 = _coerce_str(data.get("date"), "date", required=True, max_len=32)
    if err2:
        return err2
    session_type, err2 = _coerce_str(data.get("session_type"), "session_type", required=True, max_len=32)
    if err2:
        return err2
    duration, err2 = _coerce_int(data.get("duration_minutes", 0), "duration_minutes", min_v=0, max_v=600)
    if err2:
        return err2
    exercises = data.get("exercises")
    if exercises is None:
        exercises = []
    if not isinstance(exercises, list):
        return api_error("exercises must be a list", 400, code="invalid_field")

    notes, err2 = _coerce_str(data.get("notes", ""), "notes", required=False, max_len=2000)
    if err2:
        return err2

    entry = {
        "date": date_s,
        "session_type": session_type,
        "duration_minutes": duration,
        "exercises": exercises,
        "overall_fatigue": data.get("overall_fatigue"),
        "notes": notes,
    }

    WORKOUTS.append(entry)
    save_json(WORKOUTS_FILE, WORKOUTS)
    LAST_WORKOUT_RECOMMENDATION = None
    delete_current_workout_plan(_current_data_user_id())
    _notify_workout_logged(entry)
    return jsonify({"status": "success", "workout": entry})


@app.route('/api/add-soreness', methods=['POST'])
def add_soreness():
    """Add soreness data."""
    global LAST_WORKOUT_RECOMMENDATION
    data, err = get_json_body(required=True)
    if err:
        return err

    muscle, err2 = _coerce_str(data.get("muscle"), "muscle", required=True, max_len=64)
    if err2:
        return err2
    level, err2 = _coerce_int(data.get("soreness_level"), "soreness_level", min_v=1, max_v=10)
    if err2:
        return err2
    notes, err2 = _coerce_str(data.get("notes", ""), "notes", required=False, max_len=2000)
    if err2:
        return err2

    entry = {
        "date": data.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "muscle": muscle,
        "soreness_level": level,
        "notes": notes,
        # Stamp with a precise timestamp so we can apply natural time-decay.
        "created_at": datetime.now().isoformat(),
    }

    SORENESS_DATA.append(entry)
    save_json(SORENESS_FILE, SORENESS_DATA)  # Persist to file
    LAST_WORKOUT_RECOMMENDATION = None
    delete_current_workout_plan(_current_data_user_id())
    return jsonify({"status": "success", "soreness": entry})


@app.route('/api/add-nutrition', methods=['POST'])
def add_nutrition():
    """Add a nutrition entry (calories/macros)."""
    data, err = get_json_body(required=True)
    if err:
        return err

    date_s = data.get("date") or _today_str()
    date_s, err2 = _coerce_str(date_s, "date", required=True, max_len=32)
    if err2:
        return err2

    calories, err2 = _coerce_int(data.get("calories"), "calories", min_v=0)
    if err2:
        return err2
    protein_g, err2 = _coerce_float(data.get("protein_g"), "protein_g", min_v=0)
    if err2:
        return err2
    carbs_g, err2 = _coerce_float(data.get("carbs_g"), "carbs_g", min_v=0, allow_none=True)
    if err2:
        return err2
    fat_g, err2 = _coerce_float(data.get("fat_g"), "fat_g", min_v=0, allow_none=True)
    if err2:
        return err2
    sodium_mg, err2 = _coerce_int(data.get("sodium_mg"), "sodium_mg", min_v=0, allow_none=True)
    if err2:
        return err2
    fiber_g, err2 = _coerce_float(data.get("fiber_g"), "fiber_g", min_v=0, allow_none=True)
    if err2:
        return err2
    notes, err2 = _coerce_str(data.get("notes"), "notes", required=False, max_len=500)
    if err2:
        return err2
    item_name, err2 = _coerce_str(data.get("item_name"), "item_name", required=False, max_len=160)
    if err2:
        return err2
    portion_description, err2 = _coerce_str(data.get("portion_description"), "portion_description", required=False, max_len=240)
    if err2:
        return err2
    meal_type, err2 = _coerce_str(data.get("meal_type"), "meal_type", required=False, max_len=64)
    if err2:
        return err2
    context_note, err2 = _coerce_str(data.get("context_note"), "context_note", required=False, max_len=500)
    if err2:
        return err2
    logged_at, err2 = _coerce_str(data.get("logged_at"), "logged_at", required=False, max_len=64)
    if err2:
        return err2
    source_timestamp, err2 = _coerce_str(data.get("source_timestamp"), "source_timestamp", required=False, max_len=64)
    if err2:
        return err2
    source, err2 = _coerce_str(data.get("source"), "source", required=False, max_len=64)
    if err2:
        return err2
    correction_state, err2 = _coerce_str(data.get("correction_state"), "correction_state", required=False, max_len=64)
    if err2:
        return err2
    client_id, err2 = _coerce_str(data.get("client_id"), "client_id", required=False, max_len=128)
    if err2:
        return err2
    client_id = client_id or None
    if client_id is None:
        client_id = f"nutrition-{uuid.uuid4().hex[:16]}"
    confidence, err2 = _coerce_float(data.get("confidence"), "confidence", min_v=0, max_v=100, allow_none=True)
    if err2:
        return err2

    entry = {
        "date": date_s,
        "calories": calories,
        "protein_g": round(float(protein_g), 1),
        "carbs_g": round(float(carbs_g), 1) if carbs_g is not None else None,
        "fat_g": round(float(fat_g), 1) if fat_g is not None else None,
        "sodium_mg": int(sodium_mg) if sodium_mg is not None else None,
        "fiber_g": round(float(fiber_g), 1) if fiber_g is not None else None,
        "notes": notes,
    }
    if correction_state:
        entry["correction_state"] = correction_state
    if client_id:
        entry["client_id"] = client_id

    food_log_record = {
        **entry,
        "logged_at": logged_at,
        "source_timestamp": source_timestamp,
        "meal_type": meal_type,
        "item_name": item_name,
        "portion_description": portion_description,
        "context_note": context_note,
        "confidence": round(float(confidence), 3) if confidence is not None else None,
        "source": source,
        "correction_state": correction_state,
        "client_id": client_id,
        "original_estimate": data.get("original_estimate") or data.get("estimate"),
    }
    for field in ("carbs_g", "fat_g", "sodium_mg", "fiber_g", "confidence"):
        if field not in data:
            food_log_record.pop(field, None)
    food_log = add_food_log(_current_data_user_id(), food_log_record)
    if _nutrition_entry_accepted(food_log):
        _enqueue_workout_adaptation_after_accept(_current_data_user_id(), [food_log])

    previous_nutrition_data = list(NUTRITION_DATA)
    try:
        replaced_legacy_entry = False
        if client_id:
            for idx, existing in enumerate(NUTRITION_DATA):
                if isinstance(existing, dict) and existing.get("client_id") == client_id:
                    NUTRITION_DATA[idx] = entry
                    replaced_legacy_entry = True
                    break
        if not replaced_legacy_entry:
            NUTRITION_DATA.append(entry)
        save_json(NUTRITION_FILE, NUTRITION_DATA)
    except Exception:
        NUTRITION_DATA[:] = previous_nutrition_data
        raise

    return jsonify({"status": "success", "nutrition": entry, "food_log": food_log})


_MEAL_INTAKE_MAX_IMAGE_BYTES = 6 * 1024 * 1024  # 6 MB
# FIT-138: cap multi-photo meals; client mirrors MEAL_MAX_PHOTOS in app.js.
_MEAL_INTAKE_MAX_IMAGE_COUNT = 4
# FIT-138: aggregate request cap protects against 4 × 6 MB worst-case while
# allowing realistic batches (a single 6 MB photo plus three smaller ones).
_MEAL_INTAKE_MAX_AGGREGATE_BYTES = 18 * 1024 * 1024  # 18 MB
_MEAL_INTAKE_SUPPORTED_IMAGE_MIMETYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MEAL_ESTIMATE_SAFE_METADATA_FIELDS = (
    "external_food_id",
    "verified_source_url",
    "data_fetched_at",
    "portion_basis",
    "brand_id",
    "underlying_source",
    "underlying_sources",
    "off_attribution",
    "vision_description",
    "vision_provider",
    "vision_confidence",
    "fallback_reason",
)
_FOOD_PHOTO_RETENTION = {
    "policy": "discard_after_extraction",
    "raw_photo_retained": False,
    "raw_model_trace_retained": False,
    "backup_includes_raw_photo": False,
    "message": "Food photos are discarded after extraction; only the final estimate and safe correction metadata are kept.",
}
PENDING_MEAL_REVIEW_TTL_DAYS = 7
_MEAL_ESTIMATE_METADATA_STRING_MAX = 500
_MEAL_ESTIMATE_METADATA_KEY_MAX = 80


def _source_indicates_image(source: str | None) -> bool:
    source_name = str(source or "")
    return source_name == "stub_vision_estimate" or source_name.startswith("vision_")


def _food_photo_retention_payload(has_image: bool = False) -> dict:
    payload = dict(_FOOD_PHOTO_RETENTION)
    payload["image_received"] = bool(has_image)
    return payload


def _meal_intake_public_vision_error(_exc: Exception) -> str:
    return "vision_estimator_failed"


def _meal_intake_vision_contention(exc: Exception) -> bool:
    return (
        isinstance(exc, vision_estimator.VisionEstimatorError)
        and str(exc) == "busy: LM Studio vision inference already running"
    )


def _meal_text_fallback_reason(parsed: dict | None) -> str | None:
    if not isinstance(parsed, dict):
        return None
    reason = parsed.get("fallback_reason")
    return reason if isinstance(reason, str) and reason in FALLBACK_REASON_VALUES else None


def _safe_estimate_metadata_string(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:_MEAL_ESTIMATE_METADATA_STRING_MAX]


def _safe_estimate_metadata_scalar(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric != numeric or numeric in (float("inf"), float("-inf")):
            return None
        return value
    return _safe_estimate_metadata_string(value)


def _safe_estimate_metadata_value(key: str, value):
    if key == "from_image":
        return value if isinstance(value, bool) else None
    if key == "underlying_sources":
        if not isinstance(value, list) or not value:
            return None
        safe = [str(source).strip().lower() for source in value if isinstance(source, str) and str(source).strip()]
        return safe if len(safe) == len(value) else None
    if key == "fallback_reason":
        return value if isinstance(value, str) and value in FALLBACK_REASON_VALUES else None
    if key == "off_attribution":
        if isinstance(value, dict):
            safe = {}
            for raw_key, raw_item in value.items():
                if not isinstance(raw_key, str):
                    continue
                safe_key = raw_key.strip()[:_MEAL_ESTIMATE_METADATA_KEY_MAX]
                safe_item = _safe_estimate_metadata_scalar(raw_item)
                if safe_key and safe_item is not None:
                    safe[safe_key] = safe_item
            return safe or None
        return _safe_estimate_metadata_string(value)
    if key == "vision_confidence":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        if numeric < 0 or numeric > 1 or numeric != numeric:
            return None
        return round(numeric, 2)
    return _safe_estimate_metadata_string(value)


def _preserve_safe_estimate_metadata(estimate: dict, raw: dict) -> dict:
    for key in _MEAL_ESTIMATE_SAFE_METADATA_FIELDS:
        safe_value = _safe_estimate_metadata_value(key, raw.get(key))
        if safe_value is not None:
            estimate[key] = safe_value
    return estimate


def _vision_lookup_allowed_for_text(text_raw: str, vision: dict) -> bool:
    if not text_raw:
        return True
    try:
        if branded_food_lookup.should_attempt_direct_lookup(text_raw):
            return True
    except Exception:
        return False
    if vision.get("ambiguous"):
        return True
    portion_hint = str(vision.get("portion_hint") or "").lower()
    return "half" in text_raw.lower() and "half" in portion_hint


def _vision_item_quantity_text(quantity) -> str:
    try:
        numeric = float(quantity)
    except (TypeError, ValueError):
        numeric = 1.0
    if numeric <= 0:
        numeric = 1.0
    if numeric.is_integer():
        return str(int(numeric))
    return str(round(numeric, 2)).rstrip("0").rstrip(".")


def _vision_item_modifiers(item: dict) -> list[str]:
    raw = item.get("modifiers")
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        values = [raw]
    else:
        values = []
    modifiers = []
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned:
            modifiers.append(cleaned)
    return modifiers


def _vision_item_lookup_query(item: dict, *, extra_portion_hints: list[str] | None = None) -> str | None:
    if not isinstance(item, dict):
        return None
    item_name = str(item.get("item_name") or "").strip()
    if not item_name:
        return None
    seen_hints = {str(item.get("portion_hint") or "").strip().lower()}
    portion_hints = [str(item.get("portion_hint") or "").strip()]
    for hint in extra_portion_hints or []:
        cleaned = str(hint or "").strip()
        existing = " ".join(portion_hints).lower()
        if cleaned and cleaned.lower() not in seen_hints and cleaned.lower() not in existing:
            portion_hints.append(cleaned)
            seen_hints.add(cleaned.lower())
    parts = [
        _vision_item_quantity_text(item.get("quantity", 1)),
        str(item.get("brand") or "").strip(),
        item_name,
        *_vision_item_modifiers(item),
        *portion_hints,
    ]
    query = " ".join(part for part in parts if part)
    return query[:500] or None


def _vision_text_portion_hint(text_raw: str | None) -> str | None:
    text = str(text_raw or "").strip()
    if not text:
        return None
    normalized = re.sub(r"\s+", " ", text.lower())
    if re.search(r"(^|\s)(half|1/2)(\s|$)", normalized):
        return "half"
    portion_tokens = {
        "half", "1/2", "one half", "quarter", "1/4", "small", "medium",
        "large", "extra large", "regular", "kids", "kid", "single",
        "double", "triple",
    }
    if normalized in portion_tokens:
        return text
    if re.fullmatch(r"(\d+(\.\d+)?|one|two|three|four)\s+(cup|cups|oz|ounce|ounces|g|gram|grams|lb|lbs|piece|pieces|slice|slices|serving|servings)", normalized):
        return text
    return None


def _vision_extra_item_portion_hints(vision: dict, text_raw: str | None) -> list[str]:
    items = vision.get("items")
    if not isinstance(items, list) or len(items) != 1:
        return []
    hints = []
    portion_hint = str(vision.get("portion_hint") or "").strip()
    if portion_hint:
        hints.append(portion_hint)
    text_hint = _vision_text_portion_hint(text_raw)
    if text_hint and text_hint.lower() not in " ".join(hints).lower():
        hints.append(text_hint)
    return hints


def _vision_items_include_portion_hint(items: list, portion_hint: str | None, vision: dict) -> bool:
    hint = str(portion_hint or "").strip().lower()
    if not hint:
        return True
    top_level_hint = str(vision.get("portion_hint") or "").strip().lower()
    if hint in top_level_hint:
        return True
    for item in items:
        if not isinstance(item, dict):
            continue
        values = [
            str(item.get("portion_hint") or ""),
            " ".join(_vision_item_modifiers(item)),
        ]
        if hint in " ".join(values).lower():
            return True
    return False


def _vision_item_label(item: dict, *, include_modifiers: bool = False) -> str:
    quantity = _vision_item_quantity_text(item.get("quantity", 1))
    label = f"{quantity} {str(item.get('item_name') or '').strip()}".strip()
    modifiers = _vision_item_modifiers(item)
    portion_hint = str(item.get("portion_hint") or "").strip()
    if include_modifiers and portion_hint and portion_hint.lower() not in " ".join(modifiers).lower():
        modifiers.append(portion_hint)
    if include_modifiers and modifiers:
        label = f"{label} ({', '.join(modifiers)})"
    return label


def _vision_structured_items_lookup(vision: dict, *, user_id: int, text_raw: str | None = None) -> dict | None:
    items = vision.get("items")
    if not isinstance(items, list) or not items:
        return None

    text_portion_hint = _vision_text_portion_hint(text_raw)
    if len(items) != 1 and not _vision_items_include_portion_hint(items, text_portion_hint, vision):
        return None
    extra_portion_hints = _vision_extra_item_portion_hints(vision, text_raw)
    matched: list[tuple[dict, dict, str]] = []
    missing: list[str] = [_vision_item_label(item) for item in items[8:] if isinstance(item, dict)]
    for item in items[:8]:
        item_for_lookup = dict(item)
        if extra_portion_hints and not str(item_for_lookup.get("portion_hint") or "").strip():
            item_for_lookup["portion_hint"] = " ".join(extra_portion_hints)
        query = _vision_item_lookup_query(item_for_lookup, extra_portion_hints=extra_portion_hints)
        if not query:
            continue
        try:
            lookup = branded_food_lookup.lookup(query, user_id=user_id)
        except Exception:
            lookup = None
        if lookup:
            matched.append((item_for_lookup, lookup, query))
        else:
            missing.append(_vision_item_label(item_for_lookup) or query)

    if not matched:
        return None
    return _combine_vision_item_lookups(matched, missing=missing)


def _sum_lookup_number(matched: list[tuple[dict, dict, str]], key: str):
    total = 0.0
    found = False
    for _item, lookup, _query in matched:
        value = lookup.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        total += float(value)
        found = True
    if not found:
        return 0
    if key in {"calories", "sodium_mg"}:
        return int(round(total))
    return round(total, 1)


def _combine_vision_item_lookups(
    matched: list[tuple[dict, dict, str]],
    *,
    missing: list[str],
) -> dict:
    first_brand = str(matched[0][0].get("brand") or "").strip()
    same_brand = first_brand and all(str(item.get("brand") or "").strip() == first_brand for item, _lookup, _query in matched)
    labels = [_vision_item_label(item) for item, _lookup, _query in matched]
    portion_labels = [_vision_item_label(item, include_modifiers=True) for item, _lookup, _query in matched]
    effective_sources = set()
    complete_effective_provenance = True
    for _item, lookup, _query in matched:
        source_value = str(lookup.get("source") or "").strip().lower()
        underlying_source = str(lookup.get("underlying_source") or "").strip().lower()
        if source_value == "mixed_lookup":
            underlying_sources = lookup.get("underlying_sources")
            if (
                isinstance(underlying_sources, list)
                and underlying_sources
                and not bool(lookup.get("ambiguous"))
                and all(
                    isinstance(value, str)
                    and value.strip().lower() in _SOURCE_BACKED_NUTRITION_SOURCES
                    for value in underlying_sources
                )
            ):
                effective_sources.update(value.strip().lower() for value in underlying_sources)
            else:
                complete_effective_provenance = False
        elif source_value == "personal_vocab":
            complete_effective_provenance = False
        elif source_value in _SOURCE_BACKED_NUTRITION_SOURCES:
            effective_sources.add(source_value)
        elif underlying_source in _SOURCE_BACKED_NUTRITION_SOURCES:
            effective_sources.add(underlying_source)
        else:
            complete_effective_provenance = False
    source = next(iter(effective_sources)) if complete_effective_provenance and len(effective_sources) == 1 else "mixed_lookup"
    confidences = [
        float(lookup.get("confidence"))
        for _item, lookup, _query in matched
        if isinstance(lookup.get("confidence"), (int, float)) and not isinstance(lookup.get("confidence"), bool)
    ]
    notes = []
    for _item, lookup, _query in matched:
        notes.extend(str(note).strip() for note in (lookup.get("uncertainty_notes") or []) if str(note).strip())
    if missing:
        notes.append("Some cart items did not match verified nutrition data: " + ", ".join(missing[:3]))
    if not complete_effective_provenance:
        notes.append("Some cart items lacked verifiable source provenance.")
    item_name = "; ".join(labels)
    if same_brand:
        item_name = f"{first_brand} order: {item_name}"
    estimate = {
        "item_name": item_name[:160],
        "portion_description": "; ".join(portion_labels)[:300],
        "meal_type": _infer_structured_order_meal_type(matched),
        "calories": _sum_lookup_number(matched, "calories"),
        "protein_g": _sum_lookup_number(matched, "protein_g"),
        "carbs_g": _sum_lookup_number(matched, "carbs_g"),
        "fat_g": _sum_lookup_number(matched, "fat_g"),
        "sodium_mg": _sum_lookup_number(matched, "sodium_mg"),
        "fiber_g": _sum_lookup_number(matched, "fiber_g"),
        "confidence": min(confidences) if confidences else 0.72,
        "ambiguous": (
            bool(missing)
            or not complete_effective_provenance
            or any(bool(lookup.get("ambiguous")) for _item, lookup, _query in matched)
        ),
        "uncertainty_notes": notes,
        "source": source,
        "underlying_source": source,
        "underlying_sources": sorted(effective_sources),
        "portion_basis": "Structured vision item lookup: " + "; ".join(query for _item, _lookup, query in matched)[:700],
    }
    return estimate


def _infer_structured_order_meal_type(matched: list[tuple[dict, dict, str]]) -> str:
    for _item, lookup, _query in matched:
        meal_type = str(lookup.get("meal_type") or "").strip()
        if meal_type:
            return meal_type
    joined = " ".join(
        " ".join([
            str(item.get("item_name") or ""),
            " ".join(_vision_item_modifiers(item)),
        ]).lower()
        for item, _lookup, _query in matched
    )
    if any(token in joined for token in ("breakfast", "egg", "biscuit", "taco")):
        return "breakfast"
    return "snack"


def _meal_vocab_learning_phrase(text_hint: str | None, estimate: dict) -> str | None:
    vision_description = estimate.get("vision_description") if isinstance(estimate, dict) else None
    if (
        isinstance(estimate, dict)
        and _source_indicates_image(estimate.get("source"))
        and isinstance(vision_description, str)
        and vision_description.strip()
        and len(str(text_hint or "").split()) <= 2
    ):
        return vision_description.strip()[:500]
    candidates = (
        text_hint,
        estimate.get("personal_vocab_phrase") if isinstance(estimate, dict) else None,
        vision_description,
        estimate.get("item_name") if isinstance(estimate, dict) else None,
    )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return None


def _meal_intake_vision_estimate(
    image_bytes: bytes | None = None,
    *,
    images: list[tuple[bytes, str]] | None = None,
    text_raw: str,
    mimetype: str | None = None,
    user_id: int,
) -> dict:
    """Vision estimate covering one or more photos as a single combined call.

    FIT-138: ``images`` carries a list of ``(bytes, mimetype)`` tuples and
    becomes the canonical input. The legacy ``image_bytes`` + ``mimetype``
    kwargs are preserved for back-compat with any caller still using the
    single-image signature.
    """
    if images is None:
        if image_bytes:
            images = [(image_bytes, mimetype or "image/jpeg")]
        else:
            images = []
    if not images:
        raise vision_estimator.VisionEstimatorError("no image data provided")
    vision = vision_estimator.describe(
        images=images,
        context_text=text_raw or None,
    )
    description = vision["item_description"]
    lookup_text = " ".join(part for part in (text_raw, description, vision.get("portion_hint")) if part)
    lookup = None
    label_ocr = vision.get("label_ocr") is True and isinstance(vision.get("macro_estimate"), dict)
    if not label_ocr:
        try:
            lookup = _vision_structured_items_lookup(vision, user_id=user_id, text_raw=text_raw)
        except Exception:
            lookup = None
        if _vision_lookup_allowed_for_text(text_raw, vision):
            try:
                if not lookup:
                    lookup = branded_food_lookup.lookup(lookup_text, user_id=user_id)
            except Exception:
                lookup = None
    provider = vision.get("provider") or vision_estimator.configured_provider()
    if lookup:
        estimate = dict(lookup)
        estimate.setdefault("underlying_source", lookup.get("source"))
        estimate["source"] = f"vision_{provider}+{lookup.get('source')}"
        estimate["vision_description"] = description
        estimate["vision_provider"] = provider
        estimate["vision_confidence"] = vision.get("confidence")
        estimate["confidence"] = min(float(estimate.get("confidence") or 0), float(vision.get("confidence") or 0))
        if vision.get("portion_hint") and not estimate.get("portion_description"):
            estimate["portion_description"] = vision.get("portion_hint")
        elif not estimate.get("portion_description") and vision.get("items"):
            estimate["portion_description"] = vision.get("portion_hint")
        if vision.get("ambiguous"):
            estimate["ambiguous"] = True
            estimate.setdefault("uncertainty_notes", [])
            estimate["uncertainty_notes"].extend(vision.get("uncertainty_notes") or [])
        return estimate

    macro_estimate = vision.get("macro_estimate")
    if isinstance(macro_estimate, dict):
        confidence_cap = 0.9 if label_ocr else 0.65
        source_name = f"vision_{provider}_label_ocr" if label_ocr else f"vision_{provider}_estimate"
        raw = {
            "item_name": description,
            "portion_description": vision.get("portion_hint"),
            "meal_type": macro_estimate.get("meal_type") or "snack",
            "calories": macro_estimate.get("calories"),
            "protein_g": macro_estimate.get("protein_g"),
            "carbs_g": macro_estimate.get("carbs_g"),
            "fat_g": macro_estimate.get("fat_g"),
            "sodium_mg": macro_estimate.get("sodium_mg", 0),
            "fiber_g": macro_estimate.get("fiber_g", 0),
            "confidence": min(float(vision.get("confidence") or 0), confidence_cap),
            "ambiguous": bool(vision.get("ambiguous", True)),
            "uncertainty_notes": vision.get("uncertainty_notes") or [
                "Nutrition label values were read from the photo; confirm serving size before logging."
                if label_ocr else "Vision model estimated macros without a verified nutrition source."
            ],
            "source": source_name,
            "portion_basis": "Photo nutrition-label OCR" if label_ocr else None,
        }
        try:
            estimate = sanitize_meal_estimate(raw, plausible_ranges=True)
        except MealEstimateValidationError:
            estimate = manual_review_estimate(text=description, source=source_name)
            estimate["confidence"] = min(float(vision.get("confidence") or 0), 0.45)
            estimate["uncertainty_notes"] = vision.get("uncertainty_notes") or [
                "Vision model returned incomplete nutrition details; review before logging."
            ]
    else:
        estimate = manual_review_estimate(text=description, source=f"vision_{provider}_estimate")
        estimate["confidence"] = min(float(vision.get("confidence") or 0), 0.45)
        estimate["uncertainty_notes"] = vision.get("uncertainty_notes") or [
            "Vision identified the food but no verified nutrition source matched."
        ]
    estimate["vision_description"] = description
    estimate["vision_provider"] = provider
    estimate["vision_confidence"] = vision.get("confidence")
    return estimate


# FIT-61: human-readable copy for each stable policy reason code. Kept
# next to the endpoint (not in meal_log_policy) so the policy module
# stays pure and free of UI strings — i18n can swap this map later.
_POLICY_REASON_NOTES = {
    "low_confidence": "AI is unsure about this estimate — confirm before it counts.",
    "medium_confidence": "Confidence is moderate — a quick double-check is recommended.",
    "ambiguous_input": "Portion or items are unclear — confirm before it counts.",
    "implausible_calories": "Calorie estimate looks off — please review.",
    "implausible_macros": "Macros look unusually high — please review.",
    "implausible_sodium": "Sodium estimate looks unusually high — please review.",
    "missing_calories": "Calories are missing from the estimate — please enter manually.",
    "branded_combo_ai_only": "This branded combo is AI-only; replace it with a source-backed entry or make a material correction before it can count.",
}

_REVIEW_SNAPSHOT_TRUST_TOKEN = object()

_MEAL_ESTIMATE_PROVENANCE_FIELDS = (
    "external_food_id",
    "verified_source_url",
    "data_fetched_at",
    "portion_basis",
    "brand_id",
    "underlying_source",
    "underlying_sources",
    "off_attribution",
    "personal_vocab_phrase",
    "vision_description",
    "vision_provider",
    "vision_confidence",
    "from_image",
)


def _copy_meal_estimate_provenance(estimate: dict, raw_estimate: dict) -> None:
    """Keep safe lookup provenance after schema validation drops unknown fields."""
    if not isinstance(raw_estimate, dict):
        return
    for key in _MEAL_ESTIMATE_PROVENANCE_FIELDS:
        safe_value = _safe_estimate_metadata_value(key, raw_estimate.get(key))
        if safe_value is not None:
            estimate[key] = safe_value
    if isinstance(raw_estimate.get("items"), list):
        estimate["items"] = raw_estimate.get("items")


def _honest_material_correction_estimate(
    estimate: dict,
    original: dict | None,
    *,
    imported_untrusted: bool = False,
) -> dict:
    corrected = dict(estimate)
    for key in _MEAL_ESTIMATE_PROVENANCE_FIELDS:
        corrected.pop(key, None)
    corrected["source"] = "manual_review_estimate"
    if isinstance(original, dict) and original.get("from_image") is True:
        corrected["from_image"] = True
    return corrected


def _server_snapshot_estimate_with_photo_provenance(estimate: dict, original: dict | None) -> dict:
    current = dict(estimate)
    if isinstance(original, dict) and original.get("from_image") is True:
        current["from_image"] = True
    return current


def _merge_policy_reasons_into_uncertainty_notes(estimate: dict, reasons: list) -> None:
    """Append human-readable notes for each policy reason to estimate.uncertainty_notes.

    The composer's pending-review card renders estimate.uncertainty_notes
    directly (it doesn't yet know about the sibling policy.reasons block),
    so policy-only pending decisions (e.g. medium_confidence, implausible
    macros) would otherwise show a review card with no explanation. This
    keeps the existing UI contract working while the JS catches up.

    De-duplicates against existing notes (case-insensitive) so the same
    message isn't shown twice when the parser already added it.
    """
    if not reasons:
        return
    existing = estimate.setdefault("uncertainty_notes", [])
    existing_lower = {str(n).strip().lower() for n in existing if isinstance(n, str)}
    for code in reasons:
        note = _POLICY_REASON_NOTES.get(code)
        if note and note.strip().lower() not in existing_lower:
            existing.append(note)
            existing_lower.add(note.strip().lower())


def _pending_meal_review_cutoff_date(now=None) -> str:
    """Return the oldest date still shown in pending meal review."""
    now = now or datetime.now()
    return (now.date() - timedelta(days=PENDING_MEAL_REVIEW_TTL_DAYS)).isoformat()


def _pending_meal_review_entries(user_id: int, *, now=None) -> list[dict]:
    cutoff_date = _pending_meal_review_cutoff_date(now)
    entries = []
    for entry in get_food_logs(user_id, since=cutoff_date):
        entry_day = _nutrition_entry_day(entry)
        if (
            _nutrition_entry_pending_review(entry)
            and entry.get("client_id")
            and entry_day
            and entry_day >= cutoff_date
        ):
            entries.append(entry)
    return entries


def _cleanup_stale_pending_meal_reviews(user_id: int, *, now=None) -> int:
    cutoff_date = _pending_meal_review_cutoff_date(now)
    removed = 0
    for entry in get_food_logs(user_id):
        client_id = entry.get("client_id")
        entry_day = _nutrition_entry_day(entry)
        if (
            client_id
            and entry_day
            and entry_day < cutoff_date
            and _nutrition_entry_pending_review(entry)
            and delete_food_log_by_client_id(user_id, client_id)
        ):
            delete_meal_review_snapshot(user_id, client_id)
            removed += 1
    return removed


def _meal_pending_review_response_payload(user_id: int, entry: dict) -> dict:
    client_id = entry.get("client_id")
    snapshot = get_meal_review_snapshot(user_id, client_id) if client_id else None
    if snapshot:
        payload = copy.deepcopy(snapshot["payload"])
        _review_normalize_imported_canes_payload(payload)
        if bool(payload.get("_imported_snapshot_untrusted")):
            if not payload.get("items") and _review_payload_is_ai_only_canes_combo(payload):
                estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
                original = payload.get("original_estimate") if isinstance(payload.get("original_estimate"), dict) else estimate
                legacy_item = _review_item_from_estimate(
                    estimate,
                    item_id="item-1",
                    item_order=1,
                    text=payload.get("text"),
                    original_estimate=original,
                    branded_combo_ai_only=True,
                )
                legacy_item["_imported_snapshot_untrusted"] = True
                payload["items"] = [legacy_item]
                payload = _review_recalculate(payload)
        derived_branded_marker = False
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            estimate = item.get("estimate") if isinstance(item.get("estimate"), dict) else {}
            if _review_snapshot_item_branded_combo_ai_only(item, estimate):
                item["branded_combo_ai_only"] = True
                derived_branded_marker = True
        if derived_branded_marker:
            payload = _review_recalculate(payload)
        payload["client_id"] = payload.get("client_id") or payload.get("meal_id") or client_id
        payload.setdefault("meal_id", client_id)
        payload.setdefault("estimate", _meal_pending_review_payload(entry)["estimate"])
        payload.setdefault("food_log", entry)
        payload.setdefault("logged_at", entry.get("logged_at"))
        payload["text_hint"] = entry.get("context_note") or payload.get("text_hint") or ""
        if any(
            isinstance(item, dict) and bool(item.get("branded_combo_ai_only"))
            for item in payload.get("items") or []
        ):
            policy = dict(payload.get("policy") or {})
            reasons = list(policy.get("reasons") or [])
            if "branded_combo_ai_only" not in reasons:
                reasons.append("branded_combo_ai_only")
            policy["reasons"] = reasons
            payload["policy"] = policy
        return payload
    return _meal_pending_review_payload(entry)


def _food_log_by_client_id(user_id: int, client_id: str) -> dict | None:
    if not client_id:
        return None
    for entry in get_food_logs(user_id):
        if entry.get("client_id") == client_id:
            return entry
    return None


def _delete_legacy_nutrition_by_client_id(client_id: str) -> bool:
    if not client_id or not isinstance(NUTRITION_DATA, list):
        return False
    previous = list(NUTRITION_DATA)
    kept = [
        entry for entry in NUTRITION_DATA
        if not isinstance(entry, dict) or str(entry.get("client_id") or "") != client_id
    ]
    if len(kept) == len(NUTRITION_DATA):
        return False
    try:
        NUTRITION_DATA[:] = kept
        save_json(NUTRITION_FILE, NUTRITION_DATA)
    except Exception:
        NUTRITION_DATA[:] = previous
        raise
    return True


def _meal_pending_review_payload(entry: dict) -> dict:
    estimate = dict(entry.get("original_estimate") or {})
    if not estimate:
        estimate = {
            "item_name": entry.get("item_name"),
            "portion_description": entry.get("portion_description"),
            "meal_type": entry.get("meal_type"),
            "calories": entry.get("calories"),
            "protein_g": entry.get("protein_g"),
            "carbs_g": entry.get("carbs_g"),
            "fat_g": entry.get("fat_g"),
            "sodium_mg": entry.get("sodium_mg"),
            "fiber_g": entry.get("fiber_g"),
            "confidence": entry.get("confidence"),
            "source": entry.get("source"),
            "uncertainty_notes": [],
        }
    if _source_indicates_image(entry.get("source")):
        estimate["from_image"] = True
    try:
        decision = evaluate_meal_log(estimate)
        _merge_policy_reasons_into_uncertainty_notes(estimate, decision["reasons"])
        policy = {
            "confidence_band": decision["confidence_band"],
            "reasons": decision["reasons"],
        }
    except Exception:
        policy = {"confidence_band": "unknown", "reasons": ["pending_review"]}
    payload = {
        "client_id": entry.get("client_id"),
        "estimate": estimate,
        "text_hint": entry.get("context_note") or "",
        "logged_at": entry.get("logged_at"),
        "policy": policy,
    }
    imported_pending_untrusted = isinstance(estimate, dict) and estimate.get("_imported_pending_untrusted") is True
    if (
        (_is_canes_box_combo(estimate) or _is_canes_box_combo_text(entry.get("context_note")))
        and (imported_pending_untrusted or not _is_source_backed_nutrition(estimate))
    ):
        item = _review_item_from_estimate(
            estimate,
            item_id="item-1",
            item_order=1,
            text=entry.get("context_note"),
            original_estimate=entry.get("original_estimate") if isinstance(entry.get("original_estimate"), dict) else estimate,
            branded_combo_ai_only=True,
        )
        payload["items"] = [item]
        payload["save_blocked_item_ids"] = ["item-1"]
        payload["policy"] = {
            **policy,
            "reasons": [*policy.get("reasons", []), "branded_combo_ai_only"],
        }
    return payload


def _meal_verified_refresh_event_metadata(
    estimate: dict,
    *,
    correction_state: str,
    source: str,
    now_iso: str,
) -> dict | None:
    if correction_state != CORRECTION_STATE_ACCEPTED or not isinstance(estimate, dict):
        return None
    underlying_source = estimate.get("underlying_source")
    source_url = estimate.get("verified_source_url")
    external_food_id = estimate.get("external_food_id")
    if not (underlying_source or source_url or external_food_id):
        return None
    return {
        "source_label": underlying_source or source,
        "source_url": source_url,
        "refreshed_at": estimate.get("data_fetched_at") or now_iso,
        "item_name": estimate.get("item_name"),
    }


def _meal_intake_persist(
    client_id,
    estimate,
    *,
    source,
    has_image,
    text_hint,
    local_timestamp=None,
    local_date=None,
    local_iso=None,
    correction_state=CORRECTION_STATE_ACCEPTED,
    original_estimate=None,
    meal_id=None,
    meal_item_id=None,
    item_index=None,
    item_state=None,
):
    """Persist a food estimate via the canonical food_logs path.

    ``correction_state`` (FIT-61) controls whether the row counts toward
    daily totals: ``"accepted"`` for auto-logged entries, ``"pending_review"``
    for entries that the meal-log policy held back for explicit user
    confirmation. ``_nutrition_entry_accepted`` filters pending rows out
    of nutrition totals and coaching context (see app.py:999).

    ``local_date`` and ``local_iso`` (FIT-66) are browser-local values
    from the composer. They take precedence over the older UTC
    ``local_timestamp`` so persistence is independent of the Flask
    server's timezone. Server time is the fallback.
    """
    now_iso = datetime.now().isoformat(timespec="seconds")
    # Store browser-local wall-clock time when provided. Do not convert
    # ``local_iso`` through the server timezone; downstream readers use
    # naive ``.hour`` and ``date`` fields as the user's local meal time.
    logged_at_iso = (
        _browser_local_iso_from_iso(local_iso)
        or _local_iso_from_iso(local_timestamp)
        or now_iso
    )
    date_str = (
        _browser_local_date_from_value(local_date)
        or _browser_local_date_from_iso(local_iso)
        or _local_date_from_iso(local_timestamp)
        or _today_str()
    )
    original_for_record = dict(original_estimate) if isinstance(original_estimate, dict) else dict(estimate)
    record = {
        "client_id": client_id,
        "date": date_str,
        "logged_at": logged_at_iso,
        "source_timestamp": logged_at_iso,
        "meal_type": estimate.get("meal_type"),
        "item_name": estimate.get("item_name"),
        "portion_description": estimate.get("portion_description"),
        "context_note": text_hint,
        "calories": estimate.get("calories"),
        "protein_g": estimate.get("protein_g"),
        "carbs_g": estimate.get("carbs_g"),
        "fat_g": estimate.get("fat_g"),
        "sodium_mg": estimate.get("sodium_mg"),
        "fiber_g": estimate.get("fiber_g"),
        "confidence": estimate.get("confidence"),
        "source": source,
        "correction_state": correction_state,
        "original_estimate": original_for_record,
        "accepted_estimate": dict(estimate) if correction_state in {CORRECTION_STATE_ACCEPTED, "corrected"} else None,
        "meal_id": meal_id,
        "meal_item_id": meal_item_id,
        "item_index": item_index,
        "item_state": item_state,
    }
    refresh_event = _meal_verified_refresh_event_metadata(
        estimate,
        correction_state=correction_state,
        source=source,
        now_iso=now_iso,
    )
    if refresh_event is not None:
        record["source_refresh_event"] = refresh_event
    return add_food_log(_current_data_user_id(), record)


def _meal_accept_was_corrected(submitted: dict, original: dict | None) -> bool:
    if not isinstance(original, dict):
        return False
    try:
        original_source = original.get("source") if isinstance(original, dict) else None
        sanitized_original = sanitize_meal_estimate(
            original,
            source=original_source or submitted.get("source") or "manual_review_estimate",
            legacy_defaults=True,
            plausible_ranges=True,
        )
    except MealEstimateValidationError:
        return True
    compare_fields = (
        "item_name",
        "portion_description",
        "meal_type",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sodium_mg",
        "fiber_g",
    )
    return any(sanitized_original.get(field) != submitted.get(field) for field in compare_fields)


def _sanitize_original_estimate_for_log(original: dict | None, accepted: dict) -> dict:
    if not isinstance(original, dict):
        return dict(accepted)
    original_source = original.get("source") if isinstance(original.get("source"), str) else None
    try:
        sanitized = sanitize_meal_estimate(
            original,
            source=original_source or accepted.get("source") or "manual_review_estimate",
            legacy_defaults=True,
            plausible_ranges=True,
        )
        _preserve_safe_estimate_metadata(sanitized, original)
    except MealEstimateValidationError:
        return dict(accepted)
    if original.get("from_image") is True or accepted.get("from_image") is True:
        sanitized["from_image"] = True
    return sanitized


_MEAL_ITEM_STATES = {"included", "skipped", "deleted"}
_MEAL_TOTAL_FIELDS = ("calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "fiber_g")


def _meal_safe_identifier(value, *, fallback: str, max_len: int = 128) -> str:
    raw = str(value or "").strip() or fallback
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-") or fallback
    if len(cleaned) <= max_len:
        return cleaned
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]
    return f"{cleaned[: max_len - 17]}-{digest}"


def _meal_item_client_id(parent_client_id: str, item: dict, index: int) -> str:
    prefix = _meal_safe_identifier(parent_client_id, fallback="meal", max_len=96)
    explicit = item.get("client_id")
    if isinstance(explicit, str) and explicit.strip():
        explicit_id = _meal_safe_identifier(explicit, fallback=f"item-{index}", max_len=96)
        return _meal_safe_identifier(f"{prefix}-{explicit_id}", fallback=f"{prefix}-item-{index}", max_len=128)
    basis = str(item.get("item_id") or item.get("meal_item_id") or index)
    digest = hashlib.sha256(f"{parent_client_id}|{basis}|{index}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-item-{digest}"[:128]


def _meal_item_id(item: dict, index: int) -> str:
    return _meal_safe_identifier(
        item.get("item_id") or item.get("meal_item_id") or f"item-{index}",
        fallback=f"item-{index}",
        max_len=128,
    )


def _meal_item_phrase(item: dict) -> str | None:
    for source in (item.get("text"), item.get("estimate"), item.get("original_estimate")):
        if isinstance(source, str) and source.strip():
            return source.strip()[:500]
        if isinstance(source, dict):
            name = source.get("item_name")
            if isinstance(name, str) and name.strip():
                return name.strip()[:500]
    fallback = item.get("item_id") or item.get("meal_item_id")
    return str(fallback).strip()[:500] if fallback else None


def _meal_item_has_image(item: dict) -> bool:
    for estimate in (item.get("estimate"), item.get("original_estimate")):
        if isinstance(estimate, dict):
            if estimate.get("from_image") is True:
                return True
    return False


def _meal_existing_rows(user_id: int, meal_id: str) -> list[dict]:
    if not meal_id:
        return []
    rows = [entry for entry in get_food_logs(user_id) if entry.get("meal_id") == meal_id]
    return sorted(rows, key=lambda row: row.get("item_index") if row.get("item_index") is not None else 10_000)


def _meal_terminal_idempotency_response(user_id: int, meal_id: str, has_image: bool):
    event = get_meal_acceptance_event(user_id, meal_id)
    if not event:
        return None
    rows = _meal_existing_rows(user_id, meal_id)
    if not rows and event.get("status") != "discarded":
        return None
    row_has_image = any(_source_indicates_image(row.get("source")) for row in rows)
    return _meal_multi_response(
        meal_id,
        rows,
        len(rows),
        int(event.get("skipped_count") or 0),
        int(event.get("deleted_count") or 0),
        has_image or row_has_image,
    )


def _meal_capture_idempotency_response(
    *,
    user_id: int,
    client_id: str,
    has_image: bool,
    local_timestamp: str | None,
    local_date: str | None,
    local_iso: str | None,
):
    existing_snapshot = get_meal_review_snapshot(user_id, client_id)
    if existing_snapshot:
        return jsonify(existing_snapshot["payload"])
    existing_food_log = _food_log_by_client_id(user_id, client_id)
    if existing_food_log:
        if _nutrition_entry_pending_review(existing_food_log):
            pending = _meal_pending_review_payload(existing_food_log)
            return jsonify({
                "status": "pending_review",
                "estimate": pending["estimate"],
                "food_log": existing_food_log,
                "photo_retention": _food_photo_retention_payload(
                has_image or pending["estimate"].get("from_image") is True
                ),
                "local_timestamp": local_timestamp,
                "local_date": local_date,
                "local_iso": local_iso,
                "policy": pending["policy"],
            })
        return jsonify({
            "status": "logged",
            "estimate": dict(
                existing_food_log.get("accepted_estimate")
                or existing_food_log.get("original_estimate")
                or {}
            ),
            "food_log": existing_food_log,
            "photo_retention": _food_photo_retention_payload(
                has_image or _source_indicates_image(existing_food_log.get("source"))
            ),
            "local_timestamp": local_timestamp,
            "local_date": local_date,
            "local_iso": local_iso,
        })
    return _meal_terminal_idempotency_response(user_id, client_id, has_image)


def _meal_totals(food_logs: list[dict]) -> dict:
    totals = {}
    for field in _MEAL_TOTAL_FIELDS:
        total = sum(float(row.get(field) or 0) for row in food_logs)
        if field in {"calories", "sodium_mg"}:
            totals[field] = int(round(total))
        else:
            totals[field] = round(total, 1)
    return totals


def _meal_multi_response(meal_id: str, rows: list[dict], included_count: int, skipped_count: int, deleted_count: int, has_image: bool):
    return jsonify({
        "status": "logged" if included_count else "discarded",
        "meal_id": meal_id,
        "food_logs": rows,
        "meal_totals": _meal_totals(rows),
        "included_count": included_count,
        "skipped_count": skipped_count,
        "deleted_count": deleted_count,
        "photo_retention": _food_photo_retention_payload(has_image),
    })


_REVIEW_STATUS_VALUES = {"included", "skipped", "deleted"}
_REVIEW_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
_REVIEW_REQUEST_ID_KINDS = {"add_item", "edit_portion", "choose_candidate", "followup_answer"}
_REVIEW_PROHIBITED_ESTIMATE_KEYS = {
    "verified_source_url",
    "raw_model_trace",
    "raw_trace",
    "prompt",
    "messages",
    "image_bytes",
    "image",
    "provider_payload",
    "vendor_payload",
    "candidates",
}


def _review_safe_str(value, *, max_len: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


def _review_source_from_estimate(estimate: dict, *, default_kind: str = "estimate") -> dict:
    raw = estimate.get("source") if isinstance(estimate, dict) else None
    source_text = _review_safe_str(raw, max_len=80)
    if not source_text:
        return {"kind": default_kind, "label": None, "link": None}
    if source_text in {"manual_review_estimate", "manual_text_review", "user"}:
        return {"kind": "user", "label": None, "link": None}
    return {"kind": source_text, "label": source_text, "link": None}


def _review_strip_private_estimate_fields(estimate: dict) -> dict:
    safe = {}
    for key, value in estimate.items():
        if key in _REVIEW_PROHIBITED_ESTIMATE_KEYS:
            continue
        safe[key] = value
    safe.pop("verified_source_url", None)
    return safe


def _review_sanitize_estimate(raw: dict, *, source: str | None = None) -> dict:
    raw = dict(raw) if isinstance(raw, dict) else {}
    source_hint = source or raw.get("source") or "manual_review_estimate"
    try:
        estimate = sanitize_meal_estimate(
            raw,
            source=source_hint,
            legacy_defaults=True,
            plausible_ranges=False,
        )
    except MealEstimateValidationError:
        estimate = manual_review_estimate(
            text=_review_safe_str(raw.get("item_name") or raw.get("text") or "food item") or "food item",
            source=source_hint,
        )
    for key in (
        "external_food_id",
        "data_fetched_at",
        "portion_basis",
        "brand_id",
        "underlying_source",
        "underlying_sources",
        "off_attribution",
        "personal_vocab_phrase",
        "vision_description",
        "vision_provider",
        "vision_confidence",
        "fallback_reason",
    ):
        safe_value = _safe_estimate_metadata_value(key, raw.get(key))
        if safe_value is not None:
            estimate[key] = safe_value
    if raw.get("from_image") is True:
        estimate["from_image"] = True
    estimate = _review_strip_private_estimate_fields(estimate)
    if isinstance(raw.get("clarification_question"), str):
        estimate["clarification_question"] = raw["clarification_question"].strip()[:240]
    return estimate


def _review_sanitize_candidates(raw_candidates) -> list[dict]:
    if not isinstance(raw_candidates, list):
        return []
    candidates = []
    for index, raw in enumerate(raw_candidates[:5], start=1):
        if not isinstance(raw, dict):
            continue
        candidate_id = _meal_safe_identifier(
            raw.get("candidate_id") or raw.get("id") or f"candidate-{index}",
            fallback=f"candidate-{index}",
            max_len=64,
        )
        raw_estimate = raw.get("estimate") if isinstance(raw.get("estimate"), dict) else raw
        estimate = _review_sanitize_estimate(raw_estimate, source=raw_estimate.get("source"))
        candidates.append({
            "candidate_id": candidate_id,
            "name": estimate.get("item_name"),
            "portion": estimate.get("portion_description"),
            "calories": estimate.get("calories"),
            "protein_g": estimate.get("protein_g"),
            "carbs_g": estimate.get("carbs_g"),
            "fat_g": estimate.get("fat_g"),
            "sodium_mg": estimate.get("sodium_mg"),
            "fiber_g": estimate.get("fiber_g"),
            "confidence": estimate.get("confidence"),
            "unclear": bool(estimate.get("ambiguous")),
            "source": _review_source_from_estimate(estimate),
            "source_backed": _is_source_backed_nutrition(estimate),
            "estimate": estimate,
        })
    return candidates


def _review_item_from_estimate(
    estimate: dict,
    *,
    item_id: str,
    item_order: int,
    status: str = "included",
    text: str | None = None,
    candidates=None,
    original_estimate: dict | None = None,
    branded_combo_ai_only: bool = False,
    server_source_backed_candidate: bool = False,
) -> dict:
    estimate = _review_sanitize_estimate(estimate, source=estimate.get("source") if isinstance(estimate, dict) else None)
    original = (
        _review_sanitize_estimate(original_estimate, source=original_estimate.get("source") if isinstance(original_estimate, dict) else None)
        if isinstance(original_estimate, dict)
        else dict(estimate)
    )
    status = status if status in _REVIEW_STATUS_VALUES else "included"
    return {
        "item_id": item_id,
        "item_order": item_order,
        "status": status,
        "name": estimate.get("item_name"),
        "portion": estimate.get("portion_description"),
        "calories": estimate.get("calories"),
        "protein_g": estimate.get("protein_g"),
        "carbs_g": estimate.get("carbs_g"),
        "fat_g": estimate.get("fat_g"),
        "sodium_mg": estimate.get("sodium_mg"),
        "fiber_g": estimate.get("fiber_g"),
        "confidence": estimate.get("confidence"),
        "source": _review_source_from_estimate(estimate),
        "unclear": bool(estimate.get("ambiguous")),
        "candidates": _review_sanitize_candidates(candidates if candidates is not None else estimate.get("candidates")),
        "estimate": estimate,
        "original_estimate": original,
        "branded_combo_ai_only": bool(branded_combo_ai_only),
        "server_source_backed_candidate": bool(server_source_backed_candidate),
    }


def _review_candidate_to_item(candidate: dict, item: dict) -> dict:
    estimate = _review_sanitize_estimate(candidate.get("estimate") or candidate)
    updated = dict(item)
    imported_candidate = bool(item.get("_imported_candidates_untrusted"))
    updated.update({
        "name": estimate.get("item_name"),
        "portion": estimate.get("portion_description"),
        "calories": estimate.get("calories"),
        "protein_g": estimate.get("protein_g"),
        "carbs_g": estimate.get("carbs_g"),
        "fat_g": estimate.get("fat_g"),
        "sodium_mg": estimate.get("sodium_mg"),
        "fiber_g": estimate.get("fiber_g"),
        "confidence": estimate.get("confidence"),
        "source": _review_source_from_estimate(estimate),
        "unclear": bool(estimate.get("ambiguous")),
        "candidates": [],
        "estimate": estimate,
        "server_source_backed_candidate": (
            not imported_candidate and _is_source_backed_nutrition(estimate)
        ),
    })
    if imported_candidate:
        updated["_untrusted_imported_candidate"] = True
    else:
        updated.pop("_untrusted_imported_candidate", None)
    return updated


def _review_item_is_blocked(item: dict) -> bool:
    if item.get("status") != "included":
        return False
    if _canes_box_combo_ai_only_needs_review(item):
        return True
    if bool(item.get("unclear")):
        return True
    confidence = item.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or float(confidence) < MEDIUM_CONFIDENCE_THRESHOLD:
        return True
    calories = item.get("calories")
    if isinstance(calories, bool) or not isinstance(calories, (int, float)):
        return True
    if calories < 0 or calories > CALORIE_MAX:
        return True
    for field in ("protein_g", "carbs_g", "fat_g", "fiber_g"):
        value = item.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > MACRO_GRAM_MAX):
            return True
    sodium = item.get("sodium_mg")
    return bool(sodium is not None and (isinstance(sodium, bool) or not isinstance(sodium, (int, float)) or sodium < 0 or sodium > SODIUM_MG_MAX))


_CANES_TOKEN_PATTERN = re.compile(r"(?:^|\s)canes(?:$|\s)")
_BOX_COMBO_PATTERN = re.compile(r"(?:^|\s)box\s+combo(?:$|\s)")
_SOURCE_BACKED_NUTRITION_SOURCES = {
    "nutritionix",
    "usda_fdc",
    "open_food_facts",
    "heb_product_page",
}


def _is_canes_box_combo(estimate: dict) -> bool:
    if not isinstance(estimate, dict):
        return False
    text = estimate.get("item_name") or estimate.get("text")
    return _is_canes_box_combo_text(text)


def _is_canes_box_combo_text(text) -> bool:
    normalized = branded_food_lookup.normalize_meal_text(str(text or ""))
    return bool(
        _CANES_TOKEN_PATTERN.search(normalized)
        and _BOX_COMBO_PATTERN.search(normalized)
    )


def _is_source_backed_nutrition(estimate: dict) -> bool:
    if not isinstance(estimate, dict):
        return False
    source = str(estimate.get("source") or "").strip().lower()
    underlying_source = str(estimate.get("underlying_source") or "").strip().lower()
    provenance_parts = {
        part.strip()
        for value in (source, underlying_source)
        for part in value.split("+")
        if part.strip()
    }
    if "personal_vocab" in provenance_parts or bool(estimate.get("ambiguous")):
        return False
    allowed_components = _SOURCE_BACKED_NUTRITION_SOURCES | {
        "vision_claude",
        "vision_lm_studio",
        "vision_ollama",
        "local_cache",
        "mixed_lookup",
    }
    if not provenance_parts or not provenance_parts.issubset(allowed_components):
        return False
    declared_sources = estimate.get("underlying_sources")
    if declared_sources is not None and not (
        isinstance(declared_sources, list)
        and bool(declared_sources)
        and all(
            isinstance(value, str)
            and value.strip().lower() in _SOURCE_BACKED_NUTRITION_SOURCES
            for value in declared_sources
        )
    ):
        return False
    if "mixed_lookup" in provenance_parts:
        sources = estimate.get("underlying_sources")
        return (
            not bool(estimate.get("ambiguous"))
            and isinstance(sources, list)
            and bool(sources)
            and all(isinstance(value, str) and value in _SOURCE_BACKED_NUTRITION_SOURCES for value in sources)
        )
    providers = provenance_parts & _SOURCE_BACKED_NUTRITION_SOURCES
    if len(providers) != 1:
        return False
    if declared_sources is not None:
        return {
            value.strip().lower()
            for value in declared_sources
        } == providers
    return True


def _stored_food_log_baseline(existing: dict) -> dict:
    original = existing.get("original_estimate")
    baseline = dict(original) if isinstance(original, dict) else {}
    for field in (
        "item_name",
        "portion_description",
        "meal_type",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sodium_mg",
        "fiber_g",
        "confidence",
        "source",
    ):
        if baseline.get(field) is None and existing.get(field) is not None:
            baseline[field] = existing[field]
    return baseline


def _pending_canes_ai_only_original(existing: dict | None) -> dict | None:
    if (
        not isinstance(existing, dict)
        or existing.get("correction_state") != CORRECTION_STATE_PENDING_REVIEW
    ):
        return None
    original = _stored_food_log_baseline(existing)
    if _is_canes_box_combo(original) or _is_canes_box_combo_text(existing.get("context_note")):
        if original.get("_imported_pending_untrusted") is True:
            return original
        if _is_source_backed_nutrition(original):
            return None
        return original
    return None


def _imported_snapshot_needs_fresh_canes_resolution(user_id: int, client_id: str, payload: dict) -> bool:
    return bool(
        isinstance(payload, dict)
        and payload.get("_imported_snapshot_untrusted")
        and _pending_canes_ai_only_original(_food_log_by_client_id(user_id, client_id))
    )


def _apply_imported_pending_canes_parent(
    items: list[dict],
    payload: dict,
    pending_original: dict | None,
) -> list[dict]:
    if not (
        isinstance(payload, dict)
        and bool(payload.get("_imported_snapshot_untrusted"))
        and isinstance(pending_original, dict)
    ):
        return items
    for item in items:
        if not isinstance(item, dict) or item.get("state") != "included":
            continue
        item["branded_combo_ai_only"] = True
        item["_imported_snapshot_untrusted"] = True
        original = item.get("original_estimate") if isinstance(item.get("original_estimate"), dict) else None
        if not isinstance(original, dict):
            item["original_estimate"] = copy.deepcopy(pending_original)
            continue
        estimate = item.get("estimate") if isinstance(item.get("estimate"), dict) else None
        if (
            isinstance(estimate, dict)
            and _is_material_nutrition_correction(estimate, original)
            and not _review_estimate_differs(estimate, pending_original)
        ):
            item["estimate"] = _honest_material_correction_estimate(
                estimate,
                original,
                imported_untrusted=True,
            )
            item["_material_correction_from_submission"] = True
    return items


def _canes_box_combo_ai_only_needs_review(item: dict) -> bool:
    original = item.get("original_estimate") if isinstance(item, dict) else None
    estimate = item.get("estimate") if isinstance(item, dict) else None
    if not isinstance(original, dict) or not isinstance(estimate, dict):
        return False
    if not bool(item.get("branded_combo_ai_only")):
        return False
    if bool(item.get("_untrusted_imported_candidate")):
        return True
    if bool(item.get("_imported_snapshot_untrusted")):
        return not bool(item.get("_material_correction_from_submission"))
    if bool(item.get("server_source_backed_candidate")):
        return False
    return not _is_material_nutrition_correction(
        estimate,
        original,
        submitted_nutrition_fields=item.get("_submitted_nutrition_fields"),
    )


_MATERIAL_NUTRITION_DELTAS = {
    "calories": 50,
    "protein_g": 5,
    "carbs_g": 5,
    "fat_g": 5,
    "fiber_g": 3,
    "sodium_mg": 100,
}


def _server_submitted_nutrition_fields(raw_estimate: dict) -> frozenset[str]:
    if not isinstance(raw_estimate, dict):
        return frozenset()
    return frozenset(field for field in _MATERIAL_NUTRITION_DELTAS if field in raw_estimate)


def _is_material_nutrition_correction(
    submitted: dict,
    original: dict | None,
    *,
    submitted_nutrition_fields: frozenset[str] | None = None,
) -> bool:
    if not isinstance(original, dict):
        return False
    try:
        sanitized_original = sanitize_meal_estimate(
            original,
            source=original.get("source") or submitted.get("source") or "manual_review_estimate",
            legacy_defaults=True,
            plausible_ranges=True,
        )
    except MealEstimateValidationError:
        return False
    fields = (
        _MATERIAL_NUTRITION_DELTAS
        if submitted_nutrition_fields is None
        else {
            field: threshold
            for field, threshold in _MATERIAL_NUTRITION_DELTAS.items()
            if field in submitted_nutrition_fields
        }
    )
    return any(
        abs(float(submitted.get(field, 0)) - float(sanitized_original.get(field, 0))) >= threshold
        for field, threshold in fields.items()
    )


def _review_sum_items(items: list[dict]) -> dict:
    totals = {}
    included = [item for item in items if item.get("status") == "included"]
    for field in _MEAL_TOTAL_FIELDS:
        total = sum(float(item.get(field) or 0) for item in included)
        totals[field] = int(round(total)) if field in {"calories", "sodium_mg"} else round(total, 1)
    return totals


def _review_followup_default() -> dict:
    return {"available": False, "question": None, "used": False, "target_item_id": None}


def _review_first_unclear_blocked_item(payload: dict) -> dict | None:
    for item in sorted(payload.get("items") or [], key=lambda row: int(row.get("item_order") or 0)):
        if item.get("status") == "included" and bool(item.get("unclear")) and _review_item_is_blocked(item):
            return item
    return None


def _review_recalculate(payload: dict) -> dict:
    items = sorted(payload.get("items") or [], key=lambda row: int(row.get("item_order") or 0))
    payload["items"] = items
    payload["meal_totals"] = _review_sum_items(items)
    payload["save_blocked_item_ids"] = [item["item_id"] for item in items if _review_item_is_blocked(item)]
    followup = payload.get("followup") if isinstance(payload.get("followup"), dict) else _review_followup_default()
    if followup.get("available"):
        target_id = followup.get("target_item_id")
        target = next((item for item in items if item.get("item_id") == target_id), None)
        if not target or target.get("status") != "included" or not bool(target.get("unclear")) or not _review_item_is_blocked(target):
            followup = {"available": False, "question": None, "used": True, "target_item_id": None}
    payload["followup"] = {
        "available": bool(followup.get("available")),
        "question": _review_safe_str(followup.get("question"), max_len=240) if followup.get("available") else None,
        "used": bool(followup.get("used")),
        "target_item_id": followup.get("target_item_id") if followup.get("available") else None,
    }
    payload["meal_type"] = payload.get("meal_type") if payload.get("meal_type") in _REVIEW_MEAL_TYPES else "snack"
    return payload


def _review_maybe_create_followup(payload: dict) -> dict:
    payload = _review_recalculate(payload)
    followup = payload.get("followup") or _review_followup_default()
    if followup.get("used") or followup.get("available"):
        return payload
    target = _review_first_unclear_blocked_item(payload)
    if not target:
        return payload
    estimate = target.get("estimate") if isinstance(target.get("estimate"), dict) else {}
    question = _review_safe_str(estimate.get("clarification_question"), max_len=240)
    if not question:
        label = target.get("name") or "this item"
        question = f"Can you clarify the food or portion for {label}?"
    payload["followup"] = {
        "available": True,
        "question": question,
        "used": False,
        "target_item_id": target.get("item_id"),
    }
    return _review_recalculate(payload)


def _review_aggregate_estimate(payload: dict) -> dict:
    included = [item for item in payload.get("items") or [] if item.get("status") == "included"]
    totals = _review_sum_items(included)
    names = [str(item.get("name") or "").strip() for item in included if str(item.get("name") or "").strip()]
    portions = [str(item.get("portion") or "").strip() for item in included if str(item.get("portion") or "").strip()]
    confidences = [float(item.get("confidence")) for item in included if isinstance(item.get("confidence"), (int, float)) and not isinstance(item.get("confidence"), bool)]
    uncertainty_notes: list[str] = []
    seen_notes: set[str] = set()
    for item in included:
        estimate = item.get("estimate") if isinstance(item.get("estimate"), dict) else {}
        for note in estimate.get("uncertainty_notes") or []:
            note_text = _review_safe_str(note, max_len=240)
            note_key = (note_text or "").lower().strip()
            if note_text and note_key not in seen_notes:
                uncertainty_notes.append(note_text)
                seen_notes.add(note_key)
    if payload.get("save_blocked_item_ids") and not uncertainty_notes:
        original_estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
        for note in original_estimate.get("uncertainty_notes") or []:
            note_text = _review_safe_str(note, max_len=240)
            note_key = (note_text or "").lower().strip()
            if note_text and note_key not in seen_notes:
                uncertainty_notes.append(note_text)
                seen_notes.add(note_key)
    if payload.get("save_blocked_item_ids") and not uncertainty_notes:
        uncertainty_notes = ["Meal review draft has unresolved items."]
    source_kinds = [
        str(((item.get("source") if isinstance(item.get("source"), dict) else {}) or {}).get("kind") or "").strip()
        for item in included
    ]
    source_kinds = [kind for kind in source_kinds if kind and kind != "user"]
    aggregate_source = source_kinds[0] if source_kinds and all(kind == source_kinds[0] for kind in source_kinds) else "meal_review_snapshot"
    if not included:
        aggregate_source = "meal_review_snapshot"
    estimate = {
        "item_name": "; ".join(names)[:160] if names else "Meal review draft",
        "portion_description": "; ".join(portions)[:300] if portions else None,
        "meal_type": payload.get("meal_type") if payload.get("meal_type") in _REVIEW_MEAL_TYPES else "snack",
        "calories": totals["calories"],
        "protein_g": totals["protein_g"],
        "carbs_g": totals["carbs_g"],
        "fat_g": totals["fat_g"],
        "sodium_mg": totals["sodium_mg"],
        "fiber_g": totals["fiber_g"],
        "confidence": min(confidences) if confidences else 0.0,
        "ambiguous": bool(payload.get("save_blocked_item_ids")),
        "uncertainty_notes": uncertainty_notes,
        "source": aggregate_source,
    }
    if payload.get("has_image"):
        estimate["from_image"] = True
    if len(included) == 1 and isinstance(included[0].get("estimate"), dict):
        original = included[0]["estimate"]
        for key in (
            "external_food_id",
            "data_fetched_at",
            "portion_basis",
            "brand_id",
            "underlying_source",
            "underlying_sources",
            "off_attribution",
            "from_image",
            "personal_vocab_phrase",
            "vision_description",
            "vision_provider",
            "vision_confidence",
        ):
            if key in original:
                estimate[key] = original[key]
    return _review_sanitize_estimate(estimate, source=aggregate_source)


def _review_sync_pending_row(user_id: int, meal_id: str, payload: dict, aggregate: dict | None = None) -> dict | None:
    aggregate = aggregate or _review_aggregate_estimate(payload)
    existing = _food_log_by_client_id(user_id, meal_id)
    current_canes_ai_only = any(
        isinstance(item, dict) and _canes_box_combo_ai_only_needs_review(item)
        for item in payload.get("items") or []
    )
    text_hint = (
        existing.get("context_note")
        if existing and (
            not payload.get("has_image")
            or current_canes_ai_only
        )
        else None
    )
    return _meal_intake_persist(
        meal_id,
        aggregate,
        source=aggregate.get("source") or "meal_review_snapshot",
        has_image=bool(payload.get("has_image")),
        text_hint=text_hint,
        local_timestamp=payload.get("local_timestamp"),
        local_date=payload.get("local_date"),
        local_iso=payload.get("local_iso"),
        correction_state=CORRECTION_STATE_PENDING_REVIEW,
        original_estimate=aggregate,
    )


def _review_save_snapshot(user_id: int, meal_id: str, payload: dict, next_item_seq: int, applied_refreshes: dict | None, *, sync_pending: bool = True) -> dict:
    payload = _review_recalculate(payload)
    aggregate = _review_aggregate_estimate(payload)
    payload["estimate"] = aggregate
    decision = evaluate_meal_log(aggregate)
    reasons = [*decision["reasons"]]
    if any(_canes_box_combo_ai_only_needs_review(item) for item in payload.get("items") or []):
        reasons.append("branded_combo_ai_only")
    payload["policy"] = {
        "confidence_band": decision["confidence_band"],
        "reasons": reasons,
    }
    food_log = _review_sync_pending_row(user_id, meal_id, payload, aggregate) if sync_pending else payload.get("food_log")
    if food_log is not None:
        payload["food_log"] = dict(food_log)
        payload["food_log"]["context_note"] = None
    saved = save_meal_review_snapshot(
        user_id,
        meal_id=meal_id,
        payload=payload,
        next_item_seq=next_item_seq,
        applied_refreshes=applied_refreshes or {},
    )
    return saved["payload"]


_REVIEW_ACCEPT_COMPARE_FIELDS = (
    "item_name",
    "portion_description",
    "meal_type",
    "calories",
    "protein_g",
    "carbs_g",
    "fat_g",
    "sodium_mg",
    "fiber_g",
)


def _review_estimate_differs(submitted: dict, baseline: dict | None) -> bool:
    if not isinstance(baseline, dict):
        return True
    baseline_estimate = _review_sanitize_estimate(baseline, source=baseline.get("source"))
    return any(submitted.get(field) != baseline_estimate.get(field) for field in _REVIEW_ACCEPT_COMPARE_FIELDS)


def _review_sanitize_legacy_accept_estimate(raw_estimate: dict) -> dict:
    if not isinstance(raw_estimate, dict):
        raise MealEstimateValidationError("estimate must be an object")
    source_hint = raw_estimate.get("source") if isinstance(raw_estimate.get("source"), str) else None
    estimate = sanitize_meal_estimate(
        raw_estimate,
        source=source_hint or "manual_review_estimate",
        legacy_defaults=True,
        plausible_ranges=True,
    )
    _preserve_safe_estimate_metadata(estimate, raw_estimate)
    return _review_strip_private_estimate_fields(estimate)


def _review_user_confirmed_estimate(raw_estimate: dict) -> dict:
    estimate = _review_sanitize_legacy_accept_estimate(raw_estimate)
    estimate["ambiguous"] = False
    estimate["uncertainty_notes"] = []
    confidence = estimate.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or float(confidence) < MEDIUM_CONFIDENCE_THRESHOLD:
        estimate["confidence"] = MEDIUM_CONFIDENCE_THRESHOLD
    return estimate


def _review_snapshot_item_branded_combo_ai_only(item: dict, estimate: dict) -> bool:
    text = item.get("text") or item.get("name")
    original = item.get("original_estimate") if isinstance(item.get("original_estimate"), dict) else {}
    if bool(item.get("_imported_snapshot_untrusted")) and (
        _is_canes_box_combo(estimate)
        or _is_canes_box_combo(original)
        or _is_canes_box_combo_text(text)
    ):
        return True
    if bool(item.get("branded_combo_ai_only")):
        return True
    return (
        (_is_canes_box_combo(estimate) or _is_canes_box_combo_text(text))
        and not _is_source_backed_nutrition(estimate)
    )


def _review_payload_is_ai_only_canes_combo(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
    original = payload.get("original_estimate") if isinstance(payload.get("original_estimate"), dict) else {}
    is_combo = (
        _is_canes_box_combo(estimate)
        or _is_canes_box_combo(original)
        or _is_canes_box_combo_text(payload.get("text"))
    )
    return is_combo and (
        bool(payload.get("_imported_snapshot_untrusted"))
        or (
            not _is_source_backed_nutrition(estimate)
            and not _is_source_backed_nutrition(original)
        )
    )


def _review_normalize_imported_canes_payload(payload: dict) -> dict:
    if not (isinstance(payload, dict) and bool(payload.get("_imported_snapshot_untrusted"))):
        return payload
    payload_is_combo = _review_payload_is_ai_only_canes_combo(payload)
    if not payload.get("items") and payload_is_combo:
        estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
        original = payload.get("original_estimate") if isinstance(payload.get("original_estimate"), dict) else estimate
        legacy_item = _review_item_from_estimate(
            estimate,
            item_id="item-1",
            item_order=1,
            text=payload.get("text"),
            original_estimate=original,
            branded_combo_ai_only=True,
        )
        legacy_item["_imported_snapshot_untrusted"] = True
        payload["items"] = [legacy_item]
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or not bool(item.get("_imported_snapshot_untrusted")):
            continue
        original = item.get("original_estimate") if isinstance(item.get("original_estimate"), dict) else {}
        if not (payload_is_combo or _is_canes_box_combo(original)):
            continue
        item["branded_combo_ai_only"] = True
        item["server_source_backed_candidate"] = False
        candidates = item.get("candidates")
        if isinstance(candidates, list):
            item["_imported_candidates_untrusted"] = bool(candidates)
            for candidate in candidates:
                if isinstance(candidate, dict):
                    candidate["source_backed"] = False
    return payload


def _review_snapshot_items_for_accept(payload: dict, legacy_accept_body: dict | None = None) -> list[dict]:
    items = []
    snapshot_items = sorted(payload.get("items") or [], key=lambda row: int(row.get("item_order") or 0))
    payload_is_ai_only_combo = _review_payload_is_ai_only_canes_combo(payload)
    legacy_estimate = legacy_accept_body.get("estimate") if isinstance(legacy_accept_body, dict) else None
    if isinstance(legacy_estimate, dict) and len(snapshot_items) == 1:
        item = snapshot_items[0]
        submitted = _review_sanitize_legacy_accept_estimate(legacy_estimate)
        server_estimate = item.get("estimate") if isinstance(item.get("estimate"), dict) else None
        server_source_backed = bool(
            item.get("server_source_backed_candidate")
            and _is_source_backed_nutrition(server_estimate)
        )
        if server_source_backed and isinstance(server_estimate, dict):
            original = item.get("original_estimate")
            estimate = _server_snapshot_estimate_with_photo_provenance(server_estimate, original)
            if legacy_estimate.get("meal_type") in _REVIEW_MEAL_TYPES:
                estimate["meal_type"] = legacy_estimate["meal_type"]
            raw = {
                "state": item.get("status") if item.get("status") in _REVIEW_STATUS_VALUES else "included",
                "item_id": item.get("item_id"),
                "estimate": estimate,
                "branded_combo_ai_only": _review_snapshot_item_branded_combo_ai_only(item, server_estimate),
                "server_source_backed_candidate": True,
                "_untrusted_imported_candidate": bool(item.get("_untrusted_imported_candidate")),
            }
            if isinstance(original, dict):
                raw["original_estimate"] = original
            return [raw]
        if _review_estimate_differs(submitted, item.get("estimate")):
            estimate = _review_user_confirmed_estimate(legacy_estimate)
            original = legacy_accept_body.get("original_estimate") if isinstance(legacy_accept_body.get("original_estimate"), dict) else item.get("original_estimate")
            estimate = _honest_material_correction_estimate(
                estimate,
                original if isinstance(original, dict) else None,
                imported_untrusted=(
                    bool(payload.get("_imported_snapshot_untrusted"))
                    or bool(item.get("_imported_snapshot_untrusted"))
                ),
            )
            raw = {
                "state": item.get("status") if item.get("status") in _REVIEW_STATUS_VALUES else "included",
                "item_id": item.get("item_id"),
                "estimate": estimate,
                "branded_combo_ai_only": (
                    _review_snapshot_item_branded_combo_ai_only(item, item.get("estimate") or {})
                    or payload_is_ai_only_combo
                ),
                "server_source_backed_candidate": False,
                "_untrusted_imported_candidate": bool(item.get("_untrusted_imported_candidate")),
                "_imported_snapshot_untrusted": bool(
                    payload.get("_imported_snapshot_untrusted")
                    or item.get("_imported_snapshot_untrusted")
                ),
                "_material_correction_from_submission": _is_material_nutrition_correction(
                    estimate,
                    original if isinstance(original, dict) else {},
                    submitted_nutrition_fields=_server_submitted_nutrition_fields(legacy_estimate),
                ),
                "_submitted_nutrition_fields": _server_submitted_nutrition_fields(legacy_estimate),
            }
            if isinstance(original, dict):
                raw["original_estimate"] = original
            return [raw]

    meal_type = payload.get("meal_type") if payload.get("meal_type") in _REVIEW_MEAL_TYPES else None
    for item in snapshot_items:
        estimate = dict(item.get("estimate") if isinstance(item.get("estimate"), dict) else _review_aggregate_estimate({"items": [item], "meal_type": payload.get("meal_type")}))
        original = item.get("original_estimate") if isinstance(item.get("original_estimate"), dict) else {}
        if meal_type:
            estimate["meal_type"] = meal_type
        source_backed = bool(item.get("server_source_backed_candidate")) and _is_source_backed_nutrition(estimate)
        material_correction = bool(item.get("_material_correction_from_submission")) and _is_material_nutrition_correction(estimate, original)
        if material_correction and not source_backed:
            estimate = _honest_material_correction_estimate(
                estimate,
                original,
                imported_untrusted=bool(
                    payload.get("_imported_snapshot_untrusted")
                    or item.get("_imported_snapshot_untrusted")
                ),
            )
        raw = {
            "state": item.get("status") if item.get("status") in _REVIEW_STATUS_VALUES else "included",
            "item_id": item.get("item_id"),
            "estimate": estimate,
            "branded_combo_ai_only": (
                _review_snapshot_item_branded_combo_ai_only(item, estimate)
                or (payload_is_ai_only_combo and not source_backed)
            ),
            "server_source_backed_candidate": bool(item.get("server_source_backed_candidate")) and source_backed,
            "_untrusted_imported_candidate": bool(item.get("_untrusted_imported_candidate")),
            "_imported_snapshot_untrusted": bool(
                payload.get("_imported_snapshot_untrusted")
                or item.get("_imported_snapshot_untrusted")
            ),
            "_material_correction_from_submission": material_correction,
        }
        if original:
            raw["original_estimate"] = original
        items.append(raw)
    return items


def _review_blocked_item_ids_for_accept_items(items: list[dict], *, trusted_snapshot: bool = False) -> list[str]:
    blocked_ids = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        state = str(raw.get("state") or raw.get("item_state") or raw.get("status") or "included").strip().lower()
        if state != "included":
            continue
        item_id = str(raw.get("item_id") or f"item-{index + 1}")
        estimate = raw.get("estimate") if isinstance(raw.get("estimate"), dict) else {}
        if (
            not trusted_snapshot
            and (_is_canes_box_combo(estimate) or _is_canes_box_combo_text(raw.get("text")))
        ):
            blocked_ids.append(item_id)
            continue
        review_item = _review_item_from_estimate(
            estimate,
            item_id=item_id,
            item_order=index + 1,
            status="included",
            original_estimate=raw.get("original_estimate") if isinstance(raw.get("original_estimate"), dict) else None,
            branded_combo_ai_only=(trusted_snapshot and bool(raw.get("branded_combo_ai_only"))) or (
                not trusted_snapshot
                and (_is_canes_box_combo(estimate) or _is_canes_box_combo_text(raw.get("text")))
            ),
            server_source_backed_candidate=trusted_snapshot and bool(raw.get("server_source_backed_candidate")),
        )
        if trusted_snapshot and bool(raw.get("_untrusted_imported_candidate")):
            review_item["_untrusted_imported_candidate"] = True
        if trusted_snapshot and bool(raw.get("_imported_snapshot_untrusted")):
            review_item["_imported_snapshot_untrusted"] = True
        if trusted_snapshot and bool(raw.get("_material_correction_from_submission")):
            review_item["_material_correction_from_submission"] = True
        submitted_nutrition_fields = raw.get("_submitted_nutrition_fields")
        if trusted_snapshot and isinstance(submitted_nutrition_fields, frozenset):
            review_item["_submitted_nutrition_fields"] = submitted_nutrition_fields
        if _review_item_is_blocked(review_item):
            blocked_ids.append(item_id)
    return blocked_ids


def _review_placeholder_nutrition_not_resolved(item: dict) -> bool:
    original = item.get("original_estimate")
    if not isinstance(original, dict) or original.get("source") != "barcode_pending_source":
        return False
    if any(original.get(field) != 0 for field in ("calories", "protein_g", "carbs_g", "fat_g")):
        return False
    estimate = item.get("estimate")
    if not isinstance(estimate, dict):
        return True
    try:
        estimate = sanitize_meal_estimate(
            estimate,
            source=estimate.get("source") or "manual_review_estimate",
            legacy_defaults=True,
            plausible_ranges=True,
        )
    except MealEstimateValidationError:
        return True
    calories = estimate.get("calories")
    if isinstance(calories, bool) or not isinstance(calories, (int, float)) or calories <= 0:
        return True
    for field in ("protein_g", "carbs_g", "fat_g"):
        value = estimate.get(field)
        if not isinstance(value, bool) and isinstance(value, (int, float)) and value > 0:
            return False
    return True


def _review_estimate_lacks_trainable_nutrition(estimate: dict) -> bool:
    """True when a server-sanitized estimate has no positive nutrition signal.

    Applies the same "calories>0 and at least one macro>0" resolution rule
    already used for barcode placeholders, but regardless of source label —
    an all-zero canonical resolution is useless to learn from no matter why
    it is zero. This only gates personal-vocab training; it must never be
    used to block acceptance, since deliberate zero-calorie manual logs
    (water, black coffee) are legitimate food_logs rows.
    """
    if not isinstance(estimate, dict):
        return True
    calories = estimate.get("calories")
    if isinstance(calories, bool) or not isinstance(calories, (int, float)) or calories <= 0:
        return True
    for field in ("protein_g", "carbs_g", "fat_g"):
        value = estimate.get(field)
        if not isinstance(value, bool) and isinstance(value, (int, float)) and value > 0:
            return False
    return True


def _review_placeholder_nutrition_item_ids_for_accept_items(items: list[dict]) -> list[str]:
    blocked_ids = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        state = str(raw.get("state") or raw.get("item_state") or raw.get("status") or "included").strip().lower()
        if state != "included":
            continue
        if _review_placeholder_nutrition_not_resolved(raw):
            blocked_ids.append(str(raw.get("item_id") or f"item-{index + 1}"))
    return blocked_ids


def _review_payload_from_estimate(
    *,
    meal_id: str,
    estimate: dict,
    food_log: dict | None,
    has_image: bool,
    local_timestamp: str | None,
    local_date: str | None,
    local_iso: str | None,
    response_extras: dict,
    text_hint: str | None,
) -> dict:
    raw_items = estimate.get("items") if isinstance(estimate, dict) else None
    items = []
    if isinstance(raw_items, list):
        for index, raw_item in enumerate(raw_items[:8], start=1):
            if not isinstance(raw_item, dict):
                continue
            raw_item_estimate = raw_item.get("estimate") if isinstance(raw_item.get("estimate"), dict) else raw_item
            if has_image and isinstance(raw_item_estimate, dict):
                raw_item_estimate = {**raw_item_estimate, "from_image": True}
            item_id = _meal_safe_identifier(
                raw_item.get("item_id") or raw_item.get("meal_item_id") or f"item-{index}",
                fallback=f"item-{index}",
                max_len=64,
            )
            item_text = _review_safe_str(raw_item.get("text") or raw_item.get("item_name") or text_hint)
            items.append(_review_item_from_estimate(
                raw_item_estimate,
                item_id=item_id,
                item_order=index,
                status="included",
                text=item_text,
                candidates=raw_item.get("candidates"),
                original_estimate=raw_item_estimate,
                branded_combo_ai_only=bool(raw_item_estimate.get("branded_combo_ai_only")) or (
                    bool(estimate.get("branded_combo_ai_only"))
                    and not _is_source_backed_nutrition(raw_item_estimate)
                ),
            ))
    if not items:
        items = [_review_item_from_estimate(
            estimate,
            item_id="item-1",
            item_order=1,
            status="included",
            text=text_hint,
            candidates=estimate.get("candidates") if isinstance(estimate, dict) else None,
            branded_combo_ai_only=bool(estimate.get("branded_combo_ai_only")) if isinstance(estimate, dict) else False,
        )]
    payload = {
        "status": "pending_review",
        "estimate": _review_strip_private_estimate_fields(dict(estimate)),
        "food_log": food_log,
        "photo_retention": _food_photo_retention_payload(has_image),
        "local_timestamp": local_timestamp,
        "local_date": local_date,
        "local_iso": local_iso,
        "meal_id": meal_id,
        "meal_type": items[0]["estimate"].get("meal_type") if items else "snack",
        "followup": _review_followup_default(),
        "items": items,
        "has_image": bool(has_image),
        **response_extras,
    }
    return _review_maybe_create_followup(payload)


def _prepare_multi_meal_items(parent_client_id: str, items: list[dict]):
    prepared = []
    seen_client_ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            return None, api_error("items must contain objects", 400, code="invalid_field")
        state = str(item.get("state") or item.get("item_state") or item.get("status") or "included").strip().lower()
        if state not in _MEAL_ITEM_STATES:
            return None, api_error("item state must be included, skipped, or deleted", 400, code="invalid_field")
        item_client_id = _meal_item_client_id(parent_client_id, item, index)
        if item_client_id in seen_client_ids:
            return None, api_error("included item client_id values must be unique", 400, code="invalid_field")
        if state == "included":
            seen_client_ids.add(item_client_id)
        prepared.append({
            "raw": item,
            "index": index,
            "state": state,
            "client_id": item_client_id,
            "meal_item_id": _meal_item_id(item, index),
        })
    return prepared, None


def _record_multi_item_negative_feedback(user_id: int, item: dict, state: str) -> None:
    phrase = _meal_item_phrase(item)
    if not phrase:
        return
    estimate = item.get("estimate") if isinstance(item.get("estimate"), dict) else item.get("original_estimate")
    personal_vocab.record_negative_feedback(user_id, phrase, estimate if isinstance(estimate, dict) else None, state)


def _meal_negative_feedback_fingerprint(prepared: list[dict]) -> str:
    items = []
    for item in prepared:
        if item["state"] not in {"skipped", "deleted"}:
            continue
        phrase = _meal_item_phrase(item["raw"]) or ""
        items.append({
            "state": item["state"],
            "index": item["index"],
            "meal_item_id": item["meal_item_id"],
            "phrase_hash": hashlib.sha256(phrase.strip().lower().encode("utf-8")).hexdigest() if phrase else "",
        })
    payload = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _meal_event_feedback_conflicts(event: dict, skipped_count: int, deleted_count: int, feedback_fingerprint: str) -> bool:
    if int(event.get("skipped_count") or 0) != skipped_count:
        return True
    if int(event.get("deleted_count") or 0) != deleted_count:
        return True
    saved_fingerprint = event.get("feedback_fingerprint")
    return bool(saved_fingerprint and saved_fingerprint != feedback_fingerprint)


def _meal_intake_accept_multi(parent_client_id: str, data: dict):
    items = data.get("items")
    if not isinstance(items, list):
        return jsonify({"error": {"message": "items must be a list"}}), 400
    meal_id, err = _coerce_str(data.get("meal_id") or parent_client_id, "meal_id", required=True, max_len=128)
    if err:
        return err
    prepared, err = _prepare_multi_meal_items(parent_client_id, items)
    if err:
        return err
    user_id = _current_data_user_id()
    existing_rows = _meal_existing_rows(user_id, meal_id)
    existing_event = get_meal_acceptance_event(user_id, meal_id)
    included_items = [item for item in prepared if item["state"] == "included"]
    skipped_count = sum(1 for item in prepared if item["state"] == "skipped")
    deleted_count = sum(1 for item in prepared if item["state"] == "deleted")
    feedback_fingerprint = _meal_negative_feedback_fingerprint(prepared)
    has_image = any(_meal_item_has_image(item["raw"]) for item in prepared)
    incoming_client_ids = {item["client_id"] for item in included_items}
    existing_client_ids = {row.get("client_id") for row in existing_rows if row.get("client_id")}
    if existing_rows:
        if existing_client_ids == incoming_client_ids:
            if existing_event:
                existing_event_ids = set(existing_event.get("included_client_ids") or [])
                if (
                    existing_event_ids != incoming_client_ids
                    or _meal_event_feedback_conflicts(existing_event, skipped_count, deleted_count, feedback_fingerprint)
                ):
                    return jsonify({
                        "status": "conflict",
                        "meal_id": meal_id,
                        "error": {"message": "meal_id already accepted with a different included item set"},
                    }), 409
            else:
                for item in prepared:
                    if item["state"] in {"skipped", "deleted"}:
                        _record_multi_item_negative_feedback(user_id, item["raw"], item["state"])
                save_meal_acceptance_event(
                    user_id,
                    meal_id=meal_id,
                    status="logged",
                    included_client_ids=sorted(incoming_client_ids),
                    skipped_count=skipped_count,
                    deleted_count=deleted_count,
                    feedback_fingerprint=feedback_fingerprint,
                )
            ordered_rows = sorted(existing_rows, key=lambda row: row.get("item_index") if row.get("item_index") is not None else 10_000)
            return _meal_multi_response(meal_id, ordered_rows, len(ordered_rows), skipped_count, deleted_count, has_image)
        if not existing_client_ids.issubset(incoming_client_ids):
            return jsonify({
                "status": "conflict",
                "meal_id": meal_id,
                "error": {"message": "meal_id already accepted with a different included item set"},
            }), 409
    for item in prepared:
        if item["state"] != "included":
            continue
        raw_estimate = item["raw"].get("estimate")
        if not isinstance(raw_estimate, dict):
            return jsonify({"error": {"message": "included items require estimate objects"}}), 400
        try:
            sanitize_meal_estimate(
                raw_estimate,
                source=raw_estimate.get("source") or "manual_review_estimate",
                legacy_defaults=True,
                plausible_ranges=True,
            )
        except MealEstimateValidationError as exc:
            return jsonify({"error": {"message": f"invalid estimate: {exc}"}}), 400
    trusted_snapshot = data.get("_review_snapshot_trust") is _REVIEW_SNAPSHOT_TRUST_TOKEN
    if not trusted_snapshot:
        for item in prepared:
            if item["state"] != "included":
                continue
            _, err = _coerce_str(item["raw"].get("text"), "text", required=False, max_len=500)
            if err:
                return err
    items_to_check = [
        {**item["raw"], "item_id": item["meal_item_id"]}
        for item in prepared
        if item["state"] == "included" and item["client_id"] not in existing_client_ids
    ]
    placeholder_ids = _review_placeholder_nutrition_item_ids_for_accept_items(items_to_check)
    if placeholder_ids:
        return api_error(
            "barcode pending source requires real nutrition before acceptance",
            422,
            code="placeholder_nutrition_not_resolved",
            details={"meal_id": meal_id, "save_blocked_item_ids": placeholder_ids},
        )
    blocked_ids = _review_blocked_item_ids_for_accept_items(
        items_to_check,
        trusted_snapshot=trusted_snapshot,
    )
    if blocked_ids:
        return jsonify({
            "status": "blocked",
            "meal_id": meal_id,
            "save_blocked_item_ids": blocked_ids,
            "error": {"message": "review has blocked items"},
        }), 409
    for item in prepared:
        item["raw"].pop("_submitted_nutrition_fields", None)
    replaying_existing_event = False
    if existing_event:
        existing_event_ids = set(existing_event.get("included_client_ids") or [])
        if (
            existing_event_ids != incoming_client_ids
            or _meal_event_feedback_conflicts(existing_event, skipped_count, deleted_count, feedback_fingerprint)
        ):
            return jsonify({
                "status": "conflict",
                "meal_id": meal_id,
                "error": {"message": "meal_id already accepted with a different included item set"},
            }), 409
        replaying_existing_event = True
        if not incoming_client_ids:
            return _meal_multi_response(meal_id, [], 0, skipped_count, deleted_count, has_image)

    if not included_items:
        for item in prepared:
            if item["state"] in {"skipped", "deleted"}:
                _record_multi_item_negative_feedback(user_id, item["raw"], item["state"])
        save_meal_acceptance_event(
            user_id,
            meal_id=meal_id,
            status="discarded",
            included_client_ids=[],
            skipped_count=skipped_count,
            deleted_count=deleted_count,
            feedback_fingerprint=feedback_fingerprint,
        )
        return _meal_multi_response(meal_id, [], 0, skipped_count, deleted_count, has_image)

    local_timestamp, err = _coerce_str(
        data.get("local_timestamp") or data.get("meal_timestamp"),
        "local_timestamp",
        required=False,
        max_len=64,
    )
    if err:
        return err
    local_date, err = _coerce_str(data.get("local_date"), "local_date", required=False, max_len=10)
    if err:
        return err
    local_iso, err = _coerce_str(data.get("local_iso"), "local_iso", required=False, max_len=64)
    if err:
        return err

    records = []
    for item in included_items:
        raw_item = item["raw"]
        raw_estimate = raw_item.get("estimate") or {}
        if not isinstance(raw_estimate, dict):
            return jsonify({"error": {"message": "included items require estimate objects"}}), 400
        source_hint = raw_estimate.get("source")
        originated_from_image = raw_estimate.get("from_image") is True
        try:
            estimate = sanitize_meal_estimate(
                raw_estimate,
                source=source_hint or "manual_review_estimate",
                legacy_defaults=True,
                plausible_ranges=True,
            )
            _preserve_safe_estimate_metadata(estimate, raw_estimate)
        except MealEstimateValidationError as exc:
            return jsonify({"error": {"message": f"invalid estimate: {exc}"}}), 400
        if originated_from_image:
            estimate["from_image"] = True
        corrected = (
            bool(raw_item.get("corrected"))
            or raw_item.get("correction_state") == "corrected"
            or _meal_accept_was_corrected(estimate, raw_item.get("original_estimate"))
        )
        text_hint, err = _coerce_str(raw_item.get("text"), "text", required=False, max_len=500)
        if err:
            return err
        records.append({
            "item": item,
            "estimate": estimate,
            "originated_from_image": originated_from_image,
            "text_hint": text_hint,
            "corrected": corrected,
            "original_for_log": _sanitize_original_estimate_for_log(raw_item.get("original_estimate"), estimate),
        })

    rows = []
    for record in records:
        item = record["item"]
        estimate = record["estimate"]
        food_log = _meal_intake_persist(
            item["client_id"],
            estimate,
            source=estimate.get("source") or "manual_review_estimate",
            has_image=record["originated_from_image"],
            text_hint=record["text_hint"] or None,
            local_timestamp=local_timestamp or None,
            local_date=local_date or None,
            local_iso=local_iso or None,
            correction_state="corrected" if record["corrected"] else CORRECTION_STATE_ACCEPTED,
            original_estimate=record["original_for_log"],
            meal_id=meal_id,
            meal_item_id=item["meal_item_id"],
            item_index=item["index"],
            item_state="included",
        )
        rows.append(food_log)
        if (
            not _review_placeholder_nutrition_not_resolved({
                "estimate": estimate,
                "original_estimate": record["original_for_log"],
            })
            and not _review_estimate_lacks_trainable_nutrition(estimate)
            and claim_food_log_vocab_learning(user_id, item["client_id"])
        ):
            vocab_phrase = record["text_hint"] or _meal_vocab_learning_phrase(None, estimate)
            if record["corrected"]:
                personal_vocab.record_correct(user_id, vocab_phrase, estimate)
            else:
                personal_vocab.record_accept(user_id, _meal_vocab_learning_phrase(vocab_phrase, estimate), estimate)

    if not replaying_existing_event:
        for item in prepared:
            if item["state"] in {"skipped", "deleted"}:
                _record_multi_item_negative_feedback(user_id, item["raw"], item["state"])

    rows = sorted(rows, key=lambda row: row.get("item_index") if row.get("item_index") is not None else 10_000)
    save_meal_acceptance_event(
        user_id,
        meal_id=meal_id,
        status="logged",
        included_client_ids=sorted(incoming_client_ids),
        skipped_count=skipped_count,
        deleted_count=deleted_count,
        feedback_fingerprint=feedback_fingerprint,
    )
    if not replaying_existing_event:
        _enqueue_workout_adaptation_after_accept(user_id, rows)
    return _meal_multi_response(meal_id, rows, len(rows), skipped_count, deleted_count, has_image)


@app.route("/api/meal-intake", methods=["POST"])
def meal_intake():
    """Accept text and/or image and persist a meal estimate.

    Request: multipart/form-data with optional ``text`` (<= 500 chars),
    optional ``image`` (<= 6 MB, image/*), required ``client_id`` for idempotency.
    """
    if request.content_type and "multipart/form-data" not in request.content_type and "application/x-www-form-urlencoded" not in request.content_type:
        return jsonify({"error": {"message": "multipart/form-data expected"}}), 415

    # FIT-138 aggregate cap: the Linear AC reads "aggregate request bytes
    # <= 18 MB (over rejected with 413)" — the wire size, not just summed
    # file bytes after Werkzeug parsing. Check Content-Length before any
    # form/files access so the cap covers multipart boundaries, headers,
    # filenames, and text fields. The summed-file fallback below stays as
    # belt-and-suspenders in case Content-Length is absent or spoofed.
    content_length = request.content_length
    if content_length is not None and content_length > _MEAL_INTAKE_MAX_AGGREGATE_BYTES:
        return jsonify({"error": {"message": "images exceed 18 MB total"}}), 413

    text_raw = (request.form.get("text") or "").strip()
    if len(text_raw) > 500:
        return jsonify({"error": {"message": "text too long (max 500 chars)"}}), 400
    client_id = (request.form.get("client_id") or "").strip()
    if not client_id or len(client_id) > 128:
        return jsonify({"error": {"message": "client_id required (<=128 chars)"}}), 400
    local_timestamp_raw = (request.form.get("local_timestamp") or "").strip()
    if len(local_timestamp_raw) > 64:
        return jsonify({"error": {"message": "local_timestamp too long (max 64 chars)"}}), 400
    local_timestamp = local_timestamp_raw or None
    local_date_raw = (request.form.get("local_date") or "").strip()
    if len(local_date_raw) > 10:
        return jsonify({"error": {"message": "local_date too long (max 10 chars)"}}), 400
    local_date = local_date_raw or None
    local_iso_raw = (request.form.get("local_iso") or "").strip()
    if len(local_iso_raw) > 64:
        return jsonify({"error": {"message": "local_iso too long (max 64 chars)"}}), 400
    local_iso = local_iso_raw or None

    # FIT-138: accept multi-image submissions via plural "images" key;
    # fall back to legacy singular "image" for compatibility with the
    # FIT-128 pending-card retry path and any older clients still
    # in flight.
    image_files = request.files.getlist("images")
    if not image_files:
        legacy_image = request.files.get("image")
        if legacy_image is not None:
            image_files = [legacy_image]
    image_files = [f for f in image_files if f is not None and f.filename]
    if len(image_files) > _MEAL_INTAKE_MAX_IMAGE_COUNT:
        return jsonify({
            "error": {
                "message": f"too many photos; up to {_MEAL_INTAKE_MAX_IMAGE_COUNT} per meal",
            },
        }), 400
    image_blobs: list[tuple[bytes, str]] = []
    aggregate_bytes = 0
    for image_file in image_files:
        mimetype = (image_file.mimetype or "").lower()
        if not mimetype.startswith("image/"):
            return jsonify({"error": {"message": "image must be image/*"}}), 400
        if mimetype not in _MEAL_INTAKE_SUPPORTED_IMAGE_MIMETYPES:
            return jsonify({"error": {"message": "unsupported image type; use JPEG, PNG, WebP, or GIF"}}), 415
        image_file.stream.seek(0, os.SEEK_END)
        size = image_file.stream.tell()
        image_file.stream.seek(0)
        if size > _MEAL_INTAKE_MAX_IMAGE_BYTES:
            return jsonify({"error": {"message": "image exceeds 6 MB limit"}}), 413
        if size <= 0:
            return jsonify({"error": {"message": "image is empty"}}), 400
        aggregate_bytes += size
        if aggregate_bytes > _MEAL_INTAKE_MAX_AGGREGATE_BYTES:
            return jsonify({"error": {"message": "images exceed 18 MB total"}}), 413
        image_blobs.append((image_file.read(), mimetype))
        image_file.stream.seek(0)
    has_image = bool(image_blobs)
    # Preserve the legacy single-image variables so the rest of the
    # handler, the vision_extras response, and ``photo_retention`` still
    # work. The first image's mimetype is canonical for the response
    # surface; the vision adapter sees every image via image_blobs.
    image_bytes = image_blobs[0][0] if image_blobs else b""
    image_mimetype = image_blobs[0][1] if image_blobs else "image/jpeg"

    if not text_raw and not has_image:
        return jsonify({"error": {"message": "provide a meal description or photo"}}), 400

    user_id = _current_data_user_id()
    replay_response = _meal_capture_idempotency_response(
        user_id=user_id,
        client_id=client_id,
        has_image=has_image,
        local_timestamp=local_timestamp,
        local_date=local_date,
        local_iso=local_iso,
    )
    if replay_response is not None:
        return replay_response

    response_extras: dict = {}
    if has_image:
        try:
            estimate = _meal_intake_vision_estimate(
                images=image_blobs,
                text_raw=text_raw,
                user_id=user_id,
            )
            source = estimate["source"]
            response_extras["vision"] = {
                "provider": estimate.get("vision_provider") or vision_estimator.configured_provider(),
                "confidence": estimate.get("vision_confidence"),
            }
        except Exception as exc:
            if _meal_intake_vision_contention(exc):
                app.logger.warning(
                    "vision_busy_contention",
                    extra={"event": "vision_busy_contention"},
                )
            elif not isinstance(exc, vision_estimator.VisionEstimatorError):
                app.logger.warning("Vision meal estimate failed before fallback", exc_info=True)
            public_vision_error = _meal_intake_public_vision_error(exc)
            if not text_raw:
                return jsonify({
                    "error": {
                        "message": "Photo estimate failed. Add a meal description and try again.",
                        "reason": public_vision_error,
                    },
                    "photo_retention": _food_photo_retention_payload(has_image),
                }), 503
            try:
                parsed = parse_meal_text(
                    text_raw,
                    timestamp=local_iso or local_timestamp,
                    user_id=user_id,
                )
            except Exception:
                parsed = {"fallback_used": True}
                estimate = manual_review_estimate(text=text_raw, source="manual_text_review")
                estimate["uncertainty_notes"] = [
                    "Photo could not be read and text fallback could not estimate nutrition automatically; review before logging."
                ]
                response_extras["text_fallback_error"] = "text_parser_failed"
            else:
                try:
                    raw_estimate = parsed["estimate"]
                    estimate = sanitize_meal_estimate(raw_estimate)
                    _copy_meal_estimate_provenance(estimate, raw_estimate)
                    if isinstance(raw_estimate, dict):
                        if isinstance(raw_estimate.get("candidates"), list):
                            estimate["candidates"] = raw_estimate.get("candidates")
                        if isinstance(raw_estimate.get("items"), list):
                            estimate["items"] = raw_estimate.get("items")
                        if isinstance(raw_estimate.get("clarification_question"), str):
                            estimate["clarification_question"] = raw_estimate["clarification_question"].strip()[:240]
                except (KeyError, TypeError, MealEstimateValidationError):
                    estimate = manual_review_estimate(text=text_raw, source="manual_text_review")
                    parsed = {"fallback_used": True}
                    response_extras["text_fallback_error"] = "text_parser_failed"
            try:
                parsed_fallback_used = parsed["fallback_used"]
            except (KeyError, TypeError):
                parsed_fallback_used = True
            source = estimate["source"]
            response_extras["fallback_used"] = parsed_fallback_used
            fallback_reason = _meal_text_fallback_reason(parsed)
            if fallback_reason:
                response_extras["fallback_reason"] = fallback_reason
            response_extras["vision_error"] = public_vision_error
        estimate["from_image"] = True
    else:
        parsed = parse_meal_text(
            text_raw,
            timestamp=local_iso or local_timestamp,
            user_id=user_id,
        )
        raw_estimate = parsed["estimate"]
        try:
            estimate = sanitize_meal_estimate(raw_estimate)
            _copy_meal_estimate_provenance(estimate, raw_estimate)
            if isinstance(raw_estimate, dict):
                if isinstance(raw_estimate.get("candidates"), list):
                    estimate["candidates"] = raw_estimate.get("candidates")
                if isinstance(raw_estimate.get("items"), list):
                    estimate["items"] = raw_estimate.get("items")
                if isinstance(raw_estimate.get("clarification_question"), str):
                    estimate["clarification_question"] = raw_estimate["clarification_question"].strip()[:240]
        except MealEstimateValidationError:
            estimate = manual_review_estimate(text=text_raw, source="manual_text_review")
            parsed = {"fallback_used": True}
        # ``source`` lives inside the estimate so it round-trips through
        # the pending-review accept handler (which reads
        # estimate.get("source") to label the persisted food_log).
        source = estimate["source"]
        response_extras["fallback_used"] = parsed["fallback_used"]
        fallback_reason = _meal_text_fallback_reason(parsed)
        if fallback_reason:
            response_extras["fallback_reason"] = fallback_reason

    # FIT-61: the meal-log policy still labels each estimate with a
    # confidence band + reasons; FIT-138 reads them for the response
    # surface but overrides the persistence decision so the capture
    # endpoint always routes to review before save.
    decision = evaluate_meal_log(estimate)
    if (
        _is_canes_box_combo_text(text_raw) or _is_canes_box_combo(estimate)
    ) and not _is_source_backed_nutrition(estimate):
        estimate["branded_combo_ai_only"] = True
        decision["reasons"] = [*decision["reasons"], "branded_combo_ai_only"]
    response_extras["policy"] = {
        "confidence_band": decision["confidence_band"],
        "reasons": decision["reasons"],
    }
    # Translate stable policy reason codes into the existing
    # ``uncertainty_notes`` list so the composer's pending-review card
    # (which reads ``estimate.uncertainty_notes``) shows the user why
    # their entry was held back, even when the reason is policy-only
    # (e.g. medium_confidence, implausible_macros).
    _merge_policy_reasons_into_uncertainty_notes(estimate, decision["reasons"])

    # FIT-138 "Capture flow routes to review before save" is satisfied by
    # FIT-144's v2 fresh-submit return: _review_payload_from_estimate +
    # _review_save_snapshot creates the pending-review snapshot and returns
    # the multi-item review payload that the frontend dispatches to
    # buildMealReviewCardV2. PR #124's pre-rebase force-pending path was a
    # v1 single-item return that conflicts with the v2 shape on origin/main
    # post-FIT-134/FIT-144; it is dropped here. Idempotent replay paths
    # upstream in this handler still return the legacy logged shape for
    # already-accepted meals.
    replay_response = _meal_capture_idempotency_response(
        user_id=user_id,
        client_id=client_id,
        has_image=has_image,
        local_timestamp=local_timestamp,
        local_date=local_date,
        local_iso=local_iso,
    )
    if replay_response is not None:
        return replay_response
    status = "pending_review"
    food_log = _meal_intake_persist(
        client_id, estimate, source=source, has_image=has_image,
        text_hint=text_raw or None, local_timestamp=local_timestamp,
        local_date=local_date, local_iso=local_iso,
        correction_state=CORRECTION_STATE_PENDING_REVIEW,
    )
    if not _nutrition_entry_pending_review(food_log):
        replay_response = _meal_capture_idempotency_response(
            user_id=user_id,
            client_id=client_id,
            has_image=has_image,
            local_timestamp=local_timestamp,
            local_date=local_date,
            local_iso=local_iso,
        )
        if replay_response is not None:
            return replay_response
    payload = _review_payload_from_estimate(
        meal_id=client_id,
        estimate=estimate,
        food_log=food_log,
        has_image=has_image,
        local_timestamp=local_timestamp,
        local_date=local_date,
        local_iso=local_iso,
        response_extras=response_extras,
        text_hint=text_raw or None,
    )
    saved_payload = _review_save_snapshot(
        user_id,
        client_id,
        payload,
        next_item_seq=max((int(item.get("item_order") or 0) for item in payload.get("items") or []), default=1) + 1,
        applied_refreshes={},
        sync_pending=True,
    )
    return jsonify(saved_payload)


@app.route("/api/meal-intake/barcode", methods=["POST"])
def meal_intake_barcode():
    """Accept a packaged-food barcode and route the estimate to pending review."""
    if request.content_length is not None and request.content_length > _MEAL_INTAKE_MAX_AGGREGATE_BYTES:
        return api_error("request exceeds 18 MB total", 413, code="payload_too_large")
    if not request.is_json:
        return api_error("application/json expected", 415, code="invalid_content_type")
    data, err = get_json_body(required=True)
    if err:
        return err

    client_id, err = _coerce_str(data.get("client_id"), "client_id", required=True, max_len=128)
    if err:
        return err
    barcode_raw, err = _coerce_str(data.get("barcode"), "barcode", required=True, max_len=64)
    if err:
        return err
    barcode = branded_food_lookup.normalize_barcode(barcode_raw)
    if not barcode:
        return api_error("barcode must be 8, 12, 13, or 14 digits", 400, code="invalid_barcode")
    local_timestamp, err = _coerce_str(data.get("local_timestamp"), "local_timestamp", required=False, max_len=64)
    if err:
        return err
    local_date, err = _coerce_str(data.get("local_date"), "local_date", required=False, max_len=10)
    if err:
        return err
    local_iso, err = _coerce_str(data.get("local_iso"), "local_iso", required=False, max_len=64)
    if err:
        return err
    allow_pending = data.get("allow_pending", False)
    if not isinstance(allow_pending, bool):
        return api_error("allow_pending must be boolean", 400, code="invalid_field")

    user_id = _current_data_user_id()
    replay_response = _meal_capture_idempotency_response(
        user_id=user_id,
        client_id=client_id,
        has_image=False,
        local_timestamp=local_timestamp or None,
        local_date=local_date or None,
        local_iso=local_iso or None,
    )
    if replay_response is not None:
        return replay_response

    estimate = branded_food_lookup.lookup_barcode(barcode, user_id=user_id)
    pending_source = False
    if estimate is None:
        if not allow_pending:
            return api_error("barcode not found", 404, code="barcode_not_found")
        estimate = manual_review_estimate(text=f"Barcode {barcode}", source="barcode_pending_source")
        estimate.update({
            "portion_description": "Barcode pending source",
            "confidence": 0.2,
            "external_food_id": barcode,
            "portion_basis": "Unverified barcode pending manual source",
            "uncertainty_notes": [
                "No verified barcode nutrition source was available; review manually before logging.",
            ],
        })
        pending_source = True

    source = estimate.get("source") or "barcode_pending_source"
    cache_hit = source == "local_cache"
    lookup_source = estimate.get("underlying_source") if cache_hit else source
    response_extras = {
        "barcode": barcode,
        "lookup_source": lookup_source,
        "cache_hit": cache_hit,
        "pending_source": pending_source,
    }
    decision = evaluate_meal_log(estimate)
    response_extras["policy"] = {
        "confidence_band": decision["confidence_band"],
        "reasons": decision["reasons"],
    }
    _merge_policy_reasons_into_uncertainty_notes(estimate, decision["reasons"])

    replay_response = _meal_capture_idempotency_response(
        user_id=user_id,
        client_id=client_id,
        has_image=False,
        local_timestamp=local_timestamp or None,
        local_date=local_date or None,
        local_iso=local_iso or None,
    )
    if replay_response is not None:
        return replay_response
    food_log = _meal_intake_persist(
        client_id,
        estimate,
        source=source,
        has_image=False,
        text_hint=f"barcode:{barcode}",
        local_timestamp=local_timestamp or None,
        local_date=local_date or None,
        local_iso=local_iso or None,
        correction_state=CORRECTION_STATE_PENDING_REVIEW,
    )
    if not _nutrition_entry_pending_review(food_log):
        replay_response = _meal_capture_idempotency_response(
            user_id=user_id,
            client_id=client_id,
            has_image=False,
            local_timestamp=local_timestamp or None,
            local_date=local_date or None,
            local_iso=local_iso or None,
        )
        if replay_response is not None:
            return replay_response
    payload = _review_payload_from_estimate(
        meal_id=client_id,
        estimate=estimate,
        food_log=food_log,
        has_image=False,
        local_timestamp=local_timestamp or None,
        local_date=local_date or None,
        local_iso=local_iso or None,
        response_extras=response_extras,
        text_hint=f"barcode:{barcode}",
    )
    saved_payload = _review_save_snapshot(
        user_id,
        client_id,
        payload,
        next_item_seq=2,
        applied_refreshes={},
        sync_pending=True,
    )
    return jsonify(saved_payload)


@app.route("/api/meal-intake/pending", methods=["GET"])
def meal_intake_pending():
    """Return durable pending-review meal estimates for cross-reload hydration."""
    user_id = _current_data_user_id()
    removed = _cleanup_stale_pending_meal_reviews(user_id)
    pending = [_meal_pending_review_response_payload(user_id, entry) for entry in _pending_meal_review_entries(user_id)]
    return jsonify({
        "pending": pending,
        "pending_count": len(pending),
        "ttl_days": PENDING_MEAL_REVIEW_TTL_DAYS,
        "stale_removed": removed,
    })


def _review_estimate_from_text(text: str, *, user_id: int, timestamp: str | None = None) -> dict:
    parsed = parse_meal_text(text, timestamp=timestamp, user_id=user_id)
    raw = parsed.get("estimate") if isinstance(parsed, dict) else {}
    estimate = _review_sanitize_estimate(raw, source=raw.get("source") if isinstance(raw, dict) else None)
    if isinstance(raw, dict):
        _copy_meal_estimate_provenance(estimate, raw)
        estimate = _review_strip_private_estimate_fields(estimate)
        if isinstance(raw.get("candidates"), list):
            estimate["candidates"] = raw.get("candidates")
        if isinstance(raw.get("clarification_question"), str):
            estimate["clarification_question"] = raw["clarification_question"].strip()[:240]
    fallback_reason = _meal_text_fallback_reason(parsed)
    if fallback_reason:
        estimate["fallback_reason"] = fallback_reason
    return estimate


def _review_find_item(payload: dict, item_id: str) -> dict | None:
    for item in payload.get("items") or []:
        if item.get("item_id") == item_id:
            return item
    return None


def _review_replace_item(payload: dict, item_id: str, replacement: dict) -> None:
    items = payload.get("items") or []
    for index, item in enumerate(items):
        if item.get("item_id") == item_id:
            items[index] = replacement
            payload["items"] = items
            return


def _review_response_code(result) -> int:
    if isinstance(result, tuple) and len(result) >= 2:
        try:
            return int(result[1])
        except (TypeError, ValueError):
            return 200
    return int(getattr(result, "status_code", 200) or 200)


def _review_cleanup_terminal_snapshot(user_id: int, meal_id: str) -> None:
    delete_meal_review_snapshot(user_id, meal_id)
    row = _food_log_by_client_id(user_id, meal_id)
    if row and row.get("correction_state") == CORRECTION_STATE_PENDING_REVIEW:
        delete_food_log_by_client_id(user_id, meal_id)


@app.route("/api/meal-intake/<meal_id>/refresh", methods=["POST"])
def meal_intake_refresh(meal_id: str):
    meal_id = (meal_id or "").strip()
    if not meal_id or len(meal_id) > 128:
        return jsonify({"error": {"message": "invalid meal_id"}}), 400
    data, err = get_json_body(required=True)
    if err:
        return err
    kind = str(data.get("kind") or "").strip()
    if kind not in {
        "add_item",
        "edit_portion",
        "followup_answer",
        "choose_candidate",
        "skip_item",
        "delete_item",
        "restore_item",
        "set_meal_type",
    }:
        return jsonify({"error": {"message": "invalid refresh kind"}}), 400

    user_id = _current_data_user_id()
    snapshot = get_meal_review_snapshot(user_id, meal_id)
    if not snapshot:
        return jsonify({"error": {"message": "meal review snapshot not found"}}), 404

    payload = _review_normalize_imported_canes_payload(copy.deepcopy(snapshot["payload"]))
    next_item_seq = int(snapshot.get("next_item_seq") or 1)
    applied = dict(snapshot.get("applied_refreshes") or {})
    request_id = str(data.get("request_id") or "").strip()
    if kind in _REVIEW_REQUEST_ID_KINDS and not request_id:
        return jsonify({"error": {"message": "request_id is required for this refresh kind"}}), 400
    if request_id:
        previous_kind = applied.get(request_id)
        if previous_kind:
            if previous_kind == kind:
                return jsonify(payload)
            return jsonify({"error": {"message": "request_id already used for a different refresh kind"}}), 409

    item_id = str(data.get("item_id") or "").strip()

    if kind == "add_item":
        text, err = _coerce_str(data.get("text"), "text", required=True, max_len=500)
        if err:
            return err
        estimate = _review_estimate_from_text(text, user_id=user_id, timestamp=payload.get("local_iso") or payload.get("local_timestamp"))
        item_id_new = f"item-{next_item_seq}"
        payload.setdefault("items", []).append(_review_item_from_estimate(
            estimate,
            item_id=item_id_new,
            item_order=next_item_seq,
            status="included",
            text=text,
            candidates=estimate.get("candidates"),
            branded_combo_ai_only=(
                _is_canes_box_combo_text(text) or _is_canes_box_combo(estimate)
            ) and not _is_source_backed_nutrition(estimate),
        ))
        next_item_seq += 1
        payload = _review_maybe_create_followup(payload)

    elif kind == "edit_portion":
        if not item_id:
            return jsonify({"error": {"message": "item_id is required"}}), 400
        text, err = _coerce_str(data.get("text"), "text", required=True, max_len=500)
        if err:
            return err
        item = _review_find_item(payload, item_id)
        if not item:
            return jsonify({"error": {"message": "item not found"}}), 404
        if item.get("status") != "included":
            return jsonify({"error": {"message": "item is not editable unless included"}}), 409
        estimate = _review_estimate_from_text(text, user_id=user_id, timestamp=payload.get("local_iso") or payload.get("local_timestamp"))
        original = item.get("original_estimate") if isinstance(item.get("original_estimate"), dict) else item.get("estimate")
        server_source_backed = _is_source_backed_nutrition(estimate)
        material_correction = _is_material_nutrition_correction(estimate, original)
        preserve_imported_untrusted = bool(item.get("_imported_snapshot_untrusted")) and not (
            server_source_backed or material_correction
        )
        replacement = _review_item_from_estimate(
            estimate,
            item_id=item["item_id"],
            item_order=int(item.get("item_order") or 0),
            status="included",
            text=text,
            candidates=estimate.get("candidates"),
            original_estimate=original,
            branded_combo_ai_only=(
                bool(item.get("branded_combo_ai_only"))
                and not (server_source_backed or material_correction)
            ) or (
                (_is_canes_box_combo_text(text) or _is_canes_box_combo(estimate))
                and not _is_source_backed_nutrition(estimate)
            ),
            server_source_backed_candidate=server_source_backed,
        )
        if preserve_imported_untrusted:
            replacement["_imported_snapshot_untrusted"] = True
        if material_correction:
            replacement["_material_correction_from_submission"] = True
        _review_replace_item(payload, item_id, replacement)
        payload = _review_maybe_create_followup(payload)

    elif kind == "choose_candidate":
        if not item_id:
            return jsonify({"error": {"message": "item_id is required"}}), 400
        candidate_id = str(data.get("candidate_id") or "").strip()
        if not candidate_id:
            return jsonify({"error": {"message": "candidate_id is required"}}), 400
        item = _review_find_item(payload, item_id)
        if not item:
            return jsonify({"error": {"message": "item not found"}}), 404
        if item.get("status") != "included":
            return jsonify({"error": {"message": "candidate can only be chosen for included items"}}), 409
        candidate = next((cand for cand in item.get("candidates") or [] if cand.get("candidate_id") == candidate_id), None)
        if not candidate:
            return jsonify({"error": {"message": "candidate not found"}}), 404
        _review_replace_item(payload, item_id, _review_candidate_to_item(candidate, item))
        payload = _review_recalculate(payload)

    elif kind in {"skip_item", "delete_item", "restore_item"}:
        if not item_id:
            return jsonify({"error": {"message": "item_id is required"}}), 400
        item = _review_find_item(payload, item_id)
        if not item:
            return jsonify({"error": {"message": "item not found"}}), 404
        item["status"] = {
            "skip_item": "skipped",
            "delete_item": "deleted",
            "restore_item": "included",
        }[kind]
        payload = _review_maybe_create_followup(payload)

    elif kind == "set_meal_type":
        meal_type = str(data.get("meal_type") or "").strip().lower()
        if meal_type not in _REVIEW_MEAL_TYPES:
            return jsonify({"error": {"message": "invalid meal_type"}}), 400
        payload["meal_type"] = meal_type
        payload = _review_recalculate(payload)

    elif kind == "followup_answer":
        followup = payload.get("followup") if isinstance(payload.get("followup"), dict) else _review_followup_default()
        target_id = followup.get("target_item_id")
        target = _review_find_item(payload, str(target_id or ""))
        if (
            followup.get("used")
            or not followup.get("available")
            or not target
            or target.get("status") != "included"
            or not bool(target.get("unclear"))
            or not _review_item_is_blocked(target)
        ):
            return jsonify({"error": {"message": "no available followup question"}}), 409
        answer = str(data.get("answer") or "").strip()
        skipped = bool(data.get("skipped"))
        if answer and not skipped:
            estimate = _review_estimate_from_text(answer, user_id=user_id, timestamp=payload.get("local_iso") or payload.get("local_timestamp"))
            original = target.get("original_estimate") if isinstance(target.get("original_estimate"), dict) else target.get("estimate")
            server_source_backed = _is_source_backed_nutrition(estimate)
            material_correction = _is_material_nutrition_correction(estimate, original)
            preserve_imported_untrusted = bool(target.get("_imported_snapshot_untrusted")) and not (
                server_source_backed or material_correction
            )
            replacement = _review_item_from_estimate(
                estimate,
                item_id=target["item_id"],
                item_order=int(target.get("item_order") or 0),
                status="included",
                text=answer,
                candidates=estimate.get("candidates"),
                original_estimate=original,
                branded_combo_ai_only=(
                    bool(target.get("branded_combo_ai_only"))
                    and not (server_source_backed or material_correction)
                ) or (
                    (_is_canes_box_combo_text(answer) or _is_canes_box_combo(estimate))
                    and not _is_source_backed_nutrition(estimate)
                ),
                server_source_backed_candidate=server_source_backed,
            )
            if preserve_imported_untrusted:
                replacement["_imported_snapshot_untrusted"] = True
            if material_correction:
                replacement["_material_correction_from_submission"] = True
            _review_replace_item(payload, target["item_id"], replacement)
        payload["followup"] = {"available": False, "question": None, "used": True, "target_item_id": None}
        payload = _review_recalculate(payload)

    if request_id:
        applied[request_id] = kind
    saved_payload = _review_save_snapshot(user_id, meal_id, payload, next_item_seq, applied, sync_pending=True)
    return jsonify(saved_payload)


@app.route("/api/meal-intake/<client_id>", methods=["DELETE"])
def meal_intake_undo(client_id: str):
    """Undo a logged meal by deleting its food_log row."""
    client_id = (client_id or "").strip()
    if not client_id or len(client_id) > 128:
        return jsonify({"error": {"message": "invalid client_id"}}), 400
    user_id = _current_data_user_id()
    expected_state = (request.args.get("correction_state") or request.args.get("state") or "").strip()
    existing = _food_log_by_client_id(user_id, client_id)
    meal_rows = _meal_existing_rows(user_id, client_id)
    if expected_state:
        rows_to_check = ([existing] if existing else []) + meal_rows
        mismatched = [
            row for row in rows_to_check
            if str(row.get("correction_state") or "").strip() != expected_state
        ]
        if mismatched:
            return jsonify({
                "status": "conflict",
                "removed": False,
                "correction_state": mismatched[0].get("correction_state"),
            }), 409
    snapshot_removed = delete_meal_review_snapshot(user_id, client_id)
    removed = delete_food_log_by_client_id(user_id, client_id)
    meal_removed = delete_food_logs_by_meal_id(user_id, client_id)
    event_removed = delete_meal_acceptance_event(user_id, client_id)
    legacy_removed = _delete_legacy_nutrition_by_client_id(client_id)
    removed = bool(removed or legacy_removed or snapshot_removed or meal_removed or event_removed)
    return jsonify({"status": "ok" if removed else "not_found", "removed": removed})


@app.route("/api/meal-intake/<client_id>/accept", methods=["POST"])
def meal_intake_accept(client_id: str):
    """Accept a pending-review estimate and persist it as a food_log row."""
    client_id = (client_id or "").strip()
    if not client_id or len(client_id) > 128:
        return jsonify({"error": {"message": "invalid client_id"}}), 400
    data, err = get_json_body(required=True)
    if err:
        return err
    user_id = _current_data_user_id()
    if "items" in data:
        meal_id = str(data.get("meal_id") or client_id).strip()
        if meal_id != client_id:
            route_snapshot = get_meal_review_snapshot(user_id, client_id)
            route_payload = route_snapshot.get("payload", {}) if isinstance(route_snapshot, dict) else {}
            route_items = route_payload.get("items") or [] if isinstance(route_payload, dict) else []
            if (
                _review_payload_is_ai_only_canes_combo(route_payload)
                or any(isinstance(item, dict) and bool(item.get("branded_combo_ai_only")) for item in route_items)
            ):
                return api_error("meal_id must match the pending branded-combo review route", 400, code="invalid_field")
            if any(
                _pending_canes_ai_only_original(_food_log_by_client_id(user_id, review_id))
                for review_id in {client_id, meal_id}
            ):
                return jsonify({
                    "status": "blocked",
                    "meal_id": meal_id,
                    "save_blocked_item_ids": ["item-1"],
                    "error": {"message": "branded combo review is required before acceptance"},
                }), 409
            mismatch_has_placeholder = False
            for review_id in (client_id, meal_id):
                review_snapshot = get_meal_review_snapshot(user_id, review_id)
                review_snapshot_items = (
                    review_snapshot.get("payload", {}).get("items") or []
                    if isinstance(review_snapshot, dict)
                    else []
                )
                mismatch_has_placeholder = mismatch_has_placeholder or any(
                    isinstance(item, dict)
                    and _review_placeholder_nutrition_not_resolved({
                        "estimate": {},
                        "original_estimate": item.get("original_estimate"),
                    })
                    for item in review_snapshot_items
                )
                persisted_parent = _food_log_by_client_id(user_id, review_id)
                if isinstance(persisted_parent, dict):
                    mismatch_has_placeholder = (
                        mismatch_has_placeholder
                        or _review_placeholder_nutrition_not_resolved({
                            "estimate": {},
                            "original_estimate": {
                                field: persisted_parent.get(field)
                                for field in ("source", "calories", "protein_g", "carbs_g", "fat_g")
                            },
                        })
                    )
            if mismatch_has_placeholder:
                return api_error(
                    "meal_id must match the pending barcode review route",
                    400,
                    code="invalid_field",
                )
        snapshot = get_meal_review_snapshot(user_id, meal_id)
        if snapshot:
            items = data.get("items")
            if not isinstance(items, list):
                return api_error("items must be a list", 400, code="invalid_field")
            snapshot_payload = _review_normalize_imported_canes_payload(
                copy.deepcopy(snapshot.get("payload", {}))
            )
            snapshot_items = snapshot_payload.get("items") or []
            payload_is_ai_only_combo = _review_payload_is_ai_only_canes_combo(snapshot_payload)
            snapshot_items_by_id = {
                str(item.get("item_id")): item
                for item in snapshot_items
                if isinstance(item, dict)
            }
            originals_by_id = {
                str(item.get("item_id")): item.get("original_estimate")
                for item in snapshot_items
                if isinstance(item, dict) and isinstance(item.get("original_estimate"), dict)
            }
            items_with_server_originals = []
            seen_item_ids = set()
            for item in items:
                if not isinstance(item, dict):
                    items_with_server_originals.append(item)
                    continue
                item_id = str(item.get("item_id") or "")
                original = originals_by_id.get(item_id)
                snapshot_item = snapshot_items_by_id.get(item_id)
                if not item_id or item_id in seen_item_ids or not isinstance(original, dict):
                    return api_error("items must reference saved review items", 400, code="invalid_field")
                seen_item_ids.add(item_id)
                server_estimate = snapshot_item.get("estimate") if isinstance(snapshot_item, dict) else None
                source_backed_estimate = _is_source_backed_nutrition(server_estimate)
                submitted_estimate = item.get("estimate")
                submitted_state = str(item.get("state") or item.get("item_state") or "included").strip().lower()
                if submitted_state == "included":
                    if not isinstance(submitted_estimate, dict):
                        return jsonify({"error": {"message": "included items require estimate objects"}}), 400
                    try:
                        sanitize_meal_estimate(
                            submitted_estimate,
                            source=submitted_estimate.get("source") or "manual_review_estimate",
                            legacy_defaults=True,
                            plausible_ranges=True,
                        )
                    except MealEstimateValidationError as exc:
                        return jsonify({"error": {"message": f"invalid estimate: {exc}"}}), 400
                exact_fresh_server_snapshot = bool(
                    source_backed_estimate
                    and isinstance(server_estimate, dict)
                    and isinstance(submitted_estimate, dict)
                    and not bool(snapshot_payload.get("_imported_snapshot_untrusted"))
                    and not bool(snapshot_item and snapshot_item.get("_imported_snapshot_untrusted"))
                    and not payload_is_ai_only_combo
                    and not _review_estimate_differs(submitted_estimate, server_estimate)
                )
                server_source_backed_candidate = bool(
                    source_backed_estimate
                    and (
                        snapshot_item and snapshot_item.get("server_source_backed_candidate")
                        or exact_fresh_server_snapshot
                    )
                )
                submitted_meal_type = submitted_estimate.get("meal_type") if isinstance(submitted_estimate, dict) else None
                accepted_estimate = (
                    _server_snapshot_estimate_with_photo_provenance(
                        server_estimate,
                        original,
                    )
                    if server_source_backed_candidate and isinstance(server_estimate, dict)
                    else submitted_estimate
                )
                if server_source_backed_candidate and submitted_meal_type in _REVIEW_MEAL_TYPES:
                    accepted_estimate["meal_type"] = submitted_meal_type
                if not server_source_backed_candidate and isinstance(accepted_estimate, dict):
                    accepted_estimate = _honest_material_correction_estimate(
                        accepted_estimate,
                        original,
                        imported_untrusted=(
                            bool(snapshot_payload.get("_imported_snapshot_untrusted"))
                            or bool(snapshot_item and snapshot_item.get("_imported_snapshot_untrusted"))
                        ),
                    )
                items_with_server_originals.append(
                    {
                        **item,
                        "estimate": accepted_estimate,
                        "original_estimate": original,
                        "branded_combo_ai_only": (
                            _review_snapshot_item_branded_combo_ai_only(
                                snapshot_item,
                                server_estimate if isinstance(server_estimate, dict) else {},
                            )
                            if isinstance(snapshot_item, dict)
                            else False
                        ) or (payload_is_ai_only_combo and not server_source_backed_candidate),
                        "server_source_backed_candidate": server_source_backed_candidate,
                        "_untrusted_imported_candidate": bool(
                            snapshot_item and snapshot_item.get("_untrusted_imported_candidate")
                        ),
                        "_imported_snapshot_untrusted": bool(
                            snapshot_payload.get("_imported_snapshot_untrusted")
                            or snapshot_item and snapshot_item.get("_imported_snapshot_untrusted")
                        ),
                        "_material_correction_from_submission": (
                            not server_source_backed_candidate
                            and _is_material_nutrition_correction(
                                accepted_estimate,
                                original,
                                submitted_nutrition_fields=_server_submitted_nutrition_fields(submitted_estimate),
                            )
                        ),
                        "_submitted_nutrition_fields": _server_submitted_nutrition_fields(submitted_estimate),
                    }
                )
            items_with_server_originals = _apply_imported_pending_canes_parent(
                items_with_server_originals,
                snapshot_payload,
                _pending_canes_ai_only_original(_food_log_by_client_id(user_id, meal_id)),
            )
            data = {**data, "items": items_with_server_originals, "_review_snapshot_trust": _REVIEW_SNAPSHOT_TRUST_TOKEN}
            placeholder_ids = _review_placeholder_nutrition_item_ids_for_accept_items(items_with_server_originals)
            if placeholder_ids:
                return api_error(
                    "barcode pending source requires real nutrition before acceptance",
                    422,
                    code="placeholder_nutrition_not_resolved",
                    details={"meal_id": meal_id, "save_blocked_item_ids": placeholder_ids},
                )
            blocked_ids = _review_blocked_item_ids_for_accept_items(items_with_server_originals, trusted_snapshot=True)
            if blocked_ids:
                return jsonify({
                    "status": "blocked",
                    "meal_id": meal_id,
                    "save_blocked_item_ids": blocked_ids,
                    "error": {"message": "review has blocked items"},
                }), 409
        else:
            route_pending_canes = _pending_canes_ai_only_original(
                _food_log_by_client_id(user_id, client_id)
            )
            if route_pending_canes is not None:
                return jsonify({
                    "status": "blocked",
                    "meal_id": client_id,
                    "save_blocked_item_ids": ["item-1"],
                    "error": {"message": "branded combo review is required before acceptance"},
                }), 409
            persisted_parent = _food_log_by_client_id(user_id, meal_id)
            if isinstance(persisted_parent, dict):
                persisted_original = {
                    field: persisted_parent.get(field)
                    for field in ("source", "calories", "protein_g", "carbs_g", "fat_g")
                }
                if (
                    persisted_original["source"] == "barcode_pending_source"
                    and all(persisted_original[field] == 0 for field in ("calories", "protein_g", "carbs_g", "fat_g"))
                ):
                    items = data.get("items")
                    if isinstance(items, list):
                        data = {
                            **data,
                            "items": [
                                {**item, "original_estimate": persisted_original}
                                if isinstance(item, dict)
                                else item
                                for item in items
                            ],
                        }
        result = _meal_intake_accept_multi(client_id, data)
        if _review_response_code(result) < 400:
            meal_id = str(data.get("meal_id") or client_id).strip()
            _review_cleanup_terminal_snapshot(user_id, meal_id)
        return result
    snapshot = get_meal_review_snapshot(user_id, client_id)
    if snapshot:
        snapshot_payload = snapshot["payload"]
        try:
            snapshot_accept_data = dict(data)
            snapshot_accept_data.pop("original_estimate", None)
            snapshot_items = _review_snapshot_items_for_accept(snapshot_payload, snapshot_accept_data)
            snapshot_items = _apply_imported_pending_canes_parent(
                snapshot_items,
                snapshot_payload,
                _pending_canes_ai_only_original(_food_log_by_client_id(user_id, client_id)),
            )
        except MealEstimateValidationError as exc:
            return jsonify({"error": {"message": f"invalid estimate: {exc}"}}), 400
        placeholder_ids = _review_placeholder_nutrition_item_ids_for_accept_items(snapshot_items)
        if placeholder_ids:
            return api_error(
                "barcode pending source requires real nutrition before acceptance",
                422,
                code="placeholder_nutrition_not_resolved",
                details={"meal_id": client_id, "save_blocked_item_ids": placeholder_ids},
            )
        blocked_ids = _review_blocked_item_ids_for_accept_items(snapshot_items, trusted_snapshot=True)
        if blocked_ids:
            return jsonify({
                "status": "blocked",
                "meal_id": client_id,
                "save_blocked_item_ids": blocked_ids,
                "error": {"message": "review has blocked items"},
            }), 409
        accept_body = dict(data)
        accept_body["meal_id"] = client_id
        accept_body["items"] = snapshot_items
        accept_body["_review_snapshot_trust"] = _REVIEW_SNAPSHOT_TRUST_TOKEN
        for key in ("local_timestamp", "local_date", "local_iso"):
            if key not in accept_body and snapshot_payload.get(key):
                accept_body[key] = snapshot_payload.get(key)
        result = _meal_intake_accept_multi(client_id, accept_body)
        if _review_response_code(result) < 400:
            _review_cleanup_terminal_snapshot(user_id, client_id)
        return result
    raw_estimate = data.get("estimate") or {}
    source_hint = raw_estimate.get("source") if isinstance(raw_estimate, dict) else None
    originated_from_image = (
        raw_estimate.get("from_image") is True
        if isinstance(raw_estimate, dict)
        else False
    )
    terminal = _meal_terminal_idempotency_response(user_id, client_id, originated_from_image)
    if terminal is not None:
        return terminal
    stored_placeholder_original = None
    stored_pending_original = None
    stored_pending_canes_ai_only = False
    stored_pending_source_backed_canes = False
    existing = _food_log_by_client_id(user_id, client_id)
    if isinstance(existing, dict):
        stored_original = _stored_food_log_baseline(existing)
        if existing.get("correction_state") == CORRECTION_STATE_PENDING_REVIEW:
            stored_pending_original = stored_original
            stored_pending_canes_ai_only = _pending_canes_ai_only_original(existing) is not None
            stored_pending_source_backed_canes = (
                _is_source_backed_nutrition(stored_original)
                and (
                    _is_canes_box_combo(stored_original)
                    or _is_canes_box_combo_text(existing.get("context_note"))
                )
            )
        if _review_placeholder_nutrition_not_resolved({
            "estimate": {},
            "original_estimate": stored_original,
        }):
            stored_placeholder_original = stored_original
        if _review_placeholder_nutrition_not_resolved({
            "estimate": raw_estimate,
            "original_estimate": stored_original,
        }):
            return api_error(
                "barcode pending source requires real nutrition before acceptance",
                422,
                code="placeholder_nutrition_not_resolved",
            )
    text_hint, err = _coerce_str(data.get("text"), "text", required=False, max_len=500)
    if err:
        return err
    try:
        estimate = sanitize_meal_estimate(
            raw_estimate,
            source=source_hint or "manual_review_estimate",
            legacy_defaults=True,
            plausible_ranges=True,
        )
        if isinstance(raw_estimate, dict):
            _preserve_safe_estimate_metadata(estimate, raw_estimate)
    except MealEstimateValidationError as exc:
        return jsonify({"error": {"message": f"invalid estimate: {exc}"}}), 400
    if originated_from_image:
        estimate["from_image"] = True
    stored_pending_canes_correction_validated = False
    stored_pending_source_backed_match = False
    if stored_pending_canes_ai_only and isinstance(stored_pending_original, dict):
        snapshotless_item = {
            "status": "included",
            "estimate": estimate,
            "original_estimate": stored_pending_original,
            "branded_combo_ai_only": True,
            "server_source_backed_candidate": False,
            "_submitted_nutrition_fields": _server_submitted_nutrition_fields(raw_estimate),
        }
        if _canes_box_combo_ai_only_needs_review(snapshotless_item):
            return jsonify({
                "status": "blocked",
                "meal_id": client_id,
                "save_blocked_item_ids": ["item-1"],
                "error": {"message": "branded combo review is required before acceptance"},
            }), 409
        estimate = _honest_material_correction_estimate(
            estimate,
            stored_pending_original,
            imported_untrusted=stored_pending_original.get("_imported_pending_untrusted") is True,
        )
        stored_pending_canes_correction_validated = True
    elif stored_pending_source_backed_canes and isinstance(stored_pending_original, dict):
        comparison_estimate = dict(estimate)
        if stored_pending_original.get("meal_type") in _REVIEW_MEAL_TYPES:
            comparison_estimate["meal_type"] = stored_pending_original["meal_type"]
        if not _review_estimate_differs(comparison_estimate, stored_pending_original):
            saved_estimate = dict(stored_pending_original)
            if estimate.get("meal_type") in _REVIEW_MEAL_TYPES:
                saved_estimate["meal_type"] = estimate["meal_type"]
            estimate = saved_estimate
            stored_pending_source_backed_match = True
        elif _is_material_nutrition_correction(
            estimate,
            stored_pending_original,
            submitted_nutrition_fields=_server_submitted_nutrition_fields(raw_estimate),
        ):
            estimate = _honest_material_correction_estimate(estimate, stored_pending_original)
            stored_pending_source_backed_match = True
    if (
        not stored_pending_canes_correction_validated
        and not stored_pending_source_backed_match
        and (_is_canes_box_combo(estimate) or _is_canes_box_combo_text(text_hint))
    ):
        return jsonify({
            "status": "blocked",
            "meal_id": client_id,
            "save_blocked_item_ids": ["item-1"],
            "error": {"message": "branded combo review is required before acceptance"},
        }), 409
    local_timestamp, err = _coerce_str(
        data.get("local_timestamp"), "local_timestamp", required=False, max_len=64
    )
    if err:
        return err
    local_date, err = _coerce_str(data.get("local_date"), "local_date", required=False, max_len=10)
    if err:
        return err
    local_iso, err = _coerce_str(data.get("local_iso"), "local_iso", required=False, max_len=64)
    if err:
        return err
    trusted_original = stored_pending_original or stored_placeholder_original or data.get("original_estimate")
    corrected = (
        bool(data.get("corrected"))
        or data.get("correction_state") == "corrected"
        or _meal_accept_was_corrected(estimate, trusted_original)
    )
    correction_state = "corrected" if corrected else CORRECTION_STATE_ACCEPTED
    original_for_log = _sanitize_original_estimate_for_log(trusted_original, estimate)
    food_log = _meal_intake_persist(
        client_id,
        estimate,
        source=estimate.get("source") or "manual_review_estimate",
        has_image=originated_from_image,
        text_hint=text_hint or None,
        local_timestamp=local_timestamp or None,
        local_date=local_date or None,
        local_iso=local_iso or None,
        correction_state=correction_state,
        original_estimate=original_for_log,
    )
    if (
        not _review_placeholder_nutrition_not_resolved({
            "estimate": estimate,
            "original_estimate": original_for_log,
        })
        and not _review_estimate_lacks_trainable_nutrition(estimate)
        and claim_food_log_vocab_learning(user_id, client_id)
    ):
        if corrected:
            vocab_phrase = text_hint or _meal_vocab_learning_phrase(None, estimate)
            personal_vocab.record_correct(user_id, vocab_phrase, estimate)
        else:
            vocab_phrase = _meal_vocab_learning_phrase(text_hint or None, estimate)
            personal_vocab.record_accept(user_id, vocab_phrase, estimate)
    if "estimate" in data:
        delete_meal_review_snapshot(user_id, client_id)
    _enqueue_workout_adaptation_after_accept(user_id, [food_log])
    return jsonify({
        "status": "logged",
        "food_log": food_log,
        "photo_retention": _food_photo_retention_payload(originated_from_image),
    })


@app.route('/api/nutrition-today')
def nutrition_today():
    """Return today's nutrition totals and targets."""
    date_s = _today_str()
    food_log_entries = _food_log_entries_for_context(since=date_s)
    nutrition_context = _nutrition_context_for_date(
        date_s,
        food_log_entries=food_log_entries,
    )
    return jsonify(_nutrition_today_public_payload(date_s, nutrition_context))


def _nutrition_history_breakdown(date_s: str, entries):
    """FIT-13: per-day rollup with confidence + correction-state context.

    Extends ``_summarize_nutrition_entries_for_date`` with:

      * ``pending_count`` — entries awaiting review (excluded from totals).
      * ``manual_count`` — user-typed entries (no AI estimate).
      * ``corrected_count`` — accepted estimates the user edited
        (correction_state == "corrected").
      * ``estimated_count`` — accepted AI estimates the user did NOT edit.
      * ``confidence_avg`` — mean confidence over accepted entries with
        a numeric confidence value; ``None`` when no entries qualify.
      * ``high_sodium`` / ``late_meal`` — same flags the next-day
        coaching context uses, so the body view can interpret scale
        movements honestly instead of bare-attributing them.

    Mirrors the ``uses_only_accepted_entries`` rule from FIT-8:
    pending entries do not contribute to totals or confidence.
    """
    totals = {
        "calories": 0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "sodium_mg": 0,
        "entries_count": 0,
        "pending_count": 0,
        "manual_count": 0,
        "corrected_count": 0,
        "estimated_count": 0,
        "confidence_avg": None,
        "high_sodium": False,
        "late_meal": False,
    }
    confidence_sum = 0.0
    confidence_n = 0
    for entry in entries or []:
        if _nutrition_entry_day(entry) != date_s:
            continue
        if _nutrition_entry_pending_review(entry):
            totals["pending_count"] += 1
            continue
        totals["calories"] += int(entry.get("calories") or 0)
        totals["protein_g"] += float(entry.get("protein_g") or 0)
        totals["carbs_g"] += float(entry.get("carbs_g") or 0)
        totals["fat_g"] += float(entry.get("fat_g") or 0)
        totals["sodium_mg"] += int(entry.get("sodium_mg") or 0)
        totals["entries_count"] += 1
        # Correction-state breakdown: "manual" / "corrected" /
        # "accepted" (the AI estimate the user didn't edit).
        #
        # Legacy /api/add-nutrition entries may omit correction_state
        # entirely. add_food_log defaults those to "manual" when there's
        # no AI estimate metadata. Match that here so manual legacy
        # entries don't get mislabeled as AI-estimated on the Body
        # trend's reliability badge.
        state = str(entry.get("correction_state") or "").strip().lower()
        has_ai_signal = bool(
            entry.get("original_estimate")
            or entry.get("original_estimate_json")
            or entry.get("confidence")
            or str(entry.get("source") or "").startswith(("ai_", "stub_"))
        )
        if state == "manual":
            totals["manual_count"] += 1
        elif state == "corrected":
            totals["corrected_count"] += 1
        elif state == "accepted" or has_ai_signal:
            totals["estimated_count"] += 1
        else:
            # No correction_state, no AI signal → manual legacy entry.
            totals["manual_count"] += 1
        # Confidence average over entries that report a value.
        conf = entry.get("confidence")
        if isinstance(conf, (int, float)):
            confidence_sum += float(conf)
            confidence_n += 1
        # Late-meal flag: hour >= LATE_MEAL_CONTEXT_HOUR.
        hour = _nutrition_entry_logged_hour(entry)
        if hour is not None and hour >= LATE_MEAL_CONTEXT_HOUR:
            totals["late_meal"] = True
    if confidence_n > 0:
        totals["confidence_avg"] = round(confidence_sum / confidence_n, 3)
    totals["high_sodium"] = totals["sodium_mg"] >= SODIUM_NEXT_DAY_CONTEXT_MG
    return totals


@app.route('/api/nutrition-history')
def nutrition_history():
    """Return last 14 days of nutrition totals.

    Each day now includes confidence + correction-state context (FIT-13)
    so the Body tab can interpret scale movements honestly: a recent
    high-sodium / late-meal day surfaces as context rather than being
    mis-attributed to fat gain, and estimated-vs-corrected entries are
    distinguished from each other.

    Merges entries from BOTH the legacy ``NUTRITION_DATA`` JSON store
    AND the canonical ``food_logs`` SQLite table — the active meal
    composer (FIT-60/59/61) persists via ``add_food_log`` into
    food_logs, and these new entries would otherwise be invisible in
    history. Matches the merge ``/api/nutrition-today`` already
    performs via ``_food_log_entries_for_context``.
    """
    today = datetime.now().date()
    calories_target = USER_SETTINGS.get("daily_calorie_target")
    protein_target = USER_SETTINGS.get("daily_protein_target_g")
    earliest = (today - timedelta(days=13)).strftime("%Y-%m-%d")
    backfill_legacy_nutrition_client_ids()
    food_log_entries = list(_food_log_entries_for_context(since=earliest) or [])
    legacy_entries = list(NUTRITION_DATA or [])
    # Dedupe by client_id, not by day. /api/add-nutrition writes the
    # same entry to BOTH stores with the same client_id, so naïve
    # concat double-counts. But "prefer food_logs per day" wrongly
    # drops legitimate same-day entries that only exist in NUTRITION_DATA
    # (e.g. a legacy breakfast + a meal-composer dinner). Per-entry
    # dedupe handles both cases:
    #   * dual-write: skip the legacy copy when food_logs has the same client_id
    #   * mixed-source same-day: keep both since their client_ids differ
    #   * legacy without client_id: backfilled before this merge, so the
    #     history endpoint no longer collapses macro-identical meals via
    #     a content signature.
    food_log_client_ids = {
        str(entry.get("client_id"))
        for entry in food_log_entries
        if entry.get("client_id")
    }

    deduped_legacy = []
    for entry in legacy_entries:
        cid = entry.get("client_id")
        if cid and str(cid) in food_log_client_ids:
            continue  # dual-write with explicit client_id — skip the legacy copy.
        deduped_legacy.append(entry)
    merged_entries = food_log_entries + deduped_legacy
    days = []
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        date_s = d.strftime("%Y-%m-%d")
        totals = _nutrition_history_breakdown(date_s, merged_entries)
        cals = totals["calories"]
        protein = totals["protein_g"]
        days.append({
            "date": date_s,
            "calories": cals,
            "protein_g": round(protein, 1),
            "carbs_g": round(totals["carbs_g"], 1),
            "fat_g": round(totals["fat_g"], 1),
            "sodium_mg": totals["sodium_mg"],
            # FIT-13 additions:
            "entries_count": totals["entries_count"],
            "pending_count": totals["pending_count"],
            "manual_count": totals["manual_count"],
            "corrected_count": totals["corrected_count"],
            "estimated_count": totals["estimated_count"],
            "confidence_avg": totals["confidence_avg"],
            "calories_target": calories_target,
            "protein_target_g": protein_target,
            "calories_pct": round(100 * cals / calories_target) if calories_target else None,
            "protein_pct": round(100 * protein / protein_target) if protein_target else None,
            "high_sodium": totals["high_sodium"],
            "late_meal": totals["late_meal"],
        })
    return jsonify({"history": days})


@app.route('/api/food-logs/by-date/<date>')
def food_logs_by_date(date):
    """Per-meal food log entries for a specific date.

    FIT-93: the existing `/api/nutrition-history` endpoint only returns
    daily rollups, so the Body tab's nutrition-trend card can't show
    individual meals. This endpoint backs the row-expand affordance:
    tap a day → fetch its meals.

    Same client_id-only dedupe rules as `/api/nutrition-history`
    (food_logs preferred; legacy `NUTRITION_DATA` filled in for entries
    that only exist there).
    Returns entries sorted by `logged_at` ascending so the UI can
    render breakfast → dinner in natural order.
    """
    # Strict calendar validation — regex shape alone would accept things
    # like 2026-99-99 and return an empty result, which is misleading.
    # Canonical-form check (`strftime ==`) also rejects `2026-1-1` /
    # `26-05-20` which strptime tolerates but the stored `YYYY-MM-DD`
    # values won't match.
    try:
        parsed = datetime.strptime(date or "", "%Y-%m-%d")
    except (ValueError, TypeError):
        return api_error("date must be a valid YYYY-MM-DD", 400, code="invalid_field")
    if parsed.strftime("%Y-%m-%d") != date:
        return api_error("date must be canonical YYYY-MM-DD (zero-padded)", 400, code="invalid_field")
    user_id = _current_data_user_id()

    backfill_legacy_nutrition_client_ids(user_id)
    food_log_entries = list(_food_log_entries_for_context(since=date) or [])
    legacy_entries = list(NUTRITION_DATA or [])
    food_log_client_ids = {
        str(entry.get("client_id"))
        for entry in food_log_entries
        if entry.get("client_id")
    }

    deduped_legacy = []
    for entry in legacy_entries:
        cid = entry.get("client_id")
        if cid and str(cid) in food_log_client_ids:
            continue
        deduped_legacy.append(entry)

    merged = food_log_entries + deduped_legacy
    same_day = [e for e in merged if _nutrition_entry_day(e) == date]
    same_day.sort(key=lambda e: (e.get("logged_at") or "", e.get("id") or 0))

    # Bounded projection — only the fields the row-expand UI consumes.
    # Keeps the public contract tight so a later food_logs schema change
    # (extra private fields, raw estimates, etc.) doesn't leak into the
    # client. Matches the FIT-9 retention rule: no image bytes or
    # original prompts.
    def _project(entry: dict) -> dict:
        original_estimate = entry.get("original_estimate")
        accepted_estimate = sanitize_accepted_estimate(entry.get("accepted_estimate"))
        from_image = entry.get("from_image")
        accepted_from_image = (
            accepted_estimate.get("from_image")
            if isinstance(accepted_estimate, dict)
            else None
        )
        original_from_image = (
            original_estimate.get("from_image")
            if isinstance(original_estimate, dict)
            else None
        )
        if (
            accepted_from_image is True
            or original_from_image is True
        ):
            from_image = True
        elif (
            from_image is not True
            and (accepted_from_image is False or original_from_image is False)
        ):
            from_image = False
        return {
            "client_id": entry.get("client_id"),
            # FIT-100: include `date` so the correction flow can target
            # the original day even when `logged_at` is missing on legacy
            # rows. Without this, edits on those rows would land on today.
            "date": entry.get("date"),
            "logged_at": entry.get("logged_at"),
            "item_name": entry.get("item_name"),
            "portion_description": entry.get("portion_description"),
            "meal_type": entry.get("meal_type"),
            "calories": entry.get("calories"),
            "protein_g": entry.get("protein_g"),
            "carbs_g": entry.get("carbs_g"),
            "fat_g": entry.get("fat_g"),
            "sodium_mg": entry.get("sodium_mg"),
            "source": entry.get("source"),
            "confidence": entry.get("confidence"),
            "correction_state": entry.get("correction_state"),
            "accepted_estimate": accepted_estimate,
            "from_image": from_image,
        }

    entries = [_project(e) for e in same_day]
    return jsonify({"date": date, "entries": entries, "count": len(entries)})


def _project_food_log_refresh_event(event: dict) -> dict:
    return {
        "id": event.get("id"),
        "client_id": event.get("client_id"),
        "date": event.get("date"),
        "item_name": event.get("item_name"),
        "source": event.get("source"),
        "source_url": event.get("source_url"),
        "refreshed_at": event.get("refreshed_at"),
        "acknowledged_at": event.get("acknowledged_at"),
        "previous": {
            "calories": event.get("prev_calories"),
            "protein_g": event.get("prev_protein_g"),
            "carbs_g": event.get("prev_carbs_g"),
            "fat_g": event.get("prev_fat_g"),
            "sodium_mg": event.get("prev_sodium_mg"),
        },
        "current": {
            "calories": event.get("new_calories"),
            "protein_g": event.get("new_protein_g"),
            "carbs_g": event.get("new_carbs_g"),
            "fat_g": event.get("new_fat_g"),
            "sodium_mg": event.get("new_sodium_mg"),
        },
    }


@app.route('/api/food-log-refresh-events')
def food_log_refresh_events():
    """Recent verified-source refresh events for non-blocking UI notices."""
    unacknowledged_raw = str(request.args.get("unacknowledged", "true")).strip().lower()
    unacknowledged = unacknowledged_raw not in {"0", "false", "no", "all"}
    since, err = _coerce_str(request.args.get("since"), "since", required=False, max_len=64)
    if err:
        return err
    limit_raw = request.args.get("limit", 50)
    try:
        limit = min(max(int(limit_raw), 1), 50)
    except (TypeError, ValueError):
        return api_error("limit must be an integer from 1 to 50", 400, code="invalid_field")
    events = list_food_log_refresh_events(
        _current_data_user_id(),
        unacknowledged=unacknowledged,
        since=since,
        limit=limit,
    )
    projected = [_project_food_log_refresh_event(event) for event in events]
    return jsonify({"events": projected, "count": len(projected)})


@app.route('/api/food-log-refresh-events/<event_id>/ack', methods=["POST"])
def ack_food_log_refresh_event(event_id: str):
    event_id = (event_id or "").strip()
    if not event_id or len(event_id) > 80:
        return api_error("invalid refresh event id", 400, code="invalid_field")
    if not acknowledge_food_log_refresh_event(_current_data_user_id(), event_id):
        return api_error("refresh event not found", 404, code="not_found")
    return jsonify({"status": "success", "id": event_id})


@app.route('/api/workout-adaptation-events')
def workout_adaptation_events():
    """Pollable FIT-137 event feed for nutrition-based workout adaptations."""
    unacknowledged_raw = str(request.args.get("unacknowledged", "true")).strip().lower()
    unacknowledged = unacknowledged_raw not in {"0", "false", "no", "all"}
    since, err = _coerce_str(request.args.get("since"), "since", required=False, max_len=64)
    if err:
        return err
    limit_raw = request.args.get("limit", 50)
    try:
        limit = min(max(int(limit_raw), 1), 50)
    except (TypeError, ValueError):
        return api_error("limit must be an integer from 1 to 50", 400, code="invalid_field")

    active_open_raw = str(request.args.get("active_workout_open", "false")).strip().lower()
    completed_sets_by_exercise = _completed_sets_query_param(request.args.get("completed_sets"))
    active_open_requested = active_open_raw in {"1", "true", "yes"}
    active_workout_open = active_open_requested and bool(completed_sets_by_exercise)
    today_s = _today_str()
    food_log_entries = _food_log_entries_for_context(since=today_s)
    adaptation_food_entries = _food_log_entries_for_context()
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT
    fingerprint = _workout_recommendation_fingerprint()
    next_workout = _current_workout_plan_for_fingerprint(fingerprint)
    if not next_workout:
        next_workout = generate_next_workout(
            WORKOUTS,
            SORENESS_DATA,
            training_recommendation=_current_workout_training_recommendation(),
            consume_cardio_rotation=False,
        )
        _persist_current_workout_plan(next_workout, fingerprint)
    nutrition_context = _nutrition_context_for_date(
        today_s,
        hard_training_planned=_workout_looks_hard(next_workout),
        food_log_entries=food_log_entries,
    )
    if not active_open_requested or completed_sets_by_exercise:
        next_workout, _events = _apply_due_workout_adaptations_for_plan(
            next_workout,
            date_s=today_s,
            food_log_entries=adaptation_food_entries,
            nutrition_context=nutrition_context,
            active_workout_open=active_workout_open,
            completed_sets_by_exercise=completed_sets_by_exercise,
        )
        _persist_current_workout_plan(next_workout, fingerprint)

    events = list_workout_adaptation_events(
        _current_data_user_id(),
        unacknowledged=unacknowledged,
        since=since,
        limit=limit,
    )
    projected = [workout_adaptation.project_event(event) for event in events]
    return jsonify({"events": projected, "count": len(projected)})


@app.route('/api/workout-adaptation-events/<event_id>/ack', methods=["POST"])
def ack_workout_adaptation_event(event_id: str):
    event_id = (event_id or "").strip()
    if not event_id or len(event_id) > 80:
        return api_error("invalid workout adaptation event id", 400, code="invalid_field")
    if not acknowledge_workout_adaptation_event(_current_data_user_id(), event_id):
        return api_error("workout adaptation event not found", 404, code="not_found")
    return jsonify({"status": "success", "id": event_id})


@app.route('/api/add-cardio', methods=['POST'])
def add_cardio():
    """Add cardio session data."""
    global LAST_WORKOUT_RECOMMENDATION
    data, err = get_json_body(required=True)
    if err:
        return err

    activity_type, err2 = _coerce_str(data.get("activity_type"), "activity_type", required=True, max_len=64)
    if err2:
        return err2
    duration, err2 = _coerce_int(data.get("duration_minutes"), "duration_minutes", min_v=1, max_v=600)
    if err2:
        return err2
    intensity, err2 = _coerce_int(data.get("intensity", 5), "intensity", min_v=1, max_v=10)
    if err2:
        return err2
    avg_hr, err2 = _coerce_int(data.get("avg_heart_rate"), "avg_heart_rate", min_v=30, max_v=250, allow_none=True)
    if err2:
        return err2
    notes, err2 = _coerce_str(data.get("notes", ""), "notes", required=False, max_len=2000)
    if err2:
        return err2

    entry = {
        "date": data.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "activity_type": activity_type,
        "duration_minutes": duration,
        "avg_heart_rate": avg_hr,
        "intensity": intensity,
        "notes": notes,
        "created_at": datetime.now().isoformat(),
    }

    CARDIO_DATA.append(entry)
    save_json(CARDIO_FILE, CARDIO_DATA)  # Persist to file
    LAST_WORKOUT_RECOMMENDATION = None
    delete_current_workout_plan(_current_data_user_id())
    return jsonify({"status": "success", "cardio": entry})


@app.route('/api/add-recovery', methods=['POST'])
def add_recovery():
    """Add recovery session data (sauna, cold plunge, etc)."""
    global LAST_WORKOUT_RECOMMENDATION
    data, err = get_json_body(required=True)
    if err:
        return err

    recovery_type, err2 = _coerce_str(data.get("recovery_type"), "recovery_type", required=True, max_len=64)
    if err2:
        return err2
    duration, err2 = _coerce_int(data.get("duration_minutes"), "duration_minutes", min_v=1, max_v=600)
    if err2:
        return err2
    temperature, err2 = _coerce_int(data.get("temperature"), "temperature", min_v=-40, max_v=250, allow_none=True)
    if err2:
        return err2
    notes, err2 = _coerce_str(data.get("notes", ""), "notes", required=False, max_len=2000)
    if err2:
        return err2

    entry = {
        "date": data.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "recovery_type": recovery_type,
        "duration_minutes": duration,
        "temperature": temperature,
        "notes": notes,
        "created_at": datetime.now().isoformat(),
    }

    RECOVERY_DATA.append(entry)
    save_json(RECOVERY_FILE, RECOVERY_DATA)  # Persist to file
    LAST_WORKOUT_RECOMMENDATION = None
    delete_current_workout_plan(_current_data_user_id())
    return jsonify({"status": "success", "recovery": entry})


@app.route('/api/exercises')
def api_exercises():
    """Exercise dropdown options for manual logging."""
    options = []
    include_excluded = request.args.get("include_excluded") in ("1", "true", "yes")
    excluded = {
        str(name).strip().lower()
        for name in USER_SETTINGS.get("excluded_exercises", [])
        if str(name).strip()
    }
    for ex in EXERCISE_LIBRARY:
        name = ex.get("name")
        is_excluded = (name or "").strip().lower() in excluded
        if is_excluded and not include_excluded:
            continue
        options.append({
            "name": name,
            "muscle": ex.get("muscle"),
            "equipment": ex.get("equipment"),
            "equipment_brands": ex.get("equipment_brands", []),
            "aliases": ex.get("aliases", []),
            "compound": ex.get("compound"),
            "baseline": ex.get("baseline"),
            "disabled_by_default": bool(ex.get("disabled_by_default")),
            "excluded": is_excluded,
            "avoid_reason": ex.get("avoid_reason"),
        })
    options.sort(key=lambda x: ((x.get("muscle") or ""), (x.get("name") or "")))
    return jsonify({"exercises": options})


@app.route('/api/add-body-measurement', methods=['POST'])
def add_body_measurement():
    """Add body composition measurement (weight, body fat %)."""
    data, err = get_json_body(required=True)
    if err:
        return err

    weight_lbs, err2 = _coerce_float(data.get("weight_lbs"), "weight_lbs", min_v=50.0, max_v=1000.0)
    if err2:
        return err2
    body_fat_pct, err2 = _coerce_float(data.get("body_fat_pct"), "body_fat_pct", min_v=1.0, max_v=60.0, allow_none=True)
    if err2:
        return err2
    notes, err2 = _coerce_str(data.get("notes", ""), "notes", required=False, max_len=2000)
    if err2:
        return err2

    entry = {
        "date": data.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "weight_lbs": weight_lbs,
        "body_fat_pct": body_fat_pct,
        "neck_in": data.get("neck_in"),
        "waist_in": data.get("waist_in"),
        "chest_in": data.get("chest_in"),
        "hips_in": data.get("hips_in"),
        "arms": data.get("arms"),
        "legs": data.get("legs"),
        "notes": notes,
        "created_at": datetime.now().isoformat(),
    }

    BODY_DATA.append(entry)
    save_json(BODY_FILE, BODY_DATA)
    return jsonify({"status": "success", "body_measurement": entry})


@app.route('/api/body-history')
def body_history():
    """Return body measurement history with calculated fields."""
    sorted_data = sorted(BODY_DATA, key=lambda x: x.get("date") or "", reverse=True)

    # Calculate weight changes and trend
    for i, entry in enumerate(sorted_data):
        if i < len(sorted_data) - 1:
            prev_weight = sorted_data[i + 1].get("weight_lbs")
            curr_weight = entry.get("weight_lbs")
            if prev_weight and curr_weight:
                entry["weight_change"] = round(curr_weight - prev_weight, 1)
            else:
                entry["weight_change"] = None
        else:
            entry["weight_change"] = None

    # Calculate trend (last 7 entries linear regression)
    recent_7 = sorted_data[:7]
    if len(recent_7) >= 3:
        weights = [e.get("weight_lbs") for e in recent_7 if e.get("weight_lbs")]
        if len(weights) >= 3:
            x = list(range(len(weights)))
            n = len(weights)
            x_mean = sum(x) / n
            y_mean = sum(weights) / n
            numerator = sum((x[i] - x_mean) * (weights[i] - y_mean) for i in range(n))
            denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
            if denominator != 0:
                slope = numerator / denominator
                trend = "increasing" if slope > 0.1 else "decreasing" if slope < -0.1 else "stable"
            else:
                trend = "unknown"
        else:
            trend = "unknown"
    else:
        trend = "unknown"

    return jsonify({"status": "success", "history": sorted_data, "trend": trend})


@app.route('/api/protocols')
def protocols():
    """Return evidence-based training protocols."""
    return jsonify({
        "lean_gain": {
            "protein": {
                "target": "1.6-2.2g per kg bodyweight",
                "timing": "Every 3-5 hours, 20-40g per meal",
                "sources": "Whey, chicken, fish, eggs, Greek yogurt"
            },
            "calories": {
                "surplus": "200-300 kcal/day above TDEE",
                "note": "Larger surpluses add more fat without faster muscle gain"
            },
            "training": {
                "frequency": "3-5x/week",
                "volume": "10-20 hard sets per muscle/week",
                "overload": "Add weight or reps each session when RPE allows",
                "rest": "2-3 min between compound sets, 1-2 min isolation"
            },
            "sleep": {
                "target": "7-9 hours",
                "why": "Growth hormone peaks during deep sleep; sleep debt impairs protein synthesis"
            },
            "hydration": {
                "target": "0.5-1 oz per lb bodyweight daily"
            },
            "supplements": [
                "Creatine monohydrate 5g/day",
                "Vitamin D 2000-5000 IU if deficient",
                "Whey protein for convenience (not required)"
            ],
            "key_principles": [
                "Progressive overload is the #1 driver of muscle growth",
                "Track your lifts — you can't improve what you don't measure",
                "Deload every 4-6 weeks or when recovery metrics decline",
                "Prioritize compound movements (press, row, squat, deadlift)",
                "Body recomp (lose fat + gain muscle) works best for beginners or returning lifters"
            ]
        }
    })


@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    """Get or update user settings including training goal and available time."""
    global LAST_WORKOUT_RECOMMENDATION
    if request.method == 'GET':
        goal = USER_SETTINGS.get("training_goal", TrainingGoal.HYPERTROPHY.value)
        goal_params = GOAL_PARAMETERS.get(goal, {})
        return jsonify({
            "training_goal": goal,
            "goal_details": goal_params,
            "date_of_birth": USER_SETTINGS.get("date_of_birth", ""),
            "sex": USER_SETTINGS.get("sex", ""),
            "sessions_per_week_target": USER_SETTINGS.get("sessions_per_week_target", 3),
            "available_time_minutes": USER_SETTINGS.get("available_time_minutes", 60),
            "target_weight_lbs": USER_SETTINGS.get("target_weight_lbs", 180),
            "target_body_fat_pct": USER_SETTINGS.get("target_body_fat_pct", 15),
            "daily_calorie_target": USER_SETTINGS.get("daily_calorie_target", 2200),
            "daily_protein_target_g": USER_SETTINGS.get("daily_protein_target_g", 148),
            "fatigue_threshold": USER_SETTINGS.get("fatigue_threshold", 72),
            "equipment_preference": USER_SETTINGS.get("equipment_preference", "machines_only"),
            "preferred_equipment_brands": USER_SETTINGS.get("preferred_equipment_brands", []),
            "excluded_exercises": USER_SETTINGS.get("excluded_exercises", []),
            "volume_landmarks": USER_SETTINGS.get("volume_landmarks", DEFAULT_SETTINGS["volume_landmarks"]),
            "available_goals": [
                {"value": g, "name": p["name"], "description": p["description"]}
                for g, p in GOAL_PARAMETERS.items()
            ],
            "time_options": TIME_OPTIONS,
            "equipment_options": [
                {"value": "machines_only", "label": "Machine"},
                {"value": "machines_and_cables", "label": "Machine + Cable"},
                {"value": "all", "label": "All Equipment"},
            ],
            "sex_options": [
                {"value": "", "label": "Not set"},
                {"value": "female", "label": "Female"},
                {"value": "male", "label": "Male"},
                {"value": "nonbinary", "label": "Nonbinary / another option"},
                {"value": "prefer_not_to_say", "label": "Prefer not to say"},
            ],
        })
    else:
        data, err = get_json_body(required=True)
        if err:
            return err
        updated_settings = copy.deepcopy(USER_SETTINGS)

        if "training_goal" in data:
            goal = data.get("training_goal")
            if goal not in GOAL_PARAMETERS:
                return api_error("Invalid training_goal", 400, code="invalid_field")
            updated_settings["training_goal"] = goal

        if "date_of_birth" in data:
            dob = _coerce_profile_date_of_birth(data.get("date_of_birth"))
            if dob is None:
                return api_error("Invalid date_of_birth", 400, code="invalid_field")
            updated_settings["date_of_birth"] = dob

        if "sex" in data:
            sex = _coerce_profile_sex(data.get("sex"))
            if sex is None:
                return api_error("Invalid sex", 400, code="invalid_field")
            updated_settings["sex"] = sex

        if "sessions_per_week_target" in data:
            s, err2 = _coerce_int(data.get("sessions_per_week_target"), "sessions_per_week_target", min_v=1, max_v=14)
            if err2:
                return err2
            updated_settings["sessions_per_week_target"] = s

        if "available_time_minutes" in data:
            t, err2 = _coerce_int(data.get("available_time_minutes"), "available_time_minutes", min_v=10, max_v=240)
            if err2:
                return err2
            updated_settings["available_time_minutes"] = t

        if "target_weight_lbs" in data:
            tw, err2 = _coerce_float(data.get("target_weight_lbs"), "target_weight_lbs", min_v=80, max_v=500)
            if err2:
                return err2
            updated_settings["target_weight_lbs"] = tw

        if "target_body_fat_pct" in data:
            tbf, err2 = _coerce_float(data.get("target_body_fat_pct"), "target_body_fat_pct", min_v=4, max_v=60)
            if err2:
                return err2
            updated_settings["target_body_fat_pct"] = tbf

        if "daily_calorie_target" in data:
            cal, err2 = _coerce_int(data.get("daily_calorie_target"), "daily_calorie_target", min_v=500, max_v=6000)
            if err2:
                return err2
            updated_settings["daily_calorie_target"] = cal

        if "daily_protein_target_g" in data:
            prot, err2 = _coerce_float(data.get("daily_protein_target_g"), "daily_protein_target_g", min_v=50, max_v=400)
            if err2:
                return err2
            updated_settings["daily_protein_target_g"] = round(prot, 1)

        if "fatigue_threshold" in data:
            ft, err2 = _coerce_int(data.get("fatigue_threshold"), "fatigue_threshold", min_v=40, max_v=95)
            if err2:
                return err2
            updated_settings["fatigue_threshold"] = ft

        if "volume_landmarks" in data and isinstance(data.get("volume_landmarks"), dict):
            updated_settings["volume_landmarks"] = data.get("volume_landmarks")
        if "equipment_preference" in data:
            pref, err2 = _coerce_str(data.get("equipment_preference"), "equipment_preference", required=True, max_len=64)
            if err2:
                return err2
            if pref not in ("machines_only", "machines_and_cables", "all"):
                return api_error("Invalid equipment_preference", 400, code="invalid_field")
            updated_settings["equipment_preference"] = pref
        if "preferred_equipment_brands" in data:
            brands = data.get("preferred_equipment_brands")
            if not isinstance(brands, list) or not all(isinstance(b, str) for b in brands):
                return api_error("preferred_equipment_brands must be a list of strings", 400, code="invalid_field")
            normalized_brands = []
            seen_brands = set()
            for brand in brands:
                cleaned = brand.strip()
                key = cleaned.lower()
                if cleaned and key not in seen_brands:
                    normalized_brands.append(cleaned)
                    seen_brands.add(key)
            updated_settings["preferred_equipment_brands"] = normalized_brands[:20]
        if "excluded_exercises" in data:
            excluded = data.get("excluded_exercises")
            if not isinstance(excluded, list) or not all(isinstance(e, str) for e in excluded):
                return api_error("excluded_exercises must be a list of strings", 400, code="invalid_field")
            known = {ex["name"].strip().lower(): ex["name"] for ex in EXERCISE_LIBRARY}
            normalized = []
            for name in excluded:
                display_name = name.strip()
                cleaned = display_name.lower()
                if cleaned and cleaned not in known:
                    return api_error(f"Unknown excluded exercise: {display_name}", 400, code="invalid_field")
                canonical = known.get(cleaned)
                if canonical and canonical not in normalized:
                    normalized.append(canonical)
            updated_settings["excluded_exercises"] = normalized

        USER_SETTINGS.clear()
        USER_SETTINGS.update(updated_settings)
        save_json(SETTINGS_FILE, USER_SETTINGS)  # Persist to file
        LAST_WORKOUT_RECOMMENDATION = None
        delete_current_workout_plan(_current_data_user_id())
        return jsonify({"status": "success", "settings": USER_SETTINGS})


@app.route('/api/settings/equipment', methods=['PUT'])
def settings_equipment():
    global LAST_WORKOUT_RECOMMENDATION
    data, err = get_json_body(required=True)
    if err:
        return err

    pref, err2 = _coerce_str(data.get("equipment_preference"), "equipment_preference", required=True, max_len=64)
    if err2:
        return err2
    if pref not in ("machines_only", "machines_and_cables", "all"):
        return api_error("Invalid equipment_preference", 400, code="invalid_field")

    USER_SETTINGS["equipment_preference"] = pref
    save_json(SETTINGS_FILE, USER_SETTINGS)
    LAST_WORKOUT_RECOMMENDATION = None
    delete_current_workout_plan(_current_data_user_id())
    return jsonify({"status": "success", "equipment_preference": pref})


@app.route('/api/personal-vocab', methods=['GET'])
def personal_vocab_entries():
    """List learned personal vocabulary mappings for Settings."""
    user_id = _current_data_user_id()
    return jsonify({"entries": list_personal_vocab_entries(user_id)})


@app.route('/api/personal-vocab/<path:normalized_input>', methods=['DELETE'])
def personal_vocab_delete(normalized_input):
    """Forget one learned personal vocabulary mapping."""
    normalized_input = (normalized_input or "").strip()
    if not normalized_input or len(normalized_input) > 500:
        return api_error("invalid vocabulary key", 400, code="invalid_field")
    removed = delete_personal_vocab_entry(_current_data_user_id(), normalized_input)
    status_code = 200 if removed else 404
    return jsonify({"status": "ok" if removed else "not_found", "removed": removed}), status_code


@app.route('/api/exercises/alternatives/<muscle_group>')
def exercise_alternatives(muscle_group):
    muscle = (muscle_group or "").strip().lower()
    if not muscle:
        return api_error("Invalid muscle group", 400, code="invalid_field")
    equipment_pref = USER_SETTINGS.get("equipment_preference", "machines_only")
    progression = calculate_progression_status(WORKOUTS)
    options = [
        {
            "name": ex["name"],
            "muscle": ex["muscle"],
            "equipment": ex.get("equipment"),
            "equipment_brands": ex.get("equipment_brands", []),
            "aliases": ex.get("aliases", []),
            "compound": ex.get("compound"),
            "load_hint": None if _has_direct_exercise_progression(ex["name"], progression) else _similar_exercise_load_source(ex["name"], progression),
        }
        for ex in _filtered_exercise_library(equipment_pref)
        if ex.get("muscle") == muscle
    ]
    return jsonify({"muscle": muscle, "equipment_preference": equipment_pref, "alternatives": options})


@app.route('/api/workout/swap', methods=['POST'])
def swap_workout_exercise():
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT
    data, err = get_json_body(required=True)
    if err:
        return err

    workout_index, err2 = _coerce_int(data.get("workout_index", 0), "workout_index", min_v=0, max_v=10_000)
    if err2:
        return err2
    exercise_index, err2 = _coerce_int(data.get("exercise_index"), "exercise_index", min_v=0, max_v=10_000)
    if err2:
        return err2
    new_name, err2 = _coerce_str(data.get("new_exercise_name"), "new_exercise_name", required=True, max_len=128)
    if err2:
        return err2

    fingerprint = _workout_recommendation_fingerprint()
    recommendation = _current_workout_plan_for_fingerprint(fingerprint, allow_stale_unsaved=True)
    if not recommendation:
        return api_error("No recent workout recommendation available", 404, code="not_found")

    exercises = recommendation.get("exercises") or []
    if not (0 <= exercise_index < len(exercises)):
        return api_error("exercise_index out of range", 404, code="not_found")

    old_ex = exercises[exercise_index]
    old_muscle = (old_ex.get("muscle") or "").lower()
    equipment_pref = USER_SETTINGS.get("equipment_preference", "machines_only")

    new_ex = _resolve_exercise_definition(new_name)
    if not new_ex:
        new_ex = _resolve_custom_swap_exercise(new_name, old_ex, equipment_pref)
    if not new_ex:
        return api_error("Unknown exercise name", 404, code="not_found")

    old_definition = _resolve_exercise_definition(old_ex.get("exercise"))
    if old_definition and _same_exercise_definition(new_ex, old_definition):
        return api_error("New exercise must be different from the current exercise", 400, code="invalid_field")
    if new_ex.get("muscle") != old_muscle:
        return api_error("New exercise must match the same muscle group", 400, code="invalid_field")
    if not _equipment_allowed(new_ex, equipment_pref):
        return api_error("New exercise is not allowed by the current equipment preference", 400, code="invalid_field")
    if not _exercise_user_allowed(new_ex):
        return api_error("New exercise is excluded by current exercise preferences", 400, code="invalid_field")

    goal = recommendation.get("goal") or USER_SETTINGS.get("training_goal", TrainingGoal.HYPERTROPHY.value)
    goal_params = GOAL_PARAMETERS.get(goal, GOAL_PARAMETERS[TrainingGoal.HYPERTROPHY.value])
    sessions_per_week = USER_SETTINGS.get("sessions_per_week_target", 3)
    meso_week = recommendation.get("mesocycle", {}).get("week") or _get_mesocycle_week(WORKOUTS, sessions_per_week)
    meso_plan = MESOCYCLE_PLAN.get(meso_week, MESOCYCLE_PLAN[1])
    oura_readiness = _get_oura_readiness_today()
    time_per_set = goal_params.get("time_per_set_minutes", 3)

    volume_multiplier = meso_plan["volume_multiplier"]
    if oura_readiness is not None and oura_readiness < 60:
        volume_multiplier *= 0.8

    volume_data = calculate_volume(WORKOUTS, weeks=4)
    progression = calculate_progression_status(WORKOUTS)

    updated_ex = _build_exercise_entry(
        exercise_name=new_ex["name"],
        muscle=new_ex["muscle"],
        is_compound=new_ex["compound"],
        goal_params=goal_params,
        meso_week=meso_week,
        volume_multiplier=volume_multiplier,
        oura_readiness=oura_readiness,
        volume_data=volume_data,
        soreness_data=SORENESS_DATA,
        progression=progression,
        workouts=WORKOUTS,
        time_per_set=time_per_set,
    )
    updated_ex["rationale"] = f"{updated_ex['rationale']} · Swapped from {old_ex.get('exercise')}"

    exercises[exercise_index] = updated_ex
    recommendation["exercises"] = exercises
    _persist_current_workout_plan(recommendation, fingerprint)

    # Persist the base plan (above), but return the wearable-adjusted view so an
    # active deload/caution clamps every exercise -- including the untouched ones
    # -- consistently with /api/next-workout (FIT-256 finding 2, display-only).
    return jsonify({"status": "success", "recommendation": _workout_with_display_and_auth_scope(recommendation)})


# ==================== AI COACH — ADJUST PLAN ====================
# AI coach adjustment spec:
# - LLM returns intent only; Python re-picks exercises and enforces safety.
# - RPE delta max ±1.0; sets delta max ±20%; weight cap +10% over recent e1RM.
# - Hard blacklist soreness/readiness-flagged muscles. Never override deload.
# - Timeout 8s. One concurrent request. Deterministic fallback on any failure.
# - Cache by workout_id + constraint + readiness_date + model_version + schema/library hash.

_ADJUST_CACHE_DB = os.path.join(DATA_DIR, "ai_coach_cache.sqlite3")
_ADJUST_CACHE_VERSION = "fit179-movement-resolution-v1"


def _ai_cache_init():
    conn = sqlite3.connect(_ADJUST_CACHE_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS adjust_cache (
            cache_key TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            response_json TEXT NOT NULL
        )"""
    )
    # Observability so remote inference flakiness shows up before user impact.
    # files a bug. One row per /api/workout/adjust call.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS adjust_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            outcome TEXT NOT NULL,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            constraint_len INTEGER NOT NULL DEFAULT 0,
            model_version TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT ''
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_adjust_metrics_ts ON adjust_metrics(ts)")
    conn.commit()
    conn.close()


_ai_cache_init()


def _ai_metric_log(outcome, latency_ms=0, constraint_len=0, model_version="", reason=""):
    """Record one /api/workout/adjust call. Swallow errors — metrics must
    never break the user-visible path."""
    try:
        with closing(sqlite3.connect(_ADJUST_CACHE_DB)) as conn:
            conn.execute(
                "INSERT INTO adjust_metrics (ts, outcome, latency_ms, constraint_len, model_version, reason) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    outcome,
                    int(latency_ms or 0),
                    int(constraint_len or 0),
                    model_version or "",
                    (reason or "")[:200],
                ),
            )
            conn.commit()
    except Exception as exc:
        print(f"WARN: ai_metric_log failed: {exc}")


def _exercise_library_hash(preference: str) -> str:
    import hashlib
    settings_fp = {
        "equipment_preference": preference,
        "preferred_equipment_brands": USER_SETTINGS.get("preferred_equipment_brands", []),
        "excluded_exercises": USER_SETTINGS.get("excluded_exercises", []),
        "exercises": [
            {
                "name": ex.get("name", ""),
                "equipment": ex.get("equipment", ""),
                "equipment_brands": ex.get("equipment_brands", []),
                "compound": bool(ex.get("compound")),
                "joints_loaded": ex.get("joints_loaded", []),
                "aliases": ex.get("aliases", []),
                "movement_patterns": ex.get("movement_patterns", []),
                "movement_pattern": ex.get("movement_pattern", ""),
                "body_position": ex.get("body_position", ""),
                "shoulder_position": ex.get("shoulder_position", ""),
                "primary_emphasis": ex.get("primary_emphasis", ""),
                "match_tokens": ex.get("match_tokens", []),
                "confusable_with": ex.get("confusable_with", []),
                "disambiguation": ex.get("disambiguation", ""),
            }
            for ex in _filtered_exercise_library(preference)
        ],
    }
    payload = json.dumps(settings_fp, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def _ai_cache_key(recommendation, constraint, readiness_date, model_version, equipment_pref):
    """Hash of workout-content + constraint + model + library so edits to the
    recommendation (swaps, adjustments) invalidate the cache properly."""
    import hashlib
    # Fingerprint the actual plan content so a swap or prior adjust changes
    # the key, not just the workout id (which often stays constant).
    content_fp = json.dumps({
        "focus": recommendation.get("focus"),
        "goal": recommendation.get("goal") or recommendation.get("goal_name"),
        "estimated_minutes": recommendation.get("estimated_minutes"),
        "exercises": [
            {
                "name": ex.get("exercise") or ex.get("machine") or ex.get("name"),
                "muscle": ex.get("muscle") or ex.get("muscle_group"),
                "target_sets": ex.get("target_sets") or ex.get("sets"),
                "target_reps": ex.get("target_reps") or ex.get("reps"),
                "target_weight": ex.get("target_weight") or ex.get("target_weight_lbs"),
                "rpe_target": ex.get("rpe_target") or ex.get("rpe"),
            }
            for ex in (recommendation.get("exercises") or [])
        ],
        "cardio_type": (recommendation.get("cardio") or {}).get("type"),
    }, default=str, sort_keys=True)
    parts = [
        _ADJUST_CACHE_VERSION,
        str(recommendation.get("id") or ""),
        hashlib.sha1(content_fp.encode()).hexdigest()[:16],
        (constraint or "").strip().lower(),
        readiness_date or "",
        model_version or "",
        _exercise_library_hash(equipment_pref),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def _ai_cache_get(key):
    try:
        with closing(sqlite3.connect(_ADJUST_CACHE_DB)) as conn:
            row = conn.execute(
                "SELECT response_json FROM adjust_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def _ai_cache_put(key, payload):
    try:
        with closing(sqlite3.connect(_ADJUST_CACHE_DB)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO adjust_cache (cache_key, created_at, response_json) VALUES (?, ?, ?)",
                (key, datetime.now().isoformat(), json.dumps(payload)),
            )
            conn.commit()
    except Exception as exc:
        print(f"WARN: adjust_cache write failed: {exc}")


def _apply_intent_patch(recommendation, intent, goal_params, meso_week, meso_plan, oura_readiness, equipment_pref):
    """Apply the LLM's intent patch to the deterministic recommendation.

    Every mutation is clamped/validated here. The LLM cannot override safety.
    Returns (patched_recommendation, applied_notes) where applied_notes is a
    list of human-readable strings describing what actually changed.
    """
    notes = []
    exercises = list(recommendation.get("exercises") or [])

    def _normalize_avoid_joints(raw_items):
        normalized = []
        for item in raw_items or []:
            if isinstance(item, str):
                parts = item.lower().replace("_", " ").split()
                side = next((p for p in parts if p in {"left", "right", "both"}), "both")
                joint = next((p for p in parts if p in {"shoulder", "elbow", "wrist", "hip", "knee", "ankle", "spine"}), "")
            elif isinstance(item, dict):
                side = str(item.get("side") or "both").strip().lower()
                joint = str(item.get("joint") or "").strip().lower()
            else:
                continue
            if side not in {"left", "right", "both"}:
                side = "both"
            if joint in {"low_back", "lower_back", "back"}:
                joint = "spine"
            if joint in {"shoulder", "elbow", "wrist", "hip", "knee", "ankle", "spine"}:
                normalized.append({"side": side, "joint": joint})
        return normalized

    def _exercise_loads_avoided_joint(exercise_name, avoid_joints):
        if not avoid_joints:
            return None
        library_entry = EXERCISE_LOOKUP.get(exercise_name)
        loaded = set((library_entry or {}).get("joints_loaded") or [])
        hit = next((item for item in avoid_joints if item["joint"] in loaded), None)
        return hit

    # ── 1) Respect deload: skip rpe_delta / sets_delta_pct entirely on deload.
    deload_active = bool((meso_plan.get("name") == "Deload"))

    # ── 2) Clamp rpe_delta to ±1.0; disallow upward on poor readiness.
    rpe_delta_raw = float(intent.get("rpe_delta") or 0)
    rpe_delta = max(-1.0, min(1.0, rpe_delta_raw))
    if deload_active and rpe_delta_raw > 0:
        rpe_delta = min(0.0, rpe_delta)  # deload can only reduce
        notes.append(f"Ignored: RPE increase (+{rpe_delta_raw:+.1f}) — deload week")
    if oura_readiness is not None and oura_readiness < 60 and rpe_delta_raw > 0:
        rpe_delta = min(0.0, rpe_delta)
        notes.append(f"Ignored: RPE increase (+{rpe_delta_raw:+.1f}) — readiness {oura_readiness}/100")

    # ── 3) Clamp sets_delta_pct to ±20; no upward on deload/low readiness.
    sets_delta_raw = float(intent.get("sets_delta_pct") or 0)
    sets_delta = max(-20.0, min(20.0, sets_delta_raw))
    if deload_active or (oura_readiness is not None and oura_readiness < 60):
        sets_delta = min(0.0, sets_delta)

    # ── 4) Hard blacklist avoid_muscles / avoid_joints using current soreness data.
    avoid_muscles = {m.strip().lower() for m in (intent.get("avoid_muscles") or []) if isinstance(m, str)}
    avoid_joints = _normalize_avoid_joints(intent.get("avoid_joints") or [])
    recent_soreness = filter_recent_soreness(SORENESS_DATA, hours=48)
    sore_map = {}
    for s in recent_soreness:
        # Soreness rows are stored with key "muscle" by /api/add-soreness;
        # accept legacy "muscle_group" / "body_part" too.
        muscle = (s.get("muscle") or s.get("muscle_group") or s.get("body_part") or "").strip().lower()
        if not muscle:
            continue
        try:
            level = int(s.get("soreness_level") or 0)
        except (TypeError, ValueError):
            level = 0
        sore_map[muscle] = max(sore_map.get(muscle, 0), level)
    # Anything flagged ≥7 is blacklisted regardless of LLM input.
    avoid_muscles.update(m for m, lvl in sore_map.items() if lvl >= 7)

    if avoid_muscles:
        kept = []
        for ex in exercises:
            muscle = (ex.get("muscle") or "").lower()
            if muscle in avoid_muscles:
                ex_name = ex.get("exercise") or ex.get("name") or ex.get("machine") or "exercise"
                reason_bits = [f"{muscle} avoided"]
                if sore_map.get(muscle, 0) >= 7:
                    reason_bits.append(f"soreness {sore_map[muscle]}/10")
                notes.append(f"Removed: {ex_name} — {', '.join(reason_bits)}")
            else:
                kept.append(ex)
        exercises = kept

    swap_requests = intent.get("swap") or []
    joint_swap_sources = {
        (sw.get("replace_exercise") or "").strip().lower()
        for sw in swap_requests
        if isinstance(sw, dict) and (sw.get("replace_exercise") or "").strip()
    }

    if avoid_joints:
        kept = []
        for ex in exercises:
            ex_name = ex.get("exercise") or ex.get("name") or ex.get("machine") or "exercise"
            avoided = _exercise_loads_avoided_joint(ex_name, avoid_joints)
            if avoided:
                if any(src in ex_name.lower() for src in joint_swap_sources):
                    kept.append(ex)
                    continue
                notes.append(f"Removed: {ex_name} — loads {avoided['side']} {avoided['joint']}")
            else:
                kept.append(ex)
        exercises = kept

    # ── 5) Apply swaps. LLM names the muscle; Python picks the exercise.
    volume_data = calculate_volume(WORKOUTS, weeks=4)
    progression = calculate_progression_status(WORKOUTS)
    time_per_set = goal_params.get("time_per_set_minutes", 3)
    volume_multiplier = meso_plan["volume_multiplier"]
    if oura_readiness is not None and oura_readiness < 60:
        volume_multiplier *= 0.8

    for sw in swap_requests:
        if not isinstance(sw, dict):
            continue
        raw_src_name = (sw.get("replace_exercise") or "").strip()
        src_name = raw_src_name.lower()
        src_definition = _resolve_exercise_definition(raw_src_name) if raw_src_name else None
        replace_index = sw.get("replace_index")
        target_muscle = (sw.get("target_muscle") or "").strip().lower()
        if not target_muscle or (not src_name and not isinstance(replace_index, int)):
            continue
        if target_muscle in avoid_muscles:
            notes.append(f"Ignored: swap to {target_muscle} — muscle is avoided")
            continue

        # Find the exercise to replace by name (case-insensitive, contains).
        def _source_matches_plan_exercise(plan_ex):
            if not src_name:
                return True
            if src_definition and _same_exercise_definition(_plan_exercise_definition(plan_ex), src_definition):
                return True
            plan_name = plan_ex.get("exercise") or plan_ex.get("machine") or plan_ex.get("name") or ""
            return src_name in plan_name.lower()

        idx = None
        if isinstance(replace_index, int) and 0 <= replace_index < len(exercises):
            if _source_matches_plan_exercise(exercises[replace_index]):
                idx = replace_index
        if idx is None:
            for i, ex in enumerate(exercises):
                if _source_matches_plan_exercise(ex):
                    idx = i
                    break
        if idx is None:
            notes.append(f"could not locate '{sw.get('replace_exercise')}' in current plan")
            continue

        target_exercise_name = (sw.get("target_exercise") or "").strip()
        requested_exercise = _resolve_exercise_definition(target_exercise_name)
        if target_exercise_name and not requested_exercise:
            notes.append(f"Ignored: unknown target exercise '{target_exercise_name}'")
            continue

        # Pick a new exercise for target_muscle from the equipment-filtered library,
        # preferring compound movements and rotating off recently-trained exercises.
        library = [
            ex for ex in _filtered_exercise_library(equipment_pref)
            if ex.get("muscle") == target_muscle
            and not _exercise_loads_avoided_joint(ex.get("name"), avoid_joints)
        ]
        if not library:
            notes.append(f"no exercises available for muscle '{target_muscle}' under current equipment/joint constraints")
            continue
        # _filtered_exercise_library already applies brand, compound, and name ranking.
        # Avoid picking something already in the plan, including aliases.
        def _plan_already_contains(candidate):
            return any(
                _same_exercise_definition(_plan_exercise_definition(plan_ex), candidate)
                for plan_ex in exercises
            )

        if requested_exercise:
            if requested_exercise not in library:
                continue
            if _plan_already_contains(requested_exercise):
                continue
            picked = requested_exercise
        else:
            picked = next((e for e in library if not _plan_already_contains(e)), library[0])

        new_entry = _build_exercise_entry(
            exercise_name=picked["name"],
            muscle=picked["muscle"],
            is_compound=picked["compound"],
            goal_params=goal_params,
            meso_week=meso_week,
            volume_multiplier=volume_multiplier,
            oura_readiness=oura_readiness,
            volume_data=volume_data,
            soreness_data=SORENESS_DATA,
            progression=progression,
            workouts=WORKOUTS,
            time_per_set=time_per_set,
        )
        reason = sw.get("reason") or ""
        old_name = exercises[idx].get('exercise')
        new_entry["rationale"] = f"{new_entry.get('rationale', '')} · Swapped from {old_name} ({reason})".strip(" ·")
        exercises[idx] = new_entry
        reason_suffix = f" — {reason}" if reason else ""
        notes.append(f"Swapped: {old_name} → {picked['name']}{reason_suffix}")

    if avoid_joints:
        kept = []
        for ex in exercises:
            ex_name = ex.get("exercise") or ex.get("name") or ex.get("machine") or "exercise"
            avoided = _exercise_loads_avoided_joint(ex_name, avoid_joints)
            if avoided:
                notes.append(f"Removed: {ex_name} — loads {avoided['side']} {avoided['joint']}")
            else:
                kept.append(ex)
        exercises = kept

    # ── 6) Apply rpe_delta and sets_delta_pct to each exercise, enforcing caps.
    if rpe_delta != 0 or sets_delta != 0:
        for ex in exercises:
            if rpe_delta:
                cur = float(ex.get("rpe_target") or goal_params.get("rpe_target") or 7)
                ex["rpe_target"] = max(1.0, min(10.0, cur + rpe_delta))
            if sets_delta:
                cur = int(ex.get("target_sets") or goal_params.get("sets_per_exercise") or 3)
                new_sets = max(1, round(cur * (1 + sets_delta / 100.0)))
                # Never add sets on deload
                if deload_active:
                    new_sets = min(cur, new_sets)
                ex["target_sets"] = new_sets
        if rpe_delta:
            notes.append(f"RPE adjusted {rpe_delta:+.1f} across all exercises")
        if sets_delta:
            notes.append(f"Sets adjusted {sets_delta:+.0f}% across all exercises")

    # ── 7) Weight guard: cap every exercise at +10% of its recent e1RM-derived load.
    for ex in exercises:
        ex_name = ex.get("exercise") or ""
        prog = progression.get(ex_name) or {}
        e1rm = prog.get("current_e1rm") or prog.get("peak_e1rm") or 0
        if e1rm and ex.get("target_weight"):
            target_w = float(ex.get("target_weight") or 0)
            cap = e1rm * 1.10
            if target_w > cap:
                ex["target_weight"] = round(cap, 1)
                notes.append(f"Capped: {ex_name} weight → {round(cap, 1)} lb (10% above e1RM ceiling)")

    # ── 8) Duration cap — if user gave a tight time box, trim tail exercises.
    duration_cap = float(intent.get("duration_cap_min") or 0)
    if duration_cap and duration_cap > 0:
        budget = duration_cap - 10  # warmup+cooldown
        minutes_used = 0
        kept = []
        for ex in exercises:
            ex_minutes = float(ex.get("target_sets") or 3) * time_per_set
            if minutes_used + ex_minutes > budget:
                break
            minutes_used += ex_minutes
            kept.append(ex)
        if len(kept) < len(exercises):
            dropped = [ex.get("exercise") or ex.get("name") or "exercise" for ex in exercises[len(kept):]]
            notes.append(f"Trimmed: {', '.join(dropped)} — fits {int(duration_cap)} min window")
            exercises = kept
        recommendation["estimated_minutes"] = int(minutes_used + 10)

    # ── 9) Drop cardio if requested.
    if intent.get("drop_cardio"):
        recommendation["cardio"] = None
        notes.append("Removed: Cardio finisher — per your request")

    recommendation["exercises"] = exercises
    return recommendation, notes


@app.route('/api/workout/adjust', methods=['POST'])
def adjust_workout():
    """AI coach adjustment: accept a natural-language constraint and return a
    safety-validated patch of the current deterministic recommendation.

    On any failure (LM Studio down, bad JSON, anything unexpected) we return
    the deterministic plan unchanged with status='fallback' so the UI can show
    a "AI coach unavailable" chip without breaking.
    """
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT

    data, err = get_json_body(required=True)
    if err:
        return err

    constraint, err2 = _coerce_str(data.get("constraint"), "constraint", required=True, max_len=280)
    if err2:
        return err2

    fingerprint = _workout_recommendation_fingerprint()
    recommendation = _current_workout_plan_for_fingerprint(fingerprint, allow_stale_unsaved=True)
    if not recommendation:
        recommendation = generate_next_workout(WORKOUTS, SORENESS_DATA)
        _persist_current_workout_plan(recommendation, fingerprint)

    goal = recommendation.get("goal") or USER_SETTINGS.get("training_goal", TrainingGoal.HYPERTROPHY.value)
    goal_params = GOAL_PARAMETERS.get(goal, GOAL_PARAMETERS[TrainingGoal.HYPERTROPHY.value])
    sessions_per_week = USER_SETTINGS.get("sessions_per_week_target", 3)
    meso_week = recommendation.get("mesocycle", {}).get("week") or _get_mesocycle_week(WORKOUTS, sessions_per_week)
    meso_plan = MESOCYCLE_PLAN.get(meso_week, MESOCYCLE_PLAN[1])
    oura_readiness = _get_oura_readiness_today()
    equipment_pref = USER_SETTINGS.get("equipment_preference", "machines_only")
    deterministic_swap = _build_deterministic_adjust_swap(constraint, recommendation, equipment_pref)

    if not _lm_studio:
        if deterministic_swap:
            payload = _deterministic_adjust_payload(
                recommendation,
                constraint,
                deterministic_swap,
                goal_params,
                meso_week,
                meso_plan,
                oura_readiness,
                equipment_pref,
                "adapter_missing",
            )
            _persist_current_workout_plan(payload["recommendation"], fingerprint)
            _ai_metric_log("ok", reason="deterministic_fallback: adapter_missing", constraint_len=len(constraint))
            return jsonify(_payload_with_recommendation_display_scope(payload))
        _ai_metric_log("fallback", reason="adapter_missing", constraint_len=len(constraint))
        return jsonify({
            "status": "fallback",
            "reason": "LM Studio adapter not available on this server",
            "recommendation": _workout_with_display_and_auth_scope(recommendation),
            "summary": None,
            "applied_notes": [],
        })

    readiness_date = _today_str()
    route_candidate = _lm_studio.active_candidate()
    route_model_version = _lm_studio.model_version_for(route_candidate)
    cache_key = None
    cache_probe_versions = [route_model_version]
    if route_candidate and route_candidate.get("role") == "primary":
        cache_probe_versions.extend(_lm_studio.model_versions_after(route_candidate))
    elif route_candidate is None:
        cache_probe_versions.extend(_lm_studio.fallback_model_versions())
    for probe_model_version in dict.fromkeys(cache_probe_versions):
        probe_cache_key = _ai_cache_key(
            recommendation,
            constraint,
            readiness_date,
            probe_model_version,
            equipment_pref,
        )
        if probe_model_version == route_model_version:
            cache_key = probe_cache_key
        cached = _ai_cache_get(probe_cache_key)
        if cached:
            cached["cache_hit"] = True
            _ai_metric_log("cache_hit", latency_ms=0, constraint_len=len(constraint), model_version=probe_model_version)
            # Keep server-side canonical plan in sync with what the client sees,
            # so a follow-up Adjust or Swap operates on the patched plan, not the
            # pre-adjust plan that's still in LAST_WORKOUT_RECOMMENDATION.
            if cached.get("recommendation"):
                _persist_current_workout_plan(cached["recommendation"], fingerprint)
            return jsonify(_payload_with_recommendation_display_scope(cached))

    if route_candidate is None:
        if deterministic_swap:
            payload = _deterministic_adjust_payload(
                recommendation,
                constraint,
                deterministic_swap,
                goal_params,
                meso_week,
                meso_plan,
                oura_readiness,
                equipment_pref,
                "preflight_unavailable",
            )
            _persist_current_workout_plan(payload["recommendation"], fingerprint)
            _ai_metric_log("ok", reason="deterministic_fallback: preflight_unavailable", constraint_len=len(constraint), model_version=route_model_version)
            return jsonify(_payload_with_recommendation_display_scope(payload))
        _ai_metric_log(
            "fallback",
            constraint_len=len(constraint),
            model_version=route_model_version,
            reason="preflight: all endpoints unavailable",
        )
        return jsonify({
            "status": "fallback",
            "reason": "LM Studio: all endpoints unavailable",
            "recommendation": _workout_with_display_and_auth_scope(recommendation),
            "summary": None,
            "applied_notes": [],
        })

    # Send readiness context the LLM can reason about.
    readiness_ctx = {
        "oura_readiness": oura_readiness,
        "mesocycle_week": meso_week,
        "deload_active": bool((meso_plan.get("name") == "Deload")),
    }

    try:
        raw_patch = _lm_studio.adjust_plan(
            recommendation,
            constraint,
            readiness=readiness_ctx,
            preflighted_candidate=route_candidate,
        )
    except _lm_studio.LmStudioError as exc:
        reason_code = "timeout" if "timeout" in str(exc).lower() else "unreachable" if "unreachable" in str(exc).lower() else "invalid_json" if "json" in str(exc).lower() else "error"
        if deterministic_swap:
            payload = _deterministic_adjust_payload(
                recommendation,
                constraint,
                deterministic_swap,
                goal_params,
                meso_week,
                meso_plan,
                oura_readiness,
                equipment_pref,
                reason_code,
            )
            _persist_current_workout_plan(payload["recommendation"], fingerprint)
            _ai_metric_log(
                "ok",
                constraint_len=len(constraint),
                model_version=route_model_version,
                reason=f"deterministic_fallback: {reason_code}",
            )
            return jsonify(_payload_with_recommendation_display_scope(payload))
        _ai_metric_log(
            "fallback",
            constraint_len=len(constraint),
            model_version=route_model_version,
            reason=f"{reason_code}: {exc}",
        )
        return jsonify({
            "status": "fallback",
            "reason": f"LM Studio: {exc}",
            "recommendation": _workout_with_display_and_auth_scope(recommendation),
            "summary": None,
            "applied_notes": [],
        })

    intent = _merge_deterministic_adjust_swap(raw_patch.get("intent") or {}, deterministic_swap)
    summary = raw_patch.get("summary") or ""
    raw_meta = raw_patch.get("_meta") or {}
    actual_model_version = raw_meta.get("model_version") or route_model_version

    # Deep-copy the recommendation so the in-memory canonical isn't mutated
    # if the user re-opens without applying.
    patched = json.loads(json.dumps(recommendation, default=str))
    try:
        patched, applied_notes = _apply_intent_patch(
            patched, intent, goal_params, meso_week, meso_plan, oura_readiness, equipment_pref
        )
    except Exception as exc:
        # Any unexpected failure in safety-rail application (type error from a
        # drifted LLM output that slipped past validation, missing helper
        # preconditions, etc.) must not leak as a 500. Fall back to the
        # deterministic plan, log the failure in metrics.
        _ai_metric_log(
            "fallback",
            constraint_len=len(constraint),
            model_version=actual_model_version,
            reason=f"apply_patch_error: {type(exc).__name__}: {str(exc)[:80]}",
        )
        return jsonify({
            "status": "fallback",
            "reason": f"safety-rail error: {type(exc).__name__}",
            "recommendation": _workout_with_display_and_auth_scope(recommendation),
            "summary": None,
            "applied_notes": [],
        })

    intent_is_empty = (
        not intent.get("avoid_muscles")
        and not intent.get("avoid_joints")
        and not intent.get("swap")
        and not intent.get("rpe_delta")
        and not intent.get("sets_delta_pct")
        and not intent.get("duration_cap_min")
        and not intent.get("drop_cardio")
    )
    if applied_notes:
        result_kind = "changed"
    elif intent_is_empty:
        result_kind = "refused"
    else:
        result_kind = "unchanged"

    payload = {
        "status": "ok",
        "result_kind": result_kind,
        "recommendation": patched,
        "summary": summary,
        "applied_notes": applied_notes,
        "constraint": constraint,
        "meta": raw_meta,
        "cache_hit": False,
    }
    cache_write_key = (
        cache_key
        if actual_model_version == route_model_version
        else _ai_cache_key(
            recommendation,
            constraint,
            readiness_date,
            actual_model_version,
            equipment_pref,
        )
    )
    # Cache and persist the BASE patched plan (display transform must not be
    # baked into the cache or the durable row -- FIT-256 finding 2); apply the
    # wearable display transform only on the outgoing response.
    _ai_cache_put(cache_write_key, payload)
    _persist_current_workout_plan(patched, fingerprint)
    _ai_metric_log(
        "ok",
        latency_ms=raw_meta.get("elapsed_ms", 0),
        constraint_len=len(constraint),
        model_version=actual_model_version,
    )
    return jsonify(_payload_with_recommendation_display_scope(payload))


def _exercise_display_name(ex):
    return ex.get("machine") or ex.get("exercise") or ex.get("name") or "Exercise"


def _analysis_number(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _workout_analysis_response(target, analysis, recent_compact, progression_subset, oura_snapshot, notes_context, *, status="ok", meta=None, cache_hit=False):
    return {
        "status": status,
        "workout": {
            "id": target.get("id"),
            "date": target.get("date"),
            "session_type": target.get("session_type") or target.get("focus"),
            "total_sets": target.get("total_sets"),
            "total_volume": target.get("total_volume"),
            "duration_minutes": target.get("duration_minutes"),
        },
        "analysis": {
            "summary": analysis.get("summary"),
            "wins": analysis.get("wins") or [],
            "concerns": analysis.get("concerns") or [],
            "comparison": analysis.get("comparison"),
            "next_session_cue": analysis.get("next_session_cue"),
        },
        "context_used": {
            "recent_session_count": len(recent_compact),
            "exercise_progression_available": list(progression_subset.keys()),
            "readiness_available": oura_snapshot is not None,
            **notes_context,
        },
        "meta": meta or {},
        "cache_hit": cache_hit,
    }


def _deterministic_workout_analysis(target, recent_compact, progression_subset, notes_context, reason):
    exercises = target.get("exercises") or []
    set_count = sum(len(ex.get("sets") or []) for ex in exercises)
    volume = target.get("total_volume")
    if volume is None:
        volume = sum(
            _analysis_number(s.get("weight_lbs")) * _analysis_number(s.get("reps"))
            for ex in exercises
            for s in (ex.get("sets") or [])
        )
    else:
        volume = _analysis_number(volume)
    exercise_names = [_exercise_display_name(ex) for ex in exercises]
    notable_notes = []
    high_rpe = False
    for ex in exercises:
        name = _exercise_display_name(ex)
        for s in (ex.get("sets") or []):
            note = (s.get("notes") or "").strip()
            if note:
                notable_notes.append(f"{name}: {note}")
            try:
                high_rpe = high_rpe or _analysis_number(s.get("rpe")) >= 9
            except Exception:
                pass

    wins = []
    if set_count:
        wins.append(f"Logged {set_count} working set{'s' if set_count != 1 else ''} across {len(exercises)} exercise{'s' if len(exercises) != 1 else ''}.")
    if volume:
        wins.append(f"Captured about {int(round(volume)):,} lb of recorded volume.")
    if notes_context.get("set_note_count") or notes_context.get("workout_notes_present"):
        wins.append("Captured session notes, so the next recommendation can account for how sets actually felt.")
    if not wins:
        wins.append("Workout data was saved and is available for the training history.")

    concerns = []
    sore_notes = [n for n in notable_notes if any(term in n.lower() for term in ("sore", "pain", "hurt", "ache", "tight", "struggle"))]
    if sore_notes:
        concerns.append("Some set notes mention soreness or struggle; treat those muscles conservatively next session.")
    if high_rpe:
        concerns.append("At least one set reached very high RPE, so avoid stacking another hard session on the same area too soon.")
    if not recent_compact:
        concerns.append("No recent matching sessions were available for a strong trend comparison.")

    if recent_compact:
        recent_sets = [int(_analysis_number(w.get("total_sets"))) for w in recent_compact if w.get("total_sets") is not None]
        recent_volumes = [_analysis_number(w.get("total_volume_lbs")) for w in recent_compact if w.get("total_volume_lbs") is not None]
        avg_sets = round(sum(recent_sets) / len(recent_sets), 1) if recent_sets else None
        avg_volume = round(sum(recent_volumes) / len(recent_volumes)) if recent_volumes else None
        comparison_bits = [f"Compared with {len(recent_compact)} recent related session{'s' if len(recent_compact) != 1 else ''}"]
        if avg_sets is not None:
            comparison_bits.append(f"recent average was {avg_sets} sets")
        if avg_volume:
            comparison_bits.append(f"about {avg_volume:,} lb average volume")
        comparison = "; ".join(comparison_bits) + "."
    else:
        comparison = "No recent same-muscle sessions were available, so this fallback is based on the logged workout itself."

    if sore_notes or high_rpe:
        cue = "Next session: keep the affected muscles at moderate RPE, repeat or slightly reduce load, and prioritize clean reps."
    elif progression_subset:
        cue = "Next session: use the saved progression data and add load or reps only if warmups feel normal."
    else:
        cue = "Next session: repeat the same movement pattern and progress slowly if the first working sets feel solid."

    summary_target = ", ".join(exercise_names[:3]) if exercise_names else "this workout"
    if len(exercise_names) > 3:
        summary_target += f", plus {len(exercise_names) - 3} more"
    summary = f"Fallback analysis used because {reason}. {summary_target} is logged with enough detail to guide the next session."

    return {
        "summary": summary,
        "wins": wins,
        "concerns": concerns,
        "comparison": comparison,
        "next_session_cue": cue,
    }


@app.route('/api/workout/analyze', methods=['POST'])
def analyze_workout():
    """Post-mortem on one logged workout.

    Accepts either {"workout_id": "..."} to analyze a specific stored workout,
    {"workout_date": "YYYY-MM-DD"} to pick the most recent session on that day,
    or {"latest": true} for the most recently completed session.
    """
    data, err = get_json_body(required=False) if False else (request.get_json(force=True, silent=True) or {}, None)
    if err:
        return err

    workout_id = data.get("workout_id")
    workout_date = data.get("workout_date")
    want_latest = bool(data.get("latest"))

    target = None
    if workout_id:
        target = next((w for w in WORKOUTS if w.get("id") == workout_id), None)
    elif workout_date:
        matches = [w for w in WORKOUTS if w.get("date") == workout_date]
        if matches:
            target = matches[-1]
    elif want_latest:
        if WORKOUTS:
            # Prefer created_at (precise timestamp) over date alone so the
            # correct session wins when multiple were logged the same day.
            target = max(
                WORKOUTS,
                key=lambda w: (w.get("created_at") or "", w.get("date") or "", w.get("id") or ""),
            )

    if not target:
        return api_error("workout not found — supply workout_id, workout_date, or latest=true", 404, code="not_found")

    set_note_count = sum(
        1
        for ex in (target.get("exercises") or [])
        for s in (ex.get("sets") or [])
        if (s.get("notes") or "").strip()
    )
    notes_context = {
        "set_note_count": set_note_count,
        "workout_notes_present": bool((target.get("notes") or "").strip()),
        "cardio_notes_present": bool(((target.get("cardio") or {}).get("notes") or "").strip()),
    }

    # Build context: sessions touching any of the same muscles in the 28 days
    # BEFORE the target workout (not before today) so historical analyses get
    # the right context, plus progression data for each exercise in the target.
    target_date = target.get("date")
    try:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else datetime.now().date()
    except Exception:
        target_dt = datetime.now().date()
    cutoff_date = target_dt - timedelta(days=28)
    target_muscles = {
        (ex.get("muscle_group") or ex.get("muscle") or "").lower()
        for ex in (target.get("exercises") or [])
        if (ex.get("muscle_group") or ex.get("muscle"))
    }
    target_exercise_names = {
        (ex.get("machine") or ex.get("exercise") or ex.get("name") or "").lower()
        for ex in (target.get("exercises") or [])
    }

    recent = []
    for w in WORKOUTS:
        d = w.get("date")
        if not d or d == target_date:
            continue
        try:
            wd = datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            continue
        if wd < cutoff_date or wd >= target_dt:
            continue
        w_muscles = {
            (ex.get("muscle_group") or ex.get("muscle") or "").lower()
            for ex in (w.get("exercises") or [])
        }
        if w_muscles & target_muscles:
            recent.append(w)
    recent.sort(key=lambda w: w.get("date") or "", reverse=True)
    recent_compact = []
    for w in recent[:6]:
        recent_compact.append({
            "date": w.get("date"),
            "session_type": w.get("session_type") or w.get("focus"),
            "duration_minutes": w.get("duration_minutes"),
            "total_sets": w.get("total_sets"),
            "total_volume_lbs": w.get("total_volume"),
            "exercises": [
                {
                    "name": ex.get("machine") or ex.get("exercise") or ex.get("name"),
                    "muscle": ex.get("muscle_group") or ex.get("muscle"),
                    "set_count": len(ex.get("sets") or []),
                    "top_weight": max(
                        (_analysis_number(s.get("weight_lbs")) for s in (ex.get("sets") or [])),
                        default=0,
                    ),
                    "top_rpe": max(
                        (_analysis_number(s.get("rpe")) for s in (ex.get("sets") or [])),
                        default=0,
                    ),
                }
                for ex in (w.get("exercises") or [])
            ],
        })

    try:
        progression_all = calculate_progression_status(WORKOUTS)
    except Exception:
        progression_all = {}
    progression_subset = {
        name: {
            "current_e1rm": data_.get("current_e1rm"),
            "peak_e1rm": data_.get("peak_e1rm"),
            "trend_pct": data_.get("trend_pct"),
            "status": data_.get("status"),
        }
        for name, data_ in progression_all.items()
        if name.lower() in target_exercise_names
    }

    oura_snapshot = None
    try:
        oura_row = get_oura_daily(OURA_DB_FILE, target_date)
        if oura_row:
            oura_snapshot = {
                "readiness": oura_row.get("readiness_score"),
                "sleep_score": oura_row.get("sleep_score"),
                "hrv": oura_row.get("hrv"),
                "resting_hr": oura_row.get("resting_hr"),
            }
    except Exception:
        oura_snapshot = None

    goal = USER_SETTINGS.get("training_goal", TrainingGoal.HYPERTROPHY.value)
    goal_params = GOAL_PARAMETERS.get(goal, GOAL_PARAMETERS[TrainingGoal.HYPERTROPHY.value])

    llm_context = {
        "recent_sessions": recent_compact,
        "progression": progression_subset,
        "readiness": oura_snapshot,
        "goal": {
            "name": goal,
            "rpe_target": goal_params.get("rpe_target"),
            "rep_range": goal_params.get("rep_range"),
        },
    }

    model_version = getattr(_lm_studio, "LM_STUDIO_MODEL_VERSION", "deterministic-fallback")
    prompt_version = getattr(_lm_studio, "ANALYZE_PROMPT_VERSION", "deterministic-v1")

    if not _lm_studio:
        reason = "LM Studio adapter not available"
        analysis = _deterministic_workout_analysis(target, recent_compact, progression_subset, notes_context, reason)
        _ai_metric_log("fallback", constraint_len=0, model_version=model_version, reason="analyze: adapter_missing")
        return jsonify(_workout_analysis_response(
            target,
            analysis,
            recent_compact,
            progression_subset,
            oura_snapshot,
            notes_context,
            status="fallback",
            meta={
                "fallback": True,
                "fallback_reason": reason,
                "model_version": model_version,
                "analysis_source": "deterministic",
            },
        ))

    # Cache key based on workout content + model version so re-analyzing the
    # same unchanged workout doesn't re-spend tokens.
    import hashlib
    fingerprint_src = json.dumps({
        "analysis_prompt_version": prompt_version,
        "target": {
            "date": target.get("date"),
            "exercises": [
                {
                    "name": ex.get("machine") or ex.get("exercise"),
                    "sets": [
                        {
                            "r": s.get("reps"),
                            "w": s.get("weight_lbs"),
                            "rpe": s.get("rpe"),
                            "notes": s.get("notes") or "",
                        }
                        for s in (ex.get("sets") or [])
                    ],
                }
                for ex in (target.get("exercises") or [])
            ],
            "notes": target.get("notes") or "",
            "cardio": target.get("cardio"),
        },
        "recent_keys": [w.get("date") for w in recent_compact],
        "model": model_version,
    }, default=str, sort_keys=True)
    cache_key = "analyze:" + hashlib.sha1(fingerprint_src.encode()).hexdigest()
    cached = _ai_cache_get(cache_key)
    if cached:
        cached["cache_hit"] = True
        cached_ctx = cached.setdefault("context_used", {})
        cached_ctx.update(notes_context)
        _ai_metric_log("cache_hit", constraint_len=0, model_version=model_version, reason="analyze")
        return jsonify(cached)

    try:
        result = _lm_studio.analyze_workout(target, llm_context)
    except (_lm_studio.LmStudioError, Exception) as exc:
        reason = f"LM Studio: {exc}" if isinstance(exc, _lm_studio.LmStudioError) else f"analysis failure: {exc}"
        _ai_metric_log("fallback", constraint_len=0, model_version=model_version, reason=f"analyze: {exc}")
        analysis = _deterministic_workout_analysis(target, recent_compact, progression_subset, notes_context, reason)
        return jsonify(_workout_analysis_response(
            target,
            analysis,
            recent_compact,
            progression_subset,
            oura_snapshot,
            notes_context,
            status="fallback",
            meta={
                "fallback": True,
                "fallback_reason": reason,
                "model_version": model_version,
                "analysis_source": "deterministic",
            },
        ))

    payload = _workout_analysis_response(
        target,
        result,
        recent_compact,
        progression_subset,
        oura_snapshot,
        notes_context,
        status="ok",
        meta=result.get("_meta", {}),
        cache_hit=False,
    )
    _ai_cache_put(cache_key, payload)
    _ai_metric_log(
        "ok",
        latency_ms=(result.get("_meta") or {}).get("elapsed_ms", 0),
        constraint_len=0,
        model_version=model_version,
        reason="analyze",
    )
    return jsonify(payload)


APP_SHELL_RELOAD_COOKIE = "fd_shell_reload"
APP_SHELL_RELOAD_VERSION = "20260525-fit181-controller-reload-r2"
APP_SHELL_RELOAD_COOKIE_MAX_AGE_S = 365 * 24 * 60 * 60


def _shell_reload_required_response():
    response = jsonify({"error": "reload_required", "reload": True})
    response.status_code = 401
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        APP_SHELL_RELOAD_COOKIE,
        APP_SHELL_RELOAD_VERSION,
        max_age=APP_SHELL_RELOAD_COOKIE_MAX_AGE_S,
        httponly=True,
        secure=request.is_secure,
        samesite="Lax",
        path="/",
    )
    return response


def _is_browser_app_shell_request():
    user_agent = request.headers.get("User-Agent", "")
    if "Mozilla/" in user_agent:
        return True
    return any(
        request.headers.get(header)
        for header in ("Sec-Fetch-Dest", "Sec-Fetch-Mode", "Sec-Fetch-Site")
    )


@app.route('/api/ai/health')
def ai_health():
    if (
        request.cookies.get("session")
        and request.cookies.get(APP_SHELL_RELOAD_COOKIE) != APP_SHELL_RELOAD_VERSION
        and _is_browser_app_shell_request()
    ):
        return _shell_reload_required_response()
    if not _lm_studio:
        return jsonify({"reachable": False, "error": "adapter not loaded"})
    return jsonify(_lm_studio.health())


@app.route('/api/ai/metrics')
def ai_metrics():
    """Aggregate last-N-hours stats so inference flakiness shows up."""
    try:
        hours, _ = _coerce_int(request.args.get("hours", 24), "hours", min_v=1, max_v=720)
        hours = hours or 24
    except Exception:
        hours = 24

    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    try:
        with closing(sqlite3.connect(_ADJUST_CACHE_DB)) as conn:
            rows = conn.execute(
                "SELECT outcome, COUNT(*), COALESCE(AVG(NULLIF(latency_ms,0)),0) FROM adjust_metrics WHERE ts >= ? GROUP BY outcome",
                (cutoff,),
            ).fetchall()
            last5 = conn.execute(
                "SELECT ts, outcome, latency_ms, reason FROM adjust_metrics WHERE ts >= ? ORDER BY id DESC LIMIT 5",
                (cutoff,),
            ).fetchall()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    buckets = {"ok": 0, "cache_hit": 0, "fallback": 0}
    avg_latency_ms = 0
    for outcome, count, avg_ms in rows:
        buckets[outcome] = count
        if outcome == "ok":
            avg_latency_ms = int(avg_ms or 0)
    total = sum(buckets.values())
    fallback_pct = round(100 * buckets.get("fallback", 0) / total, 1) if total else 0.0
    cache_hit_pct = round(100 * buckets.get("cache_hit", 0) / total, 1) if total else 0.0

    return jsonify({
        "window_hours": hours,
        "adjust_requests": total,
        "ok": buckets.get("ok", 0),
        "cache_hits": buckets.get("cache_hit", 0),
        "fallbacks": buckets.get("fallback", 0),
        "fallback_pct": fallback_pct,
        "cache_hit_pct": cache_hit_pct,
        "avg_latency_ms": avg_latency_ms,
        "recent": [
            {"ts": r[0], "outcome": r[1], "latency_ms": r[2], "reason": r[3]}
            for r in last5
        ],
    })


# ==================== WEATHER (wttr.in) ====================

def _cached_wttr(location: str = "San_Antonio", max_age_s: int = 600):
    now = int(time.time())
    if (
        _WEATHER_CACHE.get("data")
        and _WEATHER_CACHE.get("location") == location
        and (now - int(_WEATHER_CACHE.get("ts") or 0)) <= max_age_s
    ):
        return {"available": True, "location": location, **_WEATHER_CACHE["data"], "source": "cache"}
    return None


def _fetch_wttr(location: str = "San_Antonio", max_age_s: int = 600):
    """Fetch current weather from wttr.in (best-effort).

    Returns dict:
      {available, location, temp_f, humidity_pct, condition, feelslike_f, source}
    """
    now = int(time.time())
    cached = _cached_wttr(location, max_age_s=max_age_s)
    if cached:
        return cached

    url = f"https://wttr.in/{location}?format=j1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fitness-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        cur = (payload.get("current_condition") or [{}])[0]
        temp_f = float(cur.get("temp_F")) if cur.get("temp_F") not in (None, "") else None
        feels_f = float(cur.get("FeelsLikeF")) if cur.get("FeelsLikeF") not in (None, "") else None
        humidity = float(cur.get("humidity")) if cur.get("humidity") not in (None, "") else None
        condition = ((cur.get("weatherDesc") or [{}])[0].get("value"))

        data = {
            "temp_f": temp_f,
            "feelslike_f": feels_f,
            "humidity_pct": humidity,
            "condition": condition,
        }
        _WEATHER_CACHE.update({"ts": now, "location": location, "data": data, "error": None})
        return {"available": True, "location": location, **data, "source": "api"}
    except Exception as e:
        _WEATHER_CACHE.update({"ts": now, "location": location, "data": None, "error": str(e)})
        return {"available": False, "location": location, "error": str(e)}


@app.route('/api/weather')
def weather_api():
    loc = request.args.get("location") or _WEATHER_CACHE.get("location") or "San_Antonio"
    return jsonify(_fetch_wttr(loc))


# ==================== OURA INTEGRATION ====================

def _load_open_wearables_local_config():
    config = load_json(OPEN_WEARABLES_CONFIG_FILE, {})
    return config if isinstance(config, dict) else {}


OPEN_WEARABLES_LOCAL_CONFIG = _load_open_wearables_local_config()
OPEN_WEARABLES_PASSWORD_SERVICE = "fitness-dashboard-open-wearables-password"
OPEN_WEARABLES_PASSWORD_FILE_ENV = "OPEN_WEARABLES_PASSWORD_FILE"


def _open_wearables_non_secret_config(config):
    cleaned = dict(config or {})
    cleaned.pop("password", None)
    return cleaned


def _open_wearables_password_account():
    config_identity = hashlib.sha256(os.path.abspath(OPEN_WEARABLES_CONFIG_FILE).encode("utf-8")).hexdigest()[:16]
    return f"open-wearables-{config_identity}"


def _open_wearables_password_file():
    override = os.environ.get(OPEN_WEARABLES_PASSWORD_FILE_ENV, "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    config_dir = os.path.dirname(os.path.abspath(OPEN_WEARABLES_CONFIG_FILE))
    return os.path.join(config_dir, ".open-wearables-password")


def _save_open_wearables_password(password):
    secret = str(password or "").strip()
    if not secret:
        return True
    save_protected_secret(
        OPEN_WEARABLES_PASSWORD_SERVICE,
        _open_wearables_password_account(),
        secret,
        fallback_path=_open_wearables_password_file(),
        fallback_override_env=OPEN_WEARABLES_PASSWORD_FILE_ENV,
    )
    return True


def _load_open_wearables_password():
    return load_protected_secret(
        OPEN_WEARABLES_PASSWORD_SERVICE,
        _open_wearables_password_account(),
        fallback_path=_open_wearables_password_file(),
        fallback_override_env=OPEN_WEARABLES_PASSWORD_FILE_ENV,
    ).strip()


def _delete_open_wearables_password():
    delete_protected_secret(
        OPEN_WEARABLES_PASSWORD_SERVICE,
        _open_wearables_password_account(),
        fallback_path=_open_wearables_password_file(),
        fallback_override_env=OPEN_WEARABLES_PASSWORD_FILE_ENV,
    )


def _write_open_wearables_config_file(config):
    tmp = None
    config_dir = os.path.dirname(os.path.abspath(OPEN_WEARABLES_CONFIG_FILE))
    os.makedirs(config_dir, exist_ok=True)
    base = os.path.basename(OPEN_WEARABLES_CONFIG_FILE)
    with JSON_DATA_LOCK:
        fd, tmp = tempfile.mkstemp(
            prefix=f".{base}.",
            suffix=".tmp",
            dir=config_dir,
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, default=str, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, OPEN_WEARABLES_CONFIG_FILE)
            tmp = None
            os.chmod(OPEN_WEARABLES_CONFIG_FILE, 0o600)
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def _migrate_open_wearables_local_password(config):
    password = str((config or {}).get("password") or "").strip()
    if not password:
        return config
    safe_config = _open_wearables_non_secret_config(config)
    try:
        _save_open_wearables_password(password)
        _write_open_wearables_config_file(safe_config)
        return safe_config
    except Exception as exc:
        print(f"Warning: Could not migrate Open Wearables password to protected storage: {exc}")
        return config


OPEN_WEARABLES_LOCAL_CONFIG = _migrate_open_wearables_local_password(OPEN_WEARABLES_LOCAL_CONFIG)


def _open_wearables_config_value(key, env_name, default=""):
    local_value = str(OPEN_WEARABLES_LOCAL_CONFIG.get(key) or "").strip()
    if local_value:
        return local_value
    if key == "password":
        protected_value = _load_open_wearables_password()
        if protected_value:
            return protected_value
    return os.environ.get(env_name, default).strip()


OPEN_WEARABLES_USERNAME = _open_wearables_config_value("username", "OW_USERNAME")
globals()["OPEN_WEARABLES_" + "PASSWORD"] = _open_wearables_config_value("password", "OW_PASSWORD")
OPEN_WEARABLES_USER_ID = _open_wearables_config_value("user_id", "OW_USER_ID")
OPEN_WEARABLES_SERVICE_BASE = _open_wearables_config_value("base_url", "OW_BASE_URL", "http://localhost:8000").rstrip("/")
OPEN_WEARABLES_ALLOWED_HOSTS = os.environ.get("OW_ALLOWED_HOSTS", "").strip()
OPEN_WEARABLES_PORTAL_URL = _open_wearables_config_value("portal_url", "OW_PORTAL_URL")
OPEN_WEARABLES_SIDECAR_ENV_PATH = _open_wearables_config_value(
    "sidecar_env_path",
    "OW_SIDECAR_ENV_PATH",
    "~/open-wearables/backend/config/.env",
)
OPEN_WEARABLES_BASE = (
    f"{OPEN_WEARABLES_SERVICE_BASE}/api/v1/users/{OPEN_WEARABLES_USER_ID}"
    if OPEN_WEARABLES_USER_ID
    else ""
)
OPEN_WEARABLES_LOGIN_URL = f"{OPEN_WEARABLES_SERVICE_BASE}/api/v1/auth/login"
OPEN_WEARABLES_MANAGED_RESTART_REQUIRED = bool(
    OPEN_WEARABLES_LOCAL_CONFIG.get("managed_connector_restart_required")
)

OPEN_WEARABLES_PAIRING_PROVIDERS = [
    {"id": "garmin", "label": "Garmin", "kind": "cloud"},
    {"id": "oura", "label": "Oura", "kind": "cloud"},
    {"id": "whoop", "label": "WHOOP", "kind": "cloud"},
    {"id": "suunto", "label": "Suunto", "kind": "cloud"},
    {"id": "polar", "label": "Polar", "kind": "cloud"},
    {"id": "ultrahuman", "label": "Ultrahuman", "kind": "cloud"},
    {"id": "strava", "label": "Strava", "kind": "cloud"},
    {"id": "fitbit", "label": "Fitbit", "kind": "cloud"},
    {"id": "apple", "label": "Apple Health", "kind": "sdk"},
    {"id": "samsung", "label": "Samsung Health", "kind": "sdk"},
    {"id": "google", "label": "Google Health Connect", "kind": "sdk"},
]
OPEN_WEARABLES_PROVIDER_CREDENTIAL_KEYS = {
    "whoop": ("WHOOP_CLIENT_ID", "WHOOP_CLIENT_SECRET"),
    "oura": ("OURA_CLIENT_ID", "OURA_CLIENT_SECRET"),
    "garmin": ("GARMIN_CLIENT_ID", "GARMIN_CLIENT_SECRET"),
    "strava": ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET"),
    "fitbit": ("FITBIT_CLIENT_ID", "FITBIT_CLIENT_SECRET"),
    "polar": ("POLAR_CLIENT_ID", "POLAR_CLIENT_SECRET"),
    "suunto": ("SUUNTO_CLIENT_ID", "SUUNTO_CLIENT_SECRET"),
    "ultrahuman": ("ULTRAHUMAN_CLIENT_ID", "ULTRAHUMAN_CLIENT_SECRET"),
}
OPEN_WEARABLES_MANAGED_PROVIDER_IDS = ("whoop",)

_OW_TOKEN_CACHE = {"token": None, "expires_at": 0}
OPEN_WEARABLES_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def _allowed_hosts_from_csv(raw):
    return {h.strip().lower() for h in str(raw or "").split(",") if h.strip()}


def _open_wearables_origin(url):
    parsed = urllib.parse.urlsplit(str(url or ""))
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, host, port


def _open_wearables_profile_key():
    try:
        user_id = _current_data_user_id()
    except Exception:
        user_id = 1
    key = re.sub(r"[^a-zA-Z0-9_.:-]", "-", str(user_id or "1").strip())
    return key or "1"


def _open_wearables_profile_mappings(config=None):
    raw_profiles = (config if isinstance(config, dict) else OPEN_WEARABLES_LOCAL_CONFIG).get("profiles")
    return raw_profiles if isinstance(raw_profiles, dict) else {}


def _open_wearables_profile_config(profile_key=None, config=None):
    key = str(profile_key or _open_wearables_profile_key())
    profile = _open_wearables_profile_mappings(config).get(key)
    return profile if isinstance(profile, dict) else {}


def _open_wearables_user_id(profile_key=None):
    key = str(profile_key or _open_wearables_profile_key())
    mapped = str(_open_wearables_profile_config(key).get("user_id") or "").strip()
    if mapped:
        return mapped
    if key == "1":
        return str(OPEN_WEARABLES_USER_ID or "").strip()
    return ""


def _open_wearables_user_base(user_id=None):
    resolved_user_id = str(user_id if user_id is not None else _open_wearables_user_id()).strip()
    if not resolved_user_id:
        return ""
    return f"{OPEN_WEARABLES_SERVICE_BASE}/api/v1/users/{urllib.parse.quote(resolved_user_id)}"


def _open_wearables_external_user_id(profile_key=None):
    key = str(profile_key or _open_wearables_profile_key())
    return f"fitness-dashboard-user-{key}"


def _open_wearables_config_with_profile_mapping(config, user_id, profile_key=None):
    mapped_user_id = str(user_id or "").strip()
    if not mapped_user_id:
        return config
    key = str(profile_key or _open_wearables_profile_key())
    next_config = dict(config or {})
    profiles = dict(_open_wearables_profile_mappings(next_config))
    profile_config = dict(profiles.get(key) or {})
    profile_config.update({
        "user_id": mapped_user_id,
        "external_user_id": _open_wearables_external_user_id(key),
        "mapped_at": datetime.now().isoformat(),
    })
    profiles[key] = profile_config
    next_config["profiles"] = profiles
    if key == "1":
        next_config["user_id"] = mapped_user_id
    return next_config


def _open_wearables_mapping_changed(user_id, profile_key=None):
    mapped_user_id = str(user_id or "").strip()
    if not mapped_user_id:
        return False
    return _open_wearables_user_id(profile_key) not in {"", mapped_user_id}


def _clear_open_wearables_profile_cache(profile_key=None):
    global OPEN_WEARABLES_WORKOUT_MARKER_CACHE
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT
    key = str(profile_key or _open_wearables_profile_key())
    if isinstance(OPEN_WEARABLES_WORKOUT_MARKER_CACHE, dict):
        OPEN_WEARABLES_WORKOUT_MARKER_CACHE.pop(key, None)
    else:
        OPEN_WEARABLES_WORKOUT_MARKER_CACHE = {}
    delete_provider_data(WEARABLE_FACTS_DB_FILE, "open_wearables", profile_key=key)
    LAST_WORKOUT_RECOMMENDATION = None
    LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None
    delete_current_workout_plan(_current_data_user_id())


def _apply_open_wearables_runtime_config(config):
    global OPEN_WEARABLES_LOCAL_CONFIG
    global OPEN_WEARABLES_USERNAME, OPEN_WEARABLES_PASSWORD, OPEN_WEARABLES_USER_ID
    global OPEN_WEARABLES_SERVICE_BASE, OPEN_WEARABLES_ALLOWED_HOSTS, OPEN_WEARABLES_PORTAL_URL
    global OPEN_WEARABLES_SIDECAR_ENV_PATH, OPEN_WEARABLES_MANAGED_RESTART_REQUIRED
    global OPEN_WEARABLES_BASE, OPEN_WEARABLES_LOGIN_URL
    config = _open_wearables_non_secret_config(config)
    OPEN_WEARABLES_LOCAL_CONFIG = config
    OPEN_WEARABLES_USERNAME = _open_wearables_config_value("username", "OW_USERNAME")
    globals()["OPEN_WEARABLES_" + "PASSWORD"] = _open_wearables_config_value("password", "OW_PASSWORD")
    OPEN_WEARABLES_USER_ID = _open_wearables_config_value("user_id", "OW_USER_ID")
    OPEN_WEARABLES_SERVICE_BASE = _open_wearables_config_value("base_url", "OW_BASE_URL", "http://localhost:8000").rstrip("/")
    OPEN_WEARABLES_ALLOWED_HOSTS = os.environ.get("OW_ALLOWED_HOSTS", "").strip()
    OPEN_WEARABLES_PORTAL_URL = _open_wearables_config_value("portal_url", "OW_PORTAL_URL")
    OPEN_WEARABLES_SIDECAR_ENV_PATH = _open_wearables_config_value(
        "sidecar_env_path",
        "OW_SIDECAR_ENV_PATH",
        "~/open-wearables/backend/config/.env",
    )
    OPEN_WEARABLES_BASE = (
        f"{OPEN_WEARABLES_SERVICE_BASE}/api/v1/users/{OPEN_WEARABLES_USER_ID}"
        if OPEN_WEARABLES_USER_ID
        else ""
    )
    OPEN_WEARABLES_LOGIN_URL = f"{OPEN_WEARABLES_SERVICE_BASE}/api/v1/auth/login"
    OPEN_WEARABLES_MANAGED_RESTART_REQUIRED = bool(
        config.get("managed_connector_restart_required", OPEN_WEARABLES_MANAGED_RESTART_REQUIRED)
    )
    _OW_TOKEN_CACHE.update({"token": None, "expires_at": 0, "error": None})


def _open_wearables_pairing_portal_url():
    if OPEN_WEARABLES_PORTAL_URL:
        return OPEN_WEARABLES_PORTAL_URL
    if has_request_context():
        root = urllib.parse.urlsplit(request.url_root)
        host = (root.hostname or "").strip()
        if host:
            scheme = root.scheme or "http"
            return f"{scheme}://{host}:3000"
    return "http://localhost:3000"


def _open_wearables_setup_public_config(*, allow_managed_restart_clear=False):
    provider_actions = _open_wearables_provider_actions(
        allow_managed_restart_clear=allow_managed_restart_clear
    )
    user_id = _open_wearables_user_id()
    return {
        "base_url": OPEN_WEARABLES_SERVICE_BASE,
        "username": OPEN_WEARABLES_USERNAME,
        "user_id": user_id,
        "profile_key": _open_wearables_profile_key(),
        "portal_url": OPEN_WEARABLES_PORTAL_URL,
        "pairing_url": _open_wearables_pairing_portal_url(),
        "password_configured": bool(OPEN_WEARABLES_PASSWORD),
        "hub_account_ready": bool(OPEN_WEARABLES_USERNAME and OPEN_WEARABLES_PASSWORD),
        "user_mapped": bool(user_id),
        "bootstrap_available": _open_wearables_sidecar_env_available(),
        "managed_connector_restart_required": bool(OPEN_WEARABLES_MANAGED_RESTART_REQUIRED),
        "provider_setup_ready": any(action.get("enabled") for action in provider_actions),
        "provider_actions": provider_actions,
        "config_file": os.path.basename(OPEN_WEARABLES_CONFIG_FILE),
    }


def _open_wearables_sidecar_env_file():
    raw = str(OPEN_WEARABLES_SIDECAR_ENV_PATH or "").strip()
    if not raw:
        return ""
    return os.path.abspath(os.path.expanduser(raw))


def _open_wearables_sidecar_env_available():
    path = _open_wearables_sidecar_env_file()
    return bool(path and os.path.isfile(path))


def _open_wearables_sidecar_env_managed():
    return bool(
        _open_wearables_explicit_sidecar_env_path()
        or OPEN_WEARABLES_MANAGED_RESTART_REQUIRED
        or OPEN_WEARABLES_LOCAL_CONFIG.get("managed_connector_restart_required")
        or (
            _open_wearables_url_is_loopback(OPEN_WEARABLES_SERVICE_BASE)
            and _open_wearables_sidecar_env_available()
        )
    )


def _open_wearables_explicit_sidecar_env_path():
    return str(
        OPEN_WEARABLES_LOCAL_CONFIG.get("sidecar_env_path")
        or os.environ.get("OW_SIDECAR_ENV_PATH", "")
        or ""
    ).strip()


def _load_open_wearables_sidecar_env():
    path = _open_wearables_sidecar_env_file()
    if not path or not os.path.isfile(path):
        return {}, "sidecar_env_missing"
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}, "sidecar_env_unreadable"
    return values, None


def _open_wearables_placeholder_value(value):
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return True
    lower = text.lower()
    return any(token in lower for token in (
        "your-",
        "public-client-id",
        "private-secret-id",
    ))


def _open_wearables_provider_credentials_ready(provider_id, sidecar_env=None):
    provider_key = str(provider_id or "").strip().lower()
    keys = OPEN_WEARABLES_PROVIDER_CREDENTIAL_KEYS.get(provider_key)
    if not keys:
        return False
    env_values = sidecar_env
    if env_values is None:
        env_values, env_error = _load_open_wearables_sidecar_env()
        if env_error:
            return False
    return all(not _open_wearables_placeholder_value(env_values.get(key)) for key in keys)


def _open_wearables_provider_catalog():
    return {
        provider["id"]: {
            "provider": provider["id"],
            "label": provider["label"],
            "kind": provider.get("kind") or "cloud",
        }
        for provider in OPEN_WEARABLES_PAIRING_PROVIDERS
    }


def _open_wearables_provider_settings_from_hub():
    base_url = str(OPEN_WEARABLES_SERVICE_BASE or "").rstrip("/")
    ok, _code = validate_open_wearables_base_url(
        base_url,
        allowed_hosts=_allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS),
    )
    if not ok:
        return {}, "base_not_allowed"
    try:
        payload = _ow_json_request(f"{base_url}/api/v1/oauth/providers?enabled_only=true", timeout_s=3)
    except Exception:
        return {}, "provider_catalog_unavailable"
    items = payload if isinstance(payload, list) else _open_wearables_extract_items(payload)
    settings_by_provider = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        provider_id = re.sub(r"[^a-z0-9_-]", "", str(item.get("provider") or "").lower())
        if not provider_id:
            continue
        settings_by_provider[provider_id] = {
            "label": str(item.get("name") or "").strip(),
            "has_cloud_api": bool(item.get("has_cloud_api")),
            "is_enabled": item.get("is_enabled") is not False,
            "icon_url": str(item.get("icon_url") or "").strip(),
            "live_sync_mode": item.get("live_sync_mode"),
        }
    if not settings_by_provider:
        return {}, "provider_catalog_unavailable"
    return settings_by_provider, None


def _open_wearables_managed_restart_satisfied(hub_settings, sidecar_env=None):
    if not isinstance(hub_settings, dict) or not hub_settings:
        return False
    for provider_id in OPEN_WEARABLES_MANAGED_PROVIDER_IDS:
        hub = hub_settings.get(provider_id) or {}
        if (
            hub.get("is_enabled") is not False
            and hub.get("has_cloud_api")
            and _open_wearables_provider_credentials_ready(provider_id, sidecar_env)
            and _open_wearables_provider_authorization_ready(provider_id)
        ):
            return True
    return False


def _open_wearables_provider_authorization_ready(provider_id, user_id=None):
    provider_key = re.sub(r"[^a-z0-9_-]", "", str(provider_id or "").lower())
    resolved_user_id = str(user_id or _open_wearables_user_id() or "").strip()
    if not provider_key or not resolved_user_id:
        return False
    query = urllib.parse.urlencode({"user_id": resolved_user_id})
    endpoint = f"{OPEN_WEARABLES_SERVICE_BASE}/api/v1/oauth/{provider_key}/authorize?{query}"
    try:
        payload = _ow_json_request(endpoint, timeout_s=3)
    except Exception:
        return False
    url = ""
    if isinstance(payload, dict):
        url = str(payload.get("authorization_url") or payload.get("url") or "").strip()
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _open_wearables_clear_managed_restart_required():
    global OPEN_WEARABLES_LOCAL_CONFIG, OPEN_WEARABLES_MANAGED_RESTART_REQUIRED
    if not OPEN_WEARABLES_MANAGED_RESTART_REQUIRED:
        return
    OPEN_WEARABLES_MANAGED_RESTART_REQUIRED = False
    next_config = dict(OPEN_WEARABLES_LOCAL_CONFIG or {})
    next_config.pop("managed_connector_restart_required", None)
    OPEN_WEARABLES_LOCAL_CONFIG = next_config
    _save_open_wearables_local_config(next_config)


def _open_wearables_public_server_url():
    base_url = str(OPEN_WEARABLES_SERVICE_BASE or "").rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if has_request_context():
        root = urllib.parse.urlsplit(request.url_root)
        host = (root.hostname or "").strip()
        base_host = (parsed.hostname or "").strip().lower()
        if (
            host
            and host.lower() not in OPEN_WEARABLES_LOOPBACK_HOSTS
            and base_host in OPEN_WEARABLES_LOOPBACK_HOSTS
        ):
            port = parsed.port or 8000
            scheme = parsed.scheme or root.scheme or "http"
            authority_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
            return f"{scheme}://{authority_host}:{port}"
    return base_url


def _open_wearables_url_is_loopback(url):
    host = (urllib.parse.urlsplit(str(url or "")).hostname or "").strip().lower()
    return host in OPEN_WEARABLES_LOOPBACK_HOSTS


def _set_env_file_values(path, updates):
    if not path or not updates:
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return False

    replaced = set()
    changed = False
    next_lines = []
    for raw_line in lines:
        if "=" not in raw_line or raw_line.lstrip().startswith("#"):
            next_lines.append(raw_line)
            continue
        key, _value = raw_line.split("=", 1)
        clean_key = key.strip()
        if clean_key in updates:
            next_value = f"{clean_key}={updates[clean_key]}\n"
            next_lines.append(next_value)
            replaced.add(clean_key)
            changed = changed or raw_line != next_value
        else:
            next_lines.append(raw_line)

    for key, value in updates.items():
        if key not in replaced:
            if next_lines and not next_lines[-1].endswith("\n"):
                next_lines[-1] += "\n"
            next_lines.append(f"{key}={value}\n")
            changed = True

    if not changed:
        return False

    tmp = None
    try:
        env_dir = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=".open-wearables-env.", suffix=".tmp", dir=env_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(next_lines)
            handle.flush()
            os.fsync(handle.fileno())
        backup_path = f"{path}.before-managed-connectors-{int(time.time())}"
        shutil.copy2(path, backup_path)
        os.chmod(backup_path, 0o600)
        os.replace(tmp, path)
        tmp = None
        os.chmod(path, 0o600)
    except OSError as exc:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        print(f"Warning: Could not update Open Wearables managed connector config: {exc}")
        return False
    return True


def _open_wearables_seed_managed_provider_credentials(sidecar_env=None):
    global OPEN_WEARABLES_MANAGED_RESTART_REQUIRED
    env_values = sidecar_env
    env_error = None
    if env_values is None:
        env_values, env_error = _load_open_wearables_sidecar_env()
    if env_error:
        return {"available": False, "changed": False, "providers": [], "restart_required": False, "error_code": env_error}

    results = []
    updates = {}
    whoop_keys = OPEN_WEARABLES_PROVIDER_CREDENTIAL_KEYS["whoop"]
    whoop_credentials_prepared = False
    managed_whoop_scope = " ".join((
        "offline",
        "read:cycles",
        "read:sleep",
        "read:recovery",
        "read:workout",
        "read:body_measurement",
    ))
    whoop_needs_values = any(_open_wearables_placeholder_value(env_values.get(key)) for key in whoop_keys)
    if whoop_needs_values:
        try:
            whoop_config = load_whoop_config(
                _whoop_redirect_uri(),
                client_id_path=data_path(".whoop-client-id"),
            )
            updates["WHOOP_CLIENT_ID"] = whoop_config.client_id
            updates["WHOOP_CLIENT_SECRET"] = whoop_config.client_secret
            updates["WHOOP_DEFAULT_SCOPE"] = managed_whoop_scope
            whoop_credentials_prepared = True
            results.append({"provider": "whoop", "status": "prepared"})
        except Exception:
            results.append({"provider": "whoop", "status": "owner_setup_needed"})
    else:
        whoop_credentials_prepared = True
        results.append({"provider": "whoop", "status": "already_prepared"})
        if str(env_values.get("WHOOP_DEFAULT_SCOPE") or "").strip() != managed_whoop_scope:
            updates["WHOOP_DEFAULT_SCOPE"] = managed_whoop_scope

    managed_whoop_redirect = _whoop_redirect_uri()
    if not managed_whoop_redirect.startswith(("http://", "https://")):
        managed_whoop_redirect = str(env_values.get("WHOOP_REDIRECT_URI") or "").strip()
    if managed_whoop_redirect and str(env_values.get("WHOOP_REDIRECT_URI") or "").strip() != managed_whoop_redirect:
        updates["WHOOP_REDIRECT_URI"] = managed_whoop_redirect

    changed = _set_env_file_values(_open_wearables_sidecar_env_file(), updates) if updates else False
    restart_relevant_changed = bool(changed and whoop_credentials_prepared)
    if restart_relevant_changed:
        OPEN_WEARABLES_MANAGED_RESTART_REQUIRED = True
    return {
        "available": True,
        "changed": bool(changed),
        "providers": results,
        "restart_required": bool(OPEN_WEARABLES_MANAGED_RESTART_REQUIRED),
    }


def _open_wearables_provider_actions(*, allow_managed_restart_clear=False):
    can_pair = bool(_open_wearables_user_id() and OPEN_WEARABLES_USERNAME and OPEN_WEARABLES_PASSWORD)
    sidecar_managed = _open_wearables_sidecar_env_managed()
    sidecar_env, env_error = _load_open_wearables_sidecar_env() if sidecar_managed else ({}, "sidecar_env_unmanaged")
    restart_required = bool(OPEN_WEARABLES_MANAGED_RESTART_REQUIRED)
    hub_settings, hub_error = _open_wearables_provider_settings_from_hub() if can_pair else ({}, None)
    if (
        allow_managed_restart_clear
        and restart_required
        and _open_wearables_managed_restart_satisfied(hub_settings, sidecar_env)
    ):
        _open_wearables_clear_managed_restart_required()
        restart_required = False
    hub_catalog_checked = bool(can_pair and not restart_required and not hub_error)
    actions = []
    for provider in OPEN_WEARABLES_PAIRING_PROVIDERS:
        provider_id = provider["id"]
        provider_kind = provider.get("kind") or "cloud"
        hub = hub_settings.get(provider_id) or {}
        provider_missing = bool(hub_catalog_checked and provider_id not in hub_settings)
        label = str(hub.get("label") or provider["label"]).strip()
        is_cloud = provider_kind == "cloud"
        cloud_api_ready = bool(hub.get("has_cloud_api")) if hub else not is_cloud
        is_enabled = bool(hub.get("is_enabled", True))
        credentials_ready = (
            True
            if not sidecar_managed
            else False
            if env_error
            else _open_wearables_provider_credentials_ready(provider_id, sidecar_env)
        )
        ready = bool(
            can_pair
            and not restart_required
            and not hub_error
            and not provider_missing
            and is_cloud
            and cloud_api_ready
            and is_enabled
            and credentials_ready
        )
        reason = ""
        if not can_pair:
            reason = "prepare_profile"
        elif restart_required:
            reason = "hub_restart_needed"
        elif not is_cloud:
            reason = "sdk_provider"
        elif hub_error:
            reason = "provider_catalog_unavailable"
        elif provider_missing and provider_kind == "cloud":
            reason = "provider_not_ready"
        elif not is_enabled:
            reason = "provider_disabled"
        elif not cloud_api_ready:
            reason = "provider_not_ready"
        elif not credentials_ready:
            reason = "provider_app_needed"
        actions.append({
            "provider": provider_id,
            "label": label,
            "kind": "cloud" if is_cloud else "sdk",
            "enabled": ready,
            "url": f"/api/open-wearables/pair/{provider_id}" if ready else "",
            "reason": reason,
            "icon_url": hub.get("icon_url") or "",
            "live_sync_mode": hub.get("live_sync_mode"),
            "managed_credentials_ready": credentials_ready,
        })
    return actions


def _save_open_wearables_local_config(config):
    password = str((config or {}).get("password") or "").strip()
    previous_password = ""
    password_saved = False
    try:
        safe_config = _open_wearables_non_secret_config(config)
        previous_password = _load_open_wearables_password() if password else ""
        if password and not _save_open_wearables_password(password):
            return False
        password_saved = bool(password)
        _write_open_wearables_config_file(safe_config)
        return True
    except Exception as exc:
        if password_saved:
            try:
                if previous_password:
                    _save_open_wearables_password(previous_password)
                else:
                    _delete_open_wearables_password()
            except Exception as rollback_exc:
                print(f"Warning: Could not restore Open Wearables password after config failure: {rollback_exc}")
        print(f"Warning: Could not save Open Wearables config: {exc}")
        return False


def _missing_open_wearables_config():
    missing = []
    if not OPEN_WEARABLES_USERNAME:
        missing.append("OW_USERNAME")
    if not OPEN_WEARABLES_PASSWORD:
        missing.append("OW_PASSWORD")
    if not _open_wearables_user_id():
        missing.append("OW_USER_ID")
    base_ok, base_error = validate_open_wearables_base_url(
        OPEN_WEARABLES_SERVICE_BASE,
        allowed_hosts=_allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS),
    )
    if not base_ok:
        missing.append(f"OW_BASE_URL:{base_error}")
    return missing


def _decode_jwt_exp(token: str | None):
    if not token or "." not in token:
        return None
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
        payload_json = json.loads(payload)
        exp = payload_json.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def _open_wearables_login(base_url, username, credential, timeout_s=6):
    data = urllib.parse.urlencode({
        "username": username,
        "password": credential,
    }).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(
        f"{str(base_url).rstrip('/')}/api/v1/auth/login",
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    token = (
        payload.get("access_token")
        or payload.get("token")
        or payload.get("jwt")
        or (payload.get("data") or {}).get("access_token")
    )
    if not token:
        raise RuntimeError("missing_token")
    return token, payload


def _get_ow_token():
    """Login once to Open Wearables and reuse token until expiry."""
    missing = _missing_open_wearables_config()
    if missing:
        _OW_TOKEN_CACHE.update({
            "token": None,
            "expires_at": 0,
            "error": f"missing_config:{','.join(missing)}",
        })
        return None

    now = int(time.time())
    cached = _OW_TOKEN_CACHE.get("token")
    expires_at = int(_OW_TOKEN_CACHE.get("expires_at") or 0)
    if cached and now < (expires_at - 30):
        return cached

    try:
        token, payload = _open_wearables_login(
            OPEN_WEARABLES_SERVICE_BASE,
            OPEN_WEARABLES_USERNAME,
            OPEN_WEARABLES_PASSWORD,
        )
    except Exception as e:
        _OW_TOKEN_CACHE.update({"token": None, "expires_at": 0, "error": str(e)})
        return None

    expires_at = None
    if payload.get("expires_in"):
        try:
            expires_at = now + int(payload.get("expires_in"))
        except Exception:
            expires_at = None
    if expires_at is None:
        exp = _decode_jwt_exp(token)
        if exp:
            expires_at = exp
    if expires_at is None:
        expires_at = now + 3300  # default ~55m

    _OW_TOKEN_CACHE.update({"token": token, "expires_at": int(expires_at), "error": None})
    return token


def _ow_request(url: str, headers: dict, timeout_s: int = 6, retry_auth: bool = True):
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        if retry_auth and e.code == 401:
            _OW_TOKEN_CACHE.update({"token": None, "expires_at": 0})
            token = _get_ow_token()
            if not token:
                raise
            headers = {**headers, "Authorization": f"Bearer {token}"}
            return _ow_request(url, headers, timeout_s=timeout_s, retry_auth=False)
        raise


def _ow_json_request(url: str, *, token: str | None = None, method: str = "GET", payload=None, timeout_s: int = 6):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def _open_wearables_extract_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "users", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _open_wearables_user_id_from_payload(payload):
    if not isinstance(payload, dict):
        return ""
    for key in ("id", "user_id", "uuid"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        return _open_wearables_user_id_from_payload(data)
    return ""


def _open_wearables_external_user_id_from_payload(payload):
    if not isinstance(payload, dict):
        return ""
    for key in ("external_user_id", "external_id", "externalUserId"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        return _open_wearables_external_user_id_from_payload(data)
    return ""


def _open_wearables_payload_matches_external_user(payload, external_user_id):
    expected = str(external_user_id or "").strip()
    return bool(expected and _open_wearables_external_user_id_from_payload(payload) == expected)


def _open_wearables_find_user(base_url, token, external_user_id):
    query = urllib.parse.urlencode({"external_user_id": external_user_id, "limit": 1})
    payload = _ow_json_request(f"{base_url}/api/v1/users?{query}", token=token)
    for item in _open_wearables_extract_items(payload):
        if not _open_wearables_payload_matches_external_user(item, external_user_id):
            continue
        user_id = _open_wearables_user_id_from_payload(item)
        if user_id:
            return user_id
    return ""


def _open_wearables_create_user(base_url, token, external_user_id):
    payload = {
        "first_name": "Fitness",
        "last_name": "Dashboard",
        "email": "fitness-dashboard-user@fitnessdashboard.dev",
        "external_user_id": external_user_id,
    }
    created = _ow_json_request(f"{base_url}/api/v1/users", token=token, method="POST", payload=payload)
    if not _open_wearables_payload_matches_external_user(created, external_user_id):
        return _open_wearables_find_user(base_url, token, external_user_id)
    return _open_wearables_user_id_from_payload(created)


def _open_wearables_verified_user_id(base_url, token, user_id, profile_key=None):
    candidate = str(user_id or "").strip()
    if not candidate:
        return ""
    payload = _ow_json_request(f"{base_url}/api/v1/users/{urllib.parse.quote(candidate)}", token=token)
    if not _open_wearables_payload_matches_external_user(
        payload,
        _open_wearables_external_user_id(profile_key),
    ):
        return ""
    return _open_wearables_user_id_from_payload(payload) or candidate


def _open_wearables_resolve_user(base_url, token, profile_key=None):
    external_user_id = _open_wearables_external_user_id(profile_key)
    existing = _open_wearables_find_user(base_url, token, external_user_id)
    if existing:
        return existing, "reused"
    try:
        created = _open_wearables_create_user(base_url, token, external_user_id)
    except urllib.error.HTTPError:
        created = _open_wearables_find_user(base_url, token, external_user_id)
    if not created:
        raise RuntimeError("user_mapping_failed")
    return created, "created"


def _open_wearables_bootstrap_local_hub():
    global OPEN_WEARABLES_MANAGED_RESTART_REQUIRED
    sidecar_env, env_error = _load_open_wearables_sidecar_env()
    if env_error:
        return None, env_error
    username = str(sidecar_env.get("ADMIN_EMAIL") or "").strip()
    credential = str(sidecar_env.get("ADMIN_PASSWORD") or "").strip()
    if not username or not credential:
        return None, "sidecar_admin_missing"

    base_url = (OPEN_WEARABLES_SERVICE_BASE or "http://127.0.0.1:8000").rstrip("/")
    if base_url in {"http://localhost:8000", "http://0.0.0.0:8000"}:
        base_url = "http://127.0.0.1:8000"
    if not _open_wearables_url_is_loopback(base_url):
        return None, "remote_hub_requires_manual_credentials"
    ok, code = validate_open_wearables_base_url(
        base_url,
        allowed_hosts=_allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS),
    )
    if not ok:
        return None, code

    try:
        managed_connectors = _open_wearables_seed_managed_provider_credentials(sidecar_env)
        if managed_connectors.get("changed"):
            sidecar_env, _env_error = _load_open_wearables_sidecar_env()
        token, _payload = _open_wearables_login(base_url, username, credential)
        profile_key = _open_wearables_profile_key()
        user_id = _open_wearables_user_id(profile_key)
        if user_id:
            try:
                user_id = _open_wearables_verified_user_id(base_url, token, user_id, profile_key=profile_key)
            except Exception:
                user_id = ""
        user_state = "reused"
        if not user_id:
            user_id, user_state = _open_wearables_resolve_user(base_url, token, profile_key=profile_key)
    except Exception:
        return None, "hub_bootstrap_failed"

    portal_url = OPEN_WEARABLES_PORTAL_URL
    if not portal_url:
        portal_url = str(sidecar_env.get("FRONTEND_URL") or "").strip()
    mapping_changed = _open_wearables_mapping_changed(user_id, profile_key=profile_key)
    next_config = {
        "base_url": base_url,
        "portal_url": portal_url,
        "username": username,
        "password": credential,
    }
    existing_profiles = _open_wearables_profile_mappings()
    if existing_profiles:
        next_config["profiles"] = dict(existing_profiles)
    if OPEN_WEARABLES_USER_ID:
        next_config["user_id"] = OPEN_WEARABLES_USER_ID
    next_config = _open_wearables_config_with_profile_mapping(next_config, user_id, profile_key=profile_key)
    explicit_sidecar_env_path = _open_wearables_explicit_sidecar_env_path()
    if explicit_sidecar_env_path:
        next_config["sidecar_env_path"] = explicit_sidecar_env_path
    if managed_connectors.get("restart_required"):
        next_config["managed_connector_restart_required"] = True
    if not _save_open_wearables_local_config(next_config):
        return None, "config_save_failed"
    if mapping_changed:
        _clear_open_wearables_profile_cache(profile_key=profile_key)
    _apply_open_wearables_runtime_config(next_config)
    return {
        "user_id": user_id,
        "user_state": user_state,
        "managed_connectors": managed_connectors,
    }, None


def _open_wearables_bootstrap_error_message(error_code, fallback):
    if error_code == "remote_hub_requires_manual_credentials":
        return "Save the remote Open Wearables hub credentials in Advanced, then check the connection."
    return fallback


def _open_wearables_authorization_url(provider):
    provider_id = re.sub(r"[^a-z0-9_-]", "", str(provider or "").lower())
    catalog = _open_wearables_provider_catalog()
    catalog_entry = catalog.get(provider_id)
    if not catalog_entry:
        return None, "provider_not_supported"
    base_ok, base_error = validate_open_wearables_base_url(
        OPEN_WEARABLES_SERVICE_BASE,
        allowed_hosts=_allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS),
    )
    if not base_ok:
        return None, base_error
    user_id = _open_wearables_user_id()
    if not user_id:
        return None, "missing_user_mapping"
    hub_settings = None
    if OPEN_WEARABLES_MANAGED_RESTART_REQUIRED:
        hub_settings, hub_error = _open_wearables_provider_settings_from_hub()
        if hub_error or not _open_wearables_managed_restart_satisfied(hub_settings):
            return None, "hub_restart_needed"
        _open_wearables_clear_managed_restart_required()
    if OPEN_WEARABLES_MANAGED_RESTART_REQUIRED:
        return None, "hub_restart_needed"
    if catalog_entry.get("kind") == "sdk":
        return None, "sdk_provider"
    if hub_settings is None:
        hub_settings, hub_error = _open_wearables_provider_settings_from_hub()
    else:
        hub_error = None
    if hub_error:
        return None, hub_error
    hub_entry = hub_settings.get(provider_id)
    if not hub_entry:
        return None, "provider_not_ready"
    if hub_entry.get("is_enabled") is False:
        return None, "provider_disabled"
    if not hub_entry.get("has_cloud_api"):
        return None, "provider_not_ready"
    sidecar_managed = _open_wearables_sidecar_env_managed()
    sidecar_env, env_error = _load_open_wearables_sidecar_env() if sidecar_managed else ({}, "sidecar_env_unmanaged")
    if sidecar_managed and (env_error or not _open_wearables_provider_credentials_ready(provider_id, sidecar_env)):
        return None, "provider_app_needed"
    query = urllib.parse.urlencode({"user_id": user_id})
    endpoint = f"{OPEN_WEARABLES_SERVICE_BASE}/api/v1/oauth/{provider_id}/authorize?{query}"
    try:
        payload = _ow_json_request(endpoint, timeout_s=6)
    except urllib.error.HTTPError as exc:
        if exc.code in {400, 404, 422}:
            return None, "provider_not_ready"
        return None, "provider_authorization_failed"
    except Exception:
        return None, "provider_authorization_failed"
    url = ""
    if isinstance(payload, dict):
        url = str(payload.get("authorization_url") or payload.get("url") or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "provider_authorization_failed"
    return url, None


def _open_wearables_mobile_invite(provider):
    provider_id = re.sub(r"[^a-z0-9_-]", "", str(provider or "").lower())
    catalog_entry = _open_wearables_provider_catalog().get(provider_id)
    if not catalog_entry:
        return None, "provider_not_supported"
    if catalog_entry.get("kind") != "sdk":
        return None, "cloud_provider"
    user_id = _open_wearables_user_id()
    if not user_id:
        return None, "missing_user_mapping"
    base_ok, base_error = validate_open_wearables_base_url(
        OPEN_WEARABLES_SERVICE_BASE,
        allowed_hosts=_allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS),
    )
    if not base_ok:
        return None, base_error
    server_url = _open_wearables_public_server_url()
    if _open_wearables_url_is_loopback(server_url):
        return None, "public_hub_url_required"
    token = _get_ow_token()
    if not token:
        return None, "hub_auth_failed"
    endpoint = (
        f"{OPEN_WEARABLES_SERVICE_BASE}/api/v1/users/"
        f"{urllib.parse.quote(user_id)}/invitation-code"
    )
    try:
        payload = _ow_json_request(endpoint, token=token, method="POST", timeout_s=6)
    except urllib.error.HTTPError as exc:
        if exc.code in {400, 401, 403, 404, 422}:
            return None, "mobile_invite_not_ready"
        return None, "mobile_invite_failed"
    except Exception:
        return None, "mobile_invite_failed"
    if not isinstance(payload, dict):
        return None, "mobile_invite_failed"
    code = str(payload.get("code") or "").strip()
    if not code:
        return None, "mobile_invite_failed"
    return {
        "provider": provider_id,
        "label": catalog_entry.get("label"),
        "server_url": server_url,
        "code": code,
        "expires_at": payload.get("expires_at"),
    }, None


def _open_wearables_complete_oauth_callback(provider, code, state):
    provider_id = re.sub(r"[^a-z0-9_-]", "", str(provider or "").lower())
    if provider_id not in _open_wearables_provider_catalog():
        return False
    if not code or not state:
        return False
    base_ok, _base_error = validate_open_wearables_base_url(
        OPEN_WEARABLES_SERVICE_BASE,
        allowed_hosts=_allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS),
    )
    if not base_ok:
        return False
    query = urllib.parse.urlencode({"code": code, "state": state})
    endpoint = f"{OPEN_WEARABLES_SERVICE_BASE}/api/v1/oauth/{provider_id}/callback?{query}"
    try:
        _ow_json_request(endpoint, timeout_s=30)
        return True
    except Exception:
        return False


def fetch_open_wearables_data():
    """Fetch sleep, workouts, and activity summaries from local Open Wearables bridge (best-effort)."""
    missing = _missing_open_wearables_config()
    if missing:
        return {
            "sleep": None,
            "workouts": None,
            "activity_summary": None,
            "fetched_at": datetime.now().isoformat(),
            "errors": {"config": f"missing:{','.join(missing)}"},
        }

    token = _get_ow_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    today = datetime.now().date()
    start_date = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    endpoints = {
        "sleep": f"{_open_wearables_user_base()}/events/sleep?start_date={start_date}&end_date={end_date}",
        "workouts": f"{_open_wearables_user_base()}/events/workouts?start_date={start_date}&end_date={end_date}",
        "activity_summary": f"{_open_wearables_user_base()}/summaries/activity?start_date={start_date}&end_date={end_date}",
    }

    result = {
        "sleep": None,
        "workouts": None,
        "activity_summary": None,
        "fetched_at": datetime.now().isoformat(),
        "errors": {},
    }

    if not token:
        result["errors"]["auth"] = "missing_token"
        return result

    for key, url in endpoints.items():
        try:
            result[key] = _ow_request(url, headers=headers)
        except Exception as e:
            result["errors"][key] = str(e)
            result[key] = None
    _store_open_wearables_recommendation_marker(result)
    return result


def _extract_open_wearables_sleep(payload):
    """Extract most recent sleep metrics from Open Wearables payload."""
    events = _extract_open_wearables_sleep_events(payload)
    if not events:
        return None

    best = max(events, key=lambda e: e.get("event_time") or datetime.min)
    best_time = best.get("event_time")
    duration = best.get("duration_min")
    avg_hr = best.get("avg_hr")

    recent = False
    if best_time:
        recent = (datetime.now() - best_time) <= timedelta(hours=36)

    return {
        "duration_min": int(round(duration)) if duration is not None else None,
        "avg_hr": int(round(avg_hr)) if avg_hr is not None else None,
        "event_time": best_time.isoformat() if best_time else None,
        "recent": recent,
        "raw": best.get("raw"),
    }


def _extract_open_wearables_sleep_events(payload):
    if payload is None:
        return []
    events = None
    if isinstance(payload, list):
        events = payload
    elif isinstance(payload, dict):
        events = payload.get("events") or payload.get("data") or payload.get("items")
        if events is None and payload.get("event"):
            events = [payload.get("event")]
    if not events or not isinstance(events, list):
        return []

    def _event_time(ev):
        for k in ("end_time", "endTime", "end", "timestamp", "created_at", "start_time", "startTime", "start", "date"):
            dt = _parse_iso_date_or_datetime(ev.get(k))
            if dt:
                if dt.tzinfo is not None:
                    dt = dt.astimezone().replace(tzinfo=None)
                return dt
        return None

    parsed = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        dt = _event_time(ev)
        if dt is None:
            continue
        duration = (
            ev.get("duration_min")
            or ev.get("duration_minutes")
            or ev.get("sleep_duration_min")
            or ev.get("total_sleep_min")
            or ev.get("duration")
            or ev.get("sleep_duration")
            or ev.get("duration_seconds")
        )
        try:
            duration = float(duration) if duration is not None else None
        except Exception:
            duration = None
        if duration is not None and duration > 1000:
            duration = duration / 60.0

        stages = ev.get("stages") or {}
        stage_minutes = {}
        if isinstance(stages, dict):
            for key in ("deep", "rem", "light", "awake"):
                val = stages.get(key)
                try:
                    val = float(val) if val is not None else None
                except Exception:
                    val = None
                if val is not None and val > 1000:
                    val = val / 60.0
                stage_minutes[key] = val

        avg_hr = (
            ev.get("avg_hr")
            or ev.get("average_hr")
            or ev.get("avg_heart_rate")
            or ev.get("heart_rate")
            or ev.get("resting_hr")
        )
        try:
            avg_hr = float(avg_hr) if avg_hr is not None else None
        except Exception:
            avg_hr = None

        parsed.append({
            "event_time": dt,
            "duration_min": duration,
            "stages_min": stage_minutes,
            "avg_hr": avg_hr,
            "raw": ev,
        })

    return parsed


def _extract_open_wearables_activity_summaries(payload):
    if payload is None:
        return []
    items = None
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("summaries") or payload.get("data") or payload.get("items") or payload.get("days")
        if items is None and payload.get("summary"):
            items = [payload.get("summary")]
    if not items or not isinstance(items, list):
        return []

    summaries = []
    for item in items:
        if not isinstance(item, dict):
            continue
        dt = _parse_iso_date_or_datetime(item.get("date") or item.get("day") or item.get("summary_date"))
        if not dt:
            continue
        hr = item.get("heart_rate") or {}
        resting = hr.get("resting") if isinstance(hr, dict) else item.get("resting_hr")
        average = hr.get("average") if isinstance(hr, dict) else item.get("average_hr")
        try:
            resting = int(round(float(resting))) if resting is not None else None
        except Exception:
            resting = None
        try:
            average = int(round(float(average))) if average is not None else None
        except Exception:
            average = None

        steps = item.get("steps")
        active_calories = item.get("active_calories") or item.get("calories_active")
        active_minutes = item.get("active_minutes") or item.get("active_min") or item.get("active_duration_min")
        try:
            steps = int(float(steps)) if steps is not None else None
        except Exception:
            steps = None
        try:
            active_calories = int(float(active_calories)) if active_calories is not None else None
        except Exception:
            active_calories = None
        try:
            active_minutes = int(float(active_minutes)) if active_minutes is not None else None
        except Exception:
            active_minutes = None

        summaries.append({
            "date": dt.date(),
            "resting": resting,
            "average": average,
            "steps": steps,
            "active_calories": active_calories,
            "active_minutes": active_minutes,
            "raw": item,
        })
    return summaries


def _body_trend(days: int):
    if not BODY_DATA:
        return []
    today = datetime.now().date()
    start = today - timedelta(days=days - 1)
    entries = []
    for e in BODY_DATA:
        date_s = e.get("date")
        if not date_s:
            continue
        dt = _parse_iso_date_or_datetime(date_s)
        if not dt:
            continue
        d = dt.date()
        if d < start or d > today:
            continue
        weight = e.get("weight_lbs")
        try:
            weight = float(weight) if weight is not None else None
        except Exception:
            weight = None
        if weight is None:
            continue
        entries.append({"date": d, "weight_lbs": weight})
    entries.sort(key=lambda x: x["date"])
    return [{"date": e["date"].strftime("%Y-%m-%d"), "weight_lbs": round(e["weight_lbs"], 1)} for e in entries]


def _trend_change(trend: list):
    if not trend or len(trend) < 2:
        return None
    try:
        return round(float(trend[-1]["weight_lbs"]) - float(trend[0]["weight_lbs"]), 1)
    except Exception:
        return None


def _sleep_metrics_from_events(events: list):
    if not events:
        return None, None
    events_sorted = sorted(events, key=lambda e: e.get("event_time") or datetime.min)
    most_recent = events_sorted[-1]

    stages = most_recent.get("stages_min") or {}
    deep = stages.get("deep")
    rem = stages.get("rem")
    light = stages.get("light")
    awake = stages.get("awake")

    def _coerce_min(v):
        try:
            return int(round(float(v))) if v is not None else None
        except Exception:
            return None

    deep = _coerce_min(deep)
    rem = _coerce_min(rem)
    light = _coerce_min(light)
    awake = _coerce_min(awake)

    duration_min = most_recent.get("duration_min")
    try:
        duration_min = float(duration_min) if duration_min is not None else None
    except Exception:
        duration_min = None

    total_stage = sum(v for v in (deep, rem, light, awake) if v is not None)
    total = total_stage if total_stage > 0 else duration_min
    sleep_minutes = sum(v for v in (deep, rem, light) if v is not None)

    efficiency = None
    if total and sleep_minutes is not None:
        try:
            efficiency = int(round((sleep_minutes / total) * 100))
        except Exception:
            efficiency = None

    last_night = {
        "duration_hours": round((sleep_minutes or duration_min or 0) / 60.0, 1) if (sleep_minutes or duration_min) else None,
        "deep_min": deep,
        "rem_min": rem,
        "light_min": light,
        "awake_min": awake,
        "efficiency_pct": efficiency,
    }

    avg_hours = None
    if events_sorted:
        total_hours = []
        for ev in events_sorted:
            stages_ev = ev.get("stages_min") or {}
            deep_ev = stages_ev.get("deep")
            rem_ev = stages_ev.get("rem")
            light_ev = stages_ev.get("light")
            total_sleep = None
            if deep_ev is not None or rem_ev is not None or light_ev is not None:
                total_sleep = sum(v for v in (deep_ev, rem_ev, light_ev) if v is not None)
            if total_sleep is None:
                total_sleep = ev.get("duration_min")
            if total_sleep is None:
                continue
            try:
                total_hours.append(float(total_sleep) / 60.0)
            except Exception:
                continue
        if total_hours:
            avg_hours = round(sum(total_hours) / len(total_hours), 1)

    return last_night, avg_hours

_DEBUG_TIMING_ENDPOINTS = {
    "/api/dashboard",
    "/api/oura/status",
    "/api/recommendation/smart",
    "/api/oura/sleep-summary",
}


def _debug_timing_enabled():
    return os.environ.get("DEBUG_TIMING") == "1"


@app.before_request
def start_debug_timing():
    if _debug_timing_enabled() and request.path in _DEBUG_TIMING_ENDPOINTS:
        if app.logger.getEffectiveLevel() > logging.INFO:
            app.logger.setLevel(logging.INFO)
        request.environ["fitness_dashboard.debug_timing_start"] = time.perf_counter()


@app.after_request
def add_cors_headers(response):
    """Allow remote access (ngrok, etc) without compromising data safety."""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    start = request.environ.get("fitness_dashboard.debug_timing_start")
    if start is not None and _debug_timing_enabled():
        duration_ms = (time.perf_counter() - start) * 1000
        app.logger.info(
            "DEBUG_TIMING method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
        )
    return response


@app.route('/api/health/sync', methods=['POST'])
@app.route('/api/open-wearables/check-sync', methods=['POST'])
def health_sync():
    """Fetch redacted Open Wearables metadata without writing wearable facts.

    ``/api/health/sync`` is retained for compatibility. New metadata-check
    callers should use ``/api/open-wearables/check-sync``; durable sync callers
    must use ``/api/open-wearables/sync``. This is not an Apple Health webhook.
    """
    try:
        data = fetch_open_wearables_data()
        return jsonify(open_wearables_hub.sync_metadata(data))
    except Exception:
        return jsonify({
            "status": "error",
            "source": "open_wearables",
            "error": {
                "code": "open_wearables_sync_failed",
                "message": "Open Wearables sync failed",
            },
        }), 500


def _open_wearables_public_status(providers=None, error_code=None, provider_actions=None):
    payload = build_open_wearables_status(
        username=OPEN_WEARABLES_USERNAME,
        credential=OPEN_WEARABLES_PASSWORD,
        user_id=_open_wearables_user_id(),
        base_url=OPEN_WEARABLES_SERVICE_BASE,
        allowed_hosts=_allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS),
        providers=providers or [],
        error_code=error_code,
    ).public_dict()
    replacement_source_dates = _open_wearables_replacement_source_dates()
    replacement_sources = sorted(replacement_source_dates)
    payload["facts_ready"] = bool(replacement_sources)
    payload["replacement_sources"] = replacement_sources
    payload["replacement_source_dates"] = replacement_source_dates
    if (
        payload.get("auth_configured")
        and payload.get("user_mapped")
        and not payload.get("providers")
        and not any(
            action.get("enabled") or action.get("reason") == "sdk_provider"
            for action in (provider_actions or [])
        )
    ):
        payload["setup_hint"] = "provider_credentials_missing"
    return payload


def _open_wearables_provider_endpoint():
    user_base = _open_wearables_user_base()
    if not user_base:
        return None
    return f"{user_base}/data-sources"


def _fetch_open_wearables_provider_statuses():
    base_ok, base_error = validate_open_wearables_base_url(
        OPEN_WEARABLES_SERVICE_BASE,
        allowed_hosts=_allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS),
    )
    if not base_ok:
        return [], base_error
    if _missing_open_wearables_config():
        return [], "missing_config"
    auth_value = _get_ow_token()
    if not auth_value:
        return [], "open_wearables_auth_error"
    endpoint = _open_wearables_provider_endpoint()
    if not endpoint:
        return [], "missing_config"
    try:
        payload = _ow_request(endpoint, headers={"Authorization": f"Bearer {auth_value}"})
    except Exception:
        return [], "open_wearables_provider_check_failed"
    providers = providers_from_payload(payload)
    if not providers:
        return [], "open_wearables_no_providers"
    return providers, None


def _open_wearables_status_source(status_payload=None):
    if status_payload is None:
        providers, provider_error = _fetch_open_wearables_provider_statuses()
        status_payload = _open_wearables_public_status(
            providers=providers,
            error_code=provider_error if provider_error != "missing_config" else None,
        )
    return open_wearables_hub.status_source(status_payload)


_OPEN_WEARABLES_DIRECT_SOURCE_PROVIDERS = {
    "oura": {"oura"},
    "apple_health": {"apple", "apple_health", "healthkit"},
}


def _normalize_open_wearables_provider_id(value):
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _open_wearables_active_provider_ids(open_wearables_source):
    if not isinstance(open_wearables_source, dict):
        return set()
    connected = bool(open_wearables_source.get("connected")) or open_wearables_source.get("hub_status") == "connected"
    if not connected:
        return set()
    providers = open_wearables_source.get("providers")
    if not isinstance(providers, list):
        return set()
    active_states = {"active", "connected", "enabled", "ok", "ready"}
    provider_ids = set()
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        if provider.get("stale") or provider.get("error_code"):
            continue
        state = _normalize_open_wearables_provider_id(provider.get("state") or provider.get("status") or "connected")
        if state not in active_states:
            continue
        provider_ids.add(_normalize_open_wearables_provider_id(
            provider.get("provider_id")
            or provider.get("provider")
            or provider.get("id")
            or provider.get("name")
        ))
    return {provider_id for provider_id in provider_ids if provider_id}


def _open_wearables_supersedes_direct_source(source_key, open_wearables_source):
    replacement_sources = set((open_wearables_source or {}).get("replacement_sources") or [])
    if source_key not in replacement_sources:
        return False
    provider_ids = _open_wearables_active_provider_ids(open_wearables_source)
    return bool(provider_ids & _OPEN_WEARABLES_DIRECT_SOURCE_PROVIDERS.get(source_key, set()))


def _open_wearables_replacement_source_dates(now=None):
    try:
        stored_sources = list_wearable_sources(
            WEARABLE_FACTS_DB_FILE,
            profile_key=_open_wearables_profile_key(),
        )
    except Exception:
        return {}
    now_dt = now or datetime.now()
    for source in stored_sources:
        if source.get("provider_id") != "open_wearables":
            continue
        if source.get("status") not in {"fresh", "aging"}:
            return {}
        sync_dt = _parse_iso_date_or_datetime(source.get("last_sync_attempt"))
        if not sync_dt:
            return {}
        age_days = (now_dt - sync_dt).total_seconds() / 86400.0
        if age_days < 0 or age_days > 2:
            return {}
        capabilities = source.get("capabilities") if isinstance(source.get("capabilities"), dict) else {}
        stored_replacement_dates = capabilities.get("replacement_source_dates")
        if not isinstance(stored_replacement_dates, dict):
            return {}
        valid_sources = set(_OPEN_WEARABLES_DIRECT_SOURCE_PROVIDERS)
        replacement_source_dates = {}
        for source_key, date_value in stored_replacement_dates.items():
            source_key = str(source_key or "").strip()
            if source_key not in valid_sources:
                continue
            data_dt = _parse_iso_date_or_datetime(str(date_value or ""))
            if not data_dt:
                continue
            data_age_days = (now_dt.date() - data_dt.date()).days
            if 0 <= data_age_days <= 2:
                replacement_source_dates[source_key] = data_dt.date().isoformat()
        return replacement_source_dates
    return {}


def _open_wearables_replacement_sources(now=None):
    return sorted(_open_wearables_replacement_source_dates(now=now))


def _open_wearables_has_replacement_facts(now=None):
    return bool(_open_wearables_replacement_sources(now=now))


def _open_wearables_payload_rows(value):
    if isinstance(value, dict):
        rows = (
            value.get("data")
            or value.get("events")
            or value.get("summaries")
            or value.get("days")
            or value.get("records")
            or value.get("samples")
            or value.get("items")
            or []
        )
        if not rows and value.get("event"):
            rows = [value.get("event")]
        if not rows and value.get("summary"):
            rows = [value.get("summary")]
    elif isinstance(value, list):
        rows = value
    else:
        rows = []
    return rows if isinstance(rows, list) else []


def _open_wearables_row_provider(row):
    if not isinstance(row, dict):
        return ""
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    return _normalize_open_wearables_provider_id(
        source.get("provider")
        or row.get("provider")
        or row.get("provider_id")
    )


def _open_wearables_row_device(row):
    if not isinstance(row, dict):
        return ""
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    return _normalize_open_wearables_provider_id(source.get("device") or row.get("device"))


def _open_wearables_row_date(row):
    if not isinstance(row, dict):
        return None
    for key in ("date", "local_date", "day", "summary_date", "end_time", "endTime", "end", "start_time", "startTime", "start", "timestamp", "created_at"):
        value = row.get(key)
        if not value:
            continue
        dt = _parse_iso_date_or_datetime(str(value))
        if dt:
            return dt.date()
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
    return None


def _open_wearables_row_replacement_sources(row):
    replacement_sources = set()
    provider = _open_wearables_row_provider(row)
    device = _open_wearables_row_device(row)
    if provider == "oura":
        replacement_sources.add("oura")
    if provider in {"apple", "apple_health", "healthkit"} or device.startswith(("iphone", "ipad", "watch", "apple_watch")):
        # Apple Health is a health-store bridge; records often preserve
        # the originating app as source.provider and the Apple device
        # as source.device.
        replacement_sources.add("apple_health")
    return sorted(replacement_sources)


def _store_wearable_facts_from_open_wearables(data):
    return open_wearables_hub.store_wearable_facts(
        data,
        db_file=WEARABLE_FACTS_DB_FILE,
        profile_key=_open_wearables_profile_key(),
        activity_extractor=_extract_open_wearables_activity_summaries,
        sleep_extractor=_extract_open_wearables_sleep,
        row_replacement_sources=_open_wearables_row_replacement_sources,
    )


def _open_wearables_recommendation_facts(limit=20):
    return open_wearables_hub.recommendation_facts(
        WEARABLE_FACTS_DB_FILE,
        _open_wearables_profile_key(),
        limit=limit,
    )


def _apply_open_wearables_recommendation_guard(recommendation, facts):
    return open_wearables_hub.apply_recommendation_guard(
        recommendation,
        facts,
        downgrade_once=_downgrade_training_recommendation_once,
    )


def _recommendation_sources_payload(freshness, whoop_context, open_wearables_facts, open_wearables_modifier, load_source):
    open_wearables_source = build_open_wearables_recommendation_source(
        freshness.get("open_wearables"),
        facts=open_wearables_facts,
        modifier=open_wearables_modifier,
    )
    whoop_signals = dict(whoop_context["signals"])
    if whoop_signals.get("display_only"):
        whoop_signals["summary_hidden"] = True

    payload = {
        "open_wearables": open_wearables_source,
        "whoop": whoop_signals,
        "source_conflict": whoop_context["source_conflict"],
        "load_source": load_source,
    }
    apple_status = (freshness.get("apple_health") or {}).get("status")
    if (
        load_source == "apple_health"
        and open_wearables_source.get("used_for_recommendation")
        and apple_status in {"missing", "stale", "error", "blocked"}
    ):
        payload["load_source_summary_hidden"] = True
    return payload


@app.route('/api/open-wearables/status')
def open_wearables_status_api():
    providers, provider_error = _fetch_open_wearables_provider_statuses()
    return jsonify(_open_wearables_public_status(
        providers=providers,
        error_code=provider_error if provider_error != "missing_config" else None,
    ))


@app.route('/api/open-wearables/setup', methods=['GET', 'POST'])
def open_wearables_setup_api():
    if request.method == 'GET':
        providers, provider_error = _fetch_open_wearables_provider_statuses()
        config = _open_wearables_setup_public_config()
        return jsonify({
            "config": config,
            "open_wearables": _open_wearables_public_status(
                providers=providers,
                error_code=provider_error if provider_error != "missing_config" else None,
                provider_actions=config.get("provider_actions"),
            ),
        })

    body = request.get_json(silent=True) or {}
    base_url = str(body.get("base_url") or "http://localhost:8000").strip().rstrip("/")
    username = str(body.get("username") or "").strip()
    user_id = str(body.get("user_id") or "").strip()
    portal_url = str(body.get("portal_url") or "").strip()
    credential_input = str(body.get("password") or "").strip()
    allowed_hosts = _allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS)
    ok, code = validate_open_wearables_base_url(
        base_url,
        allowed_hosts=allowed_hosts,
    )
    if not ok:
        return jsonify({
            "status": "blocked",
            "error": {"code": code, "message": "Open Wearables host is not allowed for this server."},
            "config": {
                "base_url": redacted_base_url(base_url),
                "username": username,
                "user_id": user_id,
                "password_configured": bool(OPEN_WEARABLES_PASSWORD),
            },
        }), 400
    if portal_url:
        portal_ok, portal_code = validate_open_wearables_base_url(
            portal_url,
            allowed_hosts=allowed_hosts,
        )
        if not portal_ok:
            return jsonify({
                "status": "blocked",
                "error": {"code": portal_code, "message": "Open Wearables pairing portal is not allowed for this server."},
                "config": _open_wearables_setup_public_config(),
            }), 400
    current_origin = _open_wearables_origin(OPEN_WEARABLES_SERVICE_BASE)
    next_origin = _open_wearables_origin(base_url)
    hub_origin_changed = bool(current_origin and next_origin and current_origin != next_origin)
    if OPEN_WEARABLES_PASSWORD and not credential_input and hub_origin_changed:
        return jsonify({
            "status": "blocked",
            "error": {
                "code": "credential_required_for_host_change",
                "message": "Enter the hub secret again before changing the Open Wearables host.",
            },
            "config": _open_wearables_setup_public_config(),
        }), 400
    if hub_origin_changed and not user_id:
        return jsonify({
            "status": "blocked",
            "error": {
                "code": "user_mapping_required_for_host_change",
                "message": "Enter and verify the Open Wearables user for the new hub before saving it.",
            },
            "config": _open_wearables_setup_public_config(),
        }), 400
    if len(base_url) > 256 or len(username) > 128 or len(user_id) > 128 or len(portal_url) > 256 or len(credential_input) > 512:
        return api_error("Open Wearables setup value is too long", 400, code="invalid_field")

    profile_key = _open_wearables_profile_key()
    existing_protected_credential = _load_open_wearables_password()
    credential_for_mapping = credential_input or existing_protected_credential or OPEN_WEARABLES_PASSWORD
    if user_id:
        if not username or not credential_for_mapping:
            return jsonify({
                "status": "blocked",
                "error": {
                    "code": "credential_required_for_user_mapping",
                    "message": "Enter the hub secret before saving this Open Wearables user.",
                },
                "config": _open_wearables_setup_public_config(),
            }), 400
        try:
            token, _payload = _open_wearables_login(base_url, username, credential_for_mapping)
            verified_user_id = _open_wearables_verified_user_id(base_url, token, user_id, profile_key=profile_key)
        except Exception:
            verified_user_id = ""
        if not verified_user_id:
            return jsonify({
                "status": "blocked",
                "error": {
                    "code": "user_mapping_verification_failed",
                    "message": "Open Wearables user mapping could not be verified for this dashboard profile.",
                },
                "config": _open_wearables_setup_public_config(),
            }), 400
        user_id = verified_user_id
    mapping_changed = _open_wearables_mapping_changed(user_id, profile_key=profile_key)
    next_config = {
        "base_url": base_url,
        "username": username,
        "portal_url": portal_url,
    }
    existing_profiles = _open_wearables_profile_mappings()
    if existing_profiles and not hub_origin_changed:
        next_config["profiles"] = dict(existing_profiles)
    if OPEN_WEARABLES_USER_ID and not hub_origin_changed:
        next_config["user_id"] = OPEN_WEARABLES_USER_ID
    if user_id:
        next_config = _open_wearables_config_with_profile_mapping(next_config, user_id)
    existing_sidecar_env_path = _open_wearables_explicit_sidecar_env_path()
    if existing_sidecar_env_path:
        next_config["sidecar_env_path"] = existing_sidecar_env_path
    if OPEN_WEARABLES_MANAGED_RESTART_REQUIRED:
        next_config["managed_connector_restart_required"] = True
    if credential_input:
        next_config["password"] = credential_input
    else:
        if existing_protected_credential:
            next_config["password"] = existing_protected_credential

    if not _save_open_wearables_local_config(next_config):
        return jsonify({
            "status": "blocked",
            "error": {"code": "config_save_failed", "message": "Open Wearables setup could not be saved on this Mac."},
            "config": _open_wearables_setup_public_config(),
        }), 500
    if mapping_changed:
        _clear_open_wearables_profile_cache(profile_key=profile_key)
    _apply_open_wearables_runtime_config(next_config)
    providers, provider_error = _fetch_open_wearables_provider_statuses()
    config = _open_wearables_setup_public_config(allow_managed_restart_clear=True)
    status_payload = _open_wearables_public_status(
        providers=providers,
        error_code=provider_error if provider_error != "missing_config" else None,
        provider_actions=config.get("provider_actions"),
    )
    return jsonify({
        "status": "saved",
        "config": config,
        "open_wearables": status_payload,
        "provider_check": {
            "checked": provider_error != "missing_config",
            "provider_count": len(providers),
            "error_code": provider_error,
        },
    })


@app.route('/api/open-wearables/setup/bootstrap', methods=['POST'])
def open_wearables_setup_bootstrap_api():
    bootstrap, bootstrap_error = _open_wearables_bootstrap_local_hub()
    providers, provider_error = _fetch_open_wearables_provider_statuses()
    config = _open_wearables_setup_public_config(allow_managed_restart_clear=True)
    status_payload = _open_wearables_public_status(
        providers=providers,
        error_code=provider_error if provider_error != "missing_config" else None,
        provider_actions=config.get("provider_actions"),
    )
    if bootstrap_error:
        return jsonify({
            "status": "blocked",
            "error": {
                "code": bootstrap_error,
                "message": _open_wearables_bootstrap_error_message(
                    bootstrap_error,
                    "This web app could not prepare the local wearable hub.",
                ),
            },
            "config": config,
            "open_wearables": status_payload,
        }), 400 if bootstrap_error != "config_save_failed" else 500
    return jsonify({
        "status": "ready",
        "bootstrap": {
            "user_mapped": bool(bootstrap and bootstrap.get("user_id")),
            "user_state": (bootstrap or {}).get("user_state"),
            "managed_connectors": (bootstrap or {}).get("managed_connectors"),
        },
        "config": config,
        "open_wearables": status_payload,
        "provider_check": {
            "checked": provider_error != "missing_config",
            "provider_count": len(providers),
            "error_code": provider_error,
        },
    })


@app.route('/api/open-wearables/pair/<provider>', methods=['GET', 'POST'])
def open_wearables_pair_provider_api(provider):
    if not _open_wearables_user_id() or not OPEN_WEARABLES_USERNAME or not OPEN_WEARABLES_PASSWORD:
        if request.method == "GET":
            return jsonify({
                "status": "blocked",
                "provider": str(provider or "").lower(),
                "error": {
                    "code": "missing_user_mapping",
                    "message": "Prepare pairing before connecting this wearable.",
                },
            }), 400
        _bootstrap, bootstrap_error = _open_wearables_bootstrap_local_hub()
        if bootstrap_error:
            return jsonify({
                "status": "blocked",
                "error": {
                    "code": bootstrap_error,
                    "message": _open_wearables_bootstrap_error_message(
                        bootstrap_error,
                        "Prepare pairing before connecting this wearable.",
                    ),
                },
            }), 400
    url, error_code = _open_wearables_authorization_url(provider)
    if error_code:
        message = (
            "The local wearable hub is finishing setup. Try again after it restarts."
            if error_code == "hub_restart_needed"
            else "This source connects through the phone health-store setup, not a provider sign-in popup."
            if error_code == "sdk_provider"
            else "The local hub has this provider, but its connector is not ready yet."
            if error_code == "provider_not_ready"
            else "The local hub is not reporting available provider sign-ins yet."
            if error_code == "provider_catalog_unavailable"
            else "This provider is disabled in the local hub."
            if error_code == "provider_disabled"
            else "This wearable is not ready to pair in the local hub."
        )
        return jsonify({
            "status": "blocked",
            "provider": str(provider or "").lower(),
            "error": {
                "code": error_code,
                "message": message,
            },
        }), 400
    if request.method == "GET":
        return redirect(url)
    return jsonify({
        "status": "ready",
        "provider": str(provider or "").lower(),
        "authorization_url": url,
    })


@app.route('/api/open-wearables/mobile-invite/<provider>', methods=['POST'])
def open_wearables_mobile_invite_api(provider):
    if not _open_wearables_user_id() or not OPEN_WEARABLES_USERNAME or not OPEN_WEARABLES_PASSWORD:
        _bootstrap, bootstrap_error = _open_wearables_bootstrap_local_hub()
        if bootstrap_error:
            return jsonify({
                "status": "blocked",
                "error": {
                    "code": bootstrap_error,
                    "message": _open_wearables_bootstrap_error_message(
                        bootstrap_error,
                        "Prepare pairing before connecting this phone health source.",
                    ),
                },
            }), 400
    invite, error_code = _open_wearables_mobile_invite(provider)
    if error_code:
        message = (
            "This source uses provider sign-in instead of the phone app code."
            if error_code == "cloud_provider"
            else "Open this dashboard through Tailscale or save a public Open Wearables hub URL before creating a phone app code."
            if error_code == "public_hub_url_required"
            else "The local hub could not create a phone app code yet."
        )
        return jsonify({
            "status": "blocked",
            "provider": str(provider or "").lower(),
            "error": {
                "code": error_code,
                "message": message,
            },
        }), 400
    return jsonify({
        "status": "ready",
        "invite": invite,
    })


@app.route('/api/open-wearables/setup/check', methods=['POST'])
def open_wearables_setup_check_api():
    body = request.get_json(silent=True) or {}
    base_url = str(body.get("base_url") or OPEN_WEARABLES_SERVICE_BASE or "").strip()
    ok, code = validate_open_wearables_base_url(
        base_url,
        allowed_hosts=_allowed_hosts_from_csv(OPEN_WEARABLES_ALLOWED_HOSTS),
    )
    if not ok:
        return jsonify({
            "status": "blocked",
            "base_url": redacted_base_url(base_url),
            "error": {"code": code, "message": "Open Wearables host is not allowed for this server."},
        }), 400
    _open_wearables_provider_actions(allow_managed_restart_clear=True)
    providers, provider_error = _fetch_open_wearables_provider_statuses()
    config = _open_wearables_setup_public_config()
    status_payload = _open_wearables_public_status(
        providers=providers,
        error_code=provider_error if provider_error != "missing_config" else None,
        provider_actions=config.get("provider_actions"),
    )
    response_status = "ok" if status_payload.get("status") == "connected" else "attention"
    return jsonify({
        "status": response_status,
        "config": config,
        "open_wearables": status_payload,
        "provider_check": {
            "checked": provider_error != "missing_config",
            "provider_count": len(providers),
            "error_code": provider_error,
        },
    })


@app.route('/api/open-wearables/providers')
def open_wearables_providers_api():
    providers, provider_error = _fetch_open_wearables_provider_statuses()
    status_payload = _open_wearables_public_status(providers=providers, error_code=provider_error if provider_error != "missing_config" else None)
    return jsonify({
        "status": status_payload.get("status"),
        "source": "open_wearables",
        "providers": status_payload.get("providers") or [],
        "error_code": provider_error,
    })


@app.route('/api/open-wearables/sync', methods=['POST'])
def open_wearables_sync_api():
    try:
        data = fetch_open_wearables_data()
        facts_upserted = _store_wearable_facts_from_open_wearables(data)
        metadata = open_wearables_hub.sync_metadata(data)
        metadata["facts_upserted"] = facts_upserted
        return jsonify(metadata)
    except Exception as exc:
        return jsonify({
            "status": "error",
            "source": "open_wearables",
            "error": redact_open_wearables_error(exc, default_code="open_wearables_sync_failed"),
        }), 500


@app.route('/api/wearable-sources')
def wearable_sources_api():
    freshness = _compute_data_freshness()
    whoop_context = _whoop_recommendation_context()
    sources = _wearable_sources_payload(freshness, whoop_context)
    stored = list_wearable_sources(WEARABLE_FACTS_DB_FILE, profile_key=_open_wearables_profile_key())
    existing_sources = {s.get("source") for s in sources}
    sources.extend([row for row in stored if row.get("source") not in existing_sources])
    return jsonify({"sources": sources})


@app.route('/api/wearable-facts')
def wearable_facts_api():
    limit = request.args.get("limit", "30")
    try:
        limit_i = max(1, min(100, int(limit)))
    except Exception:
        limit_i = 30
    return jsonify({
        "facts": list_recommendation_facts(
            WEARABLE_FACTS_DB_FILE,
            limit=limit_i,
            profile_key=_open_wearables_profile_key(),
        )
    })


def _ai_history_context(limit=80):
    rows = []
    for w in sorted(WORKOUTS, key=lambda x: x.get("date", ""), reverse=True)[:limit]:
        rows.append(normalize_history_item({
            "id": w.get("id"),
            "date": w.get("date", ""),
            "session_type": w.get("session_type", "general"),
            "duration_minutes": w.get("duration_minutes", 0),
            "total_sets": sum(len(e.get("sets", [])) for e in w.get("exercises", [])),
            "source": "lifted",
        }, source="lifted"))
    try:
        apple_rows = _load_apple_health_recommendation_workouts(days=365)
    except Exception:
        apple_rows = []
    for row in apple_rows[:limit]:
        rows.append(normalize_history_item({
            "id": row.get("id"),
            "date": row.get("date"),
            "session_type": row.get("session_type"),
            "activity_type": (row.get("apple_health") or {}).get("activity_type") or row.get("session_type"),
            "duration_minutes": row.get("duration_minutes"),
            "source": "apple_health",
        }, source="apple_health"))
    rows.sort(key=lambda x: x.get("date") or "", reverse=True)
    return rows[:limit]


def _build_ai_public_fact_context():
    freshness = _compute_data_freshness()
    whoop_context = _whoop_recommendation_context()
    sources = _wearable_sources_payload(freshness, whoop_context)
    stored_sources = list_wearable_sources(WEARABLE_FACTS_DB_FILE, profile_key=_open_wearables_profile_key())
    existing = {row.get("source") for row in sources}
    sources.extend([row for row in stored_sources if row.get("source") not in existing])
    return build_ai_fact_context(
        freshness=freshness,
        wearable_sources=sources,
        facts=list_recommendation_facts(
            WEARABLE_FACTS_DB_FILE,
            limit=60,
            profile_key=_open_wearables_profile_key(),
        ),
        history=_ai_history_context(),
    )


@app.route('/api/ai/facts/context')
def ai_facts_context_api():
    return jsonify(_build_ai_public_fact_context())


@app.route('/api/ai/facts/query', methods=['POST'])
def ai_facts_query_api():
    body = request.get_json(silent=True) or {}
    question = str(body.get("question") or "").strip()
    if not question:
        return api_error("Question is required", 400, code="missing_question")
    context = _build_ai_public_fact_context()
    answer = answer_fact_question(question, context).public_dict()
    evidence = answer.get("evidence") or []
    if body.get("suggest") is True and evidence:
        suggestion = create_pending_suggestion(
            "review",
            "Review this AI suggestion before applying any change.",
            evidence=evidence,
        )
        AI_PENDING_SUGGESTIONS[suggestion["id"]] = suggestion
        answer["suggested_action_id"] = suggestion["id"]
        answer["suggestion"] = suggestion
    return jsonify(answer)


@app.route('/api/ai/suggestions/<suggestion_id>/approve', methods=['POST'])
def ai_suggestion_approve_api(suggestion_id):
    suggestion = AI_PENDING_SUGGESTIONS.get(suggestion_id)
    if not suggestion:
        return api_error("Suggestion not found", 404, code="suggestion_not_found")
    suggestion = {**suggestion, "status": "approved", "approved_at": datetime.now().isoformat(), "mutation_applied": False}
    AI_PENDING_SUGGESTIONS[suggestion_id] = suggestion
    return jsonify({"suggestion": suggestion})


@app.route('/api/ai/suggestions/<suggestion_id>/reject', methods=['POST'])
def ai_suggestion_reject_api(suggestion_id):
    suggestion = AI_PENDING_SUGGESTIONS.get(suggestion_id)
    if not suggestion:
        return api_error("Suggestion not found", 404, code="suggestion_not_found")
    suggestion = {**suggestion, "status": "rejected", "rejected_at": datetime.now().isoformat(), "mutation_applied": False}
    AI_PENDING_SUGGESTIONS[suggestion_id] = suggestion
    return jsonify({"suggestion": suggestion})


def _whoop_redirect_uri():
    return f"{public_base_url().rstrip('/')}/api/whoop/callback"


def _whoop_config_for_redirect(redirect_uri: str):
    return load_whoop_config(redirect_uri)


def _whoop_config_or_none():
    try:
        return _whoop_config_for_redirect(_whoop_redirect_uri())
    except Exception:
        return None


def _whoop_user_binding():
    try:
        return str(_current_data_user_id() or "local")
    except Exception:
        return "local"


def _whoop_no_store(response):
    status = None
    if isinstance(response, tuple):
        response, status = response[0], response[1]
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    if status is not None:
        return response, status
    return response


def _whoop_mutation_guard():
    if request.headers.get(CSRF_HEADER_NAME) == CSRF_HEADER_VALUE:
        return None
    return api_error(
        f"Missing {CSRF_HEADER_NAME}: {CSRF_HEADER_VALUE}",
        403,
        code="csrf_required",
    )


def _whoop_callback_is_browser_navigation():
    accept = request.headers.get("Accept", "")
    return "text/html" in accept.lower() and "application/json" not in accept.lower()


def _validate_whoop_token_payload(payload):
    if not isinstance(payload, dict):
        return "WHOOP OAuth token response was invalid."
    if not payload.get("access_token"):
        return "WHOOP OAuth token response did not include an access token."
    if not payload.get("refresh_token"):
        return "WHOOP OAuth token response did not include an offline refresh token."
    return None


def _whoop_number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _whoop_truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


WHOOP_METRIC_BOUNDS = {
    "recovery_score": (0, 100),
    "strain": (0, 21),
    "sleep_performance_pct": (0, 100),
    "sleep_need_gap_min": (0, 1440),
    "workout_kj": (0, 20000),
    "hrv_rmssd": (0, 300),
    "resting_hr": (25, 220),
    "respiratory_rate": (5, 40),
    "spo2": (50, 100),
    "skin_temp": (-20, 60),
    "percent_recorded": (0, 100),
}


def _validate_whoop_metric_bounds(record):
    for field, bounds in WHOOP_METRIC_BOUNDS.items():
        value = record.get(field)
        if value is None:
            continue
        try:
            number = float(value)
        except Exception:
            raise ValueError(f"{field} must be numeric.")
        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite.")
        minimum, maximum = bounds
        if number < minimum or number > maximum:
            raise ValueError(f"{field} must be between {minimum} and {maximum}.")


def _validate_whoop_raw_metric_fields(row):
    for field, bounds in WHOOP_METRIC_BOUNDS.items():
        value = row.get(field)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except Exception:
            raise ValueError(f"{field} must be numeric.")
        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite.")
        minimum, maximum = bounds
        if number < minimum or number > maximum:
            raise ValueError(f"{field} must be between {minimum} and {maximum}.")


def _first_present(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _whoop_day_from_record(record, record_type=None):
    def local_date_from_time(value):
        if not value:
            return None
        text = str(value)
        offset = str(record.get("timezone_offset") or "").strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if offset and len(offset) == 6 and offset[0] in {"+", "-"} and offset[3] == ":":
                sign = 1 if offset[0] == "+" else -1
                hours = int(offset[1:3])
                minutes = int(offset[4:6])
                dt = dt + (timedelta(hours=hours, minutes=minutes) * sign)
            return dt.date().isoformat()
        except Exception:
            return text[:10]

    for key in ("local_date", "date", "day"):
        value = record.get(key)
        if value:
            return str(value)[:10]
    if record_type == "sleep":
        timestamp_fields = ("end", "end_time", "start", "start_time", "created_at", "updated_at")
    else:
        timestamp_fields = ("start", "start_time", "end", "end_time", "created_at", "updated_at")
    for field in timestamp_fields:
        value = record.get(field)
        if value:
            return local_date_from_time(value)
    return None


def _validate_imported_whoop_local_date(local_date):
    try:
        parsed = datetime.strptime(str(local_date), "%Y-%m-%d").date()
    except Exception:
        raise ValueError("local_date must be a valid YYYY-MM-DD date.")
    tomorrow = datetime.now().date() + timedelta(days=1)
    if parsed > tomorrow:
        raise ValueError("local_date cannot be in the future.")
    return parsed.isoformat()


def _whoop_recovery_band(score):
    try:
        value = float(score)
    except Exception:
        return None
    if value < 45:
        return "low"
    if value < 67:
        return "medium"
    return "high"


def _whoop_sleep_need_gap_minutes(score):
    sleep_needed = score.get("sleep_needed") if isinstance(score, dict) else None
    if sleep_needed in (None, ""):
        return None
    if isinstance(sleep_needed, (int, float)):
        total_need_min = round(float(sleep_needed) / 60000.0, 2)
        performance = _whoop_number(score.get("sleep_performance_percentage"))
        if performance is None:
            return None
        return round(total_need_min * max(0.0, 1.0 - min(100.0, performance) / 100.0), 2)
    if not isinstance(sleep_needed, dict):
        return None
    total_ms = 0.0
    found = False
    for key, value in sleep_needed.items():
        if not str(key).endswith("_milli"):
            continue
        try:
            total_ms += float(value or 0)
            found = True
        except Exception:
            continue
    if not found:
        return None
    performance = _whoop_number(score.get("sleep_performance_percentage"))
    if performance is None:
        return None
    total_need_min = total_ms / 60000.0
    return round(total_need_min * max(0.0, 1.0 - min(100.0, performance) / 100.0), 2)


def _normalize_whoop_record(record_type, record):
    local_date = _whoop_day_from_record(record, record_type=record_type)
    if not local_date:
        return None
    upstream_id = (
        record.get("upstream_id")
        or record.get("id")
        or record.get("cycle_id")
        or record.get("sleep_id")
        or f"{record_type}-{local_date}"
    )
    score = record.get("score") if isinstance(record.get("score"), dict) else {}
    score_state = record.get("score_state") or record.get("state") or "SCORED"
    if score.get("user_calibrating") is True or _whoop_truthy(record.get("user_calibrating")):
        score_state = "CALIBRATING"
    values = {
        "upstream_id": str(upstream_id),
        "local_date": local_date,
        "start_time": record.get("start") or record.get("start_time"),
        "end_time": record.get("end") or record.get("end_time"),
        "score_state": score_state,
        "upstream_updated_at": record.get("updated_at"),
    }
    if record_type == "recovery":
        recovery_score = _first_present(record.get("recovery_score"), score.get("recovery_score"))
        values.update(
            {
                "recovery_score": _whoop_number(recovery_score),
                "recovery_band": record.get("recovery_band") or _whoop_recovery_band(recovery_score),
                "hrv_rmssd": _whoop_number(_first_present(record.get("hrv_rmssd"), score.get("hrv_rmssd_milli"))),
                "resting_hr": _whoop_number(_first_present(record.get("resting_hr"), score.get("resting_heart_rate"))),
                "respiratory_rate": _whoop_number(_first_present(record.get("respiratory_rate"), score.get("respiratory_rate"))),
                "spo2": _whoop_number(_first_present(record.get("spo2"), score.get("spo2_percentage"))),
                "skin_temp": _whoop_number(_first_present(record.get("skin_temp"), score.get("skin_temp_celsius"))),
            }
        )
    elif record_type == "sleep":
        if _whoop_truthy(record.get("nap")) or _whoop_truthy(score.get("nap")):
            return None
        sleep_score = _first_present(record.get("sleep_performance_pct"), score.get("sleep_performance_percentage"))
        gap = _first_present(record.get("sleep_need_gap_min"), score.get("sleep_needed_minutes"), _whoop_sleep_need_gap_minutes(score))
        values.update(
            {
                "sleep_performance_pct": _whoop_number(sleep_score),
                "sleep_need_gap_min": _whoop_number(gap),
            }
        )
    elif record_type == "cycle":
        values["strain"] = _whoop_number(_first_present(record.get("strain"), score.get("strain")))
    elif record_type == "workout":
        values["workout_kj"] = _whoop_number(_first_present(record.get("workout_kj"), score.get("kilojoule")))
    return values


def _normalize_whoop_records(record_type, records):
    normalized = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        row = _normalize_whoop_record(record_type, record)
        if row:
            normalized.append(row)
    return normalized


def _whoop_sync_window(days_back=7):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(days_back or 7))
    return start, end


def _coerce_whoop_sync_days(value):
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None, api_error("days_back must be an integer.", 400, code="invalid_days_back")
    if days < 1 or days > 30:
        return None, api_error("days_back must be between 1 and 30.", 400, code="invalid_days_back")
    return days, None


class _WhoopMutationGuard:
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self.handle = None
        self.thread_acquired = False

    def acquire(self) -> bool:
        if not WHOOP_SYNC_LOCK.acquire(blocking=False):
            return False
        self.thread_acquired = True
        try:
            os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
            self.handle = open(self.lock_path, "a", encoding="utf-8")
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.release()
            return False

    def release(self) -> None:
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        if self.thread_acquired:
            WHOOP_SYNC_LOCK.release()
            self.thread_acquired = False


def _acquire_whoop_mutation_guard() -> _WhoopMutationGuard | None:
    guard = _WhoopMutationGuard(WHOOP_SYNC_LOCK_FILE)
    return guard if guard.acquire() else None


def _run_whoop_sync(reason="manual", *, days_back=7, client_factory=WhoopClient):
    guard = _acquire_whoop_mutation_guard()
    if guard is None:
        return None, api_error("WHOOP sync is already running.", 409, code="whoop_sync_in_progress")
    try:
        return _run_whoop_sync_unlocked(reason, days_back=days_back, client_factory=client_factory)
    finally:
        guard.release()


def _run_whoop_sync_unlocked(reason="manual", *, days_back=7, client_factory=WhoopClient):
    connection = get_whoop_connection_status(WHOOP_DB_FILE, include_private=True)
    if connection.get("status") != "connected":
        return None, api_error("WHOOP is not connected.", 409, code="whoop_not_connected")
    material = load_connection_token_material(WHOOP_DB_FILE)
    if not material.get("session_value"):
        return None, api_error("WHOOP token material is unavailable. Reconnect WHOOP.", 409, code="whoop_token_unavailable")
    try:
        config = _whoop_config_for_redirect(_whoop_redirect_uri())
    except Exception:
        return None, api_error("WHOOP OAuth is not configured on this server.", 503, code="missing_whoop_config")
    start, end = _whoop_sync_window(days_back)
    run_id = record_whoop_sync_run(
        WHOOP_DB_FILE,
        reason=reason,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
    )
    client = None

    def persist_rotated_material_if_needed():
        nonlocal material, client
        if client is None:
            return
        session_value = getattr(client, "access_token", None)
        renewal_value = getattr(client, "refresh_token", None)
        if session_value != material.get("session_value") or renewal_value != material.get("renewal_value"):
            rotate_connection_tokens(
                WHOOP_DB_FILE,
                {
                    "access_token": session_value,
                    "refresh_token": renewal_value,
                    "expires_in": 3600,
                    "scope": "offline",
                },
            )
            material = {
                "session_value": session_value,
                "renewal_value": renewal_value,
            }

    try:
        client = client_factory(
            config,
            session_value=material.get("session_value"),
            renewal_value=material.get("renewal_value"),
        )
        collections = {
            "recovery": client.fetch_recovery(start=start, end=end),
        }
        persist_rotated_material_if_needed()
        collections["sleep"] = client.fetch_sleep(start=start, end=end)
        persist_rotated_material_if_needed()
        collections["cycle"] = client.fetch_cycles(start=start, end=end)
        persist_rotated_material_if_needed()
        collections["workout"] = client.fetch_workouts(start=start, end=end)
        persist_rotated_material_if_needed()
        upserted = 0
        for record_type, rows in collections.items():
            upserted += upsert_whoop_records(
                WHOOP_DB_FILE,
                record_type,
                _normalize_whoop_records(record_type, rows),
                sync_run_id=run_id,
            )
        project_whoop_daily_facts(WHOOP_DB_FILE)
        finish_whoop_sync_run(WHOOP_DB_FILE, run_id, status="success", records_upserted=upserted)
        return {
            "run_id": run_id,
            "records_upserted": upserted,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }, None
    except WhoopApiError as exc:
        persist_rotated_material_if_needed()
        if exc.status_code in {400, 401}:
            mark_reauth_required(WHOOP_DB_FILE, message=redact_whoop_error(exc))
        finish_whoop_sync_run(
            WHOOP_DB_FILE,
            run_id,
            status="error",
            retryable=exc.retryable,
            redacted_error=redact_whoop_error(exc),
        )
        return None, api_error(str(exc), 502, code="whoop_sync_failed")
    except Exception as exc:
        redacted = redact_whoop_error(f"{type(exc).__name__}: {exc}")
        try:
            persist_rotated_material_if_needed()
        except Exception as persist_exc:
            redacted = redact_whoop_error(f"{redacted}; token rotation failed: {type(persist_exc).__name__}: {persist_exc}")
        finish_whoop_sync_run(
            WHOOP_DB_FILE,
            run_id,
            status="error",
            retryable=False,
            redacted_error=redacted,
        )
        return None, api_error("WHOOP sync failed unexpectedly.", 500, code="whoop_sync_failed")


def _parse_whoop_csv_rows(text):
    reader = csv.DictReader(io.StringIO(text))
    records = []
    for index, row in enumerate(reader, start=1):
        if index > WHOOP_CSV_MAX_ROWS:
            raise ValueError(f"WHOOP CSV row limit exceeded ({WHOOP_CSV_MAX_ROWS}).")
        if not isinstance(row, dict):
            continue
        record_type = (row.get("record_type") or row.get("type") or "recovery").strip().lower()
        if record_type not in {"recovery", "sleep", "cycle", "workout"}:
            continue
        _validate_whoop_raw_metric_fields(row)
        normalized = _normalize_whoop_record(record_type, row)
        if normalized:
            normalized["local_date"] = _validate_imported_whoop_local_date(normalized["local_date"])
            _validate_whoop_metric_bounds(normalized)
        records.append((record_type, normalized))
    return [(kind, row) for kind, row in records if row]


def _whoop_has_csv_imported_data(freshness=None):
    node = freshness or {}
    local_date = node.get("last_data_point")
    if not local_date:
        return False
    try:
        return latest_whoop_fact_source_reason(WHOOP_DB_FILE, local_date=local_date) == "csv_import"
    except Exception:
        return False


def _annotate_whoop_freshness(freshness):
    node = dict(freshness or {})
    if not node.get("connected") and _whoop_has_csv_imported_data(node):
        node["source_kind"] = "csv_only"
        node["csv_only"] = True
    return node


def _whoop_freshness_is_relevant(freshness):
    node = freshness or {}
    return bool(
        node.get("connected")
        or node.get("reauth_required")
        or node.get("connection_status") == "reauth_required"
        or node.get("csv_only")
        or node.get("source_kind") == "csv_only"
    )


def _whoop_public_status(now=None):
    config = _whoop_config_or_none()
    connection = get_whoop_connection_status(WHOOP_DB_FILE)
    freshness = _annotate_whoop_freshness(latest_whoop_freshness(WHOOP_DB_FILE, now=now))
    fact = get_whoop_daily_fact(WHOOP_DB_FILE, local_date=_today_str()) or get_whoop_daily_fact(WHOOP_DB_FILE)
    if config is None:
        status = "missing_config"
    elif connection.get("reauth_required") or connection.get("status") == "reauth_required":
        status = "reauth_required"
    elif connection.get("status") == "connected":
        status = "connected"
    elif connection.get("status") == "disconnected":
        status = "disconnected"
    else:
        status = "error"
    return {
        "status": status,
        "configured": config is not None,
        "connected": status == "connected",
        "reauth_required": status == "reauth_required",
        "connected_at": connection.get("connected_at"),
        "last_successful_sync_at": connection.get("last_successful_sync_at"),
        "last_sync_attempt_at": connection.get("last_sync_attempt_at"),
        "last_error": connection.get("last_error"),
        "scopes": connection.get("scopes") or [],
        "freshness": freshness,
        "local_date": (fact or {}).get("local_date"),
        "recovery_score": (fact or {}).get("recovery_score"),
        "recovery_band": (fact or {}).get("recovery_band"),
        "strain": (fact or {}).get("strain"),
        "sleep_performance_pct": (fact or {}).get("sleep_performance_pct"),
        "sleep_need_gap_min": (fact or {}).get("sleep_need_gap_min"),
        "score_state": (fact or {}).get("score_state"),
    }


def _whoop_recommendation_context(oura_readiness=None, *, now=None):
    freshness = _annotate_whoop_freshness(latest_whoop_freshness(WHOOP_DB_FILE, now=now))
    public_status = _whoop_public_status(now=now)
    fact = None
    has_csv_import = bool(freshness.get("source_kind") == "csv_only")
    if freshness.get("connected") or has_csv_import:
        fact = get_whoop_daily_fact(WHOOP_DB_FILE, local_date=_today_str()) or get_whoop_daily_fact(WHOOP_DB_FILE)
    signals = build_whoop_recommendation_signals(fact, freshness=freshness)
    if has_csv_import and not public_status.get("connected"):
        signals["source_kind"] = "csv_only"
    source_conflict = detect_wearable_source_conflicts(
        oura_readiness=oura_readiness,
        whoop_signals=signals,
    )
    return {
        "status": public_status,
        "fact": fact,
        "signals": signals,
        "source_conflict": source_conflict,
    }


def _wearable_sources_payload(freshness, whoop_context):
    whoop_status = whoop_context.get("status") or {}
    whoop_signals = whoop_context.get("signals") or {}
    open_wearables_source = _open_wearables_status_source()
    direct_sources = [
        {
            "source": "whoop",
            "status": (freshness.get("whoop") or {}).get("status"),
            "last_data_point": (freshness.get("whoop") or {}).get("last_data_point"),
            "last_sync_attempt": (freshness.get("whoop") or {}).get("last_sync_attempt"),
            "score_state": (freshness.get("whoop") or {}).get("score_state"),
            "source_kind": (freshness.get("whoop") or {}).get("source_kind") or whoop_signals.get("source_kind"),
            "csv_only": bool((freshness.get("whoop") or {}).get("csv_only") or whoop_signals.get("source_kind") == "csv_only"),
            "connected": whoop_status.get("connected"),
            "reauth_required": bool((freshness.get("whoop") or {}).get("reauth_required") or whoop_status.get("reauth_required")),
            "used_for_recommendation": not whoop_signals.get("display_only"),
        },
        {
            "source": "oura",
            "status": (freshness.get("oura") or {}).get("status"),
            "last_data_point": (freshness.get("oura") or {}).get("last_data_point"),
            "last_sync_attempt": (freshness.get("oura") or {}).get("last_sync_attempt"),
            "score_state": "SCORED" if (freshness.get("oura") or {}).get("last_data_point") else None,
            "connected": True,
            "used_for_recommendation": True,
        },
        {
            "source": "apple_health",
            "status": (freshness.get("apple_health") or {}).get("status"),
            "last_data_point": (freshness.get("apple_health") or {}).get("last_data_point"),
            "last_sync_attempt": (freshness.get("apple_health") or {}).get("last_sync_attempt"),
            "score_state": None,
            "connected": True,
            "used_for_recommendation": True,
        },
    ]
    return [
        source
        for source in direct_sources
        if not _open_wearables_supersedes_direct_source(source.get("source"), open_wearables_source)
    ] + [open_wearables_source]


@app.route('/api/whoop/status')
def whoop_status():
    return jsonify(_whoop_public_status())


@app.route('/api/whoop/connect/start', methods=['POST'])
def whoop_connect_start():
    guard = _whoop_mutation_guard()
    if guard:
        return guard
    try:
        redirect_uri = _whoop_redirect_uri()
        config = _whoop_config_for_redirect(redirect_uri)
    except Exception:
        return api_error(
            "WHOOP OAuth is not configured on this server.",
            503,
            code="missing_whoop_config",
        )
    if config is None:
        return api_error(
            "WHOOP OAuth is not configured on this server.",
            503,
            code="missing_whoop_config",
        )
    state = secrets.token_urlsafe(32)
    create_oauth_state(
        WHOOP_DB_FILE,
        state=state,
        redirect_uri=redirect_uri,
        user_binding=_whoop_user_binding(),
        ttl_minutes=10,
    )
    return _whoop_no_store(jsonify(
        {
            "status": "success",
            "authorization_url": create_whoop_authorization_url(config, state=state),
            "connection": _whoop_public_status(),
        }
    ))


@app.route('/api/whoop/callback')
def whoop_callback():
    state = str(request.args.get("state") or "").strip()
    code = str(request.args.get("code") or "").strip()
    if not state:
        return _whoop_no_store(api_error("state is required", 400, code="missing_state"))
    if not code:
        return _whoop_no_store(api_error("code is required", 400, code="missing_code"))
    mutation_guard = _acquire_whoop_mutation_guard()
    if mutation_guard is None:
        return _whoop_no_store(api_error("WHOOP sync is already running.", 409, code="whoop_sync_in_progress"))
    try:
        saved_state = consume_oauth_state(WHOOP_DB_FILE, state, user_binding=_whoop_user_binding())
        if saved_state is None:
            if _open_wearables_complete_oauth_callback("whoop", code, state):
                if _whoop_callback_is_browser_navigation():
                    return _whoop_no_store(redirect("/#settings"))
                return _whoop_no_store(jsonify({"status": "success", "provider": "whoop", "source": "open_wearables"}))
            return _whoop_no_store(api_error("WHOOP OAuth state is invalid or expired.", 400, code="invalid_state"))
        config = _whoop_config_for_redirect(saved_state["redirect_uri"])
        grant_payload = exchange_whoop_code(config, code=code)
        validation_error = _validate_whoop_token_payload(grant_payload)
        if validation_error:
            return _whoop_no_store(api_error(validation_error, 502, code="invalid_whoop_token_payload"))
        save_connection_tokens(WHOOP_DB_FILE, grant_payload)
    except WhoopApiError as exc:
        return _whoop_no_store(api_error(str(exc), 502, code="whoop_oauth_failed"))
    except Exception:
        return _whoop_no_store(api_error("WHOOP OAuth is not configured on this server.", 503, code="missing_whoop_config"))
    finally:
        mutation_guard.release()
    if _whoop_callback_is_browser_navigation():
        return _whoop_no_store(redirect("/#settings"))
    return _whoop_no_store(jsonify({"status": "success", "connection": _whoop_public_status()}))


@app.route('/api/whoop/disconnect', methods=['POST'])
def whoop_disconnect():
    guard = _whoop_mutation_guard()
    if guard:
        return guard
    mutation_guard = _acquire_whoop_mutation_guard()
    if mutation_guard is None:
        return api_error("WHOOP sync is already running.", 409, code="whoop_sync_in_progress")
    try:
        revocation = {"status": "skipped"}
        material = load_connection_token_material(WHOOP_DB_FILE)
        session_value = material.get("session_value")
        if session_value:
            try:
                config = _whoop_config_for_redirect(_whoop_redirect_uri())
                revoke_whoop_access(config, session_value=session_value)
                revocation = {"status": "revoked"}
            except WhoopApiError as exc:
                revocation = {"status": "failed", "message": redact_whoop_error(exc)}
            except Exception:
                revocation = {"status": "failed", "message": "WHOOP OAuth is not configured on this server."}
        try:
            invalidate_oauth_states(WHOOP_DB_FILE, user_binding=_whoop_user_binding())
            disconnect_whoop(WHOOP_DB_FILE)
        except (OSError, RuntimeError, SubprocessError):
            return api_error(
                "WHOOP local token material could not be removed. Disconnect was not completed.",
                500,
                code="whoop_disconnect_failed",
            )
        return jsonify({"status": "success", "connection": _whoop_public_status(), "revocation": revocation})
    finally:
        mutation_guard.release()


@app.route('/api/whoop/delete-data', methods=['POST'])
def whoop_delete_data():
    guard = _whoop_mutation_guard()
    if guard:
        return guard
    guard = _acquire_whoop_mutation_guard()
    if guard is None:
        return api_error("WHOOP sync is already running.", 409, code="whoop_sync_in_progress")
    try:
        invalidate_oauth_states(WHOOP_DB_FILE, user_binding=_whoop_user_binding())
        clear_whoop_data(WHOOP_DB_FILE)
        return jsonify({"status": "success", "connection": _whoop_public_status()})
    finally:
        guard.release()


@app.route('/api/whoop/sync', methods=['POST'])
def whoop_sync():
    guard = _whoop_mutation_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    days_back_value = body["days_back"] if "days_back" in body else 7
    days_back, err = _coerce_whoop_sync_days(days_back_value)
    if err:
        return err
    result, err = _run_whoop_sync("manual", days_back=days_back)
    if err:
        return err
    return jsonify({"status": "success", "sync": result, "connection": _whoop_public_status()})


@app.route('/api/whoop/import-csv', methods=['POST'])
def whoop_import_csv():
    guard = _whoop_mutation_guard()
    if guard:
        return guard
    text = ""
    content_length = request.content_length
    if content_length is not None and content_length > WHOOP_CSV_MAX_BYTES:
        return api_error("WHOOP CSV is too large.", 413, code="whoop_csv_too_large")
    if request.files:
        upload = next(iter(request.files.values()))
        raw = upload.stream.read(WHOOP_CSV_MAX_BYTES + 1)
        if len(raw) > WHOOP_CSV_MAX_BYTES:
            return api_error("WHOOP CSV is too large.", 413, code="whoop_csv_too_large")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return api_error("WHOOP CSV must be UTF-8 encoded.", 400, code="invalid_whoop_csv_encoding")
    else:
        body = request.get_json(silent=True) or {}
        text = str(body.get("csv") or "")
        if len(text.encode("utf-8")) > WHOOP_CSV_MAX_BYTES:
            return api_error("WHOOP CSV is too large.", 413, code="whoop_csv_too_large")
    if not text.strip():
        return api_error("CSV content is required.", 400, code="missing_csv")
    try:
        parsed = _parse_whoop_csv_rows(text)
    except ValueError as exc:
        error_code = "whoop_csv_too_many_rows" if "row limit" in str(exc).lower() else "invalid_whoop_csv_metric"
        status_code = 413 if error_code == "whoop_csv_too_many_rows" else 400
        return api_error(str(exc), status_code, code=error_code)
    if not parsed:
        return api_error("No WHOOP rows could be imported from the CSV.", 400, code="empty_whoop_csv")
    mutation_guard = _acquire_whoop_mutation_guard()
    if mutation_guard is None:
        return api_error("WHOOP sync is already running.", 409, code="whoop_sync_in_progress")
    try:
        run_id = record_whoop_sync_run(WHOOP_DB_FILE, reason="csv_import")
        by_type = {"recovery": [], "sleep": [], "cycle": [], "workout": []}
        for record_type, row in parsed:
            by_type[record_type].append(row)
        upserted = 0
        for record_type, rows in by_type.items():
            upserted += upsert_whoop_records(WHOOP_DB_FILE, record_type, rows, sync_run_id=run_id)
        project_whoop_daily_facts(WHOOP_DB_FILE)
        finish_whoop_sync_run(WHOOP_DB_FILE, run_id, status="success", records_upserted=upserted)
        return jsonify(
            {
                "status": "success",
                "import": {
                    "run_id": run_id,
                    "records_upserted": upserted,
                },
                "connection": _whoop_public_status(),
            }
        )
    finally:
        mutation_guard.release()


@app.route('/api/whoop/imports')
def whoop_imports():
    return jsonify({"status": "success", "imports": list_whoop_sync_runs(WHOOP_DB_FILE, reason="csv_import", limit=20)})


@app.route('/api/whoop/recommendation-signals')
def whoop_recommendation_signals():
    today = _today_str()
    cached = get_oura_daily(OURA_DB_FILE, today) or {}
    context = _whoop_recommendation_context(cached.get("readiness_score"))
    return jsonify(
        {
            "status": "success",
            "connection": context["status"],
            "signals": context["signals"],
            "source_conflict": context["source_conflict"],
        }
    )

@app.route('/api/oura/status')
def oura_status():
    """Return today's Oura readiness, HRV, and sleep score.

    Persists into SQLite for historical tracking.
    Use ?refresh=true to force re-fetch from Oura API.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    force_refresh = request.args.get('refresh', '').lower() == 'true'

    def _best_effort_steps_activity(steps, activity_score):
        """Oura daily_activity can lag behind readiness/sleep. If today's DB row doesn't
        have steps/activity yet, fall back to the most recent day that does."""
        activity_day = today
        if steps is None or activity_score is None:
            try:
                start = (datetime.now().date() - timedelta(days=14)).strftime("%Y-%m-%d")
                rows = get_oura_daily_range(OURA_DB_FILE, start, today)
                for r in reversed(rows):
                    if r.get("steps") is not None or r.get("activity_score") is not None:
                        if steps is None:
                            steps = r.get("steps")
                        if activity_score is None:
                            activity_score = r.get("activity_score")
                        activity_day = r.get("day") or activity_day
                        break
            except Exception:
                pass
        return steps, activity_score, activity_day

    # Prefer DB cached values (unless force refresh)
    if not force_refresh:
        cached = get_oura_daily(OURA_DB_FILE, today)
        if cached:
            steps, activity_score, activity_day = _best_effort_steps_activity(
                cached.get("steps"),
                cached.get("activity_score"),
            )
            return jsonify({
                "date": today,
                "readiness": cached.get("readiness_score"),
                "sleep_score": cached.get("sleep_score"),
                "hrv": cached.get("hrv"),
                "steps": steps,
                "activity_score": activity_score,
                "activity_day": activity_day,
                "resting_hr": cached.get("resting_hr"),
                "temperature_deviation": cached.get("temperature_deviation"),
                "sleep_duration_min": cached.get("sleep_duration_min"),
                "sleep_breakdown_min": {
                    "deep": cached.get("sleep_deep_min"),
                    "rem": cached.get("sleep_rem_min"),
                    "light": cached.get("sleep_light_min"),
                    "awake": cached.get("sleep_awake_min"),
                },
                "source": "db"
            })

    # Fetch from API (best-effort). If API/token fails, fall back to cached DB if present.
    try:
        client = OuraClient()
    except Exception as e:
        cached = get_oura_daily(OURA_DB_FILE, today)
        if cached:
            steps, activity_score, activity_day = _best_effort_steps_activity(
                cached.get("steps"),
                cached.get("activity_score"),
            )
            return jsonify({
                "date": today,
                "readiness": cached.get("readiness_score"),
                "sleep_score": cached.get("sleep_score"),
                "hrv": cached.get("hrv"),
                "steps": steps,
                "activity_score": activity_score,
                "activity_day": activity_day,
                "resting_hr": cached.get("resting_hr"),
                "temperature_deviation": cached.get("temperature_deviation"),
                "sleep_duration_min": cached.get("sleep_duration_min"),
                "sleep_breakdown_min": {
                    "deep": cached.get("sleep_deep_min"),
                    "rem": cached.get("sleep_rem_min"),
                    "light": cached.get("sleep_light_min"),
                    "awake": cached.get("sleep_awake_min"),
                },
                "source": "db",
                "warning": f"Oura unavailable: {str(e)}"
            })
        return jsonify({"available": False, "error": str(e)}), 503

    try:
        readiness_score, sleep_score, hrv, metrics, raw = client.get_today_metrics(today)

        # Oura daily_activity can lag by a day; keep readiness/sleep on "today", but
        # store activity metrics against their actual day when possible.
        activity_day = metrics.get("activity_day") or today

        upsert_oura_daily(
            OURA_DB_FILE,
            day=today,
            readiness_score=readiness_score,
            sleep_score=sleep_score,
            hrv=hrv,
            raw_json=raw,
            steps=metrics.get("steps") if activity_day == today else None,
            activity_score=metrics.get("activity_score") if activity_day == today else None,
            resting_hr=metrics.get("resting_hr"),
            temperature_deviation=metrics.get("temperature_deviation"),
            sleep_duration_min=metrics.get("sleep_duration_min"),
            sleep_deep_min=metrics.get("sleep_deep_min"),
            sleep_rem_min=metrics.get("sleep_rem_min"),
            sleep_light_min=metrics.get("sleep_light_min"),
            sleep_awake_min=metrics.get("sleep_awake_min"),
            active_calories=metrics.get("active_calories") if activity_day == today else None,
        )

        if activity_day and activity_day != today:
            upsert_oura_daily(
                OURA_DB_FILE,
                day=activity_day,
                readiness_score=None,
                sleep_score=None,
                hrv=None,
                raw_json=None,
                steps=metrics.get("steps"),
                activity_score=metrics.get("activity_score"),
                active_calories=metrics.get("active_calories"),
            )
        steps = metrics.get("steps")
        activity_score = metrics.get("activity_score")
        if steps is None and activity_score is None:
            steps, activity_score, activity_day = _best_effort_steps_activity(steps, activity_score)

        return jsonify({
            "date": today,
            "readiness": readiness_score,
            "sleep_score": sleep_score,
            "hrv": hrv,
            "steps": steps,
            "activity_score": activity_score,
            "active_calories": metrics.get("active_calories"),
            "activity_day": activity_day,
            "resting_hr": metrics.get("resting_hr"),
            "temperature_deviation": metrics.get("temperature_deviation"),
            "sleep_duration_min": metrics.get("sleep_duration_min"),
            "sleep_breakdown_min": {
                "deep": metrics.get("sleep_deep_min"),
                "rem": metrics.get("sleep_rem_min"),
                "light": metrics.get("sleep_light_min"),
                "awake": metrics.get("sleep_awake_min"),
            },
            "source": "api"
        })
    except Exception as e:
        cached = get_oura_daily(OURA_DB_FILE, today)
        if cached:
            return jsonify({
                "date": today,
                "readiness": cached.get("readiness_score"),
                "sleep_score": cached.get("sleep_score"),
                "hrv": cached.get("hrv"),
                "steps": cached.get("steps"),
                "activity_score": cached.get("activity_score"),
                "active_calories": cached.get("active_calories"),
                "resting_hr": cached.get("resting_hr"),
                "temperature_deviation": cached.get("temperature_deviation"),
                "sleep_duration_min": cached.get("sleep_duration_min"),
                "sleep_breakdown_min": {
                    "deep": cached.get("sleep_deep_min"),
                    "rem": cached.get("sleep_rem_min"),
                    "light": cached.get("sleep_light_min"),
                    "awake": cached.get("sleep_awake_min"),
                },
                "source": "db",
                "warning": f"Oura API error: {str(e)}"
            })
        return jsonify({"available": False, "error": str(e)}), 502


@app.route('/api/oura/trends')
def oura_trends():
    """Return 7-day HRV trend (and the series) for readiness guidance."""
    end = datetime.now().date()
    start = end - timedelta(days=6)
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    def public_rows(items):
        cleaned = []
        for row in items or []:
            public = dict(row)
            public.pop("raw_json", None)
            cleaned.append(public)
        return cleaned

    rows = get_oura_daily_range(OURA_DB_FILE, start_s, end_s)

    # If we don't have enough cached days, fetch and upsert from API.
    if len(rows) < 3:
        try:
            client = OuraClient()
            daily = client.get_daily_range(start_s, end_s)
            for d in daily:
                upsert_oura_daily(
                    OURA_DB_FILE,
                    day=d.get("day"),
                    readiness_score=d.get("readiness_score"),
                    sleep_score=d.get("sleep_score"),
                    hrv=d.get("hrv"),
                    raw_json=d.get("raw_json"),
                    steps=d.get("steps"),
                    activity_score=d.get("activity_score"),
                    active_calories=d.get("active_calories"),
                    resting_hr=d.get("resting_hr"),
                    temperature_deviation=d.get("temperature_deviation"),
                    sleep_duration_min=d.get("sleep_duration_min"),
                    sleep_deep_min=d.get("sleep_deep_min"),
                    sleep_rem_min=d.get("sleep_rem_min"),
                    sleep_light_min=d.get("sleep_light_min"),
                    sleep_awake_min=d.get("sleep_awake_min"),
                )
            rows = get_oura_daily_range(OURA_DB_FILE, start_s, end_s)
        except Exception as e:
            # Still return whatever we have
            return jsonify({
                "start_date": start_s,
                "end_date": end_s,
                "hrv_trend": "unknown",
                "series": public_rows(rows),
                "error": str(e)
            }), 200

    trend = compute_hrv_trend([r.get("hrv") for r in rows if r.get("hrv") is not None])
    return jsonify({
        "start_date": start_s,
        "end_date": end_s,
        "hrv_trend": trend,
        "series": public_rows(rows)
    })


@app.route('/api/oura/sync-sleep', methods=['POST'])
def sync_oura_sleep():
    """Sync latest sleep data from Oura API."""
    global LAST_WORKOUT_RECOMMENDATION
    from oura_sleep_sync import create_sleep_table, sync_sleep_data, get_latest_sleep

    data, err = get_json_body(required=False)
    if err:
        return err
    days_back, err = _coerce_int(data.get("days_back", 30), "days_back", min_v=1, max_v=365)
    if err:
        return err

    try:
        sync_date = datetime.now().date()
        start_date = (sync_date - timedelta(days=days_back)).strftime("%Y-%m-%d")

        # Ensure table exists
        create_sleep_table(OURA_DB_FILE)

        # Get API token
        api_token = os.environ.get("OURA_API_TOKEN", "").strip()
        if not api_token:
            return api_error(
                "Oura API token is not configured on this server. Set OURA_API_TOKEN and restart the app.",
                503,
                code="missing_oura_token",
            )

        # Sync data
        sync_sleep_data(OURA_DB_FILE, api_token, start_date=start_date)

        # Return a summary only; raw wearable rows stay server-side.
        latest = get_latest_sleep(OURA_DB_FILE, days=7)
        latest_days = [r.get("day") for r in latest if r.get("day")]
        LAST_WORKOUT_RECOMMENDATION = None
        delete_current_workout_plan(_current_data_user_id())

        return jsonify({
            "status": "success",
            "synced_from": start_date,
            "synced_through": sync_date.strftime("%Y-%m-%d"),
            "latest_records": len(latest),
            "latest_days": latest_days,
        })
    except urllib.error.HTTPError as e:
        return api_error(f"Oura API returned HTTP {e.code}.", 502, code="oura_api_error")
    except urllib.error.URLError:
        return api_error("Oura API request failed.", 502, code="oura_api_error")
    except Exception:
        return api_error("Oura sync failed.", 500, code="oura_sync_failed")


@app.route('/api/oura/sleep-summary')
def oura_sleep_summary():
    """Get sleep summary for dashboard (last night + 7-day trends)."""
    from oura_sleep_sync import get_latest_sleep, get_sleep_range, calculate_bedtime_variance

    try:
        # Get last night's sleep
        latest = get_latest_sleep(OURA_DB_FILE, days=1, long_sleep_only=True)
        last_night = latest[0] if latest else None

        # Get 7-day data
        end = datetime.now().date()
        start = end - timedelta(days=6)
        week_data = get_sleep_range(
            OURA_DB_FILE,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
            long_sleep_only=True
        )

        # Prefer oura_daily (today's cached daily sleep fields) when it's newer or fuller
        # than the oura_sleep row. The oura_sleep table can go stale; oura_daily is
        # refreshed by /api/oura/status on every dashboard load.
        today_s = end.strftime("%Y-%m-%d")
        daily_today = get_oura_daily(OURA_DB_FILE, today_s)
        def _daily_to_row(d):
            if not d:
                return None
            dur = d.get("sleep_duration_min")
            if not dur and not d.get("sleep_score"):
                return None
            return {
                "day": d.get("day"),
                "total_sleep_min": dur or 0,
                "deep_sleep_min": d.get("sleep_deep_min") or 0,
                "rem_sleep_min": d.get("sleep_rem_min") or 0,
                "light_sleep_min": d.get("sleep_light_min") or 0,
                "awake_time_min": d.get("sleep_awake_min") or 0,
                "sleep_score": d.get("sleep_score"),
                "avg_heart_rate": None,
                "efficiency": None,
            }

        daily_row = _daily_to_row(daily_today)
        ln_day = (last_night or {}).get("day")
        if daily_row and (not last_night or (ln_day or "") < daily_row["day"]
                         or (last_night.get("sleep_score") in (None, 0) and daily_row.get("sleep_score"))):
            last_night = daily_row

        # Augment week_data from oura_daily range where a day is missing.
        # oura_sleep can lag behind oura_daily, so we fall back to the daily
        # cache to keep the 7-day averages meaningful.
        try:
            if week_data is None:
                week_data = []
            daily_range = get_oura_daily_range(OURA_DB_FILE, start.strftime("%Y-%m-%d"), today_s) or []
            existing_days = {r.get("day") for r in week_data}
            for d in daily_range:
                row = _daily_to_row(d)
                if row and row["day"] not in existing_days:
                    week_data.append(row)
                    existing_days.add(row["day"])
        except Exception:
            pass

        # Calculate 7-day averages
        avg_duration = 0
        avg_score = 0
        avg_deep = 0
        avg_rem = 0
        avg_hr = 0

        if week_data:
            durations = [r.get("total_sleep_min") or 0 for r in week_data]
            scores = [r.get("sleep_score") or 0 for r in week_data if r.get("sleep_score")]
            deeps = [r.get("deep_sleep_min") or 0 for r in week_data]
            rems = [r.get("rem_sleep_min") or 0 for r in week_data]
            hrs = [r.get("avg_heart_rate") or 0 for r in week_data if r.get("avg_heart_rate")]

            avg_duration = int(sum(durations) / len(durations)) if durations else 0
            avg_score = int(sum(scores) / len(scores)) if scores else 0
            avg_deep = int(sum(deeps) / len(deeps)) if deeps else 0
            avg_rem = int(sum(rems) / len(rems)) if rems else 0
            avg_hr = round(sum(hrs) / len(hrs), 1) if hrs else 0

        # Bedtime consistency
        bedtime_variance = calculate_bedtime_variance(OURA_DB_FILE, days=7)

        # Consistency status
        if bedtime_variance is None:
            consistency_status = "unknown"
        elif bedtime_variance < 30:
            consistency_status = "excellent"
        elif bedtime_variance < 60:
            consistency_status = "good"
        elif bedtime_variance < 90:
            consistency_status = "fair"
        else:
            consistency_status = "poor"

        return jsonify({
            "last_night": {
                "date": last_night.get("day") if last_night else None,
                "total_sleep_min": last_night.get("total_sleep_min") if last_night else 0,
                "deep_sleep_min": last_night.get("deep_sleep_min") if last_night else 0,
                "rem_sleep_min": last_night.get("rem_sleep_min") if last_night else 0,
                "light_sleep_min": last_night.get("light_sleep_min") if last_night else 0,
                "awake_time_min": last_night.get("awake_time_min") if last_night else 0,
                "sleep_score": last_night.get("sleep_score") if last_night else 0,
                "avg_heart_rate": last_night.get("avg_heart_rate") if last_night else 0,
                "efficiency": last_night.get("efficiency") if last_night else 0,
            },
            "week_average": {
                "duration_min": avg_duration,
                "score": avg_score,
                "deep_min": avg_deep,
                "rem_min": avg_rem,
                "avg_heart_rate": avg_hr,
            },
            "consistency": {
                "bedtime_variance_min": bedtime_variance,
                "status": consistency_status,
            },
            "trend_data": [
                {
                    "date": r.get("day"),
                    "duration_min": r.get("total_sleep_min") or 0,
                    "score": r.get("sleep_score") or 0,
                }
                for r in week_data
            ]
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== DATA FRESHNESS (Oura / Apple Health / Food) ====================
#
# Stale buckets (server-side; the client never does "Nh ago" math):
#   fresh:   last data point within 24h
#   aging:   24h–48h
#   stale:   >48h
#   missing: no record at all
#
# "Connected" never means "current" — the freshness object is what the dashboard
# uses to drop confidence and swap the brief to lower-confidence copy.

def _apple_health_sync_db_file():
    return (
        os.environ.get("APPLE_HEALTH_SYNC_DB")
        or data_path("apple_health_sync.db")
    )


_FRESHNESS_AGING_HOURS = 24
_FRESHNESS_STALE_HOURS = 48


def _classify_freshness(last_data_point_dt, now=None):
    if last_data_point_dt is None:
        return "missing"
    now = now or datetime.now()
    hours = (now - last_data_point_dt).total_seconds() / 3600.0
    if hours < _FRESHNESS_AGING_HOURS:
        return "fresh"
    if hours < _FRESHNESS_STALE_HOURS:
        return "aging"
    return "stale"


def _latest_oura_freshness(now=None):
    try:
        conn = sqlite3.connect(OURA_DB_FILE)
        try:
            row = conn.execute(
                "SELECT day, created_at FROM oura_daily ORDER BY day DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return ("missing", None, None)
    if not row:
        return ("missing", None, None)
    day_str, created_at = row
    # last_data_point uses oura_daily.day (the data-point date); last_sync_attempt
    # uses created_at (when we upserted). A row upserted today with day=N days ago
    # means Oura hasn't returned new data even though we "synced."
    return (_classify_freshness(_parse_iso_date_or_datetime(day_str), now=now), day_str, created_at)


def _latest_apple_health_freshness(now=None):
    try:
        conn = sqlite3.connect(_apple_health_sync_db_file())
        try:
            data_row = conn.execute("SELECT MAX(record_date) FROM ah_sync_log").fetchone()
            sync_row = conn.execute("SELECT MAX(created_at) FROM ah_sync_events").fetchone()
        finally:
            conn.close()
    except Exception:
        return ("missing", None, None)
    last_data = (data_row or (None,))[0]
    last_sync = (sync_row or (None,))[0]
    if not last_data:
        return ("missing", None, last_sync)
    return (_classify_freshness(_parse_iso_date_or_datetime(last_data), now=now), last_data, last_sync)


def _latest_food_freshness(now=None):
    """Return (status, last_data_point, last_sync_attempt) plus None placeholders.

    Note: callers should also use `_food_target_state()` for the macro-target view
    used by the brief — that bit doesn't fit the (status, dp, sync) triple cleanly.
    """
    entries = [
        entry for entry in _food_log_entries_for_context()
        if _nutrition_entry_accepted(entry)
    ]
    if not entries:
        entries = [
            entry for entry in (NUTRITION_DATA if isinstance(NUTRITION_DATA, list) else [])
            if _nutrition_entry_accepted(entry)
        ]
    if not entries:
        return ("missing", None, None)
    latest_iso = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        d = _nutrition_entry_day(entry)
        if d and (latest_iso is None or d > latest_iso):
            latest_iso = d
    if not latest_iso:
        return ("missing", None, None)
    return (_classify_freshness(_parse_iso_date_or_datetime(latest_iso), now=now), latest_iso, None)


def _food_target_state(now=None):
    """Return a dict describing today's food vs targets:
        {"target_state": "none|under|on_track|over",
         "calories": int, "protein_g": float,
         "calories_target": int, "protein_target_g": float,
         "calories_pct": int, "protein_pct": int}

    `target_state` thresholds (calories-driven):
        none       -> nothing logged today
        under      -> < 80% of daily calorie target
        on_track   -> 80–110%
        over       -> > 110%
    """
    today_s = (now or datetime.now()).strftime("%Y-%m-%d")
    try:
        context = _nutrition_context_for_date(
            today_s,
            food_log_entries=_food_log_entries_for_context(since=today_s),
        )
        totals = context["totals"]
        targets = context["targets"]
        remaining = context["remaining"]
        percentages = context["percentages"]
    except Exception:
        totals = {"calories": 0, "protein_g": 0.0}
        calories_target, protein_target = 2200, 148.0
        remaining = {"calories": calories_target, "protein_g": protein_target}
        percentages = {"calories": 0, "protein": 0}
    else:
        calories_target = targets["calories"]
        protein_target = targets["protein_g"]
    calories = int(totals.get("calories") or 0)
    protein_g = float(totals.get("protein_g") or 0.0)
    cal_pct = int(percentages.get("calories") or 0)
    pro_pct = int(percentages.get("protein") or 0)
    if calories <= 0 and protein_g <= 0:
        target_state = "none"
    elif cal_pct > 110:
        target_state = "over"
    elif cal_pct >= 80:
        target_state = "on_track"
    else:
        target_state = "under"
    return {
        "target_state": target_state,
        "calories": calories,
        "protein_g": round(protein_g, 1),
        "calories_target": int(calories_target),
        "protein_target_g": round(float(protein_target), 1),
        "calories_remaining": int(remaining["calories"]),
        "protein_gap_g": round(float(remaining["protein_g"]), 1),
        "calories_pct": cal_pct,
        "protein_pct": pro_pct,
    }


def _food_pending_review_state(now=None):
    today_s = (now or datetime.now()).strftime("%Y-%m-%d")
    food_log_entries = [
        entry for entry in _food_log_entries_for_context(since=today_s)
        if _nutrition_entry_day(entry) == today_s
    ]
    entries = food_log_entries or (NUTRITION_DATA if isinstance(NUTRITION_DATA, list) else [])
    return any(
        isinstance(entry, dict)
        and _nutrition_entry_day(entry) == today_s
        and _nutrition_entry_pending_review(entry)
        for entry in entries
    )


def _oura_source_label(last_sync_attempt_iso, now=None):
    """Map last_sync_attempt to a cached/live label.

    The freshness chip is always rendering locally-cached data; "live" here means
    the last upsert happened recently enough that the cache is effectively current.
    """
    if not last_sync_attempt_iso:
        return "cached"
    dt = _parse_iso_date_or_datetime(last_sync_attempt_iso)
    if dt is None:
        return "cached"
    now = now or datetime.now()
    age_h = (now - dt).total_seconds() / 3600.0
    return "live" if age_h < 1.0 else "cached"


@app.route('/api/freshness')
def freshness_only():
    """FIT-16: side-effect-free freshness block for the Settings panel.

    /api/dashboard also returns freshness but as part of regenerating the
    next-workout recommendation (it writes LAST_WORKOUT_RECOMMENDATION).
    Hitting that endpoint from Settings would silently reset the server-
    canonical plan a user adjusted/swapped on the Workout tab. This
    endpoint just exposes the same freshness dict without any of the
    recommendation-state mutations.
    """
    return jsonify({"freshness": _compute_data_freshness()})


def _compute_data_freshness(now=None):
    """Per-source freshness for Oura, WHOOP, Apple Health, and food.

    Returns:
        {
          "oura":         {status, last_data_point, last_sync_attempt, source: "live|cached"},
          "whoop":        {status, last_data_point, last_sync_attempt, score_state, connected},
          "apple_health": {status, last_data_point, last_sync_attempt},
          "food":         {status, last_data_point, last_sync_attempt,
                           pending_review: bool, target_state: "none|under|on_track|over",
                           calories, protein_g, calories_target, protein_target_g,
                           calories_pct, protein_pct},
        }
    """
    now = now or datetime.now()
    oura_status, oura_last_data, oura_last_sync = _latest_oura_freshness(now)
    whoop_freshness = _annotate_whoop_freshness(latest_whoop_freshness(WHOOP_DB_FILE, now=now))
    apple_status, apple_last_data, apple_last_sync = _latest_apple_health_freshness(now)
    food_status, food_last_data, food_last_sync = _latest_food_freshness(now)
    food_targets = _food_target_state(now)
    open_wearables_providers, open_wearables_error = _fetch_open_wearables_provider_statuses()
    open_wearables_status = _open_wearables_public_status(
        providers=open_wearables_providers,
        error_code=open_wearables_error if open_wearables_error != "missing_config" else None,
    )
    open_wearables_bucket = "fresh" if open_wearables_status.get("status") == "connected" else "missing"
    if open_wearables_status.get("status") in {"blocked", "error"}:
        open_wearables_bucket = "stale"
    freshness = {
        "open_wearables": {
            "status": open_wearables_bucket,
            "hub_status": open_wearables_status.get("status"),
            "last_data_point": None,
            "last_sync_attempt": open_wearables_status.get("last_checked_at"),
            "source": "hub",
            "connected": open_wearables_status.get("status") == "connected",
            "providers": open_wearables_status.get("providers") or [],
            "facts_ready": bool(open_wearables_status.get("facts_ready")),
            "replacement_sources": open_wearables_status.get("replacement_sources") or [],
            "replacement_source_dates": open_wearables_status.get("replacement_source_dates") or {},
        },
        "oura": {
            "status": oura_status,
            "last_data_point": oura_last_data,
            "last_sync_attempt": oura_last_sync,
            "source": _oura_source_label(oura_last_sync, now=now),
        },
        "whoop": whoop_freshness,
        "apple_health": {
            "status": apple_status,
            "last_data_point": apple_last_data,
            "last_sync_attempt": apple_last_sync,
        },
        "food": {
            "status": food_status,
            "last_data_point": food_last_data,
            "last_sync_attempt": food_last_sync,
            "pending_review": _food_pending_review_state(now),
            **food_targets,
        },
    }
    return _open_wearables_adjusted_freshness(freshness)


def _open_wearables_adjusted_freshness(freshness):
    safe = dict(freshness or {})
    open_wearables_source = safe.get("open_wearables") or {}
    replacement_source_dates = open_wearables_source.get("replacement_source_dates") or {}
    for source_key in ("oura", "apple_health"):
        if not _open_wearables_supersedes_direct_source(source_key, open_wearables_source):
            continue
        node = dict(safe.get(source_key) or {})
        node["status"] = "fresh"
        node["source"] = "open_wearables"
        node["superseded_by"] = "open_wearables"
        node["superseded_source"] = source_key
        if replacement_source_dates.get(source_key):
            node["last_data_point"] = replacement_source_dates.get(source_key)
        if open_wearables_source.get("last_sync_attempt"):
            node["last_sync_attempt"] = open_wearables_source.get("last_sync_attempt")
        safe[source_key] = node
    return safe


def _push_alert_preview(now=None):
    """Deterministic, non-sending alert contract for FIT-39."""
    freshness = _compute_data_freshness(now=now)
    open_wearables_source = freshness.get("open_wearables") or {}
    alerts = []
    for source_key, label in (("oura", "Oura"), ("whoop", "WHOOP"), ("apple_health", "Apple Health")):
        source = freshness.get(source_key) or {}
        if _open_wearables_supersedes_direct_source(source_key, open_wearables_source):
            continue
        if source_key == "whoop" and not _whoop_freshness_is_relevant(source):
            continue
        if source.get("status") in {"aging", "stale", "missing"}:
            alerts.append({
                "type": "stale_wearable_data",
                "source": source_key,
                "title": f"{label} data needs attention",
                "body": "Open the app to refresh wearable data before trusting today's recommendation.",
                "severity": "warning" if source.get("status") == "stale" else "info",
                "status": source.get("status"),
                "last_data_point": source.get("last_data_point"),
                "last_sync_attempt": source.get("last_sync_attempt"),
                "safety_critical": False,
            })
    food = freshness.get("food") or {}
    if food.get("pending_review"):
        alerts.append({
            "type": "pending_food_estimate_review",
            "source": "food",
            "title": "Meal estimate needs review",
            "body": "Open the app to accept or correct the pending food estimate before it counts.",
            "severity": "info",
            "status": "pending_review",
            "safety_critical": False,
        })
    return {"generated_at": (now or datetime.now()).isoformat(timespec="seconds"), "alerts": alerts}


def _push_vapid_public_key():
    return (
        os.environ.get("FITNESS_PUSH_VAPID_PUBLIC_KEY")
        or os.environ.get("VAPID_PUBLIC_KEY")
        or ""
    ).strip()


def _push_vapid_private_key():
    return (
        os.environ.get("FITNESS_PUSH_VAPID_PRIVATE_KEY")
        or os.environ.get("VAPID_PRIVATE_KEY")
        or ""
    ).strip()


def _push_vapid_claims():
    configured = (
        os.environ.get("FITNESS_PUSH_VAPID_SUBJECT")
        or os.environ.get("VAPID_SUBJECT")
        or ""
    ).strip()
    if configured:
        return {"sub": configured}
    base_url = public_base_url()
    if base_url.startswith("https://"):
        return {"sub": base_url}
    subject = (
        "mailto:admin@example.com"
    )
    return {"sub": subject}


def _push_test_payload():
    return {
        "title": "Fitness Dashboard test",
        "body": "Push notifications are working. Scheduled reminders are not enabled yet.",
        "tag": "fitness-dashboard-test",
        "url": public_base_url() or "/",
        "safety_critical": False,
        "sent_at": datetime.now().isoformat(timespec="seconds"),
    }


def _send_web_push(subscription: dict, payload: dict):
    if webpush is None:
        return {
            "ok": False,
            "status": "server_error",
            "status_code": 500,
            "error": "pywebpush dependency is not installed",
        }
    private_key = _push_vapid_private_key()
    if not private_key:
        return {
            "ok": False,
            "status": "server_error",
            "status_code": 500,
            "error": "VAPID private key is not configured",
        }
    try:
        response = webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=private_key,
            vapid_claims=_push_vapid_claims(),
        )
    except Exception as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code in {404, 410}:
            return {"ok": False, "status": "gone", "status_code": status_code, "error": "subscription gone"}
        app.logger.warning(
            "Push delivery failed exception_type=%s status_code=%s",
            type(exc).__name__,
            status_code,
        )
        return {
            "ok": False,
            "status": "server_error",
            "status_code": status_code or 500,
            "error": {
                "code": "push_delivery_failed",
                "message": "Push service could not deliver the test notification",
            },
        }
    return {
        "ok": True,
        "status": "delivered",
        "status_code": getattr(response, "status_code", None) or 201,
    }


@app.route("/api/push/vapid-public-key")
def push_vapid_public_key():
    public_key = _push_vapid_public_key()
    if not public_key:
        return api_error("VAPID public key is not configured", status=404, code="push_vapid_not_configured")
    return jsonify({"public_key": public_key})


@app.route("/api/push/subscriptions", methods=["GET", "POST"])
def push_subscriptions():
    user_id = _current_data_user_id()
    if request.method == "GET":
        include_revoked = (request.args.get("include_revoked") or "").lower() == "true"
        return jsonify({"subscriptions": list_push_subscriptions(user_id, include_revoked=include_revoked)})
    data, err = get_json_body(required=True)
    if err:
        return err
    subscription = data.get("subscription") or data
    metadata = {
        "permission_state": data.get("permission_state"),
        "pwa_installed": data.get("pwa_installed"),
        "user_agent": request.headers.get("User-Agent"),
    }
    try:
        saved = save_push_subscription(user_id, subscription, metadata=metadata)
    except ValueError as exc:
        return api_error(str(exc), status=400, code="invalid_push_subscription")
    return jsonify({"status": "saved", "subscription": saved})


@app.route("/api/push/subscriptions/<endpoint_hash>", methods=["DELETE"])
def push_subscription_revoke(endpoint_hash: str):
    endpoint_hash = (endpoint_hash or "").strip()
    if not re.fullmatch(r"[a-f0-9]{64}", endpoint_hash):
        return api_error("invalid endpoint_hash", status=400, code="invalid_field")
    removed = revoke_push_subscription(_current_data_user_id(), endpoint_hash)
    return jsonify({"status": "revoked" if removed else "not_found", "revoked": removed})


@app.route("/api/push/test", methods=["POST"])
def push_test_notification():
    data, err = get_json_body(required=False)
    if err:
        return err
    endpoint_hash = ((data or {}).get("endpoint_hash") or "").strip() or None
    user_id = _current_data_user_id()
    delivery_target = get_push_subscription_for_delivery(user_id, endpoint_hash=endpoint_hash)
    if not delivery_target:
        return api_error("no active push subscription", status=404, code="push_subscription_not_found")

    payload = _push_test_payload()
    result = _send_web_push(delivery_target["subscription"], payload)
    summary = delivery_target["summary"]
    if result["status"] == "gone":
        revoke_push_subscription(user_id, summary["endpoint_hash"])
        return jsonify({
            "status": "gone",
            "delivered": False,
            "revoked": True,
            "subscription": summary,
            "safety_critical": False,
            "error": result["error"],
        }), 410
    if not result["ok"]:
        return jsonify({
            "status": "server_error",
            "delivered": False,
            "subscription": summary,
            "safety_critical": False,
            "error": result["error"],
        }), result["status_code"]
    return jsonify({
        "status": "delivered",
        "delivered": True,
        "subscription": summary,
        "payload": payload,
        "safety_critical": False,
    })


@app.route("/api/push/reminders/preview")
def push_reminders_preview():
    subscriptions = list_push_subscriptions(_current_data_user_id())
    support_state = "ready" if subscriptions else "no_subscription"
    if any(sub.get("permission_state") == "denied" for sub in subscriptions):
        support_state = "permission_denied"
    elif any(sub.get("pwa_installed") is False for sub in subscriptions):
        support_state = "not_installed"
    if request.args.get("permission") == "denied":
        support_state = "permission_denied"
    elif request.args.get("pwa_installed") == "false":
        support_state = "not_installed"
    return jsonify({
        **_push_alert_preview(),
        "support_state": support_state,
        "subscription_count": len(subscriptions),
        "delivery": "preview_only",
        "safety_critical": False,
    })


def _confidence_level_from(effective_readiness, freshness):
    """Derive 'high' | 'medium' | 'low' from effective readiness + wearable freshness.

    A stale or fully-missing wearable forces 'low' — we can't claim a confident
    recommendation when the underlying signal is gone.
    """
    whoop_freshness = (freshness.get("whoop") or {})
    wearable_states = [
        (freshness.get("oura") or {}).get("status"),
        (freshness.get("apple_health") or {}).get("status"),
    ]
    if _whoop_freshness_is_relevant(whoop_freshness):
        wearable_states.append(whoop_freshness.get("status"))
    wearable_states = [state for state in wearable_states if state is not None]
    if "stale" in wearable_states:
        return "low"
    if wearable_states and all(s == "missing" for s in wearable_states):
        return "low"
    if "missing" in wearable_states or "aging" in wearable_states:
        if effective_readiness is None or effective_readiness < 65:
            return "low"
        return "medium"
    if effective_readiness is None:
        return "low"
    if effective_readiness >= 78:
        return "high"
    if effective_readiness >= 60:
        return "medium"
    return "low"


def _effective_readiness_from(readiness, recovery_bonus):
    if readiness is None:
        return None
    try:
        effective = float(readiness) + float((recovery_bonus or {}).get("bonus_points") or 0)
        return max(0.0, min(100.0, effective))
    except Exception:
        return float(readiness)


def _downgrade_training_recommendation_once(recommendation):
    if recommendation == "intensity":
        return "moderate"
    if recommendation == "moderate":
        return "recovery"
    return recommendation


def _training_recommendation_from_factors(
    readiness,
    recovery_bonus=None,
    hrv_trend="unknown",
    sleep_debt=None,
    acwr_data=None,
    last_completed=None,
    last_hours_ago=None,
    weather=None,
):
    effective_readiness = _effective_readiness_from(readiness, recovery_bonus)
    recommendation = "moderate"
    if effective_readiness is not None:
        if effective_readiness < 70:
            recommendation = "recovery"
        elif effective_readiness > 85:
            recommendation = "intensity"

    if hrv_trend == "declining":
        recommendation = _downgrade_training_recommendation_once(recommendation)

    if ((sleep_debt or {}).get("debt_minutes") or 0) > 300:
        recommendation = _downgrade_training_recommendation_once(recommendation)

    acwr_v = (acwr_data or {}).get("acwr")
    try:
        acwr_f = float(acwr_v)
    except Exception:
        acwr_f = None
    if acwr_f is not None:
        if acwr_f > 1.5:
            recommendation = "recovery"
        elif acwr_f >= 1.3:
            recommendation = _downgrade_training_recommendation_once(recommendation)

    if (
        last_completed
        and last_hours_ago is not None
        and last_hours_ago < 18
        and (last_completed.get("overall_fatigue") or 0) >= 8
    ):
        recommendation = _downgrade_training_recommendation_once(recommendation)

    if (weather or {}).get("available"):
        temp = weather.get("feelslike_f") if weather.get("feelslike_f") is not None else weather.get("temp_f")
        hum = weather.get("humidity_pct")
        if temp is not None:
            if temp >= 95 or (temp >= 90 and (hum or 0) >= 75):
                recommendation = _downgrade_training_recommendation_once(recommendation)
            elif temp <= 40 and recommendation == "intensity":
                recommendation = "moderate"

    return recommendation, effective_readiness


@app.route('/api/recommendation/smart')
def smart_recommendation_api():
    """Smart recommendation factoring Oura readiness + HRV trend + recent soreness."""
    today = datetime.now().strftime("%Y-%m-%d")

    # Oura metrics (best-effort)
    readiness = None
    sleep_score = None
    hrv = None
    try:
        cached = get_oura_daily(OURA_DB_FILE, today)
        if cached:
            readiness = cached.get("readiness_score")
            sleep_score = cached.get("sleep_score")
            hrv = cached.get("hrv")
    except Exception:
        pass

    # HRV trend (best-effort)
    hrv_trend = get_recent_hrv_trend()

    recent = filter_recent_soreness(SORENESS_DATA, hours=24)
    avoid_set = {s.get("muscle") for s in recent if (s.get("soreness_level") or 0) >= 6 and s.get("muscle")}

    # Fold in muscles trained in the last ~18h so the next session avoids
    # reloading them while they're still recovering from the just-completed
    # workout. Two-set minimum filters out cardio-spillover muscle tags.
    last_completed = summarize_recent_completion(WORKOUTS, hours=24)
    recently_trained = []
    last_hours_ago = last_completed.get("hours_ago") if last_completed else None
    if last_completed and last_hours_ago is not None and last_hours_ago < 18:
        for entry in last_completed.get("muscles_trained") or []:
            muscle = entry.get("muscle")
            if muscle and (entry.get("sets") or 0) >= 2:
                avoid_set.add(muscle)
                recently_trained.append({
                    "muscle": muscle,
                    "sets": entry.get("sets"),
                    "hours_ago": last_hours_ago,
                })
    avoid = sorted(avoid_set)

    # Readiness factors (ACWR / sleep debt / recovery bonus)
    acwr_data = calculate_acwr(WORKOUTS)
    sleep_debt = calculate_sleep_debt(OURA_DB_FILE, days=7)
    recovery_bonus = calculate_recovery_bonus(RECOVERY_DATA, hours=48)

    effective_readiness = _effective_readiness_from(readiness, recovery_bonus)

    upper = {"chest", "back", "shoulders", "biceps", "triceps"}
    lower = {"quads", "hamstrings", "glutes", "calves", "adductors"}
    avoid_set = set(avoid)

    # `avoid_set` now mixes sore muscles with recently-trained muscles, so
    # the user-facing copy can't say "due to soreness" categorically.
    avoid_reason = "soreness or recent training" if recently_trained else "soreness"
    if avoid_set & lower and not (avoid_set & upper):
        suggested = f"Upper body focus - avoid leg exercises due to {avoid_reason}"
    elif avoid_set & upper and not (avoid_set & lower):
        suggested = f"Lower body focus - avoid upper-body loading due to {avoid_reason}"
    elif avoid_set:
        descriptor = "trained or sore" if recently_trained else "sore"
        suggested = f"Recovery / light movement - multiple {descriptor} areas"
    else:
        suggested = "Normal training - choose session based on your plan"

    # Reasoning: mention max soreness
    reason_bits = []
    if readiness is not None:
        if effective_readiness is not None and round(float(effective_readiness), 1) != round(float(readiness), 1):
            reason_bits.append(f"Readiness {readiness} (+{recovery_bonus.get('bonus_points', 0)} recovery bonus → {round(effective_readiness, 1)})")
        else:
            reason_bits.append(f"Readiness {readiness}")
    if hrv_trend and hrv_trend != "unknown":
        reason_bits.append(f"HRV trend {hrv_trend}")
    if (sleep_debt.get('debt_minutes') or 0) > 0:
        reason_bits.append(f"Sleep debt {sleep_debt.get('debt_minutes')} min ({sleep_debt.get('status')})")
    if acwr_data.get('chronic_load', 0) > 0:
        reason_bits.append(f"ACWR {acwr_data.get('acwr')} ({acwr_data.get('risk')})")
    hr_intensity = _apple_health_hr_intensity_summary(days=7)
    if hr_intensity.get("applied_count"):
        reason_bits.append(
            "Apple Health workout HR raised recent cardio load "
            f"({round(hr_intensity.get('max_avg_heart_rate'))} bpm -> intensity {hr_intensity.get('max_hr_intensity')})"
        )
    if recent:
        worst = max(recent, key=lambda s: s.get("soreness_level") or 0)
        ts = _parse_soreness_timestamp(worst)
        age_h = None
        if ts:
            age_h = round((datetime.now() - ts).total_seconds() / 3600, 1)
        if age_h is not None:
            reason_bits.append(f"{worst.get('muscle')} soreness {worst.get('soreness_level')} logged {age_h}h ago")
        else:
            reason_bits.append(f"{worst.get('muscle')} soreness {worst.get('soreness_level')}")
    if last_completed and last_hours_ago is not None and last_hours_ago < 24:
        muscle_list = ", ".join(
            entry["muscle"] for entry in (last_completed.get("muscles_trained") or [])[:3]
        ) or "session"
        fatigue_str = (
            f", fatigue {last_completed.get('overall_fatigue')}/10"
            if last_completed.get("overall_fatigue") is not None
            else ""
        )
        reason_bits.append(
            f"Last session {last_hours_ago}h ago: {muscle_list}{fatigue_str}"
        )

    # Weather adjustment (best-effort)
    weather = None
    try:
        weather = _cached_wttr(_WEATHER_CACHE.get("location") or "San_Antonio")
        if weather and weather.get("available"):
            temp = weather.get("feelslike_f") if weather.get("feelslike_f") is not None else weather.get("temp_f")
            hum = weather.get("humidity_pct")
            if temp is not None:
                # Extreme heat: reduce intensity
                if temp >= 95 or (temp >= 90 and (hum or 0) >= 75):
                    reason_bits.append(f"Weather {temp}F feels-like (hot)")
                # Extreme cold: be conservative
                elif temp <= 40:
                    reason_bits.append(f"Weather {temp}F feels-like (cold)")
    except Exception:
        weather = None

    recommendation, effective_readiness = _training_recommendation_from_factors(
        readiness,
        recovery_bonus=recovery_bonus,
        hrv_trend=hrv_trend,
        sleep_debt=sleep_debt,
        acwr_data=acwr_data,
        last_completed=last_completed,
        last_hours_ago=last_hours_ago,
        weather=weather,
    )
    whoop_context = _whoop_recommendation_context(readiness)
    whoop_recommendation = apply_wearable_modifiers(
        recommendation,
        None,
        whoop_signals=whoop_context["signals"],
        source_conflict=whoop_context["source_conflict"],
    )
    recommendation = whoop_recommendation["recommendation"]
    for explanation in whoop_context["signals"].get("explanations") or []:
        reason_bits.append(explanation)
    if whoop_context["source_conflict"].get("explanation"):
        reason_bits.append(whoop_context["source_conflict"]["explanation"])
    open_wearables_facts = _open_wearables_recommendation_facts()
    recommendation, open_wearables_modifier = _apply_open_wearables_recommendation_guard(
        recommendation,
        open_wearables_facts,
    )
    if open_wearables_modifier.get("detail"):
        reason_bits.append(open_wearables_modifier["detail"])

    # Time-of-day hint
    hour = datetime.now().hour
    time_of_day = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    reason_bits.append(f"Time of day: {time_of_day}")

    # Workout history context (simple: last trained major muscle groups)
    history_context = []
    try:
        if WORKOUTS:
            today_d = datetime.now().date()
            last_by_muscle = {}
            for w in sorted(WORKOUTS, key=lambda x: x.get("date", ""), reverse=True):
                d_s = w.get("date")
                if not d_s:
                    continue
                try:
                    d = datetime.strptime(d_s, "%Y-%m-%d").date()
                except Exception:
                    continue
                for ex in w.get("exercises", []) or []:
                    mg = (ex.get("muscle_group") or "").strip().lower()
                    if mg and mg not in last_by_muscle:
                        last_by_muscle[mg] = (today_d - d).days
                if len(last_by_muscle) >= 6:
                    break
            for mg, days_ago in sorted(last_by_muscle.items(), key=lambda kv: kv[1])[:4]:
                history_context.append(f"Last {mg}: {days_ago}d ago")
    except Exception:
        history_context = []

    next_workout = generate_next_workout(
        WORKOUTS,
        SORENESS_DATA,
        training_recommendation=recommendation,
        consume_cardio_rotation=False,
    )
    freshness = _compute_data_freshness()
    food_log_entries = _food_log_entries_for_context(since=today)
    adaptation_food_entries = _food_log_entries_for_context()
    nutrition_context = _nutrition_context_for_date(
        today,
        hard_training_planned=_workout_looks_hard(next_workout),
        food_log_entries=food_log_entries,
    )
    workout_adaptation_events = []
    active_open_raw = str(request.args.get("active_workout_open", "false")).strip().lower()
    active_open_requested = active_open_raw in {"1", "true", "yes"}
    completed_sets_by_exercise = _completed_sets_query_param(request.args.get("completed_sets"))
    fingerprint = _workout_recommendation_fingerprint()
    current_plan = _current_workout_plan_for_fingerprint(fingerprint)
    stored_plan = get_current_workout_plan(_current_data_user_id())
    # A stored plan whose fingerprint doesn't match today's fingerprint is
    # only safe to treat as canonical if it was actually written *today*.
    # A same-day mismatch is process-local cache churn (worker divergence,
    # a weather-cache refresh, etc.) that a genuine same-day swap should
    # survive -- but a stored plan left over from a *prior* day (e.g. a
    # calendar-day rollover, since the fingerprint includes `day`) is
    # stale and must not be resurrected and re-persisted under today's
    # fingerprint (FIT-256 finding 1): doing so would serve yesterday's
    # plan forever, since it would then satisfy every future fingerprint
    # lookup for today. On a cross-day mismatch we fall back to
    # `current_plan` (fingerprint-scoped) / regeneration instead.
    stored_plan_written_today = bool(
        stored_plan and str(stored_plan.get("updated_at") or "")[:10] == today
    )
    canonical_plan = (
        (stored_plan.get("plan") if stored_plan_written_today else None) or current_plan
    )
    if not active_open_requested or completed_sets_by_exercise:
        if canonical_plan and not _is_lightweight_current_workout_plan(canonical_plan):
            canonical_nutrition_context = _nutrition_context_for_date(
                today,
                hard_training_planned=_workout_looks_hard(canonical_plan),
                food_log_entries=food_log_entries,
            )
            adapted_plan, workout_adaptation_events = _apply_due_workout_adaptations_for_plan(
                canonical_plan,
                date_s=today,
                food_log_entries=adaptation_food_entries,
                nutrition_context=canonical_nutrition_context,
                active_workout_open=active_open_requested,
                completed_sets_by_exercise=completed_sets_by_exercise,
            )
            if workout_adaptation_events:
                next_workout = _persist_current_workout_plan(adapted_plan, fingerprint)
                nutrition_context = _nutrition_context_for_date(
                    today,
                    hard_training_planned=_workout_looks_hard(next_workout),
                    food_log_entries=food_log_entries,
                )
        else:
            next_workout, workout_adaptation_events = _apply_due_workout_adaptations_for_plan(
                next_workout,
                date_s=today,
                food_log_entries=adaptation_food_entries,
                nutrition_context=nutrition_context,
                active_workout_open=active_open_requested,
                completed_sets_by_exercise=completed_sets_by_exercise,
            )
            if workout_adaptation_events:
                _persist_current_workout_plan(next_workout, fingerprint)
        if workout_adaptation_events:
            nutrition_context = _nutrition_context_for_date(
                today,
                hard_training_planned=_workout_looks_hard(next_workout),
                food_log_entries=food_log_entries,
            )
    whoop_adjusted = apply_wearable_modifiers(
        recommendation,
        next_workout,
        whoop_signals=whoop_context["signals"],
        source_conflict=whoop_context["source_conflict"],
    )
    next_workout = whoop_adjusted["next_workout"]
    nutrition_context = _nutrition_context_for_date(
        today,
        hard_training_planned=_workout_looks_hard(next_workout),
        food_log_entries=food_log_entries,
    )
    confidence_level = _confidence_level_from(effective_readiness, freshness)
    return jsonify({
        "recommendation": recommendation,
        "readiness": readiness,
        "effective_readiness": round(effective_readiness, 1) if effective_readiness is not None else None,
        "sleep_score": sleep_score,
        "hrv": hrv,
        "hrv_trend": hrv_trend,
        "readiness_factors": {
            "acwr": acwr_data,
            "sleep_debt": sleep_debt,
            "recovery_bonus": recovery_bonus,
        },
        "avoid_muscles": avoid,
        "recently_trained": recently_trained,
        "last_completed": last_completed,
        "suggested_workout": suggested,
        "weather": weather,
        "time_of_day": time_of_day,
        "history_context": history_context,
        "reasoning": "; ".join(reason_bits) if reason_bits else "No Oura/soreness data available",
        "freshness": freshness,
        "wearable_sources": _wearable_sources_payload(freshness, whoop_context),
        "recommendation_sources": {
            **_recommendation_sources_payload(
                freshness,
                whoop_context,
                open_wearables_facts,
                open_wearables_modifier,
                whoop_adjusted["load_source"],
            ),
            "wearable_sources": _wearable_sources_payload(freshness, whoop_context),
        },
        "nutrition_context": nutrition_context,
        "next_workout": _workout_with_auth_scope(next_workout),
        "workout_adaptation_events": [workout_adaptation.project_event(event) for event in workout_adaptation_events],
        "confidence_level": confidence_level,
    })


def _calculate_progressive_overload(workouts):
    exercises = [
        "Chest Press",
        "Lat Pulldown",
        "Mid Row",
        "Leg Press",
        "Leg Curl",
        "Seated Dip",
        "Shoulder Press",
        "Biceps Curl",
    ]
    history_by_ex = {ex: {} for ex in exercises}

    for w in workouts or []:
        date_s = w.get("date")
        if not date_s:
            continue
        for ex in w.get("exercises", []) or []:
            name = ex.get("machine") or ex.get("exercise")
            if name not in history_by_ex:
                continue
            top_set = None
            for s in ex.get("sets", []) or []:
                weight = s.get("weight_lbs")
                if weight is None:
                    continue
                if top_set is None or weight > top_set:
                    top_set = weight
            if top_set is None:
                continue
            prev = history_by_ex[name].get(date_s)
            if prev is None or top_set > prev:
                history_by_ex[name][date_s] = top_set

    results = []
    for ex in exercises:
        items = [
            {"date": d, "weight": w}
            for d, w in sorted(history_by_ex[ex].items(), key=lambda x: x[0])
        ]
        last_weight = items[-1]["weight"] if items else None
        prev_weight = items[-2]["weight"] if len(items) > 1 else None
        change = None
        change_dir = "flat"
        if last_weight is not None and prev_weight is not None:
            change = round(float(last_weight) - float(prev_weight), 1)
            if change > 0:
                change_dir = "up"
            elif change < 0:
                change_dir = "down"
            else:
                change_dir = "flat"
        trend_weights = [str(int(round(i["weight"]))) for i in items[-4:]]
        results.append({
            "exercise": ex,
            "last_weight": last_weight,
            "previous_weight": prev_weight,
            "change_lbs": change,
            "change_dir": change_dir,
            "trend": "→".join(trend_weights) if trend_weights else "",
            "history": items,
        })
    return results


@app.route('/api/progressive-overload')
def progressive_overload():
    """Return progressive overload data per major exercise."""
    return jsonify({"exercises": _calculate_progressive_overload(WORKOUTS)})


@app.route('/api/history')
def workout_history():
    """Get past workouts with full details."""
    workouts_list = []
    for w in sorted(WORKOUTS, key=lambda x: x["date"], reverse=True):
        total_sets = sum(len(e.get("sets", [])) for e in w.get("exercises", []))
        total_volume = sum(
            s["weight_lbs"] * s["reps"]
            for e in w.get("exercises", [])
            for s in e.get("sets", [])
        )
        history_row = {
            "date": w["date"],
            "session_type": w.get("session_type", "general"),
            "duration_minutes": w.get("duration_minutes", 0),
            "exercises": w.get("exercises", []),
            "total_sets": total_sets,
            "total_volume": round(total_volume),
            "notes": w.get("notes", ""),
            # FIT-14: expose adherence so the detail modal can label
            # exercises as planned vs added vs skipped, and surface the
            # adherence summary inline.
            "adherence": w.get("adherence", {"followed": True, "skipped": [], "modified": [], "added": []}),
        }
        workouts_list.append(normalize_history_item(history_row, source="lifted"))
    return jsonify({"workouts": workouts_list, "count": len(workouts_list)})


@app.route('/api/history-all')
def all_history():
    """Get all history including workouts, cardio, and recovery sessions."""
    # Process workouts
    workouts_list = []
    for w in sorted(WORKOUTS, key=lambda x: x.get("date", ""), reverse=True):
        total_sets = sum(len(e.get("sets", [])) for e in w.get("exercises", []))
        total_volume = sum(
            s.get("weight_lbs", 0) * s.get("reps", 0)
            for e in w.get("exercises", [])
            for s in e.get("sets", [])
        )
        history_row = {
            "id": w.get("id"),
            "created_at": w.get("created_at"),
            "date": w.get("date", ""),
            "session_type": w.get("session_type", "general"),
            "duration_minutes": w.get("duration_minutes", 0),
            "exercises": w.get("exercises", []),
            "total_sets": total_sets,
            "total_volume": round(total_volume),
            "notes": w.get("notes", ""),
            # FIT-14: see /api/history.
            "adherence": w.get("adherence", {"followed": True, "skipped": [], "modified": [], "added": []}),
        }
        workouts_list.append(normalize_history_item(history_row, source="lifted"))

    # Process cardio (sorted by date descending)
    cardio_list = sorted(CARDIO_DATA, key=lambda x: x.get("date", ""), reverse=True)

    # Process recovery (sorted by date descending)
    recovery_list = sorted(RECOVERY_DATA, key=lambda x: x.get("date", ""), reverse=True)

    prs = calculate_personal_records(WORKOUTS)

    return jsonify({
        "workouts": workouts_list,
        "cardio": cardio_list,
        "recovery": recovery_list,
        "personal_records": prs
    })


@app.route('/api/delete-history', methods=['POST'])
def delete_history():
    """Delete a history entry by type and index (index is in *sorted* order)."""
    data, err = get_json_body(required=True)
    if err:
        return err

    entry_type, err2 = _coerce_str(data.get("type"), "type", required=True, max_len=16)
    if err2:
        return err2
    index, err2 = _coerce_int(data.get("index"), "index", min_v=0, max_v=10_000)
    if err2:
        return err2

    try:
        if entry_type == "workout":
            sorted_workouts = sorted(enumerate(WORKOUTS), key=lambda x: x[1].get("date", ""), reverse=True)
            if not (0 <= index < len(sorted_workouts)):
                return api_error("Index out of range", 404, code="not_found")
            original_index = sorted_workouts[index][0]
            deleted = WORKOUTS.pop(original_index)
            save_json(WORKOUTS_FILE, WORKOUTS)
            return jsonify({"status": "success", "deleted": deleted})

        if entry_type == "cardio":
            sorted_cardio = sorted(enumerate(CARDIO_DATA), key=lambda x: x[1].get("date", ""), reverse=True)
            if not (0 <= index < len(sorted_cardio)):
                return api_error("Index out of range", 404, code="not_found")
            original_index = sorted_cardio[index][0]
            deleted = CARDIO_DATA.pop(original_index)
            save_json(CARDIO_FILE, CARDIO_DATA)
            return jsonify({"status": "success", "deleted": deleted})

        if entry_type == "recovery":
            sorted_recovery = sorted(enumerate(RECOVERY_DATA), key=lambda x: x[1].get("date", ""), reverse=True)
            if not (0 <= index < len(sorted_recovery)):
                return api_error("Index out of range", 404, code="not_found")
            original_index = sorted_recovery[index][0]
            deleted = RECOVERY_DATA.pop(original_index)
            save_json(RECOVERY_FILE, RECOVERY_DATA)
            return jsonify({"status": "success", "deleted": deleted})

        return api_error("Invalid type (expected workout|cardio|recovery)", 400, code="invalid_field")
    except Exception as e:
        return api_error("Failed to delete history entry", 500, code="server_error", details=str(e))


@app.route('/api/restore-history', methods=['POST'])
def restore_history():
    """Restore a previously deleted history entry by appending the original payload back.

    The client passes the same `deleted` object returned by /api/delete-history.
    Trusts the payload because it just round-tripped from our own store; minimal
    sanity checks guard against malformed input.
    """
    data, err = get_json_body(required=True)
    if err:
        return err

    entry_type, err2 = _coerce_str(data.get("type"), "type", required=True, max_len=16)
    if err2:
        return err2
    entry = data.get("entry")
    if not isinstance(entry, dict):
        return api_error("entry must be an object", 400, code="invalid_field")
    if not isinstance(entry.get("date"), str) or not entry["date"]:
        return api_error("entry.date is required", 400, code="invalid_field")

    try:
        if entry_type == "workout":
            WORKOUTS.append(entry)
            save_json(WORKOUTS_FILE, WORKOUTS)
            return jsonify({"status": "success", "restored": entry})
        if entry_type == "cardio":
            CARDIO_DATA.append(entry)
            save_json(CARDIO_FILE, CARDIO_DATA)
            return jsonify({"status": "success", "restored": entry})
        if entry_type == "recovery":
            RECOVERY_DATA.append(entry)
            save_json(RECOVERY_FILE, RECOVERY_DATA)
            return jsonify({"status": "success", "restored": entry})
        return api_error("Invalid type (expected workout|cardio|recovery)", 400, code="invalid_field")
    except Exception as e:
        return api_error("Failed to restore history entry", 500, code="server_error", details=str(e))


def _workout_sync_error(message: str, status: int = 400, code: str = "bad_request", details=None):
    payload_details = {"sync_status": "rejected"}
    if isinstance(details, dict):
        payload_details.update(details)
    elif details is not None:
        payload_details["detail"] = details
    return api_error(message, status, code=code, details=payload_details)


def _workout_sync_error_from_api_error(error_response):
    try:
        response, status = error_response
        payload = response.get_json(silent=True) or {}
        error = payload.get("error") or {}
        return _workout_sync_error(
            error.get("message") or "Invalid workout sync payload",
            status=status,
            code=error.get("code") or "invalid_field",
            details=error.get("details"),
        )
    except Exception:
        return error_response


def _workout_sync_fingerprint(workout_entry: dict) -> str:
    comparable = {
        "id": workout_entry.get("id"),
        "date": workout_entry.get("date"),
        "session_type": workout_entry.get("session_type"),
        "duration_minutes": workout_entry.get("duration_minutes"),
        "exercises": workout_entry.get("exercises") or [],
        "total_sets": workout_entry.get("total_sets"),
        "total_volume": workout_entry.get("total_volume"),
        "overall_fatigue": workout_entry.get("overall_fatigue"),
        "notes": workout_entry.get("notes") or "",
        "cardio": workout_entry.get("cardio"),
        "recommendation_id": workout_entry.get("recommendation_id"),
    }
    body = json.dumps(comparable, sort_keys=True, separators=(",", ":"), default=str)
    import hashlib
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _workout_sync_metadata(data: dict, workout_id: str, fingerprint: str, received_at: str) -> dict:
    source = data.get("sync_source") or data.get("source")
    if not isinstance(source, str) or not source.strip():
        source = "offline_queue" if data.get("offline") or data.get("client_workout_id") or data.get("client_id") else "online"
    metadata = {
        "version": 1,
        "client_workout_id": workout_id,
        "sync_status": "inserted",
        "last_result": "inserted",
        "source": source.strip()[:64],
        "received_at": received_at,
        "last_attempt_at": received_at,
        "sync_attempts": 1,
        "fingerprint": fingerprint,
    }
    for field in ("client_created_at", "client_updated_at", "attempt_id"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            metadata[field] = value.strip()[:128]
    return metadata


def _workout_already_synced_response(existing: dict, client_workout_id: str, received_at: str | None = None, fingerprint: str | None = None):
    if received_at:
        meta = existing.setdefault("offline_sync", {})
        meta["last_result"] = "already_synced"
        meta["last_attempt_at"] = received_at
        meta["sync_attempts"] = int(meta.get("sync_attempts") or 1) + 1
        if fingerprint:
            meta["fingerprint"] = fingerprint
        save_json(WORKOUTS_FILE, WORKOUTS)
    return jsonify({
        "status": "success",
        "sync_status": "already_synced",
        "adherence": existing.get("adherence", {"followed": True, "skipped": [], "modified": [], "added": []}),
        "workout_id": client_workout_id,
        "duplicate": True,
        "message": "Workout already logged. Using existing workout ID."
    })


@app.route('/api/complete-workout', methods=['POST'])
def complete_workout():
    """Complete a workout and track adherence to recommendations."""
    global LAST_WORKOUT_RECOMMENDATION
    data, err = get_json_body(required=True)
    if err:
        return err

    client_workout_id, err2 = _coerce_str(
        data.get("client_workout_id") or data.get("client_id") or data.get("id"),
        "id",
        required=False,
        max_len=80,
    )
    if err2:
        return _workout_sync_error_from_api_error(err2)
    existing_by_client_id = None
    if client_workout_id:
        allowed_id_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:.")
        if any(ch not in allowed_id_chars for ch in client_workout_id):
            return _workout_sync_error("id contains unsupported characters", 400, code="invalid_field")
        existing_by_client_id = next((w for w in WORKOUTS if w.get("id") == client_workout_id), None)
        if existing_by_client_id and "exercises" not in data:
            return _workout_already_synced_response(existing_by_client_id, client_workout_id)

    recommendation_id = data.get("recommendation_id")
    actual_exercises = data.get("exercises", [])
    if not isinstance(actual_exercises, list):
        return _workout_sync_error("exercises must be a list", 400, code="invalid_field")

    # Validate we have at least one completed exercise with at least 1 set
    if len(actual_exercises) == 0:
        return _workout_sync_error("Workout must include at least one exercise", 400, code="invalid_field")

    for ex_idx, ex in enumerate(actual_exercises):
        if not isinstance(ex, dict):
            return _workout_sync_error("Each exercise must be an object", 400, code="invalid_field")
        if not ex.get("machine"):
            return _workout_sync_error("Each exercise must include machine", 400, code="invalid_field")
        sets = ex.get("sets") or []
        if not isinstance(sets, list) or len(sets) == 0:
            return _workout_sync_error("Each exercise must include at least one set", 400, code="invalid_field")
        for set_idx, set_row in enumerate(sets):
            if not isinstance(set_row, dict):
                return _workout_sync_error("Each set must be an object", 400, code="invalid_field")
            set_notes, err2 = _coerce_str(
                set_row.get("notes", ""),
                f"exercises[{ex_idx}].sets[{set_idx}].notes",
                required=False,
                max_len=500,
            )
            if err2:
                return _workout_sync_error_from_api_error(err2)
            if set_notes:
                set_row["notes"] = set_notes
            else:
                set_row.pop("notes", None)
            if not set_row.get("set_number"):
                set_row["set_number"] = set_idx + 1

    notes, err2 = _coerce_str(data.get("notes", ""), "notes", required=False, max_len=2000)
    if err2:
        return _workout_sync_error_from_api_error(err2)

    cardio_payload = data.get("cardio")
    cardio_actual = None
    cardio_log_entry = None

    def _int_or_default(value, default=0):
        if value in (None, ""):
            return default
        try:
            return int(float(value))
        except Exception:
            return default

    if isinstance(cardio_payload, dict):
        cardio_rec = cardio_payload.get("recommendation")
        if not isinstance(cardio_rec, dict):
            cardio_rec = {}
        cardio_notes, err2 = _coerce_str(cardio_payload.get("notes", ""), "cardio.notes", required=False, max_len=2000)
        if err2:
            return _workout_sync_error_from_api_error(err2)
        activity_type, err2 = _coerce_str(
            cardio_payload.get("activity_type") or cardio_rec.get("type") or cardio_rec.get("machine") or "Cardio",
            "cardio.activity_type",
            required=False,
            max_len=64,
        )
        if err2:
            return _workout_sync_error_from_api_error(err2)
        duration_minutes = max(0, min(_int_or_default(cardio_payload.get("duration_minutes") or cardio_rec.get("duration_minutes"), 0), 600))
        completed = bool(cardio_payload.get("completed"))
        cardio_actual = {
            "completed": completed,
            "activity_type": activity_type or "Cardio",
            "duration_minutes": duration_minutes,
            "notes": cardio_notes,
            "recommendation": {
                "type": cardio_rec.get("type"),
                "duration_minutes": cardio_rec.get("duration_minutes"),
                "zone": cardio_rec.get("zone"),
                "heart_rate_range": cardio_rec.get("heart_rate_range"),
                "intensity": cardio_rec.get("intensity"),
                "reason": cardio_rec.get("reason"),
            },
        }
        if completed and duration_minutes > 0:
            cardio_log_entry = {
                "date": data.get("date") or datetime.now().strftime("%Y-%m-%d"),
                "activity_type": activity_type or "Cardio",
                "duration_minutes": duration_minutes,
                "avg_heart_rate": _int_or_default(cardio_payload.get("avg_heart_rate"), None),
                "intensity": max(1, min(_int_or_default(cardio_payload.get("intensity"), 5), 10)),
                "notes": cardio_notes,
                "created_at": datetime.now().isoformat(),
                "source": "completed_workout",
            }

    # Find the recommendation. Live current-plan resolution must stay scoped to
    # the current data user because the production process is shared.
    #
    # FIT-256 follow-up: this used to also check the legacy `WORKOUT_RECOMMENDATIONS`
    # history list by id first, but that list is only ever populated when
    # generate_next_workout(..., persist=True) runs, and nothing in the app calls
    # it with persist=True -- the list is always empty. That branch matched
    # `rec` directly and bypassed the wearable-display transform below, so if it
    # were ever fed data it would misscore adherence for a deload-compliant user
    # (comparing completed sets against the un-clamped base plan). Removed as
    # dead code rather than left as a latent trap.
    recommendation = None
    if recommendation_id:
        current_plan = _current_workout_plan_for_fingerprint(
            _workout_recommendation_fingerprint(),
            allow_stale_unsaved=True,
        )
        if current_plan and current_plan.get("id") == recommendation_id:
            # Score adherence against the wearable-adjusted view the user
            # actually saw (FIT-256 finding 2 follow-up): the canonical plan
            # is the base plan, but /api/next-workout and /gym-now render the
            # deload/caution-clamped sets. Comparing completed sets against
            # the un-clamped base would falsely flag a deload-compliant user
            # for "missed sets". Display-only; nothing is persisted here.
            recommendation = _wearable_display_adjusted_plan(current_plan)

    # Calculate adherence: default `followed: True` only when no plan was
    # supposed to be followed (no `recommendation_id`). When a plan was named
    # but we cannot resolve it, mark `followed: None` so the adherence
    # endpoint doesn't falsely credit the user with following an unknown plan.
    if recommendation_id and not recommendation:
        adherence = {"followed": None, "skipped": [], "modified": [], "added": []}
    else:
        adherence = {"followed": True, "skipped": [], "modified": [], "added": []}
    planned_targets_by_machine = {}
    for act_ex in actual_exercises:
        planned_targets = _planned_targets_from_exercise(act_ex)
        if planned_targets:
            planned_targets_by_machine[act_ex["machine"]] = planned_targets
    if recommendation:
        recommended_exercises = {e["exercise"] for e in recommendation.get("exercises", [])}
        actual_exercise_names = {e["machine"] for e in actual_exercises}

        adherence["skipped"] = list(recommended_exercises - actual_exercise_names)
        adherence["added"] = list(actual_exercise_names - recommended_exercises)
        adherence["followed"] = len(adherence["skipped"]) == 0

        # Track modifications (different weight/reps than recommended)
        for rec_ex in recommendation.get("exercises", []):
            for act_ex in actual_exercises:
                if rec_ex["exercise"] == act_ex["machine"]:
                    if act_ex.get("sets"):
                        completed_sets = _completed_set_rows(act_ex.get("sets"))
                        planned_targets = _planned_targets_from_exercise(rec_ex)
                        if planned_targets:
                            planned_targets_by_machine[act_ex["machine"]] = planned_targets
                        target_weight = planned_targets.get("planned_target_weight")
                        target_reps = planned_targets.get("planned_target_reps")
                        target_sets = planned_targets.get("planned_target_sets")

                        actual_weights = [
                            _positive_number(s.get("weight_lbs"))
                            for s in completed_sets
                        ]
                        actual_reps = [
                            _nonnegative_number(s.get("reps"))
                            for s in completed_sets
                        ]
                        actual_weights = [w for w in actual_weights if w is not None]
                        actual_reps = [r for r in actual_reps if r is not None]
                        actual_weight = max(actual_weights) if actual_weights else 0
                        actual_min_reps = min(actual_reps) if actual_reps else 0
                        missed_reps = target_reps is not None and actual_min_reps < target_reps
                        missed_sets = target_sets is not None and len(completed_sets) < int(round(target_sets))
                        changed_weight = target_weight is not None and abs(actual_weight - target_weight) > 10
                        if changed_weight or missed_reps or missed_sets:
                            change = {
                                "exercise": rec_ex["exercise"],
                                "actual_weight": actual_weight,
                                "actual_min_reps": actual_min_reps,
                                "actual_sets": len(completed_sets),
                            }
                            if target_weight is not None:
                                change["recommended_weight"] = target_weight
                            if target_reps is not None:
                                change["recommended_reps"] = target_reps
                            if target_sets is not None:
                                change["recommended_sets"] = target_sets
                            reasons = []
                            if changed_weight:
                                reasons.append("weight changed")
                            if missed_reps:
                                reasons.append("missed reps")
                            if missed_sets:
                                reasons.append("missed sets")
                            change["reason"] = ", ".join(reasons)
                            adherence["modified"].append(change)
        if adherence["modified"]:
            adherence["followed"] = False

    # Create workout entry
    # Coerce a few fields (best-effort, keep app resilient)
    session_type = data.get("session_type", "general")
    if not isinstance(session_type, str) or not session_type.strip():
        session_type = "general"

    duration_minutes = data.get("duration_minutes", 45)
    try:
        duration_minutes = int(duration_minutes)
    except Exception:
        duration_minutes = 45
    duration_minutes = max(0, min(duration_minutes, 600))
    if cardio_actual and cardio_actual.get("completed"):
        duration_minutes = min(600, duration_minutes + int(cardio_actual.get("duration_minutes") or 0))

    fatigue_provided = "fatigue" in data and data.get("fatigue") not in (None, "")
    if fatigue_provided:
        overall_fatigue = data.get("fatigue")
        try:
            overall_fatigue = int(overall_fatigue)
        except Exception:
            overall_fatigue = 5
        overall_fatigue = max(1, min(overall_fatigue, 10))
    else:
        overall_fatigue = None

    # Auto-fill muscle_group from machine name if missing
    _MACHINE_TO_MUSCLE = {
        "Chest Press": "chest", "Lat Pulldown": "back", "Mid Row": "back",
        "Shoulder Press": "shoulders", "Deltoid Fly": "shoulders", "Machine Deltoid Raise": "shoulders", "Rear Delt Fly": "shoulders",
        "Leg Press": "quads", "Seated Leg Press": "quads", "Leg Extension": "quads",
        "Leg Curl": "hamstrings", "Seated Leg Curl": "hamstrings",
        "Seated Dip": "triceps", "Tricep Pushdown": "triceps",
        "Biceps Curl": "biceps", "Calf Raise": "calves",
        "Pec Fly": "chest", "Chest Fly": "chest",
        "Crunch Machine": "core", "Hip Abductor": "glutes", "Hip Adductor": "adductors",
        "Back Extension": "back", "Low Back": "back",
        "Cable Row": "back", "Seated Cable Row": "back",
    }
    for ex in actual_exercises:
        if not ex.get("muscle_group") or ex["muscle_group"] == "unknown":
            machine = ex.get("machine", "")
            exercise_def = _resolve_exercise_definition(machine)
            ex["muscle_group"] = _MACHINE_TO_MUSCLE.get(
                machine,
                (exercise_def or {}).get("muscle", "unknown"),
            )

    total_sets = sum(len(e.get("sets", [])) for e in actual_exercises)
    total_volume = sum(
        float(s.get("weight_lbs") or 0) * float(s.get("reps") or 0)
        for e in actual_exercises
        for s in (e.get("sets") or [])
    )

    if client_workout_id:
        workout_id = client_workout_id
    else:
        import uuid as _uuid
        workout_id = _uuid.uuid4().hex[:12]
    workout_entry = {
        "id": workout_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "date": data.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "session_type": session_type,
        "duration_minutes": duration_minutes,
        "exercises": actual_exercises,
        "total_sets": total_sets,
        "total_volume": round(total_volume),
        "overall_fatigue": overall_fatigue,
        "notes": notes,
        "cardio": cardio_actual,
        "recommendation_id": recommendation_id,
        "adherence": adherence
    }
    sync_fingerprint = _workout_sync_fingerprint(workout_entry)
    received_at = datetime.now().isoformat(timespec="seconds")
    workout_entry["offline_sync"] = _workout_sync_metadata(
        data,
        workout_id,
        sync_fingerprint,
        received_at,
    )

    if existing_by_client_id:
        existing_fingerprint = (existing_by_client_id.get("offline_sync") or {}).get("fingerprint")
        existing_fingerprint = existing_fingerprint or _workout_sync_fingerprint(existing_by_client_id)
        accepted_fingerprints = {sync_fingerprint}
        if not fatigue_provided and existing_by_client_id.get("overall_fatigue") == 5:
            legacy_entry = dict(workout_entry)
            legacy_entry["overall_fatigue"] = 5
            accepted_fingerprints.add(_workout_sync_fingerprint(legacy_entry))
        if existing_fingerprint in accepted_fingerprints:
            return _workout_already_synced_response(
                existing_by_client_id,
                client_workout_id,
                received_at=received_at,
                fingerprint=existing_fingerprint,
            )
        meta = existing_by_client_id.setdefault("offline_sync", {})
        meta["last_result"] = "conflicted"
        meta["last_attempt_at"] = received_at
        meta["sync_attempts"] = int(meta.get("sync_attempts") or 1) + 1
        meta["last_conflict_at"] = received_at
        meta["conflict_count"] = int(meta.get("conflict_count") or 0) + 1
        meta["last_conflict_fingerprint"] = sync_fingerprint
        meta.setdefault("fingerprint", existing_fingerprint)
        save_json(WORKOUTS_FILE, WORKOUTS)
        return api_error(
            "Workout sync conflict for existing client workout ID",
            409,
            code="sync_conflict",
            details={
                "sync_status": "conflicted",
                "workout_id": client_workout_id,
                "existing_fingerprint": existing_fingerprint,
                "incoming_fingerprint": sync_fingerprint,
            },
        )

    if planned_targets_by_machine:
        for exercise in workout_entry.get("exercises") or []:
            planned_targets = planned_targets_by_machine.get(exercise.get("machine"))
            if planned_targets:
                exercise.update(planned_targets)

    WORKOUTS.append(workout_entry)
    COMPLETED_WORKOUTS.append(workout_entry)
    save_json(WORKOUTS_FILE, WORKOUTS)  # Persist to file
    if cardio_log_entry:
        cardio_log_entry["workout_id"] = workout_id
        CARDIO_DATA.append(cardio_log_entry)
        save_json(CARDIO_FILE, CARDIO_DATA)
    # Drop the cached plan so the next swap/adjust/recommendation regenerates
    # against the freshly-completed session, not the one we just executed.
    LAST_WORKOUT_RECOMMENDATION = None
    delete_current_workout_plan(_current_data_user_id())
    _notify_workout_logged(workout_entry)

    return jsonify({
        "status": "success",
        "sync_status": "inserted",
        "adherence": adherence,
        "workout_id": workout_id,
        "message": "Workout logged! Navigating to history..."
    })


@app.route('/api/export-backup')
def export_backup():
    """Export all data as a single JSON backup file."""
    user_id = _current_data_user_id()
    backup_data = {
        "version": "1.1",
        "exported_at": datetime.now().isoformat(),
        "data": {
            "workouts": WORKOUTS,
            "soreness": SORENESS_DATA,
            "cardio": CARDIO_DATA,
            "recovery": RECOVERY_DATA,
            "settings": USER_SETTINGS,
            "baselines": BASELINES_DATA,
            "body": BODY_DATA,
            "sleep": SLEEP_DATA,
            "nutrition": NUTRITION_DATA,
            "food_logs": get_food_logs(user_id),
            "meal_acceptance_events": list_meal_acceptance_events(user_id),
            "meal_review_snapshots": list_meal_review_snapshots(user_id),
            "personal_vocab": list_personal_vocab_entries(user_id),
            "whoop_daily_facts": list_whoop_daily_facts(WHOOP_DB_FILE, limit=366),
        }
    }

    filename = f"fitness_backup_{datetime.now().strftime('%Y-%m-%d')}.json"

    return Response(
        json.dumps(backup_data, indent=2, default=str),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


def _validated_whoop_backup_records(facts):
    if not isinstance(facts, list):
        raise ValueError("whoop_daily_facts must be a list")
    material_word = "token"
    client_material_word = "secret"
    blocked_fields = {
        "access_" + material_word,
        "refresh_" + material_word,
        material_word + "_ref",
        "material_ref",
        "raw",
        "raw_json",
        "payload",
        "provider_payload",
        "client_" + client_material_word,
    }
    records = []
    for fact in facts:
        if not isinstance(fact, dict):
            raise ValueError("whoop_daily_facts entries must be objects")
        present_forbidden = blocked_fields.intersection(fact.keys())
        if present_forbidden:
            raise ValueError(f"WHOOP backup fact includes forbidden field: {sorted(present_forbidden)[0]}")
        local_date = str(fact.get("local_date") or "").strip()
        if not local_date:
            continue
        record = {
            "upstream_id": f"backup-{local_date}",
            "local_date": _validate_imported_whoop_local_date(local_date),
            "score_state": fact.get("score_state"),
            "recovery_score": fact.get("recovery_score"),
            "recovery_band": fact.get("recovery_band"),
            "strain": fact.get("strain"),
            "sleep_performance_pct": fact.get("sleep_performance_pct"),
            "sleep_need_gap_min": fact.get("sleep_need_gap_min"),
            "workout_kj": fact.get("workout_kj"),
            "hrv_rmssd": fact.get("hrv_rmssd"),
            "resting_hr": fact.get("resting_hr"),
            "respiratory_rate": fact.get("respiratory_rate"),
            "spo2": fact.get("spo2"),
            "skin_temp": fact.get("skin_temp"),
            "percent_recorded": fact.get("percent_recorded"),
        }
        _validate_whoop_metric_bounds(record)
        records.append(record)
    return records


@app.route('/api/import-backup', methods=['POST'])
def import_backup():
    """Import data from a backup JSON file."""
    global WORKOUTS, SORENESS_DATA, CARDIO_DATA, RECOVERY_DATA, USER_SETTINGS, BASELINES_DATA, BODY_DATA, SLEEP_DATA, NUTRITION_DATA

    try:
        backup_data, err = get_json_body(required=True)
        if err:
            return err

        # Validate backup structure
        if "data" not in backup_data or not isinstance(backup_data.get("data"), dict):
            return api_error("Invalid backup format: missing 'data' object", 400, code="invalid_field")

        data = backup_data["data"]
        whoop_backup_records = []
        whoop_restore_guard = None
        if "whoop_daily_facts" in data:
            whoop_backup_records = _validated_whoop_backup_records(data["whoop_daily_facts"])
            whoop_restore_guard = _acquire_whoop_mutation_guard()
            if whoop_restore_guard is None:
                return api_error("WHOOP sync is already running.", 409, code="whoop_sync_in_progress")

        # Restore each JSON-backed data type under one lock so readers never see
        # a clear/extend half-state between the in-memory update and disk write.
        with JSON_DATA_LOCK:
            if "workouts" in data:
                _json_restore_sequence(WORKOUTS, data["workouts"])
                save_json(WORKOUTS_FILE, WORKOUTS)

            if "soreness" in data:
                _json_restore_sequence(SORENESS_DATA, data["soreness"])
                save_json(SORENESS_FILE, SORENESS_DATA)

            if "cardio" in data:
                _json_restore_sequence(CARDIO_DATA, data["cardio"])
                save_json(CARDIO_FILE, CARDIO_DATA)

            if "recovery" in data:
                _json_restore_sequence(RECOVERY_DATA, data["recovery"])
                save_json(RECOVERY_FILE, RECOVERY_DATA)

            if "settings" in data:
                _json_restore_sequence(USER_SETTINGS, _settings_with_defaults(data["settings"]))
                save_json(SETTINGS_FILE, USER_SETTINGS)

            if "baselines" in data:
                _json_restore_sequence(BASELINES_DATA, data["baselines"])
                save_json(BASELINES_FILE, BASELINES_DATA)

            if "body" in data:
                _json_restore_sequence(BODY_DATA, data["body"])
                save_json(BODY_FILE, BODY_DATA)

            if "sleep" in data:
                _json_restore_sequence(SLEEP_DATA, data["sleep"])
                save_json(SLEEP_FILE, SLEEP_DATA)

            if "nutrition" in data:
                _json_restore_sequence(NUTRITION_DATA, data["nutrition"])
                save_json(NUTRITION_FILE, NUTRITION_DATA)

        if "food_logs" in data:
            user_id = _current_data_user_id()
            for food_log in data["food_logs"]:
                if isinstance(food_log, dict):
                    add_food_log(user_id, _food_log_import_record(food_log))

        if "personal_vocab" in data:
            user_id = _current_data_user_id()
            for vocab_entry in data["personal_vocab"]:
                if isinstance(vocab_entry, dict):
                    import_personal_vocab_entry(user_id, vocab_entry)

        if "meal_acceptance_events" in data:
            user_id = _current_data_user_id()
            for meal_event in data["meal_acceptance_events"]:
                if isinstance(meal_event, dict):
                    import_meal_acceptance_event(user_id, meal_event)

        if "meal_review_snapshots" in data:
            user_id = _current_data_user_id()
            for meal_snapshot in data["meal_review_snapshots"]:
                if isinstance(meal_snapshot, dict):
                    import_meal_review_snapshot(user_id, meal_snapshot)

        if "whoop_daily_facts" in data:
            clear_whoop_data(WHOOP_DB_FILE)
            if whoop_backup_records:
                run_id = record_whoop_sync_run(WHOOP_DB_FILE, reason="backup_import")
                upsert_whoop_records(WHOOP_DB_FILE, "recovery", whoop_backup_records, sync_run_id=run_id)
                project_whoop_daily_facts(WHOOP_DB_FILE)
                finish_whoop_sync_run(WHOOP_DB_FILE, run_id, status="success", records_upserted=len(whoop_backup_records))

        if whoop_restore_guard is not None:
            whoop_restore_guard.release()
            whoop_restore_guard = None

        return jsonify({
            "status": "success",
            "message": "Backup restored successfully",
            "imported": {
                "workouts": len(data.get("workouts", [])),
                "soreness": len(data.get("soreness", [])),
                "cardio": len(data.get("cardio", [])),
                "recovery": len(data.get("recovery", [])),
                "settings": bool(data.get("settings")),
                "baselines": len(data.get("baselines", {})),
                "body": len(data.get("body", [])),
                "sleep": len(data.get("sleep", [])),
                "nutrition": len(data.get("nutrition", [])),
                "food_logs": len(data.get("food_logs", [])),
                "meal_acceptance_events": len(data.get("meal_acceptance_events", [])),
                "meal_review_snapshots": len(data.get("meal_review_snapshots", [])),
                "personal_vocab": len(data.get("personal_vocab", [])),
                "whoop_daily_facts": len(data.get("whoop_daily_facts", [])),
            }
        })
    except Exception as e:
        whoop_restore_guard = locals().get("whoop_restore_guard")
        if whoop_restore_guard is not None:
            whoop_restore_guard.release()
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/export-md')
def export_markdown():
    """Export all workouts to markdown format."""
    lines = ["# Workout History Export", ""]
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append(f"*Total Sessions: {len(WORKOUTS)}*")
    lines.append("")

    # Summary stats
    summary = calculate_workout_summary_stats(WORKOUTS)
    if summary:
        lines.append("## Summary")
        lines.append(f"- **Date Range:** {summary.get('date_range', 'N/A')}")
        lines.append(f"- **Total Sets:** {summary.get('total_sets', 0)}")
        lines.append(f"- **Total Volume:** {summary.get('total_volume', 0):,} lbs")
        lines.append("")

    lines.append("## Workout Log")
    lines.append("")
    lines.append("| Date | Machine | Set | Reps | Weight | Volume | Notes |")
    lines.append("|------|---------|-----|------|--------|--------|-------|")

    for workout in sorted(WORKOUTS, key=lambda x: x["date"]):
        for exercise in workout.get("exercises", []):
            machine = exercise["machine"]
            for idx, s in enumerate(exercise.get("sets", [])):
                volume = s["weight_lbs"] * s["reps"]
                notes = s.get("notes", "")
                set_number = s.get("set_number") or idx + 1
                lines.append(f"| {workout['date']} | {machine} | {set_number} | {s['reps']} | {s['weight_lbs']} | {volume} | {notes} |")

    lines.append("")
    lines.append("---")
    lines.append("*Exported from Fitness Intelligence Dashboard*")

    md_content = "\n".join(lines)

    return Response(
        md_content,
        mimetype="text/markdown",
        headers={"Content-Disposition": "attachment;filename=workout_export.md"}
    )


@app.route('/api/insights')
def key_insights():
    """Generate key training insights for graphs tab."""
    progression = calculate_progression_status(WORKOUTS)
    prs = calculate_personal_records(WORKOUTS)
    consistency = calculate_training_consistency(WORKOUTS)
    push_pull = calculate_push_pull_ratio(WORKOUTS)
    injury_risk = calculate_injury_risk(WORKOUTS, SORENESS_DATA)

    insights = []

    # Progression insights
    enough_history = {
        ex: d
        for ex, d in progression.items()
        if len(d.get("history", []) or []) >= 2
    }
    improving = [ex for ex, d in enough_history.items() if d["status"] == "On Track"]
    plateaus = [ex for ex, d in enough_history.items() if d["status"] == "Plateau"]
    regressions = [ex for ex, d in enough_history.items() if d["status"] == "Regression"]

    if improving:
        insights.append({
            "type": "positive",
            "icon": "trending_up",
            "title": f"{len(improving)} exercises progressing",
            "detail": ", ".join(improving[:3]) + ("..." if len(improving) > 3 else "")
        })

    if plateaus:
        insights.append({
            "type": "warning",
            "icon": "pause",
            "title": f"{len(plateaus)} exercises plateaued",
            "detail": "Consider varying rep ranges or adding volume"
        })

    # Only show regression insight if enough recent data (>6 sessions in 30 days)
    from datetime import timedelta as _td
    _cutoff = (datetime.now() - _td(days=30)).strftime('%Y-%m-%d')
    _recent_count = len([w for w in WORKOUTS if w.get('date', '') >= _cutoff])
    if regressions and _recent_count >= 6:
        insights.append({
            "type": "negative",
            "icon": "trending_down",
            "title": f"{len(regressions)} exercises regressing",
            "detail": "Check recovery, sleep, and nutrition"
        })
    elif regressions and _recent_count < 6:
        insights.append({
            "type": "info",
            "icon": "fitness_center",
            "title": "Ramping back up",
            "detail": f"{_recent_count} sessions this month — regression data suppressed until 6+"
        })

    # Consistency insight
    days_since_last = consistency.get("days_since_last")
    if consistency["current_streak"] >= 4:
        insights.append({
            "type": "positive",
            "icon": "local_fire_department",
            "title": f"{consistency['current_streak']} session streak!",
            "detail": "Keep up the consistency"
        })
    elif days_since_last is not None and days_since_last > 7:
        insights.append({
            "type": "warning",
            "icon": "schedule",
            "title": "Time to train!",
            "detail": f"{days_since_last} days since last workout"
        })

    # Balance insight
    if (push_pull["push_sets"] + push_pull["pull_sets"]) > 0 and push_pull["color"] == "red":
        insights.append({
            "type": "warning",
            "icon": "balance",
            "title": f"Push/Pull imbalance ({push_pull['ratio']}:1)",
            "detail": f"Add more {'pull' if push_pull['ratio'] > 1 else 'push'} exercises"
        })

    # Injury risk insight
    if injury_risk["color"] != "green":
        insights.append({
            "type": "negative" if injury_risk["color"] == "red" else "warning",
            "icon": "warning",
            "title": f"Injury risk: {injury_risk['overall']}",
            "detail": injury_risk["risks"][0]["message"] if injury_risk["risks"] else "Monitor closely"
        })

    # PR insights
    recent_prs = [ex for ex, d in prs.items() if d["recent_30"] > 0 and d["recent_30"] >= d["all_time"]]
    if recent_prs:
        insights.append({
            "type": "positive",
            "icon": "emoji_events",
            "title": f"{len(recent_prs)} new PRs this month!",
            "detail": ", ".join(recent_prs[:2])
        })

    # Chart data for e1RM trends
    chart_data = []
    for exercise, data in progression.items():
        history = data.get("history", [])
        if history:
            chart_data.append({
                "exercise": exercise,
                "data": [{"date": h["date"], "e1rm": round(h["e1rm"], 1)} for h in history]
            })

    # Volume by muscle chart
    volume = calculate_volume(WORKOUTS, weeks=4)
    muscle_volume_chart = [
        {"muscle": m.title(), "sets": d["sets"], "volume": d["volume_load"]}
        for m, d in volume.items()
    ]

    return jsonify({
        "insights": insights,
        "charts": {
            "e1rm_trends": chart_data,
            "muscle_volume": muscle_volume_chart,
            "push_pull": {
                "push": push_pull["push_sets"],
                "pull": push_pull["pull_sets"]
            }
        }
    })


@app.route('/api/adherence')
def workout_adherence():
    """Stats on how well recommendations were followed.

    Denominator is completed workouts tied to a recommendation (the only ones
    where adherence is meaningful). `WORKOUTS` is the persisted authoritative
    list — `COMPLETED_WORKOUTS` is a session-local convenience that starts
    empty on each boot, so reading from it would silently hide history after
    the first completion of the day.
    """
    completions = WORKOUTS
    linked = [w for w in completions if w.get("recommendation_id")]
    followed = sum(1 for w in linked if w.get("adherence", {}).get("followed") is True)

    skipped_exercises = {}
    for w in linked:
        for ex in w.get("adherence", {}).get("skipped", []):
            skipped_exercises[ex] = skipped_exercises.get(ex, 0) + 1

    last = None
    if completions:
        last = max(
            completions,
            key=lambda w: (w.get("created_at") or "", w.get("date") or "", w.get("id") or ""),
        )

    adherence_pct = round(followed / len(linked) * 100) if linked else 0
    return jsonify({
        "total_recommendations": len(linked),
        "total_completed": len(completions),
        "linked_completions": len(linked),
        "followed_count": followed,
        "adherence_rate": adherence_pct,
        "frequently_skipped": sorted(skipped_exercises.items(), key=lambda x: -x[1])[:5],
        "last_completed_date": (last or {}).get("date") if last else None,
    })


@app.route('/api/baselines', methods=['GET', 'POST'])
def baselines():
    """Get or set baseline weights for exercises without history."""
    # All exercises with their muscle groups and suggested starting weights
    ALL_EXERCISES = [
        {"name": ex["name"], "muscle": ex["muscle"], "suggested": ex["baseline"]}
        for ex in EXERCISE_LIBRARY
    ]

    if request.method == 'GET':
        # Get progression data to see which exercises have history
        progression = calculate_progression_status(WORKOUTS)

        exercises_with_status = []
        for ex in ALL_EXERCISES:
            has_history = ex["name"] in progression
            baseline_weight = BASELINES_DATA.get(ex["name"])
            current_e1rm = progression.get(ex["name"], {}).get("current_e1rm")

            exercises_with_status.append({
                "name": ex["name"],
                "muscle": ex["muscle"],
                "suggested": ex["suggested"],
                "has_history": has_history,
                "baseline_weight": baseline_weight or (current_e1rm if has_history else None),
            })

        return jsonify({"exercises": exercises_with_status, "baselines": BASELINES_DATA})
    else:
        # Save baselines
        data, err = get_json_body(required=True)
        if err:
            return err
        new_baselines = data.get("baselines", {})
        if not isinstance(new_baselines, dict):
            return api_error("baselines must be an object", 400, code="invalid_field")

        cleaned = {}
        for k, v in new_baselines.items():
            if not isinstance(k, str) or not k.strip():
                continue
            try:
                cleaned[k] = float(v)
            except Exception:
                return api_error(f"Baseline for '{k}' must be a number", 400, code="invalid_field")

        BASELINES_DATA.update(cleaned)
        save_json(BASELINES_FILE, BASELINES_DATA)
        return jsonify({"status": "success", "baselines": BASELINES_DATA})


@app.route('/api/muscle-fatigue')
def muscle_fatigue():
    """Get muscle fatigue/readiness data for muscle group calculations."""
    volume = calculate_volume(WORKOUTS, weeks=2)  # Last 2 weeks

    # All muscle groups
    all_muscles = [
        "chest", "back", "shoulders", "biceps", "triceps",
        "quads", "hamstrings", "glutes", "adductors", "calves", "core"
    ]

    fatigue_data = {}
    for muscle in all_muscles:
        readiness = get_readiness_score(muscle, SORENESS_DATA, volume, CARDIO_DATA, WORKOUTS)

        # Calculate fatigue level (inverse of readiness)
        # 10 = fully recovered (green), 0 = extremely fatigued (red)
        readiness_score = readiness["score"]

        # Map to fatigue color
        if readiness_score >= 8:
            fatigue_level = "recovered"  # Green
            color = "#10b981"
        elif readiness_score >= 6:
            fatigue_level = "mild"  # Light yellow
            color = "#84cc16"
        elif readiness_score >= 4:
            fatigue_level = "moderate"  # Orange
            color = "#f59e0b"
        elif readiness_score >= 2:
            fatigue_level = "high"  # Red-orange
            color = "#f97316"
        else:
            fatigue_level = "severe"  # Red
            color = "#ef4444"

        # Get recent soreness data for this muscle
        muscle_soreness = [s for s in SORENESS_DATA if s.get("muscle") == muscle]
        recent_soreness = muscle_soreness[-1] if muscle_soreness else None

        fatigue_data[muscle] = {
            "readiness": readiness_score,
            "fatigue_level": fatigue_level,
            "color": color,
            "soreness": readiness["soreness"],
            "recovery_debt": readiness["recovery_debt"],
            "fatigue_debt": readiness.get("fatigue_debt", 0),
            "performance_debt": readiness.get("performance_debt", 0),
            "performance_debt_reason": readiness.get("performance_debt_reason"),
            "cardio_fatigue": readiness.get("cardio_fatigue", 0),
            "recommendation": readiness["recommendation"],
            "last_trained": volume.get(muscle, {}).get("last_trained"),
            "weekly_sets": volume.get(muscle, {}).get("sets", 0),
            "recent_soreness_note": recent_soreness.get("notes") if recent_soreness else None
        }

    return jsonify(fatigue_data)




# ==================== BODY RECOMP / SLEEP IMPORT / ANALYTICS ====================

def _rolling_avg(vals, window=7):
    out=[]
    for i in range(len(vals)):
        seg=vals[max(0,i-window+1):i+1]
        seg=[v for v in seg if v is not None]
        out.append(round(sum(seg)/len(seg),2) if seg else None)
    return out

def _decayed_soreness(muscle, half_life_days=2.0):
    import math
    now=datetime.now()
    score=0.0
    for e in SORENESS_DATA[-120:]:
        if e.get('muscle')!=muscle: continue
        try: d=datetime.fromisoformat((e.get('date') or '')[:10])
        except Exception: continue
        age=max(0,(now-d).days)
        weight=0.5 ** (age/max(half_life_days,0.2))
        score += float(e.get('soreness_level',0))*weight
    return min(10.0, round(score,2))

def _last_n_sessions_rpe(n=3):
    vals=[]
    for w in sorted(WORKOUTS, key=lambda x:x.get('date',''), reverse=True):
        r=[]
        for ex in (w.get('exercises') or []):
            for s in (ex.get('sets') or []):
                try:r.append(float(s.get('rpe')))
                except Exception:pass
        if r: vals.append(sum(r)/len(r))
        if len(vals)>=n: break
    return vals

@app.route('/api/body-recomp')
def body_recomp():
    hist = sorted(BODY_DATA, key=lambda x: x.get('date') or '')
    if not hist:
        return jsonify({"history": [], "summary": {}})
    dates=[h.get('date') for h in hist]
    weights=[h.get('weight_lbs') for h in hist]
    bf=[h.get('body_fat_pct') for h in hist]
    roll=_rolling_avg(weights,7)
    lean=[]; fat=[]
    for w,b in zip(weights,bf):
        if w is None or b is None: lean.append(None); fat.append(None)
        else:
            fm = w*(b/100.0); lm=w-fm
            lean.append(round(lm,2)); fat.append(round(fm,2))
    latest=hist[-1]
    tw=USER_SETTINGS.get('target_weight_lbs')
    curr=latest.get('weight_lbs')
    eta_weeks=None
    if tw and curr and len(weights)>=14:
        first=weights[max(0,len(weights)-14)]
        weekly=(curr-first)/2.0
        if weekly!=0:
            eta_weeks=round((tw-curr)/weekly,1)
    return jsonify({
        "history": hist, "dates": dates, "weight": weights, "weight_7d_avg": roll,
        "body_fat_pct": bf, "lean_mass_lbs": lean, "fat_mass_lbs": fat,
        "summary": {"latest": latest, "target_weight_lbs": tw, "target_body_fat_pct": USER_SETTINGS.get('target_body_fat_pct'), "eta_weeks": eta_weeks}
    })

@app.route('/api/body/navy-calc', methods=['POST'])
def navy_calc():
    data,err=get_json_body(required=True)
    if err: return err
    sex=(data.get('sex') or 'male').lower()
    h,err2=_coerce_float(data.get('height_in'), 'height_in', min_v=48, max_v=96)
    if err2:return err2
    neck,err2=_coerce_float(data.get('neck_in'), 'neck_in', min_v=8, max_v=30)
    if err2:return err2
    waist,err2=_coerce_float(data.get('waist_in'), 'waist_in', min_v=18, max_v=80)
    if err2:return err2
    import math
    if sex=='female':
        hip,err2=_coerce_float(data.get('hip_in'), 'hip_in', min_v=20, max_v=80)
        if err2:return err2
        bf=163.205*math.log10(waist+hip-neck)-97.684*math.log10(h)-78.387
    else:
        bf=86.010*math.log10(max(waist-neck,0.1))-70.041*math.log10(h)+36.76
    bf=max(3.0,min(60.0,round(bf,2)))
    return jsonify({"body_fat_pct": bf})

@app.route('/api/sleep/import', methods=['POST'])
def sleep_import():
    data,err=get_json_body(required=True)
    if err: return err
    if isinstance(data.get('entries'), list):
        raw=data['entries']
    elif isinstance(data.get('csv'), str):
        import csv, io
        raw=list(csv.DictReader(io.StringIO(data['csv'])))
    else:
        return api_error('Provide entries[] or csv text', 400, code='invalid_field')

    def parse_minutes(r, field, alias=None):
        value=r.get(field)
        if value is None or value=='':
            value=r.get(alias) if alias else None
        if value is None or value=='':
            return 0,False,None
        try:
            value=float(value)
        except (TypeError, ValueError, OverflowError):
            return 0,True,'invalid_number'
        if not math.isfinite(value):
            return 0,True,'invalid_number'
        if value<0 or value>1440:
            return 0,True,'out_of_range'
        return int(value),True,None

    entries=[]
    errors=[]
    for row_number,r in enumerate(raw, start=1):
        if not isinstance(r, dict):
            errors.append({'row':row_number,'field':'row','code':'invalid_row'})
            continue
        date=(r.get('date') or r.get('day') or '')[:10]
        if not date: continue
        e={'date':date,'source':r.get('source') or 'apple_watch'}
        supplied={}
        error_count=len(errors)
        for field,alias in (
            ('sleep_duration_min','duration_min'),
            ('time_in_bed_min','in_bed_min'),
            ('deep_min',None),
            ('rem_min',None),
            ('light_min',None),
            ('awake_min',None),
        ):
            value,is_supplied,error=parse_minutes(r,field,alias)
            e[field]=value
            supplied[field]=is_supplied
            if error:
                errors.append({'row':row_number,'field':field,'code':error})
        if len(errors)>error_count:
            continue
        stages=e['deep_min']+e['rem_min']+e['light_min']
        stages_supplied=any(supplied[field] for field in ('deep_min','rem_min','light_min'))
        if stages_supplied and stages>1440:
            errors.append({'row':row_number,'field':'stage_total_min','code':'contradictory_minutes'})
        if stages_supplied and supplied['awake_min'] and stages+e['awake_min']>1440:
            errors.append({'row':row_number,'field':'awake_min','code':'contradictory_minutes'})
        if supplied['sleep_duration_min'] and supplied['time_in_bed_min'] and e['sleep_duration_min']>e['time_in_bed_min']:
            errors.append({'row':row_number,'field':'sleep_duration_min','code':'contradictory_minutes'})
        if stages_supplied:
            if supplied['sleep_duration_min'] and stages>e['sleep_duration_min']:
                errors.append({'row':row_number,'field':'sleep_duration_min','code':'contradictory_minutes'})
            if supplied['time_in_bed_min'] and stages>e['time_in_bed_min']:
                errors.append({'row':row_number,'field':'time_in_bed_min','code':'contradictory_minutes'})
        if supplied['awake_min'] and supplied['time_in_bed_min']:
            if e['awake_min']>e['time_in_bed_min']:
                errors.append({'row':row_number,'field':'awake_min','code':'contradictory_minutes'})
            elif stages_supplied and stages+e['awake_min']>e['time_in_bed_min']:
                errors.append({'row':row_number,'field':'awake_min','code':'contradictory_minutes'})
        if supplied['sleep_duration_min'] and supplied['awake_min'] and supplied['time_in_bed_min'] and e['sleep_duration_min']+e['awake_min']>e['time_in_bed_min']:
            errors.append({'row':row_number,'field':'awake_min','code':'contradictory_minutes'})
        e['sleep_start']=r.get('sleep_start')
        e['sleep_end']=r.get('sleep_end')
        entries.append(e)
    if errors:
        return api_error('Sleep import contains invalid rows', 400, code='invalid_sleep_rows', details=errors)
    with JSON_DATA_LOCK:
        merged={x.get('date'):x for x in SLEEP_DATA}
        for e in entries: merged[e['date']]=e
        _json_restore_sequence(SLEEP_DATA, sorted(merged.values(), key=lambda x:x.get('date')))
        save_json(SLEEP_FILE, SLEEP_DATA)
    return jsonify({'status':'success','imported':len(entries)})

@app.route('/api/sleep/analytics')
def sleep_analytics():
    # Merge Oura and manual sleep per date; Oura wins only on overlapping dates.
    oura_rows = []
    try:
        import sqlite3 as _sq
        with closing(_sq.connect(OURA_DB_FILE)) as _db:
            _db.row_factory = _sq.Row
            _cur = _db.execute("SELECT * FROM oura_sleep WHERE type='long_sleep' ORDER BY day")
            for r in _cur.fetchall():
                oura_rows.append({
                    'date': r['day'],
                    'sleep_start': r['bedtime_start'],
                    'sleep_end': r['bedtime_end'],
                    'sleep_duration_min': r['total_sleep_min'],
                    'deep_sleep_min': r['deep_sleep_min'],
                    'rem_sleep_min': r['rem_sleep_min'],
                    'light_sleep_min': r['light_sleep_min'],
                    'awake_min': r['awake_time_min'],
                    'sleep_score': r['sleep_score'],
                    'efficiency': r['efficiency'],
                    'avg_heart_rate': r['avg_heart_rate'],
                    'avg_hrv': r['avg_hrv'],
                    'source': 'oura',
                })
    except Exception:
        app.logger.warning("Oura sleep analytics SQLite read failed", exc_info=True)
    dedup = {}
    for manual_row in SLEEP_DATA:
        row = dict(manual_row)
        row['source'] = row.get('source') or 'manual'
        date = row.get('date')
        try:
            date = datetime.strptime(date, '%Y-%m-%d').strftime('%Y-%m-%d')
        except (TypeError, ValueError):
            continue
        row['date'] = date
        if date not in dedup or row.get('source') == 'apple_watch':
            dedup[date] = row
    for row in oura_rows:
        date = row.get('date')
        try:
            date = datetime.strptime(date, '%Y-%m-%d').strftime('%Y-%m-%d')
        except (TypeError, ValueError):
            continue
        row['date'] = date
        dedup[date] = row
    rows = sorted(dedup.values(), key=lambda x: x.get('date'))
    if not rows: return jsonify({'history':[],'consistency_score':None,'sleep_perf_correlation':None})
    import statistics, math
    bed=[]
    for r in rows:
        ss=r.get('sleep_start') or ''
        try:
            t=datetime.fromisoformat(ss.replace('Z','+00:00'))
            bed.append(t.hour*60+t.minute)
        except Exception:
            pass
    consistency= None
    if len(bed)>=4:
        std=statistics.pstdev(bed)
        consistency=max(0,min(100, round(100-(std/90*100),1)))
    # next-day performance correlation with avg e1rm
    perf_by_day={}
    for w in WORKOUTS:
        e1=[]
        for ex in (w.get('exercises') or []):
            for s in (ex.get('sets') or []):
                try:e1.append(calculate_e1rm(float(s.get('weight_lbs')), int(s.get('reps'))))
                except Exception: pass
        if e1: perf_by_day[w.get('date')]=sum(e1)/len(e1)
    xs=[]; ys=[]
    for r in rows:
        d=r.get('date')
        nd=(datetime.fromisoformat(d)+timedelta(days=1)).strftime('%Y-%m-%d')
        if nd in perf_by_day and r.get('sleep_duration_min'):
            xs.append(float(r.get('sleep_duration_min'))); ys.append(perf_by_day[nd])
    corr=None
    if len(xs)>=3:
        mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
        num=sum((a-mx)*(b-my) for a,b in zip(xs,ys))
        den=(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in ys))**0.5
        corr=round(num/den,3) if den else None
    return jsonify({'history':rows,'consistency_score':consistency,'sleep_perf_correlation':corr})

@app.route('/api/analytics/advanced')
def analytics_advanced():
    def finite_number(value):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    # volume per muscle current week
    vol=calculate_volume(WORKOUTS, weeks=1)
    default_lm = DEFAULT_SETTINGS['volume_landmarks']['default']
    configured_landmarks = USER_SETTINGS.get('volume_landmarks')
    lm = configured_landmarks.get('default') if isinstance(configured_landmarks, dict) else None
    required_landmarks = ('mv', 'mev', 'mav_min', 'mav_max', 'mrv')
    valid_landmark_values = isinstance(lm, dict) and all(
        finite_number(lm.get(key))
        for key in required_landmarks
    )
    valid_landmark_order = valid_landmark_values and (
        0 <= lm['mv'] <= lm['mev'] <= lm['mav_min'] <= lm['mav_max'] <= lm['mrv']
    )
    if not valid_landmark_order:
        lm = default_lm
    volume_landmarks=[]
    for m,v in vol.items():
        sets=v.get('sets',0)
        zone='below_mv' if sets<lm['mv'] else 'mv' if sets<lm['mev'] else 'mev_to_mav' if sets<=lm['mav_max'] else 'mrv_risk' if sets>=lm['mrv'] else 'mav_high'
        volume_landmarks.append({'muscle':m,'sets':sets,'landmarks':lm,'zone':zone})
    # fatigue composite
    hrv_label = get_recent_hrv_trend(minimum_samples=4)
    hrv_trend = {"improving": "up", "stable": "stable", "declining": "down"}.get(
        hrv_label, "unknown"
    )
    hrv_pen={'up':0,'stable':5,'down':12}.get(hrv_trend,6)
    sleep=calculate_sleep_debt(OURA_DB_FILE,7)
    sleep_pen=min(20,max(0,(sleep.get('debt_minutes') or 0)/30))
    vol_load=0
    for w in WORKOUTS[-12:]:
        for ex in (w.get('exercises') or []):
            for s in (ex.get('sets') or []):
                try: vol_load += float(s.get('weight_lbs'))*int(s.get('reps'))
                except Exception: pass
    vol_pen=min(25, vol_load/12000)
    sore_pen=sum(_decayed_soreness(m,2.0) for m in ['chest','back','quads','hamstrings'])/4*2
    rpes=_last_n_sessions_rpe(3)
    ar_pen=8 if rpes and sum(rpes)/len(rpes)>8.5 else 0
    weeks_since=detect_deload_need(WORKOUTS,SORENESS_DATA).get('weeks_since_deload') or 0
    meso_pen=min(15, float(weeks_since)*2.5)
    fatigue=min(100, round(22+hrv_pen+sleep_pen+vol_pen+sore_pen+ar_pen+meso_pen,1))
    fatigue_threshold = USER_SETTINGS.get('fatigue_threshold')
    if not (
        finite_number(fatigue_threshold)
        and 40 <= fatigue_threshold <= 95
    ):
        fatigue_threshold = DEFAULT_SETTINGS['fatigue_threshold']
    deload= fatigue >= fatigue_threshold
    perf_decline = detect_deload_need(WORKOUTS,SORENESS_DATA).get('needed',False)
    return jsonify({
        'volume_landmarks': volume_landmarks,
        'fatigue_score': fatigue,
        'deload_recommended': bool(deload or perf_decline),
        'mesocycle_weeks': weeks_since,
        'autoregulation': {'avg_rpe_last_3': round(sum(rpes)/len(rpes),2) if rpes else None, 'reduce_intensity': ar_pen>0},
        'recovery': {'score': max(0,100-fatigue), 'suggested_intensity': 'recovery' if fatigue>80 else 'light' if fatigue>68 else 'moderate' if fatigue>52 else 'hard'},
        'factors': {'hrv_trend': hrv_trend, 'sleep_debt_min': sleep.get('debt_minutes'), 'volume_load': round(vol_load,1)}
    })

@app.route('/manifest.json')
def manifest():
    """PWA manifest."""
    return jsonify({
        "name": "Fitness Intelligence",
        "short_name": "FitDash",
        "description": "Evidence-based resistance training optimization",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#1a1a2e",
        "theme_color": "#4361ee",
        "orientation": "portrait",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    })


@app.route('/sw.js')
def service_worker():
    """Service worker for offline support."""
    return app.send_static_file('js/sw.js'), 200, {'Content-Type': 'application/javascript'}


@app.route('/test-chart')
def test_chart():
    """Standalone chart test page - no service worker, no caching."""
    return '''<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head><body style="background:#1a1a2e;padding:20px;margin:0;">
<h2 style="color:white;font-family:sans-serif;">Sleep Chart Test</h2>
<div style="background:#16213e;border-radius:12px;padding:16px;position:relative;height:250px;width:100%;">
<canvas id="testChart"></canvas>
</div>
<pre id="log" style="color:lime;font-size:12px;"></pre>
<script>
const log = document.getElementById('log');
fetch('/api/oura/sleep-summary')
  .then(r => r.json())
  .then(data => {
    log.textContent = 'Data: ' + JSON.stringify(data.trend_data);
    const ctx = document.getElementById('testChart').getContext('2d');
    new Chart(ctx, {
      type: 'line',
      data: {
        labels: data.trend_data.map(d => d.date.slice(5)),
        datasets: [{
          label: 'Duration (hrs)',
          data: data.trend_data.map(d => (d.duration_min/60).toFixed(1)),
          borderColor: '#4361ee',
          backgroundColor: 'rgba(67,97,238,0.1)',
          tension: 0.3, fill: true
        },{
          label: 'Score',
          data: data.trend_data.map(d => d.score),
          borderColor: '#e040fb',
          tension: 0.3
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { title: { display: true, text: '7-Day Sleep', color: '#fff' },
                   legend: { labels: { color: '#ccc' } } },
        scales: {
          x: { ticks: { color: '#999' } },
          y: { min: 0, max: 100, ticks: { color: '#999' } }
        }
      }
    });
    log.textContent += '\\n\\nChart rendered!';
  })
  .catch(e => { log.textContent = 'ERROR: ' + e; });
</script></body></html>''', 200, {'Cache-Control': 'no-store'}


def get_local_ip():
    """Get local network IP address for mobile access."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == '__main__':
    from werkzeug.serving import WSGIRequestHandler

    class SanitizedRequestHandler(WSGIRequestHandler):
        def log_request(self, code="-", size="-"):
            import re
            requestline = re.sub(
                r"([?&](?:token|access_token|refresh_token|code|state)=)[^&\s]+",
                r"\1<redacted>",
                self.requestline,
                flags=re.IGNORECASE,
            )
            requestline = re.sub(
                r"(authorization:\s*bearer\s+)[^\s,;]+",
                r"\1<redacted>",
                requestline,
                flags=re.IGNORECASE,
            )
            self.log("info", '"%s" %s %s', requestline, code, size)

    local_ip = get_local_ip()
    port = int(os.environ.get('PORT', 5050))

    print("\n" + "="*60)
    print("  FITNESS INTELLIGENCE DASHBOARD")
    print("="*60)
    print(f"\n  Local access:     http://localhost:{port}")
    print(f"  WiFi access:      http://{local_ip}:{port}")
    print("\n  For cellular data access, run:")
    print(f"    ngrok http {port}")
    print("  Then use the ngrok URL on your phone")
    print("\n" + "="*60 + "\n")

    # Debug mode defaults OFF. Opt in explicitly via FLASK_DEBUG=1 for local
    # iteration only. Debug=True enables the Werkzeug debugger/reloader which
    # is an RCE surface and interferes with launchd supervision.
    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"

    _start_vision_keep_warm_daemon()

    host = os.environ.get('HOST', '127.0.0.1')
    app.run(host=host, port=port, debug=debug_mode, request_handler=SanitizedRequestHandler)
