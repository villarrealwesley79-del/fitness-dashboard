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
from data_loader import parse_workout_log, get_workout_summary

app = Flask(__name__)


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

# Current user settings (in production, stored in database per user)
USER_SETTINGS = {
    "training_goal": TrainingGoal.HYPERTROPHY.value,
    "sessions_per_week_target": 3,
    "available_time_minutes": 60  # Default 60 minutes
}

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


# In-memory data store (would be database in production)
WORKOUTS = []
SORENESS_DATA = []


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

        if history[0]["e1rm"] > 0:
            trend_pct = ((current_e1rm - history[0]["e1rm"]) / history[0]["e1rm"]) * 100
        else:
            trend_pct = 0

        results[exercise] = {
            "status": status,
            "current_e1rm": round(current_e1rm, 1),
            "peak_e1rm": round(peak_e1rm, 1),
            "trend_pct": round(trend_pct, 1),
            "history": history
        }

    return results


def calculate_volume(workouts, weeks=4):
    """Calculate volume load per muscle group over recent weeks."""
    cutoff = datetime.now() - timedelta(days=weeks * 7)
    muscle_volume = {}

    for workout in workouts:
        workout_date = datetime.strptime(workout["date"], '%Y-%m-%d')
        if workout_date < cutoff:
            continue

        for exercise in workout.get("exercises", []):
            muscle = exercise["muscle_group"]
            if muscle not in muscle_volume:
                muscle_volume[muscle] = {"sets": 0, "volume_load": 0, "last_trained": workout["date"]}

            for s in exercise.get("sets", []):
                muscle_volume[muscle]["sets"] += 1
                muscle_volume[muscle]["volume_load"] += s["weight_lbs"] * s["reps"]

            muscle_volume[muscle]["last_trained"] = max(muscle_volume[muscle]["last_trained"], workout["date"])

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


def get_readiness_score(muscle, soreness_data, volume_data):
    """Calculate readiness score for a muscle group."""
    muscle_soreness = [s for s in soreness_data if s["muscle"] == muscle]
    soreness_level = muscle_soreness[-1]["soreness_level"] if muscle_soreness else 0

    last_trained = volume_data.get(muscle, {}).get("last_trained")
    recovery_debt = 0
    if last_trained:
        days_since = (datetime.now() - datetime.strptime(last_trained, '%Y-%m-%d')).days
        if days_since < 2:
            recovery_debt = 2

    readiness = 10 - soreness_level - recovery_debt

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
        "score": readiness,
        "soreness": soreness_level,
        "recovery_debt": recovery_debt,
        "recommendation": recommendation,
        "color": color
    }


def generate_next_workout(workouts, soreness_data, goal=None, available_time=None):
    """Generate optimal workout prescription based on training goal and available time."""
    if goal is None:
        goal = USER_SETTINGS.get("training_goal", TrainingGoal.HYPERTROPHY.value)

    if available_time is None:
        available_time = USER_SETTINGS.get("available_time_minutes", 60)

    goal_params = GOAL_PARAMETERS.get(goal, GOAL_PARAMETERS[TrainingGoal.HYPERTROPHY.value])
    time_per_set = goal_params.get("time_per_set_minutes", 3)

    # Calculate max exercises based on available time
    # Account for warmup (5 min) and cooldown (5 min)
    effective_time = available_time - 10
    sets_per_exercise = goal_params["sets_per_exercise"]
    time_per_exercise = time_per_set * sets_per_exercise
    max_exercises = max(2, int(effective_time / time_per_exercise))

    volume_data = calculate_volume(workouts, weeks=4)
    muscle_groups = list(volume_data.keys()) or ["chest", "back", "quads", "shoulders"]

    readiness_scores = {}
    for muscle in muscle_groups:
        readiness_scores[muscle] = get_readiness_score(muscle, soreness_data, volume_data)

    available_muscles = [m for m, r in readiness_scores.items() if r["score"] >= 5]

    # If not enough muscles available, add default ones
    default_muscles = ["chest", "back", "quads", "shoulders", "hamstrings", "biceps", "triceps", "core", "calves"]
    for m in default_muscles:
        if m not in available_muscles and len(available_muscles) < max_exercises:
            available_muscles.append(m)

    progression = calculate_progression_status(workouts)

    exercise_map = {
        "chest": ("Chest Press", True),
        "back": ("Lat Pulldown", True),
        "quads": ("Leg Press", True),
        "shoulders": ("Shoulder Press", False),
        "hamstrings": ("Leg Curl", True),
        "triceps": ("Seated Dip", True),
        "biceps": ("Biceps Curl", False),
        "core": ("Crunch Machine", False),
        "calves": ("Calf Raise", False),
    }

    exercises = []
    min_reps, max_reps = goal_params["rep_range"]
    target_sets = goal_params["sets_per_exercise"]
    intensity_pct = goal_params["intensity_pct"] / 100
    rest_time = goal_params["rest_minutes"]

    # Prioritize compound exercises when time is limited
    sorted_muscles = sorted(available_muscles, key=lambda m: (
        0 if exercise_map.get(m, (None, False))[1] else 1,  # Compounds first
        readiness_scores.get(m, {}).get("score", 0) * -1     # Higher readiness
    ))

    for muscle in sorted_muscles[:max_exercises]:
        if muscle not in exercise_map:
            continue

        exercise_name, is_compound = exercise_map[muscle]
        ex_progression = progression.get(exercise_name, {})
        status = ex_progression.get("status", "On Track")
        current_e1rm = ex_progression.get("current_e1rm", 100)

        # Apply goal-specific intensity and adjust for progression status
        if status == "On Track":
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

        # Rep range based on goal
        target_reps = (min_reps + max_reps) // 2

        exercises.append({
            "exercise": exercise_name,
            "muscle": muscle,
            "is_compound": is_compound,
            "target_weight": max(5, target_weight),  # Minimum 5 lbs
            "target_reps": target_reps,
            "target_sets": max(2, sets),
            "rationale": rationale,
            "rest_minutes": rest_time,
            "rpe_target": goal_params["rpe_target"],
            "estimated_time": round(max(2, sets) * time_per_set)
        })

    exercises.sort(key=lambda x: (not x["is_compound"], x["muscle"]))

    avoid_muscles = [
        {"muscle": m.title(), "reason": f"Readiness {r['score']}/10"}
        for m, r in readiness_scores.items() if r["score"] < 5
    ]

    upper = {"chest", "back", "shoulders", "biceps", "triceps"}
    lower = {"quads", "hamstrings", "glutes", "calves"}
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
        "exercises": exercises,
        "muscles_to_avoid": avoid_muscles,
        "time_adjusted": available_time < 60  # Flag if workout was time-constrained
    }

    # Store recommendation for tracking
    WORKOUT_RECOMMENDATIONS.append(recommendation)

    return recommendation


def generate_alerts(workouts, soreness_data):
    """Generate alerts based on current data."""
    alerts = []
    progression = calculate_progression_status(workouts)
    volume = calculate_volume(workouts, weeks=4)

    for exercise, data in progression.items():
        if data["status"] == "Plateau":
            alerts.append({
                "priority": "HIGH",
                "type": "Plateau Detected",
                "message": f"{exercise}: No progression in recent sessions",
                "action": "Consider deload or exercise variation",
                "color": "orange"
            })
        elif data["status"] == "Regression":
            alerts.append({
                "priority": "HIGH",
                "type": "Regression",
                "message": f"{exercise}: e1RM down from peak",
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
                "priority": "LOW",
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
        workout_date = datetime.strptime(workout["date"], '%Y-%m-%d')
        if workout_date < cutoff:
            continue

        for exercise in workout.get("exercises", []):
            muscle = exercise["muscle_group"]
            num_sets = len(exercise.get("sets", []))

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


# Initialize with real data if available, otherwise use sample data
WORKOUT_LOG_PATH = os.path.join(os.path.dirname(__file__), "support/Workout_Log_LogTab_Past_Workouts.md")
if os.path.exists(WORKOUT_LOG_PATH):
    print(f"Loading real workout data from {WORKOUT_LOG_PATH}")
    WORKOUTS, SORENESS_DATA = parse_workout_log(WORKOUT_LOG_PATH)
    summary = get_workout_summary(WORKOUTS)
    print(f"Loaded {summary['total_sessions']} sessions, {summary['total_sets']} sets")
else:
    print("No workout log found, using sample data")
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

    readiness_scores = [get_readiness_score(m, SORENESS_DATA, volume)["score"] for m in volume.keys()]
    avg_readiness = sum(readiness_scores) / len(readiness_scores) if readiness_scores else 7

    muscle_data = []
    for muscle, data in volume.items():
        readiness = get_readiness_score(muscle, SORENESS_DATA, volume)
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
        "next_workout": generate_next_workout(WORKOUTS, SORENESS_DATA),
        "advanced_kpis": {
            "personal_records": prs,
            "consistency": consistency,
            "push_pull_balance": push_pull,
            "deload_check": deload,
            "injury_risk": injury_risk,
            "summary_stats": summary_stats
        }
    })


@app.route('/api/add-workout', methods=['POST'])
def add_workout():
    """Add a new workout."""
    data = request.json
    WORKOUTS.append(data)
    return jsonify({"status": "success"})


@app.route('/api/add-soreness', methods=['POST'])
def add_soreness():
    """Add soreness data."""
    data = request.json
    SORENESS_DATA.append(data)
    return jsonify({"status": "success"})


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
            "available_goals": [
                {"value": g, "name": p["name"], "description": p["description"]}
                for g, p in GOAL_PARAMETERS.items()
            ],
            "time_options": TIME_OPTIONS
        })
    else:
        data = request.json
        if "training_goal" in data:
            USER_SETTINGS["training_goal"] = data["training_goal"]
        if "sessions_per_week_target" in data:
            USER_SETTINGS["sessions_per_week_target"] = data["sessions_per_week_target"]
        if "available_time_minutes" in data:
            USER_SETTINGS["available_time_minutes"] = data["available_time_minutes"]
        return jsonify({"status": "success", "settings": USER_SETTINGS})


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


@app.route('/api/complete-workout', methods=['POST'])
def complete_workout():
    """Complete a workout and track adherence to recommendations."""
    data = request.json
    recommendation_id = data.get("recommendation_id")
    actual_exercises = data.get("exercises", [])
    notes = data.get("notes", "")

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
    workout_entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "session_type": data.get("session_type", "general"),
        "duration_minutes": data.get("duration_minutes", 45),
        "exercises": actual_exercises,
        "overall_fatigue": data.get("fatigue", 5),
        "notes": notes,
        "recommendation_id": recommendation_id,
        "adherence": adherence
    }

    WORKOUTS.append(workout_entry)
    COMPLETED_WORKOUTS.append(workout_entry)

    return jsonify({
        "status": "success",
        "adherence": adherence,
        "message": "Workout logged! Navigating to history..."
    })


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
            for s in exercise.get("sets", []):
                volume = s["weight_lbs"] * s["reps"]
                notes = s.get("notes", "")
                lines.append(f"| {workout['date']} | {machine} | {s['set_number']} | {s['reps']} | {s['weight_lbs']} | {volume} | {notes} |")

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

    if regressions:
        insights.append({
            "type": "negative",
            "icon": "trending_down",
            "title": f"{len(regressions)} exercises regressing",
            "detail": "Check recovery, sleep, and nutrition"
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
    local_ip = get_local_ip()
    port = 5050

    print("\n" + "="*60)
    print("  FITNESS INTELLIGENCE DASHBOARD")
    print("="*60)
    print(f"\n  Local access:     http://localhost:{port}")
    print(f"  WiFi access:      http://{local_ip}:{port}")
    print("\n  For cellular data access, run:")
    print(f"    ngrok http {port}")
    print("  Then use the ngrok URL on your phone")
    print("\n" + "="*60 + "\n")

    app.run(host='0.0.0.0', port=port, debug=True)
