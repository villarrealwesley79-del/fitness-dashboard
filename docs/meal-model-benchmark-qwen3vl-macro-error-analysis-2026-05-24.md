# FIT-174 Qwen3-VL Macro Error Analysis

Source artifact: `docs/meal-model-benchmark-qwen3vl-30b-vs-32b-2026-05-24.json`

Generated artifact: `docs/meal-model-benchmark-qwen3vl-macro-error-analysis-2026-05-24.json`

Command:

```bash
python3 support/meal_model_macro_accuracy_analysis.py --output docs/meal-model-benchmark-qwen3vl-macro-error-analysis-2026-05-24.json
```

## Decision

The next fix should be a metric adjustment before another prompt, parser, calibration, or reference-lookup change.

The FIT-164 safe results show that the largest remaining macro-MAPE failures are dominated by denominator effects for zero or very-low gram macros, especially carbohydrates on single-item protein foods. Prompt changes and package-label parsing may still help later, but they cannot be honestly proven from the committed safe artifact alone because the private image set is not available in the repo.

The selected replay metric is `low_macro_floor_mape`:

- Gram macros use denominator `max(abs(gold), 10)`.
- Calories use denominator `max(abs(gold), 50)`.
- Absolute errors remain visible in the per-case outlier tables.
- The strict schema, model output, raw-image exclusion, and private-image-map exclusion are unchanged.

## Full Safe Replay

The replay uses the post-scale 30B-A3B result set because FIT-164 kept that route as the served model candidate.

| Metric | FIT-164 original | FIT-174 adjusted replay |
|---|---:|---:|
| Case count | 50 | 50 |
| Schema-valid count | 50 | 50 |
| Macro MAPE | 35.82% | 19.92% |

## Error Modes

| Category | Cases | Original macro MAPE | Adjusted macro MAPE |
|---|---:|---:|---:|
| Single-item plate | 20 | 59.62% | 23.43% |
| Packaged label | 5 | 35.55% | 21.46% |
| Restaurant item | 5 | 17.19% | 17.19% |
| Multi-item plate | 20 | 16.74% | 16.71% |

| Macro | Original mean APE | Adjusted mean APE |
|---|---:|---:|
| Calories | 17.98% | 17.98% |
| Protein | 20.45% | 19.12% |
| Carbs | 82.56% | 26.03% |
| Fat | 22.28% | 16.55% |

The recurring pattern is not broad schema failure or model latency. It is low-denominator percentage inflation, with single-item zero-carb foods producing the top two outliers:

- `single-015`, grilled fish filet: gold carbs 0g, predicted 18g, original carb APE 1800.00%, adjusted carb APE 180.00%.
- `single-007`, sirloin steak: gold carbs 0g, predicted 12g, original carb APE 1200.00%, adjusted carb APE 120.00%.
- `packaged-003`, greek yogurt cup: gold fat 2g, predicted 7g, original fat APE 250.00%, adjusted fat APE 50.00%.

## Rejected Next Fixes

Prompt changes: not selected for this issue because a real before/after model rerun would require the private image set. A prompt-only patch would look plausible but would not prove accuracy.

Package-label parsing rules: not selected as the first fix because packaged labels are elevated but are not the largest aggregate driver after the scale-cue prompt. They remain a good follow-up if private image reruns show label-reading failures.

Per-category calibration: not selected because category-level correction from gold-only safe results risks fitting the evaluation set without proving real-image behavior.

Small reference nutrition lookup: not selected because the largest remaining outliers are generic single-item protein foods, not branded or restaurant items.

## Privacy And Schema

This analysis uses only committed safe case results. It does not add raw photos, local image maps, caches, logs, or runtime artifacts. The replay does not change the strict meal-estimate schema or model output parsing path.
