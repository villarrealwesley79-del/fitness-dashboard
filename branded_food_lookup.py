"""Cache-first branded and generic nutrition lookup for meal logging."""

from __future__ import annotations

from datetime import datetime, timedelta
from difflib import get_close_matches
from typing import Any

import data_store
import nutritionix_client
import usda_fdc_client
from meal_estimate_schema import sanitize_meal_estimate


CACHE_TTL_DAYS = 180
SOURCE_PRIORITY = ("cache", "nutritionix", "usda_fdc")
MULTI_ITEM_TOKENS = {"and", "with", "plus", "&", "+", "combo", "meal", "plate"}
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
    priorities = tuple(source_priority or SOURCE_PRIORITY)

    if "cache" in priorities:
        cached = _cache_lookup(normalized)
        if cached:
            return cached
    if "nutritionix" in priorities:
        nutritionix = _nutritionix_lookup(text, normalized)
        if nutritionix:
            data_store.save_branded_lookup_cache(normalized, nutritionix["source"], nutritionix)
            return nutritionix
    if "usda_fdc" in priorities:
        usda = _usda_lookup(text, normalized)
        if usda:
            data_store.save_branded_lookup_cache(normalized, usda["source"], usda)
            return usda
    return None


def should_attempt_direct_lookup(text: str, *, brand_hint: str | None = None) -> bool:
    """Return True when text is safe to satisfy from a single external hit."""
    normalized = normalize_meal_text(f"{brand_hint or ''} {text}".strip())
    tokens = normalized.split()
    if not tokens:
        return False
    if any(token in tokens for token in MULTI_ITEM_TOKENS):
        return False
    if brand_hint or _brand_from_text(normalized):
        return True
    return len(tokens) == 1


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
    food_items = [food for food in foods if isinstance(food, dict)]
    if not food_items:
        return None
    food = food_items[0]
    ambiguous = _needs_modifier_review(normalized)
    notes = []
    if ambiguous:
        notes.append("Customizable item is missing protein or modifier details.")
    requested_brand = _brand_from_text(normalized)
    source_brand = _matching_source_brand(food_items, requested_brand)
    if requested_brand and not source_brand:
        ambiguous = True
        notes.append("Nutritionix did not verify the requested brand; review before logging.")
    if len(food_items) > 1:
        notes.append("Nutritionix returned multiple foods; macros were summed from all returned items.")
    item_name = _nutritionix_item_name(food_items, source_brand)
    estimate = {
        "item_name": item_name or str(food.get("food_name") or "Meal"),
        "portion_description": _portion_from_nutritionix_items(food_items),
        "meal_type": "snack",
        "calories": _sum_nutritionix(food_items, "nf_calories"),
        "protein_g": _sum_nutritionix(food_items, "nf_protein"),
        "carbs_g": _sum_nutritionix(food_items, "nf_total_carbohydrate"),
        "fat_g": _sum_nutritionix(food_items, "nf_total_fat"),
        "sodium_mg": _sum_nutritionix(food_items, "nf_sodium"),
        "fiber_g": _sum_nutritionix(food_items, "nf_dietary_fiber"),
        "confidence": 0.55 if ambiguous else 0.85,
        "ambiguous": ambiguous,
        "uncertainty_notes": notes,
        "source": "nutritionix",
        "external_food_id": _nutritionix_external_id(food_items),
        "verified_source_url": "https://www.nutritionix.com/",
        "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
        "portion_basis": _portion_from_nutritionix_items(food_items),
        "brand_id": _brand_from_text(normalize_meal_text(source_brand or "")),
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


def _portion_from_nutritionix_items(foods: list[dict[str, Any]]) -> str | None:
    portions = [_portion_from_nutritionix(food) for food in foods]
    portions = [portion for portion in portions if portion]
    if len(portions) == 1:
        return portions[0]
    if portions:
        return "; ".join(portions)
    return f"{len(foods)} items" if len(foods) > 1 else None


def _sum_nutritionix(foods: list[dict[str, Any]], key: str) -> float:
    total = 0.0
    for food in foods:
        value = food.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total


def _nutritionix_external_id(foods: list[dict[str, Any]]) -> str | None:
    ids = [
        str(food.get("nix_item_id") or food.get("tag_id") or food.get("food_name") or "").strip()
        for food in foods
    ]
    ids = [external_id for external_id in ids if external_id]
    return ",".join(ids) or None


def _nutritionix_item_name(foods: list[dict[str, Any]], source_brand: str | None) -> str:
    names = [str(food.get("food_name") or "").strip() for food in foods]
    names = [name for name in names if name]
    item = ", ".join(names)
    return " ".join(part for part in (source_brand, item) if part).strip()


def _matching_source_brand(foods: list[dict[str, Any]], requested_brand: str | None) -> str | None:
    source_brands = [str(food.get("brand_name") or "").strip() for food in foods]
    source_brands = [brand for brand in source_brands if brand]
    if not source_brands:
        return None
    first_brand = source_brands[0]
    if not requested_brand:
        return first_brand
    for brand in source_brands:
        if _brand_from_text(normalize_meal_text(brand)) == requested_brand:
            return brand
    return None


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
