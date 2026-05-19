from __future__ import annotations

import json
import urllib.parse
import urllib.request

import branded_food_lookup
import open_food_facts_client


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _product(name, code="123", brand="Brand", country="en:united-kingdom"):
    return {
        "code": code,
        "product_name": name,
        "brands": brand,
        "url": f"https://world.openfoodfacts.org/product/{code}",
        "countries_tags": [country],
        "data_quality_tags": ["en:nutrition-data-complete"],
        "nutriments": {
            "energy-kcal_100g": 500,
            "proteins_100g": 6,
            "carbohydrates_100g": 60,
            "fat_100g": 25,
            "sodium_100g": 0.4,
            "fiber_100g": 3,
        },
    }


def test_open_food_facts_client_searches_keyless_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["ua"] = req.headers["User-agent"]
        return _Response({"products": [_product("Walkers Crisps")]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = open_food_facts_client.search_products("Walkers crisps")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)

    assert result["products"][0]["product_name"] == "Walkers Crisps"
    assert params["search_terms"] == ["Walkers crisps"]
    assert params["json"] == ["1"]
    assert captured["timeout"] == open_food_facts_client.TIMEOUT_SECONDS
    assert "fitness-dashboard" in captured["ua"]


def test_open_food_facts_tier_runs_after_usda(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a: {"products": [_product("Walkers Crisps", code="500032837")]}
    )

    estimate = branded_food_lookup.lookup("UK Walkers crisps")

    assert estimate["source"] == "open_food_facts"
    assert estimate["item_name"] == "Brand Walkers Crisps"
    assert estimate["external_food_id"] == "500032837"
    assert estimate["sodium_mg"] == 400
    assert "CC-BY-SA" in estimate["off_attribution"]


def test_open_food_facts_filters_bad_quality(monkeypatch):
    bad = _product("Bad Product")
    bad["data_quality_tags"] = ["en:nutrition-data-error"]
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.open_food_facts_client, "search_products", lambda *_a: {"products": [bad]})

    assert branded_food_lookup.lookup("bad product") is None


def test_open_food_facts_skips_nutrition_mismatch_warnings(monkeypatch):
    bad = _product("Bad Product", code="bad")
    bad["data_quality_tags"] = [
        "en:nutrition-energy-value-in-kcal-does-not-match-value-computed-from-other-nutrients",
    ]
    bad["nutriments"]["energy-kcal_100g"] = 1213.95
    good = _product("Clean Product", code="good")
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a: {"products": [bad, good]},
    )

    estimate = branded_food_lookup.lookup("non us packaged food")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "good"
    assert estimate["calories"] == 500


def test_open_food_facts_non_us_cases_are_appendable(monkeypatch):
    cases = [
        ("UK Walkers crisps", "Walkers Crisps", "en:united-kingdom"),
        ("Australian Tim Tams", "Tim Tam", "en:australia"),
        ("French Petit Ecolier", "Petit Ecolier", "en:france"),
        ("Japanese Pocky", "Pocky", "en:japan"),
        ("German Haribo Goldbaren", "Haribo Goldbaren", "en:germany"),
    ]
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)

    for query, name, country in cases:
        monkeypatch.setattr(
            branded_food_lookup.open_food_facts_client,
            "search_products",
            lambda *_a, name=name, country=country: {"products": [_product(name, country=country)]},
        )
        estimate = branded_food_lookup.lookup(query)
        assert estimate["source"] == "open_food_facts"
        assert name in estimate["item_name"]
