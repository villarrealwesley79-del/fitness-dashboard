"""Thin Open Food Facts client for non-US packaged-food lookup."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request


OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
TIMEOUT_SECONDS = 1.5
USER_AGENT = "FitnessDashboard/1.0 (https://github.com/villarrealwesley79-del/fitness-dashboard)"
LOCALE_QUERY_TOKENS = {
    "australian",
    "canadian",
    "french",
    "german",
    "irish",
    "japanese",
    "kingdom",
    "uk",
    "u.k.",
    "united",
}


def search_products(query: str, *, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any] | None:
    cleaned = (query or "").strip()
    if not cleaned:
        return None
    for search_terms in _search_variants(cleaned):
        payload = _search_products_once(search_terms, timeout=timeout)
        if payload and payload.get("products"):
            return payload
    return payload if "payload" in locals() else None


def _search_variants(query: str) -> list[str]:
    variants = [query]
    product_only = _strip_locale_words(query)
    if product_only and product_only.lower() != query.lower():
        variants.append(product_only)
    return variants


def _strip_locale_words(query: str) -> str:
    return " ".join(part for part in query.split() if part.lower() not in LOCALE_QUERY_TOKENS)


def _search_products_once(query: str, *, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any] | None:
    params = {
        "search_terms": query,
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
