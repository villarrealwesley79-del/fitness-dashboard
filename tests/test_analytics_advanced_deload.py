import importlib
from datetime import date, timedelta

import pytest


@pytest.mark.parametrize(
    ("needed", "expected_deload_recommended"),
    [(True, True), (False, False)],
)
def test_analytics_advanced_uses_detector_needed_flag(
    monkeypatch, needed, expected_deload_recommended
):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "USER_SETTINGS", {"fatigue_threshold": 100})
    monkeypatch.setattr(module, "calculate_volume", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "compute_hrv_trend", lambda *_args, **_kwargs: "improving")
    monkeypatch.setattr(
        module, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0}
    )
    monkeypatch.setattr(module, "_decayed_soreness", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_last_n_sessions_rpe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "detect_deload_need",
        lambda *_args, **_kwargs: {"needed": needed, "weeks_since_deload": 0},
    )

    response = module.app.test_client().get("/api/analytics/advanced")

    assert response.status_code == 200
    assert response.get_json()["deload_recommended"] is expected_deload_recommended


@pytest.mark.parametrize(
    ("hrv_values", "expected_trend", "expected_fatigue"),
    [
        ([60.0, 60.0, 60.0, 64.0, 64.0, 64.0], "up", 22.0),
        ([60.0, 60.0, 60.0, 61.0, 61.0, 61.0], "stable", 27.0),
        ([64.0, 64.0, 64.0, 60.0, 60.0, 60.0], "down", 34.0),
    ],
)
def test_analytics_advanced_maps_real_oura_hrv_trends(
    monkeypatch, hrv_values, expected_trend, expected_fatigue
):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "USER_SETTINGS", {"fatigue_threshold": 100})
    monkeypatch.setattr(module, "calculate_volume", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        module, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0}
    )
    monkeypatch.setattr(module, "_decayed_soreness", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_last_n_sessions_rpe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "detect_deload_need",
        lambda *_args, **_kwargs: {"needed": False, "weeks_since_deload": 0},
    )

    calls = []

    def get_rows(db_path, start_date, end_date):
        calls.append((db_path, start_date, end_date))
        return [{"hrv": value} for value in hrv_values]

    real_compute_hrv_trend = module.compute_hrv_trend

    def record_compute_hrv_trend(values):
        assert values == hrv_values
        return real_compute_hrv_trend(values)

    monkeypatch.setattr(module, "get_oura_daily_range", get_rows)
    monkeypatch.setattr(module, "compute_hrv_trend", record_compute_hrv_trend)

    response = module.app.test_client().get("/api/analytics/advanced")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["factors"]["hrv_trend"] == expected_trend
    assert payload["fatigue_score"] == expected_fatigue
    assert len(calls) == 1
    db_path, start_date, end_date = calls[0]
    assert db_path == module.OURA_DB_FILE
    assert date.fromisoformat(end_date) - date.fromisoformat(start_date) == timedelta(days=6)


@pytest.mark.parametrize("failure", ["lookup", "trend"])
def test_analytics_advanced_uses_unknown_hrv_when_oura_lookup_or_trend_fails(
    monkeypatch, failure
):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "USER_SETTINGS", {"fatigue_threshold": 100})
    monkeypatch.setattr(module, "calculate_volume", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        module, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0}
    )
    monkeypatch.setattr(module, "_decayed_soreness", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_last_n_sessions_rpe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "detect_deload_need",
        lambda *_args, **_kwargs: {"needed": False, "weeks_since_deload": 0},
    )

    if failure == "lookup":
        lookup_calls = []

        def get_rows(*_args, **_kwargs):
            lookup_calls.append(True)
            raise RuntimeError("Oura lookup failed")

        monkeypatch.setattr(module, "get_oura_daily_range", get_rows)
    else:
        monkeypatch.setattr(
            module,
            "get_oura_daily_range",
            lambda *_args, **_kwargs: [
                {"hrv": 60.0},
                {"hrv": 60.0},
                {"hrv": 60.0},
                {"hrv": 60.0},
            ],
        )
        compute_inputs = []

        def fail_compute_hrv_trend(values):
            compute_inputs.append(values)
            raise RuntimeError("HRV trend failed")

        monkeypatch.setattr(module, "compute_hrv_trend", fail_compute_hrv_trend)

    response = module.app.test_client().get("/api/analytics/advanced")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["factors"]["hrv_trend"] == "unknown"
    assert payload["fatigue_score"] == 28.0
    if failure == "lookup":
        assert lookup_calls == [True]
    else:
        assert compute_inputs == [[60.0, 60.0, 60.0, 60.0]]


@pytest.mark.parametrize("hrv_values", [[], [60.0, 61.0, 62.0]])
def test_analytics_advanced_uses_unknown_hrv_for_sparse_oura_data(
    monkeypatch, hrv_values
):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "USER_SETTINGS", {"fatigue_threshold": 100})
    monkeypatch.setattr(module, "calculate_volume", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        module, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0}
    )
    monkeypatch.setattr(module, "_decayed_soreness", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_last_n_sessions_rpe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "detect_deload_need",
        lambda *_args, **_kwargs: {"needed": False, "weeks_since_deload": 0},
    )
    monkeypatch.setattr(
        module,
        "get_oura_daily_range",
        lambda *_args, **_kwargs: [{"hrv": value} for value in hrv_values],
    )

    response = module.app.test_client().get("/api/analytics/advanced")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["factors"]["hrv_trend"] == "unknown"
    assert payload["fatigue_score"] == 28.0
