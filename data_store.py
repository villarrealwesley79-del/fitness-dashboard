"""
data_store.py — SQLite-based per-user data store for Fitness Dashboard SaaS.

Replaces flat JSON file storage with user-isolated SQLite tables.
Each table has a user_id FK referencing auth.db users.id.

Usage:
    from data_store import init_data_db, get_body_data, add_body_record, ...
    init_data_db()  # call once on app startup

Created for Fitness Dashboard SaaS per-user isolation.
"""

import json
import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional
from runtime_config import data_path
from settings_contract import SQL_SETTINGS_DEFAULTS

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DB = data_path("fitness_data.db")


@contextmanager
def _get_db() -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection and always close it after the operation."""
    conn = sqlite3.connect(DATA_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row) -> dict:
    """Convert sqlite3.Row to plain dict, dropping user_id (internal)."""
    d = dict(row)
    d.pop("user_id", None)
    d.pop("id", None)
    return d


def _json_dumps_or_none(value):
    if value in (None, ""):
        return None
    return json.dumps(value, sort_keys=True, default=str)


def _json_loads_or_none(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _subscription_public_row(row) -> dict:
    payload = _json_loads_or_none(row["subscription_json"]) or {}
    endpoint = row["endpoint"] or ""
    host = endpoint.split("/")[2] if "://" in endpoint and len(endpoint.split("/")) > 2 else None
    return {
        "endpoint_hash": row["endpoint_hash"],
        "endpoint_host": host,
        "permission_state": row["permission_state"],
        "pwa_installed": bool(row["pwa_installed"]) if row["pwa_installed"] is not None else None,
        "revoked": bool(row["revoked_at"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "keys_present": bool(((payload.get("keys") or {}).get("p256dh")) and ((payload.get("keys") or {}).get("auth"))),
    }


def save_push_subscription(user_id: int, subscription: dict, metadata: Optional[dict] = None) -> dict:
    """Persist a Web Push subscription and return a secret-free summary."""
    if not isinstance(subscription, dict):
        raise ValueError("subscription must be an object")
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys") or {}
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        raise ValueError("subscription.endpoint must be an https URL")
    if not isinstance(keys, dict) or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("subscription.keys.p256dh and keys.auth are required")
    metadata = metadata or {}
    permission_state = metadata.get("permission_state")
    if not isinstance(permission_state, str) or permission_state not in {"granted", "denied", "default"}:
        permission_state = None
    endpoint_hash = _endpoint_hash(endpoint)
    now = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        conn.execute(
            """
            INSERT INTO push_subscriptions (
                user_id, endpoint_hash, endpoint, subscription_json, permission_state,
                pwa_installed, user_agent, revoked_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(user_id, endpoint_hash) DO UPDATE SET
                endpoint=excluded.endpoint,
                subscription_json=excluded.subscription_json,
                permission_state=excluded.permission_state,
                pwa_installed=excluded.pwa_installed,
                user_agent=excluded.user_agent,
                revoked_at=NULL,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                endpoint_hash,
                endpoint,
                json.dumps(subscription, sort_keys=True),
                permission_state,
                1 if metadata.get("pwa_installed") is True else 0 if metadata.get("pwa_installed") is False else None,
                metadata.get("user_agent"),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM push_subscriptions WHERE user_id = ? AND endpoint_hash = ?",
            (user_id, endpoint_hash),
        ).fetchone()
    return _subscription_public_row(row)


def list_push_subscriptions(user_id: int, include_revoked: bool = False) -> list[dict]:
    sql = "SELECT * FROM push_subscriptions WHERE user_id = ?"
    params: list = [user_id]
    if not include_revoked:
        sql += " AND revoked_at IS NULL"
    sql += " ORDER BY updated_at DESC"
    with _get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_subscription_public_row(row) for row in rows]


def get_push_subscription_for_delivery(user_id: int, endpoint_hash: str | None = None) -> Optional[dict]:
    """Return one active raw subscription for server-side Web Push delivery."""
    sql = "SELECT * FROM push_subscriptions WHERE user_id = ? AND revoked_at IS NULL"
    params: list = [user_id]
    if endpoint_hash:
        sql += " AND endpoint_hash = ?"
        params.append(endpoint_hash)
    sql += " ORDER BY updated_at DESC LIMIT 1"
    with _get_db() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    return {
        "summary": _subscription_public_row(row),
        "subscription": _json_loads_or_none(row["subscription_json"]),
    }


def revoke_push_subscription(user_id: int, endpoint_hash: str) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        cur = conn.execute(
            """
            UPDATE push_subscriptions
            SET revoked_at = ?, updated_at = ?
            WHERE user_id = ? AND endpoint_hash = ? AND revoked_at IS NULL
            """,
            (now, now, user_id, endpoint_hash),
        )
        conn.commit()
    return cur.rowcount > 0


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

            CREATE TABLE IF NOT EXISTS current_workout_plans (
                user_id       INTEGER PRIMARY KEY,
                fingerprint   TEXT    NOT NULL,
                plan_json     TEXT    NOT NULL,
                plan_generation INTEGER,
                plan_version  INTEGER NOT NULL DEFAULT 0,
                updated_at    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workout_plan_generations (
                user_id       INTEGER PRIMARY KEY,
                generation    INTEGER NOT NULL
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

            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id               INTEGER NOT NULL,
                endpoint_hash         TEXT    NOT NULL,
                endpoint              TEXT    NOT NULL,
                subscription_json     TEXT    NOT NULL,
                permission_state      TEXT,
                pwa_installed         INTEGER,
                user_agent            TEXT,
                revoked_at            TEXT,
                created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at            TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, endpoint_hash)
            );
        """)
        # Idempotent column adds for pre-existing DBs created before this column existed.
        # SQLite has no native ADD COLUMN IF NOT EXISTS, so probe table_info first.
        existing_current_plan_cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(current_workout_plans)").fetchall()
        }
        if "plan_version" not in existing_current_plan_cols:
            conn.execute(
                "ALTER TABLE current_workout_plans "
                "ADD COLUMN plan_version INTEGER NOT NULL DEFAULT 0"
            )
        if "plan_generation" not in existing_current_plan_cols:
            conn.execute(
                "ALTER TABLE current_workout_plans ADD COLUMN plan_generation INTEGER"
            )
        generation_backfill_needed = conn.execute(
            """
            SELECT 1
              FROM current_workout_plans AS current_plan
              LEFT JOIN workout_plan_generations AS generations
                ON generations.user_id = current_plan.user_id
             WHERE current_plan.plan_generation IS NULL
                OR generations.user_id IS NULL
                OR generations.generation < current_plan.plan_generation
             LIMIT 1
            """
        ).fetchone() is not None
        if generation_backfill_needed:
            conn.execute(
                """
                INSERT INTO workout_plan_generations (user_id, generation)
                SELECT user_id, COALESCE(plan_generation, 1)
                  FROM current_workout_plans
                 WHERE true
                ON CONFLICT(user_id) DO UPDATE SET
                    generation = MAX(
                        workout_plan_generations.generation,
                        excluded.generation
                    )
                """
            )
            conn.execute(
                """
                UPDATE current_workout_plans
                   SET plan_generation = (
                       SELECT generation
                         FROM workout_plan_generations
                        WHERE workout_plan_generations.user_id = current_workout_plans.user_id
                   )
                 WHERE plan_generation IS NULL
                """
            )
        existing_push_cols = {r["name"] for r in conn.execute("PRAGMA table_info(push_subscriptions)").fetchall()}
        push_columns = {
            "permission_state": "TEXT",
            "pwa_installed": "INTEGER",
            "user_agent": "TEXT",
            "revoked_at": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        for col, col_type in push_columns.items():
            if col not in existing_push_cols:
                conn.execute(f"ALTER TABLE push_subscriptions ADD COLUMN {col} {col_type}")
        conn.commit()


def save_current_workout_plan(
    user_id: int,
    fingerprint: str,
    plan: dict,
    *,
    only_if_current_plan_absent: bool = False,
    expected_plan_generation: int | None = None,
    expected_plan_version: int | None = None,
    expected_absent_plan_generation: int | None = None,
) -> dict | None:
    """Persist the current generated workout plan for one user.

    When an expected plan identity is supplied, the write is a compare-and-set
    against the durable ``(plan_generation, plan_version)`` pair.  A mismatch
    returns ``None`` without changing the row.  ``only_if_current_plan_absent``
    provides the equivalent compare-and-set for an expected absent row.  An
    absent-row expectation may also include the durable generation tombstone,
    preventing a create-delete ABA from being mistaken for the original
    absence.
    """
    init_data_db()
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    if not fingerprint:
        raise ValueError("fingerprint is required")
    now = datetime.now().isoformat(timespec="seconds")
    plan_json = json.dumps(plan, sort_keys=True, default=str)
    with _get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current_plan_row = conn.execute(
            """
            SELECT plan_generation, plan_version
              FROM current_workout_plans
             WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if only_if_current_plan_absent and current_plan_row is not None:
            conn.rollback()
            return None
        if only_if_current_plan_absent and expected_absent_plan_generation is not None:
            generation_row = conn.execute(
                "SELECT generation FROM workout_plan_generations WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current_generation = int(generation_row["generation"] or 0) if generation_row else 0
            if current_generation != int(expected_absent_plan_generation):
                conn.rollback()
                return None
        if expected_plan_generation is not None or expected_plan_version is not None:
            if expected_plan_generation is None or expected_plan_version is None:
                conn.rollback()
                return None
            if (
                current_plan_row is None
                or int(current_plan_row["plan_generation"]) != int(expected_plan_generation)
                or int(current_plan_row["plan_version"] or 0) != int(expected_plan_version)
            ):
                conn.rollback()
                return None
        if current_plan_row is None:
            conn.execute(
                """
                INSERT INTO workout_plan_generations (user_id, generation)
                VALUES (?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    generation = workout_plan_generations.generation + 1
                """,
                (user_id,),
            )
            plan_generation = conn.execute(
                "SELECT generation FROM workout_plan_generations WHERE user_id = ?",
                (user_id,),
            ).fetchone()["generation"]
        else:
            plan_generation = current_plan_row["plan_generation"]
        conn.execute(
            """
            INSERT INTO current_workout_plans (
                user_id, fingerprint, plan_json,
                plan_generation, plan_version, updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                fingerprint=excluded.fingerprint,
                plan_json=excluded.plan_json,
                plan_version=CASE
                    WHEN current_workout_plans.fingerprint <> excluded.fingerprint
                      OR current_workout_plans.plan_json <> excluded.plan_json
                    THEN current_workout_plans.plan_version + 1
                    ELSE current_workout_plans.plan_version
                END,
                updated_at=excluded.updated_at
            """,
            (user_id, fingerprint, plan_json, plan_generation, now),
        )
        saved_plan_row = conn.execute(
            """
            SELECT plan_generation, plan_version
              FROM current_workout_plans
             WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        conn.commit()
    return {
        "user_id": user_id,
        "fingerprint": fingerprint,
        "plan": json.loads(plan_json),
        "plan_generation": int(saved_plan_row["plan_generation"]),
        "plan_version": int(saved_plan_row["plan_version"]),
        "updated_at": now,
    }


def get_current_workout_plan(user_id: int, fingerprint: str | None = None) -> Optional[dict]:
    """Return the persisted current workout plan for one user."""
    sql = "SELECT * FROM current_workout_plans WHERE user_id = ?"
    params: list = [user_id]
    if fingerprint is not None:
        sql += " AND fingerprint = ?"
        params.append(fingerprint)
    with _get_db() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "fingerprint": row["fingerprint"],
        "plan": _json_loads_or_none(row["plan_json"]) or {},
        "plan_generation": int(row["plan_generation"]),
        "plan_version": int(row["plan_version"] or 0),
        "updated_at": row["updated_at"],
    }


def get_current_workout_plan_snapshot(user_id: int) -> dict:
    """Read the current plan and its generation tombstone in one snapshot.

    The current-plan row and generation tombstone are the identity used by
    workout-plan CAS.  Keep both reads on the same
    SQLite transaction so a concurrent create/delete cannot produce a torn
    identity pair.
    """
    init_data_db()
    with _get_db() as conn:
        conn.execute("BEGIN")
        row = conn.execute(
            "SELECT * FROM current_workout_plans WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        generation_row = conn.execute(
            "SELECT generation FROM workout_plan_generations WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    current_plan = None
    if row:
        current_plan = {
            "user_id": row["user_id"],
            "fingerprint": row["fingerprint"],
            "plan": _json_loads_or_none(row["plan_json"]) or {},
            "plan_generation": int(row["plan_generation"]),
            "plan_version": int(row["plan_version"] or 0),
            "updated_at": row["updated_at"],
        }
    return {
        "current_plan": current_plan,
        "plan_generation": int(generation_row["generation"] or 0) if generation_row else 0,
    }


def get_workout_plan_generation(user_id: int) -> int:
    """Return the durable current-plan generation tombstone for one user."""
    init_data_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT generation FROM workout_plan_generations WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["generation"] or 0) if row else 0


def delete_current_workout_plan(user_id: int) -> bool:
    """Delete the persisted current workout plan for one user."""
    with _get_db() as conn:
        cursor = conn.execute("DELETE FROM current_workout_plans WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0


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
DEFAULT_SETTINGS = SQL_SETTINGS_DEFAULTS


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
    """Permanently delete a user's data in the live tables (GDPR / account deletion).

    Inert tables left from the retired food feature are not touched.
    """
    tables = [
        "body_data",
        "cardio_data",
        "current_workout_plans",
        "workout_plan_generations",
        "recovery_data",
        "user_settings",
    ]
    with _get_db() as conn:
        for table in tables:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))
        conn.commit()


def get_user_data_summary(user_id: int) -> dict:
    """Return record counts per table for a user (useful for admin/debugging)."""
    tables = [
        "body_data",
        "cardio_data",
        "current_workout_plans",
        "recovery_data",
        "push_subscriptions",
    ]
    summary = {}
    with _get_db() as conn:
        for table in tables:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            summary[table] = count
    return summary
