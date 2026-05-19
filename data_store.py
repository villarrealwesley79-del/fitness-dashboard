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
import os
import sqlite3
import hashlib
from datetime import datetime
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


FOOD_ESTIMATE_FIELDS = {
    "item_name",
    "portion_description",
    "meal_type",
    "calories",
    "protein_g",
    "carbs_g",
    "fat_g",
    "sodium_mg",
    "fiber_g",
    "confidence",
    "ambiguous",
    "uncertainty_notes",
    "source",
    "logged_at",
    "date",
    "external_food_id",
    "verified_source_url",
    "data_fetched_at",
    "portion_basis",
    "brand_id",
    "underlying_source",
    "off_attribution",
    "personal_vocab_phrase",
}


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


def sanitize_food_estimate(estimate: Optional[dict]) -> Optional[dict]:
    """Return export/API-safe estimate fields, dropping raw traces and images."""
    if not isinstance(estimate, dict):
        return None
    safe = {k: estimate.get(k) for k in FOOD_ESTIMATE_FIELDS if k in estimate}
    return safe or None


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
                metadata.get("permission_state"),
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

            CREATE TABLE IF NOT EXISTS food_logs (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                INTEGER NOT NULL,
                client_id              TEXT,
                date                   TEXT    NOT NULL,
                logged_at              TEXT    NOT NULL,
                source_timestamp       TEXT,
                meal_type              TEXT,
                item_name              TEXT,
                portion_description    TEXT,
                context_note           TEXT,
                calories               INTEGER,
                protein_g              REAL,
                carbs_g                REAL,
                fat_g                  REAL,
                sodium_mg              INTEGER,
                fiber_g                REAL,
                confidence             REAL,
                source                 TEXT,
                correction_state       TEXT,
                original_estimate_json TEXT,
                created_at             TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at             TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, client_id)
            );

            CREATE TABLE IF NOT EXISTS branded_lookup_cache (
                normalized_text TEXT PRIMARY KEY,
                source          TEXT NOT NULL,
                response_json   TEXT NOT NULL,
                fetched_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS personal_vocab (
                user_id              INTEGER NOT NULL,
                normalized_input     TEXT    NOT NULL,
                phrase               TEXT    NOT NULL,
                canonical_resolution TEXT    NOT NULL,
                accept_count         INTEGER NOT NULL DEFAULT 0,
                correct_count        INTEGER NOT NULL DEFAULT 0,
                confidence_boost     REAL    NOT NULL DEFAULT 0,
                last_used            TEXT    NOT NULL,
                created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at           TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(user_id, normalized_input)
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
        existing_nutrition_cols = {r["name"] for r in conn.execute("PRAGMA table_info(nutrition_data)").fetchall()}
        if "sodium_mg" not in existing_nutrition_cols:
            conn.execute("ALTER TABLE nutrition_data ADD COLUMN sodium_mg INTEGER")
        existing_food_log_cols = {r["name"] for r in conn.execute("PRAGMA table_info(food_logs)").fetchall()}
        food_log_columns = {
            "client_id": "TEXT",
            "source_timestamp": "TEXT",
            "meal_type": "TEXT",
            "item_name": "TEXT",
            "portion_description": "TEXT",
            "context_note": "TEXT",
            "sodium_mg": "INTEGER",
            "fiber_g": "REAL",
            "confidence": "REAL",
            "source": "TEXT",
            "correction_state": "TEXT",
            "original_estimate_json": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        for col, col_type in food_log_columns.items():
            if col not in existing_food_log_cols:
                conn.execute(f"ALTER TABLE food_logs ADD COLUMN {col} {col_type}")
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
        conn.execute("""
            UPDATE food_logs
               SET client_id = NULL
             WHERE client_id IS NOT NULL
               AND id NOT IN (
                   SELECT MAX(id)
                     FROM food_logs
                    WHERE client_id IS NOT NULL
                    GROUP BY user_id, client_id
               )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_food_logs_user_client_id "
            "ON food_logs(user_id, client_id)"
        )
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


def _food_log_row_to_dict(row) -> dict:
    d = _row_to_dict(row)
    d["original_estimate"] = _json_loads_or_none(d.pop("original_estimate_json", None))
    return d


def get_food_logs(user_id: int, limit: Optional[int] = None, since: Optional[str] = None) -> list[dict]:
    """Return accepted food logs for user, sorted by logged_at desc."""
    sql = "SELECT * FROM food_logs WHERE user_id = ?"
    params: list = [user_id]
    if since:
        sql += " AND date >= ?"
        params.append(since)
    sql += " ORDER BY logged_at DESC, id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_food_log_row_to_dict(r) for r in rows]


def get_branded_lookup_cache(normalized_text: str) -> Optional[dict]:
    """Return a cached branded-food lookup payload by normalized text."""
    key = (normalized_text or "").strip()
    if not key:
        return None
    init_data_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT normalized_text, source, response_json, fetched_at FROM branded_lookup_cache WHERE normalized_text = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["response_json"] = _json_loads_or_none(result.get("response_json"))
    return result


def save_branded_lookup_cache(normalized_text: str, source: str, response: dict) -> None:
    """Persist a source response for cache-first meal lookup."""
    key = (normalized_text or "").strip()
    if not key or not isinstance(response, dict):
        return
    init_data_db()
    now_iso = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        conn.execute(
            """
            INSERT INTO branded_lookup_cache (normalized_text, source, response_json, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(normalized_text) DO UPDATE SET
                source = excluded.source,
                response_json = excluded.response_json,
                fetched_at = excluded.fetched_at
            """,
            (key, source, _json_dumps_or_none(response), now_iso),
        )
        conn.commit()


def get_personal_vocab_entry(user_id: int, normalized_input: str) -> Optional[dict]:
    key = (normalized_input or "").strip()
    if not key:
        return None
    init_data_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM personal_vocab WHERE user_id = ? AND normalized_input = ?",
            (user_id, key),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["canonical_resolution"] = _json_loads_or_none(result.get("canonical_resolution"))
    return result


def list_personal_vocab_entries(user_id: int) -> list[dict]:
    init_data_db()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM personal_vocab WHERE user_id = ? ORDER BY last_used DESC, phrase ASC",
            (user_id,),
        ).fetchall()
    entries = []
    for row in rows:
        item = dict(row)
        item["canonical_resolution"] = _json_loads_or_none(item.get("canonical_resolution"))
        entries.append(item)
    return entries


def upsert_personal_vocab_entry(
    user_id: int,
    *,
    normalized_input: str,
    phrase: str,
    canonical_resolution: dict,
    accepted: bool,
) -> dict:
    key = (normalized_input or "").strip()
    if not key or not isinstance(canonical_resolution, dict):
        raise ValueError("normalized_input and canonical_resolution are required")
    init_data_db()
    now_iso = datetime.now().isoformat(timespec="seconds")
    accept_delta = 1 if accepted else 0
    correct_delta = 0 if accepted else 1
    with _get_db() as conn:
        row = conn.execute(
            """
            INSERT INTO personal_vocab (
                user_id, normalized_input, phrase, canonical_resolution,
                accept_count, correct_count, confidence_boost, last_used, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, normalized_input) DO UPDATE SET
                phrase = excluded.phrase,
                canonical_resolution = CASE
                    WHEN excluded.accept_count > 0 THEN excluded.canonical_resolution
                    ELSE personal_vocab.canonical_resolution
                END,
                accept_count = CASE
                    WHEN excluded.accept_count > 0 THEN personal_vocab.accept_count + 1
                    ELSE 0
                END,
                correct_count = CASE
                    WHEN excluded.accept_count > 0 THEN 0
                    ELSE personal_vocab.correct_count + excluded.correct_count
                END,
                confidence_boost = CASE
                    WHEN excluded.accept_count > 0 THEN MIN(0.2, (personal_vocab.accept_count + 1) * 0.03)
                    ELSE 0
                END,
                last_used = excluded.last_used,
                updated_at = excluded.updated_at
            RETURNING *
            """,
            (
                user_id,
                key,
                phrase.strip()[:500],
                _json_dumps_or_none(canonical_resolution),
                accept_delta,
                correct_delta,
                min(0.2, accept_delta * 0.03),
                now_iso,
                now_iso,
                now_iso,
            ),
        ).fetchone()
        conn.commit()
    result = dict(row)
    result["canonical_resolution"] = _json_loads_or_none(result.get("canonical_resolution"))
    return result


def clear_food_logs(user_id: int) -> None:
    """Delete accepted food logs for a user before a full backup restore."""
    with _get_db() as conn:
        conn.execute("DELETE FROM food_logs WHERE user_id = ?", (user_id,))
        conn.commit()


def delete_food_log_by_client_id(user_id: int, client_id: str) -> bool:
    """Delete a single food log by user-scoped client_id. Returns True if removed."""
    if not client_id:
        return False
    with _get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM food_logs WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def add_food_log(user_id: int, record: dict) -> dict:
    """Persist one accepted food entry with sanitized estimate/correction metadata."""
    now_iso = datetime.now().isoformat(timespec="seconds")
    logged_at = record.get("logged_at") or record.get("timestamp") or now_iso
    date_s = record.get("date") or str(logged_at)[:10]
    original_estimate = sanitize_food_estimate(record.get("original_estimate") or record.get("estimate"))
    entry = {
        "client_id": record.get("client_id") or None,
        "date": date_s,
        "logged_at": logged_at,
        "source_timestamp": record.get("source_timestamp") or logged_at,
        "meal_type": record.get("meal_type"),
        "item_name": record.get("item_name"),
        "portion_description": record.get("portion_description"),
        "context_note": record.get("context_note") or record.get("notes"),
        "calories": record.get("calories"),
        "protein_g": record.get("protein_g"),
        "carbs_g": record.get("carbs_g"),
        "fat_g": record.get("fat_g"),
        "sodium_mg": record.get("sodium_mg"),
        "fiber_g": record.get("fiber_g"),
        "confidence": record.get("confidence"),
        "source": record.get("source") or ("vision_estimate" if original_estimate else "manual"),
        "correction_state": record.get("correction_state") or ("accepted" if original_estimate else "manual"),
        "original_estimate_json": _json_dumps_or_none(original_estimate),
        "created_at": record.get("created_at") or now_iso,
        "updated_at": now_iso,
    }
    with _get_db() as conn:
        cols = ["user_id"] + list(entry.keys())
        vals = [user_id] + [entry[c] for c in entry]
        placeholders = ", ".join(["?"] * len(cols))
        update_cols = [c for c in entry if c not in {"client_id", "created_at"}]
        assignments = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
        row = conn.execute(
            f"""
            INSERT INTO food_logs ({', '.join(cols)}) VALUES ({placeholders})
            ON CONFLICT(user_id, client_id) DO UPDATE SET {assignments}
            RETURNING *
            """,
            vals,
        ).fetchone()
        conn.commit()
    return _food_log_row_to_dict(row) if row else {k: v for k, v in entry.items() if not k.endswith("_json")}


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
    tables = ["body_data", "cardio_data", "nutrition_data", "food_logs", "recovery_data", "user_settings"]
    with _get_db() as conn:
        for table in tables:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.commit()


def get_user_data_summary(user_id: int) -> dict:
    """Return record counts per table for a user (useful for admin/debugging)."""
    tables = ["body_data", "cardio_data", "nutrition_data", "food_logs", "recovery_data"]
    summary = {}
    with _get_db() as conn:
        for table in tables:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            summary[table] = count
    return summary
