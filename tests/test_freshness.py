from datetime import datetime, timedelta
import importlib

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
        lambda *_args, **_kwargs: {"debt_minutes": 0, "status": "ok"},
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

    response = fitness_app.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    payload = response.get_json()
    assert "freshness" in payload
    assert "confidence_level" in payload
    assert payload["confidence_level"] == "high"


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
