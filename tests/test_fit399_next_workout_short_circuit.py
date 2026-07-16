from __future__ import annotations

import importlib

import data_store
import pytest


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit399-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_OWNER", None)
    return module, module.app.test_client()


def _plan():
    return {
        "id": "fit399-cached-plan",
        "focus": "upper",
        "estimated_minutes": 45,
        "exercises": [{"machine": "Chest Press", "target_sets": 3, "target_reps": 10}],
    }


def _deterministic_route(monkeypatch, module):
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "RECOVERY_DATA", [])
    monkeypatch.setattr(module, "USER_SETTINGS", {})
    monkeypatch.setattr(module, "_current_workout_training_recommendation", lambda: "strength")
    monkeypatch.setattr(module, "_open_wearables_recommendation_facts", lambda: {})
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, _facts: (recommendation, None),
    )
    monkeypatch.setattr(module, "_food_log_entries_for_workout_adaptation", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "_nutrition_context_for_date", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "_workout_looks_hard", lambda *_a, **_kw: False)
    monkeypatch.setattr(module, "get_oura_daily", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "_compute_data_freshness", lambda: {})
    monkeypatch.setattr(module, "_whoop_recommendation_context", lambda *_a, **_kw: {})
    monkeypatch.setattr(
        module,
        "_wearable_adjusted_for_display",
        lambda plan, *_a, **_kw: {"next_workout": plan, "load_source": None},
    )
    monkeypatch.setattr(module, "_recommendation_sources_payload", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "_wearable_sources_payload", lambda *_a, **_kw: {})


def _owned(module, plan, user_id=1, day="2026-05-24"):
    module.LAST_WORKOUT_RECOMMENDATION = plan
    module.LAST_WORKOUT_RECOMMENDATION_OWNER = {
        "user_id": user_id,
        "fingerprint": "cached-fingerprint",
        "plan_id": id(plan),
        "day": day,
    }


def _zero_due(monkeypatch, module, value=False):
    calls = []

    def probe(user_id, *, now_iso):
        calls.append((user_id, now_iso))
        return value

    monkeypatch.setattr(module, "has_due_workout_adaptation_pending", probe)
    return calls


def test_zero_due_owned_cached_plan_skips_fingerprint(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _deterministic_route(monkeypatch, module)
    plan = _plan()
    _owned(module, plan)
    due_calls = _zero_due(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "_workout_recommendation_fingerprint",
        lambda: (_ for _ in ()).throw(AssertionError("fingerprint should be lazy")),
    )

    response = client.get("/api/next-workout?active_workout_open=false")

    assert response.status_code == 200
    assert response.get_json()["next_workout"]["id"] == "fit399-cached-plan"
    assert len(due_calls) == 1


def test_fast_hit_matches_legacy_payload(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _deterministic_route(monkeypatch, module)
    plan = _plan()
    _owned(module, plan)
    _zero_due(monkeypatch, module)
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: pytest.fail("lazy"))
    fast_response = client.get("/api/next-workout?active_workout_open=false")

    module.LAST_WORKOUT_RECOMMENDATION = plan
    module.LAST_WORKOUT_RECOMMENDATION_OWNER = None
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "legacy-fingerprint")
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda _fingerprint: plan)
    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", lambda plan, **_kw: (plan, []))
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda plan, _fingerprint: plan)
    legacy_response = client.get("/api/next-workout?active_workout_open=false")

    assert fast_response.status_code == legacy_response.status_code == 200
    assert fast_response.get_json() == legacy_response.get_json()


def test_fast_hit_skips_resolver_generation_adaptation_deepcopy_and_persist(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _deterministic_route(monkeypatch, module)
    plan = _plan()
    _owned(module, plan)
    _zero_due(monkeypatch, module)
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: pytest.fail("fingerprint"))
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda *_a, **_kw: pytest.fail("resolver"))
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_kw: pytest.fail("generator"))
    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", lambda *_a, **_kw: pytest.fail("adaptation"))
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda *_a, **_kw: pytest.fail("persist"))
    monkeypatch.setattr(module.copy, "deepcopy", lambda *_a, **_kw: pytest.fail("deepcopy"))

    response = client.get("/api/next-workout?active_workout_open=false")

    assert response.status_code == 200


@pytest.mark.parametrize("case", ["global_none", "persisted_only", "invalidated", "unowned", "owner_mismatch", "other_user", "lightweight"])
def test_zero_due_nonqualifying_cache_uses_legacy_path_without_leak(monkeypatch, tmp_path, case):
    module, client = _client(monkeypatch, tmp_path)
    _deterministic_route(monkeypatch, module)
    cached = _plan()
    fallback = {**_plan(), "id": "legacy-plan"}
    if case != "global_none" and case != "persisted_only":
        module.LAST_WORKOUT_RECOMMENDATION = cached
    if case == "invalidated":
        module.LAST_WORKOUT_RECOMMENDATION_OWNER = {"user_id": 1, "plan_id": id({})}
    elif case == "unowned":
        module.LAST_WORKOUT_RECOMMENDATION_OWNER = None
    elif case == "owner_mismatch":
        module.LAST_WORKOUT_RECOMMENDATION_OWNER = {"user_id": 1, "plan_id": id({})}
    elif case == "other_user":
        module.LAST_WORKOUT_RECOMMENDATION_OWNER = {"user_id": 1, "plan_id": id(cached)}
        monkeypatch.setattr(module, "_current_data_user_id", lambda: 2)
    elif case == "lightweight":
        cached["_fit136_lightweight_no_ow"] = True
        module.LAST_WORKOUT_RECOMMENDATION_OWNER = {"user_id": 1, "plan_id": id(cached)}
    _zero_due(monkeypatch, module)
    fingerprint_calls = []
    resolver_calls = []
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: fingerprint_calls.append(1) or "legacy-fingerprint")
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda _fp: resolver_calls.append(1) or fallback)
    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", lambda plan, **_kw: (plan, []))
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda plan, _fp: plan)

    response = client.get("/api/next-workout?active_workout_open=false")

    assert response.status_code == 200
    assert response.get_json()["next_workout"]["id"] == "legacy-plan"
    assert len(fingerprint_calls) == len(resolver_calls) == 1


def test_due_path_keeps_legacy_fingerprint_and_adaptation_wrapper(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _deterministic_route(monkeypatch, module)
    plan = _plan()
    _owned(module, plan)
    _zero_due(monkeypatch, module, value=True)
    fingerprint_calls = []
    wrapper_calls = []
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: fingerprint_calls.append(1) or "due-fingerprint")
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda _fp: plan)
    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", lambda plan, **_kw: wrapper_calls.append(1) or (plan, []))
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda plan, _fp: plan)

    response = client.get("/api/next-workout?active_workout_open=false")

    assert response.status_code == 200
    assert fingerprint_calls == [1]
    assert wrapper_calls == [1]


def test_zero_due_owned_plan_from_prior_day_uses_legacy_path(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _deterministic_route(monkeypatch, module)
    stale = _plan()
    fresh = {**_plan(), "id": "fresh-day-plan"}
    _owned(module, stale, day="2026-05-23")
    _zero_due(monkeypatch, module)
    fingerprint_calls = []
    monkeypatch.setattr(
        module,
        "_workout_recommendation_fingerprint",
        lambda: fingerprint_calls.append(1) or "fresh-day-fingerprint",
    )
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda _fp: fresh)
    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", lambda plan, **_kw: (plan, []))
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda plan, _fp: plan)

    response = client.get("/api/next-workout?active_workout_open=false")

    assert response.status_code == 200
    assert response.get_json()["next_workout"]["id"] == "fresh-day-plan"
    assert fingerprint_calls == [1]


def test_active_open_without_completed_sets_skips_due_probe_and_adaptation(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _deterministic_route(monkeypatch, module)
    fallback = _plan()
    probe_calls = []
    monkeypatch.setattr(module, "has_due_workout_adaptation_pending", lambda *_a, **_kw: probe_calls.append(1) or pytest.fail("probe"))
    fingerprint_calls = []
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: fingerprint_calls.append(1) or "active-fingerprint")
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda _fp: fallback)
    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", lambda *_a, **_kw: pytest.fail("adaptation"))
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda plan, _fp: plan)

    response = client.get("/api/next-workout?active_workout_open=true")

    assert response.status_code == 200
    assert probe_calls == []
    assert fingerprint_calls == [1]


def test_zero_due_without_qualifying_plan_generates_and_persists(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _deterministic_route(monkeypatch, module)
    _zero_due(monkeypatch, module)
    generated = _plan()
    fingerprint_calls = []
    generate_calls = []
    persist_calls = []
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: fingerprint_calls.append(1) or "generated-fingerprint")
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda _fp: None)
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_kw: generate_calls.append(1) or generated)
    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", lambda plan, **_kw: (plan, []))
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda plan, _fp: persist_calls.append(1) or plan)

    response = client.get("/api/next-workout?active_workout_open=false")

    assert response.status_code == 200
    assert generate_calls == [1]
    assert len(persist_calls) == 2
    assert fingerprint_calls == [1]


def test_has_due_query_is_user_scoped_without_materializing_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    data_store.enqueue_workout_adaptation_pending(
        1,
        date="2026-05-24",
        meal_id=None,
        food_log_client_ids=[],
        window_started_at="2026-05-24T10:00:00",
        window_closes_at="2026-05-24T10:30:00",
    )
    monkeypatch.setattr(data_store, "_workout_adaptation_pending_payload", lambda *_a, **_kw: pytest.fail("row materialized"))

    assert data_store.has_due_workout_adaptation_pending(1, now_iso="2026-05-24T11:00:00") is True
    assert data_store.has_due_workout_adaptation_pending(2, now_iso="2026-05-24T11:00:00") is False
