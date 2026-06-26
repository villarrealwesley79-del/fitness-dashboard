# FIT-239 WHOOP Integration Design

## Goal

Add WHOOP as a durable, local-first wearable source for recovery-aware workout and nutrition recommendations without letting WHOOP become an unbounded scoring engine or hide Oura/Apple Health source provenance.

## Scope

This branch implements the FIT-239 v1 path:

- Direct official WHOOP OAuth/API.
- Local normalized persistence for connection metadata, sync runs, recovery, cycles/strain, sleep, workouts, CSV import batches, and daily wearable facts.
- Scheduled/manual sync contracts that do not block dashboard page loads.
- Recommendation modifiers based on fresh scored WHOOP recovery, strain, sleep, and load data.
- Unified Wearable Sources UI across dashboard and settings.
- Backup/export/import/delete/revocation safety.

Out of scope:

- Webhooks as required runtime infrastructure.
- Noop vendoring or BLE/runtime coupling.
- Open Wearables as the primary WHOOP source.
- A separate WHOOP-only recommendation engine.

## Architecture

The implementation adds WHOOP as a provider feeding a normalized wearable-source layer. Provider-specific API and CSV records are stored locally, then projected into daily facts keyed by local date and provider. Dashboard, freshness, vitals, and recommendations read those cached facts instead of fetching WHOOP during page loads.

Server routes stay in `app.py` to match current Flask conventions, but provider logic should live in focused modules:

- `whoop_client.py`: OAuth token exchange/refresh, paginated WHOOP API requests, redacted errors.
- `whoop_store.py`: SQLite schema, token metadata references, sync runs, API records, CSV imports, derived daily facts, disconnect/delete helpers.
- `whoop_recommendations.py`: convert WHOOP facts into bounded modifiers, source explanations, and conflict states.
- `scripts/whoop_sync.py`: CLI/local-worker entry point for scheduled sync and repair sync.

## Data Model

WHOOP storage uses SQLite under the app data directory for health facts, but token material is stored in macOS Keychain by default, or an equivalent out-of-repo secret store with `0600` permissions when Keychain is unavailable. Token material must never be stored in `DATA_DIR`, SQLite health tables, backups, debug bundles, logs, screenshots, PR text, or Linear comments. The store must support:

- Connection metadata: provider, connected_at, last_successful_sync_at, last_error, scopes, token_ref, reauth_required.
- Sync runs: run_id, reason, requested window, status, started_at, completed_at, records_upserted, retryable, redacted_error.
- API records: recovery, cycles, sleep, workouts with provider, upstream_id, score_state, start/end/local_date, upstream_updated_at, imported_at, sync_run_id, raw_payload_retention flag.
- CSV imports: batch_id, filename hash, row count, idempotency hash, imported_at, rejected rows summary.
- Daily facts: provider, local_date, recovery_score, recovery_band, strain, sleep_performance_pct, sleep_need_gap_min, workout_kj, hrv_rmssd, resting_hr, respiratory_rate, spo2, skin_temp, percent_recorded, score_state, freshness, source_kind.

Raw provider payloads are not exported by default. If retained for debugging, retention must be explicit, local-only, and excluded from backup/export/proof artifacts.

## OAuth And Tokens

`POST /api/whoop/connect/start` creates a server-side OAuth state and returns the authorization URL. State is single-use, expires after 10 minutes, is bound to the initiating app user/session where app context exists, and is deleted on successful use or explicit failure. `GET /api/whoop/callback` validates and consumes state before exchanging the code, stores token material in Keychain or the equivalent protected secret store, marks the connection connected, and returns `Cache-Control: no-store`. `offline` scope is required so refresh tokens are issued.

Refresh is atomic: when WHOOP returns a new refresh token, the old token is not discarded until the new token reference and connection metadata are durably committed. Logs and API responses never include codes, states, access tokens, refresh tokens, Authorization headers, bearer strings, callback query strings, or raw provider payloads. Existing request logging must redact `code`, `state`, `token`, `access_token`, `refresh_token`, and Authorization/Bearer-like values before WHOOP callback routes are enabled.

## Sync Contract

`POST /api/whoop/sync` triggers manual sync and returns a sync-run summary. The scheduled path uses `scripts/whoop_sync.py` so launchd/cron can run without browser interaction.

Sync modes:

- Initial backfill: configurable 30-90 day window.
- Normal polling: intended every 2-6 hours.
- Morning stale-check: lightweight dashboard-triggered status check only; no heavy sync on page load.
- Nightly repair: 7-day reconciliation to catch edited sleeps/workouts and `PENDING_SCORE` to `SCORED` transitions.

Collection endpoints must use pagination, bounded timeouts, retryable error classification, and rate-limit-aware backoff.

## Recommendation Behavior

WHOOP contributes modifiers, not replacement recommendations.

Allowed effects:

- Fresh, scored, non-calibrating recovery can reduce or maintain workout ambition.
- Low recovery can dampen volume/intensity and raise recovery nutrition timing.
- High day/cycle strain can dampen intensity, but never upgrades intensity alone.
- Poor sleep performance can reduce ambition.
- Sleep need gap can explain recovery nutrition timing.
- Workout strain, HR zones, and kilojoules can inform fueling/load context.

Guardrails:

- Do not stack WHOOP submetrics as separate penalties on top of recovery.
- Do not override manual soreness, injury notes, very high ACWR, or explicit workout-history fatigue.
- Do not produce eat-less guidance from low recovery alone.
- If WHOOP and Oura differ by more than one readiness band, mark a source conflict and choose the conservative plan.
- Apple Health remains completed-workout/load truth; WHOOP workouts are deduped against Apple Health before contributing load.
- Missing, stale, pending, unscored, or calibrating WHOOP data lowers confidence or becomes display-only, never zero.

## API Contract

New routes:

- `GET /api/whoop/status`
- `POST /api/whoop/connect/start`
- `GET /api/whoop/callback`
- `POST /api/whoop/disconnect`
- `POST /api/whoop/sync`
- `POST /api/whoop/import-csv`
- `GET /api/whoop/imports`
- `GET /api/whoop/recommendation-signals`

Extended routes:

- `/api/freshness` includes `whoop`.
- `/api/dashboard` includes `wearable_sources`, `recommendation_sources`, and WHOOP explanation/conflict state.
- `/api/vitals` includes WHOOP source blocks without replacing Oura/Apple Health data.
- `/api/next-workout` includes source explanations for modifiers applied to the plan.
- `/api/recommendation/smart` includes bounded WHOOP modifiers and source conflict metadata.

## UI Design

The dashboard shows one recommendation with source chips, e.g. `WHOOP · fresh`, `Oura · aging`, `Apple Health · fresh`. It explains source usage in plain language, such as `Using WHOOP for recovery, Apple Health for load`. A recommendation sources drawer/bottom sheet shows used today, conflicts, freshness, and normalized factors/labels only. It must not expose raw provider payloads or raw private health rows in the UI or screenshots.

Settings gets a Wearable Sources section with rows for WHOOP, Oura, Apple Health, and optional Noop import context. WHOOP supports connect, manual sync, disconnect, last successful sync, failure copy, stale/error/no-data, reauth required, pending score, unscorable, calibrating, CSV-only, and source conflict states.

UI must follow the active design contract: restrained status chips only for real data states, normal letter spacing, readable contrast in light/dark, stable responsive dimensions, and mobile proof for details/drawers.

## Privacy And Safety

- Keep client secrets and refresh/access tokens out of `DATA_DIR`, committed files, logs, backups, screenshots, PR text, and Linear comments.
- Store tokens in macOS Keychain or an equivalent out-of-repo `0600` secret store, referenced from local connection metadata by opaque token reference only.
- Redact OAuth codes, state, Authorization headers, bearer strings, webhook signatures, query tokens, callback query strings, and raw payloads.
- CSV imports are untrusted: size cap, row cap, strict column whitelist, UTF-8 only, numeric/date bounds, idempotency hash, and formula escaping for echoed cell values.
- Disconnect and delete are separate idempotent flows. Disconnect first attempts upstream revocation when possible, then always purges local token material and marks the connection disconnected; upstream revocation failure is reported without leaving local tokens active. Delete removes local WHOOP-derived data and import batches without requiring an active connection.
- Backup/export must exclude token material and raw provider payloads. Import must reject inbound token material, token references, and raw provider payload keys rather than silently accepting them.

## Test Strategy

Backend tests cover config missing/present, single-use TTL-bound OAuth state/callback, `Cache-Control: no-store`, request-log redaction, concrete protected token storage, token refresh rotation, client pagination/retry, sync-run persistence, freshness projection, recommendation modifiers, source conflict, Apple Health dedupe, CSV validation, idempotent disconnect/delete, export exclusions, and import rejection of token/raw-payload keys.

Frontend tests cover render contracts for source chips, Settings rows, recommendation source drawer, sync/error/no-data/reauth/pending/calibrating states, and conflict copy. Browser QA covers desktop/mobile, light/dark, real clicks/keyboard paths, Settings row actions, manual sync/disconnect states, recommendation explanations, and source conflict.

## Approval And Scope

This design does not change FIT-239 scope. It makes the Linear description executable by choosing focused modules and keeping the direct WHOOP API path, CSV backfill, local-first storage, bounded recommendation modifiers, and unified source UI intact.
