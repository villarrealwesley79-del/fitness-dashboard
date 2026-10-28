"""Reviewer tests (FIT-397): plan ids must be stable across reads and chained
swaps must work using the id each response hands back to the client."""
from __future__ import annotations

import copy
import importlib

import data_store


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "review-fit397")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True, PROPAGATE_EXCEPTIONS=False)
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update({"training_goal": module.TrainingGoal.HYPERTROPHY.value, "equipment_preference": "machines_only"})
    fp = {"value": "fp-review"}
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: fp["value"])
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    for name in ("WORKOUTS", "SORENESS_DATA", "WORKOUT_RECOMMENDATIONS"):
        monkeypatch.setattr(module, name, [])
    for name in ("LAST_WORKOUT_RECOMMENDATION", "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT", "LAST_WORKOUT_RECOMMENDATION_OWNER"):
        monkeypatch.setattr(module, name, None)
    monkeypatch.setattr(module, "_current_workout_training_recommendation", lambda: None)
    monkeypatch.setattr(module, "_open_wearables_recommendation_facts", lambda: {})
    monkeypatch.setattr(module, "_apply_open_wearables_recommendation_guard", lambda rec, _f: (rec, None))
    monkeypatch.setattr(module, "get_oura_daily", lambda *a, **k: {})
    monkeypatch.setattr(module, "apply_wearable_modifiers", lambda _t, workout, **k: {"next_workout": workout, "load_source": None})
    monkeypatch.setattr(module, "_compute_data_freshness", lambda: {})
    monkeypatch.setattr(module, "_whoop_recommendation_context", lambda *a, **k: {})
    monkeypatch.setattr(module, "_recommendation_sources_payload", lambda *a, **k: {})
    monkeypatch.setattr(module, "_wearable_sources_payload", lambda *a, **k: {})

    generated = []

    def fake_generate(*_a, **_k):
        generated.append(1)
        return {
            "id": module._new_workout_recommendation_id(),
            "goal": module.TrainingGoal.HYPERTROPHY.value,
            "mesocycle": {"week": 1},
            "exercises": [
                {"exercise": "Chest Press", "muscle": "chest", "target_sets": 3,
                 "target_reps": 10, "rep_range": (8, 12), "rpe_target": 7},
                {"exercise": "Lat Pulldown", "muscle": "back", "target_sets": 3,
                 "target_reps": 10, "rep_range": (8, 12), "rpe_target": 7},
            ],
        }

    monkeypatch.setattr(module, "generate_next_workout", fake_generate)
    return module, module.app.test_client(), generated, fp


def test_plan_id_stable_across_reads_and_chained_swaps_succeed(monkeypatch, tmp_path):
    module, client, generated, _fp = _setup(monkeypatch, tmp_path)

    first = client.get("/api/next-workout").get_json()["next_workout"]
    second = client.get("/api/next-workout").get_json()["next_workout"]
    assert first["id"] == second["id"], "plan id rotated on a plain read"
    assert len(generated) == 1

    swap1 = client.post("/api/workout/swap", json={
        "recommendation_id": second["id"], "exercise_index": 0, "new_exercise_name": "Incline Press"})
    assert swap1.status_code == 200, swap1.get_json()
    swapped_id = swap1.get_json()["recommendation"]["id"]
    assert swapped_id != second["id"]

    after = client.get("/api/next-workout").get_json()["next_workout"]
    assert after["id"] == swapped_id
    assert after["exercises"][0]["exercise"] == "Incline Press"

    swap2 = client.post("/api/workout/swap", json={
        "recommendation_id": swapped_id, "exercise_index": 0, "new_exercise_name": "Chest Press"})
    assert swap2.status_code == 200, swap2.get_json()
    assert swap2.get_json()["recommendation"]["exercises"][0]["exercise"] == "Chest Press"


def test_stale_id_after_regeneration_is_rejected_without_writing(monkeypatch, tmp_path):
    module, client, generated, fp = _setup(monkeypatch, tmp_path)
    old = client.get("/api/next-workout").get_json()["next_workout"]

    fp["value"] = "fp-review-2"  # an input changed; the next read regenerates
    new = client.get("/api/next-workout").get_json()["next_workout"]
    assert new["id"] != old["id"]
    before = data_store.get_current_workout_plan(1)["plan"]

    stale = client.post("/api/workout/swap", json={
        "recommendation_id": old["id"], "exercise_index": 0, "new_exercise_name": "Incline Press"})
    assert stale.status_code == 409
    assert stale.get_json()["error"]["code"] == "plan_changed"
    assert data_store.get_current_workout_plan(1)["plan"] == before


def test_missing_recommendation_id_from_old_cached_client_is_400(monkeypatch, tmp_path):
    module, client, _g, _fp = _setup(monkeypatch, tmp_path)
    client.get("/api/next-workout")
    legacy = client.post("/api/workout/swap", json={
        "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"})
    assert legacy.status_code == 400
    assert legacy.get_json()["error"]["code"] == "invalid_field"
