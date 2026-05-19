"""Provider-neutral food-photo vision estimator."""

from __future__ import annotations

import os
from typing import Any

import claude_vision_adapter


DEFAULT_PROVIDER = "claude"
SUPPORTED_PROVIDERS = {"claude"}


class VisionEstimatorError(RuntimeError):
    """Raised when a configured vision provider cannot produce a description."""


def configured_provider() -> str:
    provider = os.environ.get("VISION_ESTIMATOR_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    return provider or DEFAULT_PROVIDER


def describe(
    image_bytes: bytes,
    *,
    context_text: str | None = None,
    media_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Describe a food image without retaining raw image bytes."""
    provider = configured_provider()
    if provider not in SUPPORTED_PROVIDERS:
        raise VisionEstimatorError(f"unsupported vision provider: {provider}")
    try:
        result = claude_vision_adapter.describe_food_photo(
            image_bytes,
            context_text=context_text,
            media_type=media_type,
        )
    except claude_vision_adapter.ClaudeVisionError as exc:
        raise VisionEstimatorError(str(exc)) from exc
    return _clean_description(result, provider=provider)


def _clean_description(result: dict[str, Any], *, provider: str) -> dict[str, Any]:
    description = str(result.get("item_description") or "").strip()
    if not description:
        raise VisionEstimatorError("vision result did not include an item description")
    confidence_raw = result.get("confidence", 0.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0.0
    notes = result.get("uncertainty_notes") or []
    if not isinstance(notes, list):
        notes = [str(notes)]
    cleaned = {
        "provider": provider,
        "item_description": description[:300],
        "portion_hint": str(result.get("portion_hint") or "").strip()[:160] or None,
        "confidence": round(confidence, 2),
        "ambiguous": bool(result.get("ambiguous", confidence < 0.65)),
        "uncertainty_notes": [str(note).strip() for note in notes if str(note).strip()],
    }
    macro_estimate = result.get("macro_estimate")
    if isinstance(macro_estimate, dict):
        cleaned["macro_estimate"] = dict(macro_estimate)
    return cleaned
