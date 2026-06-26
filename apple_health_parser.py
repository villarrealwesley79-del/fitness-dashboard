"""
Apple Health JSON Export Parser for Fitness Dashboard

Parses HealthKit JSON exports from ~/Documents/Health/ (zero auth, zero infra).
Provides Flask API endpoints for the dashboard frontend.

Data sources supported:
- healthkit_timeseries_multi_*.json → steps, active energy, RHR, HRV, resp rate
- healthkit_samples_workout_*.json → workouts with duration, energy, distance
- healthkit_samples_sleepAnalysis_*.json → sleep analysis records
"""

import json
import os
import re
import sqlite3
import glob
from datetime import datetime, timezone
from collections import defaultdict
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from flask import jsonify, request
from runtime_config import clean_public_url, data_path, public_base_url

HEALTH_DIR = os.path.expanduser("~/Documents/Health")
PUBLIC_BASE_URL_ENV = "FITNESS_DASHBOARD_PUBLIC_BASE_URL"
APPLE_HEALTH_WEBHOOK_URL_ENV = "APPLE_HEALTH_WEBHOOK_URL"
APPLE_HEALTH_SYNC_DB_ENV = "APPLE_HEALTH_SYNC_DB"

ACTIVITY_MAP = {
    1: "Walking", 2: "Running", 3: "Cycling", 4: "Hiking",
    6: "Traditional Strength Training", 7: "Cross Country Skiing",
    10: "Swimming", 11: "Yoga", 13: "Elliptical",
    14: "Stair Climbing", 16: "Dance", 20: "Core Training",
    22: "Functional Strength Training", 23: "Cross Training",
    25: "Other", 28: "High Intensity Interval Training",
    30: "Rowing", 37: "Basketball", 39: "Jump Rope",
    40: "Kickboxing", 52: "Table Tennis", 54: "Tai Chi",
    55: "Volleyball", 58: "Wrestling", 64: "Stairs",
}


def _ms_to_iso(ms: float) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return ""


def _ms_to_datetime(ms: float) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def _local_date_from_iso(value) -> str:
    """Return the calendar date encoded by an ISO timestamp's own offset.

    Health Auto Export includes timezone offsets in many timestamps. For daily
    HealthKit records the intended bucket is the local date in that timestamp,
    not the server's UTC date. Date-only strings pass through unchanged.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    try:
        normalized = text
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        return datetime.fromisoformat(normalized).date().isoformat()
    except (TypeError, ValueError):
        return text[:10]


def _apple_health_sync_db_path() -> str:
    return (
        os.environ.get(APPLE_HEALTH_SYNC_DB_ENV)
        or data_path("apple_health_sync.db")
    )


def _load_json(pattern: str) -> dict:
    files = sorted(glob.glob(os.path.join(HEALTH_DIR, pattern)), reverse=True)
    if not files:
        return {}
    try:
        with open(files[0], "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _clean_public_url(value: str) -> str:
    return clean_public_url(value)


def _append_sync_token(url: str, token: str) -> str:
    if not token:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(key == "token" for key, _value in query):
        return url
    query.append(("token", token))
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query),
        parts.fragment,
    ))


def _public_base_url_from_request() -> str:
    return public_base_url(request)


def _apple_health_sync_endpoint() -> str:
    configured = _clean_public_url(os.environ.get(APPLE_HEALTH_WEBHOOK_URL_ENV, ""))
    if configured:
        return configured
    return f"{_public_base_url_from_request()}/api/apple-health/sync"


def parse_workouts() -> list[dict]:
    data = _load_json("healthkit_samples_workout_*.json")
    records = data.get("workout", [])
    parsed = []
    for w in records:
        raw_activity = w.get("activity", "")
        match = re.search(r"rawValue:\s*(\d+)", raw_activity)
        activity_num = int(match.group(1)) if match else 0
        activity_name = ACTIVITY_MAP.get(activity_num,
            raw_activity.split("(")[0].strip() if raw_activity else "Other")
        energy = w.get("total_energy_kcal")
        distance = w.get("total_distance_m")
        parsed.append({
            "date": _ms_to_iso(w.get("start_time_ms", 0)),
            "start": _ms_to_datetime(w.get("start_time_ms", 0)),
            "end": _ms_to_datetime(w.get("end_time_ms", 0)),
            "activity": activity_name,
            "duration_min": round((w.get("duration_sec", 0) or 0) / 60, 1),
            "energy_kcal": round(energy, 1) if energy else 0,
            "distance_m": round(distance, 1) if distance else 0,
            "source": w.get("source_name", ""),
        })
    return parsed


def parse_sleep() -> list[dict]:
    data = _load_json("healthkit_samples_sleepAnalysis_*.json")
    records = data.get("sleepAnalysis", [])
    daily_sleep: dict[str, float] = defaultdict(float)
    for r in records:
        start = r.get("start_time_ms", 0)
        end = r.get("end_time_ms", 0)
        if start and end:
            date_str = _ms_to_iso(start)
            if date_str:
                daily_sleep[date_str] += (end - start) / (1000 * 3600)
    return [{"date": d, "hours": round(h, 2)} for d, h in sorted(daily_sleep.items())]


def parse_timeseries(metric_key: str, file_pattern: str) -> list[dict]:
    data = _load_json(file_pattern)
    records = data.get(metric_key, [])
    daily: dict[str, list[float]] = defaultdict(list)
    for r in records:
        date_str = _ms_to_iso(r.get("time_ms", 0))
        val = r.get("value")
        if date_str and val is not None:
            daily[date_str].append(float(val))
    return [
        {"date": d, "avg": round(sum(v) / len(v), 2),
         "min": round(min(v), 2), "max": round(max(v), 2), "samples": len(v)}
        for d, v in sorted(daily.items())
    ]


def parse_steps() -> list[dict]:
    return parse_timeseries("stepCount", "healthkit_timeseries_multi_20260111T221509Z.json")


def parse_active_energy() -> list[dict]:
    return parse_timeseries("activeEnergyBurned", "healthkit_timeseries_multi_20260111T221509Z.json")


def parse_rhr() -> list[dict]:
    return parse_timeseries("restingHeartRate", "healthkit_timeseries_multi_20260111T221514Z.json")


def parse_hrv() -> list[dict]:
    return parse_timeseries("heartRateVariabilitySDNN", "healthkit_timeseries_multi_20260111T221514Z.json")


def _workout_activity_name(workout: dict) -> str:
    if not isinstance(workout, dict):
        return "Other"
    activity = (
        workout.get("activity")
        or workout.get("activity_type")
        or workout.get("type")
        or workout.get("workoutActivityType")
        or workout.get("name")
        or "Other"
    )
    try:
        return ACTIVITY_MAP.get(int(activity), str(activity).strip())
    except (TypeError, ValueError):
        return str(activity).strip()


def _ignore_workout(workout: dict) -> bool:
    return _workout_activity_name(workout).strip().lower() == "other"


def get_summary() -> dict:
    workouts = [w for w in parse_workouts() if not _ignore_workout(w)]
    sleep = parse_sleep()
    steps = parse_steps()
    now = datetime.now(timezone.utc)
    cutoff7 = (now - __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d")
    cutoff30 = (now - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d")
    recent_sleep_7d = [s for s in sleep if s["date"] >= cutoff7]
    recent_steps_7d = [s for s in steps if s["date"] >= cutoff7]
    recent_workouts_7d = [w for w in workouts if w["date"] >= cutoff7]
    recent_workouts_30d = [w for w in workouts if w["date"] >= cutoff30]
    rhr = parse_rhr()
    hrv = parse_hrv()
    return {
        "workouts_total": len(workouts),
        "workouts_7d": len(recent_workouts_7d),
        "workouts_30d": len(recent_workouts_30d),
        "avg_sleep_7d": round(sum(s["hours"] for s in recent_sleep_7d) / max(len(recent_sleep_7d), 1), 2),
        "avg_steps_7d": round(sum(s["avg"] for s in recent_steps_7d) / max(len(recent_steps_7d), 1), 0),
        "latest_rhr": rhr[-1]["avg"] if rhr else None,
        "latest_hrv": hrv[-1]["avg"] if hrv else None,
        "sleep_total_days": len(sleep),
        "steps_total_days": len(steps),
        "data_source": "Apple Health Kit JSON Export",
        "last_export": os.path.basename(
            sorted(glob.glob(os.path.join(HEALTH_DIR, "healthkit_*.json")), reverse=True)[0]
        ) if glob.glob(os.path.join(HEALTH_DIR, "healthkit_*.json")) else "not found",
    }


def health_data_available() -> bool:
    """Check if any Apple Health data is available (file-based or synced)."""
    if glob.glob(os.path.join(HEALTH_DIR, "healthkit_*.json")):
        return True
    # Also check sync DB
    try:
        db_path = _apple_health_sync_db_path()
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM ah_sync_log").fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


def _get_sync_records(record_type: str, days: int = 0) -> list[dict]:
    """Read records from the sync database for a given type."""
    db_path = _apple_health_sync_db_path()
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        if days > 0:
            cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT data_json FROM ah_sync_log WHERE record_type = ? AND record_date >= ? ORDER BY record_date",
                (record_type, cutoff)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT data_json FROM ah_sync_log WHERE record_type = ? ORDER BY record_date",
                (record_type,)
            ).fetchall()
        conn.close()
        records = []
        for (dj,) in rows:
            try:
                records.append(json.loads(dj))
            except (json.JSONDecodeError, TypeError):
                pass
        return records
    except Exception:
        return []


def _normalize_sync_workout(workout: dict) -> dict:
    normalized = dict(workout)
    activity = _workout_activity_name(normalized)
    avg_hr = normalized.get("avg_heart_rate")
    if avg_hr is None:
        avg_hr = normalized.get("avgHeartRate")
    if isinstance(avg_hr, dict):
        avg_hr = (
            avg_hr.get("avg")
            or avg_hr.get("average")
            or avg_hr.get("qty")
            or avg_hr.get("value")
        )
    normalized["activity"] = activity
    normalized.setdefault("activity_type", activity)
    normalized.setdefault("date", normalized.get("startDate", "")[:10])
    normalized.setdefault("duration_min", normalized.get("duration_minutes", 0))
    normalized.setdefault("energy_kcal", normalized.get("total_energy_kcal", 0))
    normalized.setdefault("distance_m", normalized.get("distance_m", 0))
    normalized["avg_heart_rate"] = avg_hr
    return normalized


def _merge_workouts(file_workouts: list, sync_workouts: list) -> list:
    """Merge file-based and sync-based workouts, deduplicating by date+activity."""
    seen = set()
    merged = []
    for w in file_workouts + sync_workouts:
        if _ignore_workout(w):
            continue
        key = (w.get("date", ""), w.get("activity", ""))
        if key not in seen:
            seen.add(key)
            merged.append(w)
    return sorted(merged, key=lambda x: x.get("date", ""), reverse=True)


def _merge_sleep(file_sleep: list, sync_sleep: list) -> list:
    """Merge file-based and sync-based sleep records, deduplicating by date."""
    seen = set()
    merged = []
    for s in file_sleep + sync_sleep:
        key = s.get("date", "")
        if key not in seen:
            seen.add(key)
            merged.append(s)
    return sorted(merged, key=lambda x: x.get("date", ""), reverse=True)


def _merge_timeseries(file_data: list, sync_data: list) -> list:
    """Merge file-based and sync-based timeseries records, deduplicating by date."""
    seen = set()
    merged = []
    for r in file_data + sync_data:
        key = r.get("date", "")
        if key not in seen:
            seen.add(key)
            merged.append(r)
    return sorted(merged, key=lambda x: x.get("date", ""), reverse=True)


def register_apple_health_routes(flask_app):
    """Register Apple Health API routes on the Flask app."""

    def _sleep_hours(s):
        """Canonicalize a sync sleep row to hours.
        Priority: explicit `hours` → `duration_minutes/60` → phase sum
        (deep+rem+core, awake excluded) → `duration_hours` for legacy rows."""
        h = s.get("hours")
        if isinstance(h, (int, float)) and h > 0:
            return float(h)
        m = s.get("duration_minutes")
        if isinstance(m, (int, float)) and m > 0:
            return float(m) / 60.0
        phase_sum = 0.0
        for key in ("deep_minutes", "rem_minutes", "core_minutes"):
            v = s.get(key)
            if isinstance(v, (int, float)):
                phase_sum += float(v)
        if phase_sum > 0:
            return phase_sum / 60.0
        return float(s.get("duration_hours") or 0)

    @flask_app.route("/api/apple-health/summary")
    def apple_health_summary():
        if not health_data_available():
            return jsonify({"error": "No Apple Health data found. Export from iPhone Health app or use Health Auto Export to sync.", "data_source": "unavailable"}), 404
        summary = get_summary()
        # Enrich with sync DB data
        sync_workouts = [
            w
            for w in (_normalize_sync_workout(sw) for sw in _get_sync_records("workouts", 30))
            if not _ignore_workout(w)
        ]
        sync_sleep = _get_sync_records("sleep", 30)
        sync_steps = _get_sync_records("steps", 30)
        sync_hr = _get_sync_records("heart_rate", 30)
        if sync_workouts or sync_sleep or sync_steps:
            summary["workouts_total"] = summary.get("workouts_total", 0) + len(sync_workouts)
            summary["workouts_7d"] = summary.get("workouts_7d", 0) + len([w for w in sync_workouts if w.get("date", "") >= (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d")])
            summary["workouts_30d"] = summary.get("workouts_30d", 0) + len(sync_workouts)
            if sync_sleep:
                # Use the shared helper so /summary and /sleep stay in sync.
                vals = [_sleep_hours(s) for s in sync_sleep]
                if any(v > 0 for v in vals):
                    avg_s = sum(vals) / max(len(vals), 1)
                    summary["avg_sleep_7d"] = round(avg_s, 2)
                summary["sleep_total_days"] = summary.get("sleep_total_days", 0) + len(sync_sleep)
            if sync_steps:
                avg_st = sum(s.get("avg", s.get("steps", 0)) for s in sync_steps) / max(len(sync_steps), 1)
                summary["avg_steps_7d"] = round(avg_st, 0)
                summary["steps_total_days"] = summary.get("steps_total_days", 0) + len(sync_steps)
            if sync_hr:
                summary["latest_rhr"] = sync_hr[-1].get("avg", sync_hr[-1].get("value"))
            summary["data_source"] = "Health Auto Export + File Export"
        return jsonify(summary)

    @flask_app.route("/api/apple-health/workouts")
    def apple_health_workouts():
        if not health_data_available():
            return jsonify({"workouts": [], "total": 0, "data_source": "unavailable"})
        days = request.args.get("days", 30, type=int)
        file_workouts = parse_workouts()
        sync_workouts = _get_sync_records("workouts", days)
        sync_workouts = [_normalize_sync_workout(sw) for sw in sync_workouts]
        workouts = _merge_workouts(file_workouts, sync_workouts)
        if days > 0:
            cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
            workouts = [w for w in workouts if w.get("date", "") >= cutoff]
        return jsonify({"workouts": workouts, "total": len(workouts)})

    @flask_app.route("/api/apple-health/sleep")
    def apple_health_sleep():
        if not health_data_available():
            return jsonify({"error": "No Apple Health data found"}), 404
        days = request.args.get("days", 30, type=int)
        file_sleep = parse_sleep()
        sync_sleep = _get_sync_records("sleep", days)
        for ss in sync_sleep:
            ss.setdefault("date", ss.get("startDate", "")[:10])
            if ss.get("hours") is None:
                ss["hours"] = round(_sleep_hours(ss), 2)
        sleep = _merge_sleep(file_sleep, sync_sleep)
        if days > 0:
            cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
            sleep = [s for s in sleep if s.get("date", "") >= cutoff]
        return jsonify({"sleep": sleep, "total": len(sleep)})

    @flask_app.route("/api/apple-health/steps")
    def apple_health_steps():
        if not health_data_available():
            return jsonify({"error": "No Apple Health data found"}), 404
        days = request.args.get("days", 30, type=int)
        file_steps = parse_steps()
        sync_steps = _get_sync_records("steps", days)
        for ss in sync_steps:
            ss.setdefault("date", ss.get("date", ""))
            ss.setdefault("avg", ss.get("value", ss.get("steps", 0)))
        steps = _merge_timeseries(file_steps, sync_steps)
        if days > 0:
            cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
            steps = [s for s in steps if s.get("date", "") >= cutoff]
        return jsonify({"steps": steps, "total": len(steps)})

    @flask_app.route("/api/apple-health/vitals")
    def apple_health_vitals():
        if not health_data_available():
            return jsonify({"error": "No Apple Health data found"}), 404
        days = request.args.get("days", 30, type=int)
        file_rhr = parse_rhr()
        file_hrv = parse_hrv()
        sync_hr = _get_sync_records("heart_rate", days)
        sync_hrv = _get_sync_records("hrv", days)
        # Normalize sync heart rate records
        sync_rhr = []
        for r in sync_hr:
            r.setdefault("date", _local_date_from_iso(r.get("startDate")))
            r.setdefault("avg", r.get("value", r.get("avg", 0)))
            sync_rhr.append({"date": r["date"], "avg": r["avg"]})
        sync_hrv_norm = []
        for h in sync_hrv:
            h.setdefault("date", _local_date_from_iso(h.get("startDate")))
            h.setdefault("avg", h.get("value", h.get("avg", 0)))
            sync_hrv_norm.append({"date": h["date"], "avg": h["avg"]})
        rhr = _merge_timeseries(file_rhr, sync_rhr)
        hrv = _merge_timeseries(file_hrv, sync_hrv_norm)
        if days > 0:
            cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
            rhr = [r for r in rhr if r.get("date", "") >= cutoff]
            hrv = [h for h in hrv if h.get("date", "") >= cutoff]
        return jsonify({"rhr": rhr, "hrv": hrv})

    # ── Sync endpoint for Health Auto Export app (interim auto-sync) ──
    def _init_ah_sync_db():
        conn = sqlite3.connect(_apple_health_sync_db_path())
        conn.execute("""CREATE TABLE IF NOT EXISTS ah_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            record_type TEXT NOT NULL,
            record_date TEXT NOT NULL,
            record_key TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source, record_type, record_date, record_key)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS ah_sync_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            total_count INTEGER NOT NULL DEFAULT 0,
            remote_addr TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        # Migrate legacy DBs: add record_key column + rebuild the table so the
        # old UNIQUE(source, record_type, record_date) constraint gets replaced
        # with the widened UNIQUE(... record_key) version. SQLite can't ALTER a
        # UNIQUE constraint — requires a table rebuild.
        cur = conn.execute("PRAGMA table_info(ah_sync_log)")
        cols = [row[1] for row in cur.fetchall()]
        if "record_key" not in cols:
            conn.execute("ALTER TABLE ah_sync_log ADD COLUMN record_key TEXT NOT NULL DEFAULT ''")
        # Detect whether the old 3-field UNIQUE is still in place. We look at
        # the index metadata; if there's an auto-index on just (source,
        # record_type, record_date) we rebuild.
        needs_rebuild = False
        try:
            idx_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ah_sync_log'").fetchall()
            for (idx_name,) in idx_rows:
                info = conn.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
                col_names = [r[2] for r in info]
                if col_names == ["source", "record_type", "record_date"]:
                    needs_rebuild = True
                    break
        except Exception:
            needs_rebuild = False

        if needs_rebuild:
            conn.execute("BEGIN")
            try:
                conn.execute("""CREATE TABLE ah_sync_log_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    record_date TEXT NOT NULL,
                    record_key TEXT NOT NULL DEFAULT '',
                    data_json TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(source, record_type, record_date, record_key)
                )""")
                conn.execute("""INSERT INTO ah_sync_log_new
                    (id, source, record_type, record_date, record_key, data_json, created_at)
                    SELECT id, source, record_type, record_date, COALESCE(record_key, ''), data_json, created_at
                    FROM ah_sync_log""")
                conn.execute("DROP TABLE ah_sync_log")
                conn.execute("ALTER TABLE ah_sync_log_new RENAME TO ah_sync_log")
                conn.execute("COMMIT")
                print("INFO: rebuilt ah_sync_log with widened UNIQUE(source, record_type, record_date, record_key)")
            except Exception as exc:
                conn.execute("ROLLBACK")
                print(f"WARN: ah_sync_log rebuild failed: {exc}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ah_sync_date ON ah_sync_log(record_date)")
        conn.commit()
        conn.close()

    def _record_key(record_type, rec):
        """Secondary key that lets us store multiple records per day for granular
        types (workouts). For daily-aggregate types (steps/RHR/HRV/sleep/active_energy)
        we keep the empty string so resends of the same day still dedupe."""
        if record_type == "workouts":
            # Prefer a stable start timestamp; fall back to (activity_type, duration)
            # so two identical workouts on the same minute don't duplicate either.
            start = rec.get("startDate") or rec.get("start") or rec.get("start_time")
            if start:
                return str(start)
            activity = rec.get("activity_type") or rec.get("type") or rec.get("workoutActivityType", "")
            duration = rec.get("duration_minutes") or rec.get("duration") or rec.get("totalEnergyBurned", "")
            return f"{activity}:{duration}"
        return ""

    def _merge_existing_sleep_record(conn, rec_date, rec_key, incoming):
        """Preserve richer already-synced sleep data when a partial resend arrives."""
        row = conn.execute(
            """SELECT data_json FROM ah_sync_log
               WHERE source = ? AND record_type = ? AND record_date = ? AND record_key = ?""",
            ("health_auto_export", "sleep", rec_date, rec_key),
        ).fetchone()
        if not row:
            return incoming
        try:
            existing = json.loads(row[0]) or {}
        except (TypeError, json.JSONDecodeError):
            return incoming
        merged = dict(existing)
        for key, value in incoming.items():
            if value is not None or key not in merged:
                merged[key] = value
        if existing.get("duration_minutes") is not None and (
            merged.get("duration_minutes") is None
            or (
                existing.get("duration_minutes_source") == "hae_asleep"
                and merged.get("duration_minutes_source") == "computed_phase_sum"
            )
        ):
            merged["duration_minutes"] = existing.get("duration_minutes")
            merged["duration_minutes_source"] = existing.get("duration_minutes_source")
        for key in ("deep_minutes", "rem_minutes", "core_minutes", "awake_minutes", "in_bed_minutes"):
            if merged.get(key) is None and existing.get(key) is not None:
                merged[key] = existing.get(key)
        return merged

    _init_ah_sync_db()

    # Auto-provision HEALTH_SYNC_TOKEN so the sync endpoint works out of the box.
    # Persisted to .health-sync-token in the project dir, 0600. Env var wins.
    _tok_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".health-sync-token")
    if not os.environ.get("HEALTH_SYNC_TOKEN", "").strip():
        _existing = ""
        try:
            if os.path.exists(_tok_file):
                with open(_tok_file) as _fh:
                    _existing = _fh.read().strip()
        except Exception:
            _existing = ""
        if not _existing:
            import secrets as _sec
            _existing = _sec.token_urlsafe(32)
            try:
                with open(_tok_file, "w") as _fh:
                    _fh.write(_existing)
                os.chmod(_tok_file, 0o600)
                print("INFO: generated HEALTH_SYNC_TOKEN; retrieve setup URL from the owner-only Apple Health setup flow")
            except Exception as _exc:
                print(f"WARN: could not persist health-sync-token: {_exc}")
        os.environ["HEALTH_SYNC_TOKEN"] = _existing

    # Health Auto Export metric-name → our internal record_type
    _HAE_METRIC_MAP = {
        "step_count": "steps",
        "steps": "steps",
        "active_energy": "active_energy",
        "active_energy_burned": "active_energy",
        "resting_heart_rate": "heart_rate",
        "heart_rate": "heart_rate",
        "heart_rate_variability": "hrv",
        "sleep_analysis": "sleep",
        "sleep": "sleep",
    }

    def _normalize_hae_payload(payload):
        """Accept either our flat schema or Health Auto Export's native shape
        and return our flat schema. Idempotent on already-flat payloads."""
        if not isinstance(payload, dict):
            return {}

        # Already in our internal shape (top-level workouts/steps/heart_rate/sleep).
        if any(k in payload for k in ("workouts", "steps", "heart_rate", "sleep", "active_energy")):
            return payload

        # HAE wraps everything in {"data": {...}}.
        data_block = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        out = {"workouts": [], "steps": [], "heart_rate": [], "hrv": [], "sleep": [], "active_energy": []}

        # Workouts come as data.workouts: [{name, start, end, duration (SECONDS), totalEnergy{qty,units}, avgHeartRate, ...}]
        # HAE v2 sends duration in seconds. Convert to minutes here.
        for w in (data_block.get("workouts") or []):
            if not isinstance(w, dict):
                continue
            start = w.get("start") or w.get("startDate") or w.get("start_time") or ""
            date_part = _local_date_from_iso(start)
            duration_raw = w.get("duration") or w.get("duration_minutes") or 0
            try:
                duration_raw = float(duration_raw)
            except (TypeError, ValueError):
                duration_raw = 0
            # HAE v2: seconds. Heuristic to be safe across versions — if the value
            # looks like it could plausibly be minutes already (<= 240 and integer),
            # keep it; otherwise divide by 60. This covers both v2 (seconds) and
            # manual/legacy inserts.
            if duration_raw and duration_raw > 240:
                duration = round(duration_raw / 60, 1)
            else:
                duration = duration_raw
            energy = w.get("totalEnergy") or {}
            if isinstance(energy, dict):
                energy_val = energy.get("qty") or energy.get("value")
            else:
                energy_val = energy
            avg_hr = w.get("avgHeartRate") or w.get("avg_heart_rate")
            if isinstance(avg_hr, dict):
                avg_hr = avg_hr.get("avg") or avg_hr.get("average") or avg_hr.get("qty") or avg_hr.get("value")
            # Prefer activeEnergyBurned if totalEnergy is missing (HAE v2 exposes both for some types)
            active_e = w.get("activeEnergyBurned")
            if isinstance(active_e, dict):
                active_e = active_e.get("qty") or active_e.get("value")
            if energy_val is None:
                energy_val = active_e
            # Distance — HAE v2 reports in meters inside a {qty, units} shape for outdoor walks/runs
            dist = w.get("distance")
            if isinstance(dist, dict):
                dist = dist.get("qty") or dist.get("value")
            out["workouts"].append({
                "date": date_part,
                "startDate": start,
                "activity_type": w.get("name") or w.get("workoutActivityType") or w.get("activity_type"),
                "duration_minutes": duration,
                "total_energy_kcal": energy_val,
                "avg_heart_rate": avg_hr,
                "distance_m": dist,
            })

        # Metrics come as data.metrics: [{name, units, data: [{date, qty}|{date, avg/min/max}|{date, asleep/inBed}]}]
        for metric in (data_block.get("metrics") or []):
            if not isinstance(metric, dict):
                continue
            internal = _HAE_METRIC_MAP.get((metric.get("name") or "").lower())
            if not internal:
                continue
            for pt in (metric.get("data") or []):
                if not isinstance(pt, dict):
                    continue
                rec_date = _local_date_from_iso(pt.get("date"))
                if not rec_date:
                    continue
                if internal == "heart_rate":
                    val = pt.get("avg") or pt.get("qty") or pt.get("value")
                    is_resting = (metric.get("name") or "").lower().startswith("resting")
                    out["heart_rate"].append({
                        "date": rec_date,
                        "value": val,
                        "type": "resting" if is_resting else "avg",
                    })
                elif internal == "sleep":
                    # HAE sleep_analysis aggregates: {asleep, inBed, core, deep, rem, awake, source}
                    # HAE v2 reports each phase in HOURS. Convert all to minutes.
                    def _hrs_to_min(val):
                        if val is None:
                            return None
                        try:
                            val = float(val)
                        except (TypeError, ValueError):
                            return None
                        # Heuristic: <= 24 almost certainly means hours (nobody
                        # sleeps > 24 hr/phase). Anything larger is already minutes.
                        return round(val * 60, 1) if 0 < val <= 24 else round(val, 1)

                    deep_minutes = _hrs_to_min(pt.get("deep"))
                    rem_minutes = _hrs_to_min(pt.get("rem"))
                    core_minutes = _hrs_to_min(pt.get("core"))
                    asleep_minutes = _hrs_to_min(pt.get("asleep"))
                    duration_source = "hae_asleep" if asleep_minutes is not None else None
                    if asleep_minutes is None:
                        phase_minutes = [
                            value for value in (deep_minutes, rem_minutes, core_minutes)
                            if value is not None
                        ]
                        phase_sum = round(sum(phase_minutes), 1) if phase_minutes else None
                        if phase_sum and phase_sum > 0:
                            asleep_minutes = phase_sum
                            duration_source = "computed_phase_sum"

                    out["sleep"].append({
                        "date": rec_date,
                        "duration_minutes": asleep_minutes,
                        "duration_minutes_source": duration_source,
                        "deep_minutes": deep_minutes,
                        "rem_minutes": rem_minutes,
                        "core_minutes": core_minutes,
                        "awake_minutes": _hrs_to_min(pt.get("awake")),
                        "in_bed_minutes": _hrs_to_min(pt.get("inBed")),
                    })
                else:
                    # hrv / steps / active_energy
                    val = pt.get("avg") or pt.get("qty") or pt.get("value") or pt.get("sum")
                    out[internal].append({"date": rec_date, "value": val})

        # Drop empty arrays so downstream logic is simpler
        return {k: v for k, v in out.items() if v}

    @flask_app.route("/api/apple-health/sync", methods=["POST"])
    def apple_health_sync():
        """Receive auto-exported HealthKit data from Health Auto Export app or iOS Shortcut.

        Accepts JSON with workouts, heart_rate, sleep, steps arrays.
        Deduplicates by (source, record_type, record_date, record_key).
        Returns sync_token for incremental sync.

        Auth: requires a shared secret in X-Sync-Token header or ?token= query,
        compared against HEALTH_SYNC_TOKEN env var. If the env var is unset the
        endpoint rejects everything — you must configure the secret. No bypass.
        """
        import hmac as _hmac
        expected = os.environ.get("HEALTH_SYNC_TOKEN", "").strip()
        if not expected:
            return jsonify({"error": "HEALTH_SYNC_TOKEN not configured on server"}), 503
        supplied = (request.headers.get("X-Sync-Token")
                    or request.args.get("token")
                    or "").strip()
        if not supplied or not _hmac.compare_digest(supplied, expected):
            return jsonify({"error": "invalid or missing sync token"}), 401

        data = request.get_json(force=True, silent=True) or {}
        if not data:
            return jsonify({"error": "No JSON body provided"}), 400

        # Normalize Health Auto Export's native format into the internal schema.
        # HAE sends either:
        #   {"data": {"workouts": [...], "metrics": [{"name": "step_count", "data": [...]}]}}
        # or the older flat version:
        #   {"workouts": [...], "steps": [...], ...}
        # We accept either transparently so the user doesn't have to customize
        # the HAE export template.
        data = _normalize_hae_payload(data)

        _init_ah_sync_db()
        conn = sqlite3.connect(_apple_health_sync_db_path())
        try:
            inserted = 0
            skipped = 0

            for record_type in ("workouts", "heart_rate", "hrv", "sleep", "steps", "active_energy"):
                records = data.get(record_type)
                if not records or not isinstance(records, list):
                    continue
                for rec in records:
                    # Each record must have a 'date' field
                    rec_date = _local_date_from_iso(rec.get("date") or rec.get("startDate"))
                    if not rec_date:
                        skipped += 1
                        continue
                    try:
                        stored_rec = dict(rec)
                        stored_rec["date"] = rec_date
                        rec_key = _record_key(record_type, stored_rec)
                        if record_type == "sleep":
                            stored_rec = _merge_existing_sleep_record(conn, rec_date, rec_key, stored_rec)
                        cur = conn.execute(
                            """
                            INSERT INTO ah_sync_log
                                (source, record_type, record_date, record_key, data_json)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(source, record_type, record_date, record_key)
                            DO UPDATE SET
                                data_json = excluded.data_json,
                                created_at = datetime('now')
                            WHERE ah_sync_log.data_json IS NOT excluded.data_json
                            """,
                            ("health_auto_export", record_type, rec_date, rec_key, json.dumps(stored_rec, default=str))
                        )
                        # cursor.rowcount is 1 on insert/update, 0 when an exact
                        # duplicate resend made no data change.
                        if cur.rowcount and cur.rowcount > 0:
                            inserted += 1
                        else:
                            skipped += 1
                    except Exception:
                        skipped += 1

            conn.execute(
                """INSERT INTO ah_sync_events
                   (source, inserted_count, skipped_count, total_count, remote_addr)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    "health_auto_export",
                    inserted,
                    skipped,
                    inserted + skipped,
                    (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip(),
                ),
            )
            conn.commit()

            # Return sync token (ISO timestamp) for incremental sync
            now_iso = datetime.now(timezone.utc).isoformat()
        finally:
            conn.close()

        return jsonify({
            "status": "ok",
            "inserted": inserted,
            "skipped": skipped,
            "sync_token": now_iso,
        })

    @flask_app.route("/api/apple-health/sync/status")
    def apple_health_sync_status():
        """Show sync status: last sync time, record counts."""
        token_configured = bool(os.environ.get("HEALTH_SYNC_TOKEN", "").strip())
        public_url_configured = bool(
            os.environ.get(PUBLIC_BASE_URL_ENV, "").strip()
            or os.environ.get(APPLE_HEALTH_WEBHOOK_URL_ENV, "").strip()
        )
        _init_ah_sync_db()
        conn = sqlite3.connect(_apple_health_sync_db_path())
        try:
            total = conn.execute("SELECT COUNT(*) FROM ah_sync_log").fetchone()[0]
            by_type = dict(conn.execute(
                "SELECT record_type, COUNT(*) FROM ah_sync_log GROUP BY record_type"
            ).fetchall())
            last_sync = conn.execute(
                "SELECT MAX(created_at) FROM ah_sync_log"
            ).fetchone()[0]
            last_attempt = conn.execute(
                "SELECT MAX(created_at) FROM ah_sync_events"
            ).fetchone()[0]
            last_event = conn.execute(
                """SELECT inserted_count, skipped_count, total_count, created_at
                   FROM ah_sync_events ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        finally:
            conn.close()
        return jsonify({
            "total_records": total,
            "by_type": by_type,
            "last_sync": last_sync,
            "last_attempt": last_attempt or last_sync,
            "last_event": dict(zip(
                ("inserted", "skipped", "total", "created_at"),
                last_event,
            )) if last_event else None,
            "source": "health_auto_export",
            "setup_configured": token_configured,
            "has_token": token_configured,
            "public_url_configured": public_url_configured,
        })

    @flask_app.route("/api/apple-health/sync/setup-url")
    def apple_health_sync_setup_url():
        """Return the tokenized Health Auto Export webhook URL for setup UI only."""
        token = os.environ.get("HEALTH_SYNC_TOKEN", "").strip()
        base_url = _apple_health_sync_endpoint()
        return jsonify({
            "webhook_url": _append_sync_token(base_url, token),
            "has_token": bool(token),
        })

    @flask_app.route("/api/apple-health/status")
    def apple_health_status():
        return jsonify({
            "available": health_data_available(),
            "health_dir": HEALTH_DIR,
            "export_files": len(glob.glob(os.path.join(HEALTH_DIR, "healthkit_*.json"))),
        })
