from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[1]
    support = root / "support"
    if str(support) not in sys.path:
        sys.path.insert(0, str(support))
    path = support / "meal_model_private_image_experiment.py"
    spec = importlib.util.spec_from_file_location("meal_model_private_image_experiment", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _macro(gold, predicted):
    absolute_error = abs(predicted - gold)
    return {
        "gold": gold,
        "predicted": predicted,
        "absolute_error": absolute_error,
        "absolute_percentage_error": round(absolute_error / max(abs(gold), 1) * 100, 2),
    }


def _case(case_id, category, label, calories, protein, carbs, fat):
    return {
        "case_id": case_id,
        "category": category,
        "label": label,
        "schema_valid": True,
        "schema_retries": 0,
        "macro_mape_percent": 10,
        "per_macro": {
            "calories": _macro(calories, calories),
            "protein_g": _macro(protein, protein),
            "carbs_g": _macro(carbs, carbs),
            "fat_g": _macro(fat, fat),
        },
    }


def _artifact(module):
    return {
        "benchmark_id": "fit-174-safe-fixture",
        "safe_case_results": {
            module.DEFAULT_MODEL: [
                _case("single-001", "single_item_plate", "steak", 430, 46, 0, 26),
                _case("packaged-001", "packaged_label", "protein bar", 220, 20, 24, 7),
            ]
        },
    }


def _estimate(calories, protein, carbs, fat):
    return {
        "item_name": "Meal",
        "portion_description": "one serving",
        "meal_type": "lunch",
        "calories": calories,
        "protein_g": protein,
        "carbs_g": carbs,
        "fat_g": fat,
        "sodium_mg": 500,
        "fiber_g": 3,
        "confidence": 0.7,
        "ambiguous": False,
        "uncertainty_notes": [],
        "items": [
            {
                "item_name": "Meal",
                "quantity": 1,
                "brand": None,
                "modifiers": [],
                "portion_hint": "one serving",
            }
        ],
    }


def _result(module, case, route, estimate, retries=0):
    return module._case_result(
        case,
        route=route,
        model=module.DEFAULT_MODEL,
        ran_model=True,
        latency_ms=100,
        schema_errors=[],
        schema_retries=retries,
        estimate=estimate,
        private_attempts=[],
    )


def test_missing_private_image_map_fails_clearly(tmp_path):
    module = _load_module()
    cases = module.load_fit174_cases(_artifact(module))
    image_map = {cases[0].case_id: tmp_path / "synthetic-one.bin"}
    image_map[cases[0].case_id].write_bytes(b"synthetic")

    try:
        module.validate_private_inputs(cases, image_map)
    except ValueError as exc:
        assert "image map is missing 1 FIT-174 case IDs" in str(exc)
        assert cases[1].case_id in str(exc)
    else:
        raise AssertionError("missing private image map entry should fail")


def test_missing_image_map_file_fails_clearly(tmp_path):
    module = _load_module()

    try:
        module.load_image_map(tmp_path / "missing-map.json")
    except ValueError as exc:
        assert "JSON file not found" in str(exc)
        assert "missing-map.json" in str(exc)
    else:
        raise AssertionError("missing private image-map file should fail")


def test_private_output_dir_uses_repo_root_even_from_subdir(monkeypatch):
    module = _load_module()
    monkeypatch.chdir(module.REPO_ROOT / "support")

    try:
        module.ensure_private_output_dir(module.REPO_ROOT / "fit176-private-runs")
    except ValueError as exc:
        assert "outside the repository" in str(exc)
    else:
        raise AssertionError("private output inside repo should fail from subdir cwd")


def test_baseline_payload_matches_current_served_route(tmp_path):
    module = _load_module()
    cases = module.load_fit174_cases(_artifact(module))
    image = tmp_path / "synthetic.bin"
    image.write_bytes(b"synthetic-public-test-bytes")

    payload = module._chat_payload(cases[0], "baseline", module.SERVED_LM_STUDIO_MODEL, image)
    content = payload["messages"][0]["content"]

    assert module.DEFAULT_FIT174_MODEL_KEY == module.DEFAULT_MODEL
    assert payload["model"] == module.SERVED_LM_STUDIO_MODEL
    assert payload["temperature"] == module._vision_temperature()
    assert payload["max_tokens"] == 700
    assert content[0]["text"] == module.BASELINE_PROMPT
    assert content[0]["text"] == module._lm_studio_prompt(None)
    assert "use visible scale cues" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert payload["response_format"]["json_schema"]["schema"] == module.SERVED_MEAL_ESTIMATE_RESPONSE_SCHEMA


def test_served_schema_requires_current_items_contract():
    module = _load_module()
    estimate = _estimate(430, 46, 0, 26)
    estimate.pop("items")

    assert "missing_items" in module._served_schema_errors(estimate)


def test_fenced_json_uses_served_adapter_parsing():
    module = _load_module()
    estimate = _estimate(430, 46, 0, 26)
    rendered = "```json\n" + json.dumps(estimate) + "\n```"

    parsed = module._extract_json_object(rendered)

    assert parsed == estimate
    assert module._served_schema_errors(parsed) == []


def test_served_estimate_uses_adapter_sanitized_values():
    module = _load_module()
    estimate = _estimate(430.6, 46.26, 0, 26.24)

    sanitized, errors = module._served_estimate(estimate)

    assert errors == []
    assert sanitized["calories"] == 431
    assert sanitized["protein_g"] == 46.3
    assert sanitized["fat_g"] == 26.2


def test_request_candidates_preserve_adapter_fallback(monkeypatch):
    module = _load_module()
    candidates = [
        {"role": "primary", "url": "http://primary.test", "model": module.SERVED_LM_STUDIO_MODEL},
        {"role": "fallback", "url": "http://fallback.test", "model": "fallback-vision"},
    ]
    monkeypatch.setattr(module, "_lm_studio_candidates", lambda: candidates)

    assert module._request_candidates(
        model=module.SERVED_LM_STUDIO_MODEL,
        lm_studio_url=module.SERVED_LM_STUDIO_URL,
    ) == candidates
    assert module._request_candidates(
        model="override-model",
        lm_studio_url="http://override.test",
    ) == [{"role": "override", "url": "http://override.test", "model": "override-model"}]


def test_run_private_case_falls_back_like_served_adapter(tmp_path, monkeypatch):
    module = _load_module()
    case = module.load_fit174_cases(_artifact(module))[0]
    image = tmp_path / "synthetic.bin"
    image.write_bytes(b"synthetic-public-test-bytes")
    attempts = []
    candidates = [
        {"role": "primary", "url": "http://primary.test", "model": module.SERVED_LM_STUDIO_MODEL},
        {"role": "fallback", "url": "http://fallback.test", "model": "fallback-vision"},
    ]
    monkeypatch.setattr(module, "_lm_studio_candidates", lambda: candidates)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": json.dumps(_estimate(430.6, 46.26, 0, 26.24))}}]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        del timeout
        attempts.append(request.full_url)
        if "primary.test" in request.full_url:
            raise urllib.error.URLError("primary down")
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.run_private_case(
        case,
        route="baseline",
        model=module.SERVED_LM_STUDIO_MODEL,
        lm_studio_url=module.SERVED_LM_STUDIO_URL,
        image_path=image,
        timeout=1,
        retry_limit=2,
    )

    assert attempts == [
        "http://primary.test/v1/chat/completions",
        "http://fallback.test/v1/chat/completions",
    ]
    assert result["model"] == "fallback-vision"
    assert result["estimate"]["calories"] == 431
    assert result["estimate"]["protein_g"] == 46.3


def test_run_experiment_preserves_identical_case_set_and_private_outputs(tmp_path, monkeypatch):
    module = _load_module()
    cases = module.load_fit174_cases(_artifact(module))
    image_map = {}
    for case in cases:
        image = tmp_path / f"{case.case_id}.bin"
        image.write_bytes(b"synthetic-public-test-bytes")
        image_map[case.case_id] = image
    private_dir = tmp_path / "private-output"

    def fake_run_private_case(case, *, route, model, lm_studio_url, image_path, timeout, retry_limit):
        del lm_studio_url, image_path, timeout, retry_limit
        if route == "baseline":
            estimates = {
                "single-001": _estimate(430, 46, 12, 26),
                "packaged-001": _estimate(220, 20, 18, 7),
            }
        elif route == "prompt_iteration":
            estimates = {
                "single-001": _estimate(430, 46, 0, 26),
                "packaged-001": _estimate(220, 20, 18, 7),
            }
        else:
            estimates = {
                "single-001": _estimate(430, 46, 12, 26),
                "packaged-001": _estimate(220, 20, 24, 7),
            }
        return _result(module, case, route, estimates[case.case_id])

    monkeypatch.setattr(module, "run_private_case", fake_run_private_case)

    aggregate = module.run_experiment(
        cases,
        image_map=image_map,
        private_output_dir=private_dir,
        model=module.SERVED_LM_STUDIO_MODEL,
        lm_studio_url="http://127.0.0.1:1234",
        timeout=1,
        retry_limit=2,
        fit174_model_key=module.DEFAULT_FIT174_MODEL_KEY,
        routes=("baseline", "prompt_iteration", "package_label", "reference_lookup"),
    )

    assert aggregate["model"] == module.SERVED_LM_STUDIO_MODEL
    assert aggregate["fit174_model_key"] == module.DEFAULT_FIT174_MODEL_KEY
    assert aggregate["case_ids"] == [case.case_id for case in cases]
    assert aggregate["routes"]["baseline"]["case_count"] == len(cases)
    assert aggregate["routes"]["prompt_iteration"]["case_count"] == len(cases)
    assert aggregate["routes"]["package_label"]["case_count"] == len(cases)
    assert aggregate["routes"]["reference_lookup"]["status"] == "rejected"
    assert aggregate["routes"]["prompt_iteration"]["schema_retry_summary"]["cases_with_retries"] == 0
    assert aggregate["decision"]["selected_next_fix"] == "prompt_iteration"
    assert (private_dir / "baseline" / module.RAW_OUTPUT_FILENAME).is_file()
    assert (private_dir / "prompt_iteration" / module.RAW_OUTPUT_FILENAME).is_file()
    rendered = json.dumps(aggregate)
    assert str(tmp_path) not in rendered
    assert "synthetic-public-test-bytes" not in rendered
    assert "raw_model_text" not in rendered


def test_main_defaults_split_artifact_key_from_served_adapter(monkeypatch, tmp_path, capsys):
    module = _load_module()
    captured = {}

    def fake_run_experiment(cases, **kwargs):
        captured["cases"] = cases
        captured.update(kwargs)
        return {
            "case_count": len(cases),
            "decision": {"selected_next_fix": "none", "reason": "fixture"},
        }

    monkeypatch.setattr(module, "load_json", lambda path: _artifact(module))
    monkeypatch.setattr(module, "load_image_map", lambda path: {})
    monkeypatch.setattr(module, "ensure_private_output_dir", lambda path: Path(path))
    monkeypatch.setattr(module, "run_experiment", fake_run_experiment)
    monkeypatch.setattr(module, "write_json", lambda path, payload: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "meal_model_private_image_experiment.py",
            "--image-map",
            str(tmp_path / "image-map.json"),
            "--private-output-dir",
            str(tmp_path / "private"),
            "--safe-output",
            str(tmp_path / "safe.json"),
        ],
    )

    assert module.main() == 0

    assert captured["model"] == module.SERVED_LM_STUDIO_MODEL
    assert captured["fit174_model_key"] == module.DEFAULT_FIT174_MODEL_KEY
    assert captured["lm_studio_url"] == module.SERVED_LM_STUDIO_URL
    assert [case.case_id for case in captured["cases"]] == ["single-001", "packaged-001"]
    assert json.loads(capsys.readouterr().out)["case_count"] == 2


def test_safe_artifact_rejects_private_markers():
    module = _load_module()
    unsafe = {
        "case_id": "single-001",
        "image_path": "/Users/admin/private-food-photo.jpg",
    }

    try:
        module.assert_safe_artifact(unsafe)
    except ValueError as exc:
        assert "private marker" in str(exc)
    else:
        raise AssertionError("private path and image filename should fail privacy scan")


def test_no_recommendation_when_candidates_have_mixed_or_null_improvement():
    module = _load_module()
    cases = module.load_fit174_cases(_artifact(module))
    baseline = [
        _result(module, cases[0], "baseline", _estimate(430, 46, 0, 26)),
        _result(module, cases[1], "baseline", _estimate(220, 20, 24, 7)),
    ]
    mixed = [
        _result(module, cases[0], "prompt_iteration", _estimate(430, 46, 0, 26)),
        _result(module, cases[1], "prompt_iteration", _estimate(220, 20, 200, 7)),
    ]
    aggregate = module.build_safe_aggregate(
        cases,
        {
            "baseline": {"status": "ran", "case_results": [module._safe_case_result(row) for row in baseline]},
            "prompt_iteration": {"status": "ran", "case_results": [module._safe_case_result(row) for row in mixed]},
            "reference_lookup": {"status": "rejected", "reason": module.REFERENCE_LOOKUP_REJECTION},
        },
    )

    assert aggregate["decision"]["selected_next_fix"] == "none"
    assert "No candidate showed clean aggregate improvement" in aggregate["decision"]["reason"]


def test_no_recommendation_when_candidate_increases_schema_retries():
    module = _load_module()
    cases = module.load_fit174_cases(_artifact(module))
    baseline = [
        _result(module, cases[0], "baseline", _estimate(430, 46, 12, 26)),
        _result(module, cases[1], "baseline", _estimate(220, 20, 18, 7)),
    ]
    retry_heavy = [
        _result(module, cases[0], "prompt_iteration", _estimate(430, 46, 0, 26), retries=1),
        _result(module, cases[1], "prompt_iteration", _estimate(220, 20, 18, 7), retries=1),
    ]
    aggregate = module.build_safe_aggregate(
        cases,
        {
            "baseline": {"status": "ran", "case_results": [module._safe_case_result(row) for row in baseline]},
            "prompt_iteration": {"status": "ran", "case_results": [module._safe_case_result(row) for row in retry_heavy]},
        },
    )

    assert aggregate["decision"]["selected_next_fix"] == "none"


def test_prompt_route_must_improve_single_item_category():
    module = _load_module()
    routes = {
        "baseline": {
            "status": "ran",
            "schema_valid_count": 2,
            "schema_retry_summary": {"total": 0, "cases_with_retries": 0, "max": 0},
            "category_summary": {
                "single_item_plate": {"low_macro_floor_mape_percent": 10},
                "packaged_label": {"low_macro_floor_mape_percent": 10},
            },
        },
        "prompt_iteration": {
            "status": "ran",
            "schema_valid_count": 2,
            "schema_retry_summary": {"total": 0, "cases_with_retries": 0, "max": 0},
            "category_summary": {
                "single_item_plate": {"low_macro_floor_mape_percent": 10},
                "packaged_label": {"low_macro_floor_mape_percent": 5},
            },
        },
    }

    assert module.choose_next_fix(routes)["selected_next_fix"] == "none"


def test_package_label_route_must_improve_packaged_category():
    module = _load_module()
    cases = module.load_fit174_cases(_artifact(module))
    baseline = [
        _result(module, cases[0], "baseline", _estimate(430, 46, 12, 26)),
        _result(module, cases[1], "baseline", _estimate(220, 20, 24, 7)),
    ]
    package_candidate = [
        _result(module, cases[0], "package_label", _estimate(430, 46, 0, 26)),
        _result(module, cases[1], "package_label", _estimate(220, 20, 24, 7)),
    ]
    aggregate = module.build_safe_aggregate(
        cases,
        {
            "baseline": {"status": "ran", "case_results": [module._safe_case_result(row) for row in baseline]},
            "package_label": {"status": "ran", "case_results": [module._safe_case_result(row) for row in package_candidate]},
        },
    )

    assert aggregate["decision"]["selected_next_fix"] == "none"


def test_safe_aggregate_rejects_route_case_set_drift():
    module = _load_module()
    cases = module.load_fit174_cases(_artifact(module))
    row = _result(module, cases[0], "baseline", _estimate(430, 46, 0, 26))

    try:
        module.build_safe_aggregate(
            cases,
            {"baseline": {"status": "ran", "case_results": [module._safe_case_result(row)]}},
        )
    except ValueError as exc:
        assert "did not preserve the FIT-174 case order" in str(exc)
    else:
        raise AssertionError("case-set drift should fail")
