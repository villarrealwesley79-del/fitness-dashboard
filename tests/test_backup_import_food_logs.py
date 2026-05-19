from __future__ import annotations

import importlib


def test_import_backup_replays_food_logs_without_wiping_or_duplication(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit45-contract-secret")
    import data_store

    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()

    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])

    existing = data_store.add_food_log(
        1,
        {
            "client_id": "keep-existing",
            "date": "2026-05-17",
            "logged_at": "2026-05-17T12:00:00",
            "item_name": "existing meal",
            "calories": 700,
            "protein_g": 45,
        },
    )
    backup = {
        "data": {
            "food_logs": [
                {
                    "date": "2026-05-18",
                    "logged_at": "2026-05-18T08:00:00",
                    "item_name": "legacy breakfast",
                    "calories": 500,
                    "protein_g": 30,
                }
            ]
        }
    }

    client = module.app.test_client()
    first = client.post("/api/import-backup", json=backup)
    second = client.post("/api/import-backup", json=backup)

    assert first.status_code == 200
    assert second.status_code == 200
    rows = data_store.get_food_logs(1)
    assert len(rows) == 2
    assert {row["item_name"] for row in rows} == {"existing meal", "legacy breakfast"}
    assert any(row["client_id"].startswith("backup-food-log-") for row in rows)
    assert data_store.get_food_logs(1, since=existing["date"])


def test_backup_round_trips_personal_vocab(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit74-backup-secret")
    import data_store

    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()

    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    canonical = {
        "item_name": "Chipotle chicken burrito",
        "calories": 1075,
        "protein_g": 51,
        "source": "nutritionix",
    }
    data_store.upsert_personal_vocab_entry(
        1,
        normalized_input="chip ckn bur",
        phrase="chip ckn bur",
        canonical_resolution=canonical,
        accepted=True,
    )

    client = module.app.test_client()
    exported = client.get("/api/export-backup")
    assert exported.status_code == 200
    backup = exported.get_json()
    vocab_rows = backup["data"]["personal_vocab"]
    assert vocab_rows[0]["normalized_input"] == "chip ckn bur"

    data_store.delete_user_data(1)
    restored = client.post("/api/import-backup", json={"data": {"personal_vocab": vocab_rows}})

    assert restored.status_code == 200
    assert restored.get_json()["imported"]["personal_vocab"] == 1
    entry = data_store.get_personal_vocab_entry(1, "chip ckn bur")
    assert entry["phrase"] == "chip ckn bur"
    assert entry["canonical_resolution"] == canonical
