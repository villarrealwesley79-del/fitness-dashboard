"""Round 7 (PR #360, closed list): a hung Open Wearables never holds the dashboard past the client timeout,
a stale night is not "last night", a night without time in bed has no estimated bedtime, and the HRV series
skips a night's sub-hour fragment. Synthetic data only."""

from __future__ import annotations

import socket
import threading
import time
from datetime import date, timedelta

import pytest

from wearable_fact_store import WearableDailyFact, upsert_daily_facts

TODAY = date.today().isoformat()


def _day(offset=0):
    return (date.today() - timedelta(days=offset)).isoformat()


def _fact(day, provider, metric, value, *, source_id="s", **extra):
    return WearableDailyFact(
        date=day, provider_id=provider, source_label=provider.title(), metric=metric, value=value,
        source_system="open_wearables", source_record_kind="summary", source_id=source_id,
        source_provider=provider, freshness="fresh", used_for_recommendation=True, **extra,
    )


@pytest.fixture
def app_db(isolated_app, monkeypatch):
    monkeypatch.setattr(isolated_app, "fetch_open_wearables_data", lambda **_: {"errors": {"config": "missing"}})
    monkeypatch.setattr(isolated_app, "_cached_wttr", lambda *_a, **_k: {"available": False})
    monkeypatch.setattr(isolated_app, "WORKOUTS", [])
    monkeypatch.setattr(isolated_app, "SORENESS_DATA", [])
    monkeypatch.setattr(isolated_app, "RECOVERY_DATA", [])
    monkeypatch.setitem(isolated_app._WEARABLE_REFRESH_STATE, "at", None)
    return isolated_app


@pytest.fixture
def hung_ow(isolated_app, monkeypatch):
    """A loopback server that accepts connections and never answers, configured as OW with a valid token."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(64)
    held = []
    stop = threading.Event()

    def accept():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(server.accept()[0])
            except OSError:
                continue

    threading.Thread(target=accept, daemon=True).start()
    monkeypatch.setattr(isolated_app, "OPEN_WEARABLES_SERVICE_BASE", f"http://127.0.0.1:{server.getsockname()[1]}")
    monkeypatch.setattr(isolated_app, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(isolated_app, "_open_wearables_user_id", lambda *_a, **_k: "user-1")
    monkeypatch.setattr(isolated_app, "_get_ow_token", lambda: "synthetic-token")
    yield isolated_app
    stop.set()
    for conn in held:
        conn.close()
    server.close()


def test_hung_ow_steady_state_dashboard_answers_inside_the_client_timeout(hung_ow, monkeypatch):
    """Fails before the fix (3 requests queue behind a lock held for 6 x 6 s per fetch): app.js aborts after 30 s."""
    for name, value in (("WORKOUTS", []), ("SORENESS_DATA", []), ("RECOVERY_DATA", [])):
        monkeypatch.setattr(hung_ow, name, value)
    monkeypatch.setattr(hung_ow, "_cached_wttr", lambda *_a, **_k: {"available": False})
    upsert_daily_facts(hung_ow.WEARABLE_FACTS_DB_FILE, [_fact(TODAY, "oura", "sleep_duration", 420)])
    real_fetch = hung_ow.fetch_open_wearables_data
    monkeypatch.setattr(hung_ow, "fetch_open_wearables_data", lambda **_: {"errors": {"config": "missing"}})
    assert hung_ow.app.test_client().get("/api/dashboard").status_code == 200  # stores today's plan
    monkeypatch.setattr(hung_ow, "fetch_open_wearables_data", real_fetch)
    hung_ow._WEARABLE_REFRESH_STATE["at"] = None
    latencies = {}

    def hit(path):
        started = time.monotonic()
        status = hung_ow.app.test_client().get(path).status_code
        latencies[path] = (round(time.monotonic() - started, 1), status)

    threads = [threading.Thread(target=hit, args=(p,)) for p in ("/api/dashboard", "/api/recommendation/smart", "/api/wearables/today")]
    for t in threads:
        t.start()
        time.sleep(0.05)
    for t in threads:
        t.join()
    assert latencies["/api/dashboard"][0] < 30, latencies


def test_stale_newest_night_is_not_last_night(app_db):
    """Fails before the fix: a 5-day-old night was returned as last_night."""
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [_fact(_day(5), "oura", "sleep_duration", 420)])
    body = app_db.app.test_client().get("/api/sleep/summary").get_json()
    assert body["last_night"] is None, body["last_night"]


def test_fresh_newest_night_is_still_last_night(app_db):
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [_fact(TODAY, "oura", "sleep_duration", 420)])
    assert app_db.app.test_client().get("/api/sleep/summary").get_json()["last_night"]["total_sleep_min"] == 420


def test_no_time_in_bed_means_no_bedtime_and_both_consumers_skip_the_night(app_db):
    """Fails before the fix: the bedtime was estimated from time asleep."""
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, [
        _fact(_day(i), "oura", "sleep_duration", 420, source_id=f"n{i}", observed_at=f"{_day(i)}T07:00:00+00:00")
        for i in range(5)
    ])
    rows = app_db._sleep_rows_from_facts(days=7)
    assert rows and all(r["bedtime_start"] is None for r in rows), rows
    client = app_db.app.test_client()
    assert client.get("/api/sleep/analytics").get_json()["consistency_score"] is None
    assert client.get("/api/sleep/summary").get_json()["consistency"]["status"] == "unknown"


def test_a_night_fragments_hrv_never_enters_the_hrv_series(app_db):
    """Fails before the fix: the sub-hour record's HRV (20) beat the real night's (60) on every day."""
    facts = []
    for i in range(5):
        day = _day(i)
        facts += [
            _fact(day, "oura", "sleep_duration", 420, source_id="b-night"),
            _fact(day, "oura", "sleep_hrv_rmssd", 60, source_id="b-night"),
            _fact(day, "oura", "sleep_duration", 30, source_id="a-frag"),
            _fact(day, "oura", "sleep_hrv_rmssd", 20, source_id="a-frag"),
        ]
    upsert_daily_facts(app_db.WEARABLE_FACTS_DB_FILE, facts)
    assert set(app_db._hrv_series(7)) == {60.0}, app_db._hrv_series(7)
