from __future__ import annotations

import importlib

import oura_sleep_sync


def test_sleep_summary_flags_near_zero_duration_with_high_score(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)

    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [{
        "day": "2026-06-04",
        "total_sleep_min": 480,
        "deep_sleep_min": 90,
        "rem_sleep_min": 100,
        "light_sleep_min": 290,
        "sleep_score": 88,
    }])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [])
    monkeypatch.setattr(oura_sleep_sync, "calculate_bedtime_variance", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        module,
        "get_oura_daily",
        lambda *_a, **_kw: {
            "day": "2026-06-04",
            "sleep_duration_min": 1,
            "sleep_deep_min": 0,
            "sleep_rem_min": 0,
            "sleep_light_min": 1,
            "sleep_awake_min": 0,
            "sleep_score": 88,
        },
    )
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [])

    response = module.app.test_client().get("/api/oura/sleep-summary")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["last_night"]["total_sleep_min"] == 480
    assert payload["data_quality"] == {
        "status": "inconsistent",
        "reason": "duration_score_conflict",
        "source": "oura",
        "observed_at": "2026-06-04",
        "excluded_dates": ["2026-06-04"],
        "message": "Sleep data is inconsistent. Check Oura sync.",
    }


def test_sleep_summary_does_not_treat_missing_daily_stages_as_zero(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [])
    monkeypatch.setattr(oura_sleep_sync, "calculate_bedtime_variance", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: {
        "day": "2026-06-04",
        "sleep_duration_min": 480,
        "sleep_deep_min": None,
        "sleep_rem_min": None,
        "sleep_light_min": None,
        "sleep_score": 88,
    })
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [])

    payload = module.app.test_client().get("/api/oura/sleep-summary").get_json()

    assert payload["data_quality"] == {"status": "ok"}
    assert payload["week_average"]["deep_min"] is None
    assert payload["week_average"]["rem_min"] is None


def test_vitals_renderer_blocks_inconsistent_sleep_values():
    source = open("static/js/app.js", encoding="utf-8").read()

    assert "sleep.data_quality.status === 'inconsistent'" in source
    assert "Sleep data inconsistent" in source
    assert "const dashboardSleepInconsistent = sleep && sleep.data_quality" in source
    assert "const dashboardSleepQualityKnown = sleep && sleep.data_quality" in source
    assert source.count("!dashboardSleepQualityKnown || dashboardSleepInconsistent ? '--'") == 2
    assert "sleep.data_quality.source, sleep.data_quality.observed_at, 'Check sync'" in source
    assert "d.date !== sleep.data_quality.observed_at" in source
    assert "s.day !== sleep.data_quality.observed_at" in source
    assert "excludedSleepDates.has(d.date)" in source
    assert "excludedSleepDates.has(s.day)" in source
    assert "s.sleep_duration_min != null" in source
    assert "d.score != null" in source
    assert "const sleepQualityWarning = sleep && sleep.data_quality" in source
    assert "if (dashboardSleepInconsistent || sleepQualityWarning)" in source
    assert "sleepQualityWarning ? sleepQualityAction" in source
    assert "Sleep data needs review" in source
    assert "dashboardSleepInconsistent || sleepQualityWarning" in source
    assert "last.rem_sleep_min != null ? `${Math.round(last.rem_sleep_min)}m REM` : 'REM unknown'" in source
    assert "last.deep_sleep_min != null ? `${Math.round(last.deep_sleep_min)}m Deep` : 'Deep unknown'" in source


def test_sleep_summary_flags_duration_stage_conflict(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")

    quality = module._sleep_summary_data_quality({
        "day": "2026-06-04",
        "total_sleep_min": 480,
        "deep_sleep_min": 0,
        "rem_sleep_min": 0,
        "light_sleep_min": 1,
        "sleep_score": 88,
    })

    assert quality["status"] == "inconsistent"
    assert quality["reason"] == "duration_stage_conflict"


def test_sleep_summary_flags_implausible_duration_without_high_score(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")

    quality = module._sleep_summary_data_quality({
        "day": "2026-06-04",
        "total_sleep_min": 1,
        "deep_sleep_min": 0,
        "rem_sleep_min": 0,
        "light_sleep_min": 1,
        "sleep_score": None,
    })

    assert quality["status"] == "inconsistent"
    assert quality["reason"] == "implausible_duration"


def test_sleep_summary_marks_partial_and_excludes_bad_history(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    valid = {
        "day": "2026-06-05",
        "total_sleep_min": 480,
        "deep_sleep_min": 90,
        "rem_sleep_min": 100,
        "light_sleep_min": 290,
        "sleep_score": 88,
    }
    invalid = {
        "day": "2026-06-04",
        "total_sleep_min": 1,
        "deep_sleep_min": 0,
        "rem_sleep_min": 0,
        "light_sleep_min": 1,
        "sleep_score": 88,
    }

    quality = module._sleep_summary_data_quality(valid, [invalid, valid])

    assert quality["status"] == "partial"
    assert quality["reason"] == "historical_inconsistency"
    assert quality["excluded_dates"] == ["2026-06-04"]


def test_sleep_summary_checks_historical_daily_row_even_when_sleep_row_wins(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    valid = {
        "day": "2026-06-03",
        "total_sleep_min": 480,
        "deep_sleep_min": 90,
        "rem_sleep_min": 100,
        "light_sleep_min": 290,
        "sleep_score": 88,
    }
    invalid_daily = {
        "day": "2026-06-03",
        "sleep_duration_min": 1,
        "sleep_deep_min": 0,
        "sleep_rem_min": 0,
        "sleep_light_min": 1,
        "sleep_score": 88,
    }
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [valid])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [valid])
    monkeypatch.setattr(oura_sleep_sync, "calculate_bedtime_variance", lambda *_a, **_kw: 42)
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [invalid_daily])

    payload = module.app.test_client().get("/api/oura/sleep-summary").get_json()

    assert payload["data_quality"]["status"] == "partial"
    assert payload["data_quality"]["excluded_dates"] == ["2026-06-03"]
    assert payload["trend_data"] == []
    assert payload["consistency"] == {"bedtime_variance_min": None, "status": "unknown"}


def test_sleep_summary_keeps_missing_current_stages_unknown(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [])
    monkeypatch.setattr(oura_sleep_sync, "calculate_bedtime_variance", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [])

    payload = module.app.test_client().get("/api/oura/sleep-summary").get_json()

    assert payload["last_night"]["deep_sleep_min"] is None
    assert payload["last_night"]["rem_sleep_min"] is None
    assert payload["last_night"]["light_sleep_min"] is None
    assert payload["last_night"]["total_sleep_min"] is None
    assert payload["last_night"]["sleep_score"] is None
    assert payload["week_average"]["score"] is None


def test_bedtime_variance_uses_only_supplied_valid_rows(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")

    variance = module._bedtime_variance_from_rows([
        {"bedtime_start": "2026-06-03T22:00:00", "total_sleep_min": 480},
        {"bedtime_start": "2026-06-04T22:30:00", "total_sleep_min": 450},
        {"bedtime_start": "2026-06-05T03:00:00", "total_sleep_min": None, "sleep_score": None},
    ])

    assert variance == 15


def test_sleep_summary_does_not_average_missing_duration_as_zero(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    unknown = {
        "day": "2026-06-03",
        "total_sleep_min": None,
        "deep_sleep_min": None,
        "rem_sleep_min": None,
        "light_sleep_min": None,
        "sleep_score": None,
    }
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [unknown])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [unknown])
    monkeypatch.setattr(oura_sleep_sync, "calculate_bedtime_variance", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [])

    payload = module.app.test_client().get("/api/oura/sleep-summary").get_json()

    assert payload["week_average"]["duration_min"] is None
    assert payload["trend_data"][0]["duration_min"] is None
    assert payload["trend_data"][0]["score"] is None


def test_sleep_summary_preserves_missing_daily_duration(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: {
        "day": "2026-06-03",
        "sleep_duration_min": None,
        "sleep_score": 88,
    })
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [])

    payload = module.app.test_client().get("/api/oura/sleep-summary").get_json()

    assert payload["last_night"]["total_sleep_min"] is None
    assert payload["data_quality"] == {"status": "ok"}


def test_sleep_summary_validates_supplied_zero_duration_without_score(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: {
        "day": "2026-06-03",
        "sleep_duration_min": 0,
        "sleep_score": None,
    })
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [])

    payload = module.app.test_client().get("/api/oura/sleep-summary").get_json()

    assert payload["last_night"]["total_sleep_min"] == 0
    assert payload["data_quality"]["status"] == "inconsistent"
    assert payload["data_quality"]["reason"] == "implausible_duration"
