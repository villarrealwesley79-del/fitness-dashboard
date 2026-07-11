from __future__ import annotations

import importlib

import data_store


def _isolated_db(monkeypatch, tmp_path):
    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()


def _food_log(client_id: str, *, calories=200, protein_g=8, user_id=1, **extra):
    record = {
        "client_id": client_id,
        "date": "2026-05-24",
        "logged_at": "2026-05-24T12:00:00",
        "item_name": "HEB bakery bolillo roll",
        "portion_description": "1 roll",
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": 38,
        "fat_g": 2,
        "sodium_mg": 340,
        "source": "ai_estimate",
        "correction_state": "accepted",
    }
    record.update(extra)
    return data_store.add_food_log(user_id, record)


def test_refresh_event_records_material_prior_log_change(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("roll-1", calories=180, protein_g=6)

    _food_log(
        "roll-1",
        calories=230,
        protein_g=8,
        source="verified_lookup",
        refresh_event={
            "source_label": "Open Food Facts",
            "source_url": "https://world.openfoodfacts.org/product/roll",
            "refreshed_at": "2026-05-25T10:00:00",
        },
    )

    events = data_store.list_food_log_refresh_events(1)
    assert len(events) == 1
    event = events[0]
    assert event["client_id"] == "roll-1"
    assert event["date"] == "2026-05-24"
    assert event["item_name"] == "HEB bakery bolillo roll"
    assert event["source"] == "Open Food Facts"
    assert event["source_url"] == "https://world.openfoodfacts.org/product/roll"
    assert event["prev_calories"] == 180
    assert event["new_calories"] == 230
    assert event["prev_protein_g"] == 6
    assert event["new_protein_g"] == 8


def test_refresh_event_suppresses_same_value_confirmation(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("same-1", calories=180, protein_g=6)

    _food_log(
        "same-1",
        calories=180,
        protein_g=6,
        refresh_event={"source_label": "Open Food Facts"},
    )

    assert data_store.list_food_log_refresh_events(1) == []


def test_refresh_event_is_opt_in_so_user_corrections_do_not_notify(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("correction-1", calories=180, protein_g=6)

    _food_log(
        "correction-1",
        calories=260,
        protein_g=10,
        source="manual",
        correction_state="corrected",
    )

    assert data_store.list_food_log_refresh_events(1) == []


def test_refresh_event_api_projects_and_acknowledges_user_scoped_event(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    _food_log("api-1", calories=180, protein_g=6)
    _food_log(
        "api-1",
        calories=230,
        protein_g=8,
        refresh_event={
            "source_label": "Verified source",
            "source_url": "https://example.test/verified",
            "refreshed_at": "2026-05-25T10:00:00",
        },
    )

    client = module.app.test_client()
    response = client.get("/api/food-log-refresh-events")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    event = payload["events"][0]
    assert event["client_id"] == "api-1"
    assert event["previous"]["calories"] == 180
    assert event["current"]["calories"] == 230
    assert event["source"] == "Verified source"

    ack = client.post(f"/api/food-log-refresh-events/{event['id']}/ack")
    assert ack.status_code == 200
    assert client.get("/api/food-log-refresh-events").get_json()["events"] == []


def test_long_external_event_id_stores_dismissable_internal_id(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    external_id = "x" * 200
    _food_log("long-id-1", calories=180, protein_g=6)
    _food_log(
        "long-id-1",
        calories=230,
        protein_g=8,
        refresh_event={"id": external_id, "source_label": "Long ID Source"},
    )

    events = data_store.list_food_log_refresh_events(1)
    assert len(events) == 1
    assert events[0]["id"] != external_id
    assert len(events[0]["id"]) <= 80

    ack = module.app.test_client().post(
        f"/api/food-log-refresh-events/{events[0]['id']}/ack"
    )
    assert ack.status_code == 200


def test_reused_upstream_event_id_does_not_fail_food_log_save(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("reuse-a", calories=180, protein_g=6)
    _food_log("reuse-b", calories=180, protein_g=6)

    _food_log(
        "reuse-a",
        calories=230,
        protein_g=8,
        refresh_event={"event_id": "shared-upstream-id", "source_label": "Source"},
    )
    result = _food_log(
        "reuse-b",
        calories=230,
        protein_g=8,
        refresh_event={"event_id": "shared-upstream-id", "source_label": "Source"},
    )

    assert result["client_id"] == "reuse-b"
    events = data_store.list_food_log_refresh_events(1)
    assert len(events) == 2
    assert {event["client_id"] for event in events} == {"reuse-a", "reuse-b"}
    assert len({event["id"] for event in events}) == 2


def _verified_estimate(**extra):
    estimate = {
        "item_name": "HEB bakery bolillo roll",
        "portion_description": "1 roll",
        "meal_type": "snack",
        "calories": 230,
        "protein_g": 8,
        "carbs_g": 38,
        "fat_g": 2,
        "sodium_mg": 340,
        "confidence": 0.91,
        "source": "branded_lookup",
        "underlying_source": "open_food_facts",
        "verified_source_url": "https://world.openfoodfacts.org/product/roll",
        "data_fetched_at": "2026-05-25T10:00:00",
    }
    estimate.update(extra)
    return estimate


def test_meal_accept_rejects_client_asserted_verified_source_update(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    _food_log("meal-verified-update", calories=180, protein_g=6)

    response = module.app.test_client().post(
        "/api/meal-intake/meal-verified-update/accept",
        json={"estimate": _verified_estimate(), "text": "bolillo roll"},
    )

    assert response.status_code == 409, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "duplicate_client_id"
    events = data_store.list_food_log_refresh_events(1)
    assert events == []
    stored = next(row for row in data_store.get_food_logs(1) if row["client_id"] == "meal-verified-update")
    assert stored["calories"] == 180


def test_meal_persist_corrected_verified_source_does_not_notify(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    _food_log("meal-corrected-update", calories=180, protein_g=6)

    module._meal_intake_persist(
        "meal-corrected-update",
        _verified_estimate(),
        source="branded_lookup",
        has_image=False,
        text_hint="bolillo roll",
        correction_state="corrected",
    )

    assert data_store.list_food_log_refresh_events(1) == []


def test_meal_persist_pending_verified_source_does_not_notify(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    _food_log("meal-pending-update", calories=180, protein_g=6)

    module._meal_intake_persist(
        "meal-pending-update",
        _verified_estimate(),
        source="branded_lookup",
        has_image=False,
        text_hint="bolillo roll",
        correction_state="pending_review",
    )

    assert data_store.list_food_log_refresh_events(1) == []


def test_meal_persist_accepted_unverified_source_does_not_notify(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    _food_log("meal-unverified-update", calories=180, protein_g=6)

    module._meal_intake_persist(
        "meal-unverified-update",
        _verified_estimate(
            source="vision_estimate",
            underlying_source=None,
            verified_source_url=None,
            external_food_id=None,
            data_fetched_at=None,
        ),
        source="vision_estimate",
        has_image=True,
        text_hint="bolillo roll",
    )

    assert data_store.list_food_log_refresh_events(1) == []


def test_refresh_event_ack_cannot_cross_user_boundary(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    _food_log("other-user-1", user_id=2, calories=180, protein_g=6)
    _food_log(
        "other-user-1",
        user_id=2,
        calories=230,
        protein_g=8,
        refresh_event={"source_label": "Verified source"},
    )

    other_event = data_store.list_food_log_refresh_events(2)[0]
    response = module.app.test_client().post(
        f"/api/food-log-refresh-events/{other_event['id']}/ack"
    )

    assert response.status_code == 404
    assert data_store.list_food_log_refresh_events(2)[0]["acknowledged_at"] is None


def test_delete_user_data_purges_refresh_events(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("delete-user-1", calories=180, protein_g=6)
    _food_log(
        "delete-user-1",
        calories=230,
        protein_g=8,
        refresh_event={"source_label": "Verified source"},
    )

    assert data_store.list_food_log_refresh_events(1)

    data_store.delete_user_data(1)

    assert data_store.list_food_log_refresh_events(1) == []


def test_clear_food_logs_purges_refresh_events(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("clear-1", calories=180, protein_g=6)
    _food_log(
        "clear-1",
        calories=230,
        protein_g=8,
        refresh_event={"source_label": "Verified source"},
    )

    assert data_store.list_food_log_refresh_events(1)

    data_store.clear_food_logs(1)

    assert data_store.list_food_log_refresh_events(1) == []


def test_delete_food_log_by_client_id_purges_only_matching_refresh_event(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("delete-client-1", calories=180, protein_g=6)
    _food_log(
        "delete-client-1",
        calories=230,
        protein_g=8,
        refresh_event={"source_label": "Verified source"},
    )
    _food_log("delete-client-2", calories=180, protein_g=6)
    _food_log(
        "delete-client-2",
        calories=230,
        protein_g=8,
        refresh_event={"source_label": "Verified source"},
    )

    assert data_store.delete_food_log_by_client_id(1, "delete-client-1") is True

    remaining = data_store.list_food_log_refresh_events(1)
    assert [event["client_id"] for event in remaining] == ["delete-client-2"]


def test_delete_food_logs_by_meal_id_purges_matching_refresh_events(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    _food_log("meal-1-item-1", calories=180, protein_g=6, meal_id="meal-1")
    _food_log(
        "meal-1-item-1",
        calories=230,
        protein_g=8,
        meal_id="meal-1",
        refresh_event={"source_label": "Verified source"},
    )
    _food_log("meal-1-item-2", calories=160, protein_g=5, meal_id="meal-1")
    _food_log(
        "meal-1-item-2",
        calories=210,
        protein_g=7,
        meal_id="meal-1",
        refresh_event={"source_label": "Verified source"},
    )
    _food_log("meal-2-item-1", calories=180, protein_g=6, meal_id="meal-2")
    _food_log(
        "meal-2-item-1",
        calories=230,
        protein_g=8,
        meal_id="meal-2",
        refresh_event={"source_label": "Verified source"},
    )

    assert data_store.delete_food_logs_by_meal_id(1, "meal-1") == 2

    remaining = data_store.list_food_log_refresh_events(1)
    assert [event["client_id"] for event in remaining] == ["meal-2-item-1"]
