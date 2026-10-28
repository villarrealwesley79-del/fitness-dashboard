"""Oura integration utilities.

- Uses OURA_API_TOKEN env var
- Fetches daily_readiness + daily_sleep + daily_activity (v2 usercollection)
- Stores a simplified daily snapshot into SQLite for historical tracking

This module is intentionally lightweight (urllib + sqlite3) to avoid extra deps.

Design goals:
- Be resilient to missing/null fields from Oura
- Support schema evolution (ALTER TABLE to add newly tracked metrics)
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime


# -------------------- helpers --------------------

def _safe_get(d, *keys, default=None):
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def _seconds_to_minutes(v):
    try:
        if v is None:
            return None
        return int(round(float(v) / 60.0))
    except Exception:
        return None


def _normalize_sleep_type(value):
    normalized = str(value or "").strip().lower()
    return normalized or None


def _sleep_candidate_priority(row):
    sleep_type = _normalize_sleep_type((row or {}).get("type"))
    if sleep_type in {"late_nap", "nap", "rest"}:
        return 0
    if sleep_type == "long_sleep":
        return 3
    if sleep_type == "main":
        return 2
    return 1


def _prefer_sleep_candidate(current, candidate):
    if current is None or _sleep_candidate_priority(candidate) >= _sleep_candidate_priority(current):
        return candidate
    return current


class OuraClient:
    BASE_URL = "https://api.ouraring.com/v2/usercollection"

    def __init__(self, token=None):
        self.token = token or os.environ.get("OURA_API_TOKEN")
        if not self.token:
            raise ValueError(
                "OURA_API_TOKEN not set. Create a Personal Access Token in Oura Cloud and export it as OURA_API_TOKEN."
            )
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def _request(self, endpoint: str, start_date=None, end_date=None):
        url = f"{self.BASE_URL}/{endpoint}"
        params = []
        if start_date:
            params.append(f"start_date={start_date}")
        if end_date:
            params.append(f"end_date={end_date}")
        if params:
            url += "?" + "&".join(params)

        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("data", [])

    def get_today_metrics(self, day=None):
        """Return a consolidated set of daily recovery metrics.

        Returns:
            (readiness_score, sleep_score, hrv, metrics_dict, raw_json_dict)

        NOTE: kept backward-ish compatibility for callers that only used the
        first 3 return values previously.
        """
        if day is None:
            day = datetime.now().strftime("%Y-%m-%d")

        readiness = self._request("daily_readiness", start_date=day, end_date=day)
        daily_sleep = self._request("daily_sleep", start_date=day, end_date=day)
        # Sleep endpoint has quirky date handling - query a wide range and filter
        from datetime import timedelta
        start_range = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
        end_range = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        sleep_all = self._request("sleep", start_date=start_range, end_date=end_range)
        sleep_detail = [s for s in sleep_all if s.get("day") == day]
        # NOTE: Oura's daily_activity often lags ("today" may return an empty list until
        # the day is fully processed). Query a wider window and pick the most recent day.
        activity = self._request("daily_activity", start_date=day, end_date=day)
        if not activity:
            from datetime import timedelta
            yday = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            activity = self._request("daily_activity", start_date=yday, end_date=day)

        r_last = readiness[-1] if readiness else {}
        ds_last = daily_sleep[-1] if daily_sleep else {}
        # Prefer a nightly/long sleep over same-day naps/rest entries regardless
        # of the upstream iteration order; preserve the last candidate otherwise.
        sd_last = None
        for s in sleep_detail:
            sd_last = _prefer_sleep_candidate(sd_last, s)
        sd_last = sd_last or {}
        a_last = activity[-1] if activity else {}

        readiness_score = _safe_get(r_last, "score")
        sleep_score = _safe_get(ds_last, "score")
        hrv = _safe_get(sd_last, "average_hrv")

        # Activity
        activity_day = _safe_get(a_last, "day")
        steps = _safe_get(a_last, "steps")
        activity_score = _safe_get(a_last, "score")
        active_calories = _safe_get(a_last, "active_calories")

        # Readiness extras
        temp_deviation = (
            _safe_get(r_last, "temperature_deviation")
            if _safe_get(r_last, "temperature_deviation", default=None) is not None
            else _safe_get(r_last, "temperature_delta")
        )
        # RHR from sleep detail endpoint (more reliable than readiness)
        resting_hr = (
            _safe_get(sd_last, "lowest_heart_rate")
            or _safe_get(sd_last, "average_heart_rate")
            or _safe_get(r_last, "resting_heart_rate")
            or _safe_get(r_last, "resting_hr")
        )

        # Sleep breakdown from detailed sleep endpoint (values in seconds)
        sleep_duration_min = _seconds_to_minutes(_safe_get(sd_last, "total_sleep_duration"))
        deep_min = _seconds_to_minutes(_safe_get(sd_last, "deep_sleep_duration"))
        rem_min = _seconds_to_minutes(_safe_get(sd_last, "rem_sleep_duration"))
        light_min = _seconds_to_minutes(_safe_get(sd_last, "light_sleep_duration"))
        awake_min = _seconds_to_minutes(_safe_get(sd_last, "awake_time"))

        metrics = {
            "readiness_score": readiness_score,
            "sleep_score": sleep_score,
            "hrv": hrv,
            "activity_day": activity_day,
            "steps": steps,
            "activity_score": activity_score,
            "active_calories": active_calories,
            "resting_hr": resting_hr,
            "temperature_deviation": temp_deviation,
            "sleep_type": _normalize_sleep_type(_safe_get(sd_last, "type")),
            "sleep_duration_min": sleep_duration_min,
            "sleep_deep_min": deep_min,
            "sleep_rem_min": rem_min,
            "sleep_light_min": light_min,
            "sleep_awake_min": awake_min,
        }

        raw = {"daily_readiness": readiness, "daily_sleep": daily_sleep, "sleep": sleep_detail, "daily_activity": activity}
        return readiness_score, sleep_score, hrv, metrics, raw

    def get_daily_range(self, start_date: str, end_date: str):
        """Return list of dicts: {day, readiness_score, sleep_score, hrv, ...} for a range."""
        readiness = self._request("daily_readiness", start_date=start_date, end_date=end_date)
        sleep = self._request("daily_sleep", start_date=start_date, end_date=end_date)
        activity = self._request("daily_activity", start_date=start_date, end_date=end_date)

        by_day: dict[str, dict] = {}

        for r in readiness:
            day = r.get("day")
            if not day:
                continue
            d = by_day.setdefault(day, {})
            d["readiness_score"] = r.get("score")
            d["temperature_deviation"] = r.get("temperature_deviation") if r.get("temperature_deviation") is not None else r.get("temperature_delta")
            d["resting_hr"] = r.get("resting_heart_rate") if r.get("resting_heart_rate") is not None else r.get("resting_hr")

        sleep_by_day: dict[str, dict] = {}
        for s in sleep:
            day = s.get("day")
            if not day:
                continue
            sleep_by_day[day] = _prefer_sleep_candidate(sleep_by_day.get(day), s)

        for day, s in sleep_by_day.items():
            d = by_day.setdefault(day, {})
            d["sleep_score"] = s.get("score")
            d["hrv"] = s.get("average_hrv")
            d["sleep_type"] = _normalize_sleep_type(s.get("type"))
            d["sleep_duration_min"] = (
                _seconds_to_minutes(s.get("total_sleep_duration"))
                or _seconds_to_minutes(s.get("total_sleep_time"))
                or _seconds_to_minutes(s.get("duration"))
            )
            d["sleep_deep_min"] = _seconds_to_minutes(s.get("deep_sleep_duration"))
            d["sleep_rem_min"] = _seconds_to_minutes(s.get("rem_sleep_duration"))
            d["sleep_light_min"] = _seconds_to_minutes(s.get("light_sleep_duration"))
            d["sleep_awake_min"] = _seconds_to_minutes(s.get("awake_time")) or _seconds_to_minutes(s.get("awake_duration"))

        for a in activity:
            day = a.get("day")
            if not day:
                continue
            d = by_day.setdefault(day, {})
            d["steps"] = a.get("steps")
            d["activity_score"] = a.get("score")
            d["active_calories"] = a.get("active_calories")

        out = []
        for day in sorted(by_day.keys()):
            out.append(
                {
                    "day": day,
                    **by_day[day],
                    "raw_json": {
                        "readiness": [r for r in readiness if r.get("day") == day],
                        "sleep": [s for s in sleep if s.get("day") == day],
                        "activity": [a for a in activity if a.get("day") == day],
                    },
                }
            )
        return out


# -------------------- SQLite storage --------------------

OURA_COLUMNS = {
    # original
    "day": "TEXT PRIMARY KEY",
    "readiness_score": "INTEGER",
    "sleep_score": "INTEGER",
    "hrv": "REAL",
    # new
    "steps": "INTEGER",
    "activity_score": "INTEGER",
    "active_calories": "INTEGER",
    "resting_hr": "REAL",
    "temperature_deviation": "REAL",
    "sleep_type": "TEXT",
    "sleep_duration_min": "INTEGER",
    "sleep_deep_min": "INTEGER",
    "sleep_rem_min": "INTEGER",
    "sleep_light_min": "INTEGER",
    "sleep_awake_min": "INTEGER",
    # meta
    "raw_json": "TEXT",
    "created_at": "TEXT",
}


def init_oura_db(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        # Create with the latest schema
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oura_daily (
                day TEXT PRIMARY KEY,
                readiness_score INTEGER,
                sleep_score INTEGER,
                hrv REAL,
                steps INTEGER,
                activity_score INTEGER,
                active_calories INTEGER,
                resting_hr REAL,
                temperature_deviation REAL,
                sleep_type TEXT,
                sleep_duration_min INTEGER,
                sleep_deep_min INTEGER,
                sleep_rem_min INTEGER,
                sleep_light_min INTEGER,
                sleep_awake_min INTEGER,
                raw_json TEXT,
                created_at TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oura_daily_day ON oura_daily(day);")

        # Schema hardening: add missing columns on older DBs
        cur = conn.execute("PRAGMA table_info(oura_daily);")
        existing = {row[1] for row in cur.fetchall()}  # name is column 1
        for col, col_type in OURA_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE oura_daily ADD COLUMN {col} {col_type};")
        conn.commit()
    finally:
        conn.close()


def upsert_oura_daily(
    db_path: str,
    day: str,
    readiness_score,
    sleep_score,
    hrv,
    raw_json,
    *,
    steps=None,
    activity_score=None,
    active_calories=None,
    resting_hr=None,
    temperature_deviation=None,
    sleep_type=None,
    sleep_duration_min=None,
    sleep_deep_min=None,
    sleep_rem_min=None,
    sleep_light_min=None,
    sleep_awake_min=None,
):
    sleep_type = _normalize_sleep_type(sleep_type)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO oura_daily(
                day, readiness_score, sleep_score, hrv,
                steps, activity_score, active_calories, resting_hr, temperature_deviation,
                sleep_type, sleep_duration_min, sleep_deep_min, sleep_rem_min, sleep_light_min, sleep_awake_min,
                raw_json, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                readiness_score=COALESCE(excluded.readiness_score, oura_daily.readiness_score),
                sleep_score=COALESCE(excluded.sleep_score, oura_daily.sleep_score),
                hrv=COALESCE(excluded.hrv, oura_daily.hrv),
                steps=COALESCE(excluded.steps, oura_daily.steps),
                activity_score=COALESCE(excluded.activity_score, oura_daily.activity_score),
                active_calories=COALESCE(excluded.active_calories, oura_daily.active_calories),
                resting_hr=COALESCE(excluded.resting_hr, oura_daily.resting_hr),
                temperature_deviation=COALESCE(excluded.temperature_deviation, oura_daily.temperature_deviation),
                sleep_type=COALESCE(excluded.sleep_type, oura_daily.sleep_type),
                sleep_duration_min=COALESCE(excluded.sleep_duration_min, oura_daily.sleep_duration_min),
                sleep_deep_min=COALESCE(excluded.sleep_deep_min, oura_daily.sleep_deep_min),
                sleep_rem_min=COALESCE(excluded.sleep_rem_min, oura_daily.sleep_rem_min),
                sleep_light_min=COALESCE(excluded.sleep_light_min, oura_daily.sleep_light_min),
                sleep_awake_min=COALESCE(excluded.sleep_awake_min, oura_daily.sleep_awake_min),
                raw_json=COALESCE(excluded.raw_json, oura_daily.raw_json),
                created_at=excluded.created_at;
            """,
            (
                day,
                readiness_score,
                sleep_score,
                hrv,
                steps,
                activity_score,
                active_calories,
                resting_hr,
                temperature_deviation,
                sleep_type,
                sleep_duration_min,
                sleep_deep_min,
                sleep_rem_min,
                sleep_light_min,
                sleep_awake_min,
                json.dumps(raw_json) if raw_json is not None else None,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row):
    return {
        "day": row[0],
        "readiness_score": row[1],
        "sleep_score": row[2],
        "hrv": row[3],
        "steps": row[4],
        "activity_score": row[5],
        "active_calories": row[6],
        "resting_hr": row[7],
        "temperature_deviation": row[8],
        "sleep_type": row[9],
        "sleep_duration_min": row[10],
        "sleep_deep_min": row[11],
        "sleep_rem_min": row[12],
        "sleep_light_min": row[13],
        "sleep_awake_min": row[14],
        "raw_json": row[15],
        "created_at": row[16],
    }


def get_oura_daily(db_path: str, day: str):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT day, readiness_score, sleep_score, hrv,
                   steps, activity_score, active_calories, resting_hr, temperature_deviation,
                   sleep_type, sleep_duration_min, sleep_deep_min, sleep_rem_min, sleep_light_min, sleep_awake_min,
                   raw_json, created_at
            FROM oura_daily
            WHERE day=?
            """,
            (day,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_oura_daily_range(db_path: str, start_date: str, end_date: str):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT day, readiness_score, sleep_score, hrv,
                   steps, activity_score, active_calories, resting_hr, temperature_deviation,
                   sleep_type, sleep_duration_min, sleep_deep_min, sleep_rem_min, sleep_light_min, sleep_awake_min,
                   raw_json, created_at
            FROM oura_daily
            WHERE day >= ? AND day <= ?
            ORDER BY day ASC;
            """,
            (start_date, end_date),
        )
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def compute_hrv_trend(hrv_values: list[float]):
    """Compute a simple HRV trend label from a series.

    Uses last-3 vs previous-3 average difference with a small threshold.
    """
    vals = [v for v in hrv_values if v is not None]
    if len(vals) < 4:
        return "stable"

    last = vals[-3:]
    prev = vals[-6:-3] if len(vals) >= 6 else vals[:-3]

    if not prev:
        return "stable"

    last_avg = sum(last) / len(last)
    prev_avg = sum(prev) / len(prev)
    diff = last_avg - prev_avg

    # Threshold in ms
    if diff > 2.0:
        return "improving"
    if diff < -2.0:
        return "declining"
    return "stable"
