"""Cache-first branded and generic nutrition lookup for meal logging."""

from __future__ import annotations

from datetime import datetime, timedelta
from difflib import get_close_matches
from typing import Any

import data_store
import nutritionix_client
import open_food_facts_client
import usda_fdc_client
from meal_estimate_schema import sanitize_meal_estimate


CACHE_TTL_DAYS = 180
SOURCE_PRIORITY = ("cache", "nutritionix", "usda_fdc", "open_food_facts")
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
OFF_REJECT_QUALITY_TAG_FRAGMENTS = (
    "nutrition-data-error",
    "nutrition-energy-value-in-kcal-does-not-match",
    "nutrition-energy-value-in-kcal-may-not-match",
    "nutrition-packaging-as-sold-100g-energy-value-in-kcal-does-not-match",
    "nutrition-packaging-as-sold-100g-energy-value-in-kcal-may-not-match",
    "nutrition-value-under-0-01-g-salt",
    "nutrition-packaging-as-sold-100g-value-under-0-01-g-salt",
    "nutrition-value-very-low-for-category-salt",
)
OFF_LOCALE_COUNTRY_TAGS = {
    "australian": "en:australia",
    "canadian": "en:canada",
    "french": "en:france",
    "german": "en:germany",
    "irish": "en:ireland",
    "japanese": "en:japan",
    "uk": "en:united-kingdom",
    "u.k": "en:united-kingdom",
    "u.k.": "en:united-kingdom",
}

OFF_COMPLETE_QUALITY_TAGS = {
    "en:nutrition-completed",
    "en:nutrition-data-complete",
}
OFF_PACKAGED_QUERY_TOKENS = {
    "barcode",
    "imported",
    "non-us",
    "package",
    "packaged",
    "product",
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
    if "open_food_facts" in priorities:
        off = _open_food_facts_lookup(text)
        if off:
            data_store.save_branded_lookup_cache(normalized, off["source"], off)
            return off
    return None


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


def _open_food_facts_lookup(text: str) -> dict[str, Any] | None:
    expected_country_tag = _off_expected_country_tag(text)
    if not _off_lookup_allowed(text, expected_country_tag):
        return None
    payload = open_food_facts_client.search_products(
        text,
        country_tag=expected_country_tag,
        product_filter=lambda product: _off_candidate_usable(product, expected_country_tag=expected_country_tag),
    )
    products = payload.get("products") if isinstance(payload, dict) else None
    if not products:
        return None
    for product in products:
        if not _off_candidate_usable(product, expected_country_tag=expected_country_tag):
            continue
        try:
            return _open_food_facts_estimate(product)
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _off_candidate_usable(product: dict[str, Any], *, expected_country_tag: str | None = None) -> bool:
    if not _off_country_ok(product, expected_country_tag):
        return False
    if not _off_quality_ok(product):
        return False
    try:
        _open_food_facts_estimate(product)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _off_expected_country_tag(text: str) -> str | None:
    tokens = {
        raw.strip(".,!?;:()[]{}\"'").lower()
        for raw in (text or "").split()
        if raw.strip(".,!?;:()[]{}\"'")
    }
    if {"united", "kingdom"}.issubset(tokens):
        return "en:united-kingdom"
    for token in tokens:
        country_tag = OFF_LOCALE_COUNTRY_TAGS.get(token)
        if country_tag:
            return country_tag
    return None


def _off_lookup_allowed(text: str, expected_country_tag: str | None) -> bool:
    if expected_country_tag:
        return True
    tokens = {
        raw.strip(".,!?;:()[]{}\"'").lower()
        for raw in (text or "").split()
        if raw.strip(".,!?;:()[]{}\"'")
    }
    return bool(OFF_PACKAGED_QUERY_TOKENS.intersection(tokens) or {"non", "us"}.issubset(tokens))


def _off_country_ok(product: dict[str, Any], expected_country_tag: str | None) -> bool:
    if not expected_country_tag:
        return True
    countries = product.get("countries_tags") or []
    if not isinstance(countries, list):
        return False
    return expected_country_tag in {str(country).lower() for country in countries}


def _open_food_facts_estimate(product: dict[str, Any]) -> dict[str, Any]:
    nutriments = product.get("nutriments") or {}
    name = " ".join(
        str(part).strip()
        for part in (product.get("brands"), product.get("product_name"))
        if part
    ).strip()
    estimate = {
        "item_name": name or product.get("product_name") or "Packaged food",
        "portion_description": "100 g",
        "meal_type": "snack",
        "calories": _off_energy_kcal(nutriments),
        "protein_g": nutriments.get("proteins_100g"),
        "carbs_g": nutriments.get("carbohydrates_100g"),
        "fat_g": nutriments.get("fat_100g"),
        "sodium_mg": _off_sodium_mg(nutriments),
        "fiber_g": _off_optional_number(nutriments.get("fiber_100g")),
        "confidence": 0.72,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "open_food_facts",
        "external_food_id": product.get("code"),
        "verified_source_url": product.get("url") or "https://world.openfoodfacts.org/",
        "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
        "portion_basis": "100 g Open Food Facts packaged-food reference",
        # The visible attribution surface is tracked separately in FIT-80; this
        # backend slice keeps the CC-BY-SA provenance attached to every estimate.
        "off_attribution": "Source: Open Food Facts, licensed under CC-BY-SA.",
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


def _off_quality_ok(product: dict[str, Any]) -> bool:
    if not isinstance(product, dict):
        return False
    tags = product.get("data_quality_tags") or []
    lowered_tags = [str(tag).lower() for tag in tags]
    if not OFF_COMPLETE_QUALITY_TAGS.intersection(lowered_tags):
        return False
    if any("error" in tag for tag in lowered_tags):
        return False
    if any(fragment in tag for tag in lowered_tags for fragment in OFF_REJECT_QUALITY_TAG_FRAGMENTS):
        return False
    nutriments = product.get("nutriments") or {}
    required = ("energy-kcal_100g", "proteins_100g", "carbohydrates_100g", "fat_100g")
    return (
        bool(product.get("product_name"))
        and all(nutriments.get(key) is not None for key in required)
        and _off_macros_plausible(nutriments)
    )


def _off_macros_plausible(nutriments: dict[str, Any]) -> bool:
    try:
        calories = float(nutriments.get("energy-kcal_100g"))
        protein = float(nutriments.get("proteins_100g"))
        carbs = float(nutriments.get("carbohydrates_100g"))
        fat = float(nutriments.get("fat_100g"))
    except (TypeError, ValueError):
        return False
    return 0 <= calories <= 900 and all(0 <= value <= 100 for value in (protein, carbs, fat))


def _off_energy_kcal(nutriments: dict[str, Any]) -> Any:
    value = nutriments.get("energy-kcal_100g")
    if value is not None:
        return value
    return nutriments.get("energy-kcal")


def _off_optional_number(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _off_number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _off_sodium_mg(nutriments: dict[str, Any]) -> int:
    if nutriments.get("sodium_100g") is not None:
        sodium = _off_number_or_none(nutriments["sodium_100g"])
        if sodium is not None:
            return int(round(sodium * 1000))
    if nutriments.get("salt_100g") is not None:
        salt = _off_number_or_none(nutriments["salt_100g"])
        if salt is not None:
            return int(round(salt * 393.4))
    return 0


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
