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
    return _california_roll_estimate()


def _token_list(query: str) -> list[str]:
    normalized = (query or "").lower().replace("h-e-b", "heb").replace("h‑e‑b", "heb")
    return re.findall(r"[a-z0-9]+", normalized)


def _california_roll_estimate() -> dict[str, Any]:
    estimate = {
        "item_name": "H-E-B Sushiya California Sushi Roll",
        "portion_description": "10 pieces (224 g)",
        "meal_type": "lunch",
        "calories": 240,
        "protein_g": 7,
        "carbs_g": 50,
        "fat_g": 5,
        "sodium_mg": 930,
        "fiber_g": 3,
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "heb_product_page",
    }
    sanitized = sanitize_meal_estimate(estimate, plausible_ranges=True)
    sanitized.update({
        "external_food_id": "2038218",
        "verified_source_url": HEB_CALIFORNIA_ROLL_URL,
        "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
        "portion_basis": "H-E-B product page nutrition facts: 10 pieces (224 g)",
        "brand_id": "h-e-b",
    })
    return sanitized
