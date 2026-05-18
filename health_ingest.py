"""
Apple Health Data Ingestion for Fitness Dashboard

Reads HealthKit JSON exports from ~/Documents/Health/ and serves them
via API endpoints for the fitness dashboard frontend.

Data sources:
- healthkit_timeseries_multi_*.json → steps, active energy, RHR, HRV, resp rate, body temp
- healthkit_samples_workout_*.json → 398 workouts with duration, energy, distance
- healthkit_samples_sleepAnalysis_*.json → 24,800 sleep records
"""

import json
import os
import glob
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from collections import defaultdict

HEALTH_DIR = os.path.expanduser("~/Documents/Health")

def _ms_to_iso(ms):
    """Convert milliseconds since epoch to ISO date string."""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return None

def _ms_to_datetime(ms):
    """Convert milliseconds since epoch to ISO datetime string."""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None

def load_json(pattern):
    """Load the most recent JSON file matching a glob pattern."""
    files = sorted(glob.glob(os.path.join(HEALTH_DIR, pattern)), reverse=True)
    if not files:
        return {}
    with open(files[0], "r") as f:
        return json.load(f)

# ── Activity type mapping ──
ACTIVITY_MAP = {
    1: "Walking", 2: "Running", 3: "Cycling", 4: "Hiking",
    6: "Traditional Strength Training", 7: "Cross Country Skiing",
    10: "Swimming", 11: "Yoga", 13: "Elliptical",
    14: "Stair Climbing", 16: "Dance", 20: "Core Training",
    22: "Functional Strength Training", 23: "Cross Training",
    25: "Other", 28: "High Intensity Interval Training",
    30: "Rowing", 32: "Curling", 36: "Pilates",
    37: "Basketball", 39: "Jump Rope", 40: "Kickboxing",
    52: "Table Tennis", 54: "Tai Chi", 55: "Volleyball",
    58: "Wrestling", 64: "Stairs",
}

def parse_workouts():
    """Load and parse workout data."""
    data = load_json("healthkit_samples_workout_*.json")
    workouts = data.get("workout", [])
    parsed = []
    for w in workouts:
        raw_activity = w.get("activity", "")
        # Extract activity type number from HKWorkoutActivityType(rawValue: N)
        import re
        match = re.search(r"rawValue:\s*(\d+)", raw_activity)
        activity_num = int(match.group(1)) if match else 0
        activity_name = ACTIVITY_MAP.get(activity_num, raw_activity.split("(")[0].strip() if raw_activity else "Other")
        
        parsed.append({
            "date": _ms_to_iso(w.get("start_time_ms", 0)),
            "start": _ms_to_datetime(w.get("start_time_ms", 0)),
            "end": _ms_to_datetime(w.get("end_time_ms", 0)),
            "activity": activity_name,
            "duration_min": round(w.get("duration_sec", 0) / 60, 1),
            "energy_kcal": round(w.get("total_energy_kcal") or 0, 1),
            "distance_m": round(w.get("total_distance_m") or 0, 1),
            "source": w.get("source_name", ""),
        })
    return parsed

def parse_sleep():
    """Load and parse sleep analysis data."""
    data = load_json("healthkit_samples_sleepAnalysis_*.json")
    records = data.get("sleepAnalysis", [])
    # Group by date and compute total sleep per day
    daily_sleep = defaultdict(float)
    for r in records:
        start = r.get("start_time_ms", 0)
        end = r.get("end_time_ms", 0)
        if start and end:
            date_str = _ms_to_iso(start)
            if date_str:
                daily_sleep[date_str] += (end - start) / (1000 * 3600)  # hours
    return [{"date": d, "hours": round(h, 2)} for d, h in sorted(daily_sleep.items())]

def parse_timeseries(metric_key, file_pattern):
    """Load a timeseries metric from a multi-metric JSON file."""
    data = load_json(file_pattern)
    records = data.get(metric_key, [])
    daily = defaultdict(list)
    for r in records:
        date_str = _ms_to_iso(r.get("time_ms", 0))
        val = r.get("value")
        if date_str and val is not None:
            daily[date_str].append(float(val))
    # Return daily aggregates
    result = []
    for d, vals in sorted(daily.items()):
        result.append({
            "date": d,
            "avg": round(sum(vals) / len(vals), 2),
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "samples": len(vals),
        })
    return result

def parse_steps():
    return parse_timeseries("stepCount", "healthkit_timeseries_multi_20260111T221509Z.json")

def parse_active_energy():
    return parse_timeseries("activeEnergyBurned", "healthkit_timeseries_multi_20260111T221509Z.json")

def parse_rhr():
    return parse_timeseries("restingHeartRate", "healthkit_timeseries_multi_20260111T221514Z.json")

def parse_hrv():
    return parse_timeseries("heartRateVariabilitySDNN", "healthkit_timeseries_multi_20260111T221514Z.json")

# ── Flask blueprint for health API ──

def register_health_routes(app):
    """Register Apple Health API routes on the Flask app."""
    
    @app.route("/api/health/workouts")
    def health_workouts():
        days = request.args.get("days", 30, type=int)
        workouts = parse_workouts()
        if days > 0:
            cutoff = (datetime.now() - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
            workouts = [w for w in workouts if w["date"] >= cutoff]
        return jsonify({"workouts": workouts, "total": len(workouts)})
    
    @app.route("/api/health/sleep")
    def health_sleep():
        days = request.args.get("days", 30, type=int)
        sleep = parse_sleep()
        if days > 0:
            cutoff = (datetime.now() - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
            sleep = [s for s in sleep if s["date"] >= cutoff]
        return jsonify({"sleep": sleep, "total": len(sleep)})
    
    @app.route("/api/health/steps")
    def health_steps():
        days = request.args.get("days", 30, type=int)
        steps = parse_steps()
        if days > 0:
            cutoff = (datetime.now() - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
            steps = [s for s in steps if s["date"] >= cutoff]
        return jsonify({"steps": steps, "total": len(steps)})
    
    @app.route("/api/health/vitals")
    def health_vitals():
        days = request.args.get("days", 30, type=int)
        rhr = parse_rhr()
        hrv = parse_hrv()
        if days > 0:
            cutoff = (datetime.now() - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
            rhr = [r for r in rhr if r["date"] >= cutoff]
            hrv = [h for h in hrv if h["date"] >= cutoff]
        return jsonify({"rhr": rhr, "hrv": hrv})
    
    @app.route("/api/health/summary")
    def health_summary():
        """Aggregate summary of all health data."""
        workouts = parse_workouts()
        sleep = parse_sleep()
        steps = parse_steps()
        rhr = parse_rhr()
        hrv = parse_hrv()
        
        # Last 7 days
        cutoff7 = (datetime.now() - __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d")
        cutoff30 = (datetime.now() - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d")
        
        recent_workouts = [w for w in workouts if w["date"] >= cutoff7]
        recent_sleep = [s for s in sleep if s["date"] >= cutoff7]
        recent_steps = [s for s in steps if s["date"] >= cutoff7]
        
        return jsonify({
            "workouts_total": len(workouts),
            "workouts_7d": len(recent_workouts),
            "workouts_30d": len([w for w in workouts if w["date"] >= cutoff30]),
            "avg_sleep_7d": round(sum(s["hours"] for s in recent_sleep) / max(len(recent_sleep), 1), 2),
            "avg_steps_7d": round(sum(s["avg"] for s in recent_steps) / max(len(recent_steps), 1), 0),
            "latest_rhr": rhr[-1]["avg"] if rhr else None,
            "latest_hrv": hrv[-1]["avg"] if hrv else None,
            "sleep_total_days": len(sleep),
            "steps_total_days": len(steps),
        })