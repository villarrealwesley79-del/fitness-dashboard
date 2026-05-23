"""Local vision adapter for food-photo descriptions."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib import error, request


def _env_first(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return default


LM_STUDIO_URL = _env_first(
    "VISION_LM_STUDIO_URL",
    "LM_STUDIO_URL",
    default="http://127.0.0.1:1234",
).rstrip("/")
LM_STUDIO_MODEL = _env_first(
    "VISION_LM_STUDIO_MODEL",
    "LM_STUDIO_VISION_MODEL",
    default="qwen2.5-vl-7b-instruct",
)
OLLAMA_URL = _env_first("OLLAMA_URL", default="http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = _env_first("VISION_OLLAMA_MODEL", "OLLAMA_VISION_MODEL", default="llava:latest")
TIMEOUT_SECONDS = float(_env_first("VISION_LOCAL_TIMEOUT_SEC", default="25"))


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
        "Do not include raw image data, file paths, or chain of thought."
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


def _describe_lm_studio(
    *,
    images: list[tuple[bytes, str]],
    context_text: str | None,
    timeout: float,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": _multi_image_prompt(context_text, len(images))}]
    for image_bytes, media_type in images:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}})
    payload = {
        "model": LM_STUDIO_MODEL,
        "temperature": 0,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": content}],
    }
    body = _post_json(f"{LM_STUDIO_URL}/v1/chat/completions", payload, timeout=timeout)
    try:
        content_out = body["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LocalVisionError(f"unexpected LM Studio response shape: {body}") from exc
    return _parse_json_content(content_out)


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
        raise LocalVisionError(f"http {exc.code}") from exc
    except (OSError, ValueError, error.URLError, TimeoutError) as exc:
        raise LocalVisionError(str(exc)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalVisionError("invalid response JSON") from exc


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
