from __future__ import annotations

import copy
import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "COMPLETED_WORKOUTS", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_notify_workout_logged", lambda *_args, **_kwargs: None)
    return module


def _payload(workout_id="offline-workout-1", reps=10):
    return {
        "client_workout_id": workout_id,
        "offline": True,
        "attempt_id": "attempt-1",
        "client_created_at": "2026-05-18T08:00:00",
        "client_updated_at": "2026-05-18T08:30:00",
        "date": "2026-05-18",
        "session_type": "offline",
        "duration_minutes": 40,
        "exercises": [
            {
                "machine": "Chest Press",
                "muscle_group": "chest",
                "sets": [{"set_number": 1, "weight_lbs": 100, "reps": reps, "rpe": 7}],
            }
        ],
    }


def test_complete_workout_records_offline_sync_metadata(fitness_app):
    response = fitness_app.app.test_client().post("/api/complete-workout", json=_payload())

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["sync_status"] == "inserted"
    assert payload["workout_id"] == "offline-workout-1"
    assert len(fitness_app.WORKOUTS) == 1
    metadata = fitness_app.WORKOUTS[0]["offline_sync"]
    assert metadata["client_workout_id"] == "offline-workout-1"
    assert metadata["source"] == "offline_queue"
    assert metadata["sync_attempts"] == 1
    assert metadata["attempt_id"] == "attempt-1"
    assert metadata["fingerprint"]


def test_complete_workout_retry_with_same_client_id_is_already_synced(fitness_app):
    client = fitness_app.app.test_client()
    first_payload = _payload()

    first = client.post("/api/complete-workout", json=first_payload)
    second = client.post("/api/complete-workout", json=copy.deepcopy(first_payload))

    assert first.status_code == 200
    assert second.status_code == 200
    payload = second.get_json()
    assert payload["sync_status"] == "already_synced"
    assert payload["duplicate"] is True
    assert len(fitness_app.WORKOUTS) == 1
    assert fitness_app.WORKOUTS[0]["offline_sync"]["sync_attempts"] == 2


def test_complete_workout_retry_with_empty_exercises_is_rejected(fitness_app):
    client = fitness_app.app.test_client()

    first = client.post("/api/complete-workout", json=_payload())
    retry = client.post(
        "/api/complete-workout",
        json={"client_workout_id": "offline-workout-1", "exercises": []},
    )

    assert first.status_code == 200
    assert retry.status_code == 400
    payload = retry.get_json()
    assert payload["error"]["code"] == "invalid_field"
    assert payload["error"]["details"]["sync_status"] == "rejected"
    assert len(fitness_app.WORKOUTS) == 1


def test_complete_workout_retry_conflict_is_rejected_without_duplicate_save(fitness_app):
    client = fitness_app.app.test_client()

    first = client.post("/api/complete-workout", json=_payload(reps=10))
    conflict = client.post("/api/complete-workout", json=_payload(reps=12))

    assert first.status_code == 200
    assert conflict.status_code == 409
    payload = conflict.get_json()
    assert payload["error"]["code"] == "sync_conflict"
    assert payload["error"]["details"]["sync_status"] == "conflicted"
    assert len(fitness_app.WORKOUTS) == 1
    assert fitness_app.WORKOUTS[0]["offline_sync"]["sync_attempts"] == 2
    assert fitness_app.WORKOUTS[0]["offline_sync"]["last_attempt_at"]
    assert fitness_app.WORKOUTS[0]["offline_sync"]["conflict_count"] == 1


def test_complete_workout_validation_failure_reports_rejected_sync_status(fitness_app):
    response = fitness_app.app.test_client().post(
        "/api/complete-workout",
        json={"client_workout_id": "offline-workout-2", "exercises": []},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"]["code"] == "invalid_field"
    assert payload["error"]["details"]["sync_status"] == "rejected"
    assert fitness_app.WORKOUTS == []


def test_complete_workout_coercion_failure_reports_rejected_sync_status(fitness_app):
    bad_payload = _payload()
    bad_payload["notes"] = "x" * 2001

    response = fitness_app.app.test_client().post("/api/complete-workout", json=bad_payload)

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"]["code"] == "invalid_field"
    assert payload["error"]["details"]["sync_status"] == "rejected"
    assert fitness_app.WORKOUTS == []
