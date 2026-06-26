# FIT-239 WHOOP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add durable WHOOP sync, local normalized facts, bounded recommendation modifiers, source UI, and safety proof for FIT-239.

**Architecture:** Direct WHOOP OAuth/API feeds local SQLite persistence and normalized daily facts. Existing Flask routes expose status/freshness/dashboard/recommendation data, while focused helper modules own WHOOP client, store, and recommendation projection.

**Tech Stack:** Python 3, Flask, SQLite, pytest, plain JavaScript in `static/js/app.js`, existing local-first data files, `gh` CLI, Linear/GitHub connectors.

## Global Constraints

- Start from `origin/main` in `/Users/admin/codex-worktrees/fitness-fit-239-whoop` on `codex/fit-239-whoop-integration`.
- Do not edit `/Users/admin/fitness-dashboard` except read-only audit commands.
- Do not commit `.whoop-client-id`, Keychain material, access tokens, refresh tokens, OAuth codes, raw WHOOP payloads, raw health CSVs, screenshots with private values, or generated local runtime DBs.
- Store WHOOP token material only in macOS Keychain or an equivalent out-of-repo `0600` secret store; local app storage may hold opaque token references only.
- OAuth state must be single-use, expire after 10 minutes, bind to the initiating user/session where app context exists, and WHOOP callback responses must send `Cache-Control: no-store`.
- Existing request logging must redact `code`, `state`, `token`, `access_token`, `refresh_token`, Authorization headers, bearer strings, and callback query strings before WHOOP callback routes are enabled.
- Noop is reference/import-only; do not vendor Noop code or add BLE/runtime coupling.
- Open Wearables remains optional reference/future adapter; direct official WHOOP API is the FIT-239 v1 path.
- WHOOP affects recommendations only through bounded modifiers and source explanations.
- Apple Health remains completed-workout/load truth; avoid double-counting WHOOP workouts.
- Missing/stale/pending/unscored/calibrating data must not become zero.

---

### Task 1: Groundtruth Audit And Spec Artifacts

**Owner:** main controller

**Files:**
- Create: `docs/audits/FIT-239/groundtruth-audit.md`
- Create: `docs/superpowers/specs/2026-06-26-fit-239-whoop-integration-design.md`
- Create: `docs/superpowers/plans/2026-06-26-fit-239-whoop-integration.md`

**Interfaces:**
- Produces: design, plan, and audit files used by all implementation agents.

- [x] Read Linear FIT-239 description and comments.
- [x] Verify clean worktree from current `origin/main`.
- [x] Verify codebase-memory index status and key graph anchors.
- [x] Run baseline focused tests:

```bash
python3 -m pytest tests/test_wearable_freshness_contract.py tests/test_freshness.py -q
```

Expected: `29 passed`.

- [ ] Commit audit/spec/plan artifacts.

### Task 2: Secret Config And OAuth Scaffolding

**Owner:** `cs-backend-engineer`

**Files:**
- Create: `whoop_client.py`
- Create: `whoop_store.py`
- Modify: `app.py`
- Test: `tests/test_whoop_oauth.py`

**Interfaces:**
- Produces: `load_whoop_config()`, `create_whoop_authorization_url()`, `exchange_whoop_code()`, `refresh_whoop_token()`, `get_whoop_status()`.
- Consumes: local `.whoop-client-id` and macOS Keychain item by reference only; never logs values.

- [ ] Add `.whoop-client-id` and WHOOP local runtime artifacts to `.gitignore`.
- [ ] Write failing tests for missing config, present config without value disclosure, state creation, single-use state, expired state, user/session-bound state, callback state validation, `Cache-Control: no-store`, request-log redaction, and redacted errors.
- [ ] Implement OAuth start/callback routes:

```text
POST /api/whoop/connect/start
GET /api/whoop/callback
GET /api/whoop/status
POST /api/whoop/disconnect
```

- [ ] Implement request-log redaction for `code`, `state`, `token`, `access_token`, `refresh_token`, Authorization headers, bearer strings, and callback query strings.
- [ ] Ensure responses expose statuses only: `missing_config`, `disconnected`, `connected`, `reauth_required`, `error`.
- [ ] Run:

```bash
python3 -m pytest tests/test_whoop_oauth.py -q
```

### Task 3: WHOOP Client, Pagination, Refresh, And Redaction

**Owner:** `cs-backend-engineer`

**Files:**
- Modify: `whoop_client.py`
- Test: `tests/test_whoop_client.py`

**Interfaces:**
- Produces: `WhoopClient`, `WhoopApiError`, `redact_whoop_error()`.
- Consumes: token reference from `whoop_store.py`.

- [ ] Write mocked tests for paginated collection fetch, rate-limit retry classification, timeout handling, concrete protected token storage, token refresh, and atomic refresh-token rotation.
- [ ] Implement collection helpers for recovery, cycles, sleep, and workouts.
- [ ] Ensure every error path redacts Authorization headers, OAuth codes/states, access tokens, refresh tokens, and raw payload fields.
- [ ] Run:

```bash
python3 -m pytest tests/test_whoop_client.py -q
```

### Task 4: Local Persistence, Sync Runs, And Freshness Projection

**Owner:** `cs-backend-engineer`

**Files:**
- Modify: `whoop_store.py`
- Modify: `app.py`
- Test: `tests/test_whoop_store.py`
- Test: `tests/test_whoop_freshness.py`

**Interfaces:**
- Produces: `init_whoop_db()`, `record_whoop_sync_run()`, `upsert_whoop_records()`, `project_whoop_daily_facts()`, `latest_whoop_freshness()`.
- Extends: `_compute_data_freshness()` with `whoop`.

- [ ] Write tests for schema creation, idempotent upserts, score-state projection, stale/aging/fresh/missing classification, and `PENDING_SCORE` behavior.
- [ ] Implement local tables and projection helpers.
- [ ] Extend `/api/freshness` and dashboard freshness payloads with WHOOP.
- [ ] Run:

```bash
python3 -m pytest tests/test_whoop_store.py tests/test_whoop_freshness.py tests/test_wearable_freshness_contract.py tests/test_freshness.py -q
```

### Task 5: Manual Sync, Scheduled Sync, Backfill, Retry Contract

**Owner:** `DevOps Engineer`

**Files:**
- Create: `scripts/whoop_sync.py`
- Modify: `app.py`
- Modify: `docs/audits/FIT-239/groundtruth-audit.md` if operational assumptions change.
- Test: `tests/test_whoop_sync.py`

**Interfaces:**
- Produces: `POST /api/whoop/sync`, CLI flags `--mode normal|backfill|repair`, `--days`.
- Consumes: `WhoopClient` and `whoop_store` interfaces.

- [ ] Write tests for manual sync, initial backfill bounds, repair mode, retryable errors, non-blocking dashboard behavior, and redacted sync-run errors.
- [ ] Implement manual sync route and CLI entry point.
- [ ] Document launchd/cron usage in a safe comment or README section without creating an active automation.
- [ ] Run:

```bash
python3 -m pytest tests/test_whoop_sync.py -q
python3 scripts/whoop_sync.py --help
```

### Task 6: Recommendation Modifiers And Source Conflict Model

**Owner:** `cs-backend-engineer`

**Files:**
- Create: `whoop_recommendations.py`
- Modify: `app.py`
- Test: `tests/test_whoop_recommendations.py`
- Test: `tests/test_whoop_source_conflicts.py`

**Interfaces:**
- Produces: `build_whoop_recommendation_signals()`, `apply_wearable_modifiers()`, `detect_wearable_source_conflicts()`.
- Extends: `/api/whoop/recommendation-signals`, `/api/recommendation/smart`, `/api/next-workout`, dashboard payloads.

- [ ] Write tests for low recovery dampening, high strain dampening, sleep performance dampening, sleep-need nutrition explanation, no eat-less guidance from low recovery, stale/display-only behavior, and Oura/WHOOP conflict.
- [ ] Implement banded source conflict detection and conservative plan choice.
- [ ] Keep Apple Health load truth and dedupe WHOOP workouts against Apple Health.
- [ ] Run:

```bash
python3 -m pytest tests/test_whoop_recommendations.py tests/test_whoop_source_conflicts.py tests/test_progress_loop_completion_to_recommendation.py -q
```

### Task 7: CSV Import, Backup/Export/Delete/Revoke Safety

**Owner:** `security_auditor` for design review, then backend implementer for code

**Files:**
- Modify: `whoop_store.py`
- Modify: `app.py`
- Test: `tests/test_whoop_csv_import.py`
- Test: `tests/test_whoop_backup_privacy.py`

**Interfaces:**
- Produces: `POST /api/whoop/import-csv`, `GET /api/whoop/imports`, delete/disconnect helpers.
- Extends: backup/export/import contracts by explicitly excluding token/raw payload material.

- [ ] Write tests for file size cap, row cap, strict column whitelist, UTF-8 requirement, numeric/date bounds, formula escaping, idempotency hash, import listing, export exclusion, import rejection of token/raw-payload keys, disconnect without delete, upstream-revocation failure still purging local token material, idempotent disconnect/delete, and delete with data removal.
- [ ] Implement CSV parser and import batch storage.
- [ ] Ensure backup export never includes token material or raw WHOOP payloads, and backup import rejects token material, token references, and raw provider payload keys.
- [ ] Run:

```bash
python3 -m pytest tests/test_whoop_csv_import.py tests/test_whoop_backup_privacy.py tests/test_backup_import_food_logs.py -q
```

### Task 8: Dashboard And Settings UI

**Owner:** `cs-frontend-engineer`

**Files:**
- Modify: `templates/index.html`
- Modify: `static/js/app.js`
- Modify: `static/css/app.css`
- Test: `tests/test_dashboard_render_contract.py`
- Test: `tests/test_whoop_ui_contract.py`

**Interfaces:**
- Consumes: `wearable_sources`, `recommendation_sources`, `source_conflicts`, WHOOP status/sync endpoint payloads.
- Produces: Wearable Sources Settings section, dashboard source chips, recommendation source drawer/bottom sheet.

- [ ] Write JS/render contract tests for connected, disconnected, stale, error, no-data, pending score, unscorable, calibrating, reauth required, CSV-only, sync, manual sync, disconnect, and source conflict.
- [ ] Implement source chips and source drawer without breaking current Oura/Apple Health rows.
- [ ] Wire WHOOP Settings actions to status/sync/disconnect endpoints.
- [ ] Run:

```bash
python3 -m pytest tests/test_dashboard_render_contract.py tests/test_whoop_ui_contract.py -q
node --check static/js/app.js
```

### Task 9: Browser QA And Visual Proof

**Owner:** `cs-frontend-engineer` plus `test-architect`

**Files:**
- Create proof artifacts under a gitignored proof directory only.
- Do not commit screenshots unless explicitly scrubbed and approved.

**Interfaces:**
- Consumes: local app runtime and safe mocked WHOOP states.

- [ ] Start local app server from the FIT-239 worktree.
- [ ] Verify desktop and mobile dashboard source chips/freshness labels.
- [ ] Verify Settings integration row, connect/disconnect, manual sync, last successful sync, and failure copy.
- [ ] Verify recommendation explanation drawer/panel.
- [ ] Verify stale, error, no-data, pending, unscorable, calibrating, reauth, CSV-only, and Oura/Apple Health/WHOOP conflict states.
- [ ] Verify light/dark contrast, keyboard paths, and no overlap.

### Task 10: Final Safety, Review, PR, Merge Gate

**Owner:** main controller

**Files:**
- PR evidence comment only after secret/artifact scan passes.
- Linear closeout comment only after PR evidence is safe.

- [ ] Run focused tests from all tasks.
- [ ] Run relevant broader tests:

```bash
python3 -m pytest tests/test_wearable_freshness_contract.py tests/test_freshness.py tests/test_dashboard_render_contract.py tests/test_backup_import_food_logs.py -q
```

- [ ] Run `git diff --check`.
- [ ] Run artifact safety scanner:

```bash
python3 /Users/admin/.codex/skills/artifact-safety-checker/scripts/artifact_safety_check.py --repo /Users/admin/codex-worktrees/fitness-fit-239-whoop --base origin/main
```

- [ ] Run autoreview until clean:

```bash
/Users/admin/.codex/skills/autoreview/scripts/autoreview --mode branch --base origin/main
```

- [ ] Push branch, create PR, post standalone PR evidence comment.
- [ ] Check mergeability:

```bash
gh pr view <PR> --json mergeStateStatus,state,baseRefName,headRefName
```

- [ ] Add Linear closeout comment with branch, PR, commit, tests, review result, merge state, risks, and follow-ups.
