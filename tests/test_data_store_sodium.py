"""Regression tests for FIT-23 sodium round-trip through the SQLite store.

The JSON `/api/add-nutrition` endpoint persists `sodium_mg`; direct SQLite
writes must carry that field through to the store, and a pre-existing DB without
the column must pick it up on `init_data_db()`.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """Point `data_store.DATA_DB` at a tmp file and return a fresh module reference."""
    import data_store

    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    return data_store, str(db_path)


def test_sodium_round_trip_through_add_nutrition_record(isolated_store):
    store, _ = isolated_store
    store.init_data_db()
    store.add_nutrition_record(
        user_id=1,
        record={
            "date": "2026-05-18",
            "calories": 600,
            "protein_g": 40,
            "carbs_g": 70,
            "fat_g": 20,
            "sodium_mg": 900,
            "notes": "lunch",
        },
    )
    rows = store.get_nutrition_data(user_id=1)
    assert len(rows) == 1
    assert rows[0]["sodium_mg"] == 900
    # Existing fields still round-trip.
    assert rows[0]["calories"] == 600
    assert rows[0]["protein_g"] == 40
    assert rows[0]["carbs_g"] == 70
    assert rows[0]["fat_g"] == 20


def test_init_data_db_adds_sodium_column_to_pre_existing_table(isolated_store):
    """An existing DB without sodium_mg should be migrated forward in place."""
    store, db_path = isolated_store
    # Simulate a pre-FIT-23 DB: create the table with the old schema, no sodium_mg.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE nutrition_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                calories INTEGER,
                protein_g REAL,
                carbs_g REAL,
                fat_g REAL,
                fiber_g REAL,
                water_oz REAL,
                notes TEXT,
                UNIQUE(user_id, date)
            )
            """
        )
        conn.commit()

    store.init_data_db()
    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(nutrition_data)").fetchall()}
    assert "sodium_mg" in cols

    # And a record inserted via add_nutrition_record persists sodium_mg.
    store.add_nutrition_record(
        user_id=1,
        record={"date": "2026-05-18", "calories": 500, "protein_g": 30, "sodium_mg": 700},
    )
    rows = store.get_nutrition_data(user_id=1)
    assert rows[0]["sodium_mg"] == 700


def test_add_nutrition_record_accepts_missing_sodium(isolated_store):
    """Legacy callers that don't supply sodium_mg still succeed (NULL stored)."""
    store, _ = isolated_store
    store.init_data_db()
    store.add_nutrition_record(
        user_id=1,
        record={"date": "2026-05-18", "calories": 500, "protein_g": 30},
    )
    rows = store.get_nutrition_data(user_id=1)
    assert rows[0]["sodium_mg"] is None
