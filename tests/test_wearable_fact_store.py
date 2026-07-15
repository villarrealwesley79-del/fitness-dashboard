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


def test_wearable_fact_store_adds_safe_provenance_columns_to_existing_schema(tmp_path):
    import sqlite3

    db = tmp_path / "facts.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE wearable_daily_facts (
                profile_key TEXT NOT NULL DEFAULT '1', date TEXT NOT NULL,
                provider_id TEXT NOT NULL, source_label TEXT NOT NULL, metric TEXT NOT NULL,
                value_json TEXT, unit TEXT, band TEXT, confidence TEXT NOT NULL,
                freshness TEXT NOT NULL, conflict_state TEXT,
                used_for_recommendation INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
                PRIMARY KEY (profile_key, date, provider_id, metric)
            )
        """)
        conn.execute("""
            CREATE TABLE wearable_sources (
                profile_key TEXT NOT NULL DEFAULT '1', provider_id TEXT NOT NULL,
                label TEXT NOT NULL, status TEXT NOT NULL, last_data_point TEXT,
                last_sync_attempt TEXT, capabilities_json TEXT,
                used_for_recommendation INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
                PRIMARY KEY (profile_key, provider_id)
            )
        """)

    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-06-28", "open_wearables", "Open Wearables", "workout_duration", 55, "min",
        category="strength_training", source_id="workout-1",
        source_provider="apple_health", original_label="Traditional Strength Training",
    )])

    fact = list_recommendation_facts(str(db))[0]
    assert fact["category"] == "strength_training"
    assert fact["source_id"] == "workout-1"
    assert fact["source_provider"] == "apple_health"
    assert fact["original_label"] == "Traditional Strength Training"


def test_replace_source_ids_preserves_prior_rows_when_validation_fails(tmp_path):
    db = tmp_path / "facts.sqlite3"
    prior = WearableDailyFact(
        "2026-06-28", "open_wearables", "Open Wearables", "weight", 82.4, "kg",
        source_id="undated-latest", freshness="unknown",
    )
    upsert_daily_facts(str(db), [prior], profile_key="profile-1")

    with pytest.raises(ValueError):
        upsert_daily_facts(
            str(db),
            [{
                "date": "2026-06-29", "provider_id": "open_wearables",
                "source_label": "Open Wearables", "metric": "weight",
                "value": {"raw": "forbidden"}, "source_id": "undated-latest",
            }],
            profile_key="profile-1",
            replace_source_ids={"undated-latest"},
        )

    assert list_recommendation_facts(str(db), profile_key="profile-1")[0]["value"] == 82.4


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_wearable_fact_store_rejects_non_finite_values(tmp_path, value):
    db = tmp_path / "facts.sqlite3"

    with pytest.raises(ValueError, match="finite"):
        upsert_daily_facts(str(db), [WearableDailyFact(
            "2026-06-28",
            "open_wearables",
            "Open Wearables",
            "recovery_score",
            value,
            "score",
        )])

    assert list_recommendation_facts(str(db)) == []


def test_open_wearables_source_read_ages_with_its_facts(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_wearable_source(str(db), {
        "provider_id": "open_wearables",
        "label": "Open Wearables",
        "status": "fresh",
        "used_for_recommendation": True,
    }, profile_key="profile-1")
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2020-01-01", "open_wearables", "Open Wearables", "recovery_score", 80,
        "score", freshness="fresh",
    )], profile_key="profile-1")

    source = list_wearable_sources(str(db), profile_key="profile-1")[0]

    assert source["status"] == "stale"
    assert source["used_for_recommendation"] is False
