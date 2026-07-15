#!/usr/bin/env python3
"""Smoke-test regional-chain branded nutrition lookup coverage.

This helper is intentionally read-only for app data: it skips the existing
lookup cache by default and disables cache writes so a coverage check cannot
teach the app new nutrition rows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import branded_food_lookup


@dataclass(frozen=True)
class CoverageQuery:
    query: str
    category: str
    note: str
    expected_chain: str = ""


@dataclass(frozen=True)
class CoverageResult:
    category: str
    query: str
    outcome: str
    matched_item: str
    calories: str
    source_url: str
    confidence: str
    notes: str


REQUIRED_BILL_MILLER_QUERIES: tuple[CoverageQuery, ...] = (
    CoverageQuery("bill miller bacon and egg taco", "required", "FIT-98 Bill Miller BBQ query", "bill miller"),
    CoverageQuery(
        "bill miller breakfast sandwich on biscuit",
        "required",
        "FIT-98 Bill Miller BBQ query",
        "bill miller",
    ),
    CoverageQuery("bill miller brisket sandwich", "required", "FIT-98 Bill Miller BBQ query", "bill miller"),
)

PROXY_REGIONAL_QUERIES: tuple[CoverageQuery, ...] = (
    CoverageQuery(
        "whataburger patty melt",
        "proxy",
        "Texas/regional chain proxy, not user-confirmed",
        "whataburger",
    ),
    CoverageQuery(
        "taco cabana bean and cheese taco",
        "proxy",
        "Texas/regional chain proxy, not user-confirmed",
        "taco cabana",
    ),
    CoverageQuery("torchys democrat taco", "proxy", "Texas/regional chain proxy, not user-confirmed", "torchys"),
    CoverageQuery("rudys brisket sandwich", "proxy", "Texas/regional chain proxy, not user-confirmed", "rudys"),
    CoverageQuery("p terrys cheeseburger", "proxy", "Texas/regional chain proxy, not user-confirmed", "p terrys"),
    CoverageQuery("schlotzskys original sandwich", "proxy", "Regional chain proxy, not user-confirmed", "schlotzskys"),
    CoverageQuery(
        "golden chick chicken tenders",
        "proxy",
        "Texas/regional chain proxy, not user-confirmed",
        "golden chick",
    ),
    CoverageQuery(
        "la madeleine chicken caesar salad",
        "proxy",
        "Texas/regional chain proxy, not user-confirmed",
        "la madeleine",
    ),
)

DEFAULT_QUERIES: tuple[CoverageQuery, ...] = REQUIRED_BILL_MILLER_QUERIES + PROXY_REGIONAL_QUERIES
ACTIVE_PROVIDER_SOURCES: tuple[str, ...] = ("heb_product_page", "usda_fdc", "open_food_facts")


def provider_status(
    *,
    include_cache: bool = False,
    respect_direct_lookup_gate: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ if env is None else env
    usda_ready = bool(env.get("USDA_FDC_API_KEY"))
    return {
        "cache": "read enabled" if include_cache else "skipped",
        "direct_lookup_gate": (
            "production gate respected"
            if respect_direct_lookup_gate
            else "bypassed for coverage matrix; production gate recorded per row"
        ),
        "usda_fdc": "configured" if usda_ready else "missing USDA_FDC_API_KEY",
        "open_food_facts": "no credentials required",
    }


def run_coverage(
    queries: tuple[CoverageQuery, ...] = DEFAULT_QUERIES,
    *,
    include_cache: bool = False,
    respect_direct_lookup_gate: bool = False,
    user_id: int = 1,
    env: dict[str, str] | None = None,
) -> list[CoverageResult]:
    priorities = [
        source
        for source in branded_food_lookup.SOURCE_PRIORITY
        if source in ACTIVE_PROVIDER_SOURCES or (include_cache and source == "cache")
    ]
    unavailable_sources = _unavailable_sources(env)

    original_save = branded_food_lookup.data_store.save_branded_lookup_cache
    branded_food_lookup.data_store.save_branded_lookup_cache = lambda *_args, **_kwargs: None
    try:
        return [
            _run_case(
                case,
                priorities=tuple(priorities),
                respect_direct_lookup_gate=respect_direct_lookup_gate,
                unavailable_sources=unavailable_sources,
                user_id=user_id,
            )
            for case in queries
        ]
    finally:
        branded_food_lookup.data_store.save_branded_lookup_cache = original_save


def _run_case(
    case: CoverageQuery,
    *,
    priorities: tuple[str, ...],
    respect_direct_lookup_gate: bool,
    unavailable_sources: dict[str, str],
    user_id: int,
) -> CoverageResult:
    production_gate_blocked = not branded_food_lookup.should_attempt_direct_lookup(case.query)
    if respect_direct_lookup_gate and production_gate_blocked:
        return _coverage_result(case, None, gate_blocked=True)

    rejected_notes: list[str] = []
    unavailable_notes: list[str] = []
    tried_sources: list[str] = []
    for source in priorities:
        unavailable_reason = unavailable_sources.get(source)
        if unavailable_reason:
            unavailable_notes.append(unavailable_reason)
            continue
        estimate = branded_food_lookup.lookup(
            case.query,
            source_priority=(source,),
            user_id=user_id,
        )
        if not estimate:
            tried_sources.append(source)
            continue
        if case.expected_chain and not _matches_expected_chain(estimate, case.expected_chain):
            rejected_notes.append(_wrong_chain_note(case, estimate))
            continue
        return _coverage_result(
            case,
            estimate,
            production_gate_blocked=production_gate_blocked,
            extra_notes=rejected_notes,
        )

    return _coverage_result(
        case,
        None,
        production_gate_blocked=production_gate_blocked,
        unavailable_notes=unavailable_notes,
        rejected_notes=rejected_notes,
        tried_sources=tried_sources,
    )


def _coverage_result(
    case: CoverageQuery,
    estimate: dict[str, Any] | None,
    *,
    gate_blocked: bool = False,
    production_gate_blocked: bool = False,
    unavailable_notes: list[str] | None = None,
    rejected_notes: list[str] | None = None,
    tried_sources: list[str] | None = None,
    extra_notes: list[str] | None = None,
) -> CoverageResult:
    if not estimate:
        notes = [case.note]
        if gate_blocked:
            notes.append("production direct-lookup gate blocked provider lookup before AI fallback")
        else:
            notes.extend(unavailable_notes or [])
            notes.extend(rejected_notes or [])
            if tried_sources:
                notes.append(f"{', '.join(tried_sources)} reached with no verified match")
            notes.append("fell through before AI fallback")
            if production_gate_blocked:
                notes.append("production direct-lookup gate would block this query")
        outcome = "provider unavailable" if unavailable_notes else "miss/fallback gap"
        return CoverageResult(
            category=case.category,
            query=case.query,
            outcome=outcome,
            matched_item="",
            calories="",
            source_url="",
            confidence="",
            notes="; ".join(notes),
        )

    notes = [case.note]
    if production_gate_blocked:
        notes.append("production direct-lookup gate would block this query")
    notes.extend(extra_notes or [])
    uncertainty_notes = estimate.get("uncertainty_notes")
    if isinstance(uncertainty_notes, list):
        notes.extend(str(note) for note in uncertainty_notes if note)
    underlying = estimate.get("underlying_source")
    if underlying and underlying != estimate.get("source"):
        notes.append(f"underlying source: {underlying}")

    return CoverageResult(
        category=case.category,
        query=case.query,
        outcome=str(estimate.get("source") or "unknown"),
        matched_item=str(estimate.get("item_name") or ""),
        calories=_format_value(estimate.get("calories")),
        source_url=_source_url(estimate),
        confidence=_format_confidence(estimate.get("confidence")),
        notes="; ".join(notes),
    )


def _unavailable_sources(env: dict[str, str] | None = None) -> dict[str, str]:
    status = provider_status(env=env)
    unavailable: dict[str, str] = {}
    if status["usda_fdc"] != "configured":
        unavailable["usda_fdc"] = f"usda_fdc skipped: {status['usda_fdc']}"
    return unavailable


def _wrong_chain_note(case: CoverageQuery, estimate: dict[str, Any]) -> str:
    source = str(estimate.get("source") or "unknown")
    matched_item = str(estimate.get("item_name") or "")
    source_brand = str(estimate.get("source_brand_name") or "")
    if source_brand and matched_item.lower().startswith(source_brand.lower()):
        matched_text = matched_item
    else:
        matched_text = " ".join(part for part in (source_brand, matched_item) if part)
    matched_text = matched_text or "an item"
    return (
        f"{source} returned {matched_text} without verifying expected chain "
        f"{case.expected_chain}; continuing to lower-priority providers"
    )


def _source_url(estimate: dict[str, Any]) -> str:
    for key in ("verified_source_url", "source_url", "product_url"):
        value = estimate.get(key)
        if value:
            return str(value)
    attribution = estimate.get("off_attribution")
    if isinstance(attribution, dict):
        for key in ("url", "source_url", "product_url", "license_url"):
            value = attribution.get(key)
            if value:
                return str(value)
    return ""


def _matches_expected_chain(estimate: dict[str, Any], expected_chain: str) -> bool:
    expected_tokens = set(branded_food_lookup.normalize_meal_text(expected_chain).split())
    if not expected_tokens:
        return True
    haystack_values = [
        estimate.get("item_name"),
        estimate.get("source_brand_name"),
        estimate.get("brand_name"),
        estimate.get("verified_source_url"),
        estimate.get("source_url"),
        estimate.get("product_url"),
    ]
    attribution = estimate.get("off_attribution")
    if isinstance(attribution, dict):
        haystack_values.extend(attribution.values())
    haystack = branded_food_lookup.normalize_meal_text(" ".join(str(value or "") for value in haystack_values))
    haystack_tokens = set(haystack.split())
    return expected_tokens.issubset(haystack_tokens)


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _format_confidence(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return f"{float(value):.2f}"


def markdown_table(results: list[CoverageResult]) -> str:
    headers = ("Category", "Query", "Outcome", "Matched item", "Calories", "Source URL", "Confidence", "Notes")
    rows = [
        (
            result.category,
            result.query,
            result.outcome,
            result.matched_item,
            result.calories,
            result.source_url,
            result.confidence,
            result.notes,
        )
        for result in results
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_escape_markdown_cell(value) for value in row) + " |")
    return "\n".join(lines)


def _escape_markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    parser.add_argument("--force-lookup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--respect-gate", action="store_true", help="Respect the production direct-lookup gate.")
    parser.add_argument("--include-cache", action="store_true", help="Read existing local cache rows.")
    parser.add_argument("--skip-cache", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--user-id", type=int, default=1, help="Lookup-cache user id when cache reads are enabled.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    include_cache = bool(args.include_cache and not args.skip_cache)
    respect_direct_lookup_gate = bool(args.respect_gate and not args.force_lookup)
    results = run_coverage(
        include_cache=include_cache,
        respect_direct_lookup_gate=respect_direct_lookup_gate,
        user_id=args.user_id,
    )
    status = provider_status(
        include_cache=include_cache,
        respect_direct_lookup_gate=respect_direct_lookup_gate,
    )
    if args.json:
        print(json.dumps({"provider_status": status, "results": [asdict(result) for result in results]}, indent=2))
    else:
        print("Provider status:")
        for provider, value in status.items():
            print(f"- {provider}: {value}")
        print()
        print(markdown_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
