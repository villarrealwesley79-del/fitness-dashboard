from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import branded_food_lookup


def _load_smoke_module():
    path = Path(__file__).resolve().parents[1] / "support" / "public_food_smoke_suite.py"
    spec = importlib.util.spec_from_file_location("public_food_smoke_suite", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_public_food_smoke_suite_is_ci_safe_and_fixture_only():
    module = _load_smoke_module()

    payload = module.run_public_food_smoke_suite()

    assert payload["suite_id"] == "fit-202-public-food-smoke"
    assert payload["ci_safe"] is True
    assert payload["live_network"] is False
    assert payload["raw_images_committed"] is False
    assert payload["case_count"] == 11
    assert payload["passed_count"] == payload["case_count"]


def test_barcode_cases_have_strict_nutrition_keys_except_unknown_manual_pending():
    module = _load_smoke_module()

    known_cases = [case for case in module.BARCODE_CASES if case.strict_nutrition_key]
    unknown_cases = [case for case in module.BARCODE_CASES if not case.strict_nutrition_key]

    assert {case.barcode for case in known_cases} == {
        "3017620422003",
        "5449000000996",
        "5449000131805",
        "7622210449283",
    }
    assert len(unknown_cases) == 1
    assert unknown_cases[0].barcode == "0000000000000"
    assert unknown_cases[0].expected_status == "manual_pending"
    expected_calories = {
        "3017620422003": 539,
        "5449000000996": 139,
        "5449000131805": 0,
        "7622210449283": 467,
    }
    for case in known_cases:
        result = module.score_barcode_case(case, module.BARCODE_OBSERVED_FIXTURES[case.barcode])
        assert result["passed"] is True
        assert result["expected_source"] == "open_food_facts_barcode"
        assert result["calories"] == expected_calories[case.barcode]
        assert all(result["checks"].values())


def _off_product(code, name, brands, nutriments, **overrides):
    product = {
        "code": code,
        "product_name": name,
        "brands": brands,
        "url": f"https://world.openfoodfacts.org/product/{code}",
        "countries_tags": ["en:france", "en:united-states"],
        "data_quality_tags": ["en:nutrition-data-complete"],
        "nutriments": nutriments,
    }
    product.update(overrides)
    return product


def test_barcode_smoke_scores_real_lookup_outputs_from_recorded_off_fixtures(monkeypatch):
    module = _load_smoke_module()
    products = {
        "3017620422003": _off_product(
            "3017620422003",
            "Nutella",
            "Ferrero",
            {
                "energy-kcal_100g": 539,
                "proteins_100g": 6.3,
                "carbohydrates_100g": 57.5,
                "fat_100g": 30.9,
            },
        ),
        "5449000000996": _off_product(
            "5449000000996",
            "Coca-Cola Original Taste",
            "Coca-Cola",
            {
                "energy-kcal_100g": 42.1,
                "proteins_100g": 0,
                "carbohydrates_100g": 10.6,
                "fat_100g": 0,
                "energy-kcal_serving": 139,
                "proteins_serving": 0,
                "carbohydrates_serving": 35,
                "fat_serving": 0,
            },
            serving_size="330 ml",
        ),
        "5449000131805": _off_product(
            "5449000131805",
            "Coke Zero",
            "Coca-Cola",
            {
                "energy-kcal_100g": 0,
                "proteins_100g": 0,
                "carbohydrates_100g": 0,
                "fat_100g": 0,
                "energy-kcal_serving": 0,
                "proteins_serving": 0,
                "carbohydrates_serving": 0,
                "fat_serving": 0,
            },
            serving_size="330 ml",
        ),
        "7622210449283": _off_product(
            "7622210449283",
            "Prince biscuits",
            "LU",
            {
                "energy-kcal_100g": 467,
                "proteins_100g": 6.3,
                "carbohydrates_100g": 69,
                "fat_100g": 17,
            },
        ),
    }
    monkeypatch.setattr(branded_food_lookup.data_store, "get_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_barcode_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "search_item_by_upc", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        branded_food_lookup.open_food_facts_client,
        "get_product_by_barcode",
        lambda barcode, **_kw: products.get(barcode),
    )

    observations = {
        case.barcode: branded_food_lookup.lookup_barcode(case.barcode)
        for case in module.BARCODE_CASES
    }

    payload = module.run_public_food_smoke_suite(barcode_observations=observations)

    assert payload["passed_count"] == payload["case_count"]
    for result in payload["barcode_results"]:
        assert result["passed"] is True
        if result["strict_nutrition_key"]:
            assert result["observed_source"] == "open_food_facts_barcode"
            assert all(result["checks"].values())
        else:
            assert result["checks"] == {"manual_pending": True}


def test_public_photo_cases_never_use_exact_calorie_keys():
    module = _load_smoke_module()

    for case in module.PUBLIC_PHOTO_CASES:
        assert case.exact_calories is None
        assert case.raw_image_committed is False
        result = module.score_public_photo_case(case, module.PHOTO_OBSERVED_FIXTURES[case.case_id])
        assert result["passed"] is True
        assert result["checks"]["no_exact_calorie_key"] is True
        assert result["checks"]["route"] is True
        assert result["checks"]["confidence"] is True


def test_public_photo_score_fails_observed_exact_calories_or_raw_images():
    module = _load_smoke_module()
    case = module.PUBLIC_PHOTO_CASES[0]
    observed = dict(module.PHOTO_OBSERVED_FIXTURES[case.case_id])

    calorie_result = module.score_public_photo_case(case, {**observed, "calories": 250})
    raw_image_result = module.score_public_photo_case(case, {**observed, "raw_image_committed": True})

    assert calorie_result["passed"] is False
    assert calorie_result["checks"]["no_exact_calorie_key"] is False
    assert raw_image_result["passed"] is False
    assert raw_image_result["checks"]["no_raw_image"] is False


def test_empty_barcode_observation_injection_fails_strict_cases():
    module = _load_smoke_module()

    payload = module.run_public_food_smoke_suite(barcode_observations={})

    known_results = [
        result for result in payload["barcode_results"]
        if result["strict_nutrition_key"]
    ]
    assert known_results
    assert all(result["passed"] is False for result in known_results)


def test_public_food_smoke_suite_main_writes_json(tmp_path, monkeypatch):
    module = _load_smoke_module()
    output = tmp_path / "public-smoke.json"
    monkeypatch.setattr(sys, "argv", ["public_food_smoke_suite.py", "--output", str(output)])

    assert module.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["case_count"] == 11
    assert payload["passed_count"] == 11


def test_blank_vision_model_env_defaults_to_served_qwen3_route(monkeypatch):
    monkeypatch.delenv("VISION_LM_STUDIO_MODEL", raising=False)
    monkeypatch.delenv("LM_STUDIO_VISION_MODEL", raising=False)
    import local_vision_adapter

    reloaded = importlib.reload(local_vision_adapter)

    assert reloaded.SERVED_VISION_MODEL == "qwen3-vl-30b-a3b-instruct@q4_k_xl"
    assert reloaded.LM_STUDIO_MODEL == reloaded.SERVED_VISION_MODEL


def test_fit202_report_reconciles_raw_and_floor_adjusted_metrics():
    report = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "FIT202_PUBLIC_FOOD_SMOKE_AND_ACCURACY_REPORT.md"
    ).read_text(encoding="utf-8")

    assert "Raw macro MAPE | 35.82%" in report
    assert "Floor-adjusted macro MAPE | 19.92%" in report
    assert "not a model improvement" in report
    assert "Pure vision confidence cap: `0.65`" in report
    assert "Auto-log floor: `0.75`" in report
