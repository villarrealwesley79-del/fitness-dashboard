# Data Layer & Persistence — PRD

> **Sources:** README.md; docs/VISION.md; docs/PRD.md; docs/CURRENT_STATE.md; app.py; data_store.py; data_loader.py; history_normalization.py; runtime_config.py; wearable_fact_store.py; whoop_store.py; tests/test_data_store_connection_lifecycle.py; tests/test_data_store_sodium.py; tests/test_fit183_runtime_paths.py; docs/FIT30_SYNTHETIC_HISTORY_CLEANUP.md; scripts/remove_synthetic_history_rows.py
> **Routes:** GET /api/export-backup; POST /api/import-backup; GET /api/export-md; all feature routes that read/write JSON or SQLite stores indirectly
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

The data layer is a local-first persistence system for a single-owner Flask fitness app. Runtime data lives under `DATA_DIR` as a mix of JSON files and SQLite databases. The public repository is sanitized; real owner data, auth databases, wearable caches, food logs, and secrets are runtime artifacts, not tracked source files.

The system uses JSON files for long-lived app-native history such as workouts, soreness, cardio, recovery, settings, body, sleep, nutrition, and baselines. It uses SQLite for structured local data that needs uniqueness, indexes, idempotency, caching, or multi-step contracts: food logs, meal review state, workout adaptation events, Oura/Apple Health/WHOOP caches, wearable facts, auth, push subscriptions, and AI coach cache/metrics.

This PRD is the data contract other feature PRDs should link to. It defines path resolution, store ownership, schemas, freshness/persistence behavior, backup/import boundaries, synthetic-history cleanup, and known multi-worker/global-state risks. It does not define UI rendering or individual feature workflows except where persistence affects product behavior.

## 2. User-Facing Surfaces

The data layer has few direct screens, but it powers nearly every surface:

| Surface | Data-layer role |
| --- | --- |
| Dashboard and workout tab | Read JSON history/settings, wearable SQLite caches, nutrition/food logs, recommendation cache globals, and adaptation events. |
| History views | Read normalized workout/cardio/recovery/body/nutrition records from JSON and SQLite. |
| Food review and meal logging | Persist food logs, original sanitized estimates, meal snapshots, refresh events, personal vocabulary, lookup caches, and adaptation windows. |
| Settings and integration panels | Read/write auth-gated settings, wearable connection state, Open Wearables config, freshness, push subscriptions, and public base URL derived links. |
| Backup export/import | Export JSON stores plus selected SQLite stores; import restores JSON and selected structured tables. |
| AI coach surfaces | Persist adjust/analyze cache and metrics in SQLite; rely on local history and wearable fact stores for context. |
| Admin/debug smoke tests | Depend on `DATA_DIR` isolation, authenticated session cookies, and route-level persistence paths. |

Backend-only consumers include recommendation generation, AI fact context, Apple Health sync, WHOOP sync/import, Open Wearables fact sync, and push alerts. `delete_user_data` exists as an unwired helper used by tests; it deletes only the documented user-scoped rows in `fitness_data.db`, not an account or all local data. No user-facing account-data deletion flow currently exists. The full boundary, manual owner procedure, and read-only inventory are in [Local data deletion boundaries](../LOCAL_DATA_DELETION.md).

## 3. Field Inventory

### Runtime path configuration

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `APP_DIR` | path | Yes | Directory containing `runtime_config.py` | Derived | Source checkout root. |
| `DATA_DIR` | path | Yes | `APP_DIR` | `DATA_DIR` env stripped; directory created by `data_path` | Root for runtime JSON, SQLite, caches, and local config. |
| `FITNESS_DASHBOARD_PUBLIC_BASE_URL` | URL string | No | derived from request | Adds https if scheme missing; `.ts.net` hosts forced to https | Canonical URL for external callbacks, push, and Apple Health links. |
| `APPLE_HEALTH_SYNC_DB` | path | No | `DATA_DIR/apple_health_sync.db` | Env override | Apple Health sync database path. |
| `WHOOP_PROTECTED_MATERIAL_DIR` | path | No | `~/Library/Application Support/Fitness Dashboard/secrets` | Directory created; file chmod 600 | Location for protected WHOOP token material. |

### JSON stores

| Store | File | Type | Required | Default | Business meaning |
| --- | --- | --- | --- | --- | --- |
| Workouts | `data_workouts.json` | list | Yes | [] | Logged workouts and generated/backfilled workout ids. |
| Soreness | `data_soreness.json` | list | Yes | [] | Soreness entries used by readiness and avoid-list logic. |
| Settings | `data_settings.json` | object | Yes | app defaults | Owner preferences, goals, targets, equipment, volume landmarks. |
| Cardio | `data_cardio.json` | list | Yes | [] | Cardio sessions and cardio rotation/fatigue context. |
| Recovery | `data_recovery.json` | list | Yes | [] | Recovery sessions used for recovery bonus. |
| Baselines | `data_baselines.json` | object (dict) | Yes | `{}` | Historical baselines; exact shape is feature-specific. |
| Body | `data_body.json` | list | Yes | [] | Body weight/body composition history. |
| Sleep | `data_sleep.json` | list | Yes | [] | Local sleep records outside wearable caches. |
| Nutrition | `data_nutrition.json` | list | Yes | [] | Legacy daily nutrition records; food logs now preferred for accepted item-level context. |
| Open Wearables config | `open_wearables_config.json` | object | No | missing config | Local hub setup and provider status configuration. |

`load_json` returns the supplied default when a file is missing. If a file contains malformed JSON, it is renamed to `.corrupt-<timestamp>.json` and recreated from the default. `save_json` writes to a unique temporary file in the same directory, fsyncs, and atomically replaces the target under `JSON_DATA_LOCK`.

### Core SQLite stores

| Store | File | Owner | Business meaning |
| --- | --- | --- | --- |
| Fitness data | `fitness_data.db` | `data_store.py` | Food logs, structured body/cardio/nutrition/recovery data, meal review, adaptation events, lookup caches, push subscriptions, settings table. |
| Auth | `auth.db` | `auth.py` | Local users, password hashes, owner guard, Stripe ids. |
| Oura cache | `oura_daily.sqlite3` | Oura integration | Oura daily readiness/sleep/HRV cache. |
| Apple Health sync | `apple_health_sync.db` or env override | Apple Health integration | Health Auto Export records/events used for freshness and load. |
| WHOOP | `whoop.sqlite3` | `whoop_store.py` | WHOOP connection, OAuth states, records, daily facts, sync runs. |
| Wearable facts | `wearable_facts.sqlite3` | `wearable_fact_store.py` | Public coaching-safe daily facts and wearable sources, profile-scoped. |
| AI coach cache | `ai_coach_cache.sqlite3` | app AI coach routes | Adjust/analyze cache and reliability metrics. |

### `fitness_data.db` table inventory

| Table | Key fields | Business meaning |
| --- | --- | --- |
| `body_data` | `user_id`, `date`, `weight_lbs`, body measurements, `notes`; unique `(user_id,date)` | Structured body metrics. |
| `cardio_data` | `user_id`, `date`, `activity_type`, duration, HR, intensity, distance, calories, notes; unique `(user_id,date,activity_type)` | Structured cardio records. |
| `nutrition_data` | `user_id`, `date`, calories, macros, fiber, water, sodium, notes; unique `(user_id,date)` | Daily nutrition summary. |
| `food_logs` | `user_id`, `client_id`, date/timestamps, meal/item fields, macros/sodium/fiber/confidence, source/correction state, sanitized estimate JSON, meal ids, item state; unique `(user_id,client_id)` | Item-level food records and review state. |
| `food_log_refresh_events` | refresh id, client id, source/source_url, previous/new macros, acknowledged_at | Audit trail for branded/barcode nutrition refresh changes. |
| `workout_adaptation_pending` | id, date, coalescing window, meal ids/client ids, status, processed event id | Accepted-food windows waiting to be evaluated. |
| `workout_adaptation_events` | id, status, change_type, applies_to, JSON contract columns, acknowledged_at | Persisted nutrition-to-workout adaptation events. |
| `branded_lookup_cache` | `(user_id, normalized_text)`, source, response_json, fetched_at | Food branded-search cache. |
| `barcode_lookup_cache` | `(user_id, barcode)`, source, response_json, fetched_at | Food barcode lookup cache. |
| `personal_vocab` | `(user_id, normalized_input)`, phrase, canonical resolution, accept/correct/skip/delete counts, confidence boost | Learned owner food vocabulary. |
| `meal_acceptance_events` | `(user_id, meal_id)`, status, included ids, feedback fingerprint, skipped/deleted counts | Meal review lifecycle summary. |
| `meal_review_snapshots` | `(user_id, meal_id)`, payload_json, next item sequence, applied refreshes | Durable meal review payload. |
| `recovery_data` | user/date/type/duration/temp/notes | Structured recovery records; multiple per day allowed. |
| `user_settings` | user id, goal, sessions/week, available time, target weight/body fat | SQL settings table used by data store helpers; app also has JSON settings. |
| `push_subscriptions` | endpoint hash, endpoint, subscription JSON, permission state, install flag, user agent, revoked_at | Web push subscriptions. |

### Food estimate sanitization

| Allowed field | Type | Business meaning |
| --- | --- | --- |
| `item_name`, `portion_description`, `meal_type`, `source`, `logged_at`, `date`, `external_food_id`, `verified_source_url`, `data_fetched_at`, `portion_basis`, `brand_id`, `underlying_source`, `personal_vocab_phrase`, `vision_description`, `vision_provider` | string/null | Public food estimate metadata. |
| `calories`, `protein_g`, `carbs_g`, `fat_g`, `sodium_mg`, `fiber_g`, `confidence`, `vision_confidence` | number/null | Nutrition estimate and confidence. |
| `ambiguous`, `from_image` | boolean/null | Review and source flags. |
| `uncertainty_notes` | string/list [TBC] | Explanation of uncertainty. |
| `off_attribution` | sanitized object | Allowed attribution without raw trace data. |

Raw traces/images and unrecognized fields are dropped before storage in `original_estimate_json`.

### WHOOP tables

| Table | Key fields | Business meaning |
| --- | --- | --- |
| `whoop_connection` | singleton id=1, status, connected/disconnected/sync timestamps, scopes_json, material_ref, access_token_expires_at, reauth_required, last_error | Public and private connection state without token values. |
| `whoop_oauth_states` | state, redirect_uri, user_binding, created/expires/consumed | OAuth CSRF/state tracking with 10-minute default TTL. |
| `whoop_sync_runs` | run id, reason, window, status, started/completed, records count, retryable, redacted_error | Sync audit trail. |
| `whoop_records` | record_type/upstream_id PK, local_date, times, score_state, recovery/strain/sleep/health metrics, imported_at, sync_run_id | Raw-enough normalized WHOOP records without protected token material. |
| `whoop_daily_facts` | local_date PK, metrics, score_state, last_sync_attempt, projected_at | Daily coaching facts projected from scored records. |

WHOOP metric fields are: `recovery_score`, `recovery_band`, `strain`, `sleep_performance_pct`, `sleep_need_gap_min`, `workout_kj`, `hrv_rmssd`, `resting_hr`, `respiratory_rate`, `spo2`, `skin_temp`, and `percent_recorded`.

### Wearable fact tables

| Table | Key fields | Business meaning |
| --- | --- | --- |
| `wearable_daily_facts` | `(profile_key,date,provider_id,metric)`, source_label, value_json, unit, band, confidence, freshness, conflict_state, used_for_recommendation, updated_at | Public coaching-safe facts for AI/recommendations. |
| `wearable_sources` | `(profile_key,provider_id)`, label, status, last_data_point, last_sync_attempt, capabilities_json, used_for_recommendation, updated_at | Public source metadata. |

Forbidden field names for public wearable facts include `authorization`, `access_token`, `refresh_token`, `token`, `password`, `secret`, `raw`, `payload`, `samples`, `records`, `user_id`, and any field ending `_token`.

### Backup export payload

| Field | Type | Required | Business meaning |
| --- | --- | --- | --- |
| `version` | string | Yes | Backup schema version, currently `1.1`. |
| `exported_at` | ISO string | Yes | Export timestamp. |
| `data.workouts/soreness/cardio/recovery/settings/baselines/body/sleep/nutrition` | JSON | Yes | JSON-backed stores. |
| `data.food_logs` | list | Yes | Item food logs from SQLite. |
| `data.meal_acceptance_events` | list | Yes | Meal acceptance lifecycle rows. |
| `data.meal_review_snapshots` | list | Yes | Meal review snapshots. |
| `data.personal_vocab` | list | Yes | Learned food vocabulary. |
| `data.whoop_daily_facts` | list | Yes | Last 366 WHOOP daily facts only. |

Backup filename is `fitness_backup_YYYY-MM-DD.json`.

## 4. Interactions & Flows

**Resolve runtime paths.** Trigger: module import or helper call. Behavior: `DATA_DIR` env is stripped; if unset, repo/app directory is used. `data_path(filename)` creates the directory and returns `DATA_DIR/filename`. Success: all runtime stores resolve consistently. Failure: filesystem permission errors propagate where writes occur.

**Load JSON store.** Trigger: app startup. Behavior: if the file is missing, use default; if malformed, rename corrupt file and write default. Success: global in-memory list/object is populated. Failure: unexpected IO errors propagate [TBC: exact response depends on startup context].

**Save JSON store.** Trigger: feature save such as workout/cardio/import/settings. Behavior: acquire `JSON_DATA_LOCK`, write unique temp file, fsync, atomic replace, cleanup temp. Success: readers do not see partial file. Failure: `save_json` suppresses `OSError` and only prints a warning, so callers are not told the write failed; the old file remains when replace did not happen. Startup workout-id backfill is an exception to this path: it writes `data_workouts.json` with a raw non-atomic `open()` outside `JSON_DATA_LOCK`.

**Initialize core SQLite store.** Trigger: app startup or helper import path. Behavior: `init_data_db` creates all tables, adds missing columns, rebuilds legacy branded lookup cache if necessary, normalizes duplicate food-log client ids, and creates indexes. Success: idempotent migrations complete. Failure: transaction rollback closes connection.

**Use SQLite connection.** Trigger: any data store helper. Behavior: open connection with row factory, enable foreign keys, commit on success, rollback on exception, close in finally. Success: every helper call closes its connection. Failure: exception rolls back.

**Add food log.** Trigger: manual/vision/barcode/text food save. Behavior: default source is `vision_estimate` when an original estimate exists, otherwise `manual`; default correction state is `accepted` for estimates and `manual` otherwise [TBC: business label for manual state]. Original estimate is sanitized. Unique `(user_id,client_id)` upserts item fields while preserving created_at. Success: returns persisted row. Failure: SQLite errors roll back.

**Backfill food-log client id.** Trigger: reconciliation for legacy clientless rows. Behavior: match must include date and safe food fields; exactly one candidate is required. Success: one row receives client id. Failure: ambiguous or missing candidate returns false.

**Clear food logs.** Trigger: no product trigger today; helper exists for backup-restore/tests only and no route invokes it. Behavior: deletes adaptation events/pending, refresh events, and food logs for the user. Success: food-derived adaptation state clears with logs. Failure: transaction rollback.

**Store public wearable facts.** Trigger: Open Wearables sync or provider bridge. Behavior: validate facts against forbidden fields, scope to profile key, upsert by `(profile_key,date,provider_id,metric)`. Success: facts become available for AI/recommendations. Failure: forbidden field raises an error before persistence.

**Store WHOOP token material.** Trigger: OAuth connect/refresh. Behavior: write protected material file outside SQLite, chmod 600, then update DB with material reference and expiry. If DB update fails, cleanup attempts to remove written material. Success: DB never stores token values. Failure: connection may require reauth.

**Project WHOOP daily facts.** Trigger: sync/import. Behavior: group records by local date; copy metrics only from records with `score_state=SCORED`; prefer recovery scored state, otherwise preserve unscored/calibrating state; delete and rebuild daily facts. Success: recommendation layer reads daily facts. Failure: invalid records are skipped or transaction fails.

**Export backup.** Trigger: `GET /api/export-backup`. Behavior: assemble JSON stores plus selected SQLite rows and WHOOP daily facts. Success: downloads JSON file. Failure: unhandled errors return server failure [TBC: no explicit wrapper observed].

**Import backup.** Trigger: `POST /api/import-backup`. Behavior: validate JSON body and `data` object; validate WHOOP facts before mutating; acquire WHOOP mutation guard if needed; restore JSON stores under `JSON_DATA_LOCK`; import food logs/vocab/meal state; clear and reproject WHOOP facts from backup records. Success: returns status and counts [TBC: exact count fields below truncated in sampled output]. Failure: invalid format returns 400; concurrent WHOOP sync returns 409 `whoop_sync_in_progress`; caught errors return 400 `status:error`.

**Remove synthetic history rows.** Trigger: manual script execution. Behavior: `scripts/remove_synthetic_history_rows.py` inspects `data_workouts.json`, `data_cardio.json`, and `data_recovery.json` for suspect dates 2026-04-12 through 2026-04-14. Dry run lists candidates. Actual deletion requires explicit `--remove KIND:ID`, `--expect-count`, and `--apply`; it does not delete by date alone. Success: only specified rows are removed. Failure: expectation mismatch blocks deletion.

**Delete/restore history rows.** Trigger: `POST /api/delete-history` or `POST /api/restore-history`. Behavior: mutates JSON history stores with an undo/restore contract for user-visible history rows.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
| --- | --- | --- | --- | --- | --- | --- |
| GET | /api/export-backup | Session auth | Owner downloads backup | none | JSON attachment with backup payload | Real |
| POST | /api/import-backup | Session auth + CSRF/same-origin | Owner restores backup | Backup JSON body | Status/counts or error | Real |
| GET | /api/export-md | Session auth | Owner exports markdown [TBC: details not inspected] | [TBC] | Markdown export | Real [TBC] |

Persistence is otherwise accessed indirectly through feature routes:

| Feature route group | Stores touched |
| --- | --- |
| Food logging/review/barcode/text/photo | `fitness_data.db` food logs, refresh events, lookup caches, personal vocab, meal snapshots/events, adaptation pending. |
| Recommendation/AI coach | JSON history/settings, Oura/Apple/WHOOP/wearable fact DBs, adaptation events, `ai_coach_cache.sqlite3`, in-memory recommendation cache. |
| Wearable sync/status | Oura cache, Apple Health DB, WHOOP DB/protected material, wearable facts DB, Open Wearables config JSON. |
| Account/auth/push | `auth.db`, `fitness_data.db` push subscriptions and user-scoped tables. |

Endpoint details:

- `GET /api/export-backup` exports WHOOP daily facts but deliberately does not export WHOOP protected material, OAuth states, sync run internals beyond facts, auth DB, push subscriptions, or AI cache.
- `POST /api/import-backup` validates WHOOP backup facts against forbidden fields: `access_token`, `refresh_token`, `token_ref`, `material_ref`, `raw`, `raw_json`, `payload`, `provider_payload`, and `client_secret`.
- `POST /api/import-backup` restores JSON stores first, then imports SQLite food/vocab/meal rows, then WHOOP facts. This is not a single cross-store transaction.

## 6. Data Model & Persistence

The persistence architecture has four layers:

1. Runtime path layer: `runtime_config.py` centralizes `DATA_DIR`, `data_path`, and public base URL derivation.
2. JSON app state: app-native lists/objects loaded into module globals and saved atomically.
3. SQLite structured stores: data with uniqueness, indexing, idempotency, source-proof, or integration state.
4. Protected external material: secrets/tokens that should not live in normal SQLite or backups.

Connection lifecycle is explicit. `data_store._get_db` opens a SQLite connection to `fitness_data.db`, sets `row_factory=sqlite3.Row`, enables `PRAGMA foreign_keys=ON`, commits after yielded work, rolls back on exception, and closes in `finally`. Tests assert food-log calls close every connection.

Schema migration is opportunistic and in-process. `init_data_db` creates tables if missing, adds missing columns for nutrition sodium and food-log/review/vocab/push fields, handles legacy branded lookup cache shape, removes duplicate food-log client-id conflicts by nulling older duplicates, and creates indexes for food logs, refresh events, and adaptation events.

History normalization makes mixed sources business-readable. Strength-like labels including Lifted, functional strength training, traditional strength training, strength training, weight training, and resistance training normalize to `canonical_category=strength_training`, plus any label containing strength/weight/resistance and any row whose source is `lifted`. Source labels distinguish Strength - Logged, Strength - Watch, and provider-specific strength rows. Unknown activities normalize to lowercased underscore labels or `unknown`.

`data_loader.py` parses legacy support markdown into workouts and soreness. Machine names map to primary muscles, and RPE is inferred from notes: fail/couldn't = 10, struggling = 9, burn/hard = 8, easy/felt good = 6, top set = 8.5, ramp = 6, otherwise 7.5. Session type is inferred from muscles as upper, lower, full_body, or other.

Synthetic-history cleanup is intentionally manual and constrained. The cleanup doc identifies suspected synthetic runtime rows on 2026-04-12, 2026-04-13, and 2026-04-14. The script only edits runtime JSON files and requires exact ids plus expected counts to apply.

Retention is mostly indefinite. JSON history, SQLite logs, wearable facts, food logs, and caches remain until cleared, imported over, or manually cleaned. `delete_user_data` is an unwired test-only helper today; no user-facing account-data-deletion flow currently exists. Explicit bounded reads exist for API payloads: wearable facts limit defaults to 30 and clamps to 100; WHOOP daily facts export limits to 366; workout adaptation event list clamps to 1-50; sync-run/fact helper limits clamp internally. Cache TTLs are limited: weather cache max age is 600 seconds; Oura source label is "live" only when sync attempt is under 1 hour old. Lookup cache TTLs are not evident in `data_store.py` [TBC: cache invalidation may live in food feature code].

## 7. Enums & Constants

### Runtime files

| Constant | Value |
| --- | --- |
| `WHOOP_SYNC_LOCK_FILE` | `DATA_DIR/whoop_sync.lock` |
| `WORKOUTS_FILE` | `DATA_DIR/data_workouts.json` |
| `SORENESS_FILE` | `DATA_DIR/data_soreness.json` |
| `SETTINGS_FILE` | `DATA_DIR/data_settings.json` |
| `CARDIO_FILE` | `DATA_DIR/data_cardio.json` |
| `RECOVERY_FILE` | `DATA_DIR/data_recovery.json` |
| `BASELINES_FILE` | `DATA_DIR/data_baselines.json` |
| `BODY_FILE` | `DATA_DIR/data_body.json` |
| `SLEEP_FILE` | `DATA_DIR/data_sleep.json` |
| `NUTRITION_FILE` | `DATA_DIR/data_nutrition.json` |
| `OURA_DB_FILE` | `DATA_DIR/oura_daily.sqlite3` |
| `WHOOP_DB_FILE` | `DATA_DIR/whoop.sqlite3` |
| `WEARABLE_FACTS_DB_FILE` | `DATA_DIR/wearable_facts.sqlite3` |
| `OPEN_WEARABLES_CONFIG_FILE` | `DATA_DIR/open_wearables_config.json` |
| `DATA_DB` | `DATA_DIR/fitness_data.db` |

### App JSON settings defaults

| Field | Default |
| --- | --- |
| `training_goal` | `strength_hypertrophy` |
| `date_of_birth` | empty string |
| `sex` | empty string |
| `sessions_per_week_target` | 3 |
| `available_time_minutes` | 75 |
| `target_weight_lbs` | 175 |
| `target_body_fat_pct` | 18 |
| `daily_calorie_target` | 2200 |
| `daily_protein_target_g` | 148 |
| `fatigue_threshold` | 72 |
| `equipment_preference` | `machines_only` |
| `preferred_equipment_brands` | `["Hoist","Nautilus"]` |
| `excluded_exercises` | `["Preacher Curl"]` |
| `volume_landmarks` | mv 6, mev 9, mav_min 12, mav_max 18, mrv 22 |

### SQL data-store settings defaults

| Field | Default |
| --- | --- |
| `training_goal` | `body_recomp` |
| `sessions_per_week_target` | 4 |
| `available_time_minutes` | 60 |
| `target_weight_lbs` | 175.0 |
| `target_body_fat_pct` | 12.0 |

These differ from app JSON settings defaults and should not be treated as equivalent without explicit migration/product decision.

### Training goal enum

| Value | Meaning |
| --- | --- |
| `strength` | Maximize 1RM / low reps, high intensity. |
| `hypertrophy` | Muscle growth / moderate reps. |
| `endurance` | High-rep muscular endurance. |
| `weight_loss` | Calorie-supportive training. |
| `toning` | Moderate full-body tone/recomp style. |
| `strength_hypertrophy` | Hybrid strength and hypertrophy. |
| `hypertrophy_endurance` | Hybrid hypertrophy and endurance. |
| `weight_loss_toning` | Hybrid weight-loss and toning. |

### Freshness and cache constants

| Constant | Value | Meaning |
| --- | --- | --- |
| `_FRESHNESS_AGING_HOURS` | 24 | Data point becomes aging after 24 hours. |
| `_FRESHNESS_STALE_HOURS` | 48 | Data point becomes stale after 48 hours. |
| Weather cache max age | 600 seconds | Cached wttr.in response reuse window. |
| WHOOP OAuth state TTL | 10 minutes | Default state expiry. |
| WHOOP CSV max bytes | 512 KiB | Upload/import cap. |
| WHOOP CSV max rows | 5000 | Upload/import cap. |

### WHOOP statuses and score states

| Value | Meaning |
| --- | --- |
| `disconnected` | Default connection status. |
| `connected` | OAuth/material connection is present. |
| `reauth_required` | Token/material needs renewed auth. |
| `SCORED` | Daily record can project metrics into recommendation facts. |
| `UNSCORED` / `CALIBRATING` [TBC exact upstream spellings] | Stored as score state but not used for modifying recommendation. |

### Food and meal states

| Value | Meaning |
| --- | --- |
| `pending`, `pending_review`, `needs_review`, `review` | Excluded from accepted nutrition context and adaptation triggers. |
| `accepted` | Food entry can affect nutrition totals and adaptation logic. |
| `manual` | Default correction state for manual food logs [TBC: whether treated as accepted by all consumers]. |

### Account/auth constants

| Constant | Value | Meaning |
| --- | --- | --- |
| `FITNESS_DASHBOARD_SINGLE_USER` | default true | Registration disabled after first user unless set false. |
| `FITNESS_DASHBOARD_OWNER_USER_ID` | optional | Explicit owner id for single-user guard. |
| Auth rate window | 600 seconds | Login/register failure window. |
| Auth max failures | 10 | Lockout threshold. |
| CSRF header | `X-Requested-With: XMLHttpRequest` | Browser mutation proof accepted by server. |
| CSRF exempt paths | `/api/apple-health/sync`, `/webhook` | External token/signature-authenticated routes. |

## 8. Integration Points

- AI Coach / Recommendation Engine reads workouts, soreness, settings, recovery, cardio, food logs, adaptation events, Oura, Apple Health, WHOOP, wearable facts, and AI cache.
- Food logging writes `food_logs`, lookup caches, personal vocabulary, meal review state, refresh events, and adaptation pending windows.
- Wearable integrations write Oura cache, Apple Health sync DB, WHOOP DB/protected material, Open Wearables config, and public wearable facts.
- Auth gates route access and owns `auth.db`, single-owner mode, password hashes, sessions, CSRF, and rate limiting.
- Backup/import crosses JSON stores, food/meal/vocab SQLite rows, and WHOOP daily facts.
- Push notifications store subscriptions in `fitness_data.db` and derive public URLs from runtime config.
- Synthetic cleanup script operates on runtime JSON history and should be coordinated with backup/export flows.

## 9. Permissions & Security

Runtime data is local but not inherently public-safe. The sanitized repository excludes real `DATA_DIR` artifacts. Session auth protects app routes by default; public prefixes are login/register/logout, landing/pricing/static/manifest/service worker/SEO pages, Stripe webhook, checkout pages, and exact Apple Health sync webhook. Single-user mode is enabled unless `FITNESS_DASHBOARD_SINGLE_USER=false`; registration is disabled once a user exists.

Mutating browser requests must pass same-origin browser metadata or `X-Requested-With: XMLHttpRequest`. Cross-origin browser headers are rejected before CSRF header checks. Apple Health sync is exempt from browser CSRF because it is token-authenticated; Stripe webhook is exempt because it is signature-authenticated.

WHOOP access/refresh token values are stored in a protected material file outside SQLite, not in the database. The connection table stores only `material_ref`, expiry, scopes, status, and redacted errors. Backup export validates WHOOP facts to reject token/material/raw payload fields.

Wearable facts are public coaching facts only. The store rejects raw provider payloads, records/samples, secrets, token fields, and `user_id`. Profile scoping uses `profile_key` rather than raw user id.

Known security/privacy risks remain: Open Wearables/Apple Health token and URL handling is covered by existing FIT-261; `delete_user_data` is unwired and deletes push-subscription rows but not browser subscription state; backup export includes food and health-derived data in plain JSON by design. Flask auth persists `.flask-secret` in the project directory rather than `DATA_DIR`.

## 10. Business Rules

`DATA_DIR` is the runtime boundary. Any local deployment, launchd job, test, or smoke check must set the same `DATA_DIR` if it expects to read the same owner data. If unset, runtime writes land in the checkout directory.

JSON files are the app-native source for workout history and many dashboard lists. SQLite stores are the source for food item state, adaptation contracts, caches, integration state, and source proof. The same business concept can exist in both layers during migration, such as nutrition summaries in `data_nutrition.json` and item food logs in `fitness_data.db`; accepted food logs win for current nutrition context when available.

The app uses in-memory globals as hot state for JSON stores and some caches. This is acceptable for a single local process but unsafe as a multi-worker data contract. The open FIT-256 issue covers global-state corruption; this PRD references it rather than duplicating the root issue.

Backups are user-data exports, not full runtime clones. They do not include auth users/passwords, WHOOP protected token material, OAuth states, sync lock files, push subscriptions, AI coach cache, weather cache, raw provider payloads, or app config secrets.

Import is not all-or-nothing across JSON, food SQLite, and WHOOP stores. WHOOP facts are validated before mutation, and JSON stores are restored under one lock, but later SQLite imports can still partially apply if a later row fails [TBC: exact rollback behavior per import helper varies].

Account data deletion is table-based in `fitness_data.db`, including push-subscription rows. It does not touch JSON history files, wearable integration DBs, auth users, WHOOP protected material, Open Wearables config, Apple Health sync DB, Oura cache, or browser subscription state. Treat it as partial structured-data deletion, not full local-data erasure.

## 11. Config & Environment

| Env/config | Default | Behavior when unset |
| --- | --- | --- |
| `DATA_DIR` | App directory | Runtime data is read/written beside source files. |
| `FITNESS_DASHBOARD_PUBLIC_BASE_URL` | request-derived | Public URLs are inferred from host/proxy headers. |
| `APPLE_HEALTH_SYNC_DB` | `DATA_DIR/apple_health_sync.db` | Apple Health freshness/load reads default DB. |
| `WHOOP_PROTECTED_MATERIAL_DIR` | macOS Application Support secrets dir | WHOOP protected material file is stored outside repo/data DB. |
| `FITNESS_DASHBOARD_SINGLE_USER` | true | Registration closes after first user. |
| `FITNESS_DASHBOARD_OWNER_USER_ID` | min user id | Owner guard uses first local user. |
| `FITNESS_PUSH_VAPID_PUBLIC_KEY` / `VAPID_PUBLIC_KEY` | empty | Push public key unavailable. |
| LM Studio env vars | local defaults | AI cache still exists; adapter may report unreachable. |

Launchd/runtime tests assert that installers export `DATA_DIR` and public base URL, and do not hardcode a public base URL.

## 12. Test Coverage

Existing focused tests cover:

- `tests/test_data_store_connection_lifecycle.py`: food-log operations close every `fitness_data.db` connection.
- `tests/test_data_store_sodium.py`: sodium round-trip through nutrition records, migration adding sodium to pre-existing nutrition table, and missing sodium accepted.
- `tests/test_fit183_runtime_paths.py`: `DATA_DIR` controls JSON and SQLite stores, `save_json` uses unique atomic temp files, public base URL feeds Apple Health and push, VAPID claims keep mailto fallback for local HTTP, Stripe/Oura helpers use `DATA_DIR`, and launchd installer exports runtime path/public base URL without hardcoding.
- Adjacent assigned tests also exercise persistence contracts indirectly: FIT-136 adaptation events/pending rows, AI fact context sanitization, AI metrics cache, Apple Health recommendation bridge history normalization/deduping, and dynamic cardio plan cache behavior.

Coverage gaps:

- No observed test that account deletion removes every persisted owner artifact across JSON, SQLite integration DBs, protected material, and push subscriptions.
- No observed test for backup/import partial failure rollback across JSON plus SQLite stores.
- No observed test reconciling divergent JSON settings defaults and SQL `user_settings` defaults.
- No observed test for wearable facts rejecting every forbidden raw/secret field variant plus profile migration together.

## 13. Gaps & Issue Candidates

### IC-1: Eliminate multi-worker global-state corruption
- **Type:** Bug
- **Priority:** urgent
- **Where:** app.py:215; app.py:219; app.py:494; app.py:507-509; app.py:973-997
- **Problem:** The app loads JSON stores and recommendation/suggestion/cache state into process globals. In a multi-worker or restarted process, workers can diverge on `WORKOUTS`, `USER_SETTINGS`, `LAST_WORKOUT_RECOMMENDATION`, `AI_PENDING_SUGGESTIONS`, weather cache, and related state.
- **Why it matters:** The owner can see stale recommendations, lost approvals, or inconsistent saved data depending on which process handles the request.
- **Acceptance criteria:**
  - Shared mutable product state is moved behind durable stores or single-worker enforcement.
  - Runtime configuration documents and enforces the supported worker model.
  - Tests simulate separate process/request state for at least recommendations and AI suggestions.
- **Duplicate-of:** FIT-256

### IC-2: Move integration secrets and tokens out of plaintext/URLs
- **Type:** Privacy
- **Priority:** high
- **Where:** app.py:212; runtime_config.py:29; whoop_store.py:348
- **Problem:** WHOOP token material is protected outside SQLite, but adjacent integration material such as Open Wearables hub credentials and Apple Health tokenized URLs remain a known risk area. The current data contract cannot claim all integration secrets are protected consistently.
- **Why it matters:** Local-first still needs clear secret boundaries; backups, logs, URLs, and config files can leak sensitive health integration access.
- **Acceptance criteria:**
  - Inventory each integration secret/token and its storage location.
  - Move plaintext/tokenized config to protected storage or redactable references.
  - Ensure backups and public status payloads never include secret-bearing values.
- **Duplicate-of:** FIT-261

### IC-3: Delete push subscriptions during account data deletion
- **Type:** Privacy
- **Priority:** high
- **Where:** data_store.py:1955
- **Problem:** `delete_user_data` deletes many user-scoped tables and lookup caches, but it does not delete or revoke `push_subscriptions`, which include endpoint URLs and subscription JSON.
- **Why it matters:** A user data deletion flow can leave notification endpoints and browser subscription material behind.
- **Acceptance criteria:**
  - `delete_user_data` deletes or revokes push subscriptions for the user.
  - Data summary includes push subscription counts.
  - Tests cover deletion of active and revoked subscriptions.
- **Duplicate-of:** none

### IC-4: Resolve JSON settings vs SQL settings default drift
- **Type:** Data-contract
- **Priority:** medium
- **Where:** app.py settings defaults; data_store.py:1900
- **Problem:** App JSON settings default to `strength_hypertrophy`, 3 sessions/week, 75 minutes, and 18% body fat, while SQL `user_settings` defaults to `body_recomp`, 4 sessions/week, 60 minutes, and 12% body fat. It is unclear which store is authoritative for new features.
- **Why it matters:** New routes or agents using the SQL helper can generate different coaching behavior than the dashboard.
- **Acceptance criteria:**
  - Declare the authoritative settings store and defaults.
  - Align helper defaults or add an explicit migration/adapter.
  - Tests assert both app and data-store settings callers return the same defaults where they overlap.
- **Duplicate-of:** none

### IC-5: Make backup import transactional or explicitly resumable
- **Type:** Improvement
- **Priority:** medium
- **Where:** app.py:15243
- **Problem:** Backup import restores JSON stores, then imports food/vocab/meal SQLite rows, then WHOOP facts. These operations are not one cross-store transaction, so a later failure can leave a partially restored runtime.
- **Why it matters:** A failed restore can mix old and new owner data without a clear recovery path.
- **Acceptance criteria:**
  - Import either stages and swaps all stores atomically where feasible or writes a resumable import journal.
  - Failure response identifies which stores were mutated.
  - Tests inject a failure after JSON restore and prove rollback or documented resumability.
- **Duplicate-of:** none

### IC-6: Clarify accepted/manual food-log filtering in data-store APIs
- **Type:** Data-contract
- **Priority:** medium
- **Where:** data_store.py:685; data_store.py:1810
- **Problem:** `get_food_logs` returns rows ordered by logged time without filtering by accepted/correction state, while recommendation/adaptation logic separately excludes pending/review states. The data-store API name/commenting can mislead callers into treating all returned rows as accepted.
- **Why it matters:** A future feature could accidentally count pending food estimates in nutrition totals or plan changes.
- **Acceptance criteria:**
  - Document or rename the all-rows food-log helper.
  - Add an explicit accepted-food-log query helper.
  - Tests cover pending, manual, accepted, and review states for both helpers.
- **Duplicate-of:** none

### IC-7: Define full local-data deletion boundaries
- **Type:** Privacy
- **Priority:** medium
- **Where:** data_store.py:1955; app.py runtime store declarations at app.py:199
- **Problem:** Account deletion only deletes selected `fitness_data.db` tables. It does not remove JSON history, auth rows, Oura/Apple Health/WHOOP/wearable fact DBs, protected token material, Open Wearables config, AI cache, or sync lock files.
- **Why it matters:** Product language around deleting user data could overpromise unless the boundary is explicit.
- **Acceptance criteria:**
  - Product copy and API docs distinguish structured food/body data deletion from full local purge.
  - Add a separate full local purge flow or document manual deletion steps.
  - Tests or dry-run output list every store affected and not affected.
- **Duplicate-of:** none

### IC-8: Add TTL or invalidation policy for food lookup caches
- **Type:** Improvement
- **Priority:** low
- **Where:** data_store.py:382; data_store.py:391
- **Problem:** Branded and barcode lookup caches store fetched responses and timestamps, but no TTL or invalidation policy is evident in the data-store layer.
- **Why it matters:** Nutrition provider corrections may not reach the owner if stale cached rows are reused indefinitely.
- **Acceptance criteria:**
  - Define cache TTL or manual refresh semantics for branded and barcode lookup caches.
  - Ensure refresh events record old/new values when stale cache data changes.
  - Tests cover expired cache, fresh cache, and forced refresh behavior.
- **Duplicate-of:** none
