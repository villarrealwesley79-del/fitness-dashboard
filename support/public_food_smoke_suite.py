#!/usr/bin/env python3
"""Fixture-only public food smoke suite for FIT-202.

The suite records public-source metadata and known barcode nutrition facts
without downloading images or opening network sockets. Generic photo cases are
graded on identity, uncertainty, and review routing only; exact calories are
only asserted for barcode/package-data cases.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BarcodeSmokeCase:
    case_id: str
    barcode: str
    expected_identity: str
    expected_source: str
    calories: float | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    serving_basis: str
    expected_status: str
    strict_nutrition_key: bool = True


@dataclass(frozen=True)
class PublicPhotoSmokeCase:
    case_id: str
    source: str
    license: str
    expected_identity_terms: tuple[str, ...]
    expected_route: str
    max_confidence: float
    grade_focus: tuple[str, ...]
    exact_calories: None = None
    raw_image_committed: bool = False


BARCODE_CASES: tuple[BarcodeSmokeCase, ...] = (
    BarcodeSmokeCase(
        case_id="C03",
        barcode="3017620422003",
        expected_identity="Nutella",
        expected_source="open_food_facts_barcode",
        calories=539,
        protein_g=6.3,
        carbs_g=57.5,
        fat_g=30.9,
        serving_basis="100 g Open Food Facts packaged-food reference",
        expected_status="verified_review",
    ),
    BarcodeSmokeCase(
        case_id="C07",
        barcode="5449000000996",
        expected_identity="Coca-Cola",
        expected_source="open_food_facts_barcode",
        calories=139,
        protein_g=0.0,
        carbs_g=35.0,
        fat_g=0.0,
        serving_basis="330 ml serving derived from Open Food Facts",
        expected_status="verified_review",
    ),
    BarcodeSmokeCase(
        case_id="C08a-zero",
        barcode="5449000131805",
        expected_identity="Coke Zero",
        expected_source="open_food_facts_barcode",
        calories=0,
        protein_g=0.0,
        carbs_g=0.0,
        fat_g=0.0,
        serving_basis="Open Food Facts zero-calorie variant reference",
        expected_status="verified_review",
    ),
    BarcodeSmokeCase(
        case_id="C12a",
        barcode="7622210449283",
        expected_identity="Prince biscuits",
        expected_source="open_food_facts_barcode",
        calories=467,
        protein_g=6.3,
        carbs_g=69.0,
        fat_g=17.0,
        serving_basis="100 g Open Food Facts packaged-food reference",
        expected_status="verified_review",
    ),
    BarcodeSmokeCase(
        case_id="C12b",
        barcode="0000000000000",
        expected_identity="Unknown barcode",
        expected_source="barcode_pending_source",
        calories=None,
        protein_g=None,
        carbs_g=None,
        fat_g=None,
        serving_basis="manual pending fallback",
        expected_status="manual_pending",
        strict_nutrition_key=False,
    ),
)


PUBLIC_PHOTO_CASES: tuple[PublicPhotoSmokeCase, ...] = (
    PublicPhotoSmokeCase(
        case_id="C01",
        source="Wikimedia Commons File:Scrambled_eggs.jpg",
        license="CC BY-SA 3.0",
        expected_identity_terms=("scrambled", "egg"),
        expected_route="pending_review",
        max_confidence=0.65,
        grade_focus=("identity", "plausible_range", "portion_uncertainty", "review_routing"),
    ),
    PublicPhotoSmokeCase(
        case_id="C02",
        source="Wikimedia Commons File:Good_Food_Display_-_NCI_Visuals_Online.jpg",
        license="Public domain",
        expected_identity_terms=("mixed", "food"),
        expected_route="pending_review",
        max_confidence=0.65,
        grade_focus=("multi_item_decomposition", "portion_uncertainty", "review_routing"),
    ),
    PublicPhotoSmokeCase(
        case_id="C05",
        source="Wikimedia Commons File:Cooked_white_rice.jpg",
        license="CC BY 2.0",
        expected_identity_terms=("rice",),
        expected_route="pending_review",
        max_confidence=0.65,
        grade_focus=("identity", "portion_uncertainty", "review_routing"),
    ),
    PublicPhotoSmokeCase(
        case_id="C06",
        source="Wikimedia Commons File:Eteo_organic_extra_virgin_olive_oil.jpg",
        license="CC BY-SA 4.0",
        expected_identity_terms=("olive", "oil"),
        expected_route="pending_review",
        max_confidence=0.65,
        grade_focus=("identity", "amount_question", "no_whole_bottle_log"),
    ),
    PublicPhotoSmokeCase(
        case_id="C08b",
        source="Wikimedia Commons grilled chicken reference",
        license="CC",
        expected_identity_terms=("grilled", "chicken"),
        expected_route="pending_review",
        max_confidence=0.65,
        grade_focus=("identity", "variant_awareness", "review_routing"),
    ),
    PublicPhotoSmokeCase(
        case_id="C08c",
        source="Wikimedia Commons fried chicken reference",
        license="CC",
        expected_identity_terms=("fried", "chicken"),
        expected_route="pending_review",
        max_confidence=0.65,
        grade_focus=("identity", "variant_awareness", "review_routing"),
    ),
)


PHOTO_OBSERVED_FIXTURES: dict[str, dict[str, Any]] = {
    "C01": {"identity": "scrambled eggs", "confidence": 0.62, "route": "pending_review", "uncertainty_notes": ["portion unclear"]},
    "C02": {"identity": "mixed food display", "confidence": 0.58, "route": "pending_review", "uncertainty_notes": ["multiple portions unclear"]},
    "C05": {"identity": "cooked white rice", "confidence": 0.61, "route": "pending_review", "uncertainty_notes": ["serving size unclear"]},
    "C06": {"identity": "olive oil bottle", "confidence": 0.6, "route": "pending_review", "uncertainty_notes": ["amount consumed required"]},
    "C08b": {"identity": "grilled chicken", "confidence": 0.63, "route": "pending_review", "uncertainty_notes": ["portion unclear"]},
    "C08c": {"identity": "fried chicken", "confidence": 0.63, "route": "pending_review", "uncertainty_notes": ["portion unclear"]},
}


BARCODE_OBSERVED_FIXTURES: dict[str, dict[str, Any] | None] = {
    "3017620422003": {
        "item_name": "Nutella",
        "source": "open_food_facts_barcode",
        "calories": 539,
        "protein_g": 6.3,
        "carbs_g": 57.5,
        "fat_g": 30.9,
        "portion_basis": "100 g Open Food Facts packaged-food reference",
    },
    "5449000000996": {
        "item_name": "Coca-Cola Original Taste",
        "source": "open_food_facts_barcode",
        "calories": 139,
        "protein_g": 0.0,
        "carbs_g": 35.0,
        "fat_g": 0.0,
        "portion_basis": "Open Food Facts package serving",
    },
    "5449000131805": {
        "item_name": "Coke Zero",
        "source": "open_food_facts_barcode",
        "calories": 0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "portion_basis": "Open Food Facts package serving",
    },
    "7622210449283": {
        "item_name": "LU Prince biscuits",
        "source": "open_food_facts_barcode",
        "calories": 467,
        "protein_g": 6.3,
        "carbs_g": 69.0,
        "fat_g": 17.0,
        "portion_basis": "100 g Open Food Facts packaged-food reference",
    },
    "0000000000000": None,
}


def _source_matches(observed: dict[str, Any], expected_source: str) -> bool:
    return observed.get("source") == expected_source or observed.get("underlying_source") == expected_source


def _identity_matches(observed: dict[str, Any], expected_identity: str) -> bool:
    return expected_identity.lower() in str(observed.get("item_name") or "").lower()


def _macro_matches(observed: dict[str, Any], key: str, expected: float | None) -> bool:
    if expected is None:
        return observed.get(key) is None
    try:
        actual = float(observed[key])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isclose(actual, float(expected), rel_tol=0, abs_tol=0.05)


def score_barcode_case(case: BarcodeSmokeCase, observed: dict[str, Any] | None) -> dict[str, Any]:
    if not case.strict_nutrition_key:
        passed = observed is None and case.expected_status == "manual_pending"
        return {
            "case_id": case.case_id,
            "barcode": case.barcode,
            "expected_status": case.expected_status,
            "strict_nutrition_key": case.strict_nutrition_key,
            "expected_source": case.expected_source,
            "observed_source": None if observed is None else observed.get("source"),
            "calories": None if observed is None else observed.get("calories"),
            "passed": passed,
            "checks": {
                "manual_pending": passed,
            },
        }

    observed = observed or {}
    source_passed = _source_matches(observed, case.expected_source)
    identity_passed = _identity_matches(observed, case.expected_identity)
    macro_checks = {
        "calories": _macro_matches(observed, "calories", case.calories),
        "protein_g": _macro_matches(observed, "protein_g", case.protein_g),
        "carbs_g": _macro_matches(observed, "carbs_g", case.carbs_g),
        "fat_g": _macro_matches(observed, "fat_g", case.fat_g),
    }
    portion_basis = str(observed.get("portion_basis") or "")
    portion_passed = bool(portion_basis)
    passed = all((source_passed, identity_passed, portion_passed, *macro_checks.values()))
    return {
        "case_id": case.case_id,
        "barcode": case.barcode,
        "expected_status": case.expected_status,
        "strict_nutrition_key": case.strict_nutrition_key,
        "expected_source": case.expected_source,
        "observed_source": observed.get("source"),
        "observed_underlying_source": observed.get("underlying_source"),
        "observed_identity": observed.get("item_name"),
        "calories": observed.get("calories"),
        "passed": bool(passed),
        "checks": {
            "source": source_passed,
            "identity": identity_passed,
            "portion_basis": portion_passed,
            **macro_checks,
        },
    }


def score_public_photo_case(case: PublicPhotoSmokeCase, observed: dict[str, Any]) -> dict[str, Any]:
    identity = str(observed.get("identity") or "").lower()
    identity_passed = all(term.lower() in identity for term in case.expected_identity_terms)
    route_passed = observed.get("route") == case.expected_route
    confidence = float(observed.get("confidence", 1.0))
    confidence_passed = confidence <= case.max_confidence
    uncertainty_passed = bool(observed.get("uncertainty_notes"))
    calorie_keys = ("exact_calories", "calories", "protein_g", "carbs_g", "fat_g")
    no_exact_calorie_key = all(observed.get(key) is None for key in calorie_keys)
    no_raw_image = observed.get("raw_image_committed", case.raw_image_committed) is False
    passed = all((
        identity_passed,
        route_passed,
        confidence_passed,
        uncertainty_passed,
        no_exact_calorie_key,
        no_raw_image,
    ))
    return {
        "case_id": case.case_id,
        "source": case.source,
        "license": case.license,
        "expected_route": case.expected_route,
        "max_confidence": case.max_confidence,
        "grade_focus": list(case.grade_focus),
        "exact_calories": case.exact_calories,
        "raw_image_committed": observed.get("raw_image_committed", case.raw_image_committed),
        "passed": passed,
        "checks": {
            "identity": identity_passed,
            "route": route_passed,
            "confidence": confidence_passed,
            "uncertainty": uncertainty_passed,
            "no_exact_calorie_key": no_exact_calorie_key,
            "no_raw_image": no_raw_image,
        },
    }


def run_public_food_smoke_suite(
    barcode_observations: dict[str, dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    if barcode_observations is None:
        barcode_observations = BARCODE_OBSERVED_FIXTURES
    barcode_results = [
        score_barcode_case(case, barcode_observations.get(case.barcode))
        for case in BARCODE_CASES
    ]
    photo_results = [
        score_public_photo_case(case, PHOTO_OBSERVED_FIXTURES[case.case_id])
        for case in PUBLIC_PHOTO_CASES
    ]
    all_results = barcode_results + photo_results
    return {
        "suite_id": "fit-202-public-food-smoke",
        "ci_safe": True,
        "live_network": False,
        "raw_images_committed": False,
        "barcode_case_count": len(barcode_results),
        "public_photo_case_count": len(photo_results),
        "case_count": len(all_results),
        "passed_count": sum(1 for row in all_results if row["passed"]),
        "barcode_results": barcode_results,
        "public_photo_results": photo_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="Optional output JSON path")
    args = parser.parse_args()

    payload = run_public_food_smoke_suite()
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
