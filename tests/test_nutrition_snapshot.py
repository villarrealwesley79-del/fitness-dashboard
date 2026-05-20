from __future__ import annotations

import json
import importlib
import subprocess
import sys
from pathlib import Path
import urllib.request

import branded_food_lookup
from meal_log_policy import evaluate_meal_log
from scripts import refresh_nutrition_snapshot


def test_snapshot_lookup_works_without_network_or_api_keys(monkeypatch):
    monkeypatch.delenv("NUTRITIONIX_APP_ID", raising=False)
    monkeypatch.delenv("NUTRITIONIX_APP_KEY", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network blocked")))

    result = branded_food_lookup.lookup("bananas", source_priority=("snapshot", "nutritionix", "usda_fdc"))

    assert result["source"] == "offline_snapshot"
    assert result["item_name"] == "Banana, raw"
    assert result["calories"] == 89
    assert result["external_food_id"] == "173944"


def test_parse_meal_text_reaches_snapshot_for_prefixed_food(monkeypatch):
    parser = importlib.import_module("meal_text_parser")
    monkeypatch.delenv("NUTRITIONIX_APP_ID", raising=False)
    monkeypatch.delenv("NUTRITIONIX_APP_KEY", raising=False)
    monkeypatch.delenv("USDA_FDC_API_KEY", raising=False)
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a, **_kw: None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network blocked")))

    result = parser.parse_meal_text("a banana")

    assert result["fallback_used"] is False
    assert result["estimate"]["source"] == "offline_snapshot"
    assert result["estimate"]["external_food_id"] == "173944"


def test_unquantified_snapshot_hit_requires_review():
    result = branded_food_lookup.lookup("oatmeal", source_priority=("snapshot",))
    decision = evaluate_meal_log(result)

    assert result["source"] == "offline_snapshot"
    assert result["ambiguous"] is True
    assert result["confidence"] < 0.75
    assert "reference portion" in result["uncertainty_notes"][0]
    assert decision["status"] == "pending_review"


def test_snapshot_plural_variants_hit_bundled_foods():
    assert branded_food_lookup.lookup("eggs", source_priority=("snapshot",))["external_food_id"] == "173424"
    assert branded_food_lookup.lookup("almonds", source_priority=("snapshot",))["external_food_id"] == "170567"
    assert branded_food_lookup.lookup("sweet potatoes", source_priority=("snapshot",))["external_food_id"] == "168482"
    assert branded_food_lookup.lookup("a banana", source_priority=("snapshot",))["external_food_id"] == "173944"
    assert branded_food_lookup.lookup("2 eggs", source_priority=("snapshot",))["external_food_id"] == "173424"
    assert branded_food_lookup.lookup("one sweet potato", source_priority=("snapshot",))["external_food_id"] == "168482"


def test_snapshot_suffix_match_ignores_compound_foods():
    assert branded_food_lookup.lookup("toast and banana", source_priority=("snapshot",)) is None
    assert branded_food_lookup.lookup("oatmeal and banana", source_priority=("snapshot",)) is None
    assert branded_food_lookup.lookup("banana and oatmeal", source_priority=("snapshot",)) is None
    assert branded_food_lookup.lookup("yogurt banana", source_priority=("snapshot",)) is None


def test_snapshot_file_is_small_and_documents_license():
    path = Path("data/nutrition_snapshot.json")
    payload = json.loads(path.read_text())

    assert path.stat().st_size < 5 * 1024 * 1024
    assert "USDA" in payload["license"]
    assert "No Nutritionix-derived data" in payload["license"]
    assert payload["data_sources"][0]["name"] == "USDA FoodData Central"
    assert payload["data_sources"][0]["dataset"] == "Food and Nutrient Database for Dietary Studies 2021-2023"
    assert "blocked_pending_tos_review" in payload["nutritionix_redistribution_status"]
    assert payload["coverage"]["chain_default_items"] >= 50
    assert payload["coverage"]["generic_items"] >= 1000
    assert len(payload["items"]) >= 1050
    chain_names = {
        item["item_name"]
        for item in payload["items"]
        if item.get("snapshot_group") == "chain_default"
    }
    generic_names = {
        item["item_name"]
        for item in payload["items"]
        if item.get("snapshot_group") == "generic"
    }
    assert "Potato, french fries, fast food" in chain_names
    assert "Milk shake, fast food, chocolate" in chain_names
    assert "Quarter Pounder (McDonalds)" in chain_names
    assert "Quarter Pounder (McDonalds)" not in generic_names
    assert not any("(McDonalds)" in name or "(Burger King)" in name for name in generic_names)


def test_snapshot_supports_chain_alias_without_network(monkeypatch):
    monkeypatch.delenv("NUTRITIONIX_APP_ID", raising=False)
    monkeypatch.delenv("NUTRITIONIX_APP_KEY", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network blocked")))

    result = branded_food_lookup.lookup("mcdonalds cheeseburger", source_priority=("snapshot", "nutritionix", "usda_fdc"))

    assert result["source"] == "offline_snapshot"
    assert result["item_name"] == "Cheeseburger (McDonalds)"
    assert result["meal_type"] == "lunch"
    assert result["calories"] > 0
    assert "fdc.nal.usda.gov" in result["verified_source_url"]


def test_snapshot_supports_common_chain_aliases_without_network(monkeypatch):
    monkeypatch.delenv("NUTRITIONIX_APP_ID", raising=False)
    monkeypatch.delenv("NUTRITIONIX_APP_KEY", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network blocked")))

    expected = {
        "french fries": "Potato, french fries, fast food",
        "milk shake": "Milk shake, fast food, chocolate",
        "big mac": "Big Mac (McDonalds)",
        "whopper": "Whopper (Burger King)",
        "burger king hamburger": "Hamburger (Burger King)",
        "mcdonalds hamburger": "Hamburger (McDonalds)",
    }

    for query, item_name in expected.items():
        result = branded_food_lookup.lookup(query, source_priority=("snapshot", "nutritionix", "usda_fdc"))
        assert result["source"] == "offline_snapshot"
        assert result["item_name"] == item_name

    assert branded_food_lookup.lookup("hamburger", source_priority=("snapshot",)) is None
    assert branded_food_lookup.lookup("small hamburger", source_priority=("snapshot",)) is None


def test_snapshot_supports_broad_generic_alias_without_network(monkeypatch):
    monkeypatch.delenv("NUTRITIONIX_APP_ID", raising=False)
    monkeypatch.delenv("NUTRITIONIX_APP_KEY", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network blocked")))

    result = branded_food_lookup.lookup("avocado", source_priority=("snapshot", "nutritionix", "usda_fdc"))

    assert result["source"] == "offline_snapshot"
    assert result["item_name"] == "Avocado, raw"
    assert result["fiber_g"] > 0


def test_snapshot_supports_common_default_generic_aliases_without_network(monkeypatch):
    monkeypatch.delenv("NUTRITIONIX_APP_ID", raising=False)
    monkeypatch.delenv("NUTRITIONIX_APP_KEY", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network blocked")))

    expected = {
        "rice": "Rice, cooked, NFS",
        "coffee": "Coffee, NS as to type",
        "orange juice": "Orange juice, 100%, NFS",
        "bread": "Bread, NS as to major flour",
        "whole milk": "Milk, whole",
        "skim milk": "Milk, fat free (skim)",
        "low fat milk": "Milk, low fat (1%)",
    }

    for query, item_name in expected.items():
        result = branded_food_lookup.lookup(query, source_priority=("snapshot", "nutritionix", "usda_fdc"))
        assert result["source"] == "offline_snapshot"
        assert result["item_name"] == item_name


def test_snapshot_exact_key_wins_over_earlier_alias_collision():
    branded_food_lookup._SNAPSHOT_CACHE = None

    result = branded_food_lookup.lookup("cheeseburger slider", source_priority=("snapshot",))

    assert result["source"] == "offline_snapshot"
    assert result["item_name"] == "Cheeseburger slider"


def test_snapshot_generic_aliases_avoid_specialized_food_collisions():
    branded_food_lookup._SNAPSHOT_CACHE = None

    potato = branded_food_lookup.lookup("potato", source_priority=("snapshot",))
    fish = branded_food_lookup.lookup("fish", source_priority=("snapshot",))
    egg_roll = branded_food_lookup.lookup("egg roll", source_priority=("snapshot",))

    assert potato["item_name"] == "Potato, NFS"
    assert fish["item_name"] == "Fish, NFS"
    assert egg_roll is None


def test_refresh_script_builds_snapshot_from_official_usda_api(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return json.dumps(
                {
                    "foods": [
                        {
                            "fdcId": 173944,
                            "description": "Banana, raw",
                            "servingSize": 100,
                            "servingSizeUnit": "g",
                            "foodNutrients": [
                                {"nutrientName": "Energy", "value": 89},
                                {"nutrientName": "Protein", "value": 1.1},
                                {"nutrientName": "Carbohydrate, by difference", "value": 22.8},
                                {"nutrientName": "Total lipid (fat)", "value": 0.3},
                                {"nutrientName": "Sodium, Na", "value": 1},
                                {"nutrientName": "Fiber, total dietary", "value": 2.6},
                            ],
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(req, *, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(refresh_nutrition_snapshot.request, "urlopen", fake_urlopen)

    snapshot = refresh_nutrition_snapshot.build_snapshot(["banana"])

    assert captured["url"].startswith(refresh_nutrition_snapshot.FDC_SEARCH_URL)
    assert "query=banana" in captured["url"]
    assert snapshot["data_sources"][0]["name"] == "USDA FoodData Central"
    assert snapshot["nutritionix_redistribution_status"].startswith("blocked_pending_tos_review")
    assert snapshot["items"][0]["external_food_id"] == "173944"
    assert snapshot["items"][0]["calories"] == 89


def test_refresh_script_maps_atwater_energy_names():
    item = refresh_nutrition_snapshot.food_to_snapshot_item(
        "beans",
        {
            "fdcId": 123,
            "description": "Beans",
            "foodNutrients": [
                {"nutrientName": "Energy (Atwater General Factors)", "value": 132},
                {"nutrientName": "Protein", "value": 8.9},
            ],
        },
    )

    assert item["calories"] == 132
    assert item["protein_g"] == 8.9


def test_refresh_preserves_existing_snapshot_meal_type(monkeypatch):
    def fake_fetch_usda_food(query, *, existing_item=None):
        return refresh_nutrition_snapshot.food_to_snapshot_item(
            query,
            {
                "fdcId": 173905,
                "description": "Oats, cooked",
                "foodNutrients": [{"nutrientName": "Energy", "value": 71}],
            },
            existing_item=existing_item,
        )

    monkeypatch.setattr(refresh_nutrition_snapshot, "fetch_usda_food", fake_fetch_usda_food)

    snapshot = refresh_nutrition_snapshot.build_snapshot(
        ["oatmeal"],
        existing_snapshot={"items": [{"normalized_text": "oatmeal", "meal_type": "breakfast"}]},
    )

    assert snapshot["items"][0]["meal_type"] == "breakfast"


def test_refresh_infers_meal_type_for_curated_fndds_foods():
    assert (
        refresh_nutrition_snapshot._meal_type_for_snapshot_item(
            "Cheeseburger (McDonalds)",
            None,
            category="Burgers",
        )
        == "lunch"
    )
    assert (
        refresh_nutrition_snapshot._meal_type_for_snapshot_item(
            "Egg roll, with chicken or turkey",
            None,
            category="Egg rolls, dumplings, sushi",
        )
        == "lunch"
    )
    assert (
        refresh_nutrition_snapshot._meal_type_for_snapshot_item(
            "Pancakes, plain",
            None,
            category="Pancakes, waffles, French toast",
        )
        == "breakfast"
    )


def test_refresh_script_dry_run_does_not_write():
    path = Path("data/nutrition_snapshot.json")
    before = path.read_text()
    result = subprocess.run(
        [sys.executable, "scripts/refresh_nutrition_snapshot.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    after = path.read_text()
    payload = json.loads(result.stdout)

    assert payload["status"] == "dry_run"
    assert payload["writes"] is False
    assert after == before


def test_refresh_script_write_without_source_archive_explains_requirement(monkeypatch, tmp_path):
    monkeypatch.delenv("USDA_FDC_API_KEY", raising=False)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/refresh_nutrition_snapshot.py",
            "--write",
            "--output",
            str(tmp_path / "snapshot.json"),
            "--source-dir",
            str(tmp_path / "missing-sources"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 2
    assert payload["status"] == "missing_source_archive"
    assert payload["writes"] is False
    assert payload["download_url"].startswith("https://fdc.nal.usda.gov/")


def test_refresh_script_refuses_partial_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("USDA_FDC_API_KEY", "test-key")
    monkeypatch.setattr(refresh_nutrition_snapshot, "fetch_usda_food", lambda query, **_kwargs: None)

    snapshot = refresh_nutrition_snapshot.build_snapshot(["banana"])
    error = refresh_nutrition_snapshot.refresh_completeness_error(snapshot, ["banana"])

    assert snapshot["items"] == []
    assert error == {"status": "incomplete_refresh", "expected_items": 1, "items": 0, "writes": False}
