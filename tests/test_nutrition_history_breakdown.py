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

def test_nutrition_history_surfaces_food_logs_entries(fitness_app, monkeypatch):
    """Regression for Codex audit round 1: the active meal composer
    (FIT-60/59/61) persists accepted entries via ``add_food_log`` into
    SQLite ``food_logs`` — NOT the legacy NUTRITION_DATA JSON. The
    endpoint must surface those entries so the Body tab's
    interpretation and 14-day trend reflect the actual logged meals.

    The endpoint prefers food_logs per-day when any food_logs entry
    exists for that date (matching _nutrition_context_for_date for
    /api/nutrition-today, and avoiding the dual-write double-count
    scenario covered in test_nutrition_history_does_not_double_count_...).
    """
    # Legacy NUTRITION_DATA empty for today.
    _seed_nutrition(fitness_app, [])
    # Modern entry in food_logs (simulated via monkeypatched fetcher).
    food_log_row = {
        "id": 1, "client_id": "fit13-test-1",
        "date": _today(), "logged_at": f"{_today()}T21:00:00",
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
    assert today["calories"] == 700, (
        f"food_logs entry must surface in history (got {today['calories']})"
    )
    assert today["entries_count"] == 1
    assert today["sodium_mg"] == 1200
    assert today["late_meal"] is True


def test_nutrition_history_does_not_double_count_dual_write_entries(fitness_app, monkeypatch):
    """Regression for Codex audit round 2: /api/add-nutrition writes the
    same entry to BOTH stores (food_logs via add_food_log AND legacy
    NUTRITION_DATA). Concatenating them would double-count the meal.

    The endpoint must prefer food_logs per-day when any food_logs row
    exists for that date — matching the rule
    _nutrition_context_for_date already uses for /api/nutrition-today.
    """
    # Same meal logged via /api/add-nutrition → appears in both stores
    # with the same client_id.
    legacy_entry = {
        "date": _today(), "calories": 600, "protein_g": 35,
        "carbs_g": 50, "fat_g": 20, "sodium_mg": 800,
        "client_id": "dual-write-1",
        "correction_state": "accepted",
    }
    food_log_entry = {
        "id": 1, "client_id": "dual-write-1",
        "date": _today(), "logged_at": f"{_today()}T13:00:00",
        "calories": 600, "protein_g": 35, "carbs_g": 50, "fat_g": 20,
        "sodium_mg": 800, "confidence": 0.85,
        "correction_state": "accepted",
    }
    _seed_nutrition(fitness_app, [legacy_entry])
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [food_log_entry],
    )
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["calories"] == 600, (
        "dual-write entry must count once, not twice "
        f"(got {today['calories']}, expected 600)"
    )
    assert today["entries_count"] == 1


def test_nutrition_history_falls_back_to_legacy_on_days_without_food_logs(fitness_app, monkeypatch):
    """Days that pre-date the food_logs migration still have entries in
    NUTRITION_DATA only. Those must continue to render in the Body trend.
    """
    legacy_only_date = _today_minus(10)
    _seed_nutrition(fitness_app, [
        {"date": legacy_only_date, "calories": 1900, "protein_g": 120,
         "carbs_g": 220, "fat_g": 65, "sodium_mg": 1500,
         "correction_state": "accepted"},
    ])
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [],  # no food_logs at all
    )
    days = fitness_app.app.test_client().get("/api/nutrition-history").get_json()["history"]
    target = next(d for d in days if d["date"] == legacy_only_date)
    assert target["calories"] == 1900
    assert target["entries_count"] == 1


def test_nutrition_history_merges_mixed_source_same_day_entries(fitness_app, monkeypatch):
    """Regression for Codex audit round 3 finding 1: per-day source
    selection wrongly dropped legacy entries on days that ALSO had a
    food_logs entry. E.g. a legacy /api/add-nutrition breakfast plus
    a meal-composer dinner — both should count.

    Dedupe is now per-entry by client_id, so distinct entries on the
    same day are preserved regardless of which store they live in.
    """
    legacy_breakfast = {
        "date": _today(), "calories": 400, "protein_g": 25,
        "carbs_g": 40, "fat_g": 15, "sodium_mg": 300,
        "client_id": "legacy-breakfast-1",
        "correction_state": "manual",
    }
    food_log_dinner = {
        "id": 1, "client_id": "modern-dinner-1",
        "date": _today(), "logged_at": f"{_today()}T19:00:00",
        "calories": 800, "protein_g": 45, "carbs_g": 80, "fat_g": 30,
        "sodium_mg": 1100, "confidence": 0.85,
        "correction_state": "accepted",
    }
    _seed_nutrition(fitness_app, [legacy_breakfast])
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [food_log_dinner],
    )
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["calories"] == 1200, (
        "mixed-source same-day entries must both count "
        f"(got {today['calories']}, expected 400+800=1200)"
    )
    assert today["entries_count"] == 2
    assert today["manual_count"] == 1
    assert today["estimated_count"] == 1


def test_nutrition_history_classifies_legacy_manual_without_state_as_manual(fitness_app, monkeypatch):
    """Regression for Codex audit round 3 finding 2: legacy
    /api/add-nutrition entries may omit ``correction_state``. The
    Body trend's reliability badge would otherwise mislabel those
    as AI-estimated. When there's no AI signal (no original_estimate,
    no confidence, no ai_*/stub_* source), treat as manual.
    """
    _seed_nutrition(fitness_app, [
        # Legacy entry with NO correction_state and NO AI signal.
        {"date": _today(), "calories": 500, "protein_g": 30,
         "carbs_g": 50, "fat_g": 20, "sodium_mg": 400},
    ])
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [],
    )
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["manual_count"] == 1, (
        "legacy entries without correction_state or AI signal must be "
        "classified as manual, not estimated"
    )
    assert today["estimated_count"] == 0


def test_nutrition_history_classifies_legacy_entry_with_ai_signal_as_estimated(fitness_app, monkeypatch):
    """Counter-test: a legacy entry WITHOUT correction_state but WITH
    AI metadata (e.g., confidence > 0 or an AI source label) must
    still be classified as estimated — the missing-state default only
    flips to manual when there's no AI signal whatsoever.
    """
    _seed_nutrition(fitness_app, [
        {"date": _today(), "calories": 500, "protein_g": 30,
         "carbs_g": 50, "fat_g": 20, "sodium_mg": 400,
         "confidence": 0.7, "source": "ai_text_estimate"},
    ])
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [],
    )
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["estimated_count"] == 1
    assert today["manual_count"] == 0


def test_nutrition_history_dedupes_clientless_dual_write_with_partial_legacy_fields(fitness_app, monkeypatch):
    """Regression for Codex audit round 5 finding 1: /api/add-nutrition
    builds the legacy NUTRITION_DATA entry from a smaller dict that
    only carries date + macros (see app.py:3110-3149). logged_at,
    item_name, and source_timestamp are added only to the food_log
    record. The dedupe signature must NOT include those fields,
    otherwise dual-write clientless entries never match.
    """
    legacy_partial = {  # Mirrors what /api/add-nutrition appends to NUTRITION_DATA.
        "date": _today(),
        "calories": 500, "protein_g": 30, "carbs_g": 50, "fat_g": 18,
        "sodium_mg": 600,
        "correction_state": "manual",
    }
    food_log_full = {  # Mirrors what add_food_log persists for the same call.
        "id": 1, "client_id": None,
        "date": _today(), "logged_at": f"{_today()}T13:00:00",
        "calories": 500, "protein_g": 30, "carbs_g": 50, "fat_g": 18,
        "sodium_mg": 600, "item_name": "Lunch",
        "correction_state": "manual",
    }
    _seed_nutrition(fitness_app, [legacy_partial])
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [food_log_full],
    )
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["calories"] == 500, (
        "dual-write entries with partial-legacy shape must dedupe "
        f"(got {today['calories']}, expected 500)"
    )
    assert today["entries_count"] == 1


def test_nutrition_history_dedupes_clientless_dual_write_entries(fitness_app, monkeypatch):
    """Regression for Codex audit round 4: /api/add-nutrition appends
    to BOTH NUTRITION_DATA and food_logs. When called without a
    client_id (which the endpoint allows), there's no shared key to
    dedupe on. The round-3 client_id filter kept all legacy entries
    with null client_id → double count.

    Per-entry content signature (date, logged_at, calories, protein,
    item_name) catches these clientless duplicates without breaking
    the legitimate mixed-source same-day case (different content
    on the same date).
    """
    same_meal_logged_at = f"{_today()}T13:00:00"
    # Same meal in both stores, no client_id.
    legacy_dup = {
        "date": _today(), "logged_at": same_meal_logged_at,
        "calories": 600, "protein_g": 35, "carbs_g": 50, "fat_g": 20,
        "sodium_mg": 800, "item_name": "Chicken bowl",
        "correction_state": "manual",
    }
    food_log_dup = {
        "id": 1, "client_id": None,
        "date": _today(), "logged_at": same_meal_logged_at,
        "calories": 600, "protein_g": 35, "carbs_g": 50, "fat_g": 20,
        "sodium_mg": 800, "item_name": "Chicken bowl",
        "correction_state": "manual",
    }
    _seed_nutrition(fitness_app, [legacy_dup])
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [food_log_dup],
    )
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["calories"] == 600, (
        "clientless dual-write entries must count once, not twice "
        f"(got {today['calories']}, expected 600)"
    )
    assert today["entries_count"] == 1


def test_nutrition_history_keeps_distinct_clientless_entries_with_different_content(fitness_app, monkeypatch):
    """Counter-test: clientless entries with DIFFERENT macros (e.g.
    a legacy breakfast and a food_log lunch, both lacking client_id
    but distinguishable by their macro tuple) must both count.
    The content dedupe must not collapse legitimate distinct meals.
    """
    legacy_breakfast = {
        "date": _today(), "logged_at": f"{_today()}T07:30:00",
        "calories": 350, "protein_g": 20, "carbs_g": 40, "fat_g": 10,
        "sodium_mg": 250, "item_name": "Oatmeal",
        "correction_state": "manual",
    }
    food_log_lunch = {
        "id": 1, "client_id": None,
        "date": _today(), "logged_at": f"{_today()}T12:30:00",
        "calories": 650, "protein_g": 40, "carbs_g": 60, "fat_g": 22,
        "sodium_mg": 900, "item_name": "Salad bowl",
        "correction_state": "manual",
    }
    _seed_nutrition(fitness_app, [legacy_breakfast])
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [food_log_lunch],
    )
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["calories"] == 1000, (
        "distinct clientless entries must both count "
        f"(got {today['calories']}, expected 350+650=1000)"
    )
    assert today["entries_count"] == 2


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
