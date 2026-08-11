from __future__ import annotations

import importlib
import sqlite3

import pytest
import oura_client
import oura_sleep_sync
from js_runtime import run_app_js


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


@pytest.mark.parametrize("non_nightly_type", ["nap", "REST"])
@pytest.mark.parametrize("sleep_rows", ["night_then_non_nightly", "non_nightly_then_night"])
def test_oura_same_day_sleep_selection_prefers_long_sleep(
    monkeypatch, non_nightly_type, sleep_rows
):
    day = "2026-06-04"
    long_sleep = {
        "day": day,
        "type": "long_sleep",
        "score": 88,
        "total_sleep_duration": 8 * 60 * 60,
        "deep_sleep_duration": 90 * 60,
        "rem_sleep_duration": 100 * 60,
        "light_sleep_duration": 290 * 60,
        "awake_time": 30 * 60,
        "average_hrv": 52,
        "lowest_heart_rate": 55,
    }
    non_nightly = {
        "day": day,
        "type": non_nightly_type,
        "score": 60,
        "total_sleep_duration": 30 * 60,
        "deep_sleep_duration": 0,
        "rem_sleep_duration": 0,
        "light_sleep_duration": 30 * 60,
        "awake_time": 0,
        "average_hrv": 20,
        "lowest_heart_rate": 70,
    }
    sleep = [long_sleep, non_nightly]
    if sleep_rows == "non_nightly_then_night":
        sleep.reverse()

    client = oura_client.OuraClient(token="fit234-token")

    def request(endpoint, **_kwargs):
        return {
            "daily_readiness": [{"day": day, "score": 80}],
            "daily_sleep": sleep,
            "sleep": sleep,
            "daily_activity": [{"day": day, "steps": 1000, "score": 80}],
        }[endpoint]

    monkeypatch.setattr(client, "_request", request)

    daily = client.get_daily_range(day, day)
    metrics = client.get_today_metrics(day)[3]

    assert daily[0]["sleep_type"] == "long_sleep"
    assert daily[0]["sleep_duration_min"] == 480
    assert metrics["sleep_type"] == "long_sleep"
    assert metrics["sleep_duration_min"] == 480
    assert metrics["hrv"] == 52


def test_vitals_renderer_blocks_inconsistent_sleep_values():
    output = run_app_js(
        ["paintDashboardFromState", "renderVitals", "state"],
        """
const element = () => ({
  textContent: 'stale', innerHTML: '', hidden: false, firstChild: null, className: '',
  classList: { toggle() {}, remove() {} },
});
[
  'dash-sleep', 'glance-sleep', 'glance-sleep-quality', 'insight-title',
  'insight-body', 'insight-sparkline', 'v-rhr', 'v-hrv', 'v-hr-zone',
  'v-hr-zone-sub', 'v-temp', 'v-temp-delta', 'v-steps', 'v-steps-goal',
  'v-active-cal', 'v-active-cal-goal', 'v-total-cal', 'v-total-cal-goal',
  'v-active-min', 'v-active-min-goal', 'spark-steps', 'spark-active-min',
  'spark-sleep', 'v-sleep-dur', 'v-sleep-dur-sub', 'v-sleep-score',
  'v-sleep-score-sub', 'v-weight', 'v-bf', 'v-weight-delta', 'v-bf-delta',
  'v-rhr-delta', 'v-hrv-delta',
].forEach((id) => { sandbox.elements[id] = element(); });

const sleep = {
  last_night: { total_sleep_min: 1, rem_sleep_min: 0, deep_sleep_min: 0 },
  trend_data: [{ date: '2026-06-04', duration_min: 1, score: 88 }],
  data_quality: {
    status: 'inconsistent', source: 'oura', observed_at: '2026-06-04',
    excluded_dates: ['2026-06-04'],
  },
};
const sparklines = [];
e.state.dashboard = null;
e.state.oura = null;
e.state.reco = null;
e.state.ouraSleep = sleep;
sandbox.__fitSet.renderFreshnessChips(() => {});
sandbox.__fitSet.renderRecommendationSourceSummary(() => {});
sandbox.__fitSet.renderMacroCard(() => {});
sandbox.__fitSet.sparkline((_container, values) => sparklines.push(values));
sandbox.__fitSet.getVitals(async () => ({}));
sandbox.__fitSet.getOuraStatus(async () => ({ sleep_score: 88 }));
sandbox.__fitSet.getOuraSleep(async () => sleep);
sandbox.__fitSet.getBody(async () => ({ history: [] }));
sandbox.__fitSet.getOuraTrends(async () => ({ series: [
  { day: '2026-06-03', sleep_duration_min: 480, nightly_sleep: true },
  { day: '2026-06-04', sleep_duration_min: 1, nightly_sleep: false },
] }));

e.paintDashboardFromState();
await e.renderVitals();
process.stdout.write(JSON.stringify({
  dashboard: {
    sleep: sandbox.elements['dash-sleep'].textContent,
    glanceSleep: sandbox.elements['glance-sleep'].textContent,
    glanceQuality: sandbox.elements['glance-sleep-quality'].textContent,
    insightTitle: sandbox.elements['insight-title'].textContent,
    insightBody: sandbox.elements['insight-body'].textContent,
  },
  vitals: {
    duration: sandbox.elements['v-sleep-dur'].textContent,
    subtitle: sandbox.elements['v-sleep-dur-sub'].textContent,
    score: sandbox.elements['v-sleep-score'].textContent,
    scoreSubtitle: sandbox.elements['v-sleep-score-sub'].textContent,
  },
  sleepSparklines: sparklines.slice(-1)[0],
}));
""",
        mocks=[
            "renderFreshnessChips", "renderRecommendationSourceSummary", "renderMacroCard",
            "sparkline", "getVitals", "getOuraStatus", "getOuraSleep", "getBody",
            "getOuraTrends",
        ],
    )

    assert output == {
        "dashboard": {
            "sleep": "--",
            "glanceSleep": "--",
            "glanceQuality": "oura · 2026-06-04 · Check sync",
            "insightTitle": "Sleep data needs review",
            "insightBody": "oura · 2026-06-04 · Check sync",
        },
        "vitals": {
            "duration": "--",
            "subtitle": "Sleep data inconsistent · Check Oura sync",
            "score": "--",
            "scoreSubtitle": "2026-06-04",
        },
        "sleepSparklines": [8],
    }


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


def test_bedtime_variance_wraps_across_midnight(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit234-secret")
    module = importlib.import_module("app")

    variance = module._bedtime_variance_from_rows([
        {"bedtime_start": "2026-06-03T23:50:00", "total_sleep_min": 480},
        {"bedtime_start": "2026-06-04T00:10:00", "total_sleep_min": 480},
    ])

    assert variance == 10


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
    output = run_app_js(
        ["renderVitals"],
        """
const element = () => ({ textContent: '', className: '', classList: { toggle() {}, remove() {} } });
[
  'v-rhr', 'v-hrv', 'v-hr-zone', 'v-hr-zone-sub', 'v-temp', 'v-temp-delta',
  'v-steps', 'v-steps-goal', 'v-active-cal', 'v-active-cal-goal', 'v-total-cal',
  'v-total-cal-goal', 'v-active-min', 'v-active-min-goal', 'spark-steps',
  'spark-active-min', 'spark-sleep', 'v-sleep-dur', 'v-sleep-dur-sub',
  'v-sleep-score', 'v-sleep-score-sub', 'v-weight', 'v-bf', 'v-weight-delta',
  'v-bf-delta', 'v-rhr-delta', 'v-hrv-delta',
].forEach((id) => { sandbox.elements[id] = element(); });
const sparklines = [];
sandbox.__fitSet.getVitals(async () => ({}));
sandbox.__fitSet.getOuraStatus(async () => ({ sleep_score: 88 }));
sandbox.__fitSet.getOuraSleep(async () => ({
  last_night: { total_sleep_min: 480, rem_sleep_min: 100, deep_sleep_min: 90 },
  data_quality: { status: 'ok', excluded_dates: ['2026-06-02'] },
}));
sandbox.__fitSet.getBody(async () => ({ history: [] }));
sandbox.__fitSet.getOuraTrends(async () => ({ series: [
  { day: '2026-06-01', sleep_duration_min: 480, nightly_sleep: true },
  { day: '2026-06-02', sleep_duration_min: 400, nightly_sleep: true },
  { day: '2026-06-03', sleep_duration_min: 30, sleep_type: 'nap', nightly_sleep: false },
  { day: '2026-06-04', sleep_duration_min: 30, sleep_type: 'rest', nightly_sleep: false },
  { day: '2026-06-05', sleep_duration_min: 30, sleep_type: 'late_nap', nightly_sleep: false },
  { day: '2026-06-06', sleep_duration_min: 420, nightly_sleep: true },
] }));
sandbox.__fitSet.sparkline((_container, values) => sparklines.push(values));
await e.renderVitals();
process.stdout.write(JSON.stringify(sparklines.slice(-1)[0]));
""",
        mocks=[
            "getVitals", "getOuraStatus", "getOuraSleep", "getBody", "getOuraTrends",
            "sparkline",
        ],
    )

    assert output == [8, 7]


def test_vitals_renderer_shows_unavailable_for_nullable_sleep_duration():
    output = run_app_js(
        ["renderVitals"],
        """
const element = () => ({ textContent: '', className: '', classList: { toggle() {}, remove() {} } });
[
  'v-rhr', 'v-hrv', 'v-hr-zone', 'v-hr-zone-sub', 'v-temp', 'v-temp-delta',
  'v-steps', 'v-steps-goal', 'v-active-cal', 'v-active-cal-goal', 'v-total-cal',
  'v-total-cal-goal', 'v-active-min', 'v-active-min-goal', 'spark-steps',
  'spark-active-min', 'spark-sleep', 'v-sleep-dur', 'v-sleep-dur-sub',
  'v-sleep-score', 'v-sleep-score-sub', 'v-weight', 'v-bf', 'v-weight-delta',
  'v-bf-delta', 'v-rhr-delta', 'v-hrv-delta',
].forEach((id) => { sandbox.elements[id] = element(); });
let sleep = {
  last_night: { total_sleep_min: null, rem_sleep_min: null, deep_sleep_min: null },
  data_quality: { status: 'ok', excluded_dates: [] },
};
sandbox.__fitSet.getVitals(async () => ({}));
sandbox.__fitSet.getOuraStatus(async () => ({ sleep_score: 88 }));
sandbox.__fitSet.getOuraSleep(async () => sleep);
sandbox.__fitSet.getBody(async () => ({ history: [] }));
sandbox.__fitSet.getOuraTrends(async () => ({ series: [] }));
sandbox.__fitSet.sparkline(() => {});
await e.renderVitals();
const unavailable = sandbox.elements['v-sleep-dur'].textContent;
sleep = {
  last_night: { total_sleep_min: 480, rem_sleep_min: 100, deep_sleep_min: 90 },
  data_quality: { status: 'ok', excluded_dates: [] },
};
await e.renderVitals();
process.stdout.write(JSON.stringify({
  unavailable,
  numeric: sandbox.elements['v-sleep-dur'].textContent,
}));
""",
        mocks=[
            "getVitals", "getOuraStatus", "getOuraSleep", "getBody", "getOuraTrends",
            "sparkline",
        ],
    )

    assert output == {"unavailable": "--", "numeric": "8h"}


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
