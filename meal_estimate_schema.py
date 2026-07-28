"""Canonical meal estimate schema and validation helpers (FIT-5).

The meal intake endpoint accepts text and photo estimates from different
producers, but they must share one public contract before policy or
persistence sees them. This module keeps that contract pure and reusable:
no Flask imports, no database calls, and no raw prompt/image fields.
"""

from __future__ import annotations

import math
from typing import Any


ALLOWED_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")

PUBLIC_ESTIMATE_FIELDS = (
    "item_name",
    "portion_description",
    "meal_type",
    "calories",
    "protein_g",
    "carbs_g",
    "fat_g",
    "sodium_mg",
    "fiber_g",
    "confidence",
    "ambiguous",
    "uncertainty_notes",
    "source",
    "external_food_id",
    "verified_source_url",
    "data_fetched_at",
    "portion_basis",
    "brand_id",
    "underlying_source",
    "off_attribution",
    "personal_vocab_phrase",
)

PUBLIC_PROVENANCE_FIELDS = (
    "external_food_id",
    "verified_source_url",
    "data_fetched_at",
    "portion_basis",
    "brand_id",
    "underlying_source",
    "off_attribution",
    "personal_vocab_phrase",
)

CALORIE_MAX = 5000
MACRO_GRAM_MAX = 500
SODIUM_MG_MAX = 12000
SODIUM_UNKNOWN_NOTE = "Sodium is unknown; 0 is a compatibility placeholder."


class MealEstimateValidationError(ValueError):
    """Raised when an estimate cannot satisfy the FIT-5 public schema."""


def _number(value: Any, field: str, *, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MealEstimateValidationError(f"{field} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0 or (maximum is not None and numeric > maximum):
        raise MealEstimateValidationError(f"{field} outside plausible range")
    return numeric


def _string_or_none(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MealEstimateValidationError(f"{field} must be string or null")
    cleaned = value.strip()
    return cleaned or None


def _provenance_dict(value: Any, field: str) -> dict | str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if not isinstance(value, dict):
        raise MealEstimateValidationError(f"{field} must be object, string, or null")
    safe = {}
    for key, item in value.items():
        if isinstance(key, str) and (item is None or isinstance(item, (str, int, float, bool))):
            safe[key] = item
    return safe or None


def sanitize_meal_estimate(
    raw: dict,
    *,
    source: str | None = None,
    legacy_defaults: bool = False,
    plausible_ranges: bool = False,
) -> dict:
    """Validate and return an API-safe meal estimate.

    Unknown keys are dropped so model traces, raw prompts, image paths, and
    image bytes cannot leak into responses or food-log snapshots.
    """
    if not isinstance(raw, dict):
        raise MealEstimateValidationError("estimate must be an object")
    if legacy_defaults:
        raw = {
            "portion_description": None,
            "meal_type": "snack",
            "sodium_mg": 0,
            "fiber_g": 0,
            "confidence": 0.0,
            "ambiguous": True,
            "uncertainty_notes": [],
            **raw,
        }

    item_name = raw.get("item_name")
    if not isinstance(item_name, str) or not item_name.strip():
        raise MealEstimateValidationError("item_name is required")

    meal_type = raw.get("meal_type") or "snack"
    if meal_type not in ALLOWED_MEAL_TYPES:
        raise MealEstimateValidationError("invalid meal_type")

    notes = raw.get("uncertainty_notes", [])
    if notes is None:
        notes = []
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise MealEstimateValidationError("uncertainty_notes must be strings")
    notes = [note.strip() for note in notes if note.strip()]
    if not isinstance(raw.get("ambiguous"), bool):
        raise MealEstimateValidationError("ambiguous must be boolean")

    confidence = _number(raw.get("confidence"), "confidence", maximum=1.0)
    estimate_source = source or raw.get("source")
    if not isinstance(estimate_source, str) or not estimate_source.strip():
        raise MealEstimateValidationError("source is required")

    sodium_value = raw.get("sodium_mg")
    sodium_unknown = sodium_value is None
    if sodium_unknown:
        sodium_mg = 0
        if SODIUM_UNKNOWN_NOTE not in notes:
            notes = [*notes, SODIUM_UNKNOWN_NOTE]
    else:
        sodium_mg = int(round(_number(
            sodium_value, "sodium_mg", maximum=SODIUM_MG_MAX if plausible_ranges else None
        )))

    estimate = {
        "item_name": item_name.strip(),
        "portion_description": _string_or_none(raw.get("portion_description"), "portion_description"),
        "meal_type": meal_type,
        "calories": int(round(_number(
            raw.get("calories"), "calories", maximum=CALORIE_MAX if plausible_ranges else None
        ))),
        "protein_g": round(_number(
            raw.get("protein_g"), "protein_g", maximum=MACRO_GRAM_MAX if plausible_ranges else None
        ), 1),
        "carbs_g": round(_number(
            raw.get("carbs_g"), "carbs_g", maximum=MACRO_GRAM_MAX if plausible_ranges else None
        ), 1),
        "fat_g": round(_number(
            raw.get("fat_g"), "fat_g", maximum=MACRO_GRAM_MAX if plausible_ranges else None
        ), 1),
        "sodium_mg": sodium_mg,
        "fiber_g": round(_number(
            raw.get("fiber_g"), "fiber_g", maximum=MACRO_GRAM_MAX if plausible_ranges else None
        ), 1),
        "confidence": round(confidence, 2),
        "ambiguous": raw["ambiguous"] or sodium_unknown,
        "uncertainty_notes": notes,
        "source": estimate_source.strip(),
    }
    for key in PUBLIC_PROVENANCE_FIELDS:
        if key in {"off_attribution", "personal_vocab_phrase"}:
            continue
        value = _string_or_none(raw.get(key), key)
        if value is not None:
            estimate[key] = value
    off_attribution = _provenance_dict(raw.get("off_attribution"), "off_attribution")
    if off_attribution is not None:
        estimate["off_attribution"] = off_attribution
    personal_vocab_phrase = _string_or_none(raw.get("personal_vocab_phrase"), "personal_vocab_phrase")
    if personal_vocab_phrase is not None:
        estimate["personal_vocab_phrase"] = personal_vocab_phrase
    return estimate


def manual_review_estimate(*, text: str = "", source: str = "manual_review_estimate") -> dict:
    """Low-confidence fallback used when an estimator response is unusable."""
    item = (text or "").strip()[:80] or "Meal"
    return {
        "item_name": item,
        "portion_description": None,
        "meal_type": "snack",
        "calories": 0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "sodium_mg": 0,
        "fiber_g": 0.0,
        "confidence": 0.0,
        "ambiguous": True,
        "uncertainty_notes": ["Estimate unavailable — enter or accept manually before it counts."],
        "source": source,
    }
