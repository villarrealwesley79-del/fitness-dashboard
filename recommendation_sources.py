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
