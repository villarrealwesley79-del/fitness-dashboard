"""The owner's source rule: Oura first for sleep and recovery, WHOOP first for activity."""

from datetime import date, timedelta

import pytest

import open_wearables_hub as hub
from wearable_fact_store import (
    SOURCE_PREFERENCE,
    WearableDailyFact,
    fact_age_freshness,
    list_workout_events,
    preferred_daily_facts,
    upsert_daily_facts,
)

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
def db(tmp_path, isolated_app):
    isolated_app.WEARABLE_FACTS_DB_FILE = str(tmp_path / "rule.sqlite3")
    return isolated_app.WEARABLE_FACTS_DB_FILE


def _pick(db, metric, day=TODAY):
    return preferred_daily_facts(db, {metric}, day, day, profile_key="1")[day][metric]


def test_preference_is_plain_data_in_one_place():
    assert SOURCE_PREFERENCE == {
        "sleep": ["oura", "whoop", "apple"],
        "recovery": ["oura", "whoop", "apple"],
        "activity": ["whoop", "oura", "apple"],
    }


def test_oura_and_whoop_sleep_on_the_same_date_use_oura(db):
    upsert_daily_facts(db, [
        _fact(TODAY, "whoop", "sleep_duration", 400),
        _fact(TODAY, "oura", "sleep_duration", 430),
    ])
    picked = _pick(db, "sleep_duration")
    assert (picked["provider"], picked["value"], picked["via"]) == ("oura", 430, "open_wearables")
    assert picked["conflict_state"] is None


def test_whoop_only_sleep_names_whoop_as_the_provider(db):
    upsert_daily_facts(db, [_fact(TODAY, "open_wearables", "sleep_duration", 400, source_provider="whoop")])
    picked = _pick(db, "sleep_duration")
    assert picked["provider"] == "whoop"


def test_activity_prefers_whoop_over_oura(db):
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "active_minutes", 70),
        _fact(TODAY, "whoop", "active_minutes", 55),
    ])
    assert _pick(db, "active_minutes")["provider"] == "whoop"


def test_a_conflict_takes_the_more_cautious_value_and_says_so(db):
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "sleep_duration", 480),
        _fact(TODAY, "whoop", "sleep_duration", 330),
    ])
    picked = _pick(db, "sleep_duration")
    assert picked["value"] == 330
    assert picked["conflict_state"] == "conservative_value_used"


def test_live_fact_beats_archive_for_the_same_provider_and_date(db):
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "sleep_duration", 300, system="archive", source_id="archive:x"),
        _fact(TODAY, "oura", "sleep_duration", 420),
    ])
    picked = _pick(db, "sleep_duration")
    assert (picked["value"], picked["via"]) == (420, "open_wearables")
    # Only the archive holds the older night: it still shows, labelled as archive.
    old = _day(40)
    upsert_daily_facts(db, [_fact(old, "oura", "sleep_duration", 410, system="archive", source_id="archive:y")])
    assert _pick(db, "sleep_duration", old)["via"] == "archive"


def test_summary_beats_event_so_one_night_counts_once(db):
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "sleep_duration", 400, kind="event", source_id="event-1"),
        _fact(TODAY, "oura", "sleep_duration", 430, kind="summary", source_id="sleep-summary"),
    ])
    assert _pick(db, "sleep_duration")["value"] == 430


def test_booleans_and_non_numbers_are_never_picked(db):
    upsert_daily_facts(db, [_fact(TODAY, "oura", "sleep_duration", True)])
    assert preferred_daily_facts(db, {"sleep_duration"}, TODAY, TODAY, profile_key="1") == {}


def test_sleep_debt_counts_seven_short_nights(db, isolated_app):
    upsert_daily_facts(db, [_fact(_day(i), "oura", "sleep_duration", 300) for i in range(7)])
    debt = isolated_app.calculate_sleep_debt(days=7)
    assert debt["nights_under"] == 7
    assert debt["debt_minutes"] == 7 * 120


def test_hrv_trend_reads_one_hrv_metric_from_facts(db, isolated_app):
    values = [40, 40, 40, 50, 52, 54]
    upsert_daily_facts(db, [
        _fact(_day(5 - i), "oura", "heart_rate_variability", value) for i, value in enumerate(values)
    ])
    assert isolated_app.get_recent_hrv_trend() == "improving"


def test_recovery_scores_are_not_readiness(db, isolated_app):
    # WHOOP recovery reaches the plan once, through the W4a guard; readiness is the sleep estimate only.
    upsert_daily_facts(db, [
        _fact(TODAY, "whoop", "recovery_score", 40),
        _fact(TODAY, "oura", "recovery_score", 82),
    ])
    assert isolated_app._get_oura_readiness_today(include_open_wearables=False) is None


def test_readiness_without_a_score_is_estimated_from_sleep(db, isolated_app):
    upsert_daily_facts(db, [_fact(TODAY, "oura", "sleep_duration", 300)])
    assert isolated_app._get_oura_readiness_today(include_open_wearables=False) == 62  # 70 - 1.5 * 5


def test_stale_facts_give_no_readiness_and_lower_confidence(db, isolated_app):
    upsert_daily_facts(db, [
        _fact(_day(5), "oura", "recovery_score", 90),
        _fact(_day(5), "oura", "sleep_duration", 480),
    ])
    assert isolated_app._get_oura_readiness_today(include_open_wearables=False) is None
    assert fact_age_freshness(_day(5)) == "stale"
    stale = {"wearable_facts": {"status": "stale"}}
    assert isolated_app._confidence_level_from(95, stale) == "low"


def test_outage_serves_stored_facts_as_stale_with_no_503(db, isolated_app, monkeypatch):
    upsert_daily_facts(db, [_fact(_day(4), "oura", "sleep_duration", 420)])

    def down():
        raise ConnectionError("open wearables is down")

    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", down)
    client = isolated_app.app.test_client()
    today = client.get("/api/wearables/today?refresh=true")
    history = client.get("/api/open-wearables/workouts")
    assert today.status_code == 200 and history.status_code == 200
    assert today.get_json()["readiness"] is None  # four-day-old facts are stale, not hardened
    series = preferred_daily_facts(db, {"sleep_duration"}, _day(4), _day(4), profile_key="1")
    assert series[_day(4)]["sleep_duration"]["freshness"] == "stale"


def test_refresh_is_gated_to_once_per_fifteen_minutes(db, isolated_app, monkeypatch):
    calls = []
    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", lambda **_: calls.append(1) or {})
    monkeypatch.setattr(isolated_app, "_store_wearable_facts_from_open_wearables", lambda data: 0)
    assert isolated_app._refresh_wearable_facts() is True
    assert isolated_app._refresh_wearable_facts() is False
    assert isolated_app._refresh_wearable_facts(force=True) is True
    assert len(calls) == 2


def test_multi_day_sleep_summary_stores_one_dated_fact_per_night(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    days = [_day(i) for i in range(5)]
    hub.store_wearable_facts(
        {
            "fetched_at": f"{_day(-1)}T12:00:00Z",  # after today's end time whatever the clock says (future end = unknown)
            "sleep_summary": {"data": [
                {"date": d, "source": {"provider": "oura"}, "end_time": f"{d}T08:00:00Z", "duration_minutes": 400 + n}
                for n, d in enumerate(days)
            ]},
        },
        db_file=db_file, profile_key="1",
        activity_extractor=lambda _p: [], sleep_extractor=lambda _p: None,
    )
    series = preferred_daily_facts(db_file, {"sleep_duration"}, days[-1], days[0], profile_key="1")
    assert sorted(series) == sorted(days)
    assert {series[d]["sleep_duration"]["provider"] for d in days} == {"oura"}


def test_offset_less_observed_at_is_utc_not_host_local(monkeypatch):
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")  # UTC+14: a host-local reading would be 14 h off
    import time
    time.tzset()
    try:
        assert hub._fact_freshness("2026-07-14T08:00:00", "2026-07-14T10:00:00Z") == "fresh"
        assert hub._fact_freshness("2026-07-12T08:00:00", "2026-07-14T10:00:00Z") == "stale"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_first_imported_time_survives_a_window_refresh(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    day = _day(1)

    def sync(minutes, fetched_at):
        hub.store_wearable_facts(
            {
                "fetched_at": fetched_at,
                "_sleep_summary_snapshot_complete": True,
                "_sleep_summary_query": {"start_date": _day(3), "end_date": _day(-1)},
                "sleep_summary": {"data": [
                    {"date": day, "source": {"provider": "oura"}, "end_time": f"{day}T08:00:00Z", "duration_minutes": minutes}
                ]},
            },
            db_file=db_file, profile_key="1",
            activity_extractor=lambda _p: [], sleep_extractor=lambda _p: None,
        )

    sync(400, f"{day}T12:00:00+00:00")
    first = preferred_daily_facts(db_file, {"sleep_duration"}, day, day, profile_key="1")[day]["sleep_duration"]
    sync(405, f"{day}T18:00:00+00:00")  # complete window: delete + insert path
    second = preferred_daily_facts(db_file, {"sleep_duration"}, day, day, profile_key="1")[day]["sleep_duration"]
    assert second["value"] == 405
    assert second["imported_at"] == first["imported_at"]


def test_a_workout_from_two_providers_is_one_row_with_both_labels(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    start = f"{_day(1)}T10:00:00Z"
    facts = []
    for provider, calories in (("whoop", 300.0), ("apple", None)):
        base = dict(
            date=_day(1), provider_id=provider, source_label=provider, source_provider=provider,
            source_system="open_wearables", source_record_kind="event", source_id=f"{provider}-1",
            observed_at=start, original_label="Running", category="cardio",
        )
        facts.append(WearableDailyFact(metric="workout_duration", value=45.0, **base))
        if calories:
            facts.append(WearableDailyFact(metric="workout_active_calories", value=calories, **base))
    upsert_daily_facts(db_file, facts, profile_key="1")
    rows = hub.stored_workouts(db_file, "1")
    assert len(rows) == 1
    assert rows[0]["source_label"] == "WHOOP + Apple Health"
    assert rows[0]["calories_burned"] == 300.0
    assert len(list_workout_events(db_file, "1")) == 2


def test_backfill_walks_the_year_in_28_day_windows_oldest_first(isolated_app, monkeypatch):
    windows = []

    def fetch(days=7, now=None):
        windows.append((days, now))
        return {"window": len(windows)}

    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", fetch)
    monkeypatch.setattr(isolated_app, "_store_wearable_facts_from_open_wearables", lambda data: 2)

    assert isolated_app.backfill_open_wearables_facts(days=365) == 2 * 14
    assert len(windows) == 14
    assert {days for days, _ in windows} == {28}
    ends = [end for _, end in windows]
    assert ends == sorted(ends)  # oldest first: the newest window is stored last and sets the final source state
    assert (ends[-1] - ends[0]).days == 13 * 28


def test_only_the_live_window_updates_the_plan_marker(isolated_app, monkeypatch):
    monkeypatch.setattr(isolated_app, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(isolated_app, "_get_ow_token", lambda: "synthetic-token")
    monkeypatch.setattr(isolated_app, "_open_wearables_user_base", lambda: "https://ow.example.test/users/u")
    monkeypatch.setattr(isolated_app, "_ow_request", lambda url, headers=None, **_k: {"data": [], "pagination": {"has_more": False}})
    markers = []
    monkeypatch.setattr(isolated_app, "_store_open_wearables_recommendation_marker", lambda data: markers.append(data))

    isolated_app.fetch_open_wearables_data(days=28, now=isolated_app.datetime.now().astimezone())
    assert markers == []
    isolated_app.fetch_open_wearables_data()
    assert len(markers) == 1


def test_vitals_read_the_preferred_provider_and_never_count_naps(db, isolated_app, monkeypatch):
    upsert_daily_facts(db, [
        _fact(TODAY, "oura", "sleep_duration", 430),
        _fact(TODAY, "oura", "sleep_nap_duration", 40),
        _fact(TODAY, "whoop", "steps", 9100),
        _fact(TODAY, "oura", "steps", 8800),
        _fact(TODAY, "oura", "resting_heart_rate", 51),
    ])
    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", lambda **_: {"errors": {"config": "missing"}})

    payload = isolated_app.app.test_client().get("/api/vitals").get_json()

    assert payload["sleep"]["last_night"]["total_sleep_min"] == 430
    assert payload["activity"]["steps_today"] == 9100  # WHOOP first for activity
    assert payload["heart_rate"]["resting_bpm"] == 51
    assert payload["source"] == {"heart_rate": "oura", "activity": "whoop", "sleep": "oura"}
