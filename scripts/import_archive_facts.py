#!/usr/bin/env python3
"""Import archived Oura and Apple Health history into wearable facts (read-only on the archives).

    python scripts/import_archive_facts.py --oura oura_daily.sqlite3 --apple apple_archive.db \
        --db wearable_facts.sqlite3

Facts are written with source_system="archive" and the archive's own label in
original_label. The run never deletes anything, is safe to repeat, and skips an
archive fact when a live (non-archive) fact already covers the same
provider, metric and date (a workout: the same session, category and start within
5 minutes): Open Wearables wins. History reads archive rows next
to live ones, labelled as archive. An archive fact can fill a day that has no live
fact (readiness, sleep debt and trends read it, shown as via: archive); it is never
used by the Open Wearables recommendation guard.

Apple types with no matching fact metric are not imported: heart_rate rows of
type "avg" (only "resting" maps). Oura columns with no metric (sleep_score,
activity_score) are skipped too.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from open_wearables_hub import _workout_category, same_workout, workout_key  # noqa: E402
from wearable_fact_store import WearableDailyFact, fact_provider, list_workout_events, upsert_daily_facts  # noqa: E402

LABELS = {"oura": "Oura (archive)", "apple": "Apple Health (archive)"}
# oura_daily column -> (metric, unit); readiness_score is a 0-100 score.
OURA_DAILY = {
    "readiness_score": ("recovery_score", "score"),
    "hrv": ("heart_rate_variability", "ms"),
    "resting_hr": ("resting_heart_rate", "bpm"),
    "steps": ("steps", "count"),
    "active_calories": ("active_calories", "kcal"),
    "temperature_deviation": ("temperature_deviation", "c"),
}
OURA_SLEEP_DAILY = {
    "sleep_duration_min": ("sleep_duration", "min"),
    "sleep_deep_min": ("sleep_deep_duration", "min"),
    "sleep_rem_min": ("sleep_rem_duration", "min"),
    "sleep_light_min": ("sleep_light_duration", "min"),
    "sleep_awake_min": ("sleep_awake_duration", "min"),
}
OURA_SLEEP_TABLE = {
    "total_sleep_min": ("sleep_duration", "min"),
    "deep_sleep_min": ("sleep_deep_duration", "min"),
    "rem_sleep_min": ("sleep_rem_duration", "min"),
    "light_sleep_min": ("sleep_light_duration", "min"),
    "awake_time_min": ("sleep_awake_duration", "min"),
    "efficiency": ("sleep_efficiency", "%"),
    "avg_heart_rate": ("sleep_avg_heart_rate", "bpm"),
    "avg_hrv": ("sleep_hrv_rmssd", "ms"),  # Oura reports RMSSD
}
APPLE_SLEEP = {
    "duration_minutes": ("sleep_duration", "min"),
    "deep_minutes": ("sleep_deep_duration", "min"),
    "rem_minutes": ("sleep_rem_duration", "min"),
    "core_minutes": ("sleep_light_duration", "min"),
    "awake_minutes": ("sleep_awake_duration", "min"),
    "in_bed_minutes": ("sleep_time_in_bed", "min"),
}
APPLE_DAILY = {"hrv": ("heart_rate_variability", "ms"), "steps": ("steps", "count"), "active_energy": ("active_calories", "kcal")}
NAP_TYPES = {"nap", "rest", "late_nap"}


def _open_read_only(path: str) -> sqlite3.Connection:
    # immutable: the archive is a static snapshot, so SQLite must not create -wal/-shm files beside it
    conn = sqlite3.connect(f"file:{quote(os.path.abspath(path))}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _fact(provider, day, metric, value, unit, source_id, original_label, kind, observed_at=None, category=None, imported_at=None):
    return WearableDailyFact(
        date=day,
        provider_id=provider,
        source_label=LABELS[provider],
        metric=metric,
        value=value,
        unit=unit,
        confidence="low",
        freshness="stale",
        category=category,
        source_id=source_id,
        source_provider=provider,
        original_label=original_label,
        observed_at=observed_at,
        used_for_recommendation=False,  # keeps archive out of the OW guard; readers still use it for a day with no live fact (via: archive)
        source_system="archive",
        source_record_kind=kind,
        imported_at=imported_at,
    )


def oura_facts(path: str, imported_at: str) -> list[WearableDailyFact]:
    facts = []
    with closing(_open_read_only(path)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        sleep_days = set()
        if "oura_sleep" in tables:
            for row in conn.execute("SELECT * FROM oura_sleep WHERE type = 'long_sleep' ORDER BY day"):
                total = _number(row["total_sleep_min"])
                if not row["day"] or total is None or total <= 0:
                    continue
                sleep_days.add(row["day"])
                for column, (metric, unit) in OURA_SLEEP_TABLE.items():
                    value = _number(row[column])
                    if value is not None:
                        facts.append(_fact("oura", row["day"], metric, value, unit, "archive:oura_sleep",
                                           f"oura_sleep.{column}", "event", row["bedtime_end"], imported_at=imported_at))
                try:  # the measured bedtime: without it, bedtime is guessed from sleep minutes and comes out late
                    in_bed = (datetime.fromisoformat(row["bedtime_end"]) - datetime.fromisoformat(row["bedtime_start"])).total_seconds() / 60
                except (TypeError, ValueError):
                    in_bed = 0
                if in_bed > 0:
                    facts.append(_fact("oura", row["day"], "sleep_time_in_bed", in_bed, "min", "archive:oura_sleep",
                                       "oura_sleep.bedtime_start", "event", row["bedtime_end"], imported_at=imported_at))
        if "oura_daily" in tables:
            for row in conn.execute("SELECT * FROM oura_daily ORDER BY day"):
                day = row["day"]
                if not day:
                    continue
                mapping = dict(OURA_DAILY)
                nap = str(row["sleep_type"] or "").lower() in NAP_TYPES
                if day not in sleep_days and not nap:  # oura_sleep is the better record of a night
                    mapping.update(OURA_SLEEP_DAILY)
                for column, (metric, unit) in mapping.items():
                    value = _number(row[column])
                    if value is None or (metric == "sleep_duration" and value <= 0):
                        continue
                    if metric == "recovery_score" and not 0 <= value <= 100:
                        continue
                    facts.append(_fact("oura", day, metric, value, unit, "archive:oura_daily",
                                       f"oura_daily.{column}", "summary", imported_at=imported_at))
    return facts


def apple_facts(path: str, imported_at: str) -> tuple[list[WearableDailyFact], dict[str, int]]:
    facts = []
    unmapped: dict[str, int] = {}
    with closing(_open_read_only(path)) as conn:
        for row in conn.execute("SELECT record_type, record_date, record_key, data_json FROM ah_sync_log ORDER BY record_date, id"):
            kind, day = row["record_type"], row["record_date"]
            try:
                data = json.loads(row["data_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict) or not day:
                continue
            if kind == "sleep":
                for key, (metric, unit) in APPLE_SLEEP.items():
                    value = _number(data.get(key))
                    if value is not None and not (metric == "sleep_duration" and value <= 0):
                        facts.append(_fact("apple", day, metric, value, unit, "archive:ah_sleep",
                                           f"ah_sync_log.sleep.{key}", "summary", imported_at=imported_at))
            elif kind == "heart_rate":
                value = _number(data.get("value"))
                if str(data.get("type")) == "resting" and value is not None:
                    facts.append(_fact("apple", day, "resting_heart_rate", value, "bpm", "archive:ah_heart_rate",
                                       "ah_sync_log.heart_rate.resting", "summary", imported_at=imported_at))
                else:
                    unmapped["heart_rate:avg"] = unmapped.get("heart_rate:avg", 0) + 1
            elif kind in APPLE_DAILY:
                metric, unit = APPLE_DAILY[kind]
                value = _number(data.get("value"))
                if value is not None:
                    facts.append(_fact("apple", day, metric, value, unit, f"archive:ah_{kind}",
                                       f"ah_sync_log.{kind}", "summary", imported_at=imported_at))
            elif kind == "workouts":
                label = data.get("activity_type") or "Workout"
                start = data.get("startDate") or row["record_key"]
                source_id = f"archive:ah_workout:{start or label}"
                metrics = {
                    "workout_duration": (data.get("duration_minutes"), "min"),
                    "workout_active_calories": (data.get("total_energy_kcal"), "kcal"),
                    "workout_avg_heart_rate": (data.get("avg_heart_rate"), "bpm"),
                    "workout_distance": (data.get("distance_m"), "m"),
                }
                for metric, (raw, unit) in metrics.items():
                    value = _number(raw)
                    if value is not None and value > 0:
                        facts.append(_fact("apple", day, metric, value, unit, source_id, str(label), "event",
                                           str(start) if start else None, _workout_category(label), imported_at))
            else:
                unmapped[kind] = unmapped.get(kind, 0) + 1
    return facts, unmapped


def live_keys(db_path: str, profile_key: str) -> set[tuple[str, str, str]]:
    """(provider, metric, date) already covered by a non-archive fact."""
    if not os.path.exists(db_path):
        return set()
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            rows = conn.execute(
                "SELECT provider_id, source_provider, metric, date FROM wearable_daily_facts "
                "WHERE profile_key = ? AND source_system != 'archive'",
                (profile_key,),
            ).fetchall()
        except sqlite3.OperationalError:
            return set()
    return {(fact_provider(p, sp), metric, date) for p, sp, metric, date in rows}


def import_archives(oura: str | None, apple: str | None, db_path: str, profile_key: str = "1") -> dict:
    imported_at = datetime.now().astimezone().isoformat()
    facts: list[WearableDailyFact] = []
    unmapped: dict[str, int] = {}
    if oura:
        facts += oura_facts(oura, imported_at)
    if apple:
        apple_rows, unmapped = apple_facts(apple, imported_at)
        facts += apple_rows
    live = live_keys(db_path, profile_key)
    live_workouts = [  # a live session hides only its own archived copy: same provider, start within 5 minutes (labels differ by source)
        (e["provider"], workout_key(e))
        for e in (list_workout_events(db_path, profile_key) if live else [])
        if e["via"] != "archive"
    ]
    kept, skipped_live = [], 0
    for fact in facts:
        covered = (
            any(p == fact.provider_id and same_workout((fk[0], k[1]), fk) for p, k in live_workouts for fk in [workout_key(fact.__dict__)])
            if fact.metric.startswith("workout_")
            else (fact.provider_id, fact.metric, fact.date) in live
        )
        if covered:
            skipped_live += 1
        else:
            kept.append(fact)
    written = upsert_daily_facts(db_path, kept, profile_key=profile_key) if kept else 0
    return {"written": written, "skipped_live": skipped_live, "unmapped_types": unmapped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--oura", help="archived Oura SQLite file (opened read-only)")
    parser.add_argument("--apple", help="archived Apple Health sync SQLite file (opened read-only)")
    parser.add_argument("--db", required=True, help="wearable facts SQLite file to write")
    parser.add_argument("--profile-key", default="1")
    args = parser.parse_args(argv)
    if not (args.oura or args.apple):
        parser.error("give --oura and/or --apple")
    print(json.dumps(import_archives(args.oura, args.apple, args.db, args.profile_key), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
