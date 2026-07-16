from __future__ import annotations

import copy
import importlib
import threading

import data_store


def _recommendation(module):
    return {
        "id": "fit-256-plan",
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "goal_name": "Hypertrophy",
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
            },
            {
                "exercise": "Lat Pulldown",
                "muscle": "back",
                "target_sets": 3,
                "target_reps": 10,
                "target_weight": 90,
                "rpe_target": 7,
            }
        ],
    }


def _new_recommendation(module):
    plan = _recommendation(module)
    plan["id"] = "fit-256-new-plan"
    plan["exercises"][0]["exercise"] = "Incline Press"
    return plan


def _plan_with_id(module, plan_id):
    plan = _recommendation(module)
    plan["id"] = plan_id
    return plan


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit256-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True, PROPAGATE_EXCEPTIONS=False)
    state = {"user_id": 1}
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update({
        "training_goal": module.TrainingGoal.HYPERTROPHY.value,
        "available_time_minutes": 60,
        "equipment_preference": "machines_only",
    })
    monkeypatch.setattr(module, "_current_data_user_id", lambda: state["user_id"])
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: f"fp-user-{state['user_id']}")
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_OWNER", None)
    monkeypatch.setattr(module, "_current_workout_training_recommendation", lambda: None)
    monkeypatch.setattr(module, "_open_wearables_recommendation_facts", lambda: {})
    monkeypatch.setattr(module, "_apply_open_wearables_recommendation_guard", lambda rec, _facts: (rec, None))
    monkeypatch.setattr(module, "_food_log_entries_for_context", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "_nutrition_context_for_date", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", lambda plan, **kwargs: (plan, []))
    monkeypatch.setattr(module, "get_oura_daily", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "_compute_data_freshness", lambda: {"open_wearables": {}})
    monkeypatch.setattr(module, "_whoop_recommendation_context", lambda _readiness: {"signals": {}, "source_conflict": None})
    monkeypatch.setattr(
        module,
        "apply_wearable_modifiers",
        lambda _training, workout, **kwargs: {"next_workout": workout, "load_source": None},
    )
    monkeypatch.setattr(module, "build_open_wearables_recommendation_source", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "_wearable_sources_payload", lambda _freshness, _whoop_context: {})
    return module, module.app.test_client(), state


def test_persist_current_workout_plan_uses_authoritative_saved_plan(monkeypatch, tmp_path):
    module, _flask_client, _state = _client(monkeypatch, tmp_path)
    submitted_plan = _recommendation(module)
    authoritative_plan = _new_recommendation(module)
    save_calls = []

    def save_plan(user_id, fingerprint, recommendation, **kwargs):
        save_calls.append((user_id, fingerprint, recommendation, kwargs))
        return {"plan": authoritative_plan}

    monkeypatch.setattr(module, "save_current_workout_plan", save_plan)

    persisted_plan = module._persist_current_workout_plan(submitted_plan, "fp-user-1")

    assert save_calls == [
        (1, "fp-user-1", submitted_plan, {"publish_adaptation_event_ids": None})
    ]
    assert persisted_plan == authoritative_plan
    assert module.LAST_WORKOUT_RECOMMENDATION is persisted_plan
    assert module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT == "fp-user-1"
    assert module.LAST_WORKOUT_RECOMMENDATION_OWNER == {
        "user_id": 1,
        "fingerprint": "fp-user-1",
        "plan_id": id(persisted_plan),
        "customized": False,
    }


def test_next_workout_returns_authoritative_persisted_plan(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    submitted_plan = _recommendation(module)
    authoritative_plan = _new_recommendation(module)
    persist_calls = []

    monkeypatch.setattr(module, "generate_next_workout", lambda *_args, **_kwargs: submitted_plan)

    def persist_plan(recommendation, fingerprint):
        persist_calls.append((recommendation, fingerprint))
        return authoritative_plan

    monkeypatch.setattr(module, "_persist_current_workout_plan", persist_plan)

    response = client.get("/api/next-workout")

    assert response.status_code == 200
    assert response.get_json()["next_workout"]["id"] == "fit-256-new-plan"
    assert response.get_json()["next_workout"]["exercises"][0]["exercise"] == "Incline Press"
    assert persist_calls == [(submitted_plan, "fp-user-1")]


def test_swap_uses_persisted_plan_after_worker_globals_are_empty(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))

    created = client.get("/api/next-workout")
    assert created.status_code == 200

    module.LAST_WORKOUT_RECOMMENDATION = None
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None

    swapped = client.post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert swapped.status_code == 200
    exercise = swapped.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Incline Press"

    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fp-after-gym-refresh")
    gym = client.get("/gym-now")

    assert gym.status_code == 200
    assert b"Incline Press" in gym.data
    assert data_store.get_current_workout_plan(1)["plan"]["_user_customized"] is True


def test_adjust_marks_persisted_plan_as_user_customized(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    module.USER_SETTINGS["equipment_preference"] = "machines_and_cables"
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))
    monkeypatch.setattr(module, "_lm_studio", None)

    assert client.get("/api/next-workout").status_code == 200
    adjusted = client.post("/api/workout/adjust", json={"constraint": "pectoral fly"})

    assert adjusted.status_code == 200
    assert adjusted.get_json()["status"] == "ok"
    assert data_store.get_current_workout_plan(1)["plan"]["_user_customized"] is True


def test_adjust_does_not_mark_cached_refusal_as_user_customized(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))

    class FakeAdapter:
        LM_STUDIO_MODEL_VERSION = "test-model"

        def active_candidate(self):
            return None

        def model_version_for(self, _candidate):
            return self.LM_STUDIO_MODEL_VERSION

        def fallback_model_versions(self):
            return []

    cached = {
        "status": "ok",
        "result_kind": "refused",
        "recommendation": _recommendation(module),
        "summary": "No safe change.",
        "applied_notes": [],
        "constraint": "keep it unchanged",
        "meta": {"model_version": "test-model"},
        "cache_hit": False,
    }
    monkeypatch.setattr(module, "_lm_studio", FakeAdapter())
    monkeypatch.setattr(module, "_ai_cache_key", lambda *_args, **_kwargs: "cached-refusal")
    monkeypatch.setattr(module, "_ai_cache_get", lambda _key: dict(cached))
    monkeypatch.setattr(module, "_ai_metric_log", lambda *_args, **_kwargs: None)

    assert client.get("/api/next-workout").status_code == 200
    adjusted = client.post("/api/workout/adjust", json={"constraint": "keep it unchanged"})

    assert adjusted.status_code == 200
    assert adjusted.get_json()["result_kind"] == "refused"
    assert "_user_customized" not in data_store.get_current_workout_plan(1)["plan"]


def test_equivalent_plan_copy_preserves_existing_customization(monkeypatch, tmp_path):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    fingerprint = module._workout_recommendation_fingerprint()
    customized_plan = _recommendation(module)
    module._persist_current_workout_plan(customized_plan, fingerprint, customized=True)

    module._persist_current_workout_plan(copy.deepcopy(customized_plan), fingerprint)

    assert data_store.get_current_workout_plan(1)["plan"]["_user_customized"] is True


def test_fingerprint_drift_lookup_preserves_customization_without_rekey(monkeypatch, tmp_path):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    customized_plan = _recommendation(module)
    module._persist_current_workout_plan(
        customized_plan,
        "fp-before-refresh",
        customized=True,
    )

    resolved = module._current_workout_plan_for_fingerprint(
        "fp-after-refresh",
        allow_stale_unsaved=True,
    )

    assert resolved["id"] == customized_plan["id"]
    stored = data_store.get_current_workout_plan(1)
    assert stored["fingerprint"] == "fp-before-refresh"
    assert stored["plan"]["_user_customized"] is True


def test_durable_plan_beats_stale_cache_with_different_identity(monkeypatch, tmp_path):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    durable = _plan_with_id(module, "durable-plan")
    stale = _plan_with_id(module, "stale-cache")
    data_store.save_current_workout_plan(1, "durable-fingerprint", durable)
    module.LAST_WORKOUT_RECOMMENDATION = stale
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = "stale-fingerprint"
    module.LAST_WORKOUT_RECOMMENDATION_OWNER = {
        "user_id": 1,
        "fingerprint": "stale-fingerprint",
        "plan_id": id(stale),
    }

    resolved = module._current_workout_plan_for_fingerprint(
        "requested-fingerprint",
        allow_stale_unsaved=True,
    )

    assert resolved == durable
    assert module.LAST_WORKOUT_RECOMMENDATION == durable
    assert module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT == "durable-fingerprint"


def test_durable_plan_content_beats_same_identity_cache_drift(monkeypatch, tmp_path):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    durable = _plan_with_id(module, "durable-plan")
    drifted = _plan_with_id(module, "drifted-cache")
    data_store.save_current_workout_plan(1, "same-fingerprint", durable)
    module.LAST_WORKOUT_RECOMMENDATION = drifted
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = "same-fingerprint"
    module.LAST_WORKOUT_RECOMMENDATION_OWNER = {
        "user_id": 1,
        "fingerprint": "same-fingerprint",
        "plan_id": id(drifted),
    }

    resolved = module._current_workout_plan_for_fingerprint("same-fingerprint")

    assert resolved == durable
    assert module.LAST_WORKOUT_RECOMMENDATION == durable
    assert module.LAST_WORKOUT_RECOMMENDATION_OWNER["plan_id"] == id(resolved)


def test_default_current_plan_lookup_does_not_return_cache_without_durable_row(
    monkeypatch,
    tmp_path,
):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    cached = _recommendation(module)
    module._persist_current_workout_plan(cached, "cached-fingerprint")
    data_store.delete_current_workout_plan(1)
    writes = []
    monkeypatch.setattr(
        module,
        "save_current_workout_plan",
        lambda *_args, **_kwargs: writes.append(True),
    )

    resolved = module._current_workout_plan_for_fingerprint("cached-fingerprint")

    assert resolved is None
    assert writes == []
    assert data_store.get_current_workout_plan(1) is None


def test_explicit_stale_unsaved_lookup_returns_cache_without_persisting(
    monkeypatch,
    tmp_path,
):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    cached = _recommendation(module)
    module._persist_current_workout_plan(cached, "cached-fingerprint")
    data_store.delete_current_workout_plan(1)
    writes = []
    original_save = module.save_current_workout_plan

    def record_save(*args, **kwargs):
        writes.append((args, kwargs))
        return original_save(*args, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", record_save)

    resolved = module._current_workout_plan_for_fingerprint(
        "new-fingerprint",
        allow_stale_unsaved=True,
    )

    assert resolved["_fit233_current_plan_absent"] is True
    assert {
        key: value
        for key, value in resolved.items()
        if key != "_fit233_current_plan_absent"
    } == cached
    assert writes == []
    assert data_store.get_current_workout_plan(1) is None


def test_stale_unsaved_mutation_cannot_overwrite_concurrent_replacement(
    monkeypatch,
    tmp_path,
):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    cached = _recommendation(module)
    module._persist_current_workout_plan(cached, "cached-fingerprint")
    data_store.delete_current_workout_plan(1)
    mutation = module._current_workout_plan_for_fingerprint(
        "mutation-fingerprint",
        allow_stale_unsaved=True,
    )
    replacement = _plan_with_id(module, "concurrent-replacement")
    data_store.save_current_workout_plan(1, "replacement-fingerprint", replacement)
    assert module._current_workout_plan_for_fingerprint(
        "replacement-fingerprint"
    ) == replacement
    mutation["id"] = "stale-mutation"

    module._persist_current_workout_plan(
        mutation,
        "mutation-fingerprint",
        customized=True,
    )

    durable = data_store.get_current_workout_plan(1)
    assert durable["fingerprint"] == "replacement-fingerprint"
    assert durable["plan"]["id"] == "concurrent-replacement"


def test_adjust_cache_hit_preserves_absent_plan_cas(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    cached_plan = _recommendation(module)
    module._persist_current_workout_plan(cached_plan, "cached-fingerprint")
    data_store.delete_current_workout_plan(1)
    monkeypatch.setattr(
        module,
        "_workout_recommendation_fingerprint",
        lambda: "mutation-fingerprint",
    )

    class FakeAdapter:
        def active_candidate(self):
            return None

        def model_version_for(self, _candidate):
            return "test-model"

        def fallback_model_versions(self):
            return []

    replacement = _plan_with_id(module, "concurrent-replacement")

    def cache_get(_key):
        data_store.save_current_workout_plan(
            1,
            "replacement-fingerprint",
            replacement,
        )
        module._current_workout_plan_for_fingerprint(
            "replacement-fingerprint",
            allow_stale_unsaved=True,
        )
        return {
            "status": "ok",
            "result_kind": "changed",
            "recommendation": _plan_with_id(module, "cached-adjustment"),
            "summary": "Cached adjustment",
            "applied_notes": [],
        }

    monkeypatch.setattr(module, "_lm_studio", FakeAdapter())
    monkeypatch.setattr(module, "_ai_cache_get", cache_get)
    monkeypatch.setattr(module, "_ai_metric_log", lambda *_args, **_kwargs: None)

    response = client.post(
        "/api/workout/adjust",
        json={"constraint": "use the cached adjustment"},
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"]["code"] == "workout_plan_conflict"
    assert "recommendation" not in payload
    durable = data_store.get_current_workout_plan(1)
    assert durable["fingerprint"] == "replacement-fingerprint"
    assert durable["plan"]["id"] == "concurrent-replacement"


def test_absent_plan_cas_rejects_create_delete_aba(monkeypatch, tmp_path):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    expected_generation = data_store.get_workout_plan_generation(1)
    assert expected_generation == 0

    data_store.save_current_workout_plan(1, "temporary", _plan_with_id(module, "temporary"))
    assert data_store.delete_current_workout_plan(1) is True
    assert data_store.get_current_workout_plan(1) is None
    assert data_store.get_workout_plan_generation(1) == 1

    stale_insert = data_store.save_current_workout_plan(
        1,
        "stale-insert",
        _plan_with_id(module, "stale-insert"),
        only_if_current_plan_absent=True,
        expected_absent_plan_generation=expected_generation,
    )

    assert stale_insert is None
    assert data_store.get_current_workout_plan(1) is None


def test_adjust_initial_generation_rejects_absent_create_delete_aba(
    monkeypatch,
    tmp_path,
):
    module, client, _state = _client(monkeypatch, tmp_path)
    generated = _plan_with_id(module, "generated-adjustment-plan")
    monkeypatch.setattr(module, "generate_next_workout", lambda *_args, **_kwargs: generated)
    monkeypatch.setattr(module, "_lm_studio", None)
    original_save = module.save_current_workout_plan
    interleaved = {"done": False}

    def save_with_create_delete(user_id, fingerprint, plan, **kwargs):
        if not interleaved["done"]:
            interleaved["done"] = True
            data_store.save_current_workout_plan(
                user_id,
                "temporary-adjustment-winner",
                _plan_with_id(module, "temporary-adjustment-winner"),
            )
            data_store.delete_current_workout_plan(user_id)
        return original_save(user_id, fingerprint, plan, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", save_with_create_delete)
    response = client.post("/api/workout/adjust", json={"constraint": "make this easier"})

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"]["code"] == "workout_plan_conflict"
    assert "recommendation" not in payload
    assert data_store.get_current_workout_plan(1) is None
    assert data_store.get_workout_plan_generation(1) == 1


def test_swap_rejects_interleaved_existing_plan_write_without_payload_leak(
    monkeypatch,
    tmp_path,
):
    module, client, _state = _client(monkeypatch, tmp_path)
    base_plan = _recommendation(module)
    monkeypatch.setattr(module, "generate_next_workout", lambda *_args, **_kwargs: base_plan)
    assert client.get("/api/next-workout").status_code == 200

    replacement = _plan_with_id(module, "concurrent-replacement")
    original_save = module.save_current_workout_plan
    interleaved = {"done": False}

    def save_with_interleaved_write(user_id, fingerprint, plan, **kwargs):
        if not interleaved["done"]:
            interleaved["done"] = True
            data_store.save_current_workout_plan(
                user_id,
                "replacement-fingerprint",
                replacement,
            )
        return original_save(user_id, fingerprint, plan, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", save_with_interleaved_write)
    response = client.post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"]["code"] == "workout_plan_conflict"
    assert "recommendation" not in payload
    durable = data_store.get_current_workout_plan(1)
    assert durable["fingerprint"] == "replacement-fingerprint"
    assert durable["plan"]["id"] == "concurrent-replacement"


def test_next_workout_regeneration_cas_preserves_concurrent_fingerprint_winner(
    monkeypatch,
    tmp_path,
):
    module, client, _state = _client(monkeypatch, tmp_path)
    stale_plan = _plan_with_id(module, "stale-plan")
    data_store.save_current_workout_plan(1, "stale-fingerprint", stale_plan)
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fresh-fingerprint")

    generated = _plan_with_id(module, "fresh-generated-plan")
    winner = _plan_with_id(module, "concurrent-customized-winner")
    monkeypatch.setattr(module, "generate_next_workout", lambda *_args, **_kwargs: generated)
    original_save = module.save_current_workout_plan
    interleaved = {"done": False}

    def save_with_interleaved_write(user_id, fingerprint, plan, **kwargs):
        if not interleaved["done"]:
            interleaved["done"] = True
            data_store.save_current_workout_plan(
                user_id,
                "winner-fingerprint",
                {**winner, "_user_customized": True},
            )
        return original_save(user_id, fingerprint, plan, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", save_with_interleaved_write)
    response = client.get("/api/next-workout")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["next_workout"]["id"] == "concurrent-customized-winner"
    assert "_fit233_current_plan_absent" not in payload["next_workout"]
    assert "_fit233_expected_plan_generation" not in payload["next_workout"]
    durable = data_store.get_current_workout_plan(1)
    assert durable["fingerprint"] == "winner-fingerprint"
    assert durable["plan"]["id"] == "concurrent-customized-winner"
    assert durable["plan"]["_user_customized"] is True


def test_dashboard_regeneration_rereads_concurrent_winner_across_fingerprint(
    monkeypatch,
    tmp_path,
):
    module, client, _state = _client(monkeypatch, tmp_path)
    data_store.save_current_workout_plan(
        1,
        "stale-fingerprint",
        _plan_with_id(module, "stale-dashboard-plan"),
    )
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fresh-fingerprint")
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, _facts: (recommendation, {}),
    )
    monkeypatch.setattr(module, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})
    generated = _plan_with_id(module, "fresh-dashboard-plan")
    winner = _plan_with_id(module, "concurrent-dashboard-winner")
    monkeypatch.setattr(module, "generate_next_workout", lambda *_args, **_kwargs: generated)
    original_save = module.save_current_workout_plan
    interleaved = {"done": False}

    def save_with_interleaved_write(user_id, fingerprint, plan, **kwargs):
        if not interleaved["done"]:
            interleaved["done"] = True
            data_store.save_current_workout_plan(
                user_id,
                "winner-dashboard-fingerprint",
                {**winner, "_user_customized": True},
            )
        return original_save(user_id, fingerprint, plan, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", save_with_interleaved_write)
    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["next_workout"]["id"] == "concurrent-dashboard-winner"
    assert "_fit233_current_plan_absent" not in payload["next_workout"]
    assert "_fit233_expected_plan_generation" not in payload["next_workout"]
    durable = data_store.get_current_workout_plan(1)
    assert durable["fingerprint"] == "winner-dashboard-fingerprint"
    assert durable["plan"]["id"] == "concurrent-dashboard-winner"
    assert durable["plan"]["_user_customized"] is True


def test_next_workout_delete_race_returns_conflict_without_losing_payload(
    monkeypatch,
    tmp_path,
):
    module, client, _state = _client(monkeypatch, tmp_path)
    data_store.save_current_workout_plan(
        1,
        "stale-fingerprint",
        _plan_with_id(module, "stale-delete-plan"),
    )
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fresh-fingerprint")
    generated = _plan_with_id(module, "losing-generated-plan")
    monkeypatch.setattr(module, "generate_next_workout", lambda *_args, **_kwargs: generated)
    original_save = module.save_current_workout_plan

    def save_then_delete(user_id, fingerprint, plan, **kwargs):
        data_store.delete_current_workout_plan(user_id)
        return original_save(user_id, fingerprint, plan, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", save_then_delete)
    response = client.get("/api/next-workout")

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"]["code"] == "workout_plan_conflict"
    assert "next_workout" not in payload
    assert data_store.get_current_workout_plan(1) is None


def test_dashboard_delete_race_returns_conflict_without_losing_payload(
    monkeypatch,
    tmp_path,
):
    module, client, _state = _client(monkeypatch, tmp_path)
    data_store.save_current_workout_plan(
        1,
        "stale-fingerprint",
        _plan_with_id(module, "stale-dashboard-delete-plan"),
    )
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fresh-fingerprint")
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, _facts: (recommendation, {}),
    )
    monkeypatch.setattr(module, "_nutrition_today_public_payload", lambda *_args, **_kwargs: {})
    generated = _plan_with_id(module, "losing-dashboard-generated-plan")
    monkeypatch.setattr(module, "generate_next_workout", lambda *_args, **_kwargs: generated)
    original_save = module.save_current_workout_plan

    def save_then_delete(user_id, fingerprint, plan, **kwargs):
        data_store.delete_current_workout_plan(user_id)
        return original_save(user_id, fingerprint, plan, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", save_then_delete)
    response = client.get("/api/dashboard")

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"]["code"] == "workout_plan_conflict"
    assert "next_workout" not in payload
    assert data_store.get_current_workout_plan(1) is None


def test_repeated_adaptation_event_polling_does_not_backfill_plan_tables(
    monkeypatch,
    tmp_path,
):
    module, client, _state = _client(monkeypatch, tmp_path)
    trace = []
    original_connect = data_store.sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(trace.append)
        return connection

    monkeypatch.setattr(data_store.sqlite3, "connect", traced_connect)
    for _ in range(3):
        response = client.get("/api/workout-adaptation-events?unacknowledged=false")
        assert response.status_code == 200
        assert response.get_json()["events"] == []

    plan_table_dml = []
    for statement in trace:
        normalized = statement.strip().upper()
        if (
            normalized.startswith("INSERT")
            or normalized.startswith("UPDATE")
            or normalized.startswith("DELETE")
        ) and (
            "WORKOUT_PLAN_GENERATIONS" in normalized
            or "CURRENT_WORKOUT_PLANS" in normalized
        ):
            plan_table_dml.append(statement)
    assert plan_table_dml == []
    assert data_store.list_workout_adaptation_events(1, unacknowledged=False) == []


def test_current_plan_lookup_uses_atomic_plan_snapshot(monkeypatch, tmp_path):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    snapshot_calls = []

    def snapshot(user_id):
        snapshot_calls.append(user_id)
        return {"current_plan": None, "plan_generation": 7}

    def fail_separate_plan_read(*_args, **_kwargs):
        raise AssertionError("lookup must not perform a separate current-plan read")

    monkeypatch.setattr(module, "get_current_workout_plan_snapshot", snapshot)
    monkeypatch.setattr(module, "get_current_workout_plan", fail_separate_plan_read)

    token = module._current_workout_plan_for_fingerprint(
        "missing-fingerprint",
        return_lookup_token=True,
    )

    assert token.current_plan_absent is True
    assert token.absent_plan_generation == 7
    assert snapshot_calls == [1]


def test_worker_rehydration_restores_customization_without_exposing_marker(monkeypatch, tmp_path):
    module, _client_instance, _state = _client(monkeypatch, tmp_path)
    fingerprint = module._workout_recommendation_fingerprint()
    module._persist_current_workout_plan(
        _recommendation(module),
        fingerprint,
        customized=True,
    )
    module.LAST_WORKOUT_RECOMMENDATION = None
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None
    module.LAST_WORKOUT_RECOMMENDATION_OWNER = None
    persisted_writes = []
    original_save = module.save_current_workout_plan

    def record_save(*args, **kwargs):
        persisted_writes.append(True)
        return original_save(*args, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", record_save)

    rehydrated = module._current_workout_plan_for_fingerprint(fingerprint)

    assert "_user_customized" not in rehydrated
    assert module.LAST_WORKOUT_RECOMMENDATION_OWNER["customized"] is True
    assert persisted_writes == []
    module._persist_current_workout_plan(copy.deepcopy(rehydrated), fingerprint)
    assert persisted_writes == [True]
    assert data_store.get_current_workout_plan(1)["plan"]["_user_customized"] is True


def test_swap_rejects_stale_global_plan_from_another_user(monkeypatch, tmp_path):
    module, client, state = _client(monkeypatch, tmp_path)
    module._persist_current_workout_plan(_recommendation(module), "fp-user-1")
    state["user_id"] = 2

    response = client.post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_swap_uses_same_user_plan_after_fingerprint_drift(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    module._persist_current_workout_plan(_recommendation(module), "fp-before-refresh")
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fp-after-refresh")

    swapped = client.post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert swapped.status_code == 200
    exercise = swapped.get_json()["recommendation"]["exercises"][0]
    assert exercise["exercise"] == "Incline Press"


def test_adjust_uses_persisted_plan_after_worker_globals_are_empty(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))

    created = client.get("/api/next-workout")
    assert created.status_code == 200

    module.LAST_WORKOUT_RECOMMENDATION = None
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None

    def fail_generate(*_args, **_kwargs):
        raise AssertionError("adjust should load the current persisted plan")

    monkeypatch.setattr(module, "generate_next_workout", fail_generate)
    monkeypatch.setattr(module, "_lm_studio", None)

    adjusted = client.post("/api/workout/adjust", json={"constraint": "make this easier"})

    assert adjusted.status_code == 200
    payload = adjusted.get_json()
    assert payload["recommendation"]["exercises"][0]["exercise"] == "Chest Press"


def test_adjust_uses_persisted_plan_after_fingerprint_drift(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))

    created = client.get("/api/next-workout")
    assert created.status_code == 200

    module.LAST_WORKOUT_RECOMMENDATION = None
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fp-after-refresh")

    def fail_generate(*_args, **_kwargs):
        raise AssertionError("adjust should load the current persisted plan after fingerprint drift")

    monkeypatch.setattr(module, "generate_next_workout", fail_generate)
    monkeypatch.setattr(module, "_lm_studio", None)

    adjusted = client.post("/api/workout/adjust", json={"constraint": "make this easier"})

    assert adjusted.status_code == 200
    payload = adjusted.get_json()
    assert payload["recommendation"]["exercises"][0]["exercise"] == "Chest Press"


def test_complete_workout_uses_persisted_plan_after_worker_globals_are_empty(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))

    created = client.get("/api/next-workout")
    assert created.status_code == 200

    module.LAST_WORKOUT_RECOMMENDATION = None
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None
    response = client.post(
        "/api/complete-workout",
        json={
            "recommendation_id": "fit-256-plan",
            "exercises": [
                {
                    "machine": "Chest Press",
                    "sets": [{"set_number": 1, "weight_lbs": 100, "reps": 10}],
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["adherence"]["followed"] is False
    assert payload["adherence"]["skipped"] == ["Lat Pulldown"]


def test_complete_workout_uses_persisted_plan_after_fingerprint_drift(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))

    created = client.get("/api/next-workout")
    assert created.status_code == 200

    module.LAST_WORKOUT_RECOMMENDATION = None
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fp-after-refresh")
    response = client.post(
        "/api/complete-workout",
        json={
            "recommendation_id": "fit-256-plan",
            "exercises": [
                {
                    "machine": "Chest Press",
                    "sets": [{"set_number": 1, "weight_lbs": 100, "reps": 10}],
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["adherence"]["followed"] is False
    assert payload["adherence"]["skipped"] == ["Lat Pulldown"]


def test_complete_workout_rejects_cross_user_global_plan(monkeypatch, tmp_path):
    module, client, state = _client(monkeypatch, tmp_path)
    module._persist_current_workout_plan(_recommendation(module), "fp-user-1")
    state["user_id"] = 2

    response = client.post(
        "/api/complete-workout",
        json={
            "recommendation_id": "fit-256-plan",
            "exercises": [
                {
                    "machine": "Chest Press",
                    "sets": [{"set_number": 1, "weight_lbs": 100, "reps": 10}],
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["adherence"] == {"followed": None, "skipped": [], "modified": [], "added": []}


def test_complete_workout_invalidates_persisted_current_plan(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))

    created = client.get("/api/next-workout")
    assert created.status_code == 200

    completed = client.post(
        "/api/complete-workout",
        json={
            "recommendation_id": "fit-256-plan",
            "exercises": [
                {
                    "machine": "Chest Press",
                    "sets": [{"set_number": 1, "weight_lbs": 100, "reps": 10}],
                }
            ],
        },
    )
    assert completed.status_code == 200

    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _new_recommendation(module))
    monkeypatch.setattr(module, "_lm_studio", None)

    adjusted = client.post("/api/workout/adjust", json={"constraint": "make this easier"})

    assert adjusted.status_code == 200
    payload = adjusted.get_json()
    assert payload["recommendation"]["id"] == "fit-256-new-plan"
    assert payload["recommendation"]["exercises"][0]["exercise"] == "Incline Press"


def test_gym_now_lightweight_plan_does_not_seed_next_workout(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)

    def fake_generate(*_args, **kwargs):
        if kwargs.get("include_open_wearables_readiness") is False:
            return _plan_with_id(module, "gym-now-plan")
        return _plan_with_id(module, "canonical-plan")

    monkeypatch.setattr(module, "generate_next_workout", fake_generate)

    gym_response = client.get("/gym-now")
    assert gym_response.status_code == 200

    next_response = client.get("/api/next-workout")

    assert next_response.status_code == 200
    workout = next_response.get_json()["next_workout"]
    assert workout["id"] == "canonical-plan"
    assert "_fit136_lightweight_no_ow" not in workout


def test_delete_user_data_purges_current_workout_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    data_store.save_current_workout_plan(1, "fp-user-1", {"id": "fit-256-plan"})

    assert data_store.get_user_data_summary(1)["current_workout_plans"] == 1

    data_store.delete_user_data(1)

    assert data_store.get_current_workout_plan(1) is None
    assert data_store.get_user_data_summary(1)["current_workout_plans"] == 0


def test_current_plan_cache_read_waits_for_owner_metadata(monkeypatch, tmp_path):
    module, _unused_client, _state = _client(monkeypatch, tmp_path)
    plan = _recommendation(module)
    thread_state = threading.local()
    pause_owner_lookup = threading.Event()
    release_owner_lookup = threading.Event()
    reader_finished = threading.Event()
    paused_once = {"value": False}
    reader_result = {}

    def current_user_id():
        user_id = getattr(thread_state, "user_id", 1)
        if user_id == 1 and not paused_once["value"]:
            paused_once["value"] = True
            pause_owner_lookup.set()
            assert release_owner_lookup.wait(timeout=2)
        return user_id

    monkeypatch.setattr(module, "_current_data_user_id", current_user_id)
    monkeypatch.setattr(module, "save_current_workout_plan", lambda *_args, **_kwargs: {"plan": plan})

    def writer():
        thread_state.user_id = 1
        module._persist_current_workout_plan(plan, "fp-user-1")

    def reader():
        thread_state.user_id = 2
        reader_result["plan"] = module._current_workout_plan_for_fingerprint(
            "fp-user-1",
            allow_stale_unsaved=True,
        )
        reader_finished.set()

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    assert pause_owner_lookup.wait(timeout=2)

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    assert not reader_finished.wait(timeout=0.2)

    release_owner_lookup.set()
    writer_thread.join(timeout=2)
    reader_thread.join(timeout=2)

    assert not writer_thread.is_alive()
    assert not reader_thread.is_alive()
    assert reader_result["plan"] is None
