"""FIT-382 contracts for Open Wearables per-workout effort metrics."""

from __future__ import annotations

import importlib
import json
from datetime import timedelta
from pathlib import Path

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


def test_workout_fetch_uses_tomorrow_as_exclusive_end_bound(fitness_app, monkeypatch):
    captured = {}
    monkeypatch.setattr(fitness_app, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(fitness_app, "_get_ow_token", lambda: "test-token")
    monkeypatch.setattr(fitness_app, "_open_wearables_user_base", lambda: "http://ow.test/user")
    def capture_request(url, **_kwargs):
        captured["url"] = url
        return {"data": [], "pagination": {"has_more": False, "next_cursor": None}}

    monkeypatch.setattr(fitness_app, "_ow_request", capture_request)

    result = fitness_app._fetch_open_wearables_workout_data()

    today = fitness_app.datetime.now().date()
    assert f"start_date={(today - timedelta(days=6)).isoformat()}" in captured["url"]
    assert f"end_date={(today + timedelta(days=1)).isoformat()}" in captured["url"]
    assert result["errors"] == {}


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
