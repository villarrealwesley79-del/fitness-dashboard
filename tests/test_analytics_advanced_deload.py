import importlib
from datetime import date, timedelta

import pytest


def _stub_advanced_analytics_inputs(
    monkeypatch,
    module,
    *,
    volume=None,
    settings=None,
    sleep_debt=0,
    soreness=0,
    rpes=None,
    weeks_since_deload=0,
    detector_needed=False,
):
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "USER_SETTINGS", settings or {})
    monkeypatch.setattr(module, "calculate_volume", lambda *_args, **_kwargs: volume or {})
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "calculate_sleep_debt",
        lambda *_args, **_kwargs: {"debt_minutes": sleep_debt},
    )
    monkeypatch.setattr(module, "_decayed_soreness", lambda *_args, **_kwargs: soreness)
    monkeypatch.setattr(module, "_last_n_sessions_rpe", lambda *_args, **_kwargs: rpes or [])
    monkeypatch.setattr(
        module,
        "detect_deload_need",
        lambda *_args, **_kwargs: {
            "needed": detector_needed,
            "weeks_since_deload": weeks_since_deload,
        },
    )


@pytest.mark.parametrize(
    ("sets", "expected_zone"),
    [
        (5, "below_mv"),
        (6, "mv"),
        (8, "mv"),
        (9, "mev_to_mav"),
        (18, "mev_to_mav"),
        (19, "mav_high"),
        (21, "mav_high"),
        (22, "mrv_risk"),
    ],
)
def test_analytics_advanced_default_volume_landmark_boundaries(
    monkeypatch, sets, expected_zone
):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    _stub_advanced_analytics_inputs(
        monkeypatch,
        module,
        volume={"chest": {"sets": sets}},
        settings={"fatigue_threshold": 100},
    )

    response = module.app.test_client().get("/api/analytics/advanced")

    assert response.status_code == 200
    landmark = response.get_json()["volume_landmarks"][0]
    assert landmark == {
        "muscle": "chest",
        "sets": sets,
        "landmarks": module.DEFAULT_SETTINGS["volume_landmarks"]["default"],
        "zone": expected_zone,
    }


def test_analytics_advanced_fatigue_factors_and_cap(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    workouts = [
        {"exercises": [{"sets": [{"weight_lbs": 1000, "reps": 10}]}]}
        for _ in range(12)
    ]
    _stub_advanced_analytics_inputs(
        monkeypatch,
        module,
        settings={"fatigue_threshold": 100},
        sleep_debt=900,
        soreness=10,
        rpes=[9, 9, 9],
        weeks_since_deload=20,
    )
    monkeypatch.setattr(module, "WORKOUTS", workouts)

    response = module.app.test_client().get("/api/analytics/advanced")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["fatigue_score"] == 100
    assert payload["factors"] == {
        "hrv_trend": "unknown",
        "sleep_debt_min": 900,
        "volume_load": 120000.0,
    }
    assert payload["autoregulation"] == {
        "avg_rpe_last_3": 9.0,
        "reduce_intensity": True,
    }
    assert payload["mesocycle_weeks"] == 20
    assert payload["recovery"] == {"score": 0, "suggested_intensity": "recovery"}


def test_analytics_advanced_fatigue_formula_before_cap(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    workouts = [
        {"exercises": [{"sets": [{"weight_lbs": 100, "reps": 10}]}]}
        for _ in range(12)
    ]
    _stub_advanced_analytics_inputs(
        monkeypatch,
        module,
        settings={"fatigue_threshold": 95},
        sleep_debt=60,
        soreness=2,
        rpes=[9, 9, 9],
        weeks_since_deload=2,
    )
    monkeypatch.setattr(module, "WORKOUTS", workouts)

    response = module.app.test_client().get("/api/analytics/advanced")

    assert response.status_code == 200
    payload = response.get_json()
    # 22 base + 6 unknown HRV + 2 sleep + 1 volume + 4 soreness + 8 RPE + 5 mesocycle.
    assert payload["fatigue_score"] == 48.0
    assert payload["deload_recommended"] is False


@pytest.mark.parametrize(
    ("threshold", "scenario", "expected_fatigue", "expected_deload"),
    [
        (39, "forty", 40.0, False),
        (40, "forty", 40.0, True),
        (41, "forty", 40.0, False),
        (95, "cap", 100, True),
        (96, "eighty", 80.0, True),
    ],
)
def test_analytics_advanced_fatigue_threshold_is_inclusive(
    monkeypatch, threshold, scenario, expected_fatigue, expected_deload
):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    workouts = []
    sleep_debt = 360
    soreness = 0
    rpes = []
    weeks_since_deload = 0
    if scenario == "cap":
        workouts = [
            {"exercises": [{"sets": [{"weight_lbs": 2500, "reps": 10}]}]}
            for _ in range(12)
        ]
        sleep_debt = 900
        soreness = 10
        rpes = [9, 9, 9]
        weeks_since_deload = 20
    elif scenario == "eighty":
        workouts = [
            {"exercises": [{"sets": [{"weight_lbs": 2500, "reps": 10}]}]}
            for _ in range(12)
        ]
        sleep_debt = 900
        soreness = 3.5
    _stub_advanced_analytics_inputs(
        monkeypatch,
        module,
        settings={"fatigue_threshold": threshold},
        sleep_debt=sleep_debt,
        soreness=soreness,
        rpes=rpes,
        weeks_since_deload=weeks_since_deload,
    )
    monkeypatch.setattr(module, "WORKOUTS", workouts)

    response = module.app.test_client().get("/api/analytics/advanced")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["fatigue_score"] == expected_fatigue
    assert payload["deload_recommended"] is expected_deload


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"volume_landmarks": "invalid", "fatigue_threshold": "invalid"},
        {"volume_landmarks": {"default": {"mv": 6}}},
        {
            "volume_landmarks": {
                "default": {"mv": 20, "mev": 5, "mav_min": 4, "mav_max": 3, "mrv": 0}
            },
            "fatigue_threshold": False,
        },
        {
            "volume_landmarks": {
                "default": {
                    "mv": 6,
                    "mev": 9,
                    "mav_min": 12,
                    "mav_max": float("nan"),
                    "mrv": 22,
                }
            },
            "fatigue_threshold": float("nan"),
        },
        {
            "volume_landmarks": {
                "default": {"mv": -1, "mev": 9, "mav_min": 12, "mav_max": 18, "mrv": 22}
            },
            "fatigue_threshold": float("inf"),
        },
        {
            "volume_landmarks": {
                "default": {
                    "mv": 6,
                    "mev": 9,
                    "mav_min": 12,
                    "mav_max": 18,
                    "mrv": 10**1000,
                }
            },
            "fatigue_threshold": 10**1000,
        },
    ],
)
def test_analytics_advanced_missing_or_malformed_settings_use_defaults(
    monkeypatch, settings
):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    _stub_advanced_analytics_inputs(
        monkeypatch,
        module,
        volume={"chest": {"sets": 22}},
        settings=settings,
    )

    response = module.app.test_client().get("/api/analytics/advanced")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["volume_landmarks"][0]["landmarks"] == module.DEFAULT_SETTINGS[
        "volume_landmarks"
    ]["default"]
    assert payload["volume_landmarks"][0]["zone"] == "mrv_risk"
    assert payload["deload_recommended"] is False


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
