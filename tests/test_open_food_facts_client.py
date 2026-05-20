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
    assert params["page_size"] == ["25"]
    assert captured["timeout"] == open_food_facts_client.TIMEOUT_SECONDS
    assert open_food_facts_client.TIMEOUT_SECONDS >= 5
    assert captured["ua"] == open_food_facts_client.USER_AGENT


def test_open_food_facts_client_retries_without_locale_word(monkeypatch):
    seen_terms = []

    def fake_urlopen(req, timeout):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        seen_terms.append(params["search_terms"][0])
        if len(seen_terms) < 3:
            return _Response({"products": []})
        return _Response({"products": [_product("Tim Tam")]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = open_food_facts_client.search_products("Australian Tim Tams")

    assert seen_terms == ["Tim Tams", "Tim Tam", "Australian Tim Tams"]
    assert result["products"][0]["product_name"] == "Tim Tam"


def test_open_food_facts_client_retries_without_punctuated_locale_word(monkeypatch):
    seen_terms = []

    def fake_urlopen(req, timeout):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        seen_terms.append(params["search_terms"][0])
        if len(seen_terms) == 1:
            return _Response({"products": [_product("Petit Ecolier")]})
        return _Response({"products": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = open_food_facts_client.search_products("French: Petit Ecolier")

    assert seen_terms == ["Petit Ecolier"]
    assert result["products"][0]["product_name"] == "Petit Ecolier"


def test_open_food_facts_client_retries_without_punctuated_uk_locale(monkeypatch):
    seen_terms = []

    def fake_urlopen(req, timeout):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        seen_terms.append(params["search_terms"][0])
        return _Response({"products": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    open_food_facts_client.search_products("U.K. Walkers crisps")

    assert seen_terms[0] == "Walkers crisps"


def test_open_food_facts_client_stops_after_usable_variant(monkeypatch):
    seen_terms = []

    def fake_urlopen(req, timeout):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        seen_terms.append(params["search_terms"][0])
        return _Response({"products": [_product("Tim Tam")]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = open_food_facts_client.search_products(
        "Australian Tim Tams",
        product_filter=lambda product: product["product_name"] == "Tim Tam",
    )

    assert seen_terms == ["Tim Tams"]
    assert result["products"][0]["product_name"] == "Tim Tam"


def test_open_food_facts_client_bounds_variant_retry_time(monkeypatch):
    seen_terms = []
    timeouts = []
    clock = [0.0]

    def fake_urlopen(req, timeout):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        seen_terms.append(params["search_terms"][0])
        timeouts.append(timeout)
        clock[0] += timeout
        return _Response({"products": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(open_food_facts_client.time, "monotonic", lambda: clock[0])

    result = open_food_facts_client.search_products("Australian Tim Tams")

    assert result["products"] == []
    assert seen_terms == ["Tim Tams", "Tim Tam"]
    assert timeouts == [5.0, 1.0]


def test_open_food_facts_client_passes_country_filter(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        return _Response({"products": [_product("Walkers Crisps")]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    open_food_facts_client.search_products("UK Walkers crisps", country_tag="en:united-kingdom")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)

    assert params["search_terms"] == ["Walkers crisps"]
    assert params["tagtype_0"] == ["countries"]
    assert params["tag_contains_0"] == ["contains"]
    assert params["tag_0"] == ["United Kingdom"]


def test_open_food_facts_lookup_uses_later_usable_variant(monkeypatch):
    seen_terms = []
    bad = _product("Noisy Tim Tams", code="bad")
    bad["nutriments"]["proteins_100g"] = None
    good = _product("Tim Tam", code="good", country="en:australia")

    def fake_urlopen(req, timeout):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        seen_terms.append(params["search_terms"][0])
        if len(seen_terms) == 1:
            return _Response({"products": [bad]})
        if len(seen_terms) == 2:
            return _Response({"products": []})
        return _Response({"products": [good]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)

    estimate = branded_food_lookup.lookup("imported Australian Tim Tams")

    assert seen_terms == ["Tim Tams", "Tim Tam", "imported Australian Tim Tams"]
    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "good"


def test_open_food_facts_prefers_requested_country(monkeypatch):
    wrong_country = _product("Tim Tam US", code="wrong", country="en:united-states")
    australia = _product("Tim Tam", code="right", country="en:australia")
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [wrong_country, australia]},
    )

    estimate = branded_food_lookup.lookup("imported Australian Tim Tams")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "right"


def test_open_food_facts_rejects_wrong_country_for_locale(monkeypatch):
    wrong_country = _product("Tim Tam US", code="wrong", country="en:united-states")
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [wrong_country]},
    )

    assert branded_food_lookup.lookup("Australian Tim Tams") is None


def test_open_food_facts_tier_runs_after_usda(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [_product("Walkers Crisps", code="500032837")]}
    )

    estimate = branded_food_lookup.lookup("UK Walkers crisps")

    assert estimate["source"] == "open_food_facts"
    assert estimate["item_name"] == "Brand Walkers Crisps"
    assert estimate["external_food_id"] == "500032837"
    assert estimate["sodium_mg"] == 400
    assert "CC-BY-SA" in estimate["off_attribution"]


def test_open_food_facts_skips_generic_meal_text(monkeypatch):
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("OFF must not run for generic meals")),
    )

    assert branded_food_lookup.lookup("eggs and toast") is None
    assert branded_food_lookup.lookup("French toast") is None
    assert branded_food_lookup.lookup("German chocolate cake") is None


def test_open_food_facts_accepts_zero_calorie_products(monkeypatch):
    product = _product("Sparkling Water", code="zero")
    product["nutriments"].update(
        {
            "energy-kcal_100g": 0,
            "proteins_100g": 0,
            "carbohydrates_100g": 0,
            "fat_100g": 0,
            "sodium_100g": 0,
            "fiber_100g": 0,
        }
    )
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [product]},
    )

    estimate = branded_food_lookup.lookup("UK sparkling water")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "zero"
    assert estimate["calories"] == 0


def test_open_food_facts_filters_bad_quality(monkeypatch):
    bad = _product("Bad Product")
    bad["data_quality_tags"] = ["en:nutrition-data-error"]
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.open_food_facts_client, "search_products", lambda *_a, **_kw: {"products": [bad]})

    assert branded_food_lookup.lookup("bad product") is None


def test_open_food_facts_rejects_rows_without_complete_quality_tag(monkeypatch):
    product = _product("Untagged Product", code="untagged")
    product["data_quality_tags"] = []
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [product]},
    )

    estimate = branded_food_lookup.lookup("non us packaged food")

    assert estimate is None


def test_open_food_facts_accepts_actual_completion_quality_tag(monkeypatch):
    product = _product("Completed Product", code="completed")
    product["data_quality_tags"] = ["en:nutrition-completed"]
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [product]},
    )

    estimate = branded_food_lookup.lookup("completed product")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "completed"


def test_open_food_facts_handles_punctuated_uk_locale(monkeypatch):
    wrong_country = _product("Walkers Crisps", code="wrong", country="en:france")
    uk_product = _product("Walkers Crisps", code="uk", country="en:united-kingdom")
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [wrong_country, uk_product]},
    )

    estimate = branded_food_lookup.lookup("U.K. Walkers crisps")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "uk"


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
        lambda *_a, **_kw: {"products": [bad, good]},
    )

    estimate = branded_food_lookup.lookup("non us packaged food")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "good"
    assert estimate["calories"] == 500


def test_open_food_facts_skips_salt_quality_warnings(monkeypatch):
    bad = _product("Bad Product", code="bad")
    bad["data_quality_tags"] = [
        "en:nutrition-packaging-as-sold-100g-value-under-0-01-g-salt",
    ]
    bad["nutriments"]["sodium_100g"] = 0.00116
    good = _product("Clean Product", code="good")
    good["nutriments"]["sodium_100g"] = 0.252
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [bad, good]},
    )

    estimate = branded_food_lookup.lookup("non us salty snack")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "good"
    assert estimate["sodium_mg"] == 252


def test_open_food_facts_defaults_malformed_optional_nutrients(monkeypatch):
    product = _product("Optional Fields Product", code="optional")
    product["nutriments"]["fiber_100g"] = "unknown"
    product["nutriments"]["sodium_100g"] = "unknown"
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [product]},
    )

    estimate = branded_food_lookup.lookup("optional fields product")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "optional"
    assert estimate["fiber_g"] == 0
    assert estimate["sodium_mg"] == 0


def test_open_food_facts_falls_back_to_salt_when_sodium_malformed(monkeypatch):
    product = _product("Salt Fallback Product", code="salt-fallback")
    product["nutriments"]["sodium_100g"] = "unknown"
    product["nutriments"]["salt_100g"] = 0.64
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [product]},
    )

    estimate = branded_food_lookup.lookup("salt fallback product")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "salt-fallback"
    assert estimate["sodium_mg"] == 252


def test_open_food_facts_skips_schema_invalid_candidates(monkeypatch):
    bad = _product("Too Salty Product", code="bad")
    bad["nutriments"]["sodium_100g"] = 20
    good = _product("Clean Product", code="good")
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "search_products",
        lambda *_a, **_kw: {"products": [bad, good]},
    )

    estimate = branded_food_lookup.lookup("non us packaged food")

    assert estimate["source"] == "open_food_facts"
    assert estimate["external_food_id"] == "good"
    assert estimate["sodium_mg"] == 400


def test_open_food_facts_non_us_cases_are_appendable(monkeypatch):
    cases = [
        ("UK Walkers crisps", "Walkers Crisps", "en:united-kingdom"),
        ("imported Australian Tim Tams", "Tim Tam", "en:australia"),
        ("imported French Petit Ecolier", "Petit Ecolier", "en:france"),
        ("Japan Pocky", "Pocky", "en:japan"),
        ("imported German Haribo Goldbaren", "Haribo Goldbaren", "en:germany"),
    ]
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)

    for query, name, country in cases:
        monkeypatch.setattr(
            branded_food_lookup.open_food_facts_client,
            "search_products",
            lambda *_a, name=name, country=country, **_kw: {"products": [_product(name, country=country)]},
        )
        estimate = branded_food_lookup.lookup(query)
        assert estimate["source"] == "open_food_facts"
        assert name in estimate["item_name"]
