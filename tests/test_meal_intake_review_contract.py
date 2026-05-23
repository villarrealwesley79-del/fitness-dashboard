from __future__ import annotations

import importlib

import data_store


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit144-meal-review-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module.personal_vocab, "record_accept", lambda *_a, **_kw: None)
    monkeypatch.setattr(module.personal_vocab, "record_correct", lambda *_a, **_kw: None)
    return module, module.app.test_client()


def _estimate(**overrides):
    estimate = {
        "item_name": "Chicken burrito",
        "portion_description": "1 burrito",
        "meal_type": "lunch",
        "calories": 640,
        "protein_g": 38,
        "carbs_g": 70,
        "fat_g": 24,
        "sodium_mg": 980,
        "fiber_g": 8,
        "confidence": 0.86,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "nutritionix",
    }
    estimate.update(overrides)
    return estimate


def _stub_parser(monkeypatch, module):
    def fake(text, **_kw):
        text = str(text or "")
        if "clear half" in text:
            estimate = _estimate(
                item_name="Chicken burrito",
                portion_description="1/2 burrito",
                calories=320,
                confidence=0.9,
                source="manual_review_estimate",
            )
        elif "candidate" in text:
            estimate = _estimate(
                item_name="Burrito bowl",
                portion_description="1 bowl",
                calories=520,
                confidence=0.91,
                source="nutritionix",
            )
        else:
            estimate = _estimate(
                item_name="Burrito",
                portion_description=None,
                calories=640,
                confidence=0.42,
                ambiguous=True,
                uncertainty_notes=["Portion unclear."],
                clarification_question="What size was the burrito?",
                verified_source_url="https://example.com/raw",
                source="ai_text_estimate",
                candidates=[
                    {
                        "candidate_id": "match-a",
                        "item_name": "Chicken burrito",
                        "portion_description": "1 regular burrito",
                        "meal_type": "lunch",
                        "calories": 640,
                        "protein_g": 38,
                        "carbs_g": 70,
                        "fat_g": 24,
                        "sodium_mg": 980,
                        "fiber_g": 8,
                        "confidence": 0.9,
                        "ambiguous": False,
                        "uncertainty_notes": [],
                        "source": "nutritionix",
                        "verified_source_url": "https://example.com/raw-candidate",
                        "prompt": "do not persist",
                    }
                ],
                prompt="do not persist",
            )
        return {"estimate": estimate, "fallback_used": False}

    monkeypatch.setattr(module, "parse_meal_text", fake)


def test_submit_creates_sanitized_multi_item_review_snapshot(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)

    res = client.post(
        "/api/meal-intake",
        data={"client_id": "fit144-meal-1", "text": "ambiguous burrito"},
    )

    body = res.get_json()
    assert res.status_code == 200, res.get_data(as_text=True)
    assert body["status"] == "pending_review"
    assert body["meal_id"] == "fit144-meal-1"
    assert body["meal_totals"]["calories"] == 640
    assert body["save_blocked_item_ids"] == ["item-1"]
    assert body["followup"] == {
        "available": True,
        "question": "What size was the burrito?",
        "used": False,
        "target_item_id": "item-1",
    }
    assert body["items"][0]["item_id"] == "item-1"
    assert body["items"][0]["source"]["link"] is None
    assert body["items"][0]["candidates"][0]["candidate_id"] == "match-a"
    assert "verified_source_url" not in str(body)
    assert "do not persist" not in str(body)
    assert "ambiguous burrito" not in str(body)

    snapshot = data_store.get_meal_review_snapshot(1, "fit144-meal-1")
    pending = data_store.get_food_logs(1)[0]
    assert snapshot["next_item_seq"] == 2
    assert "verified_source_url" not in str(snapshot["payload"])
    assert "ambiguous burrito" not in str(snapshot["payload"])
    assert pending["client_id"] == "fit144-meal-1"
    assert pending["correction_state"] == "pending_review"
    assert "verified_source_url" not in str(pending.get("original_estimate"))


def test_pending_hydrates_snapshot_and_blocks_unresolved_accept(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)
    client.post("/api/meal-intake", data={"client_id": "fit144-blocked-1", "text": "ambiguous burrito"})

    pending = client.get("/api/meal-intake/pending")
    accept = client.post("/api/meal-intake/fit144-blocked-1/accept", json={})
    invalid_estimate = dict(pending.get_json()["pending"][0]["estimate"])
    invalid_estimate["calories"] = None
    invalid_legacy_accept = client.post(
        "/api/meal-intake/fit144-blocked-1/accept",
        json={"estimate": invalid_estimate, "original_estimate": pending.get_json()["pending"][0]["estimate"]},
    )
    edited_estimate = dict(pending.get_json()["pending"][0]["estimate"])
    edited_estimate.update(
        {
            "item_name": "Chicken burrito reviewed",
            "meal_type": "dinner",
            "calories": 700,
            "protein_g": 42,
        }
    )
    legacy_estimate_accept = client.post(
        "/api/meal-intake/fit144-blocked-1/accept",
        json={"estimate": edited_estimate, "original_estimate": pending.get_json()["pending"][0]["estimate"]},
    )
    legacy_accept_retry = client.post(
        "/api/meal-intake/fit144-blocked-1/accept",
        json={"estimate": edited_estimate, "original_estimate": pending.get_json()["pending"][0]["estimate"]},
    )
    accepted_row = data_store.get_food_logs(1)[0]

    pending_body = pending.get_json()
    assert pending.status_code == 200
    assert pending_body["pending_count"] == 1
    hydrated = pending_body["pending"][0]
    assert hydrated["client_id"] == "fit144-blocked-1"
    assert hydrated["meal_id"] == "fit144-blocked-1"
    assert hydrated["text_hint"] == "ambiguous burrito"
    assert hydrated["items"][0]["item_id"] == "item-1"
    assert hydrated["items"][0]["candidates"][0]["candidate_id"] == "match-a"
    assert hydrated["followup"]["available"] is True
    assert hydrated["save_blocked_item_ids"] == ["item-1"]
    assert accept.status_code == 409
    assert accept.get_json()["save_blocked_item_ids"] == ["item-1"]
    assert invalid_legacy_accept.status_code == 400
    assert "invalid estimate" in invalid_legacy_accept.get_json()["error"]["message"]
    assert legacy_estimate_accept.status_code == 200, legacy_estimate_accept.get_data(as_text=True)
    assert legacy_estimate_accept.get_json()["status"] == "logged"
    assert accepted_row["calories"] == 700
    assert accepted_row["meal_type"] == "dinner"
    assert accepted_row["correction_state"] == "corrected"
    assert legacy_accept_retry.status_code == 200, legacy_accept_retry.get_data(as_text=True)
    assert legacy_accept_retry.get_json()["status"] == "logged"
    assert [row["item_name"] for row in legacy_accept_retry.get_json()["food_logs"]] == ["Chicken burrito reviewed"]
    assert len(data_store.get_food_logs(1)) == 1
    assert data_store.get_meal_review_snapshot(1, "fit144-blocked-1") is None


def test_active_snapshot_blocks_unresolved_explicit_items_accept(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)
    client.post("/api/meal-intake", data={"client_id": "fit144-explicit-block", "text": "ambiguous burrito"})

    blocked = client.post(
        "/api/meal-intake/fit144-explicit-block/accept",
        json={
            "items": [
                {
                    "item_id": "item-1",
                    "estimate": _estimate(
                        item_name="Burrito",
                        portion_description=None,
                        confidence=0.42,
                        ambiguous=True,
                    ),
                }
            ]
        },
    )

    assert blocked.status_code == 409, blocked.get_data(as_text=True)
    assert blocked.get_json()["save_blocked_item_ids"] == ["item-1"]
    assert data_store.get_meal_review_snapshot(1, "fit144-explicit-block") is not None
    assert [row.get("meal_id") for row in data_store.get_food_logs(1)] == [None]


def test_explicit_accept_honors_review_status_field(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)
    client.post("/api/meal-intake", data={"client_id": "fit144-explicit-status", "text": "ambiguous burrito"})

    skipped = client.post(
        "/api/meal-intake/fit144-explicit-status/accept",
        json={
            "items": [
                {
                    "item_id": "item-1",
                    "status": "skipped",
                    "estimate": _estimate(
                        item_name="Burrito",
                        portion_description=None,
                        confidence=0.42,
                        ambiguous=True,
                    ),
                }
            ]
        },
    )

    assert skipped.status_code == 200, skipped.get_data(as_text=True)
    assert skipped.get_json()["status"] == "discarded"
    assert skipped.get_json()["included_count"] == 0
    assert data_store.get_food_logs(1) == []
    event = data_store.get_meal_acceptance_event(1, "fit144-explicit-status")
    assert event["status"] == "discarded"
    assert event["skipped_count"] == 1


def test_snapshot_lifecycle_delete_and_backup_round_trip(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)
    client.post("/api/meal-intake", data={"client_id": "fit144-lifecycle-1", "text": "ambiguous burrito"})

    exported = client.get("/api/export-backup")
    snapshots = exported.get_json()["data"]["meal_review_snapshots"]

    assert data_store.get_user_data_summary(1)["meal_review_snapshots"] == 1
    assert len(snapshots) == 1
    assert snapshots[0]["meal_id"] == "fit144-lifecycle-1"

    data_store.delete_user_data(1)
    assert data_store.get_meal_review_snapshot(1, "fit144-lifecycle-1") is None

    restored = client.post("/api/import-backup", json={"data": {"meal_review_snapshots": snapshots}})
    assert restored.status_code == 200, restored.get_data(as_text=True)
    assert restored.get_json()["imported"]["meal_review_snapshots"] == 1
    assert data_store.get_meal_review_snapshot(1, "fit144-lifecycle-1") is not None


def test_refresh_candidate_request_replay_and_terminal_lifecycle(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)
    client.post("/api/meal-intake", data={"client_id": "fit144-meal-2", "text": "ambiguous burrito"})

    chosen = client.post(
        "/api/meal-intake/fit144-meal-2/refresh",
        json={
            "kind": "choose_candidate",
            "request_id": "choose-1",
            "item_id": "item-1",
            "candidate_id": "match-a",
        },
    )
    replay = client.post(
        "/api/meal-intake/fit144-meal-2/refresh",
        json={
            "kind": "choose_candidate",
            "request_id": "choose-1",
            "item_id": "item-1",
            "candidate_id": "match-a",
        },
    )
    conflict = client.post(
        "/api/meal-intake/fit144-meal-2/refresh",
        json={"kind": "add_item", "request_id": "choose-1", "text": "candidate bowl"},
    )
    typed = client.post(
        "/api/meal-intake/fit144-meal-2/refresh",
        json={"kind": "set_meal_type", "meal_type": "dinner"},
    )
    accepted = client.post("/api/meal-intake/fit144-meal-2/accept", json={})
    refresh_after_accept = client.post(
        "/api/meal-intake/fit144-meal-2/refresh",
        json={"kind": "set_meal_type", "meal_type": "snack"},
    )
    submit_replay_after_accept = client.post(
        "/api/meal-intake",
        data={"client_id": "fit144-meal-2", "text": "ambiguous burrito"},
    )

    chosen_body = chosen.get_json()
    assert chosen.status_code == 200, chosen.get_data(as_text=True)
    assert chosen_body["save_blocked_item_ids"] == []
    assert chosen_body["items"][0]["item_id"] == "item-1"
    assert chosen_body["items"][0]["candidates"] == []
    assert chosen_body["policy"]["reasons"] == []
    assert replay.status_code == 200
    assert conflict.status_code == 409
    assert typed.status_code == 200, typed.get_data(as_text=True)
    assert typed.get_json()["meal_type"] == "dinner"
    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    assert accepted.get_json()["status"] == "logged"
    assert data_store.get_food_logs(1)[0]["meal_type"] == "dinner"
    assert data_store.get_meal_review_snapshot(1, "fit144-meal-2") is None
    assert refresh_after_accept.status_code == 404
    assert submit_replay_after_accept.status_code == 200
    assert submit_replay_after_accept.get_json()["status"] == "logged"
    assert data_store.get_meal_review_snapshot(1, "fit144-meal-2") is None
    undo_after_accept = client.delete("/api/meal-intake/fit144-meal-2")
    assert undo_after_accept.status_code == 200, undo_after_accept.get_data(as_text=True)
    assert undo_after_accept.get_json() == {"status": "ok", "removed": True}
    assert data_store.get_food_logs(1) == []
    assert data_store.get_meal_acceptance_event(1, "fit144-meal-2") is None

    relogged = client.post(
        "/api/meal-intake/fit144-meal-2/accept",
        json={
            "items": [
                {
                    "item_id": "new-item",
                    "state": "included",
                    "estimate": _estimate(item_name="Relogged burrito", calories=480),
                }
            ]
        },
    )

    assert relogged.status_code == 200, relogged.get_data(as_text=True)
    assert relogged.get_json()["food_logs"][0]["item_name"] == "Relogged burrito"


def test_terminal_replay_preserves_explicit_multi_item_order(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)
    items = [
        {
            "item_id": "first",
            "state": "included",
            "estimate": _estimate(item_name="First item", calories=100),
        },
        {
            "item_id": "second",
            "state": "included",
            "estimate": _estimate(item_name="Second item", calories=200),
        },
    ]

    accepted = client.post("/api/meal-intake/fit144-explicit-order/accept", json={"items": items})
    replay = client.post(
        "/api/meal-intake",
        data={"client_id": "fit144-explicit-order", "text": "would parse only if terminal replay fails"},
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    assert [row["item_name"] for row in accepted.get_json()["food_logs"]] == ["First item", "Second item"]
    assert replay.status_code == 200, replay.get_data(as_text=True)
    assert replay.get_json()["status"] == "logged"
    assert [row["item_name"] for row in replay.get_json()["food_logs"]] == ["First item", "Second item"]


def test_undo_collided_meal_id_removes_direct_and_child_rows(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    data_store.add_food_log(
        1,
        {
            "client_id": "fit144-collide",
            "correction_state": "accepted",
            **_estimate(item_name="Legacy row", calories=111),
        },
    )
    accepted = client.post(
        "/api/meal-intake/fit144-collide/accept",
        json={
            "items": [
                {
                    "item_id": "child",
                    "state": "included",
                    "estimate": _estimate(item_name="Child row", calories=222),
                }
            ]
        },
    )

    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    assert sorted(row["item_name"] for row in data_store.get_food_logs(1)) == ["Child row", "Legacy row"]

    undone = client.delete("/api/meal-intake/fit144-collide")

    assert undone.status_code == 200, undone.get_data(as_text=True)
    assert undone.get_json() == {"status": "ok", "removed": True}
    assert data_store.get_food_logs(1) == []
    assert data_store.get_meal_acceptance_event(1, "fit144-collide") is None


def test_state_guarded_meal_delete_checks_all_child_rows(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    data_store.add_food_log(
        1,
        {
            "client_id": "fit144-mixed-child-a",
            "meal_id": "fit144-mixed-meal",
            "item_index": 0,
            "correction_state": "accepted",
            **_estimate(item_name="Accepted child"),
        },
    )
    data_store.add_food_log(
        1,
        {
            "client_id": "fit144-mixed-child-b",
            "meal_id": "fit144-mixed-meal",
            "item_index": 1,
            "correction_state": "corrected",
            **_estimate(item_name="Corrected child"),
        },
    )

    blocked = client.delete("/api/meal-intake/fit144-mixed-meal?correction_state=accepted")

    assert blocked.status_code == 409, blocked.get_data(as_text=True)
    assert blocked.get_json()["correction_state"] == "corrected"
    assert len(data_store.get_food_logs(1)) == 2


def test_refresh_edit_updates_top_level_estimate_and_pending_row(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)
    client.post("/api/meal-intake", data={"client_id": "fit144-meal-edit", "text": "ambiguous burrito"})

    edited = client.post(
        "/api/meal-intake/fit144-meal-edit/refresh",
        json={
            "kind": "edit_portion",
            "request_id": "edit-1",
            "item_id": "item-1",
            "text": "clear half",
        },
    )

    body = edited.get_json()
    assert edited.status_code == 200, edited.get_data(as_text=True)
    assert body["items"][0]["item_id"] == "item-1"
    assert body["items"][0]["calories"] == 320
    assert body["meal_totals"]["calories"] == 320
    assert body["estimate"]["calories"] == 320
    assert body["food_log"]["calories"] == 320
    assert body["save_blocked_item_ids"] == []

    accepted = client.post("/api/meal-intake/fit144-meal-edit/accept", json={})
    row = data_store.get_food_logs(1)[0]
    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    assert row["calories"] == 320
    assert row["correction_state"] == "corrected"
    assert row["original_estimate"]["calories"] == 640


def test_refresh_state_setters_retire_followup_and_zero_included_accept_discards(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)
    client.post("/api/meal-intake", data={"client_id": "fit144-meal-3", "text": "ambiguous burrito"})

    skipped = client.post(
        "/api/meal-intake/fit144-meal-3/refresh",
        json={"kind": "skip_item", "item_id": "item-1"},
    )
    stale_followup = client.post(
        "/api/meal-intake/fit144-meal-3/refresh",
        json={"kind": "followup_answer", "request_id": "follow-1", "answer": "clear half"},
    )
    discarded = client.post("/api/meal-intake/fit144-meal-3/accept", json={})

    assert skipped.status_code == 200, skipped.get_data(as_text=True)
    assert skipped.get_json()["followup"]["used"] is True
    assert skipped.get_json()["save_blocked_item_ids"] == []
    assert stale_followup.status_code == 409
    assert discarded.status_code == 200, discarded.get_data(as_text=True)
    assert discarded.get_json()["status"] == "discarded"
    assert data_store.get_meal_review_snapshot(1, "fit144-meal-3") is None
    assert data_store.get_food_logs(1) == []
    assert data_store.get_meal_acceptance_event(1, "fit144-meal-3")["status"] == "discarded"


def test_refresh_missing_snapshot_and_cross_user_return_404(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _stub_parser(monkeypatch, module)
    client.post("/api/meal-intake", data={"client_id": "fit144-meal-4", "text": "ambiguous burrito"})

    missing = client.post("/api/meal-intake/not-a-snapshot/refresh", json={"kind": "set_meal_type", "meal_type": "snack"})
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 2)
    cross_user = client.post("/api/meal-intake/fit144-meal-4/refresh", json={"kind": "set_meal_type", "meal_type": "snack"})

    assert missing.status_code == 404
    assert cross_user.status_code == 404
