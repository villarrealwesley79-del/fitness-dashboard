from datetime import date, datetime

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
            "id": "workout-1", "start": "2026-06-28T18:00:00Z",
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
            "recent": True, "raw": {},
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
        "workout_duration", "workout_active_calories",
    }.issubset(by_metric)
    workout_facts = [fact for fact in facts if fact["metric"] == "workout_duration"]
    assert len(workout_facts) == 2
    workout = next(fact for fact in workout_facts if fact["source_id"] == "workout-1")
    assert workout["category"] == "strength_training"
    assert workout["source_id"] == "workout-1"
    assert workout["source_provider"] == "apple_health"
    assert workout["original_label"] == "Evening Lift"
    assert workout["value"] == 55
    assert workout["observed_at"] == "2026-06-28T18:00:00Z"
    assert workout["freshness"] == "fresh"
    assert by_metric["weight"]["date"] == "2026-06-29"
    assert by_metric["weight"]["freshness"] == "unknown"
    assert by_metric["weight"]["source_id"] == "undated-latest"
    assert by_metric["resting_heart_rate"]["value"] == 53
    assert by_metric["resting_heart_rate_average"]["value"] == 53
    assert by_metric["body_temperature"]["date"] == "2026-06-27"
    run = next(fact for fact in workout_facts if fact["source_id"] == "workout-2")
    assert run["category"] == "cardio"
    assert hub._workout_category("indoor_cycling") == "cardio"
    assert hub._workout_category("elliptical") == "cardio"
    assert hub._workout_category("rowing_machine") == "cardio"
    assert hub._workout_category("stair_climbing") == "cardio"
    assert hub._workout_category("hiking") == "cardio"
    assert hub._workout_category("core_training") == "strength_training"


def test_store_wearable_facts_uses_observation_freshness_and_tolerates_partial_errors(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")

    hub.store_wearable_facts(
        {
            "fetched_at": "2026-06-29T10:00:00",
            "recovery_summary": {"data": [
                {"date": "2026-06-29", "recovery_score": 80},
                {"date": "2026-06-25", "recovery_score": 60},
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
    assert freshness == {"2026-06-29": "fresh", "2026-06-25": "stale"}
    assert list_wearable_sources(db_file, profile_key="profile-42")[0]["status"] == "fresh"


def test_recommendation_facts_filters_provider_and_freshness(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    today = date.today().isoformat()
    upsert_daily_facts(
        db_file,
        [
            WearableDailyFact(today, "open_wearables", "Open Wearables", "steps", 1200, "count", freshness="fresh"),
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
        today, "open_wearables", "Open Wearables", "steps", 9000, "count", freshness="aging"
    ))
    upsert_daily_facts(db_file, rows, profile_key="profile-1")

    facts = hub.recommendation_facts(db_file, "profile-1", limit=1)

    assert [fact["metric"] for fact in facts] == ["steps"]


def test_recommendation_facts_recomputes_age_for_orphaned_fresh_rows(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    upsert_daily_facts(db_file, [WearableDailyFact(
        "2020-01-01", "open_wearables", "Open Wearables", "recovery_score", 80,
        "score", freshness="fresh",
    )], profile_key="profile-1")

    assert hub.recommendation_facts(db_file, "profile-1") == []
