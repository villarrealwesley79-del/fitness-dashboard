"""
data_store.py — SQLite-based per-user data store for Fitness Dashboard SaaS.

Replaces flat JSON file storage with user-isolated SQLite tables.
Each table has a user_id FK referencing auth.db users.id.

Usage:
    from data_store import init_data_db, get_body_data, add_body_record, ...
    init_data_db()  # call once on app startup

Created for Fitness Dashboard SaaS per-user isolation.
"""

import os
import sqlite3
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DB = os.path.join(_DIR, "fitness_data.db")


def _get_db() -> sqlite3.Connection:
    """Return a connection with row_factory set to sqlite3.Row."""
    conn = sqlite3.connect(DATA_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _row_to_dict(row) -> dict:
    """Convert sqlite3.Row to plain dict, dropping user_id (internal)."""
    d = dict(row)
    d.pop("user_id", None)
    d.pop("id", None)
    return d


# ── Schema ────────────────────────────────────────────────────────────────────
def init_data_db():
    """Create all tables if they don't exist. Safe to call multiple times."""
    with _get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS body_data (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                date        TEXT    NOT NULL,
                weight_lbs  REAL,
                body_fat_pct REAL,
                neck_in     REAL,
                waist_in    REAL,
                hip_in      REAL,
                chest_in    REAL,
                arm_in      REAL,
                thigh_in    REAL,
                notes       TEXT,
                UNIQUE(user_id, date)
            );

            CREATE TABLE IF NOT EXISTS cardio_data (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL,
                date             TEXT    NOT NULL,
                activity_type    TEXT,
                duration_minutes REAL,
                avg_heart_rate   INTEGER,
                max_heart_rate   INTEGER,
                intensity        TEXT,
                distance_miles   REAL,
                calories_burned  INTEGER,
                notes            TEXT,
                UNIQUE(user_id, date, activity_type)
            );

            CREATE TABLE IF NOT EXISTS nutrition_data (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                date       TEXT    NOT NULL,
                calories   INTEGER,
                protein_g  REAL,
                carbs_g    REAL,
                fat_g      REAL,
                fiber_g    REAL,
                water_oz   REAL,
                sodium_mg  INTEGER,
                notes      TEXT,
                UNIQUE(user_id, date)
            );

            CREATE TABLE IF NOT EXISTS recovery_data (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL,
                date             TEXT    NOT NULL,
                recovery_type    TEXT,
                duration_minutes REAL,
                temperature      REAL,
                notes            TEXT
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id                   INTEGER PRIMARY KEY,
                training_goal             TEXT,
                sessions_per_week_target  INTEGER,
                available_time_minutes    INTEGER,
                target_weight_lbs         REAL,
                target_body_fat_pct       REAL,
                updated                   TEXT DEFAULT (datetime('now'))
            );
        """)
        # Idempotent column adds for pre-existing DBs created before this column existed.
        # SQLite has no native ADD COLUMN IF NOT EXISTS, so probe table_info first.
        existing_nutrition_cols = {r["name"] for r in conn.execute("PRAGMA table_info(nutrition_data)").fetchall()}
        if "sodium_mg" not in existing_nutrition_cols:
            conn.execute("ALTER TABLE nutrition_data ADD COLUMN sodium_mg INTEGER")
        conn.commit()


# ── Body Data ─────────────────────────────────────────────────────────────────
def get_body_data(user_id: int, limit: Optional[int] = None, since: Optional[str] = None) -> list[dict]:
    """Return body records for user, sorted by date desc."""
    sql = "SELECT * FROM body_data WHERE user_id = ?"
    params: list = [user_id]
    if since:
        sql += " AND date >= ?"
        params.append(since)
    sql += " ORDER BY date DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_body_record(user_id: int, record: dict) -> None:
    """Insert or replace a body record for the given user_id."""
    cols = ["user_id", "date", "weight_lbs", "body_fat_pct", "neck_in", "waist_in",
            "hip_in", "chest_in", "arm_in", "thigh_in", "notes"]
    vals = [user_id] + [record.get(c) for c in cols[1:]]
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO body_data ({', '.join(cols)}) VALUES ({placeholders})"
    with _get_db() as conn:
        conn.execute(sql, vals)
        conn.commit()


def get_latest_body(user_id: int) -> Optional[dict]:
    """Return the most recent body record for user."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM body_data WHERE user_id = ? ORDER BY date DESC LIMIT 1", (user_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


# ── Cardio Data ───────────────────────────────────────────────────────────────
def get_cardio_data(user_id: int, limit: Optional[int] = None, since: Optional[str] = None) -> list[dict]:
    """Return cardio records for user, sorted by date desc."""
    sql = "SELECT * FROM cardio_data WHERE user_id = ?"
    params: list = [user_id]
    if since:
        sql += " AND date >= ?"
        params.append(since)
    sql += " ORDER BY date DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_cardio_record(user_id: int, record: dict) -> None:
    """Insert or replace a cardio record."""
    cols = ["user_id", "date", "activity_type", "duration_minutes", "avg_heart_rate",
            "max_heart_rate", "intensity", "distance_miles", "calories_burned", "notes"]
    vals = [user_id] + [record.get(c) for c in cols[1:]]
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO cardio_data ({', '.join(cols)}) VALUES ({placeholders})"
    with _get_db() as conn:
        conn.execute(sql, vals)
        conn.commit()


# ── Nutrition Data ────────────────────────────────────────────────────────────
def get_nutrition_data(user_id: int, limit: Optional[int] = None, since: Optional[str] = None) -> list[dict]:
    """Return nutrition records for user, sorted by date desc."""
    sql = "SELECT * FROM nutrition_data WHERE user_id = ?"
    params: list = [user_id]
    if since:
        sql += " AND date >= ?"
        params.append(since)
    sql += " ORDER BY date DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_nutrition_record(user_id: int, record: dict) -> None:
    """Insert or replace a nutrition record."""
    cols = ["user_id", "date", "calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "water_oz", "sodium_mg", "notes"]
    vals = [user_id] + [record.get(c) for c in cols[1:]]
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO nutrition_data ({', '.join(cols)}) VALUES ({placeholders})"
    with _get_db() as conn:
        conn.execute(sql, vals)
        conn.commit()


# ── Recovery Data ─────────────────────────────────────────────────────────────
def get_recovery_data(user_id: int, limit: Optional[int] = None) -> list[dict]:
    """Return recovery records for user, sorted by date desc."""
    sql = "SELECT * FROM recovery_data WHERE user_id = ? ORDER BY date DESC"
    params: list = [user_id]
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_recovery_record(user_id: int, record: dict) -> None:
    """Insert a recovery record (no UNIQUE constraint — multiple sessions per day ok)."""
    cols = ["user_id", "date", "recovery_type", "duration_minutes", "temperature", "notes"]
    vals = [user_id] + [record.get(c) for c in cols[1:]]
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT INTO recovery_data ({', '.join(cols)}) VALUES ({placeholders})"
    with _get_db() as conn:
        conn.execute(sql, vals)
        conn.commit()


# ── Settings ──────────────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "training_goal": "body_recomp",
    "sessions_per_week_target": 4,
    "available_time_minutes": 60,
    "target_weight_lbs": 175.0,
    "target_body_fat_pct": 12.0,
}


def get_settings(user_id: int) -> dict:
    """Return settings dict for user. Falls back to defaults if not set."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return DEFAULT_SETTINGS.copy()
    d = _row_to_dict(row)
    d.pop("updated", None)
    return d


def upsert_settings(user_id: int, settings: dict) -> None:
    """Create or update user settings (full replace)."""
    cols = ["user_id", "training_goal", "sessions_per_week_target",
            "available_time_minutes", "target_weight_lbs", "target_body_fat_pct",
            "updated"]
    vals = [
        user_id,
        settings.get("training_goal", DEFAULT_SETTINGS["training_goal"]),
        settings.get("sessions_per_week_target", DEFAULT_SETTINGS["sessions_per_week_target"]),
        settings.get("available_time_minutes", DEFAULT_SETTINGS["available_time_minutes"]),
        settings.get("target_weight_lbs", DEFAULT_SETTINGS["target_weight_lbs"]),
        settings.get("target_body_fat_pct", DEFAULT_SETTINGS["target_body_fat_pct"]),
        "datetime('now')",
    ]
    # Use INSERT OR REPLACE, but handle updated specially
    sql = """
        INSERT INTO user_settings (user_id, training_goal, sessions_per_week_target,
            available_time_minutes, target_weight_lbs, target_body_fat_pct, updated)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            training_goal = excluded.training_goal,
            sessions_per_week_target = excluded.sessions_per_week_target,
            available_time_minutes = excluded.available_time_minutes,
            target_weight_lbs = excluded.target_weight_lbs,
            target_body_fat_pct = excluded.target_body_fat_pct,
            updated = datetime('now')
    """
    with _get_db() as conn:
        conn.execute(sql, vals[:6])
        conn.commit()


# ── Account Management ────────────────────────────────────────────────────────
def delete_user_data(user_id: int) -> None:
    """Permanently delete all data for a user (GDPR / account deletion)."""
    tables = ["body_data", "cardio_data", "nutrition_data", "recovery_data", "user_settings"]
    with _get_db() as conn:
        for table in tables:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.commit()


def get_user_data_summary(user_id: int) -> dict:
    """Return record counts per table for a user (useful for admin/debugging)."""
    tables = ["body_data", "cardio_data", "nutrition_data", "recovery_data"]
    summary = {}
    with _get_db() as conn:
        for table in tables:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            summary[table] = count
    return summary
