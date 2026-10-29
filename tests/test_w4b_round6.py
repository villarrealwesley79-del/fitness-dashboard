"""Round 6 (PR #360): a forced sleep night keeps the used provider's whole real night, every stored day
and provider survives a sync, the dashboard reads fresh facts, and the proof credits each field to its own
provider. Synthetic data only. The conflict and archive tests come from the round-5 review."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import import_archive_facts as importer  # noqa: E402
import open_wearables_hub as hub  # noqa: E402
from wearable_fact_store import WearableDailyFact, upsert_daily_facts  # noqa: E402

TODAY = date.today().isoformat()
_RANK = {"intensity": 0, "moderate": 1, "recovery": 2, "rest": 3}


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
    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", lambda **_: {"errors": {"config": "missing"}})
    monkeypatch.setattr(isolated_app, "_cached_wttr", lambda *_a, **_k: {"available": False})
    monkeypatch.setattr(isolated_app, "WORKOUTS", [])
    monkeypatch.setattr(isolated_app, "SORENESS_DATA", [])
    monkeypatch.setattr(isolated_app, "RECOVERY_DATA", [])
    monkeypatch.setattr(isolated_app, "_load_apple_health_recommendation_workouts", lambda **_k: [], raising=False)
    return isolated_app


def _store(app_db, payload):
    app_db._store_wearable_facts_from_open_wearables(payload)


def _rows(app_db, metric):
    with closing(sqlite3.connect(app_db.WEARABLE_FACTS_DB_FILE)) as conn:
        return conn.execute(
            "SELECT date, provider_id, source_id FROM wearable_daily_facts WHERE metric = ? ORDER BY date, provider_id",
            (metric,),
        ).fetchall()


# --- 1. A forced night keeps the used provider's whole real night (review round 5 BLOCKER) -----

def _recovery_row(day, provider, minutes, rid):
    # OW recovery summary: its sleep_duration_seconds becomes a duration-only sleep record.
    return {"id": rid, "date": day, "recorded_at": f"{day}T08:00:00Z", "source": {"provider": provider},
            "sleep_duration_seconds": minutes * 60}


def _sleep_summary_row(day, provider, minutes, hr):
    return {"date": day, "source": {"provider": provider}, "duration_minutes": minutes, "avg_heart_rate_bpm": hr,
            "end_time": f"{day}T12:00:00Z",
            "stages": {"deep_minutes": 90, "rem_minutes": 100, "light_minutes": minutes - 190, "awake_minutes": 20}}


def _whoop_only(day):
    return {"recovery_summary": [_recovery_row(day, "whoop", 450, "0a1b2c3d-whoop")],
            "sleep_summary": [_sleep_summary_row(day, "whoop", 450, 72)]}


def _whoop_and_oura(day):
    return {"recovery_summary": [_recovery_row(day, "whoop", 450, "0a1b2c3d-whoop"),
                                 _recovery_row(day, "oura", 520, "0f9e8d7c-oura")],
            "sleep_summary": [_sleep_summary_row(day, "whoop", 450, 72), _sleep_summary_row(day, "oura", 520, 72)]}


def test_conflict_night_keeps_the_used_providers_sleep_heart_rate_and_stages(app_db):
    """Oura 520 vs WHOOP 450 is a conflict, so WHOOP's night is used: its HR 72 and stages come with it."""
    _store(app_db, _whoop_and_oura(TODAY))
    row = app_db._sleep_rows_from_facts(days=1)[0]
    assert (row["provider"], row["sleep_duration_min"], row["conflict_state"]) == ("whoop", 450, "conservative_value_used"), row
    assert (row["avg_heart_rate"], row["deep_sleep_min"]) == (72, 90), row


def test_conflict_never_lifts_the_plan_above_the_used_provider_alone(app_db):
    client = app_db.app.test_client()
    _store(app_db, _whoop_only(TODAY))
    a = client.get("/api/recommendation/smart").get_json()
    _store(app_db, _whoop_and_oura(TODAY))
    b = client.get("/api/recommendation/smart").get_json()
    summary = lambda x: (x["recommendation"], x["readiness"])
    assert _RANK[b["recommendation"]] >= _RANK[a["recommendation"]], (summary(a), summary(b))
    assert b["readiness"] <= a["readiness"], (summary(a), summary(b))


def test_conflict_with_a_sleep_event_keeps_the_event_heart_rate(app_db):
    _store(app_db, {
        "recovery_summary": [_recovery_row(TODAY, "whoop", 450, "0a1b2c3d-whoop")],
        "sleep": {"events": [{"id": "whoop-ev-1", "end_time": f"{TODAY}T12:00:00Z", "duration_min": 450, "avg_hr": 78,
                              "source": {"provider": "whoop"}, "is_nap": False}]},
        "sleep_summary": [_sleep_summary_row(TODAY, "oura", 520, 60)],
    })
    row = app_db._sleep_rows_from_facts(days=1)[0]
    assert (row["provider"], row["sleep_duration_min"], row["avg_heart_rate"]) == ("whoop", 450, 78), row


def test_forced_night_still_drops_the_providers_own_fragment_record(app_db):
    """Round-4 property kept: WHOOP's 40-min record and its stages never join WHOOP's 300-min night."""
    day = _day(1)
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(day, "oura", "sleep_duration", 500, source_id="sleep-summary"),
        _fact(day, "whoop", "sleep_duration", 40, source_id="whoop-sum"),
        _fact(day, "whoop", "sleep_deep_duration", 10, source_id="whoop-sum"),
        _fact(day, "whoop", "sleep_duration", 300, kind="event", source_id="whoop-ev"),
        _fact(day, "whoop", "sleep_deep_duration", 60, kind="event", source_id="whoop-ev")])
    row = app_db._sleep_rows_from_facts(days=2)[0]
    assert (row["provider"], row["sleep_duration_min"], row["deep_sleep_min"]) == ("whoop", 300, 60), row


def test_a_fragment_never_lends_its_stages_on_a_night_without_a_conflict(app_db):
    """Oura 300-min summary without stages plus Oura's 40-min event with stages: the stages are not borrowed."""
    day = _day(1)
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(day, "oura", "sleep_duration", 300, source_id="sleep-summary"),
        _fact(day, "oura", "sleep_duration", 40, kind="event", source_id="frag"),
        _fact(day, "oura", "sleep_deep_duration", 10, kind="event", source_id="frag"),
        _fact(day, "oura", "sleep_rem_duration", 10, kind="event", source_id="frag"),
        _fact(day, "oura", "sleep_light_duration", 20, kind="event", source_id="frag")])
    row = app_db._sleep_rows_from_facts(days=2)[0]
    assert (row["sleep_duration_min"], row["deep_sleep_min"], row["rem_sleep_min"], row["light_sleep_min"]) == (300, None, None, None), row


# --- 2. Every returned day, provider and main-sleep event is stored ------------------------------

def test_every_activity_day_and_provider_is_stored(app_db):
    def row(day, provider, rid, minutes):
        return {"id": rid, "date": day, "steps": 8000, "active_minutes": minutes, "source": {"provider": provider}}

    _store(app_db, {"activity_summary": [
        row(_day(2), "whoop", "w2", 40), row(_day(1), "whoop", "w1", 50), row(_day(1), "oura", "o1", 60), row(TODAY, "whoop", "w0", 70),
    ]})
    assert [(d, p) for d, p, _ in _rows(app_db, "active_minutes")] == [
        (_day(2), "whoop"), (_day(1), "oura"), (_day(1), "whoop"), (TODAY, "whoop")]


def test_every_main_sleep_event_is_stored_and_naps_are_not(app_db):
    def event(day, rid, minutes, provider, nap=False):
        return {"id": rid, "end_time": f"{day}T12:00:00Z", "duration_min": minutes, "avg_hr": 60,
                "source": {"provider": provider}, "is_nap": nap}

    _store(app_db, {"sleep": {"events": [
        event(_day(2), "e2", 420, "whoop"), event(_day(1), "e1", 430, "oura"), event(TODAY, "e0", 440, "oura"),
        event(TODAY, "nap", 30, "oura", nap=True)]}})
    assert [(d, p, s) for d, p, s in _rows(app_db, "sleep_duration")] == [
        (_day(2), "whoop", "e2"), (_day(1), "oura", "e1"), (TODAY, "oura", "e0")]


def test_a_synced_whoop_activity_day_still_fires_the_activity_guard_beside_oura(app_db):
    """WHOOP 95 and Oura 30 active minutes arrive in one sync: both are stored, so the guard sees WHOOP's."""
    rows = [{"id": "w0", "date": TODAY, "active_minutes": 95, "source": {"provider": "whoop"}},
            {"id": "o0", "date": TODAY, "active_minutes": 30, "source": {"provider": "oura"}}]
    _store(app_db, {"activity_summary": rows})  # Oura last: payload order must not decide which row survives
    sources = app_db.app.test_client().get("/api/recommendation/smart").get_json()["recommendation_sources"]
    assert "activity_caution" in sources["open_wearables"]["applied_modifiers"], sources["open_wearables"]


# --- 3. The dashboard reads fresh facts; concurrent callers wait for the fetch in progress --------

def test_dashboard_refreshes_wearable_facts_before_reading_them(app_db, monkeypatch):
    order = []
    real_debt = app_db.calculate_sleep_debt
    monkeypatch.setattr(app_db, "fetch_open_wearables_data", lambda **_: order.append("fetch") or {"errors": {"config": "missing"}})
    monkeypatch.setattr(app_db, "calculate_sleep_debt", lambda *a, **k: order.append("sleep_debt") or real_debt(*a, **k))
    monkeypatch.setitem(app_db._WEARABLE_REFRESH_STATE, "at", None)
    assert app_db.app.test_client().get("/api/dashboard").status_code == 200
    assert order[:2] == ["fetch", "sleep_debt"], order


def test_concurrent_refreshes_share_one_fetch_and_wait_for_it(app_db, monkeypatch):
    state = {"calls": 0, "done": False}

    def slow_fetch(**_):
        state["calls"] += 1
        time.sleep(0.3)
        state["done"] = True
        return {"errors": {"config": "missing"}}

    monkeypatch.setattr(app_db, "fetch_open_wearables_data", slow_fetch)
    monkeypatch.setitem(app_db._WEARABLE_REFRESH_STATE, "at", None)
    seen = []
    first = threading.Thread(target=app_db._refresh_wearable_facts)
    first.start()
    time.sleep(0.1)  # the first fetch is in progress
    second = threading.Thread(target=lambda: (app_db._refresh_wearable_facts(), seen.append(state["done"])))
    second.start()
    first.join()
    second.join()
    assert state["calls"] == 1 and seen == [True], (state, seen)


# --- 4. Proof: each field is credited to its own provider, each domain has its own modifier flag --

def _hrv_declining():
    # newest lowest: 6 samples falling toward today
    return [_fact(_day(i), "oura", "sleep_hrv_rmssd", 30 + 2 * i, source_id=f"o{i}") for i in range(6)]


def test_declining_hrv_marks_recovery_not_sleep_as_modified(app_db):
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, _hrv_declining() + [_fact(TODAY, "oura", "sleep_duration", 450)])
    body = app_db.app.test_client().get("/api/recommendation/smart").get_json()
    proof = body["recommendation_sources"]["source_proof"]
    assert body["hrv_trend"] == "declining", body["hrv_trend"]
    assert (proof["recovery"]["modifier_applied"], proof["sleep"]["used_for_recommendation"], proof["sleep"]["modifier_applied"]) == (True, True, False), proof


def test_activity_guard_marks_only_activity_as_modified(app_db):
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(TODAY, "oura", "sleep_duration", 450), _fact(TODAY, "whoop", "active_minutes", 95)])
    sources = app_db.app.test_client().get("/api/recommendation/smart").get_json()["recommendation_sources"]
    proof = sources["source_proof"]
    assert "activity_caution" in sources["open_wearables"]["applied_modifiers"]
    assert (proof["activity"]["modifier_applied"], proof["sleep"]["modifier_applied"], proof["recovery"]["modifier_applied"]) == (True, False, False), proof


# --- 5. Archive workouts: hidden only by their own live copy, whatever each source calls the session --

def _apple_archive(tmp_path, workouts):
    apple = tmp_path / "apple_r6.db"
    with closing(sqlite3.connect(apple)) as conn:
        conn.execute(
            "CREATE TABLE ah_sync_log (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, record_type TEXT NOT NULL, "
            "record_date TEXT NOT NULL, record_key TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL, created_at TEXT)"
        )
        for start, label in workouts:
            conn.execute(
                "INSERT INTO ah_sync_log (source, record_type, record_date, record_key, data_json) VALUES (?, ?, ?, ?, ?)",
                ("health_auto_export", "workouts", start[:10], start,
                 json.dumps({"date": start[:10], "startDate": start, "activity_type": label,
                             "duration_minutes": 40, "total_energy_kcal": 300})),
            )
        conn.commit()
    return apple


def _live_apple_workout(day, observed_at, ow_type, name):
    return WearableDailyFact(
        date=day, provider_id="apple", source_label="Apple Health", metric="workout_duration", value=40.0,
        source_provider="apple", source_system="open_wearables", source_id=f"ow-{observed_at}", source_record_kind="event",
        observed_at=observed_at, original_label=name, category=hub._workout_category(ow_type))


_HAE_OW = [("Outdoor Run", "running"), ("Outdoor Walk", "walking"), ("High Intensity Interval Training", "hiit")]


@pytest.mark.parametrize("hae_label,ow_type", _HAE_OW)
def test_hae_archived_session_is_hidden_by_its_ow_copy(tmp_path, hae_label, ow_type):
    """HAE archived the session as 'Outdoor Run' in local time; OW stores type 'running' in UTC: one session."""
    apple = _apple_archive(tmp_path, [("2025-03-01 07:00:00 -0600", hae_label)])
    db = str(tmp_path / "facts.sqlite3")
    upsert_daily_facts(db, [_live_apple_workout("2025-03-01", "2025-03-01T13:00:00Z", ow_type, hae_label)], profile_key="1")
    summary = importer.import_archives(None, str(apple), db)
    rows = hub.stored_workouts(db, "1")
    assert [(r["source_label"], r["archive"]) for r in rows] == [("Apple Health", False)], (summary, rows)


@pytest.mark.parametrize("hae_label,ow_type", _HAE_OW)
def test_history_hides_an_already_imported_archive_copy_of_a_live_session(tmp_path, hae_label, ow_type):
    """Archive imported first, then OW stores the same session: one History row."""
    apple = _apple_archive(tmp_path, [("2025-03-01 07:00:00 -0600", hae_label)])
    db = str(tmp_path / "facts.sqlite3")
    importer.import_archives(None, str(apple), db)
    upsert_daily_facts(db, [_live_apple_workout("2025-03-01", "2025-03-01T13:00:00Z", ow_type, hae_label)], profile_key="1")
    rows = hub.stored_workouts(db, "1")
    assert [(r["source_label"], r["archive"]) for r in rows] == [("Apple Health", False)], rows


def test_distinct_archived_session_survives_next_to_a_live_one(tmp_path):
    apple = _apple_archive(tmp_path, [("2025-03-01 07:00:00 -0600", "Outdoor Walk")])
    db = str(tmp_path / "facts.sqlite3")
    upsert_daily_facts(db, [_live_apple_workout("2025-03-01", "2025-03-02T00:00:00Z", "cycling", "Indoor Cycling")], profile_key="1")
    importer.import_archives(None, str(apple), db)
    rows = sorted(hub.stored_workouts(db, "1"), key=lambda r: r["start_time"])
    assert [r["archive"] for r in rows] == [True, False], rows
