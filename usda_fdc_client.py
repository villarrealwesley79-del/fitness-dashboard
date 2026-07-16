"""Thin USDA FoodData Central search client for generic food lookup."""

from __future__ import annotations

import os
from typing import Any
from urllib import parse, request

import food_provider_transport


FDC_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
TIMEOUT_SECONDS = 1.5
PREFERRED_DATA_TYPES = ("Branded", "Foundation", "SR Legacy")
BARCODE_DATA_TYPES = ("Branded",)


def _effective_timeout(timeout: float) -> float:
    return food_provider_transport.clamp_timeout(timeout)


def search_foods(query: str, *, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """Return USDA FDC search JSON, or None when the source is unavailable."""
    return _search_foods(query, data_types=PREFERRED_DATA_TYPES, timeout=timeout)


def search_foods_by_barcode(barcode: str, *, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """Return USDA FDC branded-food search JSON for a barcode."""
    cleaned = "".join(char for char in str(barcode or "") if char.isdigit())
    if not cleaned:
        return None
    return _search_foods(cleaned, data_types=BARCODE_DATA_TYPES, timeout=timeout)


def _search_foods(
    query: str,
    *,
    data_types: tuple[str, ...],
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Return USDA FDC search JSON, or None when the source is unavailable."""
    cleaned = (query or "").strip()
    if not cleaned:
        return None
    params = {
        "query": cleaned,
        "pageSize": "5",
        "dataType": ",".join(data_types),
    }
    api_key = os.environ.get("USDA_FDC_API_KEY")
    if not api_key:
        return None
    params["api_key"] = api_key
    url = f"{FDC_SEARCH_URL}?{parse.urlencode(params)}"
    req = request.Request(url, headers={"Accept": "application/json"})
    return food_provider_transport.get_json(
        req,
        timeout=_effective_timeout(timeout),
        provider="usda_fdc",
    )
