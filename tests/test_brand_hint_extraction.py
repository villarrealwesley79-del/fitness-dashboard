from __future__ import annotations

import io

import branded_food_lookup


def _lookup_estimate(name="Chipotle chicken burrito"):
    return {
        "item_name": name,
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


def test_lookup_uses_brand_hint_as_prior(monkeypatch):
    captured = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)

    def fake_nutritionix(query):
        captured["query"] = query
        return {
            "foods": [
                {
                    "food_name": "chicken burrito",
                    "brand_name": "Chipotle",
                    "serving_qty": 1,
                    "serving_unit": "burrito",
                    "nf_calories": 1075,
                    "nf_protein": 51,
                    "nf_total_carbohydrate": 116,
                    "nf_total_fat": 41,
                    "nf_sodium": 2310,
                    "nf_dietary_fiber": 13,
                    "nix_item_id": "chipotle-burrito",
                }
            ]
        }

    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", fake_nutritionix)

    estimate = branded_food_lookup.lookup("foil wrapped burrito", brand_hint="chipotle")

    assert captured["query"] == "chipotle foil wrapped burrito"
    assert estimate["source"] == "nutritionix"
    assert "Chipotle" in estimate["item_name"]


def test_lookup_hint_is_non_binding_when_text_already_has_brand(monkeypatch):
    captured = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)

    def fake_nutritionix(query):
        captured["query"] = query
        return None

    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", fake_nutritionix)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)

    branded_food_lookup.lookup("Starbucks latte", brand_hint="chipotle")

    assert captured["query"] == "Starbucks latte"


def test_lookup_uses_hint_when_known_brand_token_can_be_flavor(monkeypatch):
    captured = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)

    def fake_nutritionix(query):
        captured["query"] = query
        return None

    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", fake_nutritionix)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)

    branded_food_lookup.lookup("chipotle chicken taco", brand_hint="Taco Bell")

    assert captured["query"] == "taco bell chipotle chicken taco"


def test_lookup_hint_is_non_binding_for_unlisted_brand_phrase(monkeypatch):
    captured = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)

    def fake_nutritionix(query):
        captured["query"] = query
        return None

    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", fake_nutritionix)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)

    branded_food_lookup.lookup("Taco Bell crunchy taco", brand_hint="chipotle")

    assert captured["query"] == "Taco Bell crunchy taco"


def test_lookup_hint_is_non_binding_for_spaced_chick_fil_a(monkeypatch):
    captured = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)

    def fake_nutritionix(query):
        captured["query"] = query
        return None

    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", fake_nutritionix)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)

    branded_food_lookup.lookup("Chick Fil A sandwich", brand_hint="chipotle")

    assert captured["query"] == "Chick Fil A sandwich"


def test_lookup_hint_does_not_duplicate_matching_unlisted_brand(monkeypatch):
    captured = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)

    def fake_nutritionix(query):
        captured["query"] = query
        return None

    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", fake_nutritionix)
    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", lambda *_a: None)

    branded_food_lookup.lookup("Taco Bell crunchy taco", brand_hint="Taco Bell")

    assert captured["query"] == "Taco Bell crunchy taco"


def test_lookup_keeps_brand_hint_out_of_usda_fallback(monkeypatch):
    captured = {}
    saved = {}
    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a: None)
    monkeypatch.setattr(
        branded_food_lookup.data_store,
        "save_branded_lookup_cache",
        lambda normalized, source, response: saved.update({"normalized": normalized, "source": source}),
    )
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a: None)

    def fake_usda(query):
        captured["query"] = query
        return {
            "foods": [
                {
                    "fdcId": 999,
                    "description": "BURRITO",
                    "foodNutrients": [
                        {"nutrientName": "Energy", "value": 240},
                        {"nutrientName": "Protein", "value": 8},
                        {"nutrientName": "Carbohydrate, by difference", "value": 38},
                        {"nutrientName": "Total lipid (fat)", "value": 6},
                    ],
                }
            ]
        }

    monkeypatch.setattr(branded_food_lookup.usda_fdc_client, "search_foods", fake_usda)

    estimate = branded_food_lookup.lookup("foil wrapped burrito", brand_hint="chipotle")

    assert captured["query"] == "foil wrapped burrito"
    assert estimate["source"] == "usda_fdc"
    assert saved["normalized"] == "foil wrapped burrito"


def test_meal_intake_passes_brand_hint_to_lookup(monkeypatch):
    import app

    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(app.vision_estimator, "describe", lambda *_a, **_kw: {
        "provider": "claude",
        "item_description": "foil wrapped burrito",
        "portion_hint": "1 burrito",
        "brand_hint": "chipotle",
        "brand_hint_confidence": 0.91,
        "confidence": 0.84,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    captured = {}

    def fake_lookup(text, **kwargs):
        captured["text"] = text
        captured["brand_hint"] = kwargs.get("brand_hint")
        return _lookup_estimate()

    monkeypatch.setattr(app.branded_food_lookup, "lookup", fake_lookup)
    monkeypatch.setattr(app, "add_food_log", lambda _uid, record: {"client_id": record["client_id"], **record})

    res = app.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "brand-hint-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "meal.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    body = res.get_json()
    assert res.status_code == 200
    assert captured["brand_hint"] == "chipotle"
    assert captured["text"] == "foil wrapped burrito 1 burrito"
    assert body["estimate"]["brand_hint"] == "chipotle"
    assert body["estimate"]["source"] == "vision_claude+nutritionix"


def test_meal_intake_accept_preserves_brand_hint(monkeypatch):
    import app

    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)
    captured = {}

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {"client_id": record["client_id"], "source": record["source"]}

    monkeypatch.setattr(app, "add_food_log", fake_add_food_log)

    res = app.app.test_client().post(
        "/api/meal-intake/brand-hint-accept-1/accept",
        json={
            "estimate": {
                "item_name": "Chipotle chicken burrito",
                "portion_description": "1 burrito",
                "meal_type": "lunch",
                "calories": 1075,
                "protein_g": 51,
                "carbs_g": 116,
                "fat_g": 41,
                "sodium_mg": 2310,
                "fiber_g": 13,
                "confidence": 0.72,
                "ambiguous": False,
                "uncertainty_notes": [],
                "source": "vision_claude+nutritionix",
                "brand_hint": "chipotle",
                "brand_hint_confidence": 0.91,
            },
        },
    )

    assert res.status_code == 200
    assert captured["original_estimate"]["brand_hint"] == "chipotle"
    assert captured["original_estimate"]["brand_hint_confidence"] == 0.91


def test_meal_intake_omits_brand_hint_when_none(monkeypatch):
    import app

    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(app.vision_estimator, "describe", lambda *_a, **_kw: {
        "provider": "claude",
        "item_description": "homemade pasta",
        "portion_hint": "1 bowl",
        "brand_hint": None,
        "brand_hint_confidence": 0,
        "confidence": 0.84,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    monkeypatch.setattr(app.branded_food_lookup, "lookup", lambda *_a, **_kw: _lookup_estimate("Pasta"))
    monkeypatch.setattr(app, "add_food_log", lambda _uid, record: {"client_id": record["client_id"], **record})

    res = app.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "no-brand-hint-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "meal.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    body = res.get_json()
    assert "brand_hint" not in body["estimate"]
