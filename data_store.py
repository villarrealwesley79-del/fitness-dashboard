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
import math
import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional
import uuid
from runtime_config import data_path

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DB = data_path("fitness_data.db")

REFRESH_CALORIE_DELTA_THRESHOLD = 1
REFRESH_MACRO_DELTA_THRESHOLD = 0.5
REFRESH_SODIUM_DELTA_THRESHOLD = 1
_REFRESH_EVENT_FIELDS = (
    ("calories", REFRESH_CALORIE_DELTA_THRESHOLD),
    ("protein_g", REFRESH_MACRO_DELTA_THRESHOLD),
    ("carbs_g", REFRESH_MACRO_DELTA_THRESHOLD),
    ("fat_g", REFRESH_MACRO_DELTA_THRESHOLD),
    ("sodium_mg", REFRESH_SODIUM_DELTA_THRESHOLD),
)


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
    "underlying_sources",
    "off_attribution",
    "from_image",
    "personal_vocab_phrase",
    "vision_description",
    "vision_provider",
    "vision_confidence",
    "_imported_pending_untrusted",
}
_ACCEPTED_NUTRITION_SOURCES = {"nutritionix", "usda_fdc", "open_food_facts", "heb_product_page"}
_ACCEPTED_NUTRITION_WRAPPERS = {
    "vision_claude",
    "vision_lm_studio",
    "vision_ollama",
    "local_cache",
}
_CURRENT_ESTIMATE_PROJECTION_FIELDS = (
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
    "source",
)


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
    safe = {k: estimate.get(k) for k in FOOD_ESTIMATE_FIELDS if k in estimate and k != "off_attribution"}
    off_attribution = estimate.get("off_attribution")
    if isinstance(off_attribution, str):
        cleaned_text = off_attribution.strip()
        if cleaned_text:
            safe["off_attribution"] = cleaned_text
    elif isinstance(off_attribution, dict):
        cleaned = {
            key: value
            for key, value in off_attribution.items()
            if isinstance(key, str) and (value is None or isinstance(value, (str, int, float, bool)))
        }
        if cleaned:
            safe["off_attribution"] = cleaned
    if "from_image" in safe and not isinstance(safe["from_image"], bool):
        safe.pop("from_image", None)
    if "_imported_pending_untrusted" in safe and safe["_imported_pending_untrusted"] is not True:
        safe.pop("_imported_pending_untrusted", None)
    return safe or None


def _has_accepted_mixed_provenance(estimate: Optional[dict]) -> bool:
    if not isinstance(estimate, dict):
        return False
    source = str(estimate.get("source") or "").strip().lower()
    underlying_source = str(estimate.get("underlying_source") or "").strip().lower()
    provenance_parts = _provenance_components(source, underlying_source)
    allowed_components = _ACCEPTED_NUTRITION_SOURCES | _ACCEPTED_NUTRITION_WRAPPERS | {"mixed_lookup"}
    if not provenance_parts or not provenance_parts.issubset(allowed_components):
        return False
    is_mixed = "mixed_lookup" in provenance_parts
    sources = estimate.get("underlying_sources")
    return (
        is_mixed
        and not bool(estimate.get("ambiguous"))
        and isinstance(sources, list)
        and bool(sources)
        and _has_valid_declared_underlying_sources(estimate)
    )


def _provenance_components(source: str, underlying_source: str) -> set[str]:
    return {
        part.strip()
        for value in (source, underlying_source)
        for part in value.split("+")
        if part.strip()
    }


def _has_valid_declared_underlying_sources(estimate: Optional[dict]) -> bool:
    if not isinstance(estimate, dict):
        return False
    sources = estimate.get("underlying_sources")
    if sources is None:
        return True
    return (
        isinstance(sources, list)
        and bool(sources)
        and all(
            isinstance(value, str)
            and value.strip().lower() in _ACCEPTED_NUTRITION_SOURCES
            for value in sources
        )
    )


def sanitize_accepted_estimate(estimate: Optional[dict]) -> Optional[dict]:
    safe = sanitize_food_estimate(estimate)
    if not safe:
        return safe
    sources = estimate.get("underlying_sources") if isinstance(estimate, dict) else None
    if _has_accepted_mixed_provenance(estimate):
        safe["underlying_sources"] = [value.strip().lower() for value in sources]
    else:
        safe.pop("underlying_sources", None)
    return safe


def _is_authorized_accepted_estimate_replacement(estimate: Optional[dict]) -> bool:
    safe = sanitize_accepted_estimate(estimate)
    if not isinstance(safe, dict):
        return False
    if bool(safe.get("ambiguous")):
        return False
    if not _has_valid_declared_underlying_sources(estimate):
        return False
    for field in ("calories", "protein_g", "carbs_g", "fat_g"):
        value = safe.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            return False
    for field in ("sodium_mg", "fiber_g"):
        value = safe.get(field)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            return False
    confidence = safe.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        return False
    source = str(safe.get("source") or "").strip().lower()
    underlying_source = str(safe.get("underlying_source") or "").strip().lower()
    provenance_parts = _provenance_components(source, underlying_source)
    allowed_components = _ACCEPTED_NUTRITION_SOURCES | _ACCEPTED_NUTRITION_WRAPPERS | {"mixed_lookup"}
    if not provenance_parts or not provenance_parts.issubset(allowed_components):
        return False
    if "mixed_lookup" in provenance_parts:
        return _has_accepted_mixed_provenance(estimate)
    providers = provenance_parts & _ACCEPTED_NUTRITION_SOURCES
    if len(providers) != 1:
        return False
    declared_sources = estimate.get("underlying_sources") if isinstance(estimate, dict) else None
    if declared_sources is not None:
        return {
            value.strip().lower()
            for value in declared_sources
        } == providers
    return True


def _project_accepted_estimate_onto_entry(entry: dict, accepted_estimate: Optional[dict]) -> None:
    if not isinstance(accepted_estimate, dict):
        return
    for field in _CURRENT_ESTIMATE_PROJECTION_FIELDS:
        if field in accepted_estimate:
            entry[field] = accepted_estimate[field]


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
                accepted_estimate_json TEXT,
                meal_id                TEXT,
                meal_item_id           TEXT,
                item_index             INTEGER,
                item_state             TEXT,
                vocab_learned_at       TEXT,
                created_at             TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at             TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, client_id)
            );

            CREATE TABLE IF NOT EXISTS food_log_refresh_events (
                id             TEXT PRIMARY KEY,
                user_id        INTEGER NOT NULL,
                client_id      TEXT    NOT NULL,
                date           TEXT    NOT NULL,
                item_name      TEXT,
                source         TEXT,
                source_url     TEXT,
                prev_calories  INTEGER,
                new_calories   INTEGER,
                prev_protein_g REAL,
                new_protein_g  REAL,
                prev_carbs_g   REAL,
                new_carbs_g    REAL,
                prev_fat_g     REAL,
                new_fat_g      REAL,
                prev_sodium_mg INTEGER,
                new_sodium_mg  INTEGER,
                refreshed_at   TEXT    NOT NULL,
                acknowledged_at TEXT,
                created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS workout_adaptation_pending (
                id                  TEXT PRIMARY KEY,
                user_id             INTEGER NOT NULL,
                date                TEXT    NOT NULL,
                window_started_at   TEXT    NOT NULL,
                window_closes_at    TEXT    NOT NULL,
                meal_ids_json       TEXT,
                food_log_client_ids_json TEXT,
                status              TEXT    NOT NULL DEFAULT 'pending',
                processed_event_id  TEXT,
                created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS workout_adaptation_events (
                id                  TEXT PRIMARY KEY,
                user_id             INTEGER NOT NULL,
                date                TEXT    NOT NULL,
                status              TEXT    NOT NULL,
                silent              INTEGER NOT NULL DEFAULT 1,
                change_type         TEXT    NOT NULL,
                applies_to          TEXT    NOT NULL,
                reason              TEXT,
                confidence_json     TEXT,
                trigger_json        TEXT,
                nutrition_context_json TEXT,
                patch_json          TEXT,
                before_plan_json    TEXT,
                after_plan_json     TEXT,
                active_workout_json TEXT,
                reason_metadata_json TEXT,
                created_at          TEXT    NOT NULL,
                acknowledged_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS current_workout_plans (
                user_id       INTEGER PRIMARY KEY,
                fingerprint   TEXT    NOT NULL,
                plan_json     TEXT    NOT NULL,
                updated_at    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS branded_lookup_cache (
                user_id         INTEGER NOT NULL DEFAULT 1,
                normalized_text TEXT NOT NULL,
                source          TEXT NOT NULL,
                source_tier     TEXT NOT NULL,
                response_json   TEXT NOT NULL,
                fetched_at      TEXT NOT NULL,
                PRIMARY KEY(user_id, normalized_text)
            );

            CREATE TABLE IF NOT EXISTS barcode_lookup_cache (
                user_id       INTEGER NOT NULL DEFAULT 1,
                barcode       TEXT NOT NULL,
                source        TEXT NOT NULL,
                source_tier   TEXT NOT NULL,
                response_json TEXT NOT NULL,
                fetched_at    TEXT NOT NULL,
                PRIMARY KEY(user_id, barcode)
            );

            CREATE TABLE IF NOT EXISTS personal_vocab (
                user_id              INTEGER NOT NULL,
                normalized_input     TEXT    NOT NULL,
                phrase               TEXT    NOT NULL,
                canonical_resolution TEXT    NOT NULL,
                accept_count         INTEGER NOT NULL DEFAULT 0,
                correct_count        INTEGER NOT NULL DEFAULT 0,
                skip_count           INTEGER NOT NULL DEFAULT 0,
                deleted_count        INTEGER NOT NULL DEFAULT 0,
                confidence_boost     REAL    NOT NULL DEFAULT 0,
                last_used            TEXT    NOT NULL,
                last_negative_feedback_at TEXT,
                created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at           TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(user_id, normalized_input)
            );

            CREATE TABLE IF NOT EXISTS meal_acceptance_events (
                user_id                  INTEGER NOT NULL,
                meal_id                  TEXT    NOT NULL,
                status                   TEXT    NOT NULL,
                included_client_ids_json TEXT    NOT NULL,
                feedback_fingerprint     TEXT,
                skipped_count            INTEGER NOT NULL DEFAULT 0,
                deleted_count            INTEGER NOT NULL DEFAULT 0,
                created_at               TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at               TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(user_id, meal_id)
            );

            CREATE TABLE IF NOT EXISTS meal_review_snapshots (
                user_id                 INTEGER NOT NULL,
                meal_id                 TEXT    NOT NULL,
                payload_json            TEXT    NOT NULL,
                next_item_seq           INTEGER NOT NULL DEFAULT 1,
                applied_refreshes_json  TEXT    NOT NULL DEFAULT '{}',
                created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(user_id, meal_id)
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
            "accepted_estimate_json": "TEXT",
            "meal_id": "TEXT",
            "meal_item_id": "TEXT",
            "item_index": "INTEGER",
            "item_state": "TEXT",
            "vocab_learned_at": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        for col, col_type in food_log_columns.items():
            if col not in existing_food_log_cols:
                conn.execute(f"ALTER TABLE food_logs ADD COLUMN {col} {col_type}")
        existing_vocab_cols = {r["name"] for r in conn.execute("PRAGMA table_info(personal_vocab)").fetchall()}
        personal_vocab_columns = {
            "skip_count": "INTEGER NOT NULL DEFAULT 0",
            "deleted_count": "INTEGER NOT NULL DEFAULT 0",
            "last_negative_feedback_at": "TEXT",
        }
        for col, col_type in personal_vocab_columns.items():
            if col not in existing_vocab_cols:
                conn.execute(f"ALTER TABLE personal_vocab ADD COLUMN {col} {col_type}")
        existing_meal_event_cols = {r["name"] for r in conn.execute("PRAGMA table_info(meal_acceptance_events)").fetchall()}
        if "feedback_fingerprint" not in existing_meal_event_cols:
            conn.execute("ALTER TABLE meal_acceptance_events ADD COLUMN feedback_fingerprint TEXT")
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
        existing_cache_cols = {r["name"] for r in conn.execute("PRAGMA table_info(branded_lookup_cache)").fetchall()}
        if "user_id" not in existing_cache_cols:
            conn.execute("ALTER TABLE branded_lookup_cache RENAME TO branded_lookup_cache_old")
            conn.execute("""
                CREATE TABLE branded_lookup_cache (
                    user_id         INTEGER NOT NULL DEFAULT 1,
                    normalized_text TEXT NOT NULL,
                    source          TEXT NOT NULL,
                    source_tier     TEXT NOT NULL,
                    response_json   TEXT NOT NULL,
                    fetched_at      TEXT NOT NULL,
                    PRIMARY KEY(user_id, normalized_text)
                )
            """)
            conn.execute("""
                INSERT INTO branded_lookup_cache (user_id, normalized_text, source, source_tier, response_json, fetched_at)
                SELECT 1, normalized_text, source, source, response_json, fetched_at
                  FROM branded_lookup_cache_old
            """)
            conn.execute("DROP TABLE branded_lookup_cache_old")
        else:
            if "source_tier" not in existing_cache_cols:
                conn.execute("ALTER TABLE branded_lookup_cache ADD COLUMN source_tier TEXT")
                conn.execute("UPDATE branded_lookup_cache SET source_tier = source WHERE source_tier IS NULL")
        existing_barcode_cache_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(barcode_lookup_cache)").fetchall()
        }
        if "source_tier" not in existing_barcode_cache_cols:
            conn.execute("ALTER TABLE barcode_lookup_cache ADD COLUMN source_tier TEXT")
            conn.execute("UPDATE barcode_lookup_cache SET source_tier = source WHERE source_tier IS NULL")
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_food_logs_user_meal_id "
            "ON food_logs(user_id, meal_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_food_log_refresh_events_user_ack "
            "ON food_log_refresh_events(user_id, acknowledged_at, refreshed_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_food_log_refresh_events_user_client "
            "ON food_log_refresh_events(user_id, client_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_workout_adaptation_pending_user_status "
            "ON workout_adaptation_pending(user_id, status, window_closes_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_workout_adaptation_events_user_ack "
            "ON workout_adaptation_events(user_id, acknowledged_at, created_at)"
        )
        conn.commit()


def save_current_workout_plan(user_id: int, fingerprint: str, plan: dict) -> dict:
    """Persist the current generated workout plan for one user.

    KNOWN LIMITATION (FIT-256 finding 3): this is a blind
    ``INSERT ... ON CONFLICT(user_id) DO UPDATE`` -- last write wins, with no
    version/optimistic-lock check. `app._persist_current_workout_plan` guards
    this with a process-local `threading.RLock`, which only serializes writes
    within a single worker process; it gives no cross-process protection.
    The app currently mitigates this by running gunicorn with a single worker
    (see Dockerfile), which makes the process-local lock effectively global.
    If this ever needs to scale to >1 worker/instance again, this function
    needs a real compare-and-set (e.g. a `version` column, update only when
    `version = expected_version`, caller reconciles on conflict) before that
    change is safe.
    """
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    if not fingerprint:
        raise ValueError("fingerprint is required")
    now = datetime.now().isoformat(timespec="seconds")
    plan_json = json.dumps(plan, sort_keys=True, default=str)
    with _get_db() as conn:
        conn.execute(
            """
            INSERT INTO current_workout_plans (user_id, fingerprint, plan_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                fingerprint=excluded.fingerprint,
                plan_json=excluded.plan_json,
                updated_at=excluded.updated_at
            """,
            (user_id, fingerprint, plan_json, now),
        )
        conn.commit()
    return {"user_id": user_id, "fingerprint": fingerprint, "plan": json.loads(plan_json), "updated_at": now}


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
        "updated_at": row["updated_at"],
    }


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
    d["accepted_estimate"] = _json_loads_or_none(d.pop("accepted_estimate_json", None))
    from_image = d.get("from_image")
    accepted_from_image = (
        d["accepted_estimate"].get("from_image")
        if isinstance(d["accepted_estimate"], dict)
        else None
    )
    original_from_image = (
        d["original_estimate"].get("from_image")
        if isinstance(d["original_estimate"], dict)
        else None
    )
    if (
        accepted_from_image is True
        or original_from_image is True
    ):
        from_image = True
    elif (
        from_image is not True
        and (accepted_from_image is False or original_from_image is False)
    ):
        from_image = False
    d["from_image"] = from_image
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


def _numeric_delta_reaches_threshold(before: object, after: object, threshold: float) -> bool:
    if before in (None, "") and after in (None, ""):
        return False
    if before in (None, "") or after in (None, ""):
        return True
    try:
        return abs(float(after) - float(before)) >= threshold
    except (TypeError, ValueError):
        return str(before) != str(after)


def _food_log_refresh_materially_changed(before: dict | None, after: dict | None) -> bool:
    if not before or not after:
        return False
    return any(
        _numeric_delta_reaches_threshold(before.get(field), after.get(field), threshold)
        for field, threshold in _REFRESH_EVENT_FIELDS
    )


def _refresh_event_metadata(record: dict) -> dict | None:
    raw = (
        record.get("refresh_event")
        or record.get("source_refresh_event")
        or record.get("verified_refresh_event")
    )
    if raw is True:
        return {}
    return raw if isinstance(raw, dict) else None


def _insert_food_log_refresh_event(
    conn: sqlite3.Connection,
    user_id: int,
    before: dict,
    after: dict,
    metadata: dict,
    now_iso: str,
) -> dict | None:
    if not _food_log_refresh_materially_changed(before, after):
        return None
    event_id = str(uuid.uuid4())
    source = (
        metadata.get("source")
        or metadata.get("source_label")
        or metadata.get("underlying_source")
        or after.get("source")
    )
    source_url = metadata.get("source_url") or metadata.get("verified_source_url")
    refreshed_at = metadata.get("refreshed_at") or metadata.get("data_fetched_at") or now_iso
    item_name = metadata.get("item_name") or after.get("item_name") or after.get("portion_description")
    payload = {
        "id": event_id,
        "user_id": user_id,
        "client_id": after.get("client_id"),
        "date": after.get("date"),
        "item_name": item_name,
        "source": source,
        "source_url": source_url,
        "prev_calories": before.get("calories"),
        "new_calories": after.get("calories"),
        "prev_protein_g": before.get("protein_g"),
        "new_protein_g": after.get("protein_g"),
        "prev_carbs_g": before.get("carbs_g"),
        "new_carbs_g": after.get("carbs_g"),
        "prev_fat_g": before.get("fat_g"),
        "new_fat_g": after.get("fat_g"),
        "prev_sodium_mg": before.get("sodium_mg"),
        "new_sodium_mg": after.get("sodium_mg"),
        "refreshed_at": refreshed_at,
    }
    cols = list(payload.keys())
    conn.execute(
        f"INSERT INTO food_log_refresh_events ({', '.join(cols)}) "
        f"VALUES ({', '.join(['?'] * len(cols))})",
        [payload[col] for col in cols],
    )
    return payload


def list_food_log_refresh_events(
    user_id: int,
    *,
    unacknowledged: bool = True,
    since: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Return bounded source-refresh events for user-facing notifications."""
    init_data_db()
    safe_limit = min(max(int(limit or 50), 1), 50)
    clauses = ["user_id = ?"]
    params: list = [user_id]
    if unacknowledged:
        clauses.append("acknowledged_at IS NULL")
    if since:
        clauses.append("refreshed_at >= ?")
        params.append(since)
    where_sql = " AND ".join(clauses)
    with _get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT *
              FROM food_log_refresh_events
             WHERE {where_sql}
             ORDER BY refreshed_at DESC, created_at DESC
             LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
    return [dict(row) for row in rows]


def acknowledge_food_log_refresh_event(user_id: int, event_id: str) -> bool:
    """Mark one refresh event acknowledged, scoped to the current user."""
    if not event_id:
        return False
    init_data_db()
    now_iso = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        cur = conn.execute(
            """
            UPDATE food_log_refresh_events
               SET acknowledged_at = COALESCE(acknowledged_at, ?)
             WHERE user_id = ?
               AND id = ?
            """,
            (now_iso, user_id, event_id),
        )
        conn.commit()
        return cur.rowcount > 0


def _workout_adaptation_pending_payload(row: sqlite3.Row | dict) -> dict:
    payload = dict(row)
    payload["meal_ids"] = _json_loads_or_none(payload.pop("meal_ids_json", None)) or []
    payload["food_log_client_ids"] = (
        _json_loads_or_none(payload.pop("food_log_client_ids_json", None)) or []
    )
    return payload


def _workout_adaptation_event_payload(row: sqlite3.Row | dict) -> dict:
    payload = dict(row)
    payload["silent"] = bool(payload.get("silent"))
    for column, key in (
        ("confidence_json", "confidence"),
        ("trigger_json", "trigger"),
        ("nutrition_context_json", "nutrition_context"),
        ("patch_json", "patch"),
        ("before_plan_json", "before_remaining_plan"),
        ("after_plan_json", "after_remaining_plan"),
        ("active_workout_json", "active_workout"),
        ("reason_metadata_json", "reason_metadata"),
    ):
        payload[key] = _json_loads_or_none(payload.pop(column, None))
    return payload


def enqueue_workout_adaptation_pending(
    user_id: int,
    *,
    date: str,
    meal_id: Optional[str],
    food_log_client_ids: list[str],
    window_started_at: str,
    window_closes_at: str,
) -> dict:
    """Create or update the pending nutrition-to-workout adaptation window."""
    init_data_db()
    meal_ids = [str(meal_id)] if meal_id else []
    client_ids = [str(item) for item in food_log_client_ids if item]
    now_iso = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        for client_id in client_ids:
            existing_trigger = conn.execute(
                """
                SELECT *
                  FROM workout_adaptation_pending
                 WHERE user_id = ?
                   AND food_log_client_ids_json LIKE ?
                 ORDER BY created_at ASC
                 LIMIT 1
                """,
                (user_id, f'%"{client_id}"%'),
            ).fetchone()
            if existing_trigger:
                return _workout_adaptation_pending_payload(existing_trigger)
        row = conn.execute(
            """
            SELECT *
              FROM workout_adaptation_pending
             WHERE user_id = ?
               AND date = ?
               AND status = 'pending'
               AND window_closes_at >= ?
             ORDER BY window_started_at ASC
             LIMIT 1
            """,
            (user_id, date, window_started_at),
        ).fetchone()
        if row:
            existing = _workout_adaptation_pending_payload(row)
            merged_meal_ids = sorted({*(existing.get("meal_ids") or []), *meal_ids})
            merged_client_ids = sorted({*(existing.get("food_log_client_ids") or []), *client_ids})
            conn.execute(
                """
                UPDATE workout_adaptation_pending
                   SET meal_ids_json = ?,
                       food_log_client_ids_json = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (
                    _json_dumps_or_none(merged_meal_ids),
                    _json_dumps_or_none(merged_client_ids),
                    now_iso,
                    existing["id"],
                ),
            )
            updated = conn.execute(
                "SELECT * FROM workout_adaptation_pending WHERE id = ?",
                (existing["id"],),
            ).fetchone()
            return _workout_adaptation_pending_payload(updated)
        pending_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO workout_adaptation_pending (
                id, user_id, date, window_started_at, window_closes_at,
                meal_ids_json, food_log_client_ids_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                pending_id,
                user_id,
                date,
                window_started_at,
                window_closes_at,
                _json_dumps_or_none(meal_ids),
                _json_dumps_or_none(client_ids),
                now_iso,
                now_iso,
            ),
        )
        inserted = conn.execute(
            "SELECT * FROM workout_adaptation_pending WHERE id = ?",
            (pending_id,),
        ).fetchone()
        return _workout_adaptation_pending_payload(inserted)


def list_due_workout_adaptation_pending(user_id: int, *, now_iso: str) -> list[dict]:
    """Return pending adaptation windows whose coalescing window has closed."""
    init_data_db()
    with _get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
              FROM workout_adaptation_pending
             WHERE user_id = ?
               AND status = 'pending'
               AND window_closes_at <= ?
             ORDER BY window_closes_at ASC, created_at ASC
            """,
            (user_id, now_iso),
        ).fetchall()
    return [_workout_adaptation_pending_payload(row) for row in rows]


def list_pending_workout_adaptation_windows(user_id: int) -> list[dict]:
    """Return open adaptation windows for tests and event polling."""
    init_data_db()
    with _get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
              FROM workout_adaptation_pending
             WHERE user_id = ?
               AND status = 'pending'
             ORDER BY window_started_at ASC
            """,
            (user_id,),
        ).fetchall()
    return [_workout_adaptation_pending_payload(row) for row in rows]


def save_workout_adaptation_event(user_id: int, pending_id: str, event: dict) -> dict | None:
    """Persist one evaluated workout adaptation audit/event row."""
    init_data_db()
    event_id = event.get("id") or str(uuid.uuid4())
    created_at = event.get("created_at") or datetime.now().isoformat(timespec="seconds")
    payload = {
        "id": event_id,
        "user_id": user_id,
        "date": event.get("date"),
        "status": event.get("status") or "no_change",
        "silent": 1 if event.get("silent", True) else 0,
        "change_type": event.get("change_type") or "none",
        "applies_to": event.get("applies_to") or "today",
        "reason": event.get("reason"),
        "confidence_json": _json_dumps_or_none(event.get("confidence")),
        "trigger_json": _json_dumps_or_none(event.get("trigger")),
        "nutrition_context_json": _json_dumps_or_none(event.get("nutrition_context")),
        "patch_json": _json_dumps_or_none(event.get("patch")),
        "before_plan_json": _json_dumps_or_none(event.get("before_remaining_plan")),
        "after_plan_json": _json_dumps_or_none(event.get("after_remaining_plan")),
        "active_workout_json": _json_dumps_or_none(event.get("active_workout")),
        "reason_metadata_json": _json_dumps_or_none(event.get("reason_metadata")),
        "created_at": created_at,
    }
    cols = list(payload.keys())
    with _get_db() as conn:
        claim = conn.execute(
            """
            UPDATE workout_adaptation_pending
               SET status = 'processing',
                   updated_at = ?
             WHERE user_id = ?
               AND id = ?
               AND status = 'pending'
            """,
            (created_at, user_id, pending_id),
        )
        if claim.rowcount == 0:
            pending = conn.execute(
                """
                SELECT processed_event_id
                  FROM workout_adaptation_pending
                 WHERE user_id = ?
                   AND id = ?
                """,
                (user_id, pending_id),
            ).fetchone()
            processed_event_id = pending["processed_event_id"] if pending else None
            if processed_event_id:
                existing = conn.execute(
                    "SELECT * FROM workout_adaptation_events WHERE id = ?",
                    (processed_event_id,),
                ).fetchone()
                return _workout_adaptation_event_payload(existing) if existing else None
            return None
        conn.execute(
            f"INSERT INTO workout_adaptation_events ({', '.join(cols)}) "
            f"VALUES ({', '.join(['?'] * len(cols))})",
            [payload[col] for col in cols],
        )
        conn.execute(
            """
            UPDATE workout_adaptation_pending
               SET status = 'processed',
                   processed_event_id = ?,
                   updated_at = ?
             WHERE user_id = ?
               AND id = ?
               AND status = 'processing'
            """,
            (event_id, created_at, user_id, pending_id),
        )
        row = conn.execute(
            "SELECT * FROM workout_adaptation_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    return _workout_adaptation_event_payload(row)


def list_workout_adaptation_events(
    user_id: int,
    *,
    unacknowledged: bool = True,
    since: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Return bounded workout adaptation events for the FIT-137 UI contract."""
    init_data_db()
    safe_limit = min(max(int(limit or 50), 1), 50)
    clauses = ["user_id = ?"]
    params: list = [user_id]
    if unacknowledged:
        clauses.append("acknowledged_at IS NULL")
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    where_sql = " AND ".join(clauses)
    with _get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT *
              FROM workout_adaptation_events
             WHERE {where_sql}
             ORDER BY created_at DESC
             LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
    return [_workout_adaptation_event_payload(row) for row in rows]


def acknowledge_workout_adaptation_event(user_id: int, event_id: str) -> bool:
    """Mark one workout adaptation event acknowledged, scoped to the current user."""
    if not event_id:
        return False
    init_data_db()
    now_iso = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        cur = conn.execute(
            """
            UPDATE workout_adaptation_events
               SET acknowledged_at = COALESCE(acknowledged_at, ?)
             WHERE user_id = ?
               AND id = ?
            """,
            (now_iso, user_id, event_id),
        )
        conn.commit()
        return cur.rowcount > 0


def get_branded_lookup_cache(normalized_text: str, *, user_id: int = 1) -> Optional[dict]:
    """Return a cached branded-food lookup payload by normalized text."""
    key = (normalized_text or "").strip()
    if not key:
        return None
    init_data_db()
    with _get_db() as conn:
        row = conn.execute(
            """
            SELECT user_id, normalized_text, source, source_tier, response_json, fetched_at
              FROM branded_lookup_cache
             WHERE user_id = ? AND normalized_text = ?
            """,
            (user_id, key),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["response_json"] = _json_loads_or_none(result.get("response_json"))
    return result


def save_branded_lookup_cache(
    normalized_text: str,
    source: str,
    response: dict,
    *,
    user_id: int = 1,
    source_tier: str | None = None,
) -> None:
    """Persist a source response for cache-first meal lookup."""
    key = (normalized_text or "").strip()
    if not key or not isinstance(response, dict):
        return
    tier = (source_tier or source or "").strip()
    init_data_db()
    now_iso = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        conn.execute(
            """
            INSERT INTO branded_lookup_cache (user_id, normalized_text, source, source_tier, response_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, normalized_text) DO UPDATE SET
                source = excluded.source,
                source_tier = excluded.source_tier,
                response_json = excluded.response_json,
                fetched_at = excluded.fetched_at
            """,
            (user_id, key, source, tier, _json_dumps_or_none(response), now_iso),
        )
        conn.commit()


def get_barcode_lookup_cache(barcode: str, *, user_id: int = 1) -> Optional[dict]:
    """Return a cached barcode-food lookup payload by barcode."""
    key = (barcode or "").strip()
    if not key:
        return None
    init_data_db()
    with _get_db() as conn:
        row = conn.execute(
            """
            SELECT user_id, barcode, source, source_tier, response_json, fetched_at
              FROM barcode_lookup_cache
             WHERE user_id = ? AND barcode = ?
            """,
            (user_id, key),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["response_json"] = _json_loads_or_none(result.get("response_json"))
    return result


def save_barcode_lookup_cache(
    barcode: str,
    source: str,
    response: dict,
    *,
    user_id: int = 1,
    source_tier: str | None = None,
) -> None:
    """Persist a verified barcode lookup response for cache-first reuse."""
    key = (barcode or "").strip()
    if not key or not isinstance(response, dict):
        return
    tier = (source_tier or source or "").strip()
    init_data_db()
    now_iso = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        conn.execute(
            """
            INSERT INTO barcode_lookup_cache (user_id, barcode, source, source_tier, response_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, barcode) DO UPDATE SET
                source = excluded.source,
                source_tier = excluded.source_tier,
                response_json = excluded.response_json,
                fetched_at = excluded.fetched_at
            """,
            (user_id, key, source, tier, _json_dumps_or_none(response), now_iso),
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


def get_meal_acceptance_event(user_id: int, meal_id: str) -> Optional[dict]:
    key = (meal_id or "").strip()
    if not key:
        return None
    init_data_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM meal_acceptance_events WHERE user_id = ? AND meal_id = ?",
            (user_id, key),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["included_client_ids"] = _json_loads_or_none(result.pop("included_client_ids_json", None)) or []
    return result


def delete_meal_acceptance_event(user_id: int, meal_id: str) -> bool:
    key = (meal_id or "").strip()
    if not key:
        return False
    init_data_db()
    with _get_db() as conn:
        cur = conn.execute(
            "DELETE FROM meal_acceptance_events WHERE user_id = ? AND meal_id = ?",
            (user_id, key),
        )
        conn.commit()
    return cur.rowcount > 0


def save_meal_acceptance_event(
    user_id: int,
    *,
    meal_id: str,
    status: str,
    included_client_ids: list[str],
    skipped_count: int,
    deleted_count: int,
    feedback_fingerprint: str | None = None,
) -> dict:
    key = (meal_id or "").strip()
    if not key:
        raise ValueError("meal_id is required")
    safe_ids = sorted(str(client_id) for client_id in included_client_ids if client_id)
    now_iso = datetime.now().isoformat(timespec="seconds")
    init_data_db()
    with _get_db() as conn:
        row = conn.execute(
            """
            INSERT INTO meal_acceptance_events (
                user_id, meal_id, status, included_client_ids_json,
                feedback_fingerprint, skipped_count, deleted_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, meal_id) DO UPDATE SET
                status = excluded.status,
                included_client_ids_json = excluded.included_client_ids_json,
                feedback_fingerprint = excluded.feedback_fingerprint,
                skipped_count = excluded.skipped_count,
                deleted_count = excluded.deleted_count,
                updated_at = excluded.updated_at
            RETURNING *
            """,
            (
                user_id,
                key,
                status,
                _json_dumps_or_none(safe_ids) or "[]",
                (feedback_fingerprint or None),
                max(0, int(skipped_count or 0)),
                max(0, int(deleted_count or 0)),
                now_iso,
                now_iso,
            ),
        ).fetchone()
        conn.commit()
    result = dict(row)
    result["included_client_ids"] = _json_loads_or_none(result.pop("included_client_ids_json", None)) or []
    return result


def list_meal_acceptance_events(user_id: int) -> list[dict]:
    init_data_db()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM meal_acceptance_events WHERE user_id = ? ORDER BY updated_at DESC, meal_id ASC",
            (user_id,),
        ).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        event["included_client_ids"] = _json_loads_or_none(event.pop("included_client_ids_json", None)) or []
        events.append(event)
    return events


def import_meal_acceptance_event(user_id: int, event: dict) -> dict | None:
    if not isinstance(event, dict):
        return None
    meal_id = (event.get("meal_id") or "").strip()
    if not meal_id:
        return None
    return save_meal_acceptance_event(
        user_id,
        meal_id=meal_id,
        status=(event.get("status") or "logged"),
        included_client_ids=event.get("included_client_ids") or [],
        skipped_count=event.get("skipped_count") or 0,
        deleted_count=event.get("deleted_count") or 0,
        feedback_fingerprint=event.get("feedback_fingerprint"),
    )


def get_meal_review_snapshot(user_id: int, meal_id: str) -> Optional[dict]:
    key = (meal_id or "").strip()
    if not key:
        return None
    init_data_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM meal_review_snapshots WHERE user_id = ? AND meal_id = ?",
            (user_id, key),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["payload"] = _json_loads_or_none(result.pop("payload_json", None)) or {}
    result["applied_refreshes"] = _json_loads_or_none(result.pop("applied_refreshes_json", None)) or {}
    return result


def save_meal_review_snapshot(
    user_id: int,
    *,
    meal_id: str,
    payload: dict,
    next_item_seq: int,
    applied_refreshes: dict | None = None,
) -> dict:
    key = (meal_id or "").strip()
    if not key:
        raise ValueError("meal_id is required")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    now_iso = datetime.now().isoformat(timespec="seconds")
    init_data_db()
    with _get_db() as conn:
        if payload.get("status") == "pending_review":
            terminal_food_log = conn.execute(
                """
                SELECT 1 FROM food_logs
                WHERE user_id = ? AND (meal_id = ? OR client_id = ?)
                  AND correction_state IN ('accepted', 'corrected')
                LIMIT 1
                """,
                (user_id, key, key),
            ).fetchone()
            terminal_event = conn.execute(
                """
                SELECT 1 FROM meal_acceptance_events
                WHERE user_id = ? AND meal_id = ?
                LIMIT 1
                """,
                (user_id, key),
            ).fetchone()
            if terminal_food_log or terminal_event:
                row = conn.execute(
                    """
                    SELECT * FROM meal_review_snapshots
                    WHERE user_id = ? AND meal_id = ?
                    """,
                    (user_id, key),
                ).fetchone()
                if row is None:
                    return {
                        "user_id": user_id,
                        "meal_id": key,
                        "payload": payload,
                        "next_item_seq": max(1, int(next_item_seq or 1)),
                        "applied_refreshes": applied_refreshes or {},
                        "created_at": now_iso,
                        "updated_at": now_iso,
                    }
            else:
                row = None
        else:
            row = None
        if row is None:
            row = conn.execute(
                """
                INSERT INTO meal_review_snapshots (
                    user_id, meal_id, payload_json, next_item_seq,
                    applied_refreshes_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, meal_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    next_item_seq = excluded.next_item_seq,
                    applied_refreshes_json = excluded.applied_refreshes_json,
                    updated_at = excluded.updated_at
                RETURNING *
                """,
                (
                    user_id,
                    key,
                    _json_dumps_or_none(payload) or "{}",
                    max(1, int(next_item_seq or 1)),
                    _json_dumps_or_none(applied_refreshes or {}) or "{}",
                    now_iso,
                    now_iso,
                ),
            ).fetchone()
        conn.commit()
    result = dict(row)
    result["payload"] = _json_loads_or_none(result.pop("payload_json", None)) or {}
    result["applied_refreshes"] = _json_loads_or_none(result.pop("applied_refreshes_json", None)) or {}
    return result


def delete_meal_review_snapshot(user_id: int, meal_id: str) -> bool:
    key = (meal_id or "").strip()
    if not key:
        return False
    init_data_db()
    with _get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM meal_review_snapshots WHERE user_id = ? AND meal_id = ?",
            (user_id, key),
        )
        conn.commit()
        return cursor.rowcount > 0


def list_meal_review_snapshots(user_id: int) -> list[dict]:
    init_data_db()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM meal_review_snapshots WHERE user_id = ? ORDER BY updated_at DESC, meal_id ASC",
            (user_id,),
        ).fetchall()
    snapshots = []
    for row in rows:
        snapshot = dict(row)
        snapshot["payload"] = _json_loads_or_none(snapshot.pop("payload_json", None)) or {}
        snapshot["applied_refreshes"] = _json_loads_or_none(snapshot.pop("applied_refreshes_json", None)) or {}
        snapshots.append(snapshot)
    return snapshots


def import_meal_review_snapshot(user_id: int, snapshot: dict) -> dict | None:
    if not isinstance(snapshot, dict):
        return None
    meal_id = (snapshot.get("meal_id") or "").strip()
    payload = snapshot.get("payload")
    if not meal_id or not isinstance(payload, dict):
        return None
    imported_payload = {
        key: value
        for key, value in payload.items()
        if key not in {
            "server_source_backed_candidate",
            "_imported_candidates_untrusted",
            "_imported_snapshot_untrusted",
            "_material_correction_from_submission",
        }
    }
    imported_payload["_imported_snapshot_untrusted"] = True
    imported_items = payload.get("items")
    if isinstance(imported_items, list):
        imported_payload["items"] = []
        used_item_ids = set()
        for position, item in enumerate(imported_items, start=1):
            if not isinstance(item, dict):
                continue
            imported_item = {
                key: value
                for key, value in item.items()
                if key not in {
                    "server_source_backed_candidate",
                    "_imported_candidates_untrusted",
                    "_imported_snapshot_untrusted",
                    "_material_correction_from_submission",
                }
            }
            item_id = str(imported_item.get("item_id") or "").strip()
            if not item_id or item_id in used_item_ids:
                item_id = f"item-{position}"
            used_item_ids.add(item_id)
            imported_item["item_id"] = item_id
            try:
                item_order = int(imported_item.get("item_order"))
            except (TypeError, ValueError):
                item_order = position
            imported_item["item_order"] = item_order if item_order > 0 else position
            candidates = item.get("candidates")
            if isinstance(candidates, list):
                imported_item["candidates"] = [
                    {
                        key: value
                        for key, value in candidate.items()
                        if key != "source_backed"
                    }
                    if isinstance(candidate, dict)
                    else candidate
                    for candidate in candidates
                ]
            imported_item["_imported_candidates_untrusted"] = bool(candidates)
            imported_item["_imported_snapshot_untrusted"] = True
            imported_payload["items"].append(imported_item)
    return save_meal_review_snapshot(
        user_id,
        meal_id=meal_id,
        payload=imported_payload,
        next_item_seq=snapshot.get("next_item_seq") or 1,
        applied_refreshes=snapshot.get("applied_refreshes") or {},
    )


def delete_personal_vocab_entry(user_id: int, normalized_input: str) -> bool:
    """Delete one learned vocabulary mapping for the user."""
    key = (normalized_input or "").strip()
    if not key:
        return False
    init_data_db()
    with _get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM personal_vocab WHERE user_id = ? AND normalized_input = ?",
            (user_id, key),
        )
        conn.commit()
    return cursor.rowcount > 0


def import_personal_vocab_entry(user_id: int, entry: dict) -> dict | None:
    """Restore one exported personal vocabulary row for a user."""
    if not isinstance(entry, dict):
        return None
    normalized = (entry.get("normalized_input") or "").strip()
    canonical = entry.get("canonical_resolution")
    if not normalized or not isinstance(canonical, dict):
        return None
    now_iso = datetime.now().isoformat(timespec="seconds")
    phrase = (entry.get("phrase") or normalized).strip()[:500]
    accept_count = max(0, int(entry.get("accept_count") or 0))
    correct_count = max(0, int(entry.get("correct_count") or 0))
    skip_count = max(0, int(entry.get("skip_count") or 0))
    deleted_count = max(0, int(entry.get("deleted_count") or 0))
    confidence_boost = max(0.0, min(0.2, float(entry.get("confidence_boost") or 0)))
    last_used = (entry.get("last_used") or now_iso)
    last_negative_feedback_at = entry.get("last_negative_feedback_at")
    created_at = (entry.get("created_at") or now_iso)
    updated_at = (entry.get("updated_at") or now_iso)
    init_data_db()
    with _get_db() as conn:
        row = conn.execute(
            """
            INSERT INTO personal_vocab (
                user_id, normalized_input, phrase, canonical_resolution,
                accept_count, correct_count, skip_count, deleted_count,
                confidence_boost, last_used, last_negative_feedback_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, normalized_input) DO UPDATE SET
                phrase = excluded.phrase,
                canonical_resolution = excluded.canonical_resolution,
                accept_count = excluded.accept_count,
                correct_count = excluded.correct_count,
                skip_count = excluded.skip_count,
                deleted_count = excluded.deleted_count,
                confidence_boost = excluded.confidence_boost,
                last_used = excluded.last_used,
                last_negative_feedback_at = excluded.last_negative_feedback_at,
                created_at = personal_vocab.created_at,
                updated_at = excluded.updated_at
            RETURNING *
            """,
            (
                user_id,
                normalized,
                phrase,
                _json_dumps_or_none(canonical),
                accept_count,
                correct_count,
                skip_count,
                deleted_count,
                confidence_boost,
                last_used,
                last_negative_feedback_at,
                created_at,
                updated_at,
            ),
        ).fetchone()
        conn.commit()
    result = dict(row)
    result["canonical_resolution"] = _json_loads_or_none(result.get("canonical_resolution"))
    return result


def record_personal_vocab_negative_feedback(
    user_id: int,
    *,
    normalized_input: str,
    phrase: str,
    canonical_resolution: dict,
    feedback_type: str,
) -> dict:
    """Record skipped/deleted food review feedback without creating trusted accepts."""
    key = (normalized_input or "").strip()
    if not key or not isinstance(canonical_resolution, dict):
        raise ValueError("normalized_input and canonical_resolution are required")
    feedback = (feedback_type or "").strip().lower()
    if feedback not in {"skipped", "deleted"}:
        raise ValueError("feedback_type must be skipped or deleted")
    skip_delta = 1 if feedback == "skipped" else 0
    deleted_delta = 1 if feedback == "deleted" else 0
    init_data_db()
    now_iso = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        row = conn.execute(
            """
            INSERT INTO personal_vocab (
                user_id, normalized_input, phrase, canonical_resolution,
                accept_count, correct_count, skip_count, deleted_count,
                confidence_boost, last_used, last_negative_feedback_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 0, 0, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(user_id, normalized_input) DO UPDATE SET
                phrase = excluded.phrase,
                canonical_resolution = CASE
                    WHEN personal_vocab.accept_count = 0 THEN excluded.canonical_resolution
                    ELSE personal_vocab.canonical_resolution
                END,
                skip_count = personal_vocab.skip_count + excluded.skip_count,
                deleted_count = personal_vocab.deleted_count + excluded.deleted_count,
                last_used = excluded.last_used,
                last_negative_feedback_at = excluded.last_negative_feedback_at,
                updated_at = excluded.updated_at
            RETURNING *
            """,
            (
                user_id,
                key,
                phrase.strip()[:500],
                _json_dumps_or_none(canonical_resolution),
                skip_delta,
                deleted_delta,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
            ),
        ).fetchone()
        conn.commit()
    result = dict(row)
    result["canonical_resolution"] = _json_loads_or_none(result.get("canonical_resolution"))
    return result


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
        conn.execute("DELETE FROM workout_adaptation_events WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM workout_adaptation_pending WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM food_log_refresh_events WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM food_logs WHERE user_id = ?", (user_id,))
        conn.commit()


def food_log_exists_by_client_id(user_id: int, client_id: str) -> bool:
    """Return whether a user-scoped client_id already has a food-log row."""
    if not client_id:
        return False
    with _get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM food_logs WHERE user_id = ? AND client_id = ? LIMIT 1",
            (user_id, client_id),
        ).fetchone()
    return bool(row)


def backfill_food_log_client_id(user_id: int, client_id: str, match: dict) -> bool:
    """Assign client_id to exactly one matching clientless food_log row.

    The caller supplies only fields that are safe to match on. If the
    match is ambiguous or a row already owns the client_id, leave the
    table unchanged.
    """
    if not client_id or not isinstance(match, dict):
        return False
    allowed_fields = {
        "date",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sodium_mg",
        "context_note",
        "item_name",
        "portion_description",
        "meal_type",
        "source_timestamp",
    }
    match_fields = {k: match[k] for k in allowed_fields if k in match}
    if not match_fields.get("date"):
        return False
    init_data_db()
    now_iso = datetime.now().isoformat(timespec="seconds")
    clauses = ["user_id = ?", "(client_id IS NULL OR client_id = '')"]
    params: list = [user_id]
    for field, value in match_fields.items():
        if value is None:
            clauses.append(f"{field} IS NULL")
        else:
            clauses.append(f"{field} = ?")
            params.append(value)
    where_sql = " AND ".join(clauses)
    with _get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM food_logs WHERE user_id = ? AND client_id = ? LIMIT 1",
            (user_id, client_id),
        ).fetchone()
        if existing:
            return False
        candidates = conn.execute(
            f"SELECT id FROM food_logs WHERE {where_sql} LIMIT 2",
            params,
        ).fetchall()
        if len(candidates) != 1:
            return False
        row = conn.execute(
            """
            UPDATE food_logs
               SET client_id = ?, updated_at = ?
             WHERE id = ?
               AND user_id = ?
               AND (client_id IS NULL OR client_id = '')
            RETURNING id
            """,
            (client_id, now_iso, candidates[0]["id"], user_id),
        ).fetchone()
        conn.commit()
    return bool(row)


def claim_food_log_vocab_learning(user_id: int, client_id: str) -> bool:
    """Atomically claim vocabulary learning for a persisted food log."""
    if not client_id:
        return False
    init_data_db()
    now_iso = datetime.now().isoformat(timespec="seconds")
    with _get_db() as conn:
        row = conn.execute(
            """
            UPDATE food_logs
               SET vocab_learned_at = ?
             WHERE user_id = ?
               AND client_id = ?
               AND vocab_learned_at IS NULL
            RETURNING 1
            """,
            (now_iso, user_id, client_id),
        ).fetchone()
        conn.commit()
    return bool(row)


def delete_food_log_by_client_id(user_id: int, client_id: str) -> bool:
    """Delete a single food log by user-scoped client_id. Returns True if removed."""
    if not client_id:
        return False
    with _get_db() as conn:
        conn.execute(
            "DELETE FROM food_log_refresh_events WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        )
        cursor = conn.execute(
            "DELETE FROM food_logs WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_food_logs_by_meal_id(user_id: int, meal_id: str) -> int:
    """Delete all food logs for a user-scoped meal_id. Returns rows removed."""
    key = (meal_id or "").strip()
    if not key:
        return 0
    with _get_db() as conn:
        conn.execute(
            "DELETE FROM food_log_refresh_events WHERE user_id = ? AND client_id IN "
            "(SELECT client_id FROM food_logs WHERE user_id = ? AND meal_id = ?)",
            (user_id, user_id, key),
        )
        cursor = conn.execute(
            "DELETE FROM food_logs WHERE user_id = ? AND meal_id = ?",
            (user_id, key),
        )
        conn.commit()
        return int(cursor.rowcount or 0)


def add_food_log(user_id: int, record: dict) -> dict:
    """Persist one accepted food entry with sanitized estimate/correction metadata."""
    now_iso = datetime.now().isoformat(timespec="seconds")
    logged_at = record.get("logged_at") or record.get("timestamp") or now_iso
    date_s = record.get("date") or str(logged_at)[:10]
    original_estimate = sanitize_food_estimate(record.get("original_estimate") or record.get("estimate"))
    accepted_estimate = sanitize_accepted_estimate(record.get("accepted_estimate"))
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
        "accepted_estimate_json": _json_dumps_or_none(accepted_estimate),
        "meal_id": record.get("meal_id") or None,
        "meal_item_id": record.get("meal_item_id") or None,
        "item_index": record.get("item_index"),
        "item_state": record.get("item_state") or None,
        "created_at": record.get("created_at") or now_iso,
        "updated_at": now_iso,
    }
    if (
        entry["correction_state"] in {"accepted", "corrected"}
        and _is_authorized_accepted_estimate_replacement(record.get("accepted_estimate"))
    ):
        _project_accepted_estimate_onto_entry(entry, accepted_estimate)
    refresh_metadata = _refresh_event_metadata(record)
    with _get_db() as conn:
        previous_row = None
        if entry.get("client_id"):
            previous_row = conn.execute(
                "SELECT * FROM food_logs WHERE user_id = ? AND client_id = ? LIMIT 1",
                (user_id, entry["client_id"]),
            ).fetchone()
        previous = _food_log_row_to_dict(previous_row) if previous_row is not None else None
        previous_is_terminal = bool(
            isinstance(previous, dict)
            and previous.get("correction_state") in {"accepted", "corrected"}
        )
        if previous_is_terminal:
            previous_original = previous.get("original_estimate")
            if isinstance(previous_original, dict):
                entry["original_estimate_json"] = _json_dumps_or_none(previous_original)
            has_explicit_current = _is_authorized_accepted_estimate_replacement(
                record.get("accepted_estimate")
            )
            if not has_explicit_current:
                entry["source"] = previous.get("source") or entry["source"]
            else:
                _project_accepted_estimate_onto_entry(entry, accepted_estimate)
            if not has_explicit_current and entry["correction_state"] != "pending_review":
                prior_current = previous.get("accepted_estimate") or previous_original
                if isinstance(prior_current, dict):
                    merged_current = dict(prior_current)
                    core_nutrition_fields = {"calories", "protein_g", "carbs_g", "fat_g"}
                    changed_nutrition = False
                    for field in (
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
                    ):
                        value = entry.get(field)
                        nullable_nutrition = {"carbs_g", "fat_g", "sodium_mg", "fiber_g", "confidence"}
                        if field in nullable_nutrition and field in record and value is None:
                            changed_nutrition = changed_nutrition or merged_current.get(field) is not None
                            merged_current[field] = None
                        elif value is not None and not (
                            field in core_nutrition_fields | {"sodium_mg", "fiber_g", "confidence"}
                            and (
                                isinstance(value, bool)
                                or not isinstance(value, (int, float))
                                or not math.isfinite(value)
                                or value < 0
                                or (field == "confidence" and value > 1)
                            )
                        ):
                            if field in core_nutrition_fields | {"sodium_mg", "fiber_g"}:
                                changed_nutrition = changed_nutrition or merged_current.get(field) != value
                            merged_current[field] = entry[field]
                    if changed_nutrition:
                        for field in (
                            "external_food_id",
                            "verified_source_url",
                            "data_fetched_at",
                            "portion_basis",
                            "brand_id",
                            "underlying_source",
                            "underlying_sources",
                            "off_attribution",
                            "vision_description",
                            "vision_provider",
                            "vision_confidence",
                        ):
                            merged_current.pop(field, None)
                        merged_current["source"] = "manual_review_estimate"
                        entry["source"] = "manual_review_estimate"
                    else:
                        merged_current["source"] = entry["source"]
                    finalized_current = sanitize_accepted_estimate(merged_current)
                    entry["accepted_estimate_json"] = _json_dumps_or_none(finalized_current)
                    if isinstance(finalized_current, dict):
                        for field in (
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
                        ):
                            if field in finalized_current:
                                entry[field] = finalized_current[field]
        cols = ["user_id"] + list(entry.keys())
        vals = [user_id] + [entry[c] for c in entry]
        placeholders = ", ".join(["?"] * len(cols))
        update_cols = [c for c in entry if c not in {"client_id", "created_at"}]
        assignments = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
        row = conn.execute(
            f"""
            INSERT INTO food_logs ({', '.join(cols)}) VALUES ({placeholders})
            ON CONFLICT(user_id, client_id) DO UPDATE SET {assignments}
            WHERE excluded.correction_state != 'pending_review'
               OR food_logs.correction_state NOT IN ('accepted', 'corrected')
            RETURNING *
            """,
            vals,
        ).fetchone()
        if row is None and entry.get("client_id"):
            row = conn.execute(
                "SELECT * FROM food_logs WHERE user_id = ? AND client_id = ? LIMIT 1",
                (user_id, entry["client_id"]),
            ).fetchone()
        if refresh_metadata is not None and previous_row is not None and row is not None:
            _insert_food_log_refresh_event(
                conn,
                user_id,
                _food_log_row_to_dict(previous_row),
                _food_log_row_to_dict(row),
                refresh_metadata,
                now_iso,
            )
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
    tables = [
        "body_data",
        "cardio_data",
        "nutrition_data",
        "food_logs",
        "food_log_refresh_events",
        "workout_adaptation_pending",
        "workout_adaptation_events",
        "personal_vocab",
        "meal_acceptance_events",
        "meal_review_snapshots",
        "current_workout_plans",
        "recovery_data",
        "user_settings",
    ]
    with _get_db() as conn:
        for table in tables:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM branded_lookup_cache WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM barcode_lookup_cache WHERE user_id = ?", (user_id,))
        conn.commit()


def get_user_data_summary(user_id: int) -> dict:
    """Return record counts per table for a user (useful for admin/debugging)."""
    tables = [
        "body_data",
        "cardio_data",
        "nutrition_data",
        "food_logs",
        "personal_vocab",
        "meal_acceptance_events",
        "meal_review_snapshots",
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
