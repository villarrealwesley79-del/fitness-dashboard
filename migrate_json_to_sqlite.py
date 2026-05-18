"""
migrate_json_to_sqlite.py — One-shot migration from JSON files to fitness_data.db.

Imports all existing JSON data as user_id=1 (the first/default user).
Idempotent: uses INSERT OR REPLACE / INSERT OR IGNORE, safe to run multiple times.

Usage:
    cd projects/fitness-dashboard
    python3 migrate_json_to_sqlite.py
"""

import json
import os
import sys

# Ensure we can import data_store from this directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_store import init_data_db, add_body_record, add_cardio_record, \
    add_food_log, add_nutrition_record, add_recovery_record, upsert_settings, get_user_data_summary

_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_USER_ID = 1  # Migrate existing data for the first registered user


def _load_json(filename: str, default):
    path = os.path.join(_DIR, filename)
    if not os.path.exists(path):
        print(f"  ⚠  {filename} not found — skipping")
        return default
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f"  ⚠  {filename} parse error: {e} — skipping")
            return default


def migrate():
    print("=== Fitness Dashboard JSON → SQLite Migration ===")
    print(f"Target DB: {os.path.join(_DIR, 'fitness_data.db')}")
    print(f"Migrating as user_id={DEFAULT_USER_ID}\n")

    # 1. Init schema
    init_data_db()
    print("✅ Schema initialized\n")

    # 2. Body data
    body_records = _load_json("data_body.json", [])
    body_ok = 0
    for r in body_records:
        try:
            add_body_record(DEFAULT_USER_ID, r)
            body_ok += 1
        except Exception as e:
            print(f"  body skip: {r.get('date')} — {e}")
    print(f"✅ Body data: {body_ok}/{len(body_records)} records migrated")

    # 3. Cardio data
    cardio_records = _load_json("data_cardio.json", [])
    cardio_ok = 0
    for r in cardio_records:
        try:
            add_cardio_record(DEFAULT_USER_ID, r)
            cardio_ok += 1
        except Exception as e:
            print(f"  cardio skip: {r.get('date')} — {e}")
    print(f"✅ Cardio data: {cardio_ok}/{len(cardio_records)} records migrated")

    # 4. Nutrition data
    nutrition_records = _load_json("data_nutrition.json", [])
    nutrition_ok = 0
    for idx, r in enumerate(nutrition_records):
        try:
            add_nutrition_record(DEFAULT_USER_ID, r)
            if isinstance(r, dict):
                add_food_log(DEFAULT_USER_ID, {
                    **r,
                    "client_id": r.get("client_id") or f"legacy-nutrition:{r.get('date') or idx}:{idx}",
                    "logged_at": r.get("logged_at") or r.get("date"),
                    "source_timestamp": r.get("source_timestamp") or r.get("date"),
                    "source": r.get("source") or "legacy_json",
                    "correction_state": r.get("correction_state") or "legacy_import",
                })
            nutrition_ok += 1
        except Exception as e:
            print(f"  nutrition skip: {r.get('date')} — {e}")
    print(f"✅ Nutrition data: {nutrition_ok}/{len(nutrition_records)} records migrated")

    # 5. Recovery data
    recovery_records = _load_json("data_recovery.json", [])
    recovery_ok = 0
    for r in recovery_records:
        try:
            add_recovery_record(DEFAULT_USER_ID, r)
            recovery_ok += 1
        except Exception as e:
            print(f"  recovery skip: {r.get('date')} — {e}")
    print(f"✅ Recovery data: {recovery_ok}/{len(recovery_records)} records migrated")

    # 6. Settings (single dict)
    settings = _load_json("data_settings.json", {})
    if settings:
        try:
            upsert_settings(DEFAULT_USER_ID, settings)
            print(f"✅ Settings: migrated (goal={settings.get('training_goal', '?')})")
        except Exception as e:
            print(f"  ⚠  Settings migration failed: {e}")
    else:
        print("  ⚠  Settings: empty or missing — using defaults")

    # 7. Verification
    print("\n── Verification ──")
    summary = get_user_data_summary(DEFAULT_USER_ID)
    for table, count in summary.items():
        print(f"  {table}: {count} rows")

    print("\n✅ Migration complete. fitness_data.db is ready.")
    print("Next step: update app.py to import data_store and replace load_json() calls.")


if __name__ == "__main__":
    migrate()
