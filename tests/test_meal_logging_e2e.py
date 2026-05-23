"""End-to-end meal logging coverage for FIT-83.

These tests keep the HTTP endpoint, canonical SQLite persistence, pending
review hydration, accept/discard actions, and photo privacy contract in one
place. They intentionally stub external estimators so the suite is deterministic
and never depends on network or private photo fixtures.
"""
from __future__ import annotations

import importlib
import io
from dataclasses import dataclass
from pathlib import Path

import pytest


PHOTO_BYTE_PROBE = b"PHOTO_BYTE_PROBE"


@dataclass
class MealE2EHarness:
    module: object
    client: object
    db_path: Path


@pytest.fixture()
def meal_e2e(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit83-e2e-secret")

    module = importlib.import_module("app")
    data_store = importlib.import_module("data_store")
    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()

    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    _stub_vision_pipeline(monkeypatch, module)

    return MealE2EHarness(
        module=module,
        client=module.app.test_client(),
        db_path=db_path,
    )


def _estimate(
    *,
    item_name="Chipotle chicken burrito",
    portion_description=None,
    meal_type="lunch",
    calories=720,
    protein_g=42,
    carbs_g=82,
    fat_g=24,
    sodium_mg=1480,
    fiber_g=9,
    confidence=0.84,
    ambiguous=False,
    uncertainty_notes=None,
    source="ai_text_estimate",
):
    return {
        "item_name": item_name,
        "portion_description": portion_description,
        "meal_type": meal_type,
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "sodium_mg": sodium_mg,
        "fiber_g": fiber_g,
        "confidence": confidence,
        "ambiguous": ambiguous,
        "uncertainty_notes": list(uncertainty_notes or []),
        "source": source,
    }


def _stub_text_parser(monkeypatch, module, estimate, *, fallback_used=False):
    def fake(_text, **_kw):
        return {"estimate": dict(estimate), "fallback_used": fallback_used}

    monkeypatch.setattr(module, "parse_meal_text", fake)


def _stub_vision_pipeline(monkeypatch, module):
    def fake_describe(_image_bytes, *, context_text=None, media_type=None):
        text = (context_text or "").lower()
        if "shared" in text or "popcorn" in text:
            return {
                "provider": "claude",
                "item_description": "shared movie popcorn",
                "portion_hint": "shared tub",
                "confidence": 0.45,
                "ambiguous": True,
                "uncertainty_notes": ["Portion is unclear."],
            }
        return {
            "provider": "claude",
            "item_description": "chipotle chicken burrito",
            "portion_hint": "approx half portion" if "half" in text else "1 burrito",
            "confidence": 0.84,
            "ambiguous": False,
            "uncertainty_notes": [],
        }

    def fake_lookup(text, **_kw):
        norm = (text or "").lower()
        if "shared" in norm or "popcorn" in norm:
            return _estimate(
                item_name="Shared movie popcorn",
                portion_description="shared tub",
                meal_type="snack",
                calories=300,
                protein_g=5,
                carbs_g=36,
                fat_g=18,
                sodium_mg=520,
                fiber_g=6,
                confidence=0.45,
                ambiguous=True,
                uncertainty_notes=["Portion is unclear."],
                source="nutritionix",
            )
        if "half" in norm:
            return _estimate(
                portion_description="approx half portion",
                calories=340,
                protein_g=21,
                carbs_g=41,
                fat_g=12,
                confidence=0.84,
                source="nutritionix",
            )
        return _estimate(portion_description="1 burrito", confidence=0.84, source="nutritionix")

    monkeypatch.setattr(module.vision_estimator, "describe", fake_describe)
    monkeypatch.setattr(module.branded_food_lookup, "lookup", fake_lookup)


def _post_text(harness, *, client_id, text, local_timestamp=None, local_date=None, local_iso=None):
    data = {"client_id": client_id, "text": text}
    if local_timestamp is not None:
        data["local_timestamp"] = local_timestamp
    if local_date is not None:
        data["local_date"] = local_date
    if local_iso is not None:
        data["local_iso"] = local_iso
    response = harness.client.post(
        "/api/meal-intake",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _post_photo(harness, *, client_id, text="", image_bytes=None):
    response = harness.client.post(
        "/api/meal-intake",
        data={
            "client_id": client_id,
            "text": text,
            "image": (
                io.BytesIO(image_bytes or _jpeg_probe_bytes()),
                "chipotle_burrito.jpg",
                "image/jpeg",
            ),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _accept(harness, *, client_id, estimate, text="", local_timestamp=None, local_date=None, local_iso=None):
    payload = {"estimate": estimate, "text": text}
    if local_timestamp is not None:
        payload["local_timestamp"] = local_timestamp
    if local_date is not None:
        payload["local_date"] = local_date
    if local_iso is not None:
        payload["local_iso"] = local_iso
    response = harness.client.post(f"/api/meal-intake/{client_id}/accept", json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _discard(harness, *, client_id):
    response = harness.client.delete(f"/api/meal-intake/{client_id}?correction_state=pending_review")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _food_logs(harness):
    return harness.module.get_food_logs(1)


def _log_by_client_id(harness, client_id):
    matches = [entry for entry in _food_logs(harness) if entry.get("client_id") == client_id]
    assert len(matches) == 1
    return matches[0]


def _log_by_meal_id(harness, meal_id):
    matches = [entry for entry in _food_logs(harness) if entry.get("meal_id") == meal_id]
    assert len(matches) == 1
    return matches[0]


def _jpeg_probe_bytes():
    return b"\xff\xd8\xff\xe0" + PHOTO_BYTE_PROBE + b"\xff\xd9"


def test_text_only_submit_persists_pending_review_burrito(meal_e2e, monkeypatch):
    _stub_text_parser(
        monkeypatch,
        meal_e2e.module,
        _estimate(source="nutritionix"),
    )

    body = _post_text(
        meal_e2e,
        client_id="fit83-text-auto-1",
        text="chipotle chicken burrito with white rice",
    )

    assert body["status"] == "pending_review"
    assert body["estimate"]["confidence"] >= 0.75
    assert body["estimate"]["source"] in {"nutritionix", "ai_text_estimate"}
    row = _log_by_client_id(meal_e2e, "fit83-text-auto-1")
    assert row["correction_state"] == "pending_review"
    assert row["item_name"] == "Chipotle chicken burrito"


def test_text_only_ambiguous_food_persists_pending_review(meal_e2e, monkeypatch):
    _stub_text_parser(
        monkeypatch,
        meal_e2e.module,
        _estimate(
            item_name="Food",
            portion_description=None,
            meal_type="snack",
            calories=400,
            protein_g=12,
            carbs_g=48,
            fat_g=16,
            sodium_mg=620,
            fiber_g=4,
            confidence=0.42,
            ambiguous=True,
            uncertainty_notes=["Food item and portion are unclear."],
        ),
    )

    body = _post_text(meal_e2e, client_id="fit83-text-pending-1", text="some food")

    assert body["status"] == "pending_review"
    assert body["estimate"]["ambiguous"] is True
    assert "ambiguous_input" in body["policy"]["reasons"]
    row = _log_by_client_id(meal_e2e, "fit83-text-pending-1")
    assert row["correction_state"] == "pending_review"


def test_photo_only_submit_uses_vision_pipeline_and_drops_raw_image(meal_e2e):
    body = _post_photo(meal_e2e, client_id="fit83-photo-auto-1")

    assert body["status"] == "pending_review"
    assert body["estimate"]["from_image"] is True
    assert body["estimate"]["source"] == "vision_claude+nutritionix"
    assert body["estimate"]["item_name"]
    assert body["photo_retention"]["image_received"] is True
    assert body["photo_retention"]["raw_photo_retained"] is False
    row = _log_by_client_id(meal_e2e, "fit83-photo-auto-1")
    assert row["source"] == "vision_claude+nutritionix"
    assert PHOTO_BYTE_PROBE not in meal_e2e.db_path.read_bytes()


def test_photo_with_text_returns_pending_review_and_applies_half_portion_modifier(meal_e2e):
    body = _post_photo(
        meal_e2e,
        client_id="fit83-photo-text-half-1",
        text="half chipotle chicken burrito",
    )

    assert body["status"] == "pending_review"
    assert body["estimate"]["from_image"] is True
    assert body["estimate"]["portion_description"] == "approx half portion"
    assert body["estimate"]["calories"] == 340
    assert body["estimate"]["protein_g"] == 21
    row = _log_by_client_id(meal_e2e, "fit83-photo-text-half-1")
    assert row["context_note"] is None
    assert row["calories"] == 340


def test_pending_review_accept_updates_existing_food_log(meal_e2e, monkeypatch):
    estimate = _estimate(confidence=0.48, ambiguous=True, uncertainty_notes=["Needs review."])
    _stub_text_parser(monkeypatch, meal_e2e.module, estimate)
    pending = _post_text(meal_e2e, client_id="fit83-pending-accept-1", text="some food")

    accepted = meal_e2e.client.post(
        "/api/meal-intake/fit83-pending-accept-1/accept",
        json={"estimate": pending["estimate"], "text": "some food"},
    )

    assert accepted.status_code == 409
    assert accepted.get_json()["save_blocked_item_ids"] == ["item-1"]
    row = _log_by_client_id(meal_e2e, "fit83-pending-accept-1")
    assert row["correction_state"] == "pending_review"
    assert row["item_name"] == "Chipotle chicken burrito"


def test_pending_review_edit_then_accept_persists_corrected_values(meal_e2e, monkeypatch):
    pending_estimate = _estimate(confidence=0.44, ambiguous=True, uncertainty_notes=["Needs review."])
    _stub_text_parser(monkeypatch, meal_e2e.module, pending_estimate)
    pending = _post_text(meal_e2e, client_id="fit83-pending-edit-1", text="some food")
    edited = dict(pending["estimate"])
    edited.update(
        {
            "item_name": "Edited burrito",
            "calories": 610,
            "protein_g": 39,
            "carbs_g": 64,
            "fat_g": 20,
            "confidence": 0.91,
            "ambiguous": False,
            "uncertainty_notes": [],
        }
    )

    _stub_text_parser(monkeypatch, meal_e2e.module, edited)
    refresh = meal_e2e.client.post(
        "/api/meal-intake/fit83-pending-edit-1/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "fit83-edit-1",
            "item_id": "item-1",
            "text": "corrected burrito",
        },
    )
    assert refresh.status_code == 200, refresh.get_data(as_text=True)
    _accept(meal_e2e, client_id="fit83-pending-edit-1", estimate=edited, text="corrected burrito")

    row = _log_by_meal_id(meal_e2e, "fit83-pending-edit-1")
    assert row["correction_state"] == "corrected"
    assert row["item_name"] == "Edited burrito"
    assert row["calories"] == 610
    assert row["protein_g"] == 39
    assert row["context_note"] is None
    assert row["original_estimate"]["item_name"] == "Chipotle chicken burrito"


def test_pending_review_discard_removes_food_log(meal_e2e, monkeypatch):
    _stub_text_parser(
        monkeypatch,
        meal_e2e.module,
        _estimate(confidence=0.4, ambiguous=True, uncertainty_notes=["Needs review."]),
    )
    _post_text(meal_e2e, client_id="fit83-pending-discard-1", text="some food")

    body = _discard(meal_e2e, client_id="fit83-pending-discard-1")

    assert body["removed"] is True
    assert [entry for entry in _food_logs(meal_e2e) if entry.get("client_id") == "fit83-pending-discard-1"] == []


def test_pending_endpoint_hydrates_full_estimate_after_reload(meal_e2e, monkeypatch):
    expected = _estimate(
        item_name="Shared popcorn",
        portion_description="large tub",
        meal_type="snack",
        calories=300,
        protein_g=5,
        carbs_g=36,
        fat_g=18,
        sodium_mg=520,
        fiber_g=6,
        confidence=0.45,
        ambiguous=True,
        uncertainty_notes=["Portion is unclear."],
    )
    _stub_text_parser(monkeypatch, meal_e2e.module, expected)
    _post_text(meal_e2e, client_id="fit83-pending-refresh-1", text="shared popcorn")

    response = meal_e2e.client.get("/api/meal-intake/pending")
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()

    assert body["pending_count"] == 1
    pending = body["pending"][0]
    assert pending["client_id"] == "fit83-pending-refresh-1"
    assert pending["estimate"]["item_name"] == "Shared popcorn"
    assert pending["estimate"]["calories"] == 300
    assert pending["estimate"]["protein_g"] == 5
    assert pending["estimate"]["source"] == "ai_text_estimate"
    assert pending.get("text_hint", "") == "shared popcorn"
    assert pending["policy"]["reasons"]


def test_pending_accept_keeps_submission_day_when_accepted_after_midnight(meal_e2e, monkeypatch):
    pending_estimate = _estimate(confidence=0.6, ambiguous=False)
    _stub_text_parser(monkeypatch, meal_e2e.module, pending_estimate, fallback_used=True)
    pending = _post_text(
        meal_e2e,
        client_id="fit83-midnight-accept-1",
        text="chipotle chicken burrito",
        local_timestamp="2026-05-19T04:55:00.000Z",
        local_date="2026-05-18",
        local_iso="2026-05-18T23:55:00-05:00",
    )

    _accept(
        meal_e2e,
        client_id="fit83-midnight-accept-1",
        estimate=pending["estimate"],
        text="chipotle chicken burrito",
        local_timestamp="2026-05-19T05:05:00.000Z",
        local_date=pending["local_date"],
        local_iso=pending["local_iso"],
    )

    row = _log_by_meal_id(meal_e2e, "fit83-midnight-accept-1")
    assert row["date"] == "2026-05-18"
    assert row["logged_at"] == "2026-05-18T23:55:00"


def test_server_timezone_independence_uses_browser_local_fields(meal_e2e, monkeypatch):
    _stub_text_parser(monkeypatch, meal_e2e.module, _estimate())
    _post_text(
        meal_e2e,
        client_id="fit83-server-tz-1",
        text="chipotle chicken burrito",
        local_timestamp="2026-05-19T03:00:00.000Z",
        local_date="2026-05-18",
        local_iso="2026-05-18T22:00:00-05:00",
    )

    row = _log_by_client_id(meal_e2e, "fit83-server-tz-1")
    assert row["date"] == "2026-05-18"
    assert row["logged_at"] == "2026-05-18T22:00:00"
    assert meal_e2e.module._nutrition_entry_logged_hour(row) == 22


def test_photo_probe_bytes_do_not_leak_to_response_or_database(meal_e2e):
    body = _post_photo(
        meal_e2e,
        client_id="fit83-photo-probe-1",
        text="chipotle chicken burrito",
        image_bytes=_jpeg_probe_bytes(),
    )

    assert PHOTO_BYTE_PROBE.decode("ascii") not in str(body)
    assert b"PHOTO_BYTE_PROBE" not in meal_e2e.db_path.read_bytes()


def test_pending_photo_accept_round_trips_retention_metadata(meal_e2e):
    pending = _post_photo(
        meal_e2e,
        client_id="fit83-photo-retention-1",
        text="shared movie popcorn",
    )

    assert pending["status"] == "pending_review"
    assert pending["estimate"]["from_image"] is True
    assert pending["photo_retention"]["image_received"] is True

    accepted = meal_e2e.client.post(
        "/api/meal-intake/fit83-photo-retention-1/accept",
        json={"estimate": pending["estimate"], "text": "shared movie popcorn"},
    )

    assert accepted.status_code == 409
    assert accepted.get_json()["save_blocked_item_ids"] == ["item-1"]
    row = _log_by_client_id(meal_e2e, "fit83-photo-retention-1")
    assert row["correction_state"] == "pending_review"
    assert row["source"] == "vision_claude+nutritionix"
