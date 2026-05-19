"""Contract tests for the wearable-freshness UI surfaces (FIT-16).

The Settings INTEGRATIONS panel renders Oura + Apple Health freshness
evidence by reading specific fields from `/api/oura/status` and
`/api/apple-health/sync/status`. These tests pin the field set + types
so a backend refactor can't silently break the UI's ability to show
"latest daily / sleep / cached vs live" and "last accepted / attempt /
records / stale" without the JS surface breaking too.

Auth-gating, webhook semantics, and the legacy stub on
`/api/apple-health/sync` are NOT exercised here — they have their own
tests (test_apple_health_setup_url.py, test_apple_health_hae_dates.py).
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit16-freshness-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit16-freshness-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


# ──────────────────────────────────────────────────────────────────
# /api/apple-health/sync/status — fields the FIT-16 panel renders
# ──────────────────────────────────────────────────────────────────

def test_apple_health_status_exposes_last_accepted_and_last_attempt(fitness_app):
    """The UI reads ``last_sync`` (records actually inserted) and
    ``last_attempt`` (raw webhook events) so it can distinguish
    "data is landing" from "HAE is trying but nothing is inserting".
    Both keys must be present in the response, even when null.
    """
    res = fitness_app.app.test_client().get("/api/apple-health/sync/status")
    assert res.status_code == 200
    payload = res.get_json()
    assert "last_sync" in payload, "last_accepted timestamp must be exposed"
    assert "last_attempt" in payload, "last_attempt timestamp must be exposed"


def test_apple_health_status_exposes_record_breakdown(fitness_app):
    """The UI renders ``total_records`` + the top categories from
    ``by_type``. Both must be present (empty dict is fine when no
    data has landed yet).
    """
    res = fitness_app.app.test_client().get("/api/apple-health/sync/status")
    payload = res.get_json()
    assert "total_records" in payload
    assert isinstance(payload["total_records"], int)
    assert "by_type" in payload
    assert isinstance(payload["by_type"], dict)


def test_apple_health_status_exposes_setup_configured_flag(fitness_app):
    """The UI flips between "Setup · waiting for export" and "Not
    connected" off this flag. Must be a real bool.
    """
    res = fitness_app.app.test_client().get("/api/apple-health/sync/status")
    payload = res.get_json()
    assert "setup_configured" in payload
    assert isinstance(payload["setup_configured"], bool)


def test_apple_health_status_exposes_last_event_counts_shape(fitness_app):
    """When ``last_event`` is non-null it must carry ``inserted``,
    ``skipped``, ``total`` ints so the UI can render "X inserted, Y
    skipped" inline. When no events have landed, it may be null.
    """
    res = fitness_app.app.test_client().get("/api/apple-health/sync/status")
    payload = res.get_json()
    assert "last_event" in payload
    event = payload["last_event"]
    if event is not None:
        assert "inserted" in event and isinstance(event["inserted"], int)
        assert "skipped" in event and isinstance(event["skipped"], int)
        assert "total" in event and isinstance(event["total"], int)


def test_apple_health_status_does_not_leak_raw_health_data(fitness_app):
    """The status endpoint must never include raw health records,
    user-identifying payloads, or webhook secrets — only aggregates
    and timestamps. Sanity check on the field set so a future refactor
    doesn't accidentally widen the shape.
    """
    res = fitness_app.app.test_client().get("/api/apple-health/sync/status")
    payload = res.get_json()
    forbidden = ("token", "secret", "records", "samples", "raw", "user_id")
    for key in forbidden:
        assert key not in payload, f"{key!r} leaked into /api/apple-health/sync/status"


# ──────────────────────────────────────────────────────────────────
# /api/oura/status — fields the FIT-16 panel renders
# ──────────────────────────────────────────────────────────────────

def _stub_oura_cached_row(monkeypatch, fitness_app, row):
    """Force /api/oura/status onto the cached path by monkeypatching
    ``get_oura_daily``. Bypasses the OURA_API_TOKEN check so the test
    works in dev envs without a token.
    """
    monkeypatch.setattr(fitness_app, "get_oura_daily",
                        lambda _db, _today: dict(row), raising=False)


def test_oura_status_exposes_source_field(monkeypatch, fitness_app):
    """The UI labels Oura as "Live" vs "Cached" based on ``source``.
    Even when the live API is unreachable, the cached path must still
    set ``source`` so the panel can render an honest state.
    """
    _stub_oura_cached_row(monkeypatch, fitness_app, {
        "readiness_score": 78, "sleep_score": 85, "hrv": 42,
        "steps": 8500, "activity_score": 88, "resting_hr": 52,
        "temperature_deviation": 0.1, "sleep_duration_min": 452,
        "sleep_deep_min": 80, "sleep_rem_min": 92, "sleep_light_min": 270, "sleep_awake_min": 10,
    })
    res = fitness_app.app.test_client().get("/api/oura/status")
    assert res.status_code == 200
    payload = res.get_json()
    assert "source" in payload, "source field is the live-vs-cached signal"
    # Real values are "db" (cached) or "api" (live).
    assert payload["source"] in ("db", "api"), (
        f"unexpected source value: {payload['source']!r}"
    )


def test_oura_status_carries_daily_and_sleep_rows(monkeypatch, fitness_app):
    """The UI's "Latest daily" row reads ``date`` + ``readiness`` /
    ``hrv`` / ``resting_hr``; "Latest sleep" reads
    ``sleep_duration_min`` + ``sleep_score``. All keys must be present
    in the response (null is fine when no row exists).
    """
    _stub_oura_cached_row(monkeypatch, fitness_app, {
        "readiness_score": 78, "sleep_score": 85, "hrv": 42,
        "steps": 8500, "activity_score": 88, "resting_hr": 52,
        "temperature_deviation": 0.1, "sleep_duration_min": 452,
        "sleep_deep_min": 80, "sleep_rem_min": 92, "sleep_light_min": 270, "sleep_awake_min": 10,
    })
    res = fitness_app.app.test_client().get("/api/oura/status")
    payload = res.get_json()
    for key in (
        "date", "readiness", "hrv", "resting_hr",
        "sleep_score", "sleep_duration_min",
    ):
        assert key in payload, f"FIT-16 panel reads {key!r}; must be in payload"
    # Sanity check the values are wired through correctly.
    assert payload["readiness"] == 78
    assert payload["sleep_score"] == 85
    assert payload["sleep_duration_min"] == 452


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
    for source in ("oura", "apple_health", "food"):
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
