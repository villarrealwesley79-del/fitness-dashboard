"""Thin USDA FoodData Central search client for generic food lookup."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, parse, request


FDC_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
TIMEOUT_SECONDS = 1.5
PREFERRED_DATA_TYPES = ("Foundation", "SR Legacy")


def search_foods(query: str, *, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """Return USDA FDC search JSON, or None when the source is unavailable."""
    cleaned = (query or "").strip()
    if not cleaned:
        return None
    params = {
        "query": cleaned,
        "pageSize": "5",
        "dataType": ",".join(PREFERRED_DATA_TYPES),
    }
    api_key = os.environ.get("USDA_FDC_API_KEY")
    if api_key:
        params["api_key"] = api_key
    url = f"{FDC_SEARCH_URL}?{parse.urlencode(params)}"
    req = request.Request(url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None
