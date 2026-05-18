#!/usr/bin/env python3
"""FIT-22 diagnostic for recommendation load-source selection."""

import json
import os
import sys

os.environ.setdefault("SECRET_KEY", "fit22-diagnostic-secret")
os.environ.setdefault("HEALTH_SYNC_TOKEN", "fit22-diagnostic-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app


EXERCISES = (
    {"name": "Chest Press", "baseline": 146, "stale_progression": 101.8},
    {"name": "Leg Press", "baseline": 333, "stale_progression": 250.7},
    {"name": "Crunch Machine", "baseline": 280, "stale_progression": 74.4},
)


def build_entry(fixture):
    exercise_name = fixture["name"]
    progression = {
        exercise_name: {
            "status": "On Track",
            "current_e1rm": fixture["stale_progression"],
            "peak_e1rm": fixture["stale_progression"],
            "trend_pct": 0,
            "history": [],
        }
    }
    volume_data = {}
    goal_params = app.GOAL_PARAMETERS[app.TrainingGoal.HYBRID_WEIGHT_LOSS_TONING.value]
    meso_week = 1
    meso_plan = app.MESOCYCLE_PLAN.get(meso_week, app.MESOCYCLE_PLAN[1])
    oura_readiness = None
    volume_multiplier = meso_plan["volume_multiplier"]
    exercise = app.EXERCISE_LOOKUP[exercise_name]
    return app._build_exercise_entry(
        exercise_name=exercise_name,
        muscle=exercise["muscle"],
        is_compound=exercise["compound"],
        goal_params=goal_params,
        meso_week=meso_week,
        volume_multiplier=volume_multiplier,
        oura_readiness=oura_readiness,
        volume_data=volume_data,
        soreness_data=[],
        progression=progression,
        workouts=[],
        time_per_set=goal_params.get("time_per_set_minutes", 3),
    )


def main():
    rows = []
    failures = []
    original_baselines = dict(app.BASELINES_DATA)
    app.BASELINES_DATA.clear()
    app.BASELINES_DATA.update({fixture["name"]: fixture["baseline"] for fixture in EXERCISES})

    try:
        for fixture in EXERCISES:
            exercise_name = fixture["name"]
            entry = build_entry(fixture)
            baseline = app.BASELINES_DATA.get(exercise_name)
            row = {
                "exercise": exercise_name,
                "baseline_json": baseline,
                "stale_progression_e1rm": fixture["stale_progression"],
                "load_source": entry.get("load_source"),
                "load_e1rm": entry.get("load_e1rm"),
                "target_weight": entry.get("target_weight"),
                "target_reps": entry.get("target_reps"),
                "rpe_target": entry.get("rpe_target"),
                "load_source_detail": entry.get("load_source_detail"),
            }
            rows.append(row)
            if not baseline:
                failures.append(f"{exercise_name} missing baseline_json")
            if not row["load_source"]:
                failures.append(f"{exercise_name} missing load_source")
            if row["load_source"] == "hardcoded":
                failures.append(f"{exercise_name} used hardcoded load source")
            if baseline and (row["load_e1rm"] is None or row["load_e1rm"] < baseline * 0.95):
                failures.append(f"{exercise_name} load_e1rm below calibrated baseline")
    finally:
        app.BASELINES_DATA.clear()
        app.BASELINES_DATA.update(original_baselines)

    print(json.dumps(rows, indent=2, sort_keys=True))
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
