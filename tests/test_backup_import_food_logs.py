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
