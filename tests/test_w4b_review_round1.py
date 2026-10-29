"""Review round 1 tests for PR #360 (W4b): source rule, conflicts, stale facts, outage, archive import.

Written from the request and contract before/while reading the diff. Synthetic data only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import urllib.error
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import open_wearables_hub as hub
from recommendation_sources import build_open_wearables_recommendation_source
from wearable_fact_store import WearableDailyFact, preferred_daily_facts, upsert_daily_facts

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import import_archive_facts as importer  # noqa: E402

TODAY = date.today().isoformat()


def _day(offset=0):
    return (date.today() - timedelta(days=offset)).isoformat()


def _fact(day, provider, metric, value, *, system="open_wearables", kind="summary", source_id="s", **extra):
    extra.setdefault("source_provider", provider)
    return WearableDailyFact(
        date=day, provider_id=provider, source_label=provider.title(), metric=metric, value=value,
        source_system=system, source_record_kind=kind, source_id=source_id,
        freshness="fresh", used_for_recommendation=True, **extra,
    )


@pytest.fixture
def app_db(isolated_app, monkeypatch):
    """Isolated app with no live OW, no weather, no workouts or soreness."""
    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", lambda **_: {"errors": {"config": "missing"}})
    monkeypatch.setattr(isolated_app, "_cached_wttr", lambda *_a, **_k: {"available": False})
    monkeypatch.setattr(isolated_app, "WORKOUTS", [])
    monkeypatch.setattr(isolated_app, "SORENESS_DATA", [])
    monkeypatch.setattr(isolated_app, "RECOVERY_DATA", [])
    return isolated_app


# 1. Conflicts must never increase demands: a disagreement keeps the conservative night in sleep debt.
def test_conflicting_night_stays_conservative_in_sleep_debt(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    facts = []
    for i in range(7):
        d = _day(i)
        facts += [
            _fact(d, "oura", "sleep_duration", 480),
            _fact(d, "oura", "sleep_deep_duration", 90),
            _fact(d, "oura", "sleep_rem_duration", 110),
            _fact(d, "oura", "sleep_light_duration", 280),
            _fact(d, "whoop", "sleep_duration", 300),
        ]
    upsert_daily_facts(db, facts)
    picked = preferred_daily_facts(db, {"sleep_duration"}, TODAY, TODAY, profile_key="1")[TODAY]["sleep_duration"]
    assert (picked["value"], picked["conflict_state"]) == (300, "conservative_value_used")

    debt = app_db.calculate_sleep_debt(days=7)
    # WHOOP alone (300 min/night) would give 7 short nights and 840 min of debt.
    assert debt["nights_under"] == 7, debt
    assert debt["debt_minutes"] == 7 * 120, debt


def test_conflict_control_one_provider_short_nights_downgrade(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(_day(i), "whoop", "sleep_duration", 300) for i in range(7)])
    debt = app_db.calculate_sleep_debt(days=7)
    assert (debt["nights_under"], debt["debt_minutes"]) == (7, 840)


def test_conflicting_night_is_not_reported_as_inconsistent_data(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "sleep_duration", 480),
        _fact(TODAY, "oura", "sleep_deep_duration", 90),
        _fact(TODAY, "oura", "sleep_rem_duration", 110),
        _fact(TODAY, "oura", "sleep_light_duration", 280),
        _fact(TODAY, "whoop", "sleep_duration", 300),
    ])
    body = app_db.app.test_client().get("/api/sleep/summary").get_json()
    assert body["data_quality"]["status"] != "inconsistent", body["data_quality"]


# 2. Plan W4b: readiness = sleep fallback on the preferred sleep fact + the W4a WHOOP guard.
def test_readiness_follows_plan_sleep_basis_not_whoop_score(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        _fact(TODAY, "whoop", "recovery_score", 90),
        _fact(TODAY, "oura", "sleep_duration", 300),
    ])
    readiness, basis = app_db._readiness_with_basis()
    assert (readiness, basis) == (62, "sleep_estimate"), (readiness, basis)


def test_whoop_green_recovery_does_not_turn_into_a_recovery_day(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        _fact(TODAY, "whoop", "recovery_score", 68),  # WHOOP green (67-100)
        _fact(TODAY, "oura", "sleep_duration", 450),  # 7.5 h: plan's sleep estimate = 70 -> moderate
    ])
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    assert body["recommendation"] == "moderate", (body["readiness"], body["recommendation"])


# 3. Source proof must name the provider that fed the plan, not the newest fact of any metric in the domain.
def test_recovery_proof_names_the_provider_behind_the_hrv_trend(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        *[_fact(_day(i), "oura", "heart_rate_variability", 50.0 - i) for i in range(1, 5)],
        _fact(_day(1), "oura", "recovery_score", 80),
        _fact(TODAY, "whoop", "resting_heart_rate", 52),
    ])
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    assert body["readiness"] is None  # a recovery score is no longer readiness (finding 2)
    proof = body["recommendation_sources"]["source_proof"]["recovery"]
    assert (proof["provider"], proof["fields_used"], proof["date"]) == ("oura", ["hrv_trend"], _day(1)), proof


def test_activity_proof_names_the_provider_the_guard_read(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        _fact(TODAY, "whoop", "active_minutes", 30),
        _fact(TODAY, "oura", "active_minutes", 95),  # the guard takes the highest across providers
    ])
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    proof = body["recommendation_sources"]["source_proof"]["activity"]
    assert (proof["provider"], proof["fields_used"]) == ("oura", ["active_minutes"]), proof


def test_sleep_proof_names_the_guard_provider_when_it_raised_caution(app_db):
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(TODAY, "oura", "sleep_duration", 380),
        _fact(TODAY, "whoop", "sleep_duration", 330),  # 50 min apart: no conflict, but the guard reads the minimum
    ])
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    assert "sleep_caution" in body["recommendation_sources"]["open_wearables"]["applied_modifiers"]
    proof = body["recommendation_sources"]["source_proof"]["sleep"]
    # Round 6: the readiness estimate and sleep debt stay Oura's; the WHOOP duration the guard read is credited to WHOOP.
    assert (proof["provider"], "sleep_duration" in proof["fields_used"]) == ("oura", False), proof
    assert (proof["guard_source"]["provider"], proof["guard_source"]["fields_used"]) == ("whoop", ["sleep_duration"]), proof


def test_recovery_proof_credits_the_whoop_score_the_guard_applied(app_db):
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        *[_fact(_day(i), "oura", "heart_rate_variability", 50.0 - i) for i in range(1, 5)],
        _fact(TODAY, "whoop", "recovery_score", 50),  # WHOOP yellow: caps RPE
    ])
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    proof = body["recommendation_sources"]["source_proof"]["recovery"]
    # Round 5: Oura's HRV trend stays Oura's; the WHOOP score the guard applied is credited to WHOOP separately.
    assert (proof["provider"], proof["fields_used"]) == ("oura", ["hrv_trend"]), proof
    assert (proof["guard_source"]["provider"], proof["guard_source"]["fields_used"]) == ("whoop", ["recovery_score"]), proof


def test_hrv_trend_keeps_one_providers_series(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    # Oura (preferred) has only 2 samples; Apple has 5 on other days. One provider's series is used, never a mix.
    upsert_daily_facts(db, [
        *[_fact(_day(i), "apple", "heart_rate_variability", 40.0) for i in (2, 3, 4, 5, 6)],
        *[_fact(_day(i), "oura", "heart_rate_variability", 80.0) for i in (0, 1)],
    ])
    series = app_db._hrv_facts(7)
    assert {f["provider"] for f in series} == {"apple"} and len(series) == 5


# 4. Stale facts: either excluded from the plan or labelled as used; never "unused" while they move the plan.
def test_stale_sleep_debt_is_not_reported_unused_while_it_downgrades(app_db):
    db = app_db.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [_fact(_day(i), "oura", "sleep_duration", 240) for i in (3, 4, 5, 6)])
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    debt = body["readiness_factors"]["sleep_debt"]["debt_minutes"]
    proof = body["recommendation_sources"]["source_proof"]["sleep"]
    fed_plan = debt > 300 and body["recommendation"] != "moderate"
    assert not (fed_plan and not proof["used_for_recommendation"]), (debt, body["recommendation"], proof)


def test_conflicting_night_names_the_provider_whose_value_is_used(app_db):
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(TODAY, "oura", "sleep_duration", 480),
        _fact(TODAY, "oura", "sleep_deep_duration", 90),
        _fact(TODAY, "oura", "sleep_rem_duration", 110),
        _fact(TODAY, "oura", "sleep_light_duration", 280),
        _fact(TODAY, "whoop", "sleep_duration", 300),
    ])
    row = app_db._sleep_rows_from_facts(days=1)[0]
    # The whole night is WHOOP's: its total, none of Oura's stages.
    assert (row["provider"], row["sleep_duration_min"], row["sleep_deep_min"]) == ("whoop", 300, None)
    proof = app_db.app.test_client().get("/api/recommendation/smart").get_json()["recommendation_sources"]["source_proof"]["sleep"]
    assert (proof["provider"], proof["conflict_state"], proof["date"]) == ("whoop", "conservative_value_used", TODAY), proof


def test_stale_hrv_series_is_not_fed_to_the_plan(app_db):
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(_day(i), "oura", "heart_rate_variability", v) for i, v in zip((6, 5, 4, 3), (60, 60, 40, 30))
    ])
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    proof = body["recommendation_sources"]["source_proof"]["recovery"]
    assert body["hrv_trend"] != "declining", body["hrv_trend"]  # a 3-day-old newest HRV is excluded, not trended
    assert (proof["used_for_recommendation"], proof["fields_used"]) == (False, []), proof


# 5. Realistic outage: OW configured but unreachable; stored facts and History stay; no 503.
def test_realistic_outage_keeps_stored_facts_and_views(isolated_app, monkeypatch):
    app = isolated_app
    db = app.WEARABLE_FACTS_DB_FILE
    upsert_daily_facts(db, [
        _fact(_day(1), "oura", "sleep_duration", 420, source_id="sleep-summary"),
        WearableDailyFact(
            date=_day(1), provider_id="whoop", source_label="WHOOP", metric="workout_duration", value=40.0,
            source_system="open_wearables", source_record_kind="event", source_id="w-1", source_provider="whoop",
            observed_at=f"{_day(1)}T12:00:00Z", original_label="Running", category="cardio",
        ),
    ], profile_key="1")
    with closing(sqlite3.connect(db)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM wearable_daily_facts").fetchone()[0]

    monkeypatch.setattr(app, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(app, "_get_ow_token", lambda: "synthetic-token")
    monkeypatch.setattr(app, "_open_wearables_user_base", lambda: "http://127.0.0.1:9/api/v1/users/u")
    calls = []

    def down(url, headers=None, **_k):
        calls.append(url)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(app, "_ow_request", down)
    monkeypatch.setattr(app, "_fetch_open_wearables_provider_statuses", lambda *a, **k: ([], "down"), raising=False)
    client = app.app.test_client()
    assert client.get("/api/wearables/today?refresh=true").status_code == 200
    assert calls, "refresh=true should have tried Open Wearables"
    with closing(sqlite3.connect(db)) as conn:
        after = conn.execute("SELECT COUNT(*) FROM wearable_daily_facts").fetchone()[0]
    assert after == before  # an outage deletes nothing

    calls.clear()
    for path in ("/api/open-wearables/workouts", "/api/sleep/summary", "/api/sleep/analytics"):
        resp = client.get(path)
        assert resp.status_code == 200, (path, resp.status_code)
    assert calls == []  # History and sleep views never fetch live
    rows = client.get("/api/open-wearables/workouts").get_json()["workouts"]
    assert len(rows) == 1 and rows[0]["source_label"] == "WHOOP"
    assert client.get("/api/sleep/summary").get_json()["last_night"]["total_sleep_min"] == 420


# 6. The 7-night 5 h gate through the real OW store path.
def test_seven_five_hour_nights_via_ow_store_give_seven_nights_under(app_db):
    days = [_day(i) for i in range(7)]
    hub.store_wearable_facts(
        {
            "fetched_at": f"{_day(-1)}T12:00:00Z",  # after today's end time whatever the clock says (future end = unknown)
            "sleep_summary": {"data": [
                {"date": d, "source": {"provider": "oura"}, "end_time": f"{d}T12:00:00Z", "duration_minutes": 300}
                for d in days
            ]},
        },
        db_file=app_db.WEARABLE_FACTS_DB_FILE, profile_key="1",
        activity_extractor=lambda _p: [], sleep_extractor=lambda _p: None,
    )
    debt = app_db.calculate_sleep_debt(days=7)
    assert debt["nights_under"] == 7
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    assert body["readiness_factors"]["sleep_debt"]["nights_under"] == 7


# 7. Carry-over 1 and 2: one shared 0-100 check; "none feed" wording.
@pytest.mark.parametrize("value", [True, 150, -1, "high"])
def test_invalid_whoop_recovery_is_never_counted_as_used(value):
    source = build_open_wearables_recommendation_source(
        {"status": "fresh"},
        facts=[{"metric": "recovery_score", "value": value, "provider_id": "whoop", "date": TODAY}],
        modifier={},
    )
    assert source["facts_used"] == 0
    assert source["used_for_recommendation"] is False
    assert "none feed today's recommendation" in source["detail"]
    assert hub.conservative_modifier([{"metric": "recovery_score", "value": value, "provider_id": "whoop", "date": TODAY}])["applied"] is False


def test_valid_whoop_recovery_counts_once():
    source = build_open_wearables_recommendation_source(
        {"status": "fresh"},
        facts=[
            {"metric": "recovery_score", "value": 50, "provider_id": "whoop", "date": TODAY},
            {"metric": "heart_rate_variability", "value": 50, "provider_id": "whoop", "date": TODAY},
        ],
        modifier={},
    )
    assert (source["facts_used"], source["used_for_recommendation"]) == (1, True)


# 8. Offset-less observed_at is UTC (boundary across midnight in a negative-offset zone).
def test_offset_less_observed_at_is_utc_near_midnight(monkeypatch):
    import time

    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    try:
        # 23:30 UTC observed, fetched 00:10 UTC next day: 40 min old -> fresh.
        assert hub._fact_freshness("2026-07-13T23:30:00", "2026-07-14T00:10:00Z") == "fresh"
        # Same reading with an explicit Z gives the same answer.
        assert hub._fact_freshness("2026-07-13T23:30:00Z", "2026-07-14T00:10:00Z") == "fresh"
    finally:
        monkeypatch.undo()
        time.tzset()


# 9. Combined source labels: one workout seen by two providers counts once.
def test_two_provider_workout_straddling_a_minute_counts_once(tmp_path):
    db_file = str(tmp_path / "facts.sqlite3")
    facts = []
    for provider, start in (("whoop", f"{_day(1)}T10:00:58Z"), ("apple", f"{_day(1)}T10:01:03Z")):
        facts.append(WearableDailyFact(
            date=_day(1), provider_id=provider, source_label=provider, source_provider=provider,
            source_system="open_wearables", source_record_kind="event", source_id=f"{provider}-1",
            observed_at=start, original_label="Running", category="cardio",
            metric="workout_duration", value=45.0,
        ))
    upsert_daily_facts(db_file, facts, profile_key="1")
    rows = hub.stored_workouts(db_file, "1")
    assert len(rows) == 1, [r["source_label"] for r in rows]


# 10. Archive import: read-only (WAL-mode, read-only file and folder), idempotent, never overwrites or deletes.
def _wal_archives(folder: Path):
    oura = folder / "oura_daily.sqlite3"
    with closing(sqlite3.connect(oura)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE oura_daily (day TEXT PRIMARY KEY, readiness_score INTEGER, sleep_score INTEGER, hrv REAL, "
            "steps INTEGER, activity_score INTEGER, active_calories INTEGER, resting_hr REAL, temperature_deviation REAL, "
            "sleep_type TEXT, sleep_duration_min INTEGER, sleep_deep_min INTEGER, sleep_rem_min INTEGER, "
            "sleep_light_min INTEGER, sleep_awake_min INTEGER, raw_json TEXT, created_at TEXT)"
        )
        for i, day in enumerate(("2026-02-01", "2026-02-02", "2026-02-03")):
            conn.execute(
                "INSERT INTO oura_daily (day, readiness_score, hrv, steps, resting_hr, sleep_type, sleep_duration_min) "
                "VALUES (?, 75, 50.0, 8000, 52.0, 'long_sleep', ?)",
                (day, 400 + i),
            )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    apple = folder / "apple_archive.db"
    with closing(sqlite3.connect(apple)) as conn:
        conn.execute(
            "CREATE TABLE ah_sync_log (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, record_type TEXT NOT NULL, "
            "record_date TEXT NOT NULL, record_key TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO ah_sync_log (source, record_type, record_date, record_key, data_json) VALUES (?, ?, ?, ?, ?)",
            ("health_auto_export", "workouts", "2025-05-01", "2025-05-01 07:00:00 -0500", json.dumps({
                "date": "2025-05-01", "startDate": "2025-05-01 07:00:00 -0500", "activity_type": "Outdoor Walk",
                "duration_minutes": 30, "total_energy_kcal": 120})),
        )
        conn.commit()
    return oura, apple


def test_archive_import_is_read_only_idempotent_and_ow_wins(isolated_app, tmp_path):
    folder = tmp_path / "archive"
    folder.mkdir()
    oura, apple = _wal_archives(folder)
    for path in (oura, apple):
        path.chmod(0o444)
    folder.chmod(0o555)  # SQLite cannot create -wal/-shm here
    db = isolated_app.WEARABLE_FACTS_DB_FILE
    # A live OW fact for the same provider/metric/date, plus an unrelated fact that must survive.
    upsert_daily_facts(db, [
        _fact("2026-02-02", "oura", "sleep_duration", 999, source_id="sleep-summary"),
        _fact("2026-03-01", "whoop", "steps", 1234, source_id="act"),
    ], profile_key="1")
    try:
        before_bytes = (oura.read_bytes(), apple.read_bytes())
        first = importer.import_archives(str(oura), str(apple), db, "1")
        with closing(sqlite3.connect(db)) as conn:
            rows1 = conn.execute(
                "SELECT date, provider_id, source_system, metric, source_id, value_json, imported_at "
                "FROM wearable_daily_facts ORDER BY 1, 2, 3, 4, 5"
            ).fetchall()
        second = importer.import_archives(str(oura), str(apple), db, "1")
        with closing(sqlite3.connect(db)) as conn:
            rows2 = conn.execute(
                "SELECT date, provider_id, source_system, metric, source_id, value_json, imported_at "
                "FROM wearable_daily_facts ORDER BY 1, 2, 3, 4, 5"
            ).fetchall()
        assert (oura.read_bytes(), apple.read_bytes()) == before_bytes
        assert sorted(os.listdir(folder)) == ["apple_archive.db", "oura_daily.sqlite3"]
    finally:
        folder.chmod(stat.S_IRWXU)
    assert first["written"] > 0 and first["skipped_live"] >= 1
    assert rows1 == rows2  # idempotent, imported_at kept
    assert second["skipped_live"] == first["skipped_live"]
    live = [r for r in rows2 if r[0] == "2026-02-02" and r[3] == "sleep_duration"]
    assert [(r[2], json.loads(r[5])) for r in live] == [("open_wearables", 999)]  # OW never overwritten, no archive twin
    assert any(r[0] == "2026-03-01" and r[3] == "steps" for r in rows2)  # nothing deleted
    picked = preferred_daily_facts(db, {"sleep_duration"}, "2026-02-01", "2026-02-03", profile_key="1")
    assert picked["2026-02-01"]["sleep_duration"]["via"] == "archive"
    assert picked["2026-02-02"]["sleep_duration"]["via"] == "open_wearables"

    workouts = isolated_app.app.test_client().get("/api/open-wearables/workouts").get_json()["workouts"]
    archive_rows = [w for w in workouts if w["date"] == "2025-05-01"]
    assert len(archive_rows) == 1
    assert archive_rows[0]["source_label"] == "Apple Health (archive)"


def test_later_ow_sync_wins_over_archive_and_keeps_the_archive_row(isolated_app, tmp_path):
    folder = tmp_path / "archive"
    folder.mkdir()
    oura, apple = _wal_archives(folder)
    db = isolated_app.WEARABLE_FACTS_DB_FILE
    importer.import_archives(str(oura), None, db, "1")
    hub.store_wearable_facts(
        {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sleep_summary": {"data": [
                {"date": "2026-02-01", "source": {"provider": "oura"}, "end_time": "2026-02-01T12:00:00Z", "duration_minutes": 455}
            ]},
        },
        db_file=db, profile_key="1",
        activity_extractor=lambda _p: [], sleep_extractor=lambda _p: None,
    )
    picked = preferred_daily_facts(db, {"sleep_duration"}, "2026-02-01", "2026-02-01", profile_key="1")
    assert (picked["2026-02-01"]["sleep_duration"]["via"], picked["2026-02-01"]["sleep_duration"]["value"]) == ("open_wearables", 455)
    with closing(sqlite3.connect(db)) as conn:
        kept = conn.execute(
            "SELECT COUNT(*) FROM wearable_daily_facts WHERE source_system='archive' AND date='2026-02-01' AND metric='sleep_duration'"
        ).fetchone()[0]
    assert kept == 1


# 11. Boot with no legacy DB files: nothing creates oura_daily/whoop files, stored-fact views work.
def test_boot_without_legacy_db_files(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    code = (
        "import app, os, json\n"
        "app.app.config.update(TESTING=True, LOGIN_DISABLED=True)\n"
        "app.fetch_open_wearables_data = lambda **_: {'errors': {'config': 'missing'}}\n"
        "c = app.app.test_client()\n"
        "codes = {p: c.get(p).status_code for p in ('/api/sleep/summary', '/api/wearables/today', '/api/wearables/trends', "
        "'/api/open-wearables/workouts', '/api/sleep/analytics', '/api/freshness')}\n"
        "print(json.dumps({'codes': codes, 'files': sorted(os.listdir(os.environ['DATA_DIR']))}))\n"
    )
    env = {**os.environ, "DATA_DIR": str(data_dir), "TMPDIR": str(tmp_path), "SECRET_KEY": "review-w4b"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert set(payload["codes"].values()) == {200}, payload
    assert not {"oura_daily.sqlite3", "whoop.sqlite3"} & set(payload["files"]), payload["files"]


# 12. End to end: does a sleep conflict end up less conservative than the conservative value alone?
def _seed_nights(db, *, oura):
    facts = [_fact(TODAY, "whoop", "recovery_score", 90)]
    for i in range(7):
        d = _day(i)
        facts.append(_fact(d, "whoop", "sleep_duration", 300))
        if oura:
            facts += [
                _fact(d, "oura", "sleep_duration", 480),
                _fact(d, "oura", "sleep_deep_duration", 90),
                _fact(d, "oura", "sleep_rem_duration", 110),
                _fact(d, "oura", "sleep_light_duration", 280),
            ]
    upsert_daily_facts(db, facts)


@pytest.mark.parametrize("oura", [True, False], ids=["conflict_A", "whoop_only_B"])
def test_conflict_recommendation_end_to_end(app_db, oura, request):
    _seed_nights(app_db.WEARABLE_FACTS_DB_FILE, oura=oura)
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    print(request.node.callspec.id, body["readiness"], body["readiness_factors"]["sleep_debt"]["debt_minutes"], body["recommendation"])
    # Case B (the conservative value alone) is the bar case A must not exceed.
    assert body["recommendation"] in {"recovery"}, (body["readiness"], body["readiness_factors"]["sleep_debt"], body["recommendation"])


# 13. imported_at, original_label and source_provider survive the workout window delete+insert refresh.
def test_workout_window_refresh_keeps_provenance(tmp_path):
    db_file = str(tmp_path / "facts.sqlite3")
    start = f"{_day(1)}T12:00:00Z"
    end = f"{_day(1)}T12:40:00Z"

    def sync(fetched_at, minutes):
        hub.store_wearable_facts(
            {
                "fetched_at": fetched_at,
                "_workout_snapshot_complete": True,
                "_workout_query": {"start_at": f"{_day(3)}T00:00:00+00:00", "end_at": f"{_day(-1)}T00:00:00+00:00"},
                "workouts": {"data": [{
                    "id": "w-1", "type": "running", "name": "Outdoor Run", "start_time": start, "end_time": end,
                    "duration_seconds": minutes * 60, "source": {"provider": "whoop"},
                }]},
            },
            db_file=db_file, profile_key="1",
            activity_extractor=lambda _p: [], sleep_extractor=lambda _p: None,
        )
        with closing(sqlite3.connect(db_file)) as conn:
            return conn.execute(
                "SELECT metric, value_json, imported_at, original_label, source_provider FROM wearable_daily_facts "
                "WHERE metric = 'workout_duration'"
            ).fetchall()

    first = sync(f"{_day(1)}T13:00:00+00:00", 40)
    second = sync(f"{_day(1)}T18:00:00+00:00", 41)
    assert len(first) == len(second) == 1
    assert json.loads(second[0][1]) == 41
    assert second[0][2:] == first[0][2:]
    assert second[0][3:] == ("Outdoor Run", "whoop")
