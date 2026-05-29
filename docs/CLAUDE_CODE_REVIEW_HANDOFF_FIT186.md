# Claude Code Review Handoff: Fitness Dashboard

Linear: FIT-186
Created: 2026-05-29
Repo: `/Users/admin/fitness-dashboard`
Review mode: read-only unless the user explicitly asks for implementation.

## Purpose

Use this file as the starting brief for a Claude Code review of the Fitness Dashboard codebase and Linear backlog. The review should connect what Linear says is active or blocked with what the repo actually implements.

Do not rely only on this Markdown file. Refresh Linear first, inspect the codebase second, then use the context below as memory-derived background that may be stale.

## Copy-Paste Prompt For Claude Code

```text
You are reviewing the Fitness Dashboard repo.

Work from the local repo at /Users/admin/fitness-dashboard or the current worktree if the user points you elsewhere. Start read-only. Do not edit files, create branches, change Linear, or open PRs unless the user explicitly asks for implementation.

Goal:
Review Linear and the codebase together. Identify whether active Linear issues match the current implementation, whether any issue is stale, blocked, duplicated, missing acceptance criteria, or under-scoped, and whether the codebase has obvious correctness risks related to those issues.

Required first steps:
1. Refresh Linear for the Fitness app team. Inspect open issues in In Progress, In Review, Todo, and Backlog. Do not trust the static snapshot below without refreshing it.
2. Inspect repo status, branch/worktree, and the core files listed in this handoff.
3. Read docs/AGENT_WORKTREES.md, docs/testing.md, docs/CURRENT_STATE.md, docs/REPO_HYGIENE.md, docs/MEAL_INTAKE_CONTRACT.md, docs/FOOD_PHOTO_PRIVACY.md, docs/MEAL_MODEL_DECISION.md, and docs/RELEASE_RUNBOOK.md as relevant to the issues under review.
4. Trace behavior through code before making claims. If evidence is missing, label it as unknown.

Output format:
1. Must fix before merge
2. Should fix soon
3. Safe to merge / likely stale / no action
4. Follow-up Linear candidates
5. Verification performed
6. Unknowns and assumptions

For every finding:
- Cite the Linear issue ID.
- Cite exact files and line numbers when it is code-backed.
- State whether the issue is a blocker, stale, duplicate, acceptance-gap, implementation bug, test gap, or docs gap.
- State the narrowest verification command or manual check that would prove the finding.

Hard rules:
- No invented facts. If Linear, code, or runtime evidence does not prove it, say so.
- Do not suggest implementation across unrelated Linear issues on one branch.
- Do not treat local tests as full completion. In this repo, done means GitHub and Linear evidence too.
- For UI work, visual QA must cover modals, sheets, detail panels, add/edit forms, delete confirmations, mobile nav overlap, empty states, blocked states, warning states, and mobile detail views where values must stay readable.
- For live runtime incidents, verify the actual served port/worktree/runtime data before blaming source code.
```

## Current Linear Snapshot

This snapshot was pulled on 2026-05-29. Linear can drift quickly; refresh before using it as evidence.

In Progress:
- FIT-186: Create Claude Code review handoff for Fitness Dashboard audit.
- FIT-179: Adjust Plan should accept typed replacement exercises and infer load from similar history.
- FIT-158: Route fitness dashboard AI through ASUS GX10 with Qwen3-VL vision stack.
- FIT-176: Run private-image prompt and label experiments for Qwen3-VL macro accuracy.

In Review:
- FIT-181: Workout tab should render plan when smart recommendation retry chip is showing.
- FIT-141: Backend: add barcode lookup for packaged food logging.
- FIT-43: Login rejects owner with "Invalid username or password".

Todo:
- FIT-185: Fix fitness_data.db file descriptor leak in live dashboard runtime.
- FIT-183: Consolidate Fitness Dashboard to one canonical Tailnet URL.

Backlog:
- FIT-142: Frontend: add barcode scan input for packaged food logging.
- FIT-140: Follow-up: add barcode scan and lookup for packaged foods.
- FIT-131: Photo/text food logging adjusts workout plan from calorie and macro estimate.

Recent Done issues that may still matter for regression review:
- FIT-184: Runtime 5121 Settings showed AI Coach and data sources offline.
- FIT-182: Manual 2026-05-25 workout was logged into the wrong Tailnet surface.
- FIT-151, FIT-148, FIT-150, FIT-139, FIT-152, FIT-123, FIT-147, FIT-149: recent food, dashboard, stats, and empty-state fixes.

## Repo Map

Core backend:
- `app.py`: Flask routes, workout engine, recommendation logic, AI routes, metrics, history, settings, Oura endpoints.
- `auth.py`: login/session handling, owner guard, public route allowlist.
- `data_store.py`: SQLite/local state helpers.
- `apple_health_parser.py`, `health_ingest.py`: Apple Health and Health Auto Export parsing/ingest/status.
- `oura_client.py`, `oura_sleep_sync.py`: Oura API and cache paths.
- `lm_studio_adapter.py`, `local_vision_adapter.py`, `vision_estimator.py`, `meal_estimate_schema.py`: local AI and vision-estimate path.
- `meal_log_policy.py`, `meal_text_parser.py`, `personal_vocab.py`, `branded_food_lookup.py`, `open_food_facts_client.py`, `usda_fdc_client.py`, `nutritionix_client.py`, `heb_product_lookup.py`: nutrition and lookup path.

Core frontend:
- `templates/index.html`: main UI, tabs, modals, script/style cache-bust references.
- `static/js/app.js`: main client behavior, forms, active workout, meal flow, UI state.
- `static/js/dashboard.js`: dashboard-specific rendering.
- `static/css/style.css`: mobile-first UI styling.
- `static/js/sw.js`, `static/manifest.json`: PWA support.

Important docs:
- `README.md`: product overview and local run instructions.
- `docs/CURRENT_STATE.md`: repo/runtime state snapshot, now older than some late-May work.
- `docs/AGENT_WORKTREES.md`: branch/worktree safety rules.
- `docs/testing.md`: no-live-network test policy.
- `docs/REPO_HYGIENE.md`: runtime/generated/private artifact policy.
- `docs/RELEASE_RUNBOOK.md`: release, restart, rollback, and smoke checks.
- `docs/FOOD_PHOTO_PRIVACY.md`: food-photo privacy contract.
- `docs/MEAL_INTAKE_CONTRACT.md`: meal intake API/review contract.
- `docs/MEAL_MODEL_DECISION.md`: model-routing decision context.

Relevant test areas:
- `tests/test_meal_intake_contract.py`
- `tests/test_meal_intake_api.py`
- `tests/test_meal_estimate_schema.py`
- `tests/test_meal_model_benchmark.py`
- `tests/test_dashboard_retry_contract.py`
- `tests/test_dashboard_render_contract.py`
- `tests/test_workout_sync_queue_js.py`
- `tests/test_untracked_machine_load_inference.py`
- `tests/test_ai_health_metrics.py`
- `tests/test_oura_sync.py`
- `tests/test_apple_health_recommendation_bridge.py`
- `tests/test_no_live_network_guard.py`

## Context From Codex Memory

Treat this section as background only. Re-check live Linear, repo state, and runtime evidence before relying on it.

- The primary current checkout is `/Users/admin/fitness-dashboard`. Older memories may mention `/Users/admin/.openclaw/workspace/projects/fitness-dashboard`; treat that as historical unless a live service is actually running from it.
- The repo uses a strict Linear and branch gate. Every code-facing change needs a Linear issue and its own branch/worktree from `origin/main`.
- The durable lane split is: Codex owns backend, data contracts, repo safety, tests, CI, migrations, and cross-cutting audits; Claude UI owns mobile visual polish, layout, copy, and front-end-only interaction fixes.
- FIT-179 was previously blocked by a live-demo false positive around typed replacement matching: `dragon press` mapped to `Chest Press` when unchanged/no-match behavior was required. Review the current implementation and issue status before assuming it is fixed.
- FIT-176 was previously harness-ready but blocked on missing private image map/photos. Do not use public random food images as proof for that issue.
- FIT-121 proof exposed a real served-worktree problem: the installed PWA and Tailnet ports can point at different code. Always verify the actual served path and cache-busted assets before accepting visual/runtime proof.
- Live runtime incidents may be data/runtime-rooted rather than source-rooted. Prior examples involved auth SQLite file descriptor exhaustion, Oura/Apple Health status failure, and wrong Tailnet surface usage.
- For Apple Health and Oura issues, inspect authenticated endpoints, logs, SQLite handle usage, and runtime data. A 302/401 alone only proves auth gating, not health.
- Food-photo privacy is strict: do not expose raw photos, raw model traces, private local paths, base64 image content, or private lookup keys in committed docs, logs, test fixtures, or PR evidence.

## Suggested Review Focus

1. Queue hygiene: Are FIT-183, FIT-185, FIT-181, FIT-179, FIT-176, FIT-158, FIT-141, and FIT-43 in the right statuses, and do their descriptions match the repo?
2. Runtime truth: Are Tailnet ports, launchd service paths, and local worktrees aligned with what Linear says?
3. File descriptor risk: Does `fitness_data.db` or any SQLite helper leak handles in paths related to FIT-185?
4. Workout plan rendering: Does smart recommendation failure block `renderNextWorkout()` or active workout start paths in a way that matches FIT-181?
5. Typed exercise replacement: Does the adjust/swap flow reject no-match inputs honestly and preserve active workout state?
6. Photo/text/barcode food logging: Are FIT-131, FIT-140, FIT-141, and FIT-142 split correctly between backend and UI, with no duplicate or stale scope?
7. Vision/model routing: Are ASUS primary and Mac fallback docs/env/code consistent with FIT-158 and FIT-176?
8. Privacy and test coverage: Do tests avoid live external network and private runtime data?

## Verification Commands To Consider

Pick the narrowest commands that match the reviewed issue. Do not run live destructive checks without explicit user approval.

```bash
git status --short --branch
python -m pytest tests/test_no_live_network_guard.py
python -m pytest tests/test_untracked_machine_load_inference.py
python -m pytest tests/test_dashboard_retry_contract.py tests/test_dashboard_render_contract.py
python -m pytest tests/test_meal_intake_contract.py tests/test_meal_intake_api.py tests/test_meal_estimate_schema.py
python -m pytest tests/test_ai_health_metrics.py tests/test_oura_sync.py tests/test_apple_health_recommendation_bridge.py
node --check static/js/app.js
node --check static/js/dashboard.js
```

For authenticated live checks, use `support/self_test.sh` only with a valid cookie or owner credentials supplied outside the repo.
