"""FIT-93 — per-date food log endpoint contract.

`/api/food-logs/by-date/<YYYY-MM-DD>` backs the row-expand affordance on
the Body tab's nutrition trend card so the user can drill from a day's
total down to the individual meals.

These tests lock in:

* malformed date strings are rejected with 400 / invalid_field;
* same-day entries are returned, sorted by `logged_at` ascending;
* entries from other days are excluded;
* same dedupe behavior as `/api/nutrition-history` (no double-count when
  the same meal lives in both `food_logs` and the legacy
  `NUTRITION_DATA` store).
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit93-bydate-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    # Isolate the two backing stores so each test runs against a known
    # shape and doesn't depend on whatever's in food_logs.sqlite on disk.
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "_food_log_entries_for_context", lambda since=None, limit=None: [])
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "backfill_food_log_client_id", lambda *_args, **_kwargs: False)
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _today_minus(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


# ──────────────────────────────────────────────────────────────────
# Input validation
# ──────────────────────────────────────────────────────────────────

def test_invalid_date_returns_400(fitness_app):
    res = fitness_app.app.test_client().get("/api/food-logs/by-date/not-a-date")
    assert res.status_code == 400
    body = res.get_json()
    assert body["error"]["code"] == "invalid_field"


def test_shape_valid_but_impossible_date_returns_400(fitness_app):
    """Regex shape isn't enough — 2026-99-99 looks YYYY-MM-DD but isn't
    a real calendar date. Codex round 1 audit finding."""
    res = fitness_app.app.test_client().get("/api/food-logs/by-date/2026-99-99")
    assert res.status_code == 400
    body = res.get_json()
    assert body["error"]["code"] == "invalid_field"


def test_non_canonical_date_returns_400(fitness_app):
    """`2026-5-1` parses with strptime but stored dates are zero-padded
    `2026-05-01`, so the comparison would silently miss. Codex round 2
    audit finding."""
    res = fitness_app.app.test_client().get("/api/food-logs/by-date/2026-5-1")
    assert res.status_code == 400
    body = res.get_json()
    assert body["error"]["code"] == "invalid_field"


def test_returned_entries_use_bounded_projection(fitness_app, monkeypatch):
    """Codex round 1: response shape must be a stable projection, not
    raw food_log rows. Sensitive / internal fields like
    `original_estimate` and `uncertainty_notes` must not leak."""
    today = _today()
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [{
            "client_id": "fl-1", "date": today, "logged_at": f"{today}T08:00:00",
            "item_name": "Eggs", "calories": 200, "protein_g": 14,
            "carbs_g": 2, "fat_g": 14, "sodium_mg": 200,
            "source": "ai_text_estimate", "confidence": 0.8,
            "correction_state": "accepted", "from_image": False,
            # Should NOT leak through the projection:
            "original_estimate": {"raw_prompt": "do not leak"},
            "uncertainty_notes": ["internal"],
            "id": 42,
        }],
    )
    res = fitness_app.app.test_client().get(f"/api/food-logs/by-date/{today}")
    entry = res.get_json()["entries"][0]
    # FIT-100: `date` joins the projection so the correction flow can
    # anchor the upsert to the original meal date even when `logged_at`
    # is absent (legacy rows).
    expected_keys = {
        "client_id", "date", "logged_at", "item_name", "portion_description", "meal_type",
        "calories", "protein_g", "carbs_g", "fat_g", "sodium_mg",
        "source", "confidence", "correction_state", "accepted_estimate", "from_image",
    }
    assert set(entry.keys()) == expected_keys, (
        f"projection must be tight; unexpected keys: {set(entry) - expected_keys}"
    )
    assert entry["accepted_estimate"] is None


def test_projection_derives_photo_provenance_from_sanitized_original(fitness_app, monkeypatch):
    today = _today()
    monkeypatch.setattr(
        fitness_app,
        "_food_log_entries_for_context",
        lambda since=None, limit=None: [{
            "client_id": "photo-fallback-1",
            "date": today,
            "logged_at": f"{today}T12:00:00",
            "item_name": "Restaurant combo",
            "calories": 900,
            "source": "ai_text_estimate",
            "correction_state": "corrected",
            "original_estimate": {"from_image": True},
        }],
    )

    response = fitness_app.app.test_client().get(f"/api/food-logs/by-date/{today}")

    assert response.status_code == 200
    assert response.get_json()["entries"][0]["from_image"] is True


def test_valid_iso_date_returns_200_even_when_empty(fitness_app):
    res = fitness_app.app.test_client().get(f"/api/food-logs/by-date/{_today()}")
    assert res.status_code == 200
    body = res.get_json()
    assert body["date"] == _today()
    assert body["entries"] == []
    assert body["count"] == 0


# ──────────────────────────────────────────────────────────────────
# Returns same-day entries, excludes other dates
# ──────────────────────────────────────────────────────────────────

def test_returns_same_day_entries_from_food_logs(fitness_app, monkeypatch):
    today = _today()
    food_log_entries = [
        {"client_id": "fl-1", "date": today, "logged_at": f"{today}T08:00:00",
         "item_name": "Eggs", "calories": 200, "protein_g": 14, "carbs_g": 2, "fat_g": 14},
        {"client_id": "fl-2", "date": today, "logged_at": f"{today}T12:30:00",
         "item_name": "Burger", "calories": 540, "protein_g": 28, "carbs_g": 42, "fat_g": 30},
    ]
    monkeypatch.setattr(
        fitness_app,
        "_food_log_entries_for_context",
        lambda since=None, limit=None: food_log_entries,
    )

    res = fitness_app.app.test_client().get(f"/api/food-logs/by-date/{today}")
    body = res.get_json()
    assert body["count"] == 2
    names = [e["item_name"] for e in body["entries"]]
    assert names == ["Eggs", "Burger"], "entries must be sorted by logged_at ascending"


def test_excludes_other_days(fitness_app, monkeypatch):
    today = _today()
    yesterday = _today_minus(1)
    food_log_entries = [
        {"client_id": "fl-1", "date": today, "logged_at": f"{today}T08:00:00",
         "item_name": "Eggs", "calories": 200},
        {"client_id": "fl-2", "date": yesterday, "logged_at": f"{yesterday}T19:00:00",
         "item_name": "Dinner yesterday", "calories": 800},
    ]
    monkeypatch.setattr(
        fitness_app,
        "_food_log_entries_for_context",
        lambda since=None, limit=None: food_log_entries,
    )

    res = fitness_app.app.test_client().get(f"/api/food-logs/by-date/{today}")
    body = res.get_json()
    assert body["count"] == 1
    assert body["entries"][0]["item_name"] == "Eggs"


# ──────────────────────────────────────────────────────────────────
# Dedupe parity with /api/nutrition-history
# ──────────────────────────────────────────────────────────────────

def test_deduplicates_dual_write_by_client_id(fitness_app, monkeypatch):
    """When /api/add-nutrition writes the same entry to both stores with
    the same client_id, /api/food-logs/by-date must not double-count it.
    Mirrors the dedupe rule the nutrition-history endpoint uses."""
    today = _today()
    shared = {
        "client_id": "shared-cid", "date": today, "logged_at": f"{today}T10:00:00",
        "item_name": "Shake", "calories": 300, "protein_g": 25, "carbs_g": 30, "fat_g": 8,
    }
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [shared],
    )
    fitness_app.NUTRITION_DATA[:] = [shared]

    res = fitness_app.app.test_client().get(f"/api/food-logs/by-date/{today}")
    body = res.get_json()
    assert body["count"] == 1, "shared client_id must be counted once, not twice"


def test_keeps_identical_macro_clientless_entries_after_backfill(fitness_app, monkeypatch):
    """FIT-70: by-date details use the same client_id-only dedupe rule
    as nutrition-history. Without a shared client_id, same-macro entries
    must remain visible as distinct meals.
    """
    today = _today()
    food_log = {
        "date": today, "logged_at": f"{today}T10:00:00",
        "item_name": "Shake", "calories": 300, "protein_g": 25, "carbs_g": 30, "fat_g": 8,
        "sodium_mg": 200,
    }
    legacy_match = {
        "date": today, "calories": 300, "protein_g": 25, "carbs_g": 30, "fat_g": 8,
        "sodium_mg": 200, "item_name": "Shake (legacy)",
    }
    monkeypatch.setattr(
        fitness_app, "_food_log_entries_for_context",
        lambda since=None, limit=None: [food_log],
    )
    fitness_app.NUTRITION_DATA[:] = [legacy_match]

    res = fitness_app.app.test_client().get(f"/api/food-logs/by-date/{today}")
    body = res.get_json()
    assert body["count"] == 2, (
        "same-macro entries without a shared client_id must both be returned"
    )
