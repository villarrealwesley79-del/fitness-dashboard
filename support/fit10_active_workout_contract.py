#!/usr/bin/env python3
"""FIT-10 contract checks for active workout save behavior."""

import copy
import os
import sys

os.environ.setdefault("SECRET_KEY", "fit10-contract-secret")
os.environ.setdefault("HEALTH_SYNC_TOKEN", "fit10-contract-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import auth


def assert_in_file(path, needle):
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read()
    if needle not in body:
        raise AssertionError(f"{path} missing {needle!r}")


def post_complete(client, payload):
    return client.post("/api/complete-workout", json=payload)


def main():
    original_workouts = copy.deepcopy(app.WORKOUTS)
    original_completed = copy.deepcopy(app.COMPLETED_WORKOUTS)
    original_cardio = copy.deepcopy(app.CARDIO_DATA)
    original_save_json = app.save_json
    original_notify = app._notify_workout_logged
    original_public_prefixes = auth._PUBLIC_PREFIXES
    app.save_json = lambda *args, **kwargs: None
    app._notify_workout_logged = lambda *args, **kwargs: None
    if "/api/complete-workout" not in auth._PUBLIC_PREFIXES:
        auth._PUBLIC_PREFIXES = tuple(auth._PUBLIC_PREFIXES) + ("/api/complete-workout",)
    app.app.config["TESTING"] = True

    try:
        client = app.app.test_client()
        payload = {
            "id": "fit10-contract-workout",
            "date": "2026-05-18",
            "recommendation_id": "fit10-reco",
            "session_type": "Contract",
            "exercises": [
                {
                    "machine": "Chest Press",
                    "muscle_group": "chest",
                    "sets": [{"set_number": 1, "weight_lbs": 100, "reps": 10, "rpe": 7.5}],
                }
            ],
        }
        before = len(app.WORKOUTS)
        first = post_complete(client, payload)
        assert first.status_code == 200, first.get_data(as_text=True)
        first_json = first.get_json()
        assert first_json["workout_id"] == payload["id"], first_json
        assert len(app.WORKOUTS) == before + 1, len(app.WORKOUTS)

        second = post_complete(client, payload)
        assert second.status_code == 200, second.get_data(as_text=True)
        second_json = second.get_json()
        assert second_json["workout_id"] == payload["id"], second_json
        assert second_json.get("duplicate") is True, second_json
        assert len(app.WORKOUTS) == before + 1, len(app.WORKOUTS)

        invalid = post_complete(client, {**payload, "id": "bad id with spaces"})
        assert invalid.status_code == 400, invalid.get_data(as_text=True)

        assert_in_file("templates/index.html", "active-workout-status")
        assert_in_file("static/js/app.js", "workoutSaveErrorMessage")
        assert_in_file("static/js/app.js", "Offline: workout is still open")
        assert_in_file("static/js/app.js", "Validation failed: log at least one set")
        assert_in_file("static/js/app.js", "if (!aw.exercises.length) aw.saveState = null")
        assert_in_file("static/css/style.css", ".active-workout-status")
        print("FIT10_ACTIVE_WORKOUT_CONTRACT_OK")
    finally:
        app.WORKOUTS[:] = original_workouts
        app.COMPLETED_WORKOUTS[:] = original_completed
        app.CARDIO_DATA[:] = original_cardio
        app.save_json = original_save_json
        app._notify_workout_logged = original_notify
        auth._PUBLIC_PREFIXES = original_public_prefixes


if __name__ == "__main__":
    main()
