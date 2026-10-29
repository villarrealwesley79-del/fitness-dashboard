#!/usr/bin/env python3
"""
Fitness Intelligence System - Mobile Web App
Evidence-based resistance training optimization for iOS/Android.
"""

from flask import Flask, has_request_context, render_template, jsonify, request, Response, redirect
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dataclasses import dataclass, field, asdict
from enum import Enum
from contextlib import closing
from contextvars import ContextVar
import copy
import csv
import io
import json
import logging
import os
import socket
import sqlite3
import hashlib
import html
import math
import urllib.request
import urllib.error
import urllib.parse
import base64
import time
import re
import uuid
import tempfile
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - exercised only when optional dependency is absent.
    WebPushException = None
    webpush = None

# Load .env file if present
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
from settings_contract import DEFAULT_SETTINGS
from protected_secrets import delete_protected_secret, load_protected_secret, save_protected_secret
from history_normalization import canonical_training_category, normalize_history_item
from open_wearables_adapter import (
    build_open_wearables_status,
    providers_from_payload,
    redact_open_wearables_error,
    redacted_base_url,
    validate_open_wearables_base_url,
)
from recommendation_sources import build_open_wearables_recommendation_source, build_recommendation_source_proof, consumed_facts
from wearable_fact_store import (
    MIN_NIGHT_SLEEP_MIN,
    SOURCE_PREFERENCE,
    WearableDailyFact,
    delete_provider_data,
    fact_provider,
    init_wearable_fact_db,
    list_recommendation_facts,
    list_wearable_sources,
    preferred_daily_facts,
    upsert_daily_facts,
    upsert_wearable_source,
)
from data_store import (
    init_data_db,
    delete_current_workout_plan,
    get_current_workout_plan,
    get_current_workout_plan_snapshot,
    get_push_subscription_for_delivery,
    list_push_subscriptions,
    revoke_push_subscription,
    save_push_subscription,
    save_current_workout_plan,
)
import calendar_feed
import open_wearables_hub
import workout_recommendation_fingerprint

app = Flask(__name__)

# ── Auth (must be after app creation) ──────────────────────────
from auth import (
    data_user_id_for,
    init_auth,
    remember_trusted_no_login_oauth_state,
)
init_auth(app)

# ==================== DATA PERSISTENCE ====================
# Store runtime data under DATA_DIR when configured, otherwise next to the app.
JSON_DATA_LOCK = threading.RLock()
WORKOUTS_FILE = data_path("data_workouts.json")
SORENESS_FILE = data_path("data_soreness.json")
SETTINGS_FILE = data_path("data_settings.json")
CARDIO_FILE = data_path("data_cardio.json")
RECOVERY_FILE = data_path("data_recovery.json")
BASELINES_FILE = data_path("data_baselines.json")
BODY_FILE = data_path("data_body.json")
SLEEP_FILE = data_path("data_sleep.json")
WEARABLE_FACTS_DB_FILE = data_path("wearable_facts.sqlite3")
OPEN_WEARABLES_CONFIG_FILE = data_path("open_wearables_config.json")

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

def _settings_with_defaults(settings):
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    merged.update(settings or {})
    # Keys left in old settings files and backups by the removed food targets.
    for retired_key in ("daily_calorie_target", "daily_protein_target_g"):
        merged.pop(retired_key, None)
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
_WORKOUT_PLAN_STATE_CONTEXT: ContextVar[dict | None] = ContextVar(
    "workout_plan_state_context",
    default=None,
)
OPEN_WEARABLES_WORKOUT_MARKER_CACHE = {}


class _WorkoutPlanSnapshot(dict):
    """Dict-compatible plan snapshot carrying its durable CAS identity."""

    def __init__(
        self,
        plan=None,
        *,
        plan_generation=None,
        plan_version=None,
        current_plan_absent=False,
        absent_plan_generation=None,
    ):
        super().__init__(plan or {})
        self.plan_generation = plan_generation
        self.plan_version = plan_version
        self.current_plan_absent = current_plan_absent
        self.absent_plan_generation = absent_plan_generation


_WORKOUT_PLAN_PERSISTENCE_CONFLICT = object()
_WORKOUT_PLAN_INTERNAL_KEYS = frozenset(
    {
        "_fit233_current_plan_absent",
        "_fit233_expected_plan_generation",
        "_fit233_expected_plan_version",
    }
)

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
SORENESS_DATA = load_json(SORENESS_FILE, [])
CARDIO_DATA = load_json(CARDIO_FILE, [])
RECOVERY_DATA = load_json(RECOVERY_FILE, [])


def _backfill_history_ids(rows, filepath, entry_type):
    """Persist stable IDs for history rows created before IDs were required."""
    changed = False
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("id") in (None, ""):
                row["id"] = uuid.uuid4().hex[:12]
                changed = True
    if changed:
        save_json(filepath, rows)
        logging.info("Backfilled stable IDs for legacy %s history rows", entry_type)


_backfill_history_ids(WORKOUTS, WORKOUTS_FILE, "workout")
_backfill_history_ids(CARDIO_DATA, CARDIO_FILE, "cardio")
_backfill_history_ids(RECOVERY_DATA, RECOVERY_FILE, "recovery")

BASELINES_DATA = load_json(BASELINES_FILE, {})
BODY_DATA = load_json(BODY_FILE, [])
SLEEP_DATA = load_json(SLEEP_FILE, [])

init_data_db()
init_wearable_fact_db(WEARABLE_FACTS_DB_FILE)


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

    Clients send ``new Date().toISOString()`` (UTC, e.g.
    ``2026-05-19T03:00:00.000Z``). Downstream code reads ``.hour``
    directly on the parsed value without TZ conversion, so storing the
    raw UTC string misreports a 10 PM CT entry as hour 3. Converting incoming UTC to the
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


def _fit95_number(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _workout_duration_minutes(workout):
    duration = _fit95_number(workout.get("duration_minutes") or workout.get("duration_min"))
    if duration:
        return duration
    cardio = workout.get("cardio") if isinstance(workout, dict) else None
    if isinstance(cardio, dict):
        return _fit95_number(cardio.get("duration_minutes") or cardio.get("duration_min"))
    return 0.0


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
    for entry in _safe_dated_body_entries():
        if entry.get("weight_lbs") is not None:
            return entry["weight_lbs"]
    return None


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


_WEARABLE_REFRESH_SECONDS = 15 * 60
_WEARABLE_REFRESH_STATE = {"at": None}
_WEARABLE_REFRESH_LOCK = threading.Lock()  # a second caller waits at most 2 s for the fetch in progress, then reads the stored facts
_WEARABLE_BACKFILL_CHUNK_DAYS = 28


def _refresh_wearable_facts(force=False):
    """Store the live Open Wearables window when the last attempt is over 15 minutes old.

    Failures are swallowed: readers fall back to the facts already stored.
    """
    if not _WEARABLE_REFRESH_LOCK.acquire(timeout=2):
        return False  # a fetch is still running (OW slow or hung): read the stored facts
    try:
        last = _WEARABLE_REFRESH_STATE["at"]
        if not force and last is not None and time.monotonic() - last < _WEARABLE_REFRESH_SECONDS:
            return False
        _WEARABLE_REFRESH_STATE["at"] = time.monotonic()
        try:
            # 2 s per OW call: the whole live window (6 calls) stays inside the page's 30 s client timeout when OW stalls
            _store_wearable_facts_from_open_wearables(fetch_open_wearables_data(timeout_s=2))
            return True
        except Exception:
            app.logger.warning("Open Wearables refresh failed; using stored facts", exc_info=True)
            return False
    finally:
        _WEARABLE_REFRESH_LOCK.release()


def backfill_open_wearables_facts(days=365):
    """Store ``days`` of Open Wearables history in 28-day windows, oldest first so the newest wins last."""
    now = datetime.now().astimezone()
    window_ends = [now - timedelta(days=offset) for offset in range(0, max(1, int(days)), _WEARABLE_BACKFILL_CHUNK_DAYS)]
    return sum(
        _store_wearable_facts_from_open_wearables(
            fetch_open_wearables_data(days=_WEARABLE_BACKFILL_CHUNK_DAYS, now=window_end)
        )
        for window_end in reversed(window_ends)
    )


def _preferred_facts(metrics, days=7, provider=None):
    """{date: {metric: fact}} for the last ``days`` days under the owner's source rule (one provider if given)."""
    end = datetime.now().date()
    try:
        return preferred_daily_facts(
            WEARABLE_FACTS_DB_FILE,
            metrics,
            (end - timedelta(days=days - 1)).isoformat(),
            end.isoformat(),
            _open_wearables_profile_key(),
            only_provider=provider,
        )
    except Exception:
        app.logger.warning("Stored wearable facts unreadable", exc_info=True)
        return {}


def _readiness_with_basis():
    """(readiness 0-100, basis) from the newest fresh valid night of the preferred sleep provider.

    WHOOP recovery is not read here: the W4a recovery guard applies it, once.
    """
    row = next((r for r in _sleep_rows_from_facts(days=2) if r["freshness"] == "fresh" and _is_valid_nightly_sleep_row(r)), None)
    if not row:
        return None, None
    duration_min, avg_hr = row["sleep_duration_min"], row["avg_heart_rate"]
    adjust = -5 if duration_min < 360 else -3 if duration_min < 420 else 3 if duration_min >= 540 else 0
    if avg_hr is not None:
        adjust += -4 if avg_hr >= 75 else -2 if avg_hr >= 70 else 2 if avg_hr <= 55 else 0
    return int(max(0, min(100, round(70 + 1.5 * adjust)))), "sleep_estimate"


def _get_oura_readiness_today(include_open_wearables=True):
    """Readiness 0-100 for today's plan (refreshing facts first unless told not to)."""
    if include_open_wearables:
        _refresh_wearable_facts()
    return _readiness_with_basis()[0]


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
    return _deterministic_swap_candidate(typed_name, candidates)


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
    if not deterministic_swap:
        return merged

    existing_swaps = merged.get("swap") or []
    if not isinstance(existing_swaps, list):
        existing_swaps = []

    deterministic_index = deterministic_swap.get("replace_index")
    deterministic_name = str(deterministic_swap.get("replace_exercise") or "").strip().lower()
    deterministic_definition = (
        _resolve_exercise_definition(deterministic_name) if deterministic_name else None
    )

    def _same_source_slot(candidate):
        if not isinstance(candidate, dict):
            return False
        candidate_index = candidate.get("replace_index")
        candidate_name = str(candidate.get("replace_exercise") or "").strip().lower()
        candidate_definition = (
            _resolve_exercise_definition(candidate_name) if candidate_name else None
        )

        def _same_source_name():
            if deterministic_definition and candidate_definition:
                return _same_exercise_definition(
                    deterministic_definition, candidate_definition
                )
            return bool(deterministic_name and candidate_name == deterministic_name)

        if isinstance(deterministic_index, int) and isinstance(candidate_index, int):
            if deterministic_index != candidate_index:
                return False
            if deterministic_name:
                return _same_source_name()
            return not candidate_name
        if deterministic_name and candidate_name:
            return _same_source_name()
        return False

    swaps = []
    deterministic_inserted = False
    replaced_model_swap = False
    for swap in existing_swaps:
        if _same_source_slot(swap):
            if isinstance(swap, dict) and not swap.get("_deterministic"):
                replaced_model_swap = True
            if not deterministic_inserted:
                swaps.append(deterministic_swap)
                deterministic_inserted = True
            continue
        swaps.append(swap)
    if not deterministic_inserted:
        swaps.append(deterministic_swap)
    merged["swap"] = swaps
    if replaced_model_swap:
        merged["_deterministic_swap_replaced_model_swap"] = True
    return merged


def _adjust_outcome_summary(
    result_kind,
    applied_notes,
    skipped_notes,
    model_summary,
    *,
    trust_model_summary=True,
):
    if result_kind == "unchanged":
        return "The requested adjustment could not be applied to the current plan."
    if result_kind == "changed" and applied_notes and skipped_notes:
        return "Applied some requested changes; other requested changes were skipped."
    if result_kind == "changed" and applied_notes and not trust_model_summary:
        return f"Applied validated changes: {'; '.join(applied_notes)}"
    return model_summary


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
    intent=None,
):
    patched = _copy_workout_plan_cas_identity(
        recommendation,
        json.loads(json.dumps(recommendation, default=str)),
    )
    intent = _merge_deterministic_adjust_swap(intent, deterministic_swap)
    patched, applied_notes, skipped_notes = _apply_intent_patch(
        patched, intent, goal_params, meso_week, meso_plan, oura_readiness, equipment_pref
    )
    result_kind = "changed" if applied_notes else "unchanged"
    summary = _adjust_outcome_summary(
        result_kind,
        applied_notes,
        skipped_notes,
        "Applied the requested change.",
    )
    return {
        "status": "ok",
        "result_kind": result_kind,
        "recommendation": patched,
        "summary": summary,
        "applied_notes": applied_notes,
        "skipped_notes": skipped_notes,
        "constraint": constraint,
        "meta": {"mode": "deterministic", "reason": reason},
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


def _observed_set_rpe(set_row):
    """A set's RPE as a float, only when it is an observed effort (FIT-392).

    The guided workout saves the prescribed target as ``rpe`` with
    ``rpe_observed: False`` for sets the user never touched; that target is not
    evidence of effort. Rows without the flag (legacy or imported estimates) are unobserved too.
    """
    if not isinstance(set_row, dict) or set_row.get("rpe_observed") is not True:
        return None
    try:
        rpe = float(set_row.get("rpe"))
    except (TypeError, ValueError, OverflowError):
        return None
    return rpe if math.isfinite(rpe) else None


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
            rpes = [r for r in (_observed_set_rpe(s) for s in sets) if r is not None]
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

    # Missed reps are evidence on their own, even when no RPE was observed.
    if not completed_reps:
        return max(5, base_weight - weight_step), base_reps, "Auto-regulated: reduce load"

    if avg_rpe is None:
        return base_weight, base_reps, "Maintain load: RPE missing"

    if avg_rpe < target_rpe and completed_reps:
        return base_weight + weight_step, base_reps, f"Progression: +{weight_step} lbs"
    if abs(avg_rpe - target_rpe) <= 0.25 and completed_reps:
        return base_weight, base_reps + 1, "Progression: +1 rep"
    if avg_rpe > target_rpe + 0.25:
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
        available_time = min(available_time, _schedule_fit()["plan_minutes"] or available_time)  # PER-1586

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
        "id": _new_workout_recommendation_id(),
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
                                    "action": "Check form, sleep, recovery",
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
            machine = exercise.get("machine")
            if not machine:
                continue  # rows stored before the route validated the name
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
                set_rpe = _observed_set_rpe(s)
                if set_rpe is not None and set_rpe >= 9:
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
            machine = e.get("machine")
            if not machine:
                continue
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
    duration-derived load for cardio-style app workouts.
    Acute: sum of last 7 days.
    Chronic: average 7-day load over the last 28 days (uses available days if < 28).
    """
    today = datetime.now().date()
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


def _sleep_rows_from_facts(days=7):
    """Nightly sleep rows, newest first, one per date from the preferred provider."""
    metrics = {
        "sleep_duration", "sleep_deep_duration", "sleep_rem_duration", "sleep_light_duration",
        "sleep_awake_duration", "sleep_efficiency", "sleep_avg_heart_rate", "sleep_time_in_bed",
    }
    rows = []
    for day, by_metric in sorted(_preferred_facts(metrics, days=days).items(), reverse=True):
        duration = by_metric.get("sleep_duration")
        if not duration:
            continue
        # The whole night comes from the provider whose duration was used, so a conflict
        # never pairs WHOOP's total with Oura's stages.
        def value(name):
            fact = by_metric.get(name)
            return fact["value"] if fact and fact["provider"] == duration["provider"] else None

        end_at = duration.get("observed_at")
        time_in_bed = value("sleep_time_in_bed")
        rows.append({
            "day": day,
            "sleep_duration_min": duration["value"],
            "total_sleep_min": duration["value"],
            "sleep_deep_min": value("sleep_deep_duration"),
            "sleep_rem_min": value("sleep_rem_duration"),
            "sleep_light_min": value("sleep_light_duration"),
            "sleep_awake_min": value("sleep_awake_duration"),
            "deep_sleep_min": value("sleep_deep_duration"),
            "rem_sleep_min": value("sleep_rem_duration"),
            "light_sleep_min": value("sleep_light_duration"),
            "awake_time_min": value("sleep_awake_duration"),
            "avg_heart_rate": value("sleep_avg_heart_rate"),
            "efficiency": value("sleep_efficiency"),
            "sleep_score": None,
            "provider": duration["provider"],
            "via": duration["via"],
            "freshness": duration["freshness"],
            "conflict_state": duration["conflict_state"],
            "sleep_end": end_at,
            # Estimated: wake time minus time in bed (only the offset-free spread is scored).
            "bedtime_start": _estimated_bedtime(end_at, time_in_bed) if time_in_bed else None,
        })
    return rows


def _estimated_bedtime(wake_at, time_in_bed_min):
    try:
        wake = datetime.fromisoformat(str(wake_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if len(str(wake_at)) <= 10:  # a bare date carries no time of day
        return None
    return (wake - timedelta(minutes=float(time_in_bed_min))).isoformat()


def calculate_sleep_debt(days: int = 7) -> dict:
    """Accumulated sleep debt over the last N days from the preferred sleep facts."""
    target = 420  # minutes
    days = max(1, int(days or 7))
    rows = _sleep_rows_from_facts(days=days)
    rows = rows[:days]
    rows = [r for r in rows if _is_valid_nightly_sleep_row(r)]
    # The fact behind the aggregate (provider, date, freshness) for the source proof.
    source = {k: rows[0][k] for k in ("provider", "via", "freshness", "conflict_state")} | {"date": rows[0]["day"]} if rows else None
    if not rows or rows[0]["freshness"] == "stale":  # a stale newest night is excluded, not averaged in
        return {
            "debt_minutes": 0,
            "debt_hours": 0.0,
            "nights_under": 0,
            "avg_sleep_min": 0.0,
            "status": "good",
            "message": "No recent sleep-duration data available from wearable facts.",
            "sample_count": 0,
            **({"source": source} if source else {}),
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
        "sample_count": n,
        "source": source,
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
    for key in _WORKOUT_PLAN_INTERNAL_KEYS:
        scoped.pop(key, None)
    rpe_targets = []
    for exercise in recommendation.get("exercises") or []:
        if not isinstance(exercise, dict):
            continue
        raw_target = exercise.get("rpe_target")
        if isinstance(raw_target, bool):
            continue
        try:
            target = float(raw_target)
        except (TypeError, ValueError):
            continue
        if 1 <= target <= 10:
            rpe_targets.append(target)
    if rpe_targets:
        minimum = min(rpe_targets)
        maximum = max(rpe_targets)

        def _format_rpe(value):
            return str(int(value)) if value.is_integer() else str(value)

        label = _format_rpe(minimum)
        if maximum != minimum:
            label = f"{label}–{_format_rpe(maximum)}"
        scoped["rpe_range"] = {
            "min": int(minimum) if minimum.is_integer() else minimum,
            "max": int(maximum) if maximum.is_integer() else maximum,
            "label": label,
        }
    scoped["auth_scope"] = _current_auth_scope()
    return scoped


def _new_workout_recommendation_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:12]}"


def _persist_current_workout_plan(
    recommendation: dict,
    fingerprint: str,
    *,
    customized: bool = False,
) -> dict | None:
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT, LAST_WORKOUT_RECOMMENDATION_OWNER
    with CURRENT_WORKOUT_PLAN_LOCK:
        expected_current_plan_absent = (
            recommendation.get("_fit233_current_plan_absent") is True
            or getattr(recommendation, "current_plan_absent", False) is True
        )
        expected_plan_generation = recommendation.get("_fit233_expected_plan_generation")
        expected_plan_version = recommendation.get("_fit233_expected_plan_version")
        if expected_plan_generation is None:
            expected_plan_generation = getattr(recommendation, "plan_generation", None)
        if expected_plan_version is None:
            expected_plan_version = getattr(recommendation, "plan_version", None)
        expected_absent_plan_generation = getattr(
            recommendation,
            "absent_plan_generation",
            None,
        )
        expected_plan_identity = (
            expected_plan_generation is not None and expected_plan_version is not None
        )
        if expected_current_plan_absent or expected_plan_identity or any(
            key in recommendation for key in _WORKOUT_PLAN_INTERNAL_KEYS
        ):
            recommendation = dict(recommendation)
            for key in _WORKOUT_PLAN_INTERNAL_KEYS:
                recommendation.pop(key, None)
        stored_customization_marker = recommendation.get("_user_customized") is True
        if stored_customization_marker:
            recommendation = dict(recommendation)
            recommendation.pop("_user_customized", None)
        current_user_id = _current_data_user_id()
        same_current_plan = bool(
            LAST_WORKOUT_RECOMMENDATION_OWNER
            and LAST_WORKOUT_RECOMMENDATION_OWNER.get("user_id") == current_user_id
            and LAST_WORKOUT_RECOMMENDATION
            and recommendation == LAST_WORKOUT_RECOMMENDATION
        )
        customized = bool(
            customized
            or stored_customization_marker
            or (same_current_plan and LAST_WORKOUT_RECOMMENDATION_OWNER.get("customized"))
        )
        persisted_recommendation = dict(recommendation)
        if customized:
            persisted_recommendation["_user_customized"] = True
        only_if_current_plan_absent = expected_current_plan_absent
        save_kwargs = {}
        if only_if_current_plan_absent:
            save_kwargs["only_if_current_plan_absent"] = True
            if expected_absent_plan_generation is not None:
                save_kwargs["expected_absent_plan_generation"] = int(
                    expected_absent_plan_generation
                )
        if expected_plan_identity:
            save_kwargs["expected_plan_generation"] = int(expected_plan_generation)
            save_kwargs["expected_plan_version"] = int(expected_plan_version)
        persisted = save_current_workout_plan(
            current_user_id,
            fingerprint,
            persisted_recommendation,
            **save_kwargs,
        )
        if persisted is None:
            if only_if_current_plan_absent or expected_plan_identity:
                return _WORKOUT_PLAN_PERSISTENCE_CONFLICT
            return None
        authoritative_plan = _WorkoutPlanSnapshot(
            persisted["plan"],
            plan_generation=persisted.get("plan_generation"),
            plan_version=persisted.get("plan_version"),
        )
        authoritative_plan.pop("_user_customized", None)
        LAST_WORKOUT_RECOMMENDATION = authoritative_plan
        LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = fingerprint
        LAST_WORKOUT_RECOMMENDATION_OWNER = {
            "user_id": current_user_id,
            "fingerprint": fingerprint,
            "plan_id": id(authoritative_plan),
            "customized": customized,
        }
        if persisted.get("plan_generation") is not None and persisted.get("plan_version") is not None:
            LAST_WORKOUT_RECOMMENDATION_OWNER.update(
                {
                    "plan_generation": persisted["plan_generation"],
                    "plan_version": persisted["plan_version"],
                }
            )
    return authoritative_plan


def _same_day_preserved_workout_plan(today_s: str) -> dict | None:
    stored = get_current_workout_plan(_current_data_user_id())
    if not stored or str(stored.get("updated_at") or "")[:10] != today_s:
        return None
    plan = stored.get("plan")
    preserved = isinstance(plan, dict) and plan.get("_user_customized") is True
    if not preserved or _is_lightweight_current_workout_plan(plan):
        return None
    plan = dict(plan)
    plan.pop("_user_customized", None)
    return plan


def _wearable_adjusted_for_display(base_plan, open_wearables_modifier):
    """Cap rpe_target at the Open Wearables recovery cap, for DISPLAY only.

    Returns a copy; the persisted canonical plan stays the base plan (FIT-256
    finding 2), so callers must not persist the result.
    """
    cap = (open_wearables_modifier or {}).get("rpe_cap")
    if cap is None or not isinstance(base_plan, dict):
        return base_plan
    plan = copy.deepcopy(base_plan)
    for exercise in plan.get("exercises") or []:
        rpe = exercise.get("rpe_target") if isinstance(exercise, dict) else None
        if isinstance(rpe, (int, float)) and rpe > cap:
            exercise["rpe_target"] = cap
    return plan


def _wearable_display_adjusted_plan(base_plan):
    """Display-time Open Wearables recovery cap for surfaces that render a
    persisted plan without building the modifier themselves (``/gym-now``,
    ``/api/workout/swap``, ``/api/workout/adjust``). Best-effort: returns
    ``base_plan`` unchanged on any failure. Never persist the result.
    """
    if not isinstance(base_plan, dict) or not base_plan:
        return base_plan
    try:
        modifier = open_wearables_hub.conservative_modifier(_open_wearables_recommendation_facts())
    except Exception:
        return base_plan
    return _wearable_adjusted_for_display(base_plan, modifier)


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


def _current_workout_plan_for_fingerprint(
    fingerprint: str,
    *,
    allow_stale_unsaved: bool = False,
    return_lookup_token: bool = False,
    durable_only: bool = False,
    plan_state: dict | None = None,
) -> dict | None:
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT, LAST_WORKOUT_RECOMMENDATION_OWNER
    with CURRENT_WORKOUT_PLAN_LOCK:
        current_user_id = _current_data_user_id()
        plan_state = (
            plan_state
            or _WORKOUT_PLAN_STATE_CONTEXT.get()
            or get_current_workout_plan_snapshot(current_user_id)
        )
        row = plan_state["current_plan"]
        if (
            row
            and row.get("plan")
            and not _is_lightweight_current_workout_plan(row["plan"])
            and (row["fingerprint"] == fingerprint or allow_stale_unsaved or durable_only)
        ):
            plan = _WorkoutPlanSnapshot(
                row["plan"],
                plan_generation=row.get("plan_generation"),
                plan_version=row.get("plan_version"),
            )
            customized = plan.get("_user_customized") is True
            if customized:
                plan.pop("_user_customized", None)
            LAST_WORKOUT_RECOMMENDATION = plan
            LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = row["fingerprint"]
            LAST_WORKOUT_RECOMMENDATION_OWNER = {
                "user_id": current_user_id,
                "fingerprint": row["fingerprint"],
                "plan_id": id(plan),
                "customized": customized,
                "plan_generation": row.get("plan_generation"),
                "plan_version": row.get("plan_version"),
            }
            return plan
        if durable_only:
            return None
        owner = LAST_WORKOUT_RECOMMENDATION_OWNER or {}
        if (
            allow_stale_unsaved
            and LAST_WORKOUT_RECOMMENDATION
            and not _is_lightweight_current_workout_plan(LAST_WORKOUT_RECOMMENDATION)
            and owner.get("user_id") == current_user_id
            and owner.get("plan_id") == id(LAST_WORKOUT_RECOMMENDATION)
        ):
            mutation_snapshot = _WorkoutPlanSnapshot(
                LAST_WORKOUT_RECOMMENDATION,
                current_plan_absent=True,
                absent_plan_generation=int(plan_state["plan_generation"]),
            )
            mutation_snapshot["_fit233_current_plan_absent"] = True
            return mutation_snapshot
        if return_lookup_token:
            if row:
                return _WorkoutPlanSnapshot(
                    {},
                    plan_generation=row.get("plan_generation"),
                    plan_version=row.get("plan_version"),
                )
            return _WorkoutPlanSnapshot(
                {},
                current_plan_absent=True,
                absent_plan_generation=int(plan_state["plan_generation"]),
            )
        return None


def _copy_workout_plan_cas_identity(source: dict, target: dict) -> dict:
    """Copy mutation-only CAS metadata without exposing it in API payloads."""
    copied = dict(target)
    current_plan_absent = (
        source.get("_fit233_current_plan_absent") is True
        or getattr(source, "current_plan_absent", False) is True
    )
    plan_generation = source.get("_fit233_expected_plan_generation")
    plan_version = source.get("_fit233_expected_plan_version")
    if plan_generation is None:
        plan_generation = getattr(source, "plan_generation", None)
    if plan_version is None:
        plan_version = getattr(source, "plan_version", None)
    if plan_generation is not None and plan_version is not None:
        plan_generation = int(plan_generation)
        plan_version = int(plan_version)
    for key in _WORKOUT_PLAN_INTERNAL_KEYS:
        copied.pop(key, None)
    if current_plan_absent or (plan_generation is not None and plan_version is not None):
        return _WorkoutPlanSnapshot(
            copied,
            plan_generation=plan_generation,
            plan_version=plan_version,
            current_plan_absent=current_plan_absent,
            absent_plan_generation=getattr(source, "absent_plan_generation", None),
        )
    return copied


def _payload_with_recommendation_auth_scope(payload: dict) -> dict:
    scoped = dict(payload)
    if scoped.get("recommendation"):
        scoped["recommendation"] = _workout_with_auth_scope(scoped["recommendation"])
    return scoped


def compute_hrv_trend(hrv_values):
    """Trend label from last-3 vs previous-3 averages (2 ms threshold); 'stable' under 4 samples."""
    vals = [v for v in hrv_values if v is not None]
    if len(vals) < 4:
        return "stable"
    last = vals[-3:]
    prev = vals[-6:-3] if len(vals) >= 6 else vals[:-3]
    if not prev:
        return "stable"
    diff = sum(last) / len(last) - sum(prev) / len(prev)
    return "improving" if diff > 2.0 else "declining" if diff < -2.0 else "stable"


_HRV_METRICS = ("heart_rate_variability", "sleep_hrv_rmssd", "sleep_hrv_sdnn")


def _hrv_facts(days=7, drop_short=True):
    """HRV facts oldest first, from one provider and one metric so RMSSD and SDNN are never mixed.

    The first provider in the recovery preference with at least 4 samples wins; fewer
    everywhere falls back to the most preferred one. A series whose newest fact is stale is
    skipped, so a fresh fallback provider fills the gap; all stale returns []. A sub-hour-only
    day's sleep HRV is left out unless the series chosen with short days kept (possibly another
    provider or metric) is 'declining' and the one without them is not.
    """
    preference = SOURCE_PREFERENCE["recovery"]
    metrics = set(_HRV_METRICS) | {"sleep_duration"}  # sleep_duration brings in the night's fragment exclusion
    seen = {f["provider"] for by_metric in _preferred_facts(metrics, days=days).values() for m, f in by_metric.items() if m in _HRV_METRICS}
    series = {}
    for provider in [*preference, *sorted(seen - set(preference))]:  # each provider's whole series, not the dates it won
        for _day, by_metric in sorted(_preferred_facts(metrics, days=days, provider=provider).items()):
            # a date whose only sleep is a sub-hour record (strap on briefly) has no night HRV
            short = drop_short and (by_metric.get("sleep_duration") or {}).get("value", MIN_NIGHT_SLEEP_MIN) < MIN_NIGHT_SLEEP_MIN
            for metric, fact in by_metric.items():
                if metric in _HRV_METRICS and not (short and metric.startswith("sleep_")):
                    series.setdefault((provider, metric), []).append(fact)
    ranked = sorted(series, key=lambda k: (preference.index(k[0]) if k[0] in preference else len(preference), _HRV_METRICS.index(k[1])))
    live = [k for k in ranked if series[k][-1]["freshness"] != "stale"]
    chosen = next((series[k] for k in live if len(series[k]) >= 4), series[live[0]] if live else [])
    if drop_short and compute_hrv_trend([f["value"] for f in chosen]) != "declining":
        with_short = _hrv_facts(days, drop_short=False)  # a short day's HRV may make the plan more cautious, never less
        if compute_hrv_trend([f["value"] for f in with_short]) == "declining":
            return with_short
    return chosen


def _hrv_series(days=7):
    return [f["value"] for f in _hrv_facts(days)]


def get_recent_hrv_trend(days=7, minimum_samples=None):
    """Return the recent HRV trend, or unknown when data is sparse/unavailable."""
    try:
        hrv_values = _hrv_series(days)
        if minimum_samples is not None and len(hrv_values) < minimum_samples:
            return "unknown"
        return compute_hrv_trend(hrv_values)
    except Exception:
        return "unknown"


def _current_workout_training_recommendation():
    """Mirror the dashboard's readiness context for lightweight workout loads."""
    readiness_val = _readiness_with_basis()[0]

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
        sleep_debt=calculate_sleep_debt(days=7),
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


_PROOF_FACT_METRICS = {
    "sleep_duration", "recovery_score", "resting_heart_rate", "steps", "active_minutes", "active_calories",
    *_HRV_METRICS,
}
_FINGERPRINT_FACT_METRICS = {
    "recovery_score", "resting_heart_rate",
    "sleep_duration", "sleep_efficiency", "active_minutes", "steps",
}  # HRV is fingerprinted through _hrv_facts(), the series the plan reads


# --- Google Calendar secret iCal feed (PER-1586) ---------------------------------------
# Read-only. The address is a secret: it lives in protected_secrets and never reaches a
# response, a log line or an error message. The owner's assistant blocks time through its
# own calendar connector; this app never writes to the calendar.
GOOGLE_CALENDAR_SERVICE = "fitness-dashboard-google-calendar-ical"
GOOGLE_CALENDAR_FILE_ENV = "GOOGLE_CALENDAR_ICAL_FILE"
CALENDAR_REFRESH_SECONDS = 15 * 60
_CALENDAR = {}  # last feed attempt: key, day, at, status, events, last_fetch
_CALENDAR_LOCK = threading.Lock()


def _parse_training_hours(value):
    """(start, end) as times from {"start": "HH:MM", "end": "HH:MM"} with start before end, else None."""
    try:
        start, end = (datetime.strptime(value[key], "%H:%M").time() for key in ("start", "end"))
    except (TypeError, KeyError, ValueError):
        return None
    return (start, end) if start < end else None


def _training_hours():
    return (
        _parse_training_hours(USER_SETTINGS.get("training_hours"))
        or _parse_training_hours(DEFAULT_SETTINGS["training_hours"])
    )


def _calendar_secret_args():
    override = os.environ.get(GOOGLE_CALENDAR_FILE_ENV, "").strip()
    path = os.path.abspath(os.path.expanduser(override)) if override else data_path(".google-calendar-ical-url")
    account = "google-calendar-" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return (GOOGLE_CALENDAR_SERVICE, account), {"fallback_path": path, "fallback_override_env": GOOGLE_CALENDAR_FILE_ENV}


def _calendar_now():
    return datetime.now().astimezone().replace(second=0, microsecond=0)


def _calendar_state(now, force=False):
    """The cached feed: fetched at most every 15 minutes (and once per day); outages are cached too.

    status is unconfigured | ok | error. The address is read, used and dropped in here.
    """
    # Readers never wait on a fetch: while one runs they get the last state. Only a connect waits (force).
    if not _CALENDAR_LOCK.acquire(blocking=force):
        cached = dict(_CALENDAR)
        if cached.get("day") == now.date() and cached.get("key") == _calendar_secret_args()[1]["fallback_path"]:
            return cached
        args, kwargs = _calendar_secret_args()
        if not load_protected_secret(*args, **kwargs).strip():  # a local read, no network: a never-connected user is not "refreshing"
            return {"status": "unconfigured", "events": [], "last_fetch": None}
        return {"status": "refreshing", "events": [], "last_fetch": None}  # old-day or other-address data would show a booked day as free
    try:
        args, kwargs = _calendar_secret_args()
        cached = _CALENDAR
        if force or not (
            cached.get("key") == kwargs["fallback_path"]
            and cached.get("day") == now.date()
            and time.monotonic() - cached.get("at", -CALENDAR_REFRESH_SECONDS) < CALENDAR_REFRESH_SECONDS
        ):
            fresh = dict(key=kwargs["fallback_path"], day=now.date(), at=time.monotonic(),
                         status="unconfigured", events=[], last_fetch=None)
            url = load_protected_secret(*args, **kwargs).strip()
            if url:
                fresh.update(status="error", last_fetch=now.isoformat(timespec="minutes"))
                try:
                    start = datetime.combine(now.date(), datetime.min.time()).astimezone()
                    end = datetime.combine(now.date() + timedelta(days=2), datetime.min.time()).astimezone()
                    fresh.update(events=calendar_feed.parse_events(calendar_feed.fetch_ics(url), start, end),
                                 status="ok")
                except Exception as exc:  # outage, bad address or hostile feed: the plan carries on without it
                    logging.getLogger(__name__).warning("Calendar feed unavailable (%s).", type(exc).__name__)
            cached.update(fresh)  # swapped in only when done, so readers never see a half-built state
        return dict(cached)
    finally:
        _CALENDAR_LOCK.release()


def _calendar_day(events, now, offset):
    """(busy, free windows) inside the training hours of today (from now on) or tomorrow."""
    day = now.date() + timedelta(days=offset)
    start, end = _training_hours()
    window_start, window_end = (datetime.combine(day, t).astimezone() for t in (start, end))
    if offset == 0:
        window_start = max(window_start, now)
    if window_end <= window_start:
        return [], []
    busy = calendar_feed.busy_intervals(events, window_start, window_end)
    return busy, calendar_feed.free_windows(busy, window_start, window_end)


def _calendar_iso(moment):
    return moment.astimezone().isoformat(timespec="minutes")


def _calendar_clock(moment):
    return moment.astimezone().strftime("%I:%M %p").lstrip("0")


def _schedule_fit(plan=None):
    """How the calendar changes today's plan. plan_minutes is the shortened length, else None.

    `plan` is a preserved (customised) plan that is returned as it is: it is never shortened,
    only checked against the free windows.
    """
    now = _calendar_now()
    state = _calendar_state(now)
    if state["status"] != "ok":
        return {"status": state["status"], "plan_minutes": None}
    windows = _calendar_day(state["events"], now, 0)[1]
    span = lambda w: (w[1].astimezone(timezone.utc) - w[0].astimezone(timezone.utc)).total_seconds() / 60
    if plan is not None:
        need = int(plan.get("estimated_minutes") or 0)
        held = next((w for w in windows if span(w) >= need), None)  # earliest that holds the plan
        fit = {"fits": bool(held), "minutes": need if held else 0, "shortened": False,
               "window_start": held[0] if held else None, "window_end": held[1] if held else None}
    else:
        wanted = USER_SETTINGS.get("available_time_minutes", DEFAULT_SETTINGS["available_time_minutes"])
        fit = calendar_feed.fit_session(windows, wanted, [o["value"] for o in TIME_OPTIONS])
        if fit["fits"] and not fit["shortened"]:
            fit = {**fit, "minutes": wanted}  # the generator keeps the owner's own length, even above the options
        if fit["fits"]:  # a window is a fit only if the plan built for it is no longer than the window
            built = generate_next_workout(WORKOUTS, SORENESS_DATA, available_time=fit["minutes"],
                                          consume_cardio_rotation=False, include_open_wearables_readiness=False)
            if built["estimated_minutes"] > span((fit["window_start"], fit["window_end"])):
                later = next((w for w in windows if span(w) >= built["estimated_minutes"]), None)  # earliest that holds the plan
                fit = {**fit, "window_start": later[0], "window_end": later[1]} if later else {**fit, "fits": False, "minutes": 0, "shortened": False}
    out = {
        "status": "ok", "fits": fit["fits"], "minutes": fit["minutes"] or None, "shortened": fit["shortened"],
        "plan_minutes": fit["minutes"] if fit["fits"] and fit["shortened"] else None,
    }
    if fit["fits"]:
        out["window_start"], out["window_end"] = _calendar_iso(fit["window_start"]), _calendar_iso(fit["window_end"])
        out["reason"] = (
            f"Calendar: {fit['minutes']} min fits {_calendar_clock(fit['window_start'])} to "
            f"{_calendar_clock(fit['window_end'])}" + (f"; session budget set to {fit['minutes']} min" if fit["shortened"] else "")
        )
    else:
        tomorrow = _calendar_day(state["events"], now, 1)[1]
        out["next_window"] = {"day": "tomorrow", "start": _calendar_iso(tomorrow[0][0])} if tomorrow else None
        out["reason"] = (
            f"Calendar: no free window today; next window tomorrow {_calendar_clock(tomorrow[0][0])}"
            if tomorrow else "Calendar: no free window today or tomorrow"
        )
    return out


def _calendar_status(state):
    return {"configured": state["status"] != "unconfigured", "status": state["status"], "last_fetch": state["last_fetch"]}


@app.route('/api/calendar/ical', methods=['GET', 'POST', 'DELETE'])
def api_calendar_ical():
    """Connection state only; the address is write-only."""
    if request.method == 'GET':
        return jsonify(_calendar_status(_calendar_state(_calendar_now())))
    args, kwargs = _calendar_secret_args()
    try:
        if request.method == 'DELETE':
            delete_protected_secret(*args, **kwargs)
        else:
            data, err = get_json_body(required=True)
            if err:
                return err
            raw, err = _coerce_str(data.get("url"), "url", max_len=500)
            if err:
                return err
            try:
                url = calendar_feed.validate_ical_url(raw)
            except ValueError as exc:  # the message never echoes the address
                return api_error(str(exc), 400, code="invalid_field")
            save_protected_secret(*args, url, **kwargs)
    except (OSError, RuntimeError):
        return api_error("Could not update the calendar address.", 500, code="calendar_store_failed")
    return jsonify(_calendar_status(_calendar_state(_calendar_now(), force=True)))


@app.route('/api/calendar/windows')
def api_calendar_windows():
    """Busy and free training windows for the owner's assistant (mcp get_open_windows)."""
    day = request.args.get("day", "today")
    if day not in ("today", "tomorrow"):
        return api_error("day must be today or tomorrow", 400, code="invalid_field")
    now = _calendar_now()
    state = _calendar_state(now)
    out = {"status": state["status"], "day": day, "windows": [], "busy": []}
    if state["status"] == "ok":
        busy, windows = _calendar_day(state["events"], now, 0 if day == "today" else 1)
        out["busy"] = [{"start": _calendar_iso(s), "end": _calendar_iso(e)} for s, e in busy]
        out["windows"] = [
            {"start": _calendar_iso(s), "end": _calendar_iso(e),
             "minutes": int((e.astimezone(timezone.utc) - s.astimezone(timezone.utc)).total_seconds() // 60)}
            for s, e in windows
        ]
    return jsonify(out)


def _workout_recommendation_fingerprint():
    """Fingerprint inputs that should force a new next-workout plan."""
    today_s = _today_str()
    wearable_facts = [
        fact
        for day, by_metric in sorted(_preferred_facts(_FINGERPRINT_FACT_METRICS, days=7).items())
        for _metric, fact in sorted(by_metric.items())
    ] + _hrv_facts()

    fit = _schedule_fit()
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
        wearable_facts=wearable_facts,
        readiness=_readiness_with_basis()[0],
        schedule_fit={"status": fit["status"], "plan_minutes": fit["plan_minutes"]},
    )


@app.route('/api/next-workout')
def api_next_workout():
    """Return only the active workout prescription for gym execution."""
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT
    fingerprint = _workout_recommendation_fingerprint()
    today_s = _today_str()
    current_plan = _same_day_preserved_workout_plan(today_s)
    preserved_plan = current_plan
    lookup_token = None
    if not current_plan:
        current_plan = _current_workout_plan_for_fingerprint(
            fingerprint,
            return_lookup_token=True,
        )
        if not current_plan:
            lookup_token = current_plan
    training_recommendation = _current_workout_training_recommendation()
    open_wearables_facts = _open_wearables_recommendation_facts()
    guarded_training_recommendation, open_wearables_modifier = _apply_open_wearables_recommendation_guard(
        training_recommendation,
        open_wearables_facts,
    )
    if not current_plan:
        generated_plan = generate_next_workout(
            WORKOUTS,
            SORENESS_DATA,
            training_recommendation=guarded_training_recommendation,
            consume_cardio_rotation=False,
        )
        if lookup_token is not None:
            generated_plan = _copy_workout_plan_cas_identity(lookup_token, generated_plan)
        persisted = _persist_current_workout_plan(generated_plan, fingerprint)
        if persisted is _WORKOUT_PLAN_PERSISTENCE_CONFLICT:
            # Read-only view: like /api/dashboard, serve the generated plan unpersisted.
            current_plan = _current_workout_plan_for_fingerprint(
                fingerprint,
                durable_only=True,
            ) or generated_plan
        else:
            current_plan = persisted or generated_plan
    freshness = _compute_data_freshness()
    # The Open Wearables recovery cap is a display-time transform only: the
    # durable plan was persisted above and must stay the base plan (FIT-256
    # finding 2), so a transient reading never ratchets into a stored plan.
    display_workout = _wearable_adjusted_for_display(current_plan, open_wearables_modifier)
    return jsonify({
        "next_workout": _workout_with_auth_scope(display_workout),
        "schedule_fit": _schedule_fit(preserved_plan),
        "recommendation_sources": {
            **_recommendation_sources_payload(
                freshness,
                open_wearables_facts,
                open_wearables_modifier,
            ),
            "wearable_sources": _wearable_sources_payload(freshness),
        },
    })


@app.route('/gym-now')
def gym_now():
    """Emergency no-app-shell workout view for stale mobile/PWA caches."""
    global LAST_WORKOUT_RECOMMENDATION, LAST_WORKOUT_RECOMMENDATION_FINGERPRINT
    fingerprint = _workout_recommendation_fingerprint()
    workout = _same_day_preserved_workout_plan(_today_str())
    if not workout:
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
    # Render the wearable-adjusted (RPE-capped) view so /gym-now
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
    _refresh_wearable_facts()  # before any factor or fingerprint reads stored facts
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
    sleep_debt = calculate_sleep_debt(days=7)
    recovery_bonus = calculate_recovery_bonus(RECOVERY_DATA, hours=48)
    hrv_trend = get_recent_hrv_trend()

    # Body stats
    body_stats = {}
    if BODY_DATA:
        sorted_body = _safe_dated_body_entries()
        latest_weight = next(
            (entry["weight_lbs"] for entry in sorted_body if entry.get("weight_lbs") is not None),
            None,
        )
        latest_body_fat = next(
            (entry["body_fat_pct"] for entry in sorted_body if entry.get("body_fat_pct") is not None),
            None,
        )
        body_stats["latest_weight"] = latest_weight
        body_stats["latest_body_fat"] = latest_body_fat

        # 30-day weight change
        today = datetime.now().date()
        thirty_days_ago = today - timedelta(days=30)
        old_entries = [
            entry for entry in sorted_body
            if entry.get("weight_lbs") is not None
            and datetime.strptime(entry["date"], "%Y-%m-%d").date() <= thirty_days_ago
        ]
        if old_entries:
            oldest_in_range = sorted(old_entries, key=lambda x: x.get("date") or "")[-1]
            old_weight = oldest_in_range.get("weight_lbs")
            if old_weight is not None and body_stats["latest_weight"] is not None:
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
            recent_7 = [entry for entry in sorted_body if entry.get("weight_lbs") is not None][:7]
            if len(recent_7) >= 3:
                weights = [entry["weight_lbs"] for entry in recent_7]
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
    readiness_val = _readiness_with_basis()[0]
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
    next_workout = _same_day_preserved_workout_plan(today_s)
    using_stored_customization = next_workout is not None
    lookup_token = None
    if not next_workout:
        next_workout = _current_workout_plan_for_fingerprint(
            fingerprint,
            return_lookup_token=True,
        )
        if not next_workout:
            lookup_token = next_workout
    if not next_workout or next_workout.get("_fit136_lightweight_no_ow"):
        next_workout = generate_next_workout(
            WORKOUTS,
            SORENESS_DATA,
            training_recommendation=guarded_dashboard_recommendation,
            consume_cardio_rotation=False,
        )
        if lookup_token is not None:
            next_workout = _copy_workout_plan_cas_identity(lookup_token, next_workout)
    freshness = _compute_data_freshness()
    # Persist the base/programmed plan BEFORE the display-time recovery cap
    # so a transient reading never ratchets into the stored plan (FIT-256
    # finding 2).
    if not using_stored_customization:
        persisted = _persist_current_workout_plan(next_workout, fingerprint)
        if persisted is _WORKOUT_PLAN_PERSISTENCE_CONFLICT:
            # A concurrent log may have deleted the plan; serve the generated
            # plan unpersisted rather than failing the read-only dashboard.
            next_workout = _current_workout_plan_for_fingerprint(
                fingerprint,
                durable_only=True,
            ) or next_workout
        else:
            next_workout = persisted or next_workout
    next_workout = _wearable_adjusted_for_display(next_workout, open_wearables_modifier)
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
        "schedule_fit": _schedule_fit(next_workout if using_stored_customization else None),
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
        "advanced_kpis": {
            "personal_records": prs,
            "consistency": consistency,
            "push_pull_balance": push_pull,
            "deload_check": deload,
            "injury_risk": injury_risk,
            "summary_stats": summary_stats
        },
        "freshness": freshness,
        "wearable_sources": _wearable_sources_payload(freshness),
        "recommendation_sources": _recommendation_sources_payload(
            freshness,
            open_wearables_facts,
            open_wearables_modifier,
        ),
    })


@app.route('/api/vitals')
def api_vitals():
    """Vitals panel data: weight (local), HR/sleep/activity (Open Wearables)."""
    current_weight = None
    body_fat = None
    if BODY_DATA:
        sorted_body = _safe_dated_body_entries()
        current_weight = next(
            (entry["weight_lbs"] for entry in sorted_body if entry.get("weight_lbs") is not None),
            None,
        )
        body_fat = next(
            (entry["body_fat_pct"] for entry in sorted_body if entry.get("body_fat_pct") is not None),
            None,
        )

    trend_7d = _body_trend(7)
    trend_30d = _body_trend(30)
    change_7d = _trend_change(trend_7d)
    change_30d = _trend_change(trend_30d)

    _refresh_wearable_facts()
    facts = _preferred_facts(
        {"resting_heart_rate", "steps", "active_calories", "active_minutes"}, days=7
    )

    def latest(metric):
        return next((facts[day][metric] for day in sorted(facts, reverse=True) if metric in facts[day]), None)

    hr_trend = [
        {"date": day, "resting": facts[day]["resting_heart_rate"]["value"], "average": None}
        for day in sorted(facts) if "resting_heart_rate" in facts[day]
    ]
    steps_vals = [facts[day]["steps"]["value"] for day in facts if "steps" in facts[day]]
    steps_avg_7d = int(round(sum(steps_vals) / len(steps_vals))) if steps_vals else None
    sleep_rows = [row for row in _sleep_rows_from_facts(days=7) if _is_valid_nightly_sleep_row(row)]
    last_night = None
    if sleep_rows:
        row = sleep_rows[0]
        last_night = {
            "date": row["day"],
            "total_sleep_min": row["total_sleep_min"],
            "total_hours": round(row["total_sleep_min"] / 60.0, 2),
            "deep_sleep_min": row["deep_sleep_min"],
            "rem_sleep_min": row["rem_sleep_min"],
            "light_sleep_min": row["light_sleep_min"],
            "awake_min": row["awake_time_min"],
            "efficiency_pct": row["efficiency"],
            "sleep_score": None,
            "quality_score": None,
        }
    avg_7d_hours = (
        round(sum(r["total_sleep_min"] for r in sleep_rows) / len(sleep_rows) / 60.0, 2) if sleep_rows else None
    )
    value_of = lambda fact: fact["value"] if fact else None
    resting = latest("resting_heart_rate")
    steps = latest("steps")
    resting_bpm = value_of(resting)
    steps_today = value_of(steps)
    active_calories_today = value_of(latest("active_calories"))
    active_minutes_today = value_of(latest("active_minutes"))
    quality_score = None
    sources = {
        "heart_rate": resting["provider"] if resting else None,
        "activity": steps["provider"] if steps else None,
        "sleep": sleep_rows[0]["provider"] if sleep_rows else None,
    }
    average_bpm = None

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

    if not all(isinstance(ex, dict) and isinstance(ex.get("machine"), str) and ex["machine"].strip() for ex in exercises):
        return api_error("Each exercise must include machine", 400, code="invalid_field")  # readers index ["machine"]

    for exercise in exercises:
        for set_row in (exercise.get("sets") if isinstance(exercise, dict) else None) or []:
            if isinstance(set_row, dict):
                _normalize_set_rpe(set_row)  # a missing rpe_observed is false

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
        "id": uuid.uuid4().hex[:12],
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
        "id": uuid.uuid4().hex[:12],
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


BODY_TAPE_RANGES = {
    "neck_in": (8.0, 30.0),
    "waist_in": (18.0, 80.0),
    "chest_in": (18.0, 80.0),
    "hips_in": (18.0, 80.0),
    "arms": (5.0, 30.0),
    "legs": (10.0, 50.0),
}


BODY_TAPE_SIDED_FIELDS = ("arms", "legs")


def _body_measurement_date_or_none(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _body_measurement_number_or_none(value, min_v, max_v):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < min_v or number > max_v:
        return None
    return number


def _body_measurement_left_right_or_none(value, min_v, max_v):
    """Return (left, right) for an "a/b" string with both sides in range, else None."""
    if not isinstance(value, str):
        return None
    sides = value.split("/")
    if len(sides) != 2:
        return None
    parsed = [_body_measurement_number_or_none(side, min_v, max_v) for side in sides]
    return None if None in parsed else tuple(parsed)


def _safe_body_history_entry(raw):
    entry = dict(raw)
    entry["date"] = _body_measurement_date_or_none(entry.get("date"))
    entry["weight_lbs"] = _body_measurement_number_or_none(entry.get("weight_lbs"), 50.0, 1000.0)
    entry["body_fat_pct"] = _body_measurement_number_or_none(entry.get("body_fat_pct"), 1.0, 60.0)
    for field, (min_v, max_v) in BODY_TAPE_RANGES.items():
        value = entry.get(field)
        # Legacy arms/legs rows store left/right text such as "11.37/13.62"; keep it only when both sides are in range.
        if field in BODY_TAPE_SIDED_FIELDS and _body_measurement_left_right_or_none(value, min_v, max_v):
            continue
        entry[field] = _body_measurement_number_or_none(value, min_v, max_v)
    return entry


def _safe_dated_body_entries():
    entries = [_safe_body_history_entry(raw) for raw in BODY_DATA if isinstance(raw, dict)]
    return sorted(
        (entry for entry in entries if entry.get("date") is not None),
        key=lambda entry: entry["date"],
        reverse=True,
    )


@app.route('/api/add-body-measurement', methods=['POST'])
def add_body_measurement():
    """Add body composition measurement (weight, body fat %)."""
    data, err = get_json_body(required=True)
    if err:
        return err

    weight_lbs, err2 = _coerce_float(data.get("weight_lbs"), "weight_lbs", min_v=50.0, max_v=1000.0)
    if err2:
        return err2
    if not math.isfinite(weight_lbs):
        return api_error("weight_lbs must be a finite number", 400, code="invalid_field")
    body_fat_pct, err2 = _coerce_float(data.get("body_fat_pct"), "body_fat_pct", min_v=1.0, max_v=60.0, allow_none=True)
    if err2:
        return err2
    if body_fat_pct is not None and not math.isfinite(body_fat_pct):
        return api_error("body_fat_pct must be a finite number", 400, code="invalid_field")
    notes, err2 = _coerce_str(data.get("notes", ""), "notes", required=False, max_len=2000)
    if err2:
        return err2
    raw_date = data.get("date")
    date_s = datetime.now().strftime("%Y-%m-%d") if raw_date in (None, "") else raw_date
    if _body_measurement_date_or_none(date_s) is None:
        return api_error("date must be a valid YYYY-MM-DD date", 400, code="invalid_field")
    tape_context, err2 = _validate_navy_tape_context(data)
    if err2:
        return err2
    tape_values = {}
    for field, (min_v, max_v) in BODY_TAPE_RANGES.items():
        raw_value = data.get(field)
        if field in BODY_TAPE_SIDED_FIELDS and isinstance(raw_value, str) and "/" in raw_value:
            parsed = _body_measurement_left_right_or_none(raw_value, min_v, max_v)
            if parsed is None:
                return api_error(f"{field} must be a number or left/right numbers between {min_v} and {max_v}", 400, code="invalid_field")
            tape_values[field] = f"{parsed[0]:g}/{parsed[1]:g}"
            continue
        value, err2 = _coerce_float(raw_value, field, min_v=min_v, max_v=max_v, allow_none=True)
        if err2:
            return err2
        if value is not None and not math.isfinite(value):
            return api_error(f"{field} must be a finite number", 400, code="invalid_field")
        tape_values[field] = value
    if tape_context:
        # Navy flow owns neck/waist/hips; history stores hips as hips_in.
        tape_values.update(neck_in=tape_context["neck_in"], waist_in=tape_context["waist_in"])
        if tape_context["hip_in"] is not None:
            tape_values["hips_in"] = tape_context["hip_in"]

    entry = {
        "date": date_s,
        "weight_lbs": weight_lbs,
        "body_fat_pct": body_fat_pct,
        **tape_values,
        "notes": notes,
        "created_at": datetime.now().isoformat(),
    }
    if tape_context:
        entry.update(sex=tape_context["sex"], height_in=tape_context["height_in"])

    BODY_DATA.append(entry)
    save_json(BODY_FILE, BODY_DATA)
    return jsonify({"status": "success", "body_measurement": entry})


@app.route('/api/body-history')
def body_history():
    """Return body measurement history with calculated fields."""
    sorted_data = sorted(
        (_safe_body_history_entry(entry) for entry in BODY_DATA if isinstance(entry, dict)),
        key=lambda x: x.get("date") or "",
        reverse=True,
    )
    dated_data = [entry for entry in sorted_data if entry.get("date") is not None]

    # Calculate weight changes and trend
    for entry in sorted_data:
        entry["weight_change"] = None
    for i, entry in enumerate(dated_data[:-1]):
        prev_weight = dated_data[i + 1].get("weight_lbs")
        curr_weight = entry.get("weight_lbs")
        if prev_weight is not None and curr_weight is not None:
            entry["weight_change"] = round(curr_weight - prev_weight, 1)

    # Calculate trend (last 7 entries linear regression)
    recent_7 = dated_data[:7]
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


@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    """Get or update user settings including training goal and available time."""
    global LAST_WORKOUT_RECOMMENDATION
    if request.method == 'GET':
        goal = USER_SETTINGS.get("training_goal", DEFAULT_SETTINGS["training_goal"])
        goal_params = GOAL_PARAMETERS.get(goal, {})
        return jsonify({
            "training_goal": goal,
            "goal_details": goal_params,
            "date_of_birth": USER_SETTINGS.get("date_of_birth", DEFAULT_SETTINGS["date_of_birth"]),
            "sex": USER_SETTINGS.get("sex", DEFAULT_SETTINGS["sex"]),
            "sessions_per_week_target": USER_SETTINGS.get("sessions_per_week_target", DEFAULT_SETTINGS["sessions_per_week_target"]),
            "available_time_minutes": USER_SETTINGS.get("available_time_minutes", DEFAULT_SETTINGS["available_time_minutes"]),
            "training_hours": dict(zip(("start", "end"), (f"{t:%H:%M}" for t in _training_hours()))),
            "target_weight_lbs": USER_SETTINGS.get("target_weight_lbs", DEFAULT_SETTINGS["target_weight_lbs"]),
            "target_body_fat_pct": USER_SETTINGS.get("target_body_fat_pct", DEFAULT_SETTINGS["target_body_fat_pct"]),
            "fatigue_threshold": USER_SETTINGS.get("fatigue_threshold", DEFAULT_SETTINGS["fatigue_threshold"]),
            "equipment_preference": USER_SETTINGS.get("equipment_preference", DEFAULT_SETTINGS["equipment_preference"]),
            "preferred_equipment_brands": USER_SETTINGS.get("preferred_equipment_brands", DEFAULT_SETTINGS["preferred_equipment_brands"]),
            "excluded_exercises": USER_SETTINGS.get("excluded_exercises", DEFAULT_SETTINGS["excluded_exercises"]),
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

        if "training_hours" in data:
            hours = _parse_training_hours(data.get("training_hours"))
            if hours is None:
                return api_error("training_hours needs start before end, as HH:MM", 400, code="invalid_field")
            updated_settings["training_hours"] = dict(zip(("start", "end"), (f"{t:%H:%M}" for t in hours)))

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

    raw_recommendation_id = data.get("recommendation_id")
    if (
        not isinstance(raw_recommendation_id, str)
        or not raw_recommendation_id.strip()
        or len(raw_recommendation_id.strip()) > 256
    ):
        return api_error("recommendation_id must be a non-empty string", 400, code="invalid_field")
    recommendation_id = raw_recommendation_id.strip()

    fingerprint = _workout_recommendation_fingerprint()
    recommendation = _current_workout_plan_for_fingerprint(fingerprint, allow_stale_unsaved=True)
    if not recommendation:
        return api_error("No recent workout recommendation available", 404, code="not_found")
    if recommendation.get("id") != recommendation_id:
        return api_error(
            "Workout plan changed. Refresh the workout and try again.",
            409,
            code="plan_changed",
        )

    exercise_index, err2 = _coerce_int(data.get("exercise_index"), "exercise_index", min_v=0, max_v=10_000)
    if err2:
        return err2
    new_name, err2 = _coerce_str(data.get("new_exercise_name"), "new_exercise_name", required=True, max_len=128)
    if err2:
        return err2

    recommendation = _copy_workout_plan_cas_identity(
        recommendation,
        json.loads(json.dumps(recommendation, default=str)),
    )

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
    recommendation["id"] = _new_workout_recommendation_id()
    persisted = _persist_current_workout_plan(
        recommendation,
        fingerprint,
        customized=True,
    )
    if persisted is _WORKOUT_PLAN_PERSISTENCE_CONFLICT:
        return api_error(
            "Workout plan changed while this request was in progress. Refresh and retry.",
            409,
            code="workout_plan_conflict",
        )
    recommendation = persisted or recommendation

    # Persist the base plan (above), but return the wearable-adjusted view so an
    # active deload/caution clamps every exercise -- including the untouched ones
    # -- consistently with /api/next-workout (FIT-256 finding 2, display-only).
    return jsonify({"status": "success", "recommendation": _workout_with_display_and_auth_scope(recommendation)})


def _apply_intent_patch(recommendation, intent, goal_params, meso_week, meso_plan, oura_readiness, equipment_pref):
    """Apply a deterministic preset/manual intent patch to the recommendation.

    Every mutation is clamped/validated here, so an intent cannot override safety.
    Returns (patched_recommendation, applied_notes, skipped_notes). Applied
    notes describe actual mutations; skipped notes describe rejected no-ops.
    """
    applied_notes = []
    skipped_notes = []
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
    if rpe_delta != rpe_delta_raw:
        skipped_notes.append(
            f"Clamped: RPE adjustment {rpe_delta_raw:+.1f} to {rpe_delta:+.1f}"
        )
    if deload_active and rpe_delta_raw > 0:
        rpe_delta = min(0.0, rpe_delta)  # deload can only reduce
        skipped_notes.append(f"Ignored: RPE increase (+{rpe_delta_raw:+.1f}) — deload week")
    if oura_readiness is not None and oura_readiness < 60 and rpe_delta_raw > 0:
        rpe_delta = min(0.0, rpe_delta)
        skipped_notes.append(f"Ignored: RPE increase (+{rpe_delta_raw:+.1f}) — readiness {oura_readiness}/100")

    # ── 3) Clamp sets_delta_pct to ±20; no upward on deload/low readiness.
    sets_delta_raw = float(intent.get("sets_delta_pct") or 0)
    sets_delta = max(-20.0, min(20.0, sets_delta_raw))
    if sets_delta != sets_delta_raw:
        skipped_notes.append(
            f"Clamped: sets adjustment {sets_delta_raw:+.0f}% to {sets_delta:+.0f}%"
        )
    if deload_active or (oura_readiness is not None and oura_readiness < 60):
        sets_delta = min(0.0, sets_delta)
        if sets_delta_raw > 0:
            skipped_notes.append("Ignored: sets increase — deload or low readiness")

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
                applied_notes.append(f"Removed: {ex_name} — {', '.join(reason_bits)}")
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
                applied_notes.append(f"Removed: {ex_name} — loads {avoided['side']} {avoided['joint']}")
            else:
                kept.append(ex)
        exercises = kept

    # ── 5) Apply swaps. The request names the muscle; Python picks the exercise.
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
            skipped_notes.append("Ignored: swap requires a target muscle and source exercise or index")
            continue
        if target_muscle in avoid_muscles:
            skipped_notes.append(f"Ignored: swap to {target_muscle} — muscle is avoided")
            continue

        # Find the exercise to replace by name (case-insensitive, contains).
        def _source_matches_plan_exercise(plan_ex):
            if not src_name:
                return False
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
            skipped_notes.append(f"could not locate '{sw.get('replace_exercise')}' in current plan")
            continue

        target_exercise_name = (sw.get("target_exercise") or "").strip()
        requested_exercise = _resolve_exercise_definition(target_exercise_name)
        if target_exercise_name and not requested_exercise:
            skipped_notes.append(f"Ignored: unknown target exercise '{target_exercise_name}'")
            continue

        # Pick a new exercise for target_muscle from the equipment-filtered library,
        # preferring compound movements and rotating off recently-trained exercises.
        library = [
            ex for ex in _filtered_exercise_library(equipment_pref)
            if ex.get("muscle") == target_muscle
            and not _exercise_loads_avoided_joint(ex.get("name"), avoid_joints)
        ]
        if not library:
            skipped_notes.append(f"no exercises available for muscle '{target_muscle}' under current equipment/joint constraints")
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
                skipped_notes.append(
                    f"Ignored: target exercise '{target_exercise_name}' is unavailable under current equipment/joint constraints"
                )
                continue
            if _plan_already_contains(requested_exercise):
                skipped_notes.append(
                    f"Ignored: target exercise '{target_exercise_name}' is already in the current plan"
                )
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
        applied_notes.append(f"Swapped: {old_name} → {picked['name']}{reason_suffix}")

    if avoid_joints:
        kept = []
        for ex in exercises:
            ex_name = ex.get("exercise") or ex.get("name") or ex.get("machine") or "exercise"
            avoided = _exercise_loads_avoided_joint(ex_name, avoid_joints)
            if avoided:
                applied_notes.append(f"Removed: {ex_name} — loads {avoided['side']} {avoided['joint']}")
            else:
                kept.append(ex)
        exercises = kept

    # ── 6) Apply rpe_delta and sets_delta_pct to each exercise, enforcing caps.
    rpe_changed_count = 0
    rpe_unchanged_count = 0
    sets_changed_count = 0
    sets_unchanged_count = 0
    if rpe_delta != 0 or sets_delta != 0:
        for ex in exercises:
            if rpe_delta:
                cur = float(ex.get("rpe_target") or goal_params.get("rpe_target") or 7)
                new_rpe = max(1.0, min(10.0, cur + rpe_delta))
                if new_rpe != cur:
                    ex["rpe_target"] = new_rpe
                    rpe_changed_count += 1
                else:
                    rpe_unchanged_count += 1
            if sets_delta:
                cur = int(ex.get("target_sets") or goal_params.get("sets_per_exercise") or 3)
                new_sets = max(1, round(cur * (1 + sets_delta / 100.0)))
                # Never add sets on deload
                if deload_active:
                    new_sets = min(cur, new_sets)
                if new_sets != cur:
                    ex["target_sets"] = new_sets
                    sets_changed_count += 1
                else:
                    sets_unchanged_count += 1
        if rpe_changed_count:
            suffix = "exercise" if rpe_changed_count == 1 else "exercises"
            applied_notes.append(
                f"RPE adjusted {rpe_delta:+.1f} for {rpe_changed_count} {suffix}"
            )
        if rpe_delta and not rpe_changed_count:
            skipped_notes.append("Ignored: RPE adjustment produced no exercise changes")
        elif rpe_unchanged_count:
            suffix = "exercise" if rpe_unchanged_count == 1 else "exercises"
            limit_phrase = "at its limit" if rpe_unchanged_count == 1 else "at their limits"
            skipped_notes.append(
                f"RPE adjustment skipped for {rpe_unchanged_count} {suffix} {limit_phrase}"
            )
        if sets_changed_count:
            suffix = "exercise" if sets_changed_count == 1 else "exercises"
            applied_notes.append(
                f"Sets adjusted {sets_delta:+.0f}% for {sets_changed_count} {suffix}"
            )
        if sets_delta and not sets_changed_count:
            skipped_notes.append("Ignored: sets adjustment produced no exercise changes")
        elif sets_unchanged_count:
            suffix = "exercise" if sets_unchanged_count == 1 else "exercises"
            rounding_phrase = (
                "kept it unchanged" if sets_unchanged_count == 1 else "kept them unchanged"
            )
            skipped_notes.append(
                f"Sets adjustment skipped for {sets_unchanged_count} {suffix} because rounding {rounding_phrase}"
            )

    # ── 7) Weight guard: cap every exercise at +10% of its recent e1RM-derived load.
    for ex in exercises:
        ex_name = ex.get("exercise") or ""
        prog = progression.get(ex_name) or {}
        e1rm = prog.get("current_e1rm") or prog.get("peak_e1rm") or 0
        if e1rm and ex.get("target_weight"):
            target_w = float(ex.get("target_weight") or 0)
            cap = e1rm * 1.10
            if target_w > cap:
                capped_weight = round(cap, 1)
                if capped_weight != target_w:
                    ex["target_weight"] = capped_weight
                    applied_notes.append(f"Capped: {ex_name} weight → {capped_weight} lb (10% above e1RM ceiling)")

    # ── 8) Duration cap — if user gave a tight time box, trim tail exercises.
    duration_cap = float(intent.get("duration_cap_min") or 0)
    if duration_cap and duration_cap > 0:
        # Included cardio (no include_cardio key) stays in the session unless dropped below.
        cardio = recommendation.get("cardio") or {}
        keep_cardio = cardio.get("include_cardio", True) is not False and not intent.get("drop_cardio")
        cardio_min = float(cardio.get("duration_minutes") or 0) if keep_cardio else 0
        budget = duration_cap - 10 - cardio_min  # warmup+cooldown
        minutes_used = 0
        kept = []
        for ex in exercises:
            ex_minutes = float(ex.get("target_sets") or 3) * time_per_set
            if kept and minutes_used + ex_minutes > budget:  # always keep one exercise
                break
            minutes_used += ex_minutes
            kept.append(ex)
        new_estimated_minutes = int(minutes_used + 10 + cardio_min)
        if len(kept) < len(exercises):
            dropped = ", ".join(ex.get("exercise") or ex.get("name") or "exercise" for ex in exercises[len(kept):])
            fits = new_estimated_minutes <= duration_cap
            tail = f"fits {int(duration_cap)} min window" if fits else f"kept the first exercise; shortest plan is {new_estimated_minutes} min"
            if not fits and cardio_min:
                tail += f" ({int(cardio_min)} min is cardio; \"No cardio\" would shorten it)"
            applied_notes.append(f"Trimmed: {dropped} — {tail}")
            exercises = kept
        old_estimated_minutes = recommendation.get("estimated_minutes")
        recommendation["estimated_minutes"] = new_estimated_minutes
        if old_estimated_minutes != new_estimated_minutes:
            applied_notes.append(
                f"Duration adjusted: {old_estimated_minutes} → {new_estimated_minutes} min"
            )

    # ── 9) Drop cardio if requested.
    cardio = recommendation.get("cardio")
    # Included cardio has no include_cardio key; only the skip shapes carry False.
    if intent.get("drop_cardio") and cardio and cardio.get("include_cardio", True) is not False:
        if not duration_cap and recommendation.get("estimated_minutes") is not None:  # a time box already excluded it
            recommendation["estimated_minutes"] = max(0, int(recommendation["estimated_minutes"] - float(cardio.get("duration_minutes") or 0)))
        recommendation["cardio"] = None
        applied_notes.append("Removed: Cardio finisher — per your request")

    recommendation["exercises"] = exercises
    return recommendation, applied_notes, skipped_notes


_ADJUST_NUMBER_BOUNDS = {"rpe_delta": (-1.0, 1.0), "sets_delta_pct": (-20.0, 20.0), "duration_cap_min": (10, 240)}


def _coerce_adjust_intent(raw):
    """Validate the optional preset edit sent with an adjust request."""
    if raw is None:
        return {}, None
    bad = lambda msg: (None, api_error(msg, 400, code="invalid_intent"))
    if not isinstance(raw, dict):
        return bad("intent must be an object")
    unknown = set(raw) - set(_ADJUST_NUMBER_BOUNDS) - {"drop_cardio", "avoid_muscles"}
    if unknown:
        return bad(f"unknown intent field: {sorted(unknown)[0]}")
    intent = {}
    for key, (low, high) in _ADJUST_NUMBER_BOUNDS.items():
        if key in raw:
            value = raw[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                return bad(f"{key} must be a number from {low} to {high}")
            intent[key] = value
    if "drop_cardio" in raw:
        if not isinstance(raw["drop_cardio"], bool):
            return bad("drop_cardio must be true or false")
        intent["drop_cardio"] = raw["drop_cardio"]
    if "avoid_muscles" in raw:
        muscles = raw["avoid_muscles"]
        if not isinstance(muscles, list) or len(muscles) > 8 or not all(isinstance(m, str) and len(m) <= 32 for m in muscles):
            return bad("avoid_muscles must be a short list of muscle names")
        intent["avoid_muscles"] = muscles
    return intent, None


@app.route('/api/workout/adjust', methods=['POST'])
def adjust_workout():
    """Manual plan adjustment, no model involved: a typed exercise swap
    ("replace X with Y") or a preset edit sent as `intent` (RPE, sets,
    time box, cardio, muscles to avoid). Every change goes through
    _apply_intent_patch, which clamps it and keeps deload and soreness rules.
    Text that is not a recognised swap returns the plan unchanged with
    status='fallback'.
    """
    data, err = get_json_body(required=True)
    if err:
        return err

    constraint, err2 = _coerce_str(data.get("constraint"), "constraint", required=True, max_len=280)
    if err2:
        return err2
    client_intent, err3 = _coerce_adjust_intent(data.get("intent"))
    if err3:
        return err3

    fingerprint = _workout_recommendation_fingerprint()
    recommendation = _current_workout_plan_for_fingerprint(
        fingerprint,
        allow_stale_unsaved=True,
        return_lookup_token=True,
    )
    if not recommendation:
        lookup_token = recommendation
        generated_plan = generate_next_workout(WORKOUTS, SORENESS_DATA)
        generated_plan = _copy_workout_plan_cas_identity(lookup_token, generated_plan)
        persisted = _persist_current_workout_plan(generated_plan, fingerprint)
        if persisted is _WORKOUT_PLAN_PERSISTENCE_CONFLICT:
            return api_error(
                "Workout plan changed while this request was in progress. Refresh and retry.",
                409,
                code="workout_plan_conflict",
            )
        recommendation = persisted or generated_plan

    goal = recommendation.get("goal") or USER_SETTINGS.get("training_goal", TrainingGoal.HYPERTROPHY.value)
    goal_params = GOAL_PARAMETERS.get(goal, GOAL_PARAMETERS[TrainingGoal.HYPERTROPHY.value])
    sessions_per_week = USER_SETTINGS.get("sessions_per_week_target", 3)
    meso_week = recommendation.get("mesocycle", {}).get("week") or _get_mesocycle_week(WORKOUTS, sessions_per_week)
    meso_plan = MESOCYCLE_PLAN.get(meso_week, MESOCYCLE_PLAN[1])
    oura_readiness = _get_oura_readiness_today()
    equipment_pref = USER_SETTINGS.get("equipment_preference", "machines_only")
    deterministic_swap = None if client_intent else _build_deterministic_adjust_swap(constraint, recommendation, equipment_pref)

    if not (client_intent or deterministic_swap):
        return jsonify({
            "status": "fallback",
            "reason": "No exercise swap recognised in that request",
            "recommendation": _workout_with_display_and_auth_scope(recommendation),
            "summary": None,
            "applied_notes": [],
            "skipped_notes": [],
        })

    payload = _deterministic_adjust_payload(
        recommendation,
        constraint,
        deterministic_swap,
        goal_params,
        meso_week,
        meso_plan,
        oura_readiness,
        equipment_pref,
        "manual",
        intent=client_intent,
    )
    persisted = _persist_current_workout_plan(
        payload["recommendation"],
        fingerprint,
        customized=payload.get("result_kind") == "changed",
    )
    if persisted is _WORKOUT_PLAN_PERSISTENCE_CONFLICT:
        return api_error(
            "Workout plan changed while this request was in progress. Refresh and retry.",
            409,
            code="workout_plan_conflict",
        )
    payload["recommendation"] = persisted or payload["recommendation"]
    return jsonify(_payload_with_recommendation_display_scope(payload))


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


# ==================== OPEN WEARABLES INTEGRATION ====================

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
    if not _save_open_wearables_local_config(next_config):
        return None, "config_save_failed"
    if mapping_changed:
        _clear_open_wearables_profile_cache(profile_key=profile_key)
    _apply_open_wearables_runtime_config(next_config)
    return {
        "user_id": user_id,
        "user_state": user_state,
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
    state = (urllib.parse.parse_qs(parsed.query).get("state") or [""])[0]
    remember_trusted_no_login_oauth_state(state)
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


@app.route('/api/whoop/callback')
def open_wearables_whoop_callback():
    """WHOOP's OAuth redirect (registered with the WHOOP app) lands here and is
    handed to Open Wearables, which owns the connection. No tokens are kept."""
    state = str(request.args.get("state") or "").strip()
    code = str(request.args.get("code") or "").strip()
    accept = request.headers.get("Accept", "").lower()
    if not state or not code:
        response = api_error("state and code are required", 400, code="missing_oauth_params")
    elif not _open_wearables_complete_oauth_callback("whoop", code, state):
        response = api_error("OAuth state is invalid or expired.", 400, code="invalid_state")
    elif "text/html" in accept and "application/json" not in accept:
        response = redirect("/#settings")
    else:
        response = jsonify({"status": "success", "provider": "whoop", "source": "open_wearables"})
    body, status = response if isinstance(response, tuple) else (response, None)
    body.headers["Cache-Control"] = "no-store"  # the code and state are in the URL
    body.headers["Pragma"] = "no-cache"
    body.headers["Referrer-Policy"] = "no-referrer"
    return (body, status) if status else body


def _system_local_zone():
    zone_name = str(os.environ.get("TZ") or "").lstrip(":")
    zoneinfo_marker = "/zoneinfo/"
    if zone_name and os.path.isabs(zone_name):
        resolved_zone_path = os.path.realpath(zone_name)
        zone_name = (
            resolved_zone_path.split(zoneinfo_marker, 1)[1]
            if zoneinfo_marker in resolved_zone_path
            else ""
        )
    if not zone_name:
        localtime_path = os.path.realpath("/etc/localtime")
        if zoneinfo_marker in localtime_path:
            zone_name = localtime_path.split(zoneinfo_marker, 1)[1]
    if zone_name:
        try:
            return ZoneInfo(zone_name)
        except (ValueError, ZoneInfoNotFoundError):
            pass
    return None


def _open_wearables_window_bounds(*, now=None, local_zone=None, days=7):
    zone = local_zone or _system_local_zone()
    if zone is None and now is not None and isinstance(now.tzinfo, ZoneInfo):
        zone = now.tzinfo
    local_now = (now or datetime.now(zone)).astimezone(zone)
    start_day = local_now.date() - timedelta(days=days - 1)
    end_day = local_now.date() + timedelta(days=1)
    if zone is None:
        # Naive astimezone() asks the operating system for each target instant,
        # preserving DST rules even when /etc/localtime is a copied tzfile.
        window_start = datetime(start_day.year, start_day.month, start_day.day).astimezone()
        window_end = datetime(end_day.year, end_day.month, end_day.day).astimezone()
    else:
        window_start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=zone)
        window_end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=zone)
    return window_start.isoformat(), window_end.isoformat()


def fetch_open_wearables_data(days=7, now=None, timeout_s=6):
    """Fetch coaching-safe wearable domains from the Open Wearables bridge (best-effort).

    ``now`` moves the window for a history backfill; only the live window feeds the plan marker.
    """
    missing = _missing_open_wearables_config()
    if missing:
        return {
            "sleep": None,
            "sleep_summary": None,
            "workouts": None,
            "activity_summary": None,
            "recovery_summary": None,
            "body_summary": None,
            "_sleep_summary_snapshot_complete": False,
            "_workout_snapshot_complete": False,
            "fetched_at": datetime.now().astimezone().isoformat(),
            "errors": {"config": f"missing:{','.join(missing)}"},
        }

    token = _get_ow_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    start_at, end_at = _open_wearables_window_bounds(days=days, now=now)
    event_range_query = urllib.parse.urlencode(
        {"start_date": start_at, "end_date": end_at, "limit": 100}
    )
    summary_range_query = urllib.parse.urlencode({
        "start_date": datetime.fromisoformat(start_at).date().isoformat(),
        "end_date": datetime.fromisoformat(end_at).date().isoformat(),
        "limit": 100,
    })

    endpoints = {
        "sleep": f"{_open_wearables_user_base()}/events/sleep?{event_range_query}",
        "sleep_summary": f"{_open_wearables_user_base()}/summaries/sleep?{summary_range_query}",
        "workouts": f"{_open_wearables_user_base()}/events/workouts?{event_range_query}",
        "activity_summary": f"{_open_wearables_user_base()}/summaries/activity?{summary_range_query}",
        "recovery_summary": f"{_open_wearables_user_base()}/summaries/recovery?{summary_range_query}",
        "body_summary": f"{_open_wearables_user_base()}/summaries/body",
    }

    result = {
        "sleep": None,
        "sleep_summary": None,
        "workouts": None,
        "activity_summary": None,
        "recovery_summary": None,
        "body_summary": None,
        "_sleep_summary_snapshot_complete": False,
        "_sleep_summary_query": {
            "start_date": datetime.fromisoformat(start_at).date().isoformat(),
            "end_date": datetime.fromisoformat(end_at).date().isoformat(),
        },
        "_workout_snapshot_complete": False,
        "_workout_query": {
            "start_at": start_at,
            "end_at": end_at,
        },
        "fetched_at": datetime.now().astimezone().isoformat(),
        "errors": {},
    }

    if not token:
        result["errors"]["auth"] = "missing_token"
        return result

    def fetch_paginated_pages(url, domain):
        combined = []
        current_url = url
        first_payload = None
        for _ in range(5):
            payload = _ow_request(current_url, headers=headers, timeout_s=timeout_s)
            if first_payload is None:
                first_payload = payload
            if not isinstance(payload, dict):
                raise RuntimeError(f"open_wearables_{domain}_pagination_payload_invalid")
            row_key = next((key for key in ("data", "events", "items") if key in payload), None)
            if row_key is None or not isinstance(payload.get(row_key), list):
                raise RuntimeError(f"open_wearables_{domain}_pagination_payload_invalid")
            combined.extend(payload[row_key])
            raw_pagination = payload.get("pagination")
            if raw_pagination is None:
                pagination_complete = False
                terminal_page = True
                cursor = None
            elif not isinstance(raw_pagination, dict):
                raise RuntimeError(f"open_wearables_{domain}_pagination_payload_invalid")
            else:
                has_more = raw_pagination.get("has_more")
                cursor = raw_pagination.get("next_cursor")
                if has_more is True and (cursor is None or not str(cursor).strip()):
                    raise RuntimeError(f"open_wearables_{domain}_pagination_cursor_missing")
                if has_more not in {True, False}:
                    raise RuntimeError(f"open_wearables_{domain}_pagination_payload_invalid")
                pagination_complete = has_more is False
                terminal_page = not has_more
            if terminal_page:
                result_payload = dict(first_payload)
                for row_key in ("events", "items"):
                    result_payload.pop(row_key, None)
                result_payload["data"] = combined
                result_payload["_pagination_complete"] = pagination_complete
                return result_payload
            current_url = f"{url}&cursor={urllib.parse.quote(str(cursor), safe='')}"
        raise RuntimeError(f"open_wearables_{domain}_pagination_limit")

    for key, url in endpoints.items():
        try:
            payload = (
                _ow_request(url, headers=headers, timeout_s=timeout_s)
                if key == "body_summary"
                else fetch_paginated_pages(url, key)
            )
            if isinstance(payload, dict):
                pagination_complete = payload.pop("_pagination_complete", False)
                if key == "workouts":
                    result["_workout_snapshot_complete"] = pagination_complete is True
                elif key == "sleep_summary":
                    result["_sleep_summary_snapshot_complete"] = pagination_complete is True
            result[key] = payload
        except Exception as e:
            result["errors"][key] = str(e)
            result[key] = None
    if now is None:
        _store_open_wearables_recommendation_marker(result)
    return result


def _system_local_timezone():
    """Return system-local transition rules when an IANA TZ key is unavailable."""
    try:
        with open("/etc/localtime", "rb") as timezone_file:
            return ZoneInfo.from_file(timezone_file)
    except (OSError, ValueError):
        return datetime.now().astimezone().tzinfo


def _stored_open_wearables_workouts():
    """History rows from stored workout facts; never calls Open Wearables, so an outage cannot fail History."""
    rows = open_wearables_hub.stored_workouts(WEARABLE_FACTS_DB_FILE, _open_wearables_profile_key())
    return [{**normalize_history_item(row, source="open_wearables"), "source_label": row["source_label"]} for row in rows]


def _extract_open_wearables_main_sleeps(payload):
    """Every main-sleep (non-nap) event in the payload, oldest first, with its metrics."""
    sleeps = []
    for event in sorted(_extract_open_wearables_sleep_events(payload), key=lambda e: e.get("event_time") or datetime.min):
        if event.get("is_nap") is True:
            continue
        event_time = event.get("event_time")
        duration = event.get("duration_min")
        avg_hr = event.get("avg_hr")
        sleeps.append({
            "duration_min": int(round(duration)) if duration is not None else None,
            "avg_hr": int(round(avg_hr)) if avg_hr is not None else None,
            "event_time": event_time.isoformat() if event_time else None,
            "observed_at": event.get("observed_at"),
            "recent": bool(event_time) and (datetime.now() - event_time) <= timedelta(hours=36),
            "raw": event.get("raw"),
            "stages_min": event.get("stages_min") or {},
            "efficiency_percent": event.get("efficiency_percent"),
            "is_nap": event.get("is_nap"),
        })
    return sleeps


def _extract_open_wearables_sleep(payload):
    """The most recent main-sleep event, or None."""
    sleeps = _extract_open_wearables_main_sleeps(payload)
    return sleeps[-1] if sleeps else None


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

    def _observed_at(ev):
        for key in ("end_time", "endTime", "end", "timestamp", "created_at", "start_time", "startTime", "start", "date"):
            value = ev.get(key)
            if value is not None:
                return str(value)
        return None

    parsed = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        dt = _event_time(ev)
        if dt is None:
            continue
        duration = ev.get("sleep_duration_seconds")
        duration_is_seconds = duration is not None
        if duration is None:
            for key in ("duration_min", "duration_minutes", "sleep_duration_min", "total_sleep_min", "duration", "sleep_duration"):
                if ev.get(key) is not None:
                    duration = ev.get(key)
                    break
        if duration is None and ev.get("duration_seconds") is not None:
            duration = ev.get("duration_seconds")
            duration_is_seconds = True
        try:
            duration = float(duration) if duration is not None else None
        except Exception:
            duration = None
        if duration is not None and not math.isfinite(duration):
            duration = None
        if duration is not None and (duration_is_seconds or duration > 1000):
            duration = duration / 60.0

        stages = ev.get("stages") or {}
        stage_minutes = {}
        if isinstance(stages, dict):
            for key in ("deep", "rem", "light", "awake"):
                val = stages.get(key)
                if val is None:
                    val = stages.get(f"{key}_minutes")
                try:
                    val = float(val) if val is not None else None
                except Exception:
                    val = None
                if val is not None and not math.isfinite(val):
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
        if avg_hr is not None and not math.isfinite(avg_hr):
            avg_hr = None

        efficiency = ev.get("efficiency_percent")
        try:
            efficiency = float(efficiency) if efficiency is not None else None
        except Exception:
            efficiency = None
        if efficiency is not None and not math.isfinite(efficiency):
            efficiency = None

        parsed.append({
            "event_time": dt,
            "observed_at": _observed_at(ev),
            "duration_min": duration,
            "stages_min": stage_minutes,
            "avg_hr": avg_hr,
            "efficiency_percent": efficiency,
            "is_nap": ev.get("is_nap") if isinstance(ev.get("is_nap"), bool) else None,
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
        active_calories = item.get("active_calories_kcal")
        if active_calories is None:
            active_calories = item.get("active_calories")
        if active_calories is None:
            active_calories = item.get("calories_active")
        active_minutes = item.get("active_minutes")
        if active_minutes is None:
            active_minutes = item.get("active_min")
        if active_minutes is None:
            active_minutes = item.get("active_duration_min")
        distance = item.get("distance_meters")
        if distance is None:
            distance = item.get("distance")
        try:
            steps = int(float(steps)) if steps is not None else None
        except Exception:
            steps = None
        try:
            active_calories = float(active_calories) if active_calories is not None else None
        except Exception:
            active_calories = None
        try:
            active_minutes = int(float(active_minutes)) if active_minutes is not None else None
        except Exception:
            active_minutes = None
        try:
            distance = float(distance) if distance is not None else None
        except Exception:
            distance = None

        summaries.append({
            "date": dt.date(),
            "resting": resting,
            "average": average,
            "steps": steps,
            "active_calories": active_calories,
            "active_minutes": active_minutes,
            "distance": distance,
            "raw": item,
        })
    return summaries


def _body_trend(days: int):
    if not BODY_DATA:
        return []
    today = datetime.now().date()
    start = today - timedelta(days=days - 1)
    entries = []
    for e in _safe_dated_body_entries():
        date_s = e["date"]
        dt = _parse_iso_date_or_datetime(date_s)
        if not dt:
            continue
        d = dt.date()
        if d < start or d > today:
            continue
        weight = e.get("weight_lbs")
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


_DEBUG_TIMING_ENDPOINTS = {
    "/api/dashboard",
    "/api/wearables/today",
    "/api/recommendation/smart",
    "/api/sleep/summary",
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


@app.route('/api/open-wearables/check-sync', methods=['POST'])
def health_sync():
    """Fetch redacted Open Wearables metadata without writing wearable facts.

    Durable sync callers must use ``/api/open-wearables/sync``.
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


def _store_wearable_facts_from_open_wearables(data):
    return open_wearables_hub.store_wearable_facts(
        data,
        db_file=WEARABLE_FACTS_DB_FILE,
        profile_key=_open_wearables_profile_key(),
        activity_extractor=_extract_open_wearables_activity_summaries,
        sleep_extractor=_extract_open_wearables_main_sleeps,
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


def _recommendation_sources_payload(freshness, open_wearables_facts, open_wearables_modifier, source_inputs=None):
    open_wearables_modifier = open_wearables_modifier or {}
    open_wearables_source = build_open_wearables_recommendation_source(
        freshness.get("open_wearables"),
        facts=open_wearables_facts,
        modifier=open_wearables_modifier,
    )
    payload = {"open_wearables": open_wearables_source}
    if source_inputs is not None:
        payload["source_proof"] = build_recommendation_source_proof(
            freshness=freshness,
            open_wearables_source=open_wearables_source,
            source_inputs={
                "open_wearables_facts": open_wearables_facts,
                "open_wearables_modifier_applied": bool(open_wearables_modifier.get("applied")),
                **(source_inputs or {}),
            },
        )
    return payload


@app.route('/api/open-wearables/status')
def open_wearables_status_api():
    providers, provider_error = _fetch_open_wearables_provider_statuses()
    return jsonify(_open_wearables_public_status(
        providers=providers,
        error_code=provider_error if provider_error != "missing_config" else None,
    ))


@app.route('/api/open-wearables/workouts')
def open_wearables_workouts_api():
    workouts = _stored_open_wearables_workouts()
    return jsonify({"workouts": workouts, "total": len(workouts)})


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
    sources = _wearable_sources_payload(freshness)
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


def _wearable_sources_payload(freshness):
    return [_open_wearables_status_source()]


def _latest_fact_value(facts, metric):
    return next((facts[day][metric] for day in sorted(facts, reverse=True) if metric in facts[day]), None)


def _domain_sources(facts):
    """Provider, date and freshness of each domain's newest preferred fact: the display line and the proof's fallback."""
    keys = {"sleep": ("sleep_duration",), "recovery": ("recovery_score",) + _HRV_METRICS + ("resting_heart_rate",),
            "activity": ("active_minutes", "steps", "active_calories")}
    sources = {}
    for domain, metrics in keys.items():
        newest = max(
            (f for metric in metrics if (f := _latest_fact_value(facts, metric))),
            key=lambda f: f["date"],
            default=None,
        )
        if newest:
            sources[domain] = {k: newest[k] for k in ("provider", "via", "date", "freshness", "conflict_state")}
    return sources


@app.route('/api/wearables/today')
def wearables_today():
    """Today's readiness, HRV, resting HR, sleep and activity from stored wearable facts.

    ``?refresh=true`` forces a live Open Wearables fetch first; an outage serves stored facts.
    """
    _refresh_wearable_facts(force=request.args.get('refresh', '').lower() == 'true')
    facts = _preferred_facts(
        {"resting_heart_rate", "steps", "active_calories", "active_minutes", "temperature_deviation",
         "sleep_duration", "recovery_score"} | set(_HRV_METRICS),
        days=7,
    )
    value_of = lambda metric: (_latest_fact_value(facts, metric) or {}).get("value")
    hrv = (_hrv_facts() or [{}])[-1].get("value")  # the series the plan reads, never a stray old fact
    sleep_rows = [row for row in _sleep_rows_from_facts(days=7) if _is_valid_nightly_sleep_row(row)]
    night = sleep_rows[0] if sleep_rows else None
    steps = _latest_fact_value(facts, "steps")
    return jsonify({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "available": bool(facts or night),
        "readiness": _get_oura_readiness_today(include_open_wearables=False),
        "sleep_score": None,
        "hrv": hrv,
        "steps": value_of("steps"),
        "active_minutes": value_of("active_minutes"),
        "active_calories": value_of("active_calories"),
        "activity_day": steps["date"] if steps else None,
        "resting_hr": value_of("resting_heart_rate"),
        "temperature_deviation": value_of("temperature_deviation"),
        "sleep_duration_min": night["sleep_duration_min"] if night else None,
        "sleep_breakdown_min": {
            "deep": night["sleep_deep_min"], "rem": night["sleep_rem_min"],
            "light": night["sleep_light_min"], "awake": night["sleep_awake_min"],
        } if night else {"deep": None, "rem": None, "light": None, "awake": None},
        "source": "open_wearables",
        "sources": _domain_sources(facts),
    })


@app.route('/api/wearables/trends')
def wearables_trends():
    """Last 7 days of HRV, steps and sleep from stored facts, plus the HRV trend label."""
    end = datetime.now().date()
    facts = _preferred_facts({"steps", "active_minutes", "resting_heart_rate"}, days=7)
    sleep_by_day = {row["day"]: row for row in _sleep_rows_from_facts(days=7)}
    hrv_by_day = {fact["date"]: fact["value"] for fact in _hrv_facts()}  # one provider, one metric
    series = []
    for day in sorted(set(facts) | set(sleep_by_day) | set(hrv_by_day)):
        sleep = sleep_by_day.get(day)
        value_of = lambda metric: (facts.get(day, {}).get(metric) or {}).get("value")
        series.append({
            "day": day,
            "hrv": hrv_by_day.get(day),
            "steps": value_of("steps"),
            "resting_hr": value_of("resting_heart_rate"),
            "active_minutes": value_of("active_minutes"),
            "sleep_duration_min": sleep["sleep_duration_min"] if sleep else None,
            "nightly_sleep": _is_valid_nightly_sleep_row(sleep) if sleep else False,
        })
    return jsonify({
        "start_date": (end - timedelta(days=6)).strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "hrv_trend": get_recent_hrv_trend() if hrv_by_day else "unknown",
        "series": series,
    })


def _sleep_row_inconsistency_reason(row):
    total_sleep_min = (row or {}).get("total_sleep_min")
    sleep_score = (row or {}).get("sleep_score")
    sleep_type = str((row or {}).get("sleep_type") or "").strip().lower()
    is_nap = sleep_type in {"nap", "rest", "late_nap"}
    if total_sleep_min is not None and total_sleep_min < MIN_NIGHT_SLEEP_MIN:
        if sleep_score is not None and sleep_score >= 70:
            return "duration_score_conflict"
        if not is_nap:
            return "implausible_duration"
    stage_values = [
        (row or {}).get("deep_sleep_min"),
        (row or {}).get("rem_sleep_min"),
        (row or {}).get("light_sleep_min"),
    ]
    if total_sleep_min is not None and total_sleep_min >= 60 and all(value is not None for value in stage_values):
        if abs(total_sleep_min - sum(stage_values)) > 30:
            return "duration_stage_conflict"
    return None


def _sleep_row_is_non_nightly(row):
    sleep_type = str((row or {}).get("sleep_type") or "").strip().lower()
    return sleep_type in {"nap", "rest", "late_nap"}


def _is_valid_nightly_sleep_row(row):
    row = dict(row or {})
    if _sleep_row_is_non_nightly(row):
        return False
    if "total_sleep_min" not in row:
        row["total_sleep_min"] = row.get("sleep_duration_min")
    if "deep_sleep_min" not in row:
        row["deep_sleep_min"] = row.get("sleep_deep_min")
    if "rem_sleep_min" not in row:
        row["rem_sleep_min"] = row.get("sleep_rem_min")
    if "light_sleep_min" not in row:
        row["light_sleep_min"] = row.get("sleep_light_min")
    return _sleep_row_inconsistency_reason(row) is None


def _sleep_summary_data_quality(last_night, week_data=None):
    excluded_dates = {
        row.get("day")
        for row in (week_data or [])
        if row.get("day") and _sleep_row_inconsistency_reason(row)
    }
    reason = _sleep_row_inconsistency_reason(last_night)
    if reason:
        if last_night.get("day"):
            excluded_dates.add(last_night["day"])
        return {
            "status": "inconsistent",
            "reason": reason,
            "source": last_night.get("provider"),
            "observed_at": last_night.get("day"),
            "excluded_dates": sorted(excluded_dates),
            "message": "Sleep data is inconsistent. Check the wearable sync.",
        }
    if excluded_dates:
        excluded_dates = sorted(excluded_dates)
        return {
            "status": "partial",
            "reason": "historical_inconsistency",
            "source": next((r.get("provider") for r in week_data or [] if r.get("provider")), None),
            "observed_at": excluded_dates[-1],
            "excluded_dates": excluded_dates,
            "message": "Some sleep history is inconsistent. Check the wearable sync.",
        }
    return {"status": "ok"}


def _bedtime_variance_from_rows(rows):
    """Circular population SD of bedtime minutes (FIT-337) over rows that carry a bedtime."""
    bedtimes = []
    for row in rows or []:
        if row.get("total_sleep_min", 0) is None and row.get("sleep_score") is None:
            continue  # a row with no sleep data says nothing about bedtime
        try:
            when = datetime.fromisoformat(str(row.get("bedtime_start")).replace("Z", "+00:00"))
        except ValueError:
            continue
        bedtimes.append(when.hour * 60 + when.minute)
    if len(bedtimes) < 2:
        return None
    angles = [minutes * math.tau / (24 * 60) for minutes in bedtimes]
    resultant = math.hypot(
        sum(math.cos(a) for a in angles) / len(angles), sum(math.sin(a) for a in angles) / len(angles)
    )
    if resultant < 1e-12:  # opposite times: no defined mean
        return None
    return int(round(math.sqrt(-2 * math.log(min(resultant, 1.0))) * (24 * 60) / math.tau))


@app.route('/api/sleep/summary')
def sleep_summary():
    """Sleep summary for the dashboard: last night and 7-day trends from stored facts."""
    try:
        rows = _sleep_rows_from_facts(days=7)
        # a stale newest night is not "last night" (same rule as calculate_sleep_debt)
        last_night = rows[0] if rows and rows[0]["freshness"] != "stale" else None
        data_quality = _sleep_summary_data_quality(last_night or {}, rows)
        excluded_dates = set(data_quality.get("excluded_dates") or [])
        week_data = [r for r in rows if r["day"] not in excluded_dates and _is_valid_nightly_sleep_row(r)]

        def average(key, digits=0):
            values = [r[key] for r in week_data if r.get(key) is not None]
            return round(sum(values) / len(values), digits) if values else None

        avg_duration = average("total_sleep_min")
        avg_hr = average("avg_heart_rate", 1)
        bedtime_variance = _bedtime_variance_from_rows(week_data)
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
            "last_night": last_night and {
                "date": last_night.get("day"),
                "total_sleep_min": last_night.get("total_sleep_min"),
                "deep_sleep_min": last_night.get("deep_sleep_min"),
                "rem_sleep_min": last_night.get("rem_sleep_min"),
                "light_sleep_min": last_night.get("light_sleep_min"),
                "awake_time_min": last_night.get("awake_time_min"),
                "sleep_score": None,
                "avg_heart_rate": last_night.get("avg_heart_rate"),
                "efficiency": last_night.get("efficiency"),
                "source": last_night.get("provider"),
            },
            "week_average": {
                "duration_min": int(avg_duration) if avg_duration is not None else None,
                "score": None,
                "deep_min": int(average("deep_sleep_min")) if average("deep_sleep_min") is not None else None,
                "rem_min": int(average("rem_sleep_min")) if average("rem_sleep_min") is not None else None,
                "avg_heart_rate": avg_hr,
            },
            "consistency": {
                "bedtime_circular_std_dev_min": bedtime_variance,
                # Compatibility alias for clients predating FIT-337.
                "bedtime_variance_min": bedtime_variance,
                "status": consistency_status,
            },
            "data_quality": data_quality,
            "trend_data": [
                {"date": r["day"], "duration_min": r["total_sleep_min"], "score": None}
                for r in sorted(week_data, key=lambda r: r["day"])
            ],
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== DATA FRESHNESS (wearable facts / Open Wearables) ====================
#
# Stale buckets (server-side; the client never does "Nh ago" math):
#   fresh:   last data point within 24h
#   aging:   24h–48h
#   stale:   >48h
#   missing: no record at all
#
# "Connected" never means "current" — the freshness object is what the dashboard
# uses to drop confidence and swap the brief to lower-confidence copy.

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


def _latest_wearable_fact_freshness(now=None):
    """Freshness of the newest stored sleep/recovery/activity fact (any provider)."""
    try:
        with closing(sqlite3.connect(WEARABLE_FACTS_DB_FILE)) as conn:
            data_row = conn.execute(
                "SELECT MAX(date) FROM wearable_daily_facts WHERE profile_key = ? "
                "AND metric_domain IN ('sleep', 'recovery', 'activity') AND date <= date('now', '+1 day')",
                (_open_wearables_profile_key(),),
            ).fetchone()
            sync_row = conn.execute(
                "SELECT last_sync_attempt FROM wearable_sources WHERE profile_key = ? AND provider_id = 'open_wearables'",
                (_open_wearables_profile_key(),),
            ).fetchone()
    except Exception:
        return ("missing", None, None)
    last_data = (data_row or (None,))[0]
    last_sync = (sync_row or (None,))[0]
    if not last_data:
        return ("missing", None, last_sync)
    return (_classify_freshness(_parse_iso_date_or_datetime(last_data), now=now), last_data, last_sync)


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
    """Per-source freshness: the stored wearable facts and Open Wearables.

    Returns:
        {
          "wearable_facts": {status, last_data_point, last_sync_attempt},
          "open_wearables": hub connectivity,
        }
    """
    now = now or datetime.now()
    facts_status, facts_last_data, facts_last_sync = _latest_wearable_fact_freshness(now)
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
        },
        "wearable_facts": {
            "status": facts_status,
            "last_data_point": facts_last_data,
            "last_sync_attempt": facts_last_sync,
            "source": "open_wearables",
        },
    }
    return freshness


def _push_alert_preview(now=None):
    """Deterministic, non-sending alert contract for FIT-39."""
    freshness = _compute_data_freshness(now=now)
    alerts = []
    source = freshness.get("wearable_facts") or {}
    if source.get("status") in {"aging", "stale", "missing"}:
        alerts.append({
            "type": "stale_wearable_data",
            "source": "wearable_facts",
            "title": "Wearable data needs attention",
            "body": "Open the app to refresh wearable data before trusting today's recommendation.",
            "severity": "warning" if source.get("status") == "stale" else "info",
            "status": source.get("status"),
            "last_data_point": source.get("last_data_point"),
            "last_sync_attempt": source.get("last_sync_attempt"),
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
    wearable_state = (freshness.get("wearable_facts") or {}).get("status")
    wearable_states = [wearable_state] if wearable_state is not None else []
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


def _wearable_factor_rules(readiness, recovery_bonus, hrv_trend, sleep_debt):
    """{domain: whether a wearable-owned rule changed the category}: readiness and sleep debt are sleep, HRV is recovery."""
    effective_readiness = _effective_readiness_from(readiness, recovery_bonus)
    recommendation = "moderate"
    if effective_readiness is not None:
        if effective_readiness < 70:
            recommendation = "recovery"
        elif effective_readiness > 85:
            recommendation = "intensity"

    raw_recommendation = "moderate"
    if readiness is not None:
        if readiness < 70:
            raw_recommendation = "recovery"
        elif readiness > 85:
            raw_recommendation = "intensity"
    applied = {"sleep": recommendation != "moderate" and raw_recommendation == recommendation, "recovery": False}
    if hrv_trend == "declining":
        next_recommendation = _downgrade_training_recommendation_once(recommendation)
        applied["recovery"] = next_recommendation != recommendation
        recommendation = next_recommendation
    if ((sleep_debt or {}).get("debt_minutes") or 0) > 300:
        next_recommendation = _downgrade_training_recommendation_once(recommendation)
        applied["sleep"] = applied["sleep"] or next_recommendation != recommendation
    return applied


@app.route('/api/recommendation/smart')
def smart_recommendation_api():
    """Smart recommendation factoring wearable readiness + HRV trend + recent soreness."""
    today = datetime.now().strftime("%Y-%m-%d")
    _refresh_wearable_facts()

    # Wearable metrics from stored facts (owner's source rule)
    readiness, readiness_basis = _readiness_with_basis()
    sleep_score = None
    hrv_values = _hrv_series(7)
    hrv_facts = _hrv_facts(7) if hrv_values else []  # the facts behind the series, for the source proof
    hrv = hrv_values[-1] if hrv_values else None
    hrv_trend = get_recent_hrv_trend()
    hrv_sample_count = len(hrv_values)

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
    sleep_debt = calculate_sleep_debt(days=7)
    recovery_bonus = calculate_recovery_bonus(RECOVERY_DATA, hours=48)
    recommendation_readiness = readiness
    recommendation_hrv_trend = hrv_trend
    recommendation_sleep_debt = sleep_debt

    effective_readiness = _effective_readiness_from(recommendation_readiness, recovery_bonus)

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
    if recommendation_readiness is not None:
        if effective_readiness is not None and round(float(effective_readiness), 1) != round(float(recommendation_readiness), 1):
            reason_bits.append(f"Readiness {recommendation_readiness} (+{recovery_bonus.get('bonus_points', 0)} recovery bonus → {round(effective_readiness, 1)})")
        else:
            reason_bits.append(f"Readiness {recommendation_readiness}")
    if recommendation_hrv_trend and recommendation_hrv_trend != "unknown":
        reason_bits.append(f"HRV trend {recommendation_hrv_trend}")
    if (recommendation_sleep_debt.get('debt_minutes') or 0) > 0:
        reason_bits.append(f"Sleep debt {recommendation_sleep_debt.get('debt_minutes')} min ({recommendation_sleep_debt.get('status')})")
    if acwr_data.get('chronic_load', 0) > 0:
        reason_bits.append(f"ACWR {acwr_data.get('acwr')} ({acwr_data.get('risk')})")
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
        recommendation_readiness,
        recovery_bonus=recovery_bonus,
        hrv_trend=recommendation_hrv_trend,
        sleep_debt=recommendation_sleep_debt,
        acwr_data=acwr_data,
        last_completed=last_completed,
        last_hours_ago=last_hours_ago,
        weather=weather,
    )
    factor_recommendation = recommendation
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
    fingerprint = _workout_recommendation_fingerprint()
    current_plan = _current_workout_plan_for_fingerprint(fingerprint)
    preserved_plan = _same_day_preserved_workout_plan(today)
    canonical_plan = preserved_plan or current_plan
    if calendar_reason := _schedule_fit(preserved_plan).get("reason"):
        reason_bits.append(calendar_reason)
    if canonical_plan and not _is_lightweight_current_workout_plan(canonical_plan):
        next_workout = dict(canonical_plan)
        next_workout.pop("_user_customized", None)
    next_workout = _wearable_adjusted_for_display(next_workout, open_wearables_modifier)
    recommendation_without_weather, _ = _training_recommendation_from_factors(
        recommendation_readiness,
        recovery_bonus=recovery_bonus,
        hrv_trend=recommendation_hrv_trend,
        sleep_debt=recommendation_sleep_debt,
        acwr_data=acwr_data,
        last_completed=last_completed,
        last_hours_ago=last_hours_ago,
        weather=None,
    )
    hrv_used = (hrv_sample_count >= 4 or recommendation_hrv_trend == "declining") and recommendation_hrv_trend != "unknown"
    domain_fields = {
        "recovery": ["hrv_trend"] if hrv_used else [],
        "sleep": (
            (["readiness_estimate"] if readiness_basis == "sleep_estimate" else [])
            + (["sleep_debt_minutes"] if (recommendation_sleep_debt.get("sample_count") or 0) > 0 else [])
        ),
        "activity": ["active_minutes"] if any(f.get("metric") == "active_minutes" for f in consumed_facts(open_wearables_facts)) else [],
    }
    # Name the fact behind each consumed field; the newest fact of any metric is only the display fallback.
    domain_sources = _domain_sources(_preferred_facts(_PROOF_FACT_METRICS, days=7))
    fact_node = lambda f: {k: f[k] for k in ("provider", "via", "date", "freshness", "conflict_state")}
    if sleep_debt.get("source"):
        domain_sources["sleep"] = sleep_debt["source"]
    if hrv_used and hrv_facts:
        domain_sources["recovery"] = fact_node(hrv_facts[-1])
    # A fact that made the guard fire is what changed the plan, so it takes its domain's provenance slot.
    guard_applied = set(open_wearables_modifier.get("applied_modifiers") or [])
    for domain, field, guard_fact, fired in (
        ("sleep", "sleep_duration", open_wearables_modifier.get("sleep_fact"), "sleep_caution" in guard_applied),
        ("recovery", "recovery_score", open_wearables_modifier.get("recovery_fact"),
         bool(guard_applied & {"recovery_caution", "recovery_deload"})),
        ("activity", "active_minutes", open_wearables_modifier.get("active_fact"), bool(domain_fields["activity"])),
    ):
        if fired and guard_fact:
            guard_provider = fact_provider(guard_fact.get("provider_id"), guard_fact.get("source_provider"))
            node = {
                "provider": guard_provider, "via": "open_wearables", "date": guard_fact.get("date"),
                "freshness": guard_fact.get("freshness"), "conflict_state": guard_fact.get("conflict_state"),
            }
            slot = domain_sources.get(domain, {})
            if any(f != field for f in domain_fields[domain]) and slot.get("provider") != guard_provider:
                # The slot's other fields (Oura's HRV trend or sleep estimate) stay with their provider; the guard fact is credited to its own.
                domain_sources[domain] = {**slot, "guard_source": {**node, "fields_used": [field]}}
                continue
            if field not in domain_fields[domain]:
                domain_fields[domain].append(field)
            if slot.get("provider") != guard_provider:  # same provider: keep its conflict_state
                domain_sources[domain] = node
    weather_fields = []
    if weather and weather.get("available"):
        temperature_field = "feelslike_f" if weather.get("feelslike_f") is not None else "temp_f"
        if weather.get(temperature_field) is not None:
            weather_temperature = float(weather.get(temperature_field))
            weather_fields.append(temperature_field)
            if (
                weather.get("humidity_pct") is not None
                and 90 <= weather_temperature < 95
            ):
                weather_fields.append("humidity_pct")
    wearable_rules = _wearable_factor_rules(
        recommendation_readiness, recovery_bonus, recommendation_hrv_trend, recommendation_sleep_debt,
    )
    source_inputs = {
        "open_wearables_modifier_applied": bool(open_wearables_modifier.get("applied")),
        "domain_sources": domain_sources,
        "domain_fields": domain_fields,
        "domain_modifier_applied": {
            "sleep": wearable_rules["sleep"] or "sleep_caution" in guard_applied,
            "recovery": wearable_rules["recovery"] or bool(guard_applied & {"recovery_caution", "recovery_deload"}),
            "activity": "activity_caution" in guard_applied,
        },
        "weather_fields": weather_fields,
        "weather_status": "fresh" if weather_fields else "missing",
        "weather_modifier_applied": recommendation_without_weather != factor_recommendation,
    }
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
        "reasoning": "; ".join(reason_bits) if reason_bits else "No wearable/soreness data available",
        "freshness": freshness,
        "wearable_sources": _wearable_sources_payload(freshness),
        "recommendation_sources": {
            **_recommendation_sources_payload(
                freshness,
                open_wearables_facts,
                open_wearables_modifier,
                source_inputs,
            ),
            "wearable_sources": _wearable_sources_payload(freshness),
        },
        "next_workout": _workout_with_auth_scope(next_workout),
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


def _normalize_set_rpe(set_row):
    """Normalize optional per-set RPE without blocking workout completion."""
    raw_rpe = set_row.get("rpe")
    rpe = None
    if not isinstance(raw_rpe, bool):
        try:
            candidate = float(raw_rpe)
        except (TypeError, ValueError, OverflowError):
            candidate = None
        if candidate is not None and math.isfinite(candidate) and 1 <= candidate <= 10:
            rpe = int(candidate) if candidate.is_integer() else candidate
    set_row["rpe"] = rpe
    set_row["rpe_observed"] = bool(
        rpe is not None and set_row.get("rpe_observed") is True
    )


def _history_exercises_with_rpe_provenance(exercises):
    """Return history-safe copies, treating legacy RPE targets as defaulted."""
    copied = copy.deepcopy(exercises if isinstance(exercises, list) else [])
    for exercise in copied:
        if not isinstance(exercise, dict):
            continue
        for set_row in exercise.get("sets") or []:
            if isinstance(set_row, dict) and "rpe_observed" not in set_row:
                set_row["rpe_observed"] = False
    return copied


@app.route('/api/history')
def workout_history():
    """Get past workouts with full details."""
    _backfill_history_ids(WORKOUTS, WORKOUTS_FILE, "workout")
    workouts_list = []
    for w in sorted(WORKOUTS, key=lambda x: x["date"], reverse=True):
        total_sets = sum(len(e.get("sets", [])) for e in w.get("exercises", []))
        total_volume = sum(
            s["weight_lbs"] * s["reps"]
            for e in w.get("exercises", [])
            for s in e.get("sets", [])
        )
        history_row = {
            "id": w.get("id"),
            "created_at": w.get("created_at"),
            "date": w["date"],
            "session_type": w.get("session_type", "general"),
            "duration_minutes": w.get("duration_minutes", 0),
            "exercises": _history_exercises_with_rpe_provenance(w.get("exercises", [])),
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
    _backfill_history_ids(WORKOUTS, WORKOUTS_FILE, "workout")
    _backfill_history_ids(CARDIO_DATA, CARDIO_FILE, "cardio")
    _backfill_history_ids(RECOVERY_DATA, RECOVERY_FILE, "recovery")
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
            "exercises": _history_exercises_with_rpe_provenance(w.get("exercises", [])),
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
    """Delete a history entry by its stable ID and type."""
    data, err = get_json_body(required=True)
    if err:
        return err

    entry_type, err2 = _coerce_str(data.get("type"), "type", required=True, max_len=16)
    if err2:
        return err2
    entry_id, err2 = _coerce_str(data.get("id"), "id", required=True, max_len=128)
    if err2:
        return err2

    try:
        if entry_type == "workout":
            rows, filepath = WORKOUTS, WORKOUTS_FILE
        elif entry_type == "cardio":
            rows, filepath = CARDIO_DATA, CARDIO_FILE
        elif entry_type == "recovery":
            rows, filepath = RECOVERY_DATA, RECOVERY_FILE
        else:
            return api_error("Invalid type (expected workout|cardio|recovery)", 400, code="invalid_field")

        original_index = next(
            (index for index, row in enumerate(rows) if row.get("id") == entry_id),
            None,
        )
        if original_index is None:
            return api_error("History entry not found", 404, code="not_found")
        deleted = rows.pop(original_index)
        save_json(filepath, rows)
        return jsonify({"status": "success", "deleted": deleted})
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
    entry_id, err2 = _coerce_str(entry.get("id"), "entry.id", required=True, max_len=128)
    if err2:
        return err2

    try:
        if entry_type == "workout":
            rows, filepath = WORKOUTS, WORKOUTS_FILE
        elif entry_type == "cardio":
            rows, filepath = CARDIO_DATA, CARDIO_FILE
        elif entry_type == "recovery":
            rows, filepath = RECOVERY_DATA, RECOVERY_FILE
        else:
            return api_error("Invalid type (expected workout|cardio|recovery)", 400, code="invalid_field")

        if any(row.get("id") == entry_id for row in rows):
            return api_error("History entry already exists", 409, code="conflict")
        rows.append(entry)
        save_json(filepath, rows)
        return jsonify({"status": "success", "restored": entry})
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
            _normalize_set_rpe(set_row)
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
            # recovery-capped RPE (sets are not clamped), so score against the
            # displayed view. Display-only; nothing is persisted here.
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
        legacy_rpe_entry = copy.deepcopy(workout_entry)
        for exercise in legacy_rpe_entry.get("exercises") or []:
            for set_row in exercise.get("sets") or []:
                set_row.pop("rpe_observed", None)
                if set_row.get("rpe") is None:
                    set_row.pop("rpe", None)
        accepted_fingerprints.add(_workout_sync_fingerprint(legacy_rpe_entry))
        if not fatigue_provided and existing_by_client_id.get("overall_fatigue") == 5:
            legacy_fatigue_entry = copy.deepcopy(workout_entry)
            legacy_fatigue_entry["overall_fatigue"] = 5
            accepted_fingerprints.add(_workout_sync_fingerprint(legacy_fatigue_entry))
            legacy_rpe_entry["overall_fatigue"] = 5
            accepted_fingerprints.add(_workout_sync_fingerprint(legacy_rpe_entry))
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
        }
    }

    filename = f"fitness_backup_{datetime.now().strftime('%Y-%m-%d')}.json"

    return Response(
        json.dumps(backup_data, indent=2, default=str),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


@app.route('/api/import-backup', methods=['POST'])
def import_backup():
    """Import data from a backup JSON file."""
    global WORKOUTS, SORENESS_DATA, CARDIO_DATA, RECOVERY_DATA, USER_SETTINGS, BASELINES_DATA, BODY_DATA, SLEEP_DATA

    try:
        backup_data, err = get_json_body(required=True)
        if err:
            return err

        # Validate backup structure
        if "data" not in backup_data or not isinstance(backup_data.get("data"), dict):
            return api_error("Invalid backup format: missing 'data' object", 400, code="invalid_field")

        data = backup_data["data"]
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
            }
        })
    except Exception as e:
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
            "detail": "Check recovery and sleep"
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
                observed = _observed_set_rpe(s)
                if observed is not None: r.append(observed)
        if r: vals.append(sum(r)/len(r))
        if len(vals)>=n: break
    return vals

@app.route('/api/body-recomp')
def body_recomp():
    hist = sorted(
        (_safe_body_history_entry(entry) for entry in BODY_DATA if isinstance(entry, dict)),
        key=lambda x: x.get('date') or '',
    )
    if not hist:
        return jsonify({"history": [], "summary": {}})
    dated_hist = [entry for entry in hist if entry.get('date') is not None]
    dates=[h.get('date') for h in dated_hist]
    weights=[h.get('weight_lbs') for h in dated_hist]
    bf=[h.get('body_fat_pct') for h in dated_hist]
    roll=_rolling_avg(weights,7)
    lean=[]; fat=[]
    for w,b in zip(weights,bf):
        if w is None or b is None: lean.append(None); fat.append(None)
        else:
            fm = w*(b/100.0); lm=w-fm
            lean.append(round(lm,2)); fat.append(round(fm,2))
    latest=dated_hist[-1] if dated_hist else None
    tw=USER_SETTINGS.get('target_weight_lbs')
    curr=latest.get('weight_lbs') if latest else None
    eta_weeks=None
    recent_weights = weights[-14:]
    if tw and curr and len(recent_weights) == 14 and all(weight is not None for weight in recent_weights):
        first=recent_weights[0]
        weekly=(curr-first)/2.0
        if weekly!=0:
            eta_weeks=round((tw-curr)/weekly,1)
    return jsonify({
        "history": hist, "dates": dates, "weight": weights, "weight_7d_avg": roll,
        "body_fat_pct": bf, "lean_mass_lbs": lean, "fat_mass_lbs": fat,
        "summary": {"latest": latest, "target_weight_lbs": tw, "target_body_fat_pct": USER_SETTINGS.get('target_body_fat_pct'), "eta_weeks": eta_weeks}
    })

def _coerce_body_tape_float(value, field_name, *, min_v, max_v, allow_none=False):
    normalized, err = _coerce_float(
        value,
        field_name,
        min_v=min_v,
        max_v=max_v,
        allow_none=allow_none,
    )
    if err:
        return None, err
    if normalized is not None and not math.isfinite(normalized):
        return None, api_error(f"{field_name} must be a finite number", 400, code="invalid_field")
    return normalized, None


def _validate_navy_tape_context(data, *, default_sex=None):
    """Return normalized Navy tape inputs when the request supplies any."""
    tape_fields = ("sex", "height_in", "hip_in")
    if not any(data.get(field) not in (None, "") for field in tape_fields):
        return None, None

    raw_sex = data.get("sex")
    if raw_sex is None or (default_sex is not None and isinstance(raw_sex, str) and not raw_sex.strip()):
        if default_sex is None:
            return None, api_error("Missing field: sex", 400, code="missing_field")
        raw_sex = default_sex
    if not isinstance(raw_sex, str):
        return None, api_error("sex must be male or female", 400, code="invalid_field")
    sex = raw_sex.strip().lower()
    if sex not in {"male", "female"}:
        return None, api_error("sex must be male or female", 400, code="invalid_field")

    height_in, err = _coerce_body_tape_float(data.get("height_in"), "height_in", min_v=48, max_v=96)
    if err:
        return None, err
    neck_in, err = _coerce_body_tape_float(data.get("neck_in"), "neck_in", min_v=8, max_v=30)
    if err:
        return None, err
    waist_in, err = _coerce_body_tape_float(data.get("waist_in"), "waist_in", min_v=18, max_v=80)
    if err:
        return None, err

    if sex == "male":
        if data.get("hip_in") not in (None, ""):
            return None, api_error("hip_in is only supported for female measurements", 400, code="invalid_field")
        if waist_in <= neck_in:
            return None, api_error("waist_in must be greater than neck_in", 400, code="invalid_field")
        return {
            "sex": sex,
            "height_in": height_in,
            "neck_in": neck_in,
            "waist_in": waist_in,
            "hip_in": None,
        }, None

    hip_in, err = _coerce_body_tape_float(data.get("hip_in"), "hip_in", min_v=BODY_TAPE_RANGES["hips_in"][0], max_v=BODY_TAPE_RANGES["hips_in"][1])
    if err:
        return None, err
    if waist_in + hip_in <= neck_in:
        return None, api_error("waist_in plus hip_in must be greater than neck_in", 400, code="invalid_field")
    return {
        "sex": sex,
        "height_in": height_in,
        "neck_in": neck_in,
        "waist_in": waist_in,
        "hip_in": hip_in,
    }, None


@app.route('/api/body/navy-calc', methods=['POST'])
def navy_calc():
    data, err = get_json_body(required=True)
    if err:
        return err
    tape_context, err = _validate_navy_tape_context(data, default_sex="male")
    if err:
        return err
    if not tape_context:
        return api_error("Missing Navy tape measurements", 400, code="missing_field")

    if tape_context["sex"] == "female":
        bf = (
            163.205 * math.log10(tape_context["waist_in"] + tape_context["hip_in"] - tape_context["neck_in"])
            - 97.684 * math.log10(tape_context["height_in"])
            - 78.387
        )
    else:
        bf = (
            86.010 * math.log10(tape_context["waist_in"] - tape_context["neck_in"])
            - 70.041 * math.log10(tape_context["height_in"])
            + 36.76
        )
    return jsonify({"body_fat_pct": max(3.0, min(60.0, round(bf, 2)))})

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

    def parse_sleep_timestamp(value):
        if value is None or value == '':
            return None,None
        if not isinstance(value, str):
            return None,'invalid_timestamp'
        try:
            if 'T' in value or ' ' in value:
                return ('datetime', datetime.fromisoformat(value)),None
            return ('time', datetime.strptime(value, '%H:%M').time()),None
        except ValueError:
            return None,'invalid_timestamp'

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
        timestamp_error_count=len(errors)
        parsed_start,start_error=parse_sleep_timestamp(e['sleep_start'])
        parsed_end,end_error=parse_sleep_timestamp(e['sleep_end'])
        if start_error:
            errors.append({'row':row_number,'field':'sleep_start','code':start_error})
        if end_error:
            errors.append({'row':row_number,'field':'sleep_end','code':end_error})
        if len(errors)>timestamp_error_count:
            continue
        if bool(parsed_start)!=bool(parsed_end):
            missing_field='sleep_end' if parsed_start else 'sleep_start'
            errors.append({'row':row_number,'field':missing_field,'code':'missing_timestamp'})
            continue
        if parsed_start and parsed_end:
            start_kind,start_value=parsed_start
            end_kind,end_value=parsed_end
            if start_kind!=end_kind:
                errors.append({'row':row_number,'field':'sleep_end','code':'invalid_timestamp'})
                continue
            if start_kind=='datetime':
                try:
                    record_date=datetime.strptime(date, '%Y-%m-%d').date()
                except ValueError:
                    errors.append({'row':row_number,'field':'date','code':'invalid_date'})
                    continue
                try:
                    window_minutes=(end_value-start_value).total_seconds()/60
                except TypeError:
                    errors.append({'row':row_number,'field':'sleep_end','code':'invalid_timestamp'})
                    continue
                if window_minutes<=0:
                    errors.append({'row':row_number,'field':'sleep_end','code':'contradictory_timestamp'})
                    continue
                if window_minutes>1440:
                    errors.append({'row':row_number,'field':'sleep_end','code':'out_of_range'})
                    continue
                start_date=start_value.date()
                start_matches_record_date=(
                    start_date==record_date
                    or (start_date<record_date and (record_date-start_date).days==1)
                )
                if end_value.date()!=record_date or not start_matches_record_date:
                    errors.append({'row':row_number,'field':'date','code':'contradictory_timestamp'})
                    continue
            else:
                try:
                    anchor_date=datetime.strptime(date, '%Y-%m-%d').date()
                except ValueError:
                    errors.append({'row':row_number,'field':'date','code':'invalid_date'})
                    continue
                start_dt=datetime.combine(anchor_date,start_value)
                end_dt=datetime.combine(anchor_date,end_value)
                if end_dt<=start_dt:
                    try:
                        start_dt-=timedelta(days=1)
                    except OverflowError:
                        errors.append({'row':row_number,'field':'date','code':'contradictory_timestamp'})
                        continue
                window_minutes=(end_dt-start_dt).total_seconds()/60
                e['sleep_start']=start_dt.isoformat()
                e['sleep_end']=end_dt.isoformat()
            if window_minutes>1440:
                errors.append({'row':row_number,'field':'sleep_end','code':'out_of_range'})
                continue
            if supplied['time_in_bed_min'] and e['time_in_bed_min']!=int(window_minutes):
                errors.append({'row':row_number,'field':'time_in_bed_min','code':'contradictory_minutes'})
                continue
            if supplied['sleep_duration_min'] and e['sleep_duration_min']>window_minutes:
                errors.append({'row':row_number,'field':'sleep_duration_min','code':'contradictory_minutes'})
                continue
            if stages_supplied and stages>window_minutes:
                errors.append({'row':row_number,'field':'stage_total_min','code':'contradictory_minutes'})
                continue
            if supplied['awake_min'] and e['awake_min']>window_minutes:
                errors.append({'row':row_number,'field':'awake_min','code':'contradictory_minutes'})
                continue
            if stages_supplied and supplied['awake_min'] and stages+e['awake_min']>window_minutes:
                errors.append({'row':row_number,'field':'awake_min','code':'contradictory_minutes'})
                continue
            if supplied['sleep_duration_min'] and supplied['awake_min'] and e['sleep_duration_min']+e['awake_min']>window_minutes:
                errors.append({'row':row_number,'field':'awake_min','code':'contradictory_minutes'})
                continue
        entries.append(e)
    if errors:
        return api_error('Sleep import contains invalid rows', 400, code='invalid_sleep_rows', details=errors)
    with JSON_DATA_LOCK:
        merged={x.get('date'):x for x in SLEEP_DATA}
        for e in entries: merged[e['date']]=e
        _json_restore_sequence(SLEEP_DATA, sorted(merged.values(), key=lambda x:x.get('date')))
        save_json(SLEEP_FILE, SLEEP_DATA)
    return jsonify({'status':'success','imported':len(entries)})

_SLEEP_HISTORY_DAYS = 800


@app.route('/api/sleep/analytics')
def sleep_analytics():
    # Merge wearable and manual sleep per date; the wearable wins only on overlapping dates.
    wearable_rows = [
        {
            'date': r['day'],
            'sleep_start': r['bedtime_start'],
            'sleep_end': r['sleep_end'],
            'sleep_duration_min': r['sleep_duration_min'],
            'deep_sleep_min': r['deep_sleep_min'],
            'rem_sleep_min': r['rem_sleep_min'],
            'light_sleep_min': r['light_sleep_min'],
            'awake_min': r['awake_time_min'],
            'sleep_score': None,
            'efficiency': r['efficiency'],
            'avg_heart_rate': r['avg_heart_rate'],
            'source': r['provider'],
            'via': r['via'],
        }
        for r in _sleep_rows_from_facts(days=_SLEEP_HISTORY_DAYS)
    ]
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
    for row in wearable_rows:
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
    sleep=calculate_sleep_debt(7)
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

    host = os.environ.get('HOST', '127.0.0.1')
    app.run(host=host, port=port, debug=debug_mode, request_handler=SanitizedRequestHandler)
