"""FIT-382 contracts for Open Wearables per-workout effort metrics."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

import open_wearables_hub as hub


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit382-open-wearables-secret")
    module = importlib.import_module("app")
    original_workouts = list(module.WORKOUTS)
    original_lm_studio = getattr(module, "_lm_studio", None)
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    module.WORKOUTS.clear()
    monkeypatch.setattr(module, "_lm_studio", None)
    monkeypatch.setattr(module, "_load_apple_health_recommendation_workouts", lambda **_kwargs: [])
    yield module
    module.WORKOUTS[:] = original_workouts
    module._lm_studio = original_lm_studio
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


def test_extract_workouts_normalizes_exact_open_wearables_schema_without_raw_data():
    rows = hub.extract_workouts(_open_wearables_payload())

    assert rows == [_normalized_workout()]
    serialized = json.dumps(rows)
    assert "must-not-leak" not in serialized
    assert "access_token" not in serialized
    assert "raw" not in serialized


def test_extract_workouts_keeps_missing_metrics_null_and_supports_kilojoule_fallback():
    payload = _open_wearables_payload(include_max_hr=False)
    workout = payload["data"][0]
    workout.pop("calories_kcal")
    workout["kilojoules"] = 418.4

    row = hub.extract_workouts(payload)[0]

    assert row["calories_burned"] == pytest.approx(100.0)
    assert row["avg_heart_rate"] == 148
    assert row["max_heart_rate"] is None


def test_extract_workouts_uses_zone_offset_for_local_history_date():
    payload = _open_wearables_payload()
    workout = payload["data"][0]
    workout["start_time"] = "2026-07-13T04:30:00Z"
    workout["end_time"] = "2026-07-13T05:12:00Z"
    workout["zone_offset"] = "-05:00"

    row = hub.extract_workouts(payload)[0]

    assert row["date"] == "2026-07-12"
    assert "zone_offset" not in row


def test_workout_fetch_uses_utc_bounds_for_seven_local_calendar_days(fitness_app, monkeypatch):
    captured = {}
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 7, 13, 12, 0)
            return value.replace(tzinfo=tz) if tz else value

    monkeypatch.setenv("TZ", "America/Chicago")
    monkeypatch.setattr(fitness_app, "datetime", FrozenDateTime)
    monkeypatch.setattr(fitness_app, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(fitness_app, "_get_ow_token", lambda: "test-token")
    monkeypatch.setattr(fitness_app, "_open_wearables_user_base", lambda: "http://ow.test/user")
    def capture_request(url, **_kwargs):
        captured["url"] = url
        return {"data": [], "pagination": {"has_more": False, "next_cursor": None}}

    monkeypatch.setattr(fitness_app, "_ow_request", capture_request)

    result = fitness_app._fetch_open_wearables_workout_data()

    query = parse_qs(urlsplit(captured["url"]).query)
    assert query["start_date"] == ["2026-07-07T05:00:00Z"]
    assert query["end_date"] == ["2026-07-14T05:00:00Z"]
    assert datetime.fromisoformat("2026-07-14T01:30:00+00:00") < datetime.fromisoformat(
        query["end_date"][0].replace("Z", "+00:00")
    )
    assert result["errors"] == {}


def test_workout_fetch_falls_back_when_tz_key_is_malformed(fitness_app, monkeypatch):
    captured = {}
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 11, 2, 12, 0)
            return value.replace(tzinfo=tz) if tz else value

    monkeypatch.setenv("TZ", "../invalid")
    monkeypatch.setattr(fitness_app, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        fitness_app,
        "_system_local_timezone",
        lambda: ZoneInfo("America/Chicago"),
        raising=False,
    )
    monkeypatch.setattr(fitness_app, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(fitness_app, "_get_ow_token", lambda: "test-token")
    monkeypatch.setattr(fitness_app, "_open_wearables_user_base", lambda: "http://ow.test/user")
    def capture_request(url, **_kwargs):
        captured["url"] = url
        return {"data": [], "pagination": {"has_more": False, "next_cursor": None}}

    monkeypatch.setattr(
        fitness_app,
        "_ow_request", capture_request,
    )

    result = fitness_app._fetch_open_wearables_workout_data()

    query = parse_qs(urlsplit(captured["url"]).query)
    assert query["start_date"] == ["2026-10-27T05:00:00Z"]
    assert query["end_date"] == ["2026-11-03T06:00:00Z"]
    assert result == {"workouts": {"data": []}, "errors": {}}


def test_workout_fetch_follows_open_wearables_cursor_pagination(fitness_app, monkeypatch):
    urls = []
    monkeypatch.setattr(fitness_app, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(fitness_app, "_get_ow_token", lambda: "test-token")
    monkeypatch.setattr(fitness_app, "_open_wearables_user_base", lambda: "http://ow.test/user")

    def fake_request(url, **_kwargs):
        urls.append(url)
        if "cursor=" in url:
            return {
                "data": [{"id": "page-2"}],
                "pagination": {"has_more": False, "next_cursor": None},
            }
        return {
            "data": [{"id": "page-1"}],
            "pagination": {"has_more": True, "next_cursor": "next page"},
        }

    monkeypatch.setattr(fitness_app, "_ow_request", fake_request)

    result = fitness_app._fetch_open_wearables_workout_data()

    assert [row["id"] for row in result["workouts"]["data"]] == ["page-1", "page-2"]
    assert "limit=100" in urls[0]
    assert "cursor=next+page" in urls[1]


def test_workout_fetch_rejects_schema_invalid_success_page(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(fitness_app, "_get_ow_token", lambda: "test-token")
    monkeypatch.setattr(fitness_app, "_open_wearables_user_base", lambda: "http://ow.test/user")
    monkeypatch.setattr(
        fitness_app,
        "_ow_request",
        lambda _url, **_kwargs: {"pagination": {"has_more": False}},
    )

    result = fitness_app._fetch_open_wearables_workout_data()

    assert result["workouts"] is None
    assert "workouts" in result["errors"]


def test_open_wearables_workouts_endpoint_returns_only_normalized_rows(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_fetch_open_wearables_workout_data", lambda: {
        "workouts": _open_wearables_payload(),
        "errors": {},
    })

    response = fitness_app.app.test_client().get("/api/open-wearables/workouts")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 1
    row = payload["workouts"][0]
    assert row["source"] == "open_wearables"
    assert row["source_label"] == "Open Wearables"
    assert row["calories_burned"] == 386.4
    assert row["avg_heart_rate"] == 148
    assert row["max_heart_rate"] == 176
    assert "raw" not in json.dumps(payload)


def test_open_wearables_workouts_endpoint_is_empty_when_hub_is_not_configured(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_fetch_open_wearables_workout_data", lambda: {
        "workouts": None,
        "errors": {"config": "missing"},
    })

    response = fitness_app.app.test_client().get("/api/open-wearables/workouts")

    assert response.status_code == 200
    assert response.get_json() == {"workouts": [], "total": 0}


def test_open_wearables_workouts_endpoint_does_not_cache_configured_hub_failure(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "_fetch_open_wearables_workout_data", lambda: {
        "workouts": None,
        "errors": {"workouts": "timeout"},
    }, raising=False)

    response = fitness_app.app.test_client().get("/api/open-wearables/workouts")

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "open_wearables_workouts_unavailable"


def test_ai_history_context_includes_nullable_open_wearables_metrics(fitness_app, monkeypatch):
    row = _normalized_workout(max_hr=None)
    monkeypatch.setattr(fitness_app, "_load_open_wearables_workouts", lambda: [row], raising=False)

    history = fitness_app._ai_history_context()

    open_wearables = next(item for item in history if item["source"] == "open_wearables")
    assert open_wearables["calories_burned"] == 386.4
    assert open_wearables["avg_heart_rate"] == 148
    assert open_wearables["max_heart_rate"] is None


def test_cross_source_merge_uses_unique_utc_second_and_preserves_base_values(fitness_app):
    apple = _apple_health_workout(start="2026-07-12T07:00:00.900-05:00", avg_hr=0)
    open_wearables = {**_normalized_workout(), "activity_type": "Tempo Run"}

    merged = fitness_app._merge_open_wearables_history_rows([apple], [open_wearables])

    assert len(merged) == 1
    assert merged[0]["id"] == apple["id"]
    assert merged[0]["source"] == "apple_health"
    assert merged[0]["unrelated"] == "preserve-me"
    assert merged[0]["calories_burned"] == 386.4
    assert merged[0]["avg_heart_rate"] == 0
    assert merged[0]["max_heart_rate"] == 176


def test_cross_source_merge_leaves_ambiguous_and_startless_rows_unmerged(fitness_app):
    apple = _apple_health_workout()
    duplicate_apple = {**apple, "id": "apple-health:second"}
    open_wearables = _normalized_workout()

    ambiguous = fitness_app._merge_open_wearables_history_rows(
        [apple, duplicate_apple],
        [open_wearables],
    )
    startless = fitness_app._merge_open_wearables_history_rows(
        [{**apple, "created_at": None, "apple_health": {"activity_type": "Running"}}],
        [{**open_wearables, "start_time": None}],
    )

    assert [row["id"] for row in ambiguous] == [
        "apple-health:synthetic-id",
        "apple-health:second",
        open_wearables["id"],
    ]
    assert len(startless) == 2


def test_cross_source_merge_accepts_canonical_activity_candidate(fitness_app):
    apple = _apple_health_workout()
    apple["session_type"] = "Treadmill Run"
    apple["apple_health"]["activity_type"] = "Treadmill Run"
    open_wearables = {
        **_normalized_workout(),
        "activity_type": "Treadmill Run",
        "session_type": "running",
    }

    merged = fitness_app._merge_open_wearables_history_rows([apple], [open_wearables])

    assert len(merged) == 1
    assert merged[0]["calories_burned"] == 386.4


def test_ai_history_context_dedupes_unique_pair_and_keeps_ambiguous_collisions(
    fitness_app,
    monkeypatch,
):
    apple = _apple_health_workout()
    open_wearables = _normalized_workout()
    monkeypatch.setattr(fitness_app, "_load_apple_health_recommendation_workouts", lambda **_kwargs: [apple])
    monkeypatch.setattr(fitness_app, "_load_open_wearables_workouts", lambda: [open_wearables])

    unique_history = fitness_app._ai_history_context()

    assert len(unique_history) == 1
    assert unique_history[0]["source"] == "apple_health"
    assert unique_history[0]["calories_burned"] == 386.4
    assert unique_history[0]["avg_heart_rate"] == 148
    assert unique_history[0]["max_heart_rate"] == 176

    duplicate_apple = {**apple, "id": "apple-health:second"}
    monkeypatch.setattr(
        fitness_app,
        "_load_apple_health_recommendation_workouts",
        lambda **_kwargs: [apple, duplicate_apple],
    )

    ambiguous_history = fitness_app._ai_history_context()

    assert len(ambiguous_history) == 3
    assert sum(row["source"] == "apple_health" for row in ambiguous_history) == 2
    assert sum(row["source"] == "open_wearables" for row in ambiguous_history) == 1


def test_ai_history_context_applies_limit_after_combining_sources(fitness_app, monkeypatch):
    apple_rows = [
        _apple_health_workout(start=f"2026-01-{day:02d}T12:00:00Z")
        for day in range(1, 11)
    ]
    newest_open_wearables = {
        **_normalized_workout(),
        "date": "2026-07-14",
        "start_time": "2026-07-14T12:00:00Z",
    }
    monkeypatch.setattr(
        fitness_app,
        "_load_apple_health_recommendation_workouts",
        lambda **_kwargs: apple_rows,
    )
    monkeypatch.setattr(
        fitness_app,
        "_load_open_wearables_workouts",
        lambda: [newest_open_wearables],
    )

    history = fitness_app._ai_history_context(limit=5)

    assert len(history) == 5
    assert history[0]["id"] == newest_open_wearables["id"]


def test_history_cross_source_merge_runtime_is_unique_and_collision_safe():
    if not shutil.which("node"):
        pytest.skip("FIT-382 runtime regression requires node")
    js = (Path(__file__).resolve().parents[1] / "static" / "js" / "app.js").read_text()
    helper_block = "function historyWorkoutFingerprints" + js.split(
        "function historyWorkoutFingerprints", 1
    )[1].split("async function renderHistory", 1)[0]
    canonical_helper = "function canonicalHistoryCategory" + js.split(
        "function canonicalHistoryCategory", 1
    )[1].split("function normalizeWatchHistoryRow", 1)[0]
    calorie_helper = "function historyCaloriesForDisplay" + js.split(
        "function historyCaloriesForDisplay", 1
    )[1].split("function openWorkoutDetail", 1)[0]
    node_script = f"""
{helper_block}
{canonical_helper}
{calorie_helper}
const base = {{ id: 'watch-1', source: 'watch', canonical_category: 'running', start: '2026-07-12T07:00:00.900-05:00', calories_burned: null, avg_heart_rate: 0, max_heart_rate: null, keep: 'yes' }};
const ow = {{ id: 'open-wearables-1', source: 'open_wearables', canonical_category: 'tempo_run', activity_type: 'Tempo Run', session_type: 'running', start_time: '2026-07-12T12:00:00Z', calories_burned: 386.4, avg_heart_rate: 148, max_heart_rate: 176 }};
const unique = mergeOpenWearablesHistory([base], [ow]);
const ambiguous = mergeOpenWearablesHistory([base, {{ ...base, id: 'watch-2' }}], [ow]);
const startless = mergeOpenWearablesHistory([{{ ...base, start: null }}], [{{ ...ow, start_time: null }}]);
const noImport = mergeOpenWearablesHistory([{{ ...base, calories_burned: 300, avg_heart_rate: 140, max_heart_rate: 170 }}], [ow]);
const treadmillBase = {{ ...base, canonical_category: 'treadmill' }};
const treadmillOw = {{ ...ow, canonical_category: 'treadmill', activity_type: 'Treadmill Run', session_type: 'running' }};
const treadmill = mergeOpenWearablesHistory([treadmillBase], [treadmillOw]);
const candidateCollision = mergeOpenWearablesHistory([base, treadmillBase], [treadmillOw]);
const strengthAlias = mergeOpenWearablesHistory([{{ ...base, canonical_category: 'strength_training' }}], [{{ ...ow, canonical_category: 'functional_strength_training', activity_type: 'Functional Strength Training', session_type: 'functional strength training' }}]);
const strengthWatch = {{ ...base, date: '2026-07-12', canonical_category: 'strength_training' }};
const strengthOw = {{ ...ow, canonical_category: 'strength_training', session_type: 'strength training' }};
const enrichedStrength = mergeOpenWearablesHistory([strengthWatch], [strengthOw]);
const strength = mergeStrengthHistorySources([{{ id: 'lift-1', date: '2026-07-12', source: 'lifted' }}], enrichedStrength);
const ambiguousStrength = mergeStrengthHistorySources([{{ id: 'lift-1', date: '2026-07-12', source: 'lifted' }}, {{ id: 'lift-2', date: '2026-07-12', source: 'lifted' }}], enrichedStrength);
const noImportStrength = mergeStrengthHistorySources([{{ id: 'lift-1', date: '2026-07-12', source: 'lifted' }}], [{{ ...noImport[0], date: '2026-07-12', canonical_category: 'strength_training' }}]);
const placeholderCalories = historyCaloriesForDisplay({{ total_energy_kcal: 0, calories_burned: 386.4, open_wearables_metrics: true }});
console.log(JSON.stringify({{ unique, ambiguous, startless, noImport, treadmill, candidateCollision, strengthAlias, strength, ambiguousStrength, noImportStrength, placeholderCalories }}));
"""
    result = subprocess.run(
        ["node", "-e", node_script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

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
    assert len(payload["strength"]) == 2
    assert payload["strength"][0].get("open_wearables_metrics") is None
    assert payload["strength"][1]["open_wearables_metrics"] is True
    assert payload["strength"][1]["calories_burned"] == 386.4
    assert len(payload["ambiguousStrength"]) == 3
    assert payload["ambiguousStrength"][0].get("open_wearables_metrics") is None
    assert payload["ambiguousStrength"][1].get("open_wearables_metrics") is None
    assert payload["ambiguousStrength"][2]["open_wearables_metrics"] is True
    assert len(payload["noImportStrength"]) == 2
    assert payload["noImportStrength"][1]["open_wearables_match"] is True
    assert payload["placeholderCalories"] == 386.4


def test_matched_watch_rows_render_open_wearables_metrics_and_provenance():
    js = (Path(__file__).resolve().parents[1] / "static" / "js" / "app.js").read_text()
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text()

    assert "const kcal = historyCaloriesForDisplay(w)" in js
    assert "formatOptionalWorkoutMetric(w.max_heart_rate, 'bpm max'" in js
    assert "OPEN WEARABLES METRICS" in js
    assert "loggedMatches.length === 1 && watchMatches.length === 1" in js
    assert "!row.open_wearables_match" in js
    assert "workout-detail-kpis workout-detail-kpis-four" in js
    assert ".workout-detail-kpis-four" in css


def test_setup_success_invalidates_workout_cache_and_failure_preserves_it():
    if not shutil.which("node"):
        pytest.skip("FIT-382 cache regression requires node")
    js = (Path(__file__).resolve().parents[1] / "static" / "js" / "app.js").read_text()
    get_source = "async function getOpenWearablesWorkouts" + js.split(
        "async function getOpenWearablesWorkouts", 1
    )[1].split("async function getBody", 1)[0]
    bootstrap_source = "async function bootstrapOpenWearablesSetup" + js.split(
        "async function bootstrapOpenWearablesSetup", 1
    )[1].split("async function prepareOpenWearablesThenContinue", 1)[0]
    save_source = "async function saveOpenWearablesSetup" + js.split(
        "async function saveOpenWearablesSetup", 1
    )[1].split("function openOpenWearablesPortal", 1)[0]
    node_script = f"""
const state = {{ openWearablesUi: {{ bootstrapInFlight: false, saveInFlight: false, config: {{}}, providerActions: [] }}, openWearablesStatus: null, wearableSources: null, dashboard: {{}} }};
const DASHBOARD_FETCH_TIMEOUT_MS = 1000;
let fail = false;
let workoutFetches = 0;
async function api(path) {{
  if (fail) throw new Error('failed');
  if (path === '/api/open-wearables/workouts') {{ workoutFetches += 1; return {{ workouts: [{{ id: 'fresh' }}] }}; }}
  return {{ config: {{}}, open_wearables: {{ status: 'connected' }}, status: 'connected' }};
}}
const noop = () => {{}};
const renderOpenWearablesDetail = noop, setOpenWearablesSetupStatus = noop, populateOpenWearablesSetupFields = noop;
const deriveOpenWearablesSetupState = () => 'connected', openWearablesSetupCopy = () => '', toast = noop;
const renderOpenWearablesProviderActions = noop, readOpenWearablesSetupFields = () => ({{}}), openWearablesIsConnected = () => true;
const renderSettings = async () => {{}}, getOpenWearablesStatus = async () => ({{ status: 'connected' }});
const $ = () => ({{ disabled: false }});
{get_source}
{bootstrap_source}
{save_source}
(async () => {{
  state.open_wearables_workouts = [];
  await saveOpenWearablesSetup();
  const saveInvalidated = state.open_wearables_workouts === null;
  await getOpenWearablesWorkouts();
  const saveRefetched = workoutFetches === 1;
  state.open_wearables_workouts = [{{ id: 'stale' }}];
  await bootstrapOpenWearablesSetup();
  const bootstrapInvalidated = state.open_wearables_workouts === null;
  await getOpenWearablesWorkouts();
  const bootstrapRefetched = workoutFetches === 2;
  fail = true;
  state.open_wearables_workouts = [];
  await saveOpenWearablesSetup();
  const saveFailurePreserved = Array.isArray(state.open_wearables_workouts) && state.open_wearables_workouts.length === 0;
  state.open_wearables_workouts = [{{ id: 'stale' }}];
  await bootstrapOpenWearablesSetup();
  const bootstrapFailurePreserved = state.open_wearables_workouts[0].id === 'stale';
  console.log(JSON.stringify({{ saveInvalidated, saveRefetched, bootstrapInvalidated, bootstrapRefetched, saveFailurePreserved, bootstrapFailurePreserved }}));
}})();
"""
    result = subprocess.run(
        ["node", "-e", node_script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "saveInvalidated": True,
        "saveRefetched": True,
        "bootstrapInvalidated": True,
        "bootstrapRefetched": True,
        "saveFailurePreserved": True,
        "bootstrapFailurePreserved": True,
    }


def test_analyze_open_wearables_workout_carries_metrics_without_fabrication(fitness_app, monkeypatch):
    row = _normalized_workout(max_hr=None)
    monkeypatch.setattr(fitness_app, "_load_open_wearables_workouts", lambda: [row], raising=False)

    response = fitness_app.app.test_client().post(
        "/api/workout/analyze",
        json={"workout_id": row["id"]},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["workout"]["source"] == "open_wearables"
    assert payload["workout"]["calories_burned"] == 386.4
    assert payload["workout"]["avg_heart_rate"] == 148
    assert payload["workout"]["max_heart_rate"] is None
    assert payload["context_used"]["open_wearables_metrics"] == {
        "calories_burned": 386.4,
        "avg_heart_rate": 148,
        "max_heart_rate": None,
    }


def test_analyze_open_wearables_workout_passes_metrics_to_model_context(fitness_app, monkeypatch):
    row = _normalized_workout()
    captured = {}

    class FakeLmStudio:
        LM_STUDIO_MODEL_VERSION = "fit382-test-model"
        ANALYZE_PROMPT_VERSION = "fit382-test-prompt"

        class LmStudioError(Exception):
            pass

        @staticmethod
        def analyze_workout(target, context):
            captured["target"] = target
            captured["context"] = context
            return {
                "summary": "Source-backed workout summary.",
                "wins": [],
                "concerns": [],
                "comparison": "No comparison.",
                "next_session_cue": "Keep the next session steady.",
                "_meta": {},
            }

    monkeypatch.setattr(fitness_app, "_load_open_wearables_workouts", lambda: [row], raising=False)
    monkeypatch.setattr(fitness_app, "_lm_studio", FakeLmStudio())
    monkeypatch.setattr(fitness_app, "_ai_cache_get", lambda _key: None)
    monkeypatch.setattr(fitness_app, "_ai_cache_put", lambda _key, _payload: None)

    response = fitness_app.app.test_client().post(
        "/api/workout/analyze",
        json={"workout_id": row["id"]},
    )

    assert response.status_code == 200
    assert captured["target"]["source"] == "open_wearables"
    assert captured["context"]["open_wearables_metrics"] == {
        "calories_burned": 386.4,
        "avg_heart_rate": 148,
        "max_heart_rate": 176,
    }


def test_open_wearables_analysis_cache_key_includes_workout_identity(fitness_app, monkeypatch):
    first = _normalized_workout(max_hr=None)
    first.update({"calories_burned": None, "avg_heart_rate": None})
    second = {
        **first,
        "id": "open_wearables:second-workout",
        "external_id": "second-workout",
        "activity_type": "Cycling",
        "session_type": "cycling",
        "duration_minutes": 75,
    }
    cache_keys = []

    class FakeLmStudio:
        LM_STUDIO_MODEL_VERSION = "fit382-cache-model"
        ANALYZE_PROMPT_VERSION = "fit382-cache-prompt"

        class LmStudioError(Exception):
            pass

        @staticmethod
        def analyze_workout(target, _context):
            return {
                "summary": target["id"],
                "wins": [],
                "concerns": [],
                "comparison": "No comparison.",
                "next_session_cue": "Keep steady.",
                "_meta": {},
            }

    monkeypatch.setattr(fitness_app, "_load_open_wearables_workouts", lambda: [first, second])
    monkeypatch.setattr(fitness_app, "_lm_studio", FakeLmStudio())
    monkeypatch.setattr(fitness_app, "_ai_cache_get", lambda _key: None)
    monkeypatch.setattr(fitness_app, "_ai_cache_put", lambda key, _payload: cache_keys.append(key))

    client = fitness_app.app.test_client()
    assert client.post("/api/workout/analyze", json={"workout_id": first["id"]}).status_code == 200
    assert client.post("/api/workout/analyze", json={"workout_id": second["id"]}).status_code == 200

    assert len(cache_keys) == 2
    assert cache_keys[0] != cache_keys[1]


def test_logged_workout_analysis_response_does_not_add_empty_wearable_fields(fitness_app):
    payload = fitness_app._workout_analysis_response(
        {"id": "logged-1", "date": "2026-07-12", "source": "lifted"},
        {"summary": "Logged workout", "wins": [], "concerns": []},
        [],
        {},
        None,
        {"set_note_count": 0, "workout_notes_present": False, "cardio_notes_present": False},
    )

    assert "source" not in payload["workout"]
    assert "calories_burned" not in payload["workout"]
    assert "open_wearables_metrics" not in payload["context_used"]


def test_history_ui_contract_fetches_and_renders_open_wearables_metrics():
    root = Path(__file__).resolve().parents[1]
    js = (root / "static" / "js" / "app.js").read_text()
    css = (root / "static" / "css" / "style.css").read_text()

    assert "getOpenWearablesWorkouts" in js
    assert "OPEN WEARABLES" in js
    assert "calories_burned" in js
    assert "avg_heart_rate" in js
    assert "max_heart_rate" in js
    assert "Average heart rate" in js
    assert "Maximum heart rate" in js
    assert "formatOptionalWorkoutMetric" in js
    assert "workout-metrics-list" in js
    assert ".workout-metrics-list" in css

    workout_fetch = js.split("async function getOpenWearablesWorkouts", 1)[1].split(
        "async function getBody", 1
    )[0]
    assert "delete state[key]" in workout_fetch
    sync_handler = js.split("async function syncOpenWearables", 1)[1].split(
        "async function askAiFactQuestion", 1
    )[0]
    assert "state.open_wearables_workouts = null" in sync_handler

    open_wearables_detail = js.split("if (item.source === 'open_wearables')", 1)[1].split(
        "if (item.source === 'watch')", 1
    )[0]
    assert "|| 0" not in open_wearables_detail
    analyze_handler = open_wearables_detail.split("analyzeBtn.addEventListener('click'", 1)[1]
    assert analyze_handler.index("modal.hidden = true") < analyze_handler.index("openAnalyzeModal(")
