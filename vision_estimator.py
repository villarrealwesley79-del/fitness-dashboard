"""Provider-neutral food-photo vision estimator."""

from __future__ import annotations

import os
from typing import Any

import claude_vision_adapter
import local_vision_adapter


DEFAULT_PROVIDER = "lm_studio"
SUPPORTED_PROVIDERS = {"claude", "lm_studio", "ollama"}


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
    if provider == "disabled":
        raise VisionEstimatorError("vision provider disabled; set VISION_ESTIMATOR_PROVIDER=lm_studio, ollama, or claude")
    if provider not in SUPPORTED_PROVIDERS:
        raise VisionEstimatorError(f"unsupported vision provider: {provider}")
    try:
        if provider == "claude":
            result = claude_vision_adapter.describe_food_photo(
                image_bytes,
                context_text=context_text,
                media_type=media_type,
            )
        else:
            result = local_vision_adapter.describe_food_photo(
                image_bytes,
                context_text=context_text,
                media_type=media_type,
                provider=provider,
            )
    except claude_vision_adapter.ClaudeVisionError as exc:
        raise VisionEstimatorError(str(exc)) from exc
    except local_vision_adapter.LocalVisionError as exc:
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
    items = _clean_items(result.get("items"))
    if items:
        cleaned["items"] = items
    return cleaned


def _clean_items(raw_items) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    cleaned = []
    for raw in raw_items[:10]:
        if not isinstance(raw, dict):
            continue
        item_name = str(raw.get("item_name") or raw.get("name") or "").strip()
        if not item_name:
            continue
        item: dict[str, Any] = {"item_name": item_name[:120]}
        brand = str(raw.get("brand") or "").strip()
        if brand:
            item["brand"] = brand[:80]
        item["quantity"] = _clean_quantity(raw.get("quantity", 1))
        modifiers = _clean_modifiers(raw.get("modifiers"))
        if modifiers:
            item["modifiers"] = modifiers
        portion_hint = str(raw.get("portion_hint") or "").strip()
        if portion_hint:
            item["portion_hint"] = portion_hint[:160]
        cleaned.append(item)
    return cleaned


def _clean_quantity(value) -> int | float:
    try:
        quantity = float(value)
    except (TypeError, ValueError):
        quantity = 1.0
    if quantity <= 0:
        quantity = 1.0
    quantity = min(quantity, 20.0)
    if quantity.is_integer():
        return int(quantity)
    return round(quantity, 2)


def _clean_modifiers(raw_modifiers) -> list[str]:
    if isinstance(raw_modifiers, list):
        values = raw_modifiers
    elif isinstance(raw_modifiers, str):
        values = [raw_modifiers]
    else:
        return []
    modifiers = []
    for raw in values[:8]:
        modifier = str(raw or "").strip()
        if modifier:
            modifiers.append(modifier[:80])
    return modifiers
