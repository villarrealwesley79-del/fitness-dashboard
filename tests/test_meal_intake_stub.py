"""Tests for the FIT-60 meal-intake stub.

These cover the canned-response surface that the UI relies on (logged vs
pending_review vs validation errors), the undo endpoint, and the accept
endpoint. Remove this module once the real intake pipeline (FIT-57/59/5)
replaces the stub.
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


def test_meal_intake_text_only_auto_logs_simple_meal(monkeypatch):
    module = _client(monkeypatch)
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
    assert body["stub"] is True
    assert body["estimate"]["item_name"] == "Eggs and toast"
    assert body["estimate"]["calories"] > 0
    assert body["food_log"]["client_id"] == "meal-abc-1"
    assert persisted["correction_state"] == "accepted"
    assert persisted["source"].startswith("stub_text_estimate")


def test_meal_intake_ambiguous_text_returns_pending_review(monkeypatch):
    module = _client(monkeypatch)
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
    assert body["estimate"]["item_name"] == "Popcorn" or body["estimate"]["item_name"] == "Meal"
    assert body["estimate"]["uncertainty_notes"], "stub should flag uncertainty"
    assert called["count"] == 0, "pending estimates must not auto-persist"


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
