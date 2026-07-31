"""Provider-neutral food-photo vision estimator."""

from __future__ import annotations

import os
from typing import Any
from urllib import parse, request

import claude_vision_adapter
import local_vision_adapter


DEFAULT_PROVIDER = "lm_studio"
SUPPORTED_PROVIDERS = {"claude", "lm_studio", "ollama"}


class VisionEstimatorError(RuntimeError):
    """Raised when a configured vision provider cannot produce a description."""


def configured_provider() -> str:
    provider = os.environ.get("VISION_ESTIMATOR_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    return provider or DEFAULT_PROVIDER


def health_status() -> dict[str, Any]:
    """Return a read-only, public-safe snapshot of photo-analysis readiness."""
    provider = configured_provider()
    if provider == "lm_studio":
        candidates = []
        for candidate in local_vision_adapter._lm_studio_candidates():
            item: dict[str, Any] = {"role": candidate["role"], "reachable": False, "model_loaded": False}
            try:
                loaded = local_vision_adapter._models_for(candidate)
            except TimeoutError:
                item["error"] = "timeout"
            except Exception:
                item["error"] = "unreachable"
            else:
                item["reachable"] = True
                item["model_loaded"] = local_vision_adapter._target_loaded(loaded, candidate["model"])
            candidates.append(item)
        primary = next((item for item in candidates if item["role"] == "primary"), None)
        fallback = next((item for item in candidates if item["role"] != "primary" and item["model_loaded"]), None)
        if primary and primary["model_loaded"]:
            status = "ready"
        elif fallback and fallback["model_loaded"]:
            status = "fallback"
        elif any(item["reachable"] for item in candidates):
            status = "warming"
        else:
            status = "unavailable"
        return {"provider": provider, "status": status, "candidates": candidates}

    if provider == "ollama":
        item: dict[str, Any] = {"role": "primary", "reachable": False, "model_loaded": False}
        try:
            req = request.Request(f"{local_vision_adapter.OLLAMA_URL}/api/ps", method="GET")
            with request.urlopen(req, timeout=local_vision_adapter.LM_STUDIO_PREFLIGHT_TIMEOUT_SEC) as response:
                import json
                body = json.loads(response.read().decode("utf-8"))
            names = [str(model.get("name") or "") for model in body.get("models", []) if isinstance(model, dict)]
            item["reachable"] = True
            item["model_loaded"] = local_vision_adapter.OLLAMA_MODEL in names
        except TimeoutError:
            item["error"] = "timeout"
        except Exception:
            item["error"] = "unreachable"
        status = "ready" if item["model_loaded"] else ("warming" if item["reachable"] else "unavailable")
        return {"provider": provider, "status": status, "candidates": [item]}

    if provider == "claude":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        item: dict[str, Any] = {"role": "primary", "reachable": False, "model_loaded": False}
        if not api_key:
            item["error"] = "not_configured"
        else:
            req = request.Request(
                "https://api.anthropic.com/v1/models/"
                + parse.quote(claude_vision_adapter.DEFAULT_MODEL, safe=""),
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                method="GET",
            )
            try:
                with request.urlopen(req, timeout=local_vision_adapter.LM_STUDIO_PREFLIGHT_TIMEOUT_SEC):
                    pass
            except TimeoutError:
                item["error"] = "timeout"
            except Exception:
                item["error"] = "unreachable"
            else:
                item["reachable"] = True
                item["model_loaded"] = True
        return {"provider": provider, "status": "ready" if item["model_loaded"] else "unavailable", "candidates": [item]}

    return {
        "provider": "disabled" if provider == "disabled" else "unsupported",
        "status": "unavailable",
        "candidates": [{"role": "primary", "reachable": False, "model_loaded": False, "error": "not_configured"}],
    }


def describe(
    image_bytes: bytes | None = None,
    *,
    images: list[tuple[bytes, str]] | None = None,
    context_text: str | None = None,
    media_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Describe one or more food images without retaining raw image bytes.

    FIT-138: ``images`` is the canonical multi-photo input — a list of
    ``(bytes, mimetype)`` tuples passed to the provider as one combined
    call (multi-image content array). The legacy single-image
    ``image_bytes`` + ``media_type`` kwargs remain supported for callers
    that haven't migrated.
    """
    if images is None:
        if image_bytes is None:
            raise VisionEstimatorError("no image data provided")
        images = [(image_bytes, media_type)]
    if not images:
        raise VisionEstimatorError("no image data provided")
    provider = configured_provider()
    if provider == "disabled":
        raise VisionEstimatorError("vision provider disabled; set VISION_ESTIMATOR_PROVIDER=lm_studio, ollama, or claude")
    if provider not in SUPPORTED_PROVIDERS:
        raise VisionEstimatorError(f"unsupported vision provider: {provider}")
    try:
        if provider == "claude":
            result = claude_vision_adapter.describe_food_photo(
                images=images,
                context_text=context_text,
            )
        else:
            result = local_vision_adapter.describe_food_photo(
                images=images,
                context_text=context_text,
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
    if result.get("label_ocr") is True:
        cleaned["label_ocr"] = True
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
