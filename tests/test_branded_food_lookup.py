from __future__ import annotations

from datetime import datetime, timedelta
import importlib

import branded_food_lookup


def test_normalize_plural_and_brand_typos():
    assert branded_food_lookup.normalize_meal_text("chipotole chicken burritos") == "chipotle chicken burrito"
    assert branded_food_lookup.normalize_meal_text("starbuks wraps") == "starbucks wrap"
    assert branded_food_lookup.normalize_meal_text("mcdonals sandwiches") == "mcdonalds sandwich"
    assert branded_food_lookup.normalize_meal_text("herb chicken") == "herb chicken"
    assert branded_food_lookup.normalize_meal_text("Torchy\u2019s democrat taco") == "torchys democrat taco"
    assert branded_food_lookup.normalize_meal_text("Rudy\u2019s brisket sandwich") == "rudys brisket sandwich"
    assert branded_food_lookup.normalize_meal_text("P. Terry\u2019s cheeseburger") == "p terrys cheeseburger"
    assert (
        branded_food_lookup.normalize_meal_text("Schlotzsky\u2019s original sandwich")
        == "schlotzskys original sandwich"
    )


def test_direct_lookup_gate_blocks_multi_item_generic_text():
    assert branded_food_lookup.should_attempt_direct_lookup("Chipotle chicken burrito") is True
    assert branded_food_lookup.should_attempt_direct_lookup("Chipotle") is False
    assert branded_food_lookup.should_attempt_direct_lookup("Starbucks") is False
    assert branded_food_lookup.should_attempt_direct_lookup("HEB California Roll") is True
    assert branded_food_lookup.should_attempt_direct_lookup("H-E-B Sushiya Spicy California Roll") is True
    assert branded_food_lookup.should_attempt_direct_lookup("HEB") is False
    assert branded_food_lookup.should_attempt_direct_lookup("banana") is True
    assert branded_food_lookup.should_attempt_direct_lookup("UK Walkers crisps") is True
    assert branded_food_lookup.should_attempt_direct_lookup("non-US packaged Smarties") is True
    assert branded_food_lookup.should_attempt_direct_lookup("non-US packaged") is False
    assert branded_food_lookup.should_attempt_direct_lookup("packaged product") is False
    assert branded_food_lookup.should_attempt_direct_lookup("UK") is False
    assert branded_food_lookup.should_attempt_direct_lookup("two eggs and toast") is False
    assert branded_food_lookup.should_attempt_direct_lookup("chicken with rice") is False
    assert branded_food_lookup.should_attempt_direct_lookup("half Chipotle chicken burrito") is False
    assert branded_food_lookup.should_attempt_direct_lookup("herb chicken") is False


def test_direct_lookup_gate_allows_regional_restaurant_menu_queries():
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller bacon and egg taco") is True
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller bacon & egg taco") is True
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller breakfast sandwich on biscuit") is True
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller brisket sandwich") is True
    assert branded_food_lookup.should_attempt_direct_lookup("whataburger patty melt") is True
    assert branded_food_lookup.should_attempt_direct_lookup("taco cabana bean and cheese taco") is True
    assert branded_food_lookup.should_attempt_direct_lookup("taco cabana bean & cheese taco") is True
    assert branded_food_lookup.should_attempt_direct_lookup("torchys democrat taco") is True
    assert branded_food_lookup.should_attempt_direct_lookup("rudys brisket sandwich") is True
    assert branded_food_lookup.should_attempt_direct_lookup("p terrys cheeseburger") is True
    assert branded_food_lookup.should_attempt_direct_lookup("schlotzskys original sandwich") is True
    assert branded_food_lookup.should_attempt_direct_lookup("Torchy\u2019s democrat taco") is True
    assert branded_food_lookup.should_attempt_direct_lookup("Rudy\u2019s brisket sandwich") is True
    assert branded_food_lookup.should_attempt_direct_lookup("P. Terry\u2019s cheeseburger") is True
    assert branded_food_lookup.should_attempt_direct_lookup("Schlotzsky\u2019s original sandwich") is True
    assert branded_food_lookup.should_attempt_direct_lookup("golden chick chicken tenders") is True
    assert branded_food_lookup.should_attempt_direct_lookup("la madeleine chicken caesar salad") is True


def test_direct_lookup_gate_requires_regional_restaurant_item_tokens():
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller") is False
    assert branded_food_lookup.should_attempt_direct_lookup("Bill Miller BBQ") is False
    assert branded_food_lookup.should_attempt_direct_lookup("Bill Miller Bar-B-Q") is False
    assert branded_food_lookup.should_attempt_direct_lookup("whataburger") is False
    assert branded_food_lookup.should_attempt_direct_lookup("Whataburger Restaurant") is False
    assert branded_food_lookup.should_attempt_direct_lookup("Whataburger Texas") is False
    assert branded_food_lookup.should_attempt_direct_lookup("half bill miller brisket sandwich") is False


def test_direct_lookup_gate_blocks_regional_restaurant_multi_item_queries():
    assert branded_food_lookup.should_attempt_direct_lookup("whataburger patty melt with fries") is False
    assert branded_food_lookup.should_attempt_direct_lookup("whataburger #1 with fries") is False
    assert branded_food_lookup.should_attempt_direct_lookup("whataburger burger and large fries") is False
    assert branded_food_lookup.should_attempt_direct_lookup("whataburger fries and tenders") is False
    assert branded_food_lookup.should_attempt_direct_lookup("rudys brisket plate") is False
    assert branded_food_lookup.should_attempt_direct_lookup("rudys brisket plate with sides") is False
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller bacon egg taco meal") is False
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller bacon and egg taco and fries") is False
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller bacon & egg taco and fries") is False
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller brisket sandwich and potato salad") is False
    assert branded_food_lookup.should_attempt_direct_lookup("torchys taco combo") is False
    assert branded_food_lookup.should_attempt_direct_lookup("taco cabana taco and burrito") is False
    assert branded_food_lookup.should_attempt_direct_lookup("schlotzskys turkey sandwich and chips") is False
    assert branded_food_lookup.should_attempt_direct_lookup("schlotzskys turkey sandwich and bag of chips") is False


def test_lookup_uses_open_food_facts_for_heb_private_label_when_other_sources_do_not_verify_brand(monkeypatch):
    saved = {}
    product = {
        "code": "0041220087457",
        "product_name": "California Roll",
        "brands": "H-E-B,Sushiya",
        "url": "https://world.openfoodfacts.org/product/0041220087457",
        "countries_tags": ["en:united-states"],
        "data_quality_tags": ["en:nutriments-completed"],
        "nutriments": {
            "energy-kcal_100g": 141.66666666667,
            "proteins_100g": 2.9166666666667,
            "carbohydrates_100g": 21.25,
            "fat_100g": 6.6666666666667,
            "sodium_100g": 0.45,
            "fiber_100g": 1.2,
        },
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "save_branded_lookup_cache",
        lambda normalized, source, response, **kwargs: saved.update(
            {"normalized": normalized, "source": source, "user_id": kwargs.get("user_id")}
        ),
    )
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a, **_kw: {
            "foods": [{
                "fdcId": 123,
                "description": "CALIFORNIA ROLL",
                "foodNutrients": [
                    {"nutrientName": "Energy", "value": 140},
                    {"nutrientName": "Protein", "value": 3},
                    {"nutrientName": "Carbohydrate, by difference", "value": 21},
                    {"nutrientName": "Total lipid (fat)", "value": 7},
                ],
            }]
        },
    )
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [product]},
    )

    estimate = branded_food_lookup.lookup(
        "HEB California Roll",
        source_priority=("usda_fdc", "open_food_facts"),
        user_id=42,
    )

    assert estimate["source"] == "open_food_facts"
    assert estimate["confidence"] >= 0.7
    assert estimate["external_food_id"] == "0041220087457"
    assert estimate["verified_source_url"] == "https://world.openfoodfacts.org/product/0041220087457"
    assert estimate["item_name"] == "H-E-B,Sushiya California Roll"
    assert estimate["brand_id"] == "h-e-b"
    assert saved == {"normalized": "heb california roll", "source": "open_food_facts", "user_id": 42}


def test_lookup_rejects_off_neighboring_heb_variant(monkeypatch):
    spicy_product = {
        "code": "spicy",
        "product_name": "Spicy California Roll",
        "brands": "H-E-B Sushiya",
        "url": "https://world.openfoodfacts.org/product/spicy",
        "countries_tags": ["en:united-states"],
        "data_quality_tags": ["en:nutriments-completed"],
        "nutriments": {
            "energy-kcal_100g": 140,
            "proteins_100g": 3,
            "carbohydrates_100g": 21,
            "fat_100g": 7,
        },
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [spicy_product]},
    )

    assert branded_food_lookup.lookup(
        "HEB California Roll",
        source_priority=("open_food_facts",),
    ) is None


def test_lookup_rejects_off_non_heb_brand_for_heb_private_label(monkeypatch):
    saved = []
    bad_product = {
        "code": "bad",
        "product_name": "California Roll",
        "brands": "Other Sushi Brand",
        "url": "https://world.openfoodfacts.org/product/bad",
        "countries_tags": ["en:united-states"],
        "data_quality_tags": ["en:nutriments-completed"],
        "nutriments": {
            "energy-kcal_100g": 140,
            "proteins_100g": 3,
            "carbohydrates_100g": 21,
            "fat_100g": 7,
        },
    }
    good_product = dict(bad_product)
    good_product.update({
        "code": "good",
        "product_name": "Spicy California Roll",
        "brands": "H-E-B Sushiya",
        "url": "https://world.openfoodfacts.org/product/good",
    })
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "save_branded_lookup_cache",
        lambda normalized, source, response, **kwargs: saved.append((normalized, source, response, kwargs)),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [bad_product, good_product]},
    )

    estimate = branded_food_lookup.lookup(
        "HEB Spicy California Roll",
        source_priority=("open_food_facts",),
    )

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "good"
    assert estimate["item_name"] == "H-E-B Sushiya Spicy California Roll"
    assert saved[0][1] == "open_food_facts"


def test_lookup_rejects_off_substring_brand_for_heb_private_label(monkeypatch):
    sheba_product = {
        "code": "sheba",
        "product_name": "California Roll",
        "brands": "Sheba",
        "url": "https://world.openfoodfacts.org/product/sheba",
        "countries_tags": ["en:united-states"],
        "data_quality_tags": ["en:nutriments-completed"],
        "nutriments": {
            "energy-kcal_100g": 140,
            "proteins_100g": 3,
            "carbohydrates_100g": 21,
            "fat_100g": 7,
        },
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [sheba_product]},
    )

    assert branded_food_lookup.lookup(
        "HEB Spicy California Roll",
        source_priority=("open_food_facts",),
    ) is None


def test_lookup_uses_official_heb_product_page_for_plain_california_roll(monkeypatch):
    saved = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "save_branded_lookup_cache",
        lambda normalized, source, response, **kwargs: saved.update(
            {"normalized": normalized, "source": source, "response": response, "user_id": kwargs.get("user_id")}
        ),
    )
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("OFF should not run for known HEB item")),
    )

    estimate = branded_food_lookup.lookup("HEB California Roll", user_id=42)

    assert estimate["source"] == "heb_product_page"
    assert estimate["item_name"] == "H-E-B Sushiya California Sushi Roll"
    assert estimate["portion_description"] == "10 pieces (224 g)"
    assert estimate["calories"] == 240
    assert estimate["protein_g"] == 7.0
    assert estimate["carbs_g"] == 50.0
    assert estimate["fat_g"] == 5.0
    assert estimate["sodium_mg"] == 930
    assert estimate["fiber_g"] == 3.0
    assert estimate["confidence"] >= 0.8
    assert estimate["verified_source_url"] == "https://www.heb.com/product-detail/h-e-b-sushiya-california-roll/2038218"
    assert saved["normalized"] == "heb california roll"
    assert saved["source"] == "heb_product_page"
    assert saved["user_id"] == 42


def test_official_heb_product_page_does_not_match_quantified_roll_input(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)

    assert branded_food_lookup.lookup(
        "2 HEB California Rolls",
        source_priority=("heb_product_page",),
    ) is None
    assert branded_food_lookup.lookup(
        "HEB California Roll 12 pieces",
        source_priority=("heb_product_page",),
    ) is None


def test_cache_hit_returns_local_cache_without_network(monkeypatch):
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
        "source": "usda_fdc",
        "external_food_id": "67890",
    }
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_branded_lookup_cache",
        lambda normalized, **_kw: {
            "normalized_text": normalized,
            "source": "usda_fdc",
            "response_json": cached_estimate,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["source"] == "local_cache"
    assert estimate["underlying_source"] == "usda_fdc"
    assert estimate["external_food_id"] == "67890"


def test_active_provider_priorities_exclude_retired_nutritionix():
    assert "nutritionix" not in branded_food_lookup.SOURCE_PRIORITY
    assert "nutritionix_barcode" not in branded_food_lookup.BARCODE_SOURCE_PRIORITY


def test_retired_nutritionix_text_cache_is_not_replayed(monkeypatch):
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_branded_lookup_cache",
        lambda *_a, **_kw: {
            "source": "nutritionix",
            "source_tier": "nutritionix",
            "response_json": {
                "item_name": "Retired cached meal",
                "calories": 500,
                "protein_g": 30,
                "carbs_g": 50,
                "fat_g": 20,
                "sodium_mg": 800,
                "fiber_g": 5,
                "confidence": 0.9,
                "ambiguous": False,
                "uncertainty_notes": [],
                "source": "nutritionix",
            },
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    assert branded_food_lookup._cache_lookup("retired cached meal", user_id=1) is None


def test_heb_private_label_bypasses_cache_without_verified_brand(monkeypatch):
    fetched_at = datetime.now().isoformat(timespec="seconds")
    stale_cached_estimate = {
        "item_name": "Generic California roll",
        "portion_description": "100 g",
        "meal_type": "lunch",
        "calories": 140,
        "protein_g": 3,
        "carbs_g": 21,
        "fat_g": 7,
        "sodium_mg": 300,
        "fiber_g": 1,
        "confidence": 0.72,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "open_food_facts",
        "external_food_id": "old",
    }
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_branded_lookup_cache",
        lambda normalized, **_kw: {
            "normalized_text": normalized,
            "source": "open_food_facts",
            "response_json": stale_cached_estimate,
            "fetched_at": fetched_at,
        },
    )
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)

    estimate = branded_food_lookup.lookup("HEB California Roll")

    assert estimate["source"] == "heb_product_page"
    assert estimate["external_food_id"] == "2038218"
    assert estimate["brand_id"] == "h-e-b"


def test_malformed_cache_row_is_treated_as_miss(monkeypatch):
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_branded_lookup_cache",
        lambda *_a, **_kw: {
            "source": "usda_fdc",
            "response_json": {"item_name": "Bad cached row", "source": "usda_fdc"},
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a, **_kw: {"foods": [{
            "fdcId": 173944,
            "description": "BANANAS,RAW",
            "foodNutrients": [
                {"nutrientName": "Energy", "value": 89},
                {"nutrientName": "Protein", "value": 1.1},
                {"nutrientName": "Carbohydrate, by difference", "value": 22.8},
                {"nutrientName": "Total lipid (fat)", "value": 0.3},
            ],
        }]},
    )

    estimate = branded_food_lookup.lookup("banana")

    assert estimate["source"] == "usda_fdc"
    assert estimate["item_name"] == "BANANAS,RAW"


def test_cache_write_failure_does_not_drop_valid_source_result(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)

    def fail_save(*_args, **_kwargs):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", fail_save)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a, **_kw: {"foods": [{
            "fdcId": 173944,
            "description": "BANANAS,RAW",
            "foodNutrients": [
                {"nutrientName": "Energy", "value": 89},
                {"nutrientName": "Protein", "value": 1.1},
                {"nutrientName": "Carbohydrate, by difference", "value": 22.8},
                {"nutrientName": "Total lipid (fat)", "value": 0.3},
            ],
        }]},
    )

    estimate = branded_food_lookup.lookup("banana")

    assert estimate["source"] == "usda_fdc"
    assert estimate["item_name"] == "BANANAS,RAW"








def test_malformed_external_results_return_none(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a: {
            "foods": [
                {
                    "fdcId": 12345,
                    "description": "BROKEN FOOD",
                    "foodNutrients": [
                        {"nutrientName": "Energy", "value": 99999},
                        {"nutrientName": "Protein", "value": 1},
                        {"nutrientName": "Carbohydrate, by difference", "value": 1},
                        {"nutrientName": "Total lipid (fat)", "value": 1},
                    ],
                }
            ]
        },
    )

    assert branded_food_lookup.lookup("broken food") is None


def test_stale_cache_falls_through_to_usda(monkeypatch):
    stale_at = (datetime.now() - timedelta(days=181)).isoformat(timespec="seconds")
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_branded_lookup_cache",
        lambda normalized, **_kw: {
            "normalized_text": normalized,
            "source": "usda_fdc",
            "response_json": {},
            "fetched_at": stale_at,
        },
    )
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
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
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert estimate["external_food_id"] == "173944"
    assert any("100 g reference portion" in note for note in estimate["uncertainty_notes"])


def test_usda_lookup_preserves_zero_calorie_energy(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a: {
            "foods": [
                {
                    "fdcId": 174158,
                    "description": "WATER, BOTTLED",
                    "foodNutrients": [
                        {"nutrientName": "Energy", "value": 0},
                        {"nutrientName": "Protein", "value": 0},
                        {"nutrientName": "Carbohydrate, by difference", "value": 0},
                        {"nutrientName": "Total lipid (fat)", "value": 0},
                    ],
                }
            ]
        },
    )

    estimate = branded_food_lookup.lookup("water", source_priority=("usda_fdc",))

    assert estimate["source"] == "usda_fdc"
    assert estimate["calories"] == 0
    assert estimate["external_food_id"] == "174158"


def test_usda_lookup_prefers_kcal_energy_over_kj(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a: {
            "foods": [
                {
                    "fdcId": 24680,
                    "description": "TEST FOOD",
                    "foodNutrients": [
                        {"nutrientName": "Energy", "unitName": "kJ", "value": 2000.0},
                        {"nutrientName": "Energy", "unitName": "KCAL", "value": 240},
                        {"nutrientName": "Protein", "value": 8},
                        {"nutrientName": "Carbohydrate, by difference", "value": 30},
                        {"nutrientName": "Total lipid (fat)", "value": 9},
                    ],
                }
            ]
        },
    )

    estimate = branded_food_lookup.lookup("test food", source_priority=("usda_fdc",))

    assert estimate["source"] == "usda_fdc"
    assert estimate["calories"] == 240
    assert estimate["protein_g"] == 8
    assert estimate["carbs_g"] == 30
    assert estimate["fat_g"] == 9
    assert estimate["external_food_id"] == "24680"


def test_usda_lookup_converts_kj_energy_to_kcal(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a: {
            "foods": [
                {
                    "fdcId": 13579,
                    "description": "KJ ONLY FOOD",
                    "foodNutrients": [
                        {"nutrientName": "Energy", "unitName": "kJ", "value": 836.8},
                        {"nutrientName": "Protein", "value": 10},
                        {"nutrientName": "Carbohydrate, by difference", "value": 22},
                        {"nutrientName": "Total lipid (fat)", "value": 8},
                    ],
                }
            ]
        },
    )

    estimate = branded_food_lookup.lookup("kj only food", source_priority=("usda_fdc",))

    assert estimate["source"] == "usda_fdc"
    assert estimate["calories"] == 200
    assert estimate["protein_g"] == 10
    assert estimate["carbs_g"] == 22
    assert estimate["fat_g"] == 8
    assert estimate["external_food_id"] == "13579"


def test_branded_usda_fallback_without_verified_brand_is_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a: {
            "foods": [
                {
                    "fdcId": 12345,
                    "description": "BURRITO WITH CHICKEN",
                    "foodNutrients": [
                        {"nutrientName": "Energy", "value": 250},
                        {"nutrientName": "Protein", "value": 12},
                        {"nutrientName": "Carbohydrate, by difference", "value": 32},
                        {"nutrientName": "Total lipid (fat)", "value": 8},
                    ],
                }
            ]
        },
    )

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["source"] == "usda_fdc"
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert estimate.get("brand_id") is None
    assert any("did not verify" in note for note in estimate["uncertainty_notes"])


def test_branded_usda_fallback_with_verified_brand_stays_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a: {
            "foods": [
                {
                    "fdcId": 67890,
                    "description": "CHICKEN BURRITO",
                    "brandOwner": "Chipotle Mexican Grill",
                    "foodNutrients": [
                        {"nutrientName": "Energy", "value": 1075},
                        {"nutrientName": "Protein", "value": 51},
                        {"nutrientName": "Carbohydrate, by difference", "value": 116},
                        {"nutrientName": "Total lipid (fat)", "value": 41},
                    ],
                }
            ]
        },
    )

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["source"] == "usda_fdc"
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert estimate["meal_type"] == "lunch"
    assert estimate["brand_id"] == "chipotle"
    assert any("100 g reference portion" in note for note in estimate["uncertainty_notes"])


def test_sanitize_with_provenance_keeps_safe_attribution_only():
    estimate = branded_food_lookup._sanitize_with_provenance({
        "item_name": "Imported biscuit",
        "portion_description": "100 g",
        "meal_type": "snack",
        "calories": 250,
        "protein_g": 6,
        "carbs_g": 40,
        "fat_g": 8,
        "sodium_mg": 400,
        "fiber_g": 2,
        "confidence": 0.75,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "open_food_facts",
        "off_attribution": {
            "source": "Open Food Facts",
            "url": "https://world.openfoodfacts.org/product/example",
            "raw": {"barcode": "secret"},
        },
    })

    assert estimate["off_attribution"] == {
        "source": "Open Food Facts",
        "url": "https://world.openfoodfacts.org/product/example",
    }


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
        "source": "usda_fdc",
    }
    monkeypatch.setattr(parser.branded_food_lookup, "lookup", lambda text, **_kw: branded_estimate)
    monkeypatch.setattr(parser, "_completion_json", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("LM must not run")))

    result = parser.parse_meal_text("Chipotle chicken burrito")

    assert result == {"estimate": branded_estimate, "fallback_used": False}


def test_parse_meal_text_directly_looks_up_non_us_packaged_food(monkeypatch):
    parser = importlib.import_module("meal_text_parser")
    captured = {}
    branded_estimate = {
        "item_name": "Walkers Crisps",
        "portion_description": "100 g",
        "meal_type": "snack",
        "calories": 500,
        "protein_g": 6,
        "carbs_g": 60,
        "fat_g": 25,
        "sodium_mg": 400,
        "fiber_g": 3,
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "open_food_facts",
        "verified_source_url": "https://world.openfoodfacts.org/product/500032837",
    }

    def fake_lookup(text, **kwargs):
        captured["text"] = text
        captured["user_id"] = kwargs.get("user_id")
        return dict(branded_estimate)

    monkeypatch.setattr(parser.branded_food_lookup, "lookup", fake_lookup)
    monkeypatch.setattr(parser, "_completion_json", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("LM must not run")))

    result = parser.parse_meal_text("UK Walkers crisps", user_id=42)

    assert captured == {"text": "UK Walkers crisps", "user_id": 42}
    assert result["fallback_used"] is False
    assert result["estimate"]["source"] == "open_food_facts"
    assert result["estimate"]["verified_source_url"] == "https://world.openfoodfacts.org/product/500032837"


def test_parse_meal_text_surfaces_no_branded_match_for_heb_private_label(monkeypatch):
    parser = importlib.import_module("meal_text_parser")
    monkeypatch.setattr(parser.branded_food_lookup, "lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        parser,
        "_completion_json",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("LM must not run after HEB branded miss")),
    )

    result = parser.parse_meal_text("HEB Mystery Roll")

    assert result["fallback_used"] is True
    assert result["estimate"]["source"] == "fallback_text_estimate"
    assert result["estimate"]["ambiguous"] is True
    assert result["estimate"]["confidence"] <= 0.45
    assert result["estimate"]["uncertainty_notes"][0] == (
        "Low confidence — no branded match found in USDA or Open Food Facts."
    )


def test_parse_meal_text_uses_parser_for_quantified_heb_branded_miss(monkeypatch):
    parser = importlib.import_module("meal_text_parser")
    monkeypatch.setattr(parser.branded_food_lookup, "lookup", lambda *_a, **_kw: None)

    def fake_completion(*_args, **_kwargs):
        return {
            "item_name": "H-E-B California Rolls",
            "portion_description": "2 rolls",
            "meal_type": "lunch",
            "calories": 480,
            "protein_g": 14,
            "carbs_g": 100,
            "fat_g": 10,
            "sodium_mg": 1860,
            "fiber_g": 6,
            "confidence": 0.82,
            "ambiguous": False,
            "uncertainty_notes": [],
        }

    monkeypatch.setattr(parser, "_completion_json", fake_completion)

    result = parser.parse_meal_text("2 HEB California Rolls")

    assert result["fallback_used"] is False
    assert result["estimate"]["source"] == "ai_text_estimate"
    assert result["estimate"]["portion_description"] == "2 rolls"
    assert result["estimate"]["calories"] == 480
    assert result["estimate"]["ambiguous"] is True
    assert result["estimate"]["confidence"] <= 0.45
    assert result["estimate"]["uncertainty_notes"][0] == (
        "Low confidence — no branded match found in USDA or Open Food Facts."
    )


def test_parse_meal_text_skips_direct_lookup_for_half_portions(monkeypatch):
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
        "source": "usda_fdc",
    }
    monkeypatch.setattr(
        parser.branded_food_lookup,
        "lookup",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("branded lookup must not run")),
    )
    monkeypatch.setattr(parser, "_completion_json", lambda *_a, **_kw: dict(branded_estimate, calories=537, portion_description="approx half portion"))

    result = parser.parse_meal_text("half Chipotle chicken burrito")

    assert result["fallback_used"] is False
    assert result["estimate"]["source"] == "ai_text_estimate"
    assert result["estimate"]["ambiguous"] is True
    assert result["estimate"]["confidence"] == 0.55
    assert result["estimate"]["portion_description"] == "approx half portion"
    assert result["estimate"]["calories"] == 537
    assert "confirm before it counts" in result["estimate"]["uncertainty_notes"][0]


def test_parse_meal_text_does_not_direct_lookup_multi_item_text(monkeypatch):
    parser = importlib.import_module("meal_text_parser")

    monkeypatch.setattr(
        parser.branded_food_lookup,
        "lookup",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("branded lookup must not run")),
    )
    monkeypatch.setattr(parser, "_completion_json", lambda *_a, **_kw: {
        "item_name": "Eggs and toast",
        "portion_description": "1 plate",
        "meal_type": "breakfast",
        "calories": 420,
        "protein_g": 24,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 600,
        "fiber_g": 4,
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": [],
    })

    result = parser.parse_meal_text("two eggs and toast")

    assert result["fallback_used"] is False
    assert result["estimate"]["source"] == "ai_text_estimate"


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

    data_store.save_branded_lookup_cache("banana", "usda_fdc", response, user_id=1)
    row = data_store.get_branded_lookup_cache("banana", user_id=1)

    assert row["user_id"] == 1
    assert row["source"] == "usda_fdc"
    assert row["source_tier"] == "usda_fdc"
    assert row["response_json"] == response
    assert row["fetched_at"]

    assert data_store.get_branded_lookup_cache("banana", user_id=2) is None
    data_store.save_branded_lookup_cache("banana", "usda_fdc", {**response, "item_name": "User 2 banana"}, user_id=2)
    assert data_store.get_branded_lookup_cache("banana", user_id=2)["response_json"]["item_name"] == "User 2 banana"

    data_store.delete_user_data(1)
    assert data_store.get_branded_lookup_cache("banana", user_id=1) is None
    assert data_store.get_branded_lookup_cache("banana", user_id=2)["response_json"]["item_name"] == "User 2 banana"


def test_text_fallback_cache_expires_before_top_tier_cache(monkeypatch):
    base = {
        "item_name": "Cached banana",
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
    }
    old = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    row = {
        "source": "usda_fdc",
        "source_tier": "usda_fdc",
        "response_json": {**base, "source": "usda_fdc"},
        "fetched_at": old,
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: row)

    assert branded_food_lookup._cache_lookup("banana", user_id=1) is None

    row["source"] = "heb_product_page"
    row["source_tier"] = "heb_product_page"
    row["response_json"] = {**base, "source": "heb_product_page"}
    estimate = branded_food_lookup._cache_lookup("banana", user_id=1)

    assert estimate["source"] == "local_cache"
    assert estimate["underlying_source"] == "heb_product_page"


def test_normalize_barcode_accepts_supported_digit_lengths():
    assert branded_food_lookup.normalize_barcode("0123-4567-8905") == "012345678905"
    assert branded_food_lookup.normalize_barcode(" 4006 3813 ") == "40063813"
    assert branded_food_lookup.normalize_barcode("12345678901234") == "12345678901234"
    assert branded_food_lookup.normalize_barcode("abc12345678") is None
    assert branded_food_lookup.normalize_barcode("1234567") is None


def test_lookup_barcode_does_not_route_to_heb_product_page(monkeypatch):
    assert "heb_product_page" in branded_food_lookup.SOURCE_PRIORITY
    assert "heb_product_page" not in branded_food_lookup.BARCODE_SOURCE_PRIORITY
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.heb_product_lookup,
        "lookup",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("H-E-B is text-path only")),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods_by_barcode", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.open_food_facts_client, "get_product_by_barcode", lambda *_a, **_kw: None)

    assert branded_food_lookup.lookup_barcode("012345678905") is None


def test_lookup_barcode_uses_local_cache_first(monkeypatch):
    cached = {
        "item_name": "Cached chips",
        "portion_description": "1 bag",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 3,
        "carbs_g": 25,
        "fat_g": 11,
        "sodium_mg": 280,
        "fiber_g": 2,
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "usda_fdc_barcode",
        "external_food_id": "cached-id",
    }
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_barcode_lookup_cache",
        lambda barcode, **kwargs: {
            "barcode": barcode,
            "source": cached["source"],
            "response_json": cached,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    estimate = branded_food_lookup.lookup_barcode("012345678905", user_id=7)

    assert estimate["source"] == "local_cache"
    assert estimate["underlying_source"] == "usda_fdc_barcode"
    assert estimate["item_name"] == "Cached chips"
    assert estimate["external_food_id"] == "cached-id"


def test_barcode_fallback_cache_expires_after_one_day(monkeypatch):
    base = {
        "item_name": "Cached chips",
        "portion_description": "1 bag",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 3,
        "carbs_g": 25,
        "fat_g": 11,
        "sodium_mg": 280,
        "fiber_g": 2,
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": [],
        "external_food_id": "500032837010",
    }
    old = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    row = {
        "barcode": "500032837010",
        "source": "open_food_facts_barcode",
        "source_tier": "open_food_facts_barcode",
        "response_json": {**base, "source": "open_food_facts_barcode"},
        "fetched_at": old,
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: row)

    assert branded_food_lookup._barcode_cache_lookup("500032837010", user_id=1) is None

def test_retired_nutritionix_barcode_cache_is_not_replayed(monkeypatch):
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_barcode_lookup_cache",
        lambda *_a, **_kw: {
            "source": "nutritionix_barcode",
            "source_tier": "nutritionix_barcode",
            "response_json": {
                "item_name": "Retired cached barcode",
                "calories": 210,
                "protein_g": 3,
                "carbs_g": 25,
                "fat_g": 11,
                "sodium_mg": 280,
                "fiber_g": 2,
                "confidence": 0.9,
                "ambiguous": False,
                "uncertainty_notes": [],
                "source": "nutritionix_barcode",
            },
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    assert branded_food_lookup._barcode_cache_lookup("500032837010", user_id=1) is None


def test_lookup_barcode_off_cache_replay_derives_verified_source_url(monkeypatch):
    cached = {
        "item_name": "Cached OFF food",
        "portion_description": "100 g",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 3,
        "carbs_g": 25,
        "fat_g": 11,
        "sodium_mg": 280,
        "fiber_g": 2,
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "open_food_facts_barcode",
        "external_food_id": "500032837010",
        "verified_source_url": None,
        "off_attribution": "Source: Open Food Facts (ODbL/DbCL data; product images CC BY-SA)",
    }
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_barcode_lookup_cache",
        lambda barcode, **kwargs: {
            "barcode": barcode,
            "source": "open_food_facts_barcode",
            "response_json": cached,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "get_product_by_barcode",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("provider should not run on cache hit")),
    )

    estimate = branded_food_lookup.lookup_barcode("500032837010", user_id=7)

    assert estimate["source"] == "local_cache"
    assert estimate["underlying_source"] == "open_food_facts_barcode"
    assert estimate["verified_source_url"] == "https://world.openfoodfacts.org/product/500032837010"


def test_lookup_barcode_off_cache_replay_falls_back_to_homepage_without_external_id(monkeypatch):
    cached = {
        "item_name": "Cached OFF food",
        "portion_description": "100 g",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 3,
        "carbs_g": 25,
        "fat_g": 11,
        "sodium_mg": 280,
        "fiber_g": 2,
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "local_cache",
        "external_food_id": None,
        "verified_source_url": None,
        "off_attribution": "Source: Open Food Facts (ODbL/DbCL data; product images CC BY-SA)",
    }
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_barcode_lookup_cache",
        lambda barcode, **kwargs: {
            "barcode": barcode,
            "source": "open_food_facts_barcode",
            "response_json": cached,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    estimate = branded_food_lookup.lookup_barcode("500032837010", user_id=7)

    assert estimate["source"] == "local_cache"
    assert estimate["underlying_source"] == "open_food_facts_barcode"
    assert estimate.get("external_food_id") is None
    assert estimate["verified_source_url"] == "https://world.openfoodfacts.org/"


def test_lookup_barcode_non_off_cache_replay_does_not_derive_verified_source_url(monkeypatch):
    cached = {
        "item_name": "Cached chips",
        "portion_description": "1 bag",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 3,
        "carbs_g": 25,
        "fat_g": 11,
        "sodium_mg": 280,
        "fiber_g": 2,
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "usda_fdc_barcode",
        "external_food_id": "cached-id",
        "verified_source_url": None,
    }
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "get_barcode_lookup_cache",
        lambda barcode, **kwargs: {
            "barcode": barcode,
            "source": cached["source"],
            "response_json": cached,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    estimate = branded_food_lookup.lookup_barcode("012345678905", user_id=7)
    assert estimate["underlying_source"] == "usda_fdc_barcode"
    assert estimate.get("verified_source_url") is None

    cached["source"] = "usda_fdc_barcode"
    estimate = branded_food_lookup.lookup_barcode("012345678905", user_id=7)
    assert estimate["underlying_source"] == "usda_fdc_barcode"
    assert estimate.get("verified_source_url") is None




def test_lookup_barcode_uses_usda_before_open_food_facts(monkeypatch):
    calls = []
    saved = {}
    food = {
        "fdcId": 987654,
        "description": "BRISKET BEEF JERKY",
        "brandOwner": "J&K",
        "gtinUpc": "801176022386",
        "foodNutrients": [
            {"nutrientName": "Energy", "unitName": "KCAL", "value": 286},
            {"nutrientName": "Protein", "value": 57.1},
            {"nutrientName": "Carbohydrate, by difference", "value": 10.7},
            {"nutrientName": "Total lipid (fat)", "value": 3.6},
            {"nutrientName": "Sodium, Na", "value": 1786},
        ],
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "save_barcode_lookup_cache",
        lambda barcode, source, response, **kwargs: saved.update({"barcode": barcode, "source": source}),
    )

    def usda_hit(*_a, **_kw):
        calls.append("usda")
        return {"foods": [food]}

    def off_should_not_run(*_a, **_kw):
        calls.append("off")
        raise AssertionError("OFF should not run after USDA barcode hit")

    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods_by_barcode", usda_hit)
    monkeypatch.setattr(branded_food_lookup.open_food_facts_client, "get_product_by_barcode", off_should_not_run)

    estimate = branded_food_lookup.lookup_barcode("801176022386")

    assert calls == ["usda"]
    assert estimate["source"] == "usda_fdc_barcode"
    assert estimate["item_name"] == "J&K BRISKET BEEF JERKY"
    assert estimate["calories"] == 286
    assert estimate["protein_g"] == 57.1
    assert estimate["confidence"] == 0.72
    assert estimate["ambiguous"] is True
    assert estimate["external_food_id"] == "987654"
    assert estimate["verified_source_url"].endswith("/987654/nutrients")
    assert saved == {"barcode": "801176022386", "source": "usda_fdc_barcode"}


def test_lookup_barcode_skips_incomplete_duplicate_usda_match(monkeypatch):
    incomplete = {
        "fdcId": 111,
        "description": "BRISKET BEEF JERKY INCOMPLETE",
        "brandOwner": "J&K",
        "gtinUpc": "801176022386",
        "foodNutrients": [
            {"nutrientName": "Energy", "unitName": "KCAL", "value": 286},
            {"nutrientName": "Carbohydrate, by difference", "value": 10.7},
            {"nutrientName": "Total lipid (fat)", "value": 3.6},
        ],
    }
    complete = {
        "fdcId": 222,
        "description": "BRISKET BEEF JERKY",
        "brandOwner": "J&K",
        "gtinUpc": "801176022386",
        "foodNutrients": [
            {"nutrientName": "Energy", "unitName": "KCAL", "value": 286},
            {"nutrientName": "Protein", "value": 57.1},
            {"nutrientName": "Carbohydrate, by difference", "value": 10.7},
            {"nutrientName": "Total lipid (fat)", "value": 3.6},
        ],
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods_by_barcode",
        lambda *_a, **_kw: {"foods": [incomplete, complete]},
    )
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "get_product_by_barcode",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("OFF should not run after valid USDA duplicate")),
    )

    estimate = branded_food_lookup.lookup_barcode("801176022386")

    assert estimate["source"] == "usda_fdc_barcode"
    assert estimate["external_food_id"] == "222"
    assert estimate["protein_g"] == 57.1


def test_lookup_barcode_falls_through_to_off_after_usda_miss(monkeypatch):
    calls = []
    product = {
        "code": "3017620422003",
        "product_name": "Nutella",
        "brands": "Ferrero",
        "url": "https://world.openfoodfacts.org/product/3017620422003",
        "countries_tags": ["en:france", "en:united-states"],
        "data_quality_tags": ["en:nutrition-completed"],
        "nutriments": {
            "energy-kcal_100g": 539,
            "proteins_100g": 6.3,
            "carbohydrates_100g": 57.5,
            "fat_100g": 30.9,
        },
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_barcode_lookup_cache", lambda *_a, **_kw: None)

    def usda_miss(*_a, **_kw):
        calls.append("usda")
        return {"foods": []}

    def off_hit(*_a, **_kw):
        calls.append("off")
        return product

    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods_by_barcode", usda_miss)
    monkeypatch.setattr(branded_food_lookup.open_food_facts_client, "get_product_by_barcode", off_hit)

    estimate = branded_food_lookup.lookup_barcode("3017620422003")

    assert calls == ["usda", "off"]
    assert estimate["source"] == "open_food_facts_barcode"
    assert estimate["calories"] == 539


def test_lookup_barcode_accepts_exact_off_product_with_nutrition_but_missing_complete_tag(monkeypatch):
    product = {
        "code": "0730742109487",
        "product_name": "Picositas Belts",
        "brands": "Alamo Candy Co.",
        "url": "https://world.openfoodfacts.org/product/0730742109487",
        "countries_tags": ["en:united-states"],
        "data_quality_tags": [
            "en:no-packaging-data",
            "en:serving-quantity-defined-but-quantity-undefined",
        ],
        "serving_size": "28 g",
        "serving_quantity": 28,
        "nutriments": {
            "energy-kcal_100g": 357.142857142857,
            "proteins_100g": 3.57142857142857,
            "carbohydrates_100g": 82.1428571428571,
            "fat_100g": 0,
            "sodium_100g": 1.10714285714286,
            "fiber_100g": 0,
            "energy-kcal_serving": 100,
            "proteins_serving": 1,
            "carbohydrates_serving": 23,
            "fat_serving": 0,
            "sodium_serving": 0.31,
            "fiber_serving": 0,
        },
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods_by_barcode", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.open_food_facts_client, "get_product_by_barcode", lambda *_a, **_kw: product)

    estimate = branded_food_lookup.lookup_barcode("730742109487")

    assert estimate["source"] == "open_food_facts_barcode"
    assert estimate["item_name"] == "Alamo Candy Co. Picositas Belts"
    assert estimate["portion_description"] == "28 g"
    assert estimate["calories"] == 100
    assert estimate["carbs_g"] == 23
    assert estimate["confidence"] == 0.82


def test_lookup_barcode_uses_open_food_facts_serving_macros(monkeypatch):
    saved = {}
    product = {
        "code": "500032837010",
        "product_name": "Barcode Crisps",
        "brands": "Walkers",
        "url": "https://world.openfoodfacts.org/product/500032837010",
        "countries_tags": ["en:united-kingdom"],
        "data_quality_tags": ["en:nutrition-data-complete"],
        "serving_size": "25 g",
        "serving_quantity": 25,
        "nutriments": {
            "energy-kcal_serving": 132,
            "proteins_serving": 1.5,
            "carbohydrates_serving": 13,
            "fat_serving": 8,
            "sodium_serving": 0.16,
            "fiber_serving": 1.2,
            "energy-kcal_100g": 528,
            "proteins_100g": 6,
            "carbohydrates_100g": 52,
            "fat_100g": 32,
        },
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "save_barcode_lookup_cache",
        lambda barcode, source, response, **kwargs: saved.update({"barcode": barcode, "source": source}),
    )
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "get_product_by_barcode",
        lambda *_a, **_kw: product,
    )

    estimate = branded_food_lookup.lookup_barcode("500032837010")

    assert estimate["source"] == "open_food_facts_barcode"
    assert estimate["portion_description"] == "25 g"
    assert estimate["portion_basis"] == "Open Food Facts package serving"
    assert estimate["confidence"] == 0.82
    assert estimate["calories"] == 132
    assert estimate["sodium_mg"] == 160
    assert saved == {"barcode": "500032837010", "source": "open_food_facts_barcode"}


ENERGY_MISMATCH_TAGS = [
    "en:nutrition-packaging-as-sold-100g-energy-value-in-kcal-may-not-match-value-computed-from-other-nutrients",
    "en:nutrition-packaging-as-sold-100g-energy-value-in-kcal-does-not-match-value-computed-from-other-nutrients",
]


def _off_barcode_product(
    *,
    code="3017620422003",
    product_name="Nutella",
    brands="Ferrero",
    tags=None,
    nutriments=None,
):
    return {
        "code": code,
        "product_name": product_name,
        "brands": brands,
        "url": f"https://world.openfoodfacts.org/product/{code}",
        "countries_tags": ["en:france", "en:united-states"],
        "data_quality_tags": tags or ["en:nutrition-completed"],
        "nutriments": nutriments or {
            "energy-kcal_100g": 539,
            "proteins_100g": 6.3,
            "carbohydrates_100g": 57.5,
            "fat_100g": 30.9,
        },
    }


def _mock_barcode_off(monkeypatch, product):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "get_product_by_barcode",
        lambda *_a, **_kw: product,
    )


def test_lookup_barcode_accepts_off_energy_mismatch_when_atwater_consistent(monkeypatch):
    product = _off_barcode_product(tags=["en:nutrition-completed", *ENERGY_MISMATCH_TAGS])
    _mock_barcode_off(monkeypatch, product)

    estimate = branded_food_lookup.lookup_barcode("3017620422003")

    assert estimate is not None
    assert estimate["source"] == "open_food_facts_barcode"
    assert estimate["item_name"] == "Ferrero Nutella"
    assert estimate["calories"] == 539
    assert estimate["protein_g"] == 6.3
    assert estimate["carbs_g"] == 57.5
    assert estimate["fat_g"] == 30.9
    assert estimate["portion_basis"] == "100 g Open Food Facts packaged-food reference"


def test_lookup_barcode_rejects_off_energy_mismatch_when_atwater_inconsistent(monkeypatch):
    product = _off_barcode_product(
        tags=["en:nutrition-completed", *ENERGY_MISMATCH_TAGS],
        nutriments={
            "energy-kcal_100g": 900,
            "proteins_100g": 5,
            "carbohydrates_100g": 5,
            "fat_100g": 5,
        },
    )
    _mock_barcode_off(monkeypatch, product)

    assert branded_food_lookup.lookup_barcode("3017620422003") is None


def test_lookup_barcode_rejects_off_energy_mismatch_with_missing_macro(monkeypatch):
    product = _off_barcode_product(
        tags=["en:nutrition-completed", *ENERGY_MISMATCH_TAGS],
        nutriments={
            "energy-kcal_100g": 539,
            "proteins_100g": 6.3,
            "carbohydrates_100g": 57.5,
        },
    )
    _mock_barcode_off(monkeypatch, product)

    assert branded_food_lookup.lookup_barcode("3017620422003") is None


def test_off_quality_ok_still_rejects_nutrition_data_error():
    product = _off_barcode_product(tags=["en:nutrition-completed", "en:nutrition-data-error"])

    assert branded_food_lookup._off_quality_ok(product) is False


def test_off_energy_consistent_with_macros():
    assert branded_food_lookup._off_energy_consistent_with_macros({
        "energy-kcal_100g": 539,
        "proteins_100g": 6.3,
        "carbohydrates_100g": 57.5,
        "fat_100g": 30.9,
    }) is True
    assert branded_food_lookup._off_energy_consistent_with_macros({
        "energy-kcal_100g": 1000,
        "proteins_100g": 5,
        "carbohydrates_100g": 5,
        "fat_100g": 6.6666666667,
    }) is False
    assert branded_food_lookup._off_energy_consistent_with_macros({
        "energy-kcal_100g": 125,
        "proteins_100g": 25,
        "carbohydrates_100g": 0,
        "fat_100g": 0,
    }) is True
    assert branded_food_lookup._off_energy_consistent_with_macros({
        "energy-kcal_100g": 126,
        "proteins_100g": 25,
        "carbohydrates_100g": 0,
        "fat_100g": 0,
    }) is False
    assert branded_food_lookup._off_energy_consistent_with_macros({
        "energy-kcal_100g": 539,
        "proteins_100g": "not-a-number",
        "carbohydrates_100g": 57.5,
        "fat_100g": 30.9,
    }) is False
    assert branded_food_lookup._off_energy_consistent_with_macros({
        "energy-kcal_100g": 0,
        "proteins_100g": 0,
        "carbohydrates_100g": 0,
        "fat_100g": 0,
    }) is False


def test_lookup_barcode_without_energy_mismatch_tag_still_verifies(monkeypatch):
    product = _off_barcode_product(
        code="5449000000996",
        product_name="Coca-Cola Original Taste",
        brands="Coca-Cola",
        tags=["en:nutrition-data-complete"],
        nutriments={
            "energy-kcal_100g": 42.1,
            "proteins_100g": 0,
            "carbohydrates_100g": 10.6,
            "fat_100g": 0,
            "energy-kcal_serving": 139,
            "proteins_serving": 0,
            "carbohydrates_serving": 35,
            "fat_serving": 0,
        },
    )
    product["serving_size"] = "330 ml"
    _mock_barcode_off(monkeypatch, product)

    estimate = branded_food_lookup.lookup_barcode("5449000000996")

    assert estimate is not None
    assert estimate["source"] == "open_food_facts_barcode"
    assert estimate["item_name"] == "Coca-Cola Coca-Cola Original Taste"
    assert estimate["calories"] == 139


def test_open_food_facts_barcode_serving_quantity_precedes_package_quantity(monkeypatch):
    product = {
        "code": "012345678905",
        "product_name": "Snack Pack",
        "brands": "Demo",
        "url": "https://world.openfoodfacts.org/product/012345678905",
        "countries_tags": ["en:united-states"],
        "data_quality_tags": ["en:nutrition-data-complete"],
        "serving_quantity": 30,
        "quantity": "300 g",
        "nutriments": {
            "energy-kcal_serving": 150,
            "proteins_serving": 4,
            "carbohydrates_serving": 20,
            "fat_serving": 6,
            "energy-kcal_100g": 500,
            "proteins_100g": 13.3,
            "carbohydrates_100g": 66.7,
            "fat_100g": 20,
        },
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "get_product_by_barcode",
        lambda *_a, **_kw: product,
    )

    estimate = branded_food_lookup.lookup_barcode("012345678905")

    assert estimate["portion_description"] == "30 g"
    assert estimate["calories"] == 150


def test_open_food_facts_barcode_serving_sodium_derives_from_100g(monkeypatch):
    product = {
        "code": "012345678905",
        "product_name": "Snack Pack",
        "brands": "Demo",
        "url": "https://world.openfoodfacts.org/product/012345678905",
        "countries_tags": ["en:united-states"],
        "data_quality_tags": ["en:nutrition-data-complete"],
        "serving_size": "25 g",
        "nutriments": {
            "energy-kcal_serving": 150,
            "proteins_serving": 4,
            "carbohydrates_serving": 20,
            "fat_serving": 6,
            "energy-kcal_100g": 600,
            "proteins_100g": 16,
            "carbohydrates_100g": 80,
            "fat_100g": 24,
            "sodium_100g": 0.4,
        },
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "get_product_by_barcode",
        lambda *_a, **_kw: product,
    )

    estimate = branded_food_lookup.lookup_barcode("012345678905")

    assert estimate["portion_description"] == "25 g"
    assert estimate["portion_basis"] == "Open Food Facts package serving"
    assert estimate["calories"] == 150
    assert estimate["sodium_mg"] == 100


def test_open_food_facts_barcode_does_not_label_serving_macros_as_package_quantity(monkeypatch):
    product = {
        "code": "012345678905",
        "product_name": "Snack Pack",
        "brands": "Demo",
        "url": "https://world.openfoodfacts.org/product/012345678905",
        "countries_tags": ["en:united-states"],
        "data_quality_tags": ["en:nutrition-data-complete"],
        "quantity": "300 g",
        "nutriments": {
            "energy-kcal_serving": 150,
            "proteins_serving": 4,
            "carbohydrates_serving": 20,
            "fat_serving": 6,
            "energy-kcal_100g": 500,
            "proteins_100g": 13.3,
            "carbohydrates_100g": 66.7,
            "fat_100g": 20,
        },
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "get_product_by_barcode",
        lambda *_a, **_kw: product,
    )

    estimate = branded_food_lookup.lookup_barcode("012345678905")

    assert estimate["portion_description"] == "1 serving"
    assert estimate["calories"] == 150
