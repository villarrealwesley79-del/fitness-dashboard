#!/usr/bin/env python3
"""
Parse workout logs from markdown format.
"""

import re
from collections import defaultdict
from datetime import datetime


# Muscle group mappings for each machine
MACHINE_TO_MUSCLE = {
    "Chest Press": "chest",
    "Lat Pulldown": "back",
    "Mid Row": "back",
    "Seated Dip": "triceps",
    "Biceps Curl": "biceps",
    "Shoulder Press": "shoulders",
    "Deltoid Fly": "shoulders",
    "Leg Press": "quads",
    "Seated Leg Press": "quads",
    "Leg Curl": "hamstrings",
    "Seated Leg Curl": "hamstrings",
    "Calf Raise": "calves",
    "Crunch Machine": "core",
    "Back Extension": "erectors",
    "Low Back": "erectors",
    "Adductor": "adductors",
}


def parse_workout_log(filepath: str) -> tuple[list, list]:
    """
    Parse workout log markdown file.
    Returns (workouts, soreness_data)
    """
    with open(filepath, 'r') as f:
        content = f.read()

    # Find all table rows (skip header rows)
    lines = content.split('\n')
    data_rows = []

    for line in lines:
        if line.startswith('|') and '---' not in line and 'Date' not in line:
            # Parse table row
            parts = [p.strip() for p in line.split('|')[1:-1]]
            if len(parts) >= 8:
                try:
                    date = parts[0]
                    machine = parts[1]
                    set_role = parts[2]
                    set_number = int(parts[3]) if parts[3] else 0
                    reps = int(parts[4]) if parts[4] else 0
                    weight = float(parts[5]) if parts[5] else 0
                    notes = parts[6] if len(parts) > 6 else ""
                    volume = int(parts[7]) if len(parts) > 7 and parts[7] else 0
                    is_top_set = int(parts[8]) if len(parts) > 8 and parts[8] else 0

                    data_rows.append({
                        "date": date,
                        "machine": machine,
                        "set_role": set_role,
                        "set_number": set_number,
                        "reps": reps,
                        "weight": weight,
                        "notes": notes,
                        "volume": volume,
                        "is_top_set": is_top_set
                    })
                except (ValueError, IndexError):
                    continue

    # Group by date to create workout entries
    workouts_by_date = defaultdict(list)
    for row in data_rows:
        workouts_by_date[row["date"]].append(row)

    workouts = []
    for date, rows in sorted(workouts_by_date.items()):
        # Group by machine
        exercises_by_machine = defaultdict(list)
        for row in rows:
            exercises_by_machine[row["machine"]].append(row)

        exercises = []
        for machine, sets in exercises_by_machine.items():
            muscle_group = MACHINE_TO_MUSCLE.get(machine, "other")

            # Calculate RPE based on notes and set role
            set_data = []
            for s in sorted(sets, key=lambda x: x["set_number"]):
                # Estimate RPE from notes
                notes_lower = s["notes"].lower()
                if "fail" in notes_lower or "couldn't" in notes_lower:
                    rpe = 10
                elif "struggling" in notes_lower or "struggle" in notes_lower:
                    rpe = 9
                elif "burn" in notes_lower or "hard" in notes_lower:
                    rpe = 8
                elif "easy" in notes_lower or "felt good" in notes_lower:
                    rpe = 6
                elif s["is_top_set"]:
                    rpe = 8.5
                elif s["set_role"] == "Ramp":
                    rpe = 6
                else:
                    rpe = 7.5

                set_data.append({
                    "set_number": s["set_number"],
                    "weight_lbs": s["weight"],
                    "reps": s["reps"],
                    "rpe": rpe,
                    "notes": s["notes"]
                })

            exercises.append({
                "machine": machine,
                "muscle_group": muscle_group,
                "sets": set_data
            })

        # Determine session type
        muscles_hit = set(e["muscle_group"] for e in exercises)
        upper_muscles = {"chest", "back", "shoulders", "biceps", "triceps"}
        lower_muscles = {"quads", "hamstrings", "erectors", "glutes", "calves", "adductors"}

        has_upper = bool(muscles_hit & upper_muscles)
        has_lower = bool(muscles_hit & lower_muscles)

        if has_upper and has_lower:
            session_type = "full_body"
        elif has_upper:
            session_type = "upper"
        elif has_lower:
            session_type = "lower"
        else:
            session_type = "other"

        workouts.append({
            "date": date,
            "session_type": session_type,
            "duration_minutes": len(exercises) * 8,  # Estimate
            "exercises": exercises,
            "overall_fatigue": 6,
            "notes": ""
        })

    # Generate soreness data from DOMS notes
    soreness_data = []
    for row in data_rows:
        if "DOMS" in row["notes"]:
            muscle = MACHINE_TO_MUSCLE.get(row["machine"], "other")
            # Parse DOMS duration if mentioned
            match = re.search(r"(\d+)\s*days?", row["notes"])
            severity = 6 if match else 5

            soreness_data.append({
                "date": row["date"],
                "muscle": muscle,
                "soreness_level": severity,
                "notes": row["notes"]
            })

    return workouts, soreness_data


def get_workout_summary(workouts: list) -> dict:
    """Generate a summary of workout data."""
    if not workouts:
        return {}

    dates = [w["date"] for w in workouts]
    total_sessions = len(workouts)

    # Count sessions by type
    session_types = defaultdict(int)
    for w in workouts:
        session_types[w["session_type"]] += 1

    # Count total sets and volume
    total_sets = 0
    total_volume = 0
    exercises_count = defaultdict(int)

    for workout in workouts:
        for exercise in workout["exercises"]:
            exercises_count[exercise["machine"]] += 1
            for s in exercise["sets"]:
                total_sets += 1
                total_volume += s["weight_lbs"] * s["reps"]

    return {
        "date_range": f"{min(dates)} to {max(dates)}",
        "total_sessions": total_sessions,
        "total_sets": total_sets,
        "total_volume": round(total_volume),
        "session_types": dict(session_types),
        "most_frequent_exercises": dict(sorted(exercises_count.items(), key=lambda x: -x[1])[:10])
    }


if __name__ == "__main__":
    # Test the parser
    import json

    workouts, soreness = parse_workout_log("support/Workout_Log_LogTab_Past_Workouts.md")

    print(f"Parsed {len(workouts)} workouts")
    print(f"Date range: {workouts[0]['date']} to {workouts[-1]['date']}")
    print(f"\nSummary:")
    summary = get_workout_summary(workouts)
    print(json.dumps(summary, indent=2))

    print(f"\nSample workout ({workouts[-1]['date']}):")
    print(json.dumps(workouts[-1], indent=2))
