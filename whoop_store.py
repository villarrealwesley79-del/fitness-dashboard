from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timedelta


WHOOP_MATERIAL_REF = "fitness-dashboard-whoop-oauth-material"
WHOOP_RECORD_FIELDS = (
    "recovery_score",
    "recovery_band",
    "strain",
    "sleep_performance_pct",
    "sleep_need_gap_min",
    "workout_kj",
    "hrv_rmssd",
    "resting_hr",
    "respiratory_rate",
    "spo2",
    "skin_temp",
    "percent_recorded",
)


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _iso_now(now: datetime | None = None) -> str:
    return (now or datetime.now()).replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except Exception:
            return None


def _classify_freshness(last_data_point: str | None, *, now: datetime | None = None) -> str:
    dt = _parse_iso(last_data_point)
    if dt is None:
        return "missing"
    current = now or datetime.now()
    age_hours = (current - dt).total_seconds() / 3600.0
    if age_hours < 24:
        return "fresh"
    if age_hours < 48:
        return "aging"
    return "stale"


def init_whoop_db(db_path: str) -> None:
    with closing(_connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS whoop_connection (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT NOT NULL DEFAULT 'disconnected',
                connected_at TEXT,
                disconnected_at TEXT,
                last_successful_sync_at TEXT,
                last_sync_attempt_at TEXT,
                last_error TEXT,
                scopes_json TEXT,
                material_ref TEXT,
                access_token_expires_at TEXT,
                reauth_required INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS whoop_oauth_states (
                state TEXT PRIMARY KEY,
                redirect_uri TEXT NOT NULL,
                user_binding TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS whoop_sync_runs (
                run_id TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                window_start TEXT,
                window_end TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                records_upserted INTEGER NOT NULL DEFAULT 0,
                retryable INTEGER NOT NULL DEFAULT 0,
                redacted_error TEXT
            );

            CREATE TABLE IF NOT EXISTS whoop_records (
                record_type TEXT NOT NULL,
                upstream_id TEXT NOT NULL,
                local_date TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                score_state TEXT,
                recovery_score REAL,
                recovery_band TEXT,
                strain REAL,
                sleep_performance_pct REAL,
                sleep_need_gap_min REAL,
                workout_kj REAL,
                hrv_rmssd REAL,
                resting_hr REAL,
                respiratory_rate REAL,
                spo2 REAL,
                skin_temp REAL,
                percent_recorded REAL,
                upstream_updated_at TEXT,
                imported_at TEXT NOT NULL,
                sync_run_id TEXT,
                PRIMARY KEY (record_type, upstream_id)
            );

            CREATE TABLE IF NOT EXISTS whoop_daily_facts (
                local_date TEXT PRIMARY KEY,
                provider TEXT NOT NULL DEFAULT 'whoop',
                recovery_score REAL,
                recovery_band TEXT,
                strain REAL,
                sleep_performance_pct REAL,
                sleep_need_gap_min REAL,
                workout_kj REAL,
                hrv_rmssd REAL,
                resting_hr REAL,
                respiratory_rate REAL,
                spo2 REAL,
                skin_temp REAL,
                percent_recorded REAL,
                score_state TEXT,
                last_sync_attempt TEXT,
                projected_at TEXT NOT NULL
            );
            """
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(whoop_connection)").fetchall()
        }
        if "material_ref" not in columns:
            conn.execute("ALTER TABLE whoop_connection ADD COLUMN material_ref TEXT")
        state_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(whoop_oauth_states)").fetchall()
        }
        if "user_binding" not in state_columns:
            conn.execute("ALTER TABLE whoop_oauth_states ADD COLUMN user_binding TEXT")
        # Defensive cleanup for unreleased/local draft schemas that may have
        # materialized OAuth secrets in SQLite before the protected store existed.
        if "access_token" in columns:
            conn.execute("UPDATE whoop_connection SET access_token = NULL")
        if "refresh_token" in columns:
            conn.execute("UPDATE whoop_connection SET refresh_token = NULL")
        conn.execute(
            """
            INSERT INTO whoop_connection (id, status, updated_at)
            VALUES (1, 'disconnected', ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (_iso_now(),),
        )
        conn.commit()


def _protected_material_path(db_path: str) -> str:
    override = os.environ.get("WHOOP_PROTECTED_MATERIAL_DIR", "").strip()
    if override:
        base_dir = os.path.abspath(os.path.expanduser(override))
    else:
        db_dir = os.path.dirname(os.path.abspath(db_path))
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        try:
            db_inside_repo = os.path.commonpath([db_dir, repo_dir]) == repo_dir
        except ValueError:
            db_inside_repo = False
        if db_inside_repo:
            base_dir = os.path.expanduser("~/Library/Application Support/Fitness Dashboard/secrets")
        else:
            base_dir = db_dir
    return os.path.join(base_dir, ".whoop-protected-material.json")


def _write_protected_material(db_path: str, token_payload: dict) -> str:
    material = {
        "session_value": token_payload.get("access_token"),
        "renewal_value": token_payload.get("refresh_token"),
        "stored_at": _iso_now(),
    }
    target = _protected_material_path(db_path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".whoop-protected-material.", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(material, handle)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
        os.chmod(target, 0o600)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return WHOOP_MATERIAL_REF


def load_connection_token_material(db_path: str) -> dict:
    path = _protected_material_path(db_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "session_value": payload.get("session_value"),
        "renewal_value": payload.get("renewal_value"),
    }


def delete_connection_token_material(db_path: str) -> None:
    try:
        os.unlink(_protected_material_path(db_path))
    except FileNotFoundError:
        return
    except OSError:
        return


def create_oauth_state(
    db_path: str,
    *,
    state: str,
    redirect_uri: str,
    user_binding: str | None = None,
    created_at: datetime | None = None,
    ttl_minutes: int = 10,
) -> dict:
    init_whoop_db(db_path)
    created = created_at or datetime.now()
    expires = created + timedelta(minutes=ttl_minutes)
    payload = {
        "state": state,
        "redirect_uri": redirect_uri,
        "user_binding": user_binding,
        "created_at": _iso_now(created),
        "expires_at": _iso_now(expires),
    }
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO whoop_oauth_states (state, redirect_uri, user_binding, created_at, expires_at, consumed_at)
            VALUES (:state, :redirect_uri, :user_binding, :created_at, :expires_at, NULL)
            """,
            payload,
        )
        conn.commit()
    return payload


def consume_oauth_state(
    db_path: str,
    state: str,
    *,
    user_binding: str | None = None,
    now: datetime | None = None,
) -> dict | None:
    init_whoop_db(db_path)
    current = now or datetime.now()
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT state, redirect_uri, user_binding, created_at, expires_at, consumed_at FROM whoop_oauth_states WHERE state = ?",
            (state,),
        ).fetchone()
        if row is None:
            return None
        if (
            row["consumed_at"]
            or (_parse_iso(row["expires_at"]) and _parse_iso(row["expires_at"]) < current)
            or ((row["user_binding"] or None) != (user_binding or None))
        ):
            conn.execute("DELETE FROM whoop_oauth_states WHERE state = ?", (state,))
            conn.commit()
            return None
        consumed_at = _iso_now(current)
        conn.execute(
            "UPDATE whoop_oauth_states SET consumed_at = ? WHERE state = ?",
            (consumed_at, state),
        )
        conn.commit()
        return {
            "state": row["state"],
            "redirect_uri": row["redirect_uri"],
            "user_binding": row["user_binding"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "consumed_at": consumed_at,
        }


def _token_expiry(token_payload: dict, *, now: datetime | None = None) -> str | None:
    expires_in = token_payload.get("expires_in")
    try:
        seconds = int(expires_in)
    except Exception:
        return None
    return _iso_now((now or datetime.now()) + timedelta(seconds=seconds))


def save_connection_tokens(
    db_path: str,
    token_payload: dict,
    *,
    connected_at: datetime | None = None,
    last_error: str | None = None,
) -> None:
    init_whoop_db(db_path)
    now = connected_at or datetime.now()
    scopes = token_payload.get("scope")
    if isinstance(scopes, str):
        scopes_json = json.dumps([item for item in scopes.split() if item])
    elif isinstance(scopes, (list, tuple)):
        scopes_json = json.dumps(list(scopes))
    else:
        scopes_json = json.dumps([])
    material_ref = _write_protected_material(db_path, token_payload)
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO whoop_connection (
                id, status, connected_at, disconnected_at, last_successful_sync_at, last_sync_attempt_at,
                last_error, scopes_json, material_ref, access_token_expires_at,
                reauth_required, updated_at
            ) VALUES (
                1, 'connected', :connected_at, NULL, NULL, NULL, :last_error, :scopes_json,
                :material_ref, :access_token_expires_at, 0, :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
                status = 'connected',
                connected_at = excluded.connected_at,
                disconnected_at = NULL,
                last_error = excluded.last_error,
                scopes_json = excluded.scopes_json,
                material_ref = excluded.material_ref,
                "access_token_expires_at" = excluded."access_token_expires_at",
                reauth_required = 0,
                updated_at = excluded.updated_at
            """,
            {
                "connected_at": _iso_now(now),
                "last_error": last_error,
                "scopes_json": scopes_json,
                "material_ref": material_ref,
                "access_token_expires_at": _token_expiry(token_payload, now=now),
                "updated_at": _iso_now(now),
            },
        )
        conn.commit()


def rotate_connection_tokens(
    db_path: str,
    token_payload: dict,
    *,
    rotated_at: datetime | None = None,
) -> None:
    init_whoop_db(db_path)
    now = rotated_at or datetime.now()
    material_ref = _write_protected_material(db_path, token_payload)
    with closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM whoop_connection WHERE id = 1"
        ).fetchone()
        if row is None:
            conn.rollback()
            raise ValueError("WHOOP connection state does not exist.")
        conn.execute(
            """
            UPDATE whoop_connection
            SET material_ref = ?,
                "access_token_expires_at" = ?,
                status = 'connected',
                reauth_required = 0,
                updated_at = ?
            WHERE id = 1
            """,
            (
                material_ref,
                _token_expiry(token_payload, now=now),
                _iso_now(now),
            ),
        )
        conn.commit()


def mark_reauth_required(db_path: str, *, message: str | None = None, flagged_at: datetime | None = None) -> None:
    init_whoop_db(db_path)
    now = flagged_at or datetime.now()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE whoop_connection
            SET status = 'reauth_required',
                reauth_required = 1,
                last_error = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (message, _iso_now(now)),
        )
        conn.commit()


def get_connection_status(db_path: str, *, include_private: bool = False) -> dict:
    init_whoop_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM whoop_connection WHERE id = 1").fetchone()
    if row is None:
        return {"status": "disconnected"}
    payload = {
        "status": row["status"],
        "connected_at": row["connected_at"],
        "disconnected_at": row["disconnected_at"],
        "last_successful_sync_at": row["last_successful_sync_at"],
        "last_sync_attempt_at": row["last_sync_attempt_at"],
        "last_error": row["last_error"],
        "scopes": json.loads(row["scopes_json"] or "[]"),
        "access_token_expires_at": row["access_token_expires_at"],
        "material_ref": row["material_ref"],
        "reauth_required": bool(row["reauth_required"]),
        "updated_at": row["updated_at"],
    }
    if include_private:
        payload["protected_material_available"] = bool(load_connection_token_material(db_path).get("session_value"))
    return payload


def disconnect_whoop(db_path: str, *, disconnected_at: datetime | None = None) -> None:
    init_whoop_db(db_path)
    now = disconnected_at or datetime.now()
    delete_connection_token_material(db_path)
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE whoop_connection
            SET status = 'disconnected',
                disconnected_at = ?,
                material_ref = NULL,
                "access_token_expires_at" = NULL,
                reauth_required = 0,
                updated_at = ?,
                last_error = NULL
            WHERE id = 1
            """,
            (_iso_now(now), _iso_now(now)),
        )
        conn.commit()


def record_whoop_sync_run(
    db_path: str,
    *,
    reason: str,
    window_start: str | None = None,
    window_end: str | None = None,
    status: str = "started",
    started_at: datetime | None = None,
) -> str:
    init_whoop_db(db_path)
    run_id = uuid.uuid4().hex
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO whoop_sync_runs (
                run_id, reason, window_start, window_end, status, started_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, reason, window_start, window_end, status, _iso_now(started_at)),
        )
        conn.execute(
            "UPDATE whoop_connection SET last_sync_attempt_at = ?, updated_at = ? WHERE id = 1",
            (_iso_now(started_at), _iso_now(started_at)),
        )
        conn.commit()
    return run_id


def finish_whoop_sync_run(
    db_path: str,
    run_id: str,
    *,
    status: str,
    completed_at: datetime | None = None,
    records_upserted: int = 0,
    retryable: bool = False,
    redacted_error: str | None = None,
) -> None:
    init_whoop_db(db_path)
    now = completed_at or datetime.now()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE whoop_sync_runs
            SET status = ?, completed_at = ?, records_upserted = ?, retryable = ?, redacted_error = ?
            WHERE run_id = ?
            """,
            (status, _iso_now(now), int(records_upserted), 1 if retryable else 0, redacted_error, run_id),
        )
        if status == "success":
            conn.execute(
                """
                UPDATE whoop_connection
                SET last_successful_sync_at = ?, last_error = NULL, updated_at = ?
                WHERE id = 1
                """,
                (_iso_now(now), _iso_now(now)),
            )
        elif redacted_error:
            conn.execute(
                "UPDATE whoop_connection SET last_error = ?, updated_at = ? WHERE id = 1",
                (redacted_error, _iso_now(now)),
            )
        conn.commit()


def upsert_whoop_records(
    db_path: str,
    record_type: str,
    records: list[dict],
    *,
    sync_run_id: str | None = None,
    imported_at: datetime | None = None,
) -> int:
    init_whoop_db(db_path)
    imported = _iso_now(imported_at)
    count = 0
    with closing(_connect(db_path)) as conn:
        for record in records or []:
            upstream_id = str(record.get("upstream_id") or record.get("id") or "").strip()
            local_date = str(record.get("local_date") or "").strip()
            if not upstream_id or not local_date:
                continue
            values = {
                "record_type": record_type,
                "upstream_id": upstream_id,
                "local_date": local_date,
                "start_time": record.get("start_time"),
                "end_time": record.get("end_time"),
                "score_state": record.get("score_state"),
                "recovery_score": record.get("recovery_score"),
                "recovery_band": record.get("recovery_band"),
                "strain": record.get("strain"),
                "sleep_performance_pct": record.get("sleep_performance_pct"),
                "sleep_need_gap_min": record.get("sleep_need_gap_min"),
                "workout_kj": record.get("workout_kj"),
                "hrv_rmssd": record.get("hrv_rmssd"),
                "resting_hr": record.get("resting_hr"),
                "respiratory_rate": record.get("respiratory_rate"),
                "spo2": record.get("spo2"),
                "skin_temp": record.get("skin_temp"),
                "percent_recorded": record.get("percent_recorded"),
                "upstream_updated_at": record.get("upstream_updated_at"),
                "imported_at": imported,
                "sync_run_id": sync_run_id,
            }
            conn.execute(
                """
                INSERT INTO whoop_records (
                    record_type, upstream_id, local_date, start_time, end_time, score_state,
                    recovery_score, recovery_band, strain, sleep_performance_pct,
                    sleep_need_gap_min, workout_kj, hrv_rmssd, resting_hr, respiratory_rate,
                    spo2, skin_temp, percent_recorded, upstream_updated_at, imported_at, sync_run_id
                ) VALUES (
                    :record_type, :upstream_id, :local_date, :start_time, :end_time, :score_state,
                    :recovery_score, :recovery_band, :strain, :sleep_performance_pct,
                    :sleep_need_gap_min, :workout_kj, :hrv_rmssd, :resting_hr, :respiratory_rate,
                    :spo2, :skin_temp, :percent_recorded, :upstream_updated_at, :imported_at, :sync_run_id
                )
                ON CONFLICT(record_type, upstream_id) DO UPDATE SET
                    local_date = excluded.local_date,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    score_state = excluded.score_state,
                    recovery_score = excluded.recovery_score,
                    recovery_band = excluded.recovery_band,
                    strain = excluded.strain,
                    sleep_performance_pct = excluded.sleep_performance_pct,
                    sleep_need_gap_min = excluded.sleep_need_gap_min,
                    workout_kj = excluded.workout_kj,
                    hrv_rmssd = excluded.hrv_rmssd,
                    resting_hr = excluded.resting_hr,
                    respiratory_rate = excluded.respiratory_rate,
                    spo2 = excluded.spo2,
                    skin_temp = excluded.skin_temp,
                    percent_recorded = excluded.percent_recorded,
                    upstream_updated_at = excluded.upstream_updated_at,
                    imported_at = excluded.imported_at,
                    sync_run_id = excluded.sync_run_id
                """,
                values,
            )
            count += 1
        conn.commit()
    return count


def project_whoop_daily_facts(db_path: str, *, projected_at: datetime | None = None) -> int:
    init_whoop_db(db_path)
    rows: list[sqlite3.Row]
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT record_type, upstream_id, local_date, score_state, imported_at,
                   recovery_score, recovery_band, strain, sleep_performance_pct,
                   sleep_need_gap_min, workout_kj, hrv_rmssd, resting_hr,
                   respiratory_rate, spo2, skin_temp, percent_recorded
            FROM whoop_records
            ORDER BY local_date ASC, imported_at ASC
            """
        ).fetchall()
        if not rows:
            conn.execute("DELETE FROM whoop_daily_facts")
            conn.commit()
            return 0

        by_day: dict[str, dict] = {}
        for row in rows:
            local_date = row["local_date"]
            fact = by_day.setdefault(
                local_date,
                {
                    "local_date": local_date,
                    "provider": "whoop",
                    "recovery_score": None,
                    "recovery_band": None,
                    "strain": None,
                    "sleep_performance_pct": None,
                    "sleep_need_gap_min": None,
                    "workout_kj": None,
                    "hrv_rmssd": None,
                    "resting_hr": None,
                    "respiratory_rate": None,
                    "spo2": None,
                    "skin_temp": None,
                    "percent_recorded": None,
                    "score_state": row["score_state"] or "unscored",
                    "_has_recovery_state": False,
                    "_has_scored_recovery": False,
                    "last_sync_attempt": row["imported_at"],
                    "projected_at": _iso_now(projected_at),
                },
            )
            if row["imported_at"] and (fact["last_sync_attempt"] is None or row["imported_at"] > fact["last_sync_attempt"]):
                fact["last_sync_attempt"] = row["imported_at"]
            score_state = row["score_state"]
            if row["record_type"] == "recovery":
                fact["_has_recovery_state"] = True
                if score_state == "SCORED":
                    fact["_has_scored_recovery"] = True
                    fact["score_state"] = "SCORED"
                elif not fact["_has_scored_recovery"] and score_state:
                    fact["score_state"] = score_state
            elif score_state == "SCORED" and not fact["_has_recovery_state"]:
                fact["score_state"] = "SCORED"
            elif fact["score_state"] != "SCORED" and score_state and not fact["_has_recovery_state"]:
                fact["score_state"] = score_state
            if score_state != "SCORED":
                continue
            for field in WHOOP_RECORD_FIELDS:
                value = row[field]
                if value is not None:
                    fact[field] = value

        conn.execute("DELETE FROM whoop_daily_facts")
        for fact in by_day.values():
            conn.execute(
                """
                INSERT INTO whoop_daily_facts (
                    local_date, provider, recovery_score, recovery_band, strain,
                    sleep_performance_pct, sleep_need_gap_min, workout_kj, hrv_rmssd,
                    resting_hr, respiratory_rate, spo2, skin_temp, percent_recorded,
                    score_state, last_sync_attempt, projected_at
                ) VALUES (
                    :local_date, :provider, :recovery_score, :recovery_band, :strain,
                    :sleep_performance_pct, :sleep_need_gap_min, :workout_kj, :hrv_rmssd,
                    :resting_hr, :respiratory_rate, :spo2, :skin_temp, :percent_recorded,
                    :score_state, :last_sync_attempt, :projected_at
                )
                """,
                fact,
            )
        conn.commit()
    return len(rows)


def get_daily_fact(db_path: str, *, local_date: str | None = None) -> dict | None:
    init_whoop_db(db_path)
    with closing(_connect(db_path)) as conn:
        if local_date:
            row = conn.execute(
                "SELECT * FROM whoop_daily_facts WHERE local_date = ?",
                (local_date,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM whoop_daily_facts ORDER BY local_date DESC LIMIT 1"
            ).fetchone()
    return dict(row) if row is not None else None


def latest_whoop_freshness(db_path: str, *, now: datetime | None = None) -> dict:
    init_whoop_db(db_path)
    fact = get_daily_fact(db_path)
    connection = get_connection_status(db_path)
    connection_status = connection.get("status")
    reauth_required = bool(connection.get("reauth_required") or connection_status == "reauth_required")
    if fact is None:
        return {
            "status": "missing",
            "last_data_point": None,
            "last_sync_attempt": connection.get("last_successful_sync_at") or connection.get("last_sync_attempt_at"),
            "score_state": None,
            "connected": connection_status == "connected",
            "connection_status": connection_status,
            "reauth_required": reauth_required,
        }
    return {
        "status": _classify_freshness(fact.get("local_date"), now=now),
        "last_data_point": fact.get("local_date"),
        "last_sync_attempt": fact.get("last_sync_attempt"),
        "score_state": fact.get("score_state"),
        "connected": connection_status == "connected",
        "connection_status": connection_status,
        "reauth_required": reauth_required,
    }


def list_whoop_sync_runs(db_path: str, *, reason: str | None = None, limit: int = 20) -> list[dict]:
    init_whoop_db(db_path)
    limit = max(1, min(int(limit or 20), 100))
    with closing(_connect(db_path)) as conn:
        if reason:
            rows = conn.execute(
                """
                SELECT run_id, reason, window_start, window_end, status, started_at, completed_at,
                       records_upserted, retryable, redacted_error
                FROM whoop_sync_runs
                WHERE reason = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (reason, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT run_id, reason, window_start, window_end, status, started_at, completed_at,
                       records_upserted, retryable, redacted_error
                FROM whoop_sync_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def list_whoop_daily_facts(db_path: str, *, limit: int = 30) -> list[dict]:
    init_whoop_db(db_path)
    limit = max(1, min(int(limit or 30), 366))
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM whoop_daily_facts
            ORDER BY local_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
