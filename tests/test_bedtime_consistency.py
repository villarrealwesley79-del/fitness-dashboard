import importlib

import pytest


def _sleep_record(timestamp):
    return {"bedtime_start": timestamp}


def test_bedtime_consistency_wraps_around_midnight(monkeypatch):
    module = importlib.import_module("oura_sleep_sync")
    monkeypatch.setattr(
        module,
        "get_sleep_range",
        lambda *_args, **_kwargs: [
            _sleep_record("2026-07-10T23:50:00-05:00"),
            _sleep_record("2026-07-12T00:10:00-05:00"),
        ],
    )

    assert module.calculate_bedtime_variance("unused.sqlite3") == 10


def test_bedtime_consistency_matches_ordinary_nearby_times(monkeypatch):
    module = importlib.import_module("oura_sleep_sync")
    monkeypatch.setattr(
        module,
        "get_sleep_range",
        lambda *_args, **_kwargs: [
            _sleep_record("2026-07-10T22:30:00-05:00"),
            _sleep_record("2026-07-11T23:00:00-05:00"),
        ],
    )

    assert module.calculate_bedtime_variance("unused.sqlite3") == 15


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        (
            [
                _sleep_record("2026-07-10T23:45:00-05:00"),
                _sleep_record("2026-07-11T23:45:00-05:00"),
            ],
            0,
        ),
        (
            [
                _sleep_record("2026-07-10T00:00:00-05:00"),
                _sleep_record("2026-07-11T12:00:00-05:00"),
            ],
            None,
        ),
        (
            [
                _sleep_record(None),
                _sleep_record("not-a-timestamp"),
                _sleep_record("2026-07-11T23:50:00-05:00"),
            ],
            None,
        ),
    ],
)
def test_bedtime_consistency_handles_degenerate_or_invalid_inputs(
    monkeypatch, records, expected
):
    module = importlib.import_module("oura_sleep_sync")
    monkeypatch.setattr(module, "get_sleep_range", lambda *_args, **_kwargs: records)

    assert module.calculate_bedtime_variance("unused.sqlite3") == expected


def test_sleep_summary_names_circular_standard_deviation(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "fit-337-test-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit-337-health-token")
    fitness_app = importlib.import_module("app")
    fitness_app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(fitness_app, "OURA_DB_FILE", str(tmp_path / "oura.sqlite3"))
    monkeypatch.setattr(fitness_app, "get_oura_daily", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fitness_app, "get_oura_daily_range", lambda *_args, **_kwargs: [])

    sleep_sync = importlib.import_module("oura_sleep_sync")
    monkeypatch.setattr(sleep_sync, "get_latest_sleep", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(sleep_sync, "get_sleep_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(sleep_sync, "calculate_bedtime_variance", lambda *_args, **_kwargs: 10)

    response = fitness_app.app.test_client().get("/api/oura/sleep-summary")

    assert response.status_code == 200
    consistency = response.get_json()["consistency"]
    assert consistency["bedtime_circular_std_dev_min"] == 10
    assert consistency["bedtime_variance_min"] == 10

