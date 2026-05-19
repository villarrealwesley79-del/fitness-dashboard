"""Thin Open Food Facts client for non-US packaged-food lookup."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request


OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
TIMEOUT_SECONDS = 1.5
USER_AGENT = "FitnessDashboard/1.0 (https://github.com/villarrealwesley79-del/fitness-dashboard)"


def search_products(query: str, *, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any] | None:
    cleaned = (query or "").strip()
    if not cleaned:
        return None
    params = {
        "search_terms": cleaned,
        "search_simple": "1",
        "action": "process",
        "json": "1",
        "page_size": "5",
        "fields": ",".join(
            [
                "code",
                "product_name",
                "brands",
                "url",
                "nutriments",
                "data_quality_tags",
                "countries_tags",
            ]
        ),
    }
    req = request.Request(
        f"{OFF_SEARCH_URL}?{parse.urlencode(params)}",
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None
