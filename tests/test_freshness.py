from datetime import date, datetime, timedelta
import importlib
import json
import logging
from unittest.mock import Mock

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
            {"wearable_facts": {"status": "fresh"}},
            "high",
        ),
        (
            72,
            {"wearable_facts": {"status": "aging"}},
            "medium",
        ),
        (
            88,
            {"wearable_facts": {"status": "stale"}},
            "low",
        ),
        (
            88,
            {"wearable_facts": {"status": "missing"}},
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
        "wearable_facts": {
            "status": "fresh",
            "last_data_point": "2026-05-18",
            "last_sync_attempt": "2026-05-18T07:00:00",
            "source": "open_wearables",
        },
    }
    seed_readiness(fitness_app, monkeypatch, readiness=82, hrv=41)
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
    freshness_probe = Mock(return_value=freshness)
    monkeypatch.setattr(fitness_app, "_compute_data_freshness", freshness_probe)

    response = fitness_app.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    assert freshness_probe.call_count == 1
    payload = response.get_json()
    assert "freshness" in payload
    assert "confidence_level" in payload
    assert payload["confidence_level"] == "high"
    proof = payload["recommendation_sources"]["source_proof"]
    today = datetime.now().strftime("%Y-%m-%d")
    assert proof["recovery"] == {
        "provider": "oura",
        "via": "open_wearables",
        "date": today,
        "freshness": "fresh",
        "used_for_recommendation": False,
        "fields_used": [],
        "modifier_applied": False,
        "ignored_reason": "not_used",  # one HRV sample is no trend, and a recovery score is not readiness
        "conflict_state": None,
    }
    # Sleep debt is read, but no sleep fact is stored: the proof says so instead of naming a provider.
    assert (proof["sleep"]["provider"], proof["sleep"]["freshness"]) == (None, "missing")
    assert proof["sleep"]["ignored_reason"] == "missing_data"
    assert "oura" not in proof


def _freshness_payload():
    return {
        "wearable_facts": {"status": "missing", "last_data_point": None, "last_sync_attempt": None},
    }


def seed_readiness(module, monkeypatch, readiness=None, hrv=None, provider="oura"):
    """Pin readiness (it is a sleep estimate now) and store today's HRV as an Open Wearables fact."""
    from datetime import date
    from wearable_fact_store import WearableDailyFact, upsert_daily_facts

    monkeypatch.setattr(module, "_readiness_with_basis", lambda: (readiness, "sleep_estimate" if readiness is not None else None))
    today = date.today().isoformat()
    upsert_daily_facts(module.WEARABLE_FACTS_DB_FILE, [
        WearableDailyFact(
            date=today, provider_id=provider, source_label=provider, metric="heart_rate_variability", value=float(hrv),
            source_provider=provider, source_system="open_wearables", source_id="recovery",
            observed_at=f"{today}T06:00:00Z", used_for_recommendation=True, freshness="fresh",
        )
    ] if hrv is not None else [], profile_key=module._open_wearables_profile_key())


def _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, *, readiness=None, hrv=None):
    monkeypatch.setattr(fitness_app, "WORKOUTS", [])
    monkeypatch.setattr(fitness_app, "SORENESS_DATA", [])
    monkeypatch.setattr(fitness_app, "RECOVERY_DATA", [])
    seed_readiness(fitness_app, monkeypatch, readiness, hrv)
    monkeypatch.setattr(fitness_app, "compute_hrv_trend", lambda *_args, **_kwargs: "unknown")
    monkeypatch.setattr(
        fitness_app,
        "calculate_acwr",
        lambda *_args, **_kwargs: {"acute_load": 0, "chronic_load": 0, "acwr": 0, "risk": "low"},
    )
    monkeypatch.setattr(
        fitness_app,
        "calculate_sleep_debt",
        lambda *_args, **_kwargs: {
            "debt_minutes": 0, "status": "ok",
            "source": {"provider": "oura", "via": "open_wearables", "date": date.today().isoformat(),
                       "freshness": "fresh", "conflict_state": None},
        },
    )
    monkeypatch.setattr(
        fitness_app,
        "calculate_recovery_bonus",
        lambda *_args, **_kwargs: {"bonus_points": 0},
    )
    monkeypatch.setattr(fitness_app, "_compute_data_freshness", lambda *_args, **_kwargs: _freshness_payload())
    monkeypatch.setattr(
        fitness_app,
        "generate_next_workout",
        lambda *_args, **_kwargs: {"name": "Test workout", "cardio": {}},
    )


def test_recommendation_smart_stale_facts_are_not_used_and_say_why(fitness_app, monkeypatch):
    from datetime import date, timedelta
    from wearable_fact_store import WearableDailyFact, upsert_daily_facts

    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, readiness=None)
    old = (date.today() - timedelta(days=6)).isoformat()
    upsert_daily_facts(fitness_app.WEARABLE_FACTS_DB_FILE, [WearableDailyFact(
        date=old, provider_id="oura", source_label="Oura", metric="recovery_score", value=20.0,
        source_provider="oura", source_system="open_wearables", source_id="recovery", observed_at=f"{old}T06:00:00Z", freshness="fresh",
    )], profile_key=fitness_app._open_wearables_profile_key())

    payload = fitness_app.app.test_client().get("/api/recommendation/smart").get_json()

    assert payload["readiness"] is None  # a six-day-old score is not today's readiness
    recovery = payload["recommendation_sources"]["source_proof"]["recovery"]
    assert (recovery["provider"], recovery["freshness"], recovery["date"]) == ("oura", "stale", old)
    assert recovery["used_for_recommendation"] is False
    assert recovery["ignored_reason"] == "stale_source"


def test_recommendation_smart_low_readiness_changes_the_recommendation_and_is_attributed(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, readiness=20)

    payload = fitness_app.app.test_client().get("/api/recommendation/smart").get_json()

    assert payload["recommendation"] == "recovery"
    sleep = payload["recommendation_sources"]["source_proof"]["sleep"]
    assert sleep["used_for_recommendation"] is True
    assert sleep["modifier_applied"] is True
    assert sleep["fields_used"] == ["readiness_estimate"]  # readiness is the sleep estimate, from Oura here


def test_recommendation_smart_discloses_sparse_hrv_trend_when_it_changes_recommendation(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, readiness=80, hrv=41)
    monkeypatch.setattr(fitness_app, "compute_hrv_trend", lambda *_args, **_kwargs: "declining")

    payload = fitness_app.app.test_client().get("/api/recommendation/smart").get_json()

    assert payload["recommendation"] == "recovery"
    assert "hrv_trend" in payload["recommendation_sources"]["source_proof"]["recovery"]["fields_used"]


def test_recommendation_smart_uses_cache_only_weather_empty_cache(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(
        monkeypatch,
        fitness_app,
        readiness=82,
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
        readiness=82,
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


def test_fetch_weather_normalizes_provider_payload_for_response_and_cache(fitness_app, monkeypatch):
    provider_condition = {
        "temp_F": "98",
        "FeelsLikeF": "101",
        "humidity": "50",
        "weatherDesc": [{"value": "Hot"}],
        "provider_only": "must-not-leak",
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"current_condition": [provider_condition]}).encode("utf-8")

    weather_cache = {"ts": 0, "location": "San_Antonio", "data": None, "error": None}
    monkeypatch.setattr(fitness_app, "_WEATHER_CACHE", weather_cache)
    monkeypatch.setattr(fitness_app.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    weather = fitness_app._fetch_wttr("San_Antonio")

    assert weather == {
        "available": True,
        "location": "San_Antonio",
        "temp_f": 98.0,
        "feelslike_f": 101.0,
        "humidity_pct": 50.0,
        "condition": "Hot",
        "source": "api",
    }
    assert weather_cache["data"] == {
        "temp_f": 98.0,
        "feelslike_f": 101.0,
        "humidity_pct": 50.0,
        "condition": "Hot",
    }


def test_weather_api_returns_same_normalized_fields_from_cache(fitness_app, monkeypatch):
    monkeypatch.setattr(
        fitness_app,
        "_WEATHER_CACHE",
        {
            "ts": int(fitness_app.time.time()),
            "location": "San_Antonio",
            "data": {
                "temp_f": 98.0,
                "feelslike_f": 101.0,
                "humidity_pct": 50.0,
                "condition": "Hot",
            },
            "error": None,
        },
    )

    response = fitness_app.app.test_client().get("/api/weather")

    assert response.status_code == 200
    assert response.get_json() == {
        "available": True,
        "location": "San_Antonio",
        "temp_f": 98.0,
        "feelslike_f": 101.0,
        "humidity_pct": 50.0,
        "condition": "Hot",
        "source": "cache",
    }


def test_recommendation_smart_with_no_stored_facts_does_not_refetch_inside_the_gate(fitness_app, monkeypatch):
    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, readiness=None)
    monkeypatch.setattr(fitness_app, "_WEATHER_CACHE", {"location": "San_Antonio"})
    monkeypatch.setattr(
        fitness_app,
        "_fetch_wttr",
        lambda *_args, **_kwargs: pytest.fail("/api/recommendation/smart must not fetch live weather"),
    )

    monkeypatch.setattr(
        fitness_app,
        "fetch_open_wearables_data",
        lambda *_a, **_k: pytest.fail("a gated refresh must not fetch twice"),
    )
    fitness_app._WEARABLE_REFRESH_STATE["at"] = fitness_app.time.monotonic()

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
        "wearable_sources",
        "recommendation_sources",
        "next_workout",
        "confidence_level",
    }
    assert isinstance(payload["next_workout"], dict)


def test_debug_timing_logs_required_endpoint_durations(fitness_app, monkeypatch, caplog):
    monkeypatch.setenv("DEBUG_TIMING", "1")
    caplog.set_level(logging.INFO, logger=fitness_app.app.logger.name)
    fitness_app.app.logger.setLevel(logging.WARNING)
    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, readiness=None)
    monkeypatch.setattr(fitness_app, "_WEATHER_CACHE", {"location": "San_Antonio"})
    seed_readiness(fitness_app, monkeypatch, readiness=78, hrv=42)

    client = fitness_app.app.test_client()
    for path in (
        "/api/dashboard",
        "/api/wearables/today",
        "/api/recommendation/smart",
        "/api/sleep/summary",
    ):
        response = client.get(path)
        assert response.status_code == 200, response.get_data(as_text=True)

    messages = [record.getMessage() for record in caplog.records]
    for path in (
        "/api/dashboard",
        "/api/wearables/today",
        "/api/recommendation/smart",
        "/api/sleep/summary",
    ):
        assert any(f"DEBUG_TIMING method=GET path={path} status=200 duration_ms=" in m for m in messages)
    assert fitness_app.app.logger.getEffectiveLevel() <= logging.INFO


def test_debug_timing_logs_are_disabled_by_default(fitness_app, monkeypatch, caplog):
    monkeypatch.delenv("DEBUG_TIMING", raising=False)
    caplog.set_level(logging.INFO, logger=fitness_app.app.logger.name)
    _stub_smart_recommendation_dependencies(monkeypatch, fitness_app, readiness=None)
    monkeypatch.setattr(fitness_app, "_WEATHER_CACHE", {"location": "San_Antonio"})

    response = fitness_app.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    assert not any("DEBUG_TIMING" in record.getMessage() for record in caplog.records)


def test_dashboard_route_shape_without_server_or_cookie(fitness_app, monkeypatch):
    freshness = {
        "wearable_facts": {
            "status": "missing",
            "last_data_point": None,
            "last_sync_attempt": None,
            "source": "open_wearables",
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
