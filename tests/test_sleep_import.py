"""Contract tests for validated sleep imports."""
from __future__ import annotations

import copy
import importlib
import inspect
import json

import pytest


@pytest.fixture
def sleep_api(monkeypatch, tmp_path):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    baseline = [{"date": "2026-07-01", "sleep_duration_min": 420}]
    sleep_file = tmp_path / "sleep.json"
    sleep_file.write_text(json.dumps(baseline), encoding="utf-8")
    monkeypatch.setattr(module, "SLEEP_DATA", copy.deepcopy(baseline))
    monkeypatch.setattr(module, "SLEEP_FILE", str(sleep_file))
    return module, module.app.test_client(), sleep_file, baseline


def _post(client, payload):
    return client.post(
        "/api/sleep/import",
        json=payload,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


def _row(**overrides):
    row = {"date": "2026-07-02", "sleep_duration_min": 420, "time_in_bed_min": 450}
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("not-a-number", "invalid_number"),
        ("nan", "invalid_number"),
        ("inf", "invalid_number"),
        (-1, "out_of_range"),
        (1441, "out_of_range"),
    ],
)
def test_sleep_import_rejects_invalid_minute_values(sleep_api, value, reason):
    _module, client, _sleep_file, _baseline = sleep_api

    response = _post(client, {"entries": [_row(sleep_duration_min=value)]})

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json()["error"] == {
        "code": "invalid_sleep_rows",
        "message": "Sleep import contains invalid rows",
        "details": [{"row": 1, "field": "sleep_duration_min", "code": reason}],
    }


def test_sleep_import_returns_structured_error_for_huge_numeric_integer(sleep_api):
    _module, client, _sleep_file, _baseline = sleep_api

    response = _post(client, {"entries": [_row(sleep_duration_min=10**400)]})

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json()["error"]["details"] == [
        {"row": 1, "field": "sleep_duration_min", "code": "invalid_number"}
    ]


def test_sleep_import_accepts_valid_and_midnight_crossing_windows(sleep_api):
    module, client, _sleep_file, _baseline = sleep_api

    response = _post(
        client,
        {
            "entries": [
                _row(date="2026-07-02", sleep_start="23:30", sleep_end="07:00"),
                _row(
                    date="2026-07-03",
                    sleep_start="2026-07-02T23:30:00",
                    sleep_end="2026-07-03T07:00:00",
                ),
            ]
        },
    )

    assert response.status_code == 200
    assert [entry["sleep_start"] for entry in module.SLEEP_DATA[-2:]] == [
        "2026-07-01T23:30:00",
        "2026-07-02T23:30:00",
    ]


@pytest.mark.parametrize(
    ("overrides", "details"),
    [
        (
            {"sleep_start": "not-a-time", "sleep_end": "07:00"},
            [{"row": 1, "field": "sleep_start", "code": "invalid_timestamp"}],
        ),
        (
            {
                "sleep_start": "2026-07-03T07:00:00",
                "sleep_end": "2026-07-02T23:30:00",
            },
            [{"row": 1, "field": "sleep_end", "code": "contradictory_timestamp"}],
        ),
        (
            {"sleep_start": "23:30", "sleep_end": "07:00", "time_in_bed_min": 449},
            [{"row": 1, "field": "time_in_bed_min", "code": "contradictory_minutes"}],
        ),
        (
            {
                "sleep_start": "23:30",
                "sleep_end": "07:00",
                "sleep_duration_min": 420,
                "time_in_bed_min": "",
                "awake_min": 60,
            },
            [{"row": 1, "field": "awake_min", "code": "contradictory_minutes"}],
        ),
    ],
)
def test_sleep_import_rejects_invalid_timestamp_rows_atomically(
    sleep_api, overrides, details
):
    module, client, sleep_file, baseline = sleep_api
    disk_before = sleep_file.read_text(encoding="utf-8")

    response = _post(client, {"entries": [_row(**overrides)]})

    assert response.status_code == 400
    assert response.get_json()["error"]["details"] == details
    assert module.SLEEP_DATA == baseline
    assert sleep_file.read_text(encoding="utf-8") == disk_before


def test_sleep_import_accepts_exactly_1440_minutes(sleep_api):
    _module, client, _sleep_file, _baseline = sleep_api

    response = _post(
        client,
        {"entries": [_row(sleep_duration_min=1440, time_in_bed_min=1440)]},
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("overrides", "details"),
    [
        (
            {"sleep_duration_min": 451, "time_in_bed_min": 450},
            [{"row": 1, "field": "sleep_duration_min", "code": "contradictory_minutes"}],
        ),
        (
            {"deep_min": 200, "rem_min": 150, "light_min": 100},
            [{"row": 1, "field": "sleep_duration_min", "code": "contradictory_minutes"}],
        ),
        (
            {
                "sleep_duration_min": 500,
                "time_in_bed_min": 400,
                "deep_min": 150,
                "rem_min": 150,
                "light_min": 150,
            },
            [
                {"row": 1, "field": "sleep_duration_min", "code": "contradictory_minutes"},
                {"row": 1, "field": "time_in_bed_min", "code": "contradictory_minutes"},
            ],
        ),
        (
            {"sleep_duration_min": 400, "time_in_bed_min": 450, "awake_min": 100},
            [{"row": 1, "field": "awake_min", "code": "contradictory_minutes"}],
        ),
    ],
)
def test_sleep_import_rejects_contradictory_minutes(sleep_api, overrides, details):
    _module, client, _sleep_file, _baseline = sleep_api

    response = _post(client, {"entries": [_row(**overrides)]})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_sleep_rows"
    assert response.get_json()["error"]["details"] == details


def test_sleep_import_accepts_valid_partial_csv_without_unsupplied_comparisons(sleep_api):
    module, client, sleep_file, _baseline = sleep_api

    response = _post(client, {"csv": "date,sleep_duration_min\n2026-07-02,420\n"})

    assert response.status_code == 200
    assert response.get_json() == {"status": "success", "imported": 1}
    assert module.SLEEP_DATA[-1] == {
        "date": "2026-07-02",
        "source": "apple_watch",
        "sleep_duration_min": 420,
        "time_in_bed_min": 0,
        "deep_min": 0,
        "rem_min": 0,
        "light_min": 0,
        "awake_min": 0,
        "sleep_start": None,
        "sleep_end": None,
    }
    assert json.loads(sleep_file.read_text(encoding="utf-8")) == module.SLEEP_DATA


def test_sleep_import_accepts_valid_full_csv_and_aliases_with_truncation(sleep_api):
    module, client, _sleep_file, _baseline = sleep_api
    csv = (
        "day,duration_min,in_bed_min,deep_min,rem_min,light_min,awake_min\n"
        "2026-07-02,420.9,450.9,120.9,130.9,160.9,20.9\n"
    )

    response = _post(client, {"csv": csv})

    assert response.status_code == 200
    assert module.SLEEP_DATA[-1] == {
        "date": "2026-07-02",
        "source": "apple_watch",
        "sleep_duration_min": 420,
        "time_in_bed_min": 450,
        "deep_min": 120,
        "rem_min": 130,
        "light_min": 160,
        "awake_min": 20,
        "sleep_start": None,
        "sleep_end": None,
    }


def test_sleep_import_rejects_mixed_batch_without_mutating_memory_or_disk(sleep_api):
    module, client, sleep_file, baseline = sleep_api
    disk_before = sleep_file.read_text(encoding="utf-8")

    response = _post(
        client,
        {"entries": [_row(date="2026-07-02"), _row(date="2026-07-03", deep_min=-1)]},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["details"] == [
        {"row": 2, "field": "deep_min", "code": "out_of_range"}
    ]
    assert module.SLEEP_DATA == baseline
    assert sleep_file.read_text(encoding="utf-8") == disk_before


def test_sleep_import_treats_explicit_canonical_zero_as_supplied(sleep_api):
    module, client, sleep_file, baseline = sleep_api
    disk_before = sleep_file.read_text(encoding="utf-8")

    response = _post(
        client,
        {
            "entries": [
                {
                    "date": "2026-07-02",
                    "sleep_duration_min": 0,
                    "duration_min": 420,
                    "time_in_bed_min": 0,
                    "in_bed_min": 450,
                    "deep_min": 1,
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["details"] == [
        {"row": 1, "field": "sleep_duration_min", "code": "contradictory_minutes"},
        {"row": 1, "field": "time_in_bed_min", "code": "contradictory_minutes"},
    ]
    assert module.SLEEP_DATA == baseline
    assert sleep_file.read_text(encoding="utf-8") == disk_before


def test_sleep_import_rejects_awake_only_partial_row_that_exceeds_in_bed(sleep_api):
    _module, client, _sleep_file, _baseline = sleep_api

    response = _post(
        client,
        {"entries": [{"date": "2026-07-02", "time_in_bed_min": 30, "awake_min": 60}]},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["details"] == [
        {"row": 1, "field": "awake_min", "code": "contradictory_minutes"}
    ]


def test_sleep_import_rejects_partial_stage_and_awake_total_above_in_bed(sleep_api):
    _module, client, _sleep_file, _baseline = sleep_api

    response = _post(
        client,
        {
            "entries": [
                {
                    "date": "2026-07-02",
                    "time_in_bed_min": 100,
                    "deep_min": 30,
                    "rem_min": 20,
                    "awake_min": 60,
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["details"] == [
        {"row": 1, "field": "awake_min", "code": "contradictory_minutes"}
    ]


def test_sleep_import_rejects_parentless_stage_total_above_daily_limit(sleep_api):
    _module, client, _sleep_file, _baseline = sleep_api

    response = _post(
        client,
        {"entries": [{"date": "2026-07-02", "deep_min": 800, "rem_min": 700}]},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["details"] == [
        {"row": 1, "field": "stage_total_min", "code": "contradictory_minutes"}
    ]


def test_sleep_import_rejects_parentless_stage_and_awake_total_above_daily_limit(sleep_api):
    _module, client, _sleep_file, _baseline = sleep_api

    response = _post(
        client,
        {
            "entries": [
                {"date": "2026-07-02", "deep_min": 800, "rem_min": 500, "awake_min": 200}
            ]
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["details"] == [
        {"row": 1, "field": "awake_min", "code": "contradictory_minutes"}
    ]


def test_sleep_import_uses_per_row_error_tracking(sleep_api):
    module, client, _sleep_file, _baseline = sleep_api

    response = _post(
        client,
        {
            "entries": [
                _row(date="2026-07-02", deep_min="bad"),
                _row(date="2026-07-03", rem_min="bad"),
            ]
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["details"] == [
        {"row": 1, "field": "deep_min", "code": "invalid_number"},
        {"row": 2, "field": "rem_min", "code": "invalid_number"},
    ]
    assert "any(error['row']==row_number for error in errors)" not in inspect.getsource(module.sleep_import)


def test_sleep_import_skips_missing_date_rows(sleep_api):
    module, client, _sleep_file, _baseline = sleep_api

    response = _post(client, {"entries": [{"sleep_duration_min": 1441}, _row()]})

    assert response.status_code == 200
    assert response.get_json() == {"status": "success", "imported": 1}
    assert [entry["date"] for entry in module.SLEEP_DATA] == ["2026-07-01", "2026-07-02"]
