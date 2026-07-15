import sqlite3
from datetime import datetime

import pytest

from wearable_fact_store import (
    WearableDailyFact,
    delete_provider_data,
    list_recommendation_facts,
    list_wearable_sources,
    upsert_daily_facts,
    upsert_wearable_source,
)


def _mark_as_pre_contract_db(db):
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 0")


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


def test_wearable_fact_store_round_trips_required_open_wearables_contract(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-06-26",
        "oura",
        "Oura",
        "sleep_duration",
        430,
        "min",
        source_system="open_wearables",
        source_record_kind="event",
        metric_domain="sleep",
        capability_state="available",
        source_last_synced_at="2026-06-26T12:00:00Z",
        imported_at="2026-06-26T12:00:01Z",
    )])

    [fact] = list_recommendation_facts(str(db))
    assert fact["provider_id"] == "oura"
    assert fact["source_system"] == "open_wearables"
    assert fact["source_record_kind"] == "event"
    assert fact["metric_domain"] == "sleep"
    assert fact["capability_state"] == "available"
    assert fact["source_last_synced_at"] == "2026-06-26T12:00:00Z"
    assert fact["imported_at"] == "2026-06-26T12:00:01Z"


def test_latest_per_metric_orders_observation_instants_chronologically(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [
        WearableDailyFact(
            "2026-07-14", "oura", "Oura", "recovery_score", 70, "score",
            source_id="older", observed_at="2026-07-14T13:00:00+00:00",
        ),
        WearableDailyFact(
            "2026-07-14", "oura", "Oura", "recovery_score", 80, "score",
            source_id="newer", observed_at="2026-07-14T08:30:00-05:00",
        ),
    ])

    [fact] = list_recommendation_facts(str(db), latest_per_metric=True)

    assert fact["source_id"] == "newer"
    assert fact["value"] == 80


def test_provider_facts_from_distinct_source_systems_do_not_collide(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [
        WearableDailyFact(
            "2026-07-14", "oura", "Oura", "recovery_score", 70, "score",
            source_system="oura",
        ),
        WearableDailyFact(
            "2026-07-14", "oura", "Oura", "recovery_score", 80, "score",
            source_system="open_wearables",
        ),
    ])

    facts = list_recommendation_facts(str(db))

    assert {(fact["source_system"], fact["value"]) for fact in facts} == {
        ("oura", 70),
        ("open_wearables", 80),
    }


def test_initialized_fact_read_does_not_require_writer_lock(tmp_path, monkeypatch):
    import sqlite3
    import wearable_fact_store as store

    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "oura", "Oura", "recovery_score", 70, "score",
    )])
    writer = sqlite3.connect(db)
    writer.execute("BEGIN IMMEDIATE")
    real_connect = sqlite3.connect
    monkeypatch.setattr(store.sqlite3, "connect", lambda path: real_connect(path, timeout=0))
    try:
        [fact] = list_recommendation_facts(str(db))
    finally:
        writer.rollback()
        writer.close()

    assert fact["value"] == 70


def test_latest_per_metric_prefers_newer_fact_date_before_observation_instant(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [
        WearableDailyFact(
            "2026-07-14", "oura", "Oura", "recovery_score", 70, "score",
            source_id="older-local-day", observed_at="2026-07-14T23:30:00-05:00",
        ),
        WearableDailyFact(
            "2026-07-15", "oura", "Oura", "recovery_score", 80, "score",
            source_id="newer-date",
        ),
    ])

    [fact] = list_recommendation_facts(str(db), latest_per_metric=True)

    assert fact["source_id"] == "newer-date"
    assert fact["value"] == 80


def test_distinct_metric_limit_orders_by_one_real_representative_row(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [
        WearableDailyFact(
            "2026-07-15", "oura", "Oura", "metric_a", 1, "score",
            source_system="open_wearables",
        ),
        WearableDailyFact(
            "2026-07-14", "whoop", "WHOOP", "metric_a", 2, "score",
            source_system="open_wearables", observed_at="2026-07-14T23:30:00-05:00",
        ),
        WearableDailyFact(
            "2026-07-15", "oura", "Oura", "metric_b", 3, "score",
            source_system="open_wearables", observed_at="2026-07-15T01:00:00Z",
        ),
    ])

    facts = list_recommendation_facts(str(db), latest_per_metric=True, limit=1)

    assert {fact["metric"] for fact in facts} == {"metric_b"}


def test_timestamped_sleep_fact_defaults_to_summary_record_kind(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "oura", "Oura", "sleep_duration", 420, "min",
        source_id="summary-1", observed_at="2026-07-14T08:00:00Z",
    )])

    [fact] = list_recommendation_facts(str(db))

    assert fact["source_record_kind"] == "summary"


def test_open_wearables_provider_identity_migration_avoids_parallel_legacy_rows(tmp_path):
    db = tmp_path / "facts.sqlite3"
    legacy = WearableDailyFact(
        "2026-07-14", "open_wearables", "Open Wearables", "recovery_score", 70, "score",
        source_id="summary-1", source_provider="oura", observed_at="2026-07-14T08:00:00Z",
    )
    upsert_daily_facts(str(db), [legacy])
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "oura", "Oura", "recovery_score", 80, "score",
        source_id="summary-1", source_provider="oura", observed_at="2026-07-14T08:00:00Z",
        source_system="open_wearables", source_record_kind="summary", metric_domain="recovery",
    )])
    _mark_as_pre_contract_db(db)

    facts = list_recommendation_facts(str(db))

    assert len(facts) == 1
    assert facts[0]["provider_id"] == "oura"
    assert facts[0]["value"] == 80


def test_open_wearables_provider_identity_migration_keeps_newer_collision_value(tmp_path):
    import sqlite3

    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "oura", "Oura", "recovery_score", 70, "score",
        source_id="summary-1", source_provider="oura", source_system="open_wearables",
        imported_at="2026-07-14T07:00:00Z", updated_at="2026-07-14T12:00:00Z",
    )])
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO wearable_daily_facts (
                profile_key, date, provider_id, source_label, metric, value_json,
                confidence, freshness, source_id, source_provider, source_system,
                imported_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "1", "2026-07-14", "open_wearables", "Open Wearables",
                "recovery_score", "80", "medium", "fresh", "summary-1", "oura",
                "open_wearables", "2026-07-14T06:00:00Z", "2026-07-14T09:00:00-05:00",
            ),
        )
        conn.execute("PRAGMA user_version = 0")

    facts = list_recommendation_facts(str(db))

    assert len(facts) == 1
    assert facts[0]["provider_id"] == "oura"
    assert facts[0]["value"] == 80
    assert facts[0]["imported_at"] == "2026-07-14T07:00:00Z"


def test_upsert_preserves_first_import_timestamp_on_conflict(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "oura", "Oura", "recovery_score", 70, "score",
        source_id="summary-1", imported_at="2026-07-14T07:00:00Z",
        source_last_synced_at="2026-07-14T08:00:00Z",
    )])
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "oura", "Oura", "recovery_score", 80, "score",
        source_id="summary-1", source_last_synced_at="2026-07-14T09:00:00Z",
    )])

    [fact] = list_recommendation_facts(str(db))

    assert fact["value"] == 80
    assert fact["imported_at"] == "2026-07-14T07:00:00Z"
    assert fact["source_last_synced_at"] == "2026-07-14T09:00:00Z"


@pytest.mark.parametrize(("provider_id", "display_name"), [
    ("oura", "Oura"),
    ("apple", "Apple Health"),
])
def test_open_wearables_provider_identity_migration_updates_display_name(tmp_path, provider_id, display_name):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "open_wearables", "Open Wearables", "recovery_score", 70, "score",
        source_id="summary-1", source_provider=provider_id, observed_at="2026-07-14T08:00:00Z",
    )])
    _mark_as_pre_contract_db(db)

    [fact] = list_recommendation_facts(str(db))

    assert fact["provider_id"] == provider_id
    assert fact["provider_display_name"] == display_name


def test_open_wearables_provider_identity_migration_does_not_promote_source_name(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "open_wearables", "Open Wearables", "recovery_score", 70, "score",
        source_id="summary-1", source_provider="com.example.running-app",
        observed_at="2026-07-14T08:00:00Z",
    )])
    _mark_as_pre_contract_db(db)

    [fact] = list_recommendation_facts(str(db))

    assert fact["provider_id"] == "open_wearables"
    assert fact["provider_display_name"] == "Open Wearables"
    assert fact["source_provider"] is None


def test_open_wearables_provider_identity_migration_maps_healthkit_to_apple(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "open_wearables", "Open Wearables", "recovery_score", 70, "score",
        source_id="summary-1", source_provider="healthkit",
        observed_at="2026-07-14T08:00:00Z",
    )])
    _mark_as_pre_contract_db(db)

    [fact] = list_recommendation_facts(str(db))

    assert fact["provider_id"] == "apple"
    assert fact["source_provider"] == "apple"


def test_undated_replacement_spans_open_wearables_upstream_providers(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "oura", "Oura", "weight", 82.4, "kg",
        source_id="undated-latest", source_provider="oura", source_system="open_wearables",
    )])

    upsert_daily_facts(
        str(db),
        [WearableDailyFact(
            "2026-07-14", "apple_health", "Apple Health", "weight", 82.1, "kg",
            source_id="undated-latest", source_provider="apple_health", source_system="open_wearables",
        )],
        replace_source_ids={"undated-latest"},
    )

    facts = list_recommendation_facts(str(db))
    assert [(fact["provider_id"], fact["value"]) for fact in facts] == [("apple_health", 82.1)]


def test_initialized_fact_reads_do_not_rewrite_rows(tmp_path):
    import sqlite3

    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "oura", "Oura", "recovery_score", 70, "score",
        source_id="summary-1", source_provider="oura", source_system="open_wearables",
        source_record_kind="summary", metric_domain="recovery", capability_state="available",
        source_last_synced_at="2026-07-14T09:00:00Z", imported_at="2026-07-14T09:00:00Z",
    )])
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE fact_update_audit (row_id INTEGER)")
        conn.execute(
            "CREATE TRIGGER audit_fact_update AFTER UPDATE ON wearable_daily_facts "
            "BEGIN INSERT INTO fact_update_audit(row_id) VALUES (1); END"
        )

    list_recommendation_facts(str(db))

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fact_update_audit").fetchone()[0] == 0


def test_initialized_composite_body_fact_reads_do_not_rewrite_rows(tmp_path):
    import sqlite3

    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "open_wearables", "Open Wearables", "weight", 82.4, "kg",
        source_id="undated-latest", source_system="open_wearables",
        source_record_kind="summary", metric_domain="body", capability_state="available",
        source_last_synced_at="2026-07-14T09:00:00Z", imported_at="2026-07-14T09:00:00Z",
    )])
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE body_fact_update_audit (row_id INTEGER)")
        conn.execute(
            "CREATE TRIGGER audit_body_fact_update AFTER UPDATE ON wearable_daily_facts "
            "BEGIN INSERT INTO body_fact_update_audit(row_id) VALUES (1); END"
        )

    list_recommendation_facts(str(db))

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM body_fact_update_audit").fetchone()[0] == 0


def test_legacy_body_skin_temperature_backfill_uses_conservative_valid_enums(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "open_wearables", "Open Wearables", "skin_temperature", 34.2, "c",
        source_provider="oura", observed_at="2026-07-14T08:00:00Z",
    )])
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE wearable_daily_facts SET source_record_kind = NULL, metric_domain = NULL"
        )
        conn.execute("PRAGMA user_version = 0")

    [fact] = list_recommendation_facts(str(db))

    assert fact["metric_domain"] == "body"
    assert fact["source_record_kind"] == "summary"
    assert fact["provider_id"] == "open_wearables"
    assert fact["source_provider"] is None


def test_legacy_recovery_skin_temperature_preserves_recovery_provider_provenance(tmp_path):
    import sqlite3

    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "open_wearables", "Open Wearables", "skin_temperature", 34.2, "c",
        source_id="recovery-summary-1", source_provider="oura",
        observed_at="2026-07-14T08:00:00Z",
    )])
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE wearable_daily_facts SET source_record_kind = NULL, metric_domain = NULL"
        )
        conn.execute("PRAGMA user_version = 0")

    [fact] = list_recommendation_facts(str(db))

    assert fact["metric_domain"] == "recovery"
    assert fact["source_record_kind"] == "summary"
    assert fact["provider_id"] == "oura"
    assert fact["source_provider"] == "oura"


def test_legacy_sleep_fact_backfill_uses_conservative_valid_record_kind(tmp_path):
    import sqlite3

    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "open_wearables", "Open Wearables", "sleep_duration", 420, "min",
        source_id="sleep-1", source_provider="oura", observed_at="2026-07-14T08:00:00Z",
    )])
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE wearable_daily_facts SET source_record_kind = NULL, metric_domain = NULL"
        )
        conn.execute("PRAGMA user_version = 0")

    [fact] = list_recommendation_facts(str(db))

    assert fact["source_record_kind"] == "summary"
    assert fact["metric_domain"] == "sleep"


def test_legacy_composite_body_fact_stays_under_open_wearables_identity(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "open_wearables", "Open Wearables", "weight", 82.4, "kg",
        source_id="undated-latest", source_provider="oura",
    )])
    _mark_as_pre_contract_db(db)

    [fact] = list_recommendation_facts(str(db))

    assert fact["provider_id"] == "open_wearables"
    assert fact["provider_display_name"] == "Open Wearables"
    assert fact["source_provider"] is None


@pytest.mark.parametrize("legacy_eligible", [False, True])
def test_version_two_unattributed_open_wearables_fact_stays_recommendation_ineligible(
    tmp_path, legacy_eligible,
):
    db = tmp_path / "facts.sqlite3"
    today = datetime.now().date().isoformat()
    upsert_daily_facts(str(db), [WearableDailyFact(
        today, "open_wearables", "Open Wearables", "sleep_duration", 420, "min",
        freshness="fresh", used_for_recommendation=legacy_eligible,
    )])
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 2")

    facts = list_recommendation_facts(str(db), usable_only=True)

    assert facts == []


def test_version_two_composite_body_fact_loses_legacy_recommendation_eligibility(tmp_path):
    db = tmp_path / "facts.sqlite3"
    today = datetime.now().date().isoformat()
    upsert_daily_facts(str(db), [WearableDailyFact(
        today, "open_wearables", "Open Wearables", "weight", 82.4, "kg",
        source_provider="oura", source_system="open_wearables", freshness="fresh",
        used_for_recommendation=True,
    )])
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 2")

    facts = list_recommendation_facts(str(db), usable_only=True)

    assert facts == []


def test_version_two_naive_workout_timestamp_is_quarantined(tmp_path):
    db = tmp_path / "facts.sqlite3"
    today = datetime.now().date().isoformat()
    upsert_daily_facts(str(db), [WearableDailyFact(
        today, "apple", "Apple Health", "workout_duration", 60, "min",
        source_id="legacy-workout", source_provider="apple",
        source_system="open_wearables", observed_at=f"{today}T00:30:00",
        freshness="fresh", used_for_recommendation=True,
    )])
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 2")

    assert list_recommendation_facts(str(db), usable_only=True) == []
    upsert_daily_facts(
        str(db),
        [],
        replace_provider_metric_observation_windows={(
            "open_wearables",
            "workout_",
            f"{today}T00:00:00Z",
            f"{today}T01:00:00Z",
        )},
    )
    [fact] = list_recommendation_facts(str(db))
    assert fact["capability_state"] == "legacy_timestamp_unknown"
    assert fact["used_for_recommendation"] is False


def test_version_two_missing_workout_timestamp_is_quarantined(tmp_path):
    db = tmp_path / "facts.sqlite3"
    today = datetime.now().date().isoformat()
    upsert_daily_facts(str(db), [WearableDailyFact(
        today, "apple", "Apple Health", "workout_duration", 60, "min",
        source_id="legacy-workout", source_provider="apple",
        source_system="open_wearables", observed_at=None,
        freshness="fresh", used_for_recommendation=True,
    )])
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 2")

    assert list_recommendation_facts(str(db), usable_only=True) == []
    [fact] = list_recommendation_facts(str(db))
    assert fact["capability_state"] == "legacy_timestamp_unknown"
    assert fact["used_for_recommendation"] is False


def test_version_two_attributed_fact_preserves_explicit_recommendation_exclusion(tmp_path):
    db = tmp_path / "facts.sqlite3"
    today = datetime.now().date().isoformat()
    upsert_daily_facts(str(db), [WearableDailyFact(
        today, "oura", "Oura", "sleep_duration", 420, "min",
        source_provider="oura", source_system="open_wearables",
        freshness="fresh", used_for_recommendation=False,
    )])
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 2")

    assert list_recommendation_facts(str(db), usable_only=True) == []
    [fact] = list_recommendation_facts(str(db))
    assert fact["used_for_recommendation"] is False


def test_version_one_attributed_fact_keeps_legacy_recommendation_eligibility(tmp_path):
    db = tmp_path / "facts.sqlite3"
    today = datetime.now().date().isoformat()
    upsert_daily_facts(str(db), [WearableDailyFact(
        today, "oura", "Oura", "sleep_duration", 420, "min",
        source_provider="oura", source_system="open_wearables",
        freshness="fresh", used_for_recommendation=False,
    )])
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 1")

    [fact] = list_recommendation_facts(str(db), usable_only=True)

    assert fact["provider_id"] == "oura"
    assert fact["used_for_recommendation"] is True


def test_provider_fact_suppresses_unattributed_legacy_recommendation_alternative(tmp_path):
    db = tmp_path / "facts.sqlite3"
    today = datetime.now().date().isoformat()
    upsert_daily_facts(str(db), [WearableDailyFact(
        today, "open_wearables", "Open Wearables", "sleep_duration", 120, "min",
        source_system="open_wearables", freshness="fresh", used_for_recommendation=False,
    )])
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 2")

    upsert_daily_facts(str(db), [WearableDailyFact(
        today, "oura", "Oura", "sleep_duration", 420, "min",
        source_system="open_wearables", source_provider="oura", freshness="fresh",
        used_for_recommendation=True,
    )])

    facts = list_recommendation_facts(str(db), usable_only=True, limit=100)

    assert [(fact["provider_id"], fact["value"]) for fact in facts] == [("oura", 420)]


def test_current_unattributed_fact_remains_a_recommendation_alternative(tmp_path):
    db = tmp_path / "facts.sqlite3"
    today = datetime.now().date().isoformat()
    upsert_daily_facts(str(db), [
        WearableDailyFact(
            today, "open_wearables", "Open Wearables", "sleep_duration", 300, "min",
            source_system="open_wearables", freshness="fresh", used_for_recommendation=True,
        ),
        WearableDailyFact(
            today, "oura", "Oura", "sleep_duration", 480, "min",
            source_system="open_wearables", source_provider="oura", freshness="fresh",
            used_for_recommendation=True,
        ),
    ])

    facts = list_recommendation_facts(str(db), usable_only=True, limit=100)

    assert {(fact["provider_id"], fact["value"]) for fact in facts} == {
        ("open_wearables", 300),
        ("oura", 480),
    }


def test_new_body_skin_temperature_respects_explicit_body_domain(tmp_path):
    db = tmp_path / "facts.sqlite3"
    upsert_daily_facts(str(db), [WearableDailyFact(
        "2026-07-14", "oura", "Oura", "skin_temperature", 34.2, "c",
        source_id="body-reading-1", source_provider="oura", observed_at="2026-07-14T08:00:00Z",
        metric_domain="body",
    )])

    [fact] = list_recommendation_facts(str(db))

    assert fact["metric_domain"] == "body"


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

    with pytest.raises(ValueError):
        upsert_daily_facts(str(db), [{
            "date": "2026-06-26",
            "provider_id": "oura",
            "source_label": "Oura",
            "metric": "sleep",
            "value": 430,
            "provenance": {"provider_user_id": "must-not-persist"},
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
        source_provider="apple", original_label="Traditional Strength Training",
    )])

    fact = list_recommendation_facts(str(db))[0]
    assert fact["category"] == "strength_training"
    assert fact["source_id"] == "workout-1"
    assert fact["source_provider"] == "apple"
    assert fact["original_label"] == "Traditional Strength Training"
    assert fact["source_system"] == "open_wearables"
    assert fact["source_record_kind"] == "event"
    assert fact["metric_domain"] == "training_history"
    assert fact["capability_state"] == "available"
    assert fact["source_last_synced_at"]
    assert fact["imported_at"]


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
