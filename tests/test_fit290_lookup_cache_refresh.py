import branded_food_lookup
import data_store


def _estimate(item_name, calories, source):
    return {
        "item_name": item_name,
        "portion_description": "1 serving",
        "meal_type": "snack",
        "calories": calories,
        "protein_g": 10,
        "carbs_g": 20,
        "fat_g": 5,
        "sodium_mg": 100,
        "fiber_g": 3,
        "confidence": 0.9,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": source,
    }


def test_forced_barcode_refresh_skips_fresh_cache_and_records_changed_values(monkeypatch, tmp_path):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    old = _estimate("Old bar", 200, "nutritionix_barcode")
    new = _estimate("Updated bar", 220, "nutritionix_barcode")
    data_store.save_barcode_lookup_cache(
        "012345678905", "nutritionix_barcode", old, user_id=7
    )
    monkeypatch.setattr(
        branded_food_lookup,
        "_nutritionix_barcode_lookup",
        lambda barcode: dict(new),
    )

    result = branded_food_lookup.lookup_barcode(
        "012345678905", user_id=7, force_refresh=True
    )

    assert result == new
    event = data_store.list_lookup_cache_refresh_events(7)[0]
    assert event["cache_kind"] == "barcode"
    assert event["cache_key"] == "012345678905"
    assert event["old_response"] == old
    assert event["new_response"] == new


def test_fresh_barcode_cache_does_not_refresh_or_record_event(monkeypatch, tmp_path):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    cached = _estimate("Cached bar", 200, "nutritionix_barcode")
    data_store.save_barcode_lookup_cache(
        "012345678905", "nutritionix_barcode", cached, user_id=7
    )
    monkeypatch.setattr(
        branded_food_lookup,
        "_nutritionix_barcode_lookup",
        lambda barcode: (_ for _ in ()).throw(AssertionError("provider should not run")),
    )

    result = branded_food_lookup.lookup_barcode("012345678905", user_id=7)

    assert result["source"] == "local_cache"
    assert data_store.list_lookup_cache_refresh_events(7) == []


def test_expired_branded_cache_refresh_records_changed_values(monkeypatch, tmp_path):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    old = _estimate("Old cereal", 180, "usda_fdc")
    new = _estimate("Updated cereal", 190, "nutritionix")
    data_store.save_branded_lookup_cache("cereal", "usda_fdc", old, user_id=9)
    with data_store._get_db() as conn:
        conn.execute(
            "UPDATE branded_lookup_cache SET fetched_at = ? WHERE user_id = ? AND normalized_text = ?",
            ("2020-01-01T00:00:00", 9, "cereal"),
        )
        conn.commit()
    monkeypatch.setattr(branded_food_lookup, "_nutritionix_lookup", lambda *_args: dict(new))

    result = branded_food_lookup.lookup(
        "cereal", user_id=9, source_priority=("cache", "nutritionix")
    )

    assert result == new
    event = data_store.list_lookup_cache_refresh_events(9)[0]
    assert event["cache_kind"] == "branded"
    assert event["old_response"] == old
    assert event["new_response"] == new


def test_unchanged_refresh_is_not_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    response = _estimate("Same cereal", 180, "nutritionix")

    data_store.save_branded_lookup_cache("cereal", "nutritionix", response, user_id=3)
    data_store.save_branded_lookup_cache("cereal", "nutritionix", response, user_id=3)

    assert data_store.list_lookup_cache_refresh_events(3) == []


def test_refresh_event_retention_keeps_latest_hundred(monkeypatch, tmp_path):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    for calories in range(102):
        data_store.save_branded_lookup_cache(
            "cereal", "nutritionix", _estimate("Cereal", calories, "nutritionix"), user_id=4
        )

    events = data_store.list_lookup_cache_refresh_events(4, limit=50)
    with data_store._get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM lookup_cache_refresh_events WHERE user_id = ?", (4,)
        ).fetchone()[0]
    assert count == 100
    assert events[0]["new_response"]["calories"] == 101
