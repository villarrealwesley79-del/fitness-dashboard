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
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    assert first_swap.status_code == 200
    assert first_swap.get_json()["recommendation"]["exercises"][0]["exercise"] == "Incline Press"

    sidecar_plan = copy.deepcopy(canonical_plan)
    sidecar_plan["id"] = "fit-256-sidecar-plan"
    sidecar_plan["exercises"][0]["exercise"] = "Sidecar Press"
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: sidecar_plan)
    monkeypatch.setattr(
        module,
        "_apply_due_workout_adaptations_for_plan",
        lambda plan, **kwargs: (
            plan,
            [{"status": "applied", "reason": "test due adaptation"}],
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

    assert smart.status_code == 200
    adaptation_event = smart.get_json()["workout_adaptation_events"][0]
    assert adaptation_event["status"] == "applied"
    assert adaptation_event["reason"] == "test due adaptation"

    later_swap = client.post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Chest Press"},
    )

    assert later_swap.status_code == 200
    recommendation = later_swap.get_json()["recommendation"]
    assert recommendation["id"] == "fit-256-plan"
    assert recommendation["exercises"][0]["exercise"] == "Chest Press"
