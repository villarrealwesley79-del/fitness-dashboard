from __future__ import annotations

import scripts.smoke_branded_lookup_coverage as smoke


CONFIGURED_ENV = {
    "USDA_FDC_API_KEY": "test-usda-key",
}


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


def test_coverage_records_h_e_b_hit(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.heb_product_lookup,
        "lookup",
        lambda _query: {
            "item_name": "H-E-B Sushiya California Sushi Roll",
            "calories": 240,
            "source": "heb_product_page",
            "confidence": 0.88,
            "verified_source_url": "https://www.heb.com/product-detail/h-e-b-sushiya-california-roll/2038218",
        },
    )
    monkeypatch.setattr(smoke.branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(
        (smoke.CoverageQuery("HEB California Roll", "required", "test query"),),
        env={},
    )[0]

    assert result.category == "required"
    assert result.outcome == "heb_product_page"
    assert result.matched_item == "H-E-B Sushiya California Sushi Roll"
    assert result.calories == "240"
    assert result.source_url == "https://www.heb.com/product-detail/h-e-b-sushiya-california-roll/2038218"
    assert result.confidence == "0.88"
    assert "production direct-lookup gate would block this query" not in result.notes


def test_coverage_uses_active_provider_chain(monkeypatch):
    calls = []
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke, "_unavailable_sources", lambda _env: {})

    def record_lookup(_query, *, source_priority, user_id):
        calls.append(source_priority)
        return None

    monkeypatch.setattr(smoke.branded_food_lookup, "lookup", record_lookup)

    result = smoke.run_coverage(_direct_lookup_query(), env=CONFIGURED_ENV)[0]

    assert result.outcome == "miss/fallback gap"
    assert calls == [("heb_product_page",), ("usda_fdc",), ("open_food_facts",)]


def test_coverage_records_open_food_facts_fallback_after_usda_misses(monkeypatch):
    product = {
        "code": "123",
        "product_name": "Chocolate Cookies",
        "brands": "Acme",
        "url": "https://world.openfoodfacts.org/product/123",
        "countries_tags": ["en:united-states"],
        "data_quality_tags": ["en:nutriments-completed"],
        "nutriments": {
            "energy-kcal_100g": 450,
            "proteins_100g": 5,
            "carbohydrates_100g": 65,
            "fat_100g": 20,
        },
    }
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.heb_product_lookup, "lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [product]},
    )

    query = (smoke.CoverageQuery("packaged chocolate cookies", "required", "test query"),)
    result = smoke.run_coverage(query, env=CONFIGURED_ENV)[0]

    assert result.outcome == "open_food_facts"
    assert result.matched_item == "Acme Chocolate Cookies"
    assert result.calories == "450"


def test_coverage_records_usda_fallback_when_active_providers_miss(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.heb_product_lookup, "lookup", lambda *_a, **_kw: None)
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
    monkeypatch.setattr(smoke.branded_food_lookup.heb_product_lookup, "lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a, **_kw: _usda_payload(brandOwner="Bill Miller BBQ"),
    )
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_query(), env=CONFIGURED_ENV)[0]

    assert result.outcome == "usda_fdc"
    assert result.matched_item == "BRISKET SANDWICH"
    assert "production direct-lookup gate would block this query" not in result.notes


def test_coverage_records_miss_without_lm_studio_or_cache_write(monkeypatch):
    saved = []
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.data_store,
        "save_branded_lookup_cache",
        lambda *args, **_kwargs: saved.append(args),
    )
    monkeypatch.setattr(smoke.branded_food_lookup.heb_product_lookup, "lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_direct_lookup_query(), env=CONFIGURED_ENV)[0]

    assert result.outcome == "miss/fallback gap"
    assert result.matched_item == ""
    assert "heb_product_page, usda_fdc, open_food_facts reached with no verified match" in result.notes
    assert saved == []


def test_missing_credentials_report_provider_unavailable(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.heb_product_lookup, "lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        smoke.branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("credential-gated provider must not run")),
    )
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_query(), env={})[0]

    assert result.outcome == "provider unavailable"
    assert "nutritionix" not in result.notes.lower()
    assert "usda_fdc skipped: missing USDA_FDC_API_KEY" in result.notes


def test_coverage_can_respect_production_direct_lookup_gate(monkeypatch):
    blocked_query = (smoke.CoverageQuery("eggs and toast", "required", "test query"),)
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)

    result = smoke.run_coverage(blocked_query, respect_direct_lookup_gate=True)[0]

    assert result.outcome == "miss/fallback gap"
    assert "production direct-lookup gate blocked provider lookup" in result.notes


def test_proxy_rows_remain_labeled_as_proxy(monkeypatch):
    monkeypatch.setattr(smoke.branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.heb_product_lookup, "lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a, **_kw: None)
    monkeypatch.setattr(smoke.branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: None)

    result = smoke.run_coverage(_query(category="proxy", expected_chain=""), env=CONFIGURED_ENV)[0]

    assert result.category == "proxy"
    assert "proxy" in smoke.markdown_table([result])


def test_provider_status_does_not_expose_secret_values():
    status = smoke.provider_status(
        env={
            "USDA_FDC_API_KEY": "real-usda-key",
        }
    )

    assert status["cache"] == "skipped"
    assert status["direct_lookup_gate"] == "bypassed for coverage matrix; production gate recorded per row"
    assert "nutritionix" not in status
    assert status["usda_fdc"] == "configured"
    assert "real" not in " ".join(status.values())


def test_provider_status_respects_explicit_empty_env():
    status = smoke.provider_status(env={})

    assert "nutritionix" not in status
    assert status["usda_fdc"] == "missing USDA_FDC_API_KEY"


def test_provider_status_marks_respect_gate_mode():
    status = smoke.provider_status(respect_direct_lookup_gate=True)

    assert status["direct_lookup_gate"] == "production gate respected"
