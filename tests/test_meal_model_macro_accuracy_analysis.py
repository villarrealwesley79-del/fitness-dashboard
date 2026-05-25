from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "support" / "meal_model_macro_accuracy_analysis.py"
    spec = importlib.util.spec_from_file_location("meal_model_macro_accuracy_analysis", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _case(case_id, category, label, calories, protein, carbs, fat):
    return {
        "case_id": case_id,
        "category": category,
        "label": label,
        "schema_valid": True,
        "macro_mape_percent": round(
            sum(
                macro["absolute_percentage_error"]
                for macro in (calories, protein, carbs, fat)
            )
            / 4,
            2,
        ),
        "per_macro": {
            "calories": calories,
            "protein_g": protein,
            "carbs_g": carbs,
            "fat_g": fat,
        },
    }


def _macro(gold, predicted):
    absolute_error = abs(predicted - gold)
    return {
        "gold": gold,
        "predicted": predicted,
        "absolute_error": absolute_error,
        "absolute_percentage_error": round(absolute_error / max(abs(gold), 1) * 100, 2),
    }


def test_analysis_selects_metric_adjustment_and_reduces_low_carb_outlier():
    module = _load_module()
    payload = {
        "benchmark_id": "safe-fit164",
        "safe_case_results": {
            module.DEFAULT_MODEL: [
                _case(
                    "single-001",
                    "single_item_plate",
                    "sirloin steak",
                    _macro(430, 450),
                    _macro(46, 38),
                    _macro(0, 12),
                    _macro(26, 30),
                ),
                _case(
                    "packaged-001",
                    "packaged_label",
                    "protein bar",
                    _macro(220, 200),
                    _macro(20, 20),
                    _macro(24, 15),
                    _macro(7, 8),
                ),
            ]
        },
    }

    analysis = module.analyze_artifact(payload)

    assert analysis["decision"]["selected_next_fix"] == "metric_adjustment"
    replay = analysis["safe_replay"]
    assert replay["case_count"] == 2
    assert replay["schema_valid_count"] == 2
    assert replay["adjusted_macro_mape_percent"] < replay["original_macro_mape_percent"]
    assert replay["macro_summary"]["carbs_g"]["adjusted_mean_absolute_percentage_error"] < (
        replay["macro_summary"]["carbs_g"]["original_mean_absolute_percentage_error"]
    )
    assert replay["top_low_macro_outliers"][0]["case_id"] == "single-001"
    assert replay["top_low_macro_outliers"][0]["adjusted_absolute_percentage_error"] == 120.0
    assert analysis["privacy"]["uses_safe_case_results_only"] is True


def test_main_writes_safe_analysis_json(tmp_path, monkeypatch):
    module = _load_module()
    artifact = tmp_path / "artifact.json"
    output = tmp_path / "analysis.json"
    artifact.write_text(
        json.dumps({
            "benchmark_id": "safe-fit164",
            "safe_case_results": {
                module.DEFAULT_MODEL: [
                    _case(
                        "case-001",
                        "single_item_plate",
                        "grilled fish",
                        _macro(260, 320),
                        _macro(35, 35),
                        _macro(0, 18),
                        _macro(10, 16),
                    )
                ]
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "meal_model_macro_accuracy_analysis.py",
            "--artifact",
            str(artifact),
            "--output",
            str(output),
        ],
    )

    assert module.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source_benchmark_id"] == "safe-fit164"
    assert payload["privacy"]["raw_images_included"] is False
