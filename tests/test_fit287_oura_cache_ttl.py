from datetime import datetime, timedelta
import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit287-test-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit287-health-token")
    module = importlib.import_module("app")
    monkeypatch.setitem(module.app.config, "TESTING", True)
    monkeypatch.setitem(module.app.config, "LOGIN_DISABLED", True)
    return module


def _cached_row(age):
    return {
        "readiness_score": 71,
        "sleep_score": 77,
        "hrv": 38,
        "steps": 5000,
        "activity_score": 70,
        "created_at": (datetime.now() - age).isoformat(),
    }


def test_oura_cache_ttl_compares_naive_created_at_as_local_time(fitness_app):
    local_now = datetime(2026, 7, 11, 18, 0, 0)
    row = {"created_at": (local_now - timedelta(minutes=30)).isoformat()}

    assert fitness_app._oura_status_cache_is_stale(row, now=local_now) is False


def test_oura_status_keeps_fresh_same_day_cache(fitness_app, monkeypatch):
    row = _cached_row(timedelta(seconds=fitness_app.OURA_STATUS_CACHE_TTL_SECONDS - 60))
    monkeypatch.setenv("OURA_API_TOKEN", "test-token")
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args: row)
    monkeypatch.setattr(
        fitness_app,
        "OuraClient",
        lambda: pytest.fail("fresh cache must not call Oura"),
    )

    response = fitness_app.app.test_client().get("/api/oura/status")

    assert response.status_code == 200
    assert response.get_json()["source"] == "db"


def test_oura_status_auto_refreshes_stale_cache_when_token_exists(fitness_app, monkeypatch):
    row = _cached_row(timedelta(seconds=fitness_app.OURA_STATUS_CACHE_TTL_SECONDS + 60))
    monkeypatch.setenv("OURA_API_TOKEN", "test-token")
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args: row)
    writes = []
    monkeypatch.setattr(fitness_app, "upsert_oura_daily", lambda *args, **kwargs: writes.append((args, kwargs)))

    class OuraFixture:
        def get_today_metrics(self, _today):
            return 86, 90, 51, {
                "steps": 7000,
                "activity_score": 84,
                "activity_day": _today,
                "resting_hr": 49,
                "temperature_deviation": 0.0,
                "sleep_duration_min": 470,
                "sleep_deep_min": 80,
                "sleep_rem_min": 95,
                "sleep_light_min": 275,
                "sleep_awake_min": 20,
                "active_calories": 450,
            }, {"private": "not returned"}

    monkeypatch.setattr(fitness_app, "OuraClient", OuraFixture)

    response = fitness_app.app.test_client().get("/api/oura/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "api"
    assert payload["readiness"] == 86
    assert len(writes) == 1


def test_oura_status_keeps_stale_cache_when_token_is_missing(fitness_app, monkeypatch):
    row = _cached_row(timedelta(seconds=fitness_app.OURA_STATUS_CACHE_TTL_SECONDS + 60))
    monkeypatch.delenv("OURA_API_TOKEN", raising=False)
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args: row)
    monkeypatch.setattr(
        fitness_app,
        "OuraClient",
        lambda: pytest.fail("automatic refresh requires a configured token"),
    )

    response = fitness_app.app.test_client().get("/api/oura/status")

    assert response.status_code == 200
    assert response.get_json()["source"] == "db"


def test_oura_status_falls_back_to_stale_cache_when_refresh_fails(fitness_app, monkeypatch):
    row = _cached_row(timedelta(seconds=fitness_app.OURA_STATUS_CACHE_TTL_SECONDS + 60))
    monkeypatch.setenv("OURA_API_TOKEN", "test-token")
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args: row)

    class FailingOuraFixture:
        def get_today_metrics(self, _today):
            raise RuntimeError("fixture Oura unavailable")

    monkeypatch.setattr(fitness_app, "OuraClient", FailingOuraFixture)

    response = fitness_app.app.test_client().get("/api/oura/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "db"
    assert payload["readiness"] == 71
    assert payload["warning"] == "Oura API error: fixture Oura unavailable"


def test_oura_status_keeps_cached_scores_when_live_daily_values_lag(fitness_app, monkeypatch):
    row = _cached_row(timedelta(seconds=fitness_app.OURA_STATUS_CACHE_TTL_SECONDS + 60))
    monkeypatch.setenv("OURA_API_TOKEN", "test-token")
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args: row)
    monkeypatch.setattr(fitness_app, "upsert_oura_daily", lambda *_args, **_kwargs: None)

    class LaggingOuraFixture:
        def get_today_metrics(self, today):
            return None, None, None, {"activity_day": today}, {}

    monkeypatch.setattr(fitness_app, "OuraClient", LaggingOuraFixture)

    response = fitness_app.app.test_client().get("/api/oura/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "api"
    assert (payload["readiness"], payload["sleep_score"], payload["hrv"]) == (71, 77, 38)
