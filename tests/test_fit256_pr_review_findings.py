"""Regression tests pinning fixes for the three adversarial PR review findings
raised against FIT-256 (villarrealwesley79/fit-256-eliminate-multi-worker-
global-state-corruption-plan):

1. stale-plan poisoning in smart_recommendation_api() across a fingerprint
   (e.g. calendar-day) rollover.
2. durable wearable-modifier ratchet in api_dashboard()/api_next_workout()
   permanently reducing target_sets after a transient WHOOP deload/caution
   reading.

Each test is written to FAIL against the pre-fix branch code and PASS after
the fix.
"""

from __future__ import annotations

import copy

import data_store
import whoop_recommendations

from test_fit256_workout_plan_persistence import _client, _recommendation


def test_smart_recommendation_does_not_resurrect_stale_plan_across_day_rollover(monkeypatch, tmp_path):
    """FIT-256 finding 1 regression.

    Before the fix, smart_recommendation_api() read the stored plan via
    `get_current_workout_plan(user_id)` with NO fingerprint filter, so once
    the fingerprint rolled over (new day / settings / sync -- the
    fingerprint embeds `day: today_s`) a due-adaptation event would adapt
    *yesterday's* plan and persist it re-stamped with *today's* fingerprint.
    That poisons every subsequent fingerprint-scoped read (including
    /api/next-workout) with the stale plan, seemingly forever, because it
    now satisfies the current-fingerprint lookup.
    """
    module, client, state = _client(monkeypatch, tmp_path)

    # smart_recommendation_api() (unlike /api/next-workout) also reads
    # whoop_recommendation["recommendation"] and
    # whoop_context["source_conflict"].get(...), which the default
    # _client() passthrough stubs don't support -- match
    # test_fit256_smart_recommendation_no_clobber.py's stubs.
    monkeypatch.setattr(
        module,
        "apply_wearable_modifiers",
        lambda recommendation, workout, **kwargs: {
            "recommendation": recommendation,
            "next_workout": workout,
            "load_source": None,
        },
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

    day1_plan = _recommendation(module)
    day1_plan["id"] = "day1-plan"
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: day1_plan)

    created = client.get("/api/next-workout")
    assert created.status_code == 200

    swap = client.post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    assert swap.status_code == 200
    assert swap.get_json()["recommendation"]["exercises"][0]["exercise"] == "Incline Press"

    # The swap is genuinely persisted "today" (whatever real day the suite
    # runs on). Backdate the stored row so it reflects a plan that was
    # actually written on a *prior* calendar day, independent of the
    # fingerprint string used to model the rollover below.
    with data_store._get_db() as conn:
        conn.execute(
            "UPDATE current_workout_plans SET updated_at = ? WHERE user_id = ?",
            ("2000-01-01T00:00:00", state["user_id"]),
        )
        conn.commit()

    # Simulate a fresh worker / restart on the new day: no in-memory cache
    # carried over (this is the "in-memory globals cleared" case, distinct
    # from the "worker-local cache is merely stale-but-present" case already
    # covered by test_fit256_smart_recommendation_no_clobber.py).
    module.LAST_WORKOUT_RECOMMENDATION = None
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None
    module.LAST_WORKOUT_RECOMMENDATION_OWNER = None

    # Simulate the day rollover itself: _workout_recommendation_fingerprint
    # now returns a different value because it embeds `day: today_s`.
    monkeypatch.setattr(
        module,
        "_workout_recommendation_fingerprint",
        lambda: f"fp-user-{state['user_id']}-day2",
    )

    fresh_day2_plan = _recommendation(module)
    fresh_day2_plan["id"] = "day2-fresh-plan"
    fresh_day2_plan["exercises"][0]["exercise"] = "Day2 Fresh Press"
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: fresh_day2_plan)

    def apply_due_adaptation(plan, **_kwargs):
        patched = copy.deepcopy(plan)
        patched["exercises"][1]["exercise"] = "Day2 Adapted Pulldown"
        return patched, [{"status": "applied", "reason": "test day-2 due adaptation"}]

    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", apply_due_adaptation)

    smart = client.get("/api/recommendation/smart")

    assert smart.status_code == 200
    smart_payload = smart.get_json()
    # The resurrected/stale "Incline Press" plan must NOT be served.
    assert smart_payload["next_workout"]["exercises"][0]["exercise"] != "Incline Press"
    assert smart_payload["next_workout"]["id"] == "day2-fresh-plan"
    assert smart_payload["workout_adaptation_events"][0]["status"] == "applied"

    # A subsequent /api/next-workout call must serve the freshly regenerated
    # plan (matching today's fingerprint), not the resurrected stale one.
    next_workout = client.get("/api/next-workout")
    assert next_workout.status_code == 200
    served = next_workout.get_json()["next_workout"]
    assert served["id"] == "day2-fresh-plan"
    assert served["exercises"][0]["exercise"] != "Incline Press"


def test_wearable_deload_modifier_does_not_permanently_ratchet_target_sets(monkeypatch, tmp_path):
    """FIT-256 finding 2 regression.

    Before the fix, api_next_workout()/api_dashboard() persisted the output
    of apply_wearable_modifiers() (a one-directional min(original, scaled)
    clamp keyed on the currently-active WHOOP deload/caution modifier) as the
    canonical durable plan. Because a later no-modifier call reads that
    already-clamped plan back as the base and apply_wearable_modifiers only
    clamps -- it never restores -- the reduced target_sets became a
    permanent ratchet across requests/restarts instead of a display-time-only
    effect of a transient wearable reading.
    """
    module, client, state = _client(monkeypatch, tmp_path)

    # Use the *real* wearable-modifier transform instead of the passthrough
    # stub _client() installs, so the deload clamp actually executes.
    monkeypatch.setattr(module, "apply_wearable_modifiers", whoop_recommendations.apply_wearable_modifiers)

    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))

    whoop_state = {"modifiers": ["deload"]}

    def whoop_context(_readiness):
        return {
            "signals": {"applied_modifiers": list(whoop_state["modifiers"]), "explanations": []},
            "source_conflict": {},
        }

    monkeypatch.setattr(module, "_whoop_recommendation_context", whoop_context)

    # Call 1: a transient WHOOP deload reading is active. The *display*
    # plan should reflect the clamp (3 -> 2 sets), but the canonical
    # persisted plan must stay at the base/programmed value.
    deloaded = client.get("/api/next-workout")
    assert deloaded.status_code == 200
    deloaded_exercise = deloaded.get_json()["next_workout"]["exercises"][0]
    assert deloaded_exercise["target_sets"] == 2

    stored_after_deload = data_store.get_current_workout_plan(state["user_id"])
    assert stored_after_deload is not None
    assert stored_after_deload["plan"]["exercises"][0]["target_sets"] == 3

    # Call 2: WHOOP recovery normalizes, no modifier is active. target_sets
    # must NOT be stuck at the deload-clamped value.
    whoop_state["modifiers"] = []
    recovered = client.get("/api/next-workout")
    assert recovered.status_code == 200
    recovered_exercise = recovered.get_json()["next_workout"]["exercises"][0]
    assert recovered_exercise["target_sets"] == 3

    stored_after_recovery = data_store.get_current_workout_plan(state["user_id"])
    assert stored_after_recovery is not None
    assert stored_after_recovery["plan"]["exercises"][0]["target_sets"] == 3
