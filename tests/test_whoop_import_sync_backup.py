from __future__ import annotations

import importlib

import pytest

import whoop_client
import whoop_store


@pytest.fixture()
def fitness_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "whoop-import-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "whoop-import-health-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    module.WHOOP_DB_FILE = str(tmp_path / "whoop.sqlite3")
    module.init_whoop_db(module.WHOOP_DB_FILE)
    return module


def test_whoop_csv_import_projects_facts_and_lists_history(fitness_app):
    csv_text = "\n".join(
        [
            "record_type,local_date,recovery_score,sleep_performance_pct,sleep_need_gap_min,strain,score_state",
            "recovery,2026-06-25,42,,,,SCORED",
            "sleep,2026-06-25,,68,95,,SCORED",
            "cycle,2026-06-25,,,,18.4,SCORED",
        ]
    )

    response = fitness_app.app.test_client().post("/api/whoop/import-csv", json={"csv": csv_text})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["import"]["records_upserted"] == 3
    fact = whoop_store.get_daily_fact(fitness_app.WHOOP_DB_FILE, local_date="2026-06-25")
    assert fact["recovery_score"] == 42
    assert fact["sleep_performance_pct"] == 68
    assert fact["strain"] == 18.4

    imports = fitness_app.app.test_client().get("/api/whoop/imports")
    assert imports.status_code == 200
    assert imports.get_json()["imports"][0]["reason"] == "csv_import"


def test_whoop_csv_import_can_drive_local_recommendation_signals_without_oauth(fitness_app):
    csv_text = "\n".join(
        [
            "record_type,local_date,recovery_score,score_state",
            f"recovery,{fitness_app._today_str()},38,SCORED",
        ]
    )
    client = fitness_app.app.test_client()

    import_response = client.post("/api/whoop/import-csv", json={"csv": csv_text})
    signal_response = client.get("/api/whoop/recommendation-signals")

    assert import_response.status_code == 200
    assert signal_response.status_code == 200
    payload = signal_response.get_json()
    assert payload["signals"]["display_only"] is False
    assert payload["signals"]["source_kind"] == "csv_only"
    assert "deload" in payload["signals"]["applied_modifiers"]


def test_whoop_csv_import_preserves_zero_metric_values(fitness_app):
    csv_text = "\n".join(
        [
            "record_type,local_date,recovery_score,sleep_performance_pct,sleep_need_gap_min,strain,score_state",
            "recovery,2026-06-25,0,,,,SCORED",
            "sleep,2026-06-25,,0,0,,SCORED",
            "cycle,2026-06-25,,,,0,SCORED",
        ]
    )

    response = fitness_app.app.test_client().post("/api/whoop/import-csv", json={"csv": csv_text})

    assert response.status_code == 200
    fact = whoop_store.get_daily_fact(fitness_app.WHOOP_DB_FILE, local_date="2026-06-25")
    assert fact["recovery_score"] == 0
    assert fact["sleep_performance_pct"] == 0
    assert fact["sleep_need_gap_min"] == 0
    assert fact["strain"] == 0


def test_whoop_api_normalization_uses_local_start_date_with_timezone_offset(fitness_app):
    row = fitness_app._normalize_whoop_record(
        "sleep",
        {
            "id": "sleep-late",
            "start": "2026-06-26T03:30:00.000Z",
            "end": "2026-06-26T11:00:00.000Z",
            "timezone_offset": "-05:00",
            "score": {"sleep_performance_percentage": 82},
        },
    )

    assert row["local_date"] == "2026-06-25"


def test_whoop_api_normalization_marks_calibrating_recovery_display_only(fitness_app):
    row = fitness_app._normalize_whoop_record(
        "recovery",
        {
            "id": "recovery-calibrating",
            "date": "2026-06-25",
            "score": {"recovery_score": 75, "user_calibrating": True},
        },
    )

    assert row["score_state"] == "CALIBRATING"


def test_whoop_api_normalization_imports_official_sleep_needed_components(fitness_app):
    row = fitness_app._normalize_whoop_record(
        "sleep",
        {
            "id": "sleep-needed",
            "date": "2026-06-25",
            "score": {
                "sleep_performance_percentage": 70,
                "sleep_needed": {
                    "baseline_milli": 28_800_000,
                    "need_from_sleep_debt_milli": 3_600_000,
                    "need_from_strain_milli": 1_800_000,
                },
            },
        },
    )

    assert row["sleep_need_gap_min"] == 171


def test_whoop_api_normalization_does_not_treat_total_sleep_need_as_gap_when_sleep_is_complete(fitness_app):
    row = fitness_app._normalize_whoop_record(
        "sleep",
        {
            "id": "sleep-complete",
            "date": "2026-06-25",
            "score": {
                "sleep_performance_percentage": 100,
                "sleep_needed": {"baseline_milli": 28_800_000},
            },
        },
    )

    assert row["sleep_need_gap_min"] == 0


def test_whoop_csv_import_rejects_large_payload_and_row_flood(fitness_app):
    client = fitness_app.app.test_client()
    too_large = "record_type,local_date,recovery_score\n" + ("x" * (fitness_app.WHOOP_CSV_MAX_BYTES + 1))

    large_response = client.post("/api/whoop/import-csv", json={"csv": too_large})

    assert large_response.status_code == 413
    assert large_response.get_json()["error"]["code"] == "whoop_csv_too_large"

    original_limit = fitness_app.WHOOP_CSV_MAX_ROWS
    fitness_app.WHOOP_CSV_MAX_ROWS = 1
    try:
        row_flood = "\n".join(
            [
                "record_type,local_date,recovery_score",
                "recovery,2026-06-25,42",
                "recovery,2026-06-26,43",
            ]
        )
        flood_response = client.post("/api/whoop/import-csv", json={"csv": row_flood})
    finally:
        fitness_app.WHOOP_CSV_MAX_ROWS = original_limit

    assert flood_response.status_code == 413
    assert flood_response.get_json()["error"]["code"] == "whoop_csv_too_many_rows"


def test_whoop_csv_import_rejects_impossible_metric_values(fitness_app):
    csv_text = "\n".join(
        [
            "record_type,local_date,recovery_score,strain,sleep_performance_pct",
            "recovery,2026-06-25,-500,,",
        ]
    )

    response = fitness_app.app.test_client().post("/api/whoop/import-csv", json={"csv": csv_text})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_whoop_csv_metric"


def test_whoop_csv_import_rejects_non_numeric_metric_values(fitness_app):
    csv_text = "\n".join(
        [
            "record_type,local_date,recovery_score",
            "recovery,2026-06-25,not-a-number",
        ]
    )

    response = fitness_app.app.test_client().post("/api/whoop/import-csv", json={"csv": csv_text})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_whoop_csv_metric"


def test_whoop_csv_import_rejects_future_dates(fitness_app):
    csv_text = "\n".join(
        [
            "record_type,local_date,recovery_score",
            "recovery,9999-12-31,50",
        ]
    )

    response = fitness_app.app.test_client().post("/api/whoop/import-csv", json={"csv": csv_text})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_whoop_csv_metric"


def test_whoop_sync_window_uses_utc_aware_datetimes(fitness_app):
    start, end = fitness_app._whoop_sync_window(2)

    assert start.tzinfo is not None
    assert end.tzinfo is not None
    assert end.isoformat().endswith("+00:00")


def test_whoop_manual_sync_uses_protected_material_and_normalizes_records(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "_whoop_config_for_redirect",
        lambda redirect_uri: whoop_client.WhoopConfig("client-id", "safe-placeholder", redirect_uri),
    )
    whoop_store.save_connection_tokens(
        fitness_app.WHOOP_DB_FILE,
        {
            "access_token": "runtime-access",
            "refresh_token": "runtime-refresh",
            "expires_in": 3600,
            "scope": "offline read:recovery",
        },
    )

    class FakeClient:
        def __init__(self, config, *, session_value, renewal_value):
            assert session_value == "runtime-access"
            assert renewal_value == "runtime-refresh"
            self.access_token = session_value
            self.refresh_token = renewal_value

        def fetch_recovery(self, *, start=None, end=None):
            return [{"id": "rec-1", "date": "2026-06-25", "score": {"recovery_score": 39}}]

        def fetch_sleep(self, *, start=None, end=None):
            return [{"id": "sleep-1", "date": "2026-06-25", "score": {"sleep_performance_percentage": 72}}]

        def fetch_cycles(self, *, start=None, end=None):
            return [{"id": "cycle-1", "date": "2026-06-25", "score": {"strain": 16.2}}]

        def fetch_workouts(self, *, start=None, end=None):
            return []

    result, err = fitness_app._run_whoop_sync("manual", client_factory=FakeClient)

    assert err is None
    assert result["records_upserted"] == 3
    fact = whoop_store.get_daily_fact(fitness_app.WHOOP_DB_FILE, local_date="2026-06-25")
    assert fact["recovery_score"] == 39
    assert fact["sleep_performance_pct"] == 72
    assert fact["strain"] == 16.2


def test_whoop_manual_sync_persists_rotated_material_before_later_failure(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "_whoop_config_for_redirect",
        lambda redirect_uri: whoop_client.WhoopConfig("client-id", "safe-placeholder", redirect_uri),
    )
    whoop_store.save_connection_tokens(
        fitness_app.WHOOP_DB_FILE,
        {
            "access_token": "old-session",
            "refresh_token": "old-renewal",
            "expires_in": 3600,
            "scope": "offline read:recovery",
        },
    )

    class RotatingThenFailingClient:
        def __init__(self, config, *, session_value, renewal_value):
            self.access_token = session_value
            self.refresh_token = renewal_value

        def fetch_recovery(self, *, start=None, end=None):
            self.access_token = "fresh-session"
            self.refresh_token = "fresh-renewal"
            raise whoop_client.WhoopApiError("recovery failed after refresh", retryable=True)

        def fetch_sleep(self, *, start=None, end=None):
            raise whoop_client.WhoopApiError("sleep failed", retryable=True)

    with fitness_app.app.app_context():
        result, err = fitness_app._run_whoop_sync("manual", client_factory=RotatingThenFailingClient)

    assert result is None
    assert err is not None
    material = whoop_store.load_connection_token_material(fitness_app.WHOOP_DB_FILE)
    assert material["session_value"] == "fresh-session"
    assert material["renewal_value"] == "fresh-renewal"


def test_whoop_sync_rejects_invalid_days_back_before_network(fitness_app):
    client = fitness_app.app.test_client()

    bad_type = client.post("/api/whoop/sync", json={"days_back": "forever"})
    zero = client.post("/api/whoop/sync", json={"days_back": 0})
    too_large = client.post("/api/whoop/sync", json={"days_back": 3650})
    negative = client.post("/api/whoop/sync", json={"days_back": -1})

    assert bad_type.status_code == 400
    assert zero.status_code == 400
    assert too_large.status_code == 400
    assert negative.status_code == 400
    assert bad_type.get_json()["error"]["code"] == "invalid_days_back"


def test_whoop_sync_terminal_auth_failure_marks_reauth_required(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "_whoop_config_for_redirect",
        lambda redirect_uri: whoop_client.WhoopConfig("client-id", "safe-placeholder", redirect_uri),
    )
    whoop_store.save_connection_tokens(
        fitness_app.WHOOP_DB_FILE,
        {
            "access_token": "old-session",
            "refresh_token": "old-renewal",
            "expires_in": 3600,
            "scope": "offline read:recovery",
        },
    )

    class AuthFailingClient:
        def __init__(self, config, *, session_value, renewal_value):
            self.access_token = session_value
            self.refresh_token = renewal_value

        def fetch_recovery(self, *, start=None, end=None):
            raise whoop_client.WhoopApiError("invalid refresh token", status_code=401, retryable=False)

    with fitness_app.app.app_context():
        result, err = fitness_app._run_whoop_sync("manual", client_factory=AuthFailingClient)

    assert result is None
    assert err is not None
    status = whoop_store.get_connection_status(fitness_app.WHOOP_DB_FILE)
    assert status["status"] == "reauth_required"
    assert status["reauth_required"] is True


def test_backup_exports_only_normalized_whoop_facts_not_tokens(fitness_app):
    whoop_store.save_connection_tokens(
        fitness_app.WHOOP_DB_FILE,
        {
            "access_token": "backup-access",
            "refresh_token": "backup-refresh",
            "expires_in": 3600,
        },
    )
    whoop_store.upsert_whoop_records(
        fitness_app.WHOOP_DB_FILE,
        "recovery",
        [{"upstream_id": "rec-1", "local_date": "2026-06-25", "score_state": "SCORED", "recovery_score": 55}],
    )
    whoop_store.project_whoop_daily_facts(fitness_app.WHOOP_DB_FILE)

    response = fitness_app.app.test_client().get("/api/export-backup")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "whoop_daily_facts" in body
    assert "backup-access" not in body
    assert "backup-refresh" not in body
    assert "token_ref" not in body
