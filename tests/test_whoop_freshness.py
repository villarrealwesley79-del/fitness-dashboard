from __future__ import annotations

import importlib
from datetime import datetime

import pytest

import whoop_store


@pytest.fixture()
def fitness_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "whoop-freshness-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "whoop-freshness-health-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    module.WHOOP_DB_FILE = str(tmp_path / "whoop.sqlite3")
    module.init_whoop_db(module.WHOOP_DB_FILE)
    return module


def test_latest_whoop_freshness_classifies_status(tmp_path):
    db_path = str(tmp_path / "whoop.sqlite3")
    whoop_store.init_whoop_db(db_path)
    whoop_store.upsert_whoop_records(
        db_path,
        "recovery",
        [
            {
                "upstream_id": "rec-1",
                "local_date": "2026-06-25",
                "score_state": "SCORED",
                "recovery_score": 75,
            }
        ],
        imported_at=datetime(2026, 6, 25, 6, 0, 0),
    )
    whoop_store.project_whoop_daily_facts(db_path)

    fresh = whoop_store.latest_whoop_freshness(db_path, now=datetime(2026, 6, 25, 12, 0, 0))
    aging = whoop_store.latest_whoop_freshness(db_path, now=datetime(2026, 6, 26, 12, 30, 0))
    stale = whoop_store.latest_whoop_freshness(db_path, now=datetime(2026, 6, 28, 12, 0, 0))

    assert fresh["status"] == "fresh"
    assert aging["status"] == "aging"
    assert stale["status"] == "stale"


def test_compute_data_freshness_includes_whoop_and_pending_score(fitness_app):
    whoop_store.upsert_whoop_records(
        fitness_app.WHOOP_DB_FILE,
        "recovery",
        [
            {
                "upstream_id": "rec-1",
                "local_date": "2026-06-25",
                "score_state": "PENDING_SCORE",
                "recovery_score": 52,
            }
        ],
        imported_at=datetime(2026, 6, 25, 7, 0, 0),
    )
    whoop_store.project_whoop_daily_facts(fitness_app.WHOOP_DB_FILE)

    freshness = fitness_app._compute_data_freshness(now=datetime(2026, 6, 25, 12, 0, 0))

    assert "whoop" in freshness
    assert freshness["whoop"]["status"] == "fresh"
    assert freshness["whoop"]["score_state"] == "PENDING_SCORE"


def test_whoop_status_endpoint_does_not_leak_tokens(fitness_app):
    whoop_store.save_connection_tokens(
        fitness_app.WHOOP_DB_FILE,
        {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
        },
    )

    response = fitness_app.app.test_client().get("/api/whoop/status")

    assert response.status_code == 200
    payload = response.get_json()
    body = response.get_data(as_text=True)
    assert payload["status"] in {"missing_config", "connected", "disconnected", "reauth_required", "error"}
    assert "access_token" not in body
    assert "refresh_token" not in body


def test_optional_disconnected_whoop_missing_does_not_lower_confidence(fitness_app):
    confidence = fitness_app._confidence_level_from(
        82,
        {
            "oura": {"status": "fresh"},
            "apple_health": {"status": "fresh"},
            "whoop": {"status": "missing", "connected": False, "last_data_point": None},
        },
    )

    assert confidence == "high"


def test_optional_disconnected_historical_whoop_does_not_lower_confidence(fitness_app):
    confidence = fitness_app._confidence_level_from(
        82,
        {
            "oura": {"status": "fresh"},
            "apple_health": {"status": "fresh"},
            "whoop": {"status": "stale", "connected": False, "last_data_point": "2026-06-01"},
        },
    )

    assert confidence == "high"
