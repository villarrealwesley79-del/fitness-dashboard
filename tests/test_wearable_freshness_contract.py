"""Contract tests for the wearable-freshness UI surfaces (FIT-16).

The Settings INTEGRATIONS panel renders wearable freshness evidence by
reading specific fields from `/api/wearables/today` and `/api/freshness`.
These tests pin the field set + types so a backend refactor can't silently
break the UI's ability to show "latest daily / sleep / cached vs live"
without the JS surface breaking too.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit16-freshness-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


# ──────────────────────────────────────────────────────────────────
# /api/wearables/today — the readiness/sleep numbers read from stored facts
# ──────────────────────────────────────────────────────────────────

def _store_today(fitness_app):
    from datetime import date
    from wearable_fact_store import WearableDailyFact, upsert_daily_facts

    today = date.today().isoformat()
    metrics = {
        "recovery_score": 78, "heart_rate_variability": 42, "resting_heart_rate": 52, "steps": 8500,
        "temperature_deviation": 0.1, "sleep_duration": 452, "sleep_deep_duration": 80,
        "sleep_rem_duration": 92, "sleep_light_duration": 270, "sleep_awake_duration": 10,
    }
    upsert_daily_facts(fitness_app.WEARABLE_FACTS_DB_FILE, [
        WearableDailyFact(
            date=today, provider_id="oura", source_label="Oura", metric=metric, value=float(value),
            source_provider="oura", source_system="open_wearables", source_id="s",
            observed_at=f"{today}T06:00:00Z", used_for_recommendation=True,
        )
        for metric, value in metrics.items()
    ], profile_key=fitness_app._open_wearables_profile_key())


def test_wearables_today_exposes_source_field(monkeypatch, fitness_app):
    """The UI labels the data by ``source`` and names each domain's provider."""
    _store_today(fitness_app)
    res = fitness_app.app.test_client().get("/api/wearables/today")
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["source"] == "open_wearables"
    assert payload["sources"]["sleep"]["provider"] == "oura"
    assert payload["sources"]["recovery"]["via"] == "open_wearables"


def test_wearables_today_carries_daily_and_sleep_rows(monkeypatch, fitness_app):
    """The UI's daily row reads ``date`` + ``readiness`` / ``hrv`` / ``resting_hr``;
    the sleep row reads ``sleep_duration_min``. All keys are present (null is fine).
    """
    _store_today(fitness_app)
    payload = fitness_app.app.test_client().get("/api/wearables/today").get_json()
    for key in (
        "date", "readiness", "hrv", "resting_hr",
        "sleep_score", "sleep_duration_min",
    ):
        assert key in payload, f"the dashboard reads {key!r}; must be in payload"
    assert payload["readiness"] == 70  # sleep estimate for 452 min; the 78 recovery score is not readiness
    assert payload["hrv"] == 42
    assert payload["sleep_duration_min"] == 452
    assert payload["sleep_breakdown_min"] == {"deep": 80, "rem": 92, "light": 270, "awake": 10}
    assert payload["sleep_score"] is None  # facts carry no vendor sleep score


# ──────────────────────────────────────────────────────────────────
# /api/dashboard freshness block — the chip strip the panel mirrors
# ──────────────────────────────────────────────────────────────────

def test_dashboard_freshness_block_distinguishes_attempt_from_data_point(fitness_app):
    """Acceptance criterion: "App distinguishes old file availability
    from recent real sync evidence." The freshness block exposes
    ``last_data_point`` (the day the data covers) separately from
    ``last_sync_attempt`` (when the sync was attempted) so a stale
    file from a week ago doesn't masquerade as fresh.
    """
    res = fitness_app.app.test_client().get("/api/dashboard")
    payload = res.get_json()
    freshness = payload.get("freshness") or {}
    for source in ("wearable_facts",):
        assert source in freshness, f"freshness.{source} block must exist"
        block = freshness[source]
        assert "last_data_point" in block, (
            f"{source} block must distinguish last_data_point from last_sync_attempt"
        )
        assert "last_sync_attempt" in block, (
            f"{source} block must expose last_sync_attempt"
        )


def test_freshness_only_endpoint_returns_same_shape_as_dashboard(fitness_app):
    """FIT-16: /api/freshness must return the same freshness block as
    /api/dashboard but without the recommendation side effects. The UI
    panel hits this endpoint from Settings so visiting Settings can't
    reset LAST_WORKOUT_RECOMMENDATION (which /api/dashboard does write).
    """
    client = fitness_app.app.test_client()
    fresh = client.get("/api/freshness").get_json()
    dash = client.get("/api/dashboard").get_json()
    assert "freshness" in fresh, "endpoint must return a freshness top-level key"
    assert set(fresh["freshness"].keys()) == set((dash.get("freshness") or {}).keys()), (
        "/api/freshness and /api/dashboard.freshness must expose the same sources"
    )
    for src, block in fresh["freshness"].items():
        for key in ("status", "last_data_point", "last_sync_attempt"):
            assert key in block, f"freshness.{src} must expose {key!r}"


def test_freshness_only_endpoint_does_not_mutate_workout_recommendation(fitness_app):
    """Hitting /api/freshness must NOT touch LAST_WORKOUT_RECOMMENDATION.
    Codex round 6 finding: /api/dashboard rewrites that global as a side
    effect of regenerating next_workout, so Settings using it would
    silently reset a user's adjusted plan. The new endpoint must skip
    that path entirely.
    """
    module = importlib.import_module("app")
    sentinel = {"name": "fit16-sentinel-plan", "muscles": ["chest"]}
    original = getattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    module.LAST_WORKOUT_RECOMMENDATION = sentinel
    try:
        fitness_app.app.test_client().get("/api/freshness")
        assert module.LAST_WORKOUT_RECOMMENDATION is sentinel, (
            "LAST_WORKOUT_RECOMMENDATION was replaced by a /api/freshness call"
        )
    finally:
        module.LAST_WORKOUT_RECOMMENDATION = original


def test_dashboard_freshness_status_uses_documented_buckets(fitness_app):
    """Each source's ``status`` must be one of fresh / aging / stale /
    missing. The FIT-16 panel's stale warning threshold (48h) aligns
    with the server's _FRESHNESS_STALE_HOURS constant via this enum.
    """
    res = fitness_app.app.test_client().get("/api/dashboard")
    freshness = (res.get_json() or {}).get("freshness") or {}
    valid = {"fresh", "aging", "stale", "missing"}
    for source, block in freshness.items():
        status = block.get("status")
        assert status in valid, (
            f"{source}.status={status!r} not in documented buckets {valid}"
        )
