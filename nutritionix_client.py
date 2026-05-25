"""Thin Nutritionix clients for branded meal lookup."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


NUTRITIONIX_URL = "https://trackapi.nutritionix.com/v2/natural/nutrients"
NUTRITIONIX_ITEM_URL = "https://trackapi.nutritionix.com/v2/search/item"
TIMEOUT_SECONDS = 1.5


def natural_nutrients(query: str, *, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """Return Nutritionix natural-nutrients JSON, or None when unavailable.

    API keys are read from environment only. Missing credentials silently skip
    this tier so meal logging can continue through cache, USDA, or local model
    fallback without leaking secrets into code or tests.
    """
    app_id = os.environ.get("NUTRITIONIX_APP_ID")
    app_key = os.environ.get("NUTRITIONIX_APP_KEY")
    cleaned = (query or "").strip()
    if not cleaned or not app_id or not app_key:
        return None

    payload = json.dumps({"query": cleaned}).encode("utf-8")
    req = request.Request(
        NUTRITIONIX_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-app-id": app_id,
            "x-app-key": app_key,
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def search_item_by_upc(upc: str, *, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """Return Nutritionix item-search JSON for a UPC/GTIN, or None when unavailable."""
    app_id = os.environ.get("NUTRITIONIX_APP_ID")
    app_key = os.environ.get("NUTRITIONIX_APP_KEY")
    cleaned = (upc or "").strip()
    if not cleaned or not app_id or not app_key:
        return None

    req = request.Request(
        f"{NUTRITIONIX_ITEM_URL}?upc={cleaned}&servings_per_container=true",
        method="GET",
        headers={
            "Accept": "application/json",
            "x-app-id": app_id,
            "x-app-key": app_key,
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None
