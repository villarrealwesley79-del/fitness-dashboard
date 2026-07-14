"""Regression tests pinning fixes for the three adversarial PR review findings
raised against FIT-256 (villarrealwesley79/fit-256-eliminate-multi-worker-
global-state-corruption-plan):

1. stale-plan poisoning in smart_recommendation_api() across a fingerprint
   (e.g. calendar-day) rollover.
2. durable wearable-modifier ratchet in api_dashboard()/api_next_workout()
   permanently reducing target_sets after a transient WHOOP deload/caution
   reading.
2b. (follow-up to the finding-2 fix) display-time inconsistency: after making
   the base plan canonical and only re-applying the wearable modifier at
   display time in /api/next-workout and /api/dashboard, every OTHER surface
   that renders the persisted plan (/gym-now, /api/workout/swap,
   /api/workout/adjust) showed the un-clamped BASE sets/RPE during an active
   deload -- telling the user to do MORE than the recovery-signalling routes.

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

    evaluated = client.post("/api/workout-adaptation-events/evaluate")
    smart = client.get("/api/recommendation/smart")

    assert evaluated.status_code == 200
    assert evaluated.get_json()["evaluated_count"] == 1
    assert smart.status_code == 200
    smart_payload = smart.get_json()
    # The resurrected/stale "Incline Press" plan must NOT be served.
    assert smart_payload["next_workout"]["exercises"][0]["exercise"] != "Incline Press"
    assert smart_payload["next_workout"]["id"] == "day2-fresh-plan"
    assert smart_payload["workout_adaptation_events"] == []

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


def _install_active_deload(monkeypatch, module):
    """Wire up a real, reducing WHOOP deload modifier (not an identity stub)."""
    monkeypatch.setattr(module, "apply_wearable_modifiers", whoop_recommendations.apply_wearable_modifiers)

    def whoop_context(_readiness):
        return {
            "signals": {"applied_modifiers": ["deload"], "explanations": []},
            "source_conflict": {},
        }

    monkeypatch.setattr(module, "_whoop_recommendation_context", whoop_context)


def test_gym_now_swap_adjust_show_same_clamped_plan_as_next_workout_under_deload(monkeypatch, tmp_path):
    """FIT-256 finding 2 follow-up regression.

    With an ACTIVE WHOOP deload, /gym-now, /api/workout/swap and
    /api/workout/adjust must render the SAME clamped target_sets/rpe as
    /api/next-workout for untouched exercises. Before this fix those surfaces
    rendered the persisted BASE plan directly and showed un-clamped values --
    the opposite of a recovery signal. The canonical persisted plan stays the
    base plan (display-only transform), which the finding-2 test above pins.
    """
    module, client, state = _client(monkeypatch, tmp_path)
    _install_active_deload(monkeypatch, module)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))
    # Deterministic adjust fallback (no live LLM) so /api/workout/adjust returns
    # the persisted plan through its display path.
    monkeypatch.setattr(module, "_lm_studio", None)

    # /api/next-workout is the reference surface: it persists the base plan and
    # applies the deload clamp for display. Exercise index 1 (Lat Pulldown) is
    # left untouched by the swap below, so it's our cross-surface comparison.
    reference = client.get("/api/next-workout")
    assert reference.status_code == 200
    ref_exercises = reference.get_json()["next_workout"]["exercises"]
    clamped_sets = ref_exercises[1]["target_sets"]
    clamped_rpe = ref_exercises[1]["rpe_target"]
    # Sanity: the deload actually reduced the base (3 sets / rpe 7).
    assert clamped_sets == 2
    assert clamped_rpe == 6

    # The durable row must still hold the BASE plan (finding-2 invariant).
    stored = data_store.get_current_workout_plan(state["user_id"])
    assert stored["plan"]["exercises"][1]["target_sets"] == 3

    # /gym-now renders the persisted plan as HTML; it must show the clamped
    # "2 x 10" for the untouched exercise, not the base "3 x 10".
    gym = client.get("/gym-now")
    assert gym.status_code == 200
    gym_html = gym.get_data(as_text=True)
    assert f"{clamped_sets} x 10" in gym_html
    assert "3 x 10" not in gym_html

    # /api/workout/swap returns the plan after swapping exercise index 0; the
    # untouched exercise index 1 must show the same clamped values as the
    # reference /api/next-workout, not the base.
    swap = client.post(
        "/api/workout/swap",
        json={"workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    assert swap.status_code == 200
    swap_untouched = swap.get_json()["recommendation"]["exercises"][1]
    assert swap_untouched["exercise"] == "Lat Pulldown"
    assert swap_untouched["target_sets"] == clamped_sets
    assert swap_untouched["rpe_target"] == clamped_rpe

    # The swap must NOT have durably persisted the clamped view (still base).
    stored_after_swap = data_store.get_current_workout_plan(state["user_id"])
    assert stored_after_swap["plan"]["exercises"][1]["target_sets"] == 3

    # /api/workout/adjust returns the (unchanged) plan through its fallback
    # display path; untouched exercise must also be clamped consistently.
    adjust = client.post("/api/workout/adjust", json={"constraint": "make this easier"})
    assert adjust.status_code == 200
    adjust_exercises = adjust.get_json()["recommendation"]["exercises"]
    assert adjust_exercises[1]["target_sets"] == clamped_sets
    assert adjust_exercises[1]["rpe_target"] == clamped_rpe

    # And the durable row is still the base plan after adjust.
    stored_after_adjust = data_store.get_current_workout_plan(state["user_id"])
    assert stored_after_adjust["plan"]["exercises"][1]["target_sets"] == 3


def test_complete_workout_scores_adherence_against_deloaded_view(monkeypatch, tmp_path):
    """FIT-256 finding 2 follow-up regression (adherence consistency).

    Under an active deload the user is shown a clamped plan (2 sets). When they
    complete exactly those 2 sets, /api/complete-workout must NOT flag them for
    "missed sets" against the un-clamped base plan (3 sets). Before this fix the
    canonical base plan drove adherence, so a deload-compliant user was penalised.
    """
    module, client, _state = _client(monkeypatch, tmp_path)
    _install_active_deload(monkeypatch, module)
    monkeypatch.setattr(module, "generate_next_workout", lambda *args, **kwargs: _recommendation(module))

    created = client.get("/api/next-workout")
    assert created.status_code == 200
    # The user sees the clamped prescription: 2 sets for Chest Press.
    assert created.get_json()["next_workout"]["exercises"][0]["target_sets"] == 2

    # They complete exactly the clamped 2 sets of Chest Press.
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
    modified = completed.get_json()["adherence"]["modified"]
    chest_changes = [m for m in modified if m.get("exercise") == "Chest Press"]
    # Doing the deload-clamped 2 sets must not be recorded as "missed sets".
    for change in chest_changes:
        assert "missed sets" not in (change.get("reason") or "")


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
    _install_active_deload(monkeypatch, module)

    # Simulate what the legacy persist=True path would have stored: the raw
    # BASE plan (3 sets), not the deload-clamped display view (2 sets), with
    # no matching row ever written to the fingerprint-scoped current-plan
    # store (no /api/next-workout call precedes this).
    stale_recommendation = _recommendation(module)
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [stale_recommendation])

    # Complete only the deload-clamped 2 sets of Chest Press; Lat Pulldown
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
