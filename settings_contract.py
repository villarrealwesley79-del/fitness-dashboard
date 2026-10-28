"""Authoritative owner settings defaults shared by persistence adapters.

``data_settings.json`` remains the authoritative settings store.  The SQL
``user_settings`` table is a compatibility projection for its overlapping
fields and must not introduce different defaults or rewrite existing rows.
"""

DEFAULT_SETTINGS = {
    "training_goal": "strength_hypertrophy",
    "date_of_birth": "",
    "sex": "",
    "sessions_per_week_target": 3,
    "available_time_minutes": 75,
    "target_weight_lbs": 175,
    "target_body_fat_pct": 18,
    "daily_calorie_target": 2200,
    "daily_protein_target_g": 148,
    "fatigue_threshold": 72,
    "equipment_preference": "machines_only",
    "preferred_equipment_brands": ["Hoist", "Nautilus"],
    "excluded_exercises": ["Preacher Curl"],
    "volume_landmarks": {
        "default": {"mv": 6, "mev": 9, "mav_min": 12, "mav_max": 18, "mrv": 22}
    },
}

SQL_SETTINGS_FIELDS = (
    "training_goal",
    "sessions_per_week_target",
    "available_time_minutes",
    "target_weight_lbs",
    "target_body_fat_pct",
)

SQL_SETTINGS_DEFAULTS = {key: DEFAULT_SETTINGS[key] for key in SQL_SETTINGS_FIELDS}
