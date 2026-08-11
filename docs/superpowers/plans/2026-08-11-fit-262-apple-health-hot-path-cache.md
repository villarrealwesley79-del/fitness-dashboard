# FIT-262 Apple Health Hot-Path Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development, superpowers:systematic-debugging, and superpowers:verification-before-completion to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse and normalize Apple Health recommendation workouts no more than once per requested day window during one Flask request, and compute each dashboard muscle's readiness once while preserving current results across independent requests.

**Architecture:** Keep cache lifetime request-local by storing normalized workout lists on Flask `g`, keyed by the exact `days` argument. This avoids stale cross-request health data and adds no global TTL or invalidation policy. Separately build one dashboard readiness map and derive both the average and muscle rows from it.

**Tech Stack:** Python 3, Flask request context, pytest.

## Global Constraints

- Modify only `app.py`, focused FIT-262 tests, and this plan unless a verified test contract requires another existing test file.
- Do not add dependencies, a process-global cache, background work, config, or environment flags.
- Preserve Apple Health merge, dedupe, failure-tolerance, recommendation, and muscle-fatigue behavior.
- FIT-366 remains dependency context only; do not modify its branch or PR and do not cache beyond one request.
- Use strict RED-GREEN TDD and retain the exact failing and passing commands/results.
- Do not commit, push, mutate GitHub/Linear, touch locks, merge, deploy, or start another task; Sol owns integration.

---

### Task 1: Prove request-local parsing semantics

**Files:**
- Modify: `tests/test_apple_health_recommendation_bridge.py`
- Modify: `app.py:7`
- Modify: `app.py:1569-1590`

**Interfaces:**
- Consumes: Flask `has_request_context()`, Flask `g`, `apple_health_parser.parse_workouts()`, and `apple_health_parser._get_sync_records(kind, days)`.
- Produces: `_load_apple_health_recommendation_workouts(days=28) -> list[dict]` with request-local memoization keyed by `days`.

- [ ] **Step 1: Add failing request-cache tests**

Add tests that monkeypatch both parser sources with counters, call `_load_apple_health_recommendation_workouts(days=28)` twice inside one `app.test_request_context()`, and assert each source ran once. Add a second request context and assert both counters increment again. Add a same-request `days=7` call and assert it receives an independent cache entry.

- [ ] **Step 2: Run RED**

Run:

```sh
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_apple_health_recommendation_bridge.py -k 'request_cache or request_local'
```

Expected before implementation: parser counters exceed one inside the same request.

- [ ] **Step 3: Implement minimal request-local memoization**

Import Flask `g`. At the start of `_load_apple_health_recommendation_workouts`, check a private dictionary on `g` only when `has_request_context()` is true. Key it by the normalized `days` value. After the existing tolerant parse, sync-read, and normalization logic completes, store a list copy in that dictionary and return a list copy. Outside request context, preserve the current uncached behavior.

- [ ] **Step 4: Run GREEN and adjacent bridge tests**

Run:

```sh
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_apple_health_recommendation_bridge.py
```

Expected: all tests pass, including cache isolation, per-days keys, existing dedupe, HR intensity, and recommendation behavior.

### Task 2: Compute dashboard readiness once per muscle

**Files:**
- Modify: `tests/test_dynamic_cardio_recommendations.py` or add one focused `tests/test_fit262_apple_health_hot_path_cache.py`
- Modify: `app.py:5095-5120`

**Interfaces:**
- Consumes: `get_readiness_score(muscle, SORENESS_DATA, volume, CARDIO_DATA, WORKOUTS) -> dict`.
- Produces: one local `readiness_by_muscle: dict[str, dict]` reused for average readiness and each dashboard muscle row.

- [ ] **Step 1: Add a failing dashboard call-count test**

Monkeypatch `calculate_volume` to return at least two muscles and wrap `get_readiness_score` with a counter. Exercise `GET /api/dashboard` through the Flask test client and assert each muscle key is evaluated exactly once while response average and muscle readiness values remain correct.

- [ ] **Step 2: Run RED**

Run:

```sh
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_dynamic_cardio_recommendations.py -k 'dashboard and readiness and once'
```

Expected before implementation: each volume muscle is counted twice.

- [ ] **Step 3: Implement the readiness map**

Replace the dashboard list comprehension plus second-loop recomputation with one dictionary comprehension:

```python
readiness_by_muscle = {
    muscle: get_readiness_score(muscle, SORENESS_DATA, volume, CARDIO_DATA, WORKOUTS)
    for muscle in volume
}
```

Derive `readiness_scores`, `avg_readiness`, and each `muscle_data` row from this map without changing response fields or defaults.

- [ ] **Step 4: Run GREEN and affected route tests**

Run:

```sh
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_dynamic_cardio_recommendations.py tests/test_apple_health_recommendation_bridge.py tests/test_muscle_recovery_performance_debt.py
```

Expected: all tests pass with no response-contract changes.

### Task 3: Verify the complete branch

**Files:**
- Verify: all changed files

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: a clean FIT-262-scoped diff and exact verification receipt for Sol.

- [ ] **Step 1: Run the configured full suite**

```sh
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q
```

Expected: zero failures; baseline was 1852 passed, 1 skipped.

- [ ] **Step 2: Audit scope and whitespace**

```sh
git diff --check
git status --short
git diff -- app.py tests/test_apple_health_recommendation_bridge.py tests/test_dynamic_cardio_recommendations.py tests/test_fit262_apple_health_hot_path_cache.py
```

Expected: only FIT-262 plan, implementation, and focused test changes; no secrets, data, config, or unrelated refactors.

- [ ] **Step 3: Return evidence**

Report actual model/effort, files changed, exact RED/GREEN/full-suite commands and results, request-isolation proof, risks or untested items, and one verdict: PASS, REVISE, or BLOCKED. Do not commit or publish.
