import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit282-test-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit282-health-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    return module


def test_oura_trends_uses_complete_cache_and_strips_raw_json(fitness_app, monkeypatch):
    rows = [
        {"day": f"2026-07-{day:02d}", "hrv": hrv, "raw_json": {"secret": day}}
        for day, hrv in zip(range(5, 11), (30, 31, 32, 38, 39, 40), strict=True)
    ]
    monkeypatch.setattr(fitness_app, "get_oura_daily_range", lambda *_args: rows)
    monkeypatch.setattr(
        fitness_app,
        "OuraClient",
        lambda: pytest.fail("complete cache must not call the Oura API"),
    )

    response = fitness_app.app.test_client().get("/api/oura/trends")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["hrv_trend"] == "improving"
    assert [row["hrv"] for row in payload["series"]] == [30, 31, 32, 38, 39, 40]
    assert all("raw_json" not in row for row in payload["series"])


def test_oura_trends_returns_sanitized_cache_when_api_fallback_fails(fitness_app, monkeypatch):
    rows = [
        {"day": "2026-07-09", "hrv": 35, "raw_json": {"token": "hidden"}},
        {"day": "2026-07-10", "hrv": 36, "raw_json": {"token": "hidden"}},
    ]
    monkeypatch.setattr(fitness_app, "get_oura_daily_range", lambda *_args: rows)

    class FailingOuraClient:
        def get_daily_range(self, *_args):
            raise RuntimeError("fixture API unavailable")

    monkeypatch.setattr(fitness_app, "OuraClient", FailingOuraClient)

    response = fitness_app.app.test_client().get("/api/oura/trends")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["hrv_trend"] == "unknown"
    assert payload["error"] == "fixture API unavailable"
    assert [row["hrv"] for row in payload["series"]] == [35, 36]
    assert all("raw_json" not in row for row in payload["series"])


def test_oura_sleep_summary_augments_stale_sleep_rows_from_daily_cache(fitness_app, monkeypatch):
    sleep_sync = importlib.import_module("oura_sleep_sync")
    monkeypatch.setattr(
        sleep_sync,
        "get_latest_sleep",
        lambda *_args, **_kwargs: [{
            "day": "2026-07-09",
            "total_sleep_min": 420,
            "deep_sleep_min": 60,
            "rem_sleep_min": 80,
            "sleep_score": 75,
        }],
    )
    monkeypatch.setattr(
        sleep_sync,
        "get_sleep_range",
        lambda *_args, **_kwargs: [{
            "day": "2026-07-09",
            "total_sleep_min": 420,
            "deep_sleep_min": 60,
            "rem_sleep_min": 80,
            "sleep_score": 75,
        }],
    )
    monkeypatch.setattr(sleep_sync, "calculate_bedtime_variance", lambda *_args, **_kwargs: 45)
    monkeypatch.setattr(
        fitness_app,
        "get_oura_daily",
        lambda *_args: {
            "day": "2026-07-10",
            "sleep_duration_min": 480,
            "sleep_deep_min": 90,
            "sleep_rem_min": 100,
            "sleep_light_min": 270,
            "sleep_awake_min": 20,
            "sleep_score": 88,
        },
    )
    monkeypatch.setattr(
        fitness_app,
        "get_oura_daily_range",
        lambda *_args: [{
            "day": "2026-07-10",
            "sleep_duration_min": 480,
            "sleep_deep_min": 90,
            "sleep_rem_min": 100,
            "sleep_light_min": 270,
            "sleep_awake_min": 20,
            "sleep_score": 88,
        }],
    )

    response = fitness_app.app.test_client().get("/api/oura/sleep-summary")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["last_night"]["date"] == "2026-07-10"
    assert payload["last_night"]["total_sleep_min"] == 480
    assert payload["week_average"] == {
        "duration_min": 450,
        "score": 81,
        "deep_min": 75,
        "rem_min": 90,
        "avg_heart_rate": 0,
    }
    assert payload["trend_data"] == [
        {"date": "2026-07-09", "duration_min": 420, "score": 75},
        {"date": "2026-07-10", "duration_min": 480, "score": 88},
    ]


@pytest.mark.parametrize(
    ("variance", "expected_status"),
    [
        (None, "unknown"),
        (29, "excellent"),
        (30, "good"),
        (59, "good"),
        (60, "fair"),
        (89, "fair"),
        (90, "poor"),
    ],
)
def test_oura_sleep_summary_pins_consistency_thresholds(
    fitness_app,
    monkeypatch,
    variance,
    expected_status,
):
    sleep_sync = importlib.import_module("oura_sleep_sync")
    monkeypatch.setattr(sleep_sync, "get_latest_sleep", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(sleep_sync, "get_sleep_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        sleep_sync,
        "calculate_bedtime_variance",
        lambda *_args, **_kwargs: variance,
    )
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args: None)
    monkeypatch.setattr(fitness_app, "get_oura_daily_range", lambda *_args: [])

    response = fitness_app.app.test_client().get("/api/oura/sleep-summary")

    assert response.status_code == 200
    assert response.get_json()["consistency"] == {
        "bedtime_variance_min": variance,
        "status": expected_status,
    }
