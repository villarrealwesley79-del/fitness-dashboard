"""Reviewer tests (W4a r1): the WHOOP-via-OW recovery guard must never show
harder strength targets for a worse recovery than for a better one."""
from __future__ import annotations

import copy

import open_wearables_hub as hub

from test_fit256_workout_plan_persistence import _client, _recommendation


def _recovery(score):
    return {"metric": "recovery_score", "provider_id": "whoop", "source_provider": "whoop",
            "date": "2026-10-28", "value": score}


def _hard_plan(module):
    plan = _recommendation(module)
    for exercise in plan["exercises"]:
        exercise["rpe_target"] = 9.0
    return plan


def _max_rpe(plan):
    return max(ex["rpe_target"] for ex in plan["exercises"])


def test_display_rpe_is_monotonic_in_recovery(monkeypatch, tmp_path):
    module, _client_, _state = _client(monkeypatch, tmp_path)
    plan = _hard_plan(module)
    shown = {
        score: _max_rpe(module._wearable_adjusted_for_display(copy.deepcopy(plan), hub.conservative_modifier([_recovery(score)])))
        for score in (20, 33, 34, 50, 66, 67, 90)
    }
    # A worse recovery must never display a higher RPE than a better one.
    scores = sorted(shown)
    for worse, better in zip(scores, scores[1:]):
        assert shown[worse] <= shown[better], shown


def test_generated_strength_rpe_ignores_training_recommendation(monkeypatch, tmp_path):
    """Why 'downgrade once' alone does not lower strength targets."""
    module, _client_, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda **_k: None)
    plans = {
        rec: module.generate_next_workout([], [], training_recommendation=rec, consume_cardio_rotation=False)
        for rec in ("intensity", "moderate")
    }
    rpes = {rec: [ex.get("rpe_target") for ex in plan.get("exercises") or []] for rec, plan in plans.items()}
    assert rpes["intensity"], rpes
    assert rpes["intensity"] == rpes["moderate"], rpes


def test_next_workout_rpe_does_not_rise_when_recovery_drops_red(monkeypatch, tmp_path):
    """Plan persisted while recovery was yellow (50); WHOOP then reports red (20)."""
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "generate_next_workout", lambda *a, **k: _hard_plan(module))
    facts = [_recovery(50)]
    monkeypatch.setattr(module, "_open_wearables_recommendation_facts", lambda: facts)
    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        lambda rec, f: hub.apply_recommendation_guard(rec, f, downgrade_once=module._downgrade_training_recommendation_once),
    )

    yellow = client.get("/api/next-workout").get_json()["next_workout"]
    facts[:] = [_recovery(20)]
    red = client.get("/api/next-workout").get_json()["next_workout"]

    assert _max_rpe(yellow) == 7.0
    assert _max_rpe(red) <= _max_rpe(yellow), (_max_rpe(yellow), _max_rpe(red))
