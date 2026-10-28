"""Regression tests pinning fixes for the three adversarial PR review findings
raised against FIT-256 (villarrealwesley79/fit-256-eliminate-multi-worker-
global-state-corruption-plan):

1. stale-plan poisoning in smart_recommendation_api() across a fingerprint
   (e.g. calendar-day) rollover.
2. durable wearable-modifier ratchet in api_dashboard()/api_next_workout()
   permanently capping RPE after a transient Open Wearables recovery reading.
2b. (follow-up to the finding-2 fix) display-time inconsistency: after making
   the base plan canonical and only re-applying the wearable modifier at
   display time in /api/next-workout and /api/dashboard, every OTHER surface
   that renders the persisted plan (/gym-now, /api/workout/swap,
   /api/workout/adjust) showed the un-capped BASE RPE during an active
   recovery cap -- telling the user to do MORE than the recovery-signalling routes.

Each test is written to FAIL against the pre-fix branch code and PASS after
the fix.
"""

from __future__ import annotations

import copy

import data_store

from test_fit256_workout_plan_persistence import _client, _recommendation


def test_smart_recommendation_does_not_resurrect_stale_plan_across_day_rollover(monkeypatch, tmp_path):
    """FIT-256 finding 1 regression.

    Before the fix, smart_recommendation_api() read the stored plan via
    `get_current_workout_plan(user_id)` with NO fingerprint filter, so once
    the fingerprint rolled over (new day / settings / sync -- the
    fingerprint embeds `day: today_s`) the endpoint would serve
    *yesterday's* plan and persist it re-stamped with *today's* fingerprint.
    That poisons every subsequent fingerprint-scoped read (including
    /api/next-workout) with the stale plan, seemingly forever, because it
    now satisfies the current-fingerprint lookup.
    """
    module, client, state = _client(monkeypatch, tmp_path)

    # smart_recommendation_api() (unlike /api/next-workout) reads
    # open_wearables_modifier.get(...), which the default _client() None stub
    # doesn't support -- match test_fit256_smart_recommendation_no_clobber.py.
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
        json={"recommendation_id": "day1-plan", "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
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

    smart = client.get("/api/recommendation/smart")

    assert smart.status_code == 200
    smart_payload = smart.get_json()
    # The resurrected/stale "Incline Press" plan must NOT be served.
    assert smart_payload["next_workout"]["exercises"][0]["exercise"] != "Incline Press"
    assert smart_payload["next_workout"]["id"] == "day2-fresh-plan"

    # A subsequent /api/next-workout call must serve the freshly regenerated
    # plan (matching today's fingerprint), not the resurrected stale one.
    next_workout = client.get("/api/next-workout")
    assert next_workout.status_code == 200
    served = next_workout.get_json()["next_workout"]
    assert served["id"] == "day2-fresh-plan"
    assert served["exercises"][0]["exercise"] != "Incline Press"


def _hard_plan(module):
    plan = _recommendation(module)
    for exercise in plan["exercises"]:
        exercise["rpe_target"] = 8.5
    return plan


def _whoop_recovery_fact(score):
    return {
        "metric": "recovery_score",
        "provider_id": "whoop",
        "source_provider": "whoop",
        "date": "2026-10-28",
        "value": score,
    }


def _install_recovery_facts(monkeypatch, module, facts):
    """Feed real Open Wearables facts through the real guard (not a stub)."""
    monkeypatch.setattr(module, "_open_wearables_recommendation_facts", lambda: facts)
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda rec, f: module.open_wearables_hub.apply_recommendation_guard(
            rec, f, downgrade_once=module._downgrade_training_recommendation_once
        ),
    )


def test_recovery_rpe_cap_does_not_permanently_ratchet_rpe(monkeypatch, tmp_path):
    """FIT-256 finding 2 regression.

    The recovery RPE cap (WHOOP recovery 34-66 via Open Wearables) is a
    display-time transform: the canonical persisted plan must stay the
    base/programmed plan, and the cap must disappear when recovery normalizes.
    """
    module, client, state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _hard_plan(module))
    facts = [_whoop_recovery_fact(50)]
    _install_recovery_facts(monkeypatch, module, facts)

    capped = client.get("/api/next-workout")
    assert capped.status_code == 200
    assert capped.get_json()["next_workout"]["exercises"][0]["rpe_target"] == 7.0

    stored_after_cap = data_store.get_current_workout_plan(state["user_id"])
    assert stored_after_cap is not None
    assert stored_after_cap["plan"]["exercises"][0]["rpe_target"] == 8.5

    facts[:] = []
    recovered = client.get("/api/next-workout")
    assert recovered.status_code == 200
    assert recovered.get_json()["next_workout"]["exercises"][0]["rpe_target"] == 8.5

    stored_after_recovery = data_store.get_current_workout_plan(state["user_id"])
    assert stored_after_recovery["plan"]["exercises"][0]["rpe_target"] == 8.5


def test_gym_now_swap_adjust_show_same_capped_plan_as_next_workout(monkeypatch, tmp_path):
    """FIT-256 finding 2 follow-up regression.

    With an ACTIVE recovery cap, /gym-now, /api/workout/swap and
    /api/workout/adjust must render the SAME capped RPE as /api/next-workout
    for untouched exercises, while the durable row keeps the base plan.
    """
    module, client, state = _client(monkeypatch, tmp_path)
    _install_recovery_facts(monkeypatch, module, [_whoop_recovery_fact(50)])
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _hard_plan(module))

    reference = client.get("/api/next-workout")
    assert reference.status_code == 200
    ref_exercises = reference.get_json()["next_workout"]["exercises"]
    assert ref_exercises[1]["rpe_target"] == 7.0

    stored = data_store.get_current_workout_plan(state["user_id"])
    assert stored["plan"]["exercises"][1]["rpe_target"] == 8.5

    gym = client.get("/gym-now")
    assert gym.status_code == 200
    gym_html = gym.get_data(as_text=True)
    assert "RPE 7.0" in gym_html
    assert "RPE 8.5" not in gym_html

    swap = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "fit-256-plan", "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    assert swap.status_code == 200
    swap_untouched = swap.get_json()["recommendation"]["exercises"][1]
    assert swap_untouched["exercise"] == "Lat Pulldown"
    assert swap_untouched["rpe_target"] == 7.0

    stored_after_swap = data_store.get_current_workout_plan(state["user_id"])
    assert stored_after_swap["plan"]["exercises"][1]["rpe_target"] == 8.5

    adjust = client.post("/api/workout/adjust", json={"constraint": "make this easier"})
    assert adjust.status_code == 200
    assert adjust.get_json()["recommendation"]["exercises"][1]["rpe_target"] == 7.0

    stored_after_adjust = data_store.get_current_workout_plan(state["user_id"])
    assert stored_after_adjust["plan"]["exercises"][1]["rpe_target"] == 8.5


def test_complete_workout_ignores_stale_unadjusted_recommendations_list(monkeypatch, tmp_path):
    """FIT-256 follow-up (dead branch removal) regression.

    /api/complete-workout used to resolve `recommendation_id` first against
    the legacy in-process `WORKOUT_RECOMMENDATIONS` list, matching a `rec`
    directly WITHOUT running it through `_wearable_display_adjusted_plan`.
    That list is only ever populated by generate_next_workout(...,
    persist=True), which has zero call sites repo-wide, so the branch was
    dead -- but if it were ever fed data it held the raw un-clamped BASE
    plan, and a deload-compliant completion would have been misscored as
    "missed sets" / "skipped" against it.

    This test proves the branch is gone: even with a matching, un-adjusted
    entry sitting in `WORKOUT_RECOMMENDATIONS` (simulating what the dead
    `persist=True` path would have stored) and no corresponding row in the
    fingerprint-scoped current-plan store, the completion is no longer
    resolved against that stale entry. It correctly falls back to
    `followed: None` (untracked) instead of falsely penalising the user.
    """
    module, client, _state = _client(monkeypatch, tmp_path)
    _install_recovery_facts(monkeypatch, module, [_whoop_recovery_fact(50)])

    # Simulate what the legacy persist=True path would have stored: the raw
    # BASE plan (3 sets), not the capped display view (2 sets), with
    # no matching row ever written to the fingerprint-scoped current-plan
    # store (no /api/next-workout call precedes this).
    stale_recommendation = _recommendation(module)
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [stale_recommendation])

    # Complete only the capped 2 sets of Chest Press; Lat Pulldown
    # (present on the stale plan) is not touched at all.
    completed = client.post(
        "/api/complete-workout",
        json={
            "recommendation_id": "fit-256-plan",
            "exercises": [
                {
                    "machine": "Chest Press",
                    "sets": [
                        {"set_number": 1, "weight_lbs": 100, "reps": 10},
                        {"set_number": 2, "weight_lbs": 100, "reps": 10},
                    ],
                }
            ],
        },
    )
    assert completed.status_code == 200
    adherence = completed.get_json()["adherence"]
    # Before the fix: the stale un-adjusted entry was matched directly, so
    # Lat Pulldown was flagged "skipped" and followed=False -- both false
    # positives driven by data that was never wearable-adjusted for display.
    # After the fix: WORKOUT_RECOMMENDATIONS is never consulted, no
    # persisted current-plan row matches the id, so the completion is
    # correctly left untracked rather than penalised.
    assert adherence["followed"] is None
    assert adherence["skipped"] == []
    assert adherence["modified"] == []
    assert adherence["added"] == []
