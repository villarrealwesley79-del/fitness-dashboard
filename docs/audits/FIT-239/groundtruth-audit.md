# FIT-239 Groundtruth Audit

Date: 2026-06-26
Repo: `/Users/admin/codex-worktrees/fitness-fit-239-whoop`
Branch: `codex/fit-239-whoop-integration`
Base: `origin/main` at `bd8f18651a26045324825d40fbb5a5b1b2e49ac6`

## Source Of Truth

- Linear issue: FIT-239, including the "Execution stack and merge contract" comment.
- Active repo workflow contract: AGENTS instructions supplied in the thread. No `AGENTS.md` file exists in the repo root.
- Code graph: `Users-admin-fitness-dashboard`, indexed and ready with 13,428 nodes and 25,769 edges.
- Architecture graph: `.understand-anything/knowledge-graph.json` from the dirty primary checkout describes a local-first Flask app with Flask runtime, health/wearables, nutrition, workout/coaching, UI, tests/QA, docs/audits, infra/config, and generated local state layers.
- Graphify artifact: `graphify-out/graph.json` exists in the primary checkout with 3,956 nodes and 0 edges. It is useful for symbol inventory, not relationship proof.

## Baseline State

- Primary checkout `/Users/admin/fitness-dashboard` is dirty and ahead of its remote branch. It also contains untracked local artifacts, including `.whoop-client-id`, `.understand-anything/`, and `graphify-out/`.
- FIT-239 work is isolated in `/Users/admin/codex-worktrees/fitness-fit-239-whoop`, created from current `origin/main`.
- Focused baseline proof in the clean worktree:
  - `python3 -m pytest tests/test_wearable_freshness_contract.py tests/test_freshness.py -q`
  - Result: `29 passed in 0.18s`.

## OAuth And Secret Assumptions

- Local setup exists without exposing values:
  - `.whoop-client-id` exists in the dirty primary checkout with mode `600` and size `36`.
  - macOS Keychain item `fitness-dashboard-whoop-client-secret` is present.
- Neither credential artifact is tracked. The primary checkout does not currently ignore `.whoop-client-id`, so the implementation must add explicit ignore/protection before any credential-adjacent workflow.
- Official WHOOP docs support the FIT-239 assumptions:
  - OAuth needs `offline` scope for refresh tokens.
  - Refreshing tokens returns a new refresh token and invalidates the previous one, so rotation must be atomic.
  - WHOOP collection endpoints are paginated.
  - Rate limits are documented; implementation needs bounded retries/backoff.
  - Webhooks are event notifications and do not replace API data fetches.

## Existing Wearable Patterns

- Freshness is centralized in `app.py` `_compute_data_freshness`, which currently returns `oura`, `apple_health`, and `food`.
- `freshness_only` exposes `/api/freshness` as a side-effect-free Settings endpoint. Settings intentionally avoids `/api/dashboard` because dashboard regeneration mutates server-side workout recommendation state.
- Oura freshness uses `oura_daily.day` as the data date and `created_at` as sync time. This distinction is important for WHOOP because a sync can succeed without fresh scored data.
- Apple Health freshness uses the local Apple Health sync DB and must not treat a recent duplicate-only import attempt as fresh data.
- Open Wearables is best-effort and fetches live from a local bridge via `_get_ow_token`, `_ow_request`, and `fetch_open_wearables_data`. It currently stores only a recommendation marker and should not be the FIT-239 canonical path.

## Recommendation Patterns

- `generate_next_workout` currently calls `_get_oura_readiness_today` and directly reduces volume when readiness is below 60.
- `smart_recommendation_api` combines Oura readiness, HRV trend, soreness, ACWR, workout history, and food state.
- FIT-239 should introduce a normalized wearable modifier layer rather than more raw provider thresholds inside recommendation functions.
- WHOOP should affect recommendations only when data is fresh, scored, and not calibrating; otherwise it should lower confidence or become display-only.

## UI Surfaces

- Dashboard rendering lives in `static/js/app.js` `paintDashboardFromState`, `renderFreshnessChips`, and `renderNextWorkout`.
- Settings rendering lives in `renderSettings`, `renderSettingsGroupSummaries`, and Oura/Apple detail helpers.
- Current UI has source freshness chips and Oura-specific helpers, but not a unified Wearable Sources model.
- FIT-239 needs dashboard source chips, recommendation explanation, source-conflict display, and Settings rows for WHOOP/Oura/Apple Health/optional Noop without hiding current Oura or Apple Health behavior.

## Backup, Export, Import, Delete

- `export_backup` currently exports JSON-backed workout, soreness, cardio, recovery, settings, baselines, body, sleep, nutrition, food logs, meal acceptance/review snapshots, and personal vocabulary.
- `import_backup` restores those JSON-backed datasets and food-related SQLite data.
- WHOOP token material and raw provider payloads must not enter normal backup/export output.
- WHOOP normalized daily facts may be exportable only if explicitly scrubbed of token material and raw payloads; FIT-239 accepts keeping provider-specific raw payloads server-only and excluded.
- Disconnect and delete are separate flows: disconnect revokes/removes connection state and tokens; delete removes local WHOOP-derived data and import batches.

## Test Anchors

- Existing freshness contract tests: `tests/test_wearable_freshness_contract.py`, `tests/test_freshness.py`.
- Existing backup/import tests include `tests/test_backup_import_food_logs.py` and nearby import/export contract tests.
- Existing dashboard JS contract tests include `tests/test_dashboard_render_contract.py`.
- FIT-239 should add focused tests for WHOOP OAuth/config, token refresh, sync status, local persistence, freshness projection, recommendation modifiers/conflicts, CSV validation, backup/export exclusions, disconnect/delete, and UI state contracts.

## Groundtruth Risks

- `app.py` is very large. FIT-239 should create focused helper modules for WHOOP storage/client/recommendation projection where possible, while preserving existing route patterns.
- Open Wearables has token handling and live-fetch patterns, but it is not durable local-first storage. Reusing it as the primary integration would violate the Linear decision.
- WHOOP refresh-token rotation is security-sensitive. A failed write after receiving a new refresh token can strand the connection, so token update must be atomic.
- CSV imports are untrusted input and need strict caps, whitelist parsing, date/numeric bounds, idempotency hashes, and formula-safe echoed values.
- UI QA has a wide state matrix. Backend implementation cannot be called complete without browser proof across desktop/mobile and light/dark states.

## Audit Conclusion

Proceed with direct official WHOOP API as the canonical v1 path, local normalized persistence as the source of dashboard/recommendation truth, and a bounded modifier layer for recommendations. Do not vendor Noop or use Open Wearables as the primary WHOOP runtime path. Keep credential reads local and redacted, exclude token/raw payload material from exports and proof artifacts, and split implementation into disjoint backend, scheduler/ops, frontend, tests, and security review slices.
