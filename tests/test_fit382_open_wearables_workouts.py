"""FIT-382 contracts for Open Wearables per-workout effort metrics."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import open_wearables_hub as hub
from js_runtime import run_app_js


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit382-open-wearables-secret")
    module = importlib.import_module("app")
    original_workouts = list(module.WORKOUTS)
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    module.WORKOUTS.clear()
    monkeypatch.setattr(module, "_load_apple_health_recommendation_workouts", lambda **_kwargs: [])
    yield module
    module.WORKOUTS[:] = original_workouts
    module.app.config.update(LOGIN_DISABLED=False)


def _open_wearables_payload(*, include_max_hr=True):
    workout = {
        "id": "11111111-2222-4333-8444-555555555555",
        "type": "running",
        "name": "Outdoor Run",
        "start_time": "2026-07-12T12:00:00Z",
        "end_time": "2026-07-12T12:42:00Z",
        "duration_seconds": 2520,
        "source": {"provider": "whoop", "device": "WHOOP 5.0"},
        "calories_kcal": 386.4,
        "avg_heart_rate_bpm": 148,
        "notes": "Steady aerobic run",
        "user_id": "must-not-leak",
        "raw": {"access_token": "must-not-leak"},
    }
    if include_max_hr:
        workout["max_heart_rate_bpm"] = 176
    return {"data": [workout], "next_cursor": None}


def _normalized_workout(*, max_hr=176):
    return {
        "id": "open_wearables:11111111-2222-4333-8444-555555555555",
        "external_id": "11111111-2222-4333-8444-555555555555",
        "source": "open_wearables",
        "provider": "Open Wearables",
        "provider_source": "whoop",
        "device": "WHOOP 5.0",
        "date": "2026-07-12",
        "start_time": "2026-07-12T12:00:00Z",
        "end_time": "2026-07-12T12:42:00Z",
        "activity_type": "Outdoor Run",
        "session_type": "running",
        "duration_minutes": 42,
        "calories_burned": 386.4,
        "avg_heart_rate": 148,
        "max_heart_rate": max_hr,
        "notes": "Steady aerobic run",
    }


def _apple_health_workout(*, start="2026-07-12T07:00:00-05:00", avg_hr=None):
    return {
        "id": "apple-health:synthetic-id",
        "date": "2026-07-12",
        "created_at": start,
        "session_type": "Running",
        "duration_minutes": 42,
        "avg_heart_rate": avg_hr,
        "unrelated": "preserve-me",
        "source": "apple_health",
        "apple_health": {
            "activity_type": "Running",
            "start": start,
        },
    }


def test_history_cross_source_merge_runtime_is_unique_and_collision_safe():
    payload = run_app_js(
        ["mergeOpenWearablesHistory", "mergeStrengthHistorySources", "historyCaloriesForDisplay"],
        """
const merge = e.mergeOpenWearablesHistory;
const base = { id: 'watch-1', source: 'watch', canonical_category: 'running', start: '2026-07-12T07:00:00.900-05:00', calories_burned: null, avg_heart_rate: 0, max_heart_rate: null, keep: 'yes' };
const ow = { id: 'open-wearables-1', source: 'open_wearables', canonical_category: 'tempo_run', activity_type: 'Tempo Run', session_type: 'running', start_time: '2026-07-12T12:00:00Z', calories_burned: 386.4, avg_heart_rate: 148, max_heart_rate: 176 };
const unique = merge([base], [ow]);
const ambiguous = merge([base, { ...base, id: 'watch-2' }], [ow]);
const startless = merge([{ ...base, start: null }], [{ ...ow, start_time: null }]);
const noImport = merge([{ ...base, calories_burned: 300, avg_heart_rate: 140, max_heart_rate: 170 }], [ow]);
const treadmillBase = { ...base, canonical_category: 'treadmill' };
const treadmillOw = { ...ow, canonical_category: 'treadmill', activity_type: 'Treadmill Run', session_type: 'running' };
const treadmill = merge([treadmillBase], [treadmillOw]);
const candidateCollision = merge([base, treadmillBase], [treadmillOw]);
const strengthAlias = merge([{ ...base, canonical_category: 'strength_training' }], [{ ...ow, canonical_category: 'functional_strength_training', activity_type: 'Functional Strength Training', session_type: 'functional strength training' }]);
const strengthWatch = { ...base, date: '2026-07-12', canonical_category: 'strength_training' };
const strengthOw = { ...ow, canonical_category: 'strength_training', session_type: 'strength training' };
const enrichedStrength = merge([strengthWatch], [strengthOw]);
const lift = (id) => ({ id, date: '2026-07-12', source: 'lifted' });
const strength = e.mergeStrengthHistorySources([lift('lift-1')], enrichedStrength);
const ambiguousStrength = e.mergeStrengthHistorySources([lift('lift-1'), lift('lift-2')], enrichedStrength);
const noImportStrength = e.mergeStrengthHistorySources([lift('lift-1')], [{ ...noImport[0], date: '2026-07-12', canonical_category: 'strength_training' }]);
const placeholderCalories = e.historyCaloriesForDisplay({ total_energy_kcal: 0, calories_burned: 386.4, open_wearables_metrics: true });
process.stdout.write(JSON.stringify({ unique, ambiguous, startless, noImport, treadmill, candidateCollision, strengthAlias, strength, ambiguousStrength, noImportStrength, placeholderCalories }));
""",
    )

    assert payload["unique"] == [{
        "id": "watch-1",
        "source": "watch",
        "canonical_category": "running",
        "start": "2026-07-12T07:00:00.900-05:00",
        "calories_burned": 386.4,
        "avg_heart_rate": 0,
        "max_heart_rate": 176,
        "keep": "yes",
        "open_wearables_match": True,
        "open_wearables_metrics": True,
    }]
    assert len(payload["ambiguous"]) == 3
    assert len(payload["startless"]) == 2
    assert payload["noImport"][0]["open_wearables_match"] is True
    assert payload["noImport"][0].get("open_wearables_metrics") is None
    assert len(payload["treadmill"]) == 1
    assert payload["treadmill"][0]["calories_burned"] == 386.4
    assert len(payload["candidateCollision"]) == 3
    assert len(payload["strengthAlias"]) == 1
    # One strength session counts once: the logged lift absorbs the matched Watch/OW row.
    assert len(payload["strength"]) == 1
    assert payload["strength"][0]["source"] == "lifted"
    assert "watch" in payload["strength"][0]["merged_sources"]
    # Two logged lifts that day: only the first absorbs the wearable row.
    assert [row["id"] for row in payload["ambiguousStrength"]] == ["lift-1", "lift-2"]
    assert len(payload["noImportStrength"]) == 1
    assert payload["placeholderCalories"] == 386.4


def _detail_scenario(item_json, extra_elements=""):
    return f"""
const el = () => ({{ innerHTML: '', textContent: '', hidden: true, addEventListener() {{}} }});
sandbox.elements['modal-workout-detail'] = el();
sandbox.elements['workout-detail-body'] = el();
sandbox.elements['workout-detail-title'] = el();
{extra_elements}
e.openWorkoutDetail({item_json});
"""


def test_matched_watch_rows_render_open_wearables_metrics_and_provenance():
    html = run_app_js(
        ["openWorkoutDetail"],
        _detail_scenario(
            "{ source: 'watch', date: '2026-07-12', activity_type: 'Run', duration_minutes: 42, total_energy_kcal: 0, calories_burned: 386, avg_heart_rate: 148, max_heart_rate: 176, open_wearables_metrics: true }"
        ) + "process.stdout.write(JSON.stringify(sandbox.elements['workout-detail-body'].innerHTML));",
    )

    assert "OPEN WEARABLES METRICS" in html
    assert "workout-detail-kpis-four" in html
    assert "386" in html and "148" in html and "176" in html
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text()
    assert ".workout-detail-kpis-four" in css


def test_setup_success_invalidates_workout_cache_and_failure_preserves_it():
    result = run_app_js(
        [
            "state", "getOpenWearablesWorkouts", "saveOpenWearablesSetup",
            "bootstrapOpenWearablesSetup", "syncOpenWearables",
        ],
        """
const noop = () => {};
let fail = false;
let workoutFetches = 0;
sandbox.__fitSet.api(async (path) => {
  if (fail) throw new Error('failed');
  if (path === '/api/open-wearables/workouts') { workoutFetches += 1; return { workouts: [{ id: 'fresh' }] }; }
  return { config: {}, open_wearables: { status: 'connected' }, status: 'connected' };
});
sandbox.__fitSet.renderOpenWearablesDetail(noop);
sandbox.__fitSet.setOpenWearablesSetupStatus(noop);
sandbox.__fitSet.populateOpenWearablesSetupFields(noop);
sandbox.__fitSet.deriveOpenWearablesSetupState(() => 'connected');
sandbox.__fitSet.openWearablesSetupCopy(() => '');
sandbox.__fitSet.toast(noop);
sandbox.__fitSet.renderOpenWearablesProviderActions(noop);
sandbox.__fitSet.readOpenWearablesSetupFields(() => ({}));
sandbox.__fitSet.openWearablesIsConnected(() => true);
sandbox.__fitSet.renderSettings(async () => {});
sandbox.__fitSet.getOpenWearablesStatus(async () => ({ status: 'connected' }));
const s = e.state;
const out = {};
s.open_wearables_workouts = [];
await e.saveOpenWearablesSetup();
out.saveInvalidated = s.open_wearables_workouts === null;
await e.getOpenWearablesWorkouts();
out.saveRefetched = workoutFetches === 1;
s.open_wearables_workouts = [{ id: 'stale' }];
await e.bootstrapOpenWearablesSetup();
out.bootstrapInvalidated = s.open_wearables_workouts === null;
await e.getOpenWearablesWorkouts();
out.bootstrapRefetched = workoutFetches === 2;
s.open_wearables_workouts = [{ id: 'stale' }];
await e.syncOpenWearables();
out.syncInvalidated = s.open_wearables_workouts === null;
fail = true;
s.open_wearables_workouts = [];
await e.saveOpenWearablesSetup();
out.saveFailurePreserved = Array.isArray(s.open_wearables_workouts) && s.open_wearables_workouts.length === 0;
s.open_wearables_workouts = [{ id: 'stale' }];
await e.bootstrapOpenWearablesSetup();
out.bootstrapFailurePreserved = s.open_wearables_workouts[0].id === 'stale';
s.open_wearables_workouts = [{ id: 'stale' }];
await e.syncOpenWearables();
out.syncFailurePreserved = s.open_wearables_workouts[0].id === 'stale';
// A failed workout fetch returns [] and is not cached, so the next load retries.
delete s.open_wearables_workouts;
out.failedFetch = await e.getOpenWearablesWorkouts();
out.failedFetchNotCached = !('open_wearables_workouts' in s);
process.stdout.write(JSON.stringify(out));
""",
        mocks=[
            "api", "renderOpenWearablesDetail", "setOpenWearablesSetupStatus",
            "populateOpenWearablesSetupFields", "deriveOpenWearablesSetupState",
            "openWearablesSetupCopy", "toast", "renderOpenWearablesProviderActions",
            "readOpenWearablesSetupFields", "openWearablesIsConnected", "renderSettings",
            "getOpenWearablesStatus",
        ],
    )

    assert result == {
        "saveInvalidated": True,
        "saveRefetched": True,
        "bootstrapInvalidated": True,
        "bootstrapRefetched": True,
        "syncInvalidated": True,
        "saveFailurePreserved": True,
        "bootstrapFailurePreserved": True,
        "syncFailurePreserved": True,
        "failedFetch": [],
        "failedFetchNotCached": True,
    }


def test_history_ui_contract_fetches_and_renders_open_wearables_metrics():
    result = run_app_js(
        ["openWorkoutDetail", "formatOptionalWorkoutMetric", "mergeStrengthHistorySources"],
        """
const el = () => ({ innerHTML: '', textContent: '', hidden: false, handlers: {}, addEventListener(type, fn) { this.handlers[type] = fn; } });
const modal = el(), body = el(), title = el();
sandbox.elements['modal-workout-detail'] = modal;
sandbox.elements['workout-detail-body'] = body;
sandbox.elements['workout-detail-title'] = title;
e.openWorkoutDetail({ id: 'open_wearables:w1', source: 'open_wearables', date: '2026-07-12', activity_type: 'Outdoor Run', provider_source: 'whoop', duration_minutes: 42, calories_burned: null, avg_heart_rate: 148, max_heart_rate: null });
process.stdout.write(JSON.stringify({
  html: body.innerHTML,
  title: title.textContent,
  missing: e.formatOptionalWorkoutMetric(null, 'kcal'),
  zero: e.formatOptionalWorkoutMetric(0, 'bpm'),
}));
""",
    )

    html = result["html"]
    assert "OPEN WEARABLES" in html
    assert "Average heart rate" in html and "Maximum heart rate" in html
    assert "workout-metrics-list" in html
    # Missing metrics render as an em dash, never as a fabricated zero.
    assert result["missing"] == "\u2014"
    assert result["zero"] == "0 bpm"
    assert "<dd>0 kcal</dd>" not in html
    assert "148 bpm" in html
    assert "Analyze" not in html and "analyze" not in html
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text()
    assert ".workout-metrics-list" in css


def test_open_wearables_history_row_escapes_hostile_labels():
    html = run_app_js(
        ["openWorkoutDetail"],
        _detail_scenario(
            "{ source: 'open_wearables', date: '2026-07-12', activity_type: 'Run', provider_source: '<img src=x onerror=alert(1)>', notes: '<script>x</script>' }",
        ) + "process.stdout.write(JSON.stringify(sandbox.elements['workout-detail-body'].innerHTML));",
    )

    assert "<img" not in html and "<script>" not in html


def test_logged_lift_shows_folded_open_wearables_metrics_once():
    result = run_app_js(
        [
            "normalizeWatchHistoryRow", "normalizeOpenWearablesHistoryRow",
            "mergeOpenWearablesHistory", "mergeStrengthHistorySources",
            "openWorkoutDetail", "loggedWearableMetricParts",
        ],
        """
const el = () => ({ innerHTML: '', textContent: '', hidden: true, addEventListener() {} });
sandbox.elements['modal-workout-detail'] = el();
sandbox.elements['workout-detail-body'] = el();
sandbox.elements['workout-detail-title'] = el();
const detail = (item) => { e.openWorkoutDetail(item); return sandbox.elements['workout-detail-body'].innerHTML; };
const lift = { id: 'lift-1', date: '2026-07-12', source: 'lifted', total_sets: 3 };
const watch = e.normalizeWatchHistoryRow({ id: 'aw-1', date: '2026-07-12', activity_type: 'Traditional Strength Training', start: '2026-07-12T07:00:00-05:00' });
const ow = (extra) => e.normalizeOpenWearablesHistoryRow({ id: 'open_wearables:w1', date: '2026-07-12', activity_type: 'strength_training',
  session_type: 'strength_training', start_time: '2026-07-12T12:00:00Z', ...extra });
const run = (watchRows, owRows, lifts = [lift]) => e.mergeStrengthHistorySources(lifts, e.mergeOpenWearablesHistory(watchRows, owRows));

const matched = run([watch], [ow({ calories_burned: 320, avg_heart_rate: 120, max_heart_rate: 160 })]);
const owOnly = run([], [ow({ calories_burned: 320, avg_heart_rate: 120, max_heart_rate: 160 })]);
const noMax = run([], [ow({ calories_burned: 320, avg_heart_rate: 120, max_heart_rate: null })]);
const noOw = run([watch], []);
// Two distinct wearable sessions fold into one lift: first fold target wins, never summed.
const second = e.normalizeOpenWearablesHistoryRow({ id: 'open_wearables:w2', date: '2026-07-12', activity_type: 'strength_training',
  session_type: 'strength_training', start_time: '2026-07-12T20:00:00Z', calories_burned: 100, avg_heart_rate: 90, max_heart_rate: 110 });
const several = run([], [ow({ calories_burned: 320, avg_heart_rate: 120, max_heart_rate: 160 }), second]);
process.stdout.write(JSON.stringify({
  matchedCount: matched.length, matchedRow: matched[0], matchedHtml: detail(matched[0]),
  owOnlyCount: owOnly.length, owOnlyRow: owOnly[0],
  noMaxParts: e.loggedWearableMetricParts(noMax[0]), noMaxHtml: detail(noMax[0]),
  noOwRow: noOw[0], noOwHtml: detail(noOw[0]),
  severalCount: several.length, severalRow: several[0],
}));
""",
    )

    assert result["matchedCount"] == 1
    assert result["matchedRow"]["wearable_calories_burned"] == 320
    assert result["matchedRow"]["wearable_avg_heart_rate"] == 120
    assert result["matchedRow"]["wearable_max_heart_rate"] == 160
    assert result["matchedRow"]["wearable_metrics_source"] == "Open Wearables"
    assert "OPEN WEARABLES" in result["matchedHtml"]
    assert "320 kcal" in result["matchedHtml"]
    assert "120 bpm" in result["matchedHtml"] and "160 bpm" in result["matchedHtml"]
    assert result["owOnlyCount"] == 1
    assert result["owOnlyRow"]["wearable_calories_burned"] == 320
    # Absent max HR is a dash, never 0.
    assert result["noMaxParts"] == ["320 kcal", "120 bpm avg", "— bpm max"]
    assert "<dd>— bpm max</dd>" in result["noMaxHtml"]
    assert "<dd>0" not in result["noMaxHtml"]
    # No Open Wearables data: nothing is invented on the logged row.
    assert "wearable_metrics_source" not in result["noOwRow"]
    assert "Average heart rate" not in result["noOwHtml"]
    # Several wearable rows: the lift takes the first session's metrics (never a sum); the
    # second distinct session stays visible as its own row.
    assert result["severalCount"] == 2
    assert result["severalRow"]["wearable_calories_burned"] == 320
    assert result["severalRow"]["wearable_avg_heart_rate"] == 120
    assert result["severalRow"]["wearable_max_heart_rate"] == 160


_OW_STRENGTH = (
    "e.normalizeOpenWearablesHistoryRow({ id: 'open_wearables:w1', date: '2026-07-12', "
    "activity_type: 'strength_training', session_type: 'strength_training', "
    "start_time: '2026-07-12T12:00:00Z', calories_burned: 320, avg_heart_rate: 120, max_heart_rate: 160 })"
)
_MERGE_EXPORTS = ["normalizeOpenWearablesHistoryRow", "mergeStrengthHistorySources"]


def test_two_logged_lifts_same_day_fold_the_session_but_get_no_wearable_metrics():
    rows = run_app_js(
        _MERGE_EXPORTS,
        f"""
const ow = {_OW_STRENGTH};
const lift = (id) => ({{ id, date: '2026-07-12', source: 'lifted' }});
const one = e.mergeStrengthHistorySources([lift('lift-am'), lift('lift-pm')], [ow]);
const two = e.mergeStrengthHistorySources([lift('lift-am'), lift('lift-pm')], [ow, {{ ...ow, id: 'open_wearables:w2' }}]);
const pick = (list) => list.map((r) => ({{ id: r.id, source: r.source, wearableSource: r.wearable_metrics_source || null, kcal: r.wearable_calories_burned ?? null }}));
process.stdout.write(JSON.stringify({{ one: pick(one), two: pick(two) }}));
""",
    )

    # The one session still counts once (folded into the first lift), without metrics.
    assert [row["id"] for row in rows["one"]] == ["lift-am", "lift-pm"]
    assert all(row["wearableSource"] is None and row["kcal"] is None for row in rows["one"])
    # Two sessions, two lifts: each lift absorbs one, so still two rows and no metrics by date alone.
    assert [row["id"] for row in rows["two"]] == ["lift-am", "lift-pm"]
    assert all(row["wearableSource"] is None for row in rows["two"])


def test_second_distinct_wearable_strength_session_same_day_stays_visible():
    rows = run_app_js(
        _MERGE_EXPORTS,
        f"""
const am = {_OW_STRENGTH};
const pm = {{ ...am, id: 'open_wearables:w2', start_time: '2026-07-12T23:00:00Z', calories_burned: 500, avg_heart_rate: 130, max_heart_rate: 170 }};
const out = e.mergeStrengthHistorySources([{{ id: 'lift-1', date: '2026-07-12', source: 'lifted' }}], [am, pm]);
process.stdout.write(JSON.stringify(out));
""",
    )

    assert [row["id"] for row in rows] == ["lift-1", "open_wearables:w2"]
    # The lift keeps the first session's metrics only; the 18:00 session keeps its own and is not summed in.
    assert rows[0]["wearable_calories_burned"] == 320
    assert rows[0]["wearable_avg_heart_rate"] == 120
    assert rows[1]["source"] == "open_wearables"
    assert rows[1]["calories_burned"] == 500


def _store_workout(module, *, provider="whoop", via="open_wearables", start="2026-07-12T12:00:00Z"):
    from wearable_fact_store import WearableDailyFact, upsert_daily_facts

    metrics = {
        "workout_duration": (42.0, "min"), "workout_active_calories": (386.4, "kcal"),
        "workout_avg_heart_rate": (148.0, "bpm"), "workout_max_heart_rate": (176.0, "bpm"),
    }
    upsert_daily_facts(module.WEARABLE_FACTS_DB_FILE, [
        WearableDailyFact(
            date=start[:10], provider_id=provider, source_label=provider, metric=metric, value=value, unit=unit,
            source_provider=provider, source_system=via, source_record_kind="event", source_id=f"{provider}-run-1",
            observed_at=start, original_label="Outdoor Run", category="cardio",
        )
        for metric, (value, unit) in metrics.items()
    ], profile_key=module._open_wearables_profile_key())


def test_open_wearables_workouts_endpoint_returns_only_normalized_stored_rows(isolated_app, monkeypatch):
    _store_workout(isolated_app)
    monkeypatch.setattr(
        isolated_app, "fetch_open_wearables_data",
        lambda **_: pytest.fail("History must read stored facts, never fetch live"),
    )

    response = isolated_app.app.test_client().get("/api/open-wearables/workouts")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 1
    row = payload["workouts"][0]
    assert row["source"] == "open_wearables"
    assert row["source_label"] == "WHOOP"
    assert row["activity_type"] == "Outdoor Run"
    assert (row["duration_minutes"], row["calories_burned"]) == (42.0, 386.4)
    assert (row["avg_heart_rate"], row["max_heart_rate"]) == (148.0, 176.0)
    assert row["archive"] is False
    assert "raw" not in json.dumps(payload)


def test_open_wearables_workouts_endpoint_is_empty_when_nothing_is_stored(isolated_app):
    response = isolated_app.app.test_client().get("/api/open-wearables/workouts")

    assert response.status_code == 200
    assert response.get_json() == {"workouts": [], "total": 0}


def test_open_wearables_workouts_endpoint_serves_stored_rows_during_an_outage_without_503(isolated_app, monkeypatch):
    _store_workout(isolated_app, provider="apple", via="archive", start="2025-03-01T17:30:00Z")

    monkeypatch.setattr(
        isolated_app, "fetch_open_wearables_data",
        lambda **_: pytest.fail("History must not call Open Wearables, up or down"),
    )

    response = isolated_app.app.test_client().get("/api/open-wearables/workouts")

    assert response.status_code == 200
    [row] = response.get_json()["workouts"]
    assert row["archive"] is True
    assert row["source_label"] == "Apple Health (archive)"


def test_stored_facts_and_history_rows_agree_on_the_local_date():
    # FIT-350 stored facts and FIT-382 rows must agree on the local date.
    assert hub._workout_local_date("2026-07-13T04:30:00Z", "-05:00") == "2026-07-12"
