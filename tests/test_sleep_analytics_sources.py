import importlib
import sqlite3


def _fitness_app(monkeypatch, tmp_path, manual_rows):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    db_path = tmp_path / "oura.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE oura_sleep (
                day TEXT,
                type TEXT,
                bedtime_start TEXT,
                bedtime_end TEXT,
                total_sleep_min INTEGER,
                deep_sleep_min INTEGER,
                rem_sleep_min INTEGER,
                light_sleep_min INTEGER,
                awake_time_min INTEGER,
                sleep_score INTEGER,
                efficiency INTEGER,
                avg_heart_rate REAL,
                avg_hrv REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO oura_sleep VALUES (
                '2026-07-12', 'long_sleep',
                '2026-07-11T22:30:00', '2026-07-12T06:30:00',
                450, 80, 100, 270, 30, 88, 94, 52, 41
            )
            """
        )
    monkeypatch.setattr(module, "OURA_DB_FILE", str(db_path))
    monkeypatch.setattr(module, "SLEEP_DATA", manual_rows)
    monkeypatch.setattr(module, "WORKOUTS", [])
    return module.app


def test_sleep_analytics_prefers_oura_per_date_and_fills_manual_gaps(monkeypatch, tmp_path):
    app = _fitness_app(
        monkeypatch,
        tmp_path,
        [
            {
                "date": "2026-07-11",
                "sleep_start": "2026-07-10T23:00:00",
                "sleep_duration_min": 420,
            },
            {
                "date": "2026-07-12",
                "source": "apple_watch",
                "sleep_start": "2026-07-11T23:15:00",
                "sleep_duration_min": 390,
            },
            {
                "date": "not-a-date",
                "sleep_duration_min": 400,
            },
            {
                "date": "2026-7-1",
                "sleep_duration_min": 380,
            },
        ],
    )

    response = app.test_client().get("/api/sleep/analytics")

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
