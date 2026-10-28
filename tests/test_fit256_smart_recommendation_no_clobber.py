from __future__ import annotations

import copy

from test_fit256_workout_plan_persistence import _client, _recommendation


def test_smart_recommendation_does_not_replace_current_swapped_plan(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    canonical_plan = _recommendation(module)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: canonical_plan)

    created = client.get("/api/next-workout")
    assert created.status_code == 200

    first_swap = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "fit-256-plan", "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    assert first_swap.status_code == 200
    assert first_swap.get_json()["recommendation"]["exercises"][0]["exercise"] == "Incline Press"

    sidecar_plan = copy.deepcopy(canonical_plan)
    sidecar_plan["id"] = "fit-256-sidecar-plan"
    sidecar_plan["exercises"][0]["exercise"] = "Sidecar Press"
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: sidecar_plan)
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, _facts: (recommendation, {}),
    )
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, _facts: (recommendation, {}),
    )
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fp-after-refresh")
    stale_global_plan = _recommendation(module)
    stale_global_plan["exercises"][0]["exercise"] = "Stale Press"
    module.LAST_WORKOUT_RECOMMENDATION = stale_global_plan
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = "fp-after-refresh"
    module.LAST_WORKOUT_RECOMMENDATION_OWNER = {
        "user_id": 1,
        "fingerprint": "fp-after-refresh",
        "plan_id": id(stale_global_plan),
    }

    dashboard = client.get("/api/dashboard")
    smart = client.get("/api/recommendation/smart")

    assert dashboard.status_code == 200
    assert dashboard.get_json()["next_workout"]["exercises"][0]["exercise"] == "Incline Press"
    assert smart.status_code == 200
    assert smart.get_json()["next_workout"]["exercises"][0]["exercise"] == "Incline Press"
    assert "_user_customized" not in smart.get_json()["next_workout"]
    current_recommendation_id = smart.get_json()["next_workout"]["id"]

    later_swap = client.post(
        "/api/workout/swap",
        json={"recommendation_id": current_recommendation_id, "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Chest Press"},
    )

    assert later_swap.status_code == 200
    recommendation = later_swap.get_json()["recommendation"]
    assert recommendation["id"] != current_recommendation_id
    assert recommendation["exercises"][0]["exercise"] == "Chest Press"
    assert recommendation["exercises"][1]["exercise"] == "Lat Pulldown"


def test_smart_recommendation_does_not_reuse_same_day_noncustom_plan_after_drift(
    monkeypatch, tmp_path
):
    module, client, _state = _client(monkeypatch, tmp_path)
    stale_plan = _recommendation(module)
    stale_plan["id"] = "same-day-stale-plan"
    module._persist_current_workout_plan(stale_plan, "fp-before-refresh")
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fp-after-refresh")
    fresh_plan = _recommendation(module)
    fresh_plan["id"] = "same-day-fresh-plan"
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: fresh_plan)
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, _facts: (recommendation, {}),
    )

    smart = client.get("/api/recommendation/smart")

    assert smart.status_code == 200
    assert smart.get_json()["next_workout"]["id"] == "same-day-fresh-plan"
