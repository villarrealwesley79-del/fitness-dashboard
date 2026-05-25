#!/usr/bin/env python3
"""Analyze safe Qwen3-VL food-photo macro accuracy artifacts for FIT-174."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


DEFAULT_ARTIFACT = Path("docs/meal-model-benchmark-qwen3vl-30b-vs-32b-2026-05-24.json")
DEFAULT_MODEL = "qwen3-vl-30b-a3b-instruct@q4_k_xl-scale-cue"
GRAM_MACROS = ("protein_g", "carbs_g", "fat_g")
MACROS = ("calories", *GRAM_MACROS)
GRAM_DENOMINATOR_FLOOR = 10.0
CALORIE_DENOMINATOR_FLOOR = 50.0
TOP_CASE_LIMIT = 10


def _percentage_error(absolute_error: float, gold: float, macro: str) -> float:
    floor = CALORIE_DENOMINATOR_FLOOR if macro == "calories" else GRAM_DENOMINATOR_FLOOR
    denominator = max(abs(float(gold)), floor)
    return round(float(absolute_error) / denominator * 100, 2)


def _case_adjusted_macro_mape(case: dict[str, Any]) -> float:
    adjusted_errors = [
        _percentage_error(
            macro_payload["absolute_error"],
            macro_payload["gold"],
            macro,
        )
        for macro, macro_payload in case["per_macro"].items()
        if macro in MACROS
    ]
    return round(mean(adjusted_errors), 2)


def _summarize_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    category_values: dict[str, list[dict[str, float]]] = defaultdict(list)
    macro_original: dict[str, list[float]] = defaultdict(list)
    macro_adjusted: dict[str, list[float]] = defaultdict(list)
    low_macro_outliers: list[dict[str, Any]] = []
    case_rows = []

    for case in cases:
        original_mape = float(case["macro_mape_percent"])
        adjusted_mape = _case_adjusted_macro_mape(case)
        category_values[case["category"]].append({
            "original_macro_mape_percent": original_mape,
            "adjusted_macro_mape_percent": adjusted_mape,
        })
        for macro, macro_payload in case["per_macro"].items():
            if macro not in MACROS:
                continue
            original_error = float(macro_payload["absolute_percentage_error"])
            adjusted_error = _percentage_error(
                macro_payload["absolute_error"],
                macro_payload["gold"],
                macro,
            )
            macro_original[macro].append(original_error)
            macro_adjusted[macro].append(adjusted_error)
            if macro in GRAM_MACROS and abs(float(macro_payload["gold"])) < GRAM_DENOMINATOR_FLOOR:
                low_macro_outliers.append({
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "label": case["label"],
                    "macro": macro,
                    "gold": macro_payload["gold"],
                    "predicted": macro_payload["predicted"],
                    "absolute_error": macro_payload["absolute_error"],
                    "original_absolute_percentage_error": original_error,
                    "adjusted_absolute_percentage_error": adjusted_error,
                })
        case_rows.append({
            "case_id": case["case_id"],
            "category": case["category"],
            "label": case["label"],
            "original_macro_mape_percent": original_mape,
            "adjusted_macro_mape_percent": adjusted_mape,
            "improvement_points": round(original_mape - adjusted_mape, 2),
        })

    return {
        "case_count": len(cases),
        "schema_valid_count": sum(1 for case in cases if case.get("schema_valid") is True),
        "original_macro_mape_percent": round(mean(case["macro_mape_percent"] for case in cases), 2),
        "adjusted_macro_mape_percent": round(mean(row["adjusted_macro_mape_percent"] for row in case_rows), 2),
        "category_summary": {
            category: {
                "case_count": len(values),
                "original_macro_mape_percent": round(mean(v["original_macro_mape_percent"] for v in values), 2),
                "adjusted_macro_mape_percent": round(mean(v["adjusted_macro_mape_percent"] for v in values), 2),
            }
            for category, values in sorted(category_values.items())
        },
        "macro_summary": {
            macro: {
                "original_mean_absolute_percentage_error": round(mean(macro_original[macro]), 2),
                "adjusted_mean_absolute_percentage_error": round(mean(macro_adjusted[macro]), 2),
            }
            for macro in MACROS
        },
        "top_original_cases": sorted(
            case_rows,
            key=lambda row: row["original_macro_mape_percent"],
            reverse=True,
        )[:TOP_CASE_LIMIT],
        "top_low_macro_outliers": sorted(
            low_macro_outliers,
            key=lambda row: row["original_absolute_percentage_error"],
            reverse=True,
        )[:TOP_CASE_LIMIT],
    }


def analyze_artifact(payload: dict[str, Any], model: str = DEFAULT_MODEL) -> dict[str, Any]:
    safe_results = payload.get("safe_case_results")
    if not isinstance(safe_results, dict):
        raise ValueError("artifact is missing safe_case_results")
    cases = safe_results.get(model)
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"artifact is missing model results: {model}")

    summary = _summarize_cases(cases)
    return {
        "analysis_id": "fit-174-qwen3vl-macro-error-analysis",
        "source_benchmark_id": payload.get("benchmark_id"),
        "linear_issue": "FIT-174",
        "model": model,
        "decision": {
            "selected_next_fix": "metric_adjustment",
            "reason": (
                "The dominant remaining macro error is low/zero gram macro denominator inflation, "
                "especially carbohydrates on single-item protein foods; prompt or parser changes "
                "cannot be honestly proven from the safe artifact without the private image set."
            ),
            "rejected_options": {
                "prompt_changes": "Need a private-image rerun to prove before/after behavior.",
                "package_label_parsing_rules": "Packaged labels are elevated, but not the largest remaining aggregate driver.",
                "per_category_calibration": "Risky without a private rerun because it would alter real estimates from gold-only analysis.",
                "small_reference_nutrition_lookup": "Useful later for branded/packaged foods, but the largest outliers are generic single-item foods.",
            },
        },
        "metric_adjustment": {
            "name": "low_macro_floor_mape",
            "description": (
                "Replay the full safe 50-case result set with a 10g denominator floor for gram macros "
                "and a 50 kcal denominator floor for calories. This keeps absolute errors visible while "
                "removing pathological percentage inflation when gold carbohydrate, fat, or protein is near zero."
            ),
            "gram_denominator_floor": GRAM_DENOMINATOR_FLOOR,
            "calorie_denominator_floor": CALORIE_DENOMINATOR_FLOOR,
        },
        "safe_replay": summary,
        "privacy": {
            "raw_images_included": False,
            "private_image_map_included": False,
            "uses_safe_case_results_only": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", help="Optional path for the analysis JSON")
    args = parser.parse_args()

    artifact_path = Path(args.artifact)
    with artifact_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    analysis = analyze_artifact(payload, model=args.model)
    rendered = json.dumps(analysis, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
