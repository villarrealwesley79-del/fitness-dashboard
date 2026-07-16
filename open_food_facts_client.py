"""Thin Open Food Facts client for non-US packaged-food lookup."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib import parse, request

import food_provider_transport


OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product"
TIMEOUT_SECONDS = 5.0
TOTAL_TIMEOUT_SECONDS = 6.0
USER_AGENT = "FitnessDashboard/1.0 (https://github.com/villarrealwesley79-del/fitness-dashboard)"
LOCALE_QUERY_TOKENS = {
    "australia",
    "australian",
    "canada",
    "canadian",
    "france",
    "french",
    "germany",
    "german",
    "imported",
    "ireland",
    "irish",
    "japan",
    "japanese",
    "kingdom",
    "non",
    "non-u.s",
    "non-us",
    "package",
    "packaged",
    "product",
    "uk",
    "u.k",
    "u.k.",
    "u.s",
    "united",
    "us",
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


def _effective_timeout(timeout: float) -> float:
    return food_provider_transport.clamp_timeout(timeout)


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
    deadline = time.monotonic() + _effective_timeout(total_timeout)
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
    heb_variants = []
    seen = {variant.lower() for variant in variants}
    for variant in variants:
        for heb_variant in _heb_search_variants(variant):
            lowered = heb_variant.lower()
            if lowered not in seen:
                heb_variants.append(heb_variant)
                seen.add(lowered)
    if heb_variants:
        variants = heb_variants + variants
    return variants


def _heb_search_variants(query: str) -> list[str]:
    parts = query.split()
    if not parts:
        return []
    first = parts[0].strip(".,!?;:()[]{}\"'")
    if first.lower() not in {"heb", "h-e-b"}:
        return []
    suffix = " ".join(parts[1:]).strip()
    variants = [f"H-E-B {suffix}".strip()]
    suffix_tokens = {part.strip(".,!?;:()[]{}\"'").lower() for part in parts[1:]}
    if (
        suffix
        and "sushi" not in suffix_tokens
        and "sushiya" not in suffix_tokens
        and suffix_tokens.intersection({"california", "roll", "poke", "bowl"})
    ):
        sushiya_suffix = suffix
        variants.append(f"H-E-B Sushiya {sushiya_suffix}".strip())
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
    return food_provider_transport.get_json(
        req,
        timeout=_effective_timeout(timeout),
        provider="open_food_facts",
    )


def get_product_by_barcode(
    barcode: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    cleaned = (barcode or "").strip()
    if not cleaned:
        return None
    params = {
        "fields": ",".join(
            [
                "code",
                "product_name",
                "brands",
                "url",
                "nutriments",
                "data_quality_tags",
                "countries_tags",
                "serving_size",
                "serving_quantity",
                "quantity",
            ]
        ),
    }
    req = request.Request(
        f"{OFF_PRODUCT_URL}/{parse.quote(cleaned)}.json?{parse.urlencode(params)}",
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    payload = food_provider_transport.get_json(
        req,
        timeout=_effective_timeout(timeout),
        provider="open_food_facts",
    )
    if not isinstance(payload, dict) or payload.get("status") != 1:
        return None
    product = payload.get("product")
    return product if isinstance(product, dict) else None
