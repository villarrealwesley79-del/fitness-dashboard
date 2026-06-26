from __future__ import annotations

from whoop_recommendations import apply_wearable_modifiers, build_whoop_recommendation_signals


def test_low_recovery_and_sleep_gap_reduce_workout_and_explain_fueling():
    signals = build_whoop_recommendation_signals(
        {
            "recovery_score": 38,
            "sleep_performance_pct": 69,
            "sleep_need_gap_min": 95,
            "strain": 18.2,
            "score_state": "SCORED",
        },
        freshness={"status": "fresh"},
    )

    adjusted = apply_wearable_modifiers(
        "intensity",
        {
            "estimated_minutes": 60,
            "estimated_duration": "60 min",
            "mesocycle": {"volume_multiplier": 1.0},
            "exercises": [
                {"target_sets": 4, "rpe_target": 8, "rationale": "Base"},
                {"target_sets": 3, "rpe_target": 7, "rationale": "Base"},
            ],
        },
        whoop_signals=signals,
        source_conflict={"has_conflict": False},
    )

    assert "deload" in signals["applied_modifiers"]
    assert "fuel_up" in signals["applied_modifiers"]
    assert "eat_less" not in signals["applied_modifiers"]
    assert adjusted["recommendation"] == "recovery"
    assert adjusted["next_workout"]["exercises"][0]["target_sets"] < 4
    assert adjusted["next_workout"]["exercises"][0]["rpe_target"] < 8
    assert any("sleep need gap" in explanation.lower() for explanation in signals["explanations"])


def test_deload_modifier_never_increases_one_set_exercise():
    signals = build_whoop_recommendation_signals(
        {
            "recovery_score": 38,
            "score_state": "SCORED",
        },
        freshness={"status": "fresh"},
    )

    adjusted = apply_wearable_modifiers(
        "moderate",
        {"exercises": [{"target_sets": 1, "rpe_target": 7}]},
        whoop_signals=signals,
        source_conflict={"has_conflict": False},
    )

    assert adjusted["next_workout"]["exercises"][0]["target_sets"] == 1


def test_same_whoop_modifier_signature_is_idempotent():
    signals = build_whoop_recommendation_signals(
        {
            "recovery_score": 38,
            "score_state": "SCORED",
        },
        freshness={"status": "fresh"},
    )
    base = {"estimated_minutes": 60, "exercises": [{"target_sets": 4, "rpe_target": 8}]}

    first = apply_wearable_modifiers(
        "moderate",
        base,
        whoop_signals=signals,
        source_conflict={"has_conflict": False},
    )
    second = apply_wearable_modifiers(
        "moderate",
        first["next_workout"],
        whoop_signals=signals,
        source_conflict={"has_conflict": False},
    )

    assert second["next_workout"]["exercises"][0]["target_sets"] == first["next_workout"]["exercises"][0]["target_sets"]
    assert second["next_workout"]["exercises"][0]["rpe_target"] == first["next_workout"]["exercises"][0]["rpe_target"]
    assert second["next_workout"]["estimated_minutes"] == first["next_workout"]["estimated_minutes"]


def test_source_conflict_caution_modifier_is_idempotent():
    signals = build_whoop_recommendation_signals(
        {
            "recovery_score": 70,
            "score_state": "SCORED",
        },
        freshness={"status": "fresh"},
    )
    base = {"estimated_minutes": 60, "exercises": [{"target_sets": 4, "rpe_target": 8}]}
    conflict = {"has_conflict": True, "conservative_source": "whoop"}

    first = apply_wearable_modifiers(
        "intensity",
        base,
        whoop_signals=signals,
        source_conflict=conflict,
    )
    second = apply_wearable_modifiers(
        "intensity",
        first["next_workout"],
        whoop_signals=signals,
        source_conflict=conflict,
    )

    assert first["applied_modifiers"] == ["caution"]
    assert second["next_workout"]["exercises"][0]["target_sets"] == first["next_workout"]["exercises"][0]["target_sets"]
    assert second["next_workout"]["exercises"][0]["rpe_target"] == first["next_workout"]["exercises"][0]["rpe_target"]
    assert second["next_workout"]["estimated_minutes"] == first["next_workout"]["estimated_minutes"]


def test_stale_or_unscored_data_is_display_only():
    signals = build_whoop_recommendation_signals(
        {
            "recovery_score": 80,
            "sleep_performance_pct": 92,
            "score_state": "PENDING_SCORE",
        },
        freshness={"status": "stale"},
    )

    adjusted = apply_wearable_modifiers(
        "moderate",
        {"exercises": [{"target_sets": 3, "rpe_target": 7}]},
        whoop_signals=signals,
        source_conflict={"has_conflict": False},
    )

    assert signals["display_only"] is True
    assert adjusted["recommendation"] == "moderate"
    assert adjusted["applied_modifiers"] == []
    assert adjusted["next_workout"]["exercises"][0]["target_sets"] == 3
