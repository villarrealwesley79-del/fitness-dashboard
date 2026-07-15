"""Normalized wearable fact storage.

Only public, coaching-safe facts belong here. Raw provider payloads and secret
material are rejected before persistence.
"""

from __future__ import annotations

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

WEARABLE_FACT_SCHEMA_VERSION = 2


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
    with sqlite3.connect(db_path) as conn:
        _create_wearable_fact_tables(conn)
        _ensure_column(conn, "wearable_daily_facts", "profile_key", "TEXT NOT NULL DEFAULT '1'")
        _ensure_column(conn, "wearable_daily_facts", "category", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "source_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "wearable_daily_facts", "source_provider", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "original_label", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "observed_at", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "source_system", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "source_record_kind", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "metric_domain", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "capability_state", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "source_last_synced_at", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "imported_at", "TEXT")
        _ensure_column(conn, "wearable_sources", "profile_key", "TEXT NOT NULL DEFAULT '1'")
        _migrate_profile_key_primary_keys(conn)
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version < WEARABLE_FACT_SCHEMA_VERSION:
            _backfill_fact_contract(conn)
            _migrate_open_wearables_provider_identity(conn)
            conn.execute(f"PRAGMA user_version = {WEARABLE_FACT_SCHEMA_VERSION}")


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


def _migrate_profile_key_primary_keys(conn: sqlite3.Connection) -> None:
    daily_pk = _primary_key_columns(conn, "wearable_daily_facts")
    if daily_pk != ["profile_key", "date", "provider_id", "source_system", "metric", "source_id"]:
        conn.execute("ALTER TABLE wearable_daily_facts RENAME TO wearable_daily_facts_legacy")
        conn.execute(
            """
            CREATE TABLE wearable_daily_facts (
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
            INSERT OR REPLACE INTO wearable_daily_facts (
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
            """
        )
        conn.execute("DROP TABLE wearable_daily_facts_legacy")

    sources_pk = _primary_key_columns(conn, "wearable_sources")
    if sources_pk != ["profile_key", "provider_id"]:
        conn.execute("ALTER TABLE wearable_sources RENAME TO wearable_sources_legacy")
        conn.execute(
            """
            CREATE TABLE wearable_sources (
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
        conn.execute(
            """
            INSERT OR REPLACE INTO wearable_sources (
                profile_key, provider_id, label, status, last_data_point, last_sync_attempt,
                capabilities_json, used_for_recommendation, updated_at
            )
            SELECT COALESCE(profile_key, '1'), provider_id, label, status, last_data_point, last_sync_attempt,
                capabilities_json, used_for_recommendation, updated_at
            FROM wearable_sources_legacy
            """
        )
        conn.execute("DROP TABLE wearable_sources_legacy")


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
        "imported_at = COALESCE(imported_at, updated_at), "
        "used_for_recommendation = CASE "
        "WHEN COALESCE(NULLIF(source_system, ''), provider_id) = 'open_wearables' "
        "AND freshness IN ('fresh', 'aging') THEN 1 "
        "ELSE used_for_recommendation END "
        "WHERE source_system IS NULL OR source_system = '' "
        "OR source_record_kind IS NULL OR source_record_kind = '' "
        "OR metric_domain IS NULL OR metric_domain = '' "
        "OR capability_state IS NULL OR capability_state = '' "
        "OR source_last_synced_at IS NULL OR imported_at IS NULL "
        "OR (COALESCE(NULLIF(source_system, ''), provider_id) = 'open_wearables' "
        "AND freshness IN ('fresh', 'aging') AND used_for_recommendation = 0)"
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


def upsert_daily_facts(
    db_path: str,
    facts: list[WearableDailyFact | dict],
    profile_key: str | int | None = None,
    replace_source_ids: set[str] | None = None,
    replace_fact_scopes: set[tuple[str, str, str, str]] | None = None,
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
        payload["source_id"] = str(payload.get("source_id") or "")
        payload["source_system"] = str(payload.get("source_system") or payload["provider_id"])
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
) -> list[dict]:
    init_wearable_fact_db(db_path)
    scoped_profile = _normalize_profile_key(profile_key)
    usable_cutoff = (datetime.now().date() - timedelta(days=1)).isoformat()
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
            AND (observed_at IS NULL OR datetime(observed_at) <= datetime(?))
        ))
    """
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
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), int(limit),
            ),
        ).fetchall()
    facts = []
    for row in rows:
        effective_freshness = row["freshness"]
        if usable_only:
            if row["observed_at"]:
                observed = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    observed = observed.astimezone()
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
