# FIT-202 Public Food Smoke And Accuracy Report

Source issue: FIT-202.

## Public Smoke Suite

The CI-safe public smoke suite lives in `support/public_food_smoke_suite.py` and is covered by `tests/test_public_food_smoke_suite.py`.

It uses recorded public-source metadata only:

- Barcode/package cases with knowable nutrition: Nutella `3017620422003`, Coca-Cola `5449000000996`, Coke Zero `5449000131805`, Prince biscuits `7622210449283`, and unknown `0000000000000`.
- Public photo metadata cases: scrambled eggs, mixed NCI food display, cooked white rice, olive oil, grilled chicken, and fried chicken.

Smoke rules:

- Live network: no.
- Raw images committed: no.
- Barcode/package cases: strict source and nutrition assertions.
- Generic public photo cases: identity, uncertainty, confidence cap, and pending-review routing only.
- Generic public photo cases do not carry exact calorie keys.

## Served Vision Model Pin

The served vision model is pinned in `local_vision_adapter.py`:

```text
qwen3-vl-30b-a3b-instruct@q4_k_xl
```

Blank `VISION_LM_STUDIO_MODEL` / `LM_STUDIO_VISION_MODEL` now resolves to that same model. This removes the previous silent downgrade to `qwen2.5-vl-7b-instruct` when the environment was unset.

## Accuracy Reconciliation

Source artifact:

```text
docs/meal-model-benchmark-qwen3vl-30b-vs-32b-2026-05-24.json
```

Analysis command:

```bash
python3 support/meal_model_macro_accuracy_analysis.py
```

Served route analyzed:

```text
qwen3-vl-30b-a3b-instruct@q4_k_xl-scale-cue
```

The two headline numbers measure different things:

- Raw macro MAPE: `35.82%`.
- Floor-adjusted macro MAPE: `19.92%`.

The `19.92%` figure is not a model improvement. It is the same 50-case safe replay scored with a denominator floor: 10 g for gram macros and 50 kcal for calories. It reduces pathological percentage inflation when the gold macro is near zero.

| Metric | Value |
|---|---:|
| Safe replay cases | 50 |
| Schema-valid cases | 50 |
| Raw macro MAPE | 35.82% |
| Floor-adjusted macro MAPE | 19.92% |

## Macro Breakdown

| Macro | Raw mean absolute percentage error | Floor-adjusted mean absolute percentage error |
|---|---:|---:|
| Calories | 17.98% | 17.98% |
| Protein | 20.45% | 19.12% |
| Carbs | 82.56% | 26.03% |
| Fat | 22.28% | 16.55% |

The largest reconciliation is carbohydrates. That is denominator math, not a new model result.

## Category Breakdown

| Category | Cases | Raw macro MAPE | Floor-adjusted macro MAPE |
|---|---:|---:|---:|
| Multi-item plate | 20 | 16.74% | 16.71% |
| Packaged label | 5 | 35.55% | 21.46% |
| Restaurant item | 5 | 17.19% | 17.19% |
| Single-item plate | 20 | 59.62% | 23.43% |

## Gate Readout

The existing gate remains appropriate:

- Pure vision confidence cap: `0.65`.
- Auto-log floor: `0.75`.

The measured safe replay still has enough macro error that photo estimates should remain pending-review unless a stronger verified source, such as barcode/package data, supplies the nutrition.
