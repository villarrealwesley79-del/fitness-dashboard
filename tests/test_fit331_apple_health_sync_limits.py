from __future__ import annotations

import importlib
import json
import sqlite3

from flask import Request


def _client(monkeypatch, tmp_path):
    db_path = tmp_path / "apple-health-sync.db"
    monkeypatch.setenv("SECRET_KEY", "fit331-contract-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit331-health-token")
    monkeypatch.setenv("APPLE_HEALTH_SYNC_DB", str(db_path))
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    return module.app.test_client(), db_path


def _latest_attempt(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            """SELECT outcome_code, rejection_counts_json, payload_bytes, total_count
               FROM ah_sync_events ORDER BY id DESC LIMIT 1"""
        ).fetchone()


def test_sync_rejects_oversized_body_before_json_materialization(monkeypatch, tmp_path):
    client, db_path = _client(monkeypatch, tmp_path)
    parser = importlib.import_module("apple_health_parser")
    sentinel = "private-health-value-must-not-be-persisted"
    body = json.dumps({"padding": sentinel + ("x" * parser.APPLE_HEALTH_MAX_REQUEST_BYTES)}).encode()

    def fail_if_json_is_parsed(*_args, **_kwargs):
        raise AssertionError("oversized request reached JSON parsing")

    monkeypatch.setattr(Request, "get_json", fail_if_json_is_parsed)
    response = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        data=body,
        content_type="application/json",
    )

    assert response.status_code == 413
    assert response.get_json() == {
        "status": "rejected",
        "code": "payload_too_large",
        "inserted": 0,
        "skipped": 1,
        "rejection_counts": {"payload_too_large": 1},
    }
    outcome, rejection_json, payload_bytes, total_count = _latest_attempt(db_path)
    assert outcome == "payload_too_large"
    assert json.loads(rejection_json) == {"payload_too_large": 1}
    assert payload_bytes == len(body)
    assert total_count == 0
    assert sentinel not in db_path.read_bytes().decode("utf-8", errors="ignore")


def test_sync_rejects_attempt_over_record_limit(monkeypatch, tmp_path):
    client, db_path = _client(monkeypatch, tmp_path)
    parser = importlib.import_module("apple_health_parser")
    record_count = parser.APPLE_HEALTH_MAX_RECORDS_PER_SYNC + 1

    response = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        json={"steps": [{"date": "2026-07-12", "value": index} for index in range(record_count)]},
    )

    assert response.status_code == 413
    assert response.get_json() == {
        "status": "rejected",
        "code": "record_limit_exceeded",
        "inserted": 0,
        "skipped": record_count,
        "rejection_counts": {"record_limit_exceeded": record_count},
    }
    outcome, rejection_json, _payload_bytes, total_count = _latest_attempt(db_path)
    assert outcome == "record_limit_exceeded"
    assert json.loads(rejection_json) == {"record_limit_exceeded": record_count}
    assert total_count == record_count


def test_sync_record_limit_cannot_be_bypassed_by_mixed_payload_shape(monkeypatch, tmp_path):
    client, _db_path = _client(monkeypatch, tmp_path)
    parser = importlib.import_module("apple_health_parser")
    record_count = parser.APPLE_HEALTH_MAX_RECORDS_PER_SYNC + 1

    response = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        json={
            "data": {},
            "steps": [{"date": "2026-07-12", "value": index} for index in range(record_count)],
        },
    )

    assert response.status_code == 413
    assert response.get_json()["code"] == "record_limit_exceeded"
    assert response.get_json()["rejection_counts"] == {
        "record_limit_exceeded": record_count
    }


def test_sync_record_limit_counts_nested_rows_when_flat_shape_is_present(monkeypatch, tmp_path):
    client, _db_path = _client(monkeypatch, tmp_path)
    parser = importlib.import_module("apple_health_parser")
    record_count = parser.APPLE_HEALTH_MAX_RECORDS_PER_SYNC + 1

    response = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        json={
            "steps": [],
            "data": {
                "metrics": [
                    {"name": "step_count", "data": [{} for _ in range(record_count)]}
                ]
            },
        },
    )

    assert response.status_code == 413
    assert response.get_json()["rejection_counts"] == {
        "record_limit_exceeded": record_count
    }


def test_sync_reports_native_metric_rows_ignored_by_flat_shape(monkeypatch, tmp_path):
    client, _db_path = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        json={
            "steps": [],
            "metrics": [
                {"name": "step_count", "data": [{"date": "2026-07-12", "qty": 1}]}
            ],
        },
    )

    assert response.status_code == 200
    assert response.get_json()["inserted"] == 0
    assert response.get_json()["skipped"] == 1
    assert response.get_json()["rejection_counts"] == {"invalid_record": 1}


def test_sync_record_limit_counts_unsupported_native_metrics(monkeypatch, tmp_path):
    client, _db_path = _client(monkeypatch, tmp_path)
    parser = importlib.import_module("apple_health_parser")
    record_count = parser.APPLE_HEALTH_MAX_RECORDS_PER_SYNC + 1

    response = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        json={
            "data": {
                "metrics": [
                    {"name": "future_metric", "data": [{} for _ in range(record_count)]}
                ]
            }
        },
    )

    assert response.status_code == 413
    assert response.get_json()["rejection_counts"] == {
        "record_limit_exceeded": record_count
    }


def test_sync_reports_invalid_and_duplicate_rows_by_stable_reason(monkeypatch, tmp_path):
    client, db_path = _client(monkeypatch, tmp_path)
    private_value = "raw-private-row-value"
    payload = {
        "steps": [
            {"date": "2026-07-12", "value": 1234},
            {"date": "not-a-date", "value": private_value},
            {"date": "2026-99-99", "value": private_value},
            private_value,
        ]
    }

    first = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        json=payload,
    )
    duplicate = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        json={"steps": [{"date": "2026-07-12", "value": 1234}]},
    )

    assert first.status_code == 200
    assert first.get_json()["inserted"] == 1
    assert first.get_json()["skipped"] == 3
    assert first.get_json()["rejection_counts"] == {"invalid_record": 3}
    assert duplicate.status_code == 200
    assert duplicate.get_json()["inserted"] == 0
    assert duplicate.get_json()["skipped"] == 1
    assert duplicate.get_json()["rejection_counts"] == {"duplicate_record": 1}

    with sqlite3.connect(db_path) as conn:
        summaries = conn.execute(
            "SELECT outcome_code, rejection_counts_json FROM ah_sync_events ORDER BY id"
        ).fetchall()
    assert summaries == [
        ("partial", '{"invalid_record":3}'),
        ("rejected", '{"duplicate_record":1}'),
    ]
    assert private_value not in "".join(summary for _outcome, summary in summaries)


def test_sync_reports_invalid_native_hae_metric_rows(monkeypatch, tmp_path):
    client, _db_path = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        json={
            "data": {
                "metrics": [
                    {
                        "name": "step_count",
                        "data": [
                            {"date": "2026-07-12", "qty": 1000},
                            {"qty": 2000},
                            "not-a-row",
                        ],
                    },
                    {"name": "future_unsupported_metric", "data": [{"value": 1}]},
                ]
            }
        },
    )

    assert response.status_code == 200
    assert response.get_json()["inserted"] == 1
    assert response.get_json()["skipped"] == 3
    assert response.get_json()["rejection_counts"] == {"invalid_record": 3}


def test_sync_accepts_flat_hrv_rows_without_false_rejection(monkeypatch, tmp_path):
    client, _db_path = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit331-health-token"},
        json={"hrv": [{"date": "2026-07-12", "value": 45}]},
    )

    assert response.status_code == 200
    assert response.get_json()["inserted"] == 1
    assert response.get_json()["skipped"] == 0
    assert response.get_json()["rejection_counts"] == {}
