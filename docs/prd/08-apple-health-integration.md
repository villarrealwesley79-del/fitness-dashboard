# Apple Health Integration - PRD

> **Sources:** `README.md`; `docs/VISION.md`; `docs/PRD.md`; `docs/CURRENT_STATE.md`; `docs/APPLE_HEALTH_HELPER_SLA.md`; `app.py`; `apple_health_parser.py`; `health_ingest.py`; `scripts/check-apple-health-staleness.sh`; `templates/index.html`; `static/js/app.js`; `tests/test_apple_health_hae_dates.py`; `tests/test_apple_health_recommendation_bridge.py`; `tests/test_apple_health_setup_url.py`; `tests/test_wearable_freshness_contract.py`; `tests/test_csrf_protection.py`
> **Routes:** `/api/apple-health/sync`, `/api/apple-health/sync/status`, `/api/apple-health/sync/setup-url`, `/api/apple-health/status`, `/api/apple-health/summary`, `/api/apple-health/workouts`, `/api/apple-health/sleep`, `/api/apple-health/steps`, `/api/apple-health/vitals`, legacy `/api/health/workouts`, `/api/health/sleep`, `/api/health/steps`, `/api/health/vitals`, `/api/health/summary`, adjacent non-Apple `/api/health/sync`
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

The Apple Health integration bridges iPhone HealthKit data into the local Flask dashboard. The main production path is Health Auto Export (HAE) or a Shortcut-style JSON webhook posting to `/api/apple-health/sync` with a shared token. A legacy file-export path still reads JSON exports from `~/Documents/Health`. Together these feed status cards, history, ACWR, muscle readiness, cardio fatigue, smart recommendation reasoning, and daily freshness confidence.

This is a real local integration with token-authenticated webhook ingestion and local SQLite persistence. It is not a direct browser HealthKit client. The browser cannot read Apple Health; the phone must push JSON to the backend. Native iOS HealthKit work is explicitly deferred unless the documented freshness SLA fails after bridge hardening.

The integration distinguishes three states that product stakeholders should keep separate: setup configured, sync attempts received, and usable data accepted. The UI intentionally shows last accepted export, last attempted export, data-through date, record counts, and stale warnings so a recent retry of old data does not masquerade as current health evidence.

`/api/health/sync` is included in this PRD because it was assigned, but the route is not Apple Health ingestion in the current code. It manually pulls Open Wearables data and returns metadata-only sync results. Treat it as adjacent wearable infrastructure, not the Apple Health webhook.

## 2. User-Facing Surfaces

The dashboard recommendation card has an Apple Health freshness chip. The chip uses server freshness from `freshness.apple_health`, not only HAE last-attempt time. It can render unknown, no data, fresh, aging, or stale. When all wearable data is missing or stale, the recommendation switches to lower-confidence copy and asks the owner to sync Oura or Apple Health.

The Settings integration panel has an Apple Health row with a status dot, freshness chip, setup button, "Last export" detail, and an evidence panel. The evidence panel shows last accepted export, last attempted export when it differs from accepted, data-through date, total records and top record-type counts, and stale/setup warnings.

The Apple Health setup modal explains the Health Auto Export phone setup. It instructs the owner to create daily JSON REST API automations for metrics and workouts, shows the tokenized webhook URL only when the modal is opened, and shows current backend state such as "waiting for Health Auto Export to post data" or last accepted export time/count.

History and recommendation flows consume Apple Health workouts even when they are not directly visible as an Apple Health-specific screen. Synced workouts can increase ACWR, add cardio fatigue, contribute strength muscle volume if `muscle_groups` are present, and appear in smart recommendation reasoning when workout HR intensity raises recent load.

## 3. Field Inventory

### Health Auto Export Accepted Payloads

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `data.workouts[]` | array | no | empty | object rows only | HAE native workout rows. |
| `data.metrics[]` | array | no | empty | known metric names only | HAE native metric rows. |
| `workouts[]` | array | no | empty | flat legacy/internal shape | Workout rows. |
| `steps[]` | array | no | empty | flat legacy/internal shape | Daily steps. |
| `heart_rate[]` | array | no | empty | flat legacy/internal shape | Resting or average heart-rate values. |
| `hrv[]` | array | no | empty | flat legacy/internal shape | HRV values; a flat payload containing only `hrv[]` is silently dropped because `hrv` is not part of flat-shape detection. Include at least one of `workouts`, `steps`, `heart_rate`, `sleep`, or `active_energy`. |
| `sleep[]` | array | no | empty | flat legacy/internal shape | Daily sleep rows. |
| `active_energy[]` | array | no | empty | flat legacy/internal shape | Active energy rows. |

### Normalized HAE Workout Row

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `date` | string `YYYY-MM-DD` | yes for persistence | derived from start timestamp | ISO offset local date | Calendar day used for dedupe/freshness. |
| `startDate` | ISO datetime/string | no | input start | preserved | Workout start evidence. |
| `activity_type` | string | no | from HAE `name`, `workoutActivityType`, or `activity_type` | mapped when numeric | Human-readable workout type. |
| `duration_minutes` | number | no | `0` | HAE seconds converted when value > 240 | Workout duration. |
| `total_energy_kcal` | number | no | null | from `totalEnergy` or `activeEnergyBurned` | Calorie estimate. |
| `avg_heart_rate` | number | no | null | accepts nested `avg/average/qty/value`; app-level valid range 30-230 for recommendation bridge | Average workout HR. |
| `distance_m` | number | no | null | accepts nested `qty/value` | Distance in meters. |
| `muscle_groups` | array/dict | no | empty | optional downstream normalization | Strength volume contribution when present. |

### Normalized HAE Metric Rows

| HAE metric names | Internal record type | Stored fields | Business meaning |
|---|---|---|---|
| `step_count`, `steps` | `steps` | `date`, `value` | Daily step count. |
| `active_energy`, `active_energy_burned` | `active_energy` | `date`, `value` | Daily active energy. |
| `resting_heart_rate`, `heart_rate` | `heart_rate` | `date`, `value`, `type` (`resting` or `avg`) | Daily HR summary; resting HR is not used as workout HR intensity. |
| `heart_rate_variability` | `hrv` | `date`, `value` | HRV signal. |
| `sleep_analysis`, `sleep` | `sleep` | `date`, `duration_minutes`, `duration_minutes_source`, `deep_minutes`, `rem_minutes`, `core_minutes`, `awake_minutes`, `in_bed_minutes` | Daily sleep summary. |

### Sync Database Tables

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `ah_sync_log.id` | integer | yes | autoincrement | primary key | Internal row id. |
| `source` | text | yes | `health_auto_export` | fixed by sync route | Origin of accepted row. |
| `record_type` | text | yes | none | one of sync record types | Category. |
| `record_date` | text | yes | none | derived local date | Data-through/freshness date. |
| `record_key` | text | yes | `""` | workout-specific key or empty | Allows multiple workouts per day while deduping daily aggregates. |
| `data_json` | JSON text | yes | none | JSON serializable | Stored normalized record. |
| `created_at` | SQLite datetime | no | `datetime('now')` | updated on changed data | Accepted insert/update timestamp. |
| `ah_sync_events.id` | integer | yes | autoincrement | primary key | Sync attempt row. |
| `inserted_count` | integer | yes | `0` | counted by route | Rows inserted or changed. |
| `skipped_count` | integer | yes | `0` | counted by route | Duplicate/invalid/skipped rows. |
| `total_count` | integer | yes | `0` | inserted + skipped | Attempt size. |
| `remote_addr` | text | no | request remote address | first forwarded IP if present | Basic operational source evidence. |
| `created_at` | SQLite datetime | no | `datetime('now')` | automatic | Attempt timestamp. |

### Status/UI Fields

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `total_records` | integer | yes | `0` | count from `ah_sync_log` | Number of accepted rows. |
| `by_type` | object | yes | `{}` | grouped counts | Record mix, e.g. workouts, sleep, HRV. |
| `last_sync` | datetime/null | yes | null | max `ah_sync_log.created_at` | Last accepted insert/update. |
| `last_attempt` | datetime/null | yes | `last_sync` fallback | max `ah_sync_events.created_at` | Last raw webhook attempt. |
| `last_event` | object/null | yes | null | inserted/skipped/total ints | Last attempt counts. |
| `setup_configured` | boolean | yes | env-derived | token exists | Whether webhook token is configured. |
| `has_token` | boolean | yes | env-derived | token exists | Same token evidence for setup UI. |
| `public_url_configured` | boolean | yes | env-derived | base URL or webhook URL exists | Whether setup URL has explicit public config. |

## 4. Interactions & Flows

Setup URL flow -> The owner opens Settings and clicks Apple Health `Setup`. The frontend first tries `/api/apple-health/sync/status`, then fetches `/api/apple-health/sync/setup-url`. The setup URL endpoint appends `HEALTH_SYNC_TOKEN` to either `APPLE_HEALTH_WEBHOOK_URL` or `${FITNESS_DASHBOARD_PUBLIC_BASE_URL}/api/apple-health/sync`, falling back to request-derived host when no public config exists. The modal shows the tokenized URL only inside the setup flow so routine Settings rendering does not fetch token material.

Webhook auth flow -> HAE posts JSON to `/api/apple-health/sync` with `X-Sync-Token` or `?token=`. The route compares the supplied token to `HEALTH_SYNC_TOKEN` with constant-time comparison. Missing server token returns 503. Missing/invalid supplied token returns 401. Empty/non-JSON body returns 400. Valid body is normalized and persisted.

HAE native normalization -> If payload has `data.workouts` and/or `data.metrics`, the parser converts it into internal flat arrays. Workouts derive local date from the timestamp's own offset, convert HAE v2 duration seconds to minutes when the value is over 240, extract calories from `totalEnergy` or `activeEnergyBurned`, extract average HR from nested forms, and preserve distance meters. Metrics map known names into steps, active energy, heart rate, HRV, and sleep.

Flat payload normalization -> If the payload already has top-level arrays like `workouts`, `steps`, `heart_rate`, `sleep`, or `active_energy`, the route treats it as already normalized and persists those arrays after per-record date normalization. `hrv` is not part of flat-shape detection; a flat payload containing only `hrv[]` is silently dropped with inserted=0.

Persistence/dedupe flow -> For record types `workouts`, `heart_rate`, `hrv`, `sleep`, `steps`, and `active_energy`, each row must have a derivable `record_date` from `date` or `startDate`. Missing dates are skipped. Workouts get `record_key` from start timestamp or fallback `activity:duration` (or `activity:totalEnergyBurned` when no duration field exists); all other record types use empty key for one aggregate row per day. The unique key is `(source, record_type, record_date, record_key)`. Exact duplicate resends are skipped; changed rows update `data_json` and `created_at`.

Sleep merge flow -> Reingested sleep rows merge with existing daily sleep rows to avoid erasing richer duration/stage data with partial HAE resends. Existing `duration_minutes` is preserved when incoming duration is missing or when an `hae_asleep` duration would otherwise be replaced by a computed phase sum. Deep, REM, core, awake, and in-bed minutes are preserved when incoming values are missing.

Status/freshness flow -> `/api/apple-health/sync/status` initializes the sync DB if needed, reports aggregate counts, and remains auth-gated. `_latest_apple_health_freshness()` reads max `record_date` as the data point and max `ah_sync_events.created_at` as last sync attempt. Freshness buckets are server-side: fresh under 24 hours, aging 24-48 hours, stale over 48 hours, missing when no records exist.

Recommendation bridge -> The app loads Apple Health workouts from both file exports and sync DB for the last 28 days. It normalizes each workout into app history shape, ignores activity type "Other", dedupes against existing app workouts by same activity plus start time within 5 minutes or same date/activity when start times are missing, then folds the result into ACWR and cardio fatigue. Optional strength `muscle_groups` can contribute synthetic "Apple Health Strength" sets and volume.

Workout HR intensity flow -> `APPLE_HEALTH_WORKOUT_HR_INTENSITY` is off by default. When enabled, average workout HR between 30 and 230 can raise workout/cardio load only if no explicit strength volume exists and HR-derived intensity exceeds base intensity. HR bands are: `>=170` -> intensity 8, `>=150` -> 7, `>=120` -> 6, otherwise 5. Daily resting HR rows are not used as workout intensity.

Staleness watchdog -> `scripts/check-apple-health-staleness.sh` reads local SQLite directly rather than the auth-gated status endpoint. It stays quiet until a first sync exists, records first seen time, then exits nonzero and logs to `/tmp/apple-health-staleness.log` when last event/log timestamp is older than `STALE_AFTER_HOURS`, default 36.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
|---|---|---|---|---|---|---|
| POST | `/api/apple-health/sync` | token, CSRF-exempt, public exact path | HAE/Shortcut webhook | header `X-Sync-Token` or `?token=`; JSON body | `{status:"ok", inserted, skipped, sync_token}` or error | Real |
| GET | `/api/apple-health/sync/status` | owner session | Settings/status/smoke | none | counts, timestamps, setup flags | Real local DB |
| GET | `/api/apple-health/sync/setup-url` | owner session | Setup modal | env/request host | `{webhook_url, has_token}` | Real setup contract |
| GET | `/api/apple-health/status` | owner session | availability probe | none | `{available, health_dir, export_files}` | Real local file/DB probe |
| GET | `/api/apple-health/summary` | owner session | summary consumers | none | aggregate workouts/sleep/steps/vitals | Real file + sync DB |
| GET | `/api/apple-health/workouts` | owner session | history/recommendation consumers | `days` default 30, `0` all | `{workouts,total}` | Real file + sync DB |
| GET | `/api/apple-health/sleep` | owner session | sleep consumers | `days` default 30 | `{sleep,total}` | Real file + sync DB |
| GET | `/api/apple-health/steps` | owner session | activity consumers | `days` default 30 | `{steps,total}` | Real file + sync DB |
| GET | `/api/apple-health/vitals` | owner session | vitals consumers | `days` default 30 | `{rhr,hrv}` | Real file + sync DB |
| GET | `/api/health/workouts` | owner session | legacy file route | `days` default 30 | `{workouts,total}` | Real legacy file export |
| GET | `/api/health/sleep` | owner session | legacy file route | `days` default 30 | `{sleep,total}` | Real legacy file export |
| GET | `/api/health/steps` | owner session | legacy file route | `days` default 30 | `{steps,total}` | Real legacy file export |
| GET | `/api/health/vitals` | owner session | legacy file route | `days` default 30 | `{rhr,hrv}` | Real legacy file export |
| GET | `/api/health/summary` | owner session | legacy file route | none | summary fields | Real legacy file export |
| POST | `/api/health/sync` | owner session + CSRF/header | Open Wearables manual sync | none | Open Wearables metadata | Real, not Apple Health |

Error taxonomy for `/api/apple-health/sync`: 503 `{"error":"HEALTH_SYNC_TOKEN not configured on server"}`; 401 `{"error":"invalid or missing sync token"}`; 400 `{"error":"No JSON body provided"}`. Per-record parse errors are counted as skipped, not returned individually.

## 6. Data Model & Persistence

Sync DB path is `APPLE_HEALTH_SYNC_DB` when set, otherwise `${DATA_DIR}/apple_health_sync.db`. `DATA_DIR` defaults to the app directory. The parser initializes `ah_sync_log` and `ah_sync_events` at route-registration time and again before sync/status operations.

Read endpoints differ on empty data: `/api/apple-health/summary`, `/sleep`, `/steps`, and `/vitals` return 404 with an error body when no Apple Health data exists, while `/workouts` returns 200 with an empty list. When sync data exists, summary enrichment recomputes `avg_sleep_7d` and `avg_steps_7d` from the last 30 days of sync rows and reports `workouts_total` as file-total plus 30-day sync count.

Legacy file exports live under `~/Documents/Health`. The parser reads latest matching JSON files for workouts, sleep analysis, and specific timeseries files. The newer Apple Health parser catches JSON/OS errors and returns empty data; `health_ingest.py` is a simpler legacy route module.

Migration behavior: old sync DBs without `record_key` get the column added. If SQLite still has the old unique constraint on `(source, record_type, record_date)`, the table is rebuilt to the widened unique key `(source, record_type, record_date, record_key)`.

Retention is indefinite in the current SQLite database. No size cap, row cap, or pruning policy is implemented for Apple Health sync data. Normal status responses intentionally return aggregate counts/timestamps rather than raw records.

## 7. Enums & Constants

| Name | Values | Meaning |
|---|---|---|
| Sync source | `health_auto_export` | Stored source for webhook rows. |
| Record types | `workouts`, `heart_rate`, `hrv`, `sleep`, `steps`, `active_energy` | Persisted HAE categories. |
| HAE metric map | `step_count`, `steps`, `active_energy`, `active_energy_burned`, `resting_heart_rate`, `heart_rate`, `heart_rate_variability`, `sleep_analysis`, `sleep` | Accepted metric names. |
| Freshness status | `missing`, `fresh`, `aging`, `stale` | Missing none; fresh under 24h; aging 24-48h; stale over 48h. |
| Staleness watchdog threshold | default `36` hours | Launchd script alert threshold after first sync. |
| Workout HR env truthy values | `1`, `true`, `yes`, `on` | Enables HR-derived workout intensity. |
| Workout HR valid range | `30` to `230` bpm | Values outside become null. |
| HR intensity bands | `>=170` -> 8; `>=150` -> 7; `>=120` -> 6; else 5 | Load multiplier when enabled and applicable. |
| HAE workout duration heuristic | `>240` means seconds; `<=240` treated as minutes | Avoids double converting long minute values. |
| Sleep phase unit heuristic | `0 < value <= 24` means hours; larger means minutes | Converts HAE sleep phases. |
| Dedupe start tolerance | 5 minutes | Same activity/duration within this window counts once. |
| Dedupe duration tolerance | 5 minutes | Durations must be within tolerance. |
| Setup URL envs | `FITNESS_DASHBOARD_PUBLIC_BASE_URL`, `APPLE_HEALTH_WEBHOOK_URL` | Public endpoint source for phone setup. |
| Token env | `HEALTH_SYNC_TOKEN` | Shared webhook secret. |
| Token file | `.health-sync-token` | Auto-generated local token fallback when env missing. |
| Activity type ignored | `Other` | Ignored for recommendation bridge and workouts endpoint. |

Activity map is copied from HealthKit raw values for common workout types, including Walking `1`, Running `2`, Cycling `3`, Hiking `4`, Traditional Strength Training `6`, Swimming `10`, Yoga `11`, Elliptical `13`, Stair Climbing `14`, Core Training `20`, Functional Strength Training `22`, Cross Training `23`, Other `25`, HIIT `28`, Rowing `30`, Basketball `37`, Jump Rope `39`, Kickboxing `40`, Table Tennis `52`, Tai Chi `54`, Volleyball `55`, Wrestling `58`, and Stairs `64`.

## 8. Integration Points

Apple Health feeds `/api/dashboard` and `/api/recommendation/smart` through freshness, recommendation source payloads, ACWR, cardio fatigue, and workout history normalization. It does not replace Oura as the preferred sleep/recovery source, but it is the preferred workout/activity source when recent data is available.

Apple Health sync status is used by Settings and smoke checks. The setup URL depends on runtime public URL configuration. The staleness launchd agent depends on the local SQLite DB, `DATA_DIR`, and optional `APPLE_HEALTH_FIRST_SEEN_FILE`.

Open Wearables has its own Apple Health phone-provider invite flow. That is separate from HAE webhook ingestion. The assigned `/api/health/sync` route belongs to Open Wearables sync metadata, not HAE.

## 9. Permissions & Security

`/api/apple-health/sync` is exact-path public and CSRF-exempt because phone automation cannot hold a browser session or CSRF token. It is authenticated only by `HEALTH_SYNC_TOKEN`, supplied in `X-Sync-Token` or the query string. The query-token setup is convenient for HAE but overlaps with existing security issue FIT-261.

All Apple Health status, setup-url, summary, workouts, sleep, steps, vitals, and legacy `/api/health/*` read endpoints require the owner session through the global auth guard. `/api/apple-health/sync/status` is intentionally not public because it exposes setup/token hints and sync metadata.

Status endpoints avoid raw records, samples, token, secret, raw payload, and user id fields. Tests explicitly assert the status endpoint does not leak those keys.

## 10. Business Rules

Apple Health data freshness is based on max `record_date`, not max upload time. A recent upload of old records remains stale if the data-through date is stale.

Setup configured is not the same as connected. The UI can show "Webhook configured but no Apple Health records have landed yet."

Last attempt is not the same as last accepted. If HAE posts duplicates or invalid records, last attempt can move forward while last accepted does not; the Settings detail panel surfaces that divergence.

Workout imports ignore "Other" activity. Multiple workouts on the same day are supported in the sync DB for `workouts` through `record_key`, but downstream merge behavior still has known dedupe caveats.

Date bucketing uses the ISO timestamp's own timezone offset for HAE timestamps. Date-only strings pass through unchanged. File-based HealthKit exports use UTC conversion from millisecond timestamps.

Native iOS helper work should only begin if Apple Health freshness is stale more than three times in a rolling 30-day window under normal phone use, instrumentation shows attempted sync failure/rejection/missing accepted attempts, bridge fixes have been tried or rejected, and the owner approves.

Body mass remains outside the current Apple Health bridge contract. The native helper SLA does not claim body-mass parity; adding it would require a separate end-to-end mapping, persistence, status/UI, and test change.

## 11. Config & Environment

| Env var | Default | Behavior when unset |
|---|---|---|
| `HEALTH_SYNC_TOKEN` | auto-generated `.health-sync-token` during route registration if absent | Webhook rejects if no expected token exists; current code tries to generate/persist one locally. |
| `APPLE_HEALTH_SYNC_DB` | `${DATA_DIR}/apple_health_sync.db` | Controls sync DB path. |
| `DATA_DIR` | app directory | Stores sync DB and `.apple-health-first-sync` fallback under local runtime data path. The shell watchdog uses `${DATA_DIR:-${HOME}/fitness-dashboard}`, which can diverge from the Python app-directory default. |
| `FITNESS_DASHBOARD_PUBLIC_BASE_URL` | request-derived public base URL | Preferred setup URL base. |
| `APPLE_HEALTH_WEBHOOK_URL` | none | Overrides setup endpoint path before token append. |
| `APPLE_HEALTH_WORKOUT_HR_INTENSITY` | off | Enables HR-derived workout load. |
| `APPLE_HEALTH_FIRST_SEEN_FILE` | `${DATA_DIR}/.apple-health-first-sync` | Watchdog first-sync marker; when `DATA_DIR` is unset in the shell script, fallback is `${HOME}/fitness-dashboard/.apple-health-first-sync`. |
| `STALE_AFTER_HOURS` | `36` | Watchdog staleness threshold. |

## 12. Test Coverage

Existing tests cover HAE local-date bucketing with timezone offsets and Z suffixes, flat payload date normalization, runtime DB env changes after app import, workouts endpoint activity mapping/filtering, sleep phase duration fallback and partial-row merge behavior, setup URL generation from configured public base URL and explicit webhook URL, token rejection, status auth gate, status field contracts and raw-data non-leakage, CSRF exemption for token-auth webhook, and recommendation bridge effects on ACWR, cardio fatigue, HR intensity, dedupe, strength volume, and smart recommendation reasoning.

Notable gaps: no test for the staleness watchdog script; no payload size/row-count cap tests; file-export parsers still have known stale filename/aggregation issues tracked by FIT-260; dashboard parsing hot-path cache work is tracked by FIT-262. Body mass is intentionally outside the current bridge contract rather than an unimplemented parity promise.

## 13. Gaps & Issue Candidates

### IC-1: Fix file-export parser filenames and daily aggregation
- **Type:** Bug
- **Priority:** high
- **Where:** `apple_health_parser.py:183`, `apple_health_parser.py:187`, `apple_health_parser.py:191`, `apple_health_parser.py:195`
- **Problem:** The file-export parsers for steps, active energy, RHR, and HRV point at fixed dated filenames and aggregate all timeseries metrics as average/min/max. Steps and active energy should be daily totals, and stale fixed filenames can miss newer exports.
- **Why it matters:** Legacy Apple Health data can be stale or mathematically wrong before it reaches recommendations.
- **Acceptance criteria:**
  - Load the newest matching file by metric category instead of hardcoded dated filenames.
  - Sum cumulative metrics such as steps and active energy.
  - Preserve average/min/max behavior only for true sampled vitals.
  - Add regression tests for stale filenames and steps math.
- **Duplicate-of:** FIT-260

### IC-2: Harden Apple Health workout dedupe across multiple same-day workouts
- **Type:** Bug
- **Priority:** high
- **Where:** `apple_health_parser.py:318`, `app.py:1607`, `app.py:1627`
- **Problem:** The sync DB can store multiple workouts per day via `record_key`, but public merge/recommendation dedupe can still collapse workouts by date/activity when start evidence is missing.
- **Why it matters:** Real multiple same-day workouts can be undercounted or hidden from training load.
- **Acceptance criteria:**
  - Preserve multiple same-day workouts when they have distinct start times or stable keys.
  - Only dedupe when same activity, close start time, and close duration indicate the same workout.
  - Add tests for two same-day walks/runs and HAE rows without start time.
- **Duplicate-of:** FIT-260

### IC-3: Cache Apple Health parser reads on dashboard hot paths
- **Type:** Improvement
- **Priority:** medium
- **Where:** `app.py:4660`, `app.py:1524`, `apple_health_parser.py:85`
- **Problem:** Recommendation fingerprints and Apple Health workout loading read file exports and sync records in request paths. The existing open issue calls out dashboard parsing cache and next-workout hot-path short-circuiting.
- **Why it matters:** Dashboard and workout recommendation latency can grow with health export size.
- **Acceptance criteria:**
  - Add a bounded cache keyed by file marker and sync DB marker.
  - Short-circuit next-workout recomputation when Apple Health inputs are unchanged.
  - Keep freshness/status endpoints accurate after new webhook posts.
  - Add focused performance or call-count tests.
- **Duplicate-of:** FIT-262

### IC-4: Move Apple Health setup token out of query URLs/plaintext
- **Type:** Privacy
- **Priority:** high
- **Where:** `apple_health_parser.py:100`, `apple_health_parser.py:608`, `apple_health_parser.py:921`, `templates/index.html:1597`
- **Problem:** Setup URL generation appends `HEALTH_SYNC_TOKEN` into the webhook query string, and the code auto-persists a token to `.health-sync-token` when env is absent.
- **Why it matters:** Query strings and plaintext token files are easier to leak through browser history, logs, screenshots, or filesystem mistakes.
- **Acceptance criteria:**
  - Provide a HAE-compatible secret delivery pattern that avoids token-in-query when possible.
  - Store local token material outside repo/runtime paths or in a protected store.
  - Preserve a migration path for existing HAE setup.
  - Update tests and setup modal copy.
- **Duplicate-of:** FIT-261

### IC-5: Body-mass scope decision (resolved by FIT-326)
- **Type:** Data-contract
- **Priority:** medium
- **Where:** `docs/APPLE_HEALTH_HELPER_SLA.md`, `apple_health_parser.py`
- **Resolution:** Resolved by FIT-326. Body mass does not belong in the current HAE bridge contract, so the helper SLA no longer claims body-mass parity.
- **Future work:** Adding body mass requires a separate end-to-end change covering metric mapping, persistence, status/UI evidence, and tests.
- **Duplicate-of:** none

### IC-6: Add payload limits and rejection reporting for HAE sync
- **Type:** Improvement
- **Priority:** medium
- **Where:** `apple_health_parser.py:774`, `apple_health_parser.py:815`
- **Problem:** `/api/apple-health/sync` accepts arbitrary JSON size and counts per-record parse failures as skipped without reporting why.
- **Why it matters:** A bad automation or replay can create large local writes and leave the owner guessing why records did not land.
- **Acceptance criteria:**
  - Define maximum request size and maximum records per sync attempt.
  - Return stable public rejection codes/counts by reason.
  - Store attempt summary without raw health payload leakage.
  - Add tests for over-limit payload and invalid rows.
- **Duplicate-of:** none

### IC-7: Surface staleness watchdog evidence in Settings
- **Type:** Improvement
- **Priority:** low
- **Where:** `scripts/check-apple-health-staleness.sh:10`, `static/js/app.js:6761`
- **Problem:** The launchd watchdog writes staleness state to `/tmp/apple-health-staleness.log` and exits nonzero, but the Settings panel does not read or explain the watchdog result.
- **Why it matters:** The owner may see stale Apple Health data without knowing whether the local watchdog is installed, running, or alerting.
- **Acceptance criteria:**
  - Add a safe status endpoint or local summary for watchdog last check/result.
  - Show watchdog state in the Apple Health Settings detail panel.
  - Add tests for no-first-sync quiet state, OK state, stale state, and parse-error state.
- **Duplicate-of:** none

### IC-8: Rename or clarify `/api/health/sync` ownership
- **Type:** Docs
- **Priority:** low
- **Where:** `app.py:11609`, `health_ingest.py:134`
- **Problem:** `/api/health/sync` sounds like Apple Health, while the current route manually pulls Open Wearables data and returns `source: "open_wearables"`.
- **Why it matters:** Agents and maintainers can wire the wrong sync contract when working on Apple Health.
- **Acceptance criteria:**
  - Document `/api/health/sync` as Open Wearables-owned or alias it to an Open Wearables-specific route.
  - Keep legacy consumers working.
  - Add route comments/tests that prevent Apple Health webhook assumptions.
- **Duplicate-of:** none
