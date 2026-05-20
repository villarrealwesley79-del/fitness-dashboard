#!/usr/bin/env python3
"""Refresh the offline nutrition snapshot from official APIs only.

This script intentionally defaults to dry-run. USDA FoodData Central content is
public domain; Nutritionix-derived data must not be redistributed unless a
human ToS review explicitly clears it.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from urllib import error, parse, request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "data" / "nutrition_snapshot.json"
FDC_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
USDA_SEED_QUERIES = ("banana", "oatmeal", "egg", "sweet potato", "almond")
DEFAULT_DOWNLOAD_DIR = Path(os.environ.get("FDC_DOWNLOAD_DIR", "/tmp/fit86-fdc"))
FNDDS_ARCHIVE = "fndds.zip"
FNDDS_URL = "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_survey_food_json_2024-10-31.zip"
FNDDS_MEMBER = "surveyDownload.json"
GENERIC_TARGET_COUNT = 1000
CHAIN_TARGET_COUNT = 50
CHAIN_MARKERS = (
    "(burger king)",
    "(mcdonalds)",
    " fast food",
    ", fast food",
    "fast food / restaurant",
    "from fast food",
    "from fast food / restaurant",
    "from restaurant",
)
CHAIN_FOOD_TERMS = (
    "burrito",
    "burger",
    "cheeseburger",
    "chicken nuggets",
    "chicken sandwich",
    "chicken tenders",
    "french fries",
    "fried chicken",
    "hamburger",
    "milk shake",
    "pizza",
    "quesadilla",
    "quarter pounder",
    "sandwich",
    "taco",
    "whopper",
)
CHAIN_COMMON_ALIAS_TERMS = (
    "big mac",
    "chicken nuggets",
    "chicken tenders",
    "fish sandwich",
    "french fries",
    "mcdouble",
    "milk shake",
    "pizza",
    "quarter pounder",
    "whopper",
)
BREAKFAST_TERMS = {
    "bagel",
    "breakfast",
    "cereal",
    "coffee",
    "egg",
    "eggs",
    "french toast",
    "muffin",
    "oat",
    "oatmeal",
    "pancake",
    "pancakes",
    "toast",
    "waffle",
    "waffles",
    "yogurt",
}
LUNCH_DINNER_TERMS = {
    "beans",
    "beef",
    "burrito",
    "burger",
    "cheeseburger",
    "chicken",
    "chili",
    "fish",
    "fries",
    "hamburger",
    "meat",
    "pasta",
    "pizza",
    "pork",
    "quesadilla",
    "rice",
    "salad",
    "sandwich",
    "soup",
    "spaghetti",
    "taco",
    "tacos",
    "turkey",
    "whopper",
    "wrap",
}
BREAKFAST_CATEGORIES = (
    "breakfast",
    "cereals",
    "eggs",
    "pancakes",
    "waffles",
)
LUNCH_DINNER_CATEGORIES = (
    "burritos",
    "burgers",
    "chicken",
    "dumplings",
    "egg rolls",
    "fish",
    "frankfurters",
    "fried potatoes",
    "meat",
    "pasta",
    "pizza",
    "rice",
    "salads",
    "sandwiches",
    "seafood",
    "soups",
    "stir-fry",
    "sushi",
    "tortilla",
)
NUTRIENTS = {
    "calories": {"Energy", "Energy (Atwater General Factors)", "Energy (Atwater Specific Factors)"},
    "protein_g": {"Protein"},
    "carbs_g": {"Carbohydrate, by difference"},
    "fat_g": {"Total lipid (fat)"},
    "sodium_mg": {"Sodium, Na"},
    "fiber_g": {"Fiber, total dietary"},
}
DESCRIPTOR_FIRST_ALIASES = {
    ("milk", "whole"): ("whole milk",),
    ("milk", "low fat (1%)"): ("low fat milk", "1% milk"),
    ("milk", "fat free (skim)"): ("fat free milk", "skim milk"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write the refreshed snapshot")
    parser.add_argument("--output", default=str(SNAPSHOT_PATH), help="snapshot path to write when --write is set")
    parser.add_argument("--limit", type=int, default=len(USDA_SEED_QUERIES), help="number of seed queries to refresh")
    parser.add_argument(
        "--source-dir",
        default=str(DEFAULT_DOWNLOAD_DIR),
        help="directory containing FoodData Central download archives",
    )
    parser.add_argument(
        "--allow-downloads",
        action="store_true",
        help="download missing USDA FoodData Central archives into --source-dir",
    )
    parser.add_argument("--generic-limit", type=int, default=GENERIC_TARGET_COUNT)
    parser.add_argument("--chain-limit", type=int, default=CHAIN_TARGET_COUNT)
    parser.add_argument(
        "--api-seed-refresh",
        action="store_true",
        help="legacy FIT-75 API seed refresh; requires USDA_FDC_API_KEY when --write is used",
    )
    args = parser.parse_args()

    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    if not args.write:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "items": len(snapshot.get("items", [])),
                    "writes": False,
                    "coverage": snapshot.get("coverage", {}),
                    "note": "USDA offline snapshot loaded; use --write with FoodData Central downloads to refresh.",
                },
                sort_keys=True,
            )
        )
        return 0

    if not args.api_seed_refresh:
        source_dir = Path(args.source_dir)
        archive = ensure_fndds_archive(source_dir, allow_downloads=args.allow_downloads)
        if not archive:
            print(
                json.dumps(
                    {
                        "status": "missing_source_archive",
                        "archive": str(source_dir / FNDDS_ARCHIVE),
                        "download_url": FNDDS_URL,
                        "hint": "rerun with --allow-downloads or place the USDA FNDDS JSON archive at this path",
                        "writes": False,
                    },
                    sort_keys=True,
                )
            )
            return 2
        refreshed = build_curated_snapshot_from_download(
            archive,
            existing_snapshot=snapshot,
            generic_limit=max(args.generic_limit, 0),
            chain_limit=max(args.chain_limit, 0),
        )
        completeness_error = curated_completeness_error(refreshed, args.generic_limit, args.chain_limit)
        if completeness_error:
            print(json.dumps(completeness_error, sort_keys=True))
            return 3
        output = Path(args.output)
        output.write_text(json.dumps(refreshed, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": "written", "items": len(refreshed.get("items", [])), "writes": True}, sort_keys=True))
        return 0

    if not os.environ.get("USDA_FDC_API_KEY"):
        print(json.dumps({"status": "missing_api_key", "env_var": "USDA_FDC_API_KEY", "writes": False}, sort_keys=True))
        return 2

    seed_queries = USDA_SEED_QUERIES[: max(args.limit, 0)]
    refreshed = build_snapshot(seed_queries, existing_snapshot=snapshot)
    completeness_error = refresh_completeness_error(refreshed, seed_queries)
    if completeness_error:
        print(json.dumps(completeness_error, sort_keys=True))
        return 3
    output = Path(args.output)
    output.write_text(json.dumps(refreshed, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "written", "items": len(refreshed.get("items", [])), "writes": True}, sort_keys=True))
    return 0


def build_snapshot(seed_queries: tuple[str, ...] | list[str], *, existing_snapshot: dict | None = None) -> dict:
    existing_items = _existing_snapshot_items(existing_snapshot)
    items = []
    for query in seed_queries:
        existing_item = existing_items.get(str(query).strip().lower())
        food = fetch_usda_food(query, existing_item=existing_item)
        if food:
            items.append(food)
    now = datetime.now(UTC).replace(microsecond=0)
    return {
        "version": now.strftime("%Y-%m-%d-usda-refresh"),
        "license": "USDA FoodData Central data is public domain. No Nutritionix-derived data is redistributed in this starter snapshot.",
        "generated_at": now.isoformat(),
        "data_sources": [
            {
                "name": "USDA FoodData Central",
                "url": "https://fdc.nal.usda.gov/",
                "license": "U.S. public domain",
            }
        ],
        "nutritionix_redistribution_status": "blocked_pending_tos_review; no Nutritionix-derived data included",
        "coverage": {
            "profile": "starter_usda_generic_only",
            "seed_queries": list(seed_queries),
            "full_curation_status": "deferred; FIT-75 marks actual snapshot data content as a separate curation pass",
        },
        "items": items,
    }


def ensure_fndds_archive(source_dir: Path, *, allow_downloads: bool) -> Path | None:
    archive = source_dir / FNDDS_ARCHIVE
    if archive.exists():
        return archive
    if not allow_downloads:
        return None
    source_dir.mkdir(parents=True, exist_ok=True)
    try:
        with request.urlopen(FNDDS_URL, timeout=60) as resp:
            archive.write_bytes(resp.read())
    except (OSError, error.URLError, error.HTTPError, TimeoutError):
        return None
    return archive


def build_curated_snapshot_from_download(
    fndds_archive: Path,
    *,
    existing_snapshot: dict | None = None,
    generic_limit: int = GENERIC_TARGET_COUNT,
    chain_limit: int = CHAIN_TARGET_COUNT,
) -> dict:
    foods = load_fndds_foods(fndds_archive)
    existing_items = [
        item
        for item in _existing_snapshot_items(existing_snapshot).values()
        if item.get("snapshot_group") in (None, "starter_compatibility")
    ]
    existing_keys = {str(item.get("normalized_text") or "").strip().lower() for item in existing_items}

    chain_items = []
    for food in _curated_chain_foods(foods, limit=chain_limit):
        item = fndds_food_to_snapshot_item(food, snapshot_group="chain_default")
        key = str(item["normalized_text"]).strip().lower()
        if key in existing_keys:
            continue
        chain_items.append(item)
        existing_keys.add(key)

    generic_items = _category_balanced_generic_items(foods, existing_keys, limit=generic_limit)
    now = datetime.now(UTC).replace(microsecond=0)
    items = _reconcile_default_aliases([*_tag_existing_items(existing_items), *chain_items, *generic_items])
    return {
        "version": now.strftime("%Y-%m-%d-usda-fndds-curated"),
        "license": (
            "USDA FoodData Central data is public domain. No Nutritionix-derived data is redistributed in this snapshot."
        ),
        "generated_at": now.isoformat(),
        "data_sources": [
            {
                "name": "USDA FoodData Central",
                "dataset": "Food and Nutrient Database for Dietary Studies 2021-2023",
                "release_date": "2024-10-31",
                "url": "https://fdc.nal.usda.gov/download-datasets/",
                "license": "U.S. public domain",
            }
        ],
        "nutritionix_redistribution_status": "blocked_pending_tos_review; no Nutritionix-derived data included",
        "coverage": {
            "profile": "curated_fndds_chain_and_generic",
            "chain_default_items": len(chain_items),
            "chain_target_items": chain_limit,
            "generic_items": len(generic_items),
            "generic_target_items": generic_limit,
            "starter_compatibility_items": len(existing_items),
            "selection_method": (
                "FNDDS chain/restaurant markers for safe-default chain coverage; category-balanced FNDDS "
                "survey foods for broad generic coverage after complete macro/sodium/fiber filtering."
            ),
            "full_curation_status": "complete_for_fit_86_safe_default_snapshot",
        },
        "items": items,
    }


def load_fndds_foods(fndds_archive: Path) -> list[dict]:
    with zipfile.ZipFile(fndds_archive) as archive:
        with archive.open(FNDDS_MEMBER) as raw:
            payload = json.load(raw)
    foods = payload.get("SurveyFoods") if isinstance(payload, dict) else None
    if not isinstance(foods, list):
        raise ValueError(f"{fndds_archive} does not contain {FNDDS_MEMBER} SurveyFoods")
    return [food for food in foods if isinstance(food, dict)]


def curated_completeness_error(snapshot: dict, generic_limit: int, chain_limit: int) -> dict | None:
    coverage = snapshot.get("coverage") if isinstance(snapshot, dict) else {}
    generic_items = int(coverage.get("generic_items") or 0)
    chain_items = int(coverage.get("chain_default_items") or 0)
    if generic_items >= generic_limit and chain_items >= chain_limit:
        return None
    return {
        "status": "incomplete_refresh",
        "expected_chain_items": chain_limit,
        "chain_items": chain_items,
        "expected_generic_items": generic_limit,
        "generic_items": generic_items,
        "writes": False,
    }


def fndds_food_to_snapshot_item(food: dict, *, snapshot_group: str) -> dict:
    nutrients = _nutrient_map(food.get("foodNutrients") or [])
    fdc_id = str(food.get("fdcId") or "")
    description = str(food.get("description") or fdc_id)
    category = _wweia_category(food)
    item = {
        "normalized_text": _normalized_description(description),
        "item_name": description,
        "aliases": _snapshot_aliases(description, snapshot_group=snapshot_group),
        "portion_description": "100 g",
        "meal_type": _meal_type_for_snapshot_item(description, None, category=category),
        "calories": nutrients.get("calories", 0),
        "protein_g": nutrients.get("protein_g", 0),
        "carbs_g": nutrients.get("carbs_g", 0),
        "fat_g": nutrients.get("fat_g", 0),
        "sodium_mg": nutrients.get("sodium_mg", 0),
        "fiber_g": nutrients.get("fiber_g", 0),
        "external_food_id": fdc_id,
        "verified_source_url": f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{fdc_id}/nutrients",
        "portion_basis": "100 g USDA FoodData Central FNDDS reference portion",
        "data_fetched_at": str(food.get("publicationDate") or "2024-10-31"),
        "source_dataset": "USDA FNDDS 2021-2023",
        "snapshot_group": snapshot_group,
        "wweia_category": category,
    }
    if snapshot_group == "chain_default":
        item["chain_default_basis"] = "USDA FNDDS chain, fast-food, or restaurant description"
    return item


def _complete_foods(foods: list[dict]) -> list[dict]:
    return [food for food in foods if _has_complete_snapshot_nutrients(food)]


def _has_complete_snapshot_nutrients(food: dict) -> bool:
    nutrients = _nutrient_map(food.get("foodNutrients") or [])
    return all(key in nutrients for key in ("calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "fiber_g"))


def _category_balanced_generic_items(foods: list[dict], existing_keys: set[str], *, limit: int) -> list[dict]:
    grouped: dict[str, deque[dict]] = defaultdict(deque)
    seen = set(existing_keys)
    for food in sorted(_complete_foods(foods), key=lambda item: int(item.get("fdcId") or 0)):
        if _has_chain_or_restaurant_marker(food):
            continue
        key = _normalized_description(str(food.get("description") or ""))
        if not key or key in seen:
            continue
        grouped[_wweia_category(food)].append(food)
        seen.add(key)

    items = []
    categories = deque(sorted(grouped))
    while categories and len(items) < limit:
        category = categories.popleft()
        foods_for_category = grouped[category]
        if not foods_for_category:
            continue
        items.append(fndds_food_to_snapshot_item(foods_for_category.popleft(), snapshot_group="generic"))
        if foods_for_category:
            categories.append(category)
    return items


def _curated_chain_foods(foods: list[dict], *, limit: int) -> list[dict]:
    candidates = [food for food in sorted(_complete_foods(foods), key=_chain_sort_key) if _is_chain_default(food)]
    selected: list[dict] = []
    selected_ids: set[str] = set()
    grouped: dict[str, deque[dict]] = defaultdict(deque)
    for food in candidates:
        term = _chain_food_term(food)
        if term:
            grouped[term].append(food)

    for term in CHAIN_FOOD_TERMS:
        while grouped[term]:
            food = grouped[term].popleft()
            fdc_id = str(food.get("fdcId") or "")
            if fdc_id not in selected_ids:
                selected.append(food)
                selected_ids.add(fdc_id)
                break
        if len(selected) >= limit:
            return selected

    for food in candidates:
        if len(selected) >= limit:
            break
        fdc_id = str(food.get("fdcId") or "")
        if fdc_id in selected_ids:
            continue
        selected.append(food)
        selected_ids.add(fdc_id)
    return selected


def _is_chain_default(food: dict) -> bool:
    description = str(food.get("description") or "").lower()
    if _has_branded_chain_marker(description):
        return True
    if _has_chain_or_restaurant_marker(food):
        return any(term in description for term in CHAIN_FOOD_TERMS)
    return False


def _has_chain_or_restaurant_marker(food: dict) -> bool:
    description = str(food.get("description") or "").lower()
    return any(marker in description for marker in CHAIN_MARKERS)


def _has_branded_chain_marker(description: str) -> bool:
    return "(burger king)" in description or "(mcdonalds)" in description


def _chain_food_term(food: dict) -> str | None:
    description = str(food.get("description") or "").lower()
    for term in CHAIN_FOOD_TERMS:
        if term in description:
            return term
    return None


def _chain_sort_key(food: dict) -> tuple[int, int]:
    description = str(food.get("description") or "").lower()
    if "(mcdonalds)" in description or "(burger king)" in description:
        priority = 0
    elif "from fast food" in description:
        priority = 1
    elif "from fast food / restaurant" in description:
        priority = 2
    elif "from restaurant" in description:
        priority = 3
    else:
        priority = 4
    return priority, int(food.get("fdcId") or 0)


def _tag_existing_items(items: list[dict]) -> list[dict]:
    tagged = []
    for item in items:
        copy = dict(item)
        copy.setdefault("snapshot_group", "starter_compatibility")
        copy.setdefault("source_dataset", "USDA FoodData Central starter snapshot")
        tagged.append(copy)
    return tagged


def _normalized_description(description: str) -> str:
    return " ".join(
        description.lower()
        .replace("/", " ")
        .replace("&", " and ")
        .replace("(", " ")
        .replace(")", " ")
        .replace(",", " ")
        .replace(";", " ")
        .split()
    )


def _snapshot_aliases(description: str, *, snapshot_group: str) -> list[str]:
    aliases = {description, _normalized_description(description)}
    lowered = description.lower()
    primary_name = description.split(",", 1)[0].strip()
    if _is_default_generic_description(primary_name, lowered):
        aliases.add(primary_name)
    aliases.update(_descriptor_first_aliases(primary_name, lowered))
    if "(mcdonalds)" in lowered:
        base = description.replace("(McDonalds)", "").strip()
        aliases.add(f"mcdonalds {base}")
        aliases.add(f"mcdonald's {base}")
    if "(burger king)" in lowered:
        base = description.replace("(Burger King)", "").strip()
        aliases.add(f"burger king {base}")
    if snapshot_group == "chain_default":
        aliases.add(lowered.replace("from fast food / restaurant", "fast food"))
        aliases.add(lowered.replace("from fast food", "fast food"))
        aliases.add(lowered.replace("from restaurant", "restaurant"))
        aliases.update(_chain_common_aliases(lowered))
    return sorted(alias for alias in aliases if alias)


def _is_default_generic_description(primary_name: str, lowered_description: str) -> bool:
    if not primary_name or len(primary_name.split()) > 3:
        return False
    primary = primary_name.lower()
    if lowered_description in {
        f"{primary}, raw",
        f"{primary}, nfs",
        f"{primary}, cooked, nfs",
        f"{primary}, ns as to type",
        f"{primary}, ns as to major flour",
        f"{primary}, ns as to form",
    }:
        return True
    return lowered_description == f"{primary}, 100%, nfs"


def _descriptor_first_aliases(primary_name: str, lowered_description: str) -> set[str]:
    primary = primary_name.lower()
    prefix = f"{primary}, "
    if not primary or not lowered_description.startswith(prefix):
        return set()
    descriptor = lowered_description[len(prefix) :].strip()
    return set(DESCRIPTOR_FIRST_ALIASES.get((primary, descriptor), ()))


def _chain_common_aliases(lowered_description: str) -> set[str]:
    aliases = set()
    for term in CHAIN_COMMON_ALIAS_TERMS:
        if term not in lowered_description:
            continue
        if term in {"big mac", "mcdouble", "quarter pounder", "whopper"}:
            branded_description = lowered_description.startswith(f"{term} (")
            if not branded_description:
                continue
        aliases.add(term)
    return aliases


def _reconcile_default_aliases(items: list[dict]) -> list[dict]:
    default_groups: dict[str, dict[str, dict]] = defaultdict(dict)
    for item in items:
        if not isinstance(item, dict):
            continue
        description = str(item.get("item_name") or "")
        primary_name = description.split(",", 1)[0].strip()
        if not primary_name:
            continue
        lowered = description.lower()
        if lowered == f"{primary_name.lower()}, raw":
            default_groups[primary_name.lower()]["raw"] = item
        elif lowered == f"{primary_name.lower()}, nfs":
            default_groups[primary_name.lower()]["nfs"] = item

    for primary_lower, variants in default_groups.items():
        if "raw" not in variants or "nfs" not in variants:
            continue
        raw_aliases = variants["raw"].get("aliases")
        if isinstance(raw_aliases, list):
            variants["raw"]["aliases"] = [
                alias
                for alias in raw_aliases
                if str(alias).strip().lower() != primary_lower
            ]
    return items


def _wweia_category(food: dict) -> str:
    category = food.get("wweiaFoodCategory") or {}
    if isinstance(category, dict):
        value = category.get("wweiaFoodCategoryDescription")
        if value:
            return str(value)
    return "Uncategorized"


def refresh_completeness_error(snapshot: dict, seed_queries: tuple[str, ...] | list[str]) -> dict | None:
    item_count = len(snapshot.get("items", []))
    if item_count == len(seed_queries):
        return None
    return {
        "status": "incomplete_refresh",
        "expected_items": len(seed_queries),
        "items": item_count,
        "writes": False,
    }


def fetch_usda_food(query: str, *, timeout: float = 10.0, existing_item: dict | None = None) -> dict | None:
    params = {
        "query": query,
        "pageSize": "1",
        "dataType": "Foundation,SR Legacy",
    }
    api_key = os.environ.get("USDA_FDC_API_KEY")
    if api_key:
        params["api_key"] = api_key
    url = f"{FDC_SEARCH_URL}?{parse.urlencode(params)}"
    try:
        with request.urlopen(request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None
    foods = payload.get("foods") if isinstance(payload, dict) else None
    if not foods:
        return None
    return food_to_snapshot_item(query, foods[0], existing_item=existing_item)


def food_to_snapshot_item(query: str, food: dict, *, existing_item: dict | None = None) -> dict:
    nutrients = _nutrient_map(food.get("foodNutrients") or [])
    fdc_id = str(food.get("fdcId") or "")
    return {
        "normalized_text": query,
        "item_name": food.get("description") or query.title(),
        "portion_description": _portion_description(food),
        "meal_type": _meal_type_for_snapshot_item(query, existing_item),
        "calories": nutrients.get("calories", 0),
        "protein_g": nutrients.get("protein_g", 0),
        "carbs_g": nutrients.get("carbs_g", 0),
        "fat_g": nutrients.get("fat_g", 0),
        "sodium_mg": nutrients.get("sodium_mg", 0),
        "fiber_g": nutrients.get("fiber_g", 0),
        "external_food_id": fdc_id,
        "verified_source_url": f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{fdc_id}/nutrients",
        "portion_basis": _portion_description(food) + " USDA FoodData Central reference portion",
    }


def _existing_snapshot_items(snapshot: dict | None) -> dict[str, dict]:
    items = snapshot.get("items") if isinstance(snapshot, dict) else None
    if not isinstance(items, list):
        return {}
    indexed: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("normalized_text") or "").strip().lower()
        if key:
            indexed[key] = item
    return indexed


def _meal_type_for_snapshot_item(query: str, existing_item: dict | None, *, category: str | None = None) -> str:
    meal_type = str((existing_item or {}).get("meal_type") or "").strip().lower()
    if meal_type in {"breakfast", "lunch", "dinner", "snack"}:
        return meal_type
    haystack = _normalized_description(" ".join(part for part in (query, category or "") if part))
    if _category_matches(category, LUNCH_DINNER_CATEGORIES):
        return "lunch"
    if _category_matches(category, BREAKFAST_CATEGORIES):
        return "breakfast"
    if any(_contains_term(haystack, term) for term in BREAKFAST_TERMS) or _category_matches(
        category,
        BREAKFAST_CATEGORIES,
    ):
        return "breakfast"
    if any(_contains_term(haystack, term) for term in LUNCH_DINNER_TERMS):
        return "lunch"
    return "snack"


def _contains_term(haystack: str, term: str) -> bool:
    return f" {term} " in f" {haystack} "


def _category_matches(category: str | None, markers: tuple[str, ...]) -> bool:
    lowered = str(category or "").lower()
    return any(marker in lowered for marker in markers)


def _nutrient_map(food_nutrients: list[dict]) -> dict[str, float]:
    values: dict[str, float] = {}
    for nutrient in food_nutrients:
        nutrient_meta = nutrient.get("nutrient") if isinstance(nutrient.get("nutrient"), dict) else {}
        name = str(nutrient.get("nutrientName") or nutrient_meta.get("name") or "")
        value = nutrient.get("value", nutrient.get("amount"))
        for key, names in NUTRIENTS.items():
            if name in names and value is not None:
                values[key] = round(float(value), 1)
    return values


def _portion_description(food: dict) -> str:
    serving = food.get("servingSize")
    unit = food.get("servingSizeUnit")
    if serving and unit:
        try:
            serving_text = f"{float(serving):g}"
        except (TypeError, ValueError):
            serving_text = str(serving)
        return f"{serving_text} {unit}"
    household = food.get("householdServingFullText")
    if household:
        return str(household)
    return "100 g"


if __name__ == "__main__":
    raise SystemExit(main())
