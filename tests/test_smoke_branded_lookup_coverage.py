from __future__ import annotations

import scripts.smoke_branded_lookup_coverage as smoke


CONFIGURED_ENV = {
    "NUTRITIONIX_APP_ID": "test-app-id",
    "NUTRITIONIX_APP_KEY": "test-app-key",
    "USDA_FDC_API_KEY": "test-usda-key",
}


def _nutritionix_payload(food_name="brisket sandwich", **overrides):
    food = {
        "food_name": food_name,
        "brand_name": "Bill Miller BBQ",
        "serving_qty": 1,
        "serving_unit": "sandwich",
        "serving_weight_grams": 250,
        "nf_calories": 610,
        "nf_protein": 32,
        "nf_total_carbohydrate": 58,
        "nf_total_fat": 25,
        "nf_sodium": 1450,
        "nf_dietary_fiber": 3,
        "nix_item_id": "bill-miller-brisket-sandwich",
    }
    food.update(overrides)
    return {"foods": [food]}


def _usda_payload(**overrides):
    food = {
        "fdcId": 123456,
        "description": "BRISKET SANDWICH",
        "foodNutrients": [
            {"nutrientName": "Energy", "value": 242},
            {"nutrientName": "Protein", "value": 12.8},
            {"nutrientName": "Carbohydrate, by difference", "value": 24.0},
            {"nutrientName": "Total lipid (fat)", "value": 10.0},
        ],
    }
    food.update(overrides)
    return {"foods": [food]}


def _query(category="required", expected_chain="bill miller"):
    return (smoke.CoverageQuery("bill miller brisket sandwich", category, "test query", expected_chain),)


def _direct_lookup_query(category="required"):
    return (smoke.CoverageQuery("chipotle chicken burrito", category, "test query"),)


def test_coverage_records_nutritionix_hit(monkeypatch):
    saved = []
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.data_store,
        "save_branded_lookup_cache",
        lambda *args, **_kwargs: saved.append(args),
    )
    monkeypatch.setattr(
        smoke.branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda _query: _nutritionix_payload(),
    )
    monkeypatch.setattr(smoke.branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_query(), env=CONFIGURED_ENV)[0]

    assert result.category == "required"
    assert result.outcome == "nutritionix"
    assert result.matched_item == "Bill Miller BBQ brisket sandwich"
    assert result.calories == "610"
    assert result.source_url == "https://www.nutritionix.com/"
    assert result.confidence == "0.85"
    assert "production direct-lookup gate would block this query" in result.notes
    assert saved == []


def test_wrong_chain_hit_continues_to_lower_priority_provider(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda _query: _nutritionix_payload(brand_name="Wrong Chain"),
    )
    monkeypatch.setattr(
        smoke.branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a, **_kw: _usda_payload(brandOwner="Bill Miller BBQ"),
    )
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_query(), env=CONFIGURED_ENV)[0]

    assert result.outcome == "usda_fdc"
    assert result.matched_item == "BRISKET SANDWICH"
    assert "nutritionix returned Wrong Chain brisket sandwich without verifying expected chain bill miller" in result.notes


def test_coverage_records_usda_fallback_when_nutritionix_misses(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(smoke.branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: _usda_payload())
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_direct_lookup_query(), env=CONFIGURED_ENV)[0]

    assert result.outcome == "usda_fdc"
    assert result.matched_item == "BRISKET SANDWICH"
    assert result.calories == "242"
    assert result.source_url == "https://fdc.nal.usda.gov/fdc-app.html#/food-details/123456/nutrients"
    assert "USDA FDC uses a 100 g reference portion" in result.notes


def test_coverage_accepts_usda_brand_metadata_for_expected_chain(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a, **_kw: _usda_payload(brandOwner="Bill Miller BBQ"),
    )
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_query(), env=CONFIGURED_ENV)[0]

    assert result.outcome == "usda_fdc"
    assert result.matched_item == "BRISKET SANDWICH"
    assert "production direct-lookup gate would block this query" in result.notes


def test_coverage_records_miss_without_lm_studio_or_cache_write(monkeypatch):
    saved = []
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.data_store,
        "save_branded_lookup_cache",
        lambda *args, **_kwargs: saved.append(args),
    )
    monkeypatch.setattr(smoke.branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(smoke.branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_direct_lookup_query(), env=CONFIGURED_ENV)[0]

    assert result.outcome == "miss/fallback gap"
    assert result.matched_item == ""
    assert "nutritionix, usda_fdc, open_food_facts reached with no verified match" in result.notes
    assert saved == []


def test_missing_credentials_report_provider_unavailable(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda *_a: (_ for _ in ()).throw(AssertionError("credential-gated provider must not run")),
    )
    monkeypatch.setattr(
        smoke.branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("credential-gated provider must not run")),
    )
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_query(), env={})[0]

    assert result.outcome == "provider unavailable"
    assert "nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY" in result.notes
    assert "usda_fdc skipped: missing USDA_FDC_API_KEY" in result.notes


def test_coverage_can_respect_production_direct_lookup_gate(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda *_a: (_ for _ in ()).throw(AssertionError("provider lookup must not run")),
    )

    result = smoke.run_coverage(_query(), respect_direct_lookup_gate=True)[0]

    assert result.outcome == "miss/fallback gap"
    assert "production direct-lookup gate blocked provider lookup" in result.notes


def test_proxy_rows_remain_labeled_as_proxy(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.nutritionix_client,
        "natural_nutrients",
        lambda _query: _nutritionix_payload(food_name="patty melt", brand_name="Whataburger"),
    )
    monkeypatch.setattr(smoke.branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_query(category="proxy", expected_chain=""), env=CONFIGURED_ENV)[0]

    assert result.category == "proxy"
    assert "proxy" in smoke.markdown_table([result])


def test_provider_status_does_not_expose_secret_values():
    status = smoke.provider_status(
        env={
            "NUTRITIONIX_APP_ID": "real-app-id",
            "NUTRITIONIX_APP_KEY": "real-key",
            "USDA_FDC_API_KEY": "real-usda-key",
        }
    )

    assert status["cache"] == "skipped"
    assert status["direct_lookup_gate"] == "bypassed for coverage matrix; production gate recorded per row"
    assert status["nutritionix"] == "configured"
    assert status["usda_fdc"] == "configured"
    assert "real" not in " ".join(status.values())


def test_provider_status_respects_explicit_empty_env():
    status = smoke.provider_status(env={})

    assert status["nutritionix"] == "missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY"
    assert status["usda_fdc"] == "missing USDA_FDC_API_KEY"


def test_provider_status_marks_respect_gate_mode():
    status = smoke.provider_status(respect_direct_lookup_gate=True)

    assert status["direct_lookup_gate"] == "production gate respected"
