# FIT-239 Test Matrix

Date: 2026-06-25
Repo: `/Users/admin/codex-worktrees/fitness-fit-239-whoop`
Branch: `codex/fit-239-whoop-integration`
Base: `origin/main`

Historical note: this was a pre-implementation audit. As of 2026-06-26, WHOOP
code has landed on `main` with OAuth/API sync, manual CSV import, local
normalized daily facts, freshness/status, recommendation modifiers, UI
contracts, backup/import guards, and Open Wearables sync redaction. Use the
checked-in tests on `main` as the authoritative current test inventory.

## Status

- Original branch state was docs-only for FIT-239. Relative to the current
  `main`, this section is no longer current implementation state.
- This matrix is safe to land now because it does not change product behavior and it stays within the requested write boundary.
- Existing confidence anchors are still the current freshness and render contracts:
  - `python3 -m pytest tests/test_wearable_freshness_contract.py tests/test_freshness.py -q`
  - `python3 -m pytest tests/test_dashboard_render_contract.py tests/test_backup_import_food_logs.py -q`

## Grounded Surface

### Existing backend anchors

| Surface | Current anchor | Why it matters for FIT-239 |
|---|---|---|
| Freshness API | `app.py:freshness_only`, `app.py:_compute_data_freshness` | WHOOP must extend the side-effect-free freshness shape without mutating recommendation state. |
| Dashboard API | `app.py:api_dashboard` via freshness callers | Dashboard must expose wearable-source chips, recommendation sources, and conflict state without live WHOOP fetches on page load. |
| Recommendation APIs | `app.py:generate_next_workout`, `app.py:smart_recommendation_api`, `app.py:api_next_workout` | WHOOP must act as a bounded modifier, not a replacement recommendation engine. |
| Vitals | `app.py:api_vitals` | WHOOP source blocks need to appear without hiding Oura or Apple Health provenance. |
| Backup/import | `app.py:export_backup`, `app.py:import_backup` | Token material and raw payloads must stay out of export/import contracts. |

### Existing frontend anchors

| Surface | Current anchor | Why it matters for FIT-239 |
|---|---|---|
| Dashboard paint path | `static/js/app.js:paintDashboardFromState` | Source chips, explanation text, confidence degradation, and conflict copy all fan through this render path. |
| Freshness chips | `static/js/app.js:renderFreshnessChips` | WHOOP must join the chip model without breaking current Oura and Apple Health states. |
| Recommendation card | `static/js/app.js:renderNextWorkout` plus dashboard paint path | Modifier explanations and source drawer states must remain readable in degraded states. |
| Settings integration view | `static/js/app.js:renderSettings`, `renderSettingsGroupSummaries` | WHOOP connection, sync, reauth, CSV-only, stale, and error states all need explicit rows and actions. |

### Existing test anchors

| Area | Current tests to preserve |
|---|---|
| Freshness contracts | `tests/test_wearable_freshness_contract.py`, `tests/test_freshness.py` |
| Dashboard render contracts | `tests/test_dashboard_render_contract.py` |
| Backup/import behavior | `tests/test_backup_import_food_logs.py` |
| Recommendation stability | `tests/test_progress_loop_completion_to_recommendation.py` |

## Test Structure

### New focused test files

| File | Scope | Priority |
|---|---|---|
| `tests/test_whoop_oauth.py` | missing config, connect start, callback, disconnect, redacted failures | P0 |
| `tests/test_whoop_client.py` | pagination, timeout, retry, rate-limit classification, refresh rotation | P0 |
| `tests/test_whoop_store.py` | schema, idempotent upserts, sync runs, daily facts | P0 |
| `tests/test_whoop_freshness.py` | fresh, aging, stale, missing, pending score, calibrating | P0 |
| `tests/test_whoop_sync.py` and `tests/test_whoop_import_sync_backup.py` | manual sync route, backfill bounds, repair mode, non-blocking behavior | P1 |
| `tests/test_whoop_recommendations.py` | bounded modifiers, display-only states, nutrition explanation | P0 |
| `tests/test_whoop_source_conflicts.py` | Oura/WHOOP band conflict, conservative plan choice, Apple Health load truth | P0 |
| `tests/test_whoop_import_sync_backup.py` | CSV caps, whitelist, bounds, UTF-8, idempotency, formula-safe echoes | P0 |
| `tests/test_whoop_import_sync_backup.py` | export exclusion, import rejection of tokens, disconnect vs delete | P0 |
| `tests/test_whoop_ui_contract.py` | chips, settings rows, drawer/bottom-sheet state coverage | P1 |

### Fixture and mock strategy

| Need | Recommended approach |
|---|---|
| OAuth state and callbacks | Flask test client plus server-side fake state store and fake Keychain/token-ref adapter. |
| WHOOP API responses | Pure mocked client payloads for recovery, cycles, sleep, workouts; include paginated and redacted failure fixtures. |
| Token rotation | Store-level fake transactional writer that can fail between new-token receipt and durable commit. |
| Sync runs | Temp SQLite DB under `tmp_path`; assert sync-run rows, upserts, and repair-mode behavior. |
| Recommendation inputs | Small normalized daily-fact fixtures for WHOOP, Oura, Apple Health, soreness, ACWR, and workout history. |
| Frontend states | JS contract fixtures that render dashboard/settings with server payload permutations, not live browser data. |
| Browser proof | Safe mocked WHOOP states only. Never use real OAuth codes, tokens, or raw payloads in proof artifacts. |

## Coverage Matrix

| Area | Critical scenarios | Primary proof | Current main status |
|---|---|---|---|
| OAuth mocks | missing config, valid connect start, callback state mismatch, callback exchange failure, disconnect without delete | `tests/test_whoop_oauth.py` | Implemented on `main`; keep this row as historical acceptance context. |
| Token refresh | refresh before expired API call, new refresh token rotation, mid-commit failure, redacted refresh error | `tests/test_whoop_client.py` | Implemented on `main`; keep this row as historical acceptance context. |
| Sync | manual sync, initial backfill 30-90d bounds, repair mode 7d, retryable vs terminal errors, no dashboard-blocking fetch | `tests/test_whoop_import_sync_backup.py` plus route tests | Implemented on `main`; `scripts/whoop_sync.py` is not the current scheduled entry point. |
| Freshness | fresh, aging, stale, missing, pending score, unscored, calibrating, last data point distinct from last sync | `tests/test_whoop_freshness.py` plus existing freshness contract tests | Implemented on `main`; freshness includes WHOOP. |
| Recommendations | low recovery dampens, high strain dampens, poor sleep dampens, sleep need explains fueling, stale/display-only does not zero out plan | `tests/test_whoop_recommendations.py` | Implemented on `main` as bounded modifiers. |
| Conflict and dedupe | WHOOP vs Oura band disagreement, conservative tie-break, Apple Health remains completed-workout truth, WHOOP workouts deduped from Apple Health load | `tests/test_whoop_source_conflicts.py` | Implemented on `main`; Apple Health remains load truth. |
| CSV import | file-size cap, row cap, UTF-8 only, strict columns, numeric/date bounds, idempotency hash, formula-safe cell echo, rejected-row summary | `tests/test_whoop_import_sync_backup.py` | Implemented on `main` through `/api/whoop/import-csv`. |
| Backup/export/delete | export excludes tokens and raw payloads, import rejects token material, disconnect preserves local data, delete removes WHOOP-derived data and import batches | `tests/test_whoop_import_sync_backup.py` | Implemented on `main`; backups include normalized daily facts only. |
| UI state contracts | connected, disconnected, missing config, stale, error, no-data, reauth required, pending score, calibrating, CSV-only, conflict, manual sync in progress | `tests/test_whoop_ui_contract.py` and `tests/test_dashboard_render_contract.py` | Implemented on `main` through Settings/dashboard contracts. |
| Browser proof | desktop/mobile, light/dark, source chips, settings row actions, manual sync, disconnect, drawer state, keyboard path, no overlap | Manual local QA after frontend slice lands | Historical QA requirement; current evidence should come from the PR that changes UI behavior. |
| Security checks | token/code/header redaction, ignored local secrets, no raw payload leak in responses/logs/export/proof, CSV formula defense | Focused pytest plus artifact scan | Implemented on `main`; Open Wearables sync redaction is covered separately. |

## Priority Order

1. OAuth and token rotation because a broken refresh path can strand the connection and silently invalidate the integration.
2. Freshness and recommendation modifiers because FIT-239 fails if WHOOP becomes a hidden override or a silent zero.
3. Conflict and dedupe because load double-counting and source disagreement directly affect training guidance quality.
4. Backup/export/delete and CSV security because those are data-loss and privacy boundaries.
5. UI contract and browser proof because the feature is incomplete if states exist server-side but are ambiguous or unreadable.

## Browser Proof Matrix

| Surface | States that must be clicked through |
|---|---|
| Dashboard recommendation card | WHOOP fresh, WHOOP stale, WHOOP missing, WHOOP pending score, WHOOP calibrating, WHOOP vs Oura conflict |
| Dashboard source drawer or bottom sheet | source used today, source ignored today, conflict detail, freshness detail, Apple Health load-source explanation |
| Settings wearable row | disconnected, missing config, connected, sync running, sync failed, reauth required, CSV-only imported, delete confirmation |
| Mobile layout | source chips wrap cleanly, drawer opens without overlap, action buttons stay visible, detail text remains readable |
| Light/dark | status chips, active text, warning copy, disabled text, destructive actions all maintain readable contrast |
| Keyboard and focus | connect, manual sync, disconnect, drawer open/close, delete confirmation focus trap and restore |

## Security Checklist

- Verify `.whoop-client-id` and any WHOOP runtime artifacts are ignored before any implementation branch is called reviewable.
- Verify no test fixture or proof artifact includes real OAuth codes, access tokens, refresh tokens, Authorization headers, or raw WHOOP payloads.
- Verify refresh-token rotation tests cover the atomic-write failure case explicitly.
- Verify export/import tests assert absence of token fields and raw payload blobs, not just presence of allowed fields.
- Verify CSV tests cover spreadsheet-formula prefixes such as `=`, `+`, `-`, and `@` in echoed values.
- Verify artifact safety scan runs before PR evidence is posted.

## Recommended Commands

### Focused commands by slice

```bash
python3 -m pytest tests/test_whoop_oauth.py tests/test_whoop_client.py -q
python3 -m pytest tests/test_whoop_store.py tests/test_whoop_freshness.py tests/test_wearable_freshness_contract.py tests/test_freshness.py -q
python3 -m pytest tests/test_whoop_recommendations.py tests/test_whoop_source_conflicts.py tests/test_progress_loop_completion_to_recommendation.py -q
python3 -m pytest tests/test_whoop_import_sync_backup.py tests/test_backup_import_food_logs.py -q
python3 -m pytest tests/test_dashboard_render_contract.py tests/test_whoop_ui_contract.py -q
node --check static/js/app.js
python3 scripts/whoop_sync.py --help
```

### Broader commands before PR

```bash
python3 -m pytest tests/test_wearable_freshness_contract.py tests/test_freshness.py tests/test_dashboard_render_contract.py tests/test_backup_import_food_logs.py -q
git diff --check
python3 /Users/admin/.codex/skills/artifact-safety-checker/scripts/artifact_safety_check.py --repo /Users/admin/codex-worktrees/fitness-fit-239-whoop --base origin/main
```

### Historical safe command set before implementation landed

```bash
python3 -m pytest tests/test_wearable_freshness_contract.py tests/test_freshness.py -q
python3 -m pytest tests/test_dashboard_render_contract.py tests/test_backup_import_food_logs.py -q
```

## Open Gaps

- Historical gaps in this audit have been superseded by the implementation on
  `main`. For current gaps, inspect the latest Linear issue, checked-in tests,
  and `docs/CURRENT_STATE.md`.
