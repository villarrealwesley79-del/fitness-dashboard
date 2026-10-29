"""Generic recommendation source proof for wearable-derived context."""

from __future__ import annotations


def whoop_recovery_value(fact) -> float | None:
    """The 0-100 score of a WHOOP recovery_score fact, else None.

    The one check the guard, the consumed test and the proof share: booleans and
    out-of-range scores are excluded, never clamped or counted as used.
    """
    if not isinstance(fact, dict) or fact.get("metric") != "recovery_score":
        return None
    if "whoop" not in {str(fact.get(key) or "").lower() for key in ("provider_id", "source_provider")}:
        return None
    value = fact.get("value")
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


def is_whoop_recovery(fact) -> bool:
    return whoop_recovery_value(fact) is not None


def consumed_facts(facts) -> list[dict]:
    """Facts the recommendation reads; HRV, RHR and SpO2 alone do not feed it."""
    return [
        f for f in facts or []
        if isinstance(f, dict) and (f.get("metric") in {"sleep_duration", "active_minutes"} or is_whoop_recovery(f))
    ]


def build_open_wearables_recommendation_source(
    freshness: dict | None,
    providers: list[dict] | None = None,
    facts: list[dict] | None = None,
    modifier: dict | None = None,
    load: dict | None = None,
) -> dict:
    freshness = freshness or {}
    providers = providers or []
    facts = facts or []
    modifier = modifier or {}
    status = freshness.get("status") or ("fresh" if facts else ("connected" if providers else "missing"))
    if facts and modifier.get("applied") and status == "missing":
        status = "fresh"
    consumed = consumed_facts(facts)
    used = (bool(consumed) and status in {"fresh", "aging", "connected"}) or bool(load)  # counted watch minutes are in the ACWR series
    provider_labels = [p.get("label") or p.get("provider_id") for p in providers if isinstance(p, dict)]
    detail = "Open Wearables is the wearable hub."
    if modifier.get("applied"):
        detail = modifier.get("detail") or "Open Wearables facts applied a conservative recommendation guard."
    elif consumed:
        detail = f"Open Wearables contributed {len(consumed)} normalized fact{'s' if len(consumed) != 1 else ''}."
    elif load:
        detail = "Logged and Open Wearables workout minutes are counted in the training-load ratio."
    elif facts:
        detail = f"Open Wearables has {len(facts)} fact{'s' if len(facts) != 1 else ''}; none feed today's recommendation."
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
        "facts_used": len(consumed),
        "applied_modifiers": modifier.get("applied_modifiers") or [],
    }


def build_recommendation_source_proof(
    *,
    freshness: dict,
    open_wearables_source: dict,
    source_inputs: dict | None = None,
) -> dict:
    """Return the FIT-271 proof contract for its four named sources.

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

    domain_sources = source_inputs.get("domain_sources") or {}
    domain_fields = source_inputs.get("domain_fields") or {}

    def domain_proof(domain, modifier_applied):
        """Provider, date and freshness behind one domain, and whether today's plan read it."""
        node = domain_sources.get(domain) or {}
        status = node.get("freshness") or "missing"
        fields = sorted({str(field) for field in domain_fields.get(domain) or [] if field})
        used = bool(fields) and status in {"fresh", "aging"}
        if used:
            ignored_reason = None
        elif not node:
            ignored_reason = "missing_data"
        elif status not in {"fresh", "aging"}:
            ignored_reason = f"{status}_source"
        else:
            ignored_reason = "not_used"
        return {
            "provider": node.get("provider"),
            "via": node.get("via") or "open_wearables",
            "date": node.get("date"),
            "freshness": status,
            "used_for_recommendation": used,
            "fields_used": fields if used else [],
            "modifier_applied": bool(modifier_applied and used),
            "ignored_reason": ignored_reason,
            "conflict_state": node.get("conflict_state"),
            **({"guard_source": node["guard_source"]} if node.get("guard_source") else {}),  # a guard fact from another provider
        }

    open_fields = [fact.get("metric") for fact in consumed_facts(source_inputs.get("open_wearables_facts"))]
    load = source_inputs.get("open_wearables_load")  # the ACWR comes from the minutes series with counted watch sessions: {providers, observed_at, modifier_applied}
    if load:
        open_fields.append("workout_duration")
    open_used = bool(open_fields) and bool(open_wearables_source.get("used_for_recommendation"))
    open_status = str(open_wearables_source.get("freshness") or "missing")
    freshness_with_open = dict(freshness)
    freshness_with_open["open_wearables"] = {"status": open_status}

    freshness = freshness_with_open
    result = {
        "open_wearables": proof(
            "open_wearables",
            open_fields,
            bool(source_inputs.get("open_wearables_modifier_applied") or (load or {}).get("modifier_applied")),
            used_override=open_used,
        ),
        **{
            domain: domain_proof(domain, bool((source_inputs.get("domain_modifier_applied") or {}).get(domain)))
            for domain in ("sleep", "recovery", "activity")
        },
        "weather": proof(
            "weather",
            source_inputs.get("weather_fields"),
            bool(source_inputs.get("weather_modifier_applied")),
        ),
    }
    if load:
        result["open_wearables"].update(providers=load.get("providers") or [], observed_at=load.get("observed_at"))
    return result
