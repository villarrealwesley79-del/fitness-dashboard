from __future__ import annotations

import importlib
import io
import tempfile
from pathlib import Path

import data_store


REQUIRED_ESTIMATE_KEYS = {
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
}


def _module(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit57-contract-secret")
    db_path = Path(tempfile.mkdtemp(prefix="fit-meal-intake-contract-")) / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    return module


def _good_estimate():
    return {
        "item_name": "Protein shake",
        "portion_description": None,
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.86,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "ai_text_estimate",
    }


def test_zero_context_text_request_returns_contract_shape_and_uses_client_id(monkeypatch):
    module = _module(monkeypatch)
    persisted = {}

    monkeypatch.setattr(
        module,
        "parse_meal_text",
        lambda *_a, **_kw: {"estimate": _good_estimate(), "fallback_used": False},
    )

    def fake_add_food_log(_user_id, record):
        persisted.update(record)
        return {"client_id": record["client_id"], "correction_state": record["correction_state"]}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    response = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "protein shake", "client_id": "fit57-shake-1"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "pending_review"
    assert set(REQUIRED_ESTIMATE_KEYS).issubset(body["estimate"])
    assert body["estimate"]["source"] == "ai_text_estimate"
    assert body["policy"] == {"confidence_band": "high", "reasons": []}
    assert body["food_log"]["client_id"] == "fit57-shake-1"
    assert persisted["client_id"] == "fit57-shake-1"
    assert persisted["correction_state"] == "pending_review"


def test_zero_context_image_request_uses_same_estimate_shape_without_raw_image_echo(monkeypatch):
    module = _module(monkeypatch)
    persisted = {}
    monkeypatch.setattr(module.vision_estimator, "describe", lambda *_a, **_kw: {
        "provider": "claude",
        "item_description": "protein shake",
        "portion_hint": "1 shake",
        "confidence": 0.86,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    monkeypatch.setattr(module.branded_food_lookup, "lookup", lambda *_a, **_kw: {
        "item_name": "Protein shake",
        "portion_description": "1 shake",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.86,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "nutritionix",
    })

    def fake_add_food_log(_user_id, record):
        persisted.update(record)
        return {"client_id": record["client_id"], "correction_state": record["correction_state"]}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    response = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "fit57-photo-1",
            "text": "protein shake",
            "image": (io.BytesIO(b"not-real-image-but-valid-upload-bytes"), "shake.jpg"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    body = response.get_json()
    assert set(REQUIRED_ESTIMATE_KEYS).issubset(body["estimate"])
    payload = response.get_data(as_text=True).lower()
    assert "not-real-image" not in payload
    assert "raw_image" not in payload
    assert "chain_of_thought" not in payload
    assert body["food_log"]["client_id"] == "fit57-photo-1"
    assert persisted["client_id"] == "fit57-photo-1"


def test_pending_review_response_persists_as_non_counting_review_row(monkeypatch):
    module = _module(monkeypatch)
    pending_estimate = _good_estimate()
    pending_estimate.update(
        {
            "item_name": "Shared popcorn",
            "calories": 300,
            "protein_g": 5,
            "carbs_g": 36,
            "fat_g": 18,
            "sodium_mg": 520,
            "fiber_g": 6,
            "confidence": 0.45,
            "ambiguous": True,
            "uncertainty_notes": ["Portion is unclear."],
        }
    )
    monkeypatch.setattr(
        module,
        "parse_meal_text",
        lambda *_a, **_kw: {"estimate": pending_estimate, "fallback_used": False},
    )
    persisted = []

    def fake_add_food_log(_user_id, record):
        persisted.append(record)
        return {
            "client_id": record["client_id"],
            "correction_state": record["correction_state"],
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    response = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "shared movie popcorn", "client_id": "fit57-pending-1"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "pending_review"
    assert body["food_log"]["client_id"] == "fit57-pending-1"
    assert body["food_log"]["correction_state"] == "pending_review"
    assert "ambiguous_input" in body["policy"]["reasons"]
    assert persisted[0]["correction_state"] == "pending_review"
