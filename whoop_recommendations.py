from __future__ import annotations

import copy


def _band_from_oura(readiness: int | float | None) -> str | None:
    try:
        value = float(readiness)
    except Exception:
        return None
    if value < 65:
        return "low"
    if value < 80:
        return "medium"
    return "high"


def _band_from_whoop(recovery_score: int | float | None) -> str | None:
    try:
        value = float(recovery_score)
    except Exception:
        return None
    if value < 45:
        return "low"
    if value < 67:
        return "medium"
    return "high"


def _downgrade_training_recommendation(recommendation: str | None) -> str:
    current = recommendation or "moderate"
    if current == "intensity":
        return "moderate"
    if current == "moderate":
        return "recovery"
    return current


def build_whoop_recommendation_signals(
    fact: dict | None,
    *,
    freshness: dict | None = None,
) -> dict:
    freshness_status = (freshness or {}).get("status") or "missing"
    if not fact:
        return {
            "source": "whoop",
            "freshness_status": freshness_status,
            "score_state": None,
            "display_only": True,
            "applied_modifiers": [],
            "explanations": [],
            "bounded_confidence": "none",
            "load_source": "apple_health",
        }

    score_state = (fact.get("score_state") or "").upper() or None
    display_only = freshness_status not in {"fresh", "aging"} or score_state != "SCORED"
    modifiers: list[str] = []
    explanations: list[str] = []

    recovery_score = fact.get("recovery_score")
    sleep_performance = fact.get("sleep_performance_pct")
    sleep_need_gap_min = fact.get("sleep_need_gap_min")
    strain = fact.get("strain")

    if display_only:
        explanations.append("WHOOP is available for context only until fresh scored data lands.")
    else:
        try:
            recovery_value = float(recovery_score)
        except Exception:
            recovery_value = None
        try:
            sleep_value = float(sleep_performance)
        except Exception:
            sleep_value = None
        try:
            strain_value = float(strain)
        except Exception:
            strain_value = None
        try:
            sleep_gap_value = float(sleep_need_gap_min)
        except Exception:
            sleep_gap_value = None

        if recovery_value is not None and recovery_value < 45:
            modifiers.append("deload")
            explanations.append(f"WHOOP recovery {int(round(recovery_value))} suggests reducing intensity.")
        elif recovery_value is not None and recovery_value < 60:
            modifiers.append("caution")
            explanations.append(f"WHOOP recovery {int(round(recovery_value))} suggests a conservative session.")

        if strain_value is not None and strain_value >= 18 and "deload" not in modifiers:
            modifiers.append("deload")
            explanations.append(f"WHOOP strain {round(strain_value, 1)} is already high today.")
        elif strain_value is not None and strain_value >= 15 and "caution" not in modifiers:
            modifiers.append("caution")
            explanations.append(f"WHOOP strain {round(strain_value, 1)} supports dialing intensity down.")

        if sleep_value is not None and sleep_value < 70 and "deload" not in modifiers:
            modifiers.append("deload")
            explanations.append(f"WHOOP sleep performance {int(round(sleep_value))}% is meaningfully low.")
        elif sleep_value is not None and sleep_value < 85 and "caution" not in modifiers:
            modifiers.append("caution")
            explanations.append(f"WHOOP sleep performance {int(round(sleep_value))}% is below target.")

        if sleep_gap_value is not None and sleep_gap_value >= 60:
            modifiers.append("sleep_priority")
            modifiers.append("fuel_up")
            explanations.append(f"WHOOP sleep need gap is {int(round(sleep_gap_value))} minutes.")

    ordered_modifiers: list[str] = []
    for modifier in modifiers:
        if modifier not in ordered_modifiers:
            ordered_modifiers.append(modifier)

    confidence = "low" if display_only else "high"
    if not display_only and freshness_status == "aging":
        confidence = "medium"

    return {
        "source": "whoop",
        "freshness_status": freshness_status,
        "score_state": score_state,
        "display_only": display_only,
        "applied_modifiers": ordered_modifiers,
        "explanations": explanations,
        "bounded_confidence": confidence,
        "load_source": "apple_health",
        "recovery_band": _band_from_whoop(recovery_score),
        "recovery_score": recovery_score,
    }


def detect_wearable_source_conflicts(
    *,
    oura_readiness: int | float | None,
    whoop_signals: dict | None,
) -> dict:
    whoop_band = (whoop_signals or {}).get("recovery_band")
    oura_band = _band_from_oura(oura_readiness)
    if not whoop_band or not oura_band or (whoop_signals or {}).get("display_only"):
        return {
            "has_conflict": False,
            "oura_band": oura_band,
            "whoop_band": whoop_band,
            "conservative_source": None,
            "severity": "none",
            "explanation": None,
        }

    band_order = {"low": 0, "medium": 1, "high": 2}
    difference = abs(band_order[oura_band] - band_order[whoop_band])
    if difference <= 1:
        return {
            "has_conflict": False,
            "oura_band": oura_band,
            "whoop_band": whoop_band,
            "conservative_source": None,
            "severity": "none",
            "explanation": None,
        }

    conservative_source = "whoop" if band_order[whoop_band] < band_order[oura_band] else "oura"
    return {
        "has_conflict": True,
        "oura_band": oura_band,
        "whoop_band": whoop_band,
        "conservative_source": conservative_source,
        "severity": "warning",
        "explanation": (
            f"Oura reads {oura_band} while WHOOP reads {whoop_band}; keeping the more conservative plan."
        ),
    }


def apply_wearable_modifiers(
    recommendation: str | None,
    next_workout: dict | None,
    *,
    whoop_signals: dict | None,
    source_conflict: dict | None = None,
) -> dict:
    signals = whoop_signals or {}
    conflict = source_conflict or {}
    workout = copy.deepcopy(next_workout or {})
    training_recommendation = recommendation or "moderate"
    modifiers = list(signals.get("applied_modifiers") or [])

    if signals.get("display_only"):
        return {
            "recommendation": training_recommendation,
            "next_workout": workout,
            "applied_modifiers": [],
            "display_only": True,
            "explanations": signals.get("explanations") or [],
            "source_conflict": conflict,
            "load_source": "apple_health",
        }

    if conflict.get("has_conflict") and conflict.get("conservative_source") == "whoop" and "caution" not in modifiers and "deload" not in modifiers:
        modifiers.append("caution")

    modifier_signature = "|".join(sorted(str(item) for item in modifiers))
    if modifier_signature and workout.get("_whoop_modifier_signature") == modifier_signature:
        return {
            "recommendation": training_recommendation,
            "next_workout": workout,
            "applied_modifiers": modifiers,
            "display_only": False,
            "explanations": signals.get("explanations") or [],
            "source_conflict": conflict,
            "load_source": "apple_health",
        }

    volume_scale = 1.0
    rpe_delta = 0
    if "deload" in modifiers:
        training_recommendation = "recovery"
        volume_scale = 0.8
        rpe_delta = -1
    elif "caution" in modifiers:
        training_recommendation = _downgrade_training_recommendation(training_recommendation)
        volume_scale = 0.9
        rpe_delta = -1

    exercises = workout.get("exercises")
    if isinstance(exercises, list) and volume_scale < 1.0:
        for exercise in exercises:
            if not isinstance(exercise, dict):
                continue
            sets = exercise.get("target_sets")
            if isinstance(sets, (int, float)):
                original_sets = max(1, int(round(float(sets))))
                scaled_sets = max(1, int(round(float(sets) * volume_scale)))
                exercise["target_sets"] = min(original_sets, scaled_sets)
            rpe = exercise.get("rpe_target")
            if isinstance(rpe, (int, float)) and rpe_delta:
                exercise["rpe_target"] = max(5, round(float(rpe) + rpe_delta, 1))
            rationale = exercise.get("rationale") or ""
            note = "WHOOP modifier applied"
            if note not in rationale:
                exercise["rationale"] = f"{rationale} · {note}".strip(" ·")

    mesocycle = workout.get("mesocycle")
    if isinstance(mesocycle, dict) and volume_scale < 1.0:
        original = mesocycle.get("volume_multiplier")
        if isinstance(original, (int, float)):
            mesocycle["volume_multiplier"] = round(float(original) * volume_scale, 2)

    if isinstance(workout.get("estimated_minutes"), (int, float)) and volume_scale < 1.0:
        workout["estimated_minutes"] = max(20, int(round(float(workout["estimated_minutes"]) * volume_scale)))
        workout["estimated_duration"] = f"{workout['estimated_minutes']} min"

    if modifier_signature:
        workout["_whoop_modifier_signature"] = modifier_signature

    return {
        "recommendation": training_recommendation,
        "next_workout": workout,
        "applied_modifiers": modifiers,
        "display_only": False,
        "explanations": signals.get("explanations") or [],
        "source_conflict": conflict,
        "load_source": "apple_health",
    }
