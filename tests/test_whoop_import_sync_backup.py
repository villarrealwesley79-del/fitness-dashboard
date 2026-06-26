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
