import sqlite3

import app
import data_store


OVERLAPPING_SETTINGS = (
    "training_goal",
    "sessions_per_week_target",
    "available_time_minutes",
    "target_weight_lbs",
    "target_body_fat_pct",
)


def _app_overlapping_defaults():
    settings = app._settings_with_defaults({})
    return {key: settings[key] for key in OVERLAPPING_SETTINGS}


def test_new_sql_settings_match_authoritative_app_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()

    assert data_store.get_settings(user_id=325) == _app_overlapping_defaults()

    data_store.upsert_settings(user_id=326, settings={})

    assert data_store.get_settings(user_id=326) == _app_overlapping_defaults()


def test_existing_sql_settings_are_not_replaced_by_default_alignment(tmp_path, monkeypatch):
    db_path = tmp_path / "fitness_data.db"
    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    data_store.init_data_db()
    persisted = {
        "training_goal": "weight_loss",
        "sessions_per_week_target": 5,
        "available_time_minutes": 45,
        "target_weight_lbs": 190.0,
        "target_body_fat_pct": 21.0,
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO user_settings (
                user_id, training_goal, sessions_per_week_target,
                available_time_minutes, target_weight_lbs, target_body_fat_pct
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (325, *(persisted[key] for key in OVERLAPPING_SETTINGS)),
        )

    data_store.init_data_db()

    assert data_store.get_settings(user_id=325) == persisted
