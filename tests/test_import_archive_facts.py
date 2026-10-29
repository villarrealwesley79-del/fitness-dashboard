"""scripts/import_archive_facts.py: read-only on archives, idempotent, Open Wearables wins."""

import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import import_archive_facts as importer  # noqa: E402
import open_wearables_hub as hub  # noqa: E402
from wearable_fact_store import WearableDailyFact, preferred_daily_facts, upsert_daily_facts  # noqa: E402


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def archives(tmp_path):
    oura = tmp_path / "oura_archive.sqlite3"
    with closing(sqlite3.connect(oura)) as conn:
        conn.execute(
            "CREATE TABLE oura_daily (day TEXT PRIMARY KEY, readiness_score INTEGER, sleep_score INTEGER, hrv REAL, "
            "steps INTEGER, activity_score INTEGER, active_calories INTEGER, resting_hr REAL, temperature_deviation REAL, "
            "sleep_type TEXT, sleep_duration_min INTEGER, sleep_deep_min INTEGER, sleep_rem_min INTEGER, "
            "sleep_light_min INTEGER, sleep_awake_min INTEGER, raw_json TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE oura_sleep (day TEXT, type TEXT, bedtime_start TEXT, bedtime_end TEXT, total_sleep_min INTEGER, "
            "deep_sleep_min INTEGER, rem_sleep_min INTEGER, light_sleep_min INTEGER, awake_time_min INTEGER, "
            "sleep_score INTEGER, efficiency INTEGER, avg_heart_rate REAL, avg_hrv REAL, PRIMARY KEY (day, type))"
        )
        conn.execute(
            "INSERT INTO oura_daily (day, readiness_score, hrv, steps, resting_hr, sleep_type, sleep_duration_min) "
            "VALUES ('2026-01-05', 81, 55.0, 9000, 51.0, 'long_sleep', 400)"
        )
        conn.execute(
            "INSERT INTO oura_daily (day, readiness_score, hrv, sleep_type, sleep_duration_min) "
            "VALUES ('2026-01-06', 150, 50.0, 'long_sleep', 380)"  # out-of-range score is dropped
        )
        conn.execute(
            "INSERT INTO oura_sleep (day, type, bedtime_end, total_sleep_min, deep_sleep_min, efficiency) "
            "VALUES ('2026-01-05', 'long_sleep', '2026-01-05T07:00:00+00:00', 415, 80, 91)"
        )
        conn.commit()
    apple = tmp_path / "apple_archive.db"
    with closing(sqlite3.connect(apple)) as conn:
        conn.execute(
            "CREATE TABLE ah_sync_log (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, record_type TEXT NOT NULL, "
            "record_date TEXT NOT NULL, record_key TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL, created_at TEXT)"
        )
        rows = [
            ("sleep", "2025-03-01", "", {"date": "2025-03-01", "duration_minutes": 440, "core_minutes": 250, "in_bed_minutes": 470}),
            ("heart_rate", "2025-03-01", "", {"date": "2025-03-01", "value": 58, "type": "resting"}),
            ("heart_rate", "2025-03-01", "", {"date": "2025-03-01", "value": 72, "type": "avg"}),
            ("hrv", "2025-03-01", "", {"date": "2025-03-01", "value": 47}),
            ("steps", "2025-03-01", "", {"date": "2025-03-01", "value": 8123}),
            ("active_energy", "2025-03-01", "", {"date": "2025-03-01", "value": 540}),
            ("workouts", "2025-03-01", "2025-03-01T17:30:00Z", {
                "date": "2025-03-01", "startDate": "2025-03-01T17:30:00Z", "activity_type": "Functional Strength Training",
                "duration_minutes": 50, "total_energy_kcal": 310, "avg_heart_rate": 118}),
            ("mystery", "2025-03-01", "", {"date": "2025-03-01", "value": 1}),
        ]
        for kind, day, key, data in rows:
            conn.execute(
                "INSERT INTO ah_sync_log (source, record_type, record_date, record_key, data_json) VALUES (?, ?, ?, ?, ?)",
                ("health_auto_export", kind, day, key, json.dumps(data)),
            )
        conn.commit()
    return oura, apple


def test_import_maps_history_without_touching_the_archives(tmp_path, archives):
    oura, apple = archives
    before = (_digest(oura), _digest(apple))
    db = str(tmp_path / "facts.sqlite3")

    summary = importer.import_archives(str(oura), str(apple), db)

    assert (_digest(oura), _digest(apple)) == before
    assert summary["unmapped_types"] == {"heart_rate:avg": 1, "mystery": 1}
    oura_days = preferred_daily_facts(db, {"recovery_score", "sleep_duration", "heart_rate_variability", "steps"}, "2026-01-01", "2026-01-31", "1")
    assert oura_days["2026-01-05"]["recovery_score"]["value"] == 81
    assert oura_days["2026-01-05"]["sleep_duration"]["value"] == 415  # oura_sleep beats the daily row for a night
    assert "recovery_score" not in oura_days["2026-01-06"]
    assert oura_days["2026-01-06"]["sleep_duration"]["value"] == 380
    assert oura_days["2026-01-05"]["recovery_score"]["via"] == "archive"
    apple_day = preferred_daily_facts(db, {"sleep_duration", "resting_heart_rate", "steps", "active_calories"}, "2025-03-01", "2025-03-01", "1")["2025-03-01"]
    assert apple_day["sleep_duration"]["value"] == 440 and apple_day["sleep_duration"]["provider"] == "apple"
    assert apple_day["resting_heart_rate"]["value"] == 58 and apple_day["active_calories"]["value"] == 540

    workouts = hub.stored_workouts(db, "1")
    assert len(workouts) == 1
    assert workouts[0]["archive"] is True
    assert workouts[0]["source_label"] == "Apple Health (archive)"
    assert workouts[0]["activity_type"] == "Functional Strength Training"
    assert workouts[0]["calories_burned"] == 310


def test_import_is_idempotent_and_deletes_nothing(tmp_path, archives):
    oura, apple = archives
    db = str(tmp_path / "facts.sqlite3")
    keep = WearableDailyFact(
        date="2026-02-01", provider_id="whoop", source_label="WHOOP", metric="sleep_duration", value=401.0,
        source_provider="whoop", source_system="open_wearables", source_id="live-1", freshness="fresh",
    )
    upsert_daily_facts(db, [keep], profile_key="1")

    importer.import_archives(str(oura), str(apple), db)
    with closing(sqlite3.connect(db)) as conn:
        first = conn.execute("SELECT COUNT(*), MIN(imported_at) FROM wearable_daily_facts").fetchone()
    importer.import_archives(str(oura), str(apple), db)
    with closing(sqlite3.connect(db)) as conn:
        second = conn.execute("SELECT COUNT(*), MIN(imported_at) FROM wearable_daily_facts").fetchone()
        live = conn.execute("SELECT value_json FROM wearable_daily_facts WHERE source_id = 'live-1'").fetchone()

    assert first == second
    assert live == ("401.0",)


def test_open_wearables_wins_for_the_same_provider_metric_and_date(tmp_path, archives):
    oura, apple = archives
    db = str(tmp_path / "facts.sqlite3")
    live = [
        WearableDailyFact(
            date="2026-01-05", provider_id="oura", source_label="Oura", metric="sleep_duration", value=500.0,
            source_provider="oura", source_system="open_wearables", source_id="sleep-summary", freshness="fresh",
        ),
        WearableDailyFact(
            date="2025-03-01", provider_id="apple", source_label="Apple Health", metric="workout_duration", value=49.0,
            source_provider="apple", source_system="open_wearables", source_id="w-1", source_record_kind="event",
            observed_at="2025-03-01T17:30:00Z", original_label="Functional Strength Training", category="strength_training", freshness="fresh",
        ),
    ]
    upsert_daily_facts(db, live, profile_key="1")

    summary = importer.import_archives(str(oura), str(apple), db)

    assert summary["skipped_live"] >= 2
    day = preferred_daily_facts(db, {"sleep_duration", "recovery_score"}, "2026-01-05", "2026-01-05", "1")["2026-01-05"]
    assert day["sleep_duration"]["value"] == 500 and day["sleep_duration"]["via"] == "open_wearables"
    assert day["recovery_score"]["via"] == "archive"  # no live recovery score: the archive still fills it
    rows = hub.stored_workouts(db, "1")
    assert len(rows) == 1 and rows[0]["archive"] is False  # the archived copy of that workout day is not shown


def test_a_refresh_does_not_delete_archive_rows(tmp_path, archives):
    oura, _apple = archives
    db = str(tmp_path / "facts.sqlite3")
    importer.import_archives(str(oura), None, db)
    hub.store_wearable_facts(
        {
            "fetched_at": "2026-01-05T12:00:00+00:00",
            "_sleep_summary_snapshot_complete": True,
            "_sleep_summary_query": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
            "sleep_summary": {"data": []},
        },
        db_file=db, profile_key="1",
        activity_extractor=lambda _p: [], sleep_extractor=lambda _p: None,
    )
    day = preferred_daily_facts(db, {"sleep_duration"}, "2026-01-05", "2026-01-05", "1")["2026-01-05"]
    assert day["sleep_duration"]["via"] == "archive"


def test_a_wal_mode_read_only_archive_in_a_read_only_folder_imports_without_creating_files(tmp_path):
    import os

    folder = tmp_path / "archive"
    folder.mkdir()
    path = folder / "oura_archive.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE oura_daily (day TEXT PRIMARY KEY, readiness_score INTEGER, hrv REAL, steps INTEGER, "
            "resting_hr REAL, active_calories INTEGER, temperature_deviation REAL, sleep_type TEXT, "
            "sleep_duration_min INTEGER, sleep_deep_min INTEGER, sleep_rem_min INTEGER, sleep_light_min INTEGER, "
            "sleep_awake_min INTEGER)"
        )
        conn.execute("INSERT INTO oura_daily (day, readiness_score) VALUES ('2026-01-01', 80)")
        conn.commit()
    os.chmod(path, 0o444)
    os.chmod(folder, 0o555)
    before = sorted(os.listdir(folder))
    try:
        summary = importer.import_archives(str(path), None, str(tmp_path / "facts.sqlite3"))
        assert sorted(os.listdir(folder)) == before  # no -wal or -shm appeared beside the archive
    finally:
        os.chmod(folder, 0o755)
    assert summary["written"] == 1


def _apple_archive(tmp_path, workouts):
    apple = tmp_path / "apple_two.db"
    with closing(sqlite3.connect(apple)) as conn:
        conn.execute(
            "CREATE TABLE ah_sync_log (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, record_type TEXT NOT NULL, "
            "record_date TEXT NOT NULL, record_key TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL, created_at TEXT)"
        )
        for start, label in workouts:
            conn.execute(
                "INSERT INTO ah_sync_log (source, record_type, record_date, record_key, data_json) VALUES (?, ?, ?, ?, ?)",
                ("health_auto_export", "workouts", start[:10], start,
                 json.dumps({"startDate": start, "activity_type": label, "duration_minutes": 40, "total_energy_kcal": 300})),
            )
        conn.commit()
    return apple


def test_an_archived_morning_run_survives_a_live_evening_ride_on_the_same_day(tmp_path):
    """Fails before round 5: any live workout hid every archived workout of that provider and day."""
    apple = _apple_archive(tmp_path, [("2025-03-01T08:00:00Z", "Running"), ("2025-03-01T18:00:00Z", "Cycling")])
    db = str(tmp_path / "facts.sqlite3")
    upsert_daily_facts(db, [WearableDailyFact(
        date="2025-03-01", provider_id="apple", source_label="Apple Health", metric="workout_duration", value=41.0,
        source_provider="apple", source_system="open_wearables", source_id="w-ride", source_record_kind="event",
        observed_at="2025-03-01T18:02:00Z", original_label="Cycling", category=hub._workout_category("Cycling"), freshness="fresh")], profile_key="1")

    summary = importer.import_archives(None, str(apple), db)

    assert summary["skipped_live"] == 2  # the archived ride (2 metrics) is covered by the live ride
    rows = sorted(hub.stored_workouts(db, "1"), key=lambda r: r["start_time"])
    assert [(r["activity_type"], r["archive"]) for r in rows] == [("Running", True), ("Cycling", False)]


def test_stored_workouts_hides_an_archive_row_only_for_its_own_live_session(tmp_path):
    apple = _apple_archive(tmp_path, [("2025-03-01T08:00:00Z", "Running"), ("2025-03-01T18:00:00Z", "Cycling")])
    db = str(tmp_path / "facts.sqlite3")
    importer.import_archives(None, str(apple), db)  # both archived
    upsert_daily_facts(db, [WearableDailyFact(
        date="2025-03-01", provider_id="apple", source_label="Apple Health", metric="workout_duration", value=41.0,
        source_provider="apple", source_system="open_wearables", source_id="w-ride", source_record_kind="event",
        observed_at="2025-03-01T18:02:00Z", original_label="Cycling", category=hub._workout_category("Cycling"), freshness="fresh")], profile_key="1")
    rows = sorted(hub.stored_workouts(db, "1"), key=lambda r: r["start_time"])
    assert [(r["activity_type"], r["archive"]) for r in rows] == [("Running", True), ("Cycling", False)]


def test_archived_oura_night_keeps_its_measured_time_in_bed(tmp_path):
    """Fails before round 5: bedtime_start was dropped, so bedtime was rebuilt from sleep minutes."""
    oura = tmp_path / "oura_bed.sqlite3"
    with closing(sqlite3.connect(oura)) as conn:
        conn.execute(
            "CREATE TABLE oura_sleep (day TEXT, type TEXT, bedtime_start TEXT, bedtime_end TEXT, total_sleep_min INTEGER, "
            "deep_sleep_min INTEGER, rem_sleep_min INTEGER, light_sleep_min INTEGER, awake_time_min INTEGER, "
            "sleep_score INTEGER, efficiency INTEGER, avg_heart_rate REAL, avg_hrv REAL, PRIMARY KEY (day, type))"
        )
        conn.execute(
            "INSERT INTO oura_sleep (day, type, bedtime_start, bedtime_end, total_sleep_min) "
            "VALUES ('2026-01-05', 'long_sleep', '2026-01-04T22:00:00+00:00', '2026-01-05T06:00:00+00:00', 420)"
        )
        conn.execute(  # a start that does not parse adds no time in bed
            "INSERT INTO oura_sleep (day, type, bedtime_start, bedtime_end, total_sleep_min) "
            "VALUES ('2026-01-06', 'long_sleep', 'garbage', '2026-01-06T06:00:00+00:00', 400)"
        )
        conn.commit()
    db = str(tmp_path / "facts.sqlite3")
    importer.import_archives(str(oura), None, db)
    days = preferred_daily_facts(db, {"sleep_time_in_bed"}, "2026-01-05", "2026-01-06", "1")
    assert days["2026-01-05"]["sleep_time_in_bed"]["value"] == 480
    assert "2026-01-06" not in days


def test_a_zero_total_apple_sleep_row_derives_asleep_minutes_from_the_stages_never_from_in_bed(tmp_path):
    db = tmp_path / "apple_zero.db"
    rows = {  # the HAE shape: total 0.0 beside real stages
        "2026-07-08": {"duration_minutes": 0.0, "duration_minutes_source": "hae_asleep", "deep_minutes": 81, "rem_minutes": 112,
                       "core_minutes": 228, "awake_minutes": 21.5, "in_bed_minutes": 442.5},
        "2026-07-09": {"duration_minutes": 0.0, "in_bed_minutes": 400},  # no stages: nothing to derive
        "2026-07-10": {"duration_minutes": 410, "deep_minutes": 80, "rem_minutes": 100, "core_minutes": 200},  # a real total wins
    }
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE ah_sync_log (id INTEGER PRIMARY KEY AUTOINCREMENT, record_type TEXT, record_date TEXT, record_key TEXT, data_json TEXT)")
        for day, data in rows.items():
            conn.execute("INSERT INTO ah_sync_log (record_type, record_date, record_key, data_json) VALUES ('sleep', ?, '', ?)", (day, json.dumps(data)))
        conn.commit()

    facts, _ = importer.apple_facts(str(db), "2026-10-29T00:00:00Z")

    durations = {f.date: f for f in facts if f.metric == "sleep_duration"}
    assert durations["2026-07-08"].value == 421  # 81 + 112 + 228
    assert durations["2026-07-08"].source_system == "archive"
    assert "2026-07-09" not in durations
    assert durations["2026-07-10"].value == 410
