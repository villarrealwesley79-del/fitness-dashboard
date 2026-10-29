# Fitness Dashboard Current State

Status: Snapshot from current implementation
Last updated: 2026-10-28 (wearable sections only; food and AI coach sections are not yet swept)

## Summary

Fitness Dashboard is a Flask-based, mobile-first personal fitness coaching web app running locally on the Mac mini at port 5050. It combines manual workout logging, deterministic workout prescription, WHOOP, Oura and Apple Health data (all via the Open Wearables hub), body-composition tracking, nutrition targets/logs, food-photo estimation/review paths, and a constrained LM Studio AI coach layer.

The current product is functional but still in a draft product state. The app has a real runtime, real data stores, and a substantial mobile UI. Wearable data (WHOOP, Oura, Apple Health) reaches the app only through Open Wearables as a local hub wrapper, and the app remains single-owner and local-first by default.

The app has no billing, Stripe, paid entitlement, public landing, pricing, trial, or selling surface. Existing auth databases are migrated in place to remove the retired billing columns while preserving account ids and credentials.

## Runtime State

- Primary development path may be dirty; issue work should use clean worktrees from `origin/main`.
- Live local server path commonly used for `main`: `/Users/admin/fitness-dashboard-fit-222`.
- Local listener: `127.0.0.1:5050`.
- Launchd job: `com.fitness-dashboard`.
- Root URL behavior: `GET /` redirects to `/login?next=/` when no session is present.
- There is no Apple Health webhook and no login-exempt `/api/*` route.
- Open Wearables setup uses the normal browser mutation and auth gates. The web app prepares the local hub profile when the sidecar env is available, gates cloud sign-in on real connector credentials, and creates phone-app invite codes for SDK-style health sources.
- Open Wearables sync returns only redacted metadata and stored fact counts; raw upstream payloads are not exposed in normal responses.

Authenticated browser smoke tests require owner credentials or a valid session cookie.

## Data State

The repo is a sanitized source copy. Runtime data stays local and out of Git:

- JSON data stores such as workouts, soreness, cardio, recovery, settings, body, sleep, and nutrition.
- SQLite stores such as auth, wearable facts, and food logs/review snapshots.
- Archived pre-cutover wearable files (`oura_daily.sqlite3`, `whoop.sqlite3`, `apple_health_sync.db`, `.whoop-*`) are no longer read.
- Health exports, logs, screenshots, backup bundles, and generated proof artifacts.

Committed docs should not quote live private counts or raw health rows unless they are sanitized, durable PR evidence.

## Architecture

The app is a Flask single-page PWA with auth, JSON data stores, SQLite caches, wearable integrations, food-intake flows, and a local AI coach layer.

Core files:

- `app.py`: Flask routes, deterministic workout engine, recommendation logic, AI apply/analyze routes, metrics, backup/import, history, body, settings, Open Wearables, and food-intake endpoints.
- `auth.py`: login/session handling, single-owner guard, public-prefix allowlist.
- `open_wearables_hub.py` and `wearable_fact_store.py`: Open Wearables facts (WHOOP, Oura, Apple Health), the per-domain source preference, and the conservative recommendation guard. `scripts/import_archive_facts.py` imports archived Oura and Apple history read-only.
- `protected_secrets.py`: Keychain and 0600-file storage for the Open Wearables setup password.
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
- Settings: goals, time/session preferences, equipment, Open Wearables, weather, backup/import.

Modals and interactive states include active workout, exercise swap, adjust plan, analyze, Open Wearables add-wearable setup, cloud provider owner-setup states, phone health-source invite-code states, meal review, and toast/error surfaces.

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

### WHOOP, Oura and Apple Health

None of these has a direct path. Each is paired in Open Wearables, and the app stores the normalized facts in `wearable_facts.sqlite3` (`provider_id`, `source_provider`, `original_label`, `observed_at`, `imported_at`). Per domain the preferred provider is plain data in `wearable_fact_store.SOURCE_PREFERENCE`: sleep and recovery prefer Oura, then WHOOP, then Apple; activity prefers WHOOP, then Oura, then Apple. The dashboard shows one `Sources:` line naming the provider behind each domain.

WHOOP recovery reaches the plan only through the Open Wearables guard in `open_wearables_hub.conservative_modifier`: a recovery score under 34 downgrades once, 34 to 66 caps RPE. Workouts and sleep from Apple Health show in History; they do not feed training load.

Archived history from before the cutover is imported once with `scripts/import_archive_facts.py` and shown labelled as archive.

### Open Wearables

Open Wearables is a local wearable hub wrapper plus a best-effort sync bridge. The Settings flow uses `/api/open-wearables/setup/bootstrap` to read the local Open Wearables sidecar env, create or reuse the hub user mapping, and save the local app mapping without exposing the hub secret. Provider tiles are generated from the supported provider catalog and hub state.

Cloud providers such as WHOOP, Oura, Garmin, Strava, Fitbit, Polar, Suunto, and Ultrahuman open provider sign-in only when the Open Wearables connector has non-placeholder credentials. If connector credentials are missing, the UI shows owner setup instead of sending the user to a broken provider page.

Phone health sources such as Apple Health, Samsung Health, and Google Health Connect do not use provider website OAuth. They use `/api/open-wearables/mobile-invite/<provider>` to create an Open Wearables phone-app invitation code and a phone-usable hub URL.

The sync routes remain metadata-only. `/api/open-wearables/check-sync` and `/api/open-wearables/sync` return source, fetch timestamp, counts, stored fact counts, and stable error codes. Raw upstream health payloads, token names, hub secrets, and exception text must not be exposed in normal responses.

### LM Studio

The repo supports primary and fallback LM Studio routes. Authenticated AI health checks require a valid session. The route exists at `/api/ai/health`.

## Known Limitations

- The primary checkout can have many untracked and modified local artifacts; issue work should use clean worktrees from `origin/main`.
- Authenticated smoke testing requires a valid session cookie or owner credentials.
- Release, restart, rollback, and cache-bust checks are documented in `docs/RELEASE_RUNBOOK.md`.
- Runtime/stale artifact policy is documented in `docs/REPO_HYGIENE.md`; do not delete runtime data during cleanup without explicit approval.
- The app is single-owner by design; public multi-user mode would require per-user data stores.
- Side-specific soreness and joint limitations are not modeled yet.
- Nutrition data and targets exist, and accepted food logs have a SQLite persistence path that preserves final values, sanitized original estimates, confidence/correction metadata, context notes, meal type, and source timestamps. Legacy `data_nutrition.json` remains readable for current dashboard totals and migration/backfill safety.
- Food-photo/text/barcode intake and review code exists, including privacy/retention metadata and offline photo queue rules, but the product loop still needs continued UI polish and broad live QA before it should be described as finished.
- `auth.db` may contain more than one local row even though the app is documented as single-owner by default; that should be reviewed before any multi-user assumption.

## Verification Performed In This Snapshot

- Attempted codebase-memory indexing for this worktree; the MCP returned `Transport closed`, so this pass used local repo inspection instead.
- Compared current docs against implementation routes and helpers in `app.py`, `open_wearables_hub.py`, `wearable_fact_store.py`, and food-intake modules.
- Checked current route/test evidence for freshness, recommendations, backup/import, Open Wearables setup bootstrap, provider pairing gates, mobile invitation codes, sync redaction, and food-photo privacy.
- Did not inspect private runtime databases or raw local health payloads for this documentation update.

## Recommended Next Step

Keep product work flowing through focused Linear issues and clean `codex/` worktrees. For any UI work, visual QA must cover modals, sheets, detail panels, add/edit forms, delete confirmations, mobile nav overlap, empty states, blocked states, warning states, and mobile workout detail views where values must remain readable.
