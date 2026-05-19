from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "support" / "meal_model_benchmark.py"
    spec = importlib.util.spec_from_file_location("meal_model_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_benchmark_case_counts_match_fit58_contract():
    module = _load_module()

    assert len(module.TEXT_CASES) == 20
    assert len(module.PHOTO_CASES) == 20
    assert len(module.PACKAGED_CASES) == 10
    assert len(module.AMBIGUOUS_CASES) == 10
    assert len(module.image_capable_cases()) == 40
    assert len(module.all_cases()) == 60


def test_routing_recommendation_lists_loaded_qwen_as_candidate_not_primary():
    module = _load_module()

    decision = module.routing_recommendation(["google/gemma-4-31b", "qwen/qwen3.6-35b-a3b"])

    assert decision["candidate_text_model"] == "qwen/qwen3.6-35b-a3b"
    assert "benchmark passes" in decision["production_text_route"]
    assert "primary_text_model" not in decision
    assert "pending_review" in decision["fallback"]
    assert decision["auto_log_threshold"].startswith("confidence >= 0.75")


def test_model_benchmark_summarizes_schema_validity(monkeypatch):
    module = _load_module()

    def fake_run_model_case(case, *, model, lm_studio_url, image_path=None, timeout=60.0):
        return {
            "case_id": case.case_id,
            "input_type": case.input_type,
            "model": model,
            "latency_ms": 125,
            "schema_valid": case.case_id != "txt-002",
            "schema_errors": [] if case.case_id != "txt-002" else ["missing_calories"],
            "quality": {"passed": case.case_id != "txt-002"},
            "confidence": 0.8,
            "ambiguous": False,
            "expected_item_hint": case.expected_item_hint,
        }

    monkeypatch.setattr(module, "run_model_case", fake_run_model_case)

    summary = module.run_model_benchmark(module.TEXT_CASES[:2], model="local-test-model")

    assert summary["model"] == "local-test-model"
    assert summary["case_count"] == 2
    assert summary["schema_valid_count"] == 1
    assert summary["schema_valid_rate"] == 0.5
    assert summary["quality_pass_count"] == 1
    assert summary["quality_pass_rate"] == 0.5
    assert summary["latency_passed"] is True
    assert summary["latency_limit_ms"] == module.TEXT_LATENCY_PASS_MS
    assert summary["candidate_passed"] is False
    assert summary["latency_ms_avg"] == 125


def test_schema_validation_requires_sodium_and_fiber():
    module = _load_module()

    errors = module._estimate_schema_errors({
        "item_name": "Meal",
        "portion_description": None,
        "meal_type": "lunch",
        "calories": 400,
        "protein_g": 25,
        "carbs_g": 40,
        "fat_g": 15,
        "confidence": 0.8,
        "ambiguous": False,
        "uncertainty_notes": [],
    })

    assert "missing_sodium_mg" in errors
    assert "missing_fiber_g" in errors


def test_schema_validation_matches_app_required_fields_and_numeric_guards():
    module = _load_module()

    errors = module._estimate_schema_errors({
        "item_name": "Meal",
        "portion_description": None,
        "calories": True,
        "protein_g": 25,
        "carbs_g": 40,
        "fat_g": 15,
        "sodium_mg": 999999,
        "fiber_g": 4,
        "confidence": 0.8,
        "ambiguous": False,
        "uncertainty_notes": [],
    })

    assert "missing_meal_type" in errors
    assert "invalid_meal_type" in errors
    assert "invalid_calories" in errors
    assert "invalid_sodium_mg" in errors


def test_chat_payload_uses_lm_studio_supported_json_schema_response_format():
    module = _load_module()

    payload = module._chat_payload(module.TEXT_CASES[0], "local-model")

    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["required"] == list(module.MEAL_ESTIMATE_RESPONSE_SCHEMA["required"])


def test_vision_payload_does_not_leak_expected_food_label(tmp_path):
    module = _load_module()
    image_path = tmp_path / "private-photo.jpg"
    image_path.write_bytes(b"fake-jpeg")

    payload = module._chat_payload(module.PHOTO_CASES[0], "local-model", image_path=str(image_path))

    text_parts = [
        part["text"]
        for part in payload["messages"][0]["content"]
        if part["type"] == "text"
    ]
    joined_text = "\n".join(text_parts).lower()
    assert module.PHOTO_CASES[0].expected_item_hint not in joined_text
    assert module.PHOTO_CASES[0].prompt not in joined_text
    assert "attached image" in joined_text


def test_schema_validation_rejects_malformed_field_types():
    module = _load_module()

    errors = module._estimate_schema_errors({
        "item_name": ["Meal"],
        "portion_description": "",
        "meal_type": "lunch",
        "calories": 400,
        "protein_g": 25,
        "carbs_g": 40,
        "fat_g": 15,
        "sodium_mg": 900,
        "fiber_g": 4,
        "confidence": True,
        "ambiguous": False,
        "uncertainty_notes": "looks okay",
        "source": "",
    })

    assert "invalid_item_name" in errors
    assert "invalid_portion_description" in errors
    assert "invalid_confidence" in errors
    assert "invalid_uncertainty_notes" in errors
    assert "unexpected_source" in errors


def test_schema_validation_rejects_non_finite_numbers():
    module = _load_module()

    errors = module._estimate_schema_errors({
        "item_name": "Meal",
        "portion_description": None,
        "meal_type": "lunch",
        "calories": math.nan,
        "protein_g": 25,
        "carbs_g": 40,
        "fat_g": 15,
        "sodium_mg": 900,
        "fiber_g": 4,
        "confidence": math.inf,
        "ambiguous": False,
        "uncertainty_notes": [],
    })

    assert "invalid_calories" in errors
    assert "invalid_confidence" in errors


def test_schema_validation_rejects_unexpected_fields():
    module = _load_module()

    errors = module._estimate_schema_errors({
        "item_name": "Meal",
        "portion_description": "one plate",
        "meal_type": "lunch",
        "calories": 400,
        "protein_g": 25,
        "carbs_g": 40,
        "fat_g": 15,
        "sodium_mg": 900,
        "fiber_g": 4,
        "confidence": 0.8,
        "ambiguous": False,
        "uncertainty_notes": [],
        "raw_image_reference": "/private/photo.jpg",
    })

    assert "unexpected_raw_image_reference" in errors


def test_quality_score_rejects_json_shaped_nonsense():
    module = _load_module()
    estimate = {
        "item_name": "Plain water",
        "portion_description": "one cup",
        "meal_type": "snack",
        "calories": 4500,
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 0,
        "sodium_mg": 0,
        "fiber_g": 0,
        "confidence": 0.99,
        "ambiguous": False,
        "uncertainty_notes": [],
    }

    score = module._quality_score(module.TEXT_CASES[0], estimate, [])

    assert score["passed"] is False
    assert score["item_hint_match"] is False
    assert score["calories_within_band"] is False
    assert score["nutrition_fields_within_bands"] is False
    assert score["macro_energy_consistent"] is False


def test_quality_score_rejects_bad_macro_and_sodium_bands():
    module = _load_module()
    estimate = {
        "item_name": "Protein shake",
        "portion_description": "one bottle",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 1,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 9000,
        "fiber_g": 2,
        "confidence": 0.8,
        "ambiguous": False,
        "uncertainty_notes": [],
    }

    score = module._quality_score(module.TEXT_CASES[0], estimate, [])

    assert score["nutrition_fields_within_bands"] is False
    assert score["passed"] is False


def test_quality_score_rejects_prompt_and_image_trace_leakage():
    module = _load_module()
    estimate = {
        "item_name": "Burger",
        "portion_description": "Meal input: burger photo from /tmp/private-food.jpg",
        "meal_type": "lunch",
        "calories": 520,
        "protein_g": 28,
        "carbs_g": 42,
        "fat_g": 26,
        "sodium_mg": 1100,
        "fiber_g": 3,
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": ["```json"],
    }

    score = module._quality_score(module.TEXT_CASES[6], estimate, [])

    assert score["raw_trace_free"] is False
    assert score["passed"] is False


def test_model_benchmark_requires_latency_gate(monkeypatch):
    module = _load_module()

    def slow_valid_case(case, *, model, lm_studio_url, image_path=None, timeout=60.0):
        return {
            "case_id": case.case_id,
            "input_type": case.input_type,
            "model": model,
            "has_image": False,
            "ran_model": True,
            "latency_ms": module.TEXT_LATENCY_PASS_MS + 1,
            "schema_valid": True,
            "schema_errors": [],
            "quality": {"passed": True},
            "estimate": {},
            "confidence": 0.8,
            "ambiguous": False,
            "expected_item_hint": case.expected_item_hint,
        }

    monkeypatch.setattr(module, "run_model_case", slow_valid_case)

    summary = module.run_model_benchmark(module.TEXT_CASES[:1], model="slow-model")

    assert summary["quality_pass_count"] == 1
    assert summary["latency_passed"] is False
    assert summary["candidate_passed"] is False


def test_model_benchmark_enforces_text_and_vision_latency_gates_separately(monkeypatch):
    module = _load_module()

    def mixed_case(case, *, model, lm_studio_url, image_path=None, timeout=60.0):
        latency_ms = module.TEXT_LATENCY_PASS_MS + 1 if case.input_type == "text" else 100
        return {
            "case_id": case.case_id,
            "input_type": case.input_type,
            "model": model,
            "has_image": bool(image_path),
            "ran_model": True,
            "latency_ms": latency_ms,
            "schema_valid": True,
            "schema_errors": [],
            "quality": {"passed": True},
            "estimate": {},
            "confidence": 0.8,
            "ambiguous": False,
            "expected_item_hint": case.expected_item_hint,
        }

    monkeypatch.setattr(module, "run_model_case", mixed_case)

    summary = module.run_model_benchmark(
        [module.TEXT_CASES[0], module.PHOTO_CASES[0]],
        model="mixed-model",
        image_map={module.PHOTO_CASES[0].case_id: "/tmp/private-food-photo.jpg"},
    )

    assert summary["latency_limit_ms"] is None
    assert summary["latency_gates"]["text"]["latency_passed"] is False
    assert summary["latency_gates"]["vision"]["latency_passed"] is True
    assert summary["latency_passed"] is False
    assert summary["candidate_passed"] is False


def test_quality_score_rejects_high_confidence_ambiguous_case():
    module = _load_module()
    estimate = {
        "item_name": "Popcorn",
        "portion_description": "large tub",
        "meal_type": "snack",
        "calories": 700,
        "protein_g": 8,
        "carbs_g": 80,
        "fat_g": 38,
        "sodium_mg": 900,
        "fiber_g": 10,
        "confidence": 0.95,
        "ambiguous": True,
        "uncertainty_notes": ["portion unclear"],
    }

    score = module._quality_score(module.TEXT_CASES[3], estimate, [])

    assert score["confidence_calibrated"] is False
    assert score["passed"] is False


def test_quality_score_accepts_reasonable_estimate():
    module = _load_module()
    estimate = {
        "item_name": "Protein shake",
        "portion_description": "one bottle",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.8,
        "ambiguous": False,
        "uncertainty_notes": [],
    }

    score = module._quality_score(module.TEXT_CASES[0], estimate, [])

    assert score["passed"] is True


def test_quality_score_allows_reasonable_vision_estimate_without_case_specific_macro_band():
    module = _load_module()
    estimate = {
        "item_name": "Chicken rice",
        "portion_description": "one plate",
        "meal_type": "lunch",
        "calories": 600,
        "protein_g": 35,
        "carbs_g": 60,
        "fat_g": 18,
        "sodium_mg": 800,
        "fiber_g": 5,
        "confidence": 0.6,
        "ambiguous": True,
        "uncertainty_notes": ["portion unclear"],
    }

    score = module._quality_score(module.PHOTO_CASES[0], estimate, [])

    assert score["nutrition_fields_within_bands"] is True
    assert score["passed"] is True


def test_schema_validation_allows_null_portion_description():
    module = _load_module()

    errors = module._estimate_schema_errors({
        "item_name": "Meal",
        "portion_description": None,
        "meal_type": "lunch",
        "calories": 400,
        "protein_g": 25,
        "carbs_g": 40,
        "fat_g": 15,
        "sodium_mg": 900,
        "fiber_g": 4,
        "confidence": 0.8,
        "ambiguous": False,
        "uncertainty_notes": [],
    })

    assert errors == []


def test_extract_json_object_rejects_prose_wrapped_json():
    module = _load_module()

    assert module._extract_json_object('{"item_name": "Meal"}') == {"item_name": "Meal"}
    assert module._extract_json_object('Here is the JSON: {"item_name": "Meal"}') is None


def test_model_case_records_missing_private_image_instead_of_aborting():
    module = _load_module()

    result = module.run_model_case(
        module.PHOTO_CASES[0],
        model="local-model",
        image_path="/tmp/fitness-dashboard-missing-private-meal-photo.jpg",
    )

    assert result["schema_valid"] is False
    assert any(error.startswith("request_failed:") for error in result["schema_errors"])


def test_vision_benchmark_marks_missing_image_mapping_invalid():
    module = _load_module()

    summary = module.run_model_benchmark(module.PHOTO_CASES[:1], model="local-model", image_map={})

    assert summary["schema_valid_count"] == 0
    result = summary["results"][0]
    assert result["has_image"] is False
    assert result["schema_errors"] == ["missing_image_mapping"]
    assert summary["model_run_count"] == 0
    assert summary["missing_image_count"] == 1


def test_missing_image_rows_do_not_lower_latency_average(monkeypatch):
    module = _load_module()

    def fake_run_model_case(case, *, model, lm_studio_url, image_path=None, timeout=60.0):
        return {
            "case_id": case.case_id,
            "input_type": case.input_type,
            "model": model,
            "has_image": True,
            "ran_model": True,
            "latency_ms": 10000,
            "schema_valid": True,
            "schema_errors": [],
            "quality": {"passed": True},
            "estimate": {},
            "confidence": 0.8,
            "ambiguous": False,
            "expected_item_hint": case.expected_item_hint,
        }

    monkeypatch.setattr(module, "run_model_case", fake_run_model_case)

    summary = module.run_model_benchmark(
        module.PHOTO_CASES[:2],
        model="local-model",
        image_map={module.PHOTO_CASES[0].case_id: "/tmp/private-photo.jpg"},
    )

    assert summary["model_run_count"] == 1
    assert summary["missing_image_count"] == 1
    assert summary["latency_ms_avg"] == 10000


def test_model_case_preserves_estimate_fields(monkeypatch):
    module = _load_module()

    estimate = {
        "item_name": "Burger",
        "portion_description": "one sandwich",
        "meal_type": "lunch",
        "calories": 520,
        "protein_g": 28,
        "carbs_g": 42,
        "fat_g": 26,
        "sodium_mg": 1100,
        "fiber_g": 3,
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": [],
    }
    body = json.dumps({"choices": [{"message": {"content": json.dumps(estimate)}}]}).encode("utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return body

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = module.run_model_case(module.TEXT_CASES[0], model="local-model")

    assert result["schema_valid"] is True
    assert result["estimate"]["item_name"] == "Burger"
    assert result["estimate"]["calories"] == 520
    assert result["estimate"]["source"] == "local_model_benchmark"


def test_model_case_reads_reasoning_content_when_content_is_empty(monkeypatch):
    module = _load_module()

    estimate = {
        "item_name": "Protein shake",
        "portion_description": "one bottle",
        "meal_type": "snack",
        "calories": 210,
        "protein_g": 30,
        "carbs_g": 14,
        "fat_g": 4,
        "sodium_mg": 180,
        "fiber_g": 2,
        "confidence": 0.8,
        "ambiguous": False,
        "uncertainty_notes": [],
    }
    body = json.dumps({"choices": [{"message": {"content": "", "reasoning_content": json.dumps(estimate)}}]}).encode("utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return body

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = module.run_model_case(module.TEXT_CASES[0], model="local-model")

    assert result["schema_valid"] is True
    assert result["quality"]["passed"] is True


def test_probe_only_does_not_run_model_benchmark(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(sys, "argv", ["meal_model_benchmark.py", "--probe-only", "--run-model", "local-model"])
    monkeypatch.setattr(module, "probe_lm_studio", lambda _url: {"reachable": True, "models": []})

    def fail_run_model_benchmark(*_args, **_kwargs):
        raise AssertionError("probe-only should not run benchmark cases")

    monkeypatch.setattr(module, "run_model_benchmark", fail_run_model_benchmark)

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert "benchmark" not in payload
    assert payload["lm_studio"]["reachable"] is True
