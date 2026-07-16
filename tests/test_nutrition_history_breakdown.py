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


REFERENCE_NOW = datetime(2026, 7, 1, 12, 0, 0)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(REFERENCE_NOW.timestamp(), tz=tz)


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit13-history-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "datetime", FrozenDateTime)
    monkeypatch.setattr(module, "_food_log_entries_for_context", lambda since=None, limit=None: [])
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "backfill_food_log_client_id", lambda *_args, **_kwargs: False)
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _today() -> str:
    return REFERENCE_NOW.strftime("%Y-%m-%d")


def _today_minus(days: int) -> str:
    return (REFERENCE_NOW - timedelta(days=days)).strftime("%Y-%m-%d")


def _seed_nutrition(module, entries):
    """Replace the in-memory nutrition data for the duration of one test."""
    module.NUTRITION_DATA[:] = list(entries)


def test_backfill_legacy_nutrition_client_ids_is_idempotent(fitness_app, monkeypatch):
    saved_payloads = []
    linked_matches = []
    existing_client_id = "already-set"
    _seed_nutrition(fitness_app, [
        {
            "date": _today(), "logged_at": f"{_today()}T08:00:00",
            "calories": 450, "protein_g": 30, "carbs_g": 40, "fat_g": 12,
            "sodium_mg": 500, "notes": "breakfast note",
            "correction_state": "manual",
        },
        {
            "client_id": existing_client_id,
            "date": _today(), "logged_at": f"{_today()}T12:30:00",
            "calories": 650, "protein_g": 42, "carbs_g": 55, "fat_g": 22,
            "sodium_mg": 900, "notes": "already linked lunch",
            "correction_state": "manual",
        },
    ])

    linked_client_ids = set()

    def fake_link(user_id, client_id, match):
        linked_matches.append((user_id, client_id, match))
        if client_id in linked_client_ids:
            return False
        linked_client_ids.add(client_id)
        return True

    monkeypatch.setattr(fitness_app, "save_json", lambda _path, data: saved_payloads.append([dict(e) for e in data]))
    monkeypatch.setattr(fitness_app, "backfill_food_log_client_id", fake_link)

    first = fitness_app.backfill_legacy_nutrition_client_ids(user_id=7)
    generated_client_id = fitness_app.NUTRITION_DATA[0]["client_id"]
    second = fitness_app.backfill_legacy_nutrition_client_ids(user_id=7)

    assert generated_client_id.startswith("legacy-nutrition-")
    assert fitness_app.NUTRITION_DATA[1]["client_id"] == existing_client_id
    assert first == {"legacy_backfilled": 1, "food_logs_linked": 2}
    assert second == {"legacy_backfilled": 0, "food_logs_linked": 0}
    assert len(saved_payloads) == 1, "second run must not rewrite already-backfilled JSON"
    assert linked_matches[0][0] == 7
    assert linked_matches[0][1] == generated_client_id
    assert linked_matches[0][2]["context_note"] == "breakfast note"
    assert "logged_at" not in linked_matches[0][2], "mismatched logged_at must not block linking"


def test_backfill_legacy_nutrition_client_ids_does_not_link_macro_only_rows(fitness_app, monkeypatch):
    link_attempts = []
    _seed_nutrition(fitness_app, [
        {
            "date": _today(),
            "calories": 500, "protein_g": 30, "carbs_g": 50, "fat_g": 18,
            "sodium_mg": 600,
            "correction_state": "manual",
        },
    ])
    monkeypatch.setattr(
        fitness_app,
        "backfill_food_log_client_id",
        lambda *_args, **_kwargs: link_attempts.append(_args) or True,
    )

    result = fitness_app.backfill_legacy_nutrition_client_ids(user_id=7)

    assert result == {"legacy_backfilled": 1, "food_logs_linked": 0}
    assert fitness_app.NUTRITION_DATA[0]["client_id"].startswith("legacy-nutrition-")
    assert link_attempts == []


def test_nutrition_history_fixture_does_not_read_real_food_logs_by_default(fitness_app, monkeypatch):
    """The fixture isolates this suite from the developer's local food_logs DB."""
    def fail_get_food_logs(*_args, **_kwargs):
        raise AssertionError("nutrition-history tests must not read real food_logs")

    monkeypatch.setattr(fitness_app, "get_food_logs", fail_get_food_logs)
    _seed_nutrition(fitness_app, [
        {"date": _today(), "calories": 500, "protein_g": 25,
         "carbs_g": 40, "fat_g": 20, "sodium_mg": 700,
         "correction_state": "accepted"},
    ])
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["calories"] == 500
    assert today["entries_count"] == 1


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


def test_nutrition_history_keeps_identical_macro_clientless_entries_after_backfill(fitness_app, monkeypatch):
    """FIT-70: nutrition-history must not use the old date/macro content
    signature once legacy rows are backfilled. A legacy row and a
    food_log row with identical macros but no shared client_id are
    distinct meals unless the migration can link them explicitly.
    """
    legacy_partial = {
        "date": _today(),
        "calories": 500, "protein_g": 30, "carbs_g": 50, "fat_g": 18,
        "sodium_mg": 600,
        "correction_state": "manual",
    }
    food_log_full = {
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
    assert today["calories"] == 1000, (
        "identical-macro entries without a shared client_id must both count "
        f"(got {today['calories']}, expected 500+500=1000)"
    )
    assert today["entries_count"] == 2


def test_nutrition_history_dedupes_migrated_dual_write_entries_by_client_id(fitness_app, monkeypatch):
    """FIT-70: after migration links a legacy row and its food_log row,
    nutrition-history dedupes by client_id only.
    """
    same_meal_logged_at = f"{_today()}T13:00:00"
    shared_client_id = "legacy-nutrition-linked"
    legacy_dup = {
        "client_id": shared_client_id,
        "date": _today(), "logged_at": same_meal_logged_at,
        "calories": 600, "protein_g": 35, "carbs_g": 50, "fat_g": 20,
        "sodium_mg": 800, "item_name": "Chicken bowl",
        "correction_state": "manual",
    }
    food_log_dup = {
        "id": 1, "client_id": shared_client_id,
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
        "migrated dual-write entries must count once, not twice "
        f"(got {today['calories']}, expected 600)"
    )
    assert today["entries_count"] == 1


def test_add_nutrition_generated_client_id_dedupes_history(fitness_app, monkeypatch):
    """FIT-70: /api/add-nutrition still dual-writes to legacy JSON and
    food_logs. If the request omits client_id, the server must generate one
    shared by both copies so the client_id-only history merge counts it once.
    """
    food_log_records = []

    def fake_add_food_log(_user_id, record):
        food_log_records.append(record)
        return record

    _seed_nutrition(fitness_app, [])
    monkeypatch.setattr(fitness_app, "add_food_log", fake_add_food_log)
    response = fitness_app.app.test_client().post(
        "/api/add-nutrition",
        json={
            "date": _today(),
            "calories": 500,
            "protein_g": 30,
            "carbs_g": 50,
            "fat_g": 18,
            "sodium_mg": 600,
        },
    )
    assert response.status_code == 200
    generated_client_id = fitness_app.NUTRITION_DATA[0]["client_id"]
    assert generated_client_id.startswith("nutrition-")
    assert food_log_records[0]["client_id"] == generated_client_id

    monkeypatch.setattr(
        fitness_app,
        "_food_log_entries_for_context",
        lambda since=None, limit=None: list(food_log_records),
    )
    today = next(d for d in fitness_app.app.test_client()
                 .get("/api/nutrition-history").get_json()["history"]
                 if d["date"] == _today())
    assert today["calories"] == 500
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
