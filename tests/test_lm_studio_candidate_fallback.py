"""Regression tests for LM Studio primary/fallback candidate routing."""
from __future__ import annotations

import importlib
import json


def _adapter():
    return importlib.import_module("lm_studio_adapter")


def _candidates():
    return [
        {
            "role": "primary",
            "name": "primary",
            "url": "http://primary.test",
            "model": "missing-primary-model",
        },
        {
            "role": "fallback",
            "name": "fallback",
            "url": "http://fallback.test",
            "model": "loaded-fallback-model",
        },
    ]


def test_completion_json_sends_candidate_model_when_primary_is_missing(monkeypatch):
    adapter = _adapter()
    attempts = []

    def fake_preflight(candidate):
        if candidate["role"] == "primary":
            raise adapter.LmStudioError("model not loaded: missing-primary-model")

    def fake_post(candidate, path, payload, timeout):
        attempts.append(
            {
                "role": candidate["role"],
                "candidate_model": candidate["model"],
                "payload_model": payload["model"],
            }
        )
        content = json.dumps(
            {
                "summary": "Reduced shoulder loading because you said you were sore.",
                "intent": {
                    "avoid_muscles": ["shoulders"],
                    "avoid_joints": [],
                    "swap": [],
                    "rpe_delta": -0.5,
                    "sets_delta_pct": -10,
                    "duration_cap_min": 0,
                    "drop_cardio": False,
                },
            }
        )
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(adapter, "_lm_candidates", _candidates)
    monkeypatch.setattr(adapter, "_preflight_candidate", fake_preflight)
    monkeypatch.setattr(adapter, "_post_json_to", fake_post)

    parsed = adapter._completion_json(
        "/v1/chat/completions",
        {"model": "missing-primary-model", "messages": []},
        timeout=1,
        validate=adapter._validate_adjust_intent,
    )

    assert attempts == [
        {
            "role": "fallback",
            "candidate_model": "loaded-fallback-model",
            "payload_model": "loaded-fallback-model",
        }
    ]
    assert parsed["_meta"]["role"] == "fallback"
    assert parsed["_meta"]["model"] == "loaded-fallback-model"
    assert parsed["_meta"]["fallback_used"] is True
    assert "primary: model not loaded" in parsed["_meta"]["attempt_errors"][0]


def test_active_model_version_uses_loaded_fallback_when_primary_is_missing(monkeypatch):
    adapter = _adapter()

    def fake_preflight(candidate):
        if candidate["role"] == "primary":
            raise adapter.LmStudioError("model not loaded: missing-primary-model")

    monkeypatch.setattr(adapter, "_lm_candidates", _candidates)
    monkeypatch.setattr(adapter, "_preflight_candidate", fake_preflight)

    assert adapter.active_model_version() == "loaded-fallback-model"


def test_completion_json_reuses_preflighted_fallback_candidate(monkeypatch):
    adapter = _adapter()
    preflighted = _candidates()[1]
    preflight_calls = []

    def fake_preflight(candidate):
        preflight_calls.append(candidate["role"])

    def fake_post(candidate, path, payload, timeout):
        content = json.dumps(
            {
                "summary": "Kept the plan conservative for soreness.",
                "intent": {
                    "avoid_muscles": [],
                    "avoid_joints": [],
                    "swap": [],
                    "rpe_delta": -0.5,
                    "sets_delta_pct": -10,
                    "duration_cap_min": 0,
                    "drop_cardio": False,
                },
            }
        )
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(adapter, "_lm_candidates", _candidates)
    monkeypatch.setattr(adapter, "_preflight_candidate", fake_preflight)
    monkeypatch.setattr(adapter, "_post_json_to", fake_post)

    parsed = adapter._completion_json(
        "/v1/chat/completions",
        {"model": "missing-primary-model", "messages": []},
        timeout=1,
        validate=adapter._validate_adjust_intent,
        preflighted_candidate=preflighted,
    )

    assert preflight_calls == []
    assert parsed["_meta"]["role"] == "fallback"
    assert parsed["_meta"]["model_version"] == "loaded-fallback-model"


def test_adjust_route_succeeds_and_logs_fallback_model_version(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    fallback_candidate = _candidates()[1]
    metrics = []
    cache_puts = []

    class FakeAdapter:
        LM_STUDIO_MODEL_VERSION = "missing-primary-model"

        class LmStudioError(Exception):
            pass

        def active_candidate(self):
            return fallback_candidate

        def model_version_for(self, candidate):
            return (candidate.get("model") or "").split("/")[-1] if candidate else self.LM_STUDIO_MODEL_VERSION

        def adjust_plan(self, recommendation, constraint, readiness=None, timeout=None, preflighted_candidate=None):
            assert preflighted_candidate == fallback_candidate
            return {
                "summary": "Kept the plan conservative for soreness.",
                "intent": {
                    "avoid_muscles": [],
                    "avoid_joints": [],
                    "swap": [],
                    "rpe_delta": -0.5,
                    "sets_delta_pct": -10,
                    "duration_cap_min": 0,
                    "drop_cardio": False,
                },
                "_meta": {
                    "model": "loaded-fallback-model",
                    "model_version": "loaded-fallback-model",
                    "role": "fallback",
                    "fallback_used": True,
                    "elapsed_ms": 25,
                },
            }

    monkeypatch.setattr(module, "_lm_studio", FakeAdapter(), raising=False)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", {
        "id": "fit-69-route",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 45,
        "exercises": [],
    })
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "_ai_cache_get", lambda _key: None)
    monkeypatch.setattr(module, "_ai_cache_put", lambda key, payload: cache_puts.append((key, payload)))
    monkeypatch.setattr(module, "_ai_metric_log", lambda *args, **kwargs: metrics.append((args, kwargs)))

    response = module.app.test_client().post("/api/workout/adjust", json={"constraint": "Sore"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["meta"]["model_version"] == "loaded-fallback-model"
    assert body["meta"]["fallback_used"] is True
    assert cache_puts and cache_puts[0][1]["meta"]["model_version"] == "loaded-fallback-model"
    assert metrics[-1][0] == ("ok",)
    assert metrics[-1][1]["model_version"] == "loaded-fallback-model"


def test_adjust_route_replaces_model_summary_when_swap_is_skipped(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    candidate = _candidates()[0]

    class FakeAdapter:
        LM_STUDIO_MODEL_VERSION = "missing-primary-model"

        class LmStudioError(Exception):
            pass

        def active_candidate(self):
            return candidate

        def model_version_for(self, selected):
            return selected["model"]

        def model_versions_after(self, _selected):
            return []

        def adjust_plan(self, *_args, **_kwargs):
            return {
                "summary": "Swapped Seated Row for Lat Pulldown.",
                "intent": {
                    "swap": [
                        {
                            "replace_exercise": "Seated Row",
                            "target_muscle": "back",
                            "target_exercise": "Lat Pulldown",
                        }
                    ]
                },
                "_meta": {"model_version": "missing-primary-model"},
            }

    recommendation = {
        "id": "fit-265-summary",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 45,
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "target_sets": 3,
                "target_reps": 10,
                "target_weight": 100,
                "rpe_target": 7,
            }
        ],
    }
    monkeypatch.setattr(module, "_lm_studio", FakeAdapter(), raising=False)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", recommendation)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "_ai_cache_get", lambda _key: None)
    monkeypatch.setattr(module, "_ai_cache_put", lambda *_args: None)
    monkeypatch.setattr(module, "_ai_metric_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda plan, _fingerprint: plan)

    response = module.app.test_client().post(
        "/api/workout/adjust", json={"constraint": "replace seated row with lat pulldown"}
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["result_kind"] == "unchanged"
    assert body["applied_notes"] == []
    assert body["skipped_notes"] == ["could not locate 'Seated Row' in current plan"]
    assert body["summary"] == "The requested adjustment could not be applied to the current plan."


def test_adjust_route_skips_generation_when_no_candidate_preflights(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    metrics = []

    class FakeAdapter:
        LM_STUDIO_MODEL_VERSION = "missing-primary-model"

        class LmStudioError(Exception):
            pass

        def active_candidate(self):
            return None

        def model_version_for(self, candidate):
            return self.LM_STUDIO_MODEL_VERSION

        def fallback_model_versions(self):
            return ["loaded-fallback-model"]

        def adjust_plan(self, *args, **kwargs):
            raise AssertionError("adjust_plan should not run after all preflights fail")

    monkeypatch.setattr(module, "_lm_studio", FakeAdapter(), raising=False)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", {
        "id": "fit-69-all-down",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 45,
        "exercises": [],
    })
    monkeypatch.setattr(module, "_ai_cache_get", lambda _key: None)
    monkeypatch.setattr(module, "_ai_metric_log", lambda *args, **kwargs: metrics.append((args, kwargs)))

    response = module.app.test_client().post("/api/workout/adjust", json={"constraint": "Sore"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "fallback"
    assert body["reason"] == "LM Studio: all endpoints unavailable"
    assert metrics[-1][0] == ("fallback",)
    assert metrics[-1][1]["model_version"] == "missing-primary-model"
    assert metrics[-1][1]["reason"] == "preflight: all endpoints unavailable"


def test_adjust_route_reads_fallback_cache_when_all_candidates_are_down(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    metrics = []
    cache_versions = []
    cached_payload = {
        "status": "ok",
        "result_kind": "changed",
        "recommendation": {"id": "cached-fallback-while-down"},
        "summary": "Cached fallback response.",
        "applied_notes": ["Adjusted for soreness"],
        "constraint": "Sore",
        "meta": {
            "model_version": "loaded-fallback-model",
            "role": "fallback",
            "fallback_used": True,
        },
        "cache_hit": False,
    }

    class FakeAdapter:
        LM_STUDIO_MODEL_VERSION = "missing-primary-model"

        class LmStudioError(Exception):
            pass

        def active_candidate(self):
            return None

        def model_version_for(self, candidate):
            return self.LM_STUDIO_MODEL_VERSION

        def fallback_model_versions(self):
            return ["loaded-fallback-model"]

        def adjust_plan(self, *args, **kwargs):
            raise AssertionError("fallback cache hit should avoid generation")

    def fake_cache_key(_recommendation, _constraint, _readiness_date, model_version, _equipment_pref):
        cache_versions.append(model_version)
        return f"key-{model_version}"

    def fake_cache_get(key):
        return dict(cached_payload) if key == "key-loaded-fallback-model" else None

    monkeypatch.setattr(module, "_lm_studio", FakeAdapter(), raising=False)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", {
        "id": "fit-69-all-down-cache",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 45,
        "exercises": [],
    })
    monkeypatch.setattr(module, "_ai_cache_key", fake_cache_key)
    monkeypatch.setattr(module, "_ai_cache_get", fake_cache_get)
    monkeypatch.setattr(module, "_ai_metric_log", lambda *args, **kwargs: metrics.append((args, kwargs)))

    response = module.app.test_client().post("/api/workout/adjust", json={"constraint": "Sore"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["cache_hit"] is True
    assert body["meta"]["model_version"] == "loaded-fallback-model"
    assert cache_versions == ["missing-primary-model", "loaded-fallback-model"]
    assert metrics[-1][0] == ("cache_hit",)
    assert metrics[-1][1]["model_version"] == "loaded-fallback-model"
    assert module.LAST_WORKOUT_RECOMMENDATION == {"id": "cached-fallback-while-down"}


def test_adjust_route_caches_actual_model_when_completion_falls_back(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    primary_candidate = _candidates()[0]
    cache_versions = []
    cache_puts = []

    class FakeAdapter:
        LM_STUDIO_MODEL_VERSION = "missing-primary-model"

        class LmStudioError(Exception):
            pass

        def active_candidate(self):
            return primary_candidate

        def model_version_for(self, candidate):
            return (candidate.get("model") or "").split("/")[-1] if candidate else self.LM_STUDIO_MODEL_VERSION

        def model_versions_after(self, candidate):
            return ["loaded-fallback-model"] if candidate == primary_candidate else []

        def adjust_plan(self, recommendation, constraint, readiness=None, timeout=None, preflighted_candidate=None):
            assert preflighted_candidate == primary_candidate
            return {
                "summary": "Fallback model handled the soreness request.",
                "intent": {
                    "avoid_muscles": [],
                    "avoid_joints": [],
                    "swap": [],
                    "rpe_delta": -0.5,
                    "sets_delta_pct": -10,
                    "duration_cap_min": 0,
                    "drop_cardio": False,
                },
                "_meta": {
                    "model": "loaded-fallback-model",
                    "model_version": "loaded-fallback-model",
                    "role": "fallback",
                    "fallback_used": True,
                    "elapsed_ms": 25,
                },
            }

    def fake_cache_key(_recommendation, _constraint, _readiness_date, model_version, _equipment_pref):
        cache_versions.append(model_version)
        return f"key-{model_version}"

    monkeypatch.setattr(module, "_lm_studio", FakeAdapter(), raising=False)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", {
        "id": "fit-69-rekey",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 45,
        "exercises": [],
    })
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "_ai_cache_key", fake_cache_key)
    monkeypatch.setattr(module, "_ai_cache_get", lambda _key: None)
    monkeypatch.setattr(module, "_ai_cache_put", lambda key, payload: cache_puts.append((key, payload)))
    monkeypatch.setattr(module, "_ai_metric_log", lambda *args, **kwargs: None)

    response = module.app.test_client().post("/api/workout/adjust", json={"constraint": "Sore"})

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert cache_versions == ["missing-primary-model", "loaded-fallback-model", "loaded-fallback-model"]
    assert len(cache_puts) == 1
    assert cache_puts[0][0] == "key-loaded-fallback-model"
    assert cache_puts[0][1]["meta"]["model_version"] == "loaded-fallback-model"


def test_adjust_route_reads_fallback_cache_when_primary_generation_has_fallen_back(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    primary_candidate = _candidates()[0]
    metrics = []
    cache_versions = []
    cached_payload = {
        "status": "ok",
        "result_kind": "changed",
        "recommendation": {"id": "cached-fallback-plan"},
        "summary": "Cached fallback response.",
        "applied_notes": ["Adjusted for soreness"],
        "constraint": "Sore",
        "meta": {
            "model_version": "loaded-fallback-model",
            "role": "fallback",
            "fallback_used": True,
        },
        "cache_hit": False,
    }

    class FakeAdapter:
        LM_STUDIO_MODEL_VERSION = "missing-primary-model"

        class LmStudioError(Exception):
            pass

        def active_candidate(self):
            return primary_candidate

        def model_version_for(self, candidate):
            return (candidate.get("model") or "").split("/")[-1] if candidate else self.LM_STUDIO_MODEL_VERSION

        def model_versions_after(self, candidate):
            return ["loaded-fallback-model"] if candidate == primary_candidate else []

        def adjust_plan(self, *args, **kwargs):
            raise AssertionError("fallback cache hit should avoid generation")

    def fake_cache_key(_recommendation, _constraint, _readiness_date, model_version, _equipment_pref):
        cache_versions.append(model_version)
        return f"key-{model_version}"

    def fake_cache_get(key):
        return dict(cached_payload) if key == "key-loaded-fallback-model" else None

    monkeypatch.setattr(module, "_lm_studio", FakeAdapter(), raising=False)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", {
        "id": "fit-69-secondary-cache",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 45,
        "exercises": [],
    })
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda: None)
    monkeypatch.setattr(module, "_ai_cache_key", fake_cache_key)
    monkeypatch.setattr(module, "_ai_cache_get", fake_cache_get)
    monkeypatch.setattr(module, "_ai_cache_put", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_ai_metric_log", lambda *args, **kwargs: metrics.append((args, kwargs)))

    response = module.app.test_client().post("/api/workout/adjust", json={"constraint": "Sore"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["cache_hit"] is True
    assert body["meta"]["model_version"] == "loaded-fallback-model"
    assert cache_versions == ["missing-primary-model", "loaded-fallback-model"]
    assert metrics[-1][0] == ("cache_hit",)
    assert metrics[-1][1]["model_version"] == "loaded-fallback-model"
    assert module.LAST_WORKOUT_RECOMMENDATION == {"id": "cached-fallback-plan"}
