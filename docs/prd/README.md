# Fitness Dashboard PRD Index

> **Sources:** `README.md`, `docs/VISION.md`, `docs/PRD.md`, `docs/CURRENT_STATE.md`, `app.py`, `auth.py`, `apple_health_parser.py`, `health_ingest.py`, `templates/index.html`, `static/js/app.js`, `static/js/sw.js`
> **Routes:** Master index for all documented app, API, auth, integration, PWA, and dead/legacy route surfaces.
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## System Overview

Fitness Dashboard is a local-first, single-owner Flask coaching app that turns training history, soreness, wearable recovery, Apple Health activity, WHOOP context, Open Wearables facts, nutrition logs, and body-composition data into a daily training decision. The product is not a generic metric viewer; its first job is to help the owner decide what to train today, why, what to avoid, and how food/recovery should change the plan.

The app shell is a server-rendered mobile-first dashboard with eight primary tabs, modal-driven workout execution, meal review, integration setup, and settings. The backend remains the source of truth for deterministic workout recommendation, storage, sync status, food-log policy, backup/import, and integration redaction. AI is used at the edges for constrained plan adjustment, workout analysis, fact-question answers, and local vision/text meal estimation; Python validates and applies any plan-impacting output.

Runtime data is local by default. The public repo intentionally excludes owner data, auth databases, SQLite stores, health exports, provider token material, photos, logs, backups, and generated artifacts. `DATA_DIR` can move local runtime stores out of the repo; otherwise stores live next to the app.

## Architecture At A Glance

- `app.py` - Main Flask app, route surface, workout engine, dashboard aggregation, meal flows, settings, AI coach routes, Open Wearables, WHOOP/Oura wrappers, push, backup/import, and PWA routes.
- `ai_fact_query.py` - Sanitized fact-context question answering and pending suggestion creation for the AI fact modal.
- `apple_health_parser.py` - Apple Health file parsing, Health Auto Export webhook ingestion, sync DB, setup URL, and Apple Health status routes.
- `auth.py` - Flask-Login, single-owner guard, auth DB, login/register/logout, CSRF/origin checks, session config, and public route allowlist.
- `branded_food_lookup.py` - Branded, restaurant, cache, Nutritionix, USDA, Open Food Facts, and product-page lookup orchestration for food estimates.
- `claude_vision_adapter.py` - Optional Anthropic vision estimate adapter; real only when `ANTHROPIC_API_KEY` is configured.
- `data_loader.py` - Legacy workout-log parsing and summary helpers.
- `data_store.py` - SQLite store for body, cardio, nutrition, food logs, review snapshots, vocab, push subscriptions, and workout-adaptation events.
- `health_ingest.py` - Legacy Apple Health JSON export routes for workouts, sleep, steps, vitals, and summary.
- `heb_product_lookup.py` - H-E-B product-page helper for specific packaged-product nutrition lookup.
- `history_normalization.py` - Workout/activity category normalization for history and Apple Health-derived rows.
- `lm_studio_adapter.py` - LM Studio primary/fallback routing, strict JSON schemas, adjust/analyze/swap helpers, and inference locks.
- `local_vision_adapter.py` - Local LM Studio/Ollama vision-food estimator with model routing, schema validation, retries, and keep-warm.
- `meal_estimate_schema.py` - Public meal estimate schema sanitizer and plausible-range validator.
- `meal_log_policy.py` - Meal auto-log versus pending-review policy, confidence bands, and reason codes.
- `meal_text_parser.py` - Text meal parser, branded lookup fallback, LM Studio meal-text estimate, and deterministic presets.
- `nutritionix_client.py` - Nutritionix natural-language and item lookup client.
- `open_food_facts_client.py` - Open Food Facts search/product client with locale and timeout rules.
- `open_wearables_adapter.py` - Open Wearables status normalization, provider payload normalization, base URL validation, and error redaction.
- `oura_client.py` - Oura API client and Oura daily SQLite cache.
- `oura_sleep_sync.py` - Oura sleep SQLite table, sync, latest/range reads, and bedtime-variance helpers.
- `personal_vocab.py` - Personal food vocabulary match thresholds and fuzzy matching.
- `recommendation_sources.py` - Open Wearables recommendation-source projection.
- `runtime_config.py` - `DATA_DIR` path resolution and public base URL normalization.
- `usda_fdc_client.py` - USDA FoodData Central search client.
- `vision_estimator.py` - Vision provider selection and supported provider dispatch.
- `wearable_fact_store.py` - Redacted normalized wearable-fact SQLite store and source metadata.
- `whoop_client.py` - WHOOP OAuth/API client, URL constants, scope config, client material loading, and redacted errors.
- `whoop_recommendations.py` - WHOOP recovery/strain/sleep modifiers, conservative-source conflict handling, and recommendation adjustments.
- `whoop_store.py` - WHOOP SQLite schema, protected token-material reference storage, sync runs, projection to daily facts, freshness, and backup export.
- `workout_adaptation.py` - Nutrition-to-workout adaptation event engine and coalescing rules.

Frontend:

- `templates/index.html` - Main app shell with eight tabs, modal stack, sync banner, and dashboard regions.
- `templates/login.html` - Login/register form rendered by `auth.py`.
- `static/js/app-loader.js` - Starts the async app bundle once the DOM is ready: immediately when `document.readyState` is no longer `loading`, otherwise on one-shot `DOMContentLoaded`, with a cache-bust version.
- `static/js/app.js` - SPA controller: data loading, tab switching, charts, forms, workout modal, offline queues, integration setup, push, AI status, and meal review.
- `static/js/sw.js` - Service worker: no app-shell cache, network-first fetch, offline JSON/HTML failure responses, and low-stakes push display.
- `static/css/style.css`, `static/css/app.css` - Mobile-first dark analytical UI and scoped app styling.

Data locations:

- JSON files through `data_path(...)`: `data_workouts.json`, `data_soreness.json`, `data_settings.json`, `data_cardio.json`, `data_recovery.json`, `data_baselines.json`, `data_body.json`, `data_sleep.json`, `data_nutrition.json`, `open_wearables_config.json`.
- SQLite files through `data_path(...)`: `auth.db`, `fitness_data.db`, `oura_daily.sqlite3`, `whoop.sqlite3`, `wearable_facts.sqlite3`, `apple_health_sync.db`, `ai_coach_cache.sqlite3`.
- Protected/local material: `.flask-secret`, `.health-sync-token`, `.whoop-client-id`, WHOOP protected material under `WHOOP_PROTECTED_MATERIAL_DIR` or a derived protected-material file path.
- Browser-local stores: active workout draft and sync queue in `localStorage`; meal queue metadata/photos in IndexedDB; adjust-intent draft in `sessionStorage`.

## Module Overview Table

| Module | PRD doc link | Core functionality |
| --- | --- | --- |
| Auth and account | [01-auth-and-account.md](01-auth-and-account.md) | Login, registration, owner-only authorization, sessions, CSRF/origin rules, and auth scope. |
| Daily brief dashboard | [02-daily-brief-dashboard.md](02-daily-brief-dashboard.md) | First-screen readiness, recommendation, daily glance, insight cards, food context, freshness chips. |
| Workout planning and execution | [03-workout-planning-execution.md](03-workout-planning-execution.md) | Next workout, active workout modal, swaps, manual logging, completion, history, stats, adherence, progressive overload. |
| Meal logging: text and barcode | [04-meal-logging-text-barcode.md](04-meal-logging-text-barcode.md) | Meal composer text path, barcode lookup, pending review, food logs, personal vocab, refresh events. |
| Photo food logging and vision | [05-photo-food-logging-vision.md](05-photo-food-logging-vision.md) | Multi-photo capture/upload, local VLM estimation, image validation, privacy retention, manual review. |
| Nutrition data sources | [06-nutrition-data-sources.md](06-nutrition-data-sources.md) | Nutritionix, USDA, Open Food Facts, H-E-B, branded lookup cache, nutrition history, source provenance. |
| Oura integration | [07-oura-integration.md](07-oura-integration.md) | Oura daily cache, sleep sync, readiness/trends/status, recommendation recovery source. |
| Apple Health integration | [08-apple-health-integration.md](08-apple-health-integration.md) | Health Auto Export token webhook, setup URL, legacy HealthKit JSON exports, sync freshness. |
| WHOOP integration | [09-whoop-integration.md](09-whoop-integration.md) | WHOOP OAuth, CSV import, protected material, freshness/status, daily facts, recommendation modifiers. |
| Open Wearables integration | [10-open-wearables-integration.md](10-open-wearables-integration.md) | Hub setup, provider catalog, cloud sign-in gates, phone invites, metadata-only sync, normalized facts. |
| AI coach and recommendations | [11-ai-coach-recommendations.md](11-ai-coach-recommendations.md) | LM Studio health/metrics, adjust plan, analyze workout, fact query, suggestion approval/rejection. |
| Push notifications | [12-push-notifications.md](12-push-notifications.md) | Web Push subscription lifecycle, reminder preview, test delivery, service-worker notification display. |
| Data layer and persistence | [14-data-layer-persistence.md](14-data-layer-persistence.md) | JSON/SQLite stores, backup/export/import, local queues, sanitized backup contracts, migrations. |
| Ops and deployment | [15-ops-deployment.md](15-ops-deployment.md) | Local runtime, launchd, environment variables, smoke testing, release/cache-bust behavior, staleness jobs. |
| Progress analytics and body composition | [16-progress-analytics-body.md](16-progress-analytics-body.md) | Body/recomp trends, sleep analytics, vitals, adherence, muscle fatigue, ACWR, progressive overload, weather, and advanced analytics. |

## Page Inventory

| Page or surface | Route / entry | Purpose | Owning PRD |
| --- | --- | --- | --- |
| Dashboard app shell | `GET /` -> `templates/index.html` | Auth-gated main PWA shell. | [02-daily-brief-dashboard.md](02-daily-brief-dashboard.md) |
| AI status popover | `templates/index.html` header, `/api/ai/health`, `/api/ai/metrics` | Shows reachability, model, requests, latency, cache, and fallback state. | [11-ai-coach-recommendations.md](11-ai-coach-recommendations.md) |
| Dashboard tab | `#tab-dashboard` | Readiness gauge, AI recommendation, freshness chips, nutrition macro card, meal composer, pending meals, adaptation banner, daily glance, insight cards, quick trends. | [02-daily-brief-dashboard.md](02-daily-brief-dashboard.md) |
| Meal composer | `#meal-composer` inside Dashboard | Text/photo/barcode capture, offline state, retry, retention notice, pending review list. | [04-meal-logging-text-barcode.md](04-meal-logging-text-barcode.md), [05-photo-food-logging-vision.md](05-photo-food-logging-vision.md) |
| Vitals tab | `#tab-vitals` | RHR, HRV, HR zone, temperature, steps, active minutes/calories, sleep, body metrics. | [02-daily-brief-dashboard.md](02-daily-brief-dashboard.md) |
| Next Workout tab | `#tab-workout` | Recommended workout hero, exercise cards, cardio finisher, start and adjust actions. | [03-workout-planning-execution.md](03-workout-planning-execution.md) |
| Log tab | `#tab-log` | Manual strength, cardio, recovery forms and today's summary. | [03-workout-planning-execution.md](03-workout-planning-execution.md) |
| History tab | `#tab-history` | Range-filtered workout frequency, volume, top exercises, recent workouts, workout detail modal entry points. | [03-workout-planning-execution.md](03-workout-planning-execution.md) |
| Body tab | `#tab-body` | Weight/body-fat cards, food-context interpretation, target progress, 90-day trends, measurements, 14-day nutrition trend, body logging. | [02-daily-brief-dashboard.md](02-daily-brief-dashboard.md), [14-data-layer-persistence.md](14-data-layer-persistence.md), [16-progress-analytics-body.md](16-progress-analytics-body.md) |
| Stats tab | `#tab-stats` | Range-filtered totals, volume, RPE, time, muscle recovery, volume-by-muscle, progress insights. | [03-workout-planning-execution.md](03-workout-planning-execution.md), [16-progress-analytics-body.md](16-progress-analytics-body.md) |
| Settings - coaching setup | `#tab-settings [data-group=coaching-setup]` | Goal, profile, duration, sessions/week, equipment, AI coach status. | [01-auth-and-account.md](01-auth-and-account.md), [03-workout-planning-execution.md](03-workout-planning-execution.md), [11-ai-coach-recommendations.md](11-ai-coach-recommendations.md) |
| Settings - data sources | `#tab-settings [data-group=data-sources]` | Open Wearables, WHOOP fallback, Oura, Apple Health, Weather. | [07-oura-integration.md](07-oura-integration.md), [08-apple-health-integration.md](08-apple-health-integration.md), [09-whoop-integration.md](09-whoop-integration.md), [10-open-wearables-integration.md](10-open-wearables-integration.md) |
| Settings - notifications | `#tab-settings [data-group=notifications]` | Web Push support state, enable/test/disable actions, active alert preview. | [12-push-notifications.md](12-push-notifications.md) |
| Settings - maintenance | `#tab-settings [data-group=maintenance]` | Backup export/import, units, account sign-out. | [14-data-layer-persistence.md](14-data-layer-persistence.md), [01-auth-and-account.md](01-auth-and-account.md) |
| Swap Exercise modal | `#modal-swap` | Same-muscle alternatives and free-text swap resolution. | [03-workout-planning-execution.md](03-workout-planning-execution.md) |
| Recommendation sources modal | `#modal-reco-sources` | Source attribution and wearable conflict details. | [11-ai-coach-recommendations.md](11-ai-coach-recommendations.md) |
| Ask AI modal | `#modal-ai-fact-query` | Sanitized history/wearable question answering plus suggestion approve/reject. | [11-ai-coach-recommendations.md](11-ai-coach-recommendations.md) |
| WHOOP intake modal | `#modal-whoop-intake` | Live OAuth start/fallback link and manual CSV import. | [09-whoop-integration.md](09-whoop-integration.md) |
| Open Wearables setup modal | `#modal-open-wearables-setup` | Prepare hub pairing, provider actions, mobile invite codes, advanced local settings. | [10-open-wearables-integration.md](10-open-wearables-integration.md) |
| Adjust Plan modal | `#modal-adjust` | Plain-language constraint, safety-rail result, preview, discard. | [11-ai-coach-recommendations.md](11-ai-coach-recommendations.md) |
| Analyze Workout modal | `#modal-analyze` | Post-workout AI analysis with summary, wins, concerns, comparison, cue, metadata. | [11-ai-coach-recommendations.md](11-ai-coach-recommendations.md) |
| Workout Detail modal | `#modal-workout-detail` | Workout detail and delete flow. | [03-workout-planning-execution.md](03-workout-planning-execution.md) |
| Meal Detail modal | `#modal-meal-detail` | Food-log detail, correction edit form, delete action. | [04-meal-logging-text-barcode.md](04-meal-logging-text-barcode.md) |
| Food Log modal | `#modal-food-log` | Today, yesterday, recent 14 days, day drilldown. | [04-meal-logging-text-barcode.md](04-meal-logging-text-barcode.md) |
| Delete confirmation modal | `#modal-delete-confirm` | Workout delete confirmation. | [03-workout-planning-execution.md](03-workout-planning-execution.md) |
| Apple Health setup modal | `#modal-apple` | Tokenized Health Auto Export setup URL and sync status. | [08-apple-health-integration.md](08-apple-health-integration.md) |
| Active Workout modal | `#modal-active` | Live set logging, swap/adjust, complete workout. | [03-workout-planning-execution.md](03-workout-planning-execution.md) |
| Emergency gym view | `GET /gym-now` | Emergency no-app-shell workout view for stale mobile/PWA caches. | [03-workout-planning-execution.md](03-workout-planning-execution.md) |
| Workout Saved modal | `#modal-workout-saved` | Completion summary, queued state, analyze action. | [03-workout-planning-execution.md](03-workout-planning-execution.md) |
| Pending Sync modal/banner | `#modal-sync-queue`, `#sync-banner` | Offline workout and meal queue status, retry/discard actions. | [14-data-layer-persistence.md](14-data-layer-persistence.md) |
| Login/Register | `GET/POST /login`, `GET/POST /register` | Public auth forms. Registration is blocked after first user in single-owner mode. | [01-auth-and-account.md](01-auth-and-account.md) |
| Logout | `GET /logout` | Ends owner session. | [01-auth-and-account.md](01-auth-and-account.md) |
| PWA manifest | `GET /manifest.json` | Public install metadata generated from `app.py`. | [15-ops-deployment.md](15-ops-deployment.md) |
| Service worker | `GET /sw.js`, `static/js/sw.js` | Public service worker with network-first app behavior and push notification display. | [15-ops-deployment.md](15-ops-deployment.md), [12-push-notifications.md](12-push-notifications.md) |
| Test chart page | `GET /test-chart` | Auth-gated chart test page; appears legacy/dev-only. | [15-ops-deployment.md](15-ops-deployment.md) |

## Full Endpoint Inventory Summary

See [appendix/api-inventory.md](appendix/api-inventory.md) for the route-by-route reference. See also [appendix/enum-dictionary.md](appendix/enum-dictionary.md) and [appendix/page-relationships.md](appendix/page-relationships.md). This run documented 114 appendix rows covering 113 unique routes:

| Feature area | Endpoint count |
| --- | ---: |
| Auth/account and public/PWA shell | 7 |
| Daily brief, vitals, freshness, weather | 10 |
| Workout planning, execution, history, stats | 22 |
| Meal logging, nutrition, and adaptation events | 14 |
| Body, sleep, baselines, analytics | 5 |
| AI coach and facts | 8 |
| Oura | 4 |
| Apple Health and legacy HealthKit | 14 |
| WHOOP | 9 |
| Open Wearables and wearable facts | 11 |
| Push notifications | 5 |
| Backup/import/export | 3 |
| Dev/legacy route | 1 |

## Global Notes

Permission model:

- Most app and API routes are protected by Flask-Login and, in default single-owner mode, by owner-only user-id enforcement.
- Public route allowlist includes login/register/logout, manifest, service worker, static assets, robots/sitemap, and the exact Apple Health sync webhook path.
- Mutating browser requests must pass same-origin browser metadata, a valid form CSRF token, or `X-Requested-With: XMLHttpRequest`.
- `/api/apple-health/sync` is token-authenticated by `HEALTH_SYNC_TOKEN` via `X-Sync-Token` or `?token=...`, and is exempt from session and CSRF because it is called by Health Auto Export/Shortcuts.
- `app.py` adds permissive CORS headers globally. Browser session/auth still gate protected routes, but the policy should be reviewed before any public multi-user deployment.

Common interaction patterns:

- Initial app boot renders only the hash-selected tab. Dashboard is the default and fetches dashboard, Oura status, smart recommendation, Oura sleep summary, Oura trends, and history-all; other tab data lazy-loads on first switch. Core dashboard fetches use a 30-second timeout.
- AI status refreshes every 60 seconds after boot.
- Food-log refresh events and workout-adaptation events are pollable feeds with explicit ack endpoints; the UI suppresses already-seen event IDs client-side to avoid duplicate banners.
- WHOOP OAuth status polls while the popup/authorization flow is pending and expires the pending marker after 5 minutes.
- Offline workout queue lives in `localStorage` under `fit51:sync-queue:v1`; retryable statuses are `pending` and `auth_required`.
- Offline meal queue lives in IndexedDB stores `queued_meals` and `meal_photos`; retryable statuses are `pending` and `auth_required`, with raw photos kept in IndexedDB rather than `localStorage`.
- Service worker fetch is network-first and does not cache HTML, JS, CSS, or API responses. Offline API calls return 503 JSON; offline navigation returns a simple 503 HTML page.

Data-freshness philosophy:

- Freshness is evidence-based rather than connection-label based. A source is only trustworthy when recent accepted data exists.
- App-level freshness states use `fresh`, `aging`, `stale`, or `missing`; the current app constants classify under 24 hours as fresh, 24-48 hours as aging, and over 48 hours as stale.
- Stale or missing wearable data lowers recommendation confidence rather than pretending the plan is fully informed.
- WHOOP scored/fresh data can apply bounded modifiers; stale, CSV-only, pending-score, calibrating, or unscored WHOOP data stays display-only or lower confidence.
- Food estimates affect coaching after backend policy accepts them or the owner accepts/corrects pending review. Low/medium confidence, ambiguous, missing-calorie, or implausible estimates remain pending.
