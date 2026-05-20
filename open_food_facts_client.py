"""Thin Open Food Facts client for non-US packaged-food lookup."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any
from urllib import error, parse, request


OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
TIMEOUT_SECONDS = 5.0
TOTAL_TIMEOUT_SECONDS = 6.0
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
    "u.k",
    "u.k.",
    "united",
}
PRODUCT_QUERY_ALIASES = {
    "petit ecolier": "Petit Écolier",
    "tim tams": "Tim Tam",
}
COUNTRY_SEARCH_TAGS = {
    "en:australia": "Australia",
    "en:canada": "Canada",
    "en:france": "France",
    "en:germany": "Germany",
    "en:ireland": "Ireland",
    "en:japan": "Japan",
    "en:united-kingdom": "United Kingdom",
}


def search_products(
    query: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    total_timeout: float = TOTAL_TIMEOUT_SECONDS,
    country_tag: str | None = None,
    product_filter: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any] | None:
    cleaned = (query or "").strip()
    if not cleaned:
        return None
    combined_products: list[dict[str, Any]] = []
    last_payload: dict[str, Any] | None = None
    deadline = time.monotonic() + total_timeout
    for search_terms in _search_variants(cleaned):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        payload = _search_products_once(search_terms, timeout=min(timeout, remaining), country_tag=country_tag)
        if not payload:
            continue
        last_payload = payload
        products = payload.get("products")
        if isinstance(products, list):
            dict_products = [product for product in products if isinstance(product, dict)]
            combined_products.extend(dict_products)
            if dict_products and (product_filter is None or any(product_filter(product) for product in dict_products)):
                result = dict(last_payload or {})
                result["products"] = combined_products
                return result
    if combined_products:
        result = dict(last_payload or {})
        result["products"] = combined_products
        return result
    return last_payload


def _search_variants(query: str) -> list[str]:
    variants = []
    product_only = _strip_locale_words(query)
    if product_only and product_only.lower() != query.lower():
        variants.append(product_only)
    variants.append(query)
    alias = PRODUCT_QUERY_ALIASES.get(product_only.lower())
    if alias and alias.lower() not in {variant.lower() for variant in variants}:
        variants.insert(1 if variants else 0, alias)
    return variants


def _strip_locale_words(query: str) -> str:
    return " ".join(
        part
        for part in query.split()
        if part.strip(".,!?;:()[]{}\"'").lower() not in LOCALE_QUERY_TOKENS
    )


def _search_products_once(
    query: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    country_tag: str | None = None,
) -> dict[str, Any] | None:
    params = {
        "search_terms": query,
        "search_simple": "1",
        "action": "process",
        "json": "1",
        "page_size": "25",
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
    country_filter = COUNTRY_SEARCH_TAGS.get(country_tag or "")
    if country_filter:
        params.update({
            "tagtype_0": "countries",
            "tag_contains_0": "contains",
            "tag_0": country_filter,
        })
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
