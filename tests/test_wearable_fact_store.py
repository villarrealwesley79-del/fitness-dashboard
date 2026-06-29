import pytest

from wearable_fact_store import (
    WearableDailyFact,
    delete_provider_data,
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


def test_wearable_fact_store_scopes_sources_and_facts_by_profile(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_wearable_source(str(db), {
        "provider_id": "open_wearables",
        "label": "Open Wearables",
        "status": "fresh",
    }, profile_key="1")
    upsert_wearable_source(str(db), {
        "provider_id": "open_wearables",
        "label": "Open Wearables",
        "status": "stale",
    }, profile_key="2")
    upsert_daily_facts(str(db), [
        WearableDailyFact("2026-06-26", "open_wearables", "Open Wearables", "sleep_duration", 430, "min"),
    ], profile_key="1")
    upsert_daily_facts(str(db), [
        WearableDailyFact("2026-06-26", "open_wearables", "Open Wearables", "sleep_duration", 120, "min"),
    ], profile_key="2")

    assert list_wearable_sources(str(db), profile_key="1")[0]["status"] == "fresh"
    assert list_wearable_sources(str(db), profile_key="2")[0]["status"] == "stale"
    assert list_recommendation_facts(str(db), profile_key="1")[0]["value"] == 430
    assert list_recommendation_facts(str(db), profile_key="2")[0]["value"] == 120


def test_wearable_fact_store_deletes_provider_data_for_one_profile(tmp_path):
    db = tmp_path / "facts.sqlite3"
    for profile_key, value in (("1", 430), ("2", 120)):
        upsert_wearable_source(str(db), {
            "provider_id": "open_wearables",
            "label": "Open Wearables",
            "status": "fresh",
        }, profile_key=profile_key)
        upsert_daily_facts(str(db), [
            WearableDailyFact("2026-06-26", "open_wearables", "Open Wearables", "sleep_duration", value, "min"),
        ], profile_key=profile_key)

    delete_provider_data(str(db), "open_wearables", profile_key="2")

    assert list_recommendation_facts(str(db), profile_key="2") == []
    assert list_wearable_sources(str(db), profile_key="2") == []
    assert list_recommendation_facts(str(db), profile_key="1")[0]["value"] == 430


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
