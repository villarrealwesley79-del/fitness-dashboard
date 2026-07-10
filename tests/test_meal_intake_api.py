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
import tempfile
from pathlib import Path

import data_store


def _client(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit65-meal-intake-secret")
    db_path = Path(tempfile.mkdtemp(prefix="fit-meal-intake-api-")) / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module.personal_vocab, "record_accept", lambda *_a, **_kw: None)
    monkeypatch.setattr(module.personal_vocab, "record_correct", lambda *_a, **_kw: None)
    return module


def _isolated_food_log_db(monkeypatch, tmp_path):
    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()
    return db_path


def _add_food_log(client_id: str, *, user_id: int = 1, calories: int = 500):
    return data_store.add_food_log(
        user_id,
        {
            "client_id": client_id,
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": "Lunch",
            "calories": calories,
            "protein_g": 30,
        },
    )


def _accepted_estimate(**overrides):
    estimate = {
        "item_name": "Chicken bowl",
        "portion_description": "1 bowl",
        "meal_type": "lunch",
        "calories": 500,
        "protein_g": 35,
        "carbs_g": 45,
        "fat_g": 18,
        "sodium_mg": 700,
        "fiber_g": 6,
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "manual_review_estimate",
    }
    estimate.update(overrides)
    return estimate


def _stub_parser(
    monkeypatch,
    module,
    *,
    estimate,
    source="ai_text_estimate",
    fallback_used=False,
    fallback_reason=None,
):
    """Replace meal_text_parser.parse_meal_text with a deterministic stub
    so endpoint tests exercise the wiring, not the parser internals.

    ``source`` is injected inside the returned estimate dict to match the
    real parser shape — provenance lives in the estimate so it round-trips
    through the /api/meal-intake/<client_id>/accept handler.
    """
    def fake(_text, **_kw):
        e = dict(estimate)
        e["source"] = source
        parsed = {"estimate": e, "fallback_used": fallback_used}
        if fallback_reason:
            parsed["fallback_reason"] = fallback_reason
        return parsed

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


def test_meal_intake_text_only_returns_pending_review_when_parser_is_confident(monkeypatch):
    """FIT-144 v2 capture: every text submission lands as pending_review and
    is finalized via /accept. The legacy high-confidence auto-log shortcut
    was removed by the FIT-138 + FIT-144 work."""
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
    assert body["status"] == "pending_review"
    assert body["estimate"]["item_name"] == "Eggs and toast"
    assert body["estimate"]["calories"] > 0
    assert body["food_log"]["client_id"] == "meal-abc-1"
    assert body["fallback_used"] is False
    assert "stub" not in body, "real text path must not advertise stub: true"
    assert persisted["correction_state"] == "pending_review"
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
        "off_attribution": "Source: Open Food Facts (ODbL/DbCL data; product images CC BY-SA)",
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
    assert "verified_source_url" not in str(body)
    assert "CC BY-SA" in body["estimate"]["off_attribution"]
    assert "ODbL/DbCL data" in body["estimate"]["off_attribution"]
    assert "verified_source_url" not in str(persisted["original_estimate"])
    assert "CC BY-SA" in persisted["original_estimate"]["off_attribution"]
    assert "ODbL/DbCL data" in persisted["original_estimate"]["off_attribution"]


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
    assert persisted[0]["correction_state"] == "pending_review"
    assert persisted[0]["client_id"] == "meal-pending-1"
    assert body["food_log"]["client_id"] == "meal-pending-1"
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
    assert persisted[0]["correction_state"] == "pending_review"
    assert body["food_log"]["client_id"] == "meal-fb-1"


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


def test_meal_intake_pending_retry_does_not_downgrade_accepted_row(monkeypatch):
    """A stale retry of the original pending POST must not undo accept."""
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Popcorn",
        "portion_description": "shared",
        "meal_type": "snack",
        "calories": 300,
        "protein_g": 5,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 6,
        "confidence": 0.45,
        "ambiguous": True,
        "uncertainty_notes": ["Portion unclear."],
    })
    accepted_row = {
        "client_id": "meal-retry-accepted",
        "correction_state": "accepted",
        "item_name": "Popcorn",
        "calories": 300,
    }
    monkeypatch.setattr(module, "get_food_logs", lambda *_a, **_kw: [accepted_row])

    def fail_add_food_log(*_args, **_kwargs):
        raise AssertionError("accepted retry must not be upserted back to pending")

    monkeypatch.setattr(module, "add_food_log", fail_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "shared movie popcorn", "client_id": "meal-retry-accepted"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["food_log"]["correction_state"] == "accepted"


def test_meal_intake_late_parser_result_preserves_concurrent_accept(monkeypatch):
    """A parser that finishes after accept must replay the terminal row."""
    module = _client(monkeypatch)
    client_id = "meal-race-accepted"
    today = module._today_str()

    def slow_parser(_text, **_kwargs):
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "date": today,
                "logged_at": f"{today}T12:00:00",
                "item_name": "Accepted chicken bowl",
                "calories": 550,
                "protein_g": 42,
                "carbs_g": 48,
                "fat_g": 20,
                "sodium_mg": 780,
                "fiber_g": 7,
                "correction_state": "accepted",
                "source": "manual_review_estimate",
            },
        )
        data_store.save_meal_acceptance_event(
            1,
            meal_id=client_id,
            status="accepted",
            included_client_ids=[client_id],
            skipped_count=0,
            deleted_count=0,
        )
        return {
            "estimate": _accepted_estimate(
                item_name="Late estimate",
                calories=300,
                protein_g=5,
                carbs_g=36,
                fat_g=18,
                confidence=0.45,
                ambiguous=True,
                uncertainty_notes=["Portion unclear."],
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", slow_parser)
    client = module.app.test_client()
    response = client.post(
        "/api/meal-intake",
        data={"text": "slow chicken estimate", "client_id": client_id},
        content_type="multipart/form-data",
    )
    late_snapshot = data_store.save_meal_review_snapshot(
        1,
        meal_id=client_id,
        payload={"status": "pending_review", "food_log": {"client_id": client_id}},
        next_item_seq=1,
    )
    today_response = client.get("/api/nutrition-today")

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["status"] == "logged"
    assert body["food_log"]["correction_state"] == "accepted"
    assert body["food_log"]["item_name"] == "Accepted chicken bowl"
    assert body["food_log"]["calories"] == 550
    assert late_snapshot["payload"]["status"] == "pending_review"
    assert data_store.get_meal_review_snapshot(1, client_id) is None
    assert data_store.get_food_logs(1)[0]["correction_state"] == "accepted"
    assert today_response.get_json()["calories"] == 550


def test_food_log_terminal_rows_reject_pending_but_allow_terminal_refresh(monkeypatch):
    module = _client(monkeypatch)
    client_id = "terminal-refresh-row"
    today = module._today_str()

    def save(calories, correction_state):
        return data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "date": today,
                "logged_at": f"{today}T12:00:00",
                "item_name": "Terminal refresh row",
                "calories": calories,
                "protein_g": 20,
                "carbs_g": 30,
                "fat_g": 10,
                "sodium_mg": 400,
                "fiber_g": 5,
                "correction_state": correction_state,
                "source": "verified_lookup",
            },
        )

    assert save(550, "accepted")["correction_state"] == "accepted"
    assert save(300, "pending_review")["calories"] == 550
    assert save(600, "corrected")["calories"] == 600
    assert save(300, "pending_review")["correction_state"] == "corrected"
    assert save(650, "accepted")["calories"] == 650


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


def test_meal_intake_text_response_includes_public_fallback_reason(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate={
            "item_name": "Eggs and toast",
            "portion_description": None,
            "meal_type": "breakfast",
            "calories": 420,
            "protein_g": 24,
            "carbs_g": 36,
            "fat_g": 18,
            "sodium_mg": 520,
            "fiber_g": 4,
            "confidence": 0.55,
            "ambiguous": True,
            "uncertainty_notes": ["Rough estimate — AI didn't run; review before logging."],
        },
        source="fallback_text_estimate",
        fallback_used=True,
        fallback_reason="all_endpoints_failed",
    )
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {
        "client_id": "meal-fallback-reason-1", "id": 1,
    })

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "two eggs and toast", "client_id": "meal-fallback-reason-1"},
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["fallback_used"] is True
    assert body["fallback_reason"] == "all_endpoints_failed"
    assert body["fallback_reason"] in module.FALLBACK_REASON_VALUES
    assert "qwen" not in body["fallback_reason"]


def test_review_estimate_from_text_preserves_public_fallback_reason(monkeypatch):
    module = _client(monkeypatch)

    def fake_parse(_text, **_kw):
        return {
            "fallback_used": True,
            "fallback_reason": "timeout",
            "estimate": {
                "item_name": "Eggs and toast",
                "portion_description": None,
                "meal_type": "breakfast",
                "calories": 420,
                "protein_g": 24,
                "carbs_g": 36,
                "fat_g": 18,
                "sodium_mg": 520,
                "fiber_g": 4,
                "confidence": 0.55,
                "ambiguous": True,
                "uncertainty_notes": ["Rough estimate — AI didn't run; review before logging."],
                "source": "fallback_text_estimate",
            },
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parse)

    estimate = module._review_estimate_from_text("two eggs and toast", user_id=1)

    assert estimate["source"] == "fallback_text_estimate"
    assert estimate["fallback_reason"] == "timeout"
    item = module._review_item_from_estimate(
        estimate,
        item_id="item-1",
        item_order=1,
        status="included",
        text="two eggs and toast",
    )
    assert item["estimate"]["fallback_reason"] == "timeout"


def test_review_sanitize_estimate_drops_malformed_fallback_reason(monkeypatch):
    module = _client(monkeypatch)

    estimate = module._review_sanitize_estimate({
        "item_name": "Eggs and toast",
        "portion_description": None,
        "meal_type": "breakfast",
        "calories": 420,
        "protein_g": 24,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 4,
        "confidence": 0.55,
        "ambiguous": True,
        "uncertainty_notes": ["Rough estimate — AI didn't run; review before logging."],
        "source": "fallback_text_estimate",
        "fallback_reason": ["timeout"],
    })

    assert estimate["source"] == "fallback_text_estimate"
    assert "fallback_reason" not in estimate


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


def test_meal_intake_image_only_returns_pending_review(monkeypatch):
    """FIT-144 v2 capture: image-only submissions land as pending_review."""
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
    assert body["status"] == "pending_review"
    assert body["estimate"]["confidence"] >= 0.7
    assert body["photo_retention"]["policy"] == "discard_after_extraction"
    assert body["photo_retention"]["raw_photo_retained"] is False
    assert body["photo_retention"]["backup_includes_raw_photo"] is False
    assert "image_bytes" not in str(body)
    assert "plate.png" not in str(body)
    assert captured["source"] == "vision_claude+nutritionix"
    assert captured["context_note"] is None


def test_meal_intake_bill_miller_cart_uses_structured_item_lookups(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 42)
    lookup_queries = []
    persisted = {}
    vocab_calls = []
    vision = {
        "provider": "lm_studio",
        "item_description": (
            "Bill Miller BBQ order: 1 Bacon & Egg Taco with Hot Sauce on a Flour Tortilla; "
            "2 Breakfast Sandwiches on Biscuit with Sausage Patty, Cheese, and Egg"
        ),
        "portion_hint": "1 taco and 2 breakfast sandwiches",
        "confidence": 0.91,
        "ambiguous": False,
        "uncertainty_notes": [],
        "items": [
            {
                "brand": "Bill Miller BBQ",
                "item_name": "Bacon & Egg Taco",
                "quantity": 1,
                "modifiers": ["Hot Sauce", "Flour Tortilla"],
            },
            {
                "brand": "Bill Miller BBQ",
                "item_name": "Breakfast Sandwich",
                "quantity": 2,
                "modifiers": ["On a Biscuit", "Sausage Patty", "Cheese", "Egg"],
            },
        ],
    }
    monkeypatch.setattr(module.vision_estimator, "describe", lambda *_a, **_kw: dict(vision))

    def fake_lookup(text, **kwargs):
        lookup_queries.append((text, kwargs.get("user_id")))
        if "Bacon & Egg Taco" in text:
            return {
                "item_name": "Bill Miller BBQ Bacon & Egg Taco",
                "portion_description": "1 taco",
                "meal_type": "breakfast",
                "calories": 330,
                "protein_g": 16,
                "carbs_g": 28,
                "fat_g": 18,
                "sodium_mg": 780,
                "fiber_g": 2,
                "confidence": 0.86,
                "ambiguous": False,
                "uncertainty_notes": [],
                "source": "nutritionix",
            }
        if "Breakfast Sandwich" in text:
            return {
                "item_name": "Bill Miller BBQ Breakfast Sandwich",
                "portion_description": "2 sandwiches",
                "meal_type": "breakfast",
                "calories": 960,
                "protein_g": 42,
                "carbs_g": 72,
                "fat_g": 56,
                "sodium_mg": 1880,
                "fiber_g": 4,
                "confidence": 0.82,
                "ambiguous": False,
                "uncertainty_notes": [],
                "source": "nutritionix",
            }
        return None

    monkeypatch.setattr(module.branded_food_lookup, "lookup", fake_lookup)
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: persisted.setdefault("record", record) or {"client_id": record["client_id"], **record})
    monkeypatch.setattr(module.personal_vocab, "record_accept", lambda *args, **_kw: vocab_calls.append(args))
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: True)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "Bill Miller",
            "client_id": "meal-bill-miller-cart-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\0" * 32), "bill-miller.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert lookup_queries == [
        ("1 Bill Miller BBQ Bacon & Egg Taco Hot Sauce Flour Tortilla", 42),
        ("2 Bill Miller BBQ Breakfast Sandwich On a Biscuit Sausage Patty Cheese Egg", 42),
    ]
    estimate = body["estimate"]
    assert estimate["source"] == "vision_lm_studio+nutritionix"
    assert estimate["item_name"] == "Bill Miller BBQ order: 1 Bacon & Egg Taco; 2 Breakfast Sandwich"
    assert estimate["portion_description"] == (
        "1 Bacon & Egg Taco (Hot Sauce, Flour Tortilla); "
        "2 Breakfast Sandwich (On a Biscuit, Sausage Patty, Cheese, Egg)"
    )
    assert estimate["calories"] == 1290
    assert estimate["protein_g"] == 58
    assert estimate["sodium_mg"] == 2660
    assert persisted["record"]["source"] == "vision_lm_studio+nutritionix"
    assert persisted["record"]["original_estimate"]["vision_description"].startswith("Bill Miller BBQ order")
    assert "image_bytes" not in str(persisted["record"]["original_estimate"])
    assert vocab_calls == []


def test_meal_intake_single_structured_item_applies_top_level_portion_hint(monkeypatch):
    module = _client(monkeypatch)
    lookup_queries = []
    vision = {
        "provider": "lm_studio",
        "item_description": "half burger",
        "portion_hint": "half burger",
        "confidence": 0.9,
        "ambiguous": False,
        "uncertainty_notes": [],
        "items": [{"item_name": "burger", "quantity": 1}],
    }
    monkeypatch.setattr(module.vision_estimator, "describe", lambda *_a, **_kw: dict(vision))
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: False)

    def fake_lookup(text, **_kwargs):
        lookup_queries.append(text)
        if text == "1 burger half burger":
            return {
                "item_name": "Half burger",
                "portion_description": "half burger",
                "meal_type": "lunch",
                "calories": 280,
                "protein_g": 14,
                "carbs_g": 16,
                "fat_g": 17,
                "sodium_mg": 520,
                "fiber_g": 1,
                "confidence": 0.84,
                "ambiguous": False,
                "uncertainty_notes": [],
                "source": "nutritionix",
            }
        return {
            "item_name": "Full burger",
            "portion_description": "1 burger",
            "meal_type": "lunch",
            "calories": 560,
            "protein_g": 28,
            "carbs_g": 32,
            "fat_g": 34,
            "sodium_mg": 1040,
            "fiber_g": 2,
            "confidence": 0.84,
            "ambiguous": False,
            "uncertainty_notes": [],
            "source": "nutritionix",
        }

    monkeypatch.setattr(module.branded_food_lookup, "lookup", fake_lookup)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "half",
            "client_id": "meal-half-burger-photo",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\0" * 32), "half-burger.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert lookup_queries == ["1 burger half burger"]
    assert body["estimate"]["calories"] == 280
    assert body["estimate"]["portion_description"] == "1 burger (half burger)"


def test_meal_intake_single_structured_item_applies_user_portion_modifier(monkeypatch):
    module = _client(monkeypatch)
    lookup_queries = []
    vision = {
        "provider": "lm_studio",
        "item_description": "Chipotle chicken burrito",
        "portion_hint": "1 burrito",
        "confidence": 0.9,
        "ambiguous": False,
        "uncertainty_notes": [],
        "items": [{"brand": "Chipotle", "item_name": "chicken burrito", "quantity": 1}],
    }
    monkeypatch.setattr(module.vision_estimator, "describe", lambda *_a, **_kw: dict(vision))
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: False)

    def fake_lookup(text, **_kwargs):
        lookup_queries.append(text)
        return {
            "item_name": "Half Chipotle chicken burrito",
            "portion_description": "half burrito",
            "meal_type": "lunch",
            "calories": 380,
            "protein_g": 22,
            "carbs_g": 38,
            "fat_g": 15,
            "sodium_mg": 760,
            "fiber_g": 4,
            "confidence": 0.84,
            "ambiguous": False,
            "uncertainty_notes": [],
            "source": "nutritionix",
        }

    monkeypatch.setattr(module.branded_food_lookup, "lookup", fake_lookup)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "half Chipotle chicken burrito",
            "client_id": "meal-half-chipotle-photo",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\0" * 32), "half-chipotle.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert lookup_queries == ["1 Chipotle chicken burrito 1 burrito half"]
    assert body["estimate"]["calories"] == 380
    assert body["estimate"]["portion_description"] == "1 chicken burrito (1 burrito half)"


def test_meal_intake_structured_cart_truncated_items_force_review(monkeypatch):
    module = _client(monkeypatch)
    lookup_queries = []
    vision = {
        "provider": "lm_studio",
        "item_description": "Large cart with nine breakfast tacos",
        "portion_hint": "9 tacos",
        "confidence": 0.91,
        "ambiguous": False,
        "uncertainty_notes": [],
        "items": [
            {"brand": "Bill Miller BBQ", "item_name": f"Breakfast Taco {idx}", "quantity": 1}
            for idx in range(1, 10)
        ],
    }
    monkeypatch.setattr(module.vision_estimator, "describe", lambda *_a, **_kw: dict(vision))
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: False)

    def fake_lookup(text, **_kwargs):
        lookup_queries.append(text)
        return {
            "item_name": text,
            "portion_description": "1 taco",
            "meal_type": "breakfast",
            "calories": 200,
            "protein_g": 10,
            "carbs_g": 20,
            "fat_g": 8,
            "sodium_mg": 420,
            "fiber_g": 2,
            "confidence": 0.84,
            "ambiguous": False,
            "uncertainty_notes": [],
            "source": "nutritionix",
        }

    monkeypatch.setattr(module.branded_food_lookup, "lookup", fake_lookup)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "Bill Miller",
            "client_id": "meal-large-cart-truncated",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\0" * 32), "large-cart.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert len(lookup_queries) == 8
    assert body["status"] == "pending_review"
    assert body["estimate"]["ambiguous"] is True
    assert "Breakfast Taco 9" in body["estimate"]["uncertainty_notes"][0]


def test_meal_intake_capture_does_not_fire_vocab_learning_under_fit138(monkeypatch):
    """FIT-138: vocab learning previously fired on the auto-log capture path.
    With the capture flow routed to review before save, vocab learning is
    handled by /accept instead and must NOT fire from /api/meal-intake."""
    module = _client(monkeypatch)
    _stub_vision(monkeypatch, module)
    vocab_calls = []
    claims = []
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})
    monkeypatch.setattr(module.personal_vocab, "record_accept", lambda *args, **_kw: vocab_calls.append(args))

    def fake_claim(_user_id, client_id):
        claims.append(client_id)
        return True

    monkeypatch.setattr(module, "claim_food_log_vocab_learning", fake_claim)

    client = module.app.test_client()
    for _ in range(2):
        res = module.app.test_client().post(
            "/api/meal-intake",
            data={
                "client_id": "meal-img-vocab-idempotent",
                "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\0" * 32), "plate.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        assert res.status_code == 200, res.get_data(as_text=True)

    # Capture path does not claim or record vocab — that work moves to /accept.
    assert claims == []
    accept = client.post("/api/meal-intake/meal-img-vocab-idempotent/accept", json={})
    assert accept.status_code == 200, accept.get_data(as_text=True)
    assert len(claims) == 1
    assert claims[0].startswith("meal-img-vocab-idempotent-item-")
    assert len(vocab_calls) == 1


def test_meal_intake_image_text_is_preserved_as_brand_hint(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 42)
    monkeypatch.setattr(module, "get_food_logs", lambda *_a, **_kw: [])
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
        captured["user_id"] = kwargs.get("user_id")
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
        "user_id": 42,
    }
    assert res.get_json()["estimate"]["item_name"] == "Chipotle chicken burrito"


def test_meal_intake_image_text_respects_direct_lookup_guard(monkeypatch):
    module = _client(monkeypatch)
    vision = {
        "provider": "claude",
        "item_description": "Chipotle chicken burrito",
        "portion_hint": "1 burrito",
        "confidence": 0.86,
        "ambiguous": False,
        "uncertainty_notes": [],
    }
    monkeypatch.setattr(module.vision_estimator, "describe", lambda *_a, **_kw: dict(vision))
    monkeypatch.setattr(
        module.branded_food_lookup,
        "lookup",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("unsafe image text must not direct lookup")),
    )
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "half Chipotle chicken burrito",
            "client_id": "meal-img-half-guard-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["estimate"]["source"] == "vision_claude_estimate"
    assert body["estimate"]["from_image"] is True


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
    assert body["food_log"]["correction_state"] == "pending_review"
    assert persisted[-1]["correction_state"] == "pending_review"

    accept = module.app.test_client().post(
        "/api/meal-intake/meal-img-low-confidence-1/accept",
        json={"estimate": body["estimate"], "text": "protein shake"},
    )

    assert accept.status_code == 200, accept.get_data(as_text=True)
    accepted_estimate = persisted[-1]["original_estimate"]
    assert accepted_estimate["external_food_id"] == "shake-1"
    assert "verified_source_url" not in str(accepted_estimate)
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
    assert body["food_log"]["correction_state"] == "pending_review"
    assert persisted[-1]["correction_state"] == "pending_review"


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
    assert body["food_log"]["correction_state"] == "pending_review"


def test_meal_intake_image_label_ocr_uses_higher_confidence_cap(monkeypatch):
    module = _client(monkeypatch)
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "lm_studio",
            "item_description": "Barbecue Jerky nutrition label",
            "portion_hint": "1 oz (28 g)",
            "confidence": 0.9,
            "ambiguous": False,
            "uncertainty_notes": [],
            "label_ocr": True,
            "macro_estimate": {
                "meal_type": "snack",
                "calories": 110,
                "protein_g": 7,
                "carbs_g": 6,
                "fat_g": 3,
                "sodium_mg": 520,
                "fiber_g": 0,
            },
        },
    )
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-img-label-ocr-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "label.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["estimate"]["source"] == "vision_lm_studio_label_ocr"
    assert body["estimate"]["confidence"] == 0.9
    assert body["estimate"]["portion_basis"] == "Photo nutrition-label OCR"
    assert body["estimate"]["calories"] == 110
    assert body["food_log"]["correction_state"] == "pending_review"


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
    assert body["status"] == "pending_review"
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
    assert body["status"] == "pending_review"
    assert body["estimate"]["source"] == "ai_text_estimate"
    assert body["vision_error"] == "vision_estimator_failed"


def test_meal_intake_image_merge_exception_uses_text_fallback(monkeypatch):
    module = _client(monkeypatch)
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "lm_studio",
            "item_description": "protein shake",
            "portion_hint": "1 shake",
            "confidence": "not-a-number",
            "ambiguous": False,
            "uncertainty_notes": [],
        },
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
            "client_id": "meal-vision-merge-fallback-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["estimate"]["source"] == "ai_text_estimate"
    assert body["vision_error"] == "vision_estimator_failed"


def test_meal_intake_image_provider_failure_handles_text_parser_exception(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(
        module.vision_estimator,
        "describe",
        lambda *_a, **_kw: (_ for _ in ()).throw(module.vision_estimator.VisionEstimatorError("provider down")),
    )
    monkeypatch.setattr(
        module,
        "parse_meal_text",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("parser exploded")),
    )
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: {"client_id": r["client_id"], **r})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "protein shake",
            "client_id": "meal-vision-fallback-parser-error-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["fallback_used"] is True
    assert body["vision_error"] == "vision_estimator_failed"
    assert body["text_fallback_error"] == "text_parser_failed"
    assert body["items"][0]["estimate"]["source"] == "manual_text_review"
    assert body["estimate"]["from_image"] is True
    assert body["photo_retention"]["image_received"] is True
    assert body["photo_retention"]["raw_photo_retained"] is False
    assert body["photo_retention"]["raw_model_trace_retained"] is False


def test_meal_intake_image_provider_failure_marks_malformed_text_parser_output(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(
        module.vision_estimator,
        "describe",
        lambda *_a, **_kw: (_ for _ in ()).throw(module.vision_estimator.VisionEstimatorError(
            "model warmup completed but model not loaded: qwen3-vl-30b-a3b-instruct@q4_k_xl"
        )),
    )
    monkeypatch.setattr(
        module,
        "parse_meal_text",
        lambda *_a, **_kw: {
            "estimate": {"item_name": "broken", "source": "ai_text_estimate", "calories": "bad"},
            "fallback_used": True,
        },
    )
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: {"client_id": r["client_id"], **r})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "shared movie popcorn",
            "client_id": "meal-vision-fallback-malformed-parser-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["fallback_used"] is True
    assert body["vision_error"] == "vision_estimator_failed"
    assert body["text_fallback_error"] == "text_parser_failed"
    assert "qwen3-vl" not in res.get_data(as_text=True)
    assert body["items"][0]["estimate"]["source"] == "manual_text_review"


def test_meal_intake_image_provider_failure_preserves_photo_origin_after_pending_reload(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(
        module.vision_estimator,
        "describe",
        lambda *_a, **_kw: (_ for _ in ()).throw(module.vision_estimator.VisionEstimatorError("provider down")),
    )
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Shared popcorn",
        "portion_description": "shared tub",
        "meal_type": "snack",
        "calories": 300,
        "protein_g": 5,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 6,
        "confidence": 0.45,
        "ambiguous": True,
        "uncertainty_notes": ["Portion is unclear."],
    })
    persisted_rows = []

    def fake_add_food_log(_user_id, record):
        row = dict(record)
        row["original_estimate"] = data_store.sanitize_food_estimate(record.get("original_estimate"))
        persisted_rows[:] = [row]
        return {
            "client_id": record["client_id"],
            "correction_state": record["correction_state"],
            "source": record["source"],
            "original_estimate": row["original_estimate"],
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)
    monkeypatch.setattr(module, "get_food_logs", lambda *_a, **_kw: list(persisted_rows))
    monkeypatch.setattr(module, "delete_food_log_by_client_id", lambda *_a, **_kw: False)

    client = module.app.test_client()
    res = client.post(
        "/api/meal-intake",
        data={
            "text": "shared movie popcorn",
            "client_id": "meal-vision-fallback-pending-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert persisted_rows[0]["source"] == "ai_text_estimate"
    assert persisted_rows[0]["original_estimate"]["from_image"] is True

    pending = client.get("/api/meal-intake/pending")
    assert pending.status_code == 200
    pending_estimate = pending.get_json()["pending"][0]["estimate"]
    assert pending_estimate["from_image"] is True

    accept = client.post(
        "/api/meal-intake/meal-vision-fallback-pending-1/accept",
        json={"estimate": pending_estimate, "text": "shared movie popcorn"},
    )
    assert accept.status_code == 409
    assert accept.get_json()["save_blocked_item_ids"] == ["item-1"]


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
    assert body["error"]["reason"] == "vision_estimator_failed"
    assert body["photo_retention"]["image_received"] is True


def test_meal_intake_accept_sanitizes_original_estimate_before_persist(monkeypatch):
    module = _client(monkeypatch)
    persisted = {}

    def fake_add_food_log(_user_id, record):
        persisted.update(record)
        return {"client_id": record["client_id"], "original_estimate": record["original_estimate"]}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: False)

    res = module.app.test_client().post(
        "/api/meal-intake/meal-accept-sanitize-1/accept",
        json={
            "estimate": {
                "item_name": "Shared popcorn",
                "portion_description": "shared tub",
                "meal_type": "snack",
                "calories": 300,
                "protein_g": 5,
                "carbs_g": 36,
                "fat_g": 18,
                "sodium_mg": 520,
                "fiber_g": 6,
                "confidence": 0.45,
                "ambiguous": True,
                "uncertainty_notes": ["Portion is unclear."],
                "source": "ai_text_estimate",
                "from_image": True,
            },
            "original_estimate": {
                "item_name": "Shared popcorn",
                "calories": "300",
                "protein_g": 5,
                "carbs_g": 36,
                "fat_g": 18,
                "confidence": 0.45,
                "ambiguous": True,
                "uncertainty_notes": ["Portion is unclear."],
                "source": "ai_text_estimate",
                "from_image": True,
                "image_bytes": "drop me",
            },
            "text": "shared movie popcorn",
        },
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    assert persisted["original_estimate"]["calories"] == 300
    assert persisted["original_estimate"]["from_image"] is True
    assert "image_bytes" not in persisted["original_estimate"]


def test_meal_intake_accept_filters_preserved_metadata_before_persist(monkeypatch):
    module = _client(monkeypatch)
    persisted = {}

    def fake_add_food_log(_user_id, record):
        persisted.update(record)
        return {"client_id": record["client_id"], "original_estimate": record["original_estimate"]}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: False)

    estimate = {
        "item_name": "Protein shake",
        "portion_description": "1 bottle",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.62,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "vision_claude_estimate",
        "from_image": True,
        "vision_description": {"raw_model_trace": "secret"},
        "vision_provider": "claude",
        "vision_confidence": 0.625,
        "off_attribution": {
            "name": "Open Food Facts",
            "url": "https://world.openfoodfacts.org/",
            "raw": {"drop": True},
        },
        "verified_source_url": "https://world.openfoodfacts.org/",
    }

    res = module.app.test_client().post(
        "/api/meal-intake/meal-accept-metadata-sanitize-1/accept",
        json={"estimate": estimate, "original_estimate": estimate, "text": "protein shake"},
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    original = persisted["original_estimate"]
    assert "vision_description" not in original
    assert original["vision_provider"] == "claude"
    assert original["vision_confidence"] == 0.62
    assert original["off_attribution"] == {
        "name": "Open Food Facts",
        "url": "https://world.openfoodfacts.org/",
    }
    assert original["verified_source_url"] == "https://world.openfoodfacts.org/"


def test_meal_intake_undo_calls_delete_helper(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "get_food_logs", lambda *_a, **_kw: [])

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


def test_meal_intake_undo_removes_food_log_only_entry_and_is_idempotent(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    _add_food_log("food-log-only-delete-me")
    module.NUTRITION_DATA[:] = [
        {
            "client_id": "legacy-nutrition-keep-me",
            "date": "2026-05-18",
            "calories": 650,
            "protein_g": 40,
        }
    ]

    res = module.app.test_client().delete("/api/meal-intake/food-log-only-delete-me")

    assert res.status_code == 200
    assert res.get_json() == {"status": "ok", "removed": True}
    assert [
        entry.get("client_id")
        for entry in data_store.get_food_logs(1)
        if entry.get("client_id") == "food-log-only-delete-me"
    ] == []
    assert [entry["client_id"] for entry in module.NUTRITION_DATA] == ["legacy-nutrition-keep-me"]

    retry = module.app.test_client().delete("/api/meal-intake/food-log-only-delete-me")
    assert retry.status_code == 200
    assert retry.get_json() == {"status": "not_found", "removed": False}


def test_meal_intake_undo_returns_not_found_when_missing(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "get_food_logs", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "delete_food_log_by_client_id", lambda *_a, **_kw: False)

    res = module.app.test_client().delete("/api/meal-intake/meal-missing")
    assert res.status_code == 200
    assert res.get_json() == {"status": "not_found", "removed": False}


def test_meal_intake_undo_removes_legacy_nutrition_row_by_client_id(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "get_food_logs", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "delete_food_log_by_client_id", lambda *_a, **_kw: False)
    module.NUTRITION_DATA[:] = [
        {
            "client_id": "legacy-nutrition-delete-me",
            "date": "2026-05-18",
            "calories": 500,
            "protein_g": 30,
        },
        {
            "client_id": "legacy-nutrition-keep-me",
            "date": "2026-05-18",
            "calories": 650,
            "protein_g": 40,
        },
    ]

    res = module.app.test_client().delete("/api/meal-intake/legacy-nutrition-delete-me")

    assert res.status_code == 200
    assert res.get_json() == {"status": "ok", "removed": True}
    assert [entry["client_id"] for entry in module.NUTRITION_DATA] == ["legacy-nutrition-keep-me"]

    retry = module.app.test_client().delete("/api/meal-intake/legacy-nutrition-delete-me")
    assert retry.status_code == 200
    assert retry.get_json() == {"status": "not_found", "removed": False}


def test_meal_intake_undo_removes_dual_write_food_log_and_legacy_entry(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    _add_food_log("dual-write-delete-me", calories=500)
    _add_food_log("food-log-keep-me", calories=700)
    module.NUTRITION_DATA[:] = [
        {
            "client_id": "dual-write-delete-me",
            "date": "2026-05-18",
            "calories": 500,
            "protein_g": 30,
        },
        {
            "client_id": "legacy-nutrition-keep-me",
            "date": "2026-05-18",
            "calories": 650,
            "protein_g": 40,
        },
    ]

    res = module.app.test_client().delete("/api/meal-intake/dual-write-delete-me")

    assert res.status_code == 200
    assert res.get_json() == {"status": "ok", "removed": True}
    assert [entry["client_id"] for entry in data_store.get_food_logs(1)] == ["food-log-keep-me"]
    assert [entry["client_id"] for entry in module.NUTRITION_DATA] == ["legacy-nutrition-keep-me"]


def test_meal_intake_pending_discard_does_not_delete_accepted_row(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "get_food_logs", lambda *_a, **_kw: [{
        "client_id": "meal-accepted-elsewhere",
        "correction_state": "accepted",
    }])

    def fail_delete(*_args, **_kwargs):
        raise AssertionError("accepted rows must not be deleted by pending discard")

    monkeypatch.setattr(module, "delete_food_log_by_client_id", fail_delete)

    res = module.app.test_client().delete(
        "/api/meal-intake/meal-accepted-elsewhere?correction_state=pending_review"
    )
    assert res.status_code == 409
    body = res.get_json()
    assert body["removed"] is False
    assert body["correction_state"] == "accepted"


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
    assert "stub" not in body
    assert captured["correction_state"] == "accepted"
    assert captured["item_name"] == "Popcorn"
    assert captured["context_note"] == "movie theater popcorn"


def test_meal_intake_accept_uses_vision_description_for_image_only_vocab(monkeypatch):
    module = _client(monkeypatch)
    vocab_calls = []
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: True)
    monkeypatch.setattr(module.personal_vocab, "record_accept", lambda *args, **_kw: vocab_calls.append(args))
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})

    res = module.app.test_client().post(
        "/api/meal-intake/meal-image-vocab-accept/accept",
        json={
            "estimate": {
                "item_name": "Bill Miller BBQ order: 1 Bacon & Egg Taco; 2 Breakfast Sandwich",
                "portion_description": "1 taco; 2 sandwiches",
                "meal_type": "breakfast",
                "calories": 1290,
                "protein_g": 58,
                "carbs_g": 100,
                "fat_g": 74,
                "sodium_mg": 2660,
                "fiber_g": 6,
                "confidence": 0.82,
                "ambiguous": False,
                "uncertainty_notes": [],
                "source": "vision_lm_studio+nutritionix",
                "from_image": True,
                "vision_description": "Bill Miller BBQ cart with Bacon & Egg Taco and two Breakfast Sandwiches",
                "vision_provider": "lm_studio",
                "vision_confidence": 0.91,
            }
        },
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    assert vocab_calls[0][1] == "Bill Miller BBQ cart with Bacon & Egg Taco and two Breakfast Sandwiches"


def test_meal_intake_corrected_image_vocab_uses_user_phrase(monkeypatch):
    module = _client(monkeypatch)
    vocab_calls = []
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: True)
    monkeypatch.setattr(module.personal_vocab, "record_correct", lambda *args, **_kw: vocab_calls.append(args))
    monkeypatch.setattr(module, "add_food_log", lambda _u, record: {"client_id": record["client_id"], **record})

    res = module.app.test_client().post(
        "/api/meal-intake/meal-image-vocab-correct/accept",
        json={
            "estimate": {
                "item_name": "Bill Miller BBQ order: 1 Bacon & Egg Taco; 2 Breakfast Sandwich",
                "portion_description": "1 taco; 2 sandwiches",
                "meal_type": "breakfast",
                "calories": 1290,
                "protein_g": 58,
                "carbs_g": 100,
                "fat_g": 74,
                "sodium_mg": 2660,
                "fiber_g": 6,
                "confidence": 0.82,
                "ambiguous": False,
                "uncertainty_notes": [],
                "source": "vision_lm_studio+nutritionix",
                "from_image": True,
                "vision_description": "Bill Miller BBQ cart with Bacon & Egg Taco and two Breakfast Sandwiches",
                "vision_provider": "lm_studio",
                "vision_confidence": 0.91,
            },
            "text": "Bill Miller",
            "corrected": True,
        },
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    assert vocab_calls[0][1] == "Bill Miller"


def test_meal_intake_accept_requires_calories(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake/meal-bad-accept/accept",
        json={"estimate": {"item_name": "Smoothie"}},
    )
    assert res.status_code == 400
    assert "calories" in res.get_json()["error"]["message"]


def test_meal_intake_accept_persists_sodium_and_meal_type_from_review_card(monkeypatch):
    """FIT-6 AC1 + AC3: the review card sends sodium_mg and meal_type alongside
    the existing macros when the user accepts, so the persisted food_log carries
    the user-confirmed sodium and meal time values into the day's nutrition
    totals. Regression-guards the AC1 "sodium" and AC3 "meal time" edit fields.
    """
    module = _client(monkeypatch)
    captured = {}

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {
            "client_id": record["client_id"],
            "calories": record["calories"],
            "sodium_mg": record["sodium_mg"],
            "meal_type": record["meal_type"],
            "source": record["source"],
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake/meal-fit6-accept-1/accept",
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
            },
            "text": "chipotle chicken burrito",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["food_log"]["sodium_mg"] == 2310
    assert body["food_log"]["meal_type"] == "lunch"
    assert captured["sodium_mg"] == 2310
    assert captured["meal_type"] == "lunch"
    assert captured["correction_state"] == "accepted"


def test_meal_intake_accept_rejects_invalid_meal_type_from_review_card(monkeypatch):
    """FIT-6 AC3: meal_type edits are constrained to the schema's allowed
    values. A bogus meal_type submitted via the review card payload must
    fail schema validation, not silently coerce to a default.
    """
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake/meal-fit6-bad-meal-type/accept",
        json={
            "estimate": {
                "item_name": "Snack",
                "calories": 200,
                "protein_g": 5,
                "carbs_g": 25,
                "fat_g": 8,
                "sodium_mg": 100,
                "meal_type": "midnight_feast",
            },
        },
    )
    assert res.status_code == 400
    assert "meal_type" in res.get_json()["error"]["message"]


def test_meal_intake_pending_response_exposes_policy_block_for_review_card(monkeypatch):
    """FIT-6 AC2 contract: the review card renders policy reason chips
    (Low confidence / Ambiguous input / Missing calories / ...) from the
    response's ``policy.reasons`` array, so the user sees *why* the
    estimate is held back rather than only the merged uncertainty_notes
    paragraph. The frontend's MEAL_POLICY_REASON_LABELS map mirrors the
    backend's _POLICY_REASON_NOTES; this test guards the contract by
    asserting the policy block is present with the expected shape.
    """
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Burrito",
        "portion_description": None,
        "meal_type": "lunch",
        "calories": 600,
        "protein_g": 30,
        "carbs_g": 70,
        "fat_g": 20,
        "sodium_mg": 800,
        "fiber_g": 5,
        "confidence": 0.55,
        "ambiguous": True,
        "uncertainty_notes": ["Portion unclear"],
    })
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "burrito",
            "client_id": "meal-fit6-policy-1",
            "local_timestamp": "2026-05-20T12:00:00",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert "policy" in body
    policy = body["policy"]
    assert isinstance(policy.get("reasons"), list)
    # Ambiguous + medium-confidence input should surface at least the
    # ambiguous_input reason code. confidence_band may vary as the
    # FIT-61 policy thresholds evolve, so we only assert the shape +
    # that at least one stable reason code lands.
    assert "ambiguous_input" in policy["reasons"]
    assert isinstance(policy.get("confidence_band"), str) and policy["confidence_band"]


def test_meal_intake_pending_response_exposes_source_for_review_card(monkeypatch):
    """FIT-6 AC1 + AC5: the review card renders a source chip from
    estimate.source so the user can see whether the numbers came from a
    branded lookup (Nutritionix / USDA), a cached prior, the AI text
    parser, or a fallback preset. Source presence in the pending
    response is the contract this test guards.
    """
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Burrito",
        "portion_description": None,
        "meal_type": "lunch",
        "calories": 600,
        "protein_g": 30,
        "carbs_g": 70,
        "fat_g": 20,
        "sodium_mg": 800,
        "fiber_g": 5,
        "confidence": 0.5,
        "ambiguous": True,
        "uncertainty_notes": [],
    }, source="ai_text_estimate")
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "burrito",
            "client_id": "meal-fit6-source-1",
            "local_timestamp": "2026-05-20T12:00:00",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["estimate"]["source"] == "ai_text_estimate"


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


def test_browser_local_iso_preserves_wall_clock_without_server_tz_conversion():
    module = importlib.import_module("app")
    assert (
        module._browser_local_iso_from_iso("2026-05-18T22:00:00-05:00")
        == "2026-05-18T22:00:00"
    )
    assert (
        module._browser_local_date_from_iso("2026-05-18T22:00:00-05:00")
        == "2026-05-18"
    )


def test_browser_local_date_rejects_invalid_values():
    module = importlib.import_module("app")
    assert module._browser_local_date_from_value("2026-05-18") == "2026-05-18"
    assert module._browser_local_date_from_value("2026-99-99") is None
    assert module._browser_local_date_from_value("not-a-date") is None


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


def test_meal_intake_prefers_browser_local_date_and_iso_over_utc_timestamp(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Late protein shake",
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
            "client_id": "meal-browser-local-1",
            "local_timestamp": "2026-05-19T03:00:00.000Z",
            "local_date": "2026-05-18",
            "local_iso": "2026-05-18T22:00:00-05:00",
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert captured["date"] == "2026-05-18"
    assert captured["logged_at"] == "2026-05-18T22:00:00"
    assert captured["source_timestamp"] == "2026-05-18T22:00:00"
    assert module._nutrition_entry_logged_hour(captured) == 22


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
    rather than defaulting to a generic manual-review source.
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
    """The response must include policy.confidence_band and policy.reasons so
    the review-card UI can show why the estimate was held back.

    FIT-138: high-confidence text still surfaces band=high with no reasons,
    but the response status is pending_review because the capture path now
    always routes to review before save.
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
        "client_id": "meal-policy-1", "correction_state": "pending_review",
    })

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "eggs and toast", "client_id": "meal-policy-1"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["policy"]["confidence_band"] == "high"
    assert body["policy"]["reasons"] == [], "high-confidence review has no policy reasons"


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
    assert persisted[0]["correction_state"] == "pending_review"
    assert body["food_log"]["client_id"] == "meal-implausible-1"


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
    assert persisted[0]["correction_state"] == "pending_review"
    assert body["food_log"]["client_id"] == "meal-medium-1"


def test_meal_intake_pending_response_round_trips_browser_local_time(monkeypatch):
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
        data={
            "text": "eggs and toast",
            "client_id": "meal-pending-time-1",
            "local_timestamp": "2026-05-19T04:55:00.000Z",
            "local_date": "2026-05-18",
            "local_iso": "2026-05-18T23:55:00-05:00",
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["local_timestamp"] == "2026-05-19T04:55:00.000Z"
    assert body["local_date"] == "2026-05-18"
    assert body["local_iso"] == "2026-05-18T23:55:00-05:00"
    assert persisted[0]["correction_state"] == "pending_review"
    assert persisted[0]["date"] == "2026-05-18"
    assert persisted[0]["logged_at"] == "2026-05-18T23:55:00"
    assert body["food_log"]["client_id"] == "meal-pending-time-1"


def test_meal_intake_accept_honors_pending_submission_browser_local_time(monkeypatch):
    module = _client(monkeypatch)
    captured = {}

    def fake_add_food_log(_user_id, record):
        captured.update(record)
        return {
            "client_id": record["client_id"],
            "logged_at": record["logged_at"],
            "date": record["date"],
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake/meal-accept-browser-local/accept",
        json={
            "estimate": {
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
                "source": "fallback_text_estimate",
            },
            "text": "eggs and toast",
            "local_timestamp": "2026-05-19T04:55:00.000Z",
            "local_date": "2026-05-18",
            "local_iso": "2026-05-18T23:55:00-05:00",
        },
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert captured["date"] == "2026-05-18"
    assert captured["logged_at"] == "2026-05-18T23:55:00"
    assert captured["source_timestamp"] == "2026-05-18T23:55:00"
    assert module._nutrition_entry_logged_hour(captured) == 23


def test_meal_intake_submit_persists_with_pending_review_state(monkeypatch):
    """The submit path must persist a non-counting pending-review row.
    FIT-138 + FIT-144: the legacy high-confidence auto-log shortcut is
    removed from the capture path; review-before-save holds universally.
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
    assert res.get_json()["status"] == "pending_review"
    assert persisted[0]["correction_state"] == "pending_review"


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
    """Image+text ambiguity must live on the estimate consumed by policy.

    FIT-61 evaluates only ``estimate``, so the ``ambiguous`` flag must live
    inside that dict for the policy to route shared/unclear photos to pending
    review.
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
    assert body["estimate"]["from_image"] is True
    assert "ambiguous_input" in body["policy"]["reasons"]
    assert body["photo_retention"]["image_received"] is True
    assert body["photo_retention"]["raw_model_trace_retained"] is False
    assert persisted[0]["correction_state"] == "pending_review"
    assert body["food_log"]["client_id"] == "meal-ambig-img-1"

    accept = module.app.test_client().post(
        "/api/meal-intake/meal-ambig-img-1/accept",
        json={"estimate": body["estimate"], "text": "shared movie popcorn"},
    )
    accepted = accept.get_json()
    assert accept.status_code == 409
    assert accepted["save_blocked_item_ids"] == ["item-1"]


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


def test_meal_intake_pending_endpoint_lists_visible_rows_and_cleans_stale(monkeypatch):
    """FIT-67: pending rows are durable, reloadable, and TTL-cleaned."""
    module = _client(monkeypatch)
    today = module.datetime.now().date()
    visible_day = today.isoformat()
    stale_day = (today - module.timedelta(days=module.PENDING_MEAL_REVIEW_TTL_DAYS + 1)).isoformat()
    visible_estimate = {
        "item_name": "Shared popcorn",
        "portion_description": "large tub",
        "meal_type": "snack",
        "calories": 300,
        "protein_g": 5,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 6,
        "confidence": 0.45,
        "ambiguous": True,
        "uncertainty_notes": ["Portion is unclear."],
        "source": "ai_text_estimate",
    }
    logs = [
        {
            "client_id": "meal-visible-pending",
            "date": visible_day,
            "logged_at": f"{visible_day}T12:30:00",
            "context_note": "shared popcorn",
            "correction_state": "pending_review",
            "original_estimate": dict(visible_estimate),
        },
        {
            "client_id": "meal-stale-pending",
            "date": stale_day,
            "logged_at": f"{stale_day}T12:30:00",
            "context_note": "old popcorn",
            "correction_state": "pending_review",
            "original_estimate": dict(visible_estimate),
        },
        {
            "client_id": "meal-stale-accepted",
            "date": stale_day,
            "logged_at": f"{stale_day}T08:00:00",
            "correction_state": "accepted",
            "original_estimate": dict(visible_estimate),
        },
    ]
    deleted = []

    monkeypatch.setattr(module, "get_food_logs", lambda *_a, **_kw: list(logs))
    monkeypatch.setattr(module, "delete_food_log_by_client_id", lambda _u, cid: deleted.append(cid) or True)

    res = module.app.test_client().get("/api/meal-intake/pending")
    assert res.status_code == 200
    body = res.get_json()

    assert body["pending_count"] == 1
    assert body["ttl_days"] == module.PENDING_MEAL_REVIEW_TTL_DAYS
    assert body["stale_removed"] == 1
    assert deleted == ["meal-stale-pending"]
    assert body["pending"][0]["client_id"] == "meal-visible-pending"
    assert body["pending"][0]["estimate"]["item_name"] == "Shared popcorn"
    assert body["pending"][0]["text_hint"] == "shared popcorn"
    assert body["pending"][0]["policy"]["reasons"], "pending payload should include review rationale"


def test_meal_intake_pending_endpoint_restores_photo_origin_marker(monkeypatch):
    module = _client(monkeypatch)
    today = module.datetime.now().date().isoformat()
    estimate = {
        "item_name": "Photo meal",
        "calories": 400,
        "protein_g": 22,
        "carbs_g": 40,
        "fat_g": 15,
        "confidence": 0.45,
        "ambiguous": True,
        "uncertainty_notes": ["Photo needs review."],
        "source": "stub_vision_estimate",
    }
    monkeypatch.setattr(module, "get_food_logs", lambda *_a, **_kw: [{
        "client_id": "meal-photo-pending",
        "date": today,
        "logged_at": f"{today}T12:00:00",
        "source": "stub_vision_estimate",
        "correction_state": "pending_review",
        "original_estimate": dict(estimate),
    }])
    monkeypatch.setattr(module, "delete_food_log_by_client_id", lambda *_a, **_kw: False)

    res = module.app.test_client().get("/api/meal-intake/pending")
    assert res.status_code == 200
    body = res.get_json()
    assert body["pending"][0]["estimate"]["from_image"] is True


def test_multi_item_accept_persists_included_rows_and_totals_only(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()

    payload = {
        "meal_id": "photo-meal-1",
        "meal_timestamp": "2026-05-22T12:34:00",
        "local_date": "2026-05-22",
        "text": "raw parent text should not persist",
        "items": [
            {
                "state": "included",
                "item_id": "chicken",
                "text": "grilled chicken",
                "estimate": _accepted_estimate(
                    item_name="Grilled chicken",
                    calories=300,
                    protein_g=42,
                    carbs_g=0,
                    fat_g=9,
                    sodium_mg=360,
                    fiber_g=0,
                    source="stub_vision_estimate",
                    from_image=True,
                    raw_model_trace="drop me",
                    prompt="drop me too",
                ),
            },
            {
                "state": "included",
                "item_id": "rice",
                "estimate": _accepted_estimate(
                    item_name="Rice",
                    calories=220,
                    protein_g=4,
                    carbs_g=46,
                    fat_g=1,
                    sodium_mg=10,
                    fiber_g=1,
                ),
            },
            {
                "state": "skipped",
                "item_id": "napkin",
                "text": "napkin",
                "estimate": _accepted_estimate(item_name="Napkin", calories=50),
            },
            {
                "state": "deleted",
                "text": "duplicate sauce",
                "estimate": _accepted_estimate(item_name="Duplicate sauce", calories=80),
            },
        ],
    }

    res = client.post("/api/meal-intake/photo-parent-1/accept", json=payload)

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["meal_id"] == "photo-meal-1"
    assert body["included_count"] == 2
    assert body["skipped_count"] == 1
    assert body["deleted_count"] == 1
    assert body["meal_totals"] == {
        "calories": 520,
        "protein_g": 46.0,
        "carbs_g": 46.0,
        "fat_g": 10.0,
        "sodium_mg": 370,
        "fiber_g": 1.0,
    }
    rows = data_store.get_food_logs(1)
    assert len(rows) == 2
    assert {row["meal_id"] for row in rows} == {"photo-meal-1"}
    assert {row["item_state"] for row in rows} == {"included"}
    assert sorted(row["item_index"] for row in rows) == [0, 1]
    assert all(row["logged_at"] == "2026-05-22T12:34:00" for row in rows)
    assert "raw parent text should not persist" not in {row.get("context_note") for row in rows}
    assert all("raw_model_trace" not in str(row.get("original_estimate")) for row in rows)
    assert all("prompt" not in str(row.get("original_estimate")) for row in rows)
    vocab = {entry["phrase"]: entry for entry in data_store.list_personal_vocab_entries(1)}
    assert vocab["napkin"]["skip_count"] == 1
    assert vocab["duplicate sauce"]["deleted_count"] == 1


def test_multi_item_accept_blocks_unsaved_review_item_without_snapshot(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    meal_id = "photo-meal-noenap-1"

    assert data_store.get_meal_review_snapshot(1, meal_id) is None

    res = client.post(
        "/api/meal-intake/photo-parent-noenap-1/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "included",
                    "item_id": "blocked-1",
                    "estimate": _accepted_estimate(
                        item_name="Mystery dish",
                        calories=500,
                        confidence=0.40,
                    ),
                }
            ],
        },
    )

    body = res.get_json()
    assert res.status_code == 409
    assert body["status"] == "blocked"
    assert body["meal_id"] == meal_id
    assert body["save_blocked_item_ids"] == ["blocked-1"]
    assert body["error"]["message"] == "review has blocked items"
    assert data_store.get_food_logs(1) == []
    assert data_store.list_pending_workout_adaptation_windows(1) == []


def test_multi_item_accept_persists_clear_item_without_snapshot(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()

    res = client.post(
        "/api/meal-intake/photo-parent-clear-noenap-1/accept",
        json={
            "meal_id": "photo-meal-clear-noenap-1",
            "items": [
                {
                    "state": "included",
                    "item_id": "clear-1",
                    "estimate": _accepted_estimate(
                        item_name="Chicken bowl",
                        calories=500,
                        confidence=0.88,
                    ),
                }
            ],
        },
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["calories"] == 500


def test_multi_item_accept_blocked_sibling_prevents_partial_persist(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()

    res = client.post(
        "/api/meal-intake/photo-parent-mixed-noenap-1/accept",
        json={
            "meal_id": "photo-meal-mixed-noenap-1",
            "items": [
                {
                    "state": "included",
                    "item_id": "clear-1",
                    "estimate": _accepted_estimate(
                        item_name="Chicken bowl",
                        calories=500,
                        confidence=0.88,
                    ),
                },
                {
                    "state": "included",
                    "item_id": "blocked-1",
                    "estimate": _accepted_estimate(
                        item_name="Mystery dish",
                        calories=500,
                        confidence=0.40,
                    ),
                },
            ],
        },
    )

    body = res.get_json()
    assert res.status_code == 409
    assert body["save_blocked_item_ids"] == ["blocked-1"]
    assert data_store.get_food_logs(1) == []
    assert data_store.list_pending_workout_adaptation_windows(1) == []


def test_multi_item_accept_blocked_new_sibling_rejected_when_event_missing(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    meal_id = "photo-meal-partial-event-missing"
    payload = {
        "meal_id": meal_id,
        "items": [
            {
                "state": "included",
                "item_id": "clear-1",
                "estimate": _accepted_estimate(
                    item_name="Chicken bowl",
                    calories=500,
                    confidence=0.88,
                ),
            }
        ],
    }

    first = client.post("/api/meal-intake/photo-parent-partial-event-missing/accept", json=payload)
    assert first.status_code == 200, first.get_data(as_text=True)
    data_store.delete_meal_acceptance_event(1, meal_id)
    pending_before_retry = data_store.list_pending_workout_adaptation_windows(1)

    retry_payload = dict(payload)
    retry_payload["items"] = [
        payload["items"][0],
        {
            "state": "included",
            "item_id": "blocked-1",
            "estimate": _accepted_estimate(
                item_name="Mystery dish",
                calories=500,
                confidence=0.40,
            ),
        },
    ]
    retry = client.post(
        "/api/meal-intake/photo-parent-partial-event-missing/accept",
        json=retry_payload,
    )

    body = retry.get_json()
    rows = data_store.get_food_logs(1)
    assert retry.status_code == 409
    assert body["save_blocked_item_ids"] == ["blocked-1"]
    assert len(rows) == 1
    assert rows[0]["meal_item_id"] == "clear-1"
    assert data_store.list_pending_workout_adaptation_windows(1) == pending_before_retry


def test_multi_item_accept_preserves_blocked_fallback_id_when_event_missing(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    meal_id = "photo-meal-partial-fallback-id"
    payload = {
        "meal_id": meal_id,
        "items": [
            {"state": "included", "estimate": _accepted_estimate(item_name="Chicken bowl")},
            {"state": "included", "estimate": _accepted_estimate(item_name="Rice bowl")},
        ],
    }

    first = client.post("/api/meal-intake/photo-parent-partial-fallback-id/accept", json=payload)
    assert first.status_code == 200, first.get_data(as_text=True)
    data_store.delete_meal_acceptance_event(1, meal_id)

    retry_payload = dict(payload)
    retry_payload["items"] = [
        *payload["items"],
        {
            "state": "included",
            "estimate": _accepted_estimate(
                item_name="Mystery dish",
                confidence=0.40,
            ),
        },
    ]
    retry = client.post(
        "/api/meal-intake/photo-parent-partial-fallback-id/accept",
        json=retry_payload,
    )

    body = retry.get_json()
    assert retry.status_code == 409
    assert body["save_blocked_item_ids"] == ["item-2"]


def test_snapshot_path_blocked_item_still_409(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    meal_id = "meal-snapshot-blocked-1"
    pending_estimate = _accepted_estimate(
        item_name="Mystery dish",
        calories=500,
        confidence=0.40,
        ambiguous=True,
    )
    module._review_save_snapshot(
        1,
        meal_id,
        {
            "items": [
                module._review_item_from_estimate(
                    pending_estimate,
                    item_id="item-1",
                    item_order=1,
                    status="included",
                    text="Mystery dish",
                )
            ],
            "meal_type": "lunch",
        },
        2,
        {},
        sync_pending=True,
    )
    assert data_store.get_meal_review_snapshot(1, meal_id) is not None

    accept = client.post(
        f"/api/meal-intake/{meal_id}/accept",
        json={"estimate": pending_estimate},
    )

    body = accept.get_json()
    assert accept.status_code == 409
    assert body["status"] == "blocked"
    assert body["meal_id"] == meal_id
    assert body["save_blocked_item_ids"] == ["item-1"]
    assert body["error"]["message"] == "review has blocked items"


def test_multi_item_accept_retry_is_noop_and_changed_item_set_conflicts(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    payload = {
        "meal_id": "photo-meal-idem",
        "items": [
            {"state": "included", "item_id": "a", "estimate": _accepted_estimate(item_name="A", calories=100)},
            {"state": "included", "item_id": "b", "estimate": _accepted_estimate(item_name="B", calories=200)},
            {"state": "skipped", "text": "plate", "estimate": _accepted_estimate(item_name="Plate", calories=10)},
        ],
    }

    first = client.post("/api/meal-intake/photo-parent-idem/accept", json=payload)
    retry = client.post("/api/meal-intake/photo-parent-idem/accept", json=payload)
    changed = dict(payload)
    changed["items"] = [
        {"state": "included", "item_id": "a", "estimate": _accepted_estimate(item_name="A", calories=100)}
    ]
    conflict = client.post("/api/meal-intake/photo-parent-idem/accept", json=changed)
    changed_feedback = dict(payload)
    changed_feedback["items"] = [
        {"state": "included", "item_id": "a", "estimate": _accepted_estimate(item_name="A", calories=100)},
        {"state": "included", "item_id": "b", "estimate": _accepted_estimate(item_name="B", calories=200)},
        {"state": "skipped", "text": "bowl", "estimate": _accepted_estimate(item_name="Bowl", calories=10)},
    ]
    feedback_conflict = client.post("/api/meal-intake/photo-parent-idem/accept", json=changed_feedback)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert conflict.status_code == 409
    assert feedback_conflict.status_code == 409
    rows = data_store.get_food_logs(1)
    assert len(rows) == 2
    assert retry.get_json()["meal_totals"]["calories"] == 300
    entry = data_store.get_personal_vocab_entry(1, "plate")
    assert entry["skip_count"] == 1
    assert data_store.get_personal_vocab_entry(1, "bowl") is None


def test_multi_item_accept_retry_with_existing_rows_does_not_require_estimates(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    payload = {
        "meal_id": "photo-meal-idem-minimal-retry",
        "items": [
            {"state": "included", "item_id": "a", "estimate": _accepted_estimate(item_name="A", calories=100)},
            {"state": "included", "item_id": "b", "estimate": _accepted_estimate(item_name="B", calories=200)},
        ],
    }

    first = client.post("/api/meal-intake/photo-parent-idem-minimal-retry/accept", json=payload)
    retry = client.post(
        "/api/meal-intake/photo-parent-idem-minimal-retry/accept",
        json={
            "meal_id": payload["meal_id"],
            "items": [
                {"state": "included", "item_id": "a"},
                {"state": "included", "item_id": "b"},
            ],
        },
    )

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.get_json()["meal_totals"]["calories"] == 300
    assert len(data_store.get_food_logs(1)) == 2


def test_multi_item_accept_namespaces_explicit_item_client_ids(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()

    first = client.post(
        "/api/meal-intake/photo-parent-explicit-one/accept",
        json={
            "meal_id": "photo-meal-explicit-one",
            "items": [
                {
                    "state": "included",
                    "client_id": "chicken",
                    "estimate": _accepted_estimate(item_name="Chicken", calories=100),
                }
            ],
        },
    )
    second = client.post(
        "/api/meal-intake/photo-parent-explicit-two/accept",
        json={
            "meal_id": "photo-meal-explicit-two",
            "items": [
                {
                    "state": "included",
                    "client_id": "chicken",
                    "estimate": _accepted_estimate(item_name="Chicken", calories=200),
                }
            ],
        },
    )

    rows = data_store.get_food_logs(1)
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(rows) == 2
    assert {row["meal_id"] for row in rows} == {"photo-meal-explicit-one", "photo-meal-explicit-two"}
    assert len({row["client_id"] for row in rows}) == 2
    assert sum(row["calories"] for row in rows) == 300


def test_multi_item_accept_does_not_delete_same_id_legacy_log(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    data_store.add_food_log(
        1,
        {
            "client_id": "photo-parent-legacy",
            "date": "2026-05-22",
            "logged_at": "2026-05-22T09:00:00",
            "item_name": "Legacy snack",
            "calories": 111,
            "protein_g": 3,
            "carbs_g": 10,
            "fat_g": 4,
            "sodium_mg": 90,
            "fiber_g": 1,
            "correction_state": "accepted",
            "source": "manual",
        },
    )

    res = module.app.test_client().post(
        "/api/meal-intake/photo-parent-legacy/accept",
        json={
            "meal_id": "photo-parent-legacy",
            "items": [
                {"state": "included", "item_id": "new", "estimate": _accepted_estimate(item_name="New meal", calories=222)}
            ],
        },
    )

    rows = data_store.get_food_logs(1)
    child_client_id = module._meal_item_client_id("photo-parent-legacy", {"item_id": "new"}, 0)
    assert res.status_code == 200, res.get_data(as_text=True)
    assert {row["client_id"] for row in rows} == {"photo-parent-legacy", child_client_id}
    assert next(row for row in rows if row["client_id"] == "photo-parent-legacy")["item_name"] == "Legacy snack"


def test_multi_item_accept_validates_all_items_before_persisting(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)

    res = module.app.test_client().post(
        "/api/meal-intake/photo-parent-invalid/accept",
        json={
            "meal_id": "photo-meal-invalid",
            "items": [
                {"state": "included", "item_id": "ok", "estimate": _accepted_estimate(item_name="OK", calories=100)},
                {"state": "included", "item_id": "bad", "estimate": "not an object"},
            ],
        },
    )

    assert res.status_code == 400
    assert data_store.get_food_logs(1) == []
    assert data_store.get_meal_acceptance_event(1, "photo-meal-invalid") is None


def test_multi_item_accept_retry_completes_partial_existing_rows(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "photo-parent-partial"
    partial_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "a"}, 0)
    data_store.add_food_log(
        1,
        {
            "client_id": partial_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            "item_name": "A",
            "calories": 100,
            "meal_id": "photo-meal-partial",
            "meal_item_id": "a",
            "item_index": 0,
            "item_state": "included",
        },
    )

    res = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": "photo-meal-partial",
            "items": [
                {"state": "included", "item_id": "a", "estimate": _accepted_estimate(item_name="A", calories=100)},
                {"state": "included", "item_id": "b", "estimate": _accepted_estimate(item_name="B", calories=200)},
            ],
        },
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    rows = data_store.get_food_logs(1)
    assert len(rows) == 2
    assert {row["item_name"] for row in rows} == {"A", "B"}
    event = data_store.get_meal_acceptance_event(1, "photo-meal-partial")
    assert set(event["included_client_ids"]) == {row["client_id"] for row in rows}


def test_multi_item_accept_replays_matching_event_when_rows_are_missing(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "photo-parent-event-only"
    client_ids = [
        module._meal_item_client_id(parent_client_id, {"item_id": "a"}, 0),
        module._meal_item_client_id(parent_client_id, {"item_id": "b"}, 1),
    ]
    module.personal_vocab.record_negative_feedback(
        1,
        "plate",
        _accepted_estimate(item_name="Plate", calories=10),
        "skipped",
    )
    data_store.save_meal_acceptance_event(
        1,
        meal_id="photo-meal-event-only",
        status="logged",
        included_client_ids=client_ids,
        skipped_count=1,
        deleted_count=0,
    )

    res = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": "photo-meal-event-only",
            "items": [
                {"state": "included", "item_id": "a", "estimate": _accepted_estimate(item_name="A", calories=100)},
                {"state": "included", "item_id": "b", "estimate": _accepted_estimate(item_name="B", calories=200)},
                {"state": "skipped", "text": "plate", "estimate": _accepted_estimate(item_name="Plate", calories=10)},
            ],
        },
    )

    body = res.get_json()
    rows = data_store.get_food_logs(1)
    assert res.status_code == 200, res.get_data(as_text=True)
    assert body["status"] == "logged"
    assert body["meal_totals"]["calories"] == 300
    assert len(rows) == 2
    assert {row["client_id"] for row in rows} == set(client_ids)
    assert data_store.get_personal_vocab_entry(1, "plate")["skip_count"] == 1


def test_multi_item_accept_existing_event_missing_row_still_blocks_unclear_item(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "photo-parent-event-missing-blocked"
    meal_id = "photo-meal-event-missing-blocked"
    item_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "blocked-1"}, 0)
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="logged",
        included_client_ids=[item_client_id],
        skipped_count=0,
        deleted_count=0,
    )

    res = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "included",
                    "item_id": "blocked-1",
                    "estimate": _accepted_estimate(
                        item_name="Mystery dish",
                        calories=500,
                        confidence=0.40,
                    ),
                }
            ],
        },
    )

    body = res.get_json()
    assert res.status_code == 409
    assert body["save_blocked_item_ids"] == ["blocked-1"]
    assert data_store.get_food_logs(1) == []
    assert data_store.list_pending_workout_adaptation_windows(1) == []


def test_multi_item_accept_replay_repairs_missing_event_when_rows_exist(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "photo-parent-rows-only"
    meal_id = "photo-meal-rows-only"
    item_a_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "a"}, 0)
    item_b_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "b"}, 1)
    data_store.add_food_log(
        1,
        {
            "client_id": item_a_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            "item_name": "A",
            "calories": 100,
            "meal_id": meal_id,
            "meal_item_id": "a",
            "item_index": 0,
            "item_state": "included",
        },
    )
    data_store.add_food_log(
        1,
        {
            "client_id": item_b_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            "item_name": "B",
            "calories": 200,
            "meal_id": meal_id,
            "meal_item_id": "b",
            "item_index": 1,
            "item_state": "included",
        },
    )

    res = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "a", "estimate": _accepted_estimate(item_name="A", calories=100)},
                {"state": "included", "item_id": "b", "estimate": _accepted_estimate(item_name="B", calories=200)},
                {"state": "skipped", "text": "plate", "estimate": _accepted_estimate(item_name="Plate", calories=10)},
            ],
        },
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    assert len(data_store.get_food_logs(1)) == 2
    event = data_store.get_meal_acceptance_event(1, meal_id)
    assert set(event["included_client_ids"]) == {item_a_client_id, item_b_client_id}
    assert event["skipped_count"] == 1
    assert data_store.get_personal_vocab_entry(1, "plate")["skip_count"] == 1


def test_multi_item_accept_all_skipped_discards_without_consumed_rows(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)

    client = module.app.test_client()
    payload = {
        "meal_id": "photo-meal-discard",
        "items": [
            {"state": "skipped", "text": "fork", "estimate": _accepted_estimate(item_name="Fork", calories=100)},
            {"state": "deleted", "text": "empty plate", "estimate": _accepted_estimate(item_name="Empty plate", calories=50)},
        ],
    }
    res = client.post(
        "/api/meal-intake/photo-parent-discard/accept",
        json=payload,
    )
    retry = client.post("/api/meal-intake/photo-parent-discard/accept", json=payload)

    assert res.status_code == 200, res.get_data(as_text=True)
    assert retry.status_code == 200, retry.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "discarded"
    assert body["included_count"] == 0
    assert body["food_logs"] == []
    assert body["meal_totals"]["calories"] == 0
    assert data_store.get_food_logs(1) == []
    assert data_store.get_personal_vocab_entry(1, "fork")["skip_count"] == 1
    assert data_store.get_personal_vocab_entry(1, "empty plate")["deleted_count"] == 1
    event = data_store.get_meal_acceptance_event(1, "photo-meal-discard")
    assert event["status"] == "discarded"
    assert event["included_client_ids"] == []


def test_multi_item_accept_skipped_items_do_not_affect_nutrition_endpoints(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-22")
    client = module.app.test_client()

    res = client.post(
        "/api/meal-intake/photo-parent-nutrition/accept",
        json={
            "meal_id": "photo-meal-nutrition",
            "local_date": "2026-05-22",
            "items": [
                {"state": "included", "item_id": "egg", "estimate": _accepted_estimate(item_name="Egg", calories=100, protein_g=8, carbs_g=1, fat_g=7, sodium_mg=70, fiber_g=0)},
                {"state": "skipped", "text": "receipt", "estimate": _accepted_estimate(item_name="Receipt", calories=900, protein_g=50)},
                {"state": "deleted", "text": "shadow item", "estimate": _accepted_estimate(item_name="Shadow", calories=400, protein_g=20)},
            ],
        },
    )
    today = client.get("/api/nutrition-today")
    history = client.get("/api/nutrition-history")

    assert res.status_code == 200
    assert today.status_code == 200
    assert history.status_code == 200
    today_body = today.get_json()
    history_text = str(history.get_json())
    assert today_body["calories"] == 100
    assert "Receipt" not in history_text
    assert "Shadow" not in history_text


def test_meal_composer_js_hydrates_pending_and_rolls_back_failed_discard():
    """Static guard for the browser-only FIT-67 pending-review workflow."""
    source = Path("static/js/app.js").read_text()

    assert "api('/api/meal-intake/pending')" in source
    assert "hydrateMealPending();" in source
    # FIT-134 rebase routes v2 entries through normalizeMealV2Entry while
    # keeping the legacy upsertMealPendingEntry path for single-item entries.
    assert "pending.forEach((entry) => {" in source
    assert "upsertMealPendingEntry(entry);" in source
    assert "correction_state=pending_review" in source
    assert "result.removed !== true" in source
    assert "Discard failed — retry when connected" in source
    assert "toast('Review the estimate before it counts toward today.', 'warn');\n            refreshMacroCard();" in source
    assert "local_timestamp: entry.local_timestamp || null" in source
    assert "local_timestamp: entry.local_timestamp || fallback.local_timestamp || entry.logged_at" in source


def test_fit65_stub_markers_are_removed():
    """FIT-65 guard: the old FIT-60 stub wrapper and test file stay removed."""
    app_source = Path("app.py").read_text()

    assert "FIT-60 STUB" not in app_source
    assert "_meal_intake_stub_estimate" not in app_source
    assert not Path("tests/test_meal_intake_stub.py").exists()


def test_meal_composer_js_surfaces_open_food_facts_attribution():
    """Static guard for the browser-only FIT-80 source-provenance surface."""
    js = Path("static/js/app.js").read_text()
    html = Path("templates/index.html").read_text()
    css = Path("static/css/style.css").read_text()

    assert "mealEstimateProvenanceHtml(est)" in js
    assert "nutritionix: 'Nutritionix'" in js
    assert "usda_fdc: 'USDA'" in js
    assert "open_food_facts: 'Open Food Facts'" in js
    assert "fallback_text_estimate: 'Fallback preset'" in js
    assert "renderMealComposerProvenance(payload.estimate, ctx.clientId)" in js
    assert "renderMealComposerProvenance(payload.estimate, newClientId)" in js
    assert "renderMealComposerProvenance(edited, clientId)" in js
    assert "clearMealComposerStatus(clientId);\n            toast('Meal removed', 'ok');" in js
    assert "status.dataset.provenanceClientId !== String(clientId)" in js
    # FIT-138: input/change handlers were refactored for the multi-photo
    # state machine; assert the post-FIT-138 shapes.
    assert "text.addEventListener('input'" in js
    assert "clearMealComposerStatus();" in js
    assert "refreshMealSubmitState();" in js
    assert "saveMealDraft();" in js
    assert "image.addEventListener('change', () => {\n                clearMealComposerStatus();" in js
    assert "onMealComposerImageSelected(image.files);" in js
    # FIT-138: per-thumb × replaces the single previewClear; per-thumb removal
    # is wired inside renderMealComposerThumbs().
    assert "function renderMealComposerThumbs()" in js
    assert "removeMealComposerImage(" in js
    assert "est.off_attribution" in js
    assert "Source: Open Food Facts (ODbL/DbCL data; product images CC BY-SA)" in js
    assert "attrUrl || (est && (est.verified_source_url || est.source_url || est.product_url))" in js
    assert "target=\"_blank\" rel=\"noopener noreferrer\"" in js
    assert 'data-provenance-surface="meal-estimates"' in html
    assert ".meal-pending-provenance" in css
    assert ".meal-composer-status--provenance" in css


# ──────────────────────────────────────────────────────────────────
# FIT-138: multi-image capture acceptance (plural ``images`` key,
# legacy ``image`` back-compat, per-file + count + aggregate caps).
# Every successful capture response must land in pending_review.
# ──────────────────────────────────────────────────────────────────


def _png_bytes(filler: int = 32) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\0" * filler


def test_meal_intake_accepts_two_images_under_plural_key(monkeypatch):
    """FIT-138 AC4: 2 photos in one submission → 200, pending_review, vision
    adapter receives the photos as one combined call."""
    module = _client(monkeypatch)
    seen = {}

    def fake_describe(*_args, **kwargs):
        seen["images"] = kwargs.get("images")
        seen["image_bytes"] = kwargs.get("image_bytes")
        return {
            "provider": "claude",
            "item_description": "salad bowl with chicken",
            "portion_hint": "1 bowl",
            "confidence": 0.82,
            "ambiguous": False,
            "uncertainty_notes": [],
        }

    monkeypatch.setattr(module.vision_estimator, "describe", fake_describe)
    monkeypatch.setattr(module.branded_food_lookup, "lookup", lambda *_a, **_kw: _accepted_estimate())
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: {"client_id": r["client_id"], **r})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-multi-2",
            "images": [
                (io.BytesIO(_png_bytes()), "front.png", "image/png"),
                (io.BytesIO(_png_bytes()), "side.png", "image/png"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "pending_review"
    # Both images reached the vision adapter as one combined call.
    assert seen["images"] is not None
    assert len(seen["images"]) == 2
    assert all(isinstance(item, tuple) and len(item) == 2 for item in seen["images"])


def test_meal_intake_accepts_four_images(monkeypatch):
    """FIT-138 AC4: 4 photos in one submission → 200, pending_review."""
    module = _client(monkeypatch)
    _stub_vision(monkeypatch, module)
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: {"client_id": r["client_id"], **r})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-multi-4",
            "images": [
                (io.BytesIO(_png_bytes()), f"p{i}.png", "image/png")
                for i in range(4)
            ],
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["status"] == "pending_review"


def test_meal_intake_rejects_five_images_with_400(monkeypatch):
    """FIT-138 AC4: 5+ photos → 400 too-many."""
    module = _client(monkeypatch)
    _stub_vision(monkeypatch, module)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-multi-5",
            "images": [
                (io.BytesIO(_png_bytes()), f"p{i}.png", "image/png")
                for i in range(5)
            ],
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    msg = res.get_json()["error"]["message"]
    assert "too many" in msg.lower() or "4" in msg


def test_meal_intake_rejects_aggregate_over_18_mb_with_413(monkeypatch):
    """FIT-138 AC4: aggregate bytes > 18 MB → 413."""
    module = _client(monkeypatch)
    _stub_vision(monkeypatch, module)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    # 4 × 5 MB = 20 MB total, each within the per-file 6 MB cap.
    big = b"\0" * (5 * 1024 * 1024)
    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-multi-big",
            "images": [
                (io.BytesIO(big), f"big{i}.jpg", "image/jpeg")
                for i in range(4)
            ],
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 413
    msg = res.get_json()["error"]["message"]
    assert "18" in msg or "total" in msg.lower()


def test_meal_intake_legacy_image_key_still_accepted(monkeypatch):
    """FIT-138 back-compat: the singular ``image`` FormData key still works
    (FIT-128 pending-card retry path may still send it during rollout)."""
    module = _client(monkeypatch)
    _stub_vision(monkeypatch, module)
    monkeypatch.setattr(module, "add_food_log", lambda _u, r: {"client_id": r["client_id"], **r})

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-legacy-1",
            "image": (io.BytesIO(_png_bytes()), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["status"] == "pending_review"


def test_meal_intake_rejects_oversize_individual_image_in_multi(monkeypatch):
    """FIT-138: even within a batch, any single photo > 6 MB still returns 413."""
    module = _client(monkeypatch)
    _stub_vision(monkeypatch, module)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    small = _png_bytes()
    big = b"\0" * (6 * 1024 * 1024 + 1)
    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "client_id": "meal-multi-oversize",
            "images": [
                (io.BytesIO(small), "ok.png", "image/png"),
                (io.BytesIO(big), "huge.jpg", "image/jpeg"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 413
    assert "6 MB" in res.get_json()["error"]["message"] or "exceeds" in res.get_json()["error"]["message"].lower()


def _barcode_estimate(**overrides):
    estimate = {
        "item_name": "Barcode protein bar",
        "portion_description": "1 bar (68 g)",
        "meal_type": "snack",
        "calories": 250,
        "protein_g": 10,
        "carbs_g": 43,
        "fat_g": 5,
        "sodium_mg": 190,
        "fiber_g": 5,
        "confidence": 0.88,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "nutritionix_barcode",
        "external_food_id": "barcode-bar-1",
        "portion_basis": "Nutritionix UPC label serving",
    }
    estimate.update(overrides)
    return estimate


def test_meal_intake_barcode_returns_pending_review_for_verified_lookup(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(
        module.branded_food_lookup,
        "lookup_barcode",
        lambda barcode, **kwargs: _barcode_estimate(external_food_id=barcode),
    )

    res = module.app.test_client().post(
        "/api/meal-intake/barcode",
        json={"client_id": "barcode-meal-1", "barcode": "0123-4567-8905", "local_date": "2026-05-24"},
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["barcode"] == "012345678905"
    assert body["lookup_source"] == "nutritionix_barcode"
    assert body["cache_hit"] is False
    assert body["pending_source"] is False
    assert body["estimate"]["source"] == "nutritionix_barcode"
    assert body["food_log"]["client_id"] == "barcode-meal-1"
    assert body["food_log"]["correction_state"] == "pending_review"


def test_meal_intake_barcode_reports_cache_hit_metadata(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(
        module.branded_food_lookup,
        "lookup_barcode",
        lambda *_a, **_kw: _barcode_estimate(source="local_cache", underlying_source="open_food_facts_barcode"),
    )

    res = module.app.test_client().post(
        "/api/meal-intake/barcode",
        json={"client_id": "barcode-cache-1", "barcode": "012345678905"},
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["cache_hit"] is True
    assert body["lookup_source"] == "open_food_facts_barcode"
    assert body["estimate"]["source"] == "local_cache"


def test_meal_intake_barcode_returns_404_for_unknown_without_pending(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module.branded_food_lookup, "lookup_barcode", lambda *_a, **_kw: None)

    res = module.app.test_client().post(
        "/api/meal-intake/barcode",
        json={"client_id": "barcode-miss-1", "barcode": "000000000000"},
    )

    assert res.status_code == 404
    assert res.get_json()["error"]["code"] == "barcode_not_found"
    assert data_store.get_food_logs(1) == []


def test_meal_intake_barcode_allow_pending_creates_uncached_review_draft(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module.branded_food_lookup, "lookup_barcode", lambda *_a, **_kw: None)

    res = module.app.test_client().post(
        "/api/meal-intake/barcode",
        json={"client_id": "barcode-pending-1", "barcode": "000000000000", "allow_pending": True},
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["pending_source"] is True
    assert body["cache_hit"] is False
    assert body["lookup_source"] == "barcode_pending_source"
    assert body["estimate"]["source"] == "barcode_pending_source"
    assert body["estimate"]["external_food_id"] == "000000000000"
    assert body["save_blocked_item_ids"] == ["item-1"]


def test_barcode_pending_source_accept_requires_real_nutrition_before_vocab_training(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit349-barcode-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module.branded_food_lookup, "lookup_barcode", lambda *_a, **_kw: None)
    client = module.app.test_client()

    pending = client.post(
        "/api/meal-intake/barcode",
        json={"client_id": "fit349-barcode-1", "barcode": "000000000000", "allow_pending": True},
    )
    assert pending.status_code == 200, pending.get_data(as_text=True)
    pending_body = pending.get_json()

    malformed = client.post(
        "/api/meal-intake/fit349-barcode-1/accept",
        json={"meal_id": "fit349-barcode-1", "items": "not-a-list"},
    )
    assert malformed.status_code == 400, malformed.get_data(as_text=True)
    assert data_store.get_meal_review_snapshot(1, "fit349-barcode-1") is not None

    laundered = dict(pending_body["items"][0]["estimate"])
    laundered.update({"confidence": 0.8, "ambiguous": False, "source": "manual_review_estimate"})
    laundered_item = {
        "item_id": pending_body["items"][0]["item_id"],
        "state": "included",
        "estimate": laundered,
    }
    mismatched_meal_id = client.post(
        "/api/meal-intake/fit349-barcode-1/accept",
        json={
            "meal_id": "fit349-route-body-mismatch",
            "items": [laundered_item],
        },
    )
    assert mismatched_meal_id.status_code == 400, mismatched_meal_id.get_data(as_text=True)
    assert mismatched_meal_id.get_json()["error"]["code"] == "invalid_field"
    assert data_store.get_meal_review_snapshot(1, "fit349-barcode-1") is not None
    assert data_store.get_meal_acceptance_event(1, "fit349-route-body-mismatch") is None
    assert [row["correction_state"] for row in data_store.get_food_logs(1)] == ["pending_review"]
    assert data_store.list_personal_vocab_entries(1) == []

    resolved_through_wrong_route = dict(laundered)
    resolved_through_wrong_route.update({"calories": 180, "protein_g": 12, "carbs_g": 8, "fat_g": 7})
    mismatched_route_id = client.post(
        "/api/meal-intake/fit349-unrelated-route/accept",
        json={
            "meal_id": "fit349-barcode-1",
            "items": [{**laundered_item, "estimate": resolved_through_wrong_route}],
        },
    )
    assert mismatched_route_id.status_code == 400, mismatched_route_id.get_data(as_text=True)
    assert mismatched_route_id.get_json()["error"]["code"] == "invalid_field"
    assert data_store.get_meal_review_snapshot(1, "fit349-barcode-1") is not None
    assert data_store.get_meal_acceptance_event(1, "fit349-barcode-1") is None
    assert [row["correction_state"] for row in data_store.get_food_logs(1)] == ["pending_review"]
    assert data_store.list_personal_vocab_entries(1) == []

    renamed = client.post(
        "/api/meal-intake/fit349-barcode-1/accept",
        json={
            "meal_id": "fit349-barcode-1",
            "items": [{**laundered_item, "item_id": "client-renamed-item"}],
        },
    )
    assert renamed.status_code == 400, renamed.get_data(as_text=True)

    rejected = client.post(
        "/api/meal-intake/fit349-barcode-1/accept",
        json={"meal_id": "fit349-barcode-1", "items": [laundered_item]},
    )

    assert rejected.status_code == 422, rejected.get_data(as_text=True)
    assert rejected.get_json()["error"]["code"] == "placeholder_nutrition_not_resolved"
    assert data_store.get_meal_review_snapshot(1, "fit349-barcode-1") is not None
    pending_rows = data_store.get_food_logs(1)
    assert len(pending_rows) == 1
    assert pending_rows[0]["correction_state"] == "pending_review"
    assert data_store.list_personal_vocab_entries(1) == []

    tiny_nutrition = dict(laundered)
    tiny_nutrition.update({"calories": 0.1, "protein_g": 0.01})
    still_unresolved = client.post(
        "/api/meal-intake/fit349-barcode-1/accept",
        json={
            "meal_id": "fit349-barcode-1",
            "items": [{**laundered_item, "estimate": tiny_nutrition}],
        },
    )
    assert still_unresolved.status_code == 422, still_unresolved.get_data(as_text=True)

    resolved = dict(laundered)
    resolved.update({"calories": 180, "protein_g": 12, "carbs_g": 8, "fat_g": 7})
    accepted = client.post(
        "/api/meal-intake/fit349-barcode-1/accept",
        json={
            "meal_id": "fit349-barcode-1",
            "items": [{**laundered_item, "estimate": resolved}],
        },
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    assert data_store.get_meal_review_snapshot(1, "fit349-barcode-1") is None
    assert len(data_store.get_food_logs(1)) == 1
    assert len(data_store.list_personal_vocab_entries(1)) == 1


def test_multi_item_accept_all_zero_nonbarcode_estimate_does_not_train_vocab(monkeypatch, tmp_path):
    """FIT-349 follow-up: _review_placeholder_nutrition_not_resolved only guarded
    barcode_pending_source originals, so any other source label (e.g. a laundered
    manual_review_estimate) could accept and train personal vocab on all-zero
    server-sanitized nutrition. Vocab learning must be gated on the sanitized
    calories/macros themselves, not the source label."""
    monkeypatch.setenv("SECRET_KEY", "fit349-followup-zero-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    client = module.app.test_client()

    zero_estimate = _accepted_estimate(
        calories=0,
        protein_g=0,
        carbs_g=0,
        fat_g=0,
        confidence=0.9,
        ambiguous=False,
        source="manual_review_estimate",
    )
    original_estimate = {
        "source": "manual_review_estimate",
        "calories": 0,
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 0,
    }

    res = client.post(
        "/api/meal-intake/fit349-followup-zero-1/accept",
        json={
            "meal_id": "fit349-followup-zero-1",
            "items": [
                {
                    "state": "included",
                    "item_id": "zero-item-1",
                    "estimate": zero_estimate,
                    "original_estimate": original_estimate,
                },
            ],
        },
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["calories"] == 0
    assert data_store.list_personal_vocab_entries(1) == []


def test_single_item_accept_zero_calorie_manual_water_log_persists_without_training_vocab(monkeypatch, tmp_path):
    """CRITICAL CONSTRAINT for the FIT-349 follow-up: deliberate zero-calorie
    manual logging (water, black coffee) must still persist as a food_logs
    row. The fix must not repurpose the all-zero-nutrition guard to reject
    intentional zero-cal entries -- it should only withhold vocab training
    on them, since an all-zero canonical resolution is useless to learn."""
    monkeypatch.setenv("SECRET_KEY", "fit349-followup-water-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    client = module.app.test_client()

    water_estimate = _accepted_estimate(
        item_name="Water",
        portion_description="16 oz",
        calories=0,
        protein_g=0,
        carbs_g=0,
        fat_g=0,
        sodium_mg=0,
        fiber_g=0,
        confidence=0.95,
        ambiguous=False,
        source="manual_review_estimate",
    )

    res = client.post(
        "/api/meal-intake/fit349-followup-water-1/accept",
        json={
            "estimate": water_estimate,
            "original_estimate": dict(water_estimate),
            "text": "water",
        },
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["status"] == "logged"
    assert body["food_log"]["calories"] == 0
    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["calories"] == 0
    assert data_store.list_personal_vocab_entries(1) == []


def test_barcode_pending_source_without_snapshot_still_requires_real_nutrition(monkeypatch):
    module = _client(monkeypatch)
    data_store.add_food_log(
        1,
        {
            "client_id": "fit349-pending-without-snapshot",
            "date": "2026-07-09",
            "logged_at": "2026-07-09T12:00:00",
            "item_name": "Barcode 000000000000",
            "calories": 0,
            "protein_g": 0,
            "carbs_g": 0,
            "fat_g": 0,
            "source": "barcode_pending_source",
            "correction_state": "pending_review",
        },
    )

    res = module.app.test_client().post(
        "/api/meal-intake/fit349-pending-without-snapshot/accept",
        json={"estimate": _accepted_estimate(calories=0, protein_g=0, carbs_g=0, fat_g=0, source="manual_review_estimate")},
    )

    assert res.status_code == 422, res.get_data(as_text=True)
    assert res.get_json()["error"]["code"] == "placeholder_nutrition_not_resolved"


def test_snapshotless_single_accept_preserves_stored_barcode_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit349-single-provenance-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module.branded_food_lookup, "lookup_barcode", lambda *_a, **_kw: None)
    client = module.app.test_client()

    pending = client.post(
        "/api/meal-intake/barcode",
        json={"client_id": "fit349-single-provenance", "barcode": "000000000000", "allow_pending": True},
    )
    assert pending.status_code == 200, pending.get_data(as_text=True)
    pending_body = pending.get_json()
    data_store.delete_meal_review_snapshot(1, "fit349-single-provenance")

    resolved = dict(pending_body["estimate"])
    resolved.update({
        "source": "manual_review_estimate",
        "confidence": 0.8,
        "ambiguous": False,
        "calories": 180,
        "protein_g": 12,
        "carbs_g": 8,
        "fat_g": 7,
    })
    accepted = client.post(
        "/api/meal-intake/fit349-single-provenance/accept",
        json={"estimate": resolved, "original_estimate": resolved},
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["correction_state"] == "corrected"
    assert rows[0]["original_estimate"]["source"] == "barcode_pending_source"
    assert all(
        rows[0]["original_estimate"][field] == 0
        for field in ("calories", "protein_g", "carbs_g", "fat_g")
    )
    vocab = data_store.list_personal_vocab_entries(1)
    assert len(vocab) == 1
    assert vocab[0]["accept_count"] == 0
    assert vocab[0]["correct_count"] == 1


def test_snapshotless_items_cannot_accept_zero_barcode_placeholder(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit349-coordinator-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module.branded_food_lookup, "lookup_barcode", lambda *_a, **_kw: None)
    client = module.app.test_client()

    pending = client.post(
        "/api/meal-intake/barcode",
        json={"client_id": "fit349-snapshotless-zero", "barcode": "000000000000", "allow_pending": True},
    )
    assert pending.status_code == 200, pending.get_data(as_text=True)
    body = pending.get_json()
    data_store.delete_meal_review_snapshot(1, "fit349-snapshotless-zero")

    estimate = dict(body["items"][0]["estimate"])
    estimate.update({"source": "manual_review_estimate", "confidence": 0.8, "ambiguous": False})
    response = client.post(
        "/api/meal-intake/fit349-snapshotless-zero/accept",
        json={
            "meal_id": "fit349-snapshotless-zero",
            "items": [{"item_id": body["items"][0]["item_id"], "state": "included", "estimate": estimate}],
        },
    )

    rows = data_store.get_food_logs(1)
    assert response.status_code == 422, response.get_data(as_text=True)
    assert [(row["correction_state"], row["calories"]) for row in rows] == [("pending_review", 0)]
    assert data_store.list_personal_vocab_entries(1) == []


def test_snapshotless_items_accept_resolved_barcode_placeholder_and_train_vocab(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit349-coordinator-positive-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module.branded_food_lookup, "lookup_barcode", lambda *_a, **_kw: None)
    client = module.app.test_client()

    pending = client.post(
        "/api/meal-intake/barcode",
        json={"client_id": "fit349-snapshotless-positive", "barcode": "000000000000", "allow_pending": True},
    )
    assert pending.status_code == 200, pending.get_data(as_text=True)
    body = pending.get_json()
    data_store.delete_meal_review_snapshot(1, "fit349-snapshotless-positive")

    estimate = dict(body["items"][0]["estimate"])
    estimate.update({
        "source": "manual_review_estimate",
        "confidence": 0.8,
        "ambiguous": False,
        "calories": 180,
        "protein_g": 12,
        "carbs_g": 8,
        "fat_g": 7,
    })
    response = client.post(
        "/api/meal-intake/fit349-snapshotless-positive/accept",
        json={
            "meal_id": "fit349-snapshotless-positive",
            "items": [{"item_id": body["items"][0]["item_id"], "state": "included", "estimate": estimate}],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert [(row["correction_state"], row["calories"]) for row in data_store.get_food_logs(1)] == [("corrected", 180)]
    assert len(data_store.list_personal_vocab_entries(1)) == 1


def test_meal_intake_barcode_validates_json_client_id_and_barcode(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()

    form_res = client.post(
        "/api/meal-intake/barcode",
        data={"client_id": "barcode-invalid-1", "barcode": "012345678905"},
    )
    assert form_res.status_code == 415
    assert form_res.get_json()["error"]["code"] == "invalid_content_type"

    missing_client = client.post("/api/meal-intake/barcode", json={"barcode": "012345678905"})
    assert missing_client.status_code == 400
    assert missing_client.get_json()["error"]["code"] == "missing_field"

    bad_barcode = client.post(
        "/api/meal-intake/barcode",
        json={"client_id": "barcode-invalid-2", "barcode": "abc"},
    )
    assert bad_barcode.status_code == 400
    assert bad_barcode.get_json()["error"]["code"] == "invalid_barcode"

    bad_pending = client.post(
        "/api/meal-intake/barcode",
        json={"client_id": "barcode-invalid-3", "barcode": "012345678905", "allow_pending": "yes"},
    )
    assert bad_pending.status_code == 400
    assert bad_pending.get_json()["error"]["code"] == "invalid_field"


def test_meal_intake_barcode_rejects_oversize_content_length(monkeypatch):
    module = _client(monkeypatch)

    res = module.app.test_client().post(
        "/api/meal-intake/barcode",
        data='{"client_id":"barcode-large-1","barcode":"012345678905"}',
        content_type="application/json",
        environ_overrides={"CONTENT_LENGTH": str(18 * 1024 * 1024 + 1)},
    )

    assert res.status_code == 413
    assert res.get_json()["error"]["code"] == "payload_too_large"


def test_meal_intake_barcode_replays_existing_snapshot_by_client_id(monkeypatch):
    module = _client(monkeypatch)
    calls = {"count": 0}

    def lookup(*_a, **_kw):
        calls["count"] += 1
        return _barcode_estimate(calories=250)

    monkeypatch.setattr(module.branded_food_lookup, "lookup_barcode", lookup)
    client = module.app.test_client()

    first = client.post(
        "/api/meal-intake/barcode",
        json={"client_id": "barcode-replay-1", "barcode": "012345678905"},
    )
    assert first.status_code == 200, first.get_data(as_text=True)
    monkeypatch.setattr(
        module.branded_food_lookup,
        "lookup_barcode",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("idempotent replay should not re-query providers")),
    )
    second = client.post(
        "/api/meal-intake/barcode",
        json={"client_id": "barcode-replay-1", "barcode": "012345678905"},
    )

    assert second.status_code == 200, second.get_data(as_text=True)
    assert second.get_json()["estimate"]["calories"] == 250
    assert calls["count"] == 1


def test_meal_intake_text_preserves_parser_item_breakdown(monkeypatch):
    module = _client(monkeypatch)

    def fake_parse(_text, **_kw):
        return {
            "fallback_used": True,
            "fallback_reason": "all_endpoints_failed",
            "estimate": {
                "item_name": "Protein shake; Breakfast taco",
                "portion_description": "Protein shake; approx 2 servings",
                "meal_type": "snack",
                "calories": 850,
                "protein_g": 58,
                "carbs_g": 70,
                "fat_g": 36,
                "sodium_mg": 1420,
                "fiber_g": 8,
                "confidence": 0.55,
                "ambiguous": True,
                "uncertainty_notes": ["Rough estimate — AI didn't run; review before logging."],
                "source": "fallback_text_estimate",
                "items": [
                    {
                        "item_id": "fallback-item-1",
                        "item_name": "Protein shake",
                        "quantity": 1,
                        "estimate": _accepted_estimate(
                            item_name="Protein shake",
                            meal_type="snack",
                            calories=210,
                            protein_g=30,
                            carbs_g=14,
                            fat_g=4,
                            sodium_mg=180,
                            fiber_g=2,
                            confidence=0.55,
                            ambiguous=False,
                            uncertainty_notes=[
                                "Rough estimate — AI didn't run; review before logging.",
                                "Multi-item fallback split from text; confirm each quantity.",
                            ],
                            source="fallback_text_estimate",
                        ),
                    },
                    {
                        "item_id": "fallback-item-2",
                        "item_name": "Breakfast taco",
                        "quantity": 2,
                        "portion_hint": "approx 2 servings",
                        "estimate": _accepted_estimate(
                            item_name="Breakfast taco",
                            portion_description="approx 2 servings",
                            meal_type="breakfast",
                            calories=640,
                            protein_g=28,
                            carbs_g=56,
                            fat_g=32,
                            sodium_mg=1240,
                            fiber_g=6,
                            confidence=0.55,
                            ambiguous=False,
                            uncertainty_notes=[
                                "Rough estimate — AI didn't run; review before logging.",
                                "Multi-item fallback split from text; confirm each quantity.",
                            ],
                            source="fallback_text_estimate",
                        ),
                    },
                ],
            },
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parse)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "protein shake and 2 tacos", "client_id": "meal-text-items-1"},
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["fallback_used"] is True
    assert [item["name"] for item in body["items"]] == ["Protein shake", "Breakfast taco"]
    assert body["items"][1]["portion"] == "approx 2 servings"
    assert body["meal_totals"]["calories"] == 850
    assert body["save_blocked_item_ids"] == []
    assert any("AI didn't run" in note for note in body["estimate"]["uncertainty_notes"])
    assert any("Multi-item fallback" in note for note in body["estimate"]["uncertainty_notes"])
    snapshot = data_store.get_meal_review_snapshot(1, "meal-text-items-1")
    assert snapshot["next_item_seq"] == 3


def test_meal_intake_photo_text_item_breakdown_preserves_image_origin(monkeypatch):
    module = _client(monkeypatch)

    monkeypatch.setattr(
        module.vision_estimator,
        "describe",
        lambda *_a, **_kw: (_ for _ in ()).throw(module.vision_estimator.VisionEstimatorError("vision down")),
    )

    def fake_parse(_text, **_kw):
        return {
            "fallback_used": True,
            "fallback_reason": "all_endpoints_failed",
            "estimate": {
                "item_name": "Protein shake; Breakfast taco",
                "portion_description": "Protein shake; approx 2 servings",
                "meal_type": "snack",
                "calories": 850,
                "protein_g": 58,
                "carbs_g": 70,
                "fat_g": 36,
                "sodium_mg": 1420,
                "fiber_g": 8,
                "confidence": 0.55,
                "ambiguous": True,
                "uncertainty_notes": ["Rough estimate — AI didn't run; review before logging."],
                "source": "fallback_text_estimate",
                "items": [
                    {
                        "item_id": "fallback-item-1",
                        "item_name": "Protein shake",
                        "estimate": _accepted_estimate(
                            item_name="Protein shake",
                            meal_type="snack",
                            calories=210,
                            protein_g=30,
                            carbs_g=14,
                            fat_g=4,
                            sodium_mg=180,
                            fiber_g=2,
                            confidence=0.55,
                            ambiguous=False,
                            source="fallback_text_estimate",
                        ),
                    },
                    {
                        "item_id": "fallback-item-2",
                        "item_name": "Breakfast taco",
                        "estimate": _accepted_estimate(
                            item_name="Breakfast taco",
                            meal_type="breakfast",
                            calories=640,
                            protein_g=28,
                            carbs_g=56,
                            fat_g=32,
                            sodium_mg=1240,
                            fiber_g=6,
                            confidence=0.55,
                            ambiguous=False,
                            source="fallback_text_estimate",
                        ),
                    },
                ],
            },
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parse)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "protein shake and 2 tacos",
            "client_id": "meal-photo-text-items-1",
            "image": (io.BytesIO(b"fake-image"), "meal.jpg"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["fallback_used"] is True
    assert body["fallback_reason"] == "all_endpoints_failed"
    assert body["photo_retention"]["image_received"] is True
    assert all(item["estimate"].get("from_image") is True for item in body["items"])


def test_barcode_lookup_cache_round_trip_and_delete_user_data(tmp_path, monkeypatch):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    response = _barcode_estimate()

    data_store.save_barcode_lookup_cache("012345678905", "nutritionix_barcode", response, user_id=1)
    data_store.save_barcode_lookup_cache("012345678905", "nutritionix_barcode", {**response, "item_name": "User 2 bar"}, user_id=2)

    row = data_store.get_barcode_lookup_cache("012345678905", user_id=1)
    assert row["source"] == "nutritionix_barcode"
    assert row["response_json"] == response
    assert data_store.get_barcode_lookup_cache("012345678905", user_id=2)["response_json"]["item_name"] == "User 2 bar"

    data_store.delete_user_data(1)
    assert data_store.get_barcode_lookup_cache("012345678905", user_id=1) is None
    assert data_store.get_barcode_lookup_cache("012345678905", user_id=2)["response_json"]["item_name"] == "User 2 bar"
