from __future__ import annotations

import importlib
import io

import pytest

from meal_estimate_schema import MealEstimateValidationError, manual_review_estimate, sanitize_meal_estimate


def _valid_estimate(**overrides):
    estimate = {
        "item_name": "Burger",
        "portion_description": "one sandwich",
        "meal_type": "lunch",
        "calories": 540,
        "protein_g": 28,
        "carbs_g": 42,
        "fat_g": 30,
        "sodium_mg": 980,
        "fiber_g": 3,
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "ai_text_estimate",
        "raw_prompt": "do not leak",
        "image_bytes": "do not leak",
        "chain_of_thought": "do not leak",
    }
    estimate.update(overrides)
    return estimate


def test_sanitize_meal_estimate_returns_public_schema_only():
    sanitized = sanitize_meal_estimate(_valid_estimate())

    assert sanitized == {
        "item_name": "Burger",
        "portion_description": "one sandwich",
        "meal_type": "lunch",
        "calories": 540,
        "protein_g": 28.0,
        "carbs_g": 42.0,
        "fat_g": 30.0,
        "sodium_mg": 980,
        "fiber_g": 3.0,
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "ai_text_estimate",
    }
    assert "raw_prompt" not in sanitized
    assert "image_bytes" not in sanitized
    assert "chain_of_thought" not in sanitized


def test_sanitize_meal_estimate_preserves_lookup_provenance():
    sanitized = sanitize_meal_estimate(_valid_estimate(
        external_food_id="chipotle-burrito",
        verified_source_url="https://www.nutritionix.com/",
        data_fetched_at="2026-05-19T10:00:00",
        portion_basis="1 burrito",
        brand_id="chipotle",
        underlying_source="nutritionix",
        off_attribution={
            "name": "Open Food Facts",
            "url": "https://world.openfoodfacts.org/",
            "raw": {"drop": "nested"},
        },
    ))

    assert sanitized["external_food_id"] == "chipotle-burrito"
    assert sanitized["verified_source_url"] == "https://www.nutritionix.com/"
    assert sanitized["data_fetched_at"] == "2026-05-19T10:00:00"
    assert sanitized["portion_basis"] == "1 burrito"
    assert sanitized["brand_id"] == "chipotle"
    assert sanitized["underlying_source"] == "nutritionix"
    assert sanitized["off_attribution"] == {
        "name": "Open Food Facts",
        "url": "https://world.openfoodfacts.org/",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("calories", -1),
        ("protein_g", True),
        ("confidence", 1.2),
        ("ambiguous", "no"),
        ("uncertainty_notes", ["ok", 5]),
    ],
)
def test_sanitize_meal_estimate_rejects_invalid_schema(field, value):
    with pytest.raises(MealEstimateValidationError):
        sanitize_meal_estimate(_valid_estimate(**{field: value}))


def test_sanitize_unknown_sodium_uses_zero_compatibility_placeholder_once():
    raw = _valid_estimate(
        sodium_mg=None,
        uncertainty_notes=["Sodium is unknown; 0 is a compatibility placeholder."],
    )

    estimate = sanitize_meal_estimate(raw)

    assert estimate["sodium_mg"] == 0
    assert estimate["ambiguous"] is True
    assert estimate["uncertainty_notes"].count(
        "Sodium is unknown; 0 is a compatibility placeholder."
    ) == 1


def test_sanitize_verified_zero_sodium_does_not_add_unknown_note():
    estimate = sanitize_meal_estimate(_valid_estimate(sodium_mg=0))

    assert estimate["sodium_mg"] == 0
    assert estimate["ambiguous"] is False
    assert not any("sodium is unknown" in note.lower() for note in estimate["uncertainty_notes"])


def test_manual_review_estimate_is_always_pending_review_safe():
    estimate = manual_review_estimate(text="photo of burger wrapper")

    assert estimate["confidence"] == 0.0
    assert estimate["ambiguous"] is True
    assert estimate["uncertainty_notes"]
    assert estimate["item_name"] == "photo of burger wrapper"


def test_image_meal_intake_uses_public_schema_and_drops_private_fields(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit5-schema-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module.vision_estimator, "describe", lambda *_a, **_kw: {
        "provider": "claude",
        "item_description": "burger",
        "portion_hint": "1 burger",
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    monkeypatch.setattr(module.branded_food_lookup, "lookup", lambda *_a, **_kw: {
        "item_name": "Burger",
        "portion_description": "one sandwich",
        "meal_type": "lunch",
        "calories": 540,
        "protein_g": 28,
        "carbs_g": 42,
        "fat_g": 30,
        "sodium_mg": 980,
        "fiber_g": 3,
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "nutritionix",
    })
    monkeypatch.setattr(module, "add_food_log", lambda _uid, record: {
        "client_id": record["client_id"],
        "source": record["source"],
    })

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "fit5-image-1",
            "text": "burger",
            "image": (io.BytesIO(b"fake-image"), "meal.jpg"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["estimate"]["source"] == "vision_claude+nutritionix"
    assert {
        "item_name",
        "portion_description",
        "meal_type",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sodium_mg",
        "fiber_g",
        "confidence",
        "ambiguous",
        "uncertainty_notes",
        "source",
        "from_image",
    }.issubset(body["estimate"])
    assert body["estimate"]["from_image"] is True
    assert "image_bytes" not in str(body)
    assert "chain_of_thought" not in str(body)


def test_accept_rejects_non_object_estimate(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit5-schema-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)

    res = module.app.test_client().post("/api/meal-intake/bad-estimate/accept", json={"estimate": []})

    assert res.status_code == 400
    assert "estimate" in res.get_json()["error"]["message"]


def test_accept_rejects_implausible_client_edited_estimate(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit5-schema-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not persist")))

    res = module.app.test_client().post(
        "/api/meal-intake/huge-estimate/accept",
        json={"estimate": {
            "item_name": "Meal",
            "calories": 999999,
            "protein_g": 10,
            "carbs_g": 10,
            "fat_g": 10,
        }},
    )

    assert res.status_code == 400
    assert "calories" in res.get_json()["error"]["message"]
