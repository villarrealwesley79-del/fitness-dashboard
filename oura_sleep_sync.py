"""Oura Sleep Data Sync

Creates oura_sleep table and syncs all available sleep data from Oura API.
"""

import os
import json
import sqlite3
import urllib.request
from datetime import datetime, timedelta


def create_sleep_table(db_path: str):
    """Create oura_sleep table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oura_sleep (
                day TEXT,
                type TEXT,
                bedtime_start TEXT,
                bedtime_end TEXT,
                total_sleep_min INTEGER,
                deep_sleep_min INTEGER,
                rem_sleep_min INTEGER,
                light_sleep_min INTEGER,
                awake_time_min INTEGER,
                sleep_score INTEGER,
                efficiency INTEGER,
                avg_heart_rate REAL,
                avg_hrv REAL,
                avg_breath_rate REAL,
                lowest_heart_rate INTEGER,
                restfulness_score INTEGER,
                raw_json TEXT,
                created_at TEXT,
                PRIMARY KEY (day, type)
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oura_sleep_day ON oura_sleep(day);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oura_sleep_type ON oura_sleep(type);")
        conn.commit()
        print("✅ oura_sleep table created/verified")
    finally:
        conn.close()


def seconds_to_minutes(seconds):
    """Convert seconds to minutes, handle None."""
    if seconds is None:
        return None
    try:
        return int(round(float(seconds) / 60.0))
    except:
        return None


def fetch_sleep_data(api_token: str, start_date: str, end_date: str = None, next_token: str = None):
    """Fetch sleep data from Oura API v2."""
    url = "https://api.ouraring.com/v2/usercollection/sleep"
    params = [f"start_date={start_date}"]

    if end_date:
        params.append(f"end_date={end_date}")
    if next_token:
        params.append(f"next_token={next_token}")

    if params:
        url += "?" + "&".join(params)

    headers = {"Authorization": f"Bearer {api_token}"}
    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    return data.get("data", []), data.get("next_token")


def upsert_sleep_record(conn, record: dict):
    """Insert or update a sleep record."""
    day = record.get("day")
    sleep_type = record.get("type", "long_sleep")

    # Parse timestamps
    bedtime_start = record.get("bedtime_start")
    bedtime_end = record.get("bedtime_end")

    # Sleep durations (convert from seconds)
    total_sleep_min = seconds_to_minutes(record.get("total_sleep_duration"))
    deep_sleep_min = seconds_to_minutes(record.get("deep_sleep_duration"))
    rem_sleep_min = seconds_to_minutes(record.get("rem_sleep_duration"))
    light_sleep_min = seconds_to_minutes(record.get("light_sleep_duration"))
    awake_time_min = seconds_to_minutes(record.get("awake_time"))

    # Sleep quality metrics
    sleep_score = record.get("score")
    efficiency = record.get("efficiency")

    # Heart metrics
    avg_heart_rate = record.get("average_heart_rate")
    avg_hrv = record.get("average_hrv")
    avg_breath_rate = record.get("average_breath")
    lowest_heart_rate = record.get("lowest_heart_rate")

    # Restfulness
    restfulness_score = record.get("restless_periods")  # Could also use movement_30_sec

    conn.execute("""
        INSERT INTO oura_sleep (
            day, type, bedtime_start, bedtime_end,
            total_sleep_min, deep_sleep_min, rem_sleep_min, light_sleep_min, awake_time_min,
            sleep_score, efficiency,
            avg_heart_rate, avg_hrv, avg_breath_rate, lowest_heart_rate, restfulness_score,
            raw_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(day, type) DO UPDATE SET
            bedtime_start=excluded.bedtime_start,
            bedtime_end=excluded.bedtime_end,
            total_sleep_min=excluded.total_sleep_min,
            deep_sleep_min=excluded.deep_sleep_min,
            rem_sleep_min=excluded.rem_sleep_min,
            light_sleep_min=excluded.light_sleep_min,
            awake_time_min=excluded.awake_time_min,
            sleep_score=excluded.sleep_score,
            efficiency=excluded.efficiency,
            avg_heart_rate=excluded.avg_heart_rate,
            avg_hrv=excluded.avg_hrv,
            avg_breath_rate=excluded.avg_breath_rate,
            lowest_heart_rate=excluded.lowest_heart_rate,
            restfulness_score=excluded.restfulness_score,
            raw_json=excluded.raw_json,
            created_at=excluded.created_at
    """, (
        day, sleep_type, bedtime_start, bedtime_end,
        total_sleep_min, deep_sleep_min, rem_sleep_min, light_sleep_min, awake_time_min,
        sleep_score, efficiency,
        avg_heart_rate, avg_hrv, avg_breath_rate, lowest_heart_rate, restfulness_score,
        json.dumps(record), datetime.now().isoformat()
    ))


def sync_sleep_data(db_path: str, api_token: str, start_date: str = "2024-01-01"):
    """Sync all sleep data from Oura API."""
    print(f"🔄 Syncing sleep data from {start_date}...")

    conn = sqlite3.connect(db_path)
    total_records = 0
    long_sleep_count = 0

    try:
        next_token = None
        page = 1

        while True:
            print(f"  Fetching page {page}...")
            data, next_token = fetch_sleep_data(api_token, start_date, next_token=next_token)

            if not data:
                print("  No more data")
                break

            for record in data:
                sleep_type = record.get("type", "long_sleep")
                upsert_sleep_record(conn, record)
                total_records += 1

                if sleep_type == "long_sleep":
                    long_sleep_count += 1

            conn.commit()
            print(f"  ✅ Processed {len(data)} records (page {page})")

            if not next_token:
                break

            page += 1

        print(f"\n✅ Sync complete!")
        print(f"  Total records: {total_records}")
        print(f"  Long sleep records: {long_sleep_count}")
        print(f"  Other (naps, etc): {total_records - long_sleep_count}")

    finally:
        conn.close()


def get_latest_sleep(db_path: str, days: int = 1, long_sleep_only: bool = True):
    """Get most recent sleep records."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        query = "SELECT * FROM oura_sleep"
        params = []

        if long_sleep_only:
            query += " WHERE type = 'long_sleep'"

        query += " ORDER BY day DESC LIMIT ?"
        params.append(days)

        cur = conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_sleep_range(db_path: str, start_date: str, end_date: str, long_sleep_only: bool = True):
    """Get sleep records in a date range."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        query = "SELECT * FROM oura_sleep WHERE day >= ? AND day <= ?"
        params = [start_date, end_date]

        if long_sleep_only:
            query += " AND type = 'long_sleep'"

        query += " ORDER BY day ASC"

        cur = conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def calculate_bedtime_variance(db_path: str, days: int = 7):
    """Calculate bedtime consistency (variance in minutes)."""
    end = datetime.now().date()
    start = end - timedelta(days=days - 1)

    records = get_sleep_range(db_path, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    if len(records) < 2:
        return None

    bedtimes = []
    for r in records:
        bedtime_str = r.get("bedtime_start")
        if bedtime_str:
            try:
                # Parse ISO timestamp and extract time of day in minutes
                dt = datetime.fromisoformat(bedtime_str.replace("Z", "+00:00"))
                minutes_from_midnight = dt.hour * 60 + dt.minute
                bedtimes.append(minutes_from_midnight)
            except:
                continue

    if len(bedtimes) < 2:
        return None

    # Calculate variance
    mean = sum(bedtimes) / len(bedtimes)
    variance = sum((x - mean) ** 2 for x in bedtimes) / len(bedtimes)
    std_dev = variance ** 0.5

    return int(round(std_dev))


if __name__ == "__main__":
    import sys

    db_path = os.path.join(os.path.dirname(__file__), "oura_daily.sqlite3")
    api_token = os.environ.get("OURA_API_TOKEN")

    if not api_token:
        print("❌ OURA_API_TOKEN not set in environment")
        sys.exit(1)

    # Create table
    create_sleep_table(db_path)

    # Sync data
    sync_sleep_data(db_path, api_token, start_date="2024-01-01")

    # Show latest sleep
    print("\n📊 Last 7 nights:")
    latest = get_latest_sleep(db_path, days=7)
    for record in latest:
        print(f"  {record['day']}: {record['total_sleep_min']}min, score={record['sleep_score']}")

    # Bedtime variance
    variance = calculate_bedtime_variance(db_path, days=7)
    if variance:
        print(f"\n⏰ Bedtime consistency: ±{variance} min variance")
