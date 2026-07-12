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


def test_food_log_queries_distinguish_all_rows_from_accepted_rows(isolated_store):
    store, db_path = isolated_store
    store.init_data_db()

    states = ("pending_review", "review", "", "draft", "manual", "accepted", "corrected")
    for index, state in enumerate(states):
        store.add_food_log(
            user_id=1,
            record={
                "client_id": f"food-{state}",
                "date": "2026-05-18",
                "logged_at": f"2026-05-18T12:{index:02d}:00",
                "item_name": state,
                "correction_state": state,
            },
        )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE food_logs SET correction_state = '' WHERE client_id = 'food-'"
        )
        conn.commit()

    assert [row["correction_state"] for row in store.get_food_logs(1)] == [
        "corrected",
        "accepted",
        "manual",
        "draft",
        "",
        "review",
        "pending_review",
    ]
    assert [row["correction_state"] for row in store.get_accepted_food_logs(1)] == [
        "corrected",
        "accepted",
        "manual",
    ]


def test_food_log_persists_multi_item_meal_metadata(isolated_store):
    store, _ = isolated_store
    store.init_data_db()

    saved = store.add_food_log(
        user_id=1,
        record={
            "client_id": "meal-1-item-a",
            "date": "2026-05-18",
            "logged_at": "2026-05-18T12:30:00",
            "item_name": "chicken",
            "calories": 300,
            "meal_id": "meal-1",
            "meal_item_id": "item-a",
            "item_index": 0,
            "item_state": "included",
        },
    )

    rows = store.get_food_logs(user_id=1)
    assert saved["meal_id"] == "meal-1"
    assert saved["meal_item_id"] == "item-a"
    assert saved["item_index"] == 0
    assert saved["item_state"] == "included"
    assert rows[0]["meal_id"] == "meal-1"


def test_existing_database_migration_adds_multi_item_columns_and_index(tmp_path, monkeypatch):
    import data_store

    db_path = tmp_path / "fitness_data.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE food_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            client_id TEXT,
            date TEXT NOT NULL,
            logged_at TEXT NOT NULL,
            calories INTEGER,
            UNIQUE(user_id, client_id)
        );
        CREATE TABLE personal_vocab (
            user_id INTEGER NOT NULL,
            normalized_input TEXT NOT NULL,
            phrase TEXT NOT NULL,
            canonical_resolution TEXT NOT NULL,
            accept_count INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            confidence_boost REAL NOT NULL DEFAULT 0,
            last_used TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(user_id, normalized_input)
        );
        """
    )
    conn.close()
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))

    data_store.init_data_db()

    with sqlite3.connect(db_path) as migrated:
        food_cols = {row[1] for row in migrated.execute("PRAGMA table_info(food_logs)").fetchall()}
        vocab_cols = {row[1] for row in migrated.execute("PRAGMA table_info(personal_vocab)").fetchall()}
        event_cols = {row[1] for row in migrated.execute("PRAGMA table_info(meal_acceptance_events)").fetchall()}
        indexes = {row[1] for row in migrated.execute("PRAGMA index_list(food_logs)").fetchall()}
        tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert {"meal_id", "meal_item_id", "item_index", "item_state"} <= food_cols
    assert {"skip_count", "deleted_count", "last_negative_feedback_at"} <= vocab_cols
    assert "feedback_fingerprint" in event_cols
    assert "ix_food_logs_user_meal_id" in indexes
    assert "meal_acceptance_events" in tables


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


def test_backfill_food_log_client_id_claims_single_matching_clientless_row(isolated_store):
    store, _ = isolated_store
    store.init_data_db()
    store.add_food_log(
        user_id=1,
        record={
            "date": "2026-05-18",
            "logged_at": "2026-05-18T13:00:00",
            "calories": 500,
            "protein_g": 30,
            "carbs_g": 50,
            "fat_g": 18,
            "sodium_mg": 600,
            "context_note": "lunch note",
        },
    )

    claimed = store.backfill_food_log_client_id(
        1,
        "legacy-nutrition-abc123",
        {
            "date": "2026-05-18",
            "calories": 500,
            "protein_g": 30,
            "carbs_g": 50,
            "fat_g": 18,
            "sodium_mg": 600,
            "context_note": "lunch note",
        },
    )

    rows = store.get_food_logs(user_id=1)
    assert claimed is True
    assert len(rows) == 1
    assert rows[0]["client_id"] == "legacy-nutrition-abc123"


def test_backfill_food_log_client_id_refuses_ambiguous_matches(isolated_store):
    store, _ = isolated_store
    store.init_data_db()
    for _ in range(2):
        store.add_food_log(
            user_id=1,
            record={
                "date": "2026-05-18",
                "logged_at": "2026-05-18T13:00:00",
                "calories": 500,
                "protein_g": 30,
                "carbs_g": 50,
                "fat_g": 18,
                "sodium_mg": 600,
                "context_note": "same note",
            },
        )

    claimed = store.backfill_food_log_client_id(
        1,
        "legacy-nutrition-ambiguous",
        {
            "date": "2026-05-18",
            "calories": 500,
            "protein_g": 30,
            "carbs_g": 50,
            "fat_g": 18,
            "sodium_mg": 600,
            "context_note": "same note",
        },
    )

    rows = store.get_food_logs(user_id=1)
    assert claimed is False
    assert [row["client_id"] for row in rows] == [None, None]


def test_food_log_vocab_learning_claim_is_one_time(isolated_store):
    store, _ = isolated_store
    store.init_data_db()
    store.add_food_log(
        user_id=1,
        record={"client_id": "claim-once", "date": "2026-05-18", "calories": 500, "protein_g": 30},
    )

    assert store.claim_food_log_vocab_learning(1, "claim-once") is True
    assert store.claim_food_log_vocab_learning(1, "claim-once") is False


def test_clear_food_logs_removes_only_target_user_logs(isolated_store):
    store, _ = isolated_store
    store.init_data_db()
    store.add_food_log(user_id=1, record={"date": "2026-05-18", "calories": 500, "protein_g": 30})
    store.add_food_log(user_id=2, record={"date": "2026-05-18", "calories": 600, "protein_g": 40})

    store.clear_food_logs(user_id=1)

    assert store.get_food_logs(user_id=1) == []
    assert len(store.get_food_logs(user_id=2)) == 1


def test_delete_user_data_removes_personal_vocab_rows(isolated_store):
    store, _ = isolated_store
    store.init_data_db()
    canonical = {
        "item_name": "Chipotle chicken burrito",
        "calories": 1075,
        "protein_g": 51,
        "source": "nutritionix",
    }
    store.upsert_personal_vocab_entry(
        1,
        normalized_input="chip ckn bur",
        phrase="chip ckn bur",
        canonical_resolution=canonical,
        accepted=True,
    )
    store.upsert_personal_vocab_entry(
        2,
        normalized_input="chip ckn bur",
        phrase="chip ckn bur",
        canonical_resolution=canonical,
        accepted=True,
    )

    store.delete_user_data(user_id=1)

    assert store.get_personal_vocab_entry(1, "chip ckn bur") is None
    assert store.get_personal_vocab_entry(2, "chip ckn bur") is not None
    assert store.get_user_data_summary(1)["personal_vocab"] == 0


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
        conn.execute("INSERT INTO food_logs (user_id, date, logged_at, calories) VALUES (1, '2026-05-18', '2026-05-18T12:00:00', 500)")
        conn.commit()

    store.init_data_db()

    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(food_logs)").fetchall()}
    assert "original_estimate_json" in cols
    assert "correction_state" in cols
    assert "source_timestamp" in cols
    assert "accepted_estimate_json" in cols
    rows = store.get_food_logs(1)
    assert rows[0]["calories"] == 500
    assert rows[0]["accepted_estimate"] is None


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
                "from_image": True,
                "model_response": {"raw": True},
                "image_bytes": "drop raw image bytes",
                "trace": "hidden",
            },
        },
    )

    with sqlite3.connect(db_path) as conn:
        raw = conn.execute("SELECT original_estimate_json FROM food_logs").fetchone()[0]
    persisted = json.loads(raw)
    assert persisted == {"item_name": "snack", "calories": 410, "from_image": True}


def test_original_estimate_json_preserves_lookup_provenance(isolated_store):
    store, db_path = isolated_store
    store.init_data_db()

    store.add_food_log(
        user_id=1,
        record={
            "client_id": "provenance-1",
            "date": "2026-05-19",
            "calories": 1075,
            "protein_g": 51,
            "source": "nutritionix",
            "original_estimate": {
                "item_name": "Chipotle chicken burrito",
                "calories": 1075,
                "protein_g": 51,
                "source": "nutritionix",
                "external_food_id": "chipotle-burrito",
                "verified_source_url": "https://www.nutritionix.com/",
                "data_fetched_at": "2026-05-19T10:00:00",
                "portion_basis": "1 burrito",
                "off_attribution": {
                    "name": "Open Food Facts",
                    "url": "https://world.openfoodfacts.org/",
                    "raw": {"drop": True},
                },
                "raw_model_trace": "drop me",
            },
        },
    )

    with sqlite3.connect(db_path) as conn:
        raw = conn.execute("SELECT original_estimate_json FROM food_logs").fetchone()[0]
    payload = json.loads(raw)
    assert payload["external_food_id"] == "chipotle-burrito"
    assert payload["verified_source_url"] == "https://www.nutritionix.com/"
    assert payload["data_fetched_at"] == "2026-05-19T10:00:00"
    assert payload["portion_basis"] == "1 burrito"
    assert payload["off_attribution"] == {
        "name": "Open Food Facts",
        "url": "https://world.openfoodfacts.org/",
    }
    assert "raw_model_trace" not in payload
