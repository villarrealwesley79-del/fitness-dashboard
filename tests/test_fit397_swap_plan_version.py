from __future__ import annotations

import copy
import importlib
from datetime import datetime as real_datetime
from pathlib import Path

import data_store


def _plan(module, plan_id: str = "fit397-plan"):
    return {
        "id": plan_id,
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "goal_name": "Hypertrophy",
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
            },
        ],
    }


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit397-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True, PROPAGATE_EXCEPTIONS=False)
    state = {"user_id": 1, "fingerprint": "fp-fit397"}
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update({"training_goal": module.TrainingGoal.HYPERTROPHY.value, "equipment_preference": "machines_only"})
    monkeypatch.setattr(module, "_current_data_user_id", lambda: state["user_id"])
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: state["fingerprint"])
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
    monkeypatch.setattr(module, "get_oura_daily", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "apply_wearable_modifiers", lambda _training, workout, **kwargs: {"next_workout": workout})
    return module, module.app.test_client(), state


def _install_current(module, plan, fingerprint="fp-fit397"):
    module.LAST_WORKOUT_RECOMMENDATION = plan
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = fingerprint
    module.LAST_WORKOUT_RECOMMENDATION_OWNER = {
        "user_id": 1,
        "fingerprint": fingerprint,
        "plan_id": id(plan),
    }


def test_swap_requires_nonempty_recommendation_id_before_plan_lookup(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("plan lookup must be after validation")))

    for payload in (
        {"recommendation_id": None, "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
        {"recommendation_id": "", "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    ):
        response = client.post("/api/workout/swap", json=payload)
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_field"


def test_swap_plan_mismatch_precedes_invalid_exercise_and_does_not_persist(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    current = _plan(module, "server-plan")
    _install_current(module, current)
    before = copy.deepcopy(current)
    persist_calls = []
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda *args: persist_calls.append(args))

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "old-plan", "workout_index": 999999, "exercise_index": 999999, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "plan_changed"
    assert "refresh" in response.get_json()["error"]["message"].lower()
    assert persist_calls == []
    assert current == before


def test_swap_mismatch_uses_regenerated_same_index_plan(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    regenerated = _plan(module, "regenerated-plan")
    _install_current(module, regenerated)
    data_store.save_current_workout_plan(1, "fp-fit397", regenerated)

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "old-plan", "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "plan_changed"


def test_generated_plan_ids_are_distinct_within_the_same_second(monkeypatch, tmp_path):
    module, _client_obj, _state = _client(monkeypatch, tmp_path)

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 16, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(module, "datetime", FrozenDatetime)
    first = module.generate_next_workout(
        [],
        [],
        available_time=30,
        consume_cardio_rotation=False,
        include_open_wearables_readiness=False,
    )
    second = module.generate_next_workout(
        [],
        [],
        available_time=30,
        consume_cardio_rotation=False,
        include_open_wearables_readiness=False,
    )

    assert first["id"] != second["id"]
    assert first["id"].startswith("20260716120000-")
    assert second["id"].startswith("20260716120000-")


def test_swap_matching_plan_id_applies_and_persists(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    current = _plan(module, "matching-plan")
    _install_current(module, current)
    data_store.save_current_workout_plan(1, "fp-fit397", current)

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "matching-plan", "workout_index": 999999, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 200
    assert response.get_json()["recommendation"]["exercises"][0]["exercise"] == "Incline Press"
    assert response.get_json()["recommendation"]["id"] != "matching-plan"
    assert data_store.get_current_workout_plan(1)["plan"]["exercises"][0]["exercise"] == "Incline Press"

    stale_retry = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "matching-plan", "exercise_index": 1, "new_exercise_name": "Chest Press"},
    )
    assert stale_retry.status_code == 409
    assert stale_retry.get_json()["error"]["code"] == "plan_changed"


def test_swap_rechecks_plan_id_before_persisting(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    original = _plan(module, "original-plan")
    regenerated = _plan(module, "regenerated-plan")
    _install_current(module, original)
    data_store.save_current_workout_plan(1, "fp-fit397", original)
    build_entry = module._build_exercise_entry
    persist_calls = []

    def regenerate_during_swap(**kwargs):
        _install_current(module, regenerated)
        data_store.save_current_workout_plan(1, "fp-fit397", regenerated)
        return build_entry(**kwargs)

    monkeypatch.setattr(module, "_build_exercise_entry", regenerate_during_swap)
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda *args: persist_calls.append(args))

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "original-plan", "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "plan_changed"
    assert persist_calls == []
    assert data_store.get_current_workout_plan(1)["plan"] == regenerated


def test_swap_read_only_fallback_never_writes_or_mutates_global(monkeypatch, tmp_path):
    module, _client_obj, _state = _client(monkeypatch, tmp_path)
    current = _plan(module, "fallback-plan")
    _install_current(module, current)
    before = copy.deepcopy(current)
    monkeypatch.setattr(module, "save_current_workout_plan", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("read-only lookup wrote")))

    snapshot = module._read_only_current_workout_plan_for_fingerprint("fp-fit397")

    assert snapshot == before
    assert snapshot is not current
    assert current == before


def test_swap_bumps_version_for_unpersisted_current_global(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    current = _plan(module, "global-plan")
    _install_current(module, current)

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "global-plan", "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 200
    assert response.get_json()["recommendation"]["id"] != "global-plan"


def test_swap_does_not_consult_legacy_recommendation_list_or_workout_index(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    current = _plan(module, "current-plan")
    _install_current(module, current)
    data_store.save_current_workout_plan(1, "fp-fit397", current)

    class ExplodingList:
        def __iter__(self):
            raise AssertionError("WORKOUT_RECOMMENDATIONS must not be consulted")

    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", ExplodingList())
    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "current-plan", "workout_index": 999999, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 200

    app_source = Path("app.py").read_text()
    handler = app_source[app_source.index("def swap_workout_exercise"):app_source.index("# ==================== AI COACH", app_source.index("def swap_workout_exercise"))]
    assert "workout_index" not in handler
    assert "WORKOUT_RECOMMENDATIONS" not in handler


def test_swap_without_current_plan_preserves_not_found(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "missing-plan", "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_swap_client_sends_plan_id_for_picker_and_custom_and_preserves_changed_modal():
    js = Path("static/js/app.js").read_text()
    open_swap = js[js.index("async function openSwap"):js.index("function _finalizeSwap")]
    apply_swap = js[js.index("async function applySwap"):js.index("// FIT-117: free-text swap")]
    custom_swap = js[js.index("async function applyCustomSwap"):js.index("function openAdjust")]

    assert "state.nextWorkout && state.nextWorkout.id" in open_swap
    assert "state.activeWorkout && state.activeWorkout.recommendation_id" in open_swap
    assert "recommendation_id" in apply_swap
    assert "recommendation_id" in custom_swap
    assert "plan changed" in custom_swap.lower() or "plan_changed" in custom_swap
    assert "closeModal" not in apply_swap
    assert "closeModal" not in custom_swap
