from __future__ import annotations

import importlib


def test_add_nutrition_returns_sanitized_accepted_food_log(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit7-contract-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    from data_store import sanitize_food_estimate

    def fake_add_food_log(_user_id, record):
        original_estimate = sanitize_food_estimate(record.get("original_estimate"))
        return {
            "date": record["date"],
            "calories": record["calories"],
            "protein_g": record["protein_g"],
            "meal_type": record["meal_type"],
            "context_note": record["context_note"],
            "correction_state": record["correction_state"],
            "original_estimate": original_estimate,
        }

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    response = module.app.test_client().post(
        "/api/add-nutrition",
        json={
            "date": "2026-05-18",
            "calories": 500,
            "protein_g": 35,
            "carbs_g": 55,
            "fat_g": 12,
            "sodium_mg": 850,
            "meal_type": "snack",
            "context_note": "shared half",
            "correction_state": "corrected",
            "original_estimate": {
                "item_name": "popcorn",
                "calories": 650,
                "raw_model_trace": "hidden",
            },
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["nutrition"]["sodium_mg"] == 850
    assert payload["food_log"]["meal_type"] == "snack"
    assert payload["food_log"]["context_note"] == "shared half"
    assert payload["food_log"]["original_estimate"] == {
        "item_name": "popcorn",
        "calories": 650,
    }


def test_add_nutrition_client_id_retry_replaces_legacy_summary_entry(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit7-contract-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module, "add_food_log", lambda _user_id, record: record)

    client = module.app.test_client()
    first = client.post(
        "/api/add-nutrition",
        json={"client_id": "retry-1", "date": "2026-05-18", "calories": 500, "protein_g": 35},
    )
    second = client.post(
        "/api/add-nutrition",
        json={"client_id": "retry-1", "date": "2026-05-18", "calories": 550, "protein_g": 40},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(module.NUTRITION_DATA) == 1
    assert module.NUTRITION_DATA[0]["client_id"] == "retry-1"
    assert module.NUTRITION_DATA[0]["calories"] == 550
    assert module.NUTRITION_DATA[0]["protein_g"] == 40


def test_add_nutrition_without_client_id_keeps_food_log_client_id_null(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit7-contract-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    seen_client_ids = []

    def fake_add_food_log(_user_id, record):
        seen_client_ids.append(record.get("client_id"))
        return record

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)

    client = module.app.test_client()
    first = client.post(
        "/api/add-nutrition",
        json={"date": "2026-05-18", "calories": 500, "protein_g": 35},
    )
    second = client.post(
        "/api/add-nutrition",
        json={"date": "2026-05-18", "calories": 550, "protein_g": 40},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert seen_client_ids == [None, None]
    assert len(module.NUTRITION_DATA) == 2


def test_add_nutrition_food_log_failure_leaves_legacy_json_unchanged(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit46-contract-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True, PROPAGATE_EXCEPTIONS=False)
    original = [{"client_id": "existing", "date": "2026-05-18", "calories": 400, "protein_g": 20}]
    saved_payloads = []
    monkeypatch.setattr(module, "NUTRITION_DATA", list(original))
    monkeypatch.setattr(module, "save_json", lambda _path, data: saved_payloads.append(list(data)))
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)

    def fail_add_food_log(_user_id, _record):
        raise RuntimeError("sqlite unavailable")

    monkeypatch.setattr(module, "add_food_log", fail_add_food_log)

    response = module.app.test_client().post(
        "/api/add-nutrition",
        json={"client_id": "new-entry", "date": "2026-05-18", "calories": 550, "protein_g": 35},
    )

    assert response.status_code == 500
    assert module.NUTRITION_DATA == original
    assert saved_payloads == []


def test_add_nutrition_legacy_save_failure_rolls_back_memory_after_sqlite_write(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit46-contract-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True, PROPAGATE_EXCEPTIONS=False)
    original = [{"client_id": "existing", "date": "2026-05-18", "calories": 400, "protein_g": 20}]
    food_log_records = []
    monkeypatch.setattr(module, "NUTRITION_DATA", list(original))
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)

    def fake_add_food_log(_user_id, record):
        food_log_records.append(record)
        return record

    def fail_save_json(_path, _data):
        raise RuntimeError("legacy json disk unavailable")

    monkeypatch.setattr(module, "add_food_log", fake_add_food_log)
    monkeypatch.setattr(module, "save_json", fail_save_json)

    response = module.app.test_client().post(
        "/api/add-nutrition",
        json={"client_id": "new-entry", "date": "2026-05-18", "calories": 550, "protein_g": 35},
    )

    assert response.status_code == 500
    assert len(food_log_records) == 1
    assert module.NUTRITION_DATA == original
