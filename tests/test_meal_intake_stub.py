"""Tests for the /api/meal-intake endpoint.

These cover the wire-up the UI relies on (logged vs pending_review vs
validation errors), the undo endpoint, and the accept endpoint.

Text-only input is routed through the real FIT-59 parser
(meal_text_parser.parse_meal_text); image-bearing input still hits the
FIT-60 stub until FIT-5 ships the vision estimator. Both paths share the
same response shape and the same auto-log threshold (confidence >= 0.65
and not ambiguous).

This module is scheduled for removal in FIT-65 once the real text +
vision intake pipeline replaces the stub entirely.
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

    called = {"count": 0}

    def fake_add_food_log(*_a, **_kw):
        called["count"] += 1
        return {}

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    res = module.app.test_client().post(
        "/api/meal-intake",
        data={"text": "movie theater popcorn, shared", "client_id": "meal-pending-1"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "pending_review"
    assert body["food_log"] is None
    assert body["estimate"]["item_name"] == "Popcorn"
    assert body["estimate"]["uncertainty_notes"], "ambiguous estimates must surface notes"
    assert called["count"] == 0, "pending estimates must not auto-persist"


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

    persisted = {"count": 0}

    def fake_add_food_log(*_a, **_kw):
        persisted["count"] += 1
        return {}

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
    assert body["food_log"] is None
    assert persisted["count"] == 0, "fallback estimates must not auto-persist by default"


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
    assert captured["source"] == "stub_vision_estimate"
    assert captured["context_note"] is None


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


def test_meal_intake_accept_requires_calories(monkeypatch):
    module = _client(monkeypatch)
    monkeypatch.setattr(module, "add_food_log", lambda *_a, **_kw: {})

    res = module.app.test_client().post(
        "/api/meal-intake/meal-bad-accept/accept",
        json={"estimate": {"item_name": "Smoothie"}},
    )
    assert res.status_code == 400
    assert "calories" in res.get_json()["error"]["message"]


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
