# Fitness Dashboard Current State

Status: Snapshot from local inspection  
Last updated: 2026-05-18 local time

## Summary

Fitness Dashboard is a Flask-based, mobile-first personal fitness coaching dashboard running locally on the Mac mini at port 5050. It combines manual workout logging, deterministic workout prescription, Oura recovery data, Apple Health sync data, body-composition tracking, nutrition targets/logs, and a constrained LM Studio AI coach layer.

The current product is functional but still in a draft product state. The app has a real runtime, real data stores, and a substantial mobile UI, but it also has legacy docs/files, a dirty working tree, and several known future-pass items.

## Runtime State Observed

- Project path: `/Users/admin/fitness-dashboard`
- Local listener observed: `127.0.0.1:5050`
- Launchd jobs observed: `com.fitness-dashboard` and `com.fitness-dashboard.staleness`
- Root URL behavior: `GET /` returns `302` to `/login?next=/`
- Apple Health sync status endpoint: unauthenticated request returns `401`
- Apple Health webhook without token: returns `401` with `invalid or missing sync token`
- Python compile check passed for the main Python modules inspected in this run.

Authenticated API smoke tests were not run in this pass because no session cookie was provided.

## Data State Observed

Local files and SQLite stores show active data:

- `data_workouts.json`: 27 workouts, dated from 2025-09-11 through 2026-05-17.
- `data_body.json`: 168 body entries, dated from 2022-10-13 through 2026-03-06.
- `data_settings.json`: training goal is `weight_loss_toning`; equipment preference is `machines_and_cables`; available time is 90 minutes; sessions target is 3 per week.
- `apple_health_sync.db`: 6,394 sync log rows, latest created at 2026-05-17 18:23:39.
- `apple_health_sync.db`: 44 sync event rows, latest created at 2026-05-17 18:23:39.
- `oura_daily.sqlite3`: 86 daily rows, latest day 2026-05-17.
- `oura_daily.sqlite3`: 203 sleep rows, latest day 2026-05-09.
- `auth.db`: 52 user rows.

## Architecture

The app is a Flask single-page PWA with auth, JSON data stores, SQLite caches, wearable integrations, and a local AI coach layer.

Core files:

- `app.py`: Flask routes, deterministic workout engine, recommendation logic, AI apply/analyze routes, metrics, backup/import, history, body, settings, Oura endpoints.
- `auth.py`: login/session handling, single-owner guard, public-prefix allowlist.
- `apple_health_parser.py`: Apple Health file parsing, Health Auto Export webhook ingestion, sync DB reads, status/setup endpoints.
- `oura_client.py` and `oura_sleep_sync.py`: Oura API wrapper, daily cache, sleep sync.
- `lm_studio_adapter.py`: LM Studio health checks, strict JSON schemas, adjust/analyze validators, fallback behavior.
- `templates/index.html`: main 8-tab UI and modals.
- `static/js/app.js`: frontend data loading, tab logic, active workout flow, charts, forms.
- `static/css/style.css`: dark mobile-first analytical UI.

## Current Product Surface

The current UI is an 8-tab mobile-first dashboard:

- Dashboard: readiness, AI recommendation, daily glance, insight cards, charts.
- Vitals: resting heart rate, HRV, sleep, body temp, activity, body metrics.
- Next Workout: recommended session, exercise cards, cardio finisher, start and adjust actions.
- Log: strength, cardio, and recovery forms.
- History: workout frequency, volume, top exercises, recent workouts.
- Body: weight, body fat, trend charts, measurement logging.
- Stats: workout totals, volume, RPE, time, muscle distribution, insights.
- Settings: goals, time/session preferences, equipment, Oura, Apple Health, weather, backup/import.

Modals and interactive states currently include active workout, exercise swap, adjust plan, analyze, Apple Health setup, and toast/error surfaces.

Nutrition exists as backend data and target settings, but the current product surface does not yet provide the desired photo-based food capture flow where a snack or meal photo updates daily calories/macros and coaching context.

Food photo privacy is explicitly backend-first: raw photos are discarded after extraction by default, normal API responses and backups must not expose raw image bytes or raw model traces, and the current non-UI contract is recorded in `docs/FOOD_PHOTO_PRIVACY.md`.

## AI Coach State

The AI coach is intentionally constrained. The deterministic Python engine generates and owns the plan. LM Studio is used for:

- Adjust Plan: returns an intent patch that Python validates and applies.
- Analyze Workout: returns narrative analysis only.
- Auto-analyze: post-completion narrative analysis.

Safety rails documented in the repo include RPE clamp, set-delta clamp, deload and poor-readiness blocks, soreness blacklist, duration cap, cardio drop, invalid JSON fallback, and single-inference locking.

Known AI follow-up items:

- Add side/joint granularity so "left shoulder sore" does not over-block every shoulder-adjacent movement.
- Show better "why nothing changed" copy when the LLM returns an empty intent.
- Surface AI metrics in Settings.
- Warn when fallback rate is high.

## Integration State

### Oura

Oura is wired through local SQLite caches and API endpoints for status, trends, sync, and sleep summary. Current local data shows Oura daily data through 2026-05-17 and sleep rows through 2026-05-09.

### Apple Health

Apple Health supports two paths:

- Health Auto Export webhook into `apple_health_sync.db`.
- Legacy file exports from `~/Documents/Health/healthkit_*.json`.

The current sync database has recent entries from 2026-05-17. The public webhook rejects missing tokens, and the status endpoint is auth-gated.

Health Auto Export setup URL generation should use environment-driven public URL config:

- `FITNESS_DASHBOARD_PUBLIC_BASE_URL=https://<your-public-fitness-dashboard-host>`
- Optional override: `APPLE_HEALTH_WEBHOOK_URL=https://<your-public-health-sync-endpoint>`

Do not hardcode a personal Tailscale hostname in docs or committed config. The owner-only setup route appends the configured `HEALTH_SYNC_TOKEN` to the public sync endpoint it returns.

### LM Studio

The repo documents a primary ASUS GX10 LM Studio route and a Mac mini fallback route. This snapshot did not run authenticated AI health checks, but the Python compile pass succeeded and the route exists at `/api/ai/health`.

## Known Limitations

- The repo has many untracked and modified files, including runtime databases, generated caches, audit bundles, backup files, and source files.
- Authenticated smoke testing requires a valid session cookie.
- Release, restart, rollback, cache-bust, and Apple Health bridge checks are documented in `docs/RELEASE_RUNBOOK.md`.
- Runtime/stale artifact policy is documented in `docs/REPO_HYGIENE.md`; do not delete runtime data during cleanup without explicit approval.
- The app is single-owner by design; public multi-user mode would require per-user data stores.
- Apple Health Health Auto Export ingest derives `record_date` from the ISO timestamp's own timezone offset rather than slicing the first 10 characters.
- If older Apple Health synced rows look misdated after travel, backfill by deleting the affected `health_auto_export` rows for the wrong `record_date` range from `apple_health_sync.db` and replaying the original HAE export payload; the widened `(source, record_type, record_date, record_key)` uniqueness will reinsert them under the corrected local day.
- Side-specific soreness and joint limitations are not modeled yet.
- Nutrition data and targets exist, and accepted food logs now have a SQLite persistence path that preserves final values, sanitized original estimates, confidence/correction metadata, context notes, meal type, and source timestamps. Legacy `data_nutrition.json` remains readable for current dashboard totals and migration/backfill safety.
- There is no finished food photo capture, AI food estimation, confidence/review, or auto-adjustment workflow yet.
- `auth.db` currently has 52 user rows even though the app is documented as single-owner by default; that should be reviewed before any multi-user assumption.

## Verification Performed In This Snapshot

- Read existing handoff, feature, visual review, Oura, Apple Health, and AI TODO docs.
- Inspected route inventory in `app.py`, `auth.py`, `apple_health_parser.py`, and `lm_studio_adapter.py`.
- Checked local process and launchd status for port 5050.
- Checked root login redirect.
- Checked unauthenticated auth gate on Apple Health sync status.
- Checked token gate on Apple Health webhook.
- Counted current local workout, body, Oura, Apple Health, and auth database records.
- Ran Python compile check on the main backend modules.

## Recommended Next Step

Before implementation work, decide which product milestone is next:

1. Trustworthy Daily Brief.
2. Photo Food Logging.
3. Workout Execution Reliability.
4. Progress Loop.
5. Integration Confidence.
6. Product Hardening.

For any UI work, visual QA must cover modals, sheets, detail panels, add/edit forms, delete confirmations, mobile nav overlap, empty states, blocked states, warning states, and mobile workout detail views where values must remain readable.
