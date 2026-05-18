#!/usr/bin/env python3
"""
Workout Analysis — reads latest workout from fitness dashboard,
compares against history, outputs analysis text.
Called by cron after workout is logged.
"""
import json, os
from datetime import datetime, timedelta

WORKOUTS_FILE = os.path.expanduser("~/.openclaw/workspace/projects/fitness-dashboard/data_workouts.json")


def load_workouts():
    with open(WORKOUTS_FILE) as f:
        return json.load(f)


def _parse_iso_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None


def _workout_sort_key(w):
    return _parse_iso_date(w.get("ow_event_time") or w.get("date")) or datetime.min


def _coerce_float(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def exercise_volume(exercise):
    """Total volume = sum(weight * reps) across sets"""
    total = 0.0
    for s in exercise.get('sets', []) or []:
        if not isinstance(s, dict):
            continue
        total += _coerce_float(s.get('weight_lbs')) * _coerce_float(s.get('reps'))
    return total


def exercise_top_set(exercise):
    """Heaviest weight used"""
    weights = []
    for s in exercise.get('sets', []) or []:
        if not isinstance(s, dict):
            continue
        weights.append(_coerce_float(s.get('weight_lbs')))
    return max(weights, default=0)


def analyze_latest():
    workouts = load_workouts()
    if len(workouts) < 2:
        return "Not enough workout history for comparison yet."

    workouts_sorted = sorted(workouts, key=_workout_sort_key)
    latest = workouts_sorted[-1]
    history = workouts_sorted[:-1]

    date = latest.get('date', 'Unknown')
    session_type = latest.get('session_type', 'workout')
    duration = latest.get('duration_minutes', 0)
    fatigue = latest.get('overall_fatigue', 'N/A')
    notes = latest.get('notes', '')
    exercises = latest.get('exercises', [])

    # Build exercise history lookup: machine -> list of (date, volume, top_set)
    ex_history = {}
    for w in history:
        for ex in w.get('exercises', []) or []:
            name = ex.get('machine', '') or ex.get('exercise', '')
            if not name:
                continue
            if name not in ex_history:
                ex_history[name] = []
            sets = ex.get('sets', []) or []
            avg_rpe = 0.0
            if sets:
                avg_rpe = sum(_coerce_float(s.get('rpe')) for s in sets if isinstance(s, dict)) / max(len(sets), 1)
            ex_history[name].append({
                'date': w.get('date'),
                'volume': exercise_volume(ex),
                'top_set': exercise_top_set(ex),
                'sets': len(sets),
                'avg_rpe': avg_rpe,
            })

    # Analyze each exercise
    improving = []
    declining = []
    new_exercises = []
    maintained = []
    prs = []

    total_volume = 0
    total_sets = 0

    for ex in exercises:
        name = ex.get('machine', '') or ex.get('exercise', '')
        vol = exercise_volume(ex)
        top = exercise_top_set(ex)
        sets = len(ex.get('sets', []) or [])
        avg_rpe = 0.0
        if sets:
            avg_rpe = sum(_coerce_float(s.get('rpe')) for s in ex.get('sets', []) if isinstance(s, dict)) / max(sets, 1)
        total_volume += vol
        total_sets += sets

        if not name or name not in ex_history or len(ex_history[name]) == 0:
            if name:
                new_exercises.append(f"**{name}** — first time! {top}lbs x {sets} sets")
            continue

        prev_entries = ex_history[name]
        # Compare to most recent occurrence
        last = prev_entries[-1]
        vol_change = ((vol - last['volume']) / max(last['volume'], 1)) * 100

        # Check for all-time PR
        all_time_top = max(e['top_set'] for e in prev_entries)
        all_time_vol = max(e['volume'] for e in prev_entries)

        if top > all_time_top:
            prs.append(f"🏆 **{name}** — NEW PR: {top}lbs (prev best: {all_time_top}lbs)")
        if vol > all_time_vol:
            prs.append(f"📈 **{name}** — Volume PR: {vol:,}lbs total")

        if vol_change > 5:
            improving.append(f"⬆️ **{name}** +{vol_change:.0f}% volume ({last['volume']:,} → {vol:,}lbs)")
        elif vol_change < -10:
            declining.append(f"⬇️ **{name}** {vol_change:.0f}% volume ({last['volume']:,} → {vol:,}lbs)")
        else:
            maintained.append(name)

    # Session frequency
    dates = sorted(set(w.get('date', '') for w in workouts_sorted if w.get('date')))
    recent_dates = [d for d in dates if d >= (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')]
    freq = len(recent_dates)

    # Volume trend (last 4 sessions)
    recent_vols = []
    for w in workouts_sorted[-4:]:
        sv = sum(exercise_volume(ex) for ex in w.get('exercises', []) or [])
        recent_vols.append(sv)
    vol_trend = "📈 trending up" if len(recent_vols) >= 2 and recent_vols[-1] > recent_vols[0] else "📉 trending down" if len(recent_vols) >= 2 and recent_vols[-1] < recent_vols[0] else "➡️ steady"

    # Build output
    lines = []
    lines.append(f"**🏋️ Workout Analysis — {date}**")
    lines.append(f"*{str(session_type).title()} | {duration} min | Fatigue: {fatigue}/10*")
    lines.append(f"Total volume: **{int(round(total_volume)):,}lbs** across {total_sets} sets | {len(exercises)} exercises")
    lines.append(f"4-session volume trend: {vol_trend}")
    lines.append(f"Frequency: {freq} sessions in last 14 days")

    if notes:
        lines.append(f"\n📝 *{notes}*")

    if prs:
        lines.append(f"\n🏆 **Personal Records**")
        lines.extend(prs)

    if improving:
        lines.append(f"\n**Improving**")
        lines.extend(improving)

    if declining:
        lines.append(f"\n**Watch List**")
        lines.extend(declining)

    if maintained:
        lines.append(f"\n**Maintained:** {', '.join(maintained)}")

    if new_exercises:
        lines.append(f"\n**New Exercises**")
        lines.extend(new_exercises)

    # Quick coaching note
    if isinstance(fatigue, (int, float)):
        if fatigue >= 8:
            lines.append(f"\n⚠️ High fatigue ({fatigue}/10) — consider a deload or lighter session next time.")
        elif fatigue <= 3:
            lines.append(f"\n💪 Low fatigue ({fatigue}/10) — you had room to push harder. Bump weight next session.")

    return '\n'.join(lines)


if __name__ == '__main__':
    print(analyze_latest())
