# API Inventory

> **Sources:** `app.py`, `auth.py`, `apple_health_parser.py`, `health_ingest.py`, `stripe_checkout.py`, route inventory at `/private/tmp/claude-501/-Users-admin-fitness-dashboard--claude-worktrees-dazzling-golick-7cd594/2a68f015-a5ff-4689-823e-ec2c0ee13b04/scratchpad/routes.txt`
> **Routes:** All `app.py` routes plus registered auth/Apple Health/legacy HealthKit routes and declared Stripe routes.
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

Auth values used below:

- `session/owner`: Requires Flask session and owner user in default single-owner mode.
- `public`: Allowlisted before login.
- `token`: External caller authenticated by a shared token instead of browser session.
- `webhook`: External signed webhook. Stripe route is declared in `stripe_checkout.py` but its blueprint is never registered, so the route 404s in the running app.
- Mutating requests (`POST`/`PUT`/`PATCH`/`DELETE`) require `X-Requested-With: XMLHttpRequest`, a valid form CSRF token, or same-origin browser headers; cross-origin browser posts are rejected. `/webhook` and `/api/apple-health/sync` are exempt.
- `dead/legacy`: Route or page exists in code/templates but appears dev-only, legacy, or not wired.

## App Shell, Auth, Account, Public, PWA

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| GET | `/` | session/owner | Render the main dashboard shell. | [../02-daily-brief-dashboard.md](../02-daily-brief-dashboard.md) |
| GET | `/api/auth/scope` | session/owner | Return the current auth-scope fingerprint used by offline/draft sync guards. | [../01-auth-and-account.md](../01-auth-and-account.md) |
| GET/POST | `/login` | public | Render login and authenticate owner credentials; rate-limited per IP. | [../01-auth-and-account.md](../01-auth-and-account.md) |
| GET/POST | `/register` | public | Create first account; blocked after one user in single-owner mode. | [../01-auth-and-account.md](../01-auth-and-account.md) |
| GET | `/logout` | session | End session and return to login; owner check bypassed by public allowlist, while `@login_required` still requires any authenticated user. | [../01-auth-and-account.md](../01-auth-and-account.md) |
| GET | `/manifest.json` | public | Return PWA manifest metadata. | [../15-ops-deployment.md](../15-ops-deployment.md) |
| GET | `/sw.js` | public | Serve service worker with no-cache/network-first app policy. | [../15-ops-deployment.md](../15-ops-deployment.md) |
| GET | `/pricing` | public/dead | Stripe pricing template route declared in `stripe_checkout.py`; blueprint defined but never registered, routes 404. | [../13-billing-stripe-landing.md](../13-billing-stripe-landing.md) |
| POST | `/create-checkout-session` | session/owner/dead | Start Stripe Checkout session; blueprint defined but never registered, routes 404. | [../13-billing-stripe-landing.md](../13-billing-stripe-landing.md) |
| GET | `/success` | public/dead | Checkout success page; blueprint defined but never registered, routes 404. | [../13-billing-stripe-landing.md](../13-billing-stripe-landing.md) |
| GET | `/cancel` | public/dead | Checkout cancel page; blueprint defined but never registered, routes 404. | [../13-billing-stripe-landing.md](../13-billing-stripe-landing.md) |
| POST | `/webhook` | webhook/dead | Stripe webhook updates Pro status; blueprint defined but never registered, routes 404. | [../13-billing-stripe-landing.md](../13-billing-stripe-landing.md) |

## Daily Brief, Vitals, Freshness, Weather

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| GET | `/api/dashboard` | session/owner | Aggregate daily brief, readiness, next workout, vitals, food context, and source state. | [../02-daily-brief-dashboard.md](../02-daily-brief-dashboard.md) |
| GET | `/api/vitals` | session/owner | Return vitals/activity/body values for the Vitals tab. | [../02-daily-brief-dashboard.md](../02-daily-brief-dashboard.md) |
| GET | `/api/acwr` | session/owner | Return acute:chronic workload ratio context. | [../02-daily-brief-dashboard.md](../02-daily-brief-dashboard.md) |
| GET | `/api/weather` | session/owner | Fetch/cache weather summary from wttr.in for Settings/data context. | [../02-daily-brief-dashboard.md](../02-daily-brief-dashboard.md) |
| GET | `/api/freshness` | session/owner | Return side-effect-free source freshness for Oura, WHOOP, Apple Health, and food. | [../02-daily-brief-dashboard.md](../02-daily-brief-dashboard.md) |
| GET | `/api/insights` | session/owner | Return progress/key insight cards. | [../02-daily-brief-dashboard.md](../02-daily-brief-dashboard.md) |
| GET | `/api/adherence` | session/owner | Return workout/food adherence summary. | [../02-daily-brief-dashboard.md](../02-daily-brief-dashboard.md) |
| GET | `/api/muscle-fatigue` | session/owner | Return muscle recovery/fatigue groups used by Stats and recommendation. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/api/body-recomp` | session/owner | Return body recomposition target/progress and food-context interpretation. | [../02-daily-brief-dashboard.md](../02-daily-brief-dashboard.md) |
| GET | `/api/analytics/advanced` | session/owner | Return advanced analytics bundle for Stats/Settings. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |

## Workout Planning, Execution, History, Stats

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| GET | `/api/next-workout` | session/owner | Generate/return deterministic next workout with auth scope. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/gym-now` | session/owner | Render emergency no-app-shell HTML workout view for stale mobile/PWA caches (no-store). | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| POST | `/api/add-workout` | session/owner | Append a manual strength workout/log row. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| POST | `/api/add-soreness` | session/owner | Add soreness note used by recommendation avoid logic. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| POST | `/api/add-cardio` | session/owner | Add manual cardio entry. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| POST | `/api/add-recovery` | session/owner | Add recovery protocol entry. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/api/exercises` | session/owner | Return exercise library for forms and workout cards. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/api/protocols` | session/owner | Return recovery protocol options. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/api/exercises/alternatives/<muscle_group>` | session/owner | Return swap alternatives by muscle group. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| POST | `/api/workout/swap` | session/owner | Resolve and apply exercise swap, including free-text match path. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/api/recommendation/smart` | session/owner | Return smart recommendation with wearable/food modifiers and source attribution. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |
| GET | `/api/progressive-overload` | session/owner | Return progressive-overload recommendation context. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/api/history` | session/owner | Return limited/canonical workout history. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/api/history-all` | session/owner | Return all history rows, including manual and synced activity projections. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| POST | `/api/delete-history` | session/owner | Delete a history item by type/index with restore payload support. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| POST | `/api/restore-history` | session/owner | Restore a deleted history item by appending original payload. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| POST | `/api/complete-workout` | session/owner | Save active workout completion, calculate adherence, update history. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET/POST | `/api/baselines` | session/owner | Read/update baseline strength values. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/api/settings` | session/owner | Read/write broad settings used by planning and integrations. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| POST | `/api/settings` | session/owner | Persist settings updates after validation/default merge. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| PUT | `/api/settings/equipment` | session/owner | Update equipment preference. | [../03-workout-planning-execution.md](../03-workout-planning-execution.md) |
| GET | `/api/personal-vocab` | session/owner | List personal food vocabulary entries. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| DELETE | `/api/personal-vocab/<path:normalized_input>` | session/owner | Delete one personal vocab entry. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |

## Meal Logging, Nutrition, Adaptation Events

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| POST | `/api/add-nutrition` | session/owner | Add/correct accepted nutrition row and food-log entry. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| POST | `/api/meal-intake` | session/owner | Estimate/log a meal from text and/or photos; can return logged or pending review. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md), [../05-photo-food-logging-vision.md](../05-photo-food-logging-vision.md) |
| POST | `/api/meal-intake/barcode` | session/owner | Resolve barcode to nutrition estimate or manual-review pending source. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| GET | `/api/meal-intake/pending` | session/owner | Hydrate persisted pending meal review snapshots/rows. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| POST | `/api/meal-intake/<meal_id>/refresh` | session/owner | Apply meal-review v2 actions: add item, edit portion, choose candidate, follow-up, skip/delete/restore, set meal type. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| DELETE | `/api/meal-intake/<client_id>` | session/owner | Discard pending/logged meal by client id; used for undo/delete. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| POST | `/api/meal-intake/<client_id>/accept` | session/owner | Accept/correct a pending meal and persist final food log(s). | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| GET | `/api/nutrition-today` | session/owner | Return today's accepted/pending nutrition summary. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| GET | `/api/nutrition-history` | session/owner | Return date-bucketed nutrition history and target adherence. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| GET | `/api/food-logs/by-date/<date>` | session/owner | Return food logs for a selected local date. | [../04-meal-logging-text-barcode.md](../04-meal-logging-text-barcode.md) |
| GET | `/api/food-log-refresh-events` | session/owner | Poll food-log refresh event feed. | [../06-nutrition-data-sources.md](../06-nutrition-data-sources.md) |
| POST | `/api/food-log-refresh-events/<event_id>/ack` | session/owner | Acknowledge food-log refresh event. | [../06-nutrition-data-sources.md](../06-nutrition-data-sources.md) |
| GET | `/api/workout-adaptation-events` | session/owner | Poll nutrition-to-workout adaptation events. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |
| POST | `/api/workout-adaptation-events/<event_id>/ack` | session/owner | Acknowledge workout adaptation event. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |

## Body, Sleep, Baselines

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| POST | `/api/add-body-measurement` | session/owner | Add body measurement row. | [../14-data-layer-persistence.md](../14-data-layer-persistence.md) |
| GET | `/api/body-history` | session/owner | Return body-composition history. | [../14-data-layer-persistence.md](../14-data-layer-persistence.md) |
| POST | `/api/body/navy-calc` | session/owner | Calculate body-fat estimate from Navy tape fields. | [../14-data-layer-persistence.md](../14-data-layer-persistence.md) |
| POST | `/api/sleep/import` | session/owner | Import manual/local sleep data. | [../14-data-layer-persistence.md](../14-data-layer-persistence.md) |
| GET | `/api/sleep/analytics` | session/owner | Return sleep analytics. | [../14-data-layer-persistence.md](../14-data-layer-persistence.md) |

## AI Coach, Health, Facts

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| POST | `/api/workout/adjust` | session/owner | Ask LM Studio for an intent patch and apply validated safety rails. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |
| POST | `/api/workout/analyze` | session/owner | Ask LM Studio for narrative workout analysis; does not mutate the plan. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |
| GET | `/api/ai/health` | session/owner | Return LM Studio primary/fallback reachability and model status. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |
| GET | `/api/ai/metrics` | session/owner | Return AI request/fallback/cache/latency metrics. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |
| GET | `/api/ai/facts/context` | session/owner | Return sanitized fact context for the AI fact modal. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |
| POST | `/api/ai/facts/query` | session/owner | Answer a sanitized question and optionally create a pending suggestion. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |
| POST | `/api/ai/suggestions/<suggestion_id>/approve` | session/owner | Approve a pending AI suggestion. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |
| POST | `/api/ai/suggestions/<suggestion_id>/reject` | session/owner | Reject a pending AI suggestion without changing records. | [../11-ai-coach-recommendations.md](../11-ai-coach-recommendations.md) |

## Oura

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| GET | `/api/oura/status` | session/owner | Return Oura daily readiness/activity status, cached/live source, and latest day. | [../07-oura-integration.md](../07-oura-integration.md) |
| GET | `/api/oura/trends` | session/owner | Return Oura trend windows. | [../07-oura-integration.md](../07-oura-integration.md) |
| POST | `/api/oura/sync-sleep` | session/owner | Sync Oura sleep into local SQLite cache. | [../07-oura-integration.md](../07-oura-integration.md) |
| GET | `/api/oura/sleep-summary` | session/owner | Return latest Oura sleep summary and bedtime variance. | [../07-oura-integration.md](../07-oura-integration.md) |

## Apple Health And Legacy HealthKit

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| GET | `/api/apple-health/summary` | session/owner | Return merged file-export and HAE summary. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/apple-health/workouts` | session/owner | Return merged/deduped Apple Health workouts, optional `days`. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/apple-health/sleep` | session/owner | Return merged Apple Health sleep records, optional `days`. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/apple-health/steps` | session/owner | Return merged Apple Health steps series, optional `days`. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/apple-health/vitals` | session/owner | Return merged RHR/HRV series, optional `days`. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| POST | `/api/apple-health/sync` | token | Accept Health Auto Export/Shortcuts JSON, normalize, dedupe, and persist sync rows. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/apple-health/sync/status` | session/owner | Return sync DB counts, last accepted, last attempt, and setup flags. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/apple-health/sync/setup-url` | session/owner | Return tokenized webhook URL for owner setup UI. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/apple-health/status` | session/owner | Return Apple Health availability and file-count status. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/health/workouts` | session/owner | Legacy file-export workouts endpoint. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/health/sleep` | session/owner | Legacy file-export sleep endpoint. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/health/steps` | session/owner | Legacy file-export steps endpoint. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/health/vitals` | session/owner | Legacy file-export vitals endpoint. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |
| GET | `/api/health/summary` | session/owner | Legacy file-export summary endpoint. | [../08-apple-health-integration.md](../08-apple-health-integration.md) |

## WHOOP

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| GET | `/api/whoop/status` | session/owner | Return WHOOP connection, freshness, score state, conflict, and status summary. | [../09-whoop-integration.md](../09-whoop-integration.md) |
| POST | `/api/whoop/connect/start` | session/owner | Create OAuth state and return WHOOP authorization URL. | [../09-whoop-integration.md](../09-whoop-integration.md) |
| GET | `/api/whoop/callback` | session/owner | Consume OAuth callback, validate state, store protected token material, and trigger projection. | [../09-whoop-integration.md](../09-whoop-integration.md) |
| POST | `/api/whoop/disconnect` | session/owner | Revoke/disconnect token state without deleting local WHOOP data. | [../09-whoop-integration.md](../09-whoop-integration.md) |
| POST | `/api/whoop/delete-data` | session/owner | Delete local WHOOP-derived data and import history. | [../09-whoop-integration.md](../09-whoop-integration.md) |
| POST | `/api/whoop/sync` | session/owner | Sync WHOOP API records into local SQLite, guarded by lock. | [../09-whoop-integration.md](../09-whoop-integration.md) |
| POST | `/api/whoop/import-csv` | session/owner | Import bounded UTF-8 WHOOP CSV/export rows. | [../09-whoop-integration.md](../09-whoop-integration.md) |
| GET | `/api/whoop/imports` | session/owner | List recent manual CSV import runs. | [../09-whoop-integration.md](../09-whoop-integration.md) |
| GET | `/api/whoop/recommendation-signals` | session/owner | Return WHOOP modifier signals used by recommendation. | [../09-whoop-integration.md](../09-whoop-integration.md) |

## Open Wearables And Wearable Facts

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| POST | `/api/health/sync` | session/owner | Manually pull Open Wearables data and return redacted metadata only. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| GET | `/api/open-wearables/status` | session/owner | Return local Open Wearables hub/setup/provider status. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| GET/POST | `/api/open-wearables/setup` | session/owner | Read/save advanced local hub mapping and setup values. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| POST | `/api/open-wearables/setup/bootstrap` | session/owner | Prepare local hub profile from sidecar env and owner mapping. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| GET/POST | `/api/open-wearables/pair/<provider>` | session/owner | Start provider pairing; cloud providers require connector credentials, SDK providers route to mobile invite. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| POST | `/api/open-wearables/mobile-invite/<provider>` | session/owner | Create phone-app invite code for Apple/Samsung/Google health sources. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| POST | `/api/open-wearables/setup/check` | session/owner | Verify hub setup/connection state. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| GET | `/api/open-wearables/providers` | session/owner | Return supported provider catalog/action metadata. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| POST | `/api/open-wearables/sync` | session/owner | Sync Open Wearables metadata/facts into redacted local fact store. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| GET | `/api/wearable-sources` | session/owner | Return normalized wearable source list. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |
| GET | `/api/wearable-facts` | session/owner | Return normalized daily wearable facts; raw payloads are forbidden. | [../10-open-wearables-integration.md](../10-open-wearables-integration.md) |

## Push Notifications

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| GET | `/api/push/vapid-public-key` | session/owner | Return public VAPID key or 404 if push is not configured. | [../12-push-notifications.md](../12-push-notifications.md) |
| GET/POST | `/api/push/subscriptions` | session/owner | List active/revoked subscriptions or save current browser subscription. | [../12-push-notifications.md](../12-push-notifications.md) |
| DELETE | `/api/push/subscriptions/<endpoint_hash>` | session/owner | Revoke one subscription by SHA-256 endpoint hash. | [../12-push-notifications.md](../12-push-notifications.md) |
| POST | `/api/push/test` | session/owner | Send test push to one active subscription. | [../12-push-notifications.md](../12-push-notifications.md) |
| GET | `/api/push/reminders/preview` | session/owner | Preview deterministic low-stakes reminders and push support state. | [../12-push-notifications.md](../12-push-notifications.md) |

## Backup, Export, Dev/Legacy

| Method(s) | Path | Auth | Purpose | Owning PRD |
| --- | --- | --- | --- | --- |
| GET | `/api/export-backup` | session/owner | Download JSON backup with JSON stores, food logs, vocab, meal review, and sanitized WHOOP daily facts. | [../14-data-layer-persistence.md](../14-data-layer-persistence.md) |
| POST | `/api/import-backup` | session/owner | Restore JSON backup; rejects forbidden WHOOP material and malformed records. | [../14-data-layer-persistence.md](../14-data-layer-persistence.md) |
| GET | `/api/export-md` | session/owner | Export markdown summary. | [../14-data-layer-persistence.md](../14-data-layer-persistence.md) |
| GET | `/test-chart` | session/owner/dead | Chart smoke/test page; appears developer-only. | [../15-ops-deployment.md](../15-ops-deployment.md) |

This appendix is an inventory reference and intentionally has no `### IC-n` issue-candidate section; issue candidates live in the owning PRD files.
