"""Tests for the /api/meal-intake endpoint.

These cover the wire-up the UI relies on (logged vs pending_review vs
validation errors), the undo endpoint, and the accept endpoint.

Text-only input is routed through the real FIT-59 parser
(meal_text_parser.parse_meal_text); image-bearing input is routed through
the real vision-estimator seam and the same meal-log policy.
"""
from __future__ import annotations

import importlib
import io


def _client(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit60-stub-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    return module


def _stub_parser(monkeypatch, module, *, estimate, source="ai_text_estimate", fallback_used=False):
    """Replace meal_text_parser.parse_meal_text with a deterministic stub
    so endpoint tests exercise the wiring, not the parser internals.

    ``source`` is injected inside the returned estimate dict to match the
    real parser shape — provenance lives in the estimate so it round-trips
    through the /api/meal-intake/<client_id>/accept handler.
    """
    def fake(_text, **_kw):
        e = dict(estimate)
        e["source"] = source
        return {"estimate": e, "fallback_used": fallback_used}

    monkeypatch.setattr(module, "parse_meal_text", fake)


_DEFAULT_LOOKUP = object()


def _stub_vision(monkeypatch, module, *, vision=None, lookup=_DEFAULT_LOOKUP):
    vision = vision or {
        "provider": "claude",
        "item_description": "protein shake",
        "portion_hint": "1 shake",
        "confidence": 0.86,
        "ambiguous": False,
        "uncertainty_notes": [],
    }
    if lookup is _DEFAULT_LOOKUP:
        lookup = {
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
        }
    monkeypatch.setattr(module.vision_estimator, "describe", lambda *_a, **_kw: dict(vision))
    monkeypatch.setattr(module.branded_food_lookup, "lookup", lambda *_a, **_kw: dict(lookup) if lookup else None)


def test_meal_intake_text_only_auto_logs_when_parser_is_confident(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Eggs and toast",
        "portion_description": None,
        "meal_type": "breakfast",
        "calories": 420,
        "protein_g": 24,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 4,
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": [],
    })

    persisted = {}

    def fake_add_food_log(_user_id, record):
        persisted.update(record)
        return {
            "id": 7,
            "client_id": record["client_id"],
            "item_name": record["item_name"],
            "calories": record["calories"],
            "correction_state": record["correction_state"],
            "source": record["source"],
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "two eggs and toast", "client_id": "meal-abc-1"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["estimate"]["item_name"] == "Eggs and toast"
    assert body["estimate"]["calories"] > 0
    assert body["food_log"]["client_id"] == "meal-abc-1"
    assert body["fallback_used"] is False
    assert "stub" not in body, "real text path must not advertise stub: true"
    assert persisted["correction_state"] == "accepted"
    assert persisted["source"] == "ai_text_estimate"


def test_meal_intake_preserves_open_food_facts_attribution(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
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
        "external_food_id": "500032837",
        "verified_source_url": "https://world.openfoodfacts.org/product/500032837",
        "portion_basis": "100 g Open Food Facts packaged-food reference",
        "off_attribution": "Source: Open Food Facts, licensed under CC-BY-SA.",
    }, source="open_food_facts")

    persisted = {}

    def fake_add_food_log(_user_id, record):
        persisted.update(record)
        return {
            "id": 8,
            "client_id": record["client_id"],
            "item_name": record["item_name"],
            "calories": record["calories"],
            "source": record["source"],
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "UK Walkers crisps", "client_id": "meal-off-1"},
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["estimate"]["source"] == "open_food_facts"
    assert body["estimate"]["verified_source_url"] == "https://world.openfoodfacts.org/product/500032837"
    assert "CC-BY-SA" in body["estimate"]["off_attribution"]
    assert persisted["original_estimate"]["verified_source_url"] == "https://world.openfoodfacts.org/product/500032837"
    assert "CC-BY-SA" in persisted["original_estimate"]["off_attribution"]


def test_meal_intake_text_pending_review_when_parser_ambiguous(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Popcorn",
        "portion_description": "approx half portion",
        "meal_type": "snack",
        "calories": 150,
        "protein_g": 3,
        "carbs_g": 18,
        "fat_g": 9,
        "sodium_mg": 260,
        "fiber_g": 3,
        "confidence": 0.45,
        "ambiguous": True,
        "uncertainty_notes": ["Portion unclear — confirm before it counts."],
    })

    persisted = []

    def fake_add_food_log(_user_id, record):
        persisted.append(record)
        return {"client_id": record["client_id"], "correction_state": record["correction_state"]}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "movie theater popcorn, shared", "client_id": "meal-pending-1"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["estimate"]["item_name"] == "Popcorn"
    assert body["estimate"]["uncertainty_notes"], "ambiguous estimates must surface notes"
    # FIT-61: pending entries are NOT persisted server-side. They live
    # in the composer's JS state until the user accepts (which goes
    # through /api/meal-intake/<client_id>/accept) or discards. See
    # FIT-67 for durable cross-reload pending resolution.
    assert persisted == [], "pending estimates must not auto-persist"
    assert body["food_log"] is None
    assert body["policy"]["reasons"], "pending response must surface policy reasons"


def test_meal_intake_text_pending_review_when_parser_falls_back(monkeypatch):
    """When LM Studio is unavailable the parser returns a fallback
    estimate; the endpoint must not auto-log it because the fallback
    confidence is intentionally below the auto-log threshold."""
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Eggs and toast",
        "portion_description": None,
        "meal_type": "breakfast",
        "calories": 420,
        "protein_g": 24,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 4,
        "confidence": 0.60,
        "ambiguous": False,
        "uncertainty_notes": [],
    }, source="fallback_text_estimate", fallback_used=True)

    persisted = []

    def fake_add_food_log(_user_id, record):
        persisted.append(record)
        return {"client_id": record["client_id"], "correction_state": record["correction_state"]}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "two eggs and toast", "client_id": "meal-fb-1"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["fallback_used"] is True
    # FIT-61: pending entries are not persisted; user must explicitly accept.
    assert persisted == [], "fallback estimates must not auto-persist"
    assert body["food_log"] is None


def test_meal_intake_text_idempotent_for_same_client_id(monkeypatch):
    """Two POSTs with the same client_id must persist exactly once.

    The endpoint relies on data_store.add_food_log's UNIQUE(user_id, client_id)
    upsert (see FIT-44 for the planned ON CONFLICT hardening). This test
    asserts the contract from the parser path's perspective: same client_id
    + same text returns the same logged shape both times.
    """
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Protein shake",
        "portion_description": None,
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
    })

    seen = []

    def fake_add_food_log(_user_id, record):
        seen.append(record["client_id"])
        return {
            "id": 1,
            "client_id": record["client_id"],
            "item_name": record["item_name"],
            "calories": record["calories"],
            "correction_state": record["correction_state"],
            "source": record["source"],
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    client = module.app.test_client()
    first = client.post(
        "/api/meal-intake",
        data={"text": "protein shake", "client_id": "meal-idem-1"},
        content_type="multipart/form-data",
    )
    second = client.post(
        "/api/meal-intake",
        data={"text": "protein shake", "client_id": "meal-idem-1"},
        content_type="multipart/form-data",
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["food_log"]["client_id"] == "meal-idem-1"
    assert second.get_json()["food_log"]["client_id"] == "meal-idem-1"
    # add_food_log handles UPSERT semantics; we just verify same client_id
    # reached the persistence layer both times.
    assert seen == ["meal-idem-1", "meal-idem-1"]


def test_meal_intake_text_response_omits_meta_and_traces(monkeypatch):
    """No _meta, raw model output, or chain of thought may appear in the
    response shape exposed to the client.
    """
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Protein shake",
        "portion_description": None,
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {
        "client_id": "meal-clean-1", "id": 1,
    })

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "protein shake", "client_id": "meal-clean-1"},
        content_type="multipart/form-data",
    )
    body = res.get_json()
    estimate = body["estimate"]
    for forbidden in ("_meta", "raw", "trace", "prompt", "chain_of_thought"):
        assert forbidden not in estimate, f"{forbidden} leaked into estimate"
        assert forbidden not in body, f"{forbidden} leaked into response"


def test_meal_intake_rejects_empty_submission(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"client_id": "meal-empty-1"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    body = res.get_json()
    assert "description" in body["error"]["message"]


def test_meal_intake_requires_client_id(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "yogurt"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    assert "client_id" in res.get_json()["error"]["message"]


def test_meal_intake_image_only_auto_logs(monkeypatch):
    module = _client(monkeypatch)
    captured = {}
    _stub_vision(monkeypatch, module)

    def fake_add_food_log(_user_id, record):
        captured["source"] = record["source"]
        captured["context_note"] = record.get("context_note")
        return {"client_id": record["client_id"], **record}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\0" * 32  # fake PNG header + filler
    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-img-1",
            "image": (io.BytesIO(image_bytes), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["estimate"]["confidence"] >= 0.7
    assert body["photo_retention"]["policy"] == "discard_after_extraction"
    assert body["photo_retention"]["raw_photo_retained"] is False
    assert body["photo_retention"]["backup_includes_raw_photo"] is False
    assert "image_bytes" not in str(body)
    assert "plate.png" not in str(body)
    assert captured["source"] == "vision_claude+nutritionix"
    assert captured["context_note"] is None


def test_meal_intake_image_text_is_preserved_as_brand_hint(monkeypatch):
    module = _client(monkeypatch)
    captured = {}
    vision = {
        "provider": "claude",
        "item_description": "burrito",
        "portion_hint": "1 burrito",
        "confidence": 0.86,
        "ambiguous": False,
        "uncertainty_notes": [],
    }
    lookup = {
        "item_name": "Chipotle chicken burrito",
        "portion_description": "1 burrito",
        "meal_type": "lunch",
        "calories": 1075,
        "protein_g": 51,
        "carbs_g": 116,
        "fat_g": 41,
        "sodium_mg": 2310,
        "fiber_g": 13,
        "confidence": 0.86,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "nutritionix",
    }
    monkeypatch.setattr(module.vision_estimator, "describe", lambda *_a, **_kw: dict(vision))

    def fake_lookup(text, **kwargs):
        captured["text"] = text
        captured["brand_hint"] = kwargs.get("brand_hint")
        return dict(lookup)

    monkeypatch.setattr(module.branded_food_lookup, "lookup", fake_lookup)
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "Chipotle chicken burrito",
            "client_id": "meal-img-brand-hint-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    assert captured == {
        "text": "Chipotle chicken burrito burrito 1 burrito",
        "brand_hint": None,
    }
    assert res.get_json()["estimate"]["item_name"] == "Chipotle chicken burrito"


def test_meal_intake_image_lookup_confidence_is_capped_by_vision(monkeypatch):
    module = _client(monkeypatch)
    persisted = []
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "claude",
            "item_description": "protein shake",
            "portion_hint": "1 shake",
            "confidence": 0.62,
            "ambiguous": False,
            "uncertainty_notes": [],
        },
        lookup={
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
            "external_food_id": "shake-1",
            "verified_source_url": "https://www.nutritionix.com/",
            "portion_basis": "1 shake",
        },
    )
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: persisted.append(record) or record)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-img-low-confidence-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["estimate"]["confidence"] == 0.62
    assert body["food_log"] is None
    assert persisted == []

    accept = module.app.test_client().post(
        "/api/meal-intake/meal-img-low-confidence-1/accept",
        json={"estimate": body["estimate"], "text": "protein shake"},
    )

    assert accept.status_code == 200, accept.get_data(as_text=True)
    accepted_estimate = persisted[-1]["original_estimate"]
    assert accepted_estimate["external_food_id"] == "shake-1"
    assert accepted_estimate["verified_source_url"] == "https://www.nutritionix.com/"
    assert accepted_estimate["portion_basis"] == "1 shake"
    assert accepted_estimate["vision_description"] == "protein shake"
    assert accepted_estimate["vision_provider"] == "claude"
    assert accepted_estimate["vision_confidence"] == 0.62


def test_meal_intake_image_invalid_macro_estimate_falls_to_manual_review(monkeypatch):
    module = _client(monkeypatch)
    persisted = []
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "claude",
            "item_description": "mystery pastry",
            "portion_hint": "1 pastry",
            "confidence": 0.72,
            "ambiguous": False,
            "uncertainty_notes": [],
            "macro_estimate": {
                "meal_type": "snack",
                "calories": "unknown",
            },
        },
        lookup=None,
    )
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: persisted.append(record) or record)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-img-bad-macros-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["estimate"]["source"] == "vision_claude_estimate"
    assert body["estimate"]["confidence"] == 0.45
    assert body["food_log"] is None
    assert persisted == []


def test_meal_intake_image_lookup_failure_falls_back_to_macro_estimate(monkeypatch):
    module = _client(monkeypatch)
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "claude",
            "item_description": "turkey sandwich",
            "portion_hint": "1 sandwich",
            "confidence": 0.72,
            "ambiguous": False,
            "uncertainty_notes": [],
            "macro_estimate": {
                "meal_type": "lunch",
                "calories": 520,
                "protein_g": 32,
                "carbs_g": 48,
                "fat_g": 21,
                "sodium_mg": 980,
                "fiber_g": 4,
            },
        },
        lookup=None,
    )
    monkeypatch.setattr(
        module.branded_food_lookup,
        "lookup",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("cache malformed")),
    )
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-img-lookup-failure-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["estimate"]["item_name"] == "turkey sandwich"
    assert body["estimate"]["calories"] == 520
    assert body["food_log"] is None


def test_meal_intake_image_preserves_cached_underlying_source(monkeypatch):
    module = _client(monkeypatch)
    captured = {}
    _stub_vision(
        monkeypatch,
        module,
        lookup={
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
            "source": "local_cache",
            "underlying_source": "nutritionix",
        },
    )

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {"client_id": record["client_id"], **record}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-img-cache-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["estimate"]["source"] == "vision_claude+local_cache"
    assert body["estimate"]["underlying_source"] == "nutritionix"
    assert captured["original_estimate"]["underlying_source"] == "nutritionix"


def test_meal_intake_rejects_oversize_image(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    big = io.BytesIO(b"\0" * (6 * 1024 * 1024 + 1))
    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-big-1",
            "image": (big, "huge.jpg", "image/jpeg"),
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 413
    assert "6 MB" in res.get_json()["error"]["message"]


def test_meal_intake_rejects_non_image_upload(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-bad-1",
            "image": (io.BytesIO(b"not really an image"), "notes.txt", "text/plain"),
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    assert "image/" in res.get_json()["error"]["message"]


def test_meal_intake_rejects_unsupported_image_type_before_provider(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(
        module.vision_estimator,
        "describe",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("provider must not receive unsupported image type")),
    )

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-heic-1",
            "image": (io.BytesIO(b"heic-image"), "plate.heic", "image/heic"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 415
    assert "unsupported image type" in res.get_json()["error"]["message"]


def test_meal_intake_image_provider_failure_uses_text_fallback(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(
        module.vision_estimator,
        "describe",
        lambda *_a, **_kw: (_ for _ in ()).throw(module.vision_estimator.VisionEstimatorError("provider down")),
    )
    _stub_parser(monkeypatch, module, estimate={
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
    })
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: {"client_id": r["client_id"], **r})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "protein shake",
            "client_id": "meal-vision-fallback-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["estimate"]["source"] == "ai_text_estimate"
    assert body["vision_error"] == "provider down"


def test_meal_intake_image_provider_failure_without_text_returns_clear_error(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(
        module.vision_estimator,
        "describe",
        lambda *_a, **_kw: (_ for _ in ()).throw(module.vision_estimator.VisionEstimatorError("provider down")),
    )

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-vision-error-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 503
    body = res.get_json()
    assert "Add a meal description" in body["error"]["message"]
    assert body["photo_retention"]["image_received"] is True


def test_meal_intake_undo_calls_delete_helper(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    seen = {}

    def fake_delete(user_id, client_id):
        seen["user_id"] = user_id
        seen["client_id"] = client_id
        return True

    monkeypatch.setattr(module, "delete_food_log_by_client_id", fake_delete)

    res = module.app.test_client().delete("/api/meal-intake/meal-undo-1")
    assert res.status_code == 200
    body = res.get_json()
    assert body == {"status": "ok", "removed": True}
    assert seen == {"user_id": 1, "client_id": "meal-undo-1"}


def test_meal_intake_undo_returns_not_found_when_missing(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "delete_food_log_by_client_id", lambda *_a, **_kw: False)

    res = module.app.test_client().delete("/api/meal-intake/meal-missing")
    assert res.status_code == 200
    assert res.get_json() == {"status": "not_found", "removed": False}


def test_meal_intake_accept_persists_estimate(monkeypatch):
    module = _client(monkeypatch)
    captured = {}

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {"client_id": record["client_id"], "calories": record["calories"], "source": record["source"]}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake/meal-accept-1/accept",
        json={
            "estimate": {
                "item_name": "Popcorn",
                "portion_description": "half bag",
                "meal_type": "snack",
                "calories": 180,
                "protein_g": 3,
                "carbs_g": 22,
                "fat_g": 9,
            },
            "text": "movie theater popcorn",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["food_log"]["calories"] == 180
    assert captured["correction_state"] == "accepted"
    assert captured["item_name"] == "Popcorn"
    assert captured["context_note"] == "movie theater popcorn"


def test_meal_intake_accept_preserves_offline_snapshot_provenance(monkeypatch):
    module = _client(monkeypatch)
    captured = {}
    snapshot_estimate = {
        "item_name": "Banana, raw",
        "portion_description": "100 g",
        "meal_type": "snack",
        "calories": 89,
        "protein_g": 1.1,
        "carbs_g": 22.8,
        "fat_g": 0.3,
        "sodium_mg": 1,
        "fiber_g": 2.6,
        "confidence": 0.62,
        "ambiguous": True,
        "uncertainty_notes": ["Offline snapshot uses a reference portion."],
        "source": "offline_snapshot",
        "external_food_id": "173944",
        "verified_source_url": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/173944/nutrients",
        "data_fetched_at": "2026-05-19T00:00:00",
        "portion_basis": "100 g USDA FoodData Central reference portion",
    }

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {"client_id": record["client_id"], **record}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)
    _stub_parser(
        monkeypatch,
        module,
        estimate=snapshot_estimate,
        source="offline_snapshot",
    )

    pending = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "banana", "client_id": "meal-snapshot-pending"},
        content_type="multipart/form-data",
    )
    assert pending.status_code == 200
    pending_estimate = pending.get_json()["estimate"]
    assert pending.get_json()["status"] == "pending_review"
    assert pending_estimate["external_food_id"] == "173944"
    assert pending_estimate["portion_basis"] == "100 g USDA FoodData Central reference portion"

    accepted = module.app.test_client().post(
        "/api/meal-intake/meal-snapshot-pending/accept",
        json={"estimate": pending_estimate, "text": "banana"},
    )
    assert accepted.status_code == 200
    assert captured["original_estimate"]["external_food_id"] == "173944"
    assert captured["original_estimate"]["verified_source_url"].endswith("/173944/nutrients")
    assert captured["original_estimate"]["data_fetched_at"] == "2026-05-19T00:00:00"
    assert captured["original_estimate"]["portion_basis"] == "100 g USDA FoodData Central reference portion"


def test_meal_intake_accept_requires_calories(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake/meal-bad-accept/accept",
        json={"estimate": {"item_name": "Smoothie"}},
    )
    assert res.status_code == 400
    assert "calories" in res.get_json()["error"]["message"]


def test_local_iso_from_iso_strips_offset_for_downstream_hour_reads():
    """Regression for Codex audit round 4: logged_at is read by
    _nutrition_entry_logged_hour via .hour without TZ conversion, so
    storing the raw UTC ISO misreports late-meal hour. The helper must
    return a naive server-local datetime string so .hour is local hour.
    """
    module = importlib.import_module("app")
    # Naive input: stays naive (already server-local by convention).
    result = module._local_iso_from_iso("2026-05-18T22:00:00")
    assert result is not None
    # The parsed value's hour should be 22 — that's the user's local hour.
    from datetime import datetime as _dt
    parsed = _dt.fromisoformat(result)
    assert parsed.tzinfo is None, "stored ISO must be naive (no offset suffix)"
    assert parsed.hour == 22

    # UTC input: converts to server-local before stripping tzinfo.
    result_utc = module._local_iso_from_iso("2026-05-19T03:00:00+00:00")
    assert result_utc is not None
    parsed_utc = _dt.fromisoformat(result_utc)
    assert parsed_utc.tzinfo is None
    # Hour depends on the test server's TZ — just assert it's a valid hour.
    assert 0 <= parsed_utc.hour <= 23


def test_local_iso_from_iso_returns_none_for_invalid():
    module = importlib.import_module("app")
    assert module._local_iso_from_iso(None) is None
    assert module._local_iso_from_iso("") is None
    assert module._local_iso_from_iso("not a date") is None


def test_meal_intake_text_stores_logged_at_as_naive_local(monkeypatch):
    """End-to-end check: a UTC local_timestamp submitted via the endpoint
    is persisted as a naive ISO (no offset) so downstream local-hour
    extractors see the user's actual hour.
    """
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Protein shake",
        "portion_description": None,
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    captured = {}

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {"client_id": record["client_id"], "logged_at": record["logged_at"]}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "protein shake",
            "client_id": "meal-naive-1",
            "local_timestamp": "2026-05-19T03:00:00+00:00",
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    logged_at = captured["logged_at"]
    from datetime import datetime as _dt
    parsed = _dt.fromisoformat(logged_at)
    assert parsed.tzinfo is None, (
        f"logged_at must be naive server-local (no tzinfo): {logged_at!r}"
    )


def test_local_date_from_iso_returns_none_for_empty_or_invalid():
    module = importlib.import_module("app")
    assert module._local_date_from_iso(None) is None
    assert module._local_date_from_iso("") is None
    assert module._local_date_from_iso("   ") is None
    assert module._local_date_from_iso("not a date") is None
    assert module._local_date_from_iso("2026-99-99T00:00:00Z") is None


def test_local_date_from_iso_handles_naive_input():
    module = importlib.import_module("app")
    assert module._local_date_from_iso("2026-05-18T23:55:30") == "2026-05-18"
    assert module._local_date_from_iso("2026-05-19T02:30:00") == "2026-05-19"


def test_local_date_from_iso_parses_utc_z_suffix():
    """The composer emits ``new Date().toISOString()`` which ends in Z.
    The helper must accept that without raising and return a YYYY-MM-DD.
    """
    module = importlib.import_module("app")
    result = module._local_date_from_iso("2026-05-19T03:00:00.000Z")
    assert result is not None
    assert len(result) == 10 and result[4] == "-" and result[7] == "-"


def test_local_date_from_iso_parses_explicit_offset():
    """When the timestamp carries an explicit offset, parsing must
    succeed and the returned date is the server-local conversion.
    """
    module = importlib.import_module("app")
    # 23:00 UTC === 18:00 CT (-05) === same calendar day in any negative
    # offset down to about -23. Just assert parsing succeeds and produces
    # a well-shaped date.
    result = module._local_date_from_iso("2026-05-18T23:00:00+00:00")
    assert result is not None
    assert len(result) == 10


def test_meal_intake_text_preserves_client_local_timestamp(monkeypatch):
    """Regression for Codex audit round 2: the composer already sends
    `local_timestamp` (static/js/app.js); the endpoint must honor it as
    `logged_at`/`source_timestamp` rather than stamping server time.
    FIT-59 acceptance: optional local timestamp.
    """
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Protein shake",
        "portion_description": None,
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    captured = {}

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {
            "client_id": record["client_id"],
            "logged_at": record["logged_at"],
            "source_timestamp": record["source_timestamp"],
            "date": record["date"],
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    # Use an explicit-offset timestamp where the local-converted hour
    # depends on the server TZ; pick a value where the converted date is
    # 2026-05-18 in any reasonable TZ (UTC midnight is still that day in
    # any non-positive offset, and 23:55 UTC is still that day in any
    # offset down to about -23).
    local_ts = "2026-05-18T23:55:30.000Z"
    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "protein shake",
            "client_id": "meal-ts-1",
            "local_timestamp": local_ts,
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    # Storage normalizes to naive server-local ISO (round-4 fix). Don't
    # require byte-equality with the UTC input; require that the value
    # is honored (non-empty, naive) and that the date derives correctly.
    from datetime import datetime as _dt
    parsed_logged_at = _dt.fromisoformat(captured["logged_at"])
    assert parsed_logged_at.tzinfo is None
    assert captured["source_timestamp"] == captured["logged_at"]
    assert captured["date"] == "2026-05-18", "date must derive from local_timestamp"


def test_meal_intake_text_falls_back_to_server_time_when_local_timestamp_absent(monkeypatch):
    """When the client does not send a local_timestamp, persistence
    should fall back to server now (existing behavior preserved)."""
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Protein shake",
        "portion_description": None,
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    captured = {}

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {"client_id": record["client_id"], "logged_at": record["logged_at"]}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "protein shake", "client_id": "meal-ts-noop"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert captured["logged_at"]  # server-stamped, non-empty
    # logged_at should not be empty and should match source_timestamp
    assert captured["source_timestamp"] == captured["logged_at"]


def test_meal_intake_text_rejects_oversize_local_timestamp(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "yogurt",
            "client_id": "meal-ts-big",
            "local_timestamp": "x" * 65,
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    assert "local_timestamp" in res.get_json()["error"]["message"]


def test_meal_intake_text_passes_local_timestamp_through_to_parser(monkeypatch):
    """The parser receives ``timestamp=`` so it can future-use the signal
    (e.g. meal-type inference). FIT-59 acceptance.
    """
    module = _client(monkeypatch)
    seen = {}

    def fake_parse(text, *, timestamp=None, user_id=None):
        seen["text"] = text
        seen["timestamp"] = timestamp
        seen["user_id"] = user_id
        return {
            "estimate": {
                "item_name": "Yogurt", "portion_description": None,
                "meal_type": "snack", "calories": 180, "protein_g": 14,
                "carbs_g": 22, "fat_g": 4, "sodium_mg": 90, "fiber_g": 1,
                "confidence": 0.8, "ambiguous": False, "uncertainty_notes": [],
                "source": "ai_text_estimate",
            },
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parse)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {"client_id": "meal-ts-3"})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "yogurt",
            "client_id": "meal-ts-3",
            "local_timestamp": "2026-05-19T08:15:00",
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert seen["text"] == "yogurt"
    assert seen["timestamp"] == "2026-05-19T08:15:00"
    assert seen["user_id"] == 1


def test_meal_intake_accept_persists_parser_source_when_present(monkeypatch):
    """Regression for Codex audit round 1, finding 3: when a pending-review
    estimate originated from the FIT-59 parser (text path), the accept
    handler must persist the parser-assigned ``source`` from the estimate
    rather than defaulting to ``stub_text_estimate``.
    """
    module = _client(monkeypatch)
    captured = {}

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {
            "client_id": record["client_id"],
            "calories": record["calories"],
            "source": record["source"],
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake/meal-accept-ai/accept",
        json={
            "estimate": {
                "item_name": "Popcorn",
                "meal_type": "snack",
                "calories": 180,
                "protein_g": 3,
                "carbs_g": 22,
                "fat_g": 9,
                "source": "ai_text_estimate",
            },
            "text": "movie theater popcorn",
        },
    )
    assert res.status_code == 200
    assert captured["source"] == "ai_text_estimate", (
        "accept handler must honor parser-assigned source, not default to stub"
    )


# ──────────────────────────────────────────────────────────────────
# FIT-61: meal-log policy integration
# ──────────────────────────────────────────────────────────────────

def test_meal_intake_response_surfaces_policy_band_and_reasons(monkeypatch):
    """The response must include policy.confidence_band and policy.reasons
    so the UI can explain auto-log vs pending review decisions.
    """
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Eggs and toast",
        "portion_description": None,
        "meal_type": "breakfast",
        "calories": 420,
        "protein_g": 24,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 4,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {
        "client_id": "meal-policy-1", "correction_state": "accepted",
    })

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "eggs and toast", "client_id": "meal-policy-1"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["policy"]["confidence_band"] == "high"
    assert body["policy"]["reasons"] == [], "high-confidence auto-log has no policy reasons"


def test_meal_intake_implausible_macros_force_pending_review(monkeypatch):
    """Even at high confidence, macro-out-of-range estimates must be held
    for review. FIT-61 policy: backend enforces, not UI copy.
    """
    module = _client(monkeypatch)
    # Parser returns plausible-looking confidence but absurd calories.
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Mystery dish",
        "portion_description": None,
        "meal_type": "dinner",
        "calories": 9999,  # implausible — > 5000 cap
        "protein_g": 30,
        "carbs_g": 50,
        "fat_g": 25,
        "sodium_mg": 600,
        "fiber_g": 5,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    persisted = []
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: (persisted.append(r), r)[1])

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "huge dinner", "client_id": "meal-implausible-1"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert "implausible_calories" in body["policy"]["reasons"]
    assert persisted == [], "pending estimates must not auto-persist"
    assert body["food_log"] is None


def test_meal_intake_medium_confidence_falls_to_pending(monkeypatch):
    """Confidence 0.6 (the FIT-59 deterministic-fallback ceiling) must
    fall to pending review under FIT-61's 0.75 auto-log threshold."""
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Eggs and toast",
        "portion_description": None,
        "meal_type": "breakfast",
        "calories": 420,
        "protein_g": 24,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 4,
        "confidence": 0.60,
        "ambiguous": False,
        "uncertainty_notes": [],
    }, source="fallback_text_estimate", fallback_used=True)
    persisted = []
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: (persisted.append(r), r)[1])

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "eggs and toast", "client_id": "meal-medium-1"},
        content_type="multipart/form-data",
    )
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["policy"]["confidence_band"] == "medium"
    assert "medium_confidence" in body["policy"]["reasons"]
    assert persisted == [], "medium-confidence estimates must not auto-persist"
    assert body["food_log"] is None


def test_meal_intake_auto_log_persists_with_accepted_state(monkeypatch):
    """The end-to-end auto-log path must persist correction_state="accepted"."""
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Protein shake",
        "portion_description": None,
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.90,
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    persisted = []
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: (persisted.append(r), r)[1])

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "protein shake", "client_id": "meal-accept-1"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert res.get_json()["status"] == "logged"
    assert persisted[0]["correction_state"] == "accepted"


# ──────────────────────────────────────────────────────────────────
# FIT-61: coaching context excludes pending entries
# ──────────────────────────────────────────────────────────────────

def test_nutrition_today_excludes_pending_review_entries_from_totals(monkeypatch):
    """The /api/nutrition-today totals must reflect only accepted entries.
    Pending entries (correction_state="pending_review") are tracked in
    pending_review_count but never roll into calories/protein/etc.
    This is what makes "save as pending review" actually safe.
    """
    module = _client(monkeypatch)
    today = module._today_str()
    # Mix: one accepted (counts), one pending (doesn't), one legacy (counts).
    fake_logs = [
        {
            "id": 1, "client_id": "accepted-1", "date": today,
            "logged_at": f"{today}T08:00:00",
            "calories": 400, "protein_g": 25, "carbs_g": 40, "fat_g": 15,
            "sodium_mg": 500, "fiber_g": 4,
            "correction_state": "accepted",
        },
        {
            "id": 2, "client_id": "pending-1", "date": today,
            "logged_at": f"{today}T12:00:00",
            "calories": 800, "protein_g": 30, "carbs_g": 90, "fat_g": 25,
            "sodium_mg": 1200, "fiber_g": 8,
            "correction_state": "pending_review",
        },
        {
            "id": 3, "client_id": "legacy-1", "date": today,
            "logged_at": f"{today}T15:00:00",
            "calories": 200, "protein_g": 10, "carbs_g": 20, "fat_g": 8,
            "sodium_mg": 200, "fiber_g": 2,
            "correction_state": "manual",
        },
    ]
    monkeypatch.setattr(module, "get_food_logs",
                        lambda _u, limit=None, since=None: list(fake_logs))

    res = module.app.test_client().get("/api/nutrition-today")
    assert res.status_code == 200
    body = res.get_json()
    # Accepted (400) + manual (200) = 600. Pending (800) must NOT count.
    assert body["calories"] == 600, (
        f"pending entries leaked into totals: got {body['calories']}, expected 600"
    )
    assert body["protein_g"] == 35  # 25 + 10


def test_meal_intake_pending_response_surfaces_policy_reasons_as_notes(monkeypatch):
    """Regression for Codex audit round 2 (FIT-61): the composer reads
    ``estimate.uncertainty_notes`` for the pending-review card. Policy-only
    pending decisions (medium_confidence, implausible_macros, etc.) must
    be translated into human-readable notes so the user sees an
    explanation — not just an empty card with no rationale.
    """
    module = _client(monkeypatch)
    # Estimate that the parser would consider clean — confidence 0.60
    # lands in the new MEDIUM band, where uncertainty_notes from the
    # parser is empty. The policy must add a note so the UI can render it.
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Eggs and toast",
        "portion_description": None,
        "meal_type": "breakfast",
        "calories": 420,
        "protein_g": 24,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 4,
        "confidence": 0.60,
        "ambiguous": False,
        "uncertainty_notes": [],  # parser sees no ambiguity
    }, source="fallback_text_estimate", fallback_used=True)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "eggs and toast", "client_id": "meal-policy-notes-1"},
        content_type="multipart/form-data",
    )
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert "medium_confidence" in body["policy"]["reasons"]
    notes = body["estimate"]["uncertainty_notes"]
    assert notes, "policy reasons must be translated into uncertainty_notes for the UI"
    # The note text should reference confidence so the user understands.
    assert any("confidence" in note.lower() or "double-check" in note.lower() for note in notes)


def test_meal_intake_policy_notes_do_not_duplicate_existing_uncertainty(monkeypatch):
    """When the parser already surfaced an ambiguity note, the policy's
    translated note for the same code must not be duplicated.
    """
    module = _client(monkeypatch)
    existing_note = "Portion or items are unclear — confirm before it counts."
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Popcorn",
        "portion_description": None,
        "meal_type": "snack",
        "calories": 200,
        "protein_g": 4,
        "carbs_g": 24,
        "fat_g": 12,
        "sodium_mg": 300,
        "fiber_g": 4,
        "confidence": 0.50,
        "ambiguous": True,
        "uncertainty_notes": [existing_note],
    })
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "shared movie popcorn", "client_id": "meal-policy-dedup-1"},
        content_type="multipart/form-data",
    )
    notes = res.get_json()["estimate"]["uncertainty_notes"]
    lowered = [n.lower().strip() for n in notes]
    # The parser's "unclear" note and the policy's "unclear" note share
    # the same content; assert only one copy.
    assert lowered.count(existing_note.lower().strip()) == 1


def test_meal_intake_image_with_ambiguous_text_falls_to_pending(monkeypatch):
    """Regression for Codex audit round 1, finding 1: ``_meal_intake_stub_estimate``
    detected ambiguity for image+text submissions (e.g. "shared movie popcorn"
    with a photo) but only exposed it via result["status"] and uncertainty_notes.
    Since FIT-61 evaluates only ``estimate``, the ``ambiguous`` flag must live
    inside the estimate dict so the policy sees it and routes to pending review.
    """
    module = _client(monkeypatch)
    persisted = []
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "claude",
            "item_description": "shared movie popcorn",
            "portion_hint": "shared tub",
            "confidence": 0.45,
            "ambiguous": True,
            "uncertainty_notes": ["Portion is unclear."],
            "macro_estimate": {
                "item_name": "Shared popcorn",
                "meal_type": "snack",
                "calories": 300,
                "protein_g": 5,
                "carbs_g": 36,
                "fat_g": 18,
                "sodium_mg": 520,
                "fiber_g": 6,
            },
        },
        lookup=None,
    )
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: (persisted.append(r), r)[1])

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "shared movie popcorn",
            "client_id": "meal-ambig-img-1",
            "image": (io.BytesIO(image_bytes), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "pending_review", (
        "ambiguous photo+text submissions must be held for review"
    )
    assert body["estimate"]["ambiguous"] is True
    assert "ambiguous_input" in body["policy"]["reasons"]
    assert body["photo_retention"]["image_received"] is True
    assert body["photo_retention"]["raw_model_trace_retained"] is False
    assert persisted == [], "ambiguous estimates must not auto-persist"
    assert body["food_log"] is None

    accept = module.app.test_client().post(
        "/api/meal-intake/meal-ambig-img-1/accept",
        json={"estimate": body["estimate"], "text": "shared movie popcorn"},
    )
    accepted = accept.get_json()
    assert accept.status_code == 200
    assert accepted["photo_retention"]["image_received"] is True


def test_nutrition_today_surfaces_pending_review_count(monkeypatch):
    """Pending entries must be counted separately so freshness UI can
    surface them ("3 meals awaiting review")."""
    module = _client(monkeypatch)
    today = module._today_str()
    fake_logs = [
        {"id": 1, "client_id": "a", "date": today, "logged_at": f"{today}T08:00:00",
         "calories": 400, "protein_g": 25, "correction_state": "accepted"},
        {"id": 2, "client_id": "b", "date": today, "logged_at": f"{today}T12:00:00",
         "calories": 800, "protein_g": 30, "correction_state": "pending_review"},
        {"id": 3, "client_id": "c", "date": today, "logged_at": f"{today}T13:00:00",
         "calories": 100, "protein_g": 5, "correction_state": "pending_review"},
    ]
    monkeypatch.setattr(module, "get_food_logs",
                        lambda _u, limit=None, since=None: list(fake_logs))

    res = module.app.test_client().get("/api/nutrition-today")
    body = res.get_json()
    # The coaching_context block exposes pending_review_count.
    ctx = body.get("coaching_context") or {}
    assert ctx.get("pending_review_count") == 2, (
        f"expected 2 pending entries, got {ctx.get('pending_review_count')}"
    )
