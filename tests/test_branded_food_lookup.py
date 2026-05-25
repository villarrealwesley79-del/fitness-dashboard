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
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller breakfast sandwich on biscuit") is True
    assert branded_food_lookup.should_attempt_direct_lookup("bill miller brisket sandwich") is True
    assert branded_food_lookup.should_attempt_direct_lookup("whataburger patty melt") is True
    assert branded_food_lookup.should_attempt_direct_lookup("taco cabana bean and cheese taco") is True
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
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda *_a, **_kw: _nutritionix_payload(food_name="California roll", brand_name=None),
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
        source_priority=("nutritionix", "usda_fdc", "open_food_facts"),
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
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a, **_kw: None)
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
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a, **_kw: None)
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
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a, **_kw: None)
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
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("Nutritionix should not run for known HEB item")),
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


def test_lookup_uses_nutritionix_and_records_provenance(monkeypatch):
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
    assert estimate["meal_type"] == "lunch"
    assert estimate["external_food_id"] == "chipotle-burrito"
    assert estimate["verified_source_url"] == "https://www.nutritionix.com/"
    assert estimate["portion_basis"] == "1 burrito (440 g)"
    assert saved["normalized"] == "chipotle chicken burrito"
    assert saved["source"] == "nutritionix"
    assert saved["user_id"] == 1


def test_lookup_attempts_provider_for_regional_restaurant_query(monkeypatch):
    captured = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: captured.update({"query": query}) or _nutritionix_payload(
            food_name="brisket sandwich",
            brand_name="Bill Miller Bar-B-Q",
            serving_unit="sandwich",
            nf_calories=610,
            nf_protein=32,
            nf_total_carbohydrate=58,
            nf_total_fat=27,
            nf_sodium=1450,
            nf_dietary_fiber=3,
            nix_item_id="bill-miller-brisket-sandwich",
        ),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    estimate = branded_food_lookup.lookup("bill miller brisket sandwich")

    assert captured["query"] == "bill miller brisket sandwich"
    assert estimate["source"] == "nutritionix"
    assert estimate["item_name"] == "Bill Miller Bar-B-Q brisket sandwich"
    assert estimate["calories"] == 610
    assert estimate["confidence"] == 0.85


def test_regional_restaurant_lookup_marks_wrong_chain_for_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(
            food_name="brisket sandwich",
            brand_name="Other BBQ",
            serving_unit="sandwich",
            nf_calories=610,
            nf_protein=32,
            nf_total_carbohydrate=58,
            nf_total_fat=27,
            nf_sodium=1450,
            nf_dietary_fiber=3,
        ),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    estimate = branded_food_lookup.lookup("bill miller brisket sandwich")

    assert estimate["confidence"] == 0.55
    assert any(
        "Nutritionix did not verify the requested brand" in note
        for note in estimate["uncertainty_notes"]
    )


def test_lookup_infers_breakfast_meal_type_for_external_result(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(
            food_name="oatmeal",
            brand_name=None,
            serving_unit="bowl",
            nf_calories=210,
            nf_protein=6,
            nf_total_carbohydrate=36,
            nf_total_fat=4,
            nf_sodium=120,
            nf_dietary_fiber=5,
        ),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    estimate = branded_food_lookup.lookup("oatmeal")

    assert estimate["source"] == "nutritionix"
    assert estimate["meal_type"] == "breakfast"


def test_lookup_uses_brand_hint_in_source_query(monkeypatch):
    captured = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: captured.update({"query": query}) or _nutritionix_payload(),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    branded_food_lookup.lookup("foil wrapped burrito", brand_hint="chipotle")

    assert captured["query"] == "chipotle foil wrapped burrito"


def test_lookup_honors_source_priority_order(monkeypatch):
    calls = []
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: calls.append("nutritionix") or _nutritionix_payload(),
    )
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda query: calls.append("usda_fdc") or {
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

    estimate = branded_food_lookup.lookup("banana", source_priority=("usda_fdc", "nutritionix"))

    assert estimate["source"] == "usda_fdc"
    assert calls == ["usda_fdc"]


def test_lookup_does_not_duplicate_existing_brand_hint(monkeypatch):
    captured = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: captured.update({"query": query}) or _nutritionix_payload(),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    branded_food_lookup.lookup("Chipotle chicken burrito", brand_hint="chipotle")

    assert captured["query"] == "Chipotle chicken burrito"


def test_customizable_item_without_modifier_goes_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
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


def test_nutritionix_multi_item_response_sums_all_foods(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: {
            "foods": [
                {
                    "food_name": "burger",
                    "brand_name": "McDonalds",
                    "serving_qty": 1,
                    "serving_unit": "burger",
                    "nf_calories": 500,
                    "nf_protein": 25,
                    "nf_total_carbohydrate": 40,
                    "nf_total_fat": 25,
                    "nf_sodium": 900,
                    "nf_dietary_fiber": 2,
                    "nix_item_id": "burger-id",
                },
                {
                    "food_name": "fries",
                    "brand_name": "McDonalds",
                    "serving_qty": 1,
                    "serving_unit": "serving",
                    "nf_calories": 300,
                    "nf_protein": 4,
                    "nf_total_carbohydrate": 45,
                    "nf_total_fat": 15,
                    "nf_sodium": 250,
                    "nf_dietary_fiber": 4,
                    "nix_item_id": "fries-id",
                },
            ]
        },
    )

    estimate = branded_food_lookup.lookup("McDonalds burger fries")

    assert estimate["item_name"] == "McDonalds burger, fries"
    assert estimate["calories"] == 800
    assert estimate["protein_g"] == 29.0
    assert estimate["carbs_g"] == 85.0
    assert estimate["fat_g"] == 40.0
    assert estimate["sodium_mg"] == 1150
    assert estimate["fiber_g"] == 6.0
    assert estimate["external_food_id"] == "burger-id,fries-id"


def test_requested_brand_without_source_brand_is_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(brand_name=None),
    )

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["item_name"] == "chicken burrito"
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert estimate.get("brand_id") is None
    assert any("did not verify" in note for note in estimate["uncertainty_notes"])


def test_brand_aliases_verify_against_provider_brand(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(
            food_name="sandwich",
            brand_name="Chick-fil-A",
            serving_unit="sandwich",
            nf_calories=420,
            nf_protein=29,
            nf_total_carbohydrate=41,
            nf_total_fat=18,
            nf_sodium=1460,
            nf_dietary_fiber=1,
            nix_item_id="chickfila-sandwich",
        ),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    estimate = branded_food_lookup.lookup("Chickfila sandwich")

    assert estimate["item_name"] == "Chick-fil-A sandwich"
    assert estimate["brand_id"] == "chick-fil-a"
    assert estimate["confidence"] == 0.85
    assert estimate["ambiguous"] is False
    assert not any("did not verify" in note for note in estimate["uncertainty_notes"])


def test_requested_item_category_mismatch_is_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(food_name="chicken bowl"),
    )

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["item_name"] == "Chipotle chicken bowl"
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert any("different item category" in note for note in estimate["uncertainty_notes"])


def test_requested_burrito_rejects_burrito_bowl_hit(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(food_name="chicken burrito bowl"),
    )

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["item_name"] == "Chipotle chicken burrito bowl"
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert any("different item category" in note for note in estimate["uncertainty_notes"])


def test_requested_item_category_missing_is_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(food_name="chicken"),
    )

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["item_name"] == "Chipotle chicken"
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert any("different item category" in note for note in estimate["uncertainty_notes"])


def test_requested_quesadilla_category_mismatch_is_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(food_name="chicken burrito"),
    )

    estimate = branded_food_lookup.lookup("Chipotle chicken quesadilla")

    assert estimate["item_name"] == "Chipotle chicken burrito"
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert any("different item category" in note for note in estimate["uncertainty_notes"])


def test_mixed_brand_nutritionix_multi_item_is_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: {
            "foods": [
                {
                    "food_name": "burger",
                    "brand_name": "McDonalds",
                    "serving_qty": 1,
                    "serving_unit": "burger",
                    "nf_calories": 500,
                    "nf_protein": 25,
                    "nf_total_carbohydrate": 40,
                    "nf_total_fat": 25,
                    "nf_sodium": 900,
                    "nf_dietary_fiber": 2,
                    "nix_item_id": "burger-id",
                },
                {
                    "food_name": "fries",
                    "brand_name": "Generic Diner",
                    "serving_qty": 1,
                    "serving_unit": "serving",
                    "nf_calories": 300,
                    "nf_protein": 4,
                    "nf_total_carbohydrate": 45,
                    "nf_total_fat": 15,
                    "nf_sodium": 250,
                    "nf_dietary_fiber": 4,
                    "nix_item_id": "fries-id",
                },
            ]
        },
    )

    estimate = branded_food_lookup.lookup("McDonalds burger fries")

    assert estimate["item_name"] == "burger, fries"
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert estimate.get("brand_id") is None
    assert any("did not verify" in note for note in estimate["uncertainty_notes"])


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
        lambda normalized, **_kw: {
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
            "source": "nutritionix",
            "response_json": {"item_name": "Bad cached row", "source": "nutritionix"},
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["source"] == "nutritionix"
    assert estimate["item_name"] == "Chipotle chicken burrito"


def test_cache_write_failure_does_not_drop_valid_source_result(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)

    def fail_save(*_args, **_kwargs):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", fail_save)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(),
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    estimate = branded_food_lookup.lookup("Chipotle chicken burrito")

    assert estimate["source"] == "nutritionix"
    assert estimate["item_name"] == "Chipotle chicken burrito"


def test_malformed_nutritionix_result_falls_through_to_usda(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(nf_calories=99999),
    )
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


def test_incomplete_nutritionix_result_is_a_miss(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: {"foods": [{"food_name": "mystery bar", "brand_name": "Starbucks"}]},
    )
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    assert branded_food_lookup.lookup("Starbucks mystery bar") is None


def test_incomplete_nutritionix_result_falls_through_to_usda(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(nf_protein=None),
    )
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


def test_malformed_external_results_return_none(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda query: _nutritionix_payload(nf_calories=99999),
    )
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
    assert estimate["confidence"] == 0.55
    assert estimate["ambiguous"] is True
    assert estimate["external_food_id"] == "173944"
    assert any("100 g reference portion" in note for note in estimate["uncertainty_notes"])


def test_usda_lookup_preserves_zero_calorie_energy(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
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


def test_branded_usda_fallback_without_verified_brand_is_pending_review(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
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
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
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
        "source": "nutritionix",
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
        "Low confidence — no branded match found in Nutritionix, USDA, or Open Food Facts."
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
        "Low confidence — no branded match found in Nutritionix, USDA, or Open Food Facts."
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
        "source": "nutritionix",
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
    assert row["response_json"] == response
    assert row["fetched_at"]

    assert data_store.get_branded_lookup_cache("banana", user_id=2) is None
    data_store.save_branded_lookup_cache("banana", "usda_fdc", {**response, "item_name": "User 2 banana"}, user_id=2)
    assert data_store.get_branded_lookup_cache("banana", user_id=2)["response_json"]["item_name"] == "User 2 banana"

    data_store.delete_user_data(1)
    assert data_store.get_branded_lookup_cache("banana", user_id=1) is None
    assert data_store.get_branded_lookup_cache("banana", user_id=2)["response_json"]["item_name"] == "User 2 banana"
