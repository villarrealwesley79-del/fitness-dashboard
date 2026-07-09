# Oura Integration - PRD

> **Sources:** `README.md`; `docs/VISION.md`; `docs/PRD.md`; `docs/CURRENT_STATE.md`; `app.py`; `oura_client.py`; `oura_sleep_sync.py`; `templates/index.html`; `static/js/app.js`; `tests/test_oura_sync.py`; `tests/test_wearable_freshness_contract.py`
> **Routes:** `/api/oura/status`, `/api/oura/trends`, `/api/oura/sync-sleep`, `/api/oura/sleep-summary`, `/api/freshness`, consuming routes `/api/dashboard`, `/api/next-workout`, `/api/recommendation/smart`, `/api/vitals`
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

The Oura integration is the primary sleep and recovery source for the dashboard. It pulls readiness, sleep, HRV, resting heart rate, temperature deviation, steps, activity score, active calories, and detailed sleep-stage minutes from the Oura API, then stores a simplified local SQLite cache under the app's runtime `DATA_DIR`. The product purpose is not to show Oura as a standalone tracker; Oura data feeds the daily brief, freshness chips, vitals, sleep debt, recommendation confidence, workout volume adjustment, and AI workout-analysis context.

The integration is local-first and cache-backed. Browser surfaces usually render cached SQLite values and freshness evidence; live Oura API calls happen when a cache miss requires them, when the user forces refresh from Settings, or when the user runs the manual sleep sync button. If the Oura API or token is unavailable but a cached row exists, the product keeps the cached data visible and labels it as cached/degraded instead of failing the dashboard.

Oura is a real integration, not a mock. It uses `OURA_API_TOKEN` as a Personal Access Token and calls Oura v2 `usercollection` endpoints. The app also has Open Wearables Oura provider tiles, but those are separate hub setup actions; this PRD covers the direct Oura API and local Oura SQLite cache.

## 2. User-Facing Surfaces

The dashboard recommendation card shows an Oura freshness chip beside WHOOP, Apple Health, and Food. The chip can show unknown, no data, live/cached plus age, warning, or stale state. When all wearable sources are missing, the recommendation title falls back to "Rest day - no recent wearable data"; when Oura or another wearable is stale, it lowers confidence and explains that wearable data is old.

The first dashboard screen renders readiness, HRV, resting heart rate, sleep duration, daily steps, active calories, and sleep quality from `/api/oura/status` where available. Sleep-score trend sparkline data comes from `/api/oura/sleep-summary`; HRV trend comes from `/api/oura/trends`.

The Vitals tab prefers Open Wearables for heart rate, sleep, and activity, but falls back to `oura_daily.sqlite3` when Open Wearables is unavailable or empty. Oura fallback values can populate resting BPM, steps, active calories, last-night sleep duration/stages/score, and 7-day average sleep.

The Settings integration panel has an "Oura Ring" row with a freshness chip, a `Sync` button, and a detail panel with "Latest daily", "Latest sleep", and "Source". The detail panel distinguishes live API pulls from cached local SQLite and appends warnings such as API errors or stale data.

## 3. Field Inventory

### Oura Daily Cache (`oura_daily`)

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `day` | string `YYYY-MM-DD` | yes | none | primary key | Calendar day the Oura metrics cover. |
| `readiness_score` | integer | no | null | Oura-provided | Recovery readiness score used by recommendations and daily brief. |
| `sleep_score` | integer | no | null | Oura-provided | Sleep quality score shown on dashboard and vitals. |
| `hrv` | real | no | null | Oura `average_hrv` | HRV in milliseconds, used for trend label. |
| `steps` | integer | no | null | Oura daily activity | Step count for the activity day. |
| `activity_score` | integer | no | null | Oura daily activity | Oura activity score. |
| `active_calories` | integer | no | null | Oura daily activity | Active calorie estimate. |
| `resting_hr` | real | no | null | Oura sleep/readiness | Lowest or average sleep heart rate fallback. |
| `temperature_deviation` | real | no | null | `temperature_deviation` or `temperature_delta` | Temperature context for readiness. |
| `sleep_duration_min` | integer | no | null | seconds converted to rounded minutes | Total sleep duration. |
| `sleep_deep_min` | integer | no | null | seconds converted to rounded minutes | Deep sleep duration. |
| `sleep_rem_min` | integer | no | null | seconds converted to rounded minutes | REM sleep duration. |
| `sleep_light_min` | integer | no | null | seconds converted to rounded minutes | Light sleep duration. |
| `sleep_awake_min` | integer | no | null | seconds converted to rounded minutes | Awake time during sleep. |
| `raw_json` | JSON string | no | null | stored server-side only | Raw Oura payload for repair/backfill; removed from trend responses. |
| `created_at` | ISO datetime string | no | current local server time | set on every upsert | Last local upsert/sync attempt for that row. |

### Oura Sleep Cache (`oura_sleep`)

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `day` | string `YYYY-MM-DD` | yes | none | part of primary key | Sleep record day. |
| `type` | string | yes | `long_sleep` | Oura sleep type | Distinguishes main sleep from naps/rest. |
| `bedtime_start` | ISO datetime | no | null | Oura-provided | Start of sleep window. |
| `bedtime_end` | ISO datetime | no | null | Oura-provided | End of sleep window. |
| `total_sleep_min` | integer | no | null | seconds to minutes | Total sleep minutes. |
| `deep_sleep_min` | integer | no | null | seconds to minutes | Deep sleep minutes. |
| `rem_sleep_min` | integer | no | null | seconds to minutes | REM minutes. |
| `light_sleep_min` | integer | no | null | seconds to minutes | Light sleep minutes. |
| `awake_time_min` | integer | no | null | seconds to minutes | Awake minutes. |
| `sleep_score` | integer | no | null | Oura-provided | Sleep score for summary. |
| `efficiency` | integer | no | null | Oura-provided | Sleep efficiency percentage/score. |
| `avg_heart_rate` | real | no | null | Oura-provided | Average sleep heart rate. |
| `avg_hrv` | real | no | null | Oura-provided | Average sleep HRV. |
| `avg_breath_rate` | real | no | null | Oura-provided | Average breath rate. |
| `lowest_heart_rate` | integer | no | null | Oura-provided | Lowest sleep heart rate. |
| `restfulness_score` | integer | no | null | currently `restless_periods` | Restfulness proxy. |
| `raw_json` | JSON string | no | null | stored server-side | Full sleep row. |
| `created_at` | ISO datetime | no | current local time | set on upsert | Local insertion/update time. |

### Settings Detail Fields

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| Latest daily | text | no | `Not connected` | rendered from `/api/oura/status` and freshness | Shows date/readiness/HRV/RHR or cached-through date. |
| Latest sleep | text | no | `-` or `No sleep row` | duration + score if present | Shows last known sleep duration and score. |
| Source | text | no | `-` | `api`, `db`, or freshness fallback | Labels live API pull vs local SQLite cache and warnings. |
| Sync button | button | yes | enabled | POST with CSRF header via JS helper | Runs manual Oura sleep sync and invalidates client caches. |

## 4. Interactions & Flows

Dashboard load -> The frontend requests `/api/dashboard`, `/api/oura/status`, `/api/recommendation/smart`, `/api/oura/sleep-summary`, and `/api/oura/trends`. Oura status is cached client-side unless forced. Success fills readiness, HRV, RHR, sleep, steps, calories, freshness chips, insight sparkline, and recommendation confidence. Failure sets retry chips for readiness or insight without blocking the entire page.

Oura status load -> `/api/oura/status` checks today's `oura_daily` row first unless `refresh=true`. If today's cache exists, it returns the cached row with `source: "db"`, but that same-day DB-cache path omits `active_calories`, so the dashboard active-calories tile can stay blank despite cached data existing. If today's cache is missing or refresh is forced, it creates an `OuraClient`, fetches daily readiness, daily sleep, detailed sleep, and daily activity, upserts `oura_daily`, and returns `source: "api"`. If the API/token fails and today's cache exists, it returns cached data plus `warning`; otherwise it returns 503 for missing token/client setup or 502 for API failure.

Oura activity lag handling -> Oura daily activity can lag readiness/sleep. The status route stores readiness/sleep on today's row but stores activity metrics on the actual `activity_day` when Oura returns a prior day. When today's steps/activity are missing, the route looks back 14 days for the most recent steps or activity score.

Manual sleep sync -> The Settings `Sync` button calls `POST /api/oura/sync-sleep` with no body by default. The backend accepts optional `days_back`, default `30`, min `1`, max `365`. It creates/verifies `oura_sleep`, checks `OURA_API_TOKEN`, syncs Oura sleep rows from the computed start date, clears the server-cached workout recommendation, and returns a summary of latest sleep days. Success invalidates frontend caches and reloads the current tab. Missing token returns structured 503 `missing_oura_token`; invalid `days_back` returns structured 400 `invalid_field`; upstream HTTP/URL errors return 502 `oura_api_error`; unexpected failures return 500 `oura_sync_failed`.

Sleep summary load -> `/api/oura/sleep-summary` reads latest `long_sleep` from `oura_sleep` and 7-day sleep range, but prefers/falls back to `oura_daily` when the daily cache is newer or fuller. It returns last-night fields, 7-day averages, bedtime consistency, and trend data. This fallback exists because `oura_sleep` can lag behind `oura_daily`.

Recommendation generation -> `generate_next_workout()` reads today's effective readiness through `_get_oura_readiness_today()`. That value is Oura readiness adjusted by recent Open Wearables sleep duration/average-HR nudges, and when today's Oura row is absent it can synthesize a base-70 readiness from Open Wearables alone. If the blended readiness is below 60, the workout volume multiplier is reduced by 20%. `calculate_sleep_debt()` reads the last N `sleep_duration_min` rows from `oura_daily` with a 420-minute nightly target. `/api/recommendation/smart` also reads readiness, sleep score, HRV, HRV trend, and sleep debt to construct recommendation reasoning and confidence.

Freshness flow -> `_compute_data_freshness()` classifies Oura using latest `oura_daily.day` as the data point and `created_at` as the sync attempt. Status values are `fresh`, `aging`, `stale`, or `missing`. `source` is `live` only when the last upsert is under 1 hour old; otherwise it is `cached`. The UI uses server freshness rather than doing its own stale classification.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
|---|---|---|---|---|---|---|
| GET | `/api/oura/status` | owner session | Dashboard, Vitals, Settings | `refresh=true` optional | Oura daily metrics plus `source`, optional `warning` | Real Oura API + SQLite cache |
| GET | `/api/oura/trends` | owner session | Dashboard trend chart | none | `{start_date,end_date,hrv_trend,series}` | Real/cache |
| POST | `/api/oura/sync-sleep` | owner session + CSRF/header | Settings Sync button | JSON `days_back` optional | success summary or structured error | Real Oura API + SQLite cache |
| GET | `/api/oura/sleep-summary` | owner session | Insight/sleep trend card | none | `{last_night,week_average,consistency,trend_data}` | Local SQLite cache only (no live Oura call) |
| GET | `/api/freshness` | owner session | Settings integration panel | none | `{freshness: {oura,...}}` | Local cache-derived |

`/api/oura/status` response fields: `date`, `readiness`, `sleep_score`, `hrv`, `steps`, `activity_score`, `active_calories` on API path, `activity_day`, `resting_hr`, `temperature_deviation`, `sleep_duration_min`, `sleep_breakdown_min.deep/rem/light/awake`, `source`, and optional `warning`. Error responses use `{"available": false, "error": "..."}` on missing live data.

`/api/oura/trends` strips `raw_json` before returning `series`. It fetches a 7-day API range only when the local row count is under 3; if that fetch fails it still returns HTTP 200 with existing series and `hrv_trend: "unknown"`.

`/api/oura/sleep-summary` returns HTTP 500 with `{status:"error", message}` if local sleep summary construction fails.

## 6. Data Model & Persistence

Runtime path uses `runtime_config.data_path()`: `DATA_DIR` if set, otherwise the app directory. The direct Oura database is `oura_daily.sqlite3`; both `oura_daily` and `oura_sleep` live in that same SQLite file. `app.py` initializes `oura_daily` at startup, and `oura_sleep_sync.py` creates `oura_sleep` on manual sync.

`oura_daily` has schema-hardening behavior: older SQLite files are altered to add any missing columns listed in `OURA_COLUMNS`. Upserts use `COALESCE`, so null values from a later partial Oura response do not erase existing non-null metrics, while `created_at` is always refreshed.

`oura_sleep` uses primary key `(day, type)` and overwrites all tracked sleep fields on conflict. Detailed raw sleep rows are retained server-side as JSON strings.

No retention limit is implemented in the Oura database. Normal API responses avoid returning raw Oura JSON except the raw value remains stored locally.

## 7. Enums & Constants

| Name | Values | Meaning |
|---|---|---|
| Oura API base URL | `https://api.ouraring.com/v2/usercollection` | Base for direct Oura v2 calls. |
| Oura endpoints | `daily_readiness`, `daily_sleep`, `sleep`, `daily_activity` | Direct data pulled by `OuraClient`. |
| Sleep type filter | `long_sleep` | Dashboard summaries use main sleep only. |
| Ignored detailed sleep types | `late_nap`, `nap`, `rest` | Excluded when choosing main detailed sleep for daily metrics. |
| `days_back` | default `30`, min `1`, max `365` | Manual sleep sync range. |
| Freshness status | `missing`, `fresh`, `aging`, `stale` | Missing none; fresh under 24h; aging 24-48h; stale over 48h. |
| Oura freshness source | `live`, `cached` | Live means local upsert under 1 hour old; otherwise cached. |
| `/api/oura/status` source | `api`, `db` | API pull vs cached SQLite response. |
| HRV trend | `improving`, `declining`, `stable`, `unknown` | `improving`/`declining`/`stable` come from `compute_hrv_trend`, which returns `stable` with fewer than 4 values; `unknown` is assigned by route/default layers on failed or missing trend fetch. Sparse caches therefore read as stable rather than unknown. |
| Bedtime consistency | `excellent`, `good`, `fair`, `poor`, `unknown` | `<30`, `<60`, `<90`, `>=90` minute variance, or insufficient data. |
| Sleep debt target | `420` minutes | 7-hour nightly target used for readiness factor. |
| Readiness volume threshold | `<60` | Reduces workout volume multiplier by 20%. |

## 8. Integration Points

Oura feeds the dashboard daily brief, recommendation chip strip, recommendation source summary, Vitals fallback, sleep debt, smart recommendation reasoning, AI workout analysis context, next-workout fingerprinting, and Settings integration details.

Oura also participates in source conflict context with WHOOP and Open Wearables through freshness and recommendation source payloads. The direct Oura cache remains separate from Open Wearables provider actions.

Food adaptation and workout recommendation caches depend on Oura because the recommendation fingerprint includes current Oura day, readiness, sleep score, HRV, sleep duration, RHR, last 7 days, and Oura DB file marker.

## 9. Permissions & Security

All direct Oura endpoints are owner-session authenticated by the global auth guard. `POST /api/oura/sync-sleep` is not CSRF-exempt; browser calls use the shared JS `api()` helper that sends `X-Requested-With: XMLHttpRequest`.

The Oura token is server-side only and read from `OURA_API_TOKEN`. Normal API responses do not return the token. Raw Oura payloads remain in local SQLite and are not emitted by trend or sleep-summary APIs.

API error messages can include Oura HTTP status and up to 200 characters of upstream response body for `/api/oura/sync-sleep`. This is useful for repair but should be treated as owner-only operational detail.

## 10. Business Rules

Oura readiness is the direct recovery score used by the deterministic workout engine before Open Wearables sleep nudges are applied. If no main sleep exists for the day, `OuraClient.get_today_metrics` can fall back to the last detailed sleep row of any type, including naps, so nap duration/HRV/stages can populate today's `oura_daily` sleep fields. If readiness is absent, the app can still produce conservative recommendations from soreness, training history, sleep debt defaults, weather, and other wearable sources.

Oura daily activity is explicitly allowed to lag. Readiness and sleep stay attached to today's cache row, but steps/activity can be pulled from the latest available activity day so the UI does not show blank activity merely because Oura has not finalized today.

Freshness uses data date, not just sync attempt. A row upserted today for an older Oura day still counts as stale if the data point is old.

The UI never treats "connected" as equivalent to current. It renders cached/live, data-through date, and stale warnings so the recommendation can lower confidence when Oura is stale.

Sleep summary consistency is still imperfect because the app merges `oura_sleep` and `oura_daily` sources. Existing issue FIT-234 covers inconsistent sleep summaries such as a very short sleep duration with a high score.

## 11. Config & Environment

| Env var | Default | Behavior when unset |
|---|---|---|
| `OURA_API_TOKEN` | none | Direct live Oura API calls fail. `/api/oura/sync-sleep` returns 503 `missing_oura_token`; `/api/oura/status` can still return cached data if present. |
| `DATA_DIR` | app directory | Stores `oura_daily.sqlite3` under app directory when unset. |
| `SECRET_KEY` | local fallback/dev behavior elsewhere | Required for secure sessions in real deployment. |

## 12. Test Coverage

Existing focused tests cover manual Oura sleep sync missing-token behavior, `days_back` validation, success summary shape, Settings/UI contract fields for `/api/oura/status`, and dashboard freshness block keys.

Notable gaps: no focused test pins `/api/oura/trends` cache/API fallback behavior; no focused test pins `/api/oura/sleep-summary` source-merging logic or consistency rules; no test proves stale Oura data lowers recommendation copy in the browser; no test proves raw Oura JSON never leaks from all Oura-facing endpoints.

## 13. Gaps & Issue Candidates

### IC-1: Add a consistency guard to Oura sleep summary
- **Type:** Bug
- **Priority:** high
- **Where:** `app.py:13497`, `app.py:13516`, `app.py:13596`
- **Problem:** `/api/oura/sleep-summary` merges `oura_sleep` and `oura_daily` to avoid stale sleep rows, but it does not validate that duration, score, and source row are internally plausible before rendering summary values.
- **Why it matters:** The owner can see a high-confidence sleep score attached to an implausible sleep duration.
- **Acceptance criteria:**
  - Detect inconsistent duration/score pairs before rendering the main sleep summary.
  - Surface a visible "needs review" or degraded state instead of a normal score.
  - Preserve both source rows for debugging without exposing raw JSON.
  - Add a regression test for the 1-minute/high-score case.
- **Duplicate-of:** FIT-234

### IC-2: Refresh stale Oura cache without requiring Settings
- **Type:** Improvement
- **Priority:** medium
- **Where:** `app.py:13227`, `static/js/app.js:1156`, `static/js/app.js:5355`
- **Problem:** Dashboard Oura status uses today's cached row unless the caller passes `refresh=true`; a stale `created_at` on today's row can remain cached until the user opens Settings or the date changes.
- **Why it matters:** The first-screen brief can look current while relying on an old same-day cache.
- **Acceptance criteria:**
  - Define a server-side refresh TTL for today's Oura row.
  - Auto-refresh only when the row is older than that TTL and a token exists.
  - Preserve cached fallback behavior when Oura is unavailable.
  - Add tests for fresh cache, stale cache refresh, and stale cache fallback.
- **Duplicate-of:** none

### IC-3: Pin Oura trends and sleep-summary contracts with tests
- **Type:** Test
- **Priority:** medium
- **Where:** `app.py:13376`, `app.py:13496`, `oura_sleep_sync.py:235`
- **Problem:** Tests pin Oura status and sync-sleep, but they do not pin trend fallback behavior, raw-json stripping, bedtime consistency thresholds, or daily-vs-sleep fallback behavior.
- **Why it matters:** These endpoints feed dashboard confidence and sleep cards; regressions would be user-visible.
- **Acceptance criteria:**
  - Add tests for `/api/oura/trends` with enough cache rows and with API fallback failure.
  - Add tests for `/api/oura/sleep-summary` daily-cache augmentation.
  - Assert `raw_json` is not returned in public trend series.
  - Assert consistency status thresholds.
- **Duplicate-of:** none

### IC-4: Make manual Oura sync result visible in Settings detail
- **Type:** Improvement
- **Priority:** low
- **Where:** `templates/index.html:958`, `static/js/app.js:6970`
- **Problem:** The template includes a hidden "Last sync" row for Oura, but the sync handler only shows toast messages and reloads the tab; it does not populate the row with latest sync result details.
- **Why it matters:** A toast disappears, leaving the owner without durable evidence of what the manual sync did.
- **Acceptance criteria:**
  - Show latest sync status, range, record count, and latest days in the Settings detail row.
  - Preserve existing toast behavior.
  - Show structured errors from `missing_oura_token` and `oura_api_error`.
  - Add a UI contract test or source assertion for the row update.
- **Duplicate-of:** none

### IC-5: Redact upstream Oura error detail before UI display
- **Type:** Privacy
- **Priority:** low
- **Where:** `app.py:13480`, `static/js/app.js:6977`
- **Problem:** Oura sync can include up to 200 characters of upstream response body in an owner-visible error message.
- **Why it matters:** Upstream errors are usually harmless, but owner-visible operational details should not accidentally include sensitive provider text.
- **Acceptance criteria:**
  - Map Oura upstream failures to stable public error codes and short public messages.
  - Keep full upstream detail server-side only if needed.
  - Add a regression test that response bodies do not include token-like or raw upstream payload content.
- **Duplicate-of:** none
