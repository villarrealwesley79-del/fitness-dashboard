import workout_recommendation_fingerprint as fingerprint


def _payload_kwargs(tmp_path, **overrides):
    base = {
        "day": "2026-06-27",
        "workouts": [{"date": "2026-06-26", "created_at": "2026-06-26T10:00:00"}],
        "soreness_data": [],
        "cardio_data": [],
        "recovery_data": [],
        "user_settings": {"training_goal": "strength"},
        "weather_cache": {"ts": 1, "location": "San_Antonio", "data": None, "error": None},
        "open_wearables_configured": False,
        "open_wearables_user_mapped": False,
        "open_wearables_marker": {"configured": False},
        "apple_health_enabled": True,
        "apple_health_hr_intensity_enabled": False,
        "apple_health_freshness": ("missing", None, None),
        "apple_health_sync_db_file": str(tmp_path / "apple-health.sqlite3"),
        "apple_health_workout_files": [],
        "cached_oura": {"day": "2026-06-27", "readiness_score": 72},
        "oura_rows": [{"day": "2026-06-27", "readiness_score": 72, "created_at": "2026-06-27T06:00:00"}],
        "oura_db_file": str(tmp_path / "oura.sqlite3"),
        "whoop_connection": {"status": "disconnected"},
        "whoop_freshness": {"status": "missing", "score_state": None},
        "whoop_fact": None,
        "whoop_db_file": str(tmp_path / "whoop.sqlite3"),
    }
    base.update(overrides)
    return base


def test_build_payload_keeps_source_shapes_and_file_markers(tmp_path):
    payload = fingerprint.build_payload(**_payload_kwargs(tmp_path))

    assert payload["settings"] == {
        field: ("strength" if field == "training_goal" else None)
        for field in fingerprint.SETTINGS_FIELDS
    }
    assert payload["workouts_latest"] == "2026-06-26T10:00:00"
    assert payload["apple_health"]["sync_db"]["missing"] is True
    assert payload["oura"]["last_7_days"] == [{
        "day": "2026-06-27",
        "readiness_score": 72,
        "sleep_score": None,
        "hrv": None,
        "sleep_duration_min": None,
        "resting_hr": None,
        "created_at": "2026-06-27T06:00:00",
    }]
    assert payload["whoop"]["daily_fact"]["recovery_score"] is None


def test_fingerprint_changes_when_recommendation_inputs_change(tmp_path):
    baseline = fingerprint.build_fingerprint(**_payload_kwargs(tmp_path))
    changed = fingerprint.build_fingerprint(
        **_payload_kwargs(tmp_path, user_settings={"training_goal": "fat_loss"})
    )

    assert baseline != changed
