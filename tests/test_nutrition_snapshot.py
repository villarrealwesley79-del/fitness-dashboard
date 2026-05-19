from __future__ import annotations

import json
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


def test_snapshot_file_is_small_and_documents_license():
    path = Path("data/nutrition_snapshot.json")
    payload = json.loads(path.read_text())

    assert path.stat().st_size < 5 * 1024 * 1024
    assert "USDA" in payload["license"]
    assert "No Nutritionix-derived data" in payload["license"]
    assert payload["data_sources"][0]["name"] == "USDA FoodData Central"
    assert "blocked_pending_tos_review" in payload["nutritionix_redistribution_status"]
    assert len(payload["items"]) >= 5


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
