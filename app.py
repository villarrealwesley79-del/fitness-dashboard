#!/usr/bin/env python3
"""
Fitness Intelligence System - Mobile Web App
Evidence-based resistance training optimization for iOS/Android.
"""

from flask import Flask, render_template, jsonify, request, Response
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import os
import socket
import sqlite3
import urllib.request
import urllib.error
import urllib.parse
import base64
import time
import re

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
from oura_client import (
    OuraClient,
    init_oura_db,
    upsert_oura_daily,
    get_oura_daily,
    get_oura_daily_range,
    compute_hrv_trend,
)
from data_store import init_data_db, add_food_log, clear_food_logs, get_food_logs

app = Flask(__name__)

# ── Auth (must be after app creation) ──────────────────────────
from auth import init_auth
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
# Store data in JSON files in the same directory as the app
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
WORKOUTS_FILE = os.path.join(DATA_DIR, "data_workouts.json")
SORENESS_FILE = os.path.join(DATA_DIR, "data_soreness.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "data_settings.json")
CARDIO_FILE = os.path.join(DATA_DIR, "data_cardio.json")
RECOVERY_FILE = os.path.join(DATA_DIR, "data_recovery.json")
BASELINES_FILE = os.path.join(DATA_DIR, "data_baselines.json")
BODY_FILE = os.path.join(DATA_DIR, "data_body.json")
SLEEP_FILE = os.path.join(DATA_DIR, "data_sleep.json")
NUTRITION_FILE = os.path.join(DATA_DIR, "data_nutrition.json")
OURA_DB_FILE = os.path.join(DATA_DIR, "oura_daily.sqlite3")

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
    try:
        tmp = f"{filepath}.tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str, ensure_ascii=False)
        os.replace(tmp, filepath)
    except IOError as e:
        print(f"Warning: Could not save {filepath}: {e}")


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
        "rep_range": (12, 15),
        "sets_per_exercise": 3,
        "rest_minutes": "0.5",
        "rpe_target": 7,
        "intensity_pct": 60,
        "volume_multiplier": 1.3,
        "time_per_set_minutes": 1.5
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
        "intensity_pct": 60,
        "volume_multiplier": 1.1,
        "time_per_set_minutes": 2.5  # Per set including rest
    },
    TrainingGoal.TONING.value: {
        "name": "Toning",
        "description": "Define muscles, moderate weight, higher reps",
        "rep_range": (12, 20),
        "sets_per_exercise": 3,
        "rest_minutes": "0.75-1",
        "rpe_target": 6.5,
        "intensity_pct": 55,
        "volume_multiplier": 1.0,
        "time_per_set_minutes": 1.5
    },
    TrainingGoal.HYBRID_WEIGHT_LOSS_TONING.value: {
        "name": "Weight Loss + Toning",
        "description": "Burn fat while defining muscle",
        "rep_range": (15, 20),
        "sets_per_exercise": 3,
        "rest_minutes": "0.5-0.75",
        "rpe_target": 7,
        "intensity_pct": 50,
        "volume_multiplier": 1.2,
        "time_per_set_minutes": 1.25
    }
}

# Default user settings
DEFAULT_SETTINGS = {
    "training_goal": TrainingGoal.HYBRID_STRENGTH_HYPERTROPHY.value,
    "sessions_per_week_target": 3,
    "available_time_minutes": 75,
    "target_weight_lbs": 175,
    "target_body_fat_pct": 18,
    "daily_calorie_target": 2200,
    "daily_protein_target_g": 148,
    "fatigue_threshold": 72,
    "equipment_preference": "machines_only",
    "volume_landmarks": {
        "default": {"mv": 6, "mev": 9, "mav_min": 12, "mav_max": 18, "mrv": 22}
    }
}

# Load user settings from file (or use defaults)
USER_SETTINGS = load_json(SETTINGS_FILE, DEFAULT_SETTINGS.copy())
for _k, _v in DEFAULT_SETTINGS.items():
    if _k not in USER_SETTINGS:
        USER_SETTINGS[_k] = _v

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

# ==================== EXERCISE LIBRARY ====================

EXERCISE_LIBRARY = [
    # Chest
    {"name": "Chest Press", "muscle": "chest", "compound": True, "baseline": 100, "equipment": "machine"},
    {"name": "Incline Press", "muscle": "chest", "compound": True, "baseline": 95, "equipment": "machine"},
    {"name": "Cable Crossover", "muscle": "chest", "compound": False, "baseline": 40, "equipment": "cable"},
    {"name": "Pec Fly", "muscle": "chest", "compound": False, "baseline": 50, "equipment": "machine"},
    {"name": "Dips", "muscle": "chest", "compound": True, "baseline": 50, "equipment": "bodyweight"},
    # Back
    {"name": "Lat Pulldown", "muscle": "back", "compound": True, "baseline": 100, "equipment": "machine"},
    {"name": "Seated Row", "muscle": "back", "compound": True, "baseline": 90, "equipment": "machine"},
    {"name": "Mid Row", "muscle": "back", "compound": True, "baseline": 80, "equipment": "machine"},
    {"name": "Cable Row", "muscle": "back", "compound": False, "baseline": 70, "equipment": "cable"},
    {"name": "Face Pulls", "muscle": "back", "compound": False, "baseline": 35, "equipment": "cable"},
    {"name": "Pullups", "muscle": "back", "compound": True, "baseline": 50, "equipment": "bodyweight"},
    # Shoulders
    {"name": "Shoulder Press", "muscle": "shoulders", "compound": True, "baseline": 60, "equipment": "machine"},
    {"name": "Arnold Press", "muscle": "shoulders", "compound": False, "baseline": 50, "equipment": "free_weight"},
    {"name": "Lateral Raise", "muscle": "shoulders", "compound": False, "baseline": 20, "equipment": "cable"},
    {"name": "Front Raise", "muscle": "shoulders", "compound": False, "baseline": 20, "equipment": "cable"},
    {"name": "Deltoid Fly", "muscle": "shoulders", "compound": False, "baseline": 30, "equipment": "cable"},
    {"name": "Rear Delt Fly", "muscle": "shoulders", "compound": False, "baseline": 25, "equipment": "cable"},
    # Legs
    {"name": "Leg Press", "muscle": "quads", "compound": True, "baseline": 180, "equipment": "machine"},
    {"name": "Hack Squat", "muscle": "quads", "compound": True, "baseline": 135, "equipment": "machine"},
    {"name": "Bulgarian Split Squat", "muscle": "quads", "compound": True, "baseline": 40, "equipment": "free_weight"},
    {"name": "Leg Extension", "muscle": "quads", "compound": False, "baseline": 80, "equipment": "machine"},
    {"name": "Romanian Deadlift", "muscle": "hamstrings", "compound": True, "baseline": 135, "equipment": "free_weight"},
    {"name": "Leg Curl", "muscle": "hamstrings", "compound": False, "baseline": 80, "equipment": "machine"},
    {"name": "Calf Raise", "muscle": "calves", "compound": False, "baseline": 120, "equipment": "machine"},
    {"name": "Calf Raise (Seated)", "muscle": "calves", "compound": False, "baseline": 90, "equipment": "machine"},
    {"name": "Hip Abductor", "muscle": "glutes", "compound": False, "baseline": 100, "equipment": "machine"},
    {"name": "Hip Adductor", "muscle": "adductors", "compound": False, "baseline": 100, "equipment": "machine"},
    # Arms
    {"name": "Biceps Curl", "muscle": "biceps", "compound": False, "baseline": 50, "equipment": "cable"},
    {"name": "Hammer Curl", "muscle": "biceps", "compound": False, "baseline": 40, "equipment": "free_weight"},
    {"name": "Preacher Curl", "muscle": "biceps", "compound": False, "baseline": 45, "equipment": "machine"},
    {"name": "Seated Dip", "muscle": "triceps", "compound": True, "baseline": 100, "equipment": "machine"},
    {"name": "Tricep Pushdown", "muscle": "triceps", "compound": False, "baseline": 50, "equipment": "cable"},
    {"name": "Cable Pushdown", "muscle": "triceps", "compound": False, "baseline": 55, "equipment": "cable"},
    {"name": "Overhead Tricep Extension", "muscle": "triceps", "compound": False, "baseline": 45, "equipment": "cable"},
    # Core
    {"name": "Crunch Machine", "muscle": "core", "compound": False, "baseline": 60, "equipment": "machine"},
    {"name": "Cable Crunch", "muscle": "core", "compound": False, "baseline": 55, "equipment": "cable"},
    {"name": "Hanging Leg Raise", "muscle": "core", "compound": True, "baseline": 40, "equipment": "bodyweight"},
    {"name": "Plank", "muscle": "core", "compound": False, "baseline": 0, "equipment": "bodyweight"},
]

EXERCISE_LOOKUP = {ex["name"]: ex for ex in EXERCISE_LIBRARY}


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


def _positive_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _select_recommendation_e1rm(exercise_name, ex_progression):
    """Pick the load source for a recommendation and expose why it won."""
    if not isinstance(ex_progression, dict):
        ex_progression = {}

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
            "detail": f"progression:{exercise_name}",
        }

    if baseline_e1rm is not None:
        return {
            "e1rm": baseline_e1rm,
            "status": "Calibrated Baseline",
            "source": "baseline_json",
            "detail": f"baseline_json:{baseline_key}",
        }

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


def _current_data_user_id():
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return int(current_user.get_id())
    except Exception:
        pass
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


def _summarize_nutrition_for_date(date_s: str):
    totals = {
        "calories": 0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "sodium_mg": 0,
        "entries_count": 0,
    }
    for entry in NUTRITION_DATA or []:
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
            if sets:
                best_e1rm = max(calculate_e1rm(s["weight_lbs"], s["reps"]) for s in sets)
                machine = exercise["machine"]
                if machine not in exercise_history:
                    exercise_history[machine] = []
                exercise_history[machine].append({
                    "date": workout["date"],
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
        "swimming": {"back": 1.5, "shoulders": 1.5, "core": 1, "triceps": 1}
    }

    cutoff = datetime.now() - timedelta(days=days)
    total_impact = 0

    for session in cardio_data:
        try:
            session_date = datetime.strptime(session.get("date", ""), '%Y-%m-%d')
            if session_date < cutoff:
                continue

            activity = session.get("activity_type", "").lower()
            duration = session.get("duration_minutes", 0)
            intensity = session.get("intensity", 5)

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


def get_readiness_score(muscle, soreness_data, volume_data, cardio_data=None):
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

    readiness = 10 - soreness_level - recovery_debt - cardio_fatigue

    if readiness < 5:
        recommendation = "Skip or reduced volume"
        color = "red"
    elif readiness <= 7:
        recommendation = "Proceed with caution"
        color = "yellow"
    else:
        recommendation = "Full capacity"
        color = "green"

    return {
        "score": max(0, readiness),  # Don't go below 0
        "soreness": soreness_level,
        "recovery_debt": recovery_debt,
        "cardio_fatigue": round(cardio_fatigue, 1),
        "recommendation": recommendation,
        "color": color
    }


def _get_oura_readiness_today():
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


def _filtered_exercise_library(preference: str):
    return [ex for ex in EXERCISE_LIBRARY if _equipment_allowed(ex, preference)]


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

    load_source = _select_recommendation_e1rm(exercise_name, ex_progression)
    current_e1rm = load_source["e1rm"]
    status = load_source["status"]

    if status == "Baseline":
        target_weight = round(current_e1rm * intensity_pct * 0.9, 0)
        rationale = f"{goal_params['name']}: Starting weight — log your first session to calibrate"
        sets = target_sets
    elif status == "Calibrated Baseline":
        target_weight = round(current_e1rm * intensity_pct, 0)
        rationale = f"{goal_params['name']}: Calibrated from saved baseline"
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
    target_weight, target_reps, overload_note = _apply_progressive_overload(
        target_weight, target_reps, rpe_target, is_compound, last_perf
    )
    rationale = f"{rationale} · {overload_note}"

    if exercise_name == "Plank":
        target_weight = 0
        target_reps = 45
        rationale = f"{goal_params['name']}: Timed core stability"

    rest_label = "3-4 min" if is_compound else "60-90 sec"
    return {
        "exercise": exercise_name,
        "muscle": muscle,
        "is_compound": is_compound,
        "target_weight": max(5, target_weight),
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


def generate_next_workout(workouts, soreness_data, goal=None, available_time=None, persist=False):
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
    oura_readiness = _get_oura_readiness_today()
    equipment_pref = USER_SETTINGS.get("equipment_preference", "machines_only")

    # Calculate max exercises based on available time
    # Account for warmup (5 min) and cooldown (5 min)
    effective_time = available_time - 10
    sets_per_exercise = goal_params["sets_per_exercise"]
    volume_multiplier = meso_plan["volume_multiplier"]
    if oura_readiness is not None and oura_readiness < 60:
        volume_multiplier *= 0.8
    adjusted_sets_for_timing = max(2, round(sets_per_exercise * volume_multiplier))
    time_per_exercise = time_per_set * adjusted_sets_for_timing
    max_exercises = max(2, int(effective_time / time_per_exercise))

    volume_data = calculate_volume(workouts, weeks=4)
    muscle_groups = list(volume_data.keys()) or ["chest", "back", "quads", "shoulders"]

    readiness_scores = {}
    for muscle in muscle_groups:
        readiness_scores[muscle] = get_readiness_score(muscle, soreness_data, volume_data, CARDIO_DATA)

    available_muscles = [m for m, r in readiness_scores.items() if r["score"] >= 5]

    # If not enough muscles available, add default ones
    default_muscles = ["chest", "back", "quads", "shoulders", "hamstrings", "glutes", "adductors", "biceps", "triceps", "core", "calves"]
    for m in default_muscles:
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

    # Get cardio recommendation for this goal
    cardio_rec = CARDIO_RECOMMENDATIONS.get(goal, CARDIO_RECOMMENDATIONS[TrainingGoal.HYPERTROPHY.value])
    cardio_data = None

    # Calculate remaining time for cardio (ensure we don't exceed available time)
    remaining_time = available_time - total_time
    cardio_duration = cardio_rec.get("duration_minutes", 0)

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

    recommendation = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "created_at": datetime.now().isoformat(),
        "focus": focus,
        "goal": goal,
        "goal_name": goal_params["name"],
        "estimated_duration": duration_str,
        "estimated_minutes": total_time,
        "available_time": available_time,
        "mesocycle": {
            "week": meso_week,
            "phase": meso_plan["name"],
            "volume_multiplier": round(volume_multiplier, 2),
            "rpe_base": meso_plan["rpe_base"]
        },
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
        return {"current_streak": 0, "longest_streak": 0, "weekly_avg": 0, "consistency_pct": 0}

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

    Load definition: sum over all sets of (1 * reps * weight).
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


# Initialize with real data if available, otherwise use sample data
# Data loading priority:
# 1. JSON files (user's saved data) - these persist across restarts
# 2. Markdown workout log (initial import)
# 3. Sample data (demo mode)
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
    else:
        print("No saved data found, using sample data")
        WORKOUTS, SORENESS_DATA = get_sample_data()


@app.route('/')
def index():
    """Main dashboard page."""
    return render_template('index.html')


@app.route('/api/dashboard')
def api_dashboard():
    """API endpoint for dashboard data."""
    volume = calculate_volume(WORKOUTS, weeks=4)
    progression = calculate_progression_status(WORKOUTS)

    total_sets = sum(d["sets"] for d in volume.values())
    improving = sum(1 for d in progression.values() if d["status"] == "On Track")
    total_exercises = len(progression)

    readiness_scores = [get_readiness_score(m, SORENESS_DATA, volume, CARDIO_DATA)["score"] for m in volume.keys()]
    avg_readiness = sum(readiness_scores) / len(readiness_scores) if readiness_scores else 7

    muscle_data = []
    for muscle, data in volume.items():
        readiness = get_readiness_score(muscle, SORENESS_DATA, volume, CARDIO_DATA)
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

    # Nutrition totals
    nutrition_totals = _summarize_nutrition_for_date(today_s)
    calories_target, protein_target = _get_nutrition_targets()
    carbs_target, fat_target = _compute_carb_fat_targets(calories_target, protein_target)
    calories_pct = int(round((nutrition_totals["calories"] / calories_target) * 100)) if calories_target else 0
    protein_pct = int(round((nutrition_totals["protein_g"] / protein_target) * 100)) if protein_target else 0
    carbs_pct = int(round((nutrition_totals["carbs_g"] / carbs_target) * 100)) if carbs_target else 0
    fat_pct = int(round((nutrition_totals["fat_g"] / fat_target) * 100)) if fat_target else 0

    next_workout = generate_next_workout(WORKOUTS, SORENESS_DATA)
    global LAST_WORKOUT_RECOMMENDATION
    LAST_WORKOUT_RECOMMENDATION = next_workout

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
        "next_workout": next_workout,
        "readiness_factors": {
            "acwr": acwr,
            "sleep_debt": sleep_debt,
            "recovery_bonus": recovery_bonus,
        },
        "body_stats": body_stats,
        "recomp_command": {
            "signal": signal,
            "readiness": readiness_val if readiness_val is not None else 0,
            "reason": "; ".join(reason_bits)
        },
        "nutrition_today": {
            "calories": nutrition_totals["calories"],
            "protein_g": round(nutrition_totals["protein_g"], 1),
            "carbs_g": round(nutrition_totals["carbs_g"], 1),
            "fat_g": round(nutrition_totals["fat_g"], 1),
            "sodium_mg": int(nutrition_totals["sodium_mg"]),
            "calories_target": calories_target,
            "protein_target_g": round(protein_target, 1),
            "carbs_target_g": carbs_target,
            "fat_target_g": fat_target,
            "calories_pct": calories_pct,
            "protein_pct": protein_pct,
            "carbs_pct": carbs_pct,
            "fat_pct": fat_pct,
            "entries_count": nutrition_totals["entries_count"],
        },
        "advanced_kpis": {
            "personal_records": prs,
            "consistency": consistency,
            "push_pull_balance": push_pull,
            "deload_check": deload,
            "injury_risk": injury_risk,
            "summary_stats": summary_stats
        },
        "freshness": _compute_data_freshness(),
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
        "overall_fatigue": data.get("overall_fatigue", 5),
        "notes": notes,
    }

    WORKOUTS.append(entry)
    save_json(WORKOUTS_FILE, WORKOUTS)
    _notify_workout_logged(entry)
    return jsonify({"status": "success", "workout": entry})


@app.route('/api/add-soreness', methods=['POST'])
def add_soreness():
    """Add soreness data."""
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
    if client_id:
        entry["client_id"] = client_id

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
    food_log = add_food_log(_current_data_user_id(), {
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
    })
    return jsonify({"status": "success", "nutrition": entry, "food_log": food_log})


@app.route('/api/nutrition-today')
def nutrition_today():
    """Return today's nutrition totals and targets."""
    date_s = _today_str()
    totals = _summarize_nutrition_for_date(date_s)
    calories_target, protein_target = _get_nutrition_targets()
    carbs_target, fat_target = _compute_carb_fat_targets(calories_target, protein_target)
    calories_pct = int(round((totals["calories"] / calories_target) * 100)) if calories_target else 0
    protein_pct = int(round((totals["protein_g"] / protein_target) * 100)) if protein_target else 0
    carbs_pct = int(round((totals["carbs_g"] / carbs_target) * 100)) if carbs_target else 0
    fat_pct = int(round((totals["fat_g"] / fat_target) * 100)) if fat_target else 0
    return jsonify({
        "date": date_s,
        "calories": totals["calories"],
        "protein_g": round(totals["protein_g"], 1),
        "carbs_g": round(totals["carbs_g"], 1),
        "fat_g": round(totals["fat_g"], 1),
        "sodium_mg": int(totals["sodium_mg"]),
        "calories_target": calories_target,
        "protein_target_g": round(protein_target, 1),
        "carbs_target_g": carbs_target,
        "fat_target_g": fat_target,
        "calories_pct": calories_pct,
        "protein_pct": protein_pct,
        "carbs_pct": carbs_pct,
        "fat_pct": fat_pct,
        "entries_count": totals["entries_count"],
    })


@app.route('/api/nutrition-history')
def nutrition_history():
    """Return last 14 days of nutrition totals."""
    today = datetime.now().date()
    days = []
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        date_s = d.strftime("%Y-%m-%d")
        totals = _summarize_nutrition_for_date(date_s)
        days.append({
            "date": date_s,
            "calories": totals["calories"],
            "protein_g": round(totals["protein_g"], 1),
            "carbs_g": round(totals["carbs_g"], 1),
            "fat_g": round(totals["fat_g"], 1),
        })
    return jsonify({"history": days})


@app.route('/api/add-cardio', methods=['POST'])
def add_cardio():
    """Add cardio session data."""
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
    return jsonify({"status": "success", "cardio": entry})


@app.route('/api/add-recovery', methods=['POST'])
def add_recovery():
    """Add recovery session data (sauna, cold plunge, etc)."""
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
    return jsonify({"status": "success", "recovery": entry})


@app.route('/api/exercises')
def api_exercises():
    """Exercise dropdown options for manual logging."""
    options = []
    for ex in EXERCISE_LIBRARY:
        options.append({
            "name": ex.get("name"),
            "muscle": ex.get("muscle"),
            "equipment": ex.get("equipment"),
            "compound": ex.get("compound"),
            "baseline": ex.get("baseline"),
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
    if request.method == 'GET':
        goal = USER_SETTINGS.get("training_goal", TrainingGoal.HYPERTROPHY.value)
        goal_params = GOAL_PARAMETERS.get(goal, {})
        return jsonify({
            "training_goal": goal,
            "goal_details": goal_params,
            "sessions_per_week_target": USER_SETTINGS.get("sessions_per_week_target", 3),
            "available_time_minutes": USER_SETTINGS.get("available_time_minutes", 60),
            "target_weight_lbs": USER_SETTINGS.get("target_weight_lbs", 180),
            "target_body_fat_pct": USER_SETTINGS.get("target_body_fat_pct", 15),
            "daily_calorie_target": USER_SETTINGS.get("daily_calorie_target", 2200),
            "daily_protein_target_g": USER_SETTINGS.get("daily_protein_target_g", 148),
            "fatigue_threshold": USER_SETTINGS.get("fatigue_threshold", 72),
            "equipment_preference": USER_SETTINGS.get("equipment_preference", "machines_only"),
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
            ]
        })
    else:
        data, err = get_json_body(required=True)
        if err:
            return err

        if "training_goal" in data:
            goal = data.get("training_goal")
            if goal not in GOAL_PARAMETERS:
                return api_error("Invalid training_goal", 400, code="invalid_field")
            USER_SETTINGS["training_goal"] = goal

        if "sessions_per_week_target" in data:
            s, err2 = _coerce_int(data.get("sessions_per_week_target"), "sessions_per_week_target", min_v=1, max_v=14)
            if err2:
                return err2
            USER_SETTINGS["sessions_per_week_target"] = s

        if "available_time_minutes" in data:
            t, err2 = _coerce_int(data.get("available_time_minutes"), "available_time_minutes", min_v=10, max_v=240)
            if err2:
                return err2
            USER_SETTINGS["available_time_minutes"] = t

        if "target_weight_lbs" in data:
            tw, err2 = _coerce_float(data.get("target_weight_lbs"), "target_weight_lbs", min_v=80, max_v=500)
            if err2:
                return err2
            USER_SETTINGS["target_weight_lbs"] = tw

        if "target_body_fat_pct" in data:
            tbf, err2 = _coerce_float(data.get("target_body_fat_pct"), "target_body_fat_pct", min_v=4, max_v=60)
            if err2:
                return err2
            USER_SETTINGS["target_body_fat_pct"] = tbf

        if "daily_calorie_target" in data:
            cal, err2 = _coerce_int(data.get("daily_calorie_target"), "daily_calorie_target", min_v=500, max_v=6000)
            if err2:
                return err2
            USER_SETTINGS["daily_calorie_target"] = cal

        if "daily_protein_target_g" in data:
            prot, err2 = _coerce_float(data.get("daily_protein_target_g"), "daily_protein_target_g", min_v=50, max_v=400)
            if err2:
                return err2
            USER_SETTINGS["daily_protein_target_g"] = round(prot, 1)

        if "fatigue_threshold" in data:
            ft, err2 = _coerce_int(data.get("fatigue_threshold"), "fatigue_threshold", min_v=40, max_v=95)
            if err2:
                return err2
            USER_SETTINGS["fatigue_threshold"] = ft

        if "volume_landmarks" in data and isinstance(data.get("volume_landmarks"), dict):
            USER_SETTINGS["volume_landmarks"] = data.get("volume_landmarks")
        if "equipment_preference" in data:
            pref, err2 = _coerce_str(data.get("equipment_preference"), "equipment_preference", required=True, max_len=64)
            if err2:
                return err2
            if pref not in ("machines_only", "machines_and_cables", "all"):
                return api_error("Invalid equipment_preference", 400, code="invalid_field")
            USER_SETTINGS["equipment_preference"] = pref

        save_json(SETTINGS_FILE, USER_SETTINGS)  # Persist to file
        return jsonify({"status": "success", "settings": USER_SETTINGS})


@app.route('/api/settings/equipment', methods=['PUT'])
def settings_equipment():
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
    return jsonify({"status": "success", "equipment_preference": pref})


@app.route('/api/exercises/alternatives/<muscle_group>')
def exercise_alternatives(muscle_group):
    muscle = (muscle_group or "").strip().lower()
    if not muscle:
        return api_error("Invalid muscle group", 400, code="invalid_field")
    equipment_pref = USER_SETTINGS.get("equipment_preference", "machines_only")
    options = [
        {
            "name": ex["name"],
            "muscle": ex["muscle"],
            "equipment": ex.get("equipment"),
            "compound": ex.get("compound"),
        }
        for ex in _filtered_exercise_library(equipment_pref)
        if ex.get("muscle") == muscle
    ]
    options.sort(key=lambda x: x["name"])
    return jsonify({"muscle": muscle, "equipment_preference": equipment_pref, "alternatives": options})


@app.route('/api/workout/swap', methods=['POST'])
def swap_workout_exercise():
    global LAST_WORKOUT_RECOMMENDATION
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

    recommendation = None
    if WORKOUT_RECOMMENDATIONS and 0 <= workout_index < len(WORKOUT_RECOMMENDATIONS):
        recommendation = WORKOUT_RECOMMENDATIONS[workout_index]
    elif LAST_WORKOUT_RECOMMENDATION:
        recommendation = LAST_WORKOUT_RECOMMENDATION
    else:
        return api_error("No recent workout recommendation available", 404, code="not_found")

    exercises = recommendation.get("exercises") or []
    if not (0 <= exercise_index < len(exercises)):
        return api_error("exercise_index out of range", 404, code="not_found")

    new_ex = EXERCISE_LOOKUP.get(new_name)
    if not new_ex:
        return api_error("Unknown exercise name", 404, code="not_found")

    old_ex = exercises[exercise_index]
    old_muscle = (old_ex.get("muscle") or "").lower()
    if new_ex.get("muscle") != old_muscle:
        return api_error("New exercise must match the same muscle group", 400, code="invalid_field")
    equipment_pref = USER_SETTINGS.get("equipment_preference", "machines_only")
    if not _equipment_allowed(new_ex, equipment_pref):
        return api_error("New exercise is not allowed by the current equipment preference", 400, code="invalid_field")

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
    if recommendation is LAST_WORKOUT_RECOMMENDATION:
        LAST_WORKOUT_RECOMMENDATION = recommendation

    return jsonify({"status": "success", "recommendation": recommendation})


# ==================== AI COACH — ADJUST PLAN ====================
# AI coach adjustment spec:
# - LLM returns intent only; Python re-picks exercises and enforces safety.
# - RPE delta max ±1.0; sets delta max ±20%; weight cap +10% over recent e1RM.
# - Hard blacklist soreness/readiness-flagged muscles. Never override deload.
# - Timeout 8s. One concurrent request. Deterministic fallback on any failure.
# - Cache by workout_id + constraint + readiness_date + model_version + library_hash.

_ADJUST_CACHE_DB = os.path.join(DATA_DIR, "ai_coach_cache.sqlite3")


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
        conn = sqlite3.connect(_ADJUST_CACHE_DB)
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
        conn.close()
    except Exception as exc:
        print(f"WARN: ai_metric_log failed: {exc}")


def _exercise_library_hash(preference: str) -> str:
    import hashlib
    names = sorted(ex.get("name", "") for ex in _filtered_exercise_library(preference))
    return hashlib.sha1(("|".join(names)).encode()).hexdigest()[:12]


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
        conn = sqlite3.connect(_ADJUST_CACHE_DB)
        row = conn.execute(
            "SELECT response_json FROM adjust_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        conn.close()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def _ai_cache_put(key, payload):
    try:
        conn = sqlite3.connect(_ADJUST_CACHE_DB)
        conn.execute(
            "INSERT OR REPLACE INTO adjust_cache (cache_key, created_at, response_json) VALUES (?, ?, ?)",
            (key, datetime.now().isoformat(), json.dumps(payload)),
        )
        conn.commit()
        conn.close()
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

    # ── 4) Hard blacklist avoid_muscles using current soreness data.
    avoid_muscles = {m.strip().lower() for m in (intent.get("avoid_muscles") or []) if isinstance(m, str)}
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

    # ── 5) Apply swaps. LLM names the muscle; Python picks the exercise.
    volume_data = calculate_volume(WORKOUTS, weeks=4)
    progression = calculate_progression_status(WORKOUTS)
    time_per_set = goal_params.get("time_per_set_minutes", 3)
    volume_multiplier = meso_plan["volume_multiplier"]
    if oura_readiness is not None and oura_readiness < 60:
        volume_multiplier *= 0.8

    swap_requests = intent.get("swap") or []
    for sw in swap_requests:
        if not isinstance(sw, dict):
            continue
        src_name = (sw.get("replace_exercise") or "").strip().lower()
        target_muscle = (sw.get("target_muscle") or "").strip().lower()
        if not src_name or not target_muscle:
            continue
        if target_muscle in avoid_muscles:
            notes.append(f"Ignored: swap to {target_muscle} — muscle is avoided")
            continue

        # Find the exercise to replace by name (case-insensitive, contains).
        idx = None
        for i, ex in enumerate(exercises):
            if src_name in (ex.get("exercise") or "").lower():
                idx = i
                break
        if idx is None:
            notes.append(f"could not locate '{sw.get('replace_exercise')}' in current plan")
            continue

        # Pick a new exercise for target_muscle from the equipment-filtered library,
        # preferring compound movements and rotating off recently-trained exercises.
        library = [ex for ex in _filtered_exercise_library(equipment_pref) if ex.get("muscle") == target_muscle]
        if not library:
            notes.append(f"no exercises available for muscle '{target_muscle}' under current equipment")
            continue
        # Prefer compound, then alphabetical stability
        library.sort(key=lambda e: (not e.get("compound"), e.get("name", "")))
        # Avoid picking something already in the plan
        already = {(ex.get("exercise") or "").lower() for ex in exercises}
        picked = next((e for e in library if e["name"].lower() not in already), library[0])

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
    global LAST_WORKOUT_RECOMMENDATION

    data, err = get_json_body(required=True)
    if err:
        return err

    constraint, err2 = _coerce_str(data.get("constraint"), "constraint", required=True, max_len=280)
    if err2:
        return err2

    if not _lm_studio:
        _ai_metric_log("fallback", reason="adapter_missing", constraint_len=len(constraint))
        return jsonify({
            "status": "fallback",
            "reason": "LM Studio adapter not available on this server",
            "recommendation": LAST_WORKOUT_RECOMMENDATION,
            "summary": None,
            "applied_notes": [],
        })

    recommendation = LAST_WORKOUT_RECOMMENDATION
    if not recommendation:
        recommendation = generate_next_workout(WORKOUTS, SORENESS_DATA)
        LAST_WORKOUT_RECOMMENDATION = recommendation

    goal = recommendation.get("goal") or USER_SETTINGS.get("training_goal", TrainingGoal.HYPERTROPHY.value)
    goal_params = GOAL_PARAMETERS.get(goal, GOAL_PARAMETERS[TrainingGoal.HYPERTROPHY.value])
    sessions_per_week = USER_SETTINGS.get("sessions_per_week_target", 3)
    meso_week = recommendation.get("mesocycle", {}).get("week") or _get_mesocycle_week(WORKOUTS, sessions_per_week)
    meso_plan = MESOCYCLE_PLAN.get(meso_week, MESOCYCLE_PLAN[1])
    oura_readiness = _get_oura_readiness_today()
    equipment_pref = USER_SETTINGS.get("equipment_preference", "machines_only")

    readiness_date = _today_str()
    cache_key = _ai_cache_key(
        recommendation,
        constraint,
        readiness_date,
        _lm_studio.LM_STUDIO_MODEL_VERSION,
        equipment_pref,
    )
    cached = _ai_cache_get(cache_key)
    if cached:
        cached["cache_hit"] = True
        _ai_metric_log("cache_hit", latency_ms=0, constraint_len=len(constraint), model_version=_lm_studio.LM_STUDIO_MODEL_VERSION)
        # Keep server-side canonical plan in sync with what the client sees,
        # so a follow-up Adjust or Swap operates on the patched plan, not the
        # pre-adjust plan that's still in LAST_WORKOUT_RECOMMENDATION.
        if cached.get("recommendation"):
            LAST_WORKOUT_RECOMMENDATION = cached["recommendation"]
        return jsonify(cached)

    # Send readiness context the LLM can reason about.
    readiness_ctx = {
        "oura_readiness": oura_readiness,
        "mesocycle_week": meso_week,
        "deload_active": bool((meso_plan.get("name") == "Deload")),
    }

    try:
        raw_patch = _lm_studio.adjust_plan(recommendation, constraint, readiness=readiness_ctx)
    except _lm_studio.LmStudioError as exc:
        reason_code = "timeout" if "timeout" in str(exc).lower() else "unreachable" if "unreachable" in str(exc).lower() else "invalid_json" if "json" in str(exc).lower() else "error"
        _ai_metric_log(
            "fallback",
            constraint_len=len(constraint),
            model_version=_lm_studio.LM_STUDIO_MODEL_VERSION,
            reason=f"{reason_code}: {exc}",
        )
        return jsonify({
            "status": "fallback",
            "reason": f"LM Studio: {exc}",
            "recommendation": recommendation,
            "summary": None,
            "applied_notes": [],
        })

    intent = raw_patch.get("intent") or {}
    summary = raw_patch.get("summary") or ""

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
            model_version=_lm_studio.LM_STUDIO_MODEL_VERSION,
            reason=f"apply_patch_error: {type(exc).__name__}: {str(exc)[:80]}",
        )
        return jsonify({
            "status": "fallback",
            "reason": f"safety-rail error: {type(exc).__name__}",
            "recommendation": recommendation,
            "summary": None,
            "applied_notes": [],
        })

    intent_is_empty = (
        not intent.get("avoid_muscles")
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
        "meta": raw_patch.get("_meta", {}),
        "cache_hit": False,
    }
    _ai_cache_put(cache_key, payload)
    LAST_WORKOUT_RECOMMENDATION = patched
    _ai_metric_log(
        "ok",
        latency_ms=(raw_patch.get("_meta") or {}).get("elapsed_ms", 0),
        constraint_len=len(constraint),
        model_version=_lm_studio.LM_STUDIO_MODEL_VERSION,
    )
    return jsonify(payload)


@app.route('/api/workout/analyze', methods=['POST'])
def analyze_workout():
    """Post-mortem on one logged workout.

    Accepts either {"workout_id": "..."} to analyze a specific stored workout,
    {"workout_date": "YYYY-MM-DD"} to pick the most recent session on that day,
    or {"latest": true} for the most recently completed session.
    """
    if not _lm_studio:
        return jsonify({"status": "fallback", "reason": "LM Studio adapter not available"}), 200

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
                        (float(s.get("weight_lbs") or 0) for s in (ex.get("sets") or [])),
                        default=0,
                    ),
                    "top_rpe": max(
                        (float(s.get("rpe") or 0) for s in (ex.get("sets") or [])),
                        default=0,
                    ),
                }
                for ex in (w.get("exercises") or [])
            ],
        })

    progression_all = calculate_progression_status(WORKOUTS)
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

    # Cache key based on workout content + model version so re-analyzing the
    # same unchanged workout doesn't re-spend tokens.
    import hashlib
    fingerprint_src = json.dumps({
        "analysis_prompt_version": getattr(_lm_studio, "ANALYZE_PROMPT_VERSION", "unknown"),
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
        "model": _lm_studio.LM_STUDIO_MODEL_VERSION,
    }, default=str, sort_keys=True)
    cache_key = "analyze:" + hashlib.sha1(fingerprint_src.encode()).hexdigest()
    cached = _ai_cache_get(cache_key)
    if cached:
        cached["cache_hit"] = True
        cached_ctx = cached.setdefault("context_used", {})
        cached_ctx.update(notes_context)
        _ai_metric_log("cache_hit", constraint_len=0, model_version=_lm_studio.LM_STUDIO_MODEL_VERSION, reason="analyze")
        return jsonify(cached)

    try:
        result = _lm_studio.analyze_workout(target, llm_context)
    except _lm_studio.LmStudioError as exc:
        _ai_metric_log("fallback", constraint_len=0, model_version=_lm_studio.LM_STUDIO_MODEL_VERSION, reason=f"analyze: {exc}")
        return jsonify({
            "status": "fallback",
            "reason": f"LM Studio: {exc}",
            "workout": target,
            "analysis": None,
        })

    payload = {
        "status": "ok",
        "workout": {
            "id": target.get("id"),
            "date": target.get("date"),
            "session_type": target.get("session_type") or target.get("focus"),
            "total_sets": target.get("total_sets"),
            "total_volume": target.get("total_volume"),
            "duration_minutes": target.get("duration_minutes"),
        },
        "analysis": {
            "summary": result.get("summary"),
            "wins": result.get("wins") or [],
            "concerns": result.get("concerns") or [],
            "comparison": result.get("comparison"),
            "next_session_cue": result.get("next_session_cue"),
        },
        "context_used": {
            "recent_session_count": len(recent_compact),
            "exercise_progression_available": list(progression_subset.keys()),
            "readiness_available": oura_snapshot is not None,
            **notes_context,
        },
        "meta": result.get("_meta", {}),
        "cache_hit": False,
    }
    _ai_cache_put(cache_key, payload)
    _ai_metric_log(
        "ok",
        latency_ms=(result.get("_meta") or {}).get("elapsed_ms", 0),
        constraint_len=0,
        model_version=_lm_studio.LM_STUDIO_MODEL_VERSION,
        reason="analyze",
    )
    return jsonify(payload)


@app.route('/api/ai/health')
def ai_health():
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
        conn = sqlite3.connect(_ADJUST_CACHE_DB)
        rows = conn.execute(
            "SELECT outcome, COUNT(*), COALESCE(AVG(NULLIF(latency_ms,0)),0) FROM adjust_metrics WHERE ts >= ? GROUP BY outcome",
            (cutoff,),
        ).fetchall()
        last5 = conn.execute(
            "SELECT ts, outcome, latency_ms, reason FROM adjust_metrics WHERE ts >= ? ORDER BY id DESC LIMIT 5",
            (cutoff,),
        ).fetchall()
        conn.close()
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

def _fetch_wttr(location: str = "San_Antonio", max_age_s: int = 600):
    """Fetch current weather from wttr.in (best-effort).

    Returns dict:
      {available, location, temp_f, humidity_pct, condition, feelslike_f, raw}
    """
    now = int(time.time())
    if (
        _WEATHER_CACHE.get("data")
        and _WEATHER_CACHE.get("location") == location
        and (now - int(_WEATHER_CACHE.get("ts") or 0)) <= max_age_s
    ):
        return {"available": True, "location": location, **_WEATHER_CACHE["data"], "source": "cache"}

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
            "raw": {"current_condition": cur},
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

OPEN_WEARABLES_USERNAME = os.environ.get("OW_USERNAME", "").strip()
OPEN_WEARABLES_PASSWORD = os.environ.get("OW_PASSWORD", "").strip()
OPEN_WEARABLES_USER_ID = os.environ.get("OW_USER_ID", "").strip()
OPEN_WEARABLES_BASE = (
    f"http://localhost:8000/api/v1/users/{OPEN_WEARABLES_USER_ID}"
    if OPEN_WEARABLES_USER_ID
    else ""
)
OPEN_WEARABLES_LOGIN_URL = "http://localhost:8000/api/v1/auth/login"

_OW_TOKEN_CACHE = {"token": None, "expires_at": 0}


def _missing_open_wearables_config():
    missing = []
    if not OPEN_WEARABLES_USERNAME:
        missing.append("OW_USERNAME")
    if not OPEN_WEARABLES_PASSWORD:
        missing.append("OW_PASSWORD")
    if not OPEN_WEARABLES_USER_ID:
        missing.append("OW_USER_ID")
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

    data = urllib.parse.urlencode({
        "username": OPEN_WEARABLES_USERNAME,
        "password": OPEN_WEARABLES_PASSWORD,
    }).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        req = urllib.request.Request(OPEN_WEARABLES_LOGIN_URL, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=6) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        _OW_TOKEN_CACHE.update({"token": None, "expires_at": 0, "error": str(e)})
        return None

    token = (
        payload.get("access_token")
        or payload.get("token")
        or payload.get("jwt")
        or (payload.get("data") or {}).get("access_token")
    )
    if not token:
        _OW_TOKEN_CACHE.update({"token": None, "expires_at": 0, "error": "missing_token"})
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
        "sleep": f"{OPEN_WEARABLES_BASE}/events/sleep?start_date={start_date}&end_date={end_date}",
        "workouts": f"{OPEN_WEARABLES_BASE}/events/workouts?start_date={start_date}&end_date={end_date}",
        "activity_summary": f"{OPEN_WEARABLES_BASE}/summaries/activity?start_date={start_date}&end_date={end_date}",
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

@app.after_request
def add_cors_headers(response):
    """Allow remote access (ngrok, etc) without compromising data safety."""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


@app.route('/api/health/sync', methods=['POST'])
def health_sync():
    """Manually pull Open Wearables sleep/workout data."""
    try:
        data = fetch_open_wearables_data()
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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
    from oura_sleep_sync import create_sleep_table, sync_sleep_data, get_latest_sleep

    # Get optional start_date parameter (default to last 30 days)
    data = request.get_json(silent=True) or {}
    days_back = data.get("days_back", 30)

    try:
        start_date = (datetime.now().date() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        # Ensure table exists
        create_sleep_table(OURA_DB_FILE)

        # Get API token
        api_token = os.environ.get("OURA_API_TOKEN")
        if not api_token:
            return jsonify({"status": "error", "message": "OURA_API_TOKEN not configured"}), 500

        # Sync data
        sync_sleep_data(OURA_DB_FILE, api_token, start_date=start_date)

        # Return a summary only; raw wearable rows stay server-side.
        latest = get_latest_sleep(OURA_DB_FILE, days=7)
        latest_days = [r.get("day") for r in latest if r.get("day")]

        return jsonify({
            "status": "success",
            "synced_from": start_date,
            "latest_records": len(latest),
            "latest_days": latest_days,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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

APPLE_HEALTH_SYNC_DB_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "apple_health_sync.db"
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
        conn = sqlite3.connect(APPLE_HEALTH_SYNC_DB_FILE)
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
        totals = _summarize_nutrition_for_date(today_s)
    except Exception:
        totals = {"calories": 0, "protein_g": 0.0}
    try:
        calories_target, protein_target = _get_nutrition_targets()
    except Exception:
        calories_target, protein_target = 2200, 148.0
    calories = int(totals.get("calories") or 0)
    protein_g = float(totals.get("protein_g") or 0.0)
    cal_pct = int(round((calories / calories_target) * 100)) if calories_target else 0
    pro_pct = int(round((protein_g / protein_target) * 100)) if protein_target else 0
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
        "calories_pct": cal_pct,
        "protein_pct": pro_pct,
    }


def _food_pending_review_state(now=None):
    today_s = (now or datetime.now()).strftime("%Y-%m-%d")
    entries = NUTRITION_DATA if isinstance(NUTRITION_DATA, list) else []
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


def _compute_data_freshness(now=None):
    """Per-source freshness for Oura, Apple Health, and food.

    Returns:
        {
          "oura":         {status, last_data_point, last_sync_attempt, source: "live|cached"},
          "apple_health": {status, last_data_point, last_sync_attempt},
          "food":         {status, last_data_point, last_sync_attempt,
                           pending_review: bool, target_state: "none|under|on_track|over",
                           calories, protein_g, calories_target, protein_target_g,
                           calories_pct, protein_pct},
        }
    """
    now = now or datetime.now()
    oura_status, oura_last_data, oura_last_sync = _latest_oura_freshness(now)
    apple_status, apple_last_data, apple_last_sync = _latest_apple_health_freshness(now)
    food_status, food_last_data, food_last_sync = _latest_food_freshness(now)
    food_targets = _food_target_state(now)
    return {
        "oura": {
            "status": oura_status,
            "last_data_point": oura_last_data,
            "last_sync_attempt": oura_last_sync,
            "source": _oura_source_label(oura_last_sync, now=now),
        },
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


def _confidence_level_from(effective_readiness, freshness):
    """Derive 'high' | 'medium' | 'low' from effective readiness + wearable freshness.

    A stale or fully-missing wearable forces 'low' — we can't claim a confident
    recommendation when the underlying signal is gone.
    """
    wearable_states = [
        (freshness.get("oura") or {}).get("status"),
        (freshness.get("apple_health") or {}).get("status"),
    ]
    if "stale" in wearable_states:
        return "low"
    if all(s == "missing" for s in wearable_states):
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
        else:
            client = OuraClient()
            readiness, sleep_score, hrv, metrics, raw = client.get_today_metrics(today)
            upsert_oura_daily(
                OURA_DB_FILE,
                day=today,
                readiness_score=readiness,
                sleep_score=sleep_score,
                hrv=hrv,
                raw_json=raw,
                steps=metrics.get("steps"),
                activity_score=metrics.get("activity_score"),
                active_calories=metrics.get("active_calories"),
                resting_hr=metrics.get("resting_hr"),
                temperature_deviation=metrics.get("temperature_deviation"),
                sleep_duration_min=metrics.get("sleep_duration_min"),
                sleep_deep_min=metrics.get("sleep_deep_min"),
                sleep_rem_min=metrics.get("sleep_rem_min"),
                sleep_light_min=metrics.get("sleep_light_min"),
                sleep_awake_min=metrics.get("sleep_awake_min"),
            )
    except Exception:
        pass

    # HRV trend (best-effort)
    hrv_trend = "unknown"
    try:
        end = datetime.now().date()
        start = end - timedelta(days=6)
        rows = get_oura_daily_range(OURA_DB_FILE, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        hrv_trend = compute_hrv_trend([r.get("hrv") for r in rows if r.get("hrv") is not None])
    except Exception:
        pass

    recent = filter_recent_soreness(SORENESS_DATA, hours=24)
    avoid = [s.get("muscle") for s in recent if (s.get("soreness_level") or 0) >= 6 and s.get("muscle")]
    avoid = sorted(set(avoid))

    # Readiness factors (ACWR / sleep debt / recovery bonus)
    acwr_data = calculate_acwr(WORKOUTS)
    sleep_debt = calculate_sleep_debt(OURA_DB_FILE, days=7)
    recovery_bonus = calculate_recovery_bonus(RECOVERY_DATA, hours=48)

    effective_readiness = None
    if readiness is not None:
        try:
            effective_readiness = float(readiness) + float(recovery_bonus.get("bonus_points") or 0)
            effective_readiness = max(0.0, min(100.0, effective_readiness))
        except Exception:
            effective_readiness = float(readiness)

    def _downgrade_once(rec: str) -> str:
        if rec == "intensity":
            return "moderate"
        if rec == "moderate":
            return "recovery"
        return rec

    # Determine base intensity from (effective) readiness
    recommendation = "moderate"
    if effective_readiness is not None:
        if effective_readiness < 70:
            recommendation = "recovery"
        elif effective_readiness > 85:
            recommendation = "intensity"

    # HRV trend adjustment
    if hrv_trend == "declining":
        if recommendation == "intensity":
            recommendation = "moderate"
        elif recommendation == "moderate":
            recommendation = "recovery"

    # Sleep debt adjustment
    if (sleep_debt.get("debt_minutes") or 0) > 300:
        recommendation = _downgrade_once(recommendation)

    # ACWR adjustments
    acwr_v = acwr_data.get("acwr")
    try:
        acwr_f = float(acwr_v)
    except Exception:
        acwr_f = None

    if acwr_f is not None:
        if acwr_f > 1.5:
            recommendation = "recovery"
        elif acwr_f >= 1.3:
            recommendation = _downgrade_once(recommendation)

    upper = {"chest", "back", "shoulders", "biceps", "triceps"}
    lower = {"quads", "hamstrings", "glutes", "calves", "adductors"}
    avoid_set = set(avoid)

    if avoid_set & lower and not (avoid_set & upper):
        suggested = "Upper body focus - avoid leg exercises due to soreness"
    elif avoid_set & upper and not (avoid_set & lower):
        suggested = "Lower body focus - avoid upper-body loading due to soreness"
    elif avoid_set:
        suggested = "Recovery / light movement - multiple sore areas"
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

    # Weather adjustment (best-effort)
    weather = None
    try:
        weather = _fetch_wttr(_WEATHER_CACHE.get("location") or "San_Antonio")
        if weather.get("available"):
            temp = weather.get("feelslike_f") if weather.get("feelslike_f") is not None else weather.get("temp_f")
            hum = weather.get("humidity_pct")
            if temp is not None:
                # Extreme heat: reduce intensity
                if temp >= 95 or (temp >= 90 and (hum or 0) >= 75):
                    reason_bits.append(f"Weather {temp}F feels-like (hot)")
                    if recommendation == "intensity":
                        recommendation = "moderate"
                    elif recommendation == "moderate":
                        recommendation = "recovery"
                # Extreme cold: be conservative
                elif temp <= 40:
                    reason_bits.append(f"Weather {temp}F feels-like (cold)")
                    if recommendation == "intensity":
                        recommendation = "moderate"
    except Exception:
        weather = None

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

    freshness = _compute_data_freshness()
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
        "suggested_workout": suggested,
        "weather": weather,
        "time_of_day": time_of_day,
        "history_context": history_context,
        "reasoning": "; ".join(reason_bits) if reason_bits else "No Oura/soreness data available",
        "freshness": freshness,
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
        workouts_list.append({
            "date": w["date"],
            "session_type": w.get("session_type", "general"),
            "duration_minutes": w.get("duration_minutes", 0),
            "exercises": w.get("exercises", []),
            "total_sets": total_sets,
            "total_volume": round(total_volume),
            "notes": w.get("notes", "")
        })
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
        workouts_list.append({
            "id": w.get("id"),
            "created_at": w.get("created_at"),
            "date": w.get("date", ""),
            "session_type": w.get("session_type", "general"),
            "duration_minutes": w.get("duration_minutes", 0),
            "exercises": w.get("exercises", []),
            "total_sets": total_sets,
            "total_volume": round(total_volume),
            "notes": w.get("notes", "")
        })

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


@app.route('/api/complete-workout', methods=['POST'])
def complete_workout():
    """Complete a workout and track adherence to recommendations."""
    data, err = get_json_body(required=True)
    if err:
        return err

    client_workout_id, err2 = _coerce_str(data.get("id"), "id", required=False, max_len=80)
    if err2:
        return err2
    if client_workout_id:
        allowed_id_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:.")
        if any(ch not in allowed_id_chars for ch in client_workout_id):
            return api_error("id contains unsupported characters", 400, code="invalid_field")
        existing = next((w for w in WORKOUTS if w.get("id") == client_workout_id), None)
        if existing:
            return jsonify({
                "status": "success",
                "adherence": existing.get("adherence", {"followed": True, "skipped": [], "modified": [], "added": []}),
                "workout_id": client_workout_id,
                "duplicate": True,
                "message": "Workout already logged. Using existing workout ID."
            })

    recommendation_id = data.get("recommendation_id")
    actual_exercises = data.get("exercises", [])
    if not isinstance(actual_exercises, list):
        return api_error("exercises must be a list", 400, code="invalid_field")

    # Validate we have at least one completed exercise with at least 1 set
    if len(actual_exercises) == 0:
        return api_error("Workout must include at least one exercise", 400, code="invalid_field")

    for ex_idx, ex in enumerate(actual_exercises):
        if not isinstance(ex, dict):
            return api_error("Each exercise must be an object", 400, code="invalid_field")
        if not ex.get("machine"):
            return api_error("Each exercise must include machine", 400, code="invalid_field")
        sets = ex.get("sets") or []
        if not isinstance(sets, list) or len(sets) == 0:
            return api_error("Each exercise must include at least one set", 400, code="invalid_field")
        for set_idx, set_row in enumerate(sets):
            if not isinstance(set_row, dict):
                return api_error("Each set must be an object", 400, code="invalid_field")
            set_notes, err2 = _coerce_str(
                set_row.get("notes", ""),
                f"exercises[{ex_idx}].sets[{set_idx}].notes",
                required=False,
                max_len=500,
            )
            if err2:
                return err2
            if set_notes:
                set_row["notes"] = set_notes
            else:
                set_row.pop("notes", None)
            if not set_row.get("set_number"):
                set_row["set_number"] = set_idx + 1

    notes, err2 = _coerce_str(data.get("notes", ""), "notes", required=False, max_len=2000)
    if err2:
        return err2

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
            return err2
        activity_type, err2 = _coerce_str(
            cardio_payload.get("activity_type") or cardio_rec.get("type") or cardio_rec.get("machine") or "Cardio",
            "cardio.activity_type",
            required=False,
            max_len=64,
        )
        if err2:
            return err2
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

    # Find the recommendation
    recommendation = None
    for rec in WORKOUT_RECOMMENDATIONS:
        if rec.get("id") == recommendation_id:
            recommendation = rec
            break

    # Calculate adherence if recommendation found
    adherence = {"followed": True, "skipped": [], "modified": [], "added": []}
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
                        actual_weight = max(s.get("weight_lbs", 0) for s in act_ex["sets"])
                        if abs(actual_weight - rec_ex["target_weight"]) > 10:
                            adherence["modified"].append({
                                "exercise": rec_ex["exercise"],
                                "recommended_weight": rec_ex["target_weight"],
                                "actual_weight": actual_weight
                            })

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

    overall_fatigue = data.get("fatigue", 5)
    try:
        overall_fatigue = int(overall_fatigue)
    except Exception:
        overall_fatigue = 5
    overall_fatigue = max(1, min(overall_fatigue, 10))

    # Auto-fill muscle_group from machine name if missing
    _MACHINE_TO_MUSCLE = {
        "Chest Press": "chest", "Lat Pulldown": "back", "Mid Row": "back",
        "Shoulder Press": "shoulders", "Deltoid Fly": "shoulders", "Rear Delt Fly": "shoulders",
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
            ex["muscle_group"] = _MACHINE_TO_MUSCLE.get(ex.get("machine", ""), "unknown")

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

    WORKOUTS.append(workout_entry)
    COMPLETED_WORKOUTS.append(workout_entry)
    save_json(WORKOUTS_FILE, WORKOUTS)  # Persist to file
    if cardio_log_entry:
        cardio_log_entry["workout_id"] = workout_id
        CARDIO_DATA.append(cardio_log_entry)
        save_json(CARDIO_FILE, CARDIO_DATA)
    _notify_workout_logged(workout_entry)

    return jsonify({
        "status": "success",
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
            "food_logs": get_food_logs(user_id)
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
    global WORKOUTS, SORENESS_DATA, CARDIO_DATA, RECOVERY_DATA, USER_SETTINGS, BASELINES_DATA, BODY_DATA, SLEEP_DATA, NUTRITION_DATA

    try:
        backup_data, err = get_json_body(required=True)
        if err:
            return err

        # Validate backup structure
        if "data" not in backup_data or not isinstance(backup_data.get("data"), dict):
            return api_error("Invalid backup format: missing 'data' object", 400, code="invalid_field")

        data = backup_data["data"]

        # Restore each data type if present
        if "workouts" in data:
            WORKOUTS.clear()
            WORKOUTS.extend(data["workouts"])
            save_json(WORKOUTS_FILE, WORKOUTS)

        if "soreness" in data:
            SORENESS_DATA.clear()
            SORENESS_DATA.extend(data["soreness"])
            save_json(SORENESS_FILE, SORENESS_DATA)

        if "cardio" in data:
            CARDIO_DATA.clear()
            CARDIO_DATA.extend(data["cardio"])
            save_json(CARDIO_FILE, CARDIO_DATA)

        if "recovery" in data:
            RECOVERY_DATA.clear()
            RECOVERY_DATA.extend(data["recovery"])
            save_json(RECOVERY_FILE, RECOVERY_DATA)

        if "settings" in data:
            USER_SETTINGS.clear()
            USER_SETTINGS.update(data["settings"])
            save_json(SETTINGS_FILE, USER_SETTINGS)

        if "baselines" in data:
            BASELINES_DATA.clear()
            BASELINES_DATA.update(data["baselines"])
            save_json(BASELINES_FILE, BASELINES_DATA)

        if "body" in data:
            BODY_DATA.clear()
            BODY_DATA.extend(data["body"])
            save_json(BODY_FILE, BODY_DATA)

        if "sleep" in data:
            SLEEP_DATA.clear()
            SLEEP_DATA.extend(data["sleep"])
            save_json(SLEEP_FILE, SLEEP_DATA)

        if "nutrition" in data:
            NUTRITION_DATA.clear()
            NUTRITION_DATA.extend(data["nutrition"])
            save_json(NUTRITION_FILE, NUTRITION_DATA)

        if "food_logs" in data:
            user_id = _current_data_user_id()
            clear_food_logs(user_id)
            for food_log in data["food_logs"]:
                if isinstance(food_log, dict):
                    add_food_log(user_id, food_log)

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
                "food_logs": len(data.get("food_logs", []))
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
    improving = [ex for ex, d in progression.items() if d["status"] == "On Track"]
    plateaus = [ex for ex, d in progression.items() if d["status"] == "Plateau"]
    regressions = [ex for ex, d in progression.items() if d["status"] == "Regression"]

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
    if consistency["current_streak"] >= 4:
        insights.append({
            "type": "positive",
            "icon": "local_fire_department",
            "title": f"{consistency['current_streak']} session streak!",
            "detail": "Keep up the consistency"
        })
    elif consistency["days_since_last"] > 7:
        insights.append({
            "type": "warning",
            "icon": "schedule",
            "title": "Time to train!",
            "detail": f"{consistency['days_since_last']} days since last workout"
        })

    # Balance insight
    if push_pull["color"] == "red":
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
    """Get stats on how well recommendations were followed."""
    total_recommendations = len(WORKOUT_RECOMMENDATIONS)
    followed = sum(1 for w in COMPLETED_WORKOUTS if w.get("adherence", {}).get("followed", False))

    skipped_exercises = {}
    for w in COMPLETED_WORKOUTS:
        for ex in w.get("adherence", {}).get("skipped", []):
            skipped_exercises[ex] = skipped_exercises.get(ex, 0) + 1

    return jsonify({
        "total_recommendations": total_recommendations,
        "followed_count": followed,
        "adherence_rate": round(followed / total_recommendations * 100) if total_recommendations else 0,
        "frequently_skipped": sorted(skipped_exercises.items(), key=lambda x: -x[1])[:5]
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
        readiness = get_readiness_score(muscle, SORENESS_DATA, volume, CARDIO_DATA)

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
    entries=[]
    if isinstance(data.get('entries'), list):
        raw=data['entries']
    elif isinstance(data.get('csv'), str):
        import csv, io
        raw=list(csv.DictReader(io.StringIO(data['csv'])))
    else:
        return api_error('Provide entries[] or csv text', 400, code='invalid_field')
    for r in raw:
        date=(r.get('date') or r.get('day') or '')[:10]
        if not date: continue
        e={
            'date':date,
            'source': r.get('source') or 'apple_watch',
            'sleep_duration_min': int(float(r.get('sleep_duration_min') or r.get('duration_min') or 0)),
            'time_in_bed_min': int(float(r.get('time_in_bed_min') or r.get('in_bed_min') or 0)),
            'deep_min': int(float(r.get('deep_min') or 0)),
            'rem_min': int(float(r.get('rem_min') or 0)),
            'light_min': int(float(r.get('light_min') or 0)),
            'awake_min': int(float(r.get('awake_min') or 0)),
            'sleep_start': r.get('sleep_start'),
            'sleep_end': r.get('sleep_end')
        }
        entries.append(e)
    merged={x.get('date'):x for x in SLEEP_DATA}
    for e in entries: merged[e['date']]=e
    SLEEP_DATA.clear(); SLEEP_DATA.extend(sorted(merged.values(), key=lambda x:x.get('date')))
    save_json(SLEEP_FILE, SLEEP_DATA)
    return jsonify({'status':'success','imported':len(entries)})

@app.route('/api/sleep/analytics')
def sleep_analytics():
    # Pull from Oura sleep SQLite first, fall back to SLEEP_DATA JSON
    rows = []
    try:
        import sqlite3 as _sq
        _db = _sq.connect(os.path.join(DATA_DIR, 'oura_daily.sqlite3'))
        _db.row_factory = _sq.Row
        _cur = _db.execute("SELECT * FROM oura_sleep WHERE type='long_sleep' ORDER BY day")
        for r in _cur.fetchall():
            rows.append({
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
        _db.close()
    except Exception:
        pass
    # Fall back to manual SLEEP_DATA if no Oura data
    if not rows:
        rows = sorted(SLEEP_DATA, key=lambda x: x.get('date'))
    if not rows: return jsonify({'history':[],'consistency_score':None,'sleep_perf_correlation':None})
    # prefer apple watch source if duplicates
    dedup={}
    for r in rows:
        d=r.get('date')
        if d not in dedup or r.get('source')=='apple_watch': dedup[d]=r
    rows=sorted(dedup.values(), key=lambda x:x.get('date'))
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
    # volume per muscle current week
    vol=calculate_volume(WORKOUTS, weeks=1)
    lm=USER_SETTINGS.get('volume_landmarks', {}).get('default', {"mv":6,"mev":9,"mav_min":12,"mav_max":18,"mrv":22})
    volume_landmarks=[]
    for m,v in vol.items():
        sets=v.get('sets',0)
        zone='below_mv' if sets<lm['mv'] else 'mv' if sets<lm['mev'] else 'mev_to_mav' if sets<=lm['mav_max'] else 'mrv_risk' if sets>=lm['mrv'] else 'mav_high'
        volume_landmarks.append({'muscle':m,'sets':sets,'landmarks':lm,'zone':zone})
    # fatigue composite
    try:
        hrv_trend=compute_hrv_trend(OURA_DB_FILE).get('trend')
    except Exception:
        hrv_trend='unknown'
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
    deload= fatigue >= USER_SETTINGS.get('fatigue_threshold',72)
    perf_decline = detect_deload_need(WORKOUTS,SORENESS_DATA).get('recommended',False)
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
            requestline = re.sub(r"([?&]token=)[^&\s]+", r"\1<redacted>", self.requestline)
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
