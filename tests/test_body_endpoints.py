"""Focused contracts for body measurement, recomp, and Navy endpoints."""
from __future__ import annotations

import copy
import importlib

import pytest


@pytest.fixture()
def body_api(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "BODY_DATA", [])
    monkeypatch.setattr(module, "USER_SETTINGS", copy.deepcopy(module.DEFAULT_SETTINGS))
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    return module, module.app.test_client()


def _post(client, path, payload):
    return client.post(
        path,
        json=payload,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "weight_lbs"),
        ({"weight_lbs": 49}, "weight_lbs"),
        ({"weight_lbs": 1001}, "weight_lbs"),
        ({"weight_lbs": 180, "body_fat_pct": 0}, "body_fat_pct"),
        ({"weight_lbs": 180, "body_fat_pct": 61}, "body_fat_pct"),
        ({"weight_lbs": 180, "notes": "x" * 2001}, "notes"),
    ],
)
def test_add_body_measurement_rejects_invalid_fields_without_persisting(
    body_api, payload, field
):
    module, client = body_api

    response = _post(client, "/api/add-body-measurement", payload)

    assert response.status_code == 400
    assert field in response.get_json()["error"]["message"]
    assert module.BODY_DATA == []


def test_add_body_measurement_persists_normalized_values(body_api, monkeypatch):
    module, client = body_api
    saved = []
    monkeypatch.setattr(module, "save_json", lambda path, value: saved.append((path, value)))

    response = _post(
        client,
        "/api/add-body-measurement",
        {"date": "2026-07-10", "weight_lbs": "180.5", "body_fat_pct": "20.2"},
    )

    assert response.status_code == 200
    entry = response.get_json()["body_measurement"]
    assert entry["weight_lbs"] == 180.5
    assert entry["body_fat_pct"] == 20.2
    assert entry["date"] == "2026-07-10"
    assert saved == [(module.BODY_FILE, module.BODY_DATA)]


def test_body_recomp_sorts_history_and_calculates_rolling_and_mass(body_api, monkeypatch):
    module, client = body_api
    monkeypatch.setattr(
        module,
        "BODY_DATA",
        [
            {
                "date": f"2026-07-{index:02d}",
                "weight_lbs": 99 + index,
                "body_fat_pct": 20 if index == 1 else None,
            }
            for index in range(8, 0, -1)
        ],
    )

    response = client.get("/api/body-recomp")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dates"] == [f"2026-07-{index:02d}" for index in range(1, 9)]
    assert payload["weight_7d_avg"][-1] == 104.0
    assert payload["lean_mass_lbs"] == [80.0] + [None] * 7
    assert payload["fat_mass_lbs"] == [20.0] + [None] * 7


@pytest.mark.parametrize(
    ("weights", "target", "expected_eta"),
    [
        ([200 - index for index in range(14)], 180, 1.1),
        ([180] * 14, 170, None),
        ([200 - index for index in range(13)], 180, None),
    ],
)
def test_body_recomp_eta_edges(body_api, monkeypatch, weights, target, expected_eta):
    module, client = body_api
    monkeypatch.setattr(
        module,
        "BODY_DATA",
        [
            {"date": f"2026-06-{index + 1:02d}", "weight_lbs": weight, "body_fat_pct": 20}
            for index, weight in enumerate(weights)
        ],
    )
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings["target_weight_lbs"] = target
    monkeypatch.setattr(module, "USER_SETTINGS", settings)

    response = client.get("/api/body-recomp")

    assert response.status_code == 200
    assert response.get_json()["summary"]["eta_weeks"] == expected_eta


@pytest.mark.parametrize(
    ("old_weight", "current_weight", "expected"),
    [(180, 181, "increasing"), (181, 180, "decreasing"), (180, 180.4, "stable")],
)
def test_dashboard_labels_thirty_day_body_trend(
    body_api, monkeypatch, old_weight, current_weight, expected
):
    module, client = body_api
    monkeypatch.setattr(
        module,
        "BODY_DATA",
        [
            {"date": "2020-01-01", "weight_lbs": old_weight, "body_fat_pct": 20},
            {"date": "2099-01-01", "weight_lbs": current_weight, "body_fat_pct": 19},
        ],
    )
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "RECOVERY_DATA", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "CARDIO_ROTATION_CURSOR", {})
    monkeypatch.setattr(module, "get_oura_daily", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0})
    monkeypatch.setattr(module, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(module, "_cached_wttr", lambda *_args, **_kwargs: {"available": False})
    monkeypatch.setattr(module, "_compute_data_freshness", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "_nutrition_context_for_date", lambda *_args, **_kwargs: {"warnings": []})
    monkeypatch.setattr(module, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    assert response.get_json()["body_stats"]["trend"] == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"sex": "male", "height_in": 47, "neck_in": 15, "waist_in": 35},
        {"sex": "male", "height_in": 70, "neck_in": 7, "waist_in": 35},
        {"sex": "male", "height_in": 70, "neck_in": 15, "waist_in": 81},
        {"sex": "female", "height_in": 65, "neck_in": 13, "waist_in": 30},
    ],
)
def test_navy_calculator_validates_male_and_female_inputs(body_api, payload):
    _module, client = body_api

    response = _post(client, "/api/body/navy-calc", payload)

    assert response.status_code == 400


def test_navy_calculator_computes_both_formulas_and_clamps_output(body_api):
    _module, client = body_api

    male = _post(
        client,
        "/api/body/navy-calc",
        {"sex": "male", "height_in": 72, "neck_in": 20, "waist_in": 20},
    )
    female = _post(
        client,
        "/api/body/navy-calc",
        {"sex": "female", "height_in": 65, "neck_in": 13, "waist_in": 30, "hip_in": 40},
    )
    upper_clamp = _post(
        client,
        "/api/body/navy-calc",
        {"sex": "female", "height_in": 48, "neck_in": 8, "waist_in": 80, "hip_in": 80},
    )

    assert male.status_code == 200
    assert male.get_json()["body_fat_pct"] == 3.0
    assert female.status_code == 200
    assert female.get_json()["body_fat_pct"] == 31.09
    assert upper_clamp.status_code == 200
    assert upper_clamp.get_json()["body_fat_pct"] == 60.0
