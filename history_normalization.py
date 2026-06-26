"""Canonical training-history labels for Fitness Dashboard.

The UI can preserve source-specific labels, but analytics and AI-facing
history need one stable category for equivalent strength sessions.
"""

from __future__ import annotations


_STRENGTH_LABELS = {
    "lifted",
    "functional strength training",
    "traditional strength training",
    "strength training",
    "weight training",
    "resistance training",
}


def _normalise_label(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def canonical_training_category(label: str | None, source: str | None = None) -> str:
    """Return the category used for History, Stats, recommendations, and AI."""
    normalized = _normalise_label(label)
    source_norm = _normalise_label(source)
    if normalized in _STRENGTH_LABELS:
        return "strength_training"
    if "strength" in normalized or "weight" in normalized or "resistance" in normalized:
        return "strength_training"
    if source_norm == "lifted":
        return "strength_training"
    return normalized.replace(" ", "_") if normalized else "unknown"


def history_source_label(item: dict | None) -> str:
    """Human-readable provenance label for a normalized history row."""
    if not isinstance(item, dict):
        return "Unknown"
    source = _normalise_label(item.get("source"))
    if source == "lifted":
        return "Strength - Logged"
    if source in {"watch", "apple health", "apple_health"}:
        return "Strength - Watch" if canonical_training_category(
            item.get("activity_type") or item.get("activity") or item.get("session_type"),
            source,
        ) == "strength_training" else "Watch"
    provider = item.get("provider") or item.get("provider_id")
    if provider:
        return f"Strength - {provider}" if canonical_training_category(
            item.get("activity_type") or item.get("activity") or item.get("session_type"),
            source,
        ) == "strength_training" else str(provider)
    return "Logged"


def normalize_history_item(item: dict | None, *, source: str | None = None) -> dict:
    """Return a copy with canonical and provenance fields added."""
    row = dict(item or {})
    row_source = source or row.get("source")
    if row_source == "lifted":
        original = row.get("original_label") or "Lifted"
    else:
        original = row.get("original_label") or row.get("activity_type") or row.get("activity") or row.get("session_type")
    row["original_label"] = original or "Unknown"
    row["canonical_category"] = canonical_training_category(row["original_label"], row_source)
    row["source_label"] = history_source_label({**row, "source": row_source})
    return row
