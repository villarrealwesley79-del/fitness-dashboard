#!/usr/bin/env python3
"""Run FIT-176 private-image macro experiments with safe aggregate output."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_vision_adapter import (  # noqa: E402
    LM_STUDIO_MODEL as SERVED_LM_STUDIO_MODEL,
    LM_STUDIO_URL as SERVED_LM_STUDIO_URL,
    LocalVisionError,
    _lm_studio_prompt,
    _lm_studio_candidates,
    _meal_estimate_response_schema,
    _parse_lm_studio_meal_estimate,
    _vision_temperature,
)
from meal_model_benchmark import _structured_response_schema_errors  # noqa: E402
from meal_model_macro_accuracy_analysis import (
    CALORIE_DENOMINATOR_FLOOR,
    DEFAULT_ARTIFACT,
    DEFAULT_MODEL,
    GRAM_DENOMINATOR_FLOOR,
    MACROS,
    TOP_CASE_LIMIT,
)


DEFAULT_FIT174_MODEL_KEY = DEFAULT_MODEL
LINEAR_ISSUE = "FIT-176"
RAW_OUTPUT_FILENAME = "private_case_results.jsonl"
RUNNABLE_ROUTES = ("baseline", "prompt_iteration", "package_label")
REJECTED_ROUTES = ("reference_lookup",)
ALL_ROUTES = (*RUNNABLE_ROUTES, *REJECTED_ROUTES)
SAFE_OUTPUT_VERSION = 1
BASELINE_PROMPT = _lm_studio_prompt(None)
SERVED_MEAL_ESTIMATE_RESPONSE_SCHEMA = _meal_estimate_response_schema()
ROUTE_PROMPTS = {
    "baseline": BASELINE_PROMPT,
    "prompt_iteration": (
        BASELINE_PROMPT
        + " FIT-176 candidate instruction: For single-item protein foods, do not invent "
        "carbohydrates unless a visible sauce, breading, starch, wrapper, label, or user "
        "context supports them. If the plate appears to contain only plain protein, set "
        "carbs_g near zero, explain uncertainty in uncertainty_notes, and keep the required "
        "items array valid."
    ),
    "package_label": (
        BASELINE_PROMPT
        + " FIT-176 candidate instruction: If a package label, receipt, wrapper, or branded "
        "panel is visible, OCR serving size and nutrition facts first, then estimate the "
        "consumed serving count. If no label is visible, use the same strict meal-estimate "
        "schema without claiming label evidence. Do not include OCR trace."
    ),
}
REFERENCE_LOOKUP_REJECTION = (
    "Rejected in FIT-176 harness by default: lookup keys would be derived from private images. "
    "Run this route only in a separate implementation that proves lookup keys and traces remain "
    "private and publishes aggregate metrics only."
)
PRIVATE_PATTERN = re.compile(
    r"("
    r"data:image|base64|"
    r"\.(?:jpg|jpeg|png|heic|webp)\b|"
    r"\.env\b|"
    r"\.(?:db|sqlite|sqlite3)\b|"
    r"(?:^|/)(?:tmp|Users|private|var|Volumes)/|"
    r"image[_-]?map|"
    r"\"(?:raw_model_text|raw_prompt|raw_response|lookup_key|private_lookup_key)\""
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PrivateCase:
    case_id: str
    category: str
    label: str
    gold: dict[str, float]


def load_fit174_cases(payload: dict[str, Any], model: str = DEFAULT_MODEL) -> list[PrivateCase]:
    safe_results = payload.get("safe_case_results")
    if not isinstance(safe_results, dict):
        raise ValueError("FIT-174 artifact is missing safe_case_results")
    rows = safe_results.get(model)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"FIT-174 artifact is missing model results: {model}")
    cases = []
    for row in rows:
        per_macro = row.get("per_macro")
        if not isinstance(per_macro, dict):
            raise ValueError(f"case {row.get('case_id')} is missing per_macro")
        gold = {}
        for macro in MACROS:
            macro_payload = per_macro.get(macro)
            if not isinstance(macro_payload, dict) or "gold" not in macro_payload:
                raise ValueError(f"case {row.get('case_id')} is missing gold macro: {macro}")
            gold[macro] = float(macro_payload["gold"])
        cases.append(
            PrivateCase(
                case_id=str(row["case_id"]),
                category=str(row["category"]),
                label=str(row.get("label") or row["case_id"]),
                gold=gold,
            )
        )
    return cases


def load_json(path: str | Path) -> dict[str, Any]:
    json_path = Path(path)
    if not json_path.is_file():
        raise ValueError(f"JSON file not found: {json_path}")
    with json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def load_image_map(path: str | Path) -> dict[str, Path]:
    payload = load_json(path)
    return {str(case_id): Path(str(image_path)).expanduser() for case_id, image_path in payload.items()}


def validate_private_inputs(cases: list[PrivateCase], image_map: dict[str, Path]) -> None:
    required_ids = {case.case_id for case in cases}
    mapped_ids = set(image_map)
    missing = sorted(required_ids - mapped_ids)
    extra = sorted(mapped_ids - required_ids)
    if missing:
        raise ValueError(f"image map is missing {len(missing)} FIT-174 case IDs; first missing: {missing[0]}")
    if extra:
        raise ValueError(f"image map contains {len(extra)} unknown case IDs; first unknown: {extra[0]}")
    for case_id, path in image_map.items():
        if not path.is_file():
            raise ValueError(f"private image for {case_id} is not readable")


def ensure_private_output_dir(path: str | Path, *, repo_root: Path | None = None) -> Path:
    output_dir = Path(path).expanduser().resolve()
    repo = (repo_root or REPO_ROOT).resolve()
    if output_dir == repo or repo in output_dir.parents:
        raise ValueError("private output directory must be outside the repository")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _image_part(image_path: Path) -> dict[str, Any]:
    media_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
    }


def _chat_payload(case: PrivateCase, route: str, model: str, image_path: Path) -> dict[str, Any]:
    prompt = ROUTE_PROMPTS[route]
    content = [
        {"type": "text", "text": prompt},
        _image_part(image_path),
    ]
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "temperature": _vision_temperature(),
        "max_tokens": 700,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "meal_estimate",
                "strict": True,
                "schema": SERVED_MEAL_ESTIMATE_RESPONSE_SCHEMA,
            },
        },
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _served_schema_errors(estimate: dict[str, Any] | None) -> list[str]:
    _, errors = _served_estimate(estimate)
    return errors


def _served_estimate(estimate: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    if estimate is None:
        return None, ["response_not_json_object"]
    errors = _structured_response_schema_errors(estimate, SERVED_MEAL_ESTIMATE_RESPONSE_SCHEMA)
    if not errors:
        try:
            return _parse_lm_studio_meal_estimate(json.dumps(estimate)), []
        except LocalVisionError as exc:
            errors.append(f"invalid_served_meal_estimate:{exc}")
    return None, list(dict.fromkeys(errors))


def run_private_case(
    case: PrivateCase,
    *,
    route: str,
    model: str,
    lm_studio_url: str,
    image_path: Path,
    timeout: float,
    retry_limit: int,
) -> dict[str, Any]:
    started = time.time()
    attempts = []
    for candidate in _request_candidates(model=model, lm_studio_url=lm_studio_url):
        for attempt in range(retry_limit + 1):
            try:
                body = json.dumps(_chat_payload(case, route, candidate["model"], image_path)).encode("utf-8")
                request = urllib.request.Request(
                    f"{candidate['url'].rstrip('/')}/v1/chat/completions",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    response_body = json.loads(response.read().decode("utf-8"))
                choices = response_body.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise ValueError("completion choices missing")
                message = choices[0].get("message") or {}
                content = message.get("content") or message.get("reasoning_content") or ""
                estimate, errors = _served_estimate(_extract_json_object(content))
                attempts.append({
                    "attempt": attempt,
                    "candidate_role": candidate["role"],
                    "schema_errors": errors,
                    "raw_model_text": content,
                    "raw_response": response_body,
                })
                if not errors:
                    latency_ms = int((time.time() - started) * 1000)
                    return _case_result(
                        case,
                        route=route,
                        model=candidate["model"],
                        ran_model=True,
                        latency_ms=latency_ms,
                        schema_errors=[],
                        schema_retries=attempt,
                        estimate=estimate,
                        private_attempts=attempts,
                    )
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
                attempts.append({
                    "attempt": attempt,
                    "candidate_role": candidate["role"],
                    "schema_errors": [f"request_failed:{exc}"],
                    "raw_model_text": "",
                    "raw_response": None,
                })
                break

    latency_ms = int((time.time() - started) * 1000)
    final_errors = attempts[-1]["schema_errors"] if attempts else ["request_failed:unknown"]
    return _case_result(
        case,
        route=route,
        model=model,
        ran_model=False,
        latency_ms=latency_ms,
        schema_errors=final_errors,
        schema_retries=max(0, len(attempts) - 1),
        estimate=None,
        private_attempts=attempts,
    )


def _request_candidates(*, model: str, lm_studio_url: str) -> list[dict[str, str]]:
    if model == SERVED_LM_STUDIO_MODEL and lm_studio_url.rstrip("/") == SERVED_LM_STUDIO_URL.rstrip("/"):
        return _lm_studio_candidates()
    return [{"role": "override", "url": lm_studio_url.rstrip("/"), "model": model}]


def _case_result(
    case: PrivateCase,
    *,
    route: str,
    model: str,
    ran_model: bool,
    latency_ms: int,
    schema_errors: list[str],
    schema_retries: int,
    estimate: dict[str, Any] | None,
    private_attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    estimate = estimate if estimate and not schema_errors else None
    per_macro = _per_macro(case.gold, estimate)
    macro_mape = round(mean(macro["absolute_percentage_error"] for macro in per_macro.values()), 2)
    return {
        "case_id": case.case_id,
        "category": case.category,
        "route": route,
        "model": model,
        "ran_model": ran_model,
        "latency_ms": latency_ms,
        "schema_valid": not schema_errors,
        "schema_errors": schema_errors,
        "schema_retries": schema_retries,
        "estimate": _public_estimate(estimate),
        "macro_mape_percent": macro_mape,
        "per_macro": per_macro,
        "private_attempts": private_attempts,
    }


def _public_estimate(estimate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not estimate:
        return None
    return {
        key: estimate.get(key)
        for key in (
            "item_name",
            "portion_description",
            "meal_type",
            "calories",
            "protein_g",
            "carbs_g",
            "fat_g",
            "sodium_mg",
            "fiber_g",
            "confidence",
            "ambiguous",
            "uncertainty_notes",
        )
    }


def _per_macro(gold: dict[str, float], estimate: dict[str, Any] | None) -> dict[str, dict[str, float]]:
    per_macro = {}
    for macro in MACROS:
        predicted = _number_or_zero(estimate.get(macro) if estimate else None)
        absolute_error = abs(predicted - float(gold[macro]))
        per_macro[macro] = {
            "gold": float(gold[macro]),
            "predicted": predicted,
            "absolute_error": round(absolute_error, 2),
            "absolute_percentage_error": _percentage_error(absolute_error, float(gold[macro]), macro, adjusted=False),
            "low_macro_floor_percentage_error": _percentage_error(absolute_error, float(gold[macro]), macro, adjusted=True),
        }
    return per_macro


def _number_or_zero(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _percentage_error(absolute_error: float, gold: float, macro: str, *, adjusted: bool) -> float:
    if adjusted:
        floor = CALORIE_DENOMINATOR_FLOOR if macro == "calories" else GRAM_DENOMINATOR_FLOOR
    else:
        floor = 1.0
    denominator = max(abs(float(gold)), floor)
    return round(float(absolute_error) / denominator * 100, 2)


def run_experiment(
    cases: list[PrivateCase],
    *,
    image_map: dict[str, Path],
    private_output_dir: Path,
    model: str,
    lm_studio_url: str,
    timeout: float,
    retry_limit: int,
    fit174_model_key: str = DEFAULT_FIT174_MODEL_KEY,
    routes: tuple[str, ...] = ALL_ROUTES,
) -> dict[str, Any]:
    validate_private_inputs(cases, image_map)
    private_output_dir = ensure_private_output_dir(private_output_dir)
    route_results = {}
    for route in routes:
        if route in REJECTED_ROUTES:
            route_results[route] = {
                "status": "rejected",
                "reason": REFERENCE_LOOKUP_REJECTION,
                "case_ids": [case.case_id for case in cases],
            }
            continue
        if route not in RUNNABLE_ROUTES:
            raise ValueError(f"unknown route: {route}")
        raw_route_dir = private_output_dir / route
        raw_route_dir.mkdir(parents=True, exist_ok=True)
        results = []
        with (raw_route_dir / RAW_OUTPUT_FILENAME).open("w", encoding="utf-8") as handle:
            for case in cases:
                result = run_private_case(
                    case,
                    route=route,
                    model=model,
                    lm_studio_url=lm_studio_url,
                    image_path=image_map[case.case_id],
                    timeout=timeout,
                    retry_limit=retry_limit,
                )
                private_result = dict(result)
                private_result["image_path"] = str(image_map[case.case_id])
                handle.write(json.dumps(private_result, sort_keys=True) + "\n")
                results.append(_safe_case_result(result))
        route_results[route] = {"status": "ran", "case_results": results}
    return build_safe_aggregate(cases, route_results, model=model, fit174_model_key=fit174_model_key)


def _safe_case_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result[key]
        for key in (
            "case_id",
            "category",
            "route",
            "model",
            "ran_model",
            "latency_ms",
            "schema_valid",
            "schema_errors",
            "schema_retries",
            "estimate",
            "macro_mape_percent",
            "per_macro",
        )
    }


def build_safe_aggregate(
    cases: list[PrivateCase],
    route_results: dict[str, dict[str, Any]],
    *,
    model: str = SERVED_LM_STUDIO_MODEL,
    fit174_model_key: str = DEFAULT_FIT174_MODEL_KEY,
) -> dict[str, Any]:
    case_ids = [case.case_id for case in cases]
    routes = {}
    for route, payload in route_results.items():
        if payload.get("status") == "rejected":
            routes[route] = {
                "status": "rejected",
                "reason": payload["reason"],
                "case_count": len(case_ids),
                "case_ids": case_ids,
            }
            continue
        rows = payload.get("case_results") or []
        _validate_identical_case_set(case_ids, [str(row.get("case_id")) for row in rows], route)
        routes[route] = summarize_route(route, rows)

    decision = choose_next_fix(routes)
    safe = {
        "artifact_version": SAFE_OUTPUT_VERSION,
        "linear_issue": LINEAR_ISSUE,
        "source_issue": "FIT-174",
        "model": model,
        "fit174_model_key": fit174_model_key,
        "case_count": len(case_ids),
        "case_ids": case_ids,
        "routes": routes,
        "decision": decision,
        "privacy": {
            "raw_images_included": False,
            "map_file_included": False,
            "private_paths_included": False,
            "model_text_included": False,
            "prompts_included": False,
            "reference_keys_included": False,
        },
    }
    assert_safe_artifact(safe)
    return safe


def _validate_identical_case_set(expected: list[str], actual: list[str], route: str) -> None:
    if actual != expected:
        raise ValueError(f"route {route} did not preserve the FIT-174 case order")


def summarize_route(route: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    category_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    macro_rows: dict[str, list[dict[str, float]]] = defaultdict(list)
    top_rows = []
    for row in rows:
        category_rows[row["category"]].append(row)
        for macro, macro_payload in row["per_macro"].items():
            macro_rows[macro].append(macro_payload)
            top_rows.append({
                "case_id": row["case_id"],
                "category": row["category"],
                "macro": macro,
                "gold": macro_payload["gold"],
                "predicted": macro_payload["predicted"],
                "absolute_error": macro_payload["absolute_error"],
            })
    return {
        "status": "ran",
        "route_name": route,
        "case_count": len(rows),
        "schema_valid_count": sum(1 for row in rows if row.get("schema_valid") is True),
        "schema_retry_summary": _schema_retry_summary(rows),
        "category_summary": {
            category: _summarize_rows(values)
            for category, values in sorted(category_rows.items())
        },
        "macro_summary": {
            macro: _summarize_macro(values)
            for macro, values in sorted(macro_rows.items())
        },
        "top_outliers": sorted(top_rows, key=lambda row: row["absolute_error"], reverse=True)[:TOP_CASE_LIMIT],
    }


def _schema_retry_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    retries = [int(row.get("schema_retries") or 0) for row in rows]
    return {
        "total": sum(retries),
        "max": max(retries) if retries else 0,
        "cases_with_retries": sum(1 for retry in retries if retry > 0),
    }


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_count": len(rows),
        "schema_valid_count": sum(1 for row in rows if row.get("schema_valid") is True),
        "mean_absolute_error": round(mean(_case_mean_absolute_error(row) for row in rows), 2),
        "original_macro_mape_percent": round(mean(row["macro_mape_percent"] for row in rows), 2),
        "low_macro_floor_mape_percent": round(mean(_case_low_macro_floor_mape(row) for row in rows), 2),
    }


def _summarize_macro(values: list[dict[str, float]]) -> dict[str, Any]:
    return {
        "mean_absolute_error": round(mean(value["absolute_error"] for value in values), 2),
        "original_mean_absolute_percentage_error": round(
            mean(value["absolute_percentage_error"] for value in values), 2
        ),
        "low_macro_floor_mean_absolute_percentage_error": round(
            mean(value["low_macro_floor_percentage_error"] for value in values), 2
        ),
    }


def _case_mean_absolute_error(row: dict[str, Any]) -> float:
    return mean(macro["absolute_error"] for macro in row["per_macro"].values())


def _case_low_macro_floor_mape(row: dict[str, Any]) -> float:
    return mean(macro["low_macro_floor_percentage_error"] for macro in row["per_macro"].values())


def choose_next_fix(routes: dict[str, dict[str, Any]]) -> dict[str, str]:
    baseline = routes.get("baseline")
    if not baseline or baseline.get("status") != "ran":
        return {"selected_next_fix": "none", "reason": "Baseline route did not run."}

    scored = []
    for route in ("prompt_iteration", "package_label"):
        payload = routes.get(route)
        if not payload or payload.get("status") != "ran":
            continue
        improvement = _route_improvement(baseline, payload)
        category_regression = _has_category_regression(baseline, payload)
        schema_regression = payload["schema_valid_count"] < baseline["schema_valid_count"]
        retry_regression = _has_schema_retry_regression(baseline, payload)
        route_specific_regression = _has_route_specific_regression(route, baseline, payload)
        scored.append((route, improvement, category_regression, schema_regression, retry_regression, route_specific_regression))
    eligible = [
        (route, improvement)
        for route, improvement, category_regression, schema_regression, retry_regression, route_specific_regression in scored
        if (
            improvement > 0
            and not category_regression
            and not schema_regression
            and not retry_regression
            and not route_specific_regression
        )
    ]
    if not eligible:
        return {
            "selected_next_fix": "none",
            "reason": "No candidate showed clean aggregate improvement without category or schema regression.",
        }
    selected, improvement = max(eligible, key=lambda item: item[1])
    selected_name = {
        "prompt_iteration": "prompt_iteration",
        "package_label": "package_label_parsing",
    }[selected]
    return {
        "selected_next_fix": selected_name,
        "reason": f"{selected} improved low-macro-floor MAPE by {round(improvement, 2)} points without schema/category regression.",
    }


def _route_improvement(baseline: dict[str, Any], candidate: dict[str, Any]) -> float:
    return _aggregate_mape(baseline) - _aggregate_mape(candidate)


def _aggregate_mape(route: dict[str, Any]) -> float:
    values = [
        category["low_macro_floor_mape_percent"]
        for category in route["category_summary"].values()
    ]
    return mean(values)


def _has_category_regression(baseline: dict[str, Any], candidate: dict[str, Any]) -> bool:
    for category, baseline_summary in baseline["category_summary"].items():
        candidate_summary = candidate["category_summary"].get(category)
        if not candidate_summary:
            return True
        if candidate_summary["low_macro_floor_mape_percent"] > baseline_summary["low_macro_floor_mape_percent"]:
            return True
    return False


def _has_schema_retry_regression(baseline: dict[str, Any], candidate: dict[str, Any]) -> bool:
    baseline_retries = baseline["schema_retry_summary"]
    candidate_retries = candidate["schema_retry_summary"]
    return (
        candidate_retries["total"] > baseline_retries["total"]
        or candidate_retries["cases_with_retries"] > baseline_retries["cases_with_retries"]
        or candidate_retries["max"] > baseline_retries["max"]
    )


def _has_route_specific_regression(route: str, baseline: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if route == "prompt_iteration":
        baseline_single = baseline["category_summary"].get("single_item_plate")
        candidate_single = candidate["category_summary"].get("single_item_plate")
        if not baseline_single or not candidate_single:
            return True
        return candidate_single["low_macro_floor_mape_percent"] >= baseline_single["low_macro_floor_mape_percent"]
    if route != "package_label":
        return False
    baseline_packaged = baseline["category_summary"].get("packaged_label")
    candidate_packaged = candidate["category_summary"].get("packaged_label")
    if not baseline_packaged or not candidate_packaged:
        return True
    return candidate_packaged["low_macro_floor_mape_percent"] >= baseline_packaged["low_macro_floor_mape_percent"]


def assert_safe_artifact(payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, sort_keys=True)
    match = PRIVATE_PATTERN.search(rendered)
    if match:
        raise ValueError(f"safe artifact contains private marker: {match.group(0)}")


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    assert_safe_artifact(payload)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_routes(raw: str) -> tuple[str, ...]:
    routes = tuple(route.strip() for route in raw.split(",") if route.strip())
    unknown = sorted(set(routes) - set(ALL_ROUTES))
    if unknown:
        raise ValueError(f"unknown routes: {', '.join(unknown)}")
    return routes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit174-artifact", default=str(DEFAULT_ARTIFACT))
    parser.add_argument("--image-map", required=True, help="Local-only JSON mapping FIT-174 case IDs to private image paths")
    parser.add_argument("--private-output-dir", required=True, help="Untracked directory for raw private per-case outputs")
    parser.add_argument("--safe-output", required=True, help="Safe aggregate JSON output path")
    parser.add_argument("--fit174-model-key", default=DEFAULT_FIT174_MODEL_KEY)
    parser.add_argument("--model", default=SERVED_LM_STUDIO_MODEL)
    parser.add_argument("--lm-studio-url", default=SERVED_LM_STUDIO_URL)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--schema-retry-limit", type=int, default=2)
    parser.add_argument("--routes", default=",".join(ALL_ROUTES))
    args = parser.parse_args()

    try:
        artifact = load_json(args.fit174_artifact)
        cases = load_fit174_cases(artifact, model=args.fit174_model_key)
        image_map = load_image_map(args.image_map)
        private_output_dir = ensure_private_output_dir(args.private_output_dir)
        routes = parse_routes(args.routes)
    except ValueError as exc:
        parser.error(str(exc))
    safe = run_experiment(
        cases,
        image_map=image_map,
        private_output_dir=private_output_dir,
        model=args.model,
        fit174_model_key=args.fit174_model_key,
        lm_studio_url=args.lm_studio_url,
        timeout=args.timeout,
        retry_limit=args.schema_retry_limit,
        routes=routes,
    )
    write_json(args.safe_output, safe)
    print(json.dumps({"safe_output": args.safe_output, "case_count": safe["case_count"], "decision": safe["decision"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
