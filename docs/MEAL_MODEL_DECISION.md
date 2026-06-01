# Local Meal Model Decision

Linear: FIT-58, FIT-164, FIT-175

## Verified Environment

Verified on this Mac during the FIT-58 pass:

- Mac mini, Apple M4 Pro.
- 12 CPU cores: 8 performance, 4 efficiency.
- 48 GB memory.
- LM Studio at `http://127.0.0.1:1234/v1/models` was reachable.
- Loaded LM Studio models: `google/gemma-4-31b`, `qwen/qwen3.6-35b-a3b`, `text-embedding-nomic-embed-text-v1.5`.

User-reported but not directly reachable from this repo run:

- ASUS GX10 available for heavier local model work.

Later FIT-164 evidence came from the ASUS GX10 vision route rather than this
Mac mini FIT-58 pass. The committed summary artifact is
`docs/meal-model-benchmark-qwen3vl-30b-vs-32b-2026-05-24.json`; raw photos and
the private image map remain intentionally excluded from Git.

## Benchmark Harness

Use:

```bash
python3 support/meal_model_benchmark.py --probe-only
python3 support/meal_model_benchmark.py --list-cases
python3 support/meal_model_benchmark.py --run-model qwen/qwen3.6-35b-a3b --case-set text
python3 support/meal_model_benchmark.py --run-model <local-vlm-model> --case-set vision --image-map private-image-map.json
```

The harness defines the shared benchmark set:

- 20 text-only meals/snacks.
- 20 common plate-photo scenarios.
- 10 packaged-food photo scenarios.
- 10 ambiguous photo cases.

Photo cases are scenario metadata only. Do not commit private food photos to the repo; map local private image files to the scenario IDs when running a vision model benchmark.

`--run-model` sends every selected case through the same JSON-only prompt and records schema validity, item-hint matching, calorie-band checks, macro energy consistency, confidence calibration, latency, and the returned estimate fields needed for scoring. Vision runs require private local images through `--image-map`; missing image mappings are recorded as invalid benchmark results instead of being treated as text-only photo proof. Mixed text/vision runs enforce the text and vision latency targets separately.

## Decision

Current FIT-164 vision route:

- Keep `qwen3-vl-30b-a3b-instruct@q4_k_xl` as the served vision model.
- Retain the FIT-164 scale-cue prompt for that 30B-A3B model.
- Configure the served environment with `VISION_LM_STUDIO_MODEL` or
  `LM_STUDIO_VISION_MODEL` set to `qwen3-vl-30b-a3b-instruct@q4_k_xl`.
- If the deployed host needs a low-memory candidate, set
  `VISION_LM_STUDIO_LOW_MEMORY_MODEL` explicitly to a host-verified smaller VLM.
  The adapter does not default this to another 30B quant because that is not a
  reliable safety net on contended hosts.
- FIT-202 pins the adapter's unset-env fallback to the same
  `qwen3-vl-30b-a3b-instruct@q4_k_xl` route so a blank environment no longer
  silently downgrades vision estimates to the old 7B model.
- Do not promote `qwen3-vl-32b-instruct` because it still exceeded the 25%
  macro-MAPE threshold and averaged 8.44x slower latency than the 30B baseline.
- Treat FIT-164 as a promotion of the served local VLM route, not as a claim
  that food-photo macro accuracy is finished. The same artifact records 35.82%
  aggregate macro MAPE after the scale-cue rerun, so follow-up accuracy work
  should focus on portion and package-label outliers.

Historical FIT-58 text and local-model evidence:

Benchmark evidence:

- This PR commits the repeatable benchmark harness, case set, local hardware probe, and routing criteria.
- The Mac mini probe verified LM Studio reachability and loaded models.
- Local text candidate benchmark outputs are committed in `docs/meal-model-benchmark-qwen-text-2026-05-19.json` and `docs/meal-model-benchmark-gemma-text-2026-05-19.json`.
- `qwen/qwen3.6-35b-a3b` ran all 20 text cases, produced 20 valid strict JSON estimates, passed 14/20 quality checks, and averaged 23560.8 ms.
- `google/gemma-4-31b` attempted all 20 text cases, timed out on 2 cases, produced 18 valid strict JSON estimates, passed 10/20 quality checks after raw-trace screening, and averaged 15822.8 ms across completed model calls.
- The local vision evidence is committed in `docs/meal-model-benchmark-vision-unavailable-2026-05-19.json`: LM Studio had no loaded VLM candidate and the repo has no private image map, so 40/40 image-capable cases were recorded as not runnable instead of being treated as photo-model proof.
- At FIT-58 time, no local model was promoted as the final production winner.
  FIT-164 later selected the Qwen3-VL 30B-A3B served vision route based on the
  dedicated ASUS GX10 comparison artifact named above.

Primary text route:

- Do not promote `qwen/qwen3.6-35b-a3b` for auto-log or primary text meal estimation yet because it missed the quality gate and is too slow for quick logging.
- Do not promote `google/gemma-4-31b` either because it timed out on 2/20 cases, missed the quality gate, and exceeded the text latency target.
- Keep deterministic parser fallback enabled. Fallback estimates must stay below the auto-log threshold and route to pending review unless explicitly accepted.

Primary vision route:

- Use `qwen3-vl-30b-a3b-instruct@q4_k_xl` with the FIT-164 scale-cue prompt
  for the served local vision route when the deployment sets the vision model
  environment variable to that model.
- Do not switch the served route to `qwen3-vl-32b-instruct` without new
  evidence that clears both the macro-accuracy threshold and the route latency
  tradeoff. FIT-164's 32B comparison improved aggregate macro MAPE to 25.83%
  but did not clear the 25% threshold and averaged 23196.1 ms.
- Do not rely on text-only models to infer nutrition from photos.

Fallback route:

- Cloud/API fallback stays disabled by default.
- If the local VLM is missing, unreachable, malformed, or too slow, image estimates should return a structured manual/pending-review fallback instead of failing the meal flow.

## Thresholds

Auto-log:

- `confidence >= 0.75`.
- `ambiguous == false`.
- Calories, macros, sodium, and fiber pass plausible-range validation.
- No raw traces, prompt echoes, or image references in the response.

Pending review:

- `confidence < 0.75`.
- Ambiguous portions, shared food, restaurant packaging, partial plates, hidden sauces, or uncertain item identity.
- Missing calories or impossible nutrition values.
- Any deterministic fallback estimate.

Local model acceptance:

- Text parser: valid schema and quality checks on all 20 text cases, no raw trace leakage, and sensible confidence calibration on ambiguous cases.
- Vision parser: valid schema and quality checks on all 40 photo/packaged cases, low confidence on ambiguous photo cases, no raw image echo, and no silent auto-log for uncertain photos.
- Latency target for text: average <= 10000 ms for the 20-case text set.
- Latency target for vision: average <= 20000 ms for the 40-case image set; otherwise keep the result pending and let the user move on.

## Served Route Target for FIT-5

```json
{
  "text_primary": "deterministic parser fallback, pending_review",
  "text_candidate_not_promoted": "qwen/qwen3.6-35b-a3b: 20/20 schema-valid, 14/20 quality, 23560.8 ms average",
  "text_candidate_not_promoted_2": "google/gemma-4-31b: 18/20 schema-valid, 10/20 quality, 15822.8 ms average across completed calls",
  "text_fallback": "deterministic parser fallback, pending_review",
  "vision_served_model_env": "VISION_LM_STUDIO_MODEL or LM_STUDIO_VISION_MODEL = qwen3-vl-30b-a3b-instruct@q4_k_xl",
  "vision_adapter_unset_env_fallback": "qwen3-vl-30b-a3b-instruct@q4_k_xl",
  "vision_served_prompt": "FIT-164 scale-cue prompt",
  "vision_decision_artifact": "docs/meal-model-benchmark-qwen3vl-30b-vs-32b-2026-05-24.json",
  "vision_not_promoted": "qwen3-vl-32b-instruct: 50/50 schema-valid, 25.83% macro MAPE, 23196.1 ms average, 8.44x slower than 30B baseline",
  "cloud_fallback": "disabled_by_default"
}
```

## Follow-up Proof Before Further Vision Promotion

- Find or configure a faster text candidate that returns valid strict JSON estimates and rerun the 20 text cases.
- Keep the Qwen3-VL 30B-A3B scale-cue route as the served local VLM until a
  follow-up benchmark clears both the macro-accuracy and latency gates.
- Run image-capable cases with private local images through the same harness.
- Record schema validity, confidence calibration, quality subchecks, and latency.
- Keep uncertain image estimates pending-review/fallback instead of silently
  auto-logging ambiguous photo results.
