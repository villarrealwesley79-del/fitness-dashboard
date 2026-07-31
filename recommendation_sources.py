"""Generic recommendation source proof for wearable-derived context."""

from __future__ import annotations


def build_open_wearables_recommendation_source(
    freshness: dict | None,
    providers: list[dict] | None = None,
    facts: list[dict] | None = None,
    modifier: dict | None = None,
) -> dict:
    freshness = freshness or {}
    providers = providers or []
    facts = facts or []
    modifier = modifier or {}
    status = freshness.get("status") or ("fresh" if facts else ("connected" if providers else "missing"))
    if facts and modifier.get("applied") and status == "missing":
        status = "fresh"
    used = bool(facts) and status in {"fresh", "aging", "connected"}
    provider_labels = [p.get("label") or p.get("provider_id") for p in providers if isinstance(p, dict)]
    detail = "Open Wearables is the wearable hub."
    if modifier.get("applied"):
        detail = modifier.get("detail") or "Open Wearables facts applied a conservative recommendation guard."
    elif facts:
        detail = f"Open Wearables contributed {len(facts)} normalized fact{'s' if len(facts) != 1 else ''}."
    elif provider_labels:
        detail = "Open Wearables hub providers visible: " + ", ".join(provider_labels[:4]) + ". No fresh normalized facts were applied."
    elif status in {"missing", "stale", "error", "blocked"}:
        detail = "Open Wearables has no fresh provider facts, so recommendations stay on existing sources."
    return {
        "key": "open_wearables",
        "source": "open_wearables",
        "provider": "open_wearables",
        "label": "Open Wearables",
        "role": "bounded modifier" if used else "display only",
        "freshness": status,
        "influence": "bounded_modifier" if used else "none",
        "detail": detail,
        "used_for_recommendation": used,
        "facts_used": len(facts),
        "applied_modifiers": modifier.get("applied_modifiers") or [],
    }


def conservative_conflict_message(conflicts: list[dict] | None) -> str | None:
    if not conflicts:
        return None
    first = conflicts[0]
    return first.get("message") or first.get("summary") or "Wearable sources disagree, so the recommendation stays conservative."


def build_recommendation_source_proof(
    *,
    freshness: dict,
    open_wearables_source: dict,
    whoop_signals: dict,
    source_inputs: dict | None = None,
) -> dict:
    """Return the FIT-271 proof contract for its six named external sources.

    Local soreness, workout-history, and recovery inputs remain exposed through
    the existing readiness_factors/reasoning contracts; FIT-271 does not replace
    those deterministic local-factor contracts.
    """
    source_inputs = source_inputs or {}

    def proof(source, fields, modifier_applied=False, *, display_only=False, used_override=None):
        node = freshness.get(source) or {}
        status = str(source_inputs.get(f"{source}_status") or node.get("status") or "missing")
        fields = sorted({str(field) for field in (fields or []) if field})
        usable = status not in {"missing", "stale", "error", "blocked"}
        used = bool(fields) and usable and not display_only
        if used_override is not None:
            used = bool(used_override)
        ignored_reason = None
        if not used:
            ignored_override = source_inputs.get(f"{source}_ignored_reason")
            if ignored_override:
                ignored_reason = str(ignored_override)
            elif display_only:
                ignored_reason = "display_only"
            elif node.get("superseded_by"):
                ignored_reason = f"superseded_by_{node.get('superseded_by')}"
            elif status in {"stale", "error", "blocked"}:
                ignored_reason = f"{status}_source"
            elif status == "missing" or not fields:
                ignored_reason = "missing_data"
            else:
                ignored_reason = "not_used"
        return {
            "used_for_recommendation": used,
            "fields_used": fields if used else [],
            "modifier_applied": bool(modifier_applied and used),
            "ignored_reason": ignored_reason,
        }

    open_fields = [
        fact.get("metric")
        for fact in source_inputs.get("open_wearables_facts") or []
        if isinstance(fact, dict) and fact.get("metric") in {"sleep_duration", "active_minutes"}
    ]
    open_used = bool(open_fields) and bool(open_wearables_source.get("used_for_recommendation"))
    open_status = str(open_wearables_source.get("freshness") or "missing")
    freshness_with_open = dict(freshness)
    freshness_with_open["open_wearables"] = {"status": open_status}

    whoop_fields = source_inputs.get("whoop_fields") or [
        field
        for field in (
            "recovery_score", "strain", "sleep_performance_pct", "sleep_need_gap_min"
        )
        if whoop_signals.get(field) is not None
    ]
    freshness = freshness_with_open
    result = {
        "open_wearables": proof(
            "open_wearables",
            open_fields,
            bool(source_inputs.get("open_wearables_modifier_applied")),
            used_override=open_used,
        ),
        "whoop": proof(
            "whoop",
            whoop_fields,
            bool(source_inputs.get("whoop_modifier_applied")),
            display_only=bool(whoop_signals.get("display_only")),
        ),
        "oura": proof(
            "oura",
            source_inputs.get("oura_fields"),
            bool(source_inputs.get("oura_modifier_applied")),
            used_override=source_inputs.get("oura_used_override"),
        ),
        "apple_health": proof(
            "apple_health",
            source_inputs.get("apple_health_fields"),
            bool(source_inputs.get("apple_health_modifier_applied")),
            used_override=source_inputs.get("apple_health_used_override"),
        ),
        "food": proof(
            "food",
            source_inputs.get("food_fields"),
            bool(source_inputs.get("food_modifier_applied")),
            used_override=source_inputs.get("food_used_override"),
        ),
        "weather": proof(
            "weather",
            source_inputs.get("weather_fields"),
            bool(source_inputs.get("weather_modifier_applied")),
        ),
    }
    return result
