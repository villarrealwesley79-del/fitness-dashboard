"""Legacy /api/health compatibility routes backed by apple_health_parser."""

from datetime import datetime

from flask import jsonify, request

import apple_health_parser as _canonical

# Retained for callers that still patch the legacy module during test setup.
HEALTH_DIR = _canonical.HEALTH_DIR
ACTIVITY_MAP = _canonical.ACTIVITY_MAP


def _ms_to_iso(ms):
    return _canonical._ms_to_iso(ms)


def _ms_to_datetime(ms):
    return _canonical._ms_to_datetime(ms)


def load_json(pattern):
    return _canonical._load_json(pattern)


def parse_workouts():
    return _canonical.parse_workouts()


def parse_sleep():
    return _canonical.parse_sleep()


def parse_steps():
    return _canonical.parse_steps()


def parse_active_energy():
    return _canonical.parse_active_energy()


def parse_rhr():
    return _canonical.parse_rhr()


def parse_hrv():
    return _canonical.parse_hrv()


def _within_days(records, days):
    if days <= 0:
        return records
    cutoff = _canonical._local_date_cutoff(days)
    return [record for record in records if record.get("date", "") >= cutoff]


def _legacy_summary():
    summary = _canonical.get_summary()
    workouts = parse_workouts()
    cutoff7 = _canonical._local_date_cutoff(7)
    cutoff30 = _canonical._local_date_cutoff(30)
    summary.update({
        "workouts_total": len(workouts),
        "workouts_7d": len([workout for workout in workouts if workout.get("date", "") >= cutoff7]),
        "workouts_30d": len([workout for workout in workouts if workout.get("date", "") >= cutoff30]),
    })
    return {
        key: summary.get(key)
        for key in (
            "workouts_total",
            "workouts_7d",
            "workouts_30d",
            "avg_sleep_7d",
            "avg_steps_7d",
            "latest_rhr",
            "latest_hrv",
            "sleep_total_days",
            "steps_total_days",
        )
    }


def register_health_routes(app):
    """Register compatibility routes without duplicating Apple Health parsing."""

    @app.route("/api/health/workouts")
    def health_workouts():
        workouts = _within_days(parse_workouts(), request.args.get("days", 30, type=int))
        return jsonify({"workouts": workouts, "total": len(workouts)})

    @app.route("/api/health/sleep")
    def health_sleep():
        sleep = _within_days(parse_sleep(), request.args.get("days", 30, type=int))
        return jsonify({"sleep": sleep, "total": len(sleep)})

    @app.route("/api/health/steps")
    def health_steps():
        steps = _within_days(parse_steps(), request.args.get("days", 30, type=int))
        return jsonify({"steps": steps, "total": len(steps)})

    @app.route("/api/health/vitals")
    def health_vitals():
        days = request.args.get("days", 30, type=int)
        return jsonify({
            "rhr": _within_days(parse_rhr(), days),
            "hrv": _within_days(parse_hrv(), days),
        })

    @app.route("/api/health/summary")
    def health_summary():
        return jsonify(_legacy_summary())
