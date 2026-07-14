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
import pytest


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


def test_canes_box_combo_aliases_stay_pending_with_ai_only_policy_warning(monkeypatch):
    module = _client(monkeypatch)
    aliases = (
        "Raising Cane's Box Combo",
        "Raising Canes Box Combo",
        "Cane's Box Combo",
        "Canes Box Combo",
    )

    def fake_parser(text, **_kw):
        return {
            "estimate": _accepted_estimate(
                item_name=text,
                calories=840,
                confidence=0.85,
                source="ai_text_estimate",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()

    for index, alias in enumerate(aliases, start=1):
        res = client.post(
            "/api/meal-intake",
            data={"text": alias, "client_id": f"canes-capture-{index}"},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert res.status_code == 200, res.get_data(as_text=True)
        body = res.get_json()
        assert body["status"] == "pending_review"
        assert body["food_log"]["correction_state"] == "pending_review"
        assert body["estimate"]["source"] == "ai_text_estimate"
        assert "branded_combo_ai_only" in body["policy"]["reasons"]
        assert any("AI-only" in note for note in body["estimate"]["uncertainty_notes"])

    rows = data_store.get_food_logs(1)
    assert len(rows) == len(aliases)
    assert all(row["correction_state"] == "pending_review" for row in rows)


def test_canes_box_combo_unchanged_ai_only_accept_is_blocked_without_canonical_row(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Raising Cane's Box Combo",
            calories=840,
            confidence=0.85,
            source="ai_text_estimate",
        ),
    )
    client = module.app.test_client()
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Raising Cane's Box Combo", "client_id": "canes-unchanged"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)

    accept = client.post(
        "/api/meal-intake/canes-unchanged/accept",
        json={"estimate": capture.get_json()["estimate"]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert accept.status_code == 409
    body = accept.get_json()
    assert body["status"] == "blocked"
    assert body["save_blocked_item_ids"] == ["item-1"]
    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["correction_state"] == "pending_review"


def test_canes_box_combo_material_correction_preserves_ai_original(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            confidence=0.85,
            source="ai_text_estimate",
        ),
    )
    client = module.app.test_client()

    correction_capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "canes-material-correction"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert correction_capture.status_code == 200, correction_capture.get_data(as_text=True)
    corrected = dict(correction_capture.get_json()["estimate"])
    corrected["calories"] += 80
    correction_accept = client.post(
        "/api/meal-intake/canes-material-correction/accept",
        json={"estimate": corrected},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert correction_accept.status_code == 200, correction_accept.get_data(as_text=True)
    correction_row = correction_accept.get_json()["food_logs"][0]
    assert correction_row["correction_state"] == "corrected"
    assert correction_row["source"] == "manual_review_estimate"
    assert correction_row["original_estimate"]["source"] == "ai_text_estimate"
    assert correction_row["original_estimate"]["calories"] == 840


def test_canes_box_combo_policy_leaves_ordinary_accept_and_existing_blockers_unchanged(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()

    ordinary = client.post(
        "/api/meal-intake/ordinary-meal/accept",
        json={"estimate": _accepted_estimate(item_name="Chicken bowl")},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert ordinary.status_code == 200, ordinary.get_data(as_text=True)
    assert ordinary.get_json()["food_log"]["correction_state"] == "accepted"
    blocked = module._review_item_from_estimate(
        _accepted_estimate(item_name="Unclear meal", confidence=0.40, ambiguous=True),
        item_id="item-1",
        item_order=1,
    )
    assert module._review_item_is_blocked(blocked) is True


def test_canes_raw_alias_marker_survives_parser_rephrasing_and_blocks_accept(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Raising Cane's 4-Finger Box Combo",
            calories=840,
            confidence=0.85,
            source="ai_text_estimate",
        ),
    )
    client = module.app.test_client()

    capture = client.post(
        "/api/meal-intake",
        data={"text": "Raising Cane's Box Combo", "client_id": "canes-rephrased"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert capture.status_code == 200, capture.get_data(as_text=True)
    body = capture.get_json()
    assert "branded_combo_ai_only" in body["policy"]["reasons"]
    assert body["items"][0]["branded_combo_ai_only"] is True
    accept = client.post(
        "/api/meal-intake/canes-rephrased/accept",
        json={"estimate": body["estimate"]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert accept.status_code == 409
    assert accept.get_json()["save_blocked_item_ids"] == ["item-1"]


def test_canes_accept_requires_material_nutrition_correction(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            confidence=0.85,
            source="ai_text_estimate",
        ),
    )
    client = module.app.test_client()

    for suffix, change in (
        ("meal-type", {"meal_type": "dinner"}),
        ("name", {"item_name": "Canes combo"}),
        ("portion", {"portion_description": "with extra sauce"}),
        ("tiny-calorie", {"calories": 841}),
    ):
        client_id = f"canes-cosmetic-{suffix}"
        capture = client.post(
            "/api/meal-intake",
            data={"text": "Canes Box Combo", "client_id": client_id},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        estimate = dict(capture.get_json()["estimate"])
        estimate.update(change)
        accept = client.post(
            f"/api/meal-intake/{client_id}/accept",
            json={"estimate": estimate},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert accept.status_code == 409, accept.get_data(as_text=True)

    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "canes-material-threshold"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    material = dict(capture.get_json()["estimate"])
    material["calories"] += 50
    accept = client.post(
        "/api/meal-intake/canes-material-threshold/accept",
        json={"estimate": material},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert accept.status_code == 200, accept.get_data(as_text=True)
    assert accept.get_json()["food_logs"][0]["correction_state"] == "corrected"


def test_canes_omitted_optional_nutrition_cannot_unlock_legacy_accept(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            sodium_mg=700,
            fiber_g=6,
            confidence=0.85,
            source="ai_text_estimate",
        ),
    )
    client = module.app.test_client()

    client_id = "canes-omit-legacy"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    submitted = dict(capture.get_json()["estimate"])
    submitted.pop("sodium_mg")
    submitted.pop("fiber_g")
    submitted["ambiguous"] = False
    submitted["uncertainty_notes"] = []
    accept = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": submitted},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert accept.status_code == 409, accept.get_data(as_text=True)

    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["correction_state"] == "pending_review"


def test_canes_omitted_optional_nutrition_cannot_unlock_explicit_items_accept(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            sodium_mg=700,
            fiber_g=6,
            confidence=0.85,
            source="ai_text_estimate",
        ),
    )
    client = module.app.test_client()
    client_id = "canes-omit-items"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    submitted = dict(capture.get_json()["estimate"])
    submitted.pop("sodium_mg")
    submitted.pop("fiber_g")
    submitted["ambiguous"] = False
    submitted["uncertainty_notes"] = []
    accept = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={
            "meal_id": client_id,
            "items": [{"item_id": "item-1", "state": "included", "estimate": submitted}],
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert accept.status_code == 409, accept.get_data(as_text=True)

    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["correction_state"] == "pending_review"


def test_canes_explicit_material_sodium_fiber_and_calorie_corrections_succeed(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            sodium_mg=700,
            fiber_g=6,
            confidence=0.85,
            source="ai_text_estimate",
        ),
    )
    client = module.app.test_client()

    for suffix, field, value in (
        ("sodium", "sodium_mg", 0),
        ("fiber", "fiber_g", 0),
        ("calories", "calories", 890),
    ):
        client_id = f"canes-explicit-{suffix}"
        capture = client.post(
            "/api/meal-intake",
            data={"text": "Canes Box Combo", "client_id": client_id},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert capture.status_code == 200, capture.get_data(as_text=True)
        submitted = dict(capture.get_json()["estimate"])
        submitted[field] = value
        accept = client.post(
            f"/api/meal-intake/{client_id}/accept",
            json={"estimate": submitted},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert accept.status_code == 200, accept.get_data(as_text=True)
        assert accept.get_json()["food_logs"][0]["correction_state"] == "corrected"


def test_canes_natural_box_combo_phrasings_stay_pending_and_block_unchanged_accept(monkeypatch):
    module = _client(monkeypatch)
    phrases = (
        "Raising Cane's 4-Finger Box Combo",
        "Cane's four finger box combo",
        "Box Combo from Cane's",
    )

    def fake_parser(text, **_kw):
        return {
            "estimate": _accepted_estimate(
                item_name=text,
                calories=840,
                confidence=0.85,
                source="ai_text_estimate",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()

    for index, phrase in enumerate(phrases, start=1):
        client_id = f"canes-natural-{index}"
        capture = client.post(
            "/api/meal-intake",
            data={"text": phrase, "client_id": client_id},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert capture.status_code == 200, capture.get_data(as_text=True)
        body = capture.get_json()
        assert body["items"][0]["branded_combo_ai_only"] is True
        assert "branded_combo_ai_only" in body["policy"]["reasons"]
        accept = client.post(
            f"/api/meal-intake/{client_id}/accept",
            json={"estimate": body["estimate"]},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert accept.status_code == 409

    rows = data_store.get_food_logs(1)
    assert len(rows) == len(phrases)
    assert all(row["correction_state"] == "pending_review" for row in rows)


def test_canes_accept_rejects_forged_source_and_accepts_server_selected_candidate(monkeypatch):
    module = _client(monkeypatch)
    source_backed_candidate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        confidence=0.90,
        source="nutritionix",
        external_food_id="server-canes-box",
        verified_source_url="https://example.test/server-canes-box",
    )
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            confidence=0.85,
            source="ai_text_estimate",
            candidates=[{"candidate_id": "verified-canes", "estimate": source_backed_candidate}],
        ),
    )
    client = module.app.test_client()

    forged_capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "canes-forged-source"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    forged = dict(forged_capture.get_json()["estimate"])
    forged.update({
        "source": "nutritionix",
        "underlying_source": "usda_fdc",
        "external_food_id": "forged",
        "verified_source_url": "https://example.test/forged",
        "ambiguous": False,
        "uncertainty_notes": [],
    })
    forged_accept = client.post(
        "/api/meal-intake/canes-forged-source/accept",
        json={
            "meal_id": "canes-forged-source",
            "items": [{"item_id": "item-1", "state": "included", "estimate": forged}],
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert forged_accept.status_code == 409

    candidate_capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "canes-server-candidate"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert candidate_capture.status_code == 200, candidate_capture.get_data(as_text=True)
    assert candidate_capture.get_json()["items"][0]["candidates"][0]["source_backed"] is True
    selected = client.post(
        "/api/meal-intake/canes-server-candidate/refresh",
        json={"kind": "choose_candidate", "request_id": "canes-verified", "item_id": "item-1", "candidate_id": "verified-canes"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert selected.status_code == 200, selected.get_data(as_text=True)
    assert "branded_combo_ai_only" not in selected.get_json()["policy"]["reasons"]
    selected_estimate = dict(selected.get_json()["items"][0]["estimate"])
    selected_estimate["meal_type"] = "dinner"
    accept = client.post(
        "/api/meal-intake/canes-server-candidate/accept",
        json={
            "meal_id": "canes-server-candidate",
            "items": [{"item_id": "item-1", "state": "included", "estimate": selected_estimate}],
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert accept.status_code == 200, accept.get_data(as_text=True)
    assert accept.get_json()["food_logs"][0]["source"] == "nutritionix"
    assert accept.get_json()["food_logs"][0]["meal_type"] == "dinner"


def test_canes_refresh_preserves_or_derives_marker_before_accept(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        if text == "ordinary lunch":
            return {"estimate": _accepted_estimate(item_name="Chicken bowl", source="ai_text_estimate"), "fallback_used": False}
        return {
            "estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=840,
                confidence=0.85,
                source="ai_text_estimate",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()

    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "canes-edit-marker"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    edited = client.post(
        "/api/meal-intake/canes-edit-marker/refresh",
        json={"kind": "edit_portion", "request_id": "canes-edit", "item_id": "item-1", "text": "Canes Box Combo"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    assert edited.status_code == 200, edited.get_data(as_text=True)
    assert edited.get_json()["items"][0]["branded_combo_ai_only"] is True
    assert "branded_combo_ai_only" in edited.get_json()["policy"]["reasons"]
    blocked = client.post(
        "/api/meal-intake/canes-edit-marker/accept",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert blocked.status_code == 409

    added_capture = client.post(
        "/api/meal-intake",
        data={"text": "ordinary lunch", "client_id": "canes-add-marker"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    added = client.post(
        "/api/meal-intake/canes-add-marker/refresh",
        json={"kind": "add_item", "request_id": "canes-add", "text": "Canes Box Combo"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert added_capture.status_code == 200, added_capture.get_data(as_text=True)
    assert added.status_code == 200, added.get_data(as_text=True)
    assert added.get_json()["items"][-1]["branded_combo_ai_only"] is True
    assert "branded_combo_ai_only" in added.get_json()["policy"]["reasons"]


def test_canes_followup_replacement_and_route_body_mismatch_cannot_bypass_marker(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        if text == "followup answer":
            return {
                "estimate": _accepted_estimate(
                    item_name="Canes Box Combo",
                    calories=840,
                    confidence=0.85,
                    source="ai_text_estimate",
                ),
                "fallback_used": False,
            }
        return {
            "estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=840,
                confidence=0.40,
                ambiguous=True,
                source="ai_text_estimate",
                clarification_question="Which combo and portions were included?",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "canes-followup-marker"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    followup = client.post(
        "/api/meal-intake/canes-followup-marker/refresh",
        json={"kind": "followup_answer", "request_id": "canes-followup", "answer": "followup answer"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    assert followup.status_code == 200, followup.get_data(as_text=True)
    assert followup.get_json()["items"][0]["branded_combo_ai_only"] is True
    assert client.post(
        "/api/meal-intake/canes-followup-marker/accept",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    ).status_code == 409

    mismatch = client.post(
        "/api/meal-intake/canes-followup-marker/accept",
        json={
            "meal_id": "different-meal",
            "items": [{"item_id": "item-1", "state": "included", "estimate": followup.get_json()["estimate"]}],
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert mismatch.status_code == 400


def test_canes_material_refresh_clears_warning_but_unresolved_refresh_keeps_it(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        calories = 890 if text == "material correction" else 840
        return {
            "estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=calories,
                confidence=0.85,
                source="ai_text_estimate",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    for client_id, edit_text, expected_warning in (
        ("canes-unresolved-warning", "Canes Box Combo", True),
        ("canes-material-warning", "material correction", False),
    ):
        client.post(
            "/api/meal-intake",
            data={"text": "Canes Box Combo", "client_id": client_id},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        refreshed = client.post(
            f"/api/meal-intake/{client_id}/refresh",
            json={"kind": "edit_portion", "request_id": f"{client_id}-edit", "item_id": "item-1", "text": edit_text},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert refreshed.status_code == 200, refreshed.get_data(as_text=True)
        assert ("branded_combo_ai_only" in refreshed.get_json()["policy"]["reasons"]) is expected_warning


def test_direct_accept_rejects_unsnapshotted_ai_canes_combo(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()

    accept = client.post(
        "/api/meal-intake/canes-direct-accept/accept",
        json={
            "estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=840,
                confidence=0.85,
                source="ai_text_estimate",
            )
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert accept.status_code == 409
    assert data_store.get_food_logs(1) == []


def test_direct_accept_rejects_unsnapshotted_canes_text_with_innocuous_estimate(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()

    accept = client.post(
        "/api/meal-intake/canes-direct-text/accept",
        json={
            "text": "Canes Box Combo",
            "estimate": _accepted_estimate(
                item_name="Chicken bowl",
                calories=840,
                confidence=0.85,
                source="ai_text_estimate",
            ),
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert accept.status_code == 409
    assert data_store.get_food_logs(1) == []
    assert data_store.get_meal_acceptance_event(1, "canes-direct-text") is None


def test_direct_multi_accept_rejects_unsnapshotted_canes_item_text_atomically(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()

    accept = client.post(
        "/api/meal-intake/canes-direct-multi-text/accept",
        json={
            "meal_id": "canes-direct-multi-text",
            "items": [
                {
                    "item_id": "safe",
                    "state": "included",
                    "text": "Garden salad",
                    "estimate": _accepted_estimate(item_name="Garden salad", source="manual_review_estimate"),
                },
                {
                    "item_id": "canes",
                    "state": "included",
                    "text": "Canes Box Combo",
                    "estimate": _accepted_estimate(
                        item_name="Chicken bowl",
                        calories=840,
                        confidence=0.85,
                        source="ai_text_estimate",
                    ),
                },
            ],
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert accept.status_code == 409
    assert accept.get_json()["save_blocked_item_ids"] == ["canes"]
    assert data_store.get_food_logs(1) == []
    assert data_store.get_meal_acceptance_event(1, "canes-direct-multi-text") is None


def test_direct_accept_allows_unrelated_single_and_multi_meals(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()

    single = client.post(
        "/api/meal-intake/ordinary-direct-single/accept",
        json={
            "text": "Chicken bowl",
            "estimate": _accepted_estimate(item_name="Chicken bowl", source="manual_review_estimate"),
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    multi = client.post(
        "/api/meal-intake/ordinary-direct-multi/accept",
        json={
            "meal_id": "ordinary-direct-multi",
            "items": [
                {
                    "item_id": "salad",
                    "state": "included",
                    "text": "Garden salad",
                    "estimate": _accepted_estimate(item_name="Garden salad", source="manual_review_estimate"),
                },
                {
                    "item_id": "soup",
                    "state": "included",
                    "text": "Tomato soup",
                    "estimate": _accepted_estimate(item_name="Tomato soup", source="manual_review_estimate"),
                },
            ],
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert single.status_code == 200, single.get_data(as_text=True)
    assert multi.status_code == 200, multi.get_data(as_text=True)
    assert len(data_store.get_food_logs(1)) == 3


def test_direct_accept_rejects_forged_source_canes_combo_in_single_and_multi_shapes(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    forged = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        confidence=0.85,
        source="nutritionix",
        underlying_source="usda_fdc",
        external_food_id="forged-canes",
        verified_source_url="https://example.test/forged-canes",
    )

    single = client.post(
        "/api/meal-intake/canes-direct-forged-single/accept",
        json={"estimate": forged},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    multi = client.post(
        "/api/meal-intake/canes-direct-forged-multi/accept",
        json={"items": [{"item_id": "item-1", "state": "included", "estimate": forged}]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert single.status_code == 409
    assert multi.status_code == 409
    assert data_store.get_food_logs(1) == []


def test_canes_server_generated_source_backed_edit_and_followup_resolve_policy(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        if text == "source-backed refresh":
            return {
                "estimate": _accepted_estimate(
                    item_name="Canes Box Combo",
                    calories=920,
                    confidence=0.90,
                    source="nutritionix",
                    external_food_id="server-canes-refresh",
                    verified_source_url="https://example.test/server-canes-refresh",
                ),
                "fallback_used": False,
            }
        return {
            "estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=840,
                confidence=0.40 if text == "followup initial" else 0.85,
                ambiguous=text == "followup initial",
                source="ai_text_estimate",
                clarification_question="Which combo and portions were included?" if text == "followup initial" else None,
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()

    edit_capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "canes-source-edit"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    edit = client.post(
        "/api/meal-intake/canes-source-edit/refresh",
        json={"kind": "edit_portion", "request_id": "source-edit", "item_id": "item-1", "text": "source-backed refresh"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert edit_capture.status_code == 200, edit_capture.get_data(as_text=True)
    assert edit.status_code == 200, edit.get_data(as_text=True)
    assert edit.get_json()["items"][0]["server_source_backed_candidate"] is True
    assert "branded_combo_ai_only" not in edit.get_json()["policy"]["reasons"]

    followup_capture = client.post(
        "/api/meal-intake",
        data={"text": "followup initial", "client_id": "canes-source-followup"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    followup = client.post(
        "/api/meal-intake/canes-source-followup/refresh",
        json={"kind": "followup_answer", "request_id": "source-followup", "answer": "source-backed refresh"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert followup_capture.status_code == 200, followup_capture.get_data(as_text=True)
    assert followup.status_code == 200, followup.get_data(as_text=True)
    assert followup.get_json()["items"][0]["server_source_backed_candidate"] is True
    assert "branded_combo_ai_only" not in followup.get_json()["policy"]["reasons"]


def test_mixed_source_refresh_accept_exposes_current_provenance_at_public_boundaries(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        if text == "mixed refresh":
            return {
                "estimate": _accepted_estimate(
                    item_name="Canes Box Combo",
                    calories=920,
                    source="mixed_lookup",
                    underlying_source="mixed_lookup",
                    underlying_sources=["nutritionix", "usda_fdc"],
                ),
                "fallback_used": False,
            }
        return {
            "estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=840,
                source="ai_text_estimate",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "mixed-public"},
        content_type="multipart/form-data",
        headers=headers,
    )
    refresh = client.post(
        "/api/meal-intake/mixed-public/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "mixed-public-refresh",
            "item_id": "item-1",
            "text": "mixed refresh",
        },
        headers=headers,
    )
    assert capture.status_code == 200
    assert refresh.status_code == 200
    accepted = client.post(
        "/api/meal-intake/mixed-public/accept",
        json={},
        headers=headers,
    )
    assert accepted.status_code == 200
    row = accepted.get_json()["food_logs"][0]
    assert row["accepted_estimate"]["underlying_sources"] == ["nutritionix", "usda_fdc"]
    assert row["original_estimate"]["source"] == "ai_text_estimate"
    assert "underlying_sources" not in row["original_estimate"]
    assert data_store.get_food_logs(1)[0]["accepted_estimate"] == row["accepted_estimate"]
    by_date = client.get(f"/api/food-logs/by-date/{row['date']}")
    assert by_date.status_code == 200
    history_row = next(
        entry
        for entry in by_date.get_json()["entries"]
        if entry["client_id"] == row["client_id"]
    )
    assert history_row["accepted_estimate"] == row["accepted_estimate"]
    export = client.get("/api/export-backup")
    assert export.status_code == 200
    export_row = next(
        entry
        for entry in export.get_json()["data"]["food_logs"]
        if entry["client_id"] == row["client_id"]
    )
    assert export_row["accepted_estimate"] == row["accepted_estimate"]


def test_imported_forged_canes_snapshot_cannot_grant_source_backed_accept(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "forged-imported-canes"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200
    exported = client.get("/api/export-backup")
    snapshot = exported.get_json()["data"]["meal_review_snapshots"][0]
    item = snapshot["payload"]["items"][0]
    item["branded_combo_ai_only"] = True
    item["server_source_backed_candidate"] = True
    item["estimate"]["source"] = "ai_text_estimate"
    item["original_estimate"]["source"] = "ai_text_estimate"

    data_store.delete_user_data(1)
    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    accepted = client.post(
        "/api/meal-intake/forged-imported-canes/accept",
        json={},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert not any(
        row["correction_state"] in {"accepted", "corrected"}
        for row in data_store.get_food_logs(1)
    )
    assert data_store.get_meal_acceptance_event(1, "forged-imported-canes") is None


def test_imported_ordinary_snapshot_cannot_erase_pending_canes_parent(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="ai_text_estimate",
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "imported-ordinary-overwrite-canes"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]
    ordinary = _accepted_estimate(
        item_name="Chicken bowl",
        calories=500,
        confidence=0.95,
        source="manual_review_estimate",
    )
    snapshot["payload"] = {
        "status": "pending_review",
        "text": "Chicken bowl",
        "estimate": ordinary,
        "original_estimate": ordinary,
        "items": [
            {
                "item_id": "item-1",
                "item_order": 1,
                "status": "included",
                "text": "Chicken bowl",
                "estimate": ordinary,
                "original_estimate": ordinary,
                "branded_combo_ai_only": False,
                "candidates": [],
            }
        ],
    }
    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, client_id) is None


def test_route_body_meal_id_cannot_bypass_imported_pending_canes_parent(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="ai_text_estimate",
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    meal_id = "body-pending-canes"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": meal_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]
    ordinary = _accepted_estimate(
        item_name="Chicken bowl",
        calories=500,
        confidence=0.95,
        source="manual_review_estimate",
    )
    snapshot["payload"] = {
        "status": "pending_review",
        "text": "Chicken bowl",
        "estimate": ordinary,
        "original_estimate": ordinary,
        "items": [
            {
                "item_id": "item-1",
                "item_order": 1,
                "status": "included",
                "text": "Chicken bowl",
                "estimate": ordinary,
                "original_estimate": ordinary,
                "branded_combo_ai_only": False,
                "candidates": [],
            }
        ],
    }
    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    accepted = client.post(
        "/api/meal-intake/route-without-pending/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"item_id": "item-1", "state": "included", "estimate": ordinary}
            ],
        },
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert not any(
        row["correction_state"] in {"accepted", "corrected"}
        for row in data_store.get_food_logs(1)
    )
    assert data_store.get_meal_acceptance_event(1, meal_id) is None


def test_imported_canes_snapshot_without_candidates_cannot_claim_source_backed(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    forged_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="nutritionix",
        external_food_id="imported-forged-canes",
        verified_source_url="https://example.test/imported-forged-canes",
    )
    snapshot = {
        "meal_id": "imported-canes-without-candidates",
        "payload": {
            "status": "pending_review",
            "text": "Canes Box Combo",
            "estimate": forged_estimate,
            "original_estimate": forged_estimate,
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "text": "Canes Box Combo",
                    "estimate": forged_estimate,
                    "original_estimate": forged_estimate,
                    "branded_combo_ai_only": False,
                    "candidates": [],
                }
            ],
        },
        "next_item_seq": 2,
    }

    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)
    accepted = client.post(
        "/api/meal-intake/imported-canes-without-candidates/accept",
        json={},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert not any(
        row["correction_state"] in {"accepted", "corrected"}
        for row in data_store.get_food_logs(1)
    )
    assert data_store.get_meal_acceptance_event(1, "imported-canes-without-candidates") is None


def test_imported_candidate_cannot_retain_client_source_backed_flag(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    candidate_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
    )
    snapshot = {
        "meal_id": "imported-candidate-source-backed",
        "payload": {
            "status": "pending_review",
            "text": "Canes Box Combo",
            "estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=840,
                source="ai_text_estimate",
            ),
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "text": "Canes Box Combo",
                    "estimate": _accepted_estimate(
                        item_name="Canes Box Combo",
                        calories=840,
                        source="ai_text_estimate",
                    ),
                    "original_estimate": _accepted_estimate(
                        item_name="Canes Box Combo",
                        calories=840,
                        source="ai_text_estimate",
                    ),
                    "candidates": [
                        {
                            "candidate_id": "forged-source-backed",
                            "source_backed": True,
                            "estimate": candidate_estimate,
                        }
                    ],
                }
            ],
        },
        "next_item_seq": 2,
    }

    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )

    assert restored.status_code == 200, restored.get_data(as_text=True)
    stored = data_store.get_meal_review_snapshot(1, snapshot["meal_id"])
    candidate = stored["payload"]["items"][0]["candidates"][0]
    assert not candidate.get("source_backed")


def test_material_canes_correction_drops_forged_current_provenance(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="ai_text_estimate",
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "material-forged-provenance"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    item = capture.get_json()["items"][0]
    material = dict(item["estimate"])
    material.update(
        calories=material["calories"] + 50,
        source="nutritionix",
        underlying_source="nutritionix",
        external_food_id="forged-material-correction",
        verified_source_url="https://example.test/forged-material-correction",
    )

    accepted = client.post(
        "/api/meal-intake/material-forged-provenance/accept",
        json={
            "meal_id": "material-forged-provenance",
            "items": [
                {
                    "item_id": item["item_id"],
                    "state": "included",
                    "estimate": material,
                }
            ],
        },
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_logs"][0]
    assert row["correction_state"] == "corrected"
    assert row["source"] == "manual_review_estimate"
    assert row["original_estimate"]["source"] == "ai_text_estimate"
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"
    assert "underlying_source" not in row["accepted_estimate"]
    assert "external_food_id" not in row["accepted_estimate"]
    assert "verified_source_url" not in row["accepted_estimate"]


def test_snapshotless_canes_pending_row_uses_stored_ai_provenance(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="ai_text_estimate",
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "snapshotless-canes-pending"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    data_store.delete_meal_review_snapshot(1, client_id)
    pending = data_store.get_food_logs(1)[0]
    assert pending["correction_state"] == "pending_review"

    innocuous = dict(capture.get_json()["items"][0]["estimate"])
    innocuous.update(item_name="Chicken bowl", source="manual")
    blocked = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": innocuous},
        headers=headers,
    )

    assert blocked.status_code == 409, blocked.get_data(as_text=True)
    assert data_store.get_food_logs(1)[0]["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, client_id) is None

    material = dict(innocuous)
    material.update(
        item_name="Canes Box Combo",
        calories=material["calories"] + 50,
        source="nutritionix",
        underlying_source="nutritionix",
        external_food_id="forged-snapshotless-canes",
    )
    corrected = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={
            "estimate": material,
            "original_estimate": _accepted_estimate(
                item_name="Chicken bowl",
                calories=900,
                source="nutritionix",
            ),
        },
        headers=headers,
    )
    assert corrected.status_code == 200, corrected.get_data(as_text=True)
    row = corrected.get_json()["food_log"]
    assert row["correction_state"] == "corrected"
    assert row["source"] == "manual_review_estimate"
    assert row["original_estimate"]["source"] == "ai_text_estimate"
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"


def test_canes_personal_vocab_underlying_source_stays_untrusted(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="personal_vocab",
            underlying_source="nutritionix",
        ),
        source="personal_vocab",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "canes-personal-vocab-underlying-source"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )

    assert capture.status_code == 200, capture.get_data(as_text=True)
    item = capture.get_json()["items"][0]
    assert item["branded_combo_ai_only"] is True

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert data_store.get_food_logs(1)[0]["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, client_id) is None


def test_snapshotless_canes_omitted_optional_nutrition_stays_blocked(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="ai_text_estimate",
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "snapshotless-canes-omitted-optional-nutrition"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    data_store.delete_meal_review_snapshot(1, client_id)

    unchanged = dict(capture.get_json()["items"][0]["estimate"])
    unchanged.pop("sodium_mg")
    unchanged.pop("fiber_g")
    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": unchanged},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert data_store.get_food_logs(1)[0]["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, client_id) is None


def test_snapshotless_canes_context_note_preserves_raw_alias_policy(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "snapshotless-canes-context-note"
    original = _accepted_estimate(
        item_name="Four chicken fingers, fries, toast, and drink",
        calories=840,
        source="ai_text_estimate",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": original["item_name"],
            "context_note": "Canes Box Combo",
            "calories": original["calories"],
            "protein_g": original["protein_g"],
            "carbs_g": original["carbs_g"],
            "fat_g": original["fat_g"],
            "sodium_mg": original["sodium_mg"],
            "fiber_g": original["fiber_g"],
            "source": "ai_text_estimate",
            "correction_state": "pending_review",
            "original_estimate": original,
        },
    )

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": dict(original)},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert data_store.get_food_logs(1)[0]["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, client_id) is None


def test_snapshotless_pending_canes_parent_blocks_renamed_multi_accept(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="ai_text_estimate",
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "snapshotless-canes-renamed-multi"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    data_store.delete_meal_review_snapshot(1, client_id)

    unchanged = capture.get_json()["estimate"]
    renamed_items = []
    for item_id, item_name, item_client_id in (
        ("chicken", "Chicken fingers", client_id),
        ("fries", "Fries", None),
    ):
        estimate = dict(unchanged)
        estimate["item_name"] = item_name
        estimate.update(
            source="manual_review_estimate",
            ambiguous=False,
            uncertainty_notes=[],
        )
        for field in ("branded_combo_ai_only", "underlying_source", "underlying_sources"):
            estimate.pop(field, None)
        item = {
            "item_id": item_id,
            "state": "included",
            "text": item_name,
            "estimate": estimate,
        }
        if item_client_id:
            item["client_id"] = item_client_id
        renamed_items.append(item)
    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"meal_id": client_id, "items": renamed_items},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, client_id) is None


def test_snapshotless_photo_assisted_canes_keeps_raw_alias_policy(monkeypatch):
    module = _client(monkeypatch)
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "claude",
            "item_description": "Four chicken fingers, fries, toast, and drink",
            "portion_hint": "one meal",
            "confidence": 0.90,
            "ambiguous": False,
            "uncertainty_notes": [],
            "macro_estimate": {
                "meal_type": "lunch",
                "calories": 840,
                "protein_g": 35,
                "carbs_g": 45,
                "fat_g": 18,
                "sodium_mg": 700,
                "fiber_g": 6,
            },
        },
        lookup=None,
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "snapshotless-photo-assisted-canes"
    capture = client.post(
        "/api/meal-intake",
        data={
            "text": "Canes Box Combo",
            "client_id": client_id,
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    assert capture.get_json()["items"][0]["branded_combo_ai_only"] is True
    data_store.delete_meal_review_snapshot(1, client_id)

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": capture.get_json()["estimate"]},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert data_store.get_food_logs(1)[0]["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, client_id) is None


def test_snapshotless_source_backed_pending_canes_accepts_saved_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "snapshotless-source-backed-canes"
    original = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        external_food_id="saved-nutritionix-canes",
        verified_source_url="https://example.test/saved-nutritionix-canes",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": original["item_name"],
            "context_note": "Canes Box Combo",
            "calories": original["calories"],
            "protein_g": original["protein_g"],
            "carbs_g": original["carbs_g"],
            "fat_g": original["fat_g"],
            "sodium_mg": original["sodium_mg"],
            "fiber_g": original["fiber_g"],
            "source": "nutritionix",
            "correction_state": "pending_review",
            "original_estimate": original,
        },
    )

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": dict(original)},
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_log"]
    assert row["source"] == "nutritionix"
    assert row["original_estimate"]["source"] == "nutritionix"
    assert row["accepted_estimate"]["source"] == "nutritionix"


def test_snapshotless_source_backed_canes_material_correction_preserves_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "snapshotless-source-backed-canes-material"
    original = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        external_food_id="saved-nutritionix-canes-material",
        verified_source_url="https://example.test/saved-nutritionix-canes-material",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": original["item_name"],
            "context_note": "Canes Box Combo",
            "calories": original["calories"],
            "protein_g": original["protein_g"],
            "carbs_g": original["carbs_g"],
            "fat_g": original["fat_g"],
            "sodium_mg": original["sodium_mg"],
            "fiber_g": original["fiber_g"],
            "source": "nutritionix",
            "correction_state": "pending_review",
            "original_estimate": original,
        },
    )
    material = dict(original)
    material.update(calories=material["calories"] + 50, source="manual_review_estimate")

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": material},
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_log"]
    assert row["correction_state"] == "corrected"
    assert row["calories"] == 970
    assert row["source"] == "manual_review_estimate"
    assert row["original_estimate"]["source"] == "nutritionix"
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"


def test_wrapped_mixed_accepted_estimate_round_trips_public_surfaces(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    wrapped = _accepted_estimate(
        item_name="Vision Canes Box Combo",
        calories=920,
        source="vision_claude+mixed_lookup",
        underlying_source="mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )
    row = data_store.add_food_log(
        1,
        {
            "client_id": "wrapped-mixed-public",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": wrapped["item_name"],
            "calories": wrapped["calories"],
            "protein_g": wrapped["protein_g"],
            "carbs_g": wrapped["carbs_g"],
            "fat_g": wrapped["fat_g"],
            "sodium_mg": wrapped["sodium_mg"],
            "fiber_g": wrapped["fiber_g"],
            "source": wrapped["source"],
            "correction_state": "accepted",
            "original_estimate": _accepted_estimate(
                item_name=wrapped["item_name"],
                calories=840,
                source="ai_text_estimate",
            ),
            "accepted_estimate": wrapped,
        },
    )

    assert row["accepted_estimate"]["underlying_sources"] == ["nutritionix", "usda_fdc"]
    reloaded = data_store.get_food_logs(1)[0]
    assert reloaded["accepted_estimate"] == row["accepted_estimate"]
    by_date = client.get("/api/food-logs/by-date/2026-05-18")
    assert by_date.status_code == 200
    history_row = by_date.get_json()["entries"][0]
    assert history_row["accepted_estimate"] == row["accepted_estimate"]
    export = client.get("/api/export-backup")
    assert export.status_code == 200
    assert export.get_json()["data"]["food_logs"][0]["accepted_estimate"] == row["accepted_estimate"]


def test_personal_vocab_wrapped_mixed_provenance_is_untrusted_in_both_predicates(monkeypatch):
    module = _client(monkeypatch)
    personal_wrapped = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="personal_vocab+mixed_lookup",
        underlying_source="mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )

    assert module._is_source_backed_nutrition(personal_wrapped) is False
    assert data_store._is_authorized_accepted_estimate_replacement(personal_wrapped) is False


def test_direct_provider_with_personal_vocab_underlying_component_is_untrusted(monkeypatch):
    module = _client(monkeypatch)
    tainted = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        underlying_source="personal_vocab+mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )

    assert module._is_source_backed_nutrition(tainted) is False
    assert data_store._is_authorized_accepted_estimate_replacement(tainted) is False


def test_direct_provider_with_invalid_mixed_marker_cannot_replace_current_provenance(monkeypatch):
    module = _client(monkeypatch)
    invalid_mixed = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        underlying_source="mixed_lookup",
        underlying_sources=["ai_text_estimate"],
    )

    assert module._is_source_backed_nutrition(invalid_mixed) is False
    assert data_store._is_authorized_accepted_estimate_replacement(invalid_mixed) is False


def test_honest_material_correction_restores_trusted_image_boolean(monkeypatch):
    module = _client(monkeypatch)
    original = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
        from_image=True,
    )
    corrected = module._honest_material_correction_estimate(
        _accepted_estimate(
            item_name="Canes Box Combo",
            calories=890,
            source="manual_review_estimate",
            from_image=False,
        ),
        original,
    )

    assert corrected["from_image"] is True


def test_material_canes_correction_strips_client_provenance_metadata(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="ai_text_estimate",
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "material-canes-client-provenance"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    item = capture.get_json()["items"][0]
    material = dict(item["estimate"])
    material.update(
        calories=material["calories"] + 50,
        personal_vocab_phrase="forged personal phrase",
        vision_description="forged visual evidence",
        vision_provider="forged-provider",
        vision_confidence=0.99,
    )

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={
            "meal_id": client_id,
            "items": [
                {"item_id": item["item_id"], "state": "included", "estimate": material}
            ],
        },
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    persisted = accepted.get_json()["food_logs"][0]["accepted_estimate"]
    for field in (
        "personal_vocab_phrase",
        "vision_description",
        "vision_provider",
        "vision_confidence",
    ):
        assert field not in persisted


def test_ambiguous_direct_provider_cannot_accept_or_replace_terminal_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    original = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        external_food_id="ambiguous-pending-canes",
        verified_source_url="https://example.test/ambiguous-pending-canes",
        ambiguous=True,
    )
    pending_id = "ambiguous-direct-pending-canes"
    data_store.add_food_log(
        1,
        {
            "client_id": pending_id,
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": original["item_name"],
            "context_note": "Canes Box Combo",
            "calories": original["calories"],
            "protein_g": original["protein_g"],
            "carbs_g": original["carbs_g"],
            "fat_g": original["fat_g"],
            "sodium_mg": original["sodium_mg"],
            "fiber_g": original["fiber_g"],
            "source": "nutritionix",
            "correction_state": "pending_review",
            "original_estimate": original,
        },
    )
    accepted = client.post(
        f"/api/meal-intake/{pending_id}/accept",
        json={"estimate": dict(original)},
        headers=headers,
    )
    assert module._is_source_backed_nutrition(original) is False
    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert data_store.get_food_logs(1)[0]["correction_state"] == "pending_review"

    prior_current = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="mixed_lookup",
        underlying_source="mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )
    terminal_id = "ambiguous-direct-terminal"
    data_store.add_food_log(
        1,
        {
            "client_id": terminal_id,
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": prior_current["item_name"],
            "calories": prior_current["calories"],
            "protein_g": prior_current["protein_g"],
            "carbs_g": prior_current["carbs_g"],
            "fat_g": prior_current["fat_g"],
            "sodium_mg": prior_current["sodium_mg"],
            "fiber_g": prior_current["fiber_g"],
            "source": "mixed_lookup",
            "correction_state": "corrected",
            "original_estimate": _accepted_estimate(
                item_name=prior_current["item_name"],
                calories=840,
                source="ai_text_estimate",
            ),
            "accepted_estimate": prior_current,
        },
    )
    data_store.add_food_log(
        1,
        {
            "client_id": terminal_id,
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": original["item_name"],
            "calories": 825,
            "protein_g": original["protein_g"],
            "carbs_g": original["carbs_g"],
            "fat_g": original["fat_g"],
            "sodium_mg": original["sodium_mg"],
            "fiber_g": original["fiber_g"],
            "source": "nutritionix",
            "correction_state": "corrected",
            "accepted_estimate": {**original, "calories": 825},
        },
    )
    terminal = next(
        row for row in data_store.get_food_logs(1) if row["client_id"] == terminal_id
    )
    assert terminal["accepted_estimate"]["source"] == "manual_review_estimate"
    assert "underlying_sources" not in terminal["accepted_estimate"]


def test_snapshotless_legacy_source_backed_canes_uses_food_log_row_baseline(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "snapshotless-legacy-source-backed-canes"
    original = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        external_food_id="legacy-nutritionix-canes",
        verified_source_url="https://example.test/legacy-nutritionix-canes",
    )
    legacy_original = {
        key: value
        for key, value in original.items()
        if key not in {"item_name", "meal_type"}
    }
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": original["item_name"],
            "meal_type": original["meal_type"],
            "context_note": "Canes Box Combo",
            "calories": original["calories"],
            "protein_g": original["protein_g"],
            "carbs_g": original["carbs_g"],
            "fat_g": original["fat_g"],
            "sodium_mg": original["sodium_mg"],
            "fiber_g": original["fiber_g"],
            "source": "nutritionix",
            "correction_state": "pending_review",
            "original_estimate": legacy_original,
        },
    )

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": dict(original)},
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_log"]
    assert row["source"] == "nutritionix"
    assert row["original_estimate"]["source"] == "nutritionix"
    assert row["accepted_estimate"]["source"] == "nutritionix"


def test_import_invalid_current_estimates_preserve_terminal_mixed_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    original_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
    )
    accepted_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="mixed_lookup",
        underlying_source="mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )
    cases = [
        ("invalid-mixed-current", {"source": "mixed_lookup", "underlying_sources": []}),
        ("manual-current", {"source": "manual"}),
    ]
    for client_id, current_estimate in cases:
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "date": "2026-05-18",
                "logged_at": "2026-05-18T12:00:00",
                "item_name": "Canes Box Combo",
                "calories": 920,
                "protein_g": 40,
                "carbs_g": 60,
                "fat_g": 30,
                "sodium_mg": 1000,
                "fiber_g": 4,
                "source": "mixed_lookup",
                "correction_state": "corrected",
                "original_estimate": original_estimate,
                "accepted_estimate": accepted_estimate,
            },
        )
        restored = client.post(
            "/api/import-backup",
            json={
                "data": {
                    "food_logs": [
                        {
                            "client_id": client_id,
                            "date": "2026-05-18",
                            "logged_at": "2026-05-18T12:00:00",
                            "item_name": "Canes Box Combo",
                            "calories": 825,
                            "protein_g": 38,
                            "carbs_g": 54,
                            "fat_g": 25,
                            "sodium_mg": 920,
                            "fiber_g": 5,
                            "source": "mixed_lookup",
                            "correction_state": "corrected",
                            "original_estimate": original_estimate,
                            "accepted_estimate": current_estimate,
                        }
                    ]
                }
            },
        )
        assert restored.status_code == 200, restored.get_data(as_text=True)
        row = next(
            row
            for row in data_store.get_food_logs(1)
            if row["client_id"] == client_id
        )
        assert row["calories"] == 825
        assert row["original_estimate"] == original_estimate
        assert row["accepted_estimate"]["calories"] == 825
        assert row["accepted_estimate"]["source"] == "manual_review_estimate"
        assert "underlying_sources" not in row["accepted_estimate"]


def test_import_partial_approved_current_preserves_terminal_mixed_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    original_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
    )
    accepted_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="mixed_lookup",
        underlying_source="mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )
    client_id = "partial-approved-current"
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": "Canes Box Combo",
            "calories": 920,
            "protein_g": 40,
            "carbs_g": 60,
            "fat_g": 30,
            "sodium_mg": 1000,
            "fiber_g": 4,
            "source": "mixed_lookup",
            "correction_state": "corrected",
            "original_estimate": original_estimate,
            "accepted_estimate": accepted_estimate,
        },
    )
    restored = client.post(
        "/api/import-backup",
        json={
            "data": {
                "food_logs": [
                    {
                        "client_id": client_id,
                        "date": "2026-05-18",
                        "logged_at": "2026-05-18T12:00:00",
                        "item_name": "Canes Box Combo",
                        "calories": 825,
                        "protein_g": 38,
                        "carbs_g": 54,
                        "fat_g": 25,
                        "sodium_mg": 920,
                        "fiber_g": 5,
                        "source": "mixed_lookup",
                        "correction_state": "corrected",
                        "original_estimate": original_estimate,
                        "accepted_estimate": {
                            "source": "nutritionix",
                            "calories": 900,
                        },
                    }
                ]
            }
        },
    )

    assert restored.status_code == 200, restored.get_data(as_text=True)
    row = data_store.get_food_logs(1)[0]
    assert row["calories"] == 825
    assert row["original_estimate"] == original_estimate
    assert row["accepted_estimate"]["calories"] == 825
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"
    assert "underlying_sources" not in row["accepted_estimate"]


def test_history_correction_preserves_existing_mixed_current_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    original_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
    )
    accepted_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="mixed_lookup",
        underlying_source="mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "history-mixed-provenance",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": "Canes Box Combo",
            "calories": 920,
            "protein_g": 40,
            "carbs_g": 60,
            "fat_g": 30,
            "sodium_mg": 1000,
            "fiber_g": 4,
            "source": "mixed_lookup",
            "correction_state": "corrected",
            "original_estimate": original_estimate,
            "accepted_estimate": accepted_estimate,
        },
    )

    corrected = client.post(
        "/api/add-nutrition",
        json={
            "client_id": "history-mixed-provenance",
            "date": "2026-05-18",
            "calories": 825,
            "protein_g": 38,
            "carbs_g": 54,
            "fat_g": 25,
            "sodium_mg": 920,
            "fiber_g": 5,
            "correction_state": "corrected",
        },
    )

    assert corrected.status_code == 200, corrected.get_data(as_text=True)
    row = corrected.get_json()["food_log"]
    assert row["calories"] == 825
    assert row["protein_g"] == 38.0
    assert row["original_estimate"] == original_estimate
    assert row["accepted_estimate"]["calories"] == 825
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"
    assert "underlying_sources" not in row["accepted_estimate"]

    reloaded = data_store.get_food_logs(1)[0]
    assert reloaded["original_estimate"] == original_estimate
    assert reloaded["accepted_estimate"] == row["accepted_estimate"]
    by_date = client.get("/api/food-logs/by-date/2026-05-18")
    history_row = next(
        entry
        for entry in by_date.get_json()["entries"]
        if entry["client_id"] == "history-mixed-provenance"
    )
    assert history_row["accepted_estimate"] == row["accepted_estimate"]
    exported = client.get("/api/export-backup")
    export_row = next(
        entry
        for entry in exported.get_json()["data"]["food_logs"]
        if entry["client_id"] == "history-mixed-provenance"
    )
    assert export_row["accepted_estimate"] == row["accepted_estimate"]


def test_legacy_top_level_canes_identity_blocks_component_snapshot_accept(monkeypatch):
    module = _client(monkeypatch)
    components = [
        _accepted_estimate(
            item_name="Chicken fingers",
            portion_description="4 fingers",
            calories=520,
            source="nutritionix",
        ),
        _accepted_estimate(
            item_name="Fries",
            portion_description="1 serving",
            calories=320,
            source="ai_text_estimate",
        ),
    ]
    top_level_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id="legacy-top-level-canes",
        payload={
            "status": "pending_review",
            "text": "Canes Box Combo",
            "estimate": top_level_estimate,
            "original_estimate": top_level_estimate,
            "items": [
                {
                    "item_id": "component-fingers",
                    "item_order": 0,
                    "status": "included",
                    "text": "Chicken fingers",
                    "estimate": components[0],
                    "original_estimate": components[0],
                    "candidates": [],
                },
                {
                    "item_id": "component-fries",
                    "item_order": 1,
                    "status": "included",
                    "text": "Fries",
                    "estimate": components[1],
                    "original_estimate": components[1],
                    "candidates": [],
                },
            ],
        },
        next_item_seq=3,
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}

    accepted = client.post(
        "/api/meal-intake/legacy-top-level-canes/accept",
        json={},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert set(accepted.get_json()["save_blocked_item_ids"]) == {
        "component-fingers",
        "component-fries",
    }
    assert not any(
        row["correction_state"] in {"accepted", "corrected"}
        for row in data_store.get_food_logs(1)
    )
    assert data_store.get_meal_acceptance_event(1, "legacy-top-level-canes") is None


def test_imported_canes_candidate_cannot_reacquire_source_backed_trust(monkeypatch):
    module = _client(monkeypatch)
    forged_candidate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=1290,
        protein_g=75,
        carbs_g=120,
        fat_g=55,
        source="nutritionix",
        external_food_id="invented-canes-candidate",
        verified_source_url="https://example.test/invented-canes-candidate",
    )
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="ai_text_estimate",
            candidates=[
                {
                    "candidate_id": "imported-forged-nutritionix",
                    "estimate": forged_candidate,
                }
            ],
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "imported-canes-candidate"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]

    data_store.delete_user_data(1)
    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    selected = client.post(
        "/api/meal-intake/imported-canes-candidate/refresh",
        json={
            "kind": "choose_candidate",
            "request_id": "choose-imported-forged-candidate",
            "item_id": "item-1",
            "candidate_id": "imported-forged-nutritionix",
        },
        headers=headers,
    )
    assert selected.status_code == 200, selected.get_data(as_text=True)
    selected_item = selected.get_json()["items"][0]
    accepted = client.post(
        "/api/meal-intake/imported-canes-candidate/accept",
        json={
            "meal_id": "imported-canes-candidate",
            "items": [
                {
                    "item_id": "item-1",
                    "state": "included",
                    "estimate": selected_item["estimate"],
                }
            ],
        },
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert selected_item["server_source_backed_candidate"] is False
    assert not any(
        row["correction_state"] in {"accepted", "corrected"}
        for row in data_store.get_food_logs(1)
    )
    assert data_store.get_meal_acceptance_event(1, "imported-canes-candidate") is None


def test_legacy_top_level_canes_identity_blocks_explicit_component_accept(monkeypatch):
    module = _client(monkeypatch)
    components = [
        _accepted_estimate(
            item_name="Chicken fingers",
            portion_description="4 fingers",
            calories=520,
            source="nutritionix",
        ),
        _accepted_estimate(
            item_name="Fries",
            portion_description="1 serving",
            calories=320,
            source="ai_text_estimate",
        ),
    ]
    top_level_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id="legacy-top-level-canes-explicit",
        payload={
            "status": "pending_review",
            "text": "Canes Box Combo",
            "estimate": top_level_estimate,
            "original_estimate": top_level_estimate,
            "items": [
                {
                    "item_id": "component-fingers",
                    "item_order": 0,
                    "status": "included",
                    "text": "Chicken fingers",
                    "estimate": components[0],
                    "original_estimate": components[0],
                    "candidates": [],
                },
                {
                    "item_id": "component-fries",
                    "item_order": 1,
                    "status": "included",
                    "text": "Fries",
                    "estimate": components[1],
                    "original_estimate": components[1],
                    "candidates": [],
                },
            ],
        },
        next_item_seq=3,
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    items = [
        {
            "item_id": "component-fingers",
            "state": "included",
            "estimate": components[0],
        },
        {
            "item_id": "component-fries",
            "state": "included",
            "estimate": components[1],
        },
    ]
    accepted = client.post(
        "/api/meal-intake/legacy-top-level-canes-explicit/accept",
        json={"meal_id": "legacy-top-level-canes-explicit", "items": items},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert set(accepted.get_json()["save_blocked_item_ids"]) == {
        "component-fingers",
        "component-fries",
    }
    assert not any(
        row["correction_state"] in {"accepted", "corrected"}
        for row in data_store.get_food_logs(1)
    )
    assert data_store.get_meal_acceptance_event(1, "legacy-top-level-canes-explicit") is None

    mismatched = client.post(
        "/api/meal-intake/legacy-top-level-canes-explicit/accept",
        json={"meal_id": "another-meal", "items": items},
        headers=headers,
    )
    assert mismatched.status_code == 400, mismatched.get_data(as_text=True)
    assert not any(
        row["correction_state"] in {"accepted", "corrected"}
        for row in data_store.get_food_logs(1)
    )


def test_manual_history_correction_preserves_mixed_current_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    original_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
    )
    accepted_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="mixed_lookup",
        underlying_source="mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "manual-history-mixed-provenance",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": "Canes Box Combo",
            "calories": 920,
            "protein_g": 40,
            "carbs_g": 60,
            "fat_g": 30,
            "sodium_mg": 1000,
            "fiber_g": 4,
            "source": "mixed_lookup",
            "correction_state": "corrected",
            "original_estimate": original_estimate,
            "accepted_estimate": accepted_estimate,
        },
    )

    corrected = client.post(
        "/api/add-nutrition",
        json={
            "client_id": "manual-history-mixed-provenance",
            "date": "2026-05-18",
            "calories": 825,
            "protein_g": 38,
            "carbs_g": 54,
            "fat_g": 25,
            "sodium_mg": 920,
            "fiber_g": 5,
            "source": "manual",
            "correction_state": "corrected",
        },
    )

    assert corrected.status_code == 200, corrected.get_data(as_text=True)
    row = corrected.get_json()["food_log"]
    assert row["calories"] == 825
    assert row["original_estimate"] == original_estimate
    assert row["source"] == "manual_review_estimate"
    assert row["accepted_estimate"]["calories"] == 825
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"
    assert "underlying_sources" not in row["accepted_estimate"]

    reloaded = data_store.get_food_logs(1)[0]
    assert reloaded["original_estimate"] == original_estimate
    assert reloaded["accepted_estimate"] == row["accepted_estimate"]
    history_row = next(
        entry
        for entry in client.get("/api/food-logs/by-date/2026-05-18").get_json()["entries"]
        if entry["client_id"] == "manual-history-mixed-provenance"
    )
    assert history_row["accepted_estimate"] == row["accepted_estimate"]
    export_row = next(
        entry
        for entry in client.get("/api/export-backup").get_json()["data"]["food_logs"]
        if entry["client_id"] == "manual-history-mixed-provenance"
    )
    assert export_row["accepted_estimate"] == row["accepted_estimate"]


def test_import_null_accepted_estimate_preserves_terminal_current_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    original_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
    )
    accepted_estimate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="mixed_lookup",
        underlying_source="mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "import-null-mixed-provenance",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            "item_name": "Canes Box Combo",
            "calories": 920,
            "protein_g": 40,
            "carbs_g": 60,
            "fat_g": 30,
            "sodium_mg": 1000,
            "fiber_g": 4,
            "source": "mixed_lookup",
            "correction_state": "corrected",
            "original_estimate": original_estimate,
            "accepted_estimate": accepted_estimate,
        },
    )

    restored = client.post(
        "/api/import-backup",
        json={
            "data": {
                "food_logs": [
                    {
                        "client_id": "import-null-mixed-provenance",
                        "date": "2026-05-18",
                        "logged_at": "2026-05-18T12:00:00",
                        "item_name": "Canes Box Combo",
                        "calories": 825,
                        "protein_g": 38,
                        "carbs_g": 54,
                        "fat_g": 25,
                        "sodium_mg": 920,
                        "fiber_g": 5,
                        "source": "mixed_lookup",
                        "correction_state": "corrected",
                        "original_estimate": original_estimate,
                        "accepted_estimate": None,
                    }
                ]
            }
        },
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    row = data_store.get_food_logs(1)[0]
    assert row["calories"] == 825
    assert row["original_estimate"] == original_estimate
    assert row["accepted_estimate"]["calories"] == 825
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"
    assert "underlying_sources" not in row["accepted_estimate"]
    history_row = next(
        entry
        for entry in client.get("/api/food-logs/by-date/2026-05-18").get_json()["entries"]
        if entry["client_id"] == "import-null-mixed-provenance"
    )
    assert history_row["accepted_estimate"] == row["accepted_estimate"]
    export_row = next(
        entry
        for entry in client.get("/api/export-backup").get_json()["data"]["food_logs"]
        if entry["client_id"] == "import-null-mixed-provenance"
    )
    assert export_row["accepted_estimate"] == row["accepted_estimate"]


def test_mixed_vision_cached_lookups_preserve_effective_sources(monkeypatch):
    module = _client(monkeypatch)
    matched = [
        (
            {"name": "Canes Box Combo", "brand": "Raising Cane's"},
            _accepted_estimate(
                item_name="Canes Box Combo",
                source="local_cache",
                underlying_source="nutritionix",
            ),
            "Canes Box Combo",
        ),
        (
            {"name": "Fries", "brand": "Raising Cane's"},
            _accepted_estimate(
                item_name="Fries",
                source="local_cache",
                underlying_source="usda_fdc",
            ),
            "Fries",
        ),
    ]

    estimate = module._combine_vision_item_lookups(matched, missing=[])

    assert estimate["source"] == "mixed_lookup"
    assert estimate["underlying_sources"] == ["nutritionix", "usda_fdc"]
    assert module._is_source_backed_nutrition(estimate) is True
    resolved = module._review_candidate_to_item(
        {"estimate": estimate},
        {
            "branded_combo_ai_only": True,
            "original_estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=840,
                source="ai_text_estimate",
            ),
        },
    )
    assert resolved["server_source_backed_candidate"] is True
    assert module._review_item_is_blocked(resolved) is False


def test_mixed_vision_nested_sources_flatten_with_cached_provider(monkeypatch):
    module = _client(monkeypatch)
    matched = [
        (
            {"name": "Chicken"},
            _accepted_estimate(
                item_name="Chicken",
                source="mixed_lookup",
                underlying_source="mixed_lookup",
                underlying_sources=["nutritionix", "usda_fdc"],
            ),
            "Chicken",
        ),
        (
            {"name": "Fries"},
            _accepted_estimate(
                item_name="Fries",
                source="local_cache",
                underlying_source="open_food_facts",
            ),
            "Fries",
        ),
    ]

    estimate = module._combine_vision_item_lookups(matched, missing=[])

    assert estimate["source"] == "mixed_lookup"
    assert estimate["underlying_sources"] == [
        "nutritionix",
        "open_food_facts",
        "usda_fdc",
    ]
    assert module._is_source_backed_nutrition(estimate) is True


def test_mixed_vision_personal_vocab_source_cannot_launder_approved_underlying_source(monkeypatch):
    module = _client(monkeypatch)
    estimate = module._combine_vision_item_lookups(
        [
            (
                {"name": "Canes Box Combo"},
                _accepted_estimate(
                    item_name="Canes Box Combo",
                    source="personal_vocab",
                    underlying_source="nutritionix",
                ),
                "Canes Box Combo",
            )
        ],
        missing=[],
    )

    assert estimate["source"] == "mixed_lookup"
    assert estimate["ambiguous"] is True
    assert module._is_source_backed_nutrition(estimate) is False


def test_wrapped_mixed_vision_source_is_source_backed_without_trusting_personal_vocab(monkeypatch):
    module = _client(monkeypatch)
    wrapped = _accepted_estimate(
        item_name="Vision Canes Box Combo",
        source="vision_claude+mixed_lookup",
        underlying_source="mixed_lookup",
        underlying_sources=["nutritionix", "usda_fdc"],
    )

    assert module._is_source_backed_nutrition(wrapped) is True
    assert module._is_source_backed_nutrition(
        {**wrapped, "source": "personal_vocab"}
    ) is False


def test_canes_unverified_vision_capture_stays_pending_and_blocks_unchanged_accept(monkeypatch):
    module = _client(monkeypatch)
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "claude",
            "item_description": "Canes Box Combo",
            "portion_hint": "1 combo",
            "confidence": 0.90,
            "ambiguous": False,
            "uncertainty_notes": [],
            "macro_estimate": {
                "meal_type": "lunch",
                "calories": 840,
                "protein_g": 35,
                "carbs_g": 45,
                "fat_g": 18,
                "sodium_mg": 700,
                "fiber_g": 6,
            },
        },
        lookup=None,
    )
    client = module.app.test_client()

    for suffix, text in (("photo-only", None), ("photo-assisted", "Canes Box Combo")):
        client_id = f"canes-vision-{suffix}"
        data = {
            "client_id": client_id,
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        }
        if text:
            data["text"] = text
        capture = client.post(
            "/api/meal-intake",
            data=data,
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert capture.status_code == 200, capture.get_data(as_text=True)
        body = capture.get_json()
        assert body["status"] == "pending_review"
        assert body["food_log"]["correction_state"] == "pending_review"
        assert body["estimate"]["source"] == "vision_claude_estimate"
        assert body["items"][0]["branded_combo_ai_only"] is True
        assert "branded_combo_ai_only" in body["policy"]["reasons"]
        accept = client.post(
            f"/api/meal-intake/{client_id}/accept",
            json={"estimate": body["estimate"]},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert accept.status_code == 409

    rows = data_store.get_food_logs(1)
    assert len(rows) == 2
    assert all(row["correction_state"] == "pending_review" for row in rows)


def test_canes_server_produced_source_backed_vision_estimate_is_not_overblocked(monkeypatch):
    module = _client(monkeypatch)
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "claude",
            "item_description": "Canes Box Combo",
            "portion_hint": "1 combo",
            "confidence": 0.90,
            "ambiguous": False,
            "uncertainty_notes": [],
        },
        lookup=_accepted_estimate(
            item_name="Canes Box Combo",
            portion_description="1 combo",
            calories=920,
            confidence=0.92,
            source="nutritionix",
            external_food_id="server-vision-canes",
            verified_source_url="https://example.test/server-vision-canes",
        ),
    )
    client = module.app.test_client()
    capture = client.post(
        "/api/meal-intake",
        data={
            "client_id": "canes-vision-source-backed",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    body = capture.get_json()
    assert body["estimate"]["source"] == "vision_claude+nutritionix"
    assert body["items"][0]["branded_combo_ai_only"] is False
    accept = client.post(
        "/api/meal-intake/canes-vision-source-backed/accept",
        json={"estimate": body["estimate"]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert accept.status_code == 200, accept.get_data(as_text=True)


def test_photo_assisted_canes_source_backed_refresh_redacts_pending_context(monkeypatch):
    module = _client(monkeypatch)
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "claude",
            "item_description": "Canes Box Combo",
            "portion_hint": "one combo",
            "confidence": 0.90,
            "ambiguous": False,
            "uncertainty_notes": [],
            "macro_estimate": {
                "meal_type": "lunch",
                "calories": 840,
                "protein_g": 35,
                "carbs_g": 45,
                "fat_g": 18,
                "sodium_mg": 700,
                "fiber_g": 6,
            },
        },
        lookup=None,
    )
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=920,
            source="nutritionix",
            external_food_id="photo-refresh-canes",
            verified_source_url="https://example.test/photo-refresh-canes",
        ),
        source="nutritionix",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "photo-refresh-canes-context"
    capture = client.post(
        "/api/meal-intake",
        data={
            "text": "Canes Box Combo",
            "client_id": client_id,
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    assert data_store.get_food_logs(1)[0]["context_note"] == "Canes Box Combo"

    refreshed = client.post(
        f"/api/meal-intake/{client_id}/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "photo-refresh-source-backed",
            "item_id": "item-1",
            "text": "source-backed Canes refresh",
        },
        headers=headers,
    )

    assert refreshed.status_code == 200, refreshed.get_data(as_text=True)
    assert refreshed.get_json()["items"][0]["server_source_backed_candidate"] is True
    assert data_store.get_food_logs(1)[0]["context_note"] is None


def test_mixed_vision_lookup_source_trust_requires_complete_approved_components(monkeypatch):
    module = _client(monkeypatch)
    matched = [
        ({"name": "Chicken"}, _accepted_estimate(item_name="Chicken", source="nutritionix"), "Chicken"),
        ({"name": "Fries"}, _accepted_estimate(item_name="Fries", source="usda_fdc"), "Fries"),
    ]
    estimate = module._combine_vision_item_lookups(matched, missing=[])
    assert estimate["source"] == "mixed_lookup"
    assert estimate["underlying_sources"] == ["nutritionix", "usda_fdc"]
    assert module._is_source_backed_nutrition(estimate) is True
    review_item = module._review_item_from_estimate(estimate, item_id="item-1", item_order=1)
    assert review_item["estimate"]["underlying_sources"] == ["nutritionix", "usda_fdc"]
    aggregate = module._review_aggregate_estimate({"items": [review_item], "meal_type": "lunch"})
    assert aggregate["underlying_sources"] == ["nutritionix", "usda_fdc"]

    for invalid in (
        module._combine_vision_item_lookups(matched, missing=["Drink"]),
        {**estimate, "ambiguous": True},
        {**estimate, "underlying_sources": []},
        {**estimate, "underlying_sources": ["nutritionix", "ai_text_estimate"]},
    ):
        assert module._is_source_backed_nutrition(invalid) is False


def test_legacy_snapshot_marker_fallback_blocks_ai_and_allows_source_backed(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        source = "nutritionix" if text == "verified legacy" else "ai_text_estimate"
        estimate = _accepted_estimate(
            item_name="Canes Box Combo", calories=840, confidence=0.85, source=source,
            external_food_id="verified-legacy" if source == "nutritionix" else None,
            verified_source_url="https://example.test/verified-legacy" if source == "nutritionix" else None,
        )
        if text == "mixed legacy":
            estimate.update(source="mixed_lookup", underlying_source="mixed_lookup", underlying_sources=["nutritionix", "usda_fdc"])
        return {"estimate": estimate, "fallback_used": False}

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    accepted_rows = []
    for client_id, text, expected in (
        ("legacy-ai-marker", "Canes Box Combo", 409),
        ("legacy-source-marker", "verified legacy", 200),
        ("legacy-mixed-marker", "mixed legacy", 200),
    ):
        capture = client.post("/api/meal-intake", data={"text": text, "client_id": client_id}, content_type="multipart/form-data", headers=headers)
        snapshot = data_store.get_meal_review_snapshot(1, client_id)
        snapshot["payload"]["items"][0].pop("branded_combo_ai_only", None)
        data_store.save_meal_review_snapshot(1, meal_id=client_id, payload=snapshot["payload"], next_item_seq=snapshot["next_item_seq"], applied_refreshes=snapshot.get("applied_refreshes"))
        accept = client.post(f"/api/meal-intake/{client_id}/accept", json={}, headers=headers)
        assert capture.status_code == 200
        assert accept.status_code == expected, accept.get_data(as_text=True)
        if expected == 200:
            accepted_rows.append(accept.get_json()["food_logs"][0])
    rows = data_store.get_food_logs(1)
    assert len(rows) == 3
    rows_by_client = {row["client_id"]: row for row in rows}
    assert rows_by_client["legacy-ai-marker"]["correction_state"] == "pending_review"
    assert next(row for row in rows if row["source"] == "nutritionix")["original_estimate"]["source"] == "nutritionix"
    mixed_row = next(row for row in accepted_rows if row["source"] == "mixed_lookup")
    assert mixed_row["accepted_estimate"]["underlying_sources"] == ["nutritionix", "usda_fdc"]
    assert data_store.get_meal_acceptance_event(1, "legacy-ai-marker") is None


def test_mixed_accept_persists_current_provenance_without_mutating_ai_original(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    original = _accepted_estimate(item_name="Canes Box Combo", calories=840, source="ai_text_estimate")
    mixed = _accepted_estimate(item_name="Canes Box Combo", calories=920, source="mixed_lookup", underlying_source="mixed_lookup", underlying_sources=["nutritionix", "usda_fdc"])
    row = module._meal_intake_persist("mixed-provenance", mixed, source="mixed_lookup", has_image=False, text_hint=None, original_estimate=original)
    reloaded = data_store.get_food_logs(1)[0]
    assert row["accepted_estimate"]["underlying_sources"] == ["nutritionix", "usda_fdc"]
    assert reloaded["accepted_estimate"] == row["accepted_estimate"]
    assert reloaded["original_estimate"]["source"] == "ai_text_estimate"
    assert "underlying_sources" not in reloaded["original_estimate"]


def test_malformed_mixed_sources_are_not_persisted_or_trusted(monkeypatch):
    module = _client(monkeypatch)
    for sources in ([], ["nutritionix", 3], ["nutritionix", "ai_text_estimate"]):
        estimate = _accepted_estimate(source="mixed_lookup", underlying_source="mixed_lookup", underlying_sources=sources)
        assert module._is_source_backed_nutrition(estimate) is False
        row = module._meal_intake_persist(f"bad-mixed-{len(sources)}-{str(sources)[-1]}", estimate, source="mixed_lookup", has_image=False, text_hint=None)
        assert "underlying_sources" not in (row.get("accepted_estimate") or {})


def test_canes_component_items_inherit_top_level_ai_only_marker(monkeypatch):
    module = _client(monkeypatch)
    component_items = [
        {
            "item_id": "chicken",
            "estimate": _accepted_estimate(
                item_name="Chicken fingers",
                portion_description="4 fingers",
                calories=520,
                source="ai_text_estimate",
            ),
        },
        {
            "item_id": "fries",
            "estimate": _accepted_estimate(
                item_name="Crinkle-cut fries",
                portion_description="1 serving",
                calories=320,
                source="ai_text_estimate",
            ),
        },
    ]
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            confidence=0.85,
            source="ai_text_estimate",
            items=component_items,
        ),
    )
    client = module.app.test_client()

    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "canes-component-marker"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert capture.status_code == 200, capture.get_data(as_text=True)
    body = capture.get_json()
    assert [item["branded_combo_ai_only"] for item in body["items"]] == [True, True]
    assert "branded_combo_ai_only" in body["policy"]["reasons"]
    blocked = client.post(
        "/api/meal-intake/canes-component-marker/accept",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["save_blocked_item_ids"] == ["chicken", "fries"]
    rows = data_store.get_food_logs(1)
    assert len(rows) == 1
    assert rows[0]["correction_state"] == "pending_review"


def test_canes_source_backed_component_items_are_not_overblocked(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=920,
            confidence=0.85,
            source="ai_text_estimate",
            items=[
                {
                    "item_id": "verified-combo",
                    "estimate": _accepted_estimate(
                        item_name="Canes Box Combo",
                        portion_description="1 combo",
                        calories=920,
                        source="nutritionix",
                        external_food_id="server-verified-component",
                        verified_source_url="https://example.test/server-verified-component",
                    ),
                }
            ],
        ),
    )
    client = module.app.test_client()
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "canes-source-component"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert capture.status_code == 200, capture.get_data(as_text=True)
    body = capture.get_json()
    assert body["items"][0]["branded_combo_ai_only"] is False
    assert "branded_combo_ai_only" not in body["policy"]["reasons"]
    accept = client.post(
        "/api/meal-intake/canes-source-component/accept",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert accept.status_code == 200, accept.get_data(as_text=True)


def test_canes_add_item_raw_alias_survives_parser_rephrasing(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        if text == "Canes Box Combo":
            return {
                "estimate": _accepted_estimate(
                    item_name="Raising Cane's 4-Finger Box Combo",
                    calories=840,
                    confidence=0.85,
                    source="ai_text_estimate",
                ),
                "fallback_used": False,
            }
        return {
            "estimate": _accepted_estimate(calories=840, source="ai_text_estimate"),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    capture = client.post(
        "/api/meal-intake",
        data={"text": "ordinary lunch", "client_id": "canes-add-rephrased"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    added = client.post(
        "/api/meal-intake/canes-add-rephrased/refresh",
        json={"kind": "add_item", "request_id": "add-rephrased", "text": "Canes Box Combo"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert capture.status_code == 200, capture.get_data(as_text=True)
    assert added.status_code == 200, added.get_data(as_text=True)
    assert added.get_json()["items"][-1]["branded_combo_ai_only"] is True
    assert "branded_combo_ai_only" in added.get_json()["policy"]["reasons"]
    assert client.post(
        "/api/meal-intake/canes-add-rephrased/accept",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    ).status_code == 409


def test_canes_edit_portion_raw_alias_survives_parser_rephrasing(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        if text == "Canes Box Combo":
            return {
                "estimate": _accepted_estimate(
                    item_name="Raising Cane's 4-Finger Box Combo",
                    calories=840,
                    confidence=0.85,
                    source="ai_text_estimate",
                ),
                "fallback_used": False,
            }
        return {
            "estimate": _accepted_estimate(calories=840, source="ai_text_estimate"),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    capture = client.post(
        "/api/meal-intake",
        data={"text": "ordinary lunch", "client_id": "canes-edit-rephrased"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    edited = client.post(
        "/api/meal-intake/canes-edit-rephrased/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "edit-rephrased",
            "item_id": "item-1",
            "text": "Canes Box Combo",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert capture.status_code == 200, capture.get_data(as_text=True)
    assert edited.status_code == 200, edited.get_data(as_text=True)
    assert edited.get_json()["items"][0]["branded_combo_ai_only"] is True
    assert "branded_combo_ai_only" in edited.get_json()["policy"]["reasons"]
    assert client.post(
        "/api/meal-intake/canes-edit-rephrased/accept",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    ).status_code == 409


def test_canes_followup_raw_alias_survives_parser_rephrasing(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        if text == "Canes Box Combo":
            return {
                "estimate": _accepted_estimate(
                    item_name="Raising Cane's 4-Finger Box Combo",
                    calories=840,
                    confidence=0.85,
                    source="ai_text_estimate",
                ),
                "fallback_used": False,
            }
        return {
            "estimate": _accepted_estimate(
                item_name="Unclear lunch",
                calories=840,
                confidence=0.40,
                ambiguous=True,
                source="ai_text_estimate",
                clarification_question="What was in the meal?",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    capture = client.post(
        "/api/meal-intake",
        data={"text": "unclear lunch", "client_id": "canes-followup-rephrased"},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    followup = client.post(
        "/api/meal-intake/canes-followup-rephrased/refresh",
        json={"kind": "followup_answer", "request_id": "followup-rephrased", "answer": "Canes Box Combo"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert capture.status_code == 200, capture.get_data(as_text=True)
    assert followup.status_code == 200, followup.get_data(as_text=True)
    assert followup.get_json()["items"][0]["branded_combo_ai_only"] is True
    assert "branded_combo_ai_only" in followup.get_json()["policy"]["reasons"]
    assert client.post(
        "/api/meal-intake/canes-followup-rephrased/accept",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    ).status_code == 409


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


def test_meal_intake_vision_contention_logs_distinct_tag(monkeypatch, caplog):
    module = _client(monkeypatch)
    monkeypatch.setattr(
        module.vision_estimator,
        "describe",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            module.vision_estimator.VisionEstimatorError(
                "busy: LM Studio vision inference already running"
            )
        ),
    )
    _stub_parser(monkeypatch, module, estimate={
        "item_name": "Protein shake",
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
    caplog.set_level("WARNING")

    response = module.app.test_client().post(
        "/api/meal-intake",
        data={
            "text": "protein shake",
            "client_id": "meal-vision-contention-1",
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert any(
        record.message == "vision_busy_contention"
        and getattr(record, "event", None) == "vision_busy_contention"
        for record in caplog.records
    )


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
    assert "from_image" not in persisted["original_estimate"]
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
    assert "vision_provider" not in original
    assert "vision_confidence" not in original
    assert "off_attribution" not in original
    assert "verified_source_url" not in original


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


def test_direct_accept_ignores_client_asserted_correction_state(monkeypatch):
    module = _client(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        module,
        "add_food_log",
        lambda _user_id, record: (captured.update(record), dict(record))[1],
    )

    response = module.app.test_client().post(
        "/api/meal-intake/client-asserted-correction/accept",
        json={
            "estimate": _accepted_estimate(),
            "original_estimate": _accepted_estimate(calories=900),
            "correction_state": "corrected",
            "corrected": True,
        },
    )

    assert response.status_code == 200
    assert captured["correction_state"] == "accepted"


def test_duplicate_accept_with_changed_payload_keeps_original_accepted_row(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    first = client.post(
        "/api/meal-intake/duplicate-accept/accept",
        json={"estimate": _accepted_estimate(calories=500)},
    )
    second = client.post(
        "/api/meal-intake/duplicate-accept/accept",
        json={"estimate": _accepted_estimate(calories=900)},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    stored = next(
        row for row in data_store.get_food_logs(1) if row["client_id"] == "duplicate-accept"
    )
    assert stored["calories"] == 500
    assert second.get_json()["error"]["code"] == "duplicate_client_id"


def test_duplicate_accept_cannot_claim_refresh_authority_from_request_metadata(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    first = client.post(
        "/api/meal-intake/client-refresh-claim/accept",
        json={"estimate": _accepted_estimate(calories=500)},
    )
    asserted_refresh = _accepted_estimate(calories=900)
    asserted_refresh.update({
        "underlying_source": "nutritionix",
        "verified_source_url": "https://example.test/asserted",
        "external_food_id": "asserted-id",
    })
    second = client.post(
        "/api/meal-intake/client-refresh-claim/accept",
        json={"estimate": asserted_refresh},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    stored = next(
        row for row in data_store.get_food_logs(1) if row["client_id"] == "client-refresh-claim"
    )
    assert stored["calories"] == 500


def test_legacy_corrected_duplicate_uses_current_row_and_stored_image_provenance(monkeypatch):
    module = _client(monkeypatch)
    original = _accepted_estimate(calories=400, source="ai_text_estimate", from_image=True)
    current = _accepted_estimate(calories=500, source="manual_review_estimate")
    stored = data_store.add_food_log(
        1,
        {
            "client_id": "legacy-corrected-duplicate",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **current,
            "correction_state": "corrected",
            "original_estimate": original,
        },
    )
    assert stored["accepted_estimate"] is None

    response = module.app.test_client().post(
        "/api/meal-intake/legacy-corrected-duplicate/accept",
        json={"estimate": current},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is True


def test_legacy_duplicate_rejects_request_asserted_image_provenance(monkeypatch):
    module = _client(monkeypatch)
    current = _accepted_estimate(calories=500, source="manual_review_estimate")
    data_store.add_food_log(
        1,
        {
            "client_id": "legacy-text-duplicate",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **current,
            "correction_state": "corrected",
            "original_estimate": current,
        },
    )
    asserted_image = dict(current, from_image=True)

    response = module.app.test_client().post(
        "/api/meal-intake/legacy-text-duplicate/accept",
        json={"estimate": asserted_image},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is False


def test_fresh_legacy_accept_does_not_infer_image_from_client_source(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    estimate = _accepted_estimate(
        source="vision_forged",
        from_image=True,
        external_food_id="forged-fresh-legacy",
        verified_source_url="https://example.test/forged-fresh-legacy",
    )

    response = module.app.test_client().post(
        "/api/meal-intake/fresh-legacy-forged-source/accept",
        json={"estimate": estimate},
    )
    retry = module.app.test_client().post(
        "/api/meal-intake/fresh-legacy-forged-source/accept",
        json={"estimate": estimate},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert retry.status_code == 200, retry.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is False
    assert retry.get_json()["photo_retention"]["image_received"] is False
    accepted = response.get_json()["food_log"]["accepted_estimate"]
    assert accepted["source"] == "manual_review_estimate"
    assert "from_image" not in accepted
    assert "external_food_id" not in accepted
    assert "verified_source_url" not in accepted


def test_legacy_pending_accept_uses_stored_provenance_and_capture_fields(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client_id = "legacy-pending-stored-provenance"
    estimate = _accepted_estimate(
        item_name="Stored text meal",
        calories=420,
        source="ai_text_estimate",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            "source_timestamp": "2026-05-22T11:59:00",
            "context_note": "canonical stored phrase",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )
    asserted = dict(
        estimate,
        from_image=True,
        source="vision_forged",
        vision_provider="forged-provider",
        external_food_id="forged-id",
        verified_source_url="https://example.test/forged",
        personal_vocab_phrase="forged legacy phrase",
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{client_id}/accept",
        json={
            "estimate": asserted,
            "text": "poisoned request phrase",
            "local_timestamp": "2026-06-01T20:00:00Z",
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["photo_retention"]["image_received"] is False
    row = data_store.get_food_log_by_client_id(1, client_id)
    assert row["source"] == "ai_text_estimate"
    assert row["from_image"] is not True
    assert row["date"] == "2026-05-22"
    assert row["logged_at"] == "2026-05-22T12:00:00"
    assert row["source_timestamp"] == "2026-05-22T11:59:00"
    assert row["context_note"] == "canonical stored phrase"
    assert "vision_provider" not in row["accepted_estimate"]
    assert "external_food_id" not in row["accepted_estimate"]
    assert "verified_source_url" not in row["accepted_estimate"]
    assert "personal_vocab_phrase" not in row["accepted_estimate"]


def test_legacy_pending_meal_type_edit_preserves_stored_nutrition_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client_id = "legacy-pending-meal-type"
    estimate = _accepted_estimate(
        item_name="Verified meal",
        calories=420,
        meal_type="breakfast",
        source="nutritionix",
        external_food_id="verified-legacy-meal",
        verified_source_url="https://example.test/verified-legacy-meal",
        ambiguous=True,
        uncertainty_notes=["Stored provider ambiguity"],
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{client_id}/accept",
        json={
            "estimate": dict(
                estimate,
                meal_type="lunch",
                confidence=1.0,
                ambiguous=False,
                uncertainty_notes=["Forged certainty"],
            ),
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    accepted = data_store.get_food_log_by_client_id(1, client_id)["accepted_estimate"]
    assert accepted["meal_type"] == "lunch"
    assert accepted["source"] == "nutritionix"
    assert accepted["confidence"] == estimate["confidence"]
    assert accepted["ambiguous"] is True
    assert accepted["uncertainty_notes"] == ["Stored provider ambiguity"]
    assert accepted["external_food_id"] == "verified-legacy-meal"
    assert accepted["verified_source_url"] == "https://example.test/verified-legacy-meal"


def test_legacy_pending_accept_rejects_concurrent_refresh(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client_id = "legacy-pending-race"
    original_estimate = _accepted_estimate(item_name="Original", calories=300)
    refreshed_estimate = _accepted_estimate(item_name="Refreshed", calories=450)
    base_record = {
        "client_id": client_id,
        "date": "2026-05-22",
        "logged_at": "2026-05-22T12:00:00",
        "correction_state": "pending_review",
    }
    data_store.add_food_log(
        1,
        {**base_record, **original_estimate, "original_estimate": original_estimate},
    )
    original_lookup = module._food_log_by_client_id
    refreshed = False

    def refresh_after_preflight(user_id, requested_client_id):
        nonlocal refreshed
        row = original_lookup(user_id, requested_client_id)
        if requested_client_id == client_id and not refreshed:
            refreshed = True
            data_store.add_food_log(
                1,
                {**base_record, **refreshed_estimate, "original_estimate": refreshed_estimate},
            )
        return row

    monkeypatch.setattr(module, "_food_log_by_client_id", refresh_after_preflight)

    response = module.app.test_client().post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": original_estimate},
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    stored = data_store.get_food_log_by_client_id(1, client_id)
    assert stored["item_name"] == "Refreshed"
    assert stored["correction_state"] == "pending_review"


def test_legacy_accept_rejects_pending_row_created_after_empty_preflight(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client_id = "legacy-new-pending-race"
    submitted_estimate = _accepted_estimate(item_name="Submitted", calories=300)
    pending_estimate = _accepted_estimate(item_name="New pending", calories=450)
    original_lookup = module._food_log_by_client_id
    created = False

    def create_pending_after_empty_preflight(user_id, requested_client_id):
        nonlocal created
        row = original_lookup(user_id, requested_client_id)
        if requested_client_id == client_id and row is None and not created:
            created = True
            data_store.add_food_log(
                1,
                {
                    "client_id": client_id,
                    "date": "2026-05-22",
                    "logged_at": "2026-05-22T12:00:00",
                    **pending_estimate,
                    "correction_state": "pending_review",
                    "original_estimate": pending_estimate,
                },
            )
        return row

    monkeypatch.setattr(module, "_food_log_by_client_id", create_pending_after_empty_preflight)

    response = module.app.test_client().post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": submitted_estimate},
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    stored = data_store.get_food_log_by_client_id(1, client_id)
    assert stored["item_name"] == "New pending"
    assert stored["correction_state"] == "pending_review"


def test_race_conflict_legacy_row_uses_current_values_and_stored_image_provenance(monkeypatch):
    module = _client(monkeypatch)
    original = _accepted_estimate(calories=400, source="ai_text_estimate")
    current = _accepted_estimate(calories=500, source="manual_review_estimate")
    conflict_row = {
        **current,
        "client_id": "legacy-race-conflict",
        "correction_state": "corrected",
        "original_estimate": original,
        "accepted_estimate": None,
        "from_image": True,
        "_protected_client_id_conflict": True,
    }
    monkeypatch.setattr(
        module,
        "_meal_intake_persist",
        lambda *_args, **_kwargs: dict(conflict_row),
    )

    response = module.app.test_client().post(
        "/api/meal-intake/legacy-race-conflict/accept",
        json={"estimate": current},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is True


def test_direct_protected_conflict_rolls_back_pending_child_cleanup(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client_id = "direct-manual-conflict"
    child_id = "direct-manual-conflict-pending-child"
    existing = _accepted_estimate(item_name="Existing manual row", calories=100)
    submitted = _accepted_estimate(item_name="Changed accept", calories=300)
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **existing,
            "correction_state": "manual",
        },
    )
    data_store.add_food_log(
        1,
        {
            "client_id": child_id,
            "meal_id": client_id,
            "meal_item_id": "item-1",
            "item_index": 0,
            "item_state": "included",
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **submitted,
            "correction_state": "pending_review",
            "original_estimate": submitted,
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": submitted},
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "duplicate_client_id"
    pending_child = data_store.get_food_log_by_client_id(1, child_id)
    assert pending_child["correction_state"] == "pending_review"
    assert pending_child["meal_id"] == client_id


def test_identical_direct_retry_repairs_accept_side_effects(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client_id = "direct-retry-side-effects"
    estimate = _accepted_estimate(item_name="Retry side effects", calories=300)
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "accepted",
            "original_estimate": estimate,
            "accepted_estimate": estimate,
        },
    )
    learned = []
    enqueued = []
    monkeypatch.setattr(
        module.personal_vocab,
        "record_accept",
        lambda user_id, phrase, accepted: learned.append(
            (user_id, phrase, accepted)
        ),
    )
    monkeypatch.setattr(
        module,
        "_enqueue_workout_adaptation_after_accept",
        lambda user_id, rows: enqueued.append((user_id, rows)),
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": estimate},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert len(learned) == 1
    assert learned[0][0] == 1
    assert learned[0][2]["calories"] == 300
    assert enqueued == [(1, [response.get_json()["food_log"]])]


def test_protected_food_log_accept_is_atomic_across_connections(monkeypatch):
    module = _client(monkeypatch)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def accept(calories):
        barrier.wait()
        with module.app.test_client() as client:
            return client.post(
                "/api/meal-intake/concurrent-accept/accept",
                json={"estimate": _accepted_estimate(calories=calories)},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(accept, (500, 900)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.get_json()["food_log"] for response in responses if response.status_code == 200)
    stored = next(
        row for row in data_store.get_food_logs(1) if row["client_id"] == "concurrent-accept"
    )
    assert stored["calories"] == winner["calories"]


def test_concurrent_identical_food_log_accepts_are_idempotent(monkeypatch):
    module = _client(monkeypatch)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def accept():
        barrier.wait()
        with module.app.test_client() as client:
            return client.post(
                "/api/meal-intake/concurrent-identical-accept/accept",
                json={"estimate": _accepted_estimate(calories=500)},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: accept(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert {response.get_json()["food_log"]["calories"] for response in responses} == {500}
    stored = [
        row for row in data_store.get_food_logs(1)
        if row["client_id"] == "concurrent-identical-accept"
    ]
    assert len(stored) == 1


def test_concurrent_identical_corrected_accepts_are_idempotent(monkeypatch):
    module = _client(monkeypatch)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client_id = "concurrent-identical-corrected"
    original = _accepted_estimate(calories=400)
    data_store.add_food_log(1, {
        "client_id": client_id,
        "item_name": original["item_name"],
        "calories": original["calories"],
        "protein_g": original["protein_g"],
        "carbs_g": original["carbs_g"],
        "fat_g": original["fat_g"],
        "source": original["source"],
        "correction_state": "pending_review",
        "original_estimate": original,
    })
    barrier = Barrier(2)

    def accept():
        barrier.wait()
        with module.app.test_client() as client:
            return client.post(
                f"/api/meal-intake/{client_id}/accept",
                json={"estimate": _accepted_estimate(calories=500)},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: accept(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert {response.get_json()["food_log"]["calories"] for response in responses} == {500}
    stored = next(row for row in data_store.get_food_logs(1) if row["client_id"] == client_id)
    assert stored["correction_state"] == "corrected"
    assert stored["original_estimate"]["calories"] == 400
    assert stored["accepted_estimate"]["calories"] == 500


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
    assert vocab_calls[0][1] == "Bill Miller BBQ order: 1 Bacon & Egg Taco; 2 Breakfast Sandwich"


def test_meal_intake_client_correction_flag_does_not_train_as_correction(monkeypatch):
    module = _client(monkeypatch)
    vocab_calls = []
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: True)
    monkeypatch.setattr(module.personal_vocab, "record_accept", lambda *args, **_kw: vocab_calls.append(args))
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
    """A fresh accept cannot assert parser provenance without stored state."""
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
    assert captured["source"] == "manual_review_estimate"


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


def test_unsnapshotted_multi_accept_ignores_client_correction_claims(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    accepted_calls = []
    corrected_calls = []
    monkeypatch.setattr(
        module.personal_vocab,
        "record_accept",
        lambda *_args, **_kwargs: accepted_calls.append(True),
    )
    monkeypatch.setattr(
        module.personal_vocab,
        "record_correct",
        lambda *_args, **_kwargs: corrected_calls.append(True),
    )
    estimate = _accepted_estimate(calories=500)

    response = module.app.test_client().post(
        "/api/meal-intake/untrusted-multi-correction/accept",
        json={
            "meal_id": "untrusted-multi-correction",
            "items": [
                {
                    "state": "included",
                    "item_id": "item-1",
                    "estimate": estimate,
                    "original_estimate": _accepted_estimate(calories=400),
                    "corrected": True,
                    "correction_state": "corrected",
                }
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["food_logs"][0]["correction_state"] == "accepted"
    assert accepted_calls == [True]
    assert corrected_calls == []


def test_concurrent_changed_multi_accepts_cannot_overwrite_terminal_row(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def accept(calories):
        barrier.wait()
        with module.app.test_client() as client:
            return client.post(
                "/api/meal-intake/concurrent-multi/accept",
                json={
                    "meal_id": "concurrent-multi",
                    "items": [
                        {
                            "state": "included",
                            "item_id": "item-1",
                            "estimate": _accepted_estimate(calories=calories),
                        }
                    ],
                },
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(accept, (500, 900)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(
        response.get_json()["food_logs"][0]
        for response in responses
        if response.status_code == 200
    )
    stored = next(
        row for row in data_store.get_food_logs(1)
        if row["meal_id"] == "concurrent-multi"
    )
    assert stored["calories"] == winner["calories"]


def test_concurrent_disjoint_item_sets_cannot_share_a_meal_id(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def accept(item_id):
        barrier.wait()
        with module.app.test_client() as client:
            return client.post(
                "/api/meal-intake/concurrent-disjoint/accept",
                json={
                    "meal_id": "concurrent-disjoint",
                    "items": [
                        {
                            "state": "included",
                            "item_id": item_id,
                            "estimate": _accepted_estimate(item_name=item_id, calories=500),
                        }
                    ],
                },
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(accept, ("item-a", "item-b")))

    assert sorted(response.status_code for response in responses) == [200, 409]
    rows = [row for row in data_store.get_food_logs(1) if row["meal_id"] == "concurrent-disjoint"]
    assert len(rows) == 1
    event = data_store.get_meal_acceptance_event(1, "concurrent-disjoint")
    assert event["included_client_ids"] == [rows[0]["client_id"]]


def test_concurrent_identical_multi_replay_records_negative_feedback_once(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    payload = {
        "meal_id": "concurrent-identical-feedback",
        "items": [
            {
                "state": "included",
                "item_id": "item-1",
                "estimate": _accepted_estimate(item_name="Meal", calories=500),
            },
            {
                "state": "skipped",
                "item_id": "plate",
                "text": "plate",
                "estimate": _accepted_estimate(item_name="Plate", calories=10),
            },
        ],
    }

    def accept():
        barrier.wait()
        with module.app.test_client() as client:
            return client.post(
                "/api/meal-intake/concurrent-identical-feedback/accept",
                json=payload,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: accept(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    feedback = data_store.get_personal_vocab_entry(1, "plate")
    assert feedback["skip_count"] == 1


def test_concurrent_identical_pending_multi_accepts_return_canonical_success(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    parent_client_id = "concurrent-pending-parent"
    meal_id = "concurrent-pending-meal"
    item_id = "item-1"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    estimate = _accepted_estimate(item_name="Pending meal", calories=500)
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )
    barrier = Barrier(2)
    call_lock = Lock()
    initial_reads = 0
    original_existing_rows = module._meal_existing_rows

    def synchronized_initial_rows(*args, **kwargs):
        nonlocal initial_reads
        rows = original_existing_rows(*args, **kwargs)
        with call_lock:
            should_wait = initial_reads < 2
            initial_reads += 1
        if should_wait:
            barrier.wait()
        return rows

    monkeypatch.setattr(module, "_meal_existing_rows", synchronized_initial_rows)
    payload = {
        "meal_id": meal_id,
        "items": [
            {"state": "included", "item_id": item_id, "estimate": estimate},
        ],
    }

    def accept():
        with module.app.test_client() as client:
            return client.post(f"/api/meal-intake/{parent_client_id}/accept", json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: accept(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.get_json()["status"] == "logged" for response in responses)


def test_concurrent_identical_snapshot_accepts_return_canonical_success(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    meal_id = "concurrent-snapshot-meal"
    item_id = "item-1"
    estimate = _accepted_estimate(item_name="Snapshot meal", calories=500)
    module._review_save_snapshot(
        1,
        meal_id,
        {
            "status": "pending_review",
            "meal_id": meal_id,
            "items": [
                module._review_item_from_estimate(
                    estimate,
                    item_id=item_id,
                    item_order=1,
                    status="included",
                    text="Snapshot meal",
                ),
            ],
        },
        2,
        {},
    )
    second_snapshot_captured = Event()
    first_request_finished = Event()
    call_lock = Lock()
    initial_reads = 0
    original_get_snapshot = module.get_meal_review_snapshot

    def synchronized_route_snapshot(*args, **kwargs):
        nonlocal initial_reads
        snapshot = original_get_snapshot(*args, **kwargs)
        with call_lock:
            read_index = initial_reads
            initial_reads += 1
        if read_index == 0:
            second_snapshot_captured.wait()
        elif read_index == 1:
            second_snapshot_captured.set()
            first_request_finished.wait()
        return snapshot

    monkeypatch.setattr(module, "get_meal_review_snapshot", synchronized_route_snapshot)
    payload = {
        "meal_id": meal_id,
        "items": [
            {"state": "included", "item_id": item_id, "estimate": estimate},
        ],
    }

    def accept():
        with module.app.test_client() as client:
            response = client.post(f"/api/meal-intake/{meal_id}/accept", json=payload)
        first_request_finished.set()
        return response

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: accept(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.get_json()["status"] == "logged" for response in responses)


def test_concurrent_identical_discarded_meal_records_negative_feedback_once(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    payload = {
        "meal_id": "concurrent-discarded-feedback",
        "items": [
            {
                "state": "skipped",
                "item_id": "plate",
                "text": "plate",
                "estimate": _accepted_estimate(item_name="Plate", calories=10),
            }
        ],
    }

    def discard():
        barrier.wait()
        with module.app.test_client() as client:
            return client.post(
                "/api/meal-intake/concurrent-discarded-feedback/accept",
                json=payload,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: discard(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    feedback = data_store.get_personal_vocab_entry(1, "plate")
    assert feedback["skip_count"] == 1


def test_multi_accept_preflights_all_terminal_conflicts_before_writing(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "multi-preflight-parent"
    meal_id = "multi-preflight-meal"
    existing_client_id = module._meal_item_client_id(
        parent_client_id,
        {"item_id": "existing"},
        1,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": existing_client_id,
            "meal_id": meal_id,
            "meal_item_id": "existing",
            "item_index": 1,
            "item_state": "included",
            **_accepted_estimate(item_name="Existing", calories=200),
            "correction_state": "accepted",
            "accepted_estimate": _accepted_estimate(item_name="Existing", calories=200),
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "included",
                    "item_id": "new",
                    "estimate": _accepted_estimate(item_name="New", calories=100),
                },
                {
                    "state": "included",
                    "item_id": "existing",
                    "estimate": _accepted_estimate(item_name="Existing", calories=900),
                },
            ],
        },
    )

    assert response.status_code == 409
    rows = data_store.get_food_logs(1)
    assert [(row["meal_item_id"], row["calories"]) for row in rows] == [("existing", 200)]


def test_multi_accept_rolls_back_earlier_rows_on_late_atomic_conflict(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    original_persist = module._meal_intake_persist
    calls = 0

    def conflict_on_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_persist(*args, **kwargs)
        stored_estimate = _accepted_estimate(item_name="Second", calories=200)
        return {
            **stored_estimate,
            "client_id": args[0],
            "meal_id": "atomic-conflict-meal",
            "correction_state": "accepted",
            "accepted_estimate": stored_estimate,
            "_protected_client_id_conflict": True,
        }

    monkeypatch.setattr(module, "_meal_intake_persist", conflict_on_second)
    response = module.app.test_client().post(
        "/api/meal-intake/atomic-conflict-parent/accept",
        json={
            "meal_id": "atomic-conflict-meal",
            "items": [
                {
                    "state": "included",
                    "item_id": "first",
                    "estimate": _accepted_estimate(item_name="First", calories=100),
                },
                {
                    "state": "included",
                    "item_id": "second",
                    "estimate": _accepted_estimate(item_name="Second", calories=900),
                },
            ],
        },
    )

    assert response.status_code == 409
    assert data_store.get_food_logs(1) == []
    assert data_store.get_meal_acceptance_event(1, "atomic-conflict-meal") is None


def test_multi_accept_rolls_back_rows_when_vocab_learning_fails(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    module.app.config.update(PROPAGATE_EXCEPTIONS=False)
    monkeypatch.setattr(
        module.personal_vocab,
        "record_accept",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("vocab failed")),
    )

    response = module.app.test_client().post(
        "/api/meal-intake/atomic-vocab-parent/accept",
        json={
            "meal_id": "atomic-vocab-meal",
            "items": [
                {
                    "state": "included",
                    "item_id": "item-1",
                    "estimate": _accepted_estimate(item_name="Atomic vocab", calories=500),
                }
            ],
        },
    )

    assert response.status_code == 500

    assert data_store.get_food_logs(1) == []
    assert data_store.get_meal_acceptance_event(1, "atomic-vocab-meal") is None


def test_equivalent_multi_item_client_id_cannot_move_to_another_meal(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "multi-meal-identity-parent"
    item = {
        "state": "included",
        "item_id": "item-1",
        "estimate": _accepted_estimate(calories=500),
    }
    client = module.app.test_client()
    first = client.post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={"meal_id": "meal-one", "items": [item]},
    )
    second = client.post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={"meal_id": "meal-two", "items": [item]},
    )

    assert first.status_code == 200, first.get_data(as_text=True)
    assert second.status_code == 409
    assert data_store.get_meal_acceptance_event(1, "meal-two") is None
    assert [row["meal_id"] for row in data_store.get_food_logs(1)] == ["meal-one"]


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


def test_review_snapshot_refresh_writes_pending_row_and_snapshot_in_one_transaction(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    connections = {}
    estimate = _accepted_estimate(item_name="Atomic refresh", calories=300)
    payload = {
        "status": "pending_review",
        "meal_id": "atomic-refresh",
        "items": [
            {
                "item_id": "item-1",
                "status": "included",
                "estimate": estimate,
                "original_estimate": estimate,
            },
        ],
    }

    def fake_sync(*_args, connection=None, **_kwargs):
        connections["row"] = connection
        return {"client_id": "atomic-refresh"}

    def fake_save(*_args, _conn=None, **kwargs):
        connections["snapshot"] = _conn
        return {"payload": kwargs["payload"]}

    monkeypatch.setattr(module, "_review_sync_pending_row", fake_sync)
    monkeypatch.setattr(module, "save_meal_review_snapshot", fake_save)

    module._review_save_snapshot(1, "atomic-refresh", payload, 2, {})

    assert connections["row"] is not None
    assert connections["snapshot"] is connections["row"]


def test_review_snapshot_refresh_does_not_recreate_pending_row_after_terminal_event(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "terminal-before-refresh-save"
    estimate = _accepted_estimate(item_name="Already accepted", calories=300)
    payload = {
        "status": "pending_review",
        "meal_id": meal_id,
        "items": [
            {
                "item_id": "item-1",
                "status": "included",
                "estimate": estimate,
                "original_estimate": estimate,
            },
        ],
    }
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload=payload,
        next_item_seq=2,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="discarded",
        included_client_ids=[],
        skipped_count=1,
        deleted_count=0,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "stale-discarded-child",
            "meal_id": meal_id,
            "meal_item_id": "item-1",
            "item_index": 0,
            "item_state": "included",
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )

    saved = module._review_save_snapshot(1, meal_id, payload, 2, {}, sync_pending=True)

    assert data_store.get_food_log_by_client_id(1, meal_id) is None
    assert data_store.get_food_log_by_client_id(1, "stale-discarded-child") is None
    assert data_store.get_meal_review_snapshot(1, meal_id) is None
    assert saved is module._REVIEW_TERMINAL_SAVE_CONFLICT
    with module.app.test_request_context():
        response = module._review_saved_payload_response(
            saved,
            user_id=1,
            meal_id=meal_id,
            payload=payload,
        )
    assert response.status_code == 200
    assert response.get_json()["status"] == "discarded"


def test_review_snapshot_save_does_not_reopen_logged_event_with_missing_rows(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "logged-event-missing-rows"
    estimate = _accepted_estimate(item_name="Missing accepted child", calories=300)
    payload = {
        "status": "pending_review",
        "meal_id": meal_id,
        "items": [
            {
                "item_id": "item-1",
                "status": "included",
                "estimate": estimate,
                "original_estimate": estimate,
            },
        ],
    }
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="logged",
        included_client_ids=["missing-accepted-child"],
        skipped_count=0,
        deleted_count=0,
    )

    saved = module._review_save_snapshot(
        1,
        meal_id,
        payload,
        2,
        {},
        sync_pending=True,
    )

    assert saved is module._REVIEW_TERMINAL_SAVE_CONFLICT
    assert data_store.get_food_log_by_client_id(1, meal_id) is None
    assert data_store.get_meal_review_snapshot(1, meal_id) is None


def test_review_snapshot_save_preserves_recoverable_partial_child_rows(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "partial-review-save"
    accepted = _accepted_estimate(item_name="Accepted child", calories=300)
    pending = _accepted_estimate(item_name="Pending child", calories=200)
    for index, (client_id, estimate, state) in enumerate(
        (
            ("partial-review-accepted", accepted, "accepted"),
            ("partial-review-pending", pending, "pending_review"),
        )
    ):
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "meal_id": meal_id,
                "meal_item_id": f"item-{index + 1}",
                "item_index": index,
                "item_state": "included",
                "date": "2026-07-13",
                "logged_at": "2026-07-13T12:00:00",
                **estimate,
                "correction_state": state,
                "original_estimate": estimate,
                "accepted_estimate": estimate if state == "accepted" else None,
            },
        )
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="logged",
        included_client_ids=["partial-review-accepted", "partial-review-pending"],
        skipped_count=0,
        deleted_count=0,
    )
    payload = {
        "status": "pending_review",
        "meal_id": meal_id,
        "items": [
            {
                "item_id": "item-2",
                "status": "included",
                "estimate": pending,
                "original_estimate": pending,
            },
        ],
    }

    saved = module._review_save_snapshot(
        1,
        meal_id,
        payload,
        3,
        {},
        sync_pending=True,
    )

    assert saved is not module._REVIEW_TERMINAL_SAVE_CONFLICT
    assert saved["status"] == "pending_review"
    assert data_store.get_meal_review_snapshot(1, meal_id) is not None
    assert data_store.get_food_log_by_client_id(1, "partial-review-pending") is not None


def test_review_snapshot_save_preserves_missing_child_without_event(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "partial-review-missing-child"
    accepted = _accepted_estimate(item_name="Accepted child", calories=300)
    missing = _accepted_estimate(item_name="Missing child", calories=200)
    data_store.add_food_log(
        1,
        {
            "client_id": "partial-missing-accepted",
            "meal_id": meal_id,
            "meal_item_id": "item-1",
            "item_index": 0,
            "item_state": "included",
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            **accepted,
            "correction_state": "accepted",
            "original_estimate": accepted,
            "accepted_estimate": accepted,
        },
    )
    payload = {
        "status": "pending_review",
        "meal_id": meal_id,
        "items": [
            {
                "item_id": "item-1",
                "status": "included",
                "estimate": accepted,
                "original_estimate": accepted,
            },
            {
                "item_id": "item-2",
                "status": "included",
                "estimate": missing,
                "original_estimate": missing,
            },
        ],
    }

    saved = module._review_save_snapshot(
        1,
        meal_id,
        payload,
        3,
        {},
        sync_pending=True,
    )

    assert saved is not module._REVIEW_TERMINAL_SAVE_CONFLICT
    assert data_store.get_meal_review_snapshot(1, meal_id) is not None


def test_review_terminal_conflict_ignores_imported_payload_image_claim(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "terminal-imported-image-conflict"
    estimate = _accepted_estimate(
        item_name="Canonical manual meal",
        calories=300,
        source="manual_review_estimate",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            **estimate,
            "correction_state": "accepted",
            "original_estimate": estimate,
            "accepted_estimate": estimate,
        },
    )
    payload = {
        "status": "pending_review",
        "meal_id": meal_id,
        "has_image": True,
        "_imported_snapshot_untrusted": True,
    }

    with module.app.test_request_context():
        response = module._review_saved_payload_response(
            module._REVIEW_TERMINAL_SAVE_CONFLICT,
            user_id=1,
            meal_id=meal_id,
            payload=payload,
        )

    assert response.status_code == 200
    assert response.get_json()["status"] == "logged"
    assert response.get_json()["photo_retention"]["image_received"] is False


def test_review_terminal_conflict_preserves_canonical_row_image_provenance(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "terminal-canonical-image-conflict"
    estimate = _accepted_estimate(
        item_name="Canonical photo meal",
        calories=300,
        source="nutritionix",
        from_image=True,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            **estimate,
            "correction_state": "accepted",
            "original_estimate": estimate,
            "accepted_estimate": estimate,
        },
    )

    with module.app.test_request_context():
        response = module._review_saved_payload_response(
            module._REVIEW_TERMINAL_SAVE_CONFLICT,
            user_id=1,
            meal_id=meal_id,
            payload={"status": "pending_review", "meal_id": meal_id},
        )

    assert response.status_code == 200
    assert response.get_json()["photo_retention"]["image_received"] is True


def test_exact_terminal_replay_revalidates_rows_inside_transaction(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "terminal-revalidate-parent"
    meal_id = "terminal-revalidate-meal"
    estimate = _accepted_estimate(item_name="Vanishing terminal row", calories=300)
    item_client_id = module._meal_item_client_id(
        parent_client_id,
        {"item_id": "item-1"},
        0,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": item_client_id,
            "meal_id": meal_id,
            "meal_item_id": "item-1",
            "item_index": 0,
            "item_state": "included",
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            **estimate,
            "correction_state": "accepted",
            "original_estimate": estimate,
            "accepted_estimate": estimate,
        },
    )
    original_get_rows = module.data_store_module.get_food_logs_by_meal_id

    def rows_disappear_under_lock(user_id, requested_meal_id, *, _conn=None):
        if _conn is not None and requested_meal_id == meal_id:
            return []
        return original_get_rows(user_id, requested_meal_id, _conn=_conn)

    monkeypatch.setattr(
        module.data_store_module,
        "get_food_logs_by_meal_id",
        rows_disappear_under_lock,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "item-1", "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)


def test_pending_multi_recovery_allows_skipping_one_pending_item(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-skip-parent"
    meal_id = "pending-skip-meal"
    keep_estimate = _accepted_estimate(item_name="Keep pending item", calories=300)
    skip_estimate = _accepted_estimate(
        item_name="Skip pending item",
        calories=200,
        source="nutritionix",
        from_image=True,
    )
    keep_id = module._meal_item_client_id(parent_client_id, {"item_id": "keep"}, 0)
    skip_id = module._meal_item_client_id(parent_client_id, {"item_id": "skip"}, 1)
    for client_id, item_id, index, estimate in (
        (keep_id, "keep", 0, keep_estimate),
        (skip_id, "skip", 1, skip_estimate),
    ):
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "meal_id": meal_id,
                "meal_item_id": item_id,
                "item_index": index,
                "item_state": "included",
                "date": "2026-07-13",
                "logged_at": "2026-07-13T12:00:00",
                **estimate,
                "correction_state": "pending_review",
                "original_estimate": estimate,
            },
        )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "keep", "estimate": keep_estimate},
                {
                    "state": "skipped",
                    "item_id": "skip",
                    "text": "Skip pending item",
                    "estimate": skip_estimate,
                },
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert [row["client_id"] for row in data_store.get_food_logs_by_meal_id(1, meal_id)] == [keep_id]
    assert data_store.get_meal_acceptance_event(1, meal_id)["included_client_ids"] == [keep_id]
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is True
    assert response.get_json()["photo_retention"]["image_received"] is True


def test_pending_multi_recovery_full_discard_removes_child_rows(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-child-discard-parent"
    meal_id = "pending-child-discard-meal"
    items = []
    for index, item_id in enumerate(("skip", "delete")):
        estimate = _accepted_estimate(
            item_name=f"Pending {item_id}",
            calories=200 + index * 100,
        )
        client_id = module._meal_item_client_id(
            parent_client_id,
            {"item_id": item_id},
            index,
        )
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "meal_id": meal_id,
                "meal_item_id": item_id,
                "item_index": index,
                "item_state": "included",
                "date": "2026-07-13",
                "logged_at": "2026-07-13T12:00:00",
                **estimate,
                "correction_state": "pending_review",
                "original_estimate": estimate,
            },
        )
        items.append(
            {
                "state": "skipped" if index == 0 else "deleted",
                "item_id": item_id,
                "text": estimate["item_name"],
                "estimate": estimate,
            }
        )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={"meal_id": meal_id, "items": items},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["status"] == "discarded"
    assert data_store.get_food_logs_by_meal_id(1, meal_id) == []


def test_skipped_pending_row_refresh_race_is_rejected_before_delete(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-skip-stale-parent"
    meal_id = "pending-skip-stale-meal"
    estimate = _accepted_estimate(item_name="Original pending item", calories=200)
    keep_estimate = _accepted_estimate(item_name="Keep pending item", calories=300)
    client_id = module._meal_item_client_id(
        parent_client_id,
        {"item_id": "skip"},
        0,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "meal_id": meal_id,
            "meal_item_id": "skip",
            "item_index": 0,
            "item_state": "included",
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )
    keep_id = module._meal_item_client_id(
        parent_client_id,
        {"item_id": "keep"},
        1,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": keep_id,
            "meal_id": meal_id,
            "meal_item_id": "keep",
            "item_index": 1,
            "item_state": "included",
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            **keep_estimate,
            "correction_state": "pending_review",
            "original_estimate": keep_estimate,
        },
    )
    original_get_rows = module.data_store_module.get_food_logs_by_meal_id

    def refreshed_rows_under_lock(user_id, requested_meal_id, *, _conn=None):
        rows = original_get_rows(user_id, requested_meal_id, _conn=_conn)
        if _conn is not None and requested_meal_id == meal_id and rows:
            changed = dict(rows[0])
            changed["calories"] = 250
            return [changed]
        return rows

    monkeypatch.setattr(
        module.data_store_module,
        "get_food_logs_by_meal_id",
        refreshed_rows_under_lock,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "skipped",
                    "item_id": "skip",
                    "text": "Original pending item",
                    "estimate": estimate,
                },
                {
                    "state": "included",
                    "item_id": "keep",
                    "estimate": keep_estimate,
                },
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert data_store.get_food_log_by_client_id(1, client_id) is not None


def test_source_backed_refresh_cannot_restore_imported_snapshot_image_claim(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "imported-image-then-source-refresh"
    imported = _accepted_estimate(
        item_name="Imported photo claim",
        calories=300,
        source="vision_forged",
        from_image=True,
        external_food_id="forged-imported-provider-id",
        verified_source_url="https://example.invalid/forged-imported-provider",
        brand_id="forged-imported-brand",
    )
    refreshed = _accepted_estimate(
        item_name="Verified refreshed item",
        calories=320,
        source="nutritionix",
        external_food_id="nutritionix:verified-refresh",
        verified_source_url="https://www.nutritionix.com/food/verified-refresh",
    )
    monkeypatch.setattr(
        module,
        "parse_meal_text",
        lambda *_args, **_kwargs: {"estimate": refreshed, "fallback_used": False},
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={
            "status": "pending_review",
            "meal_id": meal_id,
            "has_image": True,
            "_imported_snapshot_untrusted": True,
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "text": "Imported photo claim",
                    "estimate": imported,
                    "original_estimate": imported,
                    "_imported_snapshot_untrusted": True,
                },
            ],
        },
        next_item_seq=2,
    )
    client = module.app.test_client()
    updated = client.post(
        f"/api/meal-intake/{meal_id}/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "verified-source-refresh",
            "item_id": "item-1",
            "text": "verified refreshed item",
        },
    )
    assert updated.status_code == 200, updated.get_data(as_text=True)

    accepted = client.post(
        f"/api/meal-intake/{meal_id}/accept",
        json={},
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    body = accepted.get_json()
    assert body["food_logs"][0]["accepted_estimate"]["source"] == "nutritionix"
    assert body["photo_retention"]["image_received"] is False
    stored_original = body["food_logs"][0]["original_estimate"]
    assert stored_original.get("from_image") is not True
    assert stored_original["source"] == "manual_review_estimate"
    for field in ("external_food_id", "verified_source_url", "brand_id"):
        assert field not in stored_original


def test_imported_source_candidate_cannot_copy_untrusted_original_image(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "imported-source-candidate-image"
    original = _accepted_estimate(
        item_name="Imported photo claim",
        calories=300,
        source="vision_forged",
        from_image=True,
    )
    refreshed = _accepted_estimate(
        item_name="Verified refreshed item",
        calories=320,
        source="nutritionix",
        external_food_id="nutritionix:verified-refresh",
        verified_source_url="https://www.nutritionix.com/food/verified-refresh",
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={
            "status": "pending_review",
            "meal_id": meal_id,
            "has_image": True,
            "_imported_snapshot_untrusted": True,
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "text": "Verified refreshed item",
                    "estimate": refreshed,
                    "original_estimate": original,
                    "server_source_backed_candidate": True,
                    "_imported_snapshot_untrusted": True,
                },
            ],
        },
        next_item_seq=2,
    )

    accepted = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={},
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    body = accepted.get_json()
    assert body["food_logs"][0]["accepted_estimate"]["source"] == "nutritionix"
    assert body["food_logs"][0].get("from_image") is not True
    assert body["food_logs"][0]["accepted_estimate"].get("from_image") is not True
    assert body["photo_retention"]["image_received"] is False
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is False


def test_imported_snapshot_sync_marks_pending_row_untrusted_for_cross_meal_accept(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    route_meal_id = "imported-sync-route-meal"
    accepted_meal_id = "imported-sync-different-meal"
    forged = _accepted_estimate(
        item_name="Imported forged photo",
        calories=320,
        source="vision_forged",
        from_image=True,
        external_food_id="forged-imported-provider",
        verified_source_url="https://example.invalid/forged-imported-provider",
    )
    monkeypatch.setattr(
        module,
        "parse_meal_text",
        lambda *_args, **_kwargs: {"estimate": forged, "fallback_used": False},
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id=route_meal_id,
        payload={
            "status": "pending_review",
            "meal_id": route_meal_id,
            "has_image": True,
            "_imported_snapshot_untrusted": True,
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "text": "Imported forged photo",
                    "estimate": forged,
                    "original_estimate": forged,
                    "_imported_snapshot_untrusted": True,
                },
            ],
        },
        next_item_seq=2,
    )
    client = module.app.test_client()
    refreshed = client.post(
        f"/api/meal-intake/{route_meal_id}/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "sync-imported-pending-row",
            "item_id": "item-1",
            "text": "Imported forged photo",
        },
    )
    assert refreshed.status_code == 200, refreshed.get_data(as_text=True)
    pending_row = data_store.get_food_log_by_client_id(1, route_meal_id)
    assert pending_row["original_estimate"]["_imported_pending_untrusted"] is True

    rejected = client.post(
        f"/api/meal-intake/{route_meal_id}/accept",
        json={
            "meal_id": accepted_meal_id,
            "items": [
                {"state": "included", "item_id": "item-1", "estimate": forged},
            ],
        },
    )

    assert rejected.status_code == 400, rejected.get_data(as_text=True)
    assert rejected.get_json()["error"]["code"] == "invalid_field"
    assert data_store.get_meal_acceptance_event(1, accepted_meal_id) is None
    assert data_store.get_food_log_by_client_id(1, route_meal_id) is not None
    assert data_store.get_meal_review_snapshot(1, route_meal_id) is not None


def test_cross_meal_full_discard_rejects_pending_route_alias(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    route_meal_id = "discard-route-alias"
    accepted_meal_id = "discard-terminal-meal"
    estimate = _accepted_estimate(item_name="Empty plate", calories=10)
    data_store.add_food_log(
        1,
        {
            "client_id": route_meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id=route_meal_id,
        payload={
            "status": "pending_review",
            "meal_id": route_meal_id,
            "items": [
                {
                    "item_id": "item-1",
                    "status": "included",
                    "estimate": estimate,
                    "original_estimate": estimate,
                },
            ],
        },
        next_item_seq=2,
    )

    discarded = module.app.test_client().post(
        f"/api/meal-intake/{route_meal_id}/accept",
        json={
            "meal_id": accepted_meal_id,
            "items": [
                {"state": "skipped", "item_id": "item-1", "estimate": estimate},
            ],
        },
    )

    assert discarded.status_code == 400, discarded.get_data(as_text=True)
    assert discarded.get_json()["error"]["code"] == "invalid_field"
    assert data_store.get_food_log_by_client_id(1, route_meal_id) is not None
    assert data_store.get_meal_review_snapshot(1, route_meal_id) is not None


def test_cross_meal_accept_rejects_pending_child_owned_by_another_meal(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "shared-pending-child-parent"
    original_meal_id = "pending-child-original-meal"
    requested_meal_id = "pending-child-requested-meal"
    item_id = "item-1"
    estimate = _accepted_estimate(item_name="Original pending child", calories=300)
    client_id = module._meal_item_client_id(
        parent_client_id,
        {"item_id": item_id},
        0,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "meal_id": original_meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": requested_meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "duplicate_client_id"
    stored = data_store.get_food_log_by_client_id(1, client_id)
    assert stored["meal_id"] == original_meal_id
    assert stored["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, requested_meal_id) is None


@pytest.mark.parametrize("terminal_replay", [True, False])
def test_cross_meal_terminal_paths_reject_pending_route_alias(
    monkeypatch,
    tmp_path,
    terminal_replay,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from contextlib import contextmanager

    route_meal_id = f"route-alias-race-{terminal_replay}"
    accepted_meal_id = f"target-alias-race-{terminal_replay}"
    item_id = "item-1"
    child_id = module._meal_item_client_id(route_meal_id, {"item_id": item_id}, 0)
    original = _accepted_estimate(item_name="Original route review", calories=300)
    refreshed = _accepted_estimate(item_name="Refreshed route review", calories=450)

    def snapshot_payload(estimate):
        return {
            "status": "pending_review",
            "meal_id": route_meal_id,
            "items": [
                {
                    "item_id": item_id,
                    "status": "included",
                    "estimate": estimate,
                    "original_estimate": estimate,
                },
            ],
        }

    data_store.add_food_log(
        1,
        {
            "client_id": route_meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **original,
            "correction_state": "pending_review",
            "original_estimate": original,
        },
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id=route_meal_id,
        payload=snapshot_payload(original),
        next_item_seq=2,
    )
    if terminal_replay:
        data_store.add_food_log(
            1,
            {
                "client_id": child_id,
                "meal_id": accepted_meal_id,
                "meal_item_id": item_id,
                "item_index": 0,
                "item_state": "included",
                "date": "2026-05-22",
                "logged_at": "2026-05-22T12:00:00",
                **original,
                "correction_state": "accepted",
                "original_estimate": original,
                "accepted_estimate": original,
            },
        )
        data_store.save_meal_acceptance_event(
            1,
            meal_id=accepted_meal_id,
            status="logged",
            included_client_ids=[child_id],
            skipped_count=0,
            deleted_count=0,
        )
    original_transaction = module.food_log_transaction
    changed = False

    @contextmanager
    def refresh_route_before_lock():
        nonlocal changed
        if not changed:
            changed = True
            data_store.add_food_log(
                1,
                {
                    "client_id": route_meal_id,
                    "date": "2026-05-22",
                    "logged_at": "2026-05-22T12:01:00",
                    **refreshed,
                    "correction_state": "pending_review",
                    "original_estimate": refreshed,
                },
            )
            data_store.save_meal_review_snapshot(
                1,
                meal_id=route_meal_id,
                payload=snapshot_payload(refreshed),
                next_item_seq=2,
            )
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(module, "food_log_transaction", refresh_route_before_lock)
    item = {
        "state": "included" if terminal_replay else "skipped",
        "item_id": item_id,
        "estimate": original,
    }
    response = module.app.test_client().post(
        f"/api/meal-intake/{route_meal_id}/accept",
        json={"meal_id": accepted_meal_id, "items": [item]},
    )

    assert response.status_code == 400, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "invalid_field"
    stored = data_store.get_food_log_by_client_id(1, route_meal_id)
    assert stored["item_name"] == "Original route review"
    snapshot = data_store.get_meal_review_snapshot(1, route_meal_id)
    assert snapshot["payload"]["items"][0]["estimate"]["calories"] == 300


def test_logged_child_event_does_not_downgrade_unrelated_direct_terminal_row(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "logged-child-with-direct-row"
    child_id = "logged-child-row"
    child_estimate = _accepted_estimate(item_name="Meal child", calories=300)
    direct_estimate = _accepted_estimate(item_name="Independent direct log", calories=500)
    payload = {
        "status": "pending_review",
        "meal_id": meal_id,
        "items": [
            {
                "item_id": "child",
                "status": "included",
                "estimate": child_estimate,
                "original_estimate": child_estimate,
            },
        ],
    }
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload=payload,
        next_item_seq=2,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": child_id,
            "meal_id": meal_id,
            "meal_item_id": "child",
            "item_index": 0,
            "item_state": "included",
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **child_estimate,
            "correction_state": "accepted",
            "original_estimate": child_estimate,
            "accepted_estimate": child_estimate,
        },
    )
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T11:00:00",
            **direct_estimate,
            "correction_state": "accepted",
            "original_estimate": direct_estimate,
            "accepted_estimate": direct_estimate,
        },
    )
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="logged",
        included_client_ids=[child_id],
        skipped_count=0,
        deleted_count=0,
    )

    saved = module._review_save_snapshot(1, meal_id, payload, 2, {})

    assert saved is module._REVIEW_TERMINAL_SAVE_CONFLICT
    direct = data_store.get_food_log_by_client_id(1, meal_id)
    assert direct["correction_state"] == "accepted"
    assert direct["item_name"] == "Independent direct log"
    assert data_store.get_meal_review_snapshot(1, meal_id) is None
    with module.app.test_request_context():
        replay = module._review_saved_payload_response(
            saved,
            user_id=1,
            meal_id=meal_id,
            payload=payload,
        )
    replay_body = replay.get_json()
    assert replay_body["status"] == "logged"
    assert replay_body["meal_id"] == meal_id
    assert [row["client_id"] for row in replay_body["food_logs"]] == [child_id]
    assert "food_log" not in replay_body


def test_complete_eventless_child_rows_repair_event_before_terminal_replay(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "complete-eventless-terminal-rows"
    rows = []
    items = []
    for index, calories in enumerate((200, 300), start=1):
        item_id = f"item-{index}"
        client_id = f"eventless-child-{index}"
        estimate = _accepted_estimate(item_name=f"Child {index}", calories=calories)
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "meal_id": meal_id,
                "meal_item_id": item_id,
                "item_index": index - 1,
                "item_state": "included",
                "date": "2026-05-22",
                "logged_at": "2026-05-22T12:00:00",
                **estimate,
                "correction_state": "accepted",
                "original_estimate": estimate,
                "accepted_estimate": estimate,
            },
        )
        rows.append(client_id)
        items.append(
            {
                "item_id": item_id,
                "status": "included",
                "estimate": estimate,
                "original_estimate": estimate,
            }
        )
    payload = {"status": "pending_review", "meal_id": meal_id, "items": items}
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload=payload,
        next_item_seq=3,
    )
    aggregate = _accepted_estimate(item_name="Pending aggregate", calories=500)
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **aggregate,
            "correction_state": "pending_review",
            "original_estimate": aggregate,
        },
    )

    saved = module._review_save_snapshot(1, meal_id, payload, 3, {})

    assert saved is module._REVIEW_TERMINAL_SAVE_CONFLICT
    event = data_store.get_meal_acceptance_event(1, meal_id)
    assert event["status"] == "logged"
    assert set(event["included_client_ids"]) == set(rows)
    assert data_store.get_food_log_by_client_id(1, meal_id) is None
    with module.app.test_request_context():
        replay = module._review_saved_payload_response(
            saved,
            user_id=1,
            meal_id=meal_id,
            payload=payload,
        )
    assert replay.status_code == 200
    assert [row["client_id"] for row in replay.get_json()["food_logs"]] == rows


def test_direct_terminal_row_with_own_logged_event_is_canonical(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "direct-terminal-own-event"
    estimate = _accepted_estimate(item_name="Direct terminal meal", calories=400)
    payload = {
        "status": "pending_review",
        "meal_id": meal_id,
        "items": [
            {
                "item_id": "item-1",
                "status": "included",
                "estimate": estimate,
                "original_estimate": estimate,
            },
        ],
    }
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload=payload,
        next_item_seq=2,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "accepted",
            "original_estimate": estimate,
            "accepted_estimate": estimate,
        },
    )
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="logged",
        included_client_ids=[meal_id],
        skipped_count=0,
        deleted_count=0,
    )

    saved = module._review_save_snapshot(1, meal_id, payload, 2, {})

    assert saved is module._REVIEW_TERMINAL_SAVE_CONFLICT
    direct = data_store.get_food_log_by_client_id(1, meal_id)
    assert direct["correction_state"] == "accepted"
    assert data_store.get_meal_review_snapshot(1, meal_id) is None


def test_protected_fresh_accept_conflict_does_not_enqueue_existing_row(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "protected-fresh-conflict-parent"
    meal_id = "protected-fresh-conflict-meal"
    estimate = _accepted_estimate(item_name="Concurrent winner", calories=300)
    item_client_id = module._meal_item_client_id(
        parent_client_id,
        {"item_id": "item-1"},
        0,
    )
    canonical_row = {
        **estimate,
        "client_id": item_client_id,
        "meal_id": meal_id,
        "meal_item_id": "item-1",
        "item_index": 0,
        "item_state": "included",
        "correction_state": "accepted",
        "accepted_estimate": estimate,
        "original_estimate": estimate,
        "_protected_client_id_conflict": True,
    }
    monkeypatch.setattr(
        module,
        "_meal_intake_persist",
        lambda *_args, **_kwargs: dict(canonical_row),
    )
    original_get_event = module.data_store_module.get_meal_acceptance_event

    def transaction_only_event(user_id, requested_meal_id, *, _conn=None):
        if _conn is not None and requested_meal_id == meal_id:
            return {
                "meal_id": meal_id,
                "status": "logged",
                "included_client_ids": [item_client_id],
                "skipped_count": 0,
                "deleted_count": 0,
                "feedback_fingerprint": None,
                "has_image": False,
            }
        return original_get_event(user_id, requested_meal_id, _conn=_conn)

    monkeypatch.setattr(
        module.data_store_module,
        "get_meal_acceptance_event",
        transaction_only_event,
    )
    monkeypatch.setattr(module, "claim_food_log_vocab_learning", lambda *_a, **_kw: False)
    enqueued = []
    monkeypatch.setattr(
        module,
        "_enqueue_workout_adaptation_after_accept",
        lambda user_id, rows: enqueued.append((user_id, rows)),
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "item-1", "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert enqueued == []


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
    changed_estimate = dict(payload)
    changed_estimate["items"] = [
        {"state": "included", "item_id": "a", "estimate": _accepted_estimate(item_name="A", calories=100)},
        {"state": "included", "item_id": "b", "estimate": _accepted_estimate(item_name="B", calories=900)},
        {"state": "skipped", "text": "plate", "estimate": _accepted_estimate(item_name="Plate", calories=10)},
    ]
    estimate_conflict = client.post(
        "/api/meal-intake/photo-parent-idem/accept",
        json=changed_estimate,
    )
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
    assert estimate_conflict.status_code == 409
    assert conflict.status_code == 409
    assert feedback_conflict.status_code == 409
    rows = data_store.get_food_logs(1)
    assert len(rows) == 2
    assert retry.get_json()["meal_totals"]["calories"] == 300
    entry = data_store.get_personal_vocab_entry(1, "plate")
    assert entry["skip_count"] == 1
    assert data_store.get_personal_vocab_entry(1, "bowl") is None


def test_multi_item_retry_rejects_request_asserted_image_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    payload = {
        "meal_id": "text-meal-idem",
        "items": [
            {
                "state": "included",
                "item_id": "a",
                "estimate": _accepted_estimate(item_name="A", calories=100),
            },
        ],
    }
    first = client.post("/api/meal-intake/text-parent-idem/accept", json=payload)
    asserted_image = {
        "meal_id": payload["meal_id"],
        "items": [
            {
                "state": "included",
                "item_id": "a",
                "estimate": _accepted_estimate(item_name="A", calories=100, from_image=True),
            },
        ],
    }

    retry = client.post("/api/meal-intake/text-parent-idem/accept", json=asserted_image)

    assert first.status_code == 200, first.get_data(as_text=True)
    assert retry.status_code == 200, retry.get_data(as_text=True)
    assert retry.get_json()["photo_retention"]["image_received"] is False


def test_multi_item_accept_queries_only_target_meal_inside_transaction(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    original_get_food_logs = data_store.get_food_logs

    def reject_locked_full_history_scan(*args, **kwargs):
        if kwargs.get("_conn") is not None:
            raise AssertionError("transactional accept scanned the user's full food-log history")
        return original_get_food_logs(*args, **kwargs)

    monkeypatch.setattr(data_store, "get_food_logs", reject_locked_full_history_scan)

    response = module.app.test_client().post(
        "/api/meal-intake/targeted-query-parent/accept",
        json={
            "meal_id": "targeted-query-meal",
            "items": [
                {
                    "state": "included",
                    "item_id": "a",
                    "estimate": _accepted_estimate(item_name="A", calories=100),
                },
            ],
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)


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
    partial_estimate = _accepted_estimate(item_name="A", calories=100)
    data_store.add_food_log(
        1,
        {
            "client_id": partial_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **partial_estimate,
            "correction_state": "accepted",
            "accepted_estimate": partial_estimate,
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
    assert all(row["vocab_learned_at"] is not None for row in rows)
    event = data_store.get_meal_acceptance_event(1, "photo-meal-partial")
    assert set(event["included_client_ids"]) == {row["client_id"] for row in rows}


def test_partial_corrected_row_recovery_records_correction_learning(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    accepted_calls = []
    corrected_calls = []
    monkeypatch.setattr(
        module.personal_vocab,
        "record_accept",
        lambda *args, **_kwargs: accepted_calls.append(args),
    )
    monkeypatch.setattr(
        module.personal_vocab,
        "record_correct",
        lambda *args, **_kwargs: corrected_calls.append(args),
    )
    parent_client_id = "photo-parent-partial-corrected"
    partial_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "a"}, 0)
    original = _accepted_estimate(item_name="A", calories=90)
    corrected = _accepted_estimate(item_name="A", calories=100)
    data_store.add_food_log(
        1,
        {
            "client_id": partial_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **corrected,
            "correction_state": "corrected",
            "original_estimate": original,
            "accepted_estimate": corrected,
            "meal_id": "photo-meal-partial-corrected",
            "meal_item_id": "a",
            "item_index": 0,
            "item_state": "included",
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": "photo-meal-partial-corrected",
            "items": [
                {"state": "included", "item_id": "a", "estimate": corrected},
                {"state": "included", "item_id": "b", "estimate": _accepted_estimate(item_name="B", calories=200)},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert len(corrected_calls) == 1
    assert len(accepted_calls) == 1
    stored = next(row for row in data_store.get_food_logs(1) if row["client_id"] == partial_client_id)
    assert stored["correction_state"] == "corrected"
    assert stored["original_estimate"] == original


def test_partial_protected_row_learning_uses_stored_phrase_and_estimate(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    accepted_calls = []
    monkeypatch.setattr(
        module.personal_vocab,
        "record_accept",
        lambda *args, **_kwargs: accepted_calls.append(args),
    )
    parent_client_id = "photo-parent-partial-canonical-learning"
    partial_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "a"}, 0)
    stored_estimate = _accepted_estimate(item_name="Stored bowl", calories=100)
    data_store.add_food_log(
        1,
        {
            "client_id": partial_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **stored_estimate,
            "context_note": "stored bowl phrase",
            "correction_state": "accepted",
            "accepted_estimate": stored_estimate,
            "meal_id": "photo-meal-partial-canonical-learning",
            "meal_item_id": "a",
            "item_index": 0,
            "item_state": "included",
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": "photo-meal-partial-canonical-learning",
            "items": [
                {
                    "state": "included",
                    "item_id": "a",
                    "text": "attacker-controlled phrase",
                    "estimate": {**stored_estimate, "confidence": 0.01},
                },
                {"state": "included", "item_id": "b", "estimate": _accepted_estimate(item_name="B", calories=200)},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    recovered_call = next(call for call in accepted_calls if call[2]["item_name"] == "Stored bowl")
    assert recovered_call[1] == "stored bowl phrase"
    assert recovered_call[2]["confidence"] == stored_estimate["confidence"]


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


def test_partial_event_recovery_preserves_canonical_image_and_enqueues_only_new_row(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "partial-event-photo-parent"
    meal_id = "partial-event-photo-meal"
    item_a_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "a"}, 0)
    item_b_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "b"}, 1)
    estimate_a = _accepted_estimate(
        item_name="Photo A",
        calories=100,
        source="vision_estimate",
        from_image=True,
    )
    estimate_b = _accepted_estimate(item_name="Text B", calories=200)
    data_store.add_food_log(
        1,
        {
            "client_id": item_a_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate_a,
            "correction_state": "accepted",
            "original_estimate": estimate_a,
            "accepted_estimate": estimate_a,
            "meal_id": meal_id,
            "meal_item_id": "a",
            "item_index": 0,
            "item_state": "included",
        },
    )
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="logged",
        included_client_ids=[item_a_client_id, item_b_client_id],
        skipped_count=0,
        deleted_count=0,
        has_image=False,
    )
    enqueued = []
    monkeypatch.setattr(
        module,
        "_enqueue_workout_adaptation_after_accept",
        lambda user_id, rows: enqueued.append((user_id, rows)),
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "a", "estimate": estimate_a},
                {"state": "included", "item_id": "b", "estimate": estimate_b},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is True
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is True
    assert len(enqueued) == 1
    assert [row["client_id"] for row in enqueued[0][1]] == [item_b_client_id]


def test_terminal_replays_preserve_unknown_feedback_fingerprints(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    parent_client_id = "unknown-feedback-parent"
    logged_meal_id = "unknown-feedback-logged"
    item_id = "meal"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    estimate = _accepted_estimate(item_name="Logged meal", calories=500)
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "accepted",
            "accepted_estimate": estimate,
            "meal_id": logged_meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )
    data_store.save_meal_acceptance_event(
        1,
        meal_id=logged_meal_id,
        status="logged",
        included_client_ids=[client_id],
        skipped_count=1,
        deleted_count=0,
        feedback_fingerprint=None,
    )

    logged_response = client.post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": logged_meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
                {
                    "state": "skipped",
                    "item_id": "tampered-skip",
                    "text": "tampered replay",
                    "estimate": _accepted_estimate(item_name="Tampered", calories=10),
                },
            ],
        },
    )

    discarded_meal_id = "unknown-feedback-discarded"
    data_store.save_meal_acceptance_event(
        1,
        meal_id=discarded_meal_id,
        status="discarded",
        included_client_ids=[],
        skipped_count=1,
        deleted_count=0,
        feedback_fingerprint=None,
    )
    discarded_response = client.post(
        f"/api/meal-intake/{discarded_meal_id}/accept",
        json={
            "meal_id": discarded_meal_id,
            "items": [
                {
                    "state": "skipped",
                    "item_id": "tampered-discard",
                    "text": "tampered discard replay",
                    "estimate": _accepted_estimate(item_name="Tampered", calories=10),
                },
            ],
        },
    )

    assert logged_response.status_code == 200, logged_response.get_data(as_text=True)
    assert discarded_response.status_code == 200, discarded_response.get_data(as_text=True)
    assert data_store.get_meal_acceptance_event(1, logged_meal_id)["feedback_fingerprint"] is None
    assert data_store.get_meal_acceptance_event(1, discarded_meal_id)["feedback_fingerprint"] is None


def test_multi_item_exact_set_pending_replay_finalizes_rows(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-parent-exact-replay"
    meal_id = "pending-meal-exact-replay"
    estimates = [
        _accepted_estimate(item_name="A", calories=100),
        _accepted_estimate(item_name="B", calories=200),
    ]
    for index, estimate in enumerate(estimates):
        item_id = chr(ord("a") + index)
        client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, index)
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "date": "2026-05-22",
                "logged_at": "2026-05-22T12:00:00",
                **estimate,
                "correction_state": "pending_review",
                "original_estimate": estimate,
                "meal_id": meal_id,
                "meal_item_id": item_id,
                "item_index": index,
                "item_state": "included",
            },
        )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "a", "estimate": estimates[0]},
                {"state": "included", "item_id": "b", "estimate": estimates[1]},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    rows = [row for row in data_store.get_food_logs(1) if row["meal_id"] == meal_id]
    assert len(rows) == 2
    assert {row["correction_state"] for row in rows} <= {"accepted", "corrected"}
    assert data_store.get_meal_acceptance_event(1, meal_id)["status"] == "logged"


def test_multi_item_pending_replay_rechecks_stored_placeholder_policy(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-placeholder-parent"
    meal_id = "pending-placeholder-meal"
    item_id = "barcode"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    placeholder = _accepted_estimate(
        item_name="Unknown barcode",
        calories=0,
        protein_g=0,
        carbs_g=0,
        fat_g=0,
        source="barcode_pending_source",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **placeholder,
            "correction_state": "pending_review",
            "original_estimate": placeholder,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": placeholder},
            ],
        },
    )

    assert response.status_code == 422, response.get_data(as_text=True)
    stored = data_store.get_food_logs_by_meal_id(1, meal_id)
    assert stored[0]["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, meal_id) is None


def test_snapshotless_pending_replay_accepts_unchanged_low_confidence_row(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-low-confidence-parent"
    meal_id = "pending-low-confidence-meal"
    item_id = "uncertain"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    estimate = _accepted_estimate(item_name="Uncertain meal", calories=300)
    estimate.update(
        confidence=0.4,
        ambiguous=True,
        uncertainty_notes=["Portion needs confirmation."],
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    stored = data_store.get_food_log_by_client_id(1, client_id)
    assert stored["correction_state"] == "accepted"


def test_snapshotless_pending_ai_only_combo_uses_stored_baseline(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-stored-combo-parent"
    meal_id = "pending-stored-combo-meal"
    item_id = "combo"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    stored = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **stored,
            "context_note": None,
            "correction_state": "pending_review",
            "original_estimate": stored,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )
    renamed = dict(stored, item_name="Chicken dinner", source="manual_review_estimate")

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": renamed},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert data_store.get_food_log_by_client_id(1, client_id)["correction_state"] == "pending_review"


def test_snapshotless_pending_source_backed_combo_accepts_stored_baseline(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-source-combo-parent"
    meal_id = "pending-source-combo-meal"
    item_id = "combo"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    stored = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        external_food_id="stored-source-combo",
        verified_source_url="https://example.test/stored-source-combo",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **stored,
            "correction_state": "pending_review",
            "original_estimate": stored,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": stored},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["food_logs"][0]["source"] == "nutritionix"


def test_multi_pending_meal_type_edit_preserves_stored_nutrition_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-meal-type-parent"
    meal_id = "pending-meal-type-meal"
    item_id = "verified"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    estimate = _accepted_estimate(
        item_name="Verified meal",
        calories=420,
        meal_type="breakfast",
        source="nutritionix",
        external_food_id="verified-multi-meal",
        verified_source_url="https://example.test/verified-multi-meal",
        ambiguous=True,
        uncertainty_notes=["Stored provider ambiguity"],
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "included",
                    "item_id": item_id,
                    "estimate": dict(
                        estimate,
                        meal_type="lunch",
                        confidence=1.0,
                        ambiguous=False,
                        uncertainty_notes=["Forged certainty"],
                    ),
                },
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    accepted = data_store.get_food_log_by_client_id(1, client_id)["accepted_estimate"]
    assert accepted["meal_type"] == "lunch"
    assert accepted["source"] == "nutritionix"
    assert accepted["confidence"] == estimate["confidence"]
    assert accepted["ambiguous"] is True
    assert accepted["uncertainty_notes"] == ["Stored provider ambiguity"]
    assert accepted["external_food_id"] == "verified-multi-meal"
    assert accepted["verified_source_url"] == "https://example.test/verified-multi-meal"


def test_multi_item_pending_replay_preserves_stored_text_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-text-parent"
    meal_id = "pending-text-meal"
    item_id = "text"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    estimate = _accepted_estimate(item_name="Text meal", calories=300)
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            "source_timestamp": "2026-05-22T11:59:00",
            "context_note": "canonical stored phrase",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )
    asserted_image = dict(
        estimate,
        source="vision_forged",
        from_image=True,
        external_food_id="forged-provider-id",
        verified_source_url="https://example.test/forged",
        personal_vocab_phrase="forged multi phrase",
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "included",
                    "item_id": item_id,
                    "text": "poisoned replay phrase",
                    "estimate": asserted_image,
                },
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is False
    stored = data_store.get_food_logs_by_meal_id(1, meal_id)
    assert stored[0]["from_image"] is not True
    assert stored[0]["source"] == "manual_review_estimate"
    assert stored[0]["date"] == "2026-05-22"
    assert stored[0]["logged_at"] == "2026-05-22T12:00:00"
    assert stored[0]["source_timestamp"] == "2026-05-22T11:59:00"
    assert stored[0]["context_note"] == "canonical stored phrase"
    assert "external_food_id" not in stored[0]["accepted_estimate"]
    assert "verified_source_url" not in stored[0]["accepted_estimate"]
    assert "personal_vocab_phrase" not in stored[0]["accepted_estimate"]


def test_multi_item_pending_replay_rejects_concurrent_pending_refresh(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-race-parent"
    meal_id = "pending-race-meal"
    item_id = "race"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    original_estimate = _accepted_estimate(item_name="Original", calories=100)
    refreshed_estimate = _accepted_estimate(item_name="Refreshed", calories=200)
    base_record = {
        "client_id": client_id,
        "date": "2026-05-22",
        "logged_at": "2026-05-22T12:00:00",
        "correction_state": "pending_review",
        "meal_id": meal_id,
        "meal_item_id": item_id,
        "item_index": 0,
        "item_state": "included",
    }
    data_store.add_food_log(
        1,
        {**base_record, **original_estimate, "original_estimate": original_estimate},
    )
    original_meal_rows = module._meal_existing_rows

    def refresh_after_preflight(user_id, requested_meal_id):
        rows = original_meal_rows(user_id, requested_meal_id)
        data_store.add_food_log(
            1,
            {**base_record, **refreshed_estimate, "original_estimate": refreshed_estimate},
        )
        return rows

    monkeypatch.setattr(module, "_meal_existing_rows", refresh_after_preflight)

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": original_estimate},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    stored = data_store.get_food_logs_by_meal_id(1, meal_id)
    assert stored[0]["item_name"] == "Refreshed"
    assert stored[0]["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, meal_id) is None


def test_multi_item_snapshot_time_fields_override_independently(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "pending-snapshot-time-meal"
    item_id = "time"
    estimate = _accepted_estimate(item_name="Timed meal", calories=300)
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            "source_timestamp": "2026-05-22T11:59:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={
            "status": "pending_review",
            "meal_id": meal_id,
            "local_date": "2026-05-23",
            "items": [
                {
                    "item_id": item_id,
                    "status": "included",
                    "text": "canonical snapshot phrase",
                    "estimate": estimate,
                    "original_estimate": estimate,
                },
            ],
        },
        next_item_seq=2,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "local_timestamp": "2030-01-01T23:59:00",
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    stored = data_store.get_food_logs_by_meal_id(1, meal_id)
    assert stored[0]["date"] == "2026-05-23"
    assert stored[0]["logged_at"] == "2026-05-22T12:00:00"
    assert stored[0]["source_timestamp"] == "2026-05-22T11:59:00"
    assert stored[0]["context_note"] == "canonical snapshot phrase"


def test_multi_item_snapshot_replay_rejects_concurrent_snapshot_refresh(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "pending-snapshot-race-meal"
    item_id = "race"
    estimate = _accepted_estimate(item_name="Original snapshot meal", calories=300)
    refreshed_estimate = _accepted_estimate(item_name="Refreshed snapshot meal", calories=450)
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )

    def snapshot_payload(item_estimate):
        return {
            "status": "pending_review",
            "meal_id": meal_id,
            "items": [
                {
                    "item_id": item_id,
                    "status": "included",
                    "text": item_estimate["item_name"],
                    "estimate": item_estimate,
                    "original_estimate": item_estimate,
                },
            ],
        }

    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload=snapshot_payload(estimate),
        next_item_seq=2,
    )
    original_accept_multi = module._meal_intake_accept_multi

    def refresh_snapshot_before_transaction(parent_client_id, data):
        data_store.save_meal_review_snapshot(
            1,
            meal_id=meal_id,
            payload=snapshot_payload(refreshed_estimate),
            next_item_seq=2,
        )
        return original_accept_multi(parent_client_id, data)

    monkeypatch.setattr(module, "_meal_intake_accept_multi", refresh_snapshot_before_transaction)

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    stored = data_store.get_food_log_by_client_id(1, meal_id)
    assert stored["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, meal_id) is None
    snapshot = data_store.get_meal_review_snapshot(1, meal_id)
    assert snapshot["payload"]["items"][0]["estimate"]["calories"] == 450


def test_successful_snapshot_accept_cleans_up_inside_accept_transaction(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "atomic-snapshot-cleanup"
    item_id = "item"
    estimate = _accepted_estimate(item_name="Atomic cleanup meal", calories=300)
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={
            "status": "pending_review",
            "meal_id": meal_id,
            "items": [
                {
                    "item_id": item_id,
                    "status": "included",
                    "text": estimate["item_name"],
                    "estimate": estimate,
                    "original_estimate": estimate,
                },
            ],
        },
        next_item_seq=2,
    )
    original_delete = data_store.delete_meal_review_snapshot
    cleanup_transactions = []

    def tracked_delete(user_id, requested_meal_id, *, _conn=None):
        cleanup_transactions.append(bool(_conn is not None and _conn.in_transaction))
        return original_delete(user_id, requested_meal_id, _conn=_conn)

    monkeypatch.setattr(data_store, "delete_meal_review_snapshot", tracked_delete)

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert cleanup_transactions == [True]
    assert data_store.get_meal_review_snapshot(1, meal_id) is None


def test_terminal_idempotent_accept_cleans_trusted_snapshot_in_transaction(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "terminal-snapshot-parent"
    meal_id = "terminal-snapshot-meal"
    item_id = "item"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    estimate = _accepted_estimate(item_name="Already accepted", calories=300)
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={
            "status": "pending_review",
            "meal_id": meal_id,
            "items": [
                {
                    "item_id": item_id,
                    "status": "included",
                    "text": estimate["item_name"],
                    "estimate": estimate,
                    "original_estimate": estimate,
                },
            ],
        },
        next_item_seq=2,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "accepted",
            "original_estimate": estimate,
            "accepted_estimate": estimate,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )
    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert data_store.get_meal_review_snapshot(1, meal_id) is None


def test_multi_item_accept_rejects_snapshot_created_after_empty_preflight(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "new-snapshot-race-meal"
    item_id = "race"
    estimate = _accepted_estimate(item_name="Submitted meal", calories=300)
    original_accept_multi = module._meal_intake_accept_multi

    def create_snapshot_before_transaction(parent_client_id, data):
        data_store.save_meal_review_snapshot(
            1,
            meal_id=meal_id,
            payload={
                "status": "pending_review",
                "meal_id": meal_id,
                "items": [
                    {
                        "item_id": item_id,
                        "status": "included",
                        "text": "Fresh server review",
                        "estimate": estimate,
                        "original_estimate": estimate,
                    },
                ],
            },
            next_item_seq=2,
        )
        return original_accept_multi(parent_client_id, data)

    monkeypatch.setattr(module, "_meal_intake_accept_multi", create_snapshot_before_transaction)

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    assert data_store.get_meal_acceptance_event(1, meal_id) is None
    assert data_store.get_meal_review_snapshot(1, meal_id) is not None


def test_multi_item_accept_rejects_pending_parent_created_after_empty_preflight(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from contextlib import contextmanager

    parent_client_id = "new-pending-parent-race"
    meal_id = "new-pending-meal-race"
    item_id = "race"
    estimate = _accepted_estimate(item_name="Submitted meal", calories=300)
    original_transaction = module.food_log_transaction
    created = False

    @contextmanager
    def create_pending_parent_before_lock():
        nonlocal created
        if not created:
            created = True
            data_store.add_food_log(
                1,
                {
                    "client_id": meal_id,
                    "date": "2026-05-22",
                    "logged_at": "2026-05-22T12:00:00",
                    **estimate,
                    "correction_state": "pending_review",
                    "original_estimate": estimate,
                },
            )
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(module, "food_log_transaction", create_pending_parent_before_lock)

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    assert data_store.get_meal_acceptance_event(1, meal_id) is None
    assert data_store.get_food_log_by_client_id(1, meal_id)["correction_state"] == "pending_review"


def test_logged_retry_replays_when_pending_alias_is_removed_by_overlapping_accept(
    monkeypatch,
    tmp_path,
):
    from contextlib import contextmanager

    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "overlap-logged-parent"
    meal_id = "overlap-logged-meal"
    item_id = "item-1"
    estimate = _accepted_estimate(item_name="Overlapping accepted item", calories=300)
    client_id = module._meal_item_client_id(
        parent_client_id,
        {"item_id": item_id},
        0,
    )
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )
    original_transaction = module.food_log_transaction
    committed = False

    @contextmanager
    def commit_overlapping_accept_before_lock():
        nonlocal committed
        if not committed:
            committed = True
            data_store.delete_food_log_by_client_id(1, meal_id)
            data_store.add_food_log(
                1,
                {
                    "client_id": client_id,
                    "meal_id": meal_id,
                    "meal_item_id": item_id,
                    "item_index": 0,
                    "item_state": "included",
                    "date": "2026-05-22",
                    "logged_at": "2026-05-22T12:00:00",
                    **estimate,
                    "correction_state": "accepted",
                    "original_estimate": estimate,
                    "accepted_estimate": estimate,
                },
            )
            data_store.save_meal_acceptance_event(
                1,
                meal_id=meal_id,
                status="logged",
                included_client_ids=[client_id],
                skipped_count=0,
                deleted_count=0,
            )
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(
        module,
        "food_log_transaction",
        commit_overlapping_accept_before_lock,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert [row["client_id"] for row in response.get_json()["food_logs"]] == [client_id]


def test_discard_retry_replays_when_pending_alias_is_removed_by_overlapping_accept(
    monkeypatch,
    tmp_path,
):
    from contextlib import contextmanager

    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "overlap-discard-parent"
    meal_id = "overlap-discard-meal"
    item_id = "item-1"
    estimate = _accepted_estimate(item_name="Overlapping skipped item", calories=300)
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )
    original_transaction = module.food_log_transaction
    committed = False

    @contextmanager
    def commit_overlapping_discard_before_lock():
        nonlocal committed
        if not committed:
            committed = True
            data_store.delete_food_log_by_client_id(1, meal_id)
            data_store.save_meal_acceptance_event(
                1,
                meal_id=meal_id,
                status="discarded",
                included_client_ids=[],
                skipped_count=1,
                deleted_count=0,
            )
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(
        module,
        "food_log_transaction",
        commit_overlapping_discard_before_lock,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "skipped",
                    "item_id": item_id,
                    "text": "Overlapping skipped item",
                    "estimate": estimate,
                },
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["status"] == "discarded"


def test_legacy_snapshot_replay_rejects_concurrent_snapshot_refresh(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "legacy-snapshot-race-meal"
    estimate = _accepted_estimate(item_name="Legacy original", calories=300)
    refreshed_estimate = _accepted_estimate(item_name="Legacy refreshed", calories=450)
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )

    def snapshot_payload(item_estimate):
        return {
            "status": "pending_review",
            "meal_id": meal_id,
            "items": [
                {
                    "item_id": "item-1",
                    "status": "included",
                    "text": item_estimate["item_name"],
                    "estimate": item_estimate,
                    "original_estimate": item_estimate,
                },
            ],
        }

    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload=snapshot_payload(estimate),
        next_item_seq=2,
    )
    original_accept_multi = module._meal_intake_accept_multi

    def refresh_snapshot_before_transaction(parent_client_id, data):
        data_store.save_meal_review_snapshot(
            1,
            meal_id=meal_id,
            payload=snapshot_payload(refreshed_estimate),
            next_item_seq=2,
        )
        return original_accept_multi(parent_client_id, data)

    monkeypatch.setattr(module, "_meal_intake_accept_multi", refresh_snapshot_before_transaction)

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={},
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    snapshot = data_store.get_meal_review_snapshot(1, meal_id)
    assert snapshot["payload"]["items"][0]["estimate"]["calories"] == 450


def test_multi_item_snapshot_discard_rejects_concurrent_snapshot_refresh(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "pending-snapshot-discard-race"
    item_id = "discard"
    estimate = _accepted_estimate(item_name="Original discard", calories=300)
    refreshed_estimate = _accepted_estimate(item_name="Refreshed discard", calories=450)
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
        },
    )

    def snapshot_payload(item_estimate):
        return {
            "status": "pending_review",
            "meal_id": meal_id,
            "items": [
                {
                    "item_id": item_id,
                    "status": "included",
                    "text": item_estimate["item_name"],
                    "estimate": item_estimate,
                    "original_estimate": item_estimate,
                },
            ],
        }

    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload=snapshot_payload(estimate),
        next_item_seq=2,
    )
    original_accept_multi = module._meal_intake_accept_multi

    def refresh_snapshot_before_transaction(parent_client_id, data):
        data_store.save_meal_review_snapshot(
            1,
            meal_id=meal_id,
            payload=snapshot_payload(refreshed_estimate),
            next_item_seq=2,
        )
        return original_accept_multi(parent_client_id, data)

    monkeypatch.setattr(module, "_meal_intake_accept_multi", refresh_snapshot_before_transaction)

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "skipped",
                    "item_id": item_id,
                    "text": "Original discard",
                    "estimate": estimate,
                },
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    assert data_store.get_meal_acceptance_event(1, meal_id) is None
    snapshot = data_store.get_meal_review_snapshot(1, meal_id)
    assert snapshot["payload"]["items"][0]["estimate"]["calories"] == 450


def test_discarded_event_replay_rejects_concurrent_snapshot_refresh(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "discarded-event-snapshot-race"
    item_id = "discard"
    estimate = _accepted_estimate(item_name="Original discard", calories=300)

    def snapshot_payload(item_estimate):
        return {
            "status": "pending_review",
            "meal_id": meal_id,
            "items": [
                {
                    "item_id": item_id,
                    "status": "included",
                    "text": item_estimate["item_name"],
                    "estimate": item_estimate,
                    "original_estimate": item_estimate,
                },
            ],
        }

    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload=snapshot_payload(estimate),
        next_item_seq=2,
    )
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="discarded",
        included_client_ids=[],
        skipped_count=1,
        deleted_count=0,
        feedback_fingerprint=module._meal_negative_feedback_fingerprint([
            {
                "state": "skipped",
                "index": 0,
                "meal_item_id": item_id,
                "raw": {"text": "Original discard"},
            },
        ]),
    )
    monkeypatch.setattr(module, "_meal_review_snapshot_changed", lambda *_args: True)

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "skipped",
                    "item_id": item_id,
                    "text": "Original discard",
                    "estimate": estimate,
                },
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    snapshot = data_store.get_meal_review_snapshot(1, meal_id)
    assert snapshot["payload"]["items"][0]["estimate"]["calories"] == 300


def test_discard_rejects_pending_parent_created_after_empty_preflight(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "discard-new-pending-parent-race"
    estimate = _accepted_estimate(item_name="New pending parent", calories=300)
    original_lookup = module._food_log_by_client_id
    created = False
    lookup_count = 0

    def create_pending_after_empty_preflight(user_id, requested_client_id):
        nonlocal created, lookup_count
        row = original_lookup(user_id, requested_client_id)
        if requested_client_id == meal_id:
            lookup_count += 1
        if requested_client_id == meal_id and lookup_count == 3 and row is None and not created:
            created = True
            data_store.add_food_log(
                1,
                {
                    "client_id": meal_id,
                    "date": "2026-05-22",
                    "logged_at": "2026-05-22T12:00:00",
                    **estimate,
                    "correction_state": "pending_review",
                    "original_estimate": estimate,
                },
            )
        return row

    monkeypatch.setattr(module, "_food_log_by_client_id", create_pending_after_empty_preflight)

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "skipped",
                    "item_id": "discard",
                    "text": "Not this meal",
                    "estimate": estimate,
                },
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    stored = data_store.get_food_log_by_client_id(1, meal_id)
    assert stored["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, meal_id) is None


def test_trusted_multi_item_snapshot_preserves_per_item_image_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "mixed-photo-text-snapshot"
    photo_estimate = _accepted_estimate(
        item_name="Photo item",
        calories=300,
        source="vision_estimate",
        from_image=True,
    )
    text_estimate = _accepted_estimate(item_name="Text item", calories=200)
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **photo_estimate,
            "correction_state": "pending_review",
            "original_estimate": photo_estimate,
        },
    )
    items = [
        {
            "item_id": "photo",
            "status": "included",
            "text": "Photo item",
            "estimate": photo_estimate,
            "original_estimate": photo_estimate,
        },
        {
            "item_id": "text",
            "status": "included",
            "text": "Text item",
            "estimate": text_estimate,
            "original_estimate": text_estimate,
        },
    ]
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={"status": "pending_review", "meal_id": meal_id, "items": items},
        next_item_seq=3,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "photo", "estimate": photo_estimate},
                {"state": "included", "item_id": "text", "estimate": text_estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    by_item_id = {row["meal_item_id"]: row for row in response.get_json()["food_logs"]}
    assert by_item_id["photo"]["from_image"] is True
    assert by_item_id["text"]["from_image"] is not True


def test_imported_untrusted_snapshot_cannot_assert_image_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "imported-untrusted-image"
    item_id = "forged-photo"
    estimate = _accepted_estimate(
        item_name="Imported item",
        calories=300,
        source="vision_forged",
        from_image=True,
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={
            "status": "pending_review",
            "meal_id": meal_id,
            "has_image": True,
            "_imported_snapshot_untrusted": True,
            "items": [
                {
                    "item_id": item_id,
                    "status": "included",
                    "text": "Imported item",
                    "estimate": estimate,
                    "original_estimate": estimate,
                    "_imported_snapshot_untrusted": True,
                },
            ],
        },
        next_item_seq=2,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is False
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is False

    stored = response.get_json()["food_logs"][0]
    assert stored["original_estimate"].get("from_image") is not True

    retry = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert retry.status_code == 200, retry.get_data(as_text=True)
    assert retry.get_json()["photo_retention"]["image_received"] is False
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is False


def test_snapshotless_imported_parent_cannot_assert_image_on_discard(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "snapshotless-imported-parent-discard"
    imported = _accepted_estimate(
        item_name="Imported photo claim",
        calories=320,
        source="vision_forged",
        from_image=True,
    )
    imported["_imported_pending_untrusted"] = True
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **imported,
            "correction_state": "pending_review",
            "original_estimate": imported,
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "skipped",
                    "item_id": "item-1",
                    "estimate": imported,
                },
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["status"] == "discarded"
    assert response.get_json()["photo_retention"]["image_received"] is False
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is False


def test_imported_pending_single_accept_does_not_promote_forged_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client_id = "imported-pending-single-provenance"
    imported = _accepted_estimate(
        item_name="Imported pending item",
        calories=320,
        source="nutritionix",
        external_food_id="forged-provider-id",
        verified_source_url="https://example.invalid/forged",
        brand_id="forged-brand",
        from_image=True,
    )
    imported["_imported_pending_untrusted"] = True
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            "context_note": "Imported pending item",
            **imported,
            "correction_state": "pending_review",
            "original_estimate": imported,
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": imported},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    stored = response.get_json()["food_log"]
    assert stored["source"] == "manual_review_estimate"
    assert stored.get("from_image") is not True
    assert stored["original_estimate"].get("from_image") is not True
    assert response.get_json()["photo_retention"]["image_received"] is False
    for field in ("external_food_id", "verified_source_url", "brand_id"):
        assert stored.get(field) is None
        assert stored["accepted_estimate"].get(field) is None


def test_imported_pending_multi_accept_does_not_promote_forged_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "imported-pending-multi-provenance"
    item_client_id = module._meal_item_client_id(meal_id, {"item_id": "item-1"}, 0)
    imported = _accepted_estimate(
        item_name="Imported pending item",
        calories=320,
        source="nutritionix",
        external_food_id="forged-provider-id",
        verified_source_url="https://example.invalid/forged",
        brand_id="forged-brand",
        from_image=True,
    )
    imported["_imported_pending_untrusted"] = True
    data_store.add_food_log(
        1,
        {
            "client_id": item_client_id,
            "meal_id": meal_id,
            "date": "2026-07-13",
            "logged_at": "2026-07-13T12:00:00",
            "context_note": "Imported pending item",
            **imported,
            "correction_state": "pending_review",
            "original_estimate": imported,
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "item-1", "estimate": imported},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    stored = response.get_json()["food_logs"][0]
    assert stored["source"] == "manual_review_estimate"
    assert stored.get("from_image") is not True
    assert stored["original_estimate"].get("from_image") is not True
    assert response.get_json()["photo_retention"]["image_received"] is False
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is False
    for field in ("external_food_id", "verified_source_url", "brand_id"):
        assert stored.get(field) is None
        assert stored["accepted_estimate"].get(field) is None


@pytest.mark.parametrize("multi_item", [False, True])
@pytest.mark.parametrize(
    "pending_state",
    ["pending_review", "pending", "needs_review", "review", "PENDING_REVIEW"],
)
def test_add_nutrition_pending_rows_cannot_stage_forged_provenance(
    monkeypatch,
    tmp_path,
    multi_item,
    pending_state,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = f"client-staged-pending-{multi_item}"
    item_id = "item-1"
    client_id = (
        module._meal_item_client_id(
            parent_client_id,
            {"item_id": item_id},
            0,
        )
        if multi_item
        else parent_client_id
    )
    forged = _accepted_estimate(
        item_name="Client staged photo claim",
        calories=320,
        source="vision_forged",
        from_image=True,
        external_food_id="forged-provider-id",
        verified_source_url="https://example.invalid/forged-provider",
    )
    staged = module.app.test_client().post(
        "/api/add-nutrition",
        json={
            "client_id": client_id,
            "date": "2026-05-22",
            "calories": forged["calories"],
            "protein_g": forged["protein_g"],
            "carbs_g": forged["carbs_g"],
            "fat_g": forged["fat_g"],
            "sodium_mg": forged["sodium_mg"],
            "fiber_g": forged["fiber_g"],
            "item_name": forged["item_name"],
            "source": forged["source"],
            "correction_state": pending_state,
            "original_estimate": forged,
        },
    )
    assert staged.status_code == 200, staged.get_data(as_text=True)
    pending = data_store.get_food_log_by_client_id(1, client_id)
    assert pending["correction_state"] == "pending_review"
    assert pending["original_estimate"]["_imported_pending_untrusted"] is True
    assert pending["source"] == "manual"
    assert pending.get("from_image") is not True
    assert staged.get_json()["food_log"].get("from_image") is not True
    for field in ("external_food_id", "verified_source_url"):
        assert pending.get(field) is None
        assert pending["original_estimate"].get(field) is None
        assert staged.get_json()["food_log"].get(field) is None

    if multi_item:
        accepted = module.app.test_client().post(
            f"/api/meal-intake/{parent_client_id}/accept",
            json={
                "meal_id": parent_client_id,
                "items": [
                    {"state": "included", "item_id": item_id, "estimate": forged},
                ],
            },
        )
        stored = accepted.get_json()["food_logs"][0]
    else:
        accepted = module.app.test_client().post(
            f"/api/meal-intake/{client_id}/accept",
            json={"estimate": forged},
        )
        stored = accepted.get_json()["food_log"]

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    assert stored["source"] == "manual_review_estimate"
    assert stored.get("from_image") is not True
    assert accepted.get_json()["photo_retention"]["image_received"] is False
    for field in ("external_food_id", "verified_source_url"):
        assert stored.get(field) is None
        assert stored["accepted_estimate"].get(field) is None


def test_add_nutrition_rejects_unknown_correction_state(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client_id = "unknown-correction-state"

    response = module.app.test_client().post(
        "/api/add-nutrition",
        json={
            "client_id": client_id,
            "date": "2026-07-14",
            "calories": 320,
            "protein_g": 35,
            "correction_state": "trust_me",
        },
    )

    assert response.status_code == 400, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "invalid_field"
    assert data_store.get_food_log_by_client_id(1, client_id) is None


def test_add_nutrition_pending_without_source_stays_manual(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client_id = "manual-pending-without-source"

    staged = module.app.test_client().post(
        "/api/add-nutrition",
        json={
            "client_id": client_id,
            "date": "2026-07-14",
            "calories": 320,
            "protein_g": 35,
            "correction_state": "pending_review",
            "item_name": "Manual pending meal",
        },
    )

    assert staged.status_code == 200, staged.get_data(as_text=True)
    stored = data_store.get_food_log_by_client_id(1, client_id)
    assert stored["source"] == "manual"
    assert stored["original_estimate"]["source"] == "manual_review_estimate"
    assert stored.get("from_image") is not True
    pending = module.app.test_client().get("/api/meal-intake/pending")
    assert pending.status_code == 200, pending.get_data(as_text=True)
    estimate = pending.get_json()["pending"][0]["estimate"]
    assert estimate["source"] == "manual_review_estimate"
    assert estimate.get("from_image") is not True


def test_trusted_photo_snapshot_preserves_meal_image_when_photo_item_is_skipped(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "mixed-photo-text-skip-photo"
    photo_estimate = _accepted_estimate(
        item_name="Photo item",
        calories=300,
        source="vision_estimate",
        from_image=True,
    )
    text_estimate = _accepted_estimate(item_name="Text item", calories=200)
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={
            "status": "pending_review",
            "meal_id": meal_id,
            "has_image": True,
            "items": [
                {
                    "item_id": "photo",
                    "status": "included",
                    "text": "Photo item",
                    "estimate": photo_estimate,
                    "original_estimate": photo_estimate,
                },
                {
                    "item_id": "text",
                    "status": "included",
                    "text": "Text item",
                    "estimate": text_estimate,
                    "original_estimate": text_estimate,
                },
            ],
        },
        next_item_seq=3,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "skipped", "item_id": "photo", "estimate": photo_estimate},
                {"state": "included", "item_id": "text", "estimate": text_estimate},
            ],
        },
    )
    retry = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "skipped", "item_id": "photo", "estimate": photo_estimate},
                {"state": "included", "item_id": "text", "estimate": text_estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert retry.status_code == 200, retry.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is True
    assert retry.get_json()["photo_retention"]["image_received"] is True
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is True


def test_rows_missing_recovery_preserves_existing_event_image_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "rows-missing-photo-parent"
    meal_id = "rows-missing-photo-meal"
    item_id = "text"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    estimate = _accepted_estimate(item_name="Recovered text item", calories=200)
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="logged",
        included_client_ids=[client_id],
        skipped_count=0,
        deleted_count=0,
        has_image=True,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is True
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is True


def test_discarded_multi_item_retry_rejects_request_asserted_image_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    payload = {
        "meal_id": "discarded-text-retry",
        "items": [
            {
                "state": "skipped",
                "item_id": "plate",
                "text": "Empty plate",
                "estimate": _accepted_estimate(item_name="Empty plate", calories=0),
            },
        ],
    }
    first = client.post("/api/meal-intake/discarded-text-parent/accept", json=payload)
    retry_payload = {
        **payload,
        "items": [
            {
                **payload["items"][0],
                "estimate": dict(payload["items"][0]["estimate"], from_image=True),
            },
        ],
    }

    retry = client.post("/api/meal-intake/discarded-text-parent/accept", json=retry_payload)

    assert first.status_code == 200, first.get_data(as_text=True)
    assert retry.status_code == 200, retry.get_data(as_text=True)
    assert retry.get_json()["photo_retention"]["image_received"] is False


def test_discarded_photo_retry_preserves_stored_image_provenance(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    client = module.app.test_client()
    meal_id = "discarded-photo-retry"
    estimate = _accepted_estimate(
        item_name="Photo item",
        calories=300,
        source="vision_estimate",
        from_image=True,
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={
            "status": "pending_review",
            "meal_id": meal_id,
            "has_image": True,
            "items": [
                {
                    "item_id": "photo",
                    "status": "included",
                    "text": "Photo item",
                    "estimate": estimate,
                    "original_estimate": estimate,
                },
            ],
        },
        next_item_seq=2,
    )
    payload = {
        "meal_id": meal_id,
        "items": [
            {
                "state": "skipped",
                "item_id": "photo",
                "text": "Photo item",
                "estimate": estimate,
            },
        ],
    }
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="discarded",
        included_client_ids=[],
        skipped_count=1,
        deleted_count=0,
        has_image=False,
    )

    first = client.post(f"/api/meal-intake/{meal_id}/accept", json=payload)
    retry = client.post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            **payload,
            "items": [{**payload["items"][0], "estimate": dict(estimate, from_image=False)}],
        },
    )
    terminal_retry = client.post(f"/api/meal-intake/{meal_id}/accept", json={})

    assert first.status_code == 200, first.get_data(as_text=True)
    assert retry.status_code == 200, retry.get_data(as_text=True)
    assert terminal_retry.status_code == 200, terminal_retry.get_data(as_text=True)
    assert first.get_json()["photo_retention"]["image_received"] is True
    assert retry.get_json()["photo_retention"]["image_received"] is True
    assert terminal_retry.get_json()["photo_retention"]["image_received"] is True


def test_fresh_multi_accept_does_not_infer_image_from_client_source(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    estimate = _accepted_estimate(
        source="vision_forged",
        from_image=True,
        external_food_id="forged-fresh-multi",
        verified_source_url="https://example.test/forged-fresh-multi",
    )

    response = module.app.test_client().post(
        "/api/meal-intake/fresh-multi-forged-source/accept",
        json={
            "meal_id": "fresh-multi-forged-source",
            "items": [
                {"state": "included", "item_id": "item", "estimate": estimate},
            ],
        },
    )
    retry = module.app.test_client().post(
        "/api/meal-intake/fresh-multi-forged-source/accept",
        json={
            "meal_id": "fresh-multi-forged-source",
            "items": [
                {"state": "included", "item_id": "item", "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert retry.status_code == 200, retry.get_data(as_text=True)
    assert response.get_json()["photo_retention"]["image_received"] is False
    assert retry.get_json()["photo_retention"]["image_received"] is False
    accepted = response.get_json()["food_logs"][0]["accepted_estimate"]
    assert accepted["source"] == "manual_review_estimate"
    assert "from_image" not in accepted
    assert "external_food_id" not in accepted
    assert "verified_source_url" not in accepted


def test_pending_row_repair_enqueues_workout_adaptation_with_existing_event(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-repair-parent"
    meal_id = "pending-repair-meal"
    item_id = "repair"
    client_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    estimate = _accepted_estimate(item_name="Repair meal", calories=300)
    data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "pending_review",
            "original_estimate": estimate,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )
    data_store.save_meal_acceptance_event(
        1,
        meal_id=meal_id,
        status="logged",
        included_client_ids=[client_id],
        skipped_count=0,
        deleted_count=0,
    )
    enqueued = []
    monkeypatch.setattr(
        module,
        "_enqueue_workout_adaptation_after_accept",
        lambda user_id, rows: enqueued.append((user_id, rows)),
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert len(enqueued) == 1
    assert enqueued[0][0] == 1
    assert enqueued[0][1][0]["correction_state"] in {"accepted", "corrected"}


def test_partial_pending_accept_rejects_omitted_pending_child(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "partial-pending-omission-parent"
    meal_id = "partial-pending-omission-meal"
    included_id = module._meal_item_client_id(
        parent_client_id,
        {"item_id": "included"},
        0,
    )
    omitted_id = module._meal_item_client_id(
        parent_client_id,
        {"item_id": "omitted"},
        1,
    )
    included_estimate = _accepted_estimate(item_name="Included item", calories=200)
    omitted_estimate = _accepted_estimate(item_name="Omitted item", calories=300)
    for client_id, item_id, item_index, estimate in (
        (included_id, "included", 0, included_estimate),
        (omitted_id, "omitted", 1, omitted_estimate),
    ):
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "date": "2026-05-22",
                "logged_at": "2026-05-22T12:00:00",
                **estimate,
                "correction_state": "pending_review",
                "original_estimate": estimate,
                "meal_id": meal_id,
                "meal_item_id": item_id,
                "item_index": item_index,
                "item_state": "included",
            },
        )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "included",
                    "item_id": "included",
                    "estimate": included_estimate,
                },
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    assert data_store.get_meal_acceptance_event(1, meal_id) is None
    stored = data_store.get_food_logs_by_meal_id(1, meal_id)
    assert {row["client_id"] for row in stored} == {included_id, omitted_id}
    assert {row["correction_state"] for row in stored} == {"pending_review"}


def test_partial_pending_skip_learns_from_stored_child(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "partial-pending-feedback-parent"
    meal_id = "partial-pending-feedback-meal"
    included_id = module._meal_item_client_id(parent_client_id, {"item_id": "food"}, 0)
    skipped_id = module._meal_item_client_id(parent_client_id, {"item_id": "plate"}, 1)
    included_estimate = _accepted_estimate(item_name="Lunch", calories=300)
    skipped_estimate = _accepted_estimate(item_name="Canonical plate", calories=10)
    for client_id, item_id, index, estimate, context_note in (
        (included_id, "food", 0, included_estimate, "canonical lunch"),
        (skipped_id, "plate", 1, skipped_estimate, "canonical plate"),
    ):
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "date": "2026-05-22",
                "logged_at": "2026-05-22T12:00:00",
                "context_note": context_note,
                **estimate,
                "correction_state": "pending_review",
                "original_estimate": estimate,
                "meal_id": meal_id,
                "meal_item_id": item_id,
                "item_index": index,
                "item_state": "included",
            },
        )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "food", "estimate": included_estimate},
                {
                    "state": "skipped",
                    "item_id": "plate",
                    "text": "forged plate phrase",
                    "estimate": _accepted_estimate(item_name="Forged plate", calories=999),
                },
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert data_store.get_personal_vocab_entry(1, "canonical plate")["skip_count"] == 1
    assert data_store.get_personal_vocab_entry(1, "forged plate phrase") is None


def test_pending_full_discard_learns_from_stored_child(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "pending-discard-feedback-parent"
    meal_id = "pending-discard-feedback-meal"
    child_id = module._meal_item_client_id(parent_client_id, {"item_id": "plate"}, 0)
    stored_estimate = _accepted_estimate(item_name="Canonical empty plate", calories=10)
    data_store.add_food_log(
        1,
        {
            "client_id": child_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            "context_note": "canonical empty plate",
            **stored_estimate,
            "correction_state": "pending_review",
            "original_estimate": stored_estimate,
            "meal_id": meal_id,
            "meal_item_id": "plate",
            "item_index": 0,
            "item_state": "included",
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "deleted",
                    "item_id": "plate",
                    "text": "forged discard phrase",
                    "estimate": _accepted_estimate(item_name="Forged discard", calories=999),
                },
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert data_store.get_personal_vocab_entry(1, "canonical empty plate")["deleted_count"] == 1
    assert data_store.get_personal_vocab_entry(1, "forged discard phrase") is None


def test_child_pending_accept_rejects_concurrent_parent_alias_refresh(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from contextlib import contextmanager

    parent_client_id = "child-parent-alias-race-parent"
    meal_id = "child-parent-alias-race-meal"
    item_id = "food"
    child_id = module._meal_item_client_id(parent_client_id, {"item_id": item_id}, 0)
    child_estimate = _accepted_estimate(item_name="Child meal", calories=300)
    parent_estimate = _accepted_estimate(item_name="Parent original", calories=300)
    refreshed_parent = _accepted_estimate(item_name="Parent refreshed", calories=450)
    data_store.add_food_log(
        1,
        {
            "client_id": child_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **child_estimate,
            "correction_state": "pending_review",
            "original_estimate": child_estimate,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **parent_estimate,
            "correction_state": "pending_review",
            "original_estimate": parent_estimate,
        },
    )
    original_transaction = module.food_log_transaction
    refreshed = False

    @contextmanager
    def refresh_parent_before_lock():
        nonlocal refreshed
        if not refreshed:
            refreshed = True
            data_store.add_food_log(
                1,
                {
                    "client_id": meal_id,
                    "date": "2026-05-22",
                    "logged_at": "2026-05-22T12:01:00",
                    **refreshed_parent,
                    "correction_state": "pending_review",
                    "original_estimate": refreshed_parent,
                },
            )
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(module, "food_log_transaction", refresh_parent_before_lock)

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": child_estimate},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "stale_pending_review"
    stored_parent = data_store.get_food_log_by_client_id(1, meal_id)
    assert stored_parent["item_name"] == "Parent refreshed"
    assert stored_parent["correction_state"] == "pending_review"
    assert data_store.get_food_log_by_client_id(1, child_id)["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, meal_id) is None


def test_partial_recovery_accepts_unchanged_pending_parent_alias(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "partial-parent-alias-parent"
    meal_id = "partial-parent-alias-meal"
    terminal_id = module._meal_item_client_id(parent_client_id, {"item_id": "done"}, 0)
    new_id = module._meal_item_client_id(parent_client_id, {"item_id": "missing"}, 1)
    terminal_estimate = _accepted_estimate(
        item_name="Already accepted",
        calories=200,
        source="manual_review_estimate",
    )
    new_estimate = _accepted_estimate(
        item_name="Recovered item",
        calories=300,
        source="manual_review_estimate",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": terminal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **terminal_estimate,
            "correction_state": "accepted",
            "original_estimate": terminal_estimate,
            "accepted_estimate": terminal_estimate,
            "meal_id": meal_id,
            "meal_item_id": "done",
            "item_index": 0,
            "item_state": "included",
        },
    )
    data_store.add_food_log(
        1,
        {
            "client_id": meal_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **new_estimate,
            "correction_state": "pending_review",
            "original_estimate": new_estimate,
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "done", "estimate": terminal_estimate},
                {"state": "included", "item_id": "missing", "estimate": new_estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert {row["client_id"] for row in response.get_json()["food_logs"]} == {
        terminal_id,
        new_id,
    }
    assert data_store.get_food_log_by_client_id(1, meal_id) is None
    assert data_store.get_meal_acceptance_event(1, meal_id)["status"] == "logged"


def test_imported_snapshot_and_pending_child_cannot_restore_image_provenance(
    monkeypatch,
    tmp_path,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    meal_id = "imported-snapshot-pending-image"
    item_id = "item-1"
    child_id = module._meal_item_client_id(meal_id, {"item_id": item_id}, 0)
    forged = _accepted_estimate(
        item_name="Imported photo claim",
        calories=320,
        source="vision_forged",
        from_image=True,
    )
    forged["_imported_pending_untrusted"] = True
    data_store.add_food_log(
        1,
        {
            "client_id": child_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **forged,
            "correction_state": "pending_review",
            "original_estimate": forged,
            "meal_id": meal_id,
            "meal_item_id": item_id,
            "item_index": 0,
            "item_state": "included",
        },
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id=meal_id,
        payload={
            "status": "pending_review",
            "meal_id": meal_id,
            "has_image": True,
            "_imported_snapshot_untrusted": True,
            "items": [
                {
                    "item_id": item_id,
                    "status": "included",
                    "text": "Imported photo claim",
                    "estimate": forged,
                    "original_estimate": forged,
                    "_imported_snapshot_untrusted": True,
                },
            ],
        },
        next_item_seq=2,
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{meal_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": item_id, "estimate": forged},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    stored = response.get_json()["food_logs"][0]
    assert stored.get("from_image") is not True
    assert stored["accepted_estimate"].get("from_image") is not True
    assert stored["accepted_estimate"]["source"] == "manual_review_estimate"
    assert response.get_json()["photo_retention"]["image_received"] is False
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is False


@pytest.mark.parametrize("existing_event", [True, False])
def test_partial_pending_repair_enqueues_only_newly_finalized_rows(
    monkeypatch,
    tmp_path,
    existing_event,
):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "partial-pending-repair-parent"
    meal_id = "partial-pending-repair-meal"
    terminal_id = module._meal_item_client_id(parent_client_id, {"item_id": "terminal"}, 0)
    pending_id = module._meal_item_client_id(parent_client_id, {"item_id": "pending"}, 1)
    new_id = module._meal_item_client_id(parent_client_id, {"item_id": "new"}, 2)
    terminal_estimate = _accepted_estimate(item_name="Already accepted", calories=200)
    pending_estimate = _accepted_estimate(item_name="Needs repair", calories=300)
    new_estimate = _accepted_estimate(item_name="New item", calories=100)
    for client_id, item_id, index, estimate, state in (
        (terminal_id, "terminal", 0, terminal_estimate, "accepted"),
        (pending_id, "pending", 1, pending_estimate, "pending_review"),
    ):
        data_store.add_food_log(
            1,
            {
                "client_id": client_id,
                "date": "2026-05-22",
                "logged_at": "2026-05-22T12:00:00",
                **estimate,
                "correction_state": state,
                "original_estimate": estimate,
                "accepted_estimate": estimate if state == "accepted" else None,
                "meal_id": meal_id,
                "meal_item_id": item_id,
                "item_index": index,
                "item_state": "included",
            },
        )
    if existing_event:
        data_store.save_meal_acceptance_event(
            1,
            meal_id=meal_id,
            status="logged",
            included_client_ids=[terminal_id, pending_id, new_id],
            skipped_count=0,
            deleted_count=0,
        )
    enqueued = []
    monkeypatch.setattr(
        module,
        "_enqueue_workout_adaptation_after_accept",
        lambda user_id, rows: enqueued.append((user_id, rows)),
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {
                    "state": "included",
                    "item_id": "terminal",
                    "estimate": dict(terminal_estimate, from_image=True),
                },
                {"state": "included", "item_id": "pending", "estimate": pending_estimate},
                {"state": "included", "item_id": "new", "estimate": new_estimate},
            ],
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert len(enqueued) == 1
    assert enqueued[0][0] == 1
    assert [row["client_id"] for row in enqueued[0][1]] == [pending_id, new_id]
    assert response.get_json()["photo_retention"]["image_received"] is False
    assert data_store.get_meal_acceptance_event(1, meal_id)["has_image"] is False


def test_concurrent_rows_only_recovery_records_feedback_once(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    parent_client_id = "photo-parent-rows-only-concurrent"
    meal_id = "photo-meal-rows-only-concurrent"
    item_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "a"}, 0)
    estimate = _accepted_estimate(item_name="A", calories=100)
    data_store.add_food_log(
        1,
        {
            "client_id": item_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            **estimate,
            "correction_state": "accepted",
            "accepted_estimate": estimate,
            "meal_id": meal_id,
            "meal_item_id": "a",
            "item_index": 0,
            "item_state": "included",
        },
    )
    barrier = Barrier(2)
    original_get_event = module.get_meal_acceptance_event

    def synchronized_initial_event_read(*args, **kwargs):
        result = original_get_event(*args, **kwargs)
        barrier.wait()
        return result

    monkeypatch.setattr(module, "get_meal_acceptance_event", synchronized_initial_event_read)
    payload = {
        "meal_id": meal_id,
        "items": [
            {"state": "included", "item_id": "a", "estimate": estimate},
            {"state": "skipped", "text": "plate", "estimate": _accepted_estimate(item_name="Plate", calories=10)},
        ],
    }

    def accept():
        with module.app.test_client() as client:
            return client.post(f"/api/meal-intake/{parent_client_id}/accept", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: accept(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert data_store.get_personal_vocab_entry(1, "plate")["skip_count"] == 1


def test_multi_accept_cannot_overwrite_existing_manual_client_id(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "photo-parent-manual-conflict"
    item_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "a"}, 0)
    data_store.add_food_log(
        1,
        {
            "client_id": item_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            "item_name": "Canonical manual snack",
            "calories": 111,
            "source": "manual",
            "correction_state": "manual",
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": "photo-meal-manual-conflict",
            "items": [
                {"state": "included", "item_id": "a", "estimate": _accepted_estimate(calories=999)},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    stored = data_store.get_food_logs(1)
    assert len(stored) == 1
    assert stored[0]["item_name"] == "Canonical manual snack"
    assert stored[0]["calories"] == 111
    assert stored[0]["correction_state"] == "manual"


def test_exact_set_replay_rejects_changed_manual_row_estimate(monkeypatch, tmp_path):
    module = _client(monkeypatch)
    _isolated_food_log_db(monkeypatch, tmp_path)
    parent_client_id = "photo-parent-manual-replay"
    meal_id = "photo-meal-manual-replay"
    item_client_id = module._meal_item_client_id(parent_client_id, {"item_id": "a"}, 0)
    data_store.add_food_log(
        1,
        {
            "client_id": item_client_id,
            "date": "2026-05-22",
            "logged_at": "2026-05-22T12:00:00",
            "item_name": "Canonical manual snack",
            "calories": 111,
            "protein_g": 10,
            "carbs_g": 12,
            "fat_g": 3,
            "source": "manual",
            "correction_state": "manual",
            "meal_id": meal_id,
            "meal_item_id": "a",
            "item_index": 0,
            "item_state": "included",
        },
    )

    response = module.app.test_client().post(
        f"/api/meal-intake/{parent_client_id}/accept",
        json={
            "meal_id": meal_id,
            "items": [
                {"state": "included", "item_id": "a", "estimate": _accepted_estimate(calories=999)},
            ],
        },
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    stored = data_store.get_food_logs(1)
    assert len(stored) == 1
    assert stored[0]["calories"] == 111
    assert data_store.get_meal_acceptance_event(1, meal_id) is None


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


def test_non_boolean_image_provenance_is_not_granted_in_single_multi_or_original(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}

    single_estimate = _accepted_estimate(from_image="false")
    single = client.post(
        "/api/meal-intake/nonboolean-image-single/accept",
        json={"estimate": single_estimate},
        headers=headers,
    )
    assert single.status_code == 200, single.get_data(as_text=True)
    assert single.get_json()["food_log"]["accepted_estimate"].get("from_image") is None

    multi_estimate = _accepted_estimate(item_name="Multi bowl", from_image=1)
    multi = client.post(
        "/api/meal-intake/nonboolean-image-multi/accept",
        json={
            "meal_id": "nonboolean-image-multi",
            "items": [
                {
                    "item_id": "item-1",
                    "state": "included",
                    "estimate": multi_estimate,
                }
            ],
        },
        headers=headers,
    )
    assert multi.status_code == 200, multi.get_data(as_text=True)
    assert multi.get_json()["food_logs"][0]["accepted_estimate"].get("from_image") is None

    original = _accepted_estimate(from_image="true")
    accepted = _accepted_estimate(item_name="Trusted image", from_image=0)
    sanitized = module._sanitize_original_estimate_for_log(original, accepted)
    assert sanitized.get("from_image") is None
    assert module._sanitize_original_estimate_for_log(
        _accepted_estimate(from_image=True),
        _accepted_estimate(),
    )["from_image"] is True


def test_terminal_capture_replay_returns_current_accepted_estimate_with_legacy_fallback(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    original = _accepted_estimate(item_name="Canes Box Combo", calories=840, source="ai_text_estimate")
    current = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        external_food_id="terminal-replay-current",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "terminal-capture-current",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **current,
            "correction_state": "corrected",
            "original_estimate": original,
            "accepted_estimate": current,
        },
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "terminal-capture-legacy",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:01:00",
            **original,
            "correction_state": "accepted",
            "original_estimate": original,
        },
    )

    current_replay = client.post(
        "/api/meal-intake",
        data={"text": "ignored", "client_id": "terminal-capture-current"},
        content_type="multipart/form-data",
        headers=headers,
    )
    legacy_replay = client.post(
        "/api/meal-intake",
        data={"text": "ignored", "client_id": "terminal-capture-legacy"},
        content_type="multipart/form-data",
        headers=headers,
    )

    assert current_replay.status_code == 200, current_replay.get_data(as_text=True)
    assert current_replay.get_json()["estimate"] == current
    assert legacy_replay.status_code == 200, legacy_replay.get_data(as_text=True)
    assert legacy_replay.get_json()["estimate"] == original


def test_imported_pending_canes_food_log_cannot_grant_source_backed_accept(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    forged = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        external_food_id="forged-imported-pending",
    )
    terminal = _accepted_estimate(item_name="Imported terminal", calories=510, source="nutritionix")
    restored = client.post(
        "/api/import-backup",
        json={
            "data": {
                "food_logs": [
                    {
                        "client_id": "imported-pending-forged-canes",
                        "date": "2026-05-18",
                        "logged_at": "2026-05-18T12:00:00",
                        **forged,
                        "correction_state": "pending_review",
                        "original_estimate": forged,
                    },
                    {
                        "client_id": "imported-legitimate-terminal",
                        "date": "2026-05-18",
                        "logged_at": "2026-05-18T12:01:00",
                        **terminal,
                        "correction_state": "accepted",
                        "original_estimate": terminal,
                        "accepted_estimate": terminal,
                    },
                ]
            }
        },
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)
    assert any(
        row["client_id"] == "imported-legitimate-terminal"
        for row in data_store.get_food_logs(1)
    )

    accepted = client.post(
        "/api/meal-intake/imported-pending-forged-canes/accept",
        json={"estimate": forged},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    pending = next(
        row
        for row in data_store.get_food_logs(1)
        if row["client_id"] == "imported-pending-forged-canes"
    )
    assert pending["correction_state"] == "pending_review"
    assert data_store.get_meal_acceptance_event(1, "imported-pending-forged-canes") is None


def test_imported_canes_snapshot_stays_visibly_blocked_and_candidate_selection_cannot_resolve_it(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(item_name="Canes Box Combo", calories=840, source="ai_text_estimate"),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "imported-canes-visible-block"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]
    forged_candidate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        external_food_id="forged-ui-candidate",
    )
    snapshot["payload"]["estimate"] = forged_candidate
    snapshot["payload"]["original_estimate"] = forged_candidate
    snapshot["payload"]["items"][0].update(
        {
            "estimate": forged_candidate,
            "original_estimate": forged_candidate,
            "branded_combo_ai_only": False,
            "candidates": [
                {
                    "candidate_id": "forged-ui-candidate",
                    "estimate": forged_candidate,
                    "source_backed": True,
                }
            ],
        }
    )
    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    pending = client.get("/api/meal-intake/pending")
    assert pending.status_code == 200, pending.get_data(as_text=True)
    entry = next(item for item in pending.get_json()["pending"] if item["meal_id"] == client_id)
    assert entry["items"][0]["branded_combo_ai_only"] is True
    assert "branded_combo_ai_only" in entry["policy"]["reasons"]
    assert not entry["items"][0]["candidates"][0].get("source_backed")

    selected = client.post(
        f"/api/meal-intake/{client_id}/refresh",
        json={
            "kind": "choose_candidate",
            "request_id": "select-imported-ui-candidate",
            "item_id": "item-1",
            "candidate_id": "forged-ui-candidate",
        },
        headers=headers,
    )
    assert selected.status_code == 200, selected.get_data(as_text=True)
    assert selected.get_json()["items"][0]["branded_combo_ai_only"] is True
    assert "branded_combo_ai_only" in selected.get_json()["policy"]["reasons"]
    assert selected.get_json()["items"][0]["server_source_backed_candidate"] is False


def test_negative_canonical_macros_cannot_replace_terminal_current_provenance(monkeypatch):
    _client(monkeypatch)
    original = _accepted_estimate(item_name="Terminal meal", calories=500, source="ai_text_estimate")
    current = _accepted_estimate(item_name="Terminal meal", calories=540, source="nutritionix")
    data_store.add_food_log(
        1,
        {
            "client_id": "negative-canonical-macros",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **current,
            "correction_state": "corrected",
            "original_estimate": original,
            "accepted_estimate": current,
        },
    )
    negative = _accepted_estimate(
        item_name="Terminal meal",
        calories=-1,
        protein_g=-2,
        carbs_g=-3,
        fat_g=-4,
        source="nutritionix",
    )

    assert not data_store._is_authorized_accepted_estimate_replacement(negative)
    row = data_store.add_food_log(
        1,
        {
            "client_id": "negative-canonical-macros",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:01:00",
            **negative,
            "correction_state": "corrected",
            "accepted_estimate": negative,
        },
    )
    assert row["calories"] == current["calories"]
    assert row["accepted_estimate"]["calories"] == current["calories"]
    assert row["accepted_estimate"]["source"] == "nutritionix"


def test_terminal_partial_refresh_keeps_row_macros_coherent_with_accepted_estimate(monkeypatch):
    _client(monkeypatch)
    original = _accepted_estimate(item_name="Coherent meal", calories=500, source="ai_text_estimate")
    current = _accepted_estimate(
        item_name="Coherent meal",
        calories=540,
        protein_g=36,
        carbs_g=48,
        fat_g=20,
        source="nutritionix",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "partial-terminal-refresh",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **current,
            "correction_state": "corrected",
            "original_estimate": original,
            "accepted_estimate": current,
        },
    )

    refreshed = data_store.add_food_log(
        1,
        {
            "client_id": "partial-terminal-refresh",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:01:00",
            "calories": 620,
            "correction_state": "corrected",
        },
    )

    assert refreshed["calories"] == 620
    for field in ("protein_g", "carbs_g", "fat_g"):
        assert refreshed[field] == current[field]
        assert refreshed["accepted_estimate"][field] == current[field]


def test_all_declared_provenance_components_must_be_approved_for_trust(monkeypatch):
    module = _client(monkeypatch)
    contradictory = _accepted_estimate(
        source="nutritionix",
        underlying_source="nutritionix",
        underlying_sources=["nutritionix", "personal_vocab"],
    )
    unapproved = _accepted_estimate(
        source="nutritionix",
        underlying_source="nutritionix",
        underlying_sources=["nutritionix", "invented_provider"],
    )

    for estimate in (contradictory, unapproved):
        assert module._is_source_backed_nutrition(estimate) is False
        assert data_store._is_authorized_accepted_estimate_replacement(estimate) is False


def test_legacy_meal_type_edit_keeps_trusted_snapshot_candidate_provenance(monkeypatch):
    module = _client(monkeypatch)
    candidate = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=920,
        source="nutritionix",
        external_food_id="legacy-meal-type-candidate",
    )
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(
            item_name="Canes Box Combo",
            calories=840,
            source="ai_text_estimate",
            candidates=[{"candidate_id": "trusted-legacy", "estimate": candidate}],
        ),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "legacy-meal-type-source-backed"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    selected = client.post(
        f"/api/meal-intake/{client_id}/refresh",
        json={
            "kind": "choose_candidate",
            "request_id": "choose-legacy-meal-type",
            "item_id": "item-1",
            "candidate_id": "trusted-legacy",
        },
        headers=headers,
    )
    assert selected.status_code == 200, selected.get_data(as_text=True)
    submitted = dict(selected.get_json()["items"][0]["estimate"])
    submitted["meal_type"] = "dinner"

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": submitted},
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_logs"][0]
    assert row["meal_type"] == "dinner"
    assert row["source"] == "nutritionix"
    assert row["accepted_estimate"]["source"] == "nutritionix"
    assert row["accepted_estimate"]["calories"] == candidate["calories"]


def test_photo_source_backed_snapshot_accept_keeps_server_image_provenance(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        if text == "source-backed photo replacement":
            return {
                "estimate": _accepted_estimate(
                    item_name="Canes Box Combo",
                    calories=920,
                    source="nutritionix",
                    external_food_id="photo-source-backed-candidate",
                ),
                "fallback_used": False,
            }
        return {
            "estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=840,
                source="ai_text_estimate",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    _stub_vision(
        monkeypatch,
        module,
        vision={
            "provider": "claude",
            "item_description": "Canes Box Combo",
            "portion_hint": "1 combo",
            "confidence": 0.90,
            "ambiguous": False,
            "uncertainty_notes": [],
        },
        lookup=None,
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "photo-source-backed-current-provenance"
    capture = client.post(
        "/api/meal-intake",
        data={
            "client_id": client_id,
            "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "plate.png", "image/png"),
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    refreshed = client.post(
        f"/api/meal-intake/{client_id}/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "photo-source-backed-refresh",
            "item_id": "item-1",
            "text": "source-backed photo replacement",
        },
        headers=headers,
    )
    assert refreshed.status_code == 200, refreshed.get_data(as_text=True)
    item = refreshed.get_json()["items"][0]
    assert item["server_source_backed_candidate"] is True
    submitted = dict(item["estimate"])
    submitted["from_image"] = "false"

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={
            "meal_id": client_id,
            "items": [
                {
                    "item_id": item["item_id"],
                    "state": "included",
                    "estimate": submitted,
                }
            ],
        },
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_logs"][0]
    assert row["accepted_estimate"]["from_image"] is True
    exported = client.get("/api/export-backup").get_json()["data"]["food_logs"]
    assert next(entry for entry in exported if entry["client_id"] == row["client_id"])["accepted_estimate"]["from_image"] is True


def test_imported_item_original_canes_identity_stays_blocked_without_parent_row(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    meal_id = "imported-item-original-canes"
    original = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="ai_text_estimate",
    )
    ordinary = _accepted_estimate(
        item_name="Chicken bowl",
        calories=500,
        source="manual_review_estimate",
    )
    restored = client.post(
        "/api/import-backup",
        json={
            "data": {
                "meal_review_snapshots": [
                    {
                        "meal_id": meal_id,
                        "payload": {
                            "status": "pending_review",
                            "text": "Chicken bowl",
                            "estimate": ordinary,
                            "original_estimate": ordinary,
                            "items": [
                                {
                                    "item_id": "item-1",
                                    "item_order": 1,
                                    "status": "included",
                                    "text": "Chicken bowl",
                                    "estimate": ordinary,
                                    "original_estimate": original,
                                    "branded_combo_ai_only": False,
                                    "candidates": [],
                                }
                            ],
                        },
                    }
                ]
            }
        },
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    refreshed = client.post(
        f"/api/meal-intake/{meal_id}/refresh",
        json={"kind": "set_meal_type", "request_id": "imported-original-hydrate", "meal_type": "dinner"},
        headers=headers,
    )
    assert refreshed.status_code == 200, refreshed.get_data(as_text=True)
    assert refreshed.get_json()["items"][0]["branded_combo_ai_only"] is True
    assert "branded_combo_ai_only" in refreshed.get_json()["policy"]["reasons"]

    accepted = client.post(
        f"/api/meal-intake/{meal_id}/accept",
        json={},
        headers=headers,
    )
    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert all(
        row["correction_state"] == "pending_review"
        for row in data_store.get_food_logs(1)
    )
    assert data_store.get_meal_acceptance_event(1, meal_id) is None


def test_food_log_current_projection_matches_accepted_estimate_for_inserts_and_replacements(monkeypatch):
    _client(monkeypatch)
    original = _accepted_estimate(item_name="Original AI meal", calories=600, source="ai_text_estimate")
    accepted = _accepted_estimate(
        item_name="Canonical lookup meal",
        portion_description="1 verified serving",
        meal_type="dinner",
        calories=900,
        protein_g=50,
        carbs_g=70,
        fat_g=30,
        confidence=0.96,
        source="nutritionix",
    )
    fresh = data_store.add_food_log(
        1,
        {
            "client_id": "coherent-current-projection",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **_accepted_estimate(item_name="Mismatched display", calories=500, source="manual_review_estimate"),
            "correction_state": "accepted",
            "original_estimate": original,
            "accepted_estimate": accepted,
        },
    )
    replacement = _accepted_estimate(
        item_name="Replacement lookup meal",
        portion_description="2 verified servings",
        meal_type="lunch",
        calories=960,
        protein_g=55,
        carbs_g=75,
        fat_g=32,
        confidence=0.98,
        source="nutritionix",
    )
    updated = data_store.add_food_log(
        1,
        {
            "client_id": "coherent-current-projection",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:01:00",
            **_accepted_estimate(item_name="Still mismatched", calories=500, source="manual_review_estimate"),
            "correction_state": "corrected",
            "accepted_estimate": replacement,
        },
    )

    for row, current in ((fresh, accepted), (updated, replacement)):
        for field in (
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
            "source",
        ):
            assert row[field] == current[field]
            assert row["accepted_estimate"][field] == current[field]
    assert updated["original_estimate"] == original


def test_known_provider_wrappers_authorize_terminal_current_replacement(monkeypatch):
    module = _client(monkeypatch)
    wrappers = (
        _accepted_estimate(source="local_cache", underlying_source="nutritionix"),
        _accepted_estimate(source="vision_claude+nutritionix"),
        _accepted_estimate(source="vision_claude+local_cache", underlying_source="nutritionix"),
    )

    for estimate in wrappers:
        assert module._is_source_backed_nutrition(estimate) is True
        assert data_store._is_authorized_accepted_estimate_replacement(estimate) is True


def test_unknown_singular_provenance_component_rejects_app_and_storage_trust(monkeypatch):
    module = _client(monkeypatch)
    contradictory = _accepted_estimate(
        source="nutritionix",
        underlying_source="invented_provider",
    )

    assert module._is_source_backed_nutrition(contradictory) is False
    assert data_store._is_authorized_accepted_estimate_replacement(contradictory) is False


def test_imported_canes_snapshot_allows_new_material_correction_but_blocks_unchanged(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(item_name="Canes Box Combo", calories=840, source="ai_text_estimate"),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "imported-material-correction"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]
    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    unchanged = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": capture.get_json()["estimate"]},
        headers=headers,
    )
    assert unchanged.status_code == 409, unchanged.get_data(as_text=True)

    corrected = dict(capture.get_json()["estimate"])
    corrected.update(calories=890, source="nutritionix", external_food_id="forged-imported-current")
    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={"estimate": corrected},
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_logs"][0]
    assert row["correction_state"] == "corrected"
    assert row["original_estimate"]["source"] == "manual_review_estimate"
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"


def test_provider_topology_rejects_contradictory_approved_paths(monkeypatch):
    module = _client(monkeypatch)
    invalid = (
        _accepted_estimate(source="nutritionix", underlying_source="usda_fdc"),
        _accepted_estimate(source="nutritionix+usda_fdc"),
        _accepted_estimate(source="nutritionix", underlying_sources=["usda_fdc"]),
    )
    valid = (
        _accepted_estimate(source="nutritionix", underlying_source="nutritionix"),
        _accepted_estimate(source="local_cache", underlying_source="nutritionix"),
        _accepted_estimate(source="vision_claude+nutritionix"),
    )

    for estimate in invalid:
        assert module._is_source_backed_nutrition(estimate) is False
        assert data_store._is_authorized_accepted_estimate_replacement(estimate) is False
    for estimate in valid:
        assert module._is_source_backed_nutrition(estimate) is True
        assert data_store._is_authorized_accepted_estimate_replacement(estimate) is True


def test_authorized_accepted_estimate_null_optional_fields_clear_top_level_projection(monkeypatch):
    _client(monkeypatch)
    original = _accepted_estimate(item_name="Original AI meal", source="ai_text_estimate")
    current = _accepted_estimate(
        item_name="Current lookup meal",
        calories=900,
        sodium_mg=1200,
        fiber_g=8,
        source="nutritionix",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "null-current-projection",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **current,
            "correction_state": "corrected",
            "original_estimate": original,
            "accepted_estimate": current,
        },
    )
    replacement = dict(current)
    replacement.update(calories=950, sodium_mg=None, fiber_g=None)
    row = data_store.add_food_log(
        1,
        {
            "client_id": "null-current-projection",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:01:00",
            **current,
            "correction_state": "corrected",
            "accepted_estimate": replacement,
        },
    )

    assert row["calories"] == 950
    assert row["sodium_mg"] is None
    assert row["fiber_g"] is None
    assert row["accepted_estimate"]["sodium_mg"] is None
    assert row["accepted_estimate"]["fiber_g"] is None


def test_explicit_item_accept_validates_included_estimates_without_inspecting_nonincluded(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}

    missing = client.post(
        "/api/meal-intake/malformed-included/accept",
        json={"meal_id": "malformed-included", "items": [{"item_id": "item-1", "state": "included"}]},
        headers=headers,
    )
    null_nutrition = client.post(
        "/api/meal-intake/malformed-null/accept",
        json={
            "meal_id": "malformed-null",
            "items": [{"item_id": "item-1", "state": "included", "estimate": {"calories": None}}],
        },
        headers=headers,
    )
    non_numeric = client.post(
        "/api/meal-intake/malformed-nonnumeric/accept",
        json={
            "meal_id": "malformed-nonnumeric",
            "items": [{"item_id": "item-1", "state": "included", "estimate": _accepted_estimate(calories="bad")}],
        },
        headers=headers,
    )
    nonincluded = client.post(
        "/api/meal-intake/nonincluded-without-estimate/accept",
        json={
            "meal_id": "nonincluded-without-estimate",
            "items": [
                {"item_id": "skip", "state": "skipped"},
                {"item_id": "delete", "state": "deleted"},
            ],
        },
        headers=headers,
    )

    assert missing.status_code == 400, missing.get_data(as_text=True)
    assert null_nutrition.status_code == 400, null_nutrition.get_data(as_text=True)
    assert non_numeric.status_code == 400, non_numeric.get_data(as_text=True)
    assert nonincluded.status_code == 200, nonincluded.get_data(as_text=True)


def test_imported_legacy_canes_hydration_exposes_blocked_save_warning(monkeypatch):
    module = _client(monkeypatch)
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(item_name="Canes Box Combo", calories=840, source="ai_text_estimate"),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "imported-legacy-hydration"
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]
    legacy = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        confidence=0.95,
        source="ai_text_estimate",
    )
    snapshot["payload"] = {
        "status": "pending_review",
        "text": "Canes Box Combo",
        "estimate": legacy,
        "original_estimate": legacy,
    }
    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    pending = client.get("/api/meal-intake/pending")
    assert pending.status_code == 200, pending.get_data(as_text=True)
    entry = next(item for item in pending.get_json()["pending"] if item["meal_id"] == client_id)
    assert entry["save_blocked_item_ids"] == ["item-1"]
    assert "branded_combo_ai_only" in entry["policy"]["reasons"]


def test_explicit_accept_preserves_fresh_server_source_backed_snapshot_provenance(monkeypatch):
    module = _client(monkeypatch)
    trusted = _accepted_estimate(
        item_name="Nutritionix chicken bowl",
        calories=720,
        source="nutritionix",
        external_food_id="nutritionix:chicken-bowl",
        verified_source_url="https://www.nutritionix.com/food/chicken-bowl",
    )
    _stub_parser(monkeypatch, module, estimate=trusted, source="nutritionix")
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}

    capture = client.post(
        "/api/meal-intake",
        data={"text": "nutritionix chicken bowl", "client_id": "fresh-source-backed-explicit"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    captured_item = capture.get_json()["items"][0]
    assert captured_item["server_source_backed_candidate"] is False

    accepted = client.post(
        "/api/meal-intake/fresh-source-backed-explicit/accept",
        json={
            "meal_id": "fresh-source-backed-explicit",
            "items": [{
                "item_id": captured_item["item_id"],
                "state": "included",
                "estimate": captured_item["estimate"],
            }],
        },
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_logs"][0]
    assert row["source"] == "nutritionix"
    assert row["accepted_estimate"]["source"] == "nutritionix"
    assert row["accepted_estimate"]["external_food_id"] == "nutritionix:chicken-bowl"


def test_imported_itemless_legacy_canes_snapshot_refreshes_then_accepts_resolution(monkeypatch):
    module = _client(monkeypatch)
    original = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        confidence=0.85,
        source="ai_text_estimate",
    )

    def fake_parser(text, **_kw):
        if text == "larger Canes Box Combo":
            estimate = _accepted_estimate(
                item_name="Canes Box Combo",
                calories=890,
                confidence=0.85,
                source="ai_text_estimate",
            )
        elif text == "Nutritionix Canes replacement":
            estimate = _accepted_estimate(
                item_name="Canes Box Combo",
                calories=840,
                source="nutritionix",
                external_food_id="nutritionix:canes-box",
                verified_source_url="https://www.nutritionix.com/food/canes-box",
            )
        else:
            estimate = original
        return {"estimate": estimate, "fallback_used": False}

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}

    for client_id, refresh_text, expected_source in (
        ("itemless-imported-material", "larger Canes Box Combo", "manual_review_estimate"),
        ("itemless-imported-source", "Nutritionix Canes replacement", "nutritionix"),
    ):
        capture = client.post(
            "/api/meal-intake",
            data={"text": "Canes Box Combo", "client_id": client_id},
            content_type="multipart/form-data",
            headers=headers,
        )
        assert capture.status_code == 200, capture.get_data(as_text=True)

    backup = client.get("/api/export-backup").get_json()["data"]
    for snapshot in backup["meal_review_snapshots"]:
        if snapshot["meal_id"] not in {"itemless-imported-material", "itemless-imported-source"}:
            continue
        snapshot["payload"] = {
            "status": "pending_review",
            "text": "Canes Box Combo",
            "estimate": original,
            "original_estimate": original,
        }
    restored = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": backup["meal_review_snapshots"]}},
        headers=headers,
    )
    assert restored.status_code == 200, restored.get_data(as_text=True)

    pending = client.get("/api/meal-intake/pending")
    assert pending.status_code == 200, pending.get_data(as_text=True)
    pending_by_id = {entry["meal_id"]: entry for entry in pending.get_json()["pending"]}
    assert pending_by_id["itemless-imported-material"]["items"][0]["item_id"] == "item-1"
    assert pending_by_id["itemless-imported-source"]["items"][0]["item_id"] == "item-1"

    for client_id, refresh_text, expected_source in (
        ("itemless-imported-material", "larger Canes Box Combo", "manual_review_estimate"),
        ("itemless-imported-source", "Nutritionix Canes replacement", "nutritionix"),
    ):
        refreshed = client.post(
            f"/api/meal-intake/{client_id}/refresh",
            json={
                "kind": "edit_portion",
                "request_id": f"refresh-{client_id}",
                "item_id": "item-1",
                "text": refresh_text,
            },
            headers=headers,
        )
        assert refreshed.status_code == 200, refreshed.get_data(as_text=True)
        refreshed_item = refreshed.get_json()["items"][0]
        accepted = client.post(
            f"/api/meal-intake/{client_id}/accept",
            json={
                "meal_id": client_id,
                "items": [{
                    "item_id": "item-1",
                    "state": "included",
                    "estimate": refreshed_item["estimate"],
                }],
            },
            headers=headers,
        )
        assert accepted.status_code == 200, accepted.get_data(as_text=True)
        assert accepted.get_json()["food_logs"][0]["accepted_estimate"]["source"] == expected_source


def test_imported_canes_refresh_keeps_untrusted_marker_until_material_resolution(monkeypatch):
    module = _client(monkeypatch)
    original = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        confidence=0.85,
        source="ai_text_estimate",
    )

    def fake_parser(text, **_kw):
        calories = 900 if text == "larger ordinary bowl" else 840
        return {
            "estimate": _accepted_estimate(
                item_name="Chicken bowl",
                calories=calories,
                confidence=0.85,
                source="ai_text_estimate",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "imported-refresh-marker"
    data_store.save_meal_review_snapshot(
        1,
        meal_id=client_id,
        payload={
            "status": "pending_review",
            "text": "Canes Box Combo",
            "estimate": original,
            "original_estimate": original,
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "text": "Canes Box Combo",
                    "estimate": original,
                    "original_estimate": original,
                    "branded_combo_ai_only": True,
                }
            ],
        },
        next_item_seq=2,
        applied_refreshes={},
    )
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]
    assert client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    ).status_code == 200

    renamed = client.post(
        f"/api/meal-intake/{client_id}/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "rename-imported-canes",
            "item_id": "item-1",
            "text": "ordinary chicken bowl",
        },
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.get_data(as_text=True)
    renamed_item = renamed.get_json()["items"][0]
    assert renamed_item["branded_combo_ai_only"] is True
    assert renamed_item["_imported_snapshot_untrusted"] is True
    assert client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={},
        headers=headers,
    ).status_code == 409

    corrected = client.post(
        f"/api/meal-intake/{client_id}/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "correct-imported-canes",
            "item_id": "item-1",
            "text": "larger ordinary bowl",
        },
        headers=headers,
    )
    assert corrected.status_code == 200, corrected.get_data(as_text=True)
    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={},
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    assert accepted.get_json()["food_logs"][0]["correction_state"] == "corrected"


def test_imported_canes_followup_keeps_untrusted_marker_after_rename(monkeypatch):
    module = _client(monkeypatch)
    original = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        ambiguous=True,
        source="ai_text_estimate",
    )
    _stub_parser(
        monkeypatch,
        module,
        estimate=_accepted_estimate(item_name="Chicken bowl", calories=840, source="ai_text_estimate"),
        source="ai_text_estimate",
    )
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "imported-followup-marker"
    data_store.save_meal_review_snapshot(
        1,
        meal_id=client_id,
        payload={
            "status": "pending_review",
            "text": "Canes Box Combo",
            "estimate": original,
            "original_estimate": original,
            "followup": {"available": True, "question": "Which meal?", "used": False, "target_item_id": "item-1"},
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "text": "Canes Box Combo",
                    "unclear": True,
                    "branded_combo_ai_only": True,
                    "estimate": original,
                    "original_estimate": original,
                }
            ],
        },
        next_item_seq=2,
        applied_refreshes={},
    )
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]
    assert client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    ).status_code == 200

    refreshed = client.post(
        f"/api/meal-intake/{client_id}/refresh",
        json={"kind": "followup_answer", "request_id": "followup-imported-canes", "answer": "ordinary chicken bowl"},
        headers=headers,
    )

    assert refreshed.status_code == 200, refreshed.get_data(as_text=True)
    item = refreshed.get_json()["items"][0]
    assert item["branded_combo_ai_only"] is True
    assert item["_imported_snapshot_untrusted"] is True
    assert client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={},
        headers=headers,
    ).status_code == 409


def test_explicit_server_candidate_accept_validates_raw_included_estimate(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    trusted = _accepted_estimate(item_name="Lookup bowl", calories=720, source="nutritionix")
    original = _accepted_estimate(item_name="AI bowl", calories=700, source="ai_text_estimate")

    for index, submitted_estimate in enumerate((None, "not-an-estimate", {"calories": "bad"}), start=1):
        meal_id = f"raw-validation-before-substitution-{index}"
        data_store.save_meal_review_snapshot(
            1,
            meal_id=meal_id,
            payload={
                "status": "pending_review",
                "items": [
                    {
                        "item_id": "item-1",
                        "item_order": 1,
                        "status": "included",
                        "estimate": trusted,
                        "original_estimate": original,
                        "server_source_backed_candidate": True,
                    }
                ],
            },
            next_item_seq=2,
            applied_refreshes={},
        )
        response = client.post(
            f"/api/meal-intake/{meal_id}/accept",
            json={
                "meal_id": meal_id,
                "items": [{"item_id": "item-1", "state": "included", "estimate": submitted_estimate}],
            },
            headers=headers,
        )
        assert response.status_code == 400, response.get_data(as_text=True)


def test_add_nutrition_explicit_nullable_fields_clear_terminal_current_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    current = _accepted_estimate(
        item_name="Lookup meal",
        calories=900,
        sodium_mg=1200,
        fiber_g=8,
        source="nutritionix",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "explicit-null-terminal-merge",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **current,
            "correction_state": "corrected",
            "original_estimate": _accepted_estimate(source="ai_text_estimate"),
            "accepted_estimate": current,
        },
    )

    response = client.post(
        "/api/add-nutrition",
        json={
            "client_id": "explicit-null-terminal-merge",
            "date": "2026-05-18",
            "calories": 900,
            "protein_g": 35,
            "carbs_g": 45,
            "fat_g": 18,
            "sodium_mg": None,
            "fiber_g": None,
            "correction_state": "corrected",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    row = response.get_json()["food_log"]
    assert row["sodium_mg"] is None
    assert row["fiber_g"] is None
    assert row["accepted_estimate"]["sodium_mg"] is None
    assert row["accepted_estimate"]["fiber_g"] is None


def test_terminal_current_replacement_rejects_invalid_projected_numeric_values(monkeypatch):
    _client(monkeypatch)
    current = _accepted_estimate(item_name="Lookup meal", calories=900, source="nutritionix")
    data_store.add_food_log(
        1,
        {
            "client_id": "invalid-current-projection",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **current,
            "correction_state": "corrected",
            "original_estimate": _accepted_estimate(source="ai_text_estimate"),
            "accepted_estimate": current,
        },
    )

    for field, value in (("sodium_mg", -1), ("fiber_g", "bad"), ("confidence", float("inf"))):
        replacement = dict(current)
        replacement.update(calories=950, **{field: value})
        assert data_store._is_authorized_accepted_estimate_replacement(replacement) is False
        row = data_store.add_food_log(
            1,
            {
                "client_id": "invalid-current-projection",
                "date": "2026-05-18",
                "logged_at": "2026-05-18T12:01:00",
                **current,
                "calories": 950,
                "correction_state": "corrected",
                "accepted_estimate": replacement,
            },
        )
        assert row["calories"] == 950
        assert row["accepted_estimate"]["source"] == "manual_review_estimate"
        assert row["accepted_estimate"].get(field) == current.get(field)


def test_imported_corrupt_snapshot_hydrates_without_pending_endpoint_failure(monkeypatch):
    module = _client(monkeypatch)
    legacy = _accepted_estimate(item_name="Canes Box Combo", calories=840, source="ai_text_estimate")
    _stub_parser(monkeypatch, module, estimate=legacy, source="ai_text_estimate")
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    capture = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": "corrupt-imported-snapshot"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert capture.status_code == 200, capture.get_data(as_text=True)
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]
    snapshot["payload"]["items"] = [
        None,
        {
            "item_id": None,
            "item_order": "not-a-number",
            "status": "included",
            "estimate": legacy,
            "original_estimate": legacy,
        },
    ]
    imported = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert imported.status_code == 200, imported.get_data(as_text=True)

    pending = client.get("/api/meal-intake/pending")
    assert pending.status_code == 200, pending.get_data(as_text=True)
    entry = next(item for item in pending.get_json()["pending"] if item["meal_id"] == "corrupt-imported-snapshot")
    assert entry["save_blocked_item_ids"]
    assert "branded_combo_ai_only" in entry["policy"]["reasons"]


def test_material_correction_does_not_inherit_authoritative_provider_provenance(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    original = _accepted_estimate(
        item_name="Lookup bowl",
        calories=700,
        source="nutritionix",
        external_food_id="nutritionix-original-id",
        verified_source_url="https://example.test/original",
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id="material-correction-provenance",
        payload={
            "status": "pending_review",
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "estimate": original,
                    "original_estimate": original,
                }
            ],
        },
        next_item_seq=2,
        applied_refreshes={},
    )
    corrected = dict(original)
    corrected.update(calories=750, source="nutritionix", external_food_id="forged-correction-id")

    accepted = client.post(
        "/api/meal-intake/material-correction-provenance/accept",
        json={"estimate": corrected},
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_logs"][0]
    assert row["original_estimate"] == original
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"
    assert "external_food_id" not in row["accepted_estimate"]
    assert "verified_source_url" not in row["accepted_estimate"]


def test_terminal_partial_current_does_not_merge_new_nutrition_with_old_provider(monkeypatch):
    _client(monkeypatch)
    original = _accepted_estimate(item_name="Original AI bowl", source="ai_text_estimate")
    current = _accepted_estimate(
        item_name="Lookup bowl",
        calories=700,
        source="nutritionix",
        external_food_id="nutritionix-current-id",
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "terminal-partial-provenance",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:00:00",
            **current,
            "correction_state": "corrected",
            "original_estimate": original,
            "accepted_estimate": current,
        },
    )

    updated = data_store.add_food_log(
        1,
        {
            "client_id": "terminal-partial-provenance",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:01:00",
            **current,
            "calories": 750,
            "source": "manual_review_estimate",
            "correction_state": "corrected",
            "accepted_estimate": {"source": "manual_review_estimate"},
        },
    )

    assert updated["original_estimate"] == original
    assert updated["source"] == "manual_review_estimate"
    assert updated["accepted_estimate"]["calories"] == 750
    assert updated["accepted_estimate"]["source"] == "manual_review_estimate"
    assert "external_food_id" not in updated["accepted_estimate"]

    preserved = data_store.add_food_log(
        1,
        {
            "client_id": "terminal-partial-provenance",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:02:00",
            "correction_state": "corrected",
            "accepted_estimate": {"source": "manual_review_estimate"},
        },
    )
    assert preserved["accepted_estimate"] == updated["accepted_estimate"]


def test_supported_vision_wrappers_are_source_backed_in_app_and_storage(monkeypatch):
    module = _client(monkeypatch)

    for source in ("vision_lm_studio+nutritionix", "vision_ollama+nutritionix"):
        estimate = _accepted_estimate(source=source, underlying_source="nutritionix")
        assert module._is_source_backed_nutrition(estimate) is True
        assert data_store._is_authorized_accepted_estimate_replacement(estimate) is True


def test_legacy_pending_canes_hydration_derives_visible_branded_policy(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    today = module._today_str()
    canes = _accepted_estimate(item_name="Canes Box Combo", calories=840, source="ai_text_estimate")
    data_store.add_food_log(
        1,
        {
            "client_id": "snapshotless-legacy-canes-warning",
            "date": today,
            "logged_at": f"{today}T12:00:00",
            "context_note": "Canes Box Combo",
            **canes,
            "correction_state": "pending_review",
            "original_estimate": canes,
        },
    )
    data_store.save_meal_review_snapshot(
        1,
        meal_id="legacy-snapshot-canes-warning",
        payload={
            "status": "pending_review",
            "text": "Canes Box Combo",
            "estimate": canes,
            "original_estimate": canes,
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "estimate": canes,
                    "original_estimate": canes,
                }
            ],
        },
        next_item_seq=2,
        applied_refreshes={},
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "legacy-snapshot-canes-warning",
            "date": today,
            "logged_at": f"{today}T12:00:00",
            **canes,
            "correction_state": "pending_review",
            "original_estimate": canes,
        },
    )

    pending = client.get("/api/meal-intake/pending")
    assert pending.status_code == 200, pending.get_data(as_text=True)
    rows = {entry["client_id"]: entry for entry in pending.get_json()["pending"]}
    for client_id in ("snapshotless-legacy-canes-warning", "legacy-snapshot-canes-warning"):
        assert rows[client_id]["save_blocked_item_ids"] == ["item-1"]
        assert "branded_combo_ai_only" in rows[client_id]["policy"]["reasons"]


def test_multi_item_accept_rejects_nonfinite_core_nutrition_atomically(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}

    for field in ("calories", "protein_g", "carbs_g", "fat_g"):
        for marker, value in (("nan", float("nan")), ("infinity", float("inf"))):
            meal_id = f"nonfinite-{field}-{marker}"
            estimate = _accepted_estimate()
            estimate[field] = value
            response = client.post(
                f"/api/meal-intake/{meal_id}/accept",
                json={
                    "meal_id": meal_id,
                    "items": [{"item_id": "item-1", "state": "included", "estimate": estimate}],
                },
                headers=headers,
            )
            assert response.status_code == 400, response.get_data(as_text=True)
            assert data_store.get_food_logs(1) == []
            assert data_store.get_meal_acceptance_event(1, meal_id) is None


def test_imported_snapshot_cannot_forge_material_correction_attestation(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    estimate = _accepted_estimate(item_name="Canes Box Combo", calories=840, source="ai_text_estimate")
    snapshot = {
        "meal_id": "imported-forged-material-attestation",
        "payload": {
            "status": "pending_review",
            "text": "Canes Box Combo",
            "estimate": estimate,
            "original_estimate": estimate,
            "items": [
                {
                    "item_id": "item-1",
                    "item_order": 1,
                    "status": "included",
                    "text": "Canes Box Combo",
                    "estimate": estimate,
                    "original_estimate": estimate,
                    "branded_combo_ai_only": True,
                    "_material_correction_from_submission": True,
                }
            ],
        },
    }
    imported = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert imported.status_code == 200, imported.get_data(as_text=True)

    accepted = client.post(
        "/api/meal-intake/imported-forged-material-attestation/accept",
        json={},
        headers=headers,
    )

    assert accepted.status_code == 409, accepted.get_data(as_text=True)
    assert data_store.get_food_logs(1) == []
    assert data_store.get_meal_acceptance_event(1, "imported-forged-material-attestation") is None


def test_imported_material_correction_keeps_snapshot_original_not_pending_aggregate(monkeypatch):
    module = _client(monkeypatch)

    def fake_parser(text, **_kw):
        calories = 890 if text == "Canes Box Combo larger" else 840
        return {
            "estimate": _accepted_estimate(
                item_name="Canes Box Combo",
                calories=calories,
                source="ai_text_estimate",
            ),
            "fallback_used": False,
        }

    monkeypatch.setattr(module, "parse_meal_text", fake_parser)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    client_id = "imported-material-original"
    captured = client.post(
        "/api/meal-intake",
        data={"text": "Canes Box Combo", "client_id": client_id},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert captured.status_code == 200, captured.get_data(as_text=True)
    refreshed = client.post(
        f"/api/meal-intake/{client_id}/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "material-import-round-trip",
            "item_id": "item-1",
            "text": "Canes Box Combo larger",
        },
        headers=headers,
    )
    assert refreshed.status_code == 200, refreshed.get_data(as_text=True)
    snapshot = client.get("/api/export-backup").get_json()["data"]["meal_review_snapshots"][0]
    imported = client.post(
        "/api/import-backup",
        json={"data": {"meal_review_snapshots": [snapshot]}},
        headers=headers,
    )
    assert imported.status_code == 200, imported.get_data(as_text=True)

    accepted = client.post(
        f"/api/meal-intake/{client_id}/accept",
        json={},
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    row = accepted.get_json()["food_logs"][0]
    assert row["calories"] == 890
    assert row["accepted_estimate"]["calories"] == 890
    assert row["original_estimate"]["calories"] == 840
    assert row["accepted_estimate"]["source"] == "manual_review_estimate"


def test_imported_pending_source_claim_hydrates_as_visible_canes_block(monkeypatch):
    module = _client(monkeypatch)
    client = module.app.test_client()
    headers = {"X-Requested-With": "XMLHttpRequest"}
    today = module._today_str()
    claimed_source = _accepted_estimate(
        item_name="Canes Box Combo",
        calories=840,
        source="nutritionix",
        external_food_id="forged-imported-canes",
    )
    imported = client.post(
        "/api/import-backup",
        json={
            "data": {
                "food_logs": [
                    {
                        "client_id": "imported-pending-source-claim",
                        "date": today,
                        "logged_at": f"{today}T12:00:00",
                        "context_note": "Canes Box Combo",
                        **claimed_source,
                        "correction_state": "pending_review",
                        "original_estimate": claimed_source,
                    }
                ]
            }
        },
        headers=headers,
    )
    assert imported.status_code == 200, imported.get_data(as_text=True)
    data_store.add_food_log(
        1,
        {
            "client_id": "live-pending-source-backed",
            "date": today,
            "logged_at": f"{today}T12:00:00",
            "context_note": "Canes Box Combo",
            **claimed_source,
            "correction_state": "pending_review",
            "original_estimate": claimed_source,
        },
    )

    pending = client.get("/api/meal-intake/pending")
    assert pending.status_code == 200, pending.get_data(as_text=True)
    rows = {entry["client_id"]: entry for entry in pending.get_json()["pending"]}
    imported_row = rows["imported-pending-source-claim"]
    assert imported_row["save_blocked_item_ids"] == ["item-1"]
    assert "branded_combo_ai_only" in imported_row["policy"]["reasons"]
    assert "branded_combo_ai_only" not in rows["live-pending-source-backed"]["policy"]["reasons"]

    accepted = client.post(
        "/api/meal-intake/imported-pending-source-claim/accept",
        json={"estimate": claimed_source},
        headers=headers,
    )
    assert accepted.status_code == 409, accepted.get_data(as_text=True)
