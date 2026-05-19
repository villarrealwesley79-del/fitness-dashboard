"""Contract tests for the FIT-13 nutrition history breakdown.

The Body tab consumes /api/nutrition-history to render:

  * Per-day nutrition trend (calories, protein, carbs, fat, sodium)
    with target-adherence percentages.
  * Estimated-vs-corrected reliability badge per day.
  * Interpretation notes that surface high-sodium / late-meal context
    so scale movements aren't bare-attributed to fat gain.

These tests lock in the payload shape the UI relies on. Pending
entries must NOT contribute to the day totals, mirroring the
FIT-8 ``uses_only_accepted_entries`` rule.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit13-history-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _today_minus(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _seed_nutrition(module, entries):
    """Replace the in-memory nutrition data for the duration of one test."""
    module.NUTRITION_DATA[:] = list(entries)


# ──────────────────────────────────────────────────────────────────
# Per-day breakdown shape
# ──────────────────────────────────────────────────────────────────

def test_nutrition_history_exposes_sodium_and_target_adherence(fitness_app):
    """The Body tab renders cal/protein percentages against the user's
    targets. The endpoint must expose ``calories_target``,
    ``protein_target_g``, ``calories_pct``, ``protein_pct`` per day.
    """
    _seed_nutrition(fitness_app, [
        {"date": _today(), "calories": 2200, "protein_g": 148,
         "carbs_g": 240, "fat_g": 80, "sodium_mg": 1800,
         "confidence": 0.85, "correction_state": "accepted"},
    ])
    res = fitness_app.app.test_client().get("/api/nutrition-history")
    payload = res.get_json()
    days = payload["history"]
    today = next(d for d in days if d["date"] == _today())
    assert today["sodium_mg"] == 1800
    assert today["calories_target"]
    assert today["protein_target_g"]
    assert today["calories_pct"] == 100  # 2200/2200
    assert today["protein_pct"] == 100  # 148/148


def test_nutrition_history_breakdown_correction_state(fitness_app):
    """The reliability badge in the Body trend distinguishes estimated
    (AI accepted as-is) from corrected (user edited) and manual (no
    AI involved). The endpoint must surface those three counts per day.
    """
    _seed_nutrition(fitness_app, [
        # Day with: 1 accepted (estimated), 1 corrected, 1 manual
        {"date": _today(), "calories": 300, "protein_g": 20,
         "carbs_g": 0, "fat_g": 0, "sodium_mg": 0,
         "correction_state": "accepted"},
        {"date": _today(), "calories": 400, "protein_g": 25,
         "carbs_g": 0, "fat_g": 0, "sodium_mg": 0,
         "correction_state": "corrected"},
        {"date": _today(), "calories": 500, "protein_g": 30,
         "carbs_g": 0, "fat_g": 0, "sodium_mg": 0,
         "correction_state": "manual"},
    ])
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["entries_count"] == 3
    assert today["estimated_count"] == 1
    assert today["corrected_count"] == 1
    assert today["manual_count"] == 1
    assert today["pending_count"] == 0


def test_nutrition_history_excludes_pending_from_totals(fitness_app):
    """Pending entries must NOT contribute to per-day totals (matches
    the FIT-8 uses_only_accepted_entries rule used by /api/nutrition-today
    and the dashboard freshness path). The pending count is exposed
    separately so the UI can prompt the user to resolve them.
    """
    _seed_nutrition(fitness_app, [
        {"date": _today(), "calories": 500, "protein_g": 30,
         "carbs_g": 0, "fat_g": 0, "sodium_mg": 0,
         "correction_state": "accepted"},
        {"date": _today(), "calories": 9999, "protein_g": 999,
         "carbs_g": 0, "fat_g": 0, "sodium_mg": 0,
         "correction_state": "pending_review"},
    ])
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["calories"] == 500, "pending entries must not contribute to totals"
    assert today["entries_count"] == 1
    assert today["pending_count"] == 1


def test_nutrition_history_computes_confidence_average(fitness_app):
    """The UI uses ``confidence_avg`` to flag days that are mostly
    AI-estimated. Average must be over accepted entries with numeric
    confidence; None when no entries qualify.
    """
    _seed_nutrition(fitness_app, [
        {"date": _today(), "calories": 200, "protein_g": 10,
         "carbs_g": 0, "fat_g": 0, "sodium_mg": 0,
         "confidence": 0.8, "correction_state": "accepted"},
        {"date": _today(), "calories": 200, "protein_g": 10,
         "carbs_g": 0, "fat_g": 0, "sodium_mg": 0,
         "confidence": 0.6, "correction_state": "accepted"},
    ])
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["confidence_avg"] == 0.7  # (0.8 + 0.6) / 2


def test_nutrition_history_flags_high_sodium_and_late_meal(fitness_app):
    """The interpretation notes on the Body tab key off these per-day
    flags. ``high_sodium`` thresholds at SODIUM_NEXT_DAY_CONTEXT_MG
    (2300); ``late_meal`` triggers on any logged_at hour >=
    LATE_MEAL_CONTEXT_HOUR (20).
    """
    _seed_nutrition(fitness_app, [
        # High sodium day
        {"date": _today_minus(1), "calories": 1800, "protein_g": 100,
         "carbs_g": 200, "fat_g": 60, "sodium_mg": 2500,
         "logged_at": f"{_today_minus(1)}T13:00:00",
         "correction_state": "accepted"},
        # Late meal day
        {"date": _today(), "calories": 600, "protein_g": 25,
         "carbs_g": 60, "fat_g": 20, "sodium_mg": 800,
         "logged_at": f"{_today()}T21:30:00",
         "correction_state": "accepted"},
    ])
    days = fitness_app.app.test_client().get("/api/nutrition-history").get_json()["history"]
    yesterday = next(d for d in days if d["date"] == _today_minus(1))
    today = next(d for d in days if d["date"] == _today())
    assert yesterday["high_sodium"] is True
    assert today["late_meal"] is True
    assert today["high_sodium"] is False, "800mg sodium should not trip the high-sodium flag"


# ──────────────────────────────────────────────────────────────────
# Sanity: existing fields preserved
# ──────────────────────────────────────────────────────────────────

def test_nutrition_history_merges_food_logs_with_legacy_nutrition_data(fitness_app, monkeypatch):
    """Regression for Codex audit round 1: the active meal composer
    (FIT-60/59/61) persists accepted entries via ``add_food_log`` into
    SQLite ``food_logs`` — NOT the legacy NUTRITION_DATA JSON. The
    endpoint must merge both so the Body tab's interpretation and
    14-day trend reflect the actual logged meals.

    Mirrors what /api/nutrition-today does via
    _food_log_entries_for_context.
    """
    # Legacy entry in NUTRITION_DATA.
    _seed_nutrition(fitness_app, [
        {"date": _today(), "calories": 500, "protein_g": 30,
         "carbs_g": 0, "fat_g": 0, "sodium_mg": 0,
         "correction_state": "accepted"},
    ])
    # Modern entry in food_logs (simulated via monkeypatched fetcher).
    food_log_row = {
        "id": 1, "client_id": "fit13-test-1",
        "date": _today(), "logged_at": f"{_today()}T19:00:00",
        "calories": 700, "protein_g": 40, "carbs_g": 60, "fat_g": 25,
        "sodium_mg": 1200, "confidence": 0.85,
        "correction_state": "accepted", "source": "ai_text_estimate",
    }
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [food_log_row],
    )
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["calories"] == 1200, (
        "history must include both legacy + food_logs sources "
        f"(got {today['calories']}, expected 500+700=1200)"
    )
    assert today["entries_count"] == 2
    assert today["sodium_mg"] == 1200


def test_nutrition_history_still_exposes_legacy_macro_keys(fitness_app):
    """The Body charts and existing consumers still read the original
    calories / protein / carbs / fat keys. Locking in that the FIT-13
    additions are additive — no field renames or removals.
    """
    _seed_nutrition(fitness_app, [
        {"date": _today(), "calories": 1500, "protein_g": 100,
         "carbs_g": 180, "fat_g": 50, "sodium_mg": 1200,
         "correction_state": "accepted"},
    ])
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    for key in ("calories", "protein_g", "carbs_g", "fat_g", "date"):
        assert key in today, f"legacy field {key!r} must remain in the response"
