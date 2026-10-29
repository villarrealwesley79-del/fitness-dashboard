import importlib

import pytest


def _sleep_record(timestamp):
    return {"bedtime_start": timestamp}


def test_bedtime_consistency_wraps_around_midnight(monkeypatch):
    module = importlib.import_module("app")

    assert module._bedtime_variance_from_rows([
        _sleep_record("2026-07-10T23:50:00-05:00"),
        _sleep_record("2026-07-12T00:10:00-05:00"),
    ]) == 10


def test_bedtime_consistency_matches_ordinary_nearby_times(monkeypatch):
    module = importlib.import_module("app")

    assert module._bedtime_variance_from_rows([
        _sleep_record("2026-07-10T22:30:00-05:00"),
        _sleep_record("2026-07-11T23:00:00-05:00"),
    ]) == 15


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
    module = importlib.import_module("app")

    assert module._bedtime_variance_from_rows(records) == expected


def test_sleep_summary_names_circular_standard_deviation(isolated_app, monkeypatch):
    monkeypatch.setattr(isolated_app, "_bedtime_variance_from_rows", lambda *_args, **_kwargs: 10)

    response = isolated_app.app.test_client().get("/api/sleep/summary")

    assert response.status_code == 200
    consistency = response.get_json()["consistency"]
    assert consistency["bedtime_circular_std_dev_min"] == 10
    assert consistency["bedtime_variance_min"] == 10
