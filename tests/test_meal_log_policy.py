"""Unit tests for the FIT-61 meal auto-log policy.

The policy is pure — these tests exercise it directly without spinning
up Flask. Endpoint integration coverage lives in test_meal_intake_stub.py.
"""
from __future__ import annotations

import importlib

import pytest


def _import_policy():
    return importlib.import_module("meal_log_policy")


def _good_estimate(**overrides):
    """A sanitized estimate that should auto-log unless overrides change it."""
    base = {
        "item_name": "Eggs and toast",
        "portion_description": None,
        "meal_type": "breakfast",
        "calories": 420,
        "protein_g": 24,
        "carbs_g": 36,
        "fat_g": 18,
        "sodium_mg": 520,
        "fiber_g": 4,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "ai_text_estimate",
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────
# Auto-log path
# ──────────────────────────────────────────────────────────────────

def test_high_confidence_unambiguous_estimate_auto_logs():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(confidence=0.85))
    assert decision["status"] == policy.STATUS_LOGGED
    assert decision["correction_state"] == policy.CORRECTION_STATE_ACCEPTED
    assert decision["confidence_band"] == policy.BAND_HIGH
    assert decision["reasons"] == []


def test_exactly_threshold_confidence_auto_logs():
    policy = _import_policy()
    # 0.75 is the auto-log threshold — must be inclusive.
    decision = policy.evaluate_meal_log(_good_estimate(confidence=policy.HIGH_CONFIDENCE_THRESHOLD))
    assert decision["status"] == policy.STATUS_LOGGED
    assert decision["confidence_band"] == policy.BAND_HIGH


# ──────────────────────────────────────────────────────────────────
# Pending review — confidence bands
# ──────────────────────────────────────────────────────────────────

def test_just_below_threshold_falls_to_pending_with_medium_band():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(confidence=0.74))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert decision["correction_state"] == policy.CORRECTION_STATE_PENDING_REVIEW
    assert decision["confidence_band"] == policy.BAND_MEDIUM
    assert policy.REASON_MEDIUM_CONFIDENCE in decision["reasons"]


def test_low_confidence_falls_to_pending_with_low_band():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(confidence=0.30))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert decision["confidence_band"] == policy.BAND_LOW
    assert policy.REASON_LOW_CONFIDENCE in decision["reasons"]


def test_zero_confidence_falls_to_pending():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(confidence=0.0))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert decision["confidence_band"] == policy.BAND_LOW


def test_fallback_confidence_band_is_medium_or_low():
    """FIT-59's deterministic fallback returns 0.60 for matched presets.
    Policy must route that through pending review (not auto-log).
    """
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(confidence=0.60))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert decision["confidence_band"] == policy.BAND_MEDIUM


# ──────────────────────────────────────────────────────────────────
# Pending review — ambiguous flag
# ──────────────────────────────────────────────────────────────────

def test_ambiguous_flag_forces_pending_even_at_high_confidence():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(confidence=0.95, ambiguous=True))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert policy.REASON_AMBIGUOUS_INPUT in decision["reasons"]
    # Confidence band still reflects the input
    assert decision["confidence_band"] == policy.BAND_HIGH


def test_ambiguous_low_confidence_combines_reasons():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(confidence=0.40, ambiguous=True))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert policy.REASON_LOW_CONFIDENCE in decision["reasons"]
    assert policy.REASON_AMBIGUOUS_INPUT in decision["reasons"]


# ──────────────────────────────────────────────────────────────────
# Pending review — macro plausibility
# ──────────────────────────────────────────────────────────────────

def test_implausible_calories_forces_pending():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(calories=99999))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert policy.REASON_IMPLAUSIBLE_CALORIES in decision["reasons"]


def test_negative_calories_forces_pending():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(calories=-50))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert policy.REASON_IMPLAUSIBLE_CALORIES in decision["reasons"]


def test_missing_calories_forces_pending():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(calories=None))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert policy.REASON_MISSING_CALORIES in decision["reasons"]


@pytest.mark.parametrize("macro_key", ["protein_g", "carbs_g", "fat_g", "fiber_g"])
def test_implausible_macros_force_pending(macro_key):
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(**{macro_key: 9999}))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert policy.REASON_IMPLAUSIBLE_MACROS in decision["reasons"]


def test_implausible_sodium_forces_pending():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(sodium_mg=99999))
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    assert policy.REASON_IMPLAUSIBLE_SODIUM in decision["reasons"]


def test_macro_at_threshold_is_accepted():
    """The upper bound is inclusive — exactly the threshold must NOT
    flip the decision to pending. Off-by-one guard."""
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(
        calories=policy.CALORIE_MAX,
        protein_g=policy.MACRO_GRAM_MAX,
        sodium_mg=policy.SODIUM_MG_MAX,
    ))
    assert decision["status"] == policy.STATUS_LOGGED


# ──────────────────────────────────────────────────────────────────
# Defensive — malformed input
# ──────────────────────────────────────────────────────────────────

def test_non_dict_input_is_pending():
    policy = _import_policy()
    decision = policy.evaluate_meal_log(None)
    assert decision["status"] == policy.STATUS_PENDING_REVIEW
    decision = policy.evaluate_meal_log("not an estimate")
    assert decision["status"] == policy.STATUS_PENDING_REVIEW


def test_string_numeric_fields_are_coerced():
    """LLM output is JSON, so numbers should already be numeric, but be
    defensive about string-typed numbers from misbehaving models."""
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(confidence="0.85", calories="420"))
    # Implementation choice: string confidence coerces; otherwise falls to pending.
    # Either is acceptable; assert just that it doesn't raise.
    assert decision["status"] in (policy.STATUS_LOGGED, policy.STATUS_PENDING_REVIEW)


def test_policy_decision_shape_is_stable():
    """Every decision must include exactly the keys the endpoint relies on."""
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate())
    required_keys = {"status", "correction_state", "confidence_band", "confidence", "reasons"}
    assert set(decision.keys()) == required_keys
    assert isinstance(decision["reasons"], list)
    assert isinstance(decision["confidence"], (int, float))


def test_reasons_are_string_codes_not_user_copy():
    """Reasons must be stable codes the UI can map. Free-form sentences
    would couple the policy to UI copy and break i18n later."""
    policy = _import_policy()
    decision = policy.evaluate_meal_log(_good_estimate(
        confidence=0.40, ambiguous=True, calories=99999,
    ))
    for code in decision["reasons"]:
        assert isinstance(code, str)
        # Stable codes use snake_case; sentences have spaces.
        assert " " not in code, f"reason looks like a sentence, not a code: {code!r}"
