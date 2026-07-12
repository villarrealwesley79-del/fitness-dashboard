from datetime import datetime, timedelta
import importlib
import logging

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    return module


@pytest.mark.parametrize(
    ("age_hours", "expected"),
    [
        (1, "fresh"),
        (23, "fresh"),
        (25, "aging"),
        (47, "aging"),
        (49, "stale"),
        (96, "stale"),
    ],
)
def test_classify_freshness_boundaries(fitness_app, age_hours, expected):
    now = datetime(2026, 5, 18, 12, 0, 0)
    observed = fitness_app._classify_freshness(now - timedelta(hours=age_hours), now=now)
    assert observed == expected


def test_classify_freshness_missing(fitness_app):
    assert fitness_app._classify_freshness(None) == "missing"


@pytest.mark.parametrize(
    ("readiness", "freshness", "expected"),
    [
        (
            82,
            {"oura": {"status": "fresh"}, "apple_health": {"status": "fresh"}},
            "high",
        ),
        (
            72,
            {"oura": {"status": "aging"}, "apple_health": {"status": "fresh"}},
            "medium",
        ),
        (
            88,
            {"oura": {"status": "stale"}, "apple_health": {"status": "fresh"}},
            "low",
        ),
        (
            88,
            {"oura": {"status": "missing"}, "apple_health": {"status": "missing"}},
            "low",
        ),
    ],
)
def test_confidence_level_from_wearable_freshness(
    fitness_app, readiness, freshness, expected
):
    assert fitness_app._confidence_level_from(readiness, freshness) == expected


def test_recommendation_smart_route_shape_without_server_or_cookie(fitness_app, monkeypatch):
    freshness = {
        "oura": {
            "status": "fresh",
            "last_data_point": "2026-05-18",
            "last_sync_attempt": "2026-05-18T07:00:00",
            "source": "live",
        },
        "apple_health": {
            "status": "fresh",
            "last_data_point": "2026-05-18",
            "last_sync_attempt": "2026-05-18T07:00:00",
        },
        "food": {
            "status": "missing",
            "last_data_point": None,
            "last_sync_attempt": None,
            "pending_review": False,
            "target_state": "none",
            "calories": 0,
            "protein_g": 0.0,
            "calories_target": 2200,
            "protein_target_g": 148.0,
            "calories_pct": 0,
            "protein_pct": 0,
        },
    }

    monkeypatch.setattr(
        fitness_app,
        "get_oura_daily",
        lambda *_args, **_kwargs: {
            "readiness_score": 82,
            "sleep_score": 78,
            "hrv": 41,
        },
    )
    monkeypatch.setattr(
        fitness_app, "get_oura_daily_range", lambda *_args, **_kwargs: [{"hrv": 41}]
    )
    monkeypatch.setattr(
        fitness_app, "compute_hrv_trend", lambda *_args, **_kwargs: "stable"
    )
    monkeypatch.setattr(
        fitness_app, "calculate_acwr", lambda *_args, **_kwargs: {"acwr": 1.0}
    )
    monkeypatch.setattr(
        fitness_app,
        "calculate_sleep_debt",
        lambda *_args, **_kwargs: {"debt_minutes": 0, "status": "ok", "sample_count": 1},
    )
    monkeypatch.setattr(
        fitness_app,
        "calculate_recovery_bonus",
        lambda *_args, **_kwargs: {"bonus_points": 0},
    )
    monkeypatch.setattr(
        fitness_app, "_fetch_wttr", lambda *_args, **_kwargs: {"available": False}
    )
    monkeypatch.setattr(
        fitness_app, "_compute_data_freshness", lambda *_args, **_kwargs: freshness
    )
    monkeypatch.setattr(
        fitness_app,
        "_latest_oura_freshness",
        lambda *_args, **_kwargs: ("fresh", "2026-05-18", "2026-05-18T07:00:00"),
    )

    response = fitness_app.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    payload = response.get_json()
    assert "freshness" in payload
    assert "confidence_level" in payload
    assert payload["confidence_level"] == "high"
    assert payload["recommendation_sources"]["source_proof"]["oura"] == {
        "used_for_recommendation": True,
        "fields_used": ["readiness_score", "sleep_debt_minutes"],
        "modifier_applied": False,
        "ignored_reason": None,
    }


def _freshness_payload():
    return {
        "oura": {"status": "missing", "last_data_point": None, "last_sync_attempt": None},
        "apple_health": {"status": "missing", "last_data_point": None, "last_sync_attempt": None},
        "food": {
            "status": "missing",
            "last_data_point": None,
            "last_sync_attempt": None,
            "pending_review": False,
            "target_state": "none",
            "calories": 0,
            "protein_g": 0.0,
            "calories_target": 2200,
            "protein_target_g": 148.0,
            "calories_pct": 0,
            "protein_pct": 0,
        },
    }


def _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, *, oura_row):
    monkeypatch.setattr(fitness_app, "WORKOUTS", [])
    monkeypatch.setattr(fitness_app, "SORENESS_DATA", [])
    monkeypatch.setattr(fitness_app, "RECOVERY_DATA", [])
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args, **_kwargs: oura_row)
    monkeypatch.setattr(fitness_app, "get_oura_daily_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fitness_app, "compute_hrv_trend", lambda *_args, **_kwargs: "unknown")
    monkeypatch.setattr(
        fitness_app,
        "calculate_acwr",
        lambda *_args, **_kwargs: {"acute_load": 0, "chronic_load": 0, "acwr": 0, "risk": "low"},
    )
    monkeypatch.setattr(
        fitness_app,
        "calculate_sleep_debt",
        lambda *_args, **_kwargs: {"debt_minutes": 0, "status": "ok"},
    )
    monkeypatch.setattr(
        fitness_app,
        "calculate_recovery_bonus",
        lambda *_args, **_kwargs: {"bonus_points": 0},
    )
    monkeypatch.setattr(fitness_app, "_compute_data_freshness", lambda *_args, **_kwargs: _freshness_payload())
    monkeypatch.setattr(fitness_app, "_latest_oura_freshness", lambda *_args, **_kwargs: ("missing", None, None))
    monkeypatch.setattr(fitness_app, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(fitness_app, "_food_log_entries_for_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fitness_app, "_workout_looks_hard", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        fitness_app,
        "generate_next_workout",
        lambda *_args, **_kwargs: {"name": "Test workout", "cardio": {}},
    )


def test_recommendation_smart_reports_stale_oura_inputs_it_still_consumes(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(
        monkeypatch,
        fitness_app,
        oura_row={"readiness_score": 20, "sleep_score": 30, "hrv": 10},
    )
    stale = _freshness_payload()
    stale["oura"] = {
        "status": "stale",
        "last_data_point": "2026-05-01",
        "last_sync_attempt": "2026-05-01T07:00:00",
    }
    monkeypatch.setattr(fitness_app, "_compute_data_freshness", lambda *_args, **_kwargs: stale)
    monkeypatch.setattr(
        fitness_app,
        "_latest_oura_freshness",
        lambda *_args, **_kwargs: ("stale", "2026-05-01", "2026-05-01T07:00:00"),
    )

    payload = fitness_app.app.test_client().get("/api/recommendation/smart").get_json()

    assert payload["recommendation"] == "recovery"
    assert payload["recommendation_sources"]["source_proof"]["oura"] == {
        "used_for_recommendation": True,
        "fields_used": ["readiness_score"],
        "modifier_applied": True,
        "ignored_reason": None,
    }


def test_recommendation_smart_discloses_sparse_hrv_trend_when_it_changes_recommendation(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(
        monkeypatch,
        fitness_app,
        oura_row={"readiness_score": 80, "sleep_score": 78, "hrv": 41},
    )
    monkeypatch.setattr(fitness_app, "get_oura_daily_range", lambda *_args, **_kwargs: [{"hrv": 41}])
    monkeypatch.setattr(fitness_app, "compute_hrv_trend", lambda *_args, **_kwargs: "declining")

    payload = fitness_app.app.test_client().get("/api/recommendation/smart").get_json()

    assert payload["recommendation"] == "recovery"
    assert "hrv_trend" in payload["recommendation_sources"]["source_proof"]["oura"]["fields_used"]


def test_recommendation_smart_uses_cache_only_weather_empty_cache(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(
        monkeypatch,
        fitness_app,
        oura_row={"readiness_score": 82, "sleep_score": 78, "hrv": 41},
    )
    monkeypatch.setattr(fitness_app, "_WEATHER_CACHE", {"location": "San_Antonio"})
    monkeypatch.setattr(
        fitness_app,
        "_fetch_wttr",
        lambda *_args, **_kwargs: pytest.fail("/api/recommendation/smart must not fetch live weather"),
    )

    response = fitness_app.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["weather"] is None


def test_recommendation_smart_uses_warm_cached_weather_without_live_fetch(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(
        monkeypatch,
        fitness_app,
        oura_row={"readiness_score": 82, "sleep_score": 78, "hrv": 41},
    )
    monkeypatch.setattr(
        fitness_app,
        "_WEATHER_CACHE",
        {
            "location": "San_Antonio",
            "ts": int(fitness_app.time.time()),
            "data": {
                "temp_f": 98,
                "feelslike_f": 98,
                "humidity_pct": 50,
                "condition": "Hot",
            },
        },
    )
    monkeypatch.setattr(
        fitness_app,
        "_fetch_wttr",
        lambda *_args, **_kwargs: pytest.fail("/api/recommendation/smart must not fetch live weather"),
    )

    response = fitness_app.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    weather = response.get_json()["weather"]
    assert weather["source"] == "cache"
    assert weather["temp_f"] == 98
    assert response.get_json()["recommendation_sources"]["source_proof"]["weather"]["fields_used"] == [
        "feelslike_f"
    ]


def test_recommendation_smart_does_not_claim_out_of_window_apple_health_workouts(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, oura_row=None)
    monkeypatch.setattr(
        fitness_app,
        "_recommendation_workouts_with_apple_health",
        lambda *_args, **_kwargs: [
            {
                "date": "2000-01-01",
                "source": "apple_health",
                "recommendation_load": 100,
                "apple_health": {"hr_intensity_applied": True},
            }
        ],
    )

    payload = fitness_app.app.test_client().get("/api/recommendation/smart").get_json()

    assert payload["recommendation_sources"]["source_proof"]["apple_health"] == {
        "used_for_recommendation": False,
        "fields_used": [],
        "modifier_applied": False,
        "ignored_reason": "missing_data",
    }


def test_recommendation_smart_oura_cache_miss_does_not_fetch_live_oura(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, oura_row=None)
    monkeypatch.setattr(fitness_app, "_WEATHER_CACHE", {"location": "San_Antonio"})
    monkeypatch.setattr(
        fitness_app,
        "_fetch_wttr",
        lambda *_args, **_kwargs: pytest.fail("/api/recommendation/smart must not fetch live weather"),
    )

    class FailingOuraClient:
        def __init__(self):
            pytest.fail("/api/recommendation/smart must not fetch live Oura data")

    monkeypatch.setattr(fitness_app, "OuraClient", FailingOuraClient)

    response = fitness_app.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["readiness"] is None
    assert payload["sleep_score"] is None
    assert payload["hrv"] is None
    assert set(payload) == {
        "recommendation",
        "readiness",
        "effective_readiness",
        "sleep_score",
        "hrv",
        "hrv_trend",
        "readiness_factors",
        "avoid_muscles",
        "recently_trained",
        "last_completed",
        "suggested_workout",
        "weather",
        "time_of_day",
        "history_context",
        "reasoning",
        "freshness",
        "nutrition_context",
        "wearable_sources",
        "recommendation_sources",
        "next_workout",
        "workout_adaptation_events",
        "confidence_level",
        # FIT-136 surfaced the adapted next workout + adaptation event feed on
        # the smart recommendation payload; keep this contract assertion current.
        "next_workout",
        "workout_adaptation_events",
    }
    assert isinstance(payload["next_workout"], dict)
    assert payload["workout_adaptation_events"] == []


def test_debug_timing_logs_required_endpoint_durations(fitness_app, monkeypatch, caplog):
    monkeypatch.setenv("DEBUG_TIMING", "1")
    caplog.set_level(logging.INFO, logger=fitness_app.app.logger.name)
    fitness_app.app.logger.setLevel(logging.WARNING)
    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, oura_row=None)
    monkeypatch.setattr(fitness_app, "_WEATHER_CACHE", {"location": "San_Antonio"})
    monkeypatch.setattr(
        fitness_app,
        "get_oura_daily",
        lambda *_args, **_kwargs: {
            "readiness_score": 78,
            "sleep_score": 85,
            "hrv": 42,
            "steps": 8500,
            "activity_score": 88,
            "resting_hr": 52,
            "temperature_deviation": 0.1,
            "sleep_duration_min": 452,
            "sleep_deep_min": 80,
            "sleep_rem_min": 92,
            "sleep_light_min": 270,
            "sleep_awake_min": 10,
        },
    )
    monkeypatch.setattr(fitness_app, "_get_oura_readiness_today", lambda: 78)
    monkeypatch.setattr(fitness_app, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})

    oura_sleep_sync = importlib.import_module("oura_sleep_sync")
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(oura_sleep_sync, "calculate_bedtime_variance", lambda *_args, **_kwargs: None)

    client = fitness_app.app.test_client()
    for path in (
        "/api/dashboard",
        "/api/oura/status",
        "/api/recommendation/smart",
        "/api/oura/sleep-summary",
    ):
        response = client.get(path)
        assert response.status_code == 200, response.get_data(as_text=True)

    messages = [record.getMessage() for record in caplog.records]
    for path in (
        "/api/dashboard",
        "/api/oura/status",
        "/api/recommendation/smart",
        "/api/oura/sleep-summary",
    ):
        assert any(f"DEBUG_TIMING method=GET path={path} status=200 duration_ms=" in m for m in messages)
    assert fitness_app.app.logger.getEffectiveLevel() <= logging.INFO


def test_debug_timing_logs_are_disabled_by_default(fitness_app, monkeypatch, caplog):
    monkeypatch.delenv("DEBUG_TIMING", raising=False)
    caplog.set_level(logging.INFO, logger=fitness_app.app.logger.name)
    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, oura_row=None)
    monkeypatch.setattr(fitness_app, "_WEATHER_CACHE", {"location": "San_Antonio"})

    response = fitness_app.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    assert not any("DEBUG_TIMING" in record.getMessage() for record in caplog.records)


def test_dashboard_route_shape_without_server_or_cookie(fitness_app, monkeypatch):
    freshness = {
        "oura": {
            "status": "missing",
            "last_data_point": None,
            "last_sync_attempt": None,
            "source": "cached",
        },
        "apple_health": {
            "status": "missing",
            "last_data_point": None,
            "last_sync_attempt": None,
        },
        "food": {
            "status": "missing",
            "last_data_point": None,
            "last_sync_attempt": None,
            "pending_review": False,
            "target_state": "none",
            "calories": 0,
            "protein_g": 0.0,
            "calories_target": 2200,
            "protein_target_g": 148.0,
            "calories_pct": 0,
            "protein_pct": 0,
        },
    }
    monkeypatch.setattr(
        fitness_app, "_compute_data_freshness", lambda *_args, **_kwargs: freshness
    )

    response = fitness_app.app.test_client().get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()
    assert "freshness" in payload
    assert payload["freshness"] == freshness
