from datetime import date, datetime, timedelta, timezone

import open_wearables_hub as hub
from wearable_fact_store import WearableDailyFact, upsert_daily_facts


def _sleep_extractor_returning(value):
    def _extractor(_payload):
        return value
    return _extractor


def test_workout_marker_builds_sleep_and_payload_markers():
    data = {
        "sleep": {"raw": "ignored-by-extractor"},
        "workouts": [{"id": 1}],
        "activity_summary": {"steps": 1200},
    }
    sleep_extractor = _sleep_extractor_returning({
        "duration_min": 480,
        "avg_hr": 55,
        "event_time": "2026-06-26T07:00:00",
        "recent": True,
    })

    marker = hub.workout_marker(data, sleep_extractor=sleep_extractor)

    assert marker["configured"] is True
    assert marker["sleep"]["duration_min"] == 480
    assert marker["sleep"]["recent"] is True
    assert marker["workouts"]["bytes"] > 0
    assert marker["activity_summary"]["hash"]


def test_workout_marker_handles_missing_sleep():
    marker = hub.workout_marker({}, sleep_extractor=_sleep_extractor_returning(None))

    assert marker["sleep"] == {
        "duration_min": None,
        "avg_hr": None,
        "event_time": None,
        "recent": None,
    }
    assert marker["workouts"] is None
    assert marker["activity_summary"] is None


def test_sync_metadata_maps_counts_and_errors():
    metadata = hub.sync_metadata({
        "fetched_at": "2026-06-29T10:00:00",
        "sleep": {"events": [{"a": 1}]},
        "workouts": {"data": [{"a": 1}, {"a": 2}]},
        "errors": {"auth": True, "unexpected_key": True},
    })

    assert metadata["status"] == "success"
    assert metadata["source"] == "open_wearables"
    assert metadata["fetched_at"] == "2026-06-29T10:00:00"
    assert metadata["counts"] == {"sleep": 1, "workouts": 2}
    assert metadata["errors"] == {
        "auth": "open_wearables_auth_error",
        "sync": "open_wearables_sync_error",
    }


def test_sync_metadata_defaults_fetched_at_when_missing():
    fixed_now = datetime(2026, 6, 29, 12, 0, 0)
    metadata = hub.sync_metadata({}, now=lambda: fixed_now)

    assert metadata["fetched_at"] == fixed_now.isoformat()
    assert metadata["counts"] == {}
    assert metadata["errors"] == {}


def test_sync_metadata_counts_single_body_summary():
    metadata = hub.sync_metadata({"body_summary": {"slow_changing": {"weight_kg": 82.4}}})

    assert metadata["counts"]["body_summary"] == 1


def test_status_source_projects_fields_from_status_payload():
    source = hub.status_source({
        "status": "connected",
        "configured": True,
        "last_checked_at": "2026-06-29T10:00:00",
        "providers": [{"provider_id": "whoop"}],
        "facts_ready": True,
        "replacement_sources": ["oura"],
        "replacement_source_dates": {"oura": "2026-06-28"},
    })

    assert source["status"] == "connected"
    assert source["connected"] is True
    assert source["configured"] is True
    assert source["used_for_recommendation"] is True
    assert source["providers"] == [{"provider_id": "whoop"}]
    assert source["facts_ready"] is True
    assert source["replacement_sources"] == ["oura"]
    assert source["replacement_source_dates"] == {"oura": "2026-06-28"}


def test_status_source_handles_missing_payload():
    source = hub.status_source(None)

    assert source["status"] is None
    assert source["configured"] is False
    assert source["connected"] is False
    assert source["providers"] == []
    assert source["facts_ready"] is False


def test_conservative_modifier_applies_sleep_and_activity_caution():
    facts = [
        {"metric": "sleep_duration", "value": 300},
        {"metric": "active_minutes", "value": 120},
    ]

    modifier = hub.conservative_modifier(facts)

    assert modifier["applied"] is True
    assert set(modifier["applied_modifiers"]) == {"sleep_caution", "activity_caution"}
    assert modifier["detail"]


def test_conservative_modifier_no_caution_when_within_bounds():
    facts = [
        {"metric": "sleep_duration", "value": 480},
        {"metric": "active_minutes", "value": 45},
    ]

    modifier = hub.conservative_modifier(facts)

    assert modifier["applied"] is False
    assert modifier["applied_modifiers"] == []
    assert modifier["detail"] is None


def test_apply_recommendation_guard_downgrades_once_when_modifier_applies():
    facts = [{"metric": "sleep_duration", "value": 200}]

    recommendation, modifier = hub.apply_recommendation_guard(
        "intensity",
        facts,
        downgrade_once=lambda rec: "moderate" if rec == "intensity" else rec,
    )

    assert recommendation == "moderate"
    assert modifier["applied"] is True


def test_apply_recommendation_guard_never_hardens_recommendation():
    """Mirrors the app.py-level regression guard in
    tests/test_recommendation_sources.py::test_open_wearables_modifier_never_hardens_recommendation
    at the module boundary."""
    recommendation, modifier = hub.apply_recommendation_guard(
        "moderate",
        [WearableDailyFact("2026-06-26", "open_wearables", "Open Wearables", "sleep_duration", 480, "min", freshness="fresh").public_dict()],
        downgrade_once=lambda _rec: "recovery",
    )

    assert recommendation == "moderate"
    assert modifier["applied"] is False


def test_store_wearable_facts_is_profile_scoped(tmp_path):
    """FIT-250 redo regression guard: store_wearable_facts must persist facts
    under the caller-supplied profile_key (per-profile isolation), never a
    shared/global key -- mirrors the same hazard the workout marker cache
    guards against in app.py."""
    db_file = str(tmp_path / "wearable_facts.sqlite3")

    facts_count = hub.store_wearable_facts(
        {
            "fetched_at": "2026-06-29T10:00:00",
            "activity_summary": {"summaries": [
                {"day": "2026-06-28", "steps": 1200, "source": {"provider": "oura"}},
            ]},
            "sleep": {"events": [
                {"end": "2026-06-28T23:58:00Z", "duration_min": 420, "source": {"provider": "oura"}},
            ]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda payload: [
            {"date": date(2026, 6, 28), "steps": 1200, "resting": None, "active_minutes": None, "raw": {"source": {"provider": "oura"}}}
        ] if payload else [],
        sleep_extractor=lambda payload: (
            {"duration_min": 420, "avg_hr": None, "event_time": "2026-06-28T23:58:00", "recent": True, "raw": {"source": {"provider": "oura"}}}
            if payload else None
        ),
        row_replacement_sources=lambda row: ["oura"] if row else [],
    )

    assert facts_count == 2

    from wearable_fact_store import list_recommendation_facts, list_wearable_sources

    other_profile_facts = list_recommendation_facts(db_file, profile_key="some-other-profile")
    assert other_profile_facts == []

    scoped_facts = list_recommendation_facts(db_file, profile_key="profile-42")
    assert len(scoped_facts) == 2

    sources = list_wearable_sources(db_file, profile_key="profile-42")
    open_wearables_source = next(s for s in sources if s["provider_id"] == "open_wearables")
    assert open_wearables_source["capabilities"]["replacement_sources"] == ["oura"]


def test_store_wearable_facts_maps_sleep_recovery_activity_body_and_workouts(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    data = {
        "fetched_at": "2026-06-29T10:00:00",
        "activity_summary": {"summaries": [{"day": "2026-06-28", "steps": 8200}]},
        "sleep": {"events": [{"end": "2026-06-28T07:00:00Z", "duration_min": 430}]},
        "recovery_summary": {"summaries": [{
            "date": "2026-06-28", "recovery_score": 72, "hrv": 48,
            "resting_heart_rate": 53, "spo2": 97,
        }]},
        "body_summary": {
            "source": {"provider": "oura"},
            "slow_changing": {"weight_kg": 82.4, "body_fat_percent": 17.2},
            "averaged": {"period_end": "2026-06-28T23:59:00Z", "resting_heart_rate_bpm": 53},
            "latest": {
                "body_temperature_celsius": 36.7,
                "body_temperature_measured_at": "2026-06-27T22:00:00Z",
            },
        },
        "workouts": {"events": [{
            "id": "workout-1", "start": "2026-06-29T04:00:00Z", "zone_offset": "-05:00",
            "duration_seconds": 3300, "type": "strength_training", "name": "Evening Lift",
            "provider": "apple_health", "calories_kcal": 410,
            "avg_heart_rate_bpm": 132, "max_heart_rate_bpm": 171,
        }, {
            "id": "workout-2", "start": "2026-06-28T07:00:00Z",
            "duration_seconds": 1800, "type": "running", "name": "Morning Miles",
            "provider": "apple_health", "calories_kcal": 260,
        }]},
    }

    count = hub.store_wearable_facts(
        data,
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [
            {"date": date(2026, 6, 28), "steps": 8200, "resting": None, "active_minutes": None, "raw": {}}
        ],
        sleep_extractor=lambda _payload: {
            "duration_min": 430, "avg_hr": None, "event_time": "2026-06-28T07:00:00",
            "recent": True, "efficiency_percent": 91.5, "is_nap": False,
            "raw": {"id": "sleep-1", "source": {"provider": "oura"}},
        },
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    by_metric = {fact["metric"]: fact for fact in facts}
    assert count == len(facts)
    assert {
        "steps", "sleep_duration", "recovery_score", "heart_rate_variability",
        "resting_heart_rate", "blood_oxygen", "weight", "body_fat_percent",
        "workout_duration", "workout_active_calories", "sleep_efficiency", "sleep_is_nap",
    }.issubset(by_metric)
    workout_facts = [fact for fact in facts if fact["metric"] == "workout_duration"]
    assert len(workout_facts) == 2
    workout = next(fact for fact in workout_facts if fact["source_id"] == "workout-1")
    assert workout["category"] == "strength_training"
    assert workout["source_id"] == "workout-1"
    assert workout["source_provider"] == "apple"
    assert workout["original_label"] == "Evening Lift"
    assert workout["value"] == 55
    assert workout["observed_at"] == "2026-06-29T04:00:00Z"
    assert workout["date"] == "2026-06-28"
    assert workout["freshness"] == "fresh"
    assert by_metric["weight"]["date"] == "2026-06-29"
    assert by_metric["weight"]["freshness"] == "unknown"
    assert by_metric["weight"]["source_id"] == "undated-latest"
    assert by_metric["resting_heart_rate"]["value"] == 53
    assert by_metric["resting_heart_rate_average"]["value"] == 53
    assert by_metric["body_temperature"]["date"] == "2026-06-27"
    assert by_metric["sleep_duration"]["source_id"] == "sleep-1"
    assert by_metric["sleep_duration"]["source_provider"] == "oura"
    assert by_metric["sleep_duration"]["observed_at"] == "2026-06-28T07:00:00"
    assert by_metric["steps"]["observed_at"] is None
    run = next(fact for fact in workout_facts if fact["source_id"] == "workout-2")
    assert run["category"] == "cardio"
    assert hub._workout_category("indoor_cycling") == "cardio"
    assert hub._workout_category("elliptical") == "cardio"
    assert hub._workout_category("rowing_machine") == "cardio"
    assert hub._workout_category("stair_climbing") == "cardio"
    assert hub._workout_category("hiking") == "cardio"
    assert hub._workout_category("core_training") == "strength_training"
    assert hub._workout_category("Indoor Cycling") == "cardio"
    assert hub._workout_category("Trail Running") == "cardio"
    assert hub._workout_category("Mountain Biking") == "cardio"
    assert hub._workout_category("Rowing Machine") == "cardio"
    assert hub._workout_category("Stair Climbing") == "cardio"
    for workout_type in (
        "mixed_cardio", "hiit", "running_treadmill", "cycling_stationary",
        "swimming_pool", "swimming_open_water", "stair_climbing_machine",
    ):
        assert hub._workout_category(workout_type) == "cardio"


def test_store_wearable_facts_derives_duration_for_minimal_valid_workout(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()

    count = hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T12:00:00Z",
            "_workout_snapshot_complete": True,
            "_workout_query": {
                "start_at": f"{today}T00:00:00Z",
                "end_at": f"{(date.today() + timedelta(days=1)).isoformat()}T00:00:00Z",
            },
            "workouts": {"data": [{
                "id": "workout-minimal",
                "type": "running",
                "start_time": f"{today}T08:00:00Z",
                "end_time": f"{today}T09:00:00Z",
                "source": {"provider": "apple_health"},
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    [fact] = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert count == 1
    assert fact["metric"] == "workout_duration"
    assert fact["value"] == 60
    assert fact["provider_id"] == "apple"
    assert fact["source_system"] == "open_wearables"
    assert fact["source_record_kind"] == "event"
    assert fact["metric_domain"] == "training_history"


def test_provider_display_names_use_canonical_labels():
    assert hub._provider_display_name("oura") == "Oura"
    assert hub._provider_display_name("whoop") == "WHOOP"
    assert hub._provider_display_name("apple") == "Apple Health"
    assert hub._provider_display_name("apple_health") == "Apple Health"


def test_store_wearable_facts_rejects_body_measurement_without_timestamp(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")

    count = hub.store_wearable_facts(
        {
            "fetched_at": "2026-06-30T10:00:00Z",
            "body_summary": {"latest": {"skin_temperature_celsius": 34.2}},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    assert count == 0
    assert list_recommendation_facts(db_file, limit=100, profile_key="profile-42") == []


def test_body_summary_averages_use_recovery_domain(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")

    hub.store_wearable_facts(
        {
            "fetched_at": "2026-07-14T12:00:00Z",
            "body_summary": {"averaged": {
                "period_end": "2026-07-14T11:00:00Z",
                "resting_heart_rate_bpm": 52,
                "avg_hrv_sdnn_ms": 48,
                "avg_hrv_rmssd_ms": 44,
            }},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert {fact["metric_domain"] for fact in facts} == {"recovery"}


def test_composite_body_summary_does_not_promote_top_level_provider(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    hub.store_wearable_facts(
        {
            "fetched_at": "2026-07-14T12:00:00Z",
            "body_summary": {
                "source": {"provider": "oura"},
                "slow_changing": {"weight_kg": 82.4},
                "averaged": {
                    "period_end": "2026-07-14T11:00:00Z",
                    "resting_heart_rate_bpm": 52,
                },
                "latest": {
                    "skin_temperature_celsius": 34.2,
                    "skin_temperature_measured_at": "2026-07-14T10:00:00Z",
                },
            },
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert {fact["provider_id"] for fact in facts} == {"open_wearables"}
    assert {fact["source_provider"] for fact in facts} == {None}


def test_store_wearable_facts_drops_non_finite_health_values(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()

    count = hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T10:00:00",
            "activity_summary": {"data": [{"date": today}]},
            "sleep": {"events": [{}]},
            "recovery_summary": {"data": [
                {"date": today, "recovery_score": float("nan"), "source": {"provider": "oura"}},
                {"date": today, "recovery_score": 80, "source": {"provider": "whoop"}},
            ]},
            "body_summary": {"slow_changing": {"weight_kg": float("inf")}},
            "workouts": {"events": [{
                "id": "workout-nan",
                "start": f"{today}T08:00:00Z",
                "zone_offset": "+00:00",
                "duration_min": float("nan"),
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [{
            "date": date.today(),
            "steps": None,
            "resting": None,
            "active_minutes": float("nan"),
            "active_calories": None,
            "distance": None,
            "raw": {"source": {"provider": "oura"}},
        }],
        sleep_extractor=lambda _payload: {
            "duration_min": float("nan"),
            "avg_hr": None,
            "event_time": f"{today}T07:00:00Z",
            "recent": True,
            "raw": {"source": {"provider": "oura"}},
        },
        row_replacement_sources=lambda row: [row["source"]["provider"]] if row.get("source") else [],
    )

    from wearable_fact_store import list_recommendation_facts, list_wearable_sources

    assert count == 1
    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert {(fact["metric"], fact["source_provider"]) for fact in facts} == {
        ("recovery_score", "whoop")
    }
    [source] = list_wearable_sources(db_file, profile_key="profile-42")
    assert source["capabilities"]["replacement_sources"] == ["whoop"]


def test_store_wearable_facts_keeps_temperature_deviation_distinct_from_measurement(tmp_path):
    today = date.today().isoformat()
    from wearable_fact_store import list_recommendation_facts

    for temperature_field in ("temperature_deviation", "temperature_delta"):
        db_file = str(tmp_path / f"{temperature_field}.sqlite3")
        hub.store_wearable_facts(
            {
                "fetched_at": f"{today}T10:00:00Z",
                "recovery_summary": {"data": [{
                    "date": today,
                    temperature_field: -0.3,
                    "source": {"provider": "oura"},
                }]},
                "body_summary": {"latest": {
                    "skin_temperature_celsius": 34.2,
                    "skin_temperature_measured_at": f"{today}T09:00:00Z",
                }},
            },
            db_file=db_file,
            profile_key="profile-42",
            activity_extractor=lambda _payload: [],
            sleep_extractor=lambda _payload: None,
            row_replacement_sources=lambda _row: [],
        )

        facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
        assert {(fact["metric"], fact["value"]) for fact in facts} == {
            ("temperature_deviation", -0.3),
            ("skin_temperature", 34.2),
        }


def test_body_measurement_freshness_uses_exact_observation_timestamp(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")

    hub.store_wearable_facts(
        {
            "fetched_at": "2026-07-01T00:10:00-05:00",
            "body_summary": {"latest": {
                "skin_temperature_celsius": 34.2,
                "skin_temperature_measured_at": "2026-06-29T23:50:00-05:00",
            }},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    [fact] = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert fact["metric"] == "skin_temperature"
    assert fact["freshness"] == "aging"
    assert fact["observed_at"] == "2026-06-29T23:50:00-05:00"


def test_store_wearable_facts_rejects_malformed_temporal_keys_and_provenance(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")

    count = hub.store_wearable_facts(
        {
            "fetched_at": "2026-06-29T10:00:00Z",
            "sleep": {"events": [{}]},
            "recovery_summary": {"data": [{"date": "not-a-date", "recovery_score": 80}]},
            "body_summary": {"latest": {
                "body_temperature_celsius": 36.7,
                "body_temperature_measured_at": "not-a-timestamp",
            }},
            "workouts": {"events": [{
                "id": "workout-bad-time",
                "start": "also-not-a-timestamp",
                "zone_offset": "+00:00",
                "duration_min": 30,
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: {
            "duration_min": 420,
            "avg_hr": None,
            "event_time": "bad-sleep-time",
            "recent": True,
            "raw": {},
        },
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    assert count == 0
    assert list_recommendation_facts(db_file, limit=100, profile_key="profile-42") == []


def test_store_wearable_facts_drops_invalid_observation_provenance(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()

    hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T10:00:00Z",
            "sleep": {"events": [{}]},
            "recovery_summary": {"data": [{
                "date": today,
                "timestamp": "not-a-timestamp",
                "recovery_score": 80,
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: {
            "duration_min": 420,
            "avg_hr": None,
            "event_time": f"{today}T07:00:00Z",
            "observed_at": "also-not-a-timestamp",
            "recent": True,
            "raw": {},
        },
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert {fact["metric"] for fact in facts} == {"sleep_duration", "recovery_score"}
    assert {fact["observed_at"] for fact in facts} == {None}


def test_successful_empty_workout_snapshot_removes_deleted_workout_facts_only(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    historical_day = (date.today() - timedelta(days=10)).isoformat()
    query_start = (date.today() - timedelta(days=6)).isoformat()
    query_end = (date.today() + timedelta(days=1)).isoformat()
    upsert_daily_facts(db_file, [WearableDailyFact(
        historical_day,
        "open_wearables",
        "Open Wearables",
        "workout_duration",
        45,
        "min",
        source_id="historical-workout",
        observed_at=f"{historical_day}T08:00:00Z",
        freshness="stale",
    ), WearableDailyFact(
        (date.today() - timedelta(days=7)).isoformat(),
        "open_wearables",
        "Open Wearables",
        "workout_duration",
        30,
        "min",
        source_id="negative-offset-window-workout",
        observed_at=f"{query_start}T01:00:00Z",
        freshness="stale",
    )], profile_key="profile-42")
    common = {
        "fetched_at": f"{today}T10:00:00Z",
        "_workout_snapshot_complete": True,
        "_workout_query": {
            "start_at": f"{query_start}T00:00:00Z",
            "end_at": f"{query_end}T00:00:00Z",
        },
        "recovery_summary": {"data": [{"date": today, "recovery_score": 80}]},
    }
    hub.store_wearable_facts(
        {
            **common,
            "workouts": {"events": [{
                "id": workout_id,
                "start": f"{today}T08:00:00Z",
                "zone_offset": "+00:00",
                "duration_min": 30,
            } for workout_id in ("workout-1", "workout-2")]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    hub.store_wearable_facts(
        {**common, "workouts": {"events": []}},
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert {fact["metric"] for fact in facts} == {"recovery_score", "workout_duration"}
    assert {
        fact["source_id"] for fact in facts if fact["metric"] == "workout_duration"
    } == {"historical-workout"}


def test_failed_workout_snapshot_preserves_prior_workout_facts(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T10:00:00Z",
            "workouts": {"events": [{
                "id": "workout-1",
                "start": f"{today}T08:00:00Z",
                "zone_offset": "+00:00",
                "duration_min": 30,
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T11:00:00Z",
            "workouts": None,
            "errors": {"workouts": "upstream unavailable"},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert {(fact["metric"], fact["source_id"]) for fact in facts} == {
        ("workout_duration", "workout-1")
    }


def test_uncertified_empty_workout_snapshot_preserves_prior_workout_facts(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    upsert_daily_facts(db_file, [WearableDailyFact(
        today,
        "open_wearables",
        "Open Wearables",
        "workout_duration",
        30,
        "min",
        source_id="workout-1",
        observed_at=f"{today}T08:00:00Z",
        freshness="fresh",
        used_for_recommendation=True,
    )], profile_key="profile-42")

    hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T11:00:00Z",
            "_workout_query": {
                "start_at": f"{(date.today() - timedelta(days=6)).isoformat()}T00:00:00Z",
                "end_at": f"{(date.today() + timedelta(days=1)).isoformat()}T00:00:00Z",
            },
            "workouts": {"events": []},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert {(fact["metric"], fact["source_id"]) for fact in facts} == {
        ("workout_duration", "workout-1")
    }


def test_malformed_workout_snapshot_preserves_prior_workout_facts(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    upsert_daily_facts(db_file, [WearableDailyFact(
        today,
        "open_wearables",
        "Open Wearables",
        "workout_duration",
        30,
        "min",
        source_id="workout-1",
        freshness="fresh",
        used_for_recommendation=True,
    )], profile_key="profile-42")

    hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T11:00:00Z",
            "_workout_snapshot_complete": True,
            "_workout_query": {
                "start_at": f"{(date.today() - timedelta(days=6)).isoformat()}T00:00:00Z",
                "end_at": f"{(date.today() + timedelta(days=1)).isoformat()}T00:00:00Z",
            },
            "workouts": {"events": [{
                "id": "bad-workout",
                "start": "not-a-timestamp",
                "duration_min": 30,
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert {(fact["metric"], fact["source_id"]) for fact in facts} == {
        ("workout_duration", "workout-1")
    }


def test_mixed_timezone_workout_duration_preserves_prior_snapshot_facts(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    upsert_daily_facts(db_file, [WearableDailyFact(
        today, "open_wearables", "Open Wearables", "workout_duration", 30, "min",
        source_id="workout-1", observed_at=f"{today}T08:00:00Z",
        freshness="fresh", used_for_recommendation=True,
    )], profile_key="profile-42")

    hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T11:00:00Z",
            "_workout_snapshot_complete": True,
            "_workout_query": {
                "start_at": f"{(date.today() - timedelta(days=6)).isoformat()}T00:00:00Z",
                "end_at": f"{(date.today() + timedelta(days=1)).isoformat()}T00:00:00Z",
            },
            "workouts": {"events": [{
                "id": "mixed-time-workout",
                "type": "running",
                "start_time": f"{today}T08:00:00Z",
                "end_time": f"{today}T09:00:00",
                "load": 12.5,
                "source": {"provider": "oura"},
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert ("workout_duration", "workout-1") in {
        (fact["metric"], fact["source_id"]) for fact in facts
    }


def test_workout_snapshot_without_source_id_preserves_prior_workout_facts(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    upsert_daily_facts(db_file, [WearableDailyFact(
        today,
        "open_wearables",
        "Open Wearables",
        "workout_duration",
        30,
        "min",
        source_id="workout-1",
        observed_at=f"{today}T08:00:00Z",
        freshness="fresh",
        used_for_recommendation=True,
    )], profile_key="profile-42")

    hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T11:00:00Z",
            "_workout_snapshot_complete": True,
            "_workout_query": {
                "start_at": f"{(date.today() - timedelta(days=6)).isoformat()}T00:00:00Z",
                "end_at": f"{(date.today() + timedelta(days=1)).isoformat()}T00:00:00Z",
            },
            "workouts": {"events": [{
                "start": f"{today}T09:00:00Z",
                "duration_min": 45,
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    assert {(fact["metric"], fact["source_id"], fact["value"]) for fact in facts} == {
        ("workout_duration", "workout-1", 30)
    }


def test_future_observation_is_not_recommendation_eligible(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    fetched_at = datetime.now().astimezone()
    future = fetched_at + timedelta(days=2)
    assert hub._fact_freshness(future.isoformat(), fetched_at.isoformat()) == "unknown"


def test_store_wearable_facts_uses_observation_freshness_and_tolerates_partial_errors(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today()
    stale_day = today - timedelta(days=4)

    hub.store_wearable_facts(
        {
            "fetched_at": f"{today.isoformat()}T10:00:00",
            "recovery_summary": {"data": [
                {"date": today.isoformat(), "recovery_score": 80},
                {"date": stale_day.isoformat(), "recovery_score": 60},
            ]},
            "errors": {"body_summary": "unsupported"},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts, list_wearable_sources

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    freshness = {fact["date"]: fact["freshness"] for fact in facts}
    assert freshness == {today.isoformat(): "fresh", stale_day.isoformat(): "stale"}
    assert list_wearable_sources(db_file, profile_key="profile-42")[0]["status"] == "fresh"


def test_recovery_facts_preserve_same_day_provider_identity(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    hub.store_wearable_facts(
        {"fetched_at": f"{today}T10:00:00", "recovery_summary": {"data": [
            {"date": today, "recovery_score": 70, "source": {"provider": "oura"}},
            {"date": today, "recovery_score": 80, "source": {"provider": "whoop"}},
        ]}},
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda row: [row["source"]["provider"]],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    recovery = [fact for fact in facts if fact["metric"] == "recovery_score"]
    assert {fact["provider_id"] for fact in recovery} == {"oura", "whoop"}
    assert {fact["source_record_id"] for fact in recovery} == {None}


def test_empty_sync_keeps_source_active_while_persisted_fact_is_usable(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    upsert_daily_facts(db_file, [WearableDailyFact(
        today, "open_wearables", "Open Wearables", "recovery_score", 80,
        "score", freshness="fresh", used_for_recommendation=True,
    )], profile_key="profile-42")

    hub.store_wearable_facts(
        {"fetched_at": datetime.now().astimezone().isoformat()},
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_wearable_sources

    source = list_wearable_sources(db_file, profile_key="profile-42")[0]
    assert source["status"] == "fresh"
    assert source["used_for_recommendation"] is True
    assert source["last_data_point"] == today


def test_failed_sync_reports_error_even_with_usable_cached_fact(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    upsert_daily_facts(db_file, [WearableDailyFact(
        today, "open_wearables", "Open Wearables", "recovery_score", 80,
        "score", freshness="fresh", used_for_recommendation=True,
    )], profile_key="profile-42")

    hub.store_wearable_facts(
        {"fetched_at": datetime.now().astimezone().isoformat(), "errors": {"auth": "missing_token"}},
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_wearable_sources

    source = list_wearable_sources(db_file, profile_key="profile-42")[0]
    assert source["status"] == "error"
    assert source["used_for_recommendation"] is True


def test_undated_body_latest_replaces_prior_projection(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    for fetched_at in ("2026-06-28T10:00:00", "2026-06-29T10:00:00"):
        hub.store_wearable_facts(
            {"fetched_at": fetched_at, "body_summary": {"slow_changing": {"weight_kg": 82.4}}},
            db_file=db_file,
            profile_key="profile-42",
            activity_extractor=lambda _payload: [],
            sleep_extractor=lambda _payload: None,
            row_replacement_sources=lambda _row: [],
        )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, limit=100, profile_key="profile-42")
    weights = [fact for fact in facts if fact["metric"] == "weight"]
    assert len(weights) == 1
    assert weights[0]["date"] == "2026-06-29"


def test_recommendation_facts_filters_provider_and_freshness(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    upsert_daily_facts(
        db_file,
        [
            WearableDailyFact(today, "open_wearables", "Open Wearables", "steps", 1200, "count", freshness="fresh", used_for_recommendation=True),
            WearableDailyFact(today, "oura", "Oura", "readiness", 70, "score", freshness="fresh"),
            WearableDailyFact("2026-06-20", "open_wearables", "Open Wearables", "steps", 900, "count", freshness="stale"),
        ],
        profile_key="profile-1",
    )

    facts = hub.recommendation_facts(db_file, "profile-1")

    assert len(facts) == 1
    assert facts[0]["provider_id"] == "open_wearables"
    assert facts[0]["freshness"] == "fresh"


def test_recommendation_facts_filters_before_applying_limit(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    rows = [
        WearableDailyFact(today, "open_wearables", "Open Wearables", f"unknown_{index}", index, freshness="unknown")
        for index in range(25)
    ]
    rows.append(WearableDailyFact(
        today, "open_wearables", "Open Wearables", "steps", 9000, "count", freshness="aging", used_for_recommendation=True
    ))
    upsert_daily_facts(db_file, rows, profile_key="profile-1")

    facts = hub.recommendation_facts(db_file, "profile-1", limit=1)

    assert [fact["metric"] for fact in facts] == ["steps"]


def test_recommendation_facts_recomputes_age_for_orphaned_fresh_rows(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    upsert_daily_facts(db_file, [WearableDailyFact(
        "2020-01-01", "open_wearables", "Open Wearables", "recovery_score", 80,
        "score", freshness="fresh", used_for_recommendation=True,
    )], profile_key="profile-1")

    assert hub.recommendation_facts(db_file, "profile-1") == []


def test_recommendation_facts_keeps_latest_usable_fact_per_metric_before_limit(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today()
    rows = [
        WearableDailyFact(today.isoformat(), "open_wearables", "Open Wearables", f"metric_{index}", index, freshness="fresh", used_for_recommendation=True)
        for index in range(25)
    ]
    rows.append(WearableDailyFact(
        (today - timedelta(days=1)).isoformat(), "open_wearables", "Open Wearables",
        "sleep_duration", 300, "min", freshness="aging", used_for_recommendation=True,
    ))
    upsert_daily_facts(db_file, rows, profile_key="profile-1")

    facts = hub.recommendation_facts(db_file, "profile-1", limit=30)

    assert any(fact["metric"] == "sleep_duration" for fact in facts)


def test_recommendation_facts_uses_observation_time_as_same_day_tiebreaker(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    current = datetime.now(timezone.utc)
    upsert_daily_facts(db_file, [
        WearableDailyFact(today, "open_wearables", "Open Wearables", "sleep_duration", 300, "min", freshness="fresh", source_id="sleep-1", observed_at=(current - timedelta(hours=2)).isoformat(), used_for_recommendation=True),
        WearableDailyFact(today, "open_wearables", "Open Wearables", "sleep_duration", 420, "min", freshness="fresh", source_id="sleep-2", observed_at=(current - timedelta(hours=1)).isoformat(), used_for_recommendation=True),
    ], profile_key="profile-1")

    [fact] = hub.recommendation_facts(db_file, "profile-1", limit=1)

    assert fact["value"] == 420


def test_source_provider_canonicalizes_open_wearables_source_names():
    assert hub._source_provider({"source": {"provider": "apple_health_xml"}}) == "apple"
    assert hub._source_provider({"source": {"provider": "healthkit"}}) == "apple"
    assert hub._source_provider({"source": {"provider": "Garmin Connect"}}) == "garmin"
    assert hub._source_provider({"source": {"provider": "com.apple.Health"}}) == "apple"
    assert hub._source_provider({"source": {"provider": "com.example.running-app"}}) is None


def test_recovery_sleep_metric_keeps_summary_record_kind(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    hub.store_wearable_facts(
        {
            "fetched_at": "2026-07-14T10:00:00Z",
            "recovery_summary": {"summaries": [{
                "id": "recovery-1",
                "date": "2026-07-14",
                "recorded_at": "2026-07-14T08:00:00Z",
                "sleep_duration_seconds": 25200,
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    [fact] = list_recommendation_facts(db_file, profile_key="profile-42")
    assert fact["metric"] == "sleep_duration"
    assert fact["source_record_kind"] == "summary"
    assert fact["metric_domain"] == "sleep"


def test_activity_summary_resting_heart_rate_uses_recovery_domain(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    hub.store_wearable_facts(
        {"fetched_at": "2026-07-14T10:00:00Z"},
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [{
            "date": date(2026, 7, 14),
            "steps": None,
            "resting": 52,
            "active_minutes": None,
            "raw": {},
        }],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    [fact] = list_recommendation_facts(db_file, profile_key="profile-42")
    assert fact["metric"] == "resting_heart_rate"
    assert fact["metric_domain"] == "recovery"


def test_workout_load_uses_load_domain(tmp_path):
    today = date.today().isoformat()
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    hub.store_wearable_facts(
        {
            "fetched_at": f"{today}T12:00:00Z",
            "_workout_snapshot_complete": True,
            "_workout_query": {
                "start_at": f"{today}T00:00:00Z",
                "end_at": f"{(date.today() + timedelta(days=1)).isoformat()}T00:00:00Z",
            },
            "workouts": {"data": [{
                "id": "workout-load-1",
                "type": "running",
                "start_time": f"{today}T08:00:00Z",
                "end_time": f"{today}T09:00:00Z",
                "load": 12.5,
                "source": {"provider": "oura"},
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    facts = list_recommendation_facts(db_file, profile_key="profile-42")
    workout_load = next(fact for fact in facts if fact["metric"] == "workout_load")
    assert workout_load["metric_domain"] == "load"


def test_recovery_skin_temperature_keeps_recovery_domain_without_source_id(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    hub.store_wearable_facts(
        {
            "fetched_at": "2026-07-14T10:00:00Z",
            "recovery_summary": {"summaries": [{
                "date": "2026-07-14",
                "recorded_at": "2026-07-14T08:00:00Z",
                "skin_temperature": 34.1,
            }]},
        },
        db_file=db_file,
        profile_key="profile-42",
        activity_extractor=lambda _payload: [],
        sleep_extractor=lambda _payload: None,
        row_replacement_sources=lambda _row: [],
    )

    from wearable_fact_store import list_recommendation_facts

    [fact] = list_recommendation_facts(db_file, profile_key="profile-42")
    assert fact["metric_domain"] == "recovery"
