"""Normalized wearable fact storage.

Only public, coaching-safe facts belong here. Raw provider payloads and secret
material are rejected before persistence.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3


FORBIDDEN_FIELD_NAMES = {
    "authorization",
    "access_token",
    "refresh_token",
    "token",
    "password",
    "secret",
    "raw",
    "payload",
    "samples",
    "records",
    "user_id",
}

WEARABLE_FACT_SCHEMA_VERSION = 4


@dataclass(frozen=True)
class WearableDailyFact:
    date: str
    provider_id: str
    source_label: str
    metric: str
    value: float | str | bool | None
    unit: str | None = None
    band: str | None = None
    confidence: str = "unknown"
    freshness: str = "unknown"
    conflict_state: str | None = None
    category: str | None = None
    source_id: str | None = None
    source_provider: str | None = None
    original_label: str | None = None
    observed_at: str | None = None
    used_for_recommendation: bool = False
    updated_at: str | None = None
    source_system: str | None = None
    source_record_kind: str | None = None
    metric_domain: str | None = None
    capability_state: str | None = None
    source_last_synced_at: str | None = None
    imported_at: str | None = None

    def public_dict(self) -> dict:
        payload = asdict(self)
        if payload["updated_at"] is None:
            payload["updated_at"] = datetime.now().isoformat()
        if payload["imported_at"] is None:
            payload["imported_at"] = payload["updated_at"]
        return payload


def _scan_forbidden(value: object, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            field_name = str(key).strip().lower()
            if (
                field_name in FORBIDDEN_FIELD_NAMES
                or field_name.endswith("_token")
                or field_name.endswith("_user_id")
            ):
                hits.append(f"{path}.{field_name}" if path else field_name)
            hits.extend(_scan_forbidden(child, f"{path}.{field_name}" if path else field_name))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            hits.extend(_scan_forbidden(child, f"{path}[{idx}]"))
    return hits


def _contains_non_finite_number(value: object) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite_number(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite_number(child) for child in value)
    return False


def validate_public_fact_payload(payload: dict) -> None:
    hits = _scan_forbidden(payload)
    if hits:
        raise ValueError("wearable fact payload contains forbidden raw or secret fields")
    if _contains_non_finite_number(payload):
        raise ValueError("wearable fact payload contains non-finite numbers")


def init_wearable_fact_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    # Manual transaction: DDL must commit with the data copy, or a crash mid-upgrade strands rows in *_legacy.
    # Deferred BEGIN takes no writer lock when nothing needs upgrading.
    conn.isolation_level = None
    try:
        conn.execute("BEGIN")
        try:
            _init_wearable_fact_schema(conn)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def _init_wearable_fact_schema(conn: sqlite3.Connection) -> None:
    _create_wearable_fact_tables(conn)
    _ensure_fact_columns(conn, "wearable_daily_facts")
    _ensure_fact_columns(conn, "wearable_sources")
    restored_legacy = _migrate_profile_key_primary_keys(conn)
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if restored_legacy:
        # Rows copied back from an old *_legacy table predate every migration, whatever the DB version says.
        current_version = 0
    if current_version < WEARABLE_FACT_SCHEMA_VERSION:
        _normalize_open_wearables_fact_ids(conn, derive_blank_ids=False)
        _backfill_fact_contract(conn)
        if current_version < 2:
            _migrate_v2_recommendation_eligibility(conn)
        if current_version < 3:
            _migrate_open_wearables_provider_identity(conn)
        if current_version < 4:
            _normalize_open_wearables_fact_ids(conn, derive_blank_ids=True)
        conn.execute(f"PRAGMA user_version = {WEARABLE_FACT_SCHEMA_VERSION}")


def _ensure_fact_columns(conn: sqlite3.Connection, table: str) -> None:
    _ensure_column(conn, table, "profile_key", "TEXT NOT NULL DEFAULT '1'")
    if table.startswith("wearable_sources"):
        return
    _ensure_column(conn, table, "category", "TEXT")
    _ensure_column(conn, table, "source_id", "TEXT NOT NULL DEFAULT ''")
    for column in (
        "source_provider", "original_label", "observed_at", "source_system", "source_record_kind",
        "metric_domain", "capability_state", "source_last_synced_at", "imported_at",
    ):
        _ensure_column(conn, table, column, "TEXT")


def _create_wearable_fact_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wearable_daily_facts (
            profile_key TEXT NOT NULL DEFAULT '1',
            date TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            source_label TEXT NOT NULL,
            metric TEXT NOT NULL,
            value_json TEXT,
            unit TEXT,
            band TEXT,
            confidence TEXT NOT NULL,
            freshness TEXT NOT NULL,
            conflict_state TEXT,
            category TEXT,
            source_id TEXT NOT NULL DEFAULT '',
            source_provider TEXT,
            original_label TEXT,
            observed_at TEXT,
            source_system TEXT NOT NULL,
            source_record_kind TEXT,
            metric_domain TEXT,
            capability_state TEXT,
            source_last_synced_at TEXT,
            imported_at TEXT,
            used_for_recommendation INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (profile_key, date, provider_id, source_system, metric, source_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wearable_sources (
            profile_key TEXT NOT NULL DEFAULT '1',
            provider_id TEXT NOT NULL,
            label TEXT NOT NULL,
            status TEXT NOT NULL,
            last_data_point TEXT,
            last_sync_attempt TEXT,
            capabilities_json TEXT,
            used_for_recommendation INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (profile_key, provider_id)
        )
        """
    )


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in sorted((row for row in rows if row[5]), key=lambda row: row[5])]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _migrate_profile_key_primary_keys(conn: sqlite3.Connection) -> bool:
    """Returns True when rows were copied back from a leftover *_legacy table."""
    restored = False
    # A leftover *_legacy table means an older build died mid-upgrade: finish the copy instead of losing rows.
    for table, expected_pk, copy_sql in (
        (
            "wearable_daily_facts",
            ["profile_key", "date", "provider_id", "source_system", "metric", "source_id"],
            """
            INTO wearable_daily_facts (
                profile_key, date, provider_id, source_label, metric, value_json, unit, band,
                confidence, freshness, conflict_state, category, source_id, source_provider,
                original_label, observed_at, source_system, source_record_kind, metric_domain,
                capability_state, source_last_synced_at, imported_at,
                used_for_recommendation, updated_at
            )
            SELECT COALESCE(profile_key, '1'), date, provider_id, source_label, metric, value_json, unit, band,
                confidence, freshness, conflict_state, category, COALESCE(source_id, ''), source_provider,
                original_label, observed_at,
                COALESCE(NULLIF(source_system, ''), provider_id), source_record_kind, metric_domain,
                capability_state, source_last_synced_at, imported_at,
                used_for_recommendation, updated_at
            FROM wearable_daily_facts_legacy
            """,
        ),
        (
            "wearable_sources",
            ["profile_key", "provider_id"],
            """
            INTO wearable_sources (
                profile_key, provider_id, label, status, last_data_point, last_sync_attempt,
                capabilities_json, used_for_recommendation, updated_at
            )
            SELECT COALESCE(profile_key, '1'), provider_id, label, status, last_data_point, last_sync_attempt,
                capabilities_json, used_for_recommendation, updated_at
            FROM wearable_sources_legacy
            """,
        ),
    ):
        legacy = f"{table}_legacy"
        if _table_exists(conn, legacy):
            # Rows the new table already took after the crash are newer than the legacy copy.
            verb = "INSERT OR IGNORE"
            restored = True
            # An older build may have died before adding the newer columns to the table it renamed.
            _ensure_fact_columns(conn, legacy)
        elif _primary_key_columns(conn, table) != expected_pk:
            conn.execute(f"ALTER TABLE {table} RENAME TO {legacy}")
            _create_wearable_fact_tables(conn)
            verb = "INSERT OR REPLACE"
        else:
            continue
        conn.execute(f"{verb} {copy_sql}")
        conn.execute(f"DROP TABLE {legacy}")
    return restored


def _backfill_fact_contract(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE wearable_daily_facts SET source_system = COALESCE(NULLIF(source_system, ''), provider_id), "
        "source_record_kind = COALESCE(NULLIF(source_record_kind, ''), "
        "CASE WHEN metric LIKE 'workout_%' "
        "THEN 'event' ELSE 'summary' END), "
        "metric_domain = COALESCE(NULLIF(metric_domain, ''), CASE "
        "WHEN metric = 'workout_load' THEN 'load' "
        "WHEN metric LIKE 'workout_%' THEN 'training_history' "
        "WHEN metric LIKE 'sleep_%' THEN 'sleep' "
        "WHEN metric = 'skin_temperature' AND COALESCE(source_id, '') = '' THEN 'body' "
        "WHEN metric = 'skin_temperature' THEN 'recovery' "
        "WHEN metric IN ('weight', 'body_fat_percent', 'muscle_mass', 'body_mass_index', "
        "'body_temperature') THEN 'body' "
        "WHEN metric IN ('recovery_score', 'heart_rate_variability', 'resting_heart_rate', "
        "'blood_oxygen', 'respiratory_rate', 'temperature_deviation', "
        "'resting_heart_rate_average', 'heart_rate_variability_sdnn_average', "
        "'heart_rate_variability_rmssd_average') "
        "THEN 'recovery' ELSE 'activity' END), "
        "capability_state = COALESCE(NULLIF(capability_state, ''), 'available'), "
        "source_last_synced_at = COALESCE(source_last_synced_at, updated_at), "
        "imported_at = COALESCE(imported_at, updated_at) "
        "WHERE source_system IS NULL OR source_system = '' "
        "OR source_record_kind IS NULL OR source_record_kind = '' "
        "OR metric_domain IS NULL OR metric_domain = '' "
        "OR capability_state IS NULL OR capability_state = '' "
        "OR source_last_synced_at IS NULL OR imported_at IS NULL"
    )


def _migrate_v2_recommendation_eligibility(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE wearable_daily_facts
        SET used_for_recommendation = 1
        WHERE source_system = 'open_wearables'
          AND freshness IN ('fresh', 'aging')
        """
    )


def _migrate_open_wearables_provider_identity(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE wearable_daily_facts
        SET source_provider = CASE
            WHEN LOWER(source_provider) LIKE '%healthkit%' THEN 'apple'
            WHEN LOWER(source_provider) LIKE '%apple%' THEN 'apple'
            WHEN LOWER(source_provider) LIKE '%samsung%' THEN 'samsung'
            WHEN LOWER(source_provider) LIKE '%google%' THEN 'google'
            WHEN LOWER(source_provider) LIKE '%garmin%' THEN 'garmin'
            WHEN LOWER(source_provider) LIKE '%polar%' THEN 'polar'
            WHEN LOWER(source_provider) LIKE '%suunto%' THEN 'suunto'
            WHEN LOWER(source_provider) LIKE '%whoop%' THEN 'whoop'
            WHEN LOWER(source_provider) LIKE '%strava%' THEN 'strava'
            WHEN LOWER(source_provider) LIKE '%oura%' THEN 'oura'
            WHEN LOWER(source_provider) LIKE '%fitbit%' THEN 'fitbit'
            WHEN LOWER(source_provider) LIKE '%ultrahuman%' THEN 'ultrahuman'
            ELSE NULL
        END
        WHERE source_system = 'open_wearables'
          AND provider_id = 'open_wearables'
          AND source_provider IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE wearable_daily_facts
        SET source_provider = NULL,
            source_label = 'Open Wearables'
        WHERE source_system = 'open_wearables'
          AND provider_id = 'open_wearables'
          AND (source_provider IS NOT NULL OR source_label != 'Open Wearables')
          AND (
              metric IN (
                  'weight', 'body_fat_percent', 'muscle_mass', 'body_mass_index',
                  'body_temperature', 'resting_heart_rate_average',
                  'heart_rate_variability_sdnn_average',
                  'heart_rate_variability_rmssd_average'
              )
              OR (metric = 'skin_temperature' AND COALESCE(source_id, '') = '')
          )
        """
    )
    conn.execute(
        """
        INSERT INTO wearable_daily_facts (
            profile_key, date, provider_id, source_label, metric, value_json, unit, band,
            confidence, freshness, conflict_state, category, source_id, source_provider,
            original_label, observed_at, source_system, source_record_kind, metric_domain,
            capability_state, source_last_synced_at, imported_at,
            used_for_recommendation, updated_at
        )
        SELECT profile_key, date, source_provider,
            CASE LOWER(source_provider)
                WHEN 'oura' THEN 'Oura'
                WHEN 'whoop' THEN 'WHOOP'
                WHEN 'apple' THEN 'Apple Health'
                WHEN 'samsung' THEN 'Samsung Health'
                WHEN 'google' THEN 'Google Health Connect'
                WHEN 'garmin' THEN 'Garmin'
                WHEN 'polar' THEN 'Polar'
                WHEN 'suunto' THEN 'Suunto'
                WHEN 'strava' THEN 'Strava'
                WHEN 'fitbit' THEN 'Fitbit'
                WHEN 'ultrahuman' THEN 'Ultrahuman'
            END,
            metric, value_json, unit, band,
            confidence, freshness, conflict_state, category, source_id, source_provider,
            original_label, observed_at, source_system, source_record_kind, metric_domain,
            capability_state, source_last_synced_at, imported_at,
            used_for_recommendation, updated_at
        FROM wearable_daily_facts
        WHERE source_system = 'open_wearables'
          AND provider_id = 'open_wearables'
          AND source_provider IS NOT NULL
          AND source_provider != ''
        ON CONFLICT(profile_key, date, provider_id, source_system, metric, source_id) DO UPDATE SET
            source_label=excluded.source_label,
            value_json=excluded.value_json,
            unit=excluded.unit,
            band=excluded.band,
            confidence=excluded.confidence,
            freshness=excluded.freshness,
            conflict_state=excluded.conflict_state,
            category=excluded.category,
            source_provider=excluded.source_provider,
            original_label=excluded.original_label,
            observed_at=excluded.observed_at,
            source_system=excluded.source_system,
            source_record_kind=excluded.source_record_kind,
            metric_domain=excluded.metric_domain,
            capability_state=excluded.capability_state,
            source_last_synced_at=excluded.source_last_synced_at,
            imported_at=COALESCE(wearable_daily_facts.imported_at, excluded.imported_at),
            used_for_recommendation=excluded.used_for_recommendation,
            updated_at=excluded.updated_at
        WHERE julianday(excluded.updated_at) > julianday(wearable_daily_facts.updated_at)
        """
    )
    conn.execute(
        "DELETE FROM wearable_daily_facts "
        "WHERE source_system = 'open_wearables' AND provider_id = 'open_wearables' "
        "AND source_provider IS NOT NULL AND source_provider != '' "
    )
    conn.execute(
        """
        UPDATE wearable_daily_facts
        SET used_for_recommendation = 0
        WHERE source_system = 'open_wearables'
          AND provider_id = 'open_wearables'
          AND COALESCE(source_provider, '') = ''
        """
    )
    conn.execute(
        """
        UPDATE wearable_daily_facts
        SET capability_state = 'legacy_timestamp_unknown',
            used_for_recommendation = 0
        WHERE source_system = 'open_wearables'
          AND metric LIKE 'workout_%'
          AND (
              observed_at IS NULL
              OR TRIM(observed_at) = ''
              OR (
                  UPPER(TRIM(observed_at)) NOT LIKE '%Z'
                  AND INSTR(SUBSTR(observed_at, 12), '+') = 0
                  AND INSTR(SUBSTR(observed_at, 12), '-') = 0
              )
          )
        """
    )


def _normalize_open_wearables_fact_ids(
    conn: sqlite3.Connection,
    *,
    derive_blank_ids: bool,
) -> None:
    rows = conn.execute(
        """
        SELECT rowid, profile_key, source_id, source_record_kind, metric_domain,
               observed_at, date, provider_id, source_system, metric, updated_at
        FROM wearable_daily_facts
        WHERE COALESCE(NULLIF(source_system, ''), provider_id) = 'open_wearables'
        ORDER BY COALESCE(julianday(updated_at), 0), updated_at, rowid
        """
    ).fetchall()
    for (
        rowid, profile_key, source_id, record_kind, metric_domain, observed_at,
        fact_date, provider_id, source_system, metric, updated_at,
    ) in rows:
        raw_source_id = str(source_id or "")
        normalized_source_id = raw_source_id.strip()
        if normalized_source_id == raw_source_id and (
            normalized_source_id or not derive_blank_ids
        ):
            continue
        target_source_id = (
            normalized_source_id
            if normalized_source_id or not derive_blank_ids
            else derived_fact_source_id(
                record_kind, metric_domain, observed_at, fact_date,
            )
        )
        target_date = (
            fact_date
            if normalized_source_id or not derive_blank_ids
            else canonical_fact_date(observed_at, fact_date)
        )
        identity_source_system = str(source_system or provider_id)
        collision = conn.execute(
            """
            SELECT rowid, updated_at
            FROM wearable_daily_facts
            WHERE profile_key = ? AND date = ? AND provider_id = ?
              AND COALESCE(NULLIF(source_system, ''), provider_id) = ?
              AND metric = ? AND source_id = ?
              AND rowid != ?
            LIMIT 1
            """,
            (
                profile_key, target_date, provider_id, identity_source_system, metric,
                target_source_id, rowid,
            ),
        ).fetchone()
        if collision is not None:
            collision_rowid, collision_updated_at = collision
            current_order = conn.execute(
                "SELECT COALESCE(julianday(?), 0)", (updated_at,)
            ).fetchone()[0]
            collision_order = conn.execute(
                "SELECT COALESCE(julianday(?), 0)", (collision_updated_at,)
            ).fetchone()[0]
            if (collision_order, str(collision_updated_at), collision_rowid) >= (
                current_order, str(updated_at), rowid,
            ):
                conn.execute(
                    "DELETE FROM wearable_daily_facts WHERE rowid = ?",
                    (rowid,),
                )
                continue
            conn.execute(
                "DELETE FROM wearable_daily_facts WHERE rowid = ?",
                (collision_rowid,),
            )
        conn.execute(
            "UPDATE wearable_daily_facts SET source_id = ?, date = ? WHERE rowid = ?",
            (target_source_id, target_date, rowid),
        )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row[1] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _normalize_profile_key(profile_key: str | int | None) -> str:
    text = str(profile_key or "1").strip()
    return text or "1"


def _default_metric_domain(metric: object) -> str:
    name = str(metric or "")
    if name == "workout_load":
        return "load"
    if name.startswith("workout_"):
        return "training_history"
    if name.startswith("sleep_"):
        return "sleep"
    if name in {"weight", "body_fat_percent", "muscle_mass", "body_mass_index", "body_temperature"}:
        return "body"
    if name == "skin_temperature":
        return "body"
    if name in {
        "recovery_score", "heart_rate_variability", "resting_heart_rate", "blood_oxygen",
        "respiratory_rate", "skin_temperature", "temperature_deviation",
        "resting_heart_rate_average", "heart_rate_variability_sdnn_average",
        "heart_rate_variability_rmssd_average",
    }:
        return "recovery"
    return "activity"


def derived_fact_source_id(
    source_record_kind: object,
    metric_domain: object,
    observed_at: object,
    fact_date: object,
) -> str:
    identity_time = _canonical_utc_observation(observed_at) or fact_date
    return (
        f"derived:{source_record_kind or 'summary'}:"
        f"{metric_domain or 'activity'}:{identity_time}"
    )


def canonical_fact_date(observed_at: object, fact_date: object) -> str:
    canonical_observation = _canonical_utc_observation(observed_at)
    return canonical_observation[:10] if canonical_observation else str(fact_date)


def _canonical_utc_observation(observed_at: object) -> str | None:
    if observed_at:
        try:
            parsed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        else:
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
    return None


def upsert_daily_facts(
    db_path: str,
    facts: list[WearableDailyFact | dict],
    profile_key: str | int | None = None,
    replace_source_ids: set[str] | None = None,
    replace_fact_scopes: set[tuple[str, str, str, str]] | None = None,
    replace_source_id_date_windows: set[tuple[str, str, str, str]] | None = None,
    replace_provider_metric_observation_windows: set[tuple[str, str, str, str]] | None = None,
) -> int:
    init_wearable_fact_db(db_path)
    now = datetime.now().isoformat()
    scoped_profile = _normalize_profile_key(profile_key)
    rows = []
    for fact in facts:
        payload = fact.public_dict() if isinstance(fact, WearableDailyFact) else dict(fact)
        validate_public_fact_payload(payload)
        payload["profile_key"] = _normalize_profile_key(payload.get("profile_key") or scoped_profile)
        payload["source_system"] = str(payload.get("source_system") or payload["provider_id"])
        raw_source_id = str(payload.get("source_id") or "")
        payload["source_id"] = (
            raw_source_id.strip()
            if payload["source_system"] == "open_wearables"
            else raw_source_id
        )
        payload["source_record_kind"] = str(payload.get("source_record_kind") or (
            "event"
            if str(payload.get("metric") or "").startswith("workout_")
            else "summary"
        ))
        payload["metric_domain"] = str(
            payload.get("metric_domain")
            or _default_metric_domain(payload.get("metric"))
        )
        payload["capability_state"] = str(payload.get("capability_state") or "available")
        payload["source_last_synced_at"] = payload.get("source_last_synced_at") or payload.get("updated_at") or now
        payload["imported_at"] = payload.get("imported_at") or payload.get("updated_at") or now
        rows.append(payload)
    with sqlite3.connect(db_path) as conn:
        # The replace_* deletes below would drop the first-seen time of rows that are
        # written again, so read it before any delete.
        first_imported = {}
        for row in rows:
            key = (row["profile_key"], row["date"], row["provider_id"], row["source_system"], row["metric"], row["source_id"])
            found = conn.execute(
                "SELECT imported_at FROM wearable_daily_facts WHERE profile_key = ? AND date = ? "
                "AND provider_id = ? AND source_system = ? AND metric = ? AND source_id = ?",
                key,
            ).fetchone()
            if found and found[0]:
                first_imported[key] = found[0]
        for source_system, source_id, start_date, end_date in replace_source_id_date_windows or set():
            conn.execute(
                "DELETE FROM wearable_daily_facts "
                "WHERE profile_key = ? AND source_system = ? AND source_id = ? "
                "AND date >= date(?) AND date < date(?)",
                (scoped_profile, source_system, source_id, start_date, end_date),
            )
        for source_system, provider_id, metric, metric_domain in replace_fact_scopes or set():
            conn.execute(
                "DELETE FROM wearable_daily_facts "
                "WHERE profile_key = ? AND source_system = ? AND provider_id = ? "
                "AND metric = ? AND metric_domain = ?",
                (scoped_profile, source_system, provider_id, metric, metric_domain),
            )
        for source_system, metric_prefix, start_at, end_at in replace_provider_metric_observation_windows or set():
            conn.execute(
                "DELETE FROM wearable_daily_facts "
                "WHERE profile_key = ? AND source_system = ? "
                "AND substr(metric, 1, length(?)) = ? "
                "AND COALESCE(capability_state, '') != 'legacy_timestamp_unknown' "
                "AND julianday(observed_at) >= julianday(?) "
                "AND julianday(observed_at) < julianday(?)",
                (
                    scoped_profile,
                    source_system,
                    metric_prefix,
                    metric_prefix,
                    start_at,
                    end_at,
                ),
            )
        for source_id in replace_source_ids or set():
            source_systems = {row["source_system"] for row in rows if row.get("source_id") == source_id}
            for source_system in source_systems:
                conn.execute(
                    "DELETE FROM wearable_daily_facts WHERE profile_key = ? AND source_system = ? AND source_id = ?",
                    (scoped_profile, source_system, source_id),
                )
        for row in rows:
            row["imported_at"] = first_imported.get(
                (row["profile_key"], row["date"], row["provider_id"], row["source_system"], row["metric"], row["source_id"]),
                row["imported_at"],
            )
            conn.execute(
                """
                INSERT INTO wearable_daily_facts (
                    profile_key, date, provider_id, source_label, metric, value_json, unit, band,
                    confidence, freshness, conflict_state, category, source_id, source_provider,
                    original_label, observed_at, source_system, source_record_kind, metric_domain,
                    capability_state, source_last_synced_at, imported_at,
                    used_for_recommendation, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_key, date, provider_id, source_system, metric, source_id) DO UPDATE SET
                    source_label=excluded.source_label,
                    value_json=excluded.value_json,
                    unit=excluded.unit,
                    band=excluded.band,
                    confidence=excluded.confidence,
                    freshness=excluded.freshness,
                    conflict_state=excluded.conflict_state,
                    category=excluded.category,
                    source_id=excluded.source_id,
                    source_provider=excluded.source_provider,
                    original_label=excluded.original_label,
                    observed_at=excluded.observed_at,
                    source_system=excluded.source_system,
                    source_record_kind=excluded.source_record_kind,
                    metric_domain=excluded.metric_domain,
                    capability_state=excluded.capability_state,
                    source_last_synced_at=excluded.source_last_synced_at,
                    imported_at=COALESCE(wearable_daily_facts.imported_at, excluded.imported_at),
                    used_for_recommendation=excluded.used_for_recommendation,
                    updated_at=excluded.updated_at
                """,
                (
                    row["profile_key"],
                    row["date"],
                    row["provider_id"],
                    row["source_label"],
                    row["metric"],
                    json.dumps(row.get("value")),
                    row.get("unit"),
                    row.get("band"),
                    row.get("confidence") or "unknown",
                    row.get("freshness") or "unknown",
                    row.get("conflict_state"),
                    row.get("category"),
                    row.get("source_id"),
                    row.get("source_provider"),
                    row.get("original_label"),
                    row.get("observed_at"),
                    row.get("source_system"),
                    row.get("source_record_kind"),
                    row.get("metric_domain"),
                    row.get("capability_state"),
                    row.get("source_last_synced_at"),
                    row.get("imported_at"),
                    1 if row.get("used_for_recommendation") else 0,
                    row.get("updated_at") or now,
                ),
            )
    return len(rows)


def upsert_wearable_source(db_path: str, source: dict, profile_key: str | int | None = None) -> None:
    init_wearable_fact_db(db_path)
    validate_public_fact_payload(source)
    now = datetime.now().isoformat()
    scoped_profile = _normalize_profile_key(source.get("profile_key") or profile_key)
    capabilities = source.get("capabilities") if isinstance(source.get("capabilities"), dict) else {}
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO wearable_sources (
                profile_key, provider_id, label, status, last_data_point, last_sync_attempt,
                capabilities_json, used_for_recommendation, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_key, provider_id) DO UPDATE SET
                label=excluded.label,
                status=excluded.status,
                last_data_point=excluded.last_data_point,
                last_sync_attempt=excluded.last_sync_attempt,
                capabilities_json=excluded.capabilities_json,
                used_for_recommendation=excluded.used_for_recommendation,
                updated_at=excluded.updated_at
            """,
            (
                scoped_profile,
                source["provider_id"],
                source.get("label") or source["provider_id"],
                source.get("status") or "unknown",
                source.get("last_data_point"),
                source.get("last_sync_attempt"),
                json.dumps(capabilities, sort_keys=True),
                1 if source.get("used_for_recommendation") else 0,
                source.get("updated_at") or now,
            ),
        )


def list_wearable_sources(db_path: str, profile_key: str | int | None = None) -> list[dict]:
    init_wearable_fact_db(db_path)
    scoped_profile = _normalize_profile_key(profile_key)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM wearable_sources WHERE profile_key = ? ORDER BY provider_id",
            (scoped_profile,),
        ).fetchall()
    result = []
    for row in rows:
        status = row["status"]
        used_for_recommendation = bool(row["used_for_recommendation"])
        if row["provider_id"] == "open_wearables" and status != "error" and used_for_recommendation:
            has_usable_fact = bool(list_recommendation_facts(
                db_path,
                limit=1,
                profile_key=scoped_profile,
                source_system="open_wearables",
                usable_only=True,
            ))
            if not has_usable_fact:
                status = "stale"
                used_for_recommendation = False
        result.append({
            "source": row["provider_id"],
            "provider_id": row["provider_id"],
            "label": row["label"],
            "status": status,
            "last_data_point": row["last_data_point"],
            "last_sync_attempt": row["last_sync_attempt"],
            "capabilities": json.loads(row["capabilities_json"] or "{}"),
            "used_for_recommendation": used_for_recommendation,
        })
    return result


def list_recommendation_facts(
    db_path: str,
    limit: int = 30,
    profile_key: str | int | None = None,
    provider_id: str | None = None,
    usable_only: bool = False,
    latest_per_metric: bool = False,
    source_system: str | None = None,
    metric_names: set[str] | None = None,
) -> list[dict]:
    init_wearable_fact_db(db_path)
    scoped_profile = _normalize_profile_key(profile_key)
    usable_cutoff = (datetime.now().date() - timedelta(days=1)).isoformat()
    usable_date_ceiling = datetime.now().date().isoformat()
    where_clause = """
        profile_key = ?
        AND (? IS NULL OR provider_id = ?)
        AND (? IS NULL OR source_system = ?)
        AND (? = 0 OR (
            used_for_recommendation = 1
            AND
            freshness IN ('fresh', 'aging')
            AND ((observed_at IS NOT NULL AND datetime(observed_at) >= datetime(?))
                 OR (observed_at IS NULL AND date >= ?))
            AND ((observed_at IS NOT NULL AND datetime(observed_at) <= datetime(?))
                 OR (observed_at IS NULL AND date <= ?))
        ))
    """
    metric_filter = tuple(sorted({str(metric) for metric in metric_names or set() if str(metric)}))
    if metric_filter:
        where_clause += f" AND metric IN ({','.join('?' for _ in metric_filter)})"
    order_clause = (
        "date DESC, COALESCE(julianday(observed_at), julianday(date)) DESC, "
        "provider_id, metric, updated_at DESC, source_id"
    )
    query = (
        f"""
        WITH ranked_provider_facts AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY metric, provider_id, source_system ORDER BY {order_clause}
            ) AS provider_rank
            FROM wearable_daily_facts
            WHERE {where_clause}
        ), latest_provider_facts AS (
            SELECT * FROM ranked_provider_facts WHERE provider_rank = 1
        ), metric_representatives AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY metric ORDER BY {order_clause}
            ) AS metric_rank
            FROM latest_provider_facts
        ), selected_metrics AS (
            SELECT metric
            FROM metric_representatives
            WHERE metric_rank = 1
            ORDER BY {order_clause}
            LIMIT ?
        )
        SELECT latest_provider_facts.*
        FROM latest_provider_facts
        JOIN selected_metrics USING (metric)
        ORDER BY {order_clause}
        """
        if latest_per_metric
        else f"""
        SELECT * FROM wearable_daily_facts
        WHERE {where_clause}
        ORDER BY {order_clause}
        LIMIT ?
        """
    )
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            query,
            (
                scoped_profile, provider_id, provider_id, source_system, source_system,
                1 if usable_only else 0,
                (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(), usable_cutoff,
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                usable_date_ceiling,
                *metric_filter,
                int(limit),
            ),
        ).fetchall()
    facts = []
    for row in rows:
        effective_freshness = row["freshness"]
        if usable_only:
            if row["observed_at"]:
                observed = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    # Same reading as the SQL cutoff's datetime(): offset-less means UTC.
                    observed = observed.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() / 3600.0
                effective_freshness = "fresh" if age_hours < 24 else "aging"
            else:
                effective_freshness = "fresh" if row["date"] == datetime.now().date().isoformat() else "aging"
        facts.append({
            "profile_key": row["profile_key"],
            "date": row["date"],
            "fact_date": row["date"],
            "provider_id": row["provider_id"],
            "source_label": row["source_label"],
            "provider_display_name": row["source_label"],
            "source_system": row["source_system"],
            "source_record_kind": row["source_record_kind"],
            "metric_domain": row["metric_domain"],
            "metric": row["metric"],
            "metric_name": row["metric"],
            "value": json.loads(row["value_json"]) if row["value_json"] else None,
            "unit": row["unit"],
            "band": row["band"],
            "score_state": row["band"],
            "confidence": row["confidence"],
            "freshness": effective_freshness,
            "freshness_state": effective_freshness,
            "capability_state": row["capability_state"],
            "conflict_state": row["conflict_state"],
            "category": row["category"],
            "canonical_category": row["category"],
            "source_id": row["source_id"] or None,
            "source_record_id": row["source_id"] or None,
            "source_provider": row["source_provider"],
            "original_label": row["original_label"],
            "observed_at": row["observed_at"],
            "source_observed_at": row["observed_at"],
            "source_last_synced_at": row["source_last_synced_at"],
            "imported_at": row["imported_at"],
            "provenance": {
                "source_system": row["source_system"],
                "provider_id": row["provider_id"],
                "provider_display_name": row["source_label"],
            },
            "used_for_recommendation": bool(row["used_for_recommendation"]),
            "updated_at": row["updated_at"],
        })
    return facts


# The owner's source rule, in one place: which provider wins per domain.
# Providers not listed rank after the listed ones.
SOURCE_PREFERENCE = {
    "sleep": ["oura", "whoop", "apple"],
    "recovery": ["oura", "whoop", "apple"],
    "activity": ["whoop", "oura", "apple"],
}
# Two providers that disagree on hours slept by more than the tolerance are a
# conflict; the lower value is used so a conflict never raises demands. Recovery
# scores are different models, not the same quantity, so they are never merged
# (the WHOOP recovery guard covers a red WHOOP score on its own).
CONFLICT_TOLERANCE = {"sleep_duration": 60.0}
# A shorter night is not a night (a strap taken off); it never wins or triggers a conflict.
MIN_NIGHT_SLEEP_MIN = 60.0
_KIND_RANK = {"summary": 0, "event": 1}


def fact_provider(provider_id: object, source_provider: object) -> str:
    """The wearable that measured a fact; Open Wearables is only the hub."""
    provider = str(source_provider or "").strip().lower()
    if provider:
        return provider
    return str(provider_id or "").strip().lower() or "open_wearables"


def fact_age_freshness(date_s: str, today: str | None = None) -> str:
    """fresh <= 1 day old, aging at 2 days, stale after (dates carry no time of day)."""
    today_s = today or datetime.now().date().isoformat()
    try:
        age = (datetime.fromisoformat(today_s).date() - datetime.fromisoformat(str(date_s)[:10]).date()).days
    except ValueError:
        return "unknown"
    return "fresh" if age <= 1 else "aging" if age == 2 else "stale"


def _plain_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def preferred_daily_facts(
    db_path: str,
    metrics: set[str] | list[str] | tuple[str, ...],
    start: str,
    end: str,
    profile_key: str | int | None = None,
    today: str | None = None,
    only_provider: str | None = None,
) -> dict[str, dict[str, dict]]:
    """One fact per date and metric: {date: {metric: fact}}; only_provider keeps one provider's facts.

    Order: the domain's provider preference, then a live (non-archive) fact over an
    archive one, then summary over event, then the newest write. Every reader of
    sleep, recovery or activity goes through here.
    """
    metric_list = sorted({str(m) for m in metrics})
    if not metric_list:
        return {}
    init_wearable_fact_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM wearable_daily_facts WHERE profile_key = ? AND date >= ? AND date <= ? "
            f"AND (metric IN ({','.join('?' for _ in metric_list)})"
            # a night's representative record is judged on all its sleep metrics, whatever the caller asked for
            + (" OR metric LIKE 'sleep\\_%' ESCAPE '\\')" if "sleep_duration" in metric_list else ")"),
            (_normalize_profile_key(profile_key), start, end, *metric_list),
        ).fetchall()
    grouped: dict[tuple[str, str], list] = {}
    for row in rows:
        value = _plain_number(json.loads(row["value_json"]) if row["value_json"] else None)
        if value is None:
            continue
        provider = fact_provider(row["provider_id"], row["source_provider"])
        if only_provider and provider != only_provider:
            continue
        if row["freshness"] == "unknown" and row["source_system"] != "archive":
            continue  # the mapper writes unknown for invalid or future timestamps; archive rows are exempt by source_system
        preference = SOURCE_PREFERENCE.get(_default_metric_domain(row["metric"]), [])
        rank = (
            preference.index(provider) if provider in preference else len(preference),
            1 if row["source_system"] == "archive" else 0,
            _KIND_RANK.get(row["source_record_kind"], 2),
        )
        grouped.setdefault((row["date"], row["metric"]), []).append((rank, value, provider, row))
    result: dict[str, dict[str, dict]] = {}
    for candidates in grouped.values():
        candidates.sort(key=lambda c: c[3]["source_id"] or "")
        candidates.sort(key=lambda c: c[3]["updated_at"] or "", reverse=True)
        candidates.sort(key=lambda c: c[0])
    # A date's sleep conflict picks one provider; the whole night is then that provider's.
    # Only plausible durations compete, so a strap-off fragment is not a conflict.
    conflicts: dict[str, str] = {}
    gap_fills: dict[str, str] = {}  # the top-ranked provider logged only a fragment: the next real night fills in
    fragments: dict[str, set] = {}  # a night with a real duration: its sub-hour records (provider, source_id) never feed any sleep metric
    night_record: dict[str, dict[str, object]] = {}  # date -> provider -> source_id of that provider's representative record
    sleep_metrics_carried = Counter(  # how many sleep metrics each record supplies
        (d, c[2], c[3]["source_id"]) for (d, m), cs in grouped.items() if _default_metric_domain(m) == "sleep" for c in cs
    )
    for (date_s, metric), candidates in grouped.items():
        tolerance = CONFLICT_TOLERANCE.get(metric)
        if tolerance is None:
            continue
        # Each provider's representative night: live before archive, then the fullest record, then the sorted order.
        reps: dict[str, tuple] = {}
        for c in candidates:
            if c[1] < MIN_NIGHT_SLEEP_MIN:
                continue
            best = reps.get(c[2])
            if best is None or (c[0][1], -sleep_metrics_carried[(date_s, c[2], c[3]["source_id"])]) < (
                best[0][1], -sleep_metrics_carried[(date_s, best[2], best[3]["source_id"])]
            ):
                reps[c[2]] = c
        by_provider = {p: c[1] for p, c in reps.items()}
        night_record[date_s] = {p: c[3]["source_id"] for p, c in reps.items()}
        if len(by_provider) > 1 and max(by_provider.values()) - min(by_provider.values()) > tolerance:
            conflicts[date_s] = min(by_provider, key=by_provider.get)
        elif by_provider and candidates[0][1] < MIN_NIGHT_SLEEP_MIN:
            gap_fills[date_s] = next(iter(by_provider))
        if by_provider:
            fragments[date_s] = {(c[2], c[3]["source_id"]) for c in candidates if c[1] < MIN_NIGHT_SLEEP_MIN}
    for (date_s, metric), candidates in grouped.items():
        if metric not in metric_list:
            continue  # loaded only to rank the night's records
        forced = None
        if _default_metric_domain(metric) == "sleep":
            forced = conflicts.get(date_s) or gap_fills.get(date_s)
            # The duration always comes from the record that justified the choice; other metrics stay free outside a forced night.
            pin = forced or (metric in CONFLICT_TOLERANCE and next(iter(night_record.get(date_s, {})), None))
            candidates = [
                c for c in candidates
                if (not pin or (c[2] == pin and c[3]["source_id"] == night_record[date_s][pin]))
                and (c[2], c[3]["source_id"]) not in fragments.get(date_s, ())
            ]
            if not candidates:
                continue  # the used provider has no such metric: never borrow another provider's
        rank, value, provider, row = candidates[0]
        conflict = "conservative_value_used" if date_s in conflicts and forced else row["conflict_state"]
        result.setdefault(date_s, {})[metric] = {
            "date": date_s,
            "metric": metric,
            "value": value,
            "provider": provider,
            "via": "archive" if row["source_system"] == "archive" else "open_wearables",
            "source_label": row["source_label"],
            "original_label": row["original_label"],
            "observed_at": row["observed_at"],
            "imported_at": row["imported_at"],
            "freshness": fact_age_freshness(date_s, today),
            "conflict_state": conflict,
        }
    return result


def list_workout_events(
    db_path: str, profile_key: str | int | None = None, start: str | None = None,
) -> list[dict]:
    """Stored workouts (live and archive), one dict per event with its workout_* metrics."""
    init_wearable_fact_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM wearable_daily_facts WHERE profile_key = ? AND metric LIKE 'workout\\_%' ESCAPE '\\' "
            "AND (? IS NULL OR date >= ?)",
            (_normalize_profile_key(profile_key), start, start),
        ).fetchall()
    events: dict[tuple, dict] = {}
    for row in rows:
        value = _plain_number(json.loads(row["value_json"]) if row["value_json"] else None)
        if value is None:
            continue
        provider = fact_provider(row["provider_id"], row["source_provider"])
        key = (row["source_system"], provider, row["source_id"], row["date"])
        event = events.setdefault(key, {
            "provider": provider,
            "via": "archive" if row["source_system"] == "archive" else "open_wearables",
            "source_id": row["source_id"],
            "date": row["date"],
            "observed_at": row["observed_at"],
            "original_label": row["original_label"],
            "category": row["category"],
            "imported_at": row["imported_at"],
            "metrics": {},
        })
        event["metrics"][row["metric"]] = value
    return sorted(events.values(), key=lambda e: (e["date"], e["observed_at"] or ""), reverse=True)


def delete_provider_data(db_path: str, provider_id: str, profile_key: str | int | None = None) -> None:
    init_wearable_fact_db(db_path)
    scoped_profile = _normalize_profile_key(profile_key)
    provider = str(provider_id or "").strip()
    if not provider:
        return
    with sqlite3.connect(db_path) as conn:
        if provider == "open_wearables":
            conn.execute(
                "DELETE FROM wearable_daily_facts WHERE profile_key = ? AND source_system = ?",
                (scoped_profile, provider),
            )
        else:
            conn.execute(
                "DELETE FROM wearable_daily_facts WHERE profile_key = ? AND provider_id = ?",
                (scoped_profile, provider),
            )
        conn.execute(
            "DELETE FROM wearable_sources WHERE profile_key = ? AND provider_id = ?",
            (scoped_profile, provider),
        )




def latest_wearable_freshness(db_path: str, profile_key: str | int | None = None) -> dict:
    sources = list_wearable_sources(db_path, profile_key=profile_key)
    return {
        row["provider_id"]: {
            "status": row["status"],
            "last_data_point": row["last_data_point"],
            "last_sync_attempt": row["last_sync_attempt"],
        }
        for row in sources
    }
