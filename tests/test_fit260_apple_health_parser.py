from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import pytest
from flask import Flask

import apple_health_parser as parser
import health_ingest as legacy_health


def _timeseries_payload(*, steps=0, energy=0, rhr=0, hrv=0):
    timestamp = 1783641600000
    return {
        "stepCount": [{"time_ms": timestamp, "value": steps}],
        "activeEnergyBurned": [{"time_ms": timestamp, "value": energy}],
        "restingHeartRate": [{"time_ms": timestamp, "value": rhr}],
        "heartRateVariabilitySDNN": [{"time_ms": timestamp, "value": hrv}],
    }


def test_metric_parsers_select_the_newest_export_that_contains_each_metric(monkeypatch, tmp_path):
    older = tmp_path / "healthkit_timeseries_multi_20260112T000000Z.json"
    newer = tmp_path / "healthkit_timeseries_multi_20260113T000000Z.json"
    older.write_text(json.dumps({
        "stepCount": [{"time_ms": 1783641600000, "value": 10}],
        "activeEnergyBurned": [{"time_ms": 1783641600000, "value": 20}],
    }))
    newer.write_text(json.dumps({
        "restingHeartRate": [{"time_ms": 1783641600000, "value": 30}],
        "heartRateVariabilitySDNN": [{"time_ms": 1783641600000, "value": 40}],
    }))
    same_mtime_ns = 1_700_000_000_000_000_000
    os.utime(older, ns=(same_mtime_ns, same_mtime_ns))
    os.utime(newer, ns=(same_mtime_ns, same_mtime_ns))
    monkeypatch.setattr(parser, "HEALTH_DIR", str(tmp_path))

    assert parser.parse_steps()[0]["avg"] == 10
    assert parser.parse_active_energy()[0]["avg"] == 20
    assert parser.parse_rhr()[0]["avg"] == 30
    assert parser.parse_hrv()[0]["avg"] == 40


def test_metric_parsers_fall_back_past_missing_and_malformed_newer_exports(monkeypatch, tmp_path):
    older = tmp_path / "healthkit_timeseries_multi_20260112T000000Z.json"
    vitals = tmp_path / "healthkit_timeseries_multi_20260113T000000Z.json"
    malformed = tmp_path / "healthkit_timeseries_multi_20260114T000000Z.json"
    older.write_text(json.dumps({
        "stepCount": [{"time_ms": 1783641600000, "value": 10}],
        "activeEnergyBurned": [{"time_ms": 1783641600000, "value": 20}],
    }))
    vitals.write_text(json.dumps({
        "restingHeartRate": [{"time_ms": 1783641600000, "value": 30}],
        "heartRateVariabilitySDNN": [{"time_ms": 1783641600000, "value": 40}],
    }))
    malformed.write_text("not-json")
    monkeypatch.setattr(parser, "HEALTH_DIR", str(tmp_path))

    assert parser.parse_steps()[0]["avg"] == 10
    assert parser.parse_active_energy()[0]["avg"] == 20
    assert parser.parse_rhr()[0]["avg"] == 30
    assert parser.parse_hrv()[0]["avg"] == 40


def test_metric_parsers_select_newer_steps_and_older_vitals_when_categories_are_inverted(monkeypatch, tmp_path):
    older = tmp_path / "healthkit_timeseries_multi_20260112T000000Z.json"
    newer = tmp_path / "healthkit_timeseries_multi_20260113T000000Z.json"
    older.write_text(json.dumps({
        "restingHeartRate": [{"time_ms": 1783641600000, "value": 30}],
        "heartRateVariabilitySDNN": [{"time_ms": 1783641600000, "value": 40}],
    }))
    newer.write_text(json.dumps({
        "stepCount": [{"time_ms": 1783641600000, "value": 10}],
        "activeEnergyBurned": [{"time_ms": 1783641600000, "value": 20}],
    }))
    monkeypatch.setattr(parser, "HEALTH_DIR", str(tmp_path))

    assert parser.parse_steps()[0]["avg"] == 10
    assert parser.parse_active_energy()[0]["avg"] == 20
    assert parser.parse_rhr()[0]["avg"] == 30
    assert parser.parse_hrv()[0]["avg"] == 40


def test_cumulative_metrics_sum_while_vitals_keep_daily_means(monkeypatch):
    monkeypatch.setattr(
        parser,
        "_load_json",
        lambda _pattern, **_kwargs: {
            "stepCount": [
                {"time_ms": 1783641600000, "value": 4000},
                {"time_ms": 1783645200000, "value": 5000},
            ],
            "activeEnergyBurned": [
                {"time_ms": 1783641600000, "value": 200},
                {"time_ms": 1783645200000, "value": 300},
            ],
            "restingHeartRate": [
                {"time_ms": 1783641600000, "value": 50},
                {"time_ms": 1783645200000, "value": 70},
            ],
            "heartRateVariabilitySDNN": [
                {"time_ms": 1783641600000, "value": 20},
                {"time_ms": 1783645200000, "value": 40},
            ],
        },
    )

    assert parser.parse_steps()[0]["avg"] == 9000
    assert parser.parse_active_energy()[0]["avg"] == 500
    assert parser.parse_rhr()[0]["avg"] == 60
    assert parser.parse_hrv()[0]["avg"] == 30
    assert parser.parse_timeseries("missing", "unused.json") == []


def test_invalid_timeseries_json_keeps_the_existing_empty_result(monkeypatch, tmp_path):
    export_path = tmp_path / "healthkit_timeseries_multi_20260113T000000Z.json"
    export_path.write_text("not-json")
    monkeypatch.setattr(parser, "HEALTH_DIR", str(tmp_path))

    assert parser.parse_steps() == []


def test_merge_workouts_keeps_distinct_starts_and_dedupes_equivalent_instants():
    file_workouts = [
        {
            "date": "2026-07-09",
            "activity": "Running",
            "start": "2026-07-09T11:00:00+00:00",
            "duration_min": 30,
        },
        {
            "date": "2026-07-09",
            "activity": "Running",
            "start": "2026-07-09T23:00:00+00:00",
            "duration_min": 45,
        },
    ]
    sync_workouts = [
        {
            "date": "2026-07-09",
            "activity": "Running",
            "startDate": "2026-07-09T06:00:00-05:00",
            "duration_minutes": 30,
        }
    ]

    merged = parser._merge_workouts(file_workouts, sync_workouts)

    assert len(merged) == 2
    assert {workout["duration_min"] for workout in merged} == {30, 45}


def test_merge_workouts_uses_activity_and_bounded_start_and_duration_comparison():
    workouts = [
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T11:00:00Z", "duration_min": 30},
        {"date": "2026-07-09", "activity": "Cycling", "start": "2026-07-09T11:00:00Z", "duration_min": 30},
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T11:00:30Z", "duration_min": 30.5},
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T11:00:45Z", "duration_min": 40},
    ]

    merged = parser._merge_workouts(workouts, [])

    assert len(merged) == 3
    assert {(workout["activity"], workout["duration_min"]) for workout in merged} == {
        ("Running", 30),
        ("Cycling", 30),
        ("Running", 40),
    }


@pytest.mark.parametrize("order", [(0, 2, 1), (2, 0, 1), (1, 2, 0)])
def test_merge_workouts_dedupes_three_way_chains_to_earliest_start(order):
    workouts = [
        {
            "date": "2026-07-09",
            "activity": "Running",
            "start": "2026-07-09T11:00:00Z",
            "duration_min": 30,
        },
        {
            "date": "2026-07-09",
            "activity": "Running",
            "start": "2026-07-09T11:04:00Z",
            "duration_min": 34,
        },
        {
            "date": "2026-07-09",
            "activity": "Running",
            "start": "2026-07-09T11:08:00Z",
            "duration_min": 38,
        },
    ]

    merged = parser._merge_workouts([workouts[index] for index in order], [])

    assert len(merged) == 1
    assert merged[0]["start"] == "2026-07-09T11:00:00Z"


def test_merge_workouts_missing_start_cannot_bridge_distinct_timed_workouts():
    workouts = [
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T08:00:00Z", "duration_min": 30},
        {"date": "2026-07-09", "activity": "Running", "duration_min": 34},
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T18:00:00Z", "duration_min": 38},
    ]

    merged = parser._merge_workouts(workouts, [])

    assert len(merged) == 2
    assert {workout["start"] for workout in merged} == {
        "2026-07-09T08:00:00Z",
        "2026-07-09T18:00:00Z",
    }


def test_merge_workouts_missing_start_matches_discarded_component_member():
    workouts = [
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T11:04:00Z", "duration_min": 35},
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T11:00:00Z", "duration_min": 30},
        {"date": "2026-07-09", "activity": "Running", "duration_min": 40},
    ]

    merged = parser._merge_workouts(workouts, [])

    assert len(merged) == 1
    assert merged[0]["start"] == "2026-07-09T11:00:00Z"


def test_merge_workouts_preserves_transitive_startless_component_matches():
    workouts = [
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T11:00:00Z", "duration_min": 30},
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T11:04:00Z", "duration_min": 35},
        {"date": "2026-07-09", "activity": "Running", "duration_min": 40},
        {"date": "2026-07-09", "activity": "Running", "duration_min": 45},
    ]

    merged = parser._merge_workouts(workouts, [])

    assert len(merged) == 1
    assert merged[0]["start"] == "2026-07-09T11:00:00Z"


@pytest.mark.parametrize(
    ("sync_start", "sync_duration"),
    [
        ("2026-07-09T11:02:00+00:00", 30),
        ("2026-07-09T11:00:00+00:00", 35),
        ("2026-07-09T11:05:00+00:00", 35),
    ],
)
def test_merge_workouts_dedupes_file_and_sync_records_within_five_minutes(sync_start, sync_duration):
    file_workout = {
        "date": "2026-07-09",
        "activity": "Running",
        "start": "2026-07-09T11:00:00Z",
        "duration_min": 30,
    }
    sync_workout = {
        "date": "2026-07-09",
        "activity": "Running",
        "startDate": sync_start,
        "duration_minutes": sync_duration,
    }

    assert len(parser._merge_workouts([file_workout], [sync_workout])) == 1


@pytest.mark.parametrize(
    ("sync_start", "sync_duration"),
    [
        ("2026-07-09T11:05:01+00:00", 30),
        ("2026-07-09T11:00:00+00:00", 35.01),
    ],
)
def test_merge_workouts_keeps_file_and_sync_records_more_than_five_minutes_apart(sync_start, sync_duration):
    file_workout = {
        "date": "2026-07-09",
        "activity": "Running",
        "start": "2026-07-09T11:00:00Z",
        "duration_min": 30,
    }
    sync_workout = {
        "date": "2026-07-09",
        "activity": "Running",
        "startDate": sync_start,
        "duration_minutes": sync_duration,
    }

    assert len(parser._merge_workouts([file_workout], [sync_workout])) == 2


@pytest.mark.parametrize(("sync_duration", "expected_total"), [(35, 1), (35.01, 2)])
def test_merge_workouts_falls_back_when_only_one_start_is_available(sync_duration, expected_total):
    file_workout = {
        "date": "2026-07-09",
        "activity": "Running",
        "start": "2026-07-09T11:00:00Z",
        "duration_min": 30,
    }
    sync_workout = {
        "date": "2026-07-09",
        "activity": "Running",
        "duration_minutes": sync_duration,
    }

    assert len(parser._merge_workouts([file_workout], [sync_workout])) == expected_total


@pytest.mark.parametrize(("sync_duration", "expected_total"), [(35, 1), (35.01, 2)])
def test_merge_workouts_uses_duration_tolerance_when_both_starts_are_missing(sync_duration, expected_total):
    file_workout = {"date": "2026-07-09", "activity": "Running", "duration_min": 30}
    sync_workout = {"date": "2026-07-09", "activity": "Running", "duration_minutes": sync_duration}

    assert len(parser._merge_workouts([file_workout], [sync_workout])) == expected_total


@pytest.mark.parametrize(
    "sync_workout",
    [
        {"date": "2026-07-09", "activity": "Cycling", "duration_minutes": 35},
        {"date": "2026-07-10", "activity": "Running", "duration_minutes": 35},
    ],
)
def test_merge_workouts_fallback_keeps_different_dates_and_activities(sync_workout):
    file_workout = {
        "date": "2026-07-09",
        "activity": "Running",
        "start": "2026-07-09T11:00:00Z",
        "duration_min": 30,
    }

    assert len(parser._merge_workouts([file_workout], [sync_workout])) == 2


def test_merge_workouts_uses_date_activity_duration_only_without_start_time():
    workouts = [
        {"date": "2026-07-09", "activity": "Running", "duration_min": 30},
        {"date": "2026-07-09", "activity": "Running", "duration_minutes": 30},
        {"date": "2026-07-09", "activity": "Running", "duration_min": 45},
    ]

    merged = parser._merge_workouts(workouts, [])

    assert len(merged) == 2


def test_canonical_workouts_dedupes_mixed_naive_and_aware_start_times_without_a_500(monkeypatch, tmp_path):
    sync_db = tmp_path / "apple-health-sync.db"
    monkeypatch.setattr(parser, "_apple_health_sync_db_path", lambda: str(sync_db))
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit260-test-token")
    monkeypatch.setattr(parser, "health_data_available", lambda: True)
    monkeypatch.setattr(
        parser,
        "parse_workouts",
        lambda: [{
            "date": "2026-07-09",
            "activity": "Running",
            "start": "2026-07-09T12:00:00",
            "duration_min": 5,
        }],
    )
    monkeypatch.setattr(
        parser,
        "_get_sync_records",
        lambda record_type, _days=0: [{
            "date": "2026-07-09",
            "activity": "Running",
            "startDate": "2026-07-09T12:00:00+00:00",
            "duration_minutes": 5,
        }] if record_type == "workouts" else [],
    )
    app = Flask(__name__)
    parser.register_apple_health_routes(app)

    response = app.test_client().get("/api/apple-health/workouts?days=0")

    assert response.status_code == 200
    assert response.get_json()["total"] == 1


@pytest.mark.parametrize(("sync_duration", "expected_total"), [(35, 1), (35.01, 2)])
def test_canonical_workouts_apply_fallback_when_sync_start_is_missing(monkeypatch, tmp_path, sync_duration, expected_total):
    sync_db = tmp_path / "apple-health-sync.db"
    monkeypatch.setattr(parser, "_apple_health_sync_db_path", lambda: str(sync_db))
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit260-test-token")
    monkeypatch.setattr(parser, "health_data_available", lambda: True)
    monkeypatch.setattr(
        parser,
        "parse_workouts",
        lambda: [{
            "date": "2026-07-09",
            "activity": "Running",
            "start": "2026-07-09T12:00:00Z",
            "duration_min": 30,
        }],
    )
    monkeypatch.setattr(
        parser,
        "_get_sync_records",
        lambda record_type, _days=0: [{
            "date": "2026-07-09",
            "activity": "Running",
            "duration_minutes": sync_duration,
        }] if record_type == "workouts" else [],
    )
    app = Flask(__name__)
    parser.register_apple_health_routes(app)

    response = app.test_client().get("/api/apple-health/workouts?days=0")

    assert response.status_code == 200
    assert response.get_json()["total"] == expected_total


def test_canonical_summary_matches_workouts_after_deduping_and_legacy_keeps_other(monkeypatch, tmp_path):
    sync_db = tmp_path / "apple-health-sync.db"
    file_workouts = [
        {"date": "2026-07-09", "activity": "Running", "start": "2026-07-09T12:00:00Z", "duration_min": 30},
        {"date": "2026-06-20", "activity": "Cycling", "start": "2026-06-20T12:00:00Z", "duration_min": 45},
        {"date": "2026-07-09", "activity": "Other", "start": "2026-07-09T15:00:00Z", "duration_min": 20},
    ]
    sync_duplicate = {
        "date": "2026-07-09",
        "activity": "Running",
        "startDate": "2026-07-09T12:03:00+00:00",
        "duration_minutes": 33,
    }
    sync_old = {
        "date": "2026-05-20",
        "activity": "Hiking",
        "startDate": "2026-05-20T12:00:00+00:00",
        "duration_minutes": 60,
    }
    monkeypatch.setattr(parser, "_apple_health_sync_db_path", lambda: str(sync_db))
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit260-test-token")
    monkeypatch.setattr(parser, "_local_date_cutoff", lambda days: {7: "2026-07-03", 30: "2026-06-10"}[days])
    monkeypatch.setattr(parser, "health_data_available", lambda: True)
    monkeypatch.setattr(parser, "parse_workouts", lambda: file_workouts)
    monkeypatch.setattr(parser, "parse_sleep", lambda: [])
    monkeypatch.setattr(parser, "parse_steps", lambda: [])
    monkeypatch.setattr(parser, "parse_rhr", lambda: [])
    monkeypatch.setattr(parser, "parse_hrv", lambda: [])
    monkeypatch.setattr(
        parser,
        "_get_sync_records",
        lambda record_type, days=0: (
            [sync_duplicate, sync_old] if days == 0 else [sync_duplicate]
        ) if record_type == "workouts" else [],
    )
    app = Flask(__name__)
    parser.register_apple_health_routes(app)
    legacy_health.register_health_routes(app)
    client = app.test_client()

    canonical_summary = client.get("/api/apple-health/summary").get_json()
    canonical_all = client.get("/api/apple-health/workouts?days=0").get_json()
    canonical_7d = client.get("/api/apple-health/workouts?days=7").get_json()
    canonical_30d = client.get("/api/apple-health/workouts?days=30").get_json()
    legacy_summary = client.get("/api/health/summary").get_json()
    legacy_workouts = client.get("/api/health/workouts?days=0").get_json()

    assert {key: canonical_summary[key] for key in ("workouts_total", "workouts_7d", "workouts_30d")} == {
        "workouts_total": 3,
        "workouts_7d": 1,
        "workouts_30d": 2,
    }
    assert canonical_all["total"] == 3
    assert canonical_7d["total"] == 1
    assert canonical_30d["total"] == 2
    assert legacy_summary["workouts_total"] == legacy_workouts["total"] == 3
    assert legacy_summary["workouts_7d"] == 2
    assert legacy_summary["workouts_30d"] == 3


def test_canonical_workouts_uses_summary_dedupe_before_local_date_filtering(monkeypatch, tmp_path):
    sync_db = tmp_path / "apple-health-sync.db"
    earlier_local_date = {
        "date": "2026-07-02",
        "activity": "Running",
        "startDate": "2026-07-02T23:30:00-05:00",
        "duration_minutes": 30,
    }
    cutoff_local_date = {
        "date": "2026-07-03",
        "activity": "Running",
        "startDate": "2026-07-03T04:30:00+00:00",
        "duration_minutes": 30,
    }
    monkeypatch.setattr(parser, "_apple_health_sync_db_path", lambda: str(sync_db))
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit260-test-token")
    monkeypatch.setattr(parser, "_local_date_cutoff", lambda days: {7: "2026-07-03", 30: "2026-06-10"}[days])
    monkeypatch.setattr(parser, "health_data_available", lambda: True)
    monkeypatch.setattr(parser, "parse_workouts", lambda: [])
    monkeypatch.setattr(parser, "parse_sleep", lambda: [])
    monkeypatch.setattr(parser, "parse_steps", lambda: [])
    monkeypatch.setattr(parser, "parse_rhr", lambda: [])
    monkeypatch.setattr(parser, "parse_hrv", lambda: [])
    monkeypatch.setattr(
        parser,
        "_get_sync_records",
        lambda record_type, days=0: (
            [earlier_local_date, cutoff_local_date] if days == 0 else [cutoff_local_date]
        ) if record_type == "workouts" else [],
    )
    app = Flask(__name__)
    parser.register_apple_health_routes(app)
    client = app.test_client()

    summary = client.get("/api/apple-health/summary").get_json()
    bounded = client.get("/api/apple-health/workouts?days=7").get_json()
    unbounded = client.get("/api/apple-health/workouts?days=0").get_json()

    assert summary["workouts_7d"] == bounded["total"] == 0
    assert unbounded["total"] == 1


def test_sync_workout_raw_duration_seconds_dedupe_with_file_duration_minutes():
    file_workout = {
        "date": "2026-07-09",
        "activity": "Running",
        "start": "2026-07-09T12:00:00Z",
        "duration_min": 5,
    }
    sync_workout = parser._normalize_sync_workout({
        "date": "2026-07-09",
        "activity": "Running",
        "startDate": "2026-07-09T12:00:00+00:00",
        "duration": 300,
    })

    assert sync_workout["duration_min"] == 5
    assert len(parser._merge_workouts([file_workout], [sync_workout])) == 1


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires runtime timezone control")
def test_local_date_cutoffs_align_summary_sync_records_and_legacy_steps(monkeypatch, tmp_path):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            frozen = cls(2026, 7, 10, 0, 30, tzinfo=timezone.utc)
            return frozen if tz is not None else frozen.replace(tzinfo=None)

    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    try:
        sync_db = tmp_path / "apple-health-sync.db"
        with sqlite3.connect(sync_db) as connection:
            connection.execute(
                "CREATE TABLE ah_sync_log (record_type TEXT, record_date TEXT, data_json TEXT)"
            )
            connection.execute(
                "INSERT INTO ah_sync_log VALUES (?, ?, ?)",
                ("steps", "2026-07-08", json.dumps({"date": "2026-07-08", "value": 4000})),
            )
        monkeypatch.setattr(parser, "datetime", FrozenDateTime)
        monkeypatch.setattr(legacy_health, "datetime", FrozenDateTime)
        monkeypatch.setattr(parser, "_apple_health_sync_db_path", lambda: str(sync_db))

        assert parser._get_sync_records("steps", days=1) == [
            {"date": "2026-07-08", "value": 4000}
        ]

        steps = [
            {"date": "2026-07-02", "avg": 9000},
            {"date": "2026-07-08", "avg": 0},
        ]
        monkeypatch.setattr(parser, "parse_workouts", lambda: [])
        monkeypatch.setattr(parser, "parse_sleep", lambda: [])
        monkeypatch.setattr(parser, "parse_steps", lambda: steps)
        monkeypatch.setattr(parser, "parse_rhr", lambda: [])
        monkeypatch.setattr(parser, "parse_hrv", lambda: [])
        monkeypatch.setattr(parser, "health_data_available", lambda: True)
        monkeypatch.setattr(
            parser,
            "_get_sync_records",
            lambda record_type, _days=0: [{
                "date": "2026-07-02",
                "activity": "Running",
                "duration_minutes": 30,
            }] if record_type == "workouts" else [],
        )
        monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit260-test-token")
        app = Flask(__name__)
        parser.register_apple_health_routes(app)
        legacy_health.register_health_routes(app)
        client = app.test_client()

        assert parser.get_summary()["avg_steps_7d"] == 4500
        canonical_summary = client.get("/api/apple-health/summary")
        canonical = client.get("/api/apple-health/steps?days=1")
        legacy = client.get("/api/health/steps?days=1")
        assert canonical_summary.get_json()["workouts_7d"] == 1
        assert canonical.get_json() == {"steps": [{"date": "2026-07-08", "avg": 0}], "total": 1}
        assert legacy.get_json() == canonical.get_json()
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires runtime timezone control")
def test_epoch_file_timestamps_use_runtime_local_date(monkeypatch):
    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    try:
        timestamp_ms = datetime(2026, 7, 10, 0, 30, tzinfo=timezone.utc).timestamp() * 1000
        assert parser._ms_to_iso(timestamp_ms) == "2026-07-09"
        assert parser._local_date_from_iso("2026-07-09T19:30:00-05:00") == "2026-07-09"
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()


@pytest.mark.parametrize("failure_point", ["execute", "fetch"])
def test_get_sync_records_closes_connection_after_query_failures(monkeypatch, failure_point):
    class FailingConnection:
        closed = False

        def execute(self, *_args):
            if failure_point == "execute":
                raise RuntimeError("execute failed")
            return self

        def fetchall(self):
            raise RuntimeError("fetch failed")

        def close(self):
            self.closed = True

    connection = FailingConnection()
    monkeypatch.setattr(parser.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(parser.sqlite3, "connect", lambda _path: connection)
    monkeypatch.setattr(parser, "_apple_health_sync_db_path", lambda: "/tmp/fit260-sync.db")

    assert parser._get_sync_records("steps") == []
    assert connection.closed is True


def test_get_sync_records_closes_connection_after_success(monkeypatch):
    class SuccessfulConnection:
        closed = False

        def execute(self, *_args):
            return self

        def fetchall(self):
            return [(' {"date": "2026-07-09", "value": 9000} ',)]

        def close(self):
            self.closed = True

    connection = SuccessfulConnection()
    monkeypatch.setattr(parser.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(parser.sqlite3, "connect", lambda _path: connection)
    monkeypatch.setattr(parser, "_apple_health_sync_db_path", lambda: "/tmp/fit260-sync.db")

    assert parser._get_sync_records("steps") == [{"date": "2026-07-09", "value": 9000}]
    assert connection.closed is True


def test_json_cache_invalidates_by_mtime_and_returns_isolated_values(monkeypatch, tmp_path):
    export_path = tmp_path / "healthkit_timeseries_multi_20260113T000000Z.json"
    export_path.write_text(json.dumps(_timeseries_payload(steps=100)))
    monkeypatch.setattr(parser, "HEALTH_DIR", str(tmp_path))

    open_calls = []
    original_open = open

    def tracked_open(*args, **kwargs):
        open_calls.append(args[0])
        return original_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", tracked_open)
    first = parser._load_json("healthkit_timeseries_multi_*.json")
    first["stepCount"][0]["value"] = 999
    second = parser._load_json("healthkit_timeseries_multi_*.json")

    assert second["stepCount"][0]["value"] == 100
    assert len(open_calls) == 1

    export_path.write_text(json.dumps(_timeseries_payload(steps=200)))
    current_mtime_ns = export_path.stat().st_mtime_ns
    os.utime(export_path, ns=(current_mtime_ns + 1, current_mtime_ns + 1))
    refreshed = parser._load_json("healthkit_timeseries_multi_*.json")

    assert refreshed["stepCount"][0]["value"] == 200
    assert len(open_calls) == 2


def test_canonical_and_legacy_routes_classify_curling_and_pilates(monkeypatch, tmp_path):
    export_path = tmp_path / "healthkit_samples_workout_20260113T000000Z.json"
    export_path.write_text(json.dumps({"workout": [
        {"activity": "HKWorkoutActivityType(rawValue: 32)", "start_time_ms": 1783641600000, "duration_sec": 1800},
        {"activity": "HKWorkoutActivityType(rawValue: 36)", "start_time_ms": 1783645200000, "duration_sec": 1800},
    ]}))
    monkeypatch.setattr(parser, "HEALTH_DIR", str(tmp_path))

    assert [workout["activity"] for workout in parser.parse_workouts()] == ["Curling", "Pilates"]
    app = Flask(__name__)
    legacy_health.register_health_routes(app)
    response = app.test_client().get("/api/health/workouts?days=0")
    assert [workout["activity"] for workout in response.get_json()["workouts"]] == ["Curling", "Pilates"]


def test_legacy_health_routes_delegate_to_canonical_parser_and_keep_empty_contract(monkeypatch):
    app = Flask(__name__)
    legacy_health.register_health_routes(app)
    client = app.test_client()

    monkeypatch.setattr(parser, "parse_workouts", lambda: [])
    monkeypatch.setattr(parser, "parse_sleep", lambda: [])
    monkeypatch.setattr(parser, "parse_steps", lambda: [])
    monkeypatch.setattr(parser, "parse_rhr", lambda: [])
    monkeypatch.setattr(parser, "parse_hrv", lambda: [])
    monkeypatch.setattr(
        parser,
        "get_summary",
        lambda: {
            "workouts_total": 0,
            "workouts_7d": 0,
            "workouts_30d": 0,
            "avg_sleep_7d": 0,
            "avg_steps_7d": 0,
            "latest_rhr": None,
            "latest_hrv": None,
            "sleep_total_days": 0,
            "steps_total_days": 0,
            "data_source": "canonical",
            "last_export": "not found",
        },
    )

    assert client.get("/api/health/summary").status_code == 200
    assert client.get("/api/health/summary").get_json() == {
        "workouts_total": 0,
        "workouts_7d": 0,
        "workouts_30d": 0,
        "avg_sleep_7d": 0,
        "avg_steps_7d": 0,
        "latest_rhr": None,
        "latest_hrv": None,
        "sleep_total_days": 0,
        "steps_total_days": 0,
    }
    assert client.get("/api/health/sleep").get_json() == {"sleep": [], "total": 0}
    assert client.get("/api/health/steps").get_json() == {"steps": [], "total": 0}
    assert client.get("/api/health/vitals").get_json() == {"rhr": [], "hrv": []}
    assert client.get("/api/health/workouts").get_json() == {"workouts": [], "total": 0}

    today = datetime.now().strftime("%Y-%m-%d")
    monkeypatch.setattr(parser, "parse_steps", lambda: [{"date": today, "avg": 9000}])
    response = client.get("/api/health/steps")
    assert response.status_code == 200
    assert response.get_json() == {"steps": [{"date": today, "avg": 9000}], "total": 1}


def test_legacy_summary_counts_other_workouts_the_same_as_legacy_workouts_route(monkeypatch):
    app = Flask(__name__)
    legacy_health.register_health_routes(app)
    client = app.test_client()
    today = datetime.now().strftime("%Y-%m-%d")
    workouts = [
        {"date": today, "activity": "Running", "duration_min": 30},
        {"date": today, "activity": "Other", "duration_min": 20},
    ]
    monkeypatch.setattr(legacy_health, "parse_workouts", lambda: workouts)
    monkeypatch.setattr(legacy_health, "parse_sleep", lambda: [])
    monkeypatch.setattr(legacy_health, "parse_steps", lambda: [])
    monkeypatch.setattr(legacy_health, "parse_rhr", lambda: [])
    monkeypatch.setattr(legacy_health, "parse_hrv", lambda: [])
    monkeypatch.setattr(
        parser,
        "get_summary",
        lambda: {
            "workouts_total": 1,
            "workouts_7d": 1,
            "workouts_30d": 1,
            "avg_sleep_7d": 0,
            "avg_steps_7d": 0,
            "latest_rhr": None,
            "latest_hrv": None,
            "sleep_total_days": 0,
            "steps_total_days": 0,
        },
    )

    assert client.get("/api/health/workouts?days=0").get_json()["total"] == 2
    summary = client.get("/api/health/summary").get_json()
    assert summary["workouts_total"] == 2
    assert summary["workouts_7d"] == 2
    assert summary["workouts_30d"] == 2
