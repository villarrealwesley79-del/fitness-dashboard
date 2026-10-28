from __future__ import annotations

import importlib

import pytest
from js_runtime import run_app_js


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
            "renderFreshnessChips", "renderRecommendationSourceSummary", 
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
            "subtitle": "Sleep data inconsistent · Check wearable sync",
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


def test_vitals_renderer_pairs_partial_sleep_score_with_valid_last_night():
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
  last_night: { total_sleep_min: 480, rem_sleep_min: 100, deep_sleep_min: 90, sleep_score: 82 },
  data_quality: {
    status: 'partial', source: 'oura', observed_at: '2026-06-05',
    excluded_dates: ['2026-06-04', '2026-06-05'],
  },
}));
sandbox.__fitSet.getBody(async () => ({ history: [] }));
sandbox.__fitSet.getOuraTrends(async () => ({ series: [
  { day: '2026-06-03', sleep_duration_min: 480, nightly_sleep: true },
  { day: '2026-06-04', sleep_duration_min: 30, sleep_type: 'nap', nightly_sleep: false, score: 88 },
  { day: '2026-06-05', sleep_duration_min: 25, sleep_type: 'rest', nightly_sleep: false, score: 88 },
] }));
sandbox.__fitSet.sparkline((_container, values) => sparklines.push(values));
await e.renderVitals();
process.stdout.write(JSON.stringify({
  duration: sandbox.elements['v-sleep-dur'].textContent,
  score: sandbox.elements['v-sleep-score'].textContent,
  subtitle: sandbox.elements['v-sleep-score-sub'].textContent,
  sleepSeries: sparklines.slice(-1)[0],
}));
""",
        mocks=[
            "getVitals", "getOuraStatus", "getOuraSleep", "getBody", "getOuraTrends",
            "sparkline",
        ],
    )

    assert output == {
        "duration": "8h",
        "score": 82,
        "subtitle": "oura · 2026-06-05 · Check sync",
        "sleepSeries": [8],
    }


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


def test_vitals_renderer_falls_back_to_status_night_when_sleep_summary_fails():
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
let status = { sleep_duration_min: 450, sleep_score: 84, sleep_breakdown_min: { deep: 90, rem: 100 } };
sandbox.__fitSet.getVitals(async () => ({}));
sandbox.__fitSet.getOuraStatus(async () => status);
sandbox.__fitSet.getOuraSleep(async () => null);
sandbox.__fitSet.getBody(async () => ({ history: [] }));
sandbox.__fitSet.getOuraTrends(async () => ({ series: [] }));
sandbox.__fitSet.sparkline(() => {});
await e.renderVitals();
const fallback = {
  duration: sandbox.elements['v-sleep-dur'].textContent,
  sub: sandbox.elements['v-sleep-dur-sub'].textContent,
  score: sandbox.elements['v-sleep-score'].textContent,
};
status = { sleep_duration_min: 1, sleep_score: 88 };
await e.renderVitals();
process.stdout.write(JSON.stringify({ fallback, implausible: sandbox.elements['v-sleep-dur'].textContent }));
""",
        mocks=[
            "getVitals", "getOuraStatus", "getOuraSleep", "getBody", "getOuraTrends",
            "sparkline",
        ],
    )

    assert output == {
        "fallback": {"duration": "7h 30m", "sub": "100m REM · 90m Deep", "score": 84},
        "implausible": "--",
    }


# --- sleep quality rules over stored wearable facts -----------------------------------------

from datetime import date, timedelta  # noqa: E402

from wearable_fact_store import WearableDailyFact, upsert_daily_facts  # noqa: E402


def _day(offset):
    return (date.today() - timedelta(days=offset)).isoformat()


def _sleep(db, offset, duration, *, deep=None, rem=None, light=None, provider="oura", wake="07:00:00"):
    day = _day(offset)
    rows = [("sleep_duration", duration), ("sleep_deep_duration", deep), ("sleep_rem_duration", rem),
            ("sleep_light_duration", light)]
    upsert_daily_facts(db, [
        WearableDailyFact(
            date=day, provider_id=provider, source_label=provider, metric=metric, value=float(value),
            source_provider=provider, source_system="open_wearables", source_id="sleep-summary",
            observed_at=f"{day}T{wake}Z", used_for_recommendation=True,
        )
        for metric, value in rows if value is not None
    ])


@pytest.fixture
def facts_db(isolated_app):
    return isolated_app.WEARABLE_FACTS_DB_FILE


def test_sleep_debt_bounds_recent_window_before_filtering_invalid_rows(facts_db, isolated_app):
    _sleep(facts_db, 0, 1, deep=0, rem=0, light=1)  # newest night is implausible and is dropped
    for offset in range(1, 7):
        _sleep(facts_db, offset, 420, deep=90, rem=100, light=230)
    _sleep(facts_db, 7, 300, deep=60, rem=60, light=180)  # outside the 7-day window

    result = isolated_app.calculate_sleep_debt(days=7)

    assert (result["debt_minutes"], result["nights_under"], result["avg_sleep_min"]) == (0, 0, 420.0)


def test_sleep_debt_is_unavailable_when_the_window_has_no_valid_nights(facts_db, isolated_app):
    _sleep(facts_db, 0, 1, deep=0, rem=0, light=1)
    assert isolated_app.calculate_sleep_debt(days=2) == {
        "debt_minutes": 0, "debt_hours": 0.0, "nights_under": 0, "avg_sleep_min": 0.0, "status": "good",
        "message": "No recent sleep-duration data available from wearable facts.", "sample_count": 0,
    }


def test_sleep_summary_reads_the_preferred_provider_night_and_keeps_missing_stages_unknown(facts_db, isolated_app):
    _sleep(facts_db, 0, 450, provider="whoop")
    _sleep(facts_db, 0, 480, deep=90, rem=100, light=290, provider="oura")

    payload = isolated_app.app.test_client().get("/api/sleep/summary").get_json()

    assert payload["last_night"]["total_sleep_min"] == 480
    assert payload["last_night"]["source"] == "oura"
    assert payload["last_night"]["sleep_score"] is None
    assert payload["data_quality"] == {"status": "ok"}
    assert payload["trend_data"] == [{"date": _day(0), "duration_min": 480, "score": None}]


def test_sleep_summary_excludes_an_inconsistent_night_from_the_averages(facts_db, isolated_app):
    _sleep(facts_db, 1, 480, deep=90, rem=100, light=290)
    _sleep(facts_db, 0, 480, deep=0, rem=0, light=1)  # stages disagree with the total

    payload = isolated_app.app.test_client().get("/api/sleep/summary").get_json()

    assert payload["data_quality"]["status"] == "inconsistent"
    assert payload["data_quality"]["reason"] == "duration_stage_conflict"
    assert payload["data_quality"]["source"] == "oura"
    assert [row["date"] for row in payload["trend_data"]] == [_day(1)]
    assert payload["week_average"]["duration_min"] == 480


def test_sleep_summary_bedtime_spread_comes_from_wake_time_minus_time_in_bed(facts_db, isolated_app):
    for offset, wake in ((3, "06:00:00"), (2, "06:30:00"), (1, "06:00:00"), (0, "06:30:00")):
        _sleep(facts_db, offset, 480, deep=90, rem=100, light=290, wake=wake)

    payload = isolated_app.app.test_client().get("/api/sleep/summary").get_json()

    assert payload["consistency"]["bedtime_circular_std_dev_min"] == 15
    assert payload["consistency"]["status"] == "excellent"


def test_sleep_rule_helpers_still_classify_naps_and_conflicts(isolated_app):
    reason = isolated_app._sleep_row_inconsistency_reason
    assert reason({"total_sleep_min": 30, "sleep_score": 88, "sleep_type": "nap"}) == "duration_score_conflict"
    assert reason({"total_sleep_min": 30, "sleep_score": None, "sleep_type": "nap"}) is None
    assert reason({"total_sleep_min": 30, "sleep_score": None, "sleep_type": "long_sleep"}) == "implausible_duration"
    assert not isolated_app._is_valid_nightly_sleep_row({"total_sleep_min": 400, "sleep_type": "late_nap"})
