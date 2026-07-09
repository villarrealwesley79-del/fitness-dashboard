import importlib

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
    monkeypatch.setattr(module, "compute_hrv_trend", lambda *_args, **_kwargs: {"trend": "up"})
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
