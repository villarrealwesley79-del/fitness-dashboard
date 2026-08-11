# FIT-396 Weightlifting Golden Cases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the production-schema weightlifting benchmark to exactly 40 cases (20 adjust intent, 15 swap resolution, 5 post-workout analysis) and commit a raw-free GX10 baseline covering the unchanged food suites plus all three weightlifting surfaces.

**Architecture:** Keep `support/meal_model_benchmark.py` as the only production benchmark file changed. Reuse `_adjust_case`, `_swap_case`, and `_analysis_case`; do not change prompts, schemas, scoring, request execution, or the deterministic coach. Extend the existing contract and mock-dispatch tests so the new fixture counts, production-schema identity, unique case IDs, complete expected values, and raw-free public report are locked before adding cases.

**Tech Stack:** Python 3, pytest, the existing LM Studio OpenAI-compatible benchmark runner, GX10 endpoint `http://100.99.46.2:1234`, text model `qwen/qwen3.6-35b-a3b`, vision model `qwen3-vl-30b-a3b-instruct`.

## Global Constraints

- Scope is FIT-396 only; no prompt text changes and no scoring or runner changes beyond case registration.
- Final fixture counts are exactly 20 adjust-intent, 15 swap-resolution, and 5 post-workout analysis cases: 40 workout cases total and 107 all-suite cases total.
- Every structured payload must use the production schemas already imported by identity from `lm_studio_adapter.py`.
- Reports may contain per-case verdicts, scores, failure reasons, model names, aggregate counts, and latency; they must not contain raw prompts, raw completions, private image paths, secrets, or source payloads.
- Do not modify the deterministic coach core.

---

### Task 1: Lock the FIT-396 fixture and mock-run contract in RED

**Files:**
- Modify: `tests/test_meal_model_benchmark.py`
- Test: `tests/test_meal_model_benchmark.py`

**Interfaces:**
- Consumes: `ADJUST_INTENT_CASES`, `SWAP_RESOLUTION_CASES`, `POST_WORKOUT_ANALYSIS_CASES`, `WORKOUT_CASES`, `all_cases()`, `_chat_payload()`, and `main()` from `support/meal_model_benchmark.py`.
- Produces: exact fixture-count and mock-dispatch assertions that fail against the current 1/1/1 case set.

- [ ] **Step 1: Update the existing count contract to the FIT-396 totals**

Change `test_benchmark_case_counts_match_fit58_contract` so it asserts:

```python
assert len(module.WORKOUT_CASES) == 40
assert len(module.ADJUST_INTENT_CASES) == 20
assert len(module.SWAP_RESOLUTION_CASES) == 15
assert len(module.POST_WORKOUT_ANALYSIS_CASES) == 5
assert len(module.all_cases()) == 107
assert module.task_class_counts()["adjust_intent"] == 20
assert module.task_class_counts()["swap_resolution"] == 15
assert module.task_class_counts()["post_workout_analysis"] == 5
```

Preserve the existing unchanged nutrition, daily-brief, and branded-food assertions.

- [ ] **Step 2: Add fixture-quality assertions**

Add `test_fit396_workout_cases_are_unique_and_use_production_contracts` that checks all 40 IDs are unique; every adjust case has `athlete_constraint`, `current_plan`, `readiness`, and a non-empty expected mapping; every swap case has `typed_name`, `current_exercise`, `target_muscle`, `candidate_names`, `candidates`, and expected `canonical_name` (including explicit `None` no-match cases); every analysis case has `workout`, `context`, and non-empty expected-content checks. For every case, call `_chat_payload(case, "local-model")` and assert the response schema object is the corresponding production schema by identity.

- [ ] **Step 3: Upgrade the existing all-suite mock dispatch proof**

In `test_main_dispatches_all_cases_to_task_specific_models_without_common_model`, retain the complete fake `run_model_case` result shape and change the assertions to:

```python
assert len(captured) == 107
assert sum(1 for _case_id, model, _path in captured if model == "configured-vision-model") == 40
assert sum(1 for _case_id, model, _path in captured if model == "configured-text-model") == 67
```

Capture the parsed JSON output and assert `benchmark.case_count == 107`, `benchmark.schema_valid_count == 107`, and every public report row has an empty `failure_reasons` list. This tests the real orchestration/reporting behavior while mocking only the external model call.

- [ ] **Step 4: Run the focused RED proof**

Run:

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_meal_model_benchmark.py -k 'benchmark_case_counts_match_fit58_contract or fit396_workout_cases_are_unique or main_dispatches_all_cases'
```

Expected: FAIL because the current benchmark has 3 workout cases, 70 total cases, and the new fixture-quality test cannot observe the required 20/15/5 collections.

### Task 2: Author the 40 production-schema cases

**Files:**
- Modify: `support/meal_model_benchmark.py`
- Test: `tests/test_meal_model_benchmark.py`

**Interfaces:**
- Consumes: `_adjust_case(number, constraint, expected, *, readiness, plan, coverage)`, `_swap_case(number, typed_name, expected_name, *, current, muscle, candidates, coverage)`, `_analysis_case(number, workout, context, expected, coverage)`.
- Produces: case IDs `adjust-001` through `adjust-020`, `swap-001` through `swap-015`, and `analysis-001` through `analysis-005`.

- [ ] **Step 1: Expand adjust-intent cases to 20**

Preserve `adjust-001`. Add 19 cases spanning these concrete behaviors: make today lighter; shoulder injury avoidance; knee injury avoidance; low-back avoidance; drop cardio; combine lower sets and RPE; preserve upper work while avoiding legs; ambiguous “take it easy”; multi-constraint sleep plus soreness; travel equipment limits; shortened session; avoid overhead pressing; avoid elbow-loaded movements; swap one named exercise; fatigue without injury; keep cardio but lower intensity; recovery day request; only compound lifts; remove one muscle group; and a no-op request that should not invent changes. Expected mappings must use only `ADJUST_SCHEMA` fields and include a discriminating expected value such as `sets_delta_pct_range`, `rpe_delta_range`, `avoid_muscles`, `avoid_joints`, `drop_cardio`, or `swap`.

- [ ] **Step 2: Expand swap-resolution cases to 15**

Preserve `swap-001` as a no-match legacy case. Add 14 cases covering exact canonical name; capitalization; punctuation; common nickname; alias; one-character typo; transposed typo; equipment-qualified name; unambiguous compound nickname; multiple candidates with one alias match; empty input no-match; unrelated out-of-library request no-match; plausible but absent exercise no-match; and ambiguous generic term no-match. Never set `expected_name` to a name absent from the supplied candidates.

- [ ] **Step 3: Expand post-workout analysis cases to 5**

Preserve `analysis-001`. Add four cases covering high-RPE regression with a concern; volume progression with a win; pain-note safety cue; and under-recovered session with a conservative next-session cue. Each expected mapping must exercise the existing structured-content matcher across `summary`, `wins`, `concerns`, `comparison`, `next_session_cue`, and `empty_fields` only where emptiness is required.

- [ ] **Step 4: Run the focused GREEN proof**

Run the Task 1 command again. Expected: PASS with exactly 40 workout fixtures, 107 all-suite cases, production schema identity, and 107 mock-dispatch report rows.

### Task 3: Verify fixture scoring and unchanged benchmark behavior

**Files:**
- Modify only if the focused tests expose a fixture defect: `support/meal_model_benchmark.py`, `tests/test_meal_model_benchmark.py`

**Interfaces:**
- Consumes: the completed fixture lists and existing scoring/reporting functions.
- Produces: focused and full-suite green evidence without changing scoring logic to accommodate weak fixtures.

- [ ] **Step 1: Run all benchmark tests**

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_meal_model_benchmark.py
```

Expected: PASS.

- [ ] **Step 2: Run the configured repository check**

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q
```

Expected: PASS with no failures.

### Task 4: Generate the raw-free GX10 baseline artifact

**Files:**
- Create: `docs/meal-model-benchmark-gx10-baseline-2026-08-10.json`

**Interfaces:**
- Consumes: GX10 endpoint `http://100.99.46.2:1234`, text model `qwen/qwen3.6-35b-a3b`, vision model `qwen3-vl-30b-a3b-instruct`, and the existing `--output-file` public-report path.
- Produces: a committed JSON baseline with unchanged food-suite and 40-case workout results, per-task summaries, per-case verdicts, latency, and no raw prompt/completion fields.

- [ ] **Step 1: Confirm both configured models are live**

```bash
curl --silent --show-error --max-time 5 http://100.99.46.2:1234/v1/models
```

Expected: model IDs include `qwen/qwen3.6-35b-a3b` and `qwen3-vl-30b-a3b-instruct`.

- [ ] **Step 2: Run the one-command all-suite baseline**

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 support/meal_model_benchmark.py \
  --lm-studio-url http://100.99.46.2:1234 \
  --text-model qwen/qwen3.6-35b-a3b \
  --vision-model qwen3-vl-30b-a3b-instruct \
  --output-file docs/meal-model-benchmark-gx10-baseline-2026-08-10.json
```

Expected: exit 0; `case_counts.workout == 40`; `benchmark.case_count == 107`; task summaries include `adjust_intent`, `swap_resolution`, `post_workout_analysis`, and unchanged food task classes. Because no private image map is present, image-required food rows may honestly report `missing_image_mapping`; do not fabricate or commit private images to make those rows pass.

- [ ] **Step 3: Validate the committed artifact is raw-free and complete**

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 - <<'PY'
import json
from pathlib import Path

path = Path("docs/meal-model-benchmark-gx10-baseline-2026-08-10.json")
data = json.loads(path.read_text())
assert data["case_counts"]["workout"] == 40
assert data["benchmark"]["case_count"] == 107
assert {"adjust_intent", "swap_resolution", "post_workout_analysis"} <= set(data["benchmark"]["task_summary"])
text = path.read_text().lower()
for forbidden in ("raw_completion", "raw_prompt", "private/image", "athlete_constraint"):
    assert forbidden not in text
PY
```

Expected: PASS.

- [ ] **Step 4: Re-run focused benchmark tests after artifact creation**

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_meal_model_benchmark.py
```

Expected: PASS.

