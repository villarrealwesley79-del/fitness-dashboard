# FIT-176 Private-Image Qwen3-VL Experiment Plan

Linear issue: FIT-176

Stacked dependency: FIT-174 / PR #139

## Goal

Run a local-only private-image experiment pass that can prove or reject
Qwen3-VL macro-accuracy fixes after the FIT-174 safe outlier analysis.

This issue is research-only. Production prediction-path changes stay out of
scope until the aggregate evidence supports a separate Linear issue, branch,
and PR.

## Required Local Inputs

The experiment must not start until these inputs are available on the local
machine:

- A private image map JSON keyed by FIT-174 case IDs.
- Private image files referenced by that map.
- Gold macro labels for the same case IDs, either already encoded in the
  FIT-174 safe artifact or provided by a local-only private label artifact.
- A reachable local OpenAI-compatible vision endpoint.

The image map, photos, private labels, raw model outputs, and per-image traces
must remain uncommitted.

## Candidate Routes

Run the same case set against these options:

- Baseline: current Qwen3-VL route and current prompt.
- Prompt iteration: low/zero-carb single-item plates beyond the current
  scale-cue baseline.
- Package-label extraction: packaged-food cases only.
- Small reference lookup: stable branded/common foods only, if it can preserve
  privacy and schema guarantees.

Reject any option that requires raw photo persistence, prompt/output trace
publication, schema relaxation, or production-path changes inside FIT-176.

## Harness Command

The FIT-176 research harness is:

```bash
python3 support/meal_model_private_image_experiment.py \
  --fit174-artifact docs/meal-model-benchmark-qwen3vl-30b-vs-32b-2026-05-24.json \
  --image-map /private/local/image-map.json \
  --private-output-dir /tmp/fit176-private-runs/<run-id> \
  --safe-output docs/meal-model-private-image-experiment-<date>.json
```

`--private-output-dir` must be outside the repository. It stores raw per-case
model responses and image paths for local inspection only. The committed
`--safe-output` artifact is rejected if it contains private paths, image
filenames, raw prompts, raw model text, base64 image payloads, runtime paths, or
reference-lookup keys.

By default, the harness loads FIT-174 cases from the committed scale-cue artifact
key while sending private-image requests to the same LM Studio model and endpoint
configured for the served app adapter. Override `--fit174-model-key`, `--model`,
or `--lm-studio-url` only when intentionally comparing against a different
artifact key, model, or endpoint.

## Public Benchmark Non-Goals

Do not run the current public benchmark command as acceptance evidence for this
experiment.
`support/meal_model_benchmark.py --case-set food_photo` targets the public
`photo-*`, `pkg-*`, and `amb-*` benchmark cases, not the FIT-174 private case
IDs (`single-*`, `multi-*`, `packaged-*`, and `restaurant-*`).

Do not use `support/meal_model_macro_accuracy_analysis.py` as the FIT-176
comparison step. That script replays a committed FIT-174 safe artifact for one
model; it does not consume private baseline/candidate rerun outputs or compare
candidate routes.

The committed harness satisfies the contract below, but it still requires the
local-only private image map and readable private photos before any experiment
output can be treated as acceptance evidence.

## Required Runner Contract

The private rerun harness must:

- Load the FIT-174 private case IDs from the safe artifact without exposing
  private image paths.
- Accept a local-only private image map keyed to those FIT-174 case IDs.
- Run the baseline route and each candidate route against the same case set.
- Preserve the current served LM Studio prompt, strict JSON schema validation,
  and adapter-enforced retries.
- Keep raw model responses, image paths, image maps, and per-image traces in
  untracked private output only.
- Produce a safe aggregate artifact that compares baseline versus candidate
  routes by category and macro.

The committed aggregate JSON must not include image paths, raw photos, private
image maps, raw model text, base64 data, or per-image traces.

## Aggregate Metrics

Report before/after metrics for the FIT-174 categories:

- `single_item_plate`
- `packaged_label`
- `restaurant_item`
- `multi_item_plate`

Report by macro:

- calories
- protein_g
- carbs_g
- fat_g

At minimum, include:

- case count
- schema-valid count
- schema retry count summary
- mean absolute error
- original macro MAPE
- low-macro-floor MAPE
- top safe redacted outlier rows by case ID, category, macro, gold,
  predicted, and absolute error

## Privacy And Schema Checks

Before committing any artifact, verify:

- No image file paths are present.
- No `data:image` or base64 payloads are present.
- No `.jpg`, `.jpeg`, `.png`, `.heic`, or `.webp` filenames are present.
- No `.env`, runtime database, cache path, or private image-map path is
  present.
- The harness preserved current served strict JSON schema validation.
- Adapter-enforced retries remain intact.

## Decision Rules

Choose the next fix category only if the aggregate evidence shows a clear
improvement over baseline without privacy or schema regression:

- Prefer prompt iteration if single-item low/zero-carb cases improve beyond the
  already-shipped scale-cue baseline without hurting multi-item plates.
- Prefer package-label extraction if packaged-label errors materially improve
  and schema retry/error rates do not increase.
- Prefer reference lookup only if branded/common-food cases improve and the
  lookup can run without leaking private image context.
- Reject production changes if the aggregate gains are mixed, category-specific
  regressions appear, or the evidence depends on private traces.

## Current Blocker

As of 2026-05-25, the private image map and private photo set were not
discoverable from this machine by repo/worktree search, common user folders, or
runtime environment variables. Vision models are reachable, so model
availability is not the blocker.

The experiment is therefore blocked on private dataset access only. The
FIT-174-compatible private rerun/comparison harness is ready, tested, and
research-only.

Needed to continue:

- A local JSON image map keyed to the FIT-174 case IDs, plus readable private
  photos; or
- Explicit authorization to use a different local private image set with gold
  labels for the same aggregate categories.
