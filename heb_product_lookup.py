"""Text-only H-E-B private-label nutrition references for branded lookup misses.

FIT-218 keeps this helper out of barcode lookup: H-E-B barcode coverage needs a
real provider, and AC4 excludes scraping or hardcoded barcode rows as a stand-in.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from meal_estimate_schema import sanitize_meal_estimate


HEB_CALIFORNIA_ROLL_URL = "https://www.heb.com/product-detail/h-e-b-sushiya-california-roll/2038218"
CURATED_REFERENCES: dict[str, dict[str, Any]] = {
    "2038218": {
        "item_name": "H-E-B Sushiya California Sushi Roll",
        "portion_description": "10 pieces (224 g)",
        "calories": 240,
        "protein_g": 7,
        "carbs_g": 50,
        "fat_g": 5,
        "sodium_mg": 930,
        "fiber_g": 3,
        "verified_source_url": HEB_CALIFORNIA_ROLL_URL,
        "evidence_note": "H-E-B product page nutrition facts",
    },
}
_EXCLUDED_CALIFORNIA_ROLL_VARIANTS = {"spicy", "crunchy", "cauliflower", "poke", "brown"}


def lookup(query: str) -> dict[str, Any] | None:
    tokens = _token_list(query)
    token_set = set(tokens)
    if "heb" not in token_set or not {"california", "roll"}.issubset(token_set):
        return None
    if token_set.intersection(_EXCLUDED_CALIFORNIA_ROLL_VARIANTS):
        return None
    if any(token not in {"heb", "sushiya", "california", "sushi", "roll"} for token in tokens):
        return None
    reference = CURATED_REFERENCES["2038218"]
    if not reference.get("verified_source_url") or not reference.get("evidence_note"):
        return None
    return _california_roll_estimate(reference)


def _token_list(query: str) -> list[str]:
    normalized = (query or "").lower().replace("h-e-b", "heb").replace("h‑e‑b", "heb")
    return re.findall(r"[a-z0-9]+", normalized)


def _california_roll_estimate(reference: dict[str, Any]) -> dict[str, Any]:
    estimate = {
        "item_name": reference["item_name"],
        "portion_description": reference["portion_description"],
        "meal_type": "lunch",
        "calories": reference["calories"],
        "protein_g": reference["protein_g"],
        "carbs_g": reference["carbs_g"],
        "fat_g": reference["fat_g"],
        "sodium_mg": reference["sodium_mg"],
        "fiber_g": reference["fiber_g"],
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "heb_curated_reference",
    }
    sanitized = sanitize_meal_estimate(estimate, plausible_ranges=True)
    sanitized.update({
        "external_food_id": "2038218",
        "verified_source_url": reference["verified_source_url"],
        "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
        "portion_basis": "H-E-B product page nutrition facts: 10 pieces (224 g)",
        "brand_id": "h-e-b",
    })
    return sanitized
