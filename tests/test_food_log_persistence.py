from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    import data_store

    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    return data_store, str(db_path)


def test_food_log_preserves_final_values_and_sanitized_original_estimate(isolated_store):
    store, _ = isolated_store
    store.init_data_db()

    saved = store.add_food_log(
        user_id=1,
        record={
            "client_id": "food-1",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:30:00",
            "source_timestamp": "2026-05-18T12:29:30",
            "meal_type": "lunch",
            "item_name": "chicken bowl",
            "portion_description": "one bowl",
            "context_note": "extra rice",
            "calories": 720,
            "protein_g": 48,
            "carbs_g": 82,
            "fat_g": 22,
            "sodium_mg": 1100,
            "fiber_g": 7,
            "confidence": 0.82,
            "source": "vision_estimate",
            "correction_state": "corrected",
            "original_estimate": {
                "item_name": "rice bowl",
                "calories": 650,
                "protein_g": 35,
                "raw_model_trace": "do not store",
                "image_bytes": "do not store",
            },
        },
    )

    assert saved["calories"] == 720
    assert saved["protein_g"] == 48
    assert saved["meal_type"] == "lunch"
    assert saved["context_note"] == "extra rice"
    assert saved["source_timestamp"] == "2026-05-18T12:29:30"
    assert saved["correction_state"] == "corrected"
    assert saved["original_estimate"] == {
        "item_name": "rice bowl",
        "calories": 650,
        "protein_g": 35,
    }

    rows = store.get_food_logs(user_id=1)
    assert len(rows) == 1
    assert rows[0]["original_estimate"] == saved["original_estimate"]


def test_food_log_client_id_is_idempotent(isolated_store):
    store, _ = isolated_store
    store.init_data_db()

    store.add_food_log(
        user_id=1,
        record={"client_id": "same-entry", "date": "2026-05-18", "calories": 500, "protein_g": 30},
    )
    store.add_food_log(
        user_id=1,
        record={"client_id": "same-entry", "date": "2026-05-18", "calories": 550, "protein_g": 35},
    )

    rows = store.get_food_logs(user_id=1)
    assert len(rows) == 1
    assert rows[0]["calories"] == 550
    assert rows[0]["protein_g"] == 35


def test_food_log_parallel_client_id_retries_upsert_single_row(isolated_store):
    store, _ = isolated_store
    store.init_data_db()

    def write_retry(calories):
        return store.add_food_log(
            user_id=1,
            record={
                "client_id": "parallel-entry",
                "date": "2026-05-18",
                "calories": calories,
                "protein_g": 30,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write_retry, [500, 550]))

    rows = store.get_food_logs(user_id=1)
    assert len(rows) == 1
    assert rows[0]["client_id"] == "parallel-entry"
    assert rows[0]["calories"] in {500, 550}
    assert {result["client_id"] for result in results} == {"parallel-entry"}


def test_food_log_blank_client_id_is_not_idempotency_key(isolated_store):
    store, _ = isolated_store
    store.init_data_db()

    store.add_food_log(user_id=1, record={"client_id": "", "date": "2026-05-18", "calories": 500, "protein_g": 30})
    store.add_food_log(user_id=1, record={"client_id": "", "date": "2026-05-18", "calories": 550, "protein_g": 35})

    rows = store.get_food_logs(user_id=1)
    assert len(rows) == 2
    assert [row["client_id"] for row in rows] == [None, None]


def test_clear_food_logs_removes_only_target_user_logs(isolated_store):
    store, _ = isolated_store
    store.init_data_db()
    store.add_food_log(user_id=1, record={"date": "2026-05-18", "calories": 500, "protein_g": 30})
    store.add_food_log(user_id=2, record={"date": "2026-05-18", "calories": 600, "protein_g": 40})

    store.clear_food_logs(user_id=1)

    assert store.get_food_logs(user_id=1) == []
    assert len(store.get_food_logs(user_id=2)) == 1


def test_init_data_db_adds_food_log_columns_to_pre_existing_table(isolated_store):
    store, db_path = isolated_store
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE food_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                logged_at TEXT NOT NULL,
                calories INTEGER,
                protein_g REAL,
                carbs_g REAL,
                fat_g REAL
            )
            """
        )
        conn.commit()

    store.init_data_db()

    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(food_logs)").fetchall()}
    assert "original_estimate_json" in cols
    assert "correction_state" in cols
    assert "source_timestamp" in cols


def test_init_data_db_adds_food_log_upsert_conflict_target_to_pre_existing_table(isolated_store):
    store, db_path = isolated_store
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE food_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                logged_at TEXT NOT NULL,
                calories INTEGER,
                protein_g REAL,
                carbs_g REAL,
                fat_g REAL
            )
            """
        )
        conn.commit()

    store.init_data_db()

    store.add_food_log(user_id=1, record={"client_id": "migrated", "date": "2026-05-18", "calories": 500})
    store.add_food_log(user_id=1, record={"client_id": "migrated", "date": "2026-05-18", "calories": 550})

    rows = store.get_food_logs(user_id=1)
    assert len(rows) == 1
    assert rows[0]["client_id"] == "migrated"
    assert rows[0]["calories"] == 550


def test_original_estimate_json_does_not_include_raw_trace(isolated_store):
    store, db_path = isolated_store
    store.init_data_db()

    store.add_food_log(
        user_id=1,
        record={
            "date": "2026-05-18",
            "calories": 400,
            "protein_g": 20,
            "original_estimate": {
                "item_name": "snack",
                "calories": 410,
                "model_response": {"raw": True},
                "trace": "hidden",
            },
        },
    )

    with sqlite3.connect(db_path) as conn:
        raw = conn.execute("SELECT original_estimate_json FROM food_logs").fetchone()[0]
    persisted = json.loads(raw)
    assert persisted == {"item_name": "snack", "calories": 410}
