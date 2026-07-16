from __future__ import annotations

import copy

from test_fit256_workout_plan_persistence import _client, _recommendation


def test_smart_recommendation_due_event_does_not_replace_current_swapped_plan(monkeypatch, tmp_path):
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
    def apply_due_adaptation(plan, **_kwargs):
        patched = copy.deepcopy(plan)
        patched["exercises"][1]["exercise"] = "Adapted Pulldown"
        return patched, [{"status": "applied", "reason": "test due adaptation"}]

    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", apply_due_adaptation)
    monkeypatch.setattr(
        module,
        "_whoop_recommendation_context",
        lambda _readiness: {"signals": {}, "source_conflict": {}},
    )
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, _facts: (recommendation, {}),
    )
    monkeypatch.setattr(
        module,
        "apply_wearable_modifiers",
        lambda recommendation, workout, **kwargs: {
            "recommendation": recommendation,
            "next_workout": workout,
            "load_source": None,
        },
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

    smart = client.get("/api/recommendation/smart")

    assert smart.status_code == 200
    assert smart.get_json()["next_workout"]["exercises"][0]["exercise"] == "Incline Press"
    current_recommendation_id = smart.get_json()["next_workout"]["id"]
    adaptation_event = smart.get_json()["workout_adaptation_events"][0]
    assert adaptation_event["status"] == "applied"
    assert adaptation_event["reason"] == "test due adaptation"

    later_swap = client.post(
        "/api/workout/swap",
        json={"recommendation_id": current_recommendation_id, "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Chest Press"},
    )

    assert later_swap.status_code == 200
    recommendation = later_swap.get_json()["recommendation"]
    assert recommendation["id"] != current_recommendation_id
    assert recommendation["exercises"][0]["exercise"] == "Chest Press"
    assert recommendation["exercises"][1]["exercise"] == "Adapted Pulldown"


def test_smart_recommendation_due_event_persists_a_new_current_plan(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    new_plan = _recommendation(module)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: new_plan)
    monkeypatch.setattr(
        module,
        "_apply_due_workout_adaptations_for_plan",
        lambda plan, **_kwargs: (
            plan,
            [{"status": "applied", "reason": "test first-plan adaptation"}],
        ),
    )
    monkeypatch.setattr(
        module,
        "_whoop_recommendation_context",
        lambda _readiness: {"signals": {}, "source_conflict": {}},
    )
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda recommendation, _facts: (recommendation, {}),
    )
    monkeypatch.setattr(
        module,
        "apply_wearable_modifiers",
        lambda recommendation, workout, **kwargs: {
            "recommendation": recommendation,
            "next_workout": workout,
            "load_source": None,
        },
    )

    smart = client.get("/api/recommendation/smart")
    current_recommendation_id = smart.get_json()["next_workout"]["id"]
    later_swap = client.post(
        "/api/workout/swap",
        json={"recommendation_id": current_recommendation_id, "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert smart.status_code == 200
    assert smart.get_json()["workout_adaptation_events"][0]["status"] == "applied"
    assert later_swap.status_code == 200
    assert later_swap.get_json()["recommendation"]["id"] != current_recommendation_id
