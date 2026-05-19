"""Cache-first branded and generic nutrition lookup for meal logging."""

from __future__ import annotations

from datetime import datetime, timedelta
from difflib import get_close_matches
import json
import os
from pathlib import Path
from typing import Any

import data_store
import nutritionix_client
import usda_fdc_client
from meal_estimate_schema import sanitize_meal_estimate


CACHE_TTL_DAYS = 180
SOURCE_PRIORITY = ("cache", "nutritionix", "usda_fdc", "snapshot")
NO_KEY_SOURCE_PRIORITY = ("cache", "nutritionix", "snapshot", "usda_fdc")
SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "nutrition_snapshot.json"
_SNAPSHOT_CACHE: dict[str, dict[str, Any]] | None = None
KNOWN_BRANDS = {
    "chipotle",
    "starbucks",
    "mcdonalds",
    "subway",
    "chick-fil-a",
    "chickfila",
}
BRAND_TYPOS = {
    "mcdonalds": {"mcdonals", "mcdonlds", "mcdonald"},
    "starbucks": {"starbuks", "starbukcs"},
    "chipotle": {"chipotole", "chipoltle", "chiptole"},
}
PLURALS = {
    "burritos": "burrito",
    "tacos": "taco",
    "wraps": "wrap",
    "sandwiches": "sandwich",
    "salads": "salad",
    "bowls": "bowl",
    "bananas": "banana",
    "eggs": "egg",
    "almonds": "almond",
    "potatoes": "potato",
}
CUSTOMIZABLE_CHAIN_TOKENS = {"chipotle"}
CUSTOMIZABLE_ITEM_TOKENS = {"burrito", "bowl", "taco", "tacos", "salad"}
PROTEIN_TOKENS = {
    "chicken",
    "steak",
    "barbacoa",
    "carnitas",
    "sofritas",
    "tofu",
    "beef",
    "pork",
}
SNAPSHOT_PREFIX_TOKENS = {
    "a",
    "an",
    "the",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "half",
    "whole",
    "small",
    "medium",
    "large",
}


def normalize_meal_text(text: str) -> str:
    """Normalize user text for cache keys and typo-tolerant source lookup."""
    tokens = []
    for raw in (text or "").lower().replace("'", "").split():
        token = raw.strip(".,!?;:()[]{}\"")
        token = PLURALS.get(token, token)
        for brand, typos in BRAND_TYPOS.items():
            if token in typos:
                token = brand
                break
        else:
            match = get_close_matches(token, KNOWN_BRANDS, n=1, cutoff=0.8)
            if match:
                token = match[0]
        if token:
            tokens.append(token)
    return " ".join(tokens)


def lookup(
    text: str,
    *,
    brand_hint: str | None = None,
    source_priority: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any] | None:
    """Return a sanitized estimate from cache/Nutritionix/USDA, or None."""
    normalized = normalize_meal_text(f"{brand_hint or ''} {text}".strip())
    if not normalized:
        return None
    priorities = tuple(source_priority) if source_priority else _default_source_priority()

    for source in priorities:
        if source == "cache":
            cached = _cache_lookup(normalized)
            if cached:
                return cached
        elif source == "snapshot":
            snapshot = snapshot_lookup(normalized)
            if snapshot:
                return snapshot
        elif source == "nutritionix":
            nutritionix = _nutritionix_lookup(text, normalized)
            if nutritionix:
                data_store.save_branded_lookup_cache(normalized, nutritionix["source"], nutritionix)
                return nutritionix
        elif source == "usda_fdc":
            usda = _usda_lookup(text, normalized)
            if usda:
                data_store.save_branded_lookup_cache(normalized, usda["source"], usda)
                return usda
    return None


def _default_source_priority() -> tuple[str, ...]:
    if os.environ.get("USDA_FDC_API_KEY"):
        return SOURCE_PRIORITY
    return NO_KEY_SOURCE_PRIORITY


def snapshot_lookup(normalized_text: str) -> dict[str, Any] | None:
    """Return an offline snapshot estimate by normalized text."""
    key = normalize_meal_text(normalized_text)
    if not key:
        return None
    snapshot = _load_snapshot()
    item = _snapshot_item(snapshot, key)
    if not item:
        return None
    estimate = {
        "item_name": item.get("item_name"),
        "portion_description": item.get("portion_description"),
        "meal_type": item.get("meal_type") or "snack",
        "calories": item.get("calories"),
        "protein_g": item.get("protein_g"),
        "carbs_g": item.get("carbs_g"),
        "fat_g": item.get("fat_g"),
        "sodium_mg": item.get("sodium_mg", 0),
        "fiber_g": item.get("fiber_g", 0),
        "confidence": 0.62,
        "ambiguous": True,
        "uncertainty_notes": ["Offline snapshot uses a reference portion; confirm serving size before logging."],
        "source": "offline_snapshot",
        "external_food_id": item.get("external_food_id"),
        "verified_source_url": item.get("verified_source_url"),
        "data_fetched_at": item.get("data_fetched_at") or _snapshot_version(),
        "portion_basis": item.get("portion_basis"),
    }
    return _sanitize_with_provenance(estimate)


def _snapshot_item(snapshot: dict[str, dict[str, Any]], key: str) -> dict[str, Any] | None:
    item = snapshot.get(key)
    if item:
        return item
    tokens = key.split()
    for snapshot_key in sorted(snapshot, key=lambda candidate: len(candidate.split()), reverse=True):
        candidate_tokens = snapshot_key.split()
        prefix_tokens = tokens[: -len(candidate_tokens)]
        if (
            prefix_tokens
            and tokens[-len(candidate_tokens):] == candidate_tokens
            and all(_is_snapshot_prefix_token(token) for token in prefix_tokens)
        ):
            return snapshot[snapshot_key]
    return None


def _is_snapshot_prefix_token(token: str) -> bool:
    return token in SNAPSHOT_PREFIX_TOKENS or token.replace(".", "", 1).isdigit()


def _load_snapshot() -> dict[str, dict[str, Any]]:
    global _SNAPSHOT_CACHE
    if _SNAPSHOT_CACHE is not None:
        return _SNAPSHOT_CACHE
    try:
        raw = json.loads(SNAPSHOT_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        _SNAPSHOT_CACHE = {}
        return _SNAPSHOT_CACHE
    _SNAPSHOT_CACHE = {
        normalize_meal_text(item.get("normalized_text") or item.get("item_name") or ""): item
        for item in raw.get("items", [])
        if isinstance(item, dict)
    }
    return _SNAPSHOT_CACHE


def _snapshot_version() -> str:
    try:
        raw = json.loads(SNAPSHOT_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return "offline_snapshot"
    return str(raw.get("version") or raw.get("generated_at") or "offline_snapshot")


def _cache_lookup(normalized: str) -> dict[str, Any] | None:
    row = data_store.get_branded_lookup_cache(normalized)
    if not row:
        return None
    fetched_at = _parse_iso(row.get("fetched_at"))
    if not fetched_at or datetime.now() - fetched_at > timedelta(days=CACHE_TTL_DAYS):
        return None
    payload = row.get("response_json")
    if not isinstance(payload, dict):
        return None
    estimate = dict(payload)
    estimate["source"] = "local_cache"
    estimate.setdefault("underlying_source", row.get("source"))
    return _sanitize_with_provenance(estimate)


def _nutritionix_lookup(text: str, normalized: str) -> dict[str, Any] | None:
    payload = nutritionix_client.natural_nutrients(text)
    foods = payload.get("foods") if isinstance(payload, dict) else None
    if not foods:
        return None
    food = foods[0]
    if not isinstance(food, dict):
        return None
    ambiguous = _needs_modifier_review(normalized)
    notes = []
    if ambiguous:
        notes.append("Customizable item is missing protein or modifier details.")
    brand = food.get("brand_name") or _brand_from_text(normalized)
    item_name = " ".join(str(part).strip() for part in (brand, food.get("food_name")) if part).strip()
    estimate = {
        "item_name": item_name or str(food.get("food_name") or "Meal"),
        "portion_description": _portion_from_nutritionix(food),
        "meal_type": "snack",
        "calories": food.get("nf_calories"),
        "protein_g": food.get("nf_protein"),
        "carbs_g": food.get("nf_total_carbohydrate"),
        "fat_g": food.get("nf_total_fat"),
        "sodium_mg": food.get("nf_sodium") if food.get("nf_sodium") is not None else 0,
        "fiber_g": food.get("nf_dietary_fiber") if food.get("nf_dietary_fiber") is not None else 0,
        "confidence": 0.55 if ambiguous else 0.85,
        "ambiguous": ambiguous,
        "uncertainty_notes": notes,
        "source": "nutritionix",
        "external_food_id": food.get("nix_item_id") or food.get("tag_id") or food.get("food_name"),
        "verified_source_url": "https://www.nutritionix.com/",
        "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
        "portion_basis": _portion_from_nutritionix(food),
        "brand_id": _brand_from_text(normalized),
    }
    return _sanitize_with_provenance(estimate)


def _usda_lookup(text: str, _normalized: str) -> dict[str, Any] | None:
    payload = usda_fdc_client.search_foods(text)
    foods = payload.get("foods") if isinstance(payload, dict) else None
    if not foods:
        return None
    food = foods[0]
    nutrients = {n.get("nutrientName"): n.get("value") for n in food.get("foodNutrients", []) if isinstance(n, dict)}
    fdc_id = food.get("fdcId")
    estimate = {
        "item_name": food.get("description") or "Food",
        "portion_description": "100 g",
        "meal_type": "snack",
        "calories": nutrients.get("Energy") or nutrients.get("Energy (Atwater General Factors)"),
        "protein_g": nutrients.get("Protein"),
        "carbs_g": nutrients.get("Carbohydrate, by difference"),
        "fat_g": nutrients.get("Total lipid (fat)"),
        "sodium_mg": nutrients.get("Sodium, Na") if nutrients.get("Sodium, Na") is not None else 0,
        "fiber_g": nutrients.get("Fiber, total dietary") if nutrients.get("Fiber, total dietary") is not None else 0,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "usda_fdc",
        "external_food_id": str(fdc_id) if fdc_id is not None else None,
        "verified_source_url": f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{fdc_id}/nutrients" if fdc_id else "https://fdc.nal.usda.gov/",
        "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
        "portion_basis": "100 g USDA FoodData Central reference portion",
    }
    return _sanitize_with_provenance(estimate)


def _sanitize_with_provenance(estimate: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_meal_estimate(estimate, plausible_ranges=True)
    for key in (
        "external_food_id",
        "verified_source_url",
        "data_fetched_at",
        "portion_basis",
        "brand_id",
        "underlying_source",
        "off_attribution",
    ):
        if estimate.get(key) is not None:
            sanitized[key] = estimate[key]
    return sanitized


def _portion_from_nutritionix(food: dict[str, Any]) -> str | None:
    qty = food.get("serving_qty")
    unit = food.get("serving_unit")
    weight = food.get("serving_weight_grams")
    pieces = " ".join(str(part).strip() for part in (qty, unit) if part not in (None, "")).strip()
    if weight:
        return f"{pieces} ({weight:g} g)" if pieces else f"{weight:g} g"
    return pieces or None


def _needs_modifier_review(normalized: str) -> bool:
    tokens = set(normalized.split())
    return (
        bool(tokens & CUSTOMIZABLE_CHAIN_TOKENS)
        and bool(tokens & CUSTOMIZABLE_ITEM_TOKENS)
        and not bool(tokens & PROTEIN_TOKENS)
    )


def _brand_from_text(normalized: str) -> str | None:
    for token in normalized.split():
        if token in KNOWN_BRANDS:
            return token
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
