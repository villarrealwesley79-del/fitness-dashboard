import pytest

from wearable_fact_store import (
    WearableDailyFact,
    list_recommendation_facts,
    list_wearable_sources,
    upsert_daily_facts,
    upsert_wearable_source,
)


def test_wearable_fact_store_round_trips_normalized_facts(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_wearable_source(str(db), {
        "provider_id": "open_wearables",
        "label": "Open Wearables",
        "status": "fresh",
        "last_data_point": "2026-06-26",
        "last_sync_attempt": "2026-06-26T12:00:00",
        "capabilities": {"metrics": True, "workouts": True},
        "used_for_recommendation": True,
    })
    upsert_daily_facts(str(db), [
        WearableDailyFact("2026-06-26", "open_wearables", "Open Wearables", "sleep_duration", 430, "min", band="ok", confidence="medium", freshness="fresh"),
    ])

    assert list_wearable_sources(str(db))[0]["source"] == "open_wearables"
    facts = list_recommendation_facts(str(db))
    assert facts[0]["metric"] == "sleep_duration"
    assert facts[0]["value"] == 430
    assert facts[0]["used_for_recommendation"] is False


def test_wearable_fact_store_rejects_raw_or_secret_fields(tmp_path):
    db = tmp_path / "facts.sqlite3"
    with pytest.raises(ValueError):
        upsert_daily_facts(str(db), [{
            "date": "2026-06-26",
            "provider_id": "open_wearables",
            "source_label": "Open Wearables",
            "metric": "sleep",
            "value": {"raw": {"access_token": "leak"}},
        }])
