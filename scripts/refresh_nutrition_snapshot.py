#!/usr/bin/env python3
"""Refresh the offline nutrition snapshot from official APIs only.

This script intentionally defaults to dry-run. USDA FoodData Central content is
public domain; Nutritionix-derived data must not be redistributed unless a
human ToS review explicitly clears it.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from urllib import parse, request


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "data" / "nutrition_snapshot.json"
FDC_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
USDA_SEED_QUERIES = ("banana", "oatmeal", "egg", "sweet potato", "almond")
NUTRIENTS = {
    "calories": {"Energy"},
    "protein_g": {"Protein"},
    "carbs_g": {"Carbohydrate, by difference"},
    "fat_g": {"Total lipid (fat)"},
    "sodium_mg": {"Sodium, Na"},
    "fiber_g": {"Fiber, total dietary"},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write the refreshed snapshot")
    parser.add_argument("--output", default=str(SNAPSHOT_PATH), help="snapshot path to write when --write is set")
    parser.add_argument("--limit", type=int, default=len(USDA_SEED_QUERIES), help="number of seed queries to refresh")
    args = parser.parse_args()

    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    if not args.write:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "items": len(snapshot.get("items", [])),
                    "writes": False,
                    "note": "USDA-only starter snapshot loaded; live refresh requires a separate curation pass.",
                },
                sort_keys=True,
            )
        )
        return 0

    refreshed = build_snapshot(USDA_SEED_QUERIES[: max(args.limit, 0)])
    output = Path(args.output)
    output.write_text(json.dumps(refreshed, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "written", "items": len(refreshed.get("items", [])), "writes": True}, sort_keys=True))
    return 0


def build_snapshot(seed_queries: tuple[str, ...] | list[str]) -> dict:
    items = []
    for query in seed_queries:
        food = fetch_usda_food(query)
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


def fetch_usda_food(query: str, *, timeout: float = 10.0) -> dict | None:
    params = {
        "query": query,
        "pageSize": "1",
        "dataType": "Foundation,SR Legacy",
    }
    api_key = os.environ.get("USDA_FDC_API_KEY")
    if api_key:
        params["api_key"] = api_key
    url = f"{FDC_SEARCH_URL}?{parse.urlencode(params)}"
    with request.urlopen(request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    foods = payload.get("foods") if isinstance(payload, dict) else None
    if not foods:
        return None
    return food_to_snapshot_item(query, foods[0])


def food_to_snapshot_item(query: str, food: dict) -> dict:
    nutrients = _nutrient_map(food.get("foodNutrients") or [])
    fdc_id = str(food.get("fdcId") or "")
    return {
        "normalized_text": query,
        "item_name": food.get("description") or query.title(),
        "portion_description": _portion_description(food),
        "meal_type": "snack",
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


def _nutrient_map(food_nutrients: list[dict]) -> dict[str, float]:
    values: dict[str, float] = {}
    for nutrient in food_nutrients:
        name = str(nutrient.get("nutrientName") or "")
        value = nutrient.get("value")
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
