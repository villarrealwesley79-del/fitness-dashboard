from __future__ import annotations

import importlib
import io
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def fitness_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit95-apple-health-bridge-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit95-health-token")
    monkeypatch.setenv("APPLE_HEALTH_SYNC_DB", str(tmp_path / "apple-health-sync.db"))

    real_open = open
    fake_file_payloads = {
        ".env": "",
        "data_workouts.json": "[]",
        "data_soreness.json": "[]",
        "data_cardio.json": "[]",
        "data_recovery.json": "[]",
        "data_body.json": "[]",
        "data_sleep.json": "[]",
        "data_nutrition.json": "[]",
        "data_settings.json": "{}",
        "data_baselines.json": "{}",
    }

    def isolated_open(file, mode="r", *args, **kwargs):
        path = Path(os.fspath(file))
        if "r" in mode and path.parent == ROOT and path.name in fake_file_payloads:
            return io.StringIO(fake_file_payloads[path.name])
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", isolated_open)

    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)

    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "COMPLETED_WORKOUTS", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "RECOVERY_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_notify_workout_logged", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "get_oura_daily", lambda *_args, **_kwargs: {"readiness_score": 85})
    monkeypatch.setattr(module, "upsert_oura_daily", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "compute_hrv_trend", lambda *_args, **_kwargs: "unknown")
    monkeypatch.setattr(module, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0, "status": "ok"})
    monkeypatch.setattr(module, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "_fetch_wttr", lambda *_args, **_kwargs: {"available": False})
    monkeypatch.setattr(module, "_compute_data_freshness", lambda *_args, **_kwargs: {"apple_health": {"status": "fresh"}})
    monkeypatch.setattr(module, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})

    parser = importlib.import_module("apple_health_parser")
    monkeypatch.setattr(parser, "HEALTH_DIR", str(tmp_path / "health-export"))
    monkeypatch.setattr(parser, "parse_workouts", lambda: [])

    health_ingest = importlib.import_module("health_ingest")
    monkeypatch.setattr(health_ingest, "HEALTH_DIR", str(tmp_path / "health-export"))
    monkeypatch.setattr(health_ingest, "parse_workouts", lambda: [])

    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _today(module) -> str:
    return module._today_str()


def _sync_apple_health_workout(module, workout: dict) -> None:
    response = module.app.test_client().post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit95-health-token"},
        json={"workouts": [workout]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["inserted"] == 1


def _apple_health_walk(module, *, start_time: str = "07:00:00") -> dict:
    day = _today(module)
    start = f"{day}T{start_time}-05:00"
    return {
        "date": day,
        "startDate": start,
        "activity": "Walking",
        "activity_type": "Walking",
        "duration_min": 60,
        "duration_minutes": 60,
        "distance_m": 4828,
        "source": "health_auto_export",
    }


def test_file_based_apple_health_workout_prefers_local_start_date(fitness_app, monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("requires process timezone control")
    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()

    try:
        day = _today(fitness_app)
        stale_utc_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

        workout = fitness_app._normalise_apple_health_workout({
            "date": stale_utc_day,
            "start": f"{day}T23:30:00-05:00",
            "activity": "Walking",
            "duration_min": 60,
        })
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()

    assert workout["date"] == day


def test_apple_health_walk_increases_acwr_and_leg_readiness_load(fitness_app):
    _sync_apple_health_workout(fitness_app, _apple_health_walk(fitness_app))

    acwr = fitness_app.calculate_acwr(fitness_app.WORKOUTS)
    assert acwr["acute_load"] >= 60
    assert acwr["chronic_load"] >= 60

    readiness = fitness_app.get_readiness_score(
        "quads",
        [],
        {"quads": {"sets": 0, "volume_load": 0, "last_trained": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")}},
        fitness_app.CARDIO_DATA,
        fitness_app.WORKOUTS,
    )
    assert readiness["cardio_fatigue"] > 0
    assert readiness["score"] < 10


def test_apple_health_workout_hr_is_preserved_with_flag_off(fitness_app, monkeypatch):
    monkeypatch.delenv("APPLE_HEALTH_WORKOUT_HR_INTENSITY", raising=False)
    workout = _apple_health_walk(fitness_app)
    workout["avgHeartRate"] = {"qty": 165}
    _sync_apple_health_workout(fitness_app, workout)

    apple_workouts = fitness_app._load_apple_health_recommendation_workouts()
    assert len(apple_workouts) == 1
    assert apple_workouts[0]["avg_heart_rate"] == 165
    assert apple_workouts[0]["apple_health"]["avg_heart_rate"] == 165
    assert apple_workouts[0]["apple_health"]["hr_intensity_applied"] is False
    assert apple_workouts[0]["recommendation_load"] == 60

    cardio = fitness_app._cardio_data_with_apple_health([], fitness_app.WORKOUTS)
    assert cardio[0]["avg_heart_rate"] == 165
    assert cardio[0]["intensity"] == 5


def test_apple_health_workout_hr_can_raise_load_when_enabled(fitness_app, monkeypatch):
    monkeypatch.setenv("APPLE_HEALTH_WORKOUT_HR_INTENSITY", "1")
    workout = _apple_health_walk(fitness_app)
    workout["avg_heart_rate"] = 165
    _sync_apple_health_workout(fitness_app, workout)

    apple_workouts = fitness_app._load_apple_health_recommendation_workouts()
    assert apple_workouts[0]["apple_health"]["hr_intensity"] == 7
    assert apple_workouts[0]["apple_health"]["hr_intensity_applied"] is True
    assert apple_workouts[0]["recommendation_load"] == 420

    cardio = fitness_app._cardio_data_with_apple_health([], fitness_app.WORKOUTS)
    assert cardio[0]["intensity"] == 7

    acwr = fitness_app.calculate_acwr(fitness_app.WORKOUTS)
    assert acwr["acute_load"] >= 420


def test_apple_health_hr_does_not_raise_cardio_when_load_was_not_raised(fitness_app, monkeypatch):
    monkeypatch.setenv("APPLE_HEALTH_WORKOUT_HR_INTENSITY", "1")
    day = _today(fitness_app)
    _sync_apple_health_workout(
        fitness_app,
        {
            "date": day,
            "startDate": f"{day}T06:30:00-05:00",
            "activity": "Traditional Strength Training",
            "activity_type": "Traditional Strength Training",
            "duration_min": 30,
            "duration_minutes": 30,
            "avg_heart_rate": 172,
            "source": "health_auto_export",
            "muscle_groups": [
                {"muscle": "quads", "sets": 3, "volume_load": 900},
            ],
        },
    )

    apple_workouts = fitness_app._load_apple_health_recommendation_workouts()
    assert apple_workouts[0]["apple_health"]["hr_intensity_applied"] is False
    assert apple_workouts[0]["recommendation_load"] == 900

    cardio = fitness_app._cardio_data_with_apple_health([], fitness_app.WORKOUTS)
    assert cardio[0]["intensity"] == 5


def test_apple_health_hr_raises_cardio_when_zero_volume_uses_hr_load(fitness_app, monkeypatch):
    monkeypatch.setenv("APPLE_HEALTH_WORKOUT_HR_INTENSITY", "1")
    day = _today(fitness_app)
    _sync_apple_health_workout(
        fitness_app,
        {
            "date": day,
            "startDate": f"{day}T06:30:00-05:00",
            "activity": "Traditional Strength Training",
            "activity_type": "Traditional Strength Training",
            "duration_min": 30,
            "duration_minutes": 30,
            "avg_heart_rate": 172,
            "source": "health_auto_export",
            "muscle_groups": [
                {"muscle": "quads", "sets": 3},
            ],
        },
    )

    apple_workouts = fitness_app._load_apple_health_recommendation_workouts()
    assert apple_workouts[0]["apple_health"]["hr_intensity_applied"] is True
    assert apple_workouts[0]["recommendation_load"] == 240

    cardio = fitness_app._cardio_data_with_apple_health([], fitness_app.WORKOUTS)
    assert cardio[0]["intensity"] == 8


def test_apple_health_null_or_invalid_hr_keeps_existing_load(fitness_app, monkeypatch):
    monkeypatch.setenv("APPLE_HEALTH_WORKOUT_HR_INTENSITY", "true")
    workout = _apple_health_walk(fitness_app)
    workout["avg_heart_rate"] = 260
    _sync_apple_health_workout(fitness_app, workout)

    apple_workouts = fitness_app._load_apple_health_recommendation_workouts()
    assert apple_workouts[0]["avg_heart_rate"] is None
    assert apple_workouts[0]["recommendation_load"] == 60

    cardio = fitness_app._cardio_data_with_apple_health([], fitness_app.WORKOUTS)
    assert cardio[0]["intensity"] == 5


def test_daily_resting_hr_is_not_used_for_workout_intensity(fitness_app, monkeypatch):
    monkeypatch.setenv("APPLE_HEALTH_WORKOUT_HR_INTENSITY", "on")
    day = _today(fitness_app)
    response = fitness_app.app.test_client().post(
        "/api/apple-health/sync",
        headers={"X-Sync-Token": "fit95-health-token"},
        json={
            "heart_rate": [{"date": day, "value": 180, "type": "resting"}],
            "workouts": [_apple_health_walk(fitness_app)],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    apple_workouts = fitness_app._load_apple_health_recommendation_workouts()
    assert apple_workouts[0]["avg_heart_rate"] is None
    assert apple_workouts[0]["recommendation_load"] == 60

    cardio = fitness_app._cardio_data_with_apple_health([], fitness_app.WORKOUTS)
    assert cardio[0]["intensity"] == 5


def test_long_apple_health_duration_minutes_are_not_double_converted(fitness_app):
    workout = _apple_health_walk(fitness_app)
    workout["duration_min"] = 300
    workout["duration_minutes"] = 300
    _sync_apple_health_workout(fitness_app, workout)

    acwr = fitness_app.calculate_acwr(fitness_app.WORKOUTS)
    assert acwr["acute_load"] >= 300
    assert acwr["chronic_load"] >= 300


def test_raw_apple_health_duration_is_treated_as_seconds(fitness_app):
    workout = fitness_app._normalise_apple_health_workout({
        "date": _today(fitness_app),
        "startDate": f"{_today(fitness_app)}T07:00:00-05:00",
        "activity": "Walking",
        "duration": 300,
    })

    assert workout["duration_minutes"] == 5
    assert workout["recommendation_load"] == 5


def test_duplicate_apple_health_and_app_walk_counts_once(fitness_app):
    day = _today(fitness_app)
    start = f"{day}T07:00:00-05:00"
    app_walk = {
        "id": "app-walk-1",
        "date": day,
        "created_at": start,
        "session_type": "cardio",
        "duration_minutes": 60,
        "exercises": [],
        "cardio": {
            "type": "Walking",
            "duration_minutes": 60,
        },
        "source": "fitness_dashboard",
    }
    app_only = fitness_app.calculate_acwr([app_walk])
    fitness_app.WORKOUTS.append(app_walk)
    _sync_apple_health_workout(fitness_app, _apple_health_walk(fitness_app))

    acwr = fitness_app.calculate_acwr(fitness_app.WORKOUTS)
    assert acwr["acute_load"] == app_only["acute_load"] == 60
    assert acwr["chronic_load"] == app_only["chronic_load"]


def test_near_start_different_activity_is_not_deduped(fitness_app):
    day = _today(fitness_app)
    app_strength = {
        "id": "app-strength-1",
        "date": day,
        "start": f"{day}T07:00:00-05:00",
        "session_type": "Traditional Strength Training",
        "duration_minutes": 60,
        "exercises": [],
        "source": "fitness_dashboard",
    }
    fitness_app.WORKOUTS.append(app_strength)
    _sync_apple_health_workout(fitness_app, _apple_health_walk(fitness_app, start_time="07:02:00"))

    acwr = fitness_app.calculate_acwr(fitness_app.WORKOUTS)
    assert acwr["acute_load"] == 120


def test_date_only_app_walk_and_apple_health_walk_count_once_for_acwr(fitness_app):
    day = _today(fitness_app)
    app_walk = {
        "id": "legacy-app-walk-1",
        "date": day,
        "session_type": "cardio",
        "duration_minutes": 60,
        "exercises": [],
        "cardio": {
            "type": "Outdoor walk",
            "duration_minutes": 60,
        },
        "source": "fitness_dashboard",
    }
    app_only = fitness_app.calculate_acwr([app_walk])
    fitness_app.WORKOUTS.append(app_walk)
    _sync_apple_health_workout(fitness_app, _apple_health_walk(fitness_app))

    acwr = fitness_app.calculate_acwr(fitness_app.WORKOUTS)
    assert acwr["acute_load"] == app_only["acute_load"] == 60
    assert acwr["chronic_load"] == app_only["chronic_load"]


def test_app_walk_logged_after_apple_health_start_counts_once_for_acwr(fitness_app):
    day = _today(fitness_app)
    app_walk = {
        "id": "completed-app-walk-1",
        "date": day,
        "created_at": f"{day}T08:05:00-05:00",
        "session_type": "cardio",
        "duration_minutes": 60,
        "exercises": [],
        "cardio": {
            "type": "Outdoor walk",
            "duration_minutes": 60,
        },
        "source": "fitness_dashboard",
    }
    app_only = fitness_app.calculate_acwr([app_walk])
    fitness_app.WORKOUTS.append(app_walk)
    _sync_apple_health_workout(fitness_app, _apple_health_walk(fitness_app, start_time="07:00:00"))

    acwr = fitness_app.calculate_acwr(fitness_app.WORKOUTS)
    assert acwr["acute_load"] == app_only["acute_load"] == 60
    assert acwr["chronic_load"] == app_only["chronic_load"]


def test_date_only_app_cardio_and_apple_health_walk_count_once_for_fatigue(fitness_app):
    day = _today(fitness_app)
    fitness_app.CARDIO_DATA.append({
        "date": day,
        "activity_type": "Outdoor walk",
        "duration_minutes": 60,
        "intensity": 5,
    })
    app_only = fitness_app.get_cardio_muscle_impact(fitness_app.CARDIO_DATA, "quads")
    _sync_apple_health_workout(fitness_app, _apple_health_walk(fitness_app))

    readiness = fitness_app.get_readiness_score(
        "quads",
        [],
        {"quads": {"sets": 0, "volume_load": 0, "last_trained": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")}},
        fitness_app.CARDIO_DATA,
        fitness_app.WORKOUTS,
    )
    assert readiness["cardio_fatigue"] == app_only


def test_apple_health_strength_workout_contributes_to_muscle_volume_and_readiness(fitness_app):
    day = _today(fitness_app)
    _sync_apple_health_workout(
        fitness_app,
        {
            "date": day,
            "startDate": f"{day}T06:30:00-05:00",
            "activity": "Traditional Strength Training",
            "activity_type": "Traditional Strength Training",
            "duration_min": 30,
            "duration_minutes": 30,
            "source": "health_auto_export",
            "muscle_groups": [
                {"muscle": "quads", "sets": 3, "volume_load": 900},
            ],
        },
    )

    volume = fitness_app.calculate_volume(fitness_app.WORKOUTS, weeks=4)
    assert volume["quads"]["sets"] == 3
    assert volume["quads"]["volume_load"] == 900

    readiness = fitness_app.get_readiness_score(
        "quads",
        [],
        volume,
        fitness_app.CARDIO_DATA,
        fitness_app.WORKOUTS,
    )
    assert readiness["recovery_debt"] == 2
    assert readiness["score"] <= 8


def test_apple_health_other_is_ignored_but_basketball_counts(fitness_app):
    day = _today(fitness_app)
    _sync_apple_health_workout(
        fitness_app,
        {
            "date": day,
            "startDate": f"{day}T08:00:00-05:00",
            "workoutActivityType": 25,
            "duration_minutes": 14,
            "source": "health_auto_export",
        },
    )
    _sync_apple_health_workout(
        fitness_app,
        {
            "date": day,
            "startDate": f"{day}T12:28:05-05:00",
            "workoutActivityType": 37,
            "duration_minutes": 95.3,
            "source": "health_auto_export",
        },
    )

    apple_workouts = fitness_app._load_apple_health_recommendation_workouts()

    assert [w["session_type"] for w in apple_workouts] == ["Basketball"]
    assert apple_workouts[0]["duration_minutes"] == 95.3
    acwr = fitness_app.calculate_acwr(fitness_app.WORKOUTS)
    assert acwr["acute_load"] >= 95


def test_smart_recommendation_includes_apple_health_load_in_readiness_factors(fitness_app):
    _sync_apple_health_workout(fitness_app, _apple_health_walk(fitness_app))

    response = fitness_app.app.test_client().get("/api/recommendation/smart")
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()

    acwr = payload["readiness_factors"]["acwr"]
    assert acwr["acute_load"] >= 60
    assert acwr["chronic_load"] >= 60


def test_smart_recommendation_mentions_hr_only_when_hr_raises_load(fitness_app, monkeypatch):
    monkeypatch.setenv("APPLE_HEALTH_WORKOUT_HR_INTENSITY", "1")
    workout = _apple_health_walk(fitness_app)
    workout["avg_heart_rate"] = 172
    _sync_apple_health_workout(fitness_app, workout)

    response = fitness_app.app.test_client().get("/api/recommendation/smart")
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert "Apple Health workout HR raised recent cardio load" in payload["reasoning"]

    monkeypatch.delenv("APPLE_HEALTH_WORKOUT_HR_INTENSITY", raising=False)
    response = fitness_app.app.test_client().get("/api/recommendation/smart")
    payload = response.get_json()
    assert "Apple Health workout HR raised recent cardio load" not in payload["reasoning"]
