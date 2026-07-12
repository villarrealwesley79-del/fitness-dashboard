"""Cache-first branded and generic nutrition lookup for meal logging."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import get_close_matches
from typing import Any

import data_store
import heb_product_lookup
import nutritionix_client
import open_food_facts_client
import usda_fdc_client
from meal_estimate_schema import sanitize_meal_estimate


CACHE_TTL_DAYS = 180
FALLBACK_CACHE_TTL_DAYS = 1
LONG_LIVED_CACHE_SOURCE_TIERS = {"heb_product_page", "nutritionix", "nutritionix_barcode"}
SOURCE_PRIORITY = ("cache", "heb_product_page", "nutritionix", "usda_fdc", "open_food_facts")
# H-E-B is text-path only via `heb_product_page`; do not add it to the
# barcode ladder without a real provider. FIT-218 AC4 excludes scraping or
# hardcoded barcode rows as substitute provider coverage.
BARCODE_SOURCE_PRIORITY = ("cache", "nutritionix_barcode", "usda_fdc_barcode", "open_food_facts_barcode")
BARCODE_LENGTHS = {8, 12, 13, 14}
OPEN_FOOD_FACTS_HOME_URL = "https://world.openfoodfacts.org/"
OPEN_FOOD_FACTS_SOURCES = {"open_food_facts", "open_food_facts_barcode"}
KJ_PER_KCAL = 4.184
MULTI_ITEM_HARD_TOKENS = {"with", "plus", "&", "+", "combo", "meal", "plate"}
MULTI_ITEM_SOFT_TOKENS = {"and"}
MULTI_ITEM_TOKENS = MULTI_ITEM_HARD_TOKENS | MULTI_ITEM_SOFT_TOKENS
PORTION_MODIFIER_TOKENS = {"half"}
EXACT_ONLY_BRANDS = {"h-e-b", "heb"}
KNOWN_BRANDS = {
    "chipotle",
    "h-e-b",
    "heb",
    "starbucks",
    "mcdonalds",
    "subway",
    "chick-fil-a",
    "chickfila",
}
REGIONAL_RESTAURANT_BRAND_PHRASES = (
    ("bill", "miller"),
    ("golden", "chick"),
    ("la", "madeleine"),
    ("p", "terrys"),
    ("rudys",),
    ("schlotzskys",),
    ("taco", "cabana"),
    ("torchys",),
    ("whataburger",),
)
REGIONAL_RESTAURANT_SOFT_AND_PHRASES = (
    ("bacon", "and", "egg", "taco"),
    ("bean", "and", "cheese", "taco"),
)
BRAND_ALIASES = {
    "chickfila": "chick-fil-a",
    "heb": "h-e-b",
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
    "quesadillas": "quesadilla",
}
CUSTOMIZABLE_CHAIN_TOKENS = {"chipotle"}
CUSTOMIZABLE_ITEM_TOKENS = {"burrito", "bowl", "taco", "tacos", "salad", "quesadilla"}
NUTRITIONIX_REQUIRED_NUTRIENTS = (
    "nf_calories",
    "nf_protein",
    "nf_total_carbohydrate",
    "nf_total_fat",
    "nf_sodium",
    "nf_dietary_fiber",
)
BREAKFAST_TOKENS = {"oat", "oatmeal", "egg", "eggs", "toast", "yogurt", "coffee", "cereal"}
LUNCH_DINNER_TOKENS = {
    "burrito",
    "bowl",
    "taco",
    "tacos",
    "salad",
    "quesadilla",
    "sandwich",
    "wrap",
    "burger",
}
# Gate-only menu nouns for regional restaurant provider lookup eligibility.
REGIONAL_RESTAURANT_ITEM_TOKENS = BREAKFAST_TOKENS | LUNCH_DINNER_TOKENS | {
    "biscuit",
    "cheeseburger",
    "chips",
    "fries",
    "melt",
    "nuggets",
    "tenders",
    "wings",
}
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
    "nutrition-value-under-0-01-g-salt",
    "nutrition-packaging-as-sold-100g-value-under-0-01-g-salt",
    "nutrition-value-very-low-for-category-salt",
)
OFF_ENERGY_MISMATCH_TAG_FRAGMENTS = (
    "nutrition-energy-value-in-kcal-does-not-match",
    "nutrition-energy-value-in-kcal-may-not-match",
    "nutrition-packaging-as-sold-100g-energy-value-in-kcal-does-not-match",
    "nutrition-packaging-as-sold-100g-energy-value-in-kcal-may-not-match",
)
OFF_ENERGY_ATWATER_TOLERANCE_PCT = 0.12
OFF_ENERGY_ATWATER_TOLERANCE_ABS_KCAL = 25.0
OFF_LOCALE_COUNTRY_TAGS = {
    "australia": "en:australia",
    "australian": "en:australia",
    "canada": "en:canada",
    "canadian": "en:canada",
    "france": "en:france",
    "french": "en:france",
    "germany": "en:germany",
    "german": "en:germany",
    "ireland": "en:ireland",
    "irish": "en:ireland",
    "japan": "en:japan",
    "japanese": "en:japan",
    "uk": "en:united-kingdom",
    "u.k": "en:united-kingdom",
    "u.k.": "en:united-kingdom",
}
OFF_LOCALE_ADJECTIVE_TOKENS = {
    "australian",
    "canadian",
    "french",
    "german",
    "irish",
    "japanese",
}

OFF_COMPLETE_QUALITY_TAGS = {
    "en:nutriments-completed",
    "en:nutrition-completed",
    "en:nutrition-data-complete",
}
OFF_PRIVATE_LABEL_BRANDS = {"h-e-b"}
OFF_PRIVATE_LABEL_BRAND_TOKENS = {"h-e-b", "heb"}
OFF_PRIVATE_LABEL_VARIANT_TOKENS = {"brown", "cauliflower", "crunchy", "poke", "spicy"}
OFF_PACKAGED_QUERY_TOKENS = {
    "barcode",
    "imported",
    "non-us",
    "non-u.s",
    "package",
    "packaged",
    "product",
}
OFF_CONTEXT_ONLY_QUERY_TOKENS = (
    OFF_PACKAGED_QUERY_TOKENS
    | set(OFF_LOCALE_COUNTRY_TAGS)
    | OFF_LOCALE_ADJECTIVE_TOKENS
    | {"kingdom", "non", "u.s", "united", "us"}
)
OFF_KNOWN_PACKAGED_PRODUCT_PHRASES = {
    "goldbaren",
    "haribo goldbaren",
    "petit ecolier",
    "pocky",
    "smarties",
    "tim tams",
}


def normalize_meal_text(text: str) -> str:
    """Normalize user text for cache keys and typo-tolerant source lookup."""
    tokens = []
    for raw in re.sub(r"['\u2018\u2019]", "", (text or "").lower()).split():
        token = raw.strip(".,!?;:()[]{}\"")
        if token == "&":
            token = "and"
        token = PLURALS.get(token, token)
        for brand, typos in BRAND_TYPOS.items():
            if token in typos:
                token = brand
                break
        else:
            match = get_close_matches(token, KNOWN_BRANDS - EXACT_ONLY_BRANDS, n=1, cutoff=0.8)
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
    user_id: int = 1,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    """Return a sanitized estimate, bypassing cache when force_refresh is true."""
    lookup_text = _text_with_brand_hint(text, brand_hint)
    normalized = normalize_meal_text(lookup_text)
    if not normalized:
        return None
    priorities = tuple(source_priority or SOURCE_PRIORITY)
    private_label_brand = _off_private_label_brand_from_text(normalized)

    for source in priorities:
        if source == "cache":
            if force_refresh:
                continue
            try:
                cached = _cache_lookup(normalized, user_id=user_id)
            except Exception:
                cached = None
            if cached and _cache_allowed_for_lookup(cached, private_label_brand):
                return cached
        elif source == "nutritionix":
            try:
                nutritionix = _nutritionix_lookup(lookup_text, normalized)
            except Exception:
                nutritionix = None
            if nutritionix:
                _save_cache_best_effort(normalized, nutritionix["source"], nutritionix, user_id=user_id)
                return nutritionix
        elif source == "heb_product_page":
            try:
                heb = heb_product_lookup.lookup(lookup_text)
            except Exception:
                heb = None
            if heb:
                _save_cache_best_effort(normalized, heb["source"], heb, user_id=user_id)
                return heb
        elif source == "usda_fdc":
            try:
                usda = _usda_lookup(lookup_text, normalized)
            except Exception:
                usda = None
            if usda:
                _save_cache_best_effort(normalized, usda["source"], usda, user_id=user_id)
                return usda
        elif source == "open_food_facts":
            try:
                off = _open_food_facts_lookup(lookup_text)
            except Exception:
                off = None
            if off:
                _save_cache_best_effort(normalized, off["source"], off, user_id=user_id)
                return off
    return None


def normalize_barcode(value: str) -> str | None:
    """Return a digit-only barcode when it matches supported UPC/EAN/GTIN lengths."""
    cleaned = re.sub(r"[\s_.-]+", "", str(value or ""))
    if cleaned.isdigit() and len(cleaned) in BARCODE_LENGTHS:
        return cleaned
    return None


def lookup_barcode(
    barcode: str,
    *,
    user_id: int = 1,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    """Return a sanitized estimate from barcode cache or structured barcode providers.

    H-E-B private-label handling is intentionally text-only through `lookup()`;
    `lookup_barcode()` must not route to `heb_product_page`.
    """
    normalized = normalize_barcode(barcode)
    if not normalized:
        return None
    for source in BARCODE_SOURCE_PRIORITY:
        if source == "cache":
            if force_refresh:
                continue
            try:
                cached = _barcode_cache_lookup(normalized, user_id=user_id)
            except Exception:
                cached = None
            if cached:
                return cached
            continue
        provider_lookup = {
            "nutritionix_barcode": _nutritionix_barcode_lookup,
            "usda_fdc_barcode": _usda_barcode_lookup,
            "open_food_facts_barcode": _open_food_facts_barcode_lookup,
        }.get(source)
        if provider_lookup is None:
            continue
        try:
            estimate = provider_lookup(normalized)
        except Exception:
            estimate = None
        if estimate:
            _save_barcode_cache_best_effort(normalized, estimate["source"], estimate, user_id=user_id)
            return estimate
    return None


def _text_with_brand_hint(text: str, brand_hint: str | None) -> str:
    cleaned = (text or "").strip()
    normalized_text = normalize_meal_text(cleaned)
    hint = normalize_meal_text(brand_hint or "")
    if not hint:
        return cleaned
    if _brand_from_text(normalized_text):
        return cleaned
    return f"{hint} {cleaned}".strip()


def should_attempt_direct_lookup(text: str, *, brand_hint: str | None = None) -> bool:
    """Return True when text is safe to satisfy from a single external hit."""
    normalized = normalize_meal_text(f"{brand_hint or ''} {text}".strip())
    tokens = normalized.split()
    if not tokens:
        return False
    if any(token in tokens for token in PORTION_MODIFIER_TOKENS):
        return False
    regional_brand = _regional_restaurant_brand_tokens(tokens)
    if regional_brand:
        return _regional_restaurant_direct_lookup_allowed(tokens, regional_brand)
    if any(token in tokens for token in MULTI_ITEM_TOKENS):
        return False
    expected_country_tag = _off_expected_country_tag(normalized)
    if _off_lookup_allowed(normalized, expected_country_tag):
        return True
    if not _off_product_tokens(normalized):
        return False
    if brand_hint or _brand_from_text(normalized):
        return bool([token for token in tokens if token not in KNOWN_BRANDS])
    return len(tokens) == 1


def _regional_restaurant_brand_tokens(tokens: list[str]) -> tuple[str, ...] | None:
    for phrase in REGIONAL_RESTAURANT_BRAND_PHRASES:
        phrase_len = len(phrase)
        for idx in range(0, len(tokens) - phrase_len + 1):
            if tuple(tokens[idx:idx + phrase_len]) == phrase:
                return phrase
    return None


def _regional_restaurant_item_tokens(tokens: list[str], brand_tokens: tuple[str, ...]) -> list[str]:
    remaining = list(tokens)
    phrase_len = len(brand_tokens)
    for idx in range(0, len(tokens) - phrase_len + 1):
        if tuple(tokens[idx:idx + phrase_len]) == brand_tokens:
            del remaining[idx:idx + phrase_len]
            break
    return remaining


def _regional_restaurant_direct_lookup_allowed(tokens: list[str], brand_tokens: tuple[str, ...]) -> bool:
    remaining = _regional_restaurant_item_tokens(tokens, brand_tokens)
    if not remaining:
        return False
    if any(token in MULTI_ITEM_HARD_TOKENS for token in remaining):
        return False
    if "and" in _regional_restaurant_soft_and_stripped_tokens(remaining):
        return False
    return any(token in REGIONAL_RESTAURANT_ITEM_TOKENS for token in remaining)


def _regional_restaurant_soft_and_stripped_tokens(tokens: list[str]) -> list[str]:
    stripped = list(tokens)
    for phrase in REGIONAL_RESTAURANT_SOFT_AND_PHRASES:
        phrase_len = len(phrase)
        idx = 0
        while idx <= len(stripped) - phrase_len:
            if tuple(stripped[idx:idx + phrase_len]) == phrase:
                del stripped[idx:idx + phrase_len]
            else:
                idx += 1
    return stripped


def is_private_label_query(text: str) -> bool:
    """Return True for private-label packaged-food queries such as H-E-B sushi."""
    normalized = normalize_meal_text(text)
    return _off_private_label_brand_from_text(normalized) is not None and bool(
        _off_private_label_product_tokens(normalized)
    )


def _cache_lookup(normalized: str, *, user_id: int = 1) -> dict[str, Any] | None:
    row = data_store.get_branded_lookup_cache(normalized, user_id=user_id)
    if not row:
        return None
    fetched_at = _parse_iso(row.get("fetched_at"))
    if not _cache_entry_fresh(fetched_at, row):
        return None
    payload = row.get("response_json")
    if not isinstance(payload, dict):
        return None
    estimate = dict(payload)
    estimate["source"] = "local_cache"
    estimate.setdefault("underlying_source", row.get("source"))
    return _sanitize_with_provenance(estimate)


def _cache_allowed_for_lookup(estimate: dict[str, Any], private_label_brand: str | None) -> bool:
    if not private_label_brand:
        return True
    return estimate.get("brand_id") == private_label_brand


def _save_cache_best_effort(normalized: str, source: str, estimate: dict[str, Any], *, user_id: int) -> None:
    try:
        data_store.save_branded_lookup_cache(normalized, source, estimate, user_id=user_id, source_tier=source)
    except Exception:
        return


def _cache_entry_fresh(fetched_at: datetime | None, row: dict[str, Any]) -> bool:
    if not fetched_at:
        return False
    source_tier = str(row.get("source_tier") or row.get("source") or "").strip()
    ttl_days = CACHE_TTL_DAYS if source_tier in LONG_LIVED_CACHE_SOURCE_TIERS else FALLBACK_CACHE_TTL_DAYS
    return datetime.now() - fetched_at <= timedelta(days=ttl_days)


def _barcode_cache_lookup(barcode: str, *, user_id: int = 1) -> dict[str, Any] | None:
    row = data_store.get_barcode_lookup_cache(barcode, user_id=user_id)
    if not row:
        return None
    fetched_at = _parse_iso(row.get("fetched_at"))
    if not _cache_entry_fresh(fetched_at, row):
        return None
    payload = row.get("response_json")
    if not isinstance(payload, dict):
        return None
    estimate = dict(payload)
    estimate["source"] = "local_cache"
    estimate.setdefault("underlying_source", row.get("source"))
    _fill_off_barcode_cache_verified_url(estimate)
    return _sanitize_with_provenance(estimate)


def _fill_off_barcode_cache_verified_url(estimate: dict[str, Any]) -> None:
    if estimate.get("verified_source_url"):
        return
    if not _is_off_cache_replay(estimate):
        return
    external_id = str(estimate.get("external_food_id") or "").strip()
    if external_id:
        estimate["verified_source_url"] = f"{OPEN_FOOD_FACTS_HOME_URL}product/{external_id}"
    else:
        estimate["verified_source_url"] = OPEN_FOOD_FACTS_HOME_URL


def _is_off_cache_replay(estimate: dict[str, Any]) -> bool:
    sources = {
        str(estimate.get("source") or "").strip(),
        str(estimate.get("underlying_source") or "").strip(),
    }
    return bool(OPEN_FOOD_FACTS_SOURCES.intersection(sources) or estimate.get("off_attribution"))


def _save_barcode_cache_best_effort(barcode: str, source: str, estimate: dict[str, Any], *, user_id: int) -> None:
    try:
        data_store.save_barcode_lookup_cache(barcode, source, estimate, user_id=user_id, source_tier=source)
    except Exception:
        return


def _nutritionix_lookup(text: str, normalized: str) -> dict[str, Any] | None:
    payload = nutritionix_client.natural_nutrients(text)
    foods = payload.get("foods") if isinstance(payload, dict) else None
    if not foods:
        return None
    food_items = [food for food in foods if isinstance(food, dict)]
    if not food_items:
        return None
    if not _nutritionix_items_have_required_nutrients(food_items):
        return None
    food = food_items[0]
    ambiguous = _needs_modifier_review(normalized)
    notes = []
    if ambiguous:
        notes.append("Customizable item is missing protein or modifier details.")
    requested_brand = _brand_from_text(normalized)
    source_brand = _matching_source_brand(food_items, requested_brand)
    if requested_brand and not source_brand:
        if _off_private_label_brand_requested(requested_brand):
            return None
        ambiguous = True
        notes.append("Nutritionix did not verify the requested brand; review before logging.")
    if _requested_item_mismatch(normalized, food_items):
        ambiguous = True
        notes.append("Nutritionix returned a different item category; review before logging.")
    if len(food_items) > 1:
        notes.append("Nutritionix returned multiple foods; macros were summed from all returned items.")
    item_name = _nutritionix_item_name(food_items, source_brand)
    estimate = {
        "item_name": item_name or str(food.get("food_name") or "Meal"),
        "portion_description": _portion_from_nutritionix_items(food_items),
        "meal_type": _infer_meal_type(normalized),
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
        "source_brand_name": source_brand,
    }
    return _sanitize_with_provenance(estimate)


def _nutritionix_barcode_lookup(barcode: str) -> dict[str, Any] | None:
    payload = nutritionix_client.search_item_by_upc(barcode)
    foods = payload.get("foods") if isinstance(payload, dict) else None
    if not foods:
        return None
    food_items = [food for food in foods if isinstance(food, dict)]
    if not food_items or not _nutritionix_items_have_required_nutrients(food_items):
        return None
    food = food_items[0]
    source_brand = str(food.get("brand_name") or "").strip() or None
    item_name = _nutritionix_item_name([food], source_brand) or "Packaged food"
    portion = _portion_from_nutritionix(food)
    external_id = str(food.get("nix_item_id") or food.get("upc") or barcode).strip()
    estimate = {
        "item_name": item_name,
        "portion_description": portion,
        "meal_type": "snack",
        "calories": food.get("nf_calories"),
        "protein_g": food.get("nf_protein"),
        "carbs_g": food.get("nf_total_carbohydrate"),
        "fat_g": food.get("nf_total_fat"),
        "sodium_mg": food.get("nf_sodium"),
        "fiber_g": food.get("nf_dietary_fiber"),
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "nutritionix_barcode",
        "external_food_id": external_id or barcode,
        "verified_source_url": "https://www.nutritionix.com/",
        "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
        "portion_basis": "Nutritionix UPC label serving",
        "brand_id": _brand_from_text(normalize_meal_text(source_brand or "")),
        "source_brand_name": source_brand,
    }
    return _sanitize_with_provenance(estimate)


def _usda_barcode_lookup(barcode: str) -> dict[str, Any] | None:
    payload = usda_fdc_client.search_foods_by_barcode(barcode)
    foods = payload.get("foods") if isinstance(payload, dict) else None
    if not foods:
        return None
    for food in _matching_usda_barcode_foods([food for food in foods if isinstance(food, dict)], barcode):
        food_nutrients = [n for n in food.get("foodNutrients", []) if isinstance(n, dict)]
        nutrients = {n.get("nutrientName"): n.get("value") for n in food_nutrients}
        calories = _usda_energy_kcal(food_nutrients)
        if calories is None or any(
            nutrients.get(name) is None
            for name in ("Protein", "Carbohydrate, by difference", "Total lipid (fat)")
        ):
            continue
        fdc_id = food.get("fdcId")
        source_brand = _matching_usda_source_brand(food, None)
        item_name = " ".join(
            part
            for part in (source_brand, str(food.get("description") or "").strip())
            if part
        ).strip() or "Packaged food"
        estimate = {
            "item_name": item_name,
            "portion_description": "100 g",
            "meal_type": "snack",
            "calories": calories,
            "protein_g": nutrients.get("Protein"),
            "carbs_g": nutrients.get("Carbohydrate, by difference"),
            "fat_g": nutrients.get("Total lipid (fat)"),
            "sodium_mg": nutrients.get("Sodium, Na") if nutrients.get("Sodium, Na") is not None else 0,
            "fiber_g": nutrients.get("Fiber, total dietary") if nutrients.get("Fiber, total dietary") is not None else 0,
            "confidence": 0.72,
            "ambiguous": True,
            "uncertainty_notes": [
                "USDA FDC barcode data uses a 100 g reference portion; confirm serving size before logging."
            ],
            "source": "usda_fdc_barcode",
            "external_food_id": str(fdc_id) if fdc_id is not None else barcode,
            "verified_source_url": (
                f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{fdc_id}/nutrients"
                if fdc_id
                else "https://fdc.nal.usda.gov/"
            ),
            "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
            "portion_basis": "100 g USDA FoodData Central barcode reference portion",
            "brand_id": _brand_from_text(normalize_meal_text(source_brand or "")),
            "source_brand_name": source_brand,
        }
        return _sanitize_with_provenance(estimate)
    return None


def _usda_lookup(text: str, normalized: str) -> dict[str, Any] | None:
    payload = usda_fdc_client.search_foods(text)
    foods = payload.get("foods") if isinstance(payload, dict) else None
    if not foods:
        return None
    food = foods[0]
    food_nutrients = [n for n in food.get("foodNutrients", []) if isinstance(n, dict)]
    nutrients = {n.get("nutrientName"): n.get("value") for n in food_nutrients}
    fdc_id = food.get("fdcId")
    requested_brand = _brand_from_text(normalized)
    source_brand = _matching_usda_source_brand(food, requested_brand)
    ambiguous = True
    notes = ["USDA FDC uses a 100 g reference portion; confirm serving size before logging."]
    if _needs_modifier_review(normalized):
        notes.append("Customizable item is missing protein or modifier details.")
    if requested_brand and not source_brand:
        if _off_private_label_brand_requested(requested_brand):
            return None
        ambiguous = True
        notes.append("USDA FDC did not verify the requested brand; review before logging.")
    if _requested_item_mismatch(normalized, [food]):
        ambiguous = True
        notes.append("USDA FDC returned a different item category; review before logging.")
    calories = _usda_energy_kcal(food_nutrients)
    estimate = {
        "item_name": food.get("description") or "Food",
        "portion_description": "100 g",
        "meal_type": _infer_meal_type(normalized),
        "calories": calories,
        "protein_g": nutrients.get("Protein"),
        "carbs_g": nutrients.get("Carbohydrate, by difference"),
        "fat_g": nutrients.get("Total lipid (fat)"),
        "sodium_mg": nutrients.get("Sodium, Na") if nutrients.get("Sodium, Na") is not None else 0,
        "fiber_g": nutrients.get("Fiber, total dietary") if nutrients.get("Fiber, total dietary") is not None else 0,
        "confidence": 0.55,
        "ambiguous": ambiguous,
        "uncertainty_notes": notes,
        "source": "usda_fdc",
        "external_food_id": str(fdc_id) if fdc_id is not None else None,
        "verified_source_url": f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{fdc_id}/nutrients" if fdc_id else "https://fdc.nal.usda.gov/",
        "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
        "portion_basis": "100 g USDA FoodData Central reference portion",
        "brand_id": _brand_from_text(normalize_meal_text(source_brand or "")),
        "source_brand_name": source_brand,
    }
    return _sanitize_with_provenance(estimate)


def _matching_usda_barcode_foods(foods: list[dict[str, Any]], barcode: str) -> list[dict[str, Any]]:
    requested = _barcode_match_variants(barcode)
    matches = []
    for food in foods:
        values = _barcode_match_variants(food.get("gtinUpc") or food.get("upc") or food.get("gtin"))
        if requested.intersection(values):
            matches.append(food)
    return matches


def _barcode_match_variants(value: Any) -> set[str]:
    digits = re.sub(r"\D+", "", str(value or ""))
    if not digits:
        return set()
    variants = {digits}
    stripped = digits.lstrip("0")
    if stripped:
        variants.add(stripped)
    return variants


def _usda_energy_kcal(food_nutrients: list[dict[str, Any]]) -> Any:
    energy_rows = [
        nutrient
        for nutrient in food_nutrients
        if nutrient.get("nutrientName") in {"Energy", "Energy (Atwater General Factors)"}
    ]
    for nutrient in energy_rows:
        unit = str(nutrient.get("unitName") or "").strip().upper()
        if unit == "KCAL":
            return nutrient.get("value")
    for nutrient in energy_rows:
        unit = str(nutrient.get("unitName") or "").strip().upper()
        if unit == "KJ":
            value = _float_or_none(nutrient.get("value"))
            return value / KJ_PER_KCAL if value is not None else None
    for nutrient in energy_rows:
        if not str(nutrient.get("unitName") or "").strip():
            return nutrient.get("value")
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _open_food_facts_lookup(text: str) -> dict[str, Any] | None:
    expected_country_tag = _off_expected_country_tag(text)
    if not _off_lookup_allowed(text, expected_country_tag):
        return None
    reject_us_only = _off_non_us_requested(text) and expected_country_tag is None
    expected_private_label_brand = _off_private_label_brand_from_text(text)
    payload = open_food_facts_client.search_products(
        text,
        country_tag=expected_country_tag,
        product_filter=lambda product: _off_candidate_usable(
            product,
            expected_country_tag=expected_country_tag,
            reject_us_only=reject_us_only,
            expected_private_label_brand=expected_private_label_brand,
            query_text=text,
        ),
    )
    products = payload.get("products") if isinstance(payload, dict) else None
    if not products:
        return None
    for product in products:
        if not _off_candidate_usable(
            product,
            expected_country_tag=expected_country_tag,
            reject_us_only=reject_us_only,
            expected_private_label_brand=expected_private_label_brand,
            query_text=text,
        ):
            continue
        try:
            estimate = _open_food_facts_estimate(product)
            if expected_private_label_brand:
                estimate["brand_id"] = expected_private_label_brand
            return estimate
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _open_food_facts_barcode_lookup(barcode: str) -> dict[str, Any] | None:
    product = open_food_facts_client.get_product_by_barcode(barcode)
    if not isinstance(product, dict) or not _off_barcode_quality_ok(product):
        return None
    return _open_food_facts_barcode_estimate(product)


def _off_candidate_usable(
    product: dict[str, Any],
    *,
    expected_country_tag: str | None = None,
    reject_us_only: bool = False,
    expected_private_label_brand: str | None = None,
    query_text: str | None = None,
) -> bool:
    if expected_private_label_brand and not _off_private_label_brand_matches(product, expected_private_label_brand):
        return False
    if expected_private_label_brand and not _off_private_label_product_matches(product, query_text or ""):
        return False
    if not _off_country_ok(product, expected_country_tag, reject_us_only=reject_us_only):
        return False
    if not _off_quality_ok(product):
        return False
    try:
        _open_food_facts_estimate(product)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _off_expected_country_tag(text: str) -> str | None:
    tokens = _off_query_tokens(text)
    if {"united", "kingdom"}.issubset(tokens):
        return "en:united-kingdom"
    for token in tokens:
        country_tag = OFF_LOCALE_COUNTRY_TAGS.get(token)
        if country_tag:
            return country_tag
    return None


def _off_lookup_allowed(text: str, expected_country_tag: str | None) -> bool:
    tokens = _off_query_tokens(text)
    product_tokens = _off_product_tokens(text)
    if _off_private_label_brand_from_text(text):
        return bool(_off_private_label_product_tokens(text))
    has_packaged_context = bool(
        OFF_PACKAGED_QUERY_TOKENS.intersection(tokens) or _off_non_us_requested(text)
    )
    if has_packaged_context:
        return bool(product_tokens)
    if not expected_country_tag:
        return False
    if not product_tokens:
        return False
    if {"united", "kingdom"}.issubset(tokens):
        return True
    explicit_country_tokens = tokens - OFF_LOCALE_ADJECTIVE_TOKENS
    if {"uk", "u.k", "u.k."}.intersection(tokens):
        return True
    if any(token in OFF_LOCALE_COUNTRY_TAGS for token in explicit_country_tokens):
        return True
    product_phrase = " ".join(product_tokens)
    return product_phrase in OFF_KNOWN_PACKAGED_PRODUCT_PHRASES


def _off_query_tokens(text: str) -> set[str]:
    return set(_off_query_token_list(text))


def _off_query_token_list(text: str) -> list[str]:
    return [
        raw.strip(".,!?;:()[]{}\"'").lower()
        for raw in (text or "").split()
        if raw.strip(".,!?;:()[]{}\"'")
    ]


def _off_product_tokens(text: str) -> list[str]:
    return [
        token
        for token in _off_query_token_list(text)
        if token not in OFF_CONTEXT_ONLY_QUERY_TOKENS
    ]


def _off_private_label_product_tokens(text: str) -> list[str]:
    return [
        token
        for token in _off_product_tokens(text)
        if token not in OFF_PRIVATE_LABEL_BRAND_TOKENS
    ]


def _off_private_label_brand_from_text(text: str) -> str | None:
    brand = _brand_from_text(normalize_meal_text(text))
    return brand if _off_private_label_brand_requested(brand) else None


def _off_private_label_brand_requested(brand: str | None) -> bool:
    return brand in OFF_PRIVATE_LABEL_BRANDS


def _off_private_label_brand_matches(product: dict[str, Any], brand: str) -> bool:
    brands = str(product.get("brands") or "").lower()
    if brand == "h-e-b":
        normalized = brands.replace("h-e-b", "heb").replace("h e b", "heb").replace("h‑e‑b", "heb")
        tokens = set(re.findall(r"[a-z0-9]+", normalized))
        return bool({"heb", "sushiya"}.intersection(tokens))
    return brand in brands


def _off_private_label_product_matches(product: dict[str, Any], query_text: str) -> bool:
    requested_tokens = set(_off_private_label_product_tokens(normalize_meal_text(query_text)))
    if not requested_tokens:
        return False
    product_text = " ".join(str(product.get(key) or "") for key in ("brands", "product_name"))
    product_tokens = set(re.findall(r"[a-z0-9]+", normalize_meal_text(product_text)))
    if not requested_tokens.issubset(product_tokens):
        return False
    requested_variants = requested_tokens.intersection(OFF_PRIVATE_LABEL_VARIANT_TOKENS)
    product_variants = product_tokens.intersection(OFF_PRIVATE_LABEL_VARIANT_TOKENS)
    return product_variants.issubset(requested_variants)


def _off_non_us_requested(text: str) -> bool:
    tokens = _off_query_tokens(text)
    return bool(
        {"non-us", "non-u.s"}.intersection(tokens)
        or {"non", "us"}.issubset(tokens)
        or {"non", "u.s"}.issubset(tokens)
    )


def _off_country_ok(
    product: dict[str, Any],
    expected_country_tag: str | None,
    *,
    reject_us_only: bool = False,
) -> bool:
    countries = product.get("countries_tags") or []
    if not isinstance(countries, list):
        return False if expected_country_tag else True
    normalized_countries = {str(country).lower() for country in countries}
    if reject_us_only:
        return any(country and country != "en:united-states" for country in normalized_countries)
    if not expected_country_tag:
        return True
    return expected_country_tag in normalized_countries


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
        "source_brand_name": product.get("brands"),
        # The visible attribution surface is tracked separately in FIT-80; this
        # backend slice keeps the database and product-image licenses attached.
        "off_attribution": "Source: Open Food Facts (ODbL/DbCL data; product images CC BY-SA)",
    }
    return _sanitize_with_provenance(estimate)


def _open_food_facts_barcode_estimate(product: dict[str, Any]) -> dict[str, Any]:
    nutriments = product.get("nutriments") or {}
    name = " ".join(
        str(part).strip()
        for part in (product.get("brands"), product.get("product_name"))
        if part
    ).strip()
    serving = _off_serving_description(product)
    serving_quantity = _off_serving_quantity_g(product)
    serving_macros = _off_serving_macros(nutriments, serving_quantity_g=serving_quantity)
    if serving_macros is None:
        serving_macros = {
            "calories": _off_energy_kcal(nutriments),
            "protein_g": nutriments.get("proteins_100g"),
            "carbs_g": nutriments.get("carbohydrates_100g"),
            "fat_g": nutriments.get("fat_100g"),
            "sodium_mg": _off_sodium_mg(nutriments),
            "fiber_g": _off_optional_number(nutriments.get("fiber_100g")),
        }
        portion_description = "100 g"
        portion_basis = "100 g Open Food Facts packaged-food reference"
        confidence = 0.72
    else:
        portion_description = serving or "1 serving"
        portion_basis = "Open Food Facts package serving"
        confidence = 0.82
    estimate = {
        "item_name": name or product.get("product_name") or "Packaged food",
        "portion_description": portion_description,
        "meal_type": "snack",
        "calories": serving_macros["calories"],
        "protein_g": serving_macros["protein_g"],
        "carbs_g": serving_macros["carbs_g"],
        "fat_g": serving_macros["fat_g"],
        "sodium_mg": serving_macros["sodium_mg"],
        "fiber_g": serving_macros["fiber_g"],
        "confidence": confidence,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "open_food_facts_barcode",
        "external_food_id": product.get("code"),
        "verified_source_url": product.get("url") or "https://world.openfoodfacts.org/",
        "data_fetched_at": datetime.now().isoformat(timespec="seconds"),
        "portion_basis": portion_basis,
        "source_brand_name": product.get("brands"),
        "off_attribution": "Source: Open Food Facts (ODbL/DbCL data; product images CC BY-SA)",
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
        "source_brand_name",
        "underlying_source",
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
    macros_present_and_plausible = (
        bool(product.get("product_name"))
        and all(nutriments.get(key) is not None for key in required)
        and _off_macros_plausible(nutriments)
    )
    if not macros_present_and_plausible:
        return False
    if any(fragment in tag for tag in lowered_tags for fragment in OFF_ENERGY_MISMATCH_TAG_FRAGMENTS):
        return _off_energy_consistent_with_macros(nutriments)
    return True


def _off_barcode_quality_ok(product: dict[str, Any]) -> bool:
    if _off_quality_ok(product):
        return True
    tags = product.get("data_quality_tags") or []
    lowered_tags = [str(tag).lower() for tag in tags]
    if any("error" in tag for tag in lowered_tags):
        return False
    if any(fragment in tag for tag in lowered_tags for fragment in OFF_REJECT_QUALITY_TAG_FRAGMENTS):
        return False
    nutriments = product.get("nutriments") or {}
    required = ("energy-kcal_100g", "proteins_100g", "carbohydrates_100g", "fat_100g")
    if not bool(product.get("product_name")) or any(nutriments.get(key) is None for key in required):
        return False
    if not _off_macros_plausible(nutriments):
        return False
    if any(fragment in tag for tag in lowered_tags for fragment in OFF_ENERGY_MISMATCH_TAG_FRAGMENTS):
        return _off_energy_consistent_with_macros(nutriments)
    return True


def _off_macros_plausible(nutriments: dict[str, Any]) -> bool:
    try:
        calories = float(nutriments.get("energy-kcal_100g"))
        protein = float(nutriments.get("proteins_100g"))
        carbs = float(nutriments.get("carbohydrates_100g"))
        fat = float(nutriments.get("fat_100g"))
    except (TypeError, ValueError):
        return False
    return 0 <= calories <= 900 and all(0 <= value <= 100 for value in (protein, carbs, fat))


def _off_energy_consistent_with_macros(nutriments: dict[str, Any]) -> bool:
    try:
        stated_kcal = float(nutriments.get("energy-kcal_100g"))
        protein = float(nutriments.get("proteins_100g"))
        carbs = float(nutriments.get("carbohydrates_100g"))
        fat = float(nutriments.get("fat_100g"))
    except (TypeError, ValueError):
        return False
    atwater_kcal = 4 * protein + 4 * carbs + 9 * fat
    if atwater_kcal <= 0:
        return False
    tolerance = max(
        OFF_ENERGY_ATWATER_TOLERANCE_ABS_KCAL,
        OFF_ENERGY_ATWATER_TOLERANCE_PCT * atwater_kcal,
    )
    return abs(stated_kcal - atwater_kcal) <= tolerance


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


def _off_serving_description(product: dict[str, Any]) -> str | None:
    value = product.get("serving_size")
    if isinstance(value, str) and value.strip():
        return value.strip()
    quantity = _off_number_or_none(product.get("serving_quantity"))
    if quantity is not None:
        return f"{quantity:g} g"
    return None


def _off_serving_quantity_g(product: dict[str, Any]) -> float | None:
    quantity = _off_number_or_none(product.get("serving_quantity"))
    if quantity is not None and quantity > 0:
        return quantity
    serving_size = product.get("serving_size")
    if isinstance(serving_size, str):
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*g\b", serving_size, flags=re.IGNORECASE)
        if match:
            parsed = _off_number_or_none(match.group(1).replace(",", "."))
            if parsed is not None and parsed > 0:
                return parsed
    return None


def _off_serving_macros(nutriments: dict[str, Any], *, serving_quantity_g: float | None = None) -> dict[str, Any] | None:
    required = (
        "energy-kcal_serving",
        "proteins_serving",
        "carbohydrates_serving",
        "fat_serving",
    )
    if any(nutriments.get(key) is None for key in required):
        return None
    return {
        "calories": nutriments.get("energy-kcal_serving"),
        "protein_g": nutriments.get("proteins_serving"),
        "carbs_g": nutriments.get("carbohydrates_serving"),
        "fat_g": nutriments.get("fat_serving"),
        "sodium_mg": _off_serving_sodium_mg(nutriments, serving_quantity_g=serving_quantity_g),
        "fiber_g": _off_optional_number(nutriments.get("fiber_serving")),
    }


def _off_serving_sodium_mg(nutriments: dict[str, Any], *, serving_quantity_g: float | None = None) -> int:
    if nutriments.get("sodium_serving") is not None:
        sodium = _off_number_or_none(nutriments["sodium_serving"])
        if sodium is not None:
            return int(round(sodium * 1000))
    if nutriments.get("salt_serving") is not None:
        salt = _off_number_or_none(nutriments["salt_serving"])
        if salt is not None:
            return int(round(salt * 393.4))
    if serving_quantity_g is not None and serving_quantity_g > 0:
        serving_factor = serving_quantity_g / 100
        if nutriments.get("sodium_100g") is not None:
            sodium = _off_number_or_none(nutriments["sodium_100g"])
            if sodium is not None:
                return int(round(sodium * 1000 * serving_factor))
        if nutriments.get("salt_100g") is not None:
            salt = _off_number_or_none(nutriments["salt_100g"])
            if salt is not None:
                return int(round(salt * 393.4 * serving_factor))
    return 0


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


def _nutritionix_items_have_required_nutrients(foods: list[dict[str, Any]]) -> bool:
    for food in foods:
        for key in NUTRITIONIX_REQUIRED_NUTRIENTS:
            value = food.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
    return True


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
    if not source_brands:
        return None
    if not requested_brand:
        return next((brand for brand in source_brands if brand), None)
    matched_brands = [
        brand
        for brand in source_brands
        if brand and _brand_from_text(normalize_meal_text(brand)) == requested_brand
    ]
    if len(matched_brands) != len(source_brands):
        return None
    return matched_brands[0] if matched_brands else None


def _requested_item_mismatch(normalized: str, foods: list[dict[str, Any]]) -> bool:
    requested_items = _customizable_item_tokens_for_mismatch(normalized)
    if not requested_items:
        return False
    returned_text = " ".join(str(food.get("food_name") or food.get("description") or "") for food in foods)
    returned_items = _customizable_item_tokens_for_mismatch(normalize_meal_text(returned_text))
    return not returned_items or requested_items != returned_items


def _customizable_item_tokens_for_mismatch(normalized: str) -> set[str]:
    tokens = (normalized or "").split()
    regional_brand = _regional_restaurant_brand_tokens(tokens)
    if regional_brand:
        tokens = _regional_restaurant_item_tokens(tokens, regional_brand)
    return set(tokens) & CUSTOMIZABLE_ITEM_TOKENS


def _matching_usda_source_brand(food: dict[str, Any], requested_brand: str | None) -> str | None:
    source_parts = [
        str(food.get("brandOwner") or "").strip(),
        str(food.get("brandName") or "").strip(),
        str(food.get("brand_name") or "").strip(),
    ]
    source_brands = [brand for brand in source_parts if brand]
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
    regional_brand = _regional_restaurant_brand_tokens((normalized or "").split())
    if regional_brand:
        return " ".join(regional_brand)
    for token in normalized.split():
        if token in KNOWN_BRANDS:
            return BRAND_ALIASES.get(token, token)
    return None


def _infer_meal_type(normalized: str) -> str:
    tokens = set((normalized or "").split())
    if tokens & BREAKFAST_TOKENS:
        return "breakfast"
    if tokens & LUNCH_DINNER_TOKENS:
        return "lunch"
    return "snack"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
