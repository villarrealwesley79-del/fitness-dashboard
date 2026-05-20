"""Claude vision adapter for food-photo descriptions."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib import error, request


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = os.environ.get("CLAUDE_VISION_MODEL", "claude-sonnet-4-5")
TIMEOUT_SECONDS = 12.0


class ClaudeVisionError(RuntimeError):
    """Raised when Claude vision is unavailable or returns invalid data."""


def describe_food_photo(
    image_bytes: bytes,
    *,
    context_text: str | None = None,
    media_type: str = "image/jpeg",
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ClaudeVisionError("ANTHROPIC_API_KEY is not set")
    if not image_bytes:
        raise ClaudeVisionError("image bytes are required")

    prompt = (
        "Identify the food in this image for a fitness food log. Return JSON only "
        "with item_description, portion_hint, confidence, ambiguous, uncertainty_notes, items, "
        "and optional macro_estimate using calories/protein_g/carbs_g/fat_g/sodium_mg/fiber_g. "
        "For restaurant cart, receipt, delivery-app, or order screenshots, OCR the visible brand, "
        "line-item names, modifiers, and quantities into items. Each item must have item_name, "
        "quantity, optional brand, optional modifiers array, and optional portion_hint. Do not "
        "collapse a multi-item cart into one generic meal. Do not use prices as nutrition facts. "
        "Do not include raw image data or chain of thought."
    )
    if context_text:
        prompt += f"\nUser context: {context_text[:500]}"
    payload = {
        "model": DEFAULT_MODEL,
        "max_tokens": 500,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    req = request.Request(
        ANTHROPIC_MESSAGES_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (OSError, error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise ClaudeVisionError(str(exc)) from exc
    return _parse_message(body)


def _parse_message(body: dict[str, Any]) -> dict[str, Any]:
    content = body.get("content") if isinstance(body, dict) else None
    if not isinstance(content, list) or not content:
        raise ClaudeVisionError("missing content")
    text_chunks = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
    raw = "\n".join(text_chunks).strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeVisionError("invalid JSON response") from exc
    if not isinstance(parsed, dict) or not parsed.get("item_description"):
        raise ClaudeVisionError("missing item_description")
    return parsed
