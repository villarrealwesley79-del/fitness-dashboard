from __future__ import annotations

from datetime import datetime, timedelta
import importlib

import branded_food_lookup


def _nutritionix_payload(food_name="chicken burrito", **overrides):
    food = {
        "food_name": food_name,
        "brand_name": "Chipotle",
        "serving_qty": 1,
        "serving_unit": "burrito",
        "serving_weight_grams": 440,
        "nf_calories": 1075,
        "nf_protein": 51,
        "nf_total_carbohydrate": 116,
        "nf_total_fat": 41,
        "nf_sodium": 2310,
        "nf_dietary_fiber": 13,
        "nix_item_id": "chipotle-burrito",
    }
    food.update(overrides)
    return {"foods": [food]}


def test_normalize_plural_and_brand_typos():
    assert branded_food_lookup.normalize_meal_text("chipotole chicken burritos") == "chipotle chicken burrito"
    assert branded_food_lookup.normalize_meal_text("starbuks wraps") == "starbucks wrap"
    assert branded_food_lookup.normalize_meal_text("mcdonals sandwiches") == "mcdonalds sandwich"


def test_lookup_uses_nutritionix_and_records_provenance(monkeypatch):
    saved = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "save_branded_lookup_cache",
        lambda normalized, source, response: saved.update(
            {"normalized": normalized, "source": source, "response": response}
        ),
    )
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    estimate = branded_food_lookup.lookup("Chipotle chicken burritos")

    assert estimate["source"] == "nutritionix"
    assert estimate["item_name"] == "Chipotle chicken burrito"
    assert estimate["calories"] == 1075
    assert estimate["protein_g"] == 51.0
    assert estimate["external_food_id"] == "chipotle-burrito"
    assert estimate["verified_source_url"] == "https://www.nutritionix.com/"
    assert estimate["portion_basis"] == "1 burrito (440 g)"
    assert saved["normalized"] == "chipotle chicken burrito"
    assert saved["source"] == "nutritionix"


def test_customizable_item_without_modifier_goes_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(food_name="burrito"),
    )

    estimate = branded_food_lookup.lookup("Chipotle burrito")

    assert estimate["source"] == "nutritionix"
    assert estimate["ambiguous"] is True
    assert estimate["confidence"] == 0.55
    assert "protein" in estimate["uncertainty_notes"][0].lower()


def test_cache_hit_returns_local_cache_without_network(monkeypatch):
    fetched_at = datetime.now().isoformat(timespec="seconds")
    cached = _nutritionix_payload()["foods"][0]
    cached_estimate = {
        "item_name": "Chipotle chicken burrito",
        "portion_description": "1 burrito",
        "meal_type": "lunch",
        "calories": 1075,
        "protein_g": 51,
        "carbs_g": 116,
        "fat_g": 41,
        "sodium_mg": 2310,
        "fiber_g": 13,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "nutritionix",
        "external_food_id": cached["nix_item_id"],
    }
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_branded_lookup_cache",
        lambda normalized: {
            "normalized_text": normalized,
            "source": "nutritionix",
            "response_json": cached_estimate,
            "fetched_at": fetched_at,
        },
    )
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network tier must not run")),
    )

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["source"] == "local_cache"
    assert estimate["underlying_source"] == "nutritionix"
    assert estimate["external_food_id"] == "chipotle-burrito"


def test_stale_cache_falls_through_to_usda(monkeypatch):
    stale_at = (datetime.now() - timedelta(days=181)).isoformat(timespec="seconds")
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_branded_lookup_cache",
        lambda normalized: {
            "normalized_text": normalized,
            "source": "nutritionix",
            "response_json": {},
            "fetched_at": stale_at,
        },
    )
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a: {
            "foods": [
                {
                    "fdcId": 173944,
                    "description": "BANANAS,RAW",
                    "foodNutrients": [
                        {"nutrientName": "Energy", "value": 89},
                        {"nutrientName": "Protein", "value": 1.1},
                        {"nutrientName": "Carbohydrate, by difference", "value": 22.8},
                        {"nutrientName": "Total lipid (fat)", "value": 0.3},
                    ],
                }
            ]
        },
    )

    estimate = branded_food_lookup.lookup("banana")

    assert estimate["source"] == "usda_fdc"
    assert estimate["item_name"] == "BANANAS,RAW"
    assert estimate["external_food_id"] == "173944"


def test_parse_meal_text_uses_branded_lookup_before_lm(monkeypatch):
    parser = importlib.import_module("meal_text_parser")
    branded_estimate = {
        "item_name": "Chipotle chicken burrito",
        "portion_description": "1 burrito",
        "meal_type": "lunch",
        "calories": 1075,
        "protein_g": 51,
        "carbs_g": 116,
        "fat_g": 41,
        "sodium_mg": 2310,
        "fiber_g": 13,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "nutritionix",
    }
    monkeypatch.setattr(parser.branded_food_lookup, "lookup", lambda text: branded_estimate)
    monkeypatch.setattr(parser, "_completion_json", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("LM must not run")))

    result = parser.parse_meal_text("Chipotle chicken burrito")

    assert result == {"estimate": branded_estimate, "fallback_used": False}


def test_data_store_cache_round_trip(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    response = {
        "item_name": "Banana",
        "portion_description": "100 g",
        "meal_type": "snack",
        "calories": 89,
        "protein_g": 1.1,
        "carbs_g": 22.8,
        "fat_g": 0.3,
        "sodium_mg": 1,
        "fiber_g": 2.6,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "usda_fdc",
    }

    data_store.save_branded_lookup_cache("banana", "usda_fdc", response)
    row = data_store.get_branded_lookup_cache("banana")

    assert row["source"] == "usda_fdc"
    assert row["response_json"] == response
    assert row["fetched_at"]
