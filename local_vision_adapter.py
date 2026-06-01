"""Local vision adapter for food-photo descriptions."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any
from urllib import error, request

from meal_estimate_schema import (
    ALLOWED_MEAL_TYPES,
    CALORIE_MAX,
    MACRO_GRAM_MAX,
    SODIUM_MG_MAX,
    MealEstimateValidationError,
    sanitize_meal_estimate,
)


def _env_first(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return default


SERVED_VISION_MODEL = "qwen3-vl-30b-a3b-instruct@q4_k_xl"
LOW_MEMORY_VISION_MODEL = ""
LM_STUDIO_URL = _env_first(
    "VISION_LM_STUDIO_URL",
    "LM_STUDIO_URL",
    default="http://127.0.0.1:1234",
).rstrip("/")
LM_STUDIO_MODEL = _env_first(
    "VISION_LM_STUDIO_MODEL",
    "LM_STUDIO_VISION_MODEL",
    default=SERVED_VISION_MODEL,
)
LM_STUDIO_FALLBACK_URL = os.environ.get("VISION_LM_STUDIO_FALLBACK_URL", "").strip().rstrip("/")
LM_STUDIO_FALLBACK_MODEL = os.environ.get("VISION_LM_STUDIO_FALLBACK_MODEL", "").strip()
LM_STUDIO_LOW_MEMORY_MODEL = os.environ.get("VISION_LM_STUDIO_LOW_MEMORY_MODEL", LOW_MEMORY_VISION_MODEL).strip()
OLLAMA_URL = _env_first("OLLAMA_URL", default="http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = _env_first("VISION_OLLAMA_MODEL", "OLLAMA_VISION_MODEL", default="llava:latest")
TIMEOUT_SECONDS = float(_env_first("VISION_LOCAL_TIMEOUT_SEC", default="25"))
LM_STUDIO_PREFLIGHT_TIMEOUT_SEC = float(_env_first(
    "VISION_LM_STUDIO_PREFLIGHT_TIMEOUT_SEC",
    "LM_STUDIO_PREFLIGHT_TIMEOUT_SEC",
    default="1.5",
))
LM_STUDIO_LOAD_RETRY_LIMIT = int(_env_first("VISION_LM_STUDIO_LOAD_RETRY_LIMIT", default="2"))
LM_STUDIO_LOAD_RETRY_BACKOFF_SEC = float(_env_first("VISION_LM_STUDIO_LOAD_RETRY_BACKOFF_SEC", default="1.0"))
LM_STUDIO_WARMUP_TIMEOUT_SEC = float(_env_first("VISION_LM_STUDIO_WARMUP_TIMEOUT_SEC", default="45"))
DEFAULT_VISION_TEMPERATURE = 0.1
SCHEMA_RETRY_LIMIT = 2
LM_STUDIO_REQUIRED_FIELDS = (
    "item_name",
    "portion_description",
    "meal_type",
    "calories",
    "protein_g",
    "carbs_g",
    "fat_g",
    "sodium_mg",
    "fiber_g",
    "confidence",
    "ambiguous",
    "uncertainty_notes",
    "items",
)
LM_STUDIO_ITEM_REQUIRED_FIELDS = ("item_name", "quantity", "brand", "modifiers", "portion_hint")
SCALE_CUE_INSTRUCTION = (
    "Before estimating calories or macros, use visible scale cues: count pieces, compare "
    "food volume to plates, bowls, wrappers, labels, utensils, hands, and containers, and "
    "separate each visible component before summing macros. If neither the image nor user "
    "context provides a usable portion cue, assume a typical single serving, lower confidence, "
    "set ambiguous=true, and explain the portion uncertainty."
)


class LocalVisionError(RuntimeError):
    """Raised when a local vision model cannot produce a valid description."""


def _prompt(context_text: str | None = None) -> str:
    prompt = (
        "Identify the food in this image for a fitness food log. Return JSON only with "
        "item_description, portion_hint, confidence, ambiguous, uncertainty_notes, items, "
        "and optional macro_estimate using calories/protein_g/carbs_g/fat_g/sodium_mg/fiber_g. "
        "For restaurant cart, receipt, delivery-app, or order screenshots, OCR the visible brand, "
        "line-item names, modifiers, and quantities into items. Each item must have item_name, "
        "quantity, optional brand, optional modifiers array, and optional portion_hint. Do not "
        "collapse a multi-item cart into one generic meal. Do not use prices as nutrition facts. "
        f"{SCALE_CUE_INSTRUCTION} "
        "Do not include raw image data, file paths, or chain of thought."
    )
    if context_text:
        prompt += f"\nUser context: {context_text[:500]}"
    return prompt


def _lm_studio_prompt(context_text: str | None = None) -> str:
    prompt = (
        "Estimate the food in this image for a fitness food log. Return JSON only with "
        "item_name, portion_description, meal_type, calories, protein_g, carbs_g, fat_g, "
        "sodium_mg, fiber_g, confidence, ambiguous, uncertainty_notes, and items. Use the "
        "meal_type enum breakfast/lunch/dinner/snack. For restaurant cart, receipt, "
        "delivery-app, or order screenshots, OCR the visible brand, line-item names, "
        "modifiers, and quantities into items. Use an empty items array for a single "
        "unstructured plate. Do not collapse a multi-item cart into one generic meal. "
        "Use low confidence and ambiguous=true when the portion, ingredients, or item "
        f"identity are unclear. {SCALE_CUE_INSTRUCTION} Do not include raw image data, "
        "file paths, prompts, markdown, "
        "or chain of thought."
    )
    if context_text:
        prompt += f"\nUser context: {context_text[:500]}"
    return prompt


def describe_food_photo(
    image_bytes: bytes | None = None,
    *,
    images: list[tuple[bytes, str]] | None = None,
    context_text: str | None = None,
    media_type: str = "image/jpeg",
    provider: str = "lm_studio",
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """FIT-138: ``images`` is a list of ``(bytes, mimetype)`` for
    multi-photo meals. ``image_bytes`` + ``media_type`` are accepted for
    legacy single-image callers and normalized to a one-element list."""
    if images is None:
        if not image_bytes:
            raise LocalVisionError("image bytes are required")
        images = [(image_bytes, media_type)]
    if not images:
        raise LocalVisionError("image bytes are required")
    provider_name = (provider or "lm_studio").strip().lower()
    if provider_name == "lm_studio":
        return _describe_lm_studio(images=images, context_text=context_text, timeout=timeout)
    if provider_name == "ollama":
        return _describe_ollama(images=images, context_text=context_text, timeout=timeout)
    raise LocalVisionError(f"unsupported local vision provider: {provider_name}")


def _multi_image_prompt(context_text: str | None, count: int) -> str:
    base = _prompt(context_text)
    if count <= 1:
        return base
    return (
        f"Identify the food across these {count} photos as ONE meal. Treat the photos as "
        "different views of the same meal context — do not double-count items that appear "
        "in multiple photos.\n" + base
    )


def _lm_studio_multi_image_prompt(context_text: str | None, count: int) -> str:
    base = _lm_studio_prompt(context_text)
    if count <= 1:
        return base
    return (
        f"Estimate the food across these {count} photos as ONE meal. Treat the photos as "
        "different views of the same meal context — do not double-count items that appear "
        "in multiple photos.\n" + base
    )


def _describe_lm_studio(
    *,
    images: list[tuple[bytes, str]],
    context_text: str | None,
    timeout: float,
) -> dict[str, Any]:
    errors: list[str] = []
    for candidate in _lm_studio_candidates():
        try:
            _preflight_candidate(candidate)
        except LocalVisionError as exc:
            errors.append(f"{candidate['role']} preflight: {exc}")
            continue
        for attempt in range(SCHEMA_RETRY_LIMIT + 1):
            payload = _lm_studio_payload(
                images=images,
                context_text=context_text,
                model=candidate["model"],
            )
            try:
                body = _post_json_with_load_retries(
                    f"{candidate['url']}/v1/chat/completions",
                    payload,
                    timeout=timeout,
                )
                content_out = _lm_studio_message_content(body)
                estimate = _parse_lm_studio_meal_estimate(content_out)
                result = _meal_estimate_to_description(estimate)
                result["_meta"] = {
                    "role": candidate["role"],
                    "model": candidate["model"],
                    "url": candidate["url"],
                    "schema_retries": attempt,
                    "fallback_used": candidate["role"] != "primary",
                }
                return result
            except LocalVisionError as exc:
                errors.append(f"{candidate['role']} attempt {attempt + 1}: {_summarize_lm_studio_error(exc)}")
                if not _schema_error(exc) or attempt >= SCHEMA_RETRY_LIMIT:
                    break
    raise LocalVisionError("all LM Studio vision endpoints failed: " + "; ".join(errors))


def _lm_studio_candidates() -> list[dict[str, str]]:
    candidates = [
        {
            "role": "primary",
            "url": LM_STUDIO_URL.rstrip("/"),
            "model": LM_STUDIO_MODEL,
        }
    ]
    if LM_STUDIO_LOW_MEMORY_MODEL:
        low_memory = {
            "role": "low_memory",
            "url": LM_STUDIO_URL.rstrip("/"),
            "model": LM_STUDIO_LOW_MEMORY_MODEL,
        }
        if (low_memory["url"], low_memory["model"]) != (candidates[0]["url"], candidates[0]["model"]):
            candidates.append(low_memory)
    if LM_STUDIO_FALLBACK_URL and LM_STUDIO_FALLBACK_MODEL:
        fallback = {
            "role": "fallback",
            "url": LM_STUDIO_FALLBACK_URL.rstrip("/"),
            "model": LM_STUDIO_FALLBACK_MODEL,
        }
        if (fallback["url"], fallback["model"]) != (candidates[0]["url"], candidates[0]["model"]):
            candidates.append(fallback)
    return candidates


def _model_version(model: str) -> str:
    return (model or "").split("/")[-1]


def _target_loaded(loaded: list[str], model: str) -> bool:
    version = _model_version(model)
    return any(model == loaded_model or model in loaded_model or version in loaded_model for loaded_model in loaded)


def _models_for(candidate: dict[str, str], timeout: float = LM_STUDIO_PREFLIGHT_TIMEOUT_SEC) -> list[str]:
    req = request.Request(f"{candidate['url']}/v1/models", method="GET")
    with request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    models = data.get("data", []) if isinstance(data, dict) else []
    loaded: list[str] = []
    for model in models:
        if isinstance(model, dict):
            model_id = model.get("id")
        else:
            model_id = model
        if model_id:
            loaded.append(str(model_id))
    return loaded


def _preflight_candidate(candidate: dict[str, str]) -> None:
    try:
        loaded = _models_for(candidate, timeout=LM_STUDIO_PREFLIGHT_TIMEOUT_SEC)
    except error.URLError as exc:
        raise LocalVisionError(f"unreachable: {exc}") from exc
    except TimeoutError as exc:
        raise LocalVisionError("timeout") from exc
    except Exception as exc:
        raise LocalVisionError(f"preflight failed: {type(exc).__name__}") from exc
    if _target_loaded(loaded, candidate["model"]):
        return
    try:
        _warm_candidate(candidate)
    except LocalVisionError as exc:
        raise LocalVisionError(f"model warmup failed: {_summarize_lm_studio_error(exc)}") from exc
    try:
        loaded = _models_for(candidate, timeout=LM_STUDIO_PREFLIGHT_TIMEOUT_SEC)
    except Exception:
        return
    if not _target_loaded(loaded, candidate["model"]):
        raise LocalVisionError(f"model warmup completed but model not loaded: {candidate['model']}")


def _warm_candidate(candidate: dict[str, str]) -> None:
    payload = _lm_studio_payload(
        images=[(_WARMUP_PNG_BYTES, "image/png")],
        context_text="warm up vision model; return a minimal valid food estimate for this blank image",
        model=candidate["model"],
    )
    _post_json_with_load_retries(
        f"{candidate['url']}/v1/chat/completions",
        payload,
        timeout=LM_STUDIO_WARMUP_TIMEOUT_SEC,
    )


_WARMUP_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _vision_temperature() -> float:
    raw = os.environ.get("VISION_TEMPERATURE", "").strip()
    if not raw:
        return DEFAULT_VISION_TEMPERATURE
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_VISION_TEMPERATURE


def _lm_studio_payload(
    *,
    images: list[tuple[bytes, str]],
    context_text: str | None,
    model: str,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": _lm_studio_multi_image_prompt(context_text, len(images))}]
    for image_bytes, media_type in images:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}})
    return {
        "model": model,
        "temperature": _vision_temperature(),
        "max_tokens": 700,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "meal_estimate",
                "strict": True,
                "schema": _meal_estimate_response_schema(),
            },
        },
        "messages": [{"role": "user", "content": content}],
    }


def _lm_studio_message_content(body: dict[str, Any]) -> str:
    try:
        message = body["choices"][0]["message"]
        return message.get("content") or message.get("reasoning_content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LocalVisionError(f"unexpected LM Studio response shape: {body}") from exc


def _schema_error(exc: LocalVisionError) -> bool:
    message = str(exc)
    return message.startswith("invalid JSON response") or message.startswith("invalid meal estimate")


def _meal_estimate_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "item_name": {"type": "string"},
            "portion_description": {"type": ["string", "null"]},
            "meal_type": {"type": "string", "enum": list(ALLOWED_MEAL_TYPES)},
            "calories": {"type": "number", "minimum": 0, "maximum": CALORIE_MAX},
            "protein_g": {"type": "number", "minimum": 0, "maximum": MACRO_GRAM_MAX},
            "carbs_g": {"type": "number", "minimum": 0, "maximum": MACRO_GRAM_MAX},
            "fat_g": {"type": "number", "minimum": 0, "maximum": MACRO_GRAM_MAX},
            "sodium_mg": {"type": "number", "minimum": 0, "maximum": SODIUM_MG_MAX},
            "fiber_g": {"type": "number", "minimum": 0, "maximum": MACRO_GRAM_MAX},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "ambiguous": {"type": "boolean"},
            "uncertainty_notes": {"type": "array", "items": {"type": "string"}},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "item_name": {"type": "string"},
                        "quantity": {"type": "number", "minimum": 0},
                        "brand": {"type": ["string", "null"]},
                        "modifiers": {"type": "array", "items": {"type": "string"}},
                        "portion_hint": {"type": ["string", "null"]},
                    },
                    "required": ["item_name", "quantity", "brand", "modifiers", "portion_hint"],
                },
            },
        },
        "required": [
            "item_name",
            "portion_description",
            "meal_type",
            "calories",
            "protein_g",
            "carbs_g",
            "fat_g",
            "sodium_mg",
            "fiber_g",
            "confidence",
            "ambiguous",
            "uncertainty_notes",
            "items",
        ],
    }


def _parse_lm_studio_meal_estimate(content: str) -> dict[str, Any]:
    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalVisionError("invalid JSON response") from exc
    _validate_lm_studio_schema_fields(parsed)
    try:
        estimate = sanitize_meal_estimate(
            parsed,
            source="vision_lm_studio_estimate",
            plausible_ranges=True,
        )
    except MealEstimateValidationError as exc:
        raise LocalVisionError(f"invalid meal estimate: {exc}") from exc
    items = _sanitize_lm_studio_items(parsed.get("items"))
    if items:
        estimate["items"] = items
    return estimate


def _validate_lm_studio_schema_fields(parsed: Any) -> None:
    if not isinstance(parsed, dict):
        raise LocalVisionError("invalid meal estimate: estimate must be an object")
    missing = [field for field in LM_STUDIO_REQUIRED_FIELDS if field not in parsed]
    if missing:
        raise LocalVisionError(f"invalid meal estimate: missing required fields: {', '.join(missing)}")
    if parsed.get("meal_type") not in ALLOWED_MEAL_TYPES:
        raise LocalVisionError("invalid meal estimate: invalid meal_type")
    if not isinstance(parsed.get("items"), list):
        raise LocalVisionError("invalid meal estimate: items must be an array")
    for index, item in enumerate(parsed["items"]):
        if not isinstance(item, dict):
            raise LocalVisionError(f"invalid meal estimate: items[{index}] must be an object")
        missing_item_fields = [field for field in LM_STUDIO_ITEM_REQUIRED_FIELDS if field not in item]
        if missing_item_fields:
            raise LocalVisionError(
                f"invalid meal estimate: items[{index}] missing required fields: {', '.join(missing_item_fields)}"
            )


def _sanitize_lm_studio_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in raw_items[:10]:
        if not isinstance(raw, dict):
            continue
        item_name = str(raw.get("item_name") or "").strip()
        if not item_name:
            continue
        item: dict[str, Any] = {"item_name": item_name[:120]}
        quantity = raw.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or quantity <= 0:
            quantity = 1
        quantity = min(float(quantity), 20.0)
        item["quantity"] = int(quantity) if quantity.is_integer() else round(quantity, 2)
        brand = raw.get("brand")
        if isinstance(brand, str) and brand.strip():
            item["brand"] = brand.strip()[:80]
        modifiers = raw.get("modifiers")
        if isinstance(modifiers, list):
            cleaned_modifiers = [str(mod).strip()[:80] for mod in modifiers if str(mod).strip()]
            if cleaned_modifiers:
                item["modifiers"] = cleaned_modifiers[:10]
        portion_hint = raw.get("portion_hint")
        if isinstance(portion_hint, str) and portion_hint.strip():
            item["portion_hint"] = portion_hint.strip()[:160]
        items.append(item)
    return items


def _meal_estimate_to_description(estimate: dict[str, Any]) -> dict[str, Any]:
    description = {
        "item_description": estimate["item_name"],
        "portion_hint": estimate.get("portion_description"),
        "confidence": estimate["confidence"],
        "ambiguous": estimate["ambiguous"],
        "uncertainty_notes": estimate.get("uncertainty_notes") or [],
        "macro_estimate": {
            "meal_type": estimate["meal_type"],
            "calories": estimate["calories"],
            "protein_g": estimate["protein_g"],
            "carbs_g": estimate["carbs_g"],
            "fat_g": estimate["fat_g"],
            "sodium_mg": estimate["sodium_mg"],
            "fiber_g": estimate["fiber_g"],
        },
    }
    if estimate.get("items"):
        description["items"] = estimate["items"]
    return description


def _describe_ollama(
    *,
    images: list[tuple[bytes, str]],
    context_text: str | None,
    timeout: float,
) -> dict[str, Any]:
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
        "messages": [
            {
                "role": "user",
                "content": _multi_image_prompt(context_text, len(images)),
                "images": [base64.b64encode(b).decode("ascii") for b, _mt in images],
            }
        ],
    }
    body = _post_json(f"{OLLAMA_URL}/api/chat", payload, timeout=timeout)
    content = ""
    if isinstance(body.get("message"), dict):
        content = body["message"].get("content") or ""
    if not content:
        content = body.get("response") or ""
    return _parse_json_content(content)


def _post_json(url: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    try:
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            detail = ""
        raise LocalVisionError(_safe_http_error_message(exc.code, detail)) from exc
    except (OSError, ValueError, error.URLError, TimeoutError) as exc:
        raise LocalVisionError(str(exc)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalVisionError("invalid response JSON") from exc


def _post_json_with_load_retries(url: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    attempts = max(1, LM_STUDIO_LOAD_RETRY_LIMIT + 1)
    last_error: LocalVisionError | None = None
    for attempt in range(attempts):
        try:
            return _post_json(url, payload, timeout=timeout)
        except LocalVisionError as exc:
            last_error = exc
            if attempt >= attempts - 1 or not _transient_load_error(exc):
                raise
            time.sleep(LM_STUDIO_LOAD_RETRY_BACKOFF_SEC * (attempt + 1))
    raise last_error or LocalVisionError("LM Studio request failed")


def _transient_load_error(exc: LocalVisionError) -> bool:
    message = str(exc).lower()
    if "http 400" not in message:
        return False
    transient_markers = (
        "insufficient system resources",
        "operation canceled",
        "operation cancelled",
        "loading model",
        "model is loading",
        "server is busy",
    )
    return any(marker in message for marker in transient_markers)


def _oom_error(exc: LocalVisionError) -> bool:
    message = str(exc).lower()
    return "oom" in message or "out of memory" in message or "not enough memory" in message


def _summarize_lm_studio_error(exc: LocalVisionError) -> str:
    if _oom_error(exc):
        return f"out_of_memory: {exc}"
    if _transient_load_error(exc):
        return f"transient_load: {exc}"
    return str(exc)


def _safe_http_error_message(code: int, detail: str) -> str:
    message = f"http {code}"
    detail_lower = (detail or "").lower()
    if not detail_lower:
        return message
    if "oom" in detail_lower or "out of memory" in detail_lower or "not enough memory" in detail_lower:
        return f"{message}: out of memory"
    transient_markers = [
        marker
        for marker in (
            "insufficient system resources",
            "operation canceled",
            "operation cancelled",
            "loading model",
            "model is loading",
            "server is busy",
        )
        if marker in detail_lower
    ]
    if transient_markers:
        return f"{message}: {'; '.join(transient_markers[:2])}"
    return message


def _parse_json_content(content: str) -> dict[str, Any]:
    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalVisionError("invalid JSON response") from exc
    if not isinstance(parsed, dict) or not parsed.get("item_description"):
        raise LocalVisionError("missing item_description")
    return parsed
