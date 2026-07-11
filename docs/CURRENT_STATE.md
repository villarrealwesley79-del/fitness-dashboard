# Fitness Dashboard Current State

Status: Snapshot from current implementation
Last updated: 2026-06-27 local time

## Summary

Fitness Dashboard is a Flask-based, mobile-first personal fitness coaching web app running locally on the Mac mini at port 5050. It combines manual workout logging, deterministic workout prescription, Oura recovery data, Apple Health sync data, WHOOP recovery/strain/sleep facts, Open Wearables hub data, body-composition tracking, nutrition targets/logs, food-photo estimation/review paths, and a constrained LM Studio AI coach layer.

The current product is functional but still in a draft product state. The app has a real runtime, real data stores, and a substantial mobile UI. Productized wearable confidence now includes Oura, Apple Health, WHOOP, and Open Wearables as a local hub wrapper, but the app remains single-owner and local-first by default.

## Runtime State

- Primary development path may be dirty; issue work should use clean worktrees from `origin/main`.
- Live local server path commonly used for `main`: `/Users/admin/fitness-dashboard-fit-222`.
- Local listener: `127.0.0.1:5050`.
- Launchd jobs: `com.fitness-dashboard` and `com.fitness-dashboard.staleness`.
- Root URL behavior: `GET /` redirects to `/login?next=/` when no session is present.
- Apple Health sync status is auth-gated.
- Apple Health webhook rejects missing or invalid sync tokens.
- Open Wearables setup uses the normal browser mutation and auth gates. The web app prepares the local hub profile when the sidecar env is available, gates cloud sign-in on real connector credentials, and creates phone-app invite codes for SDK-style health sources.
- Open Wearables sync returns only redacted metadata and stored fact counts; raw upstream payloads are not exposed in normal responses.

Authenticated browser smoke tests require owner credentials or a valid session cookie.

## Data State

The repo is a sanitized source copy. Runtime data stays local and out of Git:

- JSON data stores such as workouts, soreness, cardio, recovery, settings, body, sleep, and nutrition.
- SQLite stores such as auth, Apple Health sync, Oura cache, food logs/review snapshots, and WHOOP.
- Protected WHOOP OAuth material and local client-id files.
- Health exports, logs, screenshots, backup bundles, and generated proof artifacts.

Committed docs should not quote live private counts or raw health rows unless they are sanitized, durable PR evidence.

## Architecture

The app is a Flask single-page PWA with auth, JSON data stores, SQLite caches, wearable integrations, food-intake flows, and a local AI coach layer.

Core files:

- `app.py`: Flask routes, deterministic workout engine, recommendation logic, AI apply/analyze routes, metrics, backup/import, history, body, settings, Oura, Apple Health, WHOOP, Open Wearables, and food-intake endpoints.
- `auth.py`: login/session handling, single-owner guard, public-prefix allowlist.
- `apple_health_parser.py`: Apple Health file parsing, Health Auto Export webhook ingestion, sync DB reads, status/setup endpoints.
- `oura_client.py` and `oura_sleep_sync.py`: Oura API wrapper, daily cache, sleep sync.
- `whoop_client.py`, `whoop_store.py`, and `whoop_recommendations.py`: WHOOP OAuth/API client, protected token-material references, local SQLite storage, normalized daily facts, freshness, and bounded recommendation modifiers.
- `lm_studio_adapter.py`: LM Studio health checks, strict JSON schemas, adjust/analyze validators, fallback behavior.
- `meal_estimate_schema.py`, `local_vision_adapter.py`, `claude_vision_adapter.py`, and food lookup modules: structured food-photo/text/barcode estimation and sanitization helpers.
- `templates/index.html`: main 8-tab UI and modals.
- `static/js/app.js`: frontend data loading, tab logic, active workout flow, charts, forms, Settings integration rows, and meal-review UI.
- `static/css/style.css` and `static/css/app.css`: dark mobile-first analytical UI plus newer scoped integration styling.

## Current Product Surface

The current UI is an 8-tab mobile-first dashboard:

- Dashboard: readiness, AI recommendation, daily glance, insight cards, charts.
- Vitals: resting heart rate, HRV, sleep, body temp, activity, body metrics.
- Next Workout: recommended session, exercise cards, cardio finisher, start and adjust actions.
- Log: strength, cardio, recovery, and food flows.
- History: workout frequency, volume, top exercises, recent workouts.
- Body: weight, body fat, trend charts, measurement logging.
- Stats: workout totals, volume, RPE, time, muscle distribution, insights.
- Settings: goals, time/session preferences, equipment, Oura, Apple Health, WHOOP, Open Wearables, weather, backup/import.

Modals and interactive states include active workout, exercise swap, adjust plan, analyze, Apple Health setup, WHOOP connect/import/sync/disconnect/delete states, Open Wearables add-wearable setup, cloud provider owner-setup states, phone health-source invite-code states, meal review, and toast/error surfaces.

Nutrition has backend persistence, target settings, meal intake/review APIs, accepted food-log storage, sanitized original estimates, barcode/text/photo estimation paths, and dashboard nutrition context. Some product polish remains, but photo/text/barcode food-intake code is no longer just future intent.

Food photo privacy remains backend-first: raw photos are discarded after extraction by default, normal API responses and backups must not expose raw image bytes or raw model traces, and the current contract is recorded in `docs/FOOD_PHOTO_PRIVACY.md`.

## AI Coach State

The AI coach is intentionally constrained. The deterministic Python engine generates and owns the plan. LM Studio is used for:

- Adjust Plan: returns an intent patch that Python validates and applies.
- Analyze Workout: returns narrative analysis only.
- Auto-analyze: post-completion narrative analysis.
- Food-photo/text estimation routes when the configured local provider is available.

Safety rails documented in the repo include RPE clamp, set-delta clamp, deload and poor-readiness blocks, soreness blacklist, duration cap, cardio drop, invalid JSON fallback, and single-inference locking.

Known AI follow-up items:

- Add side/joint granularity so "left shoulder sore" does not over-block every shoulder-adjacent movement.
- Show better "why nothing changed" copy when the LLM returns an empty intent.
- Surface AI metrics in Settings.
- Warn when fallback rate is high.

## Integration State

### Oura

Oura is wired through local SQLite caches and API endpoints for status, trends, sync, and sleep summary.

### Apple Health

Apple Health supports two paths:

- Health Auto Export webhook into `apple_health_sync.db`.
- Legacy file exports from `~/Documents/Health/healthkit_*.json`.

The public webhook rejects missing tokens, and the status endpoint is auth-gated.

Health Auto Export setup URL generation should use environment-driven public URL config:

- `FITNESS_DASHBOARD_PUBLIC_BASE_URL=https://<your-public-fitness-dashboard-host>`
- Optional override: `APPLE_HEALTH_WEBHOOK_URL=https://<your-public-health-sync-endpoint>`

Do not hardcode a personal Tailscale hostname in docs or committed config. The owner-only setup route appends the configured `HEALTH_SYNC_TOKEN` to the public sync endpoint it returns.

### WHOOP

WHOOP is implemented as a local-first wearable source:

- OAuth starts at `POST /api/whoop/connect/start` and callback handling validates single-use state before storing protected token material.
- Manual sync uses `POST /api/whoop/sync`; CSV/import backfill uses `POST /api/whoop/import-csv`.
- WHOOP facts are normalized into `whoop.sqlite3`, projected into `whoop_daily_facts`, and exposed through status, freshness, dashboard, vitals, and recommendation surfaces.
- WHOOP contributes bounded modifiers such as caution, deload, sleep priority, or fuel-up context. It does not replace the deterministic workout engine.
- Stale, unscored, calibrating, missing, or CSV-only data is display/context only or lower confidence rather than a raw source override.
- Backup export includes normalized `whoop_daily_facts` only. Import rejects token material, token references, raw provider payloads, and malformed WHOOP records before mutating local WHOOP data.
- Disconnect and delete are separate idempotent flows: disconnect removes connection/token state, while delete removes local WHOOP-derived data and import history.

### Open Wearables

Open Wearables is a local wearable hub wrapper plus a best-effort sync bridge. The Settings flow uses `/api/open-wearables/setup/bootstrap` to read the local Open Wearables sidecar env, create or reuse the hub user mapping, and save the local app mapping without exposing the hub secret. Provider tiles are generated from the supported provider catalog and hub state.

Cloud providers such as WHOOP, Oura, Garmin, Strava, Fitbit, Polar, Suunto, and Ultrahuman open provider sign-in only when the Open Wearables connector has non-placeholder credentials. If connector credentials are missing, the UI shows owner setup instead of sending the user to a broken provider page.

Phone health sources such as Apple Health, Samsung Health, and Google Health Connect do not use provider website OAuth. They use `/api/open-wearables/mobile-invite/<provider>` to create an Open Wearables phone-app invitation code and a phone-usable hub URL.

The sync routes remain metadata-only. `/api/health/sync` and `/api/open-wearables/sync` return source, fetch timestamp, counts, stored fact counts, and stable error codes. Raw upstream health payloads, token names, hub secrets, and exception text must not be exposed in normal responses.

### Recovering the local account owner

If a local `auth.db` has multiple rows and the wrong account is selected as the
owner, first inspect the loaded job and copy its exact `DATA_DIR` value. Then
run the diagnostic against that database without modifying it:

```bash
launchctl print gui/$(id -u)/com.fitness-dashboard
DATA_DIR=/exact/value/from/the/loaded/job
venv/bin/python support/owner_diagnostic.py --db "$DATA_DIR/auth.db"
```

The diagnostic opens `auth.db` read-only and reports only account IDs,
usernames, the selected owner, and the selection status. It never selects or
prints password hashes, salts, email addresses, or subscription fields. Status
`invalid_configuration` means `FITNESS_DASHBOARD_OWNER_USER_ID` is not an
integer; `configured_user_missing` means that integer does not match a local
account.

To recover the launchd runtime for the current login session, choose the ID for
the intended username from the diagnostic output, then set and verify the
override before restarting the app:

```bash
launchctl setenv FITNESS_DASHBOARD_OWNER_USER_ID 8
OWNER_ID="$(launchctl getenv FITNESS_DASHBOARD_OWNER_USER_ID)"
FITNESS_DASHBOARD_OWNER_USER_ID="$OWNER_ID" venv/bin/python support/owner_diagnostic.py --db "$DATA_DIR/auth.db"
launchctl kickstart -k gui/$(id -u)/com.fitness-dashboard
```

Replace `8` with the intended account ID. Sign in as that account and verify an
owner-only page before considering recovery complete. Do not delete, reorder,
or edit rows in `auth.db`; the override changes owner selection without
changing credentials or local data. Do not restart if the second diagnostic
does not report `status: selected` for the intended account and the launchd
read-back `OWNER_ID`. `launchctl setenv`
applies to the current login session, so reapply it after logout/reboot or put
the same variable in the managed service environment through the normal
deployment process. To return to minimum-ID selection, run `launchctl unsetenv
FITNESS_DASHBOARD_OWNER_USER_ID`, restart the app, and rerun the diagnostic
against the same exact `--db` path.

### LM Studio

The repo supports primary and fallback LM Studio routes. Authenticated AI health checks require a valid session. The route exists at `/api/ai/health`.

## Known Limitations

- The primary checkout can have many untracked and modified local artifacts; issue work should use clean worktrees from `origin/main`.
- Authenticated smoke testing requires a valid session cookie or owner credentials.
- Release, restart, rollback, cache-bust, and Apple Health bridge checks are documented in `docs/RELEASE_RUNBOOK.md`.
- Runtime/stale artifact policy is documented in `docs/REPO_HYGIENE.md`; do not delete runtime data during cleanup without explicit approval.
- The app is single-owner by design; public multi-user mode would require per-user data stores.
- Apple Health Health Auto Export ingest derives `record_date` from the ISO timestamp's own timezone offset rather than slicing the first 10 characters.
- If older Apple Health synced rows look misdated after travel, backfill by deleting the affected `health_auto_export` rows for the wrong `record_date` range from `apple_health_sync.db` and replaying the original HAE export payload; the widened `(source, record_type, record_date, record_key)` uniqueness will reinsert them under the corrected local day.
- Side-specific soreness and joint limitations are not modeled yet.
- Nutrition data and targets exist, and accepted food logs have a SQLite persistence path that preserves final values, sanitized original estimates, confidence/correction metadata, context notes, meal type, and source timestamps. Legacy `data_nutrition.json` remains readable for current dashboard totals and migration/backfill safety.
- Food-photo/text/barcode intake and review code exists, including privacy/retention metadata and offline photo queue rules, but the product loop still needs continued UI polish and broad live QA before it should be described as finished.
- `auth.db` may contain more than one local row even though the app is documented as single-owner by default; that should be reviewed before any multi-user assumption.

## Verification Performed In This Snapshot

- Attempted codebase-memory indexing for this worktree; the MCP returned `Transport closed`, so this pass used local repo inspection instead.
- Compared current docs against implementation routes and helpers in `app.py`, `open_wearables_adapter.py`, `whoop_client.py`, `whoop_store.py`, `whoop_recommendations.py`, `apple_health_parser.py`, and food-intake modules.
- Checked current route/test evidence for WHOOP OAuth, CSV import, freshness, recommendations, backup/import, Open Wearables setup bootstrap, provider pairing gates, mobile invitation codes, sync redaction, and food-photo privacy.
- Did not inspect private runtime databases or raw local health payloads for this documentation update.

## Recommended Next Step

Keep product work flowing through focused Linear issues and clean `codex/` worktrees. For any UI work, visual QA must cover modals, sheets, detail panels, add/edit forms, delete confirmations, mobile nav overlap, empty states, blocked states, warning states, and mobile workout detail views where values must remain readable.
