"""Tests for /api/ai/health and /api/ai/metrics (FIT-15).

These endpoints feed the Settings "AI COACH" card. The contract the UI
relies on is:

* /api/ai/health returns 200 with a JSON object describing the active
  route and primary/fallback candidate status, including a top-level
  ``reachable`` boolean even when the adapter is missing.
* /api/ai/metrics returns 200 with aggregate counters and percentages
  over the last N hours, with ``fallback_pct`` rounded to 1 decimal and
  zero-safe when no requests have been logged.

The tests also lock in that neither endpoint exposes raw model output,
prompt content, or stack traces — the UI surfaces these values to the
user and they must never carry sensitive runtime detail.
"""
from __future__ import annotations

import importlib
import sqlite3


def _client(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit15-ai-health-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    return module


# ──────────────────────────────────────────────────────────────────
# /api/ai/health
# ──────────────────────────────────────────────────────────────────

def test_ai_health_returns_200_when_adapter_missing(monkeypatch):
    """If the LM Studio adapter failed to import (e.g. dev env without
    the optional dep), the route must still return 200 with a safe
    ``{reachable: false, error: ...}`` payload. The UI relies on the
    response being JSON, not a 500 page.
    """
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "_lm_studio", None, raising=False)

    res = module.app.test_client().get("/api/ai/health")
    assert res.status_code == 200
    body = res.get_json()
    assert body["reachable"] is False
    assert "error" in body and isinstance(body["error"], str)


def test_ai_health_returns_adapter_payload(monkeypatch):
    """When the adapter is present, the route forwards its health()
    payload verbatim. Lock in that the structure the Settings card reads
    (primary/fallback/active_role/reachable) is preserved.
    """
    module = _client(monkeypatch)

    class FakeAdapter:
        def health(self):
            return {
                "reachable": True,
                "url": "http://127.0.0.1:1234",
                "model": "qwen/qwen3-30b",
                "model_loaded": True,
                "loaded_models": ["qwen/qwen3-30b"],
                "active_role": "primary",
                "fallback_active": False,
                "primary": {
                    "role": "primary", "name": "primary",
                    "url": "http://127.0.0.1:1234", "model": "qwen/qwen3-30b",
                    "reachable": True, "model_loaded": True,
                    "loaded_models": ["qwen/qwen3-30b"],
                },
                "fallback": None,
                "checks": [],
            }

    monkeypatch.setattr(module, "_lm_studio", FakeAdapter(), raising=False)

    res = module.app.test_client().get("/api/ai/health")
    body = res.get_json()
    assert res.status_code == 200
    assert body["reachable"] is True
    assert body["active_role"] == "primary"
    assert body["primary"]["model"] == "qwen/qwen3-30b"


def test_ai_health_response_does_not_leak_model_traces(monkeypatch):
    """Stable contract: the health payload must never include free-text
    completions, prompt content, or chain-of-thought. The fields it can
    carry are a small enum (model identifier, URL, role, boolean flags,
    error strings).
    """
    module = _client(monkeypatch)

    class FakeAdapter:
        def health(self):
            return {
                "reachable": True,
                "url": "http://127.0.0.1:1234",
                "model": "qwen/qwen3-30b",
                "model_loaded": True,
                "loaded_models": ["qwen/qwen3-30b"],
                "active_role": "primary",
                "fallback_active": False,
                "primary": {"role": "primary", "reachable": True, "model_loaded": True},
                "fallback": None,
                "checks": [],
            }

    monkeypatch.setattr(module, "_lm_studio", FakeAdapter(), raising=False)
    body = module.app.test_client().get("/api/ai/health").get_json()
    forbidden = ("prompt", "completion", "messages", "raw", "trace", "chain_of_thought")
    for key in forbidden:
        assert key not in body, f"{key!r} leaked into /api/ai/health response"


# ──────────────────────────────────────────────────────────────────
# /api/ai/metrics
# ──────────────────────────────────────────────────────────────────

def _seed_adjust_metrics(db_path, rows):
    """Create the adjust_metrics table and seed rows for the test.

    The schema is small: id PK, ts ISO string, outcome, latency_ms, reason.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adjust_metrics ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, outcome TEXT,"
            " latency_ms INTEGER, reason TEXT)"
        )
        conn.execute("DELETE FROM adjust_metrics")
        conn.executemany(
            "INSERT INTO adjust_metrics (ts, outcome, latency_ms, reason)"
            " VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def test_ai_metrics_returns_zeros_for_empty_window(monkeypatch, tmp_path):
    """No requests in the last 24h ⇒ all counters zero, fallback_pct 0.0.
    The UI shows "No AI requests in the last 24h" for this state and
    must not trip the warning banner.
    """
    module = _client(monkeypatch)
    db_path = tmp_path / "adjust_cache.db"
    _seed_adjust_metrics(db_path, [])
    monkeypatch.setattr(module, "_ADJUST_CACHE_DB", str(db_path), raising=False)

    res = module.app.test_client().get("/api/ai/metrics")
    assert res.status_code == 200
    body = res.get_json()
    assert body["adjust_requests"] == 0
    assert body["fallbacks"] == 0
    assert body["fallback_pct"] == 0.0
    assert body["cache_hit_pct"] == 0.0
    assert body["avg_latency_ms"] == 0
    assert body["recent"] == []
    assert body["window_hours"] == 24


def test_ai_metrics_computes_fallback_pct(monkeypatch, tmp_path):
    """3 ok + 1 fallback ⇒ fallback_pct = 25.0% (rounded 1 decimal)."""
    module = _client(monkeypatch)
    db_path = tmp_path / "adjust_cache.db"
    _seed_adjust_metrics(db_path, [
        ("2026-05-19T01:00:00", "ok", 200, None),
        ("2026-05-19T02:00:00", "ok", 220, None),
        ("2026-05-19T03:00:00", "ok", 180, None),
        ("2026-05-19T04:00:00", "fallback", 0, "timeout"),
    ])
    monkeypatch.setattr(module, "_ADJUST_CACHE_DB", str(db_path), raising=False)

    body = module.app.test_client().get("/api/ai/metrics").get_json()
    assert body["adjust_requests"] == 4
    assert body["ok"] == 3
    assert body["fallbacks"] == 1
    assert body["fallback_pct"] == 25.0
    # avg_latency_ms is computed only over "ok" rows.
    assert body["avg_latency_ms"] == 200


def test_ai_metrics_warning_threshold_triggers_above_twenty_pct(monkeypatch, tmp_path):
    """Locks in the boundary the FIT-15 Settings card and FIT-20's
    Apply Adjustment warning both flip on at. The endpoint just reports
    fallback_pct; the threshold lives in the JS as
    ``AI_COACH_FALLBACK_PCT_WARNING = 20.0``. This test pins the
    response shape so the JS check stays stable.
    """
    module = _client(monkeypatch)
    db_path = tmp_path / "adjust_cache.db"
    # 4 ok + 1 fallback = 20% — boundary case.
    _seed_adjust_metrics(db_path, [
        ("2026-05-19T01:00:00", "ok", 200, None),
        ("2026-05-19T02:00:00", "ok", 200, None),
        ("2026-05-19T03:00:00", "ok", 200, None),
        ("2026-05-19T04:00:00", "ok", 200, None),
        ("2026-05-19T05:00:00", "fallback", 0, "timeout"),
    ])
    monkeypatch.setattr(module, "_ADJUST_CACHE_DB", str(db_path), raising=False)

    body = module.app.test_client().get("/api/ai/metrics").get_json()
    assert body["fallback_pct"] == 20.0
    assert isinstance(body["fallback_pct"], float)


def test_ai_metrics_recent_does_not_include_prompt_or_completion(monkeypatch, tmp_path):
    """``recent`` carries (ts, outcome, latency_ms, reason) only. ``reason``
    is a short tag from the codepath ("timeout", "validation_error",
    "cache_hit", etc.) — never user input or model output. Locks the
    field set so a future refactor doesn't accidentally widen it.
    """
    module = _client(monkeypatch)
    db_path = tmp_path / "adjust_cache.db"
    _seed_adjust_metrics(db_path, [
        ("2026-05-19T01:00:00", "fallback", 0, "timeout"),
    ])
    monkeypatch.setattr(module, "_ADJUST_CACHE_DB", str(db_path), raising=False)

    body = module.app.test_client().get("/api/ai/metrics").get_json()
    assert len(body["recent"]) == 1
    entry = body["recent"][0]
    assert set(entry.keys()) == {"ts", "outcome", "latency_ms", "reason"}
    # reason should be a short tag, not free-form text.
    assert entry["reason"] == "timeout"


def test_ai_metrics_clamps_hours_param(monkeypatch, tmp_path):
    """``hours`` accepts 1–720. Out-of-range / invalid values fall back
    to 24 so a misbehaving client can't widen the query window.
    """
    module = _client(monkeypatch)
    db_path = tmp_path / "adjust_cache.db"
    _seed_adjust_metrics(db_path, [])
    monkeypatch.setattr(module, "_ADJUST_CACHE_DB", str(db_path), raising=False)

    res = module.app.test_client().get("/api/ai/metrics?hours=9999")
    assert res.status_code == 200
    assert res.get_json()["window_hours"] == 24

    res = module.app.test_client().get("/api/ai/metrics?hours=not-a-number")
    assert res.status_code == 200
    assert res.get_json()["window_hours"] == 24


def test_ai_metrics_handles_db_failure_gracefully(monkeypatch, tmp_path):
    """If the adjust_metrics SQLite file is missing or unreadable, the
    endpoint returns 500 with a short error string — never a Flask
    debug page. The Settings card treats 5xx as "metrics unavailable"
    and shows degraded copy.
    """
    module = _client(monkeypatch)
    # Point at a path inside a non-existent directory so sqlite raises.
    monkeypatch.setattr(
        module, "_ADJUST_CACHE_DB",
        str(tmp_path / "does-not-exist-dir" / "adjust_cache.db"),
        raising=False,
    )

    res = module.app.test_client().get("/api/ai/metrics")
    assert res.status_code == 500
    body = res.get_json()
    assert "error" in body and isinstance(body["error"], str)
    assert "messages" not in body and "prompt" not in body
