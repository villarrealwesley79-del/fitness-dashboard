# Local Meal Model Decision

Linear: FIT-58

## Verified Environment

Verified on this Mac during the FIT-58 pass:

- Mac mini, Apple M4 Pro.
- 12 CPU cores: 8 performance, 4 efficiency.
- 48 GB memory.
- LM Studio at `http://127.0.0.1:1234/v1/models` was reachable.
- Loaded LM Studio models: `google/gemma-4-31b`, `qwen/qwen3.6-35b-a3b`, `text-embedding-nomic-embed-text-v1.5`.

User-reported but not directly reachable from this repo run:

- ASUS GX10 available for heavier local model work.

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

Benchmark evidence:

- This PR commits the repeatable benchmark harness, case set, local hardware probe, and routing criteria.
- The Mac mini probe verified LM Studio reachability and loaded models.
- Local text candidate benchmark outputs are committed in `docs/meal-model-benchmark-qwen-text-2026-05-19.json` and `docs/meal-model-benchmark-gemma-text-2026-05-19.json`.
- `qwen/qwen3.6-35b-a3b` ran all 20 text cases, produced 20 valid strict JSON estimates, passed 14/20 quality checks, and averaged 23560.8 ms.
- `google/gemma-4-31b` attempted all 20 text cases, timed out on 2 cases, produced 18 valid strict JSON estimates, passed 10/20 quality checks after raw-trace screening, and averaged 15822.8 ms across completed model calls.
- The local vision evidence is committed in `docs/meal-model-benchmark-vision-unavailable-2026-05-19.json`: LM Studio had no loaded VLM candidate and the repo has no private image map, so 40/40 image-capable cases were recorded as not runnable instead of being treated as photo-model proof.
- No local model is promoted as the final production winner until it passes the shared schema, quality, and route-specific latency gates.

Primary text route:

- Do not promote `qwen/qwen3.6-35b-a3b` for auto-log or primary text meal estimation yet because it missed the quality gate and is too slow for quick logging.
- Do not promote `google/gemma-4-31b` either because it timed out on 2/20 cases, missed the quality gate, and exceeded the text latency target.
- Keep deterministic parser fallback enabled. Fallback estimates must stay below the auto-log threshold and route to pending review unless explicitly accepted.

Primary vision route:

- Use a dedicated local VLM candidate; do not rely on the text-only model to infer nutrition from photos.
- First benchmark candidate: Qwen2.5-VL 7B on the ASUS GX10 or LM Studio if available.
- Second benchmark candidate: Qwen2.5-VL 32B only if memory and latency are acceptable.
- Optional comparison candidate: Gemma 3 27B vision if the local runtime exposes it cleanly.
- InternVL-family models can be benchmarked only if LM Studio or the GX10 runtime supports them without custom glue.
- No vision model is promoted by this PR because no VLM candidate was loaded or benchmarkable from this Mac run.

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

## Routing Defaults for FIT-5

```json
{
  "text_primary": "deterministic parser fallback, pending_review",
  "text_candidate_not_promoted": "qwen/qwen3.6-35b-a3b: 20/20 schema-valid, 14/20 quality, 23560.8 ms average",
  "text_candidate_not_promoted_2": "google/gemma-4-31b: 18/20 schema-valid, 10/20 quality, 15822.8 ms average across completed calls",
  "text_fallback": "deterministic parser fallback, pending_review",
  "vision_primary": "manual/pending-review fallback until a local VLM passes the vision benchmark",
  "vision_candidate_pending": "Qwen2.5-VL 7B local VLM, preferably on ASUS GX10 for photo work; not loaded in this Mac run",
  "vision_secondary": "Qwen2.5-VL 32B only after latency/memory proof",
  "cloud_fallback": "disabled_by_default"
}
```

## Follow-up Proof Before Replacing the Photo Stub

- Find or configure a faster text candidate that returns valid strict JSON estimates and rerun the 20 text cases.
- Install or expose a local VLM endpoint, likely on the ASUS GX10 if that is where the vision model fits best.
- Run the 40 image-capable cases with private local images through the same harness.
- Record schema validity, confidence calibration, quality subchecks, and latency.
- Keep the current image path pending-review/fallback behavior until that evidence exists.
