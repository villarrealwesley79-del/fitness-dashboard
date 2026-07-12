"""Normalized wearable fact storage.

Only public, coaching-safe facts belong here. Raw provider payloads and secret
material are rejected before persistence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
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


@dataclass(frozen=True)
class WearableDailyFact:
    date: str
    provider_id: str
    source_label: str
    metric: str
    value: float | str | None
    unit: str | None = None
    band: str | None = None
    confidence: str = "unknown"
    freshness: str = "unknown"
    conflict_state: str | None = None
    category: str | None = None
    source_id: str | None = None
    source_provider: str | None = None
    original_label: str | None = None
    used_for_recommendation: bool = False
    updated_at: str | None = None

    def public_dict(self) -> dict:
        payload = asdict(self)
        if payload["updated_at"] is None:
            payload["updated_at"] = datetime.now().isoformat()
        return payload


def _scan_forbidden(value: object, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            field_name = str(key).strip().lower()
            if field_name in FORBIDDEN_FIELD_NAMES or field_name.endswith("_token"):
                hits.append(f"{path}.{field_name}" if path else field_name)
            hits.extend(_scan_forbidden(child, f"{path}.{field_name}" if path else field_name))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            hits.extend(_scan_forbidden(child, f"{path}[{idx}]"))
    return hits


def validate_public_fact_payload(payload: dict) -> None:
    hits = _scan_forbidden(payload)
    if hits:
        raise ValueError("wearable fact payload contains forbidden raw or secret fields")


def init_wearable_fact_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        _create_wearable_fact_tables(conn)
        _ensure_column(conn, "wearable_daily_facts", "profile_key", "TEXT NOT NULL DEFAULT '1'")
        _ensure_column(conn, "wearable_daily_facts", "category", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "source_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "wearable_daily_facts", "source_provider", "TEXT")
        _ensure_column(conn, "wearable_daily_facts", "original_label", "TEXT")
        _ensure_column(conn, "wearable_sources", "profile_key", "TEXT NOT NULL DEFAULT '1'")
        _migrate_profile_key_primary_keys(conn)


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
            used_for_recommendation INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (profile_key, date, provider_id, metric, source_id)
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
    if daily_pk != ["profile_key", "date", "provider_id", "metric", "source_id"]:
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
                used_for_recommendation INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (profile_key, date, provider_id, metric, source_id)
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO wearable_daily_facts (
                profile_key, date, provider_id, source_label, metric, value_json, unit, band,
                confidence, freshness, conflict_state, category, source_id, source_provider,
                original_label, used_for_recommendation, updated_at
            )
            SELECT COALESCE(profile_key, '1'), date, provider_id, source_label, metric, value_json, unit, band,
                confidence, freshness, conflict_state, category, COALESCE(source_id, ''), source_provider,
                original_label, used_for_recommendation, updated_at
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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row[1] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _normalize_profile_key(profile_key: str | int | None) -> str:
    text = str(profile_key or "1").strip()
    return text or "1"


def upsert_daily_facts(db_path: str, facts: list[WearableDailyFact | dict], profile_key: str | int | None = None) -> int:
    init_wearable_fact_db(db_path)
    now = datetime.now().isoformat()
    scoped_profile = _normalize_profile_key(profile_key)
    rows = []
    for fact in facts:
        payload = fact.public_dict() if isinstance(fact, WearableDailyFact) else dict(fact)
        validate_public_fact_payload(payload)
        payload["profile_key"] = _normalize_profile_key(payload.get("profile_key") or scoped_profile)
        payload["source_id"] = str(payload.get("source_id") or "")
        rows.append(payload)
    with sqlite3.connect(db_path) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO wearable_daily_facts (
                    profile_key, date, provider_id, source_label, metric, value_json, unit, band,
                    confidence, freshness, conflict_state, category, source_id, source_provider,
                    original_label, used_for_recommendation, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_key, date, provider_id, metric, source_id) DO UPDATE SET
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
        result.append({
            "source": row["provider_id"],
            "provider_id": row["provider_id"],
            "label": row["label"],
            "status": row["status"],
            "last_data_point": row["last_data_point"],
            "last_sync_attempt": row["last_sync_attempt"],
            "capabilities": json.loads(row["capabilities_json"] or "{}"),
            "used_for_recommendation": bool(row["used_for_recommendation"]),
        })
    return result


def list_recommendation_facts(db_path: str, limit: int = 30, profile_key: str | int | None = None) -> list[dict]:
    init_wearable_fact_db(db_path)
    scoped_profile = _normalize_profile_key(profile_key)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM wearable_daily_facts
            WHERE profile_key = ?
            ORDER BY date DESC, provider_id, metric
            LIMIT ?
            """,
            (scoped_profile, int(limit)),
        ).fetchall()
    facts = []
    for row in rows:
        facts.append({
            "date": row["date"],
            "provider_id": row["provider_id"],
            "source_label": row["source_label"],
            "metric": row["metric"],
            "value": json.loads(row["value_json"]) if row["value_json"] else None,
            "unit": row["unit"],
            "band": row["band"],
            "confidence": row["confidence"],
            "freshness": row["freshness"],
            "conflict_state": row["conflict_state"],
            "category": row["category"],
            "source_id": row["source_id"] or None,
            "source_provider": row["source_provider"],
            "original_label": row["original_label"],
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
