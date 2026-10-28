from datetime import date

from wearable_fact_store import WearableDailyFact, upsert_daily_facts


def _night(module, day, provider, minutes, wake):
    upsert_daily_facts(module.WEARABLE_FACTS_DB_FILE, [
        WearableDailyFact(
            date=day, provider_id=provider, source_label=provider, metric="sleep_duration", value=float(minutes),
            source_provider=provider, source_system="open_wearables", source_id="sleep-summary", observed_at=wake,
        )
    ])


def test_sleep_analytics_prefers_oura_per_date_and_fills_manual_gaps(isolated_app, monkeypatch):
    manual = [
        {"date": "2026-07-11", "sleep_start": "2026-07-10T23:00:00", "sleep_duration_min": 420},
        {"date": "2026-07-12", "source": "apple_watch", "sleep_start": "2026-07-11T23:15:00", "sleep_duration_min": 390},
        {"date": "not-a-date", "sleep_duration_min": 400},
        {"date": "2026-7-1", "sleep_duration_min": 380},
    ]
    monkeypatch.setattr(isolated_app, "SLEEP_DATA", manual)
    monkeypatch.setattr(isolated_app, "WORKOUTS", [])
    _night(isolated_app, "2026-07-12", "whoop", 440, "2026-07-12T06:30:00Z")
    _night(isolated_app, "2026-07-12", "oura", 450, "2026-07-12T06:30:00Z")

    response = isolated_app.app.test_client().get("/api/sleep/analytics")

    assert response.status_code == 200
    history = response.get_json()["history"]
    assert [(row["date"], row["source"]) for row in history] == [
        ("2026-07-01", "manual"),
        ("2026-07-11", "manual"),
        ("2026-07-12", "oura"),
    ]
    assert history[0]["sleep_duration_min"] == 380
    assert history[1]["sleep_duration_min"] == 420
    assert history[2]["sleep_duration_min"] == 450
