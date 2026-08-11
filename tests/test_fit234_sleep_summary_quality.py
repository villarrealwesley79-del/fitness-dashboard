from __future__ import annotations

import importlib
import sqlite3

import pytest
import oura_client
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
    assert source.count("sleep.last_night && sleep.last_night.total_sleep_min != null ? fmtDur(sleep.last_night.total_sleep_min) : '--'") == 2
    assert "oura && oura.sleep_duration_min != null ? fmtDur(oura.sleep_duration_min)" not in source


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


@pytest.mark.parametrize("sleep_type", ["nap", " rest ", "LATE_NAP"])
def test_sleep_summary_accepts_explicit_subhour_nap_types(monkeypatch, sleep_type):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")

    quality = module._sleep_summary_data_quality({
        "day": "2026-06-04",
        "total_sleep_min": 30,
        "sleep_score": None,
        "sleep_type": sleep_type,
    })

    assert quality == {"status": "ok"}


def test_sleep_summary_keeps_high_score_conflict_for_explicit_nap(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")

    quality = module._sleep_summary_data_quality({
        "day": "2026-06-04",
        "total_sleep_min": 30,
        "sleep_score": 88,
        "sleep_type": "nap",
    })

    assert quality["status"] == "inconsistent"
    assert quality["reason"] == "duration_score_conflict"


@pytest.mark.parametrize("sleep_type", [None, "", "unknown", "main", "long_sleep"])
def test_sleep_summary_keeps_subhour_guard_for_non_nap_types(monkeypatch, sleep_type):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")

    quality = module._sleep_summary_data_quality({
        "day": "2026-06-04",
        "total_sleep_min": 30,
        "sleep_score": None,
        "sleep_type": sleep_type,
    })

    assert quality["status"] == "inconsistent"
    assert quality["reason"] == "implausible_duration"


def test_oura_client_reports_normalized_fallback_sleep_type(monkeypatch):
    client = oura_client.OuraClient(token="test-token")

    def request(endpoint, **_kwargs):
        if endpoint == "sleep":
            return [{
                "day": "2026-06-04",
                "type": " NAP ",
                "total_sleep_duration": 1800,
            }]
        return []

    monkeypatch.setattr(client, "_request", request)

    _readiness, _sleep_score, _hrv, metrics, _raw = client.get_today_metrics("2026-06-04")

    assert metrics["sleep_type"] == "nap"
    assert metrics["sleep_duration_min"] == 30


def test_sleep_summary_does_not_promote_nap_to_last_night(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    long_sleep = {
        "day": "2026-06-03",
        "total_sleep_min": 480,
        "deep_sleep_min": 90,
        "rem_sleep_min": 100,
        "light_sleep_min": 290,
        "sleep_score": 88,
    }
    nap = {
        "day": "9999-01-01",
        "sleep_type": "nap",
        "sleep_duration_min": 30,
        "sleep_score": None,
    }
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [long_sleep])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [long_sleep])
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: nap)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [nap])

    payload = module.app.test_client().get("/api/oura/sleep-summary").get_json()

    assert payload["last_night"]["date"] == "2026-06-03"
    assert payload["last_night"]["total_sleep_min"] == 480
    assert payload["data_quality"] == {"status": "ok"}


def test_sleep_summary_treats_scored_nap_conflict_as_historical(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    long_sleep = {
        "day": "2026-06-03",
        "total_sleep_min": 480,
        "deep_sleep_min": 90,
        "rem_sleep_min": 100,
        "light_sleep_min": 290,
        "sleep_score": 88,
    }
    scored_nap = {
        "day": "9999-01-01",
        "sleep_type": "nap",
        "sleep_duration_min": 30,
        "sleep_score": 88,
    }
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [long_sleep])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [long_sleep])
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: scored_nap)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [scored_nap])

    payload = module.app.test_client().get("/api/oura/sleep-summary").get_json()

    assert payload["last_night"]["date"] == "2026-06-03"
    assert payload["last_night"]["total_sleep_min"] == 480
    assert payload["data_quality"]["status"] == "partial"
    assert payload["data_quality"]["reason"] == "historical_inconsistency"
    assert payload["data_quality"]["excluded_dates"] == ["9999-01-01"]


def test_smart_recommendation_excludes_inconsistent_sleep_from_debt_and_reasoning(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)

    db_path = tmp_path / "oura.db"
    oura_client.init_oura_db(str(db_path))
    oura_client.upsert_oura_daily(
        str(db_path),
        "2026-06-04",
        80,
        88,
        55,
        None,
        sleep_duration_min=1,
        sleep_deep_min=0,
        sleep_rem_min=0,
        sleep_light_min=1,
    )

    monkeypatch.setattr(module, "OURA_DB_FILE", str(db_path))
    monkeypatch.setattr(
        module,
        "get_oura_daily",
        lambda *_a, **_kw: {"readiness_score": 80, "sleep_score": 88, "hrv": 55},
    )
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "RECOVERY_DATA", [])
    monkeypatch.setattr(module, "get_recent_hrv_trend", lambda *_a, **_kw: "unknown")
    monkeypatch.setattr(module, "filter_recent_soreness", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "summarize_recent_completion", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        module,
        "calculate_acwr",
        lambda *_a, **_kw: {
            "acute_load": 0,
            "chronic_load": 0,
            "acwr": 0,
            "risk": "low",
        },
    )
    monkeypatch.setattr(
        module,
        "calculate_recovery_bonus",
        lambda *_a, **_kw: {"bonus_points": 0},
    )
    monkeypatch.setattr(
        module,
        "_apple_health_hr_intensity_summary",
        lambda *_a, **_kw: {"applied_count": 0},
    )
    monkeypatch.setattr(module, "_cached_wttr", lambda *_a, **_kw: {"available": False})
    monkeypatch.setattr(
        module,
        "_whoop_recommendation_context",
        lambda *_a, **_kw: {"signals": {}, "source_conflict": {}},
    )
    monkeypatch.setattr(
        module,
        "apply_wearable_modifiers",
        lambda recommendation, next_workout, **_kw: {
            "recommendation": recommendation,
            "next_workout": next_workout,
            "load_source": "deterministic",
        },
    )
    monkeypatch.setattr(module, "_open_wearables_recommendation_facts", lambda: {})
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, _facts: (recommendation, {}),
    )
    monkeypatch.setattr(module, "_compute_data_freshness", lambda: {})
    monkeypatch.setattr(module, "_confidence_level_from", lambda *_a, **_kw: "low")
    monkeypatch.setattr(module, "get_current_workout_plan", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_food_log_entries_for_context", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "_nutrition_context_for_date", lambda *_a, **_kw: {"warnings": []})
    monkeypatch.setattr(module, "_workout_looks_hard", lambda *_a, **_kw: False)
    monkeypatch.setattr(
        module,
        "generate_next_workout",
        lambda *_a, **_kw: {"name": "Test workout", "focus": "full_body", "exercises": []},
    )

    response = module.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["readiness_factors"]["sleep_debt"]["debt_minutes"] == 0
    assert "Sleep debt" not in payload["reasoning"]


@pytest.mark.parametrize("sleep_type", ["nap", "rest", "late_nap"])
def test_sleep_summary_excludes_non_nightly_daily_rows_from_weekly_aggregates(
    monkeypatch, sleep_type
):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    night = {
        "day": "2026-06-03",
        "total_sleep_min": 480,
        "deep_sleep_min": 90,
        "rem_sleep_min": 100,
        "light_sleep_min": 290,
        "sleep_score": 88,
    }
    non_night = {
        "day": "2026-06-04",
        "sleep_type": sleep_type,
        "sleep_duration_min": 30,
        "sleep_score": None,
    }
    monkeypatch.setattr(oura_sleep_sync, "get_latest_sleep", lambda *_a, **_kw: [night])
    monkeypatch.setattr(oura_sleep_sync, "get_sleep_range", lambda *_a, **_kw: [night])
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: non_night)
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_a, **_kw: [non_night])

    payload = module.app.test_client().get("/api/oura/sleep-summary").get_json()

    assert payload["week_average"]["duration_min"] == 480
    assert payload["trend_data"] == [
        {"date": "2026-06-03", "duration_min": 480, "score": 88}
    ]


def test_vitals_renderer_filters_sleep_plot_to_shared_nightly_contract():
    source = open("static/js/app.js", encoding="utf-8").read()

    assert "nightly_sleep" in source
    assert "s.nightly_sleep === true" in source


def test_oura_daily_migrates_and_preserves_nullable_sleep_type(tmp_path):
    db_path = tmp_path / "oura.db"
    legacy_columns = [
        f"{name} {column_type}"
        for name, column_type in oura_client.OURA_COLUMNS.items()
        if name != "sleep_type"
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"CREATE TABLE oura_daily ({', '.join(legacy_columns)})")
        conn.execute("INSERT INTO oura_daily(day) VALUES (?)", ("2026-06-03",))

    oura_client.init_oura_db(str(db_path))

    assert oura_client.get_oura_daily(str(db_path), "2026-06-03")["sleep_type"] is None

    oura_client.upsert_oura_daily(
        str(db_path),
        "2026-06-04",
        None,
        None,
        None,
        None,
        sleep_duration_min=30,
        sleep_type="nap",
    )
    assert oura_client.get_oura_daily(str(db_path), "2026-06-04")["sleep_type"] == "nap"

    oura_client.upsert_oura_daily(
        str(db_path),
        "2026-06-04",
        None,
        None,
        None,
        None,
        sleep_type=None,
    )
    assert oura_client.get_oura_daily(str(db_path), "2026-06-04")["sleep_type"] == "nap"
